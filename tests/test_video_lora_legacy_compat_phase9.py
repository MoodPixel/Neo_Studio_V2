from __future__ import annotations

import unittest

from neo_app.video.video_lora_legacy_compat_regression import run_phase9_gate


class VideoLoRALegacyCompatPhase9Tests(unittest.TestCase):
    def test_phase9_gate(self) -> None:
        result = run_phase9_gate()
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("case_count"), 21)
        self.assertEqual(result.get("passed"), 21)
        self.assertEqual(result.get("failed"), 0)
        self.assertEqual(result.get("combined_case_count"), 111)


if __name__ == "__main__":
    unittest.main()
