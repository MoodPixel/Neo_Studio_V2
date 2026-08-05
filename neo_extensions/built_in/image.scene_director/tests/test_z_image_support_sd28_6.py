from __future__ import annotations

import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest
import torch

EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
COMFY_NODE = EXT_ROOT / "comfy_node"
PACKAGE = "neo_scene_director_sd28_6_zimage_testpkg"


def _load_backend(name: str):
    if PACKAGE not in sys.modules:
        pkg = types.ModuleType(PACKAGE)
        pkg.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = pkg
    full = f"{PACKAGE}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = BACKEND / f"{name}.py"
    if name == "provider_capabilities" and not path.exists():
        module = types.ModuleType(full)
        module.resolve_provider_capabilities_v054 = lambda route=None, **kwargs: {"provider_profile": "test", "features": {}}
        sys.modules[full] = module
        return module
    if name == "prompt_authority" and not path.exists():
        module = types.ModuleType(full)
        module.PROMPT_AUTHORITY_GLOBAL_CONTEXT = "global_context"
        module.PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY = "scene_director_only"
        module.normalize_prompt_authority = lambda value, default="global_context": "scene_director_only" if str(value or "").strip().lower() in {"scene_director_only", "scene_director"} else "global_context"
        sys.modules[full] = module
        return module
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime():
    name = "neo_scene_director_sd28_6_zimage_runtime"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMFY_NODE / "regional_lora.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


execution_strategy = _load_backend("execution_strategy")
_ = _load_backend("provider_capabilities")
support_matrix = _load_backend("support_matrix")
z_support = _load_backend("z_image_support")
_ = _load_backend("prompt_authority")
regional = _load_backend("regional_lora_delta")
lightweight = _load_backend("lightweight_regional")
runtime = _load_runtime()
CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES)


def _region(idx: int = 1) -> dict:
    return {
        "id": f"character_{idx}", "label": f"Character {idx}", "type": "character",
        "bbox": {"x": 0.04 + (idx - 1) * 0.23, "y": 0.08, "w": 0.2, "h": 0.84},
        "prompt": f"character {idx}", "negative_prompt": "wrong identity", "strength": 1.0,
        "mask": {"feather": 8},
    }


def _binding(idx: int = 1, *, family: str | None = "z_image") -> dict:
    owner = {"trigger_words": f"z_trigger_{idx}"}
    if family is not None:
        owner["model_family"] = family
    return {
        "uid": f"binding_{idx}", "row_id": f"row_{idx}", "lora_row_id": f"row_{idx}",
        "region_id": f"character_{idx}", "region_index": idx,
        "name": f"z_character_{idx}.safetensors", "strength": 0.8, "target": "both",
        "source_record_trigger_words": f"record_trigger_{idx}", "owner_row": owner,
    }


def _validation(regions: list[dict], bindings: list[dict]) -> dict:
    return {
        "extension_id": "image.scene_director", "enabled": True, "ok": True,
        "can_emit_workflow_patch": True, "route_state": "available", "route": {},
        "subject_count": len(regions), "detail_region_count": 0,
        "block": {
            "enabled": True,
            "inputs": {"regions": deepcopy(regions), "global": {"prompt_authority": "global_context"}},
            "params": {"prompt_authority": "global_context"},
            "assets": {"lora_bindings": deepcopy(bindings)},
            "metadata": {"subject_count": len(regions), "detail_region_count": 0},
        },
        "validation": [], "node_status": {},
    }


def _route(*, family="z_image", loader="diffusion_model", mode="generate") -> dict:
    model = "z_image_turbo_Q4_K_M.gguf" if loader == "gguf" and family == "z_image_turbo" else (
        "z_image_Q8_0.gguf" if loader == "gguf" else ("z-image-turbo.safetensors" if family == "z_image_turbo" else "z-image.safetensors")
    )
    return {
        "backend": "comfyui", "family": family, "loader": loader, "mode": mode,
        "actual_params": {
            "width": 1024, "height": 768,
            "diffusion_model": model if loader == "diffusion_model" else "",
            "gguf_model": model if loader == "gguf" else "",
        },
    }


