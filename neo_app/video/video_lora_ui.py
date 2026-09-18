from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, video_backend_profile_payload
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.lora_stack.ui_catalog.v1"
MODEL_ONLY_LOADER: Final[str] = "LoraLoaderModelOnly"
ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
MATRIX_PATH: Final[Path] = ROOT_DIR / "neo_extensions" / "built_in" / "video.lora_stack" / "backend" / "support_matrix_data.json"
SPEED_TOKENS: Final[tuple[str, ...]] = (
    "turbo", "lightx2v", "lightning", "4step", "4steps", "8step", "8steps", "distilled", "accelerator",
)
H3_FAMILY_TOKENS: Final[tuple[str, ...]] = ("h3", "minimax", "minimax_h3", "minimax-h3", "hailuo")


def _load_matrix() -> dict[str, Any]:
    try:
        data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"groups": [], "supported_backends": []}
    return data if isinstance(data, dict) else {"groups": [], "supported_backends": []}


def _matrix_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in _load_matrix().get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        shared = {key: deepcopy(value) for key, value in group.items() if key != "route_ids"}
        for route_id in group.get("route_ids", []) or []:
            rid = str(route_id or "").strip()
            if rid:
                rows[rid] = {"route_id": rid, **deepcopy(shared)}
    return rows


def _blocked_support(route_id: str, reason: str) -> dict[str, Any]:
    return {
        "route_id": route_id,
        "state": "blocked",
        "supports_standard_lora": False,
        "supports_speed_lora": False,
        "supports_branch_targeting": False,
        "allowed_targets": ["all"],
        "required_loader_type": "unknown",
        "required_loader_nodes": [],
        "validated_topology": False,
        "eligible": False,
        "fail_closed": True,
        "reason": reason,
    }


def support_for_route_id(route_id: str, backend: str = "comfyui") -> dict[str, Any]:
    matrix = _load_matrix()
    supported_backends = {str(value) for value in matrix.get("supported_backends", []) or []}
    if backend not in supported_backends:
        return _blocked_support(route_id, f"Video LoRA Stack does not support backend {backend!r}.")
    row = _matrix_rows().get(str(route_id or "").strip())
    if not row:
        return _blocked_support(route_id, "No exact Video LoRA support-matrix row exists for this route. Fail closed.")
    result = deepcopy(row)
    result["backend"] = backend
    result["eligible"] = result.get("state") == "supported"
    result["fail_closed"] = result.get("state") != "supported"
    return result


def _actual_class(object_info: dict[str, Any], class_name: str) -> str:
    folded = {str(key).casefold(): str(key) for key in (object_info or {})}
    return folded.get(class_name.casefold(), "")


