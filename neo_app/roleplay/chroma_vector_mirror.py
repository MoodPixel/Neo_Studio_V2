from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.admin.engine import VECTOR_STORE_DIR, admin_engine_state_payload, ensure_admin_engine_foundation, ROOT_DIR
from neo_app.roleplay.embedding_reranker_adapter import index_roleplay_search_documents_payload
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema, rebuild_roleplay_memory_search_documents
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

PHASE9_SCHEMA_ID: Final[str] = "neo.roleplay.memory.chroma_vector_mirror.v1"
PHASE9_VERSION: Final[str] = "1.0.0-phase9-chroma-vector-mirror"
PHASE9_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "chroma_vector_mirror_contract.json"
PHASE9_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "chroma_vector_mirror_state.json"
PHASE9_MANIFEST_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "chroma_vector_mirror_manifest.json"

ROLEPLAY_COLLECTIONS: Final[dict[str, str]] = {
    "fragments": "neo_roleplay_fragments",
    "entities": "neo_roleplay_entities",
    "scene_packets": "neo_roleplay_scene_packets",
    "novel_canon": "neo_novel_canon_chunks",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_int(value: Any, default: int = 500, minimum: int = 1, maximum: int = 20000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _resolve_under_root(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    raw = Path(str(value))
    if not raw.is_absolute():
        raw = ROOT_DIR / raw
    return raw.resolve()


def _vector_root() -> Path:
    ensure_admin_engine_foundation()
    engine = admin_engine_state_payload()
    vector = engine.get("vector_store") or {}
    root = _resolve_under_root(vector.get("persist_path") or vector.get("root"), VECTOR_STORE_DIR)
    return root


def _chroma_client(root: Path) -> tuple[Any | None, dict[str, Any]]:
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return None, {
            "available": False,
            "status": "chromadb_unavailable",
            "error": str(exc),
            "install_hint": "Install chromadb only if you want the optional Chroma mirror. SQLite remains the source of truth.",
        }
    try:
        root.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(root))
        return client, {"available": True, "status": "ready", "persist_path": _relative_to_root(root)}
    except Exception as exc:
        return None, {"available": False, "status": "chroma_client_error", "error": str(exc), "persist_path": _relative_to_root(root)}


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _parse_embedding(raw: Any) -> list[float]:
    if raw is None:
        return []
    try:
        data = json.loads(str(raw)) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[float] = []
    for item in data:
        try:
            out.append(float(item))
        except Exception:
            continue
    return out


def _parse_payload(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(raw or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _collection_key_for_row(row: dict[str, Any]) -> str:
    source_table = str(row.get("source_table") or "")
    source_type = str(row.get("source_type") or "")
    payload = _parse_payload(row.get("payload_json"))
    payload_source_table = str(payload.get("source_table") or payload.get("source_kind") or "")
    memory_scope = str(payload.get("memory_scope") or "")
    combined = " ".join([source_table, source_type, payload_source_table, memory_scope]).lower()
    if "scene_packet" in combined:
        return "scene_packets"
    if "source_chunk" in combined or "canon" in combined or "novel" in combined or "source_document" in combined:
        return "novel_canon"
    if "entity" in combined:
        return "entities"
    return "fragments"


def _read_vector_rows(conn: sqlite3.Connection, *, scope_id: str = "", limit: int = 500, force: bool = False) -> list[dict[str, Any]]:
    clean_scope = str(scope_id or "").strip()
    lim = _safe_int(limit, default=500)
    existing_filter = "" if force else "AND COALESCE(chroma_status, '') != 'mirrored'"
    sql = f"""
        SELECT index_id, source_table, source_id, source_type, scope_id, title, content,
               embedding_json, embedding_dimension, model_id, vector_status, payload_json, indexed_at,
               chroma_collection, chroma_status, chroma_synced_at
        FROM rp_vector_index
        WHERE vector_status = 'indexed'
          AND (? = '' OR scope_id = ? OR source_id = ? OR payload_json LIKE ?)
          {existing_filter}
        ORDER BY indexed_at DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        emb = _parse_embedding(data.get("embedding_json"))
        if not emb:
            continue
        data["embedding"] = emb
        data["collection_key"] = _collection_key_for_row(data)
        out.append(data)
    return out


def ensure_roleplay_chroma_mirror_schema() -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    with _connect() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(rp_vector_index)").fetchall()}
        additions = {
            "chroma_collection": "TEXT DEFAULT ''",
            "chroma_id": "TEXT DEFAULT ''",
            "chroma_status": "TEXT DEFAULT 'not_mirrored'",
            "chroma_synced_at": "TEXT DEFAULT ''",
            "chroma_payload_json": "TEXT DEFAULT '{}'",
        }
        added: list[str] = []
        for column, ddl in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE rp_vector_index ADD COLUMN {column} {ddl}")
                added.append(column)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rp_chroma_mirror_log (
                log_id TEXT PRIMARY KEY,
                collection_name TEXT DEFAULT '',
                action TEXT DEFAULT '',
                status TEXT DEFAULT '',
                scope_id TEXT DEFAULT '',
                mirrored_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
        counts = {"vector_index": _table_count(conn, "rp_vector_index"), "mirror_log": _table_count(conn, "rp_chroma_mirror_log")}
    return {
        "schema_id": "neo.roleplay.chroma_mirror.ensure_schema.v1",
        "status": "ready",
        "added_columns": added,
        "counts": counts,
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
    }


def roleplay_chroma_mirror_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_result = ensure_roleplay_chroma_mirror_schema()
    root = _vector_root()
    client, chroma = _chroma_client(root)
    collection_states: list[dict[str, Any]] = []
    if client is not None:
        for key, name in ROLEPLAY_COLLECTIONS.items():
            try:
                collection = client.get_or_create_collection(name=name, metadata={"owner": "roleplay", "phase": "phase9", "collection_key": key})
                try:
                    count = int(collection.count())
                except Exception:
                    count = 0
                collection_states.append({"key": key, "name": name, "status": "ready", "count": count})
            except Exception as exc:
                collection_states.append({"key": key, "name": name, "status": "error", "error": str(exc)})
    else:
        collection_states = [{"key": key, "name": name, "status": "unavailable"} for key, name in ROLEPLAY_COLLECTIONS.items()]
    with _connect() as conn:
        counts = {
            "search_documents": _table_count(conn, "rp_memory_search_documents"),
            "vector_index": _table_count(conn, "rp_vector_index"),
            "mirrored_vectors": int(conn.execute("SELECT COUNT(*) FROM rp_vector_index WHERE chroma_status = 'mirrored'").fetchone()[0]) if _table_count(conn, "rp_vector_index") >= 0 else 0,
            "mirror_log": _table_count(conn, "rp_chroma_mirror_log"),
        }
    payload = {
        "schema_id": PHASE9_SCHEMA_ID,
        "contract_version": PHASE9_VERSION,
        "status": "ready" if chroma.get("available") else "sqlite_only",
        "checked_at": _now(),
        "sqlite_source_of_truth": True,
        "chroma_is_optional_mirror": True,
        "paths": {"sqlite": _relative_to_root(ROLEPLAY_SQLITE_PATH), "chroma_persist_root": _relative_to_root(root)},
        "collections": collection_states,
        "collection_names": ROLEPLAY_COLLECTIONS,
        "chroma": chroma,
        "counts": counts,
        "schema": ensure_result,
    }
    if write_report:
        PHASE9_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE9_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def roleplay_chroma_mirror_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    contract = {
        "schema_id": PHASE9_SCHEMA_ID,
        "contract_version": PHASE9_VERSION,
        "phase": "Phase 9 — Chroma Vector Mirror",
        "generated_at": _now(),
        "hard_rules": [
            "SQLite remains the authoritative Roleplay / Novel memory store.",
            "Chroma is an optional semantic mirror and must never be required for Scene Chat to function.",
            "If Chroma is unavailable, retrieval must fall back to SQLite vector / lexical search.",
            "Roleplay collection names are fixed so exports/imports remain stable.",
        ],
        "collections": ROLEPLAY_COLLECTIONS,
        "pipeline": [
            "Compile Builder / Source / Canon memory",
            "Rebuild rp_memory_search_documents",
            "Index search documents into rp_vector_index through Phase 8",
            "Mirror rp_vector_index rows into Chroma collections",
            "Keep chroma_status/chroma_collection/chroma_synced_at on rp_vector_index for diagnostics",
        ],
        "endpoints": {
            "contract": "/api/roleplay/chroma-mirror/contract",
            "state": "/api/roleplay/chroma-mirror/state",
            "ensure_schema": "/api/roleplay/chroma-mirror/ensure-schema",
            "mirror": "/api/roleplay/chroma-mirror/mirror",
            "reset_status": "/api/roleplay/chroma-mirror/reset-status",
        },
        "state": roleplay_chroma_mirror_state_payload(write_report=False),
        "next_required_phase": "Phase 10 — Runtime Retrieval Lane",
    }
    if write_report:
        PHASE9_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE9_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract


def mirror_roleplay_vectors_to_chroma_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    ensure_roleplay_chroma_mirror_schema()
    if bool(data.get("rebuild_search", False)):
        rebuild_roleplay_memory_search_documents()
    if bool(data.get("index_first", True)):
        index_roleplay_search_documents_payload({
            "scope_id": data.get("scope_id") or data.get("scope") or "",
            "limit": data.get("index_limit") or data.get("limit") or 500,
            "force": bool(data.get("force_index", False)),
            "rebuild_search": bool(data.get("rebuild_search", False)),
            "allow_fallback": bool(data.get("allow_fallback", True)),
        })

    root = _vector_root()
    client, chroma = _chroma_client(root)
    scope_id = str(data.get("scope_id") or data.get("scope") or "").strip()
    limit = _safe_int(data.get("limit"), default=500)
    force = bool(data.get("force", False))
    now = _now()
    if client is None:
        return {
            "schema_id": "neo.roleplay.chroma_mirror.mirror.v1",
            "status": "sqlite_only_chroma_unavailable",
            "scope_id": scope_id,
            "mirrored_count": 0,
            "chroma": chroma,
            "state": roleplay_chroma_mirror_state_payload(write_report=True),
        }

    mirrored: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = _read_vector_rows(conn, scope_id=scope_id, limit=limit, force=force)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("collection_key") or "fragments"), []).append(row)
        for key, items in grouped.items():
            collection_name = ROLEPLAY_COLLECTIONS.get(key, ROLEPLAY_COLLECTIONS["fragments"])
            try:
                collection = client.get_or_create_collection(name=collection_name, metadata={"owner": "roleplay", "phase": "phase9", "collection_key": key})
                ids = [str(item.get("index_id")) for item in items]
                docs = [str(item.get("content") or item.get("title") or item.get("source_id") or "") for item in items]
                embeds = [item.get("embedding") or [] for item in items]
                metadatas = []
                for item in items:
                    meta = {
                        "source_table": str(item.get("source_table") or ""),
                        "source_id": str(item.get("source_id") or ""),
                        "source_type": str(item.get("source_type") or ""),
                        "scope_id": str(item.get("scope_id") or ""),
                        "title": str(item.get("title") or ""),
                        "model_id": str(item.get("model_id") or ""),
                        "indexed_at": str(item.get("indexed_at") or ""),
                    }
                    # Chroma metadata values must be scalar.
                    metadatas.append(meta)
                collection.upsert(ids=ids, documents=docs, embeddings=embeds, metadatas=metadatas)
                for item in items:
                    conn.execute(
                        """
                        UPDATE rp_vector_index
                        SET chroma_collection = ?, chroma_id = ?, chroma_status = 'mirrored', chroma_synced_at = ?, chroma_payload_json = ?
                        WHERE index_id = ?
                        """,
                        (
                            collection_name,
                            str(item.get("index_id") or ""),
                            now,
                            _json({"collection_key": key, "collection_name": collection_name, "phase9_version": PHASE9_VERSION}),
                            str(item.get("index_id") or ""),
                        ),
                    )
                    mirrored.append({"index_id": item.get("index_id"), "collection": collection_name, "scope_id": item.get("scope_id")})
            except Exception as exc:
                errors.append({"collection_key": key, "collection": collection_name, "error": str(exc), "count": len(items)})
        log_id = f"chroma_mirror:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        conn.execute(
            """
            INSERT INTO rp_chroma_mirror_log(log_id, collection_name, action, status, scope_id, mirrored_count, skipped_count, error, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                "all_roleplay_collections",
                "mirror_vectors",
                "mirrored_with_errors" if errors else "mirrored",
                scope_id,
                len(mirrored),
                max(0, len(rows) - len(mirrored)),
                json.dumps(errors, ensure_ascii=False) if errors else "",
                _json({"request": data, "collections": ROLEPLAY_COLLECTIONS, "errors": errors}),
                now,
            ),
        )
        conn.commit()
    manifest = {
        "schema_id": "neo.roleplay.chroma_mirror.manifest.v1",
        "phase": "Phase 9 — Chroma Vector Mirror",
        "updated_at": now,
        "scope_id": scope_id,
        "mirrored_count": len(mirrored),
        "errors": errors,
        "collections": ROLEPLAY_COLLECTIONS,
        "sqlite_source_of_truth": True,
        "chroma_persist_root": _relative_to_root(root),
    }
    PHASE9_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PHASE9_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "schema_id": "neo.roleplay.chroma_mirror.mirror.v1",
        "status": "mirrored_with_errors" if errors else "mirrored",
        "scope_id": scope_id,
        "mirrored_count": len(mirrored),
        "mirrored": mirrored[:100],
        "errors": errors,
        "manifest": manifest,
        "state": roleplay_chroma_mirror_state_payload(write_report=True),
    }


def reset_roleplay_chroma_mirror_status_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    scope_id = str(data.get("scope_id") or data.get("scope") or "").strip()
    with _connect() as conn:
        if scope_id:
            conn.execute(
                "UPDATE rp_vector_index SET chroma_status = 'not_mirrored', chroma_collection = '', chroma_id = '', chroma_synced_at = '', chroma_payload_json = '{}' WHERE scope_id = ? OR source_id = ? OR payload_json LIKE ?",
                (scope_id, scope_id, f"%{scope_id}%"),
            )
        else:
            conn.execute("UPDATE rp_vector_index SET chroma_status = 'not_mirrored', chroma_collection = '', chroma_id = '', chroma_synced_at = '', chroma_payload_json = '{}'")
        changed = conn.total_changes
        conn.commit()
    return {
        "schema_id": "neo.roleplay.chroma_mirror.reset_status.v1",
        "status": "reset",
        "scope_id": scope_id,
        "changed_rows": changed,
        "state": roleplay_chroma_mirror_state_payload(write_report=True),
    }
