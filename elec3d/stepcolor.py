# -*- coding: utf-8 -*-
"""Read STEP presentation colours without creating a FreeCAD document.

STEP colour is presentation data, not part of the B-Rep geometry itself.  KiCad
models seen in the wild use both of the common forms:

* ``STYLED_ITEM`` attached directly to an ``ADVANCED_FACE`` (per-face colour),
* ``STYLED_ITEM`` attached to a ``MANIFOLD_SOLID_BREP`` (whole-solid colour).

FreeCAD's native importer keeps those styles, but the document-free preview
uses ``Part.read(...).tessellate(...)`` and therefore has to recover the style
information separately.  This module reads the small relevant subset of STEP
Part 21 text and expands solid/shell styles onto their descendant faces.  A
face-specific style always wins over an inherited solid/shell style.

No OpenCascade document is created here; callers can pair ``faces`` with
``Part.Shape.Faces`` in file/topology order.  ``solids`` is also retained so a
renderer that tessellates ``Shape.Solids`` can preserve the same grouping.
"""

import os
import re

__all__ = ["StepColors", "StepSolidColors", "read_step_colors", "clear_cache"]

# One entity per statement: "#12 = TYPE(args)".  Arguments routinely wrap over
# several lines, so the scan runs over the whole file with DOTALL enabled.
_ENTITY_RE = re.compile(r"#(\d+)\s*=\s*([A-Za-z_0-9]+)\s*\((.*?)\)\s*;", re.S)
_REF_RE = re.compile(r"#(\d+)")
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?")

_PREDEFINED = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "magenta": (1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
}

# Topology containers whose descendants can legitimately inherit a surface
# style.  Restricting the graph walk to these types avoids accidentally walking
# into placements/representation contexts and colouring unrelated geometry.
_FACE_TYPES = {"ADVANCED_FACE"}
_SHELL_TYPES = {"CLOSED_SHELL", "OPEN_SHELL"}
_SOLID_TYPES = {"MANIFOLD_SOLID_BREP", "FACETED_BREP", "BREP_WITH_VOIDS"}
_CONTAINER_TYPES = _SHELL_TYPES | _SOLID_TYPES | {"SHELL_BASED_SURFACE_MODEL"}

_MAX_WALK = 64
_cache = {}


class StepSolidColors(object):
    """Presentation information for one solid in STEP file order."""

    def __init__(self, uid, face_indexes=None, color=None):
        self.uid = int(uid)
        self.face_indexes = list(face_indexes or [])
        self.color = color

    def __repr__(self):
        return "StepSolidColors(uid=%d, faces=%d, color=%r)" % (
            self.uid, len(self.face_indexes), self.color)


class StepColors(object):
    """Effective colours for STEP faces and solid topology.

    ``faces[i]`` is the effective colour for face ``i`` after inherited
    solid/shell styles and direct face styles have been combined.  ``None``
    means there was no usable style for that face.  ``dominant`` is retained as
    a safe fallback for unstyled faces in partly-styled files.
    """

    def __init__(self, faces=None, dominant=None, solids=None):
        self.faces = faces or []
        self.dominant = dominant
        self.solids = list(solids or [])

    def __bool__(self):
        return bool(self.dominant or any(c is not None for c in self.faces)
                    or any(s.color is not None for s in self.solids))

    __nonzero__ = __bool__

    def color_for(self, index):
        if 0 <= index < len(self.faces):
            color = self.faces[index]
            if color is not None:
                return color
        return self.dominant

    def solid_color_for(self, index):
        if 0 <= index < len(self.solids):
            return self.solids[index].color or self.dominant
        return self.dominant

    def color_for_solid_face(self, solid_index, local_face_index):
        """Effective colour for a face addressed within a STEP solid."""
        if 0 <= solid_index < len(self.solids):
            solid = self.solids[solid_index]
            if 0 <= local_face_index < len(solid.face_indexes):
                return self.color_for(solid.face_indexes[local_face_index])
            if solid.color is not None:
                return solid.color
        return self.dominant

    def __repr__(self):
        colored = sum(1 for c in self.faces if c is not None)
        return "StepColors(%d/%d faces coloured, %d solids, dominant=%r)" % (
            colored, len(self.faces), len(self.solids), self.dominant)


def _parse_entities(text):
    start = text.find("DATA;")
    if start >= 0:
        text = text[start:]
    entities = {}
    order = []
    for match in _ENTITY_RE.finditer(text):
        uid = int(match.group(1))
        entities[uid] = (match.group(2).upper(), match.group(3))
        order.append(uid)
    return entities, order


def _rgb_from(args):
    numbers = _NUM_RE.findall(args)
    if len(numbers) < 3:
        return None
    try:
        rgb = tuple(float(n) for n in numbers[:3])
    except ValueError:
        return None
    return tuple(min(1.0, max(0.0, v)) for v in rgb)


