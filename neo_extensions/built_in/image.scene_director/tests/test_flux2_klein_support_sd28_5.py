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
PACKAGE = "neo_scene_director_sd28_5_klein_testpkg"


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
    name = "neo_scene_director_sd28_5_klein_runtime"
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
klein_support = _load_backend("flux2_klein_support")
_ = _load_backend("prompt_authority")
regional = _load_backend("regional_lora_delta")
lightweight = _load_backend("lightweight_regional")
runtime = _load_runtime()
CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES) | {"FluxGuidance"}


def _region(idx: int = 1) -> dict:
    return {
        "id": f"character_{idx}", "label": f"Character {idx}", "type": "character",
        "bbox": {"x": 0.05 + (idx - 1) * 0.22, "y": 0.1, "w": 0.2, "h": 0.8},
        "prompt": f"character {idx}", "negative_prompt": "wrong identity", "strength": 1.0,
        "mask": {"feather": 8},
    }


def _binding(idx: int = 1, *, scale: str = "4b", kind: str = "distilled", family: str | None = "flux2_klein") -> dict:
    owner = {"trigger_words": f"klein_trigger_{idx}", "flux_variant": f"klein_{scale}_{kind}"}
    if family is not None:
        owner["model_family"] = family
    return {
        "uid": f"binding_{idx}", "row_id": f"row_{idx}", "lora_row_id": f"row_{idx}",
        "region_id": f"character_{idx}", "region_index": idx,
        "name": f"klein_{scale}_{kind}_character_{idx}.safetensors", "strength": 0.8, "target": "both",
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


def _graph(*, steps: int = 4, cfg: float = 1.0, mode: str = "generate") -> dict:
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": 1.0}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "8": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
        "9": {"class_type": "KSampler", "inputs": {
            "seed": 11, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0],
        }},
    }
    if mode == "inpaint":
        graph["10"] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["1", 0]}}
        graph["9"]["inputs"]["model"] = ["10", 0]
    return graph


def _route(*, loader="diffusion_model", scale="4b", kind="distilled", mode="generate") -> dict:
    suffix = "-base" if kind == "base" else ""
    model = f"flux-2-klein-{scale}{suffix}.gguf" if loader == "gguf" else f"flux-2-klein-{scale}{suffix}.safetensors"
    variant = f"klein_{scale}" if kind == "distilled" else f"klein_{scale}_base"
    return {
        "backend": "comfyui", "family": "flux2_klein", "loader": loader, "mode": mode,
        "actual_params": {"width": 1024, "height": 768, "flux_variant": variant, "diffusion_model": model if loader == "diffusion_model" else "", "gguf_model": model if loader == "gguf" else ""},
    }


def _apply(monkeypatch, *, loader="diffusion_model", scale="4b", kind="distilled", regions=None, bindings=None, graph=None, mode="generate"):
    regions = regions or [_region(1)]
    bindings = bindings if bindings is not None else [_binding(1, scale=scale, kind=kind)]
    graph = deepcopy(graph or _graph(mode=mode))
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(regions, bindings))
    return lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions": {"image.scene_director": {"enabled": True}}},
        route=_route(loader=loader, scale=scale, kind=kind, mode=mode),
        available_nodes=CORE | {"NeoRegionalLoRADelta"},
        model_ref=["1", 0], clip_ref=["2", 0], sampler_node_id="9",
    )


def _classes(graph: dict) -> list[str]:
    return [str(n.get("class_type") or "") for n in graph.values() if isinstance(n, dict)]


def test_klein_native_and_gguf_are_available_for_generate_img2img_inpaint():
    for loader in ("diffusion_model", "gguf"):
        for mode in ("generate", "img2img", "inpaint"):
            support = support_matrix.get_scene_director_support({"backend": "comfyui", "family": "flux2_klein", "loader": loader, "mode": mode})
            assert support["state"] == "available"
            strategy = support["execution_strategy"]
            assert strategy["status"] == "active"
            assert strategy["regional_prompt"]["supported"] is True
            assert strategy["regional_lora"]["supported"] is True
            assert strategy["regional_lora"]["mode"] == "flux2_klein_activation_delta_v1"


def test_klein_outpaint_remains_planned_gated():
    assert support_matrix.get_scene_director_support({"backend": "comfyui", "family": "flux2_klein", "loader": "gguf", "mode": "outpaint"})["state"] == "planned_gated"


def test_klein_profile_resolves_4b_9b_and_base_distilled():
    p4 = klein_support.resolve_klein_profile(_route(scale="4b", kind="distilled"))
    p9 = klein_support.resolve_klein_profile(_route(scale="9b", kind="base"))
    assert (p4["scale"], p4["variant_kind"]) == ("4b", "distilled")
    assert p4["expected_signature"] == {"double_blocks": 5, "single_blocks": 20, "transformer_hidden_reference": 3072}
    assert (p9["scale"], p9["variant_kind"]) == ("9b", "base")
    assert p9["expected_signature"] == {"double_blocks": 8, "single_blocks": 24, "transformer_hidden_reference": 4096}


