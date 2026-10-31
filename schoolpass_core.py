# -*- coding: utf-8 -*-
"""SchoolPass core logic (no GUI imports): database, security, import, records, PDF, QR server, backup.

Every function that changes data takes the acting ``user_id`` and checks the role/active flag
in the database itself, so permissions do not depend on which buttons the GUI shows.
"""
from __future__ import annotations

import base64
from collections import Counter
import csv
import hashlib
import hmac
import html
import io
import json
import os
import queue
import re
import secrets
import socket
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

APP_NAME = "SchoolPass"
APP_VERSION = "2.5.1"
SCHOOL_NAME = "ثانوية المجاهد بسجراري لخضر بوفاطيس"
MINISTRY_NAME = "الجمهورية الجزائرية الديمقراطية الشعبية — وزارة التربية الوطنية"
SERVER_PORT = 8765
AUTO_LOCK_SECONDS = 600
RECORD_KINDS = ("late", "absence_return", "exit_permit", "exit_return")
ABSENCE_CERTIFICATE_MAX_DURATION_DAYS = 2
# Backward-compatible alias for integrations that referenced the earlier constant name.
ABSENCE_CERTIFICATE_MAX_DATE_DIFFERENCE = ABSENCE_CERTIFICATE_MAX_DURATION_DAYS


class PermissionDenied(Exception):
    """Raised when the acting user lacks the role needed for an operation."""


# ----------------------------------------------------------------------------- paths
def app_data_dir() -> Path:
    override = os.environ.get("SCHOOLPASS_DATA_DIR")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    for sub in ("prints", "backups", "logs"):
        (base / sub).mkdir(exist_ok=True)
    return base


DATA_DIR = app_data_dir()
DB_PATH = DATA_DIR / "schoolpass.sqlite3"
LOG_PATH = DATA_DIR / "logs" / "schoolpass.log"
EVENT_QUEUE: "queue.Queue[str]" = queue.Queue(maxsize=200)
_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None


def now_iso() -> str:
    """Local workstation timestamp, stored to whole-second precision for printed receipts."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def date_today() -> str:
    return date.today().strftime("%Y-%m-%d")


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_digits(text: str) -> str:
    return str(text or "").translate(_AR_DIGITS)


def get_local_ip() -> str:
    """Resolve the local interface address without requiring Internet access."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))  # UDP connect sends no data
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


# ----------------------------------------------------------------------------- passwords
PBKDF2_ITERATIONS = 310_000  # OWASP-recommended order of magnitude for PBKDF2-HMAC-SHA256


