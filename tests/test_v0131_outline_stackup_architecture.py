# -*- coding: utf-8 -*-
"""Architecture guards for v0.13.1 stackup/outline refinements."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEW2D = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
VIEW3D = (ROOT / "elec3d" / "view3d.py").read_text(encoding="utf-8")
LAYERS = (ROOT / "elec3d" / "layers.py").read_text(encoding="utf-8")


def test_stackup_dialog_shows_copper_layer_swatches():
    assert "def layer_label(text, color):" in PANEL
    assert 'swatch.setFixedSize(10, 10)' in PANEL
    assert 'grid.addWidget(layer_label("%s copper" % layer.name, layer.color), line, 0)' in PANEL
    # 0.17: no top margin, live plane heights and per-plane section status.
    dialog = PANEL.split("def show_stackup_dialog", 1)[1].split("\n    def ", 1)[0]
    assert "top margin" not in dialog
    assert "self._board_layer_status(hz)" in dialog


def test_board_edge_has_dedicated_theme_colour_not_copper_tint():
    assert "OUTLINE_COLORS" in LAYERS
    assert "def outline_hex" in LAYERS
    assert "color = layermod.outline_hex(self.theme)" in VIEW2D
    assert "edge_hex = outline_hex(self.theme)" in VIEW3D


def test_3d_stack_marks_every_reference_plane_with_its_solid_section():
    assert "layer_loops[layer.id] = solid.section(zmap.get(layer.id, 0.0))" in VIEW3D
    assert "def loft(self, lower_points, z0, upper_points, z1" in VIEW3D
    assert "heights = stack.reference_heights()" in VIEW3D
