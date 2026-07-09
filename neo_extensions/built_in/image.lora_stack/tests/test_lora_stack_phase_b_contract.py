from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EXTENSION_DIR = ROOT / "neo_extensions" / "built_in" / "image.lora_stack"

def manifest() -> dict:
    return json.loads((EXTENSION_DIR / "extension_manifest.json").read_text(encoding="utf-8"))

def test_lora_stack_manifest_declares_built_in_assets_only_image_workspace_extension():
    data = manifest()
    assert data["id"] == "lora_stack"
    assert data["extension_origin"] == "built_in"
    assert data["surface"] == "image"
    assert data["workspace_apps"] == ["assets"]
    assert data["mount_slots"] == ["image.assets.lora_stack"]
    targets = {item["workspace_app"]: item for item in data["mount_targets"]}
    assert set(targets) == {"assets"}
    assert targets["assets"]["mount_role"] == "canonical_owner"

def test_lora_stack_manifest_declares_library_and_stack_assets():
    bundle = manifest()["asset_bundle"]
    for rel in bundle["html"] + bundle["js"] + bundle["css"] + bundle["python"]:
        assert (EXTENSION_DIR / rel).exists(), rel

def test_lora_stack_route_matrix_enables_active_comfy_families():
    import importlib.util
    support_path = EXTENSION_DIR / "backend" / "support_matrix.py"
    spec = importlib.util.spec_from_file_location("lora_stack_support_matrix", support_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.route_state("comfyui", "sdxl", "checkpoint", "generate") == "available"
    assert module.route_state("comfyui", "sd15", "checkpoint", "generate") == "experimental_available"
    assert module.route_state("comfyui", "flux", "gguf", "generate") == "experimental_available"
    assert module.route_state("comfyui", "qwen_image", "gguf", "generate") == "experimental_available"
    assert module.route_state("comfyui", "wan_image", "gguf", "generate") == "provider_gated"

def test_lora_stack_payload_contract_normalizes_rows_and_legacy_payload():
    import importlib.util
    payload_path = EXTENSION_DIR / "backend" / "payload_schema.py"
    spec = importlib.util.spec_from_file_location("lora_stack_payload_schema", payload_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migrated = module.migrate_legacy_payload({"lora_enabled": True, "lora_name": "foo.safetensors", "lora_strength": "0.9"})
    block = migrated["extensions"]["lora_stack"]
    assert block["enabled"] is True
    assert block["params"]["loras"][0]["name"] == "foo.safetensors"
    assert block["params"]["loras"][0]["strength"] == 0.9

def test_lora_library_scaffold_includes_civitai_merge_modes_and_metadata_reader():
    text = (EXTENSION_DIR / "backend" / "civitai_import.py").read_text(encoding="utf-8")
    assert "parse_civitai_url" in text
    assert "modelVersionId" in text
    merge = (EXTENSION_DIR / "backend" / "merge_policy.py").read_text(encoding="utf-8")
    for mode in ["fill_missing", "smart_merge", "overwrite_selected", "previews_only"]:
        assert mode in merge
    metadata = (EXTENSION_DIR / "backend" / "metadata_reader.py").read_text(encoding="utf-8")
    assert "ss_tag_frequency" in metadata
    assert "trainedWords" in metadata
