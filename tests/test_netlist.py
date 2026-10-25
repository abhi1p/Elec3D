from elec3d.netlist import (KiCadNetlist, parse_netlist, pins_by_ref,
                             placeholder_footprint, ratsnest_mst_pairs)


SAMPLE = r'''
(export (version D)
  (components
    (comp (ref R1) (value 10k) (footprint Resistor_SMD:R_0603_1608Metric))
    (comp (ref U1) (value NE555D) (footprint Package_SO:SOIC-8_3.9x4.9mm_P1.27mm)))
  (nets
    (net (code 1) (name GND)
      (node (ref R1) (pin 1))
      (node (ref U1) (pin 1)))
    (net (code 2) (name VCC)
      (node (ref U1) (pin 8)))))
'''


def test_parse_netlist_components_and_connectivity():
    nl = parse_netlist(SAMPLE, "demo.net")
    assert nl.source_file == "demo.net"
    assert [(c.ref, c.value, c.footprint) for c in nl.components] == [
        ("R1", "10k", "Resistor_SMD:R_0603_1608Metric"),
        ("U1", "NE555D", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    ]
    assert nl.nets[0].name == "GND"
    assert nl.nets[0].nodes == [{"ref": "R1", "pin": "1"},
                                 {"ref": "U1", "pin": "1"}]
    assert nl.footprint_names() == [
        "Resistor_SMD:R_0603_1608Metric",
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    ]


def test_netlist_snapshot_round_trip():
    before = parse_netlist(SAMPLE, "demo.net")
    after = KiCadNetlist.restore(before.snapshot())
    assert after.snapshot() == before.snapshot()


def test_pins_by_ref_are_unique_and_sorted():
    nl = parse_netlist(SAMPLE)
    assert pins_by_ref(nl) == {"R1": ["1"], "U1": ["1", "8"]}


def test_placeholder_keeps_real_pin_numbers_for_ratsnest():
    fp = placeholder_footprint("Package_SO:SOIC-8", ["1", "2", "7", "8"])
    assert fp.name == "SOIC-8"
    assert fp.attr == "placeholder"
    assert [p.number for p in fp.pads] == ["1", "2", "7", "8"]
    assert "Dwgs.User" in fp.layers_used()


def test_wrong_root_is_rejected():
    try:
        parse_netlist("(footprint X)")
    except ValueError as exc:
        assert "export" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_ratsnest_mst_uses_shortest_current_geometry_not_first_node_star():
    # 0--1--2 is the shortest tree.  A first-node star would incorrectly add
    # the long 0--2 diagonal/segment instead of 1--2.
    pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    assert ratsnest_mst_pairs(pts) == [(0, 1), (1, 2)]


def test_ratsnest_mst_collapses_coincident_pad_positions():
    pts = [(0.0, 0.0), (0.0, 0.0), (3.0, 4.0)]
    assert ratsnest_mst_pairs(pts) == [(0, 2)]


def test_netlist_exposes_exact_pad_to_net_name_mapping():
    nl = parse_netlist(SAMPLE)
    assert nl.pad_net_map() == {
        ("R1", "1"): "GND",
        ("U1", "1"): "GND",
        ("U1", "8"): "VCC",
    }
    assert nl.net_name("U1", "8") == "VCC"
    assert nl.net_name("U1", "7") is None
