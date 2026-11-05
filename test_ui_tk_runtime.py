"""Real Tk runtime checks for the rounded controls used by the Windows desktop UI.

These tests complement the headless smoke suite: they ask Tk itself to resolve the
installed element layouts and ensure the native button caption remains a child
of the rounded background. They are skipped only when a Windows Tk root cannot
be created (for example, on non-Windows developer hosts).
"""
from __future__ import annotations

import os
from unittest import mock

import pytest


def _elements(nodes):
    for element, options in nodes or ():
        yield str(element)
        yield from _elements((options or {}).get("children", ()))


def _has_button_label(nodes):
    return any(element == "Button.label" for element in _elements(nodes))


@pytest.mark.skipif(os.name != "nt", reason="The visual runtime contract is verified on the Windows build runner.")
def test_windows_tk_installs_rounded_buttons_entries_and_dropdowns():
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
        assert getattr(root, "_schoolpass_button_skin_count", 0) >= 6, (
            "Rounded button surfaces were not installed: "
            + " import_error=" + repr(getattr(root, "_schoolpass_button_skin_import_error", None))
            + " per_style_errors=" + repr(getattr(root, "_schoolpass_button_skin_errors", {}))
        )

        # Standard and semantic action buttons must use a rounded image as the
        # outer element and preserve Button.label as a nested native child.
        for style_name in (
            "TButton", "Accent.TButton", "Gold.TButton",
            "Success.TButton", "Danger.TButton", "Info.TButton",
        ):
            layout = style.layout(style_name)
            elements = tuple(_elements(layout))
            assert any("SchoolPassRounded_" in name for name in elements), style_name
            assert _has_button_label(layout), style_name

        entry_elements = tuple(_elements(style.layout("TEntry")))
        combo_elements = tuple(_elements(style.layout("TCombobox")))
        assert any("SchoolPassRounded_Entry_" in name for name in entry_elements)
        assert any("SchoolPassRounded_Combo_" in name for name in combo_elements)
        assert style.lookup("TEntry", "fieldbackground") == ui.UI_PALETTE["field"]

        # Switch to the dark palette through the same public theming path. The
        # field must remain distinct from the window and the custom elements
        # must be rebuilt without hiding the button text.
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
        for style_name in ("TButton", "Accent.TButton", "Success.TButton", "Danger.TButton"):
            layout = style.layout(style_name)
            assert any("SchoolPassRounded_" in name for name in _elements(layout)), style_name
            assert _has_button_label(layout), style_name
        assert style.lookup("TEntry", "fieldbackground") == ui.UI_PALETTE["field"]
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
