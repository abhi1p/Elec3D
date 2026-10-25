# -*- coding: utf-8 -*-
"""The complete 2D viewer window.

Layout mirrors the reference web viewer: a slim header with the footprint name
and view actions, a 236px sidebar (Footprints / Properties / Graphics layers /
Pads), the canvas, and a status bar with the cursor position, grid step and
hover readout.

The widget is intentionally independent of FreeCAD documents. Parsed footprint
models live directly in the Qt scene, so the same widget also runs standalone.
"""

import os
import uuid
import math
import json
import hashlib
import time as _time

from . import __version__, PREF_PATH
from . import layers as layermod
from . import theme as thememod
from .options import DEFAULT_SIDEBAR_ORDER, normalize_sidebar_order
from .design import LayerStack
from . import boardsolid as boardsolidmod
from .boardsolid import BoardSolid, BoardSolidError
from .drc import DesignRules, RULE_LABELS
from . import footprint as fpmod
from .footprint import parse_footprint, parse_footprint_file
from . import designfile
from . import projectfile
from .netlist import (KiCadNetlist, pins_by_ref, placeholder_footprint)
from .routing import (Track, Via, route45, route_snap, routing_context,
                      assign_connection_nets, ratsnest_segments, pad_key, build_copper_topology,
                      via_spans_layer, track_touches_pad, pad_contains_point, automatic_via_target_layer,
                      dist_to_segment, drag_track_segment, route_conflicts, via_conflicts, route_outline_conflicts,
                      via_outline_conflicts, via_layer_outline_conflicts, clip_polyline_before_conflict,
                      drag_track_corner, drag_via_attached_points,
                      normalize_track_points, delete_track_segment, join_track_endpoints,
                      walkaround_route, conflict_boxes, board_outline,
                      ObstacleIndex, BoardOutline)
from .qt import (Qt, QtCore, QtGui, QtWidgets, Signal, enum, exec_dialog,
                 NoWheelComboBox, NoWheelDoubleSpinBox)
from .view2d import FootprintCanvas
from .view3d import Board3DView, board_bounds

SIDEBAR_WIDTH = 244
SIDEBAR_SPIN_WIDTH = 96
STACKUP_SPIN_WIDTH = 86
TOOL_RAIL_WIDTH = 46
ROUTING_PANEL_WIDTH = TOOL_RAIL_WIDTH
GRID_STEPS = [5.0, 2.54, 2.5, 1.27, 1.0, 0.635, 0.5, 0.25, 0.127, 0.1, 0.05, 0.025]

# Shown wherever a multi-selection owns a property but the selected objects
# disagree on its value.  The row stays visible: hiding it would wrongly imply
# the property does not apply to the selection.
MIXED = "\u2014 mixed \u2014"

SELECTION_FILTER_KEYS = (("footprints", "Footprints"), ("pads", "Pads"),
                         ("tracks", "Tracks"), ("vias", "Vias"))

# Footprints on a plane other than the active one are dimmed rather than
# hidden, so the rest of the board stays readable while you work on one layer.
INACTIVE_OPACITY = 0.55

# QSizePolicy enums are scoped in Qt6 but flat in Qt5/FreeCAD compatibility
# builds.  Resolve them once through the add-on's enum shim.
SIZE_IGNORED = enum(QtWidgets.QSizePolicy, "Policy", "Ignored")
SIZE_PREFERRED = enum(QtWidgets.QSizePolicy, "Policy", "Preferred")
SIZE_EXPANDING = enum(QtWidgets.QSizePolicy, "Policy", "Expanding")
SIZE_FIXED = enum(QtWidgets.QSizePolicy, "Policy", "Fixed")


# ---------------------------------------------------------------------------
# small building blocks
# ---------------------------------------------------------------------------
class Section(QtWidgets.QWidget):
    """A sidebar section with an uppercase header, optionally collapsible."""

    def __init__(self, title, collapsible=False, expanded=True, parent=None):
        super(Section, self).__init__(parent)
        self._title = title
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        if collapsible:
            self.header = QtWidgets.QPushButton()
            self.header.setObjectName("sectionHeader")
            self.header.setCheckable(True)
            self.header.setChecked(expanded)
            self.header.clicked.connect(self._toggle)
            self.header.setCursor(Qt.PointingHandCursor)
        else:
            self.header = QtWidgets.QLabel(title.upper())
            self.header.setObjectName("sectionLabel")
        outer.addWidget(self.header)

        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(2, 0, 2, 2)
        self.body_layout.setSpacing(3)
        outer.addWidget(self.body)

        self._collapsible = collapsible
        self._count_text = ""
        self.set_expanded(expanded)

    def _toggle(self):
        self.set_expanded(self.header.isChecked())

    def set_expanded(self, expanded):
        self.body.setVisible(expanded)
        if self._collapsible:
            self.header.setChecked(expanded)
            arrow = "\u25be" if expanded else "\u25b8"
            self.header.setText("%s  %s%s" % (arrow, self._title.upper(), self._count_text))

    def set_count(self, text):
        self._count_text = (" %s" % text) if text else ""
        # isVisible() becomes false whenever an ancestor is temporarily hidden
        # (notably during Tab/Window host rebuilds).  The check state is the
        # section's actual expansion state and survives those host transitions.
        expanded = self.header.isChecked() if self._collapsible else True
        self.set_expanded(expanded)

    def add(self, widget):
        self.body_layout.addWidget(widget)
        return widget

    def clear(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Never reparent a visible child to None: Qt turns it into a
                # top-level window, which showed up in FreeCAD as one tiny
                # floating window per sidebar row.  Hide it in-place and let
                # Qt destroy it after the current event finishes.
                widget.hide()
                widget.deleteLater()


class LayerRow(QtWidgets.QWidget):
    """Checkbox + colour swatch + layer name + item count."""

    toggled = Signal(str, bool)

    def __init__(self, layer, count, color, checked=True, parent=None):
        super(LayerRow, self).__init__(parent)
        self.layer = layer
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(7)

        self.check = QtWidgets.QCheckBox()
        self.check.setChecked(bool(checked))
        # Read QCheckBox.isChecked() instead of casting stateChanged's payload.
        # PySide versions differ on whether that payload is an int or a Qt
        # enum, and enum members are not reliably false-y for Unchecked.
        self.check.stateChanged.connect(
            lambda _state: self.toggled.emit(self.layer, self.check.isChecked()))
        row.addWidget(self.check)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(11, 11)
        self.set_color(color)
        row.addWidget(self.swatch)

        name = QtWidgets.QLabel(layer)
        row.addWidget(name)
        row.addStretch(1)

        count_label = QtWidgets.QLabel(str(count))
        count_label.setObjectName("faint")
        row.addWidget(count_label)

    def set_color(self, color):
        self.swatch.setStyleSheet(
            "background: %s; border-radius: 2px; border: 1px solid rgba(0,0,0,.35);"
            % color)


class GroupTitle(QtWidgets.QLabel):
    """A non-collapsible bar heading a group inside a section."""

    def __init__(self, text, parent=None):
        super(GroupTitle, self).__init__(text.upper(), parent)
        self.setObjectName("groupTitle")


class CollapsibleGroup(QtWidgets.QWidget):
    """A group inside a section whose header toggles its body.

    ``Section`` heads a whole sidebar section; this is the smaller nested
    equivalent used for "Fields" under Properties.
    """

    toggled = Signal(bool)

    def __init__(self, title, expanded=False, parent=None):
        super(CollapsibleGroup, self).__init__(parent)
        self._title = title
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 0)
        outer.setSpacing(4)

        self.header = QtWidgets.QPushButton()
        self.header.setObjectName("groupHeader")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        # Only a real click emits ``toggled``; set_expanded() stays silent so
        # the panel can force a group open without overwriting the user's
        # remembered preference.
        self.header.clicked.connect(self._on_clicked)
        outer.addWidget(self.header)

        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(2)
        outer.addWidget(self.body)

        self.set_expanded(expanded)

    def _on_clicked(self):
        self.set_expanded(self.header.isChecked())
        self.toggled.emit(self.is_expanded())

    def set_expanded(self, expanded):
        expanded = bool(expanded)
        self.body.setVisible(expanded)
        self.header.setChecked(expanded)
        arrow = "\u25be" if expanded else "\u25b8"
        self.header.setText("%s  %s" % (arrow, self._title.upper()))

    def is_expanded(self):
        return self.body.isVisible()

    def add(self, widget):
        self.body_layout.addWidget(widget)
        return widget


class ControlRow(QtWidgets.QWidget):
    """A dimmed caption on the left and an editable control on the right.

    Sidebar rows must be allowed to shrink below their contents' size hints.
    FreeCAD's styles can give spin boxes and combo boxes surprisingly large
    minimum-size hints; inside a ``QScrollArea`` that used to make the whole
    sidebar child wider than its viewport and silently clip the right edge.
    """

    def __init__(self, caption, control, suffix=None, parent=None):
        super(ControlRow, self).__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(7)

        label = QtWidgets.QLabel(caption)
        label.setObjectName("dim")
        row.addWidget(label)
        row.addStretch(1)

        self.control = control
        control.setMinimumWidth(0)
        # Numeric editors within a sidebar group should line up instead of
        # inheriting slightly different native size hints from their ranges or
        # suffixes.  Keep one deterministic editor width and put units in their
        # own fixed column.
        if (isinstance(control, QtWidgets.QAbstractSpinBox) or
                isinstance(control, QtWidgets.QComboBox)):
            control.setMinimumWidth(SIDEBAR_SPIN_WIDTH)
            control.setMaximumWidth(SIDEBAR_SPIN_WIDTH)
            control.setSizePolicy(SIZE_FIXED, SIZE_FIXED)
        row.addWidget(control, 0, Qt.AlignRight)

        # Reserve the unit column for every ControlRow, including combo boxes
        # and other controls that do not display a suffix.  Without this empty
        # column a suffix-less combo is right-aligned against the row edge, so
        # it starts farther right than the spin boxes above it.  A permanent
        # fixed-width unit column keeps both the left and right edges of all
        # sidebar editors aligned.
        unit = QtWidgets.QLabel(suffix or "")
        unit.setObjectName("dim")
        unit.setFixedWidth(20)
        unit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(unit)


class Export3DDialog(QtWidgets.QDialog):
    """STEP/FreeCAD export options, mirroring the reference viewer workflow."""

    def __init__(self, counts, options, destination="step", parent=None, target_document="", target_available=False):
        super(Export3DDialog, self).__init__(parent)
        self.counts = dict(counts or {})
        self.options = dict(options or {})
        self.destination = "freecad" if destination == "freecad" else "step"
        self.target_document = str(target_document or "")
        self.target_available = bool(target_available and self.target_document)
        if self.destination == "freecad":
            self.options["update_existing"] = self.target_available
        self._checks = {}
        self.setModal(True)
        self.setWindowTitle("Export STEP" if self.destination == "step" else "Export to FreeCAD")
        self.setMinimumWidth(520)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Export STEP" if self.destination == "step" else "Export to FreeCAD")
        title.setObjectName("title")
        head.addWidget(title)
        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setObjectName("faint")
        head.addWidget(self.subtitle)
        head.addStretch(1)
        outer.addLayout(head)

        self.rows = QtWidgets.QVBoxLayout()
        self.rows.setSpacing(3)
        outer.addLayout(self.rows)

        self._add_option(
            "tracks", "Tracks and vias",
            "Routed copper and via barrels as analytic OpenCascade solids.",
            self._track_count(), bool(self.counts.get("tracks") or self.counts.get("vias")))
        self._add_option(
            "pads", "Pads",
            "Footprint pads on their design copper layer, including pad drill cut-outs.",
            "%d pads" % int(self.counts.get("pads", 0)), bool(self.counts.get("pads")))
        model_count = int(self.counts.get("models", 0))
        missing = int(self.counts.get("missing_models", 0))
        model_text = "%d placed" % model_count
        if missing:
            model_text += " · %d missing" % missing
        self._add_option(
            "models", "Component 3D models",
            "Placed STEP models are loaded as exact B-Rep where the source allows it.",
            model_text, model_count > 0)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setObjectName("separator")
        self.rows.addWidget(sep)

        block = self.counts.get("block")
        self._board_available = bool(block)
        block_text = ("%.1f × %.1f × %.2f mm" % (block["w"], block["d"], block["h"])) if block else "no board solid"
        self._add_option(
            "board", "Board solid",
            "Include the imported board body as its own solid in the assembly.",
            block_text, bool(block))
        self._add_option(
            "negative", "Export as negative (cavity)",
            "Subtract the selected parts from the imported board solid; output becomes one cavity solid.",
            block_text, bool(block))
        self._add_option(
            "fuse", "Fuse touching copper",
            "Boolean-union overlapping copper before output. Cleaner solids, but slower on dense boards.",
            "", True)
        self._add_option(
            "no_pcurves", "Omit P-curves",
            ("Skip parametric surface curves to reduce STEP file size."
             if self.destination == "step" else
             "Only applies when writing a STEP file; direct FreeCAD export keeps native B-Rep geometry."),
            "", self.destination == "step")

        if self.destination == "freecad":
            sep2 = QtWidgets.QFrame()
            sep2.setFrameShape(QtWidgets.QFrame.HLine)
            sep2.setObjectName("separator")
            self.rows.addWidget(sep2)
            self._add_option(
                "update_existing", "Update existing FreeCAD document",
                "Refresh only the managed Elec3D PCB Export part; keep the same FreeCAD tab and unrelated user objects.",
                self.target_document if self.target_available else "no linked document yet",
                self.target_available)

        outer.addSpacing(5)
        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.go = QtWidgets.QPushButton("Export" if self.destination == "step" else "Create in FreeCAD")
        self.go.setDefault(True)
        self.go.clicked.connect(self.accept)
        buttons.addWidget(self.go)
        outer.addLayout(buttons)
        self._sync()

    def _track_count(self):
        tracks = int(self.counts.get("tracks", 0)); vias = int(self.counts.get("vias", 0))
        if not tracks and not vias:
            return "none"
        return "%d track%s · %d via%s" % (tracks, "" if tracks == 1 else "s",
                                           vias, "" if vias == 1 else "s")

    def _add_option(self, key, label, hint, count, enabled):
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)
        check = QtWidgets.QCheckBox()
        check.setChecked(bool(self.options.get(key, key in ("tracks", "pads", "models", "no_pcurves"))))
        check.setEnabled(bool(enabled))
        if not enabled:
            check.setChecked(False)
        check.toggled.connect(self._sync)
        row.addWidget(check, 0, Qt.AlignTop)

        text_box = QtWidgets.QWidget()
        text_layout = QtWidgets.QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        name = QtWidgets.QLabel(label)
        name.setObjectName("propertyKind")
        hint_label = QtWidgets.QLabel(hint)
        hint_label.setObjectName("faint")
        hint_label.setWordWrap(True)
        text_layout.addWidget(name)
        text_layout.addWidget(hint_label)
        row.addWidget(text_box, 1)
        if count:
            count_label = QtWidgets.QLabel(count)
            count_label.setObjectName("faint")
            row.addWidget(count_label, 0, Qt.AlignTop | Qt.AlignRight)
        self.rows.addWidget(widget)
        self._checks[key] = check

    def _sync(self):
        negative = bool(self._checks.get("negative") and self._checks["negative"].isChecked())
        fuse = self._checks.get("fuse")
        if fuse is not None:
            blocked = fuse.blockSignals(True)
            if negative:
                fuse.setChecked(True)
                fuse.setEnabled(False)
                fuse.setToolTip("Always enabled for a cavity export.")
            else:
                fuse.setEnabled(True)
                fuse.setToolTip("")
            fuse.blockSignals(blocked)
        board = self._checks.get("board")
        if board is not None:
            blocked = board.blockSignals(True)
            if negative:
                board.setChecked(False)
                board.setEnabled(False)
                board.setToolTip("A cavity export already starts from the board solid.")
            else:
                board.setEnabled(bool(getattr(self, "_board_available", False)))
                board.setToolTip("")
            board.blockSignals(blocked)
        any_body = any(self._checks[key].isChecked() for key in ("tracks", "pads", "models"))
        if board is not None and board.isChecked() and not negative:
            any_body = True
        self.go.setEnabled(any_body)
        opts = self.selected_options()
        try:
            from .export3d import export_file_name
            if self.destination == "step":
                subtitle = export_file_name(opts)
            elif opts.get("update_existing") and self.target_available:
                subtitle = self.target_document
            else:
                subtitle = "NEW FREECAD DOCUMENT"
        except Exception:
            subtitle = ""
        self.subtitle.setText(subtitle)
        if self.destination == "freecad":
            self.go.setText("Update in FreeCAD" if opts.get("update_existing") else "Create in FreeCAD")

    def selected_options(self):
        result = dict(self.options)
        for key, check in self._checks.items():
            # Direct FreeCAD output never writes a STEP file, so its disabled
            # P-curve checkbox is presentation-only.  Preserve the user's STEP
            # preference instead of overwriting it with False merely because
            # they used Export to FreeCAD between two STEP exports.
            if key == "no_pcurves" and self.destination != "step":
                continue
            result[key] = bool(check.isChecked())
        if result.get("negative"):
            result["fuse"] = True
        return result


class StackupValueRow(QtWidgets.QWidget):
    """One physical stack-up entry with a compact millimetre editor.

    ``kind`` is ``bottom_margin``, ``copper`` or
    ``separation``.  Copper rows may show the matching design-layer swatch;
    separation rows keep the two neighbouring layer ids so editing can update
    the correct physical gap without relying on row indexes.
    """

    def __init__(self, kind, caption, value, color=None, parent=None):
        super(StackupValueRow, self).__init__(parent)
        self.kind = str(kind)
        self.layer_id = None
        self.lower_id = None
        self.upper_id = None

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(6)

        if color:
            self.swatch = QtWidgets.QLabel()
            self.swatch.setFixedSize(9, 9)
            self.swatch.setStyleSheet(
                "background: %s; border-radius: 1px; border: 1px solid rgba(0,0,0,.35);"
                % color)
            row.addWidget(self.swatch)
        else:
            self.swatch = None

        self.label = QtWidgets.QLabel(caption)
        self.label.setObjectName("dim")
        row.addWidget(self.label)
        row.addStretch(1)

        self.spin = NoWheelDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setRange(0.0, 1000.0)
        self.spin.setSingleStep(0.1)
        self.spin.setValue(float(value))
        self.spin.setMinimumWidth(STACKUP_SPIN_WIDTH)
        self.spin.setMaximumWidth(STACKUP_SPIN_WIDTH)
        self.spin.setSizePolicy(SIZE_FIXED, SIZE_FIXED)
        row.addWidget(self.spin, 0, Qt.AlignRight)

        unit = QtWidgets.QLabel("mm")
        unit.setObjectName("dim")
        row.addWidget(unit)


class StackRow(QtWidgets.QFrame):
    """One design layer: visibility, colour, name, footprint count and remove.

    The whole strip is clickable and makes the layer active, so the checkbox and
    the remove button stop their own clicks from reaching it.
    """

    activated = Signal(int)
    visibilityChanged = Signal(int, bool)
    removeRequested = Signal(int)

    def __init__(self, layer, count, active, removable=True, parent=None):
        super(StackRow, self).__init__(parent)
        self.setObjectName("stackRow")
        self.uid = layer.id
        # A dynamic property drives the active look from the stylesheet, which
        # keeps the two themes' colours in one place.
        self.setProperty("active", "true" if active else "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("%s \u2014 click to make it the active layer" % layer.name)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(5, 3, 4, 3)
        row.setSpacing(7)

        self.check = QtWidgets.QCheckBox()
        self.check.setChecked(bool(layer.visible))
        self.check.setToolTip("Show outline shapes" if layer.is_outline
                              else "Show footprints on this layer")
        self.check.stateChanged.connect(
            lambda _s: self.visibilityChanged.emit(self.uid, self.check.isChecked()))
        row.addWidget(self.check)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(11, 11)
        self.swatch.setStyleSheet(
            "background: %s; border-radius: 2px; border: 1px solid rgba(0,0,0,.35);"
            % layer.color)
        row.addWidget(self.swatch)

        self.name_label = QtWidgets.QLabel(layer.name)
        self.name_label.setObjectName("stackName")
        row.addWidget(self.name_label)
        row.addStretch(1)

        self.count_label = QtWidgets.QLabel(str(count) if count else "")
        self.count_label.setObjectName("stackCount")
        noun = "shape" if layer.is_outline else "footprint"
        self.count_label.setToolTip("%d %s%s on this layer"
                                    % (count, noun, "" if count == 1 else "s"))
        row.addWidget(self.count_label)

        self.remove = QtWidgets.QPushButton("\u00d7")
        self.remove.setObjectName("removeButton")
        self.remove.setCursor(Qt.PointingHandCursor)
        self.remove.setEnabled(bool(removable))
        self.remove.setToolTip(
            "Remove layer" if removable else
            ("Outline is a permanent design layer" if layer.is_outline
             else "A board needs at least one copper layer"))
        self.remove.clicked.connect(lambda: self.removeRequested.emit(self.uid))
        row.addWidget(self.remove)

    def sync(self, layer, count, active, removable=True):
        """Update this existing row without replacing any Qt widgets.

        Layer activation is a very frequent UI action.  Recreating every row
        for a one-bit active-state change caused visible sidebar flashes and
        queued deleteLater() work in FreeCAD's event loop.
        """
        self.uid = layer.id
        active_value = "true" if active else "false"
        if self.property("active") != active_value:
            self.setProperty("active", active_value)
            style = self.style()
            if style is not None:
                style.unpolish(self)
                style.polish(self)
            self.update()

        self.setToolTip("%s \u2014 click to make it the active layer" % layer.name)
        self.check.blockSignals(True)
        self.check.setChecked(bool(layer.visible))
        self.check.blockSignals(False)
        self.check.setToolTip("Show outline shapes" if layer.is_outline
                              else "Show footprints on this layer")
        self.swatch.setStyleSheet(
            "background: %s; border-radius: 2px; border: 1px solid rgba(0,0,0,.35);"
            % layer.color)
        self.name_label.setText(layer.name)
        self.count_label.setText(str(count) if count else "")
        noun = "shape" if layer.is_outline else "footprint"
        self.count_label.setToolTip("%d %s%s on this layer"
                                    % (count, noun, "" if count == 1 else "s"))
        self.remove.setEnabled(bool(removable))
        self.remove.setToolTip(
            "Remove layer" if removable else
            ("Outline is a permanent design layer" if layer.is_outline
             else "A board needs at least one copper layer"))

    def mousePressEvent(self, event):
        self.activated.emit(self.uid)
        super(StackRow, self).mousePressEvent(event)


class ToolRailButton(QtWidgets.QToolButton):
    """Compact KiCad-style icon button for the right-hand 2D tool rail."""

    def __init__(self, mode, parent=None):
        super(ToolRailButton, self).__init__(parent)
        self.mode = str(mode)
        self.setObjectName("toolRailButton")
        self.setCheckable(True)
        self.setAutoRaise(False)
        self.setFixedSize(34, 34)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event):
        # Let the platform/FreeCAD style paint hover/checked/button chrome, then
        # draw a tiny vector glyph using the current palette so it follows the
        # application theme without maintaining a second icon set.
        super(ToolRailButton, self).paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(self.palette().color(QtGui.QPalette.ButtonText))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(Qt.NoBrush))
        r = QtCore.QRectF(self.rect()).adjusted(8.0, 8.0, -8.0, -8.0)

        if self.mode == "select":
            path = QtGui.QPainterPath()
            path.moveTo(r.left(), r.top())
            path.lineTo(r.left() + 1.5, r.bottom() - 1.0)
            path.lineTo(r.center().x() - 1.0, r.center().y() + 2.0)
            path.lineTo(r.right() - 1.0, r.bottom())
            path.lineTo(r.right() - 4.5, r.center().y() + 2.5)
            path.lineTo(r.center().x() + 1.0, r.center().y())
            path.closeSubpath()
            painter.setBrush(self.palette().color(QtGui.QPalette.ButtonText))
            painter.drawPath(path)
        elif self.mode == "route":
            path = QtGui.QPainterPath()
            path.moveTo(r.left(), r.bottom())
            path.lineTo(r.center().x() - 1.0, r.center().y() + 1.0)
            path.lineTo(r.right(), r.top())
            painter.drawPath(path)
            painter.setBrush(self.palette().color(QtGui.QPalette.ButtonText))
            painter.drawEllipse(QtCore.QPointF(r.left(), r.bottom()), 2.2, 2.2)
            painter.drawEllipse(QtCore.QPointF(r.right(), r.top()), 2.2, 2.2)
        elif self.mode == "filter":
            # Compact funnel glyph for the KiCad-style selection-filter menu.
            path = QtGui.QPainterPath()
            path.moveTo(r.left(), r.top())
            path.lineTo(r.right(), r.top())
            path.lineTo(r.center().x() + 2.0, r.center().y())
            path.lineTo(r.center().x() + 2.0, r.bottom())
            path.lineTo(r.center().x() - 2.0, r.bottom() - 2.0)
            path.lineTo(r.center().x() - 2.0, r.center().y())
            path.closeSubpath()
            painter.drawPath(path)
        elif self.mode == "fit":
            # Four corner brackets: zoom to fit.
            k = 4.0
            for cx, cy, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                                   (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
                painter.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx + dx * k, cy))
                painter.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx, cy + dy * k))
        elif self.mode == "board":
            # Board edge glyph: rounded board with a cut-out.
            painter.drawRoundedRect(r, 3.0, 3.0)
            painter.drawEllipse(QtCore.QPointF(r.right() - 4.0, r.top() + 4.0), 1.6, 1.6)
        painter.end()


# Backwards-compatible name used by earlier releases/tests.
OutlineToolButton = ToolRailButton


class PropertyGrid(QtWidgets.QWidget):
    """Two-column label/value list whose row widgets are reused in-place.

    Selection changes are common and should not destroy/recreate a dozen QLabel
    objects every time.  A small persistent row pool keeps the left sidebar
    visually stable; only text, style role and visibility are changed.
    """

    def __init__(self, parent=None):
        super(PropertyGrid, self).__init__(parent)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(3)
        self.grid.setColumnStretch(1, 1)
        self._row_widgets = []

    def _ensure_row(self, index):
        while len(self._row_widgets) <= index:
            i = len(self._row_widgets)
            left = QtWidgets.QLabel("")
            left.setObjectName("dim")
            right = QtWidgets.QLabel("")
            right.setObjectName("value")
            right.setWordWrap(True)
            right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            right.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(left, i, 0, Qt.AlignLeft | Qt.AlignTop)
            self.grid.addWidget(right, i, 1)
            self._row_widgets.append((left, right))
        return self._row_widgets[index]

    @staticmethod
    def _set_role(widget, role):
        if widget.objectName() == role:
            return
        widget.setObjectName(role)
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()

    def set_rows(self, rows):
        for i, row in enumerate(rows):
            # Rows are (label, value) or (label, value, mixed).  A mixed value
            # is styled like a hint rather than data, because it is a statement
            # about the selection rather than a value the user can read off.
            label, value = row[0], row[1]
            mixed = bool(row[2]) if len(row) > 2 else False
            left, right = self._ensure_row(i)
            text = str(label)
            if left.text() != text:
                left.setText(text)
            text = str(value)
            if right.text() != text:
                right.setText(text)
            self._set_role(right, "faint" if mixed else "value")
            left.show(); right.show()

        # Rows that do not apply to this selection remain allocated but hidden.
        # Re-selecting a footprint therefore reuses the exact same widgets.
        for left, right in self._row_widgets[len(rows):]:
            left.hide(); right.hide()


# ---------------------------------------------------------------------------
# the viewer
# ---------------------------------------------------------------------------
class Viewer2D(QtWidgets.QWidget):
    """Header + sidebar + canvas + status bar."""

    footprintMoved = Signal(object)
    hostActionRequested = Signal(str)

    def __init__(self, parent=None, theme=None, hosted=False):
        super(Viewer2D, self).__init__(parent)
        self.setObjectName("elec3dViewer")
        self.setWindowTitle("Elec3D")
        # MDI tabs must fit FreeCAD's central viewport rather than forcing the
        # QMdiArea to grow scroll bars from child size hints.  Individual
        # sections below are responsive, so there is no useful minimum canvas
        # width to impose on the host.
        self.setMinimumSize(0, 0)
        self.setSizePolicy(SIZE_IGNORED, SIZE_EXPANDING)
        self.hosted = bool(hosted)
        self._theme_sync_pending = False
        self.theme = theme or (thememod.application_theme() if self.hosted else "dark")
        self._entries = []                   # [{item, fp, row, section}]
        self._active = None
        self._selected = []                  # entries backing the Properties panel
        # Lightweight document-free edit history.  Commands store stable entry
        # ids and plain transform/deletion state rather than QGraphicsItem
        # pointers, so the history survives the viewer widget being rebuilt
        # during live Tab <-> Separate window host switches.
        self._history = []
        self._history_index = 0
        self._history_replaying = False
        self._updating_model_adjust = False
        self._model_adjust_edit_before = None
        self._auto_aligning_models = False
        # 3D export choices persist for the viewer session so repeated exports
        # only need a destination click, matching the reference web app.
        self._export_options = {
            "tracks": True, "pads": True, "models": True,
            "board": False, "negative": False, "fuse": False, "no_pcurves": True,
        }
        self.act_freecad = None
        # The board's copper stack.  Footprints are assigned to one of its
        # planes; the panel owns it because it is editor state, not something
        # that came out of any .kicad_mod file.
        self.stack = LayerStack()
        self.design_rules = DesignRules()
        self._updating_layer_combo = False
        self._updating_rotation = False
        # The board body.  Every copper layer's routing boundary is the
        # cross-section of this solid at the layer's reference-plane height,
        # so nothing about the board shape is drawn by hand any more.
        self.board_solid = None
        self._board_outline_cache = {}
        self._updating_board_controls = False
        # Remembered across selections so the Fields group does not spring shut
        # every time the user clicks a different footprint.
        self._fields_expanded = False
        # Schematic/netlist state is also document-free.  Entries created from
        # a netlist carry their schematic ref/value and can remain placeholders
        # until the footprint library is resolved by the import wizard.
        self.netlist = None
        self.models_folder = ""
        # Project identity is separate from the board file.  A .e3d_pro owns
        # one complete .e3d_pcb plus project-local Footprints/ and Models/.
        # Standalone .e3d_pcb boards can still be opened, but Save prompts for
        # project creation instead of silently writing a loose board.
        self._project_file = ""
        self._design_file = ""
        self._design_path_variables = {}
        self._design_resource_file = ""
        self._last_design_save_warnings = []
        # Persistent-content dirty tracking.  A lightweight semantic signature
        # is compared with the last successful save/open baseline, so Undo can
        # return all the way to a clean state instead of a one-way "edited" flag.
        self._saved_content_signature = None
        self._modified = False
        # Session link to the FreeCAD document produced by direct export.
        # Re-export updates this document by default instead of creating tabs
        # indefinitely; a new board/design clears the link.
        self._freecad_export_document = ""
        self.show_ratsnest = True
        self.view_mode = "2d"
        self._ratsnest_item = None
        self._ratsnest_highlight_item = None
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        # Routed copper is editor/document state, not footprint source data.
        # Tracks and vias therefore stay as plain Python models beside the
        # netlist and layer stack and survive Tab <-> Window host rebuilds.
        self.tracks = []
        self.vias = []
        self.route_tool = "select"
        self.routing = None
        self.via_pending = False
        self.track_width = 0.25
        self.via_diameter = 0.80
        self.via_hole_diameter = 0.0
        self.via_target_layer_id = None
        self.via_target_auto = True
        self.route_posture = 0
        # Copper drags hold H/V/45 geometry like KiCad; Alt frees the angle.
        self.drag_keep_45 = True
        # Walk around blocking copper instead of stopping dead at it.
        self.route_walkaround = True
        self._selected_copper = []
        self._route_history_before = None
        # M is a physical translation tool.  Direct mouse dragging of routed
        # copper is a separate KiCad-style interactive geometry edit.
        self._move_session = None
        self._copper_drag_session = None
        # Populated only for the duration of one interactive operation so the
        # repeated clearance probes of a single mouse move share one pad list
        # and one broad-phase obstacle index.
        self._routing_cache = None
        # Design-layer rows are persistent widgets.  Active/visibility changes
        # update them in place instead of destroying/recreating the whole
        # section, which avoids visible flashing during layer switches.
        self._stack_rows = {}
        self._netlist_dialog = None
        self._import_directory = os.path.expanduser("~")

        # Sidebar/cursor presentation comes from the same FreeCAD parameter
        # store as the Options dialog.  get_options() is safe outside FreeCAD
        # and returns the requested defaults when no parameter store exists.
        try:
            from . import get_options
            self._viewer_options = get_options()
        except Exception:
            from .options import ImportOptions
            self._viewer_options = ImportOptions()

        self._build_ui()
        self.apply_theme(self.theme)
        self._apply_design_layers()
        self._rebuild_stack()
        self._rebuild_stackup()

        # Polling a lightweight in-memory signature catches every editing path
        # (including direct QGraphicsItem drags) without scattering dirty flags
        # through dozens of UI callbacks.  View-only state is deliberately
        # excluded by _persistent_content_state().
        self._capture_saved_content_state()
        self._modified_timer = QtCore.QTimer(self)
        self._modified_timer.setInterval(200)
        self._modified_timer.timeout.connect(self.refresh_modified_state)
        self._modified_timer.start()

    # -- lifecycle -------------------------------------------------------
    #: Optional callable installed by the FreeCAD host.  When this viewer is a
    #: top-level (Separate Window) and the user closes it, the handler decides
    #: whether the close may proceed (it asks Save / Discard / Cancel and then
    #: tears the editor down).  Returning False keeps the window open.
    close_handler = None

    def closeEvent(self, event):
        handler = self.close_handler
        if handler is not None and self.isWindow():
            try:
                allowed = bool(handler(self))
            except Exception:
                allowed = True
            if not allowed:
                event.ignore()
                return
        super(Viewer2D, self).closeEvent(event)

    def shutdown(self):
        """Release everything this editor holds before the widget is deleted.

        Stops polling timers, removes the application-wide shortcut filter,
        cancels any in-flight mouse gesture and drops the design data (history,
        board solid, footprints, routed copper) so nothing keeps the old
        session alive after the window or tab has been closed.
        """
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        self.close_handler = None
        for name in ("_modified_timer", "_board_refresh_timer", "_ratsnest_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass
        self._set_app_filter(False)
        try:
            if getattr(self, "_move_session", None) is not None:
                self.cancel_move_selected()
            self.canvas.cancel_interaction(restore=False)
        except (AttributeError, RuntimeError):
            pass
        try:
            self.routing = None
            self.clear_history()
            self.clear(keep_design_path=False)
        except (AttributeError, RuntimeError):
            pass
        self.board_solid = None
        self._board_outline_cache = {}
        self._routing_cache = None
        self.netlist = None

    def minimumSizeHint(self):
        # A custom editor canvas can always become smaller; returning the sum
        # of native toolbar/stacked-page minimum hints here makes QMdiArea add
        # a horizontal scrollbar instead of fitting the maximized tab.
        return QtCore.QSize(0, 0)

    # -- construction ----------------------------------------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        split = QtWidgets.QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)
        split.addWidget(self._build_sidebar())

        # 2D and 3D are two views of the same in-memory editor state.  A
        # QStackedWidget avoids creating a FreeCAD document or another tab.
        self.view_stack = QtWidgets.QStackedWidget()
        self.view_stack.setMinimumSize(0, 0)
        self.view_stack.setSizePolicy(SIZE_IGNORED, SIZE_EXPANDING)
        self.page_2d = QtWidgets.QWidget()
        self.page_2d.setMinimumSize(0, 0)
        self.page_2d.setSizePolicy(SIZE_IGNORED, SIZE_EXPANDING)
        vlayout = QtWidgets.QVBoxLayout(self.page_2d)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(0)
        self.canvas = FootprintCanvas()
        self.canvas.set_crosshair_mode(
            self._viewer_options.crosshair_mode, self._viewer_options.crosshair_size_px)
        vlayout.addWidget(self.canvas, 1)
        vlayout.addWidget(self._build_layer_bar())
        vlayout.addWidget(self._build_status())
        self.view_stack.addWidget(self.page_2d)
        self.view3d = Board3DView(theme=self.theme)
        self.view3d.footprintSelected.connect(self._on_3d_selection)
        self.view_stack.addWidget(self.view3d)
        split.addWidget(self.view_stack, 1)

        # Keep one fixed-width tool rail on the right.  Earlier versions added
        # the Outline and Routing strips as two independent widgets and toggled
        # their visibility.  Qt consequently removed/inserted 46 px from the
        # layout every time the active design layer changed, making the canvas
        # visibly jump/flicker.  A stacked rail swaps pages without changing
        # layout geometry.
        self.tool_rail = self._build_copper_tool_panel()
        split.addWidget(self.tool_rail)
        root.addLayout(split, 1)

        self.canvas.cursorMoved.connect(self._on_cursor)
        self.canvas.hoverChanged.connect(self._on_hover)
        self.canvas.selectionChanged.connect(self._on_selection)
        self.canvas.padSelectionChanged.connect(self._on_pad_selection)
        self.canvas.zoomChanged.connect(self._on_zoom)
        self.canvas.rendererChanged.connect(self._on_2d_renderer_changed)
        self.canvas.footprintMoved.connect(self.footprintMoved.emit)
        self.canvas.footprintsMoving.connect(self._on_footprints_moving)
        self.canvas.footprintsMoved.connect(self._on_footprints_moved)
        self.canvas.selectionMoved.connect(self._on_selection_moved)
        self.canvas.copperSelectionChanged.connect(self._on_copper_selection)
        self.canvas.routePointerMoved.connect(self._on_route_pointer)
        self.canvas.routeClicked.connect(self._on_route_click)
        self.canvas.routeDoubleClicked.connect(self._on_route_double_click)
        self.canvas.movePointerMoved.connect(self._on_move_pointer)
        self.canvas.moveClicked.connect(self._on_move_click)
        self.canvas.moveCancelled.connect(self.cancel_move_selected)
        self.canvas.copperDragStarted.connect(self._begin_copper_drag)
        self.canvas.copperDragPointerMoved.connect(self._on_copper_drag_pointer)
        self.canvas.copperDragFinished.connect(self._finish_copper_drag)
        self._install_edit_shortcuts()

    def _install_edit_shortcuts(self):
        """Reserve editor keys while focus is anywhere inside this viewer.

        FreeCAD already owns global Ctrl+Z/Ctrl+Y actions.  A normal QShortcut
        on the child viewer can become ambiguous with those main-window
        shortcuts, especially in MDI tab mode.  An application event filter
        accepts ShortcutOverride only while this viewer has focus, then handles
        the matching KeyPress itself.  Outside the viewer FreeCAD keeps its
        normal undo/redo shortcuts untouched.
        """
        # The filter itself is installed only while the viewer is shown (see
        # showEvent/hideEvent): a hidden or closed editor must not inspect every
        # event FreeCAD processes.
        self._app_filter_installed = False

    def _set_app_filter(self, enabled):
        app = QtWidgets.QApplication.instance()
        if app is None or enabled == self._app_filter_installed:
            return
        try:
            if enabled:
                app.installEventFilter(self)
            else:
                app.removeEventFilter(self)
            self._app_filter_installed = bool(enabled)
        except RuntimeError:
            pass

    def showEvent(self, event):
        super(Viewer2D, self).showEvent(event)
        if not getattr(self, "_shut_down", False):
            self._set_app_filter(True)

    def hideEvent(self, event):
        super(Viewer2D, self).hideEvent(event)
        self._set_app_filter(False)

    @staticmethod
    def _event_type(name):
        value = getattr(QtCore.QEvent, name, None)
        if value is None and hasattr(QtCore.QEvent, "Type"):
            value = getattr(QtCore.QEvent.Type, name, None)
        return value

    def _focus_is_inside_viewer(self):
        focus = QtWidgets.QApplication.focusWidget()
        return focus is self or (focus is not None and self.isAncestorOf(focus))

    def _focus_is_editor_control(self):
        focus = QtWidgets.QApplication.focusWidget()
        return isinstance(focus, (QtWidgets.QLineEdit,
                                  QtWidgets.QAbstractSpinBox,
                                  QtWidgets.QComboBox))

    def _edit_action_for_key(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)
        meta = bool(mods & Qt.MetaModifier)
        text = str(getattr(event, "text", lambda: "")() or "")

        # File shortcuts must be intercepted before FreeCAD's main window gets
        # them.  QAction shortcuts attached to an embedded/MDI child lose to
        # FreeCAD's application/window actions, so reserve Ctrl+O/Ctrl+S here
        # just like the editor's Ctrl+Z/Ctrl+Y handling.  These remain active
        # even when focus is inside a property editor.
        if ctrl and not alt and not meta and key == Qt.Key_O and not shift:
            return self.open_design_dialog
        if ctrl and not alt and not meta and key == Qt.Key_S:
            return self.save_design_as if shift else self.save_design

        # Let line edits/spin boxes keep their native text-editing keys.  This
        # prevents Delete from removing a footprint/track while the user is
        # editing a numeric property, and lets Ctrl+Z undo typed text there.
        if self._focus_is_editor_control():
            return None

        # An explicit M move owns the pointer until it is placed or cancelled.
        # Esc must cancel before any other editor tool sees it.
        if self.view_mode == "2d" and self._move_session is not None:
            if not ctrl and not shift and not alt and not meta and key == Qt.Key_Escape:
                return self.cancel_move_selected
            if not ctrl and not shift and not alt and not meta and key in (Qt.Key_Return, Qt.Key_Enter):
                return self.commit_move_at_cursor

        if self.view_mode == "2d" and self._copper_drag_session is not None:
            if not ctrl and not shift and not alt and not meta and key == Qt.Key_Escape:
                return self.cancel_copper_drag

        # Layer navigation mirrors PCB editors: +/- walk adjacent reference
        # planes while Page Up/Down jump straight to top/bottom copper.  The
        # bottom bar is B.Cu -> ... -> F.Cu, so + walks right/up and - left/down.
        if self.view_mode == "2d" and not ctrl and not alt and not meta:
            page_up = getattr(Qt, "Key_PageUp", None)
            page_down = getattr(Qt, "Key_PageDown", None)
            if key in (Qt.Key_Plus, Qt.Key_Equal) and (text == "+" or key == Qt.Key_Plus):
                return lambda: self._cycle_active_layer(+1)
            if key == Qt.Key_Minus:
                return lambda: self._cycle_active_layer(-1)
            if page_up is not None and key == page_up:
                return lambda: self._select_extreme_layer(top=True)
            if page_down is not None and key == page_down:
                return lambda: self._select_extreme_layer(top=False)

        # KiCad PCB Editor net highlighting: ` highlights the selected copper
        # object(s), or the copper object under the cursor when nothing copper
        # is selected.  ~ clears it and Ctrl+` toggles the current highlight.
        if self.view_mode == "2d" and not alt and not meta:
            quote_key = getattr(Qt, "Key_QuoteLeft", None)
            tilde_key = getattr(Qt, "Key_AsciiTilde", None)
            is_quote = (text == "`" or (quote_key is not None and key == quote_key and not shift))
            is_tilde = (text == "~" or (tilde_key is not None and key == tilde_key))
            if is_tilde and not ctrl:
                return self._clear_net_highlight
            if is_quote:
                return self._toggle_net_highlight if ctrl else self._highlight_net_hotkey

        # Plain 2/3 (and Ctrl+2/Ctrl+3) switch views, mirroring the reference
        # app.  These shortcuts are local to the viewer, so FreeCAD keeps its
        # own keys when focus is elsewhere.
        if not shift and not alt and not meta and key == Qt.Key_2:
            return lambda: self.set_view_mode("2d")
        if not shift and not alt and not meta and key == Qt.Key_3:
            return lambda: self.set_view_mode("3d")

        if ctrl and not alt and not meta and key == Qt.Key_Z:
            return self.redo if shift else self.undo
        if ctrl and not shift and not alt and not meta and key == Qt.Key_Y:
            return self.redo

        # KiCad-like router shortcuts.  Routing is a 2D copper-layer tool;
        # object/grid modifiers themselves are read from the live mouse event.
        if self.view_mode == "2d" and not ctrl and not alt and not meta:
            if key == Qt.Key_X:
                return self._toggle_route_tool
            if self.route_tool == "route":
                if key == Qt.Key_Escape:
                    return self._route_escape
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_End):
                    return self._route_enter
                if key == Qt.Key_Backspace:
                    return self._route_backspace
                if key == Qt.Key_V:
                    return self._toggle_via_pending
                if key == Qt.Key_Slash:
                    return self._toggle_route_posture

        if (self.view_mode == "2d" and not ctrl and not shift and not alt and not meta
                and key == Qt.Key_Escape and self._net_highlight_enabled):
            return self._clear_net_highlight

        if not ctrl and not alt and not meta and key == Qt.Key_Delete:
            return self.delete_selected
        if not ctrl and not shift and not alt and not meta and key == Qt.Key_M:
            return self.begin_move_selected
        if not ctrl and not shift and not alt and not meta and key == Qt.Key_U:
            return self.select_complete_track
        if not ctrl and not alt and not meta and key == Qt.Key_R:
            return (lambda: self.rotate_selected(clockwise=shift))
        return None

    def _edit_key_allows_repeat(self, event):
        """Only history traversal repeats while a key is held down.

        Repeating R/Delete/Open/Save would be destructive or annoying, while
        desktop editors conventionally let Ctrl+Z/Ctrl+Y walk history at the
        operating system's key-repeat rate.
        """
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        alt = bool(mods & Qt.AltModifier)
        meta = bool(mods & Qt.MetaModifier)
        return bool(ctrl and not alt and not meta and key in (Qt.Key_Z, Qt.Key_Y))

    def eventFilter(self, watched, event):
        if getattr(self, "_shut_down", False):
            return False
        if self._focus_is_inside_viewer():
            etype = event.type()
            override_type = self._event_type("ShortcutOverride")
            key_type = self._event_type("KeyPress")
            action = self._edit_action_for_key(event) if etype in (override_type, key_type) else None
            if action is not None:
                if etype == override_type:
                    # Prevent FreeCAD's main-window shortcut from winning this
                    # key while the custom editor is active.
                    event.accept()
                    return True
                auto_repeat = bool(getattr(event, "isAutoRepeat", lambda: False)())
                if not auto_repeat or self._edit_key_allows_repeat(event):
                    action()
                event.accept()
                return True
        return super(Viewer2D, self).eventFilter(watched, event)

    def _build_header(self):
        header = QtWidgets.QWidget()
        header.setObjectName("elec3dHeader")
        header.setMinimumWidth(0)
        header.setSizePolicy(SIZE_EXPANDING, SIZE_PREFERRED)
        row = QtWidgets.QHBoxLayout(header)
        row.setContentsMargins(14, 8, 12, 8)
        row.setSpacing(10)

        self.title = QtWidgets.QLabel("\u2014")
        self.title.setObjectName("title")
        # Keep the active footprint/reference visible.  The v0.7.9 MDI fix
        # made this label horizontally Ignored, so Qt could collapse it to zero
        # while preserving the controls on the right.  Give it a small real
        # minimum and let it yield only after the selected name is readable.
        self.title.setMinimumWidth(96)
        self.title.setMaximumWidth(260)
        self.title.setSizePolicy(SIZE_PREFERRED, SIZE_PREFERRED)
        row.addWidget(self.title)

        self.tag = QtWidgets.QLabel("")
        self.tag.setObjectName("tag")
        self.tag.hide()
        row.addWidget(self.tag)

        # Keep the add-on version permanently visible in the main viewer so
        # screenshots and bug reports can be matched to an exact build.
        self.version_label = QtWidgets.QLabel("v%s" % __version__)
        self.version_label.setObjectName("faint")
        self.version_label.setToolTip("Elec3D add-on version %s" % __version__)
        self.version_label.setMinimumWidth(0)
        row.addWidget(self.version_label)
        row.addStretch(1)

        # FreeCAD host actions belong in the viewer itself rather than in a
        # separate workbench toolbar. Standalone preview windows omit them.
        if self.hosted:
            # One menu instead of five buttons.  The header had grown to the
            # point where the view controls -- the things used constantly --
            # were competing for space with import actions used once per
            # session.  Frequency of use decides what stays a button.
            self.menu_file = QtWidgets.QMenu(self)
            self.act_new_project = self.menu_file.addAction("Create a new project\u2026")
            self.act_new_project.setToolTip(
                "Create a project folder containing .e3d_pro, .e3d_pcb, Footprints and Models")
            self.act_new_project.triggered.connect(self.create_new_project)

            self.act_open_design = self.menu_file.addAction("Open\u2026")
            self.act_open_design.setToolTip("Open a Elec3D project (.e3d_pro) or board (.e3d_pcb)")
            self.act_open_design.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Open"))
            self.act_open_design.triggered.connect(self.open_design_dialog)

            # Keep a compact MRU list of project entry files in FreeCAD's
            # persistent add-on preferences.  Only projects belong here: loose
            # .e3d_pcb files intentionally continue through Open... so their
            # project-creation prompt is not bypassed.
            self.menu_recent_projects = self.menu_file.addMenu("Open Recent")
            self.menu_recent_projects.setToolTip("Open one of the three most recently used Elec3D projects")
            self.menu_recent_projects.aboutToShow.connect(self._rebuild_recent_projects_menu)
            self._rebuild_recent_projects_menu()

            self.act_save_design = self.menu_file.addAction("Save")
            self.act_save_design.setToolTip("Save the current project; unprojected work prompts for project creation")
            self.act_save_design.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Save"))
            self.act_save_design.triggered.connect(self.save_design)

            self.act_save_design_as = self.menu_file.addAction("Save As\u2026")
            self.act_save_design_as.setToolTip("Save the current design as a new project in another folder/name")
            self.act_save_design_as.triggered.connect(self.save_design_as)

            self.act_close_project = self.menu_file.addAction("Close Project")
            self.act_close_project.setToolTip("Close the current project, prompting to save unsaved changes")
            self.act_close_project.triggered.connect(self.close_project)

            self.menu_file.addSeparator()
            self.act_clear_design = self.menu_file.addAction("Clear design")
            self.act_clear_design.setToolTip(
                "Clear footprints, routed copper, netlist, board solid, layers and stackup")
            self.act_clear_design.triggered.connect(self.clear_design)

            self.menu_file.addSeparator()
            self.act_netlist = self.menu_file.addAction("Import netlist\u2026")
            self.act_netlist.setToolTip(
                "Import a KiCad .net file with the four-step wizard")
            self.act_netlist.triggered.connect(
                lambda: self.hostActionRequested.emit("netlist"))

            self.act_import = self.menu_file.addAction("Import footprints\u2026")
            self.act_import.setToolTip("Import one or more KiCad footprints")
            self.act_import.triggered.connect(
                lambda: self.hostActionRequested.emit("import"))

            self.act_reload = self.menu_file.addAction("Reload selected")
            self.act_reload.setToolTip("Reload the selected footprint from disk")
            self.act_reload.setEnabled(False)
            self.act_reload.triggered.connect(
                lambda: self.hostActionRequested.emit("reload"))

            self.menu_file.addSeparator()
            self.act_board_step = self.menu_file.addAction("Import board solid (STEP)\u2026")
            self.act_board_step.setToolTip(
                "Use a STEP solid as the board; every copper layer's boundary is its cross-section")
            self.act_board_step.triggered.connect(self.import_board_step_dialog)

            self.act_board_freecad = self.menu_file.addAction("Board solid from FreeCAD\u2026")
            self.act_board_freecad.setToolTip(
                "Use a solid that is open in FreeCAD (the current selection is offered first)")
            self.act_board_freecad.triggered.connect(self.import_board_freecad_dialog)

            self.menu_file.addSeparator()
            self.act_models = self.menu_file.addAction("3D models folder\u2026")
            self.act_models.setToolTip(
                "Choose the folder the .step files referenced by these "
                "footprints live in")
            self.act_models.triggered.connect(self.choose_models_folder)

            self.act_model_one = self.menu_file.addAction(
                "Load 3D model for selection\u2026")
            self.act_model_one.setToolTip(
                "Pick a STEP file for the selected footprint")
            self.act_model_one.setEnabled(False)
            self.act_model_one.triggered.connect(self.choose_model_for_selection)

            self.menu_file.addSeparator()
            self.act_export_step = self.menu_file.addAction("Export STEP…")
            self.act_export_step.setToolTip(
                "Export tracks/vias, pads and placed component models to a STEP assembly")
            self.act_export_step.triggered.connect(self.export_step_dialog)

            self.act_freecad = self.menu_file.addAction("Export to FreeCAD…")
            self.act_freecad.setToolTip(
                "Create or update the linked PCB assembly in a separate FreeCAD document tab")
            self.act_freecad.triggered.connect(self.export_freecad_dialog)

            self.menu_edit = QtWidgets.QMenu(self)
            self.act_undo_menu = self.menu_edit.addAction("Undo")
            self.act_undo_menu.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Undo"))
            self.act_undo_menu.triggered.connect(self.undo)
            self.act_redo_menu = self.menu_edit.addAction("Redo")
            self.act_redo_menu.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Redo"))
            self.act_redo_menu.triggered.connect(self.redo)
            self.menu_edit.addSeparator()
            self.act_design_rules = self.menu_edit.addAction("Design Rules…")
            self.act_design_rules.setToolTip("PCB clearance rules used by interactive DRC")
            self.act_design_rules.triggered.connect(self.show_design_rules_dialog)
            self.act_stackup_dialog = self.menu_edit.addAction("Stackup…")
            self.act_stackup_dialog.setToolTip("Edit copper thicknesses, separations and the bottom margin")
            self.act_stackup_dialog.triggered.connect(self.show_stackup_dialog)
            self.menu_edit.addSeparator()
            self.act_options = self.menu_edit.addAction("Preferences…")
            self.act_options.setToolTip("Elec3D preferences")
            self.act_options.triggered.connect(
                lambda: self.hostActionRequested.emit("options"))

            self.menu_tools = QtWidgets.QMenu(self)
            self.act_drc_check = self.menu_tools.addAction("DRC Check…")
            self.act_drc_check.setToolTip("Run a full-board design-rule check and display all violations")
            self.act_drc_check.triggered.connect(self.run_drc_check)

            # Use a normal push button and open the menu ourselves.  Native
            # QToolButton popup indicators vary considerably between FreeCAD
            # styles (the Windows style can look like two tiny chevrons sitting
            # on top of each other).  A single text caret is deterministic, and
            # because no Qt menu-indicator subcontrol is involved there is
            # nothing for the host style to draw on top of it.
            self.btn_file = QtWidgets.QPushButton("File  \u25be")
            self.btn_file.setObjectName("fileMenuButton")
            self.btn_file.setToolTip("Projects, import/export, netlist and 3D models")
            self.btn_file.clicked.connect(self._show_file_menu)
            row.addWidget(self.btn_file)

            self.btn_edit = QtWidgets.QPushButton("Edit  ▾")
            self.btn_edit.setObjectName("fileMenuButton")
            self.btn_edit.setToolTip("Undo/redo, design rules, stackup and preferences")
            self.btn_edit.clicked.connect(self._show_edit_menu)
            row.addWidget(self.btn_edit)

            self.btn_tools = QtWidgets.QPushButton("Tools  ▾")
            self.btn_tools.setObjectName("fileMenuButton")
            self.btn_tools.setToolTip("Design verification tools")
            self.btn_tools.clicked.connect(self._show_tools_menu)
            row.addWidget(self.btn_tools)

        self.btn_2d = QtWidgets.QPushButton("2D")
        self.btn_2d.setCheckable(True)
        self.btn_2d.setChecked(True)
        self.btn_2d.setToolTip("2D layout view  (2)")
        self.btn_2d.clicked.connect(lambda: self.set_view_mode("2d"))
        row.addWidget(self.btn_2d)

        self.btn_3d = QtWidgets.QPushButton("3D")
        self.btn_3d.setCheckable(True)
        self.btn_3d.setToolTip("3D board view  (3)")
        self.btn_3d.clicked.connect(lambda: self.set_view_mode("3d"))
        row.addWidget(self.btn_3d)

        self.zoom_label = QtWidgets.QLabel("40 px/mm")
        self.zoom_label.setObjectName("faint")
        row.addWidget(self.zoom_label)

        self.btn_fit = QtWidgets.QPushButton("Fit")
        self.btn_fit.setToolTip("Zoom to fit  (F)")
        self.btn_fit.clicked.connect(self.canvas_fit)
        row.addWidget(self.btn_fit)

        self.btn_grid = QtWidgets.QPushButton("Grid")
        self.btn_grid.setCheckable(True)
        self.btn_grid.setChecked(True)
        self.btn_grid.setToolTip("Show the grid  (G)")
        self.btn_grid.toggled.connect(lambda on: self.canvas.set_grid(visible=on))
        row.addWidget(self.btn_grid)

        # Keep the action buttons readable.  v0.7.9 used QSizePolicy.Ignored
        # here to stop the MDI page from advertising a large minimum width.
        # With an expanding stretch immediately before these controls, Qt was
        # then free to allocate them almost zero width -- on a wide tab they
        # appeared as a row of clipped/sliver buttons at the far right.
        #
        # The *viewer* and its title are still shrinkable (minimumSizeHint() is
        # zero and the title uses Ignored), but buttons use their native
        # minimum/size hints.  This reserves enough room for their labels while
        # allowing the footprint title to yield space first.
        for button in (self.btn_2d, self.btn_3d, self.btn_fit, self.btn_grid):
            button.setMinimumWidth(0)
            button.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)
        self.zoom_label.setMinimumWidth(0)
        self.zoom_label.setSizePolicy(SIZE_PREFERRED, SIZE_PREFERRED)
        if self.hosted:
            self.btn_file.setMinimumWidth(0)
            self.btn_file.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)
            self.btn_edit.setMinimumWidth(0)
            self.btn_edit.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)
            self.btn_tools.setMinimumWidth(0)
            self.btn_tools.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)

        return header

    def _show_file_menu(self):
        """Open File menu with a small visual gap below the header button.

        QToolButton's native InstantPopup used to put the popup flush against
        the toolbar and delegated the arrow to FreeCAD's current QStyle.  The
        resulting indicator is inconsistent across Windows themes.  Manual
        popup placement gives us one clean caret and keeps the menu visually
        separated from the 2D/3D action row.
        """
        menu = getattr(self, "menu_file", None)
        button = getattr(self, "btn_file", None)
        if menu is None or button is None:
            return
        menu.adjustSize()
        pos = button.mapToGlobal(QtCore.QPoint(0, button.height() + 4))
        menu.popup(pos)

    def _show_edit_menu(self):
        menu = getattr(self, "menu_edit", None)
        button = getattr(self, "btn_edit", None)
        if menu is None or button is None:
            return
        menu.adjustSize()
        pos = button.mapToGlobal(QtCore.QPoint(0, button.height() + 4))
        menu.popup(pos)

    def _show_tools_menu(self):
        menu = getattr(self, "menu_tools", None)
        button = getattr(self, "btn_tools", None)
        if menu is None or button is None:
            return
        menu.adjustSize()
        pos = button.mapToGlobal(QtCore.QPoint(0, button.height() + 4))
        menu.popup(pos)

    def _collect_drc_violations(self):
        """Return every unique full-board DRC violation using live editor rules.

        Interactive routing deliberately stops at the first obstacle because it
        runs on every mouse move.  The explicit Tools -> DRC Check command can
        afford a complete sweep and reports all unique copper, courtyard and
        board-edge violations without modifying the design.
        """
        rules = self.design_rules
        order = self._layer_order()
        pads = list(self._routing_pads(include_hidden=True))
        tracks = list(self.tracks)
        vias = list(self.vias)
        issues = []
        seen = set()
        rule_labels = dict(RULE_LABELS)
        track_no = dict((track.uid, i + 1) for i, track in enumerate(tracks))
        via_no = dict((via.uid, i + 1) for i, via in enumerate(vias))

        def layer_name(layer_id):
            layer = self.stack.by_id(layer_id)
            return layer.name if layer is not None else str(layer_id or "?")

        def net_text(net):
            return str(net) if net not in (None, "") else "unassigned"

        def track_label(track):
            return "Track %d" % track_no.get(getattr(track, "uid", None), 0)

        def via_label(via):
            return "Via %d" % via_no.get(getattr(via, "uid", None), 0)

        def pad_label(pad):
            ref = str(pad.get("ref") or "?")
            pin = str(pad.get("pin") or "?")
            return "%s pad %s" % (ref, pin)

        def add(key, rule_key, objects, details):
            if key in seen:
                return
            seen.add(key)
            issues.append({
                "rule": rule_labels.get(rule_key, rule_key.replace("_", " ")),
                "objects": objects,
                "details": details,
            })

        # Copper-vs-copper/pad rules use the exact same different-net tests as
        # the live router.  Scanning both tracks and vias is intentional; the
        # normalized pair key below removes duplicates.
        for track in tracks:
            for hit in route_conflicts(track, pads, tracks, vias, order,
                                       active_net=track.net, rules=rules):
                kind = str(hit.get("kind") or "")
                obstacle = hit.get("object")
                if kind == "track" and obstacle is not None:
                    pair = tuple(sorted((str(track.uid), str(obstacle.uid))))
                    add(("track_track", pair), "track_track",
                        "%s ↔ %s" % (track_label(track), track_label(obstacle)),
                        "%s; nets %s / %s" % (layer_name(track.layer_id),
                                               net_text(track.net), net_text(obstacle.net)))
                elif kind == "via" and obstacle is not None:
                    pair = tuple(sorted((("track", str(track.uid)),
                                         ("via", str(obstacle.uid)))))
                    add(("track_via", pair), "track_via",
                        "%s ↔ %s" % (track_label(track), via_label(obstacle)),
                        "%s; nets %s / %s" % (layer_name(track.layer_id),
                                               net_text(track.net), net_text(obstacle.net)))
                elif kind == "pad" and isinstance(obstacle, dict):
                    pad_id = (str(obstacle.get("entry_uid") or ""), str(obstacle.get("pin") or ""),
                              str(obstacle.get("layer_id") or ""))
                    add(("track_pad", str(track.uid), pad_id), "track_pad",
                        "%s ↔ %s" % (track_label(track), pad_label(obstacle)),
                        "%s; nets %s / %s" % (layer_name(track.layer_id),
                                               net_text(track.net), net_text(obstacle.get("net"))))

        for via in vias:
            for hit in via_conflicts(via, pads, tracks, vias, order,
                                     active_net=via.net, rules=rules):
                kind = str(hit.get("kind") or "")
                obstacle = hit.get("object")
                if kind == "track" and obstacle is not None:
                    pair = tuple(sorted((("track", str(obstacle.uid)),
                                         ("via", str(via.uid)))))
                    add(("track_via", pair), "track_via",
                        "%s ↔ %s" % (track_label(obstacle), via_label(via)),
                        "%s; nets %s / %s" % (layer_name(obstacle.layer_id),
                                               net_text(obstacle.net), net_text(via.net)))
                elif kind == "via" and obstacle is not None:
                    pair = tuple(sorted((str(via.uid), str(obstacle.uid))))
                    add(("via_via", pair), "via_via",
                        "%s ↔ %s" % (via_label(via), via_label(obstacle)),
                        "nets %s / %s" % (net_text(via.net), net_text(obstacle.net)))
                elif kind == "pad" and isinstance(obstacle, dict):
                    pad_id = (str(obstacle.get("entry_uid") or ""), str(obstacle.get("pin") or ""),
                              str(obstacle.get("layer_id") or ""))
                    add(("via_pad", str(via.uid), pad_id), "via_pad",
                        "%s ↔ %s" % (via_label(via), pad_label(obstacle)),
                        "%s; nets %s / %s" % (layer_name(obstacle.get("layer_id")),
                                               net_text(via.net), net_text(obstacle.get("net"))))

        # Pad-to-pad placement clearance mirrors the existing interactive
        # component-placement DRC and uses rendered pad paths, including pad
        # rotation and rounded geometry, rather than footprint bounding boxes.
        pad_rows = []
        by_entry_pin = {}
        for entry in self._entries:
            item = entry.get("item")
            for pin, pad_item in getattr(item, "pad_items", {}).items():
                by_entry_pin[(str(entry.get("uid") or ""), str(pin))] = pad_item
        for pad in pads:
            key = (str(pad.get("entry_uid") or ""), str(pad.get("pin") or ""))
            pad_item = by_entry_pin.get(key)
            if pad_item is not None:
                pad_rows.append((pad, pad_item))
        pad_clearance = rules.get("pad_pad")
        for i, (first, first_item) in enumerate(pad_rows):
            for second, second_item in pad_rows[i + 1:]:
                if first.get("entry_uid") == second.get("entry_uid"):
                    continue
                if first.get("layer_id") != second.get("layer_id"):
                    continue
                if self._paths_with_clearance_intersect(
                        self._scene_shape_path(first_item),
                        self._scene_shape_path(second_item), pad_clearance):
                    a = (str(first.get("entry_uid") or ""), str(first.get("pin") or ""))
                    b = (str(second.get("entry_uid") or ""), str(second.get("pin") or ""))
                    add(("pad_pad", tuple(sorted((a, b)))), "pad_pad",
                        "%s ↔ %s" % (pad_label(first), pad_label(second)),
                        "%s; nets %s / %s" % (layer_name(first.get("layer_id")),
                                               net_text(first.get("net")), net_text(second.get("net"))))

        # Courtyard-to-courtyard placement checks are layer-local, matching the
        # editor's current physical interpretation of component placement.
        courtyard_clearance = rules.get("courtyard_courtyard")
        for i, first in enumerate(self._entries):
            layer_a = self.design_layer_of(first)
            if layer_a is None:
                continue
            rect_a = self._expanded_rect(self._courtyard_rect(first), courtyard_clearance)
            for second in self._entries[i + 1:]:
                layer_b = self.design_layer_of(second)
                if layer_b is None or layer_b.id != layer_a.id:
                    continue
                if rect_a.intersects(self._courtyard_rect(second)):
                    uid_a, uid_b = str(first.get("uid") or ""), str(second.get("uid") or "")
                    ref_a = str(first.get("ref") or getattr(first.get("fp"), "name", "?"))
                    ref_b = str(second.get("ref") or getattr(second.get("fp"), "name", "?"))
                    add(("courtyard_courtyard", tuple(sorted((uid_a, uid_b)))),
                        "courtyard_courtyard", "%s ↔ %s" % (ref_a, ref_b), layer_a.name)

        # Board-edge rules are checked independently per copper reference layer.
        for track in tracks:
            outlines = self._outline_models_for_layer(track.layer_id)
            if route_outline_conflicts(track, outlines, rules=rules):
                add(("outline_track", str(track.uid), str(track.layer_id)),
                    "outline_track", track_label(track), layer_name(track.layer_id))

        for via in vias:
            for layer_id in order:
                if not via_spans_layer(via, layer_id, order):
                    continue
                outlines = self._outline_models_for_layer(layer_id)
                if via_outline_conflicts(via, outlines, rules=rules):
                    add(("outline_via", str(via.uid), str(layer_id)),
                        "outline_via", via_label(via), layer_name(layer_id))

        for pad in pads:
            layer_id = pad.get("layer_id")
            outlines = self._outline_models_for_layer(layer_id)
            if not outlines:
                continue
            probe = Via(x=pad.get("x", 0.0), y=pad.get("y", 0.0),
                        diameter=max(float(pad.get("w", 0.0)), float(pad.get("h", 0.0))),
                        start_layer_id=layer_id, end_layer_id=layer_id)
            if via_outline_conflicts(
                    probe, outlines, rules={"outline_via": rules.get("outline_pad")}):
                pad_id = (str(pad.get("entry_uid") or ""), str(pad.get("pin") or ""))
                add(("outline_pad", pad_id, str(layer_id)),
                    "outline_pad", pad_label(pad), layer_name(layer_id))

        for entry in self._entries:
            layer = self.design_layer_of(entry)
            if layer is None:
                continue
            outlines = self._outline_models_for_layer(layer.id)
            if not outlines:
                continue
            rect = self._courtyard_rect(entry)
            corners = [(rect.left(), rect.top()), (rect.right(), rect.top()),
                       (rect.right(), rect.bottom()), (rect.left(), rect.bottom()),
                       (rect.left(), rect.top())]
            boundary = Track(layer_id=layer.id, width=0.0, points=corners)
            if route_outline_conflicts(
                    boundary, outlines,
                    rules={"outline_track": rules.get("outline_courtyard")}):
                ref = str(entry.get("ref") or getattr(entry.get("fp"), "name", "?"))
                add(("outline_courtyard", str(entry.get("uid") or ""), str(layer.id)),
                    "outline_courtyard", ref, layer.name)

        issues.sort(key=lambda issue: (issue["rule"], issue["objects"], issue["details"]))
        return issues

    def _show_drc_results(self, issues):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("DRC Check")
        dialog.setModal(True)
        dialog.resize(760, 470)
        outer = QtWidgets.QVBoxLayout(dialog)

        count = len(issues or [])
        if count:
            summary = QtWidgets.QLabel("%d design-rule violation%s found." %
                                       (count, "" if count == 1 else "s"))
        else:
            summary = QtWidgets.QLabel("No design-rule violations found.")
        outer.addWidget(summary)

        table = QtWidgets.QTreeWidget(dialog)
        table.setObjectName("drcResults")
        table.setRootIsDecorated(False)
        table.setAlternatingRowColors(True)
        table.setHeaderLabels(["Rule", "Objects", "Layer / details"])
        table.setColumnWidth(0, 180)
        table.setColumnWidth(1, 260)
        table.header().setStretchLastSection(True)
        for issue in issues or []:
            QtWidgets.QTreeWidgetItem(table, [
                str(issue.get("rule") or ""),
                str(issue.get("objects") or ""),
                str(issue.get("details") or ""),
            ])
        outer.addWidget(table, 1)

        buttons = QtWidgets.QDialogButtonBox(
            enum(QtWidgets.QDialogButtonBox, "StandardButton", "Close"), dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        outer.addWidget(buttons)
        exec_dialog(dialog)

    def run_drc_check(self, _checked=False):
        """Run a non-destructive whole-board DRC and show all violations."""
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before running DRC")
            return None
        self._begin_routing_cache()
        try:
            issues = self._collect_drc_violations()
        finally:
            self._end_routing_cache()
        self._show_drc_results(issues)
        self._flash_status("DRC: %d violation%s" %
                           (len(issues), "" if len(issues) == 1 else "s"))
        return issues

    def show_design_rules_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Design Rules")
        dialog.setModal(True)
        dialog.resize(430, 480)
        outer = QtWidgets.QVBoxLayout(dialog)
        note = QtWidgets.QLabel(
            "Clearances are edge-to-edge in millimetres. Routing uses these rules live; "
            "different-net copper and the board-solid edge act as hard barriers.")
        note.setWordWrap(True); note.setObjectName("faint")
        outer.addWidget(note)
        form_host = QtWidgets.QWidget(dialog)
        form = QtWidgets.QFormLayout(form_host)
        form.setContentsMargins(0, 6, 0, 6)
        form.setHorizontalSpacing(14); form.setVerticalSpacing(7)
        controls = {}
        for key, label in RULE_LABELS:
            spin = NoWheelDoubleSpinBox(dialog)
            spin.setDecimals(3); spin.setRange(0.0, 20.0); spin.setSingleStep(0.05)
            spin.setSuffix(" mm")
            spin.setValue(self.design_rules.get(key))
            spin.setMinimumWidth(120)
            controls[key] = spin
            form.addRow(label, spin)
        outer.addWidget(form_host)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if not exec_dialog(dialog):
            return False
        changed = False
        for key, spin in controls.items():
            changed = self.design_rules.set(key, spin.value()) or changed
        if changed:
            self._update_route_preview()
            self.refresh_modified_state()
            self._flash_status("Design rules updated")
        return changed

    def show_stackup_dialog(self):
        """Edit the physical stack-up against the imported board solid.

        Reference planes are listed top to bottom.  ``z = 0`` is the XY plane
        through the solid's lowest point and the bottom margin is measured from
        there.  Each copper row shows its live plane height and whether that
        plane actually cuts the solid, so a plane that would end up above the
        board is visible before the dialog is accepted.
        """
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Stackup")
        dialog.setModal(True)
        dialog.resize(460, 440)
        outer = QtWidgets.QVBoxLayout(dialog)
        solid = self.board_solid
        if solid is not None:
            sx, sy, sz = solid.size()
            head = "Board solid: %s  ·  %.2f × %.2f × %.3f mm" % (solid.label, sx, sy, sz)
        else:
            head = "No board solid imported — the stack is measured from z = 0."
        note = QtWidgets.QLabel(
            head + "\nReference planes are listed top to bottom. z = 0 is the lowest point "
            "of the solid; the bottom margin is measured from it. Copper is etched "
            "below its plane. Layers are added/removed from the bottom layer bar.")
        note.setWordWrap(True); note.setObjectName("faint")
        outer.addWidget(note)
        form_host = QtWidgets.QWidget(dialog)
        grid = QtWidgets.QGridLayout(form_host)
        grid.setContentsMargins(0, 6, 0, 6)
        grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        for col, text in enumerate(("", "value", "plane z", "")):
            if text:
                label = QtWidgets.QLabel(text); label.setObjectName("faint")
                grid.addWidget(label, 0, col)
        values = []
        z_labels = {}
        status_labels = {}

        def spin_for(value, step=0.1, minimum=0.0):
            spin = NoWheelDoubleSpinBox(dialog)
            spin.setDecimals(3); spin.setRange(minimum, 1000.0); spin.setSingleStep(step)
            spin.setSuffix(" mm"); spin.setValue(float(value)); spin.setMinimumWidth(110)
            return spin

        def layer_label(text, color):
            host = QtWidgets.QWidget(dialog)
            row = QtWidgets.QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
            swatch = QtWidgets.QLabel(host)
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(
                "background: %s; border: 1px solid rgba(0,0,0,.35); border-radius: 1px;"
                % str(color))
            row.addWidget(swatch)
            row.addWidget(QtWidgets.QLabel(text, host))
            row.addStretch(1)
            return host

        copper = self.stack.copper_top_down()
        line = 1
        sep_spins = {}
        for index, layer in enumerate(copper):
            cu = spin_for(self.stack.copper_thickness(layer.id), 0.005, 0.001)
            cu.setToolTip("Copper etched below the %s reference plane" % layer.name)
            grid.addWidget(layer_label("%s copper" % layer.name, layer.color), line, 0)
            grid.addWidget(cu, line, 1)
            z_label = QtWidgets.QLabel(""); z_label.setObjectName("dim")
            grid.addWidget(z_label, line, 2)
            status = QtWidgets.QLabel("")
            grid.addWidget(status, line, 3)
            z_labels[layer.id] = z_label
            status_labels[layer.id] = status
            values.append(("copper", layer.id, cu))
            line += 1
            if index + 1 < len(copper):
                lower = copper[index + 1]
                sep = spin_for(self.stack.separation(lower.id, layer.id), 0.1, 0.0)
                sep.setToolTip("Distance between the %s and %s reference planes" % (layer.name, lower.name))
                grid.addWidget(QtWidgets.QLabel("   %s \u2194 %s" % (layer.name, lower.name)), line, 0)
                grid.addWidget(sep, line, 1)
                values.append(("separation", (lower.id, layer.id), sep))
                sep_spins[(lower.id, layer.id)] = sep
                line += 1
        bottom = spin_for(self.stack.bottom_margin)
        bottom.setToolTip("Height of the bottom copper reference plane above the solid's lowest point")
        grid.addWidget(QtWidgets.QLabel("bottom margin"), line, 0)
        grid.addWidget(bottom, line, 1)
        zero = QtWidgets.QLabel("from z = 0"); zero.setObjectName("faint")
        grid.addWidget(zero, line, 2)
        values.append(("bottom_margin", None, bottom))
        grid.setColumnStretch(0, 1)
        outer.addWidget(form_host)
        summary = QtWidgets.QLabel("")
        summary.setObjectName("faint"); summary.setWordWrap(True)
        outer.addWidget(summary)

        def refresh_preview(*_args):
            z = float(bottom.value())
            heights = {}
            bottom_up = list(reversed(copper))
            for i, layer in enumerate(bottom_up):
                heights[layer.id] = z
                if i + 1 < len(bottom_up):
                    spin = sep_spins.get((layer.id, bottom_up[i + 1].id))
                    z += float(spin.value()) if spin is not None else 0.0
            for layer in copper:
                hz = heights.get(layer.id, 0.0)
                z_labels[layer.id].setText("%.3f mm" % hz)
                text, role = self._board_layer_status(hz)
                status_labels[layer.id].setText(text)
                status_labels[layer.id].setObjectName(role)
                status_labels[layer.id].style().unpolish(status_labels[layer.id])
                status_labels[layer.id].style().polish(status_labels[layer.id])
            top = max(heights.values()) if heights else float(bottom.value())
            if solid is not None:
                summary.setText("Top reference plane at %.3f mm  ·  solid height %.3f mm"
                                % (top, solid.height))
            else:
                summary.setText("Top reference plane at %.3f mm" % top)

        for _kind, _target, spin in values:
            spin.valueChanged.connect(refresh_preview)
        refresh_preview()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if not exec_dialog(dialog):
            return False
        changed = False
        for kind, target, spin in values:
            if kind == "bottom_margin":
                changed = self.stack.set_bottom_margin(spin.value()) or changed
            elif kind == "copper":
                changed = self.stack.set_copper_thickness(target, spin.value()) or changed
            elif kind == "separation":
                changed = self.stack.set_separation(target[0], target[1], spin.value()) or changed
        if changed:
            self._on_stack_geometry_changed()
            self._flash_status("Stackup updated")
        return changed

    def _board_layer_status(self, z):
        """Short status text and label role for a reference plane at ``z``."""
        solid = self.board_solid
        if solid is None:
            return "no solid", "faint"
        loops = solid.section(z)
        if not loops:
            return ("\u26a0 above solid" if z > solid.height else "\u26a0 outside solid"), "layerBad"
        return "\u2713 %d loop%s" % (len(loops), "" if len(loops) == 1 else "s"), "layerOk"

    def _section_expanded(self, key):
        defaults = getattr(self._viewer_options, "sidebar_expanded", {})
        return bool(defaults.get(key, True))

    def _reorder_sidebar_sections(self, order):
        """Apply the persisted order without recreating any sidebar widgets.

        The conditional 3D-model-offset section follows Properties as an
        attached editor block; it is intentionally not a user-orderable
        top-level group because it only exists while a 3D footprint is active.
        """
        layout = getattr(self, "_sidebar_layout", None)
        sections = getattr(self, "_sidebar_sections", None)
        if layout is None or not sections:
            return
        order = normalize_sidebar_order(order)
        widgets = list(sections.values()) + [self.sec_model_adjust]
        for widget in widgets:
            layout.removeWidget(widget)
        insert_at = 0
        for key in order:
            section = sections[key]
            layout.insertWidget(insert_at, section)
            insert_at += 1
            if key == "properties":
                layout.insertWidget(insert_at, self.sec_model_adjust)
                insert_at += 1

    def apply_ui_preferences(self):
        """Reload sidebar order/collapse defaults and 2D cursor preference."""
        try:
            from . import get_options
            self._viewer_options = get_options()
        except Exception:
            return
        for key, section in getattr(self, "_sidebar_sections", {}).items():
            section.set_expanded(self._section_expanded(key))
        self._reorder_sidebar_sections(self._viewer_options.sidebar_order)
        if hasattr(self, "canvas"):
            self.canvas.set_crosshair_mode(
                self._viewer_options.crosshair_mode, self._viewer_options.crosshair_size_px)
        self.drag_keep_45 = bool(getattr(self._viewer_options, "drag_keep_45", True))
        self.route_walkaround = bool(
            getattr(self._viewer_options, "route_walkaround", True))
        self.updateGeometry()
        self.update()

    def _build_sidebar(self):
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("elec3dSidebar")
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(SIDEBAR_WIDTH)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Keep a fixed visual sidebar width.  With widgetResizable=True Qt still
        # honours the child widget's minimum-size hint; a wide row (notably the
        # Manual 3D-offset buttons) could therefore make ``inner`` wider than
        # the viewport even though the horizontal scrollbar is disabled.  The
        # result looked as if the tab expanded while the right side of the
        # controls simply vanished.  Ignoring horizontal size hints makes the
        # child follow the viewport width instead.
        self.sidebar_scroll = scroll

        inner = QtWidgets.QWidget()
        inner.setObjectName("elec3dSidebar")
        inner.setMinimumWidth(0)
        inner.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        self.sidebar_inner = inner
        layout = QtWidgets.QVBoxLayout(inner)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(12)

        self.sec_footprints = Section("Footprints", collapsible=True, expanded=self._section_expanded("footprints"))
        self.fp_empty = QtWidgets.QLabel("nothing loaded yet")
        self.fp_empty.setObjectName("faint")
        self.sec_footprints.add(self.fp_empty)
        layout.addWidget(self.sec_footprints)

        self.sec_props = Section("Properties", collapsible=True, expanded=self._section_expanded("properties"))
        self.prop_kind = QtWidgets.QLabel("No selection")
        self.prop_kind.setObjectName("kind")
        self.sec_props.add(self.prop_kind)

        # Basic Properties holds the *editable* controls; Fields below holds the
        # read-only detail.  Splitting them keeps the one control a user can act
        # on from being buried in a dozen informational rows.
        self.sec_props.add(GroupTitle("Basic Properties"))

        self.prop_layer_combo = NoWheelComboBox()
        self.prop_layer_combo.setToolTip("Design layer this footprint sits on")
        self.prop_layer_combo.setMaximumWidth(120)
        self.prop_layer_combo.currentIndexChanged.connect(self._on_property_layer)
        self.prop_layer_row = ControlRow("layer", self.prop_layer_combo)
        self.prop_layer_row.hide()
        self.sec_props.add(self.prop_layer_row)

        self.prop_rotation = NoWheelDoubleSpinBox()
        self.prop_rotation.setDecimals(3)
        self.prop_rotation.setRange(-1.0, 359.999)
        self.prop_rotation.setSingleStep(1.0)
        self.prop_rotation.setWrapping(False)
        self.prop_rotation.setSpecialValueText(MIXED)
        self.prop_rotation.setSuffix("°")
        self.prop_rotation.setMaximumWidth(120)
        self.prop_rotation.setToolTip("Footprint rotation; positive is counter-clockwise")
        self.prop_rotation.editingFinished.connect(self._on_property_rotation)
        self.prop_rotation_row = ControlRow("rotation", self.prop_rotation)
        self.prop_rotation_row.hide()
        self.sec_props.add(self.prop_rotation_row)

        self.prop_hint = QtWidgets.QLabel("select a footprint or pad")
        self.prop_hint.setObjectName("faint")
        self.prop_hint.setWordWrap(True)
        self.sec_props.add(self.prop_hint)

        self.copper_props_group = CollapsibleGroup("Routed copper", expanded=True)
        self.copper_props_host = QtWidgets.QWidget()
        cp = QtWidgets.QVBoxLayout(self.copper_props_host)
        cp.setContentsMargins(2, 2, 2, 2); cp.setSpacing(4)
        self.copper_prop_kind = QtWidgets.QLabel("")
        self.copper_prop_kind.setObjectName("value")
        cp.addWidget(self.copper_prop_kind)

        self.copper_prop_width = NoWheelDoubleSpinBox()
        self.copper_prop_width.setDecimals(3); self.copper_prop_width.setRange(0.01, 20.0)
        self.copper_prop_width.setMaximumWidth(96)
        self.copper_prop_width.editingFinished.connect(self._edit_selected_track_width)
        self.copper_prop_width_row = ControlRow("width", self.copper_prop_width, "mm")
        cp.addWidget(self.copper_prop_width_row)

        self.copper_prop_dia = NoWheelDoubleSpinBox()
        self.copper_prop_dia.setDecimals(3); self.copper_prop_dia.setRange(0.01, 20.0)
        self.copper_prop_dia.setMaximumWidth(96)
        self.copper_prop_dia.editingFinished.connect(self._edit_selected_via_diameter)
        self.copper_prop_dia_row = ControlRow("pad diameter", self.copper_prop_dia, "mm")
        cp.addWidget(self.copper_prop_dia_row)

        self.copper_prop_hole = NoWheelDoubleSpinBox()
        self.copper_prop_hole.setDecimals(3); self.copper_prop_hole.setRange(0.0, 20.0)
        self.copper_prop_hole.setMaximumWidth(96)
        self.copper_prop_hole.setToolTip("0 mm = solid conductive via")
        self.copper_prop_hole.editingFinished.connect(self._edit_selected_via_hole)
        self.copper_prop_hole_row = ControlRow("hole", self.copper_prop_hole, "mm")
        cp.addWidget(self.copper_prop_hole_row)

        self.copper_prop_start = NoWheelComboBox(); self.copper_prop_start.setMaximumWidth(112)
        self.copper_prop_start.currentIndexChanged.connect(self._edit_selected_via_start)
        self.copper_prop_start_row = ControlRow("start layer", self.copper_prop_start)
        cp.addWidget(self.copper_prop_start_row)
        self.copper_prop_end = NoWheelComboBox(); self.copper_prop_end.setMaximumWidth(112)
        self.copper_prop_end.currentIndexChanged.connect(self._edit_selected_via_end)
        self.copper_prop_end_row = ControlRow("end layer", self.copper_prop_end)
        cp.addWidget(self.copper_prop_end_row)
        self.copper_prop_net = QtWidgets.QLabel("")
        self.copper_prop_net.setObjectName("faint")
        self.copper_prop_net.setWordWrap(True)
        cp.addWidget(self.copper_prop_net)
        self.copper_props_group.add(self.copper_props_host)
        self.copper_props_group.hide()
        self.sec_props.add(self.copper_props_group)

        self.grp_fields = CollapsibleGroup("Fields", expanded=False)
        self.grp_fields.toggled.connect(self._on_fields_toggled)
        self.props = PropertyGrid()
        self.grp_fields.add(self.props)
        self.grp_fields.hide()
        self.sec_props.add(self.grp_fields)
        layout.addWidget(self.sec_props)

        # Shown only in 3D for one selected footprint.  This mirrors the
        # reference web viewer: Auto keeps the model centred/seated; Manual
        # exposes X/Y/Z nudges plus explicit Align Z / Center XY actions.
        self.sec_model_adjust = Section("3D model offset")
        self.sec_model_adjust.setMinimumWidth(0)
        self.sec_model_adjust.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        self.sec_model_adjust.body.setMinimumWidth(0)
        self.sec_model_adjust.body.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        self.model_adjust_mode = NoWheelComboBox()
        self.model_adjust_mode.addItem("Auto", "auto")
        self.model_adjust_mode.addItem("Manual", "manual")
        self.model_adjust_mode.setMinimumWidth(0)
        self.model_adjust_mode.setMaximumWidth(108)
        self.model_adjust_mode.setSizePolicy(SIZE_EXPANDING, SIZE_FIXED)
        self.model_adjust_mode.setToolTip("Auto keeps the 3D model centred on its pads and seated on pad tops")
        self.model_adjust_mode.currentIndexChanged.connect(self._on_model_adjust_mode)
        self.sec_model_adjust.add(ControlRow("mode", self.model_adjust_mode))

        self.model_adjust_manual = QtWidgets.QWidget()
        self.model_adjust_manual.setMinimumWidth(0)
        self.model_adjust_manual.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        ma_layout = QtWidgets.QVBoxLayout(self.model_adjust_manual)
        ma_layout.setContentsMargins(0, 0, 0, 0)
        ma_layout.setSpacing(2)
        self.model_adjust_spins = {}
        for axis in ("x", "y", "z"):
            spin = NoWheelDoubleSpinBox()
            spin.setDecimals(3); spin.setRange(-1000.0, 1000.0); spin.setSingleStep(0.05)
            # Keep the unit outside the spin box.  FreeCAD's Qt style computes a
            # wide minimum size for a spin box containing both the full numeric
            # range and a suffix, which was the main reason Manual mode pushed
            # this section beyond the fixed sidebar viewport.
            spin.setMinimumWidth(0); spin.setMaximumWidth(96)
            spin.setSizePolicy(SIZE_EXPANDING, SIZE_FIXED)
            spin.valueChanged.connect(lambda value, a=axis: self._on_model_adjust_live(a, value))
            spin.editingFinished.connect(self._commit_model_adjust_edit)
            self.model_adjust_spins[axis] = spin
            ma_layout.addWidget(ControlRow(axis.upper(), spin, "mm"))
        self.sec_model_adjust.add(self.model_adjust_manual)

        self.model_adjust_readout = QtWidgets.QLabel("switch to 3D to measure")
        self.model_adjust_readout.setObjectName("faint")
        self.model_adjust_readout.setWordWrap(True)
        self.model_adjust_readout.setMinimumWidth(0)
        self.sec_model_adjust.add(self.model_adjust_readout)

        ma_buttons = QtWidgets.QWidget()
        ma_buttons.setMinimumWidth(0)
        ma_buttons.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        # Two columns are deliberately used instead of three buttons on one
        # line.  The three native button minimum widths do not fit reliably in
        # FreeCAD's 244 px sidebar, especially at non-default DPI scaling.
        ma_btn_layout = QtWidgets.QGridLayout(ma_buttons)
        ma_btn_layout.setContentsMargins(0, 2, 0, 0); ma_btn_layout.setSpacing(4)
        ma_btn_layout.setColumnStretch(0, 1); ma_btn_layout.setColumnStretch(1, 1)
        self.btn_align_z = QtWidgets.QPushButton("Align Z")
        self.btn_align_z.clicked.connect(lambda: self._align_model("z"))
        self.btn_center_xy = QtWidgets.QPushButton("Center XY")
        self.btn_center_xy.clicked.connect(lambda: self._align_model("xy"))
        self.btn_adjust_reset = QtWidgets.QPushButton("Reset")
        self.btn_adjust_reset.clicked.connect(self._reset_model_adjust)
        for button in (self.btn_align_z, self.btn_center_xy, self.btn_adjust_reset):
            button.setMinimumWidth(0)
            button.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)
        ma_btn_layout.addWidget(self.btn_align_z, 0, 0)
        ma_btn_layout.addWidget(self.btn_center_xy, 0, 1)
        ma_btn_layout.addWidget(self.btn_adjust_reset, 1, 0, 1, 2)
        self.sec_model_adjust.add(ma_buttons)
        self.model_adjust_buttons = ma_buttons
        self.sec_model_adjust.hide()
        layout.addWidget(self.sec_model_adjust)

        self.sec_stack = Section("Design layers", collapsible=True, expanded=False, parent=inner)
        self.stack_box = QtWidgets.QWidget()
        self.stack_layout = QtWidgets.QVBoxLayout(self.stack_box)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)
        self.stack_layout.setSpacing(1)
        self.sec_stack.add(self.stack_box)

        self.btn_add_layer = QtWidgets.QPushButton("Add layer")
        self.btn_add_layer.setToolTip("Add a copper layer above the top one")
        self.btn_add_layer.clicked.connect(self._on_stack_add)
        self.sec_stack.add(self.btn_add_layer)

        stack_hint = QtWidgets.QLabel(
            "Design layers are selected from the bottom layer bar.")
        stack_hint.setObjectName("faint")
        stack_hint.setWordWrap(True)
        self.sec_stack.add(stack_hint)
        self.sec_stack.hide()

        # Physical board stack-up.  Unlike the reference web viewer's four
        # global values, this editor exposes the actual top-to-bottom sequence:
        # top margin / copper / separation / copper / ... / bottom margin.
        self.sec_stackup = Section("Stackup", collapsible=True, expanded=False, parent=inner)
        self.stackup_box = QtWidgets.QWidget()
        self.stackup_layout = QtWidgets.QVBoxLayout(self.stackup_box)
        self.stackup_layout.setContentsMargins(0, 0, 0, 0)
        self.stackup_layout.setSpacing(1)
        self.sec_stackup.add(self.stackup_box)
        self.stackup_summary = QtWidgets.QLabel()
        self.stackup_summary.setObjectName("faint")
        self.stackup_summary.setWordWrap(True)
        self.sec_stackup.add(self.stackup_summary)
        self._stackup_rows = []
        self.sec_stackup.hide()

        # KiCad-like interactive routing controls.  These are shown only in
        # 2D while a copper reference layer is active.  Vias have independent
        # outer pad and drill diameters; a 0 mm drill deliberately means solid
        # conductive copper rather than an implicit hole.
        self.sec_routing = Section("Routing", collapsible=True, expanded=self._section_expanded("routing"))
        self.route_track_width = NoWheelDoubleSpinBox()
        self.route_track_width.setDecimals(3); self.route_track_width.setRange(0.01, 20.0)
        self.route_track_width.setSingleStep(0.05); self.route_track_width.setValue(self.track_width)
        self.route_track_width.setMaximumWidth(92)
        self.route_track_width.valueChanged.connect(self._on_route_track_width)
        self.sec_routing.add(ControlRow("track width", self.route_track_width, "mm"))

        self.route_via_dia = NoWheelDoubleSpinBox()
        self.route_via_dia.setDecimals(3); self.route_via_dia.setRange(0.01, 20.0)
        self.route_via_dia.setSingleStep(0.05); self.route_via_dia.setValue(self.via_diameter)
        self.route_via_dia.setMaximumWidth(92)
        self.route_via_dia.valueChanged.connect(self._on_route_via_diameter)
        self.sec_routing.add(ControlRow("via pad", self.route_via_dia, "mm"))

        self.route_via_hole = NoWheelDoubleSpinBox()
        self.route_via_hole.setDecimals(3); self.route_via_hole.setRange(0.0, 20.0)
        self.route_via_hole.setSingleStep(0.05); self.route_via_hole.setValue(0.0)
        self.route_via_hole.setMaximumWidth(92)
        self.route_via_hole.setToolTip("0 mm = solid conductive via cylinder")
        self.route_via_hole.valueChanged.connect(self._on_route_via_hole)
        self.sec_routing.add(ControlRow("via hole", self.route_via_hole, "mm"))

        self.route_via_target = NoWheelComboBox()
        self.route_via_target.setMaximumWidth(112)
        self.route_via_target.setToolTip("Auto follows the active ratsnest to a different copper layer; choose a layer to override")
        self.route_via_target.currentIndexChanged.connect(self._on_via_target_changed)
        self.sec_routing.add(ControlRow("via target", self.route_via_target))

        route_hint = QtWidgets.QLabel(
            "X route · V via · / posture\n"
            "Shift: no object snap · Ctrl: no grid snap\n"
            "Backspace: previous corner · Enter: finish")
        route_hint.setObjectName("faint"); route_hint.setWordWrap(True)
        self.sec_routing.add(route_hint)
        layout.addWidget(self.sec_routing)

        self.sec_layers = Section("Footprint graphics", collapsible=True, expanded=self._section_expanded("footprint_graphics"))
        self.layer_box = QtWidgets.QWidget()
        self.layer_layout = QtWidgets.QVBoxLayout(self.layer_box)
        self.layer_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_layout.setSpacing(1)
        self.sec_layers.add(self.layer_box)

        self.chk_ratsnest = QtWidgets.QCheckBox("ratsnest")
        self.chk_ratsnest.setChecked(True)
        self.chk_ratsnest.setToolTip("Show schematic net connections from the imported netlist")
        self.chk_ratsnest.toggled.connect(self._on_ratsnest_toggle)
        self.chk_ratsnest.hide()
        self.sec_layers.add(self.chk_ratsnest)

        grid_row = QtWidgets.QWidget()
        grid_layout = QtWidgets.QHBoxLayout(grid_row)
        grid_layout.setContentsMargins(2, 6, 2, 0)
        grid_layout.setSpacing(7)
        self.chk_grid = QtWidgets.QCheckBox("grid")
        self.chk_grid.setChecked(True)
        self.chk_grid.toggled.connect(self._on_grid_toggle)
        grid_layout.addWidget(self.chk_grid)
        grid_layout.addStretch(1)
        self.grid_combo = NoWheelComboBox()
        for step in GRID_STEPS:
            self.grid_combo.addItem("%g mm" % step, step)
        self.grid_combo.setCurrentIndex(GRID_STEPS.index(0.1))
        self.grid_combo.setMinimumWidth(SIDEBAR_SPIN_WIDTH)
        self.grid_combo.setMaximumWidth(SIDEBAR_SPIN_WIDTH)
        self.grid_combo.setSizePolicy(SIZE_FIXED, SIZE_FIXED)
        self.grid_combo.currentIndexChanged.connect(self._on_grid_step)
        grid_layout.addWidget(self.grid_combo)
        self.sec_layers.add(grid_row)

        snap_row = QtWidgets.QWidget()
        snap_layout = QtWidgets.QHBoxLayout(snap_row)
        snap_layout.setContentsMargins(2, 0, 2, 0)
        self.chk_snap = QtWidgets.QCheckBox("snap to grid")
        self.chk_snap.setChecked(True)
        self.chk_snap.toggled.connect(lambda on: self.canvas.set_grid(snap=on))
        snap_layout.addWidget(self.chk_snap)
        snap_layout.addStretch(1)
        self.chk_hidden = QtWidgets.QCheckBox("hidden text")
        self.chk_hidden.setToolTip("Show texts KiCad marks as hidden")
        self.chk_hidden.toggled.connect(self._on_hidden_texts)
        snap_layout.addWidget(self.chk_hidden)
        self.sec_layers.add(snap_row)
        layout.addWidget(self.sec_layers)

        self.sec_pads = Section("Pads", collapsible=True, expanded=self._section_expanded("pads"))
        self.pad_box = QtWidgets.QWidget()
        self.pad_layout = QtWidgets.QVBoxLayout(self.pad_box)
        self.pad_layout.setContentsMargins(0, 0, 0, 0)
        self.pad_layout.setSpacing(1)
        self.sec_pads.add(self.pad_box)
        layout.addWidget(self.sec_pads)

        self._build_board_section(layout)

        self._sidebar_layout = layout
        self._sidebar_sections = {
            "footprints": self.sec_footprints,
            "properties": self.sec_props,
            "board": self.sec_board,
            "routing": self.sec_routing,
            "footprint_graphics": self.sec_layers,
            "pads": self.sec_pads,
        }
        self._reorder_sidebar_sections(self._viewer_options.sidebar_order)

        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_board_section(self, layout):
        """Board solid: import, placement and per-layer section status."""
        self.sec_board = Section("Board solid", collapsible=True,
                                 expanded=self._section_expanded("board"))

        # Empty state -------------------------------------------------------
        self.board_empty = QtWidgets.QWidget()
        empty = QtWidgets.QVBoxLayout(self.board_empty)
        empty.setContentsMargins(2, 0, 2, 2); empty.setSpacing(6)
        hint = QtWidgets.QLabel(
            "Import the board body as a solid. Every copper layer's routing "
            "boundary is its cross-section at that layer's height.")
        hint.setObjectName("faint"); hint.setWordWrap(True)
        empty.addWidget(hint)
        buttons = QtWidgets.QHBoxLayout(); buttons.setSpacing(4)
        self.btn_board_step = QtWidgets.QPushButton("STEP file\u2026")
        self.btn_board_step.setObjectName("primaryButton")
        self.btn_board_step.setToolTip("Import a .step/.stp solid as the board")
        self.btn_board_step.clicked.connect(self.import_board_step_dialog)
        self.btn_board_fc = QtWidgets.QPushButton("From FreeCAD\u2026")
        self.btn_board_fc.setToolTip("Use a solid that is open in FreeCAD")
        self.btn_board_fc.clicked.connect(self.import_board_freecad_dialog)
        for button in (self.btn_board_step, self.btn_board_fc):
            button.setMinimumWidth(0)
            button.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)
            buttons.addWidget(button)
        empty.addLayout(buttons)
        self.sec_board.add(self.board_empty)

        # Loaded state ------------------------------------------------------
        self.board_card = QtWidgets.QFrame()
        self.board_card.setObjectName("boardCard")
        self.board_card.setMinimumWidth(0)
        self.board_card.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        card = QtWidgets.QVBoxLayout(self.board_card)
        card.setContentsMargins(8, 6, 8, 6); card.setSpacing(3)
        self.board_name = QtWidgets.QLabel("")
        self.board_name.setObjectName("boardName")
        self.board_name.setMinimumWidth(0)
        card.addWidget(self.board_name)
        self.board_meta = QtWidgets.QLabel("")
        self.board_meta.setObjectName("boardMeta")
        self.board_meta.setWordWrap(True)
        card.addWidget(self.board_meta)

        self.board_up = NoWheelComboBox()
        for axis in boardsolidmod.UP_AXES:
            self.board_up.addItem(boardsolidmod.UP_AXIS_LABELS.get(axis, axis), axis)
        self.board_up.setToolTip("Which axis of the source solid points up")
        self.board_up.activated.connect(lambda _i: self._on_board_placement_edit(commit=True))
        card.addWidget(ControlRow("up axis", self.board_up))

        self.board_rotation = NoWheelDoubleSpinBox()
        self.board_rotation.setDecimals(2); self.board_rotation.setRange(-180.0, 180.0)
        self.board_rotation.setWrapping(True); self.board_rotation.setSingleStep(90.0)
        self.board_rotation.setToolTip("Rotation about the vertical axis; positive is counter-clockwise")
        card.addWidget(ControlRow("rotation", self.board_rotation, "\u00b0"))

        self.board_offset = {}
        for axis in ("x", "y"):
            spin = NoWheelDoubleSpinBox()
            spin.setDecimals(3); spin.setRange(-10000.0, 10000.0); spin.setSingleStep(1.0)
            spin.setToolTip("Board-plane position of the solid (editor coordinates)")
            self.board_offset[axis] = spin
            card.addWidget(ControlRow("offset %s" % axis.upper(), spin, "mm"))
        for spin in [self.board_rotation] + list(self.board_offset.values()):
            spin.valueChanged.connect(lambda _v: self._on_board_placement_edit(commit=False))
            spin.editingFinished.connect(lambda: self._on_board_placement_edit(commit=True))

        actions = QtWidgets.QGridLayout(); actions.setSpacing(4)
        actions.setContentsMargins(0, 4, 0, 0)
        self.btn_board_center = QtWidgets.QPushButton("Center")
        self.btn_board_center.setToolTip("Centre the solid's footprint on the editor origin")
        self.btn_board_center.clicked.connect(self.center_board_solid)
        self.btn_board_reload = QtWidgets.QPushButton("Reload")
        self.btn_board_reload.setToolTip("Re-read the solid from its STEP file or FreeCAD object")
        self.btn_board_reload.clicked.connect(self.reload_board_solid)
        self.btn_board_replace = QtWidgets.QPushButton("Replace \u25be")
        self.btn_board_replace.setToolTip("Replace the board solid")
        replace_menu = QtWidgets.QMenu(self.btn_board_replace)
        replace_menu.addAction("STEP file\u2026", self.import_board_step_dialog)
        replace_menu.addAction("From FreeCAD\u2026", self.import_board_freecad_dialog)
        self.btn_board_replace.setMenu(replace_menu)
        self.btn_board_remove = QtWidgets.QPushButton("Remove")
        self.btn_board_remove.setToolTip("Remove the board solid (routing becomes unconstrained)")
        self.btn_board_remove.clicked.connect(self.remove_board_solid)
        for i, button in enumerate((self.btn_board_center, self.btn_board_reload,
                                    self.btn_board_replace, self.btn_board_remove)):
            button.setMinimumWidth(0)
            button.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)
            actions.addWidget(button, i // 2, i % 2)
        card.addLayout(actions)

        layers_title = QtWidgets.QLabel("LAYER SECTIONS")
        layers_title.setObjectName("faint")
        layers_title.setContentsMargins(0, 6, 0, 0)
        card.addWidget(layers_title)
        self.board_layer_host = QtWidgets.QWidget()
        self.board_layer_grid = QtWidgets.QGridLayout(self.board_layer_host)
        self.board_layer_grid.setContentsMargins(0, 0, 0, 0)
        self.board_layer_grid.setHorizontalSpacing(6)
        self.board_layer_grid.setVerticalSpacing(1)
        card.addWidget(self.board_layer_host)
        self.sec_board.add(self.board_card)
        self.board_card.hide()

        self._board_edit_before = None
        self._board_refresh_timer = QtCore.QTimer(self)
        self._board_refresh_timer.setSingleShot(True)
        self._board_refresh_timer.setInterval(60)
        self._board_refresh_timer.timeout.connect(self._refresh_board_views)
        layout.addWidget(self.sec_board)

    def _build_copper_tool_panel(self):
        """One fixed rail for selection, routing, filtering and board display."""
        panel = QtWidgets.QFrame()
        panel.setObjectName("toolRail")
        panel.setFixedWidth(TOOL_RAIL_WIDTH)
        panel.setFrameShape(QtWidgets.QFrame.StyledPanel)
        outer = QtWidgets.QVBoxLayout(panel)
        outer.setContentsMargins(5, 7, 5, 7)
        outer.setSpacing(4)

        # Select and Route are mutually exclusive editor tools.
        self.editor_tool_group = QtWidgets.QButtonGroup(panel)
        self.editor_tool_group.setExclusive(True)
        self.routing_mode_buttons = {}

        for mode, tooltip in (("select", "Select objects (Esc)"),
                              ("route", "Route tracks (X)")):
            button = ToolRailButton(mode, panel)
            button.setToolTip(tooltip)
            self.editor_tool_group.addButton(button)
            self.routing_mode_buttons[mode] = button
            if mode == "select":
                button.clicked.connect(lambda _checked=False: self._activate_select_tool())
            else:
                button.clicked.connect(lambda _checked=False: self._set_route_tool("route"))
            outer.addWidget(button, 0, Qt.AlignHCenter)

        # Selection filtering is editor/view state, not a PCB design rule.  A
        # compact funnel on the 2D tool rail mirrors KiCad's Selection Filter
        # without consuming permanent canvas width.
        self.selection_filter_button = ToolRailButton("filter", panel)
        self.selection_filter_button.setCheckable(False)
        self.selection_filter_button.setToolTip("Selection filter")
        self.selection_filter_menu = QtWidgets.QMenu(self.selection_filter_button)
        self._selection_filter_actions = {}
        all_action = self.selection_filter_menu.addAction("All")
        all_action.setCheckable(True); all_action.setChecked(True)
        all_action.toggled.connect(self._on_selection_filter_all)
        self._selection_filter_actions["all"] = all_action
        self.selection_filter_menu.addSeparator()
        for key, label in SELECTION_FILTER_KEYS:
            action = self.selection_filter_menu.addAction(label)
            action.setCheckable(True); action.setChecked(True)
            action.toggled.connect(lambda checked, k=key: self._on_selection_filter_item(k, checked))
            self._selection_filter_actions[key] = action
        self.selection_filter_menu.aboutToShow.connect(self._sync_selection_filter_actions)
        self.selection_filter_button.setMenu(self.selection_filter_menu)
        self.selection_filter_button.setPopupMode(enum(QtWidgets.QToolButton, "ToolButtonPopupMode", "InstantPopup"))
        outer.addWidget(self.selection_filter_button, 0, Qt.AlignHCenter)

        sep = QtWidgets.QFrame(panel)
        sep.setObjectName("separator")
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        outer.addWidget(sep)

        # View helpers.  The board toggle hides/shows the derived board edges
        # and the outside-board shade without touching the design.
        self.btn_rail_board = ToolRailButton("board", panel)
        self.btn_rail_board.setChecked(True)
        self.btn_rail_board.setToolTip("Show board-solid edges and shade the area outside the board")
        self.btn_rail_board.toggled.connect(lambda _on: self._apply_board_sections())
        outer.addWidget(self.btn_rail_board, 0, Qt.AlignHCenter)

        self.btn_rail_fit = ToolRailButton("fit", panel)
        self.btn_rail_fit.setCheckable(False)
        self.btn_rail_fit.setToolTip("Zoom to fit  (F)")
        self.btn_rail_fit.clicked.connect(self.canvas_fit)
        outer.addWidget(self.btn_rail_fit, 0, Qt.AlignHCenter)

        self.routing_mode_buttons["select"].setChecked(True)
        outer.addStretch(1)
        return panel

    def _sync_selection_filter_actions(self):
        actions = getattr(self, "_selection_filter_actions", {})
        current = dict(getattr(self.canvas, "selection_filter", {}) or {})
        for key, _label in SELECTION_FILTER_KEYS:
            action = actions.get(key)
            if action is None:
                continue
            blocked = action.blockSignals(True)
            action.setChecked(bool(current.get(key, True)))
            action.blockSignals(blocked)
        all_action = actions.get("all")
        if all_action is not None:
            blocked = all_action.blockSignals(True)
            all_action.setChecked(all(bool(current.get(k, True))
                                      for k, _label in SELECTION_FILTER_KEYS))
            all_action.blockSignals(blocked)

    def _on_selection_filter_all(self, checked):
        values = dict((key, bool(checked)) for key, _label in SELECTION_FILTER_KEYS)
        self.canvas.set_selection_filter(values)
        self._sync_selection_filter_actions()
        self._refresh_properties()

    def _on_selection_filter_item(self, key, checked):
        self.canvas.set_selection_filter({str(key): bool(checked)})
        self._sync_selection_filter_actions()
        self._refresh_properties()

    def _activate_select_tool(self):
        self.route_tool = "select"
        self.via_pending = False
        if self.routing is not None:
            self._finish_route()
        self.canvas.clear_route_preview()
        self.canvas.set_routing_enabled(False)
        self._sync_routing_ui()

    def _build_layer_bar(self):
        """Altium-style bottom copper-layer selector with an inline add button."""
        bar = QtWidgets.QWidget()
        bar.setObjectName("layerBar")
        bar.setMinimumHeight(31)
        outer = QtWidgets.QHBoxLayout(bar)
        outer.setContentsMargins(5, 2, 5, 2)
        outer.setSpacing(4)

        scroll = QtWidgets.QScrollArea(bar)
        scroll.setObjectName("layerBarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(27)
        host = QtWidgets.QWidget()
        host.setObjectName("layerBarHost")
        self.layer_tab_layout = QtWidgets.QHBoxLayout(host)
        self.layer_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_tab_layout.setSpacing(3)
        self.layer_tab_layout.addStretch(1)
        scroll.setWidget(host)
        self.layer_bar_scroll = scroll
        outer.addWidget(scroll, 1)

        self.btn_layer_add_bottom = QtWidgets.QPushButton("+")
        self.btn_layer_add_bottom.setFixedSize(27, 24)
        self.btn_layer_add_bottom.setToolTip("Add copper layer")
        self.btn_layer_add_bottom.clicked.connect(self._on_stack_add)
        outer.addWidget(self.btn_layer_add_bottom)
        self._layer_tab_buttons = {}
        return bar

    def _layer_icon(self, color):
        pix = QtGui.QPixmap(12, 12)
        pix.fill(QtGui.QColor(color))
        return QtGui.QIcon(pix)

    def _layer_tab_context_menu(self, uid, pos, button):
        layer = self.stack.by_id(uid)
        if layer is None:
            return
        menu = QtWidgets.QMenu(button)
        vis = menu.addAction("Hide layer" if layer.visible else "Show layer")
        remove = menu.addAction("Remove layer")
        remove.setEnabled(len(self.stack) > 1)
        chosen = menu.exec_(button.mapToGlobal(pos)) if hasattr(menu, "exec_") else menu.exec(button.mapToGlobal(pos))
        if chosen is vis:
            self._on_stack_visible(uid, not layer.visible)
        elif chosen is remove:
            self._on_stack_remove(uid)

    def _rebuild_layer_bar(self):
        layout = getattr(self, "layer_tab_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._layer_tab_buttons = {}
        bottom_up = self.stack.copper_layers()
        for index, layer in enumerate(bottom_up, 1):
            button = QtWidgets.QPushButton("[%d] %s" % (index, layer.name))
            button.setObjectName("layerTabButton")
            button.setCheckable(True)
            button.setChecked(layer.id == self.stack.active_id)
            button.setIcon(self._layer_icon(layer.color))
            button.setToolTip("%s — click to make active; right-click for layer actions" % layer.name)
            button.clicked.connect(lambda _checked=False, uid=layer.id: self._on_stack_activate(uid))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, uid=layer.id, b=button: self._layer_tab_context_menu(uid, pos, b))
            layout.addWidget(button)
            self._layer_tab_buttons[layer.id] = button
        layout.addStretch(1)

    def _sync_layer_bar(self):
        expected = [layer.id for layer in self.stack.copper_layers()]
        if list(getattr(self, "_layer_tab_buttons", {}).keys()) != expected:
            self._rebuild_layer_bar()
            return
        for index, layer in enumerate(self.stack.copper_layers(), 1):
            button = self._layer_tab_buttons.get(layer.id)
            if button is None:
                continue
            blocked = button.blockSignals(True)
            button.setChecked(layer.id == self.stack.active_id)
            button.setText("[%d] %s" % (index, layer.name))
            button.setIcon(self._layer_icon(layer.color))
            # Hidden layers remain selectable so the user can activate them and
            # restore visibility from the context menu.
            button.setEnabled(True)
            button.setProperty("layerVisible", "true" if layer.visible else "false")
            button.blockSignals(blocked)

    def _build_status(self):
        bar = QtWidgets.QWidget()
        bar.setObjectName("statusBar")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(14, 5, 14, 5)
        row.setSpacing(18)

        self.status_selection = QtWidgets.QLabel("No selection")
        self.status_selection.setObjectName("statusText")
        row.addWidget(self.status_selection)

        self.status_pos = QtWidgets.QLabel("x \u2014   y \u2014 mm")
        self.status_pos.setObjectName("statusText")
        row.addWidget(self.status_pos)

        self.status_grid = QtWidgets.QLabel("grid 0.1 mm")
        self.status_grid.setObjectName("statusText")
        row.addWidget(self.status_grid)

        self.status_renderer = QtWidgets.QLabel(self.canvas.renderer_name)
        self.status_renderer.setObjectName("statusText")
        self.status_renderer.setToolTip(
            "2D canvas renderer. GPU mode uses a direct Qt QOpenGLWidget viewport; "
            "scene editing and hit testing remain on the CPU.")
        row.addWidget(self.status_renderer)

        self.status_hover = QtWidgets.QLabel("")
        self.status_hover.setObjectName("hoverText")
        row.addWidget(self.status_hover)
        row.addStretch(1)
        return bar

    # -- modified / saved-state tracking --------------------------------
    @staticmethod
    def _content_hash(data):
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _persistent_content_state(self):
        """Return design content that should trigger the unsaved ``*`` marker.

        Selection, zoom/pan, grid visibility, sidebar expansion, active tools
        and other presentation-only state are intentionally omitted.  The
        result describes the actual PCB/layout and project-linked component
        geometry, so Undo can compare equal to the saved baseline again.
        """
        stack = self.stack.snapshot()
        stack.pop("active_id", None)
        for layer in stack.get("layers", []):
            layer.pop("visible", None)

        footprints = []
        for entry in self._entries:
            item = entry["item"]
            fp = entry["fp"]
            source_text = getattr(fp, "source_text", "") or ""
            source_digest = (projectfile.footprint_sha256_text(source_text)
                             if source_text else "")
            model = getattr(fp, "model", None)
            model_state = None
            if model is not None:
                model_state = {
                    "path": str(getattr(model, "path", "") or ""),
                    "original_path": str(getattr(model, "original_path", "") or ""),
                    "offset": list(getattr(model, "offset", (0.0, 0.0, 0.0))),
                    "scale": list(getattr(model, "scale", (1.0, 1.0, 1.0))),
                    "rotate": list(getattr(model, "rotate", (0.0, 0.0, 0.0))),
                }
            footprints.append({
                "uid": str(entry.get("uid", "")),
                "ref": str(entry.get("ref", "")),
                "value": str(entry.get("value", "")),
                "footprint_name": str(entry.get("footprint_name", "")),
                "placeholder": bool(entry.get("placeholder", False)),
                "source_digest": source_digest,
                "source_name": str(getattr(fp, "name", "") or ""),
                "x": float(item.pos().x()),
                "y": float(item.pos().y()),
                "rotation": float(item.rotation()),
                "design_layer_id": getattr(item, "design_layer_id", None),
                "model": model_state,
                "model_adjust": dict(entry.get("model_adjust") or
                                     {"x": 0.0, "y": 0.0, "z": 0.0}),
                "model_adjust_mode": str(entry.get("model_adjust_mode", "auto")),
            })

        netlist = None
        if self.netlist is not None:
            snap = self.netlist.snapshot()
            # The netlist's provenance path is not layout content; connectivity is.
            snap.pop("source_file", None)
            netlist = snap

        return {
            "stack": stack,
            "design_rules": self.design_rules.snapshot(),
            "footprints": footprints,
            "board_solid": (list(self.board_solid.signature()) +
                            [self.board_solid.kind, str(self.board_solid.source.get("path") or "")]
                            if self.board_solid is not None else None),
            "tracks": [track.snapshot() for track in self.tracks],
            "vias": [via.snapshot() for via in self.vias],
            "netlist": netlist,
        }

    def _current_content_signature(self):
        return self._content_hash(self._persistent_content_state())

    def _display_window_title(self):
        identity = self._project_file or self._design_file
        base = "Elec3D"
        if identity:
            base += " - %s" % os.path.basename(identity)
        if self._modified:
            base += " *"
        return base

    def _refresh_window_title(self):
        title = self._display_window_title()
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        close_action = getattr(self, "act_close_project", None)
        if close_action is not None:
            close_action.setEnabled(bool(self._project_file))

    def refresh_modified_state(self):
        """Refresh and return whether persistent design content is unsaved."""
        if self._saved_content_signature is None:
            self._saved_content_signature = self._current_content_signature()
        modified = self._current_content_signature() != self._saved_content_signature
        if modified != self._modified:
            self._modified = modified
            self._refresh_window_title()
        return bool(self._modified)

    def is_modified(self):
        return self.refresh_modified_state()

    def _capture_saved_content_state(self):
        """Make the current committed layout the clean save/open baseline."""
        self._saved_content_signature = self._current_content_signature()
        self._modified = False
        self._refresh_window_title()
        return self._saved_content_signature

    def confirm_save_or_discard(self, action_text="closing FreeCAD"):
        """Ask what to do with unsaved layout changes before data can be lost.

        Returns True when the caller may continue (saved or explicitly
        discarded) and False when the action must be cancelled.
        """
        if not self.is_modified():
            return True
        save = enum(QtWidgets.QMessageBox, "StandardButton", "Save")
        discard = enum(QtWidgets.QMessageBox, "StandardButton", "Discard")
        cancel = enum(QtWidgets.QMessageBox, "StandardButton", "Cancel")
        parent = QtWidgets.QApplication.activeWindow() or self
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Unsaved Elec3D design")
        box.setIcon(enum(QtWidgets.QMessageBox, "Icon", "Warning"))
        box.setText("The current Elec3D design has unsaved changes.")
        box.setInformativeText("Save the changes before %s?" % action_text)
        box.setStandardButtons(save | discard | cancel)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        answer = exec_dialog(box)
        if answer == save:
            return bool(self.save_design())
        if answer == discard:
            return True
        return False

    # -- public API ------------------------------------------------------
    def add_footprint(self, fp, select=True, uid=None, ref="", value="",
                      footprint_name="", placeholder=False, fit=True):
        """Add a parsed footprint model, optionally carrying schematic metadata."""
        offset = QtCore.QPointF(0, 0)
        if self._entries:
            span = max(e["fp"].bbox()[2] - e["fp"].bbox()[0] for e in self._entries)
            offset = QtCore.QPointF(self._entries[-1]["item"].pos().x() + span + 4.0, 0)

        self._assign_pad_net_names(fp, ref)
        item = self.canvas.add_footprint(fp, offset, reference_override=ref)
        # "new footprints land there": an import joins whichever plane is
        # currently active, exactly like the reference viewer.
        active_copper = self.stack.active_copper()
        item.design_layer_id = active_copper.id if active_copper is not None else self.stack.bottom().id
        entry = {
            "item": item, "fp": fp, "uid": uid or uuid.uuid4().hex,
            "ref": str(ref or ""), "value": str(value or ""),
            "footprint_name": str(footprint_name or ""),
            "placeholder": bool(placeholder),
            "model_adjust": {"x": 0.0, "y": 0.0, "z": 0.0},
            "model_adjust_mode": "auto",
        }
        self._entries.append(entry)
        self._apply_design_layers()
        self._rebuild_footprint_list()
        self._rebuild_stack()
        if select:
            self.set_active(entry)
            self.canvas.select_footprint(item)
        self._refresh_ratsnest()
        if fit:
            self.canvas.fit()
        self._refresh_3d_if_visible()
        return item

    def _reset_design_identity(self):
        """Detach the current in-memory board from any previously opened project/file."""
        self._project_file = ""
        self._design_file = ""
        self._design_resource_file = ""
        self._design_path_variables = {}
        self._freecad_export_document = ""
        self._refresh_window_title()

    def clear(self, keep_design_path=False):
        self._freecad_export_document = ""
        if not keep_design_path:
            self._reset_design_identity()
        self.canvas.clear_footprints()
        self.board_solid = None
        self._board_outline_cache = {}
        self.canvas.clear_board_sections()
        self._entries = []
        self._active = None
        self._selected = []
        self.netlist = None
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self.canvas.set_net_highlight([], False)
        self.tracks = []
        self.vias = []
        self.routing = None
        self.via_pending = False
        self._route_history_before = None
        self.canvas.clear_routed_copper()
        self.clear_history()
        if self.hosted:
            self.act_reload.setEnabled(False)
        self._rebuild_footprint_list()
        self._rebuild_layers()
        self._rebuild_pads()
        self._rebuild_stack()
        self.title.setText("\u2014")
        self.tag.hide()
        self.chk_ratsnest.hide()
        self._refresh_ratsnest()
        self._show_no_selection()
        if self.view_mode == "3d":
            self.set_view_mode("2d")

    def clear_design(self, _checked=False, confirm=True):
        """Start a completely empty board while keeping UI preferences intact.

        This is intentionally stronger than the internal ``clear()`` helper:
        design layers/stackup, routing defaults and model-directory state are
        returned to their new-board defaults as well as removing footprints,
        tracks, vias, netlist and the board solid.
        """
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before clearing the design")
            return False
        if confirm:
            yes = enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
            no = enum(QtWidgets.QMessageBox, "StandardButton", "No")
            answer = QtWidgets.QMessageBox.question(
                self, "Clear design",
                "Clear the complete design?\n\n"
                "Footprints, tracks, vias, netlist, board solid, design layers and stackup "
                "will be removed/reset.",
                yes | no, no)
            if answer != yes:
                return False

        self.clear()
        self.stack = LayerStack()
        self.design_rules = DesignRules()
        self.models_folder = ""
        self.view3d.set_models_folder("")
        self.track_width = 0.25
        self.via_diameter = 0.80
        self.via_hole_diameter = 0.0
        self.via_target_layer_id = None
        self.via_target_auto = True
        self.route_posture = 0
        # Copper drags hold H/V/45 geometry like KiCad; Alt frees the angle.
        self.drag_keep_45 = True
        # Walk around blocking copper instead of stopping dead at it.
        self.route_walkaround = True
        self.route_tool = "select"
        self.routing = None
        self.via_pending = False
        self._route_history_before = None
        self.show_ratsnest = True
        self.chk_ratsnest.blockSignals(True)
        self.chk_ratsnest.setChecked(True)
        self.chk_ratsnest.blockSignals(False)

        for widget, value in ((self.route_track_width, self.track_width),
                              (self.route_via_dia, self.via_diameter),
                              (self.route_via_hole, self.via_hole_diameter)):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

        self._rebuild_stack()
        self._rebuild_stackup()
        self._apply_design_layers()
        self._sync_routing_ui()
        self.canvas.refresh_extent()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._notify("Design cleared")
        return True

    # -- board design files -------------------------------------------------
    def _section_state_snapshot(self):
        result = {}
        for key, section in getattr(self, "_sidebar_sections", {}).items():
            try:
                result[key] = bool(section.header.isChecked())
            except Exception:
                result[key] = bool(section.body.isVisible())
        return result

    def _design_snapshot(self, path):
        """Return the complete persistent board state as plain Python data.

        Undo history and a route that is still under the mouse are intentionally
        transient.  Everything needed to reconstruct the committed layout,
        resources and current view is included.
        """
        # Never turn an in-progress QGraphicsScene translation into committed
        # layout merely because Save was invoked.  v0.10.0 cleared the drag
        # bookkeeping here, which could strand temporary positions outside the
        # undo history.  Ask the user to finish the gesture instead.
        if self.canvas.has_active_interaction():
            raise RuntimeError("Finish the current mouse operation before saving the design.")

        footprint_sources = [getattr(entry.get("fp"), "source_file", "")
                             for entry in self._entries]
        inferred = designfile.infer_path_variables(
            path, footprint_sources, self.models_folder)
        variables = dict(self._design_path_variables or {})
        variables.update(inferred)

        footprints = []
        for entry in self._entries:
            item = entry["item"]
            fp = entry["fp"]
            source_text = self._read_footprint_source(fp)
            source_original = (getattr(fp, "original_source_file", "") or
                               getattr(fp, "source_file", "") or "")
            source_hash = projectfile.footprint_sha256_text(source_text) if source_text else ""
            model = getattr(fp, "model", None)
            saved_model = None
            if model is not None:
                original_model = (getattr(model, "original_path", "") or
                                  getattr(model, "path", "") or "")
                model_hash = str(getattr(model, "saved_sha256", "") or "")
                resolved_model = ""
                try:
                    resolved_model = self.view3d._resolved_model(model) or ""
                except Exception:
                    resolved_model = ""
                if resolved_model and os.path.isfile(resolved_model):
                    try:
                        model_hash = projectfile.sha256_file(resolved_model)
                    except Exception:
                        pass
                saved_model = {
                    "path": designfile.portable_path(
                        getattr(model, "path", ""), variables, path, preferred="MODEL_DIR"),
                    "original_path": str(original_model),
                    "sha256": model_hash,
                    "offset": list(getattr(model, "offset", (0.0, 0.0, 0.0))),
                    "scale": list(getattr(model, "scale", (1.0, 1.0, 1.0))),
                    "rotate": list(getattr(model, "rotate", (0.0, 0.0, 0.0))),
                }
            footprints.append({
                "uid": entry.get("uid", ""),
                "source": designfile.portable_path(
                    getattr(fp, "source_file", ""), variables, path,
                    preferred="FOOTPRINT_DIR"),
                "source_original": str(source_original),
                "source_sha256": source_hash,
                # The complete footprint snapshot lives in .e3d_pcb itself.
                # Project Footprints/ is a portable editable/reference copy,
                # not the only place from which layout geometry can be restored.
                "source_text": source_text,
                "ref": entry.get("ref", ""),
                "value": entry.get("value", ""),
                "footprint_name": entry.get("footprint_name", "") or getattr(fp, "name", ""),
                "placeholder": bool(entry.get("placeholder", False)),
                "x": float(item.pos().x()), "y": float(item.pos().y()),
                "rotation": float(item.rotation()),
                "design_layer_id": getattr(item, "design_layer_id", None),
                "hidden_texts": bool(getattr(item, "_hidden_texts_visible", False)),
                "layers": dict(getattr(item, "layer_visibility", {})),
                "model": saved_model,
                "model_adjust": dict(entry.get("model_adjust") or
                                     {"x": 0.0, "y": 0.0, "z": 0.0}),
                "model_adjust_mode": entry.get("model_adjust_mode", "auto"),
            })

        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        selected_copper = []
        for item in self.canvas.selected_copper_items():
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None:
                selected_copper.append(str(model.uid))
        view = {
            "grid_visible": bool(self.canvas.grid_visible),
            "grid_step": float(self.canvas.grid_step),
            "snap_enabled": bool(self.canvas.snap_enabled),
            "crosshair": bool(self.canvas.crosshair),
            "zoom": float(self.canvas.px_per_mm()),
            "center": [float(center.x()), float(center.y())],
            "auto_fit": bool(self.canvas._auto_fit),
            "show_ratsnest": bool(self.show_ratsnest),
            "highlighted_nets": sorted(self._highlighted_nets),
            "net_highlight_enabled": bool(self._net_highlight_enabled),
            "view_mode": self.view_mode,
            "active_uid": self._active_uid(),
            "selected_footprints": self._selected_uids(),
            "selected_copper": selected_copper,
            "sections": self._section_state_snapshot(),
        }
        netlist = self.netlist.snapshot() if self.netlist is not None else None
        if netlist and netlist.get("source_file"):
            netlist["source_file"] = designfile.portable_path(
                netlist["source_file"], variables, path)
        return {
            "format_version": designfile.FORMAT_VERSION,
            "generator": "Elec3D",
            "addon_version": __version__,
            "paths": variables,
            "stack": self.stack.snapshot(),
            "design_rules": self.design_rules.snapshot(),
            "footprints": footprints,
            "board_solid": self._board_solid_snapshot(variables, path),
            "routing": self.routing_snapshot(),
            "netlist": netlist,
            "view": view,
        }

    def _board_solid_snapshot(self, variables, path):
        """Board solid data for ``.e3d_pcb`` with a portable source path."""
        solid = self.board_solid
        if solid is None:
            return None
        data = solid.snapshot()
        source = dict(data.get("source") or {})
        if source.get("path"):
            source.setdefault("original_path", str(source.get("original_path") or source["path"]))
            source["path"] = designfile.portable_path(
                source["path"], variables, path, preferred="MODEL_DIR")
        data["source"] = source
        return data

    @staticmethod
    def _project_asset_name(preferred, digest, used, by_digest, default):
        """Return a stable flat project-asset name, reusing identical content."""
        if digest and digest in by_digest:
            return by_digest[digest]
        clean = projectfile.safe_asset_name(preferred, default)
        stem, ext = os.path.splitext(clean)
        candidate = clean
        key = candidate.lower()
        if key in used and used[key] != digest:
            suffix = (digest or uuid.uuid4().hex)[:8]
            candidate = "%s_%s%s" % (stem or "asset", suffix, ext)
            key = candidate.lower()
            n = 2
            while key in used and used[key] != digest:
                candidate = "%s_%s_%d%s" % (stem or "asset", suffix, n, ext)
                key = candidate.lower()
                n += 1
        used[key] = digest
        if digest:
            by_digest[digest] = candidate
        return candidate

    @staticmethod
    def _read_footprint_source(fp):
        text = getattr(fp, "source_text", "") or ""
        if text:
            return text
        source = getattr(fp, "source_file", "") or ""
        if source and os.path.isfile(source):
            try:
                with open(source, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except OSError:
                pass
        return ""

    @staticmethod
    def _external_resource_path(reference, design_identity=""):
        """Resolve an original library reference without project path variables."""
        raw = str(reference or "")
        if not raw:
            return ""
        expanded = os.path.expanduser(os.path.expandvars(raw))
        if "${" in expanded:
            return expanded
        if not os.path.isabs(expanded):
            base = os.path.dirname(os.path.abspath(design_identity)) if design_identity else os.getcwd()
            expanded = os.path.join(base, expanded)
        return os.path.abspath(os.path.normpath(expanded))

    def _project_design_snapshot(self, paths):
        """Create complete board data plus project-local footprint/model copies."""
        board_path = paths["board_file"]
        data = self._design_snapshot(board_path)
        data["paths"] = {
            "DESIGN_DIR": ".",
            "FOOTPRINT_DIR": "${DESIGN_DIR}/%s" % projectfile.FOOTPRINT_DIR_NAME,
            "MODEL_DIR": "${DESIGN_DIR}/%s" % projectfile.MODEL_DIR_NAME,
        }
        if data.get("netlist") and self.netlist is not None:
            data["netlist"]["source_file"] = str(getattr(self.netlist, "source_file", "") or "")

        footprint_assets = {}
        model_assets = {}
        warnings = []
        fp_used, fp_by_digest = {}, {}
        model_used, model_by_digest = {}, {}
        bindings = []

        for entry, saved in zip(self._entries, data.get("footprints", [])):
            fp = entry["fp"]
            ref = entry.get("ref") or saved.get("footprint_name") or getattr(fp, "name", "?")
            original_source = (getattr(fp, "original_source_file", "") or
                               getattr(fp, "source_file", "") or "")
            source_text = self._read_footprint_source(fp)
            binding = {"entry": entry, "footprint_path": "", "model_path": ""}
            if source_text:
                digest = projectfile.footprint_sha256_text(source_text)
                # KiCad's footprint identifier is the canonical local filename
                # (and preserves the library's capitalization).  Older .e3d_pcb
                # files sometimes stored a lower-cased path token, so using the
                # token's basename here would permanently lower-case the project
                # Footprints/ copy even when the library name is C_1206_3216Metric.
                preferred = projectfile.footprint_asset_filename(
                    saved.get("footprint_name", ""), original_source, getattr(fp, "name", ""))
                asset_name = self._project_asset_name(
                    preferred, digest, fp_used, fp_by_digest, "footprint.kicad_mod")
                target = os.path.join(paths["footprints_dir"], asset_name)
                footprint_assets[target] = source_text.encode("utf-8")
                saved["source"] = "${FOOTPRINT_DIR}/%s" % asset_name
                saved["source_original"] = str(original_source)
                saved["source_sha256"] = digest
                saved["source_text"] = source_text
                binding["footprint_path"] = target
            else:
                saved["source_original"] = str(original_source)
                saved["source_sha256"] = ""
                saved["source_text"] = ""
                warnings.append(
                    "Footprint %s has no recoverable source snapshot; the board keeps its placeholder/reference only."
                    % ref)

            model = getattr(fp, "model", None)
            saved_model = saved.get("model")
            if model is not None and saved_model:
                original_model = (getattr(model, "original_path", "") or
                                  getattr(model, "path", "") or "")
                resolved_model = ""
                try:
                    resolved_model = self.view3d._resolved_model(model) or ""
                except Exception:
                    resolved_model = ""
                if resolved_model and os.path.isfile(resolved_model):
                    try:
                        digest = projectfile.sha256_file(resolved_model)
                        preferred = os.path.basename(resolved_model)
                        asset_name = self._project_asset_name(
                            preferred, digest, model_used, model_by_digest, "model.step")
                        target = os.path.join(paths["models_dir"], asset_name)
                        model_assets[target] = resolved_model
                        saved_model["path"] = "${MODEL_DIR}/%s" % asset_name
                        saved_model["original_path"] = str(original_model)
                        saved_model["sha256"] = digest
                        binding["model_path"] = target
                    except Exception as exc:
                        warnings.append("3D model for %s could not be copied into the project: %s" % (ref, exc))
                elif original_model:
                    warnings.append("3D model for %s was not found while saving: %s" %
                                    (ref, original_model))
                    saved_model["original_path"] = str(original_model)
            bindings.append(binding)

        # The board solid's STEP source travels with the project like any
        # component model.  A FreeCAD-sourced solid is written out as STEP so
        # the project keeps the exact B-Rep for cavity export.
        self._board_project_asset = None
        solid = self.board_solid
        saved_solid = data.get("board_solid")
        if solid is not None and saved_solid:
            source = saved_solid.setdefault("source", {})
            src_path = str(solid.source.get("path") or "")
            if src_path and os.path.isfile(src_path):
                try:
                    digest = projectfile.sha256_file(src_path)
                    asset_name = self._project_asset_name(
                        os.path.basename(src_path), digest, model_used, model_by_digest, "board.step")
                    target = os.path.join(paths["models_dir"], asset_name)
                    model_assets[target] = src_path
                    source["path"] = "${MODEL_DIR}/%s" % asset_name
                    source["sha256"] = digest
                    self._board_project_asset = {"target": target}
                except Exception as exc:
                    warnings.append("Board solid STEP could not be copied into the project: %s" % exc)
            elif solid.exact_shape is not None:
                stem = projectfile.safe_asset_name(
                    os.path.splitext(solid.label.split(" (")[0])[0] or "board", "board")
                asset_name = self._project_asset_name(
                    "%s.step" % os.path.splitext(stem)[0], "", model_used, model_by_digest, "board.step")
                target = os.path.join(paths["models_dir"], asset_name)
                source["path"] = "${MODEL_DIR}/%s" % asset_name
                self._board_project_asset = {"target": target, "shape": solid.exact_shape}
            elif solid.kind == "step":
                warnings.append("Board solid STEP source was not found; the embedded "
                                "mesh was saved instead: %s" % (src_path or solid.label))
        return data, footprint_assets, model_assets, warnings, bindings

    def _show_design_save_warnings(self):
        warnings = list(getattr(self, "_last_design_save_warnings", []) or [])
        if not warnings:
            return
        shown = warnings[:12]
        tail = "\n... and %d more." % (len(warnings) - len(shown)) if len(warnings) > len(shown) else ""
        QtWidgets.QMessageBox.warning(
            self, "Project saved with warnings",
            "The project was saved, but some external resources could not be copied:\n\n" +
            "\n".join("- " + warning for warning in shown) + tail)

    def _write_project(self, paths):
        """Save current state to an already-resolved project folder."""
        projectfile.ensure_structure(paths)
        data, footprint_assets, model_assets, warnings, bindings = self._project_design_snapshot(paths)
        for target, payload in footprint_assets.items():
            projectfile.atomic_write_bytes(target, payload)
        for target, source in model_assets.items():
            # A second Save commonly has model.path already pointing at this
            # project's Models/ target.  On Windows, opening that target and
            # then os.replace()-ing it while the read handle is still live raises
            # WinError 5 (Access denied).  There is nothing to copy in the
            # source==destination case; otherwise close the source before the
            # atomic replacement.
            projectfile.atomic_copy_file(source, target)
        board_asset = getattr(self, "_board_project_asset", None) or {}
        if board_asset.get("shape") is not None:
            try:
                board_asset["shape"].exportStep(board_asset["target"])
            except Exception as exc:
                warnings.append("Board solid could not be written as STEP: %s" % exc)
                board_asset = {}
                (data.get("board_solid") or {}).get("source", {}).pop("path", None)
        designfile.write_file(paths["board_file"], data)
        projectfile.write_project(
            paths["project_file"], addon_version=__version__,
            board_file=paths["board_file"], footprints_dir=paths["footprints_dir"],
            models_dir=paths["models_dir"])

        # Use project-local assets immediately after a successful save.  Keep
        # original_source_file/original_path for mismatch checks against the
        # user's installed KiCad libraries.
        for binding in bindings:
            entry = binding["entry"]
            fp = entry["fp"]
            if binding["footprint_path"]:
                fp.source_file = binding["footprint_path"]
            model = getattr(fp, "model", None)
            if model is not None and binding["model_path"]:
                model.path = binding["model_path"]
        if self.board_solid is not None and board_asset.get("target"):
            self.board_solid.source["path"] = board_asset["target"]
        self._project_file = paths["project_file"]
        self._design_file = paths["board_file"]
        self._design_resource_file = paths["board_file"]
        self._design_path_variables = dict(data.get("paths") or {})
        self._last_design_save_warnings = warnings
        self._import_directory = paths["project_dir"]
        self.models_folder = paths["models_dir"]
        self.view3d.set_models_folder(self.models_folder)
        self._capture_saved_content_state()
        self._remember_recent_project(self._project_file)
        self._notify("Project saved: %s%s" % (
            os.path.basename(paths["project_file"]),
            " (%d warning%s)" % (len(warnings), "" if len(warnings) == 1 else "s") if warnings else ""))
        return paths["project_file"]

    def _recent_project_params(self):
        """Return the FreeCAD preference group used for the project MRU list."""
        try:
            import FreeCAD as App
            return App.ParamGet(PREF_PATH)
        except Exception:
            return None

    def _store_recent_projects(self, paths):
        params = self._recent_project_params()
        if params is None:
            return
        clean = []
        seen = set()
        for path in list(paths or []):
            path = os.path.abspath(os.path.expanduser(str(path)))
            if os.path.splitext(path)[1].lower() != projectfile.PROJECT_EXTENSION:
                continue
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            clean.append(path)
            if len(clean) >= 3:
                break
        try:
            params.SetString("RecentProjects", json.dumps(clean, ensure_ascii=False))
        except Exception:
            pass

    def _recent_projects(self):
        """Return up to three existing recent projects, newest first.

        Stale/deleted entries are pruned when the File menu is opened, so the
        user never has to manually maintain the list after moving a project.
        """
        params = self._recent_project_params()
        if params is None:
            return []
        try:
            raw = params.GetString("RecentProjects", "")
            values = json.loads(raw) if raw else []
        except Exception:
            values = []
        if not isinstance(values, list):
            values = []
        clean = []
        seen = set()
        for value in values:
            path = os.path.abspath(os.path.expanduser(str(value)))
            if os.path.splitext(path)[1].lower() != projectfile.PROJECT_EXTENSION:
                continue
            key = os.path.normcase(path)
            if key in seen or not os.path.isfile(path):
                continue
            seen.add(key)
            clean.append(path)
            if len(clean) >= 3:
                break
        if clean != values[:3]:
            self._store_recent_projects(clean)
        return clean

    def _remember_recent_project(self, path):
        path = os.path.abspath(os.path.expanduser(str(path or "")))
        if (not path or os.path.splitext(path)[1].lower() != projectfile.PROJECT_EXTENSION
                or not os.path.isfile(path)):
            return
        key = os.path.normcase(path)
        rest = [candidate for candidate in self._recent_projects()
                if os.path.normcase(candidate) != key]
        self._store_recent_projects([path] + rest)
        if hasattr(self, "menu_recent_projects"):
            self._rebuild_recent_projects_menu()

    def _forget_recent_project(self, path):
        key = os.path.normcase(os.path.abspath(os.path.expanduser(str(path or ""))))
        self._store_recent_projects([candidate for candidate in self._recent_projects()
                                     if os.path.normcase(candidate) != key])
        if hasattr(self, "menu_recent_projects"):
            self._rebuild_recent_projects_menu()

    def _rebuild_recent_projects_menu(self):
        menu = getattr(self, "menu_recent_projects", None)
        if menu is None:
            return
        menu.clear()
        projects = self._recent_projects()
        if not projects:
            empty = menu.addAction("No recent projects")
            empty.setEnabled(False)
            return
        for index, path in enumerate(projects, 1):
            label = "&%d  %s" % (index, os.path.basename(path))
            action = menu.addAction(label)
            action.setToolTip(path)
            action.setStatusTip(path)
            action.triggered.connect(
                lambda _checked=False, project_path=path: self._open_recent_project(project_path))

    def _open_recent_project(self, path):
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before opening a project")
            return None
        if not os.path.isfile(path):
            self._forget_recent_project(path)
            QtWidgets.QMessageBox.warning(
                self, "Open Recent", "The recent project no longer exists:\n%s" % path)
            return None
        try:
            return self.open_design_path(path, prompt_for_project=False, check_unsaved=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open Recent", str(exc))
            return None

    def _project_selection_dialog(self, title, suggested_name="board"):
        base_dir = self._import_directory or os.path.expanduser("~")
        if self._project_file:
            base_dir = os.path.dirname(os.path.dirname(self._project_file)) or base_dir
            suggested_name = os.path.splitext(os.path.basename(self._project_file))[0]
        elif self._design_file:
            suggested_name = os.path.splitext(os.path.basename(self._design_file))[0]
        start = os.path.join(base_dir, suggested_name + projectfile.PROJECT_EXTENSION)
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, title, start,
            "Elec3D project (*%s);;All files (*)" % projectfile.PROJECT_EXTENSION)
        if not path:
            return None
        paths = projectfile.new_project_paths(path)
        if os.path.isfile(paths["project_file"]):
            yes = enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
            no = enum(QtWidgets.QMessageBox, "StandardButton", "No")
            answer = QtWidgets.QMessageBox.question(
                self, "Replace project?",
                "A project named %s already exists. Replace its board/project files?\n\n"
                "Existing files in Footprints and Models are not deleted." % paths["name"],
                yes | no, no)
            if answer != yes:
                return None
        return paths

    def create_new_project(self, _checked=False):
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before creating a project")
            return None
        if not self.confirm_save_or_discard("creating a new project"):
            return None
        paths = self._project_selection_dialog("Create Elec3D project", "Project")
        if paths is None:
            return None
        self.clear_design(confirm=False)
        try:
            result = self._write_project(paths)
            self._show_design_save_warnings()
            return result
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Create project", str(exc))
            return None

    def _prompt_create_project_for_current(self, title="Create project"):
        paths = self._project_selection_dialog(title)
        if paths is None:
            return None
        try:
            result = self._write_project(paths)
            self._show_design_save_warnings()
            return result
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save project", str(exc))
            return None

    def save_design(self):
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before saving the project")
            return None
        if not self._project_file:
            yes = enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
            no = enum(QtWidgets.QMessageBox, "StandardButton", "No")
            answer = QtWidgets.QMessageBox.question(
                self, "Create project",
                "This design is not part of a project. Create a project and save it now?",
                yes | no, yes)
            if answer != yes:
                return None
            return self._prompt_create_project_for_current("Create project and save")
        try:
            paths = projectfile.read_project(self._project_file)
            result = self._write_project(paths)
            self._show_design_save_warnings()
            return result
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save project", str(exc))
            return None

    def save_design_as(self):
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before saving the project")
            return None
        return self._prompt_create_project_for_current("Save project as")

    def close_project(self, _checked=False):
        """Close the active project and return to a clean empty editor.

        The current project is never silently discarded: dirty content goes
        through the same Save / Discard / Cancel guard used when FreeCAD closes
        or another project is opened.  Recent-project history is intentionally
        retained so the just-closed project remains one click away.
        """
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before closing the project")
            return False
        if not self._project_file:
            self._notify("No Elec3D project is open")
            return False
        project_name = os.path.basename(self._project_file)
        if not self.confirm_save_or_discard("closing the current project"):
            return False
        self.clear_design(confirm=False)
        # Closing a project establishes a new clean, empty in-memory baseline;
        # it must not immediately appear as an unsaved board.
        self._capture_saved_content_state()
        self._notify("Project closed: %s" % project_name)
        return True

    def open_design_dialog(self):
        if self.canvas.has_active_interaction():
            self._notify("Finish the current mouse operation before opening a project/board")
            return None
        start = self._project_file or self._design_file or self._import_directory or os.path.expanduser("~")
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Elec3D project or board", start,
            "Elec3D project/board (*%s *%s);;Project (*%s);;Board (*%s);;All files (*)"
            % (projectfile.PROJECT_EXTENSION, designfile.DESIGN_EXTENSION,
               projectfile.PROJECT_EXTENSION, designfile.DESIGN_EXTENSION))
        if not path:
            return None
        if not self.confirm_save_or_discard("opening another project or board"):
            return None
        try:
            return self.open_design_path(path, prompt_for_project=True, check_unsaved=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open", str(exc))
            return None

    def _show_open_warnings(self, warnings):
        if not warnings:
            return
        shown = warnings[:12]
        tail = "\n... and %d more." % (len(warnings) - len(shown)) if len(warnings) > len(shown) else ""
        QtWidgets.QMessageBox.warning(
            self, "Design opened with warnings",
            "The saved layout was restored. Review these library/resource checks:\n\n" +
            "\n".join("- " + warning for warning in shown) + tail)

    def open_design_path(self, path, prompt_for_project=True, check_unsaved=True):
        if check_unsaved and not self.confirm_save_or_discard("opening another project or board"):
            return None
        path = os.path.abspath(str(path))
        ext = os.path.splitext(path)[1].lower()
        if ext == projectfile.PROJECT_EXTENSION:
            paths = projectfile.read_project(path)
            warnings = self.load_design_from(paths["board_file"], project_path=path,
                                             project_paths=paths)
            self._remember_recent_project(path)
            self._show_open_warnings(warnings)
            return path
        if ext != designfile.DESIGN_EXTENSION:
            raise RuntimeError("Unsupported Elec3D file: %s" % path)
        warnings = self.load_design_from(path)
        self._show_open_warnings(warnings)
        if prompt_for_project:
            yes = enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
            no = enum(QtWidgets.QMessageBox, "StandardButton", "No")
            answer = QtWidgets.QMessageBox.question(
                self, "Create project?",
                "This .e3d_pcb board is not attached to a project.\n\n"
                "Create a project folder with .e3d_pro, .e3d_pcb, Footprints and Models now?",
                yes | no, yes)
            if answer == yes:
                self._prompt_create_project_for_current("Create project for board")
        return path

    def _current_library_footprint(self, saved, original_ref, identity_path):
        """Find the current external library footprint for mismatch checking.

        First honor the exact path saved by the source computer.  If that path
        moved, try KiCad's conventional ``<Library>.pretty/<Footprint>.kicad_mod``
        under any versioned KICAD*_FOOTPRINT_DIR environment variable.
        """
        candidates = []
        direct = self._external_resource_path(original_ref, identity_path)
        if direct:
            candidates.append(direct)
        lib_name = str(saved.get("footprint_name") or "")
        if ":" in lib_name:
            library, footprint_name = lib_name.split(":", 1)
            rel = os.path.join(library + ".pretty", footprint_name + ".kicad_mod")
            for key in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR",
                        "KICAD8_FOOTPRINT_DIR", "KICAD7_FOOTPRINT_DIR",
                        "KICAD6_FOOTPRINT_DIR"):
                root = os.path.expanduser(os.path.expandvars(os.environ.get(key, "")))
                if root:
                    candidates.append(os.path.join(root, rel))
        seen = set()
        for candidate in candidates:
            key = os.path.normcase(os.path.abspath(candidate)) if "${" not in candidate else candidate
            if key in seen:
                continue
            seen.add(key)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return ""

    def _load_footprint_for_design(self, saved, variables, resource_path,
                                   identity_path, project_mode, pin_map, warnings):
        """Restore embedded footprint geometry and compare project/library copies."""
        label = saved.get("ref") or saved.get("footprint_name") or "?"
        source_ref = saved.get("source", "")
        source = designfile.resolve_path(source_ref, variables, resource_path) if source_ref else ""
        original_ref = saved.get("source_original", "") or (source_ref if not project_mode else "")
        saved_hash = str(saved.get("source_sha256", "") or "")
        source_text = str(saved.get("source_text", "") or "")
        fp = None
        placeholder = bool(saved.get("placeholder", False))

        # v3 .e3d_pcb is self-contained for footprint/layout geometry.  The
        # project Footprints/ copy and user's KiCad library are diagnostics and
        # reload sources, never prerequisites for restoring the saved layout.
        if source_text:
            try:
                fp = parse_footprint(source_text, source_file=source or identity_path)
                placeholder = False
                if saved_hash and projectfile.footprint_sha256_text(source_text) != saved_hash:
                    warnings.append(
                        "Embedded footprint fingerprint mismatch for %s; the board file may have been edited externally."
                        % label)
            except Exception as exc:
                warnings.append("Embedded footprint for %s could not be parsed: %s" % (label, exc))

        # Project-local footprint copy: useful for portability/editing, but the
        # embedded board snapshot remains authoritative when they disagree.
        if source:
            if os.path.isfile(source):
                if saved_hash:
                    try:
                        with open(source, "r", encoding="utf-8", errors="replace") as fh:
                            project_hash = projectfile.footprint_sha256_text(fh.read())
                        if project_hash != saved_hash:
                            warnings.append(
                                "Project footprint mismatch for %s: %s differs from the footprint stored in the board; using the board layout."
                                % (label, source))
                    except OSError as exc:
                        warnings.append("Project footprint check failed for %s: %s" % (label, exc))
                if fp is None:
                    try:
                        fp = parse_footprint_file(source)
                        placeholder = False
                    except Exception as exc:
                        warnings.append("Footprint %s failed to load from %s: %s" % (label, source, exc))
            elif project_mode:
                warnings.append("Project footprint file not found for %s: %s; using the footprint stored in the board." %
                                (label, source))

        # Compare with the original/current KiCad library footprint.  This is
        # exactly a compatibility warning: changed/missing library content does
        # not alter the saved layout.
        if original_ref or saved.get("footprint_name"):
            external = self._current_library_footprint(saved, original_ref, identity_path)
            if external:
                if saved_hash:
                    try:
                        with open(external, "r", encoding="utf-8", errors="replace") as fh:
                            external_hash = projectfile.footprint_sha256_text(fh.read())
                        if external_hash != saved_hash:
                            warnings.append(
                                "Footprint mismatch for %s: current library file differs from the design footprint; using the saved board footprint."
                                % label)
                    except OSError as exc:
                        warnings.append("Footprint library check failed for %s: %s" % (label, exc))
            else:
                # A .e3d_pro project is intentionally portable without the
                # source computer's global KiCad libraries.  Its Footprints/
                # member is the project's current external footprint and the
                # embedded board snapshot remains the layout authority.  If
                # the project copy is present (or its absence was already
                # reported above), a missing *original* library is therefore
                # not a missing-footprint condition and must not produce the
                # noisy warning seen after moving a project to another PC.
                # We still compare and warn when an original library file *is*
                # found and differs from the board snapshot.
                if not project_mode:
                    missing_ref = original_ref or saved.get("footprint_name") or "unknown library reference"
                    if source_text:
                        warnings.append(
                            "Footprint library file not found for %s: %s; using the saved board footprint."
                            % (label, missing_ref))
                    elif fp is None:
                        warnings.append(
                            "Footprint library file not found for %s: %s. This legacy board has no embedded footprint snapshot, so a placeholder will be used."
                            % (label, missing_ref))
                    else:
                        warnings.append(
                            "Footprint library file not found for %s: %s; using the available project/source footprint."
                            % (label, missing_ref))

        # Legacy v1/v2 board with no embedded source: external library is the
        # final fallback before a positioned placeholder.
        if fp is None and (original_ref or saved.get("footprint_name")):
            external = self._current_library_footprint(saved, original_ref, identity_path)
            if external:
                try:
                    fp = parse_footprint_file(external)
                    placeholder = False
                    source = external
                except Exception as exc:
                    warnings.append("Footprint %s failed to load from original library: %s" %
                                    (label, exc))

        if fp is None:
            fp = placeholder_footprint(
                saved.get("footprint_name") or saved.get("ref") or "missing",
                pin_map.get(saved.get("ref", ""), ["1", "2"]))
            fp.source_file = source
            placeholder = True
        fp.source_text = source_text or getattr(fp, "source_text", "")
        fp.original_source_file = str(original_ref or getattr(fp, "source_file", "") or "")
        return fp, placeholder

    def load_design_from(self, path, project_path="", project_paths=None):
        """Open a complete .e3d_pcb, optionally as the board owned by .e3d_pro."""
        resource_path = os.path.abspath(str(path))
        data = designfile.read_file(resource_path)  # validate before replacing current work
        warnings = []
        variables = dict(data.get("paths") or {})
        project_mode = bool(project_path)

        self.clear(keep_design_path=True)
        self._project_file = os.path.abspath(project_path) if project_path else ""
        self._design_file = resource_path
        self._design_resource_file = resource_path
        self._design_path_variables = variables
        self._import_directory = (project_paths or {}).get("project_dir", os.path.dirname(resource_path))

        self.stack = LayerStack()
        self.stack.restore(data.get("stack"))
        self.design_rules = DesignRules(data.get("design_rules"))
        self.netlist = KiCadNetlist.restore(data.get("netlist")) if data.get("netlist") else None
        if self.netlist is not None and self.netlist.source_file:
            # Net connectivity is embedded.  An unresolved provenance path is
            # therefore informational and must never make the package fail.
            self.netlist.source_file = self._external_resource_path(
                self.netlist.source_file, resource_path)

        model_dir = ""
        if "MODEL_DIR" in variables:
            model_dir = designfile.resolve_path("${MODEL_DIR}", variables, resource_path)
            self.models_folder = model_dir if os.path.isdir(model_dir) else ""
            self.view3d.set_models_folder(self.models_folder)
            if model_dir and not os.path.isdir(model_dir):
                warnings.append("3D model directory is missing: %s" % model_dir)
        else:
            self.models_folder = ""
            self.view3d.set_models_folder("")

        self.board_solid = self._restore_board_solid(data, variables, resource_path, warnings)

        pin_map = pins_by_ref(self.netlist) if self.netlist is not None else {}
        for saved in data.get("footprints", []):
            fp, placeholder = self._load_footprint_for_design(
                saved, variables, resource_path, resource_path, project_mode, pin_map, warnings)

            model_saved = saved.get("model")
            if model_saved:
                model_path_ref = model_saved.get("path", "")
                model_path = designfile.resolve_path(
                    model_path_ref, variables, resource_path) if model_path_ref else ""
                original_model = model_saved.get("original_path", "") or model_path_ref
                if model_path and os.path.isfile(model_path) and model_saved.get("sha256"):
                    try:
                        if projectfile.sha256_file(model_path) != str(model_saved.get("sha256") or ""):
                            warnings.append("Project 3D model mismatch for %s: %s" %
                                            (saved.get("ref") or saved.get("footprint_name") or "?", model_path))
                    except Exception:
                        pass
                if (not model_path or not os.path.isfile(model_path)) and original_model:
                    fallback = self._external_resource_path(original_model, resource_path)
                    if fallback and os.path.isfile(fallback):
                        model_path = fallback
                    elif model_path_ref:
                        warnings.append("3D model not found for %s: %s" %
                                        (saved.get("ref") or saved.get("footprint_name") or "?", model_path_ref))
                fp.model = fpmod.Model(
                    model_path,
                    tuple(model_saved.get("offset") or (0.0, 0.0, 0.0)),
                    tuple(model_saved.get("scale") or (1.0, 1.0, 1.0)),
                    tuple(model_saved.get("rotate") or (0.0, 0.0, 0.0)))
                fp.model.original_path = str(original_model or "")
                fp.model.saved_sha256 = str(model_saved.get("sha256", "") or "")

            item = self.add_footprint(
                fp, select=False, uid=saved.get("uid") or None,
                ref=saved.get("ref", ""), value=saved.get("value", ""),
                footprint_name=saved.get("footprint_name", ""),
                placeholder=placeholder, fit=False)
            item.setPos(float(saved.get("x", 0.0)), float(saved.get("y", 0.0)))
            item.setRotation(float(saved.get("rotation", 0.0)))
            layer_id = saved.get("design_layer_id")
            item.design_layer_id = layer_id if self.stack.has(layer_id) else self.stack.active_id
            for layer, visible in (saved.get("layers") or {}).items():
                if layer in item.layer_items:
                    item.set_layer_visible(layer, visible)
            item.set_hidden_texts_visible(saved.get("hidden_texts", False))
            entry = self._entries[-1]
            entry["model_adjust"] = dict(saved.get("model_adjust") or
                                         {"x": 0.0, "y": 0.0, "z": 0.0})
            entry["model_adjust_mode"] = saved.get("model_adjust_mode", "auto")

        self._apply_design_layers()
        self._rebuild_footprint_list()
        self._rebuild_stack()
        self._rebuild_stackup()
        self.restore_routing(data.get("routing"))

        view = data.get("view") or {}
        self.show_ratsnest = bool(view.get("show_ratsnest", True))
        self.chk_ratsnest.blockSignals(True)
        self.chk_ratsnest.setChecked(self.show_ratsnest)
        self.chk_ratsnest.blockSignals(False)
        self._highlighted_nets = set(str(n) for n in view.get("highlighted_nets", []) if n)
        self._net_highlight_enabled = bool(view.get("net_highlight_enabled", False)
                                           and self._highlighted_nets)
        self.canvas.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
        grid_visible = bool(view.get("grid_visible", True))
        grid_step = float(view.get("grid_step", 0.1))
        snap_enabled = bool(view.get("snap_enabled", True))
        self.canvas.set_grid(visible=grid_visible, step=grid_step, snap=snap_enabled)
        self.canvas.crosshair = bool(view.get("crosshair", True))
        for widget, checked in ((self.chk_grid, grid_visible),
                                (self.btn_grid, grid_visible),
                                (self.chk_snap, snap_enabled)):
            widget.blockSignals(True); widget.setChecked(checked); widget.blockSignals(False)
        for index in range(self.grid_combo.count()):
            try:
                if abs(float(self.grid_combo.itemData(index)) - grid_step) < 1e-12:
                    self.grid_combo.blockSignals(True)
                    self.grid_combo.setCurrentIndex(index)
                    self.grid_combo.blockSignals(False)
                    break
            except (TypeError, ValueError):
                pass
        self.status_grid.setText("grid %g mm" % grid_step)

        active_uid = str(view.get("active_uid") or "")
        active = self._entry_by_uid(active_uid) if active_uid else None
        if active is None and self._entries:
            active = self._entries[0]
        if active is not None:
            self.set_active(active)
        selected_uids = set(str(uid) for uid in view.get("selected_footprints", []))
        footprint_items = [entry["item"] for entry in self._entries
                           if str(entry.get("uid")) in selected_uids]
        copper_uids = set(str(uid) for uid in view.get("selected_copper", []))
        copper_items = []
        for item in self.canvas.track_items + self.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and str(model.uid) in copper_uids:
                copper_items.append(item)
        self.canvas.select_editor_items(footprint_items, copper_items)

        for key, expanded in (view.get("sections") or {}).items():
            section = getattr(self, "_sidebar_sections", {}).get(key)
            if section is not None:
                section.set_expanded(bool(expanded))

        self.clear_history()
        self._refresh_ratsnest()
        self.canvas.refresh_extent()
        self.set_view_mode(view.get("view_mode", "2d"))
        self._capture_saved_content_state()

        zoom = float(view.get("zoom", self.canvas.px_per_mm()))
        center = view.get("center") or [0.0, 0.0]
        auto_fit = bool(view.get("auto_fit", False))
        self.canvas._auto_fit = False
        self.canvas.set_zoom(zoom)
        self.canvas.centerOn(float(center[0]), float(center[1]))
        self.canvas._auto_fit = auto_fit
        self.canvas._refresh_cursor_from_global()
        self._refresh_3d_if_visible(force=True)

        refs, found = self.model_reference_stats()
        if refs and found < refs:
            warnings.append("3D models resolved: %d of %d (missing models remain as empty 3D references)." %
                            (found, refs))
        self._notify("Design opened: %s%s" % (
            os.path.basename(self._project_file or resource_path),
            " (%d warning%s)" % (len(warnings), "" if len(warnings) == 1 else "s") if warnings else ""))
        return warnings

    def _restore_board_solid(self, data, variables, resource_path, warnings):
        """Board solid from a design file, migrating pre-v0.17 drawn outlines."""
        saved = data.get("board_solid")
        if saved:
            try:
                solid = BoardSolid.restore(saved)
            except Exception as exc:
                warnings.append("Board solid could not be restored: %s" % exc)
                return None
            path_ref = solid.source.get("path")
            if path_ref:
                resolved = designfile.resolve_path(path_ref, variables, resource_path)
                solid.source["path"] = resolved
                if not os.path.isfile(resolved):
                    warnings.append("Board solid STEP source not found (the embedded mesh "
                                    "is used): %s" % resolved)
            return solid
        outlines = data.get("outlines") or []
        if not outlines:
            return None
        top_margin = self.stack.legacy_top_margin
        try:
            solid = boardsolidmod.solid_from_legacy_outlines(
                self.stack, outlines, 1.0 if top_margin is None else top_margin)
        except Exception as exc:
            warnings.append("Drawn board outlines could not be converted: %s" % exc)
            return None
        if solid is not None:
            warnings.append("This board was drawn with per-layer outlines. They were "
                            "converted into an equivalent board solid; import a STEP or "
                            "FreeCAD solid to replace it.")
        return solid

    def has_footprint(self, source_file):
        for entry in self._entries:
            if entry["fp"].source_file == source_file:
                return True
        return False

    def active_entry(self):
        """Return the currently selected in-memory footprint entry."""
        return self._active

    def replace_active_footprint(self, fp):
        """Replace the active parsed model while preserving its scene position."""
        entry = self._active
        if entry is None:
            raise RuntimeError("No footprint is selected.")

        old_item = entry["item"]
        pos = QtCore.QPointF(old_item.pos())
        rotation = float(old_item.rotation())
        saved_layers = dict(old_item.layer_visibility)
        hidden_texts = old_item._hidden_texts_visible
        design_layer_id = old_item.design_layer_id
        self.canvas.remove_footprint(old_item)
        self._assign_pad_net_names(fp, entry.get("ref", ""))
        item = self.canvas.add_footprint(fp, pos, reference_override=entry.get("ref", ""))
        item.setRotation(rotation)
        # Reloading a file must not silently move the part to another plane.
        item.design_layer_id = design_layer_id
        for layer, visible in saved_layers.items():
            if layer in item.layer_items:
                item.set_layer_visible(layer, visible)
        item.set_hidden_texts_visible(hidden_texts)
        entry["item"] = item
        entry["fp"] = fp

        self._apply_design_layers()
        self._rebuild_footprint_list()
        self._rebuild_stack()
        self.set_active(entry)
        self.canvas.select_footprint(item)
        self.canvas.refresh_extent()
        return item

    def set_active(self, entry):
        self._active = entry
        fp = entry["fp"]
        if self.hosted:
            self.act_reload.setEnabled(bool(fp.source_file))
        title = entry.get("ref") or fp.name
        if entry.get("ref") and entry.get("value"):
            title = "%s   %s" % (entry["ref"], entry["value"])
        self.title.setText(title)
        if fp.attr:
            self.tag.setText(fp.attr)
            self.tag.show()
        else:
            self.tag.hide()
        self._rebuild_layers()
        self._rebuild_pads()
        # The active entry only drives the single-footprint sections (graphics
        # and pads); Properties still describes the whole selection.
        self._refresh_properties()

    # -- edit history / footprint transforms ----------------------------
    def clear_history(self):
        """Forget all document-free edit history."""
        self._history = []
        self._history_index = 0

    def history_snapshot(self):
        """Return history state suitable for the live host rebuild snapshot."""
        return {
            "commands": list(self._history),
            "index": int(self._history_index),
        }

    def restore_history(self, snapshot):
        """Restore a history snapshot captured before a host-mode rebuild."""
        snapshot = snapshot or {}
        self._history = list(snapshot.get("commands", []))
        self._history_index = max(
            0, min(int(snapshot.get("index", len(self._history))), len(self._history)))

    def _push_history(self, command):
        if self._history_replaying:
            return
        if self._history_index < len(self._history):
            del self._history[self._history_index:]
        self._history.append(command)
        self._history_index = len(self._history)

    def _entry_by_uid(self, uid):
        for entry in self._entries:
            if entry.get("uid") == uid:
                return entry
        return None

    def _selected_uids(self):
        result = []
        for item in self.canvas.selected_footprints():
            entry = self.entry_for_item(item)
            if entry is not None:
                result.append(entry["uid"])
        return result

    def _selected_copper_uids(self):
        result = []
        for item in self.canvas.selected_copper_items():
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None:
                result.append(model.uid)
        return result

    def _active_uid(self):
        return self._active.get("uid") if self._active is not None else None

    def _transform_state(self, item, pos=None, rotation=None):
        entry = self.entry_for_item(item)
        if entry is None:
            return None
        point = QtCore.QPointF(item.pos() if pos is None else pos)
        angle = float(item.rotation() if rotation is None else rotation)
        return {
            "uid": entry["uid"],
            "x": float(point.x()),
            "y": float(point.y()),
            "rotation": angle,
        }

    def _transform_states(self, items):
        return [state for state in (self._transform_state(item) for item in items)
                if state is not None]

    def _apply_transform_states(self, states):
        # Footprint placement feeds routing pad geometry, so any cached pad
        # list is stale the moment a transform is applied.
        self._invalidate_routing_cache()
        # Undo must restore exact coordinates even if the user has changed the
        # current snap grid since the edit was made.
        old_snap = getattr(self.canvas.scene(), "snap_step", 0.0)
        self.canvas.scene().snap_step = 0.0
        try:
            for state in states:
                entry = self._entry_by_uid(state["uid"])
                if entry is None:
                    continue
                item = entry["item"]
                item.setPos(QtCore.QPointF(state["x"], state["y"]))
                item.setRotation(float(state["rotation"]))
        finally:
            self.canvas.scene().snap_step = old_snap
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)
        self._refresh_properties()

    def _apply_copper_transform_states(self, states):
        """Restore track/via snapshots in place after a selection move."""
        states = list(states or [])
        if not states:
            return
        tracks = dict((track.uid, track) for track in self.tracks)
        vias = dict((via.uid, via) for via in self.vias)
        changed = set()
        for state in states:
            uid = state.get("uid")
            if uid in tracks and "points" in state:
                restored = Track.restore(state)
                model = tracks[uid]
                model.layer_id = restored.layer_id
                model.width = restored.width
                model.points = list(restored.points)
                model.net = restored.net
                changed.add(uid)
            elif uid in vias and "x" in state and "y" in state:
                restored = Via.restore(state)
                model = vias[uid]
                model.x = restored.x; model.y = restored.y
                model.diameter = restored.diameter
                model.hole_diameter = restored.hole_diameter
                model.start_layer_id = restored.start_layer_id
                model.end_layer_id = restored.end_layer_id
                model.net = restored.net
                changed.add(uid)
        for item in self.canvas.track_items + self.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and model.uid in changed:
                item.update_geometry()
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()

    def _restore_editor_selection(self, footprint_uids, copper_uids, active_uid=None):
        """Restore a mixed footprint/track/via scene selection after history replay."""
        active = self._entry_by_uid(active_uid) if active_uid else None
        if active is not None:
            self._active = active
        footprints = []
        for uid in (footprint_uids or []):
            entry = self._entry_by_uid(uid)
            if entry is not None:
                footprints.append(entry["item"])
        wanted_copper = set(copper_uids or [])
        copper = []
        for item in self.canvas.track_items + self.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and model.uid in wanted_copper:
                copper.append(item)
        self.canvas.select_editor_items(footprints, copper)
        if not footprints and not copper:
            if active is not None:
                self.set_active(active)
            else:
                self._refresh_properties()

    def _restore_selection_uids(self, uids, active_uid=None):
        active = self._entry_by_uid(active_uid) if active_uid else None
        if active is not None:
            self._active = active
        items = []
        for uid in (uids or []):
            entry = self._entry_by_uid(uid)
            if entry is not None:
                items.append(entry["item"])
        self.canvas.select_footprints(items)
        if not items:
            if active is not None:
                self.set_active(active)
            else:
                self._refresh_properties()

    @staticmethod
    def _expanded_rect(rect, amount):
        amount = max(0.0, float(amount))
        return rect.adjusted(-amount, -amount, amount, amount)

    def _courtyard_rect(self, entry):
        """Scene-space courtyard envelope, falling back to the footprint body."""
        item = entry.get("item") if entry else None
        if item is None:
            return QtCore.QRectF()
        layers = ("F.CrtYd", "B.CrtYd", "F.Courtyard", "B.Courtyard")
        rect = QtCore.QRectF()
        found = False
        for layer in layers:
            for child in getattr(item, "layer_items", {}).get(layer, []):
                r = child.sceneBoundingRect()
                rect = QtCore.QRectF(r) if not found else rect.united(r)
                found = True
        if found:
            return rect
        return item.sceneBoundingRect()

    @staticmethod
    def _scene_shape_path(item):
        """Return an item's exact shape mapped into scene coordinates."""
        try:
            return item.mapToScene(item.shape())
        except Exception:
            path = QtGui.QPainterPath()
            try:
                path.addRect(item.sceneBoundingRect())
            except Exception:
                pass
            return path

    @staticmethod
    def _paths_with_clearance_intersect(first, second, clearance):
        if first is None or second is None:
            return False
        try:
            if first.intersects(second):
                return True
            clearance = max(0.0, float(clearance))
            if clearance <= 1e-12:
                return False
            stroker = QtGui.QPainterPathStroker()
            # The stroker grows half its width on either side, therefore 2*c
            # expands the first copper shape by exactly c millimetres.
            stroker.setWidth(2.0 * clearance)
            return first.united(stroker.createStroke(first)).intersects(second)
        except Exception:
            return False

    def _footprint_drc_violation(self, moving_items):
        """Return a short DRC message for illegal component placement, if any."""
        moving = set(moving_items or [])
        if not moving:
            return ""
        courtyard_clearance = self.design_rules.get("courtyard_courtyard")
        outline_courtyard = self.design_rules.get("outline_courtyard")
        outline_pad = self.design_rules.get("outline_pad")

        moving_entries = [self.entry_for_item(item) for item in moving]
        moving_entries = [entry for entry in moving_entries if entry is not None]
        stationary = [entry for entry in self._entries if entry.get("item") not in moving]
        for entry in moving_entries:
            layer = self.design_layer_of(entry)
            if layer is None:
                continue
            rect = self._courtyard_rect(entry)
            expanded = self._expanded_rect(rect, courtyard_clearance)
            for other in stationary:
                if self.design_layer_of(other).id != layer.id:
                    continue
                if expanded.intersects(self._courtyard_rect(other)):
                    return "DRC blocked: component courtyard clearance"

            # Pad-to-pad is also a placement rule.  Use the actual rendered pad
            # paths (including rotation/rounding) instead of footprint boxes.
            pad_clearance = self.design_rules.get("pad_pad")
            moving_pad_items = list(getattr(entry.get("item"), "pad_items", {}).values())
            for other in stationary:
                other_layer = self.design_layer_of(other)
                if other_layer is None or other_layer.id != layer.id:
                    continue
                other_pad_items = list(getattr(other.get("item"), "pad_items", {}).values())
                for moving_pad in moving_pad_items:
                    moving_path = self._scene_shape_path(moving_pad)
                    for fixed_pad in other_pad_items:
                        if self._paths_with_clearance_intersect(
                                moving_path, self._scene_shape_path(fixed_pad), pad_clearance):
                            return "DRC blocked: pad-to-pad clearance"

            outlines = self._outline_models_for_layer(layer.id)
            if outlines:
                corners = [(rect.left(), rect.top()), (rect.right(), rect.top()),
                           (rect.right(), rect.bottom()), (rect.left(), rect.bottom()),
                           (rect.left(), rect.top())]
                boundary = Track(layer_id=layer.id, width=0.0, points=corners, net=None)
                if route_outline_conflicts(
                        boundary, outlines,
                        rules={"outline_track": outline_courtyard}):
                    return "DRC blocked: component courtyard too close to the board edge"
                for pad in self._routing_pads(include_hidden=True):
                    if pad.get("entry_uid") != entry.get("uid") or pad.get("layer_id") != layer.id:
                        continue
                    probe = Via(x=pad.get("x", 0.0), y=pad.get("y", 0.0),
                                diameter=max(float(pad.get("w", 0.0)), float(pad.get("h", 0.0))),
                                start_layer_id=layer.id, end_layer_id=layer.id)
                    if via_outline_conflicts(
                            probe, outlines,
                            rules={"outline_via": outline_pad}):
                        return "DRC blocked: pad too close to the board edge"
        return ""

    def _on_footprints_moving(self, items):
        """Refresh derived 2D connectivity during an in-progress footprint drag.

        This deliberately does not touch history or rebuild the sidebar.  The
        scene already owns the temporary positions, so recalculating the
        lightweight ratsnest path is enough to make every airwire follow live.
        """
        if items:
            self._refresh_ratsnest(sync_copper_nets=False)

    def _on_footprints_moved(self, changes):
        """Record one mouse-held move/rotate gesture as one undo command."""
        self._invalidate_routing_cache()
        before = []
        after = []
        for change in (changes or []):
            # v0.8.8 carries the immutable mouse-down angle as well as position
            # so undo can restore a compound move + R gesture exactly.  Keep
            # accepting the old 3-tuple shape for compatibility with external
            # callers that may emit the legacy signal payload.
            if len(change) >= 5:
                item, old_pos, old_rotation, new_pos, new_rotation = change[:5]
            else:
                item, old_pos, new_pos = change[:3]
                old_rotation = float(item.rotation())
                new_rotation = float(item.rotation())
            state_before = self._transform_state(
                item, pos=old_pos, rotation=old_rotation)
            state_after = self._transform_state(
                item, pos=new_pos, rotation=new_rotation)
            if state_before is not None and state_after is not None:
                before.append(state_before)
                after.append(state_after)
        if not before:
            return
        violation = self._footprint_drc_violation([change[0] for change in (changes or []) if change])
        if violation:
            self._apply_transform_states(before)
            self._flash_status(violation)
            return
        self._push_history({
            "kind": "transform",
            "before": before,
            "after": after,
            "selection": self._selected_uids(),
            "active": self._active_uid(),
        })
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)
        self._refresh_properties()

    def _on_selection_moved(self, payload):
        """Record one mixed footprint/track/via drag as one undo command."""
        payload = payload or {}
        footprint_changes = list(payload.get("footprints", []) or [])
        copper_changes = list(payload.get("copper", []) or [])

        before = []
        after = []
        for change in footprint_changes:
            if len(change) >= 5:
                item, old_pos, old_rotation, new_pos, new_rotation = change[:5]
            else:
                item, old_pos, new_pos = change[:3]
                old_rotation = float(item.rotation())
                new_rotation = float(item.rotation())
            state_before = self._transform_state(item, pos=old_pos, rotation=old_rotation)
            state_after = self._transform_state(item, pos=new_pos, rotation=new_rotation)
            if state_before is not None and state_after is not None:
                before.append(state_before)
                after.append(state_after)

        copper_before = []
        copper_after = []
        for change in copper_changes:
            if len(change) < 3:
                continue
            _item, state_before, state_after = change[:3]
            if state_before and state_after:
                copper_before.append(dict(state_before))
                copper_after.append(dict(state_after))

        if not before and not copper_before:
            return
        violation = self._footprint_drc_violation(
            [change[0] for change in footprint_changes if change])
        if violation:
            self._apply_transform_states(before)
            self._apply_copper_transform_states(copper_before)
            self.canvas.refresh_extent()
            self._refresh_ratsnest()
            self._flash_status(violation)
            return
        self._push_history({
            "kind": "transform",
            "before": before,
            "after": after,
            "copper_before": copper_before,
            "copper_after": copper_after,
            "selection": self._selected_uids(),
            "copper_selection": self._selected_copper_uids(),
            "active": self._active_uid(),
        })
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)
        self._refresh_properties()

    def _move_snap_point(self, point, modifiers=None):
        """Grid-snap an M-move pointer; Ctrl temporarily disables grid snap."""
        p = QtCore.QPointF(point)
        modifiers = modifiers if modifiers is not None else Qt.NoModifier
        if bool(modifiers & Qt.ControlModifier):
            return p
        if not self.canvas.snap_enabled or float(self.canvas.grid_step) <= 0.0:
            return p
        step = float(self.canvas.grid_step)
        return QtCore.QPointF(round(float(p.x()) / step) * step,
                              round(float(p.y()) / step) * step)

    @staticmethod
    def _point_delta(point, dx, dy):
        return (float(point[0]) + float(dx), float(point[1]) + float(dy))

    def _track_segment_index(self, track, point):
        """Nearest segment to the current cursor, used as the M drag handle."""
        pts = list(getattr(track, "points", []) or [])
        if len(pts) < 2:
            return -1
        target = (float(point.x()), float(point.y()))
        return min(range(len(pts) - 1),
                   key=lambda i: dist_to_segment(target, pts[i], pts[i + 1]))

    def _track_endpoint_anchored(self, track, point):
        """Whether a route endpoint is electrically anchored to external copper."""
        x, y = float(point[0]), float(point[1])
        net = getattr(track, "net", None)
        layer_id = getattr(track, "layer_id", None)
        for pad in self._routing_pads(include_hidden=True):
            if pad.get("layer_id") != layer_id:
                continue
            pad_net = pad.get("net")
            if net not in (None, "") and pad_net not in (None, "", net):
                continue
            if pad_contains_point((x, y), pad):
                return True
        for via in self.vias:
            via_net = getattr(via, "net", None)
            if net not in (None, "") and via_net not in (None, "", net):
                continue
            if not via_spans_layer(via, layer_id, self._layer_order()):
                continue
            if math.hypot(float(via.x) - x, float(via.y) - y) <= 1e-6:
                return True
        for other in self.tracks:
            if other is track or other.layer_id != layer_id or not other.points:
                continue
            other_net = getattr(other, "net", None)
            if net not in (None, "") and other_net not in (None, "", net):
                continue
            for q in (other.points[0], other.points[-1]):
                if math.hypot(float(q[0]) - x, float(q[1]) - y) <= 1e-6:
                    return True
        return False

    def _via_connected_track_points(self, via):
        """Track vertices that must follow a moved via to preserve connectivity."""
        result = {}
        order = self._layer_order()
        net = getattr(via, "net", None)
        x, y = float(via.x), float(via.y)
        for track in self.tracks:
            if net not in (None, "") and getattr(track, "net", None) not in (None, "", net):
                continue
            if not via_spans_layer(via, track.layer_id, order):
                continue
            indices = [i for i, q in enumerate(track.points)
                       if math.hypot(float(q[0]) - x, float(q[1]) - y) <= 1e-6]
            if indices:
                result[track.uid] = indices
        return result

    def _copper_model_by_uid(self, uid):
        for model in self.tracks:
            if model.uid == uid:
                return model
        for model in self.vias:
            if model.uid == uid:
                return model
        return None

    def _assign_copper_state(self, state):
        """Restore one copper snapshot without triggering expensive view refreshes."""
        state = state or {}
        model = self._copper_model_by_uid(state.get("uid"))
        if model is None:
            return None
        if isinstance(model, Track) and "points" in state:
            model.layer_id = state.get("layer_id")
            model.width = float(state.get("width", model.width))
            model.points = normalize_track_points(state.get("points", []))
            model.net = state.get("net")
        elif isinstance(model, Via) and "x" in state:
            model.x = float(state.get("x", model.x)); model.y = float(state.get("y", model.y))
            model.diameter = float(state.get("diameter", model.diameter))
            model.hole_diameter = float(state.get("hole_diameter", model.hole_diameter))
            model.start_layer_id = state.get("start_layer_id")
            model.end_layer_id = state.get("end_layer_id")
            model.net = state.get("net")
        return model

    def _refresh_move_copper_items(self, uids):
        wanted = set(uids or [])
        for item in self.canvas.track_items + self.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and model.uid in wanted:
                item.update_geometry()

    def _copper_move_violation(self, moving_uids):
        """Run interactive DRC on copper changed by the M move tool.

        Everything not being moved is queried through one broad-phase index, so
        the cost of a drag step no longer scales with the size of the board.
        """
        moving = set(moving_uids or [])
        if not moving:
            return ""
        order = self._layer_order()
        index = self._obstacle_index(exclude_uids=moving)
        for track in self.tracks:
            if track.uid not in moving:
                continue
            outlines = self._outline_models_for_layer(track.layer_id)
            conflicts = route_outline_conflicts(track, outlines, rules=self.design_rules)
            if conflicts:
                return "DRC blocked: track too close to the board edge"
            conflicts = route_conflicts(track, None, None, None, order,
                                        active_net=track.net,
                                        rules=self.design_rules, index=index)
            if conflicts:
                return "DRC blocked: %s" % self._route_collision_message(conflicts)
        for via in self.vias:
            if via.uid not in moving:
                continue
            # A via spans several independent copper reference-plane outlines.
            # Validate them separately so coincident/nested layer boundaries do
            # not cancel through the BoardOutline even/odd fill rule.
            outline_sets = [self._outline_models_for_layer(layer_id)
                            for layer_id in order
                            if via_spans_layer(via, layer_id, order)]
            if via_layer_outline_conflicts(via, outline_sets, rules=self.design_rules):
                return "DRC blocked: via too close to the board edge"
            conflicts = via_conflicts(via, None, None, None, order,
                                      active_net=via.net,
                                      rules=self.design_rules, index=index)
            if conflicts:
                return "DRC blocked: %s" % self._route_collision_message(conflicts)
        return ""

    def _keep45_enabled(self, modifiers=None):
        """Whether a copper drag should hold H/V/45 angles.

        KiCad keeps routed copper on the 45-degree grid by default and offers an
        escape hatch for free-angle geometry; Alt is that escape hatch here.
        """
        if modifiers is not None and bool(modifiers & Qt.AltModifier):
            return False
        return bool(getattr(self, "drag_keep_45", True))

    def _track_corner_index(self, track, point, tolerance):
        """Index of a track vertex close enough to the cursor to grab.

        KiCad distinguishes grabbing a corner from grabbing a segment.  Corners
        win when the cursor is within ``tolerance`` of one, because a vertex is
        the smaller, more deliberate target.
        """
        pts = list(getattr(track, "points", []) or [])
        if len(pts) < 2:
            return -1
        target = (float(point.x()), float(point.y()))
        best, best_d = -1, float(tolerance)
        for i, p in enumerate(pts):
            d = math.hypot(float(p[0]) - target[0], float(p[1]) - target[1])
            if d <= best_d:
                best, best_d = i, d
        return best

    def _corner_grab_tolerance(self):
        """Vertex grab radius in millimetres, kept roughly constant on screen."""
        try:
            px = max(1e-6, float(self.canvas.px_per_mm()))
            return max(0.05, min(1.5, 8.0 / px))
        except Exception:
            return 0.25

    def _begin_copper_drag(self, item, point, modifiers=None):
        """Begin a direct mouse drag that preserves routed connectivity.

        This is intentionally different from M.  Dragging a track edits the
        clicked segment while anchored endpoints stay connected; dragging a via
        pulls exact connected track vertices with it.
        """
        if self.view_mode != "2d" or self._move_session is not None:
            return False
        if self.routing is not None or self.route_tool != "select":
            return False
        model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
        if model is None:
            return False
        start = self._move_snap_point(point, modifiers)
        before = {model.uid: model.snapshot()}
        session = {
            "start_cursor": (float(start.x()), float(start.y())),
            "last_target": (float(start.x()), float(start.y())),
            "item": item,
            "before": before,
            "changed_uids": set([model.uid]),
            "selection": self._selected_uids(),
            "copper_selection": self._selected_copper_uids(),
            "active": self._active_uid(),
            "violation": "",
        }
        if isinstance(model, Track):
            # A vertex under the cursor is the more deliberate target, so a
            # corner drag wins over a segment drag when one is in reach.
            corner = self._track_corner_index(model, start,
                                              self._corner_grab_tolerance())
            if corner >= 0:
                session["mode"] = "track_corner"
                session["track_uid"] = model.uid
                session["corner_index"] = corner
                try:
                    item.set_selected_segment(min(corner, len(model.points) - 2),
                                              whole=False)
                except (AttributeError, ValueError):
                    pass
                session["last_valid"] = dict(
                    (uid, dict(state)) for uid, state in before.items())
                self._copper_drag_session = session
                self._begin_routing_cache()
                return True
            seg = getattr(item, "selected_segment_index", None)
            if seg is None or int(seg) < 0:
                seg = self._track_segment_index(model, start)
            seg = int(seg)
            if seg < 0 or seg + 1 >= len(model.points):
                return False
            session["mode"] = "track_segment"
            session["track_uid"] = model.uid
            session["segment_index"] = seg
            pts = list(model.points)
            session["anchor_start"] = bool(
                seg == 0 and self._track_endpoint_anchored(model, pts[0]))
            session["anchor_end"] = bool(
                seg + 1 == len(pts) - 1 and self._track_endpoint_anchored(model, pts[-1]))
            # Keep the clicked segment visually selected during the edit.
            try:
                item.set_selected_segment(seg, whole=False)
            except AttributeError:
                pass
        elif isinstance(model, Via):
            session["mode"] = "via_drag"
            session["via_uid"] = model.uid
            links = self._via_connected_track_points(model)
            session["via_links"] = dict((uid, sorted(indices)) for uid, indices in links.items())
            for uid in links:
                linked = self._copper_model_by_uid(uid)
                if linked is not None:
                    before[uid] = linked.snapshot()
                    session["changed_uids"].add(uid)
        else:
            return False
        session["last_valid"] = dict((uid, dict(state)) for uid, state in before.items())
        self._copper_drag_session = session
        self._begin_routing_cache()
        return True

    def _apply_copper_drag_target(self, point, modifiers=None):
        session = self._copper_drag_session
        if session is None:
            return False
        target = self._move_snap_point(point, modifiers)
        sx, sy = session["start_cursor"]
        dx, dy = float(target.x()) - sx, float(target.y()) - sy

        # Always derive the current candidate from immutable mouse-down data.
        for state in session["before"].values():
            self._assign_copper_state(state)

        keep45 = self._keep45_enabled(modifiers)

        if session["mode"] == "track_corner":
            uid = session["track_uid"]
            track = self._copper_model_by_uid(uid)
            base = session["before"].get(uid, {})
            points = [(float(p[0]), float(p[1])) for p in base.get("points", [])]
            if isinstance(track, Track):
                track.points = drag_track_corner(
                    points, int(session["corner_index"]), dx, dy, keep45=keep45)
        elif session["mode"] == "track_segment":
            uid = session["track_uid"]
            track = self._copper_model_by_uid(uid)
            base = session["before"].get(uid, {})
            points = [(float(p[0]), float(p[1])) for p in base.get("points", [])]
            i = int(session["segment_index"])
            if isinstance(track, Track):
                track.points = drag_track_segment(
                    points, i, dx, dy,
                    anchor_start=session.get("anchor_start", False),
                    anchor_end=session.get("anchor_end", False),
                    keep45=keep45)
        else:
            uid = session["via_uid"]
            via = self._copper_model_by_uid(uid)
            base = session["before"].get(uid, {})
            if isinstance(via, Via):
                via.x = float(base.get("x", via.x)) + dx
                via.y = float(base.get("y", via.y)) + dy
            for track_uid, indices in session.get("via_links", {}).items():
                track = self._copper_model_by_uid(track_uid)
                base_track = session["before"].get(track_uid, {})
                if not isinstance(track, Track):
                    continue
                points = [(float(p[0]), float(p[1])) for p in base_track.get("points", [])]
                track.points = drag_via_attached_points(points, indices, dx, dy,
                                                        keep45=keep45)

        changed = session["changed_uids"]
        self._refresh_move_copper_items(changed)
        violation = self._copper_move_violation(changed)
        if violation:
            # Hard interactive DRC: keep the geometry at the most recent legal
            # position even if the pointer itself crosses an obstacle.
            for state in session["last_valid"].values():
                self._assign_copper_state(state)
            self._refresh_move_copper_items(changed)
            session["violation"] = violation
            self.status_hover.setText("%s — drag blocked" % violation)
            self._refresh_ratsnest_throttled()
            return False

        session["violation"] = ""
        session["last_target"] = (float(target.x()), float(target.y()))
        session["last_valid"] = {}
        for uid in changed:
            current = self._copper_model_by_uid(uid)
            if current is not None:
                session["last_valid"][uid] = current.snapshot()
        self.canvas.refresh_extent()
        self._refresh_ratsnest_throttled()
        hint = " · 45°" if keep45 else " · Alt frees the angle"
        if session["mode"] == "track_corner":
            self.status_hover.setText("Drag track corner — release to place" + hint)
        elif session["mode"] == "track_segment":
            self.status_hover.setText("Drag track segment — release to place" + hint)
        else:
            self.status_hover.setText("Drag via — connected tracks follow" + hint)
        return True

    def _on_copper_drag_pointer(self, point, modifiers):
        if self._copper_drag_session is not None:
            self._apply_copper_drag_target(point, modifiers)

    def _finish_copper_drag(self, point, modifiers=None):
        session = self._copper_drag_session
        if session is None:
            return False
        self._apply_copper_drag_target(point, modifiers)
        # If the release itself is illegal, the previous legal geometry is
        # already restored by _apply_copper_drag_target.
        before = [dict(state) for state in session["before"].values()]
        after = []
        for state in before:
            model = self._copper_model_by_uid(state.get("uid"))
            if model is not None:
                after.append(model.snapshot())
        if before != after:
            self._push_history({
                "kind": "transform",
                "before": [],
                "after": [],
                "copper_before": before,
                "copper_after": after,
                "selection": list(session.get("selection", [])),
                "copper_selection": list(session.get("copper_selection", [])),
                "active": session.get("active"),
            })
        self._copper_drag_session = None
        self._end_routing_cache()
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()
        if before != after:
            self._flash_status("Interactive copper drag complete")
        return True

    def cancel_copper_drag(self):
        session = self._copper_drag_session
        if session is None:
            return False
        for state in session.get("before", {}).values():
            self._assign_copper_state(state)
        self._refresh_move_copper_items(session.get("changed_uids", ()))
        self._copper_drag_session = None
        self._end_routing_cache()
        try:
            self.canvas.cancel_direct_copper_drag()
        except AttributeError:
            pass
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self.status_hover.setText("Copper drag cancelled")
        return True

    def select_complete_track(self):
        """Promote selected segment(s) to the complete connected copper island (U)."""
        selected = list(self.canvas.selected_copper_items())
        if not selected:
            self._flash_status("Select a track segment or via, then press U")
            return False
        topo = build_copper_topology(self.tracks, self.vias, self._layer_order())
        dsu = topo["dsu"]
        roots = set()
        for item in selected:
            track = getattr(item, "track_model", None)
            via = getattr(item, "via_model", None)
            if track is not None:
                roots.add(dsu.find("t:" + track.uid))
            elif via is not None:
                roots.add(dsu.find("v:" + via.uid))
        if not roots:
            return False
        uids = set()
        for key, _kind, model in topo["items"]:
            if dsu.find(key) in roots:
                uids.add(model.uid)
        items = []
        for item in self.canvas.track_items + self.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and model.uid in uids:
                items.append(item)
        self.canvas.select_copper_items(items)
        for item in items:
            if hasattr(item, "set_selected_segment"):
                item.set_selected_segment(None, whole=True)
        self._flash_status("Selected complete connected track")
        return True

    def begin_move_selected(self):
        """Start the explicit M physical-translation tool.

        M translates the selected objects as rigid editor items.  It does not
        stretch neighbouring track geometry and a moved via does not pull its
        attached tracks; those connectivity-preserving edits belong to direct
        mouse dragging.  This deliberate split mirrors the user's expected PCB
        editor model: M = move, drag = interactive route reshape.
        """
        if self.view_mode != "2d" or self._move_session is not None:
            return False
        if self._copper_drag_session is not None:
            self._flash_status("Finish the active copper drag before moving objects")
            return False
        if self.routing is not None:
            self._flash_status("Finish or cancel the active route before moving objects")
            return False
        if self.route_tool != "select":
            self._activate_select_tool()

        footprints = list(self.canvas.selected_footprints())
        copper_items = list(self.canvas.selected_copper_items())
        if not footprints and not copper_items:
            self._flash_status("Select a footprint, track or via, then press M")
            return False

        start = self._move_snap_point(self.canvas.editor_cursor_scene())
        footprint_before = self._transform_states(footprints)
        copper_before = {}
        selected_tracks = []
        selected_vias = []
        for item in copper_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is None:
                continue
            copper_before[model.uid] = model.snapshot()
            if isinstance(model, Track):
                selected_tracks.append(model)
            elif isinstance(model, Via):
                selected_vias.append(model)

        self._move_session = {
            "mode": "translate",
            "start_cursor": (float(start.x()), float(start.y())),
            "last_target": (float(start.x()), float(start.y())),
            "footprints": footprints,
            "footprint_before": footprint_before,
            "copper_before": copper_before,
            "selected_track_uids": [t.uid for t in selected_tracks],
            "selected_via_uids": [v.uid for v in selected_vias],
            "selection": self._selected_uids(),
            "copper_selection": self._selected_copper_uids(),
            "active": self._active_uid(),
            "violation": "",
        }
        self.canvas.set_interactive_move_enabled(True)
        self.canvas.setFocus()
        self.status_hover.setText(
            "Move selection — click to place; Esc cancels; Ctrl disables grid snap")
        return True

    def _apply_move_session_target(self, point, modifiers=None):
        session = self._move_session
        if session is None:
            return False
        target = self._move_snap_point(point, modifiers)
        sx, sy = session["start_cursor"]
        dx, dy = float(target.x()) - sx, float(target.y()) - sy
        session["last_target"] = (float(target.x()), float(target.y()))

        # Footprints are positioned from their immutable M-start snapshots on
        # every frame; no incremental deltas are accumulated.
        old_snap = getattr(self.canvas.scene(), "snap_step", 0.0)
        self.canvas.scene().snap_step = 0.0
        try:
            states = dict((state["uid"], state) for state in session["footprint_before"])
            for item in session["footprints"]:
                entry = self.entry_for_item(item)
                state = states.get(entry.get("uid") if entry else None)
                if state is not None:
                    item.setPos(QtCore.QPointF(float(state["x"]) + dx,
                                               float(state["y"]) + dy))
                    item.setRotation(float(state["rotation"]))
        finally:
            self.canvas.scene().snap_step = old_snap

        # First restore copper to the exact M-start geometry, then derive this
        # frame from that baseline. This makes mouse reversals perfectly stable.
        for state in session["copper_before"].values():
            self._assign_copper_state(state)

        changed_uids = set()
        for uid in session["selected_track_uids"]:
            track = self._copper_model_by_uid(uid)
            base = session["copper_before"].get(uid, {})
            if isinstance(track, Track):
                track.points = [self._point_delta(p, dx, dy)
                                for p in base.get("points", [])]
                changed_uids.add(uid)
        for uid in session["selected_via_uids"]:
            via = self._copper_model_by_uid(uid)
            base = session["copper_before"].get(uid, {})
            if isinstance(via, Via):
                via.x = float(base.get("x", via.x)) + dx
                via.y = float(base.get("y", via.y)) + dy
                changed_uids.add(uid)

        self._refresh_move_copper_items(changed_uids)
        self.canvas.refresh_extent()
        self._refresh_ratsnest(sync_copper_nets=False)

        violation = self._footprint_drc_violation(session["footprints"])
        if not violation:
            violation = self._copper_move_violation(changed_uids)
        session["violation"] = violation
        if violation:
            self.status_hover.setText("%s — move remains active" % violation)
        else:
            self.status_hover.setText("Move — click to place; Esc cancels; Ctrl disables grid snap")
        return True

    def _on_move_pointer(self, point, modifiers):
        if self._move_session is not None:
            self._apply_move_session_target(point, modifiers)

    def _finish_move_session_ui(self):
        self.canvas.set_interactive_move_enabled(False)
        self._move_session = None
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()

    def _on_move_click(self, point, modifiers):
        session = self._move_session
        if session is None:
            return False
        self._apply_move_session_target(point, modifiers)
        session = self._move_session
        if session.get("violation"):
            self._flash_status(session["violation"])
            return False

        before_fp = list(session["footprint_before"])
        after_fp = self._transform_states(session["footprints"])
        before_copper = [dict(state) for state in session["copper_before"].values()]
        after_copper = []
        for state in before_copper:
            model = self._copper_model_by_uid(state.get("uid"))
            if model is not None:
                after_copper.append(model.snapshot())

        if before_fp != after_fp or before_copper != after_copper:
            self._push_history({
                "kind": "transform",
                "before": before_fp,
                "after": after_fp,
                "copper_before": before_copper,
                "copper_after": after_copper,
                "selection": list(session.get("selection", [])),
                "copper_selection": list(session.get("copper_selection", [])),
                "active": session.get("active"),
            })
        self._finish_move_session_ui()
        self._flash_status("Move complete")
        return True

    def commit_move_at_cursor(self):
        if self._move_session is None:
            return False
        x, y = self._move_session.get("last_target", self._move_session["start_cursor"])
        return self._on_move_click(QtCore.QPointF(float(x), float(y)), Qt.NoModifier)

    def cancel_move_selected(self):
        session = self._move_session
        if session is None:
            return False
        self._apply_transform_states(session.get("footprint_before", []))
        self._apply_copper_transform_states(session.get("copper_before", {}).values())
        self.canvas.set_interactive_move_enabled(False)
        self._move_session = None
        self.status_hover.setText("Move cancelled")
        return True

    def rotate_selected(self, clockwise=False):
        """Rotate selected footprints 90 degrees as one selection.

        A single footprint rotates around its own KiCad origin.  With multiple
        footprints selected, the whole selection rotates around the centre of
        its combined visible bounding rectangle, matching KiCad's group-rotate
        behaviour.

        QGraphicsItem uses a Y-down coordinate system, so positive Qt rotation
        appears clockwise on screen.  ``R`` therefore subtracts 90 degrees for
        the requested counter-clockwise turn, while Shift+R adds 90 degrees.
        """
        items = self.canvas.selected_footprints()
        if not items:
            return False

        self.canvas.select_pad(None, None)
        before = self._transform_states(items)
        delta = 90.0 if clockwise else -90.0

        center = None
        if len(items) > 1:
            bounds = None
            for item in items:
                rect = item.mapRectToScene(item.visible_rect())
                bounds = QtCore.QRectF(rect) if bounds is None else bounds.united(rect)
            if bounds is not None:
                center = bounds.center()

        old_snap = getattr(self.canvas.scene(), "snap_step", 0.0)
        self.canvas.scene().snap_step = 0.0
        try:
            if center is not None:
                radians = math.radians(delta)
                cos_a, sin_a = math.cos(radians), math.sin(radians)
                for item in items:
                    pos = QtCore.QPointF(item.pos())
                    dx = float(pos.x() - center.x())
                    dy = float(pos.y() - center.y())
                    item.setPos(QtCore.QPointF(
                        center.x() + cos_a * dx - sin_a * dy,
                        center.y() + sin_a * dx + cos_a * dy))
                    item.setRotation((float(item.rotation()) + delta) % 360.0)
            else:
                for item in items:
                    item.setRotation((float(item.rotation()) + delta) % 360.0)
        finally:
            self.canvas.scene().snap_step = old_snap

        # If R is pressed while the left mouse button is holding the current
        # selection, rotation belongs to that same mouse gesture.  Rebase only
        # the *live* continuation state; the canvas keeps its immutable
        # mouse-down transforms for release/undo.  History is therefore deferred
        # to footprintsMoved, which records start -> final as one command.
        in_mouse_gesture = self.canvas.rebase_drag_transform(items)

        after = self._transform_states(items)
        if not in_mouse_gesture:
            self._push_history({
                "kind": "transform",
                "before": before,
                "after": after,
                "selection": self._selected_uids(),
                "active": self._active_uid(),
            })
        self.canvas.refresh_extent()
        self._refresh_ratsnest(sync_copper_nets=not in_mouse_gesture)
        self._refresh_3d_if_visible(force=True)
        self._refresh_properties()
        return True

    def _entry_snapshot(self, entry, index):
        item = entry["item"]
        return {
            "index": int(index),
            "uid": entry["uid"],
            "fp": entry["fp"],
            "x": float(item.pos().x()),
            "y": float(item.pos().y()),
            "rotation": float(item.rotation()),
            "layers": dict(getattr(item, "layer_visibility", {})),
            "hidden_texts": bool(getattr(item, "_hidden_texts_visible", False)),
            "design_layer_id": getattr(item, "design_layer_id", None),
            "ref": entry.get("ref", ""),
            "value": entry.get("value", ""),
            "footprint_name": entry.get("footprint_name", ""),
            "placeholder": bool(entry.get("placeholder", False)),
            "model_adjust": dict(entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}),
            "model_adjust_mode": entry.get("model_adjust_mode", "auto"),
        }

    def _rebuild_after_entry_change(self):
        self._invalidate_routing_cache()
        self._apply_design_layers()
        self._rebuild_footprint_list()
        self._rebuild_stack()
        if self._active not in self._entries:
            self._active = self._entries[0] if self._entries else None
        if self._active is not None:
            self.set_active(self._active)
        else:
            if self.hosted:
                self.act_reload.setEnabled(False)
            self.title.setText("\u2014")
            self.tag.hide()
            self._rebuild_layers()
            self._rebuild_pads()
            self._refresh_properties()
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)

    def _delete_uids(self, uids):
        uid_set = set(uids or [])
        targets = [entry for entry in self._entries if entry.get("uid") in uid_set]
        if not targets:
            return False

        self.canvas.select_pad(None, None)
        scene = self.canvas.scene()
        scene.blockSignals(True)
        try:
            for entry in targets:
                self.canvas.remove_footprint(entry["item"])
            self._entries = [entry for entry in self._entries
                             if entry.get("uid") not in uid_set]
        finally:
            scene.blockSignals(False)

        self._selected = []
        self._rebuild_after_entry_change()
        self.canvas.select_footprints([])
        return True

    def _restore_deleted_entries(self, states):
        restored = []
        # Ascending indices let each insertion recover the original list order.
        for saved in sorted(states or [], key=lambda state: state["index"]):
            item = self.canvas.add_footprint(
                saved["fp"], QtCore.QPointF(saved["x"], saved["y"]),
                reference_override=saved.get("ref", ""))
            item.setRotation(float(saved.get("rotation", 0.0)))
            design_layer_id = saved.get("design_layer_id")
            item.design_layer_id = (design_layer_id
                                    if design_layer_id is not None
                                    and self.stack.has(design_layer_id)
                                    else self.stack.active_id)
            for layer, visible in saved.get("layers", {}).items():
                if layer in item.layer_items:
                    item.set_layer_visible(layer, visible)
            item.set_hidden_texts_visible(saved.get("hidden_texts", False))

            entry = {
                "item": item, "fp": saved["fp"], "uid": saved["uid"],
                "ref": saved.get("ref", ""), "value": saved.get("value", ""),
                "footprint_name": saved.get("footprint_name", ""),
                "placeholder": bool(saved.get("placeholder", False)),
                "model_adjust": dict(saved.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}),
                "model_adjust_mode": saved.get("model_adjust_mode", "auto"),
            }
            index = max(0, min(int(saved["index"]), len(self._entries)))
            self._entries.insert(index, entry)

            # Canvas hit/selection order should match the sidebar/model order.
            try:
                self.canvas.footprints.remove(item)
                self.canvas.footprints.insert(index, item)
            except ValueError:
                pass
            restored.append(entry)

        self._rebuild_after_entry_change()
        return restored

    def delete_selected(self):
        """Delete selected routed copper or footprints as one edit."""
        if self.canvas.selected_copper_items():
            return self._delete_selected_copper()
        return self.delete_selected_footprints()

    def _sync_tool_rail(self):
        """Keep the tool rail available in 2D only."""
        if not hasattr(self, "tool_rail"):
            return
        self.tool_rail.setVisible(getattr(self, "view_mode", "2d") == "2d")

    # -- board solid -------------------------------------------------------
    def _board_state(self):
        solid = self.board_solid
        return {"solid": solid, "placement": dict(solid.placement) if solid is not None else None}

    def _apply_board_state(self, state):
        state = state or {}
        solid = state.get("solid")
        if solid is not None and state.get("placement"):
            solid.set_placement(**state["placement"])
        self.board_solid = solid
        self._board_geometry_changed()

    def _board_geometry_changed(self, immediate=True):
        """Every consumer of the board shape must refresh after a change."""
        self._board_outline_cache = {}
        self._invalidate_routing_cache()
        if immediate:
            self._board_refresh_timer.stop()
            self._refresh_board_views()
        else:
            self._board_refresh_timer.start()

    def _refresh_board_views(self):
        self._apply_board_sections()
        self._sync_board_section()
        self._refresh_3d_if_visible(force=True)

    def _on_stack_geometry_changed(self):
        """Plane heights moved: re-derive every layer's board section."""
        self._rebuild_stackup()
        self._board_geometry_changed()
        self.refresh_modified_state()

    def _board_sections(self):
        """Copper layer id -> section loops in editor (Y-down) coordinates."""
        solid = self.board_solid
        if solid is None:
            return {}
        heights = self.stack.reference_heights()
        return dict((layer.id, solid.board_section(heights.get(layer.id, 0.0)))
                    for layer in self.stack.copper_layers())

    def _apply_board_sections(self):
        if not hasattr(self, "canvas"):
            return
        show = getattr(self, "btn_rail_board", None)
        if self.board_solid is None or (show is not None and not show.isChecked()):
            self.canvas.set_board_sections({}, None, has_solid=False)
            return
        visible = [layer.id for layer in self.stack.copper_layers() if layer.visible]
        active = self.stack.active_copper()
        self.canvas.set_board_sections(self._board_sections(),
                                       active.id if active is not None else None,
                                       visible=visible, has_solid=True)

    def _sync_board_section(self):
        """Refresh the Board solid sidebar card in place."""
        if not hasattr(self, "board_card"):
            return
        solid = self.board_solid
        self.board_empty.setVisible(solid is None)
        self.board_card.setVisible(solid is not None)
        if solid is None:
            self.sec_board.set_count("")
            self._board_rows_signature = None
            return
        sx, sy, sz = solid.size()
        kind = {"step": "STEP", "freecad": "FreeCAD", "legacy": "converted outline",
                "generated": "generated"}.get(solid.kind, solid.kind)
        self.board_name.setText(solid.label)
        self.board_name.setToolTip(str(solid.source.get("path") or solid.label))
        self.board_meta.setText("%s · %.2f × %.2f × %.3f mm · %d triangles"
                                % (kind, sx, sy, sz, len(solid.triangles)))
        self._updating_board_controls = True
        try:
            index = self.board_up.findData(solid.placement["up_axis"])
            self.board_up.setCurrentIndex(max(0, index))
            # Editor coordinates are Y-down; the solid's world frame is Y-up.
            # Never reformat the editor the user is typing into.
            for spin, value in ((self.board_rotation, solid.placement["rotation"]),
                                (self.board_offset["x"], solid.placement["offset_x"]),
                                (self.board_offset["y"], -solid.placement["offset_y"])):
                if not (spin.hasFocus() and abs(spin.value() - float(value)) < 1e-9):
                    spin.setValue(float(value))
        finally:
            self._updating_board_controls = False
        self.btn_board_reload.setEnabled(solid.kind in ("step", "freecad"))

        heights = self.stack.reference_heights()
        rows = [(layer.id, layer.name, layer.color, round(heights.get(layer.id, 0.0), 6))
                for layer in self.stack.copper_top_down()]
        signature = (solid.signature(), tuple(rows))
        if signature == getattr(self, "_board_rows_signature", None):
            return
        self._board_rows_signature = signature
        grid = self.board_layer_grid
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        problems = 0
        for row, layer in enumerate(self.stack.copper_top_down()):
            z = heights.get(layer.id, 0.0)
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(9, 9)
            swatch.setStyleSheet("background: %s; border-radius: 1px; "
                                 "border: 1px solid rgba(0,0,0,.35);" % layer.color)
            name = QtWidgets.QLabel(layer.name)
            name.setObjectName("dim")
            zl = QtWidgets.QLabel("%.3f" % z)
            zl.setObjectName("boardMeta")
            zl.setToolTip("Reference-plane height above the solid's lowest point (mm)")
            text, role = self._board_layer_status(z)
            status = QtWidgets.QLabel(text)
            status.setObjectName(role)
            if role == "layerBad":
                problems += 1
                status.setToolTip("This plane does not cut the solid; nothing can be "
                                  "placed or routed on %s. Adjust the stackup." % layer.name)
            grid.addWidget(swatch, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(zl, row, 2, Qt.AlignRight)
            grid.addWidget(status, row, 3, Qt.AlignRight)
        grid.setColumnStretch(1, 1)
        self.sec_board.set_count("⚠ %d" % problems if problems else "")

    def _set_board_solid(self, solid, message):
        before = self._board_state()
        self.board_solid = solid
        after = self._board_state()
        self._push_history({"kind": "board_solid", "before": before, "after": after})
        self._board_geometry_changed()
        if solid is not None and not self._entries:
            self.canvas.fit()
        self.refresh_modified_state()
        self._report_board_fit(message)

    def _report_board_fit(self, message):
        """Flash ``message`` plus a hint when items fall outside the board."""
        outside = [layer.name for layer in self.stack.copper_layers()
                   if self.board_solid is not None
                   and not self._board_sections().get(layer.id)]
        violation = ""
        for layer in self.stack.copper_layers():
            violation = self._layer_outline_drc_violation(layer.id)
            if violation:
                break
        notes = [message]
        if outside:
            notes.append("outside solid: %s" % ", ".join(outside))
        if violation:
            notes.append("items now cross the board edge (Tools ▸ DRC Check)")
        self._flash_status(" — ".join(notes))

    def _with_wait_cursor(self, fn, *args, **kwargs):
        try:
            QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        except Exception:
            pass
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                QtWidgets.QApplication.restoreOverrideCursor()
            except Exception:
                pass

    def _board_error(self, title, exc):
        QtWidgets.QMessageBox.warning(self, title, str(exc))

    def _inherited_placement(self):
        """Replacing/reloading keeps the user's orientation and position."""
        return dict(self.board_solid.placement) if self.board_solid is not None else None

    def import_board_step_dialog(self, _checked=False):
        start = self._last_import_dir()
        if self.board_solid is not None and self.board_solid.source.get("path"):
            start = os.path.dirname(str(self.board_solid.source["path"])) or start
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import board solid", start, "STEP solids (*.step *.stp *.STEP *.STP);;All files (*)")
        if not path:
            return False
        return self.import_board_step(path)

    def import_board_step(self, path):
        try:
            solid = self._with_wait_cursor(boardsolidmod.load_step, path,
                                           placement=self._inherited_placement())
        except (BoardSolidError, Exception) as exc:
            self._board_error("Import board solid", exc)
            return False
        self.remember_import_directory(path)
        self._set_board_solid(solid, "Board solid imported: %s" % solid.label)
        return True

    def import_board_freecad_dialog(self, _checked=False):
        candidates = boardsolidmod.freecad_solid_candidates()
        if not candidates:
            QtWidgets.QMessageBox.information(
                self, "Board solid from FreeCAD",
                "No solid objects are open in FreeCAD.\n\nOpen or model the board body "
                "in a FreeCAD document (any Part/PartDesign solid works), or import a "
                "STEP file instead.")
            return False
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Board solid from FreeCAD")
        dialog.resize(420, 360)
        outer = QtWidgets.QVBoxLayout(dialog)
        note = QtWidgets.QLabel("Choose the solid that defines the board. Selected "
                                "objects in the 3D view are listed first.")
        note.setObjectName("faint"); note.setWordWrap(True)
        outer.addWidget(note)
        listing = QtWidgets.QListWidget(dialog)
        for cand in candidates:
            text = "%s   — %s" % (cand["label"], cand["doc_label"])
            if cand.get("selected"):
                text = "● " + text
            item = QtWidgets.QListWidgetItem(text)
            item.setData(Qt.UserRole, (cand["document"], cand["object"]))
            listing.addItem(item)
        listing.setCurrentRow(0)
        listing.itemDoubleClicked.connect(lambda _item: dialog.accept())
        outer.addWidget(listing, 1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if not exec_dialog(dialog) or listing.currentItem() is None:
            return False
        document, obj_name = listing.currentItem().data(Qt.UserRole)
        return self.import_board_freecad(document, obj_name)

    def import_board_freecad(self, document, obj_name):
        try:
            solid = self._with_wait_cursor(boardsolidmod.solid_from_freecad_object,
                                           document, obj_name,
                                           placement=self._inherited_placement())
        except (BoardSolidError, Exception) as exc:
            self._board_error("Board solid from FreeCAD", exc)
            return False
        self._set_board_solid(solid, "Board solid taken from FreeCAD: %s" % solid.label)
        return True

    def reload_board_solid(self, _checked=False):
        solid = self.board_solid
        if solid is None:
            return False
        try:
            if solid.kind == "freecad":
                fresh = self._with_wait_cursor(
                    boardsolidmod.solid_from_freecad_object,
                    solid.source.get("document"), solid.source.get("object"),
                    placement=solid.placement)
            elif solid.kind == "step":
                path = str(solid.source.get("path") or "")
                if not os.path.isfile(path):
                    raise BoardSolidError("The STEP file is missing:\n%s\n\nUse Replace to "
                                          "pick its new location." % path)
                fresh = self._with_wait_cursor(boardsolidmod.load_step, path,
                                               placement=solid.placement)
                fresh.source = dict(solid.source, **fresh.source)
            else:
                raise BoardSolidError("This board solid has no source to reload from.")
        except (BoardSolidError, Exception) as exc:
            self._board_error("Reload board solid", exc)
            return False
        self._set_board_solid(fresh, "Board solid reloaded: %s" % fresh.label)
        return True

    def remove_board_solid(self, _checked=False):
        if self.board_solid is None:
            return False
        self._set_board_solid(None, "Board solid removed (undo with Ctrl+Z)")
        return True

    def center_board_solid(self, _checked=False):
        solid = self.board_solid
        if solid is None:
            return False
        before = self._board_state()
        if not solid.center_on_origin():
            return False
        self._push_history({"kind": "board_solid", "before": before, "after": self._board_state()})
        self._board_geometry_changed()
        self.refresh_modified_state()
        self._report_board_fit("Board solid centred on the origin")
        return True

    def _on_board_placement_edit(self, commit=False):
        """Live placement edits; one undo step per finished edit."""
        solid = self.board_solid
        if solid is None or self._updating_board_controls:
            return
        if self._board_edit_before is None:
            self._board_edit_before = self._board_state()
        changed = solid.set_placement(
            up_axis=self.board_up.currentData(),
            rotation=self.board_rotation.value(),
            offset_x=self.board_offset["x"].value(),
            offset_y=-self.board_offset["y"].value())
        if changed:
            self._board_geometry_changed(immediate=commit)
        if commit:
            before, self._board_edit_before = self._board_edit_before, None
            if before and before.get("placement") != solid.placement:
                self._push_history({"kind": "board_solid", "before": before,
                                    "after": self._board_state()})
                self.refresh_modified_state()
                self._report_board_fit("Board solid placement updated")

    def _layer_outline_drc_violation(self, layer_id):
        """Validate existing items against the board section on one copper layer."""
        layer = self.stack.by_id(layer_id)
        if layer is None:
            return ""
        outlines = self._outline_models_for_layer(layer.id)
        if not outlines:
            return ""
        rules = self.design_rules
        for track in self.tracks:
            if track.layer_id == layer.id and route_outline_conflicts(track, outlines, rules=rules):
                return "DRC blocked: outline-to-track clearance"
        order = self._layer_order()
        for via in self.vias:
            if via_spans_layer(via, layer.id, order) and via_outline_conflicts(via, outlines, rules=rules):
                return "DRC blocked: outline-to-via clearance"
        for pad in self._routing_pads(include_hidden=True):
            if pad.get("layer_id") != layer.id:
                continue
            # A diameter based on the larger pad dimension is conservative for
            # rotated rectangular/oval pads and exact for circular pads.
            probe = Via(x=pad.get("x", 0.0), y=pad.get("y", 0.0),
                        diameter=max(float(pad.get("w", 0.0)), float(pad.get("h", 0.0))),
                        start_layer_id=layer.id, end_layer_id=layer.id)
            if via_outline_conflicts(
                    probe, outlines,
                    rules={"outline_via": rules.get("outline_pad")}):
                return "DRC blocked: outline-to-pad clearance"
        for entry in self._entries:
            entry_layer = self.design_layer_of(entry)
            if entry_layer is None or entry_layer.id != layer.id:
                continue
            rect = self._courtyard_rect(entry)
            corners = [(rect.left(), rect.top()), (rect.right(), rect.top()),
                       (rect.right(), rect.bottom()), (rect.left(), rect.bottom()),
                       (rect.left(), rect.top())]
            boundary = Track(layer_id=layer.id, width=0.0, points=corners)
            if route_outline_conflicts(
                    boundary, outlines,
                    rules={"outline_track": rules.get("outline_courtyard")}):
                return "DRC blocked: outline-to-courtyard clearance"
        return ""

    def delete_selected_footprints(self):
        """Delete all selected footprints as a single undoable operation."""
        selected_items = self.canvas.selected_footprints()
        if not selected_items:
            return False
        entries = [self.entry_for_item(item) for item in selected_items]
        entries = [entry for entry in entries if entry is not None]
        if not entries:
            return False

        states = [self._entry_snapshot(entry, self._entries.index(entry))
                  for entry in entries]
        command = {
            "kind": "delete",
            "entries": states,
            "selection": [entry["uid"] for entry in entries],
            "active": self._active_uid(),
        }
        if not self._delete_uids(command["selection"]):
            return False
        self._push_history(command)
        return True

    def undo(self):
        """Undo the previous move, rotation or delete operation."""
        if self._history_index <= 0:
            return False
        self._history_index -= 1
        command = self._history[self._history_index]
        self._history_replaying = True
        try:
            if command.get("kind") == "transform":
                self._apply_transform_states(command.get("before", []))
                self._apply_copper_transform_states(command.get("copper_before", []))
                if command.get("copper_selection") is not None:
                    self._restore_editor_selection(command.get("selection", []),
                                                   command.get("copper_selection", []),
                                                   command.get("active"))
                else:
                    self._restore_selection_uids(command.get("selection", []),
                                                 command.get("active"))
            elif command.get("kind") == "delete":
                self._restore_deleted_entries(command.get("entries", []))
                self._restore_selection_uids(command.get("selection", []),
                                             command.get("active"))
            elif command.get("kind") == "board_solid":
                self._apply_board_state(command.get("before", {}))
            elif command.get("kind") == "model_adjust":
                self._apply_model_adjust_state(command.get("before", {}))
            elif command.get("kind") == "copper":
                self._apply_copper_snapshot(command.get("before", {}))
            else:
                self._history_index += 1
                return False
        finally:
            self._history_replaying = False
        return True

    def redo(self):
        """Redo the next move, rotation or delete operation."""
        if self._history_index >= len(self._history):
            return False
        command = self._history[self._history_index]
        self._history_replaying = True
        try:
            if command.get("kind") == "transform":
                self._apply_transform_states(command.get("after", []))
                self._apply_copper_transform_states(command.get("copper_after", []))
                if command.get("copper_selection") is not None:
                    self._restore_editor_selection(command.get("selection", []),
                                                   command.get("copper_selection", []),
                                                   command.get("active"))
                else:
                    self._restore_selection_uids(command.get("selection", []),
                                                 command.get("active"))
            elif command.get("kind") == "delete":
                self._delete_uids(command.get("selection", []))
            elif command.get("kind") == "board_solid":
                self._apply_board_state(command.get("after", {}))
            elif command.get("kind") == "model_adjust":
                self._apply_model_adjust_state(command.get("after", {}))
            elif command.get("kind") == "copper":
                self._apply_copper_snapshot(command.get("after", {}))
            else:
                return False
        finally:
            self._history_replaying = False
        self._history_index += 1
        return True

    # -- interactive routing ---------------------------------------------
    def _layer_order(self):
        return [layer.id for layer in self.stack.layers]

    def _net_lookup(self):
        return self.netlist.pad_net_map() if self.netlist is not None else {}

    def _assign_pad_net_names(self, fp, ref):
        """Attach exact netlist names to footprint pad models in-place."""
        lookup = self._net_lookup()
        ref = str(ref or "")
        for pad in getattr(fp, "pads", []):
            pad.net_name = lookup.get((ref, str(pad.number)))
        return fp

    def _assign_saved_connection_nets(self):
        """Propagate pad net names through connected tracks/vias."""
        changed = assign_connection_nets(
            self._pads_by_net(), self.tracks, self.vias, self._layer_order())
        if changed:
            self.canvas.refresh_copper_net_metadata()
        return changed

    def _routing_pads(self, include_hidden=False):
        """Current pad geometry in board coordinates, annotated with nets.

        Rebuilding this list walks every footprint and calls ``mapToScene`` per
        pad.  During one interactive mouse move the clearance predicate runs
        many times, so the result is cached for the duration of an interaction
        and invalidated whenever geometry, nets or visibility change.
        """
        cache = getattr(self, "_routing_cache", None)
        if cache is not None:
            key = "pads_hidden" if include_hidden else "pads_visible"
            hit = cache.get(key)
            if hit is None:
                hit = self._build_routing_pads(include_hidden)
                cache[key] = hit
            return hit
        return self._build_routing_pads(include_hidden)

    def _build_routing_pads(self, include_hidden=False):
        net_lookup = self._net_lookup()
        out = []
        for entry in self._entries:
            item = entry["item"]
            layer = self.stack.by_id(getattr(item, "design_layer_id", None))
            if layer is None or not layer.is_copper:
                continue
            if not include_hidden and (not layer.visible or not item.isVisible()):
                continue
            ref = str(entry.get("ref", ""))
            for pad in entry["fp"].pads:
                # Only pads that actually participate in copper are routable.
                if not any(str(name).endswith(".Cu") or str(name) == "*.Cu"
                           for name in getattr(pad, "layers", ())):
                    continue
                q = item.mapToScene(QtCore.QPointF(float(pad.x), float(pad.y)))
                pin = str(pad.number)
                out.append({
                    "x": float(q.x()), "y": float(q.y()),
                    "w": float(pad.w), "h": float(pad.h),
                    # _add_pad() rotates pad geometry by -pad.rot inside the
                    # footprint item.  Add the parent item rotation to obtain
                    # the exact scene-space orientation used for hit testing.
                    "rot": float(item.rotation()) - float(getattr(pad, "rot", 0.0) or 0.0),
                    "shape": str(getattr(pad, "shape", "rect") or "rect"),
                    "layer_id": layer.id, "ref": ref, "pin": pin,
                    "net": getattr(pad, "net_name", None) or net_lookup.get((ref, pin)),
                    "entry_uid": entry.get("uid"),
                })
        return out

    def _begin_routing_cache(self):
        """Open an interaction-scoped cache for pad/obstacle lookups."""
        self._routing_cache = {}

    def _end_routing_cache(self):
        self._routing_cache = None

    def _invalidate_routing_cache(self):
        """Drop cached routing data after any geometry/net/visibility change."""
        if getattr(self, "_routing_cache", None) is not None:
            self._routing_cache = {}

    def _obstacle_index(self, exclude_uids=()):
        """Broad-phase index of everything the router must avoid.

        ``exclude_uids`` removes the copper currently being dragged so it never
        collides with its own previous position.
        """
        exclude = frozenset(exclude_uids or ())
        cache = getattr(self, "_routing_cache", None)
        if cache is not None:
            hit = cache.get(("index", exclude))
            if hit is not None:
                return hit
        tracks = [t for t in self.tracks if t.uid not in exclude]
        vias = [v for v in self.vias if v.uid not in exclude]
        index = ObstacleIndex(self._routing_pads(include_hidden=True),
                              tracks, vias, self._layer_order())
        if cache is not None:
            cache[("index", exclude)] = index
        return index

    def _pads_by_net(self):
        cache = getattr(self, "_routing_cache", None)
        if cache is not None:
            hit = cache.get("pads_by_net")
            if hit is not None:
                return hit
        result = {}
        for pad in self._routing_pads(include_hidden=True):
            if pad.get("net"):
                result.setdefault(pad["net"], []).append(pad)
        if cache is not None:
            cache["pads_by_net"] = result
        return result

    def _routing_snap_radius(self):
        try:
            px = max(1e-6, float(self.canvas.px_per_mm()))
            return max(0.20, min(3.0, 16.0 / px))
        except Exception:
            return 0.5

    def _sync_routing_ui(self):
        active = self.stack.active()
        copper = active is not None and active.is_copper
        in_2d = getattr(self, "view_mode", "2d") == "2d"
        if hasattr(self, "sec_routing"):
            # Keep the Routing block allocated throughout 2D so the sidebar
            # never grows/shrinks while switching layers; only the enabled
            # state changes.
            self.sec_routing.setVisible(in_2d)
            self.sec_routing.setEnabled(copper)
            self.sec_routing.setToolTip(
                "" if copper else "Select a copper layer to enable routing")
        if hasattr(self, "routing_mode_buttons"):
            for mode, button in self.routing_mode_buttons.items():
                blocked = button.blockSignals(True)
                button.setChecked(mode == self.route_tool)
                button.blockSignals(blocked)
        self.canvas.set_routing_enabled(in_2d and copper and self.route_tool == "route")
        self._sync_via_target_combo()
        self._sync_tool_rail()

    def _fallback_via_target_layer(self, current_layer_id=None):
        """Deterministic target used when Auto has no cross-layer ratsnest hint."""
        current = self.stack.by_id(current_layer_id) if current_layer_id is not None else self.stack.active_copper()
        valid_ids = [layer.id for layer in self.stack.copper_top_down()]
        if not valid_ids:
            return None
        return next((uid for uid in valid_ids
                     if current is None or uid != current.id),
                    current.id if current is not None else valid_ids[0])

    def _resolved_via_target_layer(self, preview=None):
        """Return the physical destination layer for the next via.

        In Auto mode the live route's unresolved ratsnest pads determine the
        target layer.  Manual mode returns the user's selected layer.  When the
        route has no usable net/layer hint, Auto deliberately falls back to the
        same deterministic next-plane choice older releases used.
        """
        r = self.routing
        current_id = ((r or {}).get("layer_id") if r is not None
                      else getattr(self.stack, "active_id", None))
        current = self.stack.by_id(current_id)
        valid_ids = [layer.id for layer in self.stack.copper_top_down()]
        if self.via_target_auto and r is not None and r.get("net"):
            pts = preview if preview is not None else self._preview_points()
            head = pts[-1] if pts else r.get("cursor") or (r.get("points") or [None])[-1]
            target = automatic_via_target_layer(
                head, current_id, r.get("net"), r.get("connected", set()),
                self._pads_by_net(), self._layer_order())
            if target in valid_ids and (current is None or target != current.id):
                return target
        if not self.via_target_auto:
            target = self.via_target_layer_id
            if target in valid_ids and (current is None or target != current.id or len(valid_ids) == 1):
                return target
        return self._fallback_via_target_layer(current_id)

    def _sync_via_target_combo(self):
        combo = getattr(self, "route_via_target", None)
        if combo is None:
            return
        current_layer = self.stack.active_copper()
        valid_ids = [layer.id for layer in self.stack.copper_top_down()]
        target = self.via_target_layer_id
        if target not in valid_ids or (current_layer is not None and target == current_layer.id and len(valid_ids) > 1):
            target = self._fallback_via_target_layer(current_layer.id if current_layer is not None else None)
            self.via_target_layer_id = target
        resolved = self._resolved_via_target_layer()
        resolved_layer = self.stack.by_id(resolved) if resolved is not None else None
        auto_label = "Auto" + ((" → %s" % resolved_layer.name) if resolved_layer is not None else "")
        blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItem(auto_label, "__auto__")
        for layer in self.stack.copper_top_down():
            combo.addItem(layer.name, layer.id)
        if self.via_target_auto:
            combo.setCurrentIndex(0)
        else:
            for i in range(1, combo.count()):
                if combo.itemData(i) == target:
                    combo.setCurrentIndex(i); break
        combo.blockSignals(blocked)

    def _refresh_auto_via_target_label(self):
        """Refresh only the Auto row while routing, avoiding combo rebuild/flicker."""
        combo = getattr(self, "route_via_target", None)
        if combo is None or combo.count() < 1 or combo.itemData(0) != "__auto__":
            return
        resolved = self._resolved_via_target_layer()
        layer = self.stack.by_id(resolved) if resolved is not None else None
        label = "Auto" + ((" → %s" % layer.name) if layer is not None else "")
        if combo.itemText(0) != label:
            combo.setItemText(0, label)
        if self.via_pending and self.via_target_auto:
            self.status_hover.setText(
                "Via armed — click to place on %s (Auto); V or Esc cancels" %
                (layer.name if layer is not None else "target layer"))

    def _on_route_track_width(self, value):
        self.track_width = max(0.01, float(value))
        if self.routing is not None:
            self.routing["width"] = self.track_width
            self._update_route_preview()

    def _on_route_via_diameter(self, value):
        self.via_diameter = max(0.01, float(value))
        if self.via_hole_diameter > self.via_diameter:
            self.via_hole_diameter = self.via_diameter
            self.route_via_hole.blockSignals(True)
            self.route_via_hole.setValue(self.via_hole_diameter)
            self.route_via_hole.blockSignals(False)
        self._update_route_preview()

    def _on_route_via_hole(self, value):
        self.via_hole_diameter = max(0.0, min(float(value), self.via_diameter))
        if abs(float(value) - self.via_hole_diameter) > 1e-9:
            self.route_via_hole.blockSignals(True)
            self.route_via_hole.setValue(self.via_hole_diameter)
            self.route_via_hole.blockSignals(False)
        self._update_route_preview()

    def _on_via_target_changed(self, _index):
        combo = getattr(self, "route_via_target", None)
        if combo is None:
            return
        uid = combo.currentData()
        if uid == "__auto__":
            self.via_target_auto = True
            self._sync_via_target_combo()
            self._update_route_preview()
            return
        layer = self.stack.by_id(uid) if uid is not None else None
        if layer is not None and layer.is_copper:
            self.via_target_auto = False
            self.via_target_layer_id = uid
            self._update_route_preview()

    def _set_route_tool(self, mode):
        mode = "route" if str(mode) == "route" else "select"
        active = self.stack.active()
        if mode == "route" and (active is None or not active.is_copper):
            self.status_hover.setText("Routing is available only on a copper design layer")
            mode = "select"
        if self.route_tool == mode:
            self._sync_routing_ui()
            return
        if mode != "route" and self.routing is not None:
            self._finish_route()
        self.route_tool = mode
        self.via_pending = False
        self.canvas.clear_route_preview()
        self._sync_routing_ui()
        self.status_hover.setText(
            "Click a pad, via or track to start · V via · / posture · Shift no object snap · Ctrl no grid snap"
            if mode == "route" else "")

    def _toggle_route_tool(self):
        self._set_route_tool("select" if self.route_tool == "route" else "route")

    def _route_escape(self):
        if self.via_pending:
            self.via_pending = False
            self._update_route_preview()
            self.status_hover.setText("Via cancelled; routing continues")
            return
        if self.routing is not None:
            self._finish_route()
            return
        self._set_route_tool("select")

    def _route_enter(self):
        if self.routing is None:
            return
        preview, _conflicts, _blocked = self._legal_route_preview()
        self._commit_preview_to_route(preview)
        self._finish_route()

    def _route_backspace(self):
        r = self.routing
        if r is None:
            return
        if len(r["points"]) > 1:
            r["points"].pop()
            r["cursor"] = r["points"][-1]
            self._update_route_preview()
        else:
            self.routing = None
            self.via_pending = False
            self.canvas.clear_route_preview()
            self._refresh_ratsnest()

    def _toggle_route_posture(self):
        if self.route_tool != "route":
            return
        self.route_posture = 0 if self.route_posture else 1
        self._update_route_preview()

    def _toggle_via_pending(self):
        if self.route_tool != "route" or self.routing is None:
            return
        if len(self.stack.layers) < 1:
            return
        self.via_pending = not self.via_pending
        self._sync_via_target_combo()
        self._update_route_preview()
        target = self._resolved_via_target_layer()
        target_layer = self.stack.by_id(target) if target is not None else None
        self.status_hover.setText(
            "Via armed — click to place on %s%s; V or Esc cancels" %
            (target_layer.name if target_layer is not None else "target layer",
             " (Auto)" if self.via_target_auto else "")
            if self.via_pending else "Via cancelled")

    def _snap_index(self):
        """Broad-phase index for magnetic snapping.

        Deliberately separate from :meth:`_obstacle_index`: DRC must consider
        pads on hidden layers, but the cursor may only snap to copper the user
        can actually see.
        """
        cache = getattr(self, "_routing_cache", None)
        if cache is not None:
            hit = cache.get("snap_index")
            if hit is not None:
                return hit
        index = ObstacleIndex(self._routing_pads(include_hidden=False),
                              self.tracks, self.vias, self._layer_order())
        if cache is not None:
            cache["snap_index"] = index
        return index

    def _snap_for_route(self, point, modifiers):
        active = self.stack.active()
        if active is None or not active.is_copper:
            return {"x": float(point.x()), "y": float(point.y()), "kind": "free", "net": None}
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        return route_snap(
            (float(point.x()), float(point.y())), active.id,
            None, None, None, self._layer_order(),
            self._routing_snap_radius(), grid=float(self.canvas.grid_step),
            object_snap=not shift, grid_snap=(not ctrl and bool(self.canvas.snap_enabled)),
            active_net=(self.routing or {}).get("net"),
            index=self._snap_index())

    @staticmethod
    def _append_unique_points(dst, pts):
        for p in pts:
            x, y = float(p[0]), float(p[1])
            if not dst or abs(dst[-1][0] - x) > 1e-9 or abs(dst[-1][1] - y) > 1e-9:
                dst.append((x, y))

    def _preview_points(self):
        r = self.routing
        if r is None:
            return []
        pts = list(r["points"])
        cursor = r.get("cursor")
        if cursor is not None and pts:
            if r.get("free"):
                self._append_unique_points(pts, [cursor])
            else:
                self._append_unique_points(pts, route45(pts[-1], cursor, self.route_posture))
        return pts

    def _current_route_airwires(self, preview):
        r = self.routing
        if r is None or not r.get("net") or not preview:
            return []
        pads = list(self._pads_by_net().get(r["net"], []))
        connected = set(r.get("connected", set()))
        temp = Track(layer_id=r["layer_id"], width=r["width"], points=preview, net=r["net"])
        targets = []
        for pad in pads:
            key = pad_key(pad.get("ref"), pad.get("pin"))
            if key in connected or track_touches_pad(temp, pad, eps=0.03):
                continue
            targets.append(pad)
        if not targets:
            return []
        head = preview[-1]
        # The live route head is one electrical island.  Draw an MST seeded by
        # it rather than a star to every pad: as connections are reached the
        # incomplete ratsnest changes exactly with the remaining topology.
        nodes = [{"x": head[0], "y": head[1]}] + targets
        used = {0}; rest = set(range(1, len(nodes))); edges = []
        while rest:
            best = None
            for i in used:
                for j in rest:
                    d = (nodes[i]["x"]-nodes[j]["x"])**2 + (nodes[i]["y"]-nodes[j]["y"])**2
                    if best is None or d < best[0]:
                        best = (d, i, j)
            if best is None:
                break
            _d, i, j = best
            edges.append(((nodes[i]["x"], nodes[i]["y"]), (nodes[j]["x"], nodes[j]["y"])))
            used.add(j); rest.remove(j)
        return edges

    def _board_outline_for_layer(self, layer_id):
        """Prepared routing boundary of one copper layer, or ``None``.

        The boundary is the board solid's cross-section at the layer's
        reference-plane height.  A layer whose plane misses the solid gets a
        *blocking* boundary: nothing may be placed or routed there.  Without a
        board solid routing is unconstrained, as before any outline existed.
        """
        solid = self.board_solid
        if solid is None:
            return None
        z = self.stack.reference_heights().get(layer_id)
        if z is None:
            return None
        key = (solid.signature(), round(float(z), 9))
        hit = self._board_outline_cache.get(key)
        if hit is None:
            hit = BoardOutline.from_loops(solid.board_section(z), blocking=True)
            if len(self._board_outline_cache) > 32:
                self._board_outline_cache = {}
            self._board_outline_cache[key] = hit
        return hit

    # Historical name; every DRC/routing caller asks for "the outline models".
    _outline_models_for_layer = _board_outline_for_layer

    def _route_collision_conflicts(self, preview=None, via=None):
        """Return physical obstacles touched by the live route.

        The board-solid section is always a routing boundary, even for a route
        started from free space before a net is known.  Different-net copper is
        additionally checked once the active route has an identifiable net.

        The obstacle set is queried through a shared broad-phase index because
        one interactive mouse move evaluates this predicate many times through
        the clip search.
        """
        r = self.routing
        if r is None:
            return []
        pts = list(preview if preview is not None else self._preview_points())
        order = self._layer_order()
        index = self._obstacle_index()
        outlines = self._outline_models_for_layer(r.get("layer_id"))
        conflicts = []
        if pts:
            candidate = Track(layer_id=r["layer_id"], width=r["width"],
                              points=pts, net=r.get("net"))
            conflicts.extend(route_outline_conflicts(candidate, outlines, rules=self.design_rules))
            if len(pts) >= 2 and r.get("net"):
                conflicts.extend(route_conflicts(
                    candidate, None, None, None, order,
                    active_net=r.get("net"), rules=self.design_rules, index=index))
        if via is not None:
            # The via must satisfy the outline clearance on every copper layer
            # it spans, with each layer treated as an independent cross-section.
            outline_sets = [self._outline_models_for_layer(layer_id)
                            for layer_id in order
                            if via_spans_layer(via, layer_id, order)]
            conflicts.extend(via_layer_outline_conflicts(
                via, outline_sets, rules=self.design_rules))
            if r.get("net"):
                conflicts.extend(via_conflicts(
                    via, None, None, None, order,
                    active_net=r.get("net"), rules=self.design_rules, index=index))
        return conflicts

    @staticmethod
    def _route_collision_message(conflicts):
        if not conflicts:
            return ""
        if any(str(hit.get("kind") or "") == "outline" for hit in conflicts):
            return "Routing blocked by the board edge"
        nets = []
        kinds = []
        for hit in conflicts:
            name = str(hit.get("net") or "")
            kind = str(hit.get("kind") or "copper")
            if name and name not in nets:
                nets.append(name)
            if kind not in kinds:
                kinds.append(kind)
        net_text = ", ".join(nets[:3]) or "another net"
        if len(nets) > 3:
            net_text += ", …"
        kind_text = "/".join(kinds) if kinds else "copper"
        return "Clearance blocked by %s (%s)" % (net_text, kind_text)

    def _legal_route_preview(self):
        """Return the visible route, routed around or clipped before obstacles.

        When walk-around is enabled the router first tries to reach the pointer
        by going around whatever blocks the direct path, which is how KiCad
        behaves by default.  If no legal detour exists the previous hard-barrier
        behaviour applies: the pointer keeps moving but the copper head stops at
        its last legal position, rather than showing invalid geometry.
        """
        r = self.routing
        raw = self._preview_points()
        if r is None or len(raw) < 2:
            return raw, [], False
        fixed_count = max(1, min(len(r.get("points", [])), len(raw)))
        raw_conflicts = self._route_collision_conflicts(raw)
        if not raw_conflicts:
            return raw, [], False

        committed = list(raw[:fixed_count])
        conflict_fn = lambda pts: bool(self._route_collision_conflicts(pts))

        if getattr(self, "route_walkaround", True) and committed:
            detour = self._walkaround_preview(committed, raw[-1], raw_conflicts,
                                              conflict_fn)
            if detour is not None:
                return detour, [], False

        clipped, blocked = clip_polyline_before_conflict(
            raw, conflict_fn, fixed_count=fixed_count)
        return clipped, raw_conflicts, bool(blocked)

    def _walkaround_margin(self):
        """How far outside an obstacle a detour waypoint must sit.

        An obstacle's bounding box already includes its own copper envelope, so
        the extra margin is the moving track's half width plus the widest
        clearance rule that could apply to it.  Under-estimating this puts every
        candidate waypoint inside the clearance zone and the search then finds
        no legal detour at all.
        """
        rules = self.design_rules
        keys = ("track_track", "track_via", "track_pad")
        try:
            worst = max(float(rules.get(key)) for key in keys)
        except Exception:
            worst = 0.2
        return float(self.track_width) / 2.0 + worst + 0.05

    def _walkaround_preview(self, committed, target, conflicts, conflict_fn):
        """Route the moving tail around blocking copper, or ``None`` if stuck.

        The committed prefix is held fixed, so the detour search only ever
        rewrites the segment the user is currently steering.
        """
        boxes = conflict_boxes(conflicts, margin=self._walkaround_margin())
        if not boxes:
            return None
        tail = walkaround_route(
            committed[-1], target,
            lambda pts: conflict_fn(committed[:-1] + list(pts)),
            obstacle_boxes=boxes, posture=self.route_posture)
        if not tail:
            return None
        return committed + list(tail)

    def _update_route_preview(self, snap=None):
        if self.route_tool != "route":
            self.canvas.clear_route_preview(); return []
        r = self.routing
        preview, track_conflicts, blocked = self._legal_route_preview()
        if snap is None and r is not None:
            snap = r.get("snap")
        via = None
        via_conflict = []
        if self.via_pending and r is not None and preview:
            end_layer = self._resolved_via_target_layer(preview)
            via = Via(x=preview[-1][0], y=preview[-1][1],
                      diameter=self.via_diameter, hole_diameter=self.via_hole_diameter,
                      start_layer_id=r["layer_id"], end_layer_id=end_layer, net=r.get("net"))
            via_conflict = self._route_collision_conflicts(preview, via)
            # The route itself has already been clipped to legal copper.  Any
            # remaining conflict here is therefore caused by the via span.
            if via_conflict:
                via = None
                blocked = True
        conflicts = track_conflicts or via_conflict
        active = self.stack.by_id((r or {}).get("layer_id", self.stack.active_id))
        color = active.color if active is not None else "#4D7FC4"
        # A blocked route is displayed at its last legal point in the normal
        # layer colour.  Do not use a red invalid-copper preview.
        self.canvas.set_route_preview(
            preview, (r or {}).get("width", self.track_width), color,
            snap=None if blocked else snap, via=via,
            airwires=self._current_route_airwires(preview))
        if conflicts:
            self.status_hover.setText(self._route_collision_message(conflicts))
        elif self.via_pending and r is not None:
            target = self._resolved_via_target_layer(preview)
            layer = self.stack.by_id(target) if target is not None else None
            self.status_hover.setText(
                "Via armed — click to place on %s%s; V or Esc cancels" %
                (layer.name if layer is not None else "target layer",
                 " (Auto)" if self.via_target_auto else ""))
        elif r is not None and r.get("net"):
            self.status_hover.setText("Routing %s" % r.get("net"))
        return conflicts

    def _on_route_pointer(self, point, modifiers):
        if self.route_tool != "route" or self.view_mode != "2d":
            return
        # One pointer event runs the clearance predicate many times through the
        # clip search; scoping a cache to the event lets them share one pad
        # list and one broad-phase index.
        self._begin_routing_cache()
        try:
            snap = self._snap_for_route(point, modifiers)
            self.canvas._cursor_scene = QtCore.QPointF(float(snap["x"]), float(snap["y"]))
            self.status_pos.setText("x %.3f   y %.3f mm" % (float(snap["x"]), float(snap["y"])))
            if self.routing is not None:
                self.routing["cursor"] = (float(snap["x"]), float(snap["y"]))
                self.routing["free"] = bool(modifiers & Qt.ControlModifier)
                self.routing["snap"] = snap
                if self.via_target_auto:
                    self._refresh_auto_via_target_label()
            self._update_route_preview(snap)
        finally:
            self._end_routing_cache()

    def _start_route(self, snap):
        active = self.stack.active()
        if active is None or not active.is_copper:
            return False
        ctx = routing_context(snap, active.id,
                              self.netlist.nets if self.netlist is not None else [],
                              self._pads_by_net(), self.tracks, self.vias, self._layer_order())
        p = (float(snap["x"]), float(snap["y"]))
        self.routing = {
            "layer_id": active.id, "width": self.track_width,
            "points": [p], "cursor": p, "free": False,
            "net": ctx.get("net"), "connected": set(ctx.get("connected", set())),
            "snap": snap,
            # A repaired gap should become one Track again when routing starts
            # on an existing endpoint and finishes on another compatible endpoint.
            # Store only the uid so active-route snapshots remain plain data.
            "start_track_uid": self._route_snap_endpoint_track_uid(snap),
        }
        self._route_history_before = self._copper_snapshot()
        self._refresh_ratsnest()
        if self.routing["net"]:
            self.status_hover.setText("Routing %s" % self.routing["net"])
        return True

    @staticmethod
    def _route_snap_endpoint_track_uid(snap, tol=1e-6):
        """Track uid when *snap* is exactly on one of that Track's endpoints."""
        if not isinstance(snap, dict) or snap.get("kind") != "track":
            return None
        track = snap.get("track")
        if track is None or not getattr(track, "points", None):
            return None
        p = (float(snap.get("x", 0.0)), float(snap.get("y", 0.0)))
        for q in (track.points[0], track.points[-1]):
            if math.hypot(float(q[0]) - p[0], float(q[1]) - p[1]) <= float(tol):
                return track.uid
        return None

    def _append_finished_track(self, track, start_track_uid=None, end_track_uid=None):
        """Append a routed Track, welding compatible endpoint continuations.

        Segment deletion intentionally splits a polyline.  Re-routing the missing
        piece should undo that structural split as well as restore electrical
        contact.  Only explicit endpoint snaps are welded; T-junctions and width
        changes stay as separate Track objects.
        """
        current = track
        seen = set()
        for uid in (start_track_uid, end_track_uid):
            if not uid or uid in seen:
                continue
            seen.add(uid)
            existing = next((t for t in self.tracks if t.uid == uid), None)
            if existing is None or existing is current:
                continue
            # Preserve the existing uid for the first weld; once current already
            # carries an existing uid, preserve that uid through the second weld.
            merged = (join_track_endpoints(existing, current)
                      if current.uid == track.uid
                      else join_track_endpoints(current, existing))
            if merged is None:
                continue
            self.tracks = [t for t in self.tracks if t.uid != existing.uid]
            current = merged
        self.tracks.append(current)
        return current

    def _snap_completes_route(self, snap):
        r = self.routing
        if r is None or snap.get("kind") not in ("pad", "via", "track"):
            return False
        if not r.get("net"):
            return True
        ctx = routing_context(snap, r["layer_id"],
                              self.netlist.nets if self.netlist is not None else [],
                              self._pads_by_net(), self.tracks, self.vias, self._layer_order())
        return ctx.get("net") == r.get("net")

    def _commit_preview_to_route(self, preview=None):
        r = self.routing
        if r is None:
            return
        if preview is not None:
            pts = list(preview)
            base = len(r["points"])
            if len(pts) > base:
                self._append_unique_points(r["points"], pts[base:])
            return
        cursor = r.get("cursor")
        if cursor is None:
            return
        if r.get("free"):
            self._append_unique_points(r["points"], [cursor])
        else:
            self._append_unique_points(r["points"], route45(r["points"][-1], cursor, self.route_posture))

    def _on_route_click(self, point, modifiers):
        if self.route_tool != "route" or self.view_mode != "2d":
            return
        snap = self._snap_for_route(point, modifiers)
        if self.routing is None:
            self._start_route(snap)
            self._update_route_preview(snap)
            return
        self.routing["cursor"] = (float(snap["x"]), float(snap["y"]))
        self.routing["free"] = bool(modifiers & Qt.ControlModifier)
        self.routing["snap"] = snap
        if self.via_pending:
            self._place_via_and_continue()
            return
        preview, conflicts, blocked = self._legal_route_preview()
        self._commit_preview_to_route(preview)
        if (not blocked and len(self.routing["points"]) >= 2
                and self._snap_completes_route(snap)):
            self._finish_route()
        else:
            if conflicts:
                self.status_hover.setText(self._route_collision_message(conflicts))
            self._update_route_preview(snap)

    def _on_route_double_click(self, point, modifiers):
        if self.route_tool != "route":
            return
        if self.routing is not None:
            snap = self._snap_for_route(point, modifiers)
            self.routing["cursor"] = (float(snap["x"]), float(snap["y"]))
            self.routing["free"] = bool(modifiers & Qt.ControlModifier)
            self.routing["snap"] = snap
            preview, _conflicts, _blocked = self._legal_route_preview()
            self._commit_preview_to_route(preview)
            self._finish_route()

    def _copper_snapshot(self):
        return {"tracks": [track.snapshot() for track in self.tracks],
                "vias": [via.snapshot() for via in self.vias]}

    def _apply_copper_snapshot(self, snapshot):
        snapshot = snapshot or {}
        self.tracks = [Track.restore(data) for data in snapshot.get("tracks", [])]
        self.vias = [Via.restore(data) for data in snapshot.get("vias", [])]
        self.routing = None; self.via_pending = False; self._route_history_before = None
        self._apply_routed_copper()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()

    def routing_snapshot(self):
        active = None
        if self.routing is not None:
            active = {key: value for key, value in self.routing.items()
                      if key not in ("snap", "connected")}
            active["points"] = [list(p) for p in self.routing.get("points", [])]
            if self.routing.get("cursor") is not None:
                active["cursor"] = list(self.routing["cursor"])
            active["connected"] = list(self.routing.get("connected", set()))
        return {
            "copper": self._copper_snapshot(),
            "track_width": self.track_width,
            "via_diameter": self.via_diameter,
            "via_hole_diameter": self.via_hole_diameter,
            "via_target_layer_id": self.via_target_layer_id,
            "via_target_auto": bool(self.via_target_auto),
            "route_posture": self.route_posture,
            "route_tool": self.route_tool,
            "active_route": active,
            "via_pending": bool(self.via_pending),
            "route_history_before": self._route_history_before,
        }

    def restore_routing(self, snapshot):
        snapshot = snapshot or {}
        copper = snapshot.get("copper", snapshot)
        self.tracks = [Track.restore(data) for data in copper.get("tracks", [])]
        self.vias = [Via.restore(data) for data in copper.get("vias", [])]
        self.track_width = float(snapshot.get("track_width", 0.25))
        self.via_diameter = float(snapshot.get("via_diameter", 0.8))
        self.via_hole_diameter = max(0.0, min(float(snapshot.get("via_hole_diameter", 0.0)), self.via_diameter))
        self.via_target_layer_id = snapshot.get("via_target_layer_id")
        self.via_target_auto = bool(snapshot.get(
            "via_target_auto", snapshot.get("via_target_layer_id") is None))
        self.route_posture = int(snapshot.get("route_posture", 0)) & 1
        self.route_tool = "route" if snapshot.get("route_tool") == "route" else "select"
        active = snapshot.get("active_route")
        if active:
            self.routing = dict(active)
            self.routing["points"] = [tuple(p) for p in active.get("points", [])]
            if active.get("cursor") is not None:
                self.routing["cursor"] = tuple(active.get("cursor"))
            self.routing["connected"] = set(active.get("connected", []))
            self.routing["snap"] = None
        else:
            self.routing = None
        self.via_pending = bool(snapshot.get("via_pending", False) and self.routing is not None)
        self._route_history_before = snapshot.get("route_history_before")
        for widget, value in ((self.route_track_width, self.track_width),
                              (self.route_via_dia, self.via_diameter),
                              (self.route_via_hole, self.via_hole_diameter)):
            widget.blockSignals(True); widget.setValue(value); widget.blockSignals(False)
        self._apply_routed_copper()
        self._sync_routing_ui()
        if self.routing is not None:
            self._update_route_preview()

    def _push_copper_history(self, before, label):
        after = self._copper_snapshot()
        if before == after:
            return
        self._push_history({"kind": "copper", "before": before, "after": after,
                            "label": str(label)})

    def _apply_routed_copper(self):
        # The track/via population itself changed, so a cached obstacle index
        # built from the previous population must not be reused.
        self._invalidate_routing_cache()
        self.canvas.set_routed_copper(self.tracks, self.vias, self.stack)
        self._selected_copper = self.canvas.selected_copper_items()

    def _finish_route(self):
        r = self.routing
        if r is None:
            return False
        points = list(r.get("points", []))
        if len(points) >= 2:
            conflicts = self._route_collision_conflicts(points)
            if conflicts:
                self.status_hover.setText(self._route_collision_message(conflicts))
                self._update_route_preview()
                return False
        before = self._route_history_before or self._copper_snapshot()
        self.routing = None; self.via_pending = False; self._route_history_before = None
        if len(points) >= 2:
            routed = Track(layer_id=r["layer_id"], width=r["width"],
                           points=points, net=r.get("net"))
            self._append_finished_track(
                routed, r.get("start_track_uid"),
                self._route_snap_endpoint_track_uid(r.get("snap")))
            self._push_copper_history(before, "Route track")
            self._apply_routed_copper()
            self._refresh_3d_if_visible(force=True, auto_align=False)
        self.canvas.clear_route_preview()
        self._refresh_ratsnest()
        return len(points) >= 2

    def _place_via_and_continue(self):
        r = self.routing
        if r is None:
            return False
        preview, track_conflicts, _blocked = self._legal_route_preview()
        if not preview:
            return False
        target = self._resolved_via_target_layer(preview)
        if target is None:
            return False
        at = preview[-1]
        candidate_via = Via(x=at[0], y=at[1], diameter=self.via_diameter,
                            hole_diameter=self.via_hole_diameter,
                            start_layer_id=r["layer_id"], end_layer_id=target,
                            net=r.get("net"))
        conflicts = self._route_collision_conflicts(preview, candidate_via)
        if conflicts:
            self.status_hover.setText(self._route_collision_message(conflicts))
            self._update_route_preview(r.get("snap"))
            return False

        # If the track was blocked, the visible preview already stops at its
        # last legal point.  Place the via there, never at the pointer beyond
        # the obstacle.
        self._commit_preview_to_route(preview)
        at = r["points"][-1]
        before = self._route_history_before or self._copper_snapshot()
        if len(r["points"]) >= 2:
            routed = Track(layer_id=r["layer_id"], width=r["width"],
                           points=list(r["points"]), net=r.get("net"))
            self._append_finished_track(routed, r.get("start_track_uid"), None)
        via = Via(x=at[0], y=at[1], diameter=self.via_diameter,
                  hole_diameter=self.via_hole_diameter,
                  start_layer_id=r["layer_id"], end_layer_id=target, net=r.get("net"))
        self.vias.append(via)
        self.via_pending = False
        self._push_copper_history(before, "Route via")
        self._apply_routed_copper()
        self._refresh_3d_if_visible(force=True, auto_align=False)

        # Continue from the same physical via on its explicitly selected target
        # plane.  This supports blind/buried-style spans: start/end can be any
        # two copper reference layers, not only adjacent planes.
        self.stack.set_active(target)
        self._apply_design_layers()
        snap = {"x": via.x, "y": via.y, "kind": "via", "via": via, "net": via.net}
        ctx = routing_context(snap, target,
                              self.netlist.nets if self.netlist is not None else [],
                              self._pads_by_net(), self.tracks, self.vias, self._layer_order())
        self.routing = {"layer_id": target, "width": self.track_width,
                        "points": [at], "cursor": at, "free": False,
                        "net": ctx.get("net") or r.get("net"),
                        "connected": set(ctx.get("connected", set())), "snap": snap}
        self._route_history_before = self._copper_snapshot()
        self._sync_via_target_combo()
        self._refresh_ratsnest(); self._update_route_preview(snap)
        return True

    def _on_copper_selection(self, items):
        self._selected_copper = list(items or [])
        if self._selected_copper:
            # Copper selection is mutually exclusive with footprint/pad/outline
            # selection so Delete/Properties always have one clear target set.
            self.canvas.select_pad(None, None)
        self._refresh_properties()

    def _delete_selected_copper(self):
        """Delete the selected copper at the same granularity shown in 2D.

        A normally clicked TrackGraphicsItem represents one logical straight
        segment, so Delete removes just that segment.  If the segment is in the
        middle of a polyline the surviving copper becomes two Track objects.
        ``U`` marks track items as ``select_whole_track``; those (and selection
        mechanisms without a segment index) retain whole-track deletion.  Vias
        are always atomic objects.
        """
        items = list(self.canvas.selected_copper_items())
        if not items:
            return False

        full_track_uids = set()
        segment_delete = {}
        via_uids = set()
        for item in items:
            track = getattr(item, "track_model", None)
            via = getattr(item, "via_model", None)
            if track is not None:
                seg = getattr(item, "selected_segment_index", None)
                whole = bool(getattr(item, "select_whole_track", False))
                if whole or seg is None:
                    full_track_uids.add(track.uid)
                else:
                    try:
                        segment_delete[track.uid] = int(seg)
                    except (TypeError, ValueError):
                        full_track_uids.add(track.uid)
            elif via is not None:
                via_uids.add(via.uid)

        if not (full_track_uids or segment_delete or via_uids):
            return False

        before = self._copper_snapshot()
        tracks_after = []
        deleted_segments = 0
        for track in self.tracks:
            if track.uid in full_track_uids:
                continue
            if track.uid in segment_delete:
                parts = delete_track_segment(track, segment_delete[track.uid])
                tracks_after.extend(parts)
                deleted_segments += 1
            else:
                tracks_after.append(track)
        self.tracks = tracks_after
        self.vias = [v for v in self.vias if v.uid not in via_uids]

        if deleted_segments and not full_track_uids and not via_uids:
            label = "Delete track segment" if deleted_segments == 1 else "Delete track segments"
        else:
            label = "Delete copper"
        self._push_copper_history(before, label)
        self._apply_routed_copper(); self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()
        return True


    # -- design layers ---------------------------------------------------
    def entry_for_item(self, item):
        for entry in self._entries:
            if entry["item"] is item:
                return entry
        return None

    def design_layer_of(self, entry):
        """The stack layer an entry sits on, resolved through stale ids."""
        return self.stack.by_id(entry["item"].design_layer_id)

    def _apply_design_layers(self, visibility_changed=False, refresh_properties=True):
        """Push design-layer presentation onto existing scene/UI objects.

        Layer activation is presentation-only: no footprint, track, via or
        sidebar row needs to be recreated.  Keeping those objects alive avoids
        the repaint/relayout flash that used to occur on every layer switch.  ``visibility_changed`` is reserved for the one case where
        visible extents may genuinely change.
        """
        # Layer visibility decides which pads are routable.
        self._invalidate_routing_cache()
        active_id = self.stack.active_id
        active_layer = self.stack.active()
        active_copper_id = active_id if active_layer is not None and active_layer.is_copper else None

        # Suppress intermediate selection signals and refresh Properties once
        # after the whole layer transition is complete.
        old_blocked = self.canvas.blockSignals(True)
        try:
            for entry in self._entries:
                item = entry["item"]
                layer = self.stack.by_id(item.design_layer_id)
                if layer is None:
                    continue
                item.design_layer_id = layer.id      # repair references to a removed layer
                item.set_copper_tint(layer.color)
                item.setOpacity(1.0 if layer.id == active_copper_id else INACTIVE_OPACITY)
                if item.isVisible() != layer.visible:
                    item.setVisible(layer.visible)
                    if not layer.visible and item.isSelected():
                        item.setSelected(False)

            # The active layer's board-solid section is drawn solid with the
            # outside shaded; other visible layers are faint references.
            self._apply_board_sections()

            # Tracks/vias already have QGraphicsItems.  A layer switch changes
            # only their colour/opacity/visibility, so update those items in
            # place instead of clear_routed_copper() + reconstruction.
            self.canvas.sync_routed_copper_style(
                self.stack, update_extent=bool(visibility_changed))
        finally:
            self.canvas.blockSignals(old_blocked)

        self._selected_copper = self.canvas.selected_copper_items()
        self._sync_routing_ui()
        self._sync_board_section()
        self._sync_stack_rows()
        if visibility_changed:
            self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)
        if refresh_properties:
            self._refresh_properties()

    def _stack_counts(self):
        counts = {}
        for entry in self._entries:
            layer = self.design_layer_of(entry)
            if layer is not None:
                counts[layer.id] = counts.get(layer.id, 0) + 1
        return counts

    def _sync_stack_rows(self):
        """Refresh existing Design Layers rows without replacing widgets."""
        expected = [layer.id for layer in self.stack.top_down()]
        if list(self._stack_rows.keys()) != expected:
            return False
        counts = self._stack_counts()
        for layer in self.stack.top_down():
            row = self._stack_rows.get(layer.id)
            if row is None:
                return False
            removable = bool(layer.is_copper and not layer.locked and len(self.stack) > 1)
            row.sync(layer, counts.get(layer.id, 0),
                     active=(layer.id == self.stack.active_id),
                     removable=removable)
        self._sync_layer_bar()
        return True

    def _rebuild_stack(self):
        """Rebuild Design Layers only when its topology really changed."""
        while self.stack_layout.count():
            item = self.stack_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._stack_rows = {}

        counts = self._stack_counts()
        for layer in self.stack.top_down():          # top copper first
            removable = bool(layer.is_copper and not layer.locked and len(self.stack) > 1)
            row = StackRow(layer, counts.get(layer.id, 0),
                           active=(layer.id == self.stack.active_id),
                           removable=removable)
            row.activated.connect(self._on_stack_activate)
            row.visibilityChanged.connect(self._on_stack_visible)
            row.removeRequested.connect(self._on_stack_remove)
            self.stack_layout.addWidget(row)
            self._stack_rows[layer.id] = row
        self._rebuild_layer_bar()

    def _update_stackup_summary(self):
        count = len(self.stack)
        noun = "layer" if count == 1 else "layers"
        text = "%d copper %s  ·  top plane %.3f mm" % (
            count, noun, self.stack.top_reference_height())
        if self.board_solid is not None:
            text += "  ·  solid %.3f mm" % self.board_solid.height
        self.stackup_summary.setText(text)

    def _rebuild_stackup(self):
        """Rebuild the physical stack sequence after copper topology changes.

        Normal value edits only update the model and summary in place.  The
        widgets are rebuilt only when a copper plane is added/removed (or when
        a host-mode restore loads a different stack), so editing a thickness
        does not make the sidebar flicker.
        """
        while self.stackup_layout.count():
            item = self.stackup_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._stackup_rows = []

        def add_row(row):
            self._stackup_rows.append(row)
            row.spin.valueChanged.connect(
                lambda value, current=row: self._on_stackup_value(current, value))
            self.stackup_layout.addWidget(row)
            return row

        copper = self.stack.copper_top_down()
        for index, layer in enumerate(copper):
            row = add_row(StackupValueRow(
                "copper", layer.name,
                self.stack.copper_thickness(layer.id), color=layer.color))
            row.layer_id = layer.id
            row.spin.setMinimum(0.001)
            row.spin.setSingleStep(0.005)
            row.spin.setToolTip("Copper etched below the %s reference plane; does not change separation" % layer.name)

            if index + 1 < len(copper):
                upper = layer
                lower = copper[index + 1]
                gap = add_row(StackupValueRow(
                    "separation", "separation",
                    self.stack.separation(lower.id, upper.id)))
                gap.lower_id = lower.id
                gap.upper_id = upper.id
                gap.spin.setSingleStep(0.1)
                gap.spin.setToolTip(
                    "Reference-plane distance between %s and %s" % (upper.name, lower.name))

        bottom = add_row(StackupValueRow(
            "bottom_margin", "bottom margin", self.stack.bottom_margin))
        bottom.spin.setSingleStep(0.1)
        bottom.spin.setToolTip("Height of the bottom copper reference plane above the "
                               "board solid's lowest point")

        self._update_stackup_summary()

    def _on_stackup_value(self, row, value):
        """Commit one live Stackup editor value without rebuilding the section."""
        if row.kind == "bottom_margin":
            self.stack.set_bottom_margin(value)
        elif row.kind == "copper":
            self.stack.set_copper_thickness(row.layer_id, value)
        elif row.kind == "separation":
            self.stack.set_separation(row.lower_id, row.upper_id, value)
        self._update_stackup_summary()
        self._board_geometry_changed(immediate=False)

    def _cycle_active_layer(self, direction):
        """Select the adjacent copper reference plane from the bottom layer bar.

        ``direction > 0`` walks toward the physical top/right (B.Cu -> F.Cu);
        ``direction < 0`` walks toward the physical bottom/left.
        """
        if self.routing is not None:
            self._flash_status("Finish the route or press V before changing layers")
            return False
        layers = self.stack.copper_layers()
        if not layers:
            return False
        index = self.stack.index(self.stack.active_id)
        if index < 0:
            index = 0
        target = max(0, min(len(layers) - 1, index + (1 if direction > 0 else -1)))
        if target == index:
            return False
        self._on_stack_activate(layers[target].id)
        self._flash_status("Active layer: %s" % layers[target].name)
        return True

    def _select_extreme_layer(self, top=True):
        """Page Up selects top copper; Page Down selects bottom copper."""
        if self.routing is not None:
            self._flash_status("Finish the route or press V before changing layers")
            return False
        layer = self.stack.top() if top else self.stack.bottom()
        if layer is None:
            return False
        if layer.id != self.stack.active_id:
            self._on_stack_activate(layer.id)
        self._flash_status("Active layer: %s" % layer.name)
        return True

    def _on_stack_add(self):
        self.stack.add()
        self._sync_via_target_combo()
        self._apply_design_layers()
        self._rebuild_stack()
        self._rebuild_stackup()
        self._refresh_properties()

    def _on_stack_activate(self, uid):
        if uid != self.stack.active_id and self.routing is not None:
            self._finish_route()
        if not self.stack.set_active(uid):
            return
        self._sync_via_target_combo()
        # Activation changes only active styling/tool context.  Do not rebuild
        # Design Layers, routed copper or the right-hand tool strip.
        self._apply_design_layers(visibility_changed=False)

    def _on_stack_visible(self, uid, visible):
        layer = self.stack.by_id(uid)
        if layer is None or layer.visible == bool(visible):
            return
        layer.visible = bool(visible)
        self._apply_design_layers(visibility_changed=True)

    def _on_stack_remove(self, uid):
        layer = self.stack.by_id(uid)
        if layer is None or layer.locked or not layer.is_copper or len(self.stack) <= 1:
            return
        used = [e for e in self._entries
                if self.design_layer_of(e) is not None
                and self.design_layer_of(e).id == uid]
        if used:
            answer = QtWidgets.QMessageBox.question(
                self, "Remove layer",
                "%d footprint%s sit on this layer.\n"
                "Remove the layer and move them to the nearest one?"
                % (len(used), "" if len(used) == 1 else "s"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return

        fallback = self.stack.remove(uid)
        if fallback is None:
            return
        for entry in self._entries:
            if entry["item"].design_layer_id == uid:
                entry["item"].design_layer_id = fallback
        for track in self.tracks:
            if track.layer_id == uid:
                track.layer_id = fallback
        for via in self.vias:
            if via.start_layer_id == uid:
                via.start_layer_id = fallback
            if via.end_layer_id == uid:
                via.end_layer_id = fallback
        if self.via_target_layer_id == uid:
            self.via_target_layer_id = fallback
        if self.routing is not None and self.routing.get("layer_id") == uid:
            self._finish_route()
        self._sync_via_target_combo()
        self._apply_design_layers()
        self._rebuild_stack()
        self._rebuild_stackup()
        self._refresh_properties()

    def _fill_layer_combo(self, layer_id, mixed=False):
        """Populate the Properties layer dropdown, top plane first."""
        combo = self.prop_layer_combo
        # Repopulating fires currentIndexChanged; that must not be mistaken for
        # the user picking a layer and reassigning the whole selection.
        self._updating_layer_combo = True
        try:
            combo.clear()
            if mixed:
                combo.addItem(MIXED, None)
            for layer in reversed(self.stack.layers):
                combo.addItem(layer.name, layer.id)
            if mixed:
                combo.setCurrentIndex(0)
            else:
                index = combo.findData(layer_id)
                combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._updating_layer_combo = False

    def _on_property_layer(self, index):
        if self._updating_layer_combo or index < 0:
            return
        uid = self.prop_layer_combo.itemData(index)
        if uid is None:                       # the "mixed" placeholder
            return
        layer = self.stack.by_id(uid)
        if layer is None or not layer.is_copper:
            return
        targets = self._property_targets()
        changed = False
        for entry in targets:
            if entry is not None and entry["item"].design_layer_id != uid:
                entry["item"].design_layer_id = uid
                changed = True
        if not changed:
            return
        self._apply_design_layers()

    def _set_rotation_control(self, entries):
        self._updating_rotation = True
        try:
            rotations = [(-float(e["item"].rotation())) % 360.0 for e in entries]
            if not rotations:
                self.prop_rotation_row.hide()
                return
            same = all(abs(r - rotations[0]) < 1e-7 for r in rotations[1:])
            self.prop_rotation.setValue(rotations[0] if same else -1.0)
            self.prop_rotation_row.show()
        finally:
            self._updating_rotation = False

    def _on_property_rotation(self):
        if self._updating_rotation:
            return
        value = float(self.prop_rotation.value())
        if value < 0.0:                         # mixed placeholder
            return
        targets = self._property_targets()
        if not targets:
            return
        items = [e["item"] for e in targets]
        before = self._transform_states(items)
        qt_angle = (-value) % 360.0
        changed = False
        for item in items:
            if abs((float(item.rotation()) % 360.0) - qt_angle) > 1e-7:
                item.setRotation(qt_angle)
                changed = True
        if not changed:
            return
        after = self._transform_states(items)
        self._push_history({
            "kind": "transform", "before": before, "after": after,
            "selection": self._selected_uids(), "active": self._active_uid(),
        })
        self.canvas.refresh_extent()
        self._refresh_ratsnest()
        self._refresh_3d_if_visible(force=True)
        self._refresh_properties()


    # -- netlist / ratsnest / 3D board view ------------------------------
    def last_import_directory(self):
        return self._import_directory if os.path.isdir(self._import_directory) else os.path.expanduser("~")

    def remember_import_directory(self, path):
        path = os.path.abspath(str(path or ""))
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.isdir(path):
            self._import_directory = path

    def open_netlist_wizard(self):
        """Open the native four-step KiCad netlist import wizard."""
        from .netlist_ui import NetlistImportWizard
        from .qt import exec_dialog
        dlg = NetlistImportWizard(self, self)
        self._netlist_dialog = dlg
        try:
            return bool(exec_dialog(dlg))
        finally:
            self._netlist_dialog = None

    def _clear_footprint_entries_only(self):
        """Remove footprint instances while retaining board solid/stack state."""
        scene = self.canvas.scene()
        scene.blockSignals(True)
        try:
            for entry in list(self._entries):
                self.canvas.remove_footprint(entry["item"])
        finally:
            scene.blockSignals(False)
        self._entries = []
        self._active = None
        self._selected = []
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self.canvas.set_net_highlight([], False)
        self.tracks = []
        self.vias = []
        self.routing = None
        self.via_pending = False
        self._route_history_before = None
        self.canvas.clear_routed_copper()
        self.canvas.select_pad(None, None)
        self._rebuild_footprint_list()
        self._rebuild_layers()
        self._rebuild_pads()
        self._rebuild_stack()

    def apply_netlist(self, netlist):
        """Replace footprint instances with schematic components/placeholders.

        Importing a netlist starts a new unsaved board identity even when it is
        intentionally laid into an existing board solid/stackup.  Retaining the
        previous .e3d_pcb filename here made Ctrl+S overwrite the old design and
        left FreeCAD's MDI tab showing a stale filename.
        """
        self._invalidate_routing_cache()
        if not isinstance(netlist, KiCadNetlist):
            raise TypeError("netlist must be a KiCadNetlist")
        self._reset_design_identity()
        self._clear_footprint_entries_only()
        self.clear_history()
        self.netlist = netlist
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self.canvas.set_net_highlight([], False)
        pin_map = pins_by_ref(netlist)
        for comp in netlist.components:
            fp = placeholder_footprint(comp.footprint, pin_map.get(comp.ref, []))
            self.add_footprint(
                fp, select=False, ref=comp.ref, value=comp.value,
                footprint_name=comp.footprint, placeholder=True, fit=False)
        self._flow_place_netlist_entries()
        if self._entries:
            self.set_active(self._entries[0])
            self.canvas.select_footprint(self._entries[0]["item"])
        self.chk_ratsnest.show()
        self._refresh_ratsnest()
        self.canvas.refresh_extent()
        self.canvas.fit()
        self._refresh_3d_if_visible(force=True)
        return len(self._entries)

    def _flow_place_netlist_entries(self):
        """Compact deterministic row placement for newly imported components.

        When a board solid exists, placement starts inside its bounding region
        (the same board-first behaviour as the reference app).  Without one,
        rows are centred around the editor origin.
        """
        if not self._entries:
            return
        has_board = self.board_solid is not None
        if has_board:
            bx0, by0, bx1, by1 = board_bounds([], self.board_solid)
            start_x, start_y = bx0 + 3.0, by0 + 3.0
            max_x = bx1 - 3.0
            target_width = max(10.0, max_x - start_x)
        else:
            start_x = start_y = 0.0
            target_width = max(45.0, min(120.0, 12.0 * max(4, int(len(self._entries) ** 0.5))))
            max_x = start_x + target_width

        x, y = start_x, start_y
        row_h = 0.0
        bounds = []
        old_snap = getattr(self.canvas.scene(), "snap_step", 0.0)
        self.canvas.scene().snap_step = 0.0
        try:
            for entry in self._entries:
                minx, miny, maxx, maxy = entry["fp"].bbox()
                w = max(2.0, maxx - minx) + 3.0
                h = max(2.0, maxy - miny) + 3.0
                if x > start_x and x + w > max_x:
                    x = start_x
                    y += row_h
                    row_h = 0.0
                px, py = x - minx + 1.5, y - miny + 1.5
                entry["item"].setPos(px, py)
                bounds.append((px + minx, py + miny, px + maxx, py + maxy))
                x += w
                row_h = max(row_h, h)
            if bounds and not has_board:
                cx = (min(b[0] for b in bounds) + max(b[2] for b in bounds)) / 2.0
                cy = (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2.0
                for entry in self._entries:
                    pos = entry["item"].pos()
                    entry["item"].setPos(pos.x() - cx, pos.y() - cy)
        finally:
            self.canvas.scene().snap_step = old_snap

    @staticmethod
    def _footprint_basename(name):
        return (str(name or "").split(":")[-1] + ".kicad_mod").lower()

    def _footprint_file_index(self, folder):
        result = {}
        folder = os.path.abspath(folder)
        base_depth = folder.rstrip(os.sep).count(os.sep)
        for current, dirs, names in os.walk(folder):
            depth = current.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= 5:
                dirs[:] = []
            for name in names:
                if name.lower().endswith(".kicad_mod"):
                    result.setdefault(name.lower(), os.path.join(current, name))
        return result

    def _replace_entry_footprint(self, entry, fp):
        old_item = entry["item"]
        pos = QtCore.QPointF(old_item.pos())
        rotation = float(old_item.rotation())
        layers = dict(getattr(old_item, "layer_visibility", {}))
        hidden = bool(getattr(old_item, "_hidden_texts_visible", False))
        design_layer_id = getattr(old_item, "design_layer_id", None)
        selected = old_item.isSelected()
        self.canvas.remove_footprint(old_item)
        self._assign_pad_net_names(fp, entry.get("ref", ""))
        item = self.canvas.add_footprint(fp, pos, reference_override=entry.get("ref", ""))
        item.setRotation(rotation)
        item.design_layer_id = design_layer_id if self.stack.has(design_layer_id) else self.stack.active_copper().id
        for layer, visible in layers.items():
            if layer in item.layer_items:
                item.set_layer_visible(layer, visible)
        item.set_hidden_texts_visible(hidden)
        entry["item"] = item
        entry["fp"] = fp
        entry["placeholder"] = False
        if selected:
            item.setSelected(True)
        return item

    def resolve_netlist_footprints(self, folder=None, files=None):
        """Resolve placeholder components from a KiCad library folder/files.

        The common ``Lib:Name -> Lib.pretty/Name.kicad_mod`` path is tried
        directly first.  Only unresolved custom layouts fall back to a bounded
        recursive filename index, which keeps the standard KiCad library fast.
        """
        if not self.netlist:
            return {"resolved": 0, "pending": []}
        candidates = {}
        for path in files or []:
            if os.path.isfile(path):
                candidates[os.path.basename(path).lower()] = path

        folder = os.path.abspath(folder) if folder and os.path.isdir(folder) else None
        pending_names = []
        for entry in self._entries:
            if entry.get("placeholder") and entry.get("footprint_name") \
                    and entry["footprint_name"] not in pending_names:
                pending_names.append(entry["footprint_name"])

        unresolved = []
        if folder:
            for fp_name in pending_names:
                parts = str(fp_name).split(":")
                short = parts[-1]
                library = parts[0] if len(parts) > 1 else ""
                choices = [os.path.join(folder, short + ".kicad_mod")]
                if library:
                    choices.insert(0, os.path.join(
                        folder, library + ".pretty", short + ".kicad_mod"))
                    if os.path.basename(folder).lower() == (library + ".pretty").lower():
                        choices.insert(0, os.path.join(folder, short + ".kicad_mod"))
                hit = next((candidate for candidate in choices
                            if os.path.isfile(candidate)), None)
                if hit:
                    candidates[self._footprint_basename(fp_name)] = hit
                else:
                    unresolved.append(fp_name)
            if unresolved:
                index = self._footprint_file_index(folder)
                for fp_name in unresolved:
                    key = self._footprint_basename(fp_name)
                    if key in index:
                        candidates[key] = index[key]

        resolved = 0
        for entry in self._entries:
            if not entry.get("placeholder"):
                continue
            path = candidates.get(self._footprint_basename(entry.get("footprint_name")))
            if not path:
                continue
            try:
                fp = parse_footprint_file(path)
                self._replace_entry_footprint(entry, fp)
                resolved += 1
            except Exception:
                continue
        if resolved:
            self._flow_place_netlist_entries()
            self._apply_design_layers()
            self._rebuild_footprint_list()
            self._rebuild_layers()
            self._rebuild_pads()
            self._rebuild_stack()
            self.canvas.refresh_extent()
            self.canvas.fit()
            self._refresh_ratsnest()
            self._refresh_3d_if_visible(force=True)
        return {"resolved": resolved, "pending": self.pending_netlist_footprints()}

    def pending_netlist_footprints(self):
        seen = []
        for entry in self._entries:
            name = entry.get("footprint_name", "")
            if entry.get("placeholder") and name and name not in seen:
                seen.append(name)
        return seen

    def resolved_netlist_count(self):
        return sum(1 for entry in self._entries if not entry.get("placeholder", False))

    def set_models_folder(self, path):
        self.models_folder = os.path.abspath(path) if path and os.path.isdir(path) else ""
        self.view3d.set_models_folder(self.models_folder)
        self._refresh_3d_if_visible(force=True)

    def choose_models_folder(self):
        """Pick the folder holding the .step files these footprints reference.

        Previously this was reachable only from the netlist wizard, so a plain
        footprint import could never show a 3D body no matter what the
        ``.kicad_mod`` pointed at.  The folder is a property of the board being
        assembled, not of how the parts happened to arrive.
        """
        start = self.models_folder or self._last_import_dir()
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "3D models folder", start or "")
        if not path:
            return
        self.set_models_folder(path)
        total, found = self.model_reference_stats()
        if not total:
            self._notify("No loaded footprint references a 3D model.")
        elif found:
            self._notify("Models folder set \u2014 resolved %d of %d referenced "
                         "model%s." % (found, total, "" if total == 1 else "s"))
        else:
            self._notify("Models folder set, but none of the %d referenced "
                         "model file%s were found in it."
                         % (total, "" if total == 1 else "s"))

    def choose_model_for_selection(self):
        """Attach a STEP file to the selected footprint.

        A folder only helps when the footprint already names a model file. This
        covers the other case: a footprint with no ``(model ...)`` line at all,
        or one whose reference cannot be resolved.
        """
        targets = self._property_targets()
        if len(targets) != 1:
            self._notify("Select exactly one footprint first.")
            return
        entry = targets[0]
        start = self.models_folder or self._last_import_dir()
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "3D model for %s" % entry["fp"].name, start or "",
            "STEP models (*.step *.stp);;All files (*)")
        if not path:
            return
        fp = entry["fp"]
        model = getattr(fp, "model", None)
        if model is None:
            fp.model = fpmod.Model(path, (0.0, 0.0, 0.0),
                                   (1.0, 1.0, 1.0), (0.0, 0.0, 0.0))
        else:
            # Keep the offset/scale/rotation the footprint already specified --
            # the file is wrong, not the placement.
            model.path = path
        if not self.models_folder:
            # The chosen file's folder is the obvious place to look for the
            # rest, so adopt it rather than asking again for every part.
            self.set_models_folder(os.path.dirname(path))
        else:
            self._refresh_3d_if_visible(force=True)
        self._refresh_properties()
        self._notify("3D model set for %s." % fp.name)

    def _last_import_dir(self):
        for entry in reversed(self._entries):
            source = getattr(entry.get("fp"), "source_file", "")
            if source:
                return os.path.dirname(source)
        return ""


    # -- STEP / FreeCAD export -----------------------------------------
    def _export_entries(self):
        return [self._entry_3d_state(entry) for entry in self._entries]

    def _prepare_models_for_export(self):
        """Resolve Auto model corrections even when the user never opened 3D.

        Auto alignment is normally measured by the document-free 3D preview.
        Export must honour the same placement rules from a purely 2D session, so
        build the preview scene off-screen, apply its measured correction, then
        rebuild once if any Auto entry changed.
        """
        auto_entries = [e for e in self._entries
                        if e.get("model_adjust_mode", "auto") == "auto"
                        and getattr(e.get("fp"), "model", None) is not None
                        and getattr(e["fp"].model, "path", "")]
        if not auto_entries:
            return False
        states = self._export_entries()
        self.view3d.rebuild(
            states, self.stack, self.board_solid,
            self.models_folder, force=True, routed={"tracks": self.tracks, "vias": self.vias})
        changed = False
        for entry in auto_entries:
            measure = self.view3d.measure_model(entry.get("uid"))
            if measure is None:
                continue
            adjust = entry.setdefault("model_adjust", {"x": 0.0, "y": 0.0, "z": 0.0})
            nx = round(float(adjust.get("x", 0.0)) + measure["dx"], 4)
            ny = round(float(adjust.get("y", 0.0)) + measure["dy"], 4)
            nz = float(adjust.get("z", 0.0))
            if not measure["below"]:
                nz = round(nz + measure["dz"], 4)
            if (abs(nx - float(adjust.get("x", 0.0))) > 1e-5 or
                    abs(ny - float(adjust.get("y", 0.0))) > 1e-5 or
                    abs(nz - float(adjust.get("z", 0.0))) > 1e-5):
                entry["model_adjust"] = {"x": nx, "y": ny, "z": nz}
                changed = True
        if changed:
            self.view3d.rebuild(
                self._export_entries(), self.stack,
                self.board_solid,
                self.models_folder, force=True,
                routed={"tracks": self.tracks, "vias": self.vias})
            if self.view_mode == "3d":
                self.view3d.set_selected(self._active_uid())
            self._refresh_properties()
        return changed

    def _begin_export(self):
        if self.canvas.has_active_interaction():
            self._notify("Release the current mouse operation before exporting.")
            return False
        if self.routing is not None:
            self._finish_route()
        return True

    def _export_dialog(self, destination):
        if not self._begin_export():
            return None
        try:
            from . import export3d
            counts = export3d.export_counts(
                self._export_entries(), self.stack,
                self.board_solid,
                self.tracks, self.vias, self.models_folder)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "3D export", str(exc))
            return None
        target_document = ""
        target_available = False
        if destination == "freecad":
            target_document = self._freecad_export_document
            target_available = export3d.document_is_open(target_document)
            if target_document and not target_available:
                self._freecad_export_document = ""
                target_document = ""
        dialog = Export3DDialog(
            counts, self._export_options, destination, self,
            target_document=target_document, target_available=target_available)
        if not exec_dialog(dialog):
            return None
        self._export_options = dialog.selected_options()
        return dict(self._export_options)

    def _export_source_name(self):
        if self._design_file:
            return self._design_file
        if self.netlist is not None:
            return getattr(self.netlist, "source_file", "") or "board"
        return "board"

    def _export_progress(self, text):
        self._notify(str(text or "").rstrip("\x00"))
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def export_step_dialog(self, _checked=False):
        opts = self._export_dialog("step")
        if opts is None:
            return False
        if opts.get("models"):
            self._prepare_models_for_export()
        from . import export3d
        default_name = export3d.export_file_name(opts)
        start_dir = os.path.dirname(self._design_file) if self._design_file else ""
        start = os.path.join(start_dir, default_name) if start_dir else default_name
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export STEP", start, "STEP files (*.step *.stp)")
        if not path:
            return False
        if not path.lower().endswith((".step", ".stp")):
            path += ".step"
        cursor = enum(Qt, "CursorShape", "WaitCursor")
        QtWidgets.QApplication.setOverrideCursor(cursor)
        try:
            result = export3d.export_step(
                self._export_entries(), self.stack,
                self.board_solid,
                self.tracks, self.vias, self.models_folder, opts, path,
                source_name=self._export_source_name(), progress=self._export_progress)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export STEP", str(exc))
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        size = float(result.get("bytes", 0)) / (1024.0 * 1024.0)
        message = "STEP exported: %s · %d named part%s · %.2f MB" % (
            os.path.basename(path), int(result.get("parts", 0)),
            "" if int(result.get("parts", 0)) == 1 else "s", size)
        warnings = result.get("warnings") or []
        if warnings:
            message += " · %d warning%s" % (len(warnings), "" if len(warnings) == 1 else "s")
            QtWidgets.QMessageBox.warning(self, "Export STEP",
                                          message + "\n\n" + "\n".join(warnings[:20]))
        self._notify(message)
        return True

    def export_freecad_dialog(self, _checked=False):
        opts = self._export_dialog("freecad")
        if opts is None:
            return False
        if opts.get("models"):
            self._prepare_models_for_export()
        from . import export3d
        cursor = enum(Qt, "CursorShape", "WaitCursor")
        QtWidgets.QApplication.setOverrideCursor(cursor)
        try:
            result = export3d.create_freecad_document(
                self._export_entries(), self.stack,
                self.board_solid,
                self.tracks, self.vias, self.models_folder, opts,
                source_name=self._export_source_name(), progress=self._export_progress,
                target_document=self._freecad_export_document,
                update_existing=bool(opts.get("update_existing")))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export to FreeCAD", str(exc))
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        doc = result.get("document")
        self._freecad_export_document = getattr(doc, "Name", "") or ""
        try:
            from . import viewer as viewerhost
            viewerhost.keep_viewer_tab_available()
        except Exception:
            pass
        verb = "Updated" if result.get("updated") else "Created"
        message = "%s FreeCAD document %s · %d part%s" % (
            verb, getattr(doc, "Label", None) or getattr(doc, "Name", "PCB Export"),
            int(result.get("parts", 0)), "" if int(result.get("parts", 0)) == 1 else "s")
        warnings = result.get("warnings") or []
        if warnings:
            message += " · %d warning%s" % (len(warnings), "" if len(warnings) == 1 else "s")
            QtWidgets.QMessageBox.warning(self, "Export to FreeCAD",
                                          message + "\n\n" + "\n".join(warnings[:20]))
        self._notify(message)
        return True

    def _notify(self, text):
        """Surface a short result in the status bar, and in the log if hosted."""
        try:
            self.status_hover.setText(text)
        except Exception:
            pass

    def _flash_status(self, text):
        """Compatibility helper for short editor/DRC status notifications.

        v0.13 moved transient feedback into the shared bottom status bar, but
        several dialog and DRC call sites still use the older helper name.
        Keep one implementation point so those paths cannot raise after a
        successful edit.
        """
        self._notify(text)

    def model_reference_stats(self):
        referenced = [entry for entry in self._entries
                      if getattr(entry.get("fp"), "model", None) is not None
                      and getattr(entry["fp"].model, "path", "")]
        if not referenced:
            return (0, 0)
        self.view3d.set_models_folder(self.models_folder)
        found = sum(1 for entry in referenced
                    if self.view3d._resolved_model(entry["fp"].model))
        return (len(referenced), found)

    def _entry_3d_state(self, entry):
        item = entry["item"]
        return {
            "uid": entry.get("uid"), "fp": entry["fp"],
            "pos": QtCore.QPointF(item.pos()),
            "rotation": float(item.rotation()),
            "design_layer_id": getattr(item, "design_layer_id", None),
            "layer_visibility": dict(getattr(item, "layer_visibility", {})),
            "model_adjust": dict(entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}),
            "model_adjust_mode": entry.get("model_adjust_mode", "auto"),
        }

    def _refresh_3d_if_visible(self, force=False, auto_align=True):
        if getattr(self, "view_mode", "2d") != "3d":
            return
        self.view3d.rebuild(
            [self._entry_3d_state(e) for e in self._entries], self.stack,
            self.board_solid,
            self.models_folder, force=force, routed={"tracks": self.tracks, "vias": self.vias})
        primary = self._active if self._active in self._property_targets() else (self._property_targets()[0] if self._property_targets() else None)
        self.view3d.set_selected(primary.get("uid") if primary is not None else None)
        if auto_align:
            self._auto_align_models()

    def set_view_mode(self, mode):
        mode = "3d" if str(mode).lower() == "3d" else "2d"
        if mode == "3d" and self.routing is not None:
            self._finish_route()
        if mode == "3d" and not self._entries and self.board_solid is None:
            self.status_hover.setText("import a board solid or add a footprint first, then press 3")
            blocked = self.btn_3d.blockSignals(True); self.btn_3d.setChecked(False); self.btn_3d.blockSignals(blocked)
            blocked = self.btn_2d.blockSignals(True); self.btn_2d.setChecked(True); self.btn_2d.blockSignals(blocked)
            return False
        self.view_mode = mode
        blocked = self.btn_2d.blockSignals(True); self.btn_2d.setChecked(mode == "2d"); self.btn_2d.blockSignals(blocked)
        blocked = self.btn_3d.blockSignals(True); self.btn_3d.setChecked(mode == "3d"); self.btn_3d.blockSignals(blocked)
        self.view_stack.setCurrentWidget(self.page_2d if mode == "2d" else self.view3d)
        self.zoom_label.setVisible(mode == "2d")
        self.btn_grid.setVisible(mode == "2d")
        self._sync_tool_rail()
        self._sync_routing_ui()
        if mode == "3d":
            self._refresh_3d_if_visible(force=False)
            self.view3d.setFocus()
        else:
            self.canvas.setFocus()
            self.canvas.viewport().update()
        self._refresh_properties()
        return True

    # -- 3D selection / model alignment ---------------------------------
    def _on_3d_selection(self, uid):
        """Keep 3D picking, the 2D scene and the sidebar on one selection."""
        self.canvas.select_pad(None, None)
        if uid is None:
            self.canvas.select_footprints([])
            self.view3d.set_selected(None)
            self._refresh_properties()
            return
        entry = self._entry_by_uid(uid)
        if entry is None:
            return
        self.canvas.select_footprint(entry["item"])
        self.set_active(entry)
        self.view3d.set_selected(uid)
        self._refresh_properties()

    @staticmethod
    def _model_adjust_state(entry):
        return {
            "uid": entry.get("uid"),
            "mode": entry.get("model_adjust_mode", "auto"),
            "adjust": dict(entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}),
        }

    def _apply_model_adjust_state(self, state):
        entry = self._entry_by_uid(state.get("uid")) if state else None
        if entry is None:
            return False
        entry["model_adjust_mode"] = state.get("mode", "auto")
        entry["model_adjust"] = dict(state.get("adjust") or {"x": 0.0, "y": 0.0, "z": 0.0})
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_properties()
        return True

    def _push_model_adjust_history(self, before, after):
        if not before or not after or before == after or self._history_replaying:
            return
        self._push_history({"kind": "model_adjust", "before": before, "after": after})

    def _update_model_adjust_section(self, entry):
        visible = self.view_mode == "3d" and entry is not None
        self.sec_model_adjust.setVisible(visible)
        if not visible:
            return
        self._updating_model_adjust = True
        try:
            mode = entry.get("model_adjust_mode", "auto")
            index = self.model_adjust_mode.findData(mode)
            self.model_adjust_mode.setCurrentIndex(max(0, index))
            adjust = entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}
            for axis, spin in self.model_adjust_spins.items():
                blocked = spin.blockSignals(True)
                spin.setValue(float(adjust.get(axis, 0.0)))
                spin.blockSignals(blocked)
            manual = mode == "manual"
            self.model_adjust_manual.setVisible(manual)
            self.model_adjust_buttons.setVisible(manual)
        finally:
            self._updating_model_adjust = False
        self._refresh_model_adjust_readout(entry)

    def _refresh_model_adjust_readout(self, entry=None):
        entry = entry or (self._property_targets()[0] if len(self._property_targets()) == 1 else None)
        if entry is None or self.view_mode != "3d":
            return
        measure = self.view3d.measure_model(entry.get("uid"))
        if measure is None:
            self.model_adjust_readout.setText("no 3D model loaded for this footprint")
            return
        adjust = entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}
        mode = entry.get("model_adjust_mode", "auto")
        if mode == "auto":
            if measure["below"]:
                self.model_adjust_readout.setText(
                    "Auto: centered in XY; model extends below the layer, so Z is left unchanged")
            else:
                self.model_adjust_readout.setText(
                    "Auto: X %+0.3f  Y %+0.3f  Z %+0.3f mm" %
                    (adjust.get("x", 0.0), adjust.get("y", 0.0), adjust.get("z", 0.0)))
            return
        off = math.hypot(measure["dx"], measure["dy"])
        if abs(measure["gap"]) < 0.0005 and off < 0.0005:
            text = "seated on pad tops and centred"
        else:
            text = "underside %+0.3f mm · off-centre %.3f mm" % (measure["gap"], off)
            if measure["below"]:
                text += " · extends below layer"
        self.model_adjust_readout.setText(text)

    def _on_model_adjust_live(self, axis, value):
        if self._updating_model_adjust or self.view_mode != "3d":
            return
        targets = self._property_targets()
        if len(targets) != 1:
            return
        entry = targets[0]
        if entry.get("model_adjust_mode", "auto") != "manual":
            return
        if self._model_adjust_edit_before is None:
            self._model_adjust_edit_before = self._model_adjust_state(entry)
        entry.setdefault("model_adjust", {"x": 0.0, "y": 0.0, "z": 0.0})[axis] = float(value)
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._refresh_model_adjust_readout(entry)

    def _commit_model_adjust_edit(self):
        before = self._model_adjust_edit_before
        self._model_adjust_edit_before = None
        targets = self._property_targets()
        if before is None or len(targets) != 1:
            return
        self._push_model_adjust_history(before, self._model_adjust_state(targets[0]))

    def _on_model_adjust_mode(self, _index):
        if self._updating_model_adjust:
            return
        targets = self._property_targets()
        if len(targets) != 1:
            return
        entry = targets[0]
        before = self._model_adjust_state(entry)
        entry["model_adjust_mode"] = str(self.model_adjust_mode.currentData() or "auto")
        if entry["model_adjust_mode"] == "auto":
            self._refresh_3d_if_visible(force=True, auto_align=True)
        else:
            self._refresh_3d_if_visible(force=True, auto_align=False)
        after = self._model_adjust_state(entry)
        self._push_model_adjust_history(before, after)
        self._update_model_adjust_section(entry)

    def _align_model(self, axes):
        targets = self._property_targets()
        if self.view_mode != "3d" or len(targets) != 1:
            return
        entry = targets[0]
        measure = self.view3d.measure_model(entry.get("uid"))
        if measure is None:
            self._notify("No 3D model is loaded for the selected footprint.")
            return
        before = self._model_adjust_state(entry)
        adjust = entry.setdefault("model_adjust", {"x": 0.0, "y": 0.0, "z": 0.0})
        if axes == "z":
            adjust["z"] = round(float(adjust.get("z", 0.0)) + measure["dz"], 4)
        else:
            adjust["x"] = round(float(adjust.get("x", 0.0)) + measure["dx"], 4)
            adjust["y"] = round(float(adjust.get("y", 0.0)) + measure["dy"], 4)
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._push_model_adjust_history(before, self._model_adjust_state(entry))
        self._update_model_adjust_section(entry)

    def _reset_model_adjust(self):
        targets = self._property_targets()
        if len(targets) != 1:
            return
        entry = targets[0]
        before = self._model_adjust_state(entry)
        entry["model_adjust"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._refresh_3d_if_visible(force=True, auto_align=False)
        self._push_model_adjust_history(before, self._model_adjust_state(entry))
        self._update_model_adjust_section(entry)

    def _auto_align_models(self):
        """Apply the reference viewer's measurement-based automatic alignment."""
        if self._auto_aligning_models or self.view_mode != "3d":
            return False
        self._auto_aligning_models = True
        changed = False
        try:
            for entry in self._entries:
                if entry.get("model_adjust_mode", "auto") != "auto":
                    continue
                measure = self.view3d.measure_model(entry.get("uid"))
                if measure is None:
                    continue
                adjust = entry.setdefault("model_adjust", {"x": 0.0, "y": 0.0, "z": 0.0})
                nx = round(float(adjust.get("x", 0.0)) + measure["dx"], 4)
                ny = round(float(adjust.get("y", 0.0)) + measure["dy"], 4)
                nz = float(adjust.get("z", 0.0))
                # As in the reference app, don't lift a THT model by seating the
                # bottom of its pins on the copper plane.
                if not measure["below"]:
                    nz = round(nz + measure["dz"], 4)
                if (abs(nx - adjust.get("x", 0.0)) > 1e-5 or
                        abs(ny - adjust.get("y", 0.0)) > 1e-5 or
                        abs(nz - adjust.get("z", 0.0)) > 1e-5):
                    entry["model_adjust"] = {"x": nx, "y": ny, "z": nz}
                    changed = True
            if changed:
                self.view3d.rebuild(
                    [self._entry_3d_state(e) for e in self._entries], self.stack,
                    self.board_solid,
                    self.models_folder, force=True, routed={"tracks": self.tracks, "vias": self.vias})
                self.view3d.set_selected(self._active_uid())
            if len(self._property_targets()) == 1:
                self._update_model_adjust_section(self._property_targets()[0])
            return changed
        finally:
            self._auto_aligning_models = False


    def _ensure_ratsnest_item(self):
        if self._ratsnest_item is not None:
            return self._ratsnest_item
        item = QtWidgets.QGraphicsPathItem()
        item.setZValue(-500.0)
        try:
            item.setAcceptedMouseButtons(Qt.NoButton)
        except Exception:
            pass
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.canvas.scene().addItem(item)
        self._ratsnest_item = item
        return item

    def _ensure_ratsnest_highlight_item(self):
        if self._ratsnest_highlight_item is not None:
            return self._ratsnest_highlight_item
        item = QtWidgets.QGraphicsPathItem()
        item.setZValue(-499.0)
        try:
            item.setAcceptedMouseButtons(Qt.NoButton)
        except Exception:
            pass
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.canvas.scene().addItem(item)
        self._ratsnest_highlight_item = item
        return item

    def _refresh_ratsnest_throttled(self, sync_copper_nets=False, interval=0.08):
        """Live ratsnest update that is bounded in cost during a drag.

        Airwires should follow dragged copper, but a full recompute per mouse
        event dominates the frame budget on a large board.  Updates run on the
        leading edge and are then coalesced, with a trailing single-shot timer
        guaranteeing the final geometry is always drawn.
        """
        now = _time.monotonic()
        last = getattr(self, "_ratsnest_last_refresh", 0.0)
        if now - last >= float(interval):
            self._ratsnest_last_refresh = now
            self._refresh_ratsnest(sync_copper_nets=sync_copper_nets)
            return
        timer = getattr(self, "_ratsnest_timer", None)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda: self._refresh_ratsnest(sync_copper_nets=False))
            self._ratsnest_timer = timer
        if not timer.isActive():
            timer.start(int(float(interval) * 1000.0))

    def _refresh_ratsnest(self, sync_copper_nets=True):
        """Rebuild airwires and optionally synchronize routed-copper metadata.

        During a mouse-held footprint transform the scene geometry is transient.
        The ratsnest should follow that geometry live, but it must not persist
        net assignments derived from a footprint merely passing over copper.
        Final release/undo/redo calls use the default synchronized mode.

        Schematic names are assigned to every unambiguous connected copper
        island before drawing.  With KiCad-style net highlighting active, the
        selected net's airwires are drawn brightly while unrelated airwires are
        dimmed rather than hidden.
        """
        item = self._ensure_ratsnest_item()
        highlight_item = self._ensure_ratsnest_highlight_item()
        path = QtGui.QPainterPath()
        highlight_path = QtGui.QPainterPath()

        if self.netlist is not None and sync_copper_nets:
            self._assign_saved_connection_nets()

        if self.netlist is not None and self.show_ratsnest:
            suppressed = self.routing.get("net") if self.routing is not None else None
            highlight_on = bool(self._net_highlight_enabled and self._highlighted_nets)
            for a, b, net_name in ratsnest_segments(
                    self._pads_by_net(), self.tracks, self.vias,
                    self._layer_order(), suppressed_net=suppressed):
                target = highlight_on and net_name in self._highlighted_nets
                target_path = highlight_path if target else path
                target_path.moveTo(float(a["x"]), float(a["y"]))
                target_path.lineTo(float(b["x"]), float(b["y"]))

        item.setPath(path)
        highlight_item.setPath(highlight_path)

        base = QtGui.QColor(thememod.palette(self.theme)["text_dim"])
        base.setAlpha(38 if (self._net_highlight_enabled and self._highlighted_nets) else 125)
        pen = QtGui.QPen(base, 1.0, Qt.DashLine)
        try:
            pen.setCosmetic(True)
        except Exception:
            pass
        item.setPen(pen)

        accent = QtGui.QColor(thememod.palette(self.theme)["selection"])
        accent = accent.lighter(125)
        accent.setAlpha(235)
        hpen = QtGui.QPen(accent, 1.5, Qt.DashLine)
        try:
            hpen.setCosmetic(True)
        except Exception:
            pass
        highlight_item.setPen(hpen)

        visible = self.netlist is not None and self.show_ratsnest
        item.setVisible(visible and not path.isEmpty())
        highlight_item.setVisible(visible and not highlight_path.isEmpty())
        if hasattr(self, "chk_ratsnest"):
            self.chk_ratsnest.setVisible(self.netlist is not None)

    def _on_ratsnest_toggle(self, visible):
        self.show_ratsnest = bool(visible)
        self._refresh_ratsnest()

    def canvas_fit(self):
        if self.view_mode == "3d":
            self.view3d.fit()
        else:
            self.canvas.fit()

    def sync_application_theme(self, force=False):
        """Match the custom viewer to FreeCAD's current light/dark palette."""
        if not self.hosted:
            return
        theme = thememod.application_theme(self.theme)
        if force or theme != self.theme:
            self.apply_theme(theme)

    def _queue_theme_sync(self):
        if getattr(self, "_theme_sync_pending", False) or not getattr(self, "hosted", False):
            return
        self._theme_sync_pending = True

        def sync():
            self._theme_sync_pending = False
            self.sync_application_theme()

        QtCore.QTimer.singleShot(0, sync)

    def event(self, event):
        result = super(Viewer2D, self).event(event)
        if getattr(self, "hosted", False):
            etype = event.type()
            watched = []
            for name in ("ApplicationPaletteChange", "PaletteChange"):
                value = getattr(QtCore.QEvent, name, None)
                if value is None and hasattr(QtCore.QEvent, "Type"):
                    value = getattr(QtCore.QEvent.Type, name, None)
                if value is not None:
                    watched.append(value)
            if etype in watched:
                self._queue_theme_sync()
        return result

    def apply_theme(self, theme):
        if theme not in ("dark", "light"):
            theme = thememod.application_theme(self.theme) if self.hosted else "dark"
        self.theme = theme
        self.setStyleSheet(thememod.stylesheet(theme))
        self.canvas.set_theme(theme)
        self.view3d.set_theme(theme)
        self._refresh_ratsnest()
        self._rebuild_layers()

    # -- sidebar contents ------------------------------------------------
    def _rebuild_footprint_list(self):
        self.sec_footprints.clear()
        self.sec_footprints.set_count("(%d)" % len(self._entries) if self._entries else "")
        if not self._entries:
            empty = QtWidgets.QLabel("nothing loaded yet")
            empty.setObjectName("faint")
            self.sec_footprints.add(empty)
            return
        for entry in self._entries:
            label = entry.get("ref") or entry["fp"].name
            if entry.get("ref") and entry.get("value"):
                label = "%s   %s" % (entry["ref"], entry["value"])
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName("rowButton")
            btn.setCheckable(True)
            btn.setChecked(entry in self._selected)
            btn.setToolTip(os.path.basename(entry["fp"].source_file or ""))
            btn.clicked.connect(lambda _=False, e=entry: self._select_entry(e))
            entry["row"] = btn
            self.sec_footprints.add(btn)

    def _select_entry(self, entry):
        # If this is already the active footprint, selection alone must not
        # rebuild Footprint Graphics/Pads.  This is the common "nothing
        # selected -> click the active footprint" path and used to make the
        # whole left panel look as if it reloaded.
        if entry is not self._active:
            self.set_active(entry)
        self.canvas.select_footprint(entry["item"])

    def _rebuild_layers(self):
        while self.layer_layout.count():
            item = self.layer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if self._active is None:
            return
        fp = self._active["fp"]
        counts = self._layer_counts(fp)
        footprint_item = self._active["item"]
        for layer in fp.layers_used():
            # Ask the item, not the layer table: copper is drawn in its design
            # plane's colour, and the swatch has to agree with the canvas.
            row = LayerRow(layer, counts.get(layer, 0),
                           footprint_item.layer_hex(layer),
                           checked=footprint_item.is_layer_visible(layer))
            row.toggled.connect(self._on_layer_toggle)
            self.layer_layout.addWidget(row)

    @staticmethod
    def _layer_counts(fp):
        counts = {}
        for group in (fp.lines, fp.rects, fp.circles, fp.arcs, fp.polys, fp.texts):
            for item in group:
                counts[item.layer] = counts.get(item.layer, 0) + 1
        for pad in fp.pads:
            counts[pad.copper_layer] = counts.get(pad.copper_layer, 0) + 1
        return counts

    def _rebuild_pads(self):
        while self.pad_layout.count():
            item = self.pad_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if self._active is None:
            self.sec_pads.set_count("")
            return
        fp = self._active["fp"]
        self.sec_pads.set_count("(%d)" % len(fp.pads))
        for pad in fp.pads:
            btn = QtWidgets.QPushButton("%s   %s %.2f\u00d7%.2f"
                                        % (pad.number, pad.shape, pad.w, pad.h))
            btn.setObjectName("rowButton")
            btn.clicked.connect(lambda _=False, p=pad: self._on_pad_row(p))
            self.pad_layout.addWidget(btn)

    def _selected_track_models(self):
        return [item.track_model for item in self.canvas.selected_copper_items()
                if hasattr(item, "track_model")]

    def _selected_via_models(self):
        return [item.via_model for item in self.canvas.selected_copper_items()
                if hasattr(item, "via_model")]

    def _fill_copper_layer_combo(self, combo, selected_id):
        blocked = combo.blockSignals(True)
        combo.clear()
        for layer in self.stack.copper_top_down():
            combo.addItem(layer.name, layer.id)
        for i in range(combo.count()):
            if combo.itemData(i) == selected_id:
                combo.setCurrentIndex(i); break
        combo.blockSignals(blocked)

    def _show_copper_properties(self, items):
        self.sec_model_adjust.hide()
        self.prop_layer_row.hide(); self.prop_rotation_row.hide(); self.prop_hint.hide()
        self._set_fields([])
        tracks = [item.track_model for item in items if hasattr(item, "track_model")]
        vias = [item.via_model for item in items if hasattr(item, "via_model")]
        self.copper_props_group.show()
        if len(items) > 1:
            self.prop_kind.setText("%d routed copper objects" % len(items))
            self.copper_prop_kind.setText("Mixed selection")
            self.copper_prop_width_row.setVisible(False)
            self.copper_prop_dia_row.setVisible(False); self.copper_prop_hole_row.setVisible(False)
            self.copper_prop_start_row.setVisible(False); self.copper_prop_end_row.setVisible(False)
            self.copper_prop_net.setText("Delete removes selected segments; U selects the complete route.")
            return
        if tracks:
            track = tracks[0]
            self.prop_kind.setText("Track")
            self.copper_prop_kind.setText("Track on %s" % self.stack.by_id(track.layer_id).name)
            self.copper_prop_width_row.setVisible(True)
            self.copper_prop_dia_row.setVisible(False); self.copper_prop_hole_row.setVisible(False)
            self.copper_prop_start_row.setVisible(False); self.copper_prop_end_row.setVisible(False)
            self.copper_prop_width.blockSignals(True); self.copper_prop_width.setValue(track.width); self.copper_prop_width.blockSignals(False)
            length = sum(math.hypot(track.points[i][0]-track.points[i-1][0], track.points[i][1]-track.points[i-1][1])
                         for i in range(1, len(track.points)))
            self.copper_prop_net.setText("net %s · %.3f mm long" % (track.net or "—", length))
            return
        via = vias[0]
        self.prop_kind.setText("Via")
        self.copper_prop_kind.setText("Via")
        self.copper_prop_width_row.setVisible(False)
        self.copper_prop_dia_row.setVisible(True); self.copper_prop_hole_row.setVisible(True)
        self.copper_prop_start_row.setVisible(True); self.copper_prop_end_row.setVisible(True)
        self.copper_prop_dia.blockSignals(True); self.copper_prop_dia.setValue(via.diameter); self.copper_prop_dia.blockSignals(False)
        self.copper_prop_hole.blockSignals(True); self.copper_prop_hole.setMaximum(via.diameter); self.copper_prop_hole.setValue(via.hole_diameter); self.copper_prop_hole.blockSignals(False)
        self._fill_copper_layer_combo(self.copper_prop_start, via.start_layer_id)
        self._fill_copper_layer_combo(self.copper_prop_end, via.end_layer_id)
        self.copper_prop_net.setText("net %s · %s" %
            (via.net or "—", "solid cylinder" if via.hole_diameter <= 1e-9 else "annular via"))

    def _edit_selected_track_width(self):
        tracks = self._selected_track_models()
        if len(tracks) != 1: return
        value = float(self.copper_prop_width.value())
        if abs(tracks[0].width-value) < 1e-12: return
        before = self._copper_snapshot(); tracks[0].width = max(0.01, value)
        self._push_copper_history(before, "Change track width")
        self._apply_routed_copper(); self._refresh_ratsnest(); self._refresh_3d_if_visible(force=True, auto_align=False)

    def _edit_selected_via_diameter(self):
        vias = self._selected_via_models()
        if len(vias) != 1: return
        via = vias[0]; value = max(0.01, float(self.copper_prop_dia.value()))
        if abs(via.diameter-value) < 1e-12: return
        before = self._copper_snapshot(); via.diameter = value; via.hole_diameter = min(via.hole_diameter, value)
        self._push_copper_history(before, "Change via diameter")
        self._apply_routed_copper(); self._refresh_ratsnest(); self._refresh_3d_if_visible(force=True, auto_align=False); self._refresh_properties()

    def _edit_selected_via_hole(self):
        vias = self._selected_via_models()
        if len(vias) != 1: return
        via = vias[0]; value = max(0.0, min(float(self.copper_prop_hole.value()), via.diameter))
        if abs(via.hole_diameter-value) < 1e-12: return
        before = self._copper_snapshot(); via.hole_diameter = value
        self._push_copper_history(before, "Change via hole")
        self._apply_routed_copper(); self._refresh_3d_if_visible(force=True, auto_align=False); self._refresh_properties()

    def _edit_selected_via_start(self, _index):
        vias = self._selected_via_models()
        if len(vias) != 1: return
        uid = self.copper_prop_start.currentData()
        if uid is None or vias[0].start_layer_id == uid: return
        before = self._copper_snapshot(); vias[0].start_layer_id = uid
        self._push_copper_history(before, "Change via span")
        self._apply_routed_copper(); self._refresh_ratsnest(); self._refresh_3d_if_visible(force=True, auto_align=False); self._refresh_properties()

    def _edit_selected_via_end(self, _index):
        vias = self._selected_via_models()
        if len(vias) != 1: return
        uid = self.copper_prop_end.currentData()
        if uid is None or vias[0].end_layer_id == uid: return
        before = self._copper_snapshot(); vias[0].end_layer_id = uid
        self._push_copper_history(before, "Change via span")
        self._apply_routed_copper(); self._refresh_ratsnest(); self._refresh_3d_if_visible(force=True, auto_align=False); self._refresh_properties()


    # -- selection / properties ------------------------------------------
    def _property_targets(self):
        """The footprints the Properties panel currently describes.

        Derived from the scene on every call rather than cached: a stored list
        drifts out of step the moment anything changes the selection without
        going through the panel, and the panel would then edit footprints the
        user can no longer see selected.

        There is deliberately no fall back to the active footprint. Clicking
        empty canvas is a deselection, and it has to read as one.
        """
        entries = [self.entry_for_item(item)
                   for item in self.canvas.selected_footprints()]
        return [e for e in entries if e is not None]

    def _update_selection_status(self):
        label = getattr(self, "status_selection", None)
        if label is None:
            return
        pad = self.canvas.selected_pad()
        if pad is not None:
            count = 1
        else:
            count = (len(self.canvas.selected_footprints()) +
                     len(self.canvas.selected_copper_items()))
        if count <= 0:
            text = "No selection"
        elif count == 1:
            text = "1 object is selected"
        else:
            text = "%d objects are selected" % count
        label.setText(text)

    def _refresh_properties(self):
        """Redraw the Properties panel from the current selection.

        Everything that can change what Properties should say -- the scene
        selection, a pad being picked, a design-layer edit, a footprint being
        removed -- funnels through here, so the panel has exactly one place
        that decides what it shows.  Sidebar painting is suspended while rows
        change so sections below Properties are not repainted through several
        intermediate layout states.
        """
        surface = getattr(self, "sidebar_inner", None)
        resume_updates = bool(surface is not None and surface.updatesEnabled())
        if resume_updates:
            surface.setUpdatesEnabled(False)
        try:
            self._selected = self._property_targets()
            self.copper_props_group.hide()
            # "Load 3D model for selection…" acts on one footprint, so it is only
            # offered when the selection names one.
            if self.hosted:
                self.act_model_one.setEnabled(len(self._selected) == 1)
            copper_items = self.canvas.selected_copper_items()
            pad_selection = self.canvas.selected_pad()
            if copper_items:
                self._show_copper_properties(copper_items)
            elif pad_selection is not None:
                # Pads outrank footprints: picking a pad is a more specific act
                # than picking the part it sits on.
                self._show_pad_properties(pad_selection["pad"])
            elif len(self._selected) > 1:
                self._show_multi_properties(self._selected)
            elif self._selected:
                self._show_footprint_properties(self._selected[0])
            else:
                self._show_no_selection()
            self._update_footprint_rows()
            self._update_selection_status()
        finally:
            if resume_updates:
                surface.setUpdatesEnabled(True)
                surface.update()

    def _update_footprint_rows(self):
        """Highlight every selected footprint in the Footprints list.

        The rows mirror the selection exactly: highlighting only the active one
        left the list disagreeing with both the canvas and the Properties
        header, and keeping the active one lit after a deselection contradicted
        the "No selection" the panel had just switched to.
        """
        for entry in self._entries:
            button = entry.get("row")
            if button is not None:
                button.setChecked(entry in self._selected)

    def _set_fields(self, rows, expand=None):
        """Fill the Fields group, optionally forcing it open for this view.

        Sub-object selections (a pad, a graphic) have nothing in Basic
        Properties, so their fields are all there is to show and the group is
        forced open.  Footprint selections restore whatever the user last chose.
        """
        self.props.set_rows(rows)
        self.grp_fields.setVisible(bool(rows))
        if rows:
            self.grp_fields.set_expanded(
                self._fields_expanded if expand is None else expand)

    def _show_no_selection(self):
        self.sec_model_adjust.hide()
        self.copper_props_group.hide()
        self.prop_kind.setText("No selection")
        self.prop_kind.setToolTip("")
        self.prop_layer_row.hide()
        self.prop_rotation_row.hide()
        self._set_fields([])
        self.prop_hint.setText("select a footprint, pad or routed copper")
        self.prop_hint.show()

    def _footprint_fields(self, entry):
        """The Fields rows for one footprint, as ``(key, value)`` pairs.

        Rows that do not apply are omitted rather than shown empty, which also
        gives :meth:`_show_multi_properties` the schema it intersects over.
        """
        fp = entry["fp"]
        item = entry["item"]
        minx, miny, maxx, maxy = fp.bbox()

        rows = []
        if entry.get("ref"):
            rows.append(("reference", entry["ref"]))
        if entry.get("value"):
            rows.append(("value", entry["value"]))
        if entry.get("footprint_name"):
            rows.append(("footprint library", entry["footprint_name"]))
        reference = self._text_value(fp, "reference")
        if reference and not entry.get("ref"):
            rows.append(("reference", reference))
        rows.append(("footprint", fp.name))
        value = self._text_value(fp, "value")
        if value and not entry.get("value"):
            rows.append(("value", value))
        if fp.descr:
            rows.append(("description", fp.descr))
        if fp.tags:
            rows.append(("tags", fp.tags))
        if fp.attr:
            rows.append(("attributes", fp.attr))
        rows.append(("pads", "%d" % len(fp.pads)))
        rows.append(("graphics", "%d layers" % len(fp.layers_used())))
        rows.append(("size", "%.2f \u00d7 %.2f mm" % (maxx - minx, maxy - miny)))
        pos = item.pos()
        rows.append(("position", "%.3f, %.3f mm" % (pos.x(), pos.y())))
        if fp.model is not None and fp.model.path:
            rows.append(("3D model", os.path.basename(fp.model.path)))
        if fp.source_file:
            rows.append(("file", os.path.basename(fp.source_file)))
        return rows

    @staticmethod
    def _text_value(fp, kind):
        for txt in fp.texts:
            if txt.kind == kind and txt.text:
                return txt.text
        return ""

    def _show_footprint_properties(self, entry):
        layer = self.design_layer_of(entry)
        self.prop_kind.setText("Footprint")
        self.prop_kind.setToolTip(entry["fp"].name)
        self.prop_layer_row.show()
        self._fill_layer_combo(layer.id if layer is not None else None)
        self._set_rotation_control([entry])
        self.prop_hint.hide()
        self._set_fields(self._footprint_fields(entry))
        self._update_model_adjust_section(entry)

    def _show_multi_properties(self, entries):
        """Show what several selected footprints have in common.

        A row survives only when *every* selected footprint owns that property.
        When they all own it but disagree, the row stays and reads "mixed":
        dropping it would suggest the property does not apply, and picking one
        footprint's value would be a lie about the others.
        """
        self.sec_model_adjust.hide()
        count = len(entries)
        self.prop_kind.setText("Multiple selection")
        self.prop_kind.setToolTip("%d footprints" % count)

        layer_ids = set()
        for entry in entries:
            layer = self.design_layer_of(entry)
            layer_ids.add(layer.id if layer is not None else None)
        mixed_layer = len(layer_ids) > 1
        self.prop_layer_row.show()
        self._fill_layer_combo(None if mixed_layer else next(iter(layer_ids)),
                               mixed=mixed_layer)
        self._set_rotation_control(entries)
        self.prop_hint.hide()

        # Intersect the per-footprint schemas, keeping the first footprint's
        # row order so the panel does not reshuffle as the selection grows.
        field_sets = [self._footprint_fields(e) for e in entries]
        shared = set(key for key, _ in field_sets[0])
        for fields in field_sets[1:]:
            shared &= set(key for key, _ in fields)

        rows = []
        for key, first_value in field_sets[0]:
            if key not in shared:
                continue
            values = [dict(fields)[key] for fields in field_sets]
            if all(v == first_value for v in values):
                rows.append((key, first_value))
            else:
                rows.append((key, MIXED, True))
        self._set_fields(rows)

    def _show_pad_properties(self, pad):
        self.sec_model_adjust.hide()
        rows = [
            ("number", pad.number),
            ("net", getattr(pad, "net_name", None) or "—"),
            ("type", pad.pad_type),
            ("shape", pad.shape),
            ("size", "%.3f \u00d7 %.3f mm" % (pad.w, pad.h)),
            ("position", "%.3f, %.3f mm" % (pad.x, pad.y)),
        ]
        if abs(pad.rot) > 1e-9:
            rows.append(("rotation", "%g\u00b0" % pad.rot))
        if pad.shape == "roundrect":
            rows.append(("radius ratio", "%g" % pad.rratio))
        if pad.drill is not None:
            rows.append(("drill", ("oval %.3f \u00d7 %.3f mm"
                                   % (pad.drill.w, pad.drill.h)) if pad.drill.oval
                         else "%.3f mm" % pad.drill.w))
        rows.append(("layers", " ".join(pad.layers)))
        self.prop_kind.setText("Pad %s" % pad.number)
        self.prop_kind.setToolTip("")
        # A pad belongs to its footprint's copper, not to a board plane, so the
        # design-layer dropdown would be editing the wrong thing here.
        self.prop_layer_row.hide()
        self.prop_rotation_row.hide()
        self.prop_hint.hide()
        self._set_fields(rows, expand=True)

    # -- signal handlers -------------------------------------------------
    def _on_cursor(self, x, y):
        self.status_pos.setText("x %.3f   y %.3f mm" % (x, y))

    def _on_hover(self, info):
        if not info:
            if self._net_highlight_enabled and self._highlighted_nets:
                self.status_hover.setText("Highlighted net%s: %s" % (
                    "s" if len(self._highlighted_nets) != 1 else "",
                    ", ".join(sorted(self._highlighted_nets))))
            else:
                self.status_hover.setText("")
            return
        if info.get("kind") == "pad":
            pad = info["pad"]
            net = info.get("net") or getattr(pad, "net_name", None)
            suffix = (" \u00b7 net %s" % net) if net else ""
            self.status_hover.setText(
                "pad %s \u00b7 %s %s \u00b7 %.3f \u00d7 %.3f mm \u00b7 %s%s"
                % (pad.number, pad.pad_type, pad.shape, pad.w, pad.h,
                   " ".join(pad.layers), suffix))
        else:
            net = info.get("net")
            suffix = (" \u00b7 net %s" % net) if net else ""
            self.status_hover.setText("%s \u00b7 %s%s"
                                      % (info.get("detail", info.get("kind", "")),
                                         info.get("layer", ""), suffix))

    def _highlight_candidate_nets(self):
        """KiCad order: selected copper/pad first, otherwise copper under cursor."""
        nets = set()
        for item in self.canvas.selected_copper_items():
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            name = getattr(model, "net", None) if model is not None else None
            if name:
                nets.add(str(name))
        pad_sel = self.canvas.selected_pad()
        if pad_sel is not None:
            name = getattr(pad_sel.get("pad"), "net_name", None)
            if name:
                nets.add(str(name))
        if nets:
            return nets
        info = self.canvas.hover_info() or {}
        name = info.get("net")
        if not name and info.get("kind") == "pad":
            name = getattr(info.get("pad"), "net_name", None)
        return set([str(name)]) if name else set()

    def _apply_net_highlight(self):
        enabled = bool(self._net_highlight_enabled and self._highlighted_nets)
        self.canvas.set_net_highlight(self._highlighted_nets, enabled)
        self._refresh_ratsnest()
        self._on_hover(self.canvas.hover_info())

    def _highlight_net_hotkey(self):
        nets = self._highlight_candidate_nets()
        if not nets:
            return
        self._highlighted_nets = set(nets)
        self._net_highlight_enabled = True
        self._apply_net_highlight()

    def _clear_net_highlight(self):
        if not self._highlighted_nets and not self._net_highlight_enabled:
            return
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self._apply_net_highlight()

    def _toggle_net_highlight(self):
        if not self._highlighted_nets:
            self._highlight_net_hotkey()
            return
        self._net_highlight_enabled = not self._net_highlight_enabled
        self._apply_net_highlight()

    def _on_selection(self, items):
        """Track the scene's whole selection, not just a representative item."""
        entries = [e for e in (self.entry_for_item(i) for i in (items or []))
                   if e is not None]
        if not entries:
            self.view3d.set_selected(None)
            self._refresh_properties()
            return

        # The active entry stays put when it is still part of the selection, so
        # that extending a selection does not move the graphics/pads sections
        # onto a different footprint.
        primary = self._active if self._active in entries else entries[0]
        if primary is self._active:
            # Selection changed, but the footprint driving Graphics/Pads did
            # not.  Refresh only Properties; rebuilding those other sections
            # destroys rows and produces a visible left-panel flash.
            self._refresh_properties()
        else:
            self.set_active(primary)
        self.view3d.set_selected(primary.get("uid"))

    def _on_fields_toggled(self, expanded):
        self._fields_expanded = bool(expanded)

    def _on_zoom(self, px_per_mm):
        self.zoom_label.setText("%.0f px/mm" % px_per_mm)

    def _on_2d_renderer_changed(self, name):
        self.status_renderer.setText(str(name))

    def _on_grid_toggle(self, on):
        self.canvas.set_grid(visible=on)
        self.btn_grid.setChecked(on)

    def _on_grid_step(self, index):
        step = self.grid_combo.itemData(index)
        self.canvas.set_grid(step=step)
        self.status_grid.setText("grid %g mm" % step)

    def _on_layer_toggle(self, layer, visible):
        if self._active is not None:
            item = self._active["item"]
            was_selected = item.isSelected()
            item.set_layer_visible(layer, visible)
            self.canvas.refresh_extent()

            # Changing child visibility must not make the selection outline
            # disappear.  Normally Qt keeps the parent selected; restore it if
            # a binding/platform happens to drop selection during the geometry
            # update, and always repaint the selected item's full bound.
            if was_selected and not item.isSelected():
                self.canvas.select_footprint(item)
            elif was_selected:
                item.update(item.boundingRect())
                self.canvas.viewport().update()
            self._refresh_ratsnest()
            self._refresh_3d_if_visible(force=True)

    def _on_hidden_texts(self, visible):
        for entry in self._entries:
            entry["item"].set_hidden_texts_visible(visible)
        self.canvas.refresh_extent()

    def _on_pad_row(self, pad):
        """Clicking a row in the Pads list selects that pad on the canvas."""
        if self._active is None:
            return
        # The pad belongs to one footprint, so picking it narrows a wider
        # selection down to that part before highlighting the pad itself.
        self.canvas.select_footprint(self._active["item"])
        # highlight_pad() selects the pad, which comes back through
        # padSelectionChanged and refreshes Properties; doing it here as well
        # would just paint the same panel twice.
        self.canvas.highlight_pad(self._active["item"], pad.number)

    # -- canvas pad selection --------------------------------------------
    def _on_pad_selection(self, selection):
        """React to a pad being picked or released on the canvas.

        Only pads and footprints are selectable. Graphics and texts are drawn
        for reference and reading, and clicking one selects the footprint that
        owns it rather than the shape itself.
        """
        if selection is not None:
            entry = self.entry_for_item(selection["item"])
            if entry is not None and entry is not self._active:
                self.set_active(entry)
                return
        self._refresh_properties()

    def report_click(self, info):
        """Show properties for a pad hit, ignoring every other kind of item.

        Kept as a small public seam so the pads list and tests can drive the
        panel without synthesising mouse events.
        """
        if not info or info.get("kind") != "pad":
            return
        self._show_pad_properties(info["pad"])
