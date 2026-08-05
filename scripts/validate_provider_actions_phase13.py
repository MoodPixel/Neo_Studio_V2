#!/usr/bin/env python3
"""Run the Phase 13 Image provider-action regression matrix."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.provider_action_regression import run_provider_action_regression_matrix


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path for the complete JSON report.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of indented JSON.")
    args = parser.parse_args()

    report = run_provider_action_regression_matrix()
    encoded = json.dumps(report, indent=None if args.compact else 2, sort_keys=False)
    print(encoded)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
