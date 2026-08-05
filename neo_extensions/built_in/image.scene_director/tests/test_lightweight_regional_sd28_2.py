from __future__ import annotations

import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path


EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
PACKAGE = "neo_scene_director_sd28_2_runtime_testpkg"


def _load(name: str):
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
        module.normalize_prompt_authority = lambda value, default="global_context": "scene_director_only" if str(value or "").strip().lower() in {"scene_director_only", "scene_director", "scene_only", "regional_only", "local_only"} else "global_context"
        sys.modules[full] = module
        return module
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


execution_strategy = _load("execution_strategy")
prompt_authority = _load("prompt_authority")
lightweight = _load("lightweight_regional")
workflow_dispatch = _load("workflow_dispatch")

CORE = set(execution_strategy.LIGHTWEIGHT_CORE_NODES)


def _validation(regions, authority="global_context"):
    return {
        "extension_id": "image.scene_director",
        "enabled": True,
        "ok": True,
        "can_emit_workflow_patch": True,
        "route_state": "experimental_available",
        "route": {},
        "subject_count": sum(1 for r in regions if r.get("type") == "character"),
        "detail_region_count": sum(1 for r in regions if r.get("type") != "character"),
        "block": {
            "enabled": True,
            "inputs": {"regions": deepcopy(regions), "global": {"prompt_authority": authority}},
            "params": {"prompt_authority": authority},
            "assets": {"lora_bindings": []},
            "metadata": {
                "subject_count": sum(1 for r in regions if r.get("type") == "character"),
                "detail_region_count": sum(1 for r in regions if r.get("type") != "character"),
            },
        },
        "validation": [],
        "node_status": {},
    }


def _regions(count=2, negatives=True):
    rows = []
    for i in range(count):
        rows.append({
            "id": f"character_{i+1}",
            "label": f"Character {i+1}",
            "type": "character",
            "bbox": {"x": 0.05 + i * (0.9 / count), "y": 0.1, "w": 0.8 / count, "h": 0.8},
            "prompt": f"character {i+1} distinct outfit",
            "negative_prompt": f"wrong trait {i+1}" if negatives else "",
            "strength": 1.0,
            "mask": {"feather": 12},
        })
    return rows


def _base_graph(family):
    if family == "flux2_klein":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {}},
            "2": {"class_type": "CLIPLoader", "inputs": {}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
            "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": 2.25}},
            "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "8": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
            "9": {"class_type": "KSampler", "inputs": {"seed": 10, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0]}},
        }, "9"
    turbo = family in {"krea2_turbo", "z_image_turbo"}
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "2": {"class_type": "CLIPLoader", "inputs": {}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "global", "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut" if turbo else "CLIPTextEncode", "inputs": {"conditioning": ["4", 0]} if turbo else {"text": "negative", "clip": ["2", 0]}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"seed": 10, "steps": (9 if family == "z_image_turbo" else 8 if turbo else 35), "cfg": 1.0 if turbo else 3.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
    }
    if family in {"z_image", "z_image_turbo"}:
        graph["3"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}}
        graph["7"]["inputs"]["model"] = ["3", 0]
    return graph, "7"


def _apply(monkeypatch, family, regions=None, authority="global_context", loader="diffusion_model"):
    graph, sampler_id = _base_graph(family)
    rows = regions or _regions()
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(rows, authority))
    available = CORE | ({"FluxGuidance"} if family == "flux2_klein" else set())
    before = deepcopy(graph[sampler_id]["inputs"])
    result = lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions": {"image.scene_director": {"enabled": True}}},
        route={"backend": "comfyui", "family": family, "loader": loader, "mode": "generate", "actual_params": {"width": 1024, "height": 768}},
        available_nodes=available,
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
    )
    return result, before, sampler_id


def test_krea2_raw_adds_masked_positive_and_negative_without_extra_sampler(monkeypatch):
    result, before, sampler_id = _apply(monkeypatch, "krea2")
    graph = result["workflow"]
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert proof["contract_ok"] is True
    assert proof["sampler_count_before"] == proof["sampler_count_after"] == 1
    assert graph[sampler_id]["inputs"]["model"] == before["model"]
    assert graph[sampler_id]["inputs"]["steps"] == before["steps"]
    assert proof["regional_prompt_lane_count"] == 2
    assert proof["regional_negative_lane_count"] == 2
    assert not any(node.get("class_type") in {"LoraLoader", "LoraLoaderModelOnly", "NeoSceneDirectorV054"} for node in graph.values() if isinstance(node, dict))


