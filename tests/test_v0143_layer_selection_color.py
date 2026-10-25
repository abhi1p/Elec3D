from pathlib import Path


def _view2d_source():
    return (Path(__file__).parents[1] / "elec3d" / "view2d.py").read_text(encoding="utf-8")


def test_track_selection_uses_track_layer_colour():
    src = _view2d_source()
    block = src[src.index("class TrackGraphicsItem"):src.index("class ViaGraphicsItem")]
    assert "QtGui.QColor(self.base_color).lighter(140)" in block
    assert 'QtGui.QColor("#5C7CFA")' not in block


def test_via_selection_uses_via_layer_colour():
    src = _view2d_source()
    block = src[src.index("class ViaGraphicsItem"):src.index("class FootprintItem")]
    assert "QtGui.QColor(self.base_color).lighter(140)" in block
    assert 'QtGui.QColor("#5C7CFA")' not in block


def test_selected_pad_outline_uses_pad_layer_colour():
    src = _view2d_source()
    start = src.index("def set_selected_pad")
    block = src[start:src.index("def paint", start + 20) if False else start + 5000]
    assert "pad_color = QtGui.QColor(self.layer_hex" in block
