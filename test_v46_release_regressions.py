# -*- coding: utf-8 -*-
"""Focused regressions for the 4.6 receipt safeguards and 4.9.2 release metadata."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import schoolpass_core as core

ROOT = Path(__file__).resolve().parent


def test_administration_signature_box_is_substantially_larger_and_unlabelled_for_stamp():
    canvas = MagicMock()
    with patch.object(core, "_arabic_visual", side_effect=lambda value: value):
        core._draw_admin_signature_box(
            canvas, 10.0, 12.0, "Helvetica", (0.08, 0.25, 0.42), (0.55, 0.62, 0.69)
        )
    args = canvas.roundRect.call_args.args
    assert args[2] == 48.0 * core.MM
    assert args[3] == 32.0 * core.MM
    assert args[2] > 30.0 * core.MM
    assert args[3] > 22.0 * core.MM
    labels = [call.args[2] for call in canvas.drawCentredString.call_args_list]
    assert labels == ["ختم الإدارة"]


def test_receipt_renderers_use_the_same_signature_component_and_no_removed_labels():
    source = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")
    assert source.count("_draw_admin_signature_box(") >= 3  # definition + both A6 renderers
    assert "ختم المؤسسة" not in source
    assert "إمضاء المسؤول" not in source
    assert "توقيع رقمي" not in source


def test_keyboard_help_lists_only_bound_shortcuts_and_has_f1_entry_point():
    source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    assert "def show_shortcuts(self):" in source
    assert "self.root.bind_all('<F1>', lambda _e: self.show_shortcuts())" in source
    for key in ("F1", "F5", "Ctrl + 1", "Ctrl + 2", "Ctrl + 3", "Ctrl + 4",
                "Ctrl + F", "Ctrl + Alt + D", "F11", "Esc"):
        assert key in source
    assert "بحث فوري بالاسم أو الرقم المرجعي أو القسم  •  Ctrl+F" in source


def test_database_schema_and_version_metadata_are_consistent():
    core_source = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "4.9.2"' in core_source
    assert 'SCHEMA_VERSION = "9"' in core_source
    assert (ROOT / "VERSION.txt").read_text(encoding="ascii").strip() == "4.9.2"
    setup = (ROOT / "SchoolPass_Setup.iss").read_text(encoding="utf-8-sig")
    assert '#define MyAppVersion "4.9.2"' in setup


def _ascii_for_pdf_tests(value):
    """Stand in for the Arabic shaping layer while keeping report text extractable."""
    value = str(value)
    replacements = {
        "ختم الإدارة": "ADMINISTRATION STAMP",
        "ختم المؤسسة": "INSTITUTION STAMP",
        "إمضاء المسؤول": "OFFICER SIGNATURE",
    }
    if value in replacements:
        return replacements[value]
    return "".join(char if ord(char) < 128 else "X" for char in value)


def _fresh_database():
    from pathlib import Path
    import testenv
    core.stop_local_server()
    for suffix in ("", "-wal", "-shm"):
        Path(str(core.DB_PATH) + suffix).unlink(missing_ok=True)
    core.init_db()
    admin, _ = core.bootstrap_admin("director", "المدير", "TestPassword123!")
    supervisor = core.create_user_as(admin, "supervisor46", "مشرف الاختبار", "TestPassword123!")
    core.set_setting("qr_host", "127.0.0.1")
    student = {"student_key": "S-460", "last_name": "تجريبي", "first_name": "أحمد", "class_name": "أولى علوم 1"}
    return admin, supervisor, student


def test_entry_receipt_pdf_contains_only_large_admin_signature_area(tmp_path):
    from unittest.mock import patch
    from pypdf import PdfReader
    _admin, supervisor, student = _fresh_database()
    row = core.create_late_slip(student, supervisor, "تأخر صباحي")
    with patch.object(core, "_font_setup", return_value="Helvetica"), \
         patch.object(core, "_arabic_visual", side_effect=_ascii_for_pdf_tests):
        path = core.create_pdf(row["id"])
    page = PdfReader(str(path)).pages[0]
    assert abs(float(page.mediabox.width) - (105 / 25.4 * 72)) < 0.2
    assert abs(float(page.mediabox.height) - (148 / 25.4 * 72)) < 0.2
    text = page.extract_text() or ""
    assert "ADMINISTRATION STAMP" in text
    assert "INSTITUTION STAMP" not in text
    assert "OFFICER SIGNATURE" not in text
    assert "DIGITAL" not in text
    assert len(PdfReader(str(path)).pages) == 1


def test_exit_receipt_two_copies_keep_qr_workflow_and_large_admin_signature(tmp_path):
    from unittest.mock import patch
    from pypdf import PdfReader
    _admin, supervisor, student = _fresh_database()
    with patch.object(core, "protect_sensitive_text", side_effect=lambda value: "DPAPI1:" + value):
        row = core.create_exit_permit(student, supervisor, "موعد طبي", "ولي أمر تجريبي", "الأب", "123456789012")
    with patch.object(core, "_font_setup", return_value="Helvetica"), \
         patch.object(core, "_arabic_visual", side_effect=_ascii_for_pdf_tests):
        path = core.create_pdf(row["id"])
    pages = PdfReader(str(path)).pages
    assert len(pages) == 2
    texts = [page.extract_text() or "" for page in pages]
    for page, text in zip(pages, texts):
        assert abs(float(page.mediabox.width) - (105 / 25.4 * 72)) < 0.2
        assert abs(float(page.mediabox.height) - (148 / 25.4 * 72)) < 0.2
        assert "ADMINISTRATION STAMP" in text
        assert "INSTITUTION STAMP" not in text
        assert "OFFICER SIGNATURE" not in text
        assert "123456789012" not in text
        assert "9012" in text
    assert texts[0] != texts[1]


def test_arabic_bidi_preserves_numeric_dates_times_and_reference_codes():
    visual = core._arabic_visual(
        "التاريخ: 04-11-2026 — المرجع: SP-2026-001 — الوقت: 17:06"
    )
    assert "04-11-2026" in visual
    assert "SP-2026-001" in visual
    assert "17:06" in visual
