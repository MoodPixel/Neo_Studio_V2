from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from neo_app.roleplay.sqlite_store import ensure_roleplay_memory_schema, roleplay_sqlite_state_payload, _connect

try:
    from neo_app.admin.engine import admin_engine_state_payload
    from neo_app.admin.semantic_engine import embed_texts, rerank_results
except Exception:  # pragma: no cover - optional Admin semantic bridge
    admin_engine_state_payload = None  # type: ignore
    embed_texts = None  # type: ignore
    rerank_results = None  # type: ignore

SCHEMA_ID = "neo.roleplay.contradictions.v1"
VERSION = "1.1.0-semantic-contradiction-scoring"

CONTRADICTION_STATUSES = ("open", "ignored", "intentional", "resolved")
EXCLUSIVE_TERMS: tuple[tuple[str, str, str], ...] = (
    ("alive", "dead", "alive_vs_dead"),
    ("living", "dead", "alive_vs_dead"),
    ("single", "married", "bond_status_conflict"),
    ("single", "mated", "bond_status_conflict"),
    ("enemy", "ally", "relationship_alignment_conflict"),
    ("hostile", "friendly", "relationship_alignment_conflict"),
    ("destroyed", "intact", "state_conflict"),
    ("destroyed", "standing", "state_conflict"),
    ("canon", "noncanon", "canon_status_conflict"),
    ("approved", "deprecated", "status_conflict"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(text)).strip("_")[:80]


def _tokens(text: Any) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_'-]+", str(text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    if size <= 0:
        return 0.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(size))
    an = sum(float(a[i]) * float(a[i]) for i in range(size)) ** 0.5
    bn = sum(float(b[i]) * float(b[i]) for i in range(size)) ** 0.5
    if not an or not bn:
        return 0.0
    return max(0.0, min(1.0, dot / (an * bn)))


def _confidence_from_scores(*, lexical_score: float, semantic_score: float, rule_weight: float = 0.65) -> float:
    semantic_component = max(0.0, min(1.0, semantic_score)) * 0.35
    lexical_component = max(0.0, min(1.0, lexical_score)) * 0.20
    return round(max(0.05, min(0.99, rule_weight + semantic_component + lexical_component)), 4)


def _severity_from_confidence(base: str, confidence: float) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    level = order.get(base, 1)
    if confidence >= 0.92:
        level = max(level, 2)
    if confidence >= 0.97:
        level = max(level, 3)
    if confidence < 0.55:
        level = min(level, 1)
    return {v: k for k, v in order.items()}.get(level, base)


def _suggest_resolution(rule_id: str, a: dict[str, Any], b: dict[str, Any], details: dict[str, Any]) -> str:
    title = a.get("title") or b.get("title") or "this record"
    if rule_id == "duplicate_entity_title":
        return f"Decide whether '{title}' should be merged, renamed as a variant, or marked as an intentional duplicate."
    if rule_id == "age_conflict":
        return f"Choose the canon age for '{title}', then update or deprecate the conflicting source row."
    if rule_id == "exclusive_term_conflict":
        family = str(details.get("family") or "fact conflict").replace("_", " ")
        return f"Review the {family}; mark it intentional if it is a plot twist, otherwise update the stale memory/source."
    if rule_id == "semantic_near_duplicate":
        return f"These records are semantically very close. Merge them, link them as variants, or mark as intentional if both are needed."
    return "Review both evidence rows, keep the canon source, and resolve or mark intentional with a note."


def _report_id(rule_id: str, source_a: str, source_b: str, summary: str) -> str:
    seed = "|".join(sorted([source_a, source_b]) + [rule_id, _norm(summary)])
    return "contradiction-" + hashlib.blake2b(seed.encode("utf-8"), digest_size=10).hexdigest()


def _entity_payload_text(row: dict[str, Any]) -> str:
    payload = _read_json(row.get("payload_json"), {})
    tags = _read_json(row.get("tags_json"), [])
    return "\n".join([
        str(row.get("title") or ""),
        str(row.get("kind") or ""),
        str(row.get("status") or ""),
        json.dumps(tags, ensure_ascii=False),
        json.dumps(payload, ensure_ascii=False, sort_keys=True)[:2500],
    ])


