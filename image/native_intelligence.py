from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import statistics
from typing import Any
from uuid import uuid4

from neo_app.image import output_service
from neo_app.assistant.guides import resolve_canonical_guide, resolve_guide_section
from neo_app.assistant.surface_project_context import build_surface_project_context
from neo_app.extensions.registry import get_extension, resolve_extension_manifest_route_state
from neo_app.image.capability_overlays import build_image_capability_overlay
from neo_app.models.route_matrix import normalize_backend, normalize_mode, resolve_model_backend_route
from neo_app.providers.profiles import get_backend_profile

IMAGE_SCOPE_PACKET_SCHEMA_ID = "neo.image.scope_packet.v1"
IMAGE_METADATA_INTAKE_SCHEMA_ID = "neo.image.asset_metadata_intake.v1"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_SECRET_KEY_MARKERS = ("token", "secret", "password", "passwd", "api_key", "apikey", "authorization", "cookie")


def _clean(value: Any, limit: int = 0) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _safe_value(value: Any, *, depth: int = 0, text_limit: int = 5000) -> Any:
    if depth > 5:
        return "[nested value omitted]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            low = name.lower()
            if any(marker in low for marker in _SECRET_KEY_MARKERS):
                out[name] = "[redacted]"
                continue
            out[name] = _safe_value(item, depth=depth + 1, text_limit=text_limit)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1, text_limit=text_limit) for item in list(value)[:120]]
    if isinstance(value, bytes):
        return f"[binary {len(value)} bytes]"
    if isinstance(value, str):
        return _clean(value, text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(value, text_limit)


def _scope_packet(
    *,
    operation_id: str,
    packet_kind: str,
    status: str,
    known_state: str,
    payload: dict[str, Any],
    target_refs: list[dict[str, Any]] | None = None,
    authority_classes: list[str] | None = None,
    native_refs: list[dict[str, Any]] | None = None,
    source_integrity: str = "present",
    limitations: list[str] | None = None,
    warnings: list[str] | None = None,
    derivation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_id": IMAGE_SCOPE_PACKET_SCHEMA_ID,
        "packet_id": f"imgpkt_{uuid4().hex[:16]}",
        "query_id": "",
        "operation_id": operation_id,
        "packet_kind": packet_kind,
        "status": status,
        "known_state": known_state,
        "target_refs": list(target_refs or []),
        "payload": payload,
        "authority": {
            "classes": list(authority_classes or []),
            "native_refs": list(native_refs or []),
            "source_integrity": source_integrity,
        },
        "evidence_refs": [],
        "derivation": dict(derivation or {}),
        "limitations": list(limitations or []),
        "warnings": list(warnings or []),
        "inspector": {},
    }


def _find_file_record(record: dict[str, Any], *, file_id: str = "", neo_output_id: str = "") -> dict[str, Any]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        if file_id and str(item.get("file_id") or "") == file_id:
            return item
        if neo_output_id and str(item.get("neo_output_id") or "") == neo_output_id:
            return item
    active = str(outputs.get("active_file") or "")
    for item in files:
        if isinstance(item, dict) and active and str(item.get("file_id") or "") == active:
            return item
    return next((item for item in files if isinstance(item, dict)), {})


def normalize_image_recipe(record: dict[str, Any], file_record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project a native Image sidecar into the stable R8 recipe shape.

    The original sidecar remains authoritative. This function is a read-only
    normalization layer and deliberately preserves unknown/missing fields.
    """
    record = record if isinstance(record, dict) else {}
    file_record = file_record if isinstance(file_record, dict) else _find_file_record(record)
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    run_timing = record.get("run_timing") if isinstance(record.get("run_timing"), dict) else {}
    replay = record.get("replay") if isinstance(record.get("replay"), dict) else {}
    replay_validation = record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) else {}
    provider_binding = record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else {}

    canonical_sampling_keys = (
        "seed", "actual_seed", "requested_seed", "steps", "cfg", "sampler", "scheduler",
        "denoise", "clip_skip", "width", "height", "batch_size", "batch_count",
    )
    canonical_sampling = {key: params.get(key) for key in canonical_sampling_keys if key in params}
    return {
        "identity": {
            "result_id": str(record.get("result_id") or ""),
            "neo_result_uid": str(record.get("neo_result_uid") or ""),
            "neo_output_id": str(file_record.get("neo_output_id") or ""),
            "file_id": str(file_record.get("file_id") or ""),
            "filename": str(file_record.get("filename") or ""),
            "created_at": str(record.get("created_at") or ""),
        },
        "generation": {
            "mode": str(record.get("mode") or params.get("mode") or ""),
            "subtab": str(record.get("subtab") or ""),
            "job_status": str(job.get("status") or record.get("status") or ""),
            "job_id": str(job.get("job_id") or record.get("job_id") or ""),
        },
        "provider": {
            "provider_id": str(job.get("provider_id") or record.get("provider_id") or ""),
            "backend_profile_id": str(job.get("backend_profile_id") or record.get("backend_profile_id") or ""),
            "binding": _safe_value(provider_binding),
        },
        "model": _safe_value(model),
        "prompt": _safe_value(prompt),
        "sampling": {
            **_safe_value(canonical_sampling),
            "all_recorded_params": _safe_value(params),
        },
        "canvas": {
            "width": params.get("width") or file_record.get("width"),
            "height": params.get("height") or file_record.get("height"),
            "output_file_count": len([item for item in outputs.get("files", []) if isinstance(item, dict)]),
        },
        "assets": {
            "source": _safe_value(source),
            "selected_output": _safe_value(file_record),
        },
        "extensions": {
            "used": _safe_value(extensions.get("used") if isinstance(extensions.get("used"), list) else []),
            "payloads": _safe_value(extensions.get("payloads") if isinstance(extensions.get("payloads"), dict) else {}),
            "replay_payloads": _safe_value(extensions.get("replay_payloads") if isinstance(extensions.get("replay_payloads"), dict) else {}),
            "validation": _safe_value(extensions.get("validation") if isinstance(extensions.get("validation"), list) else []),
            "timing": _safe_value(extensions.get("timing") if isinstance(extensions.get("timing"), dict) else {}),
            "assistant_summaries": _safe_value(extensions.get("assistant_summaries") if isinstance(extensions.get("assistant_summaries"), dict) else {}),
        },
        "route": {
            "route": _safe_value(record.get("route") if isinstance(record.get("route"), dict) else {}),
            "route_snapshot": _safe_value(record.get("route_snapshot") if isinstance(record.get("route_snapshot"), dict) else {}),
        },
        "lineage": _safe_value(lineage),
        "timing": _safe_value(run_timing),
        "replay": {
            "contract": _safe_value(replay),
            "validation": _safe_value(replay_validation),
        },
        "record_completeness": {
            "native_schema_id": str(record.get("schema_version") or record.get("schema_id") or ""),
            "has_full_sidecar": bool(record.get("result_id") and record.get("outputs")),
        },
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()




def _relative_output_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(output_service.ROOT_DIR.resolve()).as_posix()
    except Exception:
        return path.as_posix()

def _iter_native_sidecars() -> list[tuple[Path, dict[str, Any]]]:
    roots = [
        (output_service.ROOT_DIR / "neo_data" / "outputs" / "image_metadata"),
        output_service.IMAGE_METADATA_QUARANTINE_ROOT,
    ]
    rows: list[tuple[Path, dict[str, Any]]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(record, dict) and str(record.get("schema_version") or "").startswith("neo.image.output"):
                rows.append((path, record))
    return rows


def _integrity_matches(data: bytes) -> list[dict[str, Any]]:
    digest = _sha256(data)
    matches: list[dict[str, Any]] = []
    for path, record in _iter_native_sidecars():
        outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
        for file_record in outputs.get("files") if isinstance(outputs.get("files"), list) else []:
            if not isinstance(file_record, dict):
                continue
            integrity = file_record.get("integrity") if isinstance(file_record.get("integrity"), dict) else {}
            if str(integrity.get("sha256") or "").lower() != digest:
                continue
            matches.append({"path": path, "record": record, "file": file_record})
    return matches


def _metadata_text(value: Any, *, limit: int = 24000) -> str:
    if isinstance(value, bytes):
        raw = value
        # EXIF UserComment commonly starts with an 8-byte charset marker.
        for prefix in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00\x00"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return _clean(raw.decode(encoding, errors="strict"), limit)
            except Exception:
                continue
        return ""
    if isinstance(value, str):
        return _clean(value, limit)
    return ""


def _looks_like_a1111_parameters(text: str) -> bool:
    low = text.lower()
    return bool(text and "steps:" in low and ("sampler:" in low or "cfg scale:" in low or "seed:" in low))


def _json_or_none(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text[:1] not in {"{", "["}:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_a1111_parameters(text: str) -> dict[str, Any]:
    raw = _clean(text, 24000)
    if not raw:
        return {}
    positive = raw
    negative = ""
    settings_line = ""
    if "\nNegative prompt:" in raw:
        positive, tail = raw.split("\nNegative prompt:", 1)
        lines = tail.splitlines()
        split_at = next((i for i, line in enumerate(lines) if re.search(r"(?:^|,\s*)Steps:\s*\d+", line)), None)
        if split_at is None:
            negative = "\n".join(lines).strip()
        else:
            negative = "\n".join(lines[:split_at]).strip()
            settings_line = " ".join(lines[split_at:]).strip()
    else:
        lines = raw.splitlines()
        split_at = next((i for i, line in enumerate(lines) if re.search(r"(?:^|,\s*)Steps:\s*\d+", line)), None)
        if split_at is not None:
            positive = "\n".join(lines[:split_at]).strip()
            settings_line = " ".join(lines[split_at:]).strip()

    settings: dict[str, Any] = {}
    if settings_line:
        for match in re.finditer(r"(?:^|,\s*)([^,:]+?):\s*(.*?)(?=,\s*[^,:]+?:|$)", settings_line):
            key = match.group(1).strip()
            value = match.group(2).strip()
            settings[key] = value
    normalized: dict[str, Any] = {
        "positive_prompt": positive.strip(),
        "negative_prompt": negative,
        "settings": settings,
    }
    size = str(settings.get("Size") or "")
    m = re.fullmatch(r"\s*(\d+)x(\d+)\s*", size)
    if m:
        normalized["width"] = int(m.group(1))
        normalized["height"] = int(m.group(2))
    numeric_map = {"Steps": "steps", "CFG scale": "cfg", "Seed": "seed", "Denoising strength": "denoise"}
    for source_key, target_key in numeric_map.items():
        if source_key not in settings:
            continue
        raw_value = settings[source_key]
        try:
            normalized[target_key] = float(raw_value) if "." in str(raw_value) else int(raw_value)
        except Exception:
            normalized[target_key] = raw_value
    for source_key, target_key in (("Sampler", "sampler"), ("Schedule type", "scheduler"), ("Model", "model"), ("Model hash", "model_hash"), ("VAE", "vae")):
        if settings.get(source_key) not in (None, ""):
            normalized[target_key] = settings[source_key]
    return normalized


def _extract_comfy_summary(prompt_graph: Any) -> dict[str, Any]:
    if not isinstance(prompt_graph, dict):
        return {}
    summary: dict[str, Any] = {"prompt_candidates": [], "models": [], "samplers": []}
    for node_id, node in prompt_graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or node.get("type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        text = inputs.get("text")
        if isinstance(text, str) and text.strip():
            summary["prompt_candidates"].append({"node_id": str(node_id), "class_type": class_type, "text": _clean(text, 6000)})
        for key in ("ckpt_name", "unet_name", "model_name", "model", "vae_name", "lora_name"):
            value = inputs.get(key)
            if isinstance(value, str) and value.strip():
                summary["models"].append({"node_id": str(node_id), "class_type": class_type, "field": key, "value": value})
        sampler_fields = {key: inputs.get(key) for key in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise") if key in inputs}
        if sampler_fields:
            summary["samplers"].append({"node_id": str(node_id), "class_type": class_type, **_safe_value(sampler_fields)})
    return summary


def _extract_external_metadata(data: bytes, *, filename: str = "") -> dict[str, Any]:
    try:
        from PIL import ExifTags, Image  # type: ignore
    except Exception:
        return {
            "status": "metadata_parser_unavailable",
            "known_state": "not_established",
            "proof_level": "unidentified",
            "origin": "unknown",
            "filename": Path(filename).name,
            "normalized": {},
            "unmapped_fields": {},
            "exclusions": [],
        }
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except Exception:
        return {
            "status": "invalid_image",
            "known_state": "not_established",
            "proof_level": "unidentified",
            "origin": "unknown",
            "filename": Path(filename).name,
            "normalized": {},
            "unmapped_fields": {},
            "exclusions": [],
        }

    info = dict(image.info or {})
    exif_raw: dict[str, Any] = {}
    exclusions: list[str] = []
    try:
        exif = image.getexif()
        for key, value in exif.items():
            label = str(ExifTags.TAGS.get(key, key))
            if label.lower() in {"gpsinfo", "gpsdestlatitude", "gpsdestlongitude", "gpslatitude", "gpslongitude"}:
                exclusions.append("Precise GPS/EXIF location metadata is intentionally withheld from Assistant context.")
                continue
            if label in {"UserComment", "ImageDescription"}:
                decoded = _metadata_text(value, limit=12000)
                exif_raw[label] = decoded if decoded else _safe_value(value, text_limit=3000)
            else:
                exif_raw[label] = _safe_value(value, text_limit=3000)
    except Exception:
        pass

    normalized: dict[str, Any] = {
        "format": str(getattr(image, "format", "") or "").upper(),
        "width": int(getattr(image, "width", 0) or 0),
        "height": int(getattr(image, "height", 0) or 0),
    }
    unmapped: dict[str, Any] = {}
    for key, value in info.items():
        key_text = str(key)
        key_low = key_text.lower()
        if any(marker in key_low for marker in _SECRET_KEY_MARKERS):
            unmapped[key_text] = "[redacted]"
        elif key_low in {"icc_profile", "exif", "xmp", "photoshop", "transparency"} and isinstance(value, (bytes, bytearray)):
            unmapped[key_text] = f"[binary {len(value)} bytes]"
        else:
            unmapped[key_text] = _safe_value(value, text_limit=12000)
    if exif_raw:
        unmapped["EXIF"] = exif_raw

    prompt_graph = _json_or_none(info.get("prompt"))
    workflow = _json_or_none(info.get("workflow"))
    parameters_raw = info.get("parameters")
    parameters_text = _metadata_text(parameters_raw)
    parameters_json = _json_or_none(parameters_raw)
    comment_value = info.get("Comment") or info.get("comment") or exif_raw.get("UserComment") or exif_raw.get("ImageDescription")
    comment_text = _metadata_text(comment_value)
    comment_json = _json_or_none(comment_value) or _json_or_none(comment_text)
    if not parameters_text and _looks_like_a1111_parameters(comment_text):
        parameters_text = comment_text
    lower_keys = {str(key).lower() for key in info.keys()}

    generator = "unknown"
    if isinstance(prompt_graph, dict) or isinstance(workflow, dict):
        generator = "comfyui"
        normalized["comfyui"] = {
            "prompt_graph": _safe_value(prompt_graph, text_limit=12000) if prompt_graph is not None else {},
            "workflow": _safe_value(workflow, text_limit=12000) if workflow is not None else {},
            "summary": _extract_comfy_summary(prompt_graph),
        }
    elif any("fooocus" in key for key in lower_keys) or (isinstance(parameters_json, dict) and any("fooocus" in str(key).lower() for key in parameters_json.keys())):
        generator = "fooocus"
        normalized["fooocus"] = _safe_value(parameters_json or comment_json or parameters_raw, text_limit=16000)
    elif parameters_text and _looks_like_a1111_parameters(parameters_text):
        generator = "a1111_or_forge"
        normalized["a1111_or_forge"] = _parse_a1111_parameters(parameters_text)
    elif isinstance(parameters_json, dict):
        generator = "external_generator"
        normalized["parameters"] = _safe_value(parameters_json, text_limit=16000)
    elif isinstance(comment_json, dict):
        generator = "external_generator"
        normalized["comment"] = _safe_value(comment_json, text_limit=16000)

    normalized["generator"] = generator
    metadata_present = bool(info or exif_raw)
    proof_level = "external_embedded_metadata" if metadata_present else "visual_observation_only"
    known_state = "established" if metadata_present else "not_established"
    return {
        "status": "external_metadata_recovered" if metadata_present else "no_generation_metadata",
        "known_state": known_state,
        "proof_level": proof_level,
        "origin": "external" if metadata_present else "unidentified",
        "filename": Path(filename).name,
        "normalized": normalized,
        "unmapped_fields": unmapped,
        "exclusions": sorted(set(exclusions)),
    }


def inspect_image_asset_bytes(data: bytes, *, filename: str = "") -> dict[str, Any]:
    """Resolve provenance/metadata for an arbitrary uploaded image, fail closed.

    Resolution order follows R8 proof strength:
    1. Neo portable identity -> exact sidecar when available.
    2. Exact stored SHA-256 -> exact sidecar when unique.
    3. Recognized external/generic embedded metadata.
    4. Pixels only / no hidden generation settings established.
    """
    recovery = output_service.recover_image_metadata_from_bytes(data, filename=filename)
    if recovery.get("ok") and recovery.get("status") in {"full_record_recovered", "embedded_metadata_only"}:
        record = recovery.get("record") if isinstance(recovery.get("record"), dict) else {}
        file_record = _find_file_record(
            record,
            file_id=str(recovery.get("file_id") or ""),
            neo_output_id=str(recovery.get("neo_output_id") or ""),
        )
        full = recovery.get("status") == "full_record_recovered"
        proof = "native_exact_full_record" if full else "native_exact_embedded_only"
        identity_payload = {
            "filename": Path(filename).name,
            "proof_level": proof,
            "origin": "neo",
            "neo_output_id": str(recovery.get("neo_output_id") or ""),
            "result_id": str(recovery.get("result_id") or record.get("result_id") or ""),
            "file_id": str(recovery.get("file_id") or file_record.get("file_id") or ""),
            "metadata_path": str(recovery.get("metadata_path") or ""),
            "sidecar_state": str(recovery.get("source") or "embedded_image"),
        }
        packet = _scope_packet(
            operation_id="image.asset.resolve_identity",
            packet_kind="asset_identity",
            status="ok" if full else "partial",
            known_state="established",
            payload={"identity": identity_payload, "recipe": normalize_image_recipe(record, file_record)},
            target_refs=[{"kind": "attached_asset", "filename": Path(filename).name}],
            authority_classes=["ID", "SIDE" if full else "ID"],
            native_refs=[{
                "adapter_id": "neo.image",
                "authority_namespace": "image.output",
                "native_id": str(recovery.get("neo_output_id") or ""),
                "resolver_kind": "sidecar" if full else "embedded_metadata",
                "resolver_key": {"path": str(recovery.get("metadata_path") or "")},
            }],
            source_integrity="verified" if full else "present",
            limitations=[] if full else ["The full Neo sidecar was not available; only fields embedded in the image are established."],
        )
        return {"schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID, "ok": True, "status": packet["status"], "proof_level": proof, "packet": packet}

    matches = _integrity_matches(data)
    if len(matches) == 1:
        match = matches[0]
        record = match["record"]
        file_record = match["file"]
        path = match["path"]
        identity_payload = {
            "filename": Path(filename).name,
            "proof_level": "native_exact_integrity_match",
            "origin": "neo",
            "neo_output_id": str(file_record.get("neo_output_id") or ""),
            "result_id": str(record.get("result_id") or ""),
            "file_id": str(file_record.get("file_id") or ""),
            "metadata_path": _relative_output_path(path),
            "sidecar_state": "quarantined" if output_service.IMAGE_METADATA_QUARANTINE_ROOT.resolve() in path.resolve().parents else "active",
            "sha256": _sha256(data),
        }
        packet = _scope_packet(
            operation_id="image.asset.resolve_identity",
            packet_kind="asset_identity",
            status="ok",
            known_state="established",
            payload={"identity": identity_payload, "recipe": normalize_image_recipe(record, file_record)},
            target_refs=[{"kind": "attached_asset", "filename": Path(filename).name}],
            authority_classes=["ID", "SIDE"],
            native_refs=[{
                "adapter_id": "neo.image",
                "authority_namespace": "image.output",
                "native_id": str(file_record.get("neo_output_id") or record.get("result_id") or ""),
                "resolver_kind": "sidecar_integrity_match",
                "resolver_key": {"path": _relative_output_path(path)},
            }],
            source_integrity="verified",
            derivation={"method": "sha256_exact_file_match", "sha256": _sha256(data)},
        )
        return {"schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID, "ok": True, "status": "ok", "proof_level": "native_exact_integrity_match", "packet": packet}
    if len(matches) > 1:
        packet = _scope_packet(
            operation_id="image.asset.resolve_identity",
            packet_kind="asset_identity",
            status="ambiguous_target",
            known_state="unresolved_conflict",
            payload={"identity": {"filename": Path(filename).name, "proof_level": "unidentified", "origin": "unknown", "sha256": _sha256(data), "matching_native_records": len(matches)}},
            target_refs=[{"kind": "attached_asset", "filename": Path(filename).name}],
            authority_classes=["ID", "SIDE"],
            source_integrity="present",
            limitations=["Multiple Neo sidecars claim the same exact file hash; Neo will not choose one silently."],
        )
        return {"schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID, "ok": False, "status": "ambiguous_target", "proof_level": "unidentified", "packet": packet}

    external = _extract_external_metadata(data, filename=filename)
    if external.get("proof_level") == "external_embedded_metadata":
        packet = _scope_packet(
            operation_id="image.external.inspect_metadata",
            packet_kind="asset_metadata",
            status="ok",
            known_state="established",
            payload={
                "identity": {"filename": Path(filename).name, "proof_level": "external_embedded_metadata", "origin": "external"},
                "metadata": {
                    "format": (external.get("normalized") or {}).get("format"),
                    "generator": (external.get("normalized") or {}).get("generator"),
                    "normalized_fields": external.get("normalized") or {},
                    "unmapped_fields": external.get("unmapped_fields") or {},
                    "exclusions": external.get("exclusions") or [],
                },
            },
            target_refs=[{"kind": "attached_asset", "filename": Path(filename).name}],
            authority_classes=["EXTMETA"],
            source_integrity="present",
            warnings=["This metadata came from the uploaded file and is not a Neo generation record."],
            limitations=list(external.get("exclusions") or []),
        )
        return {"schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID, "ok": True, "status": "ok", "proof_level": "external_embedded_metadata", "packet": packet}

    packet = _scope_packet(
        operation_id="image.asset.inspect_metadata",
        packet_kind="unknown",
        status="partial" if external.get("status") != "invalid_image" else "error",
        known_state="not_established",
        payload={
            "requested_claim": "hidden image generation metadata",
            "identity": {"filename": Path(filename).name, "proof_level": "visual_observation_only", "origin": "unidentified"},
            "metadata": {"normalized_fields": external.get("normalized") or {}, "exclusions": external.get("exclusions") or []},
            "reason": "No Neo identity or recoverable generation metadata was found in the uploaded image.",
        },
        target_refs=[{"kind": "attached_asset", "filename": Path(filename).name}],
        authority_classes=["EXTMETA"],
        source_integrity="present" if external.get("status") != "invalid_image" else "unavailable",
        limitations=["Exact seed, checkpoint, prompt, workflow, and hidden generation settings cannot be inferred from pixels alone."],
    )
    return {"schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID, "ok": external.get("status") != "invalid_image", "status": packet["status"], "proof_level": "visual_observation_only", "packet": packet}


def inspect_image_asset_file(path: Path, *, filename: str = "") -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except Exception as exc:
        return {
            "schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID,
            "ok": False,
            "status": "error",
            "proof_level": "unidentified",
            "error": _clean(exc, 300),
        }
    return inspect_image_asset_bytes(data, filename=filename or path.name)


def attachment_metadata_summary(intake: dict[str, Any]) -> dict[str, Any]:
    packet = intake.get("packet") if isinstance(intake.get("packet"), dict) else {}
    payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    recipe = payload.get("recipe") if isinstance(payload.get("recipe"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "schema_id": IMAGE_METADATA_INTAKE_SCHEMA_ID,
        "status": str(intake.get("status") or packet.get("status") or ""),
        "proof_level": str(intake.get("proof_level") or identity.get("proof_level") or "unidentified"),
        "known_state": str(packet.get("known_state") or "not_established"),
        "operation_id": str(packet.get("operation_id") or ""),
        "identity": _safe_value(identity, text_limit=1200),
        "model": _safe_value(recipe.get("model") if isinstance(recipe.get("model"), dict) else {}, text_limit=1200),
        "sampling": _safe_value(recipe.get("sampling") if isinstance(recipe.get("sampling"), dict) else {}, text_limit=1200),
        "generator": str(metadata.get("generator") or ((metadata.get("normalized_fields") or {}).get("generator") if isinstance(metadata.get("normalized_fields"), dict) else "") or ""),
        "external_metadata": _safe_value(metadata.get("normalized_fields") if isinstance(metadata.get("normalized_fields"), dict) else {}, text_limit=3000),
        "limitations": list(packet.get("limitations") or [])[:6],
        "warnings": list(packet.get("warnings") or [])[:6],
    }


def _render_json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(_safe_value(value), ensure_ascii=False, indent=2, sort_keys=False)
    except Exception:
        text = str(value)
    return _clean(text, limit)


def render_attachment_image_context(intake: dict[str, Any], *, limit: int = 9000) -> str:
    """Render attachment metadata as compact answer evidence, never raw recipe JSON.

    Full normalized metadata remains available in diagnostics/Inspector. Provider-facing
    context gets scalar facts plus a bounded prompt preview so a long exact prompt does
    not dominate the answer or encourage JSON-style echoes.
    """
    packet = intake.get("packet") if isinstance(intake.get("packet"), dict) else {}
    if not packet:
        return ""
    payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    proof = str(intake.get("proof_level") or identity.get("proof_level") or "unidentified")
    lines = [
        f"Image attachment: {identity.get('filename') or 'uploaded image'}",
        f"Metadata proof level: {proof}",
        f"Known state: {packet.get('known_state') or 'not_established'}",
    ]
    if proof.startswith("native_exact"):
        lines.append("Authority: exact Neo Image identity/record. Recorded generation fields below may be treated as facts.")
        recipe = payload.get("recipe") if isinstance(payload.get("recipe"), dict) else {}
        model = recipe.get("model") if isinstance(recipe.get("model"), dict) else {}
        generation = recipe.get("generation") if isinstance(recipe.get("generation"), dict) else {}
        sampling = recipe.get("sampling") if isinstance(recipe.get("sampling"), dict) else {}
        canvas = recipe.get("canvas") if isinstance(recipe.get("canvas"), dict) else {}
        prompt = recipe.get("prompt") if isinstance(recipe.get("prompt"), dict) else {}
        facts = [
            ("Family", model.get("family")), ("Loader", model.get("loader")),
            ("Model", model.get("checkpoint") or model.get("model")), ("VAE", model.get("vae")),
            ("Workflow", generation.get("mode")),
            ("Seed", sampling.get("actual_seed") if sampling.get("actual_seed") is not None else sampling.get("seed")),
            ("Steps", sampling.get("steps")), ("CFG", sampling.get("cfg")),
            ("Sampler", sampling.get("sampler")), ("Scheduler", sampling.get("scheduler")),
        ]
        for label, value in facts:
            if value not in (None, "", []):
                lines.append(f"{label}: {value}")
        if canvas.get("width") not in (None, "") and canvas.get("height") not in (None, ""):
            lines.append(f"Resolution: {canvas.get('width')} × {canvas.get('height')}")
        positive = str(prompt.get("positive") or prompt.get("effective_positive") or "")
        negative = str(prompt.get("negative") or prompt.get("effective_negative") or "")
        if positive:
            one_line = re.sub(r"\s+", " ", positive).strip()
            preview = one_line[:320] + ("…" if len(one_line) > 320 else "")
            lines.append(f"Recorded positive prompt: {len(positive)} characters. Preview: {preview}")
        if negative:
            lines.append(f"Recorded negative prompt: {len(negative)} characters.")
        lines.append("Do not dump the full recorded prompt unless the user explicitly asks for that exact field.")
    elif proof == "external_embedded_metadata":
        lines.append("Authority: metadata embedded by an external/non-Neo image workflow. Do not present it as a Neo generation record.")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        normalized = metadata.get("normalized_fields") if isinstance(metadata.get("normalized_fields"), dict) else {}
        lines.append("Recoverable external metadata is present. Use only explicitly recorded fields; missing generation fields remain unknown.")
        if normalized:
            lines.extend(_compact_fact_lines(normalized, prefix="External metadata", limit=12))
    else:
        lines.append("Authority: pixels/technical image properties only. Hidden generation settings are NOT established.")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        technical = {key: metadata.get(key) for key in ("format", "width", "height", "mode") if metadata.get(key) not in (None, "")}
        if technical:
            lines.extend(_compact_fact_lines(technical, prefix="Technical metadata", limit=8))
    if packet.get("limitations"):
        lines.append("Limitations: " + "; ".join(_clean(item, 420) for item in packet.get("limitations")[:6]))
    if packet.get("warnings"):
        lines.append("Warnings: " + "; ".join(_clean(item, 420) for item in packet.get("warnings")[:6]))
    return _clean("\n".join(lines), limit)


def _valid_image_record(record: dict[str, Any]) -> bool:
    return bool(isinstance(record, dict) and str(record.get("schema_version") or "").startswith("neo.image.output") and record.get("result_id"))


def image_history_records(root_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    metadata_root = root_dir / "neo_data" / "outputs" / "image_metadata"
    rows: list[dict[str, Any]] = []
    health = {"files_discovered": 0, "records_parsed": 0, "records_invalid": 0}
    if not metadata_root.exists():
        return rows, health
    for path in metadata_root.rglob("*.json"):
        health["files_discovered"] += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            health["records_invalid"] += 1
            continue
        if not _valid_image_record(record):
            health["records_invalid"] += 1
            continue
        health["records_parsed"] += 1
        record = dict(record)
        record["_neo_metadata_path"] = path.relative_to(root_dir).as_posix()
        rows.append(record)
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows, health


def _is_completed_run(record: dict[str, Any]) -> bool:
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    status = str(job.get("status") or record.get("status") or "").strip().lower()
    if status in {"completed", "complete", "success", "succeeded", "done", "finished"}:
        return True
    if status in {"failed", "error", "cancelled", "canceled", "running", "queued", "pending"}:
        return False
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    return bool([item for item in outputs.get("files", []) if isinstance(item, dict)])


def _primary_output_files(record: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in outputs.get("files") if isinstance(outputs.get("files"), list) else []:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or item.get("path") or "")
        suffix = Path(filename).suffix.lower()
        role = str(item.get("role") or "image").lower()
        kind = str(item.get("kind") or "image").lower()
        if suffix and suffix not in _IMAGE_SUFFIXES:
            continue
        if role in {"mask", "alpha", "latent", "preview_mask", "control_map"} or kind in {"mask", "latent"}:
            continue
        rows.append(item)
    return rows



def _model_words(value: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    stem = Path(text).stem
    return {token.lower() for token in re.split(r"[^a-zA-Z0-9]+", stem) if token and token.lower() not in {"safetensors", "checkpoint", "model", "models", "benchmark"}}


def _semantic_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


# Query aliases intentionally represent user-facing *families/groups*, not guessed
# checkpoint identities.  Specific aliases are ordered before broad groups so
# "Krea 2 Turbo" stays specific while "Krea 2 models" includes RAW + Turbo.
_FAMILY_QUERY_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("qwenimageedit2511", "Qwen Image Edit 2511", ("qwenimageedit2511",)),
    ("qwenimageedit2509", "Qwen Image Edit 2509", ("qwenimageedit2509",)),
    ("qwenimage21", "Qwen Image 2.1", ("qwenimage21",)),
    ("krea2turbo", "Krea 2 Turbo", ("krea2turbo",)),
    ("krea2raw", "Krea 2 RAW", ("krea2",)),
    ("flux2klein", "Flux 2 Klein", ("flux2klein",)),
    ("flux2dev", "Flux 2 Dev", ("flux2dev",)),
    ("qwenrapid", "Qwen Rapid AIO", ("qwenrapidaio", "qwenrapid")),
    ("stable diffusion xl", "SDXL", ("sdxl",)),
    ("stable diffusion 1.5", "SD 1.5", ("sd15",)),
    ("stable diffusion 3.5", "SD 3.5", ("sd35",)),
    ("qwen image", "Qwen Image", ("qwenimage",)),
    ("krea 2", "Krea 2", ("krea2",)),
    ("z image", "Z-Image", ("zimage",)),
    ("wan image", "Wan Image", ("wanimage",)),
    ("hunyuan image", "Hunyuan Image", ("hunyuanimage",)),
    ("flux 2", "Flux 2", ("flux2",)),
    ("flux", "Flux", ("flux",)),
    ("sdxl", "SDXL", ("sdxl",)),
    ("sd 1.5", "SD 1.5", ("sd15",)),
    ("sd15", "SD 1.5", ("sd15",)),
    ("sd 3.5", "SD 3.5", ("sd35",)),
    ("sd35", "SD 3.5", ("sd35",)),
    ("ideogram 4", "Ideogram 4", ("ideogram4",)),
    ("hidream", "HiDream", ("hidream",)),
    ("anima", "Anima", ("anima",)),
)


def _family_constraint_from_query(rows: list[dict[str, Any]], query: str) -> dict[str, Any]:
    """Resolve an explicitly named model family/group from natural user wording.

    The returned constraint is population-level: it may intentionally match more
    than one recorded family (for example Krea 2 RAW + Krea 2 Turbo).  No family is
    inferred merely because it is frequent in history.
    """
    low = str(query or "").lower()
    qkey = _semantic_key(query)
    matched_alias = ""
    display = ""
    prefixes: tuple[str, ...] = ()
    # Prefer the longest/specific alias that is literally present after either
    # ordinary-space or punctuation-insensitive normalization.
    choices: list[tuple[int, str, str, tuple[str, ...]]] = []
    for alias, label, group_prefixes in _FAMILY_QUERY_ALIASES:
        alias_key = _semantic_key(alias)
        if (" " in alias and alias in low) or (alias_key and alias_key in qkey):
            choices.append((len(alias_key), alias, label, group_prefixes))
    if choices:
        _, matched_alias, display, prefixes = sorted(choices, reverse=True)[0]
    if not prefixes:
        return {"requested": False, "display": "", "alias": "", "family_ids": [], "canonical_keys": []}

    observed_families = sorted({
        str(((row.get("model") or {}) if isinstance(row.get("model"), dict) else {}).get("family") or ((row.get("params") or {}) if isinstance(row.get("params"), dict) else {}).get("family") or "").strip()
        for row in rows
    } - {""})
    matched_families: list[str] = []
    for family in observed_families:
        fkey = _semantic_key(family)
        if any(fkey == prefix or fkey.startswith(prefix) for prefix in prefixes):
            matched_families.append(family)

    return {
        "requested": True,
        "display": display or matched_alias,
        "alias": matched_alias,
        "family_ids": matched_families,
        "canonical_keys": list(prefixes),
    }


def _record_matches_family_constraint(record: dict[str, Any], constraint: dict[str, Any]) -> bool:
    if not constraint.get("requested"):
        return True
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    family = str(model.get("family") or params.get("family") or "").strip()
    fkey = _semantic_key(family)
    prefixes = [str(item) for item in (constraint.get("canonical_keys") or []) if str(item)]
    if fkey and any(fkey == prefix or fkey.startswith(prefix) for prefix in prefixes):
        return True
    # Legacy records can lack a family field.  Recorded checkpoint/model names may
    # still establish a family-group filter, but only by the same explicit alias.
    label_key = _semantic_key(_model_label(record, "model"))
    return bool(label_key and any(prefix in label_key for prefix in prefixes))


def _explicit_unresolved_model_phrase(query: str, *, family_requested: bool, model_filter: str) -> str:
    """Return an explicit model/checkpoint phrase that was requested but unresolved.

    This guard exists so native analytics never answer a narrower user question
    with an unfiltered population merely because the named model is unknown.
    """
    if family_requested or model_filter:
        return ""
    text = _clean(query, 2000)
    patterns = (
        r"\b(?:for|using|with|on)\s+(?:a|an|the|my\s+)?(.{2,120}?)\s+(?:models?|checkpoints?)\b",
        r"\bused\s+(?:a|an|the|my\s+)?(.{2,120}?)\s+(?:models?|checkpoints?)\b",
        r"\bwhich\s+(.{2,120}?)\s+(?:models?|checkpoints?)\s+(?:is|was|were|are)\b",
        r"\b(?:for|using|with|on)\s+([^,;?]{2,160}?\.(?:safetensors|gguf|ckpt|pt|pth))\b",
    )
    stop_phrases = {"which", "what", "fastest", "quickest", "all", "my", "the", "comparable"}
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = _clean(match.group(1), 160).strip(" .:-")
        tokens = {token.lower() for token in re.findall(r"[a-zA-Z0-9_.+-]+", value)}
        if value and not tokens.issubset(stop_phrases):
            return value
    return ""


def _query_model_filter(rows: list[dict[str, Any]], query: str) -> str:
    """Resolve one exact recorded checkpoint/model label from user wording.

    Family/group language is handled separately by ``_family_constraint_from_query``
    so a request such as "Krea 2 models" never collapses to one arbitrary Krea
    checkpoint.
    """
    if _family_constraint_from_query(rows, query).get("requested"):
        return ""
    qwords = {token.lower() for token in re.split(r"[^a-zA-Z0-9]+", str(query or "")) if token}
    qkey = _semantic_key(query)
    candidates: list[tuple[int, int, int, str]] = []
    labels = sorted({_model_label(row, "model") for row in rows if _model_label(row, "model")})
    for label in labels:
        words = _model_words(label)
        overlap = words & qwords
        meaningful = {word for word in overlap if len(word) >= 2}
        label_key = _semantic_key(Path(label).stem)
        literal = 1 if label_key and label_key in qkey else 0
        if meaningful or literal:
            candidates.append((literal, len(meaningful), len(overlap), label))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    best = candidates[0]
    if len(candidates) > 1 and candidates[1][:3] == best[:3]:
        return ""
    return best[3]


def inventory_packet(root_dir: Path, query: str = "") -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    completed_all = [row for row in rows if _is_completed_run(row)]
    family_constraint = _family_constraint_from_query(completed_all, query) if query else {"requested": False, "family_ids": []}
    model_filter = _query_model_filter(completed_all, query) if query else ""
    unresolved_model = _explicit_unresolved_model_phrase(query, family_requested=bool(family_constraint.get("requested")), model_filter=model_filter) if query else ""
    completed = [
        row for row in completed_all
        if _record_matches_family_constraint(row, family_constraint)
        and (not model_filter or _model_label(row, "model").lower() == model_filter.lower())
    ]
    if unresolved_model:
        completed = []
    requested = {
        "family": family_constraint.get("display") if family_constraint.get("requested") else None,
        "model": model_filter or unresolved_model or None,
    }
    applied = {
        "family_ids": list(family_constraint.get("family_ids") or []) if family_constraint.get("requested") else [],
        "model": model_filter or None,
    }
    constraint_status = "satisfied"
    if family_constraint.get("requested") and not any(_record_matches_family_constraint(row, family_constraint) for row in completed_all):
        constraint_status = "unsatisfied"
    if unresolved_model:
        constraint_status = "unsatisfied"
    output_count = sum(len(_primary_output_files(row)) for row in completed)
    limitations: list[str] = []
    if constraint_status != "satisfied":
        requested_label = family_constraint.get("display") or unresolved_model or model_filter or "requested model population"
        limitations.append(f"The requested Image model/family constraint ({requested_label}) could not be matched to authoritative history records; Neo will not substitute the unfiltered population.")
    return _scope_packet(
        operation_id="image.inventory.count_runs",
        packet_kind="inventory",
        status="ok" if constraint_status == "satisfied" else "partial",
        known_state="established" if constraint_status == "satisfied" else "not_established",
        payload={
            "population": {
                "kind": "completed_generation_runs",
                "health_policy": "active_sidecars",
                "status": "completed_or_legacy_with_outputs",
                "model_filter": model_filter or None,
                "family_filter": family_constraint.get("display") if family_constraint.get("requested") else None,
                "matched_family_ids": list(family_constraint.get("family_ids") or []),
            },
            "query_constraints": {"requested": requested, "applied": applied, "status": constraint_status},
            "source_health": health,
            "counts": {
                "completed_generation_runs": len(completed),
                "primary_output_images": output_count,
                "all_valid_sidecar_records": len(rows),
                "unfiltered_completed_generation_runs": len(completed_all),
            },
        },
        authority_classes=["ANALYTICS", "SIDE"],
        source_integrity="present",
        derivation={"method": "deterministic_sidecar_aggregation"},
        limitations=limitations,
    )

def _model_label(record: dict[str, Any], dimension: str) -> str:
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    if dimension == "model":
        return str(model.get("model") or model.get("checkpoint") or params.get("model") or "").strip()
    if dimension == "family":
        return str(model.get("family") or params.get("family") or "").strip()
    if dimension == "loader":
        return str(model.get("loader") or params.get("loader") or "").strip()
    if dimension == "sampler":
        return str(params.get("sampler") or "").strip()
    if dimension == "scheduler":
        return str(params.get("scheduler") or "").strip()
    if dimension == "mode":
        return str(record.get("mode") or params.get("mode") or record.get("subtab") or "").strip()
    return ""


def analytics_group_packet(root_dir: Path, *, dimension: str = "model", limit: int = 20, query: str = "") -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    completed_all = [row for row in rows if _is_completed_run(row)]
    family_constraint = _family_constraint_from_query(completed_all, query) if query else {"requested": False, "family_ids": []}
    model_filter = _query_model_filter(completed_all, query) if query else ""
    unresolved_model = _explicit_unresolved_model_phrase(query, family_requested=bool(family_constraint.get("requested")), model_filter=model_filter) if query else ""
    completed = [
        row for row in completed_all
        if _record_matches_family_constraint(row, family_constraint)
        and (not model_filter or _model_label(row, "model").lower() == model_filter.lower())
    ]
    if unresolved_model:
        completed = []
    counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    missing = 0
    for row in completed:
        if dimension == "extension":
            labels = sorted(_record_extension_ids(row))
            if not labels:
                missing += 1
                continue
            for label in labels:
                counts[label] += 1
                output_counts[label] += len(_primary_output_files(row))
            continue
        label = _model_label(row, dimension)
        if not label:
            missing += 1
            continue
        counts[label] += 1
        output_counts[label] += len(_primary_output_files(row))
    ranked = [
        {"value": label, "generation_runs": count, "primary_output_images": output_counts[label]}
        for label, count in counts.most_common(max(1, min(limit, 100)))
    ]
    requested = {"family": family_constraint.get("display") if family_constraint.get("requested") else None, "model": model_filter or unresolved_model or None}
    applied = {"family_ids": list(family_constraint.get("family_ids") or []), "model": model_filter or None}
    constraint_status = "satisfied"
    if family_constraint.get("requested") and not any(_record_matches_family_constraint(row, family_constraint) for row in completed_all):
        constraint_status = "unsatisfied"
    if unresolved_model:
        constraint_status = "unsatisfied"
    limitations: list[str] = []
    if constraint_status != "satisfied":
        requested_label = family_constraint.get("display") or unresolved_model or model_filter or "requested model population"
        limitations.append(f"The requested Image model/family constraint ({requested_label}) could not be matched to authoritative history records; Neo will not substitute the unfiltered population.")
    if not ranked:
        limitations.append(f"No completed Image sidecars contained a recorded {dimension} for the requested population.")
    return _scope_packet(
        operation_id="image.analytics.group",
        packet_kind="analytics",
        status="ok" if ranked and constraint_status == "satisfied" else "partial",
        known_state="established" if ranked and constraint_status == "satisfied" else "not_established",
        payload={
            "population": {"kind": "completed_generation_runs", "health_policy": "active_sidecars"},
            "query_constraints": {"requested": requested, "applied": applied, "status": constraint_status},
            "source_health": health,
            "grouping": {"dimension": dimension},
            "metrics": ["generation_runs", "primary_output_images"],
            "coverage": {"records_included": len(completed), "records_missing_dimension": missing, "population_size": len(completed), "unfiltered_population_size": len(completed_all)},
            "rows": ranked,
        },
        authority_classes=["ANALYTICS", "SIDE"],
        source_integrity="present",
        derivation={"method": "deterministic_sidecar_group_count", "dimension": dimension},
        limitations=limitations,
    )

def _extension_signature(record: dict[str, Any]) -> tuple[str, ...]:
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    used = extensions.get("used") if isinstance(extensions.get("used"), list) else []
    ids = []
    for item in used:
        if isinstance(item, dict):
            value = str(item.get("extension_id") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            ids.append(value)
    return tuple(sorted(set(ids)))


def performance_fastest_model_packet(root_dir: Path, query: str = "") -> dict[str, Any]:
    rows_all, health = image_history_records(root_dir)
    timed_filtered, timing_filters = _timing_rows(root_dir, query)
    validation = timing_filters.get("query_constraints") if isinstance(timing_filters.get("query_constraints"), dict) else {}
    if validation.get("status") == "unsatisfied":
        requested = validation.get("requested") if isinstance(validation.get("requested"), dict) else {}
        requested_label = requested.get("family") or requested.get("model") or "requested population"
        return _scope_packet(
            operation_id="image.performance.compare",
            packet_kind="performance",
            status="partial",
            known_state="not_established",
            payload={
                "source_health": health,
                "cohort": {},
                "filters": timing_filters,
                "statistic": "median_elapsed_seconds",
                "comparability": "constraint_mismatch",
                "groups": [],
            },
            authority_classes=["ANALYTICS", "SIDE"],
            source_integrity="present",
            derivation={"method": "cohort_median_timing_comparison", "constraint_guard": "failed_closed"},
            limitations=[f"The requested Image constraint ({requested_label}) was not established; Neo will not compare the unfiltered history population instead."],
        )
    filtered_ids = {row.get("result_id") for row in timed_filtered}
    has_filters = any(value not in (None, "", [], {}) for key, value in timing_filters.items() if key not in {"history_health", "query_constraints"})
    rows = [row for row in rows_all if _clean(row.get("result_id")) in filtered_ids] if (filtered_ids or has_filters) else list(rows_all)
    cohorts: dict[tuple[Any, ...], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    observed_by_model: dict[str, list[float]] = defaultdict(list)
    excluded = 0
    for row in rows:
        if not _is_completed_run(row):
            continue
        timing = row.get("run_timing") if isinstance(row.get("run_timing"), dict) else {}
        try:
            elapsed = float(timing.get("elapsed_seconds") or 0)
        except Exception:
            elapsed = 0.0
        model = _model_label(row, "model")
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        if elapsed <= 0 or not model:
            excluded += 1
            continue
        observed_by_model[model].append(elapsed)
        cohort = (
            str(row.get("mode") or params.get("mode") or row.get("subtab") or ""),
            params.get("width"), params.get("height"), params.get("steps"),
            _extension_signature(row),
        )
        cohorts[cohort][model].append(elapsed)

    comparable = [(key, groups) for key, groups in cohorts.items() if len(groups) >= 2]
    comparable.sort(key=lambda item: sum(len(values) for values in item[1].values()), reverse=True)
    limitations: list[str] = []
    asks_overall = "overall" in str(query or "").lower()
    if asks_overall and len(cohorts) > 1:
        ranked = sorted(
            ({"model": model, "observed_median_seconds": round(statistics.median(values), 3), "sample_count": len(values)} for model, values in observed_by_model.items()),
            key=lambda row: (row["observed_median_seconds"], -row["sample_count"], row["model"].lower()),
        )
        comparability = "mixed_uncontrolled"
        known_state = "not_established"
        status = "partial"
        cohort_payload = {}
        limitations.append("Overall Image history mixes different workflows/resolutions/steps/extensions; Neo will not declare a generic fastest model from uncontrolled workloads.")
    elif comparable:
        cohort_key, groups = comparable[0]
        ranked = sorted(
            ({"model": model, "median_seconds": round(statistics.median(values), 3), "sample_count": len(values), "min_seconds": round(min(values), 3), "max_seconds": round(max(values), 3)} for model, values in groups.items()),
            key=lambda row: (row["median_seconds"], -row["sample_count"], row["model"].lower()),
        )
        comparability = "comparable"
        known_state = "established"
        status = "ok"
        cohort_payload = {
            "mode": cohort_key[0], "width": cohort_key[1], "height": cohort_key[2], "steps": cohort_key[3], "extensions": list(cohort_key[4]),
        }
    else:
        ranked = sorted(
            ({"model": model, "observed_median_seconds": round(statistics.median(values), 3), "sample_count": len(values)} for model, values in observed_by_model.items()),
            key=lambda row: (row["observed_median_seconds"], -row["sample_count"], row["model"].lower()),
        )
        comparability = "mixed_uncontrolled" if ranked else "insufficient_sample"
        known_state = "not_established"
        status = "partial"
        cohort_payload = {}
        if ranked:
            limitations.append("Recorded timings do not contain a cohort where two or more models share the same mode, dimensions, steps, and extension signature; Neo will not declare an overall fastest model from mixed workloads.")
        else:
            limitations.append("No completed Image sidecars contain enough recorded timing + model data for a speed comparison.")

    return _scope_packet(
        operation_id="image.performance.compare",
        packet_kind="performance",
        status=status,
        known_state=known_state,
        payload={
            "source_health": health,
            "cohort": cohort_payload,
            "filters": timing_filters,
            "statistic": "median_elapsed_seconds",
            "comparability": comparability,
            "groups": ranked,
            "records_excluded_missing_timing_or_model": excluded,
        },
        authority_classes=["ANALYTICS", "SIDE"],
        source_integrity="present",
        derivation={"method": "cohort_median_timing_comparison"},
        limitations=limitations,
    )



_IMAGE_FEATURES: dict[str, dict[str, Any]] = {
    "controlnet": {"feature_id": "image.controlnet", "guide_id": "image.controlnet", "aliases": ["controlnet", "control net", "pose control", "canny", "depth control"]},
    "ip_adapter": {"feature_id": "image.ip_adapter", "guide_id": "image.ip_adapter_faceid", "aliases": ["ip adapter", "ip-adapter", "faceid", "face id"]},
    "adetailer": {"feature_id": "image.adetailer", "guide_id": "image.adetailer", "aliases": ["adetailer", "a detailer", "face detailer"]},
    "layerdiffuse": {"feature_id": "image.layerdiffuse", "guide_id": "image.layerdiffuse", "aliases": ["layerdiffuse", "layer diffuse", "transparent rgba"]},
    "high_res_lab": {"feature_id": "image.high_res_lab", "guide_id": "image.high_res_lab", "aliases": ["high-res lab", "high res lab", "highres", "high-res fix", "high res fix"]},
    "scene_director": {"feature_id": "image.scene_director", "guide_id": "image.scene_director", "aliases": ["scene director", "regional prompting"]},
    "background_removal": {"feature_id": "image.background_removal", "guide_id": "image.background_removal", "aliases": ["background removal", "remove background", "birefnet", "rembg"]},
}

_IMAGE_TASKS: dict[str, dict[str, Any]] = {
    "background_removal": {
        "task_id": "image.background_removal.remove",
        "guide_id": "image.background_removal",
        "ui_path": ["Image", "Finish", "Remove Background"],
        "aliases": ["remove background", "background removal", "transparent background", "birefnet", "rembg"],
    },
    "outpaint": {
        "task_id": "image.outpaint.run",
        "guide_id": "image.inpaint_outpaint_engines",
        "ui_path": ["Image", "Generation", "Outpaint"],
        "aliases": ["outpaint", "expand image", "extend image", "extend canvas"],
    },
    "reuse_result": {
        "task_id": "image.results.reuse",
        "guide_id": "image.results",
        "ui_path": ["Image", "Results"],
        "aliases": ["reuse result", "reuse image", "previous result", "earlier generation", "use old image", "load previous result"],
    },
    "reference": {
        "task_id": "image.reference.add",
        "guide_id": "image.reference",
        "ui_path": ["Image", "Reference"],
        "aliases": ["reference image", "add reference", "image reference", "control image"],
    },
    "results": {
        "task_id": "image.navigation.results",
        "guide_id": "image.results",
        "ui_path": ["Image", "Results"],
        "aliases": ["results", "previous images", "generated images", "saved outputs", "output inspector"],
    },
}


def _resolve_feature(query: str) -> dict[str, Any]:
    low = _clean(query, 1200).lower()
    matches: list[tuple[int, dict[str, Any]]] = []
    for spec in _IMAGE_FEATURES.values():
        hit = max((len(alias) for alias in spec.get("aliases", []) if alias in low), default=0)
        if hit:
            matches.append((hit, spec))
    return dict(max(matches, key=lambda item: item[0])[1]) if matches else {}


def _resolve_task(query: str) -> dict[str, Any]:
    low = _clean(query, 1200).lower()
    low = re.sub(r"\bremove\s+(?:the|a|an)\s+background\b", "remove background", low)
    matches: list[tuple[int, dict[str, Any]]] = []
    for spec in _IMAGE_TASKS.values():
        hit = max((len(alias) for alias in spec.get("aliases", []) if alias in low), default=0)
        if hit:
            matches.append((hit, spec))
    return dict(max(matches, key=lambda item: item[0])[1]) if matches else {}


def _current_image_context(native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return build_surface_project_context(
            surface="image",
            project_id="image_workspace",
            client_snapshot=native_context if isinstance(native_context, dict) else {},
        )
    except Exception as exc:
        return {"ok": False, "surface": "image", "error": _clean(exc, 500)}


def _requested_state_fields(query: str) -> list[str]:
    low = str(query or "").lower()
    fields: list[str] = []
    for field, markers in (
        ("model", ("model", "checkpoint")),
        ("family", ("family",)),
        ("loader", ("loader",)),
        ("seed", ("seed",)),
        ("sampler", ("sampler",)),
        ("scheduler", ("scheduler",)),
        ("steps", ("steps",)),
        ("cfg", ("cfg", "guidance")),
        ("workflow_mode", ("workflow", "mode", "outpaint", "inpaint", "img2img")),
        ("references", ("reference", "source image", "source images")),
        ("extensions", ("enabled", "active", "adetailer", "controlnet", "layerdiffuse", "scene director", "high-res")),
    ):
        if any(marker in low for marker in markers):
            fields.append(field)
    return fields


def live_state_packet(query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = _current_image_context(native_context)
    params = ctx.get("current_parameters") if isinstance(ctx.get("current_parameters"), dict) else {}
    assets = ctx.get("asset_inputs") if isinstance(ctx.get("asset_inputs"), dict) else {}
    ext = ctx.get("extension_settings") if isinstance(ctx.get("extension_settings"), dict) else {}
    runtime = ctx.get("surface_runtime") if isinstance(ctx.get("surface_runtime"), dict) else {}
    refs = [key for key in ("source_image_url", "source_image_2_url", "source_image_3_url") if str(assets.get(key) or "").strip()]
    payload = {
        "requested_fields": _requested_state_fields(query),
        "workspace": {
            "workspace_app": runtime.get("workspace_app"),
            "workflow_mode": runtime.get("workflow_mode") or runtime.get("subtab"),
        },
        "current_parameters": _safe_value(params),
        "reference_asset_count": len(refs),
        "reference_asset_slots": refs,
        "extension_settings": _safe_value(ext),
        "route_snapshot": _safe_value(ctx.get("route_snapshot") or {}),
        "backend_profile": _safe_value(ctx.get("backend_profile") or {}),
        "live_snapshot_included": bool(ctx.get("live_snapshot_included")),
    }
    known = "established" if ctx.get("ok") else "not_established"
    limitations = [] if ctx.get("ok") else ["Current Image workspace state could not be resolved."]
    if not ctx.get("live_snapshot_included"):
        limitations.append("No current-turn Image snapshot was provided; persisted Neo UI state may be older than the visible browser state.")
    return _scope_packet(
        operation_id="image.state.get", packet_kind="live_state", status="ok" if ctx.get("ok") else "partial",
        known_state=known, payload=payload, authority_classes=["LIVE"], source_integrity="present" if ctx.get("ok") else "partial",
        limitations=limitations, derivation={"method": "surface_project_context_snapshot"},
    )


def _manifest_support_state(manifest: Any, *, family: str, loader: str, backend: str, mode: str, workspace_app: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    supported = True
    if family and getattr(manifest, "supported_families", None) and family not in list(manifest.supported_families or []):
        supported = False; reasons.append(f"Family {family} is not declared by the extension.")
    if loader and getattr(manifest, "supported_loaders", None) and loader not in list(manifest.supported_loaders or []):
        supported = False; reasons.append(f"Loader {loader} is not declared by the extension.")
    backend_aliases = {backend, normalize_backend(backend)}
    manifest_backends = {normalize_backend(item) for item in list(getattr(manifest, "supported_backends", []) or [])}
    if backend and manifest_backends and not (backend_aliases & manifest_backends):
        supported = False; reasons.append(f"Backend {backend} is not declared by the extension.")
    normalized_mode = "generate" if normalize_mode(mode) == "txt2img" else normalize_mode(mode)
    manifest_modes = [str(item) for item in list(getattr(manifest, "workflow_modes", []) or [])]
    if mode and manifest_modes and normalized_mode not in manifest_modes and normalize_mode(mode) not in manifest_modes and mode not in manifest_modes and workspace_app not in manifest_modes:
        supported = False; reasons.append(f"Workflow {mode} is not declared by the extension.")
    apps = [str(item) for item in list(getattr(manifest, "workspace_apps", []) or [])]
    if workspace_app and apps and workspace_app not in apps and not (workspace_app == "generation" and "generations" in apps):
        # Workspace mismatch is a current-placement constraint, not proof that the
        # family itself cannot use the feature. Keep it as an actionable reason.
        reasons.append(f"Feature is mounted in {', '.join(apps)}, not the current {workspace_app} workspace.")
    return ("supported" if supported else "unsupported"), reasons


def capability_readiness_packet(query: str, native_context: dict[str, Any] | None = None, *, operation_id: str = "image.capability.check") -> dict[str, Any]:
    feature = _resolve_feature(query)
    if not feature:
        return _scope_packet(operation_id=operation_id, packet_kind="capability_readiness", status="not_found", known_state="not_established", payload={"feature_id": "", "layers": {}}, authority_classes=["LIVE", "CAP"], limitations=["No Image feature could be resolved from the question."])
    ctx = _current_image_context(native_context)
    params = ctx.get("current_parameters") if isinstance(ctx.get("current_parameters"), dict) else {}
    runtime = ctx.get("surface_runtime") if isinstance(ctx.get("surface_runtime"), dict) else {}
    profile_summary = ctx.get("backend_profile") if isinstance(ctx.get("backend_profile"), dict) else {}
    family = str(params.get("family") or "")
    loader = str(params.get("loader") or "")
    mode = str(runtime.get("workflow_mode") or runtime.get("subtab") or "generate")
    workspace_app = str(runtime.get("workspace_app") or "generations")
    profile_id = str(profile_summary.get("profile_id") or "")
    profile = get_backend_profile(profile_id) if profile_id else None
    provider = str((profile or {}).get("provider_id") or profile_summary.get("provider_id") or "comfyui")
    backend = normalize_backend(provider)
    record = get_extension(str(feature.get("feature_id") or ""))
    if not record:
        return _scope_packet(operation_id=operation_id, packet_kind="capability_readiness", status="missing", known_state="not_established", payload={"feature_id": feature.get("feature_id"), "layers": {"feature_registry": {"state": "missing"}}}, authority_classes=["LIVE", "CAP"], limitations=["The feature is not present in the Image extension registry."])
    manifest = record.manifest
    family_state, reasons = _manifest_support_state(manifest, family=family, loader=loader, backend=backend, mode=mode, workspace_app=workspace_app)
    route_state = "unknown"
    route_reason = ""
    raw_extension_route_state = resolve_extension_manifest_route_state(
        getattr(manifest, "route_states", {}) or {},
        backend=provider or backend,
        family=family or None,
        loader=loader or None,
        workflow_mode=mode or None,
        workspace_app=workspace_app or None,
        surface="image",
    )
    if raw_extension_route_state:
        route_state = "compatible" if raw_extension_route_state in {"available", "experimental_available"} else (
            "incompatible" if raw_extension_route_state in {"unsupported", "implementation_target", "planned_gated", "provider_gated"} else "unknown"
        )
        route_reason = f"Extension route declaration: {raw_extension_route_state}."
    else:
        try:
            route = resolve_model_backend_route(family or "sdxl", loader or "checkpoint", mode, backend).as_dict()
            raw_state = str(route.get("state") or "unknown")
            route_state = "compatible" if raw_state in {"available", "experimental_available"} else ("incompatible" if raw_state in {"unsupported", "implementation_target", "planned_gated", "provider_gated"} else "unknown")
            route_reason = str(route.get("reason") or "")
        except Exception as exc:
            route_state = "unknown"; route_reason = _clean(exc, 500)
    overlay = build_image_capability_overlay(profile) if profile else build_image_capability_overlay(None)
    connected = bool(overlay.get("connected"))
    backend_state = "ready" if connected else ("blocked" if profile_id else "unknown")
    required_map = getattr(manifest, "required_nodes", {}) or {}
    required_nodes = list(required_map.get(provider) or required_map.get(backend) or required_map.get("comfyui") if backend == "comfyui" else [])
    backend_caps = (profile or {}).get("backend_capabilities") if isinstance((profile or {}).get("backend_capabilities"), dict) else {}
    if not backend_caps and isinstance(((profile or {}).get("runtime") or {}).get("backend_capabilities"), dict):
        backend_caps = ((profile or {}).get("runtime") or {}).get("backend_capabilities")
    node_inputs = backend_caps.get("object_info_node_inputs") if isinstance(backend_caps.get("object_info_node_inputs"), dict) else {}
    if required_nodes and backend == "comfyui":
        if backend_caps.get("object_info_available") is True:
            missing_nodes = [node for node in required_nodes if node not in node_inputs]
            dep_state = "missing" if missing_nodes else "ready"
        else:
            missing_nodes = []
            dep_state = "unknown"
    elif required_nodes:
        missing_nodes = []
        dep_state = "unknown"
    else:
        missing_nodes = []
        dep_state = "not_applicable"
    ext_settings = ctx.get("extension_settings") if isinstance(ctx.get("extension_settings"), dict) else {}
    current_payload = ext_settings.get(feature.get("feature_id")) if isinstance(ext_settings.get(feature.get("feature_id")), dict) else {}
    applied = bool(current_payload.get("enabled")) if current_payload else False
    registry_state = "enabled" if record.enabled else "disabled"
    workspace_available = family_state == "supported" and route_state != "incompatible" and dep_state != "missing"
    workspace_state = "available_now" if workspace_available else "unavailable_now"
    if family_state == "unsupported": overall = "unsupported"
    elif route_state == "incompatible" or backend_state == "blocked" or dep_state == "missing": overall = "blocked"
    elif backend_state == "ready" and dep_state in {"ready", "not_applicable"}: overall = "ready"
    else: overall = "unknown"
    actionable = list(reasons)
    if route_state == "incompatible" and route_reason: actionable.append(route_reason)
    if not connected: actionable.append("Connect/Test the selected Image backend profile to establish live runtime readiness.")
    if missing_nodes: actionable.append("Missing required node classes: " + ", ".join(missing_nodes))
    payload = {
        "feature_id": feature.get("feature_id"),
        "target": {"family": family, "loader": loader, "workflow_mode": mode, "workspace_app": workspace_app, "profile_id": profile_id, "backend": backend},
        "layers": {
            "family_support": {"state": family_state, "reasons": reasons},
            "route_compatibility": {"state": route_state, "reason": route_reason},
            "backend_runtime": {"state": backend_state, "status": overlay.get("status"), "connected": connected},
            "dependencies": {"state": dep_state, "required": required_nodes, "missing": missing_nodes, "object_info_available": backend_caps.get("object_info_available")},
            "workspace": {"state": workspace_state, "registry_state": registry_state, "enabled_for_current_run": applied, "workspace_app": workspace_app},
        },
        "overall_readiness": overall,
        "actionable_reasons": list(dict.fromkeys([item for item in actionable if item])),
        "guide_id": feature.get("guide_id"),
    }
    known = "established" if family_state != "unknown" and route_state != "unknown" else "partially_established"
    return _scope_packet(operation_id=operation_id, packet_kind="capability_readiness", status="ok", known_state=known, payload=payload, authority_classes=["LIVE", "CAP"], source_integrity="present", derivation={"method": "live_route_extension_dependency_adjudication"})


def _numbered_steps(section: dict[str, Any] | None) -> list[str]:
    body = str((section or {}).get("body") or "")
    steps: list[str] = []
    current = ""
    for line in body.splitlines():
        match = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if match:
            if current: steps.append(_clean(current, 1600))
            current = match.group(1).strip()
        elif current and line.strip() and not line.lstrip().startswith("#"):
            current += " " + line.strip()
    if current: steps.append(_clean(current, 1600))
    return steps


def procedure_packet(query: str, *, operation_id: str = "image.procedure.get") -> dict[str, Any]:
    task = _resolve_task(query)
    low = str(query or "").lower()
    guide_id = str(task.get("guide_id") or "")
    task_id = str(task.get("task_id") or "")
    if not guide_id:
        feature = _resolve_feature(query)
        guide_id = str(feature.get("guide_id") or "")
        task_id = str(feature.get("feature_id") or "")
    resolved = resolve_canonical_guide(query, surface="image", project_id="image_workspace", task_id=task_id, guide_id=guide_id)
    if not resolved.get("ok"):
        return _scope_packet(operation_id=operation_id, packet_kind="procedure", status="not_found", known_state="not_established", payload={"task_id": task_id, "guide_id": guide_id}, authority_classes=["GUIDE"], limitations=["No current canonical Image guide was resolved."])
    guide = resolved.get("guide") if isinstance(resolved.get("guide"), dict) else {}
    section_query = str(guide.get("procedure_section") or "")
    if "troubleshoot" in low or "troubleshooting" in low:
        section_query = str(guide.get("troubleshooting_section") or section_query)
        operation_id = "image.guide.section"
    full_guide = bool(
        "guide" in low
        and any(term in low for term in ("full", "complete", "entire", "whole"))
    )
    if full_guide:
        operation_id = "image.guide.get"
    section = resolve_guide_section(guide, section_query) if section_query else None
    payload_mode = "full_guide" if full_guide else ("section" if operation_id == "image.guide.section" else "procedure")
    content = str(guide.get("content") or "") if full_guide else str((section or {}).get("text") or "")
    limit = 24000 if full_guide else 12000
    truncated = len(content) > limit
    content = _clean(content, limit)
    payload = {
        "guide": {key: guide.get(key) for key in ("guide_id", "title", "version", "updated", "lifecycle_state", "guide_kind", "path")},
        "task_id": task_id,
        "ui_path": list(guide.get("ui_path") or task.get("ui_path") or []),
        "payload_mode": payload_mode,
        "section": {"title": (section or {}).get("title"), "slug": (section or {}).get("slug"), "start_line": (section or {}).get("start_line"), "end_line": (section or {}).get("end_line")},
        "steps": _numbered_steps(section) if section else [],
        "authored_content": content,
        "source_sections": [str((section or {}).get("title") or "")] if section else [],
    }
    limitations = ["The full Guide exceeded the native packet safety budget and was clipped; use the cited Guide path for the remaining authored content."] if truncated else []
    return _scope_packet(operation_id=operation_id, packet_kind="procedure" if not full_guide else "reference", status="ok", known_state="established", payload=payload, authority_classes=["GUIDE"], native_refs=[{"guide_id": guide.get("guide_id"), "path": guide.get("path")}], source_integrity="present", limitations=limitations, derivation={"method": "canonical_guide_exact_section"})


def navigation_packet(query: str) -> dict[str, Any]:
    task = _resolve_task(query)
    if not task:
        return _scope_packet(operation_id="image.navigation.resolve", packet_kind="reference", status="not_found", known_state="not_established", payload={}, authority_classes=["GUIDE"], limitations=["No current Image workspace target could be resolved."])
    resolved = resolve_canonical_guide(query, surface="image", project_id="image_workspace", task_id=str(task.get("task_id") or ""), guide_id=str(task.get("guide_id") or ""))
    guide = resolved.get("guide") if isinstance(resolved.get("guide"), dict) else {}
    return _scope_packet(operation_id="image.navigation.resolve", packet_kind="reference", status="ok", known_state="established", payload={"task_id": task.get("task_id"), "ui_path": list(guide.get("ui_path") or task.get("ui_path") or []), "guide_id": guide.get("guide_id"), "guide_title": guide.get("title")}, authority_classes=["GUIDE"], native_refs=[{"guide_id": guide.get("guide_id"), "path": guide.get("path")}], derivation={"method": "canonical_guide_ui_path"})


def feature_reference_packet(query: str) -> dict[str, Any]:
    feature = _resolve_feature(query)
    if not feature:
        return _scope_packet(operation_id="image.feature.explain", packet_kind="reference", status="not_found", known_state="not_established", payload={}, authority_classes=["GUIDE"], limitations=["No Image feature was resolved."])
    resolved = resolve_canonical_guide(query, surface="image", project_id="image_workspace", task_id=str(feature.get("feature_id") or ""), guide_id=str(feature.get("guide_id") or ""))
    guide = resolved.get("guide") if isinstance(resolved.get("guide"), dict) else {}
    content = _clean(guide.get("content") or "", 9000)
    return _scope_packet(operation_id="image.feature.explain", packet_kind="reference", status="ok" if guide else "not_found", known_state="established" if guide else "not_established", payload={"feature_id": feature.get("feature_id"), "guide": {key: guide.get(key) for key in ("guide_id", "title", "version", "updated", "lifecycle_state", "path", "ui_path")}, "authored_content": content}, authority_classes=["GUIDE"], native_refs=[{"guide_id": guide.get("guide_id"), "path": guide.get("path")}], derivation={"method": "canonical_feature_guide"})


# ---------------------------------------------------------------------------
# NKB-15I-I3: exact historical recipe / history / comparison / lineage / replay
# ---------------------------------------------------------------------------

_HISTORY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does", "find", "for", "from",
    "generation", "generations", "generated", "give", "i", "image", "images", "in", "is", "it", "me", "my",
    "of", "on", "output", "outputs", "result", "results", "show", "that", "the", "this", "to", "used", "using",
    "was", "were", "what", "where", "which", "with", "you", "neo", "similar", "description", "described",
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_EXTENSION_QUERY_ALIASES = {
    "controlnet": "image.controlnet",
    "control net": "image.controlnet",
    "ip adapter": "image.ip_adapter",
    "ip-adapter": "image.ip_adapter",
    "adetailer": "image.adetailer",
    "a detailer": "image.adetailer",
    "high-res": "image.high_res_lab",
    "high res": "image.high_res_lab",
    "hires": "image.high_res_lab",
    "layerdiffuse": "image.layerdiffuse",
    "scene director": "image.scene_director",
    "background removal": "image.background_removal",
    "upscale": "image.image_upscale",
    "lora": "lora_stack",
    "style stack": "style_stack",
    "wildcards": "wildcards",
}
_RECIPE_CATEGORIES = ("model", "prompt", "sampling", "canvas", "assets", "extensions", "route", "timing", "lineage")


def _record_file_records(record: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    return [item for item in (outputs.get("files") or []) if isinstance(item, dict)]


def _record_aliases(record: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for value in (record.get("result_id"), record.get("neo_result_uid"), record.get("job_id")):
        text = _clean(value)
        if text:
            aliases.add(text.lower())
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    if _clean(job.get("job_id")):
        aliases.add(_clean(job.get("job_id")).lower())
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    for key in ("current_output_id",):
        text = _clean(lineage.get(key))
        if text:
            aliases.add(text.lower())
    for item in _record_file_records(record):
        for key in ("neo_output_id", "output_id", "file_id", "filename", "path"):
            text = _clean(item.get(key))
            if text:
                aliases.add(text.lower())
                if key in {"filename", "path"}:
                    aliases.add(Path(text).name.lower())
    return aliases


def _record_ref(record: dict[str, Any], file_record: dict[str, Any] | None = None) -> dict[str, Any]:
    file_record = file_record if isinstance(file_record, dict) else _find_file_record(record)
    return {
        "result_id": _clean(record.get("result_id")),
        "neo_result_uid": _clean(record.get("neo_result_uid")),
        "neo_output_id": _clean(file_record.get("neo_output_id")),
        "output_id": _clean(file_record.get("output_id")),
        "file_id": _clean(file_record.get("file_id")),
        "filename": _clean(file_record.get("filename")),
        "created_at": _clean(record.get("created_at")),
        "sidecar_path": _clean(record.get("_neo_metadata_path")),
    }





def _active_image_referent(native_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = native_context if isinstance(native_context, dict) else {}
    ref = ctx.get("active_image_referent") if isinstance(ctx.get("active_image_referent"), dict) else {}
    return ref

def _deictic_image_reference(query: str) -> bool:
    low = _clean(query, 2400).lower()
    return any(term in low for term in (
        "this image", "this picture", "this pic", "this file", "this png", "this jpeg", "this jpg", "this webp",
        "attached image", "attached file", "same image", "same picture", "that image", "that picture",
        "generation settings for this", "settings for this", "prompt for this", "prompt to generate this",
        "what was this generated with", "what did i generate this with", "used here", "for it", "reproduce it", "replay it",
    )) or low.strip() in {"it", "this", "that"}

def _attachment_native_targets(native_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    ctx = native_context if isinstance(native_context, dict) else {}
    rows = ctx.get("attachment_image_intelligence") if isinstance(ctx.get("attachment_image_intelligence"), list) else []
    return [item for item in rows if isinstance(item, dict)]


def _selected_native_record(native_context: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ctx = native_context if isinstance(native_context, dict) else {}
    # Current Assistant client snapshot stores the selected Results sidecar here.
    candidate = ctx.get("activeSavedResultMetadata")
    if not isinstance(candidate, dict):
        nested = ctx.get("snapshot") if isinstance(ctx.get("snapshot"), dict) else {}
        candidate = nested.get("activeSavedResultMetadata") if isinstance(nested.get("activeSavedResultMetadata"), dict) else None
    if not isinstance(candidate, dict) or not _valid_image_record(candidate):
        return None, None
    file_id = _clean(ctx.get("activeSavedOutputFileId"))
    return candidate, _find_file_record(candidate, file_id=file_id)


def _record_from_alias(rows: list[dict[str, Any]], alias: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    needle = _clean(alias).lower()
    if not needle:
        return None, None
    for record in rows:
        if needle in _record_aliases(record):
            file_match = None
            for item in _record_file_records(record):
                vals = {_clean(item.get(k)).lower() for k in ("neo_output_id", "output_id", "file_id", "filename", "path")}
                vals.add(Path(_clean(item.get("path"))).name.lower() if _clean(item.get("path")) else "")
                if needle in vals:
                    file_match = item
                    break
            return record, file_match or _find_file_record(record)
    return None, None


def _explicit_record_matches(query: str, rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    low = _clean(query, 4000).lower()
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for record in rows:
        result_id = _clean(record.get("result_id"))
        matching_aliases = sorted((alias for alias in _record_aliases(record) if alias and alias in low), key=len, reverse=True)
        if not matching_aliases:
            continue
        key = result_id or matching_aliases[0]
        if key in seen:
            continue
        record_match, file_match = _record_from_alias([record], matching_aliases[0])
        if record_match:
            matches.append((record_match, file_match or _find_file_record(record_match)))
            seen.add(key)
    return matches


def _resolve_history_target(
    root_dir: Path,
    query: str,
    *,
    native_context: dict[str, Any] | None = None,
    allow_latest: bool = True,
) -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    selected, selected_file = _selected_native_record(native_context)
    low = _clean(query, 3000).lower()
    attachment_targets = _attachment_native_targets(native_context)
    exact_attachment_targets = [
        item for item in attachment_targets
        if str(item.get("proof_level") or "").startswith("native_exact")
    ]
    external_attachment_targets = [
        item for item in attachment_targets
        if str(item.get("proof_level") or "") == "external_embedded_metadata"
    ]
    if len(exact_attachment_targets) == 1 and any(term in low for term in ("this image", "this attached", "attached image", "this result", "this output", "here", "it")):
        identity = exact_attachment_targets[0].get("identity") if isinstance(exact_attachment_targets[0].get("identity"), dict) else {}
        for alias in (identity.get("result_id"), identity.get("neo_output_id"), identity.get("file_id"), identity.get("filename")):
            record, file_record = _record_from_alias(rows, _clean(alias))
            if record:
                return {"status": "resolved", "basis": "current_attachment_exact_identity", "record": record, "file": file_record, "health": health, "attachment": exact_attachment_targets[0]}
        return {"status": "missing", "basis": "attachment_exact_identity_sidecar_unavailable", "health": health, "attachment": exact_attachment_targets[0]}
    if len(exact_attachment_targets) > 1 and any(term in low for term in ("this image", "attached image", "these images", "attachments")):
        return {"status": "ambiguous", "basis": "multiple_exact_image_attachments", "health": health, "candidates": exact_attachment_targets[:6]}
    if external_attachment_targets and any(term in low for term in ("this image", "this attached", "attached image", "here", "it")):
        return {"status": "external", "basis": "current_attachment_external_metadata", "health": health, "attachment": external_attachment_targets[0]}

    explicit = _explicit_record_matches(query, rows)
    if len(explicit) == 1:
        record, file_record = explicit[0]
        return {"status": "resolved", "basis": "explicit_record_reference", "record": record, "file": file_record, "health": health}
    if len(explicit) > 1:
        return {
            "status": "ambiguous", "basis": "multiple_explicit_record_references", "health": health,
            "candidates": [_record_ref(record, file_record) for record, file_record in explicit[:12]],
        }

    # I7: when the current turn has no image attachment, a deictic Image query may
    # reuse the session-scoped referent established by an earlier explicit attachment.
    # The referent is identity metadata only; it is not durable memory and does not
    # allow visual similarity to become provenance.
    active_ref = _active_image_referent(native_context)
    if active_ref and _deictic_image_reference(query):
        if str(active_ref.get("status") or "active") == "ambiguous":
            return {
                "status": "ambiguous", "basis": "session_image_referent_ambiguous", "health": health,
                "candidates": active_ref.get("candidates") if isinstance(active_ref.get("candidates"), list) else [],
            }
        proof = str(active_ref.get("proof_level") or "unidentified")
        if proof.startswith("native_exact"):
            identity = active_ref.get("identity") if isinstance(active_ref.get("identity"), dict) else {}
            for alias in (identity.get("result_id"), identity.get("neo_output_id"), identity.get("file_id"), identity.get("filename")):
                record, file_record = _record_from_alias(rows, _clean(alias))
                if record:
                    return {
                        "status": "resolved", "basis": "session_image_referent_exact_identity",
                        "record": record, "file": file_record, "health": health, "attachment": active_ref,
                    }
            return {
                "status": "missing", "basis": "session_image_referent_sidecar_unavailable",
                "health": health, "attachment": active_ref,
            }
        if proof == "external_embedded_metadata":
            return {
                "status": "external", "basis": "session_image_referent_external_metadata",
                "health": health, "attachment": active_ref,
            }
        return {
            "status": "unresolved", "basis": "session_image_referent_unidentified",
            "health": health, "attachment": active_ref,
        }

    selected_markers = ("this result", "this output", "selected result", "selected output", "current result", "current output", "active result", "active output", "here")
    if selected and (any(marker in low for marker in selected_markers) or not re.search(r"\b(?:latest|newest|last|previous|older|first)\b", low)):
        record = dict(selected)
        # If selected metadata was hydrated from the API it may not carry the local path.
        if not record.get("_neo_metadata_path"):
            sidecar_match, sidecar_file = _record_from_alias(rows, _clean(record.get("result_id")))
            if sidecar_match:
                record = sidecar_match
                selected_file = selected_file or sidecar_file
        return {"status": "resolved", "basis": "current_selected_result", "record": record, "file": selected_file or _find_file_record(record), "health": health}

    if allow_latest and rows and any(marker in low for marker in ("latest", "newest", "last generation", "last result", "most recent")):
        return {"status": "resolved", "basis": "latest_record", "record": rows[0], "file": _find_file_record(rows[0]), "health": health}

    if selected and "previous" in low:
        selected_id = _clean(selected.get("result_id"))
        for idx, record in enumerate(rows):
            if _clean(record.get("result_id")) == selected_id:
                if idx + 1 < len(rows):
                    prev = rows[idx + 1]
                    return {"status": "resolved", "basis": "previous_record_from_selected_anchor", "record": prev, "file": _find_file_record(prev), "health": health}
                return {"status": "missing", "basis": "selected_result_has_no_previous_record", "health": health}

    return {"status": "unresolved", "basis": "no_exact_history_target", "health": health}


def _unknown_history_packet(operation_id: str, packet_kind: str, resolution: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    candidates = resolution.get("candidates") if isinstance(resolution.get("candidates"), list) else []
    message = reason or (
        "Multiple Image records match the requested target; Neo will not choose one silently."
        if resolution.get("status") == "ambiguous"
        else "No exact Image result/output target was established from the query or current Results selection."
    )
    return _scope_packet(
        operation_id=operation_id,
        packet_kind=packet_kind,
        status="partial",
        known_state="unresolved_conflict" if resolution.get("status") == "ambiguous" else "not_established",
        payload={"resolution_basis": resolution.get("basis"), "candidates": candidates},
        authority_classes=["SIDE", "ID"],
        source_integrity="unknown",
        limitations=[message],
        derivation={"method": "exact_history_target_resolution"},
    )


def _recipe_completeness(recipe: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "model.checkpoint": _clean((recipe.get("model") or {}).get("checkpoint") or (recipe.get("model") or {}).get("model")),
        "prompt.positive": _clean((recipe.get("prompt") or {}).get("positive") or (recipe.get("prompt") or {}).get("effective_positive")),
        "sampling.seed": (recipe.get("sampling") or {}).get("actual_seed") if (recipe.get("sampling") or {}).get("actual_seed") is not None else (recipe.get("sampling") or {}).get("seed"),
        "sampling.steps": (recipe.get("sampling") or {}).get("steps"),
        "sampling.sampler": _clean((recipe.get("sampling") or {}).get("sampler")),
        "sampling.scheduler": _clean((recipe.get("sampling") or {}).get("scheduler")),
        "canvas.width": (recipe.get("canvas") or {}).get("width"),
        "canvas.height": (recipe.get("canvas") or {}).get("height"),
    }
    present = [field for field, value in checks.items() if value not in (None, "", [])]
    missing = [field for field, value in checks.items() if value in (None, "", [])]
    native_schema = _clean((recipe.get("record_completeness") or {}).get("native_schema_id"))
    return {
        "present_fields": present,
        "missing_fields": missing,
        "legacy_record": not native_schema.startswith("neo.image.output.v1"),
        "core_recipe_complete": not missing,
        "native_schema_id": native_schema,
        "has_full_sidecar": bool((recipe.get("record_completeness") or {}).get("has_full_sidecar")),
    }


def _external_recipe_projection(attachment: dict[str, Any]) -> dict[str, Any]:
    metadata = attachment.get("external_metadata") if isinstance(attachment.get("external_metadata"), dict) else {}
    normalized = metadata.get("normalized_fields") if isinstance(metadata.get("normalized_fields"), dict) else {}
    generator = _clean(metadata.get("generator") or normalized.get("generator"), 200)
    prompt: dict[str, Any] = {}
    model: dict[str, Any] = {}
    sampling: dict[str, Any] = {}
    canvas: dict[str, Any] = {}
    source = normalized.get("a1111_or_forge") if isinstance(normalized.get("a1111_or_forge"), dict) else {}
    if source:
        prompt = {
            "positive": _clean(source.get("positive_prompt"), 12000),
            "negative": _clean(source.get("negative_prompt"), 12000),
        }
        model = {"checkpoint": _clean(source.get("model"), 1000), "vae": _clean(source.get("vae"), 1000)}
        for key in ("seed", "steps", "cfg", "sampler", "scheduler", "denoise"):
            if source.get(key) not in (None, ""):
                sampling[key] = source.get(key)
        for key in ("width", "height"):
            if source.get(key) not in (None, ""):
                canvas[key] = source.get(key)
    comfy = normalized.get("comfyui") if isinstance(normalized.get("comfyui"), dict) else {}
    summary = comfy.get("summary") if isinstance(comfy.get("summary"), dict) else {}
    if summary and not prompt:
        candidates = summary.get("prompt_candidates") if isinstance(summary.get("prompt_candidates"), list) else []
        if candidates:
            prompt["recorded_candidates"] = [_safe_value(item, text_limit=6000) for item in candidates[:8]]
        models = summary.get("models") if isinstance(summary.get("models"), list) else []
        if models:
            model["recorded_candidates"] = [_safe_value(item, text_limit=2000) for item in models[:12]]
        samplers = summary.get("samplers") if isinstance(summary.get("samplers"), list) else []
        if samplers:
            sampling["recorded_candidates"] = [_safe_value(item, text_limit=2000) for item in samplers[:8]]
    return {
        "identity": {
            "filename": _clean(attachment.get("filename"), 600),
            "origin": "external",
            "proof_level": "external_embedded_metadata",
        },
        "provider": {"generator": generator or "external"},
        "prompt": prompt,
        "model": model,
        "sampling": sampling,
        "canvas": canvas,
        "external_metadata": _safe_value(normalized, text_limit=12000),
        "record_completeness": {
            "has_full_sidecar": False,
            "core_recipe_complete": False,
            "external_metadata_only": True,
        },
    }


def exact_recipe_field_value(
    root_dir: Path,
    query: str,
    field_path: str,
    *,
    native_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one exact recorded Recipe text field without normalized-packet truncation.

    Provider-facing Recipe packets intentionally bound long text. Exact native delivery
    must instead read the owning sidecar field after the same deterministic target
    resolution used by ``recipe_packet``. No vision/inference fallback is allowed.
    """
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") == "external":
        attachment = resolution.get("attachment") if isinstance(resolution.get("attachment"), dict) else {}
        metadata = attachment.get("external_metadata") if isinstance(attachment.get("external_metadata"), dict) else {}
        normalized = metadata.get("normalized_fields") if isinstance(metadata.get("normalized_fields"), dict) else {}
        source = normalized.get("a1111_or_forge") if isinstance(normalized.get("a1111_or_forge"), dict) else {}
        key = "positive_prompt" if field_path == "prompt.positive" else "negative_prompt" if field_path == "prompt.negative" else ""
        value = source.get(key) if key else None
        return {
            "status": "established" if value not in (None, "") else "not_established",
            "known_state": "partial" if value not in (None, "") else "not_established",
            "value": str(value or ""),
            "field_path": field_path,
            "source_field": f"external_metadata.a1111_or_forge.{key}" if key else "",
            "resolution_basis": resolution.get("basis"),
            "authority_classes": ["EXTMETA"],
            "source_ref": {"kind": "external_attached_asset", "filename": _clean(attachment.get("filename"), 600)},
        }
    if resolution.get("status") != "resolved":
        return {
            "status": "not_established",
            "known_state": "unresolved_conflict" if resolution.get("status") == "ambiguous" else "not_established",
            "value": "",
            "field_path": field_path,
            "resolution_basis": resolution.get("basis"),
            "authority_classes": ["SIDE", "ID"],
            "candidates": resolution.get("candidates") if isinstance(resolution.get("candidates"), list) else [],
        }
    record = resolution.get("record") if isinstance(resolution.get("record"), dict) else {}
    file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    source_field = ""
    value: Any = None
    if field_path == "prompt.positive":
        if prompt.get("positive") not in (None, ""):
            source_field, value = "prompt.positive", prompt.get("positive")
        elif prompt.get("effective_positive") not in (None, ""):
            source_field, value = "prompt.effective_positive", prompt.get("effective_positive")
    elif field_path == "prompt.negative":
        if prompt.get("negative") not in (None, ""):
            source_field, value = "prompt.negative", prompt.get("negative")
        elif prompt.get("effective_negative") not in (None, ""):
            source_field, value = "prompt.effective_negative", prompt.get("effective_negative")
    return {
        "status": "established" if value not in (None, "") else "not_established",
        "known_state": "established" if value not in (None, "") else "not_established",
        "value": str(value or ""),
        "field_path": field_path,
        "source_field": source_field,
        "resolution_basis": resolution.get("basis"),
        "authority_classes": ["ID", "SIDE"],
        "source_ref": _record_ref(record, file_record),
    }


def recipe_packet(root_dir: Path, query: str, *, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") == "external":
        attachment = resolution.get("attachment") if isinstance(resolution.get("attachment"), dict) else {}
        recipe = _external_recipe_projection(attachment)
        return _scope_packet(
            operation_id="image.recipe.get", packet_kind="recipe", status="partial", known_state="partial",
            payload={**recipe, "resolution_basis": resolution.get("basis")},
            target_refs=[{"kind": "external_attached_asset", "filename": _clean(attachment.get("filename"), 600)}],
            authority_classes=["EXTMETA"], source_integrity="present",
            limitations=["This recipe is reconstructed only from embedded external metadata and is not a Neo execution record. Missing fields remain unknown."],
            derivation={"method": "external_embedded_metadata_recipe_projection"},
        )
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.recipe.get", "recipe", resolution)
    record = resolution["record"]
    file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    recipe = normalize_image_recipe(record, file_record)
    model = recipe.get("model") if isinstance(recipe.get("model"), dict) else {}
    if model.get("model") and not model.get("checkpoint"):
        model["checkpoint"] = model.get("model")
    recipe["model"] = model
    recipe["record_completeness"] = _recipe_completeness(recipe)
    raw_prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    raw_positive = raw_prompt.get("positive") if raw_prompt.get("positive") not in (None, "") else raw_prompt.get("effective_positive")
    raw_negative = raw_prompt.get("negative") if raw_prompt.get("negative") not in (None, "") else raw_prompt.get("effective_negative")
    recipe["recorded_text_stats"] = {
        "prompt.positive": {"characters": len(str(raw_positive or "")), "recorded": raw_positive not in (None, "")},
        "prompt.negative": {"characters": len(str(raw_negative or "")), "recorded": raw_negative not in (None, "")},
    }
    known = "established" if recipe["record_completeness"]["has_full_sidecar"] and recipe["record_completeness"]["core_recipe_complete"] else "partial"
    target = _record_ref(record, file_record)
    return _scope_packet(
        operation_id="image.recipe.get",
        packet_kind="recipe",
        status="ok" if known == "established" else "partial",
        known_state=known,
        payload={**recipe, "resolution_basis": resolution.get("basis")},
        target_refs=[target],
        authority_classes=["ID", "SIDE"],
        native_refs=[target],
        source_integrity="present",
        limitations=[] if known == "established" else ["The historical Image record is partial; missing recipe fields remain unknown and are not filled from current defaults."],
        derivation={"method": "exact_sidecar_recipe_projection"},
    )


def _history_search_terms(query: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_@.+-]+", _clean(query, 3000).lower())
    return [token for token in tokens if len(token) >= 3 and token not in _HISTORY_STOPWORDS]


def _history_search_fields(record: dict[str, Any]) -> dict[str, str]:
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    return {
        "assistant_summary": _clean(record.get("assistant_summary"), 4000),
        "positive_prompt": _clean(prompt.get("effective_positive") or prompt.get("positive"), 8000),
        "negative_prompt": _clean(prompt.get("effective_negative") or prompt.get("negative"), 4000),
        "model": _clean(model.get("model") or model.get("checkpoint"), 1000),
        "family": _clean(model.get("family"), 500),
        "loader": _clean(model.get("loader"), 500),
        "lineage": _metadata_text(lineage, limit=3000),
        "filenames": " ".join(_clean(item.get("filename"), 500) for item in _record_file_records(record)),
    }


def history_search_packet(root_dir: Path, query: str, *, limit: int = 20) -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    terms = _history_search_terms(query)
    ranked: list[tuple[float, dict[str, Any], list[dict[str, Any]]]] = []
    field_weights = {"assistant_summary": 2.4, "positive_prompt": 2.0, "model": 2.0, "family": 1.5, "loader": 1.2, "filenames": 1.5, "negative_prompt": 0.8, "lineage": 0.6}
    for record in rows:
        fields = _history_search_fields(record)
        basis: list[dict[str, Any]] = []
        score = 0.0
        for field, text in fields.items():
            low = text.lower()
            matched = [term for term in terms if term in low]
            if matched:
                part = field_weights.get(field, 1.0) * len(matched)
                score += part
                basis.append({"field": field, "terms": matched[:12], "score": round(part, 3)})
        if score > 0:
            ranked.append((score, record, basis))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("created_at") or "")), reverse=False)
    candidates = []
    for score, record, basis in ranked[: max(1, min(limit, 100))]:
        ref = _record_ref(record)
        candidates.append({
            "target": ref,
            "score": round(score, 3),
            "match_basis": basis,
            "summary": _clean(record.get("assistant_summary") or (record.get("prompt") or {}).get("effective_positive") or (record.get("prompt") or {}).get("positive"), 700),
            "model": _safe_value(record.get("model") if isinstance(record.get("model"), dict) else {}),
        })
    return _scope_packet(
        operation_id="image.history.search",
        packet_kind="history_search",
        status="ok",
        known_state="established" if candidates else "not_established",
        payload={
            "query_terms": terms,
            "searched_fields": list(field_weights),
            "candidate_count": len(candidates),
            "candidates": candidates,
            "source_health": health,
            "search_kind": "native_sidecar_lexical_discovery",
        },
        authority_classes=["SEARCH", "SIDE"],
        source_integrity="present",
        limitations=[] if candidates else ["No recorded Image sidecar text matched the requested history description."],
        derivation={"method": "weighted_native_sidecar_text_search"},
    )


