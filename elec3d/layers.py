# -*- coding: utf-8 -*-
"""Layer colours and stacking order (ported from the JS reference viewer).

Colours are stored as ``#rrggbb`` and converted to FreeCAD's float triples on
demand.  Two palettes are provided because a few KiCad layer colours (silk
screen above all) are tuned for a dark canvas and vanish on a light one.
"""

__all__ = ["layer_color", "layer_hex", "layer_z", "pad_color", "pad_hex",
           "outline_hex", "hex_to_rgb", "LAYER_ORDER"]

LAYER_COLORS_DARK = {
    "F.Cu": "#C83434", "B.Cu": "#4D7FC4", "*.Cu": "#D9A441",
    "F.Paste": "#A08C7A", "B.Paste": "#00B2B2",
    "F.Mask": "#9C5FB0", "B.Mask": "#02C2A8",
    "F.SilkS": "#E8E2D0", "B.SilkS": "#C48CD4",
    "F.Silkscreen": "#E8E2D0", "B.Silkscreen": "#C48CD4",
    "F.Fab": "#B8A44C", "B.Fab": "#6D8FB8",
    "F.CrtYd": "#FF26E2", "B.CrtYd": "#26E9FF",
    "F.Courtyard": "#FF26E2", "B.Courtyard": "#26E9FF",
    "Dwgs.User": "#7AA0C4", "Cmts.User": "#5C7CFA", "Edge.Cuts": "#D0C05C",
    "Eco1.User": "#8FBE5A", "Eco2.User": "#5ABE9A",
    "unknown": "#888888",
}

LAYER_COLORS_LIGHT = dict(LAYER_COLORS_DARK)
LAYER_COLORS_LIGHT.update({
    "F.SilkS": "#6E6553", "F.Silkscreen": "#6E6553",
    "B.SilkS": "#8A4FA8", "B.Silkscreen": "#8A4FA8",
    "Dwgs.User": "#3E6B96", "Cmts.User": "#3A54C8", "Edge.Cuts": "#9A8410",
    "B.Cu": "#2E5FA3", "*.Cu": "#A87B15", "F.Paste": "#7A6A5A",
    "F.Fab": "#8A7A2C", "B.Fab": "#4E6E92",
    "Eco1.User": "#5E8F2C", "Eco2.User": "#2E8B6E",
    "unknown": "#6B6B6B",
})

# Board Outline is deliberately not tinted by its owning copper layer.  It is a
# mechanical/reference boundary, so a dedicated high-contrast orange makes it
# immediately distinguishable from routed copper in both themes.
OUTLINE_COLORS = {"dark": "#F0A33A", "light": "#B85A00"}

# Painting order, bottom first.  Used to give every layer its own tiny Z
# offset so coplanar faces do not fight for pixels in the 3D view.
LAYER_ORDER = [
    "B.Cu", "B.Paste", "B.Mask", "B.Fab", "B.SilkS", "B.Silkscreen",
    "Dwgs.User", "Cmts.User", "Eco1.User", "Eco2.User",
    "F.Mask", "F.Paste", "*.Cu", "F.Cu",
    "F.Fab", "Edge.Cuts",
    "B.CrtYd", "B.Courtyard", "F.CrtYd", "F.Courtyard",
    "F.SilkS", "F.Silkscreen",
]


def hex_to_rgb(color):
    """``'#rrggbb'`` -> ``(r, g, b)`` floats in 0..1."""
    color = color.lstrip("#")
    return (int(color[0:2], 16) / 255.0,
            int(color[2:4], 16) / 255.0,
            int(color[4:6], 16) / 255.0)


def layer_color(layer, theme="dark"):
    table = LAYER_COLORS_LIGHT if theme == "light" else LAYER_COLORS_DARK
    return hex_to_rgb(table.get(layer, table["unknown"]))


def layer_z(layer, step=0.005):
    """Small Z offset so overlapping layers stay visually separated."""
    try:
        return LAYER_ORDER.index(layer) * step
    except ValueError:
        return 0.0


def pad_color(pad, theme="dark"):
    return layer_color(pad.copper_layer, theme)


def layer_hex(layer, theme="dark"):
    """``'#rrggbb'`` for Qt, as opposed to FreeCAD's float triples."""
    table = LAYER_COLORS_LIGHT if theme == "light" else LAYER_COLORS_DARK
    return table.get(layer, table["unknown"])


def pad_hex(pad, theme="dark"):
    return layer_hex(pad.copper_layer, theme)


def outline_hex(theme="dark"):
    """Dedicated board-outline colour, independent of copper-layer colour."""
    return OUTLINE_COLORS.get(theme if theme in OUTLINE_COLORS else "dark", OUTLINE_COLORS["dark"])
