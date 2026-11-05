# -*- coding: utf-8 -*-
"""Validate release metadata and ensure repository contents are release-safe."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = "4.9.0"


def main() -> int:
    errors: list[str] = []
    core = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")
    setup = (ROOT / "SchoolPass_Setup.iss").read_text(encoding="utf-8")
    version_file = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
    app_version = re.search(r'^APP_VERSION\s*=\s*[\"\']([^\"\']+)', core, re.M)
    setup_version = re.search(r'^#define\s+MyAppVersion\s+[\"\']([^\"\']+)', setup, re.M)
    if not app_version:
        errors.append("APP_VERSION is missing from schoolpass_core.py")
    elif app_version.group(1) != EXPECTED:
        errors.append(f"APP_VERSION mismatch: {app_version.group(1)}")
    if version_file != EXPECTED:
        errors.append(f"VERSION.txt mismatch: {version_file}")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8") if (ROOT / "requirements.txt").exists() else ""
    if "ttkbootstrap==2.2.6" not in requirements:
        errors.append("requirements.txt must pin the reviewed ttkbootstrap 2.2.6 release")
    build_script = (ROOT / "build_windows.ps1").read_text(encoding="utf-8-sig") if (ROOT / "build_windows.ps1").exists() else ""
    workflow = (ROOT / ".github/workflows/release-windows.yml").read_text(encoding="utf-8") if (ROOT / ".github/workflows/release-windows.yml").exists() else ""
    if "$PythonVersion = '3.14.10'" not in build_script:
        errors.append("Windows fallback Python must be pinned to the available 3.14.10 installer")
    if "python-version: '3.14'" not in workflow:
        errors.append("GitHub Actions must build with Python 3.14 to match the supported Windows toolchain")
    if "pyinstaller==6.22.3" not in (ROOT / "requirements_dev.txt").read_text(encoding="utf-8"):
        errors.append("requirements_dev.txt must pin the reviewed PyInstaller 6.22.3 release")
    schema = re.search(r"^SCHEMA_VERSION\s*=\s*[\"\']?(\d+)[\"\']?", core, re.M)
    if not schema or schema.group(1) != "9":
        errors.append("SchoolPass 4.9.0 must preserve database schema version 9; this visual release does not migrate user data")
    if not setup_version:
        errors.append("MyAppVersion is missing from SchoolPass_Setup.iss")
    elif setup_version.group(1) != EXPECTED:
        errors.append(f"Installer version mismatch: {setup_version.group(1)}")

    required = [
        "app.py", "schoolpass_core.py", "requirements.txt", "requirements_dev.txt",
        "SchoolPass_Setup.iss", "build_windows.ps1", "build_installer_only.ps1",
        "release_preflight.py", ".github/workflows/release-windows.yml", "app.ico",
    ]
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f"Required release file is missing: {name}")

    # Windows PowerShell 5.1 misreads UTF-8 Arabic source without a BOM.
    # Validate both scripts before GitHub Actions reaches the child process.
    for name in ("build_windows.ps1", "build_installer_only.ps1"):
        path = ROOT / name
        if path.is_file() and not path.read_bytes().startswith(b"\xef\xbb\xbf"):
            errors.append(f"{name} must be UTF-8 with BOM for Windows PowerShell 5.1")

    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue  # Git metadata is expected in a GitHub Actions checkout.
        if path.is_file() and path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".bak", ".backup"}:
            errors.append(f"Potential private user data must not be committed: {path.relative_to(ROOT)}")
        if path.is_dir() and re.fullmatch(r"SchoolPass_2\.[0-9].*", path.name):
            errors.append(f"Older/versioned project copy must not be nested in source repository: {path.relative_to(ROOT)}")
    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
    for pattern in ("__pycache__/", ".pytest_cache/", "build/", "dist/"):
        if pattern not in ignore_text:
            errors.append(f".gitignore does not exclude {pattern}")

    if "dist/SchoolPass_Setup.exe" not in workflow:
        errors.append("GitHub release workflow does not publish SchoolPass_Setup.exe")
    if "schoolpass_setup.exe" not in (ROOT / "app.py").read_text(encoding="utf-8").lower():
        errors.append("In-app update checker does not verify the setup asset")
    if "--icon" not in (ROOT / "build_windows.ps1").read_text(encoding="utf-8"):
        errors.append("Windows build does not embed app.ico in SchoolPass.exe")
    if "--add-data" not in (ROOT / "build_windows.ps1").read_text(encoding="utf-8"):
        errors.append("Windows build does not package the runtime app.ico asset")
    core_source = (ROOT / "schoolpass_core.py").read_text(encoding="utf-8")
    if "def _draw_admin_signature_box(" not in core_source:
        errors.append("The enlarged administration signature component is missing")
    for removed_label in ("ختم المؤسسة", "إمضاء المسؤول", "توقيع رقمي"):
        if removed_label in core_source:
            errors.append(f"Removed receipt label reappeared: {removed_label}")
    if core_source.count("_draw_admin_signature_box(") < 3:
        errors.append("Both A6 receipt renderers must use the shared administration signature component")
    for marker, description in (
        ("def student_monthly_attention(", "monthly repeated-absence and lateness attention"),
        ("def add_student_note(", "append-only administrative notes"),
        ("def mark_exit_executed(", "physical exit confirmation at the gate"),
        ("def get_gatekeeper_log(", "gatekeeper execution ledger"),
        ("def create_annual_report_pdf(", "annual PDF archive"),
        ('mode: str = "auto"', "explicit first-print and reprint modes"),
    ):
        if marker not in core_source:
            errors.append(f"SchoolPass required core feature missing: {description}")


    app_source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    test_ui_source = (ROOT / "test_ui_smoke.py").read_text(encoding="utf-8-sig")
    for marker, description in (
        ("def test_rounded_entry_layout_replacement_keeps_text_elements(self):", "rounded field rendering layout regression test"),
        ("def test_student_search_labels_show_only_full_name_and_class(self):", "student search display regression test"),
        ("def test_arabic_rtl_rendering_covers_titles_labels_text_canvas_and_entries(self):", "Arabic rendering coverage for windows and text controls"),
        ("def test_arabic_tree_headings_are_shaped_and_right_aligned(self):", "Arabic right-aligned grid regression test"),
        ("def test_mixed_arabic_latin_digits_and_multiline_text_remain_idempotent(self):", "mixed Arabic/Latin/date rendering regression test"),
    ):
        if marker not in test_ui_source:
            errors.append(f"UI regression test missing: {description}")

    modern_ui_markers = (
        ("def _install_rounded_button_surfaces(", "rounded state-aware button renderer"),
        ("El('Button.label', side='left', expand=True)", "native button caption preserved above rounded image surfaces"),
        ("KPIAccentBlue.TFrame", "color-coded dashboard KPI cards"),
        ("MetricCardIconHover.TLabel", "interactive KPI hover feedback"),
        ("DialogHeader.TFrame", "modern permit dialog header"),
        ("FormSection.TFrame", "rounded form sections"),
        ("DialogFooter.TFrame", "consistent dialog footer"),
        ("NavGroupOpsOpen.TButton", "expanded navigation state"),
        ("NavActive.TButton", "active entry permits navigation state"),
        ("root.option_add('*TCombobox*Listbox.background', palette['field'])", "theme-aware dropdown list surfaces"),
    )
    for marker, description in modern_ui_markers:
        if marker not in app_source:
            errors.append(f"SchoolPass 4.9.0 visual feature missing: {description}")

    polish_test = (ROOT / "test_ui_polish_regressions.py").read_text(encoding="utf-8-sig")
    for marker, description in (
        ("def test_buttons_have_rounded_stateful_image_layout_without_hiding_captions():", "rounded button/caption rendering regression test"),
        ("def test_dashboard_uses_compact_hierarchy_and_color_coded_kpis():", "modern dashboard hierarchy regression test"),
        ("def test_entry_permits_dropdown_uses_clean_arabic_and_open_state():", "Arabic entry permits dropdown regression test"),
        ("def test_permit_form_has_rounded_section_cards_and_a_hierarchical_header():", "permit form visual hierarchy regression test"),
        ("def test_semantic_buttons_keep_contrast_in_dark_mode():", "dark-mode success/danger button contrast regression test"),
        ("def test_student_notes_and_admin_history_share_the_live_theme():", "theme-aware pupil history regression test"),
        ("def test_statistics_chart_uses_theme_aware_surface_and_grid():", "theme-aware statistics chart regression test"),
    ):
        if marker not in polish_test:
            errors.append(f"SchoolPass 4.9.0 visual test missing: {description}")

    if "requested_w * 0.90" in app_source or "requested_h * 0.88" in app_source:
        errors.append("Dialog geometry must not be arbitrarily scaled down; this clips buttons on school screens.")

    if "event_generate('<Down>')" in app_source:
        errors.append("Combobox Down-key injection may interrupt continuous typing")

    for marker, description in (
        ("def _rounded_photo_image(", "rounded 9-slice field and card rendering"),
        ("def _replace_ttk_layout_element(", "safe ttk rounded-element layout replacement"),
        ("def _install_rounded_widget_surfaces(", "global rounded input and surface theme"),
        ("highlightthickness=2", "visible outline for multiline text fields"),
        ("class LoginDialog:", "redesigned Arabic login dialog"),
        ("class SetupDialog:", "redesigned first-run setup dialog"),
        ("class RecoveryDialog:", "redesigned recovery dialog"),
        ("LoginBrand.TFrame", "branded login identity panel"),
        ("MetricCard.TFrame", "redesigned dashboard metric cards"),
        ("SidebarProfile.TFrame", "current-user identity panel in navigation"),
        ("PageHeader.TFrame", "professional dashboard header"),
        ("UI_PALETTE", "centralized light/dark UI palette"),
        ("primary_text", "contrast-aware primary button text"),
        ("Hero.TFrame", "premium welcome hero surface"),
        ("HeroTitle.TLabel", "hero typography hierarchy"),
        ("MetricIcon.TLabel", "icon-led KPI card layout"),
        ("def _tick_dashboard_clock(self):", "live dashboard date/time"),
        ("hero_open_button", "direct open-exit follow-up action"),
        ("def show_shortcuts(self):", "keyboard shortcuts help window"),
        ("self.root.bind_all('<F1>'", "F1 keyboard help binding"),
        ("self.shortcuts_button", "visible keyboard shortcut access"),
        ("def manage_teachers(self):", "teacher directory management"),
        ("def _refresh_student_suggestions(self, event=None):", "focus-preserving live student autocomplete"),
        ("def _refresh_teacher_suggestions(self, event=None):", "focus-preserving live teacher autocomplete"),
        ("class SuggestionPopup:", "independent autocomplete results popup"),
        ("def _center_toplevel_window(win):", "centered popup and dialog placement"),
        ("def nav_group(key, label, frame_style, title_style):", "collapsible colored grouped sidebar navigation"),
        ("NavGroupOps.TButton", "large operations group heading"),
        ("NavGroupData.TButton", "large data group heading"),
        ("NavGroupReports.TButton", "large reports group heading"),
        ("NavGroupAdmin.TButton", "large system group heading"),
        ("sidebar_open_groups", "persistent saved sidebar preferences"),
        ("def sidebar_preferences(self):", "user-selectable default open sidebar groups"),
        ("self._sidebar_group_setters", "live sidebar preference application"),
        ("SidebarGroupOps.TFrame", "distinct operations sidebar group"),
        ("SidebarGroupData.TFrame", "distinct data sidebar group"),
        ("SidebarGroupReports.TFrame", "distinct reports sidebar group"),
        ("①  تنزيل نموذج Excel للأساتذة", "visible teacher Excel template action"),
        ("②  استيراد ملف Excel ومراجعته", "visible teacher Excel import action"),
        ("def download_template():", "downloadable teacher Excel template"),
        ("إدارة الأساتذة واستيراد القائمة", "visible teacher import entry point"),
        ("before=exit_button", "correctly positioned entry submenu"),
        ("NavHover.TButton", "mouse-over navigation glow"),
        ("_install_rtl_window_titles", "consistent RTL window titles"),
        ("_schoolpass_refresh_textvariable", "live RTL display text variables"),
        ("kwargs.setdefault('justify', 'right')", "right-aligned Arabic labels and actions"),
        ("kwargs.setdefault('anchor', 'e')", "right-anchored Arabic labels and actions"),
        ("style.configure('Treeview.Heading'", "consistent data-grid heading style"),
        ("سجل التلاميذ المطرودين", "dedicated class-expulsion log navigation"),
        ("اختيار الحساب", "account selector on login"),
    ):
        if marker not in app_source:
            errors.append(f"SchoolPass 4.7.7 visual design marker missing: {description}")
    for marker, description in (
        ("def _schoolpass_map_values", "dynamic Arabic combobox values"),
        ("def _schoolpass_save_logical_values", "logical Treeview value preservation"),
        ("original_text_insert", "centralized RTL Text insertion"),
        ("original_canvas_create_text", "RTL canvas labels"),
        ("def column(self, column, option=None, **kwargs):", "right-aligned Treeview data columns"),
        ("tag_configure('_schoolpass_rtl', justify='right')", "right-aligned statistics text blocks"),
        ("self.chart_period_var = ComboStringVar", "logical dashboard period selection"),
    ):
        if marker not in app_source:
            errors.append(f"Arabic UI regression guard missing: {description}")

    if "held_tokens: list[tuple[str, str]]" not in core or "ltr_numeric = re.compile(" not in core:
        errors.append("Arabic RTL renderer must preserve dates, times and reference numbers as LTR tokens")
    if "CREATE TABLE IF NOT EXISTS teachers" not in core:
        errors.append("Teacher directory table migration is missing")
    for marker, description in (
        ("CREATE TABLE IF NOT EXISTS guardians", "encrypted guardian directory migration"),
        ("CREATE TABLE IF NOT EXISTS student_guardians", "student-to-guardian relationship migration"),
        ("def create_guardian_for_student(", "guardian registration and linking logic"),
        ("def list_student_guardians(", "guardian auto-selection query"),
        ("idx_student_guardians_one_primary", "single primary guardian constraint"),
    ):
        if marker not in core:
            errors.append(f"SchoolPass 4.7.7 guardian feature missing: {description}")
    for marker, description in (
        ("def manage_guardians(self):", "guardian management directory"),
        ("def _refresh_guardian_choices(self, preferred_id=None):", "guardian selection and auto-fill"),
        ("def add_guardian_dialog(self):", "guardian registration from permit form"),
        ("إدارة الأولياء", "guardian management navigation"),
        ("تعيين الولي المختار كأساسي", "primary guardian selection"),
    ):
        if marker not in app_source:
            errors.append(f"SchoolPass 4.7.7 guardian UI missing: {description}")
    if "expelling_teacher_id" not in core:
        errors.append("Expulsion records do not preserve a stable teacher reference")
    if "def preview_teacher_import(" not in core or "def import_teachers_as(" not in core:
        errors.append("Safe teacher Excel preview/import functions are missing")
    if "def search_students(" not in core:
        errors.append("Student autocomplete query is missing")
    if (
        "def draw_student_name(value, baseline, size):" not in core
        or "c.setFont(font, 17.0)" not in core
        or "c.drawCentredString(W / 2" not in core
    ):
        errors.append("Centered bold student name and prominent entry time markers are missing")

    for marker, description in (
        ("def gatekeeper_log(self):", "gatekeeper log screen"),
        ("def archive_reports(self):", "monthly/yearly archive selector"),
        ("فتح الملف الشامل للتلميذ", "quick pupil-history access"),
        ("إعادة طباعة الرخصة", "separate reprint action"),
        ("def _update_student_attention(self):", "monthly attention alerts"),
    ):
        if marker not in app_source:
            errors.append(f"SchoolPass required UI feature missing: {description}")

    if "SetupIconFile=app.ico" not in setup:
        errors.append("Inno Setup wizard icon is not configured")
    if 'IconFilename: "{app}\\app.ico"' not in setup:
        errors.append("Installed SchoolPass shortcuts do not use the branded icon")

    if errors:
        print("Release preflight failed:")
        for error in errors:
            print(f" - {error}")
        return 2
    print(f"Release preflight passed: SchoolPass {EXPECTED}; source tree contains no local database or build cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
