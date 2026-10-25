# -*- coding: utf-8 -*-
"""Console-side initialisation.

Register Elec3D project/board files and KiCad footprint files with
FreeCAD's file dialogs.  Opening them feeds the document-free custom viewer;
FreeCAD documents are created only by the explicit export command.
"""

import FreeCAD

FreeCAD.addImportType("KiCad footprint (*.kicad_mod)", "elec3d_import")
FreeCAD.addImportType("Elec3D project (*.e3d_pro)", "elec3d_import")
FreeCAD.addImportType("Elec3D board (*.e3d_pcb)", "elec3d_import")
FreeCAD.Console.PrintLog("Elec3D: .kicad_mod/.e3d_pro/.e3d_pcb viewer handlers registered\n")
