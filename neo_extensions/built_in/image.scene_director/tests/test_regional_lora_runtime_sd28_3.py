from __future__ import annotations

import importlib.util
import json
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest
import torch


EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
COMFY_NODE = EXT_ROOT / "comfy_node"
PACKAGE = "neo_scene_director_sd28_3_runtime_testpkg"


def _load_backend(name: str):
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = package
    full = f"{PACKAGE}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = BACKEND / f"{name}.py"
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


def _load_runtime_node():
    name = "neo_scene_director_sd28_3_comfy_regional_lora"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMFY_NODE / "regional_lora.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


execution_strategy = _load_backend("execution_strategy")
_ = _load_backend("prompt_authority")
regional = _load_backend("regional_lora_delta")
lightweight = _load_backend("lightweight_regional")
runtime_node = _load_runtime_node()

CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES)


def _regions(count: int = 2):
    out = []
    for idx in range(count):
        out.append({
            "id": f"character_{idx + 1}",
            "label": f"Character {idx + 1}",
            "type": "character",
            "bbox": {"x": 0.02 + idx * (0.96 / count), "y": 0.08, "w": 0.88 / count, "h": 0.84},
            "prompt": f"distinct character {idx + 1}",
            "negative_prompt": "",
            "strength": 1.0,
            "mask": {"feather": 8 + idx},
        })
    return out


def _bindings(count: int = 2, target: str = "both"):
    return [
        {
            "uid": f"regional_binding_{idx + 1}",
            "row_id": f"lora_{idx + 1}",
            "lora_row_id": f"lora_{idx + 1}",
            "region_id": f"character_{idx + 1}",
            "region_index": idx + 1,
            "name": f"character_{idx + 1}.safetensors",
            "strength": 0.75 + idx * 0.05,
            "target": target,
            "source_record_trigger_words": f"trigger_{idx + 1}",
            "source_record_activation_text": f"activation_{idx + 1}",
            "owner_row": {"trigger_words": f"owner_trigger_{idx + 1}"},
        }
        for idx in range(count)
    ]


def _validation(regions, bindings):
    return {
        "extension_id": "image.scene_director",
        "enabled": True,
        "ok": True,
        "can_emit_workflow_patch": True,
        "route_state": "experimental_available",
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


def _base_graph(*, turbo: bool = False):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
        "5": {
            "class_type": "ConditioningZeroOut" if turbo else "CLIPTextEncode",
            "inputs": {"conditioning": ["4", 0]} if turbo else {"text": "negative", "clip": ["2", 0]},
        },
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 123,
                "steps": 8 if turbo else 52,
                "cfg": 1.0 if turbo else 3.5,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        },
    }


def _apply(monkeypatch, *, family="krea2", count=2, runtime_node_available=True, graph=None, model_ref=None):
    rows = _regions(count)
    binds = _bindings(count)
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(rows, binds))
    graph = deepcopy(graph or _base_graph(turbo=family == "krea2_turbo"))
    available = set(CORE)
    if runtime_node_available:
        if family in {"krea2", "krea2_turbo"}:
            available.update({"Krea2RegionalBuilder", "Krea2ApplyRegional"})
        else:
            available.add("NeoRegionalLoRADelta")
    result = lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions": {"image.scene_director": {"enabled": True}}},
        route={"backend": "comfyui", "family": family, "loader": "diffusion_model", "mode": "generate", "actual_params": {"width": 1024, "height": 768}},
        available_nodes=available,
        model_ref=model_ref or ["1", 0],
        clip_ref=["2", 0],
        sampler_node_id="7",
    )
    return result


def _classes(graph):
    return [str(node.get("class_type") or "") for node in graph.values() if isinstance(node, dict)]


