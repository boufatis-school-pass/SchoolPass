"""Windows Tk runtime checks for crisp native buttons and rounded form fields."""
from __future__ import annotations

import os
from unittest import mock

import pytest


def _elements(nodes):
    for element, options in nodes or ():
        yield str(element)
        yield from _elements((options or {}).get("children", ()))


@pytest.mark.skipif(os.name != "nt", reason="The visual runtime contract is verified on the Windows build runner.")
def test_windows_tk_uses_native_buttons_and_rounded_entries():
    import tkinter as tk
    from tkinter import ttk

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"Windows runner has no usable Tk desktop: {exc}")

    try:
        import app as ui

        ui.make_style(root)
        style = ttk.Style(root)
        assert getattr(root, "_schoolpass_theme_init_error", None) is None, (
            "ttkbootstrap theme initialization failed: "
            + repr(getattr(root, "_schoolpass_theme_init_error", None))
        )

        # Buttons stay native: no large image assets are generated for every
        # button state. The ttk theme supplies normal, active and pressed states.
        for style_name in (
            "TButton", "Accent.TButton", "Gold.TButton",
            "Success.TButton", "Danger.TButton", "Info.TButton",
        ):
            layout = style.layout(style_name)
            assert layout, style_name
            assert not any("SchoolPassRounded_" in name for name in _elements(layout)), style_name
            assert style.lookup(style_name, "background") or style.lookup(style_name, "foreground"), style_name

        entry_elements = tuple(_elements(style.layout("TEntry")))
        combo_elements = tuple(_elements(style.layout("TCombobox")))
        assert any("SchoolPassRounded_Entry_" in name for name in entry_elements)
        assert any("SchoolPassRounded_Combo_" in name for name in combo_elements)
        assert style.lookup("TEntry", "fieldbackground") == ui.UI_PALETTE["field"]

        with mock.patch.object(
            ui.core, "setting",
            side_effect=lambda key, default=None: "dark" if key == "theme" else default,
        ), mock.patch.object(
            ui.core, "get_theme_colors",
            return_value=("#185A8D", "#F2F5F9"),
        ):
            ui.make_style(root)

        assert ui.UI_PALETTE["field"] == "#152A45"
        assert ui.UI_PALETTE["border"] == "#3B526F"
        assert style.lookup("TEntry", "fieldbackground") == ui.UI_PALETTE["field"]
        for style_name in ("TButton", "Accent.TButton", "Success.TButton", "Danger.TButton"):
            assert style.layout(style_name)
            assert not any("SchoolPassRounded_" in name for name in _elements(style.layout(style_name)))
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
