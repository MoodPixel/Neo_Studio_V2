from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = ROOT / "neo_extensions/built_in/image.lora_stack"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EXT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXT_ROOT))

from tests.test_lora_stack_phase19_exact_catalog_execution import (
    ExactCheckpointProvider,
    ExactKreaProvider,
    MissingCheckpointProvider,
    _checkpoint_job,
    _checkpoint_manifest,
    _compile_krea,
)

CATALOG = importlib.import_module("neo_extensions.built_in.lora_stack.backend.catalog_bridge")
PATCHER = importlib.import_module("neo_extensions.built_in.lora_stack.backend.workflow_patch")
PATCH_PROFILE = importlib.import_module("neo_extensions.built_in.lora_stack.backend.patch_profile")
SCHEMA_ID = "neo.validation.image_lora_stack_phase19_exact_catalog_execution.v1"
DATE = "2026-08-05"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def _lora_node(prompt: dict[str, Any], node_class: str) -> tuple[str, dict[str, Any]] | None:
    for node_id, node in (prompt or {}).items():
        if isinstance(node, dict) and node.get("class_type") == node_class:
            return str(node_id), node
    return None


def build_report() -> dict[str, Any]:
    exact = CATALOG.resolve_exact_provider_catalog_name(
        "Krea2/Style.safetensors", [r"Krea2\Style.safetensors"]
    )
    ambiguous = CATALOG.resolve_exact_provider_catalog_name(
        "Krea2/Style.safetensors",
        [r"Krea2\Style.safetensors", "krea2/STYLE.safetensors"],
    )
    missing = CATALOG.resolve_exact_provider_catalog_name(
        "Krea2/Style.safetensors", [r"Krea2\Other.safetensors"]
    )

    krea_native = _compile_krea(ExactKreaProvider, None)
    krea_lanpaint = _compile_krea(ExactKreaProvider, "lanpaint")
    native_node = _lora_node(krea_native.backend_payload.get("prompt") or {}, "LoraLoaderModelOnly")
    lanpaint_node = _lora_node(krea_lanpaint.backend_payload.get("prompt") or {}, "LoraLoaderModelOnly")
    native_proof = krea_native.backend_payload.get("actual_params", {}).get("_neo_lora_execution_proof", {})
    lanpaint_proof = krea_lanpaint.backend_payload.get("actual_params", {}).get("_neo_lora_execution_proof", {})

    checkpoint = ExactCheckpointProvider(_checkpoint_manifest()).compile_job(_checkpoint_job("inpaint"))
    checkpoint_node = _lora_node(checkpoint.backend_payload.get("prompt") or {}, "LoraLoader")
    checkpoint_proof = checkpoint.backend_payload.get("actual_params", {}).get("_neo_lora_execution_proof", {})
    checkpoint_missing = MissingCheckpointProvider(_checkpoint_manifest()).compile_job(_checkpoint_job("outpaint"))

    anchor_route = {"backend": "comfyui", "family": "sdxl", "loader": "checkpoint", "workflow_mode": "inpaint", "engine": "native"}
    anchor_graph = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}}}
    anchor_profile = PATCH_PROFILE.build_lora_patch_profile(
        route=anchor_route,
        model_ref=["1", 0],
        clip_ref=["1", 1],
        sampler_node_id="5",
        source="phase19_audit_missing_anchor",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=True,
    )
    missing_anchor = PATCHER.apply_lora_stack_patch(
        anchor_graph,
        _checkpoint_job("inpaint").extensions,
        route=anchor_route,
        available_nodes=ExactCheckpointProvider.object_info,
        lora_patch_profile=anchor_profile,
    )

    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs = [
        ROOT / "guides/01_IMAGE/lora_stack.md",
        ROOT / "guides/01_IMAGE/lanpaint_route_family.md",
        ROOT / "neo_system_records/03_PROVIDER_SYSTEM/IMAGE_LORA_STACK_PHASE19_EXACT_CATALOG_AND_EXECUTION_PROOF_20260805.md",
        ROOT / "neo_system_records/09_VALIDATION/IMAGE_LORA_STACK_PHASE19_EXACT_CATALOG_AND_EXECUTION_PROOF_20260805.md",
    ]
    public_sources = [
        EXT_ROOT / "backend/catalog_bridge.py",
        EXT_ROOT / "backend/payload_schema.py",
        EXT_ROOT / "backend/provider_serialization.py",
        EXT_ROOT / "backend/workflow_patch.py",
        ROOT / "neo_app/providers/comfy_provider.py",
        ROOT / "README.md",
    ]
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings|LLM)\\)")
    path_hits = [
        path.relative_to(ROOT).as_posix()
        for path in public_sources
        if path_pattern.search(path.read_text(encoding="utf-8"))
    ]

    native_exact = bool(native_node and native_node[1].get("inputs", {}).get("lora_name") == r"Krea2\Style.safetensors")
    lanpaint_exact = bool(lanpaint_node and lanpaint_node[1].get("inputs", {}).get("lora_name") == r"Krea2\Style.safetensors")
    checkpoint_exact = bool(checkpoint_node and checkpoint_node[1].get("inputs", {}).get("lora_name") == r"Styles\Cinema.safetensors")

    checks = [
        _check("portable_to_exact_provider_binding", exact.get("status") == "resolved" and exact.get("provider_catalog_name") == r"Krea2\Style.safetensors" and exact.get("portable_catalog_name") == "Krea2/Style.safetensors", "Portable slash identity resolves to the exact Windows Comfy enum."),
        _check("ambiguous_binding_fails_closed", ambiguous.get("status") == "blocked_ambiguous_catalog_entry" and len(ambiguous.get("candidate_provider_names") or []) == 2, "Normalized duplicate matches cannot be guessed."),
        _check("missing_binding_fails_closed", missing.get("status") == "blocked_missing_catalog_entry", "A genuinely absent LoRA cannot compile."),
        _check("native_krea_exact_submission", krea_native.compile_status == "compiled" and native_exact, "Native Krea submits the exact model-only catalog value."),
        _check("lanpaint_krea_exact_submission", krea_lanpaint.compile_status == "compiled" and lanpaint_exact, "LanPaint Krea submits the same exact model-only catalog value."),
        _check("engine_independent_portable_identity", native_proof.get("portable_lora_names") == lanpaint_proof.get("portable_lora_names") == ["Krea2/Style.safetensors"], "Native and LanPaint share one portable compatibility identity."),
        _check("engine_specific_graph_lineage", native_proof.get("patched_model_consumer_nodes") != lanpaint_proof.get("patched_model_consumer_nodes"), "Native and LanPaint retain compiler-owned graph anchors."),
        _check("catalog_proof_verified", native_proof.get("provider_catalog_verified") is True and lanpaint_proof.get("provider_catalog_verified") is True, "Both engine paths emit verified live-catalog proof."),
        _check("checkpoint_model_clip_exact_submission", checkpoint.compile_status == "compiled" and checkpoint_exact, "Checkpoint Inpaint submits exact model+CLIP LoRA enum."),
        _check("checkpoint_model_lineage", bool(checkpoint_node) and checkpoint.backend_payload["prompt"]["5"]["inputs"]["model"] == [checkpoint_node[0], 0], "Sampler model input reaches the inserted checkpoint LoRA node."),
        _check("checkpoint_clip_lineage", bool(checkpoint_node) and checkpoint.backend_payload["prompt"]["2"]["inputs"]["clip"] == [checkpoint_node[0], 1] and checkpoint.backend_payload["prompt"]["3"]["inputs"]["clip"] == [checkpoint_node[0], 1], "Positive and negative encoders reach patched CLIP."),
        _check("explicit_missing_request_blocks", checkpoint_missing.compile_status == "mock_compiled" and checkpoint_missing.backend_payload.get("validation", {}).get("ok") is False and checkpoint_missing.backend_payload.get("actual_params", {}).get("_neo_lora_execution") == "blocked_missing_catalog_entry", "Explicit missing LoRA stops before queueing instead of running the base graph."),
        _check("missing_graph_anchor_blocks", missing_anchor.get("mutated") is False and missing_anchor.get("workflow") == anchor_graph and missing_anchor.get("workflow_patch", {}).get("execution_state") == "blocked_missing_graph_anchor", "Inserted loader intent is rejected unless real model/CLIP consumers can be rewired."),
        _check("execution_proof_schema", checkpoint_proof.get("schema_version") == "neo.image.lora_stack.execution_proof.v1" and checkpoint_proof.get("submitted_lora_names") == [r"Styles\Cinema.safetensors"], "Execution proof separates submitted and portable names."),
        _check("manifest_phase19_contract", manifest.get("phase19_exact_catalog_binding", {}).get("required") is True, "Manifest publishes the Phase 19 exact-binding rule."),
        _check("records_and_guides_present", all(path.exists() and "Phase 19" in path.read_text(encoding="utf-8") for path in docs), "Provider record, validation record, and both guides document the lock."),
        _check("portable_readme_examples", "<backend-root>/ComfyUI_windows_portable" in readme and "<BACKEND_ROOT>" not in readme, "README uses portable role placeholders rather than a personal path."),
        _check("no_personal_paths", not path_hits, "Changed public runtime and guide surfaces contain no personal absolute paths."),
        _check("github_read_only_recorded", "GitHub was inspected read-only and was not modified" in docs[2].read_text(encoding="utf-8"), "System record preserves the no-GitHub-write boundary."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "catalog_cases": {"exact": exact, "ambiguous": ambiguous, "missing": missing},
        "execution_proofs": {"krea_native": native_proof, "krea_lanpaint": lanpaint_proof, "checkpoint_inpaint": checkpoint_proof},
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host the user's target ComfyUI models and LoRA catalogs.",
            "required_next": "Run fixed-seed LoRA off, strength 0.0, and meaningful-strength A/B tests on representative model-only, model+CLIP, checkpoint, split-model, GGUF, Native Inpaint, LanPaint, Img2Img, and Outpaint routes.",
        },
        "checks": checks,
        "summary": {
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "total": len(checks),
            "failed_ids": [item["id"] for item in failed],
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit Phase 19 exact LoRA catalog binding and execution proof.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, indent=2 if args.pretty or args.output else None, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
