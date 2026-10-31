# -*- coding: utf-8 -*-
"""Shared test environment: ONE temporary data dir for all test modules (they run in a single process)."""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="SchoolPass_Test_"))
os.environ["SCHOOLPASS_DATA_DIR"] = str(ROOT / "data")
atexit.register(lambda: shutil.rmtree(ROOT, ignore_errors=True))
