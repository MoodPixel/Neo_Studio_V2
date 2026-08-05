from __future__ import annotations

import importlib.util
import json
import sys
import types
from copy import deepcopy
from pathlib import Path

EXT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = EXT_ROOT / "backend"
PACKAGE = "neo_scene_director_sd28_7_testpkg"


def _load(name: str):
    if PACKAGE not in sys.modules:
        pkg = types.ModuleType(PACKAGE)
        pkg.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = pkg
    full = f"{PACKAGE}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = BACKEND / f"{name}.py"
    if name == "prompt_authority" and not path.exists():
        module = types.ModuleType(full)
        module.PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY = "scene_director_only"
        module.normalize_prompt_authority = lambda value, default="global_context": value or default
        sys.modules[full] = module
        return module
    spec = importlib.util.spec_from_file_location(full, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


execution = _load("execution_strategy")
_load("prompt_authority")
release = _load("release_lock")
inspector_mod = _load("inspector")
workflow_dispatch = _load("workflow_dispatch")


def _before_graph():
    return {
        "1": {"class_type": "UNETLoader", "inputs": {}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "steps": 35, "cfg": 3.5}},
    }


def _modern_result(*, lora=False, extra_class="ConditioningSetMask", gpu_proven=False):
    before = _before_graph()
    graph = deepcopy(before)
    graph["10"] = {"class_type": extra_class, "inputs": {}}
    nodes = ["10"]
    if lora:
        graph["11"] = {"class_type": "NeoRegionalLoRADelta", "inputs": {"model": ["1", 0]}}
        nodes.append("11")
    patch = {
        "applied": True,
        "mutated": True,
        "nodes_added": nodes,
        "regions": 2,
        "subject_count": 2,
        "fallback_policy": "never_fallback_to_classic_v054_or_global_lora",
        "scene_director_engine": "lightweight_regional",
        "scene_director_execution_strategy": execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"}),
        "scene_director_regional_lora_applied": lora,
        "scene_director_regional_lora_runtime_gpu_proven": gpu_proven,
        "scene_director_regional_lora_contract": {"mode":"krea2_activation_delta_v2", "route_count": 1 if lora else 0, "binding_compatibility": {}},
        "scene_director_lightweight_regional_prompt": {"status":"applied"},
        "scene_director_lightweight_runtime_proof": {
            "single_sampler_preserved": True,
            "sampler_parameters_preserved": True,
            "latent_input_unchanged": True,
            "global_model_mutation": False,
            "heavy_sd_repairs_added": False,
            "repair_sampler_nodes_added": 0,
            "regional_prompt_lane_count": 2,
            "regional_lora_route_count": 1 if lora else 0,
            "regional_lora_nodes_added": 1 if lora else 0,
            "sampler_count_before": 1,
            "sampler_count_after": 1,
            "runtime_gpu_proven": gpu_proven,
            "contract_ok": True,
        },
        "route": {"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"},
        "route_state": "available",
        "previous_model_ref": ["1", 0],
        "patched_model_ref": ["11", 0] if lora else ["1", 0],
        "clip_ref": ["2", 0],
        "previous_positive_ref": ["4", 0],
        "patched_positive_ref": ["10", 0],
        "previous_negative_ref": ["5", 0],
        "patched_negative_ref": ["10", 0],
    }
    return before, {"workflow": graph, "workflow_patch": patch, "validation": {"warnings":[],"errors":[],"route":patch["route"],"route_state":"available"}, "mutated": True}


def test_modern_release_lock_accepts_prompt_only_one_sampler_graph():
    before, result = _modern_result()
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "locked"
    assert lock["allow_output"] is True
    assert not lock["blockers"]


def test_modern_release_lock_accepts_exactly_one_regional_lora_wrapper():
    before, result = _modern_result(lora=True)
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "locked"
    assert lock["gpu_proof_required"] is True
    assert lock["gpu_proven"] is False


def test_gpu_proof_pending_is_not_compile_blocker():
    before, result = _modern_result(lora=True, gpu_proven=False)
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["locked"] is True
    assert lock["gpu_proven"] is False


def test_runtime_gpu_proof_surfaces_when_present():
    before, result = _modern_result(lora=True, gpu_proven=True)
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    ui = inspector_mod.build_scene_director_inspector(validation=result["validation"], workflow_patch=result["workflow_patch"], release_lock=lock)
    chips = {row["id"]: row for row in ui["status_chips"]}
    assert chips["gpu_proof"]["state"] == "proven"
    assert ui["gpu_proof"]["proven"] is True


def test_new_sampler_insertion_is_release_blocker():
    before, result = _modern_result(extra_class="KSampler")
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "blocked"
    assert any(row["id"] == "modern_no_forbidden_insertions" for row in lock["blockers"])


