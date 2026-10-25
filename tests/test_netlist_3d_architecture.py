from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / "elec3d" / name).read_text(encoding="utf-8")


def test_netlist_import_has_four_step_native_wizard():
    text = source("netlist_ui.py")
    assert "Step %d of 4" in text
    assert "1. Choose a netlist" in text
    assert "2. Load footprints" in text
    assert "3. Load 3D models" in text
    assert "4. Ready" in text


def test_viewer_switches_2d_3d_without_freecad_document_creation():
    panel = source("panel2d.py")
    view3d = source("view3d.py")
    assert 'key == Qt.Key_2' in panel
    assert 'key == Qt.Key_3' in panel
    assert 'QStackedWidget' in panel
    assert 'Board3DView' in panel
    assert "App.newDocument" not in panel
    assert "App.newDocument" not in view3d


def test_3d_preview_has_direct_gpu_path_and_software_fallback():
    text = source("view3d.py")
    assert "QtOpenGLWidgets" in text
    assert "_Gpu3DCanvas" in text
    assert "_Software3DCanvas" in text
    assert "QOpenGLShaderProgram" in text
    assert "QPainter" in text
    assert "Part.read" in text
    assert "App.newDocument" not in text
    assert "pivy.quarter" not in text
    assert "QuarterWidget" not in text


def test_command_is_registered_for_netlist_import():
    text = source("commands.py")
    assert "Elec3D_ImportNetlist" in text
    assert "Import KiCad netlist" in text


def test_gpu_3d_avoids_pivy_quarter_native_crash_path():
    text = source("view3d.py")
    assert "QOpenGLWidget" in text
    assert "QtOpenGLWidgets" in text
    assert "pivy.qt" not in text
    assert "setSceneGraph" not in text
    assert "QOpenGLBuffer" in text
    assert "glDrawArrays" in text


def test_software_3d_converts_tuple_colours_without_qvariant_qcolor_overload():
    text = source("view3d.py")
    assert 'isinstance(color, (tuple, list))' in text
    assert 'QtGui.QColor(color)' not in text
    assert 'QtGui.QPen(QtGui.QColor(color)' not in text
    assert 'pen.setColor(cls._qcolor(color))' in text


def test_software_3d_always_ends_qpainter_even_when_painting_fails():
    text = source("view3d.py")
    assert 'finally:' in text
    assert 'if painter.isActive():' in text
    assert 'painter.end()' in text


def test_3d_selection_and_reference_style_model_alignment_are_wired():
    panel = source("panel2d.py")
    view3d = source("view3d.py")
    assert "footprintSelected" in view3d
    assert "pick_uid" in view3d
    assert "measure_model" in view3d
    assert "Section(\"3D model\")" in panel
    assert "model_adjust_mode" in panel
    assert "_auto_align_models" in panel
    assert "Align Z" in panel
    assert "Center XY" in panel


def test_create_freecad_action_is_held_from_viewer_ui():
    panel = source("panel2d.py")
    commands = source("commands.py")
    assert 'addAction("Create FreeCAD document")' not in panel
    assert 'FreeCAD conversion settings' not in commands


def test_manual_model_offset_controls_are_sidebar_width_safe():
    panel = source("panel2d.py")
    assert "self.sidebar_scroll = scroll" in panel
    assert "inner.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)" in panel
    assert 'ControlRow(axis.upper(), spin, "mm")' in panel
    assert "QGridLayout(ma_buttons)" in panel
    assert "ma_btn_layout.addWidget(self.btn_adjust_reset, 1, 0, 1, 2)" in panel


def test_3d_orbit_is_full_trackball_without_pitch_clamp():
    text = source("view3d.py")
    assert "_rotate_about_axis" in text
    assert "camera_up" in text
    assert "pitch = max(math.radians(-89.0)" not in text
    assert "full 360-degree orbit" in text


def test_3d_view_presets_keep_explicit_screen_up_and_axis_triad():
    text = source("view3d.py")
    assert '"top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0))' in text
    assert "class _AxisTriad" in text
    assert "axis_triad" in text
    assert "WA_TransparentForMouseEvents" in text
    assert '"X", (1.0, 0.0, 0.0)' in text
    assert '"Y", (0.0, 1.0, 0.0)' in text
    assert '"Z", (0.0, 0.0, 1.0)' in text


def test_3d_board_body_is_the_imported_solid_with_per_layer_sections():
    text = source("view3d.py")
    assert "def rebuild(self, entries, stack, solid=None" in text
    assert "world = solid.world_vertices()" in text
    assert "for i, j in solid.feature_edges():" in text
    assert "layer_loops[layer.id] = solid.section(zmap.get(layer.id, 0.0))" in text
    assert "for loop in layer_loops.get(layer.id, []):" in text
    # Pre-0.17 outline lofting is no longer part of the 3D view.
    assert "_outline_loops_by_layer" not in text


def test_3d_left_drag_uses_kicad_sgi_virtual_trackball_not_yaw_pitch():
    text = source("view3d.py")
    assert "_KICAD_TRACKBALL_SIZE = 0.8" in text
    assert "_trackball_project_to_sphere" in text
    assert "_kicad_trackball_delta" in text
    assert "_normalise_trackball_point" in text
    assert "vcross(a, p2, p1)" in text
    assert "Match KiCad's TRACK_BALL::Drag()" in text
    assert "-dx * 0.008" not in text
    assert "-dy * 0.008" not in text


def test_3d_copper_uses_one_physical_material_not_layer_colours():
    text = source("view3d.py")
    assert 'COPPER_METAL = "#D4AF37"' in text
    assert 'PAD_GOLD = COPPER_METAL' in text
    assert 'color = COPPER_METAL' in text
    # Routed tracks/vias must not inherit F.Cu/B.Cu/internal editor colours.
    routed = text[text.index("def _add_routed_copper"):text.index("def rebuild", text.index("def _add_routed_copper"))]
    assert "color = layer.color" not in routed
    assert "stack.by_id(via.start_layer_id).color" not in routed
