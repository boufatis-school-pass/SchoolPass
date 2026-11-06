# -*- coding: utf-8 -*-
"""Regression tests for Arabic display and logical data handling in Tk widgets."""
import tkinter as tk
from tkinter import ttk

import app


def test_arabic_display_shapes_once_and_preserves_latin_text():
    logical = "رخصة الدخول رقم 123"
    visual = app.rtl_display(logical)
    assert visual != logical
    assert app.rtl_display(visual) == visual
    assert app.rtl_display("SchoolPass 123") == "SchoolPass 123"


def test_treeview_returns_logical_arabic_values_for_search_and_updates():
    root = tk.Tk()
    root.withdraw()
    try:
        tree = ttk.Treeview(root, columns=("student", "permit"), show="headings")
        tree.heading("student", text="التلميذ")
        iid = tree.insert("", "end", values=("محمد بن علي", "رخصة الدخول"))
        assert tree.set(iid, "student") == "محمد بن علي"
        assert tree.item(iid, "values") == ("محمد بن علي", "رخصة الدخول")
        assert tree.item(iid)["values"] == ("محمد بن علي", "رخصة الدخول")
        tree.set(iid, "student", "أحمد")
        assert tree.set(iid, "student") == "أحمد"
        tree.item(iid, values=("سليم", "رخصة خروج"))
        assert tree.item(iid, "values") == ("سليم", "رخصة خروج")
        tree.delete(iid)
        assert tree.get_children() == ()
    finally:
        root.destroy()


def test_combobox_keeps_logical_arabic_value():
    root = tk.Tk()
    root.withdraw()
    try:
        combo = ttk.Combobox(root, values=("تأخر", "عودة"), state="readonly")
        combo.set("تأخر")
        assert combo.get() == "تأخر"
        combo.set("عودة")
        assert combo.get() == "عودة"
    finally:
        root.destroy()
