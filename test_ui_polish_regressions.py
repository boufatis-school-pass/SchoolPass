from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_input_fields_have_visible_tinted_surface_and_stronger_border():
    assert "'gold': '#E5AC38', 'border': '#8499B2', 'field': '#F1F5FA'" in APP
    assert "'field': '#152A45'" in APP
    assert "focus_border=primary" in APP
    assert "size, radius, border_width = 36, 14, 2" in APP
    assert "style.configure(\n        'TEntry', padding=(10, 7), fieldbackground=palette['field']" in APP
    assert "('LoginBrand.TFrame', palette.get('sidebar'" in APP
    assert "('LoginSurface.TFrame', surface, border)" in APP


def test_dashboard_uses_compact_hierarchy_and_color_coded_kpis():
    assert "hero = ttk.Frame(content, style='Hero.TFrame', padding=(18, 14))" in APP
    assert "cards.pack(fill=X, padx=14, pady=(1, 7))" in APP
    assert "chart_panel = ttk.Frame(overview, style='Surface.TFrame', padding=(15, 12))" in APP
    assert "self.dashboard_chart = Canvas(chart_panel, height=112" in APP
    assert "panel = ttk.Frame(content, style='Surface.TFrame', padding=(16, 13))" in APP
    assert "overview.pack_configure(before=cards)" not in APP
    assert "KPIAccentBlue.TFrame" in APP and "KPIAccentRose.TFrame" in APP
    assert "MetricCardIconHover.TLabel" in APP


def test_buttons_use_native_stateful_rendering_without_image_assets():
    assert "def _install_rounded_button_surfaces(" not in APP
    assert "from ttkbootstrap import Assets, El, image_element, layout" not in APP
    assert "style.configure('TButton'" in APP
    assert "style.map('TButton'" in APP
    assert "style.configure('Accent.TButton'" in APP
    assert "style.configure('Success.TButton'" in APP and "style.configure('Danger.TButton'" in APP
    assert "('NavGroupOpsOpen.TButton'" in APP and "('NavActive.TButton'" in APP


def test_permit_form_has_rounded_section_cards_and_a_hierarchical_header():
    assert "style='DialogHeader.TFrame'" in APP
    assert "style='DialogHeaderTitle.TLabel'" in APP
    assert "style='DialogEyebrow.TLabel'" in APP
    assert "style='FormSection.TFrame'" in APP
    assert "style='FormSectionTitle.TLabel'" in APP
    assert "style='DialogFooter.TFrame'" in APP
    assert "relief='groove'" not in APP


def test_entry_permits_dropdown_uses_clean_arabic_and_open_state():
    assert "entry_toggle = nav('رخص الدخول'" in APP
    assert "('تأخر عن الدخول', 'late')" in APP
    assert "('عودة بعد الغياب', 'absence_return')" in APP
    assert "('الطرد من الحصة — حالة خاصة', 'special_entry')" in APP
    assert "entry_toggle.configure(style='NavActive.TButton')" in APP
    assert "toggle = ttk.Button(group, text=label," in APP
    assert "toggle.configure(text=label, style=_group_open_styles.get(key" in APP
    assert "entry_toggle.configure(text='＋   رخص الدخول" not in APP


def test_semantic_record_actions_and_status_bar_have_distinct_styles():
    assert "self.open_selected_record, 'Info.TButton'" in APP
    assert "self.return_selected_exit, 'Success.TButton'" in APP
    assert "self.cancel_selected, 'Danger.TButton'" in APP
    assert "style='StatusStrong.TLabel'" in APP


def test_dropdown_lists_follow_the_current_light_or_dark_palette():
    assert "root.option_add('*TCombobox*Listbox.background', palette['field'])" in APP
    assert "root.option_add('*Listbox.selectBackground', palette['primary'])" in APP
    assert "root.option_add('*Text.background', palette['field'])" in APP
    assert "background=UI_PALETTE.get('field', '#EAF1FA')" in APP


def test_semantic_buttons_keep_contrast_in_dark_mode():
    assert "palette['success_button'] = '#167B67' if dark else palette['good_fg']" in APP
    assert "palette['danger_button'] = '#A83C4C' if dark else palette['danger_fg']" in APP
    assert "('Success.TButton', palette['success_button']" in APP
    assert "('Danger.TButton', palette['danger_button']" in APP


def test_student_notes_and_admin_history_share_the_live_theme():
    assert "style='FormSection.TFrame'" in APP
    assert "background=UI_PALETTE.get('field', '#EAF1FA')" in APP
    assert "highlightbackground=UI_PALETTE.get('border', '#AEBFD4')" in APP
    assert "def _apply_record_tree_tags(self, tree):" in APP
    assert "self._apply_record_tree_tags(tree)" in APP


def test_statistics_chart_uses_theme_aware_surface_and_grid():
    assert "background=UI_PALETTE.get('canvas', '#FFFFFF')" in APP
    assert "highlightbackground=UI_PALETTE.get('border', '#C5D2E2')" in APP
    assert "fill=UI_PALETTE.get('muted', '#687A91')" in APP



def test_ttkbootstrap_style_uses_the_existing_default_root_and_exposes_scaling():
    assert "style = ttkb.Style(theme=target_theme)" in APP
    assert "ttkb.Style(root, theme=target_theme)" not in APP
    assert "root._schoolpass_theme_init_error" in APP
    assert "root._schoolpass_button_skin_count = _install_rounded_button_surfaces(root, style, palette)" not in APP


def test_dialogs_fit_their_contents_and_search_is_debounced():
    assert "requested_w = max(1, int(win.winfo_reqwidth()))" in APP
    assert "requested_h = max(1, int(win.winfo_reqheight()))" in APP
    assert "def _schedule_record_filter(self):" in APP
    assert "self.root.after(110, self._apply_record_filter)" in APP
    assert "def _schedule_student_suggestions(self, event=None):" in APP
    assert "def _schedule_teacher_suggestions(self, event=None):" in APP
    assert APP.count("self.root.after(500, self._tick_dashboard_clock)") == 1
    assert "self.root.after(1000, self.poll_qr_events)" not in APP


def test_windows_build_banner_uses_the_version_file_not_a_stale_literal():
    build = (ROOT / "build_windows.ps1").read_text(encoding="utf-8-sig")
    assert "$BuildVersion = (Get-Content -LiteralPath (Join-Path $Root 'VERSION.txt') -Raw).Trim()" in build
    assert 'اكتمل بناء SchoolPass $BuildVersion.' in build
    assert "اكتمل بناء SchoolPass 4.7.6." not in build
