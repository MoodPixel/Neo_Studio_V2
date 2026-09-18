from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha1
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "neo.lora.release.integration.v1"
IMAGE_INDEX = Path("neo_data/extensions/lora_stack/library_index.json")

PHASES = (
    (1, "Reproduction, Evidence, and Regression Baseline", "tests/test_image_phase1_reproduction_regression_baseline_20260918.py"),
    (2, "Canonical Output-to-Source Handoff Repair", "tests/test_image_phase2_canonical_source_handoff_20260918.py"),
    (3, "Results API Pagination and Complete Output Access", "tests/test_image_phase3_results_pagination_20260918.py"),
    (4, "Stable Results Selection and Scroll Preservation", "tests/test_image_phase4_stable_results_selection_scroll_20260918.py"),
    (5, "LoRA Identity and Metadata Persistence Hardening", "tests/test_image_phase5_lora_identity_persistence_20260918.py"),
    (6, "Image LoRA Search, Folder Filters, and Notes", "tests/test_image_phase6_lora_search_folder_notes_20260918.py"),
    (7, "Modern Video LoRA UI", "tests/test_video_lora_modern_ui_phase7_20260918.py"),
    (8, "Integration, Migration, Documentation, and Release", "tests/test_lora_release_integration_phase8_20260918.py"),
)

REQUIRED_FILES = (
    "neo_app/static/js/neo.js",
    "neo_app/static/css/neo.css",
    "neo_extensions/built_in/image.lora_stack/extension_manifest.json",
    "neo_extensions/built_in/video.lora_stack/extension_manifest.json",
    "neo_extensions/built_in/image.lora_stack/backend/library_store.py",
    "neo_extensions/built_in/image.lora_stack/backend/library_routes.py",
    "guides/01_IMAGE/lora_stack.md",
    "guides/02_VIDEO/video_lora_stack_ui.md",
    "guides/00_GLOBAL/lora_release_phase8.md",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _portable(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.split("/") if part and part not in {".", ".."})


def _identity(provider_id: Any, catalog_name: Any) -> str:
    provider = str(provider_id or "").strip().casefold()
    path = _portable(catalog_name).casefold()
    return f"{provider}:{path}" if provider and path else ""


def _record_id(identity: str) -> str:
    digest = sha1(identity.casefold().encode("utf-8", errors="ignore")).hexdigest()[:16]
    stem = Path(identity.split(":", 1)[-1]).stem or "lora"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)[:64].strip("._-") or "lora"
    return f"{safe}_{digest}"


def image_library_migration_inventory(root: str | Path = ROOT) -> dict[str, Any]:
    """Inspect Image LoRA persistence without rewriting or guessing legacy identities."""
    root_path = Path(root)
    path = root_path / IMAGE_INDEX
    if not path.exists():
        return {
            "status": "not_needed", "index_path": str(IMAGE_INDEX).replace("\\", "/"),
            "record_count": 0, "canonical_count": 0, "migration_candidate_count": 0,
            "ambiguous_count": 0, "apply_endpoint": "/api/extensions/lora_stack/library/identity-repair",
            "preview_endpoint": "/api/extensions/lora_stack/library/identity-audit", "mutated": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "blocked_invalid_index", "index_path": str(IMAGE_INDEX), "error": str(exc), "mutated": False}
    records = [item for item in payload.get("records", []) if isinstance(item, dict)]
    canonical, candidates, ambiguous = [], [], []
    basename_counts: dict[str, int] = {}
    for record in records:
        basename = Path(_portable(record.get("catalog_name") or record.get("name"))).name.casefold()
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
    for record in records:
        provider = str(record.get("provider_id") or "").strip().casefold()
        catalog_name = _portable(record.get("catalog_name") or record.get("name"))
        identity = _identity(provider, catalog_name)
        expected_id = _record_id(identity) if identity else ""
        summary = {"record_id": str(record.get("id") or ""), "provider_id": provider, "catalog_name": catalog_name}
        if identity and record.get("canonical_identity") == identity and record.get("id") == expected_id:
            canonical.append(summary)
        elif not provider or "/" not in catalog_name:
            reason = "provider_missing" if not provider else "basename_only_identity"
            if basename_counts.get(Path(catalog_name).name.casefold(), 0) > 1:
                reason = "ambiguous_duplicate_basename"
            ambiguous.append({**summary, "reason": reason})
        else:
            candidates.append({**summary, "expected_identity": identity, "expected_id": expected_id})
    status = "ready" if not candidates and not ambiguous else "preview_required"
    return {
        "status": status, "index_path": str(IMAGE_INDEX).replace("\\", "/"),
        "schema_version": payload.get("schema_version"), "record_count": len(records),
        "canonical_count": len(canonical), "migration_candidate_count": len(candidates),
        "ambiguous_count": len(ambiguous), "candidates": candidates, "ambiguous": ambiguous,
        "preview_endpoint": "/api/extensions/lora_stack/library/identity-audit",
        "apply_endpoint": "/api/extensions/lora_stack/library/identity-repair",
        "policy": "Preview against the selected live provider catalog; backup before apply; never guess or delete ambiguous records.",
        "mutated": False,
    }


