# -*- coding: utf-8 -*-
"""Elec3D project files (``.e3d_pro``).

A project is a normal directory, intentionally similar to KiCad's project
layout rather than a packaged archive::

    ProjectName/
        ProjectName.e3d_pro
        ProjectName.e3d_pcb
        Footprints/
        Models/

The ``.e3d_pro`` file is small JSON metadata.  The ``.e3d_pcb`` remains the
complete authoritative board/layout file; project-local asset folders provide
portable footprint/model copies and are checked against the embedded layout
fingerprints when a project is reopened.

This module is Qt/FreeCAD-free so paths/project validation can be unit tested.
"""

import hashlib
import json
import os
import tempfile

from . import designfile

__all__ = [
    "PROJECT_EXTENSION", "PROJECT_FORMAT_VERSION", "ProjectError",
    "sha256_bytes", "sha256_file", "footprint_sha256_text", "safe_asset_name", "same_path",
    "footprint_asset_filename", "atomic_copy_file",
    "new_project_paths", "existing_project_paths", "ensure_structure",
    "write_project", "read_project", "atomic_write_bytes", "atomic_write_text",
]

PROJECT_EXTENSION = ".e3d_pro"
PROJECT_FORMAT_VERSION = 1
FOOTPRINT_DIR_NAME = "Footprints"
MODEL_DIR_NAME = "Models"


