from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_ID: Final[str] = "neo.ui.strict_dom_ids.v25_8"
PHASE: Final[str] = "V25.8"

SURFACE_COMMAND_DOM_IDS: Final[dict[str, dict[str, str]]] = {
    "image": {
        "family": "imageWorkspaceFamily",
        "loader": "imageWorkspaceLoader",
        "workflow_mode": "imageWorkflowMode",
        "validate": "imageValidateBtn",
        "generate": "imageGenerateBtn",
        "pause": "imagePauseBtn",
        "stop": "imageStopBtn",
        "progress_label": "imageProgressLabel",
        "progress_fill": "imageProgressFill",
        "progress_elapsed": "imageProgressElapsed",
    },
    "video": {
        "family": "videoWorkspaceFamily",
        "loader": "videoWorkspaceLoader",
        "generation_mode": "videoGenerationMode",
        "compile": "videoCompileWanTxt2VidBtn",
        "generate": "videoGenerateWanTxt2VidBtn",
        "probe_backend": "videoRefreshBackendProbeBtn",
        "refresh_results": "videoRefreshResultsBtn",
        "progress_label": "videoProgressLabel",
        "progress_fill": "videoProgressFill",
        "progress_elapsed": "videoProgressElapsed",
    },
    "voice": {
        "family": "voiceWorkspaceFamily",
        "runtime": "voiceWorkspaceRuntime",
        "job_type": "voiceJobType",
        "probe_backend": "voiceRefreshAdapterBtn",
        "preview": "voicePreviewBtn",
        "render": "voiceRenderBtn",
        "refresh_results": "voiceRefreshHistoryBtn",
        "progress_label": "voiceProgressLabel",
        "progress_fill": "voiceProgressFill",
    },
    "prompt_captioning": {
        "workspace_mode": "promptCaptioningWorkspaceMode",
    },
    "generic": {
        "workspace_mode": "surfaceWorkspaceMode",
    },
}

RETIRED_ACTIVE_SURFACE_ALIASES: Final[tuple[str, ...]] = (
    "workspaceMode",
    "workspaceFamily",
    "workspaceLoader",
    "workspaceGenerateBtn",
    "workspaceValidateBtn",
    "workspacePauseBtn",
    "workspaceStopBtn",
    "workspaceProgressLabel",
    "workspaceProgressFill",
    "workspaceProgressElapsed",
)


def strict_dom_id_contract() -> dict[str, Any]:
    """Return the V25.8 strict DOM-id contract.

    V25.6 split command-strip renderers but kept active-surface DOM aliases.
    V25.8 retires those aliases from rendered command strips and event/progress
    binding so Image, Video, Voice, and Prompt/Captioning cannot fight over the
    same element IDs.
    """

    return {
        "schema_id": SCHEMA_ID,
        "phase": PHASE,
        "rule": "Command-strip controls must use surface-specific DOM ids; active-surface workspace* aliases are retired.",
        "surface_command_dom_ids": deepcopy(SURFACE_COMMAND_DOM_IDS),
        "retired_active_surface_aliases": list(RETIRED_ACTIVE_SURFACE_ALIASES),
        "allowed_legacy_state_aliases": ["activeSubtabId", "activeWorkspaceAppId", "activeSubtabsBySurface"],
        "allowed_legacy_state_alias_rule": "State aliases may mirror active surface only; DOM aliases are not rendered by command strips.",
    }


def surface_dom_ids(surface_id: str | None) -> dict[str, str]:
    surface = str(surface_id or "").strip() or "generic"
    return deepcopy(SURFACE_COMMAND_DOM_IDS.get(surface, SURFACE_COMMAND_DOM_IDS["generic"]))
