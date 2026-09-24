from __future__ import annotations

import binascii
import hashlib
import json
import struct
from pathlib import Path
from typing import Any
from uuid import uuid4

PORTABLE_METADATA_SCHEMA = "neo.image.portable_metadata.v1"
PNG_KEYWORD = b"NeoStudio"
JPEG_PREFIX = b"NeoStudio\x00"
WEBP_CHUNK = b"NMDT"
MAX_EMBEDDED_JSON_BYTES = 48 * 1024


def new_neo_output_id() -> str:
    return f"neoimg_{uuid4().hex}"


def new_neo_result_uid() -> str:
    return f"neoresult_{uuid4().hex}"


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def build_portable_payload(record: dict[str, Any], file_record: dict[str, Any]) -> dict[str, Any]:
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    save_details = record.get("save_details") if isinstance(record.get("save_details"), dict) else {}
    payload = {
        "schema_version": PORTABLE_METADATA_SCHEMA,
        "neo_origin": "Neo Studio V2",
        "neo_output_id": str(file_record.get("neo_output_id") or ""),
        "neo_result_uid": str(record.get("neo_result_uid") or ""),
        "file_id": str(file_record.get("file_id") or ""),
        "result_id": str(record.get("result_id") or ""),
        "created_at": str(record.get("created_at") or ""),
        "save_category": str(save_details.get("category") or record.get("subtab") or ""),
        "image_filename": str(file_record.get("filename") or ""),
        "summary": {
            "positive_prompt": _compact_text(prompt.get("positive"), 6000),
            "negative_prompt": _compact_text(prompt.get("negative"), 3000),
            "effective_positive_prompt": _compact_text(prompt.get("effective_positive"), 6000),
            "effective_negative_prompt": _compact_text(prompt.get("effective_negative"), 3000),
            "model": {
                "family": str(model.get("family") or ""),
                "loader": str(model.get("loader") or ""),
                "model": str(model.get("model") or ""),
                "vae": str(model.get("vae") or ""),
            },
            "params": {key: params.get(key) for key in (
                "seed", "actual_seed", "requested_seed", "steps", "cfg", "sampler", "scheduler",
                "width", "height", "denoise", "clip_skip", "family", "loader", "mode",
            ) if key in params},
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) <= MAX_EMBEDDED_JSON_BYTES:
        return payload
    payload["summary"]["positive_prompt"] = _compact_text(payload["summary"]["positive_prompt"], 2500)
    payload["summary"]["negative_prompt"] = _compact_text(payload["summary"]["negative_prompt"], 1200)
    payload["summary"]["effective_positive_prompt"] = _compact_text(payload["summary"]["effective_positive_prompt"], 2500)
    payload["summary"]["effective_negative_prompt"] = _compact_text(payload["summary"]["effective_negative_prompt"], 1200)
    return payload


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_EMBEDDED_JSON_BYTES:
        raise ValueError("Portable Neo metadata exceeds embedded payload limit.")
    return raw


def _png_embed(data: bytes, raw: bytes) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        raise ValueError("Not a PNG image")
    itxt = PNG_KEYWORD + b"\x00\x00\x00\x00\x00" + raw
    chunk_type = b"iTXt"
    chunk = struct.pack(">I", len(itxt)) + chunk_type + itxt + struct.pack(">I", binascii.crc32(chunk_type + itxt) & 0xFFFFFFFF)
    pos = len(sig)
    output = bytearray(sig)
    inserted = False
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        end = pos + 12 + length
        if end > len(data):
            break
        ctype = data[pos+4:pos+8]
        cdata = data[pos+8:pos+8+length]
        is_neo = ctype == b"iTXt" and cdata.startswith(PNG_KEYWORD + b"\x00")
        if ctype == b"IEND" and not inserted:
            output.extend(chunk)
            inserted = True
        if not is_neo:
            output.extend(data[pos:end])
        pos = end
    if not inserted:
        raise ValueError("PNG IEND chunk not found")
    return bytes(output)


def _png_extract(data: bytes) -> dict[str, Any] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    found = None
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        end = pos + 12 + length
        if end > len(data):
            break
        ctype = data[pos+4:pos+8]
        cdata = data[pos+8:pos+8+length]
        if ctype == b"iTXt" and cdata.startswith(PNG_KEYWORD + b"\x00"):
            try:
                parts = cdata.split(b"\x00", 5)
                found = json.loads(parts[-1].decode("utf-8"))
            except Exception:
                pass
        pos = end
    return found if isinstance(found, dict) else None


def _jpeg_embed(data: bytes, raw: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Not a JPEG image")
    body = JPEG_PREFIX + raw
    if len(body) + 2 > 65535:
        raise ValueError("JPEG embedded Neo metadata is too large")
    segment = b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
    return data[:2] + segment + data[2:]


def _jpeg_extract(data: bytes) -> dict[str, Any] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    found = None
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos+1]
        if marker in (0xD9, 0xDA):
            break
        if marker in range(0xD0, 0xD8) or marker == 0x01:
            pos += 2
            continue
        length = struct.unpack(">H", data[pos+2:pos+4])[0]
        if length < 2 or pos + 2 + length > len(data):
            break
        payload = data[pos+4:pos+2+length]
        if marker == 0xFE and payload.startswith(JPEG_PREFIX):
            try:
                found = json.loads(payload[len(JPEG_PREFIX):].decode("utf-8"))
            except Exception:
                pass
        pos += 2 + length
    return found if isinstance(found, dict) else None


def _webp_embed(data: bytes, raw: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Not a WebP image")
    chunk = WEBP_CHUNK + struct.pack("<I", len(raw)) + raw + (b"\x00" if len(raw) % 2 else b"")
    out = bytearray(data + chunk)
    out[4:8] = struct.pack("<I", len(out) - 8)
    return bytes(out)


def _webp_extract(data: bytes) -> dict[str, Any] | None:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    pos = 12
    found = None
    while pos + 8 <= len(data):
        ctype = data[pos:pos+4]
        length = struct.unpack("<I", data[pos+4:pos+8])[0]
        start = pos + 8
        end = start + length
        if end > len(data):
            break
        if ctype == WEBP_CHUNK:
            try:
                found = json.loads(data[start:end].decode("utf-8"))
            except Exception:
                pass
        pos = end + (length % 2)
    return found if isinstance(found, dict) else None


def embed_portable_metadata_bytes(data: bytes, payload: dict[str, Any]) -> tuple[bytes, str]:
    raw = _payload_bytes(payload)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_embed(data, raw), "png_itxt"
    if data.startswith(b"\xff\xd8"):
        return _jpeg_embed(data, raw), "jpeg_comment"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_embed(data, raw), "webp_neo_chunk"
    raise ValueError("Portable Neo metadata currently supports PNG, JPEG, and WebP outputs.")


def extract_portable_metadata_bytes(data: bytes) -> dict[str, Any] | None:
    for reader in (_png_extract, _jpeg_extract, _webp_extract):
        payload = reader(data)
        if isinstance(payload, dict) and payload.get("neo_output_id"):
            return payload
    return None


def embed_portable_metadata_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    original = path.read_bytes()
    embedded, method = embed_portable_metadata_bytes(original, payload)
    path.write_bytes(embedded)
    return {
        "schema_version": PORTABLE_METADATA_SCHEMA,
        "embedded": True,
        "method": method,
        "neo_output_id": str(payload.get("neo_output_id") or ""),
        "size_bytes": len(embedded),
        "sha256": hashlib.sha256(embedded).hexdigest(),
    }


def file_integrity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    stat = path.stat()
    return {
        "size_bytes": len(data),
        "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
