from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .constants import (
    ACTIVE_ROUTE_STATES,
    AVAILABLE,
    DISCOVERED_FAMILIES,
    DISCOVERED_LOADERS,
    DISCOVERED_MODES,
    DISCOVERED_SUBTABS,
    EXTENSION_ID,
    PHASE,
    GATED_ROUTE_STATES,
    PROVIDER_GATED,
    SUPPORTED_BACKENDS,
    UNSUPPORTED,
    VALID_ROUTE_STATES,
    WORKSPACE_APP,
)

_DATA_PATH = Path(__file__).with_name("support_matrix_data.json")
IMAGE_WORKSPACE = "image"
PROVIDER_FAMILIES = {"wan_image", "hunyuan_image"}
KREA2_FAMILIES = {"krea2", "krea2_turbo"}
KREA2_LOADERS = {"diffusion_model", "gguf"}
KREA2_MODES = {"generate", "img2img", "inpaint", "outpaint"}
FAMILY_ALIASES = {
    "sd": "sd15",
    "sd1": "sd15",
    "sd1.5": "sd15",
    "stable_diffusion": "sd15",
    "stable_diffusion_xl": "sdxl",
    "sd_xl": "sdxl",
    "qwen": "qwen_image",
    "qwen_image_edit": "qwen_image",
    "qwen_rapid": "qwen_rapid_aio",
    "qwen_rapid_aio": "qwen_rapid_aio",
    "qwen-rapid-aio": "qwen_rapid_aio",
    "qwen_image_edit_2509": "qwen_image_edit_2509",
    "qwen-image-edit-2509": "qwen_image_edit_2509",
    "qwen_2509": "qwen_image_edit_2509",
    "qwen_image_edit_2511": "qwen_image_edit_2511",
    "qwen-image-edit-2511": "qwen_image_edit_2511",
    "qwen_2511": "qwen_image_edit_2511",
    "qwen-image": "qwen_image",
    "krea_2": "krea2",
    "krea2_raw": "krea2",
    "krea_2_turbo": "krea2_turbo",
    "krea2-turbo": "krea2_turbo",
    "zimage": "z_image",
    "z-image": "z_image",
    "zimage_turbo": "z_image_turbo",
    "z-image-turbo": "z_image_turbo",
    "z_image_turbo": "z_image_turbo",
    "hunyuan": "hunyuan_image",
    "wan": "wan_image",
}
BACKEND_ALIASES = {
    "comfy": "comfyui",
    "comfy_ui": "comfyui",
    "comfyui-local": "comfyui",
    "comfyui_portable": "comfyui_portable",
    "portable_comfyui": "comfyui_portable",
}
LOADER_ALIASES = {
    "ckpt": "checkpoint",
    "safetensors": "checkpoint",
    "checkpoint_loader": "checkpoint",
    "checkpointloader": "checkpoint",
    "diffusion": "diffusion_model",
    "diffusionmodel": "diffusion_model",
    "unet": "diffusion_model",
    "gguf_loader": "gguf",
    "ggufloader": "gguf",
    "provider": "native",
}
MODE_ALIASES = {
    "txt2img": "generate",
    "text2image": "generate",
    "text_to_image": "generate",
    "t2i": "generate",
    "image": "img2img",
    "image_to_image": "img2img",
    "i2i": "img2img",
    "edit": "img2img",
    "reference": "img2img",
    "multi_reference": "img2img",
    "repair": "inpaint",
    "mask": "inpaint",
}
SUBTAB_ALIASES = {
    "finnish": "finish",
    "finishing": "finish",
    "finish_pass": "finish",
    "generate": "generations",
    "txt2img": "generations",
    "output": "results",
    "outputs": "results",
}


