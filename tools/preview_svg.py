# -*- coding: utf-8 -*-
"""Quick SVG preview of a footprint, without starting FreeCAD.

    python3 tools/preview_svg.py tests/data/NE555DR.kicad_mod out.svg

Handy while working on the parser: it draws in KiCad coordinates (Y down), so
the picture should look exactly like the KiCad footprint editor.  The FreeCAD
importer flips Y; this tool deliberately does not, so the two views together
make coordinate mistakes obvious.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d.footprint import parse_footprint_file          # noqa: E402
from elec3d.layers import LAYER_COLORS_DARK, LAYER_ORDER   # noqa: E402

SCALE = 40.0          # px per mm
MARGIN = 1.0          # mm


def _color(layer):
    return LAYER_COLORS_DARK.get(layer, LAYER_COLORS_DARK["unknown"])


def _z(layer):
    try:
        return LAYER_ORDER.index(layer)
    except ValueError:
        return 0


def _pad_polygon(pad):
    """Pad corners in footprint coordinates (rectangular family only)."""
    dx, dy = pad.w / 2.0, pad.h / 2.0
    if pad.shape == "trapezoid":
        ddx, ddy = pad.delta[0] / 2.0, pad.delta[1] / 2.0
        pts = [(-dx - ddy, dy + ddx), (-dx + ddy, -dy - ddx),
               (dx - ddy, -dy + ddx), (dx + ddy, dy - ddx)]
    else:
        pts = [(-dx, -dy), (dx, -dy), (dx, dy), (-dx, dy)]
    a = math.radians(pad.rot)
    out = []
    for x, y in pts:
        # KiCad's RotatePoint, i.e. counter-clockwise on screen
        out.append((pad.x + x * math.cos(a) + y * math.sin(a),
                    pad.y - x * math.sin(a) + y * math.cos(a)))
    return out


def render(fp):
    minx, miny, maxx, maxy = fp.bbox()
    minx -= MARGIN
    miny -= MARGIN
    maxx += MARGIN
    maxy += MARGIN
    w, h = (maxx - minx) * SCALE, (maxy - miny) * SCALE

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
           'viewBox="%.4f %.4f %.4f %.4f">' % (w, h, minx, miny, maxx - minx, maxy - miny),
           '<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="#1b1b1b"/>'
           % (minx, miny, maxx - minx, maxy - miny)]

    items = []
    for l in fp.lines:
        items.append((_z(l.layer),
                      '<line x1="%.4f" y1="%.4f" x2="%.4f" y2="%.4f" stroke="%s" '
                      'stroke-width="%.4f" stroke-linecap="round"/>'
                      % (l.x1, l.y1, l.x2, l.y2, _color(l.layer), max(l.width, 0.02))))
    for r in fp.rects:
        items.append((_z(r.layer),
                      '<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="%s" '
                      'stroke="%s" stroke-width="%.4f"/>'
                      % (min(r.x1, r.x2), min(r.y1, r.y2), abs(r.x2 - r.x1),
                         abs(r.y2 - r.y1), _color(r.layer) if r.filled else "none",
                         _color(r.layer), max(r.width, 0.02))))
    for c in fp.circles:
        items.append((_z(c.layer),
                      '<circle cx="%.4f" cy="%.4f" r="%.4f" fill="%s" stroke="%s" '
                      'stroke-width="%.4f"/>'
                      % (c.cx, c.cy, c.r, _color(c.layer) if c.filled else "none",
                         _color(c.layer), max(c.width, 0.02))))
    for a in fp.arcs:
        r = _arc_radius(a)
        cross = ((a.mid[0] - a.start[0]) * (a.end[1] - a.start[1]) -
                 (a.mid[1] - a.start[1]) * (a.end[0] - a.start[0]))
        items.append((_z(a.layer),
                      '<path d="M %.4f %.4f A %.4f %.4f 0 0 %d %.4f %.4f" fill="none" '
                      'stroke="%s" stroke-width="%.4f"/>'
                      % (a.start[0], a.start[1], r, r, 1 if cross > 0 else 0,
                         a.end[0], a.end[1], _color(a.layer), max(a.width, 0.02))))
    for p in fp.polys:
        pts = " ".join("%.4f,%.4f" % pt for pt in p.points)
        items.append((_z(p.layer),
                      '<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.4f"/>'
                      % (pts, _color(p.layer) if p.filled else "none",
                         _color(p.layer), max(p.width, 0.02))))
    for pad in fp.pads:
        col = _color(pad.copper_layer)
        z = _z(pad.copper_layer)
        if pad.shape == "circle" or (pad.shape == "oval" and abs(pad.w - pad.h) < 1e-9):
            items.append((z, '<circle cx="%.4f" cy="%.4f" r="%.4f" fill="%s"/>'
                          % (pad.x, pad.y, pad.w / 2.0, col)))
        else:
            pts = " ".join("%.4f,%.4f" % pt for pt in _pad_polygon(pad))
            rx = min(pad.w, pad.h) / 2.0 if pad.shape == "oval" else 0.0
            items.append((z, '<polygon points="%s" fill="%s" %s/>'
                          % (pts, col, 'rx="%.4f"' % rx if rx else "")))
        if pad.drill:
            items.append((z + 1, '<circle cx="%.4f" cy="%.4f" r="%.4f" fill="#1b1b1b"/>'
                          % (pad.x + pad.drill.offx, pad.y + pad.drill.offy,
                             pad.drill.w / 2.0)))
        items.append((99, '<text x="%.4f" y="%.4f" font-size="%.3f" fill="#ffffff" '
                      'text-anchor="middle" dominant-baseline="central">%s</text>'
                      % (pad.x, pad.y, min(pad.w, pad.h) * 0.6, pad.number)))

    for _, svg in sorted(items, key=lambda it: it[0]):
        out.append(svg)
    out.append("</svg>")
    return "\n".join(out)


def _arc_radius(arc):
    (ax, ay), (bx, by), (cx, cy) = arc.start, arc.mid, arc.end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return 0.0
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    return math.hypot(ax - ux, ay - uy)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    fp = parse_footprint_file(argv[1])
    svg = render(fp)
    target = argv[2] if len(argv) > 2 else os.path.splitext(argv[1])[0] + ".svg"
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(fp.summary())
    print("wrote", target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
