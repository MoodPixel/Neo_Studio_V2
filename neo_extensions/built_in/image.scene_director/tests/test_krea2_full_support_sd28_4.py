from __future__ import annotations

import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

import torch

EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
COMFY_NODE = EXT_ROOT / "comfy_node"
PACKAGE = "neo_scene_director_sd28_4_krea_testpkg"


def _load_backend(name: str):
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = package
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
        module.normalize_prompt_authority = lambda value, default="global_context": (
            "scene_director_only" if str(value or "").strip().lower() in {"scene_director_only", "scene_director"} else "global_context"
        )
        sys.modules[full] = module
        return module
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime():
    name = "neo_scene_director_sd28_4_krea_runtime"
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
krea2_support = _load_backend("krea2_support")
_ = _load_backend("prompt_authority")
regional = _load_backend("regional_lora_delta")
lightweight = _load_backend("lightweight_regional")
runtime = _load_runtime()
CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES)


def _region(idx: int = 1) -> dict:
    return {
        "id": f"character_{idx}",
        "label": f"Character {idx}",
        "type": "character",
        "bbox": {"x": 0.05 + (idx - 1) * 0.22, "y": 0.1, "w": 0.2, "h": 0.8},
        "prompt": f"character {idx}",
        "negative_prompt": "wrong identity",
        "strength": 1.0,
        "mask": {"feather": 8},
    }


def _binding(idx: int = 1, declared_family: str | None = "krea2") -> dict:
    owner = {"trigger_words": f"krea_trigger_{idx}"}
    if declared_family is not None:
        owner["model_family"] = declared_family
    return {
        "uid": f"binding_{idx}",
        "row_id": f"row_{idx}",
        "lora_row_id": f"row_{idx}",
        "region_id": f"character_{idx}",
        "region_index": idx,
        "name": f"krea_character_{idx}.safetensors",
        "strength": 0.8,
        "target": "both",
        "source_record_trigger_words": f"record_trigger_{idx}",
        "owner_row": owner,
    }


def _validation(regions: list[dict], bindings: list[dict]) -> dict:
    return {
        "extension_id": "image.scene_director",
        "enabled": True,
        "ok": True,
        "can_emit_workflow_patch": True,
        "route_state": "available",
        "route": {},
        "subject_count": len(regions),
        "detail_region_count": 0,
        "block": {
            "enabled": True,
            "inputs": {"regions": deepcopy(regions), "global": {"prompt_authority": "global_context"}},
            "params": {"prompt_authority": "global_context"},
            "assets": {"lora_bindings": deepcopy(bindings)},
            "metadata": {"subject_count": len(regions), "detail_region_count": 0},
        },
        "validation": [],
        "node_status": {},
    }


def _graph(*, turbo: bool, steps: int | None = None, cfg: float | None = None) -> dict:
    steps = (8 if turbo else 52) if steps is None else steps
    cfg = (1.0 if turbo else 3.5) if cfg is None else cfg
    return {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
        "5": {
            "class_type": "ConditioningZeroOut" if turbo else "CLIPTextEncode",
            "inputs": {"conditioning": ["4", 0]} if turbo else {"text": "negative", "clip": ["2", 0]},
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "seed": 11, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
        }},
    }


def _apply(monkeypatch, *, family="krea2", loader="diffusion_model", regions=None, bindings=None, graph=None):
    regions = regions or [_region(1)]
    bindings = bindings if bindings is not None else [_binding(1)]
    graph = deepcopy(graph or _graph(turbo=family == "krea2_turbo"))
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(regions, bindings))
    return lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions": {"image.scene_director": {"enabled": True}}},
        route={"backend": "comfyui", "family": family, "loader": loader, "mode": "generate", "actual_params": {"width": 1024, "height": 768}},
        available_nodes=CORE | {"NeoRegionalLoRADelta"},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id="7",
    )