def _clean(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_backend(value: Any) -> str:
    text = _clean(value, "comfyui")
    return BACKEND_ALIASES.get(text, text or "comfyui")


def normalize_family(value: Any) -> str:
    text = _clean(value, "unknown")
    return FAMILY_ALIASES.get(text, text or "unknown")


def normalize_loader(value: Any) -> str:
    text = _clean(value, "checkpoint")
    return LOADER_ALIASES.get(text, text or "checkpoint")


def normalize_mode(value: Any) -> str:
    text = _clean(value, "generate")
    return MODE_ALIASES.get(text, text or "generate")


def normalize_subtab(value: Any) -> str:
    text = _clean(value, WORKSPACE_APP)
    return SUBTAB_ALIASES.get(text, text or WORKSPACE_APP)


def normalize_workspace(value: Any) -> str:
    return _clean(value, IMAGE_WORKSPACE) or IMAGE_WORKSPACE


def normalize_route(route: dict[str, Any] | None = None, **overrides: Any) -> dict[str, str]:
    source = dict(route or {})
    source.update({k: v for k, v in overrides.items() if v is not None})
    return {
        "backend": normalize_backend(source.get("backend") or source.get("provider") or source.get("provider_id")),
        "family": normalize_family(source.get("family") or source.get("model_family")),
        "loader": normalize_loader(source.get("loader") or source.get("loader_type") or source.get("model_loader")),
        "mode": normalize_mode(source.get("workflow_mode") or source.get("mode") or source.get("generation_mode")),
        "workspace": normalize_workspace(source.get("workspace") or source.get("surface") or IMAGE_WORKSPACE),
        "subtab": normalize_subtab(source.get("workspace_app") or source.get("workspace_subtab") or source.get("subtab") or WORKSPACE_APP),
    }


@lru_cache(maxsize=1)
def _load_data() -> dict[str, Any]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def _rows_by_key() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {
        (row["backend"], row["family"], row["loader"], row["workflow_mode"]): row
        for row in _load_data().get("rows", [])
    }


def route_key(route: dict[str, Any] | None = None, **overrides: Any) -> str:
    norm = normalize_route(route, **overrides)
    return f"{norm['backend']}:{norm['family']}:{norm['loader']}:{norm['mode']}"


def route_reason(reason_code: str | None = None, state: str | None = None) -> str:
    """Return user-facing route guidance without internal rollout terminology."""
    reasons = _load_data().get("reasons", {})
    user_reasons = {
        "krea2_compiler_contract_experimental": (
            "Krea 2 finishing is available experimentally through the compiled workflow contract."
        ),
        "phase5_compiler_contract_experimental": (
            "This experimental route provides the image, model, conditioning, VAE, and sampler references "
            "needed for ADetailer. Real-image validation is still recommended before production use."
        ),
        "phase6_qwen_edit_identity_risk_experimental": (
            "Qwen Image Edit supports experimental detail repair with an identity-drift warning. "
            "Use a compatible identity LoRA or dedicated detailer model when face consistency matters."
        ),
        "phase6_qwen_edit_outpaint_gated": (
            "Qwen Image Edit outpaint is unavailable until padded-canvas repair, seams, and identity retention "
            "are validated for this route."
        ),
    }
    if reason_code in user_reasons:
        return user_reasons[reason_code]
    if reason_code and reason_code in reasons:
        return reasons[reason_code]
    if state == PROVIDER_GATED:
        return reasons.get("provider_family_gated", "ADetailer is unavailable for this provider route.")
    if state == UNSUPPORTED:
        return reasons.get("unknown_route", "ADetailer is unsupported for this route.")
    return reasons.get("unknown_route", "ADetailer support is not defined for this route.")


def _user_notes(notes: Any) -> list[str]:
    """Keep only guidance useful to artists; omit matrix and rollout bookkeeping."""
    clean: list[str] = []
    for item in notes if isinstance(notes, list) else []:
        text = str(item or "").strip()
        lowered = text.casefold()
        if not text or "phase " in lowered or "support matrix" in lowered or "exact row" in lowered:
            continue
        clean.append(text)
    return clean


def _workspace_overlay(normalized: dict[str, str]) -> tuple[str | None, str | None]:
    if normalized["workspace"] != IMAGE_WORKSPACE:
        return UNSUPPORTED, "not_generation_compiler"
    workspace_data = _load_data().get("workspace_subtabs", {})
    info = workspace_data.get(normalized["subtab"])
    if not info:
        return UNSUPPORTED, "not_generation_compiler"
    if normalized["subtab"] != WORKSPACE_APP:
        return info.get("state", UNSUPPORTED), info.get("reason_code", "finish_mount_only")
    return None, None


def support_for_route(route: dict[str, Any] | None = None, *, require_finish_subtab: bool = True, **overrides: Any) -> dict[str, Any]:
    normalized = normalize_route(route, **overrides)
    data = _load_data()
    rows = _rows_by_key()
    row = rows.get((normalized["backend"], normalized["family"], normalized["loader"], normalized["mode"]))

    if (
        row is None
        and normalized["backend"] in SUPPORTED_BACKENDS
        and normalized["family"] in KREA2_FAMILIES
        and normalized["loader"] in KREA2_LOADERS
        and normalized["mode"] in KREA2_MODES
    ):
        row = {
            "backend": normalized["backend"],
            "family": normalized["family"],
            "loader": normalized["loader"],
            "workflow_mode": normalized["mode"],
            "state": "experimental_available",
            "reason_code": "krea2_compiler_contract_experimental",
            "parameter_profile": "adetailer_krea2_route_owned",
            "workflow_patch_profile": "compiler_contract_face_detailer_or_segs",
            "notes": [
                "Krea 2 uses the compiler-published model, conditioning, VAE, sampler, and image references.",
                "Detailer LoRAs patch the generation model only so Krea 2 text-encoder conditioning remains unchanged.",
            ],
        }

    if row is None:
        if normalized["backend"] not in SUPPORTED_BACKENDS or normalized["family"] in PROVIDER_FAMILIES:
            state = PROVIDER_GATED
            reason_code = "provider_family_gated"
        else:
            state = data.get("default_state", UNSUPPORTED)
            reason_code = "unknown_route"
        row = {
            "backend": normalized["backend"],
            "family": normalized["family"],
            "loader": normalized["loader"],
            "workflow_mode": normalized["mode"],
            "state": state,
            "reason_code": reason_code,
            "parameter_profile": "diagnostic_only" if state in GATED_ROUTE_STATES else "hidden",
            "workflow_patch_profile": "none",
            "notes": [],
        }

    base_state = row.get("state", UNSUPPORTED)
    state = base_state if base_state in VALID_ROUTE_STATES else UNSUPPORTED
    reason_code = row.get("reason_code") or "unknown_route"
    workspace_state = None
    workspace_reason_code = None
    if require_finish_subtab:
        workspace_state, workspace_reason_code = _workspace_overlay(normalized)
        if workspace_state and workspace_state != AVAILABLE:
            state = workspace_state
            reason_code = workspace_reason_code or reason_code

    workflow_patch_allowed = state in ACTIVE_ROUTE_STATES and normalized["subtab"] == WORKSPACE_APP
    parameter_visible = state in ACTIVE_ROUTE_STATES and normalized["subtab"] == WORKSPACE_APP

    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "skeleton_only": False,
        "node_discovery_ready": True,
        "state": state,
        "route_state": base_state,
        "route_key": f"{normalized['backend']}:{normalized['family']}:{normalized['loader']}:{normalized['mode']}",
        "route": normalized,
        "reason_code": reason_code,
        "reason": route_reason(reason_code, state),
        "workflow_patch_allowed": workflow_patch_allowed,
        "parameter_visible": parameter_visible,
        "parameter_profile": row.get("parameter_profile", "hidden") if parameter_visible else "hidden",
        "workflow_patch_profile": row.get("workflow_patch_profile", "none") if workflow_patch_allowed else "none",
        "requires_nodes": True,
        "node_availability_checked": False,
        "node_availability_phase": "E",
        "notes": _user_notes(row.get("notes", [])),
    }


def route_state(backend: str, family: str, loader: str, mode: str) -> str:
    return support_for_route({"backend": backend, "family": family, "loader": loader, "workflow_mode": mode}, require_finish_subtab=False)["state"]


def is_active_state(state: str) -> bool:
    return state in ACTIVE_ROUTE_STATES


def support_matrix() -> list[dict[str, Any]]:
    rows = []
    for row in _load_data().get("rows", []):
        rows.append({
            "backend": row["backend"],
            "family": row["family"],
            "loader": row["loader"],
            "workflow_mode": row["workflow_mode"],
            "state": row["state"],
            "reason": route_reason(row.get("reason_code"), row.get("state")),
            "parameter_profile": row.get("parameter_profile", "hidden"),
            "workflow_patch_profile": row.get("workflow_patch_profile", "none"),
        })
    return rows


def support_summary() -> dict[str, Any]:
    rows = support_matrix()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "rows": len(rows),
        "states": counts,
        "backends": list(SUPPORTED_BACKENDS),
        "families": list(DISCOVERED_FAMILIES),
        "loaders": list(DISCOVERED_LOADERS),
        "workflow_modes": list(DISCOVERED_MODES),
        "workspace_subtabs": list(DISCOVERED_SUBTABS),
    }
