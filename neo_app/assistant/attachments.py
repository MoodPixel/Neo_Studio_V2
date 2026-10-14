from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.assistant.store import ASSISTANT_DATA_DIR, ROOT_DIR, now_iso, read_json, slugify, write_json
from neo_app.image.upload_validation import ImageUploadValidationError, validate_and_store_image_upload
from neo_app.assistant.ephemeral_sources import (
    delete_ephemeral_bundle,
    delete_session_ephemeral_index,
    prepare_ephemeral_document,
    prepare_ephemeral_media,
    retrieve_ephemeral_context,
)
from neo_app.knowledge.media_source import AUDIO_MEDIA_SUFFIXES, VIDEO_MEDIA_SUFFIXES, MEDIA_SUFFIXES

ASSISTANT_ATTACHMENT_SCHEMA_ID = "neo.assistant.attachments.v1"
ATTACHMENTS_DIR = ASSISTANT_DATA_DIR / "attachments"
ATTACHMENTS_INDEX_PATH = ATTACHMENTS_DIR / "assistant_attachments_index.json"
DOCUMENT_DIRNAME = "documents"
IMAGE_DIRNAME = "images"
MEDIA_DIRNAME = "media"
MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MEDIA_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 24_000
MAX_CONTEXT_TEXT_CHARS = 12_000

TEXT_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".log",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".srt",
    ".vtt",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".less",
    ".ini",
    ".toml",
}
BINARY_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}
ALLOWED_DOCUMENT_EXTENSIONS = TEXT_DOCUMENT_EXTENSIONS | BINARY_DOCUMENT_EXTENSIONS


def ensure_attachment_dirs() -> None:
    for path in (ATTACHMENTS_DIR, ATTACHMENTS_DIR / IMAGE_DIRNAME, ATTACHMENTS_DIR / DOCUMENT_DIRNAME, ATTACHMENTS_DIR / MEDIA_DIRNAME):
        path.mkdir(parents=True, exist_ok=True)


