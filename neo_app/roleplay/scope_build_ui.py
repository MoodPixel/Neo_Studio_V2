from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.forge import HIERARCHY, list_forge_records
from neo_app.roleplay.runtime import runtime_compile_payload, runtime_state_payload
from neo_app.roleplay.scoped_compile_plan import build_compile_plan_payload, execute_compile_plan_payload, ensure_scoped_compile_schema
from neo_app.roleplay.sandbox_contract import derive_sandbox_context, context_scope_id
from neo_app.roleplay.sqlite_store import _connect, _json
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT

PHASE186_SCHEMA_ID = "neo.roleplay.scope_build_ui.v1"
PHASE186_VERSION = "1.1.1-phase18.10e-file-backed-scope-discovery"
PHASE186_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "scope_build_contract.json"
PHASE186_STATE_PATH = ROLEPLAY_DATA_ROOT / "scope_build_state.json"

_SCOPE_BUILD_KINDS = ("project", "universe", "world", "region", "city", "location", "scenario")
_SCOPE_LABELS = {
    "project": "Project",
    "universe": "Universe",
    "world": "World",
    "region": "Region / Kingdom",
    "city": "City / Settlement",
    "location": "Location",
    "scenario": "Scenario",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _stable_id(*parts: Any, prefix: str = "scope-build") -> str:
    base = "|".join(_clean(part) for part in parts)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=8).hexdigest()
    return f"{prefix}:{digest}"