def test_krea2_uses_external_builder_apply_and_no_extra_sampler(monkeypatch):
    result = _apply(monkeypatch)
    graph = result["workflow"]
    classes = _classes(graph)
    assert classes.count("Krea2RegionalBuilder") == 1
    assert classes.count("Krea2ApplyRegional") == 1
    assert classes.count("NeoRegionalLoRADelta") == 0
    assert classes.count("KSampler") == 1
    assert "LoraLoader" not in classes
    assert "LoraLoaderModelOnly" not in classes
    patch = result["workflow_patch"]
    assert patch["scene_director_regional_lora_status"] == "external_runtime_armed"
    proof = patch["scene_director_lightweight_runtime_proof"]
    assert proof["contract_ok"] is True
    assert proof["external_engine_nodes_added"] == 2
    contract = patch["scene_director_regional_lora_contract"]
    assert contract["route_count"] == 2
    assert contract["adapter"] == "krea2_regional_external"
    assert contract["clip_delta_execution"] == "regional_prompt_tokens_and_image_tokens_owned_by_external_engine"


def test_three_plus_loras_have_no_legacy_two_route_cap(monkeypatch):
    result = _apply(monkeypatch, count=4)
    patch = result["workflow_patch"]
    assert patch["scene_director_regional_lora_contract"]["route_count"] == 4
    assert patch["scene_director_regional_lora_contract"]["route_limit"] is None
    proof = patch["scene_director_lightweight_runtime_proof"]
    assert proof["external_engine_nodes_added"] == 2
    assert _classes(result["workflow"]).count("Krea2RegionalBuilder") == 1
    assert _classes(result["workflow"]).count("Krea2ApplyRegional") == 1
    assert _classes(result["workflow"]).count("KSampler") == 1


def test_krea2_external_engine_preserves_downstream_differential_diffusion(monkeypatch):
    graph = _base_graph()
    graph["20"] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["1", 0]}}
    graph["7"]["inputs"]["model"] = ["20", 0]
    result = _apply(monkeypatch, graph=graph, model_ref=["1", 0])
    patched = result["workflow"]
    apply_id = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]["apply_node_id"]
    assert patched["20"]["inputs"]["model"] == ["1", 0]
    assert patched[apply_id]["inputs"]["model"] == ["20", 0]
    assert patched["7"]["inputs"]["model"] == [apply_id, 0]
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert proof["external_model_input_ref"] == ["20", 0]
    assert proof["latent_input_unchanged"] is True


def test_krea2_external_engine_layers_after_unrelated_global_lora_model_ref(monkeypatch):
    graph = _base_graph()
    graph["10"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "global.safetensors", "strength_model": 0.7}}
    graph["7"]["inputs"]["model"] = ["10", 0]
    result = _apply(monkeypatch, graph=graph, model_ref=["1", 0])
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    apply_id = proof["apply_node_id"]
    assert result["workflow"][apply_id]["inputs"]["model"] == ["10", 0]
    assert result["workflow"]["7"]["inputs"]["model"] == [apply_id, 0]
    assert _classes(result["workflow"]).count("KSampler") == 1


def test_missing_krea_external_runtime_never_falls_back_to_global_or_neo_runtime(monkeypatch):
    result = _apply(monkeypatch, runtime_node_available=False)
    classes = _classes(result["workflow"])
    patch = result["workflow_patch"]
    assert patch["scene_director_regional_lora_status"] == "missing_external_runtime"
    assert patch["scene_director_regional_lora_applied"] is False
    assert "Krea2RegionalBuilder" not in classes
    assert "Krea2ApplyRegional" not in classes
    assert "NeoRegionalLoRADelta" not in classes
    assert "LoraLoader" not in classes
    assert "LoraLoaderModelOnly" not in classes
    assert classes.count("KSampler") == 1
    assert "ComfyUI-Krea2-Regional" in patch["reason"]


