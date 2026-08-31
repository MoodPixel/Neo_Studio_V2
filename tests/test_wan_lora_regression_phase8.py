from __future__ import annotations

import unittest

from neo_app.video.wan_lora_regression import run_phase8_gate


class WanLoRARegressionPhase8Tests(unittest.TestCase):
    def test_phase8_gate(self) -> None:
        result = run_phase8_gate()
        failures = [case for case in result.get("cases", []) if not case.get("ok")]
        self.assertEqual(result.get("gate"), "pass", failures)
        self.assertEqual(result.get("failed"), 0, failures)
        self.assertEqual(result.get("passed"), result.get("case_count"), failures)
        self.assertTrue(result.get("next_phase_allowed"), failures)


if __name__ == "__main__":
    unittest.main()
