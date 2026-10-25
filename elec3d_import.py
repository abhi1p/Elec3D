# -*- coding: utf-8 -*-
"""FreeCAD file-type bridge for footprints and Elec3D projects/boards."""

import os
import FreeCAD

from elec3d.footprint import parse_footprint_file

__all__ = ["open", "insert"]


def open(filename):                    # noqa: A001 - name required by FreeCAD
    _do_open(filename)
    return None


def insert(filename, docname=None):
    _do_open(filename)
    return None


def _do_open(filename):
    lower = str(filename).lower()
    if lower.endswith((".e3d_pro", ".e3d_pcb")):
        from elec3d import designfile, projectfile
        if not FreeCAD.GuiUp:
            if lower.endswith(projectfile.PROJECT_EXTENSION):
                paths = projectfile.read_project(filename)
                data = designfile.read_file(paths["board_file"])
            else:
                data = designfile.read_file(filename)
            FreeCAD.Console.PrintMessage("Elec3D design validated: %s\n" % filename)
            return data
        try:
            from elec3d import viewer as viewer2d
            viewer = viewer2d.show_viewer()
            # The GUI path performs the same standalone-board project prompt as
            # File -> Open inside the add-on.
            viewer.open_design_path(filename, prompt_for_project=True)
            FreeCAD.Console.PrintMessage("Elec3D design opened: %s\n" % filename)
            return None
        except Exception as exc:
            FreeCAD.Console.PrintError(
                "Elec3D design open failed for %s: %s\n" % (filename, exc))
            raise

    fp = parse_footprint_file(filename)
    FreeCAD.Console.PrintMessage("KiCad footprint opened: %s\n" % fp.summary())
    for warn in fp.warnings:
        FreeCAD.Console.PrintWarning("KiCad footprint: %s\n" % warn)

    if not FreeCAD.GuiUp:
        return fp

    try:
        from elec3d import viewer as viewer2d
        viewer2d.add_footprint(fp)
    except Exception as exc:
        FreeCAD.Console.PrintError(
            "Elec3D failed for %s: %s\n" % (filename, exc))
        raise
    return fp
