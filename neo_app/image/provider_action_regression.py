"""Deterministic end-to-end regression matrix for Image provider actions.

This module exercises the provider-neutral contracts and selected-provider
routing rules used by Preview and Output Inspector without requiring a live GPU
backend.  It is intentionally side-effect free so it can run in CI, release
validation, and public-source audits.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from neo_app.image.action_state import (
    clear_cross_provider_upload_caches,
    sanitize_replay_extensions,
    sanitize_replay_params,
)
from neo_app.image.output_records import build_image_output_record, build_output_lineage_metadata
from neo_app.image.preview_action_routing import build_preview_action_provider_evaluation
from neo_app.image.preview_actions import preview_action_definition_registry_payload
from neo_app.image.preview_finish_dispatch import build_derived_action_contract, normalize_preview_finish_params
from neo_app.image.preview_reference_handoff import build_preview_reference_handoff, normalize_preview_reference_handoffs
from neo_app.image.preview_source_handoff import build_preview_source_handoff, normalize_preview_source_handoff_params

SCHEMA_ID = "neo.image.provider_action_regression_matrix.v1"
CASE_SCHEMA_ID = "neo.image.provider_action_regression_case.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source(output_id: str = "output-a", job_id: str = "job-a") -> dict[str, Any]:
    return {
        "source_type": "generated_output",
        "source_scope": "regression_matrix",
        "result_id": f"result-{output_id}",
        "job_id": job_id,
        "output_id": output_id,
        "file_id": output_id,
        "filename": f"{output_id}.png",
        "path": f"neo_data/image_outputs/{output_id}.png",
        "url": f"/api/image/results/result-{output_id}/files/{output_id}",
        "width": 1024,
        "height": 768,
    }


def _extension_payload() -> dict[str, Any]:
    ids = (
        "image.controlnet",
        "image.ip_adapter",
        "image.layerdiffuse",
        "image.high_res_lab",
        "image.adetailer",
        "image.image_upscale",
    )
    return {
        "extensions": [
            {
                "enabled": True,
                "registry_enabled": True,
                "status": "enabled",
                "manifest": {
                    "id": extension_id,
                    "name": extension_id,
                    "supported_backends": (
                        ["comfyui", "comfyui_portable"]
                        if extension_id == "image.layerdiffuse"
                        else ["comfyui", "comfyui_portable", "forge"]
                    ),
                    "route_states": {"*": "available"},
                    "capability_profiles": (
                        {"faceid": {"available": True}}
                        if extension_id == "image.ip_adapter"
                        else {}
                    ),
                },
            }
            for extension_id in ids
        ]
    }


def _profile(provider_id: str, *, profile_id: str | None = None, bridge_state: str = "current") -> dict[str, Any]:
    provider = str(provider_id).casefold()
    caps = {
        "img2img": True,
        "inpaint": True,
        "outpaint": True,
        "controlnet": True,
        "ip_adapter": True,
        "layerdiffuse_inline": provider in {"comfyui", "comfyui_portable"},
        "highres_inline": True,
        "adetailer_inline": True,
        "image_upscale": True,
        "face_id": True,
    }
    profile: dict[str, Any] = {
        "profile_id": profile_id or f"{provider}-selected",
        "display_name": f"{provider.title()} Selected",
        "provider_id": provider,
        "provider_label": "Forge Neo" if provider == "forge" else ("ComfyUI" if provider.startswith("comfyui") else provider.title()),
        "surface": "image",
        "enabled": True,
        "capabilities": caps,
        "runtime": {"status": "connected", "reachable": True},
    }
    if provider == "forge":
        bridge: dict[str, Any] = {"selected": bridge_state != "missing"}
        if bridge_state == "current":
            bridge.update({
                "version": "1.2.1",
                "capabilities": {
                    "native_post_hires": True,
                    "native_post_hires_size_contract": True,
                    "native_operations": ["native_txt2img_upscale"],
                },
            })
        elif bridge_state == "legacy":
            bridge.update({"version": "1.1.0", "capabilities": {}})
        elif bridge_state == "operation_missing":
            bridge.update({"version": "1.2.1", "capabilities": {"native_post_hires": True, "native_post_hires_size_contract": True, "native_operations": []}})
        profile["runtime"]["forge_admin"] = {"bridge": bridge}
    return profile


def _overlay(provider_id: str) -> dict[str, Any]:
    provider = str(provider_id).casefold()
    return {
        "connected": True,
        "capabilities": deepcopy(_profile(provider)["capabilities"]),
        "extension_policy": {
            "image.controlnet": {"allowed": provider in {"forge", "comfyui", "comfyui_portable"}, "required_capability": "controlnet"},
            "image.ip_adapter": {
                "allowed": provider in {"forge", "comfyui", "comfyui_portable"},
                "required_capability": "ip_adapter",
                "faceid_available": True,
                "face_id_available": True,
            },
            "image.layerdiffuse": {"allowed": provider in {"comfyui", "comfyui_portable"}, "required_capability": "layerdiffuse_inline"},
            "image.high_res_lab": {"allowed": provider in {"forge", "comfyui", "comfyui_portable"}, "required_capability": "highres_inline"},
            "image.adetailer": {"allowed": provider in {"forge", "comfyui", "comfyui_portable"}, "required_capability": "adetailer_inline"},
            "image.image_upscale": {"allowed": provider in {"forge", "comfyui", "comfyui_portable"}, "required_capability": "image_upscale"},
        },
    }


def _evaluation(provider_id: str, *, bridge_state: str = "current") -> dict[str, Any]:
    profile = _profile(provider_id, bridge_state=bridge_state)
    return build_preview_action_provider_evaluation(
        profile=profile,
        overlay=_overlay(provider_id),
        extension_payload=_extension_payload(),
        family="sdxl",
        loader="checkpoint",
        workflow_mode="generate",
        expert_mode=True,
    )


def _case(case_id: str, category: str, check: Callable[[], Any], *, provider_id: str = "", action_id: str = "") -> dict[str, Any]:
    try:
        detail = check()
        return {
            "schema": CASE_SCHEMA_ID,
            "case_id": case_id,
            "category": category,
            "provider_id": provider_id,
            "action_id": action_id,
            "status": "passed",
            "detail": detail if isinstance(detail, (str, int, float, bool, list, dict)) or detail is None else str(detail),
        }
    except Exception as exc:  # pragma: no cover - the report itself captures failures
        return {
            "schema": CASE_SCHEMA_ID,
            "case_id": case_id,
            "category": category,
            "provider_id": provider_id,
            "action_id": action_id,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _require(condition: bool, message: str) -> str:
    if not condition:
        raise AssertionError(message)
    return message


def _reference_extensions(provider_id: str, extension_id: str, *, mutate: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    action_id = "extension.controlnet" if extension_id == "image.controlnet" else "extension.ip_adapter"
    unit_id = "control-1" if extension_id == "image.controlnet" else "ip-1"
    contract = build_preview_reference_handoff(
        _source(),
        action_id=action_id,
        target_extension=extension_id,
        target_unit_id=unit_id,
        target_unit_index=0,
        profile_id=f"{provider_id}-selected",
        provider_id=provider_id,
        execution_mode=("forge_integrated_controlnet" if extension_id == "image.controlnet" else "forge_integrated_ip_adapter") if provider_id == "forge" else ("comfy_controlnet" if extension_id == "image.controlnet" else "comfy_ip_adapter"),
    )
    if mutate:
        mutate(contract)
    bucket_name = "control_images" if extension_id == "image.controlnet" else "reference_images"
    return {
        "payloads": {
            extension_id: {
                "enabled": True,
                "inputs": {"units": [{"uid": unit_id, "enabled": True}]},
                "assets": {bucket_name: {unit_id: [{"ref": _source()["path"]}]}},
                "metadata": {"preview_reference_handoff": contract},
            }
        }
    }


def _lineage_chain() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_a = {**_source("output-a", "job-a"), "lineage": {"root_output_id": "output-a", "root_job_id": "job-a", "depth": 0, "ancestor_output_ids": []}}
    contract_b = build_derived_action_contract(
        source_a,
        action_id="extension.high_res_lab",
        profile_id="forge-selected",
        provider_id="forge",
        dispatch_type="run_forge_native_hires",
        execution_mode="forge_native_txt2img_upscale",
    )
    record_b = build_image_output_record(
        mode="generate",
        job_id="job-b",
        provider_id="forge",
        backend_profile_id="forge-selected",
        params={"_neo_derived_action": contract_b},
        output_files=[{"file_id": "output-b", "output_id": "output-b", "filename": "output-b.png"}],
        active_file="output-b",
        result_id="result-b",
    )
    lineage_b = build_output_lineage_metadata(record_b)
    source_b = {**_source("output-b", "job-b"), "lineage": lineage_b, "root_output_id": lineage_b["root_output_id"], "root_job_id": lineage_b["root_job_id"], "lineage_depth": lineage_b["depth"], "ancestor_output_ids": lineage_b["ancestor_output_ids"]}
    contract_c = build_derived_action_contract(
        source_b,
        action_id="extension.adetailer",
        profile_id="forge-selected",
        provider_id="forge",
        dispatch_type="run_provider_img2img_derived",
        execution_mode="forge_adetailer_finish",
    )
    record_c = build_image_output_record(
        mode="img2img",
        job_id="job-c",
        provider_id="forge",
        backend_profile_id="forge-selected",
        params={"_neo_derived_action": contract_c},
        output_files=[{"file_id": "output-c", "output_id": "output-c", "filename": "output-c.png"}],
        active_file="output-c",
        result_id="result-c",
    )
    lineage_c = build_output_lineage_metadata(record_c)
    return lineage_b, lineage_c, contract_c


def run_provider_action_regression_matrix() -> dict[str, Any]:
    """Run the deterministic Phase 13 matrix and return a JSON-safe report."""

    cases: list[dict[str, Any]] = []
    registry = preview_action_definition_registry_payload()
    actions = {item["id"]: item for item in registry["actions"]}

    cases.append(_case("registry.authority", "registry", lambda: _require(registry["authority"] == "neo_app.image.preview_actions", "canonical Python registry authority retained")))
    cases.append(_case("registry.inventory", "registry", lambda: _require(registry["action_count"] == 13 and registry["group_order"] == ["source", "reference", "layerdiffuse", "finish"], "13 actions and canonical group order retained")))
    cases.append(_case("registry.unique_ids", "registry", lambda: _require(len(actions) == registry["action_count"], "all canonical action IDs are unique")))
    cases.append(_case("registry.no_auto_run", "registry", lambda: _require(all(item.get("auto_run_default") is False for item in registry["actions"]), "all actions require explicit user execution")))

    expected_routes = {
        "forge": {
            "core.img2img": (True, "stage_source_mode", "forge_img2img"),
            "core.inpaint": (True, "stage_source_mode", "forge_inpaint"),
            "core.outpaint": (True, "stage_source_mode", "forge_outpaint"),
            "extension.controlnet": (True, "stage_reference", "forge_integrated_controlnet"),
            "extension.ip_adapter": (True, "stage_reference", "forge_integrated_ip_adapter"),
            "extension.layerdiffuse.source": (False, "unavailable", "none"),
            "extension.layerdiffuse.background": (False, "unavailable", "none"),
            "extension.layerdiffuse.foreground": (False, "unavailable", "none"),
            "extension.layerdiffuse.replace_target": (False, "unavailable", "none"),
            "extension.high_res_lab": (True, "run_forge_native_hires", "forge_native_txt2img_upscale"),
            "extension.adetailer": (True, "run_provider_img2img_derived", "forge_adetailer_finish"),
            "extension.identity_rescue": (True, "run_provider_img2img_derived", "forge_faceid_finish"),
            "extension.image_upscale": (True, "run_provider_extras", "forge_extra_single_image"),
        },
        "comfyui": {
            "core.img2img": (True, "stage_source_mode", "comfy_img2img"),
            "core.inpaint": (True, "stage_source_mode", "comfy_inpaint"),
            "core.outpaint": (True, "stage_source_mode", "comfy_outpaint"),
            "extension.controlnet": (True, "stage_reference", "comfy_controlnet"),
            "extension.ip_adapter": (True, "stage_reference", "comfy_ip_adapter"),
            "extension.layerdiffuse.source": (True, "stage_layer_slot", "comfy_layerdiffuse"),
            "extension.layerdiffuse.background": (True, "stage_layer_slot", "comfy_layerdiffuse"),
            "extension.layerdiffuse.foreground": (True, "stage_layer_slot", "comfy_layerdiffuse"),
            "extension.layerdiffuse.replace_target": (True, "stage_layer_slot", "comfy_layerdiffuse"),
            "extension.high_res_lab": (True, "run_comfy_derived", "comfy_high_res_finish"),
            "extension.adetailer": (True, "run_comfy_derived", "comfy_adetailer_finish"),
            "extension.identity_rescue": (True, "run_comfy_derived", "comfy_faceid_finish"),
            "extension.image_upscale": (True, "run_provider_upscale", "comfy_image_upscale"),
        },
    }
    for provider_id, expectations in expected_routes.items():
        evaluation = {item["id"]: item for item in _evaluation(provider_id)["actions"]}
        for action_id, expected in expectations.items():
            def check(evaluation=evaluation, action_id=action_id, expected=expected) -> str:
                item = evaluation[action_id]
                actual = (bool(item["enabled"]), item["dispatch_type"], item["execution_mode"])
                return _require(actual == expected, f"{action_id} routes as {expected}")
            cases.append(_case(f"evaluation.{provider_id}.{action_id}", "provider_evaluation", check, provider_id=provider_id, action_id=action_id))

    cloud_eval = {item["id"]: item for item in _evaluation("openai")["actions"]}
    for action_id in ("extension.controlnet", "extension.ip_adapter", "extension.high_res_lab", "extension.adetailer", "extension.identity_rescue", "extension.image_upscale"):
        cases.append(_case(
            f"evaluation.cloud.{action_id}",
            "unsupported_provider",
            lambda action_id=action_id: _require(
                cloud_eval[action_id]["enabled"] is False
                and cloud_eval[action_id]["disabled_reason_code"] == "provider_unsupported"
                and cloud_eval[action_id]["automatic_provider_fallback"] is False,
                f"{action_id} fails closed on cloud provider without fallback",
            ),
            provider_id="openai",
            action_id=action_id,
        ))

    for bridge_state, should_enable in (("missing", False), ("legacy", False), ("operation_missing", False), ("current", True)):
        action = {item["id"]: item for item in _evaluation("forge", bridge_state=bridge_state)["actions"]}["extension.high_res_lab"]
        cases.append(_case(
            f"bridge.{bridge_state}",
            "bridge_capability",
            lambda action=action, should_enable=should_enable: _require(
                bool(action["enabled"]) is should_enable
                and (should_enable or action["disabled_reason_code"] == "bridge_missing"),
                f"Forge High-Res enablement follows native Bridge capability ({bridge_state})",
            ),
            provider_id="forge",
            action_id="extension.high_res_lab",
        ))

    for provider_id in ("forge", "comfyui"):
        for mode in ("img2img", "inpaint", "outpaint"):
            contract = build_preview_source_handoff(
                _source(),
                action_id=f"core.{mode}",
                target_mode=mode,
                profile_id=f"{provider_id}-selected",
                provider_id=provider_id,
                execution_mode=f"{provider_id}_{mode}",
            )
            normalized, report = normalize_preview_source_handoff_params(
                {"_neo_preview_action_source": contract, "comfy_source_image_name": "stale", "forge_source_image_b64": "stale"},
                runtime_mode=mode,
                provider_id=provider_id,
                profile_id=f"{provider_id}-selected",
            )
            cases.append(_case(
                f"source.{provider_id}.{mode}",
                "source_handoff",
                lambda contract=contract, normalized=normalized, report=report: _require(
                    report["status"] == "normalized"
                    and report["provider_locked"] is True
                    and contract["auto_run"] is False
                    and contract["automatic_provider_fallback"] is False
                    and "comfy_source_image_name" not in normalized
                    and "forge_source_image_b64" not in normalized,
                    f"{mode} stages on selected {provider_id} profile and clears provider caches",
                ),
                provider_id=provider_id,
                action_id=f"core.{mode}",
            ))

    mismatch_contract = build_preview_source_handoff(_source(), action_id="core.img2img", target_mode="img2img", profile_id="forge-selected", provider_id="forge")
    _, mismatch_report = normalize_preview_source_handoff_params({"_neo_preview_action_source": mismatch_contract}, runtime_mode="img2img", provider_id="comfyui", profile_id="comfyui-selected")
    cases.append(_case("source.cross_provider_block", "source_handoff", lambda: _require(mismatch_report["status"] == "blocked" and "preview_source_provider_mismatch" in mismatch_report["warning_codes"], "Source handoff rejects provider drift")))

    for provider_id in ("forge", "comfyui"):
        for extension_id in ("image.controlnet", "image.ip_adapter"):
            _, report = normalize_preview_reference_handoffs(_reference_extensions(provider_id, extension_id), provider_id=provider_id, profile_id=f"{provider_id}-selected")
            cases.append(_case(
                f"reference.{provider_id}.{extension_id}",
                "reference_handoff",
                lambda report=report: _require(report["status"] == "normalized" and report["selected_profile_only"] is True and report["automatic_provider_fallback"] is False, f"{extension_id} stages on selected provider without overwrite or auto-run"),
                provider_id=provider_id,
                action_id="extension.controlnet" if extension_id == "image.controlnet" else "extension.ip_adapter",
            ))

    for mutation_name, mutation, warning in (
        ("provider_mismatch", lambda c: c.update({"provider_id": "comfyui"}), "preview_reference_provider_mismatch"),
        ("overwrite", lambda c: c.update({"overwrite_existing": True}), "preview_reference_overwrite_forbidden"),
        ("auto_run", lambda c: c.update({"auto_run": True}), "preview_reference_auto_run_forbidden"),
    ):
        _, report = normalize_preview_reference_handoffs(_reference_extensions("forge", "image.controlnet", mutate=mutation), provider_id="forge", profile_id="forge-selected")
        cases.append(_case(f"reference.block.{mutation_name}", "reference_handoff", lambda report=report, warning=warning: _require(report["status"] == "blocked" and warning in report["warning_codes"], f"Reference handoff blocks {mutation_name}")))

    finish_routes = {
        "forge": {
            "extension.high_res_lab": ("run_forge_native_hires", "forge_native_txt2img_upscale", "txt2img", False),
            "extension.adetailer": ("run_provider_img2img_derived", "forge_adetailer_finish", "img2img", False),
            "extension.identity_rescue": ("run_provider_img2img_derived", "forge_faceid_finish", "img2img", False),
            "extension.image_upscale": ("run_provider_extras", "forge_extra_single_image", "image_upscale_finish", True),
        },
        "comfyui": {
            "extension.high_res_lab": ("run_comfy_derived", "comfy_high_res_finish", "img2img", False),
            "extension.adetailer": ("run_comfy_derived", "comfy_adetailer_finish", "img2img", False),
            "extension.identity_rescue": ("run_comfy_derived", "comfy_faceid_finish", "img2img", False),
            "extension.image_upscale": ("run_provider_upscale", "comfy_image_upscale", "image_upscale_finish", True),
        },
    }
    for provider_id, routes in finish_routes.items():
        for action_id, (dispatch, execution, runtime_mode, upscale) in routes.items():
            contract = build_derived_action_contract(
                _source(),
                action_id=action_id,
                profile_id=f"{provider_id}-selected",
                provider_id=provider_id,
                dispatch_type=dispatch,
                execution_mode=execution,
            )
            _, report = normalize_preview_finish_params(
                {"_neo_derived_action": contract},
                runtime_mode=runtime_mode,
                provider_id=provider_id,
                profile_id=f"{provider_id}-selected",
                allow_upscale_dispatch=upscale,
            )
            cases.append(_case(
                f"finish.{provider_id}.{action_id}",
                "finish_dispatch",
                lambda contract=contract, report=report: _require(report["status"] == "validated" and report["provider_locked"] is True and contract["automatic_provider_fallback"] is False, f"{action_id} validates on selected {provider_id} route"),
                provider_id=provider_id,
                action_id=action_id,
            ))

    blocked_finish = build_derived_action_contract(_source(), action_id="extension.adetailer", profile_id="forge-selected", provider_id="forge", dispatch_type="run_provider_img2img_derived", execution_mode="forge_adetailer_finish")
    blocked_finish["automatic_provider_fallback"] = True
    _, blocked_finish_report = normalize_preview_finish_params({"_neo_derived_action": blocked_finish}, runtime_mode="img2img", provider_id="forge", profile_id="forge-selected")
    cases.append(_case("finish.fallback_forbidden", "finish_dispatch", lambda: _require(blocked_finish_report["status"] == "blocked" and "derived_action_automatic_fallback_forbidden" in blocked_finish_report["warning_codes"], "Finish contract rejects automatic fallback")))

    clean_params, replay_report = sanitize_replay_params({"steps": 24, "_neo_derived_action": {"schema": "neo.image.derived_action.v2"}, "forge_source_image_b64": "runtime", "comfy_source_image_name": "runtime", "source_image": "source.png", "mask_image": "mask.png"}, mode="txt2img")
    cases.append(_case("replay.params_sanitized", "replay", lambda: _require(clean_params == {"steps": 24} and replay_report["temporary_action_state_restored"] is False, "Replay strips action contracts, provider caches, source, and mask for txt2img")))

    replay_extensions, extension_report = sanitize_replay_extensions({"payloads": {"image.controlnet": {"enabled": True, "params": {"strength": 0.8, "preview_reference_handoff": {"profile_id": "forge-selected"}, "forge_control_image_b64": "runtime"}, "metadata": {}}}})
    replay_control = replay_extensions["payloads"]["image.controlnet"]
    cases.append(_case("replay.extensions_revalidation", "replay", lambda: _require(replay_control["enabled"] is False and replay_control["metadata"]["revalidation_required"] is True and extension_report["temporary_handoff_restored"] is False, "Replay keeps canonical extension settings disabled pending provider revalidation")))

    owned = {"_neo_provider_state_owner": {"provider_id": "forge", "profile_id": "forge-a"}, "forge_source_image_b64": "runtime", "comfy_source_image_name": "runtime"}
    clean_owned, owner_report = clear_cross_provider_upload_caches(owned, provider_id="comfyui", profile_id="comfy-b")
    cases.append(_case("replay.provider_change_cache_clear", "replay", lambda: _require(owner_report["provider_changed"] is True and "forge_source_image_b64" not in clean_owned and "comfy_source_image_name" not in clean_owned, "Provider/profile change clears all provider-owned upload aliases")))

    lineage_b, lineage_c, contract_c = _lineage_chain()
    cases.append(_case("lineage.first_finish", "lineage", lambda: _require(lineage_b["parent_output_id"] == "output-a" and lineage_b["root_output_id"] == "output-a" and lineage_b["depth"] == 1, "First Finish pass links to base output")))
    cases.append(_case("lineage.repeated_finish", "lineage", lambda: _require(lineage_c["parent_output_id"] == "output-b" and lineage_c["root_output_id"] == "output-a" and lineage_c["depth"] == 2 and lineage_c["ancestor_output_ids"] == ["output-a", "output-b"], "Repeated Finish pass preserves immediate parent, root, depth, and ordered ancestors")))
    cases.append(_case("lineage.contract_parent", "lineage", lambda: _require(contract_c["parent_output_id"] == "output-b" and contract_c["root_output_id"] == "output-a", "Derived contract uses selected output as immediate parent")))

    js = (PROJECT_ROOT / "neo_app" / "static" / "js" / "neo.js").read_text(encoding="utf-8")
    finish_start = js.index("async function previewActionDispatchFinish")
    finish_end = js.index("function outputPostFixActionLabel", finish_start)
    finish_block = js[finish_start:finish_end]
    failed_start = js.index("if (result.status === 'failed')", js.index("async function pollImageGeneration"))
    failed_end = js.index("if (state.activeImageJob?.job_id === jobId)", failed_start)
    failed_block = js[failed_start:failed_end]
    cases.append(_case("frontend.shared_toolbar", "frontend_parity", lambda: _require("renderPreviewActionToolbar(liveSourceContext, { placement: 'live_preview' })" in js and "renderPreviewActionToolbar(buildInspectorPreviewActionSource" in js, "Preview and Output Inspector share one toolbar renderer")))
    cases.append(_case("frontend.shared_finish_dispatch", "frontend_parity", lambda: _require("return await previewActionDispatchFinish(actionId, source, { placement: 'output_inspector' })" in js, "Preview and Output Inspector share one Finish dispatcher")))
    cases.append(_case("frontend.no_finish_profile_switch", "no_fallback", lambda: _require("setSelectedBackendProfileForSurface" not in finish_block and "imagePostOutputComfyBridgeProfile" not in js, "Finish dispatcher never silently switches provider profiles")))
    cases.append(_case("frontend.cancel_cleanup", "lifecycle", lambda: _require("imageFinalizeActionLifecycle('generation_cancelled'" in js and "state.activeImageJob = null" in js[js.index("async function stopImageGeneration"):js.index("async function togglePauseImageGeneration")], "Cancellation clears job and transient action state")))
    cases.append(_case("frontend.failure_cleanup", "lifecycle", lambda: _require("state.activeImageJob = null" in failed_block and "closeImageProgressSocket()" in failed_block and "imageFinalizeActionLifecycle('generation_failed'" in failed_block, "Failed polling clears active job, live preview, watchdog, and action state")))
    cases.append(_case("frontend.no_legacy_comfy_bridge", "no_fallback", lambda: _require("neo.image.post_output_comfy_bridge.v1" not in js and "previewActionRunFinishPass" not in js, "Legacy Comfy-only Finish bridge remains removed")))

    counts = Counter(item["status"] for item in cases)
    category_counts: dict[str, dict[str, int]] = {}
    for category in sorted({item["category"] for item in cases}):
        group = [item for item in cases if item["category"] == category]
        category_counts[category] = {
            "case_count": len(group),
            "passed": sum(item["status"] == "passed" for item in group),
            "failed": sum(item["status"] == "failed" for item in group),
        }
    return {
        "schema": SCHEMA_ID,
        "status": "passed" if counts.get("failed", 0) == 0 else "failed",
        "case_count": len(cases),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "selected_profile_only": True,
        "automatic_provider_fallback": False,
        "physical_backend_execution": False,
        "categories": category_counts,
        "cases": cases,
    }
