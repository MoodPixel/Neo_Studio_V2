from __future__ import annotations

from typing import Any

EXTENSION_ID = "image.scene_director"
EXTENSION_TYPE = "built_in"
SURFACE = "image"
WORKSPACE_APP = "generations"
TARGET_SUBTAB = "generations"
WORKFLOW_MODES = ("generate", "img2img", "inpaint")
CANONICAL_MOUNT_SLOT = "image.generate.scene_director"
UI_MOUNT_KIND = "direct_workspace"
EXTERNAL_SECTION_ALLOWED = False


def scene_director_mount_contract() -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "surface": SURFACE,
        "workspace_app": WORKSPACE_APP,
        "target_subtab": TARGET_SUBTAB,
        "workflow_modes": list(WORKFLOW_MODES),
        "canonical_mount_slot": CANONICAL_MOUNT_SLOT,
        "ui_mount_kind": UI_MOUNT_KIND,
        "external_section_allowed": EXTERNAL_SECTION_ALLOWED,
        "phase": "F",
    }


def record_mounts_as_direct_built_in(record: dict[str, Any]) -> bool:
    manifest = record.get("manifest") or {}
    origin = record.get("origin") or manifest.get("extension_origin")
    if manifest.get("id") != EXTENSION_ID:
        return False
    if origin != EXTENSION_TYPE:
        return False
    if manifest.get("surface") != SURFACE:
        return False
    workspace_apps = manifest.get("workspace_apps") or []
    if WORKSPACE_APP not in workspace_apps:
        return False
    slots = manifest.get("mount_slots") or []
    return CANONICAL_MOUNT_SLOT in slots
