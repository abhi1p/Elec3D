# -*- coding: utf-8 -*-
"""Lightweight interactive 2D editing for imported KiCad footprints.

This module deliberately keeps the live interaction in the FreeCAD/Coin3D
viewer instead of rebuilding Part geometry while the mouse moves.  The
imported footprint is an ``App::Part`` container, so changing only the
container Placement moves every layer, pad and text together.

Implemented for the first interaction prototype:

* click a footprint to select it without moving;
* Shift-click to add/remove footprints from the selection;
* press and drag a selected footprint to move the whole selection in the XY plane;
* click empty space to clear the selection;
* draw a Coin3D selection rectangle around each selected footprint.

Routing, marquee selection, snapping and rotation are intentionally left for
later revisions.
"""

import math

import FreeCAD as App
import FreeCADGui as Gui

# FreeCAD exposes Qt through the compatibility ``PySide`` package.  Querying
# Qt for the current mouse-button state gives us a reliable guard against a
# missed Coin3D button-release callback (which otherwise leaves a footprint
# attached to the cursor after a simple click on some platforms/builds).
try:
    from PySide import QtCore, QtWidgets
except Exception:  # pragma: no cover - FreeCAD GUI normally provides PySide
    QtCore = None
    QtWidgets = None

try:
    from pivy import coin
except Exception:  # pragma: no cover - only meaningful inside the FreeCAD GUI
    coin = None


_INTERACTIVE_PROP = "KiCadInteractive2D"
_BBOX_MIN_PROP = "KiCadBBoxMin"
_BBOX_MAX_PROP = "KiCadBBoxMax"

_enabled = False
_manager = None


def _is_interactive_root(obj):
    try:
        return bool(getattr(obj, _INTERACTIVE_PROP, False))
    except Exception:
        return False


def _interactive_root(obj):
    """Return the imported footprint container that owns *obj*, if any."""
    if obj is None:
        return None
    todo = [obj]
    seen = set()
    while todo:
        cur = todo.pop(0)
        key = getattr(cur, "Name", None)
        doc = getattr(getattr(cur, "Document", None), "Name", None)
        ident = (doc, key)
        if ident in seen:
            continue
        seen.add(ident)
        if _is_interactive_root(cur):
            return cur
        try:
            todo.extend(list(cur.InList))
        except Exception:
            pass
    return None


def _copy_rotation(rot):
    try:
        return App.Rotation(*rot.Q)
    except Exception:
        return App.Rotation()


