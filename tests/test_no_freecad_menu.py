# -*- coding: utf-8 -*-
"""The workbench adds nothing to FreeCAD's own menus; its commands are only
called from the editor's File/Edit menus through ``Gui.runCommand``."""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source(name):
    with open(os.path.join(ROOT, "elec3d", name), encoding="utf-8") as fh:
        return fh.read()


class TestNoFreeCADMenu(unittest.TestCase):
    def test_workbench_adds_no_menu_toolbar_or_context_menu(self):
        text = _source("workbench.py")
        for call in ("appendMenu", "appendToolbar", "appendContextMenu", "def ContextMenu"):
            self.assertNotIn(call, text)

    def test_only_editor_backed_commands_are_registered(self):
        text = _source("commands.py")
        self.assertNotIn("Elec3D_View2D", text)
        self.assertNotIn("Elec3D_FitView", text)
        self.assertNotIn('"Checkable"', text)
        hosted = _source("viewer.py")
        for name in ("Elec3D_Import", "Elec3D_ImportNetlist", "Elec3D_Reload", "Elec3D_Options"):
            self.assertIn('"%s"' % name, text)
            self.assertIn('"%s"' % name, hosted)

    def test_every_command_tolerates_freecads_extra_argument(self):
        tree = ast.parse(_source("commands.py"))
        found = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "Activated":
                found += 1
                self.assertIsNotNone(node.args.vararg, "Activated must accept *args")
        self.assertEqual(found, 4)


if __name__ == "__main__":
    unittest.main()
