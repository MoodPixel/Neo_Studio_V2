from __future__ import annotations

"""Read-only Scene Director inspector contract for SD-28.7."""

from copy import deepcopy
from typing import Any

from .execution_strategy import ENGINE_CLASSIC_V054, ENGINE_LIGHTWEIGHT_REGIONAL
from .release_lock import RELEASE_LOCK_PHASE

INSPECTOR_SCHEMA = "neo.image.scene_director.inspector.v2"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _tone(state: str) -> str:
    state = str(state or "").lower()
    if state in {"available", "active", "locked", "proven", "applied", "ok"}:
        return "ok"
    if state in {"experimental_available", "experimental", "pending", "preflight", "gated_safe", "not_requested"}:
        return "warning"
    if state in {"blocked", "unsupported", "provider_gated", "planned_gated", "missing"}:
        return "blocker"
    return "neutral"


def _chip(chip_id: str, label: str, state: str, detail: str = "") -> dict[str, str]:
    return {"id": chip_id, "label": label, "state": state, "tone": _tone(state), "detail": detail}


def _compatibility_rows(patch: dict[str, Any]) -> list[dict[str, Any]]:
    contract = _dict(patch.get("scene_director_regional_lora_contract"))
    compatibility = contract.get("binding_compatibility")
    rows: list[dict[str, Any]] = []
    if isinstance(compatibility, dict):
        for bucket in ("accepted", "rejected", "unknown"):
            for item in _list(compatibility.get(bucket)):
                if not isinstance(item, dict):
                    continue
                details = (
                    _dict(item.get("compatibility"))
                    or _dict(item.get("krea2_compatibility"))
                    or _dict(item.get("flux2_klein_compatibility"))
                    or _dict(item.get("z_image_compatibility"))
                )
                rows.append({
                    "state": bucket,
                    "region_id": item.get("region_id"),
                    "lora_name": item.get("lora_name") or item.get("name"),
                    "compatible": details.get("compatible"),
                    "compatibility_state": details.get("state"),
                    "declared_family": details.get("declared_family") or details.get("declared_variant"),
                    "target_family": details.get("target_family") or details.get("target_variant"),
                    "reason": details.get("reason"),
                })
        if not rows and compatibility.get("schema"):
            rows.append({
                "state": "summary",
                "accepted": compatibility.get("accepted_count", 0),
                "rejected": compatibility.get("rejected_count", 0),
                "unknown": compatibility.get("unknown_count", 0),
                "schema": compatibility.get("schema"),
            })
    elif isinstance(compatibility, list):
        rows.extend(deepcopy([row for row in compatibility if isinstance(row, dict)]))
    return rows


def _proof_rows(patch: dict[str, Any]) -> list[dict[str, Any]]:
    proof = _dict(patch.get("scene_director_lightweight_runtime_proof"))
    keys = (
        "single_sampler_preserved",
        "sampler_parameters_preserved",
        "latent_input_unchanged",
        "global_model_mutation",
        "regional_lora_route_count",
        "regional_lora_nodes_added",
        "regional_lora_compile_status",
        "runtime_gpu_proven",
        "runtime_status",
        "contract_ok",
    )
    return [{"proof": key, "value": deepcopy(proof.get(key))} for key in keys if key in proof]


