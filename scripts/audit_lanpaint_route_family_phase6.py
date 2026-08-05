from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.providers.comfy_provider import ComfyProvider
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support
from scripts.audit_lanpaint_route_family_phase0 import build_report as build_phase0_report
from scripts.audit_lanpaint_route_family_phase1 import build_report as build_phase1_report
from scripts.audit_lanpaint_route_family_phase2 import build_report as build_phase2_report
from scripts.audit_lanpaint_route_family_phase3 import build_report as build_phase3_report
from scripts.audit_lanpaint_route_family_phase4 import build_report as build_phase4_report
from scripts.audit_lanpaint_route_family_phase5 import (
    _capabilities as phase5_capabilities,
    _job as phase5_job,
    build_report as build_phase5_report,
)
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _manifest, _node_inputs

SCHEMA_ID = "neo.image.lanpaint_route_family_phase6_lora_stack_audit.v1"
DATE = "2026-08-04"


def _compiler_capabilities(*, include_lora: bool = True) -> dict[str, Any]:
    payload = phase5_capabilities()
    if include_lora:
        payload["object_info_node_inputs"]["LoraLoaderModelOnly"] = _node_inputs("model", "lora_name", "strength_model")
    return payload


def _object_info(*, catalog: tuple[str, ...] = ("detail.safetensors", "style.safetensors"), signature: tuple[str, ...] = ("model", "lora_name", "strength_model")) -> dict[str, Any]:
    required: dict[str, Any] = {}
    for name in signature:
        if name == "lora_name":
            required[name] = [list(catalog), {}]
        elif name == "model":
            required[name] = ["MODEL", {}]
        else:
            required[name] = ["FLOAT", {}]
    return {"LoraLoaderModelOnly": {"input": {"required": required}}}


def _extensions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"payloads": {"lora_stack": {"enabled": bool(rows), "version": 1, "inputs": {"loras": rows} if rows else {}, "params": {"loras": rows} if rows else {}, "assets": {}, "metadata": {"source": "phase6_audit"}}}}


class AuditProvider(ComfyProvider):
    compiler_capabilities = _compiler_capabilities()
    live_object_info = _object_info()

    def discover_backend_capabilities(self):
        return self.compiler_capabilities

    def _get_json(self, path: str, **kwargs):
        return self.live_object_info if path == "/object_info" else {}


def _compile(rows: list[dict[str, Any]], provider_cls=AuditProvider):
    return provider_cls(_manifest()).compile_job(phase5_job(extensions=_extensions(rows)))


def _lora_nodes(prompt: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(str(node_id), node) for node_id, node in prompt.items() if isinstance(node, dict) and node.get("class_type") == "LoraLoaderModelOnly"]


