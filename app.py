# -*- coding: utf-8 -*-
"""SchoolPass — Windows GUI (Tkinter). All business rules and permission checks live in schoolpass_core.py."""
from __future__ import annotations

import json
import os
import queue
import socket
import sys
import time
import re
import webbrowser
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, date
from pathlib import Path
from tkinter import (
    Tk, Toplevel, StringVar, BooleanVar, messagebox, filedialog, colorchooser,
    END, BOTH, LEFT, RIGHT, X, Y, E, Canvas
)
from tkinter import ttk

import schoolpass_core as core
from schoolpass_core import (
    APP_VERSION, SCHOOL_NAME, SERVER_PORT, AUTO_LOCK_SECONDS, DATA_DIR, LOG_PATH,
    PermissionDenied, db_connect, setting, set_setting, audit, date_today, now_iso,
    kind_label, status_label, student_name, record_by_id, record_by_token, get_user,
)

USER_ERRORS = (ValueError, RuntimeError, PermissionDenied, FileNotFoundError, OSError)
_INSTANCE_LOCK: socket.socket | None = None


# ---------------------------------------------------------------- UI helpers
def _window_background() -> str:
    try:
        if core.setting('theme', 'light') == 'dark':
            return '#17211E'
        _primary, secondary = core.get_theme_colors()
        return secondary
    except Exception:
        return '#F2F6F4'


def configure_window(win, title: str, geometry: str = '900x620'):
    win.title(title)
    win.geometry(geometry)
    win.minsize(760, 520)
    try:
        win.configure(background=_window_background())
    except Exception:
        pass


def _shade(hex_color: str, factor: float = 0.82) -> str:
    """Darken a validated #RRGGBB color for hover states."""
    value = hex_color.lstrip('#')
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    return '#%02X%02X%02X' % tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _text_on(hex_color: str) -> str:
    return core.text_color_for(hex_color)


def _install_toplevel_background():
    """Apply the selected secondary color to every Tk top-level, not only the main window."""
    if getattr(Toplevel, '_schoolpass_theme_installed', False):
        return
    original_init = Toplevel.__init__
    def themed_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            self.configure(background=_window_background())
        except Exception:
            pass
    Toplevel.__init__ = themed_init
    Toplevel._schoolpass_theme_installed = True

_install_toplevel_background()


def make_style(root: Tk, primary: str | None = None, secondary: str | None = None):
    """Apply the institutional light/dark visual system using readable contrast."""
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    try:
        dark = core.setting('theme', 'light') == 'dark'
        saved_primary, saved_secondary = core.get_theme_colors()
    except Exception:
        dark = False
        saved_primary, saved_secondary = '#103E34', '#F2F6F4'
    primary_color = primary or saved_primary
    secondary_color = secondary or saved_secondary
    try:
        primary_color = core._validate_hex_color(primary_color, 'اللون الرئيسي')
        secondary_color = core._validate_hex_color(secondary_color, 'اللون الثانوي')
    except Exception:
        primary_color, secondary_color = '#103E34', '#F2F6F4'
    if dark:
        palette = {
            'bg': '#17211E', 'surface': '#22312C', 'ink': '#E8F0EB', 'muted': '#B7C7BE',
            'green': _shade(primary_color, 0.42), 'green2': primary_color, 'green_soft': '#29483D',
            'gold': '#C9A34A', 'border': '#3D5148', 'sidebar_text': '#EAF3EF',
            'sidebar_muted': '#B7CEC3', 'alert_bg': '#4B351F', 'alert_fg': '#FFD49B',
            'good_bg': '#24483A', 'good_fg': '#BDE8CC', 'field': '#293B34',
            'heading': '#2B4038', 'selected': '#315A49', 'canvas': '#22312C'
        }
    else:
        palette = {
            'bg': secondary_color, 'surface': '#FFFFFF', 'ink': core.text_color_for(secondary_color), 'muted': '#667A73',
            'green': primary_color, 'green2': _shade(primary_color, 0.86), 'green_soft': '#E4F0EA',
            'gold': '#DAB65E', 'border': '#E1EAE5', 'sidebar_text': '#FFFFFF',
            'sidebar_muted': '#C6DAD2', 'alert_bg': '#FFF4E5', 'alert_fg': '#8A4B08',
            'good_bg': '#E5F4EC', 'good_fg': '#176342', 'field': '#FFFFFF',
            'heading': '#E8F0EC', 'selected': '#D9EAE1', 'canvas': '#FFFFFF'
        }
    bg, surface, ink, muted = palette['bg'], palette['surface'], palette['ink'], palette['muted']
    green, green2, gold, border = palette['green'], palette['green2'], palette['gold'], palette['border']
    root.configure(background=bg)
    style.configure('.', font=('Segoe UI', 10), foreground=ink)
    style.configure('TFrame', background=bg)
    style.configure('Surface.TFrame', background=surface, relief='flat')
    style.configure('Sidebar.TFrame', background=green)
    style.configure('TLabel', background=bg, foreground=ink, font=('Segoe UI', 10))
    style.configure('Title.TLabel', background=bg, foreground=green2 if dark else green, font=('Segoe UI', 21, 'bold'))
    style.configure('Subtitle.TLabel', background=bg, foreground=muted, font=('Segoe UI', 10))
    style.configure('Badge.TLabel', background=palette['green_soft'], foreground=palette['sidebar_text'] if dark else green,
                    padding=(12, 9), font=('Segoe UI', 9, 'bold'))
    style.configure('Hint.TLabel', background=surface, foreground=muted, font=('Segoe UI', 9))
    style.configure('Status.TLabel', background=palette['green_soft'], foreground=ink, anchor='e', padding=(12, 6))
    style.configure('Section.TLabel', background=surface, foreground=green2 if dark else green, font=('Segoe UI', 13, 'bold'))
    style.configure('CardTitle.TLabel', background=surface, foreground=muted, font=('Segoe UI', 9, 'bold'))
    style.configure('CardValue.TLabel', background=surface, foreground=palette['sidebar_muted'] if dark else green, font=('Segoe UI', 25, 'bold'))
    style.configure('SidebarTitle.TLabel', background=green, foreground=palette['sidebar_text'], font=('Segoe UI', 18, 'bold'))
    style.configure('SidebarSub.TLabel', background=green, foreground=palette['sidebar_muted'], font=('Segoe UI', 9))
    style.configure('Nav.TButton', background=green, foreground='#EAF3EF', borderwidth=0,
                    focusthickness=0, padding=(14, 12), anchor='e', font=('Segoe UI', 10, 'bold'))
    style.map('Nav.TButton', background=[('active', '#28624E' if dark else '#1B5B4A'), ('pressed', '#0B3028')],
              foreground=[('active', '#FFFFFF')])
    style.configure('TButton', background=palette['heading'], foreground=ink, borderwidth=0,
                    focusthickness=0, padding=(12, 10), font=('Segoe UI', 9, 'bold'))
    style.map('TButton', background=[('active', palette['selected']), ('pressed', palette['selected'])])
    style.configure('Accent.TButton', background=green2, foreground='#FFFFFF', borderwidth=0,
                    focusthickness=0, padding=(15, 11), font=('Segoe UI', 10, 'bold'))
    style.map('Accent.TButton', background=[('active', '#23775B'), ('pressed', '#14523E')],
              foreground=[('disabled', '#D6E2DD')])
    style.configure('Gold.TButton', background=gold, foreground='#27372F', borderwidth=0,
                    padding=(14, 10), font=('Segoe UI', 10, 'bold'))
    style.map('Gold.TButton', background=[('active', '#CDA849'), ('pressed', '#B99036')])
    style.configure('Treeview', font=('Segoe UI', 10), rowheight=40, background=surface,
                    fieldbackground=surface, foreground=ink, borderwidth=0, relief='flat')
    style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'), background=palette['heading'],
                    foreground=ink, padding=(10, 12), relief='flat')
    style.map('Treeview', background=[('selected', palette['selected'])], foreground=[('selected', ink)])
    style.configure('TEntry', padding=9, fieldbackground=palette['field'], foreground=ink)
    style.configure('TCombobox', padding=9, fieldbackground=palette['field'], foreground=ink)
    style.configure('TNotebook', background=bg, borderwidth=0)
    style.configure('TNotebook.Tab', padding=(15, 10), font=('Segoe UI', 9, 'bold'))
    style.configure('Horizontal.TProgressbar', troughcolor=border, background=green2)
    style.configure('Vertical.TScrollbar', background=palette['heading'], troughcolor=bg,
                    bordercolor=bg, arrowcolor=muted, width=12)
    style.configure('Horizontal.TScrollbar', background=palette['heading'], troughcolor=bg,
                    bordercolor=bg, arrowcolor=muted, width=12)
    style.map('TCombobox', fieldbackground=[('readonly', palette['field'])],
              foreground=[('readonly', ink)])
    style.map('TNotebook.Tab', background=[('selected', surface), ('active', palette['selected'])],
              foreground=[('selected', green if not dark else palette['sidebar_text'])])
    style.configure('Alert.TLabel', background=palette['alert_bg'], foreground=palette['alert_fg'], padding=(12, 9), font=('Segoe UI', 9, 'bold'))
    style.configure('Good.TLabel', background=palette['good_bg'], foreground=palette['good_fg'], padding=(12, 9), font=('Segoe UI', 9, 'bold'))


RTL_SHAPING_ENABLED = True

def rtl_display(value) -> str:
    """Shape each line independently so paragraph wrapping cannot reorder whole lines."""
    text = str(value if value is not None else '')
    if not text or not RTL_SHAPING_ENABLED:
        return text
    try:
        return "\n".join(core._arabic_visual(line) if line.strip() else line for line in text.split("\n"))
    except (ImportError, RuntimeError):
        return text

def _wrap_messageboxes_once():
    """Make every Tk message box use the same Arabic shaping path."""
    if getattr(messagebox, '_schoolpass_rtl_wrapped', False):
        return
    for _name in ('showinfo','showwarning','showerror','askyesno','askokcancel','askretrycancel','askyesnocancel'):
        _original = getattr(messagebox, _name)
        def _make(original):
            def _wrapped(title=None, message=None, *args, **kwargs):
                return original(rtl_display(title) if title is not None else title,
                                rtl_display(message) if message is not None else message, *args, **kwargs)
            return _wrapped
        setattr(messagebox, _name, _make(_original))
    messagebox._schoolpass_rtl_wrapped = True

_wrap_messageboxes_once()


class DisplayStringVar(StringVar):
    """StringVar for read-only labels; input variables must remain logical/unmodified."""
    def set(self, value):
        super().set(rtl_display(value))


class ComboStringVar(StringVar):
    """Store/read logical Arabic values while Tk displays shaped visual strings."""
    def __init__(self, values, master=None, value=''):
        self._logical_to_display = {str(item): rtl_display(item) for item in values}
        self._display_to_logical = {display: logical for logical, display in self._logical_to_display.items()}
        super().__init__(master=master, value=self._logical_to_display.get(str(value), rtl_display(value)))

    def get(self):
        value = super().get()
        return self._display_to_logical.get(value, value)

    def set(self, value):
        super().set(self._logical_to_display.get(str(value), rtl_display(value)))


def _install_rtl_widget_adapters():
    """Shape static labels/buttons, table text and read-only combo choices in Tk."""
    if getattr(ttk, '_schoolpass_rtl_installed', False):
        return

    for class_name in ('Label', 'Button', 'Checkbutton', 'Radiobutton'):
        if not hasattr(ttk, class_name):
            continue
        original = getattr(ttk, class_name)

        class ArabicTextWidget(original):
            def __init__(self, master=None, **kwargs):
                if isinstance(kwargs.get('text'), str):
                    kwargs['text'] = rtl_display(kwargs['text'])
                super().__init__(master, **kwargs)

            def configure(self, cnf=None, **kwargs):
                if isinstance(cnf, dict):
                    cnf = dict(cnf)
                    if isinstance(cnf.get('text'), str):
                        cnf['text'] = rtl_display(cnf['text'])
                if isinstance(kwargs.get('text'), str):
                    kwargs['text'] = rtl_display(kwargs['text'])
                return super().configure(cnf, **kwargs)

            config = configure

        ArabicTextWidget.__name__ = f'ArabicAware{class_name}'
        setattr(ttk, class_name, ArabicTextWidget)

    original_combo = ttk.Combobox

    class ArabicAwareCombobox(original_combo):
        def __init__(self, master=None, **kwargs):
            self._schoolpass_original_values = tuple(kwargs.get('values', ()))
            self._schoolpass_display_to_logical = {rtl_display(v): str(v) for v in self._schoolpass_original_values}
            self._schoolpass_logical_to_display = {str(v): rtl_display(v) for v in self._schoolpass_original_values}
            if 'values' in kwargs:
                kwargs['values'] = tuple(self._schoolpass_logical_to_display[str(v)] for v in self._schoolpass_original_values)
            super().__init__(master, **kwargs)

        def get(self):
            value = super().get()
            return self._schoolpass_display_to_logical.get(value, value)

        def set(self, value):
            super().set(self._schoolpass_logical_to_display.get(str(value), rtl_display(value)))

    ttk.Combobox = ArabicAwareCombobox

    original_tree = ttk.Treeview

    class ArabicAwareTreeview(original_tree):
        def heading(self, column, option=None, **kwargs):
            if isinstance(kwargs.get('text'), str):
                kwargs['text'] = rtl_display(kwargs['text'])
            base_method = getattr(super(), 'heading', None)
            return base_method(column, option, **kwargs) if base_method else None

        def insert(self, parent, index, iid=None, **kwargs):
            if 'values' in kwargs:
                kwargs['values'] = tuple(rtl_display(v) if isinstance(v, str) else v for v in kwargs['values'])
            return super().insert(parent, index, iid=iid, **kwargs)

    ttk.Treeview = ArabicAwareTreeview
    ttk._schoolpass_rtl_installed = True


_install_rtl_widget_adapters()


def show_error(parent, title: str, exc: Exception):
    messagebox.showerror(title, str(exc), parent=parent)


def ask_password(parent, title: str, prompt: str) -> str | None:
    dialog = Toplevel(parent)
    dialog.title(title)
    dialog.geometry('430x190')
    dialog.transient(parent)
    dialog.grab_set()
    result = {'value': None}
    ttk.Label(dialog, text=prompt).pack(pady=(24, 8))
    v = StringVar()
    ent = ttk.Entry(dialog, textvariable=v, show='●', width=32, justify='right')
    ent.pack(pady=5)

    def ok():
        result['value'] = v.get()
        dialog.destroy()
    bar = ttk.Frame(dialog)
    bar.pack(pady=12)
    ttk.Button(bar, text='متابعة', command=ok, style='Accent.TButton').pack(side=RIGHT, padx=5)
    ttk.Button(bar, text='إلغاء', command=dialog.destroy).pack(side=RIGHT, padx=5)
    ent.bind('<Return>', lambda _e: ok())
    ent.focus_set()
    parent.wait_window(dialog)
    return result['value']


def ask_text(parent, title: str, prompt: str) -> str | None:
    win = Toplevel(parent)
    win.title(title)
    win.geometry('470x230')
    win.transient(parent)
    win.grab_set()
    ttk.Label(win, text=prompt).pack(pady=(20, 8))
    value = StringVar()
    ent = ttk.Entry(win, textvariable=value, justify='right', width=48)
    ent.pack(padx=20, fill=X)
    result = {'value': None}

    def ok():
        result['value'] = value.get()
        win.destroy()
    bar = ttk.Frame(win)
    bar.pack(pady=16)
    ttk.Button(bar, text='تأكيد', command=ok, style='Accent.TButton').pack(side=RIGHT, padx=4)
    ttk.Button(bar, text='إلغاء', command=win.destroy).pack(side=RIGHT, padx=4)
    ent.bind('<Return>', lambda _e: ok())
    ent.focus_set()
    parent.wait_window(win)
    return result['value']


def show_recovery_code(parent, code: str):
    """Modal window that shows the one-time recovery code (copyable)."""
    win = Toplevel(parent)
    win.title('رمز استرداد حساب المدير')
    win.geometry('560x330')
    win.transient(parent)
    win.grab_set()
    ttk.Label(win, text='رمز الاسترداد — يظهر مرة واحدة فقط', style='Title.TLabel').pack(pady=(18, 8))
    ttk.Label(win, wraplength=500, justify='right',
              text='إذا نسيت كلمة مرور المدير فهذا الرمز هو الوسيلة الوحيدة لاستعادة الدخول دون فقدان البيانات. '
                   'اكتبه على ورقة واحتفظ بها في مكان آمن (مثل خزانة المدير)، ولا تحفظه في الحاسوب نفسه.').pack(pady=6)
    ent = ttk.Entry(win, justify='center', font=('Consolas', 15), width=34)
    ent.insert(0, code)
    ent.configure(state='readonly')
    ent.pack(pady=14)

    def copy():
        win.clipboard_clear()
        win.clipboard_append(code)
    bar = ttk.Frame(win)
    bar.pack(pady=10)
    ttk.Button(bar, text='لقد دوّنته وحفظته', command=win.destroy, style='Accent.TButton').pack(side=RIGHT, padx=5)
    ttk.Button(bar, text='نسخ', command=copy).pack(side=RIGHT, padx=5)
    parent.wait_window(win)