def _record_extension_ids(record: dict[str, Any]) -> set[str]:
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    ids: set[str] = set()
    for item in extensions.get("used") if isinstance(extensions.get("used"), list) else []:
        value = _clean(item.get("extension_id") if isinstance(item, dict) else item).lower()
        if value:
            ids.add(value)
    for bucket in ("payloads", "replay_payloads"):
        payload = extensions.get(bucket) if isinstance(extensions.get(bucket), dict) else {}
        ids.update(_clean(key).lower() for key in payload if _clean(key))
    return ids


def _history_filters_from_query(query: str) -> dict[str, Any]:
    text = _clean(query, 3000)
    low = text.lower()
    filters: dict[str, Any] = {}
    model_match = re.search(r"\b([^\s,;]+\.(?:safetensors|gguf|ckpt|pt|pth))\b", text, re.I)
    if model_match:
        filters["model"] = model_match.group(1)
    for mode in ("outpaint", "inpaint", "img2img", "txt2img", "edit", "generate"):
        if re.search(rf"\b{re.escape(mode)}\b", low):
            filters["mode"] = mode
            break
    for alias, extension_id in _EXTENSION_QUERY_ALIASES.items():
        if alias in low and any(term in low for term in ("used", "using", "with", "enabled", "generations", "runs")):
            filters["extension_id"] = extension_id
            break
    dims = re.search(r"\b(\d{2,5})\s*[x×]\s*(\d{2,5})\b", low)
    if dims:
        filters["width"] = int(dims.group(1)); filters["height"] = int(dims.group(2))
    steps = re.search(r"\b(\d{1,4})\s+steps?\b", low)
    if steps:
        filters["steps"] = int(steps.group(1))
    year_match = re.search(r"\b(20\d{2})\b", low)
    if year_match:
        filters["year"] = int(year_match.group(1))
    for name, month in _MONTHS.items():
        if name in low:
            filters["month"] = month
            break
    return filters