def password_digest(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        _, actual = password_digest(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(actual, digest_hex)
    except Exception:
        return False


_WEAK = {"12345678", "123456789", "1234567890", "password", "password1", "azertyuiop", "qwertyuiop",
         "00000000", "11111111", "motdepasse", "schoolpass", "admin1234", "abcd1234", "aaaaaaaa"}


def validate_password(password: str, username: str = "") -> None:
    if len(password) < 8:
        raise ValueError("كلمة المرور يجب ألا تقل عن 8 أحرف.")
    low = password.lower()
    if low in _WEAK or (username and low == username.strip().lower()) or len(set(password)) < 4:
        raise ValueError("كلمة المرور سهلة التخمين. اختر كلمة أطول وأقل شيوعًا.")


# ----------------------------------------------------------------------------- DPAPI
def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("حماية DPAPI متاحة في إصدار Windows فقط، ولا تعمل على هذا النظام.")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out = DATA_BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                                             wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        ok = crypt32.CryptProtectData(ctypes.byref(source), "SchoolPass", None, None, None, 0x1, ctypes.byref(out))
    else:
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
                                               ctypes.POINTER(DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
                                               wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("فشلت عملية التشفير/فك التشفير بواسطة Windows DPAPI (قد يكون السجل أُنشئ بحساب Windows آخر).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_sensitive_text(value: str) -> str:
    """Encrypt with Windows DPAPI (bound to the current Windows user profile)."""
    return "DPAPI1:" + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")


def reveal_sensitive_text(value: str) -> str:
    if not value.startswith("DPAPI1:"):
        return value
    return _dpapi(base64.b64decode(value.split(":", 1)[1]), False).decode("utf-8")


# ----------------------------------------------------------------------------- database
class _ClosingConnection(sqlite3.Connection):
    """`with db_connect() as conn` commits/rolls back AND closes (stdlib only commits)."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA_VERSION = "6"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','supervisor')),
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    failed_count INTEGER NOT NULL DEFAULT 0,
    lock_until REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_key TEXT NOT NULL UNIQUE,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    year TEXT DEFAULT '',
    branch TEXT DEFAULT '',
    section TEXT DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_no TEXT NOT NULL UNIQUE,
    qr_token TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    student_key TEXT NOT NULL,
    student_name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    issued_at TEXT NOT NULL DEFAULT '',
    user_id INTEGER,
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'valid',
    details_json TEXT NOT NULL DEFAULT '{}',
    related_record_id INTEGER,
    cancelled_reason TEXT DEFAULT '',
    cancelled_at TEXT DEFAULT '',
    cancelled_by INTEGER,
    returned_at TEXT DEFAULT '',
    returned_by INTEGER,
    printed_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(related_record_id) REFERENCES records(id),
    FOREIGN KEY(cancelled_by) REFERENCES users(id),
    FOREIGN KEY(returned_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    happened_at TEXT NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_student ON records(student_key);
CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at);

-- Append-only protection enforced by the database itself (not only by the GUI).
CREATE TRIGGER IF NOT EXISTS trg_records_no_delete BEFORE DELETE ON records
BEGIN SELECT RAISE(ABORT, 'records cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS trg_records_immutable
BEFORE UPDATE OF record_no, qr_token, kind, student_key, student_name, class_name, created_at, user_id,
                 reason, details_json, related_record_id ON records
BEGIN SELECT RAISE(ABORT, 'record content is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_records_status_final BEFORE UPDATE OF status ON records
WHEN OLD.status <> 'valid' AND NEW.status <> OLD.status
BEGIN SELECT RAISE(ABORT, 'closed record status cannot change'); END;
CREATE TRIGGER IF NOT EXISTS trg_records_status_values BEFORE UPDATE OF status ON records
WHEN NEW.status NOT IN ('valid','cancelled','returned')
BEGIN SELECT RAISE(ABORT, 'invalid status'); END;
CREATE TRIGGER IF NOT EXISTS trg_records_cancel_needs_reason BEFORE UPDATE OF status ON records
WHEN NEW.status = 'cancelled' AND TRIM(COALESCE(NEW.cancelled_reason, '')) = ''
BEGIN SELECT RAISE(ABORT, 'cancellation requires a reason'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def _migrate_absence_day_counts(conn: sqlite3.Connection, prior_version: str) -> int:
    """Migrate historical records so the return date is excluded from absence days."""
    try:
        old_version = int(prior_version or 0)
    except (TypeError, ValueError):
        old_version = 0
    if old_version >= 6:
        return 0
    migrated = 0
    rows = conn.execute("SELECT id,details_json FROM records WHERE kind='absence_return'").fetchall()
    for item in rows:
        try:
            details = json.loads(item["details_json"] or "{}")
            start_s, return_s = details.get("absence_start"), details.get("return_date")
            if not start_s or not return_s:
                continue
            correct_days = (date.fromisoformat(return_s) - date.fromisoformat(start_s)).days
            if correct_days < 0:
                continue
            if int(details.get("days_count", -1)) != correct_days or int(details.get("date_difference_days", -1)) != correct_days:
                details["days_count"] = correct_days
                details["date_difference_days"] = correct_days
                conn.execute("UPDATE records SET details_json=? WHERE id=?", (json.dumps(details, ensure_ascii=False), item["id"]))
                migrated += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            # Preserve malformed legacy data rather than guessing or erasing it.
            continue
    if migrated:
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), None, "ترحيل حساب أيام الغياب إلى قاعدة استبعاد يوم العودة", "database", "schema-v6",
                      f"تم تصحيح المدة المحسوبة في {migrated} سجلًا دون تغيير تواريخ البداية والعودة."))
    return migrated


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(_SCHEMA)
        # Explicit issue timestamp; older records retain their historical created_at value.
        columns = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        if "issued_at" not in columns:
            conn.execute("ALTER TABLE records ADD COLUMN issued_at TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE records SET issued_at=created_at WHERE TRIM(COALESCE(issued_at,''))=''")
        # Rebuild the immutable trigger so issue timestamp cannot be altered independently.
        # Schema v6 corrects historical absence day counts: the return date is not
        # an absence day. Only this derived value is migrated; record identity,
        # student data, reasons and audit history remain untouched.
        prior_version_row = conn.execute("SELECT setting_value FROM settings WHERE setting_key='schema_version'").fetchone()
        prior_version = str(prior_version_row[0]) if prior_version_row else "0"
        conn.execute("DROP TRIGGER IF EXISTS trg_records_immutable")
        _migrate_absence_day_counts(conn, prior_version)
        conn.execute("""CREATE TRIGGER trg_records_immutable
            BEFORE UPDATE OF record_no, qr_token, kind, student_key, student_name, class_name, created_at, issued_at, user_id,
                             reason, details_json, related_record_id ON records
            BEGIN SELECT RAISE(ABORT, 'record content is immutable'); END""")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('school_name',?)", (SCHOOL_NAME,))
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('secret',?)", (secrets.token_hex(32),))
        # Appearance defaults are added with INSERT OR IGNORE so existing 0.2/0.3 data is preserved.
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('theme_primary','#185A8D')")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('theme_secondary','#F2F5F9')")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('late_alert_threshold','3')")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('rtl_shaping','1')")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('external_backup_path','')")
        # Older experimental silent printing could ignore A6; force safe PDF preview after upgrade.
        conn.execute("UPDATE settings SET setting_value='open' WHERE setting_key='print_mode' AND setting_value='direct'")
        conn.execute("INSERT INTO settings(setting_key,setting_value) VALUES('schema_version',?) "
                     "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (SCHEMA_VERSION,))


def check_database_integrity() -> None:
    """Fast startup integrity/foreign-key check; raise rather than silently operate on damaged data."""
    with db_connect() as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"فشل فحص سلامة قاعدة البيانات: {result}")
        foreign_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_errors:
            raise RuntimeError(f"تحتوي قاعدة البيانات على {len(foreign_errors)} مخالفة مرجعية؛ أوقف البرنامج وخذ نسخة من الملف قبل أي إصلاح.")


def setting(key: str, default: str = "") -> str:
    with db_connect() as conn:
        row = conn.execute("SELECT setting_value FROM settings WHERE setting_key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute("INSERT INTO settings(setting_key,setting_value) VALUES(?,?) "
                     "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (key, value))


def audit(user_id: int | None, action: str, target_type: str = "", target_id="", description: str = "") -> None:
    with db_connect() as conn:
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), user_id, action, target_type, str(target_id), description))


# ----------------------------------------------------------------------------- users & permissions
def _uid(user) -> int:
    return int(user) if isinstance(user, int) else int(user["id"])


def get_user(user_id) -> sqlite3.Row | None:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (_uid(user_id),)).fetchone()


def require_user(user_id) -> sqlite3.Row:
    row = get_user(user_id)
    if row is None or not row["active"]:
        raise PermissionDenied("الحساب غير موجود أو موقوف.")
    return row


def require_admin(user_id) -> sqlite3.Row:
    row = require_user(user_id)
    if row["role"] != "admin":
        raise PermissionDenied("هذه العملية متاحة لمدير النظام فقط.")
    return row


def has_admin() -> bool:
    with db_connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone() is not None


def _insert_user(username: str, display_name: str, password: str, role: str) -> int:
    username, display_name = username.strip(), display_name.strip()
    if not username or not display_name:
        raise ValueError("أدخل اسم الدخول والاسم الظاهر.")
    if role not in ("admin", "supervisor"):
        raise ValueError("دور غير صالح.")
    validate_password(password, username)
    salt, digest = password_digest(password)
    with db_connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone():
            raise ValueError("اسم الدخول مستخدم بالفعل.")
        cur = conn.execute("INSERT INTO users(username,display_name,role,salt,password_hash,created_at) VALUES(?,?,?,?,?,?)",
                           (username, display_name, role, salt, digest, now_iso()))
        return cur.lastrowid


def bootstrap_admin(username: str, display_name: str, password: str) -> tuple[int, str]:
    """First-run only: creates the first admin and returns (user_id, recovery_code)."""
    if has_admin():
        raise PermissionDenied("يوجد مدير نشط بالفعل؛ لا يمكن تنفيذ الإعداد الأول مرة أخرى.")
    uid = _insert_user(username, display_name, password, "admin")
    audit(uid, "إنشاء حساب المدير", "user", uid, "الإعداد الأول")
    return uid, _issue_recovery_code()


def create_user_as(actor, username: str, display_name: str, password: str, role: str = "supervisor") -> int:
    admin = require_admin(actor)
    uid = _insert_user(username, display_name, password, role)
    audit(admin["id"], "إنشاء حساب مستخدم", "user", uid, f"{username.strip()} ({role})")
    return uid


def reset_password_as(actor, target_id: int, new_password: str) -> None:
    admin = require_admin(actor)
    target = get_user(target_id)
    if target is None:
        raise ValueError("المستخدم غير موجود.")
    validate_password(new_password, target["username"])
    salt, digest = password_digest(new_password)
    with db_connect() as conn:
        conn.execute("UPDATE users SET salt=?,password_hash=?,failed_count=0,lock_until=0 WHERE id=?", (salt, digest, target["id"]))
    audit(admin["id"], "إعادة تعيين كلمة مرور", "user", target["id"], target["username"])


def set_user_active_as(actor, target_id: int, active: bool) -> None:
    admin = require_admin(actor)
    target = get_user(target_id)
    if target is None:
        raise ValueError("المستخدم غير موجود.")
    if not active:
        if int(target["id"]) == int(admin["id"]):
            raise ValueError("لا يمكنك إيقاف الحساب الذي تستخدمه الآن.")
        with db_connect() as conn:
            others = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?", (target["id"],)).fetchone()[0]
        if target["role"] == "admin" and others == 0:
            raise ValueError("لا يمكن إيقاف آخر مدير نشط.")
    with db_connect() as conn:
        conn.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, target["id"]))
    audit(admin["id"], "تغيير حالة حساب", "user", target["id"], "نشط" if active else "موقوف")


def authenticate(username: str, password: str) -> tuple[sqlite3.Row | None, str]:
    note = None  # (user_id, action, description) written after the transaction closes
    result: tuple[sqlite3.Row | None, str]
    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
        if not user:
            return None, "اسم المستخدم أو كلمة المرور غير صحيحة."
        if not user["active"]:
            return None, "هذا الحساب موقوف. راجع مدير النظام."
        if user["lock_until"] > time.time():
            remaining = int(user["lock_until"] - time.time())
            return None, f"الحساب مقفل مؤقتًا بسبب محاولات الدخول الفاشلة. حاول بعد {max(1, remaining // 60 + 1)} دقيقة."
        if verify_password(password, user["salt"], user["password_hash"]):
            conn.execute("UPDATE users SET failed_count=0,lock_until=0 WHERE id=?", (user["id"],))
            return user, ""
        failed = int(user["failed_count"]) + 1
        lock_until = time.time() + 300 if failed >= 5 else 0
        conn.execute("UPDATE users SET failed_count=?,lock_until=? WHERE id=?", (failed, lock_until, user["id"]))
        note = (user["id"], "فشل تسجيل الدخول", f"المحاولة {failed}" + ("؛ قُفل الحساب 5 دقائق" if failed >= 5 else ""))
        result = (None, "تجاوزت عدد محاولات الدخول المسموح. تم قفل الحساب لمدة 5 دقائق." if failed >= 5
                  else f"اسم المستخدم أو كلمة المرور غير صحيحة. المحاولة {failed} من 5.")
    if note:
        audit(note[0], note[1], "user", note[0], note[2])
    return result


# ----------------------------------------------------------------------------- recovery code
def _normalize_code(code: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", normalize_digits(code).upper())


def _issue_recovery_code() -> str:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(6))
    salt, digest = password_digest(_normalize_code(code))
    set_setting("recovery_salt", salt)
    set_setting("recovery_hash", digest)
    set_setting("recovery_fail", "0")
    set_setting("recovery_lock_until", "0")
    return code


def issue_recovery_code_as(actor) -> str:
    admin = require_admin(actor)
    code = _issue_recovery_code()
    audit(admin["id"], "إنشاء رمز استرداد جديد", "settings", "recovery", "")
    return code


def recover_admin_password(username: str, code: str, new_password: str) -> str:
    """Reset an admin password with the recovery code; returns a NEW recovery code."""
    if float(setting("recovery_lock_until", "0") or 0) > time.time():
        raise PermissionDenied("محاولات الاسترداد الخاطئة كثيرة. انتظر 15 دقيقة ثم أعد المحاولة.")
    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND role='admin'", (username.strip(),)).fetchone()
    salt, digest = setting("recovery_salt"), setting("recovery_hash")
    if not user or not salt or not verify_password(_normalize_code(code), salt, digest):
        fails = int(setting("recovery_fail", "0") or 0) + 1
        set_setting("recovery_fail", str(fails))
        if fails >= 5:
            set_setting("recovery_lock_until", str(time.time() + 900))
            set_setting("recovery_fail", "0")
        audit(None, "فشل استرداد حساب المدير", "user", username.strip()[:40], "")
        raise PermissionDenied("اسم المدير أو رمز الاسترداد غير صحيح.")
    validate_password(new_password, user["username"])
    s, d = password_digest(new_password)
    with db_connect() as conn:
        conn.execute("UPDATE users SET salt=?,password_hash=?,failed_count=0,lock_until=0,active=1 WHERE id=?", (s, d, user["id"]))
    audit(user["id"], "استرداد كلمة مرور المدير برمز الاسترداد", "user", user["id"], user["username"])
    return _issue_recovery_code()


# ----------------------------------------------------------------------------- student import
from html.parser import HTMLParser  # noqa: E402


class _TableHTMLParser(HTMLParser):
    """Reads <table> data from HTML exported by Excel (tolerates missing </tr>)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.tables: list[list[list[str]]] = []
        self.current_table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def _close_cell(self):
        if self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
        self.cell = None

    def _close_row(self):
        self._close_cell()
        if self.row and self.current_table is not None:
            self.current_table.append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.in_table, self.current_table = True, []
        elif self.in_table and tag == "tr":
            self._close_row()
            self.row = []
        elif self.in_table and tag in ("td", "th"):
            self._close_cell()
            if self.row is None:
                self.row = []
            self.cell = []
        elif self.in_table and tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.in_table and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.in_table:
            return
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table, self.in_table = None, False


def _norm_header(value) -> str:
    text = str(value or "").replace("\u200f", "").replace("\u200e", "").replace("ـ", "")
    return " ".join(text.strip().split())


def normalize_student_key(value) -> str:
    """Make IDs identical across CSV/XLS/XLSX/HTML (digits, '.0' suffix from Excel floats, spaces)."""
    s = normalize_digits(str(value if value is not None else "")).replace("\u200f", "").replace("\u200e", "")
    s = "".join(s.split())
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", s):
        raise ValueError(f"رقم التعريف «{s}» بصيغة علمية؛ فسد الرقم في Excel. حوّل العمود إلى نص ثم أعد الحفظ.")
    return s


def _find_header_rows(rows: list[list[str]]):
    for idx, row in enumerate(rows):
        norm = [_norm_header(c) for c in row]
        if any("رقم التعريف" in h for h in norm) and any(h == "اللقب" for h in norm) and any(h == "الاسم" for h in norm):
            return norm, rows[idx + 1:]
    return None


def _row_to_student(headers: list[str], row: list[str]) -> dict | None:
    cells = list(row) + [""] * max(0, len(headers) - len(row))
    data = {headers[i]: str(cells[i]).strip() for i in range(len(headers))}

    def val(*names):
        for name in names:            # exact header match first, then "contains"
            if name in data:
                return data[name].strip()
        for name in names:
            for k, v in data.items():
                if name in k:
                    return v.strip()
        return ""

    last = val("اللقب", "الاسم العائلي", "Surname")
    first = val("الاسم", "الاسم الشخصي", "First name")
    if not (last or first):
        return None
    year, branch, section = val("السنة"), val("الشعبة"), val("القسم")
    class_name = " ".join(p for p in (year, branch, section) if p).strip() or val("الفوج", "Class", "Section")
    return {"raw_key": val("رقم التعريف", "رقم تعريف", "معرّف التلميذ", "Student ID"), "last_name": last, "first_name": first,
            "gender": val("الجنس"), "year": year, "branch": branch, "section": section,
            "class_name": class_name or "غير محدد"}


def _decode_text(raw: bytes) -> str:
    """UTF-8 first; Arabic Excel's 'CSV' is usually Windows-1256, so fall back to it (never silently mangle)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1256")
    except UnicodeDecodeError as exc:
        raise ValueError("تعذر تحديد ترميز الملف. احفظه من Excel بصيغة XLSX أو «CSV UTF-8».") from exc


def _students_from_rows(rows, label: str) -> list[dict]:
    found = _find_header_rows(rows)
    if not found:
        raise ValueError(f"لم أجد صف العناوين المطلوب في {label}: رقم التعريف، اللقب، الاسم.")
    headers, rest = found
    return [s for s in (_row_to_student(headers, r) for r in rest) if s]


def read_students_file(path: str) -> list[dict]:
    """Return raw student dicts (key may still be empty/duplicated; see prepare_students)."""
    suffix = Path(path).suffix.lower()
    raw = Path(path).read_bytes()
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if suffix in (".html", ".htm") or head.startswith((b"<html", b"<!doctype", b"<table", b"<?xml")) or b"<table" in head:
        parser = _TableHTMLParser()
        parser.feed(_decode_text(raw))
        for table in parser.tables:
            if _find_header_rows(table):
                return _students_from_rows(table, "جدول HTML")
        raise ValueError("لم أجد جدولًا يحتوي الأعمدة: رقم التعريف، اللقب، الاسم.")
    if suffix == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                rows = [["" if v is None else str(v).strip() for v in row] for row in ws.iter_rows(values_only=True)]
                if _find_header_rows(rows):
                    return _students_from_rows(rows, "ملف Excel")
        finally:
            wb.close()
        raise ValueError("لم أجد صف العناوين المطلوب في ملف Excel: رقم التعريف، اللقب، الاسم.")
    if suffix == ".xls":
        try:
            import xlrd
            book = xlrd.open_workbook(path)
            for sheet in book.sheets():
                rows = [[str(sheet.cell_value(i, j)).strip() for j in range(sheet.ncols)] for i in range(sheet.nrows)]
                if _find_header_rows(rows):
                    return _students_from_rows(rows, "ملف Excel")
        except Exception as exc:
            raise ValueError("لم أستطع قراءة ملف XLS. احفظه من Excel بصيغة XLSX ثم أعد الاستيراد.") from exc
        raise ValueError("لم أجد صف العناوين المطلوب في ملف Excel: رقم التعريف، اللقب، الاسم.")
    if suffix == ".csv":
        text = _decode_text(raw)
        for delimiter in (",", ";", "\t"):
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
            if _find_header_rows(rows):
                return _students_from_rows(rows, "ملف CSV")
        raise ValueError("لم أجد صف العناوين المطلوب في ملف CSV: رقم التعريف، اللقب، الاسم.")
    raise ValueError("الصيغ المقبولة: ملف Excel XLSX/XLS، CSV، أو ملف HTML المُصدّر من Excel.")


class ImportReport(NamedTuple):
    inserted: int
    updated: int
    deactivated: int
    skipped_no_id: int
    duplicate_rows: int
    renamed: int


def prepare_students(raw_students: list[dict]) -> tuple[list[dict], int, int]:
    """Validate IDs. Returns (unique students, rows skipped for missing ID, harmless duplicate rows).

    Two different people sharing one ID abort the whole import (they must never be merged silently).
    """
    unique: dict[str, dict] = {}
    skipped = dups = 0
    conflicts: list[str] = []
    for s in raw_students:
        key = normalize_student_key(s.get("raw_key", ""))
        if not key:
            skipped += 1
            continue
        s = {**s, "student_key": key}
        prev = unique.get(key)
        if prev is None:
            unique[key] = s
        elif (prev["last_name"], prev["first_name"]) == (s["last_name"], s["first_name"]):
            dups += 1
        else:
            conflicts.append(f"{key}: «{prev['last_name']} {prev['first_name']}» ≠ «{s['last_name']} {s['first_name']}»")
    if conflicts:
        raise ValueError("يوجد تلاميذ مختلفون بنفس رقم التعريف في الملف، لذلك أُلغي الاستيراد لتفادي الدمج الخاطئ:\n"
                         + "\n".join(conflicts[:8]) + ("\n..." if len(conflicts) > 8 else ""))
    if raw_students and not unique:
        raise ValueError("عمود «رقم التعريف» فارغ في جميع الصفوف؛ لا يمكن الاستيراد بأمان دون رقم تعريف.")
    return list(unique.values()), skipped, dups


def import_students(path: str, user_id, deactivate_missing: bool = False) -> ImportReport:
    admin = require_admin(user_id)
    students, skipped, dups = prepare_students(read_students_file(path))
    if not students:
        raise ValueError("لم يتم العثور على أي تلميذ قابل للاستيراد.")
    inserted = updated = renamed = deactivated = 0
    renamed_examples: list[str] = []
    stamp = now_iso()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for s in students:
            old = conn.execute("SELECT last_name,first_name FROM students WHERE student_key=?", (s["student_key"],)).fetchone()
            conn.execute("""INSERT INTO students(student_key,last_name,first_name,gender,year,branch,section,class_name,active,imported_at)
                VALUES(:student_key,:last_name,:first_name,:gender,:year,:branch,:section,:class_name,1,:imported_at)
                ON CONFLICT(student_key) DO UPDATE SET last_name=excluded.last_name,first_name=excluded.first_name,
                gender=excluded.gender,year=excluded.year,branch=excluded.branch,section=excluded.section,
                class_name=excluded.class_name,active=1,imported_at=excluded.imported_at""", {**s, "imported_at": stamp})
            if old:
                updated += 1
                if (old["last_name"], old["first_name"]) != (s["last_name"], s["first_name"]):
                    renamed += 1
                    if len(renamed_examples) < 5:
                        renamed_examples.append(f"{s['student_key']}: {old['last_name']} {old['first_name']} ← {s['last_name']} {s['first_name']}")
            else:
                inserted += 1
        if deactivate_missing:
            present = {s["student_key"] for s in students}
            gone = [r[0] for r in conn.execute("SELECT student_key FROM students WHERE active=1").fetchall() if r[0] not in present]
            conn.executemany("UPDATE students SET active=0 WHERE student_key=?", [(k,) for k in gone])
            deactivated = len(gone)
    report = ImportReport(inserted, updated, deactivated, skipped, dups, renamed)
    audit(admin["id"], "استيراد قائمة التلاميذ", "students", "",
          f"ملف: {Path(path).name}؛ جديد: {inserted}؛ محدّث: {updated}؛ أُوقف: {deactivated}؛ بلا رقم: {skipped}؛ مكرر: {dups}؛ تغيّر اسم: {renamed}"
          + ("؛ أمثلة: " + " | ".join(renamed_examples) if renamed_examples else ""))
    return report


def student_name(row) -> str:
    return (str(row["last_name"]) + " " + str(row["first_name"])).strip()


def get_student(key: str) -> sqlite3.Row | None:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM students WHERE student_key=?", (key,)).fetchone()


# ----------------------------------------------------------------------------- records
_PREFIXES = {"late": "SP", "absence_return": "SP", "exit_permit": "SP", "exit_return": "SP"}


def next_record_no(kind: str, conn: sqlite3.Connection) -> str:
    prefix, year = _PREFIXES.get(kind, "SP"), date.today().year
    stem = f"{prefix}-{year}-"
    greatest = conn.execute("SELECT COALESCE(MAX(CAST(substr(record_no,?) AS INTEGER)),0) FROM records WHERE record_no LIKE ?",
                            (len(stem) + 1, stem + "%")).fetchone()[0]
    return f"{stem}{int(greatest) + 1:06d}"


def validate_workstation_clock(conn: sqlite3.Connection | None = None, current: datetime | None = None) -> datetime:
    """Reject implausible dates and a workstation clock moved behind saved operations.

    This is an offline consistency check, not an Internet time authority. Users should
    keep Windows date, time, and time-zone synchronization enabled.
    """
    now = current or datetime.now()
    if now.year < 2020 or now.year > 2100:
        raise ValueError("تاريخ الحاسوب غير منطقي. صحّح التاريخ والساعة في Windows قبل إصدار أي رخصة.")
    own_connection = conn is None
    db = conn or db_connect()
    try:
        latest = db.execute("SELECT MAX(COALESCE(NULLIF(issued_at,''),created_at)) FROM records").fetchone()[0]
        if latest:
            try:
                latest_dt = datetime.strptime(str(latest)[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ValueError("تعذر التحقق من آخر توقيت محفوظ. راجع قاعدة البيانات قبل إصدار رخصة جديدة.")
            if latest_dt > now + timedelta(minutes=5):
                raise ValueError("ساعة الحاسوب أقدم من توقيت عملية مسجلة سابقًا. صحّح ساعة Windows ومزامنتها قبل إصدار رخصة جديدة.")
    finally:
        if own_connection:
            db.close()
    return now


def _insert_record(conn, kind, student_key, name, class_name, user_id, reason, details, related_id=None) -> sqlite3.Row:
    token = secrets.token_hex(24)  # 192-bit random; carries no student data
    record_no = next_record_no(kind, conn)
    validate_workstation_clock(conn)
    issued_at = now_iso()
    cur = conn.execute("""INSERT INTO records(record_no,qr_token,kind,student_key,student_name,class_name,created_at,issued_at,user_id,reason,
                          status,details_json,related_record_id) VALUES(?,?,?,?,?,?,?,?,?,?,'valid',?,?)""",
                       (record_no, token, kind, student_key, name, class_name, issued_at, issued_at, user_id, (reason or "").strip(),
                        json.dumps(details or {}, ensure_ascii=False), related_id))
    return conn.execute("SELECT * FROM records WHERE id=?", (cur.lastrowid,)).fetchone()


def create_record(kind: str, student, user_id, reason: str = "", details: dict | None = None,
                  related_record_id: int | None = None) -> sqlite3.Row:
    if kind not in RECORD_KINDS:
        raise ValueError("نوع سجل غير معروف.")
    user = require_user(user_id)
    if student is None:
        raise ValueError("يجب اختيار التلميذ.")
    if len((reason or "").strip()) > 200:
        raise ValueError("النص طويل جدًا (الحد الأقصى 200 حرف للسبب).")
    for key in ("guardian_name", "relationship"):
        if len(str((details or {}).get(key, ""))) > 80:
            raise ValueError("اسم ولي الأمر أو صلته طويل جدًا (الحد الأقصى 80 حرفًا).")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # serialises numbering between sessions
        row = _insert_record(conn, kind, student["student_key"], student_name(student), student["class_name"],
                             user["id"], reason, details, related_record_id)
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), user["id"], "إنشاء سجل", "record", str(row["id"]),
                      f"{row['record_no']}؛ النوع: {kind}؛ التلميذ: {row['student_name']}"))
    return row



def find_open_exit_for_student(student_key: str) -> sqlite3.Row | None:
    """Return the newest valid exceptional-exit permit that has no recorded return."""
    with db_connect() as conn:
        return conn.execute("SELECT * FROM records WHERE student_key=? AND kind='exit_permit' AND status='valid' ORDER BY id DESC LIMIT 1",
                            (str(student_key),)).fetchone()


def update_student_as(actor, student_id: int, fields: dict) -> sqlite3.Row:
    """Admin-only controlled student edit; the permanent student key cannot be changed."""
    admin = require_admin(actor)
    allowed = ('last_name', 'first_name', 'gender', 'year', 'branch', 'section', 'class_name')
    clean = {key: str(fields.get(key, '') or '').strip() for key in allowed}
    if not clean['last_name'] or not clean['first_name'] or not clean['class_name']:
        raise ValueError("اللقب والاسم والقسم حقول إلزامية.")
    if any(len(value) > 120 for value in clean.values()):
        raise ValueError("أحد الحقول أطول من الحد المسموح (120 حرفًا).")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM students WHERE id=?", (int(student_id),)).fetchone()
        if not old:
            raise ValueError("التلميذ غير موجود.")
        conn.execute("UPDATE students SET last_name=?,first_name=?,gender=?,year=?,branch=?,section=?,class_name=? WHERE id=?",
                     (clean['last_name'], clean['first_name'], clean['gender'], clean['year'], clean['branch'], clean['section'], clean['class_name'], int(student_id)))
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), admin['id'], "تعديل بيانات تلميذ", "student", str(student_id),
                      f"{old['student_key']}: {student_name(old)} → {clean['first_name']} {clean['last_name']}؛ القسم: {clean['class_name']}"))
        return conn.execute("SELECT * FROM students WHERE id=?", (int(student_id),)).fetchone()

def monthly_late_count(student_key: str, month: str | None = None) -> int:
    """Count valid/non-cancelled late slips in YYYY-MM for one student."""
    month = month or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("صيغة الشهر غير صحيحة؛ استعمل YYYY-MM.")
    with db_connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM records WHERE kind='late' AND status!='cancelled' "
                                "AND student_key=? AND substr(COALESCE(NULLIF(issued_at,''),created_at),1,7)=?",
                                (str(student_key), month)).fetchone()[0])


def create_late_slip(student, user_id, reason: str) -> sqlite3.Row:
    if not (reason or "").strip():
        raise ValueError("أدخل سبب التأخر.")
    return create_record("late", student, user_id, reason)


def absence_days(start: str, end: str) -> tuple[str, str, int]:
    """Validate dates and count absence days up to, but excluding, the return date.

    Example: absence starts 2025-01-10 and the student returns 2025-01-12:
    absence days are Jan 10 and Jan 11 (2 days). A same-day return is 0 days.
    """
    try:
        d1 = datetime.strptime(normalize_digits(start).strip(), "%Y-%m-%d").date()
        d2 = datetime.strptime(normalize_digits(end).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("استعمل الصيغة YYYY-MM-DD لتاريخي البداية والعودة.") from exc
    if d2 < d1:
        raise ValueError("تاريخ العودة لا يمكن أن يسبق بداية الغياب.")
    if d2 > date.today():
        raise ValueError("تاريخ العودة لا يمكن أن يكون في المستقبل.")
    days = (d2 - d1).days
    if days > 366:
        raise ValueError("مدة الغياب تتجاوز 366 يومًا؛ راجع التاريخين.")
    return d1.isoformat(), d2.isoformat(), days


def create_absence_return(student, user_id, start: str, end: str, note: str = "",
                          proof_method: str = "medical_certificate", certificate_present: bool = False,
                          guardian_name: str = "", relationship: str = "", national_id: str = "",
                          guardian_present: bool = False) -> sqlite3.Row:
    """Create a post-absence entry permit using date-only absence duration.

    The return date is not counted as an absence day. A duration of 0-2 days allows
    a verified medical certificate; more than 2 days requires the guardian in person,
    even if a medical certificate exists. A guardian may attend voluntarily earlier.
    """
    s, e, absence_count = absence_days(start, end)
    note = (note or "").strip()
    if len(note) > 200:
        raise ValueError("الملاحظة طويلة جدًا (الحد الأقصى 200 حرف).")
    date_difference_days = absence_count
    if proof_method not in ("medical_certificate", "guardian"):
        raise ValueError("اختر طريقة تبرير الغياب: شهادة طبية أو حضور ولي الأمر.")
    if absence_count > ABSENCE_CERTIFICATE_MAX_DURATION_DAYS and proof_method != "guardian":
        raise ValueError("عدد أيام الغياب قبل يوم العودة تجاوز يومين؛ حضور ولي الأمر شخصيًا إلزامي حتى مع وجود شهادة طبية.")

    details = {
        "absence_start": s, "return_date": e, "days_count": absence_count,
        "date_difference_days": date_difference_days, "proof_method": proof_method,
    }
    if proof_method == "medical_certificate":
        if not certificate_present:
            raise ValueError("يجب تأكيد استلام الشهادة الطبية والتحقق منها قبل إصدار الرخصة.")
        details["medical_certificate_confirmed"] = True
        details["medical_certificate_verified_at"] = now_iso()
    else:
        guardian_name, relationship = guardian_name.strip(), relationship.strip()
        if not guardian_present:
            raise ValueError("يجب تأكيد حضور ولي الأمر شخصيًا إلى الإدارة.")
        if not (guardian_name and relationship and national_id.strip()):
            raise ValueError("عند حضور ولي الأمر، يجب إدخال اسمه الكامل وصلته ورقم بطاقة هويته.")
        if len(guardian_name) > 80 or len(relationship) > 80:
            raise ValueError("اسم ولي الأمر أو صلته طويل جدًا (الحد الأقصى 80 حرفًا).")
        nid = validate_national_id(national_id)
        try:
            encrypted = protect_sensitive_text(nid)
        except Exception as exc:
            raise RuntimeError(f"تعذر تشفير رقم هوية ولي الأمر على هذا الحساب في Windows. لم تُحفظ الرخصة.\n{exc}") from exc
        details.update({"guardian_name": guardian_name, "relationship": relationship,
                        "national_id_enc": encrypted, "national_id_last4": nid[-4:],
                        "national_id_length": len(nid), "guardian_present": True,
                        "guardian_verified_at": now_iso()})

    user = require_user(user_id)
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for r in conn.execute("SELECT record_no,details_json FROM records WHERE kind='absence_return' AND student_key=? AND status<>'cancelled'",
                              (student["student_key"],)).fetchall():
            d = json.loads(r["details_json"] or "{}")
            old_start, old_return = d.get("absence_start", ""), d.get("return_date", "")
            # Both periods are half-open [absence_start, return_date): the return
            # date itself is present at school and is not an absence day.
            if old_start and s < e and old_start < old_return and s < old_return and e > old_start:
                raise ValueError(f"هذه الفترة تتداخل مع فترة غياب مسجلة سابقًا ({r['record_no']}: من {old_start} إلى ما قبل {old_return}). "
                                 "ألغِ السجل القديم أولًا إن كان خاطئًا.")
        row = _insert_record(conn, "absence_return", student["student_key"], student_name(student), student["class_name"], user["id"],
                             note, details)
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), user["id"], "إنشاء سجل", "record", str(row["id"]),
                      f"{row['record_no']}؛ النوع: absence_return؛ التلميذ: {row['student_name']}؛ طريقة التبرير: {proof_method}"))
    return row


def validate_national_id(value: str) -> str:
    nid = normalize_digits(value).replace(" ", "").strip()
    if not nid.isdigit() or not (6 <= len(nid) <= 20):
        raise ValueError("أدخل رقم بطاقة الهوية الوطنية أرقامًا فقط (6 إلى 20 رقمًا).")
    return nid


def create_exit_permit(student, user_id, reason: str, guardian_name: str, relationship: str, national_id: str) -> sqlite3.Row:
    reason, guardian_name, relationship = reason.strip(), guardian_name.strip(), relationship.strip()
    if not (reason and guardian_name and relationship and national_id.strip()):
        raise ValueError("أكمل سبب الخروج واسم ولي الأمر وصلته ورقم هويته.")
    nid = validate_national_id(national_id)
    try:
        encrypted = protect_sensitive_text(nid)  # full number is NEVER stored in clear text
    except Exception as exc:
        raise RuntimeError(f"تعذر تشفير رقم الهوية على هذا الحساب في Windows. لم يُحفظ التصريح.\n{exc}") from exc
    details = {"guardian_name": guardian_name, "relationship": relationship, "national_id_enc": encrypted,
               "national_id_last4": nid[-4:], "national_id_length": len(nid)}
    return create_record("exit_permit", student, user_id, reason, details)


def mask_national_id(details: dict) -> str:
    last4 = str(details.get("national_id_last4", "")).strip()
    n = int(details.get("national_id_length", 0) or 0)
    return ("*" * max(8, n - 4) + last4) if last4 else "غير مدخل"


def reveal_national_id(record_id: int, user_id) -> str:
    admin = require_admin(user_id)
    row = record_by_id(record_id)
    if not row or row["kind"] not in ("exit_permit", "absence_return"):
        raise ValueError("السجل لا يحتوي على بيانات هوية ولي أمر.")
    enc = json.loads(row["details_json"] or "{}").get("national_id_enc", "")
    if not enc:
        raise ValueError("لا يوجد رقم هوية محفوظ لهذا السجل.")
    value = reveal_sensitive_text(enc)
    audit(admin["id"], "عرض رقم الهوية الكامل", "record", record_id, row["record_no"])
    return value


def find_recent_duplicate(student_key: str, kind: str, window_seconds: int = 300) -> sqlite3.Row | None:
    """Return a recent same-kind record for a student, for a UI confirmation warning."""
    cutoff = (datetime.now() - timedelta(seconds=max(1, int(window_seconds)))).strftime("%Y-%m-%d %H:%M:%S")
    with db_connect() as conn:
        return conn.execute("SELECT * FROM records WHERE student_key=? AND kind=? AND status='valid' AND created_at>=? "
                            "ORDER BY created_at DESC,id DESC LIMIT 1", (student_key, kind, cutoff)).fetchone()


def cancel_record(record_id: int, user_id, reason: str) -> sqlite3.Row:
    user = require_user(user_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("لا يمكن الإلغاء دون تسجيل السبب.")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("السجل غير موجود.")
        if row["status"] != "valid":
            raise ValueError("لا يمكن إلغاء سجل غير ساري.")
        changed = conn.execute("UPDATE records SET status='cancelled',cancelled_reason=?,cancelled_at=?,cancelled_by=? WHERE id=? AND status='valid'",
                               (reason, now_iso(), user["id"], record_id)).rowcount
        if changed != 1:
            raise ValueError("تغيرت حالة السجل أثناء العملية؛ حدّث القائمة وحاول مجددًا.")
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), user["id"], "إلغاء سجل", "record", str(record_id), f"{row['record_no']}؛ السبب: {reason}"))
    return record_by_id(record_id)


def return_from_exit(record_id: int, user_id) -> sqlite3.Row:
    """Atomically mark a valid exit permit as returned and issue the linked entry slip."""
    user = require_user(user_id)
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        permit = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if not permit or permit["kind"] != "exit_permit":
            raise ValueError("السجل ليس رخصة خروج استثنائي.")
        if permit["status"] != "valid":
            raise ValueError("التصريح غير ساري (ملغى أو سُجّلت عودته سابقًا).")
        changed = conn.execute("UPDATE records SET status='returned',returned_at=?,returned_by=? WHERE id=? AND status='valid'",
                               (now_iso(), user["id"], record_id)).rowcount
        if changed != 1:
            raise ValueError("تغيرت حالة التصريح أثناء العملية؛ حدّث القائمة وحاول مجددًا.")
        slip = _insert_record(conn, "exit_return", permit["student_key"], permit["student_name"], permit["class_name"], user["id"],
                              "العودة بموجب رخصة خروج استثنائي", {"permit_no": permit["record_no"]}, related_id=record_id)
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), user["id"], "تسجيل عودة بعد خروج استثنائي", "record", str(record_id), f"{permit['record_no']} → {slip['record_no']}"))
    return slip


def record_by_token(token: str) -> sqlite3.Row | None:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM records WHERE qr_token=?", (token,)).fetchone()


def record_by_id(record_id: int) -> sqlite3.Row | None:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()


def kind_label(kind: str) -> str:
    return {"late": "رخصة الدخول — تأخر", "absence_return": "رخصة الدخول — عودة بعد الغياب",
            "exit_permit": "رخصة خروج استثنائي", "exit_return": "إثبات عودة من خروج استثنائي"}.get(kind, kind)


def status_label(status: str) -> str:
    return {"valid": "ساري", "cancelled": "ملغى", "returned": "تمت العودة"}.get(status, status)


# ----------------------------------------------------------------------------- stats & export
def compute_stats(today: date | None = None) -> dict:
    """Return consistent daily, rolling-week and calendar-month statistics.

    The issue timestamp is authoritative for every dashboard/report. Cancelled permits
    remain available in the cancellation section but are excluded from active totals.
    """
    today = today or date.today()
    day = today.strftime("%Y-%m-%d")
    month = today.strftime("%Y-%m")
    week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    issue = "COALESCE(NULLIF(issued_at,''),created_at)"
    out: dict = {"day": day, "month": month, "week_start": week_start}
    with db_connect() as conn:
        q = lambda sql, *p: [dict(r) for r in conn.execute(sql, p).fetchall()]  # noqa: E731
        out["today_by_kind"] = q(
            f"SELECT kind,status,COUNT(*) n FROM records WHERE substr({issue},1,10)=? GROUP BY kind,status", day)
        out["week_by_kind"] = q(
            f"SELECT kind,status,COUNT(*) n FROM records WHERE substr({issue},1,10)>=? AND substr({issue},1,10)<=? GROUP BY kind,status", week_start, day)
        out["month_by_kind"] = q(
            f"SELECT kind,status,COUNT(*) n FROM records WHERE substr({issue},1,7)=? GROUP BY kind,status", month)
        out["today_total"] = int(conn.execute(
            f"SELECT COUNT(*) FROM records WHERE substr({issue},1,10)=? AND status<>'cancelled'", (day,)).fetchone()[0])
        out["week_total"] = int(conn.execute(
            f"SELECT COUNT(*) FROM records WHERE substr({issue},1,10)>=? AND substr({issue},1,10)<=? AND status<>'cancelled'", (week_start, day)).fetchone()[0])
        out["month_total"] = int(conn.execute(
            f"SELECT COUNT(*) FROM records WHERE substr({issue},1,7)=? AND status<>'cancelled'", (month,)).fetchone()[0])
        out["today_unique_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)=? AND kind IN ('late','absence_return','exit_permit','exit_return') AND status<>'cancelled'", (day,)).fetchone()[0])
        out["today_entry_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)=? AND kind IN ('late','absence_return','exit_return') AND status<>'cancelled'", (day,)).fetchone()[0])
        out["today_late_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)=? AND kind='late' AND status<>'cancelled'", (day,)).fetchone()[0])
        out["today_absence_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)=? AND kind='absence_return' AND status<>'cancelled'", (day,)).fetchone()[0])
        out["today_exit_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)=? AND kind='exit_permit' AND status<>'cancelled'", (day,)).fetchone()[0])
        out["week_unique_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,10)>=? AND substr({issue},1,10)<=? AND kind IN ('late','absence_return','exit_permit','exit_return') AND status<>'cancelled'", (week_start, day)).fetchone()[0])
        out["month_unique_students"] = int(conn.execute(
            f"SELECT COUNT(DISTINCT student_key) FROM records WHERE substr({issue},1,7)=? AND kind IN ('late','absence_return','exit_permit','exit_return') AND status<>'cancelled'", (month,)).fetchone()[0])
        out["by_day"] = q(
            f"SELECT substr({issue},1,10) d,kind,COUNT(*) n FROM records WHERE substr({issue},1,7)=? AND status<>'cancelled' GROUP BY d,kind ORDER BY d", month)
        out["by_class"] = q(
            f"SELECT class_name,kind,COUNT(*) n FROM records WHERE substr({issue},1,7)=? AND status<>'cancelled' GROUP BY class_name,kind ORDER BY n DESC,class_name LIMIT 30", month)
        out["by_supervisor"] = q(
            f"SELECT COALESCE(u.display_name,'غير معروف') supervisor,r.kind,COUNT(*) n FROM records r LEFT JOIN users u ON r.user_id=u.id WHERE substr(COALESCE(NULLIF(r.issued_at,''),r.created_at),1,7)=? AND r.status<>'cancelled' GROUP BY r.user_id,u.display_name,r.kind ORDER BY n DESC,supervisor LIMIT 40", month)
        out["by_reason"] = q(
            f"SELECT kind,reason,COUNT(*) n FROM records WHERE substr({issue},1,7)=? AND status<>'cancelled' AND TRIM(COALESCE(reason,''))<>'' GROUP BY kind,reason ORDER BY n DESC,reason LIMIT 30", month)
        out["cancellations"] = q(
            "SELECT r.record_no,r.kind,r.student_name,r.class_name,r.created_at,r.issued_at,r.cancelled_at,r.cancelled_reason,u.display_name cancelled_by FROM records r LEFT JOIN users u ON r.cancelled_by=u.id WHERE r.status='cancelled' AND substr(r.cancelled_at,1,7)=? ORDER BY r.cancelled_at DESC LIMIT 100", month)
        rows = conn.execute("SELECT student_key,student_name,class_name,details_json FROM records WHERE kind='absence_return' AND status<>'cancelled'").fetchall()
        out["active_students"] = int(conn.execute("SELECT COUNT(*) FROM students WHERE active=1").fetchone()[0])
        out["open_exits"] = int(conn.execute("SELECT COUNT(DISTINCT student_key) FROM records WHERE kind='exit_permit' AND status='valid'").fetchone()[0])
    totals: dict[str, dict] = {}
    for r in rows:
        t = totals.setdefault(r["student_key"], {"student_name": r["student_name"], "class_name": r["class_name"], "n": 0, "days": 0})
        t["n"] += 1
        try:
            t["days"] += max(0, int(json.loads(r["details_json"] or "{}").get("days_count", 0) or 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    out["absence_totals"] = sorted(totals.values(), key=lambda t: (-t["days"], t["student_name"]))[:30]
    return out


def csv_safe(value):
    """Neutralise spreadsheet formulas (CSV injection) in exported text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_records_csv(path: str, user_id) -> int:
    user = require_user(user_id)
    with db_connect() as conn:
        rows = conn.execute("SELECT r.record_no,r.kind,r.student_name,r.class_name,r.created_at,u.display_name,r.reason,r.status,"
                            "r.cancelled_reason,r.cancelled_at,r.returned_at,r.printed_count FROM records r LEFT JOIN users u ON r.user_id=u.id "
                            "ORDER BY r.id DESC").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["الرقم", "النوع", "التلميذ", "القسم", "تاريخ الإنشاء", "المشرف", "السبب", "الحالة", "سبب الإلغاء", "تاريخ الإلغاء", "تاريخ العودة", "عدد الطباعة"])
        for r in rows:
            w.writerow([csv_safe(x) for x in (r[0], kind_label(r[1]), r[2], r[3], r[4], r[5] or "", r[6], status_label(r[7]), r[8], r[9], r[10], r[11])])
    audit(user["id"], "تصدير سجل العمليات", "export", "", f"{len(rows)} سجلًا")
    return len(rows)


def export_students_csv(path: str, user_id) -> int:
    admin = require_admin(user_id)
    with db_connect() as conn:
        rows = conn.execute("SELECT student_key,last_name,first_name,gender,year,branch,section,class_name,active FROM students ORDER BY class_name,last_name").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["رقم التعريف", "اللقب", "الاسم", "الجنس", "السنة", "الشعبة", "القسم", "القسم الكامل", "الحالة"])
        for r in rows:
            w.writerow([csv_safe(x) for x in (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], "نشط" if r[8] else "غير نشط")])
    audit(admin["id"], "تصدير قائمة التلاميذ", "export", "", f"{len(rows)} تلميذًا")
    return len(rows)



def dashboard_series(period: str = "week", today: date | None = None) -> dict:
    """Return chronological chart buckets for today, the last 7 days, or the last 30 days.

    Counts are based on issue time (falling back to creation time) and exclude cancelled records.
    """
    today = today or date.today()
    if period not in {"day", "week", "month"}:
        raise ValueError("الفترة الإحصائية غير صحيحة.")
    if period == "day":
        labels = [f"{h:02d}:00" for h in range(24)]
        start = datetime.combine(today, datetime.min.time())
        end = start + timedelta(days=1)
        sql = """SELECT CAST(substr(COALESCE(NULLIF(issued_at,''),created_at),12,2) AS INTEGER) h,
                         kind,COUNT(*) n FROM records
                  WHERE COALESCE(NULLIF(issued_at,''),created_at)>=?
                    AND COALESCE(NULLIF(issued_at,''),created_at)<? AND status<>'cancelled'
                  GROUP BY h,kind"""
        params = (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
        index = {h: i for i, h in enumerate(range(24))}
    else:
        if period == "week":
            first = today - timedelta(days=6)
        else:
            first = today.replace(day=1)
        days = (today - first).days + 1
        labels = [(first + timedelta(days=i)).strftime("%d/%m") for i in range(days)]
        sql = """SELECT substr(COALESCE(NULLIF(issued_at,''),created_at),1,10) d,
                         kind,COUNT(*) n FROM records
                  WHERE substr(COALESCE(NULLIF(issued_at,''),created_at),1,10)>=?
                    AND substr(COALESCE(NULLIF(issued_at,''),created_at),1,10)<=?
                    AND status<>'cancelled' GROUP BY d,kind"""
        params = (first.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
        index = {(first + timedelta(days=i)).strftime("%Y-%m-%d"): i for i in range(days)}
    data = {kind: [0] * len(labels) for kind in ("late", "absence_return", "exit_permit", "exit_return")}
    with db_connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    for row in rows:
        key = int(row["h"]) if period == "day" else row["d"]
        if key in index and row["kind"] in data:
            data[row["kind"]][index[key]] = int(row["n"])
    return {"period": period, "labels": labels, "series": data,
            "totals": {kind: sum(values) for kind, values in data.items()}}



def dashboard_metrics(today: date | None = None) -> dict:
    """Fast dashboard metrics; student counts are DISTINCT pupils, not permit rows."""
    today = today or date.today()
    day = today.strftime("%Y-%m-%d")
    week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    issue = "COALESCE(NULLIF(issued_at,''),created_at)"
    def aggregate(conn, start: str, end: str) -> dict:
        row = conn.execute(f"""SELECT
            COUNT(*) AS permits,
            COUNT(DISTINCT student_key) AS students,
            COUNT(DISTINCT CASE WHEN kind IN ('late','absence_return','exit_return') THEN student_key END) AS entry_students,
            COUNT(DISTINCT CASE WHEN kind='late' THEN student_key END) AS late_students,
            COUNT(DISTINCT CASE WHEN kind='absence_return' THEN student_key END) AS absence_students,
            COUNT(DISTINCT CASE WHEN kind='exit_permit' THEN student_key END) AS exit_students
            FROM records WHERE substr({issue},1,10)>=? AND substr({issue},1,10)<=?
              AND kind IN ('late','absence_return','exit_permit','exit_return') AND status<>'cancelled'""", (start, end)).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}
    with db_connect() as conn:
        today_data = aggregate(conn, day, day)
        week_data = aggregate(conn, week_start, day)
        month_data = aggregate(conn, month_start, day)
        students = int(conn.execute("SELECT COUNT(*) FROM students WHERE active=1").fetchone()[0])
        open_exits = int(conn.execute("SELECT COUNT(DISTINCT student_key) FROM records WHERE kind='exit_permit' AND status='valid'").fetchone()[0])
    return {"day": day, "students_active": students, "open_exits": open_exits,
            "today": today_data, "week": week_data, "month": month_data}


def dashboard_detail_records(key: str, today: date | None = None) -> list[dict]:
    """Rows behind dashboard KPI cards; used to make each count explainable."""
    today = today or date.today()
    day = today.strftime("%Y-%m-%d")
    week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    issue = "COALESCE(NULLIF(r.issued_at,''),r.created_at)"
    mapping = {
        "today_entry": (day, day, ("late", "absence_return", "exit_return"), "active"),
        "today_late": (day, day, ("late",), "active"),
        "today_absence": (day, day, ("absence_return",), "active"),
        "today_exit": (day, day, ("exit_permit",), "active"),
        "today_all": (day, day, ("late", "absence_return", "exit_permit", "exit_return"), "active"),
        "week_all": (week_start, day, ("late", "absence_return", "exit_permit", "exit_return"), "active"),
        "month_all": (month_start, day, ("late", "absence_return", "exit_permit", "exit_return"), "active"),
        "open_exits": ("", "", ("exit_permit",), "open"),
    }
    if key not in mapping:
        raise ValueError("مؤشر لوحة التحكم غير معروف.")
    start, end, kinds, state = mapping[key]
    placeholders = ",".join("?" for _ in kinds)
    params: list[object] = list(kinds)
    clause = f"r.kind IN ({placeholders})"
    if state == "open":
        clause += " AND r.status='valid'"
    else:
        clause += f" AND substr({issue},1,10)>=? AND substr({issue},1,10)<=? AND r.status<>'cancelled'"
        params.extend([start, end])
    sql = ("SELECT r.*,u.display_name AS supervisor FROM records r LEFT JOIN users u ON u.id=r.user_id WHERE "
           + clause + f" ORDER BY {issue} DESC,r.id DESC")
    with db_connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

def query_records(user_id, filters: dict | None = None) -> list[dict]:
    """Fetch all records matching validated UI filters for display/export/reporting."""
    require_user(user_id)
    filters = filters or {}
    clauses = ["1=1"]
    params: list[object] = []
    query = str(filters.get("query", "")).strip()
    if query:
        clauses.append("(r.record_no LIKE ? OR r.student_name LIKE ? OR r.student_key LIKE ? OR r.class_name LIKE ? OR r.reason LIKE ? OR COALESCE(u.display_name,'') LIKE ?)")
        term = f"%{query}%"
        params.extend([term] * 6)
    for key, column in (("kind", "r.kind"), ("status", "r.status"), ("class_name", "r.class_name"), ("user_id", "r.user_id")):
        value = filters.get(key)
        if value not in (None, "", "الكل"):
            clauses.append(f"{column}=?")
            params.append(value)
    date_from = str(filters.get("date_from", "")).strip()
    date_to = str(filters.get("date_to", "")).strip()
    for value, label in ((date_from, "بداية"), (date_to, "نهاية")):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"تاريخ {label} يجب أن يكون بصيغة YYYY-MM-DD.") from exc
    if date_from and date_to and date_from > date_to:
        raise ValueError("تاريخ البداية يجب ألا يتجاوز تاريخ النهاية.")
    date_expr = "substr(COALESCE(NULLIF(r.issued_at,''),r.created_at),1,10)"
    if date_from:
        clauses.append(f"{date_expr}>=?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{date_expr}<=?")
        params.append(date_to)
    sql = ("SELECT r.*,u.display_name AS supervisor FROM records r "
           "LEFT JOIN users u ON u.id=r.user_id WHERE " + " AND ".join(clauses) + " ORDER BY r.id DESC")
    with db_connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def export_filtered_records_csv(path: str, user_id, filters: dict | None = None) -> int:
    """Export exactly the current filtered result set (UTF-8 BOM; spreadsheet-safe)."""
    user = require_user(user_id)
    rows = query_records(user_id, filters)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["الرقم", "النوع", "التلميذ", "رقم التعريف", "القسم", "تاريخ الإصدار", "المشرف", "السبب", "الحالة", "وقت العودة", "عدد مرات الطباعة"])
        for r in rows:
            writer.writerow([csv_safe(x) for x in (
                r["record_no"], kind_label(r["kind"]), r["student_name"], r["student_key"], r["class_name"],
                r["issued_at"] or r["created_at"], r.get("supervisor") or "—", r["reason"], status_label(r["status"]),
                r["returned_at"], r["printed_count"])])
    audit(user["id"], "تصدير نتائج سجل العمليات المصفاة", "export", Path(path).name, f"{len(rows)} سجلًا")
    return len(rows)


