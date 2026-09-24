from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from neo_app.image.krea2_contract import (
    KREA2_EDIT_ENGINE_IDENTITY,
    KREA2_EDIT_ENGINE_NATIVE,
    KREA2_EDIT_ENGINE_OSTRIS,
    KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL,
    KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA,
    normalize_krea2_edit_engine,
    normalize_krea2_edit_weight_source,
    normalize_krea2_ostris_kv_cache,
)
from neo_app.providers.schema import NeoJob


KREA2_QUALIFICATION_SCHEMA = "neo.image.krea2_physical_qualification.v1"
KREA2_QUALIFICATION_REPORT_SCHEMA = "neo.image.krea2_physical_qualification_report.v1"
KREA2_QUALIFICATION_REVIEW_SCHEMA = "neo.image.krea2_physical_qualification_review.v1"

QUALIFICATION_STATES = (
    "pending",
    "skipped_missing_config",
    "backend_unreachable",
    "compile_blocked",
    "queue_failed",
    "runtime_failed",
    "completed_needs_visual_review",
    "qualified",
    "rejected",
)

EXECUTABLE_ENGINES = {
    KREA2_EDIT_ENGINE_NATIVE,
    KREA2_EDIT_ENGINE_IDENTITY,
    KREA2_EDIT_ENGINE_OSTRIS,
}
EXECUTABLE_LOADERS = {"diffusion_model", "gguf"}
EXECUTABLE_MODES = {"img2img", "edit", "inpaint", "outpaint"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _source_value(case: dict[str, Any], lane: int) -> str:
    key = "source_image" if lane == 1 else f"source_image_{lane}"
    return _clean_text(case.get(key))


def _case_id(case: dict[str, Any], index: int = 0) -> str:
    explicit = _clean_text(case.get("case_id") or case.get("id"))
    if explicit:
        return explicit
    engine = normalize_krea2_edit_engine(case.get("engine") or case.get("krea2_edit_engine") or "native")
    weight = normalize_krea2_edit_weight_source(case.get("weight_source") or case.get("krea2_edit_weight_source") or "separate_lora")
    loader = _clean_text(case.get("loader") or "diffusion_model")
    mode = _clean_text(case.get("mode") or "img2img")
    return f"case_{index + 1}_{engine}_{weight}_{loader}_{mode}"


def normalize_qualification_case(case: dict[str, Any], index: int = 0) -> dict[str, Any]:
    raw = deepcopy(case if isinstance(case, dict) else {})
    engine = normalize_krea2_edit_engine(raw.get("engine") or raw.get("krea2_edit_engine") or "native")
    weight_source = "none" if engine == KREA2_EDIT_ENGINE_NATIVE else normalize_krea2_edit_weight_source(
        raw.get("weight_source") or raw.get("krea2_edit_weight_source") or KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA
    )
    loader = _clean_text(raw.get("loader") or "diffusion_model").lower()
    mode = _clean_text(raw.get("mode") or "img2img").lower()
    family = _clean_text(raw.get("family") or "krea2").lower()
    references = [value for lane in (1, 2, 3) if (value := _source_value(raw, lane))]
    normalized = {
        **raw,
        "case_id": _case_id(raw, index),
        "enabled": bool(raw.get("enabled", True)),
        "family": family,
        "loader": loader,
        "mode": mode,
        "engine": engine,
        "weight_source": weight_source,
        "kv_cache": normalize_krea2_ostris_kv_cache(raw.get("kv_cache") or raw.get("krea2_ostris_kv_cache")) if engine == KREA2_EDIT_ENGINE_OSTRIS else False,
        "reference_count": len(references),
        "prompt": _clean_text(raw.get("prompt") or "Preserve the subject identity and apply a controlled visible edit."),
        "negative_prompt": _clean_text(raw.get("negative_prompt")),
        "steps": int(raw.get("steps") or (8 if family == "krea2_turbo" else 20)),
        "cfg": float(raw.get("cfg") if raw.get("cfg") not in (None, "") else (1.0 if family == "krea2_turbo" else 3.0)),
        "denoise": float(raw.get("denoise") if raw.get("denoise") not in (None, "") else 1.0),
        "seed": int(raw.get("seed") if raw.get("seed") not in (None, "") else 1701),
        "width": int(raw.get("width") or 1024),
        "height": int(raw.get("height") or 1024),
        "repeat_count": max(1, min(5, int(raw.get("repeat_count") or 1))),
        "global_loras": list(raw.get("global_loras") or []) if isinstance(raw.get("global_loras"), list) else [],
        "manual_checks": list(raw.get("manual_checks") or []) if isinstance(raw.get("manual_checks"), list) else [],
    }
    return normalized


def qualification_case_requirements(case: dict[str, Any]) -> dict[str, Any]:
    row = normalize_qualification_case(case)
    missing: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []

    if row["family"] not in {"krea2", "krea2_turbo"}:
        invalid.append("family must be krea2 or krea2_turbo")
    if row["loader"] not in EXECUTABLE_LOADERS:
        invalid.append("loader must be diffusion_model or gguf")
    if row["mode"] not in EXECUTABLE_MODES:
        invalid.append("mode must be img2img, edit, inpaint, or outpaint")
    if row["engine"] not in EXECUTABLE_ENGINES:
        invalid.append("engine must be native, identity_edit, or ostris_edit")

    for field in ("model", "text_encoder", "vae", "source_image"):
        if not _clean_text(row.get(field)):
            missing.append(field)

    if row["mode"] == "inpaint" and not _clean_text(row.get("mask_image")):
        missing.append("mask_image")
    if row["mode"] == "outpaint":
        padding = row.get("outpaint") if isinstance(row.get("outpaint"), dict) else {}
        if not any(int(padding.get(side, 0) or 0) > 0 for side in ("left", "right", "top", "bottom")):
            missing.append("outpaint padding")

    if row["engine"] != KREA2_EDIT_ENGINE_NATIVE and row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA:
        if not _clean_text(row.get("edit_lora")):
            missing.append("edit_lora")
    if row["engine"] == KREA2_EDIT_ENGINE_IDENTITY and row["reference_count"] > 2:
        invalid.append("Identity Edit supports at most two ordered references")
    if row["engine"] == KREA2_EDIT_ENGINE_OSTRIS and row["reference_count"] > 3:
        invalid.append("Ostris Edit supports at most three ordered references")
    if row["engine"] == KREA2_EDIT_ENGINE_OSTRIS and _source_value(row, 3) and not _source_value(row, 2):
        invalid.append("Ostris Image 3 requires Image 2 because references are ordered")
    if row["engine"] == KREA2_EDIT_ENGINE_OSTRIS and row["kv_cache"]:
        warnings.append("KV Cache is enabled; qualify only with a LoRA/model whose training documentation explicitly requires KV Cache.")
    if row["engine"] != KREA2_EDIT_ENGINE_NATIVE and row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL:
        warnings.append("Baked mode must use a model that actually contains the selected engine's edit weights; Neo cannot infer that from the filename alone.")
    for idx, lora in enumerate(row.get("global_loras") or []):
        if not isinstance(lora, dict) or not _clean_text(lora.get("name")):
            missing.append(f"global_loras[{idx}].name")

    return {
        "ok": not missing and not invalid,
        "missing": missing,
        "invalid": invalid,
        "warnings": warnings,
        "case": row,
    }


def build_qualification_job(case: dict[str, Any], *, uploaded_names: dict[str, str] | None = None) -> NeoJob:
    check = qualification_case_requirements(case)
    row = check["case"]
    uploaded = uploaded_names or {}
    params: dict[str, Any] = {
        "seed": row["seed"],
        "requested_seed": row["seed"],
        "actual_seed": row["seed"],
        "width": row["width"],
        "height": row["height"],
        "steps": row["steps"],
        "cfg": row["cfg"],
        "denoise": row["denoise"],
        "sampler": _clean_text(row.get("sampler") or "euler"),
        "scheduler": _clean_text(row.get("scheduler") or "simple"),
        "qwen3vl_text_encoder": _clean_text(row.get("text_encoder")),
        "vae": _clean_text(row.get("vae")),
        "source_image": uploaded.get("source_image") or _clean_text(row.get("source_image")),
        "comfy_source_image_name": uploaded.get("source_image") or "",
        "krea2_edit_engine": row["engine"],
    }
    if row["loader"] == "gguf":
        params["gguf_model"] = _clean_text(row.get("model"))
    else:
        params["diffusion_model"] = _clean_text(row.get("model"))

    for lane in (2, 3):
        source_key = f"source_image_{lane}"
        value = uploaded.get(source_key) or _clean_text(row.get(source_key))
        if value:
            params[source_key] = value
            params[f"comfy_source_image_{lane}_name"] = uploaded.get(source_key) or ""

    if row["mode"] == "inpaint":
        params["mask_image"] = uploaded.get("mask_image") or _clean_text(row.get("mask_image"))
        params["comfy_mask_image_name"] = uploaded.get("mask_image") or ""
        params["mask_grow"] = int(row.get("mask_grow") or 3)
        params["mask_blur"] = int(row.get("mask_blur") or 0)
    if row["mode"] == "outpaint":
        padding = row.get("outpaint") if isinstance(row.get("outpaint"), dict) else {}
        params.update({
            "outpaint_left": int(padding.get("left", 0) or 0),
            "outpaint_right": int(padding.get("right", 0) or 0),
            "outpaint_top": int(padding.get("top", 0) or 0),
            "outpaint_bottom": int(padding.get("bottom", 0) or 0),
            "outpaint_feather": int(padding.get("feather", 16) or 16),
        })

    if row["engine"] != KREA2_EDIT_ENGINE_NATIVE:
        params["krea2_edit_weight_source"] = row["weight_source"]
        if row["engine"] == KREA2_EDIT_ENGINE_IDENTITY:
            if row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA:
                params["krea2_identity_edit_lora"] = _clean_text(row.get("edit_lora"))
                params["krea2_identity_edit_lora_strength"] = float(row.get("lora_strength") if row.get("lora_strength") not in (None, "") else 1.0)
            params["krea2_identity_edit_ref_boost"] = float(row.get("identity_ref_boost") if row.get("identity_ref_boost") not in (None, "") else 4.0)
            params["krea2_identity_edit_ref_boost_a"] = float(row.get("scene_ref_boost") if row.get("scene_ref_boost") not in (None, "") else 1.0)
            params["krea2_identity_edit_fit_mode"] = _clean_text(row.get("fit_mode") or "fit")
            params["krea2_identity_edit_grounding_px"] = int(row.get("grounding_px") if row.get("grounding_px") not in (None, "") else 768)
        elif row["engine"] == KREA2_EDIT_ENGINE_OSTRIS:
            if row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA:
                params["krea2_ostris_edit_lora"] = _clean_text(row.get("edit_lora"))
                params["krea2_ostris_edit_lora_strength"] = float(row.get("lora_strength") if row.get("lora_strength") not in (None, "") else 1.0)
            params["krea2_ostris_kv_cache"] = bool(row["kv_cache"])

    lora_rows = []
    for idx, lora in enumerate(row.get("global_loras") or []):
        if not isinstance(lora, dict) or not _clean_text(lora.get("name")):
            continue
        lora_rows.append({
            "uid": _clean_text(lora.get("uid") or f"krea4-global-{idx + 1}"),
            "enabled": lora.get("enabled", True) is not False,
            "name": _clean_text(lora.get("name")),
            "strength": float(lora.get("strength") if lora.get("strength") not in (None, "") else 0.7),
            "target": _clean_text(lora.get("target") or "both"),
            "apply_to": _clean_text(lora.get("apply_to") or "global"),
        })
    extensions = {}
    if lora_rows:
        extensions = {
            "payloads": {
                "lora_stack": {
                    "enabled": True,
                    "version": 1,
                    "inputs": {"loras": lora_rows},
                    "params": {"loras": lora_rows},
                    "assets": {},
                    "metadata": {"source": "krea4_physical_qualification"},
                }
            }
        }

    return NeoJob(
        surface="image",
        subtab=row["mode"],
        mode=row["mode"],
        provider_id="comfyui",
        family=row["family"],
        loader=row["loader"],
        model=_clean_text(row.get("model")),
        prompt=row["prompt"],
        negative_prompt=row["negative_prompt"],
        params=params,
        extensions=extensions,
    )


def manual_review_template(case: dict[str, Any]) -> dict[str, Any]:
    row = normalize_qualification_case(case)
    checks = {
        "output_opens": False,
        "no_runtime_artifacts_or_black_frame": False,
        "requested_edit_is_visible": False,
        "source_content_is_not_unintentionally_destroyed": False,
    }
    if row["engine"] == KREA2_EDIT_ENGINE_IDENTITY:
        checks.update({
            "identity_is_preserved": False,
            "reference_order_matches_identity_contract": False,
        })
        if row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL:
            checks["no_double_lora_signature_or_overcook"] = False
    if row["engine"] == KREA2_EDIT_ENGINE_OSTRIS:
        checks.update({
            "reference_conditioning_is_visible": False,
            "ordered_reference_stack_behaves_as_expected": False,
            "kv_cache_matches_training_contract": False,
        })
        if row["weight_source"] == KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL:
            checks["no_double_lora_signature_or_overcook"] = False
    if row["mode"] == "inpaint":
        checks["mask_boundary_and_unmasked_preservation_are_acceptable"] = False
    if row["mode"] == "outpaint":
        checks["expanded_canvas_is_filled_without_source_corruption"] = False
    for check in row.get("manual_checks") or []:
        key = _clean_text(check).lower().replace(" ", "_").replace("-", "_")
        if key:
            checks[key] = False
    if row.get("repeat_count", 1) > 1:
        checks["repeated_runs_are_stable_for_the_intended_test"] = False
    if row.get("global_loras"):
        checks["global_lora_stack_effect_is_present_without_breaking_edit_engine"] = False
    return {
        "schema": KREA2_QUALIFICATION_REVIEW_SCHEMA,
        "case_id": row["case_id"],
        "verdict": "pending",
        "checks": checks,
        "notes": "",
        "reviewer": "",
        "reviewed_at": None,
    }


def new_case_evidence(case: dict[str, Any]) -> dict[str, Any]:
    row = normalize_qualification_case(case)
    requirements = qualification_case_requirements(row)
    state = "pending" if requirements["ok"] and row["enabled"] else "skipped_missing_config"
    if not row["enabled"]:
        state = "skipped_missing_config"
    return {
        "schema": KREA2_QUALIFICATION_SCHEMA,
        "case": row,
        "state": state,
        "counts_as_qualified": False,
        "requirements": {
            "ok": requirements["ok"],
            "missing": requirements["missing"],
            "invalid": requirements["invalid"],
            "warnings": requirements["warnings"],
        },
        "compile": {},
        "runtime": {},
        "outputs": [],
        "review": manual_review_template(row),
    }


def mark_compile_result(evidence: dict[str, Any], compiled: Any) -> dict[str, Any]:
    out = deepcopy(evidence)
    payload = compiled.backend_payload if hasattr(compiled, "backend_payload") else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    actual = payload.get("actual_params") if isinstance(payload.get("actual_params"), dict) else {}
    readiness = actual.get("_neo_krea2_edit_readiness") if isinstance(actual.get("_neo_krea2_edit_readiness"), dict) else {}
    out["compile"] = {
        "compile_status": getattr(compiled, "compile_status", ""),
        "validation_ok": bool(validation.get("ok")),
        "errors": list(validation.get("errors") or []),
        "warnings": list(validation.get("warnings") or []),
        "readiness": readiness,
        "workflow_engine": (payload.get("compile_route") or {}).get("workflow_engine") if isinstance(payload.get("compile_route"), dict) else None,
    }
    if not validation.get("ok") or getattr(compiled, "compile_status", "") != "compiled":
        out["state"] = "compile_blocked"
    return out


def mark_runtime_result(
    evidence: dict[str, Any],
    *,
    prompt_id: str | None,
    status: str,
    elapsed_seconds: float | None = None,
    outputs: Iterable[dict[str, Any]] = (),
    error: str = "",
    history_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = deepcopy(evidence)
    output_rows = [dict(row) for row in outputs]
    out["runtime"] = {
        "prompt_id": prompt_id or "",
        "status": status,
        "elapsed_seconds": elapsed_seconds,
        "error": error,
        "history_status": history_status or {},
    }
    out["outputs"] = output_rows
    if status == "backend_unreachable":
        out["state"] = "backend_unreachable"
    elif status == "queue_failed":
        out["state"] = "queue_failed"
    elif status != "success" or not output_rows:
        out["state"] = "runtime_failed"
    else:
        out["state"] = "completed_needs_visual_review"
    out["counts_as_qualified"] = False
    return out


def apply_manual_review(evidence: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(evidence)
    expected = manual_review_template(out.get("case") or {})
    supplied = review if isinstance(review, dict) else {}
    checks = dict(expected["checks"])
    checks.update({key: bool(value) for key, value in (supplied.get("checks") or {}).items() if key in checks})
    verdict = _clean_text(supplied.get("verdict") or "pending").lower()
    runtime_success = out.get("state") in {"completed_needs_visual_review", "qualified", "rejected"} and bool(out.get("outputs"))
    all_checks = bool(checks) and all(checks.values())
    qualified = runtime_success and verdict == "pass" and all_checks
    rejected = runtime_success and verdict == "fail"
    out["review"] = {
        **expected,
        **{key: supplied.get(key, expected.get(key)) for key in ("notes", "reviewer")},
        "verdict": verdict,
        "checks": checks,
        "reviewed_at": supplied.get("reviewed_at") or (utc_now_iso() if verdict in {"pass", "fail"} else None),
    }
    if qualified:
        out["state"] = "qualified"
        out["counts_as_qualified"] = True
    elif rejected:
        out["state"] = "rejected"
        out["counts_as_qualified"] = False
    else:
        out["state"] = "completed_needs_visual_review" if runtime_success else out.get("state", "pending")
        out["counts_as_qualified"] = False
    return out


def qualification_summary(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(cases)
    counts = {state: 0 for state in QUALIFICATION_STATES}
    for row in rows:
        state = _clean_text(row.get("state") or "pending")
        counts[state] = counts.get(state, 0) + 1
    enabled_rows = [row for row in rows if row.get("case", {}).get("enabled", True)]
    return {
        "total": len(rows),
        "enabled": len(enabled_rows),
        "qualified": sum(1 for row in rows if row.get("counts_as_qualified") is True),
        "counts": counts,
        "all_qualified": bool(enabled_rows) and all(row.get("counts_as_qualified") is True for row in enabled_rows),
    }


def build_report(cases: Iterable[dict[str, Any]], *, base_url: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = list(cases)
    return {
        "schema": KREA2_QUALIFICATION_REPORT_SCHEMA,
        "created_at": utc_now_iso(),
        "base_url": base_url,
        "contract": "A case counts as physically qualified only after a real Comfy execution produces output and a human reviewer passes every required visual check.",
        "summary": qualification_summary(rows),
        "metadata": metadata or {},
        "cases": rows,
    }


def recommended_case_template() -> list[dict[str, Any]]:
    """Return a conservative matrix template with blank local assets.

    The template intentionally does not guess model/LoRA filenames. Public Neo
    installs differ, and baked models cannot be detected safely from names.
    """

    base = {
        "enabled": False,
        "family": "krea2",
        "mode": "img2img",
        "model": "",
        "text_encoder": "",
        "vae": "",
        "source_image": "",
        "source_image_2": "",
        "source_image_3": "",
        "mask_image": "",
        "prompt": "Preserve the subject identity and make one obvious controlled edit.",
        "negative_prompt": "",
        "width": 1024,
        "height": 1024,
        "steps": 20,
        "cfg": 3.0,
        "denoise": 1.0,
        "seed": 1701,
        "sampler": "euler",
        "scheduler": "simple",
    }
    cases: list[dict[str, Any]] = []

    def add(case_id: str, *, loader: str, engine: str, weight_source: str = "none", kv_cache: bool = False, refs: int = 1, mode: str = "img2img") -> None:
        row = {**base, "case_id": case_id, "loader": loader, "engine": engine, "weight_source": weight_source, "kv_cache": kv_cache, "mode": mode}
        if engine != KREA2_EDIT_ENGINE_NATIVE:
            row["edit_lora"] = ""
            row["lora_strength"] = 1.0
        if refs < 2:
            row["source_image_2"] = ""
        if refs < 3:
            row["source_image_3"] = ""
        if mode == "inpaint":
            row["mask_image"] = ""
        if mode == "outpaint":
            row["outpaint"] = {"left": 128, "right": 128, "top": 128, "bottom": 128, "feather": 16}
        cases.append(row)

    for loader, suffix in (("diffusion_model", "safetensors"), ("gguf", "gguf")):
        add(f"native_{suffix}_img2img", loader=loader, engine=KREA2_EDIT_ENGINE_NATIVE)
        add(f"identity_separate_{suffix}_1ref", loader=loader, engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, refs=1)
        add(f"identity_separate_{suffix}_2ref", loader=loader, engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, refs=2)
        add(f"identity_baked_{suffix}_1ref", loader=loader, engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, refs=1)
        add(f"identity_baked_{suffix}_2ref", loader=loader, engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, refs=2)
        add(f"ostris_separate_{suffix}_kv_off_1ref", loader=loader, engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, kv_cache=False, refs=1)
        add(f"ostris_separate_{suffix}_kv_off_2ref", loader=loader, engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, kv_cache=False, refs=2)
        add(f"ostris_separate_{suffix}_kv_off_3ref", loader=loader, engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, kv_cache=False, refs=3)
        add(f"ostris_separate_{suffix}_kv_on", loader=loader, engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, kv_cache=True, refs=1)
        add(f"ostris_baked_{suffix}_1ref", loader=loader, engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, refs=1)

    add("identity_baked_safetensors_inpaint", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, mode="inpaint")
    add("identity_baked_safetensors_outpaint", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, mode="outpaint")
    add("ostris_separate_safetensors_inpaint", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, mode="inpaint")
    add("ostris_separate_safetensors_outpaint", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, mode="outpaint")
    add("identity_baked_safetensors_global_lora", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, refs=1)
    cases[-1]["global_loras"] = [{"name": "", "strength": 0.7, "target": "both", "apply_to": "global"}]
    add("ostris_separate_safetensors_global_lora", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_OSTRIS, weight_source=KREA2_EDIT_WEIGHT_SOURCE_SEPARATE_LORA, refs=1)
    cases[-1]["global_loras"] = [{"name": "", "strength": 0.7, "target": "both", "apply_to": "global"}]
    add("identity_baked_safetensors_repeat", loader="diffusion_model", engine=KREA2_EDIT_ENGINE_IDENTITY, weight_source=KREA2_EDIT_WEIGHT_SOURCE_BAKED_MODEL, refs=1)
    cases[-1]["repeat_count"] = 2
    cases[-1]["manual_checks"] = ["replay_preserves_engine_and_weight_source", "switching_to_a_different_model_does_not_carry_baked_assumption"]
    return cases


def qualification_config_template() -> dict[str, Any]:
    return {
        "schema": KREA2_QUALIFICATION_SCHEMA,
        "base_url": "http://127.0.0.1:8188",
        "notes": [
            "Enable only cases for which you have the exact required assets.",
            "Use the real baked checkpoint in baked cases; do not point baked cases at a base model.",
            "Enable KV Cache only for a LoRA/model whose author says it was trained/exported with KV Cache support.",
            "Source values may be local file paths. The qualification runner uploads them to Comfy input before queueing.",
        ],
        "cases": recommended_case_template(),
    }
