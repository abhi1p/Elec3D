"""Pure-math regression checks for the KiCad/SGI virtual trackball helpers.

The production view3d module imports Qt, so these tests extract only the small
math helpers from its AST and execute them without a GUI binding.
"""

import ast
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW3D = ROOT / "elec3d" / "view3d.py"


def _load_helpers():
    tree = ast.parse(VIEW3D.read_text(encoding="utf-8"))
    wanted_functions = {
        "_vsub", "_dot", "_cross", "_length", "_norm",
        "_trackball_project_to_sphere", "_kicad_trackball_delta",
    }
    wanted_constants = {"_KICAD_TRACKBALL_SIZE"}
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_functions:
            body.append(node)
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in wanted_constants for n in names):
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"math": math}
    exec(compile(module, str(VIEW3D), "exec"), ns)
    return ns


def test_kicad_trackball_center_drag_right_rotates_about_negative_screen_y():
    ns = _load_helpers()
    axis, angle = ns["_kicad_trackball_delta"](0.0, 0.0, 0.2, 0.0)
    assert angle > 0.0
    assert abs(axis[0]) < 1e-12
    assert axis[1] < -0.999
    assert abs(axis[2]) < 1e-12


def test_kicad_trackball_center_drag_up_rotates_about_positive_screen_x():
    ns = _load_helpers()
    axis, angle = ns["_kicad_trackball_delta"](0.0, 0.0, 0.0, 0.2)
    assert angle > 0.0
    assert axis[0] > 0.999
    assert abs(axis[1]) < 1e-12
    assert abs(axis[2]) < 1e-12


def test_kicad_trackball_no_motion_is_identity():
    ns = _load_helpers()
    axis, angle = ns["_kicad_trackball_delta"](0.35, -0.2, 0.35, -0.2)
    assert angle == 0.0
    assert axis == (0.0, 0.0, 1.0)


def test_trackball_project_is_continuous_at_sphere_hyperbola_join():
    ns = _load_helpers()
    r = ns["_KICAD_TRACKBALL_SIZE"]
    x = r / math.sqrt(2.0)
    left = ns["_trackball_project_to_sphere"](r, x * (1.0 - 1e-9), 0.0)
    right = ns["_trackball_project_to_sphere"](r, x * (1.0 + 1e-9), 0.0)
    assert abs(left - right) < 1e-7
