# -*- coding: utf-8 -*-
"""Import and viewer options.

Kept in a module of its own, free of FreeCAD imports, so the 2D viewer and its
unit tests can use the same defaults without dragging in Part/OCC.
"""

SIDEBAR_SECTION_LABELS = {
    "footprints": "Footprints",
    "properties": "Properties",
    "board": "Board solid",
    "routing": "Routing",
    "footprint_graphics": "Footprint graphics",
    "pads": "Pads",
}

# Requested default order.  These stable keys, rather than translated/display
# labels, are what gets persisted in FreeCAD's parameter store.
DEFAULT_SIDEBAR_ORDER = (
    "footprints",
    "properties",
    "board",
    "routing",
    "footprint_graphics",
    "pads",
)

DEFAULT_SMALL_CROSSHAIR_PX = 40
MIN_SMALL_CROSSHAIR_PX = 8
MAX_SMALL_CROSSHAIR_PX = 200

# Compact by default where sections tend to become tall.  The three day-to-day
# editing groups remain open unless the user changes their startup preference.
DEFAULT_SIDEBAR_EXPANDED = {
    "footprints": False,
    "properties": True,
    "board": True,
    "routing": True,
    "footprint_graphics": False,
    "pads": False,
}


def normalize_crosshair_size(value):
    """Clamp the compact crosshair total screen-space span to a sane range."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = DEFAULT_SMALL_CROSSHAIR_PX
    return max(MIN_SMALL_CROSSHAIR_PX, min(MAX_SMALL_CROSSHAIR_PX, value))


def normalize_sidebar_order(value):
    """Return a complete, duplicate-free sidebar order from saved input."""
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    try:
        incoming = list(value or [])
    except TypeError:
        incoming = []
    result = []
    for key in incoming + list(DEFAULT_SIDEBAR_ORDER):
        if key in SIDEBAR_SECTION_LABELS and key not in result:
            result.append(key)
    return result


class ImportOptions(object):
    """Everything the user can tweak, with sane defaults."""

    def __init__(self, **kw):
        self.theme = "dark"             # resolved from FreeCAD/Qt at runtime
        self.stroke_faces = True        # draw line widths as real faces
        self.import_texts = True
        self.show_hidden_texts = False  # hidden texts are created but invisible
        self.layer_z_step = 0.005       # mm between stacked layers
        self.line_width_px = 2          # fallback screen width for wires
        self.group_by_layer = True
        self.transparency = 0

        # 2D-view / sidebar preferences.  Copy mutable defaults per instance.
        self.sidebar_order = list(DEFAULT_SIDEBAR_ORDER)
        self.sidebar_expanded = dict(DEFAULT_SIDEBAR_EXPANDED)
        self.crosshair_mode = "small"   # "small" or "full"
        self.crosshair_size_px = DEFAULT_SMALL_CROSSHAIR_PX

        # Interactive routing behaviour, mirroring KiCad's defaults.
        self.drag_keep_45 = True        # hold H/V/45 while dragging copper
        self.route_walkaround = True    # route around obstacles, not into them

        for k, v in kw.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.sidebar_order = normalize_sidebar_order(self.sidebar_order)
        expanded = dict(DEFAULT_SIDEBAR_EXPANDED)
        try:
            expanded.update(dict(self.sidebar_expanded or {}))
        except (TypeError, ValueError):
            pass
        self.sidebar_expanded = {
            key: bool(expanded.get(key, DEFAULT_SIDEBAR_EXPANDED[key]))
            for key in DEFAULT_SIDEBAR_ORDER
        }
        if self.crosshair_mode not in ("small", "full"):
            self.crosshair_mode = "small"
        self.crosshair_size_px = normalize_crosshair_size(self.crosshair_size_px)
        self.drag_keep_45 = bool(self.drag_keep_45)
        self.route_walkaround = bool(self.route_walkaround)