class SetupDialog:
    def __init__(self, root: Tk):
        self.result = False
        win = Toplevel(root)
        win.title('الإعداد الأول — إنشاء المدير')
        win.geometry('520x390')
        win.transient(root)
        win.grab_set()
        ttk.Label(win, text='مرحبًا بك في SchoolPass', style='Title.TLabel').pack(pady=(20, 5))
        ttk.Label(win, text='أنشئ حساب المدير. لا توجد كلمة مرور افتراضية، ولا تُحفظ كلمة المرور كنص واضح.').pack(pady=(0, 14))
        form = ttk.Frame(win)
        form.pack(fill=X, padx=40)
        self.user = StringVar(value='')
        self.name = StringVar(value='')
        self.password = StringVar()
        self.confirm = StringVar()
        rows = [('اسم الدخول (مثال: director)', self.user, ''), ('اسم المدير الظاهر', self.name, ''),
                ('كلمة المرور (8 أحرف على الأقل)', self.password, '●'), ('تأكيد كلمة المرور', self.confirm, '●')]
        for label, var, show in rows:
            row = ttk.Frame(form)
            row.pack(fill=X, pady=5)
            ttk.Label(row, text=label, width=27).pack(side=RIGHT)
            ttk.Entry(row, textvariable=var, show=show, justify='right', width=29).pack(side=RIGHT, fill=X, expand=True)

        def create():
            if self.password.get() != self.confirm.get():
                messagebox.showerror('عدم تطابق', 'كلمتا المرور غير متطابقتين.', parent=win)
                return
            try:
                _uid, code = core.bootstrap_admin(self.user.get(), self.name.get(), self.password.get())
            except USER_ERRORS as exc:
                show_error(win, 'تعذر إنشاء الحساب', exc)
                return
            self.result = True
            win.grab_release()
            show_recovery_code(win, code)
            win.destroy()
        ttk.Button(win, text='إنشاء حساب المدير', command=create, style='Accent.TButton').pack(pady=18)
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        root.wait_window(win)


class RecoveryDialog:
    """Reset an admin password with the recovery code written down at first setup."""

    def __init__(self, parent):
        win = Toplevel(parent)
        win.title('استرداد حساب المدير')
        win.geometry('520x400')
        win.transient(parent)
        win.grab_set()
        ttk.Label(win, text='استرداد كلمة مرور المدير', style='Title.TLabel').pack(pady=(18, 8))
        form = ttk.Frame(win)
        form.pack(fill=X, padx=36)
        vars_ = {k: StringVar() for k in ('user', 'code', 'pw', 'pw2')}
        for key, label, show in [('user', 'اسم دخول المدير', ''), ('code', 'رمز الاسترداد', ''),
                                 ('pw', 'كلمة المرور الجديدة', '●'), ('pw2', 'تأكيد كلمة المرور', '●')]:
            row = ttk.Frame(form)
            row.pack(fill=X, pady=5)
            ttk.Label(row, text=label, width=22).pack(side=RIGHT)
            ttk.Entry(row, textvariable=vars_[key], show=show, justify='right').pack(side=RIGHT, fill=X, expand=True)

        def go():
            if vars_['pw'].get() != vars_['pw2'].get():
                messagebox.showerror('عدم تطابق', 'كلمتا المرور غير متطابقتين.', parent=win)
                return
            try:
                new_code = core.recover_admin_password(vars_['user'].get(), vars_['code'].get(), vars_['pw'].get())
            except USER_ERRORS as exc:
                show_error(win, 'تعذر الاسترداد', exc)
                return
            messagebox.showinfo('تم', 'تم تغيير كلمة المرور. سيظهر الآن رمز استرداد جديد؛ الرمز القديم لم يعد صالحًا.', parent=win)
            win.grab_release()
            show_recovery_code(win, new_code)
            win.destroy()
        ttk.Button(win, text='تغيير كلمة المرور', command=go, style='Accent.TButton').pack(pady=16)
        parent.wait_window(win)


class LoginDialog:
    def __init__(self, root: Tk, lock_mode: bool = False, prefill: str = ''):
        self.user = None
        win = Toplevel(root)
        win.title('الجلسة مقفلة — SchoolPass' if lock_mode else 'تسجيل الدخول — SchoolPass')
        win.geometry('470x400')
        win.transient(root)
        win.grab_set()
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        ttk.Label(win, text=f'SchoolPass {APP_VERSION}', style='Title.TLabel').pack(pady=(27, 3))
        ttk.Label(win, text=setting('school_name', SCHOOL_NAME)).pack(pady=(0, 16))
        form = ttk.Frame(win)
        form.pack(fill=X, padx=55)
        self.username = StringVar(value=prefill or setting('last_username', ''))
        self.password = StringVar()
        ttk.Label(form, text='اسم المستخدم').pack(anchor=E)
        user_entry = ttk.Entry(form, textvariable=self.username, justify='right')
        user_entry.pack(fill=X, pady=(3, 12))
        ttk.Label(form, text='كلمة المرور').pack(anchor=E)
        pass_entry = ttk.Entry(form, textvariable=self.password, show='●', justify='right')
        pass_entry.pack(fill=X, pady=(3, 8))
        err = DisplayStringVar()
        ttk.Label(win, textvariable=err, foreground='#b42318', wraplength=380).pack(pady=4)

        def login():
            user, msg = core.authenticate(self.username.get(), self.password.get())
            if not user:
                err.set(msg)
                self.password.set('')
                pass_entry.focus_set()
                return
            self.user = user
            set_setting('last_username', user['username'])
            audit(user['id'], 'تسجيل الدخول' if not lock_mode else 'فتح الجلسة بعد القفل', 'user', user['id'], user['username'])
            win.destroy()
        ttk.Button(win, text='دخول', command=login, style='Accent.TButton').pack(pady=10)
        ttk.Button(win, text='نسيت كلمة مرور المدير؟', command=lambda: RecoveryDialog(win)).pack(pady=2)
        pass_entry.bind('<Return>', lambda _e: login())
        (pass_entry if self.username.get() else user_entry).focus_set()
        root.wait_window(win)


