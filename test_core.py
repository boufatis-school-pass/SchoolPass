# -*- coding: utf-8 -*-
"""Automated tests for SchoolPass core. Synthetic data only; no GUI, no real student data."""
from __future__ import annotations

import csv
from contextlib import closing
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv  # noqa: E402  (one shared data dir for all modules)
TEST_ROOT = testenv.ROOT

import schoolpass_core as core  # noqa: E402

core.PBKDF2_ITERATIONS = 2000   # speed only; production value is 310 000 (asserted below)

PW = "TestPassword123!"
FULL_NID = "123456789012345678"
fake_protect = lambda v: "DPAPI1:" + v[::-1]            # stands in for Windows DPAPI (Windows-only API)
fake_reveal = lambda v: v.split(":", 1)[1][::-1]


def tearDownModule():
    core.stop_local_server()
    pass


def fresh_db():
    # Each test owns a fresh data directory; never unlink a database that may
    # still be held by SQLite or a background server thread.
    core.stop_local_server()
    core.init_db()
    admin_id, code = core.bootstrap_admin("director", "المدير", PW)
    sup_id = core.create_user_as(admin_id, "sup1", "مشرف أول", PW)
    return admin_id, sup_id, code


def write_csv(path: Path, rows, header=("رقم التعريف", "اللقب", "الاسم", "الجنس", "السنة", "الشعبة", "القسم"),
              encoding="utf-8-sig", delimiter=","):
    with path.open("w", encoding=encoding, newline="") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(header)
        w.writerows(rows)


class Base(unittest.TestCase):
    def setUp(self):
        core.stop_local_server()
        self.tmp = Path(tempfile.mkdtemp(dir=TEST_ROOT))
        self.data_dir = self.tmp / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("prints", "backups", "logs"):
            (self.data_dir / sub).mkdir(exist_ok=True)

        # schoolpass_core resolves these paths at import time. Repoint all
        # database/log paths for this test before initializing its database.
        core.DATA_DIR = self.data_dir
        core.DB_PATH = self.data_dir / "schoolpass.sqlite3"
        core.LOG_PATH = self.data_dir / "logs" / "schoolpass.log"

        self.admin, self.sup, self.code = fresh_db()
        self.csv = self.tmp / "students.csv"
        write_csv(self.csv, [["T-001", "تجريبي", "أحمد", "ذكر", "أولى", "علوم", "1"],
                             ["T-002", "اختباري", "سارة", "أنثى", "أولى", "علوم", "1"]])
        core.import_students(str(self.csv), self.admin)
        self.s1, self.s2 = core.get_student("T-001"), core.get_student("T-002")

    def permit(self, student=None, nid=FULL_NID):
        with mock.patch.object(core, "protect_sensitive_text", fake_protect):
            return core.create_exit_permit(student or self.s1, self.sup, "موعد طبي", "ولي تجريبي", "الأب", nid)


