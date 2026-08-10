from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Mapping

GENERATION_MODEL_SOURCE = "generation_model"
DEDICATED_CHECKPOINT_SOURCE = "dedicated_checkpoint"
VALID_MODEL_SOURCES = {GENERATION_MODEL_SOURCE, DEDICATED_CHECKPOINT_SOURCE}
VALID_DEDICATED_FAMILIES = {"sdxl", "sd15"}
AUTO_VAE_VALUES = {"", "automatic", "auto", "baked", "checkpoint", "checkpoint_vae", "provider_default"}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_model_source(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "generation": GENERATION_MODEL_SOURCE,
        "current": GENERATION_MODEL_SOURCE,
        "current_model": GENERATION_MODEL_SOURCE,
        "route_model": GENERATION_MODEL_SOURCE,
        "dedicated": DEDICATED_CHECKPOINT_SOURCE,
        "checkpoint": DEDICATED_CHECKPOINT_SOURCE,
        "dedicated_model": DEDICATED_CHECKPOINT_SOURCE,
    }
    resolved = aliases.get(text, text or GENERATION_MODEL_SOURCE)
    return resolved if resolved in VALID_MODEL_SOURCES else GENERATION_MODEL_SOURCE


def normalize_dedicated_family(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "").replace("_", "").replace(" ", "")
    if text in {"sd15", "sd1.5", "stable diffusion 1.5"}:
        return "sd15"
    if text in {"sdxl", "stablediffusionxl"}:
        return "sdxl"
    return "sdxl"


def _safe_comfy_model_name(value: Any) -> str:
    text = _clean_text(value).replace("\\", "/")
    if not text or text.startswith(("/", "//")) or (len(text) > 1 and text[1] == ":"):
        return ""
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    return PurePosixPath(*parts).as_posix()


def _object_info(available_nodes: Any) -> Mapping[str, Any]:
    if not isinstance(available_nodes, Mapping):
        return {}
    nested = available_nodes.get("object_info")
    return nested if isinstance(nested, Mapping) else available_nodes


def _choice_values(available_nodes: Any, node_class: str, field_name: str) -> list[str] | None:
    info = _object_info(available_nodes)
    schema = info.get(node_class) if isinstance(info, Mapping) else None
    if not isinstance(schema, Mapping):
        return None
    input_schema = schema.get("input") if isinstance(schema.get("input"), Mapping) else {}
    for section_name in ("required", "optional"):
        section = input_schema.get(section_name) if isinstance(input_schema.get(section_name), Mapping) else {}
        raw = section.get(field_name)
        if raw is None:
            continue
        first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if isinstance(first, Mapping):
            first = first.get("choices") or first.get("values") or first.get("options") or []
        if isinstance(first, (list, tuple, set)):
            return [_clean_text(item).replace("\\", "/") for item in first if _clean_text(item)]
        return []
    return None


def _canonical_choice(requested: str, choices: list[str] | None) -> tuple[str, str]:
    if choices is None:
        return requested, "unchecked"
    by_folded = {item.casefold(): item for item in choices}
    exact = by_folded.get(requested.casefold())
    if exact:
        return exact, "accepted"
    basename = requested.rsplit("/", 1)[-1].casefold()
    matches = [item for item in choices if item.rsplit("/", 1)[-1].casefold() == basename]
    if len(matches) == 1:
        return matches[0], "canonicalized"
    return "", "rejected"


def _route_prompt_context(route: Mapping[str, Any] | None) -> dict[str, str]:
    route_data = route if isinstance(route, Mapping) else {}
    actual = route_data.get("actual_params") if isinstance(route_data.get("actual_params"), Mapping) else {}
    params = route_data.get("params") if isinstance(route_data.get("params"), Mapping) else {}
    containers = (actual, params, route_data)

    def first(keys: tuple[str, ...]) -> str:
        for container in containers:
            for key in keys:
                value = _clean_text(container.get(key))
                if value:
                    return value
        return ""

    return {
        "positive": first(("prompt", "positive_prompt", "positive", "main_prompt", "text")),
        "negative": first(("negative_prompt", "negative", "negative_text", "main_negative_prompt")),
    }


def _enabled_passes(detailer_passes: Any) -> list[dict[str, Any]]:
    if not isinstance(detailer_passes, list):
        return []
    return [dict(item) for item in detailer_passes if isinstance(item, Mapping) and item.get("enabled", True)]


