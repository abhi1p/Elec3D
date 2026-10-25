# -*- coding: utf-8 -*-
"""Host the custom, document-free KiCad 2D viewer inside FreeCAD.

Normal footprint imports live entirely in the Qt viewer.  No ``App::Document``
or ``DocumentObject`` is created on import, reload, selection, movement, or
workbench activation.  FreeCAD geometry is created only by the explicit
**Export STEP** / **Export to FreeCAD** workflows; normal editing remains
document-free.

Lifecycle
---------
Once opened, the editor runs beside FreeCAD and never reacts to the rest of
FreeCAD's workflow: switching workbenches or documents neither closes, hides
nor re-shows it.  Closing its tab or window is a real close: unsaved changes
prompt Save / Discard / Cancel, and on Save/Discard the widget, its timers,
application-wide hooks and the whole in-memory design are released.  Selecting
the Elec3D workbench (or *Elec3D viewer* in the menu) opens a fresh editor.
"""

import os

import FreeCAD as App

from . import PREF_PATH, icon_path
from . import wbtrack
from .panel2d import Viewer2D
from .qt import Qt, QtCore, QtGui, QtWidgets, enum

TITLE = "Elec3D"

_viewer = None
_subwindow = None
_hidden_panels = []
_mdi_hooked = False
_quit_hooked = False
_closing = False
_rehost_pending = False
_window_geometry = None
_mdi_return_subwindow = None
_mdi_return_was_maximized = False
_main_close_filter = None
# Tab mode follows the editor tab with the workbench: entering the Elec3D tab
# activates the Elec3D workbench, leaving it restores the workbench that was
# active before (tracked by :mod:`wbtrack`).
ELEC3D_WORKBENCH = wbtrack.ELEC3D_WORKBENCH

SIZE_IGNORED = enum(QtWidgets.QSizePolicy, "Policy", "Ignored")
WINDOW_MINIMIZED = enum(Qt, "WindowState", "WindowMinimized")



# ---------------------------------------------------------------------------
# host-window helpers
# ---------------------------------------------------------------------------
def _alive(widget):
    """True if the C++ side of *widget* still exists."""
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


def _main_window():
    try:
        import FreeCADGui as Gui

        return Gui.getMainWindow()
    except Exception:
        return None


def _mdi_area():
    main = _main_window()
    return main.findChild(QtWidgets.QMdiArea) if main is not None else None




def _remember_mdi_return_target(mdi):
    """Remember the FreeCAD document that should regain the MDI area.

    FreeCAD commonly runs QMdiArea in sub-window mode while presenting each
    maximized child as a bottom document tab.  If our maximized custom child is
    removed without restoring the previous child, Qt leaves that document in
    its old *normal* geometry (a small floating rectangle).  Keep the previous
    active document and whether it was maximized so Window <- Tab switching
    returns FreeCAD to exactly the document presentation it had before.
    """
    global _mdi_return_subwindow, _mdi_return_was_maximized
    if mdi is None:
        return
    try:
        current = mdi.activeSubWindow()
    except RuntimeError:
        current = None
    if current is None or current is _subwindow or not _alive(current):
        return
    _mdi_return_subwindow = current
    try:
        _mdi_return_was_maximized = bool(current.isMaximized())
    except RuntimeError:
        _mdi_return_was_maximized = False


def _restore_mdi_return_target(mdi, removed_was_maximized=False):
    """Restore the FreeCAD document presentation after our MDI tab is removed."""
    global _mdi_return_subwindow, _mdi_return_was_maximized
    if mdi is None:
        _mdi_return_subwindow = None
        _mdi_return_was_maximized = False
        return

    target = _mdi_return_subwindow if _alive(_mdi_return_subwindow) else None
    if target is None:
        try:
            target = mdi.activeSubWindow()
        except RuntimeError:
            target = None
    if target is None:
        try:
            remaining = mdi.subWindowList()
            target = remaining[-1] if remaining else None
        except RuntimeError:
            target = None

    if _alive(target):
        try:
            target.show()
            mdi.setActiveSubWindow(target)
            # A maximized KiCad viewer replacing a maximized FreeCAD document
            # must hand that maximized state back.  Qt otherwise restores the
            # old child to its small pre-maximize rectangle, which looks like
            # FreeCAD's document tabs have broken into floating windows.
            if _mdi_return_was_maximized or removed_was_maximized:
                target.showMaximized()
        except RuntimeError:
            pass

    _mdi_return_subwindow = None
    _mdi_return_was_maximized = False


def _params():
    return App.ParamGet(PREF_PATH)


def _window_mode():
    return _params().GetString("ViewerMode", "tab")


# ---------------------------------------------------------------------------
# application close guard
# ---------------------------------------------------------------------------
class _MainWindowCloseFilter(QtCore.QObject):
    """Give the document-free editor a Save / Discard / Cancel close guard."""

    def eventFilter(self, watched, event):
        close_type = getattr(QtCore.QEvent, "Close", None)
        if close_type is None and hasattr(QtCore.QEvent, "Type"):
            close_type = getattr(QtCore.QEvent.Type, "Close", None)
        if event.type() == close_type:
            viewer = get_viewer(create=False)
            if _alive(viewer):
                try:
                    if not viewer.confirm_save_or_discard("closing FreeCAD"):
                        event.ignore()
                        return True
                except RuntimeError:
                    pass
        return False


