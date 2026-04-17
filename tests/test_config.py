from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_shore_bridge.bridge.config import BridgeConfig


class BridgeConfigTest(unittest.TestCase):
    def test_from_mapping_parses_extended_fields(self) -> None:
        settings = BridgeConfig.from_mapping(
            {
                "api_key_mode": "x-api-key",
                "platform_agent_map_json": '{"qq": "shore-qq", "discord": "shore-discord"}',
                "recall_debug": True,
                "connect_timeout_seconds": 1.5,
                "session_idle_minutes": 45,
            },
        )

        self.assertEqual(settings.api_key_mode, "x-api-key")
        self.assertEqual(settings.platform_agent_map["qq"], "shore-qq")
        self.assertEqual(settings.resolve_agent_id("QQ", "none"), "shore-qq")
        self.assertEqual(settings.resolve_agent_id("missing", "DISCORD"), "shore-discord")
        self.assertTrue(settings.recall_debug)
        self.assertAlmostEqual(settings.connect_timeout_seconds, 1.5)
        self.assertEqual(settings.session_idle_minutes, 45)
