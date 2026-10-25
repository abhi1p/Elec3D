# -*- coding: utf-8 -*-
"""Elec3D — KiCad footprint and netlist board editor for FreeCAD 1.1.

Public API::

    from elec3d.footprint import parse_footprint_file
    from elec3d.builder import import_file, ImportOptions

``elec3d.sexpr`` and ``elec3d.footprint`` are pure Python and can be used
outside FreeCAD; ``elec3d.builder`` and ``elec3d.commands`` need FreeCAD.
"""

import os

__version__ = "0.21.0"

PREF_PATH = "User parameter:BaseApp/Preferences/Mod/Elec3D"

# This package always has a real __file__, unlike Init.py / InitGui.py which
# FreeCAD exec's in a namespace where __file__ may be missing.  Everything that
# needs a path on disk therefore asks here.
ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(ADDON_DIR, "resources", "icons")


def addon_dir():
    """Absolute path of the installed addon folder."""
    return ADDON_DIR


def icon_path(name):
    """Absolute path of an icon in ``resources/icons``."""
    return os.path.join(ICON_DIR, name)


def get_options():
    """Read the user's preferences into an :class:`~elec3d.builder.ImportOptions`."""
    from .options import (ImportOptions, DEFAULT_SIDEBAR_ORDER,
                          normalize_crosshair_size, normalize_sidebar_order)

    opts = ImportOptions()
    try:
        import FreeCAD as App

        params = App.ParamGet(PREF_PATH)
        # The add-on has no independent colour preference.  Use FreeCAD's
        # current application palette for both the 2D viewer and any explicit
        # FreeCAD conversion.  Old stored ``Theme`` values are intentionally
        # ignored for backwards compatibility.
        try:
            from .theme import application_theme
            opts.theme = application_theme(opts.theme)
        except Exception:
            pass
        opts.stroke_faces = params.GetBool("StrokeFaces", opts.stroke_faces)
        opts.import_texts = params.GetBool("ImportTexts", opts.import_texts)
        opts.show_hidden_texts = params.GetBool("ShowHiddenTexts", opts.show_hidden_texts)
        opts.group_by_layer = params.GetBool("GroupByLayer", opts.group_by_layer)
        opts.layer_z_step = params.GetFloat("LayerZStep", opts.layer_z_step)
        opts.line_width_px = params.GetInt("LineWidth", opts.line_width_px)
        opts.transparency = params.GetInt("Transparency", opts.transparency)

        saved_order = params.GetString("SidebarOrder", ",".join(opts.sidebar_order))
        opts.sidebar_order = normalize_sidebar_order(saved_order)
        for key in DEFAULT_SIDEBAR_ORDER:
            pref_key = "SidebarExpanded_" + key
            opts.sidebar_expanded[key] = params.GetBool(
                pref_key, bool(opts.sidebar_expanded.get(key, True)))
        crosshair = params.GetString("CrosshairMode", opts.crosshair_mode)
        opts.crosshair_mode = crosshair if crosshair in ("small", "full") else "small"
        opts.crosshair_size_px = normalize_crosshair_size(
            params.GetInt("CrosshairSizePx", opts.crosshair_size_px))
        opts.drag_keep_45 = params.GetBool("DragKeep45", opts.drag_keep_45)
        opts.route_walkaround = params.GetBool("RouteWalkaround",
                                               opts.route_walkaround)
    except Exception:
        pass
    return opts