def _classes(graph: dict) -> list[str]:
    return [str(node.get("class_type") or "") for node in graph.values() if isinstance(node, dict)]


def test_krea_raw_and_turbo_are_available_for_components_and_gguf():
    for family in ("krea2", "krea2_turbo"):
        for loader in ("diffusion_model", "gguf"):
            for mode in ("generate", "img2img", "inpaint"):
                support = support_matrix.get_scene_director_support({"backend": "comfyui", "family": family, "loader": loader, "mode": mode})
                assert support["state"] == "available"
                strategy = support["execution_strategy"]
                assert strategy["status"] == "active"
                assert strategy["regional_prompt"]["supported"] is True
                assert strategy["regional_lora"]["supported"] is True
                assert strategy["regional_lora"]["mode"] == "krea2_activation_delta_v2"


def test_krea_outpaint_stays_planned_gated():
    support = support_matrix.get_scene_director_support({"backend": "comfyui", "family": "krea2_turbo", "loader": "diffusion_model", "mode": "outpaint"})
    assert support["state"] == "planned_gated"


def test_turbo_profile_requires_8_steps_cfg1_and_zero_negative():
    good = krea2_support.validate_krea2_sampler_profile(_graph(turbo=True), sampler_node_id="7", family="krea2_turbo", loader="diffusion_model")
    assert good["ok"] is True
    assert good["steps"] == 8 and good["cfg"] == 1.0
    assert good["negative_class"] == "ConditioningZeroOut"

    bad_graph = _graph(turbo=True, steps=52, cfg=3.5)
    bad = krea2_support.validate_krea2_sampler_profile(bad_graph, sampler_node_id="7", family="krea2_turbo", loader="diffusion_model")
    assert bad["ok"] is False
    assert any("8" in item for item in bad["errors"])


def test_invalid_turbo_profile_fails_closed_without_scene_director_mutation(monkeypatch):
    graph = _graph(turbo=True, steps=52, cfg=3.5)
    before = deepcopy(graph)
    result = _apply(monkeypatch, family="krea2_turbo", graph=graph)
    assert result["mutated"] is False
    assert result["workflow"] == before
    assert "sampler profile validation failed" in result["workflow_patch"]["reason"].lower()
    assert "NeoRegionalLoRADelta" not in _classes(result["workflow"])


def test_raw_profile_preserves_provider_or_user_sampler_values(monkeypatch):
    graph = _graph(turbo=False, steps=44, cfg=4.25)
    result = _apply(monkeypatch, family="krea2", graph=graph)
    sampler = result["workflow"]["7"]["inputs"]
    assert sampler["steps"] == 44
    assert sampler["cfg"] == 4.25
    assert result["workflow_patch"]["scene_director_krea2_sampler_profile"]["ok"] is True


def test_raw_and_turbo_lora_cross_variant_metadata_is_accepted():
    raw_to_turbo = krea2_support.classify_krea2_binding_compatibility(_binding(1, "krea2_raw"), "krea2_turbo")
    turbo_to_raw = krea2_support.classify_krea2_binding_compatibility(_binding(1, "krea2_turbo"), "krea2")
    assert raw_to_turbo["compatible"] is True and raw_to_turbo["cross_variant_compatible"] is True
    assert turbo_to_raw["compatible"] is True and turbo_to_raw["cross_variant_compatible"] is True


def test_declared_non_krea_lora_is_rejected_before_wrapper_and_trigger_injection(monkeypatch):
    bad = _binding(1, "sdxl")
    result = _apply(monkeypatch, bindings=[bad])
    patch = result["workflow_patch"]
    compatibility = patch["scene_director_krea2_lora_compatibility"]
    assert compatibility["accepted_count"] == 0
    assert compatibility["rejected_count"] == 1
    assert patch["scene_director_regional_lora_applied"] is False
    assert "NeoRegionalLoRADelta" not in _classes(result["workflow"])
    prompt = patch["scene_director_lightweight_regional_prompt"]["positive_lanes"][0]["prompt_with_regional_lora_triggers"]
    assert "record_trigger_1" not in prompt
    assert "krea_trigger_1" not in prompt


