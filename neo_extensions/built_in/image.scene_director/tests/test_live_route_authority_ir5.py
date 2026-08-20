from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MANIFEST = ROOT / "extension_manifest.json"
EDITOR = ROOT / "ui" / "editor_restore.js"
NEO_JS = ROOT.parents[2] / "neo_app" / "static" / "js" / "neo.js"
PACKAGE = "neo_scene_director_ir5_testpkg"


def _ensure_package() -> None:
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
    spec = importlib.util.spec_from_file_location(full_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


execution_strategy = _load("execution_strategy")
provider_capabilities = _load("provider_capabilities")
support_matrix = _load("support_matrix")


def _ui_state_from_backend(strategy: dict) -> str:
    status = str(strategy.get("status") or "unsupported")
    engine = str(strategy.get("engine") or "unsupported")
    if status == "active" and strategy.get("execution_enabled"):
        return "available"
    if status == "experimental" and strategy.get("execution_enabled"):
        return "experimental_available"
    if status in {"planned_gated", "provider_gated", "unsupported"}:
        return status
    if engine == "unsupported":
        return "unsupported"
    return "unsupported"


def test_manifest_declares_ir5_live_route_authority_contract():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert tuple(int(part) for part in manifest["version"].split(".")) >= (1, 2, 21)
    contract = manifest["ui_schema"]["route_authority"]
    assert contract["phase"] == "IR-5"
    assert contract["route_state_source"] == "route_states"
    assert contract["fail_closed"] is True
    assert contract["no_cross_family_fallback"] is True
    engines = {item["id"]: item for item in contract["engines"]}
    assert engines["classic_v054"]["families"] == ["sdxl", "sd15"]
    assert set(engines["lightweight_regional"]["families"]) == {
        "krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"
    }
    assert set(engines["lightweight_regional"]["loaders"]) == {"diffusion_model", "gguf"}


def test_manifest_route_states_are_parity_locked_to_backend_strategy():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    route_states = manifest["route_states"]
    matrix: list[tuple[str, str, str]] = []
    for family, loaders in {
        "sdxl": ["checkpoint"],
        "sd15": ["checkpoint"],
        "krea2": ["diffusion_model", "gguf"],
        "krea2_turbo": ["diffusion_model", "gguf"],
        "flux2_klein": ["diffusion_model", "gguf"],
        "z_image": ["diffusion_model", "gguf"],
        "z_image_turbo": ["diffusion_model", "gguf"],
    }.items():
        for loader in loaders:
            for mode in ("generate", "img2img", "inpaint", "outpaint"):
                matrix.append((family, loader, mode))

    for family, loader, mode in matrix:
        strategy = execution_strategy.resolve_scene_director_execution_strategy(
            {"backend": "comfyui", "family": family, "loader": loader, "mode": mode}
        )
        expected = _ui_state_from_backend(strategy)
        key = f"comfyui:{family}:{loader}:{mode}"
        assert key in route_states, f"missing exact UI route state for {key}"
        assert route_states[key] == expected, (key, route_states[key], expected, strategy)


def test_backend_loader_aliases_match_live_ui_semantics():
    modern_aliases = ("safetensors", "components", "component")
    for family in ("krea2", "krea2_turbo", "flux2_klein", "z_image", "z_image_turbo"):
        for loader in modern_aliases:
            strategy = execution_strategy.resolve_scene_director_execution_strategy(
                {"backend": "comfyui", "family": family, "loader": loader, "mode": "generate"}
            )
            assert strategy["engine"] == "lightweight_regional", (family, loader, strategy)
            assert strategy["loader"] == "diffusion_model"
            assert strategy["execution_enabled"] is True

    classic = execution_strategy.resolve_scene_director_execution_strategy(
        {"backend": "comfyui", "family": "sdxl", "loader": "safetensors", "mode": "generate"}
    )
    assert classic["engine"] == "classic_v054"
    assert classic["loader"] == "checkpoint"


def test_unsupported_families_never_fallback_to_classic_or_lightweight():
    for family in ("flux", "qwen_image", "qwen_image_edit_2509", "qwen_rapid_aio", "hidream", "wan_image", "hunyuan_image"):
        strategy = execution_strategy.resolve_scene_director_execution_strategy(
            {"backend": "comfyui", "family": family, "loader": "diffusion_model", "mode": "generate"}
        )
        assert strategy["engine"] == "unsupported"
        assert strategy["execution_enabled"] is False
        assert "fallback" in str(strategy.get("reason") or "").lower() or family in str(strategy.get("reason") or "")


def test_support_matrix_accepts_reference_workspace_for_source_workflows():
    for family in ("krea2", "flux2_klein", "z_image"):
        for mode in ("img2img", "inpaint"):
            support = support_matrix.get_scene_director_support(
                backend="comfyui",
                family=family,
                loader="diffusion_model",
                workflow_mode=mode,
                workspace="image",
                workspace_app="reference",
            )
            assert support["state"] == "available", support
            assert support["execution_engine"] == "lightweight_regional"

    outpaint = support_matrix.get_scene_director_support(
        backend="comfyui", family="z_image", loader="gguf", workflow_mode="outpaint",
        workspace="image", workspace_app="reference",
    )
    assert outpaint["state"] == "planned_gated"


def test_generic_manifest_route_resolver_prefers_exact_modern_gguf_over_wildcard_guard():
    source = NEO_JS.read_text(encoding="utf-8")
    start = source.index("function manifestRouteState(")
    end = source.index("\n\nfunction sceneDirectorRoleToLegacyType", start)
    snippet = source[start:end]
    script = snippet + """
      const record={manifest:{route_states:{
        'comfyui:*:gguf:*':'unsupported',
        'comfyui:krea2:gguf:generate':'available'
      }}};
      const a=manifestRouteState(record,{backend:'comfyui',family:'krea2',loader:'gguf',workflow_mode:'generate'});
      const b=manifestRouteState(record,{backend:'comfyui_portable',family:'krea2',loader:'gguf',workflow_mode:'generate'});
      console.log(JSON.stringify([a,b]));
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout.strip()) == ["available", "available"]


def test_live_neo_route_authority_consumes_manifest_contract_not_checkpoint_only_matrix():
    source = NEO_JS.read_text(encoding="utf-8")
    active_start = source.index("function sceneDirectorUiRouteAuthority(")
    active_end = source.index("function sceneDirectorRouteControlsEnabled", active_start)
    block = source[active_start:active_end]
    assert "ui_schema?.route_authority" in block
    assert "manifestRouteState(record, route)" in block
    assert "loader !== 'checkpoint'" not in block
    assert "Scene Director is checkpoint-only" not in block
    assert "NeoSceneDirectorRouteAuthority" in source
    assert "engine_label" in block
    assert "regional_prompt_mode" in block


def test_extension_editor_consumes_host_route_authority_and_has_no_family_support_matrix():
    source = EDITOR.read_text(encoding="utf-8")
    assert "NeoSceneDirectorRouteAuthority?.resolve" in source
    assert "const MODERN = new Set" not in source
    assert "const CLASSIC = new Set" not in source
    assert "MODERN_LOADERS" not in source
    assert "#imageWorkspaceFamily" in source
    assert "#imageWorkspaceLoader" in source

    script = f"""
      global.window=global;
      global.document={{readyState:'loading',querySelector:()=>null,addEventListener:()=>{{}},documentElement:null}};
      global.MutationObserver=function(){{}};
      global.NeoSceneDirectorRouteAuthority={{resolve:(route)=>({{
        route_state: route.mode==='outpaint' ? 'planned_gated' : 'available',
        engine:'lightweight_regional',engine_label:'Lightweight Regional',
        family:'krea2',loader:'diffusion_model',reason:'authoritative test route'
      }})}};
      require({json.dumps(str(EDITOR))});
      const active=global.NeoSceneDirectorEditor.setRouteContext({{family:'krea2',loader:'safetensors',mode:'generate',backend:'comfyui'}});
      const planned=global.NeoSceneDirectorEditor.setRouteContext({{family:'krea2',loader:'safetensors',mode:'outpaint',backend:'comfyui'}});
      console.log(JSON.stringify([active,planned]));
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    active, planned = json.loads(result.stdout.strip())
    assert active["route"] == "available"
    assert active["engine"] == "Krea2 Regional"
    assert planned["route"] == "planned gated"
    assert planned["prompt"] == "blocked"


def test_live_ui_labels_report_engine_not_false_v054_node_claim():
    source = NEO_JS.read_text(encoding="utf-8")
    assert "Engine: ${escapeHtml(route.engine_label)}" in source
    assert "Runtime: ${escapeHtml(route.node_label)}" in source
    assert "Node readiness: V054 preferred" not in source
    assert "Node: V054 active" not in source


def test_live_neo_scene_director_resolver_matches_supported_and_blocked_routes():
    source = NEO_JS.read_text(encoding="utf-8")
    manifest_start = source.index("function manifestRouteState(")
    manifest_end = source.index("\n\nfunction sceneDirectorRoleToLegacyType", manifest_start)
    manifest_fn = source[manifest_start:manifest_end]
    route_start = source.index("function sceneDirectorUiRouteAuthority(")
    route_end = source.index("function sceneDirectorRouteControlsEnabled", route_start)
    route_fns = source[route_start:route_end]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    script = manifest_fn + "\n" + """
      const window=globalThis;
      const SCENE_DIRECTOR_EXTENSION_ID='image.scene_director';
      const state={imageDraft:{},activeSurfaceId:'image',activeWorkspaceAppId:'generations',activeSubtabId:'generate'};
      let __route={};
      let __record={manifest:MANIFEST_DATA};
      function extensionRouteSnapshot(record){ return {...__route}; }
      function getImageWorkflowMode(){ return __route.workflow_mode || __route.mode || 'generate'; }
      function getSurfaceWorkspaceAppId(){ return __route.workspace_app || 'generations'; }
      function extensionRecordById(){ return __record; }
      function imageWorkflowExtensionRecordById(){ return __record; }
    """.replace("MANIFEST_DATA", json.dumps(manifest)) + "\n" + route_fns + "\n" + """
      const cases=[
        {family:'krea2',loader:'safetensors',workflow_mode:'generate',backend:'comfyui_portable'},
        {family:'flux2_klein',loader:'gguf',workflow_mode:'img2img',backend:'comfyui'},
        {family:'zimage_turbo',loader:'components',workflow_mode:'inpaint',backend:'comfyui'},
        {family:'sdxl',loader:'safetensors',workflow_mode:'generate',backend:'comfyui'},
        {family:'krea2',loader:'diffusion_model',workflow_mode:'outpaint',backend:'comfyui'},
        {family:'flux',loader:'diffusion_model',workflow_mode:'generate',backend:'comfyui'},
        {family:'qwen_image',loader:'diffusion_model',workflow_mode:'generate',backend:'comfyui'},
      ];
      const out=cases.map((item)=>{ __route=item; return sceneDirectorActiveRoute(__record,item); });
      console.log(JSON.stringify(out.map((r)=>({state:r.route_state,engine:r.engine,family:r.family,loader:r.loader,backend:r.backend}))));
    """
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    rows = json.loads(completed.stdout.strip())
    assert rows[0] == {"state": "available", "engine": "lightweight_regional", "family": "krea2", "loader": "diffusion_model", "backend": "comfyui"}
    assert rows[1]["state"] == "available" and rows[1]["engine"] == "lightweight_regional"
    assert rows[2]["state"] == "available" and rows[2]["family"] == "z_image_turbo" and rows[2]["loader"] == "diffusion_model"
    assert rows[3] == {"state": "available", "engine": "classic_v054", "family": "sdxl", "loader": "checkpoint", "backend": "comfyui"}
    assert rows[4]["state"] == "planned_gated" and rows[4]["engine"] == "lightweight_regional"
    assert rows[5]["state"] == "unsupported" and rows[5]["engine"] == "unsupported"
    assert rows[6]["state"] == "unsupported" and rows[6]["engine"] == "unsupported"
