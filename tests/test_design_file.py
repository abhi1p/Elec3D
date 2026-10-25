# -*- coding: utf-8 -*-
"""Pure-Python coverage for the persistent .e3d_pcb board design format."""

import os
from pathlib import Path

import pytest

from elec3d.designfile import (
    DESIGN_EXTENSION, FORMAT_VERSION, DesignFileError,
    dumps, loads, infer_path_variables, portable_path, resolve_path,
    write_file, read_file,
)


def sample_design():
    return {
        "format_version": FORMAT_VERSION,
        "generator": "Elec3D",
        "addon_version": "0.10.0",
        "paths": {
            "DESIGN_DIR": ".",
            "FOOTPRINT_DIR": "${DESIGN_DIR}/footprints",
            "MODEL_DIR": "${DESIGN_DIR}/models",
        },
        "stack": {
            "next_id": 3, "active_id": 2,
            "outline": {"id": 0, "name": "Outline", "color": "#D0C05C",
                        "visible": True, "renamed": True, "kind": "outline", "locked": True},
            "layers": [
                {"id": 1, "name": "B.Cu", "color": "#4D7FC4", "visible": True,
                 "renamed": False, "kind": "copper", "locked": False},
                {"id": 2, "name": "F.Cu", "color": "#C83434", "visible": False,
                 "renamed": False, "kind": "copper", "locked": False},
            ],
            "stackup": {
                "top_margin": 0.9, "bottom_margin": 1.1,
                "copper": [{"layer_id": 1, "thickness": 0.035},
                           {"layer_id": 2, "thickness": 0.07}],
                "separations": [{"lower_id": 1, "upper_id": 2, "thickness": 1.2}],
            },
        },
        "footprints": [{
            "uid": "fp1", "source": "${FOOTPRINT_DIR}/R.kicad_mod",
            "source_original": "/library/R.kicad_mod", "source_sha256": "abc123",
            "source_text": '(footprint "R_0603" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))\n',
            "ref": "R1", "value": "10k", "footprint_name": "R_0603", "placeholder": False,
            "x": 12.5, "y": -3.25, "rotation": 90.0, "design_layer_id": 2,
            "hidden_texts": True, "layers": {"F.SilkS": False, "F.CrtYd": True},
            "model": {"path": "${MODEL_DIR}/R.step", "original_path": "${KICAD9_3DMODEL_DIR}/R.step",
                      "sha256": "def456", "offset": [1, 2, 3],
                      "scale": [1, 1, 1], "rotate": [0, 0, 90]},
            "model_adjust": {"x": 0.3, "y": -0.2, "z": 1.7},
            "model_adjust_mode": "manual",
        }],
        "outlines": [{"uid": "o1", "kind": "rectangle", "x": 5.0, "y": 6.0,
                      "width": 80.0, "height": 40.0}],
        "routing": {
            "track_width": 0.3, "via_diameter": 0.9, "via_hole_diameter": 0.4,
            "via_target_layer_id": 1, "via_target_auto": True,
            "route_posture": 1, "route_tool": "select",
            "copper": {
                "tracks": [{"uid": "t1", "layer_id": 2, "width": 0.3,
                            "points": [[1, 2], [3, 4], [5, 4]], "net": "GND"}],
                "vias": [{"uid": "v1", "x": 5, "y": 4, "diameter": 0.9,
                          "hole_diameter": 0.4, "start_layer_id": 1,
                          "end_layer_id": 2, "net": "GND"}],
            },
        },
        "netlist": {
            "source_file": "${DESIGN_DIR}/board.net",
            "components": [{"ref": "R1", "value": "10k", "footprint": "Resistor:R_0603"}],
            "nets": [{"name": "GND", "nodes": [{"ref": "R1", "pin": "1"}]}],
        },
        "view": {
            "grid_visible": True, "grid_step": 0.127, "snap_enabled": True,
            "crosshair": True, "zoom": 55.0, "center": [10.0, 20.0], "auto_fit": False,
            "show_ratsnest": False, "highlighted_nets": ["GND"],
            "net_highlight_enabled": True, "view_mode": "3d", "outline_draw_mode": "select",
            "active_uid": "fp1", "selected_footprints": ["fp1"],
            "selected_copper": ["t1", "v1"], "selected_outlines": [],
            "sections": {"footprints": False, "routing": True, "stackup": False},
        },
    }


