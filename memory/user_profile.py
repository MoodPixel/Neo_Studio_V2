from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .unified_schema import ensure_unified_memory_schema

CORE_USER_PROFILE_SCHEMA_ID = "neo.memory.core_user_profile.aci6.v1"
CORE_USER_PROFILE_VERSION = "aci6-core-profile-temporal-v1"
ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"

# A Core User Profile is deliberately small. These are presentation/category
# labels, not a second truth schema; canonical truth remains neo_memory_facts.
_CORE_CATEGORY_PREFIXES = {
    "language": "language",
    "response_style": "response_style",
    "tone": "tone",
    "length": "verbosity",
    "prompt_style": "prompt_style",
    "caption_style": "caption_style",
    "workflow": "workflow",
    "aspect_ratio": "aspect_ratio",
    "resolution": "resolution",
    "color_palette": "color_palette",
    "lighting": "lighting",
    "skin_tone": "skin_tone",
    "model": "model",
    "backend": "backend",
    "sampler": "sampler",
    "scheduler": "scheduler",
    "cfg": "cfg",
    "steps": "steps",
}

# ACI-6 does not auto-inject sensitive-profile categories into every prompt.
# The durable-memory record can still remain reviewable/auditable in SQLite;
# this filter only controls the bounded Core User Profile projection.
_SENSITIVE_PROFILE_PATTERNS = (
    r"\b(?:password|passcode|pin code|api key|secret key|private key|recovery phrase|seed phrase)\b",
    r"\b(?:bank account|credit card|debit card|social security|passport number|national id)\b",
    r"\b(?:diagnos(?:is|ed)|medical condition|medication|mental health|pregnan(?:t|cy)|disability)\b",
    r"\b(?:religion|religious belief|political party|political ideology|voting preference|trade union)\b",
    r"\b(?:race|ethnicity|sexual orientation|sex life|criminal history)\b",
)

_EXPLICITNESS_RANK = {
    "explicit_override": 5,
    "explicit_user": 4,
    "explicit_memory_directive": 4,
    "reviewed": 3,
    "repeated_workflow": 2,
    "inferred": 1,
    "": 0,
}

