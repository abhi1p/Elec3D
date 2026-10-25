import unittest

from elec3d.design import LayerStack

try:
    from elec3d.view3d import layer_heights
    HAVE_VIEW3D = True
except ImportError:
    layer_heights = None
    HAVE_VIEW3D = False


@unittest.skipUnless(HAVE_VIEW3D, "no PySide binding available for view3d import")
class TestReferencePlaneHeights(unittest.TestCase):
    def test_separation_is_exact_reference_plane_distance(self):
        stack = LayerStack()
        middle = stack.add()
        top = stack.add()
        bottom = stack.bottom()
        stack.set_bottom_margin(0.4)
        stack.set_separation(bottom.id, middle.id, 0.8)
        stack.set_separation(middle.id, top.id, 1.3)
        stack.set_copper_thickness(bottom.id, 0.200)
        stack.set_copper_thickness(middle.id, 0.050)
        stack.set_copper_thickness(top.id, 0.500)

        z, total = layer_heights(stack)
        self.assertAlmostEqual(z[bottom.id], 0.4)
        self.assertAlmostEqual(z[middle.id] - z[bottom.id], 0.8)
        self.assertAlmostEqual(z[top.id] - z[middle.id], 1.3)
        # No top margin: the second value is the top reference plane height.
        self.assertAlmostEqual(total, 0.4 + 0.8 + 1.3)

    def test_changing_copper_thickness_does_not_change_reference_heights(self):
        stack = LayerStack()
        top = stack.add()
        before, total_before = layer_heights(stack)
        stack.set_copper_thickness(stack.bottom().id, 0.9)
        stack.set_copper_thickness(top.id, 0.4)
        after, total_after = layer_heights(stack)
        self.assertEqual(before, after)
        self.assertAlmostEqual(total_before, total_after)


if __name__ == "__main__":
    unittest.main()
