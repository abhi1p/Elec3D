from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_panel_clips_live_route_at_clearance_barrier_instead_of_red_preview():
    src = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    view = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    routing = (ROOT / "elec3d" / "routing.py").read_text(encoding="utf-8")
    assert "def _route_collision_conflicts" in src
    assert "def _legal_route_preview" in src
    assert "clip_polyline_before_conflict(" in src
    assert "def clip_polyline_before_conflict" in routing
    assert "Clearance blocked by" in src
    assert "blocked=bool(conflicts)" not in src
    assert 'preview_color = "#E53935" if blocked else color' not in view
    assert 'ViaGraphicsItem(via, "#E53935" if blocked else color)' not in view
    assert "self._commit_preview_to_route(preview)" in src
