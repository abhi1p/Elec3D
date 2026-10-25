# -*- coding: utf-8 -*-
"""Source-level guards for the document-free footprint edit commands."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestEditingArchitecture(unittest.TestCase):
    def test_requested_keyboard_actions_are_present(self):
        text = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn('key == Qt.Key_Z', text)
        self.assertIn('key == Qt.Key_Y', text)
        self.assertIn('key == Qt.Key_R', text)
        self.assertIn('key == Qt.Key_Delete', text)
        self.assertIn('def rotate_selected', text)
        self.assertIn('def delete_selected_footprints', text)
        self.assertIn('def undo', text)
        self.assertIn('def redo', text)

    def test_move_gesture_is_grouped_for_undo(self):
        text = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        self.assertIn('footprintsMoved = Signal(object)', text)
        self.assertIn('self.footprintsMoved.emit(moved)', text)

    def test_rotate_while_mouse_is_held_preserves_undo_start_and_rebases_live_state(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        self.assertIn('in_mouse_gesture = self.canvas.rebase_drag_transform(items)', panel)
        self.assertIn('if not in_mouse_gesture:', panel)
        self.assertIn('self._drag_start_rotation = {}', canvas)
        self.assertIn('self._drag_rebase_positions = {}', canvas)
        self.assertIn('def _continue_rebased_drag(self, view_pos):', canvas)
        self.assertIn('rebased[item] = QtCore.QPointF(item.pos())', canvas)
        self.assertIn('self._drag_rotation[item] = float(item.rotation())', canvas)
        # The immutable mouse-down position must never be overwritten by R.
        self.assertNotIn('self._drag_origin[item] = QtCore.QPointF(item.pos())', canvas)

    def test_compound_mouse_transform_carries_rotation_in_undo_payload(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        self.assertIn('old_rotation = float(self._drag_start_rotation.get(item, item.rotation()))', canvas)
        self.assertIn('rotation_changed = abs(new_rotation - old_rotation) > 1e-9', canvas)
        self.assertIn('moved.append((item, QtCore.QPointF(origin), old_rotation,', canvas)
        self.assertIn('item, old_pos, old_rotation, new_pos, new_rotation = change[:5]', panel)
        self.assertIn('rotation=old_rotation', panel)
        self.assertIn('rotation=new_rotation', panel)

    def test_board_edges_never_steal_hits_and_tracks_use_visible_geometry(self):
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        edge = canvas.split("class BoardEdgeItem", 1)[1].split("class BoardMaskItem", 1)[0]
        mask = canvas.split("class BoardMaskItem", 1)[1].split(
            "# ---------------------------------------------------------------------------\n# routed copper scene items", 1)[0]
        track = canvas.split("class TrackGraphicsItem", 1)[1].split(
            "class ViaGraphicsItem", 1)[0]
        # Derived board edges are reference geometry: empty hit shapes.
        for body in (edge, mask):
            self.assertIn("def shape(self):", body)
            self.assertIn("return QtGui.QPainterPath()", body)
            self.assertIn("setAcceptedMouseButtons(Qt.NoButton)", body)
        self.assertIn("ItemIsSelectable, False", edge)
        self.assertIn("def shape(self):", track)
        self.assertIn("QPainterPathStroker", track)
        self.assertIn("float(self.track_model.width)", track)
        self.assertIn('data.get("kind") in ("board_edge", "board_mask")', canvas)

    def test_tracks_and_vias_use_explicit_m_move_without_bbox_frames(self):
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        track = canvas.split("class TrackGraphicsItem", 1)[1].split("class ViaGraphicsItem", 1)[0]
        via = canvas.split("class ViaGraphicsItem", 1)[1].split("# ---------------------------------------------------------------------------\n# scene items", 1)[0]
        # Copper is selectable but direct mouse dragging is disabled.
        self.assertNotIn("ItemIsMovable", track)
        self.assertNotIn("ItemIsMovable", via)
        self.assertIn("ItemIsSelectable", track)
        self.assertIn("ItemIsSelectable", via)
        # Selection painting remains geometry-only, without Qt bbox frames.
        self.assertIn("def paint(self, painter, option, widget=None):", track)
        self.assertIn("painter.drawPath(self.path())", track)
        self.assertIn("def paint(self, painter, option, widget=None):", via)
        self.assertIn("painter.drawPath(self.path())", via)
        # M owns the interactive copper move and one transform history command.
        self.assertIn("key == Qt.Key_M", panel)
        self.assertIn("return self.begin_move_selected", panel)
        self.assertIn("def begin_move_selected", panel)
        self.assertIn("def _on_move_pointer", panel)
        self.assertIn("def _on_move_click", panel)
        self.assertIn('"copper_before": before_copper', panel)
        self.assertIn('"copper_after": after_copper', panel)

    def test_mixed_move_undo_restores_footprints_tracks_and_vias_together(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn('"copper_before": copper_before', panel)
        self.assertIn('"copper_after": copper_after', panel)
        self.assertIn('"copper_selection": self._selected_copper_uids()', panel)
        self.assertIn('self._apply_copper_transform_states(command.get("copper_before", []))', panel)
        self.assertIn('self._apply_copper_transform_states(command.get("copper_after", []))', panel)
        self.assertIn("def _restore_editor_selection", panel)
        self.assertIn("self.canvas.select_editor_items(footprints, copper)", panel)

    def test_host_snapshot_preserves_rotation_and_history(self):
        text = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        self.assertIn('"rotation": float(item.rotation())', text)
        self.assertIn('"history": viewer.history_snapshot()', text)
        self.assertIn('viewer.restore_history(state.get("history"))', text)

    def test_outline_drawing_is_gone_and_board_solid_editor_is_present(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        self.assertIn('ControlRow("rotation", self.prop_rotation)', panel)
        self.assertIn('def _build_copper_tool_panel', panel)
        self.assertIn('ToolRailButton(mode, panel)', panel)
        for gone in ('Draw outline rectangle', 'Draw outline circle', 'Draw outline ellipse',
                     'Draw outline line', 'def _commit_outline_property',
                     'outline_props_group', 'def _set_outline_mode'):
            self.assertNotIn(gone, panel)
        for gone in ('def set_outline_draw_mode', 'class OutlineGraphicsItem',
                     'class OutlineHandleItem', 'outlinesMoved', 'def _commit_outline_drag'):
            self.assertNotIn(gone, canvas)
        self.assertIn('def _build_board_section', panel)
        self.assertIn('def import_board_step_dialog', panel)
        self.assertIn('def import_board_freecad_dialog', panel)
        self.assertIn('def set_board_sections', canvas)

    def test_tool_rail_escape_and_delete_key_architecture(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        design = (ROOT / "elec3d" / "design.py").read_text(encoding="utf-8")
        self.assertIn('TOOL_RAIL_WIDTH = 46', panel)
        self.assertIn('class ToolRailButton', panel)
        self.assertNotIn('Delete selected")', panel)
        self.assertIn('key == Qt.Key_Escape', panel)
        self.assertIn('return list(reversed(self.layers))', design)
        self.assertNotIn('self.outline = DesignLayer', design)

    def test_board_placement_edits_are_single_undo_steps(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        body = panel.split("def _on_board_placement_edit", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._board_edit_before = self._board_state()", body)
        self.assertIn('"kind": "board_solid"', body)
        self.assertIn('self._apply_board_state(command.get("before", {}))', panel)
        self.assertIn('self._apply_board_state(command.get("after", {}))', panel)

    def test_file_shortcuts_are_intercepted_before_freecad_and_history_repeats(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn('key == Qt.Key_O', panel)
        self.assertIn('return self.open_design_dialog', panel)
        self.assertIn('key == Qt.Key_S', panel)
        self.assertIn('return self.save_design_as if shift else self.save_design', panel)
        self.assertIn('def _edit_key_allows_repeat', panel)
        self.assertIn('key in (Qt.Key_Z, Qt.Key_Y)', panel)
        self.assertIn('if not auto_repeat or self._edit_key_allows_repeat(event):', panel)

    def test_interrupted_drag_rolls_back_instead_of_poisoning_undo(self):
        canvas = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        self.assertIn('self._left_drag_active = False', canvas)
        self.assertIn('def has_active_transform_gesture(self):', canvas)
        self.assertIn('if not self.has_active_transform_gesture():', canvas)
        self.assertIn('def _restore_uncommitted_transform(self):', canvas)
        self.assertIn('item.setPos(QtCore.QPointF(origin))', canvas)
        self.assertIn('item.setRotation(float(self._drag_start_rotation[item]))', canvas)
        self.assertIn('def cancel_interaction(self, restore=True):', canvas)
        self.assertIn('canvas.cancel_interaction(restore=True)', viewer)

    def test_live_ratsnest_refresh_is_read_only_until_transform_commits(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn('def _refresh_ratsnest(self, sync_copper_nets=True):', panel)
        self.assertIn('self._refresh_ratsnest(sync_copper_nets=False)', panel)
        self.assertIn('self._refresh_ratsnest(sync_copper_nets=not in_mouse_gesture)', panel)
        self.assertIn('if self.netlist is not None and sync_copper_nets:', panel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
