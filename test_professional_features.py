# -*- coding: utf-8 -*-
"""Regression tests for 2.2 Professional dashboard, filters, exports and student history queries."""
from __future__ import annotations
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testenv  # noqa: E402
import schoolpass_core as core  # noqa: E402
core.PBKDF2_ITERATIONS = 2000


def reset_db():
    core.stop_local_server()
    for suffix in ('', '-wal', '-shm'):
        Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
    core.init_db()
    admin, _ = core.bootstrap_admin('director', 'المدير', 'TestPassword123!')
    supervisor = core.create_user_as(admin, 'sup1', 'مشرف أول', 'TestPassword123!')
    csv_path = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'students.csv'
    csv_path.write_text('رقم التعريف,اللقب,الاسم,الجنس,السنة,الشعبة,القسم\nP-001,تجريبي,أحمد,ذكر,أولى,علوم,أولى علوم 1\nP-002,اختباري,سارة,أنثى,ثانية,آداب,ثانية آداب 2\n', encoding='utf-8-sig')
    core.import_students(str(csv_path), admin)
    return admin, supervisor, core.get_student('P-001'), core.get_student('P-002')


class TestProfessionalFeatures(unittest.TestCase):
    def setUp(self):
        self.admin, self.supervisor, self.s1, self.s2 = reset_db()
        self.r1 = core.create_late_slip(self.s1, self.supervisor, 'تأخر بسبب النقل')
        self.r2 = core.create_late_slip(self.s2, self.supervisor, 'تأخر تجريبي')

    def test_dashboard_series_periods_return_consistent_counts(self):
        for period in ('day', 'week', 'month'):
            data = core.dashboard_series(period)
            self.assertEqual(data['period'], period)
            self.assertTrue(data['labels'])
            if period == 'day':
                self.assertEqual(len(data['labels']), 24)
                self.assertEqual(data['labels'][0], '00:00')
                self.assertEqual(data['labels'][-1], '23:00')
            self.assertEqual(data['totals']['late'], 2)

    def test_records_query_filters_by_class_kind_and_search(self):
        rows = core.query_records(self.supervisor, {'kind': 'late', 'class_name': self.s1['class_name'], 'query': 'أحمد'})
        self.assertEqual([r['id'] for r in rows], [self.r1['id']])
        self.assertEqual(core.query_records(self.supervisor, {'kind': 'exit_permit'}), [])

    def test_filtered_csv_exports_only_matching_records(self):
        path = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'filtered.csv'
        n = core.export_filtered_records_csv(str(path), self.supervisor, {'class_name': self.s1['class_name']})
        self.assertEqual(n, 1)
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 2)
        self.assertIn('أحمد', rows[1][2])

    def test_filtered_pdf_report_is_created(self):
        path = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'records.pdf'
        with mock.patch.object(core, '_font_setup', return_value='Helvetica'), mock.patch.object(core, '_arabic_visual', side_effect=lambda value: str(value)):
            out = core.create_filtered_records_report_pdf(path, {'kind': 'late'}, self.supervisor)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 500)
        self.assertEqual(out.read_bytes()[:4], b'%PDF')

    def test_invalid_date_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            core.query_records(self.supervisor, {'date_from': '31/10/2026'})

    def test_student_edit_keeps_permanent_key_and_is_admin_only(self):
        updated = core.update_student_as(self.admin, self.s1['id'], {
            'last_name': 'لقب معدل', 'first_name': 'أحمد', 'class_name': 'قسم جديد',
            'gender': 'ذكر', 'year': 'ثانية', 'branch': 'علوم', 'section': '2'
        })
        self.assertEqual(updated['student_key'], 'P-001')
        self.assertEqual(updated['class_name'], 'قسم جديد')
        with self.assertRaises(core.PermissionDenied):
            core.update_student_as(self.supervisor, self.s1['id'], {'last_name':'x','first_name':'y','class_name':'z'})


    def test_a4_sheet_is_portrait_with_two_distinct_record_sources(self):
        from pypdf import PdfReader
        root = Path(tempfile.mkdtemp(dir=testenv.ROOT))
        with mock.patch.object(core, 'DATA_DIR', root), \
             mock.patch.object(core, '_font_setup', return_value='Helvetica'), \
             mock.patch.object(core, '_arabic_visual', side_effect=lambda value: 'X' * len(str(value))):
            out = core.create_a4_two_record_sheet([self.r1['id'], self.r2['id']])
        reader = PdfReader(str(out))
        self.assertEqual(len(reader.pages), 1)
        box = reader.pages[0].mediabox
        self.assertGreater(float(box.height), float(box.width))
        self.assertAlmostEqual(float(box.width), 595.2756, places=1)
        self.assertAlmostEqual(float(box.height), 841.8898, places=1)
        with self.assertRaises(ValueError):
            core.create_a4_two_record_sheet([self.r1['id'], self.r1['id']])

    def test_open_exit_lookup_detects_unclosed_permit(self):
        with mock.patch.object(core, 'protect_sensitive_text', side_effect=lambda value: 'DPAPI1:' + value):
            permit = core.create_exit_permit(self.s1, self.supervisor, 'موعد طبي', 'ولي الأمر', 'الأب', '1234567890')
        found = core.find_open_exit_for_student(self.s1['student_key'])
        self.assertEqual(found['id'], permit['id'])
        core.return_from_exit(permit['id'], self.supervisor)
        self.assertIsNone(core.find_open_exit_for_student(self.s1['student_key']))


if __name__ == '__main__':
    unittest.main()