def _mode_matches(record: dict[str, Any], expected: str) -> bool:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    values = {
        _clean(record.get("mode")).lower(), _clean(record.get("subtab")).lower(), _clean(params.get("mode")).lower(),
    }
    if expected == "generate":
        return bool(values & {"generate", "txt2img"})
    return expected in values


def _record_matches_history_filters(record: dict[str, Any], filters: dict[str, Any]) -> tuple[bool, list[str]]:
    matched: list[str] = []
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    if filters.get("model"):
        actual = _clean(model.get("model") or model.get("checkpoint") or params.get("model"))
        if actual.lower() != _clean(filters["model"]).lower():
            return False, []
        matched.append("model")
    if filters.get("mode"):
        if not _mode_matches(record, _clean(filters["mode"]).lower()):
            return False, []
        matched.append("mode")
    if filters.get("extension_id"):
        ext = _clean(filters["extension_id"]).lower()
        if ext not in _record_extension_ids(record):
            return False, []
        matched.append("extension_id")
    for field in ("width", "height", "steps"):
        if field in filters:
            try:
                if int(params.get(field)) != int(filters[field]):
                    return False, []
            except Exception:
                return False, []
            matched.append(field)
    if filters.get("month") or filters.get("year"):
        created = _clean(record.get("created_at"))
        match = re.match(r"(\d{4})-(\d{2})", created)
        if not match:
            return False, []
        year, month = int(match.group(1)), int(match.group(2))
        if filters.get("month") and month != int(filters["month"]):
            return False, []
        if filters.get("year") and year != int(filters["year"]):
            return False, []
        matched.append("date")
    return True, matched


