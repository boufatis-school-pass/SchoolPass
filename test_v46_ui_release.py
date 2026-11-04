# -*- coding: utf-8 -*-
"""Static regression guards for the SchoolPass 4.7.6 guardian and UI release."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8-sig")
CORE = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")


def test_release_version_is_476():
    assert re.search(r'^APP_VERSION\s*=\s*["\']4\.7\.6["\']', CORE, re.M)
    assert (ROOT / "VERSION.txt").read_text(encoding="ascii").strip() == "4.7.6"
    setup = (ROOT / "SchoolPass_Setup.iss").read_text(encoding="utf-8-sig")
    assert '#define MyAppVersion "4.7.6"' in setup


def test_database_schema_is_additively_migrated():
    assert re.search(r'^SCHEMA_VERSION\s*=\s*["\']8["\']', CORE, re.M)
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
    assert "width: float = 43.0 * MM, height: float = 29.0 * MM" in CORE
    assert "توقيع الإدارة" in CORE
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
