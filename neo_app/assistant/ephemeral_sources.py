from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from neo_app.admin.semantic_engine import embed_texts, rerank_results
from neo_app.assistant.store import ASSISTANT_DATA_DIR, now_iso, write_json, read_json, slugify
from neo_app.knowledge.project_documents import build_contextual_fragments, extract_project_document
from neo_app.knowledge.media_source import prepare_media_source

EPHEMERAL_SOURCE_SCHEMA_ID = "neo.assistant.ephemeral_source.v1"
EPHEMERAL_RETRIEVAL_SCHEMA_ID = "neo.assistant.ephemeral_retrieval.v1"
MAX_EPHEMERAL_FRAGMENTS_PER_SOURCE = 256
MAX_EPHEMERAL_CANDIDATES = 24
DEFAULT_EPHEMERAL_TOP_K = 6
DEFAULT_EPHEMERAL_CONTEXT_CHARS = 14_000
EPHEMERAL_INDEX_DIR = ASSISTANT_DATA_DIR / "attachments" / "ephemeral_indexes"

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_'-]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    ln = math.sqrt(sum(a * a for a in left)) or 1.0
    rn = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (ln * rn)


def _lexical_score(query: str, text: str) -> float:
    q = _tokens(query)
    d = _tokens(text)
    if not q or not d:
        return 0.0
    overlap = len(q & d)
    phrase_bonus = 0.25 if str(query or "").strip().lower() in str(text or "").lower() else 0.0
    return min(1.0, (overlap / max(1, len(q))) + phrase_bonus)


def _bundle_dir_for_attachment(path: Path) -> Path:
    folder = path.parent / ".ephemeral_knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def bundle_path_for_attachment(path: Path, attachment_id: str) -> Path:
    return _bundle_dir_for_attachment(path) / f"{attachment_id}.json"


def _public_source_summary(source: dict[str, Any]) -> dict[str, Any]:
    parser = source.get("parser") if isinstance(source.get("parser"), dict) else {}
    revision = source.get("revision") if isinstance(source.get("revision"), dict) else {}
    return {
        "schema_id": str(source.get("schema_id") or ""),
        "status": str(source.get("status") or ""),
        "source_id": str(source.get("source_id") or ""),
        "source_name": str(source.get("source_name") or ""),
        "suffix": str(source.get("suffix") or ""),
        "media_type": str(source.get("media_type") or ""),
        "block_count": int(source.get("block_count") or 0),
        "chars": int(source.get("chars") or 0),
        "content_hash": str(source.get("content_hash") or revision.get("content_sha256") or ""),
        "revision_id": str(source.get("revision_id") or revision.get("revision_id") or ""),
        "parser_id": str(parser.get("parser_id") or source.get("method") or ""),
        "parser_version": str(parser.get("version") or ""),
        "parser_mode": str(parser.get("mode") or ""),
        "fallback_used": bool(parser.get("fallback_used")),
        "capabilities": dict(source.get("capabilities") or {}),
        "media": {
            "kind": str((source.get("metadata") or {}).get("media_kind") or ""),
            "language": str((source.get("metadata") or {}).get("language") or ""),
            "duration_seconds": (source.get("metadata") or {}).get("duration_seconds"),
            "segment_count": int((source.get("metadata") or {}).get("segment_count") or 0),
            "asr_backend": str((source.get("metadata") or {}).get("asr_backend") or ""),
            "asr_model": str((source.get("metadata") or {}).get("asr_model") or ""),
        } if bool((source.get("capabilities") or {}).get("media")) else {},
    }


