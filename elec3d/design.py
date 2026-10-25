# -*- coding: utf-8 -*-
"""Document-free board design state used by the custom 2D editor.

``DesignLayer`` models the board editing layers, which are deliberately separate
from the KiCad graphic layers that live *inside* each footprint (F.SilkS,
F.CrtYd, ...).  Copper planes are ordered bottom-first.  Since v0.17 board
boundaries are not stored here at all: they are derived per layer from the
imported board solid (:mod:`elec3d.boardsolid`).

The physical layer stack-up is kept beside the design layers.  Each copper plane
owns an editable copper thickness and every adjacent pair owns an editable
separation.  The stack is anchored to the imported board solid: the XY plane
through the solid's lowest point is ``z = 0`` and ``bottom_margin`` is the
height of the bottom copper reference plane above it.  There is no top margin;
the solid itself decides where the board ends.  Keeping this as plain
Python data makes the values survive Tab <-> Window host rebuilds without tying
them to Qt widgets.

The module contains no Qt objects so the layer/stack-up state is easy to
test, serialize and carry across the viewer's Tab <-> Window rebuilds.
"""

__all__ = [
    "LAYER_PALETTE",
    "DEFAULT_TOP_MARGIN_MM", "DEFAULT_BOTTOM_MARGIN_MM",
    "DEFAULT_COPPER_THICKNESS_MM", "DEFAULT_SEPARATION_MM",
    "DesignLayer", "LayerStack", "OutlineShape",
]

# Same palette the reference viewer cycles through when a copper plane is added.
LAYER_PALETTE = ["#4D7FC4", "#C83434", "#3FA34D", "#C9A227",
                 "#9C5FB0", "#00A0A0", "#D07A2A", "#7A8CCB"]

# v0.12 and earlier used design-layer id 0 for a standalone Outline layer.
# Keep the numeric value only as a migration sentinel; it is never exposed as
# an editable layer in v0.13+.
LEGACY_OUTLINE_LAYER_ID = 0

# Physical stack-up defaults, in millimetres.  The UI presents these as a
# top-to-bottom sequence: copper / separation / copper / ... / bottom margin.
# DEFAULT_TOP_MARGIN_MM only describes the implicit top margin that boards
# drawn before v0.17 had; it is used when such boards are migrated.
DEFAULT_TOP_MARGIN_MM = 1.0
DEFAULT_BOTTOM_MARGIN_MM = 1.0
DEFAULT_COPPER_THICKNESS_MM = 0.035
DEFAULT_SEPARATION_MM = 1.6


def box_margins(entry):
    """``(top, side)`` clearance in mm added to a component's bounding box.

    The box only grows upwards and outwards: its underside stays on the
    model's lowest point, so a box never sinks into the board.
    """
    margin = (entry or {}).get("box_margin") or {}
    try:
        top = max(0.0, float(margin.get("top", 0.0) or 0.0))
        side = max(0.0, float(margin.get("side", 0.0) or 0.0))
    except (TypeError, ValueError, AttributeError):
        return 0.0, 0.0
    return top, side


class DesignLayer(object):
    """One copper editor/reference layer.

    ``kind`` is retained for backward-compatible snapshot loading, but current
    projects create copper layers only.
    """

    def __init__(self, uid, name, color, visible=True, renamed=False,
                 kind="copper", locked=False):
        self.id = uid
        self.name = name
        self.color = color
        self.visible = bool(visible)
        self.renamed = bool(renamed)
        self.kind = kind
        self.locked = bool(locked)

    @property
    def is_outline(self):
        return self.kind == "outline"

    @property
    def is_copper(self):
        return self.kind == "copper"

    def __repr__(self):
        return ("DesignLayer(id=%r, name=%r, color=%r, visible=%r, kind=%r, "
                "locked=%r)" % (self.id, self.name, self.color, self.visible,
                                  self.kind, self.locked))