def create_filtered_records_report_pdf(dest_path, filters: dict | None, user_id) -> Path:
    """Create a printable A4-landscape PDF of the filtered records without touching permit artwork."""
    user = require_user(user_id)
    rows = query_records(user_id, filters)
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    out = Path(dest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = landscape(A4)
    font = _font_setup()
    c = canvas.Canvas(str(out), pagesize=(page_w, page_h))
    c.setTitle("SchoolPass — تقرير سجل العمليات")
    c.setAuthor(APP_NAME)
    school = setting("school_name", SCHOOL_NAME)
    cols = [("الرقم", 38*MM), ("النوع", 48*MM), ("التلميذ", 57*MM), ("القسم", 37*MM),
            ("التاريخ والوقت", 37*MM), ("المشرف", 34*MM), ("الحالة", 23*MM)]
    margin = 9*MM
    def header(page_no):
        c.setFillColorRGB(0.06, 0.24, 0.19)
        c.setFont(font, 13)
        c.drawRightString(page_w-margin, page_h-12*MM, _arabic_visual("تقرير سجل العمليات"))
        c.setFillColorRGB(0.28, 0.36, 0.32)
        c.setFont(font, 8)
        c.drawRightString(page_w-margin, page_h-18*MM, _arabic_visual(school))
        c.drawString(margin, page_h-18*MM, f"{datetime.now():%Y-%m-%d %H:%M}  |  {page_no}")
        c.setStrokeColorRGB(0.76, 0.83, 0.79)
        c.line(margin, page_h-22*MM, page_w-margin, page_h-22*MM)
        y = page_h-29*MM
        x = page_w-margin
        c.setFillColorRGB(0.90, 0.95, 0.92)
        c.rect(margin, y-5*MM, page_w-2*margin, 8*MM, stroke=0, fill=1)
        c.setFillColorRGB(0.06, 0.24, 0.19)
        c.setFont(font, 7.2)
        for title, width in cols:
            c.drawRightString(x-2*MM, y-2.1*MM, _arabic_visual(title))
            x -= width
        return y-10*MM
    y = header(1)
    page_no = 1
    for idx, row in enumerate(rows):
        values = [row["record_no"], kind_label(row["kind"]), row["student_name"], row["class_name"],
                  (row["issued_at"] or row["created_at"])[:16], row.get("supervisor") or "—", status_label(row["status"])]
        x = page_w-margin
        if idx % 2 == 0:
            c.setFillColorRGB(0.97, 0.98, 0.97)
            c.rect(margin, y-3*MM, page_w-2*margin, 7*MM, stroke=0, fill=1)
        c.setFillColorRGB(0.12, 0.19, 0.16)
        c.setFont(font, 6.5)
        for value, (_title, width) in zip(values, cols):
            text = str(value or "—").replace("\n", " ")
            while len(text) > 46 and _text_width(text, font, 6.5) > width-4*MM:
                text = text[:-2]
            c.drawRightString(x-2*MM, y, _arabic_visual(text))
            x -= width
        y -= 7*MM
        if y < 15*MM:
            c.setFont(font, 7)
            c.drawRightString(page_w-margin, 8*MM, _arabic_visual(f"عدد السجلات في التقرير: {len(rows)}"))
            c.showPage()
            page_no += 1
            y = header(page_no)
    c.setFont(font, 7)
    c.drawRightString(page_w-margin, 8*MM, _arabic_visual(f"عدد السجلات في التقرير: {len(rows)}"))
    c.save()
    audit(user["id"], "إنشاء تقرير PDF مفلتر", "report", out.name, f"{len(rows)} سجلًا")
    return out

def set_school_name_as(actor, name: str) -> None:
    admin = require_admin(actor)
    name = name.strip()
    if not name:
        raise ValueError("أدخل اسم المؤسسة.")
    set_setting("school_name", name)
    audit(admin["id"], "تغيير اسم المؤسسة", "settings", "school_name", name)


DEFAULT_THEME_PRIMARY = "#185A8D"
DEFAULT_THEME_SECONDARY = "#F2F5F9"


def _validate_hex_color(value: str, label: str) -> str:
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", value):
        raise ValueError(f"{label}: اختر لونًا بصيغة HEX صحيحة مثل #185A8D.")
    return value

def _relative_luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

def contrast_ratio(first: str, second: str) -> float:
    a, b = sorted((_relative_luminance(_validate_hex_color(first, 'اللون')),
                   _relative_luminance(_validate_hex_color(second, 'اللون'))), reverse=True)
    return (a + 0.05) / (b + 0.05)

def text_color_for(background: str) -> str:
    """Return the black/white foreground with the strongest WCAG contrast."""
    bg = _validate_hex_color(background, 'لون الخلفية')
    black, white = '#000000', '#ffffff'
    candidate = black if contrast_ratio(bg, black) >= contrast_ratio(bg, white) else white
    if contrast_ratio(bg, candidate) < 4.5:
        raise ValueError('تعذر ضمان تباين النص مع الخلفية بنسبة 4.5:1 على الأقل.')
    return candidate


def get_theme_colors() -> tuple[str, str]:
    """Return saved UI colors, with safe defaults for old or damaged settings."""
    primary = setting("theme_primary", DEFAULT_THEME_PRIMARY)
    secondary = setting("theme_secondary", DEFAULT_THEME_SECONDARY)
    try:
        primary = _validate_hex_color(primary, "اللون الرئيسي")
    except ValueError:
        primary = DEFAULT_THEME_PRIMARY
    try:
        secondary = _validate_hex_color(secondary, "اللون الثانوي")
    except ValueError:
        secondary = DEFAULT_THEME_SECONDARY
    return primary, secondary


def set_theme_as(actor, primary: str, secondary: str) -> tuple[str, str]:
    """Admin-only theme update. Validates and audits both colors as one operation."""
    admin = require_admin(actor)
    primary = _validate_hex_color(primary, "اللون الرئيسي")
    secondary = _validate_hex_color(secondary, "اللون الثانوي")
    if primary == secondary:
        raise ValueError("اختر لونين مختلفين حتى تبقى عناصر الواجهة واضحة.")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO settings(setting_key,setting_value) VALUES('theme_primary',?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (primary,))
        conn.execute("INSERT INTO settings(setting_key,setting_value) VALUES('theme_secondary',?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (secondary,))
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (now_iso(), admin["id"], "تغيير ألوان الواجهة", "settings", "theme", f"{primary} / {secondary}"))
    return primary, secondary


def set_print_mode_as(actor, mode: str) -> None:
    admin = require_admin(actor)
    if mode != "open":
        raise ValueError("الطباعة المباشرة أُوقفت لتفادي تجاهل مقاس A6؛ يجب معاينة ملف PDF قبل الطباعة.")
    set_setting("print_mode", "open")
    audit(admin["id"], "تثبيت الطباعة عبر معاينة PDF", "settings", "print_mode", "open")


def save_preferences_as(actor, school_name: str, host: str, primary: str, secondary: str,
                        late_alert_threshold: int = 3, external_backup_path: str = "",
                        rtl_shaping_enabled: bool = True) -> None:
    """Validate and save all administrator preferences atomically, with one audit event per setting."""
    admin = require_admin(actor)
    school_name = str(school_name or "").strip()
    host = str(host or "").strip()
    if not school_name:
        raise ValueError("أدخل اسم المؤسسة.")
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,60}[A-Za-z0-9])?", host):
        raise ValueError("أدخل عنوان IP أو اسم حاسوب صالحًا دون http:// أو منفذ.")
    primary = _validate_hex_color(primary, "اللون الرئيسي")
    secondary = _validate_hex_color(secondary, "اللون الثانوي")
    if primary == secondary:
        raise ValueError("اختر لونين مختلفين حتى تبقى عناصر الواجهة واضحة.")
    try:
        late_alert_threshold = int(late_alert_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("حد تنبيه التأخر يجب أن يكون عددًا صحيحًا من 2 إلى 20.") from exc
    if not 2 <= late_alert_threshold <= 20:
        raise ValueError("حد تنبيه التأخر يجب أن يكون بين 2 و20 مرة شهريًا.")
    external_backup_path = str(external_backup_path or "").strip()
    if external_backup_path:
        backup_dir = Path(external_backup_path).expanduser()
        if not backup_dir.exists() or not backup_dir.is_dir():
            raise ValueError("مسار النسخ الخارجي يجب أن يكون مجلدًا موجودًا ويمكن الوصول إليه.")
        resolved_backup = backup_dir.resolve()
        try:
            resolved_backup.relative_to(DATA_DIR.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("اختر USB أو مجلدًا خارج مجلد بيانات SchoolPass؛ النسخ داخل المجلد نفسه لا تحمي من تلف القرص.")
        external_backup_path = str(resolved_backup)
    changes = {
        "school_name": school_name,
        "qr_host": host,
        "theme_primary": primary,
        "theme_secondary": secondary,
        "print_mode": "open",
        "late_alert_threshold": str(late_alert_threshold),
        "external_backup_path": external_backup_path,
        "rtl_shaping": "1" if rtl_shaping_enabled else "0",
    }
    labels = {
        "school_name": ("تغيير اسم المؤسسة", "اسم المؤسسة"),
        "qr_host": ("تغيير عنوان رمز QR", "عنوان QR"),
        "theme_primary": ("تغيير ألوان الواجهة", "اللون الرئيسي"),
        "theme_secondary": ("تغيير ألوان الواجهة", "اللون الثانوي"),
        "print_mode": ("تثبيت الطباعة عبر معاينة PDF", "طريقة الطباعة"),
        "late_alert_threshold": ("تغيير حد إنذار التأخر", "مرات التأخر شهريًا"),
        "external_backup_path": ("تغيير مسار النسخ الخارجي", "مسار النسخ الخارجي"),
        "rtl_shaping": ("تغيير عرض العربية", "تشكيل النصوص العربية"),
    }
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for key, value in changes.items():
            conn.execute("INSERT INTO settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (key, value))
            action, desc = labels[key]
            conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                         (now_iso(), admin["id"], action, "settings", key, f"{desc}: {value}"))


# ----------------------------------------------------------------------------- QR host / local verification server
def qr_host() -> str:
    """Address written into printed QR codes. Saved once so old printouts keep working."""
    return setting("qr_host", "").strip() or get_local_ip()


def ensure_qr_host_saved() -> str:
    host = setting("qr_host", "").strip()
    if not host:
        ip = get_local_ip()
        if not ip.startswith("127."):
            set_setting("qr_host", ip)
            host = ip
    return host or get_local_ip()


def qr_host_mismatch() -> tuple[str, str] | None:
    """(configured, current) when the saved IP is no longer this computer's IP; old QR codes would break."""
    configured, current = setting("qr_host", "").strip(), get_local_ip()
    if configured and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", configured) and current != configured and not current.startswith("127."):
        return configured, current
    return None


def set_qr_host_as(actor, host: str) -> None:
    admin = require_admin(actor)
    host = host.strip()
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,60}[A-Za-z0-9])?", host):
        raise ValueError("أدخل عنوان IP (مثل 192.168.1.20) أو اسم حاسوب صالحًا دون http:// أو منفذ.")
    set_setting("qr_host", host)
    audit(admin["id"], "تغيير عنوان رمز QR", "settings", "qr_host", host)


