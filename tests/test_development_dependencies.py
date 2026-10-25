# -*- coding: utf-8 -*-
"""Packaging checks for standalone GUI test dependencies."""

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, "requirements-test.txt")


class TestDevelopmentDependencies(unittest.TestCase):
    def test_test_requirements_declares_pyside6(self):
        self.assertTrue(os.path.isfile(REQ))
        with open(REQ, "r", encoding="utf-8") as fh:
            requirements = [
                line.strip()
                for line in fh
                if line.strip() and not line.lstrip().startswith("#")
            ]
        self.assertTrue(
            any(req.lower().startswith("pyside6") for req in requirements),
            "requirements-test.txt must declare PySide6",
        )

    def test_pyside_is_not_a_runtime_package_dependency(self):
        package_xml = os.path.join(ROOT, "package.xml")
        with open(package_xml, "r", encoding="utf-8") as fh:
            metadata = fh.read().lower()
        self.assertNotIn("<depend>pyside", metadata)
        self.assertNotIn("<depend>pyside6", metadata)


if __name__ == "__main__":
    unittest.main(verbosity=2)