def test_krea2_turbo_preserves_low_step_profile_and_zero_negative(monkeypatch):
    result, before, sampler_id = _apply(monkeypatch, "krea2_turbo")
    graph = result["workflow"]
    sampler = graph[sampler_id]["inputs"]
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert sampler["steps"] == before["steps"] == 8
    assert sampler["cfg"] == before["cfg"] == 1.0
    assert proof["regional_negative_lane_count"] == 0
    assert proof["regional_negative_suppressed_count"] == 2
    assert graph[str(sampler["negative"][0])]["class_type"] == "ConditioningZeroOut"
    assert graph[str(sampler["negative"][0])]["inputs"]["conditioning"] == sampler["positive"]


def test_flux2_klein_applies_same_flux_guidance_to_every_region(monkeypatch):
    result, before, sampler_id = _apply(monkeypatch, "flux2_klein")
    graph = result["workflow"]
    patch = result["workflow_patch"]["scene_director_lightweight_regional_prompt"]
    guidance_nodes = [graph[str(row["family_adapter_ref"][0])] for row in patch["positive_lanes"]]
    assert all(node["class_type"] == "FluxGuidance" for node in guidance_nodes)
    assert all(node["inputs"]["guidance"] == 2.25 for node in guidance_nodes)
    assert graph[sampler_id]["inputs"]["steps"] == before["steps"] == 4
    assert result["workflow_patch"]["scene_director_extra_samplers_added"] == 0


def test_zimage_base_supports_masked_regional_negatives(monkeypatch):
    result, _, _ = _apply(monkeypatch, "z_image")
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert proof["regional_prompt_lane_count"] == 2
    assert proof["regional_negative_lane_count"] == 2
    assert proof["regional_negative_suppressed_count"] == 0


def test_zimage_turbo_suppresses_regional_negative_and_keeps_sampler(monkeypatch):
    result, before, sampler_id = _apply(monkeypatch, "z_image_turbo")
    sampler = result["workflow"][sampler_id]["inputs"]
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert sampler["steps"] == before["steps"] == 9
    assert sampler["cfg"] == before["cfg"] == 1.0
    assert proof["regional_negative_lane_count"] == 0
    assert proof["regional_negative_suppressed_count"] == 2


def test_three_regions_do_not_hit_old_two_route_limit(monkeypatch):
    result, _, _ = _apply(monkeypatch, "krea2", regions=_regions(3))
    proof = result["workflow_patch"]["scene_director_lightweight_runtime_proof"]
    assert proof["regional_prompt_lane_count"] == 3
    assert proof["sampler_count_after"] == 1


def test_scene_director_only_zeroes_global_base_but_keeps_regions(monkeypatch):
    result, _, sampler_id = _apply(monkeypatch, "krea2", authority="scene_director_only")
    graph = result["workflow"]
    patch = result["workflow_patch"]
    assert patch["scene_director_global_prompt_excluded"] is True
    assert graph[sampler_id]["inputs"]["positive"] != patch["previous_positive_ref"]
    assert any(graph[node_id]["class_type"] == "ConditioningZeroOut" for node_id in patch["nodes_added"])


def test_mask_nodes_use_mask_bounds_and_region_bbox(monkeypatch):
    result, _, _ = _apply(monkeypatch, "krea2", regions=[{
        "id": "a", "type": "character", "bbox": {"x": .1, "y": .2, "w": .3, "h": .4},
        "prompt": "one person", "negative_prompt": "", "strength": .7, "mask": {"feather": 10},
    }])
    graph = result["workflow"]
    patch = result["workflow_patch"]["scene_director_lightweight_regional_prompt"]
    lane = patch["positive_lanes"][0]
    assert lane["rect_px"] == {"x": 102, "y": 154, "w": 308, "h": 307}
    mask_node = graph[str(lane["masked_conditioning_ref"][0])]
    assert mask_node["class_type"] == "ConditioningSetMask"
    assert mask_node["inputs"]["set_cond_area"] == "mask bounds"