def qr_url(token: str) -> str:
    return f"http://{qr_host()}:{SERVER_PORT}/v/{token}"


_TOKEN_RE = re.compile(r"^[0-9a-f]{48}$")
_RATE: dict[str, list[float]] = {}
_RATE_LOCK = threading.Lock()


def _rate_limited(ip: str, limit: int = 20, window: float = 60.0) -> bool:
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(ip, []) if now - t < window]
        hits.append(now)
        _RATE[ip] = hits
        if len(_RATE) > 500:
            _RATE.clear()
        return len(hits) > limit


def verification_page(token: str) -> tuple[int, bytes, dict | None]:
    """Build the phone verification page. Never includes national ID or guardian data."""
    row = None
    if _TOKEN_RE.match(token or ""):
        with db_connect() as conn:
            row = conn.execute("SELECT record_no,kind,student_name,class_name,status,created_at,cancelled_at FROM records WHERE qr_token=?",
                               (token,)).fetchone()
    school = html.escape(setting("school_name", SCHOOL_NAME))
    if not row:
        title, body = "رمز غير معروف", "<p>لم يتم العثور على هذا السجل في قاعدة بيانات SchoolPass.</p>"
    else:
        title = "التحقق من الرخصة"
        body = (f'<p><b>الرقم:</b> {html.escape(row["record_no"])}</p><p><b>النوع:</b> {html.escape(kind_label(row["kind"]))}</p>'
                f'<p><b>التلميذ:</b> {html.escape(row["student_name"])}</p><p><b>القسم:</b> {html.escape(row["class_name"])}</p>'
                f'<p><b>التاريخ:</b> {html.escape(row["created_at"])}</p>'
                f'<p><b>الحالة:</b> <strong>{html.escape(status_label(row["status"]))}</strong></p>')
        if row["status"] == "cancelled":
            body += f'<p class="bad">تنبيه: هذا السجل ملغى منذ {html.escape(row["cancelled_at"] or "")} ولا يجوز الاعتماد عليه.</p>'
        elif row["status"] == "returned":
            body += "<p>سُجّلت عودة التلميذ بموجب هذا التصريح.</p>"
        else:
            body += "<p>السجل سارٍ. طابق الاسم والقسم مع التلميذ الحاضر.</p>"
    page = (f'<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>body{{font-family:Tahoma,Arial,sans-serif;background:#f4f7fb;margin:0;padding:22px;color:#17243a}}'
            f'main{{max-width:480px;margin:auto;background:#fff;border-radius:14px;padding:22px}}h2{{color:#174a77}}p{{padding:9px 0;border-bottom:1px solid #edf0f5}}'
            f'.bad{{color:#b00020;font-weight:bold}}</style><main><h2>{school}</h2><h3>{html.escape(title)}</h3>{body}'
            f'<p>صفحة تحقق محلية؛ لا تتضمن رقم بطاقة الهوية.</p></main></html>').encode("utf-8")
    return (200 if row else 404), page, ({"qr_token": token} if row else None)


