# -*- coding: utf-8 -*-
"""Tests for per-face STEP colour extraction and the 3D material pipeline.

The parser half is pure Python and runs anywhere.  The half that talks to
FreeCAD's ``Part`` module is exercised through a stub, because FreeCAD is not
importable from a plain Python environment -- and because the interesting
question is how the add-on maps colours onto faces, not whether OpenCascade
tessellates correctly.

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import stepcolor                                # noqa: E402

try:
    from elec3d.qt import QtWidgets as _QtWidgetsProbe       # noqa: F401,E402
    HAVE_QT = True
except ImportError:                                           # pragma: no cover
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# A minimal but structurally faithful STEP fragment: two faces, one styled
# black and one styled silver, through the full part 21 style chain.
SAMPLE_STEP = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#17 = ADVANCED_FACE('',(#18),#52,.F.);
#57 = ADVANCED_FACE('',(#58),#135,.T.);
#99 = ADVANCED_FACE('',(#100),#150,.T.);
#812 = STYLED_ITEM('color',(#813),#17);
#813 = PRESENTATION_STYLE_ASSIGNMENT((#814));
#814 = SURFACE_STYLE_USAGE(.BOTH.,#815);
#815 = SURFACE_SIDE_STYLE('',(#816));
#816 = SURFACE_STYLE_FILL_AREA(#817);
#817 = FILL_AREA_STYLE('',(#818));
#818 = FILL_AREA_STYLE_COLOUR('',#819);
#819 = COLOUR_RGB('',0.0,0.0,0.0);
#820 = STYLED_ITEM('color',(#821),#57);
#821 = PRESENTATION_STYLE_ASSIGNMENT((#822));
#822 = SURFACE_STYLE_USAGE(.BOTH.,#823);
#823 = SURFACE_SIDE_STYLE('',(#824));
#824 = SURFACE_STYLE_FILL_AREA(#825);
#825 = FILL_AREA_STYLE('',(#826));
#826 = FILL_AREA_STYLE_COLOUR('',#827);
#827 = COLOUR_RGB('',1.0,0.5,0.25);
#828 = STYLED_ITEM('color',(#821),#99);
ENDSEC;
END-ISO-10303-21;
"""


