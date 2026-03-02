from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.models.schemas import ToolCatalogItem
from app.services.ha_service import resolve_area_entity, resolve_service_data


class TestAreaResolutionGuardrail(unittest.IsolatedAsyncioTestCase):
    async def test_cover_area_balcony_without_exact_ha_match_returns_400(self) -> None:
        async def fake_get_ha_areas(*, include_state_validation: bool = True):  # noqa: ARG001
            return {
                "success": True,
                "areas": [
                    {
                        "area_id": "yang_tai",
                        "area_name": "阳台",
                        "ha_entities": ["cover.yang_tai_chuang_lian", "cover.yang_tai_sha_lian"],
                    }
                ],
            }

        with patch("app.services.ha_service.get_ha_areas", new=fake_get_ha_areas):
            with self.assertRaises(HTTPException) as ex:
                await resolve_area_entity("cover", {"area": "balcony"})

        self.assertEqual(400, ex.exception.status_code)
        self.assertIn("cover entity is not configured for area: balcony", str(ex.exception.detail))

    async def test_cover_area_not_found_returns_400_instead_of_living_room_fallback(self) -> None:
        async def fake_get_ha_areas(*, include_state_validation: bool = True):  # noqa: ARG001
            return {
                "success": True,
                "areas": [
                    {
                        "area_id": "living_room",
                        "area_name": "客厅",
                        "ha_entities": ["cover.living_room"],
                    }
                ],
            }

        merged_args = {
            "area": "balcony",
            "area_entity_map": {
                "living_room": "cover.living_room",
            },
        }
        with patch("app.services.ha_service.get_ha_areas", new=fake_get_ha_areas):
            with self.assertRaises(HTTPException) as ex:
                await resolve_area_entity("cover", merged_args)

        self.assertEqual(400, ex.exception.status_code)
        self.assertIn("cover entity is not configured for area: balcony", str(ex.exception.detail))

    async def test_explicit_entity_id_still_supported(self) -> None:
        merged_args = {
            "area": "balcony",
            "entity_id": "cover.yang_tai_sha_lian",
            "area_entity_map": {
                "living_room": "cover.living_room",
            },
        }
        resolved = await resolve_area_entity("cover", merged_args)
        self.assertEqual("cover.yang_tai_sha_lian", resolved)

    async def test_area_is_required_without_entity_id(self) -> None:
        with self.assertRaises(HTTPException) as ex:
            await resolve_area_entity("cover", {})
        self.assertEqual(400, ex.exception.status_code)
        self.assertIn("area is required for cover strategy", str(ex.exception.detail))

    async def test_service_data_ignores_catalog_default_area_fallback(self) -> None:
        item = ToolCatalogItem(
            tool_name="home.lights.on",
            domain="auto",
            service="turn_on",
            strategy="light_area",
            enabled=True,
            default_arguments={
                "area": "living_room",
                "area_entity_map": {"living_room": "switch.living_room_light"},
            },
        )
        with self.assertRaises(HTTPException) as ex:
            await resolve_service_data(item, {})
        self.assertEqual(400, ex.exception.status_code)
        self.assertIn("area is required for light strategy", str(ex.exception.detail))


if __name__ == "__main__":
    unittest.main()