def ensure_local_server(port: int | None = None) -> tuple[bool, str]:
    global _SERVER, _SERVER_THREAD
    if _SERVER is not None:
        return True, get_local_ip()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SchoolPass"
        sys_version = ""

        def log_message(self, *_args):
            return

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self._send(200, b"SchoolPass OK", "text/plain; charset=utf-8")
            if not path.startswith("/v/"):
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            if _rate_limited(self.client_address[0]):
                return self._send(429, b"Too many requests", "text/plain; charset=utf-8")
            code, page, ev = verification_page(path[3:].strip("/"))
            if ev:
                try:
                    EVENT_QUEUE.put_nowait(ev["qr_token"])
                except queue.Full:
                    pass
            self._send(code, page, "text/html; charset=utf-8")

    try:
        _SERVER = ThreadingHTTPServer(("0.0.0.0", SERVER_PORT if port is None else port), Handler)
        # Wait for request-handler threads during shutdown so tests and restore
        # operations never race a handler that still has SQLite open.
        _SERVER.daemon_threads = False
        _SERVER.block_on_close = True
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True)
        _SERVER_THREAD.start()
        return True, get_local_ip()
    except OSError as exc:
        _SERVER = _SERVER_THREAD = None
        return False, str(exc)


def stop_local_server() -> bool:
    global _SERVER, _SERVER_THREAD
    server, thread = _SERVER, _SERVER_THREAD
    was = server is not None
    # Detach globals first: a concurrent UI action cannot mistake a server
    # being shut down for a healthy listener.
    _SERVER = _SERVER_THREAD = None
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()  # waits for non-daemon request handlers
        except Exception:
            pass
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=10)
    return was


# ----------------------------------------------------------------------------- A6 PDF
MM = 72.0 / 25.4
PAGE_W, PAGE_H = 105 * MM, 148 * MM  # A6 portrait
_FONT_NAME = "SchoolPassArabic"


def _font_setup() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if _FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return _FONT_NAME
    win = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [os.environ.get("SCHOOLPASS_FONT", ""), os.path.join(win, "Fonts", "tahoma.ttf"), os.path.join(win, "Fonts", "arial.ttf"),
                  os.path.join(win, "Fonts", "segoeui.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, c))
                return _FONT_NAME
            except Exception:
                continue
    raise RuntimeError("لم أجد خطًا عربيًا (Tahoma/Arial) في Windows لإنشاء الرخصة. لن تُطبع رخصة بحروف غير صحيحة.")


def _arabic_visual(text: str) -> str:
    import arabic_reshaper  # raises ImportError if missing: better than printing broken Arabic
    try:
        from bidi.algorithm import get_display
    except ImportError:  # pragma: no cover
        from bidi import get_display
    return get_display(arabic_reshaper.reshape(str(text)), base_dir="R")


def _text_width(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase import pdfmetrics
    return pdfmetrics.stringWidth(_arabic_visual(text), font, size)


def _wrap_to_width(text: str, font: str, size: float, max_w: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in str(text or "—").split():
        candidate = f"{current} {word}".strip()
        if current and _text_width(candidate, font, size) > max_w:
            lines.append(current)
            current = word
        else:
            current = candidate
        while _text_width(current, font, size) > max_w and len(current) > 1:  # single over-long word
            cut = len(current)
            while cut > 1 and _text_width(current[:cut], font, size) > max_w:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines or ["—"]


_WARN_GATE = ("تعليمات الحجابة — نسخة خاصة بالحاجب. يجب على الحاجب الاحتفاظ بهذه النسخة في سجل الحجابة باعتبارها إثباتًا "
              "لترخيص خروج التلميذ المعني من المؤسسة. لا تُسلَّم هذه النسخة إلى التلميذ، ويُرجع إليها عند الحاجة للتحقق من التصريح.")
_WARN_STUDENT = ("تنبيه مهم للتلميذ. يجب الاحتفاظ بهذه الرخصة وتقديمها عند العودة إلى المؤسسة للحصول على إثبات العودة. "
                 "لا يُشترط حضور ولي الأمر مرة أخرى ما دام التصريح ساريًا ولم يُلغَ.")


def _academic_year(value: datetime | None = None) -> str:
    """Return the Algerian school year label for the local issue date."""
    d = value or datetime.now()
    start = d.year if d.month >= 9 else d.year - 1
    return f"{start}/{start + 1}"


def _draw_entry_page(c, row, details: dict, reprint: bool, font: str, school: str, reprint_at: str = "") -> None:
    """Premium A6 entry permit; follows the approved exit-permit visual language.

    This renderer is intentionally isolated from the exit-permit renderer below:
    the approved exceptional-exit design remains byte-for-byte untouched.
    """
    from reportlab.lib.utils import ImageReader

    W, H = PAGE_W, PAGE_H
    mm = MM
    margin = 6.2 * mm
    right = W - margin
    usable = W - 2 * margin
    cancelled = row["status"] == "cancelled"
    is_absence = row["kind"] == "absence_return"
    issued = str(row["issued_at"] if "issued_at" in row.keys() and row["issued_at"] else row["created_at"])
    try:
        stamp = datetime.strptime(issued[:19], "%Y-%m-%d %H:%M:%S")
        issue_date = stamp.strftime("%d/%m/%Y")
        issue_time = stamp.strftime("%H:%M")
        year_label = _academic_year(stamp)
    except Exception:
        issue_date, issue_time, year_label = issued, "—", _academic_year()

    title = "رخصة الدخول"
    subtitle = "تأخر عن الدخول" if row["kind"] == "late" else (
        "عودة إلى المؤسسة بعد الغياب" if is_absence else "إثبات عودة من خروج استثنائي"
    )
    signature = hashlib.sha256(str(row["qr_token"]).encode("utf-8")).hexdigest()[:12].upper()
    issuer = "الإدارة"
    if row["user_id"] is not None:
        user = get_user(row["user_id"])
        if user:
            issuer = user["display_name"]

    # Deep institutional green and soft green-grey surfaces match the app's
    # current visual identity while preserving monochrome print legibility.
    ink = (0.055, 0.18, 0.15)
    green = (0.07, 0.31, 0.25)
    pale = (0.94, 0.97, 0.95)
    line = (0.68, 0.76, 0.72)
    dark_muted = (0.28, 0.37, 0.33)

    c.saveState()
    c.setStrokeColorRGB(*green)
    c.setLineWidth(0.8)
    c.roundRect(3.5 * mm, 3.5 * mm, W - 7 * mm, H - 7 * mm, 2.1 * mm, stroke=1, fill=0)

    # Header block.
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 7.2)
    c.drawCentredString(W / 2, H - 8.4 * mm, _arabic_visual(MINISTRY_NAME))
    c.setFillColorRGB(*green)
    c.setFont(font, 8.6)
    c.drawCentredString(W / 2, H - 13.3 * mm, _arabic_visual(school))
    c.setStrokeColorRGB(*line)
    c.setLineWidth(0.45)
    c.line(margin + 1.5 * mm, H - 17.0 * mm, right - 1.5 * mm, H - 17.0 * mm)

    # Main title banner.
    title_y = H - 26.0 * mm
    c.setFillColorRGB(*green)
    c.roundRect(margin, title_y - 3.2 * mm, usable, 9.1 * mm, 1.5 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 12.2)
    c.drawCentredString(W / 2, title_y, _arabic_visual(title))
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 7.6)
    c.drawCentredString(W / 2, H - 32.2 * mm, _arabic_visual(subtitle))

    # Reference and issue date strip.
    strip_y = H - 40.0 * mm
    c.setFillColorRGB(*pale)
    c.roundRect(margin + 0.6 * mm, strip_y - 2.4 * mm, usable - 1.2 * mm, 6.0 * mm, 0.8 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*ink)
    c.setFont(font, 7.6)
    c.drawRightString(right - 2.0 * mm, strip_y, _arabic_visual(f"رقم الرخصة: {row['record_no']}"))
    c.drawString(margin + 2.0 * mm, strip_y, _arabic_visual(f"التاريخ: {issue_date}"))
    if cancelled:
        c.setFillColorRGB(0.72, 0.05, 0.08)
        c.setFont(font, 7.0)
        c.drawCentredString(W / 2, H - 43.7 * mm, _arabic_visual("ملغاة — لا يُسمح باستعمالها"))
    elif reprint:
        c.setFillColorRGB(0.56, 0.34, 0.06)
        c.setFont(font, 6.0)
        c.drawCentredString(W / 2, H - 43.7 * mm, _arabic_visual("نسخة معاد طباعتها: " + (reprint_at or now_iso())))

    # Prominent student card: this is the main visual anchor of the entry permit.
    student_top = H - 47.0 * mm
    student_bottom = student_top - 13.0 * mm
    c.setFillColorRGB(0.965, 0.978, 0.970)
    c.roundRect(margin, student_bottom, usable, 13.0 * mm, 1.3 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 6.8)
    c.drawRightString(right - 2.2 * mm, student_top - 3.8 * mm, _arabic_visual("اسم التلميذ"))
    c.setFillColorRGB(*ink)
    c.setFont(font, 12.0)
    name_lines = _wrap_to_width(str(row["student_name"] or "—"), font, 12.0, usable - 5.0 * mm)
    if len(name_lines) == 1:
        c.drawRightString(right - 2.2 * mm, student_bottom + 2.4 * mm, _arabic_visual(name_lines[0]))
    else:
        c.setFont(font, 9.8)
        c.drawRightString(right - 2.2 * mm, student_bottom + 1.8 * mm, _arabic_visual(name_lines[0]))
        c.drawRightString(right - 2.2 * mm, student_bottom + 5.4 * mm, _arabic_visual(name_lines[1]))

    # Two equal information tiles; entry time is deliberately larger and dominant.
    tile_y = student_bottom - 3.0 * mm
    tile_h = 13.0 * mm
    gap = 2.0 * mm
    tile_w = (usable - gap) / 2
    left_x = margin
    right_x = margin + tile_w + gap
    for x in (left_x, right_x):
        c.setFillColorRGB(*pale)
        c.roundRect(x, tile_y - tile_h, tile_w, tile_h, 1.0 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 7.0)
    c.drawCentredString(right_x + tile_w / 2, tile_y - 3.3 * mm, _arabic_visual("وقت الدخول"))
    c.setFillColorRGB(*green)
    c.setFont(font, 13.0)
    c.drawCentredString(right_x + tile_w / 2, tile_y - 9.0 * mm, _arabic_visual(issue_time))
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 7.0)
    c.drawCentredString(left_x + tile_w / 2, tile_y - 3.3 * mm, _arabic_visual("القسم"))
    c.setFillColorRGB(*ink)
    c.setFont(font, 8.8)
    c.drawCentredString(left_x + tile_w / 2, tile_y - 9.0 * mm, _arabic_visual(str(row["class_name"] or "—")))

    # Compact but readable data grid. Paired cells keep the return-after-absence
    # information complete without shrinking the type to the old tiny layout.
    data_top = tile_y - tile_h - 3.0 * mm
    data_bottom = 36.0 * mm
    rows: list[tuple[str, str, str, str]] = []
    if is_absence:
        rows.append(("بداية الغياب", str(details.get("absence_start", "—")),
                     "تاريخ العودة", str(details.get("return_date", issue_date))))
        rows.append(("مدة الغياب", f"{details.get('days_count', '—')} يومًا",
                     "طريقة التبرير", "حضور ولي الأمر" if details.get("proof_method") == "guardian" else "شهادة طبية"))
        if details.get("proof_method") == "guardian":
            guardian = str(details.get("guardian_name", "—"))
            relation = str(details.get("relationship", "—"))
            rows.append(("بيانات ولي الأمر", f"{guardian} — {relation}", "", ""))
            rows.append(("رقم الهوية", mask_national_id(details), "تأكيد الحضور", str(details.get("guardian_verified_at", "—"))[:16]))
        else:
            rows.append(("حالة الشهادة", "تم الاستلام والتحقق", "أصدرها", issuer))
            rows.append(("ملاحظة", str(row["reason"] or "—"), "", ""))
    else:
        try:
            session_label = "صباحية" if int(issue_time.split(":")[0]) < 12 else "مسائية"
        except (ValueError, IndexError):
            session_label = "—"
        rows.append(("سبب التأخر", str(row["reason"] or "تأخر عن الدخول"), "", ""))
        rows.append(("الفترة", session_label, "أصدرها", issuer))

    available_h = max(0.0, data_top - data_bottom)
    row_h = min(7.0 * mm, available_h / max(1, len(rows)))
    if row_h < 5.0 * mm:
        raise ValueError("بيانات رخصة الدخول لا تتسع بوضوح في مقاس A6؛ راجع طول اسم ولي الأمر أو بيانات الرخصة.")
    col_gap = 2.0 * mm
    col_w = (usable - col_gap) / 2
    # We draw a pair of label/value cells on each row, value aligned left in its cell.
    for idx, (label_a, value_a, label_b, value_b) in enumerate(rows):
        y_top = data_top - idx * row_h
        y_bottom = y_top - row_h + 0.4 * mm
        c.setFillColorRGB(0.975, 0.983, 0.978) if idx % 2 == 0 else c.setFillColorRGB(1, 1, 1)
        c.roundRect(margin, y_bottom, usable, row_h - 0.6 * mm, 0.7 * mm, stroke=0, fill=1)
        c.setFillColorRGB(*dark_muted)
        baseline = y_top - 2.35 * mm
        label_size = 6.2 if row_h <= 5.2 * mm else 6.8
        value_size = 6.6 if row_h <= 5.2 * mm else 7.6
        if not label_b:
            # Full-width field for long reason / guardian details; reduce the
            # font slightly rather than silently dropping the end of a value.
            c.setFillColorRGB(*dark_muted)
            c.setFont(font, label_size)
            c.drawRightString(right - 1.8 * mm, baseline, _arabic_visual(label_a + ":"))
            value_font = 7.0 if row_h > 5.2 * mm else 6.1
            max_value_w = usable - 24 * mm
            while value_font > 5.2 and _text_width(value_a, font, value_font) > max_value_w:
                value_font -= 0.2
            c.setFillColorRGB(*ink)
            c.setFont(font, value_font)
            c.drawRightString(right - 22.0 * mm, baseline, _arabic_visual(value_a))
        else:
            # Two paired data cells: right and left, each with a right-aligned label.
            c.setFillColorRGB(*dark_muted)
            c.setFont(font, label_size)
            c.drawRightString(right - 1.8 * mm, baseline, _arabic_visual(label_a + ":"))
            c.setFillColorRGB(*ink)
            value_a_font = value_size
            while value_a_font > 5.2 and _text_width(value_a, font, value_a_font) > col_w - 18 * mm:
                value_a_font -= 0.2
            c.setFont(font, value_a_font)
            c.drawRightString(right - 18.0 * mm, baseline, _arabic_visual(value_a))
            left_cell_right = margin + col_w
            c.setFillColorRGB(*dark_muted)
            c.setFont(font, label_size)
            c.drawRightString(left_cell_right - 1.8 * mm, baseline, _arabic_visual(label_b + ":"))
            c.setFillColorRGB(*ink)
            value_b_font = value_size
            while value_b_font > 5.2 and _text_width(value_b, font, value_b_font) > col_w - 18 * mm:
                value_b_font -= 0.2
            c.setFont(font, value_b_font)
            c.drawRightString(left_cell_right - 18.0 * mm, baseline, _arabic_visual(value_b))

    # QR and signature/stamp area intentionally matches the approved exit slip.
    qr_x, qr_y, qr_size = margin + 1.0 * mm, 8.0 * mm, 23.0 * mm
    import qrcode
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
    qr.add_data(qr_url(row["qr_token"]))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_size, height=qr_size,
                preserveAspectRatio=True, mask="auto")
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 5.1)
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 2.0 * mm, _arabic_visual("امسح للتحقق"))

    sx, sy = W - margin - 34 * mm, 9.0 * mm
    c.setStrokeColorRGB(*green)
    c.setLineWidth(0.6)
    c.roundRect(sx, sy, 32 * mm, 22 * mm, 1.2 * mm, stroke=1, fill=0)
    c.setFillColorRGB(*green)
    c.setFont(font, 6.3)
    c.drawCentredString(sx + 16 * mm, sy + 16.5 * mm, _arabic_visual("توقيع الإدارة"))
    c.setStrokeColorRGB(*line)
    c.line(sx + 3 * mm, sy + 6.7 * mm, sx + 29 * mm, sy + 6.7 * mm)
    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 5.4)
    c.drawCentredString(sx + 16 * mm, sy + 2.8 * mm, _arabic_visual("ختم المؤسسة"))

    c.setFillColorRGB(*dark_muted)
    c.setFont(font, 5.0)
    c.drawRightString(right, 3.5 * mm, _arabic_visual(f"توقيع رقمي: {signature}"))
    if cancelled:
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(35)
        try:
            c.setFillAlpha(0.16)
        except Exception:
            pass
        c.setFillColorRGB(0.72, 0.05, 0.08)
        c.setFont(font, 48)
        c.drawCentredString(0, 0, _arabic_visual("ملغاة"))
        c.restoreState()
    c.restoreState()