def _graph(*, family="z_image", mode="generate", steps=None, cfg=None) -> dict:
    turbo = family == "z_image_turbo"
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut" if turbo else "CLIPTextEncode", "inputs": {"conditioning": ["4", 0]} if turbo else {"text": "negative", "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "seed": 44, "steps": int(steps if steps is not None else (9 if turbo else 35)),
            "cfg": float(cfg if cfg is not None else (1.0 if turbo else 3.5)),
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
        }},
    }
    if mode == "inpaint":
        graph["10"] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["3", 0]}}
        graph["7"]["inputs"]["model"] = ["10", 0]
    return graph


def _apply(monkeypatch, *, family="z_image", loader="diffusion_model", mode="generate", regions=None, bindings=None, graph=None):
    regions = regions or [_region(1)]
    bindings = bindings if bindings is not None else [_binding(1, family=family)]
    graph = deepcopy(graph or _graph(family=family, mode=mode))
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(regions, bindings))
    return lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions": {"image.scene_director": {"enabled": True}}},
        route=_route(family=family, loader=loader, mode=mode),
        available_nodes=CORE | {"NeoRegionalLoRADelta"},
        model_ref=["1", 0], clip_ref=["2", 0], sampler_node_id="7",
    )


def _classes(graph: dict) -> list[str]:
    return [str(n.get("class_type") or "") for n in graph.values() if isinstance(n, dict)]


def test_zimage_base_and_turbo_native_gguf_generate_img2img_inpaint_are_available():
    for family in ("z_image", "z_image_turbo"):
        for loader in ("diffusion_model", "gguf"):
            for mode in ("generate", "img2img", "inpaint"):
                support = support_matrix.get_scene_director_support(_route(family=family, loader=loader, mode=mode))
                assert support["state"] == "available"
                assert support["execution_strategy"]["regional_lora"]["mode"] == "z_image_activation_delta_v1"
                assert support["execution_strategy"]["regional_lora"]["supported"] is True


def test_zimage_outpaint_remains_planned_gated():
    for family in ("z_image", "z_image_turbo"):
        assert support_matrix.get_scene_director_support(_route(family=family, loader="gguf", mode="outpaint"))["state"] == "planned_gated"


def test_zimage_profile_uses_official_6b_architecture_signature():
    profile = z_support.resolve_z_image_profile(_route(family="z_image_turbo"))
    assert profile["variant"] == "turbo"
    assert profile["expected_signature"] == {
        "dim": 3840, "in_channels": 16, "n_heads": 30,
        "main_layers": 30, "noise_refiner_layers": 2, "context_refiner_layers": 2, "patch_size": 2,
    }


def test_zimage_lora_family_compatibility_is_conservative_across_base_turbo():
    same = z_support.classify_z_image_binding_compatibility(_binding(family="z_image"), "z_image")
    cross = z_support.classify_z_image_binding_compatibility(_binding(family="z_image"), "z_image_turbo")
    bad = z_support.classify_z_image_binding_compatibility(_binding(family="sdxl"), "z_image")
    unknown = z_support.classify_z_image_binding_compatibility(_binding(family=None), "z_image")
    assert same["compatible"] is True
    assert cross["compatible"] is None and cross["state"] == "base_turbo_cross_variant_runtime_preflight_required"
    assert bad["compatible"] is False
    assert unknown["compatible"] is None


def test_zimage_base_sampler_profile_preserves_aura_flow_and_encoded_negative():
    result = z_support.validate_z_image_sampler_profile(_graph(family="z_image"), sampler_node_id="7", route=_route(family="z_image"))
    assert result["ok"] is True
    assert result["steps"] == 35 and result["cfg"] == 3.5
    assert result["negative_class"] == "CLIPTextEncode"
    assert result["model_sampling_aura_flow_present"] is True


def test_zimage_turbo_hard_profile_requires_9_steps_cfg1_zero_negative():
    good = z_support.validate_z_image_sampler_profile(_graph(family="z_image_turbo"), sampler_node_id="7", route=_route(family="z_image_turbo"))
    assert good["ok"] is True
    bad = z_support.validate_z_image_sampler_profile(_graph(family="z_image_turbo", steps=35, cfg=3.5), sampler_node_id="7", route=_route(family="z_image_turbo"))
    assert bad["ok"] is False
    assert any("9" in e for e in bad["errors"])