class ProjectError(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _normalise_footprint_text(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def footprint_sha256_text(text):
    return sha256_bytes(_normalise_footprint_text(text).encode("utf-8"))


def safe_asset_name(name, default="asset"):
    raw = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not raw:
        raw = default
    out = []
    for ch in raw:
        if ch.isalnum() or ch in (".", "-", "_", " "):
            out.append(ch)
        else:
            out.append("_")
    result = "".join(out).strip(" .")
    return result or default




def footprint_asset_filename(footprint_name, source_reference="", parsed_name=""):
    """Choose a project-local ``.kicad_mod`` name without losing KiCad case.

    KiCad library identifiers carry the canonical footprint spelling after the
    optional ``Library:`` prefix.  Prefer that spelling over a saved path token
    because older board files may contain lower-cased filenames.
    """
    canonical = str(footprint_name or parsed_name or "").split(":")[-1].strip()
    if canonical:
        if not canonical.lower().endswith(".kicad_mod"):
            canonical += ".kicad_mod"
        return safe_asset_name(canonical, "footprint.kicad_mod")
    preferred = os.path.basename(str(source_reference or "").replace("\\", "/"))
    if not preferred.lower().endswith(".kicad_mod"):
        preferred = "footprint.kicad_mod"
    return safe_asset_name(preferred, "footprint.kicad_mod")


def same_path(left, right):
    """Return True when two path spellings address the same filesystem file.

    This is deliberately tolerant of Windows' case-insensitive path spelling.
    ``os.path.samefile`` is used when both paths already exist, while the
    normalized absolute-path comparison also works before the destination is
    created.
    """
    if not left or not right:
        return False
    a = os.path.normcase(os.path.abspath(str(left)))
    b = os.path.normcase(os.path.abspath(str(right)))
    if a == b:
        return True
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _with_extension(path, extension):
    path = os.path.abspath(str(path))
    if not path.lower().endswith(extension):
        path += extension
    return path


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0] or "Project"


def new_project_paths(selected_project_file):
    """Resolve a New Project/Save As dialog selection to project paths.

    Selecting ``C:/Work/MyBoard.e3d_pro`` creates ``C:/Work/MyBoard/`` and puts
    ``MyBoard.e3d_pro`` plus ``MyBoard.e3d_pcb`` inside it.  If the caller has
    already navigated into a folder named ``MyBoard``, it is reused rather than
    producing ``MyBoard/MyBoard``.
    """
    selected = _with_extension(selected_project_file, PROJECT_EXTENSION)
    name = _stem(selected)
    parent = os.path.dirname(selected)
    if os.path.basename(os.path.normpath(parent)).casefold() == name.casefold():
        project_dir = parent
    else:
        project_dir = os.path.join(parent, name)
    project_file = os.path.join(project_dir, name + PROJECT_EXTENSION)
    return {
        "name": name,
        "project_dir": os.path.abspath(project_dir),
        "project_file": os.path.abspath(project_file),
        "board_file": os.path.abspath(os.path.join(project_dir, name + designfile.DESIGN_EXTENSION)),
        "footprints_dir": os.path.abspath(os.path.join(project_dir, FOOTPRINT_DIR_NAME)),
        "models_dir": os.path.abspath(os.path.join(project_dir, MODEL_DIR_NAME)),
    }


def existing_project_paths(project_file, metadata=None):
    project_file = _with_extension(project_file, PROJECT_EXTENSION)
    project_dir = os.path.dirname(project_file)
    name = _stem(project_file)
    metadata = dict(metadata or {})
    board_name = str(metadata.get("board") or (name + designfile.DESIGN_EXTENSION))
    fp_name = str(metadata.get("footprints_dir") or FOOTPRINT_DIR_NAME)
    model_name = str(metadata.get("models_dir") or MODEL_DIR_NAME)

    def local_member(member, label):
        # Project metadata is intentionally project-relative; never allow it to
        # escape the project directory.
        if os.path.isabs(member):
            raise ProjectError("%s must be relative to the project folder" % label)
        target = os.path.abspath(os.path.normpath(os.path.join(project_dir, member)))
        root = os.path.abspath(project_dir)
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:
            inside = False
        if not inside:
            raise ProjectError("%s escapes the project folder" % label)
        return target

    return {
        "name": str(metadata.get("name") or name),
        "project_dir": os.path.abspath(project_dir),
        "project_file": os.path.abspath(project_file),
        "board_file": local_member(board_name, "Project board path"),
        "footprints_dir": local_member(fp_name, "Project footprint folder"),
        "models_dir": local_member(model_name, "Project model folder"),
    }


def ensure_structure(paths):
    for key in ("project_dir", "footprints_dir", "models_dir"):
        folder = os.path.abspath(str(paths[key]))
        if not os.path.isdir(folder):
            os.makedirs(folder)
    return paths


def _atomic_replace(path, write_fn):
    path = os.path.abspath(str(path))
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    fd, temp = tempfile.mkstemp(prefix=".e3d-project-", suffix=".tmp", dir=folder or None)
    os.close(fd)
    try:
        write_fn(temp)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return path


def atomic_write_bytes(path, data):
    payload = bytes(data)
    def write(temp):
        with open(temp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    return _atomic_replace(path, write)


def atomic_copy_file(source, target):
    """Atomically copy a file unless source and destination are the same file.

    The source handle is always closed before replacing the destination.  This
    is required on Windows, where replacing an open destination raises
    ``PermissionError`` / WinError 5.
    """
    source = os.path.abspath(str(source))
    target = os.path.abspath(str(target))
    if same_path(source, target):
        return target
    with open(source, "rb") as fh:
        payload = fh.read()
    return atomic_write_bytes(target, payload)


def atomic_write_text(path, text):
    payload = str(text)
    def write(temp):
        with open(temp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    return _atomic_replace(path, write)


def write_project(project_file, addon_version="", board_file=None,
                  footprints_dir=None, models_dir=None):
    project_file = _with_extension(project_file, PROJECT_EXTENSION)
    base = existing_project_paths(project_file)
    board_file = os.path.abspath(board_file or base["board_file"])
    footprints_dir = os.path.abspath(footprints_dir or base["footprints_dir"])
    models_dir = os.path.abspath(models_dir or base["models_dir"])
    project_dir = os.path.dirname(project_file)

    def relative(target, label):
        rel = os.path.relpath(target, project_dir).replace("\\", "/")
        if rel == ".." or rel.startswith("../"):
            raise ProjectError("%s must live inside the project folder" % label)
        return rel

    data = {
        "format": "Elec3D project",
        "version": PROJECT_FORMAT_VERSION,
        "name": _stem(project_file),
        "board": relative(board_file, "Board"),
        "footprints_dir": relative(footprints_dir, "Footprint folder"),
        "models_dir": relative(models_dir, "Model folder"),
        "addon_version": str(addon_version or ""),
    }
    atomic_write_text(project_file, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return project_file


def read_project(project_file):
    project_file = _with_extension(project_file, PROJECT_EXTENSION)
    if not os.path.isfile(project_file):
        raise ProjectError("Project file does not exist: %s" % project_file)
    try:
        with open(project_file, "r", encoding="utf-8", errors="strict") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise ProjectError("Could not read project file: %s" % exc)
    if str(data.get("format", "")) != "Elec3D project":
        raise ProjectError("Not a Elec3D project file.")
    try:
        version = int(data.get("version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version <= 0:
        raise ProjectError("Project file has no supported format version.")
    if version > PROJECT_FORMAT_VERSION:
        raise ProjectError(
            "Project file format %d is newer than supported format %d." %
            (version, PROJECT_FORMAT_VERSION))
    paths = existing_project_paths(project_file, data)
    if not os.path.isfile(paths["board_file"]):
        raise ProjectError("Project board file is missing: %s" % paths["board_file"])
    paths["metadata"] = data
    return paths
