from __future__ import annotations
from copy import deepcopy
import json
from typing import Any,Callable
from neo_app.video.video_lora_persistence import EXTENSION_ID,attach_state_to_replay,build_video_lora_persistence,inspector_payload,state_from_record

def _request(rows:list[dict[str,Any]],enabled:bool=True)->dict[str,Any]:return {"extensions":{EXTENSION_ID:{"enabled":enabled,"version":1,"params":{"loras":rows}}}}

def run_persistence_release_gate()->dict[str,Any]:
    cases=[]
    def record(name:str,fn:Callable[[],Any])->None:
        try:fn();cases.append({"name":name,"ok":True})
        except Exception as exc:cases.append({"name":name,"ok":False,"error":f"{type(exc).__name__}: {exc}"})
    def check(value:bool)->None:
        if not value:raise AssertionError("contract failed")
    rows=[{"uid":"style","enabled":True,"name":"style.safetensors","strength_model":.72,"role":"standard","target":"all"},{"uid":"turbo","enabled":True,"name":"turbo.safetensors","strength_model":1.0,"role":"speed","target":"all"},{"uid":"paused","enabled":False,"name":"paused.safetensors","strength_model":.4,"role":"standard","target":"all"}]
    saved=build_video_lora_persistence({"video_lora_stack":{"active":True,"applied":rows[:2]}},_request(rows))
    record("save preserves exact canonical rows",lambda:check(saved["rows"]==rows))
    record("reload preserves canonical state",lambda:check(state_from_record({"video_lora_stack":deepcopy(saved)})==saved))
    record("replay emits canonical extension only",lambda:check("h3_turbo_enabled" not in attach_state_to_replay({"h3_turbo_enabled":True},saved) and EXTENSION_ID in attach_state_to_replay({},saved)["extensions"]))
    record("disabled stack preserves rows without extension execution",lambda:check(EXTENSION_ID not in attach_state_to_replay({},build_video_lora_persistence({},_request(rows,False)))["extensions"]))
    missing=build_video_lora_persistence({"video_lora_stack":{"missing_loras":["style.safetensors"]}},_request(rows[:1]))
    record("missing file stays repairable",lambda:check(inspector_payload({"video_lora_stack":missing})["repair_required"]))
    legacy={"h3_turbo_enabled":True,"h3_turbo_lora":"old.safetensors","h3_turbo_strength":.9}
    retired=state_from_record(legacy)
    record("legacy H3 state migrates once",lambda:check(retired["rows"][0]["name"]=="old.safetensors" and retired["legacy_field_writeback"] is False))
    replay=attach_state_to_replay(legacy,retired)
    record("migrated replay strips every legacy key",lambda:check(not any(key in replay for key in legacy)))
    failed=sum(not case["ok"] for case in cases);return {"schema_version":"neo.video.lora_stack.persistence_release_regression.v1","phase":"phase_21","ok":failed==0,"gate":"pass" if failed==0 else "fail","case_count":len(cases),"passed":len(cases)-failed,"failed":failed,"cases":cases}

if __name__=="__main__":
    report=run_persistence_release_gate();print(json.dumps(report,indent=2));raise SystemExit(0 if report["ok"] else 1)