def ensure_scope_build_schema() -> dict[str, Any]:
    ensure_scoped_compile_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_scope_build_runs (
                build_run_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                linked_record_count INTEGER NOT NULL DEFAULT 0,
                compile_run_id TEXT NOT NULL DEFAULT '',
                compiled_count INTEGER NOT NULL DEFAULT 0,
                fragment_count INTEGER NOT NULL DEFAULT 0,
                runtime_bundle_id TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL DEFAULT '{}',
                preview_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_rp_scope_build_runs_scope ON rp_scope_build_runs(scope_type, scope_id, started_at);
            """
        )
        conn.commit()
    return {"status": "ready", "table": "rp_scope_build_runs"}


def _records_from_live_forge_api() -> list[dict[str, Any]]:
    try:
        return [model_to_dict(record) for record in list_forge_records(None)]
    except Exception:
        return []


def _normalise_scope_kind(value: Any) -> str:
    clean = _clean(value).lower().replace(" ", "_").replace("/", "_")
    aliases = {
        "region_kingdom": "region",
        "city_settlement": "city",
        "legend_canon": "legend",
        "lore": "legend",
        "canon": "legend",
        "locations": "location",
        "characters": "character",
        "organizations": "organization",
        "artifacts": "artifact",
        "relationships": "relationship",
    }
    return aliases.get(clean, clean) or "record"


def _coerce_disk_record(raw: dict[str, Any], path: Any = "") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    # Normal Forge wrapper shape.
    if isinstance(raw.get("payload"), dict):
        payload = raw.get("payload") or {}
        rid = _clean(raw.get("record_id") or payload.get("id") or Path(str(path)).stem)
        kind = _normalise_scope_kind(raw.get("kind") or payload.get("kind") or Path(str(path)).parent.name)
        return {
            "record_id": rid,
            "kind": kind,
            "title": _clean(raw.get("title") or payload.get("display_label") or payload.get("label") or rid),
            "body": _clean(raw.get("body") or payload.get("summary")),
            "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else payload.get("tags", []),
            "payload": payload,
            "markdown": _clean(raw.get("markdown")),
            "created_at": _clean(raw.get("created_at") or (payload.get("meta") or {}).get("created_at")),
            "updated_at": _clean(raw.get("updated_at") or (payload.get("meta") or {}).get("updated_at")),
            "storage_path": _clean(raw.get("storage_path") or str(path)),
        }
    # Raw Builder payload shape, useful when imports or old files landed without wrapper metadata.
    if raw.get("kind") or raw.get("fields") or raw.get("links"):
        kind = _normalise_scope_kind(raw.get("kind") or Path(str(path)).parent.name)
        rid = _clean(raw.get("id") or Path(str(path)).stem)
        return {
            "record_id": rid,
            "kind": kind,
            "title": _clean(raw.get("display_label") or raw.get("label") or rid),
            "body": _clean(raw.get("summary")),
            "tags": raw.get("tags", []) if isinstance(raw.get("tags"), list) else [],
            "payload": raw,
            "markdown": "",
            "created_at": _clean((raw.get("meta") or {}).get("created_at") if isinstance(raw.get("meta"), dict) else ""),
            "updated_at": _clean((raw.get("meta") or {}).get("updated_at") if isinstance(raw.get("meta"), dict) else ""),
            "storage_path": str(path),
        }
    return None


def _records_from_disk_fallback() -> list[dict[str, Any]]:
    root = ROLEPLAY_DATA_ROOT / "entities"
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in sorted(root.glob("*/*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        record = _coerce_disk_record(raw, path)
        if record:
            records.append(record)
    return records


def _records() -> list[dict[str, Any]]:
    # Phase 18.10E: Compile scope discovery must be file-backed. Newly imported Forge records
    # can exist before they are compiled or synced into memory tables, so do not depend on
    # compiled entities/vector rows. Merge the live Forge API with a tolerant disk scan.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for record in _records_from_disk_fallback() + _records_from_live_forge_api():
        rid = _record_id(record) if isinstance(record, dict) else ""
        kind = _record_kind(record) if isinstance(record, dict) else ""
        if not rid:
            continue
        merged[(kind, rid)] = record
    return list(merged.values())


def scope_discovery_debug_payload() -> dict[str, Any]:
    api_records = _records_from_live_forge_api()
    disk_records = _records_from_disk_fallback()
    merged = _records()
    counts: dict[str, int] = {}
    for record in merged:
        kind = _record_kind(record)
        counts[kind] = counts.get(kind, 0) + 1
    scope_counts = {kind: 0 for kind in _SCOPE_BUILD_KINDS}
    for record in merged:
        kind = _record_kind(record)
        if kind in scope_counts:
            scope_counts[kind] += 1
    return {
        "schema_id": "neo.roleplay.scope_build.discovery_debug.v1",
        "status": "ready",
        "api_record_count": len(api_records),
        "disk_record_count": len(disk_records),
        "merged_record_count": len(merged),
        "counts_by_kind": counts,
        "scope_counts_by_kind": scope_counts,
        "entities_root": str(ROLEPLAY_DATA_ROOT / "entities"),
    }


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("payload")
    return value if isinstance(value, dict) else record


def _record_id(record: dict[str, Any]) -> str:
    payload = _payload(record)
    return _clean(record.get("record_id") or payload.get("id"))


def _record_kind(record: dict[str, Any]) -> str:
    payload = _payload(record)
    return _clean(record.get("kind") or payload.get("kind") or "record").lower()


def _record_title(record: dict[str, Any]) -> str:
    payload = _payload(record)
    return _clean(record.get("title") or payload.get("display_label") or payload.get("label") or _record_id(record))


def _record_summary(record: dict[str, Any]) -> str:
    payload = _payload(record)
    return _clean(record.get("summary") or payload.get("summary"))[:260]


def _scope_context(record: dict[str, Any]) -> dict[str, Any]:
    rid = _record_id(record)
    return derive_sandbox_context(_payload(record), record_id=rid, kind=_record_kind(record))


def _record_compile_state_map() -> dict[str, dict[str, Any]]:
    ensure_scope_build_schema()
    with _connect() as conn:
        try:
            rows = [dict(row) for row in conn.execute("SELECT * FROM rp_record_compile_state").fetchall()]
        except sqlite3.Error:
            rows = []
    return {_clean(row.get("record_id")): row for row in rows if _clean(row.get("record_id"))}


def _add_scalar_refs(value: Any, known_ids: set[str], refs: set[str]) -> None:
    if isinstance(value, str):
        clean = value.strip()
        if clean in known_ids:
            refs.add(clean)
        return
    if isinstance(value, list):
        for item in value:
            _add_scalar_refs(item, known_ids, refs)
        return
    if isinstance(value, dict):
        for child in value.values():
            _add_scalar_refs(child, known_ids, refs)


def _record_refs(record: dict[str, Any], known_ids: set[str]) -> set[str]:
    payload = _payload(record)
    refs: set[str] = set()
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    _add_scalar_refs(links.get("scope") or {}, known_ids, refs)
    _add_scalar_refs(links.get("related") or {}, known_ids, refs)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    # Relationship participants, scenario cast, and other common nested ID sections are intentionally scanned.
    for key in ("participants", "cast_roles_pov", "placement_scene_anchor", "identity", "current_state"):
        if key in fields:
            _add_scalar_refs(fields.get(key), known_ids, refs)
    # A light full-payload scan catches IDs buried in custom fields without turning prose into links.
    _add_scalar_refs(payload.get("memory_hints") or {}, known_ids, refs)
    refs.discard(_record_id(record))
    return refs


def _record_card(record: dict[str, Any], state_map: dict[str, dict[str, Any]] | None = None, *, distance: int = 0, link_reason: str = "") -> dict[str, Any]:
    rid = _record_id(record)
    kind = _record_kind(record)
    context = _scope_context(record)
    state = (state_map or {}).get(rid) or {}
    return {
        "record_id": rid,
        "kind": kind,
        "kind_label": HIERARCHY.get(kind, {}).get("display_name") or kind.replace("_", " ").title(),
        "title": _record_title(record),
        "summary": _record_summary(record),
        "scope_id": context_scope_id(context),
        "project_id": _clean(context.get("project_id")),
        "sandbox_id": _clean(context.get("sandbox_id")),
        "universe_id": _clean(context.get("universe_id")),
        "world_id": _clean(context.get("world_id")),
        "region_id": _clean(context.get("region_id")),
        "city_id": _clean(context.get("city_id")),
        "location_id": _clean(context.get("location_id")),
        "compile_status": _clean(state.get("compile_status") or "new"),
        "compiled_fragment_count": _safe_int(state.get("compiled_fragment_count"), 0),
        "last_compiled_at": _clean(state.get("last_compiled_at")),
        "distance": distance,
        "link_reason": link_reason or ("selected scope" if distance == 0 else f"linked depth {distance}"),
    }


def _scope_matches_card(card: dict[str, Any], scope_type: str, scope_id: str) -> bool:
    if not scope_id:
        return False
    scope_type = _clean(scope_type).lower()
    if scope_type in {"scope", "sandbox"}:
        return scope_id in {_clean(card.get("scope_id")), _clean(card.get("sandbox_id"))}
    key = scope_type if scope_type.endswith("_id") else f"{scope_type}_id"
    return _clean(card.get(key)) == scope_id or _clean(card.get("record_id")) == scope_id


def list_scope_build_options(limit: int = 400, scope_type: str | None = None) -> list[dict[str, Any]]:
    """Return browsable compile scopes from the live Forge record registry.

    Phase 18.10D: this must see newly imported Forge records before they are compiled.
    The Compile UI can request one scope level at a time, e.g. universe/world/scenario.
    """
    ensure_scope_build_schema()
    records = _records()
    state_map = _record_compile_state_map()
    wanted = _clean(scope_type).lower()
    if wanted in {"region / kingdom", "region_kingdom"}:
        wanted = "region"
    if wanted in {"city / settlement", "city_settlement"}:
        wanted = "city"
    if wanted in {"all", "any", ""}:
        wanted = ""
    options: list[dict[str, Any]] = []
    for record in records:
        kind = _record_kind(record)
        if kind not in _SCOPE_BUILD_KINDS:
            continue
        if wanted and kind != wanted:
            continue
        card = _record_card(record, state_map)
        label = f"{_SCOPE_LABELS.get(kind, kind.title())} — {card['title']}"
        options.append({
            "label": label,
            "scope_type": kind,
            "scope_id": card["record_id"],
            "record_id": card["record_id"],
            "record_kind": kind,
            "kind": kind,
            "kind_label": _SCOPE_LABELS.get(kind, kind.title()),
            "title": card["title"],
            "summary": card.get("summary") or "",
            "sandbox_id": card.get("sandbox_id") or card.get("scope_id") or "",
            "universe_id": card.get("universe_id") or "",
            "world_id": card.get("world_id") or "",
            "region_id": card.get("region_id") or "",
            "city_id": card.get("city_id") or "",
            "location_id": card.get("location_id") or "",
            "compile_status": card.get("compile_status") or "new",
        })
    scope_order = {kind: index for index, kind in enumerate(_SCOPE_BUILD_KINDS)}
    options.sort(key=lambda item: (scope_order.get(item.get("scope_type") or "", 99), item.get("label") or ""))
    return options[: max(1, min(limit, 2000))]


def scope_options_by_type(limit_per_type: int = 1000) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in _SCOPE_BUILD_KINDS:
        result[kind] = list_scope_build_options(limit=limit_per_type, scope_type=kind)
    return result

def scope_kind_counts() -> dict[str, int]:
    return {kind: len(items) for kind, items in scope_options_by_type(limit_per_type=5000).items()}

def linked_records_for_scope(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_scope_build_schema()
    payload = payload or {}
    scope_type = _clean(payload.get("scope_type") or "world").lower()
    scope_id = _clean(payload.get("scope_id") or payload.get("record_id") or "")
    graph_depth = max(0, min(_safe_int(payload.get("graph_depth"), 2), 5))
    include_reverse = payload.get("include_reverse_links", True) is not False
    include_scope_family = payload.get("include_scope_family", True) is not False
    records = _records()
    record_map = {_record_id(record): record for record in records if _record_id(record)}
    known_ids = set(record_map.keys())
    state_map = _record_compile_state_map()

    refs = {rid: _record_refs(record, known_ids) for rid, record in record_map.items()}
    reverse: dict[str, set[str]] = defaultdict(set)
    for rid, targets in refs.items():
        for target in targets:
            reverse[target].add(rid)

    seeds: set[str] = set()
    if scope_id in record_map:
        seeds.add(scope_id)
    if include_scope_family:
        for rid, record in record_map.items():
            card = _record_card(record, state_map)
            if _scope_matches_card(card, scope_type, scope_id):
                seeds.add(rid)

    queue: deque[tuple[str, int]] = deque((rid, 0) for rid in sorted(seeds))
    distances: dict[str, int] = {rid: 0 for rid in seeds}
    while queue:
        rid, distance = queue.popleft()
        if distance >= graph_depth:
            continue
        neighbors = set(refs.get(rid) or set())
        if include_reverse:
            neighbors |= reverse.get(rid, set())
        for neighbor in sorted(neighbors):
            if neighbor not in record_map:
                continue
            next_distance = distance + 1
            if neighbor not in distances or next_distance < distances[neighbor]:
                distances[neighbor] = next_distance
                queue.append((neighbor, next_distance))

    cards = [_record_card(record_map[rid], state_map, distance=distances.get(rid, 0)) for rid in sorted(distances, key=lambda item: (distances[item], _record_kind(record_map[item]), _record_title(record_map[item])))]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        groups[card["kind"]].append(card)
    group_payload = [{"kind": kind, "kind_label": HIERARCHY.get(kind, {}).get("display_name") or kind.title(), "count": len(items), "records": items} for kind, items in sorted(groups.items())]
    status_counts: dict[str, int] = defaultdict(int)
    for card in cards:
        status_counts[card.get("compile_status") or "new"] += 1
    return {
        "schema_id": "neo.roleplay.scope_build.linked_records.v1",
        "status": "ready" if cards else "empty",
        "scope_type": scope_type,
        "scope_id": scope_id,
        "graph_depth": graph_depth,
        "include_reverse_links": include_reverse,
        "linked_record_count": len(cards),
        "record_ids": [card["record_id"] for card in cards],
        "kind_counts": {group["kind"]: group["count"] for group in group_payload},
        "status_counts": dict(status_counts),
        "groups": group_payload,
        "records": cards,
    }


def preview_scope_build_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_scope_build_schema()
    payload = payload or {}
    linked = linked_records_for_scope(payload)
    compile_payload = {
        "mode": payload.get("mode") or "changed_only",
        "scope_type": "selected_records",
        "scope_id": linked.get("scope_id") or "",
        "record_ids": linked.get("record_ids") or [],
        "include_compiled": bool(payload.get("include_compiled", False)),
        "kind": _clean(payload.get("kind") or ""),
        "limit": _safe_int(payload.get("limit"), 5000),
    }
    plan = build_compile_plan_payload(compile_payload)
    runtime_record_ids = linked.get("record_ids") or []
    return {
        "schema_id": "neo.roleplay.scope_build.preview.v1",
        "status": "ready" if linked.get("linked_record_count") else "empty",
        "scope_type": linked.get("scope_type"),
        "scope_id": linked.get("scope_id"),
        "linked_records": linked,
        "compile_plan": plan,
        "runtime_plan": {
            "will_build_runtime_bundle": payload.get("build_runtime", True) is not False,
            "record_count": len(runtime_record_ids),
            "record_ids": runtime_record_ids,
            "kind_counts": linked.get("kind_counts") or {},
        },
        "safe_message": "Review linked records before building. Global/manual compile stays available under advanced controls only.",
    }


def build_scope_memory_runtime_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_scope_build_schema()
    payload = payload or {}
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else preview_scope_build_payload(payload)
    linked = preview.get("linked_records") or {}
    scope_type = _clean(preview.get("scope_type") or payload.get("scope_type"))
    scope_id = _clean(preview.get("scope_id") or payload.get("scope_id"))
    build_run_id = _stable_id(scope_type, scope_id, _now(), prefix="scope-build-run")
    started = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO rp_scope_build_runs(build_run_id, scope_type, scope_id, status, started_at, linked_record_count, preview_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (build_run_id, scope_type, scope_id, "running", started, int(linked.get("linked_record_count") or 0), _json(preview)),
        )
        conn.commit()

    compile_result: dict[str, Any] = {"status": "skipped", "compiled_count": 0, "fragment_count": 0}
    if payload.get("compile_memory", True) is not False:
        execute_payload = {
            "plan": preview.get("compile_plan") or {},
            "rebuild_search": payload.get("rebuild_search", True) is not False,
            "index_after": payload.get("index_after", True) is not False,
            "mirror_after": bool(payload.get("mirror_after", False)),
            "force_index": payload.get("force_index", True) is not False,
        }
        compile_result = execute_compile_plan_payload(execute_payload)

    runtime_result: dict[str, Any] = {"status": "skipped"}
    if payload.get("build_runtime", True) is not False:
        records = linked.get("records") or []
        record_ids = [card.get("record_id") for card in records if card.get("record_id")]
        kinds = sorted({card.get("kind") for card in records if card.get("kind")})
        selected_title = _clean(payload.get("bundle_title") or "")
        if not selected_title:
            selected = next((card for card in records if card.get("record_id") == scope_id), None) or (records[0] if records else {})
            selected_title = f"Runtime — {selected.get('title') or scope_id or 'Selected Scope'}"
        runtime_result = runtime_compile_payload({
            "title": selected_title,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "record_ids": record_ids,
            "include_kinds": kinds,
            "max_entities": max(len(record_ids), 1),
            "max_sources": _safe_int(payload.get("max_sources"), 24),
            "max_memory_fragments": _safe_int(payload.get("max_memory_fragments"), 160),
        })

    status = "built"
    if (compile_result.get("status") in {"failed", "partial"}) and runtime_result.get("status") == "skipped":
        status = "partial"
    finished = _now()
    summary = {
        "compile_status": compile_result.get("status"),
        "compile_run_id": compile_result.get("compile_run_id") or "",
        "compiled_count": compile_result.get("compiled_count") or 0,
        "fragment_count": compile_result.get("fragment_count") or 0,
        "runtime_status": runtime_result.get("status"),
        "runtime_bundle_id": (runtime_result.get("bundle") or {}).get("bundle_id") or runtime_result.get("bundle_id") or "",
        "linked_record_count": linked.get("linked_record_count") or 0,
    }
    with _connect() as conn:
        conn.execute(
            "UPDATE rp_scope_build_runs SET status=?, finished_at=?, compile_run_id=?, compiled_count=?, fragment_count=?, runtime_bundle_id=?, summary_json=? WHERE build_run_id=?",
            (status, finished, summary["compile_run_id"], int(summary["compiled_count"] or 0), int(summary["fragment_count"] or 0), summary["runtime_bundle_id"], _json(summary), build_run_id),
        )
        conn.commit()
    return {
        "schema_id": "neo.roleplay.scope_build.build.v1",
        "status": status,
        "build_run_id": build_run_id,
        "preview": preview,
        "compile_result": compile_result,
        "runtime_result": runtime_result,
        "summary": summary,
        "state": scope_build_state_payload(),
    }


def scope_build_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_scope_build_schema()
    scopes = list_scope_build_options()
    scopes_by_type = scope_options_by_type()
    scope_counts = {kind: len(items) for kind, items in scopes_by_type.items()}
    latest_runs: list[dict[str, Any]] = []
    with _connect() as conn:
        try:
            latest_runs = [dict(row) for row in conn.execute("SELECT build_run_id, scope_type, scope_id, status, started_at, finished_at, linked_record_count, compile_run_id, compiled_count, fragment_count, runtime_bundle_id FROM rp_scope_build_runs ORDER BY started_at DESC LIMIT 10").fetchall()]
        except sqlite3.Error:
            latest_runs = []
    payload = {
        "schema_id": "neo.roleplay.scope_build.state.v1",
        "version": PHASE186_VERSION,
        "status": "active",
        "scope_count": len(scopes),
        "scope_counts": scope_counts,
        "scopes": scopes,
        "scopes_by_type": scopes_by_type,
        "latest_runs": latest_runs,
        "runtime_state": runtime_state_payload(),
        "discovery_debug": scope_discovery_debug_payload(),
        "safe_defaults": {
            "mode": "changed_only",
            "graph_depth": 2,
            "include_reverse_links": True,
            "compile_memory": True,
            "rebuild_search": True,
            "index_after": True,
            "mirror_after": False,
            "build_runtime": True,
        },
    }
    if write_report:
        PHASE186_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE186_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def scope_build_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE186_SCHEMA_ID,
        "version": PHASE186_VERSION,
        "phase": "Phase 18.10E — File-backed Compile Scope Discovery Fix",
        "status": "implemented",
        "purpose": "Make Compile discover newly imported Forge records directly from Forge entity files before they are compiled or indexed.",
        "scope_selector": ["project", "universe", "world", "region", "city", "location", "scenario"],
        "graph_expansion": {
            "default_depth": 2,
            "includes_direct_scope_matches": True,
            "includes_links_related": True,
            "includes_reverse_links": True,
            "includes_neighbor_records": True,
        },
        "build_steps": ["discover linked records", "preview compile plan", "compile changed records", "rebuild search docs", "index vectors", "optional Chroma mirror", "compile scoped runtime bundle"],
        "endpoints": {
            "contract": "GET /api/roleplay/scope-build/contract",
            "state": "GET /api/roleplay/scope-build/state",
            "ensure_schema": "POST /api/roleplay/scope-build/ensure-schema",
            "scopes": "GET /api/roleplay/scope-build/scopes",
            "linked_records": "POST /api/roleplay/scope-build/linked-records",
            "preview": "POST /api/roleplay/scope-build/preview",
            "build": "POST /api/roleplay/scope-build/build",
        },
    }
    if write_report:
        PHASE186_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE186_CONTRACT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
