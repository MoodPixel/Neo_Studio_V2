from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal

PromptCaptioningMode = Literal["prompt_builder", "captioning"]

TEXT_TOOLS = {"prompt_generate", "prompt_enhance", "prompt_rewrite", "prompt_cleanup", "negative_prompt", "text_transform", "prompt_studio"}
CAPTION_TOOLS = {"image_captioning", "result_image_captioning", "batch_captioning", "caption_studio"}
TOOL_ALIASES = {"prompt_studio": "prompt_generate", "caption_studio": "image_captioning"}

_ALLOWED_INPUTS: dict[str, set[str]] = {
    "prompt_generate": {"source_text", "idea", "style", "custom_instructions", "selected_preset", "character_profile", "negative_source"},
    "prompt_enhance": {"source_text", "style", "custom_instructions", "selected_preset", "character_profile"},
    "prompt_rewrite": {"source_text", "style", "custom_instructions", "selected_preset", "character_profile"},
    "prompt_cleanup": {"source_text", "cleanup_mode", "selected_preset"},
    "negative_prompt": {"source_text", "positive_prompt", "style", "custom_instructions"},
    "text_transform": {"source_text", "transform_mode", "custom_instructions"},
    "image_captioning": {"caption_instruction", "caption_style", "caption_length", "output_style", "selected_preset"},
    "result_image_captioning": {"caption_instruction", "caption_style", "caption_length", "output_style", "selected_preset", "result_id"},
    "batch_captioning": {"caption_instruction", "caption_style", "caption_length", "output_style", "selected_preset", "batch_name", "folder_path", "workflow_mode"},
}

_ALLOWED_PARAMS: dict[str, set[str]] = {
    "prompt_generate": {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "stream", "cleanup_enabled"},
    "prompt_enhance": {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "stream", "enhance_mode"},
    "prompt_rewrite": {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "stream", "rewrite_mode"},
    "prompt_cleanup": {"cleanup_mode", "dedupe_tags", "normalize_commas", "trim_weights", "remove_empty_tokens"},
    "negative_prompt": {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "stream"},
    "text_transform": {"temperature", "top_p", "top_k", "max_tokens", "stop_sequences", "stream", "transform_mode"},
    "image_captioning": {"temperature", "top_p", "top_k", "max_tokens", "caption_mode", "component_type", "detail_level", "crop_json"},
    "result_image_captioning": {"temperature", "top_p", "top_k", "max_tokens", "caption_mode", "component_type", "detail_level", "crop_json"},
    "batch_captioning": {"temperature", "top_p", "top_k", "max_tokens", "caption_mode", "component_type", "detail_level", "recursive", "skip_existing", "start_index", "include_exts", "post_task_action", "caption_settings", "dataset", "library"},
}

_ALLOWED_ASSETS: dict[str, set[str]] = {
    "prompt_generate": set(),
    "prompt_enhance": set(),
    "prompt_rewrite": set(),
    "prompt_cleanup": set(),
    "negative_prompt": set(),
    "text_transform": set(),
    "image_captioning": {"image", "source_image"},
    "result_image_captioning": {"result_image", "source_image", "image"},
    "batch_captioning": {"images", "image_batch"},
}
# Wave 1: legacy marker for retired folder-only batch tests: "batch_captioning": set()

_DEFAULT_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 512,
    "stream": False,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _resolved_tool_id(tool_id: str) -> str:
    return TOOL_ALIASES.get(_clean_str(tool_id), _clean_str(tool_id))


def _mode_for_tool(tool_id: str, mode: str | None = None) -> PromptCaptioningMode:
    raw_mode = _clean_str(mode)
    if raw_mode in {"prompt_builder", "captioning"}:
        return raw_mode  # type: ignore[return-value]
    return "captioning" if _resolved_tool_id(tool_id) in CAPTION_TOOLS else "prompt_builder"


def _filter_mapping(source: dict[str, Any], allowed: set[str], prefix: str, stripped: list[str]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in source.items():
        if key in allowed:
            clean[key] = value
        else:
            stripped.append(f"{prefix}.{key}")
    return clean


def _normalize_assets(tool_id: str, assets: dict[str, Any], stripped: list[str]) -> dict[str, Any]:
    allowed = _ALLOWED_ASSETS.get(tool_id, set())
    clean = _filter_mapping(assets, allowed, "assets", stripped)
    if tool_id in TEXT_TOOLS:
        # Text routes must never carry hidden/stale image assets into provider calls.
        return {}
    if tool_id == "batch_captioning":
        images = clean.get("images") if "images" in clean else clean.get("image_batch")
        if images is None:
            return clean
        clean["images"] = _list(images)
        clean.pop("image_batch", None)
    return clean


def normalize_prompt_captioning_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a Prompt & Captioning payload into the V2 route-aware contract.

    This function does not execute providers. It strips hidden/stale fields and
    returns a safe payload that Prompt Builder and Captioning routes can validate,
    replay, save, or hand off later.
    """
    payload = _dict(payload)
    stripped: list[str] = []
    raw_tool_id = _clean_str(payload.get("tool") or payload.get("tool_id") or "prompt_generate")
    tool_id = _resolved_tool_id(raw_tool_id)
    mode = _mode_for_tool(tool_id, payload.get("mode"))

    inputs = _filter_mapping(_dict(payload.get("inputs")), _ALLOWED_INPUTS.get(tool_id, set()), "inputs", stripped)
    params = {**_DEFAULT_PARAMS, **_dict(payload.get("params"))}
    params = _filter_mapping(params, _ALLOWED_PARAMS.get(tool_id, set()), "params", stripped)
    assets = _normalize_assets(tool_id, _dict(payload.get("assets")), stripped)
    metadata = deepcopy(_dict(payload.get("metadata")))

    metadata.update({
        "surface_id": "prompt_captioning",
        "workspace_app": "neo_studio",
        "normalized_at": _now(),
    })
    if raw_tool_id != tool_id:
        metadata["tool_alias"] = raw_tool_id

    clean_payload = {
        "workspace": "prompt_captioning",
        "surface_id": "prompt_captioning",
        "mode": mode,
        "tool": tool_id,
        "tool_id": tool_id,
        "inputs": inputs,
        "params": params,
        "assets": assets,
        "metadata": metadata,
    }

    return {
        "ok": True,
        "payload": clean_payload,
        "stripped_fields": stripped,
        "contract_version": 1,
        "rules": [
            "Disabled or gated tools must validate before execution.",
            "Text-only tools never emit image assets.",
            "Caption tools must validate assets before execution.",
            "Hidden/stale fields are stripped before route execution.",
        ],
    }


def create_prompt_payload(*, tool: str = "prompt_generate", inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return normalize_prompt_captioning_payload({
        "workspace": "prompt_captioning",
        "mode": "prompt_builder",
        "tool": tool,
        "inputs": inputs or {},
        "params": params or {},
        "assets": {},
        "metadata": metadata or {},
    })["payload"]


def create_caption_payload(*, tool: str = "image_captioning", inputs: dict[str, Any] | None = None, params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return normalize_prompt_captioning_payload({
        "workspace": "prompt_captioning",
        "mode": "captioning",
        "tool": tool,
        "inputs": inputs or {},
        "params": params or {},
        "assets": assets or {},
        "metadata": metadata or {},
    })["payload"]
