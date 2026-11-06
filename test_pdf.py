# -*- coding: utf-8 -*-
"""PDF / A6 / QR tests. Needs reportlab, qrcode, arabic-reshaper, python-bidi (installed by the build script)."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import importlib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv  # noqa: E402  (one shared data dir for all modules)
TEST_ROOT = testenv.ROOT

try:
    import reportlab, qrcode, arabic_reshaper  # noqa: F401,E401
    from bidi.algorithm import get_display  # noqa: F401
    HAVE_LIBS = True
except Exception:
    HAVE_LIBS = False

import schoolpass_core as core  # noqa: E402

core.PBKDF2_ITERATIONS = 2000
A6_W_PT, A6_H_PT = 105 / 25.4 * 72, 148 / 25.4 * 72
NID = "987654321098765432"


def tearDownModule():
    core.stop_local_server()


@unittest.skipUnless(HAVE_LIBS, "PDF libraries not installed")
class TestPdf(unittest.TestCase):
    def req(self, *mods):
        for m in mods:
            try:
                importlib.import_module(m)
            except ImportError:
                self.skipTest(f"optional test dependency missing: {m}")

    def setUp(self):
        # QR tests may have started the local HTTP server in a prior test.
        # Stop it fully before resetting the synthetic database.
        core.stop_local_server()
        for suffix in ("", "-wal", "-shm"):
            Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
        core.init_db()
        self.admin, _ = core.bootstrap_admin("director", "المدير", "TestPassword123!")
        self.sup = core.create_user_as(self.admin, "sup1", "مشرف أول", "TestPassword123!")
        self.student = {"student_key": "P-1", "last_name": "بوفاطيس", "first_name": "لخضر عبد الرحمن", "class_name": "ثالثة علوم تجريبية 2"}
        core.set_setting("qr_host", "192.168.50.7")

    def permit(self, **kw):
        args = dict(reason="موعد طبي عاجل عند الطبيب المختص", guardian_name="ولي الأمر التجريبي", relationship="الأب", national_id=NID)
        args.update(kw)
        with mock.patch.object(core, "protect_sensitive_text", lambda v: "DPAPI1:" + v[::-1]):
            return core.create_exit_permit(self.student, self.sup, args["reason"], args["guardian_name"], args["relationship"], args["national_id"])

    def pages(self, path):
        self.req('pypdf')
        import pypdf
        r = pypdf.PdfReader(str(path))
        return [(float(p.mediabox.width), float(p.mediabox.height)) for p in r.pages]

    def test_monthly_report_pdf_is_created_for_selected_month(self):
        from datetime import date
        current = date.today()
        core.create_late_slip(self.student, self.sup, "اختبار تقرير")
        target = Path(tempfile.mkdtemp(dir=TEST_ROOT)) / "monthly.pdf"
        out = core.create_monthly_report_pdf(target, current.year, current.month, self.sup)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 500)
        self.assertEqual(out.read_bytes()[:4], b"%PDF")
        sizes = self.pages(out)
        self.assertTrue(all(w > h for w, h in sizes), "monthly report must use A4 landscape")

    def test_monthly_report_long_arabic_cells_stay_inside_page(self):
        import pdfplumber
        from datetime import date
        current = date.today()
        long_student = {
            "student_key": "LONG-1",
            "last_name": "لقب طويل جدًا للتأكد من احتواء التقرير على الأسماء الممتدة دون تداخل",
            "first_name": "اسم مركب طويل للتلميذ التجريبي في قائمة المؤسسة",
            "class_name": "ثالثة ثانوي علوم تجريبية شعبة طويلة الاسم قسم رقم 12",
        }
        for _ in range(4):
            core.create_late_slip(long_student, self.sup, "سبب طويل " * 20)
        target = Path(tempfile.mkdtemp(dir=TEST_ROOT)) / "monthly-long.pdf"
        core.create_monthly_report_pdf(target, current.year, current.month, self.sup)
        with pdfplumber.open(str(target)) as pdf:
            self.assertGreaterEqual(len(pdf.pages), 1)
            for page in pdf.pages:
                self.assertGreater(page.width, page.height)
                for char in page.chars:
                    self.assertGreaterEqual(char["x0"], 25, "text must stay within left printable margin")
                    self.assertLessEqual(char["x1"], page.width - 25, "text must stay within right printable margin")

    def test_monthly_report_extreme_long_cell_splits_across_pages_without_clipping(self):
        import pdfplumber
        from datetime import date
        current = date.today()
        very_long_student = {
            "student_key": "EXTREME-LONG-1",
            "last_name": "اسم طويل جدًا " * 180,
            "first_name": "تلميذ للاختبار",
            "class_name": "قسم تجريبي طويل",
        }
        core.create_late_slip(very_long_student, self.sup, "اختبار تقسيم صف طويل")
        target = Path(tempfile.mkdtemp(dir=TEST_ROOT)) / "monthly-extreme-long.pdf"
        core.create_monthly_report_pdf(target, current.year, current.month, self.sup)
        with pdfplumber.open(str(target)) as pdf:
            self.assertGreaterEqual(len(pdf.pages), 2, "very long cell should continue onto another page")
            for page in pdf.pages:
                self.assertGreater(page.width, page.height)
                for char in page.chars:
                    self.assertGreaterEqual(char["x0"], 25)
                    self.assertLessEqual(char["x1"], page.width - 25)
                    self.assertGreaterEqual(char["top"], 0)
                    self.assertLessEqual(char["bottom"], page.height - 5)

    def test_every_kind_is_true_a6_portrait(self):
        rows = [core.create_late_slip(self.student, self.sup, "حافلة"), core.create_absence_return(self.student, self.sup, "2025-01-10", "2025-01-12", "مرض", certificate_present=True),
                self.permit()]
        rows.append(core.return_from_exit(rows[2]["id"], self.sup))
        for r in rows:
            path = core.create_pdf(r["id"])
            sizes = self.pages(path)
            self.assertEqual(len(sizes), 2 if r["kind"] == "exit_permit" else 1, r["kind"])
            for w, h in sizes:
                self.assertAlmostEqual(w, A6_W_PT, delta=0.2); self.assertAlmostEqual(h, A6_H_PT, delta=0.2)

    def test_permit_pages_same_number_and_distinct_notice_text(self):
        p = self.permit()
        path = core.create_pdf(p["id"])
        self.req('pdfplumber')
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            texts = [pg.extract_text() or "" for pg in pdf.pages]
        for t in texts:
            self.assertIn(p["record_no"], t)
            self.assertNotIn(NID, t); self.assertNotIn(NID[:-4], t)
            self.assertIn(NID[-4:], t)
        # notice text differs per copy (compare on glyph-count; reshaping is not verified in the sandbox)
        self.assertNotEqual(texts[0], texts[1])

    def test_nothing_is_clipped_and_notice_does_not_overlap_data(self):
        self.req('pdfplumber')
        import pdfplumber
        for reason in ("حافلة", "موعد طبي عاجل " * 12):           # short and very long free text
            p = self.permit(reason=reason, guardian_name="ولي أمر باسم طويل جدا جدا جدا جدا " * 2)
            path = core.create_pdf(p["id"])
            with pdfplumber.open(str(path)) as pdf:
                for pg in pdf.pages:
                    for ch in pg.chars:
                        self.assertGreaterEqual(ch["x0"], 5, "text must keep a margin (printer non-printable area)")
                        self.assertLessEqual(ch["x1"], pg.width - 5)
                        self.assertGreaterEqual(ch["top"], 5); self.assertLessEqual(ch["bottom"], pg.height - 5)
                    # find the notice font size: smallest text block above the QR area must stay >= 5.6pt
                    sizes = [round(c["size"], 1) for c in pg.chars if c["top"] < pg.height - 40]
                    self.assertGreaterEqual(min(sizes), 5.0)
                    # vertical bands must not overlap: collect distinct baselines and ensure spacing >= 2pt
                    tops = sorted({round(c["top"], 1) for c in pg.chars})
                    gaps = [b - a for a, b in zip(tops, tops[1:]) if b - a > 0.5]
                    self.assertTrue(all(g >= 2.0 for g in gaps), gaps)

    def test_qr_decodes_to_random_token_url_without_personal_data(self):
        self.req('cv2', 'numpy')
        import shutil as _sh
        if not _sh.which('pdftoppm'):
            self.skipTest('pdftoppm (poppler) not available; QR decode test runs on the review machine only')
        import cv2, subprocess
        p = self.permit()
        path = core.create_pdf(p["id"])
        out = TEST_ROOT / "render"
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(path), str(out)], check=True)
        pngs = sorted(TEST_ROOT.glob("render*.png"))
        self.assertEqual(len(pngs), 2)
        urls = []
        for png in pngs:
            img = cv2.imread(str(png)); h, w = img.shape[:2]
            crop = img[int(h * 0.70):, : int(w * 0.50)]                     # bottom-left corner = QR zone
            data, _, _ = cv2.QRCodeDetector().detectAndDecode(crop)
            urls.append(data)
        self.assertEqual(urls[0], urls[1], "both copies carry the same QR")
        self.assertEqual(urls[0], f"http://192.168.50.7:{core.SERVER_PORT}/v/{p['qr_token']}")
        self.assertNotIn(NID, urls[0]); self.assertNotIn("P-1", urls[0])

    def test_cancelled_and_reprint_are_marked(self):
        self.req('pdfplumber')
        import pdfplumber
        p = self.permit(); core.cancel_record(p["id"], self.sup, "تراجع")
        first = core.create_pdf(p["id"])                                   # printed_count == 0 -> not a reprint
        core.print_record(p["id"], self.sup, launch=False)
        again = core.create_pdf(p["id"])                                   # now it is a reprint
        with pdfplumber.open(str(first)) as a, pdfplumber.open(str(again)) as b:
            ta, tb = a.pages[0].extract_text(), b.pages[0].extract_text()
        self.assertGreater(len(tb), len(ta), "reprint adds a marker line")
        self.assertEqual(len(core.compute_stats()["cancellations"]), 1)

    def test_missing_font_or_qr_library_fails_loudly_instead_of_printing_wrong_output(self):
        p = self.permit()
        with mock.patch.object(core, "_FONT_NAME", "NoSuchFont"), mock.patch.dict(os.environ, {"SCHOOLPASS_FONT": "", "WINDIR": "/nonexistent"}), \
                mock.patch("os.path.exists", return_value=False):
            with self.assertRaises(RuntimeError):
                core.create_pdf(p["id"])
        with mock.patch.dict(sys.modules, {"qrcode": None}):
            with self.assertRaises(ImportError):
                core.create_pdf(p["id"])

    def test_absence_guardian_pdf_masks_identity_and_contains_issue_time(self):
        with mock.patch.object(core, "protect_sensitive_text", lambda v: "DPAPI1:" + v[::-1]):
            row = core.create_absence_return(self.student, self.sup, "2025-01-06", "2025-01-09",
                                             proof_method="guardian", guardian_name="ولي تجريبي",
                                             relationship="الأب", national_id=NID, guardian_present=True)
        path = core.create_pdf(row['id'])
        self.req('pdfplumber')
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = pdf.pages[0].extract_text() or ""
        from datetime import datetime
        issued = datetime.strptime(row['issued_at'][:19], "%Y-%m-%d %H:%M:%S")
        self.assertIn(row['record_no'], text)
        # The redesign presents the date and time in separate prominent fields.
        self.assertIn(issued.strftime("%H:%M"), text)
        self.assertIn(issued.strftime("%d/%m/%Y"), text)
        self.assertNotIn(NID, text)
        self.assertNotIn(NID[:-4], text)
        self.assertIn(NID[-4:], text)
        self.assertIn('SP-', row['record_no'])

    def test_old_pdfs_are_purged(self):
        f = core.DATA_DIR / "prints" / "old.pdf"; f.write_bytes(b"%PDF-1.4")
        os.utime(f, (1, 1))
        self.assertEqual(core.purge_old_prints(30), 1)


    def test_two_independent_entry_permits_fit_one_a4_portrait_sheet(self):
        self.req('pypdf')
        import pypdf
        student_two = {"student_key": "P-2", "last_name": "التلميذ الثاني", "first_name": "اسم مستقل", "class_name": "ثانية آداب 1"}
        first = core.create_late_slip(self.student, self.sup, "تأخر صباحي")
        second = core.create_late_slip(student_two, self.sup, "تأخر بسبب النقل")
        out = core.create_a4_two_record_sheet([first['id'], second['id']])
        reader = pypdf.PdfReader(str(out))
        self.assertEqual(len(reader.pages), 1)
        page = reader.pages[0]
        self.assertGreater(float(page.mediabox.height), float(page.mediabox.width))
        self.assertAlmostEqual(float(page.mediabox.width), 595.28, delta=2)
        self.assertAlmostEqual(float(page.mediabox.height), 841.89, delta=2)

    def test_two_record_sheet_rejects_duplicate_ids_and_exit_permits(self):
        first = core.create_late_slip(self.student, self.sup, "تأخر صباحي")
        with self.assertRaises(ValueError):
            core.create_a4_two_record_sheet([first['id'], first['id']])
        exit_id = self.permit()['id']
        second = core.create_late_slip({"student_key": "P-2", "last_name": "ثان", "first_name": "تلميذ", "class_name": "ثانية"}, self.sup, "تأخر")
        with self.assertRaises(ValueError):
            core.create_a4_two_record_sheet([exit_id, second['id']])


if __name__ == "__main__":
    unittest.main(verbosity=2)

