from __future__ import annotations

import unittest

from app.services.ha_service import _string_or_empty


class TestAreaAssignSafety(unittest.TestCase):
    def test_string_or_empty_does_not_convert_none_to_literal(self) -> None:
        self.assertEqual("", _string_or_empty(None))
        self.assertEqual("", _string_or_empty(0))
        self.assertEqual("书房", _string_or_empty("  书房  "))


if __name__ == "__main__":
    unittest.main()