def validate_detailer_model_source_selection(
    params: Mapping[str, Any] | None,
    *,
    route: Mapping[str, Any] | None = None,
    available_nodes: Any = None,
    detailer_passes: Any = None,
) -> dict[str, Any]:
    source_params = params if isinstance(params, Mapping) else {}
    source = normalize_model_source(source_params.get("model_source"))
    family = normalize_dedicated_family(source_params.get("detailer_model_family"))
    checkpoint_requested = _safe_comfy_model_name(source_params.get("detailer_checkpoint"))
    vae_requested = _safe_comfy_model_name(source_params.get("detailer_vae"))
    use_main_prompt = bool(source_params.get("use_main_prompt", True))
    prompt_context = _route_prompt_context(route)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if source == GENERATION_MODEL_SOURCE:
        return {
            "ready": True,
            "source": source,
            "family": "route_owned",
            "checkpoint": "",
            "vae": "",
            "checkpoint_status": "not_applicable",
            "vae_status": "not_applicable",
            "prompt_policy": "reuse_route_conditioning_with_optional_local_overrides",
            "use_main_prompt": use_main_prompt,
            "prompt_context": prompt_context,
            "errors": [],
            "warnings": [],
        }

    backend = _clean_text((route or {}).get("provider_id") or (route or {}).get("backend")).lower()
    if backend not in {"comfyui", "comfyui_portable"}:
        errors.append({
            "code": "adetailer_dedicated_model_provider_unsupported",
            "field": "model_source",
            "message": "Dedicated ADetailer checkpoints are supported only by ComfyUI and ComfyUI Portable routes.",
        })
    if family not in VALID_DEDICATED_FAMILIES:
        errors.append({
            "code": "adetailer_dedicated_family_invalid",
            "field": "detailer_model_family",
            "message": "Dedicated ADetailer checkpoints must be declared as SDXL or SD 1.5.",
        })
    if not checkpoint_requested:
        errors.append({
            "code": "adetailer_dedicated_checkpoint_missing",
            "field": "detailer_checkpoint",
            "message": "Select a dedicated SDXL or SD 1.5 checkpoint before queueing ADetailer.",
        })

    checkpoint_choices = _choice_values(available_nodes, "CheckpointLoaderSimple", "ckpt_name")
    checkpoint, checkpoint_status = _canonical_choice(checkpoint_requested, checkpoint_choices) if checkpoint_requested else ("", "missing")
    if checkpoint_requested and checkpoint_status == "rejected":
        errors.append({
            "code": "adetailer_dedicated_checkpoint_not_accepted",
            "field": "detailer_checkpoint",
            "message": "The selected dedicated checkpoint is not accepted by the active Comfy CheckpointLoaderSimple catalog.",
            "requested": checkpoint_requested,
            "choice_count": len(checkpoint_choices or []),
        })

    vae = ""
    vae_status = "checkpoint_output"
    raw_vae = _clean_text(source_params.get("detailer_vae")).lower()
    if raw_vae not in AUTO_VAE_VALUES:
        if not vae_requested:
            errors.append({
                "code": "adetailer_dedicated_vae_invalid",
                "field": "detailer_vae",
                "message": "The selected dedicated VAE path is invalid.",
            })
            vae_status = "invalid"
        else:
            vae_choices = _choice_values(available_nodes, "VAELoader", "vae_name")
            vae, vae_status = _canonical_choice(vae_requested, vae_choices)
            if vae_status == "rejected":
                errors.append({
                    "code": "adetailer_dedicated_vae_not_accepted",
                    "field": "detailer_vae",
                    "message": "The selected dedicated VAE is not accepted by the active Comfy VAELoader catalog.",
                    "requested": vae_requested,
                    "choice_count": len(vae_choices or []),
                })

    pass_prompt_rows: list[dict[str, Any]] = []
    for index, item in enumerate(_enabled_passes(detailer_passes), start=1):
        positive_local = _clean_text(item.get("positive_prompt"))
        negative_local = _clean_text(item.get("negative_prompt"))
        positive = positive_local or (prompt_context["positive"] if use_main_prompt else "")
        negative = negative_local or (prompt_context["negative"] if use_main_prompt else "")
        pass_id = _clean_text(item.get("id")) or f"pass-{index}"
        pass_prompt_rows.append({
            "pass_id": pass_id,
            "positive": positive,
            "negative": negative,
            "positive_source": "local" if positive_local else ("main" if positive else "missing"),
            "negative_source": "local" if negative_local else ("main" if negative else "empty"),
        })
        if not positive:
            errors.append({
                "code": "adetailer_dedicated_positive_prompt_missing",
                "field": f"detailer_passes[{index - 1}].positive_prompt",
                "message": "Dedicated ADetailer checkpoints require a positive repair prompt. Enable Use main prompts or enter a prompt on this pass.",
                "pass_id": pass_id,
            })

    if source_params.get("detailer_checkpoint") and checkpoint_status == "unchecked":
        warnings.append({
            "code": "adetailer_dedicated_checkpoint_catalog_unchecked",
            "field": "detailer_checkpoint",
            "message": "The active backend did not expose checkpoint choices; Neo will submit the exact selected checkpoint name and Comfy remains authoritative.",
        })
    if raw_vae not in AUTO_VAE_VALUES and vae_status == "unchecked":
        warnings.append({
            "code": "adetailer_dedicated_vae_catalog_unchecked",
            "field": "detailer_vae",
            "message": "The active backend did not expose VAE choices; Neo will submit the exact selected VAE name and Comfy remains authoritative.",
        })

    return {
        "ready": not errors,
        "source": source,
        "family": family,
        "checkpoint": checkpoint or checkpoint_requested,
        "vae": vae or (vae_requested if raw_vae not in AUTO_VAE_VALUES else ""),
        "checkpoint_status": checkpoint_status,
        "vae_status": vae_status,
        "use_checkpoint_vae": raw_vae in AUTO_VAE_VALUES,
        "prompt_policy": "encode_text_with_dedicated_checkpoint_clip",
        "use_main_prompt": use_main_prompt,
        "prompt_context": prompt_context,
        "pass_prompts": pass_prompt_rows,
        "errors": errors,
        "warnings": warnings,
    }


