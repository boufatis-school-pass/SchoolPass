# -*- coding: utf-8 -*-
"""Strict dependency and Arabic-printing preflight used before Windows packaging."""
from __future__ import annotations

import importlib
import os
import platform
import sys
from pathlib import Path

# Windows consoles may default to cp1252/cp437 and fail on Arabic output.
# Keep diagnostics readable without allowing console encoding to abort preflight.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

REQUIRED = (
    "reportlab", "qrcode", "PIL", "arabic_reshaper", "bidi", "openpyxl", "xlrd",
    "pypdf", "pdfplumber", "PyInstaller", "ttkbootstrap", "tkinter",
)


def main() -> int:
    missing = []
    for name in REQUIRED:
        try:
            importlib.import_module(name)
        except Exception as exc:  # report the actual import error
            missing.append((name, str(exc)))
    if missing:
        print("فشل فحص المتطلبات:")
        for name, error in missing:
            print(f" - {name}: {error}")
        return 2

    if platform.system() == "Windows":
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.update_idletasks()
            root.destroy()
        except Exception as exc:
            print(f"فشل اختبار واجهة Tk/Tcl في Windows: {exc}")
            return 5

    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=2, border=2)
        qr.add_data("SchoolPass preflight")
        qr.make(fit=True)
        if qr.make_image().size[0] <= 0:
            raise RuntimeError("لم تُنشأ صورة QR سليمة.")
    except Exception as exc:
        print(f"فشل اختبار توليد QR: {exc}")
        return 6

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        sample = "اختبار تشكيل الحروف العربية"
        shaped = arabic_reshaper.reshape(sample)
        visual = get_display(shaped, base_dir="R")
        if not shaped or not visual or shaped == sample:
            raise RuntimeError("لم ينتج تشكيل عربي فعلي.")
    except Exception as exc:
        print(f"فشل فحص تشكيل العربية واتجاه النص: {exc}")
        return 3

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates = [
            os.environ.get("SCHOOLPASS_FONT", ""),
            str(Path(windir) / "Fonts" / "tahoma.ttf"),
            str(Path(windir) / "Fonts" / "arial.ttf"),
            str(Path(windir) / "Fonts" / "segoeui.ttf"),
        ]
        font_path = next((p for p in candidates if p and Path(p).is_file()), None)
        if not font_path:
            raise FileNotFoundError("لم يُعثر على Tahoma أو Arial أو Segoe UI أو SCHOOLPASS_FONT.")
        TTFont("SchoolPassPreflightFont", font_path)
    except Exception as exc:
        print(f"فشل فحص الخط العربي في Windows: {exc}")
        return 4

    if platform.system() != "Windows":
        print("تنبيه: الفحص نجح على نظام غير Windows؛ لم يتم اختبار DPAPI أو الخطوط/واجهة Windows.")
    print("نجاح: المكتبات المطلوبة وتشكيل العربية والخط متاحة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
