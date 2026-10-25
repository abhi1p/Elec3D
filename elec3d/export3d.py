# -*- coding: utf-8 -*-
"""Exact-BRep STEP / FreeCAD export for the document-free PCB editor."""

from __future__ import division

import math
import os
import re
import uuid


COPPER_COLOR = "#C98A2E"
PAD_COLOR = "#D4AF37"
MODEL_COLOR = "#BFC5CD"
CAVITY_COLOR = "#8FA3B8"
BOARD_COLOR = "#3F7D4E"


class ExportError(RuntimeError):
    pass


def _safe_name(value, fallback="Part"):
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_")
    return clean or fallback


def component_name(entry):
    ref = _safe_name(entry.get("ref"), "Component_%s" % entry.get("uid", ""))
    value = _safe_name(entry.get("value"), "")
    fp = entry.get("fp")
    detail = value or _safe_name(getattr(fp, "name", ""), "")
    return "%s_%s" % (ref, detail) if detail and detail != ref else ref


def export_file_name(opts):
    opts = opts or {}
    if opts.get("negative"):
        return "cavity.step"
    copper = bool(opts.get("tracks") or opts.get("pads"))
    models = bool(opts.get("models"))
    board = bool(opts.get("board"))
    if board and not copper and not models:
        return "board.step"
    if (copper and models) or board:
        return "assembly.step"
    if models:
        return "components.step"
    if opts.get("tracks") and opts.get("pads"):
        return "copper.step"
    if opts.get("pads"):
        return "pads.step"
    if opts.get("tracks"):
        return "tracks.step"
    return "export.step"


def _entry_xy(entry):
    pos = entry.get("pos", (0.0, 0.0))
    if hasattr(pos, "x"):
        return float(pos.x()), float(pos.y())
    return float(pos[0]), float(pos[1])


def _layer_visible(stack, uid):
    layer = stack.by_id(uid) if stack is not None else None
    return bool(layer is not None and getattr(layer, "is_copper", False) and layer.visible)


def _resolve_model_path(model, model_dir=""):
    if model is None or not getattr(model, "path", ""):
        return ""
    raw = os.path.expanduser(os.path.expandvars(str(model.path)))
    candidates = [raw]
    if model_dir:
        candidates += [os.path.join(model_dir, raw),
                       os.path.join(model_dir, os.path.basename(raw.replace("\\", os.sep)))]
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate):
            return candidate
    base = os.path.basename(raw.replace("\\", os.sep)).lower()
    if model_dir and base and os.path.isdir(model_dir):
        for current, _dirs, files in os.walk(model_dir):
            for name in files:
                if name.lower() == base:
                    return os.path.join(current, name)
    return ""


def export_counts(entries, stack, solid, tracks, vias, model_dir=""):
    visible_tracks = [t for t in (tracks or []) if _layer_visible(stack, t.layer_id)]
    visible_vias = []
    order = [layer.id for layer in stack.layers] if stack is not None else []
    for via in (vias or []):
        if not order or via.start_layer_id not in order or via.end_layer_id not in order:
            continue
        a, b = sorted((order.index(via.start_layer_id), order.index(via.end_layer_id)))
        if any(stack.layers[i].visible for i in range(a, b + 1)):
            visible_vias.append(via)
    visible_entries = [e for e in (entries or []) if _layer_visible(stack, e.get("design_layer_id"))]
    pads = sum(len(getattr(e.get("fp"), "pads", []) or []) for e in visible_entries)
    models = missing = boxes = 0
    for entry in visible_entries:
        model = getattr(entry.get("fp"), "model", None)
        if model is None or not getattr(model, "path", ""):
            continue
        path = _resolve_model_path(model, model_dir)
        if path and path.lower().endswith((".step", ".stp")):
            models += 1
            if str(entry.get("model_display", "model")) == "box":
                boxes += 1
        else:
            missing += 1
    block = None
    if solid is not None:
        try:
            w, d, h = solid.size()
            if w > 0 and d > 0 and h > 0:
                block = {"w": w, "d": d, "h": h, "label": solid.label}
        except Exception:
            block = None
    return {"tracks": len(visible_tracks), "vias": len(visible_vias), "pads": pads,
            "models": models, "missing_models": missing, "model_boxes": boxes,
            "block": block}


