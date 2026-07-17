from __future__ import annotations

from copy import deepcopy
from importlib import util as importlib_util
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]

CFG_FIX_EXTENSION_ID = "cfg_fix_dynamic_thresholding"
LORA_STACK_EXTENSION_ID = "lora_stack"
EMBEDDINGS_TI_EXTENSION_ID = "embeddings_ti"
CONTROLNET_EXTENSION_ID = "image.controlnet"
IP_ADAPTER_EXTENSION_ID = "image.ip_adapter"
SCENE_DIRECTOR_EXTENSION_ID = "image.scene_director"
HIGH_RES_LAB_EXTENSION_ID = "image.high_res_lab"
ADETAILER_EXTENSION_ID = "image.adetailer"
LAYERDIFFUSE_EXTENSION_ID = "image.layerdiffuse"
LAYERDIFFUSE_NODE_CLASSES = {
    "LayeredDiffusionApply",
    "LayeredDiffusionCondApply",
    "LayeredDiffusionDecode",
    "LayeredDiffusionDecodeRGBA",
}
LAYERDIFFUSE_TEMPLATE_MARKERS = {
    "foreground_on_background_sdxl.json",
    "background_aware_blend_sdxl.json",
    "extract_foreground_sdxl.json",
    "transparent_asset_sdxl.json",
}
STYLE_STACK_EXTENSION_ID = "style_stack"
WILDCARDS_EXTENSION_ID = "wildcards"
PROMPT_ONLY_EXTENSION_IDS = {STYLE_STACK_EXTENSION_ID, WILDCARDS_EXTENSION_ID}
GRAPH_MUTATING_EXTENSION_IDS = {
    CFG_FIX_EXTENSION_ID,
    LORA_STACK_EXTENSION_ID,
    CONTROLNET_EXTENSION_ID,
    IP_ADAPTER_EXTENSION_ID,
    SCENE_DIRECTOR_EXTENSION_ID,
    HIGH_RES_LAB_EXTENSION_ID,
    ADETAILER_EXTENSION_ID,
    LAYERDIFFUSE_EXTENSION_ID,
}


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_non_negative_comfy_seed(value: Any, *, fallback: int | None = None) -> int:
    """Return a ComfyUI-safe KSampler seed.

    ComfyUI KSampler rejects the UI random sentinel ``-1``. Neo core normally
    resolves random seeds before provider compilation, but external workflow
    replacement adapters can accidentally bypass that if they only read their
    extension payload. Keep the guard here so external graph patches never emit
    invalid sampler seeds.
    """
    try:
        seed = int(value)
    except Exception:
        seed = int(fallback if fallback is not None else 0)
    if seed < 0:
        if fallback is not None:
            try:
                fallback_seed = int(fallback)
                if fallback_seed >= 0:
                    return fallback_seed
            except Exception:
                pass
        return int(time.time() * 1000) % 2147483647
    return seed


def _first_present(*values: Any, fallback: Any = None) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return fallback


def _extension_payload_enabled(extensions: Any, extension_id: str) -> bool:
    block = _extension_payload_block(extensions, extension_id)
    return bool(block.get("enabled"))




def _route_actual_params(route: dict[str, Any] | None) -> dict[str, Any]:
    route = route or {}
    actual = route.get("actual_params") if isinstance(route.get("actual_params"), dict) else None
    params = route.get("params") if isinstance(route.get("params"), dict) else None
    return dict(actual or params or {})


def _ui_submit_extension_state(route: dict[str, Any] | None) -> dict[str, Any]:
    params = _route_actual_params(route)
    state = params.get("_neo_extension_state") if isinstance(params.get("_neo_extension_state"), dict) else {}
    extensions = state.get("extensions") if isinstance(state.get("extensions"), dict) else {}
    return extensions


def _ui_state_says_disabled(route: dict[str, Any] | None, extension_id: str) -> bool:
    extensions = _ui_submit_extension_state(route)
    if extension_id not in extensions:
        return False
    block = extensions.get(extension_id)
    if not isinstance(block, dict):
        return False
    return block.get("enabled") is False or block.get("ui_enabled") is False