@pytest.mark.parametrize("family", ["z_image", "z_image_turbo"])
def test_zimage_families_are_armed_by_sd28_6_regional_lora_contract(family):
    contract = regional.build_regional_lora_delta_contract(
        {"backend": "comfyui", "family": family, "loader": "diffusion_model", "mode": "generate"},
        bindings=_bindings(1),
        regions=_regions(1),
        canvas={"width": 1024, "height": 768},
    )
    assert contract["adapter"]["status"] == "supported_runtime_contract"
    assert contract["adapter"]["adapter"] == "z_image_activation_delta_v1"
    assert contract["execution_enabled"] is True
    assert contract["runtime_node"] == "NeoRegionalLoRADelta"
    graph = regional.apply_regional_lora_delta(
        _base_graph(), contract=contract, model_ref=["1", 0], available_nodes=CORE | {"NeoRegionalLoRADelta"}
    )
    assert graph["metadata"]["status"] == "armed_not_gpu_proven"
    assert "NeoRegionalLoRADelta" in _classes(graph["workflow"])


def test_krea_turbo_sampler_profile_is_not_changed_by_regional_lora(monkeypatch):
    before = deepcopy(_base_graph(turbo=True)["7"]["inputs"])
    result = _apply(monkeypatch, family="krea2_turbo")
    after = result["workflow"]["7"]["inputs"]
    for key in ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "latent_image"):
        assert after[key] == before[key]
    assert after["steps"] == 8
    assert after["cfg"] == 1.0
    assert _classes(result["workflow"]).count("KSampler") == 1


def test_krea_external_builder_keeps_regional_prompts_and_loras_local(monkeypatch):
    result = _apply(monkeypatch, count=2)
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    builder_id = proof["builder_node_id"]
    builder = result["workflow"][builder_id]
    payload = json.loads(builder["inputs"]["regions_data"])
    assert len(payload["regions"]) == 2
    first, second = payload["regions"]
    assert first["desc"] == "distinct character 1"
    assert second["desc"] == "distinct character 2"
    assert first["loras"][0]["name"] == "character_1.safetensors"
    assert second["loras"][0]["name"] == "character_2.safetensors"
    assert builder["inputs"]["base_prompt"] == "global"
    assert "trigger_1" not in builder["inputs"]["base_prompt"]
    assert "trigger_2" not in builder["inputs"]["base_prompt"]


def test_standard_ab_pair_parser_honors_alpha_scale():
    state = {
        "block.to_q.lora_A.weight": torch.ones(2, 4),
        "block.to_q.lora_B.weight": torch.ones(6, 2),
        "block.to_q.alpha": torch.tensor(1.0),
    }
    pairs = runtime_node.parse_standard_lora_pairs(state)
    assert set(pairs) == {"block.to_q"}
    assert pairs["block.to_q"]["rank"] == 2
    assert pairs["block.to_q"]["alpha"] == 1.0
    assert pairs["block.to_q"]["base_scale"] == pytest.approx(0.5)


def test_pair_resolver_uses_unique_normalized_fallback(monkeypatch):
    dm = torch.nn.Module()
    dm.first = torch.nn.Linear(4, 6, bias=False)
    pairs = {
        "lora_unet_first": {
            "down": torch.ones(2, 4),
            "up": torch.ones(6, 2),
            "rank": 2,
            "alpha": 2.0,
            "base_scale": 1.0,
        }
    }
    monkeypatch.setattr(runtime_node, "_comfy_key_map", lambda _base: {})
    resolved, stats = runtime_node.resolve_lora_pairs_to_modules(pairs, base_model=object(), diffusion_model=dm)
    assert set(resolved) == {"first"}
    assert stats["resolved_count"] == 1
    assert not stats["unresolved"]


def test_pair_resolver_accepts_comfy_canonical_mapped_state_dict_key(monkeypatch):
    dm = torch.nn.Module()
    dm.first = torch.nn.Linear(4, 6, bias=False)
    pairs = {
        "base_model.model.first": {
            "down": torch.ones(2, 4),
            "up": torch.ones(6, 2),
            "rank": 2,
            "alpha": 2.0,
            "base_scale": 1.0,
        }
    }
    monkeypatch.setattr(runtime_node, "_comfy_key_map", lambda _base: {
        "base_model.model.first": "diffusion_model.first.weight"
    })
    resolved, stats = runtime_node.resolve_lora_pairs_to_modules(pairs, base_model=object(), diffusion_model=dm)
    assert set(resolved) == {"first"}
    assert stats["resolved_count"] == 1