# Solid-level styling is common in STEP files exported for KiCad packages.
# The dark package body is one solid; the light lead is a second solid; one
# body face then carries a direct red override to verify precedence.
SOLID_STYLE_STEP = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#10 = ADVANCED_FACE('',(#101),#201,.T.);
#11 = ADVANCED_FACE('',(#102),#202,.T.);
#12 = CLOSED_SHELL('',(#10,#11));
#13 = MANIFOLD_SOLID_BREP('',#12);
#20 = ADVANCED_FACE('',(#103),#203,.T.);
#21 = CLOSED_SHELL('',(#20));
#22 = MANIFOLD_SOLID_BREP('',#21);
#100 = STYLED_ITEM('body',(#1010),#13);
#1010 = PRESENTATION_STYLE_ASSIGNMENT((#1011));
#1011 = SURFACE_STYLE_USAGE(.BOTH.,#1012);
#1012 = SURFACE_SIDE_STYLE('',(#1013));
#1013 = SURFACE_STYLE_FILL_AREA(#1014);
#1014 = FILL_AREA_STYLE('',(#1015));
#1015 = FILL_AREA_STYLE_COLOUR('',#1016);
#1016 = COLOUR_RGB('',0.30,0.30,0.30);
#110 = STYLED_ITEM('lead',(#1110),#22);
#1110 = PRESENTATION_STYLE_ASSIGNMENT((#1111));
#1111 = SURFACE_STYLE_USAGE(.BOTH.,#1112);
#1112 = SURFACE_SIDE_STYLE('',(#1113));
#1113 = SURFACE_STYLE_FILL_AREA(#1114);
#1114 = FILL_AREA_STYLE('',(#1115));
#1115 = FILL_AREA_STYLE_COLOUR('',#1116);
#1116 = COLOUR_RGB('',0.734,0.773,0.797);
#120 = STYLED_ITEM('face override',(#1210),#11);
#1210 = PRESENTATION_STYLE_ASSIGNMENT((#1211));
#1211 = SURFACE_STYLE_USAGE(.BOTH.,#1212);
#1212 = SURFACE_SIDE_STYLE('',(#1213));
#1213 = SURFACE_STYLE_FILL_AREA(#1214);
#1214 = FILL_AREA_STYLE('',(#1215));
#1215 = FILL_AREA_STYLE_COLOUR('',#1216);
#1216 = COLOUR_RGB('',1.0,0.0,0.0);
ENDSEC;
END-ISO-10303-21;
"""


class TestStepColorParsing(unittest.TestCase):
    def setUp(self):
        stepcolor.clear_cache()

    def parse(self, text=SAMPLE_STEP):
        return stepcolor._colors_from_text(text)

    def test_face_colours_follow_the_style_chain(self):
        colors = self.parse()
        self.assertEqual(colors.faces[0], (0.0, 0.0, 0.0))
        self.assertEqual(colors.faces[1], (1.0, 0.5, 0.25))

    def test_face_index_is_position_in_file_order(self):
        # Callers pair this with Part.Shape.Faces, so index, not entity id, is
        # what has to line up.
        colors = self.parse()
        self.assertEqual(len(colors.faces), 3)

    def test_a_shared_style_colours_every_face_that_points_at_it(self):
        # #828 reuses #821, the style already used by face #57.
        colors = self.parse()
        self.assertEqual(colors.faces[2], (1.0, 0.5, 0.25))

    def test_dominant_is_the_most_used_colour(self):
        colors = self.parse()
        self.assertEqual(colors.dominant, (1.0, 0.5, 0.25))

    def test_unstyled_face_falls_back_to_the_dominant_colour(self):
        text = SAMPLE_STEP.replace("#828 = STYLED_ITEM('color',(#821),#99);\n", "")
        colors = stepcolor._colors_from_text(text)
        self.assertIsNone(colors.faces[2])
        # A grey hole in an otherwise black package reads as a modelling error,
        # so an unstyled face borrows the body colour instead.
        self.assertEqual(colors.color_for(2), colors.dominant)

    def test_out_of_range_index_is_safe(self):
        colors = self.parse()
        self.assertEqual(colors.color_for(999), colors.dominant)

    def test_predefined_colour_names_are_understood(self):
        text = SAMPLE_STEP.replace(
            "#819 = COLOUR_RGB('',0.0,0.0,0.0);",
            "#819 = DRAUGHTING_PRE_DEFINED_COLOUR('green');")
        colors = stepcolor._colors_from_text(text)
        self.assertEqual(colors.faces[0], (0.0, 1.0, 0.0))

    def test_a_file_without_styles_yields_nothing(self):
        text = "ISO-10303-21;\nDATA;\n#1 = ADVANCED_FACE('',(#2),#3,.T.);\nENDSEC;\n"
        colors = stepcolor._colors_from_text(text)
        self.assertFalse(colors)
        self.assertIsNone(colors.color_for(0))

    def test_garbage_input_does_not_raise(self):
        self.assertFalse(stepcolor._colors_from_text("not a step file at all"))
        self.assertFalse(stepcolor._colors_from_text(""))

    def test_missing_file_returns_an_empty_result(self):
        self.assertFalse(stepcolor.read_step_colors(
            os.path.join(DATA, "does-not-exist.step")))
        self.assertFalse(stepcolor.read_step_colors(""))

    def test_oversized_files_are_skipped_rather_than_read(self):
        path = os.path.join(DATA, "SHAPES_TEST.kicad_mod")
        self.assertFalse(stepcolor.read_step_colors(path, max_bytes=1))

    def test_results_are_cached_per_file_stamp(self):
        path = os.path.join(DATA, "SHAPES_TEST.kicad_mod")
        first = stepcolor.read_step_colors(path)
        self.assertIs(stepcolor.read_step_colors(path), first)

    def test_solid_style_is_expanded_to_all_descendant_faces(self):
        colors = stepcolor._colors_from_text(SOLID_STYLE_STEP)
        self.assertEqual(colors.faces[0], (0.30, 0.30, 0.30))
        self.assertEqual(colors.faces[2], (0.734, 0.773, 0.797))

    def test_direct_face_style_overrides_its_solid_style(self):
        colors = stepcolor._colors_from_text(SOLID_STYLE_STEP)
        self.assertEqual(colors.faces[1], (1.0, 0.0, 0.0))

    def test_solid_topology_is_retained_in_file_order(self):
        colors = stepcolor._colors_from_text(SOLID_STYLE_STEP)
        self.assertEqual(len(colors.solids), 2)
        self.assertEqual(colors.solids[0].uid, 13)
        self.assertEqual(colors.solids[0].face_indexes, [0, 1])
        self.assertEqual(colors.solids[0].color, (0.30, 0.30, 0.30))
        self.assertEqual(colors.solids[1].face_indexes, [2])
        self.assertEqual(colors.color_for_solid_face(0, 0), (0.30, 0.30, 0.30))
        self.assertEqual(colors.color_for_solid_face(0, 1), (1.0, 0.0, 0.0))

    def test_solid_styles_make_the_effective_dominant_face_colour(self):
        colors = stepcolor._colors_from_text(SOLID_STYLE_STEP)
        # The three effective face colours are body grey, direct red override,
        # and lead grey.  Ties are stable in first-seen face order.
        self.assertEqual(colors.dominant, (0.30, 0.30, 0.30))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestModelColorPipeline(unittest.TestCase):
    """The Part-facing half, driven through a stub module."""

    def setUp(self):
        stepcolor.clear_cache()
        self.step_path = os.path.join(DATA, "COLORED_TEST.step")
        with open(self.step_path, "w") as handle:
            handle.write(SAMPLE_STEP)
        self._install_part_stub(face_count=3)

    def tearDown(self):
        sys.modules.pop("Part", None)
        if os.path.exists(self.step_path):
            os.remove(self.step_path)

    def _install_part_stub(self, face_count):
        """Stand in for FreeCAD's Part module with a fixed number of faces."""

        class _Vertex(object):
            def __init__(self, x, y, z):
                self.x, self.y, self.z = x, y, z

        class _Face(object):
            def __init__(self, index):
                self.index = index

            def tessellate(self, _tol):
                base = float(self.index)
                return ([_Vertex(base, 0.0, 0.0), _Vertex(base + 1, 0.0, 0.0),
                         _Vertex(base, 1.0, 0.0)], [(0, 1, 2)])

        class _Shape(object):
            def __init__(self):
                self.Faces = [_Face(i) for i in range(face_count)]

            def tessellate(self, _tol):
                return ([_Vertex(0, 0, 0), _Vertex(1, 0, 0), _Vertex(0, 1, 0)],
                        [(0, 1, 2)])

        class _PartStub(object):
            Shape = _Shape

            @staticmethod
            def read(_path):
                return _Shape()

        sys.modules["Part"] = _PartStub()

    def _view(self):
        from elec3d.qt import QtWidgets
        from elec3d.view3d import Board3DView
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])
        return Board3DView(theme="light")

    def test_each_face_becomes_its_own_coloured_group(self):
        view = self._view()
        groups = view._model_mesh(self.step_path)
        self.assertEqual(len(groups), 3)
        colors = [group[2] for group in groups]
        self.assertEqual(colors[0], (0.0, 0.0, 0.0))
        self.assertEqual(colors[1], (1.0, 0.5, 0.25))

    def test_groups_carry_real_geometry(self):
        view = self._view()
        verts, facets, _color = view._model_mesh(self.step_path)[0]
        self.assertEqual(len(verts), 3)
        self.assertEqual(facets, [(0, 1, 2)])

    def test_a_faceless_shape_still_renders_as_one_mesh(self):
        # Some STEP files tessellate as a whole but expose no Face list; the
        # preview must fall back rather than draw nothing.
        self._install_part_stub(face_count=0)
        view = self._view()
        groups = view._model_mesh(self.step_path)
        self.assertEqual(len(groups), 1)

    def test_mesh_results_are_cached(self):
        view = self._view()
        self.assertIs(view._model_mesh(self.step_path),
                      view._model_mesh(self.step_path))

    def test_wrl_models_are_not_tessellated(self):
        view = self._view()
        self.assertIsNone(view._model_mesh("/tmp/whatever.wrl"))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestShading(unittest.TestCase):
    def test_up_facing_surfaces_are_brighter_than_down_facing_ones(self):
        from elec3d import view3d
        top = view3d._shade_rgb((0.0, 0.0, 1.0))
        bottom = view3d._shade_rgb((0.0, 0.0, -1.0))
        self.assertGreater(sum(top), sum(bottom))

    def test_the_underside_is_lit_rather_than_black(self):
        # Orbiting below the board must not show a solid black slab, which is
        # why the rig carries an upward fill light.
        from elec3d import view3d
        bottom = view3d._shade_rgb((0.0, 0.0, -1.0))
        self.assertGreater(min(bottom), 0.3)

    def test_shading_stays_close_to_the_unit_range(self):
        from elec3d import view3d
        for normal in [(0, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, -1), (-1, 0, 0)]:
            for channel in view3d._shade_rgb(normal):
                # Well above 1.0 clips every light surface to flat white.
                self.assertLess(channel, 1.25)

    def test_rgb01_to_hex_round_trips_a_known_colour(self):
        from elec3d import view3d
        self.assertEqual(view3d._rgb01_to_hex((0.0, 0.0, 0.0)), "#000000")
        self.assertEqual(view3d._rgb01_to_hex((1.0, 1.0, 1.0)), "#FFFFFF")


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestBoardSceneComposition(unittest.TestCase):
    """What the 3D scene is actually made of, independent of how it is painted."""

    def _scene(self, layer_count=1):
        from elec3d.qt import QtCore, QtWidgets
        from elec3d.design import LayerStack
        from elec3d.footprint import parse_footprint_file
        from elec3d.view3d import Board3DView
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])

        stack = LayerStack()
        for _ in range(layer_count - 1):
            stack.add()
        fp = parse_footprint_file(os.path.join(DATA, "NE555DR.kicad_mod"))
        entries = [{"uid": 0, "fp": fp, "pos": QtCore.QPointF(0.0, 0.0),
                    "rotation": 0.0, "design_layer_id": stack.layers[-1].id,
                    "layer_visibility": {}}]
        view = Board3DView(theme="light")
        view.rebuild(entries, stack, solid=None, model_search_dir="")
        return view

    def test_design_layers_do_not_fill_the_board_with_their_colour(self):
        from elec3d import view3d
        view = self._scene(layer_count=3)
        colors = {tri[3] for tri in view.canvas.triangles}
        # Filling each plane stacked translucent coloured sheets over the whole
        # board, tinting the substrate and every component with the layer
        # colour.  Only the block and the pads should be large filled surfaces.
        self.assertIn(view3d.BOARD_BLOCK, colors)
        self.assertNotIn(view3d.PLANE_EDGE, colors)

    def test_each_visible_plane_contributes_an_outline(self):
        from elec3d import view3d
        view = self._scene(layer_count=3)
        edges = [line for line in view.canvas.lines
                 if line[1] == view3d.PLANE_EDGE]
        self.assertEqual(len(edges), 3)
        # A closed rectangle: five points, first and last coincident.
        self.assertEqual(len(edges[0][0]), 5)
        self.assertEqual(edges[0][0][0], edges[0][0][-1])

    def test_the_board_block_is_translucent(self):
        from elec3d import view3d
        view = self._scene()
        alphas = {tri[4] for tri in view.canvas.triangles
                  if tri[3] == view3d.BOARD_BLOCK}
        # Opaque substrate would hide every component on an inner or bottom
        # layer, which is the whole point of drawing a stack.
        self.assertTrue(all(0.0 < a < 0.5 for a in alphas))

    def test_imported_solid_is_the_substrate_and_planes_follow_its_sections(self):
        from elec3d.qt import QtCore, QtWidgets
        from elec3d.design import LayerStack
        from elec3d import boardsolid, view3d
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])
        stack = LayerStack()
        top = stack.add()
        stack.set_bottom_margin(0.5)
        stack.set_separation(stack.bottom().id, top.id, 1.0)
        a = [(-10, -5), (10, -5), (10, 5), (-10, 5)]
        b = [(-5, -5), (5, -5), (5, 5), (-5, 5)]
        v, t = boardsolid._prism_mesh_from_sections([(0.0, [a]), (1.0, [a]), (2.0, [b])])
        solid = boardsolid.BoardSolid(v, t)
        view = view3d.Board3DView(theme="light")
        view.rebuild([], stack, solid=solid, model_search_dir="")
        block = [tri for tri in view.canvas.triangles if tri[3] == view3d.BOARD_BLOCK]
        self.assertEqual(len(block), len(solid.triangles))
        # Bottom plane (z 0.5) is 20 mm wide; top plane (z 1.5) is mid-taper: 15 mm.
        planes = {}
        for points, color, _width in view.canvas.lines:
            if len(points) == 5 and abs(points[0][2] - points[-1][2]) < 1e-9:
                planes.setdefault(round(points[0][2], 6), points)
        widths = dict((z, max(p[0] for p in pts) - min(p[0] for p in pts))
                      for z, pts in planes.items())
        self.assertAlmostEqual(widths[0.5], 20.0, places=6)
        self.assertAlmostEqual(widths[1.5], 15.0, places=6)

    def test_hidden_planes_contribute_no_outline(self):
        from elec3d.qt import QtCore, QtWidgets
        from elec3d.design import LayerStack
        from elec3d.footprint import parse_footprint_file
        from elec3d import view3d
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])

        stack = LayerStack()
        stack.add()
        fp = parse_footprint_file(os.path.join(DATA, "NE555DR.kicad_mod"))
        entries = [{"uid": 0, "fp": fp, "pos": QtCore.QPointF(0.0, 0.0),
                    "rotation": 0.0, "design_layer_id": stack.layers[-1].id,
                    "layer_visibility": {}}]
        view = view3d.Board3DView(theme="light")
        view.rebuild(entries, stack, solid=None, model_search_dir="")
        self.assertEqual(
            len([l for l in view.canvas.lines if l[1] == view3d.PLANE_EDGE]), 2)

        stack.layers[0].visible = False
        view.rebuild(entries, stack, solid=None, model_search_dir="", force=True)
        self.assertEqual(
            len([l for l in view.canvas.lines if l[1] == view3d.PLANE_EDGE]), 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestModelSourcesWithoutANetlist(unittest.TestCase):
    """A plain footprint import must still be able to reach its 3D models.

    Before 0.7 the models folder was set only by the netlist wizard, so
    importing a ``.kicad_mod`` directly could never show a package body no
    matter what the file's ``(model ...)`` line pointed at.
    """

    def setUp(self):
        from elec3d.qt import QtWidgets
        from elec3d.panel2d import Viewer2D
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])
        self.viewer = Viewer2D(theme="light", hosted=True)
        self.viewer.resize(900, 600)
        self.viewer.show()

    def tearDown(self):
        self.viewer.hide()
        self.viewer.deleteLater()

    def _load(self, name="NE555DR.kicad_mod"):
        from elec3d.footprint import parse_footprint_file
        return self.viewer.add_footprint(
            parse_footprint_file(os.path.join(DATA, name)))

    def test_the_models_folder_action_exists_outside_the_wizard(self):
        labels = [a.text() for a in self.viewer.menu_file.actions() if a.text()]
        self.assertIn("3D models folder\u2026", labels)

    def test_setting_the_folder_reaches_the_3d_view(self):
        self._load()
        self.viewer.set_models_folder(DATA)
        self.assertEqual(self.viewer.models_folder, os.path.abspath(DATA))
        self.assertEqual(self.viewer.view3d._model_dir, os.path.abspath(DATA))

    def test_a_nonexistent_folder_is_rejected_rather_than_stored(self):
        self.viewer.set_models_folder("/no/such/folder/anywhere")
        self.assertEqual(self.viewer.models_folder, "")

    def test_per_selection_model_action_needs_exactly_one_footprint(self):
        first = self._load()
        second = self._load("SHAPES_TEST.kicad_mod")
        self.viewer.canvas.select_footprint(first)
        self.assertTrue(self.viewer.act_model_one.isEnabled())
        self.viewer.canvas.select_all_footprints()
        # "Load 3D model for selection" acts on one footprint, so a
        # multi-selection must not offer it.
        self.assertFalse(self.viewer.act_model_one.isEnabled())
        self.viewer.canvas.select_footprint(None)
        self.assertFalse(self.viewer.act_model_one.isEnabled())

    def test_assigning_a_model_to_a_footprint_that_has_none(self):
        from elec3d import footprint as fpmod
        item = self._load("SHAPES_TEST.kicad_mod")
        entry = self.viewer.entry_for_item(item)
        entry["fp"].model = None
        # Stand in for the file dialog, which cannot run headless.
        target = os.path.join(DATA, "NE555DR.kicad_mod")
        entry["fp"].model = fpmod.Model(target, (0.0, 0.0, 0.0),
                                        (1.0, 1.0, 1.0), (0.0, 0.0, 0.0))
        self.assertEqual(entry["fp"].model.path, target)

    def test_replacing_a_path_keeps_the_placement_the_footprint_declared(self):
        from elec3d import footprint as fpmod
        model = fpmod.Model("old.step", (1.0, 2.0, 3.0), (2.0, 2.0, 2.0), (0.0, 90.0, 0.0))
        model.path = "new.step"
        # The file was wrong, not the offset/scale/rotation KiCad recorded.
        self.assertEqual(model.offset, (1.0, 2.0, 3.0))
        self.assertEqual(model.scale, (2.0, 2.0, 2.0))
        self.assertEqual(model.rotate, (0.0, 90.0, 0.0))

    def test_model_reference_stats_reports_what_resolved(self):
        self._load()
        self.viewer.set_models_folder(DATA)
        total, found = self.viewer.model_reference_stats()
        self.assertGreaterEqual(total, 0)
        self.assertLessEqual(found, total)
