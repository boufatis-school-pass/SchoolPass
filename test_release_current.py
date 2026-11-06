# -*- coding: utf-8 -*-
"""Focused regression tests for SchoolPass current-release dashboard metrics and daily handover report."""
from __future__ import annotations
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


def fresh_db():
    for suffix in ('', '-wal', '-shm'):
        Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
    core.init_db()
    admin, _ = core.bootstrap_admin('director', 'المدير', 'TestPassword123!')
    supervisor = core.create_user_as(admin, 'supervisor23', 'مشرف الاختبار', 'TestPassword123!')
    csv_file = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'students.csv'
    csv_file.write_text(
        'رقم التعريف,اللقب,الاسم,الجنس,السنة,الشعبة,القسم\n'
        'S-001,تجريبي,أحمد,ذكر,أولى,علوم,أولى علوم 1\n'
        'S-002,اختباري,سارة,أنثى,ثانية,آداب,ثانية آداب 2\n', encoding='utf-8-sig')
    core.import_students(str(csv_file), admin)
    return admin, supervisor, core.get_student('S-001'), core.get_student('S-002')


class TestReleaseCurrent(unittest.TestCase):
    def setUp(self):
        self.admin, self.supervisor, self.s1, self.s2 = fresh_db()

    def test_update_checker_parses_release_versions_strictly(self):
        from app import SchoolPassApp
        self.assertEqual(SchoolPassApp._version_parts('v2.5.0'), (2, 5, 0))
        self.assertEqual(SchoolPassApp._version_parts('v2.5.2'), (2, 5, 2))
        self.assertEqual(SchoolPassApp._version_parts('v2.5.3'), (2, 5, 3))
        self.assertEqual(SchoolPassApp._version_parts('2.6.1'), (2, 6, 1))
        with self.assertRaises(ValueError):
            SchoolPassApp._version_parts('latest-release')

    def test_update_checker_ignores_archived_other_major_releases(self):
        from app import SchoolPassApp
        releases = [
            {"tag_name": "v4.9.4-local-fast-ui", "draft": False, "prerelease": False},
            {"tag_name": "v3.0.0", "draft": False, "prerelease": False},
            {"tag_name": "v3.1.0-beta", "draft": False, "prerelease": True},
            {"tag_name": "v2.5.3", "draft": False, "prerelease": False},
        ]
        chosen = SchoolPassApp._select_release_for_current_major(releases, "3.0.0")
        self.assertEqual(chosen["tag_name"], "v3.0.0")
        self.assertEqual(SchoolPassApp._select_release_for_current_major(releases, "3.1.0")["tag_name"], "v3.0.0")

    def test_dashboard_uses_unique_students_and_separate_permit_counts(self):
        core.create_late_slip(self.s1, self.supervisor, 'تأخر أول')
        core.create_late_slip(self.s1, self.supervisor, 'تأخر ثان')
        core.create_absence_return(self.s2, self.supervisor, '2026-10-29', '2026-10-31', 'شهادة طبية', certificate_present=True)
        metrics = core.dashboard_metrics()
        self.assertEqual(metrics['today']['entry_students'], 2)
        self.assertEqual(metrics['today']['late_students'], 1)
        self.assertEqual(metrics['today']['absence_students'], 1)
        self.assertEqual(metrics['today']['permits'], 3)
        details = core.dashboard_detail_records('today_entry')
        self.assertEqual(len(details), 3)
        self.assertEqual(len({r['student_key'] for r in details}), 2)

    def test_dashboard_periods_and_open_exit_are_consistent(self):
        with mock.patch.object(core, 'protect_sensitive_text', side_effect=lambda value: 'DPAPI1:' + value):
            exit_permit = core.create_exit_permit(self.s1, self.supervisor, 'موعد طبي', 'ولي تجريبي', 'الأب', '1234567890')
        metrics = core.dashboard_metrics()
        self.assertEqual(metrics['today']['exit_students'], 1)
        self.assertEqual(metrics['open_exits'], 1)
        self.assertEqual(core.dashboard_detail_records('open_exits')[0]['id'], exit_permit['id'])
        core.return_from_exit(exit_permit['id'], self.supervisor)
        after_return = core.dashboard_metrics()
        self.assertEqual(after_return['open_exits'], 0)
        self.assertEqual(after_return['today']['entry_students'], 1)
        self.assertEqual(len(core.dashboard_detail_records('today_entry')), 1)

    def test_compute_stats_includes_rolling_week_and_unique_pupils(self):
        core.create_late_slip(self.s1, self.supervisor, 'تأخر')
        core.create_late_slip(self.s1, self.supervisor, 'تأخر متكرر')
        stats = core.compute_stats()
        self.assertEqual(stats['today_unique_students'], 1)
        self.assertEqual(stats['week_total'], 2)
        self.assertEqual(stats['week_unique_students'], 1)
        self.assertTrue('week_by_kind' in stats)


    def test_approved_exit_permit_renderer_is_unchanged(self):
        import ast, hashlib
        source = (HERE / 'schoolpass_core.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_page')
        digest = hashlib.sha256(ast.dump(node, include_attributes=False).encode('utf-8')).hexdigest()
        self.assertEqual(digest, 'e4b459691a6201b748b9e31b7508befc31d4c16308e4f7ad611dbc997a90fb7f')

    def test_daily_report_pdf_created_on_a4_landscape_and_contains_all_record_types(self):
        report_day = core.date.today()
        late = core.create_late_slip(self.s1, self.supervisor, 'ازدحام المرور')
        absence = core.create_absence_return(self.s1, self.supervisor,
                                             core.date.fromordinal(report_day.toordinal() - 1).isoformat(),
                                             report_day.isoformat(), 'عودة بعد غياب', certificate_present=True)
        with mock.patch.object(core, 'protect_sensitive_text', side_effect=lambda value: 'DPAPI1:' + value):
            exit_permit = core.create_exit_permit(self.s2, self.supervisor, 'موعد طبي', 'ولي سارة', 'الأب', '1234567890')
        core.return_from_exit(exit_permit['id'], self.supervisor)
        core.cancel_record(late['id'], self.supervisor, 'تصحيح إداري')
        with core.db_connect() as conn:
            kinds = {r['kind'] for r in conn.execute(
                "SELECT kind FROM records WHERE substr(COALESCE(NULLIF(issued_at,''),created_at),1,10)=?",
                (report_day.isoformat(),))}
        self.assertTrue({'late', 'absence_return', 'exit_permit', 'exit_return'}.issubset(kinds))
        target = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'daily.pdf'
        with mock.patch.object(core, '_font_setup', return_value='Helvetica'), \
             mock.patch.object(core, '_arabic_visual', side_effect=lambda value: 'X' * len(str(value))), \
             mock.patch.object(core, 'reveal_sensitive_text', return_value='1234567890'):
            out = core.create_daily_report_pdf(target, core.date.today(), self.admin)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 600)
        self.assertEqual(out.read_bytes()[:4], b'%PDF')
        from pypdf import PdfReader
        reader = PdfReader(str(out))
        self.assertGreaterEqual(len(reader.pages), 1)
        box = reader.pages[0].mediabox
        self.assertGreater(float(box.width), float(box.height))
        self.assertAlmostEqual(float(box.width), 841.8898, places=1)
        with core.db_connect() as conn:
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='إنشاء التقرير اليومي PDF ببيانات ولي الأمر الكاملة'").fetchone()[0], 1)

    def test_daily_report_with_full_guardian_data_is_admin_only(self):
        with self.assertRaises(core.PermissionDenied):
            core.create_daily_report_pdf(Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'restricted.pdf',
                                         core.date.today(), self.supervisor)


    def test_daily_report_includes_absence_proof_method_and_complete_guardian_fields(self):
        today = core.date.today()
        guardian_start = core.date.fromordinal(today.toordinal() - 4).isoformat()
        certificate_start = core.date.fromordinal(today.toordinal() - 1).isoformat()
        with mock.patch.object(core, 'protect_sensitive_text', side_effect=lambda value: 'DPAPI1:' + value):
            core.create_absence_return(
                self.s1, self.supervisor, guardian_start, today.isoformat(),
                'متابعة حالة الغياب', proof_method='guardian', guardian_present=True,
                guardian_name='GuardianDemo', relationship='RelationshipDemo', national_id='123456789012')
            core.create_absence_return(
                self.s2, self.supervisor, certificate_start, today.isoformat(),
                'مرفق طبي', proof_method='medical_certificate', certificate_present=True)
            exit_permit = core.create_exit_permit(
                self.s2, self.supervisor, 'مراجعة طبية', 'ExitGuardianDemo', 'FatherDemo', '987654321098')
            core.return_from_exit(exit_permit['id'], self.supervisor)

        target = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'daily-full-data.pdf'
        def pdf_test_visual(value):
            # Keep recognizable test labels in the extracted PDF while retaining normal font metrics.
            text = (str(value).replace('حضور', 'Guardian').replace('ولي', 'Parent')
                               .replace('الأمر', 'Present').replace('شخصيًا', 'InPerson')
                               .replace('شهادة', 'Medical').replace('طبية', 'Certificate'))
            return ''.join(ch if ord(ch) < 128 else 'X' for ch in text)
        with mock.patch.object(core, '_arabic_visual', side_effect=pdf_test_visual), \
             mock.patch.object(core, '_font_setup', return_value='Helvetica'), \
             mock.patch.object(core, 'reveal_sensitive_text', side_effect=lambda value: value.split(':', 1)[-1]):
            out = core.create_daily_report_pdf(target, today, self.admin)
        from pypdf import PdfReader
        extracted = '\n'.join(page.extract_text() or '' for page in PdfReader(str(out)).pages)
        self.assertIn('GuardianDemo', extracted)
        self.assertIn('Relationship', extracted)
        self.assertIn('Demo', extracted)
        self.assertIn('123456789012', extracted)
        self.assertIn('987654321098', extracted)
        self.assertGreaterEqual(extracted.count('ExitGuardianDemo'), 2)
        self.assertGreaterEqual(extracted.count('987654321098'), 2)
        self.assertIn(guardian_start, extracted)
        self.assertIn(certificate_start, extracted)
        self.assertIn('Guardian', extracted)
        self.assertIn('Medical', extracted)
        self.assertIn('Certificate', extracted)

    def test_daily_report_for_empty_day_still_creates_a4_pdf(self):
        target = Path(tempfile.mkdtemp(dir=testenv.ROOT)) / 'empty-day.pdf'
        with mock.patch.object(core, '_font_setup', return_value='Helvetica'), \
             mock.patch.object(core, '_arabic_visual', side_effect=lambda value: 'X' * len(str(value))):
            out = core.create_daily_report_pdf(target, core.date(2020, 1, 1), self.admin)
        self.assertTrue(out.exists())
        from pypdf import PdfReader
        reader = PdfReader(str(out))
        self.assertEqual(len(reader.pages), 1)
        box = reader.pages[0].mediabox
        self.assertGreater(float(box.width), float(box.height))


if __name__ == '__main__':
    unittest.main()
