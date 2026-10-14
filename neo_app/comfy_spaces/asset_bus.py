from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

from neo_app.comfy_spaces.asset_schema import (
    SPACE_ASSET_BUS_SCHEMA_VERSION,
    ComfySpaceAssetRecord,
    SpaceAssetBindingResolutionReport,
    SpaceAssetCleanupReport,
    SpaceAssetCollectionReport,
    SpaceAssetIngestReport,
    SpaceAssetIssue,
    SpaceAssetResolutionRecord,
    SpaceAssetSource,
    SpaceAssetTransportRecord,
)
from neo_app.comfy_spaces.binding_engine import SpaceAssetReference, bind_space_inputs
from neo_app.comfy_spaces.contract import parse_space_manifest
from neo_app.comfy_spaces.paths import (
    comfy_space_asset_record_path,
    comfy_space_asset_storage_dir,
    comfy_spaces_assets_root,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict
from neo_app.providers.comfy_asset_transport import (
    ComfyAssetTransportError,
    comfy_asset_transport_capabilities,
    download_comfy_artifact,
    upload_comfy_input_asset,
)


SPACE_ASSET_BUS_SERVICE_VERSION = "neo.comfy_space.asset_bus.service.v1"
_ASSET_KINDS = {"image", "mask", "video", "audio", "file"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".gif"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff"}
_MAX_BYTES = {
    "image": 128 * 1024 * 1024,
    "mask": 128 * 1024 * 1024,
    "audio": 1024 * 1024 * 1024,
    "video": 8 * 1024 * 1024 * 1024,
    "file": 4 * 1024 * 1024 * 1024,
}
_DEFAULT_TEMP_TTL_SECONDS = 24 * 60 * 60
_MAX_TEMP_TTL_SECONDS = 30 * 24 * 60 * 60
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class SpaceAssetBusError(ValueError):
    """Raised when a Comfy Space asset violates Neo's neutral asset boundary."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _root(root_dir: Path | str | None = None) -> Path:
    return Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parents[2]


def _safe_original_filename(value: str, *, kind: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SpaceAssetBusError("Asset filename must not be empty.")
    if "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise SpaceAssetBusError("Asset filename must not contain a local path.")
    name = _SAFE_FILENAME_RE.sub("_", raw).strip(" .")
    if not name:
        name = f"asset_{kind}.bin"
    if name.startswith("."):
        name = f"asset{name}"
    if len(name) > 180:
        suffix = Path(name).suffix[:16]
        name = f"{Path(name).stem[: max(1, 170 - len(suffix))]}{suffix}"
    return name


def _image_signature_ok(prefix: bytes) -> bool:
    return (
        prefix.startswith(b"\x89PNG\r\n\x1a\n")
        or prefix.startswith(b"\xff\xd8\xff")
        or prefix.startswith((b"GIF87a", b"GIF89a"))
        or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
        or prefix.startswith(b"BM")
        or prefix.startswith((b"II*\x00", b"MM\x00*"))
    )


def _normalized_content_type(content_type: str, filename: str) -> str:
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if declared:
        return declared
    return (mimetypes.guess_type(filename)[0] or "application/octet-stream").lower()


def _validate_asset_type(*, kind: str, filename: str, content_type: str, prefix: bytes) -> str:
    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in _ASSET_KINDS:
        raise SpaceAssetBusError(f"Unsupported Comfy Space asset kind: {kind!r}")
    ext = Path(filename).suffix.lower()
    mime = _normalized_content_type(content_type, filename)
    if clean_kind in {"image", "mask"}:
        if ext not in _IMAGE_EXTENSIONS:
            raise SpaceAssetBusError(f"Unsupported {clean_kind} asset extension: {ext or 'none'}")
        if mime != "application/octet-stream" and not mime.startswith("image/"):
            raise SpaceAssetBusError(f"Declared MIME type does not match {clean_kind} asset: {mime}")
        if not _image_signature_ok(prefix):
            raise SpaceAssetBusError(f"Uploaded {clean_kind} does not match a supported raster image signature.")
    elif clean_kind == "video":
        if ext not in _VIDEO_EXTENSIONS:
            raise SpaceAssetBusError(f"Unsupported video asset extension: {ext or 'none'}")
        if mime != "application/octet-stream" and not (mime.startswith("video/") or (ext == ".gif" and mime == "image/gif")):
            raise SpaceAssetBusError(f"Declared MIME type does not match video asset: {mime}")
    elif clean_kind == "audio":
        if ext not in _AUDIO_EXTENSIONS:
            raise SpaceAssetBusError(f"Unsupported audio asset extension: {ext or 'none'}")
        if mime != "application/octet-stream" and not mime.startswith("audio/"):
            raise SpaceAssetBusError(f"Declared MIME type does not match audio asset: {mime}")
    return mime


def _max_bytes(kind: str, override: int | None = None) -> int:
    limit = int(override if override is not None else _MAX_BYTES[str(kind)])
    if limit <= 0:
        raise SpaceAssetBusError("Asset size limit must be positive.")
    return limit


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _storage_relpath(path: Path, root_dir: Path | str | None) -> str:
    try:
        return path.resolve().relative_to(_root(root_dir)).as_posix()
    except ValueError as exc:  # pragma: no cover - constructed only from Neo-owned roots
        raise SpaceAssetBusError("Asset storage path escaped the Neo root.") from exc


def _content_path(record: ComfySpaceAssetRecord, root_dir: Path | str | None = None) -> Path:
    root = _root(root_dir)
    candidate = (root / record.storage_relpath).resolve()
    allowed = comfy_space_asset_storage_dir(record.asset_id, direction=record.direction, root_dir=root_dir).resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise SpaceAssetBusError("Asset record points outside its Neo-owned storage directory.") from exc
    return candidate


def _verify_record_content_integrity(record: ComfySpaceAssetRecord, root_dir: Path | str | None = None) -> Path:
    """Fail closed if Neo-owned asset bytes no longer match the immutable record digest."""

    path = _content_path(record, root_dir)
    if not path.is_file():
        raise SpaceAssetBusError(f"Comfy Space asset content is missing: {record.asset_id}")
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != record.size_bytes or digest.hexdigest() != record.sha256:
        raise SpaceAssetBusError(
            f"Comfy Space asset integrity check failed: {record.asset_id}. "
            "The stored bytes no longer match the asset record."
        )
    return path


def _new_asset_id() -> str:
    return f"asset_{uuid4().hex}"


def _retention_fields(retention: str, ttl_seconds: int | None) -> tuple[str, str | None]:
    clean = str(retention or "temporary").strip().lower()
    if clean not in {"temporary", "durable"}:
        raise SpaceAssetBusError("Asset retention must be temporary or durable.")
    if clean == "durable":
        return clean, None
    ttl = int(ttl_seconds if ttl_seconds is not None else _DEFAULT_TEMP_TTL_SECONDS)
    ttl = max(60, min(ttl, _MAX_TEMP_TTL_SECONDS))
    return clean, _iso(_now() + timedelta(seconds=ttl))


def _record_payload(record: ComfySpaceAssetRecord) -> dict[str, Any]:
    return model_to_dict(record)


def save_asset_record(record: ComfySpaceAssetRecord, *, root_dir: Path | str | None = None) -> None:
    _write_json_atomic(comfy_space_asset_record_path(record.asset_id, root_dir), _record_payload(record))


def load_asset_record(asset_id: str, *, root_dir: Path | str | None = None, require_content: bool = True) -> ComfySpaceAssetRecord:
    path = comfy_space_asset_record_path(asset_id, root_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = model_from_dict(ComfySpaceAssetRecord, payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SpaceAssetBusError(f"Unknown or invalid Comfy Space asset: {asset_id}") from exc
    if record.state != "ready":
        raise SpaceAssetBusError(f"Comfy Space asset is not available: {record.asset_id}")
    if require_content and not _content_path(record, root_dir).is_file():
        raise SpaceAssetBusError(f"Comfy Space asset content is missing: {record.asset_id}")
    return record


def asset_reference(record: ComfySpaceAssetRecord) -> SpaceAssetReference:
    return SpaceAssetReference(asset_id=record.asset_id, kind=record.kind)


def asset_content_url(asset_id: str) -> str:
    return f"/api/comfy-spaces/assets/{asset_id}/content"


def asset_content_path(asset_id: str, *, root_dir: Path | str | None = None) -> Path:
    record = load_asset_record(asset_id, root_dir=root_dir)
    return _content_path(record, root_dir)


def asset_public_payload(record: ComfySpaceAssetRecord) -> dict[str, Any]:
    return {
        "asset_id": record.asset_id,
        "kind": record.kind,
        "direction": record.direction,
        "origin": record.origin,
        "state": record.state,
        "filename": record.original_filename,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "retention": record.retention,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "label": record.label,
        "asset_ref": model_to_dict(asset_reference(record)),
        "content_url": asset_content_url(record.asset_id),
        "transport_count": len(record.transports),
        "local_path_exposed": False,
    }


def ingest_asset_stream(
    stream: BinaryIO,
    *,
    original_filename: str,
    kind: str,
    content_type: str = "",
    direction: str = "input",
    origin: str = "upload",
    retention: str = "temporary",
    ttl_seconds: int | None = None,
    label: str = "",
    source: SpaceAssetSource | Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    max_bytes: int | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Ingest arbitrary supported media/file bytes into Neo-owned Space storage.

    The caller supplies bytes, never a client-controlled destination path. Input
    assets and generated/output assets are isolated under separate Neo data roots.
    """

    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in _ASSET_KINDS:
        raise SpaceAssetBusError(f"Unsupported Comfy Space asset kind: {kind!r}")
    clean_direction = str(direction or "input").strip().lower()
    if clean_direction not in {"input", "output", "intermediate"}:
        raise SpaceAssetBusError("Asset direction must be input, output, or intermediate.")
    clean_origin = str(origin or "upload").strip().lower()
    allowed_origins = {"upload", "local_import", "comfy_output", "generated_intermediate"}
    if clean_origin not in allowed_origins:
        raise SpaceAssetBusError("Unsupported asset origin.")
    safe_name = _safe_original_filename(original_filename, kind=clean_kind)
    limit = _max_bytes(clean_kind, max_bytes)

    temp_root = _root(root_dir) / "neo_data" / "tmp" / "comfy_spaces_assets"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix="asset_ingest_", suffix=".tmp", dir=temp_root)
    os.close(temp_fd)  # Windows cannot move/delete a file while this descriptor is open.
    temp_path = Path(temp_name)
    storage_dir: Path | None = None
    size = 0
    digest = hashlib.sha256()
    prefix = bytearray()
    try:
        with temp_path.open("wb") as target:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise SpaceAssetBusError("Asset stream returned non-byte data.")
                size += len(chunk)
                if size > limit:
                    raise SpaceAssetBusError(f"{clean_kind} asset exceeds Neo's {limit} byte limit.")
                if len(prefix) < 32:
                    prefix.extend(chunk[: 32 - len(prefix)])
                digest.update(chunk)
                target.write(chunk)
        if size <= 0:
            raise SpaceAssetBusError("Uploaded asset is empty.")
        normalized_mime = _validate_asset_type(
            kind=clean_kind,
            filename=safe_name,
            content_type=content_type,
            prefix=bytes(prefix),
        )
        asset_id = _new_asset_id()
        retention_value, expires_at = _retention_fields(retention, ttl_seconds)
        storage_dir = comfy_space_asset_storage_dir(asset_id, direction=clean_direction, root_dir=root_dir)
        storage_dir.mkdir(parents=True, exist_ok=False)
        stored_name = safe_name
        final_path = storage_dir / stored_name
        os.replace(temp_path, final_path)
        now = _iso(_now())
        source_model = None
        if source is not None:
            source_model = source if isinstance(source, SpaceAssetSource) else model_from_dict(SpaceAssetSource, dict(source))
        record = ComfySpaceAssetRecord(
            asset_id=asset_id,
            kind=clean_kind,  # type: ignore[arg-type]
            direction=clean_direction,  # type: ignore[arg-type]
            origin=clean_origin,  # type: ignore[arg-type]
            original_filename=safe_name,
            stored_filename=stored_name,
            content_type=normalized_mime,
            size_bytes=size,
            sha256=digest.hexdigest(),
            storage_relpath=_storage_relpath(final_path, root_dir),
            retention=retention_value,  # type: ignore[arg-type]
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            label=str(label or "").strip(),
            source=source_model,
            metadata=dict(metadata or {}),
        )
        save_asset_record(record, root_dir=root_dir)
        report = SpaceAssetIngestReport(
            asset=record,
            asset_ref=asset_reference(record),
            content_url=asset_content_url(record.asset_id),
        )
        return model_to_dict(report)
    except Exception:
        temp_path.unlink(missing_ok=True)
        if storage_dir is not None:
            shutil.rmtree(storage_dir, ignore_errors=True)
        raise


def ingest_asset_bytes(data: bytes, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise SpaceAssetBusError("Asset payload must be bytes.")
    return ingest_asset_stream(io.BytesIO(bytes(data)), **kwargs)


def ingest_local_asset(path: Path | str, **kwargs: Any) -> dict[str, Any]:
    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise SpaceAssetBusError(f"Local asset file does not exist: {source_path.name}")
    kwargs.setdefault("original_filename", source_path.name)
    kwargs.setdefault("content_type", mimetypes.guess_type(source_path.name)[0] or "application/octet-stream")
    kwargs.setdefault("origin", "local_import")
    with source_path.open("rb") as handle:
        return ingest_asset_stream(handle, **kwargs)


def _updated_record_with_transport(
    record: ComfySpaceAssetRecord,
    transport: Mapping[str, Any],
) -> ComfySpaceAssetRecord:
    now = _iso(_now())
    transport_record = SpaceAssetTransportRecord(
        runtime_profile_id=str(transport.get("profile_id") or ""),
        provider_id=str(transport.get("provider_id") or ""),
        method=str(transport.get("method") or ""),
        comfy_name=str(transport.get("name") or ""),
        comfy_subfolder=str(transport.get("subfolder") or ""),
        comfy_type="input",
        comfy_value=str(transport.get("comfy_value") or ""),
        verified=bool(transport.get("verified")),
        content_sha256=record.sha256,
        resolved_at=now,
    )
    payload = _record_payload(record)
    existing = [
        item for item in list(payload.get("transports") or [])
        if str(item.get("runtime_profile_id") or "") != transport_record.runtime_profile_id
    ]
    existing.append(model_to_dict(transport_record))
    payload["transports"] = existing
    payload["updated_at"] = now
    return model_from_dict(ComfySpaceAssetRecord, payload)


def resolve_asset_for_comfy(
    asset_ref: SpaceAssetReference | Mapping[str, Any],
    *,
    runtime_profile_id: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        ref = asset_ref if isinstance(asset_ref, SpaceAssetReference) else model_from_dict(SpaceAssetReference, dict(asset_ref))
    except Exception as exc:
        raise SpaceAssetBusError(str(exc)) from exc
    record = load_asset_record(ref.asset_id, root_dir=root_dir)
    if record.kind != ref.kind:
        raise SpaceAssetBusError(f"Asset kind mismatch for {record.asset_id}: expected {ref.kind}, stored {record.kind}.")
    content_path = _verify_record_content_integrity(record, root_dir)
    try:
        transport = upload_comfy_input_asset(
            runtime_profile_id,
            file_path=content_path,
            original_filename=record.original_filename,
            kind=record.kind,
            sha256=record.sha256,
        )
    except ComfyAssetTransportError as exc:
        raise SpaceAssetBusError(str(exc)) from exc
    updated = _updated_record_with_transport(record, transport)
    save_asset_record(updated, root_dir=root_dir)
    resolution = SpaceAssetResolutionRecord(
        asset_id=updated.asset_id,
        kind=updated.kind,
        runtime_profile_id=str(transport.get("profile_id") or runtime_profile_id),
        comfy_value=str(transport.get("comfy_value") or ""),
        reused_transport=bool(transport.get("reused")),
        verified=bool(transport.get("verified")),
        transport_method=str(transport.get("method") or ""),
    )
    return model_to_dict(resolution)


def _asset_values_for_definition(definition, values: Mapping[str, Any]) -> tuple[bool, Any]:
    if definition.input_id in values:
        return True, values[definition.input_id]
    if definition.default is not None:
        return True, definition.default
    return False, None


def resolve_space_asset_inputs(
    manifest: Mapping[str, Any] | Any,
    values: Mapping[str, Any] | None,
    *,
    runtime_profile_id: str,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve every provided Space media/file reference into a Comfy-ready value.

    Validation is two-pass: all references are checked against Neo storage first;
    only then can transport begin. Raw paths/filenames never enter this API.
    """

    space = manifest if hasattr(manifest, "inputs") else parse_space_manifest(dict(manifest))
    supplied = dict(values or {})
    errors: list[SpaceAssetIssue] = []
    warnings: list[SpaceAssetIssue] = []
    pending: list[tuple[Any, list[SpaceAssetReference], bool]] = []

    for definition in space.inputs:
        if definition.kind not in _ASSET_KINDS:
            continue
        present, raw_value = _asset_values_for_definition(definition, supplied)
        if not present:
            if definition.required:
                errors.append(SpaceAssetIssue(code="missing_required_asset", message=f"Required asset input '{definition.input_id}' was not supplied.", input_id=definition.input_id))
            continue
        if raw_value is None or raw_value == "" or (isinstance(raw_value, (list, tuple)) and len(raw_value) == 0):
            if definition.required:
                errors.append(SpaceAssetIssue(
                    code="missing_required_asset",
                    message=f"Select and upload an image/file for required input '{definition.input_id}' before validating or generating.",
                    input_id=definition.input_id,
                ))
            continue
        raw_items = raw_value if definition.multiplicity == "multiple" and isinstance(raw_value, (list, tuple)) else [raw_value]
        if definition.multiplicity == "multiple" and not isinstance(raw_value, (list, tuple)):
            errors.append(SpaceAssetIssue(code="asset_multiplicity_mismatch", message=f"Asset input '{definition.input_id}' requires multiple asset references.", input_id=definition.input_id))
            continue
        refs: list[SpaceAssetReference] = []
        for raw in raw_items:
            try:
                ref = model_from_dict(SpaceAssetReference, dict(raw)) if isinstance(raw, Mapping) else None
                if ref is None:
                    raise ValueError("raw paths and filenames are not accepted")
                if ref.kind != definition.kind:
                    raise ValueError(f"expected {definition.kind}, got {ref.kind}")
                record = load_asset_record(ref.asset_id, root_dir=root_dir)
                if record.kind != ref.kind:
                    raise ValueError(f"stored asset kind is {record.kind}, reference says {ref.kind}")
                refs.append(ref)
            except Exception as exc:  # noqa: BLE001 - normalized into fail-closed report
                errors.append(SpaceAssetIssue(code="invalid_asset_reference", message=f"Asset input '{definition.input_id}' is invalid: {exc}", input_id=definition.input_id))
        if refs:
            pending.append((definition, refs, definition.multiplicity == "multiple"))

    if errors:
        report = SpaceAssetBindingResolutionReport(
            ok=False,
            ready_for_binding=False,
            runtime_profile_id=str(runtime_profile_id or ""),
            errors=errors,
            warnings=warnings,
        )
        return model_to_dict(report)

    resolved_values: dict[str, Any] = {}
    resolutions: list[SpaceAssetResolutionRecord] = []
    for definition, refs, multiple in pending:
        resolved_for_input: list[str] = []
        for ref in refs:
            try:
                resolved = resolve_asset_for_comfy(ref, runtime_profile_id=runtime_profile_id, root_dir=root_dir)
                resolution = model_from_dict(SpaceAssetResolutionRecord, resolved)
                resolutions.append(resolution)
                resolved_for_input.append(resolution.comfy_value)
            except Exception as exc:  # noqa: BLE001
                errors.append(SpaceAssetIssue(code="asset_transport_failed", message=str(exc), input_id=definition.input_id, asset_id=ref.asset_id))
        if len(resolved_for_input) == len(refs):
            resolved_values[definition.input_id] = resolved_for_input if multiple else resolved_for_input[0]

    ok = not errors
    report = SpaceAssetBindingResolutionReport(
        ok=ok,
        ready_for_binding=ok,
        runtime_profile_id=str(runtime_profile_id or ""),
        resolved_values=resolved_values if ok else {},
        resolutions=resolutions,
        errors=errors,
        warnings=warnings,
    )
    return model_to_dict(report)


def prepare_space_asset_bindings(
    workflow: Mapping[str, Any],
    manifest: Mapping[str, Any] | Any,
    values: Mapping[str, Any] | None,
    *,
    runtime_profile_id: str,
    object_info: Mapping[str, Any] | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve NCS-6 assets and feed only Comfy-ready values into the NCS-4 clone/bind seam."""

    resolution = resolve_space_asset_inputs(manifest, values, runtime_profile_id=runtime_profile_id, root_dir=root_dir)
    if not resolution.get("ok"):
        return {
            "ok": False,
            "ready_for_execution": False,
            "asset_resolution": resolution,
            "binding": None,
            "workflow": None,
            "execution_performed": False,
        }
    binding = bind_space_inputs(
        workflow,
        manifest,
        values,
        resolved_asset_values=resolution.get("resolved_values") or {},
        object_info=object_info,
    )
    return {
        "ok": bool(binding.get("ok")),
        "ready_for_execution": bool((binding.get("report") or {}).get("ready_for_execution")),
        "asset_resolution": resolution,
        "binding": binding.get("report"),
        "workflow": binding.get("workflow"),
        "execution_performed": False,
    }


def collect_comfy_artifact(
    runtime_profile_id: str,
    *,
    kind: str,
    filename: str,
    subfolder: str = "",
    artifact_type: str = "output",
    prompt_id: str | None = None,
    node_id: str | None = None,
    label: str = "",
    retention: str = "durable",
    ttl_seconds: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in _ASSET_KINDS:
        raise SpaceAssetBusError(f"Unsupported collected artifact kind: {kind!r}")
    temp_root = _root(root_dir) / "neo_data" / "tmp" / "comfy_spaces_assets"
    temp_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(filename or "artifact.bin")).suffix or ".bin"
    temp_fd, temp_name = tempfile.mkstemp(prefix="asset_collect_", suffix=suffix, dir=temp_root)
    os.close(temp_fd)  # Release the handle before the downloader writes to the file.
    temp_path = Path(temp_name)
    try:
        transport = download_comfy_artifact(
            runtime_profile_id,
            filename=filename,
            subfolder=subfolder,
            artifact_type=artifact_type,
            destination=temp_path,
            max_bytes=_max_bytes(clean_kind),
        )
        source = SpaceAssetSource(
            runtime_profile_id=str(transport.get("profile_id") or runtime_profile_id),
            provider_id=str(transport.get("provider_id") or ""),
            prompt_id=str(prompt_id or "").strip() or None,
            node_id=str(node_id or "").strip() or None,
            filename=str(transport.get("filename") or filename),
            subfolder=str(transport.get("subfolder") or subfolder),
            artifact_type=str(transport.get("type") or artifact_type),
            label=str(label or "").strip(),
            metadata={"transport_method": str(transport.get("method") or ""), **dict(metadata or {})},
        )
        with temp_path.open("rb") as handle:
            ingested = ingest_asset_stream(
                handle,
                original_filename=str(transport.get("filename") or filename),
                kind=clean_kind,
                content_type=str(transport.get("content_type") or "application/octet-stream"),
                direction="output",
                origin="comfy_output",
                retention=retention,
                ttl_seconds=ttl_seconds,
                label=label,
                source=source,
                metadata=metadata,
                root_dir=root_dir,
            )
        asset = model_from_dict(ComfySpaceAssetRecord, ingested["asset"])
        report = SpaceAssetCollectionReport(
            asset=asset,
            asset_ref=asset_reference(asset),
            content_url=asset_content_url(asset.asset_id),
        )
        return model_to_dict(report)
    finally:
        temp_path.unlink(missing_ok=True)


def delete_asset(asset_id: str, *, force: bool = False, root_dir: Path | str | None = None) -> dict[str, Any]:
    # Deletion must still be able to clean stale metadata when the content file is already gone.
    record = load_asset_record(asset_id, root_dir=root_dir, require_content=False)
    if record.retention == "durable" and not force:
        raise SpaceAssetBusError("Durable assets require explicit force deletion.")
    storage_dir = comfy_space_asset_storage_dir(record.asset_id, direction=record.direction, root_dir=root_dir)
    shutil.rmtree(storage_dir, ignore_errors=True)
    comfy_space_asset_record_path(record.asset_id, root_dir).unlink(missing_ok=True)
    return {"ok": True, "asset_id": record.asset_id, "deleted": True, "workflow_execution_performed": False}


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cleanup_expired_assets(*, now: datetime | None = None, root_dir: Path | str | None = None) -> dict[str, Any]:
    current = (now or _now()).astimezone(timezone.utc)
    root = comfy_spaces_assets_root(root_dir)
    scanned = 0
    deleted = 0
    skipped_durable = 0
    errors: list[SpaceAssetIssue] = []
    if root.is_dir():
        for path in sorted(root.glob("asset_*.json")):
            scanned += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = model_from_dict(ComfySpaceAssetRecord, payload)
                if record.retention == "durable":
                    skipped_durable += 1
                    continue
                expires = _parse_iso(record.expires_at)
                if expires is not None and expires <= current:
                    delete_asset(record.asset_id, force=True, root_dir=root_dir)
                    deleted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(SpaceAssetIssue(code="cleanup_failed", message=str(exc), asset_id=path.stem))
    report = SpaceAssetCleanupReport(
        ok=not errors,
        scanned=scanned,
        deleted=deleted,
        skipped_durable=skipped_durable,
        errors=errors,
    )
    return model_to_dict(report)


def list_assets(*, root_dir: Path | str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    root = comfy_spaces_assets_root(root_dir)
    if root.is_dir():
        for path in sorted(root.glob("asset_*.json")):
            try:
                record = model_from_dict(ComfySpaceAssetRecord, json.loads(path.read_text(encoding="utf-8")))
                if record.state == "ready":
                    rows.append(asset_public_payload(record))
            except Exception as exc:  # noqa: BLE001
                errors.append({"asset_id": path.stem, "error": str(exc)})
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "schema_version": SPACE_ASSET_BUS_SCHEMA_VERSION,
        "ok": not errors,
        "assets": rows,
        "errors": errors,
        "count": len(rows),
        "workflow_execution_performed": False,
    }


def asset_bus_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_ASSET_BUS_SCHEMA_VERSION,
        "service_version": SPACE_ASSET_BUS_SERVICE_VERSION,
        "status": "implemented",
        "neutral_media_kinds": sorted(_ASSET_KINDS),
        "neo_asset_ref_authority": True,
        "raw_local_paths_accepted_from_controls": False,
        "neo_owned_input_storage": "neo_data/inputs/comfy_spaces",
        "neo_owned_output_storage": "neo_data/outputs/comfy_spaces",
        "stable_asset_ids": True,
        "sha256_metadata": True,
        "temporary_retention_cleanup": True,
        "durable_retention": True,
        "streaming_ingest": True,
        "comfy_input_resolution": True,
        "comfy_output_collection": True,
        "multi_media_binding_resolution": True,
        "binding_engine_bridge": "ncs4_resolved_asset_values",
        "workflow_execution": False,
        "prompt_submission": False,
        "job_persistence": False,
        "provider_transport": comfy_asset_transport_capabilities(),
    }
