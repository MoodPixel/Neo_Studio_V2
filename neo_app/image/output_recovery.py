from __future__ import annotations

from pathlib import Path
from typing import Any

SCHEMA_ID = "neo.image.output_recovery.v25_3"
RECOVERABLE_IMPORT_STATUSES = {
    "import_failed",
    "saved_in_comfy_only",
    "completed_no_outputs_recoverable",
    "completed_import_failed",
}
IMAGE_OUTPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value if isinstance(value, list) else []) if isinstance(item, dict)]


def _backend_file_candidates(output: dict[str, Any], backend_output_root: str) -> list[Path]:
    filename = str(output.get("filename") or "").strip()
    if not filename or not backend_output_root:
        return []
    root = Path(backend_output_root).expanduser()
    subfolder = str(output.get("subfolder") or "").strip().strip("/\\")
    file_type = str(output.get("type") or "output").strip() or "output"
    direct = Path(filename)
    candidates: list[Path] = []
    if direct.is_absolute():
        candidates.append(direct)
    for source_key in ("local_path", "path"):
        value = str(output.get(source_key) or "").strip()
        if value:
            candidates.append(Path(value).expanduser())
    if file_type in {"output", "temp", "input"}:
        candidates.append(root / file_type / subfolder / filename if subfolder else root / file_type / filename)
    candidates.append(root / subfolder / filename if subfolder else root / filename)
    if root.name.lower() == "output":
        if file_type == "temp":
            candidates.append(root.parent / "temp" / subfolder / filename if subfolder else root.parent / "temp" / filename)
        if file_type == "input":
            candidates.append(root.parent / "input" / subfolder / filename if subfolder else root.parent / "input" / filename)
    # Preserve order while dropping duplicates.
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.as_posix()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def enrich_provider_outputs_for_local_recovery(provider_outputs: list[dict[str, Any]], context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Attach a backend local_path fallback when Comfy /view is slow or unavailable.

    Comfy can finish and save files while Neo's HTTP import times out. If the backend
    profile exposes a local output root, this keeps persistence retry-safe by reading
    the file directly before falling back to /view.
    """
    context = context if isinstance(context, dict) else {}
    backend_root = str(context.get("backend_output_root") or "").strip()
    enriched: list[dict[str, Any]] = []
    for output in _as_list(provider_outputs):
        item = dict(output)
        if not item.get("local_path") and not item.get("path") and backend_root:
            for candidate in _backend_file_candidates(item, backend_root):
                try:
                    resolved = candidate.expanduser().resolve()
                except Exception:  # noqa: BLE001
                    resolved = candidate.expanduser()
                if resolved.exists() and resolved.is_file():
                    item["local_path"] = str(resolved)
                    item.setdefault("recovery_source", "backend_local_path")
                    break
        enriched.append(item)
    return enriched


def image_output_recovery_payload(
    *,
    job_id: str,
    profile_id: str,
    provider_outputs: list[dict[str, Any]] | None = None,
    persisted: dict[str, Any] | None = None,
    status: str = "saved_in_comfy_only",
    errors: list[str] | None = None,
) -> dict[str, Any]:
    outputs = _as_list(provider_outputs)
    persisted = persisted if isinstance(persisted, dict) else {}
    files = _as_list(persisted.get("files"))
    result_id = str(persisted.get("result_id") or "")
    clean_status = str(status or "saved_in_comfy_only")
    recoverable = clean_status in RECOVERABLE_IMPORT_STATUSES or (bool(outputs) and not files)
    endpoint = f"/api/image/jobs/{profile_id}/{job_id}/recover" if job_id and profile_id else ""
    label = "Saved in Comfy only — recovery available" if recoverable else "Output import state recorded"
    if clean_status == "completed_no_outputs_recoverable":
        label = "Comfy completed, but Neo found no output files yet"
    elif clean_status == "import_failed":
        label = "Neo output import failed — recovery available"
    return {
        "schema_id": SCHEMA_ID,
        "job_id": str(job_id or ""),
        "profile_id": str(profile_id or ""),
        "status": clean_status,
        "label": label,
        "recoverable": recoverable,
        "recovery_endpoint": endpoint,
        "provider_output_count": len(outputs),
        "persisted_file_count": len(files),
        "result_id": result_id,
        "errors": [str(item) for item in (errors or persisted.get("errors") or []) if str(item)],
        "policy": {
            "do_not_cache_failed_imports": True,
            "provider_outputs_are_temporary_until_neo_data_import_succeeds": True,
            "manual_recovery_endpoint_available": bool(endpoint),
        },
    }


def normalize_import_failure_status(provider_outputs: list[dict[str, Any]] | None, files: list[dict[str, Any]] | None) -> str:
    output_count = len(_as_list(provider_outputs))
    file_count = len(_as_list(files))
    if file_count:
        return "completed_with_warnings"
    if output_count:
        return "import_failed"
    return "completed_no_outputs_recoverable"
