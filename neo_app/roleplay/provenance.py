from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from neo_app.roleplay.sqlite_store import ensure_roleplay_memory_schema, roleplay_sqlite_state_payload, _connect
from neo_app.roleplay.runtime import runtime_state_payload

SCHEMA_ID = "neo.roleplay.provenance.v1"
VERSION = "1.0.0-full-provenance-graph-ui"

NODE_TABLES: tuple[dict[str, str], ...] = (
    {"table": "rp_entities", "id": "entity_id", "title": "title", "type": "entity", "scope": "scope_id"},
    {"table": "rp_memory_fragments", "id": "fragment_id", "title": "memory_type", "type": "memory_fragment", "scope": "source_id"},
    {"table": "rp_shared_memories", "id": "memory_id", "title": "title", "type": "shared_memory", "scope": "scope_id"},
    {"table": "rp_continuity_rows", "id": "row_id", "title": "title", "type": "continuity_row", "scope": "scope_id"},
    {"table": "rp_turn_summaries", "id": "summary_id", "title": "turn_id", "type": "turn_summary", "scope": "scene_id"},
    {"table": "rp_story_checkpoints", "id": "checkpoint_id", "title": "title", "type": "story_checkpoint", "scope": "storyline_id"},
    {"table": "rp_retrieval_traces", "id": "trace_id", "title": "query", "type": "retrieval_trace", "scope": "scope_id"},
    {"table": "rp_contradiction_reports", "id": "report_id", "title": "title", "type": "contradiction_report", "scope": "scope_id"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _node_id(table: str, source_id: str) -> str:
    return f"{table}:{source_id}"


def _safe_limit(value: Any, *, default: int = 250, maximum: int = 2000) -> int:
    try:
        return max(1, min(int(value or default), maximum))
    except Exception:
        return default


def _fetch_table_nodes(conn: sqlite3.Connection, *, scope_id: str = "", node_type: str = "", limit: int = 250) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    clean_scope = str(scope_id or "").strip()
    clean_type = str(node_type or "").strip()
    lim = _safe_limit(limit)
    for spec in NODE_TABLES:
        if clean_type and spec["type"] != clean_type:
            continue
        table = spec["table"]
        id_key = spec["id"]
        title_key = spec["title"]
        scope_key = spec["scope"]
        try:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall()
        except Exception:
            continue
        for row in rows:
            data = dict(row)
            source_id = str(data.get(id_key) or "")
            if not source_id:
                continue
            payload_blob = "\n".join(str(data.get(key) or "") for key in data.keys())
            scope_value = str(data.get(scope_key) or data.get("source_id") or data.get("scene_id") or data.get("storyline_id") or data.get("session_id") or "")
            if clean_scope and clean_scope not in (scope_value, source_id) and clean_scope not in payload_blob:
                continue
            title = str(data.get(title_key) or data.get("summary") or data.get("content") or source_id)[:160]
            nodes.append({
                "id": _node_id(table, source_id),
                "source_table": table,
                "source_id": source_id,
                "type": spec["type"],
                "label": title or source_id,
                "scope_id": scope_value,
                "status": str(data.get("status") or data.get("vector_status") or "active"),
                "created_at": str(data.get("created_at") or data.get("indexed_at") or ""),
                "updated_at": str(data.get("updated_at") or data.get("indexed_at") or data.get("created_at") or ""),
                "payload_preview": payload_blob[:700],
            })
            if len(nodes) >= lim:
                return nodes
    return nodes[:lim]


def _add_edge(edges: list[dict[str, Any]], source: str, target: str, edge_type: str, label: str = "", weight: float = 1.0, evidence: dict[str, Any] | None = None) -> None:
    if not source or not target or source == target:
        return
    edge_id = f"{source}->{edge_type}->{target}"
    if any(edge.get("id") == edge_id for edge in edges):
        return
    edges.append({"id": edge_id, "source": source, "target": target, "type": edge_type, "label": label or edge_type, "weight": weight, "evidence": evidence or {}})


def _build_edges(conn: sqlite3.Connection, node_ids: set[str], *, limit: int = 800) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    lim = _safe_limit(limit, default=800, maximum=5000)

    def has(table: str, source_id: str) -> bool:
        return _node_id(table, source_id) in node_ids

    # Explicit Forge graph edges.
    try:
        for row in conn.execute("SELECT source_id, target_id, relation_type, payload_json FROM rp_edges ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall():
            data = dict(row)
            source_id = str(data.get("source_id") or "")
            target_id = str(data.get("target_id") or "")
            source_node = _node_id("rp_entities", source_id)
            target_node = _node_id("rp_entities", target_id)
            if source_node in node_ids and target_node in node_ids:
                _add_edge(edges, source_node, target_node, "linked_to", str(data.get("relation_type") or "linked_to"), evidence={"source": "rp_edges", "payload": _read_json(data.get("payload_json"), {})})
    except Exception:
        pass

    # Memory fragments generated from source rows.
    try:
        for row in conn.execute("SELECT fragment_id, source_type, source_id, payload_json FROM rp_memory_fragments ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall():
            data = dict(row)
            fragment_node = _node_id("rp_memory_fragments", str(data.get("fragment_id") or ""))
            source_id = str(data.get("source_id") or "")
            source_type = str(data.get("source_type") or "")
            source_table = "rp_entities" if source_type in {"forge_record", "entity", "character", "world", "location"} else ""
            if not source_table and has("rp_continuity_rows", source_id):
                source_table = "rp_continuity_rows"
            if not source_table and has("rp_turn_summaries", source_id):
                source_table = "rp_turn_summaries"
            if source_table:
                source_node = _node_id(source_table, source_id)
                if fragment_node in node_ids and source_node in node_ids:
                    _add_edge(edges, source_node, fragment_node, "generated_memory", "generated memory", evidence={"source_type": source_type})
    except Exception:
        pass

    # Retrieval traces link to returned rows.
    try:
        for row in conn.execute("SELECT trace_id, results_json FROM rp_retrieval_traces ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall():
            trace_id = str(row["trace_id"] or "")
            trace_node = _node_id("rp_retrieval_traces", trace_id)
            if trace_node not in node_ids:
                continue
            results = _read_json(row["results_json"], [])
            if isinstance(results, dict):
                results = results.get("results", [])
            for result in results[:50] if isinstance(results, list) else []:
                table = str(result.get("source_table") or result.get("table") or "")
                source_id = str(result.get("source_id") or result.get("id") or result.get("entity_id") or "")
                if table and source_id and _node_id(table, source_id) in node_ids:
                    _add_edge(edges, trace_node, _node_id(table, source_id), "retrieved", "retrieved", evidence={"score": result.get("score")})
    except Exception:
        pass

    # Checkpoints include scene transcript/setup data and sometimes turn ids.
    try:
        for row in conn.execute("SELECT checkpoint_id, session_id, storyline_id, payload_json FROM rp_story_checkpoints ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall():
            data = dict(row)
            checkpoint_node = _node_id("rp_story_checkpoints", str(data.get("checkpoint_id") or ""))
            if checkpoint_node not in node_ids:
                continue
            payload = _read_json(data.get("payload_json"), {})
            for key in ("session_id", "storyline_id"):
                value = str(data.get(key) or payload.get(key) or "")
                if value and has("rp_continuity_rows", value):
                    _add_edge(edges, _node_id("rp_continuity_rows", value), checkpoint_node, "checkpointed_from", "checkpointed from")
            transcript = payload.get("transcript") or payload.get("scene_transcript") or []
            if isinstance(transcript, list):
                for turn in transcript[-20:]:
                    turn_id = str(turn.get("turn_id") or turn.get("id") or "") if isinstance(turn, dict) else ""
                    if turn_id and has("rp_turn_summaries", turn_id):
                        _add_edge(edges, _node_id("rp_turn_summaries", turn_id), checkpoint_node, "checkpointed_from", "checkpointed from")
    except Exception:
        pass

    # Contradiction reports link to conflicting records.
    try:
        for row in conn.execute("SELECT report_id, source_a_table, source_a_id, source_b_table, source_b_id FROM rp_contradiction_reports ORDER BY rowid DESC LIMIT ?", (lim,)).fetchall():
            data = dict(row)
            report_node = _node_id("rp_contradiction_reports", str(data.get("report_id") or ""))
            if report_node not in node_ids:
                continue
            for table_key, id_key in (("source_a_table", "source_a_id"), ("source_b_table", "source_b_id")):
                table = str(data.get(table_key) or "")
                source_id = str(data.get(id_key) or "")
                target = _node_id(table, source_id)
                if target in node_ids:
                    _add_edge(edges, report_node, target, "conflicts_with", "conflicts with")
    except Exception:
        pass

    return edges[:lim]


def _runtime_bundle_nodes_edges(scope_id: str = "", limit: int = 100) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    try:
        runtime = runtime_state_payload(limit=limit)
        for bundle in runtime.get("bundles", [])[:limit]:
            bundle_id = str(bundle.get("bundle_id") or "")
            if not bundle_id:
                continue
            blob = json.dumps(bundle, ensure_ascii=False)
            if scope_id and scope_id not in bundle_id and scope_id not in blob:
                continue
            node_id = f"runtime_bundle:{bundle_id}"
            nodes.append({
                "id": node_id,
                "source_table": "runtime_bundle",
                "source_id": bundle_id,
                "type": "runtime_bundle",
                "label": str(bundle.get("title") or bundle_id),
                "scope_id": str(bundle.get("project_id") or ""),
                "status": str(bundle.get("status") or "compiled"),
                "created_at": str(bundle.get("created_at") or ""),
                "updated_at": str(bundle.get("updated_at") or bundle.get("created_at") or ""),
                "payload_preview": blob[:700],
            })
            for entity in bundle.get("included_entities", [])[:80] if isinstance(bundle.get("included_entities"), list) else []:
                entity_id = str(entity.get("entity_id") or entity.get("source_id") or "") if isinstance(entity, dict) else str(entity or "")
                if entity_id:
                    edges.append({"id": f"rp_entities:{entity_id}->included_in_runtime->{node_id}", "source": f"rp_entities:{entity_id}", "target": node_id, "type": "included_in_runtime", "label": "included in runtime", "weight": 1.0, "evidence": {"bundle_id": bundle_id}})
            for fragment in bundle.get("included_memory_fragments", [])[:80] if isinstance(bundle.get("included_memory_fragments"), list) else []:
                fragment_id = str(fragment.get("fragment_id") or fragment.get("source_id") or "") if isinstance(fragment, dict) else str(fragment or "")
                if fragment_id:
                    edges.append({"id": f"rp_memory_fragments:{fragment_id}->included_in_runtime->{node_id}", "source": f"rp_memory_fragments:{fragment_id}", "target": node_id, "type": "included_in_runtime", "label": "included in runtime", "weight": 1.0, "evidence": {"bundle_id": bundle_id}})
    except Exception:
        return [], []
    return nodes, edges


def provenance_graph_payload(*, scope_id: str = "", node_type: str = "", limit: int = 250, include_runtime: bool = True) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    with _connect() as conn:
        nodes = _fetch_table_nodes(conn, scope_id=scope_id, node_type=node_type, limit=limit)
        if include_runtime and (not node_type or node_type == "runtime_bundle"):
            runtime_nodes, runtime_edges = _runtime_bundle_nodes_edges(scope_id=scope_id, limit=min(_safe_limit(limit), 100))
            nodes.extend(runtime_nodes)
        node_ids = {str(node.get("id") or "") for node in nodes}
        edges = _build_edges(conn, node_ids, limit=max(500, _safe_limit(limit) * 4))
        if include_runtime:
            _, runtime_edges = _runtime_bundle_nodes_edges(scope_id=scope_id, limit=min(_safe_limit(limit), 100))
            edges.extend([edge for edge in runtime_edges if edge.get("source") in node_ids and edge.get("target") in node_ids])
    counts_by_type: dict[str, int] = {}
    for node in nodes:
        counts_by_type[str(node.get("type") or "unknown")] = counts_by_type.get(str(node.get("type") or "unknown"), 0) + 1
    edge_counts: dict[str, int] = {}
    for edge in edges:
        edge_counts[str(edge.get("type") or "unknown")] = edge_counts.get(str(edge.get("type") or "unknown"), 0) + 1
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "surface_id": "roleplay",
        "status": "active",
        "ready": True,
        "scope_id": scope_id,
        "node_type": node_type,
        "counts": {"nodes": len(nodes), "edges": len(edges), "by_type": counts_by_type, "edge_types": edge_counts},
        "nodes": nodes,
        "edges": edges,
        "filters": {"node_types": [spec["type"] for spec in NODE_TABLES] + ["runtime_bundle"], "edge_types": sorted(edge_counts.keys() or ["generated_memory", "retrieved", "checkpointed_from", "included_in_runtime", "conflicts_with", "linked_to"])},
        "active_features": ["provenance_graph_nodes", "connected_edge_map", "trace_selected_record", "runtime_bundle_inclusion_edges", "retrieval_trace_edges", "contradiction_report_edges"],
    }


def provenance_state_payload() -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    sqlite_state = roleplay_sqlite_state_payload()
    graph = provenance_graph_payload(limit=80)
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "surface_id": "roleplay",
        "status": "active",
        "ready": True,
        "sqlite": sqlite_state,
        "graph_summary": graph.get("counts", {}),
        "endpoints": {
            "state": "/api/roleplay/provenance/state",
            "graph": "/api/roleplay/provenance/graph",
            "trace": "/api/roleplay/provenance/trace",
        },
        "active_features": graph.get("active_features", []),
    }


def provenance_trace_payload(*, source_table: str = "", source_id: str = "", node_id: str = "") -> dict[str, Any]:
    source_table = str(source_table or "").strip()
    source_id = str(source_id or "").strip()
    node_id = str(node_id or "").strip()
    if node_id and ":" in node_id and not source_table:
        source_table, source_id = node_id.split(":", 1)
    target_node_id = node_id or _node_id(source_table, source_id)
    graph = provenance_graph_payload(limit=1000, include_runtime=True)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    direct = next((node for node in nodes if node.get("id") == target_node_id), None)
    incoming = [edge for edge in edges if edge.get("target") == target_node_id]
    outgoing = [edge for edge in edges if edge.get("source") == target_node_id]
    related_ids = {edge.get("source") for edge in incoming} | {edge.get("target") for edge in outgoing}
    related = [node for node in nodes if node.get("id") in related_ids]
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "found" if direct else "not_found",
        "ready": True,
        "node_id": target_node_id,
        "source_table": source_table,
        "source_id": source_id,
        "direct_node": direct,
        "incoming": incoming,
        "outgoing": outgoing,
        "related_nodes": related[:100],
        "counts": {"incoming": len(incoming), "outgoing": len(outgoing), "related_nodes": len(related)},
        "created_at": _now(),
    }
