"""Fast Fill uses Neo's upload helpers and normal native-output persister."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from neo_app.image.canonical_source_asset import resolve_canonical_source_asset
from neo_app.image.preview_finish_dispatch import build_derived_action_contract
from . import native_lama


def source_provenance(source, load_record=None):
    if not source.get("result_id"):
        return dict(source)
    if load_record is None:
        from neo_app.image.output_service import load_output_record
        load_record = load_output_record
    record = load_record(source["result_id"])
    lineage = record.get("lineage") or {}
    return {**source, "output_id": source.get("file_id", ""),
            "job_id": (record.get("job") or {}).get("job_id", ""),
            "lineage": lineage, "root_output_id": lineage.get("root_output_id", ""),
            "root_job_id": lineage.get("root_job_id", ""),
            "ancestor_output_ids": lineage.get("ancestor_output_ids", []),
            "lineage_depth": lineage.get("depth", 0)}


def create_fast_fill_router(root_dir: Path, *, save_source, save_mask, persister, runner=None):
    router = APIRouter()
    runner = runner or native_lama.run
    running = threading.Lock()

    @router.get("/fast-fill/status")
    async def fast_fill_status():
        return {"ok": True, **await run_in_threadpool(native_lama.status, root_dir)}

    def execute(source, mask, settings):
        if not running.acquire(blocking=False):
            raise HTTPException(409, "Fast Fill is already running. Wait for it to finish.")
        try:
            job_id = f"fast-fill-{uuid4().hex}"
            output_root = root_dir / "neo_data/runtime/fast_fill" / job_id
            result = runner(Path(source["path"]), Path(mask["path"]), root_dir=root_dir,
                            output_root=output_root, settings=settings)
            evidence = result["runtime"]
            params = {**settings, "workflow_mode": "fast_fill", "resolved_engine": "native_lama",
                      "source_asset": source, "mask_asset": mask, "fast_fill": evidence,
                      "_neo_source_result_id": source.get("result_id", ""),
                      "_neo_source_output_id": source.get("file_id", "")}
            params["_neo_derived_action"] = build_derived_action_contract(
                source_provenance(source), action_id="image.fast_fill", label="Fast Fill",
                profile_id="native.fast_fill", provider_id="neo_native",
                dispatch_type="run_native_derived", execution_mode="fast_fill", cross_provider=True,
            )
            context = {"job_id": job_id, "profile_id": "native.fast_fill",
                       "backend_profile_id": "native.fast_fill", "provider_id": "neo_native",
                       "backend_output_root": str(output_root), "subtab": "finish", "mode": "fast_fill",
                       "prompt": "", "positive_prompt": "", "negative_prompt": "", "params": params,
                       "model": {"family": "standalone", "loader": "lama_onnx", "model": native_lama.MODEL_ID},
                       "extensions": {"used": [{"extension_id": "image.background_removal", "enabled": True}],
                                      "memory_events": {"image.fast_fill": evidence}}}
            saved = persister(result["outputs"], context)
            outputs = saved.get("outputs") or saved.get("files") or []
            if not outputs or saved.get("ok") is False or saved.get("errors"):
                raise RuntimeError("Fast Fill completed but output persistence failed.")
            return {"ok": True, "jobs": [], "resolved_engine": "native_lama", "workflow_mode": "fast_fill",
                    "fallback_used": False, "completed_outputs": outputs,
                    "completed_results": [{"job_id": job_id, "result_id": saved.get("result_id", ""),
                                           "outputs": outputs, "metadata": evidence}], "runtime": evidence}
        finally:
            running.release()

    @router.post("/fast-fill/run")
    async def fast_fill_run(settings_json: str = Form("{}"), source_json: str = Form(""),
                            image_file: UploadFile | None = File(default=None),
                            mask_file: UploadFile = File(...)):
        if persister is None:
            raise HTTPException(503, "Native image result persistence is not configured.")
        try:
            settings = native_lama.normalize_options(json.loads(settings_json))
            if bool(source_json) == bool(image_file):
                raise ValueError("Provide exactly one source: a saved output reference or an image upload.")
            if image_file is not None:
                source = await save_source(image_file, root_dir, 1)
            else:
                reference = json.loads(source_json)
                if not isinstance(reference, dict):
                    raise ValueError("Source reference must be an object.")
                source = await run_in_threadpool(resolve_canonical_source_asset, reference, root_dir=root_dir)
            mask = await save_mask(mask_file, root_dir)
            return await run_in_threadpool(execute, source, mask, settings)
        except HTTPException:
            raise
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, f"Fast Fill failed without fallback: {exc}") from exc

    return router
