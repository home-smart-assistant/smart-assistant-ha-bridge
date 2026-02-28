from __future__ import annotations

import unittest

from app.services.ha_service import _is_area_audit_ignored_entity


class TestAreaAuditIgnore(unittest.TestCase):
    def test_ignore_zigbee2mqtt_bridge_switch(self) -> None:
        self.assertTrue(_is_area_audit_ignored_entity("switch.zigbee2mqtt_bridge_permit_join"))

    def test_keep_regular_switch(self) -> None:
        self.assertFalse(_is_area_audit_ignored_entity("switch.ke_ting_deng"))


if __name__ == "__main__":
    unittest.main()
