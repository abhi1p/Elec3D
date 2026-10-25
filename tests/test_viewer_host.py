# -*- coding: utf-8 -*-
"""Tests for hosting the 2D viewer inside FreeCAD.

FreeCAD itself is not importable here, so this module installs small stubs for
``FreeCAD`` and ``FreeCADGui`` -- the latter handing out a real ``QMainWindow``
with an MDI area and dock panels.  That is enough to exercise the parts that
actually went wrong in the GUI: a closed tab must disappear, reopening must not
stack up duplicates, and the Model/Tasks panels must come back. Existing document tabs must not be hidden.

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from elec3d.qt import QtCore, QtGui, QtWidgets, QT_BINDING
    try:
        if QT_BINDING == "PySide6":
            from PySide6 import QtTest
        elif QT_BINDING == "PySide2":
            from PySide2 import QtTest
        else:
            from PySide import QtTest
    except ImportError:
        QtTest = None
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    QtTest = None
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")
SHAPES = os.path.join(DATA, "SHAPES_TEST.kicad_mod")

_app = None
_main = None
viewer2d = None


def _install_stubs():
    """Minimal FreeCAD / FreeCADGui good enough for the window plumbing."""
    global _main

    params = {}

    class _Params(object):
        def GetString(self, k, d=""):
            return params.get(k, d)

        def GetBool(self, k, d=False):
            return params.get(k, d)

        def GetInt(self, k, d=0):
            return params.get(k, d)

        def GetFloat(self, k, d=0.0):
            return params.get(k, d)

        def SetString(self, k, v):
            params[k] = v

        def SetBool(self, k, v):
            params[k] = v

    class _Console(object):
        def __getattr__(self, _name):
            return lambda *a, **k: None

    app = types.ModuleType("FreeCAD")
    app.Console = _Console()
    app.GuiUp = True
    app.ActiveDocument = types.SimpleNamespace(Name="Unnamed", Label="Unnamed")
    app.ParamGet = lambda _path: _Params()
    app.Vector = lambda x=0, y=0, z=0: (x, y, z)
    app._params = params
    sys.modules["FreeCAD"] = app

    main = QtWidgets.QMainWindow()
    mdi = QtWidgets.QMdiArea()
    mdi.setViewMode(QtWidgets.QMdiArea.TabbedView)
    main.setCentralWidget(mdi)
    for name, area in (("Model", QtCore.Qt.LeftDockWidgetArea),
                       ("Tasks", QtCore.Qt.RightDockWidgetArea)):
        dock = QtWidgets.QDockWidget(name, main)
        dock.setObjectName(name)
        dock.setWidget(QtWidgets.QLabel(name))
        main.addDockWidget(area, dock)
    overlay = QtWidgets.QWidget(main)      # stands in for FreeCAD's auto-hide panel
    overlay.setObjectName("OverlayLeft")
    overlay.show()
    doc_window = QtWidgets.QMdiSubWindow()
    doc_window.setWidget(QtWidgets.QLabel("3D view"))
    doc_window.setWindowTitle("Unnamed : 1")
    mdi.addSubWindow(doc_window)
    doc_window.show()
    main.resize(1200, 800)
    main.show()
    _main = main

    class _Selection(object):
        def clearSelection(self):
            pass

        def addSelection(self, *a):
            pass

        def getSelection(self):
            return []

    gui = types.ModuleType("FreeCADGui")
    gui.getMainWindow = lambda: _main
    gui.Selection = _Selection()
    gui.Workbench = object
    gui.addWorkbench = lambda w: None
    gui.addCommand = lambda n, c: None
    gui.runCommand = lambda n, m=0: None
    sys.modules["FreeCADGui"] = gui


def setUpModule():
    global _app, viewer2d
    if not HAVE_QT:
        return
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _install_stubs()
    from elec3d import viewer as _viewer_module
    viewer2d = _viewer_module


def _settle(ms=400):
    """Let deferred (timer-based) workbench sync run, like a real event loop."""
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.01)
    _pump()


def _pump():
    _app.processEvents()
    _app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    _app.processEvents()


class _WorkbenchStub(object):
    """Fake FreeCADGui.activeWorkbench/activateWorkbench for follow tests."""

    def __init__(self, initial="PartDesignWorkbench"):
        import FreeCADGui
        from elec3d import wbtrack
        self.gui, self.wbtrack = FreeCADGui, wbtrack
        self.name = initial
        stub = self

        class _Bench(object):
            def name(self):
                return stub.name

        FreeCADGui.activeWorkbench = lambda: _Bench()
        FreeCADGui.activateWorkbench = self.activate
        wbtrack.on_activated(initial)

    def activate(self, name):
        self.name = name
        self.wbtrack.on_activated(name)
        if name == viewer2d.ELEC3D_WORKBENCH:
            viewer2d.show_viewer()

    def remove(self):
        del self.gui.activeWorkbench
        del self.gui.activateWorkbench


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestViewerHosting(unittest.TestCase):
    def setUp(self):
        self.mdi = _main.findChild(QtWidgets.QMdiArea)
        viewer2d.show_viewer()
        _pump()

    def tearDown(self):
        viewer2d.close_viewer()
        _pump()

    # -- helpers ---------------------------------------------------------
    def titles(self):
        return [w.windowTitle() for w in self.mdi.subWindowList()]

    def visible_titles(self):
        return [w.windowTitle() for w in self.mdi.subWindowList() if w.isVisible()]

    def visible_panels(self):
        panels = [w.objectName() for w in _main.findChildren(QtWidgets.QDockWidget)
                  if w.isVisible()]
        panels += [w.objectName() for w in _main.findChildren(QtWidgets.QWidget)
                   if w.objectName().startswith("Overlay") and w.isVisible()]
        return sorted(panels)

    # -- tests -----------------------------------------------------------
    def test_opens_as_one_tab(self):
        self.assertIn(viewer2d.TITLE, self.titles())
        self.assertEqual(self.titles().count(viewer2d.TITLE), 1)
        self.assertTrue(viewer2d.is_open())

    def test_existing_document_tab_is_not_hidden(self):
        self.assertIn(viewer2d.TITLE, self.titles())
        self.assertIn("Unnamed : 1", self.titles())

    def test_panels_hidden_while_open(self):
        self.assertEqual(self.visible_panels(), [])

    def test_closing_removes_the_tab_and_restores_panels(self):
        viewer2d._subwindow.close()
        _pump()
        self.assertNotIn(viewer2d.TITLE, self.titles())
        self.assertFalse(viewer2d.is_open())
        self.assertIn("Unnamed : 1", self.visible_titles())
        self.assertEqual(self.visible_panels(), ["Model", "OverlayLeft", "Tasks"])

    def test_reopening_does_not_duplicate_tabs(self):
        for _ in range(4):
            viewer2d.close_viewer()
            _pump()
            viewer2d.show_viewer()
            _pump()
        self.assertEqual(self.titles().count(viewer2d.TITLE), 1)

    def test_closing_the_tab_ends_the_session_and_frees_the_editor(self):
        from elec3d.footprint import parse_footprint_file

        old = viewer2d.get_viewer()
        viewer2d.add_footprint(parse_footprint_file(SAMPLE))
        old.confirm_save_or_discard = lambda *_a: True      # "Discard"
        viewer2d._subwindow.close()
        _pump()
        self.assertFalse(viewer2d.is_open())
        self.assertIsNone(viewer2d.get_viewer(create=False))
        self.assertTrue(getattr(old, "_shut_down", False))
        self.assertIsNone(old.board_solid)
        self.assertEqual(old._entries, [])
        # Workbench switches afterwards must not bring it back.
        viewer2d.on_workbench_deactivated()
        _pump()
        self.assertFalse(viewer2d.is_open())
        self.assertNotIn(viewer2d.TITLE, self.titles())
        # Re-opening starts a fresh, empty editor.
        fresh = viewer2d.show_viewer()
        _pump()
        self.assertIsNot(fresh, old)
        self.assertEqual(len(fresh.canvas.footprints), 0)

    def test_cancelling_the_unsaved_prompt_keeps_the_tab_open(self):
        viewer = viewer2d.get_viewer()
        tab = viewer2d._subwindow
        viewer.confirm_save_or_discard = lambda *_a: False  # "Cancel"
        tab.close()
        _pump()
        self.assertIs(viewer2d._subwindow, tab)
        self.assertIs(viewer2d.get_viewer(create=False), viewer)
        self.assertIn(viewer2d.TITLE, self.titles())
        self.assertFalse(viewer2d.hide_viewer())
        self.assertTrue(viewer2d.is_open())
        del viewer.confirm_save_or_discard

    def test_panels_follow_the_active_tab(self):
        others = [w for w in self.mdi.subWindowList() if w is not viewer2d._subwindow]
        others[0].show()
        self.mdi.setActiveSubWindow(others[0])
        _pump()
        self.assertEqual(self.visible_panels(), ["Model", "OverlayLeft", "Tasks"])
        self.mdi.setActiveSubWindow(viewer2d._subwindow)
        _pump()
        self.assertEqual(self.visible_panels(), [])

    def test_hide_viewer_is_idempotent(self):
        viewer2d.hide_viewer()
        viewer2d.hide_viewer()
        _pump()
        self.assertFalse(viewer2d.is_open())
        self.assertEqual(self.visible_panels(), ["Model", "OverlayLeft", "Tasks"])

    def test_host_mode_switches_live_and_keeps_session(self):
        import FreeCAD
        from elec3d.footprint import parse_footprint_file

        viewer = viewer2d.get_viewer()
        viewer2d.add_footprint(parse_footprint_file(SAMPLE))
        self.assertEqual(len(viewer.canvas.footprints), 1)

        try:
            FreeCAD._params["ViewerMode"] = "window"
            viewer2d.apply_host_preferences()
            _pump()
            window_viewer = viewer2d.get_viewer()
            # Native host changes intentionally rebuild the Qt widget.  The
            # parsed session, not the HWND/widget hierarchy, is preserved.
            self.assertIsNot(window_viewer, viewer)
            self.assertTrue(window_viewer.isWindow())
            self.assertNotIn(viewer2d.TITLE, self.titles())
            self.assertEqual(len(window_viewer.canvas.footprints), 1)
            self.assertEqual(self.visible_panels(), ["Model", "OverlayLeft", "Tasks"])

            FreeCAD._params["ViewerMode"] = "tab"
            viewer2d.apply_host_preferences()
            _pump()
            tab_viewer = viewer2d.get_viewer()
            self.assertIsNot(tab_viewer, window_viewer)
            self.assertIsInstance(viewer2d._subwindow, QtWidgets.QMdiSubWindow)
            self.assertIn(viewer2d.TITLE, self.titles())
            self.assertIs(viewer2d._subwindow.widget(), tab_viewer)
            self.assertTrue(tab_viewer.isVisible())
            self.assertTrue(viewer2d._subwindow.isVisible())
            self.assertTrue(viewer2d._subwindow.isMaximized())
            self.assertEqual(len(tab_viewer.canvas.footprints), 1)
            self.assertEqual(self.visible_panels(), [])
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_separate_window_remains_visible_on_workbench_deactivation(self):
        import FreeCAD

        try:
            FreeCAD._params["ViewerMode"] = "window"
            viewer2d.apply_host_preferences()
            _pump()
            window = viewer2d._subwindow
            self.assertTrue(window.isWindow())
            self.assertTrue(window.isVisible())

            viewer2d.on_workbench_deactivated()
            _pump()

            self.assertIs(viewer2d._subwindow, window)
            self.assertTrue(viewer2d.is_open())
            self.assertTrue(window.isVisible())
            self.assertFalse(bool(window.windowState() & viewer2d.WINDOW_MINIMIZED))
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_tab_stays_open_on_workbench_deactivation(self):
        import FreeCAD

        FreeCAD._params["ViewerMode"] = "tab"
        try:
            viewer2d.apply_host_preferences()
            viewer2d.show_viewer()
            _pump()
            tab = viewer2d._subwindow
            self.assertIsInstance(tab, QtWidgets.QMdiSubWindow)

            viewer2d.on_workbench_deactivated()     # e.g. switch to Part Design
            _pump()

            self.assertIs(viewer2d._subwindow, tab)
            self.assertTrue(viewer2d.is_open())
            self.assertIn(tab, tab.mdiArea().subWindowList())
            self.assertEqual(viewer2d._hidden_panels, [])
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_tab_brings_its_workbench_and_leaving_restores_the_previous_one(self):
        import FreeCADGui
        from elec3d import wbtrack

        state = {"name": "PartDesignWorkbench"}

        class _Bench(object):
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        def activate(name):
            state["name"] = name
            wbtrack.on_activated(name)
            if name == viewer2d.ELEC3D_WORKBENCH:
                viewer2d.show_viewer()          # what Workbench.Activated does

        FreeCADGui.activeWorkbench = lambda: _Bench(state["name"])
        FreeCADGui.activateWorkbench = activate
        try:
            wbtrack.on_activated("PartDesignWorkbench")
            tab = viewer2d._subwindow
            doc = [w for w in self.mdi.subWindowList() if w is not tab][0]

            self.mdi.setActiveSubWindow(doc)     # leave the Elec3D tab
            _settle()
            # Part Design was already active, so nothing changes here.
            self.assertEqual(state["name"], "PartDesignWorkbench")

            self.mdi.setActiveSubWindow(tab)     # enter the Elec3D tab
            _settle()
            self.assertEqual(state["name"], viewer2d.ELEC3D_WORKBENCH)
            self.assertEqual(self.visible_panels(), [])

            self.mdi.setActiveSubWindow(doc)     # back to the FreeCAD document
            _settle()
            self.assertEqual(state["name"], "PartDesignWorkbench")
            self.assertEqual(self.visible_panels(), ["Model", "OverlayLeft", "Tasks"])

            self.mdi.setActiveSubWindow(tab)
            _settle()
            viewer2d.close_viewer()             # closing the tab also hands it back
            _settle()
            self.assertEqual(state["name"], "PartDesignWorkbench")
        finally:
            del FreeCADGui.activeWorkbench
            del FreeCADGui.activateWorkbench

    def test_choosing_another_workbench_on_the_tab_brings_back_the_document(self):
        stub = _WorkbenchStub("PartWorkbench")
        try:
            tab = viewer2d._subwindow
            doc = [w for w in self.mdi.subWindowList() if w is not tab][0]
            self.mdi.setActiveSubWindow(doc)
            _settle()
            self.mdi.setActiveSubWindow(tab)              # Part -> Elec3D
            _settle()
            self.assertEqual(stub.name, viewer2d.ELEC3D_WORKBENCH)

            stub.activate("PartWorkbench")                # selector, on the tab
            _settle()
            self.assertIs(self.mdi.activeSubWindow(), doc)
            self.assertEqual(stub.name, "PartWorkbench")
            self.assertTrue(viewer2d.is_open())           # the tab itself stays open
        finally:
            stub.remove()

    def test_host_change_request_is_deferred_out_of_click_stack(self):
        import FreeCAD

        try:
            self.assertEqual(viewer2d._current_host_mode(), "tab")
            FreeCAD._params["ViewerMode"] = "window"
            viewer2d.request_apply_host_preferences()
            # The old host must not be retired/rebuilt synchronously from the
            # caller's button/click stack.  The zero-delay timer does it next turn.
            self.assertEqual(viewer2d._current_host_mode(), "tab")
            _pump()
            self.assertEqual(viewer2d._current_host_mode(), "window")
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_repeated_tab_window_tab_keeps_input_enabled(self):
        import FreeCAD

        previous = viewer2d.get_viewer()
        try:
            for mode in ("window", "tab", "window", "tab"):
                FreeCAD._params["ViewerMode"] = mode
                viewer2d.apply_host_preferences()
                _pump()
                viewer = viewer2d.get_viewer()
                self.assertIsNot(viewer, previous)
                self.assertTrue(viewer.isEnabled())
                self.assertTrue(viewer.canvas.isEnabled())
                self.assertTrue(viewer.canvas.viewport().isEnabled())
                self.assertFalse(viewer.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents))
                self.assertFalse(viewer.canvas.viewport().testAttribute(
                    QtCore.Qt.WA_TransparentForMouseEvents))
                if QtTest is not None:
                    # Exercise a real mouse event, not QPushButton.click().  The
                    # Windows regression rendered the controls correctly while
                    # native event routing still targeted the retired host.
                    before = viewer.btn_grid.isChecked()
                    QtTest.QTest.mouseClick(viewer.btn_grid, QtCore.Qt.LeftButton)
                    _pump()
                    self.assertNotEqual(viewer.btn_grid.isChecked(), before)
                previous = viewer
            self.assertEqual(self.titles().count(viewer2d.TITLE), 1)
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_window_to_tab_builds_visible_fresh_viewer(self):
        """Window -> Tab must not reuse the native top-level widget hierarchy."""
        import FreeCAD

        original = viewer2d.get_viewer()
        try:
            FreeCAD._params["ViewerMode"] = "window"
            viewer2d.apply_host_preferences()
            _pump()
            window_viewer = viewer2d.get_viewer()
            self.assertIsNot(window_viewer, original)
            self.assertTrue(window_viewer.isVisible())
            self.assertTrue(window_viewer.isWindow())

            FreeCAD._params["ViewerMode"] = "tab"
            viewer2d.apply_host_preferences()
            _pump()

            viewer = viewer2d.get_viewer()
            host = viewer2d._subwindow
            self.assertIsNot(viewer, window_viewer)
            self.assertIsInstance(host, QtWidgets.QMdiSubWindow)
            self.assertIs(host.widget(), viewer)
            self.assertTrue(host.isVisible())
            self.assertTrue(viewer.isVisible())
            self.assertTrue(host.isMaximized())
        finally:
            FreeCAD._params.pop("ViewerMode", None)

    def test_viewer_follows_freecad_palette(self):
        viewer = viewer2d.get_viewer()
        original = _main.palette()
        try:
            dark = QtGui.QPalette(original)
            dark.setColor(QtGui.QPalette.Window, QtGui.QColor("#101216"))
            _main.setPalette(dark)
            viewer.sync_application_theme(force=True)
            self.assertEqual(viewer.theme, "dark")

            light = QtGui.QPalette(original)
            light.setColor(QtGui.QPalette.Window, QtGui.QColor("#fafafa"))
            _main.setPalette(light)
            viewer.sync_application_theme(force=True)
            self.assertEqual(viewer.theme, "light")
        finally:
            _main.setPalette(original)
            viewer.sync_application_theme(force=True)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestSeparateWindowMode(unittest.TestCase):
    def setUp(self):
        import FreeCAD

        FreeCAD._params["ViewerMode"] = "window"

    def tearDown(self):
        import FreeCAD

        viewer2d.close_viewer()
        _pump()
        FreeCAD._params.pop("ViewerMode", None)

    def test_closing_the_window_ends_the_session_and_stays_closed(self):
        viewer = viewer2d.show_viewer()
        _pump()
        self.assertTrue(viewer.isWindow())
        viewer.confirm_save_or_discard = lambda *_a: True
        viewer.close()
        _pump()
        _pump()
        self.assertFalse(viewer2d.is_open())
        self.assertIsNone(viewer2d.get_viewer(create=False))
        self.assertTrue(getattr(viewer, "_shut_down", False))
        # Selecting another workbench must not resurrect the window.
        viewer2d.on_workbench_deactivated()
        _pump()
        self.assertFalse(viewer2d.is_open())
        self.assertIsNone(viewer2d.get_viewer(create=False))

    def test_cancelled_window_close_keeps_the_window(self):
        viewer = viewer2d.show_viewer()
        _pump()
        viewer.confirm_save_or_discard = lambda *_a: False
        viewer.close()
        _pump()
        self.assertTrue(viewer.isVisible())
        self.assertIs(viewer2d.get_viewer(create=False), viewer)
        del viewer.confirm_save_or_discard

    def test_separate_window_is_not_coupled_to_workbenches(self):
        stub = _WorkbenchStub("PartDesignWorkbench")
        try:
            viewer = viewer2d.show_viewer()
            _pump()
            viewer.canvas.setFocus()
            viewer.activateWindow()
            _settle()
            self.assertEqual(stub.name, "PartDesignWorkbench")
            stub.activate("PartWorkbench")          # user picks another workbench
            _settle()
            self.assertTrue(viewer2d.is_open())
            self.assertTrue(viewer.isVisible())
            self.assertEqual(stub.name, "PartWorkbench")
        finally:
            stub.remove()

    def test_opens_outside_the_mdi_area(self):
        mdi = _main.findChild(QtWidgets.QMdiArea)
        viewer = viewer2d.show_viewer()
        _pump()
        self.assertNotIn(viewer2d.TITLE, [w.windowTitle() for w in mdi.subWindowList()])
        self.assertTrue(viewer.isWindow())
        self.assertEqual(viewer.windowTitle(), viewer2d.TITLE)
        # a separate window does not need the panels out of the way
        self.assertTrue(any(w.isVisible()
                            for w in _main.findChildren(QtWidgets.QDockWidget)))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestStateRoundTrip(unittest.TestCase):
    """Switching Tab <-> Window rebuilds the widgets, so editor state has to
    survive as plain data.  0.5 added the copper stack and multi-selection to
    that snapshot; both would otherwise silently reset on every host change."""

    def setUp(self):
        from elec3d.footprint import parse_footprint_file
        from elec3d.panel2d import Viewer2D

        self.viewer = Viewer2D(theme="dark")
        self.viewer.resize(900, 600)
        self.viewer.show()
        self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.viewer.add_footprint(parse_footprint_file(SHAPES))
        self.spare = []
        _pump()

    def tearDown(self):
        for widget in [self.viewer] + self.spare:
            widget.hide()
            widget.deleteLater()
        _pump()

    def _rebuild(self, state):
        """Restore a snapshot into a fresh viewer, as a host change would."""
        from elec3d.panel2d import Viewer2D

        fresh = Viewer2D(theme="dark")
        fresh.resize(900, 600)
        fresh.show()
        self.spare.append(fresh)
        viewer2d._restore_viewer_state(fresh, state)
        _pump()
        return fresh

    def test_stack_and_layer_assignments_survive(self):
        viewer = self.viewer
        viewer._on_stack_add()
        viewer._on_stack_add()
        top = viewer.stack.top().id
        viewer.canvas.select_footprint(viewer._entries[1]["item"])
        combo = viewer.prop_layer_combo
        combo.setCurrentIndex(combo.findData(top))
        viewer._on_stack_visible(viewer.stack.layers[1].id, False)

        fresh = self._rebuild(viewer2d._snapshot_viewer_state(viewer))
        self.assertEqual([l.name for l in fresh.stack.top_down()],
                         [l.name for l in viewer.stack.top_down()])
        self.assertEqual(fresh.stack.active_id, viewer.stack.active_id)
        self.assertEqual([l.visible for l in fresh.stack],
                         [l.visible for l in viewer.stack])
        self.assertEqual([e["item"].design_layer_id for e in fresh._entries],
                         [e["item"].design_layer_id for e in viewer._entries])

    def test_multi_selection_survives(self):
        self.viewer.canvas.select_all_footprints()
        fresh = self._rebuild(viewer2d._snapshot_viewer_state(self.viewer))
        self.assertEqual(len(fresh.canvas.selected_footprints()), 2)
        self.assertEqual(fresh.prop_kind.text(), "2 objects selected")

    def test_clean_modified_baseline_survives_host_rebuild(self):
        self.viewer._capture_saved_content_state()
        self.assertFalse(self.viewer.is_modified())
        fresh = self._rebuild(viewer2d._snapshot_viewer_state(self.viewer))
        self.assertFalse(fresh.is_modified())
        self.assertFalse(fresh.windowTitle().endswith(" *"))

    def test_dirty_modified_baseline_survives_host_rebuild(self):
        self.viewer._capture_saved_content_state()
        self.viewer._entries[0]["item"].setPos(QtCore.QPointF(12.0, 3.0))
        self.assertTrue(self.viewer.is_modified())
        fresh = self._rebuild(viewer2d._snapshot_viewer_state(self.viewer))
        self.assertTrue(fresh.is_modified())
        self.assertTrue(fresh.windowTitle().endswith(" *"))

    def test_fields_expansion_survives(self):
        self.viewer._fields_expanded = True
        self.viewer.grp_fields.set_expanded(True)
        fresh = self._rebuild(viewer2d._snapshot_viewer_state(self.viewer))
        self.assertTrue(fresh._fields_expanded)

    def test_a_pre_0_5_snapshot_still_restores(self):
        # Snapshots are plain dicts that can outlive an upgrade, so a missing
        # stack/selection key must fall back rather than raise.
        self.viewer.canvas.select_footprint(self.viewer._entries[1]["item"])
        state = viewer2d._snapshot_viewer_state(self.viewer)
        for key in ("stack", "selected_indexes", "fields_expanded"):
            state.pop(key, None)
        for entry in state["entries"]:
            entry.pop("design_layer_id", None)

        fresh = self._rebuild(state)
        self.assertEqual(len(fresh._entries), 2)
        self.assertEqual(len(fresh.stack), 1)
        # The old single-index selection key is still honoured.
        self.assertEqual(len(fresh.canvas.selected_footprints()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
