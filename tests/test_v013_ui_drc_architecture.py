# -*- coding: utf-8 -*-
"""Architecture guards for the v0.13 layer bar / DRC / per-layer outline UI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEW = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
DESIGN = (ROOT / "elec3d" / "design.py").read_text(encoding="utf-8")
DESIGNFILE = (ROOT / "elec3d" / "designfile.py").read_text(encoding="utf-8")
THEME = (ROOT / "elec3d" / "theme.py").read_text(encoding="utf-8")
VIEWER = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")


def test_bottom_layer_bar_is_between_canvas_and_status_and_can_add_layers():
    assert "vlayout.addWidget(self.canvas, 1)" in PANEL
    assert "vlayout.addWidget(self._build_layer_bar())" in PANEL
    assert "vlayout.addWidget(self._build_status())" in PANEL
    assert 'self.btn_layer_add_bottom = QtWidgets.QPushButton("+")' in PANEL
    assert "self.btn_layer_add_bottom.clicked.connect(self._on_stack_add)" in PANEL
    assert 'button = QtWidgets.QPushButton("[%d] %s" % (index, layer.name))' in PANEL
    assert 'button.clicked.connect(lambda _checked=False, uid=layer.id: self._on_stack_activate(uid))' in PANEL
    assert "QPushButton#layerTabButton" in THEME


def test_selection_count_is_in_bottom_status_bar():
    assert 'self.status_selection = QtWidgets.QLabel("No selection")' in PANEL
    assert 'text = "1 object is selected"' in PANEL
    assert 'text = "%d objects are selected" % count' in PANEL


def test_design_rules_and_stackup_are_edit_menu_commands_not_sidebar_preferences():
    assert 'self.menu_edit = QtWidgets.QMenu(self)' in PANEL
    assert 'self.act_design_rules = self.menu_edit.addAction("Design Rules…")' in PANEL
    assert 'self.act_stackup_dialog = self.menu_edit.addAction("Stackup…")' in PANEL
    assert 'self.sec_stack.hide()' in PANEL
    assert 'self.sec_stackup.hide()' in PANEL


def test_design_rules_cover_requested_clearance_pairs_and_live_router_uses_them():
    drc = (ROOT / "elec3d" / "drc.py").read_text(encoding="utf-8")
    for key in ("track_track", "track_via", "track_pad", "via_via", "via_pad",
                "pad_pad", "courtyard_courtyard", "outline_track", "outline_via",
                "outline_pad", "outline_courtyard"):
        assert '"%s"' % key in drc
    assert "rules=self.design_rules" in PANEL
    assert "clip_polyline_before_conflict" in PANEL
    assert "def _footprint_drc_violation" in PANEL
    assert "DRC blocked: component courtyard clearance" in PANEL
    assert "DRC blocked: pad-to-pad clearance" in PANEL
    assert "def _layer_outline_drc_violation" in PANEL


def test_design_rules_persist_in_board_and_host_rebuilds():
    assert 'lines.append("  (design_rules")' in DESIGNFILE
    assert 'data["design_rules"]' in DESIGNFILE
    assert '"design_rules": self.design_rules.snapshot()' in PANEL
    assert '"design_rules": viewer.design_rules.snapshot()' in VIEWER
    assert 'viewer.design_rules.restore(state.get("design_rules"))' in VIEWER


def test_board_boundaries_come_from_the_solid_and_no_standalone_design_layer_exists():
    # OutlineShape survives only to migrate pre-0.17 boards into a solid.
    assert "self.layer_id = layer_id" in DESIGN
    assert "OutlineShape" not in VIEW
    assert "def set_board_sections" in VIEW
    assert "return list(reversed(self.layers))" in DESIGN
    assert "self.outline = DesignLayer" not in DESIGN
    assert "outline_layer" not in DESIGNFILE.split("def dumps", 1)[1].split("def loads", 1)[0]
