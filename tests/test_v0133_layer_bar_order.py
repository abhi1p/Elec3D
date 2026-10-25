# -*- coding: utf-8 -*-
"""Regression guard for v0.13.3 bottom-to-top layer-bar ordering."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
DESIGN = (ROOT / "elec3d" / "design.py").read_text(encoding="utf-8")


def test_layer_bar_is_bottom_to_top_so_bcu_is_left_and_new_top_layer_is_right():
    assert "bottom_up = self.stack.copper_layers()" in PANEL
    assert "for index, layer in enumerate(bottom_up, 1):" in PANEL
    assert "expected = [layer.id for layer in self.stack.copper_layers()]" in PANEL
    assert "for index, layer in enumerate(self.stack.copper_layers(), 1):" in PANEL
    # LayerStack stores planes bottom-first and appends newly created top planes.
    assert "self.layers = []                       # copper planes only, bottom first" in DESIGN
    assert "self.layers.append(layer)" in DESIGN
