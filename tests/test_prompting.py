from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_shore_bridge.bridge.prompting import build_recall_block, build_recall_preview


class PromptingTest(unittest.TestCase):
    def test_build_recall_block_filters_and_formats(self) -> None:
        response = {
            "degraded": True,
            "memory_context": [
                {
                    "memory_id": 10,
                    "score": 0.9,
                    "time": "2026-01-01T00:00:00Z",
                    "content": "Alice likes jasmine tea.",
                    "entities": [{"name": "Alice"}],
                },
                {
                    "memory_id": 11,
                    "score": 0.1,
                    "content": "This should be filtered.",
                },
            ],
            "agent_state": {"mood": "calm", "mind": "focused"},
        }

        block = build_recall_block(
            response,
            min_score=0.5,
            max_chars=500,
            include_entities=True,
            inject_agent_state=True,
            degraded_notice=True,
        )

        self.assertIn("Shore Recall Notice", block)
        self.assertIn("Alice likes jasmine tea.", block)
        self.assertIn("entities: Alice", block)
        self.assertIn("mood: calm", block)
        self.assertNotIn("This should be filtered.", block)

    def test_build_recall_preview_handles_empty_result(self) -> None:
        preview = build_recall_preview({"memory_context": []}, min_score=0.0, limit=5)

        self.assertEqual(preview, "No recalled memories matched the current filter.")