def _draw_page(c, row, details: dict, copy_type: str | None, reprint: bool, font: str, school: str, reprint_at: str = "") -> None:
    if row["kind"] in ("late", "absence_return", "exit_return"):
        _draw_entry_page(c, row, details, reprint, font, school, reprint_at)
        return
    from reportlab.lib.utils import ImageReader
    W, H = PAGE_W, PAGE_H
    margin = 6.5 * MM
    x_right, usable = W - margin, W - 2 * margin
    cancelled = row["status"] == "cancelled"

    # Refined institutional A6 layout: framed page, clear hierarchy and a strong title band.
    c.saveState()
    c.setStrokeColorRGB(0.08, 0.29, 0.48)
    c.setLineWidth(0.75)
    c.roundRect(3.5 * MM, 3.5 * MM, W - 7 * MM, H - 7 * MM, 2.2 * MM, stroke=1, fill=0)
    c.setFillColorRGB(0.965, 0.978, 0.992)
    c.roundRect(margin, H - 27 * MM, usable, 19 * MM, 1.8 * MM, stroke=0, fill=1)
    c.setFillColorRGB(0.05, 0.22, 0.39)
    c.setFont(font, 7.0)
    c.drawCentredString(W / 2, H - 11 * MM, _arabic_visual(MINISTRY_NAME))
    c.setFont(font, 8.1)
    c.drawCentredString(W / 2, H - 16 * MM, _arabic_visual(school))
    c.setStrokeColorRGB(0.12, 0.39, 0.62)
    c.setLineWidth(0.65)
    c.line(margin + 2 * MM, H - 19.2 * MM, W - margin - 2 * MM, H - 19.2 * MM)
    # Main title banner
    c.setFillColorRGB(0.055, 0.31, 0.53)
    c.roundRect(margin + 2 * MM, H - 36.5 * MM, usable - 4 * MM, 8.2 * MM, 1.7 * MM, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 10.0)
    c.drawCentredString(W / 2, H - 33.6 * MM, _arabic_visual(kind_label(row["kind"])))
    c.restoreState()
    y = H - 41.5 * MM

    # --- lines: (text, size, gap_mm, red?)
    items: list[tuple[str, float, float, bool]] = []
    if cancelled:
        items.append(("ملغاة — لا يُعتمد", 8.6, 4.4, True))
        if row["cancelled_reason"]:
            items.append((f"سبب الإلغاء: {row['cancelled_reason']}", 6.6, 3.5, True))
    else:
        items.append((f"حالة السجل: {status_label(row['status'])}", 7.0, 3.9, False))
    if reprint:
        n = int(row["printed_count"] or 0)
        items.append((f"نسخة معاد طباعتها (الطباعة رقم {n + 1}) بتاريخ {reprint_at or now_iso()}", 6.2, 3.6, False))
    issued_at = row["issued_at"] if "issued_at" in row.keys() and row["issued_at"] else row["created_at"]
    items += [(f"الرقم المرجعي: {row['record_no']}", 8.2, 4.4, False), (f"التلميذ: {row['student_name']}", 8.2, 4.4, False),
              (f"القسم: {row['class_name']}", 7.8, 4.2, False), (f"تاريخ ووقت الإصدار: {issued_at}", 7.2, 4.1, False)]
    audit_user = None
    if row["user_id"] is not None:
        u = get_user(row["user_id"])
        audit_user = u["display_name"] if u else None
    if row["reason"]:
        items.append((f"السبب: {row['reason']}", 7.2, 4.1, False))
    if row["kind"] == "absence_return":
        items.append((f"بداية الغياب: {details.get('absence_start', '—')}", 7.2, 4.1, False))
        items.append((f"تاريخ العودة: {details.get('return_date', '—')} — أيام الغياب قبل يوم العودة: {details.get('days_count', '—')} يومًا", 7.0, 4.1, False))
        if details.get("proof_method") == "guardian":
            items.append((f"طريقة التبرير: حضور ولي الأمر شخصيًا", 7.0, 4.0, False))
            items.append((f"ولي الأمر: {details.get('guardian_name', '—')} — الصلة: {details.get('relationship', '—')}", 7.0, 4.0, False))
            items.append((f"رقم الهوية: {mask_national_id(details)}", 7.0, 4.0, False))
            items.append((f"تأكيد الحضور: {details.get('guardian_verified_at', '—')}", 6.5, 3.7, False))
        else:
            items.append(("طريقة التبرير: شهادة طبية (مدة الغياب لا تتجاوز يومين)", 6.8, 4.0, False))
    if row["kind"] == "exit_return":
        items.append((f"مرجع رخصة الخروج: {details.get('permit_no', '—')}", 7.2, 4.1, False))
    if row["kind"] == "exit_permit":
        items.append((f"ولي الأمر: {details.get('guardian_name', '—')}", 7.2, 4.1, False))
        items.append((f"صلة القرابة: {details.get('relationship', '—')}", 7.2, 4.1, False))
        items.append((f"رقم الهوية: {mask_national_id(details)}", 7.2, 4.1, False))
    if audit_user:
        items.append((f"المشرف: {audit_user}", 7.2, 4.1, False))
    if copy_type:
        items.append((f"النسخة: {copy_type}", 6.6, 3.8, False))

    # --- bottom zones
    qr_x, qr_y, qr_size = margin, 8 * MM, 24 * MM
    box_bottom = 36 * MM  # warning box (permits) sits above QR/stamp
    warn_lines: list[str] = []
    warn_gap = 3.15 * MM
    if row["kind"] == "exit_permit":
        warn_lines = _wrap_to_width(_WARN_GATE if copy_type == "الحاجب" else _WARN_STUDENT, font, 6.9, usable - 3 * MM)
        content_floor = box_bottom + len(warn_lines) * warn_gap + 3.5 * MM
    else:
        content_floor = 36 * MM

    # --- wrap each item to width, then fit vertically (shrink a little, truncate only the free-text reason)
    def build(scale: float, max_reason_lines: int):
        out = []
        for text, size, gap, red in items:
            s = size * scale * 1.12
            lines = _wrap_to_width(text, font, s, usable)
            if text.startswith(("السبب:", "سبب الإلغاء:")) and len(lines) > max_reason_lines:
                lines = lines[:max_reason_lines]
                lines[-1] = lines[-1].rstrip() + " …"
            out += [(ln, s, gap * scale * 1.12, red) for ln in lines]
        return out
    built = build(1.0, 3)
    for scale, mrl in ((1.0, 3), (0.92, 3), (0.85, 2), (0.8, 2), (0.8, 1)):
        built = build(scale, mrl)
        if y - sum(g for _, _, g, _ in built) * MM >= content_floor:
            break
    else:
        raise ValueError("بيانات الرخصة طويلة جدًا ولا تتسع في ورقة A6؛ اختصر السبب أو اسم ولي الأمر.")
    for idx, (text, size, gap, red) in enumerate(built):
        # Soft alternating information rows improve scanning without wasting A6 space.
        row_h = max(3.0 * MM, gap * MM * 0.82)
        if idx % 2 == 0 and not red:
            c.setFillColorRGB(0.955, 0.972, 0.987)
            c.roundRect(margin + 0.7 * MM, y - 1.45 * MM, usable - 1.4 * MM, row_h,
                        0.7 * MM, stroke=0, fill=1)
        c.setFillColorRGB(0.72, 0.05, 0.08) if red else c.setFillColorRGB(0.055, 0.16, 0.27)
        c.setFont(font, size)
        c.drawRightString(x_right - 1.4 * MM, y, _arabic_visual(text))
        y -= gap * MM
    c.setFillColorRGB(0, 0, 0)

    # --- QR (random token URL only) — failure must stop printing, never print a permit without QR
    import qrcode
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
    qr.add_data(qr_url(row["qr_token"]))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True, mask="auto")
    c.setFont(font, 5.6)
    c.drawString(qr_x, qr_y - 2.6 * MM, "SchoolPass " + row["record_no"])

    # --- stamp box
    sx, sy = W - margin - 32 * MM, 9 * MM
    c.setStrokeColorRGB(0.22, 0.48, 0.70)
    c.setLineWidth(0.55)
    c.roundRect(sx, sy, 30 * MM, 22 * MM, 1.2 * MM, stroke=1, fill=0)
    c.setFillColorRGB(0.08, 0.25, 0.42)
    c.setFont(font, 6.2)
    c.drawCentredString(sx + 15 * MM, sy + 16.5 * MM, _arabic_visual("ختم المؤسسة"))
    c.setStrokeColorRGB(0.55, 0.62, 0.69)
    c.line(sx + 3 * MM, sy + 6 * MM, sx + 27 * MM, sy + 6 * MM)
    c.setFont(font, 5.3)
    c.drawCentredString(sx + 15 * MM, sy + 2.7 * MM, _arabic_visual("إمضاء المسؤول"))
    c.setFillColorRGB(0, 0, 0)

    # --- notice box (exit permit)
    if warn_lines:
        c.setFont(font, 6.9)
        top = box_bottom + len(warn_lines) * warn_gap + 1.5 * MM
        c.setLineWidth(0.35)
        c.roundRect(margin, box_bottom, usable, top - box_bottom + 1.8 * MM, 1.2 * MM, stroke=1, fill=0)
        wy = top - 1.0 * MM
        for ln in warn_lines:
            c.drawRightString(x_right - 1.4 * MM, wy - 1.6 * MM, _arabic_visual(ln))
            wy -= warn_gap
    else:
        c.setFont(font, 6.2)
        c.drawRightString(x_right, 3.5 * MM, _arabic_visual("تُحفظ الرخصة ضمن سجل المؤسسة عند الحاجة."))

    if cancelled:  # big diagonal watermark, visible even in black & white
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(35)
        try:
            c.setFillAlpha(0.16)
        except Exception:
            pass
        c.setFillColorRGB(0.72, 0.05, 0.08)
        c.setFont(font, 54)
        c.drawCentredString(0, 0, _arabic_visual("ملغى"))
        c.restoreState()


