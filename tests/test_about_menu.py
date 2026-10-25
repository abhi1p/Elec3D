# -*- coding: utf-8 -*-
"""About menu (bug report, GitHub) and File > Open Project Folder."""

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import ISSUES_URL, REPOSITORY_URL, __version__   # noqa: E402

try:
    from elec3d.qt import QtCore, QtWidgets
    from elec3d.panel2d import Viewer2D
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    HAVE_QT = False

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestAboutMenu(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D(theme="dark", hosted=True)
        self.opened = []
        self.viewer._open_url = lambda url: self.opened.append(
            url.toString() if hasattr(url, "toString") else url) or True

    def tearDown(self):
        self.viewer.deleteLater()

    def test_repository_matches_package_metadata(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "package.xml"), encoding="utf-8") as fh:
            self.assertIn(">%s</url>" % REPOSITORY_URL, fh.read())

    def test_menu_entries(self):
        texts = [a.text() for a in self.viewer.menu_about.actions() if a.text()]
        self.assertEqual(texts, ["Report a Bug…", "Elec3D on GitHub", "About Elec3D…"])
        self.assertEqual(self.viewer.btn_about.text().split()[0], "About")

    def _query(self):
        url = QtCore.QUrl(self.opened[-1])
        items = dict(QtCore.QUrlQuery(url).queryItems())
        return dict((k, QtCore.QUrl.fromPercentEncoding(v.encode("utf-8")))
                    for k, v in items.items())

    def test_report_bug_opens_the_issue_form_prefilled(self):
        self.assertTrue(self.viewer.report_bug())
        self.assertEqual(self.opened[-1].split("?", 1)[0], ISSUES_URL + "/new")
        query = self._query()
        self.assertEqual(query["template"], "bug_report.yml")
        self.assertEqual(query["labels"], "bug")
        self.assertEqual(query["version"], __version__)
        self.assertIn("- Qt:", query["environment"])
        self.assertIn("- OS:", query["environment"])
        # Fallback body for a repository without the form.
        self.assertIn("### Steps to reproduce", query["body"])
        self.assertIn("- Elec3D: %s" % __version__, query["body"])

    def test_prefilled_fields_exist_in_the_issue_form(self):
        try:
            import yaml
        except ImportError:                                 # pragma: no cover
            self.skipTest("PyYAML not installed")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, ".github", "ISSUE_TEMPLATE", self.viewer.BUG_TEMPLATE)
        with open(path, encoding="utf-8") as fh:
            form = yaml.safe_load(fh)
        ids = set(item.get("id") for item in form["body"] if item.get("id"))
        self.viewer.report_bug()
        for key in self._query():
            if key not in ("template", "labels", "title", "body"):
                self.assertIn(key, ids)
        self.assertEqual(form["labels"], ["bug"])

    def test_github_entry_opens_the_repository(self):
        self.viewer.act_github.trigger()
        self.assertEqual(self.opened[-1], REPOSITORY_URL)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestOpenProjectFolder(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D(theme="dark", hosted=True)
        self.opened = []
        self.viewer._open_url = lambda url: self.opened.append(url) or True
        self.tmp = tempfile.mkdtemp(prefix="e3d-folder-")

    def tearDown(self):
        self.viewer.deleteLater()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_until_the_design_has_a_file(self):
        self.viewer._refresh_window_title()
        self.assertFalse(self.viewer.act_open_project_folder.isEnabled())
        self.assertFalse(self.viewer.open_project_folder())
        self.assertEqual(self.opened, [])

    def test_opens_the_folder_holding_the_project(self):
        project = os.path.join(self.tmp, "demo", "demo.e3d_pro")
        os.makedirs(os.path.dirname(project))
        self.viewer._project_file = project
        self.viewer._refresh_window_title()
        self.assertTrue(self.viewer.act_open_project_folder.isEnabled())
        self.viewer.act_open_project_folder.trigger()
        self.assertEqual(self.opened[-1].toLocalFile(), os.path.dirname(project).replace(os.sep, "/"))
