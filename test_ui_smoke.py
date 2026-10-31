# -*- coding: utf-8 -*-
"""Headless GUI smoke test: runs app.py with a FAKE Tk so that NameError/TypeError/wrong-signature bugs in the
GUI code are caught without a display. It does NOT check how widgets look; that needs a real Windows session."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testenv  # noqa: E402  (one shared data dir for all modules)
TEST_ROOT = testenv.ROOT
import schoolpass_core as core  # noqa: E402

core.PBKDF2_ITERATIONS = 2000
CALLS: list = []   # (kind, title/text)
COMMANDS: list = []


class StrVar:
    def __init__(self, master=None, value=""):
        self._v = value
    def get(self): return self._v
    def set(self, v): self._v = v
    def trace_add(self, *a, **k): pass


class Widget:
    registry: list = []
    def __init__(self, *a, **k):
        self.kw = k
        self.rows = {}
        self._sel = ()
        if "command" in k: COMMANDS.append(k["command"])
        Widget.registry.append(self)
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        return lambda *a, **k: None
    def pack(self, *a, **k): return None
    grid = place = pack
    def bind(self, *a, **k): return None
    def configure(self, cnf=None, **k):
        if isinstance(cnf, dict): self.kw.update(cnf)
        self.kw.update(k)
        return None
    config = configure
    def get(self):
        var = self.kw.get('textvariable')
        return var.get() if var is not None else getattr(self, '_value', '')
    def set(self, value):
        self._value = value
        var = self.kw.get('textvariable')
        if var is not None: var.set(value)
    def current(self, index):
        values = self.kw.get('values', ())
        if 0 <= index < len(values): self.set(values[index])
    def winfo_exists(self): return True
    def winfo_width(self): return 900
    def winfo_height(self): return 160
    def update_idletasks(self): return None
    def insert(self, parent, index=None, iid=None, values=(), **k):
        if isinstance(parent, str) and iid is not None: self.rows[iid] = values
    def delete(self, *a): self.rows.clear()
    def get_children(self, *a): return list(self.rows)
    def selection(self): return self._sel
    def after(self, ms, fn=None, *a): return None
    def wait_window(self, w=None): return None
    def clipboard_clear(self): pass
    def clipboard_append(self, s): pass
    def yview(self, *a): pass


def fake_ttk():
    m = types.ModuleType("tkinter.ttk")
    for n in ("Style", "Frame", "Label", "Button", "Entry", "Combobox", "Treeview", "Scrollbar", "Checkbutton"):
        setattr(m, n, type(n, (Widget,), {}))
    return m


def fake_tk():
    m = types.ModuleType("tkinter")
    m.Tk = type("Tk", (Widget,), {}); m.Toplevel = type("Toplevel", (Widget,), {})
    m.Text = type("Text", (Widget,), {})
    m.Canvas = type("Canvas", (Widget,), {})
    m.StringVar = StrVar; m.BooleanVar = StrVar
    class MB:
        def __getattr__(self, name):
            def f(*a, **k):
                CALLS.append((name, a[:2])); return True
            return f
    m.messagebox = MB()
    class FD:
        answer = ""
        def __getattr__(self, name):
            return lambda *a, **k: FD.answer
    m.filedialog = FD()
    class CC:
        @staticmethod
        def askcolor(*a, **k): return ((24, 90, 141), "#185A8D")
    m.colorchooser = CC()
    for n, v in dict(END="end", BOTH="both", LEFT="left", RIGHT="right", X="x", Y="y", E="e").items(): setattr(m, n, v)
    m.ttk = fake_ttk()
    return m


def load_app():
    tk = fake_tk()
    with mock.patch.dict(sys.modules, {"tkinter": tk, "tkinter.ttk": tk.ttk}):
        spec = importlib.util.spec_from_file_location("app_ui", HERE / "app.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["tkinter"] = tk  # for `import tkinter` inside show_stats
        sys.modules["tkinter.ttk"] = tk.ttk
        mod._tk = tk
        return mod


def tearDownModule():
    core.stop_local_server()
    pass


class TestGuiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {k: sys.modules.get(k) for k in ("tkinter", "tkinter.ttk")}
        cls.app = load_app()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v

    def setUp(self):
        core.stop_local_server()
        for s in ("", "-wal", "-shm"): Path(str(core.DB_PATH) + s).unlink(missing_ok=True)
        core.init_db()
        self.admin, _ = core.bootstrap_admin("director", "المدير", "TestPassword123!")
        self.sup = core.create_user_as(self.admin, "sup1", "مشرف", "TestPassword123!")
        csv_path = TEST_ROOT / "s.csv"
        csv_path.write_text("رقم التعريف,اللقب,الاسم,الجنس,السنة,الشعبة,القسم\nU-1,تجريبي,أحمد,ذكر,أولى,علوم,1\nU-2,اختباري,سارة,أنثى,أولى,علوم,1\n", encoding="utf-8-sig")
        core.import_students(str(csv_path), self.admin)
        self.s1 = core.get_student("U-1")
        CALLS.clear(); COMMANDS.clear()
        sys.modules["tkinter"] = self.app._tk; sys.modules["tkinter.ttk"] = self.app._tk.ttk

    def make_app(self, uid):
        user = core.get_user(uid)
        with mock.patch.object(self.app.core, "ensure_local_server", return_value=(True, "192.168.1.10")):
            return self.app.SchoolPassApp(self.app._tk.Tk(), user)

    def press_all(self, label):
        """Invoke every button command created so far; none may raise."""
        failures = []
        for cmd in list(COMMANDS):
            try: cmd()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{label}: {getattr(cmd, '__name__', cmd)} -> {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [])

    def test_admin_and_supervisor_main_windows_and_every_button(self):
        for uid in (self.admin, self.sup):
            a = self.make_app(uid)
            with mock.patch.object(self.app, "ask_text", return_value="سبب تجريبي"), mock.patch.object(self.app, "ask_password", return_value="NewPassword456!"):
                self.press_all(f"main uid={uid}")
            a.refresh_records(); a.poll_qr_events(); a.check_inactivity()

    def test_forms_create_each_record_kind_through_core(self):
        a = self.make_app(self.sup)
        for kind, vals in (("late", {"reason": "حافلة"}),
                           ("absence_return", {"absence_start": "2025-01-10", "return_date": "2025-01-11", "reason": ""}),
                           ("exit_permit", {"reason": "طبيب", "guardian_name": "ولي", "relationship": "أب", "national_id": "123456789012345678"})):
            f = self.app.RecordForm(a, kind)
            f.student = self.s1
            for k, v in vals.items(): f.vars[k].set(v)
            if kind == "absence_return":
                f.check_vars['certificate_present'].set(True)
                f.method_combo.set('شهادة طبية')
            with mock.patch.object(core, "protect_sensitive_text", lambda v: "DPAPI1:" + v[::-1]), mock.patch.object(a, "show_record") as sr:
                f.save()
            self.assertTrue(sr.called, kind)
        with core.db_connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 3)

    def test_absence_form_forces_guardian_when_absence_days_exceed_two_excluding_return_date(self):
        a = self.make_app(self.sup)
        f = self.app.RecordForm(a, "absence_return")
        f.vars['absence_start'].set('2025-01-06')
        f.vars['return_date'].set('2025-01-09')
        f._update_absence_rule()
        self.assertEqual(f.method_combo.get(), 'حضور ولي الأمر شخصيًا')
        
        # The widget stores display-shaped RTL glyphs; test semantic Arabic letters, not
        # the logical Unicode sequence (which is reordered for visual presentation).
        import unicodedata
        visible = unicodedata.normalize('NFKC', f.rule_label.kw.get('text', ''))
        self.assertTrue(set('إلزامي') - set(visible) == set(), visible)

    def test_form_validation_errors_are_shown_not_raised(self):
        a = self.make_app(self.sup)
        f = self.app.RecordForm(a, "late"); f.student = self.s1
        CALLS.clear(); f.save()
        self.assertTrue(any(c[0] == "showerror" for c in CALLS))
        f2 = self.app.RecordForm(a, "late"); CALLS.clear(); f2.save()           # no student
        self.assertTrue(any(c[0] == "showerror" for c in CALLS))

    def test_record_detail_cancel_return_reveal_and_print_paths(self):
        a = self.make_app(self.admin)
        with mock.patch.object(core, "protect_sensitive_text", lambda v: "DPAPI1:" + v[::-1]):
            p = core.create_exit_permit(self.s1, self.sup, "طبيب", "ولي", "أب", "123456789012345678")
        late = core.create_late_slip(self.s1, self.sup, "x")
        with mock.patch.object(self.app, "ask_text", return_value="خطأ إدخال"), mock.patch.object(core, "reveal_sensitive_text", lambda v: v.split(":", 1)[1][::-1]):
            for rid in (p["id"], late["id"]):
                COMMANDS.clear(); a.show_record(rid, offer_print=True); self.press_all(f"detail {rid}")
        COMMANDS.clear(); a.show_record(late["id"]); a.show_record(p["id"])    # now cancelled / returned or cancelled
        a.tree._sel = (str(p["id"]),); a.open_selected_record(); a.reprint_selected()
        self.assertEqual(core.record_by_id(late["id"])["status"], "cancelled")

    def test_students_logs_stats_users_settings_audit_backup_restore_windows(self):
        a = self.make_app(self.admin)
        FD = type(self.app._tk.filedialog)
        with mock.patch.object(self.app, "ask_text", return_value="x"), mock.patch.object(self.app, "ask_password", return_value="NewPassword456!"):
            for fn in (a.manage_students, a.open_logs, a.show_stats, a.manage_users, a.settings_dialog, a.audit_view):
                COMMANDS.clear(); fn(); self.press_all(fn.__name__)
            a.show_student_profile(self.s1["id"])
            FD.answer = str(TEST_ROOT / "b.sqlite3"); a.backup_db()
            self.assertTrue(Path(FD.answer).exists())
            FD.answer = str(TEST_ROOT / "export.csv"); a.export_records(); a.export_students()
            self.assertTrue(Path(FD.answer).exists())
            FD.answer = str(TEST_ROOT / "b.sqlite3"); a.restore_db()
            FD.answer = str(TEST_ROOT / "s.csv"); a._import_students(a.root, lambda: None)
            FD.answer = ""

    def test_setup_login_recovery_dialogs(self):
        for s in ("", "-wal", "-shm"): Path(str(core.DB_PATH) + s).unlink(missing_ok=True)
        core.init_db()
        root = self.app._tk.Tk()
        self.app.SetupDialog(root); self.app.LoginDialog(root); self.app.LoginDialog(root, lock_mode=True, prefill="x")
        self.app.RecoveryDialog(root); self.app.show_recovery_code(root, "AAAA-BBBB")
        COMMANDS.clear(); self.app.SetupDialog(root); self.press_all("setup")     # empty fields -> error message, no crash
        self.assertFalse(core.has_admin())

    def test_theme_color_helpers_preserve_contrast(self):
        self.assertEqual(self.app._text_on("#000000"), "#ffffff")
        self.assertEqual(self.app._text_on("#FFFFFF"), "#000000")
        shaded = self.app._shade("#185A8D")
        self.assertRegex(shaded, r"^#[0-9A-F]{6}$")
        self.assertNotEqual(shaded, "#185A8D")

    def test_combo_stringvar_returns_logical_arabic_values(self):
        values = ['الكل', 'ساري', 'ملغى']
        variable = self.app.ComboStringVar(values, value='الكل')
        self.assertEqual(variable.get(), 'الكل')
        variable.set('ملغى')
        self.assertEqual(variable.get(), 'ملغى')
        self.assertEqual(variable._display_to_logical[variable._logical_to_display['ساري']], 'ساري')

    def test_qr_phone_scan_is_logged_anonymously_without_opening_windows(self):
        a = self.make_app(self.sup)
        rec = core.create_late_slip(self.s1, self.sup, 'اختبار QR')
        with mock.patch.object(a, 'show_record') as show:
            core.EVENT_QUEUE.put(rec['qr_token'])
            a.poll_qr_events()
            show.assert_not_called()
        self.assertIn(rec['record_no'], a.status_text.get())
        with core.db_connect() as c:
            event = c.execute("SELECT user_id FROM audit_logs WHERE action='تحقق بواسطة QR (الشبكة المحلية)' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNone(event['user_id'])

    def test_session_helpers(self):
        self.assertTrue(self.app._single_instance())
        self.assertFalse(self.app._single_instance())
        self.app._INSTANCE_LOCK.close(); self.app._INSTANCE_LOCK = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