def _hook_main_close_guard():
    global _main_close_filter
    main = _main_window()
    if main is None:
        return
    if _main_close_filter is None:
        _main_close_filter = _MainWindowCloseFilter(main)
    try:
        main.installEventFilter(_main_close_filter)
    except (AttributeError, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# side panels
# ---------------------------------------------------------------------------
def _panel_widgets():
    """Dock widgets plus FreeCAD's overlay panels, which can auto-reveal."""
    main = _main_window()
    if main is None:
        return []
    found = list(main.findChildren(QtWidgets.QDockWidget))
    for widget in main.findChildren(QtWidgets.QWidget):
        try:
            name = widget.objectName() or ""
        except RuntimeError:
            continue
        if name.startswith("Overlay") and widget not in found:
            found.append(widget)
    return found


def _on_about_to_quit():
    # FreeCAD's own close already went through the Save/Discard guard.  Tear
    # the editor down so a parentless Separate Window cannot outlive FreeCAD.
    restore_panels()
    close_viewer()


def _hook_quit():
    global _quit_hooked
    if _quit_hooked:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        try:
            app.aboutToQuit.connect(_on_about_to_quit)
            _quit_hooked = True
        except Exception:
            pass


def hide_panels():
    """Hide Model/Tasks while the custom MDI tab is active."""
    global _hidden_panels
    if _hidden_panels:
        return
    _hook_quit()
    for widget in _panel_widgets():
        try:
            if widget.isVisible():
                widget.hide()
                _hidden_panels.append(widget)
        except RuntimeError:
            continue


def restore_panels():
    """Restore whatever :func:`hide_panels` hid."""
    global _hidden_panels
    for widget in _hidden_panels:
        try:
            widget.show()
        except RuntimeError:
            continue
    _hidden_panels = []


def _active_workbench_name():
    try:
        import FreeCADGui as Gui

        bench = Gui.activeWorkbench()
        if bench is None:
            return None
        if type(bench).__name__ == ELEC3D_WORKBENCH:
            return ELEC3D_WORKBENCH
        return str(bench.name())
    except Exception:
        return None


def _elec3d_workbench_active():
    """``True`` while Elec3D is FreeCAD's active workbench (or outside FreeCAD)."""
    name = _active_workbench_name()
    return name is None or name == ELEC3D_WORKBENCH


_last_document_subwindow = None
_switching_workbench = False
_sync_scheduled = False
_choice_scheduled = False
SETTLE_MS = 40          # let Qt/FreeCAD focus and activation churn settle first


def _switch_workbench(name):
    """Activate *name*, marking the switch as ours so listeners ignore it."""
    global _switching_workbench
    if not name or _active_workbench_name() == name:
        return
    _switching_workbench = True
    try:
        import FreeCADGui as Gui

        App.Console.PrintLog("Elec3D: switching workbench to %s\n" % name)
        Gui.activateWorkbench(name)
    except Exception as exc:
        App.Console.PrintWarning("Elec3D: could not switch to workbench %s: %s\n"
                                 % (name, exc))
    finally:
        # FreeCAD may report the activation slightly after it returns.
        QtCore.QTimer.singleShot(SETTLE_MS * 3, _end_switch)


def _end_switch():
    global _switching_workbench
    _switching_workbench = False


def _tab_mode_open():
    return _alive(_subwindow) and isinstance(_subwindow, QtWidgets.QMdiSubWindow)


def _active_tab_area():
    area = _subwindow.mdiArea() if _tab_mode_open() else None
    return area or _mdi_area()


def _tab_is_active():
    if not _tab_mode_open():
        return False
    area = _active_tab_area()
    try:
        return area is not None and area.activeSubWindow() is _subwindow
    except RuntimeError:
        return False


def _schedule_workbench_sync():
    global _sync_scheduled
    if not _sync_scheduled:
        _sync_scheduled = True
        QtCore.QTimer.singleShot(SETTLE_MS, _sync_workbench_with_tab)


def _sync_workbench_with_tab():
    """Pair the workbench with whichever MDI tab has *settled* in front.

    Decisions are taken from the final active sub-window rather than from each
    activation signal: FreeCAD/Qt can emit transient activations while focus
    moves (e.g. during a toolbar rebuild), and reacting to those flipped the
    workbench back and forth.
    """
    global _sync_scheduled, _last_document_subwindow
    _sync_scheduled = False
    if not _tab_mode_open():
        return
    if _switching_workbench:
        _schedule_workbench_sync()      # re-check once our own switch is done
        return
    area = _active_tab_area()
    try:
        active = area.activeSubWindow() if area is not None else None
    except RuntimeError:
        return
    current = _active_workbench_name()
    if current is None:
        return
    if active is _subwindow:
        if current != ELEC3D_WORKBENCH:
            wbtrack.remember(current)
            _switch_workbench(ELEC3D_WORKBENCH)
        else:
            hide_panels()
    elif active is not None:
        _last_document_subwindow = active
        restore_panels()
        previous = wbtrack.last_other()
        if current == ELEC3D_WORKBENCH and previous:
            _switch_workbench(previous)


def _leave_elec3d_tab():
    """The Elec3D tab is going away: hand back the previous workbench."""
    previous = wbtrack.last_other()
    if previous and _active_workbench_name() == ELEC3D_WORKBENCH:
        QtCore.QTimer.singleShot(0, lambda: _switch_workbench(previous))


def _on_workbench_activated(name):
    """The user chose another workbench while the Elec3D tab is in front.

    A FreeCAD workbench cannot act on the Elec3D tab, so the last FreeCAD
    document is brought back to the front instead (tab mode only).
    """
    global _choice_scheduled
    name = str(name or "")
    if (not name or name == ELEC3D_WORKBENCH or _switching_workbench
            or _choice_scheduled or not _tab_is_active()):
        return
    _choice_scheduled = True

    def show_document():
        global _choice_scheduled
        _choice_scheduled = False
        if _switching_workbench or not _tab_is_active():
            return
        if _active_workbench_name() in (None, ELEC3D_WORKBENCH):
            return
        target = _last_document_subwindow
        area = _active_tab_area()
        if not (_alive(target) and area is not None):
            return
        try:
            if target in area.subWindowList():
                area.setActiveSubWindow(target)
        except RuntimeError:
            pass

    QtCore.QTimer.singleShot(SETTLE_MS, show_document)


def _on_subwindow_activated(sub):
    global _last_document_subwindow
    if not _tab_mode_open():
        return
    if sub is not None and sub is not _subwindow:
        _last_document_subwindow = sub
        restore_panels()
    elif sub is _subwindow and _elec3d_workbench_active():
        hide_panels()
    _schedule_workbench_sync()


def _hook_mdi():
    global _mdi_hooked
    mdi = _mdi_area()
    if mdi is not None and not _mdi_hooked:
        try:
            mdi.subWindowActivated.connect(_on_subwindow_activated)
            _mdi_hooked = True
        except Exception:
            pass


# ---------------------------------------------------------------------------
# sub-window lifetime
# ---------------------------------------------------------------------------
class ViewerSubWindow(QtWidgets.QMdiSubWindow):
    """MDI host whose close ends the editor session (after Save/Discard)."""

    def closeEvent(self, event):
        if _subwindow is self and _alive(_viewer) and not _closing:
            try:
                allowed = _viewer.confirm_save_or_discard("closing Elec3D")
            except RuntimeError:
                allowed = True
            if not allowed:
                event.ignore()
                return
        event.accept()
        if _subwindow is self:
            _destroy_viewer()
            return
        # A stray/retired shell: just remove it.
        area = self.mdiArea()
        if area is not None:
            try:
                area.removeSubWindow(self)
            except Exception:
                pass
        self.deleteLater()


def _on_window_close_requested(viewer):
    """Close handler for the Separate Window host (see ``Viewer2D.closeEvent``)."""
    if viewer is not _viewer or _closing:
        return True
    try:
        if not viewer.confirm_save_or_discard("closing Elec3D"):
            return False
    except RuntimeError:
        return True
    # Tear down after Qt has finished delivering this close event.
    QtCore.QTimer.singleShot(0, lambda v=viewer: _destroy_viewer(expected=v))
    return True


def _destroy_viewer(expected=None):
    """Remove the host and delete the editor and everything it holds.

    No prompt is shown here; callers ask Save/Discard first when needed.
    """
    global _viewer, _subwindow, _window_geometry
    viewer, host = _viewer, _subwindow
    if expected is not None and viewer is not expected:
        return
    _viewer = None
    _subwindow = None
    restore_panels()
    if isinstance(host, QtWidgets.QMdiSubWindow) and _alive(host):
        _leave_elec3d_tab()

    if _alive(viewer):
        try:
            if viewer.isWindow():
                _window_geometry = viewer.saveGeometry()
        except RuntimeError:
            pass
        _prepare_viewer_for_rehost(viewer)
        try:
            viewer.close_handler = None
            viewer.shutdown()
        except (AttributeError, RuntimeError):
            pass

    if _alive(host) and isinstance(host, QtWidgets.QMdiSubWindow):
        area = host.mdiArea()
        try:
            removed_was_maximized = bool(host.isMaximized())
        except RuntimeError:
            removed_was_maximized = False
        try:
            host.hide()
        except RuntimeError:
            pass
        if area is not None:
            try:
                area.removeSubWindow(host)
            except Exception:
                pass
            _restore_mdi_return_target(area, removed_was_maximized)
        try:
            host.deleteLater()          # owns and deletes the Viewer2D child
        except RuntimeError:
            pass
    elif _alive(viewer):
        try:
            viewer.hide()
            viewer.deleteLater()
        except RuntimeError:
            pass



# ---------------------------------------------------------------------------
# public viewer API
# ---------------------------------------------------------------------------
def get_viewer(create=True, theme=None):
    """Return the single custom viewer instance, creating it on demand."""
    global _viewer
    if not _alive(_viewer):
        _viewer = None
    if _viewer is None and create:
        from . import theme as thememod

        # Hosted viewers follow FreeCAD's current application palette.  The
        # optional *theme* argument remains useful to callers during startup,
        # but no persistent add-on theme preference is used.
        _viewer = Viewer2D(theme=theme or thememod.application_theme(), hosted=True)
        _viewer.hostActionRequested.connect(_on_host_action)
        _viewer.close_handler = _on_window_close_requested
        _hook_main_close_guard()
        _hook_quit()
        wbtrack.add_listener(_on_workbench_activated)
    return _viewer


def _current_host_mode():
    if not _alive(_subwindow):
        return None
    if isinstance(_subwindow, QtWidgets.QMdiSubWindow):
        return "tab"
    return "window"



def _snapshot_viewer_state(viewer):
    """Capture the document-free session without keeping Qt scene objects.

    A Viewer2D that has been a native top-level window is not safely reusable
    as an MDI child on every Windows/Qt build used by FreeCAD.  In particular,
    native child handles can continue routing mouse events through the retired
    host after Window -> Tab -> Window -> Tab.  Host changes therefore recreate
    the *Qt widgets* while preserving the parsed footprint models and editor
    state in ordinary Python data.
    """
    if not _alive(viewer):
        return None

    canvas = viewer.canvas
    # A host rebuild must snapshot only committed editor geometry.  If a mouse
    # gesture is interrupted by the host/options action, roll the temporary
    # QGraphicsItem transforms back before copying positions/history.
    try:
        if getattr(viewer, "_move_session", None) is not None:
            viewer.cancel_move_selected()
        if canvas.has_active_interaction():
            canvas.cancel_interaction(restore=True)
            viewer._refresh_ratsnest()
    except (AttributeError, RuntimeError):
        pass
    entries = []
    selected_index = None
    active_index = None
    for index, entry in enumerate(viewer._entries):
        item = entry["item"]
        if entry is viewer._active:
            active_index = index
        if item.isSelected():
            selected_index = index
        entries.append({
            "fp": entry["fp"],
            "uid": entry.get("uid"),
            "pos": QtCore.QPointF(item.pos()),
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
        })

    # A multi-selection has to survive a host rebuild in full; keeping only one
    # index would silently drop the rest of the user's selection.
    selected_indexes = [i for i, entry in enumerate(viewer._entries)
                        if entry["item"].isSelected()]

    center = canvas.mapToScene(canvas.viewport().rect().center())
    selected_copper_uids = []
    for item in canvas.selected_copper_items():
        model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
        if model is not None:
            selected_copper_uids.append(model.uid)
    return {
        "entries": entries,
        # The live BoardSolid object (mesh + transient exact shape) is carried
        # across the rebuild as-is; nothing about it depends on the widget.
        "board_solid": getattr(viewer, "board_solid", None),
        "selected_copper_uids": selected_copper_uids,
        "routing": viewer.routing_snapshot(),
        "active_index": active_index,
        "selected_index": selected_index,
        "selected_indexes": selected_indexes,
        "stack": viewer.stack.snapshot(),
        "design_rules": viewer.design_rules.snapshot(),
        "fields_expanded": bool(viewer._fields_expanded),
        "grid_visible": bool(canvas.grid_visible),
        "grid_step": float(canvas.grid_step),
        "snap_enabled": bool(canvas.snap_enabled),
        "crosshair": bool(canvas.crosshair),
        "zoom": float(canvas.px_per_mm()),
        "center": QtCore.QPointF(center),
        "auto_fit": bool(canvas._auto_fit),
        "history": viewer.history_snapshot(),
        "netlist": viewer.netlist.snapshot() if viewer.netlist is not None else None,
        "models_folder": viewer.models_folder,
        "project_file": getattr(viewer, "_project_file", ""),
        "design_file": getattr(viewer, "_design_file", ""),
        "design_path_variables": dict(getattr(viewer, "_design_path_variables", {}) or {}),
        "design_resource_file": getattr(viewer, "_design_resource_file", ""),
        "freecad_export_document": getattr(viewer, "_freecad_export_document", ""),
        "saved_content_signature": getattr(viewer, "_saved_content_signature", None),
        # Re-evaluate synchronously.  The normal dirty-state poll runs every
        # 200 ms, so a host switch immediately after an edit must not snapshot
        # a stale ``_modified`` flag and accidentally bless unsaved content as
        # the new clean baseline.
        "modified": bool(viewer.is_modified()),
        "export_options": dict(getattr(viewer, "_export_options", {}) or {}),
        "show_ratsnest": bool(viewer.show_ratsnest),
        "highlighted_nets": sorted(getattr(viewer, "_highlighted_nets", set())),
        "net_highlight_enabled": bool(getattr(viewer, "_net_highlight_enabled", False)),
        "view_mode": viewer.view_mode,
        "import_directory": viewer._import_directory,
        "sections": {
            key: bool(section.header.isChecked())
            for key, section in getattr(viewer, "_sidebar_sections", {}).items()
        },
    }


def _restore_selection(viewer, state):
    """Reselect the footprints that were selected before the host rebuild."""
    indexes = state.get("selected_indexes")
    if indexes is None:
        # Snapshots taken by an older build only recorded a single index.
        single = state.get("selected_index")
        indexes = [] if single is None else [single]
    items = [viewer._entries[i]["item"] for i in indexes
             if 0 <= i < len(viewer._entries)]
    viewer.canvas.select_footprints(items)


def _restore_viewer_state(viewer, state, restore_view=False):
    """Restore a snapshot created by :func:`_snapshot_viewer_state`."""
    if not state or not _alive(viewer):
        return

    canvas = viewer.canvas
    if restore_view:
        # View transforms/scroll ranges depend on the final host geometry, so
        # this part is applied in a queued callback after layout completes.
        canvas._auto_fit = False
        canvas.set_zoom(state["zoom"])
        canvas.centerOn(state["center"])
        canvas._auto_fit = state["auto_fit"]
        canvas._refresh_cursor_from_global()
        return

    # Restore the global editor controls first so newly-created scene items use
    # the correct snap policy immediately.
    canvas.set_grid(visible=state["grid_visible"],
                    step=state["grid_step"],
                    snap=state["snap_enabled"])
    canvas.crosshair = state["crosshair"]

    # Keep both copies of the grid UI in sync without relying on signal order.
    viewer.chk_grid.blockSignals(True)
    viewer.btn_grid.blockSignals(True)
    viewer.chk_snap.blockSignals(True)
    viewer.chk_grid.setChecked(state["grid_visible"])
    viewer.btn_grid.setChecked(state["grid_visible"])
    viewer.chk_snap.setChecked(state["snap_enabled"])
    viewer.chk_grid.blockSignals(False)
    viewer.btn_grid.blockSignals(False)
    viewer.chk_snap.blockSignals(False)
    for index in range(viewer.grid_combo.count()):
        try:
            if abs(float(viewer.grid_combo.itemData(index)) - state["grid_step"]) < 1e-12:
                viewer.grid_combo.blockSignals(True)
                viewer.grid_combo.setCurrentIndex(index)
                viewer.grid_combo.blockSignals(False)
                break
        except (TypeError, ValueError):
            pass
    viewer.status_grid.setText("grid %g mm" % state["grid_step"])

    # The stack has to come back before the footprints do: each one restores a
    # design-layer id, and those ids only mean anything once the planes exist.
    viewer.stack.restore(state.get("stack"))
    viewer.design_rules.restore(state.get("design_rules"))
    viewer._fields_expanded = bool(state.get("fields_expanded", False))
    from .netlist import KiCadNetlist
    viewer.netlist = KiCadNetlist.restore(state.get("netlist")) if state.get("netlist") else None
    viewer.models_folder = state.get("models_folder", "") or ""
    viewer._project_file = state.get("project_file", "") or ""
    viewer._design_file = state.get("design_file", "") or ""
    viewer._design_path_variables = dict(state.get("design_path_variables", {}) or {})
    viewer._design_resource_file = state.get("design_resource_file", "") or ""
    viewer._freecad_export_document = state.get("freecad_export_document", "") or ""
    viewer._saved_content_signature = state.get("saved_content_signature")
    viewer._modified = bool(state.get("modified", False))
    viewer._export_options.update(dict(state.get("export_options", {}) or {}))
    viewer._refresh_window_title()
    viewer.show_ratsnest = bool(state.get("show_ratsnest", True))
    viewer._highlighted_nets = set(str(n) for n in state.get("highlighted_nets", []) if n)
    viewer._net_highlight_enabled = bool(state.get("net_highlight_enabled", False)
                                         and viewer._highlighted_nets)
    viewer._import_directory = state.get("import_directory", viewer._import_directory)
    viewer.view3d.set_models_folder(viewer.models_folder)
    viewer.chk_ratsnest.blockSignals(True)
    viewer.chk_ratsnest.setChecked(viewer.show_ratsnest)
    viewer.chk_ratsnest.blockSignals(False)

    viewer.board_solid = state.get("board_solid")

    for saved in state["entries"]:
        item = viewer.add_footprint(
            saved["fp"], select=False, uid=saved.get("uid"),
            ref=saved.get("ref", ""), value=saved.get("value", ""),
            footprint_name=saved.get("footprint_name", ""),
            placeholder=saved.get("placeholder", False), fit=False)
        item.setPos(saved["pos"])
        item.setRotation(float(saved.get("rotation", 0.0)))
        restored_entry = viewer._entries[-1]
        restored_entry["model_adjust"] = dict(saved.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0})
        restored_entry["model_adjust_mode"] = saved.get("model_adjust_mode", "auto")
        design_layer_id = saved.get("design_layer_id")
        if design_layer_id is not None and viewer.stack.has(design_layer_id):
            item.design_layer_id = design_layer_id
        for layer, visible in saved["layers"].items():
            if layer in item.layer_items:
                item.set_layer_visible(layer, visible)
        item.set_hidden_texts_visible(saved["hidden_texts"])
    viewer._apply_design_layers()
    viewer._rebuild_stack()
    viewer._rebuild_stackup()
    viewer.restore_routing(state.get("routing"))
    viewer.canvas.set_net_highlight(viewer._highlighted_nets, viewer._net_highlight_enabled)
    viewer._refresh_ratsnest()

    # The hidden-text checkbox is a viewer-wide control.  Current releases set
    # it on all entries together, so use the first saved item as the UI state.
    hidden = bool(state["entries"] and state["entries"][0]["hidden_texts"])
    viewer.chk_hidden.blockSignals(True)
    viewer.chk_hidden.setChecked(hidden)
    viewer.chk_hidden.blockSignals(False)

    active_index = state.get("active_index")
    if active_index is not None and 0 <= active_index < len(viewer._entries):
        viewer.set_active(viewer._entries[active_index])
    elif viewer._entries:
        viewer.set_active(viewer._entries[0])

    _restore_selection(viewer, state)
    copper_uids = set(str(uid) for uid in state.get("selected_copper_uids", []))
    if copper_uids:
        items = []
        for item in viewer.canvas.track_items + viewer.canvas.via_items:
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None and str(model.uid) in copper_uids:
                items.append(item)
        viewer.canvas.select_copper_items(items)
    viewer.restore_history(state.get("history"))
    viewer._refresh_ratsnest()
    viewer.set_view_mode(state.get("view_mode", "2d"))

    sections = state.get("sections", {})
    # Current snapshots use the seven preference keys.  Accept the pre-0.10
    # aliases so a live host rebuild taken by an older widget remains harmless.
    if "layers" in sections and "footprint_graphics" not in sections:
        sections["footprint_graphics"] = sections["layers"]
    for key, section in getattr(viewer, "_sidebar_sections", {}).items():
        if key in sections:
            section.set_expanded(bool(sections[key]))
    viewer.canvas._update_scene_rect()

    # Rehosting is presentation-only.  A clean board must stay clean even if
    # reconstruction normalises harmless runtime details (Qt angle/coordinate
    # representations, restored model objects, etc.) differently from the old
    # widget.  Conversely, a board that was dirty before the switch must keep
    # its original saved baseline so the unsaved marker is not lost.
    if bool(state.get("modified", False)):
        viewer._saved_content_signature = state.get("saved_content_signature")
        viewer._modified = True
        viewer._refresh_window_title()
    else:
        viewer._capture_saved_content_state()


