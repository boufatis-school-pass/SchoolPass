# -*- coding: utf-8 -*-
"""Validate release metadata and ensure repository contents are release-safe."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = "4.6.1"


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
    if not schema or schema.group(1) != "6":
        errors.append("The 4.6.1 corrective release must preserve database schema version 6 for existing SchoolPass data compatibility")
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

    app_source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    for marker, description in (
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
    ):
        if marker not in app_source:
            errors.append(f"SchoolPass 4.6.1 visual design marker missing: {description}")
    for marker, description in (
        ("def _schoolpass_map_values", "dynamic Arabic combobox values"),
        ("def _schoolpass_save_logical_values", "logical Treeview value preservation"),
        ("original_text_insert", "centralized RTL Text insertion"),
        ("original_canvas_create_text", "RTL canvas labels"),
        ("self.chart_period_var = ComboStringVar", "logical dashboard period selection"),
    ):
        if marker not in app_source:
            errors.append(f"Arabic UI regression guard missing: {description}")

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
