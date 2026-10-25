# -*- coding: utf-8 -*-
"""GUI-side initialisation for the Elec3D addon.

Kept as thin as possible on purpose.  FreeCAD runs this file with ``exec()``
using separate globals and locals dictionaries, which means:

* the module path variable Python normally provides may not be defined, and
* a ``class`` body defined here cannot see module level names assigned here
  (they go into locals, class bodies read globals).

So this file only imports and registers; the workbench class itself lives in
``elec3d/workbench.py``, a normal module where ordinary scoping applies.
"""

import FreeCAD
import FreeCADGui

try:
    from elec3d.workbench import Elec3DWorkbench

    FreeCADGui.addWorkbench(Elec3DWorkbench())

    # Track the last non-Elec3D workbench so leaving the Elec3D tab can
    # return FreeCAD to it.
    from elec3d import wbtrack

    wbtrack.install()
except Exception:
    import traceback

    FreeCAD.Console.PrintError(
        "Elec3D: the workbench could not be registered.\n%s\n"
        % traceback.format_exc())
