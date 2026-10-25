from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_panel_routes_treat_board_solid_section_as_same_hard_barrier_pipeline():
    src = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    routing = (ROOT / "elec3d" / "routing.py").read_text(encoding="utf-8")
    assert "route_outline_conflicts(candidate, outlines, rules=self.design_rules)" in src
    assert "via_layer_outline_conflicts(via, outline_sets, rules=self.design_rules)" in src
    assert "Routing blocked by the board edge" in src
    # Each layer's boundary is the board solid's cross-section at its plane.
    lookup = src.split("def _board_outline_for_layer", 1)[1].split("\n    def ", 1)[0]
    assert "solid.board_section(z)" in lookup
    assert "BoardOutline.from_loops(" in lookup
    assert "blocking=True" in lookup
    assert "def route_outline_conflicts" in routing
    assert "def via_outline_conflicts" in routing
    assert "def via_layer_outline_conflicts" in routing
    # Outline protection must also work for free-space/unknown-net routes.
    collision_body = src.split("def _route_collision_conflicts", 1)[1].split("@staticmethod", 1)[0]
    assert 'if r is None or not r.get("net")' not in collision_body
