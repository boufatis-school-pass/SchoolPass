# -*- coding: utf-8 -*-
"""Release gate for UTF-8 integrity and critical Arabic terminology consistency.

This is an encoding/terminology check, not a substitute for human proofreading.
"""
from __future__ import annotations
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            pass

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (ROOT / 'app.py', ROOT / 'schoolpass_core.py')
REQUIRED_TERMS = (
    'رخصة الدخول',
    'رخصة خروج استثنائي',
    'التقرير اليومي',
    'التلميذ',
    'المشرف',
    'ولي الأمر',
)
BAD_MARKERS = ('\ufffd', 'Ã', 'Ø§', 'Ù', 'â€', 'ï»¿')


def main() -> int:
    contents = {}
    failed = False
    for path in SOURCE_FILES:
        try:
            contents[path.name] = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            print(f'فشل قراءة {path.name} بترميز UTF-8: {exc}')
            failed = True
    joined = '\n'.join(contents.values())
    for marker in BAD_MARKERS:
        if marker in joined:
            print(f'تحذير ترميز محتمل: عُثر على العلامة {marker!r}.')
            failed = True
    for term in REQUIRED_TERMS:
        if term not in joined:
            print(f'مصطلح أساسي مفقود أو تغيّر: {term}')
            failed = True
    if failed:
        return 1
    print('نجاح: ملفات الواجهة والمنطق مقروءة بترميز UTF-8، والمصطلحات الإدارية الأساسية موجودة.')
    print('تنبيه: هذا الفحص آلي للترميز والمصطلحات؛ التدقيق النحوي النهائي يحتاج مراجعة بشرية.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
