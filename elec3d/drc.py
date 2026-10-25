# -*- coding: utf-8 -*-
"""Design-rule values used by the interactive PCB editor.

The rules are intentionally plain Python data so they can be saved in .e3d_pcb,
carried through Tab <-> Window rebuilds, and tested without Qt/FreeCAD.
All clearances are edge-to-edge distances in millimetres.
"""

__all__ = ["DesignRules", "DEFAULT_RULES", "RULE_LABELS"]

DEFAULT_RULES = {
    "track_track": 0.20,
    "track_via": 0.20,
    "track_pad": 0.20,
    "via_via": 0.20,
    "via_pad": 0.20,
    "pad_pad": 0.20,
    "courtyard_courtyard": 0.25,
    "outline_track": 0.25,
    "outline_via": 0.25,
    "outline_pad": 0.25,
    "outline_courtyard": 0.25,
}

RULE_LABELS = (
    ("track_track", "track to track"),
    ("track_via", "track to via"),
    ("track_pad", "track to pad"),
    ("via_via", "via to via"),
    ("via_pad", "via to pad"),
    ("pad_pad", "pad to pad"),
    ("courtyard_courtyard", "courtyard to courtyard"),
    ("outline_track", "outline to track"),
    ("outline_via", "outline to via"),
    ("outline_pad", "outline to pad"),
    ("outline_courtyard", "outline to courtyard"),
)


class DesignRules(object):
    def __init__(self, values=None):
        self.values = dict(DEFAULT_RULES)
        self.restore(values)

    @staticmethod
    def _value(value, default):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return float(default)

    def get(self, key, default=None):
        if default is None:
            default = DEFAULT_RULES.get(key, 0.0)
        return self._value(self.values.get(key, default), default)

    def set(self, key, value):
        if key not in DEFAULT_RULES:
            return False
        value = self._value(value, DEFAULT_RULES[key])
        if abs(self.get(key) - value) < 1e-12:
            return False
        self.values[key] = value
        return True

    def snapshot(self):
        return dict((key, self.get(key)) for key in DEFAULT_RULES)

    def restore(self, data):
        if isinstance(data, DesignRules):
            data = data.snapshot()
        if not isinstance(data, dict):
            return
        for key, default in DEFAULT_RULES.items():
            if key in data:
                self.values[key] = self._value(data.get(key), default)