def _load_candidates(conn: sqlite3.Connection, *, scope_id: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    scope = str(scope_id or "").strip()
    lim = max(1, min(int(limit or 1000), 5000))
    candidates: list[dict[str, Any]] = []

    def add(table: str, row: sqlite3.Row, id_key: str, title_key: str, content_key: str = "content", scope_key: str = "scope_id") -> None:
        data = dict(row)
        source_id = str(data.get(id_key) or "")
        title = str(data.get(title_key) or source_id)
        content = str(data.get(content_key) or data.get("summary") or data.get("payload_json") or "")
        row_scope = str(data.get(scope_key) or data.get("scene_id") or data.get("storyline_id") or data.get("session_id") or "")
        if scope and scope not in (row_scope, source_id) and scope not in content and scope not in str(data.get("payload_json") or ""):
            return
        candidates.append({
            "table": table,
            "source_id": source_id,
            "source_key": f"{table}:{source_id}",
            "title": title,
            "title_norm": _slug(title),
            "scope_id": row_scope,
            "content": content,
            "content_norm": _norm(content),
            "tokens": _tokens(f"{title}\n{content}"),
            "payload": data,
        })

    for row in conn.execute("SELECT entity_id, kind, title, status, scope_id, tags_json, payload_json, updated_at FROM rp_entities ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
        data = dict(row)
        data["content"] = _entity_payload_text(data)
        add("rp_entities", sqlite3.Row if False else _DictRow(data), "entity_id", "title", "content", "scope_id")
    for row in conn.execute("SELECT row_id, scope_id, continuity_type, title, content, source_id, status, updated_at FROM rp_continuity_rows ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
        add("rp_continuity_rows", row, "row_id", "title", "content", "scope_id")
    for row in conn.execute("SELECT fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, updated_at FROM rp_memory_fragments ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
        add("rp_memory_fragments", row, "fragment_id", "memory_type", "content", "source_id")
    for row in conn.execute("SELECT memory_id, namespace, scope_id, title, content, status, payload_json, updated_at FROM rp_shared_memories ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
        add("rp_shared_memories", row, "memory_id", "title", "content", "scope_id")
    for row in conn.execute("SELECT checkpoint_id, storyline_id, session_id, title, summary, payload_json, updated_at FROM rp_story_checkpoints ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
        add("rp_story_checkpoints", row, "checkpoint_id", "title", "summary", "storyline_id")
    return candidates[:lim]


class _DictRow(dict):
    def keys(self):
        return super().keys()


def _evidence(a: dict[str, Any], b: dict[str, Any], details: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"source_table": a["table"], "source_id": a["source_id"], "title": a["title"], "excerpt": str(a.get("content") or "")[:500]},
        {"source_table": b["table"], "source_id": b["source_id"], "title": b["title"], "excerpt": str(b.get("content") or "")[:500]},
        {"details": details},
    ]


def ensure_contradiction_resolver_schema() -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_contradiction_reports (
                report_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'open',
                scope_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_a_table TEXT NOT NULL DEFAULT '',
                source_a_id TEXT NOT NULL DEFAULT '',
                source_b_table TEXT NOT NULL DEFAULT '',
                source_b_id TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                resolution_note TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                semantic_score REAL NOT NULL DEFAULT 0,
                lexical_score REAL NOT NULL DEFAULT 0,
                scoring_json TEXT NOT NULL DEFAULT '{}',
                resolution_suggestion TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_status ON rp_contradiction_reports(status);
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_scope ON rp_contradiction_reports(scope_id);
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_rule ON rp_contradiction_reports(rule_id);
            """
        )
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(rp_contradiction_reports)").fetchall()}
        for column_name, column_sql in {
            "confidence": "REAL NOT NULL DEFAULT 0",
            "semantic_score": "REAL NOT NULL DEFAULT 0",
            "lexical_score": "REAL NOT NULL DEFAULT 0",
            "scoring_json": "TEXT NOT NULL DEFAULT '{}'",
            "resolution_suggestion": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column_name not in existing_cols:
                conn.execute(f"ALTER TABLE rp_contradiction_reports ADD COLUMN {column_name} {column_sql}")
        conn.commit()
    return {"schema_id": SCHEMA_ID, "version": VERSION, "status": "ready", "ready": True}


def _report_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {status: 0 for status in CONTRADICTION_STATUSES}
    try:
        for row in conn.execute("SELECT status, COUNT(*) AS count FROM rp_contradiction_reports GROUP BY status").fetchall():
            counts[str(row["status"] or "open")] = int(row["count"] or 0)
    except Exception:
        pass
    counts["total"] = sum(counts.values())
    return counts


def list_contradiction_reports(*, status: str = "open", scope_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    ensure_contradiction_resolver_schema()
    clean_status = str(status or "").strip()
    clean_scope = str(scope_id or "").strip()
    lim = max(1, min(int(limit or 50), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rp_contradiction_reports
            WHERE (? = '' OR status = ?) AND (? = '' OR scope_id = ? OR source_a_id = ? OR source_b_id = ? OR evidence_json LIKE ?)
            ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'intentional' THEN 1 WHEN 'resolved' THEN 2 ELSE 3 END, updated_at DESC
            LIMIT ?
            """,
            (clean_status, clean_status, clean_scope, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim),
        ).fetchall()
    reports = []
    for row in rows:
        data = dict(row)
        data["evidence"] = _read_json(data.pop("evidence_json", "[]"), [])
        data["scoring"] = _read_json(data.pop("scoring_json", "{}"), {})
        reports.append(data)
    return reports


def contradiction_state_payload(*, include_reports: bool = True, status: str = "open") -> dict[str, Any]:
    ensure_contradiction_resolver_schema()
    with _connect() as conn:
        counts = _report_counts(conn)
    sqlite_state = roleplay_sqlite_state_payload()
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "surface_id": "roleplay",
        "status": "active",
        "ready": True,
        "sqlite": sqlite_state,
        "counts": counts,
        "reports": list_contradiction_reports(status=status) if include_reports else [],
        "rules": [
            {"rule_id": "duplicate_entity_title", "label": "Duplicate entity title", "severity": "low"},
            {"rule_id": "status_conflict", "label": "Status conflict", "severity": "medium"},
            {"rule_id": "age_conflict", "label": "Age conflict", "severity": "high"},
            {"rule_id": "exclusive_term_conflict", "label": "Mutually exclusive facts", "severity": "high"},
            {"rule_id": "semantic_near_duplicate", "label": "Semantic near duplicate", "severity": "medium"},
        ],
        "semantic_scoring": {
            "status": "active",
            "admin_engine_owned": True,
            "embedding_source": "Admin Engine semantic bridge with deterministic fallback",
            "confidence_fields": ["confidence", "semantic_score", "lexical_score"],
        },
        "actions": ["ignore", "mark_intentional", "resolve", "reopen"],
        "active_features": [
            "sqlite_contradiction_report_table",
            "entity_duplicate_scan",
            "status_conflict_scan",
            "age_conflict_scan",
            "exclusive_fact_scan",
            "resolver_status_actions",
            "semantic_similarity_scoring",
            "confidence_ranked_reports",
            "resolution_suggestion_text",
        ],
        "deferred_features": [
            "llm_assisted_resolution_drafts",
            "automatic_canon_rewrite",
            "graph_visual_resolution_flow",
        ],
    }


def _insert_report(conn: sqlite3.Connection, *, rule_id: str, severity: str, scope_id: str, title: str, summary: str, a: dict[str, Any], b: dict[str, Any], details: dict[str, Any], semantic_score: float = 0.0, lexical_score: float = 0.0, rule_weight: float = 0.65) -> dict[str, Any]:
    now = _now()
    rid = _report_id(rule_id, a["source_key"], b["source_key"], summary)
    confidence = _confidence_from_scores(lexical_score=lexical_score, semantic_score=semantic_score, rule_weight=rule_weight)
    severity = _severity_from_confidence(severity, confidence)
    scoring = {
        "confidence": confidence,
        "semantic_score": round(float(semantic_score or 0), 6),
        "lexical_score": round(float(lexical_score or 0), 6),
        "rule_weight": rule_weight,
        "semantic_mode": details.get("semantic_mode") or "admin_engine_or_fallback",
    }
    details = dict(details)
    details["scoring"] = scoring
    suggestion = _suggest_resolution(rule_id, a, b, details)
    evidence = _evidence(a, b, details)
    existing = conn.execute("SELECT status FROM rp_contradiction_reports WHERE report_id = ?", (rid,)).fetchone()
    status = str(existing["status"] if existing else "open")
    conn.execute(
        """
        INSERT INTO rp_contradiction_reports(report_id, rule_id, severity, status, scope_id, title, summary, source_a_table, source_a_id, source_b_table, source_b_id, evidence_json, resolution_note, confidence, semantic_score, lexical_score, scoring_json, resolution_suggestion, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            rule_id=excluded.rule_id,
            severity=excluded.severity,
            scope_id=excluded.scope_id,
            title=excluded.title,
            summary=excluded.summary,
            evidence_json=excluded.evidence_json,
            confidence=excluded.confidence,
            semantic_score=excluded.semantic_score,
            lexical_score=excluded.lexical_score,
            scoring_json=excluded.scoring_json,
            resolution_suggestion=excluded.resolution_suggestion,
            updated_at=excluded.updated_at
        """,
        (rid, rule_id, severity, status, scope_id, title, summary, a["table"], a["source_id"], b["table"], b["source_id"], _json(evidence), "", confidence, scoring["semantic_score"], scoring["lexical_score"], _json(scoring), suggestion, now, now),
    )
    return {"report_id": rid, "rule_id": rule_id, "severity": severity, "status": status, "summary": summary, "confidence": confidence, "semantic_score": scoring["semantic_score"], "lexical_score": scoring["lexical_score"], "resolution_suggestion": suggestion}


def _extract_age(text: str) -> set[str]:
    ages: set[str] = set()
    for match in re.finditer(r"\b(?:age\s*[:=]?\s*|aged\s+)?([1-9][0-9]{0,2})\s*(?:years?\s*old|yo|yrs?|age)?\b", text.lower()):
        num = int(match.group(1))
        if 1 <= num <= 999:
            ages.add(str(num))
    return ages


def _attach_semantic_vectors(candidates: list[dict[str, Any]], *, enabled: bool, limit: int) -> dict[str, Any]:
    if not enabled or not candidates:
        return {"status": "disabled", "mode": "none", "fallback_used": False}
    if embed_texts is None or admin_engine_state_payload is None:
        return {"status": "unavailable", "mode": "missing_admin_semantic_bridge", "fallback_used": True}
    sample = candidates[: max(1, min(limit, 300))]
    texts = [f"{row.get('title','')}\n{row.get('content','')}"[:5000] for row in sample]
    try:
        engine = admin_engine_state_payload()
        embedded = embed_texts(texts, engine_state=engine, allow_fallback=True)
        vectors = embedded.get("vectors") or []
        for row, vec in zip(sample, vectors):
            row["semantic_vector"] = vec
        return {k: v for k, v in embedded.items() if k != "vectors"}
    except Exception as exc:
        return {"status": "failed", "mode": "semantic_score_failed", "fallback_used": True, "error": str(exc)[:800]}


def scan_contradictions_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    scope_id = str(data.get("scope_id") or data.get("scope") or "").strip()
    limit = int(data.get("limit") or 1000)
    include_low = bool(data.get("include_low", True))
    use_semantic = bool(data.get("use_semantic", True))
    semantic_threshold = float(data.get("semantic_threshold") or 0.86)
    ensure_contradiction_resolver_schema()
    created: list[dict[str, Any]] = []
    with _connect() as conn:
        candidates = _load_candidates(conn, scope_id=scope_id, limit=limit)
        semantic_status = _attach_semantic_vectors(candidates, enabled=use_semantic, limit=limit)
        # Duplicate entity title scan.
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in candidates:
            if row["table"] != "rp_entities" or not row.get("title_norm"):
                continue
            kind = str(row["payload"].get("kind") or "entity")
            groups.setdefault((kind, row["title_norm"]), []).append(row)
        for (kind, title_norm), rows in groups.items():
            if len(rows) < 2:
                continue
            a, b = rows[0], rows[1]
            summary = f"Multiple {kind} records share the title '{a['title']}'. Confirm whether these are duplicates, variants, or separate canon records."
            created.append(_insert_report(conn, rule_id="duplicate_entity_title", severity="low", scope_id=scope_id or a.get("scope_id") or b.get("scope_id") or "", title=a["title"], summary=summary, a=a, b=b, details={"kind": kind, "title_norm": title_norm, "record_count": len(rows), "semantic_mode": semantic_status.get("mode")}, semantic_score=_cosine(a.get("semantic_vector"), b.get("semantic_vector")), lexical_score=_jaccard(a.get("tokens", set()), b.get("tokens", set())), rule_weight=0.58))
        # Pairwise conflict scan with cheap candidate narrowing.
        max_pairs = min(len(candidates), 350)
        for i in range(max_pairs):
            a = candidates[i]
            if not a.get("content_norm"):
                continue
            for b in candidates[i + 1:max_pairs]:
                if a["source_key"] == b["source_key"]:
                    continue
                shared_context = bool(a.get("scope_id") and a.get("scope_id") == b.get("scope_id")) or bool(a.get("title_norm") and a.get("title_norm") == b.get("title_norm")) or bool((a["tokens"] & b["tokens"]) and len(a["tokens"] & b["tokens"]) >= 2)
                semantic_score = _cosine(a.get("semantic_vector"), b.get("semantic_vector"))
                lexical_score = _jaccard(a.get("tokens", set()), b.get("tokens", set()))
                if not shared_context and semantic_score < semantic_threshold:
                    continue
                a_text = a["content_norm"]
                b_text = b["content_norm"]
                # Age conflict when titles match or records are scoped together.
                if a.get("title_norm") and a.get("title_norm") == b.get("title_norm"):
                    a_ages = _extract_age(a_text)
                    b_ages = _extract_age(b_text)
                    if a_ages and b_ages and a_ages != b_ages:
                        summary = f"Age conflict for '{a['title']}': {', '.join(sorted(a_ages))} vs {', '.join(sorted(b_ages))}."
                        created.append(_insert_report(conn, rule_id="age_conflict", severity="high", scope_id=scope_id or a.get("scope_id") or b.get("scope_id") or "", title=a["title"], summary=summary, a=a, b=b, details={"a_ages": sorted(a_ages), "b_ages": sorted(b_ages), "semantic_mode": semantic_status.get("mode")}, semantic_score=semantic_score, lexical_score=lexical_score, rule_weight=0.72))
                # Exclusive terms.
                for left, right, family in EXCLUSIVE_TERMS:
                    if (left in a["tokens"] and right in b["tokens"]) or (right in a["tokens"] and left in b["tokens"]):
                        summary = f"Possible {family.replace('_', ' ')}: '{left}' conflicts with '{right}' across related records."
                        created.append(_insert_report(conn, rule_id="exclusive_term_conflict", severity="high" if family != "status_conflict" else "medium", scope_id=scope_id or a.get("scope_id") or b.get("scope_id") or "", title=a.get("title") or b.get("title") or family, summary=summary, a=a, b=b, details={"left": left, "right": right, "family": family, "semantic_mode": semantic_status.get("mode")}, semantic_score=semantic_score, lexical_score=lexical_score, rule_weight=0.70))
                        break
                if semantic_score >= max(0.90, semantic_threshold) and lexical_score >= 0.18 and a.get("title_norm") != b.get("title_norm"):
                    if a.get("table") in {"rp_entities", "rp_memory_fragments", "rp_shared_memories"} and b.get("table") in {"rp_entities", "rp_memory_fragments", "rp_shared_memories"}:
                        summary = f"Semantic near-duplicate candidate: '{a.get('title')}' and '{b.get('title')}' are strongly similar but stored as separate records."
                        created.append(_insert_report(conn, rule_id="semantic_near_duplicate", severity="medium", scope_id=scope_id or a.get("scope_id") or b.get("scope_id") or "", title=a.get("title") or b.get("title") or "Semantic duplicate", summary=summary, a=a, b=b, details={"semantic_threshold": semantic_threshold, "semantic_mode": semantic_status.get("mode")}, semantic_score=semantic_score, lexical_score=lexical_score, rule_weight=0.38))
        conn.commit()
    return {
        "schema_id": "neo.roleplay.contradictions.scan.v1",
        "status": "scanned",
        "scope_id": scope_id,
        "created_or_updated_count": len(created),
        "created_or_updated": created[:100],
        "semantic_scoring": semantic_status if 'semantic_status' in locals() else {"status": "not_run"},
        "contradictions": contradiction_state_payload(include_reports=True),
    }


def update_contradiction_report_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    report_id = str(data.get("report_id") or "").strip()
    action = str(data.get("action") or data.get("status") or "").strip().lower()
    note = str(data.get("resolution_note") or data.get("note") or "")
    if not report_id:
        raise ValueError("report_id is required")
    status_map = {
        "ignore": "ignored",
        "ignored": "ignored",
        "mark_intentional": "intentional",
        "intentional": "intentional",
        "resolve": "resolved",
        "resolved": "resolved",
        "reopen": "open",
        "open": "open",
    }
    status = status_map.get(action)
    if not status:
        raise ValueError("action must be ignore, mark_intentional, resolve, or reopen")
    ensure_contradiction_resolver_schema()
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT report_id FROM rp_contradiction_reports WHERE report_id = ?", (report_id,)).fetchone()
        if not row:
            raise KeyError(report_id)
        conn.execute("UPDATE rp_contradiction_reports SET status = ?, resolution_note = ?, updated_at = ? WHERE report_id = ?", (status, note, now, report_id))
        conn.commit()
    return {"schema_id": "neo.roleplay.contradictions.update.v1", "status": "updated", "report_id": report_id, "report_status": status, "contradictions": contradiction_state_payload(include_reports=True)}