def video_lora_migration_inventory(root: str | Path = ROOT) -> dict[str, Any]:
    path = Path(root) / "neo_app/static/js/neo.js"
    if not path.is_file():
        return {
            "status": "blocked_reader_missing", "legacy_fields_inspected_at_runtime": False,
            "one_way_to_canonical": False, "legacy_writeback": False,
            "user_state_mutated_by_report": False, "note": "Video UI source is missing from the inspected tree.",
        }
    source = path.read_text(encoding="utf-8")
    retired = "VIDEO_LORA_RETIRED_FIELDS" in source and "retireLegacyVideoLoraDraft" in source
    return {
        "status": "runtime_reader_ready" if retired else "blocked_reader_missing",
        "legacy_fields_inspected_at_runtime": True,
        "one_way_to_canonical": retired,
        "legacy_writeback": False,
        "user_state_mutated_by_report": False,
        "note": "Browser-owned saved drafts are inspected only when loaded; this release report never edits browser storage.",
    }


def _file_check(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {"name": relative, "ok": path.is_file(), "size_bytes": path.stat().st_size if path.is_file() else 0}


def _syntax_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for relative in ("neo_app/lora_release_integration.py", "neo_extensions/built_in/image.lora_stack/backend/library_routes.py"):
        try:
            ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
            checks.append({"name": f"python:{relative}", "ok": True})
        except Exception as exc:  # noqa: BLE001 - release evidence must retain exact failure.
            checks.append({"name": f"python:{relative}", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    try:
        result = subprocess.run(["node", "--check", str(root / "neo_app/static/js/neo.js")], cwd=root, capture_output=True, text=True, timeout=30)
        checks.append({"name": "javascript:neo_app/static/js/neo.js", "ok": result.returncode == 0, "error": (result.stderr or "").strip()})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "javascript:neo_app/static/js/neo.js", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return checks


def _gpu_evidence(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"provided": False, "physical_inference_proven": False, "status": "not_run"}
    evidence_path = Path(path)
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"provided": True, "physical_inference_proven": False, "status": "invalid", "error": str(exc)}
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    proven = bool(data.get("gate") == "pass" and cases and all(isinstance(item, dict) and item.get("physical_inference_proven") for item in cases))
    return {"provided": True, "physical_inference_proven": proven, "status": "passed" if proven else "incomplete", "case_count": len(cases)}


def build_release_report(root: str | Path = ROOT, *, gpu_evidence: str | Path | None = None, include_syntax: bool = True) -> dict[str, Any]:
    root_path = Path(root)
    files = [_file_check(root_path, relative) for relative in REQUIRED_FILES]
    phases = [{"phase": number, "name": name, "evidence": evidence, "ok": (root_path / evidence).is_file()} for number, name, evidence in PHASES]
    syntax = _syntax_checks(root_path) if include_syntax else []
    manifests = []
    for relative in ("neo_extensions/built_in/image.lora_stack/extension_manifest.json", "neo_extensions/built_in/video.lora_stack/extension_manifest.json"):
        try:
            json.loads((root_path / relative).read_text(encoding="utf-8"))
            manifests.append({"name": relative, "ok": True})
        except Exception as exc:  # noqa: BLE001
            manifests.append({"name": relative, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    image_migration = image_library_migration_inventory(root_path)
    video_migration = video_lora_migration_inventory(root_path)
    gpu = _gpu_evidence(gpu_evidence)
    static_ready = all(item["ok"] for item in [*files, *phases, *syntax, *manifests])
    migration_tooling_ready = image_migration.get("status") != "blocked_invalid_index" and video_migration.get("one_way_to_canonical") is True
    code_ready = bool(static_ready and migration_tooling_ready)
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": _now(), "phase": 8,
        "status": "release_ready" if code_ready else "blocked", "code_ready": code_ready,
        "production_proven": bool(code_ready and gpu.get("physical_inference_proven")),
        "summary": {"phase_count": len(phases), "phase_passed": sum(item["ok"] for item in phases), "required_file_count": len(files), "required_files_present": sum(item["ok"] for item in files)},
        "phases": phases, "file_checks": files, "syntax_checks": syntax, "manifest_checks": manifests,
        "migration": {"image_library": image_migration, "video_lora": video_migration, "automatic_destructive_migration": False},
        "gpu_evidence": gpu,
        "release_note": "Code-ready is not physical inference proof. Keep a neo_data backup, preview Image identity repairs, and require real backend/GPU evidence before claiming production validation.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the integrated Image/Video LoRA Phase-8 release contract.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--gpu-evidence", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = build_release_report(args.root, gpu_evidence=args.gpu_evidence or None)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")
    return 0 if report["code_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
