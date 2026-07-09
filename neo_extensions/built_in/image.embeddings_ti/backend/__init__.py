"""Embeddings / Textual Inversion built-in extension backend contracts.

Phase B creates the extension-local skeleton and safe contract helpers only.
Prompt/workflow mutation is intentionally deferred to later phases.
"""

EXTENSION_ID = "embeddings_ti"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "assets"
PHASE = "B"

from .validation import validate_and_normalize_payload  # noqa: E402,F401
from .workflow_patch import apply_embeddings_ti_patch  # noqa: E402,F401
