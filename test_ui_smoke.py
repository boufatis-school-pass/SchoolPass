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
        self.headings = {}
        self.columns_cfg = {}
        self.tags = {}
        self.tag_ranges = []
        self.insertions = []
        self.canvas_texts = []
        self.canvas_config = {}
        self._title = ""
        if "command" in k: COMMANDS.append(k["command"])
        Widget.registry.append(self)
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        return lambda *a, **k: None
    def title(self, value=None):
        if value is None:
            return self._title
        self._title = value
        return value
    def heading(self, column, option=None, **kwargs):
        self.headings.setdefault(str(column), {}).update(kwargs)
        return self.headings[str(column)].get(option) if option else self.headings[str(column)]
    def column(self, column, option=None, **kwargs):
        self.columns_cfg.setdefault(str(column), {}).update(kwargs)
        return self.columns_cfg[str(column)].get(option) if option else self.columns_cfg[str(column)]
    def index(self, value):
        return '1.0' if value == 'end' else str(value)
    def tag_configure(self, tag, **kwargs):
        self.tags.setdefault(tag, {}).update(kwargs)
    def tag_add(self, tag, start, end):
        self.tag_ranges.append((tag, start, end))
    def create_text(self, *args, **kwargs):
        self.canvas_texts.append(dict(kwargs))
        return len(self.canvas_texts)
    def itemconfigure(self, tag_or_id, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            self.canvas_config.update(cnf)
        self.canvas_config.update(kwargs)
        return self.canvas_config
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
    def set(self, *args):
        # Entry/Combobox uses set(value); Treeview uses set(iid, column[, value]).
        if len(args) == 1:
            value = args[0]
            self._value = value
            var = self.kw.get('textvariable')
            if var is not None: var.set(value)
            return value
        if len(args) == 2:
            iid, column = args
            values = self.rows.get(str(iid), ())
            columns = tuple(self.kw.get('columns', ()))
            try: return values[columns.index(column)]
            except (ValueError, IndexError): return ''
        if len(args) == 3:
            iid, column, value = args
            columns = tuple(self.kw.get('columns', ()))
            values = list(self.rows.get(str(iid), ()))
            try:
                values[columns.index(column)] = value
                self.rows[str(iid)] = tuple(values)
            except (ValueError, IndexError): pass
            return value
        raise TypeError('set expects 1, 2, or 3 arguments')
    def current(self, index):
        values = self.kw.get('values', ())
        if 0 <= index < len(values): self.set(values[index])
    def winfo_exists(self): return True
    def winfo_width(self): return 900
    def winfo_height(self): return 160
    def update_idletasks(self): return None
    def insert(self, parent, index=None, iid=None, values=(), **k):
        if isinstance(parent, str) and iid is not None:
            self.rows[iid] = values
        else:
            self.insertions.append((parent, index, iid, values, dict(k)))
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
    m.LabelFrame = type("LabelFrame", (Widget,), {})
    m.Listbox = type("Listbox", (Widget,), {})
    m.Canvas = type("Canvas", (Widget,), {})
    m.StringVar = StrVar; m.BooleanVar = StrVar
    class MB:
        def __getattr__(self, name):
            if name.startswith('_schoolpass_') or name.startswith('_original_'):
                raise AttributeError(name)
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
        def headless_message_dialog(kind, title=None, message=None, **options):
            CALLS.append((kind, (title, message)))
            return {
                'showinfo': 'ok', 'showwarning': 'ok', 'showerror': 'ok',
                'askyesno': False, 'askokcancel': False, 'askretrycancel': False,
                'askyesnocancel': None, 'askquestion': 'no',
            }.get(kind, 'ok')
        mod._custom_message_dialog_gui = mod._custom_message_dialog
        mod._custom_message_dialog = headless_message_dialog
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

    def test_custom_arabic_dialog_preserves_messagebox_default_results(self):
        owner = self.app._tk.Tk()
        self.assertEqual(self.app._custom_message_dialog('showinfo', 'عنوان', 'رسالة', parent=owner), 'ok')
        self.assertFalse(self.app._custom_message_dialog('askyesno', 'تأكيد', 'متابعة؟', parent=owner))
        self.assertIsNone(self.app._custom_message_dialog('askyesnocancel', 'تأكيد', 'متابعة؟', parent=owner))

    def test_custom_dialog_shapes_title_and_body_for_arabic_rtl(self):
        owner = self.app._tk.Tk()
        Widget.registry.clear()
        with mock.patch.object(self.app, 'rtl_display', side_effect=lambda value: f'<RTL>{value}</RTL>') as shape:
            self.app._custom_message_dialog_gui('showinfo', 'عنوان عربي', 'رسالة عربية 2.5.3', parent=owner)
        labels = {w.kw.get('style'): w.kw.get('text') for w in Widget.registry
                  if w.kw.get('style') in ('DialogTitle.TLabel', 'DialogMessage.TLabel')}
        self.assertEqual(labels.get('DialogTitle.TLabel'), '<RTL>عنوان عربي</RTL>')
        self.assertEqual(labels.get('DialogMessage.TLabel'), '<RTL>رسالة عربية 2.5.3</RTL>')
        self.assertGreaterEqual(shape.call_count, 3)

    def test_arabic_messagebox_wrappers_keep_standard_return_contracts(self):
        wrappers = self.app.messagebox
        expected = {
            'showinfo': 'ok', 'showwarning': 'ok', 'showerror': 'ok',
            'askyesno': False, 'askokcancel': False, 'askretrycancel': False,
            'askyesnocancel': None, 'askquestion': 'no',
        }
        for name, value in expected.items():
            with self.subTest(name=name), mock.patch.object(self.app, '_custom_message_dialog', return_value=value) as dialog:
                self.assertEqual(getattr(wrappers, name)('عنوان عربي', 'نص عربي', parent='owner'), value)
                dialog.assert_called_once_with(name, 'عنوان عربي', 'نص عربي', parent='owner')

    def test_student_profile_exposes_pdf_print_action(self):
        a = self.make_app(self.admin)
        start = len(COMMANDS)
        a.show_student_profile(self.s1['id'])
        new_commands = COMMANDS[start:]
        print_command = next((cmd for cmd in new_commands if getattr(cmd, '__name__', '') == 'print_student_profile'), None)
        self.assertIsNotNone(print_command)
        with mock.patch.object(core, 'create_student_history_report_pdf', return_value=Path('student.pdf')) as create, \
             mock.patch.object(a, '_show_print_options') as show_print:
            print_command()
        create.assert_called_once()
        show_print.assert_called_once_with(Path('student.pdf'), mock.ANY, title='الملف التاريخي للتلميذ — معاينة وطباعة')

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

    def test_saved_primary_guardian_autofills_exit_permit_form(self):
        a = self.make_app(self.sup)
        full_id = "123456789012345678"
        with mock.patch.object(core, "protect_sensitive_text", lambda value: "DPAPI1:" + value[::-1]), \
             mock.patch.object(core, "reveal_sensitive_text", lambda value: value.split(":", 1)[1][::-1]):
            core.create_guardian_for_student(
                self.sup, self.s1["id"], "محمد ولي تجريبي", "الأب", full_id,
                "0550123456", make_primary=True,
            )
            form = self.app.RecordForm(a, "exit_permit")
            form.set_student(self.s1)
            self.assertEqual(form.vars["guardian_name"].get(), "محمد ولي تجريبي")
            self.assertEqual(form.vars["relationship"].get(), "الأب")
            self.assertEqual(form.vars["national_id"].get(), full_id)
            self.assertEqual(form.guardian_id_var.get(), full_id)
            self.assertTrue(form.guardian_combo.get())

    def test_student_autocomplete_clears_selection_when_query_changes(self):
        a = self.make_app(self.sup)
        form = self.app.RecordForm(a, "late")
        form.set_student(self.s1)
        self.assertIsNotNone(form.student)
        form.student_var.set("اسم غير مطابق")
        form._refresh_student_suggestions()
        self.assertIsNone(form.student)
        self.assertEqual(form.student_label.get(), self.app.rtl_display("لم يتم اختيار التلميذ"))

    def test_live_student_entry_keeps_query_text_and_uses_independent_suggestions(self):
        a = self.make_app(self.sup)
        form = self.app.RecordForm(a, "late")
        form.student_var.set("أحمد")
        form._refresh_student_suggestions()
        self.assertIsInstance(form.student_combo, self.app.ttk.Entry)
        self.assertEqual(form.student_var.get(), "أحمد")
        self.assertTrue(hasattr(form, "student_suggestions"))

    def test_live_teacher_entry_keeps_query_text_and_uses_independent_suggestions(self):
        a = self.make_app(self.admin)
        form = self.app.RecordForm(a, "special_entry")
        form.teacher_var.set("مح")
        form._refresh_teacher_suggestions()
        self.assertIsInstance(form.teacher_combo, self.app.ttk.Entry)
        self.assertEqual(form.teacher_var.get(), "مح")
        self.assertTrue(hasattr(form, "teacher_suggestions"))

    def test_teacher_template_download_creates_excel_header_file(self):
        import openpyxl
        a = self.make_app(self.admin)
        COMMANDS.clear()
        a.manage_teachers()
        self.assertIn("download_template", [getattr(cmd, "__name__", "") for cmd in COMMANDS])
        target = TEST_ROOT / "teacher-template-test.xlsx"
        FD = type(self.app._tk.filedialog)
        FD.answer = str(target)
        command = next(cmd for cmd in COMMANDS if getattr(cmd, "__name__", "") == "download_template")
        command()
        FD.answer = ""
        self.assertTrue(target.exists())
        workbook = openpyxl.load_workbook(target, read_only=True)
        self.assertEqual(tuple(next(workbook.active.iter_rows(values_only=True))),
                         ("اسم الأستاذ", "المادة", "الأقسام"))
        workbook.close()

    def test_student_search_labels_show_only_full_name_and_class(self):
        app = self.make_app(self.sup)
        form = self.app.RecordForm(app, "late")
        form.student_var.set("أحمد")
        form._refresh_student_suggestions()
        labels = list(form.student_option_map)
        self.assertTrue(labels)
        self.assertTrue(all(self.s1["student_key"] not in label for label in labels))
        self.assertTrue(all(self.s1["class_name"] in label for label in labels))
        form.set_student(self.s1)
        self.assertNotIn(self.s1["student_key"], form.student_var.get())
        self.assertIn(self.s1["class_name"], form.student_var.get())

    def test_special_entry_form_creates_expulsion_with_teacher_and_subject(self):
        a = self.make_app(self.sup)
        form = self.app.RecordForm(a, "special_entry")
        form.student = self.s1
        form.special_reason.set("الطرد من الحصة")
        teacher = core.create_teacher_as(self.admin, "الأستاذ التجريبي", "رياضيات")
        form.teacher_var.set("الأستاذ التجريبي — رياضيات")
        form.subject_var.set("رياضيات")
        form.selected_teacher_id = int(teacher["id"])
        with mock.patch.object(a, "show_record") as show_record:
            form.save()
        show_record.assert_called_once()
        report = core.get_expulsion_report(core.date_today(), core.date_today(), "الأستاذ التجريبي")
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["records"][0]["subject"], "رياضيات")

    def test_expulsion_report_window_loads_teacher_filtered_rows(self):
        a = self.make_app(self.sup)
        core.create_special_entry(self.s1, self.sup, "class_expulsion", "أستاذ الواجهة", "علوم")
        core.create_special_entry(self.s1, self.sup, "administration_visit")
        a.open_expulsion_report()  # constructs date pickers, teacher filter, table, and initial query
        self.assertEqual(core.get_expulsion_report(core.date_today(), core.date_today(), "أستاذ الواجهة")["total"], 1)

    def test_expulsion_report_pdf_export_and_print_actions_reach_shared_print_dialog(self):
        a = self.make_app(self.sup)
        core.create_special_entry(self.s1, self.sup, "class_expulsion", "أستاذ للطباعة", "علوم")
        COMMANDS.clear()
        a.open_expulsion_report()
        actions = {getattr(cmd, '__name__', ''): cmd for cmd in COMMANDS}
        self.assertIn('export_pdf', actions)
        self.assertIn('print_expulsion_report', actions)
        target = TEST_ROOT / 'expulsions.pdf'
        FD = type(self.app._tk.filedialog)
        with mock.patch.object(core, 'create_expulsion_report_pdf', return_value=target) as create, \
             mock.patch.object(a, '_show_print_options') as show_print:
            FD.answer = str(target)
            actions['export_pdf']()
            actions['print_expulsion_report']()
        self.assertEqual(create.call_count, 2)
        self.assertEqual(show_print.call_count, 2)
        self.assertEqual(show_print.call_args.kwargs['title'], 'تقرير الطرد حسب الأستاذ')
        FD.answer = ''

    def test_long_absence_ui_offers_combined_medical_and_guardian_choice(self):
        a = self.make_app(self.sup)
        form = self.app.RecordForm(a, "absence_return")
        form.vars["absence_start"].set((__import__("datetime").date.today() - __import__("datetime").timedelta(days=4)).isoformat())
        form.vars["return_date"].set(core.date_today())
        form._update_absence_rule()
        expected_display_values = tuple(
            self.app.rtl_display(value)
            for value in ("حضور ولي الأمر شخصيًا", "شهادة طبية + حضور ولي الأمر شخصيًا")
        )
        self.assertEqual(tuple(form.method_combo.kw["values"]), expected_display_values)
        form.proof_method.set("شهادة طبية + حضور ولي الأمر شخصيًا")
        form._refresh_absence_method()
        self.assertTrue(form.medical_frame.kw.get("_packed", True))

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

    def test_combobox_runtime_values_are_shaped_and_keep_logical_values(self):
        owner = self.app._tk.Tk()
        combo = self.app.ttk.Combobox(owner, values=('شهادة طبية', 'حضور ولي الأمر شخصيًا'), state='readonly')
        initial = tuple(combo.kw['values'])
        self.assertEqual(initial, tuple(self.app.rtl_display(v) for v in ('شهادة طبية', 'حضور ولي الأمر شخصيًا')))
        combo.configure(values=('حضور ولي الأمر شخصيًا', 'شهادة طبية + حضور ولي الأمر شخصيًا'))
        self.assertEqual(tuple(combo.kw['values']), tuple(self.app.rtl_display(v) for v in ('حضور ولي الأمر شخصيًا', 'شهادة طبية + حضور ولي الأمر شخصيًا')))
        combo.set('شهادة طبية + حضور ولي الأمر شخصيًا')
        self.assertEqual(combo.get(), 'شهادة طبية + حضور ولي الأمر شخصيًا')

    def test_treeview_displays_rtl_but_returns_logical_values_for_sorting(self):
        owner = self.app._tk.Tk()
        tree = self.app.ttk.Treeview(owner, columns=('student', 'class_name'), show='headings')
        tree.heading('student', text='اسم التلميذ')
        tree.insert('', 'end', iid='1', values=('أحمد بن علي', 'أولى علوم'))
        self.assertEqual(tree.rows['1'][0], self.app.rtl_display('أحمد بن علي'))
        self.assertEqual(tree.set('1', 'student'), 'أحمد بن علي')
        tree.set('1', 'student', 'سارة محمد')
        self.assertEqual(tree.set('1', 'student'), 'سارة محمد')

    def test_arabic_display_shaping_is_idempotent_and_preserves_latin(self):
        displayed = self.app.rtl_display('رخصة الدخول — SchoolPass 2.5.1')
        self.assertEqual(self.app.rtl_display(displayed), displayed)
        self.assertEqual(self.app.rtl_display('SchoolPass 2.5.1'), 'SchoolPass 2.5.1')

    def test_rounded_entry_layout_replacement_keeps_text_elements(self):
        layout = [
            ('Entry.border', {
                'sticky': 'nswe',
                'children': [
                    ('Entry.padding', {
                        'sticky': 'nswe',
                        'children': [('Entry.textarea', {'sticky': 'nswe'})],
                    }),
                ],
            }),
        ]
        rounded, replaced = self.app._replace_ttk_layout_element(
            layout, ('.field', '.border'), 'SchoolPassRounded_Entry_Test'
        )
        self.assertTrue(replaced)
        self.assertEqual(rounded[0][0], 'SchoolPassRounded_Entry_Test')
        self.assertEqual(rounded[0][1]['children'][0][0], 'Entry.padding')
        self.assertEqual(
            rounded[0][1]['children'][0][1]['children'][0][0],
            'Entry.textarea',
        )

    def test_arabic_rtl_rendering_covers_titles_labels_text_canvas_and_entries(self):
        owner = self.app._tk.Tk()
        title = 'لوحة المتابعة — SchoolPass 4.8.0'
        owner.title(title)
        self.assertEqual(owner.title(), self.app.rtl_display(title))

        child = self.app._tk.Toplevel(owner)
        child_title = 'إصدار رخصة الدخول'
        child.title(child_title)
        self.assertEqual(child.title(), self.app.rtl_display(child_title))

        label_text = 'إدارة التلاميذ'
        label = self.app.ttk.Label(owner, text=label_text)
        self.assertEqual(label.kw['text'], self.app.rtl_display(label_text))
        self.assertEqual(label.kw['justify'], 'right')
        self.assertEqual(label.kw['anchor'], 'e')
        label.configure(text='إدارة الأولياء')
        self.assertEqual(label.kw['text'], self.app.rtl_display('إدارة الأولياء'))

        source = self.app.StringVar(value='اسم التلميذ')
        dynamic_label = self.app.ttk.Label(owner, textvariable=source)
        source.set('بيانات الولي')
        dynamic_label._schoolpass_refresh_textvariable()
        self.assertEqual(dynamic_label._schoolpass_text_display.get(), self.app.rtl_display('بيانات الولي'))

        entry = self.app.ttk.Entry(owner)
        self.assertEqual(entry.kw['justify'], 'right')

        text = self.app._tk.Text(owner)
        text.insert('end', 'ملاحظات الإدارة')
        self.assertEqual(text.insertions[-1][1], self.app.rtl_display('ملاحظات الإدارة'))
        self.assertEqual(text.tags['_schoolpass_rtl']['justify'], 'right')
        self.assertEqual(text.tag_ranges[-1][0], '_schoolpass_rtl')

        canvas = self.app._tk.Canvas(owner)
        canvas.create_text(20, 20, text='إحصائيات الدخول')
        self.assertEqual(canvas.canvas_texts[-1]['text'], self.app.rtl_display('إحصائيات الدخول'))
        canvas.itemconfigure('chart-label', text='عدد التلاميذ')
        self.assertEqual(canvas.canvas_config['text'], self.app.rtl_display('عدد التلاميذ'))

    def test_arabic_tree_headings_are_shaped_and_right_aligned(self):
        owner = self.app._tk.Tk()
        tree = self.app.ttk.Treeview(owner, columns=('student',), show='headings')
        tree.heading('student', text='اسم التلميذ')
        self.assertEqual(tree.column('student')['anchor'], 'e')
        tree.column('student', width=220)
        self.assertEqual(tree.headings['student']['text'], self.app.rtl_display('اسم التلميذ'))
        self.assertEqual(tree.headings['student']['anchor'], 'e')
        self.assertEqual(tree.columns_cfg['student']['anchor'], 'e')

    def test_mixed_arabic_latin_digits_and_multiline_text_remain_idempotent(self):
        original = 'رقم الرخصة: SP-2026-001\nاسم التلميذ: أحمد بن علي\nالتاريخ: 04-11-2026'
        displayed = self.app.rtl_display(original)
        self.assertNotEqual(displayed, original)
        self.assertEqual(self.app.rtl_display(displayed), displayed)
        self.assertIn('SP-2026-001', displayed)
        self.assertIn('04-11-2026', displayed)

    def test_dashboard_reflows_for_small_and_large_windows(self):
        app = self.make_app(self.admin)
        app._adapt_dashboard_layout(520)
        self.assertEqual(app._dashboard_layout, 'stacked')
        self.assertEqual(app.maximize_button.kw.get('text'), self.app.rtl_display('□ تكبير'))
        app._adapt_dashboard_layout(1200)
        self.assertEqual(app._dashboard_layout, 'side_by_side')
        self.assertEqual(app.maximize_button.kw.get('text'), self.app.rtl_display('□ استعادة / تكبير'))
        self.assertEqual(app.update_button.kw.get('text'), self.app.rtl_display('بحث عن تحديثات'))

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
