# -*- coding: utf-8 -*-
"""Parser tests that run in a plain Python interpreter (no FreeCAD needed).

    cd Elec3D && python3 -m unittest discover tests
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import sexpr                       # noqa: E402
from elec3d.footprint import parse_footprint, parse_footprint_file   # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")


class TestSexpr(unittest.TestCase):
    def test_nested(self):
        self.assertEqual(sexpr.parse('(a b (c "d e") 1.5)'),
                         ["a", "b", ["c", "d e"], "1.5"])

    def test_escapes_and_comments(self):
        self.assertEqual(sexpr.parse(r'(a "say \"hi\"")'), ["a", 'say "hi"'])

    def test_unbalanced(self):
        with self.assertRaises(sexpr.SExprError):
            sexpr.parse("(a (b)")


class TestSampleFootprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fp = parse_footprint_file(SAMPLE)

    def test_header(self):
        self.assertEqual(self.fp.name, "NE555DR")
        self.assertEqual(self.fp.descr, "D (R-PDSO-G8)")
        self.assertEqual(self.fp.tags, "Integrated Circuit")
        self.assertEqual(self.fp.attr, "smd")

    def test_pads(self):
        self.assertEqual(len(self.fp.pads), 8)
        pad1 = self.fp.pads[0]
        self.assertEqual(pad1.number, "1")
        self.assertEqual((pad1.pad_type, pad1.shape), ("smd", "rect"))
        self.assertAlmostEqual(pad1.x, -2.712)
        self.assertAlmostEqual(pad1.y, -1.905)
        self.assertAlmostEqual(pad1.rot, 90.0)
        self.assertAlmostEqual(pad1.w, 0.65)
        self.assertAlmostEqual(pad1.h, 1.525)
        self.assertEqual(pad1.layers, ["F.Cu", "F.Paste"])
        self.assertIsNone(pad1.drill)
        self.assertFalse(pad1.is_through_hole)

    def test_graphics_split_by_layer(self):
        silk = [l for l in self.fp.lines if l.layer == "F.SilkS"]
        dwgs = [l for l in self.fp.lines if l.layer == "Dwgs.User"]
        self.assertEqual(len(silk), 5)
        self.assertEqual(len(dwgs), 9)
        self.assertAlmostEqual(silk[0].width, 0.2)

    def test_texts(self):
        kinds = sorted(t.kind for t in self.fp.texts)
        self.assertEqual(kinds, ["reference", "value"])
        ref = [t for t in self.fp.texts if t.kind == "reference"][0]
        self.assertEqual(ref.text, "IC**")
        self.assertFalse(ref.hide)
        val = [t for t in self.fp.texts if t.kind == "value"][0]
        self.assertTrue(val.hide)          # (hide yes)

    def test_model(self):
        self.assertTrue(self.fp.model.path.endswith("NE555DR.stp"))
        self.assertEqual(self.fp.model.offset, (0.5, -0.5, -0.305))

    def test_bbox(self):
        minx, miny, maxx, maxy = self.fp.bbox()
        self.assertAlmostEqual(minx, -3.725)
        self.assertAlmostEqual(maxx, 3.725)
        self.assertAlmostEqual(miny, -2.75)
        self.assertAlmostEqual(maxy, 2.75)


class TestFormatVariants(unittest.TestCase):
    def test_legacy_module_and_arc(self):
        src = """
        (module TEST (layer F.Cu)
          (fp_text reference REF** (at 0 1) (layer F.SilkS) hide)
          (fp_arc (start 0 0) (end 1 0) (angle 90) (layer F.SilkS) (width 0.12))
          (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8)
               (layers *.Cu *.Mask))
          (model foo.step (at (xyz 0 0 0.1)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
        )"""
        fp = parse_footprint(src)
        self.assertEqual(fp.name, "TEST")
        self.assertTrue(fp.texts[0].hide)            # bare 'hide' flag

        arc = fp.arcs[0]
        self.assertEqual(arc.start, (1.0, 0.0))      # legacy: start = centre
        self.assertAlmostEqual(arc.end[0], 0.0, places=6)
        self.assertAlmostEqual(arc.end[1], 1.0, places=6)
        self.assertAlmostEqual(math.hypot(*arc.mid), 1.0, places=6)

        pad = fp.pads[0]
        self.assertTrue(pad.is_through_hole)
        self.assertEqual(pad.copper_layer, "*.Cu")
        self.assertAlmostEqual(pad.drill.w, 0.8)
        self.assertFalse(pad.drill.oval)

        # legacy model offsets are inches
        self.assertAlmostEqual(fp.model.offset[2], 2.54)

    def test_oval_drill_and_roundrect(self):
        src = """
        (footprint "T2"
          (pad "A1" thru_hole oval (at 1 2 45) (size 2 3)
            (drill oval 1 1.5 (offset 0.1 -0.2)) (layers "*.Cu"))
          (pad "A2" smd roundrect (at 0 0) (size 1 1) (roundrect_rratio 0.2)
            (layers "F.Cu"))
          (fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0.1) (type solid))
            (fill yes) (layer "F.Fab"))
        )"""
        fp = parse_footprint(src)
        drill = fp.pads[0].drill
        self.assertTrue(drill.oval)
        self.assertAlmostEqual(drill.w, 1.0)
        self.assertAlmostEqual(drill.h, 1.5)
        self.assertAlmostEqual(drill.offx, 0.1)
        self.assertAlmostEqual(fp.pads[1].rratio, 0.2)
        self.assertTrue(fp.polys[0].filled)
        self.assertEqual(len(fp.polys[0].points), 3)

    def test_not_a_footprint(self):
        with self.assertRaises(ValueError):
            parse_footprint('(kicad_pcb (version 1))')


if __name__ == "__main__":
    unittest.main(verbosity=2)
