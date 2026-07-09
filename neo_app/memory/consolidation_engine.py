from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .unified_schema import ensure_unified_memory_schema, unified_memory_schema_status

CONSOLIDATION_SCHEMA_ID = "neo.memory.consolidation.phase_m4.v1"
CONSOLIDATION_VERSION = "deterministic-consolidation.v1"

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "about", "have", "has", "are", "was", "were", "will",
    "your", "you", "user", "neo", "memory", "scene", "roleplay", "prompt", "caption", "image", "metadata", "record",
    "source", "backend", "provider", "local", "runtime", "project", "scope", "fragment", "summary", "generation",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash_text(text: str, length: int = 24) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _safe_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 5000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _snippet(text: str, limit: int = 320) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _keywords(text: str, *, limit: int = 18) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_'-]{2,}", text or "")
    counter: Counter[str] = Counter()
    seen_original: dict[str, str] = {}
    for word in words:
        key = word.lower().strip("_'-")
        if len(key) < 3 or key in _STOPWORDS:
            continue
        counter[key] += 1
        seen_original.setdefault(key, word.strip())
    return [seen_original[key] for key, _ in counter.most_common(limit)]


class UnifiedMemoryConsolidationEngine:
    """Deterministic Phase M4 consolidation for Neo's unified memory schema.

    M4 intentionally does not require an LLM, embeddings, reranker, or Chroma. It
    reads scoped neo_memory_fragments/events and writes auditable summaries back
    into SQLite. Embeddings stay queued for M9+ indexing.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        return conn

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            schema = unified_memory_schema_status(conn)
            rows = conn.execute(
                """
                SELECT surface, COUNT(*) AS count
                FROM neo_memory_summaries
                WHERE status='active'
                GROUP BY surface
                ORDER BY surface
                """
            ).fetchall()
            jobs = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM neo_memory_jobs
                WHERE job_type='memory_consolidation'
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
            queued = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM neo_memory_fragments
                WHERE status='active' AND memory_type!='consolidated_summary'
                """
            ).fetchone()["count"]
        return {
            "ok": True,
            "schema_id": CONSOLIDATION_SCHEMA_ID,
            "status": "ready",
            "version": CONSOLIDATION_VERSION,
            "summary_counts_by_surface": {row["surface"]: row["count"] for row in rows},
            "job_counts_by_status": {row["status"]: row["count"] for row in jobs},
            "active_source_fragment_count": int(queued or 0),
            "unified_schema": schema,
            "policy": "M4 consolidation is deterministic, SQLite-first, additive, and does not archive originals unless explicitly requested.",
        }

    def plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        surfaces = [str(item).strip() for item in (data.get("surfaces") or []) if str(item or "").strip()]
        surface = str(data.get("surface") or "").strip()
        if surface and surface not in surfaces:
            surfaces.append(surface)
        project_id = str(data.get("project_id") or "").strip() or None
        scope_id = str(data.get("scope_id") or "").strip() or None
        min_group_size = _safe_int(data.get("min_group_size"), 2, minimum=1, maximum=500)
        limit = _safe_int(data.get("limit"), 25, minimum=1, maximum=500)
        group_limit = _safe_int(data.get("group_fragment_limit"), 80, minimum=3, maximum=500)
        include_existing = bool(data.get("include_existing", False))

        where = ["status='active'", "memory_type!='consolidated_summary'"]
        params: list[Any] = []
        if surfaces:
            where.append("surface IN (%s)" % ",".join("?" for _ in surfaces))
            params.extend(surfaces)
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if scope_id:
            where.append("scope_id=?")
            params.append(scope_id)
        where_sql = " AND ".join(where)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT fragment_id, surface, project_id, scope_id, source_type, source_id, memory_type, title, content, summary, priority, confidence, trust_level, updated_at, metadata_json, content_hash
                FROM neo_memory_fragments
                WHERE {where_sql}
                ORDER BY surface, project_id, scope_id, memory_type, updated_at DESC
                """,
                params,
            ).fetchall()
            existing_summary_rows = conn.execute(
                "SELECT summary_id, surface, project_id, scope_id, summary_type, covers_json, metadata_json, updated_at FROM neo_memory_summaries WHERE status='active'"
            ).fetchall()

        existing_by_group: set[str] = set()
        for row in existing_summary_rows:
            meta = _json_loads(row["metadata_json"], {})
            group_key = str(meta.get("group_key") or "")
            if group_key:
                existing_by_group.add(group_key)

        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            key_parts = [row["surface"] or "global", row["project_id"] or "", row["scope_id"] or "", row["memory_type"] or "fragment"]
            group_key = "|".join(key_parts)
            grouped[group_key].append(row)

        groups: list[dict[str, Any]] = []
        for group_key, items in grouped.items():
            if len(items) < min_group_size:
                continue
            if group_key in existing_by_group and not include_existing:
                continue
            sample = items[:group_limit]
            combined = "\n".join((item["summary"] or item["content"] or "") for item in sample)
            keywords = _keywords(combined, limit=16)
            first = sample[0]
            summary_id = "sum_" + _hash_text(group_key)
            groups.append({
                "group_id": "grp_" + _hash_text(group_key),
                "group_key": group_key,
                "summary_id": summary_id,
                "surface": first["surface"],
                "project_id": first["project_id"],
                "scope_id": first["scope_id"],
                "memory_type": first["memory_type"],
                "fragment_count": len(items),
                "candidate_fragment_ids": [item["fragment_id"] for item in sample],
                "source_types": sorted({str(item["source_type"] or "") for item in items if item["source_type"]}),
                "trust_levels": sorted({str(item["trust_level"] or "") for item in items if item["trust_level"]}),
                "keywords": keywords,
                "preview": [_snippet(item["summary"] or item["content"], 220) for item in sample[:5]],
                "recommended_action": "refresh_summary" if group_key in existing_by_group else "create_summary",
            })
        groups.sort(key=lambda g: (-int(g["fragment_count"]), str(g["surface"]), str(g["memory_type"])))
        return {
            "ok": True,
            "schema_id": CONSOLIDATION_SCHEMA_ID,
            "status": "ready",
            "version": CONSOLIDATION_VERSION,
            "filters": {
                "surfaces": surfaces,
                "project_id": project_id,
                "scope_id": scope_id,
                "min_group_size": min_group_size,
                "limit": limit,
                "group_fragment_limit": group_limit,
                "include_existing": include_existing,
            },
            "group_count": len(groups[:limit]),
            "total_candidate_groups": len(groups),
            "groups": groups[:limit],
            "policy": "Plan is read-only. Run creates scoped summaries and summary fragments; source fragments stay auditable.",
        }

    def _build_summary_content(self, group: dict[str, Any], rows: list[sqlite3.Row], *, max_items: int) -> str:
        lines: list[str] = []
        title = f"{group.get('surface')} / {group.get('memory_type')} memory summary"
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"Scope: {group.get('scope_id') or 'surface/project'}")
        lines.append(f"Fragments consolidated: {group.get('fragment_count')}")
        kws = group.get("keywords") or []
        if kws:
            lines.append(f"Key terms: {', '.join(kws[:16])}")
        lines.append("")
        lines.append("## Consolidated observations")
        seen: set[str] = set()
        for row in rows[:max_items]:
            text = _snippet(row["summary"] or row["content"], 360)
            if not text:
                continue
            marker = text.lower()[:120]
            if marker in seen:
                continue
            seen.add(marker)
            lines.append(f"- {text}")
        lines.append("")
        lines.append("## Source policy")
        lines.append("- This summary is deterministic and source-backed by unified SQLite memory fragments.")
        lines.append("- It is a recall aid, not a replacement for original source fragments.")
        lines.append("- Embeddings/reranking are queued for later retrieval phases and are not required for this consolidation.")
        return "\n".join(lines).strip()

    def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        dry_run = bool(data.get("dry_run", False))
        archive_originals = bool(data.get("archive_originals", False))
        max_groups = _safe_int(data.get("max_groups") or data.get("limit"), 25, minimum=1, maximum=500)
        max_items = _safe_int(data.get("summary_item_limit"), 16, minimum=3, maximum=80)
        plan_payload = dict(data)
        plan_payload.setdefault("limit", max_groups)
        plan_payload.setdefault("min_group_size", data.get("min_group_size") or 2)
        plan = self.plan(plan_payload)
        groups = (plan.get("groups") or [])[:max_groups]
        stamp = _now()
        job_id = "job_memory_consolidation_" + _hash_text(stamp + _json_dumps(plan_payload), 20)

        if dry_run:
            return {
                "ok": True,
                "schema_id": CONSOLIDATION_SCHEMA_ID,
                "status": "dry_run",
                "job_id": job_id,
                "planned_group_count": len(groups),
                "plan": plan,
            }

        created: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_memory_jobs (job_id, job_type, status, surface, project_id, scope_id, started_at, finished_at, progress_json, result_json, error, created_at, updated_at)
                VALUES (?, 'memory_consolidation', 'running', 'global', NULL, NULL, ?, NULL, ?, '{}', '', ?, ?)
                """,
                (job_id, stamp, _json_dumps({"planned_groups": len(groups)}), stamp, stamp),
            )
            try:
                for group in groups:
                    frag_ids = list(group.get("candidate_fragment_ids") or [])
                    if not frag_ids:
                        continue
                    placeholders = ",".join("?" for _ in frag_ids)
                    rows = conn.execute(
                        f"SELECT * FROM neo_memory_fragments WHERE fragment_id IN ({placeholders}) ORDER BY updated_at DESC",
                        frag_ids,
                    ).fetchall()
                    if not rows:
                        continue
                    content = self._build_summary_content(group, rows, max_items=max_items)
                    content_hash = _hash_text(content, 64)
                    summary_id = str(group.get("summary_id") or ("sum_" + _hash_text(str(group.get("group_key")))))
                    summary_fragment_id = "frag_summary_" + _hash_text(summary_id + content_hash, 24)
                    title = f"{group.get('surface')} {group.get('memory_type')} consolidated summary"
                    metadata = {
                        "phase": "M4",
                        "group_id": group.get("group_id"),
                        "group_key": group.get("group_key"),
                        "keywords": group.get("keywords") or [],
                        "source_fragment_count": group.get("fragment_count"),
                        "consolidation_version": CONSOLIDATION_VERSION,
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO neo_memory_summaries (summary_id, surface, project_id, scope_id, summary_type, title, content, covers_json, source_ids_json, confidence, status, metadata_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'deterministic_scope_summary', ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                        """,
                        (
                            summary_id,
                            group.get("surface") or "global",
                            group.get("project_id"),
                            group.get("scope_id"),
                            title,
                            content,
                            _json_dumps({"fragment_ids": frag_ids, "group_key": group.get("group_key")}),
                            _json_dumps(frag_ids),
                            0.86,
                            _json_dumps(metadata),
                            stamp,
                            stamp,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO neo_memory_fragments (fragment_id, surface, project_id, scope_id, source_type, source_id, memory_type, title, content, summary, token_estimate, priority, confidence, trust_level, status, metadata_json, created_at, updated_at, content_hash, embedding_status)
                        VALUES (?, ?, ?, ?, 'memory_consolidation', ?, 'consolidated_summary', ?, ?, ?, ?, 0.82, 0.86, 'inferred', 'active', ?, ?, ?, ?, 'queued')
                        """,
                        (
                            summary_fragment_id,
                            group.get("surface") or "global",
                            group.get("project_id"),
                            group.get("scope_id"),
                            summary_id,
                            title,
                            content,
                            _snippet(content, 900),
                            max(1, int(len(content) / 4)),
                            _json_dumps({**metadata, "summary_id": summary_id}),
                            stamp,
                            stamp,
                            content_hash,
                        ),
                    )
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO neo_memory_fragments_fts (fragment_id, surface, project_id, scope_id, title, content, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (summary_fragment_id, group.get("surface") or "global", group.get("project_id"), group.get("scope_id"), title, content, _snippet(content, 900)),
                        )
                    except sqlite3.OperationalError:
                        pass
                    if archive_originals:
                        conn.execute(
                            f"UPDATE neo_memory_fragments SET status='summarized', updated_at=? WHERE fragment_id IN ({placeholders})",
                            [stamp, *frag_ids],
                        )
                    created.append({
                        "summary_id": summary_id,
                        "summary_fragment_id": summary_fragment_id,
                        "surface": group.get("surface"),
                        "project_id": group.get("project_id"),
                        "scope_id": group.get("scope_id"),
                        "memory_type": group.get("memory_type"),
                        "source_fragment_count": len(frag_ids),
                    })
                finished = _now()
                result = {"created_count": len(created), "created": created, "errors": errors, "archive_originals": archive_originals}
                conn.execute(
                    "UPDATE neo_memory_jobs SET status='completed', finished_at=?, progress_json=?, result_json=?, updated_at=? WHERE job_id=?",
                    (finished, _json_dumps({"created_count": len(created), "error_count": len(errors)}), _json_dumps(result), finished, job_id),
                )
            except Exception as exc:
                finished = _now()
                conn.execute(
                    "UPDATE neo_memory_jobs SET status='failed', finished_at=?, error=?, updated_at=? WHERE job_id=?",
                    (finished, str(exc)[:1000], finished, job_id),
                )
                raise
        return {
            "ok": True,
            "schema_id": CONSOLIDATION_SCHEMA_ID,
            "status": "completed",
            "version": CONSOLIDATION_VERSION,
            "job_id": job_id,
            "created_count": len(created),
            "created": created,
            "errors": errors,
            "policy": "Summaries were written to neo_memory_summaries and mirrored as consolidated_summary fragments for later retrieval/indexing.",
        }
