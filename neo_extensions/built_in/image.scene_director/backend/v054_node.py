"""Compatibility import for the executable Scene Director node.

The full V054 implementation lives in ``comfy_node/nodes.py`` so the manifest
and ComfyUI entrypoint resolve the same node class and output contract. This
module remains as a stable backend import for Neo's support/readiness code.
"""

from ..comfy_node.nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
    NeoSceneDirectorV054,
)

__all__ = [
    "NeoSceneDirectorV054",
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
]
