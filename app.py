# -*- coding: utf-8 -*-
"""SchoolPass 4.9.4 — fast offline desktop interface and unified visual system."""
from __future__ import annotations

import json
import hashlib
import tempfile
import subprocess
import os
import socket
import sys
import time
import re
import threading
import calendar as calendar_module
import webbrowser
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, date
from pathlib import Path
import tkinter as tk
from tkinter import (
    Tk, Toplevel, StringVar, BooleanVar, messagebox, filedialog, colorchooser,
    END, BOTH, LEFT, RIGHT, X, Y, E, Canvas
)
from tkinter import ttk

import schoolpass_core as core
from schoolpass_core import (
    APP_VERSION, SCHOOL_NAME, SERVER_PORT, AUTO_LOCK_SECONDS, DATA_DIR, LOG_PATH,
    PermissionDenied, db_connect, setting, set_setting, audit, date_today, now_iso,
    kind_label, status_label, student_name, record_by_id, get_user,
)

USER_ERRORS = (ValueError, RuntimeError, PermissionDenied, FileNotFoundError, OSError)
_INSTANCE_LOCK: socket.socket | None = None


# ---------------------------------------------------------------- UI helpers
UI_PALETTE: dict[str, str] = {}


def _window_background() -> str:
    """Return the active application canvas color, including during dialog creation."""
    if UI_PALETTE:
        return UI_PALETTE.get('bg', '#F4F7FB')
    try:
        if core.setting('theme', 'light') == 'dark':
            return '#111827'
        _primary, secondary = core.get_theme_colors()
        return secondary
    except Exception:
        return '#F4F7FB'


def application_asset_path(name: str) -> Path:
    """Locate bundled assets in both source runs and PyInstaller onedir builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name

def apply_application_icon(win) -> None:
    icon_path = application_asset_path("app.ico")
    try:
        if icon_path.is_file():
            win.iconbitmap(default=str(icon_path))
    except Exception:
        # Window managers other than Windows may not support ICO files.
        pass


def configure_window(win, title: str, geometry: str = '900x620'):
    """Configure a resizable window without imposing a larger minimum than its screen."""
    win.title(title)
    apply_application_icon(win)
    win.geometry(geometry)
    try:
        match = re.match(r'^(\d+)x(\d+)', str(geometry))
        requested_w, requested_h = (int(match.group(1)), int(match.group(2))) if match else (900, 620)
        screen_w = int(win.winfo_screenwidth() or 1366)
        screen_h = int(win.winfo_screenheight() or 768)
        if isinstance(win, Toplevel):
            win.minsize(min(360, max(320, screen_w - 48)), min(240, max(220, screen_h - 80)))
        else:
            min_w = min(requested_w, max(680, screen_w - 80))
            min_h = min(requested_h, max(440, screen_h - 120))
            win.minsize(min_w, min_h)
        win.resizable(True, True)
    except Exception:
        try:
            win.minsize(min(760, 900), min(520, 620))
        except Exception:
            pass
    try:
        win.configure(background=_window_background())
    except Exception:
        pass
    # Start every dialog in a predictable place: centered over its owner when
    # possible, otherwise centered on the available desktop work area.
    try:
        win.update_idletasks()
        width = max(1, int(win.winfo_width()))
        height = max(1, int(win.winfo_height()))
        owner = getattr(win, 'master', None)
        if owner is not None and owner is not win:
            try:
                owner.update_idletasks()
                owner_w, owner_h = int(owner.winfo_width()), int(owner.winfo_height())
                if owner_w >= width * 0.60 and owner_h >= height * 0.60:
                    x = int(owner.winfo_rootx() + (owner_w - width) / 2)
                    y = int(owner.winfo_rooty() + (owner_h - height) / 2)
                else:
                    raise RuntimeError('Owner is smaller than the dialog')
            except Exception:
                x = int((win.winfo_screenwidth() - width) / 2)
                y = int((win.winfo_screenheight() - height) / 2)
        else:
            x = int((win.winfo_screenwidth() - width) / 2)
            y = int((win.winfo_screenheight() - height) / 2)
        x = max(0, x); y = max(0, y)
        win.geometry(f'{width}x{height}+{x}+{y}')
    except Exception:
        pass


def _shade(hex_color: str, factor: float = 0.82) -> str:
    """Darken a validated #RRGGBB color for hover states."""
    value = hex_color.lstrip('#')
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    return '#%02X%02X%02X' % tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _text_on(hex_color: str) -> str:
    return core.text_color_for(hex_color)


def _rounded_photo_image(root, fill: str, border: str, fallback: str):
    """Create a scalable 9-slice rounded rectangle image for ttk surfaces."""
    size, radius, border_width = 36, 14, 2
    try:
        image = tk.PhotoImage(master=root, width=size, height=size)
    except Exception:
        return None

    def inside(x, y, left, top, right, bottom, corner_radius):
        cx = left + corner_radius if x < left + corner_radius else right - corner_radius if x > right - corner_radius else x
        cy = top + corner_radius if y < top + corner_radius else bottom - corner_radius if y > bottom - corner_radius else y
        return (x - cx) ** 2 + (y - cy) ** 2 <= corner_radius ** 2

    for y in range(size):
        for x in range(size):
            if not inside(x, y, 0, 0, size - 1, size - 1, radius):
                try:
                    image.transparency_set(x, y, True)
                except Exception:
                    image.put(fallback, (x, y))
                continue
            is_inner = inside(
                x, y, border_width, border_width,
                size - 1 - border_width, size - 1 - border_width,
                radius - border_width,
            )
            image.put(fill if is_inner else border, (x, y))
    return image


def _replace_ttk_layout_element(layout, suffixes, replacement):
    """Replace the outer ttk drawing element while preserving its children."""
    replaced = False
    suffixes = tuple(str(value).lower() for value in suffixes)

    def visit(nodes):
        nonlocal replaced
        result = []
        for element, options in nodes or ():
            options = dict(options or {})
            element_name = str(element)
            if not replaced and any(element_name.lower().endswith(suffix) for suffix in suffixes):
                element_name = replacement
                replaced = True
            children = options.get('children')
            if children is not None:
                options['children'] = visit(children)
            result.append((element_name, options))
        return result

    return visit(layout), replaced


def _install_rounded_ttk_style(
    root, style, style_name: str, element_label: str, fill: str, border: str,
    fallback: str, *, focus_border: str | None = None, hover_fill: str | None = None,
    disabled_fill: str | None = None, suffixes=('.field', '.border', '.button', '.background'),
):
    """Install a rounded 9-slice element into one ttk style without changing widget APIs."""
    fingerprint = ''.join(ch if ch.isalnum() else '_' for ch in
                          f'{style_name}_{fill}_{border}_{focus_border}_{hover_fill}_{disabled_fill}')
    element_name = f'SchoolPassRounded_{element_label}_{fingerprint}'
    normal = _rounded_photo_image(root, fill, border, fallback)
    if normal is None:
        return False
    images = [normal]
    states = []
    if focus_border:
        focused = _rounded_photo_image(root, fill, focus_border, fallback)
        if focused is not None:
            images.append(focused)
            states.append(('focus', focused))
    if hover_fill:
        hovered = _rounded_photo_image(root, hover_fill, border, fallback)
        if hovered is not None:
            images.append(hovered)
            states.append(('active', hovered))
    if disabled_fill:
        disabled = _rounded_photo_image(root, disabled_fill, border, fallback)
        if disabled is not None:
            images.append(disabled)
            states.append(('disabled', disabled))
    try:
        # Retain Python references: Tk image elements are blanked if their PhotoImage
        # objects are garbage-collected while the window remains open.
        retained = list(getattr(root, '_schoolpass_rounded_images', ()))
        retained.extend(images)
        root._schoolpass_rounded_images = retained
        style.element_create(
            element_name, 'image', normal, *states,
            border=(12, 12, 12, 12), sticky='nswe',
        )
    except Exception:
        # The element may already exist after a theme refresh; its previous image
        # references are retained on the root and can safely be reused.
        pass
    try:
        layout = style.layout(style_name)
        rounded_layout, changed = _replace_ttk_layout_element(layout, suffixes, element_name)
        if changed:
            style.layout(style_name, rounded_layout)
            return True
        return False
    except Exception:
        return False


def _install_rounded_widget_surfaces(root, style, palette: dict[str, str]):
    """Apply rounded, high-contrast styling to inputs, cards and action buttons."""
    bg = palette.get('bg', '#F3F6FB')
    field = palette.get('field', '#FFFFFF')
    border = palette.get('border', '#D9E2EF')
    primary = palette.get('primary', '#185A8D')
    surface = palette.get('surface', '#FFFFFF')
    _install_rounded_ttk_style(
        root, style, 'TEntry', 'Entry', field, border, bg,
        focus_border=primary, disabled_fill=palette.get('surface_alt', field),
        suffixes=('.field', '.border'),
    )
    _install_rounded_ttk_style(
        root, style, 'TCombobox', 'Combo', field, border, bg,
        focus_border=primary, disabled_fill=palette.get('surface_alt', field),
        suffixes=('.field', '.border'),
    )

    for style_name, fill, edge in (
        ('Surface.TFrame', surface, border),
        ('Card.TFrame', surface, border),
        ('MetricCard.TFrame', surface, border),
        ('MetricCardHover.TFrame', palette.get('primary_soft', surface), palette.get('primary', border)),
        ('DialogCard.TFrame', surface, border),
        ('FormSection.TFrame', surface, border),
        ('DialogHeader.TFrame', palette.get('primary_soft', surface), border),
        ('DialogFooter.TFrame', palette.get('surface_alt', surface), border),
        ('LoginBrand.TFrame', palette.get('sidebar', '#142B49'), palette.get('sidebar_hover', border)),
        ('LoginSurface.TFrame', surface, border),
        ('PageHeader.TFrame', surface, border),
        ('InfoPanel.TFrame', palette.get('primary_soft', surface), border),
        ('SidebarGroupOps.TFrame', palette.get('sidebar_ops_bg', surface), border),
        ('SidebarGroupData.TFrame', palette.get('sidebar_data_bg', surface), border),
        ('SidebarGroupReports.TFrame', palette.get('sidebar_reports_bg', surface), border),
        ('SidebarGroupAdmin.TFrame', palette.get('sidebar_admin_bg', surface), border),
        ('SidebarSubmenu.TFrame', palette.get('sidebar_hover', '#24466E'), palette.get('sidebar_hover', border)),
        ('Hero.TFrame', palette.get('sidebar', '#142B49'), palette.get('sidebar_hover', border)),
        ('HeroBadge.TFrame', palette.get('sidebar_hover', '#24466E'), palette.get('sidebar_hover', border)),
        ('SidebarProfile.TFrame', palette.get('sidebar_hover', '#24466E'), palette.get('sidebar_hover', border)),
    ):
        _install_rounded_ttk_style(
            root, style, style_name, 'Surface', fill, edge, bg,
            suffixes=('.border', '.background'),
        )

    # Buttons use native ttk rendering for crisp text and responsive state changes.



def _center_toplevel_window(win):
    """Center a mapped dialog over its owner, or over the desktop as a fallback."""
    try:
        if win.overrideredirect():
            return
        win.update_idletasks()
        screen_w = int(win.winfo_screenwidth() or 1366)
        screen_h = int(win.winfo_screenheight() or 768)
        # Fit every dialog to its actual widget request after its children have
        # been created; explicit geometry strings are treated as upper bounds,
        # not as empty space to reserve around short forms.
        requested_w = max(1, int(win.winfo_reqwidth()))
        requested_h = max(1, int(win.winfo_reqheight()))
        width = min(max(320, requested_w + 24), max(320, screen_w - 48))
        height = min(max(220, requested_h + 24), max(220, screen_h - 80))
        try:
            win.minsize(width, height)
        except Exception:
            pass
        owner = getattr(win, 'master', None)
        x = y = None
        if owner is not None and owner is not win:
            try:
                owner.update_idletasks()
                owner_w, owner_h = int(owner.winfo_width()), int(owner.winfo_height())
                if owner.winfo_viewable() and owner_w >= width * 0.55 and owner_h >= height * 0.55:
                    x = int(owner.winfo_rootx() + (owner_w - width) / 2)
                    y = int(owner.winfo_rooty() + (owner_h - height) / 2)
            except Exception:
                x = y = None
        if x is None or y is None:
            x = int((screen_w - width) / 2)
            y = int((screen_h - height) / 2)
        x = max(0, min(x, max(0, screen_w - width)))
        y = max(0, min(y, max(0, screen_h - height)))
        win.geometry(f'{width}x{height}+{x}+{y}')
    except Exception:
        pass


def _install_toplevel_background():
    """Apply the selected secondary color to every Tk top-level, not only the main window."""
    if getattr(Toplevel, '_schoolpass_theme_installed', False):
        return
    original_init = Toplevel.__init__
    original_geometry = getattr(Toplevel, 'geometry', None)
    original_minsize = getattr(Toplevel, 'minsize', None)

    def themed_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            self.configure(background=_window_background())
        except Exception:
            pass
        try:
            def center_when_mapped(event):
                if event.widget is self and not getattr(self, '_schoolpass_center_done', False):
                    self._schoolpass_center_done = True
                    self.after_idle(lambda target=self: _center_toplevel_window(target))
            self.bind('<Map>', center_when_mapped, add='+')
        except Exception:
            pass

    def screen_fit_geometry(self, *args):
        # Keep fixed-size dialogs inside the usable desktop area, including on
        # Windows machines where display scaling reduces the logical work area.
        if args and isinstance(args[0], str):
            match = re.match(r'^(\d+)x(\d+)(.*)$', args[0])
            if match:
                try:
                    screen_w = int(self.winfo_screenwidth() or 1366)
                    screen_h = int(self.winfo_screenheight() or 768)
                    requested_w, requested_h = int(match.group(1)), int(match.group(2))
                    # Preserve designed dimensions; arbitrary shrinking clipped buttons.
                    width = min(max(320, requested_w), max(320, screen_w - 48))
                    height = min(max(220, requested_h), max(220, screen_h - 80))
                    args = (f'{width}x{height}{match.group(3)}',) + args[1:]
                except Exception:
                    pass
        if callable(original_geometry):
            return original_geometry(self, *args)
        return None

    def screen_fit_minsize(self, width=None, height=None):
        try:
            screen_w = int(self.winfo_screenwidth() or 1366)
            screen_h = int(self.winfo_screenheight() or 768)
            if width is not None:
                width = min(max(320, int(width)), max(320, screen_w - 48))
            if height is not None:
                height = min(max(220, int(height)), max(220, screen_h - 80))
        except Exception:
            pass
        if callable(original_minsize):
            return original_minsize(self, width, height)
        return None

    Toplevel.__init__ = themed_init
    if callable(original_geometry):
        Toplevel.geometry = screen_fit_geometry
    if callable(original_minsize):
        Toplevel.minsize = screen_fit_minsize
    Toplevel._schoolpass_theme_installed = True

_install_toplevel_background()


class _ThemeState:
    registered = False


_ttkbootstrap_theme_state = _ThemeState()