def test_zimage_sampler_profile_blocks_missing_model_sampling_aura_flow():
    graph = _graph(family="z_image")
    graph["7"]["inputs"]["model"] = ["1", 0]
    result = z_support.validate_z_image_sampler_profile(graph, sampler_node_id="7", route=_route(family="z_image"))
    assert result["ok"] is False
    assert any("ModelSamplingAuraFlow" in e for e in result["errors"])


def test_zimage_spatial_scope_excludes_context_and_timestep_paths():
    assert runtime.z_image_spatial_module_scope("x_embedder") == "image_unpadded"
    assert runtime.z_image_spatial_module_scope("noise_refiner.0.attention.qkv") == "image_only"
    assert runtime.z_image_spatial_module_scope("noise_refiner.1.feed_forward.w2") == "image_only"
    assert runtime.z_image_spatial_module_scope("layers.0.attention.out") == "combined_text_image"
    assert runtime.z_image_spatial_module_scope("layers.29.feed_forward.w3") == "combined_text_image"
    assert runtime.z_image_spatial_module_scope("final_layer.linear") == "combined_text_image"
    for name in ("context_refiner.0.attention.qkv", "cap_embedder.1", "t_embedder.mlp.0", "layers.0.adaLN_modulation.0", "layers.0.attention_norm1"):
        assert runtime.z_image_spatial_module_scope(name) is None


def test_zimage_runtime_signature_rejects_generic_lumina2_and_accepts_exact_signature(monkeypatch):
    class DM:
        dim = 3840
        in_channels = 16
        n_heads = 30
        patch_size = 2
        pad_tokens_multiple = 32
        layers = [object()] * 30
        noise_refiner = [object()] * 2
        context_refiner = [object()] * 2
    Lumina2 = type("Lumina2", (), {})
    base = Lumina2(); base.diffusion_model = DM()
    patcher = types.SimpleNamespace(model=base)
    runtime.comfy_model_base = None
    _, sig = runtime._require_z_image_model(patcher, "z_image")
    assert sig["z_image_signature_proven"] is True
    assert sig["variant_runtime_identity_proven"] is False
    base.diffusion_model.layers = [object()] * 24
    with pytest.raises(RuntimeError):
        runtime._require_z_image_model(patcher, "z_image")


def test_zimage_padding_mask_zeroes_caption_and_image_padding_tokens():
    class DM:
        pad_tokens_multiple = 8
    patcher = types.SimpleNamespace(model=types.SimpleNamespace(diffusion_model=DM()))
    proof = {}
    session = runtime._ZImageRegionalSession(patcher, [], seam_feather=0.0, runtime_proof=proof)
    session.image_masks = [torch.tensor([1, 1, 1, 1, 1, 1], dtype=torch.float32)]
    session.text_len = 8
    unpadded = session._full_mask(0, 6, 3, "image_unpadded").reshape(-1)
    image = session._full_mask(0, 8, 3, "image_only").reshape(-1)
    combined = session._full_mask(0, 16, 3, "combined_text_image").reshape(-1)
    unknown = session._full_mask(0, 15, 3, "combined_text_image").reshape(-1)
    assert torch.equal(unpadded, torch.ones(6))
    assert torch.equal(image, torch.tensor([1, 1, 1, 1, 1, 1, 0, 0], dtype=torch.float32))
    assert torch.count_nonzero(combined[:8]).item() == 0
    assert torch.equal(combined[8:], torch.tensor([1, 1, 1, 1, 1, 1, 0, 0], dtype=torch.float32))
    assert torch.count_nonzero(unknown).item() == 0


def test_zimage_inpaint_wrapper_is_upstream_of_aura_flow_and_differential(monkeypatch):
    result = _apply(monkeypatch, family="z_image", mode="inpaint")
    graph = result["workflow"]
    regional_id = result["workflow_patch"]["scene_director_regional_lora_nodes_added"][0]
    assert graph["3"]["inputs"]["model"] == [regional_id, 0]
    assert graph["10"]["inputs"]["model"] == ["3", 0]
    assert graph["7"]["inputs"]["model"] == ["10", 0]
    assert _classes(graph).count("KSampler") == 1
    assert result["workflow_patch"]["scene_director_z_image_sampler_profile"]["model_sampling_aura_flow_present"] is True


