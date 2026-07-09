from __future__ import annotations

import json
from pathlib import Path

from neo_extensions.built_in.lora_stack.backend import support_matrix as matrix


def test_phase_i_support_matrix_exposes_required_state_vocab():
    assert matrix.AVAILABLE == "available"
    assert matrix.EXPERIMENTAL == "experimental_available"
    assert matrix.IMPLEMENTATION_TARGET == "implementation_target"
    assert matrix.PLANNED == "planned_gated"
    assert matrix.PROVIDER_GATED == "provider_gated"
    assert matrix.UNSUPPORTED == "unsupported"
    assert matrix.ACTIVE_STATES == {"available", "experimental_available"}
    assert "implementation_target" in matrix.GATED_STATES
    assert matrix.KNOWN_STATES == {
        "available",
        "experimental_available",
        "implementation_target",
        "planned_gated",
        "provider_gated",
        "unsupported",
    }


def test_phase_i_support_matrix_marks_checkpoint_routes_correctly():
    assert matrix.route_state("comfyui", "sdxl", "checkpoint", "generate") == "available"
    assert matrix.route_state("comfyui", "sdxl", "checkpoint", "txt2img") == "available"
    assert matrix.route_state("comfyui", "sdxl", "checkpoint", "inpaint") == "available"
    assert matrix.graph_patch_strategy("comfyui", "sdxl", "checkpoint", "generate") == "lora_loader_model_clip_chain"
    assert matrix.route_state("comfyui", "sd15", "checkpoint", "outpaint") == "experimental_available"
    assert matrix.route_state("comfyui", "sdxl", "checkpoint", "edit") == "unsupported"


def test_phase_i_support_matrix_marks_active_non_checkpoint_routes_experimental_without_fallbacks():
    assert matrix.route_state("comfyui", "flux", "diffusion_model", "generate") == "experimental_available"
    assert matrix.route_state("comfyui", "flux", "gguf", "generate") == "experimental_available"
    assert matrix.route_state("comfyui", "qwen_image", "gguf", "inpaint") == "experimental_available"
    assert matrix.route_state("comfyui", "qwen_image", "diffusion_model", "generate") == "experimental_available"
    assert matrix.route_state("comfyui", "qwen_rapid_aio", "gguf", "edit") == "experimental_available"
    assert matrix.route_state("comfyui", "qwen_rapid_aio", "checkpoint_aio", "generate") == "experimental_available"
    assert matrix.route_state("comfyui", "flux", "gguf", "img2img") == "experimental_available"
    assert matrix.route_state("comfyui", "flux", "gguf", "inpaint") == "experimental_available"
    assert matrix.route_state("comfyui", "flux", "gguf", "outpaint") == "experimental_available"
    assert matrix.route_state("comfyui", "flux2_klein", "diffusion_model", "edit") == "experimental_available"
    assert matrix.route_state("comfyui", "z_image", "gguf", "generate") == "experimental_available"
    assert matrix.route_state("comfyui", "hidream", "diffusion_model", "generate") == "experimental_available"
    assert matrix.graph_patch_strategy("comfyui", "qwen_image", "gguf", "generate") == "lora_loader_model_clip_consumer_rewire"


def test_phase_i_support_matrix_keeps_true_gates_and_unsupported_routes_gated():
    # L6 promotes compiler-profile-backed family routes to experimental, but real provider gates stay closed.
    assert matrix.route_state("comfyui", "wan_image", "gguf", "generate") == "provider_gated"
    assert matrix.route_state("comfyui", "hunyuan_image", "diffusion_model", "outpaint") == "provider_gated"
    assert matrix.route_state("comfyui", "hidream", "diffusion_model", "img2img") == "planned_gated"
    assert matrix.route_state("comfyui", "flux", "diffusion_model", "edit") == "unsupported"
    assert matrix.route_state("comfyui", "z_image_turbo", "gguf", "edit") == "unsupported"
    assert matrix.route_state("other_backend", "sdxl", "checkpoint", "generate") == "provider_gated"
    assert matrix.route_state("comfyui", "sdxl", "checkpoint", "results") == "unsupported"
    assert matrix.route_state("comfyui", "sdxl", "diffusion_model", "generate") == "unsupported"


