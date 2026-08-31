from __future__ import annotations

import unittest

from neo_app.video.minimax_h3_lora_regression import run_minimax_h3_lora_regression


class MiniMaxH3LoRARegressionPhase6Tests(unittest.TestCase):
    def test_phase6_gate(self) -> None:
        result = run_minimax_h3_lora_regression()
        failed = [case for case in result.get("cases", []) if not case.get("ok")]
        self.assertEqual(result.get("case_count"), 43, result)
        self.assertEqual(result.get("passed"), 43, result)
        self.assertEqual(result.get("failed"), 0, failed)
        self.assertEqual(result.get("gate"), "pass", failed)
        self.assertTrue(result.get("next_phase_allowed"), failed)


if __name__ == "__main__":
    unittest.main()