def test_zimage_four_regional_loras_use_one_wrapper_and_one_sampler(monkeypatch):
    regions = [_region(i) for i in range(1, 5)]
    bindings = [_binding(i, family="z_image") for i in range(1, 5)]
    result = _apply(monkeypatch, regions=regions, bindings=bindings)
    classes = _classes(result["workflow"])
    patch = result["workflow_patch"]
    assert patch["scene_director_regional_lora_contract"]["route_count"] == 4
    assert classes.count("NeoRegionalLoRADelta") == 1
    assert classes.count("KSampler") == 1
    assert "LoraLoader" not in classes and "LoraLoaderModelOnly" not in classes


def test_declared_non_zimage_lora_is_rejected_before_trigger_injection(monkeypatch):
    binding = _binding(1, family="sdxl")
    result = _apply(monkeypatch, bindings=[binding])
    patch = result["workflow_patch"]
    lane = patch["scene_director_lightweight_regional_prompt"]["positive_lanes"][0]
    assert "z_trigger_1" not in lane["prompt_with_regional_lora_triggers"]
    assert patch["scene_director_regional_lora_contract"]["route_count"] == 0
    assert patch["scene_director_regional_lora_contract"]["binding_compatibility"]["rejected_count"] == 1


def test_cross_variant_lora_remains_runtime_preflight_candidate_and_trigger_local(monkeypatch):
    binding = _binding(1, family="z_image")
    result = _apply(monkeypatch, family="z_image_turbo", bindings=[binding])
    compat = result["workflow_patch"]["scene_director_z_image_lora_compatibility"]
    assert compat["unknown_count"] == 1
    lane = result["workflow_patch"]["scene_director_lightweight_regional_prompt"]["positive_lanes"][0]
    assert "z_trigger_1" in lane["prompt_with_regional_lora_triggers"]
    assert result["workflow"]["7"]["inputs"]["steps"] == 9


def test_manifest_promotes_zimage_and_registers_sd28_6_contract():
    import json
    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.2.20"
    assert "backend/z_image_support.py" in manifest["asset_bundle"]["python"]
    phase = manifest["phase_sd_28_6_z_image_support"]
    assert phase["regional_lora"] == "z_image_activation_delta_v1"
    assert phase["single_sampler_required"] is True
    assert manifest["route_states"]["comfyui:z_image_turbo:gguf:inpaint"] == "available"
    assert manifest["route_states"]["comfyui:z_image:gguf:outpaint"] == "planned_gated"


def test_runtime_node_apply_selects_zimage_adapter_without_mutating_original(monkeypatch):
    class FakePatcher:
        def __init__(self):
            self.model = object()
            self.wrappers = []
            self.attachments = {}
        def clone(self):
            other = FakePatcher()
            other.model = self.model
            return other
        def add_wrapper_with_key(self, wrapper_type, key, wrapper):
            self.wrappers.append((wrapper_type, key, wrapper))
        def set_attachments(self, key, value):
            self.attachments[key] = value

    monkeypatch.setattr(runtime, "_require_z_image_model", lambda model, family: (object(), {"z_image_signature_proven": True}))
    monkeypatch.setattr(runtime, "build_z_image_region_entries", lambda model, routes: ([{
        "region_id": "character_1", "lora_name": "z.safetensors", "strength": 1.0,
        "bbox_norm": (0.0, 0.0, 1.0, 1.0), "modules": {},
    }], {"routes": [], "file_count": 1}))
    original = FakePatcher()
    patched, = runtime.NeoRegionalLoRADelta().apply(
        original,
        routes_json='[{"region_id":"character_1","lora_name":"z.safetensors","bbox":{"x":0,"y":0,"w":1,"h":1}}]',
        family="z_image_turbo", canvas_width=1024, canvas_height=768,
        seam_feather=0.0, sampler_count=1, loader="gguf", variant="turbo",
    )
    assert patched is not original
    assert original.wrappers == []
    assert len(patched.wrappers) == 1
    proof = patched.attachments[runtime.RUNTIME_ATTACHMENT_KEY]
    assert proof["phase"] == "SD-28.6"
    assert proof["adapter"] == "z_image_activation_delta_v1"
    assert proof["family"] == "z_image_turbo"
    assert proof["loader"] == "gguf"
    assert proof["global_model_mutation"] is False
