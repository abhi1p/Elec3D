from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEW = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")


def _body(source, name, next_name):
    start = source.index("    def %s" % name)
    end = source.index("    def %s" % next_name, start)
    return source[start:end]


def test_layer_activation_does_not_rebuild_design_layer_rows():
    body = _body(PANEL, "_on_stack_activate", "_on_stack_visible")
    assert "_apply_design_layers" in body
    assert "_rebuild_stack" not in body


def test_layer_visibility_does_not_rebuild_design_layer_rows():
    body = _body(PANEL, "_on_stack_visible", "_on_stack_remove")
    assert "visibility_changed=True" in body
    assert "_rebuild_stack" not in body


def test_design_layer_rows_have_in_place_sync():
    assert "class StackRow" in PANEL
    assert "def sync(self, layer, count, active, removable=True)" in PANEL
    assert "def _sync_stack_rows(self):" in PANEL


def test_routed_copper_has_style_only_layer_switch_path():
    assert "def sync_routed_copper_style(self, stack, update_extent=False):" in VIEW
    body = _body(PANEL, "_apply_design_layers", "_stack_counts")
    assert "sync_routed_copper_style" in body
    assert "_apply_routed_copper()" not in body


def test_select_and_route_tools_share_one_fixed_width_rail():
    assert 'self.tool_rail = self._build_copper_tool_panel()' in PANEL
    assert 'self.editor_tool_group = QtWidgets.QButtonGroup(panel)' in PANEL
    assert 'self.routing_mode_buttons = {}' in PANEL
    assert 'self.outline_mode_buttons' not in PANEL
    assert 'panel.setFixedWidth(TOOL_RAIL_WIDTH)' in PANEL
    assert "def _sync_tool_rail(self):" in PANEL


def test_layer_transition_coalesces_property_refresh():
    body = _body(PANEL, "_apply_design_layers", "_stack_counts")
    assert "self.canvas.blockSignals(True)" in body
    assert body.count("self._refresh_properties()") == 1


def test_outline_to_copper_keeps_left_routing_section_geometry_stable():
    body = _body(PANEL, "_sync_routing_ui", "_sync_via_target_combo")
    assert "self.sec_routing.setVisible(in_2d)" in body
    assert "self.sec_routing.setEnabled(copper)" in body