def _required_inputs(object_info: dict[str, Any], class_name: str) -> dict[str, Any]:
    entry = object_info.get(class_name, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    return required if isinstance(required, dict) else {}


def _combo_values(required: dict[str, Any], field: str) -> list[str]:
    spec = required.get(field)
    if not isinstance(spec, list) or not spec:
        return []
    values = spec[0]
    return [str(item) for item in values if str(item)] if isinstance(values, list) else []


def validate_model_only_loader(object_info: dict[str, Any]) -> dict[str, Any]:
    actual = _actual_class(object_info, MODEL_ONLY_LOADER)
    if not actual:
        return {
            "available": False,
            "safe": False,
            "class_name": "",
            "required_inputs": [],
            "missing_inputs": ["model", "lora_name", "strength_model"],
            "catalog": [],
            "reason": "ComfyUI does not expose LoraLoaderModelOnly.",
        }
    required = _required_inputs(object_info, actual)
    required_names = {str(name) for name in required}
    expected = {"model", "lora_name", "strength_model"}
    missing = sorted(expected - required_names)
    catalog = _combo_values(required, "lora_name")
    safe = not missing
    reason = "" if safe else f"LoraLoaderModelOnly is missing required input(s): {', '.join(missing)}."
    return {
        "available": True,
        "safe": safe,
        "class_name": actual,
        "required_inputs": sorted(required_names),
        "missing_inputs": missing,
        "catalog": catalog,
        "reason": reason,
    }


def is_speed_candidate(name: str, family: str = "") -> bool:
    text = str(name or "").casefold().replace("_", "-")
    if not text:
        return False
    has_speed = any(token.replace("_", "-") in text for token in SPEED_TOKENS)
    if not has_speed:
        return False
    if normalize_video_family(family) == "minimax_h3":
        return any(token.replace("_", "-") in text for token in H3_FAMILY_TOKENS)
    return True


def _resolved_route(family: str | None, loader: str | None, generation_type: str | None) -> dict[str, Any]:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    ng = normalize_video_generation_type(generation_type)
    route = find_video_route(nf, nl, ng, include_planned=True)
    return {
        "family": nf,
        "loader": nl,
        "generation_type": ng,
        "route_id": route.route_id if route else f"{nf}.{nl}.{ng}",
        "route_status": route.status if route else "unavailable",
    }


def video_lora_catalog_from_object_info(
    *,
    family: str | None,
    loader: str | None,
    generation_type: str | None,
    profile: dict[str, Any] | None,
    object_info: dict[str, Any] | None,
) -> dict[str, Any]:
    route = _resolved_route(family, loader, generation_type)
    provider_id = str((profile or {}).get("provider_id") or "comfyui")
    backend = provider_id if provider_id in {"comfyui", "comfyui_portable"} else provider_id
    support = support_for_route_id(route["route_id"], backend=backend)
    loader_probe = validate_model_only_loader(object_info or {})
    catalog = list(loader_probe.get("catalog") or []) if support.get("eligible") else []
    speed_candidates = [name for name in catalog if is_speed_candidate(name, route["family"])]
    reasons: list[str] = []
    if not support.get("eligible"):
        reasons.append(str(support.get("reason") or f"Video LoRA support is {support.get('state')}."))
    if support.get("eligible") and not loader_probe.get("safe"):
        reasons.append(str(loader_probe.get("reason") or "LoraLoaderModelOnly is not safe for this route."))
    if support.get("eligible") and loader_probe.get("safe") and not catalog:
        reasons.append("LoraLoaderModelOnly exposes an empty live LoRA catalog.")
    ready = bool(support.get("eligible") and loader_probe.get("safe") and catalog)
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "route": route,
        "profile": {
            "profile_id": (profile or {}).get("profile_id") or "",
            "provider_id": provider_id,
            "display_name": (profile or {}).get("display_name") or "",
        },
        "support": support,
        "loader": loader_probe,
        "catalog": catalog,
        "speed_candidates": speed_candidates,
        "manual_selection_classifier_gate": False,
        "ready": ready,
        "fail_closed": not ready,
        "reasons": reasons,
    }


def video_lora_catalog_payload(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    profile_id: str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    profile = video_backend_profile_payload(profile_id)
    provider_id = str(profile.get("provider_id") or "")
    if provider_id not in {"comfyui", "comfyui_portable"}:
        route = _resolved_route(family, loader, generation_type)
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "route": route,
            "profile": profile,
            "support": _blocked_support(route["route_id"], f"Video LoRA Stack requires a local ComfyUI backend; active provider is {provider_id or 'unknown'}."),
            "loader": validate_model_only_loader({}),
            "catalog": [],
            "speed_candidates": [],
            "manual_selection_classifier_gate": False,
            "ready": False,
            "fail_closed": True,
            "reasons": ["Video LoRA catalog discovery is available only for ComfyUI / ComfyUI Portable profiles."],
        }
    base_url = str((profile.get("connection") or {}).get("base_url") or "http://127.0.0.1:8188")
    try:
        object_info = _get_json(base_url, "/object_info", max(0.25, min(float(timeout), 10.0)))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        route = _resolved_route(family, loader, generation_type)
        support = support_for_route_id(route["route_id"], backend=provider_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "route": route,
            "profile": profile,
            "support": support,
            "loader": validate_model_only_loader({}),
            "catalog": [],
            "speed_candidates": [],
            "manual_selection_classifier_gate": False,
            "ready": False,
            "fail_closed": True,
            "reasons": [f"ComfyUI /object_info could not be read: {exc}"],
        }
    return video_lora_catalog_from_object_info(
        family=family,
        loader=loader,
        generation_type=generation_type,
        profile=profile,
        object_info=object_info,
    )
