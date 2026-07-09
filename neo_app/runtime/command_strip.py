from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_ID: Final[str] = "neo.ui.command_strip.v25_6"
PHASE: Final[str] = "V25.6"

SURFACE_COMMAND_STRIPS: Final[dict[str, dict[str, Any]]] = {
    "image": {
        "strip_id": "image-generation-command-strip",
        "owns": ["workflow_mode", "family", "loader", "preflight", "generate", "pause", "stop", "recover_output", "progress"],
        "may_read": ["backend_profile", "surface_runtime.image", "imageDraft", "activeImageJob"],
        "must_not_write": ["video.generation_mode", "prompt_captioning.workspace_mode", "voice.job_type"],
    },
    "video": {
        "strip_id": "video-generation-command-strip",
        "owns": ["generation_mode", "family", "loader", "compile", "generate", "probe_backend", "refresh_results", "progress"],
        "may_read": ["backend_profile", "surface_runtime.video", "videoDraft"],
        "must_not_write": ["image.workflow_mode", "prompt_captioning.workspace_mode", "voice.job_type"],
    },
    "voice": {
        "strip_id": "voice-generation-command-strip",
        "owns": ["job_type", "family", "runtime", "preview", "render", "probe_backend", "refresh_results", "progress"],
        "may_read": ["backend_profile", "surface_runtime.voice", "voiceDraft"],
        "must_not_write": ["image.workflow_mode", "video.generation_mode", "prompt_captioning.workspace_mode"],
    },
    "prompt_captioning": {
        "strip_id": "prompt-captioning-command-strip",
        "owns": ["workspace_mode", "studio_assist_summary", "run_note"],
        "may_read": ["surface_runtime.prompt_captioning", "promptCaptioning"],
        "must_not_write": ["image.workflow_mode", "video.generation_mode", "voice.job_type"],
    },
}

BACKEND_COMMAND_STRIP: Final[dict[str, Any]] = {
    "strip_id": "backend",
    "owns": ["backend_profile_selection", "connect", "disconnect", "connection_status"],
    "scope_rule": "The backend card may change only the active surface backend profile and connection state.",
}


def command_strip_contract() -> dict[str, Any]:
    """Return the locked V25.6 command-strip contract.

    The frontend uses this contract shape as a stable implementation target: the
    workspace header may orchestrate cards, but surface-specific controls must be
    rendered by surface-owned command strips.
    """

    return {
        "schema_id": SCHEMA_ID,
        "phase": PHASE,
        "state_owner": "surfaceRuntime + surface draft state",
        "header_role": "orchestrator only",
        "backend_strip": deepcopy(BACKEND_COMMAND_STRIP),
        "surface_strips": deepcopy(SURFACE_COMMAND_STRIPS),
        "rules": [
            "renderWorkspaceHeader may compose cards, but it must not own surface-specific route/action HTML.",
            "Each runtime surface has a dedicated command strip renderer and command-strip marker.",
            "Image workflow mode and Video generation mode remain separate from workspace navigation.",
            "Prompt/Captioning and Voice controls cannot reuse Image/Video command ownership.",
            "Command-strip DOM ids are surface-specific; active-surface workspace* DOM aliases are retired by V25.8.",
        ],
    }


def normalize_command_strip(surface_id: str | None) -> dict[str, Any]:
    surface = str(surface_id or "").strip() or "generic"
    if surface in SURFACE_COMMAND_STRIPS:
        payload = deepcopy(SURFACE_COMMAND_STRIPS[surface])
    else:
        payload = {"strip_id": "generic-command-strip", "owns": ["workspace_mode", "runtime_profile"], "may_read": ["surface_runtime"], "must_not_write": []}
    payload.update({"schema_id": SCHEMA_ID, "phase": PHASE, "surface_id": surface})
    return payload