def _fc_modules():
    try:
        import FreeCAD as App
        import Part
    except Exception as exc:
        raise ExportError("FreeCAD Part/OpenCascade is unavailable: %s" % exc)
    return App, Part


def _copy_shape(shape):
    try:
        return shape.copy()
    except Exception:
        return shape


def _document_exists(App, name):
    """Return whether *name* is an open FreeCAD document.

    FreeCAD's ``App.getDocument(name)`` raises ``Base::FreeCADError`` for a
    missing document; it does not return ``None``.  Keep that host-specific
    behavior behind one helper so existence probes never abort an export.
    """
    if not name:
        return False
    try:
        return str(name) in App.listDocuments()
    except Exception:
        try:
            App.getDocument(str(name))
            return True
        except Exception:
            return False



def document_is_open(name):
    """Public, exception-safe probe used by the export dialog."""
    if not name:
        return False
    try:
        import FreeCAD as App
    except Exception:
        return False
    return _document_exists(App, name)

def _track_shape(Part, App, track, z0, thickness):
    pts = list(getattr(track, "points", []) or [])
    if len(pts) < 2:
        return None
    width = max(0.001, float(track.width))
    pieces = []
    for i in range(1, len(pts)):
        ax, ay = pts[i - 1]; bx, by = pts[i]
        dx, dy = float(bx - ax), float(by - ay)
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        shape = Part.makeBox(length, width, thickness,
                             App.Vector(-length / 2.0, -width / 2.0, z0))
        shape.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1),
                     -math.degrees(math.atan2(dy, dx)))
        shape.translate(App.Vector((ax + bx) / 2.0, -(ay + by) / 2.0, 0.0))
        pieces.append(shape)
    for x, y in pts:
        pieces.append(Part.makeCylinder(width / 2.0, thickness,
                                        App.Vector(float(x), -float(y), z0)))
    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    try:
        return pieces[0].multiFuse(pieces[1:]).removeSplitter()
    except Exception:
        try:
            acc = pieces[0]
            for piece in pieces[1:]:
                acc = acc.fuse(piece)
            return acc.removeSplitter()
        except Exception:
            return Part.makeCompound(pieces)


def _via_shape(Part, App, via, z0, z1):
    bottom, top = sorted((float(z0), float(z1)))
    height = max(0.001, top - bottom)
    outer = Part.makeCylinder(max(0.0005, float(via.diameter) / 2.0), height,
                              App.Vector(float(via.x), -float(via.y), bottom))
    hole = max(0.0, min(float(via.hole_diameter), float(via.diameter)))
    if hole > 1e-6:
        try:
            inner = Part.makeCylinder(hole / 2.0, height + 0.02,
                                      App.Vector(float(via.x), -float(via.y), bottom - 0.01))
            return outer.cut(inner)
        except Exception:
            pass
    return outer


def _pad_shape(Part, App, entry, pad, plane_z, thickness):
    from .builder import pad_shape
    face = pad_shape(pad, 0.0)
    shape = face.extrude(App.Vector(0.0, 0.0, -float(thickness)))
    # builder.pad_shape() preserves KiCad's local footprint coordinates (+Y
    # down in the editor).  The 3D/export world is Z-up with +Y up, exactly
    # like view3d._transform_fp_point(), so mirror the complete local pad
    # (including drill offset and pad rotation) before the footprint transform.
    # This also changes local +pad.rot into the required -pad.rot.
    try:
        shape.mirror(App.Vector(0, 0, 0), App.Vector(0, 1, 0))
    except Exception as exc:
        raise ExportError("pad Y-axis conversion failed: %s" % exc)
    rotation = -float(entry.get("rotation", 0.0) or 0.0)
    if abs(rotation) > 1e-12:
        shape.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), rotation)
    px, py = _entry_xy(entry)
    shape.translate(App.Vector(px, -py, float(plane_z)))
    return shape

