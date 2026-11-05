from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_input_fields_have_visible_tinted_surface_and_stronger_border():
    assert "'border': '#B8C7D9', 'field': '#F1F5FB'" in APP
    assert "'field': '#18283D'" in APP
    assert "focus_border=primary" in APP
    assert "size, radius, border_width = 36, 14, 2" in APP


def test_dashboard_uses_compact_spacing_and_chart():
    assert "hero = ttk.Frame(content, style='Hero.TFrame', padding=(14, 9))" in APP
    assert "cards.pack(fill=X, padx=12, pady=(1, 5))" in APP
    assert "chart_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(12, 9))" in APP
    assert "self.dashboard_chart = Canvas(chart_panel, height=112" in APP
    assert "panel.pack(fill=BOTH, expand=True, padx=12, pady=(0, 10))" in APP


def test_entry_permits_dropdown_uses_plain_arabic_labels():
    assert "entry_toggle = nav('رخص الدخول'" in APP
    assert "('تأخر عن الدخول', 'late')" in APP
    assert "('عودة بعد الغياب', 'absence_return')" in APP
    assert "('الطرد من الحصة — حالة خاصة', 'special_entry')" in APP
    assert "entry_toggle.configure(text='＋   رخص الدخول" not in APP


def test_permit_dropdown_has_distinct_rounded_submenu_surface():
    assert "style.configure('SidebarSubmenu.TFrame'" in APP
    assert "style='SidebarSubmenu.TFrame', padding=(8, 6)" in APP
    assert "('SidebarSubmenu.TFrame', palette.get('sidebar_hover'" in APP