def _discard_current_host_for_rebuild():
    """Destroy the current host and viewer instead of reparenting native UI."""
    global _viewer, _subwindow, _window_geometry

    viewer = _viewer
    host = _subwindow
    _viewer = None
    _subwindow = None
    restore_panels()

    if _alive(viewer):
        _prepare_viewer_for_rehost(viewer)
        try:
            # The session was snapshotted into the replacement widget; stop the
            # old one's timers/filters so it cannot keep running hidden.
            viewer.close_handler = None
            viewer.shutdown()
        except (AttributeError, RuntimeError):
            pass

    if _alive(host) and isinstance(host, QtWidgets.QMdiSubWindow):
        area = host.mdiArea()
        try:
            removed_was_maximized = bool(host.isMaximized())
        except RuntimeError:
            removed_was_maximized = False
        try:
            host.hide()
            host.setEnabled(False)
            host.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        except RuntimeError:
            pass
        if area is not None:
            try:
                area.removeSubWindow(host)
            except Exception:
                pass
            _restore_mdi_return_target(area, removed_was_maximized)
        try:
            host.deleteLater()  # owns and deletes the old Viewer2D child
        except RuntimeError:
            pass
    elif _alive(viewer):
        try:
            _window_geometry = viewer.saveGeometry()
        except RuntimeError:
            pass
        try:
            viewer.hide()
            viewer.setEnabled(False)
            viewer.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            viewer.deleteLater()
        except RuntimeError:
            pass


