from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

VIDEO_LORA_EXTENSION_ID = "video.lora_stack"
MAX_VIDEO_LORAS = 12
H3_MODEL_ONLY_LOADER = "LoraLoaderModelOnly"

ROLE_ALIASES = {
    "normal": "standard",
    "default": "standard",
    "style": "standard",
    "motion": "standard",
    "turbo": "speed",
    "lightning": "speed",
    "lightx2v": "speed",
    "distilled": "speed",
    "accelerator": "speed",
    "fast": "speed",
}
TARGET_ALIASES = {
    "both": "all",
    "model": "all",
    "base": "all",
    "global": "all",
    "high_noise": "high",
    "high-noise": "high",
    "low_noise": "low",
    "low-noise": "low",
}

H3_FAMILY_ALIASES = (
    "h3",
    "minimax",
    "minimax_h3",
    "minimax-h3",
    "hailuo",
)
H3_SPEED_TOKENS = (
    "turbo",
    "lightx2v",
    "lightning",
    "4step",
    "4steps",
    "8step",
    "8steps",
    "distilled",
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _strength(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(-10.0, min(10.0, number)), 4)


def _portable_name(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def normalize_video_lora_row(row: dict[str, Any] | None, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not _as_bool(row.get("enabled"), True):
        return None
    name = _portable_name(row.get("portable_catalog_name") or row.get("name") or row.get("lora_name"))
    if not name:
        return None
    role = str(row.get("role") or "standard").strip().casefold()
    role = ROLE_ALIASES.get(role, role)
    if role not in {"standard", "speed"}:
        role = "standard"
    target = str(row.get("target") or row.get("lora_target") or "all").strip().casefold()
    target = TARGET_ALIASES.get(target, target)
    if target not in {"all", "high", "low"}:
        target = "all"
    result: dict[str, Any] = {
        "uid": str(row.get("uid") or f"video_lora_{index + 1}"),
        "enabled": True,
        "name": name,
        "strength_model": _strength(row.get("strength_model", row.get("strength", row.get("lora_strength", 1.0)))),
        "role": role,
        "target": target,
    }
    if row.get("strength_clip") is not None:
        result["strength_clip"] = _strength(row.get("strength_clip"), 1.0)
    return result


def normalize_video_lora_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(rows or []):
        item = normalize_video_lora_row(row, index)
        if not item:
            continue
        key = (
            item["name"].casefold(),
            item["strength_model"],
            item.get("strength_clip"),
            item["role"],
            item["target"],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= MAX_VIDEO_LORAS:
            break
    return normalized


def extract_video_lora_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    block: dict[str, Any] = {}
    extensions = data.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(VIDEO_LORA_EXTENSION_ID), dict):
        block = extensions[VIDEO_LORA_EXTENSION_ID]
    elif isinstance(data.get(VIDEO_LORA_EXTENSION_ID), dict):
        block = data[VIDEO_LORA_EXTENSION_ID]
    payloads = data.get("payloads")
    if not block and isinstance(payloads, dict) and isinstance(payloads.get(VIDEO_LORA_EXTENSION_ID), dict):
        block = payloads[VIDEO_LORA_EXTENSION_ID]
    if not block or not _as_bool(block.get("enabled"), False):
        return []
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    rows = params.get("loras") if isinstance(params.get("loras"), list) else []
    return normalize_video_lora_rows(rows)


def _classifier_text(name: str) -> str:
    text = _portable_name(name).casefold()
    basename = PurePosixPath(text).name
    return " ".join((text, basename)).replace("_", "-")


def is_h3_speed_lora_name(name: str) -> bool:
    """Classify H3 acceleration candidates without restricting manual selection."""
    text = _classifier_text(name)
    has_family = any(alias.replace("_", "-") in text for alias in H3_FAMILY_ALIASES)
    has_speed = any(token.replace("_", "-") in text for token in H3_SPEED_TOKENS)
    return bool(has_family and has_speed)


def h3_speed_lora_candidates(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        name = _portable_name(value)
        key = name.casefold()
        if name and key not in seen and is_h3_speed_lora_name(name):
            seen.add(key)
            result.append(name)
    return result


def merge_h3_legacy_turbo(
    rows: list[dict[str, Any]] | None,
    *,
    enabled: bool,
    selected_name: str = "",
    discovered_candidates: list[str] | tuple[str, ...] | None = None,
    strength: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bridge legacy H3 Turbo fields into the universal stack without double application."""
    merged = normalize_video_lora_rows(rows)
    meta = {
        "requested": bool(enabled),
        "bridged": False,
        "duplicate_suppressed": False,
        "selected_name": "",
        "source": "legacy_h3_turbo_fields",
    }
    if not enabled:
        return _ordered_h3_rows(merged), meta

    selected = _portable_name(selected_name)
    if not selected:
        candidates = h3_speed_lora_candidates(discovered_candidates)
        selected = candidates[0] if candidates else ""
    if not selected:
        raise ValueError(
            "H3 Turbo is enabled but no Turbo/LightX2V/Lightning LoRA was selected or discovered in LoraLoaderModelOnly."
        )

    meta["selected_name"] = selected
    existing = next((row for row in merged if str(row.get("name") or "").casefold() == selected.casefold()), None)
    if existing:
        meta["duplicate_suppressed"] = True
        return _ordered_h3_rows(merged), meta

    if len(merged) >= MAX_VIDEO_LORAS:
        raise ValueError(f"Video LoRA Stack already contains the maximum {MAX_VIDEO_LORAS} entries; legacy H3 Turbo cannot be appended.")

    merged.append(
        {
            "uid": "legacy_h3_turbo",
            "enabled": True,
            "name": selected,
            "strength_model": _strength(strength),
            "role": "speed",
            "target": "all",
        }
    )
    meta["bridged"] = True
    return _ordered_h3_rows(merged), meta


def _ordered_h3_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    standard = [deepcopy(row) for row in rows if row.get("role") != "speed"]
    speed = [deepcopy(row) for row in rows if row.get("role") == "speed"]
    return [*standard, *speed]


def _next_numeric_node_id(workflow: dict[str, Any]) -> int:
    numeric = [int(key) for key in workflow if str(key).isdigit()]
    return max(numeric, default=0) + 1


def _validate_model_only_profile(workflow: dict[str, Any], profile: dict[str, Any], route_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(profile, dict) or profile.get("owner") != "compiler":
        raise ValueError("Video LoRA Stack requires a compiler-owned LoRA patch profile.")
    if str(profile.get("route_id") or "") != route_id:
        raise ValueError("Video LoRA patch profile route does not match the compiled H3 route.")
    if str(profile.get("loader_type") or "") != "model_only":
        raise ValueError("MiniMax H3 Video LoRA integration requires a model_only patch profile.")
    if str(profile.get("loader_node_class") or "") != H3_MODEL_ONLY_LOADER:
        raise ValueError("MiniMax H3 Video LoRA integration requires LoraLoaderModelOnly; generic LoraLoader is not accepted.")
    if bool(profile.get("allow_generic_lora_loader_fallback", False)):
        raise ValueError("Generic LoraLoader fallback is forbidden for MiniMax H3 Video LoRA integration.")
    if profile.get("targets") != ["all"]:
        raise ValueError("MiniMax H3 patch profile must expose only the all target.")
    branches = profile.get("branches") if isinstance(profile.get("branches"), list) else []
    if len(branches) != 1 or not isinstance(branches[0], dict) or branches[0].get("target") != "all":
        raise ValueError("MiniMax H3 patch profile must contain one all-target model branch.")
    branch = branches[0]
    model_ref = branch.get("model_ref") if isinstance(branch.get("model_ref"), list) else []
    consumers = branch.get("model_consumers") if isinstance(branch.get("model_consumers"), list) else []
    if len(model_ref) != 2 or not consumers:
        raise ValueError("MiniMax H3 patch profile is missing its model reference or consumer declaration.")
    if str(model_ref[0]) not in workflow:
        raise ValueError("MiniMax H3 patch profile model reference no longer exists in the compiled workflow.")
    for consumer in consumers:
        node_id = str(consumer.get("node_id") or "")
        input_name = str(consumer.get("input") or "")
        node = workflow.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else {}
        if inputs.get(input_name) != model_ref:
            raise ValueError(f"MiniMax H3 LoRA anchor is stale: {node_id}.{input_name} no longer consumes the declared model ref.")
    return branch, consumers


def apply_h3_model_only_lora_stack(
    workflow: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
    *,
    route_id: str,
    loader_available: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply H3 LoRAs only through the compiler-declared model anchor."""
    graph = deepcopy(workflow) if isinstance(workflow, dict) else {}
    ordered = _ordered_h3_rows(normalize_video_lora_rows(rows))
    if not ordered:
        return graph, {
            "schema_version": "neo.video.lora_stack.h3.runtime.v1",
            "active": False,
            "route_id": route_id,
            "requested_count": 0,
            "applied_count": 0,
            "standard_count": 0,
            "speed_count": 0,
            "loader_node_class": H3_MODEL_ONLY_LOADER,
            "applied": [],
            "warnings": [],
        }
    if ".unet." not in route_id:
        raise ValueError("Video LoRA Stack is fail-closed for MiniMax H3 GGUF until GGUF LoRA-loader compatibility is validated.")
    if not loader_available:
        raise ValueError("MiniMax H3 LoRA rows were requested but ComfyUI does not expose LoraLoaderModelOnly.")
    branch, consumers = _validate_model_only_profile(graph, profile or {}, route_id)
    upstream_ref = list(branch["model_ref"])
    next_id = _next_numeric_node_id(graph)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row in ordered:
        if row.get("target") != "all":
            raise ValueError("MiniMax H3 supports only target='all'; high/low branch targeting is WAN-only.")
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {
            "class_type": H3_MODEL_ONLY_LOADER,
            "inputs": {
                "model": upstream_ref,
                "lora_name": row["name"],
                "strength_model": float(row["strength_model"]),
            },
            "_meta": {"title": f"Video LoRA · H3 · {row['role']}"},
        }
        upstream_ref = [node_id, 0]
        applied.append(
            {
                "uid": row.get("uid") or "",
                "name": row["name"],
                "strength_model": row["strength_model"],
                "role": row["role"],
                "target": "all",
                "node_id": node_id,
            }
        )
        if row.get("strength_clip") is not None:
            warnings.append(f"{row['name']}: strength_clip is ignored because MiniMax H3 uses a model-only LoRA loader.")

    for consumer in consumers:
        node_id = str(consumer.get("node_id") or "")
        input_name = str(consumer.get("input") or "")
        graph[node_id]["inputs"][input_name] = list(upstream_ref)

    return graph, {
        "schema_version": "neo.video.lora_stack.h3.runtime.v1",
        "active": True,
        "route_id": route_id,
        "requested_count": len(ordered),
        "applied_count": len(applied),
        "standard_count": len([row for row in ordered if row.get("role") != "speed"]),
        "speed_count": len([row for row in ordered if row.get("role") == "speed"]),
        "loader_node_class": H3_MODEL_ONLY_LOADER,
        "final_model_ref": upstream_ref,
        "applied": applied,
        "warnings": warnings,
    }