def make_style(root: Tk, primary: str | None = None, secondary: str | None = None):
    """Apply the SchoolPass 4.6 design system consistently to every window.

    The palette follows a calm Windows desktop hierarchy: restrained navy navigation,
    clear blue primary actions, generous white surfaces, readable Arabic typography,
    and semantic colors reserved for status and risk. Existing ttk widget trees and
    application callbacks are deliberately retained.
    """
    global UI_PALETTE
    try:
        dark = core.setting('theme', 'light') == 'dark'
        saved_primary, saved_secondary = core.get_theme_colors()
    except Exception:
        dark, saved_primary, saved_secondary = False, '#185A8D', '#F2F5F9'
    primary_color = primary or saved_primary
    secondary_color = secondary or saved_secondary
    try:
        primary_color = core._validate_hex_color(primary_color, 'اللون الرئيسي')
        secondary_color = core._validate_hex_color(secondary_color, 'اللون الثانوي')
    except Exception:
        primary_color, secondary_color = '#185A8D', '#F2F5F9'

    if dark:
        palette = {
            'bg': '#0B1422', 'surface': '#142238', 'surface_alt': '#1A2C45',
            'ink': '#F1F6FC', 'muted': '#B2C2D6', 'primary': '#6EA8FE', 'primary_text': '#FFFFFF',
            'primary_hover': '#8AB9FF', 'primary_soft': '#203B60',
            'sidebar': '#071526', 'sidebar_hover': '#193654',
            'sidebar_text': '#F7FAFF', 'sidebar_muted': '#B4C4D8',
            'accent': '#36C5B4', 'gold': '#F0BE5B', 'border': '#3B526F',
            'field': '#152A45', 'heading': '#243A57', 'selected': '#284A73',
            'alert_bg': '#47351E', 'alert_fg': '#FFD79A',
            'good_bg': '#173D37', 'good_fg': '#A8E7D7',
            'danger_bg': '#4A2830', 'danger_fg': '#FFB7BF',
            'canvas': '#1B2738', 'white': '#FFFFFF',
        }
    else:
        # Preserve custom primary/secondary colors set by the administrator while
        # giving the rest of the interface a coherent navy/blue institutional skin.
        palette = {
            'bg': secondary_color if secondary_color not in ('#F2F5F9', '#F2F6F4') else '#E7EDF5',
            'surface': '#FFFFFF', 'surface_alt': '#F0F4F9',
            'ink': '#14243A', 'muted': '#53677F',
            'primary': primary_color, 'primary_text': core.text_color_for(primary_color),
            'primary_hover': '#3B82F6' if primary_color == '#185A8D' else _shade(primary_color, 0.88),
            'primary_soft': '#DCE9F8', 'sidebar': '#10243D',
            'sidebar_hover': '#1E4167', 'sidebar_text': '#F8FBFF',
            'sidebar_muted': '#C5D3E5', 'accent': '#0FA99A',
            'gold': '#E5AC38', 'border': '#8499B2', 'field': '#F1F5FA',
            'heading': '#EAF0F8', 'selected': '#DFEAFF',
            'alert_bg': '#FBEBCB', 'alert_fg': '#744308',
            'good_bg': '#D9F0E7', 'good_fg': '#075D4D',
            'danger_bg': '#F8DEE2', 'danger_fg': '#922B3A',
            'canvas': '#FFFFFF', 'white': '#FFFFFF',
        }
    palette['sidebar_ops_bg'] = '#183652' if dark else '#E7F0FF'
    palette['sidebar_data_bg'] = '#173C3B' if dark else '#E4F5EF'
    palette['sidebar_reports_bg'] = '#3B3152' if dark else '#F2ECFF'
    palette['sidebar_admin_bg'] = '#46351F' if dark else '#FFF2DF'
    palette['sidebar_ops_fg'] = '#C3DFFF' if dark else '#214F83'
    palette['sidebar_data_fg'] = '#B9EBDD' if dark else '#206A58'
    palette['sidebar_reports_fg'] = '#E2D0FF' if dark else '#63468B'
    palette['sidebar_admin_fg'] = '#F1D9A8' if dark else '#875A20'
    palette['button_bg'] = '#243A56' if dark else '#DCE5F0'
    palette['button_hover'] = '#314C6D' if dark else '#C9D8E9'
    palette['button_pressed'] = '#1B2D45' if dark else '#B7C9DE'
    palette['field_focus'] = palette['primary']
    palette['success_button'] = '#167B67' if dark else palette['good_fg']
    palette['danger_button'] = '#A83C4C' if dark else palette['danger_fg']
    UI_PALETTE = palette

    # ttkbootstrap supplies polished native ttk mappings; all application-specific
    # styles are then explicitly configured so they remain consistent in fallback mode.
    try:
        import ttkbootstrap as ttkb
        if not getattr(_ttkbootstrap_theme_state, 'registered', False):
            ttkb.Theme(
                name='schoolpass', primary='#2563B8', success='#0F8D7D',
                info='#367AC4', warning='#C48A31', danger='#B54552',
                light={'background': '#EEF3FA', 'foreground': '#17263D'},
                dark={'background': '#0B1422', 'foreground': '#F1F6FC'},
            ).register()
            _ttkbootstrap_theme_state.registered = True
        target_theme = 'schoolpass-dark' if dark else 'schoolpass-light'
        style = ttkb.Style(theme=target_theme)
        if style.theme_use() != target_theme:
            style.theme_use(target_theme)
    except Exception as exc:
        # Keep the reason visible to the Windows Tk runtime regression test;
        # silently falling back here would erase the ttkbootstrap scaling service
        # required by the rounded image elements below.
        root._schoolpass_theme_init_error = f'{type(exc).__name__}: {exc}'
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except Exception:
            pass

    bg, surface, ink, muted = palette['bg'], palette['surface'], palette['ink'], palette['muted']
    primary_color = palette['primary']
    try:
        root.configure(background=bg)
        root.option_add('*Font', ('Segoe UI', 10))
        root.option_add('*TCombobox*Listbox.font', ('Segoe UI', 10))
        root.option_add('*TCombobox*Listbox.background', palette['field'])
        root.option_add('*TCombobox*Listbox.foreground', ink)
        root.option_add('*TCombobox*Listbox.selectBackground', palette['primary'])
        root.option_add('*TCombobox*Listbox.selectForeground', '#FFFFFF')
        root.option_add('*Listbox.background', palette['field'])
        root.option_add('*Listbox.foreground', ink)
        root.option_add('*Listbox.selectBackground', palette['primary'])
        root.option_add('*Listbox.selectForeground', '#FFFFFF')
        root.option_add('*Listbox.borderWidth', 0)
        root.option_add('*Listbox.relief', 'flat')
        root.option_add('*Text.background', palette['field'])
        root.option_add('*Text.foreground', ink)
        root.option_add('*Text.insertBackground', palette['primary'])
        root.option_add('*Text.highlightThickness', 1)
        root.option_add('*Text.highlightBackground', palette['border'])
        root.option_add('*Text.highlightColor', palette['primary'])
        root.option_add('*Text.relief', 'flat')
        root.option_add('*Text.borderWidth', 1)
        root.option_add('*Text.padX', 10)
        root.option_add('*Text.padY', 8)
        import tkinter.font as tkfont
        for font_name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont', 'TkCaptionFont'):
            try:
                tkfont.nametofont(font_name, root=root).configure(family='Segoe UI', size=10, weight='normal')
            except Exception:
                pass
    except Exception:
        pass

    # Base typography and surfaces
    style.configure('.', font=('Segoe UI', 10), foreground=ink)
    style.configure('TFrame', background=bg)
    style.configure('Surface.TFrame', background=surface, relief='flat', borderwidth=0)
    style.configure('PageHeader.TFrame', background=surface, relief='flat', borderwidth=0)
    style.configure('Card.TFrame', background=surface, relief='flat', borderwidth=1)
    style.configure('MetricCard.TFrame', background=surface, relief='flat', borderwidth=1)
    style.configure('MetricCardHover.TFrame', background=palette['primary_soft'], relief='flat', borderwidth=1)
    style.configure('MetricCardTitleHover.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('MetricCardValueHover.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    font=('Segoe UI', 27, 'bold'), anchor='center')
    style.configure('MetricCardHintHover.TLabel', background=palette['primary_soft'], foreground=muted,
                    font=('Segoe UI', 9), anchor='e')
    style.configure('MetricCardIconHover.TLabel', background=primary_color, foreground='#FFFFFF',
                    font=('Segoe UI Symbol', 17, 'bold'), padding=(11, 9), anchor='center')
    style.configure('KPIAccent.TFrame', background=palette['accent'])
    for _accent_style, _accent_color in (
        ('KPIAccentBlue.TFrame', palette['primary']),
        ('KPIAccentTeal.TFrame', palette['accent']),
        ('KPIAccentGold.TFrame', palette['gold']),
        ('KPIAccentViolet.TFrame', palette['sidebar_reports_fg']),
        ('KPIAccentAmber.TFrame', palette['sidebar_admin_fg']),
        ('KPIAccentRose.TFrame', palette['danger_fg']),
    ):
        style.configure(_accent_style, background=_accent_color)
    for _icon_style, _icon_bg, _icon_fg in (
        ('MetricIconBlue.TLabel', palette['primary_soft'], palette['primary']),
        ('MetricIconTeal.TLabel', palette['good_bg'], palette['good_fg']),
        ('MetricIconGold.TLabel', palette['alert_bg'], palette['alert_fg']),
        ('MetricIconViolet.TLabel', palette['sidebar_reports_bg'], palette['sidebar_reports_fg']),
        ('MetricIconAmber.TLabel', palette['sidebar_admin_bg'], palette['sidebar_admin_fg']),
        ('MetricIconRose.TLabel', palette['danger_bg'], palette['danger_fg']),
    ):
        style.configure(_icon_style, background=_icon_bg, foreground=_icon_fg,
                        font=('Segoe UI Symbol', 17, 'bold'), padding=(11, 9), anchor='center')
    style.configure('Dialog.TFrame', background=surface)
    style.configure('DialogCard.TFrame', background=surface, relief='flat', borderwidth=1)
    style.configure('FormSection.TFrame', background=surface, relief='flat', borderwidth=1)
    style.configure('FormSectionHeader.TFrame', background=palette['surface_alt'], relief='flat', borderwidth=0)
    style.configure('FormSectionTitle.TLabel', background=palette['surface_alt'], foreground=primary_color, font=('Segoe UI', 10, 'bold'), anchor='e')
    style.configure('DialogFooter.TFrame', background=palette['surface_alt'], relief='flat', borderwidth=0)
    style.configure('InfoPanel.TFrame', background=palette['primary_soft'], relief='flat', borderwidth=0)
    style.configure('DialogHeader.TFrame', background=palette['primary_soft'], relief='flat', borderwidth=0)
    style.configure('DialogHeaderTitle.TLabel', background=palette['primary_soft'], foreground=primary_color, font=('Segoe UI', 17, 'bold'), anchor='e')
    style.configure('DialogEyebrow.TLabel', background=palette['primary_soft'], foreground=palette['accent'], font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('Toolbar.TFrame', background=surface, relief='flat', borderwidth=0)
    style.configure('ToolbarBadge.TLabel', background=palette['primary_soft'], foreground=primary_color, padding=(12, 7), font=('Segoe UI', 9, 'bold'))
    style.configure('Sidebar.TFrame', background=palette['sidebar'])
    style.configure('SidebarProfile.TFrame', background=palette['sidebar_hover'], relief='flat')
    style.configure('SidebarSeparator.TFrame', background=palette['sidebar_hover'])
    style.configure('SidebarSubmenu.TFrame', background=palette['sidebar_hover'], relief='flat', borderwidth=0)
    for suffix, bg_key, fg_key in (
        ('Ops', 'sidebar_ops_bg', 'sidebar_ops_fg'),
        ('Data', 'sidebar_data_bg', 'sidebar_data_fg'),
        ('Reports', 'sidebar_reports_bg', 'sidebar_reports_fg'),
        ('Admin', 'sidebar_admin_bg', 'sidebar_admin_fg'),
    ):
        style.configure(f'SidebarGroup{suffix}.TFrame', background=palette[bg_key], relief='flat', borderwidth=0)
        style.configure(f'SidebarGroup{suffix}Title.TLabel', background=palette[bg_key],
                        foreground=palette[fg_key], font=('Segoe UI', 10, 'bold'),
                        padding=(9, 7), anchor='e')
    style.configure('LoginBrand.TFrame', background=palette['sidebar'])
    style.configure('LoginSurface.TFrame', background=surface)
    style.configure('LoginMark.TLabel', background=palette['primary'], foreground=palette['primary_text'],
                    font=('Segoe UI', 22, 'bold'), padding=(16, 12), anchor='center')
    style.configure('LoginBrandTitle.TLabel', background=palette['sidebar'], foreground='#FFFFFF',
                    font=('Segoe UI', 22, 'bold'), anchor='e')
    style.configure('LoginBrandSub.TLabel', background=palette['sidebar'], foreground=palette['sidebar_muted'],
                    font=('Segoe UI', 10), anchor='e', justify='right')
    style.configure('LoginBrandKicker.TLabel', background=palette['sidebar'], foreground='#8DBBEE',
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('LoginBrandFoot.TLabel', background=palette['sidebar'], foreground=palette['sidebar_muted'],
                    font=('Segoe UI', 8), anchor='e')
    style.configure('LoginTitle.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 23, 'bold'), anchor='e')
    style.configure('LoginSubtitle.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 10), anchor='e', justify='right')
    style.configure('FieldLabel.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 10, 'bold'), anchor='e')
    style.configure('Error.TLabel', background=surface, foreground=palette['danger_fg'],
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('Title.TLabel', background=bg, foreground=primary_color,
                    font=('Segoe UI', 22, 'bold'), anchor='e')
    style.configure('PageTitle.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 21, 'bold'), anchor='e')
    style.configure('Subtitle.TLabel', background=bg, foreground=muted,
                    font=('Segoe UI', 10), anchor='e')
    style.configure('PageSubtitle.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 10), anchor='e')
    style.configure('DialogTitle.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 16, 'bold'), anchor='e')
    style.configure('DialogMessage.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 10), padding=(4, 8), anchor='e')
    style.configure('DialogIcon.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    font=('Segoe UI', 20, 'bold'), padding=(10, 8))
    style.configure('Hint.TLabel', background=surface, foreground=muted, font=('Segoe UI', 9))
    style.configure('ShortcutDesc.TLabel', background=surface, foreground=ink, font=('Segoe UI', 10), anchor='e')
    style.configure('ShortcutDivider.TFrame', background=palette['border'])
    style.configure('Section.TLabel', background=surface, foreground=ink,
                    font=('Segoe UI', 13, 'bold'), anchor='e')
    style.configure('PageEyebrow.TLabel', background=surface, foreground=palette['accent'],
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('CardMeta.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 9), anchor='e')
    style.configure('Eyebrow.TLabel', background=surface, foreground=palette['accent'],
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('CardTitle.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('CardValue.TLabel', background=surface, foreground=primary_color,
                    font=('Segoe UI', 27, 'bold'), anchor='center')
    style.configure('Badge.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    padding=(13, 8), font=('Segoe UI', 9, 'bold'))
    style.configure('ToolbarBadge.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    padding=(11, 7), font=('Segoe UI', 9, 'bold'))
    style.configure('FieldHint.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 9), anchor='e')
    style.configure('FormCaption.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 9), anchor='e')
    style.configure('Status.TLabel', background=palette['surface_alt'], foreground=muted,
                    anchor='e', padding=(14, 8), font=('Segoe UI', 9))
    style.configure('StatusStrong.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    anchor='e', padding=(13, 8), font=('Segoe UI', 9, 'bold'))
    style.configure('SidebarTitle.TLabel', background=palette['sidebar'], foreground=palette['sidebar_text'],
                    font=('Segoe UI', 19, 'bold'), anchor='e')
    style.configure('SidebarSub.TLabel', background=palette['sidebar'], foreground=palette['sidebar_muted'],
                    font=('Segoe UI', 9), anchor='e')
    style.configure('SidebarProfileName.TLabel', background=palette['sidebar_hover'],
                    foreground=palette['sidebar_text'], font=('Segoe UI', 10, 'bold'), anchor='e')
    style.configure('SidebarProfileRole.TLabel', background=palette['sidebar_hover'],
                    foreground=palette['sidebar_muted'], font=('Segoe UI', 8), anchor='e')
    # Premium dashboard hero: deliberately independent of the saved school palette,
    # while adapting its surfaces and text for the selected light/dark mode.
    style.configure('Hero.TFrame', background=palette['sidebar'], relief='flat', borderwidth=0)
    style.configure('HeroAccent.TFrame', background=palette['accent'])
    style.configure('HeroTitle.TLabel', background=palette['sidebar'], foreground='#FFFFFF',
                    font=('Segoe UI', 20, 'bold'), anchor='e')
    style.configure('HeroText.TLabel', background=palette['sidebar'], foreground='#D6E3F2',
                    font=('Segoe UI', 10), anchor='e', justify='right')
    style.configure('HeroKicker.TLabel', background=palette['sidebar'], foreground='#8FC8F0',
                    font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('HeroBadge.TFrame', background=palette['sidebar_hover'], relief='flat')
    style.configure('HeroBadgeTitle.TLabel', background=palette['sidebar_hover'], foreground='#FFFFFF',
                    font=('Segoe UI', 15, 'bold'), anchor='center')
    style.configure('HeroBadgeText.TLabel', background=palette['sidebar_hover'], foreground='#C5D8EE',
                    font=('Segoe UI', 8), anchor='center')
    style.configure('MetricIcon.TLabel', background=palette['primary_soft'], foreground=primary_color,
                    font=('Segoe UI Symbol', 18, 'bold'), padding=(11, 9), anchor='center')
    style.configure('MetricCardHover.TFrame', background=palette['primary_soft'], relief='flat', borderwidth=1)
    style.configure('MetricHint.TLabel', background=surface, foreground=muted,
                    font=('Segoe UI', 8), anchor='e')

    # Command buttons: compact and consistent with visible focus and hover states.
    style.configure('TButton', background=palette['button_bg'], foreground=ink, borderwidth=0,
                    focusthickness=2, focuscolor=palette['primary'], padding=(12, 7),
                    font=('Segoe UI', 9, 'bold'), relief='flat', anchor='center')
    style.map('TButton', background=[('disabled', palette['button_bg']), ('pressed', palette['button_pressed']),
                                    ('active', palette['button_hover'])],
              foreground=[('disabled', muted), ('active', ink)])
    style.configure('Accent.TButton', background=primary_color, foreground=palette['primary_text'], borderwidth=0,
                    focusthickness=2, focuscolor=palette['accent'], padding=(14, 8),
                    font=('Segoe UI', 10, 'bold'), relief='flat', anchor='center')
    style.configure('Success.TButton', background=palette['success_button'], foreground='#FFFFFF', borderwidth=0,
                    padding=(15, 10), font=('Segoe UI', 9, 'bold'), relief='flat')
    style.configure('Danger.TButton', background=palette['danger_button'], foreground='#FFFFFF', borderwidth=0,
                    padding=(15, 10), font=('Segoe UI', 9, 'bold'), relief='flat')
    style.configure('Info.TButton', background=palette['primary_soft'], foreground=primary_color, borderwidth=0,
                    padding=(15, 10), font=('Segoe UI', 9, 'bold'), relief='flat')
    style.map('Accent.TButton', background=[('disabled', '#9AA9BA'), ('pressed', _shade(primary_color, 0.72)),
                                            ('active', palette['primary_hover'])],
              foreground=[('disabled', '#F3F6FB'), ('active', palette['primary_text'])])
    style.configure('Gold.TButton', background=palette['gold'], foreground='#26364B', borderwidth=0,
                    padding=(15, 10), font=('Segoe UI', 9, 'bold'), relief='flat')
    style.map('Gold.TButton', background=[('active', _shade(palette['gold'], 0.92)),
                                         ('pressed', _shade(palette['gold'], 0.82))])
    # Large, high-contrast category headers remain prominent while their contents are folded.
    style.configure('NavGroupOps.TButton', background='#245B86', foreground='#FFFFFF',
                    borderwidth=0, padding=(14, 10), anchor='e', font=('Segoe UI', 13, 'bold'), relief='flat')
    style.configure('NavGroupData.TButton', background='#286B62', foreground='#FFFFFF',
                    borderwidth=0, padding=(16, 14), anchor='e', font=('Segoe UI', 13, 'bold'), relief='flat')
    style.configure('NavGroupReports.TButton', background='#66518A', foreground='#FFFFFF',
                    borderwidth=0, padding=(16, 14), anchor='e', font=('Segoe UI', 13, 'bold'), relief='flat')
    style.configure('NavGroupAdmin.TButton', background='#8A6435', foreground='#FFFFFF',
                    borderwidth=0, padding=(16, 14), anchor='e', font=('Segoe UI', 13, 'bold'), relief='flat')
    _group_open_colors = {
        'NavGroupOpsOpen.TButton': '#3475A5',
        'NavGroupDataOpen.TButton': '#34877A',
        'NavGroupReportsOpen.TButton': '#7A65A1',
        'NavGroupAdminOpen.TButton': '#A47A45',
    }
    for _open_style, _open_color in _group_open_colors.items():
        style.configure(_open_style, background=_open_color, foreground='#FFFFFF',
                        borderwidth=1, focuscolor=palette['accent'], padding=(16, 14),
                        anchor='e', font=('Segoe UI', 13, 'bold'), relief='flat')
    for _group_style, _group_color in (
        ('NavGroupOps.TButton', '#3475A5'), ('NavGroupData.TButton', '#34877A'),
        ('NavGroupReports.TButton', '#7A65A1'), ('NavGroupAdmin.TButton', '#A47A45')):
        style.map(_group_style, background=[('active', _group_color), ('pressed', _group_color)],
                  foreground=[('active', '#FFFFFF'), ('pressed', '#FFFFFF')])
    style.configure('Nav.TButton', background=palette['sidebar'], foreground=palette['sidebar_muted'],
                    borderwidth=0, focusthickness=1, focuscolor=palette['accent'], padding=(14, 9), anchor='e',
                    font=('Segoe UI', 10, 'bold'), relief='flat')
    style.map('Nav.TButton', background=[('active', palette['sidebar_hover']),
                                         ('pressed', palette['sidebar_hover'])],
              foreground=[('active', '#FFFFFF')])
    style.configure('NavHover.TButton', background=palette['sidebar_hover'], foreground='#FFFFFF',
                    borderwidth=1, focusthickness=1, focuscolor=palette['accent'],
                    padding=(17, 12), anchor='e', font=('Segoe UI', 10, 'bold'), relief='flat')
    style.configure('NavActive.TButton', background=palette['sidebar_hover'], foreground='#FFFFFF',
                    borderwidth=1, focusthickness=1, focuscolor=palette['accent'],
                    padding=(17, 12), anchor='e', font=('Segoe UI', 10, 'bold'), relief='flat')
    style.configure('SubNav.TButton', background=palette['surface_alt'], foreground=palette['ink'],
                    borderwidth=0, padding=(11, 7), anchor='e',
                    font=('Segoe UI', 9, 'bold'), relief='flat')
    style.map('SubNav.TButton', background=[('active', palette['primary_soft']),
                                            ('pressed', palette['primary_soft'])],
              foreground=[('active', primary_color)])
    style.configure('SubNavHover.TButton', background=palette['primary_soft'], foreground=primary_color,
                    borderwidth=1, padding=(13, 9), anchor='e',
                    font=('Segoe UI', 9, 'bold'), relief='flat')
    style.configure('Link.TButton', background=surface, foreground=primary_color, borderwidth=0,
                    padding=(8, 7), font=('Segoe UI', 9, 'bold'), relief='flat')
    style.map('Link.TButton', foreground=[('active', palette['accent'])])

    # Forms and data grids
    style.configure('TLabel', background=bg, foreground=ink, font=('Segoe UI', 10), anchor='e')
    style.configure(
        'TEntry', padding=(10, 7), fieldbackground=palette['field'], foreground=ink,
        borderwidth=2, relief='solid', bordercolor=palette['border'],
        lightcolor=palette['border'], darkcolor=palette['border'],
        insertwidth=2,
    )
    style.map('TEntry',
              fieldbackground=[('readonly', palette['surface_alt']), ('disabled', palette['surface_alt'])],
              bordercolor=[('focus', primary_color), ('!focus', palette['border'])],
              lightcolor=[('focus', primary_color), ('!focus', palette['border'])],
              darkcolor=[('focus', primary_color), ('!focus', palette['border'])])
    style.configure(
        'TCombobox', padding=(10, 7), fieldbackground=palette['field'], foreground=ink,
        arrowsize=16, borderwidth=2, relief='solid', bordercolor=palette['border'],
        lightcolor=palette['border'], darkcolor=palette['border'],
    )
    style.map('TCombobox',
              fieldbackground=[('readonly', palette['field'])],
              foreground=[('readonly', ink)],
              bordercolor=[('focus', primary_color), ('!focus', palette['border'])],
              lightcolor=[('focus', primary_color), ('!focus', palette['border'])],
              darkcolor=[('focus', primary_color), ('!focus', palette['border'])])
    style.configure('TCheckbutton', background=surface, foreground=ink, font=('Segoe UI', 9), padding=4)
    style.map('TCheckbutton', foreground=[('disabled', muted), ('active', primary_color)])
    style.configure('TRadiobutton', background=surface, foreground=ink, font=('Segoe UI', 9), padding=4)
    style.configure('TNotebook', background=bg, borderwidth=0, tabmargins=(3, 4, 3, 0))
    style.configure('TNotebook.Tab', padding=(17, 11), font=('Segoe UI', 9, 'bold'), background=palette['heading'])
    style.configure('TNotebook', tabmargins=(5, 5, 5, 0))
    style.configure('TNotebook.Tab', borderwidth=0, relief='flat')
    style.map('TNotebook.Tab', background=[('selected', surface), ('active', palette['selected'])],
              foreground=[('selected', primary_color), ('active', ink)])
    style.configure('Treeview', font=('Segoe UI', 10), rowheight=36, background=surface,
                    fieldbackground=surface, foreground=ink, borderwidth=0, relief='flat')
    style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), background=palette['heading'],
                    foreground=ink, padding=(10, 8), relief='flat', anchor='e')
    style.map('Treeview', background=[('selected', palette['selected'])],
              foreground=[('selected', ink)])
    style.configure('Horizontal.TProgressbar', troughcolor=palette['border'], background=palette['accent'])
    style.configure('Vertical.TScrollbar', background=palette['heading'], troughcolor=bg,
                    bordercolor=bg, arrowcolor=muted, width=10, relief='flat')
    style.map('Vertical.TScrollbar', background=[('active', palette['primary_soft'])])
    style.configure('Horizontal.TScrollbar', background=palette['heading'], troughcolor=bg,
                    bordercolor=bg, arrowcolor=muted, width=10, relief='flat')
    style.map('Horizontal.TScrollbar', background=[('active', palette['primary_soft'])])
    style.configure('Alert.TLabel', background=palette['alert_bg'], foreground=palette['alert_fg'],
                    padding=(13, 10), font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('Good.TLabel', background=palette['good_bg'], foreground=palette['good_fg'],
                    padding=(13, 10), font=('Segoe UI', 9, 'bold'), anchor='e')
    style.configure('Danger.TLabel', background=palette['danger_bg'], foreground=palette['danger_fg'],
                    padding=(13, 10), font=('Segoe UI', 9, 'bold'), anchor='e')

    # Apply a stronger, consistent type weight across the application while
    # preserving the larger title sizes already defined above.
    for style_name, font_spec in {
        'TLabel': (10, 'normal'), 'Hint.TLabel': (9, 'normal'), 'Subtitle.TLabel': (10, 'normal'),
        'PageSubtitle.TLabel': (10, 'normal'), 'LoginSubtitle.TLabel': (10, 'normal'),
        'SidebarSub.TLabel': (9, 'normal'), 'SidebarProfileRole.TLabel': (9, 'normal'),
        'HeroText.TLabel': (10, 'normal'), 'MetricHint.TLabel': (9, 'normal'),
        'CardTitle.TLabel': (9, 'normal'), 'DialogMessage.TLabel': (10, 'normal'),
        'ShortcutDesc.TLabel': (10, 'normal'), 'Status.TLabel': (9, 'normal'),
        'TEntry': (10, 'normal'), 'TCombobox': (10, 'normal'), 'Treeview': (10, 'normal'),
        'Treeview.Heading': (10, 'bold'), 'TCheckbutton': (9, 'normal'), 'TRadiobutton': (9, 'normal'),
    }.items():
        style.configure(style_name, font=('Segoe UI', font_spec[0], font_spec[1]))

    # The outer image elements give all entry boxes, combo boxes and main cards
    # genuinely rounded corners. Entry focus and disabled states remain distinct.
    _install_rounded_widget_surfaces(root, style, palette)


RTL_SHAPING_ENABLED = True

def rtl_display(value) -> str:
    """Return a Tk-ready Arabic visual string exactly once; preserve logical data elsewhere."""
    text = str(value if value is not None else '')
    if not text or not RTL_SHAPING_ENABLED:
        return text
    # python-bidi/arabic-reshaper output presentation-form code points. Detect them
    # to prevent a second shaping/reordering pass when a widget is reconfigured.
    if any(0xFB50 <= ord(ch) <= 0xFDFF or 0xFE70 <= ord(ch) <= 0xFEFF for ch in text):
        return text
    has_arabic = any(0x0600 <= ord(ch) <= 0x06FF or 0x0750 <= ord(ch) <= 0x077F or
                     0x08A0 <= ord(ch) <= 0x08FF for ch in text)
    if not has_arabic:
        return text
    try:
        return "\n".join(core._arabic_visual(line) if line.strip() else line for line in text.split("\n"))
    except (ImportError, RuntimeError, ValueError):
        return text

def _install_rtl_window_titles():
    """Shape Arabic window titles once, just like labels and table cells."""
    for window_class in (Tk, Toplevel):
        if getattr(window_class, '_schoolpass_rtl_title_installed', False):
            continue
        original_title = getattr(window_class, 'title', None)
        if not callable(original_title):
            continue
        def title_with_rtl(self, string=None, _original=original_title):
            if string is None:
                return _original(self)
            return _original(self, rtl_display(string))
        window_class.title = title_with_rtl
        window_class._schoolpass_rtl_title_installed = True


_install_rtl_window_titles()


def _custom_message_dialog(kind: str, title=None, message=None, **options):
    """Render native-looking, fully themed modal dialogs with Arabic RTL content.

    Windows messagebox dialogs use a separate native rendering path and were
    still displaying some Arabic text in the wrong visual order. These dialogs
    use the same Tk/ttk rendering path as the main application and preserve the
    standard messagebox return contracts.
    """
    parent = options.pop('parent', None)
    title_text = str(title or 'SchoolPass')
    message_text = str(message or '')
    choices = {
        'showinfo': [('حسنًا', 'ok')],
        'showwarning': [('حسنًا', 'ok')],
        'showerror': [('حسنًا', 'ok')],
        'askyesno': [('نعم', True), ('لا', False)],
        'askokcancel': [('متابعة', True), ('إلغاء', False)],
        'askretrycancel': [('إعادة المحاولة', True), ('إلغاء', False)],
        'askyesnocancel': [('نعم', True), ('لا', False), ('إلغاء', None)],
        'askquestion': [('نعم', 'yes'), ('لا', 'no')],
    }
    buttons = choices.get(kind, choices['showinfo'])
    default_value = False if kind in ('askyesno', 'askokcancel', 'askretrycancel') else (None if kind == 'askyesnocancel' else ('no' if kind == 'askquestion' else 'ok'))
    result = {'value': default_value}
    owner = parent if parent is not None else getattr(tk, '_default_root', None)
    temporary_root = None
    try:
        if owner is None:
            try:
                temporary_root = Tk()
                temporary_root.withdraw()
                owner = temporary_root
            except Exception:
                owner = None
        dialog = Toplevel(owner) if owner is not None else Toplevel()
        dialog.title(rtl_display(title_text))
        dialog.resizable(False, False)
        try:
            dialog.configure(background=_window_background())
            apply_application_icon(dialog)
        except Exception:
            pass
        if owner is not None:
            try:
                dialog.transient(owner)
            except Exception:
                pass

        outer = ttk.Frame(dialog, style='Dialog.TFrame', padding=(17, 15))
        outer.pack(fill=BOTH, expand=True)
        heading = ttk.Label(outer, text=title_text, style='DialogTitle.TLabel', anchor='e', justify='right')
        heading.pack(fill=X, pady=(0, 8))

        body = ttk.Frame(outer, style='Dialog.TFrame')
        body.pack(fill=BOTH, expand=True)
        symbol = {'showinfo': 'i', 'showwarning': '!', 'showerror': '×',
                  'askyesno': '?', 'askokcancel': '?', 'askretrycancel': '?',
                  'askyesnocancel': '?', 'askquestion': '?'}[kind]
        icon = ttk.Label(body, text=symbol, style='DialogIcon.TLabel', anchor='center')
        icon.pack(side=LEFT, padx=(0, 14), anchor='n')
        text_label = ttk.Label(body, text=message_text, style='DialogMessage.TLabel',
                               justify='right', anchor='e', wraplength=520)
        text_label.pack(side=RIGHT, fill=BOTH, expand=True, anchor='e')

        bar = ttk.Frame(outer, style='Dialog.TFrame')
        bar.pack(fill=X, pady=(18, 0))

        def finish(value):
            result['value'] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        for index, (label, value) in enumerate(buttons):
            style_name = 'Accent.TButton' if index == 0 else 'TButton'
            ttk.Button(bar, text=label, command=lambda v=value: finish(v), style=style_name).pack(
                side=RIGHT, padx=(5, 0), ipadx=8, ipady=2)

        dialog.bind('<Escape>', lambda _event: finish(default_value))
        dialog.protocol('WM_DELETE_WINDOW', lambda: finish(default_value))
        dialog.update_idletasks()
        try:
            width = max(420, min(640, dialog.winfo_reqwidth() + 10))
            height = max(210, min(420, dialog.winfo_reqheight() + 10))
            if owner is not None:
                owner.update_idletasks()
                px = owner.winfo_rootx() + max(0, (owner.winfo_width() - width) // 2)
                py = owner.winfo_rooty() + max(0, (owner.winfo_height() - height) // 2)
            else:
                sw = int(dialog.winfo_screenwidth() or 1366)
                sh = int(dialog.winfo_screenheight() or 768)
                px, py = max(0, (sw-width)//2), max(0, (sh-height)//2)
            dialog.geometry(f'{width}x{height}+{px}+{py}')
        except Exception:
            dialog.geometry('520x250')
        try:
            dialog.grab_set()
            dialog.focus_force()
        except Exception:
            pass
        dialog.wait_window()
    except Exception:
        # Last-resort native fallback if a widget cannot be constructed.
        # The caller still receives the expected return type.
        try:
            fallback = getattr(messagebox, f'_original_{kind}', None)
            if fallback:
                return fallback(title_text, message_text, parent=parent)
        except Exception:
            pass
    finally:
        if temporary_root is not None:
            try:
                temporary_root.destroy()
            except Exception:
                pass
    return result['value']


def _wrap_messageboxes_once():
    """Route every standard messagebox function through the SchoolPass dialog renderer."""
    if getattr(messagebox, '_schoolpass_rtl_wrapped', False):
        return
    for _name in ('showinfo', 'showwarning', 'showerror', 'askyesno', 'askokcancel',
                  'askretrycancel', 'askyesnocancel', 'askquestion'):
        if hasattr(messagebox, _name):
            try:
                setattr(messagebox, f'_original_{_name}', getattr(messagebox, _name))
            except Exception:
                pass
        setattr(messagebox, _name, (lambda name: lambda title=None, message=None, **kwargs:
            _custom_message_dialog(name, title, message, **kwargs))(_name))
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
            def __init__(self, master=None, _supports_alignment=(class_name == 'Label'), **kwargs):
                # Only ttk.Label supports both justify and anchor as widget
                # options. ttk.Button/Checkbutton/Radiobutton reject -anchor
                # (and may reject -justify), which previously crashed startup.
                # Capture the class-specific flag in a default argument because
                # this adapter class is defined repeatedly inside a loop.
                if _supports_alignment:
                    kwargs.setdefault('justify', 'right')
                    kwargs.setdefault('anchor', 'e')
                if isinstance(kwargs.get('text'), str):
                    kwargs['text'] = rtl_display(kwargs['text'])
                self._schoolpass_text_source = None
                self._schoolpass_text_display = None
                source_var = kwargs.get('textvariable')
                if source_var is not None and hasattr(source_var, 'get') and hasattr(source_var, 'trace_add'):
                    try:
                        self._schoolpass_text_source = source_var
                        self._schoolpass_text_display = StringVar(master=master, value=rtl_display(source_var.get()))
                        source_var.trace_add('write', self._schoolpass_refresh_textvariable)
                        kwargs['textvariable'] = self._schoolpass_text_display
                    except Exception:
                        self._schoolpass_text_source = None
                        self._schoolpass_text_display = None
                super().__init__(master, **kwargs)

            def _schoolpass_refresh_textvariable(self, *_args):
                if self._schoolpass_text_source is None or self._schoolpass_text_display is None:
                    return
                try:
                    self._schoolpass_text_display.set(rtl_display(self._schoolpass_text_source.get()))
                except Exception:
                    pass

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

    original_entry = ttk.Entry

    class ArabicAwareEntry(original_entry):
        def __init__(self, master=None, *args, **kwargs):
            kwargs.setdefault('justify', 'right')
            # ttk.Combobox inherits from the original ttk.Entry, but its
            # __init__ resolves ttk.Entry through the module at runtime.
            # Since ttk.Entry is replaced below, calling super() here would
            # reject a Combobox instance (it is not an ArabicAwareEntry).
            # Invoke the original Entry initializer directly instead.
            original_entry.__init__(self, master, *args, **kwargs)

    ttk.Entry = ArabicAwareEntry

    original_combo = ttk.Combobox

    class ArabicAwareCombobox(original_combo):
        """RTL-aware combobox with a logical-value contract for application code.

        Values may be replaced after construction (for example, when the allowed
        absence-proof choices depend on the absence duration). Keep that update in
        the same display/logical mapping as initial construction; otherwise Tk
        would show unshaped Arabic in the newly supplied dropdown items.
        """
        def _schoolpass_map_values(self, values):
            logical_values = tuple(values or ())
            self._schoolpass_original_values = logical_values
            self._schoolpass_display_to_logical = {}
            self._schoolpass_logical_to_display = {}
            display_values = []
            for value in logical_values:
                logical = str(value)
                display = rtl_display(logical)
                self._schoolpass_display_to_logical[display] = logical
                self._schoolpass_logical_to_display[logical] = display
                display_values.append(display)
            return tuple(display_values)

        def __init__(self, master=None, *args, **kwargs):
            kwargs.setdefault('justify', 'right')
            initial_values = kwargs.get('values', ())
            kwargs['values'] = self._schoolpass_map_values(initial_values)
            super().__init__(master, *args, **kwargs)

        def configure(self, cnf=None, **kwargs):
            if isinstance(cnf, dict):
                cnf = dict(cnf)
                if 'values' in cnf:
                    cnf['values'] = self._schoolpass_map_values(cnf['values'])
            if 'values' in kwargs:
                kwargs['values'] = self._schoolpass_map_values(kwargs['values'])
            return super().configure(cnf, **kwargs)

        config = configure

        def get(self):
            value = super().get()
            return self._schoolpass_display_to_logical.get(value, value)

        def set(self, value):
            super().set(self._schoolpass_logical_to_display.get(str(value), rtl_display(value)))

    ttk.Combobox = ArabicAwareCombobox

    original_tree = ttk.Treeview
    _TREE_VALUE_UNSET = object()

    class ArabicAwareTreeview(original_tree):
        """Render shaped cells while returning original logical values to sorting."""
        def __init__(self, master=None, *args, **kwargs):
            self._schoolpass_tree_columns = tuple(kwargs.get('columns', ()))
            self._schoolpass_logical_rows = {}
            super().__init__(master, *args, **kwargs)
            # Treeview columns default to centered/left-biased platform settings;
            # set every data column to RTL unless a screen later overrides it.
            for column_id in self._schoolpass_tree_columns:
                try:
                    self.column(column_id, anchor='e')
                except Exception:
                    pass

        def heading(self, column, option=None, **kwargs):
            if kwargs:
                kwargs.setdefault('anchor', 'e')
            if isinstance(kwargs.get('text'), str):
                kwargs['text'] = rtl_display(kwargs['text'])
            base_method = getattr(super(), 'heading', None)
            return base_method(column, option, **kwargs) if base_method else None

        def column(self, column, option=None, **kwargs):
            if kwargs:
                kwargs.setdefault('anchor', 'e')
            base_method = getattr(super(), 'column', None)
            return base_method(column, option, **kwargs) if base_method else None

        def _schoolpass_save_logical_values(self, iid, values):
            if iid is None:
                return
            try:
                self._schoolpass_logical_rows[str(iid)] = {
                    str(column): value for column, value in zip(self._schoolpass_tree_columns, tuple(values))
                }
            except (TypeError, ValueError):
                pass

        def insert(self, parent, index, iid=None, **kwargs):
            logical_values = kwargs.get('values', ())
            if 'values' in kwargs:
                kwargs['values'] = tuple(rtl_display(v) if isinstance(v, str) else v for v in logical_values)
            created_iid = super().insert(parent, index, iid=iid, **kwargs)
            self._schoolpass_save_logical_values(iid or created_iid, logical_values)
            return created_iid

        def item(self, item, option=None, **kwargs):
            if 'values' in kwargs:
                logical_values = kwargs['values']
                self._schoolpass_save_logical_values(item, logical_values)
                kwargs['values'] = tuple(rtl_display(v) if isinstance(v, str) else v for v in logical_values)
            return super().item(item, option, **kwargs)

        def set(self, item, column=None, value=_TREE_VALUE_UNSET):
            key = str(item)
            if value is _TREE_VALUE_UNSET:
                if column is not None:
                    row = self._schoolpass_logical_rows.get(key, {})
                    if str(column) in row:
                        return row[str(column)]
                return super().set(item, column) if column is not None else super().set(item)
            row = self._schoolpass_logical_rows.setdefault(key, {})
            row[str(column)] = value
            display_value = rtl_display(value) if isinstance(value, str) else value
            return super().set(item, column, display_value)

        def delete(self, *items):
            for item in items:
                self._schoolpass_logical_rows.pop(str(item), None)
            return super().delete(*items)

    ttk.Treeview = ArabicAwareTreeview

    # Tk Text is used for the statistics overview. Shape all inserted content at
    # the widget boundary so future screens cannot accidentally bypass RTL rules.
    original_text_insert = getattr(tk.Text, 'insert', None)
    if callable(original_text_insert) and not getattr(tk.Text, '_schoolpass_rtl_insert', False):
        def rtl_text_insert(self, index, chars, *args):
            if not isinstance(chars, str):
                return original_text_insert(self, index, chars, *args)
            has_arabic = any(0x0600 <= ord(ch) <= 0x06FF or 0x0750 <= ord(ch) <= 0x077F or
                             0x08A0 <= ord(ch) <= 0x08FF or 0xFB50 <= ord(ch) <= 0xFDFF or
                             0xFE70 <= ord(ch) <= 0xFEFF for ch in chars)
            try:
                start = self.index(index) if has_arabic else None
            except Exception:
                start = None
            shaped = rtl_display(chars)
            result = original_text_insert(self, index, shaped, *args)
            if has_arabic:
                try:
                    self.tag_configure('_schoolpass_rtl', justify='right')
                    if start is not None:
                        end = self.index(f'{start}+{len(shaped)}c')
                        self.tag_add('_schoolpass_rtl', start, end)
                except Exception:
                    # Older/custom Text implementations still receive shaped text;
                    # lack of tag support must not break reports.
                    pass
            return result
        tk.Text.insert = rtl_text_insert
        tk.Text._schoolpass_rtl_insert = True

    # Canvas text includes Arabic chart legends and annotations. Keep this
    # centralized just like ttk labels; presentation-form text is idempotent.
    original_canvas_create_text = getattr(Canvas, 'create_text', None)
    if callable(original_canvas_create_text) and not getattr(Canvas, '_schoolpass_rtl_text', False):
        def rtl_canvas_create_text(self, *args, **kwargs):
            if isinstance(kwargs.get('text'), str):
                kwargs['text'] = rtl_display(kwargs['text'])
            return original_canvas_create_text(self, *args, **kwargs)
        Canvas.create_text = rtl_canvas_create_text

        original_canvas_itemconfigure = getattr(Canvas, 'itemconfigure', None)
        if callable(original_canvas_itemconfigure):
            def rtl_canvas_itemconfigure(self, tag_or_id, cnf=None, **kwargs):
                if isinstance(cnf, dict):
                    cnf = dict(cnf)
                    if isinstance(cnf.get('text'), str):
                        cnf['text'] = rtl_display(cnf['text'])
                if isinstance(kwargs.get('text'), str):
                    kwargs['text'] = rtl_display(kwargs['text'])
                return original_canvas_itemconfigure(self, tag_or_id, cnf, **kwargs)
            Canvas.itemconfigure = rtl_canvas_itemconfigure
            if hasattr(Canvas, 'itemconfig'):
                Canvas.itemconfig = rtl_canvas_itemconfigure
        Canvas._schoolpass_rtl_text = True

    ttk._schoolpass_rtl_installed = True


_install_rtl_widget_adapters()


class ScrollableFrame(ttk.Frame):
    """A width-following vertical content area with reliable Windows mouse-wheel scrolling."""
    def __init__(self, master, *, inner_style='TFrame', canvas_background=None, width=None, **kwargs):
        super().__init__(master, width=width, **kwargs)
        self.canvas = Canvas(self, highlightthickness=0, bd=0, yscrollincrement=26,
                             background=canvas_background or _window_background())
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=RIGHT, fill=Y)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.inner = ttk.Frame(self.canvas, style=inner_style)
        self._window_id = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', self._update_scroll_region)
        self.canvas.bind('<Configure>', self._fit_inner_width)
        self._wheel_bindings = []
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            binding_id = self.bind_all(sequence, self._on_mousewheel, add='+')
            self._wheel_bindings.append((sequence, binding_id))
        self.bind('<Destroy>', self._cleanup_mousewheel_bindings, add='+')

    def _cleanup_mousewheel_bindings(self, event=None):
        if event is not None and getattr(event, 'widget', self) is not self:
            return
        for sequence, binding_id in getattr(self, '_wheel_bindings', []):
            if binding_id:
                try:
                    self._unbind(('bind', 'all', sequence), binding_id)
                except Exception:
                    pass
        self._wheel_bindings = []

    def _update_scroll_region(self, _event=None):
        try:
            box = self.canvas.bbox(self._window_id)
            content_width = max(int(self.canvas.winfo_width()), int(self.inner.winfo_reqwidth()), int(box[2]) if box else 1)
            content_height = max(int(self.canvas.winfo_height()), int(self.inner.winfo_reqheight()), int(box[3]) if box else 1)
            self.canvas.configure(scrollregion=(0, 0, content_width, content_height))
            if content_height <= int(self.canvas.winfo_height() or 1):
                self.canvas.yview_moveto(0)
        except Exception:
            pass

    def _fit_inner_width(self, event=None):
        try:
            width = int(getattr(event, 'width', 0) or self.canvas.winfo_width())
            self.canvas.itemconfigure(self._window_id, width=max(1, width))
        except Exception:
            pass

    def set_canvas_background(self, color):
        try:
            self.canvas.configure(background=color)
        except Exception:
            pass

    def _on_mousewheel(self, event):
        # Only scroll the container under the pointer; leave tables, text boxes,
        # combo boxes and entries to their own native scrolling/selection behavior.
        node = getattr(event, 'widget', None)
        inside = False
        while node is not None:
            if node is self.inner or node is self.canvas:
                inside = True
                break
            try:
                parent = node.master
            except Exception:
                parent = None
            if callable(parent):
                parent = None
            node = parent
        if not inside:
            return None
        try:
            widget_class = str(event.widget.winfo_class()).lower()
        except Exception:
            widget_class = type(getattr(event, 'widget', None)).__name__.lower()
        if any(name in widget_class for name in ('treeview', 'entry', 'combobox', 'text', 'listbox', 'scrollbar')):
            return None
        number = getattr(event, 'num', None)
        if number == 4:
            units = -3
        elif number == 5:
            units = 3
        else:
            delta = int(getattr(event, 'delta', 0) or 0)
            if not delta:
                return None
            units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        try:
            first, last = self.canvas.yview()
            if (units < 0 and first <= 0.0001) or (units > 0 and last >= 0.9999):
                return 'break'
            self.canvas.yview_scroll(units, 'units')
            return 'break'
        except Exception:
            return None


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
    """First-run administrator setup in the unified SchoolPass 4.0 dialog design."""
    def __init__(self, root: Tk):
        self.result = False
        win = Toplevel(root)
        configure_window(win, 'الإعداد الأول — إنشاء حساب المدير', '620x590')
        win.minsize(540, 520)
        win.transient(root)
        win.grab_set()
        outer = ttk.Frame(win, style='DialogCard.TFrame', padding=(26, 18))
        outer.pack(fill=BOTH, expand=True, padx=18, pady=18)
        ttk.Label(outer, text='تهيئة النظام', style='Eyebrow.TLabel').pack(anchor=E, pady=(0, 5))
        ttk.Label(outer, text='مرحبًا بك في SchoolPass', style='DialogTitle.TLabel').pack(anchor=E, pady=(0, 7))
        ttk.Label(outer, text='أنشئ حساب المدير الأول لإدارة المستخدمين والإعدادات. لا توجد كلمة مرور افتراضية، ولا تُحفظ كلمة المرور كنص واضح.',
                  style='DialogMessage.TLabel', wraplength=500, justify='right').pack(fill=X, pady=(0, 10))
        form = ttk.Frame(outer, style='Dialog.TFrame')
        form.pack(fill=X)
        self.user = StringVar(value='')
        self.name = StringVar(value='')
        self.password = StringVar()
        self.confirm = StringVar()
        rows = [('اسم الدخول', self.user, '', 'مثال: director'),
                ('الاسم الظاهر للمدير', self.name, '', 'الاسم الذي سيظهر داخل البرنامج'),
                ('كلمة المرور', self.password, '●', 'ثمانية أحرف على الأقل'),
                ('تأكيد كلمة المرور', self.confirm, '●', 'أعد كتابة كلمة المرور')]
        entries = []
        for label, var, show, hint in rows:
            ttk.Label(form, text=label, style='FieldLabel.TLabel').pack(anchor=E, pady=(6, 2))
            ent = ttk.Entry(form, textvariable=var, show=show, justify='right')
            ent.pack(fill=X, ipady=2)
            entries.append(ent)
            ttk.Label(form, text=hint, style='Hint.TLabel').pack(anchor=E, pady=(2, 1))

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
        actions = ttk.Frame(outer, style='Dialog.TFrame')
        actions.pack(fill=X, pady=(12, 0))
        ttk.Button(actions, text='إنشاء حساب المدير', command=create, style='Accent.TButton').pack(side=RIGHT)
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        entries[0].focus_set()
        root.wait_window(win)


class RecoveryDialog:
    """Reset an admin password with the recovery code written down at first setup."""
    def __init__(self, parent):
        win = Toplevel(parent)
        configure_window(win, 'استرداد حساب المدير', '590x560')
        win.minsize(520, 500)
        win.transient(parent)
        win.grab_set()
        outer = ttk.Frame(win, style='DialogCard.TFrame', padding=(32, 26))
        outer.pack(fill=BOTH, expand=True, padx=18, pady=18)
        ttk.Label(outer, text='استعادة الوصول الآمن', style='Eyebrow.TLabel').pack(anchor=E, pady=(0, 5))
        ttk.Label(outer, text='استرداد كلمة مرور المدير', style='DialogTitle.TLabel').pack(anchor=E, pady=(0, 8))
        ttk.Label(outer, text='أدخل بيانات المدير ورمز الاسترداد الذي تم حفظه عند إنشاء الحساب.',
                  style='DialogMessage.TLabel', wraplength=480, justify='right').pack(fill=X, pady=(0, 12))
        form = ttk.Frame(outer, style='Dialog.TFrame')
        form.pack(fill=X)
        vars_ = {k: StringVar() for k in ('user', 'code', 'pw', 'pw2')}
        rows = [('user', 'اسم دخول المدير', ''), ('code', 'رمز الاسترداد', ''),
                ('pw', 'كلمة المرور الجديدة', '●'), ('pw2', 'تأكيد كلمة المرور', '●')]
        entries = []
        for key, label, show in rows:
            ttk.Label(form, text=label, style='FieldLabel.TLabel').pack(anchor=E, pady=(9, 3))
            ent = ttk.Entry(form, textvariable=vars_[key], show=show, justify='right')
            ent.pack(fill=X, ipady=1)
            entries.append(ent)

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
        ttk.Button(outer, text='تغيير كلمة المرور', command=go, style='Accent.TButton').pack(anchor=E, pady=(22, 0))
        entries[0].focus_set()
        parent.wait_window(win)


class LoginDialog:
    """Accessible login with an account selector and optional password visibility."""
    def __init__(self, root: Tk, lock_mode: bool = False, prefill: str = ''):
        self.user = None
        win = Toplevel(root)
        title = 'فتح الجلسة — SchoolPass' if lock_mode else 'تسجيل الدخول — SchoolPass'
        configure_window(win, title, '740x480')
        win.minsize(620, 410)
        win.transient(root)
        win.grab_set()
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        win.configure(background=UI_PALETTE.get('bg', '#F3F6FB'))

        body = ttk.Frame(win, style='LoginSurface.TFrame')
        body.pack(fill=BOTH, expand=True, padx=14, pady=14)
        form_panel = ttk.Frame(body, style='LoginSurface.TFrame', padding=(22, 18))
        form_panel.pack(side=LEFT, fill=BOTH, expand=True)
        brand_panel = ttk.Frame(body, style='LoginBrand.TFrame', padding=(18, 18), width=235)
        brand_panel.pack(side=RIGHT, fill=Y)
        brand_panel.pack_propagate(False)

        ttk.Label(brand_panel, text='SP', style='LoginMark.TLabel').pack(anchor=E, pady=(8, 28))
        ttk.Label(brand_panel, text='SchoolPass', style='LoginBrandTitle.TLabel').pack(anchor=E, pady=(0, 8))
        ttk.Label(brand_panel, text='منصة المتابعة الإدارية\nللحياة المدرسية', style='LoginBrandSub.TLabel',
                  justify='right').pack(anchor=E, fill=X, pady=(0, 22))
        ttk.Frame(brand_panel, height=1, style='SidebarSeparator.TFrame').pack(fill=X, pady=(0, 18))
        ttk.Label(brand_panel, text='إدارة واضحة. متابعة موثوقة.', style='LoginBrandKicker.TLabel').pack(anchor=E, pady=(0, 12))
        for line in ('رخص الدخول والخروج', 'سجل العمليات والمتابعة', 'إحصائيات وتقارير إدارية'):
            ttk.Label(brand_panel, text='•  ' + line, style='LoginBrandSub.TLabel', justify='right').pack(anchor=E, fill=X, pady=5)
        ttk.Frame(brand_panel, style='LoginBrand.TFrame').pack(fill=BOTH, expand=True)
        ttk.Label(brand_panel, text=f'الإصدار {APP_VERSION}\n{setting("school_name", SCHOOL_NAME)}',
                  style='LoginBrandFoot.TLabel', justify='right', wraplength=220).pack(anchor=E, pady=(14, 0))

        ttk.Label(form_panel, text='بوابة الدخول الآمن', style='Eyebrow.TLabel').pack(anchor=E, pady=(12, 7))
        ttk.Label(form_panel, text='مرحبًا بعودتك', style='LoginTitle.TLabel').pack(anchor=E, pady=(0, 6))
        school_name = setting('school_name', SCHOOL_NAME)
        ttk.Label(form_panel, text=school_name, style='LoginSubtitle.TLabel', wraplength=420,
                  justify='right').pack(anchor=E, fill=X, pady=(0, 22))

        accounts = core.list_active_accounts()
        self.account_map = {}
        account_labels = []
        for account in accounts:
            role = 'مدير النظام' if account['role'] == 'admin' else 'مشرف تربوي'
            label = f"{account['display_name']}  —  {role}  ({account['username']})"
            account_labels.append(label)
            self.account_map[label] = account['username']
        self.username = StringVar(value=prefill or setting('last_username', ''))
        account_var = StringVar(value='')
        selected = next((label for label, username in self.account_map.items()
                         if username.casefold() == self.username.get().casefold()), '')
        if not selected and account_labels:
            selected = account_labels[0]
            self.username.set(self.account_map[selected])
        account_var.set(selected)
        ttk.Label(form_panel, text='اختيار الحساب', style='FieldLabel.TLabel').pack(anchor=E, pady=(0, 5))
        account_combo = ttk.Combobox(form_panel, textvariable=account_var, state='readonly',
                                     values=account_labels, justify='right')
        account_combo.pack(fill=X, ipady=3, pady=(0, 16))
        def account_selected(_event=None):
            username = self.account_map.get(account_var.get())
            if username:
                self.username.set(username)
        account_combo.bind('<<ComboboxSelected>>', account_selected)

        self.password = StringVar()
        ttk.Label(form_panel, text='كلمة المرور', style='FieldLabel.TLabel').pack(anchor=E, pady=(0, 5))
        pass_entry = ttk.Entry(form_panel, textvariable=self.password, show='●', justify='right')
        pass_entry.pack(fill=X, ipady=3, pady=(0, 4))
        show_password = BooleanVar(value=False)
        ttk.Checkbutton(form_panel, text='إظهار كلمة المرور', variable=show_password,
                        command=lambda: pass_entry.configure(show='' if show_password.get() else '●')).pack(anchor=E, pady=(0, 5))
        err = DisplayStringVar()
        ttk.Label(form_panel, textvariable=err, style='Error.TLabel', wraplength=430,
                  justify='right').pack(fill=X, pady=(2, 5))

        def login():
            account_selected()
            user, msg = core.authenticate(self.username.get(), self.password.get())
            if not user:
                err.set(msg)
                self.password.set('')
                pass_entry.focus_set()
                return
            self.user = user
            set_setting('last_username', user['username'])
            audit(user['id'], 'فتح الجلسة بعد القفل' if lock_mode else 'تسجيل الدخول',
                  'user', user['id'], user['username'])
            win.destroy()

        ttk.Button(form_panel, text='دخول إلى النظام', command=login, style='Accent.TButton').pack(fill=X, pady=(12, 8), ipady=2)
        ttk.Button(form_panel, text='نسيت كلمة مرور المدير؟', command=lambda: RecoveryDialog(win),
                   style='Link.TButton').pack(anchor=E, pady=(1, 0))
        ttk.Label(form_panel, text='استخدم بيانات حسابك المصرّح به فقط.', style='Hint.TLabel').pack(anchor=E, pady=(18, 0))
        pass_entry.bind('<Return>', lambda _e: login())
        account_combo.bind('<Return>', lambda _e: pass_entry.focus_set())
        pass_entry.focus_set()
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
        ttk.Label(top, text='ابحث بالاسم أو اللقب أو القسم:').pack(side=RIGHT, padx=5)
        self.query = StringVar()
        ent = ttk.Entry(top, textvariable=self.query, justify='right')
        ent.pack(side=RIGHT, fill=X, expand=True)
        cols = ('name', 'class_name')
        self.tree = ttk.Treeview(self.win, columns=cols, show='headings')
        for col, title, width in [('name', 'اللقب والاسم', 360), ('class_name', 'القسم', 250)]:
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
        self.win.after(100, ent.focus_set)

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
            self.tree.insert('', END, iid=str(s['id']), values=(student_name(s), s['class_name']))

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


class DatePicker:
    """Mouse-only date selector. Future days are disabled; no free-typing is allowed."""
    def __init__(self, parent, variable: StringVar, on_change=None):
        self.parent = parent
        self.variable = variable
        self.on_change = on_change
        self.button = ttk.Button(parent, text='📅', width=4, command=self.open)

    def pack(self, **kwargs):
        self.button.pack(**kwargs)

    def open(self):
        try:
            selected = date.fromisoformat(core.normalize_digits(self.variable.get()).strip())
        except ValueError:
            selected = date.fromisoformat(date_today())
        today = date.fromisoformat(date_today())
        shown = {'year': selected.year, 'month': selected.month}
        win = Toplevel(self.parent.winfo_toplevel())
        win.title('اختيار التاريخ')
        win.geometry('310x300')
        win.resizable(False, False)
        win.transient(self.parent.winfo_toplevel())
        win.grab_set()
        outer = ttk.Frame(win, padding=9)
        outer.pack(fill=BOTH, expand=True)
        head = ttk.Frame(outer)
        head.pack(fill=X, pady=(0, 8))
        grid_frame = ttk.Frame(outer)
        grid_frame.pack(fill=BOTH, expand=True)

        def render():
            for child in (head.winfo_children() or []):
                child.destroy()
            for child in (grid_frame.winfo_children() or []):
                child.destroy()
            previous = ttk.Button(head, text='‹', width=4, command=lambda: move(-1))
            previous.pack(side=LEFT)
            month_names = ('', 'جانفي', 'فيفري', 'مارس', 'أفريل', 'ماي', 'جوان',
                           'جويلية', 'أوت', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر')
            title = f"{month_names[shown['month']]} {shown['year']}"
            ttk.Label(head, text=title, anchor='center').pack(side=LEFT, fill=X, expand=True)
            next_btn = ttk.Button(head, text='›', width=4, command=lambda: move(1))
            next_btn.pack(side=RIGHT)
            if (shown['year'], shown['month']) >= (today.year, today.month):
                next_btn.configure(state='disabled')
            weekdays = ('أحد', 'إثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت')
            for col, label in enumerate(weekdays):
                ttk.Label(grid_frame, text=label, anchor='center').grid(row=0, column=col, sticky='nsew', padx=1, pady=2)
                grid_frame.grid_columnconfigure(col, weight=1)
            cal = calendar_module.Calendar(firstweekday=6)
            weeks = cal.monthdayscalendar(shown['year'], shown['month'])
            for r, week in enumerate(weeks, start=1):
                for col, day_num in enumerate(week):
                    if not day_num:
                        ttk.Label(grid_frame, text='').grid(row=r, column=col, sticky='nsew', padx=1, pady=1)
                        continue
                    value = date(shown['year'], shown['month'], day_num)
                    state = 'disabled' if value > today else 'normal'
                    btn = ttk.Button(grid_frame, text=str(day_num), width=4, state=state,
                                     command=lambda value=value: choose(value))
                    if value == selected:
                        btn.configure(style='Accent.TButton')
                    btn.grid(row=r, column=col, sticky='nsew', padx=1, pady=1)

        def move(delta):
            m = shown['month'] + delta
            y = shown['year']
            if m < 1:
                m, y = 12, y - 1
            elif m > 12:
                m, y = 1, y + 1
            if (y, m) <= (today.year, today.month):
                shown.update(year=y, month=m)
                render()

        def choose(value):
            self.variable.set(value.isoformat())
            if callable(self.on_change):
                self.on_change()
            win.destroy()

        render()


class SuggestionPopup:
    """Non-modal suggestion list which leaves keyboard focus in the active search entry."""
    def __init__(self, anchor, on_select):
        self.anchor = anchor
        self.on_select = on_select
        self.items = []
        self.popup = Toplevel(anchor.winfo_toplevel())
        try:
            self.popup.overrideredirect(True)
            self.popup.attributes('-topmost', True)
        except Exception:
            pass
        self.listbox = tk.Listbox(
            self.popup, height=7, exportselection=False, activestyle='none',
            font=('Segoe UI', 10),
            background=UI_PALETTE.get('field', '#EAF1FA'),
            foreground=UI_PALETTE.get('ink', '#17263C'),
            selectbackground=UI_PALETTE.get('primary', '#185A8D'),
            selectforeground=UI_PALETTE.get('primary_text', '#FFFFFF'),
            highlightthickness=1, highlightbackground=UI_PALETTE.get('border', '#C5D2E2'),
            highlightcolor=UI_PALETTE.get('primary', '#185A8D'), relief='flat',
            borderwidth=1)
        self.listbox.pack(fill=BOTH, expand=True)
        self.listbox.bind('<ButtonRelease-1>', lambda _event: self.select_first())
        self.listbox.bind('<Return>', lambda _event: self.select_first())
        self.listbox.bind('<Escape>', lambda _event: self.hide())
        self.hide()

    def show(self, items):
        self.items = list(items or [])
        self.listbox.delete(0, END)
        for label, _row in self.items:
            self.listbox.insert(END, rtl_display(label))
        if not self.items:
            self.hide()
            return
        try:
            self.anchor.update_idletasks()
            x = int(self.anchor.winfo_rootx())
            y = int(self.anchor.winfo_rooty() + self.anchor.winfo_height())
            screen_w = int(self.anchor.winfo_screenwidth())
            screen_h = int(self.anchor.winfo_screenheight())
            width = min(max(300, int(self.anchor.winfo_width())), max(300, screen_w - 12))
            height = min(7, len(self.items)) * 28 + 8
            height = min(height, max(72, screen_h - 16))
            if y + height > screen_h - 8:
                y = max(0, int(self.anchor.winfo_rooty()) - height)
            x = max(0, min(x, screen_w - width - 6))
            self.popup.geometry(f'{width}x{height}+{x}+{max(0, y)}')
            self.popup.deiconify()
            self.popup.lift()
        except Exception:
            try:
                self.popup.deiconify()
            except Exception:
                pass

    def move_selection(self):
        if not self.items:
            return 'break'
        try:
            current = self.listbox.curselection()
            index = (int(current[0]) + 1) % len(self.items) if current else 0
            self.listbox.selection_clear(0, END)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
            self.listbox.see(index)
        except Exception:
            pass
        return 'break'

    def select_first(self):
        if not self.items:
            return 'break'
        try:
            selected = self.listbox.curselection()
            index = int(selected[0]) if selected else 0
            label, row = self.items[index]
            self.hide()
            self.on_select(row, label)
            self.anchor.focus_set()
        except Exception:
            pass
        return 'break'

    def hide(self):
        try:
            self.popup.withdraw()
        except Exception:
            pass


class RecordForm:
    def __init__(self, app: 'SchoolPassApp', kind: str):
        self.app = app
        self.kind = kind
        self.student = None
        self._saving = False
        titles = {
            'late': 'إصدار رخصة الدخول — تأخر',
            'absence_return': 'إصدار رخصة الدخول — عودة بعد الغياب',
            'special_entry': 'إصدار رخصة الدخول — حالة خاصة',
            'exit_permit': 'إصدار رخصة خروج استثنائي',
        }
        self.title = titles[kind]
        self.win = Toplevel(app.root)
        configure_window(
            self.win, self.title,
            '660x620' if kind == 'absence_return' else '620x520',
        )
        self.win.transient(app.root)
        self.win.grab_set()
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        form_header = ttk.Frame(self.win, style='DialogHeader.TFrame', padding=(18, 12))
        form_header.pack(fill=X, padx=14, pady=(12, 7))
        ttk.Label(form_header, text='إصدار وتوثيق الرخصة', style='DialogEyebrow.TLabel').pack(anchor=E)
        ttk.Label(form_header, text=self.title, style='DialogHeaderTitle.TLabel').pack(anchor=E, pady=(3, 0))
        form_scroll = ScrollableFrame(self.win, inner_style='TFrame')
        form_scroll.pack(fill=BOTH, expand=True, padx=14, pady=(0, 6))
        form = form_scroll.inner
        self.vars: dict[str, StringVar] = {}
        self.check_vars: dict[str, BooleanVar] = {}

        def add_row(parent, key, label, value=''):
            row = ttk.Frame(parent)
            row.pack(fill=X, pady=6)
            ttk.Label(row, text=label, width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            v = StringVar(value=value)
            self.vars[key] = v
            ent = ttk.Entry(row, textvariable=v, justify='right')
            ent.pack(side=RIGHT, fill=X, expand=True)
            return ent

        def add_date_row(parent, key, label, value=''):
            row = ttk.Frame(parent)
            row.pack(fill=X, pady=6)
            ttk.Label(row, text=label, width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            v = StringVar(value=value or date_today())
            self.vars[key] = v
            ent = ttk.Entry(row, textvariable=v, justify='center', state='readonly', width=15)
            ent.pack(side=RIGHT, padx=4)
            picker = DatePicker(row, v, self._update_absence_rule if key == 'absence_start' else None)
            picker.pack(side=RIGHT, padx=3)
            return ent

        student_row = ttk.Frame(form)
        student_row.pack(fill=X, pady=(3, 9))
        ttk.Label(student_row, text='التلميذ', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
        self.student_label = DisplayStringVar(value='لم يتم اختيار التلميذ')
        self.student_var = StringVar(value='')
        # Use a real Entry for typing; suggestions live in a separate, non-modal popup.
        self.student_combo = ttk.Entry(student_row, textvariable=self.student_var, justify='right')
        self.student_combo.pack(side=RIGHT, fill=X, expand=True)
        self.student_option_map = {}
        self.student_suggestions = SuggestionPopup(self.student_combo, self._choose_student_suggestion)
        self.student_combo.bind('<KeyRelease>', self._schedule_student_suggestions)
        self.student_combo.bind('<Return>', self._student_suggestion_selected)
        self.student_combo.bind('<Down>', lambda _e: self.student_suggestions.move_selection())
        self.student_combo.bind('<Escape>', lambda _e: self.student_suggestions.hide())
        ttk.Button(student_row, text='بحث بالقائمة', command=self.pick_student, style='Info.TButton').pack(side=RIGHT, padx=6)

        attention_box = ttk.Frame(form, style='InfoPanel.TFrame', padding=(12, 9))
        attention_box.pack(fill=X, pady=(3, 7))
        attention_header = ttk.Frame(attention_box, style='InfoPanel.TFrame')
        attention_header.pack(fill=X)
        ttk.Label(attention_header, text='متابعة الغياب والتأخر المتكرر', style='Section.TLabel').pack(side=RIGHT)
        self.attention_open_button = ttk.Button(
            attention_header, text='فتح الملف الشامل للتلميذ',
            command=self.open_student_profile, state='disabled')
        self.attention_open_button.pack(side=LEFT, padx=4)
        self.attention_var = DisplayStringVar(value='اختر التلميذ لعرض ملخص التأخر والغياب والملاحظات السابقة.')
        ttk.Label(attention_box, textvariable=self.attention_var, wraplength=610,
                  justify='right', foreground=UI_PALETTE.get('ink', '#17263D')).pack(fill=X, anchor=E, pady=(4, 0))

        # Guardian profiles are shared across all permit forms and are loaded per selected student.
        guardian_box = ttk.Frame(form, style='FormSection.TFrame', padding=(12, 9))
        guardian_box.pack(fill=X, pady=(5, 8))
        ttk.Label(guardian_box, text='أولياء التلميذ المسجلون', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, padx=4, pady=(0, 7))
        guardian_row = ttk.Frame(guardian_box, style='FormSection.TFrame')
        guardian_row.pack(fill=X, pady=3)
        ttk.Label(guardian_row, text='الولي', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
        self.guardian_var = StringVar(value='')
        self.guardian_combo = ttk.Combobox(guardian_row, textvariable=self.guardian_var,
                                           state='readonly', values=(), justify='right')
        self.guardian_combo.pack(side=RIGHT, fill=X, expand=True)
        self.guardian_combo.bind('<<ComboboxSelected>>', self._guardian_selected)
        ttk.Button(guardian_row, text='+ إضافة ولي', command=self.add_guardian_dialog,
                   style='Gold.TButton').pack(side=RIGHT, padx=5)
        guardian_actions = ttk.Frame(guardian_box, style='FormSection.TFrame')
        guardian_actions.pack(fill=X, pady=(4, 6))
        self.guardian_info_var = DisplayStringVar(value='اختر التلميذ لعرض بيانات أوليائه المسجلين.')
        ttk.Label(guardian_actions, textvariable=self.guardian_info_var,
                  foreground=UI_PALETTE.get('muted', '#64758B'), wraplength=490, justify='right').pack(side=RIGHT, fill=X, expand=True)
        ttk.Button(guardian_actions, text='تعيين الولي المختار كأساسي',
                   command=self.set_selected_guardian_primary, style='Link.TButton').pack(side=LEFT, padx=4)
        guardian_id_row = ttk.Frame(guardian_box, style='FormSection.TFrame')
        guardian_id_row.pack(fill=X, pady=(3, 0))
        ttk.Label(guardian_id_row, text='رقم بطاقة التعريف', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
        self.guardian_id_var = StringVar(value='')
        ttk.Entry(guardian_id_row, textvariable=self.guardian_id_var,
                  state='readonly', justify='center').pack(side=RIGHT, fill=X, expand=True)

        if kind == 'late':
            ttk.Label(form, text='سبب التأخر', anchor=E, style='FieldLabel.TLabel').pack(fill=X, pady=(10, 4))
            late_options = ('التواجد في الاستشارة', 'التأخر بسبب انعدام النقل', 'التأخر في الالتحاق بالقسم')
            self.late_reason = ComboStringVar(late_options, value='')
            self.vars['reason'] = self.late_reason  # logical reason alias used by the form's save/validation path
            self.late_combo = ttk.Combobox(form, textvariable=self.late_reason, state='readonly', values=late_options, justify='right')
            self.late_combo.pack(fill=X, pady=3)
            add_row(form, 'reason_note', 'ملاحظة (اختياري)')
        elif kind == 'absence_return':
            add_date_row(form, 'absence_start', 'تاريخ بداية الغياب')
            row = ttk.Frame(form)
            row.pack(fill=X, pady=5)
            ttk.Label(row, text='تاريخ العودة (يُحدد تلقائيًا)', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            self.vars['return_date'] = StringVar(value=date_today())
            ttk.Entry(row, textvariable=self.vars['return_date'], justify='center', state='readonly', width=15).pack(side=RIGHT, padx=4)
            ttk.Label(form, text='يُحسب الغياب من تاريخ بدايته إلى اليوم السابق للعودة؛ يوم العودة غير محسوب.',
                      style='FormCaption.TLabel', wraplength=600).pack(anchor=E, pady=4)
            add_row(form, 'reason', 'ملاحظة (اختياري)')
            rule_box = ttk.Frame(form, style='FormSection.TFrame', padding=(12, 9))
            rule_box.pack(fill=X, pady=8)
            ttk.Label(rule_box, text='قاعدة قبول تبرير الغياب', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, pady=(0, 5))
            self.rule_label = ttk.Label(rule_box, text='اختر تاريخ بداية الغياب لتحديد طريقة التبرير.', anchor=E, wraplength=560, style='FormCaption.TLabel')
            self.rule_label.pack(fill=X, padx=10, pady=6)
            self.proof_choices = {
                'شهادة طبية': 'medical_certificate',
                'حضور ولي الأمر شخصيًا': 'guardian',
                'شهادة طبية + حضور ولي الأمر شخصيًا': 'medical_and_guardian',
                'تبرير آخر': 'other',
            }
            choice_labels = tuple(self.proof_choices)
            self.proof_method = ComboStringVar(choice_labels, value='شهادة طبية')
            method_row = ttk.Frame(form); method_row.pack(fill=X, pady=5)
            ttk.Label(method_row, text='طريقة تبرير الغياب', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            self.method_combo = ttk.Combobox(method_row, textvariable=self.proof_method, state='readonly',
                                              values=choice_labels, justify='right')
            self.method_combo.pack(side=RIGHT, fill=X, expand=True)
            self.medical_frame = ttk.Frame(form, style='FormSection.TFrame', padding=(11, 8))
            self.check_vars['certificate_present'] = BooleanVar(value=False)
            ttk.Checkbutton(self.medical_frame, text='أؤكد استلام الشهادة الطبية والتحقق منها.',
                            variable=self.check_vars['certificate_present']).pack(anchor=E, padx=7, pady=6)
            self.guardian_frame = ttk.Frame(form, style='FormSection.TFrame', padding=(11, 8))
            ttk.Label(self.guardian_frame, text='بيانات ولي الأمر — يجب أن يكون حاضرًا فعليًا', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, pady=(0, 6))
            add_row(self.guardian_frame, 'guardian_name', 'الاسم الكامل لولي الأمر')
            add_row(self.guardian_frame, 'relationship', 'صلة القرابة')
            add_row(self.guardian_frame, 'national_id', 'رقم بطاقة الهوية الوطنية')
            self.check_vars['guardian_present'] = BooleanVar(value=False)
            ttk.Checkbutton(self.guardian_frame, text='أؤكد حضور ولي الأمر شخصيًا إلى الإدارة والتحقق من هويته.',
                            variable=self.check_vars['guardian_present']).pack(anchor=E, padx=7, pady=6)
            self.other_frame = ttk.Frame(form, style='FormSection.TFrame', padding=(11, 8))
            ttk.Label(self.other_frame, text='تفاصيل التبرير الآخر', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, pady=(0, 6))
            add_row(self.other_frame, 'other_reason', 'اكتب سبب التبرير')
            self.method_combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_absence_method())
            self._update_absence_rule()
        elif kind == 'special_entry':
            self.special_options = {'الطرد من الحصة': 'class_expulsion', 'التواجد في الإدارة': 'administration_visit'}
            self.special_reason = ComboStringVar(tuple(self.special_options), value='الطرد من الحصة')
            reason_row = ttk.Frame(form); reason_row.pack(fill=X, pady=7)
            ttk.Label(reason_row, text='سبب رخصة الدخول', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            self.special_combo = ttk.Combobox(reason_row, textvariable=self.special_reason, state='readonly',
                                               values=tuple(self.special_options), justify='right')
            self.special_combo.pack(side=RIGHT, fill=X, expand=True)
            self.expulsion_frame = ttk.Frame(form, style='FormSection.TFrame', padding=(11, 8))
            ttk.Label(self.expulsion_frame, text='معلومات الطرد من الحصة', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, pady=(0, 6))
            self.teacher_var = StringVar(value='')
            self.selected_teacher_id = None
            self.teacher_option_map = {}
            teacher_row = ttk.Frame(self.expulsion_frame); teacher_row.pack(fill=X, pady=5)
            ttk.Label(teacher_row, text='اسم الأستاذ', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            self.teacher_combo = ttk.Entry(teacher_row, textvariable=self.teacher_var, justify='right')
            self.teacher_combo.pack(side=RIGHT, fill=X, expand=True)
            self.teacher_suggestions = SuggestionPopup(self.teacher_combo, self._choose_teacher_suggestion)
            self.teacher_combo.bind('<KeyRelease>', self._schedule_teacher_suggestions)
            self.teacher_combo.bind('<Return>', self._teacher_suggestion_selected)
            self.teacher_combo.bind('<Down>', lambda _e: self.teacher_suggestions.move_selection())
            self.teacher_combo.bind('<Escape>', lambda _e: self.teacher_suggestions.hide())
            self.subject_var = StringVar(value='')
            subject_row = ttk.Frame(self.expulsion_frame); subject_row.pack(fill=X, pady=5)
            ttk.Label(subject_row, text='المادة الدراسية', width=26, anchor=E, style='FieldLabel.TLabel').pack(side=RIGHT, padx=(4, 9))
            ttk.Entry(subject_row, textvariable=self.subject_var, state='readonly', justify='right').pack(side=RIGHT, fill=X, expand=True)
            if self.app.current_user['role'] == 'admin':
                ttk.Button(self.expulsion_frame, text='إدارة الأساتذة واستيراد القائمة',
                           command=self.app.manage_teachers, style='Link.TButton').pack(anchor=E, padx=8, pady=(0, 6))
            self.special_note = add_row(form, 'special_note', 'ملاحظة (اختياري)')
            self.special_combo.bind('<<ComboboxSelected>>', lambda _e: self._refresh_special_reason())
            self._refresh_special_reason()
        elif kind == 'exit_permit':
            add_row(form, 'reason', 'سبب الخروج')
            add_row(form, 'guardian_name', 'اسم ولي الأمر')
            add_row(form, 'relationship', 'صلة القرابة')
            add_row(form, 'national_id', 'رقم بطاقة الهوية الوطنية')
            ttk.Label(form, text='لن يُطبع رقم الهوية كاملًا ولن يوضع داخل رمز QR؛ سيظهر آخر 4 أرقام فقط على التصريح.',
                      style='FormCaption.TLabel', wraplength=580).pack(anchor=E, pady=4)

        bar = ttk.Frame(self.win, style='DialogFooter.TFrame', padding=(14, 10))
        bar.pack(fill=X, padx=14, pady=(0, 12))
        ttk.Button(bar, text='حفظ وإصدار الرخصة', command=self.save, style='Accent.TButton').pack(side=RIGHT, padx=5)
        ttk.Button(bar, text='إلغاء', command=self.close, style='TButton').pack(side=RIGHT, padx=5)

    def close(self):
        for name in ('student_suggestions', 'teacher_suggestions'):
            popup = getattr(self, name, None)
            if popup is not None:
                popup.hide()
        self.win.destroy()

    def _absence_days_now(self):
        if self.kind != 'absence_return':
            return 0
        try:
            return core.absence_days(self.vars['absence_start'].get(), self.vars['return_date'].get())[2]
        except Exception:
            return 0

    def _update_absence_rule(self):
        if self.kind != 'absence_return':
            return
        try:
            days = core.absence_days(self.vars['absence_start'].get(), self.vars['return_date'].get())[2]
        except Exception:
            self.rule_label.configure(text='تاريخ البداية غير صالح أو يقع بعد تاريخ اليوم.', foreground=UI_PALETTE.get('alert_fg', '#87500D'))
            days = 0
        if days > core.ABSENCE_CERTIFICATE_MAX_DURATION_DAYS:
            self.rule_label.configure(text=f'مدة الغياب {days} يومًا قبل يوم العودة. حضور ولي الأمر شخصيًا إلزامي؛ اختر حضور الولي أو حضوره مع شهادة طبية.', foreground=UI_PALETTE.get('danger_fg', '#A73543'))
        else:
            self.rule_label.configure(text=f'مدة الغياب {days} يومًا قبل يوم العودة. يمكن اختيار شهادة طبية أو حضور الولي أو تبرير آخر.', foreground=UI_PALETTE.get('good_fg', '#126B59'))
        self._refresh_absence_method()

    def _refresh_absence_method(self):
        if self.kind != 'absence_return':
            return
        days = self._absence_days_now()
        choices = tuple(self.proof_choices) if days <= core.ABSENCE_CERTIFICATE_MAX_DURATION_DAYS else (
            'حضور ولي الأمر شخصيًا', 'شهادة طبية + حضور ولي الأمر شخصيًا')
        current = self.proof_method.get()
        if current not in choices:
            self.proof_method.set('حضور ولي الأمر شخصيًا' if days > core.ABSENCE_CERTIFICATE_MAX_DURATION_DAYS else 'شهادة طبية')
        self.method_combo.configure(values=choices)
        method = self.proof_choices.get(self.proof_method.get(), '')
        self.medical_frame.pack_forget(); self.guardian_frame.pack_forget(); self.other_frame.pack_forget()
        if method in ('medical_certificate', 'medical_and_guardian'):
            self.medical_frame.pack(fill=X, pady=5)
        if method in ('guardian', 'medical_and_guardian'):
            self.guardian_frame.pack(fill=X, pady=5)
        if method == 'other':
            self.other_frame.pack(fill=X, pady=5)

    def _refresh_special_reason(self):
        if self.kind != 'special_entry':
            return
        if self.special_options.get(self.special_reason.get()) == 'class_expulsion':
            self.expulsion_frame.pack(fill=X, pady=7)
        else:
            self.expulsion_frame.pack_forget()

    def _schedule_student_suggestions(self, event=None):
        if event is not None and getattr(event, 'keysym', '') in ('Return', 'Escape', 'Up', 'Down', 'Tab', 'Shift_L', 'Shift_R'):
            return
        pending = getattr(self, '_student_search_after_id', None)
        if pending:
            try:
                self.win.after_cancel(pending)
            except Exception:
                pass
        self._student_search_after_id = self.win.after(100, self._refresh_student_suggestions)

    def _refresh_student_suggestions(self, event=None):
        self._student_search_after_id = None
        if not hasattr(self, 'student_combo'):
            return
        if event is not None and getattr(event, 'keysym', '') in ('Return', 'Escape', 'Up', 'Down', 'Tab', 'Shift_L', 'Shift_R'):
            return
        query = self.student_var.get().strip()
        selected_label = getattr(self, '_selected_student_label', '')
        if self.student is not None and query != selected_label:
            self.student = None
            self.student_label.set('لم يتم اختيار التلميذ')
            self._selected_student_label = ''
        if not query:
            self.student_option_map = {}
            self.student_suggestions.hide()
            return
        try:
            rows = core.search_students(query, 18)
            self.student_option_map = {
                f"{student_name(row)} — {row['class_name']}": row
                for row in rows
            }
            self.student_suggestions.show(list(self.student_option_map.items()))
        except Exception:
            self.student_option_map = {}
            self.student_suggestions.hide()

    def _choose_student_suggestion(self, row, _label=''):
        self.set_student(row)
        self.student_suggestions.hide()

    def _student_suggestion_selected(self, _event=None):
        row = self.student_option_map.get(self.student_var.get())
        if row:
            self.set_student(row)
        else:
            self.student_suggestions.select_first()
        return 'break'

    def _schedule_teacher_suggestions(self, event=None):
        if event is not None and getattr(event, 'keysym', '') in ('Return', 'Escape', 'Up', 'Down', 'Tab', 'Shift_L', 'Shift_R'):
            return
        pending = getattr(self, '_teacher_search_after_id', None)
        if pending:
            try:
                self.win.after_cancel(pending)
            except Exception:
                pass
        self._teacher_search_after_id = self.win.after(100, self._refresh_teacher_suggestions)

    def _refresh_teacher_suggestions(self, event=None):
        self._teacher_search_after_id = None
        if not hasattr(self, 'teacher_combo'):
            return
        if event is not None and getattr(event, 'keysym', '') in ('Return', 'Escape', 'Up', 'Down', 'Tab', 'Shift_L', 'Shift_R'):
            return
        query = self.teacher_var.get().strip()
        self.selected_teacher_id = None
        self.subject_var.set('')
        if len(query) < 2:
            self.teacher_option_map = {}
            self.teacher_suggestions.hide()
            return
        try:
            rows = core.search_teachers(query, 20)
            self.teacher_option_map = {}
            for row in rows:
                classes = f" — {row['classes']}" if row['classes'] else ''
                label = f"{row['full_name']} — {row['subject']}{classes}"
                self.teacher_option_map[label] = row
            self.teacher_suggestions.show(list(self.teacher_option_map.items()))
        except Exception:
            self.teacher_option_map = {}
            self.teacher_suggestions.hide()

    def _choose_teacher_suggestion(self, row, _label=''):
        self.selected_teacher_id = int(row['id'])
        self.teacher_var.set(f"{row['full_name']} — {row['subject']}")
        self.subject_var.set(row['subject'])
        self.teacher_suggestions.hide()
        self.teacher_combo.focus_set()

    def _teacher_suggestion_selected(self, _event=None):
        row = self.teacher_option_map.get(self.teacher_var.get())
        if row:
            self._choose_teacher_suggestion(row)
        else:
            self.teacher_suggestions.select_first()
        return 'break'

    def pick_student(self):
        with db_connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM students WHERE active=1').fetchone()[0]
        if not count:
            messagebox.showwarning('قائمة التلاميذ فارغة', 'استورد قائمة التلاميذ أولًا من قسم إدارة التلاميذ (يتطلب حساب المدير).', parent=self.win)
            return
        StudentPicker(self.win, self.set_student)

    def set_student(self, row):
        self.student = row
        label = f"{student_name(row)} — {row['class_name']}"
        self.student_label.set(label)
        self._selected_student_label = label
        if hasattr(self, 'student_var'):
            self.student_var.set(label)
        if hasattr(self, 'student_suggestions'):
            self.student_suggestions.hide()
        self._refresh_guardian_choices()
        self._update_student_attention()
        if hasattr(self, 'student_combo'):
            self.student_combo.focus_set()

    def open_student_profile(self):
        if self.student:
            self.app.show_student_profile(int(self.student["id"]))

    def _update_student_attention(self):
        if not self.student or not hasattr(self, "attention_var"):
            return
        try:
            summary = core.student_monthly_attention(self.student["student_key"], self.app.current_user["id"])
            late_threshold = int(core.setting("late_alert_threshold", "3") or "3")
            absence_threshold = int(core.setting("absence_alert_threshold_days", "5") or "5")
            messages = [
                f"هذا الشهر ({summary['month']}): {summary['late_count']} حالة تأخر.",
                f"أيام الغياب المسجلة خلال الشهر: {summary['absence_days']} يومًا.",
                f"العودة بعد الغياب: {summary['absence_return_count']} مرة في السجل.",
                f"الملاحظات الإدارية السابقة: {summary['note_count']}.",
            ]
            alerts = []
            if summary["late_count"] >= late_threshold:
                alerts.append(f"تنبيه: تجاوز حد التأخر المتكرر ({late_threshold}).")
            if summary["absence_days"] >= absence_threshold:
                alerts.append(f"تنبيه: تجاوز حد الغياب المتكرر ({absence_threshold} أيام).")
            if summary["recent_notes"]:
                last_note = summary["recent_notes"][0]
                preview = str(last_note["note"]).replace("\n", " ").strip()
                if len(preview) > 110:
                    preview = preview[:107] + "…"
                messages.append(f"آخر ملاحظة ({last_note['supervisor']}): {preview}")
            self.attention_var.set("\n".join(alerts + messages))
            self.attention_open_button.configure(state="normal")
        except Exception as exc:
            self.attention_var.set(f"تعذر تحميل ملخص متابعة التلميذ: {exc}")
            self.attention_open_button.configure(state="normal")

    def _refresh_guardian_choices(self, preferred_id=None):
        if not self.student or not hasattr(self, 'guardian_combo'):
            return
        for key in ('guardian_name', 'relationship', 'national_id'):
            if key in self.vars:
                self.vars[key].set('')
        try:
            rows = core.list_student_guardians(int(self.student['id']), self.app.current_user['id'])
        except Exception as exc:
            self.guardian_option_map = {}
            self.guardian_combo.configure(values=())
            self.guardian_var.set('')
            self.guardian_info_var.set('تعذر تحميل بيانات الأولياء المحفوظة.')
            self.guardian_id_var.set('')
            show_error(self.win, 'تعذر تحميل بيانات الولي', exc)
            return
        self.guardian_option_map = {}
        labels = []
        for item in rows:
            relationship = item.get('relationship_label') or item.get('relationship') or ''
            label = f"{item['full_name']} — {relationship} — {item['national_id_masked']}"
            self.guardian_option_map[label] = item
            labels.append(label)
        self.guardian_combo.configure(values=tuple(labels))
        if not rows:
            self.guardian_var.set('')
            self.guardian_id_var.set('')
            self.guardian_info_var.set('لا توجد بيانات ولي محفوظة لهذا التلميذ. أضف الأب أو الأم أو الوصي مرة واحدة لتظهر بياناته تلقائيًا لاحقًا.')
            return
        chosen = next((item for item in rows if preferred_id and int(item['id']) == int(preferred_id)), None)
        if chosen is None:
            chosen = next((item for item in rows if item.get('is_primary')), rows[0])
        chosen_label = next(label for label, item in self.guardian_option_map.items()
                            if int(item['id']) == int(chosen['id']))
        self.guardian_combo.set(chosen_label)
        self._apply_selected_guardian(chosen)

    def _guardian_selected(self, _event=None):
        item = self.guardian_option_map.get(self.guardian_combo.get())
        if item:
            self._apply_selected_guardian(item)

    def _apply_selected_guardian(self, item):
        relationship = item.get('relationship_label') or item.get('relationship') or ''
        for key, value in (('guardian_name', item.get('full_name', '')),
                           ('relationship', relationship),
                           ('national_id', item.get('national_id', ''))):
            if key in self.vars:
                self.vars[key].set(value)
        phone = item.get('phone') or 'غير مسجل'
        self.guardian_id_var.set(item.get('national_id', ''))
        self.guardian_info_var.set(
            f"الولي المحدد: {item.get('full_name', '')} — {relationship}؛ "
            f"آخر أربعة أرقام من الهوية: {item.get('national_id_last4', 'غير متاح')}؛ الهاتف: {phone}. "
            "تحديده هنا لا يعني تأكيد حضوره شخصيًا."
        )

    def set_selected_guardian_primary(self):
        if not self.student:
            messagebox.showinfo('اختر تلميذًا', 'اختر التلميذ أولًا.', parent=self.win)
            return
        item = self.guardian_option_map.get(self.guardian_combo.get())
        if not item:
            messagebox.showinfo('اختر وليًا', 'اختر وليًا مسجلًا أولًا.', parent=self.win)
            return
        try:
            core.set_primary_student_guardian(
                self.app.current_user['id'], int(self.student['id']), int(item['id']))
            self._refresh_guardian_choices(preferred_id=item['id'])
            self.guardian_info_var.set('تم تعيين هذا الولي كأساسي لهذا التلميذ.')
        except Exception as exc:
            show_error(self.win, 'تعذر تعيين الولي الأساسي', exc)

    def add_guardian_dialog(self):
        if not self.student:
            messagebox.showinfo('اختر تلميذًا', 'اختر التلميذ قبل إضافة ولي.', parent=self.win)
            return
        dialog = Toplevel(self.win)
        configure_window(dialog, 'إضافة ولي وربطه بالتلميذ', '560x470')
        dialog.transient(self.win)
        dialog.grab_set()
        ttk.Label(dialog, text=f"إضافة ولي إلى ملف: {student_name(self.student)}",
                  style='Title.TLabel').pack(pady=(14, 8))
        form = ttk.Frame(dialog, padding=18)
        form.pack(fill=BOTH, expand=True)

        def field(label, variable):
            row = ttk.Frame(form); row.pack(fill=X, pady=6)
            ttk.Label(row, text=label, width=22, anchor=E).pack(side=RIGHT, padx=(4, 8))
            entry = ttk.Entry(row, textvariable=variable, justify='right')
            entry.pack(side=RIGHT, fill=X, expand=True)
            return entry

        name_var, id_var, phone_var = StringVar(), StringVar(), StringVar()
        relationship_var = ComboStringVar(core.GUARDIAN_RELATIONSHIPS, value='الأب')
        other_var = StringVar()
        primary_var = BooleanVar(value=not bool(getattr(self, 'guardian_option_map', {})))
        field('الاسم واللقب الكامل', name_var)
        rel_row = ttk.Frame(form); rel_row.pack(fill=X, pady=6)
        ttk.Label(rel_row, text='صلة القرابة', width=22, anchor=E).pack(side=RIGHT, padx=(4, 8))
        relationship_combo = ttk.Combobox(
            rel_row, textvariable=relationship_var, state='readonly',
            values=core.GUARDIAN_RELATIONSHIPS, justify='right')
        relationship_combo.pack(side=RIGHT, fill=X, expand=True)
        field('صلة أخرى (عند الاختيار)', other_var)
        field('رقم بطاقة التعريف', id_var)
        field('رقم الهاتف (اختياري)', phone_var)
        ttk.Checkbutton(form, text='تعيين هذا الولي كأساسي لهذا التلميذ',
                        variable=primary_var).pack(anchor=E, pady=(8, 4))
        ttk.Label(form, text='يُحفظ رقم بطاقة التعريف مشفرًا، ويُعرض مختصرًا في القوائم والتقارير.',
                  style='FormCaption.TLabel', wraplength=490, justify='right').pack(anchor=E, pady=4)

        def save_guardian():
            try:
                saved = core.create_guardian_for_student(
                    self.app.current_user['id'], int(self.student['id']),
                    name_var.get(), relationship_var.get(), id_var.get(), phone_var.get(),
                    other_var.get(), bool(primary_var.get()))
            except Exception as exc:
                show_error(dialog, 'تعذر حفظ بيانات الولي', exc)
                return
            dialog.destroy()
            self._refresh_guardian_choices(preferred_id=saved['id'])
            messagebox.showinfo('تم الحفظ', 'حُفظ ملف الولي وربط بالتلميذ. ستظهر بياناته تلقائيًا عند إصدار الرخص القادمة.', parent=self.win)

        buttons = ttk.Frame(dialog); buttons.pack(fill=X, padx=18, pady=(0, 14))
        ttk.Button(buttons, text='حفظ وربط بالتلميذ', command=save_guardian,
                   style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(buttons, text='إلغاء', command=dialog.destroy).pack(side=RIGHT, padx=4)

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
                selected_reason = self.late_reason.get().strip()
                if not selected_reason:
                    raise ValueError('اختر سبب التأخر قبل إصدار الرخصة.')
                row = core.create_late_slip(self.student, uid, selected_reason, g('reason_note'))
            elif self.kind == 'absence_return':
                proof_method = self.proof_choices[self.proof_method.get()]
                row = core.create_absence_return(
                    self.student, uid, g('absence_start'), g('return_date'), g('reason'),
                    proof_method=proof_method, certificate_present=self.check_vars['certificate_present'].get(),
                    guardian_name=g('guardian_name'), relationship=g('relationship'), national_id=g('national_id'),
                    guardian_present=self.check_vars['guardian_present'].get(), other_reason=g('other_reason'))
            elif self.kind == 'special_entry':
                entry_reason = self.special_options[self.special_reason.get()]
                if entry_reason == 'class_expulsion' and not self.selected_teacher_id:
                    raise ValueError('اختر الأستاذ من القائمة. أضف بياناته أولًا من قسم إدارة الأساتذة إن لم يكن مسجلًا.')
                row = core.create_special_entry(
                    self.student, uid, entry_reason, g('teacher_name'), g('subject'), g('special_note'),
                    teacher_id=getattr(self, 'selected_teacher_id', None))
            else:
                row = core.create_exit_permit(self.student, uid, g('reason'), g('guardian_name'), g('relationship'), g('national_id'))
        except USER_ERRORS as exc:
            self._saving = False
            show_error(self.win, 'تعذر إنشاء السجل', exc)
            return
        if hasattr(self, 'student_suggestions'):
            self.student_suggestions.hide()
        if hasattr(self, 'teacher_suggestions'):
            self.teacher_suggestions.hide()
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
        configure_window(root, f"SchoolPass {APP_VERSION}", '1320x860')
        try:
            sw = int(root.winfo_screenwidth() or 1366)
            sh = int(root.winfo_screenheight() or 768)
        except (TypeError, ValueError):
            sw, sh = 1366, 768
        root.minsize(min(900, max(680, sw - 80)), min(560, max(440, sh - 120)))
        root.resizable(True, True)
        self._windowed_geometry = '1320x860'
        self._fullscreen_mode = False
        make_style(root)
        self.build_ui()
        self.root.bind_all('<F5>', lambda _e: self.refresh_all())
        self.root.bind_all('<F1>', lambda _e: self.show_shortcuts())
        self.root.bind_all('<Control-Key-1>', lambda _e: RecordForm(self, 'late'))
        self.root.bind_all('<Control-Key-2>', lambda _e: RecordForm(self, 'absence_return'))
        self.root.bind_all('<Control-Key-3>', lambda _e: RecordForm(self, 'exit_permit'))
        self.root.bind_all('<Control-Key-4>', lambda _e: RecordForm(self, 'special_entry'))
        self.root.bind_all('<Control-Key-f>', lambda _e: self.quick_search_entry.focus_set())
        self.root.bind_all('<Control-Shift-Key-p>', lambda _e: self.daily_summary())
        self.root.bind_all('<Control-Alt-Key-d>', lambda _e: self.toggle_theme())
        # Windows sometimes ignores a maximize request made before mapping.
        self.root.after(180, self._maximize_on_start)
        self.root.bind('<F11>', lambda _e: self.toggle_fullscreen())
        self.root.bind('<Escape>', lambda _e: self.leave_fullscreen())
        role = 'مدير النظام' if user['role'] == 'admin' else 'مشرف'
        self.base_status = (
            f"المستخدم: {user['display_name']} ({role})  |  قاعدة البيانات محلية  |  "
            "رمز QR مستقل: يعرض وقت الإصدار والانتهاء دون اتصال"
        )
        self.status_text.set(self.base_status)
        self.refresh_records()
        self.root.bind_all('<Key>', self.touch)
        self.root.bind_all('<Button>', self.touch)
        self.root.bind('<Configure>', lambda _e: self._sync_window_button() if hasattr(self, 'maximize_button') else None)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(500, self._tick_dashboard_clock)
        self.root.after(5000, self.check_inactivity)
        self.root.after(60 * 60 * 1000, self.periodic_backup)
    @staticmethod
    def _welcome_text() -> str:
        # Create a brief contextual greeting without relying on a network clock.
        hour = datetime.now().hour
        if 5 <= hour < 12:
            greeting = 'صباح الخير'
        elif 12 <= hour < 18:
            greeting = 'مساء الخير'
        else:
            greeting = 'أهلًا بك'
        return f'{greeting}، {setting("school_name", SCHOOL_NAME)}'

    @staticmethod
    def _dashboard_clock_text() -> str:
        now = datetime.now()
        weekdays = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
        return f'{weekdays[now.weekday()]}  •  {now:%Y/%m/%d}  •  {now:%H:%M}'

    def _tick_dashboard_clock(self):
        # Refresh welcome date/time without doing database or network work.
        try:
            if not self.root.winfo_exists():
                return
            if hasattr(self, 'hero_clock_var'):
                self.hero_clock_var.set(self._dashboard_clock_text())
            if hasattr(self, 'hero_title'):
                title = self._welcome_text()
                if self.hero_title.cget('text') != title:
                    self.hero_title.configure(text=title)
            self.root.after(30_000, self._tick_dashboard_clock)
        except Exception:
            # The window may already be closing; never block shutdown for a clock tick.
            return

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

        self._update_repo = repo
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
                f'ملاحظات الإصدار:\n{notes or "لا توجد ملاحظات إضافية."}\n\n'
                'سيُنزل البرنامج ملف التحديث ويتحقق من سلامته، ثم يشغّل التثبيت تلقائيًا. '
                'سيُغلق SchoolPass أثناء التثبيت وتبقى قاعدة البيانات كما هي.\n\nمتابعة التحديث؟',
                parent=self.root)
            if answer:
                self._download_and_install_update(setup_asset, tag)
        except Exception as exc:
            messagebox.showerror('تعذر قراءة الإصدار', f'بيانات الإصدار على GitHub غير صالحة.\n{exc}', parent=self.root)
        finally:
            self.status_text.set(self.base_status)

    def _download_and_install_update(self, asset, tag):
        """Download and verify the installer before handing off to Inno Setup."""
        import threading
        url = str(asset.get('browser_download_url') or '').strip()
        if not url.startswith('https://'):
            messagebox.showerror('رابط تحديث غير صالح', 'لم يقدّم GitHub رابط تنزيل آمنًا.', parent=self.root)
            return
        self.status_text.set(self.base_status + ' | جارٍ تنزيل التحديث…')
        self.root.update_idletasks()

        def worker():
            installer_path = None
            try:
                request = Request(url, headers={'User-Agent': f'SchoolPass/{APP_VERSION}'})
                with urlopen(request, timeout=90) as response:
                    payload = response.read(250 * 1024 * 1024 + 1)
                if not payload or len(payload) > 250 * 1024 * 1024:
                    raise ValueError('حجم ملف التحديث غير مقبول.')
                digest = hashlib.sha256(payload).hexdigest()
                api = f'https://api.github.com/repos/{self._update_repo}/releases/tags/{tag}'
                req = Request(api, headers={'Accept': 'application/vnd.github+json',
                    'User-Agent': f'SchoolPass/{APP_VERSION}', 'X-GitHub-Api-Version': '2022-11-28'})
                with urlopen(req, timeout=20) as response:
                    release_data = json.loads(response.read(2_000_000).decode('utf-8'))
                checksum_asset = next((x for x in release_data.get('assets', [])
                    if str(x.get('name', '')).casefold() == 'schoolpass_setup.exe.sha256'), None)
                if not checksum_asset:
                    raise ValueError('ملف SHA-256 غير مرفق بالإصدار؛ أُلغي التحديث.')
                checksum_req = Request(checksum_asset['browser_download_url'],
                    headers={'User-Agent': f'SchoolPass/{APP_VERSION}'})
                with urlopen(checksum_req, timeout=20) as response:
                    checksum_text = response.read(4096).decode('ascii', errors='strict')
                expected = checksum_text.strip().split()[0].lower()
                if not re.fullmatch(r'[0-9a-f]{64}', expected) or digest != expected:
                    raise ValueError('فشل التحقق من SHA-256؛ أُلغي التحديث لحماية البرنامج.')
                import tempfile
                fd, installer_path = tempfile.mkstemp(prefix='SchoolPass_Update_', suffix='.exe')
                os.close(fd)
                with open(installer_path, 'wb') as stream:
                    stream.write(payload)
                self.root.after(0, lambda: self._launch_update_installer(installer_path))
            except Exception as exc:
                detail = str(exc)
                try:
                    if installer_path and os.path.exists(installer_path):
                        os.remove(installer_path)
                except OSError:
                    pass
                self.root.after(0, lambda msg=detail: self._update_download_failed(msg))
        threading.Thread(target=worker, name='SchoolPassUpdateDownload', daemon=True).start()

    def _launch_update_installer(self, installer_path):
        try:
            import subprocess
            subprocess.Popen([installer_path, '/SILENT', '/CLOSEAPPLICATIONS',
                '/RESTARTAPPLICATIONS', '/SUPPRESSMSGBOXES'], close_fds=True)
            self.status_text.set('يجري تثبيت التحديث…')
            self.root.after(900, self.close)
        except Exception as exc:
            messagebox.showerror('تعذر بدء التثبيت', f'تم تنزيل التحديث والتحقق منه، لكن تعذر تشغيل المثبّت.\n{exc}',
                parent=self.root)
            self.status_text.set(self.base_status)

    def _update_download_failed(self, detail):
        self.status_text.set(self.base_status)
        messagebox.showerror('فشل تنزيل التحديث أو التحقق منه',
            f'لم يتم تثبيت أي ملف.\n\nالتفاصيل: {detail}', parent=self.root)

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

    def sidebar_preferences(self):
        """Choose which sidebar sections should reopen automatically next time."""
        options = [
            ('operations', 'إجراءات الدخول والخروج'),
            ('data', 'إدارة البيانات'),
            ('reports', 'المتابعة والتقارير'),
        ]
        if self.current_user['role'] == 'admin':
            options.append(('admin', 'إدارة النظام'))
        try:
            saved = json.loads(core.setting('sidebar_open_groups', '[]'))
            if not isinstance(saved, list):
                saved = []
        except (TypeError, ValueError):
            saved = []
        dialog = Toplevel(self.root)
        configure_window(dialog, 'تخصيص القائمة الجانبية', '520x430')
        dialog.transient(self.root)
        dialog.configure(bg=_window_background())
        outer = ttk.Frame(dialog, padding=22)
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(outer, text='المجموعات المفتوحة افتراضيًا', font=('Segoe UI', 16, 'bold')).pack(anchor=E, pady=(0, 8))
        ttk.Label(outer, text='اختر الأقسام التي تريد ظهور عناصرها تلقائيًا عند تشغيل البرنامج. يمكنك تغيير الاختيار في أي وقت.',
                  wraplength=460, justify='right').pack(fill=X, anchor=E, pady=(0, 16))
        variables = {}
        for key, label in options:
            variable = BooleanVar(master=dialog, value=key in saved)
            variables[key] = variable
            ttk.Checkbutton(outer, text=label, variable=variable).pack(fill=X, anchor=E, pady=7)
        ttk.Label(outer, text='سيُحفظ اختيارك على هذا الجهاز ويُطبّق في المرة القادمة أيضًا.',
                  style='FormCaption.TLabel').pack(anchor=E, pady=(12, 8))

        def save_preferences():
            selected = [key for key, variable in variables.items() if bool(variable.get())]
            try:
                core.set_setting('sidebar_open_groups', json.dumps(selected, ensure_ascii=False))
                for key, setter in self._sidebar_group_setters.items():
                    if key in variables:
                        setter(key in selected)
                dialog.destroy()
                self.status_text.set('تم حفظ تفضيلات القائمة الجانبية.')
            except Exception as exc:
                messagebox.showerror('تعذر حفظ التفضيلات', str(exc), parent=dialog)

        footer = ttk.Frame(outer)
        footer.pack(fill=X, side='bottom', pady=(18, 0))
        ttk.Button(footer, text='حفظ الاختيارات', command=save_preferences, style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(footer, text='إلغاء', command=dialog.destroy).pack(side=RIGHT, padx=4)

    def build_ui(self):
        """Professional dashboard shell: RTL navigation, six KPIs, charts, alerts and recent activity."""
        shell = ttk.Frame(self.root)
        shell.pack(fill=BOTH, expand=True)

        sidebar_scroll = ScrollableFrame(shell, inner_style='Sidebar.TFrame',
                                         canvas_background=UI_PALETTE.get('sidebar', '#142B49'),
                                         width=280)
        sidebar_scroll.pack(side=RIGHT, fill=Y)
        sidebar_scroll.configure(width=280)
        self.sidebar_scroll = sidebar_scroll
        sidebar = sidebar_scroll.inner
        brand = ttk.Frame(sidebar, style='Sidebar.TFrame')
        brand.pack(fill=X, padx=18, pady=(24, 18))
        ttk.Label(brand, text='SchoolPass', style='SidebarTitle.TLabel').pack(anchor=E)
        ttk.Label(brand, text='نظام المتابعة المدرسية', style='SidebarSub.TLabel').pack(anchor=E, pady=(5, 0))
        ttk.Frame(sidebar, height=1, style='Sidebar.TFrame').pack(fill=X, padx=16, pady=(0, 12))

        def nav(label, command, style='Nav.TButton', parent=None):
            button = ttk.Button(parent or sidebar, text=label, command=command, style=style)
            button.pack(fill=X, padx=10 if parent is None else 7, pady=3 if parent is None else 2)
            if style == 'Nav.TButton':
                button.bind('<Enter>', lambda _event, target=button: target.configure(style='NavHover.TButton'))
                button.bind('<Leave>', lambda _event, target=button: target.configure(style='Nav.TButton'))
            elif style == 'SubNav.TButton':
                button.bind('<Enter>', lambda _event, target=button: target.configure(style='SubNavHover.TButton'))
                button.bind('<Leave>', lambda _event, target=button: target.configure(style='SubNav.TButton'))
            return button

        self._sidebar_group_setters = {}
        try:
            _saved_groups = json.loads(core.setting('sidebar_open_groups', '[]'))
            if not isinstance(_saved_groups, list):
                _saved_groups = []
        except (TypeError, ValueError):
            _saved_groups = []
        _saved_groups = set(str(item) for item in _saved_groups)
        _group_styles = {
            'operations': 'NavGroupOps.TButton',
            'data': 'NavGroupData.TButton',
            'reports': 'NavGroupReports.TButton',
            'admin': 'NavGroupAdmin.TButton',
        }
        _group_open_styles = {
            'operations': 'NavGroupOpsOpen.TButton',
            'data': 'NavGroupDataOpen.TButton',
            'reports': 'NavGroupReportsOpen.TButton',
            'admin': 'NavGroupAdminOpen.TButton',
        }

        def nav_group(key, label, frame_style, title_style):
            # Category headers stay large and visible; content is independently collapsible.
            group = ttk.Frame(sidebar, style=frame_style)
            group.pack(fill=X, padx=8, pady=5)
            expanded = {'value': False}
            body = ttk.Frame(group, style=frame_style)
            toggle = ttk.Button(group, text=label,
                                style=_group_styles.get(key, 'Nav.TButton'))
            toggle.pack(fill=X, padx=5, pady=(4, 3))

            def set_open(opened):
                if bool(opened) == expanded['value']:
                    return
                if opened:
                    body.pack(fill=X, padx=4, pady=(0, 6))
                    expanded['value'] = True
                    toggle.configure(text=label, style=_group_open_styles.get(key, 'NavHover.TButton'))
                else:
                    body.pack_forget()
                    expanded['value'] = False
                    toggle.configure(text=label, style=_group_styles.get(key, 'Nav.TButton'))

            def toggle_group():
                set_open(not expanded['value'])

            toggle.configure(command=toggle_group)
            # ttk's active state already drives the rounded hover surface; do not
            # overwrite an expanded group style on mouse leave.
            self._sidebar_group_setters[key] = set_open
            if key in _saved_groups:
                set_open(True)
            return body

        nav('⌂   لوحة التحكم الرئيسية', self.refresh_all, 'NavActive.TButton')
        operations_group = nav_group('operations', 'إجراءات الدخول والخروج', 'SidebarGroupOps.TFrame', 'SidebarGroupOpsTitle.TLabel')
        entry_group = ttk.Frame(operations_group, style='SidebarSubmenu.TFrame', padding=(8, 6))
        # Keep the dropdown labels as clean Arabic text. Mixing arrows and Latin-style
        # symbols into the same RTL string caused punctuation to jump to the wrong side.
        entry_toggle = nav('رخص الدخول', lambda: None, parent=operations_group)
        entry_items = (
            ('تأخر عن الدخول', 'late'),
            ('عودة بعد الغياب', 'absence_return'),
            ('الطرد من الحصة — حالة خاصة', 'special_entry'),
        )
        for label, kind in entry_items:
            nav(label, lambda k=kind: RecordForm(self, k), 'SubNav.TButton', parent=entry_group)
        exit_button = nav('⇥   رخصة خروج استثنائي', lambda: RecordForm(self, 'exit_permit'), 'Gold.TButton', parent=operations_group)
        def toggle_entry_group(_event=None):
            if entry_group.winfo_ismapped():
                entry_group.pack_forget()
                entry_toggle.configure(style='Nav.TButton')
            else:
                entry_group.pack(fill=X, padx=8, pady=(0, 4), before=exit_button)
                entry_toggle.configure(style='NavActive.TButton')
        entry_toggle.configure(command=toggle_entry_group)
        entry_toggle.bind('<Enter>', lambda _event: entry_toggle.configure(style='NavHover.TButton'), add='+')
        entry_toggle.bind('<Leave>', lambda _event: entry_toggle.configure(
            style='NavActive.TButton' if entry_group.winfo_ismapped() else 'Nav.TButton'), add='+')

        data_group = nav_group('data', 'إدارة البيانات', 'SidebarGroupData.TFrame', 'SidebarGroupDataTitle.TLabel')
        if self.current_user['role'] == 'admin':
            nav('♟   إدارة الأساتذة واستيراد Excel', self.manage_teachers, parent=data_group)
            nav('👥   إدارة الأولياء', self.manage_guardians, parent=data_group)
        nav('▤   إدارة التلاميذ', self.manage_students, parent=data_group)

        reports_group = nav_group('reports', 'المتابعة والتقارير', 'SidebarGroupReports.TFrame', 'SidebarGroupReportsTitle.TLabel')
        nav('▥   سجل التلاميذ المطرودين', self.open_expulsion_report, parent=reports_group)
        nav('▥   سجل العمليات والتصفية', self.open_logs, parent=reports_group)
        nav('▦   الإحصائيات والرسوم البيانية', self.show_stats, parent=reports_group)
        nav('▧   الأرشيف الشهري والسنوي', self.archive_reports, parent=reports_group)
        nav('▤   سجل الحاجب — تنفيذ الخروج والعودة', self.gatekeeper_log, parent=reports_group)
        nav('!   تنبيهات الخروج دون عودة', self.show_open_exits, parent=reports_group)
        if self.current_user['role'] == 'admin':
            admin_group = nav_group('admin', 'إدارة النظام', 'SidebarGroupAdmin.TFrame', 'SidebarGroupAdminTitle.TLabel')
            nav('◷   التقرير اليومي التفصيلي A4', self.daily_summary, parent=admin_group)
            nav('♙   المستخدمون والصلاحيات', self.manage_users, parent=admin_group)
            nav('▣   النسخ الاحتياطي والاستعادة', self.backup_db, parent=admin_group)
            nav('⚙   إعدادات المؤسسة والطباعة', self.settings_dialog, parent=admin_group)
            nav('≡   سجل التدقيق', self.audit_view, parent=admin_group)
        ttk.Button(sidebar, text='⚙  تخصيص القائمة الجانبية', command=self.sidebar_preferences,
                   style='Accent.TButton').pack(fill=X, padx=10, pady=(10, 8))
        ttk.Frame(sidebar, style='Sidebar.TFrame').pack(fill=BOTH, expand=True)
        profile = ttk.Frame(sidebar, style='SidebarProfile.TFrame', padding=(13, 11))
        profile.pack(fill=X, padx=10, pady=(4, 8))
        ttk.Label(profile, text=self.current_user['display_name'], style='SidebarProfileName.TLabel').pack(anchor=E, fill=X)
        ttk.Label(profile, text=('مدير النظام' if self.current_user['role'] == 'admin' else 'مشرف تربوي'), style='SidebarProfileRole.TLabel').pack(anchor=E, fill=X, pady=(3, 0))
        ttk.Label(sidebar, text=f'SchoolPass  {APP_VERSION}', style='SidebarSub.TLabel').pack(anchor=E, padx=18, pady=(0, 15))

        content_outer = ttk.Frame(shell)
        content_outer.pack(side=LEFT, fill=BOTH, expand=True)
        main_scroll = ScrollableFrame(content_outer, inner_style='TFrame', canvas_background=_window_background())
        main_scroll.pack(fill=BOTH, expand=True)
        self.main_scroll = main_scroll
        content = main_scroll.inner
        self.content_frame = content
        content.bind('<Configure>', lambda event: self._adapt_dashboard_layout(event.width))
        header = ttk.Frame(content, style='PageHeader.TFrame', padding=(20, 15))
        header.pack(fill=X, padx=14, pady=(10, 8))
        right_head = ttk.Frame(header, style='PageHeader.TFrame')
        right_head.pack(fill=X, expand=True)
        ttk.Label(right_head, text='لوحة المتابعة الرئيسية', style='PageTitle.TLabel').pack(anchor=E)
        ttk.Label(right_head, text=setting('school_name', SCHOOL_NAME) + '  •  إدارة رخص الدخول والخروج وسجل المتابعة',
                  style='PageSubtitle.TLabel').pack(anchor=E, pady=(4, 0))
        ttk.Label(right_head, text=f"المستخدم الحالي: {self.current_user['display_name']}  •  اليوم: {date_today()}",
                  style='Hint.TLabel').pack(anchor=E, pady=(7, 0))
        toolbar = ttk.Frame(header, style='PageHeader.TFrame')
        toolbar.pack(fill=X, pady=(10, 0))
        controls = ttk.Frame(toolbar, style='PageHeader.TFrame')
        controls.pack(side=LEFT, anchor='w')
        self.maximize_button = ttk.Button(controls, text='□  استعادة / تكبير', command=self.toggle_maximize)
        self.maximize_button.pack(side=LEFT, padx=2)
        self.refresh_button = ttk.Button(controls, text='⟳  تحديث البيانات', command=self.refresh_all)
        self.refresh_button.pack(side=LEFT, padx=2)
        self.update_button = ttk.Button(controls, text='بحث عن تحديثات', command=self.check_for_updates)
        self.update_button.pack(side=LEFT, padx=2)
        self.theme_button = ttk.Button(controls, text=('☀  الوضع النهاري' if core.setting('theme', 'light') == 'dark' else '☾  الوضع الليلي'), command=self.toggle_theme)
        self.theme_button.pack(side=LEFT, padx=2)
        self.shortcuts_button = ttk.Button(controls, text='⌨  الاختصارات', command=self.show_shortcuts)
        self.shortcuts_button.pack(side=LEFT, padx=2)
        self.user_badge = ttk.Label(toolbar, text=f"●  {self.current_user['display_name']}  •  {'مدير النظام' if self.current_user['role']=='admin' else 'مشرف تربوي'}",
                                    style='ToolbarBadge.TLabel')
        self.user_badge.pack(side=RIGHT, padx=(0, 6), anchor='center')

        # High-impact welcome banner inspired by the approved SchoolPass premium concept.
        # It is built from native ttk widgets so it remains crisp at Windows DPI scaling.
        hero = ttk.Frame(content, style='Hero.TFrame', padding=(18, 14))
        hero.pack(fill=X, padx=14, pady=(0, 9))
        ttk.Frame(hero, style='HeroAccent.TFrame', height=4).pack(fill=X, pady=(0, 10))
        hero_body = ttk.Frame(hero, style='Hero.TFrame')
        hero_body.pack(fill=X)
        hero_info = ttk.Frame(hero_body, style='Hero.TFrame')
        hero_info.pack(side=RIGHT, fill=X, expand=True)
        self.hero_kicker = ttk.Label(hero_info, text='منظومة المتابعة والانضباط المدرسي', style='HeroKicker.TLabel')
        self.hero_kicker.pack(anchor=E, pady=(0, 5))
        self.hero_title = ttk.Label(hero_info, text=self._welcome_text(), style='HeroTitle.TLabel')
        self.hero_title.pack(anchor=E)
        self.hero_description = ttk.Label(
            hero_info,
            text='إدارة دقيقة لرخص الدخول والخروج، متابعة الحالات المفتوحة، وسجل موحّد لجميع العمليات.',
            style='HeroText.TLabel', wraplength=660, justify='right')
        self.hero_description.pack(anchor=E, pady=(7, 0))
        self.hero_clock_var = DisplayStringVar(value=self._dashboard_clock_text())
        self.hero_clock = ttk.Label(hero_info, textvariable=self.hero_clock_var, style='HeroText.TLabel')
        self.hero_clock.pack(anchor=E, pady=(8, 0))

        hero_badge = ttk.Frame(hero_body, style='HeroBadge.TFrame', padding=(20, 15))
        hero_badge.pack(side=LEFT, padx=(14, 0), anchor='center')
        ttk.Label(hero_badge, text='SP', style='HeroBadgeTitle.TLabel').pack()
        ttk.Label(hero_badge, text='SCHOOLPASS', style='HeroBadgeText.TLabel').pack(pady=(2, 0))
        ttk.Label(hero_badge, text=f'الإصدار {APP_VERSION}', style='HeroBadgeText.TLabel').pack(pady=(5, 0))

        hero_actions = ttk.Frame(hero, style='Hero.TFrame')
        hero_actions.pack(fill=X, pady=(12, 0))
        self.hero_entry_button = ttk.Button(
            hero_actions, text='＋  إصدار رخصة دخول', style='Accent.TButton',
            command=lambda: RecordForm(self, 'late'))
        self.hero_entry_button.pack(side=RIGHT, padx=(0, 7))
        self.hero_exit_button = ttk.Button(
            hero_actions, text='⇥  خروج استثنائي', style='Gold.TButton',
            command=lambda: RecordForm(self, 'exit_permit'))
        self.hero_exit_button.pack(side=RIGHT, padx=7)
        self.hero_open_button = ttk.Button(
            hero_actions, text='متابعة حالات الخروج', style='TButton',
            command=self.show_open_exits)
        self.hero_open_button.pack(side=LEFT, padx=(7, 0))
        self.hero_frame = hero

        # KPI cards in a responsive 3x2 grid with an icon, a clear metric and context.
        cards = ttk.Frame(content)
        cards.pack(fill=X, padx=14, pady=(1, 7))
        self.card_values = {}
        card_specs = [
            ('students', 'إجمالي التلاميذ النشطين', '♙', 'قاعدة بيانات المؤسسة', 'KPIAccentBlue.TFrame', 'MetricIconBlue.TLabel'),
            ('today_entry', 'رخص الدخول اليوم', '↩', 'تأخر وعودة بعد الغياب', 'KPIAccentTeal.TFrame', 'MetricIconTeal.TLabel'),
            ('today_exit', 'رخص الخروج اليوم', '⇥', 'خروج استثنائي موثّق', 'KPIAccentGold.TFrame', 'MetricIconGold.TLabel'),
            ('late', 'حالات التأخر اليوم', '◷', 'متابعة الدخول الصباحي', 'KPIAccentViolet.TFrame', 'MetricIconViolet.TLabel'),
            ('absence', 'العودة بعد الغياب', '✓', 'مع احتساب المدة بدقة', 'KPIAccentAmber.TFrame', 'MetricIconAmber.TLabel'),
            ('open', 'حالات خروج مفتوحة', '!', 'يلزم تسجيل العودة', 'KPIAccentRose.TFrame', 'MetricIconRose.TLabel'),
        ]
        self.cards_frame = cards
        self.kpi_cards = []
        for col in range(3):
            cards.grid_columnconfigure(col, weight=1, uniform='kpi')
        for idx, (key, label, icon, hint, accent_style, icon_style) in enumerate(card_specs):
            card = ttk.Frame(cards, style='MetricCard.TFrame', padding=(12, 9))
            self.kpi_cards.append(card)
            card.grid(row=idx // 3, column=2 - (idx % 3), sticky='nsew', padx=5, pady=5)
            ttk.Frame(card, height=4, style=accent_style).pack(fill=X, pady=(0, 8))
            card_head = ttk.Frame(card, style='MetricCard.TFrame')
            card_head.pack(fill=X)
            icon_label = ttk.Label(card_head, text=icon, style=icon_style)
            icon_label.pack(side=LEFT, anchor='n')
            title_label = ttk.Label(card_head, text=label, style='CardTitle.TLabel', wraplength=220, justify='right')
            title_label.pack(side=RIGHT, fill=X, expand=True, anchor='n', pady=(4, 0))
            value = ttk.Label(card, text='—', style='CardValue.TLabel')
            value.pack(fill=X, pady=(7, 0))
            hint_label = ttk.Label(card, text=hint, style='MetricHint.TLabel')
            hint_label.pack(fill=X, pady=(4, 0))
            self.card_values[key] = value
            detail_keys = {'today_entry': 'today_entry', 'today_exit': 'today_exit', 'late': 'today_late',
                           'absence': 'today_absence', 'open': 'open_exits'}
            if key in detail_keys:
                def set_card_hover(active, _card=card, _head=card_head, _icon=icon_label,
                                   _title=title_label, _value=value, _hint=hint_label, _icon_style=icon_style):
                    _card.configure(style='MetricCardHover.TFrame' if active else 'MetricCard.TFrame')
                    _head.configure(style='MetricCardHover.TFrame' if active else 'MetricCard.TFrame')
                    _icon.configure(style='MetricCardIconHover.TLabel' if active else _icon_style)
                    _title.configure(style='MetricCardTitleHover.TLabel' if active else 'CardTitle.TLabel')
                    _value.configure(style='MetricCardValueHover.TLabel' if active else 'CardValue.TLabel')
                    _hint.configure(style='MetricCardHintHover.TLabel' if active else 'MetricHint.TLabel')
                for interactive in (card, card_head, icon_label, title_label, value, hint_label):
                    interactive.bind('<Enter>', lambda _e, fn=set_card_hover: fn(True), add='+')
                    interactive.bind('<Leave>', lambda _e, fn=set_card_hover: fn(False), add='+')
                    interactive.bind('<Button-1>', lambda _e, metric=detail_keys[key]: self.open_dashboard_details(metric), add='+')
                    try: interactive.configure(cursor='hand2')
                    except Exception: pass

        # Chart and alerts share a row to keep the dashboard informative yet compact.
        overview = ttk.Frame(content)
        overview.pack(fill=X, padx=14, pady=(0, 9))
        chart_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(15, 12))
        self.chart_panel = chart_panel
        chart_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
        chart_head = ttk.Frame(chart_panel, style='Surface.TFrame')
        chart_head.pack(fill=X)
        ttk.Label(chart_head, text='حركة الرخص حسب الفترة', style='Section.TLabel').pack(side=RIGHT)
        self.chart_period_var = ComboStringVar(('اليوم', 'الأسبوع', 'الشهر'), value='الأسبوع')
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
        self.dashboard_chart = Canvas(chart_panel, height=112, background=UI_PALETTE.get('canvas', '#FFFFFF'), highlightthickness=0)
        self.dashboard_chart.pack(fill=X, expand=True, pady=(3, 0))

        alert_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(15, 12))
        self.alert_panel = alert_panel
        alert_panel.pack(side=RIGHT, fill=Y, padx=(5, 0))
        ttk.Label(alert_panel, text='التنبيهات والمتابعة', style='Section.TLabel').pack(anchor=E, pady=(0, 8))
        self.alert_summary = DisplayStringVar(value='لا توجد تنبيهات حالية.')
        self.alert_label = ttk.Label(alert_panel, textvariable=self.alert_summary, style='Good.TLabel',
                                     wraplength=260, justify='right')
        self.alert_label.pack(fill=X, pady=3)
        ttk.Button(alert_panel, text='عرض حالات الخروج المفتوحة', command=self.show_open_exits).pack(fill=X, pady=(7, 2))
        ttk.Button(alert_panel, text='فتح سجل العمليات', command=self.open_logs).pack(fill=X, pady=2)
        self.stats_toggle_button = ttk.Button(alert_panel, text='＋  عرض الإحصائيات والرسوم', style='Accent.TButton')
        self.stats_toggle_button.pack(fill=X, pady=(9, 2))

        def toggle_dashboard_stats():
            if self.chart_panel.winfo_ismapped():
                self.chart_panel.pack_forget()
                self.stats_toggle_button.configure(text='＋  عرض الإحصائيات والرسوم')
            else:
                self.chart_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
                self.stats_toggle_button.configure(text='−  إخفاء الإحصائيات والرسوم')

        self.stats_toggle_button.configure(command=toggle_dashboard_stats)
        # Keep the activity chart visible on first launch. Hiding it by default
        # left a large, uninformative blank area and made the dashboard appear
        # unfinished. Users may still collapse it with the toggle in the alerts
        # panel when they need more room for the records table.
        self.stats_toggle_button.configure(text='−  إخفاء الإحصائيات والرسوم')

        action_row = ttk.Frame(content)
        action_row.pack(fill=X, padx=14, pady=(0, 7))
        self.dashboard_action_buttons = []
        def add_dashboard_action(parent, full, compact, command, style=None, side=RIGHT):
            button = ttk.Button(parent, text=full, command=command, style=style or 'TButton')
            button.pack(side=side, padx=3)
            self.dashboard_action_buttons.append((button, full, compact))
        add_dashboard_action(action_row, '＋  إصدار رخصة الدخول — تأخر', '＋ دخول — تأخر', lambda: RecordForm(self, 'late'), 'Accent.TButton')
        add_dashboard_action(action_row, '↩  رخصة عودة بعد الغياب', '↩ عودة بعد الغياب', lambda: RecordForm(self, 'absence_return'), 'Info.TButton')
        add_dashboard_action(action_row, '⚑  دخول خاص — طرد / إدارة', '⚑ دخول خاص', lambda: RecordForm(self, 'special_entry'), 'Info.TButton')
        add_dashboard_action(action_row, '⇥  إصدار رخصة خروج', '⇥ خروج', lambda: RecordForm(self, 'exit_permit'), 'Gold.TButton')
        if self.current_user['role'] == 'admin':
            add_dashboard_action(action_row, 'التقرير اليومي التفصيلي A4', 'التقرير اليومي A4', self.daily_summary, 'Info.TButton', LEFT)
        add_dashboard_action(action_row, 'التقرير الشهري', 'شهري', self.monthly_report, 'TButton', LEFT)

        panel = ttk.Frame(content, style='Surface.TFrame', padding=(16, 13))
        panel.pack(fill=BOTH, expand=True, padx=14, pady=(0, 12))
        title_row = ttk.Frame(panel, style='Surface.TFrame')
        title_row.pack(fill=X, pady=(0, 7))
        ttk.Label(title_row, text='أحدث العمليات المسجلة', style='Section.TLabel').pack(side=RIGHT)
        ttk.Label(title_row, text='انقر مرتين لعرض ملف الرخصة', style='Hint.TLabel').pack(side=LEFT)
        search_row = ttk.Frame(panel, style='Surface.TFrame')
        search_row.pack(fill=X, pady=(0, 7))
        ttk.Label(search_row, text='بحث فوري بالاسم أو الرقم المرجعي أو القسم  •  Ctrl+F', style='Hint.TLabel').pack(side=RIGHT, padx=(0, 7))
        self.quick_search_var = StringVar(value='')
        self.quick_search_entry = ttk.Entry(search_row, textvariable=self.quick_search_var, justify='right')
        self.quick_search_entry.pack(side=RIGHT, fill=X, expand=True, padx=5)
        self.quick_search_entry.bind('<KeyRelease>', lambda _e: self._schedule_record_filter())
        ttk.Button(search_row, text='مسح', command=self.clear_quick_search, style='Link.TButton').pack(side=LEFT, padx=3)
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
        self.record_action_buttons = []
        def add_record_action(full, compact, command, style=None, side=RIGHT):
            button = ttk.Button(bar, text=full, command=command, style=style or 'TButton')
            button.pack(side=side, padx=3)
            self.record_action_buttons.append((button, full, compact))
        add_record_action('عرض التفاصيل', 'تفاصيل', self.open_selected_record, 'Info.TButton')
        add_record_action('طباعة الرخصة', 'طباعة', self.print_selected, 'Gold.TButton')
        add_record_action('إعادة طباعة الرخصة', 'إعادة طباعة', self.reprint_selected, 'Accent.TButton')
        add_record_action('تسجيل العودة', 'عودة', self.return_selected_exit, 'Success.TButton')
        add_record_action('إلغاء السجل', 'إلغاء', self.cancel_selected, 'Danger.TButton', side=LEFT)
        add_record_action('طباعة رخصتين مختلفتين على A4', 'رخصتان A4', self.print_two_selected, 'Gold.TButton', LEFT)
        self.status_text = DisplayStringVar(value='')
        status = ttk.Label(content_outer, textvariable=self.status_text, style='StatusStrong.TLabel')
        status.pack(fill=X, side='bottom')
        self.root.after_idle(lambda: self._adapt_dashboard_layout(self.content_frame.winfo_width()))

    def _apply_record_tree_tags(self, tree):
        """Apply semantic, theme-aware row colors to every permit/history grid."""
        palette = UI_PALETTE
        tree.tag_configure('late', background=palette['alert_bg'], foreground=palette['alert_fg'])
        tree.tag_configure('absence', background=palette['primary_soft'], foreground=palette['primary'])
        tree.tag_configure('exit', background=palette['good_bg'], foreground=palette['good_fg'])
        tree.tag_configure('special', background=palette['danger_bg'], foreground=palette['danger_fg'])
        tree.tag_configure('cancelled', background=palette['surface_alt'], foreground=palette['danger_fg'])

    def _apply_main_tree_tags(self):
        if hasattr(self, 'tree'):
            self._apply_record_tree_tags(self.tree)

    def toggle_theme(self):
        """Persist and apply the light/dark theme without changing school data."""
        try:
            current = core.setting('theme', 'light')
            selected = 'dark' if current != 'dark' else 'light'
            core.set_setting('theme', selected)
            make_style(self.root)
            self._apply_main_tree_tags()
            surface_background = UI_PALETTE.get('bg', '#111827')
            if hasattr(self, 'main_scroll'):
                self.main_scroll.set_canvas_background(surface_background)
            if hasattr(self, 'sidebar_scroll'):
                self.sidebar_scroll.set_canvas_background(UI_PALETTE.get('sidebar', '#0E1B2D'))
            # Canvas/text widgets do not inherit ttk styles; update the live dashboard.
            if hasattr(self, 'dashboard_chart'):
                self.dashboard_chart.configure(background=UI_PALETTE.get('canvas', '#FFFFFF'))
            self.refresh_dashboard_chart()
            if hasattr(self, 'theme_button'):
                self.theme_button.configure(text='☀  الوضع النهاري' if selected == 'dark' else '☾  الوضع الليلي')
            self.root.update_idletasks()
        except Exception as exc:
            show_error(self.root, 'تعذر تغيير المظهر', exc)

    def show_shortcuts(self):
        """Show the actual keyboard shortcuts supported by the current application."""
        win = Toplevel(self.root)
        configure_window(win, 'اختصارات SchoolPass', '590x560')
        win.transient(self.root)
        win.grab_set()
        outer = ttk.Frame(win, style='Dialog.TFrame', padding=(22, 18))
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(outer, text='اختصارات لوحة المفاتيح', style='DialogTitle.TLabel').pack(anchor=E)
        ttk.Label(outer, text='أوامر سريعة لتقليل التنقل وتسريع العمل اليومي.',
                  style='Hint.TLabel').pack(anchor=E, pady=(4, 14))
        rows = [
            ('F1', 'عرض هذه النافذة'),
            ('F5', 'تحديث لوحة المتابعة والسجلات'),
            ('Ctrl + 1', 'إصدار رخصة دخول بسبب التأخر'),
            ('Ctrl + 2', 'إصدار رخصة عودة بعد الغياب'),
            ('Ctrl + 3', 'إصدار رخصة خروج استثنائي'),
            ('Ctrl + 4', 'إصدار رخصة دخول لحالة خاصة'),
            ('Ctrl + F', 'الانتقال مباشرة إلى البحث الفوري'),
            ('Ctrl + Alt + D', 'التبديل بين المظهرين النهاري والليلي'),
            ('F11', 'تفعيل أو إلغاء ملء الشاشة'),
            ('Esc', 'الخروج من وضع ملء الشاشة'),
        ]
        if self.current_user['role'] == 'admin':
            rows.insert(8, ('Ctrl + Shift + P', 'فتح التقرير اليومي التفصيلي'))
        panel = ttk.Frame(outer, style='Surface.TFrame', padding=(12, 8))
        panel.pack(fill=BOTH, expand=True)
        for index, (key, description) in enumerate(rows):
            row = ttk.Frame(panel, style='Surface.TFrame', padding=(5, 6))
            row.pack(fill=X)
            key_label = ttk.Label(row, text=key, style='Badge.TLabel', width=17, anchor='center')
            key_label.pack(side=LEFT, padx=(0, 12))
            ttk.Label(row, text=description, style='ShortcutDesc.TLabel', anchor=E).pack(side=RIGHT, fill=X, expand=True)
            if index < len(rows) - 1:
                ttk.Frame(panel, height=1, style='ShortcutDivider.TFrame').pack(fill=X, padx=4)
        footer = ttk.Frame(outer, style='Dialog.TFrame')
        footer.pack(fill=X, pady=(12, 0))
        ttk.Button(footer, text='إغلاق', command=win.destroy, style='Accent.TButton').pack(side=LEFT)
        win.bind('<Escape>', lambda _event: win.destroy())
        win.bind('<Return>', lambda _event: win.destroy())
        win.focus_set()

    def _adapt_dashboard_layout(self, available_width: int):
        """Reflow dashboard cards and controls as the available width changes."""
        width = max(1, int(available_width or 1))
        if hasattr(self, 'kpi_cards'):
            columns = 3 if width >= 1050 else (2 if width >= 650 else 1)
            for index, card in enumerate(self.kpi_cards):
                card.grid_configure(row=index // columns, column=columns - 1 - (index % columns))
            for col in range(3):
                self.cards_frame.grid_columnconfigure(col, weight=1 if col < columns else 0,
                                                      uniform='kpi' if col < columns else '')
        compact = width < 900
        if hasattr(self, 'hero_entry_button'):
            self.hero_entry_button.configure(text='＋ دخول' if compact else '＋  إصدار رخصة دخول')
            self.hero_exit_button.configure(text='⇥ خروج' if compact else '⇥  خروج استثنائي')
            self.hero_open_button.configure(text='الحالات المفتوحة' if compact else 'متابعة حالات الخروج')
        if hasattr(self, 'hero_description'):
            self.hero_description.configure(wraplength=max(220, min(660, width - 360)))
        if hasattr(self, 'maximize_button'):
            self.maximize_button.configure(text='□ تكبير' if compact else '□ استعادة / تكبير')
            self.refresh_button.configure(text='⟳ تحديث' if compact else '⟳ تحديث البيانات')
            self.update_button.configure(text='تحديثات' if compact else 'بحث عن تحديثات')
            self.theme_button.configure(text=('☀ نهاري' if compact else '☀ الوضع النهاري') if core.setting('theme', 'light') == 'dark'
                                        else ('☾ ليلي' if compact else '☾ الوضع الليلي'))
            self.shortcuts_button.configure(text='⌨ مفاتيح' if compact else '⌨ الاختصارات')
        for button, full, short in getattr(self, 'dashboard_action_buttons', []):
            button.configure(text=short if compact else full)
        for button, full, short in getattr(self, 'record_action_buttons', []):
            button.configure(text=short if compact else full)
        if not hasattr(self, 'chart_panel') or not hasattr(self, 'alert_panel'):
            return
        layout = 'stacked' if compact else 'side_by_side'
        if getattr(self, '_dashboard_layout', None) == layout:
            return
        self._dashboard_layout = layout
        if compact:
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
        surface = UI_PALETTE.get('canvas', '#FFFFFF')
        axis = UI_PALETTE.get('border', '#E4EAF2')
        grid = UI_PALETTE.get('heading', '#EDF2F8')
        label_color = UI_PALETTE.get('muted', '#64758B')
        c.configure(background=surface)
        c.delete('all')
        width = max(440, c.winfo_width())
        height = max(130, c.winfo_height())
        left, right, top, bottom = 34, width-12, 10, height-25
        c.create_line(left, top, left, bottom, fill=axis)
        c.create_line(left, bottom, right, bottom, fill=axis)
        names = [('late', 'تأخر', '#D7A33B'), ('absence_return', 'عودة بعد غياب', '#4A89B8'),
                 ('special_entry', 'دخول خاص', '#B85A45'), ('exit_permit', 'خروج', '#2D8062'),
                 ('exit_return', 'إثبات عودة', '#8A78B7')]
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

    def gatekeeper_log(self):
        """Show permits issued, physical exit confirmed at the gate and subsequent return."""
        win=Toplevel(self.root)
        win.title('سجل الحاجب — تنفيذ الخروج والعودة')
        win.geometry('1380x650')
        win.minsize(1000,520)
        win.transient(self.root)
        top=ttk.Frame(win);top.pack(fill=X,padx=12,pady=(12,6))
        states={'الكل':'all','لم ينفذ الخروج':'pending','نُفذ الخروج':'executed',
                'عاد التلميذ':'returned','ملغاة':'cancelled'}
        state_var=ComboStringVar(list(states),value='الكل')
        ttk.Label(top,text='الحالة:').pack(side=RIGHT,padx=5)
        ttk.Combobox(top,textvariable=state_var,values=list(states),state='readonly',
                     width=18,justify='right').pack(side=RIGHT,padx=4)
        count_var=DisplayStringVar(value='')
        ttk.Label(top,textvariable=count_var).pack(side=LEFT,padx=8)
        cols=('record_no','student','class_name','issued','guardian','executed','executor','returned','returner','state')
        tree=ttk.Treeview(win,columns=cols,show='headings')
        heads=[('record_no','رقم الرخصة',120),('student','التلميذ',160),('class_name','القسم',125),
               ('issued','وقت إصدار الرخصة',145),('guardian','ولي الأمر',140),('executed','وقت الخروج الفعلي',145),
               ('executor','من سجّل التنفيذ',125),('returned','وقت العودة',145),('returner','من سجّل العودة',125),('state','الحالة',105)]
        for col,title,width in heads:
            tree.heading(col,text=title);tree.column(col,width=width,anchor=E,stretch=True)
        tree.pack(fill=BOTH,expand=True,padx=12,pady=6)
        def refresh():
            try: rows=core.get_gatekeeper_log(self.current_user['id'],state=states[state_var.get()])
            except USER_ERRORS as exc:
                show_error(win,'تعذر تحميل سجل الحاجب',exc);return
            for item in tree.get_children():tree.delete(item)
            for r in rows:
                details=json.loads(r.get('details_json') or '{}')
                state=('ملغاة' if r['status']=='cancelled' else
                       ('عاد التلميذ' if r['status']=='returned' else
                        ('نُفذ الخروج' if r.get('exit_executed_at') else 'لم ينفذ الخروج')))
                tree.insert('',END,iid=str(r['id']),values=(
                    r['record_no'],r['student_name'],r['class_name'],r['issued_at'] or r['created_at'],
                    details.get('guardian_name','—'),r.get('exit_executed_at') or '—',
                    r.get('exit_executor_name') or '—',r.get('returned_at') or '—',
                    r.get('returner_name') or '—',state))
            count_var.set(f'عدد رخص الخروج: {len(rows)}')
        def selected():
            return int(tree.selection()[0]) if tree.selection() else None
        def mark_executed():
            rid=selected()
            if not rid:
                messagebox.showinfo('اختيار رخصة','حدد رخصة خروج أولًا.',parent=win);return
            try:core.mark_exit_executed(rid,self.current_user['id'])
            except USER_ERRORS as exc:
                show_error(win,'تعذر تأكيد تنفيذ الخروج',exc);return
            self.refresh_records();refresh()
        def returned():
            rid=selected()
            if not rid:
                messagebox.showinfo('اختيار رخصة','حدد رخصة خروج أولًا.',parent=win);return
            self._return_exit(rid)
            self.refresh_records();refresh()
        def details():
            rid=selected()
            if rid:self.show_record(rid)
            else:messagebox.showinfo('اختيار رخصة','حدد رخصة أولًا.',parent=win)
        bar=ttk.Frame(win);bar.pack(fill=X,padx=12,pady=(2,12))
        ttk.Button(bar,text='تأكيد تنفيذ الخروج فعليًا',command=mark_executed,style='Accent.TButton').pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='تسجيل عودة التلميذ',command=returned).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='عرض الرخصة المحددة',command=details).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='تحديث السجل',command=refresh).pack(side=LEFT,padx=4)
        state_var.trace_add('write',lambda *_:refresh())
        tree.bind('<Double-1>',lambda _e:details())
        refresh()

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

    def _schedule_record_filter(self):
        """Debounce quick-search updates so typing never rebuilds the grid per keystroke."""
        pending = getattr(self, "_record_filter_after_id", None)
        if pending:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
        self._record_filter_after_id = self.root.after(110, self._apply_record_filter)

    def _apply_record_filter(self):
        if not hasattr(self, 'tree'):
            return
        self._record_filter_after_id = None
        query = self.quick_search_var.get().strip().casefold() if hasattr(self, 'quick_search_var') else ''
        wanted = []
        for r in getattr(self, '_records_cache', []):
            values = (r['record_no'], kind_label(r['kind']), r['student_name'], r['class_name'],
                      (r['issued_at'] or r['created_at']), r['supervisor'] or '—', status_label(r['status']))
            if query and query not in ' '.join(str(v or '') for v in values).casefold():
                continue
            tag = 'late' if r['kind'] == 'late' else ('absence' if r['kind'] == 'absence_return' else ('special' if r['kind'] == 'special_entry' else 'exit'))
            tags = ('cancelled',) if r['status'] == 'cancelled' else (tag,)
            wanted.append((str(r['id']), values, tags))
        wanted_ids = {item[0] for item in wanted}
        existing_ids = set(self.tree.get_children())
        for stale_id in existing_ids - wanted_ids:
            self.tree.delete(stale_id)
        for index, (iid, values, tags) in enumerate(wanted):
            if iid in existing_ids:
                current = self.tree.item(iid, 'values')
                if tuple(current) != tuple(values):
                    self.tree.item(iid, values=values, tags=tags)
                if self.tree.index(iid) != index:
                    self.tree.move(iid, '', index)
            else:
                self.tree.insert('', END, iid=iid, values=values, tags=tags)

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

    def print_selected(self):
        rid = self._need_selection()
        if not rid:
            return
        row = record_by_id(rid)
        if row and int(row["printed_count"] or 0) > 0:
            messagebox.showinfo("هذه الرخصة طُبعت سابقًا",
                                "استخدم زر «إعادة طباعة الرخصة» حتى تظهر علامة النسخة المعادة ويُسجل ذلك في التدقيق.",
                                parent=self.root)
            return
        self._print_by_id(rid, self.root)

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
            proof_labels = {'guardian': 'حضور ولي الأمر شخصيًا', 'medical_certificate': 'شهادة طبية',
                            'medical_and_guardian': 'شهادة طبية + حضور ولي الأمر شخصيًا', 'other': 'تبرير آخر'}
            lines += [f"بداية الغياب: {details.get('absence_start', '—')}", f"تاريخ العودة: {details.get('return_date', '—')}",
                      f"أيام الغياب (دون احتساب يوم العودة): {details.get('days_count', '—')} يومًا",
                      f"طريقة التبرير: {proof_labels.get(details.get('proof_method'), '—')}"]
            if details.get('proof_method') == 'other':
                lines.append(f"تفاصيل التبرير الآخر: {details.get('other_proof_reason', '—')}")
            if details.get('medical_certificate_confirmed'):
                lines.append(f"الشهادة الطبية: تم الاستلام والتحقق ({details.get('medical_certificate_verified_at', '—')})")
            if details.get('guardian_present'):
                lines += [f"ولي الأمر: {details.get('guardian_name', '—')}", f"صلة القرابة: {details.get('relationship', '—')}",
                          f"رقم الهوية (مقنّع؛ الأصل مشفّر): {core.mask_national_id(details)}",
                          f"وقت تأكيد الحضور: {details.get('guardian_verified_at', '—')}"]
        if row['kind'] == 'special_entry':
            lines += [f"سبب الدخول: {details.get('entry_reason_label', 'حالة خاصة')}"]
            if details.get('entry_reason') == 'class_expulsion':
                lines += [f"الأستاذ الذي قام بالطرد: {details.get('expelling_teacher', '—')}",
                          f"المادة: {details.get('subject', '—')}"]
            if details.get('note'):
                lines.append(f"ملاحظة: {details.get('note')}")
        if row['kind'] == 'exit_return':
            lines.append(f"مرجع تصريح الخروج: {details.get('permit_no', '—')}")
        if row['kind'] == 'exit_permit':
            lines += [f"ولي الأمر: {details.get('guardian_name', '—')}", f"صلة القرابة: {details.get('relationship', '—')}",
                      f"رقم الهوية (مقنّع؛ الأصل مشفّر): {core.mask_national_id(details)}",
                      f"تنفيذ الخروج فعليًا: {row['exit_executed_at'] or 'لم يُسجل بعد'}"]
            if row['exit_executed_by'] is not None:
                executor = get_user(row['exit_executed_by'])
                lines.append(f"سجّل التنفيذ: {executor['display_name'] if executor else '—'}")
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
        print_count = int(row['printed_count'] or 0)
        ttk.Button(bar, text='طباعة الرخصة', state='disabled' if print_count else 'normal',
                   command=lambda: self._print_by_id(record_id, win), style='Gold.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إعادة طباعة الرخصة', state='normal' if print_count else 'disabled',
                   command=lambda: self._reprint_by_id(record_id, win), style='Accent.TButton').pack(side=RIGHT, padx=4)
        if row['status'] == 'valid':
            if row['kind'] == 'exit_permit':
                if not str(row['exit_executed_at'] or '').strip():
                    ttk.Button(bar, text='تأكيد تنفيذ الخروج', command=lambda: self._mark_exit_executed(record_id, win)).pack(side=RIGHT, padx=4)
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
        if messagebox.askyesno('طباعة الرخصة', 'تم حفظ السجل. هل تريد طباعة الرخصة الآن؟\nعند فتح نافذة الطباعة اختر الطابعة وورق A6 والحجم الفعلي 100%.', parent=win):
            self._print_by_id(record_id, win)

    def _print_by_id(self, record_id, parent):
        """Create the one-time original print; reject accidental duplicate originals."""
        try:
            paths = core.print_record(record_id, self.current_user['id'], launch=False, mode='initial')
        except USER_ERRORS + (ImportError,) as exc:
            show_error(parent, 'تعذرت طباعة الرخصة', exc)
            return
        self.refresh_records()
        self._show_print_options(paths[0], parent, title='طباعة الرخصة — النسخة الأصلية')

    def _reprint_by_id(self, record_id, parent):
        """Explicitly create a marked reprint of the same numbered permit."""
        try:
            paths = core.print_record(record_id, self.current_user['id'], launch=False, mode='reprint')
        except USER_ERRORS + (ImportError,) as exc:
            show_error(parent, 'تعذرت إعادة طباعة الرخصة', exc)
            return
        self.refresh_records()
        self._show_print_options(paths[0], parent, title='إعادة طباعة الرخصة')

    def _show_print_options(self, pdf_path: Path, parent, title='معاينة وطباعة'):
        win = Toplevel(parent)
        win.title(title)
        win.geometry('560x300')
        win.minsize(460, 250)
        win.transient(parent)
        ttk.Label(win, text=title, style='Title.TLabel').pack(pady=(18, 6))
        ttk.Label(win, text=f'الملف: {pdf_path.name}', wraplength=500, style='CardMeta.TLabel').pack(pady=4)
        printers, default_printer = _windows_printer_list()
        ttk.Label(win, text='اختر الطابعة (أو افتح المعاينة واخترها من عارض PDF):').pack(anchor=E, padx=25, pady=(10, 3))
        printer_var = StringVar(value=default_printer or (printers[0] if printers else 'اختيار الطابعة من عارض PDF'))
        values = printers or ['اختيار الطابعة من عارض PDF']
        combo = ttk.Combobox(win, textvariable=printer_var, values=values, state='readonly', justify='right')
        combo.pack(fill=X, padx=25, pady=4)
        note = 'سيبقى رقم الرخصة الأصلي كما هو، وستُسجّل إعادة الطباعة في سجل التدقيق.'
        ttk.Label(win, text=note, wraplength=500, style='FormCaption.TLabel').pack(pady=7)
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

    def _mark_exit_executed(self, record_id, parent=None):
        try:
            core.mark_exit_executed(record_id, self.current_user['id'])
        except USER_ERRORS as exc:
            show_error(parent or self.root, 'تعذر تسجيل تنفيذ الخروج', exc)
            return
        self.refresh_records()
        if parent is not None and parent.winfo_exists():
            parent.destroy()
        self.show_record(record_id)

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
    def manage_teachers(self):
        """Administer the reusable teacher/subject directory and preview Excel imports safely."""
        if self.current_user['role'] != 'admin':
            messagebox.showwarning('صلاحيات غير كافية', 'إدارة بيانات الأساتذة متاحة لمدير النظام فقط.', parent=self.root)
            return
        win = Toplevel(self.root)
        configure_window(win, 'إدارة الأساتذة والمواد — SchoolPass', '980x650')
        win.minsize(780, 540)
        win.transient(self.root)
        ttk.Label(win, text='إدارة الأساتذة والمواد الدراسية', style='Title.TLabel').pack(pady=(12, 6))
        ttk.Label(win, text='أضف الأستاذ يدويًا، أو نزّل النموذج واملأه ثم استورده مع معاينة وكشف التكرارات قبل الحفظ.',
                  style='Hint.TLabel').pack(anchor=E, padx=18, pady=(0, 6))
        ttk.Label(win, text='الخطوات: تنزيل النموذج ← تعبئة الاسم والمادة ← استيراد الملف ومراجعة المعاينة.',
                  style='Hint.TLabel').pack(anchor=E, padx=18, pady=(0, 5))
        import_toolbar = ttk.Frame(win)
        import_toolbar.pack(fill=X, padx=14, pady=(0, 5))

        form = ttk.Frame(win, style='Surface.TFrame', padding=12)
        form.pack(fill=X, padx=14, pady=6)
        name_var, subject_var, classes_var = StringVar(), StringVar(), StringVar()
        selected = {'id': None, 'active': True}
        def field(parent, title, variable, width=32):
            row = ttk.Frame(parent); row.pack(fill=X, pady=4)
            ttk.Label(row, text=title, width=18, anchor=E).pack(side=RIGHT, padx=(4, 8))
            ttk.Entry(row, textvariable=variable, justify='right', width=width).pack(side=RIGHT, fill=X, expand=True)
        field(form, 'الاسم الكامل', name_var)
        field(form, 'المادة الدراسية', subject_var)
        field(form, 'الأقسام (اختياري)', classes_var)

        table_wrap = ttk.Frame(win)
        table_wrap.pack(fill=BOTH, expand=True, padx=14, pady=6)
        columns = ('name', 'subject', 'classes', 'status')
        tree = ttk.Treeview(table_wrap, columns=columns, show='headings', height=12)
        for col, title, width in (
            ('name', 'الأستاذ', 260), ('subject', 'المادة', 190),
            ('classes', 'الأقسام المسندة', 280), ('status', 'الحالة', 90),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=E, stretch=True)
        yscroll = ttk.Scrollbar(table_wrap, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True); yscroll.pack(side=RIGHT, fill=Y)

        def refresh():
            for iid in tree.get_children():
                tree.delete(iid)
            for row in core.list_teachers(active_only=False):
                tree.insert('', END, iid=str(row['id']),
                            values=(row['full_name'], row['subject'], row['classes'] or '—',
                                    'نشط' if row['active'] else 'غير نشط'))
        def clear_form():
            selected.update({'id': None, 'active': True})
            name_var.set(''); subject_var.set(''); classes_var.set('')
        def choose(_event=None):
            selection = tree.selection()
            if not selection:
                return
            row = core.get_teacher(int(selection[0]))
            if row:
                selected.update({'id': int(row['id']), 'active': bool(row['active'])})
                name_var.set(row['full_name']); subject_var.set(row['subject']); classes_var.set(row['classes'])
        tree.bind('<<TreeviewSelect>>', choose)

        def add():
            try:
                core.create_teacher_as(self.current_user['id'], name_var.get(), subject_var.get(), classes_var.get())
                messagebox.showinfo('تم الحفظ', 'تمت إضافة الأستاذ إلى القائمة.', parent=win)
                clear_form(); refresh()
            except USER_ERRORS as exc:
                show_error(win, 'تعذر إضافة الأستاذ', exc)
        def update():
            if not selected['id']:
                messagebox.showwarning('تحديد أستاذ', 'اختر أستاذًا من الجدول أولًا.', parent=win); return
            try:
                core.update_teacher_as(self.current_user['id'], selected['id'],
                                       name_var.get(), subject_var.get(), classes_var.get())
                messagebox.showinfo('تم التحديث', 'تم تحديث بيانات الأستاذ. تبقى بيانات الرخص السابقة محفوظة كما صدرت.', parent=win)
                clear_form(); refresh()
            except USER_ERRORS as exc:
                show_error(win, 'تعذر تحديث الأستاذ', exc)
        def toggle_active():
            if not selected['id']:
                messagebox.showwarning('تحديد أستاذ', 'اختر أستاذًا من الجدول أولًا.', parent=win); return
            new_active = not selected['active']
            try:
                core.set_teacher_active_as(self.current_user['id'], selected['id'], new_active)
                messagebox.showinfo('حالة الأستاذ', 'تم تفعيل الأستاذ.' if new_active else 'تم إيقاف الأستاذ؛ ستبقى سجلاته السابقة محفوظة.', parent=win)
                clear_form(); refresh()
            except USER_ERRORS as exc:
                show_error(win, 'تعذر تغيير حالة الأستاذ', exc)

        def download_template():
            target = filedialog.asksaveasfilename(
                parent=win, title='حفظ نموذج قائمة الأساتذة',
                defaultextension='.xlsx', initialfile='قالب_قائمة_الأساتذة.xlsx',
                filetypes=[('ملف Excel', '*.xlsx')])
            if not target:
                return
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Alignment, Font, PatternFill
                workbook = Workbook()
                sheet = workbook.active
                sheet.title = 'قائمة الأساتذة'
                sheet.append(['اسم الأستاذ', 'المادة', 'الأقسام'])
                for cell in sheet[1]:
                    cell.font = Font(name='Arial', bold=True, color='FFFFFF')
                    cell.fill = PatternFill('solid', fgColor='185A8D')
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                sheet.column_dimensions['A'].width = 34
                sheet.column_dimensions['B'].width = 24
                sheet.column_dimensions['C'].width = 38
                sheet.freeze_panes = 'A2'
                sheet.auto_filter.ref = 'A1:C1'
                sheet.row_dimensions[1].height = 24
                workbook.save(target)
                workbook.close()
                messagebox.showinfo(
                    'نموذج الأساتذة جاهز',
                    'تم حفظ النموذج. اكتب اسم الأستاذ والمادة في كل سطر، ويمكن ترك الأقسام فارغة، ثم اختر «استيراد ملف Excel».',
                    parent=win)
            except Exception as exc:
                show_error(win, 'تعذر إنشاء نموذج الأساتذة', exc)

        def import_excel():
            path = filedialog.askopenfilename(parent=win, title='اختيار ملف قائمة الأساتذة',
                filetypes=[('ملفات Excel', '*.xlsx *.xlsm'), ('ملفات CSV', '*.csv'), ('جميع الملفات', '*.*')])
            if not path:
                return
            try:
                preview = core.preview_teacher_import(path)
            except (USER_ERRORS + (ImportError,)) as exc:
                show_error(win, 'تعذر قراءة ملف الأساتذة', exc); return
            preview_win = Toplevel(win)
            configure_window(preview_win, 'مراجعة استيراد الأساتذة', '900x540')
            ttk.Label(preview_win, text='معاينة قبل الاستيراد', style='Title.TLabel').pack(pady=(10, 4))
            counts = preview['counts']
            ttk.Label(preview_win,
                text=f"الأسطر: {preview['total']}  |  جاهز: {counts['ready']}  |  موجود مسبقًا: {counts['existing']}  |  مكرر: {counts['duplicate_file']}  |  غير صالح: {counts['invalid']}",
                style='Badge.TLabel').pack(anchor=E, padx=12, pady=5)
            preview_tree = ttk.Treeview(preview_win, columns=('row','name','subject','classes','status'),
                                        show='headings', height=14)
            for col, title, width in (('row','السطر',55),('name','الأستاذ',205),('subject','المادة',140),
                                      ('classes','الأقسام',170),('status','نتيجة المراجعة',250)):
                preview_tree.heading(col,text=title); preview_tree.column(col,width=width,anchor=E)
            preview_tree.pack(fill=BOTH,expand=True,padx=12,pady=6)
            for item in preview['rows']:
                preview_tree.insert('',END,values=(item['row'],item['full_name'],item['subject'],
                                                   item['classes'] or '—',item['message']))
            def confirm_import():
                if not counts['ready']:
                    messagebox.showinfo('لا توجد إضافات', 'لم يتم العثور على صفوف جديدة صالحة للإضافة.', parent=preview_win)
                    return
                if not messagebox.askyesno('تأكيد الاستيراد',
                    f"سيتم إضافة {counts['ready']} أستاذًا جديدًا فقط. لن تُعدّل السجلات الموجودة ولن تُحذف أي بيانات. هل تتابع؟",
                    parent=preview_win):
                    return
                try:
                    result = core.import_teachers_as(path, self.current_user['id'])
                    messagebox.showinfo('اكتمل الاستيراد',
                        f"تمت إضافة {result['added']} أستاذًا. راجع الأسطر المكررة أو غير الصالحة في المعاينة.",
                        parent=preview_win)
                    preview_win.destroy(); refresh()
                except (USER_ERRORS + (ImportError,)) as exc:
                    show_error(preview_win, 'تعذر استيراد الأساتذة', exc)
            buttons = ttk.Frame(preview_win); buttons.pack(fill=X,padx=12,pady=8)
            ttk.Button(buttons,text='تأكيد إضافة الصفوف الجديدة',command=confirm_import,
                       style='Accent.TButton').pack(side=RIGHT,padx=4)
            ttk.Button(buttons,text='إلغاء',command=preview_win.destroy).pack(side=LEFT,padx=4)

        ttk.Button(import_toolbar, text='①  تنزيل نموذج Excel للأساتذة',
                   command=download_template, style='Gold.TButton').pack(side=RIGHT, padx=5, pady=2)
        ttk.Button(import_toolbar, text='②  استيراد ملف Excel ومراجعته',
                   command=import_excel, style='Accent.TButton').pack(side=RIGHT, padx=5, pady=2)
        buttons = ttk.Frame(win); buttons.pack(fill=X,padx=14,pady=(2,12))
        ttk.Button(buttons,text='إضافة أستاذ',command=add,style='Accent.TButton').pack(side=RIGHT,padx=4)
        ttk.Button(buttons,text='تحديث البيانات المحددة',command=update).pack(side=RIGHT,padx=4)
        ttk.Button(buttons,text='تفعيل / إيقاف',command=toggle_active).pack(side=RIGHT,padx=4)
        ttk.Button(buttons,text='تفريغ الحقول',command=clear_form).pack(side=LEFT,padx=4)
        ttk.Button(buttons,text='إغلاق',command=win.destroy).pack(side=LEFT,padx=4)
        refresh()

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

    def manage_guardians(self):
        if self.current_user['role'] != 'admin':
            messagebox.showwarning('صلاحيات محدودة', 'إدارة سجل الأولياء متاحة لمدير النظام فقط. يستطيع المشرف اختيار ولي مسجل أو إضافة ولي عند إصدار الرخصة.', parent=self.root)
            return
        win = Toplevel(self.root)
        configure_window(win, 'إدارة أولياء التلاميذ', '1160x660')
        win.transient(self.root)
        ttk.Label(win, text='سجل أولياء التلاميذ', style='Title.TLabel').pack(pady=(14, 4))
        ttk.Label(win, text='تُسجّل بيانات الولي من نموذج إصدار الرخصة، ويمكن من هنا مراجعة بياناته وتعديلها. رقم بطاقة التعريف مخفي في القائمة.',
                  wraplength=1050, justify='right').pack(fill=X, padx=18, pady=(0, 8))
        top = ttk.Frame(win); top.pack(fill=X, padx=14, pady=7)
        query = StringVar()
        ttk.Label(top, text='بحث بالاسم أو الهاتف أو آخر أربعة أرقام من الهوية:').pack(side=RIGHT, padx=5)
        search = ttk.Entry(top, textvariable=query, justify='right')
        search.pack(side=RIGHT, fill=X, expand=True)
        columns = ('name', 'id', 'phone', 'count', 'students')
        tree = ttk.Treeview(win, columns=columns, show='headings', height=18)
        for key, label, width in (
            ('name', 'اسم الولي', 210), ('id', 'رقم الهوية (مقنّع)', 150),
            ('phone', 'الهاتف', 130), ('count', 'عدد التلاميذ', 100),
            ('students', 'التلاميذ المرتبطون والصفة', 500),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor=E)
        tree.pack(fill=BOTH, expand=True, padx=14, pady=6)

        def refresh(*_):
            for child in tree.get_children():
                tree.delete(child)
            try:
                rows = core.list_guardians_as_admin(self.current_user['id'], query.get())
            except Exception as exc:
                show_error(win, 'تعذر تحميل سجل الأولياء', exc)
                return
            for item in rows:
                masked = '*' * max(4, int(item['national_id_length'] or 0) - 4) + item['national_id_last4']
                tree.insert('', END, iid=str(item['id']),
                            values=(item['full_name'], masked, item['phone'] or '—',
                                    item['student_count'], item['linked_students'] or 'لا يوجد ارتباط'))
        def edit_selected():
            selected = tree.selection()
            if not selected:
                messagebox.showinfo('اختر وليًا', 'حدد وليًا من القائمة أولًا.', parent=win)
                return
            guardian_id = int(selected[0])
            try:
                details = core.get_guardian_as_admin(self.current_user['id'], guardian_id)
            except Exception as exc:
                show_error(win, 'تعذر فتح بيانات الولي', exc)
                return
            dialog = Toplevel(win)
            configure_window(dialog, 'تعديل بيانات ولي', '560x410')
            dialog.transient(win); dialog.grab_set()
            ttk.Label(dialog, text='تعديل ملف الولي', style='Title.TLabel').pack(pady=(14, 7))
            form = ttk.Frame(dialog, padding=18); form.pack(fill=BOTH, expand=True)
            name_var, id_var, phone_var = (
                StringVar(value=details['full_name']),
                StringVar(value=details['national_id']),
                StringVar(value=details['phone'] or ''),
            )
            for label, variable in (('الاسم الكامل', name_var), ('رقم بطاقة التعريف', id_var), ('رقم الهاتف', phone_var)):
                row = ttk.Frame(form); row.pack(fill=X, pady=7)
                ttk.Label(row, text=label, width=22, anchor=E).pack(side=RIGHT, padx=(4, 8))
                ttk.Entry(row, textvariable=variable, justify='right').pack(side=RIGHT, fill=X, expand=True)
            ttk.Label(form, text='يُسجل عرض رقم الهوية الكامل في سجل التدقيق. اترك الرقم كما هو إن لم ترغب بتغييره.',
                      style='FormCaption.TLabel', wraplength=480, justify='right').pack(anchor=E, pady=6)
            def save():
                try:
                    core.update_guardian_as_admin(
                        self.current_user['id'], guardian_id, name_var.get(), phone_var.get(), id_var.get())
                except Exception as exc:
                    show_error(dialog, 'تعذر تعديل بيانات الولي', exc)
                    return
                dialog.destroy()
                refresh()
                messagebox.showinfo('تم التعديل', 'حُفظت بيانات الولي وسُجل التعديل في سجل التدقيق.', parent=win)
            bar = ttk.Frame(dialog); bar.pack(fill=X, padx=18, pady=(0, 14))
            ttk.Button(bar, text='حفظ التعديلات', command=save, style='Accent.TButton').pack(side=RIGHT, padx=4)
            ttk.Button(bar, text='إلغاء', command=dialog.destroy).pack(side=RIGHT, padx=4)

        bar = ttk.Frame(win); bar.pack(fill=X, padx=14, pady=(0, 12))
        ttk.Button(bar, text='تعديل بيانات الولي المحدد', command=edit_selected,
                   style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='تحديث القائمة', command=refresh).pack(side=RIGHT, padx=4)
        ttk.Button(bar, text='إغلاق', command=win.destroy).pack(side=LEFT, padx=4)
        query.trace_add('write', refresh)
        tree.bind('<Double-1>', lambda _e: edit_selected())
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
        notes = core.list_student_notes(student['student_key'], self.current_user['id'])
        total_absence_days = 0
        late_count = 0
        return_after_absence = 0
        exit_count = 0
        open_exit_count = 0
        special_entry_count = 0
        for r in records:
            if r['status'] == 'cancelled':
                continue
            if r['kind'] == 'late': late_count += 1
            elif r['kind'] == 'absence_return':
                return_after_absence += 1
                try: total_absence_days += int(json.loads(r['details_json'] or '{}').get('days_count', 0) or 0)
                except (ValueError, TypeError): pass
            elif r['kind'] == 'special_entry':
                special_entry_count += 1
            elif r['kind'] == 'exit_permit':
                exit_count += 1
                if r['status'] == 'valid': open_exit_count += 1
        win = Toplevel(self.root)
        win.title('الملف الشامل للتلميذ')
        win.geometry('1120x760')
        win.minsize(850, 600)
        win.transient(self.root)
        ttk.Label(win, text=student_name(student), style='Title.TLabel').pack(pady=(14, 3))
        ttk.Label(win, text=f"رقم التعريف: {student['student_key']}   |   القسم: {student['class_name']}   |   الحالة: {'نشط' if student['active'] else 'غير نشط'}").pack(pady=(0, 8))
        cards = ttk.Frame(win); cards.pack(fill=X, padx=12, pady=6)
        metrics = [('عدد العمليات', len(records)), ('مرات التأخر', late_count), ('العودة بعد الغياب', return_after_absence),
                   ('دخول خاص (طرد / إدارة)', special_entry_count), ('أيام الغياب المدخلة', total_absence_days),
                   ('تصاريح الخروج', exit_count), ('خروج دون عودة', open_exit_count),
                   ('ملاحظات إدارية', len(notes))]
        for title, val in metrics:
            box = ttk.Frame(cards, style='Surface.TFrame', padding=(8, 7)); box.pack(side=RIGHT, fill=X, expand=True, padx=3)
            ttk.Label(box, text=title, style='CardTitle.TLabel').pack(anchor=E)
            ttk.Label(box, text=str(val), style='CardValue.TLabel').pack(anchor=E)
        notes_box = ttk.Frame(win, style='FormSection.TFrame', padding=(12, 9))
        notes_box.pack(fill=X, padx=14, pady=(5, 7))
        ttk.Label(notes_box, text='الملاحظات الإدارية السابقة', style='FormSectionTitle.TLabel').pack(anchor=E, fill=X, pady=(0, 6))
        notes_text = tk.Text(
            notes_box, height=3, wrap='word', font=('Segoe UI', 9), bd=0,
            background=UI_PALETTE.get('field', '#EAF1FA'),
            foreground=UI_PALETTE.get('ink', '#17263D'),
            insertbackground=UI_PALETTE.get('primary', '#185A8D'),
            highlightthickness=2,
            highlightbackground=UI_PALETTE.get('border', '#AEBFD4'),
            highlightcolor=UI_PALETTE.get('primary', '#185A8D'),
            relief='flat', padx=10, pady=8,
        )
        notes_scroll = ttk.Scrollbar(notes_box, command=notes_text.yview)
        notes_text.configure(yscrollcommand=notes_scroll.set)
        notes_text.pack(side=LEFT, fill=X, expand=True, padx=5, pady=4)
        notes_scroll.pack(side=RIGHT, fill=Y, pady=4)
        def refresh_notes():
            current = core.list_student_notes(student['student_key'], self.current_user['id'])
            notes_text.configure(state='normal')
            notes_text.delete('1.0', END)
            if not current:
                notes_text.insert(END, 'لا توجد ملاحظات إدارية مسجلة لهذا التلميذ.')
            else:
                for item in current:
                    notes_text.insert(END, f"{item['created_at']} — {item['supervisor']}\n{item['note']}\n\n")
            notes_text.configure(state='disabled')
        def add_note():
            value = ask_text(win, 'إضافة ملاحظة إدارية', 'اكتب ملاحظة إدارية موجزة (حتى 500 حرف):')
            if not value:
                return
            try:
                core.add_student_note(student['student_key'], self.current_user['id'], value)
            except USER_ERRORS as exc:
                show_error(win, 'تعذر حفظ الملاحظة', exc)
                return
            refresh_notes()
            messagebox.showinfo('تم الحفظ', 'أُضيفت الملاحظة إلى السجل الدائم للتلميذ.', parent=win)
        ttk.Button(notes_box, text='＋ إضافة ملاحظة', command=add_note, style='Gold.TButton').pack(side=RIGHT, padx=7, pady=4)
        refresh_notes()
        ttk.Label(win, text='السجل الزمني الكامل لرخص الدخول والخروج والإلغاءات وإثباتات العودة', style='Section.TLabel').pack(anchor=E, padx=16, pady=(8, 4))
        cols = ('record_no', 'kind', 'created_at', 'reason', 'status', 'supervisor')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        heads = [('record_no','رقم الرخصة',135),('kind','نوع العملية',195),('created_at','التاريخ والوقت',155),('reason','السبب / الملاحظة',260),('status','الحالة',85),('supervisor','المشرف',130)]
        for col,title,width in heads:
            tree.heading(col,text=title); tree.column(col,width=width,anchor=E)
        self._apply_record_tree_tags(tree)
        history_wrap = ttk.Frame(win)
        history_wrap.pack(fill=BOTH, expand=True, padx=12, pady=6)
        history_y = ttk.Scrollbar(history_wrap, orient='vertical', command=tree.yview)
        history_x = ttk.Scrollbar(history_wrap, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=history_y.set, xscrollcommand=history_x.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        history_y.pack(side=RIGHT, fill=Y)
        history_x.pack(side='bottom', fill=X)
        for r in records:
            typetag = 'late' if r['kind']=='late' else ('absence' if r['kind']=='absence_return' else ('special' if r['kind']=='special_entry' else 'exit'))
            tags = ('cancelled',) if r['status']=='cancelled' else (typetag,)
            tree.insert('', END, iid=str(r['id']), values=(r['record_no'], kind_label(r['kind']), r['issued_at'] or r['created_at'], r['reason'] or '—', status_label(r['status']), r['supervisor'] or '—'), tags=tags)
        tree.bind('<Double-1>', lambda _e: self.show_record(int(tree.selection()[0])) if tree.selection() else None)
        ttk.Label(win, text='مدة الغياب التراكمية محسوبة من فترات العودة بعد الغياب التي أدخلها المشرف يدويًا، وليست سجل غياب يوميًا تلقائيًا.', wraplength=1000).pack(pady=(4, 9))
        bar = ttk.Frame(win); bar.pack(fill=X, padx=12, pady=(0, 12))
        ttk.Button(bar, text='عرض تفاصيل العملية المحددة', command=lambda: self.show_record(int(tree.selection()[0])) if tree.selection() else messagebox.showinfo('اختيار', 'حدد عملية أولًا.', parent=win), style='Accent.TButton').pack(side=RIGHT, padx=4)
        def print_student_profile():
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_key = re.sub(r'[^A-Za-z0-9_-]+', '_', str(student['student_key']))[:40] or 'student'
            path = core.DATA_DIR / 'prints' / f"SchoolPass_Student_{safe_key}_{stamp}.pdf"
            try:
                out = core.create_student_history_report_pdf(path, student_id, self.current_user['id'])
            except (USER_ERRORS + (ImportError,)) as exc:
                show_error(win, 'تعذر إنشاء تقرير التلميذ', exc)
                return
            self._show_print_options(out, win, title='الملف التاريخي للتلميذ — معاينة وطباعة')
        ttk.Button(bar, text='حفظ / طباعة الملف التاريخي', command=print_student_profile, style='Gold.TButton').pack(side=RIGHT, padx=4)
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
        kinds = {'late': 'رخصة الدخول — تأخر', 'absence_return': 'رخصة الدخول — عودة بعد الغياب',
                 'special_entry': 'رخصة الدخول — حالة خاصة', 'exit_permit': 'رخصة خروج استثنائي',
                 'exit_return': 'إثبات عودة من خروج استثنائي'}
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
        self._apply_record_tree_tags(tree)
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
                tag = 'late' if r['kind']=='late' else ('absence' if r['kind']=='absence_return' else ('special' if r['kind']=='special_entry' else 'exit'))
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
            self._show_print_options(out, win, title='تقرير سجل العمليات المصفّى')
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
        self._show_print_options(out, self.root, title='التقرير الشهري — معاينة وطباعة')

    def archive_reports(self):
        """Select a month or full year and create a shareable, printable PDF archive."""
        win=Toplevel(self.root)
        win.title('الأرشيف الشهري والسنوي')
        win.geometry('540x300')
        win.transient(self.root)
        ttk.Label(win,text='اختيار فترة الأرشيف',style='Title.TLabel').pack(pady=(18,10))
        today=date.today()
        years=[str(y) for y in range(today.year,max(2000,today.year-15)-1,-1)]
        year_var=ComboStringVar(years,value=str(today.year))
        month_names=['جانفي','فيفري','مارس','أفريل','ماي','جوان','جويلية','أوت','سبتمبر','أكتوبر','نوفمبر','ديسمبر']
        month_options=['كل السنة']+month_names
        month_var=ComboStringVar(month_options,value=month_names[today.month-1])
        row=ttk.Frame(win);row.pack(fill=X,padx=24,pady=9)
        ttk.Label(row,text='السنة:',width=12,anchor=E).pack(side=RIGHT,padx=4)
        ttk.Combobox(row,textvariable=year_var,values=years,state='readonly',width=12,justify='center').pack(side=RIGHT,padx=4)
        ttk.Label(row,text='الشهر:',width=12,anchor=E).pack(side=RIGHT,padx=4)
        ttk.Combobox(row,textvariable=month_var,values=month_options,state='readonly',width=16,justify='right').pack(side=RIGHT,padx=4)
        ttk.Label(win,text='ينشئ التقرير PDF قابلًا للحفظ والطباعة والمشاركة، ويشمل السجلات والإحصاءات.',
                  wraplength=470,justify='right').pack(fill=X,padx=26,pady=10)
        def generate():
            year=int(year_var.get());selected=month_var.get()
            month=month_names.index(selected)+1 if selected in month_names else None
            initial=f"SchoolPass_Archive_{year}_{month:02d}.pdf" if month else f"SchoolPass_Annual_Archive_{year}.pdf"
            path=filedialog.asksaveasfilename(parent=win,title='حفظ أرشيف PDF',initialfile=initial,
                defaultextension='.pdf',filetypes=[('تقرير PDF','*.pdf')])
            if not path:return
            try:
                out=(core.create_monthly_report_pdf(path,year,month,self.current_user['id'])
                     if month else core.create_annual_report_pdf(path,year,self.current_user['id']))
            except Exception as exc:
                show_error(win,'تعذر إنشاء الأرشيف',exc);return
            self._show_print_options(out,win,title=f'الأرشيف {selected} — {year}')
        bar=ttk.Frame(win);bar.pack(fill=X,padx=20,pady=12)
        ttk.Button(bar,text='إنشاء ملف PDF',command=generate,style='Accent.TButton').pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إغلاق',command=win.destroy).pack(side=RIGHT,padx=4)

    def open_expulsion_report(self):
        """Browse and export class-expulsion records by teacher and date interval."""
        today_text = date_today()
        today_value = date.fromisoformat(today_text)
        start_var = StringVar(value=today_value.replace(day=1).isoformat())
        end_var = StringVar(value=today_text)
        win = Toplevel(self.root)
        win.title('سجل التلاميذ المطرودين — الطرد من الحصة')
        win.geometry('1180x690')
        win.minsize(900, 560)
        win.transient(self.root)
        ttk.Label(win, text='سجل التلاميذ المطرودين حسب الأستاذ', style='Title.TLabel').pack(pady=(14, 6))

        filters = ttk.Frame(win)
        filters.pack(fill=X, padx=16, pady=8)
        def date_control(parent, caption, variable):
            group = ttk.Frame(parent)
            group.pack(side=RIGHT, padx=5)
            ttk.Label(group, text=caption).pack(side=RIGHT, padx=4)
            entry = ttk.Entry(group, textvariable=variable, state='readonly', width=12, justify='center')
            entry.pack(side=RIGHT, padx=2)
            DatePicker(group, variable).pack(side=RIGHT, padx=2)
        date_control(filters, 'من تاريخ:', start_var)
        date_control(filters, 'إلى تاريخ:', end_var)

        teacher_names = set()
        with db_connect() as conn:
            stored = conn.execute("SELECT details_json FROM records WHERE kind='special_entry'").fetchall()
        for item in stored:
            try:
                details = json.loads(item['details_json'] or '{}')
            except (TypeError, json.JSONDecodeError):
                continue
            if details.get('entry_reason') == 'class_expulsion':
                name = str(details.get('expelling_teacher') or '').strip()
                if name:
                    teacher_names.add(name)
        teacher_names.update(str(row['full_name']) for row in core.list_teachers(active_only=False))
        teacher_values = ['الكل'] + sorted(teacher_names, key=str.casefold)
        teacher_var = ComboStringVar(teacher_values, value='الكل')
        ttk.Label(filters, text='الأستاذ:').pack(side=RIGHT, padx=(12, 3))
        ttk.Combobox(filters, textvariable=teacher_var, values=teacher_values, state='readonly', width=28, justify='right').pack(side=RIGHT, padx=3)

        summary = DisplayStringVar(value='حدد الفترة والأستاذ ثم اضغط عرض النتائج.')
        ttk.Label(win, textvariable=summary, style='Badge.TLabel').pack(anchor=E, padx=18, pady=(2, 6))
        columns = ('issued', 'teacher', 'subject', 'student', 'class_name', 'supervisor', 'record_no')
        tree = ttk.Treeview(win, columns=columns, show='headings')
        headings = [('issued', 'تاريخ ووقت الطرد', 155), ('teacher', 'الأستاذ', 175), ('subject', 'المادة', 120),
                    ('student', 'التلميذ', 185), ('class_name', 'القسم', 140), ('supervisor', 'المشرف الذي أصدر الرخصة', 175),
                    ('record_no', 'رقم الرخصة', 130)]
        for col, title, width in headings:
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=E, stretch=True)
        tree.pack(fill=BOTH, expand=True, padx=14, pady=8)

        state = {'report': None}
        def refresh():
            try:
                report = core.get_expulsion_report(start_var.get(), end_var.get(), teacher_var.get())
            except USER_ERRORS as exc:
                messagebox.showwarning('تحقق من الفترة', str(exc), parent=win)
                return
            state['report'] = report
            for item_id in tree.get_children():
                tree.delete(item_id)
            for row in report['records']:
                tree.insert('', END, iid=str(row['id']), values=(row['issued_at_text'], row['expelling_teacher'],
                    row['subject'], row['student_name'], row['class_name'], row.get('supervisor') or '—', row['record_no']))
            distinct = len({row['student_key'] for row in report['records']})
            summary.set(f"الفترة: {report['start_date']} ← {report['end_date']}  |  الأستاذ: {report['teacher']}  |  عدد حالات الطرد: {report['total']}  |  عدد التلاميذ المختلفين: {distinct}")

        def export_pdf():
            if not state['report']:
                refresh()
                if not state['report']:
                    return
            initial = f"SchoolPass_Expulsions_{start_var.get()}_{end_var.get()}.pdf"
            path = filedialog.asksaveasfilename(parent=win, title='حفظ تقرير الطرد حسب الأستاذ',
                initialfile=initial, defaultextension='.pdf', filetypes=[('PDF', '*.pdf')])
            if not path:
                return
            try:
                out = core.create_expulsion_report_pdf(path, start_var.get(), end_var.get(), teacher_var.get(), self.current_user['id'])
            except (USER_ERRORS + (ImportError,)) as exc:
                show_error(win, 'تعذر إنشاء تقرير الطرد', exc)
                return
            self._show_print_options(out, win, title='تقرير حالات الطرد حسب الأستاذ — معاينة وطباعة')

        def print_expulsion_report():
            if not state['report']:
                refresh()
                if not state['report']:
                    return
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = core.DATA_DIR / 'prints' / f'SchoolPass_Expulsions_{start_var.get()}_{end_var.get()}_{stamp}.pdf'
            try:
                out = core.create_expulsion_report_pdf(path, start_var.get(), end_var.get(), teacher_var.get(), self.current_user['id'])
            except (USER_ERRORS + (ImportError,)) as exc:
                show_error(win, 'تعذر إنشاء تقرير الطرد', exc)
                return
            self._show_print_options(out, win, title='تقرير الطرد حسب الأستاذ')

        buttons = ttk.Frame(win)
        buttons.pack(fill=X, padx=14, pady=(0, 12))
        ttk.Button(buttons, text='عرض النتائج', command=refresh, style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(buttons, text='حفظ تقرير PDF', command=export_pdf).pack(side=RIGHT, padx=4)
        ttk.Button(buttons, text='إنشاء وطباعة التقرير', command=print_expulsion_report, style='Gold.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(buttons, text='إغلاق', command=win.destroy).pack(side=LEFT, padx=4)
        refresh()

    def print_statistics_report(self):
        # Create a comprehensive PDF from the same stats displayed in the dashboard.
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = core.DATA_DIR / 'prints' / f'SchoolPass_Statistics_{date_today()}_{stamp}.pdf'
        try:
            out = core.create_statistics_report_pdf(path, self.current_user['id'])
        except (USER_ERRORS + (ImportError,)) as exc:
            show_error(self.root, 'تعذر إنشاء تقرير الإحصائيات', exc)
            return
        self._show_print_options(out, self.root, title='تقرير الإحصائيات الشامل — معاينة وطباعة')

    def show_stats(self):
        win = Toplevel(self.root)
        win.title('الإحصائيات والرسوم البيانية')
        win.geometry('1050x760')
        win.minsize(820, 600)
        win.transient(self.root)
        ttk.Label(win, text='لوحة الإحصائيات والتقارير', style='Title.TLabel').pack(pady=(14, 5))
        controls = ttk.Frame(win); controls.pack(fill=X,padx=18,pady=5)
        period = ComboStringVar(('اليوم', 'الأسبوع', 'الشهر'), value='الأسبوع')
        ttk.Label(controls,text='الفترة البيانية:').pack(side=RIGHT,padx=5)
        combo=ttk.Combobox(controls,textvariable=period,state='readonly',values=('اليوم','الأسبوع','الشهر'),width=12,justify='right')
        combo.pack(side=RIGHT,padx=5)
        chart=Canvas(win, height=220, background=UI_PALETTE.get('canvas', '#FFFFFF'),
                      highlightthickness=1, highlightbackground=UI_PALETTE.get('border', '#C5D2E2'),
                      highlightcolor=UI_PALETTE.get('primary', '#185A8D'))
        chart.pack(fill=X,padx=18,pady=8)
        def draw_chart(_e=None):
            data=core.dashboard_series({'اليوم':'day','الأسبوع':'week','الشهر':'month'}.get(period.get(),'week'))
            chart.delete('all'); w=max(600,chart.winfo_width()); h=max(180,chart.winfo_height())
            l,r,t,b=45,w-16,14,h-34; chart.create_line(l,b,r,b,fill=UI_PALETTE.get('border', '#C5D2E2'))
            series=[('late','تأخر','#D7A33B'),('absence_return','عودة بعد غياب','#4A89B8'),
                    ('special_entry','دخول خاص','#B85A45'),('exit_permit','خروج','#2D8062'),('exit_return','إثبات عودة','#8A78B7')]
            mx=max([1]+[n for arr in data['series'].values() for n in arr]); gw=(r-l)/max(1,len(data['labels'])); bw=max(2,min(10,gw/5))
            for tick in range(4):
                y=b-(b-t)*tick/3; chart.create_line(l,y,r,y,fill=UI_PALETTE.get('border', '#C5D2E2')); chart.create_text(l-8,y,text=str(round(mx*tick/3)),anchor='e',font=('Segoe UI',8),fill=UI_PALETTE.get('muted', '#687A91'))
            for i,lab in enumerate(data['labels']):
                cx=l+i*gw+gw/2
                for j,(key,_name,color) in enumerate(series):
                    n=data['series'][key][i]; bh=(b-t)*n/mx
                    if n: chart.create_rectangle(cx+(j-1.5)*bw,b-bh,cx+(j-1.5)*bw+bw-1,b,fill=color,outline='')
                if len(data['labels'])<=10 or i%5==0 or i==len(data['labels'])-1: chart.create_text(cx,b+13,text=lab,font=('Segoe UI',7),fill=UI_PALETTE.get('muted', '#687A91'))
            x=r-5
            for _k,label,color in reversed(series):
                chart.create_rectangle(x-88,3,x-80,11,fill=color,outline=''); chart.create_text(x-92,7,text=label,anchor='e',font=('Segoe UI',8),fill=UI_PALETTE.get('ink', '#17263D')); x-=110
        combo.bind('<<ComboboxSelected>>',draw_chart)
        frame=ttk.Frame(win);frame.pack(fill=BOTH,expand=True,padx=18,pady=8)
        text_widget=__import__('tkinter').Text(
            frame, wrap='word', font=('Segoe UI', 10), height=18,
            background=UI_PALETTE.get('field', '#FFFFFF'),
            foreground=UI_PALETTE.get('ink', '#17263C'),
            insertbackground=UI_PALETTE.get('ink', '#17263C'),
            highlightthickness=2,
            highlightbackground=UI_PALETTE.get('border', '#E4EAF2'),
            highlightcolor=UI_PALETTE.get('primary', '#185A8D'),
            bd=0, relief='flat', padx=12, pady=10,
        )
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
                ('حالات الطرد حسب الأستاذ هذا الشهر',[f"{r['teacher']}: {r['count']} حالة — {r.get('student_count', len(r['students']))} تلميذًا مختلفًا" for r in st['expulsion_by_teacher']]),
                ('أكثر أسباب العمليات',[f"{kind_label(r['kind'])} — {r['reason']}: {r['n']}" for r in st['by_reason']]),
                ('فترات الغياب المدخلة يدويًا',[f"{r['student_name']} — {r['class_name']}: {r['n']} فترة، {r['days']} يومًا" for r in st['absence_totals']])]
            for title,lines in sections:
                text_widget.insert(END,rtl_display(title+'\n'+'—'*40+'\n'))
                text_widget.insert(END,rtl_display(('\n'.join(lines) if lines else 'لا توجد بيانات.')+'\n\n'))
            text_widget.configure(state='disabled')
        bar=ttk.Frame(win);bar.pack(fill=X,padx=18,pady=(0,12))
        ttk.Button(bar,text='تحديث الإحصائيات',command=refresh_text).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='تقرير الطرد حسب الأستاذ',command=self.open_expulsion_report).pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='إنشاء التقرير الشهري PDF',command=self.monthly_report,style='TButton').pack(side=RIGHT,padx=4)
        ttk.Button(bar,text='طباعة تقرير الإحصائيات الكامل',command=self.print_statistics_report,style='Accent.TButton').pack(side=RIGHT,padx=4)
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
        win.geometry('760x720')
        win.transient(self.root)
        ttk.Label(win, text='إعدادات المؤسسة', style='Title.TLabel').pack(pady=(12, 6))
        settings_scroll = ScrollableFrame(win, inner_style='TFrame')
        settings_scroll.pack(fill=BOTH, expand=True, padx=12, pady=4)
        form = settings_scroll.inner
        name = StringVar(value=setting('school_name', SCHOOL_NAME))
        primary_saved, secondary_saved = core.get_theme_colors()
        primary = StringVar(value=primary_saved)
        secondary = StringVar(value=secondary_saved)
        backup_path = StringVar(value=core.setting('external_backup_path', ''))
        late_threshold = StringVar(value=core.setting('late_alert_threshold', '3'))
        rtl_enabled = BooleanVar(value=True)
        ttk.Label(form, text='اسم المؤسسة على الوصول والتصاريح:').pack(anchor=E)
        ttk.Entry(form, textvariable=name, justify='right').pack(fill=X, pady=6)
        ttk.Label(form, text='رمز QR مستقل عن الإنترنت', style='Title.TLabel').pack(anchor=E, pady=(8, 3))
        ttk.Label(form, text='يعرض الرمز رقم الرخصة ونوعها ووقت إصدارها وموعد انتهاء الساعتين. لا يمكنه إظهار إلغاء تم بعد الطباعة؛ تُراجع حالة الإلغاء من سجل المؤسسة المحلي.', style='Info.TLabel', wraplength=650, justify='right').pack(anchor=E, fill=X, pady=(3, 10))
        ttk.Label(form, text='طريقة الطباعة: فتح ملف PDF للمعاينة قبل الطباعة (A6 و100%).', style='SubTitle.TLabel').pack(anchor=E, pady=(10, 0))
        ttk.Label(form, text='ألوان الواجهة (يمكن تغييرها في أي وقت):', style='Title.TLabel').pack(anchor=E, pady=(14, 5))
        color_row = ttk.Frame(form)
        color_row.pack(fill=X, pady=3)
        primary_preview = ttk.Label(color_row, text='اللون الرئيسي', width=18, anchor=E)
        primary_preview.pack(side=RIGHT, padx=5)
        primary_value = ttk.Label(color_row, textvariable=primary, width=12, relief='flat', anchor='center', style='ThemePrimary.TLabel')
        primary_value.pack(side=RIGHT, padx=5)
        secondary_preview = ttk.Label(color_row, text='اللون الثانوي', width=18, anchor=E)
        secondary_preview.pack(side=RIGHT, padx=(20, 5))
        secondary_value = ttk.Label(color_row, textvariable=secondary, width=12, relief='flat', anchor='center', style='ThemeSecondary.TLabel')
        secondary_value.pack(side=RIGHT, padx=5)

        theme_style = ttk.Style(win)
        def readable_foreground(value):
            return core.text_color_for(value)

        def paint_swatch(style_name, value):
            theme_style.configure(style_name, background=value, foreground=readable_foreground(value),
                                  padding=(10, 7), borderwidth=1, relief='solid')

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
        ttk.Label(form, text='يُعرض النص العربي تلقائيًا بالاتجاه الصحيح دون إضافة حركات صوتية أو معالجة مكررة.', style='Subtitle.TLabel', wraplength=650).pack(anchor=E, pady=4)
        backup_row = ttk.Frame(form); backup_row.pack(fill=X, pady=(10, 3))
        ttk.Label(backup_row, text='مجلد نسخة احتياطية خارجي (USB أو قرص آخر):').pack(anchor=E)
        ttk.Entry(backup_row, textvariable=backup_path, justify='left').pack(side=LEFT, fill=X, expand=True, padx=4)
        def pick_backup_dir():
            selected = filedialog.askdirectory(parent=win, title='اختيار مجلد النسخ الاحتياطي الخارجي')
            if selected: backup_path.set(selected)
        ttk.Button(backup_row, text='اختيار مجلد', command=pick_backup_dir).pack(side=RIGHT)
        def save():
            try:
                core.save_preferences_as(self.current_user['id'], name.get(), '', primary.get(), secondary.get(), late_threshold.get(), backup_path.get(), rtl_enabled.get())
            except USER_ERRORS as exc:
                show_error(win, 'تعذر الحفظ', exc)
                return
            global RTL_SHAPING_ENABLED
            RTL_SHAPING_ENABLED = True
            make_style(self.root, primary.get(), secondary.get())
            win.destroy()
            messagebox.showinfo('تم', 'حُفظت إعدادات المؤسسة وألوان الواجهة والنسخ الاحتياطي.', parent=self.root)

        def new_code():
            if messagebox.askyesno('رمز استرداد جديد', 'سيصبح الرمز القديم غير صالح. متابعة؟', parent=win):
                try:
                    code = core.issue_recovery_code_as(self.current_user['id'])
                except USER_ERRORS as exc:
                    show_error(win, 'تعذر', exc)
                    return
                show_recovery_code(win, code)
        settings_actions = ttk.Frame(win)
        settings_actions.pack(fill=X, padx=12, pady=(4, 9))
        ttk.Button(settings_actions, text='حفظ الإعدادات', command=save, style='Accent.TButton').pack(side=RIGHT, padx=4)
        ttk.Button(settings_actions, text='إنشاء رمز استرداد جديد للمدير', command=new_code).pack(side=RIGHT, padx=4)

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
    apply_application_icon(root)
    # Keep a real, visible Tk owner window during startup. In 0.5.6 the root
    # was withdrawn before creating transient login/setup Toplevels; on some
    # Windows/Tk builds those dialogs never became visible, leaving a silent
    # background process with no MainWindowHandle.
    root.title('SchoolPass 4.0 — جاري التشغيل')
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
    core.set_setting('rtl_shaping', '1')
    startup_log('database initialized')
    global RTL_SHAPING_ENABLED
    RTL_SHAPING_ENABLED = True
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