def _rebuild_viewer_in_host(mode):
    """Switch Tab/Window using a fresh Qt widget while keeping session state.

    Recreating the widget is intentional.  Reparenting a widget that has owned
    a native top-level HWND into QMdiArea is unreliable on Windows and was the
    source of the still-visible-but-unclickable controls reported in 0.4.7-9.
    Parsed footprints are lightweight Python models, so preserving the session
    does not require preserving the problematic native widget hierarchy.
    """
    global _viewer

    old = get_viewer(create=False)
    state = _snapshot_viewer_state(old) if old is not None else None
    _discard_current_host_for_rebuild()

    from . import theme as thememod
    _viewer = Viewer2D(theme=thememod.application_theme(), hosted=True)
    _viewer.hostActionRequested.connect(_on_host_action)
    _viewer.close_handler = _on_window_close_requested
    _hook_main_close_guard()
    _restore_viewer_state(_viewer, state, restore_view=False)
    viewer = _host_viewer(mode)

    def finish_restore():
        if _alive(viewer):
            _restore_viewer_state(viewer, state, restore_view=True)
            try:
                viewer.setEnabled(True)
                viewer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                viewer.canvas.viewport().setEnabled(True)
                viewer.canvas.viewport().setAttribute(Qt.WA_TransparentForMouseEvents, False)
                if viewer.view_mode == "3d":
                    viewer._refresh_3d_if_visible(force=True)
                    viewer.view3d.setFocus()
                else:
                    viewer.canvas.viewport().setFocus()
            except RuntimeError:
                pass

    QtCore.QTimer.singleShot(0, finish_restore)
    return viewer


