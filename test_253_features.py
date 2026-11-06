# -*- coding: utf-8 -*-
"""Regression coverage for the combined SchoolPass 2.5.3 features."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testenv  # noqa: E402
import schoolpass_core as core  # noqa: E402

core.PBKDF2_ITERATIONS = 2000


class TestSchoolPass253(unittest.TestCase):
    def setUp(self):
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        core.init_db()
        self.admin, _ = core.bootstrap_admin("director", "مدير الاختبار", "TestPassword123!")
        self.supervisor = core.create_user_as(self.admin, "supervisor", "مشرف الاختبار", "TestPassword123!")
        csv_path = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / "students.csv"
        csv_path.write_text(
            "رقم التعريف,اللقب,الاسم,الجنس,السنة,الشعبة,القسم\n"
            "S-253-1,تجريبي,أحمد,ذكر,أولى,علوم,أولى علوم 1\n"
            "S-253-2,اختباري,سارة,أنثى,ثانية,آداب,ثانية آداب 2\n",
            encoding="utf-8-sig",
        )
        core.import_students(str(csv_path), self.admin)
        self.student1 = core.get_student("S-253-1")
        self.student2 = core.get_student("S-253-2")

    def test_late_reasons_are_structured_and_keep_optional_note(self):
        cases = {
            "consultation": "التواجد في الاستشارة",
            "transport": "التأخر بسبب انعدام النقل",
            "class_delay": "التأخر في الالتحاق بالقسم",
        }
        for key, label in cases.items():
            row = core.create_late_slip(self.student1, self.supervisor, label, "ملاحظة قصيرة")
            detail = json.loads(row["details_json"])
            self.assertEqual(detail["late_reason"], key)
            self.assertEqual(detail["late_reason_label"], label)
            self.assertEqual(detail["note"], "ملاحظة قصيرة")

    def test_special_entries_are_separate_from_absence_and_expulsions_are_counted_by_teacher(self):
        with self.assertRaisesRegex(ValueError, "اسم الأستاذ والمادة"):
            core.create_special_entry(self.student1, self.supervisor, "class_expulsion", "", "رياضيات")
        expulsion1 = core.create_special_entry(
            self.student1, self.supervisor, "class_expulsion", "الأستاذ الأول", "رياضيات")
        expulsion2 = core.create_special_entry(
            self.student2, self.supervisor, "class_expulsion", "الأستاذ الأول", "فيزياء")
        expulsion3 = core.create_special_entry(
            self.student1, self.supervisor, "class_expulsion", "الأستاذ الأول", "رياضيات")
        admin_visit = core.create_special_entry(
            self.student2, self.supervisor, "administration_visit")
        self.assertEqual(expulsion1["kind"], "special_entry")
        self.assertEqual(admin_visit["kind"], "special_entry")
        report = core.get_expulsion_report(core.date_today(), core.date_today(), "الأستاذ الأول")
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["by_teacher"][0]["count"], 3)
        self.assertEqual(report["by_teacher"][0]["student_count"], 2)
        self.assertEqual(len(report["by_teacher"][0]["students"]), 2)
        stats = core.compute_stats()
        self.assertEqual(stats["expulsion_by_teacher"][0]["teacher"], "الأستاذ الأول")
        self.assertEqual(stats["expulsion_by_teacher"][0]["count"], 3)
        self.assertEqual(stats["expulsion_by_teacher"][0]["student_count"], 2)
        # A school-office presence or classroom expulsion never becomes a medical-absence interval.
        self.assertEqual(stats["absence_totals"], [])

    def test_expulsion_teacher_counts_distinct_pupils_not_only_event_rows(self):
        core.create_special_entry(self.student1, self.supervisor, "class_expulsion", "أستاذ التكرار", "فيزياء")
        core.create_special_entry(self.student1, self.supervisor, "class_expulsion", "أستاذ التكرار", "فيزياء")
        report = core.get_expulsion_report(core.date_today(), core.date_today(), "أستاذ التكرار")
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["by_teacher"][0]["count"], 2)
        self.assertEqual(report["by_teacher"][0]["student_count"], 1)

    def test_expulsion_report_filters_dates_and_teacher(self):
        core.create_special_entry(self.student1, self.supervisor, "class_expulsion", "أستاذ أ", "رياضيات")
        core.create_special_entry(self.student2, self.supervisor, "class_expulsion", "أستاذ ب", "علوم")
        today = core.date_today()
        only_a = core.get_expulsion_report(today, today, "أستاذ أ")
        all_rows = core.get_expulsion_report(today, today, "الكل")
        self.assertEqual(only_a["total"], 1)
        self.assertEqual(only_a["records"][0]["subject"], "رياضيات")
        self.assertEqual(all_rows["total"], 2)
        with self.assertRaisesRegex(ValueError, "بداية|نهاية"):
            core.get_expulsion_report(today, (date.today() - timedelta(days=1)).isoformat(), "الكل")

    def test_long_absence_requires_guardian_before_medical_certificate_confirmation(self):
        start = (date.today() - timedelta(days=4)).isoformat()
        today = core.date_today()
        with self.assertRaisesRegex(ValueError, "حضور ولي الأمر"):
            core.create_absence_return(
                self.student1, self.supervisor, start, today,
                proof_method="medical_certificate", certificate_present=False)

    def test_mixed_medical_and_guardian_proof_for_long_absence(self):
        start = (date.today() - timedelta(days=4)).isoformat()
        with mock.patch.object(core, "protect_sensitive_text", side_effect=lambda v: "DPAPI1:" + v):
            row = core.create_absence_return(
                self.student1, self.supervisor, start, core.date_today(),
                proof_method="medical_and_guardian", certificate_present=True,
                guardian_name="ولي تجريبي", relationship="الأب", national_id="123456789012",
                guardian_present=True)
        detail = json.loads(row["details_json"])
        self.assertEqual(detail["days_count"], 4)
        self.assertEqual(detail["proof_method"], "medical_and_guardian")
        self.assertTrue(detail["medical_certificate_confirmed"])
        self.assertTrue(detail["guardian_present"])
        self.assertEqual(detail["national_id_last4"], "9012")

    def test_other_justification_is_recorded_and_requires_a_description(self):
        start = (date.today() - timedelta(days=2)).isoformat()
        with self.assertRaisesRegex(ValueError, "اكتب سبب التبرير الآخر"):
            core.create_absence_return(self.student1, self.supervisor, start, core.date_today(), proof_method="other")
        row = core.create_absence_return(
            self.student1, self.supervisor, start, core.date_today(),
            proof_method="other", other_reason="ظرف عائلي مبرر")
        self.assertEqual(json.loads(row["details_json"])["other_proof_reason"], "ظرف عائلي مبرر")

    def test_arabic_visual_does_not_reshape_presentation_forms_twice(self):
        already_shaped = "ﻻ"  # Arabic presentation-form lam-alef
        self.assertEqual(core._arabic_visual(already_shaped), already_shaped)

    def test_expulsion_pdf_report_is_generated(self):
        core.create_special_entry(self.student1, self.supervisor, "class_expulsion", "أستاذ الطرد", "لغة عربية")
        destination = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / "expulsions.pdf"
        with mock.patch.object(core, "_font_setup", return_value="Helvetica"), \
             mock.patch.object(core, "_arabic_visual", side_effect=lambda value: str(value)):
            result = core.create_expulsion_report_pdf(
                destination, core.date_today(), core.date_today(), "الكل", self.supervisor)
        self.assertTrue(result.exists())
        self.assertEqual(result.read_bytes()[:4], b"%PDF")
        self.assertGreater(result.stat().st_size, 500)


if __name__ == "__main__":
    unittest.main()
