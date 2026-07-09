from __future__ import annotations

from typing import Any

EXTENSION_ID = "embeddings_ti"

AVAILABLE = "available"
EXPERIMENTAL = "experimental_available"
PLANNED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"

ACTIVE_ROUTE_STATES = {AVAILABLE, EXPERIMENTAL}
ACTIVE_STATES = ACTIVE_ROUTE_STATES
GATED_STATES = {PLANNED, PROVIDER_GATED, UNSUPPORTED}

SUPPORTED_BACKENDS = {"comfyui", "comfyui_portable"}
SUPPORTED_FAMILIES = ("sdxl", "sd15", "flux", "flux2_klein", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "z_image_turbo", "hidream", "wan_image", "hunyuan_image")
SUPPORTED_LOADERS = ("checkpoint", "diffusion_model", "unet", "gguf")
SUPPORTED_MODES = ("generate", "img2img", "inpaint", "outpaint")
FAMILIES = SUPPORTED_FAMILIES
LOADERS = SUPPORTED_LOADERS
MODES = SUPPORTED_MODES

PROMPT_PATCH_STRATEGIES = ("classic_ti_prompt_token_append", "metadata_only", "none")


def _key(family: str | None, loader: str | None, mode: str | None) -> tuple[str, str, str]:
    return ((family or "").strip(), (loader or "").strip(), (mode or "").strip())


# Phase D is explicit on purpose. Embeddings/TI is non-node-based, but active
# prompt injection still depends on a compatible classic checkpoint text-encoder
# prompt-token route. Asset browsing is wider than active prompt mutation.
ROUTE_SUPPORT: dict[tuple[str, str, str], dict[str, Any]] = {}