def _prepare_viewer_for_rehost(viewer):
    """Clear transient input/focus state before changing QWidget hosts.

    The Options command can be launched by a button inside the viewer itself.
    Reparenting that viewer while a mouse/click event is still unwinding can
    leave a stale mouse grab on the old viewport/MDI shell.  The next host then
    looks correct but does not receive clicks.
    """
    if not _alive(viewer):
        return
    try:
        if getattr(viewer, "_move_session", None) is not None:
            viewer.cancel_move_selected()
        viewer.canvas.cancel_interaction()
    except (AttributeError, RuntimeError):
        pass
    for widget in (viewer, getattr(viewer, "canvas", None)):
        if widget is None:
            continue
        try:
            widget.releaseMouse()
        except RuntimeError:
            pass
    try:
        viewer.clearFocus()
    except RuntimeError:
        pass


def _host_viewer(mode):
    """Put the existing Viewer2D widget in *mode* without recreating it."""
    global _subwindow

    viewer = get_viewer()
    main = _main_window()
    mdi = _mdi_area()
    mode = "window" if mode == "window" or mdi is None else "tab"

    # Rehosting must never inherit disabled/transparent state from a retiring
    # MDI shell.  Re-enable the persistent viewer and its viewport explicitly.
    try:
        viewer.setEnabled(True)
        viewer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        viewer.setUpdatesEnabled(True)
        viewer.canvas.setEnabled(True)
        viewer.canvas.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        viewer.canvas.viewport().setEnabled(True)
        viewer.canvas.viewport().setAttribute(Qt.WA_TransparentForMouseEvents, False)
    except RuntimeError:
        pass

    if mode == "window":
        # A Separate Window must be a genuinely independent top-level window.
        # Parenting it to FreeCAD's QMainWindow makes it an owned tool window on
        # Windows; workbench/MDI activation can then hide/minimise it along with
        # the workbench UI.  Keep no QWidget parent so FreeCAD can switch
        # workbenches/documents without changing this window's visibility.
        viewer.setParent(None, Qt.Window)
        viewer._refresh_window_title()
        viewer.setWindowIcon(QtGui.QIcon(icon_path("Elec3D_Panel.svg")))
        if _window_geometry is not None:
            try:
                viewer.restoreGeometry(_window_geometry)
            except RuntimeError:
                viewer.resize(1150, 780)
        else:
            viewer.resize(1150, 780)
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()
        _subwindow = viewer
        restore_panels()
    else:
        _remember_mdi_return_target(mdi)
        # A widget previously used as a top-level window keeps Qt.Window in its
        # flags until explicitly reset.  Clear it before installing the widget
        # into the MDI subwindow so switching Window -> Tab is immediate.
        try:
            viewer.setWindowFlags(Qt.Widget)
        except Exception:
            pass
        # Never let child size-hints make a maximized MDI document wider than
        # FreeCAD's central viewport.  Responsive toolbars inside Viewer2D can
        # shrink; the MDI shell must be allowed to shrink with them as well.
        viewer.setMinimumSize(0, 0)
        viewer.setSizePolicy(SIZE_IGNORED, SIZE_IGNORED)
        sub = ViewerSubWindow()
        sub.setMinimumSize(0, 0)
        sub.setWidget(viewer)
        sub.setWindowTitle(viewer.windowTitle())
        viewer.windowTitleChanged.connect(sub.setWindowTitle)
        sub.setWindowIcon(QtGui.QIcon(icon_path("Elec3D_Panel.svg")))
        sub.setAttribute(Qt.WA_DeleteOnClose, True)
        mdi.addSubWindow(sub)

        # A rebuilt Viewer2D may still carry an explicit hidden state from
        # reparenting it.  Qt preserves that explicit hidden state when the
        # widget is installed into a QMdiSubWindow, so merely showing/maximising
        # the MDI host leaves a blank child window.  Explicitly show the viewer
        # again after setWidget(), then activate/maximise the host.
        viewer.show()
        sub.show()
        mdi.setActiveSubWindow(sub)
        sub.showMaximized()

        _subwindow = sub
        _hook_mdi()
        hide_panels()

        # FreeCAD may process MDI activation/layout on the next event turn.
        # Reassert visibility after that layout pass so Window -> Tab switching
        # cannot regress to an empty MDI child on Qt/FreeCAD combinations that
        # restore sub-window state asynchronously.
        def finish_tab_host():
            if not (_alive(sub) and _alive(viewer)):
                return
            try:
                viewer.show()
                sub.show()
                area = sub.mdiArea()
                if area is not None:
                    area.setActiveSubWindow(sub)
                sub.showMaximized()
                viewer.setEnabled(True)
                viewer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                viewer.canvas.viewport().setAttribute(Qt.WA_TransparentForMouseEvents, False)
                viewer.updateGeometry()
                viewer.update()
                viewer.canvas._refresh_cursor_from_global()
            except (AttributeError, RuntimeError):
                pass

        QtCore.QTimer.singleShot(0, finish_tab_host)

    viewer.sync_application_theme()
    try:
        viewer.canvas.setFocus()
    except (AttributeError, RuntimeError):
        viewer.setFocus()
    return viewer