class OutlineShape(object):
    """Legacy (v0.13-v0.16) drawn board-outline primitive.

    Kept only so boards saved by those releases can be read and migrated into
    an equivalent board solid; nothing creates new outline shapes any more.
    Since v0.13 there was no separate editor Outline layer.  Board-outline
    primitives belong to the copper layer that was active when they were drawn.
    ``layer_id`` is kept outside ``geometry`` so dimensions remain clean and old
    v0.12 snapshots (which had no layer id) can be upgraded on load.
    """

    def __init__(self, uid, kind, layer_id=None, **geometry):
        self.uid = str(uid)
        self.kind = str(kind)
        self.layer_id = layer_id
        self.geometry = dict(geometry)

    def snapshot(self):
        data = {"uid": self.uid, "kind": self.kind}
        if self.layer_id is not None:
            data["layer_id"] = self.layer_id
        data.update(self.geometry)
        return data

    @classmethod
    def restore(cls, data):
        data = dict(data or {})
        uid = data.pop("uid", "")
        kind = data.pop("kind", "rectangle")
        layer_id = data.pop("layer_id", None)
        try:
            layer_id = int(layer_id) if layer_id is not None else None
        except (TypeError, ValueError):
            layer_id = None
        return cls(uid, kind, layer_id=layer_id, **data)

    def __repr__(self):
        return "OutlineShape(uid=%r, kind=%r, geometry=%r)" % (
            self.uid, self.kind, self.geometry)