def test_design_extension_and_root_are_addon_specific():
    text = dumps(sample_design())
    assert DESIGN_EXTENSION == ".e3d_pcb"
    assert text.startswith("(elec3d_pcb")
    assert "(version %d)" % FORMAT_VERSION in text


def test_design_roundtrip_preserves_layout_resources_and_offsets():
    restored = loads(dumps(sample_design()))
    assert restored["stack"]["active_id"] == 2
    assert restored["stack"]["stackup"]["separations"][0]["thickness"] == 1.2
    fp = restored["footprints"][0]
    assert (fp["x"], fp["y"], fp["rotation"]) == (12.5, -3.25, 90.0)
    assert fp["source"] == "${FOOTPRINT_DIR}/R.kicad_mod"
    assert fp["source_original"] == "/library/R.kicad_mod"
    assert fp["source_sha256"] == "abc123"
    assert fp["source_text"].startswith('(footprint "R_0603"')
    assert fp["model"]["path"] == "${MODEL_DIR}/R.step"
    assert fp["model"]["original_path"] == "${KICAD9_3DMODEL_DIR}/R.step"
    assert fp["model"]["sha256"] == "def456"
    assert fp["model_adjust_mode"] == "manual"
    assert fp["model_adjust"] == {"x": 0.3, "y": -0.2, "z": 1.7}
    assert restored["routing"]["copper"]["tracks"][0]["points"] == [[1.0, 2.0], [3.0, 4.0], [5.0, 4.0]]
    assert restored["routing"]["copper"]["vias"][0]["net"] == "GND"
    assert restored["routing"]["via_target_auto"] is True
    assert restored["netlist"]["nets"][0]["nodes"][0] == {"ref": "R1", "pin": "1"}
    assert restored["view"]["selected_copper"] == ["t1", "v1"]
    assert restored["view"]["sections"]["footprints"] is False


def test_path_variables_make_project_resources_portable(tmp_path):
    design = tmp_path / "project" / ("board" + DESIGN_EXTENSION)
    fp_dir = design.parent / "lib" / "footprints"
    model_dir = design.parent / "lib" / "models"
    fp_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    footprint = fp_dir / "C.kicad_mod"
    footprint.write_text("(footprint \"C\")", encoding="utf-8")

    variables = infer_path_variables(str(design), [str(footprint)], str(model_dir))
    assert variables["DESIGN_DIR"] == "."
    assert "FOOTPRINT_DIR" in variables
    assert "MODEL_DIR" in variables

    token = portable_path(str(footprint), variables, str(design), preferred="FOOTPRINT_DIR")
    assert token == "${FOOTPRINT_DIR}/C.kicad_mod"
    assert resolve_path(token, variables, str(design)) == str(footprint.resolve())
    assert resolve_path("${MODEL_DIR}/part.step", variables, str(design)) == str((model_dir / "part.step").resolve())


def test_external_environment_model_reference_is_preserved(tmp_path):
    design = tmp_path / ("board" + DESIGN_EXTENSION)
    raw = "${KICAD9_3DMODEL_DIR}/Package_SO/SOIC.step"
    assert portable_path(raw, {"DESIGN_DIR": "."}, str(design), preferred="MODEL_DIR") == raw


def test_atomic_file_io_roundtrips(tmp_path):
    path = tmp_path / ("board" + DESIGN_EXTENSION)
    write_file(str(path), sample_design())
    assert path.exists()
    restored = read_file(str(path))
    assert restored["addon_version"] == "0.10.0"
    assert restored["footprints"][0]["ref"] == "R1"
    assert not list(tmp_path.glob(".e3d-save-*.tmp"))


def test_newer_design_format_is_rejected_cleanly():
    text = dumps(sample_design()).replace("(version %d)" % FORMAT_VERSION,
                                          "(version %d)" % (FORMAT_VERSION + 1), 1)
    with pytest.raises(DesignFileError, match="newer"):
        loads(text)


def test_legacy_design_without_auto_flag_keeps_manual_via_target():
    text = dumps(sample_design()).replace("    (via_target_auto yes)\n", "")
    restored = loads(text)
    assert restored["routing"]["via_target_auto"] is False
    assert restored["routing"]["via_target_layer_id"] == 1
