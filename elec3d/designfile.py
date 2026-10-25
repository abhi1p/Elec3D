# -*- coding: utf-8 -*-
"""Elec3D document format (``.e3d_pcb``).

The syntax deliberately follows KiCad's readable S-expression style while the
schema belongs to this add-on.  The file stores board/editor state and external
resource *references*; component STEP/WRL model bytes are never embedded.  The
board solid is the one exception: its tessellated mesh is embedded (compressed)
next to the reference to its STEP source, so the board boundary of every copper
layer can always be reconstructed.

This module contains no Qt or FreeCAD imports.  It handles text encoding,
versioning and portable path variables.  Viewer capture/restore lives in
``panel2d.py`` because that is where QGraphicsItem state exists.
"""

import os
import re
import tempfile

from . import sexpr
from .sexpr import child, children

__all__ = [
    "DESIGN_EXTENSION", "FORMAT_VERSION", "DesignFileError",
    "infer_path_variables", "portable_path", "resolve_path",
    "dumps", "loads", "write_file", "read_file",
]

DESIGN_EXTENSION = ".e3d_pcb"
FORMAT_VERSION = 5
_ROOT = "elec3d_pcb"
_VAR_RX = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class DesignFileError(Exception):
    pass


def _q(value):
    text = str(value if value is not None else "")
    text = (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"%s"' % text


def _f(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if abs(value) < 5e-13:
        value = 0.0
    return ("%.12g" % value)


def _i(value, default=0):
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(int(default))


def _b(value):
    return "yes" if bool(value) else "no"


def _indent(lines, level=1):
    pad = "  " * level
    return [pad + line for line in lines]


def _slash(path):
    return str(path or "").replace("\\", "/")


def _under(path, root):
    """Return a portable relative path if *path* is inside *root*."""
    try:
        path = os.path.normcase(os.path.abspath(path))
        root = os.path.normcase(os.path.abspath(root))
        common = os.path.commonpath([path, root])
        if common != root:
            return None
        return os.path.relpath(path, root)
    except (ValueError, OSError):
        return None


def _var_value(path, design_dir):
    if not path:
        return ""
    path = os.path.abspath(path)
    rel = _under(path, design_dir)
    if rel is not None:
        if rel in ("", "."):
            return "${DESIGN_DIR}"
        return "${DESIGN_DIR}/" + _slash(rel)
    return _slash(path)


def infer_path_variables(design_file, footprint_paths=None, models_dir=""):
    """Create the standard project path table for a design.

    ``DESIGN_DIR`` is implicit and always points at the file's containing
    folder.  ``FOOTPRINT_DIR`` is the common directory of all referenced
    footprint sources when one can be determined.  ``MODEL_DIR`` preserves the
    board's configured 3D model search directory even if it is currently
    missing, so moving a project does not erase the intended reference.
    """
    design_dir = os.path.dirname(os.path.abspath(design_file))
    result = {"DESIGN_DIR": "."}

    dirs = []
    for path in footprint_paths or []:
        if not path:
            continue
        raw = os.path.expanduser(os.path.expandvars(str(path)))
        if os.path.isabs(raw):
            dirs.append(os.path.dirname(os.path.abspath(raw)))
    if dirs:
        try:
            common = os.path.commonpath(dirs)
        except ValueError:
            common = ""
        # Avoid inventing an unhelpful drive/root variable.
        if common and os.path.dirname(common) != common:
            result["FOOTPRINT_DIR"] = _var_value(common, design_dir)

    if models_dir:
        raw = os.path.expanduser(os.path.expandvars(str(models_dir)))
        if os.path.isabs(raw):
            result["MODEL_DIR"] = _var_value(raw, design_dir)
        else:
            result["MODEL_DIR"] = _slash(raw)
    return result


def _expanded_variables(variables, design_dir):
    values = dict(variables or {})
    values["DESIGN_DIR"] = os.path.abspath(design_dir)
    resolved = {"DESIGN_DIR": os.path.abspath(design_dir)}
    # Variables may refer to DESIGN_DIR or to a variable defined before/after
    # them.  A short bounded fixed-point expansion avoids recursive loops.
    for _ in range(12):
        changed = False
        for name, raw in values.items():
            if name == "DESIGN_DIR":
                continue
            text = str(raw or "")
            def repl(match):
                key = match.group(1)
                return resolved.get(key, os.environ.get(key, match.group(0)))
            text = _VAR_RX.sub(repl, text)
            text = os.path.expanduser(os.path.expandvars(text))
            if not os.path.isabs(text):
                text = os.path.join(design_dir, text)
            text = os.path.normpath(text)
            if resolved.get(name) != text:
                resolved[name] = text
                changed = True
        if not changed:
            break
    return resolved


def portable_path(path, variables, design_file, preferred=None):
    """Replace a concrete absolute path with the best ``${VAR}`` reference."""
    if not path:
        return ""
    raw = str(path)
    # Preserve unresolved KiCad/environment variables verbatim.  They can still
    # be found by the model-folder basename index at load time.
    if _VAR_RX.search(raw) and not os.path.isabs(os.path.expandvars(raw)):
        return _slash(raw)
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(expanded):
        return _slash(raw)
    absolute = os.path.abspath(expanded)
    design_dir = os.path.dirname(os.path.abspath(design_file))
    resolved = _expanded_variables(variables, design_dir)

    order = []
    if preferred and preferred in resolved:
        order.append(preferred)
    for name in ("FOOTPRINT_DIR", "MODEL_DIR", "DESIGN_DIR"):
        if name in resolved and name not in order:
            order.append(name)
    # Longest root wins when custom variables are added later.
    for name in sorted((n for n in resolved if n not in order),
                       key=lambda n: len(resolved[n]), reverse=True):
        order.append(name)

    candidates = []
    for name in order:
        rel = _under(absolute, resolved[name])
        if rel is not None:
            candidates.append((len(resolved[name]), name, rel))
    if not candidates:
        return _slash(absolute)
    _length, name, rel = max(candidates)
    return "${%s}" % name if rel in ("", ".") else "${%s}/%s" % (name, _slash(rel))


def resolve_path(path, variables, design_file):
    """Expand design variables/environment variables and return an absolute path.

    Unknown variables are intentionally left in the returned text rather than
    treated as fatal; callers can report the missing resource and continue.
    """
    if not path:
        return ""
    design_dir = os.path.dirname(os.path.abspath(design_file))
    resolved = _expanded_variables(variables, design_dir)
    text = str(path)
    for _ in range(12):
        before = text
        def repl(match):
            key = match.group(1)
            return resolved.get(key, os.environ.get(key, match.group(0)))
        text = _VAR_RX.sub(repl, text)
        text = os.path.expanduser(os.path.expandvars(text))
        if text == before:
            break
    if _VAR_RX.search(text):
        return os.path.normpath(text)
    if not os.path.isabs(text):
        text = os.path.join(design_dir, text)
    return os.path.abspath(os.path.normpath(text))


def _node_value(node, name, default=""):
    c = child(node, name)
    return c[1] if c is not None and len(c) > 1 and isinstance(c[1], str) else default


def _num(node, name, default=0.0):
    try:
        return float(_node_value(node, name, default))
    except (TypeError, ValueError):
        return float(default)


def _int(node, name, default=0):
    try:
        return int(float(_node_value(node, name, default)))
    except (TypeError, ValueError):
        return int(default)


def _bool_value(node, name, default=False):
    raw = str(_node_value(node, name, "yes" if default else "no")).lower()
    return raw in ("yes", "true", "1", "on")


def _scalar_props(data, keys):
    lines = []
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if value is None:
            continue
        if isinstance(value, bool):
            atom = _b(value)
        elif isinstance(value, (int, float)):
            atom = _f(value)
        else:
            atom = _q(value)
        lines.append("(%s %s)" % (key, atom))
    return lines



_SOLID_SOURCE_KEYS = ("kind", "path", "original_path", "sha256", "label", "document", "object")
_CHUNK = 4000


def _chunks(text):
    text = str(text or "")
    return [text[i:i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]


def _board_solid_lines(solid):
    """Serialize ``BoardSolid.snapshot()`` data (or nothing)."""
    if not solid:
        return []
    lines = ["  (board_solid"]
    source = solid.get("source") or {}
    lines.append("    (source")
    for key in _SOLID_SOURCE_KEYS:
        if source.get(key):
            lines.append("      (%s %s)" % (key, _q(source.get(key))))
    lines.append("    )")
    placement = solid.get("placement") or {}
    lines.append("    (placement (up %s) (rotation %s) (offset %s %s))" % (
        _q(placement.get("up_axis", "+Z")), _f(placement.get("rotation", 0.0)),
        _f(placement.get("offset_x", 0.0)), _f(placement.get("offset_y", 0.0))))
    bounds = solid.get("exact_bounds")
    if bounds and len(bounds) == 6:
        lines.append("    (exact_bounds %s)" % " ".join(_f(v) for v in bounds))
    lines.append("    (mesh (vertex_count %s) (triangle_count %s)" % (
        _i(solid.get("vertex_count", 0)), _i(solid.get("triangle_count", 0))))
    for key in ("vertices", "triangles"):
        lines.append("      (%s" % key)
        for chunk in _chunks(solid.get(key, "")):
            lines.append("        %s" % _q(chunk))
        lines.append("      )")
    lines.append("    )")
    lines.append("  )")
    return lines


def _read_board_solid(node):
    if node is None:
        return None
    source = {}
    sn = child(node, "source") or []
    for key in _SOLID_SOURCE_KEYS:
        value = _node_value(sn, key, "")
        if value:
            source[key] = value
    pn = child(node, "placement") or []
    offset = child(pn, "offset") or ["offset", "0", "0"]
    try:
        offset_x, offset_y = float(offset[1]), float(offset[2])
    except (IndexError, TypeError, ValueError):
        offset_x = offset_y = 0.0
    placement = {"up_axis": _node_value(pn, "up", "+Z"),
                 "rotation": _num(pn, "rotation", 0.0),
                 "offset_x": offset_x, "offset_y": offset_y}
    mesh = child(node, "mesh") or []
    result = {
        "source": source, "placement": placement,
        "vertex_count": _int(mesh, "vertex_count", 0),
        "triangle_count": _int(mesh, "triangle_count", 0),
        "vertices": "".join(str(x) for x in (child(mesh, "vertices") or ["vertices"])[1:]),
        "triangles": "".join(str(x) for x in (child(mesh, "triangles") or ["triangles"])[1:]),
    }
    bn = child(node, "exact_bounds")
    if bn is not None and len(bn) >= 7:
        try:
            result["exact_bounds"] = [float(v) for v in bn[1:7]]
        except (TypeError, ValueError):
            pass
    return result


def dumps(data):
    """Serialize a pure-Python design dictionary to the add-on S-expression."""
    data = dict(data or {})
    lines = ["(" + _ROOT]
    lines.append("  (version %d)" % int(data.get("format_version", FORMAT_VERSION)))
    lines.append("  (generator %s)" % _q(data.get("generator", "Elec3D")))
    lines.append("  (addon_version %s)" % _q(data.get("addon_version", "")))

    lines.append("  (paths")
    for name, value in sorted((data.get("paths") or {}).items()):
        lines.append("    (var %s %s)" % (_q(name), _q(value)))
    lines.append("  )")

    stack = data.get("stack") or {}
    lines.append("  (board")
    lines.append("    (layers")
    for layer in stack.get("layers", []):
        lines.append("      (layer %s %s %s %s %s %s)" % (
            _i(layer.get("id")), _q(layer.get("name", "")), _q(layer.get("color", "")),
            _b(layer.get("visible", True)), _b(layer.get("renamed", False)),
            _b(layer.get("locked", False))))
    lines.append("      (active %s)" % _i(stack.get("active_id", 1)))
    lines.append("      (next_id %s)" % _i(stack.get("next_id", 2)))
    lines.append("    )")
    su = stack.get("stackup") or {}
    lines.append("    (stackup")
    lines.append("      (bottom_margin %s)" % _f(su.get("bottom_margin", 1.0)))
    for item in su.get("copper", []):
        lines.append("      (copper %s %s)" % (_i(item.get("layer_id")), _f(item.get("thickness", 0.035))))
    for item in su.get("separations", []):
        lines.append("      (separation %s %s %s)" % (
            _i(item.get("lower_id")), _i(item.get("upper_id")), _f(item.get("thickness", 1.6))))
    lines.append("    )")
    lines.append("  )")

    rules = data.get("design_rules") or {}
    lines.append("  (design_rules")
    for key, value in sorted(rules.items()):
        lines.append("    (rule %s %s)" % (_q(key), _f(value)))
    lines.append("  )")

    lines.append("  (footprints")
    for fp in data.get("footprints", []):
        lines.append("    (footprint")
        for line in _scalar_props(fp, ("uid", "source", "source_original", "source_sha256", "source_text",
                                           "ref", "value", "footprint_name", "placeholder")):
            lines.append("      " + line)
        lines.append("      (at %s %s %s)" % (_f(fp.get("x", 0.0)), _f(fp.get("y", 0.0)), _f(fp.get("rotation", 0.0))))
        if fp.get("design_layer_id") is not None:
            lines.append("      (design_layer %s)" % _i(fp.get("design_layer_id")))
        lines.append("      (hidden_texts %s)" % _b(fp.get("hidden_texts", False)))
        for name, visible in sorted((fp.get("layers") or {}).items()):
            lines.append("      (graphic_layer %s %s)" % (_q(name), _b(visible)))
        model = fp.get("model") or {}
        if model:
            lines.append("      (model")
            lines.append("        (path %s)" % _q(model.get("path", "")))
            if model.get("original_path"):
                lines.append("        (original_path %s)" % _q(model.get("original_path", "")))
            if model.get("sha256"):
                lines.append("        (sha256 %s)" % _q(model.get("sha256", "")))
            for key in ("offset", "scale", "rotate"):
                vec = list(model.get(key) or ([1.0, 1.0, 1.0] if key == "scale" else [0.0, 0.0, 0.0]))
                vec += [0.0, 0.0, 0.0]
                lines.append("        (%s %s %s %s)" % (key, _f(vec[0]), _f(vec[1]), _f(vec[2])))
            lines.append("      )")
        adjust = fp.get("model_adjust") or {}
        lines.append("      (model_adjust %s %s %s %s)" % (
            _q(fp.get("model_adjust_mode", "auto")), _f(adjust.get("x", 0.0)),
            _f(adjust.get("y", 0.0)), _f(adjust.get("z", 0.0))))
        if str(fp.get("model_display", "model")) == "box":
            lines.append("      (model_display \"box\")")
        margin = fp.get("box_margin") or {}
        top = float(margin.get("top", 0.0) or 0.0)
        side = float(margin.get("side", 0.0) or 0.0)
        if top > 0.0 or side > 0.0:
            lines.append("      (box_margin (top %s) (side %s))" % (_f(top), _f(side)))
        lines.append("    )")
    lines.append("  )")

    lines.extend(_board_solid_lines(data.get("board_solid")))

    routing = data.get("routing") or {}
    copper = routing.get("copper") or {}
    lines.append("  (routing")
    for line in _scalar_props(routing, ("track_width", "via_diameter", "via_hole_diameter",
                                        "via_target_layer_id", "via_target_auto",
                                        "route_posture", "route_tool")):
        lines.append("    " + line)
    for track in copper.get("tracks", []):
        lines.append("    (track %s %s %s %s" % (
            _q(track.get("uid", "")), _i(track.get("layer_id")), _f(track.get("width", 0.25)),
            _q(track.get("net") or "")))
        for point in track.get("points", []):
            lines.append("      (xy %s %s)" % (_f(point[0]), _f(point[1])))
        lines.append("    )")
    for via in copper.get("vias", []):
        lines.append("    (via %s %s %s %s %s %s %s %s)" % (
            _q(via.get("uid", "")), _f(via.get("x", 0.0)), _f(via.get("y", 0.0)),
            _f(via.get("diameter", 0.8)), _f(via.get("hole_diameter", 0.0)),
            _i(via.get("start_layer_id")), _i(via.get("end_layer_id")),
            _q(via.get("net") or "")))
    lines.append("  )")

    netlist = data.get("netlist")
    if netlist:
        lines.append("  (netlist")
        lines.append("    (source %s)" % _q(netlist.get("source_file", "")))
        for comp in netlist.get("components", []):
            lines.append("    (component %s %s %s)" % (
                _q(comp.get("ref", "")), _q(comp.get("value", "")), _q(comp.get("footprint", ""))))
        for net in netlist.get("nets", []):
            lines.append("    (net %s" % _q(net.get("name", "")))
            for nd in net.get("nodes", []):
                lines.append("      (node %s %s)" % (_q(nd.get("ref", "")), _q(nd.get("pin", ""))))
            lines.append("    )")
        lines.append("  )")

    view = data.get("view") or {}
    lines.append("  (view")
    for line in _scalar_props(view, ("grid_visible", "grid_step", "snap_enabled", "crosshair",
                                     "zoom", "auto_fit", "show_ratsnest", "net_highlight_enabled",
                                     "view_mode")):
        lines.append("    " + line)
    center = view.get("center") or [0.0, 0.0]
    lines.append("    (center %s %s)" % (_f(center[0]), _f(center[1])))
    if view.get("active_uid"):
        lines.append("    (active_footprint %s)" % _q(view.get("active_uid")))
    for uid in view.get("selected_footprints", []):
        lines.append("    (selected_footprint %s)" % _q(uid))
    for uid in view.get("selected_copper", []):
        lines.append("    (selected_copper %s)" % _q(uid))
    for net in view.get("highlighted_nets", []):
        lines.append("    (highlighted_net %s)" % _q(net))
    for key, expanded in sorted((view.get("sections") or {}).items()):
        lines.append("    (section %s %s)" % (_q(key), _b(expanded)))
    lines.append("  )")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _vec(node, name, default):
    c = child(node, name)
    if c is None:
        return list(default)
    result = []
    for i in range(1, 4):
        try:
            result.append(float(c[i]))
        except (IndexError, TypeError, ValueError):
            result.append(float(default[i - 1]))
    return result


def loads(text):
    """Parse design text to a pure-Python dictionary."""
    try:
        root = sexpr.parse(text)
    except Exception as exc:
        raise DesignFileError("Invalid design file: %s" % exc)
    if not root or root[0] != _ROOT:
        raise DesignFileError("Not a Elec3D design file.")
    version = _int(root, "version", 0)
    if version <= 0:
        raise DesignFileError("Design file has no supported format version.")
    if version > FORMAT_VERSION:
        raise DesignFileError(
            "Design file format %d is newer than supported format %d." % (version, FORMAT_VERSION))

    data = {
        "format_version": version,
        "generator": _node_value(root, "generator", ""),
        "addon_version": _node_value(root, "addon_version", ""),
        "paths": {}, "stack": {}, "design_rules": {}, "footprints": [], "outlines": [],
        "board_solid": None,
        "routing": {}, "netlist": None, "view": {},
    }
    pn = child(root, "paths") or []
    for var in children(pn, "var"):
        if len(var) >= 3:
            data["paths"][str(var[1])] = str(var[2])

    board = child(root, "board") or []
    ln = child(board, "layers") or []
    layers = []
    for node in children(ln, "layer"):
        if len(node) >= 7:
            layers.append({
                "id": int(float(node[1])), "name": node[2], "color": node[3],
                "visible": str(node[4]).lower() == "yes",
                "renamed": str(node[5]).lower() == "yes",
                "kind": "copper", "locked": str(node[6]).lower() == "yes",
            })
    outline_layer = child(ln, "outline_layer")
    outline = {"id": 0, "name": "Outline", "color": "#D0C05C",
               "visible": True, "renamed": True, "kind": "outline", "locked": True}
    if outline_layer is not None and len(outline_layer) >= 5:
        outline.update({"id": int(float(outline_layer[1])), "name": outline_layer[2],
                        "color": outline_layer[3],
                        "visible": str(outline_layer[4]).lower() == "yes"})
    su_node = child(board, "stackup") or []
    stackup = {
        "bottom_margin": _num(su_node, "bottom_margin", 1.0),
        "copper": [], "separations": [],
    }
    # Only files written before v0.17 have a top margin; keep it so their
    # drawn outlines can be migrated into a solid of the same height.
    if child(su_node, "top_margin") is not None:
        stackup["top_margin"] = _num(su_node, "top_margin", 1.0)
    for c in children(su_node, "copper"):
        if len(c) >= 3:
            stackup["copper"].append({"layer_id": int(float(c[1])), "thickness": float(c[2])})
    for s in children(su_node, "separation"):
        if len(s) >= 4:
            stackup["separations"].append({"lower_id": int(float(s[1])),
                                             "upper_id": int(float(s[2])),
                                             "thickness": float(s[3])})
    data["stack"] = {"next_id": _int(ln, "next_id", len(layers) + 1),
                     "active_id": _int(ln, "active", layers[0]["id"] if layers else 1),
                     "outline": outline, "layers": layers, "stackup": stackup}

    dr = child(root, "design_rules") or []
    for rule in children(dr, "rule"):
        if len(rule) >= 3:
            try:
                data["design_rules"][str(rule[1])] = float(rule[2])
            except (TypeError, ValueError):
                pass

    fps = child(root, "footprints") or []
    for node in children(fps, "footprint"):
        at = child(node, "at") or ["at", "0", "0", "0"]
        fp = {
            "uid": _node_value(node, "uid", ""), "source": _node_value(node, "source", ""),
            "source_original": _node_value(node, "source_original", ""),
            "source_sha256": _node_value(node, "source_sha256", ""),
            "source_text": _node_value(node, "source_text", ""),
            "ref": _node_value(node, "ref", ""), "value": _node_value(node, "value", ""),
            "footprint_name": _node_value(node, "footprint_name", ""),
            "placeholder": _bool_value(node, "placeholder", False),
            "x": float(at[1]) if len(at) > 1 else 0.0,
            "y": float(at[2]) if len(at) > 2 else 0.0,
            "rotation": float(at[3]) if len(at) > 3 else 0.0,
            "design_layer_id": _int(node, "design_layer", 1),
            "hidden_texts": _bool_value(node, "hidden_texts", False),
            "layers": {}, "model": None,
            "model_adjust": {"x": 0.0, "y": 0.0, "z": 0.0}, "model_adjust_mode": "auto",
            "model_display": "box" if _node_value(node, "model_display", "model") == "box" else "model",
        }
        bm = child(node, "box_margin")
        if bm is not None:
            fp["box_margin"] = {"top": max(0.0, _num(bm, "top", 0.0)),
                                "side": max(0.0, _num(bm, "side", 0.0))}
        for gl in children(node, "graphic_layer"):
            if len(gl) >= 3:
                fp["layers"][gl[1]] = str(gl[2]).lower() == "yes"
        mn = child(node, "model")
        if mn is not None:
            fp["model"] = {"path": _node_value(mn, "path", ""),
                           "original_path": _node_value(mn, "original_path", ""),
                           "sha256": _node_value(mn, "sha256", ""),
                           "offset": _vec(mn, "offset", [0.0, 0.0, 0.0]),
                           "scale": _vec(mn, "scale", [1.0, 1.0, 1.0]),
                           "rotate": _vec(mn, "rotate", [0.0, 0.0, 0.0])}
        ma = child(node, "model_adjust")
        if ma is not None:
            if len(ma) > 1:
                fp["model_adjust_mode"] = str(ma[1])
            vals = []
            for i in range(2, 5):
                try: vals.append(float(ma[i]))
                except (IndexError, TypeError, ValueError): vals.append(0.0)
            fp["model_adjust"] = {"x": vals[0], "y": vals[1], "z": vals[2]}
        data["footprints"].append(fp)

    data["board_solid"] = _read_board_solid(child(root, "board_solid"))

    # Legacy (format <= 4) drawn outlines.  Read only for migration.
    on = child(root, "outline") or []
    for node in children(on, "shape"):
        if len(node) < 3:
            continue
        shape = {"uid": node[1], "kind": node[2]}
        for prop in children(node, "property"):
            if len(prop) < 3:
                continue
            key, raw = prop[1], prop[2]
            try: shape[key] = float(raw)
            except (TypeError, ValueError): shape[key] = raw
        data["outlines"].append(shape)

    rn = child(root, "routing") or []
    routing = {
        "track_width": _num(rn, "track_width", 0.25),
        "via_diameter": _num(rn, "via_diameter", 0.8),
        "via_hole_diameter": _num(rn, "via_hole_diameter", 0.0),
        "via_target_layer_id": _int(rn, "via_target_layer_id", 1),
        # Files written before 0.12.6 had only a concrete target layer, so
        # preserve their manual behaviour rather than silently switching them.
        "via_target_auto": _bool_value(rn, "via_target_auto", False),
        "route_posture": _int(rn, "route_posture", 0),
        "route_tool": _node_value(rn, "route_tool", "select"),
        "copper": {"tracks": [], "vias": []},
    }
    for t in children(rn, "track"):
        if len(t) < 5: continue
        routing["copper"]["tracks"].append({
            "uid": t[1], "layer_id": int(float(t[2])), "width": float(t[3]),
            "net": t[4] or None,
            "points": [[float(p[1]), float(p[2])] for p in children(t, "xy") if len(p) >= 3],
        })
    for v in children(rn, "via"):
        if len(v) < 9: continue
        routing["copper"]["vias"].append({
            "uid": v[1], "x": float(v[2]), "y": float(v[3]),
            "diameter": float(v[4]), "hole_diameter": float(v[5]),
            "start_layer_id": int(float(v[6])), "end_layer_id": int(float(v[7])),
            "net": v[8] or None,
        })
    data["routing"] = routing

    nn = child(root, "netlist")
    if nn is not None:
        netlist = {"source_file": _node_value(nn, "source", ""), "components": [], "nets": []}
        for c in children(nn, "component"):
            if len(c) >= 4:
                netlist["components"].append({"ref": c[1], "value": c[2], "footprint": c[3]})
        for n in children(nn, "net"):
            if len(n) >= 2:
                netlist["nets"].append({"name": n[1], "nodes": [
                    {"ref": nd[1], "pin": nd[2]} for nd in children(n, "node") if len(nd) >= 3]})
        data["netlist"] = netlist

    vn = child(root, "view") or []
    view = {
        "grid_visible": _bool_value(vn, "grid_visible", True),
        "grid_step": _num(vn, "grid_step", 0.1), "snap_enabled": _bool_value(vn, "snap_enabled", True),
        "crosshair": _bool_value(vn, "crosshair", True), "zoom": _num(vn, "zoom", 40.0),
        "auto_fit": _bool_value(vn, "auto_fit", False),
        "show_ratsnest": _bool_value(vn, "show_ratsnest", True),
        "net_highlight_enabled": _bool_value(vn, "net_highlight_enabled", False),
        "view_mode": _node_value(vn, "view_mode", "2d"),
        "selected_footprints": [n[1] for n in children(vn, "selected_footprint") if len(n) > 1],
        "selected_copper": [n[1] for n in children(vn, "selected_copper") if len(n) > 1],
        "highlighted_nets": [n[1] for n in children(vn, "highlighted_net") if len(n) > 1],
        "active_uid": _node_value(vn, "active_footprint", ""), "sections": {},
    }
    center = child(vn, "center")
    view["center"] = [float(center[1]), float(center[2])] if center is not None and len(center) >= 3 else [0.0, 0.0]
    for s in children(vn, "section"):
        if len(s) >= 3:
            view["sections"][s[1]] = str(s[2]).lower() == "yes"
    data["view"] = view
    return data


def write_file(path, data):
    """Atomically write a design file."""
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise DesignFileError("Design folder does not exist: %s" % folder)
    text = dumps(data)
    fd, temp = tempfile.mkstemp(prefix=".e3d-save-", suffix=".tmp", dir=folder or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            try: os.fsync(fh.fileno())
            except OSError: pass
        os.replace(temp, path)
    except Exception:
        try: os.unlink(temp)
        except OSError: pass
        raise
    return path


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as fh:
            return loads(fh.read())
    except DesignFileError:
        raise
    except Exception as exc:
        raise DesignFileError("Could not read design file: %s" % exc)
