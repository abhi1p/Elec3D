# -*- coding: utf-8 -*-
"""Pure-Python coverage for Elec3D .e3d_pro projects."""

import json
import os

import pytest

from elec3d import projectfile


def test_new_project_paths_always_make_named_project_folder(tmp_path):
    paths = projectfile.new_project_paths(str(tmp_path / "Sensor.e3d_pro"))
    assert paths["project_dir"] == str((tmp_path / "Sensor").resolve())
    assert paths["project_file"].endswith(os.path.join("Sensor", "Sensor.e3d_pro"))
    assert paths["board_file"].endswith(os.path.join("Sensor", "Sensor.e3d_pcb"))
    assert paths["footprints_dir"].endswith(os.path.join("Sensor", "Footprints"))
    assert paths["models_dir"].endswith(os.path.join("Sensor", "Models"))


def test_new_project_paths_does_not_double_existing_named_folder(tmp_path):
    folder = tmp_path / "Sensor"
    folder.mkdir()
    paths = projectfile.new_project_paths(str(folder / "Sensor.e3d_pro"))
    assert paths["project_dir"] == str(folder.resolve())


def test_project_roundtrip_and_structure(tmp_path):
    paths = projectfile.new_project_paths(str(tmp_path / "Board.e3d_pro"))
    projectfile.ensure_structure(paths)
    # read_project requires the board to exist, just as a real project does.
    with open(paths["board_file"], "w", encoding="utf-8") as fh:
        fh.write("(elec3d_pcb (version 3))\n")
    projectfile.write_project(
        paths["project_file"], addon_version="0.12.0",
        board_file=paths["board_file"], footprints_dir=paths["footprints_dir"],
        models_dir=paths["models_dir"])
    restored = projectfile.read_project(paths["project_file"])
    assert restored["board_file"] == paths["board_file"]
    assert restored["footprints_dir"] == paths["footprints_dir"]
    assert restored["models_dir"] == paths["models_dir"]
    data = json.loads(open(paths["project_file"], encoding="utf-8").read())
    assert data["format"] == "Elec3D project"
    assert data["board"] == "Board.e3d_pcb"
    assert data["footprints_dir"] == "Footprints"
    assert data["models_dir"] == "Models"


def test_project_rejects_board_outside_project(tmp_path):
    folder = tmp_path / "Bad"
    folder.mkdir()
    pro = folder / "Bad.e3d_pro"
    pro.write_text(json.dumps({
        "format": "Elec3D project", "version": 1,
        "name": "Bad", "board": "../outside.e3d_pcb",
        "footprints_dir": "Footprints", "models_dir": "Models",
    }), encoding="utf-8")
    with pytest.raises(projectfile.ProjectError, match="escapes"):
        projectfile.read_project(str(pro))


def test_footprint_hash_ignores_line_ending_only_changes():
    assert projectfile.footprint_sha256_text("a\r\nb\r\n") == \
           projectfile.footprint_sha256_text("a\nb\n")


def test_footprint_asset_filename_preserves_library_identifier_case():
    assert projectfile.footprint_asset_filename(
        "Capacitor_SMD:C_1206_3216Metric",
        "${FOOTPRINT_DIR}/c_1206_3216metric.kicad_mod") == \
        "C_1206_3216Metric.kicad_mod"
    assert projectfile.footprint_asset_filename(
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        "${FOOTPRINT_DIR}/soic-8_3.9x4.9mm_p1.27mm.kicad_mod") == \
        "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"


def test_atomic_copy_file_skips_source_equal_to_destination(tmp_path, monkeypatch):
    model = tmp_path / "Models" / "C_1206_3216Metric.step"
    model.parent.mkdir()
    model.write_bytes(b"step-data")

    called = []
    real = projectfile.atomic_write_bytes
    def spy(path, data):
        called.append(path)
        return real(path, data)
    monkeypatch.setattr(projectfile, "atomic_write_bytes", spy)

    result = projectfile.atomic_copy_file(str(model), str(model))
    assert result == str(model.resolve())
    assert model.read_bytes() == b"step-data"
    assert called == []


def test_atomic_copy_file_reads_source_before_replacing_destination(tmp_path):
    source = tmp_path / "library.step"
    target = tmp_path / "Project" / "Models" / "library.step"
    source.write_bytes(b"new-step-data")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old-step-data")
    projectfile.atomic_copy_file(str(source), str(target))
    assert target.read_bytes() == b"new-step-data"