def _scaled_shape(App, shape, scale):
    sx, sy, sz = [float(v) for v in (scale or (1.0, 1.0, 1.0))]
    if abs(sx - 1.0) < 1e-12 and abs(sy - 1.0) < 1e-12 and abs(sz - 1.0) < 1e-12:
        return shape
    matrix = App.Matrix()
    matrix.A11 = sx; matrix.A22 = sy; matrix.A33 = sz
    try:
        transformed = shape.transformGeometry(matrix)
        return transformed if transformed is not None else shape
    except Exception:
        try:
            copy = _copy_shape(shape)
            transformed = copy.transformShape(matrix, True)
            return transformed if transformed is not None else copy
        except Exception as exc:
            raise ExportError("component model scale could not be applied: %s" % exc)


def _bounding_box_shape(Part, App, shape):
    """Solid box of *shape*'s extents in its current (footprint-local) frame.

    Taken before the footprint rotation, exactly like the 3D view's box, so
    the exported box is aligned with the component rather than the board.
    """
    try:
        bb = shape.optimalBoundingBox(True, False)
    except Exception:
        bb = shape.BoundBox
    size = [max(1e-4, float(v)) for v in (bb.XLength, bb.YLength, bb.ZLength)]
    return Part.makeBox(size[0], size[1], size[2],
                        App.Vector(float(bb.XMin), float(bb.YMin), float(bb.ZMin)))


def _model_shape(Part, App, path, entry, plane_z):
    model = getattr(entry.get("fp"), "model", None)
    if model is None:
        return None
    try:
        shape = Part.read(path)
    except Exception:
        shape = Part.Shape()
        shape.read(path)
    shape = _copy_shape(shape)
    if shape is None or getattr(shape, "isNull", lambda: False)():
        raise ExportError("component STEP contains no usable shape: %s" % os.path.basename(path))

    shape = _scaled_shape(App, shape, getattr(model, "scale", (1.0, 1.0, 1.0)))
    rx, ry, rz = [float(v) for v in (getattr(model, "rotate", None) or (0.0, 0.0, 0.0))]
    for axis, angle in ((App.Vector(1, 0, 0), -rx),
                        (App.Vector(0, 1, 0), -ry),
                        (App.Vector(0, 0, 1), -rz)):
        if abs(angle) > 1e-12:
            shape.rotate(App.Vector(0, 0, 0), axis, angle)
    ox, oy, oz = [float(v) for v in (getattr(model, "offset", None) or (0.0, 0.0, 0.0))]
    shape.translate(App.Vector(ox, oy, oz))
    if str(entry.get("model_display", "model")) == "box":
        shape = _bounding_box_shape(Part, App, shape)
    fp_rotation = -float(entry.get("rotation", 0.0) or 0.0)
    if abs(fp_rotation) > 1e-12:
        shape.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), fp_rotation)
    px, py = _entry_xy(entry)
    adjust = entry.get("model_adjust") or {}
    shape.translate(App.Vector(px + float(adjust.get("x", 0.0)),
                               -py - float(adjust.get("y", 0.0)),
                               float(plane_z) + float(adjust.get("z", 0.0))))
    return shape


def _board_block(solid):
    """Exact board body used as the cavity source.

    The imported solid itself is the board: it is placed exactly like the
    viewer places its mesh (oriented, rotated, lowest point on ``z = 0``).
    """
    if solid is None:
        raise ExportError("Export as negative requires an imported board solid "
                          "(File ▸ Import board solid…).")
    from .boardsolid import exact_shape_for_export, BoardSolidError
    try:
        shape = exact_shape_for_export(solid)
    except BoardSolidError as exc:
        raise ExportError(str(exc))
    except Exception as exc:
        raise ExportError("Could not build the board solid for export: %s" % exc)
    if shape is None or shape.isNull():
        raise ExportError("The board solid could not be converted to a B-Rep.")
    return shape


