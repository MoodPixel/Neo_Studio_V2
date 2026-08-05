from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
PACKAGE = "neo_scene_director_sd28_testpkg"


def _ensure_package():
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = package


def _load(name: str):
    _ensure_package()
    full_name = f"{PACKAGE}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    path = BACKEND / f"{name}.py"
    if name == "provider_capabilities" and not path.exists():
        module = types.ModuleType(full_name)
        module.resolve_provider_capabilities_v054 = lambda route=None, **kwargs: {"provider_profile": "test", "features": {}}
        sys.modules[full_name] = module
        return module
    spec = importlib.util.spec_from_file_location(full_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


execution_strategy = _load("execution_strategy")
provider_capabilities = _load("provider_capabilities")
support_matrix = _load("support_matrix")
regional_lora_delta = _load("regional_lora_delta")
node_decision = _load("node_decision")


CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES)


def test_sdxl_keeps_classic_v054_runtime_boundary():
    strategy = execution_strategy.resolve_scene_director_execution_strategy(
        {"backend": "comfyui", "family": "sdxl", "loader": "checkpoint", "mode": "generate"}
    )
    assert strategy["engine"] == "classic_v054"
    assert strategy["status"] == "active"
    assert strategy["execution_enabled"] is True
    assert strategy["workflow_patch_ready"] is True
    assert strategy["public_node"] == "NeoSceneDirectorV054"
    assert strategy["new_exported_node_required"] is False
    assert strategy["custom_scene_director_node_required"] is True
    assert strategy["repair_policy"]["heavy_sd_repairs_allowed"] is True


def test_sd15_keeps_existing_experimental_classic_route():
    strategy = execution_strategy.resolve_scene_director_execution_strategy(
        {"backend": "comfyui", "family": "sd15", "loader": "checkpoint", "mode": "img2img"}
    )
    assert strategy["engine"] == "classic_v054"
    assert strategy["status"] == "experimental"
    assert strategy["execution_enabled"] is True


def test_sd28_6_promotes_all_modern_lightweight_families():
    for family in ("krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"):
        strategy = execution_strategy.resolve_scene_director_execution_strategy(
            {"backend": "comfyui", "family": family, "loader": "diffusion_model", "mode": "generate"}
        )
        assert strategy["engine"] == "lightweight_regional"
        assert strategy["execution_enabled"] is True
        assert strategy["workflow_patch_ready"] is True
        assert strategy["regional_prompt"]["supported"] is True
        assert strategy["regional_prompt"]["mode"] == "masked_conditioning"
        assert strategy["status"] == "active"
        assert strategy["regional_prompt"]["implementation_state"] == "active"
        assert strategy["regional_lora"]["supported"] is True
        expected_mode = (
            "flux2_klein_activation_delta_v1" if family == "flux2_klein"
            else "z_image_activation_delta_v1" if family in {"z_image", "z_image_turbo"}
            else "krea2_activation_delta_v2"
        )
        assert strategy["regional_lora"]["mode"] == expected_mode
        assert strategy["regional_lora"]["implementation_state"] == "active"
        assert strategy["regional_lora"]["required_node"] == "NeoRegionalLoRADelta"
        assert strategy["sampler_policy"]["single_sampler_required"] is True
        assert strategy["repair_policy"]["heavy_sd_repairs_allowed"] is False
        assert strategy["fallback_policy"] == "never_fallback_to_classic_v054"


def test_zimage_gguf_is_promoted_to_available_lightweight_engine():
    support = support_matrix.get_scene_director_support(
        backend="comfyui", family="z_image_turbo", loader="gguf", workflow_mode="generate"
    )
    assert support["state"] == "available"
    assert support["workflow_patch_allowed"] is True
    assert support["requires_node"] is False
    assert support["execution_strategy"]["engine"] == "lightweight_regional"


def test_modern_checkpoint_loader_is_not_silently_accepted():
    support = support_matrix.get_scene_director_support(
        backend="comfyui", family="krea2", loader="checkpoint", workflow_mode="generate"
    )
    assert support["state"] == "unsupported"
    assert support["workflow_patch_allowed"] is False