def prepare_ephemeral_document(
    path: Path,
    *,
    attachment_id: str,
    session_id: str,
    filename: str,
) -> dict[str, Any]:
    """Prepare an ordinary Assistant document through the ACI-2 source contract.

    The resulting bundle is intentionally stored beside the temporary attachment,
    never in canonical memory/Project Brain. Deleting the attachment/session removes
    the source bundle as part of the same ephemeral lifecycle.
    """
    source = extract_project_document(path, path.suffix.lower())
    fragments = build_contextual_fragments(source, filename=filename)[:MAX_EPHEMERAL_FRAGMENTS_PER_SOURCE]
    bundle_path = bundle_path_for_attachment(path, attachment_id)
    payload = {
        "schema_id": EPHEMERAL_SOURCE_SCHEMA_ID,
        "lifecycle": "ephemeral_chat_attachment",
        "attachment_id": str(attachment_id or ""),
        "session_id": str(session_id or ""),
        "filename": str(filename or path.name),
        "created_at": now_iso(),
        "source_document": source,
        "fragments": fragments,
        "fragment_count": len(fragments),
    }
    write_json(bundle_path, payload)
    preview_parts = [str(item.get("text") or "").strip() for item in fragments if isinstance(item, dict) and str(item.get("text") or "").strip()]
    preview_text = "\n\n".join(preview_parts)[:24_000].rstrip()
    return {
        "status": str(source.get("status") or ("extracted" if fragments else "stored_only")),
        "bundle_path": bundle_path.as_posix(),
        "fragment_count": len(fragments),
        "source": _public_source_summary(source),
        "preview_text": preview_text,
    }



def prepare_ephemeral_media(
    path: Path,
    *,
    attachment_id: str,
    session_id: str,
    filename: str,
) -> dict[str, Any]:
    """Transcribe Assistant audio/video-audio through the shared Voice authority.

    The resulting NeoSourceDocument and transcript fragments inherit the same
    ephemeral chat lifecycle as ACI-3 documents.
    """
    source = prepare_media_source(path)
    fragments = build_contextual_fragments(source, filename=filename)[:MAX_EPHEMERAL_FRAGMENTS_PER_SOURCE]
    bundle_path = bundle_path_for_attachment(path, attachment_id)
    payload = {
        "schema_id": EPHEMERAL_SOURCE_SCHEMA_ID,
        "lifecycle": "ephemeral_chat_attachment",
        "attachment_id": str(attachment_id or ""),
        "session_id": str(session_id or ""),
        "filename": str(filename or path.name),
        "created_at": now_iso(),
        "source_document": source,
        "fragments": fragments,
        "fragment_count": len(fragments),
    }
    write_json(bundle_path, payload)
    preview_parts = [str(item.get("text") or "").strip() for item in fragments if isinstance(item, dict) and str(item.get("text") or "").strip()]
    preview_text = "\n\n".join(preview_parts)[:24_000].rstrip()
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    return {
        "status": str(source.get("status") or ("extracted" if fragments else "stored_only")),
        "bundle_path": bundle_path.as_posix(),
        "fragment_count": len(fragments),
        "source": _public_source_summary(source),
        "preview_text": preview_text,
        "transcription": {
            "status": str(metadata.get("transcription_status") or source.get("status") or ""),
            "language": metadata.get("language"),
            "duration_seconds": metadata.get("duration_seconds"),
            "segment_count": int(metadata.get("segment_count") or 0),
            "backend": str(metadata.get("asr_backend") or ""),
            "model": str(metadata.get("asr_model") or ""),
            "device": str(metadata.get("asr_device") or ""),
            "compute_type": str(metadata.get("asr_compute_type") or ""),
            "error": str(metadata.get("transcription_error") or ""),
        },
    }

def load_ephemeral_bundle(record: dict[str, Any]) -> dict[str, Any]:
    path_text = str(record.get("ephemeral_bundle_path") or "").strip()
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return {}
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def delete_ephemeral_bundle(record: dict[str, Any]) -> bool:
    path_text = str(record.get("ephemeral_bundle_path") or "").strip()
    if not path_text:
        return False
    try:
        path = Path(path_text)
        if path.exists() and path.is_file():
            path.unlink()
            parent = path.parent
            try:
                if parent.name == ".ephemeral_knowledge" and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception:
                pass
            return True
    except Exception:
        return False
    return False



def _fts_index_path(session_id: str) -> Path:
    EPHEMERAL_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    safe = slugify(session_id or "unassigned", "unassigned")
    return EPHEMERAL_INDEX_DIR / f"{safe}.sqlite3"


