from __future__ import annotations

from collections import Counter
from typing import Any

from .route_profiles import (
    ACTIVE_STATES,
    AVAILABLE,
    EXPERIMENTAL,
    IMPLEMENTATION_TARGET,
    PLANNED_GATED,
    PROVIDER_GATED,
    UNSUPPORTED,
    KNOWN_STATES,
    TASK_INPAINT_CONTROL,
    TASK_MAP_CONTROL,
    TASK_OUTPAINT_CONTROL,
    CONTROLNET_TASKS,
    all_route_profiles,
    base_route_state,
    controlnet_state_for_route,
    normalized_route,
    route_profile_summary,
)

EXTENSION_ID = "image.controlnet"
TASKS = CONTROLNET_TASKS
TASK_WORKFLOW_MODES = {
    TASK_MAP_CONTROL: {"generate", "img2img", "edit", "inpaint", "outpaint"},
    TASK_INPAINT_CONTROL: {"inpaint"},
    TASK_OUTPAINT_CONTROL: {"outpaint"},
}
TASK_LABELS = {
    TASK_MAP_CONTROL: "Standard map control",
    TASK_INPAINT_CONTROL: "Inpaint mask control",
    TASK_OUTPAINT_CONTROL: "Outpaint mask/canvas control",
}

BACKENDS = ("comfyui", "comfyui_portable")
FAMILIES = ("sdxl", "sd15", "flux", "flux2_klein", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "z_image_turbo", "hidream", "wan_image", "hunyuan_image")
LOADERS = ("checkpoint", "checkpoint_aio", "diffusion_model", "gguf", "native")
MODES = ("generate", "img2img", "edit", "inpaint", "outpaint")


def normalize_workflow_mode(mode: str | None) -> str:
    value = str(mode or "generate").strip() or "generate"
    return "generate" if value == "txt2img" else value


def normalize_controlnet_task(task: str | None, *, workflow_mode: str | None = None) -> str:
    value = str(task or "").strip()
    if value in CONTROLNET_TASKS:
        return value
    return TASK_MAP_CONTROL


def task_allowed_for_mode(task: str, workflow_mode: str | None) -> bool:
    mode = normalize_workflow_mode(workflow_mode)
    return mode in TASK_WORKFLOW_MODES.get(normalize_controlnet_task(task), set())


def _state_for(family: str, loader: str, mode: str, *, backend: str = "comfyui", task: str = TASK_MAP_CONTROL) -> str:
    normalized = {"backend": backend, "family": family, "loader": loader, "mode": normalize_workflow_mode(mode)}
    return controlnet_state_for_route(normalized, task=task)


SUPPORT_MATRIX: dict[tuple[str, str, str, str], str] = {
    (backend, family, loader, mode): _state_for(family, loader, mode, backend=backend, task=TASK_MAP_CONTROL)
    for backend in BACKENDS
    for family in FAMILIES
    for loader in LOADERS
    for mode in MODES
}

WORKSPACE_SUPPORT = {
    "generations": AVAILABLE,
    "assets": IMPLEMENTATION_TARGET,
    "reference": AVAILABLE,
    "finish": UNSUPPORTED,
    "results": AVAILABLE,
}

GATED_REASONS = {
    IMPLEMENTATION_TARGET: "ControlNet has a route profile for this family/loader, but the family adapter has not been promoted yet.",
    PLANNED_GATED: "ControlNet is declared for this route but the base route or exact V2 compiler graph is not ready.",
    PROVIDER_GATED: "The selected backend/family/provider route or required ControlNet nodes are unavailable.",
    UNSUPPORTED: "This route is technically invalid for this ControlNet adapter in V2.",
}

TASK_GATED_REASONS = {
    TASK_MAP_CONTROL: "Standard map ControlNet uses a route-profiled map-conditioning patch path for active routes.",
    TASK_INPAINT_CONTROL: "ControlNet inpaint mask control requires a family-specific inpaint adapter: SD mask/canvas, Flux Alimama/Fun Union, Qwen DiffSynth/InstantX, or ZImage model patch.",
    TASK_OUTPAINT_CONTROL: "ControlNet outpaint mask/canvas control requires a family-specific canvas adapter: SD mask/canvas, Flux Alimama/Fun Union, Qwen DiffSynth/InstantX, or ZImage model patch.",
}