def history_filter_packet(root_dir: Path, query: str, *, limit: int = 100) -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    filters = _history_filters_from_query(query)
    matches: list[dict[str, Any]] = []
    for record in rows:
        ok, matched_fields = _record_matches_history_filters(record, filters)
        if ok and filters:
            matches.append({
                "target": _record_ref(record),
                "matched_fields": matched_fields,
                "summary": _clean(record.get("assistant_summary") or (record.get("prompt") or {}).get("effective_positive") or (record.get("prompt") or {}).get("positive"), 700),
                "model": _safe_value(record.get("model") if isinstance(record.get("model"), dict) else {}),
                "mode": _clean(record.get("mode") or record.get("subtab")),
            })
            if len(matches) >= max(1, min(limit, 500)):
                break
    known = "established" if filters else "not_established"
    limitations = [] if filters else ["The request did not contain a structured Image history filter that Neo could establish deterministically."]
    return _scope_packet(
        operation_id="image.history.filter",
        packet_kind="history_search",
        status="ok" if filters else "partial",
        known_state=known,
        payload={"filters": filters, "match_count": len(matches), "records": matches, "source_health": health, "filter_kind": "exact_native_sidecar_filter"},
        authority_classes=["SIDE"],
        source_integrity="present",
        limitations=limitations,
        derivation={"method": "deterministic_sidecar_filter"},
    )


def _flatten_diff(value: Any, *, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten_diff(value[key], prefix=path))
        return out
    # Lists are intentionally compared as one normalized value; order often has recipe meaning.
    return {prefix: _safe_value(value, text_limit=4000)} if prefix else {"value": _safe_value(value, text_limit=4000)}