def create_pdf(record_id: int, copy_types: list[str | None] | None = None, reprint: bool | None = None, reprint_at: str = "") -> Path:
    """One A6 (105x148 mm) PDF; an exit permit yields TWO pages (gate copy + student copy), same number & QR."""
    from reportlab.pdfgen import canvas
    row = record_by_id(record_id)
    if not row:
        raise ValueError("السجل غير موجود.")
    details = json.loads(row["details_json"] or "{}")
    if copy_types is None:
        copy_types = ["الحاجب", "التلميذ"] if row["kind"] == "exit_permit" else [None]
    if reprint is None:
        reprint = int(row["printed_count"] or 0) > 0
    font, school = _font_setup(), setting("school_name", SCHOOL_NAME)
    out = DATA_DIR / "prints" / f"{row['record_no']}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf"
    c = canvas.Canvas(str(out), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"{row['record_no']}")
    c.setAuthor(APP_NAME)
    for ct in copy_types:
        _draw_page(c, row, details, ct, reprint, font, school, reprint_at)
        c.showPage()
    c.save()
    return out


def print_record(record_id: int, user_id, launch: bool = True) -> list[Path]:
    """Create the PDF (re-print = same record, never a new number) and optionally hand it to Windows."""
    user = require_user(user_id)
    row = record_by_id(record_id)
    if not row:
        raise ValueError("السجل غير موجود.")
    with db_connect() as conn:
        latest = conn.execute("SELECT record_no,printed_count FROM records WHERE id=?", (record_id,)).fetchone()
    if not latest:
        raise ValueError("السجل غير موجود.")
    is_reprint = int(latest["printed_count"] or 0) > 0
    print_at = now_iso()
    path = create_pdf(record_id, reprint=is_reprint, reprint_at=print_at if is_reprint else "")
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute("SELECT record_no,printed_count FROM records WHERE id=?", (record_id,)).fetchone()
        if not latest:
            raise ValueError("السجل غير موجود.")
        # If another workstation printed concurrently, preserve the record but expose that this is a reprint.
        is_reprint_now = int(latest["printed_count"] or 0) > 0
        changed = conn.execute("UPDATE records SET printed_count=printed_count+1 WHERE id=?", (record_id,)).rowcount
        if changed != 1:
            raise RuntimeError("تعذر تحديث عدد مرات الطباعة.")
        conn.execute("INSERT INTO audit_logs(happened_at,user_id,action,target_type,target_id,description) VALUES(?,?,?,?,?,?)",
                     (print_at, user["id"], "إعادة طباعة رخصة" if is_reprint_now else "طباعة رخصة", "record", str(record_id), latest["record_no"]))
    if launch:
        open_pdf(path)
    return [path]


