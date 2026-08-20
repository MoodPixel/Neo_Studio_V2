from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension_manifest.json"
EDITOR = ROOT / "ui" / "editor_restore.js"


def test_manifest_mounts_reference_workflows_in_reference_workspace():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert tuple(int(part) for part in data["version"].split(".")) >= (1, 2, 21)
    assert set(data["workspace_apps"]) == {"generations", "reference"}
    targets = {item["workflow_mode"]: item for item in data["mount_targets"]}
    assert targets["generate"]["workspace_app"] == "generations"
    assert targets["generate"]["slot"] == "image.generate.scene_director"
    for mode in ("img2img", "inpaint", "outpaint"):
        assert targets[mode]["workspace_app"] == "reference"
        assert targets[mode]["slot"] == f"image.{mode}.scene_director"


def test_manifest_accepts_live_ui_family_and_loader_aliases():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for family in ("krea2", "krea2_raw", "flux2_klein", "flux2", "z_image", "zimage_turbo"):
        assert family in data["supported_families"]
    for loader in ("checkpoint", "diffusion_model", "gguf", "safetensors", "components"):
        assert loader in data["supported_loaders"]


def test_editor_consumes_live_authority_for_modern_aliases():
    script = f"""
      global.window=global;
      global.document={{readyState:'loading',querySelector:()=>null,addEventListener:()=>{{}},documentElement:null}};
      global.MutationObserver=function(){{}};
      global.NeoSceneDirectorRouteAuthority={{resolve:(route)=>({{
        route_state:'available',engine:'lightweight_regional',engine_label:'Lightweight Regional',
        family:route.family,loader:route.loader,reason:'host authority'
      }})}};
      require({json.dumps(str(EDITOR))});
      const cases=[
        {{family:'krea2',loader:'safetensors',mode:'txt2img',backend:'comfyui_portable'}},
        {{family:'flux2_klein',loader:'components',mode:'generate',backend:'comfyui'}},
        {{family:'zimage_turbo',loader:'gguf',mode:'inpaint',backend:'comfyui'}},
      ];
      const out=cases.map((item)=>global.NeoSceneDirectorEditor.setRouteContext(item));
      console.log(JSON.stringify(out));
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    rows = json.loads(result.stdout.strip())
    assert all(row["route"] == "available" for row in rows)
    assert rows[0]["engine"] == "Krea2 Regional"
    assert rows[1]["engine"] == "Lightweight Regional"
    assert rows[2]["engine"] == "Lightweight Regional"


def test_outpaint_is_visible_but_execution_stays_planned_gated():
    script = f"""
      global.window=global;
      global.document={{readyState:'loading',querySelector:()=>null,addEventListener:()=>{{}},documentElement:null}};
      global.MutationObserver=function(){{}};
      global.NeoSceneDirectorRouteAuthority={{resolve:(route)=>({{
        route_state:route.mode==='outpaint'?'planned_gated':'available',
        engine:'lightweight_regional',engine_label:'Lightweight Regional',family:'krea2',loader:'diffusion_model',
        reason:'host authority'
      }})}};
      require({json.dumps(str(EDITOR))});
      console.log(JSON.stringify(global.NeoSceneDirectorEditor.setRouteContext({{family:'krea2',loader:'diffusion_model',mode:'outpaint',backend:'comfyui'}})));
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    row = json.loads(result.stdout.strip())
    assert row["route"] == "planned gated"
    assert row["prompt"] == "blocked"
