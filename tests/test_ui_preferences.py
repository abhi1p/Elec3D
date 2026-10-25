# -*- coding: utf-8 -*-
"""Source/pure-Python coverage for configurable sidebar and 2D cursor UI."""

from pathlib import Path

from elec3d.options import (
    DEFAULT_SIDEBAR_EXPANDED,
    DEFAULT_SIDEBAR_ORDER,
    DEFAULT_SMALL_CROSSHAIR_PX,
    MAX_SMALL_CROSSHAIR_PX,
    MIN_SMALL_CROSSHAIR_PX,
    ImportOptions,
    normalize_crosshair_size,
    normalize_sidebar_order,
)

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEW2D = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "elec3d" / "commands.py").read_text(encoding="utf-8")
QT = (ROOT / "elec3d" / "qt.py").read_text(encoding="utf-8")


def test_requested_default_sidebar_order():
    assert DEFAULT_SIDEBAR_ORDER == (
        "footprints", "properties", "board", "routing", "footprint_graphics", "pads",
    )


def test_requested_sections_start_collapsed():
    for key in ("footprints", "footprint_graphics", "pads"):
        assert DEFAULT_SIDEBAR_EXPANDED[key] is False
    for key in ("properties", "board", "routing"):
        assert DEFAULT_SIDEBAR_EXPANDED[key] is True
    assert "design_layers" not in DEFAULT_SIDEBAR_EXPANDED
    assert "stackup" not in DEFAULT_SIDEBAR_EXPANDED


def test_sidebar_order_normalizer_keeps_all_sections_once():
    result = normalize_sidebar_order("pads,pads,properties,unknown")
    assert result[:2] == ["pads", "properties"]
    assert len(result) == len(DEFAULT_SIDEBAR_ORDER)
    assert set(result) == set(DEFAULT_SIDEBAR_ORDER)


def test_crosshair_defaults_small_and_rejects_unknown_values():
    assert ImportOptions().crosshair_mode == "small"
    assert ImportOptions(crosshair_mode="full").crosshair_mode == "full"
    assert ImportOptions(crosshair_mode="giant").crosshair_mode == "small"


def test_small_crosshair_defaults_40px_and_size_is_clamped():
    assert DEFAULT_SMALL_CROSSHAIR_PX == 40
    assert ImportOptions().crosshair_size_px == 40
    assert normalize_crosshair_size(1) == MIN_SMALL_CROSSHAIR_PX
    assert normalize_crosshair_size(999) == MAX_SMALL_CROSSHAIR_PX
    assert ImportOptions(crosshair_size_px=72).crosshair_size_px == 72


def test_remaining_sidebar_sections_are_collapsible_and_layers_stackup_moved_out():
    for title in ("Footprints", "Properties", "Routing", "Footprint graphics", "Pads"):
        marker = 'Section("%s", collapsible=True' % title
        assert marker in PANEL
    assert 'self.sec_stack.hide()' in PANEL
    assert 'self.sec_stackup.hide()' in PANEL
    assert 'self.act_stackup_dialog = self.menu_edit.addAction("Stackup…")' in PANEL


def test_sidebar_combos_share_spinbox_width_and_unit_column_alignment():
    assert "isinstance(control, QtWidgets.QAbstractSpinBox)" in PANEL
    assert "isinstance(control, QtWidgets.QComboBox)" in PANEL
    assert 'unit = QtWidgets.QLabel(suffix or "")' in PANEL
    assert "unit.setFixedWidth(20)" in PANEL
    assert "self.grid_combo.setMaximumWidth(SIDEBAR_SPIN_WIDTH)" in PANEL


def test_stackup_copper_rows_show_layer_name_without_redundant_word():
    assert '"copper", layer.name,' in PANEL
    assert '"copper", "%s copper" % layer.name,' not in PANEL


def test_preferences_offer_sidebar_order_expansion_and_crosshair():
    assert 'params.SetString("SidebarOrder"' in COMMANDS
    assert 'params.SetBool("SidebarExpanded_" + key' in COMMANDS
    assert 'crosshair.addItem("Small crosshair", "small")' in COMMANDS
    assert 'crosshair.addItem("Full crosshair", "full")' in COMMANDS
    assert 'view_form.addRow("Small crosshair length", crosshair_size)' in COMMANDS
    assert 'params.SetInt("CrosshairSizePx", crosshair_size.value())' in COMMANDS


def test_2d_canvas_supports_small_and_full_crosshairs():
    assert 'self.crosshair_mode = "small"' in VIEW2D
    assert 'def set_crosshair_mode(self, mode, size_px=None):' in VIEW2D
    assert 'if self.crosshair_mode == "full":' in VIEW2D
    assert 'self.crosshair_size_px = DEFAULT_SMALL_CROSSHAIR_PX' in VIEW2D
    assert 'half_span = 0.5 * float(self.crosshair_size_px)' in VIEW2D
    assert 'dx = half_span / sx' in VIEW2D
    assert 'dy = half_span / sy' in VIEW2D


def test_combo_and_spin_controls_ignore_mouse_wheel_edits():
    assert 'class NoWheelComboBox(QtWidgets.QComboBox):' in QT
    assert 'class NoWheelSpinBox(QtWidgets.QSpinBox):' in QT
    assert 'class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):' in QT
    assert 'def wheelEvent(self, event):' in QT
    assert 'event.ignore()' in QT
    assert 'QtWidgets.QComboBox()' not in PANEL
    assert 'QtWidgets.QDoubleSpinBox()' not in PANEL
    assert 'QtWidgets.QComboBox()' not in COMMANDS


def test_crosshair_uses_high_contrast_theme_colours_and_halo():
    theme_src = (ROOT / "elec3d" / "theme.py").read_text(encoding="utf-8")
    assert '"crosshair": "#E6ECF7"' in theme_src
    assert '"crosshair_halo": "#080A0D"' in theme_src
    assert '"crosshair": "#394150"' in theme_src
    assert '"crosshair_halo": "#FFFFFF"' in theme_src
    assert 'colors["crosshair_halo"]' in VIEW2D
    assert 'halo.setWidthF(3.5)' in VIEW2D
    assert 'pen.setWidthF(1.5)' in VIEW2D