def test_standard_lora_loader_insertion_is_release_blocker():
    before, result = _modern_result(extra_class="LoraLoader")
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "blocked"


def test_v054_insertion_on_modern_route_is_release_blocker():
    before, result = _modern_result(extra_class="NeoSceneDirectorV054")
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "blocked"


def test_two_regional_lora_wrappers_are_release_blocked():
    before, result = _modern_result(lora=True)
    result["workflow"]["12"] = {"class_type":"NeoRegionalLoRADelta","inputs":{}}
    result["workflow_patch"]["nodes_added"].append("12")
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    assert lock["status"] == "blocked"
    assert any(row["id"] == "single_regional_lora_wrapper" for row in lock["blockers"])


def test_outpaint_is_gated_safe_only_without_mutation():
    before = _before_graph()
    route = {"backend":"comfyui","family":"z_image","loader":"gguf","mode":"outpaint"}
    untouched = {"workflow": deepcopy(before), "workflow_patch": {"applied":False,"mutated":False}, "mutated":False}
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=untouched, route=route)
    assert lock["status"] == "gated_safe"
    mutated = deepcopy(untouched)
    mutated["mutated"] = True
    mutated["workflow_patch"]["applied"] = True
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=mutated, route=route)
    assert lock["status"] == "blocked"


def test_classic_route_keeps_frozen_boundary():
    before = _before_graph()
    route = {"backend":"comfyui","family":"sdxl","loader":"checkpoint","mode":"generate"}
    strategy = execution.resolve_scene_director_execution_strategy(route)
    result = {"workflow": deepcopy(before), "workflow_patch": {"applied":True,"nodes_added":[],"scene_director_execution_strategy":strategy}, "mutated":True}
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=route, strategy=strategy)
    assert lock["status"] == "locked"
    assert lock["engine"] == "classic_v054"


def test_inspector_has_release_route_lora_and_proof_panels():
    before, result = _modern_result(lora=True)
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    ui = inspector_mod.build_scene_director_inspector(validation=result["validation"], workflow_patch=result["workflow_patch"], release_lock=lock)
    assert ui["schema"] == "neo.image.scene_director.inspector.v2"
    assert ui["phase"] == "SD-28.7"
    panel_ids = {row["panel_id"] for row in ui["panels"]}
    assert {"overview","route","regional_lora","runtime_proof","release_lock","diagnostics"}.issubset(panel_ids)
    chips = {row["id"]: row for row in ui["status_chips"]}
    assert chips["release_lock"]["state"] == "locked"
    assert chips["gpu_proof"]["state"] == "pending"


def test_preflight_inspector_never_claims_gpu_proof():
    strategy = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":"z_image_turbo","loader":"gguf","mode":"generate"})
    ui = inspector_mod.build_preflight_inspector(block={"metadata":{"route_state":"available","regional_count":3,"subject_count":3}}, strategy=strategy)
    chips = {row["id"]: row for row in ui["status_chips"]}
    assert chips["release_lock"]["state"] == "preflight"
    assert chips["gpu_proof"]["state"] == "not_requested"
    assert ui["gpu_proof"]["proven"] is False


def test_dispatch_release_lock_fails_closed_to_original_graph(monkeypatch):
    before = _before_graph()
    bad = _modern_result(extra_class="KSampler")[1]
    monkeypatch.setattr(workflow_dispatch, "apply_lightweight_regional_prompt_patch", lambda *a, **k: deepcopy(bad))
    route = {"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"}
    result = workflow_dispatch.apply_scene_director_patch(before, payload={}, route=route, available_nodes=set(), model_ref=["1",0], clip_ref=["2",0], sampler_node_id="7")
    assert result["scene_director_release_lock"]["status"] == "blocked"
    assert result["workflow"] == before
    assert result["mutated"] is False
    assert result["model_ref"] == ["1", 0]
    assert result["positive_ref"] == ["4", 0]
    assert result["negative_ref"] == ["5", 0]
    assert result["workflow_patch"]["nodes_added"] == []
    assert result["workflow_patch"]["release_lock_attempted_nodes_added"] == ["10"]
    assert result["workflow_patch"]["release_lock_blocked"] is True


def test_release_ux_assets_are_read_only_and_manifest_ready():
    js = (EXT_ROOT / "ui" / "release_inspector.js").read_text(encoding="utf-8")
    css = (EXT_ROOT / "ui" / "release_inspector.css").read_text(encoding="utf-8")
    assert "generation state" in js
    assert "addEventListener" not in js
    assert "fetch(" not in js
    assert "NeoSceneDirectorReleaseUX" in js
    assert ".neo-scene-release-chip" in css