def test_klein_lora_scale_mismatch_is_rejected():
    bad = klein_support.classify_klein_binding_compatibility(_binding(scale="9b"), _route(scale="4b"))
    assert bad["compatible"] is False
    assert bad["state"] == "model_scale_incompatible"


def test_same_scale_base_to_distilled_is_runtime_preflight_not_false_green():
    base_lora = _binding(scale="4b", kind="base")
    result = klein_support.classify_klein_binding_compatibility(base_lora, _route(scale="4b", kind="distilled"))
    assert result["compatible"] is None
    assert result["state"] == "same_scale_base_distilled_runtime_preflight_required"


def test_unknown_klein_lora_metadata_is_runtime_preflight_candidate():
    b = _binding(scale="4b", family=None)
    b["name"] = "character_style.safetensors"
    b["owner_row"].pop("flux_variant", None)
    filtered = klein_support.filter_klein_bindings([b], _route(scale="4b"))
    assert filtered["accepted_count"] == 1
    assert filtered["unknown_count"] == 1


def test_klein_sampler_profile_preserves_flux_guidance_cfg1_and_zero_negative():
    good = klein_support.validate_klein_sampler_profile(_graph(), sampler_node_id="9", route=_route())
    assert good["ok"] is True
    assert good["cfg"] == 1.0 and good["positive_class"] == "FluxGuidance" and good["negative_class"] == "ConditioningZeroOut"
    bad_graph = _graph(cfg=3.5)
    bad = klein_support.validate_klein_sampler_profile(bad_graph, sampler_node_id="9", route=_route())
    assert bad["ok"] is False


def test_klein_spatial_scope_excludes_text_modulation_and_marks_single_stream_combined():
    assert runtime.flux2_klein_spatial_module_scope("img_in") == "image_only"
    assert runtime.flux2_klein_spatial_module_scope("double_blocks.0.img_attn.qkv") == "image_only"
    assert runtime.flux2_klein_spatial_module_scope("double_blocks.0.img_mlp.0") == "image_only"
    assert runtime.flux2_klein_spatial_module_scope("single_blocks.0.linear1") == "combined_text_image"
    assert runtime.flux2_klein_spatial_module_scope("single_blocks.0.linear2") == "combined_text_image"
    assert runtime.flux2_klein_spatial_module_scope("final_layer.linear") == "image_only"
    for name in ("txt_in", "time_in.in_layer", "vector_in.out_layer", "guidance_in.in_layer", "double_blocks.0.txt_attn.qkv", "double_blocks.0.img_mod.lin", "single_blocks.0.modulation.lin", "final_layer.adaLN_modulation.1"):
        assert runtime.flux2_klein_spatial_module_scope(name) is None


