from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.providers.comfy_workflows.lanpaint import (
    AUTHORITY,
    COMPILER_ID,
    COMPILER_STATE,
    LANPAINT_OBJECT_INFO_NODE_CLASSES,
    SCHEMA_ID,
    WORKFLOW_TYPE,
    build_lanpaint_comfy_compile_plan,
    lanpaint_comfy_compile_plan_fingerprint,
    validate_lanpaint_comfy_compile_plan,
)
from scripts.audit_lanpaint_route_family_phase0 import build_report as build_phase0_report
from scripts.audit_lanpaint_route_family_phase1 import build_report as build_phase1_report
from scripts.audit_lanpaint_route_family_phase2 import build_report as build_phase2_report
from scripts.audit_lanpaint_route_family_phase3 import build_report as build_phase3_report

AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase4_audit.v1"
DATE = "2026-08-04"


def _route(*, family: str = "krea2_turbo", loader: str = "gguf", provider: str = "comfyui_portable") -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": provider,
            "family": family,
            "loader": loader,
            "mode": "inpaint",
            "engine": "lanpaint",
            "variant": "default",
        },
        "assets": {
            "source_image": {"kind": "neo_asset_id", "ref": "audit-source"},
            "mask_image": {"kind": "neo_asset_id", "ref": "audit-mask"},
        },
    }


def _inputs(*names: str) -> dict[str, Any]:
    return {"required": list(names), "optional": [], "all": list(names)}


