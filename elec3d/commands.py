# -*- coding: utf-8 -*-
"""FreeCAD GUI commands for the document-free Elec3D editor.

The normal import path deliberately does *not* create an ``App::Document``.
The current UI is fully document-free; the older explicit FreeCAD conversion
action is held while the 3D/STEP workflow is redesigned.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui

from .qt import (Qt, QtWidgets, exec_dialog, enum, NoWheelComboBox,
                 NoWheelSpinBox)

from . import PREF_PATH, get_options, icon_path as _icon
from .options import (MAX_SMALL_CROSSHAIR_PX, MIN_SMALL_CROSSHAIR_PX,
                      SIDEBAR_SECTION_LABELS, normalize_sidebar_order)

FILTER = "KiCad footprint (*.kicad_mod);;All files (*)"


def _last_dir():
    return App.ParamGet(PREF_PATH).GetString("LastDirectory", os.path.expanduser("~"))


def _remember_dir(path):
    App.ParamGet(PREF_PATH).SetString("LastDirectory", os.path.dirname(path))


# ---------------------------------------------------------------------------
class CommandImport(object):
    """Load one or more ``.kicad_mod`` files into the custom 2D viewer."""

    def GetResources(self):
        return {
            "Pixmap": _icon("Elec3D_Import.svg"),
            "MenuText": "Import footprint...",
            "ToolTip": "Open one or more KiCad .kicad_mod files in the custom 2D viewer",
            "Accel": "K, I",
        }

    def IsActive(self):
        return True

    def Activated(self, *_args):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            Gui.getMainWindow(), "Select KiCad footprint(s)", _last_dir(), FILTER)
        if not files:
            return
        _remember_dir(files[0])

        from . import viewer as viewer2d

        errors = viewer2d.import_paths(files, raise_window=True)
        if errors:
            text = "\n\n".join("%s\n%s" % (path, error) for path, error in errors)
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "KiCad footprint import",
                "Some footprints could not be opened:\n\n%s" % text)


# ---------------------------------------------------------------------------
class CommandImportNetlist(object):
    """Open the four-step KiCad netlist import wizard."""

    def GetResources(self):
        return {
            "Pixmap": _icon("Elec3D_Import.svg"),
            "MenuText": "Import KiCad netlist...",
            "ToolTip": "Import a KiCad .net file, resolve footprints/models, and build the ratsnest",
            "Accel": "K, N",
        }

    def IsActive(self):
        return True

    def Activated(self, *_args):
        from . import viewer as viewer2d
        viewer2d.show_viewer().open_netlist_wizard()


# ---------------------------------------------------------------------------
class CommandReload(object):
    """Re-read the source file of the active custom-viewer footprint."""

    def GetResources(self):
        return {
            "Pixmap": _icon("Elec3D_Reload.svg"),
            "MenuText": "Reload selected footprint",
            "ToolTip": "Reload the selected footprint from its source file",
        }

    def IsActive(self):
        try:
            from . import viewer as viewer2d

            viewer = viewer2d.get_viewer(create=False)
            entry = viewer.active_entry() if viewer is not None else None
            return bool(entry and entry["fp"].source_file)
        except Exception:
            return False

    def Activated(self, *_args):
        from . import viewer as viewer2d

        try:
            viewer2d.reload_active()
        except Exception as exc:
            App.Console.PrintError("KiCad footprint reload failed: %s\n" % exc)
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(), "KiCad footprint reload", str(exc))


# ---------------------------------------------------------------------------
class CommandOptions(object):
    """Viewer preferences."""

    def GetResources(self):
        return {
            "Pixmap": _icon("Elec3D_Options.svg"),
            "MenuText": "Preferences...",
            "ToolTip": "Elec3D preferences",
        }

    def IsActive(self):
        return True

    def Activated(self, *_args):
        params = App.ParamGet(PREF_PATH)
        opts = get_options()

        dlg = QtWidgets.QDialog(Gui.getMainWindow())
        dlg.setWindowTitle("Elec3D Preferences")
        dlg.resize(430, 520)
        outer = QtWidgets.QVBoxLayout(dlg)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        host_group = QtWidgets.QGroupBox("Viewer")
        host_form = QtWidgets.QFormLayout(host_group)
        mode = NoWheelComboBox()
        mode.addItem("Tab in the main window", "tab")
        mode.addItem("Separate window", "window")
        mode.setCurrentIndex(0 if params.GetString("ViewerMode", "tab") == "tab" else 1)
        mode.setToolTip("Choose whether the custom viewer is an MDI tab or a top-level window; applied immediately")
        host_form.addRow("Viewer opens as", mode)
        outer.addWidget(host_group)

        view_group = QtWidgets.QGroupBox("2D view")
        view_form = QtWidgets.QFormLayout(view_group)
        crosshair = NoWheelComboBox()
        crosshair.addItem("Small crosshair", "small")
        crosshair.addItem("Full crosshair", "full")
        crosshair.setCurrentIndex(0 if opts.crosshair_mode == "small" else 1)
        crosshair.setToolTip("Small is the default compact cursor; Full spans the complete 2D canvas")
        view_form.addRow("Cursor crosshair", crosshair)

        crosshair_size = NoWheelSpinBox()
        crosshair_size.setRange(MIN_SMALL_CROSSHAIR_PX, MAX_SMALL_CROSSHAIR_PX)
        crosshair_size.setValue(opts.crosshair_size_px)
        crosshair_size.setSuffix(" px")
        crosshair_size.setToolTip(
            "Total screen-space length of each line in the Small crosshair")
        view_form.addRow("Small crosshair length", crosshair_size)
        crosshair_size.setEnabled(crosshair.currentData() == "small")
        crosshair.currentIndexChanged.connect(
            lambda _index: crosshair_size.setEnabled(crosshair.currentData() == "small"))
        outer.addWidget(view_group)

        routing_group = QtWidgets.QGroupBox("Interactive routing")
        routing_form = QtWidgets.QVBoxLayout(routing_group)
        keep45 = QtWidgets.QCheckBox("Keep 45° angles when dragging copper")
        keep45.setChecked(bool(opts.drag_keep_45))
        keep45.setToolTip(
            "Dragging a track segment, corner or via keeps H/V/45 geometry, "
            "as KiCad does. Hold Alt during a drag for a free angle.")
        routing_form.addWidget(keep45)

        walkaround = QtWidgets.QCheckBox("Walk around obstacles while routing")
        walkaround.setChecked(bool(opts.route_walkaround))
        walkaround.setToolTip(
            "Route around blocking copper instead of stopping at it. "
            "When no legal detour exists the route still stops at the last "
            "legal point.")
        routing_form.addWidget(walkaround)
        outer.addWidget(routing_group)

        sidebar_group = QtWidgets.QGroupBox("Sidebar")
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar_group)
        sidebar_hint = QtWidgets.QLabel(
            "Set the section order. Checked sections start expanded; "
            "unchecked sections start collapsed.")
        sidebar_hint.setWordWrap(True)
        sidebar_layout.addWidget(sidebar_hint)

        section_list = QtWidgets.QListWidget()
        role = enum(Qt, "ItemDataRole", "UserRole")
        checked = enum(Qt, "CheckState", "Checked")
        unchecked = enum(Qt, "CheckState", "Unchecked")
        for key in normalize_sidebar_order(opts.sidebar_order):
            item = QtWidgets.QListWidgetItem(SIDEBAR_SECTION_LABELS[key])
            item.setData(role, key)
            item.setCheckState(checked if opts.sidebar_expanded.get(key, True) else unchecked)
            section_list.addItem(item)
        if section_list.count():
            section_list.setCurrentRow(0)
        sidebar_layout.addWidget(section_list, 1)

        order_buttons = QtWidgets.QHBoxLayout()
        btn_up = QtWidgets.QPushButton("Move up")
        btn_down = QtWidgets.QPushButton("Move down")
        order_buttons.addWidget(btn_up)
        order_buttons.addWidget(btn_down)
        order_buttons.addStretch(1)
        sidebar_layout.addLayout(order_buttons)

        def move_section(offset):
            row = section_list.currentRow()
            target = row + offset
            if row < 0 or target < 0 or target >= section_list.count():
                return
            item = section_list.takeItem(row)
            section_list.insertItem(target, item)
            section_list.setCurrentRow(target)

        btn_up.clicked.connect(lambda: move_section(-1))
        btn_down.clicked.connect(lambda: move_section(1))
        outer.addWidget(sidebar_group, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        outer.addWidget(buttons)

        if exec_dialog(dlg):
            params.SetString("ViewerMode", mode.currentData())
            params.SetString("CrosshairMode", crosshair.currentData())
            params.SetInt("CrosshairSizePx", crosshair_size.value())
            params.SetBool("DragKeep45", bool(keep45.isChecked()))
            params.SetBool("RouteWalkaround", bool(walkaround.isChecked()))

            order = []
            expanded = {}
            for row in range(section_list.count()):
                item = section_list.item(row)
                key = str(item.data(role))
                if key not in SIDEBAR_SECTION_LABELS:
                    continue
                order.append(key)
                expanded[key] = (item.checkState() == checked)
            order = normalize_sidebar_order(order)
            params.SetString("SidebarOrder", ",".join(order))
            for key in order:
                params.SetBool("SidebarExpanded_" + key, bool(expanded.get(key, True)))

            from . import viewer as viewer2d
            # Apply hosting, sidebar order/default states and crosshair preferences
            # after the modal dialog/click stack has fully unwound.
            viewer2d.request_apply_host_preferences()


# ---------------------------------------------------------------------------
COMMANDS = (
    ("Elec3D_Import", CommandImport()),
    ("Elec3D_ImportNetlist", CommandImportNetlist()),
    ("Elec3D_Reload", CommandReload()),
    ("Elec3D_Options", CommandOptions()),
)


def register():
    for name, cmd in COMMANDS:
        Gui.addCommand(name, cmd)
    return [name for name, _ in COMMANDS]