def route_state(backend: str, family: str, loader: str, mode: str) -> str:
    route = {"backend": backend, "family": family, "loader": loader, "mode": normalize_workflow_mode(mode)}
    return controlnet_state_for_route(route, task=TASK_MAP_CONTROL)


def controlnet_task_state(backend: str, family: str, loader: str, mode: str, task: str | None = None) -> str:
    workflow_mode = normalize_workflow_mode(mode)
    control_task = normalize_controlnet_task(task, workflow_mode=workflow_mode)
    if not task_allowed_for_mode(control_task, workflow_mode):
        return UNSUPPORTED
    route = {"backend": backend, "family": family, "loader": loader, "mode": workflow_mode}
    return controlnet_state_for_route(route, task=control_task)


def is_active_state(state: str) -> bool:
    return state in ACTIVE_STATES


def support_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (backend, family, loader, mode), state in sorted(SUPPORT_MATRIX.items()):
        summary = route_profile_summary({"backend": backend, "family": family, "loader": loader, "mode": mode}, task=TASK_MAP_CONTROL)
        rows.append({
            "backend": backend,
            "family": family,
            "loader": loader,
            "workflow_mode": mode,
            "state": state,
            "base_route_state": summary.get("base_route_state"),
            "route_profile_id": summary.get("profile_id"),
            "map_adapter": summary.get("map_adapter"),
            "family_group": summary.get("family_group"),
        })
    return rows


def task_support_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for backend in BACKENDS:
        for family in FAMILIES:
            for loader in LOADERS:
                for mode in MODES:
                    for task in CONTROLNET_TASKS:
                        summary = route_profile_summary({"backend": backend, "family": family, "loader": loader, "mode": mode}, task=task)
                        rows.append({
                            "backend": backend,
                            "family": family,
                            "loader": loader,
                            "workflow_mode": mode,
                            "controlnet_task": task,
                            "state": summary.get("controlnet_state"),
                            "base_route_state": summary.get("base_route_state"),
                            "route_profile_id": summary.get("profile_id"),
                            "map_adapter": summary.get("map_adapter"),
                            "inpaint_adapter": summary.get("inpaint_adapter"),
                            "outpaint_adapter": summary.get("outpaint_adapter"),
                            "model_dir": summary.get("model_dir"),
                            "cfg_policy": summary.get("cfg_policy"),
                        })
    return rows


def route_reason(state: str) -> str:
    return GATED_REASONS.get(state, "ControlNet route state is not active for this route.")


def task_route_reason(task: str | None, state: str) -> str:
    control_task = normalize_controlnet_task(task)
    if state in ACTIVE_STATES:
        return TASK_GATED_REASONS.get(control_task, "ControlNet task route is active.")
    if state == IMPLEMENTATION_TARGET:
        return GATED_REASONS[IMPLEMENTATION_TARGET]
    if control_task != TASK_MAP_CONTROL:
        return TASK_GATED_REASONS.get(control_task, route_reason(state))
    return route_reason(state)


def route_profile_for_route(backend: str, family: str, loader: str, mode: str, task: str | None = None) -> dict[str, Any]:
    control_task = normalize_controlnet_task(task, workflow_mode=mode)
    return route_profile_summary({"backend": backend, "family": family, "loader": loader, "mode": normalize_workflow_mode(mode)}, task=control_task)


def support_summary() -> dict[str, Any]:
    route_states = [row["state"] for row in support_matrix()]
    task_states = [row["state"] for row in task_support_matrix()]
    return {
        "extension_id": EXTENSION_ID,
        "phase": "P9.3-controlnet-flux-klein-enablement",
        "route_count": len(route_states),
        "task_route_count": len(task_states),
        "route_state_counts": dict(Counter(route_states)),
        "task_state_counts": dict(Counter(task_states)),
        "route_profile_count": len(all_route_profiles()),
        "known_states": sorted(KNOWN_STATES),
    }