def on_workbench_deactivated():
    """Elec3D lost workbench focus: leave the editor exactly as it is.

    The tab or window keeps running beside the new workbench (it is neither
    closed nor re-shown); only the Model/Tasks panels hidden for the Elec3D
    tab are given back to the newly active workbench.
    """
    restore_panels()


def keep_viewer_tab_available():
    """Keep the add-on MDI tab present after FreeCAD creates/activates a document.

    Creating a normal FreeCAD document can make Qt/FreeCAD relayout the MDI
    area.  Reassert the custom viewer subwindow on the next event turn without
    activating it, so the new FreeCAD document and the add-on remain separate
    switchable tabs.
    """
    if _window_mode() != "tab":
        return
    host = _subwindow
    mdi = _mdi_area()
    viewer = _viewer
    if not (_alive(host) and _alive(viewer) and mdi is not None and
            isinstance(host, QtWidgets.QMdiSubWindow)):
        return

    def ensure_present():
        if not (_alive(host) and _alive(viewer)):
            return
        area = host.mdiArea() or _mdi_area()
        if area is None:
            return
        try:
            if host not in area.subWindowList():
                area.addSubWindow(host)
            viewer.show()
            host.show()
        except (AttributeError, RuntimeError):
            pass

    QtCore.QTimer.singleShot(0, ensure_present)