def delete_session_ephemeral_index(session_id: str) -> bool:
    path = _fts_index_path(session_id)
    deleted = False
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            if candidate.exists():
                candidate.unlink()
                deleted = True
        except Exception:
            pass
    return deleted


def _fts_shortlist(query: str, candidates: list[dict[str, Any]], *, session_id: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates:
        return [], {"status": "empty", "backend": "sqlite_fts5", "count": 0}
    path = _fts_index_path(session_id)
    by_key: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS ephemeral_fts USING fts5(fragment_key UNINDEXED, title, content)")
        conn.execute("DELETE FROM ephemeral_fts")
        rows = []
        for idx, item in enumerate(candidates):
            key = str(item.get("fragment_id") or f"row_{idx}") + ":" + str(item.get("attachment_id") or "")
            by_key[key] = item
            rows.append((key, str(item.get("title") or ""), str(item.get("content") or "")))
        conn.executemany("INSERT INTO ephemeral_fts(fragment_key, title, content) VALUES (?, ?, ?)", rows)
        conn.commit()
        terms = [token.replace('"', '') for token in _TOKEN_RE.findall(str(query or "")) if token]
        match = " OR ".join(f'"{term}"' for term in terms[:16])
        if not match:
            conn.close()
            return [], {"status": "empty_query", "backend": "sqlite_fts5", "count": 0, "path": str(path)}
        result = conn.execute(
            "SELECT fragment_key, bm25(ephemeral_fts) AS rank FROM ephemeral_fts WHERE ephemeral_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, max(1, min(int(limit), 64))),
        ).fetchall()
        conn.close()
        selected = []
        for key, rank in result:
            item = dict(by_key.get(str(key)) or {})
            if not item:
                continue
            item["fts_rank"] = float(rank)
            # bm25 is lower-is-better and often negative in FTS5. Preserve lexical
            # overlap as a stable cross-query score; FTS controls shortlist order.
            selected.append(item)
        return selected, {"status": "ready", "backend": "sqlite_fts5", "count": len(selected), "path": str(path)}
    except Exception as exc:
        return [], {"status": "unavailable", "backend": "sqlite_fts5", "count": 0, "error": str(exc)[:500], "path": str(path)}

def _fragment_candidate(record: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    text = str(fragment.get("search_text") or fragment.get("text") or "").strip()
    return {
        "attachment_id": str(record.get("attachment_id") or ""),
        "filename": str(record.get("filename") or record.get("stored_filename") or "attachment"),
        "fragment_id": str(fragment.get("fragment_key") or fragment.get("fragment_id") or ""),
        "title": str(fragment.get("title") or record.get("filename") or "Attachment fragment"),
        "content": text,
        "text": str(fragment.get("text") or text),
        "source_locators": list(fragment.get("source_locators") or []),
        "source_locator": dict(fragment.get("source_locator") or {}),
        "heading_path": list(fragment.get("heading_path") or []),
        "epistemic_state": str(fragment.get("epistemic_state") or "established"),
        "supports_positive_claims": bool(fragment.get("supports_positive_claims", True)),
    }


def retrieve_ephemeral_context(
    query: str,
    records: list[dict[str, Any]],
    *,
    session_id: str = "",
    top_k: int = DEFAULT_EPHEMERAL_TOP_K,
    max_context_chars: int = DEFAULT_EPHEMERAL_CONTEXT_CHARS,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    missing_bundles: list[str] = []
    for record in records:
        if str(record.get("kind") or "") not in {"document", "media"} or record.get("missing"):
            continue
        bundle = load_ephemeral_bundle(record)
        if not bundle:
            missing_bundles.append(str(record.get("attachment_id") or ""))
            continue
        source = bundle.get("source_document") if isinstance(bundle.get("source_document"), dict) else {}
        sources.append({"attachment_id": record.get("attachment_id"), **_public_source_summary(source)})
        for fragment in bundle.get("fragments") or []:
            if isinstance(fragment, dict):
                item = _fragment_candidate(record, fragment)
                if item["content"]:
                    item["lexical_score"] = _lexical_score(query, item["content"])
                    candidates.append(item)

    candidates.sort(key=lambda item: (float(item.get("lexical_score") or 0), len(str(item.get("content") or ""))), reverse=True)
    fts_items, fts = _fts_shortlist(query, candidates, session_id=session_id, limit=MAX_EPHEMERAL_CANDIDATES)
    shortlist = fts_items or candidates[:MAX_EPHEMERAL_CANDIDATES]
    semantic = {"status": "skipped", "reason": "no_candidates" if not shortlist else "not_run"}
    if shortlist and str(query or "").strip():
        try:
            embedding = embed_texts([query] + [str(item.get("content") or "") for item in shortlist], allow_fallback=False)
            vectors = embedding.get("vectors") if isinstance(embedding.get("vectors"), list) else []
            if len(vectors) == len(shortlist) + 1:
                qv = vectors[0]
                for item, vector in zip(shortlist, vectors[1:]):
                    sem = _cosine(qv, vector)
                    item["semantic_score"] = sem
                    item["score"] = round((float(item.get("lexical_score") or 0) * 0.35) + (sem * 0.65), 6)
                shortlist.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
            semantic = {key: value for key, value in embedding.items() if key != "vectors"}
        except Exception as exc:
            semantic = {"status": "unavailable", "reason": "embedding_failed", "error": str(exc)[:500]}
            for item in shortlist:
                item["score"] = float(item.get("lexical_score") or 0)

    reranker = {"status": "skipped", "reason": "no_candidates"}
    ranked = shortlist
    if shortlist and str(query or "").strip():
        try:
            result = rerank_results(query, shortlist, top_n=max(top_k, min(12, len(shortlist))), allow_fallback=False)
            reranker = {key: value for key, value in result.items() if key != "results"}
            ranked = [dict(item) for item in (result.get("results") or shortlist)]
        except Exception as exc:
            reranker = {"status": "unavailable", "reason": "reranker_failed", "error": str(exc)[:500]}

    selected = ranked[: max(1, min(int(top_k or DEFAULT_EPHEMERAL_TOP_K), 12))]
    sections: list[str] = []
    used = 0
    selected_public: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        body = str(item.get("text") or item.get("content") or "").strip()
        if not body:
            continue
        heading = f"Attachment: {item.get('filename')}\nFragment: {item.get('title')}"
        loc = item.get("source_locator") if isinstance(item.get("source_locator"), dict) else {}
        if loc.get("label"):
            heading += f"\nLocation: {loc.get('label')}"
        section = f"{heading}\n\n{body}"
        remaining = max_context_chars - used
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip()
        sections.append(section)
        used += len(section)
        selected_public.append({
            "rank": index,
            "attachment_id": item.get("attachment_id"),
            "filename": item.get("filename"),
            "fragment_id": item.get("fragment_id"),
            "title": item.get("title"),
            "score": item.get("score", item.get("lexical_score", 0.0)),
            "lexical_score": item.get("lexical_score", 0.0),
            "semantic_score": item.get("semantic_score"),
            "source_locator": item.get("source_locator") or {},
            "source_locators": item.get("source_locators") or [],
            "supports_positive_claims": item.get("supports_positive_claims", True),
        })

    return {
        "schema_id": EPHEMERAL_RETRIEVAL_SCHEMA_ID,
        "status": "ready" if sections else "empty",
        "lifecycle": "ephemeral_chat_attachment",
        "query": str(query or ""),
        "source_count": len(sources),
        "candidate_count": len(candidates),
        "selected_count": len(selected_public),
        "context": "\n\n---\n\n".join(sections),
        "selected": selected_public,
        "sources": sources,
        "fts": fts,
        "semantic": semantic,
        "reranker": reranker,
        "missing_bundles": [item for item in missing_bundles if item],
        "context_chars": used,
        "generated_at": now_iso(),
    }
