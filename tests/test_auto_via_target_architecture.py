from pathlib import Path

PANEL = (Path(__file__).parents[1] / "elec3d" / "panel2d.py").read_text(encoding="utf-8")


def test_via_target_combo_offers_auto_and_manual_override():
    assert 'combo.addItem(auto_label, "__auto__")' in PANEL
    assert 'self.via_target_auto = False' in PANEL
    assert 'automatic_via_target_layer(' in PANEL


def test_via_placement_and_preview_use_resolved_target():
    assert 'end_layer = self._resolved_via_target_layer(preview)' in PANEL
    assert 'target = self._resolved_via_target_layer(preview)' in PANEL