def _add(
    family: str,
    loader: str,
    mode: str,
    state: str,
    reason: str,
    prompt_patch: str,
    *,
    parameter_profile: str = "hidden",
    required_roles: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> None:
    ROUTE_SUPPORT[_key(family, loader, mode)] = {
        "backend": "comfyui",
        "family": family,
        "loader": loader,
        "mode": mode,
        "workflow_mode": mode,
        "state": state,
        "reason": reason,
        "prompt_patch": prompt_patch,
        "graph_patch": "none",
        "required_roles": roles_list(required_roles),
        "parameter_profile": parameter_profile,
        "notes": list(notes),
    }


def roles_list(values: tuple[str, ...]) -> list[str]:
    return [str(value) for value in values]


for _mode in SUPPORTED_MODES:
    _add(
        "sdxl",
        "checkpoint",
        _mode,
        AVAILABLE,
        "Validated classic checkpoint prompt-token route. Textual Inversion tokens can be appended to SDXL prompt fields before compile.",
        "classic_ti_prompt_token_append",
        parameter_profile="classic_ti_prompt_fields",
        required_roles=("checkpoint_clip", "positive_prompt", "negative_prompt"),
        notes=("No Comfy custom node is required; patching is prompt text only.",),
    )
    _add(
        "sd15",
        "checkpoint",
        _mode,
        EXPERIMENTAL,
        "Classic SD1.5 checkpoint TI token behavior is supported by the same prompt-token path, but route-specific visual parity is still marked experimental.",
        "classic_ti_prompt_token_append",
        parameter_profile="classic_ti_prompt_fields",
        required_roles=("checkpoint_clip", "positive_prompt", "negative_prompt"),
        notes=("Keep labelled experimental until direct SD1.5 parity images are validated.",),
    )

# Explicit gates. Do not open modern/GGUF families without a proven tokenizer/text
# encoder embedding injection path; no Flux/Qwen/SDXL fallback borrowing.
for _family in ("sdxl", "sd15"):
    for _loader in ("diffusion_model", "unet", "gguf"):
        for _mode in SUPPORTED_MODES:
            _add(
                _family,
                _loader,
                _mode,
                PLANNED,
                "Only checkpoint loader TI prompt-token behavior is validated for this family; diffusion_model/unet/gguf loader text-encoder injection is not proven.",
                "none",
                parameter_profile="diagnostic_gated",
            )

for _family in ("flux", "flux2_klein", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "z_image_turbo", "hidream"):
    for _loader in SUPPORTED_LOADERS:
        for _mode in SUPPORTED_MODES:
            _add(
                _family,
                _loader,
                _mode,
                PLANNED,
                "Textual Inversion is not validated for this modern family/loader/mode; active prompt injection stays gated until a compatible tokenizer/text-encoder path is proven.",
                "none",
                parameter_profile="diagnostic_gated",
                notes=("Asset browsing remains metadata-only; generation payload must not emit active prompt patch data.",),
            )

for _family in ("wan_image", "hunyuan_image"):
    for _loader in SUPPORTED_LOADERS:
        for _mode in SUPPORTED_MODES:
            _add(
                _family,
                _loader,
                _mode,
                PROVIDER_GATED,
                "Provider/compiler route is not active or not validated for Embeddings/TI prompt injection in this V2 build.",
                "none",
                parameter_profile="diagnostic_provider_gated",
            )

WORKSPACE_SUPPORT: dict[str, dict[str, str]] = {
    "assets": {
        "state": AVAILABLE,
        "reason": "Primary Embeddings/TI asset browser, scanner, metadata editor, and prompt-chip surface.",
        "behavior": "library_and_chip_staging",
    },
    "generations": {
        "state": AVAILABLE,
        "reason": "Generation compile may consume validated embeddings_ti payload blocks on active classic checkpoint routes only.",
        "behavior": "consume_validated_prompt_patch",
    },
    "reference": {
        "state": EXPERIMENTAL,
        "reason": "Reference/edit workflows may consume Embeddings/TI only when their active route resolves to SDXL/SD1.5 checkpoint prompt patching.",
        "behavior": "route_gated_consumption",
    },
    "finish": {
        "state": EXPERIMENTAL,
        "reason": "Expert finish-target chips are preserved; compile consumption must revalidate the selected finish route.",
        "behavior": "route_gated_finish_prompt_patch",
    },
    "results": {
        "state": UNSUPPORTED,
        "reason": "Results can display metadata/replay summaries but must not actively edit prompt fields.",
        "behavior": "metadata_display_only",
    },
}

PARAMETER_PROFILES: dict[str, dict[str, Any]] = {
    "classic_ti_prompt_fields": {
        "state": AVAILABLE,
        "visible_controls": [
            "selected_embedding",
            "selected_embedding_token",
            "target",
            "strength",
            "items",
            "civitai_url",
        ],
        "expert_controls": ["finish_positive", "finish_negative"],
        "payload_params": ["items"],
        "payload_assets": ["selected_embedding", "selected_embeddings"],
        "stale_field_policy": "strip_hidden_or_gated_values_before_generation",
    },
    "diagnostic_gated": {
        "state": PLANNED,
        "visible_controls": [],
        "diagnostic_controls": ["selected_embedding", "selected_embedding_token", "route_reason"],
        "payload_params": [],
        "payload_assets": [],
        "stale_field_policy": "strip_all_active_generation_values",
    },
    "diagnostic_provider_gated": {
        "state": PROVIDER_GATED,
        "visible_controls": [],
        "diagnostic_controls": ["route_reason"],
        "payload_params": [],
        "payload_assets": [],
        "stale_field_policy": "strip_all_active_generation_values",
    },
}


def normalize_route(route: dict[str, Any] | None = None) -> dict[str, str]:
    route = route or {}
    return {
        "backend": str(route.get("backend") or route.get("provider_id") or "comfyui"),
        "family": str(route.get("family") or "sdxl"),
        "loader": str(route.get("loader") or "checkpoint"),
        "workflow_mode": str(route.get("workflow_mode") or route.get("mode") or "generate"),
    }


def route_state(backend: str | None = "comfyui", family: str | None = "sdxl", loader: str | None = "checkpoint", workflow_mode: str | None = "generate") -> str:
    if backend and backend not in SUPPORTED_BACKENDS:
        return PROVIDER_GATED
    mode = (workflow_mode or "").strip()
    if not mode or mode not in SUPPORTED_MODES:
        return UNSUPPORTED
    support = ROUTE_SUPPORT.get(_key(family, loader, mode))
    if support:
        return str(support["state"])
    return PLANNED


def support_reason(backend: str | None = "comfyui", family: str | None = "sdxl", loader: str | None = "checkpoint", workflow_mode: str | None = "generate") -> str:
    if backend and backend not in SUPPORTED_BACKENDS:
        return "Backend is not a supported Comfy backend for Embeddings/TI."
    mode = (workflow_mode or "").strip()
    if not mode or mode not in SUPPORTED_MODES:
        return "Workflow mode is not an active image generation prompt target for Embeddings/TI."
    support = ROUTE_SUPPORT.get(_key(family, loader, mode))
    if support:
        return str(support["reason"])
    return "Embeddings/TI route is planned but not proven; active prompt injection remains gated."


def prompt_patch_strategy(backend: str | None = "comfyui", family: str | None = "sdxl", loader: str | None = "checkpoint", workflow_mode: str | None = "generate") -> str:
    if backend and backend not in SUPPORTED_BACKENDS:
        return "none"
    support = ROUTE_SUPPORT.get(_key(family, loader, workflow_mode))
    return str(support["prompt_patch"]) if support else "none"


def parameter_profile(backend: str | None = "comfyui", family: str | None = "sdxl", loader: str | None = "checkpoint", workflow_mode: str | None = "generate") -> dict[str, Any]:
    support = route_support(backend, family, loader, workflow_mode)
    profile_id = str(support.get("parameter_profile") or "diagnostic_gated")
    profile = dict(PARAMETER_PROFILES.get(profile_id, PARAMETER_PROFILES["diagnostic_gated"]))
    profile["profile_id"] = profile_id
    profile["route_state"] = support["state"]
    profile["reason"] = support["reason"]
    return profile


def route_support(backend: str | None = "comfyui", family: str | None = "sdxl", loader: str | None = "checkpoint", workflow_mode: str | None = "generate") -> dict[str, Any]:
    backend = str(backend or "comfyui")
    family = str(family or "")
    loader = str(loader or "")
    mode = str(workflow_mode or "")
    state = route_state(backend, family, loader, mode)
    support = ROUTE_SUPPORT.get(_key(family, loader, mode))
    prompt_patch = str(support["prompt_patch"]) if support else "none"
    parameter_profile_id = str(support["parameter_profile"]) if support else "diagnostic_gated"
    return {
        "backend": backend,
        "family": family,
        "loader": loader,
        "mode": mode,
        "workflow_mode": mode,
        "state": state,
        "reason": support_reason(backend, family, loader, mode),
        "prompt_patch": prompt_patch,
        "graph_patch": "none",
        "required_roles": list(support["required_roles"] if support else []),
        "parameter_profile": parameter_profile_id,
        "notes": list(support["notes"] if support else []),
        "active": state in ACTIVE_ROUTE_STATES,
        "route_key": f"{family}:{loader}:{mode}",
    }


def is_active_route(route: dict[str, Any] | None = None) -> bool:
    ctx = normalize_route(route)
    return route_state(ctx["backend"], ctx["family"], ctx["loader"], ctx["workflow_mode"]) in ACTIVE_ROUTE_STATES


def support_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in SUPPORTED_FAMILIES:
        for loader in SUPPORTED_LOADERS:
            for mode in SUPPORTED_MODES:
                rows.append(route_support("comfyui", family, loader, mode))
    return rows


def active_prompt_patch_routes() -> list[dict[str, Any]]:
    return [row for row in support_matrix() if row["state"] in ACTIVE_ROUTE_STATES]


def gated_routes() -> list[dict[str, Any]]:
    return [row for row in support_matrix() if row["state"] in GATED_STATES]


def workspace_support_matrix() -> list[dict[str, str]]:
    return [{"workspace_app": key, **value} for key, value in WORKSPACE_SUPPORT.items()]


def manifest_route_states() -> dict[str, str]:
    states = {row["route_key"]: row["state"] for row in support_matrix()}
    states.update({key: value["state"] for key, value in WORKSPACE_SUPPORT.items()})
    states["*"] = PLANNED
    return states
