# -*- coding: utf-8 -*-
"""Static regression guards for the SchoolPass 4.8.1 attention and permit release."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8-sig")
CORE = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")


def test_release_version_is_481():
    assert re.search(r'^APP_VERSION\s*=\s*["\']4\.8\.0["\']', CORE, re.M)
    assert (ROOT / "VERSION.txt").read_text(encoding="ascii").strip() == "4.8.1"
    setup = (ROOT / "SchoolPass_Setup.iss").read_text(encoding="utf-8-sig")
    assert '#define MyAppVersion "4.8.1"' in setup


def test_database_schema_is_additively_migrated():
    assert re.search(r'^SCHEMA_VERSION\s*=\s*["\']9["\']', CORE, re.M)
    assert "CREATE TABLE IF NOT EXISTS guardians" in CORE
    assert "CREATE TABLE IF NOT EXISTS student_guardians" in CORE
    assert "guardians" in CORE[CORE.index("_REQUIRED_COLUMNS"):CORE.index("def _migrate_staged_database")]


def test_branded_login_and_first_run_dialogs_exist():
    for marker in ("class LoginDialog:", "class SetupDialog:", "class RecoveryDialog:",
                   "LoginBrand.TFrame", "LoginTitle.TLabel", "FieldLabel.TLabel",
                   "نسيت كلمة مرور المدير؟"):
        assert marker in APP


def test_premium_dashboard_hero_and_kpis_exist():
    for marker in ("Hero.TFrame", "HeroTitle.TLabel", "HeroBadge.TFrame",
                   "MetricIcon.TLabel", "MetricHint.TLabel", "hero_open_button",
                   "def _tick_dashboard_clock(self):", "self._dashboard_clock_text()",
                   "def show_shortcuts(self):", "self.root.bind_all('<F1>'"):
        assert marker in APP


def test_shared_visual_system_covers_main_controls():
    for marker in ("def make_style(", "PageHeader.TFrame", "MetricCard.TFrame",
                   "SidebarProfile.TFrame", "Treeview.Heading", "Accent.TButton",
                   "Status.TLabel", "UI_PALETTE"):
        assert marker in APP


def test_theme_switch_keeps_saved_data_layer_separate():
    assert "def toggle_theme(self):" in APP
    assert "SCHOOLPASS_DATA_DIR" in CORE
    assert 'Path(local) / APP_NAME' in CORE


def test_a6_receipts_use_large_administration_signature_without_stamp_or_digital_claims():
    assert "def _draw_admin_signature_box(" in CORE
    assert "width: float = 48.0 * MM, height: float = 32.0 * MM" in CORE
    assert "ختم الإدارة" in CORE
    assert "ختم المؤسسة" not in CORE
    assert "إمضاء المسؤول" not in CORE
    assert "توقيع رقمي" not in CORE


def test_sidebar_groups_are_large_colored_and_foldable_with_saved_preferences():
    for marker in (
        "NavGroupOps.TButton", "NavGroupData.TButton", "NavGroupReports.TButton",
        "NavGroupAdmin.TButton", "font=('Segoe UI', 13, 'bold')",
        "sidebar_open_groups", "def sidebar_preferences(self):",
        "self._sidebar_group_setters", "_saved_groups", "if key in _saved_groups:",
        "تخصيص القائمة الجانبية",
    ):
        assert marker in APP


def test_dashboard_hides_detailed_charts_until_requested():
    assert "chart_panel.pack_forget()" in APP
    assert "عرض الإحصائيات والرسوم" in APP
    assert "overview.pack_configure(before=cards)" in APP


def test_guardian_directory_and_permit_autofill_are_present():
    for marker in (
        "def create_guardian_for_student(",
        "def list_student_guardians(",
        "def set_primary_student_guardian(",
        "def list_guardians_as_admin(",
        "def update_guardian_as_admin(",
        "national_id_enc",
        "national_id_hash",
    ):
        assert marker in CORE
    for marker in (
        "def manage_guardians(self):",
        "def _refresh_guardian_choices(self, preferred_id=None):",
        "def _guardian_selected(self, _event=None):",
        "def add_guardian_dialog(self):",
        "إدارة الأولياء",
        "تعيين الولي المختار كأساسي",
    ):
        assert marker in APP


def test_arabic_rtl_adapters_cover_window_titles_widgets_and_right_aligned_reports():
    for marker in (
        "_install_rtl_window_titles",
        "kwargs.setdefault('justify', 'right')",
        "kwargs.setdefault('anchor', 'e')",
        "def column(self, column, option=None, **kwargs):",
        "tag_configure('_schoolpass_rtl', justify='right')",
    ):
        assert marker in APP
    assert 'base_dir="R"' in CORE
    assert "held_tokens: list[tuple[str, str]]" in CORE
    assert "ltr_numeric = re.compile(" in CORE
    ui_tests = (ROOT / "test_ui_smoke.py").read_text(encoding="utf-8-sig")
    for marker in (
        "def test_arabic_rtl_rendering_covers_titles_labels_text_canvas_and_entries(self):",
        "def test_arabic_tree_headings_are_shaped_and_right_aligned(self):",
        "def test_mixed_arabic_latin_digits_and_multiline_text_remain_idempotent(self):",
    ):
        assert marker in ui_tests