def apply_detailer_model_source_plan(
    workflow: dict[str, Any],
    next_id: str,
    *,
    plan: Mapping[str, Any],
    contract_refs: Mapping[str, Any],
) -> dict[str, Any]:
    source = normalize_model_source(plan.get("source"))
    if source == GENERATION_MODEL_SOURCE:
        return {
            "workflow": workflow,
            "next_id": next_id,
            "model_ref": deepcopy(contract_refs.get("model") or []),
            "clip_ref": deepcopy(contract_refs.get("clip") or []),
            "vae_ref": deepcopy(contract_refs.get("vae") or []),
            "positive_ref": deepcopy(contract_refs.get("positive") or []),
            "negative_ref": deepcopy(contract_refs.get("negative") or []),
            "node_ids": [],
            "model_source": deepcopy(dict(plan)),
        }

    loader_id = str(next_id)
    workflow[loader_id] = {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": str(plan.get("checkpoint") or "")},
    }
    next_id = _next_graph_id(workflow)
    vae_ref: list[Any] = [loader_id, 2]
    node_ids = [loader_id]
    if not bool(plan.get("use_checkpoint_vae", True)) and _clean_text(plan.get("vae")):
        vae_id = str(next_id)
        workflow[vae_id] = {
            "class_type": "VAELoader",
            "inputs": {"vae_name": str(plan.get("vae") or "")},
        }
        vae_ref = [vae_id, 0]
        node_ids.append(vae_id)
        next_id = _next_graph_id(workflow)
    return {
        "workflow": workflow,
        "next_id": next_id,
        "model_ref": [loader_id, 0],
        "clip_ref": [loader_id, 1],
        "vae_ref": vae_ref,
        "positive_ref": [],
        "negative_ref": [],
        "node_ids": node_ids,
        "model_source": {
            **deepcopy(dict(plan)),
            "loader_node_id": loader_id,
            "model_ref": [loader_id, 0],
            "clip_ref": [loader_id, 1],
            "vae_ref": deepcopy(vae_ref),
        },
    }


def add_dedicated_prompt_nodes(
    workflow: dict[str, Any],
    next_id: str,
    *,
    clip_ref: list[Any],
    positive_text: str,
    negative_text: str,
) -> dict[str, Any]:
    positive_id = str(next_id)
    workflow[positive_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": str(positive_text or "").strip(), "clip": deepcopy(clip_ref)},
    }
    next_id = _next_graph_id(workflow)
    negative_id = str(next_id)
    workflow[negative_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": str(negative_text or "").strip(), "clip": deepcopy(clip_ref)},
    }
    next_id = _next_graph_id(workflow)
    return {
        "workflow": workflow,
        "next_id": next_id,
        "positive_ref": [positive_id, 0],
        "negative_ref": [negative_id, 0],
        "node_ids": [positive_id, negative_id],
    }


def prompt_text_for_runtime_unit(plan: Mapping[str, Any], unit: Mapping[str, Any]) -> tuple[str, str, dict[str, str]]:
    local_positive = _clean_text(unit.get("positive_prompt"))
    local_negative = _clean_text(unit.get("negative_prompt"))
    context = plan.get("prompt_context") if isinstance(plan.get("prompt_context"), Mapping) else {}
    use_main = bool(plan.get("source") == DEDICATED_CHECKPOINT_SOURCE and plan.get("use_main_prompt", True))
    positive = local_positive or (_clean_text(context.get("positive")) if use_main else "")
    negative = local_negative or (_clean_text(context.get("negative")) if use_main else "")
    return positive, negative, {
        "positive_source": "local" if local_positive else ("main" if positive else "missing"),
        "negative_source": "local" if local_negative else ("main" if negative else "empty"),
    }


def _next_graph_id(workflow: Mapping[str, Any]) -> str:
    numeric: list[int] = []
    for key in workflow:
        try:
            numeric.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str((max(numeric) if numeric else 0) + 1)


def public_model_source_metadata(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    source = plan if isinstance(plan, Mapping) else {}
    allowed = (
        "ready",
        "source",
        "family",
        "checkpoint",
        "vae",
        "checkpoint_status",
        "vae_status",
        "use_checkpoint_vae",
        "prompt_policy",
        "use_main_prompt",
        "loader_node_id",
        "model_ref",
        "clip_ref",
        "vae_ref",
    )
    return {key: deepcopy(source.get(key)) for key in allowed if source.get(key) not in (None, "", [], {})}