def request_apply_host_preferences():
    """Queue a host-mode change for the next Qt event turn.

    Options is commonly opened from the viewer's own **Options** button.  A
    synchronous host rebuild from inside that button's clicked/command call
    stack can race the modal dialog teardown.  Deferring one event turn lets
    the dialog and originating click finish before the old native host is retired.
    Multiple requests in the same turn are coalesced.
    """
    global _rehost_pending
    if _rehost_pending:
        return
    _rehost_pending = True

    def apply_later():
        global _rehost_pending
        _rehost_pending = False
        apply_host_preferences()

    QtCore.QTimer.singleShot(0, apply_later)


def apply_host_preferences():
    """Apply viewer hosting/panel preferences immediately to a live viewer.

    Changing **Tab in the main window** / **Separate window** in Options is
    applied immediately.  The Qt host/view widgets are rebuilt to avoid native
    reparenting bugs while parsed models, positions, visibility and selection
    are restored into the new viewer.
    """
    viewer = get_viewer(create=False)
    if viewer is None:
        return None

    target = _window_mode()
    if _alive(_subwindow) and _current_host_mode() != target:
        return _rebuild_viewer_in_host(target)

    # If the host is already correct, still apply panel visibility immediately
    # and resync colours to FreeCAD's current palette.
    if _alive(_subwindow):
        if _current_host_mode() == "tab":
            mdi = _mdi_area()
            if mdi is not None and mdi.activeSubWindow() is _subwindow:
                hide_panels()
            else:
                restore_panels()
        else:
            restore_panels()
    try:
        viewer.apply_ui_preferences()
    except (AttributeError, RuntimeError):
        pass
    viewer.sync_application_theme()
    return viewer


def show_viewer(theme=None):
    """Create or raise the custom viewer without creating a FreeCAD document."""
    viewer = get_viewer(theme=theme)
    target = _window_mode()

    if _alive(_subwindow):
        if _current_host_mode() != target:
            return _rebuild_viewer_in_host(target)

        _subwindow.show()
        mdi = _mdi_area()
        if target == "tab" and mdi is not None:
            mdi.setActiveSubWindow(_subwindow)
            hide_panels()
        else:
            restore_panels()
            try:
                if _subwindow.windowState() & WINDOW_MINIMIZED:
                    _subwindow.showNormal()
            except (AttributeError, RuntimeError, TypeError):
                pass
            _subwindow.raise_()
            _subwindow.activateWindow()
        viewer.sync_application_theme()
        viewer.setFocus()
        return viewer

    return _host_viewer(target)


def hide_viewer(confirm=True):
    """Close the editor (tab or window) and release its session.

    With *confirm* unsaved changes prompt Save / Discard / Cancel first.
    Returns ``False`` when the user cancelled and the editor stays open.
    """
    global _closing
    if _closing:
        return False
    if not (_alive(_subwindow) or _alive(_viewer)):
        restore_panels()
        return True
    if confirm and _alive(_viewer):
        try:
            if not _viewer.confirm_save_or_discard("closing Elec3D"):
                return False
        except RuntimeError:
            pass
    _closing = True
    try:
        _destroy_viewer()
    finally:
        _closing = False
    return True