class LayerStack(object):
    """Ordered copper reference-plane stack.

    Each layer's board boundary is the board solid's cross-section at the
    layer's reference-plane height (see :meth:`reference_heights`).

    Every copper layer is a *reference plane*.  ``separation`` is the distance
    between adjacent reference planes; copper is etched below its own plane, so
    changing copper thickness must never move any reference plane or alter an
    inter-plane separation.  Physical stack-up values are keyed by stable layer
    ids, not by current layer names/indices.  Adding a new top plane therefore
    leaves every existing copper/separation value untouched and inserts just one
    new copper thickness plus one new reference-plane separation above the old
    top plane.
    """

    def __init__(self):
        self._next_id = 1
        self.layers = []                       # copper planes only, bottom first
        self.active_id = None

        self.bottom_margin = DEFAULT_BOTTOM_MARGIN_MM
        # Only populated when restoring a pre-v0.17 snapshot; the migration of
        # drawn outlines into a solid needs the old block height.
        self.legacy_top_margin = None
        self._copper_thickness = {}            # layer id -> mm
        self._separations = {}                 # (lower id, upper id) -> mm

        self.add()                              # board starts with one B.Cu

    # -- lookup ----------------------------------------------------------
    def __len__(self):
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    def all_layers(self):
        return list(self.layers)

    def copper_layers(self):
        return list(self.layers)

    def copper_top_down(self):
        return list(reversed(self.layers))

    def index(self, uid):
        """Position of copper layer ``uid``, or ``-1`` when it is unknown."""
        for i, layer in enumerate(self.layers):
            if layer.id == uid:
                return i
        return -1

    def by_id(self, uid):
        """Resolve a copper editor layer, falling back to the bottom plane."""
        for layer in self.layers:
            if layer.id == uid:
                return layer
        return self.layers[0] if self.layers else None

    def has(self, uid):
        return self.index(uid) >= 0

    def top_down(self):
        """Editor order: top copper to bottom copper."""
        return list(reversed(self.layers))

    def active(self):
        return self.by_id(self.active_id)

    def active_copper(self):
        """Active copper plane."""
        layer = self.active()
        return layer if layer is not None and layer.is_copper else self.top()

    def bottom(self):
        return self.layers[0] if self.layers else None

    def top(self):
        return self.layers[-1] if self.layers else None

    # -- physical stack-up ----------------------------------------------
    @staticmethod
    def _positive(value, minimum=0.0):
        try:
            return max(float(minimum), float(value))
        except (TypeError, ValueError):
            return float(minimum)

    def _gap_key(self, first_id, second_id):
        """Return the physical (lower, upper) key for two adjacent planes."""
        first_index = self.index(first_id)
        second_index = self.index(second_id)
        if first_index < 0 or second_index < 0 or abs(first_index - second_index) != 1:
            return None
        if first_index < second_index:
            return (first_id, second_id)
        return (second_id, first_id)

    def _ensure_stackup(self):
        """Fill missing defaults and drop values for planes/gaps that no longer exist."""
        live_ids = set(layer.id for layer in self.layers)
        self._copper_thickness = {
            uid: value for uid, value in self._copper_thickness.items()
            if uid in live_ids
        }
        for layer in self.layers:
            self._copper_thickness.setdefault(layer.id, DEFAULT_COPPER_THICKNESS_MM)

        valid_gaps = set((self.layers[i].id, self.layers[i + 1].id)
                         for i in range(max(0, len(self.layers) - 1)))
        self._separations = {
            key: value for key, value in self._separations.items()
            if key in valid_gaps
        }
        for key in valid_gaps:
            self._separations.setdefault(key, DEFAULT_SEPARATION_MM)

    def copper_thickness(self, uid):
        if self.index(uid) < 0:
            return DEFAULT_COPPER_THICKNESS_MM
        return float(self._copper_thickness.get(uid, DEFAULT_COPPER_THICKNESS_MM))

    def set_copper_thickness(self, uid, value):
        if self.index(uid) < 0:
            return False
        value = self._positive(value, 0.001)
        if abs(self.copper_thickness(uid) - value) < 1e-12:
            return False
        self._copper_thickness[uid] = value
        return True

    def separation(self, first_id, second_id):
        key = self._gap_key(first_id, second_id)
        if key is None:
            return None
        return float(self._separations.get(key, DEFAULT_SEPARATION_MM))

    def set_separation(self, first_id, second_id, value):
        key = self._gap_key(first_id, second_id)
        if key is None:
            return False
        value = self._positive(value, 0.0)
        current = float(self._separations.get(key, DEFAULT_SEPARATION_MM))
        if abs(current - value) < 1e-12:
            return False
        self._separations[key] = value
        return True

    def set_bottom_margin(self, value):
        value = self._positive(value, 0.0)
        if abs(float(self.bottom_margin) - value) < 1e-12:
            return False
        self.bottom_margin = value
        return True

    def reference_heights(self):
        """Map copper layer id -> reference-plane height above ``z = 0``.

        ``z = 0`` is the XY plane through the board solid's lowest point.  The
        bottom plane sits ``bottom_margin`` above it and every further plane
        one ``separation`` higher.  Copper is etched *below* its plane, so
        copper thickness never moves a plane.
        """
        z = float(self.bottom_margin)
        result = {}
        for index, layer in enumerate(self.layers):
            result[layer.id] = z
            if index + 1 < len(self.layers):
                z += self.separation(layer.id, self.layers[index + 1].id) or 0.0
        return result

    def top_reference_height(self):
        """Height of the top copper reference plane above the solid's bottom."""
        heights = self.reference_heights()
        return max(heights.values()) if heights else float(self.bottom_margin)

    def total_stackup_thickness(self):
        """Compatibility name for :meth:`top_reference_height`.

        Before v0.17 this included a top margin.  The board's real height now
        comes from the imported solid, so the stack only spans up to its top
        reference plane.
        """
        return self.top_reference_height()

    # -- mutation --------------------------------------------------------
    def add(self):
        """Add a copper plane above the current top one and make it active."""
        old_top = self.top()
        uid = self._next_id
        self._next_id += 1
        color = LAYER_PALETTE[len(self.layers) % len(LAYER_PALETTE)]
        layer = DesignLayer(uid, "F.Cu", color, kind="copper")
        self.layers.append(layer)
        self._copper_thickness[uid] = DEFAULT_COPPER_THICKNESS_MM
        if old_top is not None:
            self._separations[(old_top.id, uid)] = DEFAULT_SEPARATION_MM
        self.relabel()
        self.active_id = uid
        return layer

    def remove(self, uid):
        """Drop a copper plane and return the id orphaned footprints should use.

        The final copper plane is permanent.
        When a middle reference plane is removed, its two adjacent separations
        are merged into one.  Copper thickness is deliberately *not* folded into
        that distance: copper is etched below a reference plane and does not
        participate in reference-plane spacing.
        """
        if len(self.layers) <= 1:
            return None
        idx = self.index(uid)
        if idx < 0:
            return None

        lower = self.layers[idx - 1] if idx > 0 else None
        upper = self.layers[idx + 1] if idx + 1 < len(self.layers) else None
        lower_gap = (self.separation(lower.id, uid) if lower is not None else None)
        upper_gap = (self.separation(uid, upper.id) if upper is not None else None)

        self.layers.pop(idx)
        self._copper_thickness.pop(uid, None)
        self._separations = {
            key: value for key, value in self._separations.items()
            if uid not in key
        }
        if lower is not None and upper is not None:
            self._separations[(lower.id, upper.id)] = (
                float(lower_gap or 0.0) + float(upper_gap or 0.0))

        self._ensure_stackup()
        self.relabel()
        fallback = self.layers[min(idx, len(self.layers) - 1)].id
        if self.active_id == uid:
            self.active_id = fallback
        return fallback

    def set_active(self, uid):
        if not self.has(uid) or self.active_id == uid:
            return False
        self.active_id = uid
        return True

    def rename(self, uid, name):
        layer = self.by_id(uid)
        if layer is None or layer.locked:
            return False
        name = (name or "").strip()
        if not name or name == layer.name:
            return False
        layer.name = name
        layer.renamed = True
        return True

    def relabel(self):
        """Re-apply KiCad-style names to copper planes only."""
        count = len(self.layers)
        for i, layer in enumerate(self.layers):
            if layer.renamed:
                continue
            if i == 0:
                layer.name = "B.Cu"
            elif i == count - 1:
                layer.name = "F.Cu"
            else:
                layer.name = "In%d.Cu" % i

    # -- serialisation ---------------------------------------------------
    @staticmethod
    def _layer_data(layer):
        return {
            "id": layer.id, "name": layer.name, "color": layer.color,
            "visible": layer.visible, "renamed": layer.renamed,
            "kind": layer.kind, "locked": layer.locked,
        }

    def snapshot(self):
        return {
            "next_id": self._next_id,
            "active_id": self.active_id,
            "layers": [self._layer_data(l) for l in self.layers],
            "stackup": {
                "bottom_margin": float(self.bottom_margin),
                "copper": [
                    {"layer_id": layer.id,
                     "thickness": self.copper_thickness(layer.id)}
                    for layer in self.layers
                ],
                "separations": [
                    {"lower_id": lower.id, "upper_id": upper.id,
                     "thickness": self.separation(lower.id, upper.id)}
                    for lower, upper in zip(self.layers[:-1], self.layers[1:])
                ],
            },
        }

    def restore(self, data):
        """Load a snapshot; old standalone-Outline/stack-up states are upgraded."""
        if not data or not data.get("layers"):
            return
        self.layers = [DesignLayer(
            d["id"], d["name"], d["color"], d.get("visible", True),
            d.get("renamed", False), d.get("kind", "copper"),
            d.get("locked", False)) for d in data["layers"]]

        self._next_id = max(data.get("next_id", 1),
                            max(l.id for l in self.layers) + 1)
        self.active_id = data.get("active_id")
        # v0.12 used id 0 for a separate Outline layer.  Any unknown/legacy
        # active id migrates to the top copper plane.
        if not self.has(self.active_id):
            self.active_id = self.top().id if self.top() is not None else None

        # Defaults make old (pre-stackup) snapshots immediately usable.
        self.bottom_margin = DEFAULT_BOTTOM_MARGIN_MM
        self.legacy_top_margin = None
        self._copper_thickness = {}
        self._separations = {}
        self._ensure_stackup()

        stackup = data.get("stackup") or {}
        if stackup:
            if stackup.get("top_margin") is not None:
                self.legacy_top_margin = self._positive(stackup.get("top_margin"), 0.0)
            self.bottom_margin = self._positive(
                stackup.get("bottom_margin", DEFAULT_BOTTOM_MARGIN_MM), 0.0)

            for saved in stackup.get("copper", []):
                uid = saved.get("layer_id")
                if self.index(uid) >= 0:
                    self._copper_thickness[uid] = self._positive(
                        saved.get("thickness", DEFAULT_COPPER_THICKNESS_MM), 0.001)

            for saved in stackup.get("separations", []):
                lower_id = saved.get("lower_id")
                upper_id = saved.get("upper_id")
                key = self._gap_key(lower_id, upper_id)
                if key is not None:
                    self._separations[key] = self._positive(
                        saved.get("thickness", DEFAULT_SEPARATION_MM), 0.0)

        self._ensure_stackup()
