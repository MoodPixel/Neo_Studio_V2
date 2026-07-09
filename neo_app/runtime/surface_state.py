from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_ID: Final[str] = "neo.ui.surface_runtime.v25_5"
PHASE: Final[str] = "V25.5"

IMAGE_WORKSPACE_APPS: Final[tuple[str, ...]] = ("generations", "assets", "reference", "finish", "results")
VIDEO_WORKSPACE_APPS: Final[tuple[str, ...]] = ("workspace", "generation", "assets", "reference", "finish", "results")
IMAGE_WORKFLOW_MODES: Final[tuple[str, ...]] = ("generate", "edit", "inpaint", "outpaint", "upscale", "variation")
VIDEO_GENERATION_MODES: Final[tuple[str, ...]] = ("txt2vid", "img2vid", "first_last_frame", "multiscene", "extend", "vid2vid", "depth_motion", "prompt_schedule", "audio_video")

DEFAULT_SURFACE_RUNTIME: Final[dict[str, dict[str, str]]] = {
    "image": {
        "schema_id": SCHEMA_ID,
        "workspace_app": "generations",
        "workflow_mode": "generate",
        "subtab": "generate",
    },
    "video": {
        "schema_id": SCHEMA_ID,
        "workspace_app": "workspace",
        "generation_mode": "txt2vid",
        "subtab": "workspace",
    },
    "prompt_captioning": {
        "schema_id": SCHEMA_ID,
        "workspace_app": "prompt_preset_details",
        "workspace_mode": "prompt_builder",
        "child_tab": "prompt_preset_details",
        "subtab": "prompt_builder",
    },
    "voice": {
        "schema_id": SCHEMA_ID,
        "workspace_app": "generation",
        "workflow_mode": "quick_preview",
        "subtab": "generation",
    },
}


def normalize_image_workspace_app(value: str | None) -> str:
    alias = {"generation": "generations", "asset": "assets"}.get(str(value or ""), str(value or ""))
    return alias if alias in IMAGE_WORKSPACE_APPS else "generations"


def normalize_video_workspace_app(value: str | None) -> str:
    alias = {"generations": "generation", "asset": "assets"}.get(str(value or ""), str(value or ""))
    return alias if alias in VIDEO_WORKSPACE_APPS else "workspace"


def normalize_image_workflow_mode(value: str | None) -> str:
    mode = str(value or "")
    if mode == "txt2img":
        mode = "generate"
    return mode if mode in IMAGE_WORKFLOW_MODES else "generate"


def normalize_video_generation_mode(value: str | None) -> str:
    mode = str(value or "")
    return mode if mode in VIDEO_GENERATION_MODES else "txt2vid"


def default_surface_runtime(surface_id: str) -> dict[str, str]:
    return deepcopy(DEFAULT_SURFACE_RUNTIME.get(surface_id, {"schema_id": SCHEMA_ID, "workspace_app": "workspace", "subtab": "workspace"}))


def normalize_surface_runtime(surface_id: str, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a normalized runtime entry without mixing workspace and workflow concepts."""

    surface = surface_id or "image"
    merged: dict[str, Any] = {**default_surface_runtime(surface), **(runtime or {}), "schema_id": SCHEMA_ID}

    if surface == "image":
        merged["workspace_app"] = normalize_image_workspace_app(merged.get("workspace_app"))
        merged["workflow_mode"] = normalize_image_workflow_mode(merged.get("workflow_mode") or merged.get("subtab"))
        merged["subtab"] = merged["workflow_mode"]
    elif surface == "video":
        merged["workspace_app"] = normalize_video_workspace_app(merged.get("workspace_app") or merged.get("subtab"))
        merged["generation_mode"] = normalize_video_generation_mode(merged.get("generation_mode"))
        merged["subtab"] = merged["workspace_app"]
    elif surface == "prompt_captioning":
        mode = "captioning" if merged.get("workspace_mode") == "captioning" else "prompt_builder"
        child = str(merged.get("child_tab") or merged.get("workspace_app") or "prompt_preset_details")
        merged.update({"workspace_mode": mode, "child_tab": child, "workspace_app": child, "subtab": mode})
    else:
        merged["workspace_app"] = str(merged.get("workspace_app") or merged.get("subtab") or "workspace")
        merged["subtab"] = str(merged.get("subtab") or merged["workspace_app"])
    return merged


def surface_state_isolation_contract() -> dict[str, Any]:
    return {
        "schema_id": SCHEMA_ID,
        "phase": PHASE,
        "state_owner": "surfaceRuntime",
        "legacy_aliases": ["activeSubtabId", "activeWorkspaceAppId", "activeSubtabsBySurface"],
        "legacy_alias_rule": "Aliases mirror the active surface only; inactive surface state stays in surfaceRuntime.",
        "separation_rules": {
            "image": {
                "workspace_app": list(IMAGE_WORKSPACE_APPS),
                "workflow_mode": list(IMAGE_WORKFLOW_MODES),
                "rule": "Image workspace app and workflow mode are independent; Finish/Reference/Results never overwrite generate/edit/inpaint mode.",
            },
            "video": {
                "workspace_app": list(VIDEO_WORKSPACE_APPS),
                "generation_mode": list(VIDEO_GENERATION_MODES),
                "rule": "Video workspace app and generation type are independent; Results/Finish never overwrite Txt2Vid/Img2Vid type.",
            },
            "prompt_captioning": {
                "workspace_mode": ["prompt_builder", "captioning"],
                "rule": "Prompt/Captioning main mode and left child tab are stored under prompt_captioning runtime.",
            },
        },
        "defaults": deepcopy(DEFAULT_SURFACE_RUNTIME),
    }
