from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import support_matrix

EXTENSION_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = EXTENSION_DIR / "extension_manifest.json"
SUPPORT_MATRIX_DATA_PATH = EXTENSION_DIR / "backend" / "support_matrix_data.json"


def build_manifest_updates() -> dict[str, Any]:
    """Return the manifest fields owned by support_matrix.py.

    The LoRA Stack manifest must not hand-maintain route states. This keeps
    backend-prefixed route keys, legacy unprefixed keys, generate/txt2img
    aliases, workspace keys, and checksum metadata in one generated bundle.
    """
    return {
        "supported_backends": list(support_matrix.SUPPORTED_BACKENDS),
        "supported_families": list(support_matrix.SUPPORTED_FAMILIES),
        "supported_loaders": list(support_matrix.SUPPORTED_LOADERS),
        "workflow_modes": list(support_matrix.SUPPORTED_MODES),
        "route_states": support_matrix.manifest_route_states(),
        "support_matrix_contract": support_matrix.manifest_sync_contract(),
    }


def apply_manifest_updates(manifest: dict[str, Any]) -> dict[str, Any]:
    updated = dict(manifest)
    updated.update(build_manifest_updates())
    updated["version"] = "0.1.0-p10-l6-family-enablements"
    updated["description"] = (
        "Built-in Image LoRA Stack extension with L6 family-by-family experimental enablement. "
        "Assets remains the canonical owner, Generation/Reference/Finish mount the shared stack, and graph execution uses compiler-declared patch profiles with family-specific route gating."
    )
    ui_schema = dict(updated.get("ui_schema") or {})
    ui_schema["phase"] = "L6"
    features = list(ui_schema.get("phase_l2_features") or [])
    for item in [
        "backend-prefixed route state keys",
        "legacy route key compatibility",
        "generate/txt2img route aliases",
        "support matrix checksum contract",
    ]:
        if item not in features:
            features.append(item)
    ui_schema["phase_l2_features"] = features
    l4_features = list(ui_schema.get("phase_l4_features") or [])
    for item in [
        "compiler-owned LoRA patch profiles",
        "profile-required route gating",
        "no hardcoded non-checkpoint model/clip refs",
        "patch profile metadata in workflow patches",
    ]:
        if item not in l4_features:
            l4_features.append(item)
    ui_schema["phase_l4_features"] = l4_features

    l5_features = list(ui_schema.get("phase_l5_features") or [])
    for item in [
        "strategy-dispatched LoRA graph patching",
        "standard LoraLoader model+clip strategy",
        "LoraLoaderModelOnly strategy",
        "provider-specific adapter placeholder strategy",
        "explicit no-op strategy with metadata preservation",
    ]:
        if item not in l5_features:
            l5_features.append(item)
    ui_schema["phase_l5_features"] = l5_features

    l6_features = list(ui_schema.get("phase_l6_features") or [])
    for item in [
        "family-by-family experimental LoRA route enablement",
        "Flux / Flux 2 Klein compiler-profile-backed LoRA routes",
        "Qwen Image / Rapid AIO / Edit 2509 compiler-profile-backed LoRA routes",
        "ZImage / ZImage Turbo compiler-profile-backed LoRA routes",
        "HiDream txt2img only with image modes kept gated",
        "Wan and Hunyuan provider-specific gates preserved",
    ]:
        if item not in l6_features:
            l6_features.append(item)
    ui_schema["phase_l6_features"] = l6_features
    updated["ui_schema"] = ui_schema
    bundle = dict(updated.get("asset_bundle") or {})
    python_assets = list(bundle.get("python") or [])
    for rel in ["backend/manifest_sync.py", "backend/support_matrix_data.json", "backend/patch_profile.py"]:
        if rel not in python_assets:
            python_assets.append(rel)
    bundle["python"] = python_assets
    node_requirements = dict(updated.get("node_requirements") or {})
    optional_nodes = list(node_requirements.get("optional") or [])
    for node_name in ["LoraLoader", "LoraLoaderModelOnly"]:
        if node_name not in optional_nodes:
            optional_nodes.append(node_name)
    node_requirements["optional"] = optional_nodes
    node_requirements["source"] = "standard_comfy_nodes"
    notes = list(node_requirements.get("notes") or [])
    l5_note = "L5 supports standard Comfy LoraLoader and LoraLoaderModelOnly through route/strategy-gated compiler patch profiles."
    if l5_note not in notes:
        notes.append(l5_note)
    l6_note = "L6 promotes only compiler-profile-backed family routes to experimental; available remains reserved for physically validated checkpoint routes."
    if l6_note not in notes:
        notes.append(l6_note)
    node_requirements["notes"] = notes
    updated["node_requirements"] = node_requirements
    updated["asset_bundle"] = bundle
    return updated


def sync_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    updated = apply_manifest_updates(manifest)
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    SUPPORT_MATRIX_DATA_PATH.write_text(json.dumps(support_matrix.support_matrix_snapshot(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return updated


def manifest_is_synced(manifest: dict[str, Any]) -> bool:
    expected = build_manifest_updates()
    return all(manifest.get(key) == value for key, value in expected.items())


if __name__ == "__main__":  # pragma: no cover - manual sync helper
    sync_manifest()