def test_release_route_matrix_is_exact_and_outpaint_remains_gated():
    families = ("krea2","krea2_turbo","flux2_klein","z_image","z_image_turbo")
    for family in families:
        for loader in ("diffusion_model","gguf"):
            for mode in ("generate","img2img","inpaint"):
                strategy = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":family,"loader":loader,"mode":mode})
                assert strategy["status"] == "active"
                assert strategy["execution_enabled"] is True
                assert strategy["fallback_policy"] == "never_fallback_to_classic_v054"
            outpaint = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":family,"loader":loader,"mode":"outpaint"})
            assert outpaint["status"] == "planned_gated"
            assert outpaint["execution_enabled"] is False


def test_manifest_is_release_locked_and_declares_inspector_assets():
    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.2.20"
    assert manifest["support_matrix_contract"]["phase"] == "SD-28.7"
    assert manifest["phase_sd_28_7_release_lock"]["release_state"] == "locked"
    assert "ui/release_inspector.js" in manifest["asset_bundle"]["js"]
    assert "ui/release_inspector.css" in manifest["asset_bundle"]["css"]
    assert "backend/release_lock.py" in manifest["asset_bundle"]["python"]
    assert "backend/inspector.py" in manifest["asset_bundle"]["python"]
    assert "backend/krea2_support.py" in manifest["asset_bundle"]["python"]


def test_execution_strategy_phase_is_release_lock_phase():
    assert execution.EXECUTION_STRATEGY_PHASE == "SD-28.7"
    assert execution.EXECUTION_STRATEGY_SCHEMA.endswith(".v7")


def test_active_route_noop_is_locked_not_false_positive_blocked():
    before = _before_graph()
    route = {"backend":"comfyui","family":"krea2","loader":"diffusion_model","mode":"generate"}
    result = {"workflow":deepcopy(before),"workflow_patch":{"applied":False,"mutated":False,"nodes_added":[],"scene_director_execution_strategy":execution.resolve_scene_director_execution_strategy(route)},"mutated":False}
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=route)
    assert lock["status"] == "locked"
    assert lock["allow_output"] is True


def test_inspector_shows_regional_lora_failed_closed_without_blocking_prompt_engine():
    before, result = _modern_result(lora=False)
    result["workflow_patch"]["scene_director_regional_lora_contract"] = {"mode":"krea2_activation_delta_v2","route_count":1,"status":"runtime_node_missing"}
    result["workflow_patch"]["scene_director_regional_lora_status"] = "provider_gated_missing_runtime_node"
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=result["workflow_patch"]["route"])
    ui = inspector_mod.build_scene_director_inspector(validation=result["validation"], workflow_patch=result["workflow_patch"], release_lock=lock)
    chips = {row["id"]: row for row in ui["status_chips"]}
    assert lock["status"] == "locked"
    assert chips["regional_lora"]["state"] == "blocked"
    assert chips["gpu_proof"]["state"] == "blocked"
    assert any("failed closed" in warning for warning in ui["warnings"])


def test_classic_release_matrix_remains_frozen():
    for mode in ("generate", "img2img", "inpaint"):
        sdxl = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":"sdxl","loader":"checkpoint","mode":mode})
        sd15 = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":"sd15","loader":"checkpoint","mode":mode})
        assert sdxl["engine"] == "classic_v054" and sdxl["status"] == "active"
        assert sd15["engine"] == "classic_v054" and sd15["status"] == "experimental"
        assert sdxl["required_comfy_nodes"] == ["NeoSceneDirectorV054"]
    modern_checkpoint = execution.resolve_scene_director_execution_strategy({"backend":"comfyui","family":"krea2","loader":"checkpoint","mode":"generate"})
    assert modern_checkpoint["execution_enabled"] is False


def test_unsupported_family_never_falls_back_to_classic_or_lightweight():
    route = {"backend":"comfyui","family":"qwen_image","loader":"diffusion_model","mode":"generate"}
    strategy = execution.resolve_scene_director_execution_strategy(route)
    assert strategy["engine"] == "unsupported"
    assert strategy["execution_enabled"] is False
    before = _before_graph()
    result = {"workflow":deepcopy(before),"workflow_patch":{"applied":False,"mutated":False},"mutated":False}
    lock = release.evaluate_scene_director_release_lock(before_workflow=before, result=result, route=route, strategy=strategy)
    assert lock["status"] == "gated_safe"
    assert lock["allow_output"] is True


def test_manifest_keeps_every_modern_outpaint_route_planned_gated():
    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    for family in ("krea2","krea2_turbo","flux2_klein","z_image","z_image_turbo"):
        for loader in ("diffusion_model","gguf"):
            assert manifest["route_states"][f"comfyui:{family}:{loader}:outpaint"] == "planned_gated"
