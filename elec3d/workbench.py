# -*- coding: utf-8 -*-
"""Elec3D workbench.

The workbench is centred on a custom Qt 2D/3D viewer.  Activating it does not
create, activate, or modify a FreeCAD document.  FreeCAD geometry is generated
only when the user explicitly requests it from the viewer.
"""

import os

import FreeCAD
import FreeCADGui

from . import ICON_DIR
from .qt import QtCore


class Elec3DWorkbench(FreeCADGui.Workbench):
    """View and edit KiCad footprints in a document-free custom 2D/3D UI."""

    MenuText = "Elec3D"
    ToolTip = "Open KiCad .kicad_mod footprints in a custom 2D/3D viewer"
    Icon = os.path.join(ICON_DIR, "Elec3D.svg")

    def Initialize(self):
        from . import commands
        from . import wbtrack

        # Normally installed from InitGui at start-up; repeat defensively.
        wbtrack.install()

        # The commands back the viewer's own File/Edit menus (Gui.runCommand);
        # the workbench adds no menu, toolbar or context menu of its own.
        # Selecting the workbench opens the editor, and closing the editor
        # returns FreeCAD to the previous workbench, so it can be reopened.
        commands.register()
        FreeCAD.Console.PrintLog("Elec3D workbench loaded\n")

    def Activated(self):
        # The custom viewer is the workbench UI, so activation always opens it.
        # This used to be optional through AutoOpen2DView, but allowing it to be
        # disabled only produced an empty workbench with no useful surface.
        # Defer until FreeCAD has completed the workbench switch before adding
        # an MDI sub-window.
        QtCore.QTimer.singleShot(0, self._open_viewer)

    def _open_viewer(self):
        try:
            from . import viewer as viewer2d

            viewer2d.show_viewer()
        except Exception as exc:
            FreeCAD.Console.PrintError(
                "Elec3D: the 2D view could not open: %s\n" % exc)

    def Deactivated(self):
        # The editor (tab or separate window) stays open while another
        # workbench is active; only the hidden side panels come back.
        try:
            from . import viewer as viewer2d

            viewer2d.on_workbench_deactivated()
        except Exception:
            pass
        # Coin3D callbacks are only enabled after an explicit FreeCAD conversion.
        try:
            from .interaction2d import deactivate_interaction

            deactivate_interaction()
        except Exception:
            pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"