def test_prompt_only_scene_has_no_regional_lora_runtime_node(monkeypatch):
    result, _, _ = _apply(monkeypatch, "krea2")
    patch = result["workflow_patch"]
    assert patch["scene_director_regional_lora_applied"] is False
    assert patch["scene_director_regional_lora_status"] == "not_requested"
    assert patch["scene_director_lightweight_runtime_proof"]["regional_lora_nodes_added"] == 0


def test_dispatcher_delegates_classic_and_routes_modern(monkeypatch):
    calls = {"legacy": 0, "modern": 0}
    fake_legacy = types.SimpleNamespace(apply_scene_director_patch=lambda *args, **kwargs: calls.__setitem__("legacy", calls["legacy"] + 1) or {"legacy": True})
    monkeypatch.setattr(workflow_dispatch, "_legacy_module", lambda: fake_legacy)
    monkeypatch.setattr(workflow_dispatch, "apply_lightweight_regional_prompt_patch", lambda *args, **kwargs: calls.__setitem__("modern", calls["modern"] + 1) or {"modern": True})
    common = dict(workflow={}, payload={}, available_nodes=CORE, model_ref=["1", 0], clip_ref=["2", 0], sampler_node_id="7")
    classic = workflow_dispatch.apply_scene_director_patch(route={"backend":"comfyui","family":"sdxl","loader":"checkpoint","mode":"generate"}, **common)
    modern = workflow_dispatch.apply_scene_director_patch(route={"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"}, **common)
    assert classic["legacy"] is True
    assert modern["modern"] is True
    assert classic["scene_director_release_lock"]["status"] == "locked"
    assert modern["scene_director_release_lock"]["status"] == "locked"
    assert classic["inspector_debug_ui"]["schema"] == "neo.image.scene_director.inspector.v2"
    assert modern["inspector_debug_ui"]["schema"] == "neo.image.scene_director.inspector.v2"
    assert calls == {"legacy": 1, "modern": 1}


def test_img2img_and_inpaint_like_sampler_inputs_remain_untouched(monkeypatch):
    for mode in ("img2img", "inpaint"):
        graph, sampler_id = _base_graph("krea2")
        graph["20"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["19", 0], "vae": ["3", 0]}}
        graph["21"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["20", 0], "mask": ["18", 0]}}
        graph["22"] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["1", 0]}}
        graph[sampler_id]["inputs"]["model"] = ["22", 0]
        graph[sampler_id]["inputs"]["latent_image"] = ["21", 0]
        before = deepcopy(graph[sampler_id]["inputs"])
        monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(_regions(), "global_context"))
        result = lightweight.apply_lightweight_regional_prompt_patch(
            graph,
            payload={"extensions":{"image.scene_director":{"enabled":True}}},
            route={"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":mode,"actual_params":{"width":1024,"height":768}},
            available_nodes=CORE,
            model_ref=["22",0],
            clip_ref=["2",0],
            sampler_node_id=sampler_id,
        )
        after = result["workflow"][sampler_id]["inputs"]
        assert after["model"] == before["model"] == ["22", 0]
        assert after["latent_image"] == before["latent_image"] == ["21", 0]
        for key in ("steps", "cfg", "sampler_name", "scheduler", "denoise", "seed"):
            assert after[key] == before[key]
        assert result["workflow_patch"]["scene_director_lightweight_runtime_proof"]["sampler_count_after"] == 1


def test_missing_builtin_node_blocks_without_mutating(monkeypatch):
    graph, sampler_id = _base_graph("krea2")
    before = deepcopy(graph)
    monkeypatch.setattr(lightweight, "_validate_payload", lambda payload, route, available_nodes: _validation(_regions()))
    result = lightweight.apply_lightweight_regional_prompt_patch(
        graph,
        payload={"extensions":{"image.scene_director":{"enabled":True}}},
        route={"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"},
        available_nodes=CORE - {"ConditioningSetMask"},
        model_ref=["1",0], clip_ref=["2",0], sampler_node_id=sampler_id,
    )
    assert result["mutated"] is False
    assert result["workflow"] == before
    assert "ConditioningSetMask" in result["workflow_patch"]["reason"]


