"""Built-in Image Scene Director extension skeleton.

The folder is intentionally self-contained: manifest, backend contracts, UI assets,
docs, and tests live under ``neo_extensions/built_in/image.scene_director``.
The import alias ``neo_extensions.built_in.scene_director`` is registered by
``neo_extensions.built_in`` because the on-disk folder keeps the canonical
extension id prefix.
"""

EXTENSION_ID = "image.scene_director"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "image"
MOUNT_SUBTAB = "generations"

# ComfyUI loaders may import the built-in extension root directly. Keep the
# canonical mapping identical to the manifest entrypoint.
try:
    from .comfy_node.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
except Exception:  # pragma: no cover - lightweight Neo metadata imports.
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
