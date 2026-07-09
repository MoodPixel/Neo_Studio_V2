"""CFG Fix / Dynamic Thresholding built-in extension backend contracts.

Phase E adds extension-local Comfy workflow graph patching after server-side payload normalization and route/node validation.
"""

EXTENSION_ID = "cfg_fix_dynamic_thresholding"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "generations"
WORKFLOW_MODE = "generate"
PHASE = "E"

from .validation import validate_and_normalize_payload  # noqa: E402,F401

from .workflow_patch import apply_cfg_fix_dynamic_thresholding_patch  # noqa: E402,F401
