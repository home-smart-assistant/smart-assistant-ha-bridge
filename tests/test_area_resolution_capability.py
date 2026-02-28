from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.ha_service import (
    _build_area_suggestion_tokens,
    _filter_entities_by_type,
    _iter_area_lookup_candidates,
    _suggest_area_for_entity,
    resolve_area_entity,
)


class TestAreaResolutionCapability(unittest.IsolatedAsyncioTestCase):
    def test_lookup_candidates_include_dining_aliases(self) -> None:
        candidates = _iter_area_lookup_candidates("餐厅")
        self.assertIn("dining_room", candidates)
        self.assertIn("can_ting", candidates)

    def test_suggestion_prefers_dining_for_dining_light(self) -> None:
        suggested, token = _suggest_area_for_entity(
            entity_id="switch.ke_ting_can_ting_deng_l2",
            friendly_name="餐厅灯",
            target_areas=["客厅", "餐厅"],
        )
        self.assertEqual("餐厅", suggested)
        self.assertIn(token, {"canting", "餐厅"})

    async def test_resolve_area_entity_prefers_runtime_ha_area(self) -> None:
        async def fake_get_ha_areas(*, include_state_validation: bool = True):  # noqa: ARG001
            return {
                "success": True,
                "areas": [
                    {
                        "area_id": "dining_room",
                        "area_name": "餐厅",
                        "ha_entities": ["switch.dining_light"],
                    }
                ],
            }

        merged_args = {
            "area": "dining_room",
            "area_entity_map": {
                "living_room": ["switch.living_light_1", "switch.living_light_2"],
            },
        }
        with patch("app.services.ha_service.get_ha_areas", new=fake_get_ha_areas):
            resolved = await resolve_area_entity("light", merged_args)
        self.assertEqual("switch.dining_light", resolved)

    def test_suggestion_tokens_include_pinyin_alias(self) -> None:
        tokens = _build_area_suggestion_tokens("餐厅")
        self.assertIn("canting", tokens)

    def test_filter_light_entities_ignores_non_light_switches(self) -> None:
        entities = [
            "switch.ke_ting_can_ting_deng_l1",
            "switch.xiaomi_cn_931449784_h39h00_eco_p_2_7",
            "light.xiaomi_cn_931449784_h39h00_s_6_indicator_light",
            "light.ke_ting_zhao_ming",
        ]
        filtered = _filter_entities_by_type(entities, entity_type="light")
        self.assertIn("switch.ke_ting_can_ting_deng_l1", filtered)
        self.assertIn("light.ke_ting_zhao_ming", filtered)
        self.assertNotIn("light.xiaomi_cn_931449784_h39h00_s_6_indicator_light", filtered)
        self.assertNotIn("switch.xiaomi_cn_931449784_h39h00_eco_p_2_7", filtered)


if __name__ == "__main__":
    unittest.main()
