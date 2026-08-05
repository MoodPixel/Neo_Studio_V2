"""Built-in Image Scene Director extension.

The extension keeps the public V054 node for classic SD routes and registers the
small regional-LoRA runtime node used by the modern lightweight engine.
"""

EXTENSION_ID = "image.scene_director"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "image"
MOUNT_SUBTAB = "generations"

try:
    from .comfy_node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
except Exception:  # pragma: no cover - lightweight Neo metadata imports.
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