def test_sequence_masks_never_touch_text_and_unknown_layout_fails_closed():
    image = torch.tensor([1.0, 0.5, 0.25, 0.0])
    image_only = runtime_node.full_sequence_mask_fail_closed(image, seq_len=4, ndim=3, text_len=None)
    assert torch.equal(image_only.reshape(-1), image)

    text_only = runtime_node.full_sequence_mask_fail_closed(image, seq_len=3, ndim=3, text_len=3)
    assert torch.count_nonzero(text_only) == 0

    mixed = runtime_node.full_sequence_mask_fail_closed(image, seq_len=7, ndim=3, text_len=3).reshape(-1)
    assert torch.count_nonzero(mixed[:3]) == 0
    assert torch.equal(mixed[3:], image)

    unknown = runtime_node.full_sequence_mask_fail_closed(image, seq_len=9, ndim=3, text_len=None)
    assert torch.count_nonzero(unknown) == 0


def test_runtime_session_removes_hooks_even_when_executor_raises():
    class DM(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.first = torch.nn.Linear(4, 4, bias=False)

    dm = DM()
    base = types.SimpleNamespace(diffusion_model=dm)
    patcher = types.SimpleNamespace(model=base)
    proof = {
        "lora_loaded": True,
        "model_family_match": True,
        "region_mask_bound": False,
        "masked_delta_hook_active": False,
        "delta_eval_attempted": False,
        "delta_nonzero": False,
        "global_model_mutation": False,
        "sampler_count": 1,
        "forward_hooks_removed": False,
        "runtime_gpu_proven": False,
    }
    entry = {
        "region_id": "a",
        "bbox_norm": (0.0, 0.0, 1.0, 1.0),
        "seam_feather": 0.0,
        "modules": {
            "first": {
                "down": torch.eye(4),
                "up": torch.eye(4),
                "scale": 0.5,
                "spatial_scope": "image_only",
            }
        },
    }
    session = runtime_node._Krea2RegionalSession(patcher, [entry], seam_feather=0.0, runtime_proof=proof)

    def executor(x):
        tokens = x.reshape(x.shape[0], 4, 4)
        _ = dm.first(tokens)
        raise RuntimeError("synthetic executor failure")

    with pytest.raises(RuntimeError, match="synthetic executor failure"):
        session.run(executor, torch.ones(1, 1, 4, 4))
    assert proof["masked_delta_hook_active"] is True
    assert proof["delta_eval_attempted"] is True
    assert proof["delta_nonzero"] is True
    assert proof["forward_hooks_removed"] is True
    assert len(dm.first._forward_hooks) == 0


def test_runtime_session_can_produce_complete_live_proof_on_tensor_fixture():
    class DM(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.first = torch.nn.Linear(4, 4, bias=False)

    dm = DM()
    with torch.no_grad():
        dm.first.weight.copy_(torch.eye(4))
    base = types.SimpleNamespace(diffusion_model=dm)
    patcher = types.SimpleNamespace(model=base)
    proof = {
        "lora_loaded": True,
        "model_family_match": True,
        "region_mask_bound": False,
        "masked_delta_hook_active": False,
        "delta_eval_attempted": False,
        "delta_nonzero": False,
        "global_model_mutation": False,
        "sampler_count": 1,
        "forward_hooks_removed": False,
        "runtime_gpu_proven": False,
    }
    entry = {
        "region_id": "a",
        "bbox_norm": (0.0, 0.0, 1.0, 1.0),
        "seam_feather": 0.0,
        "modules": {"first": {"down": torch.eye(4), "up": torch.eye(4), "scale": 0.25, "spatial_scope": "image_only"}},
    }
    session = runtime_node._Krea2RegionalSession(patcher, [entry], seam_feather=0.0, runtime_proof=proof)
    output = session.run(lambda x: dm.first(x.reshape(x.shape[0], 4, 4)), torch.ones(1, 1, 4, 4))
    assert torch.is_tensor(output)
    assert proof["runtime_gpu_proven"] is True
    checked = regional.validate_regional_lora_runtime_proof(proof)
    assert checked["ready"] is True
    assert checked["runtime_gpu_proven"] is True


def test_node_clones_model_and_registers_wrapper_without_mutating_original(monkeypatch):
    class FakeKrea2:
        def __init__(self):
            self.diffusion_model = object()

    class FakePatcher:
        def __init__(self, *, cloned=False):
            self.model = FakeKrea2()
            self.cloned = cloned
            self.wrappers = []
            self.attachments = {}

        def clone(self):
            return FakePatcher(cloned=True)

        def add_wrapper_with_key(self, wrapper_type, key, wrapper):
            self.wrappers.append((wrapper_type, key, wrapper))

        def set_attachments(self, key, value):
            self.attachments[key] = value

    monkeypatch.setattr(runtime_node, "_require_krea2_model", lambda model, family: model.model)
    monkeypatch.setattr(runtime_node, "build_krea2_region_entries", lambda model, routes: ([{
        "region_id": "a", "lora_name": "a.safetensors", "strength": 1.0, "bbox_norm": (0, 0, 1, 1), "modules": {}
    }], {"routes": [], "file_count": 1}))

    original = FakePatcher()
    node = runtime_node.NeoRegionalLoRADelta()
    patched, = node.apply(
        original,
        routes_json=json.dumps([{"region_id": "a", "lora_name": "a.safetensors", "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}]),
        family="krea2",
        canvas_width=1024,
        canvas_height=1024,
        seam_feather=0.0,
        sampler_count=1,
    )
    assert patched is not original
    assert patched.cloned is True
    assert not original.wrappers
    assert len(patched.wrappers) == 1
    proof = patched.attachments[runtime_node.RUNTIME_ATTACHMENT_KEY]
    assert proof["global_model_mutation"] is False
    assert proof["clip_delta_execution"] == "suppressed_model_side_only"
    assert proof["sampler_count"] == 1


def test_manifest_registers_regional_runtime_through_combined_comfy_entrypoint():
    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    assert tuple(int(part) for part in manifest["version"].split(".")) >= (1, 2, 21)
    assert manifest["entrypoints"]["comfy_node"] == "comfy_node/__init__.py"
    phase = manifest["phase_sd_28_3_regional_lora_runtime_foundation"]
    assert phase["runtime_node"] == "NeoRegionalLoRADelta"
    assert phase["executable_families"] == ["krea2", "krea2_turbo"]
    assert "flux2_klein" in phase["gated_families"]


def test_contract_preserves_per_region_feather_and_has_no_route_limit():
    regions = _regions(2)
    regions[0]["mask"]["feather"] = 4
    regions[1]["mask"]["feather"] = 32
    contract = regional.build_regional_lora_delta_contract(
        {"backend": "comfyui", "family": "krea2", "loader": "diffusion_model", "mode": "generate"},
        bindings=_bindings(2), regions=regions, canvas={"width": 1024, "height": 1024},
    )
    assert contract["route_limit"] is None
    assert contract["routes"][0]["seam_feather"] < contract["routes"][1]["seam_feather"]


def test_runtime_node_only_resolves_lora_files_through_comfy_search_paths():
    source = (COMFY_NODE / "regional_lora.py").read_text(encoding="utf-8")
    assert 'get_full_path_or_raise("loras", lora_name)' in source
    assert 'get_full_path("loras", lora_name)' in source
    assert "Path(lora_name)" not in source
    assert "os.path.isfile(lora_name)" not in source
