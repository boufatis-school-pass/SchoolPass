# -*- coding: utf-8 -*-
"""Static regression guards for the SchoolPass 4.7.0 premium UI corrective release."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8-sig")
CORE = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")


def test_release_version_is_471():
    assert re.search(r'^APP_VERSION\s*=\s*["\']4\.7\.1["\']', CORE, re.M)
    assert (ROOT / "VERSION.txt").read_text(encoding="ascii").strip() == "4.7.1"
    setup = (ROOT / "SchoolPass_Setup.iss").read_text(encoding="utf-8-sig")
    assert '#define MyAppVersion "4.7.1"' in setup


def test_database_schema_is_additively_migrated():
    assert re.search(r'^SCHEMA_VERSION\s*=\s*["\']7["\']', CORE, re.M)


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