def _ui_state_block(route: dict[str, Any] | None, extension_id: str) -> dict[str, Any]:
    extensions = _ui_submit_extension_state(route)
    block = extensions.get(extension_id) if isinstance(extensions, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def _layerdiffuse_execution_requested(extensions: Any, route: dict[str, Any] | None) -> bool:
    """Return True only for a deliberate LayerDiffuse executable route.

    LayerDiffuse is a workflow-replacement extension with an 8-channel/model
    contract. A stale payload is not enough to execute it. Current submit state
    must not mark it disabled, and if the UI snapshot is available it must also
    say the workflow is applied for this run.
    """
    if not _extension_payload_enabled(extensions, LAYERDIFFUSE_EXTENSION_ID):
        return False
    if _ui_state_says_disabled(route, LAYERDIFFUSE_EXTENSION_ID):
        return False
    state = _ui_state_block(route, LAYERDIFFUSE_EXTENSION_ID)
    if state and state.get("workflow_applied") is False:
        return False
    return True


def _purge_layerdiffuse_payloads_for_non_route(extensions: Any, route: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    source = deepcopy(extensions) if isinstance(extensions, dict) else {}
    report = {
        "schema": "neo.image.layerdiffuse_contamination_report.v1",
        "layerdiffuse_requested": _layerdiffuse_execution_requested(source, route),
        "payload_present_before_filter": bool(_extension_payload_block(source, LAYERDIFFUSE_EXTENSION_ID)),
        "payload_present_after_filter": False,
        "purged": False,
        "reason": "layerdiffuse_route_active",
        "illegal_node_classes": sorted(LAYERDIFFUSE_NODE_CLASSES),
    }
    validation: list[dict[str, Any]] = []
    if report["layerdiffuse_requested"]:
        report["payload_present_after_filter"] = bool(_extension_payload_block(source, LAYERDIFFUSE_EXTENSION_ID))
        return source, validation, report
    had_payload = bool(_extension_payload_block(source, LAYERDIFFUSE_EXTENSION_ID))
    _strip_extension_block(source, LAYERDIFFUSE_EXTENSION_ID)
    report["payload_present_after_filter"] = bool(_extension_payload_block(source, LAYERDIFFUSE_EXTENSION_ID))
    report["purged"] = had_payload
    report["reason"] = "not_explicit_layerdiffuse_route"
    if had_payload:
        if _ui_state_says_disabled(route, LAYERDIFFUSE_EXTENSION_ID):
            validation.append({
                "extension_id": LAYERDIFFUSE_EXTENSION_ID,
                "ok": True,
                "blocked": False,
                "level": "info",
                "message": "Stale LayerDiffuse payload stripped because the current Image UI submit state has LayerDiffuse disabled.",
                "reason": "current_ui_state_disabled",
                "source": "neo.image.extension_submit_state.v1",
            })
        validation.append({
            "extension_id": LAYERDIFFUSE_EXTENSION_ID,
            "ok": True,
            "blocked": False,
            "level": "info",
            "message": "LayerDiffuse payload was purged because this compile is not an explicit LayerDiffuse route.",
            "reason": "layerdiffuse_non_route_purge",
            "source": "neo.image.layerdiffuse_contamination_report.v1",
        })
    return source, validation, report


def _workflow_layerdiffuse_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if not isinstance(workflow, dict):
        return nodes
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if class_type in LAYERDIFFUSE_NODE_CLASSES:
            nodes.append({"node_id": str(node_id), "class_type": class_type})
    return nodes


def _workflow_layerdiffuse_template_refs(patches: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        template = str(patch.get("template") or "").strip()
        if template and (template in LAYERDIFFUSE_TEMPLATE_MARKERS or "layerdiffuse" in template.lower()):
            refs.append(template)
    return refs

def _strip_extension_block(container: dict[str, Any], extension_id: str) -> None:
    container.pop(extension_id, None)
    for key in ("payloads", "replay_payloads", "extensions", "memory_events", "assistant_summaries"):
        nested = container.get(key)
        if isinstance(nested, dict):
            nested.pop(extension_id, None)
    used = container.get("used")
    if isinstance(used, list):
        container["used"] = [item for item in used if not (isinstance(item, dict) and item.get("extension_id") == extension_id)]
    patches = container.get("workflow_patches")
    if isinstance(patches, list):
        container["workflow_patches"] = [item for item in patches if not (isinstance(item, dict) and item.get("extension_id") == extension_id)]


def _sanitize_extensions_for_submit_state(extensions: Any, route: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Strip stale graph-mutating extension payloads disabled in the current UI snapshot.

    Replay Draft, Post-Fix staging, and preview-action handoffs can preserve old
    payload blocks for review. The provider compiler must not execute those
    blocks unless the current Image submit state explicitly says the extension is
    enabled for this run.
    """
    source, validation, _ = _purge_layerdiffuse_payloads_for_non_route(extensions, route)
    ui_extensions = _ui_submit_extension_state(route)
    if not ui_extensions:
        return source, validation
    for extension_id in GRAPH_MUTATING_EXTENSION_IDS:
        if not _ui_state_says_disabled(route, extension_id):
            continue
        had_payload = bool(_extension_payload_block(source, extension_id))
        _strip_extension_block(source, extension_id)
        if had_payload:
            validation.append({
                "extension_id": extension_id,
                "ok": True,
                "blocked": False,
                "level": "info",
                "message": "Stale extension payload stripped because the current Image UI submit state has this extension disabled.",
                "reason": "current_ui_state_disabled",
                "source": "neo.image.extension_submit_state.v1",
            })
    return source, validation


def _workflow_contains_extension_nodes(workflow: dict[str, Any], node_names: set[str]) -> bool:
    if not isinstance(workflow, dict):
        return False
    for node in workflow.values():
        if isinstance(node, dict) and str(node.get("class_type") or "") in node_names:
            return True
    return False



def _scene_director_has_regional_identity(extensions: Any) -> bool:
    block = _extension_payload_block(extensions, SCENE_DIRECTOR_EXTENSION_ID)
    if not isinstance(block, dict):
        return False
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    identity_units = assets.get("identity_units") if isinstance(assets.get("identity_units"), list) else []
    if any(isinstance(unit, dict) and (unit.get("image_name") or unit.get("image_names") or unit.get("reference_image")) for unit in identity_units):
        return True
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    for region in regions:
        if not isinstance(region, dict):
            continue
        identity = region.get("identity") if isinstance(region.get("identity"), dict) else {}
        if identity.get("image_name") or identity.get("image_names") or identity.get("reference_image"):
            return True
    return False


def _apply_cfg_fix_patch_to_graph(
    graph: dict[str, Any],
    *,
    extensions: Any,
    route: dict[str, Any] | None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None,
    cfg: float | int | str,
    model_output_ref: list[Any],
    sampler_node_id: str | int,
    sampler_model_input: str,
) -> tuple[dict[str, Any], list[Any], dict[str, Any], dict[str, Any]]:
    from neo_extensions.built_in.cfg_fix_dynamic_thresholding.backend.workflow_patch import apply_cfg_fix_dynamic_thresholding_patch

    result = apply_cfg_fix_dynamic_thresholding_patch(
        graph,
        payload=extensions,
        route=route,
        available_nodes=available_nodes,
        cfg=cfg,
        model_ref=model_output_ref,
        sampler_node_id=sampler_node_id,
        sampler_model_input=sampler_model_input,
    )
    next_graph = result["workflow"]
    next_model_ref = result["model_ref"]
    patch = result.get("workflow_patch") or {}
    if isinstance(patch, dict) and patch.get("node_class") and not patch.get("node"):
        patch = {**patch, "node": patch.get("node_class")}
    validation_result = result.get("validation") or {}
    return next_graph, next_model_ref, patch, validation_result

def prompt_only_workflow_extension_ids() -> set[str]:
    """Return extensions that must never request provider workflow patches."""
    return set(PROMPT_ONLY_EXTENSION_IDS)


def is_prompt_only_workflow_extension(extension_id: Any) -> bool:
    """Return True when an extension is handled by prompt merge, not graph patching."""
    return str(extension_id or "").strip() in PROMPT_ONLY_EXTENSION_IDS

def _extension_payload_block(extensions: Any, extension_id: str) -> dict[str, Any]:
    if not isinstance(extensions, dict):
        return {}
    if extension_id in extensions and isinstance(extensions.get(extension_id), dict):
        return extensions.get(extension_id) or {}
    payloads = extensions.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(extension_id), dict):
        return payloads.get(extension_id) or {}
    nested = extensions.get("extensions")
    if isinstance(nested, dict) and isinstance(nested.get(extension_id), dict):
        return nested.get(extension_id) or {}
    return {}




def _load_layerdiffuse_adapter_module():
    """Load the external LayerDiffuse adapter from installed/data extension folders.

    The extension folder uses the canonical id `image.layerdiffuse`, which is
    not a normal Python package name. Importing by file path keeps the external
    package self-contained and avoids teaching users to rename extension folders.
    """
    candidates = [
        ROOT_DIR / "neo_extensions" / "built_in" / "image.layerdiffuse" / "backend" / "adapter.py",
        ROOT_DIR / "neo_extensions" / "installed" / "image.layerdiffuse" / "backend" / "adapter.py",
        ROOT_DIR / "neo_data" / "extensions" / "image" / "image.layerdiffuse" / "backend" / "adapter.py",
    ]
    for adapter_path in candidates:
        if not adapter_path.exists():
            continue
        spec = importlib_util.spec_from_file_location("neo_external_image_layerdiffuse_adapter", adapter_path)
        if spec and spec.loader:
            module = importlib_util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            module.__dict__.setdefault("__neo_extension_root__", adapter_path.parents[1])
            return module, adapter_path.parents[1]
    return None, None


def _layerdiffuse_context_from_route(route: dict[str, Any] | None, block: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or {}
    block = block or {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    route_params = _as_mapping(route.get("actual_params")) or _as_mapping(route.get("params"))
    route_metadata = _as_mapping(route.get("metadata"))
    workflow_mode = route.get("workflow_mode") or route.get("mode") or "txt2img"
    workflow_mode = "txt2img" if workflow_mode == "generate" else workflow_mode
    requested_seed = _first_present(
        params.get("requested_seed"),
        route_params.get("requested_seed"),
        metadata.get("requested_seed"),
        route_metadata.get("requested_seed"),
        fallback=None,
    )
    raw_seed = _first_present(
        params.get("actual_seed"),
        params.get("seed"),
        route_params.get("actual_seed"),
        route_params.get("seed"),
        metadata.get("actual_seed"),
        metadata.get("seed"),
        route_metadata.get("actual_seed"),
        route_metadata.get("seed"),
        fallback=-1,
    )
    seed = _coerce_non_negative_comfy_seed(raw_seed, fallback=route_params.get("actual_seed"))
    return {
        "workflow": workflow_mode,
        "workflow_type": workflow_mode,
        "family": route.get("family") or route.get("model_family") or route_metadata.get("family") or metadata.get("family") or "sdxl",
        "model_family": route.get("family") or route.get("model_family") or route_metadata.get("model_family") or metadata.get("model_family") or metadata.get("family") or "sdxl",
        "loader": route.get("loader") or route_metadata.get("loader") or metadata.get("loader") or "checkpoint",
        # Neo core owns final prompt conditioning. External workflow replacement
        # must consume the resolved provider prompt from route_params first, not
        # stale client-side extension preview fields. Otherwise LayerDiffuse can
        # execute a valid graph with an old/default CLIPTextEncode prompt.
        "prompt": route_params.get("effective_positive") or route_params.get("positive_prompt") or route_params.get("prompt") or inputs.get("source_positive") or inputs.get("prompt") or metadata.get("positive_prompt") or "transparent asset",
        "positive_prompt": route_params.get("effective_positive") or route_params.get("positive_prompt") or route_params.get("prompt") or inputs.get("source_positive") or inputs.get("prompt") or metadata.get("positive_prompt") or "transparent asset",
        "negative_prompt": route_params.get("effective_negative") or route_params.get("negative_prompt") or inputs.get("source_negative") or metadata.get("negative_prompt") or "",
        "batch_size": _first_present(params.get("batch_size"), route_params.get("batch_size"), metadata.get("batch_size"), fallback=1),
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed if requested_seed is not None else raw_seed,
        "seed_source": "resolved_by_neo_core" if route_params.get("actual_seed") not in (None, "") else "external_hook_safe_fallback",
        "steps": _first_present(params.get("steps"), route_params.get("steps"), metadata.get("steps"), fallback=30),
        "cfg": _first_present(params.get("cfg"), route_params.get("cfg"), metadata.get("cfg"), fallback=7),
        "sampler_name": _first_present(params.get("sampler_name"), route_params.get("sampler"), route_params.get("sampler_name"), metadata.get("sampler"), fallback="euler"),
        "scheduler": _first_present(params.get("scheduler"), route_params.get("scheduler"), metadata.get("scheduler"), fallback="normal"),
        "denoise": _first_present(params.get("denoise"), route_params.get("denoise"), metadata.get("denoise"), fallback=1.0),
        "checkpoint": _first_present(params.get("checkpoint"), route_params.get("model"), route_params.get("checkpoint"), metadata.get("checkpoint"), fallback="provider_default"),
        "width": _first_present(params.get("width"), route_params.get("width"), metadata.get("width"), fallback=1024),
        "height": _first_present(params.get("height"), route_params.get("height"), metadata.get("height"), fallback=1024),
        "layerdiffuse_weight": _first_present(params.get("layerdiffuse_weight"), metadata.get("layerdiffuse_weight"), fallback=1.0),
        "sub_batch_size": _first_present(params.get("sub_batch_size"), metadata.get("sub_batch_size"), fallback=16),
    }


def _layerdiffuse_raw_state_from_block(block: dict[str, Any] | None) -> dict[str, Any]:
    block = block or {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    raw = {
        "enabled": bool(block.get("enabled")),
        "mode": block.get("mode") or inputs.get("mode") or params.get("mode") or "transparent_asset",
        "source_type": inputs.get("source_type") or block.get("source_type") or "prompt",
        "source_image_id": inputs.get("source_image_id") or block.get("source_image_id"),
        "background_image_id": inputs.get("background_image_id") or block.get("background_image_id"),
        "foreground_image_id": inputs.get("foreground_image_id") or block.get("foreground_image_id"),
        "blended_image_id": inputs.get("blended_image_id") or block.get("blended_image_id"),
        "decode_mode": params.get("decode_mode") or block.get("decode_mode") or "rgba",
        "output_policy": params.get("output_policy") or block.get("output_policy") or "new_run",
        "replace_target_id": inputs.get("replace_target_id") or block.get("replace_target_id"),
        "replace_confirmed": bool(params.get("replace_confirmed") or block.get("replace_confirmed")),
        "save_rgba": params.get("save_rgba", block.get("save_rgba", True)),
        "save_rgb": params.get("save_rgb", block.get("save_rgb", False)),
        "save_alpha": params.get("save_alpha", block.get("save_alpha", True)),
        "save_metadata": params.get("save_metadata", block.get("save_metadata", True)),
        "compatibility_mode": params.get("compatibility_mode") or block.get("compatibility_mode") or "auto",
        "sd_version": params.get("sd_version") or block.get("sd_version") or "auto",
        "workflow_variant": params.get("workflow_variant") or block.get("workflow_variant") or "auto",
        "layerdiffuse_weight": params.get("layerdiffuse_weight", block.get("layerdiffuse_weight", 1.0)),
        "sub_batch_size": params.get("sub_batch_size", block.get("sub_batch_size", 16)),
    }
    return raw



def _sanitize_layerdiffuse_executable_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Remove sampler-only inputs from LayerDiffuse apply nodes.

    This is a defensive bridge guard for external workflow replacement. The
    upstream ComfyUI-layerdiffuse foreground node accepts only model/config/weight.
    If a later Neo hook accidentally targets the pre-replacement sampler id, the
    invalid positive/negative/latent/sampler inputs must not be forwarded to the
    LayeredDiffusionFG function.
    """
    clean_graph = deepcopy(graph or {})
    allowed_apply_inputs = {"model", "config", "weight"}
    for node in clean_graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "LayeredDiffusionApply":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        node["inputs"] = {k: v for k, v in inputs.items() if k in allowed_apply_inputs}
    return clean_graph



def _sync_layerdiffuse_prompt_nodes(graph: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Force Neo's resolved positive/negative prompts into LayerDiffuse CLIP nodes.

    LayerDiffuse replaces Neo's base workflow, so the extension graph must be
    explicitly rehydrated with the same final prompt strings that the provider
    would have sent to the base CLIPTextEncode nodes. This prevents stale
    template/default prompts from generating unrelated subjects.
    """
    clean_graph = deepcopy(graph or {})
    positive = str(context.get("positive_prompt") or context.get("prompt") or "").strip()
    negative = str(context.get("negative_prompt") or "").strip()
    for node_id, text in (("2", positive), ("3", negative)):
        node = clean_graph.get(node_id)
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            continue
        inputs = node.setdefault("inputs", {})
        if isinstance(inputs, dict):
            inputs["text"] = text
    return clean_graph

def _build_layerdiffuse_metadata(plan: dict[str, Any], patch: dict[str, Any], route: dict[str, Any] | None) -> dict[str, Any]:
    entry = plan.get("payload_entry") if isinstance(plan.get("payload_entry"), dict) else {}
    raw = plan.get("raw_state") or entry.get("raw_state") or {}
    effective = plan.get("effective_state") or entry.get("effective_state") or {}
    validation = effective.get("validation") if isinstance(effective.get("validation"), dict) else {}
    active = bool(effective.get("effective_enabled") or patch.get("enabled"))
    summary_mode = effective.get("mode") or raw.get("mode") or "transparent_asset"
    template = effective.get("workflow_template") or patch.get("template")
    payload = {
        "enabled": bool(raw.get("enabled")),
        "version": 1,
        "inputs": {
            "mode": raw.get("mode"),
            "source_type": raw.get("source_type"),
            "source_image_id": raw.get("source_image_id"),
            "background_image_id": raw.get("background_image_id"),
            "foreground_image_id": raw.get("foreground_image_id"),
        },
        "params": {
            "decode_mode": raw.get("decode_mode"),
            "output_policy": raw.get("output_policy"),
            "layerdiffuse_weight": raw.get("layerdiffuse_weight"),
            "sub_batch_size": raw.get("sub_batch_size"),
            "sd_version": raw.get("sd_version"),
            "workflow_variant": raw.get("workflow_variant"),
        },
        "assets": {
            "workflow_template": template,
            "required_nodes": patch.get("required_nodes") or effective.get("required_nodes") or [],
        },
        "metadata": {
            "extension_id": LAYERDIFFUSE_EXTENSION_ID,
            "provider_graph_mutation": True,
            "workflow_patch_strategy": patch.get("strategy"),
            "template_status": patch.get("template_status"),
            "active": active,
            "route": route or {},
        },
    }
    return {
        "used": [{
            "extension_id": LAYERDIFFUSE_EXTENSION_ID,
            "name": "ComfyUI LayerDiffuse",
            "status": "applied" if active else ("blocked" if raw.get("enabled") else "disabled"),
            "mode": summary_mode,
            "workflow_template": template,
        }],
        "payload": payload,
        "validation": [{
            "extension_id": LAYERDIFFUSE_EXTENSION_ID,
            "ok": bool(validation.get("ok", active)),
            "blocked": bool(validation.get("blocked", not active and raw.get("enabled"))),
            "errors": list(validation.get("errors") or []),
            "warnings": list(validation.get("warnings") or []),
            "disabled_reason": validation.get("disabled_reason"),
        }],
        "replay_payload": {
            "enabled": bool(raw.get("enabled")),
            "mode": raw.get("mode"),
            "source_type": raw.get("source_type"),
            "decode_mode": raw.get("decode_mode"),
            "output_policy": raw.get("output_policy"),
            "save_rgba": raw.get("save_rgba"),
            "save_rgb": raw.get("save_rgb"),
            "save_alpha": raw.get("save_alpha"),
            "save_metadata": raw.get("save_metadata"),
            "compatibility_mode": raw.get("compatibility_mode"),
            "sd_version": raw.get("sd_version"),
            "workflow_variant": raw.get("workflow_variant"),
            "layerdiffuse_weight": raw.get("layerdiffuse_weight"),
            "sub_batch_size": raw.get("sub_batch_size"),
        },
        "assistant_summary": f"LayerDiffuse {summary_mode} using {template or 'no template'}.",
        "memory_event": {
            "event_type": "image_layerdiffuse_used",
            "mode": summary_mode,
            "workflow_template": template,
            "active": active,
            "output_policy": raw.get("output_policy"),
        },
    }

def apply_comfy_workflow_extension_patches(
    workflow: dict[str, Any],
    *,
    extensions: Any,
    route: dict[str, Any] | None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None,
    cfg: float | int | str,
    model_ref: list[Any] | tuple[Any, ...] | None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    sampler_model_input: str = "model",
    lora_patch_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply registered Comfy workflow extension patches without provider-specific hardcoding.

    The hook shape is generic so built-in/external extensions can be dispatched
    here without patching random provider compiler files. LoRA Stack uses
    this path for Phase H Comfy LoraLoader graph patching on validated checkpoint routes.
    """
    graph = deepcopy(workflow or {})
    _, _, layerdiffuse_contamination_report = _purge_layerdiffuse_payloads_for_non_route(extensions, route)
    extensions, suppressed_validation = _sanitize_extensions_for_submit_state(extensions, route)
    patches: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = list(suppressed_validation)
    payloads: dict[str, Any] = {}
    used: list[dict[str, Any]] = []
    replay_payloads: dict[str, Any] = {}
    assistant_summaries: dict[str, str] = {}
    memory_events: dict[str, Any] = {}
    model_output_ref = list(model_ref) if isinstance(model_ref, (list, tuple)) else ["1", 0]
    clip_output_ref = list(clip_ref) if isinstance(clip_ref, (list, tuple)) else ["1", 1]
    cfg_fix_applied_pre_scene_director_identity = False
    reference_context: dict[str, Any] = {
        "ip_adapter": {"applied": False, "faceid_active": False, "standard_active": False, "unit_count": 0},
        "controlnet": {"applied": False, "unit_count": 0},
    }

    if _layerdiffuse_execution_requested(extensions, route):
        adapter, extension_root = _load_layerdiffuse_adapter_module()
        block = _extension_payload_block(extensions, LAYERDIFFUSE_EXTENSION_ID)
        if adapter is None or extension_root is None:
            validation.append({
                "extension_id": LAYERDIFFUSE_EXTENSION_ID,
                "ok": False,
                "blocked": True,
                "errors": ["layerdiffuse_adapter_not_found"],
                "warnings": [],
            })
        else:
            raw_state = _layerdiffuse_raw_state_from_block(block)
            context = _layerdiffuse_context_from_route(route, block)
            plan = adapter.build_execution_plan(raw_state, context, extension_root=extension_root)
            patch = plan.get("workflow_patch") or {}
            if isinstance(patch, dict) and patch.get("enabled") and isinstance(patch.get("graph"), dict) and patch.get("graph"):
                graph = patch.get("graph") or graph
                # LayerDiffuse replaces the base checkpoint workflow with its own
                # executable graph. In that graph node 5 is LayeredDiffusionApply
                # and node 6 is the KSampler. Neo core and other workflow hooks
                # usually receive sampler_node_id="5" from the base compiler, so
                # leaving it untouched lets downstream hooks attach sampler-only
                # inputs such as positive/negative to LayeredDiffusionApply.
                # Comfy then calls LayeredDiffusionFG.apply_layered_diffusion(...,
                # positive=...), causing the observed runtime TypeError. Rebase the
                # live hook context immediately after the workflow replacement.
                mode_for_rebase = str((plan.get("effective_state") or {}).get("mode") or raw_state.get("mode") or "transparent_asset")
                if mode_for_rebase in {"foreground_on_background", "background_aware_blend"}:
                    sampler_node_id = "7"
                    model_output_ref = ["6", 0]
                elif mode_for_rebase == "extract_foreground":
                    sampler_node_id = "9"
                    model_output_ref = ["8", 0]
                else:
                    sampler_node_id = "6"
                    model_output_ref = ["5", 0]
                sampler_model_input = "model"
                clip_output_ref = ["1", 1]
                graph = _sanitize_layerdiffuse_executable_graph(graph)
                graph = _sync_layerdiffuse_prompt_nodes(graph, context)
            patch_record = {
                "extension_id": LAYERDIFFUSE_EXTENSION_ID,
                "node_class": "LayeredDiffusionApply",
                "node": "LayeredDiffusionApply",
                "applied": bool(patch.get("enabled") and patch.get("graph")),
                "strategy": patch.get("strategy") or "replace_workflow",
                "template": patch.get("template"),
                "template_status": patch.get("template_status"),
                "provider_graph_mutation": True,
                "required_nodes": patch.get("required_nodes") or [],
                "output_bindings": patch.get("output_bindings") or {},
                "blocked": bool(patch.get("blocked")),
            }
            patches.append(patch_record)
            meta = _build_layerdiffuse_metadata(plan, patch, route)
            used.extend(meta.get("used") or [])
            payloads[LAYERDIFFUSE_EXTENSION_ID] = meta.get("payload") or block
            validation.extend(meta.get("validation") or [])
            replay_payloads[LAYERDIFFUSE_EXTENSION_ID] = meta.get("replay_payload") or raw_state
            if meta.get("assistant_summary"):
                assistant_summaries[LAYERDIFFUSE_EXTENSION_ID] = str(meta.get("assistant_summary"))
            memory_events[LAYERDIFFUSE_EXTENSION_ID] = meta.get("memory_event") or {}


    if _extension_payload_enabled(extensions, LORA_STACK_EXTENSION_ID):
        from neo_extensions.built_in.lora_stack.backend.workflow_patch import apply_lora_stack_patch
        from neo_extensions.built_in.lora_stack.backend.metadata import build_output_extension_metadata

        result = apply_lora_stack_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_node_id,
            sampler_model_input=sampler_model_input,
            lora_patch_profile=lora_patch_profile,
        )
        graph = result["workflow"]
        model_output_ref = result.get("model_ref", model_output_ref)
        clip_output_ref = result.get("clip_ref", clip_output_ref)
        patch = result.get("workflow_patch") or {}
        if isinstance(patch, dict) and patch.get("node_class") and not patch.get("node"):
            patch = {**patch, "node": patch.get("node_class")}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[LORA_STACK_EXTENSION_ID] = block
        lora_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(lora_metadata.get("used") or [])
        replay_payloads.update(lora_metadata.get("replay_payloads") or {})
        if lora_metadata.get("assistant_summary"):
            assistant_summaries[LORA_STACK_EXTENSION_ID] = str(lora_metadata.get("assistant_summary"))
        memory_events.update(lora_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, EMBEDDINGS_TI_EXTENSION_ID):
        from neo_extensions.built_in.embeddings_ti.backend.workflow_patch import apply_embeddings_ti_patch
        from neo_extensions.built_in.embeddings_ti.backend.metadata import build_output_extension_metadata

        result = apply_embeddings_ti_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
        )
        graph = result["workflow"]
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[EMBEDDINGS_TI_EXTENSION_ID] = block
        embeddings_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(embeddings_metadata.get("used") or [])
        replay_payloads.update(embeddings_metadata.get("replay_payloads") or {})
        if embeddings_metadata.get("assistant_summary"):
            assistant_summaries[EMBEDDINGS_TI_EXTENSION_ID] = str(embeddings_metadata.get("assistant_summary"))
        memory_events.update(embeddings_metadata.get("memory_events") or {})



    if (
        _extension_payload_enabled(extensions, CFG_FIX_EXTENSION_ID)
        and _extension_payload_enabled(extensions, SCENE_DIRECTOR_EXTENSION_ID)
        and _scene_director_has_regional_identity(extensions)
    ):
        graph, model_output_ref, patch, validation_result = _apply_cfg_fix_patch_to_graph(
            graph,
            extensions=extensions,
            route=route,
            available_nodes=available_nodes,
            cfg=cfg,
            model_output_ref=model_output_ref,
            sampler_node_id=sampler_node_id,
            sampler_model_input=sampler_model_input,
        )
        if isinstance(patch, dict):
            patch = {
                **patch,
                "patch_order": "before_scene_director_regional_identity",
                "ordering_reason": "Scene Director regional FaceID/IPAdapter must see the CFG Fix model wrapper before identity nodes are inserted.",
            }
        patches.append(patch)
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[CFG_FIX_EXTENSION_ID] = block
        from neo_extensions.built_in.cfg_fix_dynamic_thresholding.backend.metadata import build_output_extension_metadata

        cfg_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(cfg_metadata.get("used") or [])
        replay_payloads.update(cfg_metadata.get("replay_payloads") or {})
        if cfg_metadata.get("assistant_summary"):
            assistant_summaries[CFG_FIX_EXTENSION_ID] = str(cfg_metadata.get("assistant_summary"))
        memory_events.update(cfg_metadata.get("memory_events") or {})
        validation.append({
            "extension_id": CFG_FIX_EXTENSION_ID,
            "ok": True,
            "blocked": False,
            "level": "info",
            "reason": "scene_director_regional_identity_patch_order",
            "message": "CFG Fix was applied before Scene Director regional identity to preserve the previously working FaceID/IPAdapter patch order.",
        })
        cfg_fix_applied_pre_scene_director_identity = True


    if _extension_payload_enabled(extensions, SCENE_DIRECTOR_EXTENSION_ID):
        from neo_extensions.built_in.scene_director.backend.workflow_patch import apply_scene_director_patch
        from neo_extensions.built_in.scene_director.backend.metadata import build_output_extension_metadata

        result = apply_scene_director_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_node_id,
        )
        graph = result["workflow"]
        model_output_ref = result.get("model_ref", model_output_ref)
        clip_output_ref = result.get("clip_ref", clip_output_ref)
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[SCENE_DIRECTOR_EXTENSION_ID] = block
        # Keep the shared extensions payload in sync before downstream owner
        # extensions run. IP Adapter consumes Scene Director identity_units from
        # extensions.payloads.image.scene_director, so it must see the normalized
        # block, not the pre-validation client preview.
        if isinstance(extensions, dict):
            ext_payloads = extensions.get("payloads")
            if isinstance(ext_payloads, dict):
                ext_payloads[SCENE_DIRECTOR_EXTENSION_ID] = block
            else:
                extensions[SCENE_DIRECTOR_EXTENSION_ID] = block
        scene_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(scene_metadata.get("used") or [])
        replay_payloads.update(scene_metadata.get("replay_payloads") or {})
        if scene_metadata.get("assistant_summary"):
            assistant_summaries[SCENE_DIRECTOR_EXTENSION_ID] = str(scene_metadata.get("assistant_summary"))
        memory_events.update(scene_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, IP_ADAPTER_EXTENSION_ID):
        from neo_extensions.built_in.ip_adapter.backend.workflow_patch import apply_ip_adapter_patch
        from neo_extensions.built_in.ip_adapter.backend.metadata import build_output_extension_metadata

        result = apply_ip_adapter_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_output_ref,
            sampler_node_id=sampler_node_id,
            sampler_model_input=sampler_model_input,
        )
        graph = result["workflow"]
        model_output_ref = result.get("model_ref", model_output_ref)
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        active_ip_units = validation_result.get("active_units") if isinstance(validation_result.get("active_units"), list) else []
        active_ip_modes = {str(unit.get("mode") or "standard").strip().lower() for unit in active_ip_units if isinstance(unit, dict)}
        reference_context["ip_adapter"] = {
            "applied": bool(patch.get("applied")),
            "faceid_active": "faceid" in active_ip_modes,
            "standard_active": any(mode != "faceid" for mode in active_ip_modes),
            "unit_count": len(active_ip_units),
        }
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[IP_ADAPTER_EXTENSION_ID] = block
        ip_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(ip_metadata.get("used") or [])
        replay_payloads.update(ip_metadata.get("replay_payloads") or {})
        if ip_metadata.get("assistant_summary"):
            assistant_summaries[IP_ADAPTER_EXTENSION_ID] = str(ip_metadata.get("assistant_summary"))
        memory_events.update(ip_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, CONTROLNET_EXTENSION_ID):
        from neo_extensions.built_in.controlnet.backend.workflow_patch import apply_controlnet_patch
        from neo_extensions.built_in.controlnet.backend.metadata import build_output_extension_metadata

        result = apply_controlnet_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            sampler_node_id=sampler_node_id,
        )
        graph = result["workflow"]
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        active_controlnet_units = validation_result.get("active_units") if isinstance(validation_result.get("active_units"), list) else []
        reference_context["controlnet"] = {
            "applied": bool(patch.get("applied")),
            "unit_count": len(active_controlnet_units) or int(patch.get("controlnet_unit_count") or patch.get("units") or 0),
        }
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[CONTROLNET_EXTENSION_ID] = block
        controlnet_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(controlnet_metadata.get("used") or [])
        replay_payloads.update(controlnet_metadata.get("replay_payloads") or {})
        if controlnet_metadata.get("assistant_summary"):
            assistant_summaries[CONTROLNET_EXTENSION_ID] = str(controlnet_metadata.get("assistant_summary"))
        memory_events.update(controlnet_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, CFG_FIX_EXTENSION_ID) and not cfg_fix_applied_pre_scene_director_identity:
        graph, model_output_ref, patch, validation_result = _apply_cfg_fix_patch_to_graph(
            graph,
            extensions=extensions,
            route=route,
            available_nodes=available_nodes,
            cfg=cfg,
            model_output_ref=model_output_ref,
            sampler_node_id=sampler_node_id,
            sampler_model_input=sampler_model_input,
        )
        patches.append(patch)
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[CFG_FIX_EXTENSION_ID] = block
        from neo_extensions.built_in.cfg_fix_dynamic_thresholding.backend.metadata import build_output_extension_metadata

        cfg_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(cfg_metadata.get("used") or [])
        replay_payloads.update(cfg_metadata.get("replay_payloads") or {})
        if cfg_metadata.get("assistant_summary"):
            assistant_summaries[CFG_FIX_EXTENSION_ID] = str(cfg_metadata.get("assistant_summary"))
        memory_events.update(cfg_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, HIGH_RES_LAB_EXTENSION_ID):
        from neo_extensions.built_in.high_res_lab.backend.workflow_patch import apply_high_res_lab_patch
        from neo_extensions.built_in.high_res_lab.backend.metadata import build_output_extension_metadata

        result = apply_high_res_lab_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_output_ref,
            sampler_node_id=sampler_node_id,
        )
        graph = result["workflow"]
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[HIGH_RES_LAB_EXTENSION_ID] = block
        highres_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        used.extend(highres_metadata.get("used") or [])
        replay_payloads.update(highres_metadata.get("replay_payloads") or {})
        if highres_metadata.get("assistant_summary"):
            assistant_summaries[HIGH_RES_LAB_EXTENSION_ID] = str(highres_metadata.get("assistant_summary"))
        memory_events.update(highres_metadata.get("memory_events") or {})

    if _extension_payload_enabled(extensions, ADETAILER_EXTENSION_ID):
        from neo_extensions.built_in.adetailer.backend.workflow_patch import apply_adetailer_patch
        from neo_extensions.built_in.adetailer.backend.metadata import build_output_extension_metadata

        result = apply_adetailer_patch(
            graph,
            payload=extensions,
            route=route,
            available_nodes=available_nodes,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_node_id,
            reference_context=deepcopy(reference_context),
        )
        graph = result["workflow"]
        patch = result.get("workflow_patch") or {}
        patches.append(patch)
        validation_result = result.get("validation") or {}
        validation.extend(validation_result.get("validation") or [])
        block = validation_result.get("block") or {}
        payloads[ADETAILER_EXTENSION_ID] = block
        adetailer_metadata = build_output_extension_metadata(validation_result, workflow_patch=patch, route=route)
        adetailer_payloads = adetailer_metadata.get("payloads") if isinstance(adetailer_metadata.get("payloads"), dict) else {}
        if isinstance(adetailer_payloads.get(ADETAILER_EXTENSION_ID), dict):
            payloads[ADETAILER_EXTENSION_ID] = adetailer_payloads[ADETAILER_EXTENSION_ID]
        used.extend(adetailer_metadata.get("used") or [])
        replay_payloads.update(adetailer_metadata.get("replay_payloads") or {})
        if adetailer_metadata.get("assistant_summary"):
            assistant_summaries[ADETAILER_EXTENSION_ID] = str(adetailer_metadata.get("assistant_summary"))
        memory_events.update(adetailer_metadata.get("memory_events") or {})


    # User-installed graph adapters share one permission-gated late-finish host.
    # Neo Base does not import or branch on individual external extension ids.
    from neo_app.extensions.external_runtime import apply_external_workflow_patches

    external_result = apply_external_workflow_patches(
        graph,
        extensions=extensions,
        route=route,
        available_nodes=available_nodes,
        model_ref=model_output_ref,
        clip_ref=clip_output_ref,
    )
    graph = external_result.get("workflow") or graph
    model_output_ref = external_result.get("model_ref", model_output_ref)
    clip_output_ref = external_result.get("clip_ref", clip_output_ref)
    patches.extend(external_result.get("workflow_patches") or [])
    validation.extend(external_result.get("validation") or [])
    payloads.update(external_result.get("payloads") or {})
    used.extend(external_result.get("used") or [])
    replay_payloads.update(external_result.get("replay_payloads") or {})
    assistant_summaries.update(external_result.get("assistant_summaries") or {})
    memory_events.update(external_result.get("memory_events") or {})

    layer_nodes = _workflow_layerdiffuse_nodes(graph)
    layer_template_refs = _workflow_layerdiffuse_template_refs(patches)
    layerdiffuse_active = _layerdiffuse_execution_requested(extensions, route)
    layerdiffuse_contamination_report = {
        **layerdiffuse_contamination_report,
        "layerdiffuse_requested": layerdiffuse_active,
        "payload_present_after_filter": bool(_extension_payload_block(extensions, LAYERDIFFUSE_EXTENSION_ID)),
        "nodes_present_in_final_workflow": bool(layer_nodes),
        "final_workflow_nodes": layer_nodes,
        "template_refs_in_patches": layer_template_refs,
        "model_channel_risk": "layerdiffuse_8ch_allowed" if layerdiffuse_active else ("illegal_layerdiffuse_residue" if layer_nodes or layer_template_refs else "normal_4ch"),
        "sampler_node_id": str(sampler_node_id),
    }
    if not layerdiffuse_active and (layer_nodes or layer_template_refs):
        validation.append({
            "extension_id": LAYERDIFFUSE_EXTENSION_ID,
            "ok": False,
            "blocked": True,
            "level": "error",
            "message": "LayerDiffuse residue was present in a non-LayerDiffuse compile. Queue blocked to avoid 8-channel/4-channel latent contamination.",
            "reason": "layerdiffuse_compile_contamination",
            "legacy_reason": "disabled_extension_nodes_present",
            "source": "neo.image.layerdiffuse_contamination_report.v1",
            "nodes": layer_nodes,
            "template_refs": layer_template_refs,
        })

    return {
        "workflow": graph,
        "model_ref": model_output_ref,
        "extensions": {
            "used": used,
            "payloads": payloads,
            "workflow_patches": patches,
            "validation": validation,
            "replay_payloads": replay_payloads,
            "assistant_summaries": assistant_summaries,
            "memory_events": memory_events,
            "contamination_reports": {
                LAYERDIFFUSE_EXTENSION_ID: layerdiffuse_contamination_report,
            },
        },
        "workflow_patches": patches,
        "validation": validation,
    }


