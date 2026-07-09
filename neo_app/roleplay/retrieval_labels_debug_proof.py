from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, ensure_roleplay_foundation

SCHEMA_ID: Final[str] = "neo.roleplay.phase17_5d.retrieval_labels_debug_proof.v1"
VERSION: Final[str] = "17.5D.0-retrieval-labels-debug-proof"
CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "retrieval_labels_debug_proof_contract.json"
STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "retrieval_labels_debug_proof_state.json"
SYSTEM_MEMORY_DOC: Final[Path] = Path("neo_system_records/05_MEMORY_SYSTEM/ROLEPLAY_PHASE_17_5D_RETRIEVAL_LABELS_DEBUG_PROOF.md")
SYSTEM_SURFACE_DOC: Final[Path] = Path("neo_system_records/06_SURFACES/roleplay/PHASE_17_5D_RETRIEVAL_LABELS_DEBUG_PROOF.md")

KIND_LABELS: Final[dict[str, dict[str, str]]] = {
    "universe": {"label": "Universe law", "category": "universe_context", "role": "cosmology / global canon"},
    "world": {"label": "World lore", "category": "world_context", "role": "setting canon"},
    "region": {"label": "Region pressure", "category": "region_context", "role": "kingdom / border / culture context"},
    "kingdom": {"label": "Region pressure", "category": "region_context", "role": "kingdom / power context"},
    "city": {"label": "City context", "category": "city_context", "role": "settlement / district context"},
    "settlement": {"label": "City context", "category": "city_context", "role": "settlement context"},
    "location": {"label": "Location context", "category": "location_context", "role": "scene-grounding / access / hazards"},
    "character": {"label": "Character profile", "category": "character_context", "role": "persona / goals / wounds / speech"},
    "relationship": {"label": "Relationship state", "category": "relationship_context", "role": "bond dynamic / conflict / repair"},
    "organization": {"label": "Organization pressure", "category": "organization_context", "role": "faction / agenda / hierarchy"},
    "artifact": {"label": "Artifact rule", "category": "artifact_context", "role": "object power / limits / triggers"},
    "ritual": {"label": "Ritual rule", "category": "ritual_context", "role": "practice / taboo / cost"},
    "practice": {"label": "Ritual rule", "category": "ritual_context", "role": "practice / taboo / cost"},
    "cycle": {"label": "System rule", "category": "system_context", "role": "recurrence / phase / structural rule"},
    "system": {"label": "System rule", "category": "system_context", "role": "recurrence / phase / structural rule"},
    "creature": {"label": "Creature lore", "category": "creature_context", "role": "behavior / habitat / danger logic"},
    "legend": {"label": "Legend reveal", "category": "legend_context", "role": "myth / hidden truth / reveal gate"},
    "scenario": {"label": "Scenario pressure", "category": "scenario_context", "role": "stakes / constraints / scene clock"},
    "source_document": {"label": "Source memory", "category": "retrieved_memory", "role": "novel/source document chunk"},
    "canon_breakdown": {"label": "Approved canon", "category": "canon_guards", "role": "approved source-derived canon"},
}

