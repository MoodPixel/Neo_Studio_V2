from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.scene_packet_builder import build_scene_packet_payload, scene_packet_builder_state_payload
from neo_app.roleplay.scope_build_ui import ensure_scope_build_schema, linked_records_for_scope, preview_scope_build_payload
from neo_app.roleplay.sqlite_store import _connect, _json
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT

PHASE187_SCHEMA_ID = "neo.roleplay.runtime_presets.v1"
PHASE187_VERSION = "1.0.0-phase18.7-runtime-presets-auto-scene-packet"
PHASE187_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "runtime_presets_contract.json"
PHASE187_STATE_PATH = ROLEPLAY_DATA_ROOT / "runtime_presets_state.json"
PHASE187_JOB_STATE_PATH = ROLEPLAY_DATA_ROOT / "runtime_scene_packet_jobs_state.json"
_RUNTIME_PACKET_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="neo-runtime-packet")
_RUNTIME_PACKET_JOBS: dict[str, dict[str, Any]] = {}
_RUNTIME_PACKET_JOBS_LOCK = threading.Lock()

_KIND_TO_PACKET_FIELD = {
    "universe": "universe_ids",
    "world": "world_ids",
    "region": "region_ids",
    "city": "city_ids",
    "location": "location_ids",
    "character": "character_ids",
    "relationship": "relationship_ids",
    "organization": "organization_ids",
    "artifact": "artifact_ids",
    "ritual": "ritual_ids",
    "cycle": "cycle_ids",
    "creature": "creature_ids",
    "legend": "legend_ids",
    "scenario": "scenario_ids",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_runtime_presets_schema() -> dict[str, Any]:
    ensure_scope_build_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_runtime_presets (
                preset_id TEXT PRIMARY KEY,
                build_run_id TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                scope_type TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ready',
                runtime_bundle_id TEXT NOT NULL DEFAULT '',
                latest_scene_packet_id TEXT NOT NULL DEFAULT '',
                selected_scenario_id TEXT NOT NULL DEFAULT '',
                selected_player_character_ids TEXT NOT NULL DEFAULT '',
                record_count INTEGER NOT NULL DEFAULT 0,
                records_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_runtime_presets_scope ON rp_runtime_presets(scope_type, scope_id, updated_at);
            """
        )
        conn.commit()
    return {"status": "ready", "table": "rp_runtime_presets"}


def _selected_scope_card(preview: dict[str, Any]) -> dict[str, Any]:
    linked = preview.get("linked_records") or {}
    scope_id = _clean(preview.get("scope_id") or linked.get("scope_id"))
    records = linked.get("records") or []
    return next((row for row in records if _clean(row.get("record_id")) == scope_id), None) or (records[0] if records else {})


def _records_by_kind_from_preview(preview: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    linked = preview.get("linked_records") or {}
    for row in linked.get("records") or []:
        kind = _clean(row.get("kind") or "record").lower()
        grouped[kind].append(row)
    return {kind: items for kind, items in sorted(grouped.items())}


def _preset_from_run(row: dict[str, Any]) -> dict[str, Any]:
    preview = _read_json(row.get("preview_json"), {}) or {}
    summary = _read_json(row.get("summary_json"), {}) or {}
    selected = _selected_scope_card(preview)
    records_by_kind = _records_by_kind_from_preview(preview)
    linked = preview.get("linked_records") or {}
    scope_type = _clean(row.get("scope_type") or preview.get("scope_type") or linked.get("scope_type"))
    scope_id = _clean(row.get("scope_id") or preview.get("scope_id") or linked.get("scope_id"))
    title = _clean(selected.get("title") or scope_id)
    scope_label = scope_type.replace("_", " ").title() if scope_type else "Scope"
    runtime_bundle_id = _clean(row.get("runtime_bundle_id") or summary.get("runtime_bundle_id"))
    status = "ready" if runtime_bundle_id else _clean(row.get("status") or "built")
    preset_id = _clean(row.get("build_run_id")) or f"preset:{scope_type}:{scope_id}"
    scenarios = records_by_kind.get("scenario") or []
    characters = records_by_kind.get("character") or []
    return {
        "preset_id": preset_id,
        "build_run_id": _clean(row.get("build_run_id")),
        "label": f"{scope_label} — {title}" if title else preset_id,
        "title": title,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "status": status,
        "runtime_bundle_id": runtime_bundle_id,
        "compile_run_id": _clean(row.get("compile_run_id") or summary.get("compile_run_id")),
        "linked_record_count": int(row.get("linked_record_count") or linked.get("linked_record_count") or 0),
        "compiled_count": int(row.get("compiled_count") or summary.get("compiled_count") or 0),
        "fragment_count": int(row.get("fragment_count") or summary.get("fragment_count") or 0),
        "updated_at": _clean(row.get("finished_at") or row.get("started_at")),
        "kind_counts": linked.get("kind_counts") or {kind: len(items) for kind, items in records_by_kind.items()},
        "records_by_kind": records_by_kind,
        "scenario_options": [{"record_id": item.get("record_id"), "title": item.get("title") or item.get("record_id")} for item in scenarios],
        "character_options": [{"record_id": item.get("record_id"), "title": item.get("title") or item.get("record_id")} for item in characters],
        "preview": preview,
        "summary": summary,
    }


def _run_rows(limit: int = 50) -> list[dict[str, Any]]:
    ensure_runtime_presets_schema()
    with _connect() as conn:
        try:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM rp_scope_build_runs ORDER BY COALESCE(finished_at, started_at) DESC, started_at DESC LIMIT ?",
                (max(1, min(int(limit or 50), 200)),),
            ).fetchall()]
        except sqlite3.Error:
            rows = []
    return rows


def list_runtime_presets(limit: int = 50) -> list[dict[str, Any]]:
    presets = [_preset_from_run(row) for row in _run_rows(limit=limit)]
    # De-duplicate by build run while preserving newest-first order.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for preset in presets:
        pid = _clean(preset.get("preset_id"))
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(preset)
    return out


def get_runtime_preset(preset_id: str = "", *, scope_type: str = "", scope_id: str = "") -> dict[str, Any] | None:
    preset_id = _clean(preset_id)
    scope_type = _clean(scope_type)
    scope_id = _clean(scope_id)
    for preset in list_runtime_presets(limit=200):
        if preset_id and preset.get("preset_id") == preset_id:
            return preset
        if scope_type and scope_id and preset.get("scope_type") == scope_type and preset.get("scope_id") == scope_id:
            return preset
    return None


def _ids_for_kind(preset: dict[str, Any], kind: str) -> list[str]:
    return [_clean(row.get("record_id")) for row in (preset.get("records_by_kind") or {}).get(kind, []) if _clean(row.get("record_id"))]


def _auto_query(preset: dict[str, Any], scenario_id: str = "", player_ids: list[str] | None = None) -> str:
    parts: list[str] = []
    if scenario_id:
        scenario = next((row for row in (preset.get("records_by_kind") or {}).get("scenario", []) if row.get("record_id") == scenario_id), None)
        if scenario:
            parts.append(_clean(scenario.get("title")))
            parts.append(_clean(scenario.get("summary")))
    for kind in ("character", "relationship", "artifact", "ritual", "legend", "location"):
        for row in (preset.get("records_by_kind") or {}).get(kind, [])[:8]:
            parts.append(_clean(row.get("title")))
    if player_ids:
        parts.extend(player_ids)
    return " ".join([part for part in parts if part])[:900] or _clean(preset.get("title") or preset.get("label") or "scene context")


def scene_packet_payload_from_preset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    preset = get_runtime_preset(_clean(payload.get("preset_id")), scope_type=_clean(payload.get("scope_type")), scope_id=_clean(payload.get("scope_id")))
    if not preset:
        # Allow a direct scope build preview fallback so Runtime can be used after scope selection even before a run was persisted.
        scope_type = _clean(payload.get("scope_type") or "world")
        scope_id = _clean(payload.get("scope_id"))
        if scope_id:
            preview = preview_scope_build_payload({"scope_type": scope_type, "scope_id": scope_id, "graph_depth": payload.get("graph_depth", 2)})
            preset = _preset_from_run({
                "build_run_id": f"preview:{scope_type}:{scope_id}",
                "scope_type": scope_type,
                "scope_id": scope_id,
                "status": "preview",
                "started_at": _now(),
                "linked_record_count": (preview.get("linked_records") or {}).get("linked_record_count") or 0,
                "runtime_bundle_id": "",
                "summary_json": "{}",
                "preview_json": json.dumps(preview),
            })
    if not preset:
        return {"status": "error", "error": "Runtime preset not found. Build Memory + Runtime from the Compile tab first."}

    scenario_id = _clean(payload.get("scenario_id") or payload.get("selected_scenario_id"))
    if not scenario_id:
        scenario_ids = _ids_for_kind(preset, "scenario")
        scenario_id = scenario_ids[0] if scenario_ids else ""
    player_ids = []
    raw_player = payload.get("player_character_ids") or payload.get("selected_player_character_ids") or []
    if isinstance(raw_player, str):
        player_ids = [_clean(item) for item in raw_player.replace("\n", ",").split(",") if _clean(item)]
    elif isinstance(raw_player, list):
        player_ids = [_clean(item) for item in raw_player if _clean(item)]
    title = _clean(payload.get("title") or "")
    if not title:
        scenario_label = scenario_id
        for row in (preset.get("records_by_kind") or {}).get("scenario", []):
            if row.get("record_id") == scenario_id:
                scenario_label = row.get("title") or scenario_id
                break
        title = f"{scenario_label or preset.get('title') or 'Scene'} — Runtime Packet"

    packet_payload: dict[str, Any] = {
        "title": title,
        "scene_id": _clean(payload.get("scene_id") or scenario_id or preset.get("scope_id") or "default"),
        "scope_id": _clean(payload.get("packet_scope_id") or preset.get("scope_id") or ""),
        "sandbox_id": _clean(payload.get("sandbox_id") or (preset.get("preview") or {}).get("sandbox_id") or preset.get("scope_id") or ""),
        "query": _clean(payload.get("query") or _auto_query(preset, scenario_id, player_ids)),
        "mode": _clean(payload.get("mode") or "hybrid"),
        "limit": int(payload.get("limit") or 8),
        "candidate_limit": int(payload.get("candidate_limit") or 24),
        "rerank_candidate_limit": int(payload.get("rerank_candidate_limit") or 8),
        "rerank": payload.get("rerank", True) is not False,
        "run_retrieval": bool(payload.get("run_retrieval", False)),
        "rebuild_search": bool(payload.get("rebuild_search", False)),
        "player_character_ids": ",".join(player_ids),
        "tone": _clean(payload.get("tone") or "canon-aware, emotionally grounded, continuity-safe"),
        "player_control": _clean(payload.get("player_control") or "User controls the player character(s). Do not write their dialogue, thoughts, or actions."),
        "npc_control": _clean(payload.get("npc_control") or "Model controls narrator and non-player characters only."),
        "forbidden_behavior": _clean(payload.get("forbidden_behavior") or "Do not contradict canon guards, reveal staged secrets early, or override user POV."),
        "runtime_preset_id": preset.get("preset_id"),
        "runtime_bundle_id": preset.get("runtime_bundle_id"),
    }
    for kind, field in _KIND_TO_PACKET_FIELD.items():
        ids = _ids_for_kind(preset, kind)
        if kind == "scenario" and scenario_id:
            ids = [scenario_id] + [item for item in ids if item != scenario_id]
        packet_payload[field] = ",".join(ids)
    return {"status": "ready", "preset": preset, "packet_payload": packet_payload}


def build_scene_packet_from_runtime_preset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    prepared = scene_packet_payload_from_preset(payload or {})
    if prepared.get("status") != "ready":
        return prepared
    result = build_scene_packet_payload(prepared.get("packet_payload") or {})
    scene_packet = result.get("scene_packet") or {}
    preset = prepared.get("preset") or {}
    # Store light preset row and latest packet binding for future browsing.
    now = _now()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO rp_runtime_presets(preset_id, build_run_id, label, scope_type, scope_id, status, runtime_bundle_id, latest_scene_packet_id, selected_scenario_id, selected_player_character_ids, record_count, records_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(preset_id) DO UPDATE SET
                    label=excluded.label,
                    status=excluded.status,
                    runtime_bundle_id=excluded.runtime_bundle_id,
                    latest_scene_packet_id=excluded.latest_scene_packet_id,
                    selected_scenario_id=excluded.selected_scenario_id,
                    selected_player_character_ids=excluded.selected_player_character_ids,
                    record_count=excluded.record_count,
                    records_json=excluded.records_json,
                    updated_at=excluded.updated_at
                """,
                (
                    preset.get("preset_id"), preset.get("build_run_id") or preset.get("preset_id"), preset.get("label"), preset.get("scope_type"), preset.get("scope_id"), "ready",
                    preset.get("runtime_bundle_id") or "", scene_packet.get("scene_packet_id") or "", (prepared.get("packet_payload") or {}).get("scenario_ids", "").split(",")[0], (prepared.get("packet_payload") or {}).get("player_character_ids", ""),
                    int(preset.get("linked_record_count") or 0), _json(preset.get("records_by_kind") or {}), now, now,
                ),
            )
            conn.commit()
    except sqlite3.Error:
        pass
    return {
        "schema_id": "neo.roleplay.runtime_presets.scene_packet.response.v1",
        "status": result.get("status") or "built",
        "preset": preset,
        "prepared": prepared,
        "scene_packet": scene_packet,
        "scene_packet_builder": result.get("scene_packet_builder") or scene_packet_builder_state_payload(write_report=True),
        "runtime_presets": runtime_presets_state_payload(write_report=True),
    }



def _runtime_packet_job_public(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"status": "missing", "error": "Scene packet job not found."}
    public = {k: v for k, v in job.items() if k not in {"payload"}}
    return public


def _snapshot_runtime_packet_jobs() -> None:
    try:
        with _RUNTIME_PACKET_JOBS_LOCK:
            jobs = [_runtime_packet_job_public(job) for job in sorted(_RUNTIME_PACKET_JOBS.values(), key=lambda item: item.get("started_at") or "", reverse=True)[:25]]
        _write_json(PHASE187_JOB_STATE_PATH, {
            "schema_id": "neo.roleplay.runtime_presets.scene_packet_jobs.v1",
            "version": PHASE187_VERSION,
            "status": "active",
            "jobs": jobs,
            "updated_at": _now(),
        })
    except Exception:
        pass


def _run_scene_packet_build_job(job_id: str, payload: dict[str, Any]) -> None:
    with _RUNTIME_PACKET_JOBS_LOCK:
        job = _RUNTIME_PACKET_JOBS.get(job_id)
        if job:
            job.update({"status": "running", "stage": "build_scene_packet", "message": "Runtime retrieval/rerank job is running in the background.", "updated_at": _now()})
    _snapshot_runtime_packet_jobs()
    started = datetime.now(timezone.utc)
    try:
        result = build_scene_packet_from_runtime_preset(payload)
        finished = datetime.now(timezone.utc)
        with _RUNTIME_PACKET_JOBS_LOCK:
            job = _RUNTIME_PACKET_JOBS.get(job_id)
            if job is not None:
                packet = (result.get("scene_packet") or {}) if isinstance(result, dict) else {}
                job.update({
                    "status": "built" if (result or {}).get("status") != "error" else "failed",
                    "stage": "done" if (result or {}).get("status") != "error" else "failed",
                    "message": f"Scene packet {packet.get('packet_id') or packet.get('scene_packet_id') or 'saved'} is ready." if (result or {}).get("status") != "error" else str((result or {}).get("error") or "Build failed"),
                    "result": result,
                    "finished_at": finished.isoformat(),
                    "updated_at": finished.isoformat(),
                    "elapsed_ms": round((finished - started).total_seconds() * 1000, 2),
                })
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        with _RUNTIME_PACKET_JOBS_LOCK:
            job = _RUNTIME_PACKET_JOBS.get(job_id)
            if job is not None:
                job.update({
                    "status": "failed",
                    "stage": "failed",
                    "message": str(exc)[:1000],
                    "error": str(exc)[:2000],
                    "traceback": traceback.format_exc()[-5000:],
                    "finished_at": finished.isoformat(),
                    "updated_at": finished.isoformat(),
                    "elapsed_ms": round((finished - started).total_seconds() * 1000, 2),
                })
    _snapshot_runtime_packet_jobs()


def start_scene_packet_build_job(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    job_id = f"runtime-packet-job:{uuid.uuid4().hex[:12]}"
    now = _now()
    job = {
        "schema_id": "neo.roleplay.runtime_presets.scene_packet_job.v1",
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": "Scene packet job queued. Heavy retrieval/rerank will continue without a browser request timeout.",
        "payload": data,
        "preset_id": data.get("preset_id") or "",
        "run_retrieval": bool(data.get("run_retrieval", False)),
        "rerank": data.get("rerank", True) is not False,
        "mode": data.get("mode") or "hybrid",
        "started_at": now,
        "updated_at": now,
    }
    with _RUNTIME_PACKET_JOBS_LOCK:
        _RUNTIME_PACKET_JOBS[job_id] = job
        # Keep memory bounded inside the desktop app process.
        if len(_RUNTIME_PACKET_JOBS) > 50:
            for old_id, _ in sorted(_RUNTIME_PACKET_JOBS.items(), key=lambda pair: pair[1].get("started_at") or "")[:10]:
                _RUNTIME_PACKET_JOBS.pop(old_id, None)
    _snapshot_runtime_packet_jobs()
    _RUNTIME_PACKET_JOB_EXECUTOR.submit(_run_scene_packet_build_job, job_id, data)
    return {
        "schema_id": "neo.roleplay.runtime_presets.scene_packet_job_start.v1",
        "status": "queued",
        "job": _runtime_packet_job_public(job),
        "runtime_presets": runtime_presets_state_payload(write_report=True),
    }


def scene_packet_build_job_status(job_id: str) -> dict[str, Any]:
    with _RUNTIME_PACKET_JOBS_LOCK:
        job = dict(_RUNTIME_PACKET_JOBS.get(job_id) or {})
    if not job:
        return {"schema_id": "neo.roleplay.runtime_presets.scene_packet_job_status.v1", "status": "missing", "error": "Scene packet job not found.", "job_id": job_id}
    return {"schema_id": "neo.roleplay.runtime_presets.scene_packet_job_status.v1", "status": job.get("status") or "unknown", "job": _runtime_packet_job_public(job)}

def runtime_presets_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_runtime_presets_schema()
    presets = list_runtime_presets(limit=80)
    latest = presets[0] if presets else None
    payload = {
        "schema_id": "neo.roleplay.runtime_presets.state.v1",
        "version": PHASE187_VERSION,
        "status": "active",
        "ready": bool(presets),
        "preset_count": len(presets),
        "presets": presets,
        "latest_preset": latest,
        "ui": {
            "simple_flow": ["select compiled scope", "choose scenario", "choose player character", "build scene packet", "send to scene chat"],
            "advanced_controls": "manual retrieval and manual packet ID fields remain available in collapsible debug panels",
        },
    }
    if write_report:
        _write_json(PHASE187_STATE_PATH, payload)
    return payload


def runtime_presets_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE187_SCHEMA_ID,
        "version": PHASE187_VERSION,
        "phase": "Phase 18.7 — Runtime Presets + Auto Scene Packet From Compiled Scope",
        "status": "implemented",
        "purpose": "Make Studio > Runtime use compiled scope builds as reusable presets and auto-populate Scene Packet Builder from linked records.",
        "endpoints": {
            "contract": "GET /api/roleplay/runtime-presets/contract",
            "state": "GET /api/roleplay/runtime-presets/state",
            "ensure_schema": "POST /api/roleplay/runtime-presets/ensure-schema",
            "prepare_packet": "POST /api/roleplay/runtime-presets/prepare-scene-packet",
            "build_packet": "POST /api/roleplay/runtime-presets/build-scene-packet",
            "start_build_packet_job": "POST /api/roleplay/runtime-presets/build-scene-packet-job",
            "build_packet_job_status": "GET /api/roleplay/runtime-presets/build-scene-packet-job/{job_id}",
        },
        "simple_runtime_flow": ["Select compiled scope", "Pick scenario", "Pick player character", "Build Scene Packet", "Open Scene Chat"],
        "manual_fields_policy": "Manual packet fields and retrieval diagnostics are preserved under Advanced, not shown as the default path.",
        "heavy_retrieval_policy": "When retrieval/rerank is enabled, the UI should use the background job endpoints instead of a single blocking HTTP request.",
    }
    if write_report:
        _write_json(PHASE187_CONTRACT_PATH, payload)
    return payload