def build_scene_director_inspector(
    *,
    validation: dict[str, Any] | None = None,
    workflow_patch: dict[str, Any] | None = None,
    release_lock: dict[str, Any] | None = None,
    preflight: bool = False,
) -> dict[str, Any]:
    validation = _dict(validation)
    patch = _dict(workflow_patch)
    lock = _dict(release_lock)
    strategy = _dict(patch.get("scene_director_execution_strategy")) or _dict(validation.get("execution_strategy"))
    route = _dict(patch.get("route")) or _dict(validation.get("route")) or _dict(strategy.get("route"))
    engine = str(strategy.get("engine") or patch.get("scene_director_engine") or lock.get("engine") or "unknown")
    route_state = str(patch.get("route_state") or validation.get("route_state") or strategy.get("status") or "unknown")
    family = str(route.get("family") or strategy.get("family") or "unknown")
    loader = str(route.get("loader") or strategy.get("loader") or "unknown")
    mode = str(route.get("mode") or route.get("workflow_mode") or strategy.get("mode") or "generate")
    regional_prompt = _dict(patch.get("scene_director_lightweight_regional_prompt"))
    regional_lora = _dict(patch.get("scene_director_regional_lora_contract")) or _dict(strategy.get("regional_lora"))
    lora_applied = bool(patch.get("scene_director_regional_lora_applied"))
    proof = _dict(patch.get("scene_director_lightweight_runtime_proof"))
    gpu_proven = bool(patch.get("scene_director_regional_lora_runtime_gpu_proven") or proof.get("runtime_gpu_proven") or lock.get("gpu_proven"))
    lora_requested = bool(regional_lora.get("route_count") or lora_applied)

    lock_status = str(lock.get("status") or ("preflight" if preflight else "unknown"))
    prompt_state = "active" if regional_prompt.get("status") == "applied" else ("available" if _dict(strategy.get("regional_prompt")).get("supported") else "not_requested")
    if engine == ENGINE_CLASSIC_V054:
        prompt_state = "active" if patch.get("applied") else "available"
    lora_runtime_status = str(patch.get("scene_director_regional_lora_status") or regional_lora.get("status") or "")
    lora_runtime_status_lc = lora_runtime_status.lower()
    lora_failed_closed = bool(
        lora_requested
        and not lora_applied
        and any(token in lora_runtime_status_lc for token in ("gated", "blocked", "missing", "unsupported", "incompatible", "rejected"))
    )
    lora_state = (
        "applied" if lora_applied
        else "blocked" if lora_failed_closed
        else "available" if _dict(strategy.get("regional_lora")).get("supported")
        else "not_requested"
    )
    proof_state = "proven" if gpu_proven else ("pending" if lora_applied else "blocked" if lora_failed_closed else "not_requested")

    blockers = [str(item.get("detail") or item.get("message") or item) for item in _list(lock.get("blockers"))]
    blockers.extend(str(item.get("message") or item) for item in _list(validation.get("errors")))
    warnings = [str(item.get("detail") or item.get("message") or item) for item in _list(lock.get("warnings"))]
    warnings.extend(str(item.get("message") or item) for item in _list(validation.get("warnings")))
    if lora_failed_closed:
        warnings.append(f"Regional LoRA failed closed for this run: {lora_runtime_status or 'runtime adapter unavailable'}.")

    status_chips = [
        _chip("route", "Route", route_state, f"{family} · {loader} · {mode}"),
        _chip("engine", "Engine", "active" if engine in {ENGINE_CLASSIC_V054, ENGINE_LIGHTWEIGHT_REGIONAL} else "blocked", engine),
        _chip("regional_prompt", "Regional Prompt", prompt_state, str(_dict(strategy.get("regional_prompt")).get("mode") or regional_prompt.get("status") or "")),
        _chip("regional_lora", "Regional LoRA", lora_state, str(regional_lora.get("mode") or regional_lora.get("status") or "")),
        _chip("gpu_proof", "GPU Proof", proof_state, "Per-run runtime proof; compile-time support does not imply spatial isolation proof."),
        _chip("release_lock", "Release Lock", lock_status, str(lock.get("reason") or "SD-28.7 preflight")),
    ]

    route_rows = [{
        "backend": route.get("backend"),
        "family": family,
        "loader": loader,
        "mode": mode,
        "route_state": route_state,
        "engine": engine,
        "fallback_policy": patch.get("fallback_policy") or strategy.get("fallback_policy"),
    }]
    lock_rows = deepcopy(_list(lock.get("checks")))
    diagnostics = [{"level": "blocker", "message": item} for item in blockers] + [{"level": "warning", "message": item} for item in warnings]

    counts = {
        "regions": int(patch.get("regions") or validation.get("regional_count") or 0),
        "subjects": int(patch.get("subject_count") or validation.get("subject_count") or 0),
        "prompt_lanes": int(proof.get("regional_prompt_lane_count") or 0),
        "regional_loras": int(proof.get("regional_lora_route_count") or regional_lora.get("route_count") or 0),
        "samplers": int(proof.get("sampler_count_after") or proof.get("sampler_count_before") or 0),
        "blockers": len(blockers),
        "warnings": len(warnings),
    }
    tables = {
        "route": route_rows,
        "regional_lora": _compatibility_rows(patch),
        "runtime_proof": _proof_rows(patch),
        "release_lock": lock_rows,
        "diagnostics": diagnostics,
    }
    panels = [
        {"panel_id": "overview", "title": "Overview", "default_open": True, "row_count": len(counts)},
        {"panel_id": "route", "title": "Route & Engine", "table_id": "route", "default_open": True, "row_count": len(route_rows)},
        {"panel_id": "regional_lora", "title": "Regional LoRA Compatibility", "table_id": "regional_lora", "default_open": bool(tables["regional_lora"]), "row_count": len(tables["regional_lora"])},
        {"panel_id": "runtime_proof", "title": "Runtime Proof", "table_id": "runtime_proof", "default_open": lora_requested, "row_count": len(tables["runtime_proof"])},
        {"panel_id": "release_lock", "title": "Release Lock", "table_id": "release_lock", "default_open": bool(blockers), "row_count": len(lock_rows)},
        {"panel_id": "diagnostics", "title": "Diagnostics", "table_id": "diagnostics", "default_open": bool(diagnostics), "row_count": len(diagnostics)},
    ]
    return {
        "schema": INSPECTOR_SCHEMA,
        "phase": RELEASE_LOCK_PHASE,
        "runtime_mode": "preflight inspector" if preflight else "compiled graph inspector",
        "summary": f"{family} / {loader} / {mode} · {engine} · release {lock_status} · GPU proof {proof_state}",
        "status_chips": status_chips,
        "counts": counts,
        "panels": panels,
        "tables": tables,
        "blockers": blockers,
        "warnings": warnings,
        "release_lock": deepcopy(lock),
        "gpu_proof": {"required": lora_requested, "proven": gpu_proven, "status": proof_state},
        "route": deepcopy(route),
        "engine": engine,
    }


def build_preflight_inspector(*, block: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(block.get("metadata"))
    validation = {
        "route": deepcopy(_dict(strategy.get("route"))),
        "route_state": metadata.get("route_state") or strategy.get("status"),
        "regional_count": metadata.get("regional_count") or 0,
        "subject_count": metadata.get("subject_count") or 0,
        "execution_strategy": deepcopy(strategy),
        "warnings": [],
        "errors": [],
    }
    lock = {
        "phase": RELEASE_LOCK_PHASE,
        "status": "preflight",
        "locked": False,
        "allow_output": True,
        "engine": strategy.get("engine"),
        "route": deepcopy(_dict(strategy.get("route"))),
        "checks": [],
        "blockers": [],
        "warnings": [],
        "gpu_proof_required": bool(_dict(strategy.get("regional_lora")).get("supported")),
        "gpu_proven": False,
        "reason": "Release lock is evaluated after graph compilation.",
    }
    return build_scene_director_inspector(validation=validation, workflow_patch={}, release_lock=lock, preflight=True)


__all__ = ["INSPECTOR_SCHEMA", "build_scene_director_inspector", "build_preflight_inspector"]
