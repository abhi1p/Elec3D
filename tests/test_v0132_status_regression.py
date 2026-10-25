# -*- coding: utf-8 -*-
"""Regression guards for v0.13.2 status notification helper."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")


def test_flash_status_helper_exists_and_delegates_to_notify():
    assert "def _flash_status(self, text):" in PANEL
    start = PANEL.index("def _flash_status(self, text):")
    body = PANEL[start:start + 700]
    assert "self._notify(text)" in body


def test_stackup_and_design_rules_can_use_flash_status():
    assert 'self._flash_status("Stackup updated")' in PANEL
    assert 'self._flash_status("Design rules updated")' in PANEL
