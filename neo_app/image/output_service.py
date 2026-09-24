from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable
from urllib import parse, request

from neo_app.image.output_paths import IMAGE_OUTPUT_CATEGORIES, ROOT_DIR, get_image_output_paths, sanitize_path_part
from neo_app.image.portable_metadata import (
    build_portable_payload,
    embed_portable_metadata_file,
    extract_portable_metadata_bytes,
    file_integrity,
    new_neo_output_id,
    new_neo_result_uid,
)
from neo_app.image.upload_validation import ALLOWED_IMAGE_EXTENSIONS, _detect_image_type, canonical_image_suffix_for_type
from neo_app.providers.comfy_artifact_paths import ComfyArtifactPathError, normalize_comfy_artifact_reference
from neo_app.image.action_state import sanitize_replay_extensions, sanitize_replay_params
from neo_app.image.lanpaint_replay import build_lanpaint_replay_contract
from neo_app.image.output_settings import (
    ensure_output_settings_dirs,
    category_slug,
    load_image_output_settings,
    metadata_category_dir,
    next_category_index,
    output_category_dir,
)
from neo_app.image.output_records import (
    build_assistant_output_summary,
    build_image_output_record,
    build_output_lineage_metadata,
    build_output_file_record,
    build_output_replay_metadata,
    build_output_replay_payload,
    build_provider_binding_metadata,
    build_provider_replay_validation_metadata,
    extension_metadata_with_run_timing,
    normalize_run_timing,
    utc_now_iso,
)


@dataclass(frozen=True)
class PersistedImageOutputs:
    """Result of copying backend image outputs into Neo_Data."""

    ok: bool
    result_id: str
    record: dict[str, Any]
    record_path: Path
    files: list[dict[str, Any]]
    errors: list[str]