def test_phase_i_support_matrix_rows_have_reasons_and_route_keys():
    rows = matrix.support_matrix()
    assert rows
    assert all(row["route_key"] == f"{row['family']}:{row['loader']}:{row['mode']}" for row in rows)
    assert all(row["reason"] for row in rows)
    assert all(row["route_state"] == row["state"] for row in rows)
    assert all("base_route_state" in row for row in rows)
    assert all("loader_node_class" in row for row in rows)
    assert all("requires_model" in row for row in rows)
    assert all("requires_clip" in row for row in rows)
    assert all("patch_profile_required" in row for row in rows)
    assert all("validated" in row for row in rows)
    assert all("enablement_pass" in row for row in rows)
    assert any(row["graph_patch"] == "lora_loader_model_clip_chain" for row in rows)
    assert any(row["graph_patch"] == "lora_loader_model_clip_consumer_rewire" for row in rows)
    assert any(row["graph_patch"] == "provider_specific" for row in rows)
    assert any(row["state"] == "provider_gated" for row in rows)
    assert matrix.IMPLEMENTATION_TARGET in matrix.KNOWN_STATES


def test_phase_i_workspace_support_matrix_is_explicit():
    workspaces = {row["workspace_app"]: row for row in matrix.workspace_support_matrix()}
    assert workspaces["assets"]["state"] == "available"
    assert workspaces["generations"]["state"] == "unsupported"
    assert workspaces["reference"]["state"] == "unsupported"
    assert workspaces["finish"]["state"] == "unsupported"
    assert workspaces["results"]["state"] == "unsupported"


def test_phase_i_manifest_route_states_match_support_matrix():
    manifest = json.loads(Path("neo_extensions/built_in/image.lora_stack/extension_manifest.json").read_text())
    states = manifest["route_states"]
    for key, state in matrix.manifest_route_states().items():
        assert states[key] == state
    assert manifest["supported_loaders"] == list(matrix.SUPPORTED_LOADERS)
    assert manifest["workflow_modes"] == list(matrix.SUPPORTED_MODES)
    assert manifest["supported_backends"] == list(matrix.SUPPORTED_BACKENDS)
    assert manifest["support_matrix_contract"] == matrix.manifest_sync_contract()
    assert manifest["support_matrix_contract"]["phase"] == "L6-family-enablements"
    assert manifest["support_matrix_contract"]["canonical_workspace_app"] == "assets"
    assert manifest["support_matrix_contract"]["workspace_apps"] == ["assets"]
    assert "compiler-owned LoRA patch profile" in manifest["support_matrix_contract"]["rule"]
    assert "LoRA Stack UI only inside Image Assets" in manifest["support_matrix_contract"]["l1"]
    assert "backend-prefixed" in manifest["support_matrix_contract"]["l2"]
    assert manifest["support_matrix_contract"]["patch_profile_contract"]["owner"] == "compiler"
    assert "model_ref" in manifest["support_matrix_contract"]["patch_profile_contract"]["required_fields"]
    assert "hardcoded" in manifest["support_matrix_contract"]["l4"]
    assert "strategy dispatcher" in manifest["support_matrix_contract"]["l5"]
    assert "LoraLoaderModelOnly" in manifest["support_matrix_contract"]["patch_node_classes"]
    assert "lora_loader_model_only_consumer_rewire" in manifest["support_matrix_contract"]["supported_patch_strategies"]
    assert "family routes" in manifest["support_matrix_contract"]["l6"]


def test_phase_i_l2_manifest_keys_include_backend_and_txt2img_aliases():
    manifest = json.loads(Path("neo_extensions/built_in/image.lora_stack/extension_manifest.json").read_text())
    states = manifest["route_states"]
    assert states["comfyui:sdxl:checkpoint:generate"] == "available"
    assert states["comfyui:sdxl:checkpoint:txt2img"] == "available"
    assert states["comfyui_portable:sdxl:checkpoint:txt2img"] == "available"
    assert states["sdxl:checkpoint:generate"] == "available"
    assert states["sdxl:checkpoint:txt2img"] == "available"
    assert states["comfyui:qwen_rapid_aio:checkpoint_aio:img2img"] == "experimental_available"
    assert states["qwen_rapid_aio:checkpoint_aio:img2img"] == "experimental_available"
    assert states["finish"] == "unsupported"
    assert states["results"] == "unsupported"


def test_phase_i_l2_support_matrix_snapshot_and_manifest_sync_helper_are_current():
    from neo_extensions.built_in.lora_stack.backend import manifest_sync

    manifest = json.loads(Path("neo_extensions/built_in/image.lora_stack/extension_manifest.json").read_text())
    snapshot = json.loads(Path("neo_extensions/built_in/image.lora_stack/backend/support_matrix_data.json").read_text())
    assert manifest_sync.manifest_is_synced(manifest) is True
    assert snapshot == matrix.support_matrix_snapshot()
    assert snapshot["checksum"] == manifest["support_matrix_contract"]["checksum"] == matrix.manifest_sync_checksum()
    assert snapshot["route_states"] == matrix.manifest_route_states()
