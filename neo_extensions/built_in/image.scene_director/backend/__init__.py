"""Scene Director backend helpers.

SD-28.7 release-locks the frozen V054 implementation on disk while exposing a small
route-aware workflow dispatcher at the historical ``backend.workflow_patch``
import path. Unknown helper symbols are delegated by the dispatcher to the
legacy module, preserving existing imports/tests without editing the 11k-line
V054 patcher.
"""
from __future__ import annotations

import sys

from .v054_contract import normalize_scene_graph_v054, validate_scene_graph_v054
from .v054_node import NeoSceneDirectorV054, NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .provider_capabilities import resolve_provider_capabilities_v054
from .flux_adapter import build_flux_adapter_plan_v054
from .qwen_adapter import build_qwen_adapter_plan_v054
from .execution_strategy import resolve_scene_director_execution_strategy
from .release_lock import evaluate_scene_director_release_lock
from .inspector import build_scene_director_inspector
from . import workflow_dispatch as _workflow_dispatch
from . import payload_schema_dispatch as _payload_schema_dispatch

# Neo's core workflow hook historically imports
# ``neo_extensions.built_in.scene_director.backend.workflow_patch`` directly.
# Route that import through the SD-28.7 dispatcher without overwriting the frozen
# workflow_patch.py file that remains the classic V054 implementation source.
sys.modules[f"{__name__}.workflow_patch"] = _workflow_dispatch
sys.modules[f"{__name__}.payload_schema"] = _payload_schema_dispatch

__all__ = [
    "normalize_scene_graph_v054",
    "validate_scene_graph_v054",
    "NeoSceneDirectorV054",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "resolve_provider_capabilities_v054",
    "build_flux_adapter_plan_v054",
    "build_qwen_adapter_plan_v054",
    "resolve_scene_director_execution_strategy",
    "evaluate_scene_director_release_lock",
    "build_scene_director_inspector",
]