def test_flux1_stays_unsupported_and_never_falls_back_to_v054():
    support = support_matrix.get_scene_director_support(
        backend="comfyui", family="flux", loader="diffusion_model", workflow_mode="generate"
    )
    assert support["state"] == "unsupported"
    assert support["execution_strategy"]["engine"] == "unsupported"
    assert support["workflow_patch_allowed"] is False


def test_existing_sdxl_support_state_is_unchanged():
    support = support_matrix.get_scene_director_support(
        backend="comfyui", family="sdxl", loader="checkpoint", workflow_mode="generate"
    )
    assert support["state"] == "available"
    assert support["route_state"] == "available"
    assert support["workflow_patch_allowed"] is True
    assert support["requires_node"] is True


def test_existing_sd15_support_state_is_unchanged():
    support = support_matrix.get_scene_director_support(
        backend="comfyui", family="sd15", loader="checkpoint", workflow_mode="inpaint"
    )
    assert support["state"] == "experimental_available"
    assert support["workflow_patch_allowed"] is True


def test_lightweight_readiness_does_not_require_v054_node():
    route = {"backend": "comfyui", "family": "krea2", "loader": "diffusion_model", "mode": "generate"}
    ready = node_decision.workflow_readiness(route=route, available_nodes=CORE, enabled=True)
    assert ready["workflow_patch_allowed"] is True
    assert ready["node_status"]["custom_scene_director_node_required"] is False
    assert ready["node_status"]["selected_node"] == "ComfyBuiltInMaskedRegionalConditioning"


def test_classic_readiness_still_requires_v054():
    route = {"backend": "comfyui", "family": "sdxl", "loader": "checkpoint", "mode": "generate"}
    blocked = node_decision.workflow_readiness(route=route, available_nodes=CORE, enabled=True)
    assert blocked["workflow_patch_allowed"] is False
    ready = node_decision.workflow_readiness(route=route, available_nodes=CORE | {"NeoSceneDirectorV054"}, enabled=True)
    assert ready["workflow_patch_allowed"] is True


def test_regional_lora_runtime_proof_contract_is_krea2_armed_but_not_gpu_proven():
    contract = regional_lora_delta.build_regional_lora_delta_contract(
        {"backend": "comfyui", "family": "krea2", "loader": "diffusion_model", "mode": "generate"},
        bindings=[{"region_id": "a", "row_id": "lora_1", "name": "person.safetensors", "strength": 1.0}],
        regions=[{"id": "a", "bbox": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.8}, "mask": {"feather": 8}}],
        canvas={"width": 1024, "height": 1024},
    )
    assert contract["execution_enabled"] is True
    assert contract["adapter"]["adapter"] == "krea2_activation_delta_v2"
    assert contract["runtime_gpu_proven"] is False
    assert contract["global_model_mutation_allowed"] is False
    proof = {
        "lora_loaded": True,
        "model_family_match": True,
        "region_mask_bound": True,
        "masked_delta_hook_active": True,
        "delta_eval_attempted": True,
        "delta_nonzero": True,
        "global_model_mutation": False,
        "sampler_count": 1,
        "forward_hooks_removed": True,
        "spatial_scope_filter_active": True,
        "loader_supported": True,
        "token_mask_scope_proven": True,
    }
    assert regional_lora_delta.validate_regional_lora_runtime_proof(proof)["ready"] is True
    assert regional_lora_delta.validate_regional_lora_runtime_proof(proof)["runtime_gpu_proven"] is False


def test_outpaint_stays_planned_gated():
    for family, loader in (("sdxl", "checkpoint"), ("krea2", "diffusion_model"), ("z_image_turbo", "gguf")):
        strategy = execution_strategy.resolve_scene_director_execution_strategy(
            {"backend": "comfyui", "family": family, "loader": loader, "mode": "outpaint"}
        )
        assert strategy["status"] == "planned_gated"
        assert strategy["execution_enabled"] is False


def test_non_comfy_routes_are_provider_gated():
    strategy = execution_strategy.resolve_scene_director_execution_strategy(
        {"backend": "cloud_api", "family": "krea2", "loader": "diffusion_model", "mode": "generate"}
    )
    assert strategy["status"] == "provider_gated"
    assert strategy["execution_enabled"] is False