def test_unknown_lora_family_is_allowed_only_as_runtime_preflight_candidate():
    filtered = krea2_support.filter_krea2_bindings([_binding(1, None)], "krea2")
    assert filtered["accepted_count"] == 1
    assert filtered["unknown_count"] == 1
    assert filtered["accepted"][0]["krea2_compatibility"]["state"] == "unknown_runtime_preflight_required"


def test_krea_spatial_scope_policy_excludes_text_and_timestep_modules():
    assert runtime.krea2_spatial_module_scope("first") == "image_only"
    assert runtime.krea2_spatial_module_scope("blocks.0.attn.qkv") == "combined_text_image"
    assert runtime.krea2_spatial_module_scope("last.linear") == "combined_text_image"
    for name in ("txtfusion", "txtmlp", "tmlp", "tproj", "txtfusion.proj"):
        assert runtime.krea2_spatial_module_scope(name) is None


def test_linear_dimension_contract_does_not_require_reading_quantized_weight_tensor():
    class QuantizedStyleLinear:
        in_features = 16
        out_features = 32
        weight = object()

    assert runtime._linear_dimensions(QuantizedStyleLinear()) == (16, 32)


def test_scope_masks_protect_text_tokens_and_unknown_layout_fails_closed():
    image_mask = torch.tensor([1.0, 0.5, 0.0, 1.0])
    image_only = runtime.sequence_mask_for_scope(image_mask, seq_len=4, ndim=3, scope="image_only", text_len=None).reshape(-1)
    assert torch.equal(image_only, image_mask)
    mixed = runtime.sequence_mask_for_scope(image_mask, seq_len=7, ndim=3, scope="combined_text_image", text_len=3).reshape(-1)
    assert torch.count_nonzero(mixed[:3]) == 0
    assert torch.equal(mixed[3:], image_mask)
    unknown = runtime.sequence_mask_for_scope(image_mask, seq_len=9, ndim=3, scope="combined_text_image", text_len=None)
    assert torch.count_nonzero(unknown) == 0


def test_four_regional_loras_use_one_model_wrapper_and_one_sampler(monkeypatch):
    regions = [_region(i) for i in range(1, 5)]
    bindings = [_binding(i, "krea2") for i in range(1, 5)]
    result = _apply(monkeypatch, regions=regions, bindings=bindings)
    classes = _classes(result["workflow"])
    assert classes.count("NeoRegionalLoRADelta") == 1
    assert classes.count("KSampler") == 1
    assert "KSamplerAdvanced" not in classes
    assert "LoraLoader" not in classes
    assert "LoraLoaderModelOnly" not in classes
    contract = result["workflow_patch"]["scene_director_regional_lora_contract"]
    assert contract["route_count"] == 4
    assert contract["route_limit"] is None


def test_gguf_route_passes_loader_identity_into_runtime_wrapper(monkeypatch):
    result = _apply(monkeypatch, loader="gguf")
    node_id = result["workflow_patch"]["scene_director_regional_lora_nodes_added"][0]
    node = result["workflow"][node_id]
    assert node["class_type"] == "NeoRegionalLoRADelta"
    assert node["inputs"]["loader"] == "gguf"
    assert result["workflow_patch"]["scene_director_regional_lora_contract"]["krea2_full_support"]["supported"] is True


def test_runtime_node_exposes_loader_as_optional_for_saved_workflow_compatibility():
    inputs = runtime.NeoRegionalLoRADelta.INPUT_TYPES()
    assert "loader" not in inputs["required"]
    assert "loader" in inputs["optional"]
    assert set(inputs["optional"]["loader"][0]) == {"diffusion_model", "gguf"}