def _rgb_tuple(value, fallback=(0.75, 0.75, 0.75)):
    value = str(value or "")
    if len(value) == 7 and value.startswith("#"):
        try:
            return tuple(int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        except ValueError:
            pass
    return fallback


def _record(shape, name, color, group=None, face_colors=None, copper=False):
    return {"shape": shape, "name": _safe_name(name), "group": _safe_name(group or name),
            "color": color, "face_colors": list(face_colors or []), "copper": bool(copper)}


def _fuse_copper_records(Part, records):
    copper = [r for r in records if r.get("copper")]
    others = [r for r in records if not r.get("copper")]
    if len(copper) < 2:
        return records
    shapes = [r["shape"] for r in copper]
    try:
        fused = shapes[0].multiFuse(shapes[1:]).removeSplitter()
    except Exception:
        acc = shapes[0]
        for shape in shapes[1:]:
            acc = acc.fuse(shape)
        try:
            fused = acc.removeSplitter()
        except Exception:
            fused = acc
    return [_record(fused, "PCB_Copper", COPPER_COLOR, "PCB_Copper", copper=True)] + others


def _group_records(Part, records):
    buckets = {}
    order = []
    for record in records:
        key = record["group"]
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(record)
    grouped = []
    for key in order:
        members = buckets[key]
        if len(members) == 1:
            one = dict(members[0]); one["name"] = key
            grouped.append(one)
        else:
            grouped.append(_record(Part.makeCompound([m["shape"] for m in members]),
                                   key, members[0].get("color"), key,
                                   copper=all(m.get("copper") for m in members)))
    return grouped


def build_records(entries, stack, solid, tracks, vias, model_dir, opts, progress=None):
    App, Part = _fc_modules()
    opts = dict(opts or {})
    from .view3d import layer_heights
    zmap, _total_h = layer_heights(stack)
    records, warnings = [], []
    seq = 1

    def tell(text):
        if progress:
            progress(text)

    if opts.get("tracks", True):
        tell("Building routed copper…")
        order = [layer.id for layer in stack.layers]
        for track in tracks or []:
            layer = stack.by_id(track.layer_id)
            if layer is None or not layer.is_copper or not layer.visible:
                continue
            z = float(zmap.get(layer.id, 0.0))
            thickness = max(0.001, float(stack.copper_thickness(layer.id)))
            shape = _track_shape(Part, App, track, z - thickness, thickness)
            if shape is not None:
                records.append(_record(shape, "Track_%d" % seq, COPPER_COLOR,
                                       "PCB_Tracks", copper=True)); seq += 1
        for via in vias or []:
            if via.start_layer_id not in order or via.end_layer_id not in order:
                warnings.append("Skipped via %s: layer span is no longer valid." % via.uid)
                continue
            ai, bi = order.index(via.start_layer_id), order.index(via.end_layer_id)
            lo, hi = sorted((ai, bi))
            if not any(stack.layers[i].visible for i in range(lo, hi + 1)):
                continue
            lower, upper = stack.layers[lo], stack.layers[hi]
            z0 = float(zmap.get(lower.id, 0.0)) - float(stack.copper_thickness(lower.id))
            z1 = float(zmap.get(upper.id, z0))
            if abs(z1 - z0) < 1e-9:
                z1 = z0 + max(0.001, float(stack.copper_thickness(lower.id)))
            records.append(_record(_via_shape(Part, App, via, z0, z1),
                                   "Via_%d" % seq, COPPER_COLOR, "PCB_Vias", copper=True)); seq += 1

    if opts.get("pads", True):
        tell("Building footprint pads…")
        for entry in entries or []:
            layer = stack.by_id(entry.get("design_layer_id"))
            if layer is None or not layer.is_copper or not layer.visible:
                continue
            plane_z = float(zmap.get(layer.id, 0.0))
            thickness = max(0.001, float(stack.copper_thickness(layer.id)))
            ref = _safe_name(entry.get("ref"), _safe_name(getattr(entry.get("fp"), "name", "Footprint")))
            for index, pad in enumerate(getattr(entry.get("fp"), "pads", []) or [], 1):
                pin = _safe_name(getattr(pad, "number", "") or index, str(index))
                try:
                    records.append(_record(_pad_shape(Part, App, entry, pad, plane_z, thickness),
                                           "%s_Pad_%s" % (ref, pin), PAD_COLOR,
                                           "%s_Pads" % ref, copper=True))
                except Exception as exc:
                    warnings.append("Skipped %s pad %s: %s" % (ref, pin, exc))

    if opts.get("models", True):
        tell("Loading component STEP models…")
        from . import stepcolor
        for entry in entries or []:
            layer = stack.by_id(entry.get("design_layer_id"))
            if layer is None or not layer.is_copper or not layer.visible:
                continue
            model = getattr(entry.get("fp"), "model", None)
            if model is None or not getattr(model, "path", ""):
                continue
            path = _resolve_model_path(model, model_dir)
            name = component_name(entry)
            if not path:
                warnings.append("Missing component model for %s: %s" % (name, model.path)); continue
            if not path.lower().endswith((".step", ".stp")):
                warnings.append("Skipped %s model %s: STEP/STP is required for B-Rep export." %
                                (name, os.path.basename(path))); continue
            try:
                shape = _model_shape(Part, App, path, entry, float(zmap.get(layer.id, 0.0)))
                if str(entry.get("model_display", "model")) == "box":
                    records.append(_record(shape, name + "_Box", MODEL_COLOR, name))
                    continue
                colors = stepcolor.read_step_colors(path)
                neutral = _rgb_tuple(MODEL_COLOR)
                face_colors = [colors.color_for(i) or neutral
                               for i in range(len(getattr(shape, "Faces", []) or []))]
                records.append(_record(shape, name, MODEL_COLOR, name, face_colors=face_colors))
            except Exception as exc:
                warnings.append("Skipped component model %s: %s" % (name, exc))

    if opts.get("fuse") or opts.get("negative"):
        if any(r.get("copper") for r in records):
            tell("Fusing touching/overlapping copper…")
            try:
                records = _fuse_copper_records(Part, records)
            except Exception as exc:
                raise ExportError("Copper Boolean union failed: %s" % exc)

    if opts.get("negative"):
        tell("Subtracting board cavity…")
        if not records:
            raise ExportError("Nothing is selected to subtract from the board block.")
        block = _board_block(solid)
        try:
            tool = records[0]["shape"] if len(records) == 1 else Part.makeCompound([r["shape"] for r in records])
            cavity = block.cut(tool)
        except Exception as exc:
            raise ExportError("Board cavity subtraction failed: %s" % exc)
        records = [_record(cavity, "PCB_Cavity", CAVITY_COLOR, "PCB_Cavity")]
    else:
        if opts.get("board") and solid is not None:
            tell("Adding board solid…")
            try:
                records.insert(0, _record(_board_block(solid), "PCB_Board", BOARD_COLOR, "PCB_Board"))
            except ExportError as exc:
                warnings.append(str(exc))
        records = _group_records(Part, records)
    return records, warnings


def _apply_object_style(obj, color, face_colors=None):
    rgb = _rgb_tuple(color)
    try:
        obj.ViewObject.ShapeColor = rgb
        obj.ViewObject.LineColor = rgb
    except Exception:
        pass
    if face_colors:
        try:
            obj.ViewObject.DiffuseColor = [tuple(float(v) for v in c[:3]) for c in face_colors]
        except Exception:
            pass


def _find_export_root(doc, name=""):
    """Return the add-on-managed export root in *doc*, if one exists.

    With *name* the specific export part created earlier is returned (a
    document may hold several exports added side by side).
    """
    if name:
        try:
            obj = doc.getObject(str(name))
        except Exception:
            obj = None
        if obj is not None:
            return obj
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            if bool(getattr(obj, "Elec3DManaged", False)):
                return obj
        except Exception:
            pass
        if getattr(obj, "Name", "") == "Elec3DExport":
            return obj
    return None


def _clear_export_contents(doc, root):
    """Remove only objects owned by the KiCad export root.

    The FreeCAD document itself is deliberately retained so its MDI tab, save
    path and any unrelated user-created objects survive a board refresh.
    """
    if root is None:
        return
    children = list(getattr(root, "Group", []) or [])
    for obj in children:
        name = getattr(obj, "Name", "")
        if not name:
            continue
        try:
            doc.removeObject(name)
        except Exception:
            pass
    try:
        doc.recompute()
    except Exception:
        pass


def _populate_document(doc, records, source_name="", root=None, label="Elec3D PCB Export"):
    if root is None:
        root = doc.addObject("App::Part", "Elec3DExport")
        root.Label = label
    elif not str(getattr(root, "Label", "") or ""):
        root.Label = label
    try:
        if "Elec3DManaged" not in list(getattr(root, "PropertiesList", []) or []):
            root.addProperty("App::PropertyBool", "Elec3DManaged", "KiCad",
                             "Managed by the Elec3D add-on")
        root.Elec3DManaged = True
    except Exception:
        pass
    try:
        if "SourceDesign" not in list(getattr(root, "PropertiesList", []) or []):
            root.addProperty("App::PropertyString", "SourceDesign", "KiCad",
                             "Source Elec3D design")
        root.SourceDesign = str(source_name or "")
    except Exception:
        pass
    objects = []
    for record in records:
        obj = doc.addObject("Part::Feature", _safe_name(record["name"], "Part"))
        obj.Label = record["name"]
        obj.Shape = record["shape"]
        _apply_object_style(obj, record.get("color"), record.get("face_colors"))
        try:
            root.addObject(obj)
        except Exception:
            pass
        objects.append(obj)
    doc.recompute()
    return root, objects


def _document_name(source_name):
    base = os.path.splitext(os.path.basename(source_name or ""))[0] or "Elec3D"
    return _safe_name(base + "_Export", "Elec3D_Export")


def _new_hidden_temp_document(App, name):
    """Create a non-GUI temporary document for STEP serialization.

    FreeCAD 1.1 exposes ``hidden``/``temp`` document flags.  Using them keeps
    STEP export from momentarily creating/activating an MDI document tab.  The
    fallbacks preserve compatibility with older host builds, but current
    FreeCAD should take the first path.
    """
    attempts = (
        lambda: App.newDocument(str(name), hidden=True, temp=True),
        lambda: App.newDocument(str(name), "", True, True),
        lambda: App.newDocument(str(name)),
    )
    last = None
    for create in attempts:
        try:
            return create()
        except (TypeError, AttributeError) as exc:
            last = exc
    if last is not None:
        raise last
    raise ExportError("FreeCAD could not create a temporary STEP document.")


def _get_document(App, name):
    """Return an open FreeCAD document without letting a missing name raise."""
    if not name:
        return None
    try:
        docs = App.listDocuments()
        if str(name) in docs:
            return docs[str(name)]
    except Exception:
        pass
    try:
        return App.getDocument(str(name))
    except Exception:
        return None


def open_documents():
    """``[(name, label), ...]`` of open, visible FreeCAD documents."""
    try:
        import FreeCAD as App
        docs = App.listDocuments()
    except Exception:
        return []
    result = []
    for name, doc in docs.items():
        try:
            if getattr(doc, "Temporary", False) or getattr(doc, "Hidden", False):
                continue
        except Exception:
            pass
        result.append((str(name), str(getattr(doc, "Label", "") or name)))
    return result


def document_has_export(doc_name, root_name=""):
    """Whether *doc_name* is open and holds an Elec3D export part."""
    try:
        import FreeCAD as App
    except Exception:
        return False
    doc = _get_document(App, doc_name)
    return bool(doc is not None and _find_export_root(doc, root_name) is not None)


def create_freecad_document(entries, stack, solid, tracks, vias, model_dir, opts,
                            source_name="", progress=None, target_document="",
                            update_existing=True, mode=None, target_root=""):
    """Put the exported board into FreeCAD.

    ``mode``:

    ``"update"``  replace the contents of the Elec3D export part (*target_root*,
                  or the first one found) in ``target_document``; the tab, its
                  save path and unrelated objects are kept.
    ``"add"``     add the export as a *new* part to ``target_document`` next to
                  whatever is already there (several exports in one tab).
    ``"new"``     create a new document.

    Without ``mode`` the historical ``update_existing`` flag decides between
    ``"update"`` and ``"new"``.
    """
    App, _Part = _fc_modules()
    if mode not in ("update", "add", "new"):
        mode = "update" if update_existing else "new"
    records, warnings = build_records(entries, stack, solid, tracks, vias,
                                      model_dir, opts, progress=progress)
    if not records:
        raise ExportError("Nothing to export.")

    doc = _get_document(App, target_document) if mode in ("update", "add") else None
    created = False
    root = None
    if doc is None:
        if mode in ("update", "add") and target_document:
            warnings.append("FreeCAD document %s is no longer open; a new document was created."
                            % target_document)
        mode = "new"
        base = _document_name(source_name)
        name = base
        n = 2
        while _document_exists(App, name):
            name = "%s_%d" % (base, n); n += 1
        doc = App.newDocument(name)
        created = True
    elif mode == "update":
        root = _find_export_root(doc, target_root)
        if root is not None:
            _clear_export_contents(doc, root)

    label = "Elec3D PCB Export"
    if mode == "add":
        base = os.path.splitext(os.path.basename(source_name or ""))[0]
        label = "%s Export" % base if base else label

    try:
        App.setActiveDocument(doc.Name)
    except Exception:
        pass
    try:
        root, objects = _populate_document(doc, records, source_name, root=root, label=label)
    except Exception:
        # Never destroy a pre-existing user document on an update failure.
        if created:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
        raise
    try:
        import FreeCADGui as Gui
        try:
            gui_doc = Gui.getDocument(doc.Name)
        except Exception:
            gui_doc = Gui.activeDocument()
        if gui_doc is not None:
            gui_doc.activeView().viewAxonometric()
            gui_doc.activeView().fitAll()
    except Exception:
        pass
    return {"document": doc, "root": root, "objects": objects,
            "warnings": warnings, "parts": len(objects), "mode": mode,
            "updated": mode == "update", "added": mode == "add",
            "created": bool(created)}


def export_step(entries, stack, solid, tracks, vias, model_dir, opts, path,
                source_name="", progress=None):
    App, _Part = _fc_modules()
    try:
        import Import
    except Exception as exc:
        raise ExportError("FreeCAD STEP exporter is unavailable: %s" % exc)

    records, warnings = build_records(entries, stack, solid, tracks, vias,
                                      model_dir, opts, progress=progress)
    if not records:
        raise ExportError("Nothing to export.")
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise ExportError("Export folder does not exist: %s" % folder)

    # Do not capture/restore App.ActiveDocument here.  The Elec3D
    # viewer is a custom MDI tab and may be active while FreeCAD's logical
    # ActiveDocument still points at an older direct-export document.  Restoring
    # that document after STEP export would visibly switch away from the add-on.
    # A hidden temporary document leaves the GUI/MDI activation untouched.
    tmp_name = "Elec3D_STEP_%s" % uuid.uuid4().hex[:8]
    doc = _new_hidden_temp_document(App, tmp_name)
    pcurve_group = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/General")
    old_pcurve = pcurve_group.GetInt("WriteSurfaceCurveMode", 1)
    try:
        _root, objects = _populate_document(doc, records, source_name)
        # FreeCAD maps this preference to OpenCascade write.surfacecurve.mode.
        pcurve_group.SetInt("WriteSurfaceCurveMode", 0 if opts.get("no_pcurves", True) else 1)
        if progress:
            progress("Writing %s…" % os.path.basename(path))
        Import.export(objects, path)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise ExportError("FreeCAD did not create a STEP file.")
    finally:
        pcurve_group.SetInt("WriteSurfaceCurveMode", old_pcurve)
        try:
            App.closeDocument(tmp_name)
        except Exception:
            pass
    return {"path": path, "warnings": warnings, "parts": len(records),
            "bytes": os.path.getsize(path) if os.path.isfile(path) else 0}
