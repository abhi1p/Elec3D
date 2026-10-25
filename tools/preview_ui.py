# -*- coding: utf-8 -*-
"""Run the 2D viewer outside FreeCAD.

    python3 tools/preview_ui.py tests/data/NE555DR.kicad_mod
    python3 tools/preview_ui.py tests/data/NE555DR.kicad_mod --shot out.png

Needs PySide6 (or PySide2) on the path.  Handy for working on the UI without
restarting FreeCAD every time; ``--shot`` renders the window offscreen to a PNG,
which also works on a headless machine with ``QT_QPA_PLATFORM=offscreen``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d.footprint import parse_footprint_file       # noqa: E402
from elec3d.panel2d import Viewer2D                     # noqa: E402
from elec3d.qt import QtCore, QtGui, QtWidgets          # noqa: E402


def main(argv):
    paths, shot, theme, size = [], None, "dark", (1280, 760)
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--shot":
            i += 1
            shot = argv[i]
        elif arg == "--light":
            theme = "light"
        elif arg == "--size":
            i += 1
            size = tuple(int(v) for v in argv[i].split("x"))
        else:
            paths.append(arg)
        i += 1
    if not paths:
        print(__doc__)
        return 1

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = Viewer2D(theme=theme)
    viewer.resize(*size)
    for path in paths:
        viewer.add_footprint(parse_footprint_file(path))
    viewer.show()

    if shot:
        viewer.canvas.fit()
        app.processEvents()
        QtCore.QCoreApplication.processEvents()
        pixmap = viewer.grab()
        pixmap.save(shot)
        print("wrote", shot, pixmap.width(), "x", pixmap.height())
        return 0

    return app.exec() if hasattr(app, "exec") else app.exec_()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