def build_report() -> dict[str, Any]:
    previous = {
        "phase0": build_phase0_report(),
        "phase1": build_phase1_report(),
        "phase2": build_phase2_report(),
        "phase3": build_phase3_report(),
        "phase4": build_phase4_report(),
        "phase5": build_phase5_report(),
    }
    native = route_support("comfyui", "krea2_turbo", "gguf", "inpaint", "native")
    lanpaint = route_support("comfyui", "krea2_turbo", "gguf", "inpaint", "lanpaint")
    no_lora = _compile([])
    one = _compile([{"enabled": True, "name": "detail.safetensors", "strength": 0.7, "target": "both", "apply_to": "global"}])
    multi_rows = [
        {"enabled": True, "name": "style.safetensors", "strength": 0.45, "target": "base", "apply_to": "global"},
        {"enabled": True, "name": "detail.safetensors", "strength": 0.3, "target": "both", "apply_to": "global"},
    ]
    multi = _compile(multi_rows)
    deferred_rows = [
        {"enabled": True, "name": "style.safetensors", "strength": 0.6, "target": "both", "apply_to": "scene_region_hero"},
        {"enabled": True, "name": "detail.safetensors", "strength": 0.7, "target": "finish", "apply_to": "global"},
    ]

    class MissingNodeProvider(AuditProvider):
        compiler_capabilities = _compiler_capabilities(include_lora=False)

    class BadSignatureProvider(AuditProvider):
        live_object_info = _object_info(signature=("model", "lora_name"))

    class MissingAssetProvider(AuditProvider):
        live_object_info = _object_info(catalog=("another.safetensors",))

    deferred_only = _compile(deferred_rows, MissingNodeProvider)
    missing_node = _compile([{"enabled": True, "name": "detail.safetensors", "strength": 0.7}], MissingNodeProvider)
    bad_signature = _compile([{"enabled": True, "name": "detail.safetensors", "strength": 0.7}], BadSignatureProvider)
    missing_asset = _compile([{"enabled": True, "name": "detail.safetensors", "strength": 0.7}], MissingAssetProvider)

    no_prompt = no_lora.backend_payload.get("prompt") or {}
    one_prompt = one.backend_payload.get("prompt") or {}
    one_nodes = _lora_nodes(one_prompt)
    multi_nodes = _lora_nodes(multi.backend_payload.get("prompt") or {})
    lineage = (multi.backend_payload.get("actual_params") or {}).get("lanpaint_lora_lineage") or {}
    replay = (((multi.backend_payload.get("extensions") or {}).get("replay_payloads") or {}).get("lora_stack") or {})
    manifest = json.loads((ROOT / "neo_extensions/built_in/image.lora_stack/extension_manifest.json").read_text(encoding="utf-8"))

    changed_sources = [
        ROOT / "neo_app/providers/comfy_workflows/lanpaint.py",
        ROOT / "neo_app/providers/comfy_provider.py",
        ROOT / "neo_app/static/js/neo.js",
        ROOT / "neo_extensions/built_in/image.lora_stack/backend/support_matrix.py",
        ROOT / "neo_extensions/built_in/image.lora_stack/backend/patch_profile.py",
        ROOT / "neo_extensions/built_in/image.lora_stack/backend/workflow_patch.py",
        ROOT / "neo_extensions/built_in/image.lora_stack/ui/stack_panel.js",
    ]
    path_pattern = re.compile(r"(?:[A-Za-z]:\\(?:Users|Documents and Settings)\\|/(?:home|Users|mnt)/)")
    path_hits = [str(path.relative_to(ROOT)) for path in changed_sources if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        {"id": "engine_independent_compatibility", "passed": native["active"] is True and lanpaint["state"] == "experimental_available" and native["route_key"] == lanpaint["route_key"] == "krea2_turbo:gguf:inpaint" and lanpaint["workflow_route_key"].endswith(":lanpaint"), "detail": "Phase 12 lets Native Inpaint and LanPaint share Krea2 LoRA compatibility while retaining distinct workflow graph route keys."},
        {"id": "model_only_patch_profile", "passed": lanpaint["loader_node_class"] == "LoraLoaderModelOnly" and lanpaint["requires_clip"] is False and lanpaint["graph_patch"] == "lora_loader_model_only_consumer_rewire", "detail": "LanPaint uses the shared model-only consumer-rewire strategy."},
        {"id": "no_lora_graph_unchanged", "passed": no_lora.compile_status == "compiled" and not _lora_nodes(no_prompt), "detail": "An empty stack leaves the Phase 5 graph untouched."},
        {"id": "single_lora_rewires_differential", "passed": one.compile_status == "compiled" and len(one_nodes) == 1 and one_prompt.get("14", {}).get("inputs", {}).get("model") == [one_nodes[0][0], 0] and one_prompt.get("15", {}).get("inputs", {}).get("model") == ["14", 0], "detail": "The final LoRA model feeds DifferentialDiffusionAdvanced; LanPaint consumes Differential output."},
        {"id": "clip_conditioning_untouched", "passed": one_prompt.get("12", {}).get("inputs", {}).get("clip") == ["11", 0] and not lineage.get("clip_patched"), "detail": "Krea 2 CLIPLoader and conditioning are not patched."},
        {"id": "multi_lora_order_preserved", "passed": [node["inputs"]["lora_name"] for _, node in multi_nodes] == [row["name"] for row in multi_rows] and lineage.get("stack_order") == [row["name"] for row in multi_rows], "detail": "Multiple rows compile in canonical UI order."},
        {"id": "deferred_rows_do_not_require_loader", "passed": deferred_only.compile_status == "compiled" and not _lora_nodes(deferred_only.backend_payload.get("prompt") or {}) and (deferred_only.backend_payload.get("actual_params") or {}).get("lanpaint_lora_base_graph_rows") == [], "detail": "Regional and finish-only rows stay serialized without requiring the model-only loader or mutating the base graph."},
        {"id": "missing_node_fails_closed", "passed": missing_node.compile_status == "mock_compiled" and not missing_node.backend_payload.get("prompt"), "detail": "Missing LoraLoaderModelOnly blocks graph emission."},
        {"id": "signature_and_asset_gates", "passed": bad_signature.compile_status == "mock_compiled" and missing_asset.compile_status == "mock_compiled", "detail": "Live loader signature and selected LoRA catalog are validated before queue submission."},
        {"id": "replay_and_lineage_separate_compatibility_from_engine", "passed": lineage.get("engine") == "lanpaint" and lineage.get("compatibility_route_key") == "krea2_turbo:gguf:inpaint" and lineage.get("workflow_route_key", "").endswith(":lanpaint") and replay.get("route", {}).get("route_key") == "krea2_turbo:gguf:inpaint" and "workflow_engine" in replay.get("revalidate_keys", []), "detail": "Replay and output lineage retain graph engine metadata separately from the engine-independent compatibility route."},
        {"id": "manifest_and_frontend_engine_decoupled", "passed": manifest.get("route_states", {}).get("comfyui:krea2_turbo:gguf:inpaint") == "experimental_available" and not any(":lanpaint" in key for key in manifest.get("route_states", {})) and manifest.get("compatibility_dimensions", {}).get("engine") is False and "forge" in manifest.get("supported_backends", []) and "const engineIndependent = manifest?.compatibility_dimensions?.engine === false" in (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8"), "detail": "Manifest and frontend resolve LoRA compatibility without engine keys while preserving provider support and engine-specific graph lineage."},
        {"id": "no_personal_paths", "passed": not path_hits, "detail": "Phase 6 source changes contain no personal absolute paths."},
        {"id": "previous_phase_audits_pass", "passed": all(report.get("status") == "passed" for report in previous.values()), "detail": "Phase 0–5 route and graph regressions remain green."},
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "route": lanpaint,
        "native_route": native,
        "graph": {
            "base_node_count": len(no_prompt),
            "single_lora_node_ids": [node_id for node_id, _ in one_nodes],
            "multi_lora_node_ids": [node_id for node_id, _ in multi_nodes],
            "lineage": lineage,
        },
        "gating": {
            "missing_node": missing_node.compile_status,
            "bad_signature": bad_signature.compile_status,
            "missing_asset": missing_asset.compile_status,
        },
        "path_hits": path_hits,
        "previous_phase_status": {key: value.get("status") for key, value in previous.items()},
        "physical_validation": {
            "status": "not_run",
            "reason": "This packaging environment does not host the target ComfyUI profile, Krea 2 Turbo GGUF assets, or real Krea-compatible LoRA files.",
            "required_next": "Run no-LoRA, single-LoRA, multi-LoRA, and reversed-order masked-region generations before promoting the route from experimental_available.",
        },
        "checks": checks,
        "summary": {"passed": len(checks) - len(failed), "failed": len(failed), "total": len(checks), "failed_ids": [item["id"] for item in failed]},
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 6 LoRA Stack enablement.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