def test_flux_linear1_qkv_comfy_offset_is_preserved_and_delta_expands_to_full_output(monkeypatch):
    class DM(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.single_blocks = torch.nn.ModuleList([torch.nn.Module()])
            self.single_blocks[0].linear1 = torch.nn.Linear(4, 14, bias=False)
    dm = DM()
    base = types.SimpleNamespace()
    pair = {"transformer.single_blocks.0.linear1_qkv": {"down": torch.ones(2, 4), "up": torch.ones(6, 2), "rank": 2, "alpha": 2.0, "base_scale": 1.0}}
    monkeypatch.setattr(runtime, "_comfy_key_map", lambda _base: {"transformer.single_blocks.0.linear1_qkv": ("diffusion_model.single_blocks.0.linear1.weight", (0, 0, 6))})
    resolved, stats = runtime.resolve_lora_pairs_to_modules(pair, base_model=base, diffusion_model=dm, spatial_scope_resolver=runtime.flux2_klein_spatial_module_scope)
    data = next(iter(resolved.values()))
    assert data["output_slice"] == (0, 6)
    assert stats["sliced_targets"]
    proof = {"sampler_count": 1}
    session = runtime._Flux2KleinRegionalSession(None, [], seam_feather=0.0, runtime_proof=proof)
    session.image_masks = [torch.ones(3)]
    session.text_len = 2
    session.region_entries = [{"modules": {}}]
    data = {**data, "down_device": pair["transformer.single_blocks.0.linear1_qkv"]["down"], "up_device": pair["transformer.single_blocks.0.linear1_qkv"]["up"], "spatial_scope": "combined_text_image"}
    hook = session._make_hook([(0, data)])
    x = torch.ones(1, 5, 4)
    output = torch.zeros(1, 5, 14)
    out = hook(None, (x,), output)
    assert torch.count_nonzero(out[:, :2]) == 0
    assert torch.count_nonzero(out[:, 2:, :6]) > 0
    assert torch.count_nonzero(out[:, :, 6:]) == 0


class Flux2(torch.nn.Module):
    def __init__(self, scale="4b"):
        super().__init__()
        self.diffusion_model = FakeKleinDM(scale)


class FakeKleinDM(torch.nn.Module):
    def __init__(self, scale="4b"):
        super().__init__()
        if scale == "4b":
            d, s, h = 5, 20, 3072
        elif scale == "9b":
            d, s, h = 8, 24, 4096
        else:
            d, s, h = 8, 48, 4096
        self.double_blocks = torch.nn.ModuleList([torch.nn.Identity() for _ in range(d)])
        self.single_blocks = torch.nn.ModuleList([torch.nn.Identity() for _ in range(s)])
        self.hidden_size = h
        self.patch_size = 2


class FakePatcher:
    def __init__(self, scale="4b"):
        self.model = Flux2(scale)


def test_runtime_signature_uses_block_depth_as_authority_not_text_encoder_width():
    dm = FakeKleinDM("4b")
    dm.hidden_size = 2560  # historical text-encoder width confusion; depth still proves Klein 4B
    sig = runtime._flux2_klein_signature(dm)
    assert sig["scale"] == "4b"
    assert sig["klein_signature_proven"] is True
    assert sig["hidden_reference_match"] is False


def test_runtime_signature_accepts_klein_4b_9b_and_rejects_flux2_dev_shape():
    _, sig4 = runtime._require_flux2_klein_model(FakePatcher("4b"), "klein_4b")
    _, sig9 = runtime._require_flux2_klein_model(FakePatcher("9b"), "klein_9b")
    assert sig4["scale"] == "4b" and sig9["scale"] == "9b"
    assert sig4["hidden_size"] == 3072 and sig4["hidden_reference_match"] is True
    assert sig9["hidden_size"] == 4096 and sig9["hidden_reference_match"] is True
    with pytest.raises(RuntimeError, match="does not match supported Klein"):
        runtime._require_flux2_klein_model(FakePatcher("dev"), "auto")
    with pytest.raises(RuntimeError, match="requested Klein 9B"):
        runtime._require_flux2_klein_model(FakePatcher("4b"), "klein_9b")


def test_klein_four_regional_loras_one_wrapper_one_sampler_flux_guidance_preserved(monkeypatch):
    regions = [_region(i) for i in range(1, 5)]
    bindings = [_binding(i, scale="4b") for i in range(1, 5)]
    result = _apply(monkeypatch, regions=regions, bindings=bindings)
    classes = _classes(result["workflow"])
    assert classes.count("NeoRegionalLoRADelta") == 1
    assert classes.count("KSampler") == 1
    assert "LoraLoader" not in classes and "LoraLoaderModelOnly" not in classes and "KSamplerAdvanced" not in classes
    contract = result["workflow_patch"]["scene_director_regional_lora_contract"]
    assert contract["route_count"] == 4 and contract["route_limit"] is None
    assert contract["adapter"]["adapter"] == "flux2_klein_activation_delta_v1"
    lanes = result["workflow_patch"]["scene_director_lightweight_regional_prompt"]["positive_lanes"]
    assert len(lanes) == 4 and all(lane["family_adapter_ref"] for lane in lanes)
    assert result["workflow"]["9"]["inputs"]["steps"] == 4
    assert result["workflow"]["9"]["inputs"]["cfg"] == 1.0


def test_klein_gguf_route_passes_loader_and_variant_into_runtime_wrapper(monkeypatch):
    result = _apply(monkeypatch, loader="gguf", scale="9b")
    node_id = result["workflow_patch"]["scene_director_regional_lora_nodes_added"][0]
    node = result["workflow"][node_id]
    assert node["inputs"]["loader"] == "gguf"
    assert "9b" in node["inputs"]["variant"]
    assert result["workflow_patch"]["scene_director_regional_lora_contract"]["flux2_klein_full_support"]["supported"] is True


def test_klein_rejects_cross_scale_binding_before_trigger_injection(monkeypatch):
    result = _apply(monkeypatch, scale="4b", bindings=[_binding(1, scale="9b")])
    patch = result["workflow_patch"]
    compat = patch["scene_director_flux2_klein_lora_compatibility"]
    assert compat["accepted_count"] == 0 and compat["rejected_count"] == 1
    assert patch["scene_director_regional_lora_applied"] is False
    prompt = patch["scene_director_lightweight_regional_prompt"]["positive_lanes"][0]["prompt_with_regional_lora_triggers"]
    assert "record_trigger_1" not in prompt and "klein_trigger_1" not in prompt


def test_klein_inpaint_preserves_differential_diffusion_model_chain(monkeypatch):
    graph = _graph(mode="inpaint")
    result = _apply(monkeypatch, graph=graph, mode="inpaint")
    patch = result["workflow_patch"]
    wrapper_id = patch["scene_director_regional_lora_nodes_added"][0]
    assert result["workflow"]["10"]["class_type"] == "DifferentialDiffusion"
    assert result["workflow"]["10"]["inputs"]["model"] == [wrapper_id, 0]
    assert result["workflow"]["9"]["inputs"]["model"] == ["10", 0]
    assert _classes(result["workflow"]).count("KSampler") == 1
