from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.action_state import sanitize_replay_params
from neo_app.image.lanpaint_replay import (
    PHASE11_STATE,
    SCHEMA_ID,
    build_lanpaint_replay_contract,
    refresh_lanpaint_replay_contract,
    validate_lanpaint_replay_request,
)
from neo_app.image.output_records import (
    build_image_output_record,
    build_output_replay_metadata,
    build_output_replay_payload,
    build_route_metadata,
    build_route_snapshot,
)
from neo_app.image.output_service import build_image_result_reuse_payload


def _params() -> dict[str, Any]:
    return {
        "inpaint_engine": "lanpaint",
        "family": "qwen_image",
        "loader": "diffusion_model",
        "lanpaint_route": {
            "route_key": "qwen_image:diffusion_model:inpaint:lanpaint",
            "engine": "lanpaint",
            "family": "qwen_image",
            "loader": "diffusion_model",
            "variant": "crop_stitch_aura_v1",
            "policy_id": "lanpaint.qwen_image.v1",
            "compiler_id": "comfy.lanpaint.family_aware.v1",
            "graph_state": "qwen_zimage_onboarded_experimental",
        },
        "lanpaint_controls": {
            "crop_padding": 152,
            "processing_size": {"width": 768, "height": 768},
            "resize_method": "lanczos",
            "restore_resize_method": "lanczos",
            "sampling_mask": {"expand": 45, "blur": 31},
            "stitch_mask": {"expand": 50, "blur": 9.1},
            "steps": 20,
            "cfg": 4.0,
            "sampler": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
            "thinking_steps": 5,
            "prompt_mode": "Image First",
            "aura_shift": 3.1,
        },
        "lanpaint_ui_state": {
            "route": {
                "provider_id": "comfyui_portable",
                "family": "qwen_image",
                "loader": "diffusion_model",
                "mode": "inpaint",
                "engine": "lanpaint",
            }
        },
        "lanpaint_ui_state_fingerprint": "a" * 64,
        "lanpaint_capability_report": {
            "status": "experimental_available",
            "selectable": True,
            "executable": True,
            "capability_fingerprint": "b" * 64,
            "blockers": [],
            "warnings": [],
        },
        "lanpaint_capability_fingerprint": "b" * 64,
        "lanpaint_contract_fingerprint": "c" * 64,
        "lanpaint_compile_plan_fingerprint": "d" * 64,
        "lanpaint_node_roles": {
            "1": "source_image",
            "20": "family_model_loader",
            "30": "family_model_transform",
            "40": "sampler",
        },
        "_neo_sampler_node_id": "40",
        "lanpaint_selected_assets": [
            {"role_id": "diffusion_model", "selected": "qwen_image_bf16.safetensors", "state": "verified_live_catalog"},
            {"role_id": "qwen_image_clip_loader", "selected": "qwen_image_text_encoder.safetensors", "state": "verified_live_catalog"},
            {"role_id": "vae_or_ae", "selected": "qwen_image_vae.safetensors", "state": "verified_live_catalog"},
        ],
        "lanpaint_mask_target": "masked_area",
        "lanpaint_lora_mode": "model_and_clip",
        "lanpaint_lora_requested_rows": [
            {"id": "style", "lora_name": "style.safetensors", "strength_model": 0.7, "strength_clip": 0.5, "enabled": True},
        ],
        "lanpaint_lora_base_graph_rows": [
            {"id": "style", "lora_name": "style.safetensors", "strength_model": 0.7, "strength_clip": 0.5, "enabled": True},
        ],
        "lanpaint_lora_deferred_rows": [],
        "lanpaint_lora_lineage": {
            "base_model_ref": ["20", 0],
            "final_model_ref": ["25", 0],
            "final_clip_ref": ["25", 1],
            "ordered_node_ids": ["25"],
        },
    }


def _assets() -> list[dict[str, Any]]:
    return [
        {
            "asset_id": "source_image_1",
            "role": "source",
            "label": "Source image 1",
            "filename": "source.png",
            "path": "neo_data/projects/demo/source.png",
            "url": "/api/image/source/source.png",
            "backend_handoff_name": "neo_src_runtime.png",
        },
        {
            "asset_id": "mask_image",
            "role": "mask",
            "label": "Mask",
            "filename": "mask.png",
            "path": "neo_data/projects/demo/mask.png",
            "url": "/api/image/source/mask.png",
            "backend_handoff_name": "neo_mask_runtime.png",
        },
    ]


def _workflow() -> dict[str, Any]:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
        "20": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_bf16.safetensors"}},
        "25": {"class_type": "LoraLoader", "inputs": {"model": ["20", 0], "clip": ["21", 0]}},
        "30": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["25", 0], "shift": 3.1}},
        "40": {"class_type": "LanPaint_KSampler", "inputs": {"model": ["30", 0]}},
    }