def close_viewer():
    """Destroy the editor without prompting (FreeCAD quit, tests, scripts)."""
    return hide_viewer(confirm=False)


def is_open():
    return _alive(_subwindow)


def add_footprint(fp, doc_object=None, raise_window=True):
    """Add a parsed footprint to the custom viewer.

    ``doc_object`` is accepted only for compatibility with 0.3.x callers.  It
    is never required by the normal 0.4 import path.  If supplied (for example
    when reading an old FreeCAD document), its placement is used as an initial
    display position but the custom viewer remains independent afterwards.
    """
    viewer = show_viewer() if raise_window else get_viewer()
    item = viewer.add_footprint(fp)
    if doc_object is not None:
        try:
            base = doc_object.Placement.Base
            item.setPos(QtCore.QPointF(base.x, -base.y))
        except Exception:
            pass
    return item


def import_paths(paths, raise_window=True):
    """Parse *paths* directly into the custom viewer.

    Returns ``[(path, error_text), ...]`` for files that failed.  Crucially,
    this function has no document-building step and opens no FreeCAD document.
    """
    from .footprint import parse_footprint_file

    viewer = show_viewer() if raise_window else get_viewer()
    errors = []
    loaded = 0
    for path in paths:
        try:
            fp = parse_footprint_file(path)
            viewer.add_footprint(fp)
            loaded += 1
            App.Console.PrintMessage("KiCad footprint opened in 2D viewer: %s\n" % fp.summary())
            for warn in fp.warnings:
                App.Console.PrintWarning("KiCad footprint: %s\n" % warn)
        except Exception as exc:
            errors.append((path, str(exc)))
            App.Console.PrintError("KiCad footprint import failed for %s: %s\n" % (path, exc))
    if loaded:
        viewer.canvas.fit()
    return errors


def reload_active():
    """Reload the selected viewer entry from disk, preserving its scene position."""
    from .footprint import parse_footprint_file

    viewer = get_viewer(create=False)
    if viewer is None:
        raise RuntimeError("The 2D viewer is not open.")
    entry = viewer.active_entry()
    if entry is None:
        raise RuntimeError("No footprint is selected.")
    path = entry["fp"].source_file
    if not path:
        raise RuntimeError("The selected footprint has no source file.")
    if not os.path.isfile(path):
        raise RuntimeError("File not found: %s" % path)

    fp = parse_footprint_file(path)
    viewer.replace_active_footprint(fp)
    App.Console.PrintMessage("KiCad footprint reloaded: %s\n" % fp.summary())
    return fp


def load_document(doc=None, raise_window=True):
    """Load source-linked footprints from an *existing* FreeCAD document.

    This is a compatibility helper for files created by older addon versions.
    It never creates a document and never binds the custom viewer to one.
    """
    from .footprint import parse_footprint_file

    viewer = show_viewer() if raise_window else get_viewer()
    doc = doc or App.ActiveDocument
    if doc is None:
        return viewer

    viewer.clear()
    loaded = 0
    for obj in getattr(doc, "Objects", []):
        path = getattr(obj, "SourceFile", "")
        if not path or not os.path.isfile(path):
            continue
        try:
            add_footprint(parse_footprint_file(path), obj, raise_window=False)
            loaded += 1
        except Exception as exc:
            App.Console.PrintError("Elec3D: %s could not be drawn: %s\n" % (path, exc))
    if loaded:
        viewer.canvas.fit()
    return viewer


# ---------------------------------------------------------------------------
# explicit conversion to a normal FreeCAD document
# ---------------------------------------------------------------------------
def _unique_document_name(base):
    """Return a FreeCAD-internal document name that is not already in use."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in (base or "Elec3D"))
    cleaned = cleaned.strip("_") or "Elec3D"
    candidate = cleaned
    index = 2
    while True:
        try:
            existing = App.getDocument(candidate)
        except Exception:
            existing = None
        if existing is None:
            return candidate
        candidate = "%s_%d" % (cleaned, index)
        index += 1


def create_freecad_document():
    """Create or refresh the current PCB assembly in a FreeCAD document.

    This compatibility API now delegates to the same exact-BRep exporter used
    by File -> Export to FreeCAD, so routed copper, pads, models and model
    correction offsets stay consistent with the visible export workflow.
    """
    viewer = get_viewer(create=False)
    if viewer is None:
        raise RuntimeError("The 2D viewer is not open.")
    if viewer.canvas.has_active_interaction():
        raise RuntimeError("Release the current mouse operation before exporting.")
    if viewer.routing is not None:
        viewer._finish_route()
    if viewer._export_options.get("models"):
        viewer._prepare_models_for_export()

    from . import export3d
    result = export3d.create_freecad_document(
        viewer._export_entries(), viewer.stack,
        viewer.board_solid,
        viewer.tracks, viewer.vias, viewer.models_folder,
        dict(viewer._export_options), source_name=viewer._export_source_name(),
        progress=viewer._export_progress,
        target_document=getattr(viewer, "_freecad_export_document", ""),
        update_existing=True)
    viewer._freecad_export_document = getattr(result["document"], "Name", "") or ""
    keep_viewer_tab_available()
    return result["document"]


# ---------------------------------------------------------------------------
def _on_host_action(action):
    """Dispatch actions embedded in the custom viewer header."""
    names = {
        "import": "Elec3D_Import",
        "netlist": "Elec3D_ImportNetlist",
        "reload": "Elec3D_Reload",
        "options": "Elec3D_Options",
    }
    name = names.get(action)
    if not name:
        return
    try:
        import FreeCADGui as Gui

        Gui.runCommand(name, 0)
    except Exception as exc:
        App.Console.PrintError("Elec3D: %s action failed: %s\n" % (action, exc))
