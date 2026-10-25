from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEW = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
VIEWER = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")


def _body(source, name, next_name):
    start = source.index("    def %s" % name)
    end = source.index("    def %s" % next_name, start)
    return source[start:end]


def test_outline_to_copper_does_not_insert_routing_section_into_layout():
    body = _body(PANEL, "_sync_routing_ui", "_sync_via_target_combo")
    assert "self.sec_routing.setVisible(in_2d)" in body
    assert "self.sec_routing.setEnabled(copper)" in body
    assert "setVisible(in_2d and copper)" not in body


def test_backtick_shortcut_matches_kicad_highlight_behavior():
    body = _body(PANEL, "_edit_action_for_key", "eventFilter")
    assert 'text == "`"' in body
    assert "_highlight_net_hotkey" in body
    assert "_toggle_net_highlight" in body
    assert 'text == "~"' in body
    assert "_clear_net_highlight" in body


def test_canvas_has_persistent_net_highlight_path_for_pads_tracks_and_vias():
    assert "def set_net_highlight(self, nets=None, enabled=False):" in VIEW
    assert "item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)" in VIEW
    assert "def refresh_copper_net_metadata(self):" in VIEW


def test_ratsnest_uses_separate_bright_highlight_path():
    body = _body(PANEL, "_refresh_ratsnest", "_on_ratsnest_toggle")
    assert "highlight_path" in body
    assert "_highlighted_nets" in body
    assert "_assign_saved_connection_nets()" in body


def test_net_highlight_survives_tab_window_host_rebuild():
    assert '"highlighted_nets"' in VIEWER
    assert '"net_highlight_enabled"' in VIEWER
    assert "viewer.canvas.set_net_highlight" in VIEWER