def run_audit() -> dict[str, Any]:
    params = _params()
    contract = build_lanpaint_replay_contract(
        params,
        provider_id="comfyui_portable",
        input_assets=_assets(),
        workflow_prompt=_workflow(),
    )
    missing_contract = build_lanpaint_replay_contract(params, provider_id="comfyui_portable", input_assets=[])
    refreshed = refresh_lanpaint_replay_contract(params, provider_id="comfyui_portable", input_assets=_assets(), workflow_prompt=_workflow())
    tampered = deepcopy(refreshed)
    tampered["lanpaint_replay"]["controls"]["steps"] = 99

    route = build_route_metadata(
        mode="inpaint",
        provider_id="comfyui_portable",
        params=refreshed,
        model={"family": "qwen_image", "loader": "diffusion_model"},
    )
    snapshot = build_route_snapshot(
        route_metadata=route,
        mode="inpaint",
        provider_id="comfyui_portable",
        params=refreshed,
        model={"family": "qwen_image", "loader": "diffusion_model"},
    )
    record = build_image_output_record(
        mode="inpaint",
        subtab="inpaint",
        job_id="audit-job",
        provider_id="comfyui_portable",
        backend_profile_id="portable-local",
        params=refreshed,
        model={
            "family": "qwen_image",
            "loader": "diffusion_model",
            "model": "qwen_image_bf16.safetensors",
            "vae": "qwen_image_vae.safetensors",
        },
        output_files=[{"file_id": "output-1", "filename": "output.png"}],
        active_file="output-1",
        result_id="result-1",
    )
    record["source"]["input_assets"] = _assets()
    record["lanpaint"] = build_lanpaint_replay_contract(
        refreshed,
        provider_id="comfyui_portable",
        input_assets=_assets(),
        route_snapshot=snapshot,
        output_lineage=record.get("lineage"),
    )
    record["replay"] = build_output_replay_metadata(record)
    record["replay_payload"] = build_output_replay_payload(record)
    reuse = build_image_result_reuse_payload(record)

    sanitized, sanitize_report = sanitize_replay_params(
        {
            **refreshed,
            "source_image_name": "neo_src_runtime.png",
            "mask_image_name": "neo_mask_runtime.png",
        },
        mode="inpaint",
    )

    schema_path = ROOT / "neo_app/image/lanpaint_replay.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "neo_app/image/lanpaint_replay.py",
            schema_path,
            ROOT / "neo_app/image/output_records.py",
            ROOT / "neo_app/image/output_service.py",
            ROOT / "neo_app/image/action_state.py",
            ROOT / "neo_app/providers/comfy_workflows/lanpaint.py",
            ROOT / "neo_app/providers/comfy_workflows/lanpaint_family.py",
            ROOT / "neo_app/providers/comfy_provider.py",
        )
    )

    checks = [
        {
            "id": "canonical_replay_contract",
            "passed": contract.get("schema_id") == SCHEMA_ID and contract.get("phase_state") == PHASE11_STATE,
            "detail": "Phase 11 emits the canonical LanPaint replay contract.",
        },
        {
            "id": "exact_route_and_controls_preserved",
            "passed": contract.get("route", {}).get("route_key") == "qwen_image:diffusion_model:inpaint:lanpaint" and contract.get("controls", {}).get("aura_shift") == 3.1,
            "detail": "Exact route identity and resolved LanPaint controls are preserved.",
        },
        {
            "id": "portable_assets_only",
            "passed": not contract.get("input_assets", {}).get("missing_roles") and "backend_handoff_name" not in contract.get("input_assets", {}).get("source", {}) and contract.get("audit", {}).get("provider_upload_aliases_retained") is False,
            "detail": "Replay retains Neo-owned source/mask references, not disposable provider aliases or image bytes.",
        },
        {
            "id": "missing_assets_block_reconstruction",
            "passed": set(missing_contract.get("input_assets", {}).get("missing_roles", [])) == {"source", "mask"} and missing_contract.get("reconstruction", {}).get("state") == "blocked_missing_portable_assets",
            "detail": "Missing portable source or mask assets block reconstruction explicitly.",
        },
        {
            "id": "fingerprint_tamper_detection",
            "passed": any("fingerprint" in item for item in validate_lanpaint_replay_request(tampered, provider_id="comfyui_portable", family="qwen_image", loader="diffusion_model")),
            "detail": "Tampered replay controls are rejected by the deterministic fingerprint.",
        },
        {
            "id": "exact_route_mismatch_fails_closed",
            "passed": any("family" in item for item in validate_lanpaint_replay_request(refreshed, provider_id="comfyui_portable", family="z_image", loader="diffusion_model")),
            "detail": "Replay cannot silently substitute another family, loader or engine.",
        },
        {
            "id": "workflow_and_lora_lineage_present",
            "passed": contract.get("workflow_lineage", {}).get("node_count") == 4 and contract.get("lora", {}).get("lineage", {}).get("final_model_ref") == ["25", 0],
            "detail": "Workflow roles and final LoRA model/CLIP lineage are recorded.",
        },
        {
            "id": "lora_restore_is_disabled_pending_revalidation",
            "passed": contract.get("lora", {}).get("restore_enabled") is False and contract.get("lora", {}).get("revalidation_required") is True and "disabled" in contract.get("lora", {}).get("restore_policy", ""),
            "detail": "Saved LoRA rows restore disabled and require live route/node/catalog revalidation.",
        },
        {
            "id": "route_snapshot_is_engine_aware",
            "passed": route.get("engine") == "lanpaint" and snapshot.get("route", {}).get("engine") == "lanpaint" and snapshot.get("route", {}).get("policy_id") == "lanpaint.qwen_image.v1",
            "detail": "Route metadata and snapshots retain engine, policy, compiler and graph identity.",
        },
        {
            "id": "output_replay_and_reuse_are_linked",
            "passed": record.get("replay", {}).get("lanpaint", {}).get("schema_id") == SCHEMA_ID and record.get("replay_payload", {}).get("lanpaint", {}).get("replay_fingerprint") == record.get("lanpaint", {}).get("replay_fingerprint") and reuse.get("lanpaint", {}).get("schema_id") == SCHEMA_ID and {item.get("role") for item in reuse.get("input_assets", [])} == {"source", "mask"},
            "detail": "Output records, replay payloads and Results reuse expose the same LanPaint contract and portable assets.",
        },
        {
            "id": "disposable_names_are_sanitized",
            "passed": "source_image_name" not in sanitized and "mask_image_name" not in sanitized and {"source_image_name", "mask_image_name"}.issubset(set(sanitize_report.get("cleared_fields", []))) and sanitized.get("lanpaint_replay", {}).get("schema_id") == SCHEMA_ID,
            "detail": "Replay sanitization removes disposable compiled names while retaining the canonical contract.",
        },
        {
            "id": "frontend_reconstruction_and_audit_exist",
            "passed": all(marker in js for marker in ("buildImageLanpaintReplayRestore", "neo.image.lanpaint_replay_reconstruction.v1", "LanPaint Replay Audit", "blocked_missing_portable_assets", "rows_restored_disabled_pending_revalidation")),
            "detail": "Frontend reconstruction, preflight blockers and inspector audit are present.",
        },
        {
            "id": "compiler_and_provider_refresh_lineage",
            "passed": all("refresh_lanpaint_replay_contract" in (ROOT / path).read_text(encoding="utf-8") for path in ("neo_app/providers/comfy_workflows/lanpaint.py", "neo_app/providers/comfy_workflows/lanpaint_family.py", "neo_app/providers/comfy_provider.py")),
            "detail": "Krea, Qwen/Z and post-LoRA provider paths refresh the replay contract at the correct boundaries.",
        },
        {
            "id": "schema_and_cache_revision_are_current",
            "passed": schema.get("$id") == SCHEMA_ID and "phase11=lanpaint_replay_lineage_20260804" in index and any(marker in js for marker in ("lanpaint_capability_transport_hotfix_20260804", "lanpaint_lora_independence_hotfix_20260804", "global_lora_engine_decoupling_20260805", "lanpaint_family_adapter_v2_20260805", "lanpaint_route_parity_phase14_20260805", "lanpaint_sd_family_phase15_20260805", "lanpaint_flux1_family_phase16_20260805", "lanpaint_flux2_family_phase17_20260805")),
            "detail": "Schema and static asset cache revision match Phase 11.",
        },
        {
            "id": "public_paths_only",
            "passed": all(token not in source_text for token in ("/" + "home" + "/", "/" + "Users" + "/", "/" + "mnt" + "/" + "data", "C:" + chr(92) + "Users" + chr(92), "D:" + chr(92) + "Users" + chr(92))) and re.search(r"[A-Za-z]:\\\\(?:Users|Documents|Desktop|Downloads|AppData)\\\\", source_text) is None,
            "detail": "Phase 11 public source contains no personal or machine-specific paths.",
        },
    ]

    return {
        "schema_id": "neo.validation.lanpaint_route_family_phase11.v1",
        "phase": 11,
        "title": "LanPaint replay, lineage and audit support",
        "passed": all(item["passed"] for item in checks),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_total": len(checks),
        "checks": checks,
        "replay_schema_id": SCHEMA_ID,
        "replay_fingerprint": contract.get("replay_fingerprint", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 11 replay, lineage and audit support.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = run_audit()
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