class TestSecurityAndPermissions(Base):
    def test_password_stored_hashed_and_lockout(self):
        with core.db_connect() as c:
            row = c.execute("SELECT salt,password_hash FROM users WHERE username='sup1'").fetchone()
        self.assertNotIn(PW, row["password_hash"])
        self.assertEqual(len(row["salt"]), 32)
        for i in range(1, 5):
            u, msg = core.authenticate("sup1", "wrong-pass-" + str(i))
            self.assertIsNone(u)
        u, msg = core.authenticate("sup1", "wrong-pass-5")
        self.assertIn("قفل", msg)
        u, msg = core.authenticate("sup1", PW)
        self.assertIsNone(u, "locked account must refuse even the right password")
        with core.db_connect() as c:
            self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='فشل تسجيل الدخول'").fetchone()[0], 5)

    def test_login_success_and_case_insensitive_username(self):
        u, _ = core.authenticate("SUP1", PW)
        self.assertEqual(u["username"], "sup1")
        with self.assertRaises(ValueError):
            core.create_user_as(self.admin, "Sup1", "مكرر", PW)

    def test_weak_passwords_rejected(self):
        for bad in ("short", "12345678", "password", "aaaaaaaa", "sup2sup2"[:0] + "Sup2"):
            with self.assertRaises(ValueError):
                core.create_user_as(self.admin, "sup2", "x", bad)
        with self.assertRaises(ValueError):
            core.create_user_as(self.admin, "samename1", "x", "samename1")

    def test_no_default_accounts_or_passwords(self):
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        core.init_db()
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        self.assertFalse(core.has_admin())

    def test_supervisor_blocked_in_core_functions_not_just_buttons(self):
        sup = self.sup
        calls = [lambda: core.create_user_as(sup, "x1", "x", PW), lambda: core.reset_password_as(sup, self.admin, PW + "x"),
                 lambda: core.set_user_active_as(sup, self.admin, False), lambda: core.import_students(str(self.csv), sup),
                 lambda: core.set_school_name_as(sup, "اسم"), lambda: core.backup_database(self.tmp / "b.sqlite3", sup),
                 lambda: core.restore_database(self.tmp / "b.sqlite3", sup), lambda: core.export_students_csv(str(self.tmp / "s.csv"), sup),
                 lambda: core.issue_recovery_code_as(sup), lambda: core.set_qr_host_as(sup, "192.168.1.5"),
                 lambda: core.set_theme_as(sup, "#112233", "#FFFFFF"), lambda: core.set_print_mode_as(sup, "open"),
                 lambda: core.reveal_national_id(1, sup)]
        for i, fn in enumerate(calls):
            with self.assertRaises(core.PermissionDenied, msg=f"call #{i}"):
                fn()

    def test_inactive_user_cannot_create_records(self):
        core.set_user_active_as(self.admin, self.sup, False)
        with self.assertRaises(core.PermissionDenied):
            core.create_late_slip(self.s1, self.sup, "تأخر")
        u, msg = core.authenticate("sup1", PW)
        self.assertIsNone(u)

    def test_cannot_deactivate_self_or_last_admin(self):
        with self.assertRaises(ValueError):
            core.set_user_active_as(self.admin, self.admin, False)
        a2 = core.create_user_as(self.admin, "second", "مدير ثان", PW, "admin")
        core.set_user_active_as(a2, self.admin, False)     # allowed: another active admin remains
        with self.assertRaises(ValueError):
            core.set_user_active_as(a2, a2, False)

    def test_theme_defaults_and_admin_only_customisation(self):
        self.assertEqual(core.get_theme_colors(), ("#185A8D", "#F2F5F9"))
        self.assertEqual(core.set_theme_as(self.admin, "#143B63", "#EAF1F7"), ("#143B63", "#EAF1F7"))
        self.assertEqual(core.get_theme_colors(), ("#143B63", "#EAF1F7"))
        with self.assertRaises(ValueError):
            core.set_theme_as(self.admin, "blue", "#FFFFFF")
        with self.assertRaises(ValueError):
            core.set_theme_as(self.admin, "#FFFFFF", "#FFFFFF")
        with self.assertRaises(core.PermissionDenied):
            core.set_theme_as(self.sup, "#112233", "#FFFFFF")
        with core.db_connect() as conn:
            row = conn.execute("SELECT action,description FROM audit_logs WHERE action='تغيير ألوان الواجهة' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("#143B63", row["description"])

    def test_preferences_save_atomically_and_only_admin_can_change_them(self):
        original = (core.setting("school_name"), core.qr_host(), core.get_theme_colors())
        with self.assertRaises(ValueError):
            core.save_preferences_as(self.admin, "اسم جديد", "bad host", "#123456", "#ABCDEF")
        self.assertEqual((core.setting("school_name"), core.qr_host(), core.get_theme_colors()), original)
        with self.assertRaises(core.PermissionDenied):
            core.save_preferences_as(self.sup, "اسم جديد", "192.168.1.10", "#123456", "#ABCDEF")
        core.save_preferences_as(self.admin, "ثانوية اختبار", "school-pc.local", "#123456", "#ABCDEF")
        self.assertEqual(core.setting("school_name"), "ثانوية اختبار")
        self.assertEqual(core.qr_host(), "school-pc.local")
        self.assertEqual(core.get_theme_colors(), ("#123456", "#ABCDEF"))
        self.assertEqual(core.setting("print_mode"), "open")

    def test_print_mode_is_admin_only_and_validated(self):
        core.set_print_mode_as(self.admin, "open")
        self.assertEqual(core.setting("print_mode"), "open")
        with self.assertRaises(ValueError):
            core.set_print_mode_as(self.admin, "direct")
        with self.assertRaises(core.PermissionDenied):
            core.set_print_mode_as(self.sup, "open")

    def test_open_pdf_never_uses_silent_print_verb(self):
        calls = []
        with mock.patch.object(core.os, "startfile", side_effect=lambda *args: calls.append(args), create=True), \
             mock.patch.object(core, "setting", return_value="direct"):
            self.assertTrue(core.open_pdf(Path("preview.pdf")))
        self.assertEqual(calls, [("preview.pdf",)])

    def test_production_kdf_strength_is_kept(self):
        import inspect
        self.assertEqual(inspect.getsource(core).count("PBKDF2_ITERATIONS = 310_000"), 1)

    def test_bootstrap_only_once(self):
        with self.assertRaises(core.PermissionDenied):
            core.bootstrap_admin("other", "x", PW)

    def test_recovery_code_flow(self):
        with self.assertRaises(core.PermissionDenied):
            core.recover_admin_password("director", "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA", "NewPassword456!")
        new_code = core.recover_admin_password("director", self.code.lower(), "NewPassword456!")
        self.assertNotEqual(new_code, self.code)
        u, _ = core.authenticate("director", "NewPassword456!")
        self.assertIsNotNone(u)
        with self.assertRaises(core.PermissionDenied):       # old code is dead
            core.recover_admin_password("director", self.code, "Another789!pw")
        with self.assertRaises(core.PermissionDenied):       # supervisors cannot be recovered this way
            core.recover_admin_password("sup1", new_code, "Another789!pw")

    def test_recovery_lockout(self):
        for _ in range(5):
            with self.assertRaises(core.PermissionDenied):
                core.recover_admin_password("director", "0000-0000-0000-0000-0000-0000", "NewPassword456!")
        with self.assertRaises(core.PermissionDenied) as cm:
            core.recover_admin_password("director", self.code, "NewPassword456!")
        self.assertIn("15", str(cm.exception))


class TestDatabaseIntegrity(Base):
    def test_records_cannot_be_deleted_or_rewritten_even_with_raw_sql(self):
        r = core.create_late_slip(self.s1, self.sup, "حافلة")
        with core.db_connect() as c:
            for sql in ("DELETE FROM records", "UPDATE records SET reason='x'", "UPDATE records SET student_name='x'",
                        "UPDATE records SET record_no='EN-1'", "UPDATE records SET qr_token='x'", "UPDATE records SET user_id=1",
                        "DELETE FROM audit_logs", "UPDATE audit_logs SET description='x'"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    c.execute(sql)
        self.assertEqual(core.record_by_id(r["id"])["reason"], "حافلة")

    def test_status_is_final_and_cancel_needs_reason(self):
        r = core.create_late_slip(self.s1, self.sup, "x")
        with core.db_connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE records SET status='cancelled' WHERE id=?", (r["id"],))   # no reason
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE records SET status='bogus' WHERE id=?", (r["id"],))
        core.cancel_record(r["id"], self.sup, "خطأ إدخال")
        with core.db_connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE records SET status='valid' WHERE id=?", (r["id"],))

    def test_old_silent_print_setting_is_migrated_to_safe_pdf_preview(self):
        core.set_setting("print_mode", "direct")
        core.init_db()
        self.assertEqual(core.setting("print_mode", "open"), "open")

    def test_connections_are_closed_after_with_block(self):
        with core.db_connect() as c:
            c.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")

    def test_upgrade_from_0_2_0_database_keeps_data_and_adds_protection(self):
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        old_schema = Path(__file__).resolve().parent / "tests_data" / "legacy_schema_fixture.sql"
        con = sqlite3.connect(core.DB_PATH)
        con.executescript(old_schema.read_text(encoding="utf-8"))
        con.execute("INSERT INTO users(username,display_name,role,salt,password_hash,created_at) VALUES('old','قديم','admin','00','00','2025-01-01')")
        con.execute("INSERT INTO records(record_no,qr_token,kind,student_key,student_name,class_name,created_at,user_id) VALUES('EN-2025-000001','t1','late','K','ن','ق','2025-01-01 08:00:00',1)")
        con.commit(); con.close()
        core.init_db()
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
            migrated = c.execute("SELECT created_at,issued_at FROM records").fetchone()
            self.assertEqual(migrated["issued_at"], migrated["created_at"])
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM records")
        self.assertEqual(core.setting("schema_version"), core.SCHEMA_VERSION)


class TestStudentImport(Base):
    def test_idempotent_and_template_empty(self):
        rep = core.import_students(str(self.csv), self.admin)
        self.assertEqual((rep.inserted, rep.updated), (0, 2))
        template = Path(__file__).resolve().parent / "قالب_قائمة_التلاميذ.csv"
        self.assertEqual(core.read_students_file(str(template)), [])

    def test_two_different_students_with_same_id_abort_import_atomically(self):
        p = self.tmp / "dup.csv"
        write_csv(p, [["N-9", "أ", "ب", "", "", "", "1"], ["N-10", "ج", "د", "", "", "", "1"], ["N-9", "هـ", "و", "", "", "", "2"]])
        with self.assertRaises(ValueError) as cm:
            core.import_students(str(p), self.admin)
        self.assertIn("N-9", str(cm.exception))
        self.assertIsNone(core.get_student("N-10"), "nothing may be imported when the file is ambiguous")

    def test_identical_duplicate_rows_collapse_and_missing_ids_skipped(self):
        p = self.tmp / "d2.csv"
        write_csv(p, [["Z-1", "أ", "ب", "", "", "", "1"], ["Z-1", "أ", "ب", "", "", "", "1"], ["", "ج", "د", "", "", "", "1"]])
        rep = core.import_students(str(p), self.admin)
        self.assertEqual((rep.inserted, rep.duplicate_rows, rep.skipped_no_id), (1, 1, 1))
        p2 = self.tmp / "noid.csv"
        write_csv(p2, [["", "ج", "د", "", "", "", "1"]])
        with self.assertRaises(ValueError):
            core.import_students(str(p2), self.admin)

    def test_arabic_digits_and_float_style_ids_match_same_student(self):
        p = self.tmp / "d3.csv"
        write_csv(p, [["٢٠١٢٣٤", "أ", "ب", "", "", "", "1"]])
        core.import_students(str(p), self.admin)
        write_csv(p, [["201234.0", "أ", "ب", "", "", "", "1"]])
        rep = core.import_students(str(p), self.admin)
        self.assertEqual((rep.inserted, rep.updated), (0, 1))
        self.assertEqual(core.normalize_student_key(2012345678.0), "2012345678")
        with self.assertRaises(ValueError):
            core.normalize_student_key("2.01234E+17")

    def test_windows_1256_csv_and_semicolon_delimiter(self):
        p = self.tmp / "ar.csv"
        write_csv(p, [["W-1", "بوفاطيس", "لخضر", "ذكر", "ثالثة", "رياضيات", "2"]], encoding="cp1256", delimiter=";")
        core.import_students(str(p), self.admin)
        s = core.get_student("W-1")
        self.assertEqual((s["last_name"], s["first_name"]), ("بوفاطيس", "لخضر"))

    def test_html_export_with_bom_and_missing_closing_tr(self):
        html_doc = ("\ufeff<html><body><table><tr><td>تقرير</td></tr><tr><th>رقم التعريف<th>اللقب<th>الاسم<th>القسم"
                    "<tr><td>H-1<td>حاج<td>علي<td>3 م 1<tr><td>H-2<td>سعد<td>نور<td>3 م 2</table></body></html>")
        p = self.tmp / "export.xls"
        p.write_bytes(html_doc.encode("utf-8"))
        rep = core.import_students(str(p), self.admin)
        self.assertEqual(rep.inserted, 2)
        self.assertEqual(core.get_student("H-2")["class_name"], "3 م 2")

    def test_xlsx_numeric_ids_and_second_sheet(self):
        from openpyxl import Workbook
        wb = Workbook(); wb.active.append(["ورقة فارغة"]); ws = wb.create_sheet()
        ws.append(["رقم التعريف", "اللقب", "الاسم", "القسم"]); ws.append([2012345678.0, "ق", "ل", "1"])
        p = self.tmp / "s.xlsx"; wb.save(p)
        core.import_students(str(p), self.admin)
        self.assertIsNotNone(core.get_student("2012345678"))

    def test_update_keeps_history_and_reports_renames_and_deactivation(self):
        r = core.create_late_slip(self.s1, self.sup, "تأخر")
        write_csv(self.csv, [["T-001", "جديد", "اسم", "ذكر", "أولى", "علوم", "5"]])
        rep = core.import_students(str(self.csv), self.admin, deactivate_missing=True)
        self.assertEqual((rep.updated, rep.renamed, rep.deactivated), (1, 1, 1))
        self.assertEqual(core.record_by_id(r["id"])["student_name"], "تجريبي أحمد", "historical snapshot must not change")
        self.assertEqual(core.get_student("T-002")["active"], 0)
        self.assertIsNotNone(core.get_student("T-002"), "inactive students are kept, never deleted")

    def test_header_with_family_name_column_does_not_swap(self):
        p = self.tmp / "h.csv"
        write_csv(p, [["F-1", "العائلة", "الشخصي", "", "", "", "1"]], header=("رقم التعريف", "اللقب", "الاسم", "الجنس", "السنة", "الشعبة", "القسم"))
        core.import_students(str(p), self.admin)
        self.assertEqual(core.get_student("F-1")["last_name"], "العائلة")


class TestRecords(Base):
    def test_sequential_unique_numbers_across_threads(self):
        errors, nos = [], []
        def work():
            try:
                for _ in range(10):
                    nos.append(core.create_late_slip(self.s1, self.sup, "x")["record_no"])
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        ts = [threading.Thread(target=work) for _ in range(5)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(nos), 50); self.assertEqual(len(set(nos)), 50)
        self.assertEqual(sorted(nos)[0][-6:], "000001"); self.assertEqual(sorted(nos)[-1][-6:], "000050")

    def test_tokens_random_and_carry_no_student_data(self):
        a, b = core.create_late_slip(self.s1, self.sup, "x"), core.create_late_slip(self.s1, self.sup, "x")
        self.assertNotEqual(a["qr_token"], b["qr_token"])
        self.assertRegex(a["qr_token"], r"^[0-9a-f]{48}$")
        self.assertNotIn("T-001", core.qr_url(a["qr_token"]))

    def test_late_slip_requires_reason_and_records_supervisor(self):
        with self.assertRaises(ValueError):
            core.create_late_slip(self.s1, self.sup, "   ")
        r = core.create_late_slip(self.s1, self.sup, "حافلة")
        self.assertEqual(r["user_id"], self.sup); self.assertEqual(r["status"], "valid")

    def test_absence_days_exclude_return_date_and_edge_cases(self):
        today = date.today()
        self.assertEqual(core.absence_days("2024-02-28", "2024-03-01")[2], 2)          # leap year; return date excluded
        self.assertEqual(core.absence_days("2023-02-28", "2023-03-01")[2], 1)
        self.assertEqual(core.absence_days("2024-12-31", "2025-01-02")[2], 2)          # across years
        self.assertEqual(core.absence_days("٢٠٢٤-٠١-١٥", "2024-01-15")[2], 0)           # same day = 0, Arabic digits
        self.assertEqual(core.absence_days("2025-01-10", "2025-01-12")[2], 2)          # return day excluded
        self.assertEqual(core.absence_days("2025-01-10", "2025-01-13")[2], 3)
        for bad in (("2024-03-02", "2024-03-01"), ("15/01/2024", "16/01/2024"), ("2024-02-30", "2024-03-01"),
                    ((today + timedelta(days=3)).isoformat(),) * 2, ("2022-01-01", "2024-01-01")):
            with self.assertRaises(ValueError, msg=str(bad)):
                core.absence_days(*bad)


    def test_schema_v5_migrates_existing_absence_counts_excluding_return_day(self):
        row = core.create_absence_return(self.s1, self.sup, "2025-01-10", "2025-01-12", "مرض", certificate_present=True)
        with core.db_connect() as conn:
            conn.execute("DROP TRIGGER IF EXISTS trg_records_immutable")
            details = json.loads(row["details_json"])
            details["days_count"] = 3
            details["date_difference_days"] = 2
            conn.execute("UPDATE records SET details_json=? WHERE id=?", (json.dumps(details, ensure_ascii=False), row["id"]))
            conn.execute("CREATE TRIGGER trg_records_immutable BEFORE UPDATE OF record_no,qr_token,kind,student_key,student_name,class_name,created_at,issued_at,user_id,reason,details_json,related_record_id ON records BEGIN SELECT RAISE(ABORT,'record content is immutable'); END")
            conn.execute("UPDATE settings SET setting_value='5' WHERE setting_key='schema_version'")
        core.init_db()
        upgraded = core.record_by_id(row["id"])
        upgraded_details = json.loads(upgraded["details_json"])
        self.assertEqual(upgraded_details["days_count"], 2)
        self.assertEqual(upgraded_details["date_difference_days"], 2)
        with core.db_connect() as conn:
            audit = conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='ترحيل حساب أيام الغياب إلى قاعدة استبعاد يوم العودة'").fetchone()[0]
        self.assertEqual(audit, 1)

    def test_absence_periods_use_exclusive_return_date_and_overlap_refused(self):
        a = core.create_absence_return(self.s1, self.sup, "2025-01-10", "2025-01-12", "مرض", certificate_present=True)
        # The return date (Jan 12) is not an absence day, so a new same-day return/record is zero days.
        b = core.create_absence_return(self.s1, self.sup, "2025-01-12", "2025-01-12", certificate_present=True)
        self.assertEqual(json.loads(a["details_json"])["days_count"], 2)
        self.assertEqual(json.loads(b["details_json"])["days_count"], 0)
        with self.assertRaises(ValueError):
            core.create_absence_return(self.s1, self.sup, "2025-01-11", "2025-01-13", certificate_present=True)
        core.cancel_record(a["id"], self.sup, "خطأ")
        c = core.create_absence_return(self.s1, self.sup, "2025-01-11", "2025-01-12", certificate_present=True)
        tot = {t["student_name"]: t for t in core.compute_stats()["absence_totals"]}["تجريبي أحمد"]
        self.assertEqual((tot["n"], tot["days"]), (2, 0 + 1))

    def test_absence_entry_rule_excludes_return_date_and_requires_guardian_after_two_absence_days(self):
        # Monday -> Wednesday = two actual absence days (Mon/Tue); Wednesday return is excluded.
        med = core.create_absence_return(self.s1, self.sup, "2025-01-06", "2025-01-08", "مرض",
                                         proof_method="medical_certificate", certificate_present=True)
        md = json.loads(med["details_json"])
        self.assertEqual((md["date_difference_days"], md["days_count"], md["proof_method"]), (2, 2, "medical_certificate"))
        with self.assertRaisesRegex(ValueError, "استلام الشهادة الطبية"):
            core.create_absence_return(self.s2, self.sup, "2025-01-01", "2025-01-02", proof_method="medical_certificate")
        # Monday -> Thursday is three absence days (Mon/Tue/Wed); certificate alone is rejected.
        with self.assertRaisesRegex(ValueError, "حضور ولي الأمر"):
            core.create_absence_return(self.s2, self.sup, "2025-01-06", "2025-01-09", proof_method="medical_certificate")
        # Guardian route requires an explicit physical-presence confirmation and encrypted ID.
        with self.assertRaisesRegex(ValueError, "تأكيد حضور"):
            with mock.patch.object(core, "protect_sensitive_text", fake_protect):
                core.create_absence_return(self.s2, self.sup, "2025-01-06", "2025-01-09", proof_method="guardian",
                                           guardian_name="ولي تجريبي", relationship="الأب", national_id=FULL_NID,
                                           guardian_present=False)
        with mock.patch.object(core, "protect_sensitive_text", fake_protect):
            guardian = core.create_absence_return(self.s2, self.sup, "2025-01-06", "2025-01-09", proof_method="guardian",
                                                  guardian_name="ولي تجريبي", relationship="الأب", national_id=FULL_NID,
                                                  guardian_present=True)
        gd = json.loads(guardian["details_json"])
        self.assertEqual(gd["proof_method"], "guardian")
        self.assertTrue(gd["guardian_present"])
        self.assertRegex(gd["guardian_verified_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(gd["national_id_last4"], FULL_NID[-4:])
        self.assertNotIn(FULL_NID, guardian["details_json"])
        self.assertEqual(core.mask_national_id(gd)[-4:], FULL_NID[-4:])

    def test_absence_guardian_identity_reveal_is_admin_only_and_audited(self):
        with mock.patch.object(core, "protect_sensitive_text", fake_protect):
            row = core.create_absence_return(self.s1, self.sup, "2025-02-01", "2025-02-04", proof_method="guardian",
                                             guardian_name="ولي", relationship="الأب", national_id=FULL_NID,
                                             guardian_present=True)
        with mock.patch.object(core, "reveal_sensitive_text", fake_reveal):
            self.assertEqual(core.reveal_national_id(row["id"], self.admin), FULL_NID)
        with self.assertRaises(core.PermissionDenied):
            core.reveal_national_id(row["id"], self.sup)

    def test_recent_duplicate_finder_is_same_student_and_same_kind_only(self):
        recent = core.create_late_slip(self.s1, self.sup, "حافلة")
        self.assertEqual(core.find_recent_duplicate(self.s1['student_key'], 'late')['id'], recent['id'])
        self.assertIsNone(core.find_recent_duplicate(self.s2['student_key'], 'late'))
        self.assertIsNone(core.find_recent_duplicate(self.s1['student_key'], 'exit_permit'))

    def test_startup_integrity_check(self):
        core.check_database_integrity()
        with core.db_connect() as conn:
            conn.execute("CREATE TABLE harmless_integrity_check(value TEXT)")
        core.check_database_integrity()

    def test_workstation_clock_rejects_implausible_or_backwards_time(self):
        self.permit()
        with core.db_connect() as conn:
            latest = conn.execute("SELECT MAX(issued_at) FROM records").fetchone()[0]
            earlier = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S") - timedelta(hours=1)
            with self.assertRaisesRegex(ValueError, "أقدم"):
                core.validate_workstation_clock(conn, earlier)
            with self.assertRaisesRegex(ValueError, "غير منطقي"):
                core.validate_workstation_clock(conn, datetime(2010, 1, 1, 0, 0, 0))

    def test_issued_at_is_second_precise_immutable_and_reference_is_unified(self):
        row = core.create_late_slip(self.s1, self.sup, "حافلة")
        self.assertEqual(row["issued_at"], row["created_at"])
        self.assertRegex(row["issued_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertTrue(row["record_no"].startswith("SP-"))
        with core.db_connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE records SET issued_at='2000-01-01 00:00:00' WHERE id=?", (row["id"],))

    def test_exit_permit_privacy_and_fields(self):
        p = self.permit()
        d = json.loads(p["details_json"])
        self.assertEqual(d["national_id_last4"], FULL_NID[-4:])
        self.assertNotIn(FULL_NID, p["details_json"], "full NID must not be stored in clear text")
        self.assertNotIn(FULL_NID, core.mask_national_id(d))
        self.assertTrue(core.mask_national_id(d).endswith(FULL_NID[-4:]))
        self.assertNotIn(FULL_NID, core.qr_url(p["qr_token"]))
        self.assertNotIn(FULL_NID[-4:], core.qr_url(p["qr_token"]))
        self.assertTrue(p["record_no"].startswith("SP-"))
        for bad in ("12ab56", "123", "1" * 25, ""):
            with self.assertRaises(ValueError):
                self.permit(nid=bad)

    def test_reveal_national_id_admin_only_and_audited(self):
        p = self.permit()
        with mock.patch.object(core, "reveal_sensitive_text", fake_reveal):
            self.assertEqual(core.reveal_national_id(p["id"], self.admin), FULL_NID)
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='عرض رقم الهوية الكامل'").fetchone()[0], 1)

    def test_dpapi_unavailable_blocks_permit_instead_of_storing_plaintext(self):
        if os.name == "nt":
            self.skipTest("Windows has DPAPI")
        with self.assertRaises(RuntimeError):
            core.create_exit_permit(self.s1, self.sup, "x", "ولي", "أب", FULL_NID)
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)

    def test_cancel_keeps_record_and_audit_trail(self):
        p = self.permit()
        with self.assertRaises(ValueError):
            core.cancel_record(p["id"], self.sup, "  ")
        c = core.cancel_record(p["id"], self.sup, "تراجع ولي الأمر")
        self.assertEqual((c["status"], c["cancelled_by"], c["cancelled_reason"]), ("cancelled", self.sup, "تراجع ولي الأمر"))
        self.assertRegex(c["cancelled_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        with self.assertRaises(ValueError):
            core.cancel_record(p["id"], self.sup, "مرة ثانية")
        with self.assertRaises(ValueError):
            core.return_from_exit(p["id"], self.sup)               # cancelled permit cannot be used to return
        with core.db_connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)

    def test_return_flow_is_atomic_single_use_and_linked(self):
        p = self.permit()
        slip = core.return_from_exit(p["id"], self.sup)
        self.assertEqual((slip["kind"], slip["related_record_id"]), ("exit_return", p["id"]))
        self.assertEqual(json.loads(slip["details_json"])["permit_no"], p["record_no"])
        self.assertEqual(core.record_by_id(p["id"])["status"], "returned")
        with self.assertRaises(ValueError):
            core.return_from_exit(p["id"], self.sup)
        with self.assertRaises(ValueError):
            core.return_from_exit(slip["id"], self.sup)            # not a permit
        # if slip creation fails, the permit must stay valid (no half-done state)
        p2 = self.permit(self.s2)
        with mock.patch.object(core, "_insert_record", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                core.return_from_exit(p2["id"], self.sup)
        self.assertEqual(core.record_by_id(p2["id"])["status"], "valid")


class TestStatsAndExport(Base):
    def test_stats_exclude_cancelled_and_reprints(self):
        core.create_late_slip(self.s1, self.sup, "حافلة"); l2 = core.create_late_slip(self.s2, self.admin, "حافلة")
        p = self.permit(); core.cancel_record(p["id"], self.sup, "خطأ"); core.cancel_record(l2["id"], self.admin, "مكرر")
        with mock.patch.object(core, "create_pdf", return_value=self.tmp / "mock.pdf"):
            core.print_record(p["id"], self.sup, launch=False); core.print_record(p["id"], self.sup, launch=False)
        st = core.compute_stats()
        self.assertEqual((st["today_total"], st["month_total"]), (1, 1))
        self.assertEqual(sum(r["n"] for r in st["by_class"]), 1)
        self.assertEqual(sum(r["n"] for r in st["by_reason"]), 1)
        self.assertEqual({c["record_no"] for c in st["cancellations"]}, {p["record_no"], l2["record_no"]})
        self.assertTrue(all(c["cancelled_by"] and c["cancelled_reason"] for c in st["cancellations"]))
        self.assertEqual(sum(r["n"] for r in st["by_day"]), 1)

    def test_reprint_never_creates_new_record_but_counts_prints(self):
        p = self.permit()
        before = core.compute_stats()["month_total"]
        with mock.patch.object(core, "create_pdf", return_value=self.tmp / "mock.pdf") as pdf_mock:
            core.print_record(p["id"], self.sup, launch=False); core.print_record(p["id"], self.sup, launch=False)
        self.assertFalse(pdf_mock.call_args_list[0].kwargs["reprint"])
        self.assertTrue(pdf_mock.call_args_list[1].kwargs["reprint"])
        reprint_at = pdf_mock.call_args_list[1].kwargs["reprint_at"]
        self.assertRegex(reprint_at, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
            latest_print = c.execute("SELECT happened_at FROM audit_logs WHERE action='إعادة طباعة رخصة' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(latest_print["happened_at"], reprint_at)
        r = core.record_by_id(p["id"])
        self.assertEqual((r["printed_count"], r["record_no"], r["qr_token"]), (2, p["record_no"], p["qr_token"]))
        self.assertEqual(core.compute_stats()["month_total"], before)

    def test_csv_export_neutralises_formulas_and_keeps_arabic(self):
        core.create_late_slip(self.s1, self.sup, "=HYPERLINK(\"http://evil\",\"x\")")
        out = self.tmp / "o.csv"
        self.assertEqual(core.export_records_csv(str(out), self.sup), 1)
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK", text); self.assertIn("تجريبي أحمد", text)
        self.assertEqual(core.export_students_csv(str(self.tmp / "st.csv"), self.admin), 2)


class TestBackupRestore(Base):
    def test_daily_automatic_backup_is_verified_and_admin_only(self):
        backup_root = self.tmp / "backups"
        backup_root.mkdir()
        with mock.patch.object(core, "DATA_DIR", self.tmp):
            # Automatic daily backup runs for whichever active account signs in.
            first = core.automatic_backup_if_due(self.sup)
            self.assertTrue(first and first.exists())
            core.validate_database(first)
            # A second checkpoint on the same day must refresh the file, not skip it.
            core.create_late_slip(self.s1, self.sup, "بعد أول نسخة")
            refreshed = core.automatic_backup_if_due(self.admin)
            self.assertEqual(refreshed, first)
            # sqlite3.Connection context managers commit/rollback but do NOT close.
            # Explicit finally prevents WinError 32 when the next test resets the shared DB.
            c = sqlite3.connect(refreshed)
            try:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM records WHERE kind='late'").fetchone()[0], 1)
            finally:
                c.close()
            first.write_bytes(b"corrupt copy")
            repaired = core.automatic_backup_if_due(self.admin)
            self.assertTrue(repaired and repaired.exists())
            core.validate_database(repaired)
            self.assertTrue(list(backup_root.glob("*.corrupt.sqlite3")))

    def test_backup_verified_and_restore_roundtrip_with_safety_copy(self):
        a = core.create_late_slip(self.s1, self.sup, "قبل النسخة")
        bak = core.backup_database(self.tmp / "backup.sqlite3", self.admin)
        core.validate_database(bak)
        b = core.create_late_slip(self.s1, self.sup, "بعد النسخة")
        previous = core.restore_database(bak, self.admin)
        self.assertTrue(previous.exists())
        with core.db_connect() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT record_no FROM records")], [a["record_no"]])
        con = sqlite3.connect(previous); self.assertEqual(con.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2); con.close()
        u, _ = core.authenticate("sup1", PW); self.assertIsNotNone(u)
        self.assertIsNotNone(core.record_by_token(a["qr_token"]))
        with self.assertRaises(sqlite3.IntegrityError):
            with core.db_connect() as c:
                c.execute("DELETE FROM records")                    # protection survives restore

    def test_bad_restore_files_leave_current_data_untouched(self):
        core.create_late_slip(self.s1, self.sup, "حالي")
        garbage = self.tmp / "garbage.sqlite3"; garbage.write_bytes(b"not a database" * 50)
        other = self.tmp / "other.sqlite3"; con = sqlite3.connect(other); con.execute("CREATE TABLE x(a)"); con.commit(); con.close()
        noadmin = self.tmp / "noadmin.sqlite3"
        core.backup_database(noadmin, self.admin)
        con = sqlite3.connect(noadmin); con.execute("UPDATE users SET active=0 WHERE role='admin'"); con.commit(); con.close()
        for bad in (garbage, other, noadmin, self.tmp / "missing.sqlite3"):
            with self.assertRaises(Exception, msg=str(bad)):
                core.restore_database(bad, self.admin)
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
        u, _ = core.authenticate("director", PW); self.assertIsNotNone(u)

    def test_restore_rolls_back_when_final_validation_fails(self):
        core.create_late_slip(self.s1, self.sup, "حالي")
        bak = core.backup_database(self.tmp / "ok.sqlite3", self.admin)
        calls = {"n": 0}
        real = core.validate_database
        def flaky(p):
            calls["n"] += 1
            if calls["n"] == 3 and Path(p) == core.DB_PATH:      # the post-replace check
                raise ValueError("simulated corruption")
            return real(p)
        with mock.patch.object(core, "validate_database", flaky):
            with self.assertRaises(ValueError):
                core.restore_database(bak, self.admin)
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)

    def test_restore_migrates_legacy_03_and_04_backups_before_validation(self):
        core.create_late_slip(self.s1, self.sup, "سجل تاريخي")
        for old_version in ("2", "3"):
            legacy = self.tmp / f"legacy_{old_version}.sqlite3"
            core._copy_db(core.DB_PATH, legacy)
            with closing(sqlite3.connect(legacy)) as c:
                c.execute("DROP TRIGGER IF EXISTS trg_records_immutable")
                c.execute("ALTER TABLE records DROP COLUMN issued_at")
                c.execute("UPDATE settings SET setting_value=? WHERE setting_key='schema_version'", (old_version,))
                c.commit()
            core.restore_database(legacy, self.admin)
            with core.db_connect() as c:
                row = c.execute("SELECT issued_at,created_at FROM records ORDER BY id LIMIT 1").fetchone()
                self.assertEqual(row["issued_at"], row["created_at"])
                self.assertEqual(c.execute("SELECT setting_value FROM settings WHERE setting_key='schema_version'").fetchone()[0], core.SCHEMA_VERSION)

    def test_restore_migrates_v5_absence_days_before_replacing_live_database(self):
        row = core.create_absence_return(self.s1, self.sup, "2025-01-10", "2025-01-12", "مرض", certificate_present=True)
        legacy = self.tmp / "legacy_v5.sqlite3"
        core._copy_db(core.DB_PATH, legacy)
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("DROP TRIGGER IF EXISTS trg_records_immutable")
            details = json.loads(conn.execute("SELECT details_json FROM records WHERE id=?", (row["id"],)).fetchone()[0])
            details["days_count"] = 3  # 0.5.1 stored both the first and return dates
            details["date_difference_days"] = 2
            conn.execute("UPDATE records SET details_json=? WHERE id=?", (json.dumps(details, ensure_ascii=False), row["id"]))
            conn.execute("CREATE TRIGGER trg_records_immutable BEFORE UPDATE OF record_no,qr_token,kind,student_key,student_name,class_name,created_at,issued_at,user_id,reason,details_json,related_record_id ON records BEGIN SELECT RAISE(ABORT,'record content is immutable'); END")
            conn.execute("UPDATE settings SET setting_value='5' WHERE setting_key='schema_version'")
            conn.commit()
        core.restore_database(legacy, self.admin)
        restored = json.loads(core.record_by_id(row["id"])["details_json"])
        self.assertEqual(restored["days_count"], 2)
        self.assertEqual(restored["date_difference_days"], 2)
        self.assertEqual(core.setting("schema_version"), core.SCHEMA_VERSION)

    def test_external_daily_backup_is_mirrored_and_verified(self):
        external = self.tmp / "usb"; external.mkdir()
        core.set_setting("external_backup_path", str(external))
        result = core.automatic_backup_if_due(self.sup)
        self.assertTrue(result and result.exists())
        mirror = external / result.name
        self.assertTrue(mirror.exists())
        core.validate_database(mirror)
        self.assertEqual(core.setting("external_backup_last_error", ""), "")

    def test_contrast_helpers_guarantee_wcag_text_pair(self):
        for bg in ("#000000", "#FFFFFF", "#FFFFFE", "#010101", "#777777", "#185A8D"):
            fg = core.text_color_for(bg)
            self.assertGreaterEqual(core.contrast_ratio(bg, fg), 4.5)

    def test_backup_to_unwritable_location_fails_cleanly(self):
        with self.assertRaises(Exception):
            core.backup_database(self.tmp / "no_such_dir" / "b.sqlite3", self.admin)


class TestQrServer(Base):
    def setUp(self):
        super().setUp()
        ok, msg = core.ensure_local_server(port=0)
        self.assertTrue(ok, msg)
        self.port = core._SERVER.server_address[1]
        core._RATE.clear()
        while not core.EVENT_QUEUE.empty():
            core.EVENT_QUEUE.get_nowait()

    def tearDown(self):
        core.stop_local_server()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{urllib.parse.quote(path, safe='/')}", timeout=5) as r:
                return r.status, r.read().decode("utf-8"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)

    def test_valid_cancelled_and_unknown_tokens(self):
        p = self.permit()
        code, body, hdr = self.get("/v/" + p["qr_token"])
        self.assertEqual(code, 200); self.assertIn(p["record_no"], body); self.assertIn("ساري", body)
        self.assertEqual(hdr.get("Cache-Control"), "no-store"); self.assertEqual(hdr.get("X-Content-Type-Options"), "nosniff")
        for secret in (FULL_NID, FULL_NID[-4:], "ولي تجريبي", "T-001", "موعد طبي"):
            self.assertNotIn(secret, body, "verification page must not leak guardian/ID/student key/reason")
        core.cancel_record(p["id"], self.sup, "تراجع")
        code, body, _ = self.get("/v/" + p["qr_token"])
        self.assertEqual(code, 200); self.assertIn("ملغى", body); self.assertIn("لا يجوز الاعتماد", body)
        for bad in ("/v/" + "0" * 48, "/v/abc", "/v/../../etc/passwd", "/v/' OR 1=1 --", "/", "/students", "/v/"):
            code, body, _ = self.get(bad)
            self.assertIn(code, (404,), bad)
            self.assertNotIn("تجريبي", body)

    def test_returned_status_and_scan_event(self):
        p = self.permit(); core.return_from_exit(p["id"], self.sup)
        _, body, _ = self.get("/v/" + p["qr_token"])
        self.assertIn("تمت العودة", body)
        self.assertEqual(core.EVENT_QUEUE.get(timeout=2), p["qr_token"])

    def test_qr_health_check_returns_lan_address_and_success(self):
        ok, url, detail = core.qr_health_check()
        self.assertTrue(ok)
        self.assertIn(f":{core._SERVER.server_address[1]}/health", url)
        self.assertEqual(detail, "SchoolPass OK")

    def test_rate_limit_and_health(self):
        self.assertEqual(self.get("/health")[0], 200)
        codes = {self.get("/v/" + "f" * 48)[0] for _ in range(45)}
        self.assertIn(429, codes)

    def test_html_is_escaped(self):
        s = {"student_key": "X-1", "class_name": "<b>x</b>", "last_name": "<script>alert(1)</script>", "first_name": "q"}
        r = core.create_late_slip(s, self.sup, "x")
        _, body, _ = self.get("/v/" + r["qr_token"])
        self.assertNotIn("<script>alert", body); self.assertIn("&lt;script&gt;", body)


class TestQrHost(Base):
    def test_host_saved_validated_and_used_in_urls(self):
        with mock.patch.object(core, "get_local_ip", return_value="192.168.1.20"):
            self.assertEqual(core.ensure_qr_host_saved(), "192.168.1.20")
        with mock.patch.object(core, "get_local_ip", return_value="192.168.1.99"):   # DHCP changed the address
            self.assertEqual(core.qr_host_mismatch(), ("192.168.1.20", "192.168.1.99"))
            self.assertTrue(core.qr_url("t").startswith("http://192.168.1.20:8765/v/"))
        for bad in ("http://x", "1.2.3.4:80", "a b", "", "../x"):
            with self.assertRaises(ValueError):
                core.set_qr_host_as(self.admin, bad)
        core.set_qr_host_as(self.admin, "192.168.1.99")
        self.assertEqual(core.setting("qr_host"), "192.168.1.99")


if __name__ == "__main__":
    unittest.main(verbosity=2)
