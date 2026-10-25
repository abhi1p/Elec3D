# -*- coding: utf-8 -*-
"""Components shown as 3D model or bounding box, and FreeCAD export targets."""

import os
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import designfile, export3d                    # noqa: E402
from elec3d.footprint import parse_footprint_file          # noqa: E402

try:
    from elec3d.qt import QtCore, QtWidgets
    from elec3d import view3d
    from elec3d.design import LayerStack
    from elec3d.panel2d import Viewer2D, Export3DDialog
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")
_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# A 2 x 4 x 1 mm "model" (two triangles per face are not needed: one facet
# per side is enough to give it extents) with a non-neutral colour.
_FAKE_MESH = [([(-1.0, -2.0, 0.0), (1.0, -2.0, 0.0), (1.0, 2.0, 0.0), (-1.0, 2.0, 1.0)],
               [(0, 1, 2), (0, 2, 3)], (0.2, 0.2, 0.2))]


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestBoxRendering(unittest.TestCase):
    def _view(self, display, rotation=0.0, margin=None):
        stack = LayerStack()
        fp = parse_footprint_file(SAMPLE)
        entry = {"uid": "u1", "fp": fp, "pos": QtCore.QPointF(5.0, 3.0), "rotation": rotation,
                 "design_layer_id": stack.layers[-1].id, "layer_visibility": {},
                 "model_adjust": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "model_adjust_mode": "manual", "model_display": display,
                 "box_margin": dict(margin or {})}
        view = view3d.Board3DView(theme="light")
        view._resolved_model = lambda model: "/fake/model.step"
        view._model_mesh = lambda path: _FAKE_MESH
        view.rebuild([entry], stack, solid=None, model_search_dir="")
        return view

    def _model_triangles(self, view):
        return [t for t in view.canvas.triangles if t[3] == "#333333"]

    def test_box_replaces_the_mesh_with_twelve_triangles(self):
        model = self._view("model")
        box = self._view("box")
        self.assertEqual(len(self._model_triangles(model)), 2)
        self.assertEqual(len(self._model_triangles(box)), 12)

    def test_box_spans_the_model_and_keeps_alignment_bounds(self):
        model = self._view("model")
        box = self._view("box")
        self.assertEqual(model._model_bounds["u1"], box._model_bounds["u1"])
        pts = [p for t in self._model_triangles(box) for p in t[:3]]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        self.assertAlmostEqual(max(xs) - min(xs), 2.0)
        self.assertAlmostEqual(max(ys) - min(ys), 4.0)
        self.assertAlmostEqual(max(zs) - min(zs), 1.0)

    def _extents(self, view):
        pts = [p for t in self._model_triangles(view) for p in t[:3]]
        return [(min(p[i] for p in pts), max(p[i] for p in pts)) for i in range(3)]

    def test_margins_grow_top_and_sides_but_not_bottom(self):
        plain = self._extents(self._view("box"))
        grown = self._extents(self._view("box", margin={"top": 0.5, "side": 0.25}))
        for axis in (0, 1):
            self.assertAlmostEqual(grown[axis][0], plain[axis][0] - 0.25)
            self.assertAlmostEqual(grown[axis][1], plain[axis][1] + 0.25)
        self.assertAlmostEqual(grown[2][0], plain[2][0])          # underside unchanged
        self.assertAlmostEqual(grown[2][1], plain[2][1] + 0.5)

    def test_margins_do_not_affect_the_model_or_alignment(self):
        model = self._view("model", margin={"top": 1.0, "side": 1.0})
        self.assertEqual(len(self._model_triangles(model)), 2)
        box = self._view("box", margin={"top": 1.0, "side": 1.0})
        self.assertEqual(model._model_bounds["u1"], box._model_bounds["u1"])

    def test_box_rotates_with_the_footprint(self):
        box = self._view("box", rotation=90.0)
        pts = [p for t in self._model_triangles(box) for p in t[:3]]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        self.assertAlmostEqual(max(xs) - min(xs), 4.0)
        self.assertAlmostEqual(max(ys) - min(ys), 2.0)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestPanelModelDisplay(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D(theme="dark")
        self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.viewer.add_footprint(parse_footprint_file(SAMPLE))

    def tearDown(self):
        self.viewer.deleteLater()

    def modes(self):
        return [e.get("model_display") for e in self.viewer._entries]

    def test_new_components_show_their_model(self):
        self.assertEqual(self.modes(), ["model", "model"])

    def test_toolbar_toggle_switches_all_and_is_one_undo_step(self):
        self.viewer.view3d.modelDisplayRequested.emit("box")
        self.assertEqual(self.modes(), ["box", "box"])
        self.assertTrue(self.viewer.view3d.btn_boxes.isChecked())
        self.viewer.undo()
        self.assertEqual(self.modes(), ["model", "model"])
        self.assertFalse(self.viewer.view3d.btn_boxes.isChecked())
        self.viewer.redo()
        self.assertEqual(self.modes(), ["box", "box"])

    def test_single_component_override(self):
        first = self.viewer._entries[0]
        self.viewer._set_model_display([first], "box")
        self.assertEqual(self.modes(), ["box", "model"])
        self.assertFalse(self.viewer.view3d.btn_boxes.isChecked())
        states = self.viewer._export_entries()
        self.assertEqual([s["model_display"] for s in states], ["box", "model"])

    def test_margins_are_one_undo_step_and_only_for_boxes(self):
        first, second = self.viewer._entries
        self.viewer._set_model_display([first], "box")
        self.assertTrue(self.viewer._set_box_margins([first], top=0.4, side=0.2))
        self.assertEqual(first["box_margin"], {"top": 0.4, "side": 0.2})
        self.assertFalse(self.viewer._set_box_margins([first], top=0.4))   # no change
        self.viewer.undo()
        self.assertEqual(first.get("box_margin"), {"top": 0.0, "side": 0.0})
        self.assertEqual(first["model_display"], "box")
        self.viewer.redo()
        self.assertEqual(first["box_margin"], {"top": 0.4, "side": 0.2})
        self.assertEqual(self.viewer._export_entries()[0]["box_margin"],
                         {"top": 0.4, "side": 0.2})
        self.assertFalse(second.get("box_margin"))

    def test_negative_margin_is_clamped(self):
        from elec3d.design import box_margins
        self.assertEqual(box_margins({"box_margin": {"top": -1, "side": "x"}}), (0.0, 0.0))
        self.assertEqual(box_margins({"box_margin": {"top": -1, "side": 2}}), (0.0, 2.0))
        self.assertEqual(box_margins({}), (0.0, 0.0))

    def test_choice_survives_save_and_load(self):
        import tempfile, shutil
        self.viewer._set_model_display([self.viewer._entries[1]], "box")
        self.viewer._set_box_margins([self.viewer._entries[1]], top=1.5, side=0.3)
        folder = tempfile.mkdtemp(prefix="e3d-display-")
        try:
            path = os.path.join(folder, "b.e3d_pcb")
            designfile.write_file(path, self.viewer._design_snapshot(path))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
                self.assertEqual(text.count('(model_display "box")'), 1)
                self.assertEqual(text.count("(box_margin (top 1.5) (side 0.3))"), 1)
            fresh = Viewer2D(theme="dark")
            try:
                fresh.load_design_from(path)
                self.assertEqual([e.get("model_display") for e in fresh._entries],
                                 ["model", "box"])
                self.assertEqual(fresh._entries[1]["box_margin"], {"top": 1.5, "side": 0.3})
                self.assertFalse(fresh._entries[0].get("box_margin"))
            finally:
                fresh.deleteLater()
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class _BB(object):
    XMin, YMin, ZMin, XLength, YLength, ZLength = -1.0, -2.0, 0.0, 2.0, 4.0, 1.0


class _StubShape(object):
    def optimalBoundingBox(self, *_a):
        return _BB()


class TestExportBoxMargins(unittest.TestCase):
    def test_exported_box_grows_like_the_view(self):
        calls = []
        part = types.SimpleNamespace(makeBox=lambda *a: calls.append(a) or "BOX")
        app = types.SimpleNamespace(Vector=lambda *v: tuple(v))
        self.assertEqual(export3d._bounding_box_shape(part, app, _StubShape(), 0.5, 0.25), "BOX")
        w, d, h, base = calls[-1]
        self.assertAlmostEqual(w, 2.5); self.assertAlmostEqual(d, 4.5); self.assertAlmostEqual(h, 1.5)
        self.assertEqual(base, (-1.25, -2.25, 0.0))
        export3d._bounding_box_shape(part, app, _StubShape())
        self.assertEqual(calls[-1][:3], (2.0, 4.0, 1.0))


class _Obj(object):
    def __init__(self, doc, kind, name):
        self.doc, self.TypeId, self.Name, self.Label = doc, kind, name, ""
        self.Group, self.PropertiesList = [], []

    def addObject(self, obj):
        self.Group.append(obj)

    def addProperty(self, _type, name, *_a):
        self.PropertiesList.append(name)
        setattr(self, name, None)


class _Doc(object):
    def __init__(self, name):
        self.Name = self.Label = name
        self.Objects = []

    def addObject(self, kind, name):
        base, n, unique = name, 1, name
        while any(o.Name == unique for o in self.Objects):
            unique = "%s%03d" % (base, n); n += 1
        obj = _Obj(self, kind, unique)
        self.Objects.append(obj)
        return obj

    def getObject(self, name):
        return next((o for o in self.Objects if o.Name == name), None)

    def removeObject(self, name):
        obj = self.getObject(name)
        self.Objects.remove(obj)
        for o in self.Objects:
            if obj in o.Group:
                o.Group.remove(obj)

    def recompute(self):
        pass


class TestFreeCADTargets(unittest.TestCase):
    def setUp(self):
        self.docs = {}
        app = types.ModuleType("FreeCAD")
        app.listDocuments = lambda: dict(self.docs)
        app.getDocument = lambda n: self.docs[n]

        def new(name):
            self.docs[name] = _Doc(name)
            return self.docs[name]
        app.newDocument = new
        app.setActiveDocument = lambda n: None
        app.closeDocument = lambda n: self.docs.pop(n, None)
        part = types.ModuleType("Part")
        self._saved = {k: sys.modules.get(k) for k in ("FreeCAD", "Part", "FreeCADGui")}
        sys.modules["FreeCAD"], sys.modules["Part"] = app, part
        sys.modules.pop("FreeCADGui", None)
        self._build = export3d.build_records
        export3d.build_records = lambda *a, **k: (
            [export3d._record("SHAPE", "Board_Part", "#FFFFFF", "Board_Part")], [])

    def tearDown(self):
        export3d.build_records = self._build
        for key, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod

    def export(self, **kw):
        return export3d.create_freecad_document([], None, None, [], [], "", {},
                                                source_name="board.e3d_pro", **kw)

    def roots(self, doc):
        return [o for o in doc.Objects if o.TypeId == "App::Part"]

    def test_new_add_and_replace(self):
        first = self.export(mode="new")
        doc = first["document"]
        self.assertTrue(first["created"])
        self.assertEqual(len(self.roots(doc)), 1)

        added = self.export(mode="add", target_document=doc.Name)
        self.assertIs(added["document"], doc)
        self.assertTrue(added["added"])
        self.assertEqual(len(self.roots(doc)), 2)          # same tab, two exports
        self.assertEqual(len(self.docs), 1)

        replaced = self.export(mode="update", target_document=doc.Name,
                               target_root=added["root"].Name)
        self.assertIs(replaced["root"], added["root"])
        self.assertEqual(len(self.roots(doc)), 2)          # replaced, not appended
        self.assertEqual(len(replaced["root"].Group), 1)
        self.assertEqual(len(self.roots(doc)[0].Group), 1)  # first export untouched

    def test_closed_target_falls_back_to_a_new_document(self):
        result = self.export(mode="add", target_document="Gone")
        self.assertTrue(result["created"])
        self.assertTrue(any("no longer open" in w for w in result["warnings"]))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestExportDialogTargets(unittest.TestCase):
    def test_chosen_target_is_returned(self):
        targets = [("update", "Doc1", "Replace previous export in Doc1"),
                   ("add", "Doc1", "Add as a new part to Doc1"),
                   ("add", "Doc2", "Add as a new part to Doc2"),
                   ("new", "", "New FreeCAD document")]
        dialog = Export3DDialog({"models": 2, "model_boxes": 1}, {}, "freecad",
                                targets=targets, default_target=("add", "Doc2"))
        try:
            self.assertEqual(dialog.selected_options()["freecad_target"],
                             {"mode": "add", "document": "Doc2"})
            self.assertEqual(dialog.go.text(), "Add to FreeCAD")
            dialog.target_combo.setCurrentIndex(0)
            self.assertEqual(dialog.go.text(), "Replace in FreeCAD")
            self.assertNotIn("update_existing", dialog.selected_options())
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
