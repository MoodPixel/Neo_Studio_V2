from __future__ import annotations

import unittest

from neo_app.video.ltx_lora_regression import run_regression


class LtxLoRARegressionPhase7Tests(unittest.TestCase):
    def test_phase7_gate(self) -> None:
        report = run_regression()
        failures = [case for case in report.get("cases", []) if not case.get("ok")]
        self.assertEqual(report.get("case_count"), 17, report)
        self.assertEqual(report.get("failed"), 0, failures)
        self.assertTrue(report.get("ok"), failures)
        self.assertEqual(report.get("gate"), "pass", failures)
        self.assertTrue(report.get("next_phase_allowed"), failures)


if __name__ == "__main__":
    unittest.main()
