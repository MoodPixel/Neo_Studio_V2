from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.release_hygiene import ROOT_DIR, build_clean_release_zip, runtime_data_hygiene_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Neo Studio public runtime archive from the internal source tree.")
    parser.add_argument("--output", "-o", default="dist/Neo_Studio_V2_clean_release.zip", help="Output zip path.")
    parser.add_argument("--audit-only", action="store_true", help="Only print the public-export exclusion audit; do not build an archive.")
    args = parser.parse_args()

    if args.audit_only:
        payload = runtime_data_hygiene_audit(ROOT_DIR)
    else:
        payload = build_clean_release_zip(Path(args.output), ROOT_DIR)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
