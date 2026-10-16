#!/usr/bin/env python3
"""Focused, dependency-free checks for displayctl's pure state logic."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).with_name("displayctl.py")
SPEC = importlib.util.spec_from_file_location("displayctl", MODULE)
displayctl = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(displayctl)


def config(name="eDP-1", *, enabled=True, x=0, y=0, mode="1920x1080@60.00Hz", scale=1.0, transform=0, mirror=None):
    return {"name": name, "enabled": enabled, "mode": mode, "x": x, "y": y, "scale": scale, "transform": transform, "mirror_of": mirror}


class DisplayStateTests(unittest.TestCase):
    def test_mode_and_logical_size(self):
        self.assertEqual(displayctl.parse_mode("1920x1080@59.99Hz"), (1920, 1080, 59.99))
        self.assertEqual(displayctl.hypr_mode("1920x1080@60.00Hz"), "1920x1080@60")
        self.assertEqual(displayctl.hypr_mode("1920x1080@59.99Hz"), "1920x1080@59.99")
        self.assertEqual(displayctl.logical_size(config(transform=1)), (1080, 1920))

    def test_scale_validation(self):
        self.assertIn(1.0, displayctl.valid_scales("1920x1080@60.00Hz"))
        self.assertIn("integral logical pixels", "\n".join(displayctl.validate_layout([config(scale=1.3)])))

    def test_overlap_and_mirror_validation(self):
        first, second = config("eDP-1"), config("HDMI-A-1")
        self.assertIn("overlaps", "\n".join(displayctl.validate_layout([first, second])))
        second["mirror_of"] = "eDP-1"
        self.assertEqual(displayctl.validate_layout([first, second]), [])
        first["mirror_of"] = "HDMI-A-1"
        self.assertTrue(displayctl.validate_layout([first, second]))

    def test_layout_match_includes_mirror_state(self):
        source = {
            "id": 0, "name": "eDP-1", "description": "Internal", "make": "ACME", "model": "Panel", "serial": "0",
            "physicalWidth": 300, "physicalHeight": 170, "disabled": False, "width": 1920, "height": 1080,
            "refreshRate": 60, "x": 0, "y": 0, "scale": 1, "transform": 0, "mirrorOf": "none", "availableModes": [],
        }
        monitor = {
            "id": 1, "name": "HDMI-A-1", "description": "External", "make": "ACME", "model": "Panel", "serial": "1",
            "physicalWidth": 600, "physicalHeight": 340, "disabled": False, "width": 1920, "height": 1080,
            "refreshRate": 60, "x": 0, "y": 0, "scale": 1, "transform": 0, "mirrorOf": "none", "availableModes": [],
        }
        saved = displayctl.normalize_monitor(monitor)
        saved["mirror_of"] = "eDP-1"
        self.assertFalse(displayctl.layout_matches([saved], [monitor]))
        monitor["mirrorOf"] = "0"
        # Mirrored output geometry belongs to its source and therefore is not
        # compared against the pre-mirror extended geometry.
        self.assertTrue(displayctl.layout_matches([saved], [source, monitor]))

    def test_requires_enabled_output(self):
        self.assertEqual(displayctl.validate_layout([config(enabled=False)]), ["At least one display must remain enabled."])

    def test_monitor_rules(self):
        self.assertEqual(displayctl.monitor_rule(config()), "eDP-1,1920x1080@60,0x0,1,transform,0")
        self.assertEqual(displayctl.monitor_rule(config(enabled=False)), "eDP-1,disable")

    def test_profile_matching_does_not_reapply_an_identical_layout(self):
        monitor = {
            "id": 0, "name": "eDP-1", "description": "Internal", "make": "ACME", "model": "Panel", "serial": "0",
            "physicalWidth": 300, "physicalHeight": 170, "disabled": False, "width": 1920, "height": 1080,
            "refreshRate": 60, "x": 0, "y": 0, "scale": 1, "transform": 0, "mirrorOf": "none", "availableModes": [],
        }
        self.assertTrue(displayctl.layout_matches(displayctl.configs_from_monitors([monitor]), [monitor]))

    def test_boot_scoped_idle_configuration(self):
        old_state = displayctl.state_root
        with tempfile.TemporaryDirectory() as temporary:
            displayctl.state_root = lambda: Path(temporary)
            displayctl.set_idle_override(300)
            self.assertEqual(displayctl.idle_timeouts(), (300, 330, 630))
            self.assertIn("timeout = 630", displayctl.generated_idle_config())
            displayctl.set_idle_override("never")
            self.assertIsNone(displayctl.idle_timeouts())
        displayctl.state_root = old_state


if __name__ == "__main__":
    unittest.main()
