# -*- coding: utf-8 -*-
"""Remember the last non-Elec3D workbench (no Qt/panel imports).

In tab mode the Elec3D workbench and the Elec3D tab are paired: entering the
tab activates Elec3D, leaving it returns FreeCAD to the workbench that was in
use before, and choosing another workbench while the tab is in front brings
the last FreeCAD document back to the front.  Separate Window mode is not
coupled to workbenches.  ``InitGui.py`` connects :func:`on_activated` to FreeCAD's
``workbenchActivated`` signal at start-up so that workbench is always known.
"""

ELEC3D_WORKBENCH = "Elec3DWorkbench"

_last_other = None
_installed = False
_listeners = []


def add_listener(callback):
    """Call ``callback(name)`` after every workbench activation (once per callback)."""
    if callback not in _listeners:
        _listeners.append(callback)


def remember(name):
    """Record *name* as the workbench to return to, without notifying anyone."""
    global _last_other
    name = str(name or "")
    if name and name != ELEC3D_WORKBENCH:
        _last_other = name


def on_activated(name):
    global _last_other
    name = str(name or "")
    if name and name != ELEC3D_WORKBENCH:
        _last_other = name
    for callback in list(_listeners):
        try:
            callback(name)
        except Exception:
            pass


def last_other():
    """Name of the most recently active workbench other than Elec3D."""
    return _last_other


def install():
    """Connect to FreeCAD's main window once; safe to call repeatedly."""
    global _installed
    if _installed:
        return
    try:
        import FreeCADGui as Gui

        main = Gui.getMainWindow()
        main.workbenchActivated.connect(on_activated)
        _installed = True
        bench = Gui.activeWorkbench()
        if bench is not None:
            on_activated(bench.name())
    except Exception:
        pass