_PROFILE_MEMORY_TYPES = {
    "user_preference_change",
    "workflow_preference_candidate",
    "user_memory_directive",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _clean(value: Any, limit: int = 1400) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def is_sensitive_profile_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(re.search(pattern, lowered, flags=re.I) for pattern in _SENSITIVE_PROFILE_PATTERNS)


def profile_category_from_durable_key(durable_key: str, memory_type: str = "") -> str:
    key = str(durable_key or "").strip()
    memory_type = str(memory_type or "").strip()
    if key.startswith("workflow_preference:") or memory_type == "workflow_preference_candidate":
        return "workflow"
    suffix = key.rsplit(":", 1)[-1] if ":" in key else key
    for prefix, category in _CORE_CATEGORY_PREFIXES.items():
        if suffix == prefix or suffix.startswith(prefix + "_"):
            return category
    if memory_type == "user_preference_change":
        # Explicit, reviewed preferences outside the standard buckets are still
        # allowed as bounded "other_preference" lines unless sensitivity policy
        # rejects them.
        return "other_preference"
    return ""


def profile_metadata_for_candidate(
    *,
    memory_type: str,
    durable_key: str,
    content: str,
    candidate_class: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = (payload or {}).get("profile") if isinstance((payload or {}).get("profile"), dict) else {}
    category = str(existing.get("category") or profile_category_from_durable_key(durable_key, memory_type)).strip()
    sensitive = bool(existing.get("sensitive")) or is_sensitive_profile_text(content)
    explicitness = str(existing.get("explicitness") or "").strip()
    if not explicitness:
        if candidate_class in {"explicit_user_override", "explicit_user_correction", "explicit_user_avoidance"}:
            explicitness = "explicit_override"
        elif candidate_class == "explicit_user_preference":
            explicitness = "explicit_user"
        elif candidate_class == "explicit_memory_directive":
            explicitness = "explicit_memory_directive"
        elif candidate_class == "workflow_preference":
            explicitness = "repeated_workflow"
        else:
            explicitness = "inferred"

    eligible = memory_type in _PROFILE_MEMORY_TYPES and bool(category) and not sensitive
    # Generic remember-this directives do not automatically enter the profile.
    # They are eligible only when they map to a known stable category.
    if memory_type == "user_memory_directive" and category in {"", "other_preference"}:
        eligible = False
    return {
        "eligible": bool(existing.get("eligible", eligible)) and not sensitive,
        "category": category,
        "explicitness": explicitness,
        "sensitive": sensitive,
        "temporal_intent": str(existing.get("temporal_intent") or "current"),
        "projection": "core_user_profile",
    }


def render_core_user_profile_context(snapshot: dict[str, Any] | None, *, max_chars: int = 2800) -> str:
    payload = snapshot if isinstance(snapshot, dict) else {}
    if str(payload.get("status") or "") != "ready":
        return ""
    lines = [
        "Core User Profile — approved current durable preferences only.",
        "Current user instructions in this conversation override older profile lines.",
    ]
    for item in payload.get("current") or []:
        category = str(item.get("category") or "preference").replace("_", " ")
        statement = _clean(item.get("statement") or item.get("value"), 520)
        if statement:
            lines.append(f"- [{category}] {statement}")
    return "\n".join(lines).strip()[: max(400, int(max_chars or 2800))]


@dataclass(frozen=True)
class ProfileScopePolicy:
    scope_class: str = "general_federated"
    allow_project_profile: bool = False

    @property
    def permitted(self) -> bool:
        return self.scope_class != "project_sandbox" or self.allow_project_profile


class CoreUserProfileService:
    """ACI-6 derived Core User Profile over canonical durable SQLite facts.

    The service never writes a second profile truth store. It reads reviewed or
    otherwise applied durable writebacks and their canonical neo_memory_facts,
    applies temporal/current-state rules, and returns a bounded projection with
    proof ids. Chroma is not an authority for profile truth.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def _rows(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._connect() as conn:
            if not self._table_exists(conn, "neo_memory_writebacks"):
                return []
            rows = conn.execute(
                """
                SELECT
                    f.*,
                    w.writeback_id,
                    w.memory_type AS writeback_memory_type,
                    w.candidate_class,
                    w.durable_key,
                    w.risk_level,
                    w.status AS writeback_status,
                    w.support_count,
                    w.support_threshold,
                    w.evidence_json,
                    w.payload_json AS writeback_payload_json,
                    w.created_at AS writeback_created_at,
                    w.last_supported_at AS writeback_last_supported_at,
                    w.supersedes_writeback_id,
                    w.superseded_by_writeback_id
                FROM neo_memory_facts f
                JOIN neo_memory_writebacks w ON w.applied_fact_id = f.fact_id
                WHERE w.memory_type IN ('user_preference_change', 'workflow_preference_candidate', 'user_memory_directive')
                  AND w.status IN ('applied', 'superseded', 'archived')
                ORDER BY COALESCE(f.valid_from, f.created_at) ASC, f.updated_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        metadata = _loads(row.get("metadata_json"), {})
        payload = _loads(row.get("writeback_payload_json"), {})
        memory_type = str(row.get("writeback_memory_type") or row.get("predicate") or "").strip()
        durable_key = str(row.get("profile_key") or row.get("durable_key") or metadata.get("durable_key") or "").strip()
        profile_meta = profile_metadata_for_candidate(
            memory_type=memory_type,
            durable_key=durable_key,
            content=str(row.get("statement") or row.get("object_value") or ""),
            candidate_class=str(row.get("candidate_class") or metadata.get("candidate_class") or ""),
            payload=payload,
        )
        if not profile_meta.get("eligible"):
            return None

        # Core profile source is global/general user memory only. Project-local
        # durable facts remain in Project memory and must not leak into this view.
        scope_id = str(row.get("scope_id") or "").strip()
        project_id = str(row.get("project_id") or "").strip()
        surface = str(row.get("surface") or "").strip()
        if scope_id not in {"", "general"}:
            return None
        if project_id not in {"", "general", "assistant:general"}:
            return None
        if surface not in {"", "global", "assistant"}:
            return None

        temporal_state = str(row.get("temporal_state") or "").strip()
        status = str(row.get("status") or "active").strip()
        if not temporal_state:
            temporal_state = "historical" if status in {"superseded", "archived", "deprecated"} or row.get("valid_to") else "current"
        explicitness = str(row.get("explicitness") or profile_meta.get("explicitness") or "reviewed").strip()
        evidence = _loads(row.get("evidence_json"), [])
        source_turn_ids = list(dict.fromkeys(
            str(item.get("source_id") or item.get("trace_id") or "")
            for item in evidence
            if isinstance(item, dict) and (item.get("source_id") or item.get("trace_id"))
        ))
        item = {
            "profile_key": durable_key,
            "category": str(row.get("profile_category") or profile_meta.get("category") or "other_preference"),
            "statement": _clean(row.get("statement") or row.get("object_value") or "", 1200),
            "value": _clean(row.get("object_value") or row.get("statement") or "", 1200),
            "fact_id": str(row.get("fact_id") or ""),
            "writeback_id": str(row.get("writeback_id") or ""),
            "source_event_id": str(row.get("source_event_id") or ""),
            "source_turn_ids": source_turn_ids,
            "memory_type": memory_type,
            "candidate_class": str(row.get("candidate_class") or ""),
            "explicitness": explicitness,
            "confidence": float(row.get("confidence") or 0.0),
            "support_count": int(row.get("support_count") or 0),
            "support_threshold": int(row.get("support_threshold") or 0),
            "trust_level": str(row.get("trust_level") or ""),
            "status": status,
            "temporal_state": temporal_state,
            "observed_at": str(row.get("observed_at") or row.get("writeback_created_at") or row.get("created_at") or ""),
            "valid_from": str(row.get("valid_from") or row.get("created_at") or ""),
            "valid_to": str(row.get("valid_to") or ""),
            "last_supported_at": str(row.get("last_supported_at") or row.get("writeback_last_supported_at") or row.get("updated_at") or ""),
            "supersedes_fact_id": str(row.get("supersedes_fact_id") or ""),
            "superseded_by_fact_id": str(row.get("superseded_by_fact_id") or ""),
            "supersedes_writeback_id": str(row.get("supersedes_writeback_id") or ""),
            "superseded_by_writeback_id": str(row.get("superseded_by_writeback_id") or ""),
            "policy": {"profile_eligible": True, "sensitive_projection_blocked": False},
        }
        item["proof"] = {
            "fact_id": item["fact_id"],
            "writeback_id": item["writeback_id"],
            "source_event_id": item["source_event_id"],
            "source_turn_ids": list(item["source_turn_ids"]),
            "valid_from": item["valid_from"],
            "valid_to": item["valid_to"],
            "temporal_state": item["temporal_state"],
            "supersedes_fact_id": item["supersedes_fact_id"],
            "superseded_by_fact_id": item["superseded_by_fact_id"],
        }
        return item

    @staticmethod
    def _active_at(item: dict[str, Any], at: datetime) -> bool:
        start = _parse_time(item.get("valid_from") or item.get("observed_at"))
        end = _parse_time(item.get("valid_to"))
        if start and start > at:
            return False
        if end and at >= end:
            return False
        if str(item.get("status") or "") in {"revoked", "deleted"}:
            return False
        return True

    @staticmethod
    def _rank(item: dict[str, Any]) -> tuple[int, float, str]:
        return (
            _EXPLICITNESS_RANK.get(str(item.get("explicitness") or ""), 0),
            float(item.get("confidence") or 0.0),
            str(item.get("last_supported_at") or item.get("valid_from") or ""),
        )

    def snapshot(
        self,
        *,
        scope_class: str = "general_federated",
        allow_project_profile: bool = False,
        as_of: str = "",
        limit: int = 12,
        include_history: bool = True,
    ) -> dict[str, Any]:
        policy = ProfileScopePolicy(scope_class=scope_class, allow_project_profile=allow_project_profile)
        limit = max(1, min(int(limit or 12), 24))
        if not policy.permitted:
            return {
                "ok": True,
                "schema_id": CORE_USER_PROFILE_SCHEMA_ID,
                "version": CORE_USER_PROFILE_VERSION,
                "status": "excluded_by_scope_policy",
                "scope_policy": {"scope_class": scope_class, "allow_project_profile": False},
                "current": [],
                "history": [],
                "current_count": 0,
                "history_count": 0,
                "conflict_count": 0,
                "projection": "derived_from_neo_memory_facts",
            }

        target_time = _parse_time(as_of) or datetime.now(timezone.utc)
        normalized = [item for raw in self._rows() if (item := self._normalize_row(raw)) is not None]
        by_key: dict[str, list[dict[str, Any]]] = {}
        for item in normalized:
            by_key.setdefault(str(item.get("profile_key") or item.get("fact_id")), []).append(item)

        current: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        conflict_count = 0
        for profile_key, timeline in by_key.items():
            active = [item for item in timeline if self._active_at(item, target_time)]
            if active:
                active.sort(key=self._rank, reverse=True)
                chosen = dict(active[0])
                chosen["temporal_state"] = "current" if not as_of else "current_as_of"
                chosen["competing_fact_ids"] = [str(item.get("fact_id") or "") for item in active[1:]]
                if len(active) > 1:
                    conflict_count += 1
                    chosen["selection_reason"] = "multiple_active_profile_facts_precedence_applied"
                else:
                    chosen["selection_reason"] = "single_active_profile_fact"
                current.append(chosen)
            if include_history:
                for item in timeline:
                    if active and item.get("fact_id") == active[0].get("fact_id"):
                        continue
                    historical = dict(item)
                    if historical.get("status") == "active" and not historical.get("valid_to") and not as_of:
                        # Another active row for the same key is surfaced as a competing
                        # current fact above, not silently called historical.
                        historical["temporal_state"] = "competing_current"
                    elif historical.get("temporal_state") in {"", "current"}:
                        historical["temporal_state"] = "historical"
                    history.append(historical)

        current.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("profile_key") or "")))
        history.sort(key=lambda item: str(item.get("valid_to") or item.get("last_supported_at") or item.get("valid_from") or ""), reverse=True)
        bounded = current[:limit]
        return {
            "ok": True,
            "schema_id": CORE_USER_PROFILE_SCHEMA_ID,
            "version": CORE_USER_PROFILE_VERSION,
            "status": "ready",
            "projection": "derived_from_neo_memory_facts",
            "source_authority": "sqlite_neo_memory_facts",
            "scope_policy": {"scope_class": scope_class, "allow_project_profile": bool(allow_project_profile)},
            "as_of": target_time.isoformat(),
            "current": bounded,
            "history": history[: max(limit * 4, 24)] if include_history else [],
            "current_count": len(current),
            "history_count": len(history),
            "conflict_count": conflict_count,
            "bounded": len(current) > limit,
            "policy": {
                "approved_durable_only": True,
                "project_sandbox_default": "excluded",
                "sensitive_categories_auto_injected": False,
                "historical_in_normal_context": False,
                "current_user_turn_overrides_profile": True,
            },
        }

    def history(self, profile_key: str, *, limit: int = 50) -> dict[str, Any]:
        key = str(profile_key or "").strip()
        snapshot = self.snapshot(limit=24, include_history=True)
        items = [item for item in [*(snapshot.get("current") or []), *(snapshot.get("history") or [])] if str(item.get("profile_key") or "") == key]
        items.sort(key=lambda item: str(item.get("valid_from") or item.get("observed_at") or ""), reverse=True)
        return {
            "ok": True,
            "schema_id": "neo.memory.core_user_profile.history.aci6.v1",
            "status": "ready",
            "profile_key": key,
            "count": min(len(items), max(1, min(int(limit or 50), 200))),
            "timeline": items[: max(1, min(int(limit or 50), 200))],
        }

    def render_context(self, snapshot: dict[str, Any] | None = None, *, max_chars: int = 2800) -> str:
        payload = snapshot if isinstance(snapshot, dict) else self.snapshot()
        return render_core_user_profile_context(payload, max_chars=max_chars)

    def status(self) -> dict[str, Any]:
        snapshot = self.snapshot(limit=12, include_history=True)
        return {
            "ok": True,
            "schema_id": "neo.memory.core_user_profile.status.aci6.v1",
            "status": snapshot.get("status") or "ready",
            "version": CORE_USER_PROFILE_VERSION,
            "current_count": snapshot.get("current_count", 0),
            "history_count": snapshot.get("history_count", 0),
            "conflict_count": snapshot.get("conflict_count", 0),
            "source_authority": "sqlite_neo_memory_facts",
            "updated_at": _now(),
        }


def core_user_profile_snapshot(payload: dict[str, Any] | None = None, *, db_path: Path | None = None) -> dict[str, Any]:
    data = payload or {}
    return CoreUserProfileService(db_path or DEFAULT_DB_PATH).snapshot(
        scope_class=str(data.get("scope_class") or "general_federated"),
        allow_project_profile=bool(data.get("allow_project_profile", False)),
        as_of=str(data.get("as_of") or ""),
        limit=int(data.get("limit") or 12),
        include_history=bool(data.get("include_history", True)),
    )
