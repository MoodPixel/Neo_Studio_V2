from __future__ import annotations

from pathlib import Path
from shutil import disk_usage
from typing import Any, Callable
import math

DISK_PREFLIGHT_SCHEMA_ID = "neo.admin.models.huggingface.disk_preflight.v1"
PHASE_ID = "phase4_5_4_huggingface_snapshot_disk_preflight"
MEBIBYTE = 1024 * 1024
MIN_SAFETY_RESERVE_MB = 1024
SAFETY_RESERVE_PERCENT = 10


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _nearest_existing_path(path_text: str) -> Path | None:
    text = _clean(path_text)
    if not text:
        return None
    candidate = Path(text).expanduser()
    while True:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            return None
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _usage_values(value: Any) -> tuple[int, int, int]:
    if hasattr(value, "total") and hasattr(value, "used") and hasattr(value, "free"):
        return int(value.total), int(value.used), int(value.free)
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return int(value[0]), int(value[1]), int(value[2])
    raise TypeError("disk_usage_result_invalid")


def build_huggingface_snapshot_disk_preflight(
    request_payload: dict[str, Any],
    *,
    disk_usage_fn: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Check storage headroom before a Hugging Face snapshot transfer starts.

    The estimate is deliberately conservative in Phase 4.5.4. Neo trusts the
    manifest-declared expected snapshot size and grants no cache-reuse credit
    until Phase 4.5.5 can authoritatively probe the installed snapshot.
    """

    request_data = _as_dict(request_payload)
    install = _as_dict(request_data.get("install"))
    cache = _as_dict(request_data.get("cache"))
    expected_size_mb = _safe_float(install.get("expected_size_mb"), 0.0)
    cache_dir = _clean(cache.get("hub_cache"))
    errors: list[str] = []
    warnings: list[str] = []

    if expected_size_mb <= 0:
        errors.append("snapshot_expected_size_missing")
    if not cache_dir:
        errors.append("huggingface_cache_unresolved")

    expected_size_bytes = int(math.ceil(expected_size_mb * MEBIBYTE)) if expected_size_mb > 0 else 0
    percent_reserve_mb = int(math.ceil(expected_size_mb * (SAFETY_RESERVE_PERCENT / 100.0))) if expected_size_mb > 0 else 0
    safety_reserve_mb = max(MIN_SAFETY_RESERVE_MB, percent_reserve_mb) if expected_size_mb > 0 else 0
    safety_reserve_bytes = safety_reserve_mb * MEBIBYTE

    # Phase 4.5.5 now owns authoritative snapshot/cache state, but it does
    # not calculate exact missing-blob transfer bytes. Keep zero cache credit
    # so partial/stale state cannot make this storage gate under-estimate.
    cached_reuse_credit_bytes = 0
    estimated_download_bytes = expected_size_bytes
    required_free_bytes = estimated_download_bytes + safety_reserve_bytes

    usage_path: Path | None = None
    total_bytes = used_bytes = free_bytes = 0
    usage_error = ""
    if not errors:
        usage_path = _nearest_existing_path(cache_dir)
        if usage_path is None:
            errors.append("disk_space_check_unavailable")
            usage_error = "No existing parent path could be found for the resolved Hugging Face cache."
        else:
            if str(usage_path) != str(Path(cache_dir).expanduser()):
                warnings.append("huggingface_cache_missing_using_nearest_existing_parent_for_disk_check")
            try:
                usage = (disk_usage_fn or disk_usage)(str(usage_path))
                total_bytes, used_bytes, free_bytes = _usage_values(usage)
            except Exception as exc:
                errors.append("disk_space_check_unavailable")
                usage_error = f"Disk usage could not be read ({type(exc).__name__})."

    sufficient = bool(not errors and free_bytes >= required_free_bytes)
    if not errors and not sufficient:
        errors.append("insufficient_disk_space")

    free_after_estimated_install_bytes = free_bytes - estimated_download_bytes if free_bytes else 0
    headroom_after_install_bytes = free_bytes - required_free_bytes if free_bytes else 0
    ok = not errors
    if "insufficient_disk_space" in errors:
        status = "insufficient_space"
    elif errors:
        status = "unavailable"
    else:
        status = "ready"

    if status == "insufficient_space":
        message = (
            f"Not enough free disk space for this snapshot. Neo requires approximately "
            f"{required_free_bytes / MEBIBYTE:.0f} MB free ({expected_size_bytes / MEBIBYTE:.0f} MB snapshot + "
            f"{safety_reserve_bytes / MEBIBYTE:.0f} MB safety reserve), but only "
            f"{free_bytes / MEBIBYTE:.0f} MB is currently free on the Hugging Face cache filesystem."
        )
    elif status == "unavailable":
        if "snapshot_expected_size_missing" in errors:
            message = "This repository snapshot does not declare a positive install.expected_size_mb, so Neo cannot perform a safe disk-space preflight."
        elif "huggingface_cache_unresolved" in errors:
            message = "Neo could not resolve the Hugging Face Hub cache path required for disk-space preflight."
        else:
            message = usage_error or "Neo could not verify free disk space for the Hugging Face cache filesystem."
    else:
        message = (
            f"Disk preflight passed with {free_bytes / MEBIBYTE:.0f} MB free. "
            f"Neo reserves {required_free_bytes / MEBIBYTE:.0f} MB for the snapshot estimate and safety headroom."
        )

    return {
        "schema_id": DISK_PREFLIGHT_SCHEMA_ID,
        "phase": PHASE_ID,
        "ok": ok,
        "status": status,
        "catalog_id": _clean(request_data.get("catalog_id")),
        "cache": {
            "hub_cache": cache_dir,
            "usage_path": str(usage_path) if usage_path is not None else "",
            "cache_exists": bool(cache_dir and Path(cache_dir).expanduser().exists()),
        },
        "estimate": {
            "expected_size_mb": expected_size_mb,
            "expected_size_bytes": expected_size_bytes,
            "estimated_download_bytes": estimated_download_bytes,
            "cached_reuse_credit_bytes": cached_reuse_credit_bytes,
            "safety_reserve_mb": safety_reserve_mb,
            "safety_reserve_bytes": safety_reserve_bytes,
            "required_free_bytes": required_free_bytes,
            "estimate_basis": "manifest_expected_size_conservative_no_cache_credit",
            "safety_policy": f"max_{MIN_SAFETY_RESERVE_MB}mb_or_{SAFETY_RESERVE_PERCENT}_percent",
        },
        "disk": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "free_after_estimated_install_bytes": free_after_estimated_install_bytes,
            "headroom_after_install_bytes": headroom_after_install_bytes,
            "sufficient": sufficient,
        },
        "message": message,
        "warnings": sorted(set(warnings)),
        "errors": sorted(set(errors)),
        "policy": {
            "fail_closed": True,
            "checked_before_job_creation": True,
            "recheck_before_snapshot_download": True,
            "authoritative_installed_probe": False,
            "cache_reuse_credit": False,
            "manifest_mutated": False,
            "runtime_download_on_generate": False,
        },
    }