def _capabilities(*, loader: str = "gguf", loader_node: str = "UnetLoaderGGUF", lora: bool = True) -> dict[str, Any]:
    node_inputs = {
        "LoadImage": _inputs("image", "upload"),
        "ImageToMask": _inputs("image", "channel"),
        "CLIPLoader": _inputs("clip_name", "type", "device"),
        "CLIPTextEncode": _inputs("clip", "text"),
        "ConditioningZeroOut": _inputs("conditioning"),
        "VAELoader": _inputs("vae_name"),
        "CropByMask": _inputs("image", "mask", "padding"),
        "ImageResizeKJv2": _inputs("image", "mask", "width", "height", "upscale_method"),
        "GrowMaskWithBlur": _inputs("mask", "expand", "blur_radius"),
        "VAEEncode": _inputs("pixels", "vae"),
        "SetLatentNoiseMask": _inputs("samples", "mask"),
        "DifferentialDiffusionAdvanced": _inputs("model", "samples", "mask", "multiplier"),
        "LanPaint_KSampler": _inputs(
            "model", "positive", "negative", "latent_image", "seed", "steps", "cfg",
            "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps",
            "LanPaint_PromptMode", "Inpainting_mode",
        ),
        "VAEDecode": _inputs("samples", "vae"),
        "ImageCompositeMasked": _inputs("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": _inputs("images"),
    }
    if lora:
        node_inputs["LoraLoaderModelOnly"] = _inputs("model", "lora_name", "strength_model")
    roles = {
        "krea2_clip_loader": {"available": True, "backend_node": "CLIPLoader", "notes": []},
        "qwen_image_vae": {"available": True, "backend_node": "VAELoader", "notes": []},
    }
    if loader == "gguf":
        node_inputs[loader_node] = _inputs("gguf_name" if loader_node == "LoaderGGUF" else "unet_name")
        roles["gguf_unet"] = {"available": True, "backend_node": loader_node, "notes": []}
    else:
        node_inputs["UNETLoader"] = _inputs("unet_name", "weight_dtype")
        roles["diffusion_model"] = {"available": True, "backend_node": "UNETLoader", "notes": []}
    return {
        "provider_id": "comfyui_portable" if loader == "gguf" else "comfyui",
        "reachable": True,
        "object_info_available": True,
        "loaders": {loader: {"available": True, "roles": roles}},
        "object_info_node_inputs": node_inputs,
    }


def build_report() -> dict[str, Any]:
    gguf = build_lanpaint_comfy_compile_plan(_route(loader="gguf"), _capabilities(loader="gguf"))
    gguf_fallback = build_lanpaint_comfy_compile_plan(
        _route(loader="gguf"),
        _capabilities(loader="gguf", loader_node="LoaderGGUF"),
    )
    native = build_lanpaint_comfy_compile_plan(
        _route(loader="diffusion_model", provider="comfyui"),
        _capabilities(loader="diffusion_model"),
    )
    with_lora = build_lanpaint_comfy_compile_plan(_route(loader="gguf"), _capabilities(loader="gguf"), lora_stack_enabled=True)

    missing_sampler_capabilities = _capabilities(loader="gguf")
    missing_sampler_capabilities["object_info_node_inputs"].pop("LanPaint_KSampler")
    missing_sampler = build_lanpaint_comfy_compile_plan(_route(loader="gguf"), missing_sampler_capabilities)

    incompatible_capabilities = _capabilities(loader="gguf")
    incompatible_capabilities["object_info_node_inputs"]["LanPaint_KSampler"] = _inputs("model", "positive", "negative", "latent_image")
    incompatible_sampler = build_lanpaint_comfy_compile_plan(_route(loader="gguf"), incompatible_capabilities)

    ambiguous_capabilities = _capabilities(loader="gguf")
    ambiguous_capabilities["loaders"]["gguf"]["roles"].pop("gguf_unet")
    ambiguous_capabilities["object_info_node_inputs"]["LoaderGGUF"] = _inputs("gguf_name")
    ambiguous_loader = build_lanpaint_comfy_compile_plan(_route(loader="gguf"), ambiguous_capabilities)

    qwen = build_lanpaint_comfy_compile_plan(_route(family="qwen_image", loader="gguf"), _capabilities(loader="gguf"))
    schema = json.loads((ROOT / "neo_app" / "providers" / "comfy_workflows" / "lanpaint_compiler.schema.json").read_text(encoding="utf-8"))
    provider_source = (ROOT / "neo_app" / "providers" / "comfy_provider.py").read_text(encoding="utf-8")
    router_source = (ROOT / "neo_app" / "providers" / "compile_router.py").read_text(encoding="utf-8")

    previous = {
        "phase0": build_phase0_report(),
        "phase1": build_phase1_report(),
        "phase2": build_phase2_report(),
        "phase3": build_phase3_report(),
    }

    bindings = {item["stage_id"]: item["binding"] for item in gguf["stage_bindings"]}
    blocker_codes = {item["code"] for item in missing_sampler["diagnostics"]["blockers"]}
    incompatible_nodes = {item["node_class"] for item in incompatible_sampler["capability_evaluation"]["signature_mismatches"]}

    checks = [
        {
            "id": "compiler_identity_is_locked",
            "passed": gguf["schema_id"] == SCHEMA_ID and gguf["authority"] == AUTHORITY and gguf["compiler"]["compiler_id"] == COMPILER_ID,
            "detail": "The provider/compiler boundary publishes the Phase 4 schema and compiler identity.",
        },
        {
            "id": "compiler_remains_binding_only",
            "passed": gguf["compiler"]["state"] == COMPILER_STATE and gguf["compiler"]["graph_emitted"] is False and gguf["compiler"]["backend_prompt"] is None,
            "detail": "Phase 4 binds roles but emits no runnable Comfy prompt.",
        },
        {
            "id": "execution_and_ui_remain_disabled",
            "passed": gguf["execution"]["enabled"] is False and gguf["execution"]["selectable"] is False and gguf["compiler"]["ui_route_registered"] is False,
            "detail": "No production route or UI selector is activated.",
        },
        {
            "id": "gguf_loader_role_resolves",
            "passed": gguf["external_bindings"]["family_model"]["node_class"] == "UnetLoaderGGUF",
            "detail": "The preferred Krea 2 Turbo GGUF loader resolves from live backend roles.",
        },
        {
            "id": "gguf_loader_alias_fallback_resolves",
            "passed": gguf_fallback["external_bindings"]["family_model"]["node_class"] == "LoaderGGUF",
            "detail": "The alternative city96 GGUF loader class resolves without changing core route identity.",
        },
        {
            "id": "safetensors_loader_branch_resolves",
            "passed": native["external_bindings"]["family_model"]["node_class"] == "UNETLoader",
            "detail": "The future native/safetensors branch binds separately from GGUF.",
        },
        {
            "id": "krea_encoder_and_vae_roles_are_bound",
            "passed": gguf["external_bindings"]["text_encoder"]["required_clip_type"] == "krea2" and gguf["external_bindings"]["vae"]["node_class"] == "VAELoader",
            "detail": "Krea 2 keeps native CLIPLoader(type=krea2) and Qwen Image VAE bindings.",
        },
        {
            "id": "base_stage_roles_bind_to_sample_pipeline",
            "passed": (
                bindings["crop_context"]["node_chain"] == ["CropByMask"]
                and bindings["processing_resize"]["node_chain"] == ["ImageResizeKJv2"]
                and bindings["sampling_mask_refine"]["node_chain"] == ["GrowMaskWithBlur"]
                and bindings["lanpaint_sample"]["node_chain"] == ["LanPaint_KSampler"]
                and bindings["stitch_composite"]["node_chain"] == ["ImageCompositeMasked"]
            ),
            "detail": "The Phase 2 logical graph now has provider-local concrete node-role bindings.",
        },
        {
            "id": "neo_mask_adapter_is_explicit",
            "passed": bindings["mask_image"]["node_chain"] == ["LoadImage", "ImageToMask"],
            "detail": "Neo's separate mask asset is explicitly adapted to Comfy MASK data.",
        },
        {
            "id": "lora_is_conditional_before_differential_diffusion",
            "passed": with_lora["stage_bindings"][7]["binding"]["node_chain"] == ["LoraLoaderModelOnly", "DifferentialDiffusionAdvanced"],
            "detail": "The shared LoRA Stack remains an optional model-only transform before the required differential transform.",
        },
        {
            "id": "complete_live_capabilities_are_next_phase_ready",
            "passed": gguf["validation"]["graph_compile_ready_for_next_phase"] is True and native["validation"]["graph_compile_ready_for_next_phase"] is True,
            "detail": "Synthetic complete object_info proves both loader branches can reach the graph-emission boundary.",
        },
        {
            "id": "missing_lanpaint_sampler_fails_closed",
            "passed": "LanPaint_KSampler" in missing_sampler["capability_evaluation"]["missing_node_classes"] and "missing_required_nodes" in blocker_codes,
            "detail": "Missing custom nodes produce exact blockers instead of falling back to normal KSampler.",
        },
        {
            "id": "incompatible_sampler_signature_fails_closed",
            "passed": "LanPaint_KSampler" in incompatible_nodes and incompatible_sampler["validation"]["graph_compile_ready_for_next_phase"] is False,
            "detail": "Outdated/incompatible LanPaint node signatures are rejected before graph emission.",
        },
        {
            "id": "missing_custom_node_pack_is_identified",
            "passed": any(item.get("pack_id") == "LanPaint" and "LanPaint_KSampler" in item.get("missing_node_classes", []) for item in missing_sampler["diagnostics"]["missing_custom_node_packs"]),
            "detail": "Missing execution nodes identify the custom-node pack that must be installed or updated.",
        },
        {
            "id": "ambiguous_loader_selection_fails_closed",
            "passed": any(item.get("code") == "ambiguous_model_loader" for item in ambiguous_loader["diagnostics"]["blockers"]) and ambiguous_loader["capability_evaluation"]["binding_complete"] is False,
            "detail": "Multiple GGUF loaders without an authoritative discovered role are not guessed silently.",
        },
        {
            "id": "phase10_families_receive_only_family_owned_bindings",
            "passed": (
                qwen["family_policy_state"] == "complete_policy"
                and any(item["role_id"] == "family_model_transform" and item["binding"]["node_chain"] == ["ModelSamplingAuraFlow"] for item in qwen["stage_bindings"])
                and qwen["external_bindings"]["text_encoder"]["required_clip_type"] == "qwen_image"
                and any(item.get("code") == "missing_family_text_encoder_loader" for item in qwen["diagnostics"]["blockers"])
            ),
            "detail": "Phase 10 Qwen bindings use AuraFlow and qwen_image conditioning; Krea Differential Diffusion is not inherited.",
        },
        {
            "id": "provider_publishes_safe_lanpaint_object_info_slice",
            "passed": "LANPAINT_OBJECT_INFO_NODE_CLASSES" in provider_source and '"graph_compile_enabled": True' in provider_source and "PHASE5_GRAPH_STATE" in provider_source,
            "detail": "Comfy profile discovery still exposes the safe Phase 4 node slice, with Phase 5 activation declared explicitly.",
        },
        {
            "id": "required_object_info_scope_contains_core_custom_nodes",
            "passed": {"LanPaint_KSampler", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "DifferentialDiffusionAdvanced"}.issubset(set(LANPAINT_OBJECT_INFO_NODE_CLASSES)),
            "detail": "The safe capability scope covers the reusable LanPaint graph and model-transform nodes.",
        },
        {
            "id": "plan_validates_and_fingerprints",
            "passed": not validate_lanpaint_comfy_compile_plan(gguf) and gguf["plan_fingerprint"] == lanpaint_comfy_compile_plan_fingerprint(gguf),
            "detail": "The compiler plan validates and has a deterministic fingerprint.",
        },
        {
            "id": "public_schema_locks_no_graph_emission",
            "passed": schema.get("$id") == SCHEMA_ID and schema["properties"]["compiler"]["properties"]["graph_emitted"]["const"] is False,
            "detail": "The public schema makes graph emission and route activation invalid in Phase 4.",
        },
        {
            "id": "phase5_dispatch_is_explicit_and_phase4_plan_remains_isolated",
            "passed": "build_lanpaint_comfy_compile_plan(" not in provider_source and COMPILER_ID in router_source and "compile_lanpaint_family_inpaint" in provider_source,
            "detail": "Later family-aware dispatch is explicit while the Phase 4 planner remains a separate binding-only contract.",
        },
        {
            "id": "previous_phase_audits_still_pass",
            "passed": all(report.get("status") == "passed" for report in previous.values()),
            "detail": "Phases 0-3 remain green after the provider/compiler boundary was added.",
        },
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "compiler_authority": {
            "module": AUTHORITY,
            "schema_id": SCHEMA_ID,
            "compiler_id": COMPILER_ID,
            "workflow_type": WORKFLOW_TYPE,
            "state": COMPILER_STATE,
        },
        "plans": {
            "krea2_turbo_gguf": gguf,
            "krea2_turbo_gguf_loader_alias": gguf_fallback,
            "krea2_turbo_diffusion_model": native,
            "krea2_turbo_gguf_with_lora": with_lora,
            "missing_lanpaint_sampler": missing_sampler,
            "incompatible_lanpaint_sampler": incompatible_sampler,
            "ambiguous_gguf_loader": ambiguous_loader,
            "qwen_placeholder": qwen,
        },
        "previous_phase_status": {key: value.get("status") for key, value in previous.items()},
        "checks": checks,
        "summary": {
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "failed_ids": [item["id"] for item in failed],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the Phase 4 LanPaint Comfy provider/compiler boundary.")
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()
    report = build_report()
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