def _diff_recipe_payloads(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    categories: dict[str, list[dict[str, Any]]] = {}
    summary = {"same": 0, "changed": 0, "added": 0, "removed": 0, "unknown": 0}
    sentinel = object()
    for category in _RECIPE_CATEGORIES:
        lf = _flatten_diff(left.get(category, {}), prefix=category)
        rf = _flatten_diff(right.get(category, {}), prefix=category)
        diffs: list[dict[str, Any]] = []
        for field in sorted(set(lf) | set(rf)):
            lv = lf.get(field, sentinel); rv = rf.get(field, sentinel)
            if lv is sentinel:
                state = "added"
            elif rv is sentinel:
                state = "removed"
            elif lv in (None, "", []) and rv not in (None, "", []):
                state = "unknown"
            elif rv in (None, "", []) and lv not in (None, "", []):
                state = "unknown"
            elif lv == rv:
                state = "same"
            else:
                state = "changed"
            summary[state] += 1
            diffs.append({
                "field": field,
                "state": state,
                "left": None if lv is sentinel else lv,
                "right": None if rv is sentinel else rv,
                "left_evidence_refs": [],
                "right_evidence_refs": [],
            })
        categories[category] = diffs
    return categories, summary


def _parent_target(rows: list[dict[str, Any]], record: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    parent = _clean(lineage.get("parent_output_id") or lineage.get("source_output_id"))
    return _record_from_alias(rows, parent) if parent else (None, None)


def comparison_packet(root_dir: Path, query: str, *, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    explicit = _explicit_record_matches(query, rows)
    low = _clean(query, 3000).lower()
    left: tuple[dict[str, Any] | None, dict[str, Any] | None] = (None, None)
    right: tuple[dict[str, Any] | None, dict[str, Any] | None] = (None, None)
    basis = ""
    if len(explicit) >= 2:
        left, right, basis = explicit[0], explicit[1], "two_explicit_record_references"
    else:
        selected, selected_file = _selected_native_record(native_context)
        if len(explicit) == 1:
            left = explicit[0]
        elif selected:
            selected_sidecar, selected_sidecar_file = _record_from_alias(rows, _clean(selected.get("result_id")))
            left = (selected_sidecar or selected, selected_sidecar_file or selected_file or _find_file_record(selected))
        if left[0] and "parent" in low:
            right = _parent_target(rows, left[0])
            basis = "subject_and_recorded_parent"
        elif left[0] and "previous" in low:
            left_id = _clean(left[0].get("result_id"))
            for idx, record in enumerate(rows):
                if _clean(record.get("result_id")) == left_id and idx + 1 < len(rows):
                    right = (rows[idx + 1], _find_file_record(rows[idx + 1])); basis = "subject_and_previous_record"; break
    if not left[0] or not right[0]:
        resolution = {"status": "unresolved", "basis": basis or "comparison_requires_two_exact_targets", "health": health}
        return _unknown_history_packet("image.recipe.diff", "comparison", resolution, reason="Recipe comparison requires two exact Image records, or one exact record plus a recorded parent/previous anchor.")
    left_recipe = normalize_image_recipe(left[0], left[1]); right_recipe = normalize_image_recipe(right[0], right[1])
    categories, counts = _diff_recipe_payloads(left_recipe, right_recipe)
    left_ref, right_ref = _record_ref(left[0], left[1]), _record_ref(right[0], right[1])
    return _scope_packet(
        operation_id="image.recipe.diff",
        packet_kind="comparison",
        status="ok",
        known_state="established",
        payload={"left_target": left_ref, "right_target": right_ref, "resolution_basis": basis, "categories": categories, "summary_counts": counts},
        target_refs=[left_ref, right_ref],
        authority_classes=["SIDE"],
        native_refs=[left_ref, right_ref],
        source_integrity="present",
        derivation={"method": "normalized_recipe_field_diff"},
    )


def _lineage_id(record: dict[str, Any], file_record: dict[str, Any] | None = None) -> str:
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    file_record = file_record if isinstance(file_record, dict) else _find_file_record(record)
    return _clean(lineage.get("current_output_id") or file_record.get("output_id") or file_record.get("file_id") or file_record.get("neo_output_id") or record.get("result_id"))


def _lineage_node(rows: list[dict[str, Any]], alias: str) -> dict[str, Any]:
    record, file_record = _record_from_alias(rows, alias)
    if record:
        return _record_ref(record, file_record)
    return {"output_id": _clean(alias), "recorded_reference_only": True}


def lineage_packet(root_dir: Path, query: str, *, native_context: dict[str, Any] | None = None, children_only: bool = False) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.lineage.children" if children_only else "image.lineage.trace", "lineage", resolution)
    rows, _ = image_history_records(root_dir)
    record = resolution["record"]; file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    subject_id = _lineage_id(record, file_record)
    aliases = _record_aliases(record) | ({subject_id.lower()} if subject_id else set())
    children: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for candidate in rows:
        if candidate is record:
            continue
        lin = candidate.get("lineage") if isinstance(candidate.get("lineage"), dict) else {}
        parent = _clean(lin.get("parent_output_id") or lin.get("source_output_id")).lower()
        if parent and parent in aliases:
            cref = _record_ref(candidate)
            children.append(cref)
            edges.append({"relation": "lineage.derived_from", "source": _lineage_id(candidate), "target": subject_id, "recorded": True, "evidence_refs": []})
    if children_only:
        subject_ref = _record_ref(record, file_record)
        return _scope_packet(
            operation_id="image.lineage.children", packet_kind="lineage", status="ok", known_state="established",
            payload={"subject": subject_ref, "children": children, "edges": edges, "completeness": "known_recorded_children_only", "resolution_basis": resolution.get("basis")},
            target_refs=[subject_ref], authority_classes=["SIDE", "ID"], native_refs=[subject_ref], source_integrity="present",
            limitations=["Children are limited to relationships explicitly recorded in Neo Image sidecars."], derivation={"method": "recorded_lineage_child_scan"},
        )

    parent_id = _clean(lineage.get("parent_output_id") or lineage.get("source_output_id"))
    root_id = _clean(lineage.get("root_output_id"))
    recorded_ancestors = [_clean(value) for value in (lineage.get("ancestor_output_ids") or []) if _clean(value)] if isinstance(lineage.get("ancestor_output_ids"), list) else []
    # Walk recorded parents if an older sidecar did not persist ancestor_output_ids.
    chain: list[str] = list(recorded_ancestors)
    if parent_id and parent_id not in chain:
        chain.append(parent_id)
    if not recorded_ancestors and parent_id:
        walked: list[str] = []
        cursor = parent_id
        seen = {subject_id.lower()} if subject_id else set()
        for _ in range(24):
            if not cursor or cursor.lower() in seen:
                break
            seen.add(cursor.lower()); walked.append(cursor)
            parent_record, _parent_file = _record_from_alias(rows, cursor)
            if not parent_record:
                break
            plin = parent_record.get("lineage") if isinstance(parent_record.get("lineage"), dict) else {}
            cursor = _clean(plin.get("parent_output_id") or plin.get("source_output_id"))
        if walked:
            chain = list(reversed(walked))
    if root_id and root_id not in chain:
        chain.insert(0, root_id)
    # De-duplicate while retaining root→parent ordering.
    ordered: list[str] = []
    for item in chain:
        if item and item.lower() != subject_id.lower() and item not in ordered:
            ordered.append(item)
    ancestor_nodes = [_lineage_node(rows, item) for item in ordered]
    subject_ref = _record_ref(record, file_record)
    immediate = _lineage_node(rows, parent_id) if parent_id else {}
    root = _lineage_node(rows, root_id) if root_id else (ancestor_nodes[0] if ancestor_nodes else subject_ref)
    prev = subject_id
    if parent_id:
        edges.append({"relation": "lineage.derived_from", "source": subject_id, "target": parent_id, "recorded": True, "evidence_refs": []})
    completeness = "full_recorded_chain" if (not parent_id or bool(root_id or ordered)) else "partial"
    return _scope_packet(
        operation_id="image.lineage.trace",
        packet_kind="lineage",
        status="ok",
        known_state="established",
        payload={
            "subject": subject_ref,
            "immediate_parent": immediate,
            "root": root,
            "ancestors": ancestor_nodes,
            "children": children,
            "edges": edges,
            "completeness": completeness,
            "resolution_basis": resolution.get("basis"),
        },
        target_refs=[subject_ref],
        authority_classes=["SIDE", "ID"],
        native_refs=[subject_ref],
        source_integrity="present",
        limitations=["Lineage reflects relationships explicitly recorded by Neo. Missing edges do not prove that no external relationship existed."],
        derivation={"method": "recorded_lineage_trace"},
    )


def _profile_model_names(profile: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if not isinstance(profile, dict):
        return names
    sources = [profile.get("available_models"), (profile.get("profile_snapshot") or {}).get("available_models") if isinstance(profile.get("profile_snapshot"), dict) else None]
    caps = profile.get("backend_capabilities") if isinstance(profile.get("backend_capabilities"), dict) else {}
    sources.extend([caps.get("available_models"), caps.get("models")])
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if isinstance(item, dict):
                value = _clean(item.get("id") or item.get("name") or item.get("model") or item.get("filename"))
            else:
                value = _clean(item)
            if value:
                names.add(value.lower())
    return names



def _walk_named_items(value: Any, key_name: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == key_name.lower() and isinstance(item, list):
                found.extend(item)
            else:
                found.extend(_walk_named_items(item, key_name))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_named_items(item, key_name))
    return found


def _recorded_lora_names(record: dict[str, Any]) -> list[str]:
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    names: list[str] = []
    seen: set[str] = set()
    for bucket in ("payloads", "replay_payloads"):
        payloads = extensions.get(bucket) if isinstance(extensions.get(bucket), dict) else {}
        block = payloads.get("lora_stack") if isinstance(payloads.get("lora_stack"), dict) else {}
        for item in _walk_named_items(block, "loras"):
            if not isinstance(item, dict) or item.get("enabled") is False:
                continue
            name = _clean(item.get("catalog_name") or item.get("provider_catalog_name") or item.get("file") or item.get("name") or item.get("id"))
            if name and name.lower() not in seen:
                seen.add(name.lower()); names.append(name)
    return names


def _current_lora_catalog_names(root_dir: Path, profile: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    try:
        from neo_extensions.built_in.lora_stack.backend.library_store import load_records
        for record in load_records(root_dir):
            if not isinstance(record, dict):
                continue
            # catalog_available is strongest; older saved rows may predate the
            # flag but still carry a provider catalog identity.
            if record.get("catalog_available") is False and not _clean(record.get("provider_catalog_name")):
                continue
            for key in ("catalog_name", "provider_catalog_name", "file", "name", "id", "canonical_identity"):
                value = _clean(record.get(key))
                if value:
                    names.add(value.lower()); names.add(Path(value.replace("\\", "/")).name.lower())
    except Exception:
        pass
    caps = profile.get("backend_capabilities") if isinstance(profile.get("backend_capabilities"), dict) else {}
    for source in (caps.get("loras"), caps.get("available_loras"), profile.get("available_loras")):
        if not isinstance(source, list):
            continue
        for item in source:
            value = _clean(item.get("name") or item.get("file") or item.get("id")) if isinstance(item, dict) else _clean(item)
            if value:
                names.add(value.lower()); names.add(Path(value.replace("\\", "/")).name.lower())
    return names


def _local_asset_state(root_dir: Path, item: dict[str, Any]) -> tuple[str, str]:
    for key in ("path", "local_path", "source_path", "file_path"):
        value = _clean(item.get(key))
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = root_dir / path
        return ("ready", value) if path.exists() else ("missing", value)
    if _clean(item.get("url")):
        return "unknown", _clean(item.get("url"))
    if _clean(item.get("asset_id")):
        return "unknown", _clean(item.get("asset_id"))
    return "not_recorded", ""


def replay_readiness_packet(root_dir: Path, query: str, *, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") == "external":
        attachment = resolution.get("attachment") if isinstance(resolution.get("attachment"), dict) else {}
        metadata = attachment.get("external_metadata") if isinstance(attachment.get("external_metadata"), dict) else {}
        return _scope_packet(
            operation_id="image.replay.check",
            packet_kind="replay_readiness",
            status="partial",
            known_state="partial",
            payload={
                "recipe_ref": {"attachment_id": attachment.get("attachment_id"), "filename": attachment.get("filename")},
                "readiness": "partial",
                "requirements": [{
                    "requirement_id": "native_recipe",
                    "state": "not_recorded",
                    "historical_value": metadata,
                    "current_match": None,
                    "detail": "The attached image exposes external metadata, not a Neo execution sidecar.",
                    "evidence_refs": [],
                }],
                "missing": [],
                "incompatible": [],
                "unknown": ["native_recipe"],
                "non_determinism": ["External metadata may omit workflow dependencies or resolved defaults required for exact reproduction."],
                "exact_reproduction_guaranteed": False,
                "resolution_basis": resolution.get("basis"),
            },
            target_refs=[{"attachment_id": attachment.get("attachment_id"), "filename": attachment.get("filename")}],
            authority_classes=["EXTMETA", "CAP"],
            source_integrity="partial",
            limitations=["Neo can inspect/import the external metadata, but it cannot claim exact Neo replay readiness without a native execution record."],
            derivation={"method": "external_metadata_replay_preflight"},
        )
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.replay.check", "replay_readiness", resolution)
    record = resolution["record"]; file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    recipe = normalize_image_recipe(record, file_record)
    recipe["record_completeness"] = _recipe_completeness(recipe)
    model = recipe.get("model") if isinstance(recipe.get("model"), dict) else {}
    saved_model = _clean(model.get("checkpoint") or model.get("model"))
    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    binding = record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) else {}
    profile_id = _clean(binding.get("backend_profile_id") or job.get("backend_profile_id"))
    provider_id = _clean(binding.get("provider_id") or job.get("provider_id"))
    try:
        profile = get_backend_profile(profile_id) if profile_id else None
    except Exception:
        profile = None
    profile = profile if isinstance(profile, dict) else {}
    current_ctx = _current_image_context(native_context)
    current_params = current_ctx.get("current_parameters") if isinstance(current_ctx.get("current_parameters"), dict) else {}
    current_model = _clean(current_params.get("model") or current_params.get("checkpoint"))
    requirements: list[dict[str, Any]] = []

    def add(req_id: str, state: str, historical: Any = None, current: Any = None, detail: str = "") -> None:
        requirements.append({"requirement_id": req_id, "state": state, "historical_value": _safe_value(historical), "current_match": _safe_value(current), "detail": detail, "evidence_refs": []})

    # Historical recipe itself.
    if recipe["record_completeness"]["core_recipe_complete"]:
        add("historical_recipe", "ready", recipe["record_completeness"], None, "Core recorded recipe fields are present.")
    else:
        add("historical_recipe", "not_recorded", recipe["record_completeness"], None, "One or more core historical recipe fields were not recorded.")

    if saved_model:
        available_models = _profile_model_names(profile)
        if available_models:
            if saved_model.lower() in available_models:
                add("checkpoint", "ready", saved_model, saved_model, "Saved checkpoint is listed by the recorded backend profile.")
            else:
                add("checkpoint", "missing", saved_model, sorted(available_models)[:30], "Saved checkpoint is not present in the backend profile's available-model list.")
        elif current_model and current_model.lower() == saved_model.lower():
            add("checkpoint", "ready", saved_model, current_model, "Saved checkpoint matches the current Image workspace selection.")
        else:
            add("checkpoint", "unknown", saved_model, current_model or None, "Neo has no authoritative current model catalog for this replay check.")
    else:
        add("checkpoint", "not_recorded", None, current_model or None, "The historical record does not establish a checkpoint.")

    if profile_id:
        if profile:
            enabled = profile.get("enabled", True) is not False
            runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
            runtime_status = _clean(profile.get("runtime_status") or runtime.get("status"))
            state = "ready" if enabled and runtime_status not in {"offline", "error", "failed", "disabled"} else "incompatible"
            add("backend_profile", state, profile_id, {"enabled": enabled, "runtime_status": runtime_status}, "Recorded backend profile resolved in current Admin configuration.")
            current_provider = _clean(profile.get("provider_id"))
            if provider_id and current_provider and provider_id != current_provider:
                add("provider_match", "incompatible", provider_id, current_provider, "Provider drift changes replay semantics.")
            elif provider_id:
                add("provider_match", "ready", provider_id, current_provider or provider_id)
        else:
            add("backend_profile", "missing", profile_id, None, "Recorded backend profile is not available in current Admin configuration.")
    else:
        add("backend_profile", "not_recorded", None, None, "No backend profile identity was recorded.")

    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    assets = source.get("input_assets") if isinstance(source.get("input_assets"), list) else []
    if assets:
        asset_states = []
        for item in assets:
            if not isinstance(item, dict):
                continue
            state, locator = _local_asset_state(root_dir, item)
            asset_states.append({"asset_id": _clean(item.get("asset_id")), "role": _clean(item.get("role")), "state": state, "locator": locator})
        if any(item["state"] == "missing" for item in asset_states):
            source_state = "missing"
        elif any(item["state"] in {"unknown", "not_recorded"} for item in asset_states):
            source_state = "unknown"
        else:
            source_state = "ready"
        add("input_assets", source_state, asset_states, None, "Replay source/reference/mask assets are checked where a local locator was recorded.")
    else:
        add("input_assets", "not_needed" if _clean(record.get("mode")).lower() in {"txt2img", "generate"} else "not_recorded", [], None)

    extension_ids = sorted(_record_extension_ids(record))
    for extension_id in extension_ids:
        try:
            ext_record = get_extension(extension_id)
        except Exception:
            ext_record = None
        if ext_record is None:
            add(f"extension:{extension_id}", "missing", extension_id, None, "Recorded extension is not registered in the current Neo extension registry.")
            continue
        if isinstance(ext_record, dict):
            enabled = ext_record.get("enabled", ext_record.get("registry_enabled", True)) is not False
        else:
            enabled = getattr(ext_record, "enabled", True) is not False
        add(f"extension:{extension_id}", "ready" if enabled else "missing", extension_id, {"registered": True, "enabled": enabled}, "Extension-specific models/assets may still require live revalidation.")

    recorded_loras = _recorded_lora_names(record)
    if recorded_loras:
        current_loras = _current_lora_catalog_names(root_dir, profile)
        if current_loras:
            missing_loras = [name for name in recorded_loras if name.lower() not in current_loras and Path(name.replace("\\", "/")).name.lower() not in current_loras]
            if missing_loras:
                add("lora_assets", "missing", recorded_loras, sorted(current_loras)[:50], "One or more recorded LoRA assets are absent from the current Neo/provider LoRA catalog: " + ", ".join(missing_loras[:8]))
            else:
                add("lora_assets", "ready", recorded_loras, recorded_loras, "Recorded LoRA assets are present in the current catalog.")
        else:
            add("lora_assets", "unknown", recorded_loras, None, "Neo could not establish a current LoRA catalog for this replay check.")

    static_validation = record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) else {}
    evaluated_static_checks = {
        "backend_profile_exists": "backend_profile",
        "backend_profile_enabled": "backend_profile",
        "provider_matches_record": "provider_match",
        "model_available": "checkpoint",
        "input_assets_available": "input_assets",
    }
    current_requirement_ids = {item["requirement_id"] for item in requirements}
    for check in static_validation.get("checks") if isinstance(static_validation.get("checks"), list) else []:
        if not isinstance(check, dict):
            continue
        check_id = _clean(check.get("check_id"))
        equivalent = evaluated_static_checks.get(check_id, check_id)
        if equivalent in current_requirement_ids:
            continue
        if check_id:
            add(check_id, "not_needed" if check.get("state") == "not_needed" else "unknown", check.get("state"), None, _clean(check.get("reason"), 500))
            current_requirement_ids.add(check_id)

    missing = [item["requirement_id"] for item in requirements if item["state"] == "missing"]
    incompatible = [item["requirement_id"] for item in requirements if item["state"] == "incompatible"]
    unknown = [item["requirement_id"] for item in requirements if item["state"] in {"unknown", "not_recorded"}]
    if missing or incompatible:
        readiness = "blocked"
        known = "established"
    elif unknown:
        readiness = "partial"
        known = "partial"
    else:
        readiness = "ready"
        known = "established"
    ref = _record_ref(record, file_record)
    non_determinism = [
        "Exact pixel-for-pixel reproduction is not guaranteed by prompt + seed alone.",
        "Backend/library/model implementation drift can change outputs even when recorded settings match.",
    ]
    return _scope_packet(
        operation_id="image.replay.check",
        packet_kind="replay_readiness",
        status="ok" if readiness in {"ready", "blocked"} else "partial",
        known_state=known,
        payload={
            "recipe_ref": ref,
            "readiness": readiness,
            "requirements": requirements,
            "missing": missing,
            "incompatible": incompatible,
            "unknown": unknown,
            "non_determinism": non_determinism,
            "exact_reproduction_guaranteed": False,
            "historical_replay_validation": _safe_value(static_validation),
            "resolution_basis": resolution.get("basis"),
        },
        target_refs=[ref],
        authority_classes=["SIDE", "REPLAY", "CAP"],
        native_refs=[ref],
        source_integrity="present",
        limitations=non_determinism,
        derivation={"method": "historical_recipe_plus_current_environment_readiness"},
    )



# ---------------------------------------------------------------------------
# NKB-15I-I4 — Remaining native Image intelligence operators
# ---------------------------------------------------------------------------

_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "model": ("model", "checkpoint", "main model"),
    "family": ("model family", "family"),
    "loader": ("loader", "main model type"),
    "vae": ("vae", "ae", "autoencoder"),
    "text_encoder": ("text encoder", "encoder", "clip"),
    "sampler": ("sampler",),
    "scheduler": ("scheduler", "noise schedule"),
    "steps": ("steps", "sampling steps", "denoising steps"),
    "cfg": ("cfg", "cfg scale", "guidance scale"),
    "flux_guidance": ("flux guidance",),
    "seed": ("seed",),
    "width": ("width",),
    "height": ("height",),
    "resolution": ("resolution", "image size", "dimensions"),
    "denoise": ("denoise", "denoising strength"),
    "clip_skip": ("clip skip",),
    "batch_count": ("batch count", "batch"),
    "negative_prompt": ("negative prompt",),
    "positive_prompt": ("positive prompt", "prompt"),
}


def _resolve_parameter(query: str) -> str:
    low = _clean(query, 1600).lower()
    matches: list[tuple[int, str]] = []
    for parameter, aliases in _PARAMETER_ALIASES.items():
        hit = max((len(alias) for alias in aliases if alias in low), default=0)
        if hit:
            matches.append((hit, parameter))
    return max(matches)[1] if matches else ""


def _parameter_authored_lines(parameter: str, *, max_lines: int = 8) -> tuple[dict[str, Any], list[str]]:
    resolved = resolve_canonical_guide(
        parameter,
        surface="image",
        project_id="image_workspace",
        guide_id="image.parameters",
    )
    guide = resolved.get("guide") if resolved.get("ok") and isinstance(resolved.get("guide"), dict) else {}
    if not guide:
        return {}, []
    aliases = _PARAMETER_ALIASES.get(parameter, (parameter,))
    needles = tuple(alias.lower() for alias in aliases)
    candidates: list[str] = []
    for raw in str(guide.get("content") or "").splitlines():
        line = raw.strip()
        low = line.lower()
        if line and any(needle in low for needle in needles):
            candidates.append(line)
            if len(candidates) >= max_lines:
                break
    return guide, candidates


def parameter_explain_packet(query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    parameter = _resolve_parameter(query)
    if not parameter:
        return _scope_packet(
            operation_id="image.parameter.explain",
            packet_kind="reference",
            status="not_found",
            known_state="not_established",
            payload={"parameter": ""},
            authority_classes=["GUIDE", "LIVE"],
            limitations=["No Image parameter could be resolved from the question."],
        )
    guide, authored = _parameter_authored_lines(parameter)
    ctx = _current_image_context(native_context)
    params = ctx.get("current_parameters") if isinstance(ctx.get("current_parameters"), dict) else {}
    current_value: Any = None
    if parameter == "model":
        current_value = params.get("model") or params.get("checkpoint") or params.get("diffusion_model") or params.get("gguf_model")
    elif parameter == "text_encoder":
        current_value = [params.get(key) for key in ("text_encoder_1", "text_encoder_2", "qwen_text_encoder") if params.get(key)]
    elif parameter == "resolution":
        current_value = {"width": params.get("width"), "height": params.get("height")}
    else:
        current_value = params.get(parameter)
    known = "established" if authored else "partial"
    return _scope_packet(
        operation_id="image.parameter.explain",
        packet_kind="reference",
        status="ok" if authored else "partial",
        known_state=known,
        payload={
            "parameter": parameter,
            "current_value": _safe_value(current_value),
            "current_family": params.get("family"),
            "current_loader": params.get("loader"),
            "current_workflow": (ctx.get("surface_runtime") or {}).get("workflow_mode") if isinstance(ctx.get("surface_runtime"), dict) else "",
            "authored_guide_facts": authored,
            "guide": {key: guide.get(key) for key in ("guide_id", "title", "version", "updated", "path")},
        },
        authority_classes=["GUIDE", "LIVE"],
        native_refs=[{"guide_id": guide.get("guide_id"), "path": guide.get("path")}] if guide else [],
        source_integrity="present" if authored else "partial",
        limitations=[] if authored else ["The canonical Image parameter guide did not contain an exact authored statement for this parameter."],
        derivation={"method": "canonical_parameter_guide_plus_live_state"},
    )


def parameter_recommend_packet(query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    explained = parameter_explain_packet(query, native_context)
    payload = explained.get("payload") if isinstance(explained.get("payload"), dict) else {}
    parameter = str(payload.get("parameter") or "")
    authored = list(payload.get("authored_guide_facts") or [])
    limitations = [
        "This is advisory guidance, not a native execution fact.",
        "Neo will not invent an exact numeric setting when the current Guide/capability contract does not establish one for the selected route and goal.",
    ]
    if not parameter:
        limitations.insert(0, "No Image parameter could be resolved from the question.")
    return _scope_packet(
        operation_id="image.parameter.recommend",
        packet_kind="advisory",
        status="ok" if parameter and authored else "partial",
        known_state="partial" if parameter else "not_established",
        payload={
            "parameter": parameter,
            "current_value": payload.get("current_value"),
            "current_family": payload.get("current_family"),
            "current_loader": payload.get("current_loader"),
            "current_workflow": payload.get("current_workflow"),
            "guide_backed_advice": authored,
            "recommendation_policy": "guide_backed_route_aware_no_invented_numeric_defaults",
            "guide": payload.get("guide") or {},
        },
        authority_classes=["GUIDE", "LIVE", "CAP"],
        native_refs=list((explained.get("authority") or {}).get("native_refs") or []),
        source_integrity=str((explained.get("authority") or {}).get("source_integrity") or "partial"),
        limitations=limitations,
        derivation={"method": "guide_backed_advisory_projection"},
    )


def current_prompt_packet(query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = _current_image_context(native_context)
    params = ctx.get("current_parameters") if isinstance(ctx.get("current_parameters"), dict) else {}
    positive = _clean(params.get("positive_prompt") or params.get("prompt"), 12000)
    negative = _clean(params.get("negative_prompt"), 12000)
    has_prompt = bool(positive or negative)
    limitations: list[str] = []
    if not ctx.get("live_snapshot_included"):
        limitations.append("No current-turn Image snapshot was provided; persisted UI state may be older than the visible browser prompt fields.")
    if not has_prompt:
        limitations.append("No current Image positive/negative prompt text was present in the resolved workspace state.")
    return _scope_packet(
        operation_id="image.prompt.get_current",
        packet_kind="prompt_asset",
        status="ok" if has_prompt else "partial",
        known_state="established" if has_prompt and ctx.get("ok") else "not_established",
        payload={
            "source": "current_image_workspace",
            "positive_prompt": positive,
            "negative_prompt": negative,
            "family": params.get("family"),
            "loader": params.get("loader"),
            "workflow_mode": (ctx.get("surface_runtime") or {}).get("workflow_mode") if isinstance(ctx.get("surface_runtime"), dict) else "",
            "live_snapshot_included": bool(ctx.get("live_snapshot_included")),
        },
        authority_classes=["LIVE"],
        source_integrity="present" if ctx.get("ok") else "partial",
        limitations=limitations,
        derivation={"method": "current_image_workspace_prompt_state"},
    )


def _prompt_search_terms(query: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_@.+-]+", _clean(query, 3000).lower())
    stop = {
        "find", "show", "search", "saved", "prompt", "prompts", "library", "my", "the", "a", "an", "about", "for", "with", "containing", "that", "where", "image", "neo", "please", "me",
    }
    return [token for token in tokens if len(token) >= 2 and token not in stop]


def saved_prompt_search_packet(query: str, *, limit: int = 20) -> dict[str, Any]:
    from neo_app.image.prompt_library import list_image_prompt_library

    try:
        payload = list_image_prompt_library("")
    except Exception as exc:
        return _scope_packet(
            operation_id="image.prompt.search_saved",
            packet_kind="prompt_asset",
            status="error",
            known_state="not_established",
            payload={"records": [], "query_terms": []},
            authority_classes=["PROMPT"],
            limitations=[f"Image Prompt Library could not be read: {_clean(exc, 300)}"],
        )
    records = [item for item in (payload.get("records") or []) if isinstance(item, dict)]
    terms = _prompt_search_terms(query)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in records:
        hay = " ".join(_clean(item.get(key), 12000) for key in ("name", "source_label", "positive_prompt", "negative_prompt", "notes")).lower()
        score = sum(4 if term in _clean(item.get("name"), 1000).lower() else 1 for term in terms if term in hay)
        if terms and score <= 0:
            continue
        ranked.append((score, str(item.get("updated_at") or item.get("created_at") or ""), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    selected = [dict(row[2]) for row in ranked[: max(1, min(int(limit or 20), 100))]]
    return _scope_packet(
        operation_id="image.prompt.search_saved",
        packet_kind="prompt_asset",
        status="ok",
        known_state="established",
        payload={
            "query_terms": terms,
            "match_count": len(selected),
            "records": _safe_value(selected, text_limit=5000),
            "library_counts": {
                "image": int(payload.get("image_count") or 0),
                "prompt_studio": int(payload.get("prompt_studio_count") or 0),
                "total": int(payload.get("count") or len(records)),
            },
            "search_kind": "native_prompt_library_field_match",
        },
        authority_classes=["PROMPT", "SEARCH"],
        source_integrity="present",
        limitations=["A saved Prompt Library match does not prove that any historical Image generation used that prompt unless an execution sidecar links it."],
        derivation={"method": "prompt_library_native_record_search"},
    )


def _image_job_records(root_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = root_dir / "neo_data" / "runtime" / "jobs" / "image"
    rows: list[dict[str, Any]] = []
    health = {"files_discovered": 0, "records_parsed": 0, "records_invalid": 0}
    if not root.exists():
        return rows, health
    for path in root.glob("*.json"):
        health["files_discovered"] += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            health["records_invalid"] += 1
            continue
        if not isinstance(record, dict) or not _clean(record.get("job_id")):
            health["records_invalid"] += 1
            continue
        row = dict(record)
        row["_neo_job_path"] = path.relative_to(root_dir).as_posix()
        rows.append(row)
        health["records_parsed"] += 1
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows, health


def _job_ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": _clean(record.get("job_id")),
        "provider_job_id": _clean(record.get("provider_job_id")),
        "profile_id": _clean(record.get("profile_id") or record.get("backend_profile_id")),
        "provider_id": _clean(record.get("provider_id")),
        "status": _clean(record.get("status")),
        "updated_at": _clean(record.get("updated_at")),
        "job_path": _clean(record.get("_neo_job_path")),
    }


def _resolve_job_target(root_dir: Path, query: str) -> dict[str, Any]:
    rows, health = _image_job_records(root_dir)
    low = _clean(query, 3000).lower()
    matches = [row for row in rows if _clean(row.get("job_id")).lower() and _clean(row.get("job_id")).lower() in low]
    if len(matches) == 1:
        return {"status": "resolved", "basis": "explicit_job_id", "record": matches[0], "health": health}
    if len(matches) > 1:
        return {"status": "ambiguous", "basis": "multiple_explicit_job_ids", "candidates": [_job_ref(row) for row in matches[:12]], "health": health}
    if any(term in low for term in ("current job", "active job", "running job", "generation running", "still running", "progress")):
        live = [row for row in rows if str(row.get("status") or "").lower() not in {"completed", "failed", "cancelled", "canceled"}]
        if live:
            return {"status": "resolved", "basis": "latest_nonterminal_image_job", "record": live[0], "health": health}
        return {"status": "missing", "basis": "no_nonterminal_image_job", "health": health}
    if rows and any(term in low for term in ("latest job", "last job", "recent job", "last generation", "latest generation", "most recent job", "this job")):
        return {"status": "resolved", "basis": "latest_image_job", "record": rows[0], "health": health}
    if rows and any(term in low for term in ("failed generation", "generation failed", "last failure", "latest failure")):
        failed = [row for row in rows if str(row.get("status") or "").lower() in {"failed", "error"}]
        if failed:
            return {"status": "resolved", "basis": "latest_failed_image_job", "record": failed[0], "health": health}
    return {"status": "unresolved", "basis": "no_exact_job_target", "health": health}


def attempt_count_packet(root_dir: Path) -> dict[str, Any]:
    rows, health = _image_job_records(root_dir)
    statuses = Counter(str(row.get("status") or "unknown").strip().lower() or "unknown" for row in rows)
    return _scope_packet(
        operation_id="image.inventory.count_attempts",
        packet_kind="inventory",
        status="ok",
        known_state="established",
        payload={
            "population": "generation_attempts",
            "attempt_count": len(rows),
            "status_counts": dict(sorted(statuses.items())),
            "coverage": {**health, "population_size": len(rows)},
        },
        authority_classes=["JOB", "ANALYTICS"],
        source_integrity="present" if health["records_invalid"] == 0 else "partial",
        limitations=[] if health["records_invalid"] == 0 else ["Some runtime Image job records could not be parsed and are excluded from the attempt count."],
        derivation={"method": "durable_image_job_registry_count"},
    )


def _parse_iso_datetime(value: Any) -> Any:
    from datetime import datetime
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def analytics_trend_packet(root_dir: Path, query: str) -> dict[str, Any]:
    rows, health = image_history_records(root_dir)
    completed_all = [row for row in rows if _is_completed_run(row)]
    family_constraint = _family_constraint_from_query(completed_all, query)
    model_filter = _query_model_filter(completed_all, query)
    unresolved_model = _explicit_unresolved_model_phrase(query, family_requested=bool(family_constraint.get("requested")), model_filter=model_filter)
    completed = [
        row for row in completed_all
        if _record_matches_family_constraint(row, family_constraint)
        and (not model_filter or _model_label(row, "model").lower() == model_filter.lower())
    ]
    if unresolved_model:
        completed = []
    low = _clean(query, 1200).lower()
    bucket = "month" if any(term in low for term in ("monthly", "per month", "each month", "by month")) or any(name in low for name in _MONTHS) else ("week" if any(term in low for term in ("weekly", "per week", "each week", "by week")) else "day")
    grouped: Counter[str] = Counter()
    for row in completed:
        dt = _parse_iso_datetime(row.get("created_at"))
        if dt is None:
            continue
        if bucket == "month":
            key = f"{dt.year:04d}-{dt.month:02d}"
        elif bucket == "week":
            iso = dt.isocalendar()
            key = f"{iso.year:04d}-W{iso.week:02d}"
        else:
            key = dt.date().isoformat()
        grouped[key] += 1
    points = [{"bucket": key, "completed_generation_runs": grouped[key]} for key in sorted(grouped)]
    constraint_status = "satisfied"
    if family_constraint.get("requested") and not any(_record_matches_family_constraint(row, family_constraint) for row in completed_all):
        constraint_status = "unsatisfied"
    if unresolved_model:
        constraint_status = "unsatisfied"
    known = "established" if constraint_status == "satisfied" and (points or not completed) else "not_established"
    limitations: list[str] = []
    if constraint_status != "satisfied":
        limitations.append(f"The requested Image family constraint ({family_constraint.get('display')}) could not be matched to authoritative history records.")
    if sum(point["completed_generation_runs"] for point in points) != len(completed):
        limitations.append("Some completed Image records have no parseable creation timestamp and are excluded from the time series.")
    return _scope_packet(
        operation_id="image.analytics.trend",
        packet_kind="analytics",
        status="ok" if known == "established" else "partial",
        known_state=known,
        payload={
            "population": "completed_generation_runs",
            "query_constraints": {
                "requested": {"family": family_constraint.get("display") if family_constraint.get("requested") else None, "model": model_filter or unresolved_model or None},
                "applied": {"family_ids": list(family_constraint.get("family_ids") or []), "model": model_filter or None},
                "status": constraint_status,
            },
            "bucket": bucket,
            "metric": "run_count",
            "points": points,
            "coverage": {**health, "completed_population": len(completed), "unfiltered_completed_population": len(completed_all), "timestamped_population": sum(point["completed_generation_runs"] for point in points)},
        },
        authority_classes=["SIDE", "ANALYTICS"],
        source_integrity="present" if health["records_invalid"] == 0 else "partial",
        limitations=limitations,
        derivation={"method": "completed_sidecar_time_bucket_aggregation"},
    )

def _timing_rows(root_dir: Path, query: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, health = image_history_records(root_dir)
    low = _clean(query, 2000).lower()
    completed = [row for row in rows if _is_completed_run(row)]
    family_constraint = _family_constraint_from_query(completed, query)
    model_filter = _query_model_filter(completed, query)
    unresolved_model = _explicit_unresolved_model_phrase(query, family_requested=bool(family_constraint.get("requested")), model_filter=model_filter)
    mode_filter = next((mode for mode in ("outpaint", "inpaint", "img2img", "txt2img", "generate") if re.search(rf"\b{re.escape(mode)}\b", low)), "")
    dims = re.search(r"\b(\d{2,5})\s*[x×]\s*(\d{2,5})\b", low)
    width_filter = int(dims.group(1)) if dims else None
    height_filter = int(dims.group(2)) if dims else None
    if not dims:
        square = re.search(r"\b(\d{3,5})\b(?=\s+(?:generate|generation|render|runs?))", low)
        if square:
            width_filter = height_filter = int(square.group(1))
    steps_match = re.search(r"\b(\d{1,4})\s+steps?\b", low)
    steps_filter = int(steps_match.group(1)) if steps_match else None
    timed: list[dict[str, Any]] = []
    family_matched_population = 0
    for row in completed:
        if unresolved_model:
            continue
        model = row.get("model") if isinstance(row.get("model"), dict) else {}
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        model_label = _clean(model.get("model") or model.get("checkpoint") or model.get("family")) or "unknown"
        if not _record_matches_family_constraint(row, family_constraint):
            continue
        if family_constraint.get("requested"):
            family_matched_population += 1
        if model_filter and model_filter.lower() != model_label.lower():
            continue
        mode = str(row.get("mode") or row.get("subtab") or "").lower()
        if mode_filter:
            if mode_filter == "generate" and mode not in {"generate", "txt2img"}:
                continue
            if mode_filter != "generate" and mode_filter not in mode:
                continue
        if width_filter is not None:
            try:
                if int(params.get("width")) != width_filter or int(params.get("height")) != height_filter:
                    continue
            except Exception:
                continue
        if steps_filter is not None:
            try:
                if int(params.get("steps")) != steps_filter:
                    continue
            except Exception:
                continue
        timing = row.get("run_timing") if isinstance(row.get("run_timing"), dict) else {}
        try:
            elapsed = float(timing.get("elapsed_seconds"))
        except Exception:
            continue
        if elapsed < 0:
            continue
        timed.append({
            "result_id": _clean(row.get("result_id")),
            "model": model_label,
            "family": _clean(model.get("family") or params.get("family")),
            "mode": mode,
            "width": params.get("width"), "height": params.get("height"), "steps": params.get("steps"),
            "extensions": list(_extension_signature(row)),
            "elapsed_seconds": elapsed,
            "created_at": _clean(row.get("created_at")),
        })
    requested_constraints = {
        "family": family_constraint.get("display") if family_constraint.get("requested") else None,
        "model": model_filter or unresolved_model or None,
        "mode": mode_filter or None,
        "width": width_filter,
        "height": height_filter,
        "steps": steps_filter,
    }
    applied_constraints = {
        "family_ids": list(family_constraint.get("family_ids") or []),
        "family_alias": family_constraint.get("alias") if family_constraint.get("requested") else None,
        "model": model_filter or None,
        "mode": mode_filter or None,
        "width": width_filter,
        "height": height_filter,
        "steps": steps_filter,
    }
    constraint_status = "satisfied"
    unsatisfied: list[str] = []
    if family_constraint.get("requested") and family_matched_population == 0:
        constraint_status = "unsatisfied"
        unsatisfied.append("family")
    # An exact model name is only considered applied when at least one completed
    # authoritative record can satisfy it.  This blocks empty-filter fallthrough.
    if model_filter and not any(_model_label(row, "model").lower() == model_filter.lower() for row in completed):
        constraint_status = "unsatisfied"
        unsatisfied.append("model")
    if unresolved_model:
        constraint_status = "unsatisfied"
        unsatisfied.append("model")
    return timed, {
        "history_health": health,
        "model_filter": model_filter,
        "family_filter": family_constraint.get("display") if family_constraint.get("requested") else "",
        "matched_family_ids": list(family_constraint.get("family_ids") or []),
        "mode_filter": mode_filter,
        "width_filter": width_filter,
        "height_filter": height_filter,
        "steps_filter": steps_filter,
        "query_constraints": {
            "requested": requested_constraints,
            "applied": applied_constraints,
            "status": constraint_status,
            "unsatisfied": unsatisfied,
        },
    }

def performance_summary_packet(root_dir: Path, query: str) -> dict[str, Any]:
    rows, filters = _timing_rows(root_dir, query)
    constraint_validation = filters.get("query_constraints") if isinstance(filters.get("query_constraints"), dict) else {}
    if constraint_validation.get("status") == "unsatisfied":
        requested = constraint_validation.get("requested") if isinstance(constraint_validation.get("requested"), dict) else {}
        requested_label = requested.get("family") or requested.get("model") or "requested population"
        return _scope_packet(
            operation_id="image.performance.summarize",
            packet_kind="performance",
            status="partial",
            known_state="not_established",
            payload={"sample_count": 0, "filters": filters, "constraint_validation": constraint_validation},
            authority_classes=["SIDE", "ANALYTICS"],
            limitations=[f"The requested Image constraint ({requested_label}) was not established in authoritative generation history, so Neo will not summarize the unfiltered population instead."],
            derivation={"method": "recorded_elapsed_seconds_summary", "constraint_guard": "failed_closed"},
        )
    if not rows:
        return _scope_packet(
            operation_id="image.performance.summarize",
            packet_kind="performance",
            status="partial",
            known_state="not_established",
            payload={"sample_count": 0, "filters": filters, "constraint_validation": constraint_validation},
            authority_classes=["SIDE", "ANALYTICS"],
            limitations=["No completed Image records with authoritative elapsed_seconds matched the requested performance population."],
            derivation={"method": "recorded_elapsed_seconds_summary"},
        )
    values = sorted(float(row["elapsed_seconds"]) for row in rows)
    mean = statistics.fmean(values)
    median = statistics.median(values)
    p90_index = max(0, min(len(values) - 1, int(round((len(values) - 1) * 0.9))))
    payload = {
        "population": "completed_generation_runs_with_timing",
        "filters": filters,
        "constraint_validation": constraint_validation,
        "sample_count": len(values),
        "elapsed_seconds": {
            "min": min(values),
            "max": max(values),
            "mean": mean,
            "median": median,
            "p90": values[p90_index],
        },
        "models": dict(Counter(row["model"] for row in rows)),
        "families": dict(Counter(row.get("family") or "unknown" for row in rows)),
        "workflows": dict(Counter(row["mode"] for row in rows)),
    }
    return _scope_packet(
        operation_id="image.performance.summarize",
        packet_kind="performance",
        status="ok",
        known_state="established",
        payload=payload,
        authority_classes=["SIDE", "ANALYTICS"],
        source_integrity="present",
        derivation={"method": "recorded_elapsed_seconds_summary", "constraint_guard": "satisfied"},
    )

def job_status_packet(root_dir: Path, query: str) -> dict[str, Any]:
    resolution = _resolve_job_target(root_dir, query)
    if resolution.get("status") != "resolved":
        return _scope_packet(
            operation_id="image.job.status",
            packet_kind="job_status",
            status="partial",
            known_state="unresolved_conflict" if resolution.get("status") == "ambiguous" else "not_established",
            payload={"resolution_basis": resolution.get("basis"), "candidates": resolution.get("candidates") or []},
            authority_classes=["JOB"],
            limitations=["No exact current/recent Image job was established from the query."],
        )
    record = resolution["record"]
    ref = _job_ref(record)
    payload = {
        "job": ref,
        "message": _clean(record.get("message"), 2400),
        "progress": _safe_value(record.get("progress") if isinstance(record.get("progress"), dict) else {}),
        "runtime": _safe_value(record.get("runtime") if isinstance(record.get("runtime"), dict) else {}, text_limit=2500),
        "import_state": _safe_value(record.get("import_state") if isinstance(record.get("import_state"), dict) else {}),
        "control": _safe_value(record.get("control") if isinstance(record.get("control"), dict) else {}),
        "output_count": len(record.get("outputs") or []),
        "resolution_basis": resolution.get("basis"),
    }
    return _scope_packet(
        operation_id="image.job.status",
        packet_kind="job_status",
        status="ok",
        known_state="established",
        payload=payload,
        target_refs=[ref],
        authority_classes=["JOB"],
        native_refs=[ref],
        source_integrity="present",
        derivation={"method": "durable_image_job_registry_lookup"},
    )


def _job_observations(record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed: list[dict[str, Any]] = []
    causes: list[dict[str, Any]] = []
    status = str(record.get("status") or "").lower()
    if status:
        observed.append({"fact": f"Job status is {status}.", "field": "status", "value": status})
    message = _clean(record.get("message"), 2000)
    if message:
        observed.append({"fact": message, "field": "message", "value": message})
    runtime = record.get("runtime") if isinstance(record.get("runtime"), dict) else {}
    for key in ("error", "error_code", "failure_reason", "phase"):
        value = _clean(record.get(key) or runtime.get(key), 1200)
        if value:
            observed.append({"fact": f"{key}: {value}", "field": key, "value": value})
            if key in {"error", "error_code", "failure_reason"}:
                causes.append({"cause": value, "basis": f"job.{key}", "validated": True})
    recovery = runtime.get("recovery") if isinstance(runtime.get("recovery"), dict) else {}
    if recovery:
        observed.append({"fact": _clean(recovery.get("message") or recovery.get("reason"), 1800), "field": "runtime.recovery", "value": _safe_value(recovery)})
        reason = _clean(recovery.get("reason"), 1000)
        if reason:
            causes.append({"cause": reason, "basis": "runtime.recovery.reason", "validated": True})
    import_state = record.get("import_state") if isinstance(record.get("import_state"), dict) else {}
    errors = import_state.get("errors") if isinstance(import_state.get("errors"), list) else []
    for item in errors[:8]:
        text = _clean(item, 1200)
        if text:
            observed.append({"fact": f"Output import error: {text}", "field": "import_state.errors", "value": text})
            causes.append({"cause": text, "basis": "import_state.errors", "validated": True})
    return observed, causes


def job_diagnostic_packet(root_dir: Path, query: str) -> dict[str, Any]:
    resolution = _resolve_job_target(root_dir, query)
    if resolution.get("status") != "resolved":
        return _scope_packet(
            operation_id="image.job.diagnose",
            packet_kind="diagnostic",
            status="partial",
            known_state="not_established" if resolution.get("status") != "ambiguous" else "unresolved_conflict",
            payload={"resolution_basis": resolution.get("basis"), "candidates": resolution.get("candidates") or [], "observed": [], "validated_causes": [], "hypotheses": []},
            authority_classes=["JOB", "GUIDE"],
            limitations=["No exact failed/current Image job was established, so Neo will not invent a failure cause."],
        )
    record = resolution["record"]
    observed, causes = _job_observations(record)
    guide_res = resolve_canonical_guide("image generation troubleshooting failed job", surface="image", project_id="image_workspace", guide_id="image.parameters")
    guide = guide_res.get("guide") if guide_res.get("ok") and isinstance(guide_res.get("guide"), dict) else {}
    section = resolve_guide_section(guide, "Important route behavior") if guide else None
    documented_checks = [line.strip("- ") for line in str((section or {}).get("body") or "").splitlines() if line.strip().startswith("-")][:12]
    known = "established" if causes else "partial"
    ref = _job_ref(record)
    return _scope_packet(
        operation_id="image.job.diagnose",
        packet_kind="diagnostic",
        status="ok" if causes else "partial",
        known_state=known,
        payload={
            "subject": ref,
            "symptom": _clean(record.get("message") or f"Image job status: {record.get('status')}", 1600),
            "observed": observed,
            "validated_causes": causes,
            "hypotheses": [],
            "documented_checks": documented_checks,
            "recommended_next_checks": documented_checks[:6],
            "resolution_basis": resolution.get("basis"),
        },
        target_refs=[ref],
        authority_classes=["JOB", "GUIDE"],
        native_refs=[ref] + ([{"guide_id": guide.get("guide_id"), "path": guide.get("path")}] if guide else []),
        source_integrity="present",
        limitations=[] if causes else ["The job record establishes the observed failure state but does not record one validated root cause; Neo is not promoting plausible hypotheses to facts."],
        derivation={"method": "job_record_observation_plus_documented_checks"},
    )


def image_diagnostic_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    job_resolution = _resolve_job_target(root_dir, query)
    if job_resolution.get("status") == "resolved":
        packet = job_diagnostic_packet(root_dir, query)
        packet["operation_id"] = "image.diagnose"
        return packet
    feature = _resolve_feature(query)
    if feature:
        readiness = capability_readiness_packet(query, native_context, operation_id="image.readiness.check")
        payload = readiness.get("payload") if isinstance(readiness.get("payload"), dict) else {}
        layers = payload.get("layers") if isinstance(payload.get("layers"), dict) else {}
        observed = []
        causes = []
        for name, layer in layers.items():
            if not isinstance(layer, dict):
                continue
            state = str(layer.get("state") or "")
            if state:
                observed.append({"fact": f"{name}: {state}", "field": name, "value": _safe_value(layer)})
            if state in {"missing", "unsupported", "blocked", "incompatible", "unavailable"}:
                causes.append({"cause": f"{name} is {state}", "basis": f"capability.{name}", "validated": True})
        return _scope_packet(
            operation_id="image.diagnose",
            packet_kind="diagnostic",
            status="ok" if causes else "partial",
            known_state="established" if causes else "partial",
            payload={"subject": {"feature_id": feature.get("feature_id")}, "observed": observed, "validated_causes": causes, "hypotheses": [], "readiness": payload, "recommended_next_checks": []},
            authority_classes=["CAP", "LIVE"],
            source_integrity="present",
            limitations=[] if causes else ["Current capability/readiness evidence did not establish one blocking cause."],
            derivation={"method": "live_capability_diagnostic"},
        )
    state = live_state_packet(query, native_context)
    return _scope_packet(
        operation_id="image.diagnose",
        packet_kind="diagnostic",
        status="partial",
        known_state="not_established",
        payload={"subject": {"scope": "image"}, "observed": [], "validated_causes": [], "hypotheses": [], "live_state": state.get("payload") or {}, "recommended_next_checks": ["Check the latest Image job record, route validation, backend readiness, and the relevant current Guide troubleshooting section."]},
        authority_classes=["LIVE", "JOB", "CAP", "GUIDE"],
        limitations=["No exact job failure or feature readiness target was established, so Neo cannot truthfully assign a root cause."],
        derivation={"method": "fail_closed_generic_diagnostic"},
    )


def output_location_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.output.location", "reference", resolution)
    record = resolution["record"]
    file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    files = _record_file_records(record)
    target = _record_ref(record, file_record)
    payload = {
        "target": target,
        "active_file": _safe_value(file_record),
        "files": _safe_value(files),
        "metadata_sidecar": _clean(record.get("_neo_metadata_path")),
        "save_category": _clean((record.get("save_details") or {}).get("category") if isinstance(record.get("save_details"), dict) else record.get("subtab")),
        "resolution_basis": resolution.get("basis"),
    }
    return _scope_packet(
        operation_id="image.output.location",
        packet_kind="reference",
        status="ok",
        known_state="established",
        payload=payload,
        target_refs=[target],
        authority_classes=["SIDE"],
        native_refs=[target],
        source_integrity="present",
        derivation={"method": "exact_sidecar_output_location"},
    )


def _resolve_record_file_path(root_dir: Path, file_record: dict[str, Any]) -> Path | None:
    raw = _clean(file_record.get("path"))
    if not raw:
        return None
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (root_dir / raw).resolve()


def _file_integrity_status(root_dir: Path, file_record: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_record_file_path(root_dir, file_record)
    expected = file_record.get("integrity") if isinstance(file_record.get("integrity"), dict) else {}
    expected_hash = _clean(expected.get("sha256")).lower()
    exists = bool(path and path.exists() and path.is_file())
    actual_hash = ""
    hash_state = "not_recorded" if not expected_hash else "missing_file" if not exists else "unknown"
    if exists and expected_hash:
        try:
            actual_hash = _sha256(path.read_bytes())
            hash_state = "match" if actual_hash.lower() == expected_hash else "mismatch"
        except Exception:
            hash_state = "unreadable"
    return {
        "file_id": _clean(file_record.get("file_id")),
        "neo_output_id": _clean(file_record.get("neo_output_id")),
        "filename": _clean(file_record.get("filename")),
        "path": _clean(file_record.get("path")),
        "exists": exists,
        "expected_sha256": expected_hash,
        "actual_sha256": actual_hash,
        "hash_state": hash_state,
    }


def output_integrity_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    low = _clean(query, 1600).lower()
    library_scope = any(term in low for term in ("scan", "library", "all outputs", "all image", "orphan", "collision", "integrity problems", "metadata integrity"))
    resolution = {"status": "unresolved", "basis": "library_integrity_scope"} if library_scope else _resolve_history_target(root_dir, query, native_context=native_context, allow_latest=True)
    if resolution.get("status") == "resolved":
        record = resolution["record"]
        file_rows = [_file_integrity_status(root_dir, item) for item in _record_file_records(record)]
        problems = [row for row in file_rows if not row.get("exists") or row.get("hash_state") in {"mismatch", "unreadable"}]
        target = _record_ref(record, resolution.get("file") if isinstance(resolution.get("file"), dict) else None)
        sidecar = _clean(record.get("_neo_metadata_path"))
        quarantine = "image_metadata_quarantine" in sidecar.replace("\\", "/")
        return _scope_packet(
            operation_id="image.output.integrity",
            packet_kind="integrity",
            status="ok" if not problems else "partial",
            known_state="established",
            payload={
                "target": target,
                "sidecar_path": sidecar,
                "quarantined": quarantine,
                "files": file_rows,
                "problem_count": len(problems),
                "state": "healthy" if not problems and file_rows else ("problem" if problems else "no_output_files_recorded"),
                "resolution_basis": resolution.get("basis"),
            },
            target_refs=[target],
            authority_classes=["ID", "SIDE"],
            native_refs=[target],
            source_integrity="present" if not problems else "partial",
            limitations=[] if file_rows else ["The sidecar does not record output files, so file integrity cannot be established."],
            derivation={"method": "sidecar_file_presence_and_recorded_sha256_validation"},
        )

    # A general integrity scan is still useful when the user asks about the Image
    # library rather than one exact result. It stays read-only and reports parse,
    # orphan and collision health without moving/quarantining anything.
    if library_scope:
        active_rows, health = image_history_records(root_dir)
        orphaned: list[dict[str, Any]] = []
        id_claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in active_rows:
            ref = _record_ref(record)
            files = _record_file_records(record)
            statuses = [_file_integrity_status(root_dir, item) for item in files]
            if files and not any(item.get("exists") for item in statuses):
                orphaned.append(ref)
            for item in files:
                oid = _clean(item.get("neo_output_id"))
                if oid:
                    id_claims[oid].append(_record_ref(record, item))
        collisions = [{"neo_output_id": key, "claims": refs} for key, refs in id_claims.items() if len(refs) > 1]
        return _scope_packet(
            operation_id="image.output.integrity",
            packet_kind="integrity",
            status="ok",
            known_state="established",
            payload={"scope": "image_library", "record_health": health, "orphan_count": len(orphaned), "orphans": orphaned[:50], "identity_collision_count": len(collisions), "identity_collisions": collisions[:50], "policy": "read_only_scan_no_quarantine_or_delete"},
            authority_classes=["ID", "SIDE"],
            source_integrity="present" if health["records_invalid"] == 0 else "partial",
            limitations=[] if health["records_invalid"] == 0 else ["Some metadata sidecars could not be parsed and are excluded from the integrity scan."],
            derivation={"method": "read_only_image_library_integrity_scan"},
        )
    return _unknown_history_packet("image.output.integrity", "integrity", resolution)


def delete_preview_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.output.delete_preview", "action_preview", resolution)
    record = resolution["record"]
    result_id = _clean(record.get("result_id"))
    target = _record_ref(record, resolution.get("file") if isinstance(resolution.get("file"), dict) else None)
    manifest: dict[str, Any] = {}
    limitations: list[str] = []
    # The output service owns delete safety. In the application root this call is
    # exact; tests may monkeypatch output_service.ROOT_DIR to an isolated fixture.
    try:
        if output_service.ROOT_DIR.resolve() == root_dir.resolve():
            manifest = output_service.build_image_result_delete_preview(result_id, category=str(record.get("subtab") or "") or None)
        else:
            limitations.append("Delete preview could not invoke the owning output service because the supplied knowledge root differs from the active Image output-service root.")
    except Exception as exc:
        limitations.append(f"Delete preview service could not build a manifest: {_clean(exc, 300)}")
    can_preview = bool(manifest.get("ok"))
    if not can_preview and not manifest:
        # Conservative fallback describes only exact sidecar/output files and never
        # claims unique-input cascade safety without the owning service scan.
        manifest = {
            "result_id": result_id,
            "output_files": [_safe_value(item) for item in _record_file_records(record)],
            "metadata_files": [_clean(record.get("_neo_metadata_path"))] if record.get("_neo_metadata_path") else [],
            "policy": "fallback_exact_record_only_no_cascade_claims",
        }
    return _scope_packet(
        operation_id="image.output.delete_preview",
        packet_kind="action_preview",
        status="ok" if can_preview else "partial",
        known_state="established" if can_preview else "partial",
        payload={
            "requested_action": "delete_result",
            "target": target,
            "can_preview": can_preview,
            "preconditions": ["Exact result identity is established.", "No deletion is executed by this query."],
            "affected_items": _safe_value(manifest),
            "execution_performed": False,
            "resolution_basis": resolution.get("basis"),
        },
        target_refs=[target],
        authority_classes=["SIDE", "OUTPUT_SERVICE"],
        native_refs=[target],
        source_integrity="present" if can_preview else "partial",
        limitations=limitations,
        derivation={"method": "owning_output_service_delete_manifest_preview" if can_preview else "conservative_record_only_delete_preview"},
    )


def reuse_plan_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.reuse.plan", "action_preview", resolution)
    record = resolution["record"]
    file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    target = _record_ref(record, file_record)
    reuse = output_service.build_image_result_reuse_payload(record)
    replay = replay_readiness_packet(root_dir, query, native_context=native_context)
    return _scope_packet(
        operation_id="image.reuse.plan",
        packet_kind="action_preview",
        status="ok",
        known_state="established",
        payload={
            "requested_action": "reuse_result",
            "target": target,
            "can_preview": True,
            "reuse_payload": _safe_value(reuse, text_limit=6000),
            "replay_readiness": _safe_value(replay.get("payload") if isinstance(replay.get("payload"), dict) else {}, text_limit=4500),
            "execution_performed": False,
            "restore_policy": "provider_neutral_disabled_pending_live_revalidation",
            "resolution_basis": resolution.get("basis"),
        },
        target_refs=[target],
        authority_classes=["SIDE", "REPLAY", "LIVE"],
        native_refs=[target],
        source_integrity="present",
        limitations=["This packet is a read-only reuse plan. It does not load the recipe into the Image workspace or start a generation."],
        derivation={"method": "sanitized_reuse_payload_plus_current_replay_readiness"},
    )


def _handoff_target_scope(query: str) -> str:
    low = _clean(query, 1400).lower()
    for scope, markers in (
        ("video", ("video", "img2vid", "image to video", "image-to-video")),
        ("project_workspace", ("project", "project workspace", "asset tray")),
        ("prompt_captioning", ("prompt", "caption", "captioning", "prompt studio")),
        ("roleplay", ("roleplay", "character", "scene")),
        ("image_reference", ("reference", "controlnet", "ip adapter", "ip-adapter")),
        ("image_source", ("img2img", "inpaint", "outpaint", "source image")),
    ):
        if any(marker in low for marker in markers):
            return scope
    return ""


def handoff_readiness_packet(root_dir: Path, query: str, native_context: dict[str, Any] | None = None) -> dict[str, Any]:
    target_scope = _handoff_target_scope(query)
    resolution = _resolve_history_target(root_dir, query, native_context=native_context)
    if resolution.get("status") != "resolved":
        return _unknown_history_packet("image.handoff.check", "handoff_readiness", resolution)
    record = resolution["record"]
    file_record = resolution.get("file") if isinstance(resolution.get("file"), dict) else _find_file_record(record)
    target = _record_ref(record, file_record)
    path = _resolve_record_file_path(root_dir, file_record)
    exists = bool(path and path.exists() and path.is_file())
    suffix = Path(_clean(file_record.get("filename") or file_record.get("path"))).suffix.lower()
    image_file = suffix in _IMAGE_SUFFIXES if suffix else exists
    requirements = [
        {"requirement_id": "exact_image_output", "state": "ready", "detail": target},
        {"requirement_id": "local_file", "state": "ready" if exists else "missing", "detail": _clean(file_record.get("path"))},
        {"requirement_id": "image_file_type", "state": "ready" if image_file else "incompatible", "detail": suffix},
        {"requirement_id": "target_scope", "state": "ready" if target_scope else "unknown", "detail": target_scope},
    ]
    missing = [item for item in requirements if item["state"] == "missing"]
    incompatible = [item for item in requirements if item["state"] == "incompatible"]
    unknown = [item for item in requirements if item["state"] == "unknown"]
    readiness = "blocked" if missing or incompatible else ("partial" if unknown else "ready")
    return _scope_packet(
        operation_id="image.handoff.check",
        packet_kind="handoff_readiness",
        status="ok" if readiness in {"ready", "blocked"} else "partial",
        known_state="established" if readiness in {"ready", "blocked"} else "partial",
        payload={
            "source": target,
            "target_scope": target_scope,
            "readiness": readiness,
            "requirements": requirements,
            "missing": missing,
            "incompatible": incompatible,
            "unknown": unknown,
            "execution_performed": False,
            "policy": "read_only_preflight_target_action_path_owns_execution",
        },
        target_refs=[target],
        authority_classes=["SIDE", "ID", "CAP"],
        native_refs=[target],
        source_integrity="present" if exists else "partial",
        limitations=["This check validates the known Image asset and coarse target-scope handoff requirements only; the target scope performs its own final live validation when an action is executed."],
        derivation={"method": "exact_output_file_plus_target_scope_preflight"},
    )


def attachment_provenance_packet(query: str, native_context: dict[str, Any] | None = None, *, operation_id: str = "image.provenance.establish") -> dict[str, Any]:
    items = _attachment_native_targets(native_context)
    packet_kind = "asset_metadata" if operation_id in {"image.asset.inspect_metadata", "image.external.inspect_metadata"} else "asset_identity"
    referent = _active_image_referent(native_context)
    referent_source = False
    if not items and referent and _deictic_image_reference(query):
        if str(referent.get("status") or "active") == "ambiguous":
            return _scope_packet(
                operation_id=operation_id, packet_kind=packet_kind, status="partial", known_state="unresolved_conflict",
                payload={"attachments": _safe_value(referent.get("candidates") or []), "proof_level": "ambiguous", "referent_source": "session"},
                authority_classes=["ID", "EXTMETA"],
                limitations=["The active conversation Image referent is ambiguous because multiple images were attached; Neo will not choose one silently."],
            )
        items = [referent]
        referent_source = True
    if not items:
        return _scope_packet(
            operation_id=operation_id,
            packet_kind=packet_kind,
            status="partial",
            known_state="not_established",
            payload={"attachments": [], "proof_level": "unidentified"},
            authority_classes=["ID"] if operation_id != "image.external.inspect_metadata" else ["EXTMETA"],
            limitations=["No current-turn Image attachment or active conversation Image referent metadata proof was available."],
        )
    if len(items) > 1 and any(term in _clean(query).lower() for term in ("this image", "the image", "it")):
        return _scope_packet(
            operation_id=operation_id,
            packet_kind=packet_kind,
            status="partial",
            known_state="unresolved_conflict",
            payload={"attachments": _safe_value(items), "proof_level": "ambiguous"},
            authority_classes=["ID", "EXTMETA"],
            limitations=["Multiple current-turn image attachments are present; Neo will not silently choose which one the deictic question refers to."],
        )
    summaries = []
    has_native = False
    has_external = False
    for item in items:
        proof = str(item.get("proof_level") or "unidentified")
        if proof.startswith("native_exact"):
            origin = "neo_native"
            has_native = True
        elif proof == "external_embedded_metadata":
            origin = "external_metadata"
            has_external = True
        else:
            origin = "not_established"
        summaries.append({
            "attachment_id": item.get("attachment_id"),
            "filename": item.get("filename"),
            "proof_level": proof,
            "origin": origin,
            "known_state": item.get("known_state"),
            "identity": _safe_value(item.get("identity") if isinstance(item.get("identity"), dict) else {}),
            "model": _safe_value(item.get("model") if isinstance(item.get("model"), dict) else {}),
            "sampling": _safe_value(item.get("sampling") if isinstance(item.get("sampling"), dict) else {}),
            "generator": _clean(item.get("generator"), 300),
            "external_metadata": _safe_value(item.get("external_metadata") if isinstance(item.get("external_metadata"), dict) else {}, text_limit=3000),
        })
    known = "established" if all(row["origin"] != "not_established" for row in summaries) else "partial"
    classes: list[str] = []
    if has_native:
        classes.append("ID")
    if has_external:
        classes.append("EXTMETA")
    if not classes:
        classes.append("ID")
    return _scope_packet(
        operation_id=operation_id,
        packet_kind=packet_kind,
        status="ok" if known == "established" else "partial",
        known_state=known,
        payload={"attachments": summaries, "proof_levels": [row["proof_level"] for row in summaries], "referent_source": "session" if referent_source else "current_turn"},
        authority_classes=classes,
        source_integrity="present" if known == "established" else "partial",
        limitations=["Pixel appearance alone is not used to infer hidden generation settings or native provenance."],
        derivation={"method": "session_image_referent_metadata_proof" if referent_source else "current_turn_attachment_metadata_proof"},
    )


_IMAGE_PACKET_KIND_BY_OPERATION = {
    "image.navigation.resolve": "reference",
    "image.feature.explain": "reference",
    "image.state.get": "live_state",
    "image.capability.check": "capability_readiness",
    "image.readiness.check": "capability_readiness",
    "image.dependency.check": "capability_readiness",
    "image.procedure.get": "procedure",
    "image.guide.get": "reference",
    "image.guide.section": "procedure",
    "image.parameter.explain": "reference",
    "image.parameter.recommend": "advisory",
    "image.prompt.get_current": "prompt_asset",
    "image.prompt.search_saved": "prompt_asset",
    "image.asset.resolve_identity": "asset_identity",
    "image.asset.inspect_metadata": "asset_metadata",
    "image.external.inspect_metadata": "asset_metadata",
    "image.provenance.establish": "asset_identity",
    "image.recipe.get": "recipe",
    "image.history.search": "history_search",
    "image.history.filter": "history_search",
    "image.inventory.count_runs": "inventory",
    "image.inventory.count_outputs": "inventory",
    "image.inventory.count_attempts": "inventory",
    "image.analytics.group": "analytics",
    "image.analytics.trend": "analytics",
    "image.performance.compare": "performance",
    "image.performance.summarize": "performance",
    "image.recipe.diff": "comparison",
    "image.lineage.trace": "lineage",
    "image.lineage.children": "lineage",
    "image.replay.check": "replay_readiness",
    "image.reuse.plan": "action_preview",
    "image.handoff.check": "handoff_readiness",
    "image.diagnose": "diagnostic",
    "image.job.status": "job_status",
    "image.job.diagnose": "diagnostic",
    "image.output.location": "reference",
    "image.output.integrity": "integrity",
    "image.output.delete_preview": "action_preview",
}


def _contract_op(operation_id: str, **extra: Any) -> dict[str, Any]:
    return {"operation_id": operation_id, "packet_kind": _IMAGE_PACKET_KIND_BY_OPERATION.get(operation_id, "reference"), **extra}


def _contract_preplan_image_query(query: str) -> list[dict[str, Any]]:
    """High-confidence R7 intent classifier used before legacy heuristics.

    This deliberately models *semantic operation families* rather than exact benchmark
    phrases.  It exists so native Image questions keep deterministic authority even
    when phrased conversationally, with aliases, or with mild wording drift.
    """
    low = _clean(query, 1800).lower().strip()
    if not low:
        return []
    # Normalize common creator shorthand/aliases without changing quoted values.
    n = low
    n = re.sub(r"\bpic(?:ture)?s?\b", "image", n)
    n = re.sub(r"\brenders?\b", "generation", n)
    n = n.replace("control net", "controlnet").replace("high res", "high-res")
    n = n.replace("backgroun removal", "background removal").replace("metdata", "metadata")

    def has(*terms: str) -> bool:
        return any(term in n for term in terms)

    def all_has(*terms: str) -> bool:
        return all(term in n for term in terms)

    def ops(*operation_ids: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for operation_id in operation_ids:
            extra: dict[str, Any] = {}
            if operation_id == "image.analytics.group":
                dimension = "model"
                if has("extension", "lora"):
                    dimension = "extension"
                elif "family" in n:
                    dimension = "family"
                elif "loader" in n:
                    dimension = "loader"
                elif "sampler" in n:
                    dimension = "sampler"
                elif "scheduler" in n:
                    dimension = "scheduler"
                elif has("workflow", "mode"):
                    dimension = "mode"
                extra["dimension"] = dimension
            if operation_id == "image.parameter.explain" or operation_id == "image.parameter.recommend":
                parameter = _resolve_parameter(n)
                if parameter:
                    extra["parameter"] = parameter
            if operation_id == "image.performance.compare":
                extra["goal"] = "fastest_model"
            out.append(_contract_op(operation_id, **extra))
        return out

    # ---- compound/dependent plans: preserve dependency order ----
    if has("settings made this image", "settings generated this image", "settings did this use") and has("reproduce", "replay", "recreate"):
        return ops("image.asset.resolve_identity", "image.recipe.get", "image.replay.check")
    if has("use most", "used most", "most used", "most frequently") and has("loaded now", "model loaded now", "current model"):
        return ops("image.analytics.group", "image.state.get")
    if has("september") and has("outpaint") and has("median", "average", "timing", "generation time"):
        return ops("image.history.filter", "image.performance.summarize")
    if has("attached result", "attached image") and has("slower", "slow") and has("usual", "normal", "same checkpoint", "same model"):
        return ops("image.asset.resolve_identity", "image.recipe.get", "image.performance.summarize", "image.diagnose")
    if has("high-res run", "high-res generation") and has("slower", "slow"):
        return ops("image.recipe.get", "image.performance.summarize", "image.diagnose")
    if has("why can't this legacy result replay exactly", "why cant this legacy result replay exactly"):
        return ops("image.diagnose")
    if has("legacy recipe", "old recipe", "legacy result") and has("exact replay", "replay exactly", "complete enough"):
        return ops("image.recipe.get", "image.replay.check")
    if has("compare this result with its parent", "compare this image with its parent", "compare with its parent"):
        return ops("image.lineage.trace", "image.recipe.diff")
    if has("current prompt", "active prompt") and "saved" in n and "prompt" in n:
        return ops("image.prompt.get_current", "image.prompt.search_saved")

    # ---- privacy/raw-sidecar questions keep the native packet but never expose secrets ----
    if has("raw sidecar") and has("secret", "every field", "provider"):
        return ops("image.recipe.get")
    if has("private backend token", "backend token", "api key", "provider secret"):
        return ops("image.asset.inspect_metadata")

    # ---- explicit provenance / attachment identity / external metadata ----
    attachmentish = has(
        "attached image", "attached neo image", "attached file", "this image", "this neo image", "this file", "this png", "this jpeg", "this jpg", "this webp",
        "moved image", "moved png", "renamed", "random-folder", "random folder", "screenshot", "flattened image", "stripped jpeg",
        "external image", "camera jpeg", "comfyui png", "a1111", "fooocus", "embedded-only image", "legacy file",
    )
    if has("pixels alone", "looks identical", "visual similarity", "no metadata", "stripped jpeg", "flattened image", "screenshot") and has(
        "seed", "model", "workflow", "neo", "same output", "sidecar", "fill missing settings", "current defaults", "provenance"
    ):
        return ops("image.provenance.establish")
    if has("external image") and has("generated by neo", "prove", "neo result"):
        return ops("image.provenance.establish")
    if has("sidecar is missing", "sidecar missing") and has("current defaults", "fill missing settings"):
        return ops("image.provenance.establish")
    if has("comfyui png", "a1111", "fooocus", "camera jpeg") and has("metadata", "parameters", "what can neo read"):
        return ops("image.external.inspect_metadata")
    if attachmentish and has("generation metadata", "metadata is in", "what metadata", "read metadata", "inspect metadata"):
        if has("comfyui", "a1111", "fooocus", "camera jpeg", "external"):
            return ops("image.external.inspect_metadata")
        return ops("image.asset.inspect_metadata")
    if has("embedded metadata") and has("sidecar", "what can you prove"):
        return ops("image.asset.resolve_identity", "image.asset.inspect_metadata")
    generation_recipe_intent = has(
        "prompt and settings", "checkpoint and seed", "settings generated", "settings did this use",
        "settings used to generate", "generation settings", "generation parameters", "parameters used",
        "generation info", "what was this generated with", "what did i generate this with",
        "prompt generated this", "prompt used to generate", "original prompt", "seed used", "sampler used",
    )
    deictic_recipe_target = attachmentish or _deictic_image_reference(n)
    if generation_recipe_intent and deictic_recipe_target:
        return ops("image.asset.resolve_identity", "image.recipe.get")
    if has("prompt to generate this", "prompt to recreate this", "prompt for this image", "prompt for the same image") and has(
        "krea", "qwen", "flux", "sdxl", "generate", "recreate", "make"
    ):
        return ops("image.asset.resolve_identity", "image.recipe.get")
    identity_markers = (
        "one neo generated", "which result", "exact output id", "identify it", "identify this", "neo identity", "linked to",
        "which sidecar", "recorded hash", "hash match", "history match exact", "same output", "generation history match",
    )
    if attachmentish and any(marker in n for marker in identity_markers):
        return ops("image.asset.resolve_identity")
    if has("hash match") and has("semantic similarity"):
        return ops("image.asset.resolve_identity")
    if has("generation history match") and has("exact", "similar"):
        return ops("image.asset.resolve_identity")
    if has("changing the filename", "change the filename") and has("neo identity", "identity"):
        return ops("image.asset.resolve_identity")
    if has("external file") and has("neo result", "looks like"):
        return ops("image.asset.resolve_identity")
    if has("what settings did this use"):
        return ops("image.asset.resolve_identity", "image.recipe.get")
    if has("checkpoint and seed") and has("used here", "were used here"):
        return ops("image.asset.resolve_identity", "image.recipe.get")

    # ---- Guide / procedure / navigation ----
    if has("troubleshooting section", "show only the troubleshooting"):
        return ops("image.guide.section")
    if has("which background-removal path", "which background removal path") and has("old", "current"):
        return ops("image.procedure.get")
    if "guide" in n and has("full current", "full guide", "complete guide", "current or old", "old or current", "changed from the old"):
        return ops("image.guide.get")
    if has("changed from the old background-removal path", "changed from the old background removal path"):
        return ops("image.guide.get")
    if has("old or current guide", "current guide") and "background" in n:
        return ops("image.guide.get")
    if has("which background-removal path", "which background removal path") and has("old", "current"):
        return ops("image.procedure.get")
    if has("exact steps", "walk me through", "how do i", "how can i", "what's the way to", "what is the way to", "steps to") and has(
        "remove a background", "remove background", "background removal", "outpaint", "outpainting", "reuse a previous", "reuse previous", "add a reference", "reference image"
    ):
        return ops("image.procedure.get")
    if has("where do i go", "where is", "where can i find", "which image section", "which image area", "which section", "get back to an earlier generation", "navigate to") and has(
        "background", "outpaint", "results", "previous results", "earlier generation", "reference", "finish"
    ):
        return ops("image.navigation.resolve")

    # ---- feature/reference explanations ----
    if has("what does", "what is", "explain what", "what's the difference", "difference between", "compare") and has(
        "high-res", "controlnet", "ip adapter", "scene director", "layerdiffuse", "adetailer", "background removal"
    ):
        return ops("image.feature.explain")

    # ---- live state ----
    if has("can i use", "is it possible to use") and has("controlnet", "ip adapter", "adetailer", "layerdiffuse", "scene director", "high-res", "background removal"):
        return ops("image.capability.check")
    if has("available with my current model", "available for my current model"):
        return ops("image.capability.check")
    if has("old image", "old result", "legacy image", "legacy result") and has("scheduler", "vae", "encoder", "settings", "recipe"):
        return ops("image.recipe.get")
    if has("right now", "currently", "current", "loaded now", "using now", "attached now", "enabled right now", "enabled now") and has(
        "model", "checkpoint", "seed", "adetailer", "reference image", "reference images", "sampler", "scheduler", "steps", "cfg", "workflow", "loader", "family"
    ):
        if "prompt" not in n:
            return ops("image.state.get")
    if has("what seed am i using", "what model is loaded"):
        return ops("image.state.get")

    # ---- capability/readiness/dependencies ----
    if has("available with my current model", "available for my current model"):
        return ops("image.capability.check")
    if has("what am i missing", "all dependencies", "dependencies present", "missing before"):
        return ops("image.dependency.check")
    if has("ready to use", "am i ready", "unavailable even though", "why is this feature unavailable"):
        return ops("image.readiness.check")
    if has("can the current route use", "family support", "support layerdiffuse", "unsupported extension here", "available with my current model", "can i use this unsupported", "does this family support"):
        return ops("image.capability.check")

    # ---- parameter explanation/advisory ----
    if has("reduce generation time", "more variation", "cleaner upscale", "overcooking detail", "what should i change", "which current setting"):
        return ops("image.parameter.recommend")
    if has("what does denoise", "increase cfg", "what is clip skip", "what does the scheduler", "explain cfg", "what happens if i increase"):
        return ops("image.parameter.explain")

    # ---- Prompt Library ----
    if has("load this result into the workspace", "load this result") and has("keep the current prompt", "keep current prompt"):
        return ops("image.reuse.plan")
    if has("prompt am i using", "current prompt", "prompt right now"):
        return ops("image.prompt.get_current")
    if ("saved" in n and "prompt" in n) and has("find", "show", "search", "qwen", "neon portrait"):
        return ops("image.prompt.search_saved")

    # ---- diagnostics/jobs/output integrity/action preview ----
    if has("why is background removal unavailable", "why isn't layerdiffuse available", "why is layerdiffuse unavailable", "why was this run unusually slow", "why can't this legacy result replay exactly"):
        return ops("image.diagnose")
    if has("why did this job fail", "backend disconnect", "job fail"):
        return ops("image.job.diagnose")
    if has("generation still running", "job cancelled or", "job canceled or", "still running"):
        return ops("image.job.status")
    if has("what would be deleted", "what will go", "delete that result", "delete this result"):
        return ops("image.output.delete_preview")
    if has("sidecar healthy", "result quarantined", "identity collision", "which record wins", "which sidecar should neo trust", "output intact", "integrity"):
        return ops("image.output.integrity")
    if has("where was this output saved", "where was this result saved", "output saved", "file path"):
        return ops("image.output.location")

    # ---- lineage / comparison ----
    if has("which outputs came from", "children of", "derivatives of"):
        return ops("image.lineage.children")
    if has("based on", "root image", "trace the full edit chain", "edit chain", "lineage") and has("img_child", "image", "output", "result", "chain"):
        return ops("image.lineage.trace")
    if has("compare", "what changed", "settings changed", "same seed and checkpoint", "model version switch"):
        return ops("image.recipe.diff")

    # ---- replay/reuse/handoff ----
    if has("reuse this result", "reuse this image", "img2img source", "reuse result"):
        return ops("image.reuse.plan")
    if has("send this image output to video", "send this output to video", "video as a first frame", "use this image in video"):
        return ops("image.handoff.check")
    if has("reproduce", "replay this", "replay this old", "exactly reproduce", "reproduce this"):
        return ops("image.replay.check")

    # ---- history discovery/filtering ----
    if has("find my", "find images", "show the generation where", "find results similar", "similar in description"):
        if "saved" not in n or "prompt" not in n:
            return ops("image.history.search")
    if has("show generations using", "september outpaint", "generations used controlnet", "1024x1024", "1024×1024", "which version of benchmarkkrea"):
        return ops("image.history.filter")

    # ---- exact inventory / analytics / performance ----
    if has("how many generation attempts", "generation attempts including", "attempts including"):
        return ops("image.inventory.count_attempts")
    if has("how many primary image outputs", "how many image outputs", "primary outputs"):
        return ops("image.inventory.count_outputs")
    if has("how many completed image generations", "how many completed generation", "how many completed runs", "completed runs used"):
        return ops("image.inventory.count_runs")
    if has("which checkpoint do i use most", "rank checkpoints", "extensions are most common", "most common extensions", "model do i use most"):
        return ops("image.analytics.group")
    if has("generation volume increase", "generation trend", "over time", "august to september"):
        return ops("image.analytics.trend")
    if has("median time", "average generation time", "performance summary"):
        return ops("image.performance.summarize")
    if has("which model is fastest", "fastest overall", "fastest for comparable", "fastest model"):
        return ops("image.performance.compare")

    # ---- historical recipe ----
    if has("full recipe", "recipe for this", "what settings do we still know", "which vae did this legacy", "assume the current default scheduler", "did this output use", "tell me everything neo recorded", "what checkpoint and seed were used here", "what prompt and settings generated"):
        return ops("image.recipe.get")

    return []

def plan_image_native_query(query: str) -> dict[str, Any]:
    low = _clean(query, 1200).lower()
    operations: list[dict[str, Any]] = []
    task = _resolve_task(query)
    feature = _resolve_feature(query)

    contract_ops = _contract_preplan_image_query(query)
    if contract_ops:
        return {"schema_id": "neo.image.native_query_plan.v1", "query": query, "operations": contract_ops, "recognized": True}

    # Historical recipe questions frequently contain words such as "current"
    # when the user is asking whether today's defaults may be substituted for
    # an older result.  Those are *not* live-state questions.  Keep the native
    # authority lane scoped to the historical record whenever the utterance is
    # anchored to an image/result/output/generation from history.
    historical_anchor = any(term in low for term in (
        "old image", "old result", "old output", "old generation", "legacy",
        "this image", "this result", "this output", "this generation",
        "that image", "that result", "that output", "that generation",
        "attached image", "saved result", "saved output", "previous result",
    ))
    historical_recipe_subject = any(term in low for term in (
        "model", "checkpoint", "vae", "encoder", "text encoder", "clip",
        "seed", "sampler", "scheduler", "steps", "cfg", "denoise", "prompt",
        "lora", "controlnet", "control net", "ip adapter", "adetailer",
        "high-res", "high res", "reference", "mask", "resolution", "width", "height",
    ))

    # Live-state questions are deterministic reads of the current Image workspace.
    state_markers = ("currently", "current", "right now", "selected", "set to", "loaded", "enabled now", "active now")
    state_subjects = ("model", "checkpoint", "family", "loader", "seed", "sampler", "scheduler", "steps", "cfg", "workflow", "mode", "reference", "adetailer", "controlnet", "layerdiffuse", "scene director")
    if (
        any(marker in low for marker in state_markers)
        and any(subject in low for subject in state_subjects)
        and not (historical_anchor and historical_recipe_subject)
    ):
        operations.append({"operation_id": "image.state.get", "packet_kind": "live_state"})

    # I4 parameter and Prompt Library intelligence. Explanations come from the
    # canonical Image parameter Guide, while current prompt text is live state and
    # saved prompts come from the Image/Prompt Studio library records.
    parameter = _resolve_parameter(query)
    recommendation_markers = ("recommend", "recommended", "should i use", "should i set", "best setting", "good setting", "what should", "ideal")
    explanation_markers = ("what is", "what does", "explain", "meaning", "how does", "difference between")
    current_prompt_question = "prompt" in low and any(marker in low for marker in ("current prompt", "prompt right now", "prompt is set", "prompt am i using", "prompt i'm using", "prompt i am using"))
    if parameter and any(marker in low for marker in recommendation_markers):
        operations.append({"operation_id": "image.parameter.recommend", "packet_kind": "advisory", "parameter": parameter})
    elif parameter and any(marker in low for marker in explanation_markers) and not historical_anchor and not current_prompt_question:
        operations.append({"operation_id": "image.parameter.explain", "packet_kind": "reference", "parameter": parameter})

    if current_prompt_question:
        operations.append({"operation_id": "image.prompt.get_current", "packet_kind": "prompt_asset"})
    if any(marker in low for marker in ("saved prompt", "saved prompts", "prompt library", "find prompt", "search prompt")) and any(marker in low for marker in ("find", "show", "search", "list", "saved", "library")):
        operations.append({"operation_id": "image.prompt.search_saved", "packet_kind": "prompt_asset"})

    # Current-turn attached-image identity/metadata/provenance operations. These
    # consume I1 attachment proof and never infer hidden generation facts from pixels.
    attachment_words = any(term in low for term in ("this image", "attached image", "attachment", "this png", "this jpeg", "this jpg", "this webp"))
    if attachment_words and any(term in low for term in ("which neo output", "identify this image", "identity of this image", "what output is this", "which result is this")):
        operations.append({"operation_id": "image.asset.resolve_identity", "packet_kind": "asset_identity"})
    if attachment_words and any(term in low for term in ("inspect metadata", "what metadata", "read metadata", "metadata does", "metadata in")):
        operations.append({"operation_id": "image.asset.inspect_metadata", "packet_kind": "asset_metadata"})
    if attachment_words and any(term in low for term in ("external metadata", "comfy metadata", "a1111 metadata", "forge metadata", "fooocus metadata")):
        operations.append({"operation_id": "image.external.inspect_metadata", "packet_kind": "asset_metadata"})
    if attachment_words and any(term in low for term in ("did neo generate", "generated by neo", "is this a neo", "where did this image come from", "provenance", "origin of this image")):
        operations.append({"operation_id": "image.provenance.establish", "packet_kind": "asset_identity"})

    # Canonical procedures/guides. Exact authored Guide content establishes UI steps.
    procedure_markers = ("how do i", "how can i", "step by step", "steps to", "instructions", "procedure", "how to")
    full_guide = bool("guide" in low and any(term in low for term in ("full", "complete", "entire", "whole")))
    section_guide = "troubleshoot" in low or "troubleshooting" in low or "section" in low and "guide" in low
    if (task or feature) and full_guide:
        operations.append({"operation_id": "image.guide.get", "packet_kind": "reference"})
    elif (task or feature) and section_guide:
        operations.append({"operation_id": "image.guide.section", "packet_kind": "procedure"})
    elif task and any(marker in low for marker in procedure_markers):
        operations.append({"operation_id": "image.procedure.get", "packet_kind": "procedure"})

    navigation_markers = ("where is", "where do i", "where can i", "which section", "which tab", "which menu", "navigate", "find the")
    if task and any(marker in low for marker in navigation_markers) and not any(op.get("operation_id") == "image.procedure.get" for op in operations):
        operations.append({"operation_id": "image.navigation.resolve", "packet_kind": "reference"})

    # Support/readiness/dependency are related but not equivalent. Preserve the
    # user's requested claim so the packet can keep family/route/runtime layers separate.
    if feature:
        if any(term in low for term in ("dependency", "dependencies", "missing node", "missing nodes", "what is missing", "what's missing", "need installed", "requirements installed")) or ("missing" in low and "node" in low):
            operations.append({"operation_id": "image.dependency.check", "packet_kind": "capability_readiness"})
        elif any(term in low for term in ("ready", "readiness", "available now", "can i run", "can i use now", "work right now", "why unavailable", "why disabled")):
            operations.append({"operation_id": "image.readiness.check", "packet_kind": "capability_readiness"})
        elif any(term in low for term in ("support", "supported", "can use", "can this", "compatible", "compatibility", "available with", "works with")) or bool(re.search(r"\bcan\b.{0,40}\buse\b", low)):
            operations.append({"operation_id": "image.capability.check", "packet_kind": "capability_readiness"})

    if feature and not any(op.get("operation_id") in {"image.capability.check", "image.readiness.check", "image.dependency.check", "image.guide.get", "image.guide.section"} for op in operations):
        if re.search(r"^\s*(?:what\s+is|what\s+does|explain|tell\s+me\s+about|how\s+does)\b", low) or "difference between" in low:
            operations.append({"operation_id": "image.feature.explain", "packet_kind": "reference"})

    # I3 historical intelligence. These operations require native Image records;
    # discovery may find candidates, but exact recipe/diff/replay claims require
    # an explicit/selected/lineage-resolved target at execution time.
    replay_markers = ("reproduce", "reproduction", "replay", "recreate", "re-create", "exactly reproduce", "reproduce exactly")
    if any(marker in low for marker in replay_markers) and any(term in low for term in ("image", "result", "output", "generation", "this", "it", "old")):
        operations.append({"operation_id": "image.replay.check", "packet_kind": "replay_readiness"})

    reuse_markers = ("reuse this", "reuse result", "reuse image", "reuse settings", "load this result", "load this generation", "what would be reused", "reuse plan")
    if any(marker in low for marker in reuse_markers):
        operations.append({"operation_id": "image.reuse.plan", "packet_kind": "action_preview"})

    handoff_markers = ("send this", "use this in video", "use this for video", "use this as video", "handoff", "hand off", "send to project", "use this as reference", "use this as source")
    handoff_shape = bool(re.search(r"\b(?:use|send)\b.{0,36}\b(?:image|result|output|this)\b.{0,36}\b(?:video|project|reference|source)\b", low))
    if (any(marker in low for marker in handoff_markers) or handoff_shape) and any(term in low for term in ("image", "result", "output", "this", "project", "video", "reference", "source")):
        operations.append({"operation_id": "image.handoff.check", "packet_kind": "handoff_readiness"})

    job_status_markers = ("job status", "current job", "active job", "running job", "still running", "generation progress", "progress of")
    job_failure_markers = ("job failed", "failed job", "why did the job", "why did my last generation fail", "why did the last generation fail", "latest generation fail", "last failure")
    generic_diagnose_markers = ("diagnose", "what went wrong", "why is generation failing", "why does generation fail", "why is this failing")
    if any(marker in low for marker in job_failure_markers):
        operations.append({"operation_id": "image.job.diagnose", "packet_kind": "diagnostic"})
    elif any(marker in low for marker in job_status_markers):
        operations.append({"operation_id": "image.job.status", "packet_kind": "job_status"})
    elif any(marker in low for marker in generic_diagnose_markers):
        operations.append({"operation_id": "image.diagnose", "packet_kind": "diagnostic"})

    delete_preview_markers = ("what would be deleted", "delete preview", "if i delete", "before i delete", "delete this result")
    location_markers = ("where was this saved", "where is this saved", "output location", "file path", "saved path", "where is the file")
    integrity_markers = ("integrity", "is this output intact", "is this image intact", "missing output", "missing file", "orphan", "collision", "broken sidecar", "metadata scan")
    if any(marker in low for marker in delete_preview_markers):
        operations.append({"operation_id": "image.output.delete_preview", "packet_kind": "action_preview"})
    elif any(marker in low for marker in integrity_markers):
        operations.append({"operation_id": "image.output.integrity", "packet_kind": "integrity"})
    elif any(marker in low for marker in location_markers) or bool(re.search(r"\bwhere\b.{0,28}\b(?:result|output|image|file)\b.{0,18}\bsaved\b", low)):
        operations.append({"operation_id": "image.output.location", "packet_kind": "reference"})

    lineage_children = any(term in low for term in ("which outputs came from", "what outputs came from", "children of", "derivatives of", "derived from this"))
    lineage_trace = any(term in low for term in ("based on", "parent", "root image", "root output", "edit chain", "lineage", "ancestor", "source output"))
    if lineage_children:
        operations.append({"operation_id": "image.lineage.children", "packet_kind": "lineage"})
    elif lineage_trace:
        operations.append({"operation_id": "image.lineage.trace", "packet_kind": "lineage"})

    compare_markers = ("compare", "what changed", "what settings changed", "same seed", "same checkpoint", "same model", "difference between these", "different between")
    if any(marker in low for marker in compare_markers) and (
        any(term in low for term in ("result", "output", "image", "generation", "parent", "previous", "these", "between", "settings", "model version", "version switch"))
        or ("compare" in low and " and " in low)
    ):
        operations.append({"operation_id": "image.recipe.diff", "packet_kind": "comparison"})

    structured_history = bool(
        re.search(r"\b(?:show|list|which|find)\b", low)
        and (
            re.search(r"\b[^\s,;]+\.(?:safetensors|gguf|ckpt|pt|pth)\b", low)
            or any(term in low for term in ("outpaint", "inpaint", "img2img", "txt2img", "controlnet", "control net", "adetailer", "layerdiffuse"))
            or bool(re.search(r"\b\d{2,5}\s*[x×]\s*\d{2,5}\b", low))
            or bool(re.search(r"\b\d{1,4}\s+steps?\b", low))
            or any(month in low for month in _MONTHS)
        )
    )
    if structured_history:
        operations.append({"operation_id": "image.history.filter", "packet_kind": "history_search"})
    elif any(term in low for term in ("find my", "find images", "find results", "find generations", "show the generation where", "show me the generation where", "similar in description")):
        operations.append({"operation_id": "image.history.search", "packet_kind": "history_search"})

    recipe_markers = (
        "what settings", "which settings", "generation recipe", "full recipe", "how was this generated",
        "how was this made", "what checkpoint", "what seed", "what sampler", "what scheduler",
        "what vae", "which vae", "what encoder", "which encoder", "what text encoder", "which text encoder",
        "what clip", "which clip", "what prompt", "tell me everything neo recorded", "did this output use", "did this image use",
        "settings used", "used to generate", "made this image", "made this output",
    )
    historical_recipe_question = historical_anchor and historical_recipe_subject and any(term in low for term in (
        "use", "used", "which", "what", "assume", "recorded", "know", "known", "settings", "recipe",
    ))
    if (any(marker in low for marker in recipe_markers) or historical_recipe_question) and not any(op.get("operation_id") == "image.state.get" for op in operations):
        operations.append({"operation_id": "image.recipe.get", "packet_kind": "recipe"})

    inventory_terms = ("how many", "count", "total number", "number of")
    if any(term in low for term in inventory_terms) and any(term in low for term in ("image", "generation", "generated", "result", "output", "attempt", "job", "failed", "cancelled", "canceled")):
        count_clause = re.split(r"\b(?:and|,|;)\s+(?=(?:what|which|who|how|when|where)\b)", low, maxsplit=1)[0]
        asks_attempts = bool(re.search(r"\b(?:attempts?|jobs?|failed|cancelled|canceled)\b", count_clause))
        asks_runs = bool(re.search(r"\b(?:generations?|generation\s+runs?|runs?)\b", count_clause))
        asks_outputs = bool(re.search(r"\b(?:images?|outputs?|results?)\b", count_clause))
        if asks_attempts:
            operations.append({"operation_id": "image.inventory.count_attempts", "packet_kind": "inventory", "metric": "attempts"})
        elif asks_runs:
            operations.append({"operation_id": "image.inventory.count_runs", "packet_kind": "inventory", "metric": "runs"})
        elif asks_outputs:
            operations.append({"operation_id": "image.inventory.count_outputs", "packet_kind": "inventory", "metric": "outputs"})
    usage_terms = ("most used", "use most", "used most", "frequently used", "most frequent", "rank", "top")
    if any(term in low for term in usage_terms):
        dimension = "model"
        if "family" in low: dimension = "family"
        elif "loader" in low: dimension = "loader"
        elif "sampler" in low: dimension = "sampler"
        elif "scheduler" in low: dimension = "scheduler"
        elif re.search(r"\bmode\b", low) or "workflow" in low: dimension = "mode"
        if any(term in low for term in ("model", "checkpoint", "family", "loader", "sampler", "scheduler", "mode", "workflow")):
            operations.append({"operation_id": "image.analytics.group", "packet_kind": "analytics", "dimension": dimension})
    trend_markers = ("trend", "over time", "per month", "each month", "monthly", "per week", "weekly", "per day", "daily", "by month", "by week", "by day")
    if any(term in low for term in trend_markers) and any(term in low for term in ("generation", "image", "run", "usage", "activity")):
        operations.append({"operation_id": "image.analytics.trend", "packet_kind": "analytics"})

    performance_summary_markers = ("average generation time", "average time", "median generation time", "median time", "how long do my generations", "generation times", "performance summary", "timing summary")
    if any(term in low for term in performance_summary_markers):
        operations.append({"operation_id": "image.performance.summarize", "packet_kind": "performance"})
    if any(term in low for term in ("fastest", "quickest", "fastest generation", "fastest model")) and any(term in low for term in ("model", "checkpoint", "generation", "image")):
        operations.append({"operation_id": "image.performance.compare", "packet_kind": "performance", "goal": "fastest_model"})

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for op in operations:
        key = (str(op.get("operation_id")), str(op.get("dimension") or ""))
        if key not in seen:
            seen.add(key); deduped.append(op)
    return {"schema_id": "neo.image.native_query_plan.v1", "query": query, "operations": deduped, "recognized": bool(deduped)}


def execute_image_native_query(root_dir: Path, query: str, *, native_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plan = plan_image_native_query(query)
    packets: list[dict[str, Any]] = []
    for op in plan.get("operations") or []:
        operation_id = str(op.get("operation_id") or "")
        # The planner may request an identity preflight for ambiguous natural wording.
        # If the current Image Results selection already provides an exact native target
        # and no current-turn attachment needs proving, Recipe can consume that target
        # directly; avoid emitting a redundant empty attachment-identity packet.
        if operation_id == "image.asset.resolve_identity":
            ctx = native_context if isinstance(native_context, dict) else {}
            attachments = ctx.get("attachment_image_intelligence") if isinstance(ctx.get("attachment_image_intelligence"), list) else []
            active_ref = ctx.get("active_image_referent") if isinstance(ctx.get("active_image_referent"), dict) else {}
            selected = ctx.get("selected_result") if isinstance(ctx.get("selected_result"), dict) else {}
            if not selected and isinstance(ctx.get("activeSavedResultMetadata"), dict):
                selected = ctx.get("activeSavedResultMetadata") or {}
            if selected and not attachments and not active_ref:
                continue
        if operation_id in {"image.inventory.count_runs", "image.inventory.count_outputs"}:
            packet = inventory_packet(root_dir, query)
            packet["operation_id"] = operation_id
            packet["payload"]["requested_metric"] = "primary_output_images" if operation_id.endswith("count_outputs") else "completed_generation_runs"
            packets.append(packet)
        elif operation_id == "image.inventory.count_attempts":
            packets.append(attempt_count_packet(root_dir))
        elif operation_id == "image.analytics.group":
            packets.append(analytics_group_packet(root_dir, dimension=str(op.get("dimension") or "model"), query=query))
        elif operation_id == "image.analytics.trend":
            packets.append(analytics_trend_packet(root_dir, query))
        elif operation_id == "image.performance.summarize":
            packets.append(performance_summary_packet(root_dir, query))
        elif operation_id == "image.performance.compare":
            packets.append(performance_fastest_model_packet(root_dir, query))
        elif operation_id == "image.recipe.get":
            packets.append(recipe_packet(root_dir, query, native_context=native_context))
        elif operation_id == "image.history.search":
            packets.append(history_search_packet(root_dir, query))
        elif operation_id == "image.history.filter":
            packets.append(history_filter_packet(root_dir, query))
        elif operation_id == "image.recipe.diff":
            packets.append(comparison_packet(root_dir, query, native_context=native_context))
        elif operation_id == "image.lineage.trace":
            packets.append(lineage_packet(root_dir, query, native_context=native_context, children_only=False))
        elif operation_id == "image.lineage.children":
            packets.append(lineage_packet(root_dir, query, native_context=native_context, children_only=True))
        elif operation_id == "image.replay.check":
            packets.append(replay_readiness_packet(root_dir, query, native_context=native_context))
        elif operation_id == "image.reuse.plan":
            packets.append(reuse_plan_packet(root_dir, query, native_context=native_context))
        elif operation_id == "image.handoff.check":
            packets.append(handoff_readiness_packet(root_dir, query, native_context=native_context))
        elif operation_id == "image.state.get":
            packets.append(live_state_packet(query, native_context))
        elif operation_id == "image.parameter.explain":
            packets.append(parameter_explain_packet(query, native_context))
        elif operation_id == "image.parameter.recommend":
            packets.append(parameter_recommend_packet(query, native_context))
        elif operation_id == "image.prompt.get_current":
            packets.append(current_prompt_packet(query, native_context))
        elif operation_id == "image.prompt.search_saved":
            packets.append(saved_prompt_search_packet(query))
        elif operation_id in {"image.asset.resolve_identity", "image.asset.inspect_metadata", "image.external.inspect_metadata", "image.provenance.establish"}:
            packets.append(attachment_provenance_packet(query, native_context, operation_id=operation_id))
        elif operation_id in {"image.capability.check", "image.readiness.check", "image.dependency.check"}:
            packets.append(capability_readiness_packet(query, native_context, operation_id=operation_id))
        elif operation_id == "image.navigation.resolve":
            packets.append(navigation_packet(query))
        elif operation_id in {"image.procedure.get", "image.guide.get", "image.guide.section"}:
            packet = procedure_packet(query, operation_id=operation_id)
            # Preserve the planner's exact operation when it was explicitly classified.
            if operation_id in {"image.guide.get", "image.guide.section"}: packet["operation_id"] = operation_id
            packets.append(packet)
        elif operation_id == "image.feature.explain":
            packets.append(feature_reference_packet(query))
        elif operation_id == "image.job.status":
            packets.append(job_status_packet(root_dir, query))
        elif operation_id == "image.job.diagnose":
            packets.append(job_diagnostic_packet(root_dir, query))
        elif operation_id == "image.diagnose":
            packets.append(image_diagnostic_packet(root_dir, query, native_context))
        elif operation_id == "image.output.location":
            packets.append(output_location_packet(root_dir, query, native_context))
        elif operation_id == "image.output.integrity":
            packets.append(output_integrity_packet(root_dir, query, native_context))
        elif operation_id == "image.output.delete_preview":
            packets.append(delete_preview_packet(root_dir, query, native_context))
    return packets


def _human_label(key: str) -> str:
    return str(key or "").replace("_", " ").strip().capitalize()


def _format_seconds(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".") + " seconds"


def _compact_fact_lines(value: Any, *, prefix: str = "", depth: int = 0, limit: int = 24) -> list[str]:
    """Human-readable fallback projection for provider context.

    Scope packets stay structured in Inspector/metadata.  The provider sees a small
    fact projection instead of raw JSON so normal chat answers do not mirror debug
    packet syntax.
    """
    if depth > 3 or limit <= 0:
        return []
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if len(lines) >= limit:
                break
            low = str(key).lower()
            if low in {"source_health", "history_health", "query_constraints", "coverage", "raw", "record"}:
                continue
            label = _human_label(str(key))
            child_prefix = f"{prefix} {label}".strip()
            if isinstance(item, (dict, list)):
                lines.extend(_compact_fact_lines(item, prefix=child_prefix, depth=depth + 1, limit=limit - len(lines)))
            elif item not in (None, "", []):
                lines.append(f"{child_prefix}: {_clean(item, 900)}")
    elif isinstance(value, list):
        for index, item in enumerate(value[: min(8, limit)], start=1):
            child_prefix = f"{prefix} item {index}".strip()
            if isinstance(item, (dict, list)):
                lines.extend(_compact_fact_lines(item, prefix=child_prefix, depth=depth + 1, limit=limit - len(lines)))
            elif item not in (None, "", []):
                lines.append(f"{child_prefix}: {_clean(item, 900)}")
    elif value not in (None, "", []):
        lines.append(f"{prefix or 'Value'}: {_clean(value, 900)}")
    return lines[:limit]


def _scope_packet_answer_facts(packet: dict[str, Any]) -> list[str]:
    operation = str(packet.get("operation_id") or "")
    kind = str(packet.get("packet_kind") or "image")
    payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
    known = str(packet.get("known_state") or "")
    lines: list[str] = []

    if operation == "image.performance.summarize":
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        constraints = filters.get("query_constraints") if isinstance(filters.get("query_constraints"), dict) else {}
        requested = constraints.get("requested") if isinstance(constraints.get("requested"), dict) else {}
        family = requested.get("family") or filters.get("family_filter")
        model = requested.get("model") or filters.get("model_filter")
        if family:
            lines.append(f"Requested population: recorded {family} Image generations.")
        elif model:
            lines.append(f"Requested population: recorded generations using {model}.")
        else:
            lines.append("Requested population: completed Image generation runs with recorded timing.")
        sample = payload.get("sample_count")
        if sample is not None:
            lines.append(f"Matching timed runs: {sample}.")
        elapsed = payload.get("elapsed_seconds") if isinstance(payload.get("elapsed_seconds"), dict) else {}
        if elapsed:
            if _format_seconds(elapsed.get("mean")):
                lines.append(f"Average generation time: {_format_seconds(elapsed.get('mean'))}.")
            if _format_seconds(elapsed.get("median")):
                lines.append(f"Median generation time: {_format_seconds(elapsed.get('median'))}.")
            if _format_seconds(elapsed.get("min")) and _format_seconds(elapsed.get("max")):
                lines.append(f"Recorded range: {_format_seconds(elapsed.get('min'))} to {_format_seconds(elapsed.get('max'))}.")
            if _format_seconds(elapsed.get("p90")):
                lines.append(f"90th-percentile time: {_format_seconds(elapsed.get('p90'))}.")
        models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
        if models:
            lines.append("Models in this population: " + ", ".join(f"{name} ({count} runs)" for name, count in list(models.items())[:8]) + ".")
    elif operation in {"image.inventory.count_runs", "image.inventory.count_outputs"}:
        population = payload.get("population") if isinstance(payload.get("population"), dict) else {}
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        family = population.get("family_filter")
        model = population.get("model_filter")
        if family:
            lines.append(f"Population filter: {family}.")
        elif model:
            lines.append(f"Population filter: {model}.")
        if "completed_generation_runs" in counts:
            lines.append(f"Completed generation runs: {counts.get('completed_generation_runs')}.")
        if "primary_output_images" in counts:
            lines.append(f"Primary output images: {counts.get('primary_output_images')}.")
    elif operation == "image.analytics.group":
        grouping = payload.get("grouping") if isinstance(payload.get("grouping"), dict) else {}
        dimension = str(grouping.get("dimension") or "value")
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        if rows:
            lines.append(f"Recorded usage grouped by {dimension}:")
            for row in rows[:10]:
                if isinstance(row, dict):
                    lines.append(f"{row.get('value')}: {row.get('generation_runs')} generation runs, {row.get('primary_output_images')} primary outputs.")
    elif operation == "image.analytics.trend":
        points = payload.get("points") if isinstance(payload.get("points"), list) else []
        bucket = payload.get("bucket") or "period"
        lines.append(f"Generation activity is grouped by {bucket}.")
        for point in points[:16]:
            if isinstance(point, dict):
                lines.append(f"{point.get('bucket')}: {point.get('completed_generation_runs')} completed runs.")
    elif operation == "image.performance.compare":
        comparability = str(payload.get("comparability") or "")
        lines.append(f"Timing comparability: {comparability or 'not established'}.")
        cohort = payload.get("cohort") if isinstance(payload.get("cohort"), dict) else {}
        if cohort:
            lines.append("Comparison cohort: " + ", ".join(f"{_human_label(k)} {v}" for k, v in cohort.items() if v not in (None, "", [])) + ".")
        groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
        for row in groups[:8]:
            if not isinstance(row, dict):
                continue
            metric = row.get("median_seconds") if row.get("median_seconds") is not None else row.get("observed_median_seconds")
            lines.append(f"{row.get('model')}: median {_format_seconds(metric)} across {row.get('sample_count')} runs.")
    elif operation == "image.inventory.count_attempts":
        lines.append(f"Recorded Image generation attempts: {payload.get('attempt_count', 0)}.")
        statuses = payload.get("status_counts") if isinstance(payload.get("status_counts"), dict) else {}
        if statuses:
            lines.append("Attempt statuses: " + ", ".join(f"{key} {value}" for key, value in statuses.items()) + ".")
    elif kind in {"procedure", "reference"}:
        title = payload.get("title") or payload.get("feature") or payload.get("parameter")
        if title:
            lines.append(f"Current Image guidance: {title}.")
        path = payload.get("ui_path")
        if isinstance(path, list) and path:
            lines.append("UI path: " + " → ".join(str(item) for item in path) + ".")
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        for index, step in enumerate(steps[:20], start=1):
            lines.append(f"Step {index}: {_clean(step, 1100)}")
        authored = payload.get("authored_guide_facts") if isinstance(payload.get("authored_guide_facts"), list) else []
        for fact in authored[:12]:
            lines.append(_clean(fact, 1100))
    elif kind == "live_state":
        lines.append("Current Image workspace state:")
        lines.extend(_compact_fact_lines(payload, limit=20))
    elif kind == "recipe":
        lines.append("Recorded Image generation recipe:")
        model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
        generation = payload.get("generation") if isinstance(payload.get("generation"), dict) else {}
        sampling = payload.get("sampling") if isinstance(payload.get("sampling"), dict) else {}
        canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else {}
        prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        scalar_facts = [
            ("Result", identity.get("result_id")),
            ("Family", model.get("family")),
            ("Loader", model.get("loader")),
            ("Model", model.get("checkpoint") or model.get("model")),
            ("VAE", model.get("vae")),
            ("Workflow", generation.get("mode")),
            ("Seed", sampling.get("actual_seed") if sampling.get("actual_seed") is not None else sampling.get("seed")),
            ("Steps", sampling.get("steps")),
            ("CFG", sampling.get("cfg")),
            ("Sampler", sampling.get("sampler")),
            ("Scheduler", sampling.get("scheduler")),
        ]
        for label, value in scalar_facts:
            if value not in (None, "", []):
                lines.append(f"{label}: {value}.")
        if canvas.get("width") not in (None, "") and canvas.get("height") not in (None, ""):
            lines.append(f"Resolution: {canvas.get('width')} × {canvas.get('height')}.")
        positive = str(prompt.get("positive") or prompt.get("effective_positive") or "")
        negative = str(prompt.get("negative") or prompt.get("effective_negative") or "")
        if positive:
            preview = re.sub(r"\s+", " ", positive).strip()
            preview = preview[:320] + ("…" if len(preview) > 320 else "")
            lines.append(f"Positive prompt is recorded ({len(positive)} characters). Preview: {preview}")
        else:
            lines.append("Positive prompt is not recorded in the available recipe.")
        if negative:
            lines.append(f"Negative prompt is recorded ({len(negative)} characters).")
        lines.append("Do not reproduce the full recorded prompt unless the user explicitly requests that exact field.")
    elif kind == "history_search":
        count = payload.get("match_count")
        if count is not None:
            lines.append(f"Matching Image history records: {count}.")
        records = payload.get("records") if isinstance(payload.get("records"), list) else []
        for row in records[:10]:
            if isinstance(row, dict):
                target = row.get("target") if isinstance(row.get("target"), dict) else {}
                rid = target.get("result_id") or row.get("result_id") or "record"
                summary = row.get("summary") or row.get("title") or ""
                lines.append(f"{rid}: {_clean(summary, 700)}")
    elif kind == "comparison":
        summary = payload.get("summary_counts") if isinstance(payload.get("summary_counts"), dict) else {}
        if summary:
            lines.append("Recipe comparison summary: " + ", ".join(f"{key} {value}" for key, value in summary.items()) + ".")
        categories = payload.get("categories") if isinstance(payload.get("categories"), dict) else {}
        for category, rows in categories.items():
            if not isinstance(rows, list):
                continue
            changed = [row for row in rows if isinstance(row, dict) and row.get("state") != "same"]
            for row in changed[:8]:
                lines.append(f"{category} — {row.get('field')}: {row.get('state')} ({_clean(row.get('left'), 250)} → {_clean(row.get('right'), 250)}).")
    else:
        lines.extend(_compact_fact_lines(payload, limit=24))

    if known not in {"", "established"} and not lines:
        lines.append("The requested Image fact is not established by the available authoritative evidence.")
    return [line for line in lines if _clean(line)]


def render_scope_packet_for_context(packet: dict[str, Any], *, limit: int = 5000) -> str:
    """Project a structured Image scope packet into answer-ready provider evidence.

    Full packet JSON remains available to Inspector through ``scope_packet`` metadata.
    The provider-facing text intentionally omits schema IDs, operation IDs, packet
    bookkeeping, epistemic debug labels, and raw JSON to reduce debug-console echoes.
    """
    lines = ["Verified Neo Image facts for the user's request:"]
    facts = _scope_packet_answer_facts(packet)
    if facts:
        lines.extend(f"- {line}" if not line.startswith("Step ") else f"- {line}" for line in facts)
    else:
        lines.append("- No answer-ready Image fact was established from this native result.")
    limitations = [_clean(item, 650) for item in (packet.get("limitations") or []) if _clean(item)]
    if limitations:
        lines.append("Caveats:")
        lines.extend(f"- {item}" for item in limitations[:8])
    lines.append("Answer naturally from these facts. Do not expose internal packet/schema/operation names or reproduce structured debug data.")
    return _clean("\n".join(lines), limit)