class _Interaction2D(object):
    """Own the callbacks and selection overlay for one active 3D view."""

    def __init__(self, view):
        self.view = view
        self.mouse_callback = None
        self.move_callback = None
        self.overlay_root = None
        self.overlay_geometry = None

        self.left_button_down = False
        self.drag_candidate = False
        self.dragging = False
        self.drag_start_screen = None
        self.drag_start_world = None
        self.drag_items = []   # [(root, base_vector, rotation), ...]
        self.drag_doc = None
        self.transaction_open = False
        self.last_screen_pos = None

        self._attach()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _attach(self):
        if coin is None or self.view is None:
            return
        try:
            self.overlay_root = coin.SoSeparator()
            # SoAnnotation renders after normal geometry, keeping the selection
            # outline visible even though footprint layers use small Z offsets.
            try:
                annotation = coin.SoAnnotation()
                self.overlay_geometry = annotation
                self.overlay_root.addChild(annotation)
            except Exception:
                self.overlay_geometry = self.overlay_root
            self.view.getSceneGraph().addChild(self.overlay_root)

            self.mouse_callback = self.view.addEventCallbackPivy(
                coin.SoMouseButtonEvent.getClassTypeId(), self._on_mouse)
            self.move_callback = self.view.addEventCallbackPivy(
                coin.SoLocation2Event.getClassTypeId(), self._on_move)
            self.refresh_selection_overlay()
        except Exception as exc:
            App.Console.PrintError(
                "Elec3D: could not start interactive 2D mode: %s\n" % exc)
            self.detach()

    def detach(self):
        self.left_button_down = False
        self._finish_drag(commit=True)
        if self.view is not None and coin is not None:
            try:
                if self.mouse_callback is not None:
                    self.view.removeEventCallbackPivy(
                        coin.SoMouseButtonEvent.getClassTypeId(), self.mouse_callback)
            except Exception:
                pass
            try:
                if self.move_callback is not None:
                    self.view.removeEventCallbackPivy(
                        coin.SoLocation2Event.getClassTypeId(), self.move_callback)
            except Exception:
                pass
            try:
                if self.overlay_root is not None:
                    self.view.getSceneGraph().removeChild(self.overlay_root)
            except Exception:
                pass
        self.mouse_callback = None
        self.move_callback = None
        self.overlay_root = None
        self.overlay_geometry = None
        self.view = None

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def selected_roots(self):
        roots = []
        seen = set()
        try:
            selection = Gui.Selection.getSelection()
        except Exception:
            selection = []
        for obj in selection:
            root = _interactive_root(obj)
            if root is None:
                continue
            key = (root.Document.Name, root.Name)
            if key not in seen:
                seen.add(key)
                roots.append(root)
        return roots

    def _set_selection(self, roots):
        try:
            Gui.Selection.clearSelection()
            for root in roots:
                Gui.Selection.addSelection(root)
        except Exception:
            pass
        self.refresh_selection_overlay()

    def _root_at(self, pos):
        """Map the picked child Part/Draft object back to its footprint root."""
        try:
            info = self.view.getObjectInfo((int(pos[0]), int(pos[1])))
        except Exception:
            info = None
        if not info:
            return None
        try:
            doc_name = info.get("Document") or App.ActiveDocument.Name
            obj_name = info.get("Object")
            if not obj_name:
                return None
            doc = App.getDocument(doc_name)
            root = _interactive_root(doc.getObject(obj_name))
            if root is not None and not self._screen_hit_inside_root(root, pos):
                return None
            return root
        except Exception:
            return None

    # ------------------------------------------------------------------
    # screen/world conversion
    # ------------------------------------------------------------------
    def _screen_to_world(self, pos):
        """Convert a viewer mouse position to a point on the top-view plane.

        FreeCAD exposes ``getPoint`` on the active view and also
        ``getPointOnFocalPlane``.  The former is used first because it accepts
        exactly the Position tuple delivered by the Coin event callback.  The
        fallbacks keep the addon compatible with builds exposing only the
        newer wrapper.
        """
        p = (int(pos[0]), int(pos[1]))
        try:
            v = self.view.getPoint(p)
            return App.Vector(v.x, v.y, v.z)
        except Exception:
            pass
        try:
            v = self.view.getPointOnFocalPlane(p)
            return App.Vector(v.x, v.y, v.z)
        except Exception:
            pass
        try:
            v = self.view.getPointOnFocalPlane(p[0], p[1])
            return App.Vector(v.x, v.y, v.z)
        except Exception:
            return None

    def _world_per_pixel(self, pos):
        """Approximate world millimetres represented by one screen pixel."""
        try:
            p = (int(pos[0]), int(pos[1]))
        except Exception:
            return None
        origin = self._screen_to_world(p)
        px = self._screen_to_world((p[0] + 8, p[1]))
        py = self._screen_to_world((p[0], p[1] + 8))
        if origin is None:
            return None

        values = []
        for other in (px, py):
            if other is None:
                continue
            dx = other.x - origin.x
            dy = other.y - origin.y
            dz = other.z - origin.z
            values.append(math.sqrt(dx * dx + dy * dy + dz * dz) / 8.0)
        return max(values) if values else None

    def _screen_hit_inside_root(self, root, pos):
        """Reject Coin picks that are visibly outside the footprint bounds.

        FreeCAD/Coin selection tolerance is screen based.  At a distant zoom it
        can report a child object even when the pointer is well away from the
        footprint.  Validate that pick against the importer's own local PCB
        bounds using a small, zoom-aware pixel tolerance.
        """
        try:
            bmin = getattr(root, _BBOX_MIN_PROP)
            bmax = getattr(root, _BBOX_MAX_PROP)
        except Exception:
            return True

        world = self._screen_to_world(pos)
        if world is None:
            return False
        try:
            local = root.Placement.inverse().multVec(world)
        except Exception:
            local = world

        per_px = self._world_per_pixel(pos)
        tol = 0.08 if per_px is None else max(0.02, min(1.0, per_px * 5.0))
        return (bmin.x - tol <= local.x <= bmax.x + tol and
                bmin.y - tol <= local.y <= bmax.y + tol)

    def _qt_left_button_down(self):
        """Return Qt's current left-button state when available.

        Coin location events do not themselves carry a button state.  A lost
        release event would therefore keep ``drag_candidate`` alive forever.
        Qt owns the actual native mouse state, so it is the safest cross-platform
        check before moving a footprint.
        """
        if QtCore is None or QtWidgets is None:
            return None
        try:
            app = QtWidgets.QApplication.instance()
            if app is None:
                return None
            buttons = app.mouseButtons()
            # PySide2/Qt5 exposes Qt.LeftButton; PySide6/Qt6 also exposes the
            # scoped MouseButton enum.  Support both without version checks.
            left = getattr(QtCore.Qt, "LeftButton", None)
            if left is None:
                left = QtCore.Qt.MouseButton.LeftButton
            return bool(buttons & left)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # mouse callbacks
    # ------------------------------------------------------------------
    def _on_mouse(self, event_callback):
        try:
            event = event_callback.getEvent()
            if event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
                return
            pos = event.getPosition().getValue()
            self.last_screen_pos = (int(pos[0]), int(pos[1]))
            state = event.getState()

            if state == coin.SoButtonEvent.DOWN:
                self.left_button_down = True
                self._left_down(pos, bool(event.wasShiftDown()))
                # Prevent FreeCAD's normal object drag/selection from competing
                # with this lightweight PCB interaction.
                event_callback.setHandled()
            elif state == coin.SoButtonEvent.UP:
                # Always clear the physical-button state on release, even when
                # no drag candidate exists.  Selection remains intact after a
                # normal click; only movement is ended here.
                self.left_button_down = False
                if self.drag_candidate or self.dragging:
                    self._finish_drag(commit=True)
                event_callback.setHandled()
        except Exception as exc:
            App.Console.PrintError("Elec3D 2D mouse error: %s\n" % exc)
            self.left_button_down = False
            self._finish_drag(commit=True)

    def _left_down(self, pos, shift):
        root = self._root_at(pos)
        selected = self.selected_roots()

        if root is None:
            if not shift:
                self._set_selection([])
            self._reset_drag_state()
            return

        if shift:
            if root in selected:
                selected = [r for r in selected if r != root]
                self._set_selection(selected)
                self._reset_drag_state()
                return
            selected.append(root)
            self._set_selection(selected)
        elif root not in selected:
            selected = [root]
            self._set_selection(selected)

        # Dragging one member of an existing multi-selection moves the whole set.
        selected = self.selected_roots()
        if root not in selected:
            self._reset_drag_state()
            return

        world = self._screen_to_world(pos)
        if world is None:
            self._reset_drag_state()
            return

        self.drag_candidate = True
        self.dragging = False
        self.drag_start_screen = (int(pos[0]), int(pos[1]))
        self.drag_start_world = world
        self.drag_items = []
        for item in selected:
            try:
                pl = item.Placement
                base = App.Vector(pl.Base.x, pl.Base.y, pl.Base.z)
                self.drag_items.append((item, base, _copy_rotation(pl.Rotation)))
            except Exception:
                pass
        self.drag_doc = selected[0].Document if selected else None

    def _on_move(self, event_callback):
        if not self.drag_candidate:
            return

        # A footprint must move only while button 1 is physically held down.
        # This reproduces the web editor's click-to-select / drag-to-move model
        # and also recovers cleanly if Coin misses a button-release callback.
        qt_down = self._qt_left_button_down()
        if qt_down is False or (qt_down is None and not self.left_button_down):
            self.left_button_down = False
            self._finish_drag(commit=True)
            return

        try:
            event = event_callback.getEvent()
            pos = event.getPosition().getValue()
            self.last_screen_pos = (int(pos[0]), int(pos[1]))
            sx, sy = self.drag_start_screen
            dx_px = int(pos[0]) - sx
            dy_px = int(pos[1]) - sy

            # A small threshold lets a normal click select without producing an
            # undoable zero-distance move.
            if not self.dragging and (dx_px * dx_px + dy_px * dy_px) < 9:
                event_callback.setHandled()
                return

            world = self._screen_to_world(pos)
            if world is None or self.drag_start_world is None:
                return

            if not self.dragging:
                self.dragging = True
                if self.drag_doc is not None:
                    try:
                        self.drag_doc.openTransaction("Move KiCad footprint")
                        self.transaction_open = True
                    except Exception:
                        self.transaction_open = False

            delta = world - self.drag_start_world
            # Interaction is intentionally constrained to the PCB XY plane.
            delta.z = 0.0
            for root, base, rotation in self.drag_items:
                root.Placement = App.Placement(base + delta, rotation)

            self.refresh_selection_overlay()
            try:
                self.view.redraw()
            except Exception:
                try:
                    Gui.updateGui()
                except Exception:
                    pass
            event_callback.setHandled()
        except Exception as exc:
            App.Console.PrintError("Elec3D 2D drag error: %s\n" % exc)
            self._finish_drag(commit=True)

    def _finish_drag(self, commit=True):
        if self.transaction_open and self.drag_doc is not None:
            try:
                if commit:
                    self.drag_doc.commitTransaction()
                else:
                    self.drag_doc.abortTransaction()
            except Exception:
                pass
        if self.dragging and self.drag_doc is not None:
            try:
                self.drag_doc.recompute()
            except Exception:
                pass
        self._reset_drag_state()
        self.refresh_selection_overlay()

    def _reset_drag_state(self):
        self.drag_candidate = False
        self.dragging = False
        self.drag_start_screen = None
        self.drag_start_world = None
        self.drag_items = []
        self.drag_doc = None
        self.transaction_open = False

    # ------------------------------------------------------------------
    # Coin3D selection overlay
    # ------------------------------------------------------------------
    def refresh_selection_overlay(self):
        if self.overlay_geometry is None or coin is None:
            return
        try:
            self.overlay_geometry.removeAllChildren()
        except Exception:
            return

        for root in self.selected_roots():
            node = self._selection_box(root)
            if node is not None:
                self.overlay_geometry.addChild(node)
        try:
            self.view.redraw()
        except Exception:
            pass

    def _selection_box(self, root):
        try:
            bmin = getattr(root, _BBOX_MIN_PROP)
            bmax = getattr(root, _BBOX_MAX_PROP)
        except Exception:
            return None

        # Keep the outline offset approximately constant on screen.  A fixed
        # millimetre margin looks enormous when zoomed in and disappears when
        # zoomed out.
        per_px = self._world_per_pixel(self.last_screen_pos) \
            if self.last_screen_pos is not None else None
        pad = 0.35 if per_px is None else max(0.04, min(2.0, per_px * 6.0))
        local = [
            App.Vector(bmin.x - pad, bmin.y - pad, 0.25),
            App.Vector(bmax.x + pad, bmin.y - pad, 0.25),
            App.Vector(bmax.x + pad, bmax.y + pad, 0.25),
            App.Vector(bmin.x - pad, bmax.y + pad, 0.25),
            App.Vector(bmin.x - pad, bmin.y - pad, 0.25),
        ]
        try:
            pts = [root.Placement.multVec(p) for p in local]
        except Exception:
            pts = local

        sep = coin.SoSeparator()
        color = coin.SoBaseColor()
        color.rgb = (0.24, 0.45, 1.0)
        style = coin.SoDrawStyle()
        style.style = coin.SoDrawStyle.LINES
        style.lineWidth = 2.5
        try:
            style.linePattern = 0xF0F0
        except Exception:
            pass
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(pts), [(p.x, p.y, p.z) for p in pts])
        line = coin.SoLineSet()
        line.numVertices.setValue(len(pts))
        sep.addChild(color)
        sep.addChild(style)
        sep.addChild(coords)
        sep.addChild(line)
        return sep


def activate_interaction():
    """Enable interactive 2D handling while this workbench is active."""
    global _enabled
    _enabled = True
    ensure_interaction()


def deactivate_interaction():
    """Release callbacks so other FreeCAD workbenches keep normal navigation."""
    global _enabled, _manager
    _enabled = False
    if _manager is not None:
        _manager.detach()
        _manager = None


def ensure_interaction():
    """Attach to the current active view when interaction mode is enabled."""
    global _manager
    if not _enabled or not App.GuiUp or coin is None:
        return None
    try:
        gui_doc = Gui.activeDocument()
        view = gui_doc.activeView() if gui_doc is not None else None
    except Exception:
        view = None
    if view is None:
        return None

    # ActiveDocument can change after File > Open / Import.  Recreate callbacks
    # on the new view and remove them from the old one.
    if _manager is not None and _manager.view is view:
        _manager.refresh_selection_overlay()
        return _manager
    if _manager is not None:
        _manager.detach()
    _manager = _Interaction2D(view)
    return _manager


def refresh_interaction():
    """Public cheap refresh used after importing/reloading footprints."""
    mgr = ensure_interaction()
    if mgr is not None:
        mgr.refresh_selection_overlay()