class StudentPicker:
    def __init__(self, parent, on_pick):
        self.on_pick = on_pick
        self.win = Toplevel(parent)
        self.win.title('اختيار التلميذ')
        self.win.geometry('720x510')
        self.win.transient(parent)
        self.win.grab_set()
        top = ttk.Frame(self.win)
        top.pack(fill=X, padx=12, pady=12)
        ttk.Label(top, text='ابحث بالاسم أو اللقب أو رقم التعريف أو القسم:').pack(side=RIGHT, padx=5)
        self.query = StringVar()
        ent = ttk.Entry(top, textvariable=self.query, justify='right')
        ent.pack(side=RIGHT, fill=X, expand=True)
        cols = ('student_key', 'name', 'class_name')
        self.tree = ttk.Treeview(self.win, columns=cols, show='headings')
        for col, title, width in [('student_key', 'رقم التعريف', 170), ('name', 'اسم التلميذ', 260), ('class_name', 'القسم', 250)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=E)
        self.tree.pack(fill=BOTH, expand=True, padx=12, pady=(0, 6))
        self.tree.bind('<Double-1>', self.pick_selected)
        bar = ttk.Frame(self.win)
        bar.pack(fill=X, padx=12, pady=10)
        ttk.Button(bar, text='اختيار', command=self.pick_selected, style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إغلاق', command=self.win.destroy).pack(side=RIGHT, padx=4)
        self.query.trace_add('write', lambda *_: self.refresh())
        ent.bind('<Return>', lambda _e: self.pick_selected())
        self.refresh()
        ent.focus_set()

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        q = self.query.get().strip()
        with db_connect() as conn:
            rows = conn.execute(
                "SELECT * FROM students WHERE active=1 AND (student_key LIKE ? OR last_name LIKE ? OR first_name LIKE ? "
                "OR (last_name||' '||first_name) LIKE ? OR (first_name||' '||last_name) LIKE ? OR class_name LIKE ?) "
                "ORDER BY last_name,first_name LIMIT 250", (f'%{q}%',) * 6).fetchall()
        for s in rows:
            self.tree.insert('', END, iid=str(s['id']), values=(s['student_key'], student_name(s), s['class_name']))

    def pick_selected(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('اختيار التلميذ', 'حدد تلميذًا من القائمة.', parent=self.win)
            return
        with db_connect() as conn:
            row = conn.execute("SELECT * FROM students WHERE id=?", (int(sel[0]),)).fetchone()
        if row:
            self.on_pick(row)
        self.win.destroy()


class RecordForm:
    def __init__(self, app: 'SchoolPassApp', kind: str):
        self.app = app
        self.kind = kind
        self.student = None
        self._saving = False
        titles = {'late': 'إصدار رخصة الدخول — تأخر', 'absence_return': 'إصدار رخصة الدخول — عودة بعد الغياب', 'exit_permit': 'إصدار رخصة خروج استثنائي'}
        self.title = titles[kind]
        self.win = Toplevel(app.root)
        self.win.title(self.title)
        self.win.geometry('760x720' if kind == 'absence_return' else '650x600')
        self.win.minsize(640, 560)
        self.win.transient(app.root)
        self.win.grab_set()
        ttk.Label(self.win, text=self.title, style='Title.TLabel').pack(pady=(16, 8))
        form = ttk.Frame(self.win)
        form.pack(fill=BOTH, expand=True, padx=25, pady=4)
        self.vars: dict[str, StringVar] = {}
        self.check_vars: dict[str, BooleanVar] = {}

        def add_row(parent, key, label, value=''):
            row = ttk.Frame(parent)
            row.pack(fill=X, pady=5)
            ttk.Label(row, text=label, width=29, anchor=E).pack(side=RIGHT, padx=(4, 8))
            v = StringVar(value=value)
            self.vars[key] = v
            ent = ttk.Entry(row, textvariable=v, justify='right')
            ent.pack(side=RIGHT, fill=X, expand=True)
            return ent

        row = ttk.Frame(form)
        row.pack(fill=X, pady=6)
        ttk.Label(row, text='التلميذ', width=29, anchor=E).pack(side=RIGHT, padx=(4, 8))
        self.student_label = DisplayStringVar(value='لم يتم اختيار التلميذ')
        ttk.Label(row, textvariable=self.student_label, relief='sunken', anchor=E, padding=7).pack(side=RIGHT, fill=X, expand=True)
        ttk.Button(row, text='اختيار...', command=self.pick_student).pack(side=RIGHT, padx=6)

        if kind == 'late':
            add_row(form, 'reason', 'سبب التأخر')
        elif kind == 'absence_return':
            start_entry = add_row(form, 'absence_start', 'تاريخ بداية الغياب (YYYY-MM-DD)', date_today())
            end_entry = add_row(form, 'return_date', 'تاريخ العودة (YYYY-MM-DD)', date_today())
            add_row(form, 'reason', 'ملاحظة (اختياري)')
            ttk.Label(form, text='يُحتسب الغياب من تاريخ بدايته إلى اليوم السابق للعودة؛ يوم العودة غير محسوب، ولا تُسجل الساعات.',
                      foreground='#526579', wraplength=680).pack(anchor=E, pady=2)

            rule_box = ttk.Frame(form, relief='groove', borderwidth=1)
            rule_box.pack(fill=X, pady=7)
            ttk.Label(rule_box, text='قاعدة قبول تبرير الغياب', font=('Segoe UI', 9, 'bold')).pack(anchor=E, padx=8, pady=(4, 0))
            self.rule_label = ttk.Label(rule_box, text='أدخل التاريخين لتحديد طريقة التبرير.', anchor=E, wraplength=650)
            self.rule_label.pack(fill=X, padx=10, pady=6)

            method_row = ttk.Frame(form)
            method_row.pack(fill=X, pady=5)
            ttk.Label(method_row, text='طريقة تبرير الغياب', width=29, anchor=E).pack(side=RIGHT, padx=(4, 8))
            self.proof_method = StringVar(value='شهادة طبية')
            self.method_combo = ttk.Combobox(method_row, textvariable=self.proof_method, state='readonly', justify='right',
                                              values=('شهادة طبية', 'حضور ولي الأمر شخصيًا'))
            self.method_combo.current(0)
            self.method_combo.pack(side=RIGHT, fill=X, expand=True)
            self.medical_frame = ttk.Frame(form)
            self.check_vars['certificate_present'] = BooleanVar(value=False)
            ttk.Checkbutton(self.medical_frame, text='أؤكد استلام الشهادة الطبية والتحقق منها.',
                            variable=self.check_vars['certificate_present']).pack(anchor=E, pady=4)
            self.guardian_frame = ttk.Frame(form, relief='groove', borderwidth=1)
            ttk.Label(self.guardian_frame, text='بيانات ولي الأمر — يجب أن يكون حاضرًا فعليًا', font=('Segoe UI', 9, 'bold')).pack(anchor=E, padx=6, pady=4)
            add_row(self.guardian_frame, 'guardian_name', 'الاسم الكامل لولي الأمر')
            add_row(self.guardian_frame, 'relationship', 'صلة القرابة')
            add_row(self.guardian_frame, 'national_id', 'رقم بطاقة الهوية الوطنية')
            self.check_vars['guardian_present'] = BooleanVar(value=False)
            ttk.Checkbutton(self.guardian_frame, text='أؤكد حضور ولي الأمر شخصيًا إلى الإدارة والتحقق من هويته.',
                            variable=self.check_vars['guardian_present']).pack(anchor=E, pady=6)
            self.method_combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_absence_method())

            def date_changed(_event=None):
                self._update_absence_rule()
            start_entry.bind('<KeyRelease>', date_changed)
            end_entry.bind('<KeyRelease>', date_changed)
            self._update_absence_rule()

        elif kind == 'exit_permit':
            add_row(form, 'reason', 'سبب الخروج')
            add_row(form, 'guardian_name', 'اسم ولي الأمر')
            add_row(form, 'relationship', 'صلة القرابة')
            add_row(form, 'national_id', 'رقم بطاقة الهوية الوطنية')
            ttk.Label(form, text='لن يُطبع رقم الهوية كاملًا ولن يوضع داخل رمز QR؛ سيظهر آخر 4 أرقام فقط على التصريح.',
                      foreground='#526579', wraplength=540).pack(anchor=E, pady=4)
        bar = ttk.Frame(self.win)
        bar.pack(fill=X, padx=25, pady=16)
        ttk.Button(bar, text='حفظ وإصدار الرخصة', command=self.save, style='Accent.TButton').pack(side=RIGHT, padx=5)
        ttk.Button(bar, text='إلغاء', command=self.win.destroy).pack(side=RIGHT, padx=5)

    def _update_absence_rule(self):
        if self.kind != 'absence_return':
            return
        start = self.vars['absence_start'].get().strip()
        end = self.vars['return_date'].get().strip()
        try:
            s, e, absence_count = core.absence_days(start, end)
            duration_days = absence_count
        except Exception:
            self.rule_label.configure(text='تحقق من التاريخين بصيغة YYYY-MM-DD. لا يمكن تحديد طريقة التبرير بعد.', foreground='#A05A00')
            self.method_combo.configure(state='readonly')
            self._refresh_absence_method()
            return
        if duration_days > core.ABSENCE_CERTIFICATE_MAX_DURATION_DAYS:
            self.rule_label.configure(text=f'مدة الغياب قبل يوم العودة {duration_days} يومًا. حضور ولي الأمر شخصيًا إلزامي حتى مع وجود شهادة طبية.', foreground='#B42318')
            self.method_combo.set('حضور ولي الأمر شخصيًا')
            self.method_combo.configure(state='disabled')
        else:
            self.rule_label.configure(text=f'مدة الغياب قبل يوم العودة {duration_days} يومًا. يمكن قبول الشهادة الطبية بعد التحقق منها أو حضور ولي الأمر شخصيًا.', foreground='#18794E')
            self.method_combo.configure(state='readonly')
        self._refresh_absence_method()

    def _refresh_absence_method(self):
        if self.kind != 'absence_return':
            return
        method = self.method_combo.get()
        self.medical_frame.pack_forget()
        self.guardian_frame.pack_forget()
        if method == 'حضور ولي الأمر شخصيًا':
            self.guardian_frame.pack(fill=X, pady=5)
        else:
            self.medical_frame.pack(fill=X, pady=5)

    def pick_student(self):
        with db_connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM students WHERE active=1').fetchone()[0]
        if not count:
            messagebox.showwarning('قائمة التلاميذ فارغة', 'استورد قائمة التلاميذ أولًا من قسم إدارة التلاميذ (يتطلب حساب المدير).', parent=self.win)
            return
        StudentPicker(self.win, self.set_student)

    def set_student(self, row):
        self.student = row
        self.student_label.set(f"{student_name(row)} — {row['class_name']} — {row['student_key']}")

    def save(self):
        if self._saving:
            return
        if not self.student:
            messagebox.showerror('بيانات ناقصة', 'يجب اختيار التلميذ أولًا.', parent=self.win)
            return
        if self.kind == 'late':
            threshold = int(core.setting('late_alert_threshold', '3') or '3')
            count = core.monthly_late_count(self.student['student_key'])
            if count + 1 >= threshold and not messagebox.askyesno(
                'إنذار التأخر المتكرر',
                f"ستُصدر رخصة التأخر رقم {count + 1} لهذا التلميذ خلال الشهر الحالي (حد التنبيه: {threshold}).\nهل تريد المتابعة؟",
                parent=self.win,
            ):
                return
        open_exit = core.find_open_exit_for_student(self.student['student_key'])
        if open_exit:
            if self.kind == 'exit_permit':
                messagebox.showwarning('رخصة خروج سابقة غير مغلقة',
                    f"لدى هذا التلميذ رخصة خروج ما زالت مفتوحة: {open_exit['record_no']} بتاريخ {open_exit['issued_at'] or open_exit['created_at']}.\n\nسجّل عودته أولًا قبل إصدار رخصة خروج جديدة.", parent=self.win)
                return
            if not messagebox.askyesno('تنبيه: رخصة خروج سابقة غير مغلقة',
                f"يوجد تصريح خروج سابق لم تُسجل عودته ({open_exit['record_no']}).\n\nتحقق من عودة التلميذ فعليًا قبل إصدار رخصة دخول جديدة. هل تريد المتابعة؟", parent=self.win):
                return
        recent = core.find_recent_duplicate(self.student['student_key'], self.kind)
        if recent and not messagebox.askyesno(
            'تنبيه: رخصة مشابهة حديثة',
            f"توجد رخصة من النوع نفسه للتلميذ خلال آخر 5 دقائق:\n{recent['record_no']} — {recent['created_at']}\n\nهل تريد المتابعة وإصدار سجل جديد؟",
            parent=self.win,
        ):
            return
        g = lambda k: self.vars[k].get().strip() if k in self.vars else ''  # noqa: E731
        uid = self.app.current_user['id']
        self._saving = True
        try:
            if self.kind == 'late':
                row = core.create_late_slip(self.student, uid, g('reason'))
            elif self.kind == 'absence_return':
                method = self.method_combo.get()
                proof_method = 'guardian' if method == 'حضور ولي الأمر شخصيًا' else 'medical_certificate'
                if proof_method == 'medical_certificate' and not self.check_vars['certificate_present'].get():
                    raise ValueError('يجب تأكيد استلام الشهادة الطبية والتحقق منها قبل إصدار الرخصة.')
                row = core.create_absence_return(
                    self.student, uid, g('absence_start'), g('return_date'), g('reason'),
                    proof_method=proof_method, certificate_present=self.check_vars['certificate_present'].get(),
                    guardian_name=g('guardian_name'), relationship=g('relationship'), national_id=g('national_id'),
                    guardian_present=self.check_vars['guardian_present'].get(),
                )
            else:
                row = core.create_exit_permit(self.student, uid, g('reason'), g('guardian_name'), g('relationship'), g('national_id'))
        except USER_ERRORS as exc:
            self._saving = False
            show_error(self.win, 'تعذر إنشاء السجل', exc)
            return
        self.win.destroy()
        self.app.refresh_records()
        self.app.show_record(row['id'], offer_print=True)


class UserAddDialog:
    def __init__(self, parent):
        self.result = False
        self.username = self.display_name = self.password = ''
        self.role = 'supervisor'
        win = Toplevel(parent)
        win.title('إضافة مستخدم')
        win.geometry('480x420')
        win.transient(parent)
        win.grab_set()
        form = ttk.Frame(win)
        form.pack(fill=BOTH, expand=True, padx=28, pady=25)
        vars_ = {'username': StringVar(), 'display_name': StringVar(), 'password': StringVar(), 'confirm': StringVar(), 'role': ComboStringVar(['مشرف', 'مدير'], value='مشرف')}
        for key, label, show in [('username', 'اسم المستخدم', ''), ('display_name', 'الاسم الظاهر', ''),
                                 ('password', 'كلمة المرور (8 أحرف على الأقل)', '●'), ('confirm', 'تأكيد كلمة المرور', '●')]:
            row = ttk.Frame(form)
            row.pack(fill=X, pady=7)
            ttk.Label(row, text=label, width=28, anchor=E).pack(side=RIGHT, padx=5)
            ttk.Entry(row, textvariable=vars_[key], show=show, justify='right').pack(side=RIGHT, fill=X, expand=True)
        row = ttk.Frame(form)
        row.pack(fill=X, pady=7)
        ttk.Label(row, text='الدور', width=28, anchor=E).pack(side=RIGHT, padx=5)
        ttk.Combobox(row, textvariable=vars_['role'], values=['مشرف', 'مدير'], state='readonly', justify='right').pack(side=RIGHT, fill=X, expand=True)

        def save():
            if not all(vars_[k].get().strip() for k in ('username', 'display_name', 'password', 'confirm')):
                messagebox.showerror('بيانات ناقصة', 'أكمل جميع الحقول.', parent=win)
                return
            if vars_['password'].get() != vars_['confirm'].get():
                messagebox.showerror('عدم تطابق', 'كلمتا المرور غير متطابقتين.', parent=win)
                return
            self.username, self.display_name = vars_['username'].get().strip(), vars_['display_name'].get().strip()
            self.password = vars_['password'].get()
            self.role = 'admin' if vars_['role'].get() == 'مدير' else 'supervisor'
            self.result = True
            win.destroy()
        ttk.Button(win, text='إنشاء الحساب', command=save, style='Accent.TButton').pack(pady=8)
        parent.wait_window(win)



def _windows_printer_list() -> tuple[list[str], str]:
    """Enumerate Windows printers without making pywin32 a mandatory dependency."""
    if os.name != 'nt':
        return [], ''
    try:
        import win32print  # optional acceleration when installed
        names = [item[2] for item in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        default = win32print.GetDefaultPrinter() if names else ''
        return sorted(set(names), key=str.casefold), default
    except Exception:
        pass
    try:
        import subprocess
        script = "$OutputEncoding=[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + \
                 "Get-Printer | Sort-Object Name | ForEach-Object { $_.Name }; " + \
                 "Write-Output '---DEFAULT---'; " + \
                 "(Get-Printer | Where-Object {$_.Default} | Select-Object -First 1 -ExpandProperty Name)"
        proc = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if proc.returncode == 0:
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if '---DEFAULT---' in lines:
                split = lines.index('---DEFAULT---')
                names = lines[:split]
                default = next((line for line in lines[split+1:] if line), '')
                return sorted(set(names), key=str.casefold), default
    except Exception:
        pass
    return [], ''


def _shell_print_to(path: Path, printer: str) -> None:
    """Ask the registered PDF handler to print to a named Windows printer."""
    if os.name != 'nt':
        raise RuntimeError('الإرسال المباشر إلى الطابعة متاح في Windows فقط؛ افتح معاينة PDF للطباعة.')
    import ctypes
    result = ctypes.windll.shell32.ShellExecuteW(None, 'printto', str(path), f'"{printer}"', str(path.parent), 0)
    if int(result) <= 32:
        raise OSError(f'رفض Windows أمر الطباعة المباشر (ShellExecute={result}).')

class SchoolPassApp:
    def __init__(self, root: Tk, user):
        self.root = root
        self.current_user = user
        self._update_check_running = False
        self.last_activity = time.time()
        self._locked = False
        configure_window(root, f"SchoolPass — {setting('school_name', SCHOOL_NAME)}", '1180x760')
        try:
            sw = int(root.winfo_screenwidth() or 1366)
            sh = int(root.winfo_screenheight() or 768)
        except (TypeError, ValueError):
            sw, sh = 1366, 768
        root.minsize(min(940, max(760, sw - 60)), min(650, max(520, sh - 100)))
        root.resizable(True, True)
        self._windowed_geometry = '1180x760'
        self._fullscreen_mode = False
        make_style(root)
        self.build_ui()
        self.root.bind_all('<F5>', lambda _e: self.refresh_all())
        self.root.bind_all('<Control-Key-1>', lambda _e: RecordForm(self, 'late'))
        self.root.bind_all('<Control-Key-2>', lambda _e: RecordForm(self, 'absence_return'))
        self.root.bind_all('<Control-Key-3>', lambda _e: RecordForm(self, 'exit_permit'))
        self.root.bind_all('<Control-Key-f>', lambda _e: self.quick_search_entry.focus_set())
        self.root.bind_all('<Control-Shift-Key-p>', lambda _e: self.daily_summary())
        self.root.bind_all('<Control-Alt-Key-d>', lambda _e: self.toggle_theme())
        # Windows sometimes ignores a maximize request made before mapping.
        self.root.after(180, self._maximize_on_start)
        self.root.bind('<F11>', lambda _e: self.toggle_fullscreen())
        self.root.bind('<Escape>', lambda _e: self.leave_fullscreen())
        ok, ip = core.ensure_local_server()
        self.server_ok = ok
        host = core.ensure_qr_host_saved() if ok else ''
        role = 'مدير النظام' if user['role'] == 'admin' else 'مشرف'
        qr_state = f"QR عبر الشبكة: {host}:{SERVER_PORT}" if ok else f"خدمة QR غير متاحة (قد يكون البرنامج مفتوحًا مرتين): {ip}"
        self.base_status = f"المستخدم: {user['display_name']} ({role})  |  قاعدة البيانات محلية  |  {qr_state}"
        self.status_text.set(self.base_status)
        self.refresh_records()
        self.root.bind_all('<Key>', self.touch)
        self.root.bind_all('<Button>', self.touch)
        self.root.bind('<Configure>', lambda _e: self._sync_window_button() if hasattr(self, 'maximize_button') else None)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(1000, self.poll_qr_events)
        self.root.after(5000, self.check_inactivity)
        self.root.after(60 * 60 * 1000, self.periodic_backup)
        mismatch = core.qr_host_mismatch() if ok else None
        if mismatch:
            messagebox.showwarning('تغيّر عنوان الحاسوب',
                                   f'رموز QR المطبوعة تشير إلى {mismatch[0]} لكن عنوان الحاسوب الآن {mismatch[1]}.\n'
                                   'الرموز القديمة لن تفتح حتى تثبّت عنوان الحاسوب (يُنصح بحجز عنوان ثابت في الراوتر) '
                                   'أو يعيد المدير العنوان القديم من «إعدادات المؤسسة».', parent=self.root)

    def _maximize_on_start(self):
        """Maximize the main window after Windows has mapped it."""
        try:
            if os.name == 'nt':
                self.root.state('zoomed')
            else:
                try:
                    self.root.attributes('-zoomed', True)
                except Exception:
                    self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
            self._sync_window_button()
        except Exception as exc:
            try:
                self.status_text.set(f"تعذر تكبير النافذة تلقائيًا: {exc}")
            except Exception:
                pass

    def _sync_window_button(self):
        if not hasattr(self, 'maximize_button') or not self.maximize_button.winfo_exists():
            return
        try:
            zoomed = self.root.state() == 'zoomed' or self._fullscreen_mode
        except Exception:
            zoomed = self._fullscreen_mode
        self.maximize_button.configure(text='▣  استعادة الحجم' if zoomed else '□  تكبير الشاشة')

    def toggle_maximize(self):
        """Toggle standard window maximize/restore (not kiosk fullscreen)."""
        try:
            if self._fullscreen_mode:
                self.leave_fullscreen()
                return
            if self.root.state() == 'zoomed':
                self.root.state('normal')
                self.root.geometry(self._windowed_geometry)
            else:
                self._windowed_geometry = self.root.geometry()
                if os.name == 'nt':
                    self.root.state('zoomed')
                else:
                    try:
                        self.root.attributes('-zoomed', True)
                    except Exception:
                        self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
            self._sync_window_button()
        except Exception as exc:
            show_error(self.root, 'تعذر تغيير حجم النافذة', exc)

    def toggle_fullscreen(self):
        try:
            self._fullscreen_mode = not self._fullscreen_mode
            self.root.attributes('-fullscreen', self._fullscreen_mode)
            self._sync_window_button()
        except Exception as exc:
            self._fullscreen_mode = False
            show_error(self.root, 'تعذر تفعيل ملء الشاشة', exc)

    def leave_fullscreen(self):
        if self._fullscreen_mode:
            self._fullscreen_mode = False
            try:
                self.root.attributes('-fullscreen', False)
            except Exception:
                pass
            self._sync_window_button()

    def refresh_all(self):
        """Refresh the dashboard and recent-records table together."""
        self.refresh_records()

    def check_for_updates(self):
        """Check GitHub Releases without freezing the school-office interface."""
        if self._update_check_running:
            messagebox.showinfo('البحث عن التحديثات', 'البحث جارٍ بالفعل؛ انتظر ظهور النتيجة.', parent=self.root)
            return
        repo = core.setting('github_repository', '').strip()
        if not repo:
            dialog = Toplevel(self.root)
            configure_window(dialog, 'إعداد مصدر التحديثات', '560x250')
            dialog.transient(self.root)
            dialog.grab_set()
            ttk.Label(dialog, text='إعداد مستودع التحديثات الرسمي', style='Title.TLabel').pack(pady=(22, 8))
            ttk.Label(dialog, text='أدخل اسم المستودع العام بصيغة owner/repository.\nمثال: schoolname/SchoolPass',
                      justify='center').pack(pady=4)
            repo_var = StringVar()
            repo_entry = ttk.Entry(dialog, textvariable=repo_var, width=48, justify='center')
            repo_entry.pack(pady=10)
            result = {'value': None}
            def accept_repo():
                result['value'] = repo_var.get().strip()
                dialog.destroy()
            buttons = ttk.Frame(dialog)
            buttons.pack(pady=8)
            ttk.Button(buttons, text='حفظ ومتابعة', command=accept_repo, style='Accent.TButton').pack(side=RIGHT, padx=5)
            ttk.Button(buttons, text='إلغاء', command=dialog.destroy).pack(side=RIGHT, padx=5)
            repo_entry.focus_set()
            self.root.wait_window(dialog)
            repo = result['value']
            if not repo:
                return
            repo = repo.replace('https://github.com/', '').strip('/')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
                messagebox.showerror('صيغة غير صحيحة', 'أدخل اسم المستودع بصيغة owner/repository.', parent=self.root)
                return
            core.set_setting('github_repository', repo)
        repo = repo.replace('https://github.com/', '').strip('/')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            messagebox.showerror('مصدر التحديثات غير صحيح', 'راجع إعداد مستودع GitHub.', parent=self.root)
            return

        self._update_check_running = True
        self.status_text.set(self.base_status + ' | جارٍ البحث عن التحديثات عبر GitHub…')
        self.root.update_idletasks()

        def worker():
            result = None
            error = None
            try:
                api = f'https://api.github.com/repos/{repo}/releases/latest'
                request = Request(api, headers={
                    'Accept': 'application/vnd.github+json',
                    'User-Agent': f'SchoolPass/{APP_VERSION}',
                    'X-GitHub-Api-Version': '2022-11-28',
                })
                with urlopen(request, timeout=12) as response:
                    raw = response.read(2_000_000)
                result = json.loads(raw.decode('utf-8'))
                if not isinstance(result, dict):
                    raise ValueError('استجابة GitHub غير مفهومة.')
            except Exception as exc:  # forwarded to the UI thread
                error = exc
            try:
                self.root.after(0, lambda: self._finish_update_check(repo, result, error))
            except Exception:
                pass  # the main window may have been closed during the request

        import threading
        threading.Thread(target=worker, name='SchoolPassUpdateCheck', daemon=True).start()

    @staticmethod
    def _version_parts(value: str) -> tuple[int, ...]:
        text = str(value or '').strip().lstrip('vV')
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}', text):
            raise ValueError('صيغة رقم الإصدار المنشور غير صحيحة.')
        return tuple(int(part) for part in text.split('.'))

    def _finish_update_check(self, repo: str, release, error):
        """Render the result of the background release lookup on the Tk main thread."""
        self._update_check_running = False
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        if error is not None:
            if isinstance(error, HTTPError):
                detail = f'أعاد GitHub رمز HTTP {error.code}. تأكد من صحة المستودع العام وأنه يحتوي على إصدار منشور.'
            elif isinstance(error, URLError):
                detail = f'تعذر الاتصال بـ GitHub: {error.reason}'
            else:
                detail = f'تحقق من الإنترنت ومستودع GitHub.\nالتفاصيل: {error}'
            messagebox.showerror('تعذر البحث عن التحديثات', detail, parent=self.root)
            self.status_text.set(self.base_status)
            return
        try:
            tag = str(release.get('tag_name', '')).strip()
            latest_parts = self._version_parts(tag)
            current_parts = self._version_parts(APP_VERSION)
            latest = '.'.join(str(part) for part in latest_parts)
            if latest_parts <= current_parts:
                messagebox.showinfo('البحث عن التحديثات',
                    f'نسختك الحالية: {APP_VERSION}\nأحدث إصدار منشور: {tag}\n\nلا يوجد تحديث أحدث من النسخة المثبتة.',
                    parent=self.root)
                return
            assets = release.get('assets') or []
            setup_asset = next((a for a in assets if str(a.get('name', '')).casefold() == 'schoolpass_setup.exe'), None)
            release_page = f'https://github.com/{repo}/releases/tag/{tag}'
            if not setup_asset:
                messagebox.showwarning('التحديث منشور دون ملف تثبيت',
                    f'وجد البرنامج الإصدار {tag}، لكن ملف SchoolPass_Setup.exe غير مرفق به.\n'
                    'لن يتم توجيهك إلى تنزيل ناقص. على مسؤول المستودع إرفاق ملف المثبّت أولًا.', parent=self.root)
                return
            size = int(setup_asset.get('size') or 0)
            size_text = f'{size / (1024 * 1024):.1f} MB' if size else 'الحجم غير محدد'
            notes = str(release.get('body') or '').strip()[:1600]
            answer = messagebox.askyesno('يتوفر تحديث جديد',
                f'النسخة الحالية: {APP_VERSION}\nالإصدار الجديد: {tag}\nحجم المثبّت: {size_text}\n\n'
                f'ملاحظات الإصدار:\n{notes or "راجع صفحة الإصدار لمعرفة التغييرات."}\n\n'
                'سيتم فتح صفحة GitHub لتنزيل ملف التثبيت. أغلق البرنامج وشغّل المثبّت فوق النسخة الحالية.\n'
                'تبقى قاعدة البيانات في مجلدها المستقل ولا تحذفها أثناء التحديث.\n\nفتح صفحة التحديث؟',
                parent=self.root)
            if answer:
                webbrowser.open(release_page)
        except Exception as exc:
            messagebox.showerror('تعذر قراءة الإصدار', f'بيانات الإصدار على GitHub غير صالحة.\n{exc}', parent=self.root)
        finally:
            self.status_text.set(self.base_status)

    def periodic_backup(self):
        """Refresh local and configured external backups hourly during a long session."""
        if not self.root.winfo_exists():
            return
        try:
            core.automatic_backup_if_due(self.current_user['id'])
            error = core.setting('external_backup_last_error', '')
            if error:
                self.status_text.set(self.base_status + ' | تحذير: النسخة الخارجية لم تُحدّث؛ افحص USB/القرص.')
        except Exception as exc:
            try:
                with LOG_PATH.open('a', encoding='utf-8') as log:
                    log.write(f"\n[{now_iso()}] PERIODIC BACKUP ERROR: {exc}\n")
            except OSError:
                pass
        self.root.after(60 * 60 * 1000, self.periodic_backup)

    def touch(self, _event=None):
        self.last_activity = time.time()

    def build_ui(self):
        """Professional dashboard shell: RTL navigation, six KPIs, charts, alerts and recent activity."""
        shell = ttk.Frame(self.root)
        shell.pack(fill=BOTH, expand=True)

        sidebar = ttk.Frame(shell, style='Sidebar.TFrame', width=238)
        sidebar.pack(side=RIGHT, fill=Y)
        sidebar.pack_propagate(False)
        brand = ttk.Frame(sidebar, style='Sidebar.TFrame')
        brand.pack(fill=X, padx=18, pady=(25, 22))
        ttk.Label(brand, text='SchoolPass', style='SidebarTitle.TLabel').pack(anchor=E)
        ttk.Label(brand, text='إدارة رخص الدخول والخروج', style='SidebarSub.TLabel').pack(anchor=E, pady=(5, 0))
        ttk.Frame(sidebar, height=1, style='Sidebar.TFrame').pack(fill=X, padx=16, pady=(0, 12))

        def nav(label, command):
            ttk.Button(sidebar, text=label, command=command, style='Nav.TButton').pack(fill=X, padx=10, pady=2)

        nav('⌂   لوحة التحكم', self.refresh_all)
        nav('＋   رخصة الدخول — تأخر', lambda: RecordForm(self, 'late'))
        nav('↩   رخصة الدخول — عودة بعد الغياب', lambda: RecordForm(self, 'absence_return'))
        nav('⇥   رخصة خروج استثنائي', lambda: RecordForm(self, 'exit_permit'))
        nav('▤   إدارة التلاميذ', self.manage_students)
        nav('▥   سجل العمليات والتصفية', self.open_logs)
        nav('▦   الإحصائيات والرسوم البيانية', self.show_stats)
        nav('▧   التقارير الشهرية', self.monthly_report)
        nav('!   تنبيهات الخروج دون عودة', self.show_open_exits)
        if self.current_user['role'] == 'admin':
            ttk.Frame(sidebar, height=1, style='Sidebar.TFrame').pack(fill=X, padx=16, pady=9)
            ttk.Label(sidebar, text='إدارة النظام', style='SidebarSub.TLabel').pack(anchor=E, padx=18, pady=(0, 4))
            nav('◷   التقرير اليومي التفصيلي A4', self.daily_summary)
            nav('♙   المستخدمون والصلاحيات', self.manage_users)
            nav('▣   النسخ الاحتياطي والاستعادة', self.backup_db)
            nav('⚙   إعدادات المؤسسة والطباعة', self.settings_dialog)
            nav('≡   سجل التدقيق', self.audit_view)
        ttk.Frame(sidebar, style='Sidebar.TFrame').pack(fill=BOTH, expand=True)
        ttk.Label(sidebar, text=f'الإصدار الرسمي {APP_VERSION}', style='SidebarSub.TLabel').pack(anchor=E, padx=18, pady=16)

        content = ttk.Frame(shell)
        content.pack(side=LEFT, fill=BOTH, expand=True)
        self.content_frame = content
        content.bind('<Configure>', lambda event: self._adapt_dashboard_layout(event.width))
        header = ttk.Frame(content)
        header.pack(fill=X, padx=24, pady=(18, 9))
        right_head = ttk.Frame(header)
        right_head.pack(side=RIGHT, fill=X, expand=True)
        ttk.Label(right_head, text=setting('school_name', SCHOOL_NAME), style='Title.TLabel').pack(anchor=E)
        ttk.Label(right_head, text='منصة إدارية موحدة لتصاريح الدخول والخروج وسجل المتابعة  •  F5 تحديث  •  Ctrl+1/2/3 إصدار الرخص',
                  style='Subtitle.TLabel').pack(anchor=E, pady=(4, 0))
        controls = ttk.Frame(header)
        controls.pack(side=LEFT, padx=(8, 0), anchor='n')
        self.maximize_button = ttk.Button(controls, text='□  تكبير الشاشة', command=self.toggle_maximize)
        self.maximize_button.pack(side=LEFT, padx=3)
        ttk.Button(controls, text='⟳  تحديث البيانات', command=self.refresh_all).pack(side=LEFT, padx=3)
        ttk.Button(controls, text='بحث عن تحديثات', command=self.check_for_updates).pack(side=LEFT, padx=3)
        self.theme_button = ttk.Button(controls, text=('☀  الوضع النهاري' if core.setting('theme', 'light') == 'dark' else '☾  الوضع الليلي'), command=self.toggle_theme)
        self.theme_button.pack(side=LEFT, padx=3)
        self.user_badge = ttk.Label(header, text=f"{self.current_user['display_name']}  •  {'مدير النظام' if self.current_user['role']=='admin' else 'مشرف'}",
                                    style='Badge.TLabel')
        self.user_badge.pack(side=LEFT, padx=(8, 0), anchor='n')

        # KPI cards in a responsive 3x2 grid (no squeezed six-card row on 1366x768).
        cards = ttk.Frame(content)
        cards.pack(fill=X, padx=18, pady=(2, 8))
        self.card_values = {}
        card_specs = [
            ('students', 'إجمالي التلاميذ النشطين'), ('today_entry', 'التلاميذ الحاصلون على رخصة الدخول اليوم'), ('today_exit', 'التلاميذ الحاصلون على رخصة الخروج اليوم'),
            ('late', 'التلاميذ المتأخرون اليوم'), ('absence', 'العائدون بعد الغياب اليوم'), ('open', 'خروج دون تسجيل العودة')
        ]
        for col in range(3):
            cards.grid_columnconfigure(col, weight=1, uniform='kpi')
        for idx, (key, label) in enumerate(card_specs):
            row, col = divmod(idx, 3)
            card = ttk.Frame(cards, style='Surface.TFrame', padding=(13, 9))
            card.grid(row=row, column=2-col, sticky='nsew', padx=4, pady=4)
            title_label = ttk.Label(card, text=label, style='CardTitle.TLabel')
            title_label.pack(anchor=E)
            value = ttk.Label(card, text='—', style='CardValue.TLabel')
            value.pack(anchor=E, pady=(3, 0))
            self.card_values[key] = value
            detail_keys = {'today_entry': 'today_entry', 'today_exit': 'today_exit', 'late': 'today_late',
                           'absence': 'today_absence', 'open': 'open_exits'}
            if key in detail_keys:
                for clickable in (card, title_label, value):
                    clickable.bind('<Button-1>', lambda _e, metric=detail_keys[key]: self.open_dashboard_details(metric))
                    try: clickable.configure(cursor='hand2')
                    except Exception: pass

        # Chart and alerts share a row to keep the dashboard informative yet compact.
        overview = ttk.Frame(content)
        overview.pack(fill=X, padx=22, pady=(1, 8))
        chart_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(12, 8))
        self.chart_panel = chart_panel
        chart_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
        chart_head = ttk.Frame(chart_panel, style='Surface.TFrame')
        chart_head.pack(fill=X)
        ttk.Label(chart_head, text='حركة الرخص حسب الفترة', style='Section.TLabel').pack(side=RIGHT)
        self.chart_period_var = StringVar(value='الأسبوع')
        chart_combo = ttk.Combobox(chart_head, textvariable=self.chart_period_var, state='readonly', width=10,
                                   values=('اليوم', 'الأسبوع', 'الشهر'), justify='right')
        chart_combo.pack(side=LEFT)
        chart_combo.bind('<<ComboboxSelected>>', lambda _e: self.refresh_dashboard_chart())
        self.dashboard_period_summary = DisplayStringVar(value='')
        self.dashboard_period_label = ttk.Label(chart_panel, textvariable=self.dashboard_period_summary, style='Subtitle.TLabel', cursor='hand2')
        self.dashboard_period_label.pack(anchor=E, pady=(5, 0))
        self.dashboard_period_label.bind('<Button-1>', lambda _e: self.open_dashboard_details('week_all'))
        self.dashboard_month_summary = DisplayStringVar(value='')
        self.dashboard_month_label = ttk.Label(chart_panel, textvariable=self.dashboard_month_summary, style='Subtitle.TLabel', cursor='hand2')
        self.dashboard_month_label.pack(anchor=E, pady=(2, 0))
        self.dashboard_month_label.bind('<Button-1>', lambda _e: self.open_dashboard_details('month_all'))
        self.dashboard_chart = Canvas(chart_panel, height=145, background=('#22312C' if core.setting('theme', 'light') == 'dark' else '#FFFFFF'), highlightthickness=0)
        self.dashboard_chart.pack(fill=X, expand=True, pady=(3, 0))

        alert_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(13, 10))
        self.alert_panel = alert_panel
        alert_panel.pack(side=RIGHT, fill=Y, padx=(5, 0))
        ttk.Label(alert_panel, text='التنبيهات والمتابعة', style='Section.TLabel').pack(anchor=E, pady=(0, 8))
        self.alert_summary = DisplayStringVar(value='لا توجد تنبيهات حالية.')
        self.alert_label = ttk.Label(alert_panel, textvariable=self.alert_summary, style='Good.TLabel',
                                     wraplength=260, justify='right')
        self.alert_label.pack(fill=X, pady=3)
        ttk.Button(alert_panel, text='عرض حالات الخروج المفتوحة', command=self.show_open_exits).pack(fill=X, pady=(7, 2))
        ttk.Button(alert_panel, text='فتح سجل العمليات', command=self.open_logs).pack(fill=X, pady=2)

        action_row = ttk.Frame(content)
        action_row.pack(fill=X, padx=24, pady=(0, 7))
        ttk.Button(action_row, text='＋  إصدار رخصة الدخول — تأخر', command=lambda: RecordForm(self, 'late'), style='Accent.TButton').pack(side=RIGHT, padx=(0, 7))
        ttk.Button(action_row, text='↩  رخصة عودة بعد الغياب', command=lambda: RecordForm(self, 'absence_return')).pack(side=RIGHT, padx=7)
        ttk.Button(action_row, text='⇥  إصدار رخصة خروج', command=lambda: RecordForm(self, 'exit_permit'), style='Gold.TButton').pack(side=RIGHT, padx=7)
        if self.current_user['role'] == 'admin':
            ttk.Button(action_row, text='التقرير اليومي التفصيلي A4', command=self.daily_summary, style='Accent.TButton').pack(side=LEFT, padx=3)
        ttk.Button(action_row, text='التقرير الشهري', command=self.monthly_report).pack(side=LEFT, padx=3)

        panel = ttk.Frame(content, style='Surface.TFrame', padding=(13, 10))
        panel.pack(fill=BOTH, expand=True, padx=22, pady=(0, 12))
        title_row = ttk.Frame(panel, style='Surface.TFrame')
        title_row.pack(fill=X, pady=(0, 7))
        ttk.Label(title_row, text='أحدث العمليات المسجلة', style='Section.TLabel').pack(side=RIGHT)
        ttk.Label(title_row, text='انقر مرتين لعرض ملف الرخصة', style='Hint.TLabel').pack(side=LEFT)
        search_row = ttk.Frame(panel, style='Surface.TFrame')
        search_row.pack(fill=X, pady=(0, 7))
        ttk.Label(search_row, text='بحث فوري:', style='Hint.TLabel').pack(side=RIGHT, padx=(0, 7))
        self.quick_search_var = StringVar(value='')
        self.quick_search_entry = ttk.Entry(search_row, textvariable=self.quick_search_var, justify='right')
        self.quick_search_entry.pack(side=RIGHT, fill=X, expand=True, padx=5)
        self.quick_search_entry.bind('<KeyRelease>', lambda _e: self._apply_record_filter())
        ttk.Button(search_row, text='مسح', command=self.clear_quick_search).pack(side=LEFT, padx=3)
        table_wrap = ttk.Frame(panel, style='Surface.TFrame')
        table_wrap.pack(fill=BOTH, expand=True)
        cols = ('record_no', 'kind', 'student_name', 'class_name', 'created_at', 'supervisor', 'status')
        self.tree = ttk.Treeview(table_wrap, columns=cols, show='headings', selectmode='extended')
        headings = [('record_no', 'رقم الرخصة', 125), ('kind', 'نوع الرخصة', 165), ('student_name', 'اسم التلميذ', 175),
                    ('class_name', 'القسم', 105), ('created_at', 'تاريخ ووقت الإصدار', 145), ('supervisor', 'المشرف', 105), ('status', 'الحالة', 85)]
        for col, title, width in headings:
            self.tree.heading(col, text=title, command=lambda c=col: self._sort_main_tree(c))
            self.tree.column(col, width=width, anchor=E, stretch=True)
        self._apply_main_tree_tags()
        yscroll = ttk.Scrollbar(table_wrap, orient='vertical', command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_wrap, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        yscroll.pack(side=RIGHT, fill=Y)
        xscroll.pack(side='bottom', fill=X)
        self.tree.bind('<Double-1>', self.open_selected_record)
        bar = ttk.Frame(panel, style='Surface.TFrame')
        bar.pack(fill=X, pady=(8, 0))
        ttk.Button(bar, text='عرض التفاصيل', command=self.open_selected_record).pack(side=RIGHT, padx=3)
        ttk.Button(bar, text='إعادة الطباعة', command=self.reprint_selected, style='Accent.TButton').pack(side=RIGHT, padx=3)
        ttk.Button(bar, text='تسجيل العودة', command=self.return_selected_exit).pack(side=RIGHT, padx=3)
        ttk.Button(bar, text='إلغاء السجل', command=self.cancel_selected).pack(side=LEFT, padx=3)
        ttk.Button(bar, text='طباعة رخصتين مختلفتين على A4', command=self.print_two_selected, style='Gold.TButton').pack(side=LEFT, padx=6)
        self.status_text = DisplayStringVar(value='')
        status = ttk.Label(content, textvariable=self.status_text, style='Status.TLabel')
        status.pack(fill=X, side='bottom')

    def _apply_main_tree_tags(self):
        if not hasattr(self, 'tree'):
            return
        dark = core.setting('theme', 'light') == 'dark'
        self.tree.tag_configure('late', background='#4A3D23' if dark else '#FFF8E8', foreground='#F4E8C8' if dark else '#1E2B25')
        self.tree.tag_configure('absence', background='#253C4B' if dark else '#EEF7FF', foreground='#D8EAF5' if dark else '#1E2B25')
        self.tree.tag_configure('exit', background='#263F34' if dark else '#F0F7F2', foreground='#D9EDE2' if dark else '#1E2B25')
        self.tree.tag_configure('cancelled', foreground='#FF9C9C' if dark else '#A12D2D')

    def toggle_theme(self):
        """Persist and apply the light/dark theme without changing school data."""
        try:
            current = core.setting('theme', 'light')
            selected = 'dark' if current != 'dark' else 'light'
            core.set_setting('theme', selected)
            make_style(self.root)
            self._apply_main_tree_tags()
            # Canvas/text widgets do not inherit ttk styles; update the live dashboard.
            if hasattr(self, 'dashboard_chart'):
                self.dashboard_chart.configure(background='#22312C' if selected == 'dark' else '#FFFFFF')
            self.refresh_dashboard_chart()
            if hasattr(self, 'theme_button'):
                self.theme_button.configure(text='☀  الوضع النهاري' if selected == 'dark' else '☾  الوضع الليلي')
            self.root.update_idletasks()
        except Exception as exc:
            show_error(self.root, 'تعذر تغيير المظهر', exc)

    def _adapt_dashboard_layout(self, available_width: int):
        """Stack chart and alert cards on narrower displays instead of clipping controls."""
        if not hasattr(self, 'chart_panel') or not hasattr(self, 'alert_panel'):
            return
        narrow = int(available_width or 0) < 900
        layout = 'stacked' if narrow else 'side_by_side'
        if getattr(self, '_dashboard_layout', None) == layout:
            return
        self._dashboard_layout = layout
        if narrow:
            self.chart_panel.pack_configure(side='top', fill=X, expand=True, padx=(0, 0), pady=(0, 5))
            self.alert_panel.pack_configure(side='top', fill=X, expand=False, padx=(0, 0), pady=(0, 0))
            self.dashboard_chart.configure(height=125)
        else:
            self.chart_panel.pack_configure(side=LEFT, fill=BOTH, expand=True, padx=(0, 5), pady=0)
            self.alert_panel.pack_configure(side=RIGHT, fill=Y, expand=False, padx=(5, 0), pady=0)
            self.dashboard_chart.configure(height=145)

    def _sort_main_tree(self, column):
        data = [(self.tree.set(item, column), item) for item in self.tree.get_children('')]
        reverse = getattr(self, '_main_sort_reverse', {}).get(column, False)
        data.sort(key=lambda item: str(item[0]).casefold(), reverse=reverse)
        for index, (_value, item) in enumerate(data):
            self.tree.move(item, '', index)
        if not hasattr(self, '_main_sort_reverse'):
            self._main_sort_reverse = {}
        self._main_sort_reverse[column] = not reverse

    def refresh_dashboard_chart(self):
        if not hasattr(self, 'dashboard_chart'):
            return
        period = {'اليوم': 'day', 'الأسبوع': 'week', 'الشهر': 'month'}.get(self.chart_period_var.get(), 'week')
        data = core.dashboard_series(period)
        c = self.dashboard_chart
        dark = core.setting('theme', 'light') == 'dark'
        surface = '#22312C' if dark else '#FFFFFF'
        axis = '#6A8176' if dark else '#D9E4DE'
        grid = '#344A40' if dark else '#EDF2EF'
        label_color = '#C2D0C8' if dark else '#667A73'
        c.configure(background=surface)
        c.delete('all')
        c.update_idletasks()
        width = max(440, c.winfo_width())
        height = max(130, c.winfo_height())
        left, right, top, bottom = 34, width-12, 10, height-25
        c.create_line(left, top, left, bottom, fill=axis)
        c.create_line(left, bottom, right, bottom, fill=axis)
        names = [('late', 'تأخر', '#D7A33B'), ('absence_return', 'عودة بعد غياب', '#4A89B8'),
                 ('exit_permit', 'خروج', '#2D8062'), ('exit_return', 'إثبات عودة', '#8A78B7')]
        values = data['series']
        maximum = max([1] + [v for arr in values.values() for v in arr])
        n = len(data['labels'])
        group_w = max(3, (right-left)/max(n, 1))
        bar_w = max(1.5, min(8, group_w/(len(names)+1)))
        for tick in range(4):
            val = round(maximum*tick/3)
            yy = bottom - (bottom-top)*tick/3
            c.create_line(left, yy, right, yy, fill=grid)
            c.create_text(left-7, yy, text=str(val), anchor='e', fill=label_color, font=('Segoe UI', 7))
        for i, label in enumerate(data['labels']):
            base_x = left + i*group_w + group_w/2
            for j, (key, _title, color) in enumerate(names):
                v = values[key][i]
                bh = (bottom-top)*v/maximum
                x = base_x + (j-(len(names)-1)/2)*bar_w - bar_w/2
                if v:
                    c.create_rectangle(x, bottom-bh, x+bar_w-1, bottom, fill=color, outline='')
            if period == 'day' or n <= 7 or i % 5 == 0 or i == n-1:
                c.create_text(base_x, bottom+12, text=label, fill=label_color, font=('Segoe UI', 7))
        legend_x = right-6
        for key, title, color in reversed(names):
            c.create_rectangle(legend_x-72, 2, legend_x-64, 10, fill=color, outline='')
            c.create_text(legend_x-77, 6, text=title, anchor='e', fill=label_color, font=('Segoe UI', 7))
            legend_x -= 92

    def refresh_dashboard(self):
        metrics = core.dashboard_metrics()
        today = metrics['today']
        values = {
            'students': metrics['students_active'],
            'today_entry': today['entry_students'],
            'today_exit': today['exit_students'],
            'late': today['late_students'],
            'absence': today['absence_students'],
            'open': metrics['open_exits'],
        }
        if hasattr(self, 'card_values'):
            for key, value in values.items():
                self.card_values[key].configure(text=str(value))
        if hasattr(self, 'dashboard_period_summary'):
            self.dashboard_period_summary.set(
                f"آخر 7 أيام: {metrics['week']['students']} تلميذًا / {metrics['week']['permits']} رخصة — اضغط لعرض التفاصيل")
            self.dashboard_month_summary.set(
                f"من بداية الشهر: {metrics['month']['students']} تلميذًا / {metrics['month']['permits']} رخصة — اضغط لعرض التفاصيل")
        if hasattr(self, 'alert_summary'):
            if metrics['open_exits']:
                self.alert_summary.set(f"هناك {metrics['open_exits']} تلميذًا خرجوا ولم تُسجل عودتهم بعد. راجع القائمة وأغلق الحالات عند عودتهم.")
                self.alert_label.configure(style='Alert.TLabel')
            else:
                self.alert_summary.set('لا توجد تصاريح خروج مفتوحة حاليًا.')
                self.alert_label.configure(style='Good.TLabel')
        if hasattr(self, 'status_text'):
            self.status_text.set(self.base_status +
                f"  |  دخول اليوم: {today['entry_students']} تلميذًا  |  تأخر: {today['late_students']}  |  خروج: {today['exit_students']}  |  خروج مفتوح: {metrics['open_exits']}")
        self.refresh_dashboard_chart()

    def open_dashboard_details(self, key: str):
        try:
            rows = core.dashboard_detail_records(key)
        except USER_ERRORS as exc:
            show_error(self.root, 'تعذر عرض تفاصيل الإحصائية', exc)
            return
        labels = {
            'today_entry': 'التلاميذ الحاصلون على رخصة الدخول اليوم',
            'today_late': 'التلاميذ المتأخرون اليوم',
            'today_absence': 'العائدون بعد الغياب اليوم',
            'today_exit': 'التلاميذ الحاصلون على رخصة الخروج اليوم',
            'today_all': 'جميع الرخص الصادرة اليوم',
            'week_all': 'الرخص الصادرة خلال آخر 7 أيام',
            'month_all': 'الرخص الصادرة منذ بداية الشهر',
            'open_exits': 'تصاريح الخروج التي لم تُسجل عودتها',
        }
        win = Toplevel(self.root)
        win.title(labels.get(key, 'تفاصيل الإحصائية'))
        win.geometry('1080x560')
        win.minsize(820, 440)
        win.transient(self.root)
        ttk.Label(win, text=labels.get(key, 'تفاصيل الإحصائية'), style='Title.TLabel').pack(anchor=E, padx=16, pady=(14, 5))
        distinct = len({r['student_key'] for r in rows})
        ttk.Label(win, text=f"عدد السجلات: {len(rows)}    |    عدد التلاميذ: {distinct}", style='Subtitle.TLabel').pack(anchor=E, padx=18, pady=(0, 7))
        cols = ('record_no', 'time', 'kind', 'student', 'class', 'supervisor', 'status')
        wrap = ttk.Frame(win); wrap.pack(fill=BOTH, expand=True, padx=12, pady=8)
        tree = ttk.Treeview(wrap, columns=cols, show='headings')
        for col, title, width in [('record_no','رقم الرخصة',125),('time','التاريخ والوقت',155),('kind','نوع الرخصة',190),
                                  ('student','التلميذ',190),('class','القسم',130),('supervisor','المشرف',125),('status','الحالة',100)]:
            tree.heading(col, text=title); tree.column(col, width=width, anchor=E)
        sy = ttk.Scrollbar(wrap, orient='vertical', command=tree.yview)
        sx = ttk.Scrollbar(wrap, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True); sy.pack(side=RIGHT, fill=Y); sx.pack(side='bottom', fill=X)
        for r in rows:
            tree.insert('', END, iid=str(r['id']), values=(r['record_no'], r.get('issued_at') or r.get('created_at'),
                kind_label(r['kind']), r['student_name'], r['class_name'], r.get('supervisor') or '—', status_label(r['status'])))
        tree.bind('<Double-1>', lambda _e: self.show_record(int(tree.selection()[0])) if tree.selection() else None)
        ttk.Button(win, text='عرض تفاصيل السجل المحدد', command=lambda: self.show_record(int(tree.selection()[0])) if tree.selection() else messagebox.showinfo('اختيار', 'حدد سجلًا أولًا.', parent=win), style='Accent.TButton').pack(pady=(0, 12))

    def daily_summary(self):
        """Generate the director-ready A4 daily handover report and open print options."""
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = core.DATA_DIR / 'prints' / f'SchoolPass_Daily_Report_{date_today()}_{stamp}.pdf'
        try:
            out = core.create_daily_report_pdf(path, date_today(), self.current_user['id'])
        except (USER_ERRORS + (ImportError,)) as exc:
            show_error(self.root, 'تعذر إنشاء التقرير اليومي', exc)
            return
        self._show_print_options(out, self.root, title='التقرير اليومي — ورقة A4 للمدير')

    def show_open_exits(self):
        with db_connect() as conn:
            rows = conn.execute("""SELECT r.id,r.record_no,r.student_name,r.class_name,r.issued_at,r.created_at,
                r.details_json,u.display_name AS supervisor FROM records r LEFT JOIN users u ON u.id=r.user_id
                WHERE r.kind='exit_permit' AND r.status='valid' ORDER BY r.issued_at""").fetchall()
        win = Toplevel(self.root)
        win.title('رخص الخروج التي لم تُسجل عودتها')
        win.geometry('1050x520')
        win.transient(self.root)
        cols = ('record_no','student','class','issued','guardian','supervisor')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        for col,title,width in [('record_no','الرقم المرجعي',145),('student','التلميذ',190),('class','القسم',150),
                                ('issued','توقيت الخروج',160),('guardian','ولي الأمر',170),('supervisor','المشرف',130)]:
            tree.heading(col,text=title); tree.column(col,width=width,anchor=E)
        tree.pack(fill=BOTH,expand=True,padx=12,pady=12)
        for r in rows:
            d=json.loads(r['details_json'] or '{}')
            tree.insert('',END,iid=str(r['id']),values=(r['record_no'],r['student_name'],r['class_name'],r['issued_at'] or r['created_at'],d.get('guardian_name','—'),r['supervisor'] or '—'))
        ttk.Label(win,text=f"عدد التلاميذ الذين لم تُسجل عودتهم: {len(rows)}",font=('Segoe UI',10,'bold')).pack(pady=(0,8))
        ttk.Button(win,text='عرض الرخصة المحددة',command=lambda: self.show_record(int(tree.selection()[0])) if tree.selection() else messagebox.showinfo('اختيار', 'حدد رخصة أولًا.', parent=win)).pack(pady=(0,10))

    def clear_quick_search(self):
        if hasattr(self, 'quick_search_var'):
            self.quick_search_var.set('')
        self._apply_record_filter()

    def _apply_record_filter(self):
        if not hasattr(self, 'tree'):
            return
        query = self.quick_search_var.get().strip().casefold() if hasattr(self, 'quick_search_var') else ''
        for item in self.tree.get_children():
            self.tree.delete(item)
        for r in getattr(self, '_records_cache', []):
            values = (r['record_no'], kind_label(r['kind']), r['student_name'], r['class_name'],
                      (r['issued_at'] or r['created_at']), r['supervisor'] or '—', status_label(r['status']))
            if query and query not in ' '.join(str(v or '') for v in values).casefold():
                continue
            tag = 'late' if r['kind'] == 'late' else ('absence' if r['kind'] == 'absence_return' else 'exit')
            tags = ('cancelled',) if r['status'] == 'cancelled' else (tag,)
            self.tree.insert('', END, iid=str(r['id']), values=values, tags=tags)

    def refresh_records(self):
        with db_connect() as conn:
            rows = conn.execute("SELECT r.*,u.display_name AS supervisor FROM records r LEFT JOIN users u ON r.user_id=u.id "
                                "ORDER BY r.id DESC LIMIT 150").fetchall()
        self._records_cache = rows
        self._apply_record_filter()
        self.refresh_dashboard()

    def print_two_selected(self):
        ids = [int(x) for x in self.tree.selection()]
        if len(ids) != 2:
            messagebox.showinfo('اختيار رخصتين', 'حدد رخصتي دخول مختلفتين من الجدول؛ ستظهر كل رخصة في نصف مستقل من ورقة A4. رخص الخروج تُطبع بالطريقة المعتادة بنسختي الحاجب والتلميذ.', parent=self.root)
            return
        try:
            path = core.create_a4_two_record_sheet(ids)
            records_to_audit = []
            with db_connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                for rid in ids:
                    row = conn.execute('SELECT record_no FROM records WHERE id=?', (rid,)).fetchone()
                    if not row:
                        raise ValueError('إحدى الرخصتين لم تعد موجودة.')
                    conn.execute('UPDATE records SET printed_count=printed_count+1 WHERE id=?', (rid,))
                    records_to_audit.append((rid, row['record_no']))
            for rid, number in records_to_audit:
                audit(self.current_user['id'], 'إعداد ورقة A4 لرخصة مستقلة', 'record', str(rid), number)
            self._show_print_options(path, self.root, title='معاينة ورقة A4 — رخصتان مستقلتان')
            self.refresh_records()
        except USER_ERRORS as exc:
            show_error(self.root, 'تعذر إعداد ورقة A4', exc)

    def selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _need_selection(self, msg='حدد سجلًا من الجدول أولًا.'):
        rid = self.selected_id()
        if not rid:
            messagebox.showinfo('اختر سجلًا', msg, parent=self.root)
        return rid

    def open_selected_record(self, _event=None):
        rid = self._need_selection()
        if rid:
            self.show_record(rid)

    def reprint_selected(self):
        rid = self._need_selection()
        if rid:
            self._reprint_by_id(rid, self.root)

    def show_record(self, record_id: int, offer_print: bool = False):
        row = record_by_id(record_id)
        if not row:
            return
        details = json.loads(row['details_json'] or '{}')
        win = Toplevel(self.root)
        win.title(f"تفاصيل {row['record_no']}")
        win.geometry('640x600')
        win.transient(self.root)
        ttk.Label(win, text=kind_label(row['kind']), style='Title.TLabel').pack(pady=(18, 5))
        body = ttk.Frame(win)
        body.pack(fill=BOTH, expand=True, padx=25, pady=10)
        creator = get_user(row['user_id']) if row['user_id'] is not None else None
        lines = [f"الرقم المرجعي: {row['record_no']}", f"الحالة: {status_label(row['status'])}", f"اسم التلميذ: {row['student_name']}",
                 f"القسم: {row['class_name']}", f"تاريخ ووقت الإصدار: {row['issued_at'] if row['issued_at'] else row['created_at']}", f"المشرف: {creator['display_name'] if creator else '—'}",
                 f"السبب: {row['reason'] or '—'}", f"عدد مرات الطباعة المسجل: {row['printed_count']}"]
        if row['kind'] == 'absence_return':
            lines += [f"بداية الغياب: {details.get('absence_start', '—')}", f"تاريخ العودة: {details.get('return_date', '—')}",
                      f"أيام الغياب (دون احتساب يوم العودة): {details.get('days_count', '—')} يومًا",
                      f"طريقة التبرير: {'حضور ولي الأمر شخصيًا' if details.get('proof_method') == 'guardian' else 'شهادة طبية'}"]
            if details.get('proof_method') == 'guardian':
                lines += [f"ولي الأمر: {details.get('guardian_name', '—')}", f"صلة القرابة: {details.get('relationship', '—')}",
                          f"رقم الهوية (مقنّع؛ الأصل مشفّر): {core.mask_national_id(details)}",
                          f"وقت تأكيد الحضور: {details.get('guardian_verified_at', '—')}"]
        if row['kind'] == 'exit_return':
            lines.append(f"مرجع تصريح الخروج: {details.get('permit_no', '—')}")
        if row['kind'] == 'exit_permit':
            lines += [f"ولي الأمر: {details.get('guardian_name', '—')}", f"صلة القرابة: {details.get('relationship', '—')}",
                      f"رقم الهوية (مقنّع؛ الأصل مشفّر): {core.mask_national_id(details)}"]
            if row['status'] == 'returned':
                lines.append(f"وقت العودة: {row['returned_at'] or '—'}")
        if row['status'] == 'cancelled':
            who = get_user(row['cancelled_by']) if row['cancelled_by'] is not None else None
            lines += [f"سبب الإلغاء: {row['cancelled_reason'] or '—'}", f"وقت الإلغاء: {row['cancelled_at'] or '—'}",
                      f"نفّذ الإلغاء: {who['display_name'] if who else '—'}"]
        for line in lines:
            ttk.Label(body, text=line, anchor=E, wraplength=560).pack(fill=X, pady=3)
        bar = ttk.Frame(win)
        bar.pack(fill=X, padx=20, pady=15)
        ttk.Button(bar, text='طباعة / إعادة طباعة الرخصة', command=lambda: self._reprint_by_id(record_id, win)).pack(side=RIGHT, padx=4)
        if row['status'] == 'valid':
            if row['kind'] == 'exit_permit':
                ttk.Button(bar, text='تسجيل عودة التلميذ', command=lambda: self._return_exit(record_id, win)).pack(side=RIGHT, padx=4)
            ttk.Button(bar, text='إلغاء السجل', command=lambda: self._cancel_record(record_id, win)).pack(side=RIGHT, padx=4)
        if row['kind'] in ('exit_permit', 'absence_return') and self.current_user['role'] == 'admin' and json.loads(row['details_json'] or '{}').get('national_id_enc'):
            ttk.Button(bar, text='إظهار رقم الهوية (للمدير)', command=lambda: self._reveal_nid(record_id, win)).pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إغلاق', command=win.destroy).pack(side=RIGHT, padx=4)
        if offer_print:
            win.after(500, lambda: self._ask_initial_print(record_id, win))

    def _reveal_nid(self, record_id, parent):
        try:
            value = core.reveal_national_id(record_id, self.current_user['id'])
        except USER_ERRORS as exc:
            show_error(parent, 'تعذر إظهار الرقم', exc)
            return
        messagebox.showinfo('رقم الهوية الكامل', f'{value}\n\n(سُجّل هذا الاطلاع في سجل التدقيق)', parent=parent)

    def _ask_initial_print(self, record_id, win):
        if not win.winfo_exists():
            return
        if messagebox.askyesno('طباعة الرخصة', 'تم حفظ السجل. هل تريد إنشاء ملف الرخصة الآن؟\nعند فتح نافذة الطباعة اختر الطابعة وورق A6 والحجم الفعلي 100%. رخصة الدخول الآن بتنسيق كبير مماثل لرخصة الخروج.', parent=win):
            self._reprint_by_id(record_id, win)

    def _reprint_by_id(self, record_id, parent):
        """Create a reprint of the SAME numbered permit, then offer preview/printer selection."""
        try:
            paths = core.print_record(record_id, self.current_user['id'], launch=False)
        except USER_ERRORS + (ImportError,) as exc:
            show_error(parent, 'تعذرت الطباعة', exc)
            return
        self.refresh_records()
        self._show_print_options(paths[0], parent, title='معاينة وطباعة الرخصة')

    def _show_print_options(self, pdf_path: Path, parent, title='معاينة وطباعة'):
        win = Toplevel(parent)
        win.title(title)
        win.geometry('560x300')
        win.minsize(460, 250)
        win.transient(parent)
        ttk.Label(win, text=title, style='Title.TLabel').pack(pady=(18, 6))
        ttk.Label(win, text=f'الملف: {pdf_path.name}', wraplength=500).pack(pady=4)
        printers, default_printer = _windows_printer_list()
        ttk.Label(win, text='اختر الطابعة (أو افتح المعاينة واخترها من عارض PDF):').pack(anchor=E, padx=25, pady=(10, 3))
        printer_var = StringVar(value=default_printer or (printers[0] if printers else 'اختيار الطابعة من عارض PDF'))
        values = printers or ['اختيار الطابعة من عارض PDF']
        combo = ttk.Combobox(win, textvariable=printer_var, values=values, state='readonly', justify='right')
        combo.pack(fill=X, padx=25, pady=4)
        note = 'سيبقى رقم الرخصة الأصلي كما هو، وستُسجّل إعادة الطباعة في سجل التدقيق.'
        ttk.Label(win, text=note, wraplength=500, foreground='#526579').pack(pady=7)
        def preview():
            if core.open_pdf(pdf_path):
                self.status_text.set(self.base_status + f' | المعاينة جاهزة: {pdf_path.name}')
            else:
                messagebox.showwarning('المعاينة', f'تعذر فتح الملف تلقائيًا. افتحه يدويًا من:\n{pdf_path}', parent=win)
        def send_to_printer():
            selected = printer_var.get()
            if not printers or selected not in printers:
                preview()
                messagebox.showinfo('اختيار الطابعة', 'اختر الطابعة من نافذة عارض PDF ثم تحقق من حجم الورق والاتجاه قبل الطباعة.', parent=win)
                return
            try:
                _shell_print_to(pdf_path, selected)
                messagebox.showinfo('تم إرسال أمر الطباعة', f'أُرسل الملف إلى الطابعة:\n{selected}\n\nقد تظهر نافذة عارض PDF حسب البرنامج الافتراضي. تحقق من المقاس والاتجاه قبل تأكيد الطباعة.', parent=win)
            except Exception as exc:
                preview()
                messagebox.showwarning('تعذر الإرسال المباشر', f'لم يقبل عارض PDF أمر الطباعة المباشر. فُتحت المعاينة لتختار الطابعة يدويًا.\n\nالتفاصيل: {exc}', parent=win)
        bar=ttk.Frame(win);bar.pack(fill=X,padx=20,pady=(8,15))
        ttk.Button(bar,text='فتح المعاينة',command=preview,style='Accent.TButton').pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إرسال إلى الطابعة المختارة',command=send_to_printer).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إغلاق',command=win.destroy).pack(side=LEFT,padx=4)

    def _cancel_record(self, record_id, parent=None):
        reason = ask_text(self.root, 'إلغاء السجل', 'أدخل سبب الإلغاء (إلزامي):')
        if reason is None:
            return
        try:
            core.cancel_record(record_id, self.current_user['id'], reason)
        except USER_ERRORS as exc:
            show_error(parent or self.root, 'لا يمكن الإلغاء', exc)
            return
        self.refresh_records()
        if parent is not None and parent.winfo_exists():
            parent.destroy()
        messagebox.showinfo('تم الإلغاء', 'تم إلغاء السجل مع الاحتفاظ به وبأثر التدقيق.', parent=self.root)

    def cancel_selected(self):
        rid = self._need_selection('حدد السجل المراد إلغاؤه.')
        if rid:
            self._cancel_record(rid)

    def _return_exit(self, record_id, parent=None):
        try:
            slip = core.return_from_exit(record_id, self.current_user['id'])
        except USER_ERRORS as exc:
            show_error(parent or self.root, 'لا يمكن تسجيل العودة', exc)
            return
        self.refresh_records()
        if parent is not None and parent.winfo_exists():
            parent.destroy()
        self.show_record(slip['id'], offer_print=True)

    def return_selected_exit(self):
        rid = self._need_selection('حدد تصريح الخروج أولًا.')
        if rid:
            self._return_exit(rid)

    # ------------------------------------------------------------ students
    def manage_students(self):
        if self.current_user['role'] != 'admin':
            messagebox.showwarning('صلاحيات محدودة', 'إدارة قائمة التلاميذ واستيرادها متاحة لمدير النظام فقط.', parent=self.root)
            return
        win = Toplevel(self.root)
        win.title('إدارة التلاميذ')
        win.geometry('930x600')
        win.transient(self.root)
        ttk.Label(win, text='قائمة التلاميذ', style='Title.TLabel').pack(pady=(16, 4))
        ttk.Label(win, text='يستورد التطبيق ملف Excel/CSV أو ملف HTML المُصدّر من Excel؛ السجلات السابقة لا تُحذف عند تحديث القائمة.', wraplength=800).pack(pady=5)
        top = ttk.Frame(win)
        top.pack(fill=X, padx=18, pady=10)
        ttk.Button(top, text='استيراد / تحديث القائمة', command=lambda: self._import_students(win, refresh), style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(top, text='تصدير القائمة إلى CSV', command=self.export_students).pack(side=RIGHT, padx=4)
        q = StringVar()
        ttk.Label(top, text='بحث:').pack(side=RIGHT, padx=(18, 4))
        ent = ttk.Entry(top, textvariable=q, justify='right')
        ent.pack(side=RIGHT, fill=X, expand=True)
        cols = ('student_key', 'name', 'class_name', 'gender', 'active')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        sort_state = {'column': None, 'reverse': False}
        def sort_students(column):
            items = [(tree.set(item, column), item) for item in tree.get_children('')]
            reverse = sort_state['reverse'] if sort_state['column'] == column else False
            items.sort(key=lambda pair: str(pair[0]).casefold(), reverse=reverse)
            for index, (_value, item) in enumerate(items): tree.move(item, '', index)
            sort_state.update(column=column, reverse=not reverse)
        for col, title, width in [('student_key', 'رقم التعريف', 160), ('name', 'اسم التلميذ', 250), ('class_name', 'القسم', 300), ('gender', 'الجنس', 90), ('active', 'الحالة', 80)]:
            tree.heading(col, text=title, command=lambda c=col: sort_students(c))
            tree.column(col, width=width, anchor=E)
        tree.pack(fill=BOTH, expand=True, padx=18, pady=(0, 6))
        bottom = ttk.Frame(win)
        bottom.pack(fill=X, padx=18, pady=(0, 12))

        def profile():
            if not tree.selection():
                messagebox.showinfo('اختر تلميذًا', 'حدد تلميذًا من الجدول أولًا.', parent=win)
                return
            self.show_student_profile(int(tree.selection()[0]))
        ttk.Button(bottom, text='ملف التلميذ الشامل', command=profile).pack(side=RIGHT, padx=4)
        def edit_selected():
            if not tree.selection():
                messagebox.showinfo('اختر تلميذًا', 'حدد تلميذًا لتعديل بياناته.', parent=win)
                return
            self.edit_student(int(tree.selection()[0]), refresh)
        ttk.Button(bottom, text='تعديل بيانات التلميذ', command=edit_selected, style='Accent.TButton').pack(side=RIGHT, padx=4)

        def refresh(*_):
            for it in tree.get_children():
                tree.delete(it)
            term = f'%{q.get().strip()}%'
            with db_connect() as conn:
                rows = conn.execute("SELECT * FROM students WHERE student_key LIKE ? OR last_name LIKE ? OR first_name LIKE ? OR class_name LIKE ? "
                                    "ORDER BY last_name,first_name LIMIT 1000", (term,) * 4).fetchall()
            for s in rows:
                tree.insert('', END, iid=str(s['id']), values=(s['student_key'], student_name(s), s['class_name'], s['gender'], 'نشط' if s['active'] else 'غير نشط'))
        tree.bind('<Double-1>', lambda _e: profile())
        q.trace_add('write', refresh)
        refresh()

    def _import_students(self, parent, after):
        default_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        path = filedialog.askopenfilename(parent=parent, title='اختر قائمة التلاميذ', initialdir=str(default_dir),
                                          filetypes=[('قوائم التلاميذ', '*.xlsx *.xls *.csv *.html *.htm'), ('كل الملفات', '*.*')])
        if not path:
            return
        full = messagebox.askyesno('نوع القائمة', 'هل هذه القائمة الكاملة لجميع التلاميذ الحاليين؟\n\n'
                                   'نعم: التلاميذ غير الموجودين فيها يصبحون «غير نشطين» (لا يُحذفون وتبقى سجلاتهم).\n'
                                   'لا: تُضاف/تُحدَّث الأسماء فقط دون إيقاف أحد (مثلًا قائمة قسم واحد).', parent=parent)
        try:
            rep = core.import_students(path, self.current_user['id'], deactivate_missing=full)
        except USER_ERRORS as exc:
            show_error(parent, 'تعذر استيراد الملف', exc)
            return
        msg = f'أضيف {rep.inserted} تلميذًا جديدًا.\nحُدّثت بيانات {rep.updated} تلميذًا.\n'
        if full:
            msg += f'أُوقف {rep.deactivated} تلميذًا غير موجود في القائمة.\n'
        if rep.renamed:
            msg += f'تنبيه: تغيّر اسم {rep.renamed} تلميذًا مع بقاء رقم التعريف؛ راجع سجل التدقيق.\n'
        if rep.skipped_no_id:
            msg += f'تنبيه: تُجوهل {rep.skipped_no_id} صفًا بلا رقم تعريف.\n'
        if rep.duplicate_rows:
            msg += f'تنبيه: تُجوهل {rep.duplicate_rows} صفًا مكررًا متطابقًا.\n'
        messagebox.showinfo('اكتمل الاستيراد', msg + 'لم تُحذف سجلات الوصول والتصاريح القديمة.', parent=parent)
        after()
        self.refresh_dashboard()

    def export_students(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='تصدير قائمة التلاميذ', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            n = core.export_students_csv(path, self.current_user['id'])
            messagebox.showinfo('تم التصدير', f'حُفظت القائمة ({n} تلميذًا).', parent=self.root)
        except USER_ERRORS as exc:
            show_error(self.root, 'خطأ في التصدير', exc)

    def edit_student(self, student_id: int, on_saved=None):
        if self.current_user['role'] != 'admin':
            messagebox.showwarning('صلاحيات محدودة', 'تعديل بيانات التلاميذ متاح لمدير النظام فقط.', parent=self.root)
            return
        with db_connect() as conn:
            student = conn.execute('SELECT * FROM students WHERE id=?', (int(student_id),)).fetchone()
        if not student:
            messagebox.showwarning('التلميذ غير موجود', 'تعذر العثور على بيانات التلميذ.', parent=self.root)
            return
        win=Toplevel(self.root); win.title('تعديل بيانات التلميذ'); win.geometry('620x560'); win.minsize(500,470); win.transient(self.root)
        ttk.Label(win,text='تعديل بيانات التلميذ',style='Title.TLabel').pack(pady=(16,5))
        ttk.Label(win,text=f"رقم التعريف الثابت: {student['student_key']} (لا يمكن تغييره من هنا)").pack(pady=(0,10))
        form=ttk.Frame(win);form.pack(fill=BOTH,expand=True,padx=28,pady=8)
        fields=[('last_name','اللقب'),('first_name','الاسم'),('class_name','القسم'),('gender','الجنس'),('year','السنة'),('branch','الشعبة'),('section','رمز القسم')]
        variables={}
        for key,label in fields:
            row=ttk.Frame(form);row.pack(fill=X,pady=6)
            ttk.Label(row,text=label,width=20,anchor=E).pack(side=RIGHT,padx=5)
            variables[key]=StringVar(value=str(student[key] or ''))
            ttk.Entry(row,textvariable=variables[key],justify='right').pack(side=RIGHT,fill=X,expand=True)
        def save():
            try:
                core.update_student_as(self.current_user['id'],student_id,{k:v.get() for k,v in variables.items()})
            except USER_ERRORS as exc:
                show_error(win,'تعذر حفظ التعديل',exc);return
            win.destroy()
            if on_saved:on_saved()
            self.refresh_dashboard()
            messagebox.showinfo('تم التعديل','حُدثت بيانات التلميذ مع تسجيل التعديل في سجل التدقيق. بقي رقم التعريف وسجلات الرخص كما هي.',parent=self.root)
        bar=ttk.Frame(win);bar.pack(fill=X,padx=22,pady=14)
        ttk.Button(bar,text='حفظ التعديلات',command=save,style='Accent.TButton').pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إلغاء',command=win.destroy).pack(side=RIGHT,padx=4)

    def show_student_profile(self, student_id: int):
        with db_connect() as conn:
            student = conn.execute('SELECT * FROM students WHERE id=?', (student_id,)).fetchone()
            if not student:
                return
            records = conn.execute("SELECT r.*,u.display_name AS supervisor FROM records r LEFT JOIN users u ON u.id=r.user_id WHERE r.student_key=? ORDER BY r.id DESC",
                                   (student['student_key'],)).fetchall()
        total_absence_days = 0
        late_count = 0
        return_after_absence = 0
        exit_count = 0
        open_exit_count = 0
        for r in records:
            if r['status'] == 'cancelled':
                continue
            if r['kind'] == 'late': late_count += 1
            elif r['kind'] == 'absence_return':
                return_after_absence += 1
                try: total_absence_days += int(json.loads(r['details_json'] or '{}').get('days_count', 0) or 0)
                except (ValueError, TypeError): pass
            elif r['kind'] == 'exit_permit':
                exit_count += 1
                if r['status'] == 'valid': open_exit_count += 1
        win = Toplevel(self.root)
        win.title('الملف الشامل للتلميذ')
        win.geometry('1120x700')
        win.minsize(850, 550)
        win.transient(self.root)
        ttk.Label(win, text=student_name(student), style='Title.TLabel').pack(pady=(14, 3))
        ttk.Label(win, text=f"رقم التعريف: {student['student_key']}   |   القسم: {student['class_name']}   |   الحالة: {'نشط' if student['active'] else 'غير نشط'}").pack(pady=(0, 8))
        cards = ttk.Frame(win); cards.pack(fill=X, padx=12, pady=6)
        metrics = [('عدد العمليات', len(records)), ('مرات التأخر', late_count), ('العودة بعد الغياب', return_after_absence),
                   ('أيام الغياب المدخلة', total_absence_days), ('تصاريح الخروج', exit_count), ('خروج دون عودة', open_exit_count)]
        for title, val in metrics:
            box = ttk.Frame(cards, style='Surface.TFrame', padding=(8, 7)); box.pack(side=RIGHT, fill=X, expand=True, padx=3)
            ttk.Label(box, text=title, style='CardTitle.TLabel').pack(anchor=E)
            ttk.Label(box, text=str(val), style='CardValue.TLabel').pack(anchor=E)
        ttk.Label(win, text='السجل الزمني الكامل لرخص الدخول والخروج والإلغاءات وإثباتات العودة', style='Section.TLabel').pack(anchor=E, padx=16, pady=(8, 4))
        cols = ('record_no', 'kind', 'created_at', 'reason', 'status', 'supervisor')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        heads = [('record_no','رقم الرخصة',135),('kind','نوع العملية',195),('created_at','التاريخ والوقت',155),('reason','السبب / الملاحظة',260),('status','الحالة',85),('supervisor','المشرف',130)]
        for col,title,width in heads:
            tree.heading(col,text=title); tree.column(col,width=width,anchor=E)
        tree.tag_configure('late', background='#FFF8E8'); tree.tag_configure('absence', background='#EEF7FF')
        tree.tag_configure('exit', background='#F0F7F2'); tree.tag_configure('cancelled', foreground='#A12D2D')
        history_wrap = ttk.Frame(win)
        history_wrap.pack(fill=BOTH, expand=True, padx=12, pady=6)
        history_y = ttk.Scrollbar(history_wrap, orient='vertical', command=tree.yview)
        history_x = ttk.Scrollbar(history_wrap, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=history_y.set, xscrollcommand=history_x.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        history_y.pack(side=RIGHT, fill=Y)
        history_x.pack(side='bottom', fill=X)
        for r in records:
            typetag = 'late' if r['kind']=='late' else ('absence' if r['kind']=='absence_return' else 'exit')
            tags = ('cancelled',) if r['status']=='cancelled' else (typetag,)
            tree.insert('', END, iid=str(r['id']), values=(r['record_no'], kind_label(r['kind']), r['issued_at'] or r['created_at'], r['reason'] or '—', status_label(r['status']), r['supervisor'] or '—'), tags=tags)
        tree.bind('<Double-1>', lambda _e: self.show_record(int(tree.selection()[0])) if tree.selection() else None)
        ttk.Label(win, text='مدة الغياب التراكمية محسوبة من فترات العودة بعد الغياب التي أدخلها المشرف يدويًا، وليست سجل غياب يوميًا تلقائيًا.', wraplength=1000).pack(pady=(4, 9))
        bar = ttk.Frame(win); bar.pack(fill=X, padx=12, pady=(0, 12))
        ttk.Button(bar, text='عرض تفاصيل العملية المحددة', command=lambda: self.show_record(int(tree.selection()[0])) if tree.selection() else messagebox.showinfo('اختيار', 'حدد عملية أولًا.', parent=win), style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إغلاق', command=win.destroy).pack(side=RIGHT, padx=4)

    # ------------------------------------------------------------ logs / stats
    def open_logs(self):
        win = Toplevel(self.root)
        win.title('سجل العمليات — البحث والتصفية والتقارير')
        win.geometry('1300x760')
        win.minsize(920, 580)
        win.transient(self.root)
        search_frame = ttk.Frame(win); search_frame.pack(fill=X, padx=14, pady=(12, 5))
        q = StringVar()
        ttk.Label(search_frame, text='بحث بالرقم أو اسم التلميذ أو رقم التعريف أو السبب أو المشرف:').pack(side=RIGHT, padx=5)
        ent = ttk.Entry(search_frame, textvariable=q, justify='right'); ent.pack(side=RIGHT, fill=X, expand=True)
        filters = ttk.Frame(win); filters.pack(fill=X, padx=14, pady=(0, 8))
        date_from, date_to = StringVar(), StringVar()
        kinds = {'late': 'رخصة الدخول — تأخر', 'absence_return': 'رخصة الدخول — عودة بعد الغياب', 'exit_permit': 'رخصة خروج استثنائي', 'exit_return': 'إثبات عودة من خروج استثنائي'}
        kind = ComboStringVar(['الكل'] + list(kinds.values()), value='الكل')
        status = ComboStringVar(['الكل', 'ساري', 'ملغى', 'تمت العودة'], value='الكل')
        ttk.Label(filters, text='النوع:').pack(side=RIGHT, padx=(6, 2))
        ttk.Combobox(filters, textvariable=kind, values=['الكل'] + list(kinds.values()), state='readonly', width=22, justify='right').pack(side=RIGHT, padx=3)
        ttk.Label(filters, text='الحالة:').pack(side=RIGHT, padx=(6, 2))
        ttk.Combobox(filters, textvariable=status, values=['الكل', 'ساري', 'ملغى', 'تمت العودة'], state='readonly', width=10, justify='right').pack(side=RIGHT, padx=3)
        with db_connect() as conn:
            user_rows = conn.execute('SELECT id,username,display_name FROM users ORDER BY display_name').fetchall()
            class_rows = conn.execute("SELECT class_name FROM (SELECT DISTINCT class_name FROM students UNION SELECT DISTINCT class_name FROM records) WHERE TRIM(class_name)<>'' ORDER BY class_name").fetchall()
        supervisor_map = {'الكل': None}
        for u in user_rows: supervisor_map[f"{u['display_name']} ({u['username']})"] = int(u['id'])
        supervisor = ComboStringVar(list(supervisor_map), value='الكل')
        ttk.Label(filters, text='المشرف:').pack(side=RIGHT, padx=(6, 2))
        ttk.Combobox(filters, textvariable=supervisor, values=list(supervisor_map), state='readonly', width=18, justify='right').pack(side=RIGHT, padx=3)
        class_values = ['الكل'] + [r['class_name'] for r in class_rows]
        class_filter = ComboStringVar(class_values, value='الكل')
        ttk.Label(filters, text='القسم:').pack(side=RIGHT, padx=(6, 2))
        ttk.Combobox(filters, textvariable=class_filter, values=class_values, state='readonly', width=16, justify='right').pack(side=RIGHT, padx=3)
        row2 = ttk.Frame(win); row2.pack(fill=X, padx=14, pady=(0, 7))
        ttk.Label(row2, text='من:').pack(side=RIGHT, padx=(5, 2)); ttk.Entry(row2, textvariable=date_from, width=12, justify='center').pack(side=RIGHT, padx=3)
        ttk.Label(row2, text='إلى:').pack(side=RIGHT, padx=(5, 2)); ttk.Entry(row2, textvariable=date_to, width=12, justify='center').pack(side=RIGHT, padx=3)
        ttk.Button(row2, text='تطبيق الفلاتر', command=lambda: refresh(), style='Accent.TButton').pack(side=RIGHT, padx=8)
        ttk.Button(row2, text='مسح الفلاتر', command=lambda: clear_filters()).pack(side=RIGHT, padx=3)
        result_count = DisplayStringVar(value='')
        ttk.Label(row2, textvariable=result_count).pack(side=LEFT, padx=8)
        cols = ('record_no', 'kind', 'student_name', 'class_name', 'created_at', 'supervisor', 'status', 'printed_count')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        headings = [('record_no', 'الرقم المرجعي', 135), ('kind', 'نوع الرخصة', 185), ('student_name', 'التلميذ', 190), ('class_name', 'القسم', 145),
                    ('created_at', 'التاريخ والوقت', 145), ('supervisor', 'المشرف', 115), ('status', 'الحالة', 85), ('printed_count', 'الطباعة', 65)]
        for col,title,width in headings:
            tree.heading(col,text=title,command=lambda c=col: sort_tree(c)); tree.column(col,width=width,anchor=E,stretch=True)
        tree.tag_configure('late', background='#FFF8E8'); tree.tag_configure('absence', background='#EEF7FF')
        tree.tag_configure('exit', background='#F0F7F2'); tree.tag_configure('cancelled', foreground='#A12D2D')
        yscroll=ttk.Scrollbar(win,orient='vertical',command=tree.yview)
        xscroll=ttk.Scrollbar(win,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set,xscrollcommand=xscroll.set)
        tree.pack(side=LEFT,fill=BOTH,expand=True,padx=(14,0),pady=(0,8)); yscroll.pack(side=RIGHT,fill=Y,padx=(0,14),pady=(0,8))
        xscroll.pack(side='bottom',fill=X,padx=14,pady=(0,4))
        state = {'rows': [], 'sort': {}}

        def current_filters():
            return {'query': q.get().strip(), 'kind': {v:k for k,v in kinds.items()}.get(kind.get()),
                    'status': {'ساري':'valid','ملغى':'cancelled','تمت العودة':'returned'}.get(status.get()),
                    'user_id': supervisor_map.get(supervisor.get()),
                    'class_name': None if class_filter.get()=='الكل' else class_filter.get(),
                    'date_from': core.normalize_digits(date_from.get()).strip(), 'date_to': core.normalize_digits(date_to.get()).strip()}
        def refresh():
            try:
                rows = core.query_records(self.current_user['id'], current_filters())
            except ValueError as exc:
                messagebox.showwarning('تحقق من الفلاتر', str(exc), parent=win); return
            state['rows'] = rows
            for item in tree.get_children(): tree.delete(item)
            for r in rows:
                tag = 'late' if r['kind']=='late' else ('absence' if r['kind']=='absence_return' else 'exit')
                tags = ('cancelled',) if r['status']=='cancelled' else (tag,)
                tree.insert('',END,iid=str(r['id']),values=(r['record_no'],kind_label(r['kind']),r['student_name'],r['class_name'],
                    r['issued_at'] or r['created_at'],r.get('supervisor') or '—',status_label(r['status']),r['printed_count']),tags=tags)
            result_count.set(f'عدد النتائج: {len(rows)}')
        def clear_filters():
            q.set(''); kind.set('الكل'); status.set('الكل'); supervisor.set('الكل'); class_filter.set('الكل'); date_from.set(''); date_to.set(''); refresh()
        def sort_tree(column):
            values=[(tree.set(item,column),item) for item in tree.get_children('')]
            reverse=state['sort'].get(column,False)
            values.sort(key=lambda pair: str(pair[0]).casefold(),reverse=reverse)
            for i,(_v,item) in enumerate(values): tree.move(item,'',i)
            state['sort'][column]=not reverse
        def selected_details(_event=None):
            if not tree.selection():
                messagebox.showinfo('اختر سجلًا','حدد سجلًا من الجدول أولًا.',parent=win); return
            self.show_record(int(tree.selection()[0]))
        def export_filtered_csv():
            path=filedialog.asksaveasfilename(parent=win,title='تصدير النتائج المصفاة',defaultextension='.csv',filetypes=[('CSV','*.csv')])
            if not path:return
            try: n=core.export_filtered_records_csv(path,self.current_user['id'],current_filters())
            except Exception as exc: show_error(win,'تعذر التصدير',exc); return
            messagebox.showinfo('تم التصدير',f'تم تصدير {n} سجلًا بعد تطبيق الفلاتر.',parent=win)
        def export_filtered_pdf():
            path=filedialog.asksaveasfilename(parent=win,title='إنشاء تقرير PDF للنتائج المصفاة',defaultextension='.pdf',filetypes=[('PDF','*.pdf')])
            if not path:return
            try: out=core.create_filtered_records_report_pdf(path,current_filters(),self.current_user['id'])
            except Exception as exc: show_error(win,'تعذر إنشاء التقرير',exc); return
            core.open_pdf(out)
        bar=ttk.Frame(win); bar.pack(fill=X,padx=14,pady=(0,12))
        ttk.Button(bar,text='عرض التفاصيل',command=selected_details,style='Accent.TButton').pack(side=RIGHT,padx=3)
        ttk.Button(bar,text='إعادة طباعة',command=lambda: self._reprint_by_id(int(tree.selection()[0]),win) if tree.selection() else messagebox.showinfo('اختيار','حدد رخصة أولًا.',parent=win)).pack(side=RIGHT,padx=3)
        ttk.Button(bar,text='تصدير النتائج CSV',command=export_filtered_csv).pack(side=LEFT,padx=3)
        ttk.Button(bar,text='تقرير PDF قابل للطباعة',command=export_filtered_pdf).pack(side=LEFT,padx=3)
        ttk.Button(bar,text='تصدير كل السجل',command=self.export_records).pack(side=LEFT,padx=3)
        tree.bind('<Double-1>',selected_details); ent.bind('<Return>',lambda _e:refresh()); ent.bind('<KeyRelease>',lambda _e:refresh())
        refresh()

    def export_records(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='تصدير سجل العمليات', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            n = core.export_records_csv(path, self.current_user['id'])
            messagebox.showinfo('التصدير', f'تم تصدير {n} سجلًا.', parent=self.root)
        except USER_ERRORS as exc:
            show_error(self.root, 'خطأ', exc)

    def monthly_report(self):
        month = date.today()
        suggested = f"SchoolPass_Report_{month:%Y_%m}.pdf"
        path = filedialog.asksaveasfilename(parent=self.root, title='حفظ التقرير الشهري',
                                            initialfile=suggested, defaultextension='.pdf',
                                            filetypes=[('تقرير PDF', '*.pdf')])
        if not path:
            return
        try:
            out = core.create_monthly_report_pdf(path, month.year, month.month, self.current_user['id'])
        except Exception as exc:
            show_error(self.root, 'تعذر إنشاء التقرير', exc)
            return
        core.open_pdf(out)
        messagebox.showinfo('التقرير الشهري جاهز', f'تم إنشاء التقرير وحفظه في:\n{out}\n\nفُتح الملف للمعاينة والطباعة.', parent=self.root)

    def show_stats(self):
        win = Toplevel(self.root)
        win.title('الإحصائيات والرسوم البيانية')
        win.geometry('1050x760')
        win.minsize(820, 600)
        win.transient(self.root)
        ttk.Label(win, text='لوحة الإحصائيات والتقارير', style='Title.TLabel').pack(pady=(14, 5))
        controls = ttk.Frame(win); controls.pack(fill=X,padx=18,pady=5)
        period = StringVar(value='الأسبوع')
        ttk.Label(controls,text='الفترة البيانية:').pack(side=RIGHT,padx=5)
        combo=ttk.Combobox(controls,textvariable=period,state='readonly',values=('اليوم','الأسبوع','الشهر'),width=12,justify='right')
        combo.pack(side=RIGHT,padx=5)
        chart=Canvas(win,height=220,background='#FFFFFF',highlightthickness=1,highlightbackground='#E1EAE5')
        chart.pack(fill=X,padx=18,pady=8)
        def draw_chart(_e=None):
            data=core.dashboard_series({'اليوم':'day','الأسبوع':'week','الشهر':'month'}.get(period.get(),'week'))
            chart.delete('all'); chart.update_idletasks(); w=max(600,chart.winfo_width()); h=max(180,chart.winfo_height())
            l,r,t,b=45,w-16,14,h-34; chart.create_line(l,b,r,b,fill='#D9E4DE')
            series=[('late','تأخر','#D7A33B'),('absence_return','عودة بعد غياب','#4A89B8'),('exit_permit','خروج','#2D8062'),('exit_return','إثبات عودة','#8A78B7')]
            mx=max([1]+[n for arr in data['series'].values() for n in arr]); gw=(r-l)/max(1,len(data['labels'])); bw=max(2,min(10,gw/5))
            for tick in range(4):
                y=b-(b-t)*tick/3; chart.create_line(l,y,r,y,fill='#EDF2EF'); chart.create_text(l-8,y,text=str(round(mx*tick/3)),anchor='e',font=('Segoe UI',8),fill='#667A73')
            for i,lab in enumerate(data['labels']):
                cx=l+i*gw+gw/2
                for j,(key,_name,color) in enumerate(series):
                    n=data['series'][key][i]; bh=(b-t)*n/mx
                    if n: chart.create_rectangle(cx+(j-1.5)*bw,b-bh,cx+(j-1.5)*bw+bw-1,b,fill=color,outline='')
                if len(data['labels'])<=10 or i%5==0 or i==len(data['labels'])-1: chart.create_text(cx,b+13,text=lab,font=('Segoe UI',7),fill='#667A73')
            x=r-5
            for _k,label,color in reversed(series):
                chart.create_rectangle(x-88,3,x-80,11,fill=color,outline=''); chart.create_text(x-92,7,text=label,anchor='e',font=('Segoe UI',8),fill='#52665E'); x-=110
        combo.bind('<<ComboboxSelected>>',draw_chart)
        frame=ttk.Frame(win);frame.pack(fill=BOTH,expand=True,padx=18,pady=8)
        text_widget=__import__('tkinter').Text(frame,wrap='word',font=('Segoe UI',10),height=18)
        sb=ttk.Scrollbar(frame,command=text_widget.yview);text_widget.configure(yscrollcommand=sb.set)
        text_widget.pack(side=LEFT,fill=BOTH,expand=True);sb.pack(side=RIGHT,fill=Y)
        def refresh_text():
            st=core.compute_stats();text_widget.configure(state='normal');text_widget.delete('1.0',END)
            sections=[('الإجماليات',[f"عدد العمليات اليوم: {st['today_total']}",f"عدد التلاميذ أصحاب الرخص اليوم: {st['today_unique_students']}",f"آخر 7 أيام: {st['week_total']} عملية / {st['week_unique_students']} تلميذًا",f"هذا الشهر: {st['month_total']} عملية / {st['month_unique_students']} تلميذًا"]),
                ('عمليات الأسبوع حسب النوع والحالة',[f"{kind_label(r['kind'])} — {status_label(r['status'])}: {r['n']}" for r in st['week_by_kind']]),
                ('عمليات اليوم حسب النوع والحالة',[f"{kind_label(r['kind'])} — {status_label(r['status'])}: {r['n']}" for r in st['today_by_kind']]),
                ('عمليات الشهر حسب النوع والحالة',[f"{kind_label(r['kind'])} — {status_label(r['status'])}: {r['n']}" for r in st['month_by_kind']]),
                ('حسب القسم هذا الشهر',[f"{r['class_name']} — {kind_label(r['kind'])}: {r['n']}" for r in st['by_class']]),
                ('حسب المشرف هذا الشهر',[f"{r['supervisor']} — {kind_label(r['kind'])}: {r['n']}" for r in st['by_supervisor']]),
                ('أكثر أسباب العمليات',[f"{kind_label(r['kind'])} — {r['reason']}: {r['n']}" for r in st['by_reason']]),
                ('فترات الغياب المدخلة يدويًا',[f"{r['student_name']} — {r['class_name']}: {r['n']} فترة، {r['days']} يومًا" for r in st['absence_totals']])]
            for title,lines in sections:
                text_widget.insert(END,rtl_display(title+'\n'+'—'*40+'\n'))
                text_widget.insert(END,rtl_display(('\n'.join(lines) if lines else 'لا توجد بيانات.')+'\n\n'))
            text_widget.configure(state='disabled')
        bar=ttk.Frame(win);bar.pack(fill=X,padx=18,pady=(0,12))
        ttk.Button(bar,text='تحديث الإحصائيات',command=refresh_text).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إنشاء التقرير الشهري PDF',command=self.monthly_report,style='Accent.TButton').pack(side=RIGHT,padx=4)
        draw_chart();refresh_text()

    # ------------------------------------------------------------ admin
    def manage_users(self):
        if self.current_user['role'] != 'admin':
            return
        win = Toplevel(self.root)
        win.title('إدارة المستخدمين')
        win.geometry('850x510')
        win.transient(self.root)
        ttk.Label(win, text='حسابات المشرفين والمدراء', style='Title.TLabel').pack(pady=(16, 6))
        cols = ('username', 'display_name', 'role', 'active', 'created_at')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        for col, title, width in [('username', 'اسم الدخول', 150), ('display_name', 'الاسم الظاهر', 220), ('role', 'الدور', 100), ('active', 'الحالة', 90), ('created_at', 'تاريخ الإنشاء', 210)]:
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=E)
        tree.pack(fill=BOTH, expand=True, padx=15, pady=8)
        uid_me = self.current_user['id']

        def refresh():
            for i in tree.get_children():
                tree.delete(i)
            with db_connect() as conn:
                rows = conn.execute('SELECT * FROM users ORDER BY role,username').fetchall()
            for r in rows:
                tree.insert('', END, iid=str(r['id']), values=(r['username'], r['display_name'], 'مدير' if r['role'] == 'admin' else 'مشرف', 'نشط' if r['active'] else 'موقوف', r['created_at']))

        def add():
            d = UserAddDialog(win)
            if d.result:
                try:
                    core.create_user_as(uid_me, d.username, d.display_name, d.password, d.role)
                except USER_ERRORS as exc:
                    show_error(win, 'تعذر إنشاء الحساب', exc)
                refresh()

        def reset():
            if not tree.selection():
                return
            target = int(tree.selection()[0])
            u = get_user(target)
            pw = ask_password(win, 'تعيين كلمة مرور جديدة', f"أدخل كلمة المرور الجديدة للمستخدم {u['username']} (8 أحرف على الأقل):")
            if pw is None:
                return
            try:
                core.reset_password_as(uid_me, target, pw)
                messagebox.showinfo('تم', 'تم تغيير كلمة المرور.', parent=win)
            except USER_ERRORS as exc:
                show_error(win, 'تعذر التغيير', exc)

        def toggle():
            if not tree.selection():
                return
            target = int(tree.selection()[0])
            u = get_user(target)
            try:
                core.set_user_active_as(uid_me, target, not u['active'])
            except USER_ERRORS as exc:
                show_error(win, 'غير مسموح', exc)
            refresh()
        bar = ttk.Frame(win)
        bar.pack(fill=X, padx=15, pady=12)
        ttk.Button(bar, text='إضافة مستخدم', command=add, style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='تغيير كلمة المرور', command=reset).pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إيقاف / تفعيل الحساب', command=toggle).pack(side=RIGHT, padx=4)
        refresh()

    def backup_db(self):
        if self.current_user['role'] != 'admin':
            return
        name = f"SchoolPass_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
        path = filedialog.asksaveasfilename(parent=self.root, title='حفظ نسخة احتياطية', initialfile=name, initialdir=str(DATA_DIR / 'backups'),
                                            defaultextension='.sqlite3', filetypes=[('قاعدة بيانات SQLite', '*.sqlite3')])
        if not path:
            return
        try:
            dest = core.backup_database(path, self.current_user['id'])
        except USER_ERRORS + (Exception,) as exc:
            show_error(self.root, 'فشل النسخ الاحتياطي', exc)
            return
        messagebox.showinfo('تم النسخ الاحتياطي', f'حُفظت النسخة وتم التحقق من سلامتها:\n{dest}\n\nانسخها أيضًا إلى وسيط خارجي (قرص/USB).\n'
                            'تنبيه: أرقام الهوية المشفرة لا تُفك إلا بحساب Windows نفسه على هذا الحاسوب.', parent=self.root)

    def restore_db(self):
        if self.current_user['role'] != 'admin':
            return
        source = filedialog.askopenfilename(parent=self.root, title='اختيار نسخة SchoolPass الاحتياطية',
                                            filetypes=[('نسخ قاعدة بيانات SchoolPass', '*.sqlite3 *.db'), ('كل الملفات', '*.*')])
        if not source:
            return
        if not messagebox.askyesno('تأكيد الاستعادة', 'ستُستبدل بيانات SchoolPass الحالية بمحتويات النسخة المحددة.\n'
                                   'يُنشأ تلقائيًا ملف أمان من البيانات الحالية قبل الاستبدال، ثم يُغلق البرنامج لتسجيل الدخول من جديد.\n\nهل تريد المتابعة؟',
                                   icon='warning', parent=self.root):
            return
        try:
            previous = core.restore_database(source, self.current_user['id'])
        except Exception as exc:  # noqa: BLE001 - shown to the user; data is rolled back by the core
            messagebox.showerror('تعذرت الاستعادة', f'لم تكتمل الاستعادة وبقيت البيانات الحالية كما هي.\n\n{exc}', parent=self.root)
            return
        messagebox.showinfo('تمت الاستعادة', f'تمت استعادة قاعدة البيانات.\nحُفظت البيانات السابقة هنا:\n{previous}\n\n'
                            'سيُغلق التطبيق الآن؛ افتحه وسجّل الدخول بحسابات النسخة المستعادة.', parent=self.root)
        self.root.destroy()

    def settings_dialog(self):
        if self.current_user['role'] != 'admin':
            return
        win = Toplevel(self.root)
        win.title('إعدادات المؤسسة')
        win.geometry('760x850')
        win.transient(self.root)
        ttk.Label(win, text='إعدادات المؤسسة', style='Title.TLabel').pack(pady=(18, 8))
        form = ttk.Frame(win)
        form.pack(fill=X, padx=25, pady=6)
        name = StringVar(value=setting('school_name', SCHOOL_NAME))
        host = StringVar(value=core.qr_host())
        primary_saved, secondary_saved = core.get_theme_colors()
        primary = StringVar(value=primary_saved)
        secondary = StringVar(value=secondary_saved)
        backup_path = StringVar(value=core.setting('external_backup_path', ''))
        late_threshold = StringVar(value=core.setting('late_alert_threshold', '3'))
        rtl_enabled = BooleanVar(value=core.setting('rtl_shaping', '1') != '0')
        ttk.Label(form, text='اسم المؤسسة على الوصول والتصاريح:').pack(anchor=E)
        ttk.Entry(form, textvariable=name, justify='right').pack(fill=X, pady=6)
        ttk.Label(form, text='عنوان هذا الحاسوب داخل رموز QR (يُفضّل عنوان ثابت):').pack(anchor=E, pady=(8, 0))
        ttk.Entry(form, textvariable=host, justify='center').pack(fill=X, pady=6)
        ttk.Button(form, text=f'استعمال العنوان الحالي ({core.get_local_ip()})', command=lambda: host.set(core.get_local_ip())).pack(anchor=E)
        ttk.Label(form, text='طريقة الطباعة: فتح ملف PDF للمعاينة قبل الطباعة (A6 و100%).', style='SubTitle.TLabel').pack(anchor=E, pady=(10, 0))
        ttk.Label(form, text='ألوان الواجهة (يمكن تغييرها في أي وقت):', style='Title.TLabel').pack(anchor=E, pady=(14, 5))
        color_row = ttk.Frame(form)
        color_row.pack(fill=X, pady=3)
        primary_preview = ttk.Label(color_row, text='اللون الرئيسي', width=18, anchor=E)
        primary_preview.pack(side=RIGHT, padx=5)
        primary_value = ttk.Label(color_row, textvariable=primary, width=12, relief='groove', anchor='center', style='ThemePrimary.TLabel')
        primary_value.pack(side=RIGHT, padx=5)
        secondary_preview = ttk.Label(color_row, text='اللون الثانوي', width=18, anchor=E)
        secondary_preview.pack(side=RIGHT, padx=(20, 5))
        secondary_value = ttk.Label(color_row, textvariable=secondary, width=12, relief='groove', anchor='center', style='ThemeSecondary.TLabel')
        secondary_value.pack(side=RIGHT, padx=5)

        theme_style = ttk.Style(win)
        def readable_foreground(value):
            return core.text_color_for(value)

        def paint_swatch(style_name, value):
            theme_style.configure(style_name, background=value, foreground=readable_foreground(value), padding=5)

        def choose_color(var, title, style_name):
            chosen = colorchooser.askcolor(color=var.get(), parent=win, title=title)[1]
            if chosen:
                var.set(chosen.upper())
                paint_swatch(style_name, chosen.upper())

        paint_swatch('ThemePrimary.TLabel', primary.get())
        paint_swatch('ThemeSecondary.TLabel', secondary.get())
        ttk.Button(color_row, text='اختيار الرئيسي', command=lambda: choose_color(primary, 'اختر اللون الرئيسي', 'ThemePrimary.TLabel')).pack(side=RIGHT, padx=4)
        ttk.Button(color_row, text='اختيار الثانوي', command=lambda: choose_color(secondary, 'اختر اللون الثانوي', 'ThemeSecondary.TLabel')).pack(side=RIGHT, padx=4)

        def reset_theme():
            primary.set(core.DEFAULT_THEME_PRIMARY)
            secondary.set(core.DEFAULT_THEME_SECONDARY)
            paint_swatch('ThemePrimary.TLabel', primary.get())
            paint_swatch('ThemeSecondary.TLabel', secondary.get())
        ttk.Button(form, text='استعادة الألوان الافتراضية', command=reset_theme).pack(anchor=E, pady=4)
        ttk.Label(form, text='حد إنذار التأخر المتكرر خلال الشهر (2–20):').pack(anchor=E, pady=(10, 0))
        ttk.Entry(form, textvariable=late_threshold, justify='center', width=8).pack(anchor=E, pady=3)
        ttk.Checkbutton(form, text='تفعيل تشكيل وعرض النصوص العربية (أزل العلامة إذا ظهر التشكيل مضاعفًا)', variable=rtl_enabled).pack(anchor=E, pady=4)
        backup_row = ttk.Frame(form); backup_row.pack(fill=X, pady=(10, 3))
        ttk.Label(backup_row, text='مجلد نسخة احتياطية خارجي (USB أو قرص آخر):').pack(anchor=E)
        ttk.Entry(backup_row, textvariable=backup_path, justify='left').pack(side=LEFT, fill=X, expand=True, padx=4)
        def pick_backup_dir():
            selected = filedialog.askdirectory(parent=win, title='اختيار مجلد النسخ الاحتياطي الخارجي')
            if selected: backup_path.set(selected)
        ttk.Button(backup_row, text='اختيار مجلد', command=pick_backup_dir).pack(side=RIGHT)
        def test_qr():
            ok, url, detail = core.qr_health_check()
            if ok:
                messagebox.showinfo('اختبار QR ناجح', f'خادم التحقق يعمل على هذا الحاسوب.\nالعنوان الذي يمكن اختباره من الهاتف: {url}\nالنتيجة: {detail}', parent=win)
            else:
                messagebox.showerror('فشل اختبار QR', f'تعذر الوصول إلى خادم QR.\n{url}\n{detail}', parent=win)
        ttk.Button(form, text='اختبار رابط QR الآن', command=test_qr).pack(anchor=E, pady=5)
        ttk.Label(win, wraplength=650, justify='right',
                  text='الهاتف والحاسوب يجب أن يكونا على شبكة Wi-Fi نفسها. استخدم عنوانًا ثابتًا للحاسوب. النسخة الخارجية تُفحص بعد نسخها، ويُفضّل تشفير USB بـ BitLocker. تأكد من تفعيل ضبط الوقت والمنطقة الزمنية تلقائيًا في Windows؛ يمنع البرنامج إصدار رخصة إذا كانت الساعة أقدم من توقيت سجل محفوظ.').pack(pady=8)

        def save():
            try:
                core.save_preferences_as(self.current_user['id'], name.get(), host.get(), primary.get(), secondary.get(), late_threshold.get(), backup_path.get(), rtl_enabled.get())
            except USER_ERRORS as exc:
                show_error(win, 'تعذر الحفظ', exc)
                return
            global RTL_SHAPING_ENABLED
            RTL_SHAPING_ENABLED = rtl_enabled.get()
            make_style(self.root, primary.get(), secondary.get())
            win.destroy()
            messagebox.showinfo('تم', 'حُفظت إعدادات المؤسسة وألوان الواجهة. طُبّقت الألوان فورًا.', parent=self.root)

        def new_code():
            if messagebox.askyesno('رمز استرداد جديد', 'سيصبح الرمز القديم غير صالح. متابعة؟', parent=win):
                try:
                    code = core.issue_recovery_code_as(self.current_user['id'])
                except USER_ERRORS as exc:
                    show_error(win, 'تعذر', exc)
                    return
                show_recovery_code(win, code)
        ttk.Button(win, text='حفظ الإعدادات', command=save, style='Accent.TButton').pack(pady=10)
        ttk.Button(win, text='إنشاء رمز استرداد جديد للمدير', command=new_code).pack(pady=4)

    def audit_view(self):
        if self.current_user['role'] != 'admin':
            return
        win = Toplevel(self.root)
        win.title('سجل التدقيق')
        win.geometry('1050x600')
        win.transient(self.root)
        cols = ('happened_at', 'username', 'action', 'target_type', 'target_id', 'description')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        for col, title, width in [('happened_at', 'التاريخ والوقت', 155), ('username', 'المستخدم', 120), ('action', 'العملية', 190), ('target_type', 'النوع', 90), ('target_id', 'المعرف', 90), ('description', 'التفاصيل', 370)]:
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=E)
        tree.pack(fill=BOTH, expand=True, padx=12, pady=12)
        with db_connect() as conn:
            rows = conn.execute('SELECT a.*,u.username FROM audit_logs a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.id DESC LIMIT 2000').fetchall()
        for r in rows:
            tree.insert('', END, values=(r['happened_at'], r['username'] or '—', r['action'], r['target_type'], r['target_id'], r['description']))

    # ------------------------------------------------------------ session
    def poll_qr_events(self):
        try:
            while True:
                token = core.EVENT_QUEUE.get_nowait()
                if self._locked:
                    continue  # no data is shown while the session is locked
                row = record_by_token(token)
                if row:
                    audit(None, 'تحقق بواسطة QR (الشبكة المحلية)', 'record', row['id'], row['record_no'])
                    self.status_text.set(f"تم التحقق من QR للسجل {row['record_no']} ({status_label(row['status'])}) — {now_iso()}؛ راجع سجل العمليات عند الحاجة.")
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(1000, self.poll_qr_events)

    def check_inactivity(self):
        if not self.root.winfo_exists():
            return
        if not self._locked and time.time() - self.last_activity > AUTO_LOCK_SECONDS:
            self._locked = True
            self.root.withdraw()  # hide student data completely while locked
            login = LoginDialog(self.root, lock_mode=True, prefill=self.current_user['username'])
            self._locked = False
            if login.user and int(login.user['id']) == int(self.current_user['id']):
                self.current_user = login.user
                self.last_activity = time.time()
                self.root.deiconify()
            else:
                if login.user:
                    messagebox.showinfo('تبديل الحساب', 'لتغيير الحساب أغلق التطبيق ثم افتحه من جديد.', parent=self.root)
                self.root.destroy()
                return
        self.root.after(5000, self.check_inactivity)

    def close(self):
        if messagebox.askyesno('إغلاق التطبيق', 'هل تريد إغلاق SchoolPass؟', parent=self.root):
            audit(self.current_user['id'], 'إغلاق التطبيق', 'app', '', '')
            try:
                core.automatic_backup_if_due(self.current_user['id'])
            except Exception as exc:
                messagebox.showwarning('تعذر تحديث النسخة الاحتياطية', f'سيُغلق البرنامج، لكن لم يكتمل آخر تحديث للنسخة الاحتياطية.\n{exc}', parent=self.root)
            core.stop_local_server()
            self.root.destroy()


def _log_exception(exc_type, exc, tb):
    import traceback
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f"\n[{now_iso()}] ERROR\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass
    try:
        messagebox.showerror('SchoolPass — خطأ', f'حدث خطأ غير متوقع:\n{exc}\n\nتفاصيله في:\n{LOG_PATH}')
    except Exception:
        pass


def _single_instance() -> bool:
    global _INSTANCE_LOCK
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', SERVER_PORT + 1))
    except OSError:
        s.close()
        return False
    _INSTANCE_LOCK = s
    return True


def main():
    root = Tk()
    # Keep a real, visible Tk owner window during startup. In 0.5.6 the root
    # was withdrawn before creating transient login/setup Toplevels; on some
    # Windows/Tk builds those dialogs never became visible, leaving a silent
    # background process with no MainWindowHandle.
    root.title('SchoolPass — جاري التشغيل')
    root.geometry('430x130')
    root.resizable(False, False)
    root.report_callback_exception = _log_exception
    make_style(root)
    startup_label = ttk.Label(root, text='جاري تهيئة SchoolPass...\nيرجى الانتظار', style='Title.TLabel', justify='center')
    startup_label.pack(expand=True, fill=BOTH, padx=20, pady=18)
    root.update_idletasks()
    root.update()

    def startup_log(message: str):
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"\n[{now_iso()}] STARTUP: {message}\n")
        except Exception:
            pass

    startup_log('visible root created')
    if not _single_instance():
        messagebox.showwarning('SchoolPass', 'البرنامج مفتوح بالفعل على هذا الحاسوب.', parent=root)
        root.destroy()
        return
    startup_log('single-instance lock acquired')
    core.init_db()
    startup_log('database initialized')
    global RTL_SHAPING_ENABLED
    RTL_SHAPING_ENABLED = core.setting('rtl_shaping', '1') != '0'
    core.check_database_integrity()
    startup_log('database integrity checked')
    make_style(root)  # reload the administrator's saved palette before showing the login window
    core.purge_old_prints(30)
    startup_log('startup cleanup complete')
    if not core.has_admin():
        SetupDialog(root)
        if not core.has_admin():
            root.destroy()
            return
    startup_label.configure(text='اكتملت التهيئة.\nيرجى تسجيل الدخول', style='Title.TLabel')
    root.update_idletasks()
    startup_log('opening login dialog')
    login = LoginDialog(root)
    if not login.user:
        root.destroy()
        return
    try:
        core.automatic_backup_if_due(login.user['id'])
        external_error = core.setting('external_backup_last_error', '')
        if external_error:
            messagebox.showwarning('النسخ الخارجي غير متاح', f'تم الاحتفاظ بالنسخة المحلية، لكن تعذر تحديث النسخة على USB/القرص الخارجي:\n{external_error}', parent=root)
    except Exception as backup_exc:
        try:
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"\n[{now_iso()}] AUTO BACKUP ERROR: {backup_exc}\n")
        except OSError:
            pass
        messagebox.showwarning('النسخ الاحتياطي التلقائي', f'تعذر إنشاء النسخة الاحتياطية اليومية تلقائيًا. استخدم النسخ اليدوي من داخل البرنامج.\n\n{backup_exc}', parent=root)
    startup_label.destroy()
    root.deiconify()
    SchoolPassApp(root, login.user)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as log_file:
                log_file.write(f"\n[{now_iso()}] UNHANDLED ERROR\n")
                traceback.print_exc(file=log_file)
            import tkinter as tk
            tmp = tk.Tk()
            tmp.withdraw()
            messagebox.showerror('SchoolPass — خطأ غير متوقع', f'حدث خطأ غير متوقع:\n{exc}\n\nتفاصيل الخطأ في:\n{LOG_PATH}')
            tmp.destroy()
        except Exception:
            print(f'SchoolPass error: {exc}', file=sys.stderr)
        raise
