from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import parse, request

from neo_app.image.output_paths import ROOT_DIR, get_image_output_paths, sanitize_path_part
from neo_app.image.upload_validation import ALLOWED_IMAGE_EXTENSIONS, _detect_image_type, canonical_image_suffix_for_type
from neo_app.image.output_settings import (
    ensure_output_settings_dirs,
    load_image_output_settings,
    metadata_category_dir,
    next_category_index,
    output_category_dir,
)
from neo_app.image.output_records import (
    build_assistant_output_summary,
    build_image_output_record,
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
    result_id = _build_result_id(paths.category, job_id)
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
            files.append(build_output_file_record(
                file_id=file_id,
                filename=target.name,
                path=_relative_to_root(target),
                url=f"/api/image/output-file?result_id={result_id}&file_id={file_id}",
                mime_type=mime_type,
                role="image",
                metadata=_output_file_metadata(output, index=index),
            ))
        except Exception as exc:  # noqa: BLE001 - keep partial persistence visible to UI.
            errors.append(f"Output {index} could not be persisted: {exc}")

    latent_artifacts = persist_latent_outputs(latent_outputs, result_id=result_id, timeout=timeout)
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

    return PersistedImageOutputs(
        ok=bool(files) and not errors,
        result_id=result_id,
        record=record,
        record_path=record_path,
        files=files,
        errors=errors,
    )




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


def _comfy_load_latent_name(output: dict[str, Any], filename: str) -> str:
    """Build the Comfy LoadLatent name without f-string backslash expressions.

    Python 3.10 rejects backslashes inside f-string expression bodies. Keep the
    path cleanup in normal statements so Neo can start on the user's runtime.
    """
    subfolder = str(output.get("subfolder") or "").strip("/\\")
    source_type = str(output.get("type") or "output")
    prefix = f"{subfolder}/" if subfolder else ""
    suffix = f" [{source_type}]" if source_type != "input" else ""
    return f"{prefix}{filename}{suffix}"

def persist_latent_outputs(latent_outputs: list[dict[str, Any]], *, result_id: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Copy provider-owned Comfy latent artifacts into Neo_Data and return replay manifests.

    R8 only accepts provider outputs explicitly marked kind=latent. This keeps
    latent resume honest: no PNG re-encode and no synthetic checkpoint claims.
    """
    if not latent_outputs:
        return []
    clean_result_id = sanitize_path_part(result_id, fallback="image_result")
    target_dir = ROOT_DIR / "neo_data" / "outputs" / "image_latents" / clean_result_id
    target_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for index, output in enumerate(latent_outputs, start=1):
        try:
            restore_point = str(output.get("restore_point") or "final_latent").strip() or "final_latent"
            original_name = str(output.get("filename") or f"{restore_point}_{index}.latent")
            suffix = Path(original_name).suffix.lower() or ".latent"
            if suffix not in {".latent", ".safetensors", ".pt", ".bin"}:
                suffix = ".latent"
            filename = sanitize_path_part(f"{restore_point}_{index}{suffix}", fallback=f"latent_{index}{suffix}")
            target = target_dir / filename
            counter = 2
            while target.exists():
                filename = sanitize_path_part(f"{restore_point}_{index}_{counter}{suffix}", fallback=f"latent_{index}_{counter}{suffix}")
                target = target_dir / filename
                counter += 1
            target.write_bytes(_read_output_bytes(output, timeout=timeout))
            artifacts.append({
                "artifact_id": f"latent_{index}",
                "restore_point": restore_point,
                "kind": "latent_tensor",
                "path": _relative_to_root(target),
                "format": str(output.get("format") or "comfy_latent"),
                "provider_owned": True,
                "provider_id": str(output.get("provider_id") or "comfyui"),
                "backend": "comfyui",
                "source_node_id": str(output.get("node_id") or ""),
                "source_filename": original_name,
                "source_subfolder": str(output.get("subfolder") or ""),
                "source_type": str(output.get("type") or "output"),
                "comfy_load_name": _comfy_load_latent_name(output, original_name),
                "state": "available",
            })
        except Exception as exc:  # noqa: BLE001
            artifacts.append({
                "artifact_id": f"latent_{index}",
                "restore_point": str(output.get("restore_point") or "final_latent"),
                "kind": "latent_tensor",
                "path": "",
                "format": str(output.get("format") or "comfy_latent"),
                "provider_owned": True,
                "provider_id": str(output.get("provider_id") or "comfyui"),
                "backend": "comfyui",
                "source_node_id": str(output.get("node_id") or ""),
                "state": "failed_to_persist",
                "error": str(exc),
            })
    return [item for item in artifacts if item.get("path")]


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


def _iter_comfy_handoff_names(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").lower()
            if key_text in {"comfy_source_image_name", "comfy_source_image_2_name", "comfy_source_image_3_name", "comfy_mask_image_name", "comfy_outpaint_canvas_image_name", "comfy_outpaint_mask_image_name", "comfy_input_name", "comfy_image_name", "comfy_name", "workflow_source", "image_name", "mask_name"}:
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


def list_image_results(*, category: str | None = None, limit: int = 50, sort: str = "newest") -> dict[str, Any]:
    """List persisted Image output records from Neo_Data metadata sidecars.

    Results APIs intentionally read Neo-owned sidecars, not ComfyUI output folders.
    Missing image files are pruned from the API response so the UI never renders
    broken placeholder cards after users manually clear Neo_Data outputs.
    """
    limit = max(1, min(int(limit or 50), 200))
    selected_category = sanitize_path_part(category or "", fallback="").strip()
    if selected_category.lower() in {"", "all", "any"}:
        selected_category = ""

    records: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    metadata_root = (ROOT_DIR / "neo_data" / "outputs" / "image_metadata")
    metadata_dirs = [item for item in metadata_root.iterdir() if item.is_dir()] if metadata_root.exists() else []

    # Include canonical dirs even if metadata_root has not been created yet.
    for item in ["generate", "img2img", "inpaint", "outpaint", "upscale", "edit", "batch", "uncategorized"]:
        path = get_image_output_paths(item, create=False).metadata_dir
        if path not in metadata_dirs:
            metadata_dirs.append(path)

    for metadata_dir in metadata_dirs:
        if not metadata_dir.exists():
            continue
        for record_path in metadata_dir.glob("*.json"):
            if record_path in seen_paths:
                continue
            seen_paths.add(record_path)
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                summary = _summarize_output_record(record, record_path)
                if summary.get("is_missing_files"):
                    continue
                if selected_category and selected_category not in {
                    sanitize_path_part(summary.get("subtab") or "", fallback=""),
                    sanitize_path_part(summary.get("save_category") or "", fallback=""),
                    sanitize_path_part(record_path.parent.name, fallback=""),
                }:
                    continue
                records.append(summary)
            except Exception:
                continue
    reverse = str(sort or "newest").lower() not in {"oldest", "old_to_new", "asc"}
    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=reverse)
    return {
        "schema_version": "neo.image.results_api.v1",
        "count": min(len(records), limit),
        "total": len(records),
        "results": records[:limit],
        "source": "neo_data/outputs/image_metadata",
        "category": selected_category or "all",
        "sort": "newest" if reverse else "oldest",
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
        "params": deepcopy_dict(params),
        "model": deepcopy_dict(model),
        "outputs": {
            "active_file": outputs.get("active_file") or "",
            "files": deepcopy_list(outputs.get("files") if isinstance(outputs.get("files"), list) else []),
            "reuse_policy": "all_batch_files_available_core_reads_first_by_default",
        },
        "extensions": {
            "used": deepcopy_list(extensions.get("used") if isinstance(extensions, dict) else []),
            "payloads": deepcopy_dict(extensions.get("payloads") if isinstance(extensions, dict) else {}),
            "workflow_patches": deepcopy_list(extensions.get("workflow_patches") if isinstance(extensions, dict) else []),
            "validation": deepcopy_list(extensions.get("validation") if isinstance(extensions, dict) else []),
            "restore_policy": "defer_to_extension_runtime",
        },
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
    return neo_output_root in path.parents and path.exists() and path.is_file()


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
    if metadata_root.exists():
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