def has_comfy_workflow_extension_requests(extensions: Any) -> bool:
    """Return True only for extensions that need Comfy workflow/node patches.

    Prompt-only extensions, including Style Stack and Wildcards, must remain False here. They
    are applied before provider execution by neo_app.image.prompt_extensions.
    """
    from neo_app.extensions.external_runtime import has_external_workflow_patch_request

    return (
        _extension_payload_enabled(extensions, CFG_FIX_EXTENSION_ID)
        or _extension_payload_enabled(extensions, LORA_STACK_EXTENSION_ID)
        or _extension_payload_enabled(extensions, EMBEDDINGS_TI_EXTENSION_ID)
        or _extension_payload_enabled(extensions, CONTROLNET_EXTENSION_ID)
        or _extension_payload_enabled(extensions, IP_ADAPTER_EXTENSION_ID)
        or _extension_payload_enabled(extensions, SCENE_DIRECTOR_EXTENSION_ID)
        or _extension_payload_enabled(extensions, HIGH_RES_LAB_EXTENSION_ID)
        or _extension_payload_enabled(extensions, ADETAILER_EXTENSION_ID)
        or _extension_payload_enabled(extensions, LAYERDIFFUSE_EXTENSION_ID)
        or has_external_workflow_patch_request(extensions)
    )