MEMORY_TYPE_LABELS: Final[dict[str, dict[str, str]]] = {
    "canon_guard": {"label": "Canon guard", "category": "canon_guards", "role": "must not contradict"},
    "reveal_gate": {"label": "Reveal gate", "category": "reveal_gates", "role": "staged truth / spoiler guard"},
    "callback_anchor": {"label": "Callback anchor", "category": "callback_anchors", "role": "recurring symbol / memory hook"},
    "relationship_state": {"label": "Relationship state", "category": "relationship_context", "role": "bond dynamic / current emotional state"},
    "character_profile": {"label": "Character profile", "category": "character_context", "role": "persona / behavior"},
    "world_lore": {"label": "World lore", "category": "world_context", "role": "setting fact"},
    "location_context": {"label": "Location context", "category": "location_context", "role": "scene environment"},
    "organization_lore": {"label": "Organization pressure", "category": "organization_context", "role": "faction fact"},
    "artifact_rule": {"label": "Artifact rule", "category": "artifact_context", "role": "object rule"},
    "ritual_rule": {"label": "Ritual rule", "category": "ritual_context", "role": "practice rule"},
    "system_rule": {"label": "System rule", "category": "system_context", "role": "cycle/system rule"},
    "creature_lore": {"label": "Creature lore", "category": "creature_context", "role": "creature behavior"},
    "legend_lore": {"label": "Legend reveal", "category": "legend_context", "role": "legend / hidden truth"},
    "scenario_pressure": {"label": "Scenario pressure", "category": "scenario_context", "role": "scene pressure"},
    "semantic_fact": {"label": "Semantic fact", "category": "retrieved_memory", "role": "general memory fact"},
    "episodic_memory": {"label": "Episodic memory", "category": "retrieved_memory", "role": "event memory"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_doc(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] or 0)
    except Exception:
        return 0


def ensure_retrieval_label_debug_schema() -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rp_retrieval_label_proofs (
                proof_id TEXT PRIMARY KEY,
                trace_id TEXT DEFAULT '',
                query TEXT DEFAULT '',
                result_id TEXT DEFAULT '',
                source_table TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                source_record_kind TEXT DEFAULT '',
                memory_type TEXT DEFAULT '',
                scene_category TEXT DEFAULT '',
                retrieval_label TEXT DEFAULT '',
                semantic_role TEXT DEFAULT '',
                score REAL DEFAULT 0,
                lanes_json TEXT DEFAULT '[]',
                proof_flags_json TEXT DEFAULT '{}',
                why_selected TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_retrieval_label_proofs_trace ON rp_retrieval_label_proofs(trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_retrieval_label_proofs_source ON rp_retrieval_label_proofs(source_table, source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_retrieval_label_proofs_category ON rp_retrieval_label_proofs(scene_category)")
        conn.commit()
    return {"ok": True, "status": "ready", "table": "rp_retrieval_label_proofs"}


def _extract_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if not payload and isinstance(item.get("metadata"), dict):
        payload = item.get("metadata") or {}
    return payload


def _infer_kind(item: dict[str, Any]) -> str:
    payload = _extract_payload(item)
    for key in ("source_record_kind", "record_kind", "kind", "entity_kind"):
        value = item.get(key) or payload.get(key)
        if value:
            return str(value).strip().lower().replace(" ", "_")
    table = str(item.get("table") or item.get("source_table") or payload.get("source_table") or "").lower()
    if "entity" in table:
        return str(payload.get("kind") or payload.get("source_record_kind") or "entity").lower()
    if "canon" in table:
        return "canon_breakdown"
    if "source" in table:
        return "source_document"
    return "memory"


def _infer_memory_type(item: dict[str, Any]) -> str:
    payload = _extract_payload(item)
    for key in ("memory_type", "type", "semantic_role", "matched_compiler_rule"):
        value = item.get(key) or payload.get(key)
        if value:
            return str(value).strip().lower().replace(" ", "_")
    return ""


def label_retrieval_result(item: dict[str, Any]) -> dict[str, Any]:
    payload = _extract_payload(item)
    kind = _infer_kind(item)
    memory_type = _infer_memory_type(item)
    profile = MEMORY_TYPE_LABELS.get(memory_type) or KIND_LABELS.get(kind) or {"label": "Retrieved memory", "category": "retrieved_memory", "role": "general retrieved context"}
    scene_category = str(item.get("scene_category") or payload.get("scene_category") or profile["category"])
    label = str(item.get("retrieval_label") or payload.get("retrieval_label") or profile["label"])
    semantic_role = str(item.get("semantic_role") or payload.get("semantic_role") or profile["role"])
    lanes = item.get("lanes") or ([item.get("lane")] if item.get("lane") else [])
    lanes = [str(lane) for lane in lanes if lane]
    score = float(item.get("score") or item.get("combined_score") or item.get("rerank_score") or 0.0)
    proof_flags = {
        "has_source_table": bool(item.get("table") or item.get("source_table") or payload.get("source_table")),
        "has_source_id": bool(item.get("source_id") or payload.get("source_id") or item.get("result_id")),
        "has_kind": kind not in {"", "memory", "entity"},
        "has_memory_type": bool(memory_type),
        "has_scene_category": bool(scene_category),
        "has_score": score > 0,
        "multi_lane_match": len(lanes) > 1,
        "strong_label": label != "Retrieved memory",
    }
    why_bits = []
    if lanes:
        why_bits.append("matched " + " + ".join(lanes))
    if memory_type:
        why_bits.append(f"memory type `{memory_type}`")
    if kind and kind not in {"memory", "entity"}:
        why_bits.append(f"record kind `{kind}`")
    if scene_category:
        why_bits.append(f"routes to `{scene_category}`")
    if not why_bits:
        why_bits.append("generic retrieval fallback")
    out = dict(item)
    out.update({
        "retrieval_label": label,
        "scene_category": scene_category,
        "semantic_role": semantic_role,
        "source_record_kind": kind,
        "memory_type": memory_type or str(payload.get("memory_type") or ""),
        "debug_badges": [label, scene_category, *(lanes or [])],
        "proof_flags": proof_flags,
        "why_selected": "; ".join(why_bits),
    })
    return out


def label_runtime_retrieval_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [label_retrieval_result(row) for row in rows]


def write_retrieval_label_proofs(*, trace_id: str, query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_retrieval_label_debug_schema()
    now = _now()
    written = 0
    with _connect() as conn:
        for index, item in enumerate(results):
            payload = _extract_payload(item)
            result_id = str(item.get("result_id") or item.get("index_id") or item.get("source_id") or index)
            source_table = str(item.get("table") or item.get("source_table") or payload.get("source_table") or "")
            source_id = str(item.get("source_id") or payload.get("source_id") or result_id)
            proof_id = f"label-proof-{trace_id}-{index:03d}"
            conn.execute(
                """
                INSERT OR REPLACE INTO rp_retrieval_label_proofs(
                    proof_id, trace_id, query, result_id, source_table, source_id, source_record_kind,
                    memory_type, scene_category, retrieval_label, semantic_role, score, lanes_json,
                    proof_flags_json, why_selected, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proof_id,
                    trace_id,
                    query,
                    result_id,
                    source_table,
                    source_id,
                    str(item.get("source_record_kind") or ""),
                    str(item.get("memory_type") or ""),
                    str(item.get("scene_category") or ""),
                    str(item.get("retrieval_label") or ""),
                    str(item.get("semantic_role") or ""),
                    float(item.get("score") or item.get("combined_score") or 0.0),
                    _json(item.get("lanes") or ([item.get("lane")] if item.get("lane") else [])),
                    _json(item.get("proof_flags") or {}),
                    str(item.get("why_selected") or ""),
                    _json({"title": item.get("title"), "content_preview": str(item.get("content") or "")[:900], "payload": payload}),
                    "active",
                    now,
                ),
            )
            written += 1
        conn.commit()
    return {"ok": True, "status": "written", "trace_id": trace_id, "proof_count": written}


def retrieval_label_debug_contract_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_retrieval_label_debug_schema()
    payload = {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "active",
        "ready": True,
        "purpose": "Adds human-readable retrieval labels, scene packet category labels, why-selected explanations, and proof rows for every runtime retrieval result.",
        "endpoints": {
            "contract": "/api/roleplay/retrieval-labels/contract",
            "state": "/api/roleplay/retrieval-labels/state",
            "ensure_schema": "/api/roleplay/retrieval-labels/ensure-schema",
        },
        "label_sources": ["source_record_kind", "memory_type", "scene_category", "compiler semantic_role", "retrieval lane"],
        "proof_table": "rp_retrieval_label_proofs",
        "locked_rules": [
            "Every runtime retrieval result must expose a human-readable retrieval_label.",
            "Every result should map to a scene_category before Scene Packet build.",
            "Every runtime retrieval run writes label proof rows tied to the retrieval trace id.",
            "Generic fallback labels are allowed only as safety, not as the target behavior.",
        ],
        "kind_labels": KIND_LABELS,
        "memory_type_labels": MEMORY_TYPE_LABELS,
    }
    if write_report:
        _write_json(CONTRACT_PATH, payload)
        _write_doc(SYSTEM_MEMORY_DOC, "Roleplay Phase 17.5D — Retrieval Labels + Debug Proof", "Runtime retrieval results now carry human labels, scene categories, semantic roles, why-selected text, proof flags, and SQLite proof rows. This makes all first-class Builder kinds inspectable before Scene Packet injection.")
        _write_doc(SYSTEM_SURFACE_DOC, "Roleplay Surface Phase 17.5D — Retrieval Labels + Debug Proof", "Runtime result cards and Inspector debug panels expose retrieval label, category, memory type, source kind, and why-selected proof so users can see whether the right worldbuilding rows are being selected.")
    return payload


def retrieval_label_debug_state_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_retrieval_label_debug_schema()
    with _connect() as conn:
        proof_count = _table_count(conn, "rp_retrieval_label_proofs")
        try:
            rows = conn.execute(
                "SELECT scene_category, COUNT(*) AS c FROM rp_retrieval_label_proofs GROUP BY scene_category ORDER BY c DESC"
            ).fetchall()
            categories = {str(row["scene_category"] or "unlabeled"): int(row["c"] or 0) for row in rows}
        except Exception:
            categories = {}
        try:
            recent = [dict(row) for row in conn.execute("SELECT * FROM rp_retrieval_label_proofs ORDER BY created_at DESC LIMIT 12").fetchall()]
        except Exception:
            recent = []
    payload = {
        "schema_id": f"{SCHEMA_ID}.state",
        "version": VERSION,
        "status": "active",
        "ready": True,
        "proof_count": proof_count,
        "category_counts": categories,
        "recent_proofs": recent,
        "contract": retrieval_label_debug_contract_payload(write_report=False),
        "updated_at": _now(),
    }
    if write_report:
        _write_json(STATE_PATH, payload)
    return payload