def _walk_to_color(entities, start_ids):
    """Breadth-first search from a style assignment to its colour."""
    seen = set()
    queue = list(start_ids)
    steps = 0
    while queue and steps < _MAX_WALK:
        uid = queue.pop(0)
        if uid in seen or uid not in entities:
            continue
        seen.add(uid)
        steps += 1
        kind, args = entities[uid]
        if kind == "COLOUR_RGB":
            rgb = _rgb_from(args)
            if rgb is not None:
                return rgb
        elif kind == "DRAUGHTING_PRE_DEFINED_COLOUR":
            name = args.strip().strip("'").lower()
            if name in _PREDEFINED:
                return _PREDEFINED[name]
        queue.extend(int(ref) for ref in _REF_RE.findall(args))
    return None


def _styled_targets(kind, args):
    """Return ``(style refs, styled target)`` for a STEP styled item.

    ``OVER_RIDING_STYLED_ITEM`` has one extra final reference naming the style
    that it overrides, so its actual item target is the penultimate reference.
    """
    refs = [int(ref) for ref in _REF_RE.findall(args)]
    if kind == "OVER_RIDING_STYLED_ITEM":
        if len(refs) < 3:
            return [], None
        return refs[:-2], refs[-2]
    if not refs:
        return [], None
    return refs[:-1], refs[-1]


def _face_uids_for_target(entities, uid, memo, visiting=None):
    """Return ADVANCED_FACE ids contained by a styled topology target."""
    if uid in memo:
        return memo[uid]
    if uid not in entities:
        memo[uid] = []
        return memo[uid]
    if visiting is None:
        visiting = set()
    if uid in visiting:
        return []
    visiting.add(uid)
    kind, args = entities[uid]
    if kind in _FACE_TYPES:
        result = [uid]
    elif kind in _CONTAINER_TYPES:
        result = []
        for ref in (int(x) for x in _REF_RE.findall(args)):
            child_kind = entities.get(ref, ("", ""))[0]
            if child_kind in _FACE_TYPES or child_kind in _CONTAINER_TYPES:
                result.extend(_face_uids_for_target(entities, ref, memo, visiting))
    else:
        result = []
    visiting.remove(uid)
    # Stable de-duplication.  Voided BReps and odd exporters can repeat shell
    # references; a face should still receive the style only once.
    result = list(dict.fromkeys(result))
    memo[uid] = result
    return result


def read_step_colors(path, max_bytes=64 * 1024 * 1024):
    """Return :class:`StepColors` for ``path``; never raises."""
    if not path:
        return StepColors()
    try:
        stamp = (os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return StepColors()
    cached = _cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]

    result = StepColors()
    try:
        if stamp[1] <= max_bytes:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                result = _colors_from_text(handle.read())
    except (OSError, UnicodeError, MemoryError, re.error):
        result = StepColors()

    _cache[path] = (stamp, result)
    return result


def _colors_from_text(text):
    entities, order = _parse_entities(text)
    if not entities:
        return StepColors()

    face_index = {}
    for uid in order:
        if entities[uid][0] in _FACE_TYPES:
            face_index[uid] = len(face_index)

    # First collect styles by target.  Inherited container colours and direct
    # face colours are kept separately so a face style wins regardless of the
    # order in which the exporter wrote the STYLED_ITEM entities.
    inherited_styles = []
    direct_face = {}
    target_color = {}
    all_style_counts = {}
    memo = {}
    for uid in order:
        kind, args = entities[uid]
        if kind not in ("STYLED_ITEM", "OVER_RIDING_STYLED_ITEM"):
            continue
        styles, target = _styled_targets(kind, args)
        if target is None:
            continue
        rgb = _walk_to_color(entities, styles)
        if rgb is None:
            continue
        target_color[target] = rgb
        all_style_counts[rgb] = all_style_counts.get(rgb, 0) + 1
        target_kind = entities.get(target, ("", ""))[0]
        if target_kind in _FACE_TYPES:
            direct_face[target] = rgb
        elif target_kind in _CONTAINER_TYPES:
            inherited_styles.append((target, rgb))

    faces = [None] * len(face_index)
    for target, rgb in inherited_styles:
        for face_uid in _face_uids_for_target(entities, target, memo):
            index = face_index.get(face_uid)
            if index is not None:
                faces[index] = rgb
    for face_uid, rgb in direct_face.items():
        index = face_index.get(face_uid)
        if index is not None:
            faces[index] = rgb

    # Retain solid topology in file order.  This is useful to renderers that
    # tessellate Shape.Solids rather than relying on one global Faces ordering.
    solids = []
    for uid in order:
        if entities[uid][0] not in _SOLID_TYPES:
            continue
        indexes = [face_index[f] for f in _face_uids_for_target(entities, uid, memo)
                   if f in face_index]
        solids.append(StepSolidColors(uid, indexes, target_color.get(uid)))

    # Weight the dominant colour by the effective coloured faces, not merely by
    # the number of STYLED_ITEM records.  A single styled package body can own
    # many faces while dozens of tiny leads may each be separate solids.
    counts = {}
    for color in faces:
        if color is not None:
            counts[color] = counts.get(color, 0) + 1
    if not counts:
        counts = all_style_counts
    dominant = max(counts.items(), key=lambda item: item[1])[0] if counts else None
    return StepColors(faces, dominant, solids)


def clear_cache():
    _cache.clear()
