# -*- coding: utf-8 -*-
"""Focused regression coverage for SchoolPass 4.7.0 teacher and autocomplete features."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

import testenv  # noqa: F401 - initializes an isolated application data directory
import schoolpass_core as core


class TestV47Features:
    def setup_method(self):
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        core.init_db()
        self.admin, _ = core.bootstrap_admin("director", "مدير الاختبار", "TestPassword123!")
        self.supervisor = core.create_user_as(
            self.admin, "supervisor47", "مشرف الاختبار", "TestPassword123!"
        )
        self.student = {
            "student_key": "S-470-01",
            "last_name": "تجريبي",
            "first_name": "أحمد",
            "class_name": "أولى علوم 1",
        }
        with core.db_connect() as conn:
            conn.execute(
                """INSERT INTO students(student_key,last_name,first_name,gender,year,branch,section,
                                         class_name,active,imported_at)
                   VALUES(?,?,?,?,?,?,?,?,1,?)""",
                ("S-470-01", "تجريبي", "أحمد", "ذكر", "أولى", "علوم", "1",
                 "أولى علوم 1", core.now_iso()),
            )
        self.student = core.get_student("S-470-01")

    def test_teacher_directory_is_admin_managed_and_searchable(self):
        teacher = core.create_teacher_as(
            self.admin, "الأستاذ محمد بن صالح", "الرياضيات", "ثانية علوم 1، ثالثة علوم 2"
        )
        assert teacher["id"] > 0
        assert teacher["active"] == 1
        results = core.search_teachers("محمد")
        assert len(results) == 1
        assert results[0]["subject"] == "الرياضيات"

        with pytest.raises(ValueError, match="مسجل مسبقًا"):
            core.create_teacher_as(self.admin, "الأستاذ محمد بن صالح", "الرياضيات")

        with pytest.raises(core.PermissionDenied):
            core.create_teacher_as(self.supervisor, "أستاذ غير مخول", "الفيزياء")

        core.set_teacher_active_as(self.admin, teacher["id"], False)
        assert core.search_teachers("محمد") == []
        assert core.get_teacher(teacher["id"])["active"] == 0

    def test_expulsion_record_keeps_teacher_id_and_original_snapshot(self):
        teacher = core.create_teacher_as(self.admin, "الأستاذة ليلى", "اللغة العربية")
        row = core.create_special_entry(
            self.student, self.supervisor, "class_expulsion",
            teacher_id=teacher["id"], note="إخلال بالنظام",
        )
        details = json.loads(row["details_json"])
        assert details["expelling_teacher_id"] == teacher["id"]
        assert details["expelling_teacher"] == "الأستاذة ليلى"
        assert details["subject"] == "اللغة العربية"

        core.update_teacher_as(
            self.admin, teacher["id"], "الأستاذة ليلى الجديدة", "الأدب العربي", "ثالثة آداب"
        )
        report = core.get_expulsion_report(core.date_today(), core.date_today(), "الأستاذة ليلى")
        assert report["total"] == 1
        assert report["records"][0]["expelling_teacher"] == "الأستاذة ليلى"
        assert report["records"][0]["subject"] == "اللغة العربية"

    def test_teacher_excel_preview_detects_existing_duplicates_and_invalid_rows(self, tmp_path):
        core.create_teacher_as(self.admin, "أستاذ موجود", "علوم")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["اسم الأستاذ", "المادة", "الأقسام"])
        sheet.append(["أستاذ جديد", "فيزياء", "أولى علوم 1"])
        sheet.append(["أستاذ جديد", "فيزياء", "أولى علوم 1"])
        sheet.append(["أستاذ موجود", "علوم", ""])
        sheet.append(["أستاذ بلا مادة", "", "ثانية"])
        path = tmp_path / "teachers.xlsx"
        workbook.save(path)
        workbook.close()

        preview = core.preview_teacher_import(str(path))
        assert preview["total"] == 4
        assert preview["counts"] == {
            "ready": 1, "existing": 1, "duplicate_file": 1, "invalid": 1
        }

        result = core.import_teachers_as(str(path), self.admin)
        assert result["added"] == 1
        current = core.list_teachers(active_only=False)
        assert len(current) == 2
        assert {(r["full_name"], r["subject"]) for r in current} == {
            ("أستاذ موجود", "علوم"), ("أستاذ جديد", "فيزياء")
        }

    def test_student_autocomplete_matches_partial_arabic_name_and_class(self):
        assert [r["student_key"] for r in core.search_students("أحمد")] == ["S-470-01"]
        assert [r["student_key"] for r in core.search_students("أولى علوم")] == ["S-470-01"]

    def test_v46_database_upgrade_adds_teacher_table_without_losing_school_data(self, tmp_path):
        record = core.create_late_slip(self.student, self.supervisor, "سجل قبل التحديث")
        legacy = tmp_path / "schoolpass_4_6_1.sqlite3"
        core._copy_db(core.DB_PATH, legacy)
        with sqlite3.connect(legacy) as conn:
            conn.execute("DROP TABLE IF EXISTS teachers")
            conn.execute("DELETE FROM settings WHERE setting_key='schema_version'")
            conn.execute(
                "INSERT INTO settings(setting_key,setting_value) VALUES('schema_version','6')"
            )
            conn.commit()

        core.restore_database(legacy, self.admin)

        assert core.setting("schema_version") == "7"
        assert core.get_student("S-470-01")["student_key"] == "S-470-01"
        assert core.record_by_id(record["id"])["reason"] == "سجل قبل التحديث"
        assert core.list_teachers(active_only=False) == []

    def test_login_selector_returns_active_accounts_without_credentials(self):
        accounts = core.list_active_accounts()
        assert {r["username"] for r in accounts} == {"director", "supervisor47"}
        assert all("password_hash" not in r.keys() and "salt" not in r.keys() for r in accounts)

