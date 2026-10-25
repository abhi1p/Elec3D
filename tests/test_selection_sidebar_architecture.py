from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")


def _body(source, name, next_name):
    start = source.index("    def %s(" % name)
    end = source.index("    def %s(" % next_name, start)
    return source[start:end]


def test_property_grid_reuses_widgets_instead_of_delete_later():
    start = PANEL.index("class PropertyGrid")
    end = PANEL.index("class Viewer2D", start)
    body = PANEL[start:end]
    assert "self._row_widgets = []" in body
    assert "def _ensure_row" in body
    assert "deleteLater" not in body


def test_selecting_current_active_footprint_does_not_rebuild_aux_sections():
    body = _body(PANEL, "_on_selection", "_on_fields_toggled")
    assert "if primary is self._active" in body
    assert "self._refresh_properties()" in body
    assert "self._rebuild_layers" not in body
    assert "self._rebuild_pads" not in body


def test_properties_refresh_is_coalesced_for_sidebar_painting():
    body = _body(PANEL, "_refresh_properties", "_update_footprint_rows")
    assert "surface.setUpdatesEnabled(False)" in body
    assert "surface.setUpdatesEnabled(True)" in body


def test_sidebar_spinboxes_use_shared_widths():
    assert "SIDEBAR_SPIN_WIDTH = 96" in PANEL
    assert "STACKUP_SPIN_WIDTH = 86" in PANEL
    assert "QtWidgets.QAbstractSpinBox" in PANEL
    assert "QtWidgets.QComboBox" in PANEL
    assert "control.setMinimumWidth(SIDEBAR_SPIN_WIDTH)" in PANEL
