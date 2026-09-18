from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib
import json
from pathlib import Path
from typing import Any,Callable

ROOT=Path(__file__).resolve().parents[2]
SCHEMA_VERSION="neo.video.lora_stack.release_acceptance.v1"
GATES=(
    ("phase_6_h3","neo_app.video.minimax_h3_lora_regression","run_minimax_h3_lora_regression"),("phase_7_ltx","neo_app.video.ltx_lora_regression","run_regression"),("phase_8_wan","neo_app.video.wan_lora_regression","run_phase8_gate"),("phase_9_legacy_reader","neo_app.video.video_lora_legacy_compat_regression","run_phase9_gate"),("phase_10_ui","neo_app.video.video_lora_ui_regression","run_video_lora_ui_regression"),
    ("phase_12_persistence","neo_app.video.video_lora_persistence_release_regression","run_persistence_release_gate"),("phase_13_h3_gguf","neo_app.video.minimax_h3_gguf_lora_regression","run_minimax_h3_gguf_lora_regression"),("phase_14_ltx_complete","neo_app.video.ltx_complete_route_lora_regression","run_ltx_complete_route_lora_regression"),("phase_15_wan_unet_speed","neo_app.video.wan_unet_speed_lora_regression","run_wan_unet_speed_lora_regression"),("phase_16_wan_rapid_aio","neo_app.video.wan_rapid_aio_lora_regression","run_phase16_gate"),("phase_17_native_contract","neo_app.video.native_workflow_lora_contract_regression","run_phase17_gate"),("phase_18_gpu_framework","neo_app.video.physical_gpu_validation_regression","run_phase18_gate"),("phase_19_recovery","neo_app.video.video_lora_compatibility_recovery_regression","run_phase19_gate"),("phase_20_retirement","neo_app.video.video_lora_legacy_retirement_regression","run_phase20_gate"),("phase_20_1_sigma","neo_app.video.h3_sigma_shift_regression","run_h3_sigma_shift_regression"),
)

def _run(module:str,function:str)->dict[str,Any]:
    runner:Callable[[],dict[str,Any]]=getattr(importlib.import_module(module),function);result=runner()
    if not isinstance(result,dict): raise TypeError(f"{module}.{function} returned a non-object")
    return result

def _support_report()->dict[str,Any]:
    data=json.loads((ROOT/"neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json").read_text(encoding="utf-8"));rows=[]
    for group in data.get("groups",[]):
        if not isinstance(group,dict): continue
        for route in group.get("route_ids") or []: rows.append({"route_id":str(route),"group":group.get("name"),"state":group.get("state"),"standard":bool(group.get("supports_standard_lora")),"speed":bool(group.get("supports_speed_lora")),"targets":list(group.get("allowed_targets") or []),"validated_topology":bool(group.get("validated_topology")),"reason":group.get("reason")})
    counts={state:sum(row["state"]==state for row in rows) for state in sorted({str(row["state"]) for row in rows})};unsafe=[row["route_id"] for row in rows if row["state"]=="supported" and not row["validated_topology"]]
    return {"schema_version":data.get("schema_version"),"route_count":len(rows),"state_counts":counts,"unsafe_supported_routes":unsafe,"routes":rows}

def _static_contracts()->list[dict[str,Any]]:
    neo=(ROOT/"neo_app/static/js/neo.js").read_text(encoding="utf-8");persistence=(ROOT/"neo_app/video/video_lora_persistence.py").read_text(encoding="utf-8");manifest=json.loads((ROOT/"neo_extensions/built_in/video.lora_stack/extension_manifest.json").read_text(encoding="utf-8"));compiler=(ROOT/"neo_app/video/minimax_h3_compiler.py").read_text(encoding="utf-8")
    checks={"legacy normal-user banner removed":"video-lora-legacy-banner" not in neo,"legacy migration button removed":"videoLoraMigrateLegacyBtn" not in neo,"one-way browser reader active":"retireLegacyVideoLoraDraft" in neo,"persistence retirement active":"video_lora_legacy_retirement import retire_legacy_payload" in persistence,"manifest declares Phase 20":(manifest.get("ui_schema") or {}).get("phase")=="phase_20","manifest forbids legacy writeback":(manifest.get("ui_schema") or {}).get("legacy_writeback") is False,"H3 sigma keys mapped":'{"shift_video": params["h3_shift_video"], "shift_audio": params["h3_shift_audio"]}' in compiler}
    return [{"name":name,"ok":ok} for name,ok in checks.items()]

def _gpu_report(path:str)->dict[str,Any]:
    if not path:return {"provided":False,"gate":"not_run","physical_inference_proven":False}
    data=json.loads(Path(path).read_text(encoding="utf-8"));cases=data.get("cases") if isinstance(data.get("cases"),list) else [];proven=bool(data.get("gate")=="pass" and cases and all(case.get("physical_inference_proven") for case in cases))
    return {"provided":True,"path":str(path),"gate":data.get("gate"),"case_count":len(cases),"physical_inference_proven":proven}

def run_release_acceptance(gpu_evidence:str="")->dict[str,Any]:
    reports=[]
    for gate_id,module,function in GATES:
        try:
            result=_run(module,function);ok=result.get("gate")=="pass" and int(result.get("failed") or 0)==0;reports.append({"gate_id":gate_id,"ok":ok,"case_count":int(result.get("case_count") or 0),"schema_version":result.get("schema_version"),"error":None if ok else result})
        except Exception as exc:reports.append({"gate_id":gate_id,"ok":False,"case_count":0,"error":f"{type(exc).__name__}: {exc}"})
    static=_static_contracts();support=_support_report();gpu=_gpu_report(gpu_evidence);code_ready=all(item["ok"] for item in reports) and all(item["ok"] for item in static) and not support["unsafe_supported_routes"]
    return {"schema_version":SCHEMA_VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),"phase":"phase_21","code_ready":code_ready,"production_proven":bool(code_ready and gpu["physical_inference_proven"]),"deterministic_case_count":sum(item["case_count"] for item in reports),"gate":"pass" if code_ready else "fail","regression_gates":reports,"static_contracts":static,"support_matrix":support,"gpu_evidence":gpu,"release_note":"Code-ready is not physical inference proof. Production-proven requires a complete passing Phase 18 GPU evidence report."}

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--gpu-evidence",default="");parser.add_argument("--output",default="phase21_acceptance_report.json");args=parser.parse_args();report=run_release_acceptance(args.gpu_evidence);Path(args.output).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,indent=2));return 0 if report["code_ready"] else 1

if __name__=="__main__":raise SystemExit(main())
