from __future__ import annotations

from typing import Any, Callable
from neo_app.video.video_lora_compatibility_recovery import apply_recovery_action, reconcile_video_lora_rows

NORMAL="cinematic_style.safetensors"; SPEED="wan_lightx2v_4steps.safetensors"
SUPPORT={"supports_standard_lora": True, "supports_speed_lora": True, "allowed_targets": ["all"]}


def run_phase19_gate() -> dict[str, Any]:
    cases=[]
    def run(name: str, fn: Callable[[], Any]):
        try: fn(); cases.append({"name":name,"ok":True})
        except Exception as exc: cases.append({"name":name,"ok":False,"error":f"{type(exc).__name__}: {exc}"})
    row={"uid":"saved","enabled":True,"name":NORMAL,"strength_model":.8,"role":"standard","target":"all"}
    ready=lambda: reconcile_video_lora_rows([row],catalog=[NORMAL,SPEED],support=SUPPORT,catalog_ready=True,stack_enabled=True)
    run("available saved row is ready",lambda: (_ for _ in ()).throw(AssertionError()) if not ready()["generation_allowed"] else None)
    run("case-only filename difference resolves",lambda: (_ for _ in ()).throw(AssertionError()) if not reconcile_video_lora_rows([{**row,"name":NORMAL.upper()}],catalog=[NORMAL],support=SUPPORT,catalog_ready=True,stack_enabled=True)["rows"][0]["canonical_catalog_name"] else None)
    run("missing file preserves intent and blocks",lambda: (lambda r: (_ for _ in ()).throw(AssertionError()) if r["generation_allowed"] or r["rows"][0]["name"]!=NORMAL else None)(reconcile_video_lora_rows([row],catalog=[SPEED],support=SUPPORT,catalog_ready=True,stack_enabled=True)))
    run("missing row offers three recovery actions",lambda: (_ for _ in ()).throw(AssertionError()) if reconcile_video_lora_rows([row],catalog=[],support=SUPPORT,catalog_ready=True,stack_enabled=True)["rows"][0]["actions"] != ["replace","disable","remove"] else None)
    run("catalog outage blocks enabled stack",lambda: (_ for _ in ()).throw(AssertionError()) if reconcile_video_lora_rows([row],catalog=[],support=SUPPORT,catalog_ready=False,stack_enabled=True)["generation_allowed"] else None)
    run("disabled stack preserves issue without blocking",lambda: (_ for _ in ()).throw(AssertionError()) if not reconcile_video_lora_rows([row],catalog=[],support=SUPPORT,catalog_ready=True,stack_enabled=False)["generation_allowed"] else None)
    run("disabled broken row does not block",lambda: (_ for _ in ()).throw(AssertionError()) if not reconcile_video_lora_rows([{**row,"enabled":False}],catalog=[],support=SUPPORT,catalog_ready=True,stack_enabled=True)["generation_allowed"] else None)
    run("speed blocked on standard-only route",lambda: (_ for _ in ()).throw(AssertionError()) if reconcile_video_lora_rows([{**row,"name":SPEED,"role":"speed"}],catalog=[SPEED],support={**SUPPORT,"supports_speed_lora":False},catalog_ready=True,stack_enabled=True)["generation_allowed"] else None)
    run("high target blocked on single-model route",lambda: (_ for _ in ()).throw(AssertionError()) if reconcile_video_lora_rows([{**row,"target":"high"}],catalog=[NORMAL],support=SUPPORT,catalog_ready=True,stack_enabled=True)["generation_allowed"] else None)
    run("suggestion ranks similar replacement",lambda: (_ for _ in ()).throw(AssertionError()) if reconcile_video_lora_rows([{**row,"name":"cinematic_styles.safetensors"}],catalog=[NORMAL,SPEED],support=SUPPORT,catalog_ready=True,stack_enabled=True)["rows"][0]["suggestions"][0]["name"]!=NORMAL else None)
    run("replace preserves uid and other intent",lambda: (lambda rows: (_ for _ in ()).throw(AssertionError()) if rows[0]["uid"]!="saved" or rows[0]["name"]!=SPEED or rows[0]["strength_model"]!=.8 else None)(apply_recovery_action([row],uid="saved",action="replace",replacement=SPEED)))
    run("disable preserves row",lambda: (lambda rows: (_ for _ in ()).throw(AssertionError()) if len(rows)!=1 or rows[0]["enabled"] else None)(apply_recovery_action([row],uid="saved",action="disable")))
    run("remove is explicit",lambda: (_ for _ in ()).throw(AssertionError()) if apply_recovery_action([row],uid="saved",action="remove") else None)
    failed=sum(not c["ok"] for c in cases)
    return {"schema_version":"neo.video.lora_stack.compatibility_recovery_regression.v1","phase":"phase_19","ok":failed==0,"gate":"pass" if failed==0 else "fail","case_count":len(cases),"passed":len(cases)-failed,"failed":failed,"cases":cases}


if __name__=="__main__":
    import json
    r=run_phase19_gate();print(json.dumps(r,indent=2));raise SystemExit(0 if r["ok"] else 1)