def test_gguf_uses_same_lightweight_conditioning_contract(monkeypatch):
    result, _, _ = _apply(monkeypatch, "z_image", loader="gguf")
    patch = result["workflow_patch"]
    assert patch["applied"] is True
    assert patch["scene_director_execution_strategy"]["loader"] == "gguf"
    assert patch["scene_director_lightweight_runtime_proof"]["contract_ok"] is True


def test_payload_schema_proxy_fixes_modern_experimental_label(monkeypatch):
    payload_proxy = _load("payload_schema_dispatch")
    fake_normalized = {
        "extensions": {
            "image.scene_director": {
                "enabled": True,
                "inputs": {"regions": []},
                "params": {},
                "assets": {},
                "metadata": {
                    "warnings": ["Scene Director SD/SD1.5 route is experimental in V2."],
                    "node_status": {"selected_node":"NeoSceneDirectorV054"},
                    "node_decision": {"node_status": {"selected_node":"ComfyBuiltInMaskedRegionalConditioning", "available":True}},
                },
            }
        }
    }
    fake = types.SimpleNamespace(
        EXTENSION_ID="image.scene_director",
        normalize_scene_director_payload=lambda *args, **kwargs: deepcopy(fake_normalized),
    )
    monkeypatch.setattr(payload_proxy, "_legacy_module", lambda: fake)
    result = payload_proxy.normalize_scene_director_payload(
        {}, route={"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"}
    )
    metadata = result["extensions"]["image.scene_director"]["metadata"]
    assert metadata["execution_engine"] == "lightweight_regional"
    assert metadata["node_status"]["selected_node"] == "ComfyBuiltInMaskedRegionalConditioning"
    assert metadata["warnings"] == ["Scene Director lightweight regional route is release-locked in SD-28.7 for Krea2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo; regional LoRA still requires per-run runtime proof."]
    assert metadata["inspector_debug_ui"]["phase"] == "SD-28.7"


def test_backend_package_redirects_historical_workflow_patch_import(tmp_path):
    pkg = tmp_path / "sdproxy"
    backend = pkg / "backend"
    backend.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (backend / "__init__.py").write_text((BACKEND / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8")
    (backend / "v054_contract.py").write_text("normalize_scene_graph_v054=lambda x:x\nvalidate_scene_graph_v054=lambda x:[]\n", encoding="utf-8")
    (backend / "v054_node.py").write_text("class NeoSceneDirectorV054: pass\nNODE_CLASS_MAPPINGS={}\nNODE_DISPLAY_NAME_MAPPINGS={}\n", encoding="utf-8")
    (backend / "provider_capabilities.py").write_text("resolve_provider_capabilities_v054=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "flux_adapter.py").write_text("build_flux_adapter_plan_v054=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "qwen_adapter.py").write_text("build_qwen_adapter_plan_v054=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "execution_strategy.py").write_text("resolve_scene_director_execution_strategy=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "release_lock.py").write_text("evaluate_scene_director_release_lock=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "inspector.py").write_text("build_scene_director_inspector=lambda *a,**k:{}\n", encoding="utf-8")
    (backend / "workflow_dispatch.py").write_text("MARKER='dispatch'\ndef apply_scene_director_patch(*a,**k): return {'dispatch':True}\n", encoding="utf-8")
    (backend / "payload_schema_dispatch.py").write_text("MARKER='payload_proxy'\n", encoding="utf-8")
    (backend / "workflow_patch.py").write_text("MARKER='legacy'\n", encoding="utf-8")
    (backend / "payload_schema.py").write_text("MARKER='legacy_payload'\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        for name in list(sys.modules):
            if name == "sdproxy" or name.startswith("sdproxy."):
                sys.modules.pop(name, None)
        import importlib
        routed = importlib.import_module("sdproxy.backend.workflow_patch")
        payload_routed = importlib.import_module("sdproxy.backend.payload_schema")
        assert routed.MARKER == "dispatch"
        assert payload_routed.MARKER == "payload_proxy"
    finally:
        sys.path.remove(str(tmp_path))
        for name in list(sys.modules):
            if name == "sdproxy" or name.startswith("sdproxy."):
                sys.modules.pop(name, None)
