from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_dashboard_activity_chart_is_visible_by_default():
    # A regression previously hid the chart panel on startup, leaving the
    # dashboard overview with an empty-looking area.
    assert "self.stats_toggle_button.configure(text='−  إخفاء الإحصائيات والرسوم')" in APP
    assert "chart_panel.pack_forget()\n        overview.pack_configure(before=cards)" not in APP


def test_permit_dialog_uses_shared_window_configuration():
    start = APP.index("class RecordForm:")
    end = APP.index("\nclass ", start + 10)
    section = APP[start:end]
    assert "configure_window(" in section
    assert "'660x620' if kind == 'absence_return' else '620x520'" in section


def test_arabic_rendering_adapters_cover_core_tk_text_and_canvas():
    start = APP.index("def _install_rtl_widget_adapters")
    end = APP.index("_install_rtl_widget_adapters()", start + 100)
    section = APP[start:end]
    assert "ttk.Entry = ArabicAwareEntry" in section
    assert "ttk.Combobox = ArabicAwareCombobox" in section
    assert "ttk.Treeview = ArabicAwareTreeview" in section
    assert "tk.Text.insert = rtl_text_insert" in section
    assert "Canvas.create_text = rtl_canvas_create_text" in section