def open_pdf(path: Path) -> bool:
    """Open the PDF for review; direct/silent printing is deliberately disabled for A6 safety."""
    if not hasattr(os, "startfile"):
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def purge_old_prints(days: int = 30) -> int:
    """PDFs hold student names; delete old ones (re-printing regenerates them from the database)."""
    cutoff, n = time.time() - days * 86400, 0
    for p in (DATA_DIR / "prints").glob("*.pdf"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


# ----------------------------------------------------------------------------- backup / restore
_REQUIRED_COLUMNS = {
    "users": {"id", "username", "display_name", "role", "salt", "password_hash", "active", "failed_count", "lock_until", "created_at"},
    "students": {"id", "student_key", "last_name", "first_name", "gender", "year", "branch", "section", "class_name", "active", "imported_at"},
    "records": {"id", "record_no", "qr_token", "kind", "student_key", "student_name", "class_name", "created_at", "issued_at", "user_id", "reason", "status",
                "details_json", "related_record_id", "cancelled_reason", "cancelled_at", "cancelled_by", "returned_at", "returned_by", "printed_count"},
    "audit_logs": {"id", "happened_at", "user_id", "action", "target_type", "target_id", "description"},
    "settings": {"setting_key", "setting_value"},
}


def _migrate_staged_database(db_path) -> None:
    """Apply non-destructive additive migrations to a COPY of any 0.3/0.4/0.5 database."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    # The migration helpers use named columns; keep the same row contract as db_connect().
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        if "issued_at" not in columns:
            conn.execute("ALTER TABLE records ADD COLUMN issued_at TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE records SET issued_at=created_at WHERE TRIM(COALESCE(issued_at,''))=''")
        prior_version_row = conn.execute("SELECT setting_value FROM settings WHERE setting_key='schema_version'").fetchone()
        prior_version = str(prior_version_row[0]) if prior_version_row else "0"
        conn.execute("DROP TRIGGER IF EXISTS trg_records_immutable")
        _migrate_absence_day_counts(conn, prior_version)
        conn.execute("""CREATE TRIGGER trg_records_immutable
            BEFORE UPDATE OF record_no, qr_token, kind, student_key, student_name, class_name, created_at, issued_at, user_id,
                             reason, details_json, related_record_id ON records
            BEGIN SELECT RAISE(ABORT, 'record content is immutable'); END""")
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('school_name',?)", (SCHOOL_NAME,))
        conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES('secret',?)", (secrets.token_hex(32),))
        defaults = {"theme_primary": DEFAULT_THEME_PRIMARY, "theme_secondary": DEFAULT_THEME_SECONDARY,
                    "late_alert_threshold": "3", "rtl_shaping": "1", "external_backup_path": "",
                    "print_mode": "open", "schema_version": SCHEMA_VERSION}
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(setting_key,setting_value) VALUES(?,?)", (key, value))
        conn.execute("UPDATE settings SET setting_value=? WHERE setting_key='schema_version'", (SCHEMA_VERSION,))
        conn.execute("UPDATE settings SET setting_value='open' WHERE setting_key='print_mode' AND setting_value='direct'")
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"فشل فحص قاعدة البيانات بعد الترقية: {integrity}")
    finally:
        conn.close()


def validate_database(db_path) -> None:
    check = sqlite3.connect(str(db_path), timeout=20)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"فشل اختبار سلامة قاعدة البيانات: {integrity}")
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(_REQUIRED_COLUMNS) - tables
        if missing:
            raise ValueError("هذا الملف ليس نسخة SchoolPass متوافقة؛ الجداول الناقصة: " + ", ".join(sorted(missing)))
        for table, cols in _REQUIRED_COLUMNS.items():
            absent = cols - {r[1] for r in check.execute(f"PRAGMA table_info({table})")}
            if absent:
                raise ValueError(f"النسخة غير متوافقة؛ الأعمدة الناقصة في {table}: " + ", ".join(sorted(absent)))
        if not check.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
            raise ValueError("لا يوجد حساب مدير نشط داخل النسخة؛ أُلغيت العملية لحماية الوصول إلى التطبيق.")
        fk = check.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ValueError(f"تحتوي النسخة على {len(fk)} مخالفة مرجعية في قاعدة البيانات.")
    finally:
        check.close()


def _copy_db(src_path, dst_path) -> None:
    src, dst = sqlite3.connect(str(src_path), timeout=20), sqlite3.connect(str(dst_path), timeout=20)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_database(dest_path, user_id) -> Path:
    """Consistent online backup, verified (integrity + schema) before it replaces the destination."""
    admin = require_admin(user_id)
    dest = Path(dest_path)
    partial = dest.with_name(dest.name + ".partial")
    try:
        _copy_db(DB_PATH, partial)
        validate_database(partial)
        os.replace(partial, dest)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
    audit(admin["id"], "إنشاء نسخة احتياطية", "backup", dest.name, f"{dest.stat().st_size} بايت؛ تم التحقق من السلامة")
    return dest


def automatic_backup_if_due(user_id, keep_days: int = 30) -> Path | None:
    """Create/verify a daily local backup, then mirror it to the configured external folder."""
    actor = require_user(user_id)
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    today_path = backup_dir / f"automatic_{date.today().strftime('%Y%m%d')}.sqlite3"
    if today_path.exists():
        try:
            validate_database(today_path)
        except Exception:
            corrupt = backup_dir / f"{today_path.stem}_{datetime.now().strftime('%H%M%S')}.corrupt.sqlite3"
            os.replace(today_path, corrupt)
    # Refresh the same daily snapshot on every sign-in, periodic checkpoint and clean exit.
    partial = today_path.with_name(today_path.name + ".partial")
    try:
        _copy_db(DB_PATH, partial)
        validate_database(partial)
        os.replace(partial, today_path)
    finally:
        partial.unlink(missing_ok=True)
    audit(actor["id"], "تحديث النسخة الاحتياطية اليومية", "backup", today_path.name,
          f"{today_path.stat().st_size} بايت؛ فحص السلامة ناجح")
    # External copy is deliberately a separate, verified file; it is not silently treated as success.
    external = setting("external_backup_path", "").strip()
    if external:
        try:
            ext_dir = Path(external)
            if not ext_dir.is_dir():
                raise OSError("مجلد النسخ الخارجي غير متصل أو غير متاح.")
            target = ext_dir / today_path.name
            partial_ext = target.with_name(target.name + ".partial")
            try:
                import shutil
                shutil.copy2(today_path, partial_ext)
                validate_database(partial_ext)
                os.replace(partial_ext, target)
            finally:
                partial_ext.unlink(missing_ok=True)
            set_setting("external_backup_last_error", "")
            audit(actor["id"], "تحديث نسخة احتياطية خارجية", "backup", str(target),
                  f"{target.stat().st_size} بايت؛ فحص السلامة ناجح")
            ext_cutoff = time.time() - max(7, int(keep_days)) * 86400
            for old in ext_dir.glob("automatic_*.sqlite3"):
                try:
                    if old != target and old.stat().st_mtime < ext_cutoff:
                        old.unlink()
                except OSError:
                    pass
        except Exception as exc:
            set_setting("external_backup_last_error", str(exc)[:500])
    else:
        set_setting("external_backup_last_error", "")
    cutoff = time.time() - max(7, int(keep_days)) * 86400
    for old in backup_dir.glob("automatic_*.sqlite3"):
        try:
            if old != today_path and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return today_path


def qr_health_check() -> tuple[bool, str, str]:
    """Check local QR HTTP endpoint and return (ok, local-IP URL, response text)."""
    import urllib.request
    ip = get_local_ip()
    port = int(_SERVER.server_address[1]) if _SERVER is not None else SERVER_PORT
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read(120).decode("utf-8", errors="replace")
            return response.status == 200 and "SchoolPass OK" in body, f"http://{ip}:{port}/health", body
    except Exception as exc:
        return False, f"http://{ip}:{port}/health", str(exc)



def create_daily_report_pdf(dest_path, report_date: date | str, user_id) -> Path:
    """Create an A4-landscape daily handover report listing every permit issued that day.

    Entry permits (late/absence return) and exceptional exits are listed once each.
    Exit-return slips are listed as their own issued entry permits as well as reflected
    in the original exit row's return status/time. Cancelled records remain visible.
    """
    # The report contains complete guardian identity numbers; restrict it to the system administrator.
    actor = require_admin(user_id)
    if isinstance(report_date, str):
        try:
            report_date = date.fromisoformat(report_date)
        except ValueError as exc:
            raise ValueError("تاريخ التقرير غير صحيح؛ استخدم الصيغة YYYY-MM-DD.") from exc
    if not isinstance(report_date, date):
        raise ValueError("يجب تحديد تاريخ صحيح للتقرير.")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors

    day = report_date.strftime("%Y-%m-%d")
    issue = "COALESCE(NULLIF(r.issued_at,''),r.created_at)"
    with db_connect() as conn:
        rows = conn.execute(f"""SELECT r.*,u.display_name AS supervisor,ru.display_name AS returned_by_name,
            cu.display_name AS cancelled_by_name,s.last_name AS student_last_name,s.first_name AS student_first_name,
            pr.details_json AS parent_details_json
            FROM records r
            LEFT JOIN users u ON u.id=r.user_id
            LEFT JOIN users ru ON ru.id=r.returned_by
            LEFT JOIN users cu ON cu.id=r.cancelled_by
            LEFT JOIN students s ON s.student_key=r.student_key
            LEFT JOIN records pr ON pr.id=r.related_record_id
            WHERE substr({issue},1,10)=? AND r.kind IN ('late','absence_return','exit_permit','exit_return')
            ORDER BY {issue},r.record_no""", (day,)).fetchall()
    out = Path(dest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = landscape(A4)
    margin = 9 * mm
    font = _font_setup()
    c = canvas.Canvas(str(out), pagesize=(page_w, page_h), pageCompression=1)
    c.setTitle(f"SchoolPass — التقرير اليومي {day}")
    c.setAuthor(APP_NAME)
    generated_at = now_iso()
    active_rows = [r for r in rows if r["status"] != "cancelled"]
    counts = Counter(r["kind"] for r in active_rows)
    distinct_students = len({r["student_key"] for r in active_rows})
    cancelled_count = len(rows) - len(active_rows)
    # Wide but compact A4 landscape register: one permit per row. Full guardian ID
    # is decrypted only for this administrator-only report and never written to logs.
    columns = [
        (25, "م", "seq"), (72, "اللقب", "last_name"), (72, "الاسم", "first_name"),
        (59, "القسم", "class"), (65, "بداية الغياب", "absence_start"),
        (91, "التبرير / السبب", "reason"), (72, "اسم الولي", "guardian"),
        (46, "القرابة", "relationship"), (80, "رقم هوية الولي", "national_id"),
        (46, "الوقت", "time"), (53, "نوع الرخصة", "kind"), (48, "الحالة", "state"),
    ]
    table_w = sum(w for w, _, _ in columns)
    usable_w = page_w - 2 * margin
    if table_w > usable_w:
        raise RuntimeError("تعذر ضبط أعمدة التقرير اليومي ضمن عرض ورقة A4.")
    table_right = page_w - margin - (usable_w - table_w) / 2
    page_no = 0
    y = 0.0

    def visual(value):
        return _arabic_visual(str(value if value not in (None, "") else "—"))

    def wrapped(value, width, size=6.8):
        lines = _wrap_to_width(str(value if value not in (None, "") else "—"), font, size, width - 7)
        # Preserve the complete value; long reasons and identifiers expand the row
        # and continue onto another page rather than being silently truncated.
        return lines

    def footer():
        c.setStrokeColor(colors.HexColor("#AAB8B1"))
        c.setLineWidth(0.35)
        c.line(margin, 8 * mm, page_w - margin, 8 * mm)
        c.setFillColor(colors.HexColor("#53645C"))
        c.setFont(font, 6.8)
        c.drawString(margin, 4.5 * mm, visual(f"صفحة {page_no}"))
        c.drawRightString(page_w - margin, 4.5 * mm, visual(f"وقت إنشاء التقرير: {generated_at}"))

    def table_header():
        nonlocal y
        h = 8 * mm
        c.setFillColor(colors.HexColor("#E7F0EB"))
        c.roundRect(table_right - table_w, y - h + 1, table_w, h, 2, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#103E34"))
        c.setFont(font, 7.0)
        x_right = table_right
        for width, title, _key in columns:
            c.drawRightString(x_right - 4, y - 5.1 * mm, visual(title))
            x_right -= width
        y -= h

    def page_header(first=False):
        nonlocal y, page_no
        page_no += 1
        c.setFillColor(colors.HexColor("#103E34"))
        c.setFont(font, 14)
        c.drawRightString(page_w - margin, page_h - 13 * mm, visual(setting("school_name", SCHOOL_NAME)))
        c.setFont(font, 12)
        c.drawRightString(page_w - margin, page_h - 20 * mm, visual(f"التقرير اليومي لرخص الدخول والخروج — {report_date.strftime('%d/%m/%Y')}"))
        c.setStrokeColor(colors.HexColor("#789087"))
        c.setLineWidth(0.6)
        c.line(margin, page_h - 24 * mm, page_w - margin, page_h - 24 * mm)
        y = page_h - 31 * mm
        if first:
            summary = (f"إجمالي السجلات: {len(rows)}  |  غير الملغاة: {len(active_rows)}  |  "
                       f"عدد التلاميذ أصحاب الرخص غير الملغاة: {distinct_students}  |  "
                       f"تأخر: {counts.get('late', 0)}  |  عودة بعد الغياب: {counts.get('absence_return', 0)}  |  "
                       f"خروج استثنائي: {counts.get('exit_permit', 0)}  |  إثبات عودة: {counts.get('exit_return', 0)}  |  ملغاة: {cancelled_count}")
            summary_lines = _wrap_to_width(summary, font, 7.0, usable_w - 12)
            summary_h = max(10 * mm, len(summary_lines) * 9 + 14)
            c.setFillColor(colors.HexColor("#F2F6F3"))
            c.roundRect(margin, y - summary_h, usable_w, summary_h - 2, 2 * mm, stroke=0, fill=1)
            c.setFillColor(colors.HexColor("#243C32"))
            c.setFont(font, 7.0)
            baseline = y - 7 * mm
            for line in summary_lines:
                c.drawRightString(page_w - margin - 4, baseline, visual(line))
                baseline -= 9
            y -= summary_h + 5 * mm
        else:
            c.setFont(font, 7)
            c.drawRightString(page_w - margin, y, visual("استكمال التقرير اليومي"))
            y -= 5 * mm
        table_header()

    page_header(first=True)
    for index, row in enumerate(rows, 1):
        issued = str(row["issued_at"] or row["created_at"] or "")
        try:
            time_text = datetime.strptime(issued[:19], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
        except ValueError:
            time_text = issued[11:16] if len(issued) >= 16 else "—"
        if row["status"] == "cancelled":
            state = "ملغاة"
            if row["cancelled_reason"]:
                reason_text = f"{row['reason'] or '—'} — سبب الإلغاء: {row['cancelled_reason']}"
            else:
                reason_text = row["reason"] or "—"
        elif row["kind"] == "exit_permit" and row["status"] == "returned":
            returned = str(row["returned_at"] or "")
            state = f"تمت العودة {returned[11:16]}" if len(returned) >= 16 else "تمت العودة"
            reason_text = row["reason"] or "—"
        elif row["kind"] == "exit_permit":
            state = "لم تسجل العودة"
            reason_text = row["reason"] or "—"
        elif row["kind"] == "exit_return":
            state = "تم إثبات العودة"
            reason_text = row["reason"] or "—"
        else:
            state = "سارية"
            reason_text = row["reason"] or "—"
        details = json.loads(row["details_json"] or "{}")
        if row["kind"] == "exit_return" and row["parent_details_json"]:
            parent_details = json.loads(row["parent_details_json"] or "{}")
            for key in ("guardian_name", "relationship", "national_id_enc"):
                if not details.get(key):
                    details[key] = parent_details.get(key)
        if row["kind"] == "absence_return":
            proof_method = str(details.get("proof_method", "")).strip()
            proof_label = {
                "medical_certificate": "شهادة طبية",
                "guardian": "حضور ولي الأمر شخصيًا",
            }.get(proof_method, "طريقة التبرير غير محددة")
            note = str(row["reason"] or "").strip()
            reason_text = f"{proof_label} — {note}" if note else proof_label
            if row["status"] == "cancelled" and row["cancelled_reason"]:
                reason_text += f" — سبب الإلغاء: {row['cancelled_reason']}"
        absence_start = details.get("absence_start", "—") if row["kind"] == "absence_return" else "—"
        guardian_name = details.get("guardian_name", "—")
        relationship = details.get("relationship", "—")
        full_national_id = "—"
        if details.get("national_id_enc"):
            # require_admin above is deliberate: this is the only report that prints the full ID.
            full_national_id = reveal_sensitive_text(details["national_id_enc"])
        last_name = row["student_last_name"] or row["student_name"]
        first_name = row["student_first_name"] or "—"
        values = [str(index), last_name, first_name, row["class_name"], absence_start,
                  reason_text, guardian_name, relationship, full_national_id, time_text,
                  kind_label(row["kind"]), state]
        cells = [wrapped(v, width, 5.8) for (width, _title, _key), v in zip(columns, values)]
        max_lines = max(len(lines) for lines in cells)
        row_h = max(7 * mm, max_lines * 7.2 + 4)
        if y - row_h < 18 * mm:
            footer()
            c.showPage()
            page_header(first=False)
            row_h = max(7 * mm, max_lines * 7.2 + 4)
        if index % 2 == 0:
            c.setFillColor(colors.HexColor("#F7FAF8"))
            c.rect(table_right - table_w, y - row_h, table_w, row_h, stroke=0, fill=1)
        if row["status"] == "cancelled":
            c.setFillColor(colors.HexColor("#8F2727"))
        else:
            c.setFillColor(colors.HexColor("#1E2B25"))
        c.setFont(font, 5.8)
        x_right = table_right
        for (width, _title, _key), lines in zip(columns, cells):
            baseline = y - 4.7 * mm
            for line in lines:
                c.drawRightString(x_right - 4, baseline, visual(line))
                baseline -= 7.2
            x_right -= width
        c.setStrokeColor(colors.HexColor("#D8E2DC"))
        c.setLineWidth(0.25)
        c.line(table_right - table_w, y - row_h, table_right, y - row_h)
        y -= row_h

    if y < 31 * mm:
        footer()
        c.showPage()
        page_header(first=False)
    c.setFillColor(colors.HexColor("#263D33"))
    c.setFont(font, 8)
    c.drawRightString(page_w - margin, y - 6 * mm, visual(f"أُعدّ التقرير بواسطة: {actor['display_name']} — اعتمادًا على سجلات الرخص المسجلة في النظام."))
    c.setFont(font, 8)
    c.drawRightString(page_w - margin - 12 * mm, y - 17 * mm, visual("إمضاء المشرف: ................................"))
    c.drawString(margin + 12 * mm, y - 17 * mm, visual("مصادقة المدير: ................................"))
    footer()
    c.save()
    audit(actor["id"], "إنشاء التقرير اليومي PDF ببيانات ولي الأمر الكاملة", "report", day,
          f"عدد الرخص: {len(rows)}؛ التلاميذ: {distinct_students}؛ الملغاة: {cancelled_count}")
    return out

def create_monthly_report_pdf(dest_path, year: int, month: int, user_id) -> Path:
    """Create a readable A4-landscape monthly report with measured RTL cell wrapping."""
    require_user(user_id)
    year, month = int(year), int(month)
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("الشهر أو السنة غير صحيحين.")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics

    font = _font_setup()
    with db_connect() as conn:
        rows = conn.execute("""SELECT r.*,u.display_name AS supervisor FROM records r
            LEFT JOIN users u ON u.id=r.user_id WHERE substr(COALESCE(NULLIF(r.issued_at,''),r.created_at),1,7)=?
            ORDER BY COALESCE(NULLIF(r.issued_at,''),r.created_at),r.record_no""", (f"{year:04d}-{month:02d}",)).fetchall()

    out = Path(dest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = landscape(A4)
    margin = 10 * mm
    usable_w = page_w - 2 * margin
    c = canvas.Canvas(str(out), pagesize=(page_w, page_h), pageCompression=1)
    c.setTitle(f"SchoolPass Monthly {year:04d}-{month:02d}")
    c.setAuthor(APP_NAME)
    generated_at = now_iso()

    active = sum(1 for r in rows if r["status"] != "cancelled")
    cancelled = len(rows) - active
    relevant = [r for r in rows if r["kind"] in ("late", "absence_return") and r["status"] != "cancelled"]
    by_class = Counter((r["class_name"] or "—", kind_label(r["kind"])) for r in relevant)
    by_supervisor = Counter((r["supervisor"] or "—", kind_label(r["kind"])) for r in relevant)

    # Column widths total 776 pt, within the 796 pt printable area. Anchors are
    # ordered right-to-left, matching Arabic reading direction.
    columns = [
        (104, "الرقم المرجعي", "record_no"),
        (100, "تاريخ ووقت الإصدار", "issued_at"),
        (112, "نوع الرخصة", "kind"),
        (150, "التلميذ", "student_name"),
        (110, "القسم", "class_name"),
        (115, "المشرف", "supervisor"),
        (85, "الحالة", "status"),
    ]
    table_w = sum(width for width, _, _ in columns)
    if table_w > usable_w:
        raise RuntimeError("تعذر ضبط أعمدة التقرير ضمن عرض الصفحة.")
    table_right = page_w - margin - (usable_w - table_w) / 2

    def visual(text: str) -> str:
        return _arabic_visual(str(text or "—"))

    def wrapped(text: str, max_width: float, size: float) -> list[str]:
        # _wrap_to_width measures the shaped text using the selected embedded font.
        return _wrap_to_width(str(text or "—"), font, size, max_width)

    page_no = 0
    y = 0.0

    def footer():
        c.setStrokeColor(colors.HexColor("#9AA7B2"))
        c.setLineWidth(0.35)
        c.line(margin, 9 * mm, page_w - margin, 9 * mm)
        c.setFillColor(colors.HexColor("#45515C"))
        c.setFont(font, 7)
        c.drawString(margin, 5 * mm, visual(f"صفحة {page_no}"))
        c.drawRightString(page_w - margin, 5 * mm, visual(f"تاريخ إنشاء التقرير: {generated_at}"))

    def page_header(first: bool):
        nonlocal y, page_no
        page_no += 1
        c.setFillColor(colors.black)
        c.setFont(font, 14)
        c.drawRightString(page_w - margin, page_h - 16 * mm, visual(setting("school_name", SCHOOL_NAME)))
        c.setFont(font, 11)
        c.drawRightString(page_w - margin, page_h - 23 * mm, visual(f"التقرير الشهري للعمليات — {year:04d}-{month:02d}"))
        c.setStrokeColor(colors.HexColor("#566573"))
        c.setLineWidth(0.55)
        c.line(margin, page_h - 27 * mm, page_w - margin, page_h - 27 * mm)
        y = page_h - 34 * mm
        if first:
            c.setFont(font, 8)
            c.drawRightString(page_w - margin, y, visual(
                f"إجمالي السجلات: {len(rows)}  |  السارية/المعادة: {active}  |  الملغاة: {cancelled}"))
            y -= 7 * mm
            summaries = [
                "حسب القسم (التأخر والدخول بعد الغياب): " + ("؛ ".join(f"{k[0]} / {k[1]}: {v}" for k, v in by_class.items()) or "لا توجد عمليات"),
                "حسب المشرف (التأخر والدخول بعد الغياب): " + ("؛ ".join(f"{k[0]} / {k[1]}: {v}" for k, v in by_supervisor.items()) or "لا توجد عمليات"),
            ]
            c.setFont(font, 7)
            for summary in summaries:
                for line in wrapped(summary, usable_w, 7):
                    c.drawRightString(page_w - margin, y, visual(line))
                    y -= 4.4 * mm
            y -= 1.5 * mm
        draw_table_header()

    def draw_table_header():
        nonlocal y
        h = 8 * mm
        c.setFillColor(colors.HexColor("#E8EEF3"))
        c.rect(table_right - table_w, y - h + 1.5, table_w, h, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#172B3A"))
        c.setFont(font, 7.2)
        x_right = table_right
        for width, title, _key in columns:
            c.drawRightString(x_right - 4, y - 5.1 * mm, visual(title))
            x_right -= width
        c.setStrokeColor(colors.HexColor("#778899"))
        c.setLineWidth(0.5)
        c.line(table_right - table_w, y - h, table_right, y - h)
        y -= h

    page_header(first=True)
    c.setFont(font, 6.8)
    for idx, r in enumerate(rows):
        vals = [
            r["record_no"],
            (r["issued_at"] or r["created_at"]),
            kind_label(r["kind"]),
            r["student_name"],
            r["class_name"],
            r["supervisor"] or "—",
            status_label(r["status"]),
        ]
        cells: list[list[str]] = []
        max_lines = 1
        for width, _title, _key in columns:
            lines = wrapped(str(vals[len(cells)]), width - 8, 6.8)
            cells.append(lines)
            max_lines = max(max_lines, len(lines))
        # Extremely long values are split across page fragments instead of being
        # drawn below the footer. Each fragment repeats the table header after a page break.
        line_offset = 0
        while line_offset < max_lines:
            available_lines = int((y - 15 * mm - 5) // 8.2)
            if available_lines < 1:
                footer()
                c.showPage()
                page_header(first=False)
                c.setFont(font, 6.8)
                available_lines = int((y - 15 * mm - 5) // 8.2)
            chunk_lines = min(max_lines - line_offset, max(1, available_lines))
            row_h = max(8 * mm, chunk_lines * 8.2 + 5)
            if row_h > y - 15 * mm:
                footer()
                c.showPage()
                page_header(first=False)
                c.setFont(font, 6.8)
                continue
            if idx % 2 == 1:
                c.setFillColor(colors.HexColor("#F7F9FB"))
                c.rect(table_right - table_w, y - row_h, table_w, row_h, stroke=0, fill=1)
            c.setFillColor(colors.black)
            x_right = table_right
            for (width, _title, _key), cell_lines in zip(columns, cells):
                baseline = y - 4.8 * mm
                fragment = cell_lines[line_offset:line_offset + chunk_lines]
                for line in fragment:
                    c.drawRightString(x_right - 4, baseline, visual(line))
                    baseline -= 8.2
                x_right -= width
            c.setStrokeColor(colors.HexColor("#D4DCE3"))
            c.setLineWidth(0.25)
            c.line(table_right - table_w, y - row_h, table_right, y - row_h)
            y -= row_h
            line_offset += chunk_lines
            if line_offset < max_lines:
                footer()
                c.showPage()
                page_header(first=False)
                c.setFont(font, 6.8)

    footer()
    c.save()
    audit(_uid(user_id), "إنشاء تقرير شهري PDF", "report", f"{year:04d}-{month:02d}", f"عدد السجلات: {len(rows)}")
    return out


def restore_database(source_path, user_id) -> Path:
    """Replace the live DB with a validated backup. Keeps a safety copy; rolls back on failure.
    Returns the path of the safety copy of the previous data."""
    admin = require_admin(user_id)
    source = Path(source_path).resolve()
    if source == DB_PATH.resolve():
        raise ValueError("لا يمكن استعادة قاعدة البيانات الحالية فوق نفسها.")
    if not source.is_file():
        raise FileNotFoundError("لم يُعثر على ملف النسخة الاحتياطية المحدد.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = DATA_DIR / "backups" / f"restore_candidate_{stamp}.sqlite3"
    previous = DATA_DIR / "backups" / f"before_restore_{stamp}.sqlite3"
    # Never validate legacy schemas against the newest columns before migrating them.
    _copy_db(source, stage)
    server_was_running, replaced = False, False
    try:
        _migrate_staged_database(stage)
        validate_database(stage)
        _copy_db(DB_PATH, previous)  # safety copy of current data
        validate_database(previous)
        server_was_running = stop_local_server()
        for suffix in ("-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(stage, DB_PATH)
        replaced = True
        init_db()
        validate_database(DB_PATH)
        audit(None, "استعادة نسخة احتياطية", "database", source.name, f"بواسطة المدير #{admin['id']}؛ النسخة السابقة: {previous.name}")
        return previous
    except Exception as exc:
        if replaced and previous.exists():
            try:
                stop_local_server()
                for suffix in ("-wal", "-shm"):
                    Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
                os.replace(previous, DB_PATH)
                init_db()
            except Exception as rb:  # pragma: no cover
                raise RuntimeError(f"{exc}\nوفشل التراجع التلقائي أيضًا: {rb}. احتفظ بالملف: {previous}") from exc
        if server_was_running:
            ensure_local_server()
        raise
    finally:
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            pass


def create_a4_two_record_sheet(record_ids: list[int]) -> Path:
    """Create one A4 portrait sheet containing TWO independent permits, one per half.

    Each half is populated from its own database record. No duplicate copy is created.
    The dashed horizontal centre line is a cutting guide.
    """
    if len(record_ids) != 2 or int(record_ids[0]) == int(record_ids[1]):
        raise ValueError("يجب تحديد رخصتين مختلفتين بالضبط لطباعة ورقة A4.")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    page_w, page_h = A4
    half_h = page_h / 2
    # Each A6 design is rotated 90 degrees inside an A5 half, matching the
    # user's reference: the permit reads sideways on the portrait A4 sheet.
    scale = half_h / PAGE_W
    if PAGE_H * scale > page_w:
        raise RuntimeError("أبعاد الرخصة لا تتناسب مع نصف ورقة A4.")
    rows = [record_by_id(int(rid)) for rid in record_ids]
    if any(row is None for row in rows):
        raise ValueError("إحدى الرخصتين المحددتين غير موجودة.")
    if any(row["kind"] == "exit_permit" for row in rows):
        raise ValueError("رخصة الخروج لها نسختان إلزاميتان (للحاجب والتلميذ)؛ اطبعها بالطريقة المعتادة منفردة.")
    font, school = _font_setup(), setting("school_name", SCHOOL_NAME)
    out = DATA_DIR / "prints" / f"A4_TWO_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("SchoolPass — رخصتان مستقلتان على A4")
    c.setAuthor(APP_NAME)
    for index, row in enumerate(rows):
        details = json.loads(row["details_json"] or "{}")
        reprint = int(row["printed_count"] or 0) > 0
        issued = str(row["issued_at"] if "issued_at" in row.keys() and row["issued_at"] else row["created_at"])
        c.saveState()
        # Rotate the A6 artwork sideways and centre it in the upper/lower A5 half.
        half_bottom = half_h if index == 0 else 0
        c.translate(page_w / 2, half_bottom + half_h / 2)
        c.rotate(90)
        c.scale(scale, scale)
        c.translate(-PAGE_W / 2, -PAGE_H / 2)
        _draw_page(c, row, details, None, reprint, font, school, "")
        c.restoreState()
    c.saveState()
    c.setStrokeColorRGB(0.72, 0.76, 0.74)
    c.setLineWidth(0.6)
    c.setDash(4, 3)
    c.line(8 * MM, half_h, page_w - 8 * MM, half_h)
    c.setDash()
    c.setFillColorRGB(0.42, 0.48, 0.45)
    c.setFont(font, 7)
    c.drawCentredString(page_w / 2, half_h + 2 * MM, _arabic_visual("خط القص"))
    c.restoreState()
    c.save()
    return out
