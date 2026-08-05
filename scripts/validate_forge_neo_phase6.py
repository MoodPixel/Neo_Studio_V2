from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.providers.forge_neo_validation import run_forge_neo_offline_validation, validate_forge_patch_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic offline Forge Neo Phase 6 validation matrix.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete JSON validation report instead of the concise summary.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optionally write the complete JSON report to this path.",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        help="Optionally validate a patch ZIP for path traversal, runtime state, cache, bytecode, and model-file leakage.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_forge_neo_offline_validation()
    if args.patch:
        patch_report = validate_forge_patch_archive(args.patch)
        report["patch_archive"] = patch_report
        if not patch_report.get("ok"):
            report["ok"] = False
            report.setdefault("failed_check_ids", []).append("patch_archive_hygiene")
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        summary = report.get("summary") or {}
        print(
            "Forge Neo Phase 6 offline validation: "
            f"{'PASS' if report.get('ok') else 'FAIL'} — "
            f"{summary.get('passed', 0)}/{summary.get('total', 0)} checks passed "
            f"across {summary.get('scenario_count', 0)} profile scenarios."
        )
        if report.get("failed_check_ids"):
            print("Failed checks:")
            for check_id in report["failed_check_ids"]:
                print(f"- {check_id}")
        if args.patch:
            patch_report = report.get("patch_archive") or {}
            print(
                "Patch archive hygiene: "
                f"{'PASS' if patch_report.get('ok') else 'FAIL'} — "
                f"{patch_report.get('entry_count', 0)} file entries checked."
            )
        print("Physical GPU/backend validation status: not run (required separately).")
        if args.output:
            print(f"Report: {args.output}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