def _attachment_index() -> dict[str, Any]:
    ensure_attachment_dirs()
    payload = read_json(ATTACHMENTS_INDEX_PATH, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_id", ASSISTANT_ATTACHMENT_SCHEMA_ID)
    payload.setdefault("attachments", [])
    if not isinstance(payload.get("attachments"), list):
        payload["attachments"] = []
    payload.setdefault("updated_at", now_iso())
    return payload


def _write_attachment_index(payload: dict[str, Any]) -> None:
    ensure_attachment_dirs()
    payload = payload if isinstance(payload, dict) else {}
    payload.setdefault("schema_id", ASSISTANT_ATTACHMENT_SCHEMA_ID)
    payload.setdefault("attachments", [])
    payload["updated_at"] = now_iso()
    write_json(ATTACHMENTS_INDEX_PATH, payload)


def _safe_session_dir(kind: str, session_id: str | None = None) -> Path:
    ensure_attachment_dirs()
    bucket = IMAGE_DIRNAME if kind == "image" else (MEDIA_DIRNAME if kind == "media" else DOCUMENT_DIRNAME)
    safe_session = slugify(session_id or "unassigned", "unassigned")
    path = (ATTACHMENTS_DIR / bucket / safe_session).resolve()
    root = (ATTACHMENTS_DIR / bucket).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid Assistant attachment session path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_attachment_id(value: str | None) -> str:
    return slugify(value or f"att_{uuid4().hex[:12]}", "attachment")


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except Exception:
        return path.as_posix()


def _guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(filename or "")[0] or fallback


def _record_public(record: dict[str, Any]) -> dict[str, Any]:
    clean = dict(record or {})
    extracted = clean.get("extracted_text")
    if isinstance(extracted, str) and len(extracted) > MAX_CONTEXT_TEXT_CHARS:
        clean["extracted_text"] = extracted[:MAX_CONTEXT_TEXT_CHARS].rstrip()
        clean["extracted_text_truncated_for_response"] = True
    return clean


def _upsert_record(record: dict[str, Any]) -> dict[str, Any]:
    index = _attachment_index()
    attachment_id = str(record.get("attachment_id") or "")
    records = [item for item in index.get("attachments", []) if isinstance(item, dict) and item.get("attachment_id") != attachment_id]
    records.insert(0, record)
    index["attachments"] = records[:500]
    _write_attachment_index(index)
    return record


def get_attachment_record(attachment_id: str) -> dict[str, Any] | None:
    wanted = str(attachment_id or "").strip()
    if not wanted:
        return None
    for record in _attachment_index().get("attachments", []):
        if isinstance(record, dict) and str(record.get("attachment_id") or "") == wanted:
            return record
    return None


def list_attachment_records(session_id: str = "", project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    records = []
    for record in _attachment_index().get("attachments", []):
        if not isinstance(record, dict):
            continue
        if session_id and str(record.get("session_id") or "") != session_id:
            continue
        if project_id and str(record.get("project_id") or "") != project_id:
            continue
        records.append(_record_public(record))
        if len(records) >= max(1, min(200, int(limit or 50))):
            break
    return records


def attachment_path(record: dict[str, Any]) -> Path | None:
    path_text = str((record or {}).get("path") or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT_DIR / path
    try:
        resolved = path.resolve()
        root = ATTACHMENTS_DIR.resolve()
        if root not in resolved.parents and resolved != root:
            return None
        return resolved
    except Exception:
        return None


def _decode_text_document(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _clean_extracted_text(text: str) -> str:
    cleaned = str(text or "").replace("\x00", "")
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = re.sub(r"\n{5,}", "\n\n\n", cleaned).strip()
    if len(cleaned) > MAX_EXTRACTED_TEXT_CHARS:
        return cleaned[:MAX_EXTRACTED_TEXT_CHARS].rstrip()
    return cleaned


def _extract_pdf_text(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return "", {"status": "stored_only", "reason": "pdf_text_extractor_unavailable"}
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages[:40]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        text = _clean_extracted_text("\n\n".join(pages))
        return text, {"status": "extracted" if text else "stored_only", "page_count": len(reader.pages), "pages_scanned": min(40, len(reader.pages))}
    except Exception as exc:
        return "", {"status": "stored_only", "reason": f"pdf_extract_failed: {exc}"}


def _extract_docx_text(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
        text = _clean_extracted_text(" ".join(texts))
        return text, {"status": "extracted" if text else "stored_only", "method": "docx_xml"}
    except Exception as exc:
        return "", {"status": "stored_only", "reason": f"docx_extract_failed: {exc}"}


def extract_document_text(path: Path, suffix: str, data: bytes | None = None) -> tuple[str, dict[str, Any]]:
    suffix = suffix.lower()
    if suffix in TEXT_DOCUMENT_EXTENSIONS:
        raw = data if data is not None else path.read_bytes()
        text, encoding = _decode_text_document(raw)
        text = _clean_extracted_text(text)
        return text, {"status": "extracted" if text else "empty", "encoding": encoding, "chars": len(text)}
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    if suffix == ".docx":
        return _extract_docx_text(path)
    return "", {"status": "stored_only", "reason": f"text_extraction_not_supported_for_{suffix.lstrip('.') or 'file'}"}


async def save_image_attachment(file: Any, *, session_id: str = "", project_id: str = "") -> dict[str, Any]:
    attachment_id = f"img_{uuid4().hex[:12]}"
    target_dir = _safe_session_dir("image", session_id)
    try:
        stored = await validate_and_store_image_upload(
            file,
            target_dir=target_dir,
            prefix=attachment_id,
            default_filename="assistant_image.png",
            label="assistant attachment",
            repair_extension_mismatch=True,
        )
    except ImageUploadValidationError:
        raise
    mime = _guess_mime(stored.stored_filename, f"image/{stored.detected_type or 'png'}")
    image_intelligence = {}
    try:
        from neo_app.image.native_intelligence import attachment_metadata_summary, inspect_image_asset_file
        intake = inspect_image_asset_file(stored.path, filename=stored.original_filename)
        image_intelligence = attachment_metadata_summary(intake)
    except Exception as exc:
        image_intelligence = {
            "schema_id": "neo.image.asset_metadata_intake.v1",
            "status": "error",
            "proof_level": "unidentified",
            "known_state": "not_established",
            "error": str(exc)[:300],
        }
    record = {
        "schema_id": ASSISTANT_ATTACHMENT_SCHEMA_ID,
        "attachment_id": attachment_id,
        "kind": "image",
        "session_id": str(session_id or ""),
        "project_id": str(project_id or ""),
        "filename": stored.original_filename,
        "stored_filename": stored.stored_filename,
        "mime_type": mime,
        "path": stored.path.as_posix(),
        "relative_path": _relative_to_root(stored.path),
        "url": f"/api/assistant/attachments/{attachment_id}",
        "storage": "neo_data/assistant/attachments/images",
        "size_bytes": stored.size_bytes,
        "detected_type": stored.detected_type,
        "extension_repaired": stored.extension_repaired,
        "created_at": now_iso(),
        "extraction": {"status": "not_applicable", "reason": "image_attachment"},
        "image_intelligence": image_intelligence,
    }
    return _record_public(_upsert_record(record))


async def _stream_upload_to_path(file: Any, target: Path, *, max_bytes: int) -> int:
    written = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise OverflowError(f"Assistant media is too large. Max allowed: {max_bytes // (1024 * 1024)} MB.")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return written


async def save_document_attachment(file: Any, *, session_id: str = "", project_id: str = "") -> dict[str, Any]:
    original = Path(getattr(file, "filename", None) or "assistant_document.txt").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Assistant document type '{suffix or 'unknown'}' is not supported yet.")
    data = await file.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
    if not data:
        raise ValueError("Assistant document upload is empty.")
    if len(data) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise OverflowError("Assistant document is too large. Max allowed: 25 MB.")
    attachment_id = f"doc_{uuid4().hex[:12]}"
    target_dir = _safe_session_dir("document", session_id)
    safe_base = slugify(Path(original).stem, "document")
    target = target_dir / f"{attachment_id}_{safe_base}{suffix}"
    target.write_bytes(data)
    try:
        prepared = prepare_ephemeral_document(
            target,
            attachment_id=attachment_id,
            session_id=str(session_id or ""),
            filename=original,
        )
    except Exception as exc:
        prepared = {
            "status": "stored_only",
            "bundle_path": "",
            "fragment_count": 0,
            "source": {},
            "preview_text": "",
            "error": str(exc)[:500],
        }
    mime = str(getattr(file, "content_type", None) or "") or _guess_mime(original, "application/octet-stream")
    source_summary = prepared.get("source") if isinstance(prepared.get("source"), dict) else {}
    extraction = {
        "status": str(prepared.get("status") or "stored_only"),
        "method": str(source_summary.get("parser_id") or "neo_source_document"),
        "parser_version": str(source_summary.get("parser_version") or ""),
        "parser_mode": str(source_summary.get("parser_mode") or ""),
        "fragment_count": int(prepared.get("fragment_count") or 0),
        "fallback_used": bool(source_summary.get("fallback_used")),
    }
    if prepared.get("error"):
        extraction["ephemeral_prepare_error"] = str(prepared.get("error"))
    record = {
        "schema_id": ASSISTANT_ATTACHMENT_SCHEMA_ID,
        "attachment_id": attachment_id,
        "kind": "document",
        "session_id": str(session_id or ""),
        "project_id": str(project_id or ""),
        "filename": original,
        "stored_filename": target.name,
        "mime_type": mime,
        "path": target.as_posix(),
        "relative_path": _relative_to_root(target),
        "url": f"/api/assistant/attachments/{attachment_id}",
        "storage": "neo_data/assistant/attachments/documents",
        "size_bytes": len(data),
        "created_at": now_iso(),
        # Compatibility preview only. ACI-3 prompt grounding uses the ephemeral
        # NeoSourceDocument fragments rather than injecting this field wholesale.
        "extracted_text": str(prepared.get("preview_text") or ""),
        "extraction": extraction,
        "ephemeral_bundle_path": str(prepared.get("bundle_path") or ""),
        "ephemeral_fragment_count": int(prepared.get("fragment_count") or 0),
        "source_document": source_summary,
        "lifecycle": "ephemeral_chat_attachment",
    }
    return _record_public(_upsert_record(record))


async def save_media_attachment(file: Any, *, session_id: str = "", project_id: str = "") -> dict[str, Any]:
    original = Path(getattr(file, "filename", None) or "assistant_media.wav").name
    suffix = Path(original).suffix.lower()
    content_type = str(getattr(file, "content_type", None) or "").lower()
    if suffix not in MEDIA_SUFFIXES and not (content_type.startswith("audio/") or content_type.startswith("video/")):
        raise ValueError(f"Assistant media type '{suffix or content_type or 'unknown'}' is not supported yet.")
    attachment_id = f"media_{uuid4().hex[:12]}"
    target_dir = _safe_session_dir("media", session_id)
    safe_base = slugify(Path(original).stem, "media")
    target_suffix = suffix if suffix in MEDIA_SUFFIXES else (".mp4" if content_type.startswith("video/") else ".wav")
    target = target_dir / f"{attachment_id}_{safe_base}{target_suffix}"
    written = await _stream_upload_to_path(file, target, max_bytes=MAX_MEDIA_UPLOAD_BYTES)
    try:
        prepared = await asyncio.to_thread(
            prepare_ephemeral_media,
            target,
            attachment_id=attachment_id,
            session_id=str(session_id or ""),
            filename=original,
        )
    except Exception as exc:
        prepared = {
            "status": "stored_only",
            "bundle_path": "",
            "fragment_count": 0,
            "source": {},
            "preview_text": "",
            "transcription": {"status": "transcription_failed", "error": str(exc)[:500]},
            "error": str(exc)[:500],
        }
    mime = str(getattr(file, "content_type", None) or "") or _guess_mime(original, "application/octet-stream")
    source_summary = prepared.get("source") if isinstance(prepared.get("source"), dict) else {}
    capabilities = source_summary.get("capabilities") if isinstance(source_summary.get("capabilities"), dict) else {}
    media_meta = source_summary.get("media") if isinstance(source_summary.get("media"), dict) else {}
    media_kind = str(media_meta.get("kind") or ("video" if content_type.startswith("video/") or suffix in VIDEO_MEDIA_SUFFIXES and suffix not in AUDIO_MEDIA_SUFFIXES else "audio"))
    transcription = prepared.get("transcription") if isinstance(prepared.get("transcription"), dict) else {}
    extraction = {
        "status": str(prepared.get("status") or "stored_only"),
        "method": str(source_summary.get("parser_id") or "voice_transcription"),
        "parser_version": str(source_summary.get("parser_version") or ""),
        "parser_mode": str(source_summary.get("parser_mode") or "asr"),
        "fragment_count": int(prepared.get("fragment_count") or 0),
        "fallback_used": bool(source_summary.get("fallback_used")),
    }
    if prepared.get("error"):
        extraction["ephemeral_prepare_error"] = str(prepared.get("error"))
    record = {
        "schema_id": ASSISTANT_ATTACHMENT_SCHEMA_ID,
        "attachment_id": attachment_id,
        "kind": "media",
        "media_kind": media_kind,
        "session_id": str(session_id or ""),
        "project_id": str(project_id or ""),
        "filename": original,
        "stored_filename": target.name,
        "mime_type": mime,
        "path": target.as_posix(),
        "relative_path": _relative_to_root(target),
        "url": f"/api/assistant/attachments/{attachment_id}",
        "storage": "neo_data/assistant/attachments/media",
        "size_bytes": written,
        "created_at": now_iso(),
        "extracted_text": str(prepared.get("preview_text") or ""),
        "extraction": extraction,
        "transcription": transcription,
        "ephemeral_bundle_path": str(prepared.get("bundle_path") or ""),
        "ephemeral_fragment_count": int(prepared.get("fragment_count") or 0),
        "source_document": source_summary,
        "media_capabilities": capabilities,
        "lifecycle": "ephemeral_chat_attachment",
    }
    return _record_public(_upsert_record(record))


async def save_attachment_upload(file: Any, *, session_id: str = "", project_id: str = "", kind: str = "auto") -> dict[str, Any]:
    filename = str(getattr(file, "filename", None) or "")
    suffix = Path(filename).suffix.lower()
    content_type = str(getattr(file, "content_type", "") or "").lower()
    requested = str(kind or "auto").lower().strip()
    if requested == "image" or content_type.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return await save_image_attachment(file, session_id=session_id, project_id=project_id)
    if requested in {"media", "audio", "video"} or content_type.startswith("audio/") or content_type.startswith("video/") or suffix in MEDIA_SUFFIXES:
        return await save_media_attachment(file, session_id=session_id, project_id=project_id)
    if requested in {"document", "doc", "file", "auto"}:
        return await save_document_attachment(file, session_id=session_id, project_id=project_id)
    raise ValueError("Unsupported Assistant attachment kind.")


def delete_attachment_record(attachment_id: str) -> dict[str, Any]:
    record = get_attachment_record(attachment_id)
    if not record:
        return {"ok": False, "status": "missing", "message": "Assistant attachment not found."}
    path = attachment_path(record)
    deleted_bundle = delete_ephemeral_bundle(record)
    deleted_file = False
    if path and path.exists() and path.is_file():
        path.unlink()
        deleted_file = True
    index = _attachment_index()
    index["attachments"] = [item for item in index.get("attachments", []) if not (isinstance(item, dict) and item.get("attachment_id") == attachment_id)]
    _write_attachment_index(index)
    return {"ok": True, "status": "deleted", "attachment_id": attachment_id, "deleted_file": deleted_file, "deleted_ephemeral_bundle": deleted_bundle}


def _record_for_prompt(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_id": str(record.get("attachment_id") or ""),
        "kind": str(record.get("kind") or ""),
        "filename": str(record.get("filename") or record.get("stored_filename") or ""),
        "mime_type": str(record.get("mime_type") or ""),
        "size_bytes": int(record.get("size_bytes") or 0),
        "url": str(record.get("url") or ""),
        "extraction": record.get("extraction") if isinstance(record.get("extraction"), dict) else {},
        "media_kind": str(record.get("media_kind") or ""),
        "transcription": record.get("transcription") if isinstance(record.get("transcription"), dict) else {},
    }


def resolve_payload_attachments(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = payload if isinstance(payload, dict) else {}
    raw_items = payload.get("attachments") or payload.get("attachment_ids") or []
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items[:12]:
        record: dict[str, Any] | None = None
        if isinstance(item, str):
            record = get_attachment_record(item)
        elif isinstance(item, dict):
            attachment_id = str(item.get("attachment_id") or item.get("id") or "").strip()
            if attachment_id:
                record = get_attachment_record(attachment_id) or item
        if not record:
            continue
        attachment_id = str(record.get("attachment_id") or "").strip()
        if not attachment_id or attachment_id in seen:
            continue
        path = attachment_path(record)
        if path is None or not path.exists() or not path.is_file():
            record = {**record, "missing": True}
        resolved.append(record)
        seen.add(attachment_id)
    return resolved



def attachment_native_image_context(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a bounded, read-only native Image context for NKB-15I operators.

    This is intentionally smaller than the prompt-facing attachment context. It
    carries only provenance/identity and normalized external metadata needed by
    native Image target resolution and replay checks before provider prompt
    compilation occurs.
    """
    items: list[dict[str, Any]] = []
    for record in records[:6]:
        if str(record.get("kind") or "") != "image" or record.get("missing"):
            continue
        path = attachment_path(record)
        if path is None or not path.exists() or not path.is_file():
            continue
        try:
            from neo_app.image.native_intelligence import inspect_image_asset_file
            intake = inspect_image_asset_file(path, filename=str(record.get("filename") or path.name))
        except Exception as exc:
            items.append({
                "attachment_id": str(record.get("attachment_id") or ""),
                "filename": str(record.get("filename") or path.name),
                "proof_level": "unidentified",
                "known_state": "not_established",
                "error": str(exc)[:300],
            })
            continue
        packet = intake.get("packet") if isinstance(intake.get("packet"), dict) else {}
        payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        items.append({
            "attachment_id": str(record.get("attachment_id") or ""),
            "filename": str(record.get("filename") or path.name),
            "proof_level": str(intake.get("proof_level") or "unidentified"),
            "known_state": str(packet.get("known_state") or "not_established"),
            "identity": payload.get("identity") if isinstance(payload.get("identity"), dict) else {},
            "model": (payload.get("recipe") or {}).get("model") if isinstance(payload.get("recipe"), dict) and isinstance((payload.get("recipe") or {}).get("model"), dict) else {},
            "sampling": (payload.get("recipe") or {}).get("sampling") if isinstance(payload.get("recipe"), dict) and isinstance((payload.get("recipe") or {}).get("sampling"), dict) else {},
            "external_metadata": {
                "origin": metadata.get("origin"),
                "format": metadata.get("format"),
                "normalized_fields": metadata.get("normalized_fields") if isinstance(metadata.get("normalized_fields"), dict) else {},
            } if metadata else {},
        })
    return items

def attachment_context_payload(
    records: list[dict[str, Any]],
    *,
    vision_supported: bool,
    query: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    image_metadata_sections: list[str] = []
    image_records: list[dict[str, Any]] = []
    document_records: list[dict[str, Any]] = []
    media_records: list[dict[str, Any]] = []
    image_intelligence: list[dict[str, Any]] = []
    warnings: list[str] = []
    for record in records:
        kind = str(record.get("kind") or "")
        prompt_record = _record_for_prompt(record)
        if record.get("missing"):
            prompt_record["missing"] = True
            warnings.append(f"Attachment {record.get('filename') or record.get('attachment_id')} is missing on disk.")
        summaries.append(prompt_record)
        if kind == "document":
            document_records.append(record)
            if not str(record.get("ephemeral_bundle_path") or "").strip():
                extraction = record.get("extraction") if isinstance(record.get("extraction"), dict) else {}
                if not str(record.get("extracted_text") or "").strip():
                    warnings.append(f"Document {record.get('filename') or record.get('attachment_id')} has no prepared ephemeral source ({extraction.get('reason') or extraction.get('status') or 'stored only'}).")
        elif kind == "media":
            media_records.append(record)
            transcription = record.get("transcription") if isinstance(record.get("transcription"), dict) else {}
            transcription_status = str(transcription.get("status") or "").strip().lower()
            if transcription_status != "transcribed":
                warnings.append(
                    f"Media {record.get('filename') or record.get('attachment_id')} has no usable transcript "
                    f"({transcription_status or 'transcriber unavailable'})."
                )
            elif not str(record.get("ephemeral_bundle_path") or "").strip():
                warnings.append(f"Media {record.get('filename') or record.get('attachment_id')} has no prepared transcript source.")
            if str(record.get("media_kind") or "") == "video":
                warnings.append(f"Video {record.get('filename') or record.get('attachment_id')} contributes transcribed audio only; ACI-4 did not inspect visual frames.")
        elif kind == "image":
            image_records.append(record)
            intelligence = {}
            path = attachment_path(record)
            if path is not None and path.exists() and path.is_file():
                try:
                    from neo_app.image.native_intelligence import (
                        attachment_metadata_summary,
                        inspect_image_asset_file,
                        render_attachment_image_context,
                    )
                    intake = inspect_image_asset_file(path, filename=str(record.get("filename") or path.name))
                    intelligence = attachment_metadata_summary(intake)
                    rendered = render_attachment_image_context(intake, limit=9000)
                    if rendered:
                        image_metadata_sections.append(rendered)
                except Exception as exc:
                    intelligence = {
                        "schema_id": "neo.image.asset_metadata_intake.v1",
                        "status": "error",
                        "proof_level": "unidentified",
                        "known_state": "not_established",
                        "error": str(exc)[:300],
                    }
            if intelligence:
                prompt_record["image_intelligence"] = intelligence
                image_intelligence.append({"attachment_id": str(record.get("attachment_id") or ""), **intelligence})
            proof = str(intelligence.get("proof_level") or "")
            if not vision_supported:
                if proof in {"native_exact_full_record", "native_exact_embedded_only", "native_exact_integrity_match", "external_embedded_metadata"}:
                    warnings.append(f"Image {record.get('filename') or record.get('attachment_id')} is attached; the selected backend cannot inspect images visually, but recoverable image metadata is available.")
                else:
                    warnings.append(f"Image {record.get('filename') or record.get('attachment_id')} is attached, but the selected backend cannot inspect images visually and no recoverable generation metadata was established.")

    retrieval_records = document_records + media_records
    retrieval = retrieve_ephemeral_context(query, retrieval_records, session_id=session_id) if retrieval_records else {
        "schema_id": "neo.assistant.ephemeral_retrieval.v1",
        "status": "empty",
        "context": "",
        "selected": [],
        "source_count": 0,
        "candidate_count": 0,
        "selected_count": 0,
        "fts": {"status": "skipped", "reason": "no_documents"},
        "semantic": {"status": "skipped", "reason": "no_documents"},
        "reranker": {"status": "skipped", "reason": "no_documents"},
    }
    document_context = str(retrieval.get("context") or "").strip()
    # Compatibility bridge for attachments created before ACI-3. Do not use it when
    # a prepared bundle exists, and keep it bounded to the old context budget.
    if not document_context and document_records:
        legacy_sections = []
        for record in document_records:
            if str(record.get("ephemeral_bundle_path") or "").strip():
                continue
            text = str(record.get("extracted_text") or "").strip()
            if text:
                legacy_sections.append(
                    f"Document: {record.get('filename') or record.get('attachment_id')}\nAttachment ID: {record.get('attachment_id')}\nLegacy extracted text:\n{text[:MAX_CONTEXT_TEXT_CHARS].rstrip()}"
                )
        document_context = "\n\n---\n\n".join(legacy_sections)
        if legacy_sections:
            retrieval = {**retrieval, "status": "legacy_bridge", "legacy_bridge": True}

    return {
        "records": summaries,
        "document_context": document_context,
        "ephemeral_retrieval": retrieval,
        "image_metadata_context": "\n\n---\n\n".join(image_metadata_sections),
        "image_intelligence": image_intelligence,
        "images": image_records,
        "media": media_records,
        "warnings": warnings,
        "counts": {
            "total": len(records),
            "images": len(image_records),
            "documents": len(document_records),
            "media": len(media_records),
            "audio": sum(1 for item in media_records if str(item.get("media_kind") or "") == "audio"),
            "video_audio": sum(1 for item in media_records if str(item.get("media_kind") or "") == "video"),
            "ephemeral_sources": int(retrieval.get("source_count") or 0),
            "retrieved_fragments": int(retrieval.get("selected_count") or 0),
        },
        "vision_supported": bool(vision_supported),
    }


def image_attachment_content_part(record: dict[str, Any]) -> dict[str, Any] | None:
    path = attachment_path(record)
    if path is None or not path.exists() or not path.is_file():
        return None
    mime = str(record.get("mime_type") or _guess_mime(path.name, "image/png"))
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def cleanup_session_orphan_attachment_dir(session_id: str) -> None:
    """Best-effort cleanup hook for future session delete cascades."""
    safe = slugify(session_id or "", "")
    if not safe:
        return
    for bucket in (IMAGE_DIRNAME, DOCUMENT_DIRNAME, MEDIA_DIRNAME):
        path = ATTACHMENTS_DIR / bucket / safe
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    delete_session_ephemeral_index(session_id)