def _parse_timing_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _elapsed_label_from_seconds(value: float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining = seconds % 60
    if hours:
        return f"{hours}h {minutes:02d}m {remaining:02d}s"
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"


def _completed_image_run_timing_source(context: dict[str, Any], record_params: dict[str, Any], *, completed_at: str) -> dict[str, Any]:
    source = context.get("run_timing") if isinstance(context.get("run_timing"), dict) else {}
    if not source:
        source = record_params.get("_neo_run_timing") if isinstance(record_params.get("_neo_run_timing"), dict) else {}
    if not source:
        source = record_params.get("_neo_client_run_timing") if isinstance(record_params.get("_neo_client_run_timing"), dict) else {}
    if not source:
        return {}
    merged = dict(source)
    completed_dt = _parse_timing_iso(merged.get("completed_at")) or _parse_timing_iso(completed_at) or datetime.now(timezone.utc)
    started_dt = _parse_timing_iso(merged.get("started_at") or merged.get("queued_at"))
    elapsed = merged.get("elapsed_seconds")
    try:
        elapsed_seconds = float(elapsed) if elapsed not in (None, "") else 0.0
    except Exception:
        elapsed_seconds = 0.0
    if elapsed_seconds <= 0 and started_dt:
        elapsed_seconds = max(0.0, round((completed_dt - started_dt).total_seconds(), 3))
        merged["elapsed_seconds"] = elapsed_seconds
        merged["elapsed_ms"] = round(elapsed_seconds * 1000.0, 3)
        merged["elapsed_label"] = _elapsed_label_from_seconds(elapsed_seconds)
    if not merged.get("completed_at"):
        merged["completed_at"] = completed_dt.isoformat().replace("+00:00", "Z")
    merged["state"] = "completed"
    if not merged.get("timing_source"):
        merged["timing_source"] = "client_wall_clock_fallback"
    return merged


def persist_image_outputs(
    *,
    provider_outputs: list[dict[str, Any]],
    job_context: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> PersistedImageOutputs:
    """Copy provider image outputs into Neo-owned storage and write sidecar metadata.

    Provider outputs, including ComfyUI `/view` URLs, are source references only.
    This service creates the final Neo-owned files under `neo_data/outputs/image/*`
    and writes the `neo.image.output.v1` sidecar under `neo_data/outputs/image_metadata/*`.
    """
    context = job_context if isinstance(job_context, dict) else {}
    mode = context.get("subtab") or context.get("mode") or "generate"
    settings = load_image_output_settings()
    ensure_output_settings_dirs(settings)
    paths = get_image_output_paths(mode, create=True)
    # Phase 11.5G+ save-detail routing: keep Result history grouped by workflow mode,
    # but place final files in the user-selected Results > Results & Save Details category.
    paths = type(paths)(
        category=paths.category,
        output_dir=output_category_dir(settings),
        metadata_dir=metadata_category_dir(settings),
    ).ensure()
    job_id = str(context.get("job_id") or "job")
    provider_id = str(context.get("provider_id") or "")
    profile_id = str(context.get("backend_profile_id") or context.get("profile_id") or "")
    neo_result_uid = new_neo_result_uid()
    result_id = _build_result_id(paths.category, job_id)
    if paths.metadata_file(result_id).exists():
        result_id = sanitize_path_part(f"{result_id}_{neo_result_uid.rsplit('_', 1)[-1][:12]}", fallback="image_output")
    filename_prefix = sanitize_path_part(settings.get("filename_prefix"), fallback="NeoStudio")
    filename_index = next_category_index(paths.output_dir, filename_prefix, int(settings.get("filename_padding") or 4))
    created_at = utc_now_iso()

    files: list[dict[str, Any]] = []
    errors: list[str] = []

    image_outputs = [item for item in provider_outputs if isinstance(item, dict) and (item.get("kind") in (None, "image"))]
    latent_outputs = [item for item in provider_outputs if isinstance(item, dict) and item.get("kind") == "latent"]
    for index, output in enumerate(image_outputs, start=1):
        try:
            source_url = str(output.get("url") or "")
            original_name = str(output.get("filename") or f"{result_id}_{index}.png")
            data = _read_output_bytes(output, timeout=timeout)
            detected_type = _detect_image_type(data)
            filename = _category_output_filename(
                paths.output_dir,
                filename_prefix,
                filename_index + index - 1,
                int(settings.get("filename_padding") or 4),
                original_name,
                detected_type=detected_type,
            )
            target = paths.image_file(filename)
            target.write_bytes(data)
            mime_type = _guess_mime_type(target.name, detected_type=detected_type)
            file_id = f"image_{index}"
            file_record = build_output_file_record(
                file_id=file_id,
                filename=target.name,
                path=_relative_to_root(target),
                url=f"/api/image/output-file?result_id={result_id}&file_id={file_id}",
                mime_type=mime_type,
                role="image",
                metadata=_output_file_metadata(output, index=index),
            )
            file_record["neo_output_id"] = new_neo_output_id()
            files.append(file_record)
        except Exception as exc:  # noqa: BLE001 - keep partial persistence visible to UI.
            errors.append(f"Output {index} could not be persisted: {exc}")

    latent_artifacts = persist_latent_outputs(latent_outputs, result_id=result_id, timeout=timeout)
    for artifact in latent_artifacts:
        if isinstance(artifact, dict) and artifact.get("neo_copy_state") == "failed_to_persist":
            label = str(artifact.get("restore_point") or artifact.get("artifact_id") or "latent")
            detail = str(artifact.get("neo_copy_error") or "unknown persistence error")
            errors.append(f"Latent {label} could not be copied into Neo storage: {detail}")
    first_output = image_outputs[0] if image_outputs else {}
    record_params = context.get("params") if isinstance(context.get("params"), dict) else {}
    if latent_artifacts:
        existing_latents = record_params.get("_neo_latent_artifacts") if isinstance(record_params.get("_neo_latent_artifacts"), list) else []
        record_params = {**record_params, "_neo_latent_artifacts": [*existing_latents, *latent_artifacts]}
    # Preserve both the user request sentinel and the concrete backend seed when
    # providers resolve random seed values. Older records may only contain -1; the
    # UI will show those as random instead of pretending -1 was the actual seed.
    requested_seed = record_params.get("requested_seed")
    seed_value = record_params.get("seed")
    try:
        requested_seed_int = int(requested_seed)
    except Exception:
        requested_seed_int = None
    try:
        seed_int = int(seed_value)
    except Exception:
        seed_int = None
    if requested_seed_int is not None and requested_seed_int < 0 and seed_int is not None and seed_int >= 0:
        record_params = {**record_params, "actual_seed": seed_int, "requested_seed": requested_seed_int, "seed": seed_int}
    run_timing_source = _completed_image_run_timing_source(context, record_params, completed_at=created_at)
    run_timing = normalize_run_timing(run_timing_source)
    if run_timing:
        record_params = {**record_params, "_neo_run_timing": run_timing}
    record_extensions = context.get("extensions") if isinstance(context.get("extensions"), dict) else {}
    if run_timing:
        record_extensions = extension_metadata_with_run_timing(record_extensions, run_timing)
    record = build_image_output_record(
        mode=str(context.get("mode") or "generate"),
        subtab=paths.category,
        job_id=job_id,
        provider_id=provider_id,
        backend_profile_id=profile_id,
        status="completed" if files and not errors else ("failed" if not files else "completed_with_warnings"),
        positive_prompt=str(context.get("positive_prompt") or context.get("prompt") or ""),
        negative_prompt=str(context.get("negative_prompt") or ""),
        effective_positive_prompt=((context.get("prompt_conditioning") or {}).get("effective_positive") if isinstance(context.get("prompt_conditioning"), dict) else None),
        effective_negative_prompt=((context.get("prompt_conditioning") or {}).get("effective_negative") if isinstance(context.get("prompt_conditioning"), dict) else None),
        prompt_conditioning=context.get("prompt_conditioning") if isinstance(context.get("prompt_conditioning"), dict) else {},
        params=record_params,
        model=context.get("model") if isinstance(context.get("model"), dict) else {},
        extensions=record_extensions,
        route_snapshot=context.get("route_snapshot") if isinstance(context.get("route_snapshot"), dict) else None,
        run_timing=run_timing,
        output_files=files,
        active_file=files[0]["file_id"] if files else "",
        backend_output_ref=_backend_output_ref(first_output),
        comfy_view_url=str(first_output.get("url") or ""),
        created_at=created_at,
        result_id=result_id,
    )

    record["neo_result_uid"] = neo_result_uid
    record["lineage"] = build_output_lineage_metadata(record)
    record["provider_binding"] = build_provider_binding_metadata(record)
    record["replay_validation"] = build_provider_replay_validation_metadata(record)

    record.setdefault("save_details", {}).update({
        "category": settings.get("selected_category") or "Uncategorized",
        "filename_prefix": filename_prefix,
        "filename_padding": int(settings.get("filename_padding") or 4),
        "cleanup_backend_native_outputs": bool(settings.get("cleanup_backend_native_outputs", True)),
    })
    input_assets = collect_input_asset_records(record_params, extensions=record_extensions)
    record.setdefault("source", {})["input_assets"] = input_assets
    record.setdefault("source", {})["asset_contract"] = build_input_asset_contract(input_assets)
    lanpaint_contract = build_lanpaint_replay_contract(
        record_params,
        provider_id=provider_id,
        input_assets=input_assets,
        route_snapshot=record.get("route_snapshot") if isinstance(record.get("route_snapshot"), dict) else {},
        output_lineage=record.get("lineage") if isinstance(record.get("lineage"), dict) else {},
    )
    if lanpaint_contract:
        record["lanpaint"] = lanpaint_contract
        record_params = {**record_params, "lanpaint_replay": lanpaint_contract, "lanpaint_replay_fingerprint": lanpaint_contract.get("replay_fingerprint")}
        record["params"] = record_params

    background_block = (record_extensions.get("payloads") or {}).get("image.background_removal") if isinstance(record_extensions.get("payloads"), dict) else None
    if isinstance(background_block, dict) and background_block.get("enabled"):
        try:
            from neo_extensions.built_in.background_removal.backend.verification import verify_background_removal_outputs

            background_params = background_block.get("params") if isinstance(background_block.get("params"), dict) else {}
            verification = verify_background_removal_outputs(
                root_dir=ROOT_DIR,
                provider_outputs=image_outputs,
                persisted_files=files,
                save_mask=bool(background_params.get("save_mask", True)),
            )
            record.setdefault("persistence", {})["background_removal_verification"] = verification
            memory_events = record_extensions.setdefault("memory_events", {})
            event = memory_events.get("image.background_removal") if isinstance(memory_events.get("image.background_removal"), dict) else {}
            memory_events["image.background_removal"] = {**event, "output_verification": verification}
            record["extensions"] = record_extensions
            if verification.get("errors"):
                errors.extend([f"Background Removal verification: {item}" for item in verification.get("errors") or []])
                record["status"] = "completed_with_warnings"
        except Exception as exc:  # noqa: BLE001
            verification_error = f"Background Removal output verification could not run: {exc}"
            errors.append(verification_error)
            record.setdefault("persistence", {})["background_removal_verification"] = {
                "schema_version": "neo.image.background_removal_verification.v1",
                "status": "failed",
                "ok": False,
                "errors": [verification_error],
            }
            record["status"] = "completed_with_warnings"

    record["assistant_summary"] = build_assistant_output_summary(record)
    record["replay"] = build_output_replay_metadata(record)
    record["replay_payload"] = build_output_replay_payload(record)
    record.setdefault("workflow_memory", {}).update({
        "namespace": "image",
        "assistant_summary": record["assistant_summary"],
    })
    if latent_artifacts:
        record.setdefault("persistence", {})["latent_artifacts"] = latent_artifacts

    portable_report = _embed_portable_metadata_for_record(record, files)
    record.setdefault("persistence", {})["portable_metadata"] = portable_report
    if portable_report.get("errors"):
        # Embedded metadata is a recovery enhancement, not generation authority.
        # Keep the image/result successful while making unsupported/corrupt formats visible.
        record.setdefault("persistence", {}).setdefault("warnings", []).extend(portable_report.get("errors") or [])
    if errors:
        record.setdefault("persistence", {})["errors"] = errors
    cleanup = cleanup_backend_native_outputs(image_outputs, context=context, enabled=bool(settings.get("cleanup_backend_native_outputs", True)))
    input_cleanup = cleanup_backend_input_handoffs(record_params, context=context, extensions=record_extensions, enabled=bool(settings.get("cleanup_backend_native_outputs", True)))
    asset_cleanup = build_asset_cleanup_report(backend_cleanup=cleanup, backend_input_cleanup=input_cleanup, input_assets=input_assets)
    record.setdefault("persistence", {})["backend_cleanup"] = cleanup
    record.setdefault("persistence", {})["backend_input_cleanup"] = input_cleanup
    record.setdefault("persistence", {})["asset_cleanup"] = asset_cleanup
    record["cleanup"] = asset_cleanup
    record_path = paths.metadata_file(result_id)
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    _register_record_identities(record_path, record)

    return PersistedImageOutputs(
        ok=bool(files) and not errors,
        result_id=result_id,
        record=record,
        record_path=record_path,
        files=files,
        errors=errors,
    )



IMAGE_OUTPUT_IDENTITY_INDEX_SCHEMA = "neo.image.output_identity_index.v1"
IMAGE_METADATA_QUARANTINE_ROOT = ROOT_DIR / "neo_data" / "outputs" / "image_metadata_quarantine"
IMAGE_OUTPUT_IDENTITY_INDEX_PATH = ROOT_DIR / "neo_data" / "cache" / "image_output_identity_index.json"
_IDENTITY_INDEX_LOCK = Lock()


def _load_output_identity_index() -> dict[str, Any]:
    try:
        raw = json.loads(IMAGE_OUTPUT_IDENTITY_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    identities = raw.get("identities") if isinstance(raw.get("identities"), dict) else {}
    return {"schema_version": IMAGE_OUTPUT_IDENTITY_INDEX_SCHEMA, "identities": identities}


def _save_output_identity_index(payload: dict[str, Any]) -> None:
    IMAGE_OUTPUT_IDENTITY_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = IMAGE_OUTPUT_IDENTITY_INDEX_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(IMAGE_OUTPUT_IDENTITY_INDEX_PATH)


def _register_record_identities(record_path: Path, record: dict[str, Any]) -> None:
    with _IDENTITY_INDEX_LOCK:
        index = _load_output_identity_index()
        identities = index.setdefault("identities", {})
        outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
        files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
        for item in files:
            if not isinstance(item, dict):
                continue
            output_id = str(item.get("neo_output_id") or "").strip()
            if not output_id:
                continue
            identities[output_id] = {
                "result_id": str(record.get("result_id") or ""),
                "neo_result_uid": str(record.get("neo_result_uid") or ""),
                "file_id": str(item.get("file_id") or ""),
                "metadata_path": _relative_to_root(record_path),
                "state": "active" if "image_metadata_quarantine" not in record_path.as_posix() else "quarantined",
                "updated_at": utc_now_iso(),
            }
        _save_output_identity_index(index)


def _embed_portable_metadata_for_record(record: dict[str, Any], live_files: list[dict[str, Any]]) -> dict[str, Any]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    record_files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    by_id = {str(item.get("file_id") or ""): item for item in live_files if isinstance(item, dict)}
    embedded: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in record_files:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or "")
        original = by_id.get(file_id)
        if original and not item.get("neo_output_id"):
            item["neo_output_id"] = original.get("neo_output_id")
        path_value = str(item.get("path") or "").strip()
        if not path_value:
            continue
        path = (ROOT_DIR / path_value).resolve()
        try:
            payload = build_portable_payload(record, item)
            report = embed_portable_metadata_file(path, payload)
            item["portable_metadata"] = report
            item["integrity"] = file_integrity(path)
            if original is not None:
                original["portable_metadata"] = dict(report)
                original["integrity"] = dict(item["integrity"])
                original["neo_output_id"] = item.get("neo_output_id")
            embedded.append({"file_id": file_id, "neo_output_id": item.get("neo_output_id"), "method": report.get("method")})
        except Exception as exc:
            try:
                item["integrity"] = file_integrity(path)
                if original is not None:
                    original["integrity"] = dict(item["integrity"])
            except Exception:
                pass
            failures.append(f"{item.get('filename') or file_id}: portable metadata was not embedded ({exc})")
    return {
        "schema_version": "neo.image.portable_metadata_persistence.v1",
        "neo_result_uid": str(record.get("neo_result_uid") or ""),
        "embedded_count": len(embedded),
        "files": embedded,
        "errors": failures,
        "policy": "binary_metadata_no_pixel_reencode",
    }


def _output_file_metadata(output: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Preserve provider output metadata on the saved file record without secrets."""
    metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
    safe = {k: v for k, v in metadata.items() if str(k).lower() not in {"b64_json", "base64", "api_key", "authorization"}}
    safe.setdefault("provider_output_index", index)
    if output.get("provider_id"):
        safe.setdefault("provider_id", output.get("provider_id"))
    if output.get("url"):
        safe.setdefault("provider_returned_url", True)
    if output.get("local_path"):
        safe.setdefault("provider_local_handoff", True)
    return safe



def persist_latent_outputs(latent_outputs: list[dict[str, Any]], *, result_id: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Persist Comfy latent outputs while keeping execution and storage authority separate.

    Neo keeps a byte-for-byte copy under ``neo_data`` for retention, inspection,
    and cleanup. Replay execution still requires the original provider-relative
    Comfy reference because ``LoadLatent`` cannot consume Neo-local filesystem
    paths. A copied latent without canonical Comfy coordinates is therefore
    recorded as ``neo_copy_only`` and must not unlock provider resume.

    A Neo-copy failure does not erase a valid provider reference. The artifact
    remains provider-replayable and is revalidated against Comfy before queueing,
    while the failed retention copy is surfaced as a persistence warning.
    """
    if not latent_outputs:
        return []
    clean_result_id = sanitize_path_part(result_id, fallback="image_result")
    target_dir = ROOT_DIR / "neo_data" / "outputs" / "image_latents" / clean_result_id
    target_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for index, output in enumerate(latent_outputs, start=1):
        restore_point = str(output.get("restore_point") or "final_latent").strip() or "final_latent"
        raw_filename = str(output.get("filename") or "").strip()
        raw_load_name = str(output.get("comfy_load_name") or output.get("load_latent_name") or "").strip()
        reference = None
        reference_error = ""
        if raw_filename or raw_load_name:
            try:
                reference = normalize_comfy_artifact_reference(
                    filename=raw_filename,
                    subfolder=output.get("subfolder") or "",
                    artifact_type=output.get("type") or "output",
                    load_name=raw_load_name,
                )
            except ComfyArtifactPathError as exc:
                reference_error = str(exc)

        local_source_name = raw_filename.replace("\\", "/").rsplit("/", 1)[-1] if raw_filename else ""
        local_source_name = local_source_name or f"{restore_point}_{index}.latent"
        suffix = Path(local_source_name).suffix.lower() or ".latent"
        if suffix not in {".latent", ".safetensors", ".pt", ".bin"}:
            suffix = ".latent"
        filename = sanitize_path_part(f"{restore_point}_{index}{suffix}", fallback=f"latent_{index}{suffix}")
        target = target_dir / filename
        counter = 2
        while target.exists():
            filename = sanitize_path_part(f"{restore_point}_{index}_{counter}{suffix}", fallback=f"latent_{index}_{counter}{suffix}")
            target = target_dir / filename
            counter += 1

        neo_path = ""
        neo_copy_state = "available"
        copy_error = ""
        try:
            target.write_bytes(_read_output_bytes(output, timeout=timeout))
            neo_path = _relative_to_root(target)
        except Exception as exc:  # noqa: BLE001
            neo_copy_state = "failed_to_persist"
            copy_error = str(exc)

        provider_reference = ({
            "filename": reference.filename,
            "subfolder": reference.subfolder,
            "type": reference.artifact_type,
            "load_name": reference.load_name,
        } if reference is not None else {})
        provider_owned = bool(output.get("provider_owned", True))
        provider_id = str(output.get("provider_id") or "comfyui")
        backend = str(output.get("backend") or "comfyui")
        provider_is_comfy = provider_id in {"comfyui", "comfyui_portable"} and backend == "comfyui"
        provider_resume_ready = bool(reference is not None and provider_owned and provider_is_comfy)
        if provider_resume_ready:
            state = "available"
            provider_reference_state = "captured_from_comfy_history"
        elif neo_path:
            state = "neo_copy_only"
            provider_reference_state = "provider_incompatible" if reference is not None else "missing_or_invalid"
        else:
            state = "failed_to_persist"
            provider_reference_state = "provider_incompatible" if reference is not None else "missing_or_invalid"
        artifact = {
            "artifact_id": f"latent_{index}",
            "restore_point": restore_point,
            "kind": "latent_tensor",
            "path": neo_path,
            "format": str(output.get("format") or "comfy_latent"),
            "provider_owned": provider_owned,
            "provider_id": provider_id,
            "backend": backend,
            "source_node_id": str(output.get("node_id") or ""),
            "source_filename": reference.filename if reference is not None else "",
            "source_subfolder": reference.subfolder if reference is not None else "",
            "source_type": reference.artifact_type if reference is not None else "",
            "comfy_load_name": reference.load_name if reference is not None else "",
            "provider_reference": provider_reference,
            "provider_resume_ready": provider_resume_ready,
            "provider_reference_state": provider_reference_state,
            "neo_copy_state": neo_copy_state,
            "neo_copy_role": "retention_cleanup_copy_not_loadlatent_source",
            "state": state,
        }
        if reference_error:
            artifact["provider_reference_error"] = reference_error
        if copy_error:
            artifact["neo_copy_error"] = copy_error
        artifacts.append(artifact)
    return artifacts


IMAGE_ASSET_CLEANUP_SCHEMA_VERSION = "neo.image.asset_cleanup.v1"
IMAGE_INPUT_ASSET_CONTRACT_SCHEMA_VERSION = "neo.image.input_asset_contract.v1"

# Only delete backend input-folder files that Neo created as runtime handoffs.
# User/original Comfy input files must never be touched by this post-save cleanup.
COMFY_INPUT_HANDOFF_PREFIXES: tuple[str, ...] = (
    "neo_img2img_",
    "neo_mask_",
    "neo_controlnet_",
    "controlnet_",
)

IMAGE_RESULT_DELETE_MANIFEST_SCHEMA_VERSION = "neo.image.result_delete_manifest.v1"
IMAGE_RESULT_DELETE_SCHEMA_VERSION = "neo.image.result_delete.v1"
IMAGE_RESULT_DELETE_ALLOWED_REL_ROOTS: tuple[str, ...] = (
    "neo_data/outputs/image",
    "neo_data/outputs/image_metadata",
    "neo_data/outputs/image_latents",
    "neo_data/runtime/image_jobs",
    "neo_data/inputs/image",
    "neo_data/inputs/image_masks",
    "neo_data/controlnet_maps",
    "neo_data/outputs/video/source",
)
IMAGE_RESULT_DELETE_INPUT_REL_ROOTS: tuple[str, ...] = (
    "neo_data/inputs/image",
    "neo_data/inputs/image_masks",
    "neo_data/controlnet_maps",
    "neo_data/outputs/video/source",
)


def _basename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse.urlparse(text) if text.startswith(("/api/", "http://", "https://")) else None
    path_text = parsed.path if parsed else text
    return Path(path_text.replace("\\", "/")).name


def _path_from_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("/api/", "http://", "https://")):
        return ""
    return text


def _url_from_ref(value: Any, *, role: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/api/") or text.startswith(("http://", "https://", "data:")):
        return text
    name = _basename(text)
    if not name:
        return ""
    if role in {"mask", "inpaint_mask", "outpaint_mask"} or name.startswith("mask_"):
        return f"/api/image/mask-file/{parse.quote(name)}"
    if role in {"controlnet_map", "generated_map"} or name.startswith("controlnet_"):
        return f"/api/extensions/controlnet/maps/file/{parse.quote(name)}"
    return f"/api/image/source-file/{parse.quote(name)}"


def _extension_payloads(extensions: dict[str, Any] | None) -> dict[str, Any]:
    source = extensions if isinstance(extensions, dict) else {}
    payloads = source.get("payloads") if isinstance(source.get("payloads"), dict) else {}
    # Some pre-normalized paths may pass extension blocks at the top level.
    merged = dict(payloads)
    for extension_id in ("image.controlnet", "image.ip_adapter", "image.layerdiffuse"):
        if extension_id not in merged and isinstance(source.get(extension_id), dict):
            merged[extension_id] = source.get(extension_id)
    return merged


def _iter_asset_bucket(bucket: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(bucket, dict):
        for key, value in bucket.items():
            yield str(key), value
    elif isinstance(bucket, list):
        for index, value in enumerate(bucket, start=1):
            yield str(index), value
    elif bucket not in (None, ""):
        yield "primary", bucket


def _asset_record_ref(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        ref = ""
        for key in ("path", "local_path", "url", "preview_url", "ref", "filename", "stored_filename", "map_id", "asset_id", "image_name", "workflow_source", "comfy_image_name", "comfy_name", "comfy_input_name"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                ref = candidate
                break
        backend = ""
        for key in ("comfy_image_name", "comfy_name", "comfy_input_name", "workflow_source", "image_name", "mask_name"):
            candidate = str(value.get(key) or "").strip()
            if candidate and _is_safe_comfy_input_handoff_name(candidate):
                backend = _basename(candidate)
                break
        return {
            "ref": ref,
            "filename": str(value.get("filename") or value.get("stored_filename") or value.get("map_id") or value.get("asset_id") or _basename(ref)),
            "backend_handoff_name": backend,
            "path": str(value.get("path") or value.get("local_path") or ""),
            "url": str(value.get("url") or value.get("preview_url") or ""),
        }
    ref = str(value or "").strip()
    return {"ref": ref, "filename": _basename(ref), "backend_handoff_name": _basename(ref) if _is_safe_comfy_input_handoff_name(ref) else "", "path": "", "url": ""}


def _storage_for_asset(path: str, url: str, role: str) -> str:
    joined = f"{path} {url}".replace("\\", "/")
    if "controlnet_maps" in joined or role == "controlnet_map":
        return "neo_data/controlnet_maps"
    if "image_masks" in joined or role in {"mask", "inpaint_mask", "outpaint_mask"}:
        return "neo_data/inputs/image_masks"
    if "outputs/video/source" in joined or role.startswith("video_"):
        return "neo_data/outputs/video/source"
    return "neo_data/inputs/image"


def _dedupe_asset_records(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in assets:
        key = (
            str(item.get("role") or ""),
            _basename(item.get("filename") or item.get("path") or item.get("url") or ""),
            str(item.get("path") or ""),
            str(item.get("url") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _make_input_asset_record(
    *,
    asset_id: str,
    role: str,
    label: str,
    filename: str = "",
    path: str = "",
    url: str = "",
    backend_handoff_name: str = "",
    extension_id: str = "",
    unit: str = "",
    owned_by_result: bool = False,
) -> dict[str, Any] | None:
    path = str(path or "").strip()
    url = str(url or "").strip()
    backend_handoff_name = str(backend_handoff_name or "").strip()
    filename = str(filename or "").strip() or _basename(path) or _basename(url) or _basename(backend_handoff_name)
    if not (path or url or backend_handoff_name or filename):
        return None
    url = url or _url_from_ref(path or filename or backend_handoff_name, role=role)
    item: dict[str, Any] = {
        "asset_id": sanitize_path_part(asset_id, fallback="asset"),
        "role": role,
        "label": label,
        "filename": filename,
        "path": path,
        "url": url,
        "storage": _storage_for_asset(path, url, role),
        "source_surface": "image",
        "owned_by_result": bool(owned_by_result),
        "delete_policy": "cascade_unique_only" if owned_by_result else "shared_input_scan_before_delete",
    }
    if backend_handoff_name:
        item["backend_handoff_name"] = _basename(backend_handoff_name)
        item["backend_handoff_cleanup_policy"] = "delete_after_neo_persistence" if _is_safe_comfy_input_handoff_name(backend_handoff_name) else "skip_unowned_backend_input"
    if extension_id:
        item["extension_id"] = extension_id
    if unit:
        item["unit"] = unit
    return item


def collect_input_asset_records(params: dict[str, Any], *, extensions: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return Neo-owned source/reference/mask/control assets used by a generation.

    Output Inspector uses this list for thumbnails and future cascade-delete
    previews.  The list is deliberately Neo-owned: source masters, masks, IP
    Adapter refs, ControlNet generated maps, and generated maps live under
    ``neo_data``. Backend Comfy input names are stored only as disposable handoff
    diagnostics.
    """
    params = params if isinstance(params, dict) else {}
    assets: list[dict[str, Any]] = []

    def add(asset_id: str, role: str, label: str, path_key: str | tuple[str, ...], url_key: str, name_key: str, *, backend_key: str = "") -> None:
        path_keys = (path_key,) if isinstance(path_key, str) else path_key
        path = ""
        for key in path_keys:
            path = str(params.get(key) or "").strip()
            if path:
                break
        url = str(params.get(url_key) or "").strip()
        filename = str(params.get(name_key) or (Path(path).name if path else "") or (Path(parse.urlparse(url).path).name if url else "")).strip()
        backend_handoff = str(params.get(backend_key) or "").strip() if backend_key else ""
        item = _make_input_asset_record(
            asset_id=asset_id,
            role=role,
            label=label,
            filename=filename,
            path=path,
            url=url,
            backend_handoff_name=backend_handoff,
            owned_by_result=False,
        )
        if item:
            assets.append(item)

    add("source_image_1", "source", "Source image 1", ("source_image_path", "source_image", "init_image"), "source_image_url", "source_image_name", backend_key="comfy_source_image_name")
    add("source_image_2", "reference", "Source image 2", ("source_image_2_path", "source_image_2", "source_image__2", "reference_image_2"), "source_image_2_url", "source_image_2_name", backend_key="comfy_source_image_2_name")
    add("source_image_3", "reference", "Source image 3", ("source_image_3_path", "source_image_3", "source_image__3", "composition_image", "reference_image_3"), "source_image_3_url", "source_image_3_name", backend_key="comfy_source_image_3_name")
    for lane in range(4, 11):
        add(
            f"source_image_{lane}",
            "reference",
            f"Source image {lane}",
            (f"source_image_{lane}_path", f"source_image_{lane}", f"reference_image_{lane}"),
            f"source_image_{lane}_url",
            f"source_image_{lane}_name",
            backend_key=f"comfy_source_image_{lane}_name",
        )
    add("mask_image", "mask", "Mask", ("mask_image_path", "mask_image", "inpaint_mask", "mask"), "mask_image_url", "mask_image_name", backend_key="comfy_mask_image_name")
    add("outpaint_canvas", "outpaint_canvas", "Outpaint canvas", ("outpaint_canvas_image_path", "outpaint_canvas_image", "outpaint_padded_image", "padded_image"), "outpaint_canvas_image_url", "outpaint_canvas_image_name", backend_key="comfy_outpaint_canvas_image_name")
    add("outpaint_mask", "outpaint_mask", "Outpaint mask", ("outpaint_mask_image_path", "outpaint_mask_image", "outpaint_mask", "padded_mask"), "outpaint_mask_image_url", "outpaint_mask_image_name", backend_key="comfy_outpaint_mask_image_name")

    payloads = _extension_payloads(extensions)

    controlnet = payloads.get("image.controlnet") if isinstance(payloads.get("image.controlnet"), dict) else {}
    controlnet_assets = controlnet.get("assets") if isinstance(controlnet.get("assets"), dict) else {}
    for uid, value in _iter_asset_bucket(controlnet_assets.get("generated_maps")):
        ref = _asset_record_ref(value)
        item = _make_input_asset_record(
            asset_id=f"controlnet_map_{uid}",
            role="controlnet_map",
            label=f"ControlNet map · {uid}",
            filename=ref.get("filename", ""),
            path=ref.get("path") or _path_from_ref(ref.get("ref")),
            url=ref.get("url") or _url_from_ref(ref.get("ref"), role="controlnet_map"),
            backend_handoff_name=ref.get("backend_handoff_name", ""),
            extension_id="image.controlnet",
            unit=uid,
            owned_by_result=True,
        )
        if item:
            assets.append(item)
    for uid, value in _iter_asset_bucket(controlnet_assets.get("control_images")):
        ref = _asset_record_ref(value)
        item = _make_input_asset_record(
            asset_id=f"controlnet_image_{uid}",
            role="controlnet_image",
            label=f"Control image · {uid}",
            filename=ref.get("filename", ""),
            path=ref.get("path") or _path_from_ref(ref.get("ref")),
            url=ref.get("url") or _url_from_ref(ref.get("ref"), role="controlnet_image"),
            backend_handoff_name=ref.get("backend_handoff_name", ""),
            extension_id="image.controlnet",
            unit=uid,
            owned_by_result=False,
        )
        if item:
            assets.append(item)

    ip_adapter = payloads.get("image.ip_adapter") if isinstance(payloads.get("image.ip_adapter"), dict) else {}
    ip_assets = ip_adapter.get("assets") if isinstance(ip_adapter.get("assets"), dict) else {}
    for uid, bucket in _iter_asset_bucket(ip_assets.get("reference_images")):
        values = bucket if isinstance(bucket, list) else [bucket]
        for index, value in enumerate(values, start=1):
            ref = _asset_record_ref(value)
            suffix = f"_{index}" if len(values) > 1 else ""
            item = _make_input_asset_record(
                asset_id=f"ip_adapter_ref_{uid}{suffix}",
                role="ip_adapter_reference",
                label=f"IP Adapter ref · {uid}{f' #{index}' if len(values) > 1 else ''}",
                filename=ref.get("filename", ""),
                path=ref.get("path") or _path_from_ref(ref.get("ref")),
                url=ref.get("url") or _url_from_ref(ref.get("ref"), role="ip_adapter_reference"),
                backend_handoff_name=ref.get("backend_handoff_name", ""),
                extension_id="image.ip_adapter",
                unit=uid,
                owned_by_result=False,
            )
            if item:
                assets.append(item)

    layerdiffuse = payloads.get("image.layerdiffuse") if isinstance(payloads.get("image.layerdiffuse"), dict) else {}
    layer_assets = layerdiffuse.get("assets") if isinstance(layerdiffuse.get("assets"), dict) else {}
    for index, handoff in enumerate(layer_assets.get("comfy_input_handoffs") if isinstance(layer_assets.get("comfy_input_handoffs"), list) else [], start=1):
        if not isinstance(handoff, dict):
            continue
        source = str(handoff.get("source_path") or handoff.get("source") or "").strip()
        field = str(handoff.get("field") or f"slot_{index}").strip()
        item = _make_input_asset_record(
            asset_id=f"layerdiffuse_{field}_{index}",
            role="layerdiffuse_slot",
            label=f"LayerDiffuse · {field}",
            filename=_basename(source),
            path=_path_from_ref(source),
            url=_url_from_ref(source, role="layerdiffuse_slot"),
            backend_handoff_name=str(handoff.get("comfy_input_name") or ""),
            extension_id="image.layerdiffuse",
            unit=field,
            owned_by_result=False,
        )
        if item:
            assets.append(item)

    return _dedupe_asset_records(assets)


def build_input_asset_contract(input_assets: list[dict[str, Any]] | None) -> dict[str, Any]:
    assets = input_assets if isinstance(input_assets, list) else []
    return {
        "schema_version": IMAGE_INPUT_ASSET_CONTRACT_SCHEMA_VERSION,
        "storage_authority": "neo_data",
        "backend_handoff_authority": "disposable_after_persistence",
        "asset_count": len(assets),
        "roles": sorted({str(item.get("role") or "") for item in assets if isinstance(item, dict) and item.get("role")}),
        "backend_handoff_names": sorted({str(item.get("backend_handoff_name") or "") for item in assets if isinstance(item, dict) and item.get("backend_handoff_name")}),
        "rules": [
            "Neo-owned source/control/reference/mask assets are stored under neo_data and referenced by output metadata.",
            "Comfy input/output copies are runtime handoffs only and may be deleted after Neo persistence.",
            "Future cascade delete must scan references before deleting shared Neo-owned input assets.",
        ],
    }


def _is_safe_comfy_input_handoff_name(value: Any) -> bool:
    name = _basename(value)
    if not name:
        return False
    if "/" in name or "\\" in name or name in {".", ".."}:
        return False
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        return False
    return name.startswith(COMFY_INPUT_HANDOFF_PREFIXES)


def _is_cache_stable_comfy_input_handoff_name(value: Any) -> bool:
    """Return True for Neo content-addressed Comfy input cache entries.

    These files intentionally survive per-output cleanup so unchanged source
    images keep the same LoadImage identity between runs. They remain Neo-owned
    and can be removed by an explicit backend/input cache cleanup in a future
    maintenance pass.
    """
    name = _basename(value)
    if not _is_safe_comfy_input_handoff_name(name):
        return False
    return name.startswith(("neo_img2img_cache_", "neo_mask_cache_"))


def _iter_comfy_handoff_names(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").lower()
            if key_text in {"comfy_source_image_name", "comfy_mask_image_name", "comfy_outpaint_canvas_image_name", "comfy_outpaint_mask_image_name", "comfy_input_name", "comfy_image_name", "comfy_name", "workflow_source", "image_name", "mask_name"} or (key_text.startswith("comfy_source_image_") and key_text.endswith("_name")):
                if _is_safe_comfy_input_handoff_name(item):
                    yield _basename(item)
            if isinstance(item, (dict, list)):
                yield from _iter_comfy_handoff_names(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_comfy_handoff_names(item, depth=depth + 1)
    elif _is_safe_comfy_input_handoff_name(value):
        yield _basename(value)


def cleanup_backend_input_handoffs(params: dict[str, Any], *, context: dict[str, Any], extensions: dict[str, Any] | None = None, enabled: bool = True) -> dict[str, Any]:
    """Delete temporary Comfy input-folder handoff files after Neo persistence.

    Source/reference/mask masters stay under Neo_Data. Comfy still needs input
    handoffs for LoadImage/LoadImageMask while the job is running, so cleanup only
    touches recorded Neo-created names in Comfy's input folder after the output
    and metadata are saved.
    """
    if not enabled:
        return {"enabled": False, "deleted": [], "skipped": ["Cleanup disabled in output settings."], "errors": [], "policy": "neo_created_backend_input_handoffs_only"}
    backend_root = str(context.get("backend_output_root") or "").strip()
    if not backend_root:
        return {"enabled": True, "deleted": [], "skipped": ["Backend root is not configured; input cleanup skipped."], "errors": [], "policy": "neo_created_backend_input_handoffs_only"}
    output_root = Path(backend_root).expanduser().resolve()
    comfy_root = output_root.parent if output_root.name.lower() in {"output", "temp"} else output_root.parent
    input_root = (comfy_root / "input").resolve()
    deleted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    names = sorted(set([*_iter_comfy_handoff_names(params or {}), *_iter_comfy_handoff_names(extensions or {})]))
    for raw_name in names:
        safe_name = _basename(raw_name)
        if not _is_safe_comfy_input_handoff_name(safe_name):
            skipped.append(safe_name or "empty")
            continue
        if _is_cache_stable_comfy_input_handoff_name(safe_name):
            skipped.append(f"{safe_name} (retained for Comfy cache stability)")
            continue
        target = (input_root / safe_name).resolve()
        try:
            if input_root not in target.parents:
                skipped.append(safe_name)
                continue
            if target.exists() and target.is_file():
                target.unlink()
                deleted.append(_relative_to_root(target))
            else:
                skipped.append(safe_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{safe_name}: {exc}")
    return {
        "enabled": True,
        "backend_input_root": str(input_root),
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "policy": "neo_created_backend_input_handoffs_only",
        "safe_prefixes": list(COMFY_INPUT_HANDOFF_PREFIXES),
        "cache_retention_prefixes": ["neo_img2img_cache_", "neo_mask_cache_"],
    }


def build_asset_cleanup_report(*, backend_cleanup: dict[str, Any] | None, backend_input_cleanup: dict[str, Any] | None, input_assets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    output_cleanup = backend_cleanup if isinstance(backend_cleanup, dict) else {}
    input_cleanup = backend_input_cleanup if isinstance(backend_input_cleanup, dict) else {}
    assets = input_assets if isinstance(input_assets, list) else []
    skipped = [
        *[str(item) for item in output_cleanup.get("skipped", []) if str(item or "").strip()],
        *[str(item) for item in input_cleanup.get("skipped", []) if str(item or "").strip()],
    ]
    errors = [
        *[str(item) for item in output_cleanup.get("errors", []) if str(item or "").strip()],
        *[str(item) for item in input_cleanup.get("errors", []) if str(item or "").strip()],
    ]
    return {
        "schema_version": IMAGE_ASSET_CLEANUP_SCHEMA_VERSION,
        "storage_authority": "neo_data",
        "backend_policy": "delete_backend_duplicates_after_neo_persistence",
        "backend_outputs_deleted": list(output_cleanup.get("deleted", []) if isinstance(output_cleanup.get("deleted"), list) else []),
        "backend_inputs_deleted": list(input_cleanup.get("deleted", []) if isinstance(input_cleanup.get("deleted"), list) else []),
        "skipped": skipped,
        "errors": errors,
        "input_asset_count": len(assets),
        "neo_owned_asset_roles": sorted({str(item.get("role") or "") for item in assets if isinstance(item, dict) and item.get("role")}),
        "notes": [
            "Final outputs and reusable source/control/reference assets remain Neo-owned under neo_data.",
            "Backend-native Comfy files listed here were duplicate handoffs only.",
        ],
    }

def load_output_record(result_id: str, *, mode_or_category: str | None = None) -> dict[str, Any]:
    """Load a persisted output sidecar by result id."""
    clean_result_id = sanitize_path_part(result_id, fallback="output")
    search_categories = [mode_or_category] if mode_or_category else ["generate", "img2img", "inpaint", "outpaint", "upscale", "edit", "batch", "uncategorized"]
    checked: list[Path] = []
    extra_dirs = [metadata_category_dir(load_image_output_settings())]
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata")
    if metadata_root.exists():
        extra_dirs.extend([item for item in metadata_root.iterdir() if item.is_dir()])
    for category in search_categories:
        metadata_dirs = [get_image_output_paths(category, create=False).metadata_dir, *extra_dirs]
        for metadata_dir in metadata_dirs:
            if metadata_dir in checked:
                continue
            checked.append(metadata_dir)
            path = metadata_dir / f"{clean_result_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Unknown image output result: {result_id}")


def resolve_output_file(result_id: str, file_id: str) -> Path:
    """Resolve a persisted image file from its sidecar record."""
    record = load_output_record(result_id)
    for file_record in record.get("outputs", {}).get("files", []):
        if file_record.get("file_id") == file_id:
            path = (ROOT_DIR / str(file_record.get("path") or "")).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Persisted output file missing: {file_id}")
            neo_output_root = (ROOT_DIR / "neo_data" / "outputs" / "image").resolve()
            if neo_output_root not in path.parents:
                raise ValueError("Resolved output path is outside Neo_Data image outputs.")
            return path
    raise FileNotFoundError(f"Unknown output file id: {file_id}")


_IMAGE_RESULT_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any] | None]] = {}


def _cached_output_summary(record_path: Path) -> dict[str, Any] | None:
    """Read one result sidecar only when its mtime/size changed.

    Image metadata can be very large because replay/runtime snapshots are stored
    with each result. The Results browser must therefore never deserialize the
    entire history just to render the first page.
    """
    try:
        stat = record_path.stat()
    except OSError:
        return None
    key = str(record_path.resolve())
    cached = _IMAGE_RESULT_SUMMARY_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    summary: dict[str, Any] | None = None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        candidate = _summarize_output_record(record, record_path)
        if not candidate.get("is_missing_files"):
            summary = candidate
    except Exception:
        summary = None
    _IMAGE_RESULT_SUMMARY_CACHE[key] = (stat.st_mtime_ns, stat.st_size, summary)
    return summary


def _image_result_metadata_paths(*, selected_category: str = "", newest_first: bool = True) -> tuple[list[Path], int, list[str]]:
    """Return cheaply sorted metadata candidates without opening sidecar JSON.

    Custom save categories own their metadata folder. For a selected category we
    prefer that folder directly, which prevents an empty/stale category filter
    from forcing a read of every historical sidecar. `all` still enumerates all
    paths, but JSON is opened lazily by `list_image_results`.
    """
    metadata_root = ROOT_DIR / "neo_data" / "outputs" / "image_metadata"
    if not metadata_root.exists():
        return [], 0, []

    all_paths = [item for item in metadata_root.rglob("*.json") if item.is_file()]
    folder_names = sorted({item.parent.name for item in all_paths}, key=str.casefold)
    if selected_category:
        wanted_slug = category_slug(selected_category).casefold()
        # Workflow filters (generate/img2img/inpaint/...) must inspect every save
        # category because modern Neo stores metadata by user save category while
        # preserving the workflow in `subtab`. User-created save categories, on
        # the other hand, can use their own folder directly for a very fast path.
        if selected_category.strip().casefold() in {item.casefold() for item in IMAGE_OUTPUT_CATEGORIES}:
            candidates = all_paths
        else:
            candidates = [item for item in all_paths if item.parent.name.casefold() == wanted_slug]
    else:
        candidates = all_paths

    def sort_key(path: Path) -> tuple[int, str]:
        try:
            return (path.stat().st_mtime_ns, path.name.casefold())
        except OSError:
            return (0, path.name.casefold())

    candidates.sort(key=sort_key, reverse=newest_first)
    return candidates, len(all_paths), folder_names


def list_image_results(*, category: str | None = None, limit: int = 50, offset: int = 0, sort: str = "newest") -> dict[str, Any]:
    """List persisted Image results using lazy paged sidecar parsing.

    Previous pagination sliced *after* deserializing every metadata file. Large
    libraries could therefore read hundreds of MB/GB before showing page one.
    This implementation enumerates/stat-sorts cheap file paths first and opens
    sidecars only until it has enough valid records for the requested page.
    Parsed summaries are cached by file mtime+size for subsequent pages.
    """
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    selected_category = str(category or "").strip()
    if selected_category.casefold() in {"", "all", "any"}:
        selected_category = ""
    newest_first = str(sort or "newest").lower() not in {"oldest", "old_to_new", "asc"}
    candidates, all_candidate_count, folder_names = _image_result_metadata_paths(
        selected_category=selected_category,
        newest_first=newest_first,
    )

    # Small libraries keep exact-total behavior for compatibility. Large
    # libraries switch to lazy page parsing so page one does not deserialize the
    # entire history.
    exact_total_threshold = 250
    needed = len(candidates) if len(candidates) <= exact_total_threshold else offset + limit + 1
    valid: list[dict[str, Any]] = []
    scanned = 0
    for record_path in candidates:
        scanned += 1
        summary = _cached_output_summary(record_path)
        if summary is None:
            continue
        # Folder routing is authoritative for modern save categories. Retain the
        # logical match check as a guard for canonical/legacy records.
        if selected_category:
            wanted = category_slug(selected_category).casefold()
            logical = {
                category_slug(summary.get("subtab") or "").casefold(),
                category_slug(summary.get("save_category") or "").casefold(),
                record_path.parent.name.casefold(),
            }
            if wanted not in logical:
                continue
        valid.append(summary)
        if len(valid) >= needed:
            break

    exhausted = scanned >= len(candidates)
    page = valid[offset: offset + limit]
    has_more = len(valid) > offset + limit or not exhausted
    next_offset = offset + len(page)
    # Exact total is known only when every candidate has been inspected. Do not
    # pretend an estimate is exact; the frontend renders an honest "more
    # available" label until the final page is reached.
    total_known = exhausted
    total = len(valid) if total_known else next_offset
    return {
        "schema_version": "neo.image.results_api.v1",
        "count": len(page),
        "total": total,
        "total_known": total_known,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "results": page,
        "source": "neo_data/outputs/image_metadata",
        "category": selected_category or "all",
        "sort": "newest" if newest_first else "oldest",
        "metadata_candidates": len(candidates),
        "all_metadata_candidates": all_candidate_count,
        "metadata_sidecars_scanned_this_request": scanned,
        "available_metadata_folders": folder_names,
        "pagination_policy": "lazy_sidecar_parse_with_mtime_size_summary_cache",
    }


def get_image_result(result_id: str, *, category: str | None = None) -> dict[str, Any]:
    """Return a full persisted Image output record."""
    record = load_output_record(result_id, mode_or_category=category)
    return {
        "schema_version": "neo.image.result_detail.v1",
        "result": record,
        "reuse": build_image_result_reuse_payload(record),
    }


def get_image_result_metadata(result_id: str, *, category: str | None = None) -> dict[str, Any]:
    """Return only the metadata sidecar payload for a persisted Image result."""
    record = load_output_record(result_id, mode_or_category=category)
    return {
        "schema_version": "neo.image.result_metadata.v1",
        "metadata": record,
    }




def _delete_allowed_roots() -> tuple[Path, ...]:
    return tuple((ROOT_DIR / rel).resolve() for rel in IMAGE_RESULT_DELETE_ALLOWED_REL_ROOTS)


def _delete_input_roots() -> tuple[Path, ...]:
    return tuple((ROOT_DIR / rel).resolve() for rel in IMAGE_RESULT_DELETE_INPUT_REL_ROOTS)


def _path_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
        return resolved == base or base in resolved.parents
    except Exception:
        return False


def _matching_allowed_root(path: Path, roots: Iterable[Path] | None = None) -> Path | None:
    for root in roots or _delete_allowed_roots():
        if _path_within(path, root):
            return root
    return None


def _resolve_neo_relative_path(raw_path: Any) -> Path | None:
    text = str(raw_path or "").strip()
    if not text or text.startswith(("http://", "https://", "data:", "/api/")):
        return None
    path = Path(text)
    if not path.is_absolute():
        path = ROOT_DIR / text
    return path.resolve()


def _manifest_path_entry(path: Path, *, kind: str, reason: str = "", source: str = "", label: str = "", role: str = "", asset_id: str = "", references: list[str] | None = None) -> dict[str, Any]:
    exists = path.exists()
    is_file = path.is_file()
    return {
        "kind": kind,
        "path": _relative_to_root(path),
        "filename": path.name,
        "label": label or path.name,
        "role": role,
        "asset_id": asset_id,
        "source": source,
        "exists": bool(exists),
        "is_file": bool(is_file),
        "size_bytes": _safe_file_size(path) if exists and is_file else 0,
        "reason": reason,
        "referenced_by_result_ids": references or [],
    }


def _append_manifest_entry(manifest: dict[str, Any], bucket: str, path: Path | None, *, kind: str, reason: str = "", source: str = "", label: str = "", role: str = "", asset_id: str = "", roots: Iterable[Path] | None = None, references: list[str] | None = None, require_file: bool = False) -> None:
    if path is None:
        manifest.setdefault("skipped", []).append({"kind": kind, "path": "", "label": label, "role": role, "asset_id": asset_id, "reason": reason or "No Neo-owned path recorded."})
        return
    root = _matching_allowed_root(path, roots)
    if root is None:
        manifest.setdefault("skipped", []).append(_manifest_path_entry(path, kind=kind, reason="Path is outside the Image result delete allowlist.", source=source, label=label, role=role, asset_id=asset_id, references=references))
        return
    if require_file and path.exists() and not path.is_file():
        manifest.setdefault("skipped", []).append(_manifest_path_entry(path, kind=kind, reason=reason or "Candidate is not a file.", source=source, label=label, role=role, asset_id=asset_id, references=references))
        return
    entry = _manifest_path_entry(path, kind=kind, reason=reason, source=source, label=label, role=role, asset_id=asset_id, references=references)
    existing = {item.get("path") for item in manifest.setdefault(bucket, []) if isinstance(item, dict)}
    if entry["path"] not in existing:
        manifest[bucket].append(entry)


def _metadata_paths_for_result(result_id: str, *, category: str | None = None) -> list[Path]:
    clean_result_id = sanitize_path_part(result_id, fallback="output")
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve()
    candidates: list[Path] = []
    if category and str(category).strip().lower() not in {"all", "any"}:
        candidates.append(get_image_output_paths(category, create=False).metadata_dir / f"{clean_result_id}.json")
        candidates.append(metadata_category_dir({"selected_category": category}) / f"{clean_result_id}.json")
    if metadata_root.exists():
        candidates.extend(metadata_root.rglob(f"{clean_result_id}.json"))
    # Canonical fallback dirs, useful when metadata_root does not exist yet in tests.
    for item in ["generate", "img2img", "inpaint", "outpaint", "upscale", "edit", "batch", "uncategorized"]:
        candidates.append(get_image_output_paths(item, create=False).metadata_dir / f"{clean_result_id}.json")
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and _matching_allowed_root(resolved):
            out.append(resolved)
    return out


def _iter_sidecar_records() -> Iterable[tuple[Path, dict[str, Any]]]:
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve()
    if not metadata_root.exists():
        return
    for record_path in metadata_root.rglob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(record, dict):
            yield record_path.resolve(), record


def _asset_path_from_record(asset: dict[str, Any]) -> Path | None:
    if not isinstance(asset, dict):
        return None
    raw_path = str(asset.get("path") or asset.get("local_path") or "").strip()
    resolved = _resolve_neo_relative_path(raw_path)
    if resolved:
        return resolved
    storage = str(asset.get("storage") or "").strip().replace("\\", "/").strip("/")
    filename = str(asset.get("filename") or asset.get("stored_filename") or asset.get("asset_id") or "").strip()
    url = str(asset.get("url") or asset.get("preview_url") or "").strip()
    if not filename and url:
        parsed = parse.urlparse(url)
        filename = Path(parsed.path).name
    if storage and filename:
        return (ROOT_DIR / storage / Path(filename).name).resolve()
    if url.startswith("/api/image/source-file/") and filename:
        return (ROOT_DIR / "neo_data" / "inputs" / "image" / Path(filename).name).resolve()
    if url.startswith("/api/image/mask-file/") and filename:
        return (ROOT_DIR / "neo_data" / "inputs" / "image_masks" / Path(filename).name).resolve()
    if url.startswith("/api/extensions/controlnet/maps/file/") and filename:
        return (ROOT_DIR / "neo_data" / "controlnet_maps" / Path(filename).name).resolve()
    if url.startswith("/api/video/source-file/") and filename:
        return (ROOT_DIR / "neo_data" / "outputs" / "video" / "source" / Path(filename).name).resolve()
    return None


def _collect_other_input_asset_references(current_result_id: str) -> dict[str, list[str]]:
    current = sanitize_path_part(current_result_id, fallback="")
    references: dict[str, list[str]] = {}
    for _record_path, record in _iter_sidecar_records() or []:
        result_id = sanitize_path_part(str(record.get("result_id") or ""), fallback="")
        if result_id and result_id == current:
            continue
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        assets = source.get("input_assets") if isinstance(source.get("input_assets"), list) else []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            path = _asset_path_from_record(asset)
            if path is None or _matching_allowed_root(path, _delete_input_roots()) is None:
                continue
            rel = _relative_to_root(path)
            references.setdefault(rel, [])
            if result_id and result_id not in references[rel]:
                references[rel].append(result_id)
    return references


def _record_latent_paths(record: dict[str, Any], result_id: str) -> list[Path]:
    paths: list[Path] = []
    replay = record.get("replay") if isinstance(record.get("replay"), dict) else {}
    latent_capture = replay.get("latent_capture") if isinstance(replay.get("latent_capture"), dict) else {}
    persistence = record.get("persistence") if isinstance(record.get("persistence"), dict) else {}
    groups = [
        latent_capture.get("artifacts") if isinstance(latent_capture.get("artifacts"), list) else [],
        persistence.get("latent_artifacts") if isinstance(persistence.get("latent_artifacts"), list) else [],
    ]
    for artifact in [item for group in groups for item in group]:
        if not isinstance(artifact, dict):
            continue
        raw = str(artifact.get("path") or "").strip()
        path = _resolve_neo_relative_path(raw)
        if path and _matching_allowed_root(path):
            paths.append(path)
    latent_dir = (ROOT_DIR / "neo_data" / "outputs" / "image_latents" / sanitize_path_part(result_id, fallback="image_result")).resolve()
    if latent_dir.exists() and _matching_allowed_root(latent_dir):
        paths.extend([item.resolve() for item in latent_dir.rglob("*") if item.is_file()])
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def build_image_result_delete_preview(result_id: str, *, category: str | None = None) -> dict[str, Any]:
    """Build a read-only cascade-delete manifest for one Image result.

    The manifest is intentionally conservative: shared input assets are listed as
    skipped/shared, and only Neo-owned allowlisted paths are eligible.
    """
    clean_result_id = sanitize_path_part(result_id, fallback="output")
    record = load_output_record(clean_result_id, mode_or_category=category)
    manifest: dict[str, Any] = {
        "schema_version": IMAGE_RESULT_DELETE_MANIFEST_SCHEMA_VERSION,
        "ok": True,
        "result_id": clean_result_id,
        "category": category or record.get("subtab") or "all",
        "storage_authority": "neo_data",
        "delete_policy": "output_only_or_full_unique_cascade",
        "allowed_roots": list(IMAGE_RESULT_DELETE_ALLOWED_REL_ROOTS),
        "output_files": [],
        "metadata_files": [],
        "latent_files": [],
        "job_context_files": [],
        "input_assets_unique": [],
        "input_assets_shared": [],
        "skipped": [],
        "errors": [],
    }
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    for file_record in outputs.get("files") if isinstance(outputs.get("files"), list) else []:
        if not isinstance(file_record, dict):
            continue
        path = _resolve_neo_relative_path(file_record.get("path"))
        _append_manifest_entry(manifest, "output_files", path, kind="output_file", source="outputs.files", label=str(file_record.get("filename") or "Output file"), require_file=True)

    for metadata_path in _metadata_paths_for_result(clean_result_id, category=category):
        _append_manifest_entry(manifest, "metadata_files", metadata_path, kind="metadata_file", source="image_metadata", label=metadata_path.name, require_file=True)

    for latent_path in _record_latent_paths(record, clean_result_id):
        _append_manifest_entry(manifest, "latent_files", latent_path, kind="latent_file", source="replay.latent_capture", label=latent_path.name, require_file=True)

    job = record.get("job") if isinstance(record.get("job"), dict) else {}
    job_id = sanitize_path_part(str(job.get("job_id") or ""), fallback="")
    if job_id:
        job_path = (ROOT_DIR / "neo_data" / "runtime" / "image_jobs" / f"{job_id}.json").resolve()
        if job_path.exists():
            _append_manifest_entry(manifest, "job_context_files", job_path, kind="job_context_file", source="neo_data/runtime/image_jobs", label=job_path.name, require_file=True)

    references = _collect_other_input_asset_references(clean_result_id)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    for asset in source.get("input_assets") if isinstance(source.get("input_assets"), list) else []:
        if not isinstance(asset, dict):
            continue
        path = _asset_path_from_record(asset)
        label = str(asset.get("label") or asset.get("filename") or asset.get("asset_id") or "Input asset")
        role = str(asset.get("role") or "")
        asset_id = str(asset.get("asset_id") or "")
        if path is None:
            _append_manifest_entry(manifest, "input_assets_unique", None, kind="input_asset", source="source.input_assets", label=label, role=role, asset_id=asset_id, reason="Input asset has no Neo-owned path/url that can be resolved.")
            continue
        rel = _relative_to_root(path)
        shared_refs = references.get(rel, [])
        if shared_refs:
            _append_manifest_entry(manifest, "input_assets_shared", path, kind="input_asset", source="source.input_assets", label=label, role=role, asset_id=asset_id, references=shared_refs, reason="Input asset is referenced by another Image result.", roots=_delete_input_roots(), require_file=True)
        else:
            _append_manifest_entry(manifest, "input_assets_unique", path, kind="input_asset", source="source.input_assets", label=label, role=role, asset_id=asset_id, reason="Unique to this result by current metadata scan.", roots=_delete_input_roots(), require_file=True)

    full_count = sum(len(manifest[key]) for key in ("output_files", "metadata_files", "latent_files", "job_context_files", "input_assets_unique"))
    output_only_count = len(manifest["output_files"]) + len(manifest["metadata_files"])
    manifest["summary"] = {
        "output_only_delete_count": output_only_count,
        "full_delete_count": full_count,
        "output_files": len(manifest["output_files"]),
        "metadata_files": len(manifest["metadata_files"]),
        "latent_files": len(manifest["latent_files"]),
        "job_context_files": len(manifest["job_context_files"]),
        "unique_input_assets": len(manifest["input_assets_unique"]),
        "shared_input_assets": len(manifest["input_assets_shared"]),
        "skipped": len(manifest["skipped"]),
        "errors": len(manifest["errors"]),
    }
    manifest["actions"] = {
        "output_only": "Delete generated output files and metadata sidecars only.",
        "full": "Delete output files, metadata, latent restore files, job context, and unique linked Neo-owned assets. Shared assets are skipped.",
    }
    return manifest


def _delete_manifest_file(entry: dict[str, Any], *, deleted: list[str], skipped: list[str], errors: list[str]) -> None:
    raw_path = str(entry.get("path") or "").strip()
    if not raw_path:
        skipped.append(str(entry.get("label") or "missing path"))
        return
    path = _resolve_neo_relative_path(raw_path)
    if path is None:
        skipped.append(raw_path)
        return
    if _matching_allowed_root(path) is None:
        skipped.append(raw_path)
        return
    try:
        if not path.exists():
            skipped.append(raw_path)
            return
        if path.is_dir():
            shutil.rmtree(path)
            deleted.append(raw_path)
            return
        if path.is_file():
            path.unlink()
            deleted.append(raw_path)
            return
        skipped.append(raw_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{raw_path}: {exc}")


def delete_image_result(result_id: str, *, category: str | None = None, cascade: str | None = None) -> dict[str, Any]:
    """Delete one persisted Image result through a safe manifest.

    Default behavior remains output-only for backward compatibility. Passing
    ``cascade=full`` also removes unique linked Neo-owned input assets, latent
    restore files, and persisted job context files. Shared inputs are never
    deleted by this endpoint.
    """
    clean_result_id = sanitize_path_part(result_id, fallback="output")
    mode = str(cascade or "output_only").strip().lower().replace("-", "_")
    if mode not in {"output_only", "output", "metadata", "full", "cascade", "cascade_full"}:
        mode = "output_only"
    full = mode in {"full", "cascade", "cascade_full"}
    manifest = build_image_result_delete_preview(clean_result_id, category=category)
    buckets = ["output_files", "metadata_files"]
    if full:
        buckets.extend(["latent_files", "job_context_files", "input_assets_unique"])
    deleted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for bucket in buckets:
        for entry in manifest.get(bucket) if isinstance(manifest.get(bucket), list) else []:
            if isinstance(entry, dict):
                _delete_manifest_file(entry, deleted=deleted, skipped=skipped, errors=errors)
    if full:
        latent_dir = (ROOT_DIR / "neo_data" / "outputs" / "image_latents" / clean_result_id).resolve()
        try:
            if latent_dir.exists() and latent_dir.is_dir() and _matching_allowed_root(latent_dir) and not any(latent_dir.iterdir()):
                latent_dir.rmdir()
        except Exception:
            pass
    return {
        "schema_version": IMAGE_RESULT_DELETE_SCHEMA_VERSION,
        "ok": not errors,
        "result_id": clean_result_id,
        "cascade": "full" if full else "output_only",
        "deleted": deleted,
        "deleted_files": [item for item in deleted if "/outputs/image/" in f"/{item}"],
        "deleted_metadata": [item for item in deleted if "/outputs/image_metadata/" in f"/{item}"],
        "deleted_latents": [item for item in deleted if "/outputs/image_latents/" in f"/{item}"],
        "deleted_job_contexts": [item for item in deleted if "/runtime/image_jobs/" in f"/{item}"],
        "deleted_input_assets": [item for item in deleted if any(root in item for root in IMAGE_RESULT_DELETE_INPUT_REL_ROOTS)],
        "shared_input_assets_skipped": manifest.get("input_assets_shared", []),
        "skipped": skipped + [str(item.get("path") or item.get("label") or "shared input asset") for item in manifest.get("input_assets_shared", []) if isinstance(item, dict)] if full else skipped,
        "errors": errors,
        "manifest": manifest,
    }

def build_image_result_reuse_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build a core-safe reuse payload from a result record.

    Extension metadata is included as read-only context for later extension runtimes.
    The core does not execute extension payloads during this API call.
    """
    prompt = record.get("prompt") if isinstance(record.get("prompt"), dict) else {}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    extensions = record.get("extensions") if isinstance(record.get("extensions"), dict) else {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    safe_params, params_cleanup = sanitize_replay_params(params, mode=str(record.get("mode") or "generate"))
    safe_extensions, extensions_cleanup = sanitize_replay_extensions(extensions)
    return {
        "schema_version": "neo.image.result_reuse.v1",
        "result_id": record.get("result_id") or "",
        "surface": "image",
        "subtab": record.get("subtab") or "generate",
        "mode": record.get("mode") or "txt2img",
        "prompt": {
            "positive": prompt.get("positive") or "",
            "negative": prompt.get("negative") or "",
            "effective_positive": prompt.get("effective_positive") or prompt.get("positive") or "",
            "effective_negative": prompt.get("effective_negative") or prompt.get("negative") or "",
            "conditioning": deepcopy_dict(prompt.get("conditioning") if isinstance(prompt.get("conditioning"), dict) else {}),
        },
        "params": deepcopy_dict(safe_params),
        "model": deepcopy_dict(model),
        "outputs": {
            "active_file": outputs.get("active_file") or "",
            "files": deepcopy_list(outputs.get("files") if isinstance(outputs.get("files"), list) else []),
            "reuse_policy": "all_batch_files_available_core_reads_first_by_default",
        },
        "input_assets": deepcopy_list((record.get("source") or {}).get("input_assets") if isinstance(record.get("source"), dict) and isinstance((record.get("source") or {}).get("input_assets"), list) else []),
        "lanpaint": deepcopy_dict(record.get("lanpaint") if isinstance(record.get("lanpaint"), dict) else {}),
        "provider_binding": deepcopy_dict(record.get("provider_binding") if isinstance(record.get("provider_binding"), dict) and record.get("provider_binding") else build_provider_binding_metadata(record)),
        "replay_validation": deepcopy_dict(record.get("replay_validation") if isinstance(record.get("replay_validation"), dict) and record.get("replay_validation") else build_provider_replay_validation_metadata(record)),
        "lineage": deepcopy_dict(record.get("lineage") if isinstance(record.get("lineage"), dict) and record.get("lineage") else build_output_lineage_metadata(record)),
        "replay": deepcopy_dict(record.get("replay") if isinstance(record.get("replay"), dict) and record.get("replay") else build_output_replay_metadata(record)),
        "extensions": {
            "used": deepcopy_list(safe_extensions.get("used") if isinstance(safe_extensions, dict) else []),
            "payloads": deepcopy_dict(safe_extensions.get("payloads") if isinstance(safe_extensions, dict) else {}),
            "replay_payloads": deepcopy_dict(safe_extensions.get("replay_payloads") if isinstance(safe_extensions, dict) else {}),
            "workflow_patches": deepcopy_list(safe_extensions.get("workflow_patches") if isinstance(safe_extensions, dict) else []),
            "validation": deepcopy_list(safe_extensions.get("validation") if isinstance(safe_extensions, dict) else []),
            "restore_policy": "provider_neutral_disabled_pending_live_revalidation",
        },
        "state_cleanup": {
            "schema": "neo.image.replay_state_sanitization.v1",
            "params": params_cleanup,
            "extensions": extensions_cleanup,
        },
    }


def _metadata_record_from_path(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return record if isinstance(record, dict) else None


def _identity_index_record_path(entry: dict[str, Any]) -> Path | None:
    rel = str(entry.get("metadata_path") or "").strip()
    if not rel:
        return None
    path = (ROOT_DIR / rel).resolve()
    allowed = [
        (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve(),
        IMAGE_METADATA_QUARANTINE_ROOT.resolve(),
    ]
    if not any(root == path or root in path.parents for root in allowed):
        return None
    return path if path.exists() and path.is_file() else None


def find_output_record_by_neo_output_id(neo_output_id: str) -> tuple[dict[str, Any] | None, Path | None, str]:
    wanted = str(neo_output_id or "").strip()
    if not wanted:
        return None, None, ""
    index = _load_output_identity_index()
    entry = index.get("identities", {}).get(wanted) if isinstance(index.get("identities"), dict) else None
    if isinstance(entry, dict):
        path = _identity_index_record_path(entry)
        if path is not None:
            record = _metadata_record_from_path(path)
            if record is not None:
                return record, path, str(entry.get("file_id") or "")
    for root in ((ROOT_DIR / "neo_data" / "outputs" / "image_metadata"), IMAGE_METADATA_QUARANTINE_ROOT):
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            record = _metadata_record_from_path(path)
            if not record:
                continue
            outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
            files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
            match = next((item for item in files if isinstance(item, dict) and str(item.get("neo_output_id") or "") == wanted), None)
            if match:
                _register_record_identities(path, record)
                return record, path.resolve(), str(match.get("file_id") or "")
    return None, None, ""


def _portable_inspector_record(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    model = summary.get("model") if isinstance(summary.get("model"), dict) else {}
    params = summary.get("params") if isinstance(summary.get("params"), dict) else {}
    filename = str(payload.get("image_filename") or "Recovered image")
    return {
        "schema_version": "neo.image.portable_recovery_inspector.v1",
        "result_id": str(payload.get("result_id") or payload.get("neo_output_id") or "portable_recovery"),
        "neo_result_uid": str(payload.get("neo_result_uid") or ""),
        "surface": "image",
        "mode": str(params.get("mode") or "recovered"),
        "subtab": "recovered",
        "created_at": str(payload.get("created_at") or ""),
        "prompt": {
            "positive": str(summary.get("positive_prompt") or ""),
            "negative": str(summary.get("negative_prompt") or ""),
            "effective_positive": str(summary.get("effective_positive_prompt") or summary.get("positive_prompt") or ""),
            "effective_negative": str(summary.get("effective_negative_prompt") or summary.get("negative_prompt") or ""),
            "conditioning": {},
        },
        "params": params,
        "model": model,
        "outputs": {"active_file": "recovered_image", "files": [{"file_id": "recovered_image", "filename": filename, "neo_output_id": str(payload.get("neo_output_id") or ""), "role": "image"}]},
        "save_details": {"category": str(payload.get("save_category") or "Recovered")},
        "portable_recovery": {"status": "embedded_only", "payload": payload},
    }


def recover_image_metadata_from_bytes(data: bytes, *, filename: str = "") -> dict[str, Any]:
    payload = extract_portable_metadata_bytes(data)
    if not payload:
        return {
            "schema_version": "neo.image.metadata_recovery.v1",
            "ok": False,
            "status": "not_neo_portable_image",
            "message": "No embedded Neo output identity was found in this image.",
            "filename": Path(filename).name,
        }
    output_id = str(payload.get("neo_output_id") or "")
    record, record_path, file_id = find_output_record_by_neo_output_id(output_id)
    if record is not None and record_path is not None:
        state = "quarantined" if IMAGE_METADATA_QUARANTINE_ROOT.resolve() in record_path.resolve().parents else "active"
        return {
            "schema_version": "neo.image.metadata_recovery.v1",
            "ok": True,
            "status": "full_record_recovered",
            "source": state,
            "neo_output_id": output_id,
            "file_id": file_id,
            "result_id": str(record.get("result_id") or payload.get("result_id") or ""),
            "metadata_path": _relative_to_root(record_path),
            "portable": payload,
            "record": record,
        }
    return {
        "schema_version": "neo.image.metadata_recovery.v1",
        "ok": True,
        "status": "embedded_metadata_only",
        "source": "embedded_image",
        "neo_output_id": output_id,
        "file_id": str(payload.get("file_id") or ""),
        "result_id": str(payload.get("result_id") or ""),
        "portable": payload,
        "record": _portable_inspector_record(payload),
        "message": "The full Neo sidecar was not found; Inspector can still show the compact metadata embedded in the image.",
    }


def scan_image_metadata_integrity() -> dict[str, Any]:
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve()
    orphans: list[dict[str, Any]] = []
    records: list[tuple[Path, dict[str, Any]]] = []
    path_claims: dict[str, list[dict[str, Any]]] = {}
    parse_errors: list[str] = []
    if metadata_root.exists():
        for path in metadata_root.rglob("*.json"):
            record = _metadata_record_from_path(path)
            if record is None:
                parse_errors.append(_relative_to_root(path))
                continue
            records.append((path.resolve(), record))
            outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
            files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
            live = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("path") or "").strip()
                if not rel:
                    continue
                resolved = (ROOT_DIR / rel).resolve()
                claim = {
                    "result_id": str(record.get("result_id") or path.stem),
                    "neo_output_id": str(item.get("neo_output_id") or ""),
                    "file_id": str(item.get("file_id") or ""),
                    "metadata_path": _relative_to_root(path),
                    "image_path": rel,
                    "exists": resolved.exists() and resolved.is_file(),
                }
                path_claims.setdefault(str(resolved).casefold(), []).append(claim)
                if claim["exists"]:
                    live.append(claim)
            if files and not live:
                orphans.append({
                    "result_id": str(record.get("result_id") or path.stem),
                    "neo_result_uid": str(record.get("neo_result_uid") or ""),
                    "metadata_path": _relative_to_root(path),
                    "output_ids": [str(item.get("neo_output_id") or "") for item in files if isinstance(item, dict) and item.get("neo_output_id")],
                    "reason": "no_corresponding_image_file",
                })
    collisions: list[dict[str, Any]] = []
    stale_collision_metadata: set[str] = set()
    for claims in path_claims.values():
        if len(claims) < 2:
            continue
        existing = next((item for item in claims if item.get("exists")), None)
        embedded_id = ""
        if existing:
            try:
                payload = extract_portable_metadata_bytes((ROOT_DIR / str(existing.get("image_path") or "")).read_bytes())
                embedded_id = str((payload or {}).get("neo_output_id") or "")
            except Exception:
                embedded_id = ""
        matching = [item for item in claims if embedded_id and item.get("neo_output_id") == embedded_id]
        stale = [item for item in claims if matching and item not in matching]
        for item in stale:
            stale_collision_metadata.add(str(item.get("metadata_path") or ""))
        collisions.append({
            "image_path": str((existing or claims[0]).get("image_path") or ""),
            "claim_count": len(claims),
            "embedded_neo_output_id": embedded_id,
            "resolved": len(matching) == 1,
            "authoritative_result_id": matching[0].get("result_id") if len(matching) == 1 else "",
            "claims": claims,
            "stale_metadata_paths": [item.get("metadata_path") for item in stale],
        })
    return {
        "schema_version": "neo.image.metadata_integrity_scan.v1",
        "ok": True,
        "metadata_record_count": len(records),
        "orphan_count": len(orphans),
        "collision_count": len(collisions),
        "resolved_collision_count": sum(1 for item in collisions if item.get("resolved")),
        "unresolved_collision_count": sum(1 for item in collisions if not item.get("resolved")),
        "parse_error_count": len(parse_errors),
        "orphans": orphans,
        "collisions": collisions,
        "parse_errors": parse_errors,
        "quarantine_candidates": sorted({*(item["metadata_path"] for item in orphans), *stale_collision_metadata}),
        "policy": "read_only_scan_embedded_id_resolves_filename_collisions",
    }


def quarantine_orphan_image_metadata(*, include_resolved_collisions: bool = True) -> dict[str, Any]:
    scan = scan_image_metadata_integrity()
    candidates = set(item.get("metadata_path") for item in scan.get("orphans", []) if item.get("metadata_path"))
    if include_resolved_collisions:
        for collision in scan.get("collisions", []):
            if collision.get("resolved"):
                candidates.update(item for item in collision.get("stale_metadata_paths", []) if item)
    active_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve()
    moved: list[dict[str, Any]] = []
    errors: list[str] = []
    for rel in sorted(candidates):
        source = (ROOT_DIR / str(rel)).resolve()
        if active_root not in source.parents or not source.exists():
            continue
        relative = source.relative_to(active_root)
        target = (IMAGE_METADATA_QUARANTINE_ROOT / relative).resolve()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            record = _metadata_record_from_path(source) or {}
            record["integrity_status"] = {
                "state": "quarantined",
                "reason": "missing_output_or_resolved_filename_collision",
                "quarantined_at": utc_now_iso(),
                "original_metadata_path": _relative_to_root(source),
            }
            target.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            source.unlink()
            _IMAGE_RESULT_SUMMARY_CACHE.pop(str(source), None)
            _register_record_identities(target, record)
            moved.append({"result_id": str(record.get("result_id") or source.stem), "from": _relative_to_root(source), "to": _relative_to_root(target)})
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
    return {
        "schema_version": "neo.image.metadata_quarantine.v1",
        "ok": not errors,
        "moved_count": len(moved),
        "moved": moved,
        "errors": errors,
        "quarantine_root": _relative_to_root(IMAGE_METADATA_QUARANTINE_ROOT),
        "policy": "reversible_quarantine_no_hard_delete",
        "scan": scan,
    }

def image_results_integrity_guard(*, selected_result_id: str | None = None, category: str | None = None) -> dict[str, Any]:
    """Validate persisted Results state after manual Neo_Data cleanup.

    This is intentionally read-only. It never tries to rebuild missing output files
    from Comfy/native backend folders and never triggers UI reloads by itself. The
    caller uses the payload to clear stale selected-result/cache references.
    """
    results_payload = list_image_results(category=category, limit=200, sort="newest")
    valid_ids = {str(item.get("result_id") or "") for item in results_payload.get("results", []) if item.get("result_id")}
    requested = sanitize_path_part(selected_result_id or "", fallback="").strip()
    selected_valid = bool(requested and requested in valid_ids)
    outputs_root = (ROOT_DIR / "neo_data" / "outputs" / "image")
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata")
    output_file_count = sum(1 for item in outputs_root.rglob("*") if item.is_file()) if outputs_root.exists() else 0
    metadata_file_count = sum(1 for item in metadata_root.rglob("*.json")) if metadata_root.exists() else 0
    has_orphaned_metadata = metadata_file_count > len(valid_ids)
    should_clear_selected = bool(requested and not selected_valid)
    should_clear_cached_results = output_file_count == 0 or has_orphaned_metadata
    return {
        "schema_version": "neo.image.results_integrity.v1",
        "ok": True,
        "selected_result_id": requested,
        "selected_valid": selected_valid,
        "valid_result_ids": sorted(valid_ids),
        "valid_result_count": len(valid_ids),
        "output_file_count": output_file_count,
        "metadata_file_count": metadata_file_count,
        "has_orphaned_metadata": has_orphaned_metadata,
        "should_clear_selected": should_clear_selected,
        "should_clear_cached_results": should_clear_cached_results,
        "policy": "clear_stale_refs_no_retry",
    }


def _summarize_output_record(record: dict[str, Any], record_path: Path) -> dict[str, Any]:
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    files = outputs.get("files") if isinstance(outputs.get("files"), list) else []
    existing_files = [item for item in files if isinstance(item, dict) and _output_file_exists(item)]
    active_file = outputs.get("active_file") or (existing_files[0].get("file_id") if existing_files else "")
    active = next((item for item in existing_files if item.get("file_id") == active_file), existing_files[0] if existing_files else {})
    result_id = record.get("result_id") or record_path.stem
    save_details = record.get("save_details") if isinstance(record.get("save_details"), dict) else {}
    return {
        "result_id": result_id,
        "schema_version": record.get("schema_version") or "",
        "surface": record.get("surface") or "image",
        "subtab": record.get("subtab") or "generate",
        "mode": record.get("mode") or "",
        "save_category": save_details.get("category") or record_path.parent.name,
        "created_at": record.get("created_at") or "",
        "job": record.get("job") if isinstance(record.get("job"), dict) else {},
        "active_file": active,
        "replay": record.get("replay") if isinstance(record.get("replay"), dict) else {},
        "file_count": len(existing_files),
        "missing_file_count": max(0, len(files) - len(existing_files)),
        "is_missing_files": bool(files) and not existing_files,
        "metadata_url": f"/api/image/result-metadata/{result_id}",
        "detail_url": f"/api/image/results/{result_id}",
    }


def _output_file_exists(file_record: dict[str, Any]) -> bool:
    path_value = str(file_record.get("path") or "").strip()
    if not path_value:
        return False
    path = (ROOT_DIR / path_value).resolve()
    neo_output_root = (ROOT_DIR / "neo_data" / "outputs" / "image").resolve()
    if neo_output_root not in path.parents or not path.exists() or not path.is_file():
        return False
    # New portable-metadata records carry a cheap filesystem fingerprint. If a
    # user deletes an image and a later generation reuses the same human-friendly
    # filename, the stale sidecar must not silently claim the replacement file.
    integrity = file_record.get("integrity") if isinstance(file_record.get("integrity"), dict) else {}
    if integrity:
        try:
            stat = path.stat()
            expected_size = int(integrity.get("size_bytes")) if integrity.get("size_bytes") not in (None, "") else None
            expected_mtime = int(integrity.get("mtime_ns")) if integrity.get("mtime_ns") not in (None, "") else None
            if expected_size is not None and stat.st_size != expected_size:
                return False
            if expected_mtime is not None and stat.st_mtime_ns != expected_mtime:
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def deepcopy_dict(value: Any) -> dict[str, Any]:
    return json.loads(json.dumps(value if isinstance(value, dict) else {}))


def deepcopy_list(value: Any) -> list[Any]:
    return json.loads(json.dumps(value if isinstance(value, list) else []))


def _read_output_bytes(output: dict[str, Any], *, timeout: float) -> bytes:
    source_path = output.get("path") or output.get("local_path")
    if source_path:
        path = Path(str(source_path))
        if path.exists() and path.is_file():
            return path.read_bytes()
    source_url = str(output.get("url") or "")
    if not source_url:
        raise ValueError("Provider output has no url or local path.")
    with request.urlopen(source_url, timeout=timeout) as response:  # noqa: S310 - local Comfy/controlled backend URL.
        return response.read()



def _output_suffix_for_bytes(original_name: str, detected_type: str | None = None) -> str:
    """Choose a persisted output suffix from image bytes, not only provider filename.

    Some backends/providers can emit JPEG bytes while reporting a .png filename.
    Neo-owned output records must use the content suffix so later source-image
    uploads do not fail the safety validator on Neo's own generated files.
    """

    suffix = Path(original_name).suffix.lower() or ".png"
    if detected_type:
        expected_type = ALLOWED_IMAGE_EXTENSIONS.get(suffix)
        if expected_type != detected_type:
            return canonical_image_suffix_for_type(detected_type)
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        return ".png"
    return suffix


def _category_output_filename(
    output_dir: Path,
    prefix: str,
    index: int,
    padding: int,
    original_name: str,
    *,
    detected_type: str | None = None,
) -> str:
    suffix = _output_suffix_for_bytes(original_name, detected_type=detected_type)
    clean_prefix = sanitize_path_part(prefix, fallback="NeoStudio")
    candidate = f"{clean_prefix}_{int(index):0{int(padding)}d}{suffix}"
    counter = 2
    while (output_dir / candidate).exists():
        candidate = f"{clean_prefix}_{int(index):0{int(padding)}d}_{counter}{suffix}"
        counter += 1
    return candidate


def cleanup_backend_native_outputs(outputs: list[dict[str, Any]], *, context: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
    """Best-effort cleanup for backend-native files after Neo_Data persistence.

    ComfyUI may materialize a temporary PreviewImage/SaveImage file before Neo can
    fetch it. There is no stable HTTP delete endpoint, so cleanup is file-system
    based and only runs when the backend profile exposes a local portable/backend path.
    """
    if not enabled:
        return {"enabled": False, "deleted": [], "skipped": ["Cleanup disabled in output settings."], "errors": []}
    backend_root = str(context.get("backend_output_root") or "").strip()
    if not backend_root:
        return {"enabled": True, "deleted": [], "skipped": ["Backend output root is not configured; native cleanup skipped."], "errors": []}
    root = Path(backend_root).expanduser().resolve()
    allowed_roots = [root]
    # Comfy PreviewImage stores temporary handoff files under <ComfyUI>/temp,
    # while SaveImage stores under <ComfyUI>/output. Both are backend-native
    # handoff locations and are safe to clean only after Neo_Data persistence.
    if root.name.lower() == "output":
        allowed_roots.append((root.parent / "temp").resolve())
    deleted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for output in outputs:
        filename = str(output.get("filename") or "").strip()
        if not filename:
            skipped.append("Output missing filename.")
            continue
        subfolder = str(output.get("subfolder") or "").strip().strip("/\\")
        file_type = str(output.get("type") or "output").strip() or "output"
        candidates = []
        if output.get("local_path") or output.get("path"):
            candidates.append(Path(str(output.get("local_path") or output.get("path"))))
        candidates.append(root / file_type / subfolder / filename if subfolder else root / file_type / filename)
        candidates.append(root / subfolder / filename if subfolder else root / filename)
        if file_type == "temp":
            candidates.append(root.parent / "temp" / subfolder / filename if subfolder else root.parent / "temp" / filename)
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
                if not any(resolved == allowed or allowed in resolved.parents for allowed in allowed_roots):
                    continue
                if resolved.exists() and resolved.is_file():
                    resolved.unlink()
                    deleted.append(_relative_to_root(resolved))
                    break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{filename}: {exc}")
        else:
            skipped.append(filename)
    return {"enabled": True, "backend_output_root": str(root), "deleted": deleted, "skipped": skipped, "errors": errors}

def _unique_output_filename(
    output_dir: Path,
    result_id: str,
    index: int,
    original_name: str,
    *,
    detected_type: str | None = None,
) -> str:
    suffix = _output_suffix_for_bytes(original_name, detected_type=detected_type)
    stem = sanitize_path_part(Path(original_name).stem, fallback=f"image_{index}")
    base = sanitize_path_part(f"{result_id}_{index}_{stem}", fallback=f"{result_id}_{index}")
    candidate = f"{base}{suffix}"
    counter = 2
    while (output_dir / candidate).exists():
        candidate = f"{base}_{counter}{suffix}"
        counter += 1
    return candidate


def _build_result_id(category: str, job_id: str) -> str:
    clean_job = sanitize_path_part(job_id, fallback="job")
    return sanitize_path_part(f"{category}_{clean_job}", fallback="image_output")


def _guess_mime_type(filename: str, *, detected_type: str | None = None) -> str:
    if detected_type == "jpeg":
        return "image/jpeg"
    if detected_type in {"png", "webp", "bmp"}:
        return f"image/{detected_type}"
    return mimetypes.guess_type(filename)[0] or "image/png"


def _backend_output_ref(output: dict[str, Any]) -> str:
    if not output:
        return ""
    parts = [str(output.get("type") or "output")]
    subfolder = str(output.get("subfolder") or "")
    filename = str(output.get("filename") or "")
    if subfolder:
        parts.append(subfolder)
    if filename:
        parts.append(filename)
    return "/".join(parts)


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def image_replay_storage_summary() -> dict[str, Any]:
    """Return IMG-R10 Replay/Latent storage usage and safe cleanup candidates.

    This is read-only. It scans Neo-owned image outputs, metadata sidecars, and
    provider-owned latent artifacts persisted under Neo_Data. It never inspects
    or deletes backend-native Comfy/Fooocus/Forge folders.
    """
    outputs_root = (ROOT_DIR / "neo_data" / "outputs" / "image").resolve()
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata").resolve()
    latents_root = (ROOT_DIR / "neo_data" / "outputs" / "image_latents").resolve()

    output_bytes, output_files = _safe_tree_size(outputs_root, suffixes={".png", ".jpg", ".jpeg", ".webp"})
    metadata_bytes, metadata_files = _safe_tree_size(metadata_root, suffixes={".json"})
    latent_bytes, latent_files = _safe_tree_size(latents_root, suffixes={".latent", ".safetensors", ".pt", ".bin"})

    referenced_paths: set[str] = set()
    referenced_result_ids: set[str] = set()
    records_scanned = 0
    latent_artifacts = 0
    failed_artifacts = 0
    metadata_scan_mode = "full_latent_reference_scan"
    # If there are no persisted latent files there cannot be any orphan latent
    # candidates. Avoid deserializing the entire Image metadata history merely to
    # prove an empty set. Large installations can have GB-scale replay sidecars.
    if latent_files == 0:
        metadata_scan_mode = "skipped_no_latent_files"
    elif metadata_root.exists():
        for record_path in metadata_root.rglob("*.json"):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            records_scanned += 1
            result_id = str(record.get("result_id") or record_path.stem)
            if result_id:
                referenced_result_ids.add(sanitize_path_part(result_id, fallback=""))
            replay = record.get("replay") if isinstance(record.get("replay"), dict) else {}
            latent_capture = replay.get("latent_capture") if isinstance(replay.get("latent_capture"), dict) else {}
            artifacts = latent_capture.get("artifacts") if isinstance(latent_capture.get("artifacts"), list) else []
            persistence = record.get("persistence") if isinstance(record.get("persistence"), dict) else {}
            persisted = persistence.get("latent_artifacts") if isinstance(persistence.get("latent_artifacts"), list) else []
            for artifact in [*artifacts, *persisted]:
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("state") == "failed_to_persist":
                    failed_artifacts += 1
                    continue
                raw_path = str(artifact.get("path") or "").strip()
                if not raw_path:
                    continue
                path = (ROOT_DIR / raw_path).resolve()
                if latents_root == path.parent or latents_root in path.parents:
                    referenced_paths.add(_relative_to_root(path))
                    latent_artifacts += 1

    orphan_dirs: list[dict[str, Any]] = []
    orphan_files: list[dict[str, Any]] = []
    if latents_root.exists():
        for result_dir in latents_root.iterdir():
            if not result_dir.is_dir():
                continue
            result_id = sanitize_path_part(result_dir.name, fallback="")
            dir_bytes, dir_files = _safe_tree_size(result_dir, suffixes={".latent", ".safetensors", ".pt", ".bin"})
            if result_id and result_id not in referenced_result_ids:
                orphan_dirs.append({
                    "result_id": result_id,
                    "path": _relative_to_root(result_dir),
                    "bytes": dir_bytes,
                    "files": dir_files,
                    "reason": "No metadata sidecar references this latent result folder.",
                })
                continue
            for file_path in result_dir.rglob("*"):
                if not file_path.is_file() or file_path.suffix.lower() not in {".latent", ".safetensors", ".pt", ".bin"}:
                    continue
                rel = _relative_to_root(file_path.resolve())
                if rel not in referenced_paths:
                    orphan_files.append({
                        "path": rel,
                        "bytes": _safe_file_size(file_path),
                        "reason": "Latent file is not referenced by any replay metadata artifact.",
                    })

    return {
        "schema_version": "neo.image.replay_storage.v1",
        "ok": True,
        "roots": {
            "outputs": _relative_to_root(outputs_root),
            "metadata": _relative_to_root(metadata_root),
            "latents": _relative_to_root(latents_root),
        },
        "usage": {
            "outputs": {"bytes": output_bytes, "files": output_files, "display": _format_bytes(output_bytes)},
            "metadata": {"bytes": metadata_bytes, "files": metadata_files, "display": _format_bytes(metadata_bytes)},
            "latents": {"bytes": latent_bytes, "files": latent_files, "display": _format_bytes(latent_bytes)},
            "total_bytes": output_bytes + metadata_bytes + latent_bytes,
            "total_display": _format_bytes(output_bytes + metadata_bytes + latent_bytes),
        },
        "records": {
            "metadata_records_scanned": records_scanned,
            "metadata_records_count": metadata_files,
            "metadata_scan_mode": metadata_scan_mode,
            "referenced_latent_artifacts": latent_artifacts,
            "failed_latent_artifacts": failed_artifacts,
            "referenced_result_ids": len(referenced_result_ids),
        },
        "cleanup_candidates": {
            "orphan_latent_dirs": orphan_dirs,
            "orphan_latent_files": orphan_files,
            "orphan_latent_bytes": sum(int(item.get("bytes") or 0) for item in orphan_dirs) + sum(int(item.get("bytes") or 0) for item in orphan_files),
            "orphan_latent_display": _format_bytes(sum(int(item.get("bytes") or 0) for item in orphan_dirs) + sum(int(item.get("bytes") or 0) for item in orphan_files)),
        },
        "retention_policy": {
            "metadata": "keep_forever_unless_result_deleted",
            "outputs": "delete_only_when_user_deletes_saved_output",
            "latents": "delete_orphans_only_from_storage_manager_or_delete_result",
            "backend_native_outputs": "controlled_by_results_save_details_cleanup_toggle",
        },
        "policy": "read_only_summary_safe_cleanup_candidates_only",
    }


def cleanup_image_replay_storage(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete safe IMG-R10 replay storage candidates from Neo_Data.

    Supported action: delete_orphan_latents. It only removes latent files/folders
    not referenced by any metadata sidecar. It never deletes outputs, metadata, or
    currently referenced restore-point artifacts.
    """
    data = payload if isinstance(payload, dict) else {}
    action = str(data.get("action") or "delete_orphan_latents").strip().lower()
    if action not in {"delete_orphan_latents"}:
        return {"ok": False, "action": action, "deleted": [], "errors": ["Unsupported cleanup action."], "policy": "orphan_latents_only"}
    summary = image_replay_storage_summary()
    candidates = summary.get("cleanup_candidates") if isinstance(summary.get("cleanup_candidates"), dict) else {}
    deleted: list[str] = []
    errors: list[str] = []
    latents_root = (ROOT_DIR / "neo_data" / "outputs" / "image_latents").resolve()

    for item in candidates.get("orphan_latent_files") if isinstance(candidates.get("orphan_latent_files"), list) else []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        path = (ROOT_DIR / raw_path).resolve()
        try:
            if latents_root not in path.parents or not path.is_file():
                continue
            path.unlink()
            deleted.append(raw_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{raw_path}: {exc}")

    for item in candidates.get("orphan_latent_dirs") if isinstance(candidates.get("orphan_latent_dirs"), list) else []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        path = (ROOT_DIR / raw_path).resolve()
        try:
            if latents_root not in path.parents or not path.is_dir():
                continue
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                    deleted.append(_relative_to_root(child))
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
            deleted.append(raw_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{raw_path}: {exc}")

    after = image_replay_storage_summary()
    return {
        "schema_version": "neo.image.replay_storage_cleanup.v1",
        "ok": not errors,
        "action": action,
        "deleted": deleted,
        "errors": errors,
        "summary": after,
        "policy": "deleted_orphan_latents_only_no_metadata_or_outputs_touched",
    }


def _safe_tree_size(root: Path, *, suffixes: set[str] | None = None) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    total = 0
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        total += _safe_file_size(path)
        count += 1
    return total, count


def _safe_file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _format_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
