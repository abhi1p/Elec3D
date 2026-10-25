# -*- coding: utf-8 -*-
"""Non-GUI guards for v0.7.11 2D/netlist behaviour."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_f_fab_starts_hidden_but_saved_visibility_wins_on_rebuild():
    src = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    assert 'previous_visibility.get(layer, False if layer == "F.Fab" else True)' in src


def test_live_ratsnest_does_not_commit_history_during_mouse_move():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    block = panel.split("def _on_footprints_moving", 1)[1].split("def _on_footprints_moved", 1)[0]
    assert "_refresh_ratsnest(sync_copper_nets=False)" in block
    assert "_push_history" not in block
