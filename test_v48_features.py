# -*- coding: utf-8 -*-
"""Regression tests for SchoolPass 4.8.0 attendance, notes, gate and archive workflows."""
from __future__ import annotations
import csv
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock
import pytest
import testenv  # noqa: F401
import schoolpass_core as core


class TestV48Features:
    def setup_method(self):
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        core.init_db()
        self.admin, _ = core.bootstrap_admin("director48", "مدير الاختبار", "TestPassword123!")
        self.supervisor = core.create_user_as(
            self.admin, "supervisor48", "مشرف الاختبار", "TestPassword123!"
        )
        source = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / "students.csv"
        source.write_text(
            "رقم التعريف,اللقب,الاسم,الجنس,السنة,الشعبة,القسم\n"
            "S-480,تجريبي,أحمد,ذكر,أولى,علوم,أولى علوم 1\n",
            encoding="utf-8-sig",
        )
        core.import_students(str(source), self.admin)
        self.student = core.get_student("S-480")

    def test_monthly_attention_counts_real_absence_days_without_return_day(self):
        core.create_late_slip(self.student, self.supervisor, "تأخر أول")
        core.create_late_slip(self.student, self.supervisor, "تأخر ثان")
        core.create_absence_return(
            self.student, self.supervisor, "2026-11-01", "2026-11-03",
            "شهادة طبية", certificate_present=True,
        )
        summary = core.student_monthly_attention("S-480", self.supervisor, "2026-11")
        assert summary["late_count"] == 2
        assert summary["absence_days"] == 2
        assert summary["absence_return_count"] == 1

    def test_administrative_notes_are_audited_and_append_only(self):
        note = core.add_student_note("S-480", self.supervisor, "ملاحظة متابعة تربوية.")
        assert note["supervisor"] == "مشرف الاختبار"
        assert core.list_student_notes("S-480", self.admin)[0]["note"] == "ملاحظة متابعة تربوية."
        with core.db_connect() as conn:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("UPDATE student_notes SET note='تعديل' WHERE id=?", (note["id"],))
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("DELETE FROM student_notes WHERE id=?", (note["id"],))
            audit = conn.execute(
                "SELECT action FROM audit_logs WHERE target_type='student_note' AND target_id=?",
                (str(note["id"]),),
            ).fetchone()
        assert audit["action"] == "إضافة ملاحظة إدارية للتلميذ"

    def test_gatekeeper_tracks_physical_exit_separately_from_permit(self):
        with mock.patch.object(core, "protect_sensitive_text", side_effect=lambda value: "DPAPI1:" + value):
            permit = core.create_exit_permit(
                self.student, self.supervisor, "موعد طبي", "ولي تجريبي", "الأب", "1234567890"
            )
        assert not permit["exit_executed_at"]
        executed = core.mark_exit_executed(permit["id"], self.supervisor)
        assert executed["exit_executed_at"]
        log = core.get_gatekeeper_log(self.admin, state="executed")
        assert len(log) == 1
        assert log[0]["exit_executor_name"] == "مشرف الاختبار"
        with pytest.raises(ValueError, match="مسبقًا"):
            core.mark_exit_executed(permit["id"], self.supervisor)
        returned = core.return_from_exit(permit["id"], self.supervisor)
        assert returned["kind"] == "exit_return"
        final = core.get_gatekeeper_log(self.admin, state="returned")
        assert len(final) == 1 and final[0]["returned_at"]

    def test_explicit_print_modes_prevent_second_original_print(self):
        record = core.create_late_slip(self.student, self.supervisor, "تأخر")
        with mock.patch.object(core, "open_pdf", return_value=True):
            core.print_record(record["id"], self.supervisor, launch=False, mode="initial")
            with pytest.raises(ValueError, match="طُبعت سابقًا"):
                core.print_record(record["id"], self.supervisor, launch=False, mode="initial")
            core.print_record(record["id"], self.supervisor, launch=False, mode="reprint")
        assert core.record_by_id(record["id"])["printed_count"] == 2

    def test_annual_archive_pdf_is_created(self, tmp_path):
        core.create_late_slip(self.student, self.supervisor, "تأخر")
        path = core.create_annual_report_pdf(
            tmp_path / "annual.pdf", int(core.date_today()[:4]), self.supervisor
        )
        assert path.exists() and path.stat().st_size > 1000
