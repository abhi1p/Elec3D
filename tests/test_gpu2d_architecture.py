# -*- coding: utf-8 -*-
"""Source-level checks for the optional GPU-backed 2D canvas.

These run even in CI environments that do not have FreeCAD/PySide installed;
the real QWidget/OpenGL behaviour is covered by the GUI suite when run inside
FreeCAD.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "elec3d"


def source(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_2d_canvas_uses_direct_qt_opengl_viewport_without_pivy():
    text = source("view2d.py")
    assert "QtOpenGLWidgets" in text
    assert "QOpenGLWidget" in text
    assert "setViewport(viewport)" in text
    assert "import pivy" not in text.lower()
    assert "from pivy" not in text.lower()


def test_2d_gpu_keeps_qgraphics_scene_as_interaction_source_of_truth():
    text = source("view2d.py")
    assert "QGraphicsView" in text
    assert "QGraphicsScene" in text
    assert "RubberBandDrag" in text
    assert "GPU 2D (Qt OpenGL)" in text


def test_2d_gpu_has_runtime_software_fallback_and_diagnostic_override():
    text = source("view2d.py")
    assert "ELEC3D_DISABLE_GPU_2D" in text
    assert "_validate_gpu_viewport" in text
    assert 'self.renderer_name = "software fallback"' in text
    assert "QtCore.QTimer.singleShot(0, self._validate_gpu_viewport)" in text


def test_2d_renderer_is_visible_in_status_bar():
    text = source("panel2d.py")
    assert "status_renderer" in text
    assert "self.canvas.renderer_name" in text
    assert "rendererChanged.connect(self._on_2d_renderer_changed)" in text
