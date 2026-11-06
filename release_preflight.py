# -*- coding: utf-8 -*-
"""Validate release metadata and ensure repository contents are release-safe."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = "2.5.4"


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

    workflow = (ROOT / ".github/workflows/release-windows.yml").read_text(encoding="utf-8") if (ROOT / ".github/workflows/release-windows.yml").exists() else ""
    if "dist/SchoolPass_Setup.exe" not in workflow:
        errors.append("GitHub release workflow does not publish SchoolPass_Setup.exe")
    if "schoolpass_setup.exe" not in (ROOT / "app.py").read_text(encoding="utf-8").lower():
        errors.append("In-app update checker does not verify the setup asset")
    if "--icon" not in (ROOT / "build_windows.ps1").read_text(encoding="utf-8"):
        errors.append("Windows build does not embed app.ico in SchoolPass.exe")
    if "--add-data" not in (ROOT / "build_windows.ps1").read_text(encoding="utf-8"):
        errors.append("Windows build does not package the runtime app.ico asset")
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
