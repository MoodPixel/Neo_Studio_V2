from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib import request, error
import json
import os
from datetime import datetime, timezone

from neo_app.providers.registry import get_provider, get_provider_feature_capabilities
from neo_app.services.ui_state import read_backend_profile_selection_state, write_backend_profile_selection_state
from neo_app.runtime_data import (
    backend_api_key_secret_runtime_path,
    backend_profile_runtime_path,
    backend_profile_template_path,
    default_backend_api_key_secret_payload,
    ensure_backend_api_key_secret_store,
    ensure_backend_profile_store,
)
from neo_extensions.built_in.ip_adapter.backend.node_discovery import discover_extra_model_path_inputs, merge_model_inputs

PROVIDER_DIR = Path(__file__).resolve().parent
PROFILE_TEMPLATE_PATH = PROVIDER_DIR / "backend_profiles.json"

# Phase 5 public-preview runtime gate: local backends must be explicitly
# connected/tested in the current server session before generation routes run.
# This prevents a stale saved runtime block from silently enabling Image, Video,
# Assistant, Roleplay, or Prompt & Captioning work after a fresh launch.
_CONNECTED_TASK_PROFILE_IDS: set[str] = set()
# Runtime user profile edits live in neo_data, not in the app/template file.
PROFILE_PATH = backend_profile_runtime_path()
SECRET_PATH = backend_api_key_secret_runtime_path()
MANIFEST_PATH = PROVIDER_DIR / "provider_manifest.json"
PROFILE_REGISTRY_VERSION = "0.2.0-unified-backend-profile-schema"
SUPPORTED_BACKEND_SURFACES = {"image", "video", "voice", "text", "audio", "prompt_captioning", "assistant", "roleplay"}
LOCAL_CONNECTION_KINDS = {"url", "local_http"}
LOCAL_PROCESS_CONNECTION_KINDS = {"portable_path", "local_process", "local_process_or_http"}
CLOUD_CONNECTION_KINDS = {"cloud_api", "api", "remote_api"}
API_KEY_AUTH_MODES = {"env", "manual", "none"}
API_KEY_CLEAR_SENTINELS = {"__CLEAR__", "<CLEAR>", "clear", "CLEAR"}
CONNECTION_TEST_DIAGNOSTIC_STATUSES = {"missing_config", "missing_key", "auth_failed", "offline", "error", "disabled", "disconnected"}
CONNECTION_TEST_READY_STATUSES = {"connected", "available", "online", "ready"}
BACKEND_SELECTION_ALIASES = {
    "prompt_captioning": {"prompt_captioning", "text"},
    "roleplay": {"roleplay", "text"},
    "assistant": {"assistant", "text", "prompt_captioning"},
}



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def _normalize_auth_mode(value: Any, default: str = "env") -> str:
    mode = str(value or default or "env").strip().lower()
    return mode if mode in API_KEY_AUTH_MODES else default


def _mask_secret(value: str) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    return f"••••{clean[-4:]}" if len(clean) >= 4 else "••••"


def _secret_ref_for_profile(profile_id: str | None) -> str:
    return _safe_profile_id(str(profile_id or "").strip(), "backend_profile")


def backend_api_key_secret_store_paths() -> dict[str, str | bool]:
    secret_path = Path(SECRET_PATH)
    return {
        "runtime_store": True,
        "secret_store": True,
        "store_policy": "local_backend_api_key_secret_store",
        "secret_path": secret_path.as_posix(),
        "runtime_path": secret_path.as_posix(),
        "raw_values_returned_to_frontend": False,
        "plaintext_local_store": True,
    }


def _ensure_secret_payload_exists() -> dict[str, Any]:
    secret_path = Path(SECRET_PATH)
    if secret_path == backend_api_key_secret_runtime_path():
        return ensure_backend_api_key_secret_store()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if not secret_path.exists():
        secret_path.write_text(json.dumps(default_backend_api_key_secret_payload(), indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "runtime_store": True,
        "secret_store": True,
        "runtime_path": secret_path.as_posix(),
        "created_files": [],
    }


def _read_secret_payload() -> dict[str, Any]:
    _ensure_secret_payload_exists()
    secret_path = Path(SECRET_PATH)
    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8")) if secret_path.exists() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_id", "neo.runtime_data.backend_api_key_secret_store.v1")
    payload.setdefault("secrets", {})
    if not isinstance(payload.get("secrets"), dict):
        payload["secrets"] = {}
    payload.setdefault("metadata", {})
    return payload


def _write_secret_payload(payload: dict[str, Any]) -> None:
    _ensure_secret_payload_exists()
    secret_path = Path(SECRET_PATH)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    payload = payload if isinstance(payload, dict) else default_backend_api_key_secret_payload()
    payload.setdefault("schema_id", "neo.runtime_data.backend_api_key_secret_store.v1")
    payload.setdefault("secrets", {})
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["metadata"] = {
        **metadata,
        "runtime_store": True,
        "secret_store": True,
        "raw_values_returned_to_frontend": False,
        "plaintext_local_store": True,
        "updated_at": _now_iso(),
    }
    secret_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _backend_api_key_secret_record(profile_id: str | None) -> dict[str, Any] | None:
    ref = _secret_ref_for_profile(profile_id)
    payload = _read_secret_payload()
    record = (payload.get("secrets") or {}).get(ref)
    return record if isinstance(record, dict) else None


def _backend_api_key_secret_value(profile_id: str | None) -> str:
    record = _backend_api_key_secret_record(profile_id)
    return str((record or {}).get("value") or "").strip()


def _save_backend_api_key_secret(profile_id: str, value: str, *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_value = str(value or "").strip()
    if not clean_value:
        return _delete_backend_api_key_secret(profile_id)
    payload = _read_secret_payload()
    secrets = payload.setdefault("secrets", {})
    ref = _secret_ref_for_profile(profile_id)
    profile = profile if isinstance(profile, dict) else {}
    secrets[ref] = {
        "value": clean_value,
        "provider_id": str(profile.get("provider_id") or ""),
        "surface": str(profile.get("surface") or ""),
        "profile_id": str(profile_id or ref),
        "preview": _mask_secret(clean_value),
        "updated_at": _now_iso(),
    }
    _write_secret_payload(payload)
    return {"api_key_ref": ref, "api_key_saved": True, "api_key_preview": _mask_secret(clean_value)}


def _delete_backend_api_key_secret(profile_id: str | None) -> dict[str, Any]:
    payload = _read_secret_payload()
    secrets = payload.setdefault("secrets", {})
    ref = _secret_ref_for_profile(profile_id)
    existed = ref in secrets
    secrets.pop(ref, None)
    _write_secret_payload(payload)
    return {"api_key_ref": ref, "api_key_saved": False, "api_key_preview": "", "deleted": existed}


def _linked_credential_profile_id(connection: dict[str, Any]) -> str:
    connection = connection or {}
    return str(connection.get("credential_profile_id") or connection.get("linked_credential_profile_id") or "").strip()


def _linked_manual_secret(connection: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    linked_profile_id = _linked_credential_profile_id(connection)
    if not linked_profile_id:
        return "", "", None
    linked_ref = _secret_ref_for_profile(linked_profile_id)
    return linked_profile_id, _backend_api_key_secret_value(linked_ref), _backend_api_key_secret_record(linked_ref)


def _connection_api_key_status(connection: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    connection = connection or {}
    auth_mode = _normalize_auth_mode(connection.get("auth_mode") or connection.get("api_key_mode"), "none")
    env_name = str(connection.get("api_key_env") or "").strip()
    profile_ref = _secret_ref_for_profile(connection.get("api_key_ref") or profile_id or connection.get("profile_id") or "")
    legacy_manual_value = str(connection.get("api_key_value") or "").strip()
    secret_value = _backend_api_key_secret_value(profile_ref) if profile_ref else ""
    secret_record = _backend_api_key_secret_record(profile_ref) if profile_ref else None
    linked_profile_id, linked_secret_value, linked_secret_record = _linked_manual_secret(connection)
    env_value = str(os.getenv(env_name) or "").strip() if env_name else ""

    if auth_mode == "none":
        return {
            "auth_mode": auth_mode,
            "api_key_is_configured": True,
            "api_key_status": "not_required",
            "api_key_source": "none",
            "api_key_preview": "",
            "api_key_saved": False,
            "api_key_ref": "",
            "api_key_status_message": "No API key required for this profile.",
        }

    own_manual = secret_value or legacy_manual_value
    if auth_mode == "env" and env_value:
        return {
            "auth_mode": auth_mode,
            "api_key_is_configured": True,
            "api_key_status": "configured",
            "api_key_source": "env",
            "api_key_env": env_name,
            "api_key_preview": _mask_secret(env_value),
            "api_key_saved": False,
            "api_key_ref": "",
            "credential_profile_id": linked_profile_id,
            "api_key_status_message": f"Environment key {env_name} is configured.",
        }
    if own_manual:
        preview = _mask_secret(own_manual) or str(connection.get("api_key_preview") or (secret_record or {}).get("preview") or "")
        return {
            "auth_mode": auth_mode,
            "api_key_is_configured": True,
            "api_key_status": "configured",
            "api_key_source": "manual",
            "api_key_saved": True,
            "api_key_ref": profile_ref,
            "api_key_preview": preview,
            "api_key_storage": "local_secret_store",
            "credential_profile_id": linked_profile_id,
            "api_key_status_message": f"Manual API key saved locally · {preview}" if preview else "Manual API key is saved locally.",
        }
    if linked_secret_value:
        linked_ref = _secret_ref_for_profile(linked_profile_id)
        preview = _mask_secret(linked_secret_value) or str((linked_secret_record or {}).get("preview") or "")
        return {
            "auth_mode": auth_mode,
            "api_key_is_configured": True,
            "api_key_status": "configured",
            "api_key_source": "linked_manual",
            "api_key_saved": True,
            "api_key_ref": linked_ref,
            "api_key_preview": preview,
            "api_key_storage": "local_secret_store",
            "credential_profile_id": linked_profile_id,
            "api_key_status_message": f"Credentials shared with {linked_profile_id} · {preview}" if preview else f"Credentials shared with {linked_profile_id}.",
        }
    if auth_mode == "manual" and env_value:
        return {
            "auth_mode": auth_mode,
            "api_key_is_configured": True,
            "api_key_status": "configured",
            "api_key_source": "env_fallback",
            "api_key_env": env_name,
            "api_key_preview": _mask_secret(env_value),
            "api_key_saved": False,
            "api_key_ref": "",
            "credential_profile_id": linked_profile_id,
            "api_key_status_message": f"Environment key {env_name} is configured as a fallback.",
        }
    if auth_mode == "env" and not env_name:
        message = "Environment variable name is missing."
        status_name = "missing_env_name"
    elif linked_profile_id:
        message = f"No API key was found in {linked_profile_id} or {env_name or 'the environment'}."
        status_name = "missing"
    elif auth_mode == "env":
        message = f"Environment key {env_name} is missing."
        status_name = "missing"
    else:
        message = "Manual API key is missing."
        status_name = "missing"
    return {
        "auth_mode": auth_mode,
        "api_key_is_configured": False,
        "api_key_status": status_name,
        "api_key_source": "linked_manual" if linked_profile_id else auth_mode,
        "api_key_env": env_name,
        "api_key_preview": "",
        "api_key_saved": False,
        "api_key_ref": _secret_ref_for_profile(linked_profile_id) if linked_profile_id else profile_ref if auth_mode == "manual" else "",
        "credential_profile_id": linked_profile_id,
        "api_key_status_message": message,
    }


def resolve_backend_profile_api_key(profile: dict[str, Any]) -> dict[str, Any]:
    profile = profile or {}
    connection = profile.get("connection", {}) or {}
    profile_id = str(profile.get("profile_id") or connection.get("api_key_ref") or "")
    status = _connection_api_key_status(connection, profile_id)
    source = str(status.get("api_key_source") or "")
    key_value = ""
    if source in {"env", "env_fallback"}:
        key_value = str(os.getenv(str(connection.get("api_key_env") or "").strip()) or "").strip()
    elif source == "manual":
        key_value = str(connection.get("api_key_value") or "").strip() or _backend_api_key_secret_value(profile_id or status.get("api_key_ref"))
    elif source == "linked_manual":
        linked_profile_id = _linked_credential_profile_id(connection)
        key_value = _backend_api_key_secret_value(linked_profile_id or status.get("api_key_ref"))
    return {**status, "api_key_value": key_value}


def _strip_secret_fields(block: dict[str, Any]) -> dict[str, Any]:
    safe = {**(block or {})}
    safe.pop("api_key_value", None)
    return safe


def _connection_test_severity(status: str) -> str:
    status = str(status or "").strip().lower()
    if status in CONNECTION_TEST_READY_STATUSES:
        return "success"
    if status in {"missing_key", "missing_config", "auth_failed"}:
        return "warning"
    if status in {"offline", "error"}:
        return "danger"
    return "muted"


def _connection_test_next_action(status: str, profile: dict[str, Any]) -> str:
    status = str(status or "").strip().lower()
    connection = profile.get("connection", {}) or {}
    if status == "missing_key":
        auth_status = _connection_api_key_status(connection, profile.get("profile_id"))
        if auth_status.get("api_key_source") == "env":
            return f"Set the {auth_status.get('api_key_env') or connection.get('api_key_env') or 'API key'} environment variable, then restart/test Neo."
        return "Paste a manual API key or switch this profile to environment-variable auth."
    if status == "auth_failed":
        return "Check that the API key belongs to this provider and has access to the selected model/API."
    if status == "missing_config":
        return "Fill the missing base URL/provider configuration, then test again."
    if status == "offline":
        return "Check the base URL, health-check path, internet/local server availability, and timeout."
    if status in CONNECTION_TEST_READY_STATUSES:
        return "Connection is ready for this backend profile."
    return "Run Test Connection after changing this profile."


def _decorate_connection_test_result(profile: dict[str, Any], runtime: dict[str, Any], *, operation: str = "test") -> dict[str, Any]:
    status = str((runtime or {}).get("status") or "missing_config").strip().lower()
    safe_runtime = {**(runtime or {})}
    if isinstance(safe_runtime.get("api_key_status"), dict):
        safe_runtime["api_key_status"] = _strip_secret_fields(safe_runtime.get("api_key_status") or {})
    diagnostic = {
        "operation": operation,
        "status": status,
        "severity": _connection_test_severity(status),
        "reachable": bool(safe_runtime.get("reachable", False)),
        "last_checked": safe_runtime.get("last_checked") or _now_iso(),
        "message": safe_runtime.get("message") or "Connection test completed.",
        "next_action": _connection_test_next_action(status, profile),
        "connection_type": str(profile.get("connection_type") or (profile.get("connection") or {}).get("connection_type") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "surface": str(profile.get("surface") or ""),
        "base_url": safe_runtime.get("base_url") or (profile.get("connection") or {}).get("base_url") or "",
    }
    return {**safe_runtime, "diagnostic": diagnostic}



def backend_profile_store_paths() -> dict[str, str]:
    """Return Backend Profile template/runtime paths for diagnostics and tests."""

    runtime_path = Path(PROFILE_PATH)
    template_path = Path(PROFILE_TEMPLATE_PATH)
    return {
        "runtime_store": True,
        "store_policy": "runtime_profile_store",
        "profile_path": runtime_path.as_posix(),
        "runtime_path": runtime_path.as_posix(),
        "template_path": template_path.as_posix(),
        "repo_template_is_seed_only": True,
        "api_key_secret_store": backend_api_key_secret_store_paths(),
        "api_key_save_load_policy": backend_api_key_save_load_policy(),
        "api_key_migration_policy": backend_api_key_migration_policy(),
    }


def _ensure_profile_payload_exists() -> dict[str, Any]:
    """Ensure the runtime backend profile store exists before read/write.

    Tests may monkeypatch PROFILE_PATH to an isolated temp file; in that case the
    same runtime-store behavior is preserved, but the target is the test file.
    """

    runtime_path = Path(PROFILE_PATH)
    if runtime_path == backend_profile_runtime_path():
        return ensure_backend_profile_store()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    if runtime_path.exists():
        return {
            "ok": True,
            "runtime_store": True,
            "runtime_path": runtime_path.as_posix(),
            "template_path": Path(PROFILE_TEMPLATE_PATH).as_posix(),
            "created_files": [],
            "seeded_from_template": False,
        }
    template_path = PROFILE_TEMPLATE_PATH if PROFILE_TEMPLATE_PATH.exists() else backend_profile_template_path()
    if template_path.exists():
        try:
            payload = json.loads(template_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"profile_registry_version": PROFILE_REGISTRY_VERSION, "defaults": {}, "profiles": []}
    else:
        payload = {"profile_registry_version": PROFILE_REGISTRY_VERSION, "defaults": {}, "profiles": []}
    runtime_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True,
        "runtime_store": True,
        "runtime_path": runtime_path.as_posix(),
        "template_path": template_path.as_posix(),
        "created_files": [runtime_path.as_posix()],
        "seeded_from_template": template_path.exists(),
    }


def _profile_secret_metadata(profile: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("profile_id") or connection.get("api_key_ref") or "").strip()
    status = _connection_api_key_status(connection, profile_id)
    return {
        "api_key_saved": bool(status.get("api_key_saved")),
        "api_key_ref": status.get("api_key_ref") or _secret_ref_for_profile(profile_id),
        "api_key_preview": status.get("api_key_preview") or "",
        "api_key_storage": "local_secret_store",
    }


def _migrate_inline_api_keys_to_secret_store(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, dict):
        return payload, False
    changed = False
    for profile in payload.get("profiles", []) if isinstance(payload.get("profiles"), list) else []:
        if not isinstance(profile, dict):
            continue
        connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
        if not isinstance(connection, dict):
            continue
        profile_id = str(profile.get("profile_id") or connection.get("api_key_ref") or "").strip()
        raw_key = str(connection.get("api_key_value") or "").strip()
        if raw_key and raw_key not in API_KEY_CLEAR_SENTINELS:
            # A legacy inline key is a local/manual secret, even if the older profile
            # was still labelled as env auth. Migrate it into the local secret store
            # and make the active auth mode explicit so restart behavior is stable.
            connection["auth_mode"] = "manual"
            connection["api_key_mode"] = "manual"
            _save_backend_api_key_secret(profile_id, raw_key, profile=profile)
            connection.pop("api_key_value", None)
            connection.update(_profile_secret_metadata(profile, connection))
            profile["connection"] = connection
            changed = True
        elif connection.get("api_key_value") not in {None, ""}:
            connection.pop("api_key_value", None)
            changed = True
        if _normalize_auth_mode(connection.get("auth_mode") or connection.get("api_key_mode"), "none") == "manual":
            metadata = _profile_secret_metadata(profile, connection)
            for key, value in metadata.items():
                if connection.get(key) != value:
                    connection[key] = value
                    changed = True
            profile["connection"] = connection
        elif any(key in connection for key in ("api_key_saved", "api_key_preview", "api_key_ref", "api_key_storage")):
            # Env/none auth should not carry stale manual-secret UI metadata.
            for key in ("api_key_saved", "api_key_preview", "api_key_ref", "api_key_storage"):
                connection.pop(key, None)
            profile["connection"] = connection
            changed = True
    return payload, changed


def backend_api_key_save_load_policy() -> dict[str, Any]:
    return {
        "schema_id": "neo.backend_api_key_save_load_policy.v1",
        "manual_key_save_location": "neo_data/settings/secrets/backend_api_keys.json",
        "profile_metadata_location": "neo_data/settings/backends/backend_profiles.json",
        "blank_password_field_behavior": "preserve_existing_manual_key",
        "manual_key_entry_behavior": "save_to_local_secret_store_and_switch_auth_mode_to_manual",
        "clear_key_behavior": "delete_local_secret_and_mark_missing",
        "env_mode_behavior": "use_environment_variable_only_and_remove_profile_secret_metadata",
        "raw_key_returned_to_frontend": False,
        "raw_key_allowed_in_backend_profiles_json": False,
        "legacy_inline_key_migration": "startup_and_profile_read",
    }


def backend_api_key_migration_policy() -> dict[str, Any]:
    return {
        "schema_id": "neo.backend_api_key_migration_policy.v1",
        "migration_pass": "Pass W",
        "source_field": "connection.api_key_value",
        "runtime_profile_store": "neo_data/settings/backends/backend_profiles.json",
        "secret_store": "neo_data/settings/secrets/backend_api_keys.json",
        "runs_during_runtime_bootstrap": True,
        "runs_during_backend_profile_read": True,
        "repo_template_mutated": False,
        "raw_key_returned_to_frontend": False,
        "raw_key_allowed_after_migration": False,
    }




def backend_selection_persistence_policy() -> dict[str, Any]:
    return {
        "schema_id": "neo.backend_selection_persistence_policy.v1",
        "selection_store": "neo_data/ui_state/ui_state.json",
        "default_store": "neo_data/settings/backends/backend_profiles.json",
        "default_endpoint": "/api/backend-profiles/default",
        "selection_endpoint": "/api/backend-profiles/selection",
        "backend_selection_survives_restart": True,
        "set_default_also_updates_active_selection": True,
        "selection_only_ui_state": True,
        "full_ui_state_autosave_enabled": False,
    }


def _backend_selection_aliases(surface: str | None) -> set[str]:
    surface_id = str(surface or "").strip()
    if not surface_id:
        return set()
    return set(BACKEND_SELECTION_ALIASES.get(surface_id, {surface_id}))


def _profile_available_for_surface(profile: dict[str, Any], surface: str) -> bool:
    if not isinstance(profile, dict) or profile.get("enabled") is False:
        return False
    # Utility-only profiles are selected inside their owning extension. They must
    # never replace the primary backend for a surface or become its default.
    if str(profile.get("profile_role") or "").strip() == "image_background_removal_backend":
        return False
    aliases = _backend_selection_aliases(surface)
    return bool(aliases and str(profile.get("surface") or "") in aliases)


def _normalize_backend_selection_map(selection: dict[str, Any] | None, backend_payload: dict[str, Any] | None = None) -> dict[str, str]:
    backend_payload = backend_payload or _read_payload()
    profiles = backend_payload.get("profiles") if isinstance(backend_payload.get("profiles"), list) else []
    by_id = {str(profile.get("profile_id") or ""): profile for profile in profiles if isinstance(profile, dict)}
    clean: dict[str, str] = {}
    if not isinstance(selection, dict):
        return clean
    for raw_surface, raw_profile_id in selection.items():
        surface = str(raw_surface or "").strip()
        profile_id = str(raw_profile_id or "").strip()
        if not surface or not profile_id:
            continue
        profile = by_id.get(profile_id)
        if profile and _profile_available_for_surface(profile, surface):
            clean[surface] = profile_id
    return clean


def get_backend_profile_selection_payload() -> dict[str, Any]:
    backend_payload = _read_payload()
    saved = read_backend_profile_selection_state()
    saved_map = _normalize_backend_selection_map(saved.get("activeBackendProfileIdsBySurface"), backend_payload)
    defaults = backend_payload.get("defaults") if isinstance(backend_payload.get("defaults"), dict) else {}
    profiles = backend_payload.get("profiles") if isinstance(backend_payload.get("profiles"), list) else []
    # Defaults are not copied into active selections automatically; the frontend
    # resolves fallback defaults. This payload only reports explicit user selections.
    active_id = str(saved.get("activeBackendProfileId") or "")
    if active_id and active_id not in {str(profile.get("profile_id") or "") for profile in profiles if isinstance(profile, dict)}:
        active_id = ""
    return {
        "ok": True,
        "schema_id": "neo.backend_profile_selection.v1",
        "selection": {
            "activeBackendProfileId": active_id,
            "activeBackendProfileIdsBySurface": saved_map,
        },
        "activeBackendProfileId": active_id,
        "activeBackendProfileIdsBySurface": saved_map,
        "defaults": defaults,
        "policy": backend_selection_persistence_policy(),
    }


def save_backend_profile_selection(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    backend_payload = _read_payload()
    existing = read_backend_profile_selection_state().get("activeBackendProfileIdsBySurface") or {}
    merged: dict[str, Any] = {**(existing if isinstance(existing, dict) else {})}
    incoming = payload.get("activeBackendProfileIdsBySurface") or payload.get("selection") or payload.get("selected_profiles_by_surface")
    if isinstance(incoming, dict):
        merged.update(incoming)
    surface = str(payload.get("surface") or "").strip()
    profile_id = str(payload.get("profile_id") or payload.get("activeBackendProfileId") or "").strip()
    if surface and profile_id:
        merged[surface] = profile_id
    clean = _normalize_backend_selection_map(merged, backend_payload)
    if surface and profile_id and clean.get(surface) != profile_id:
        return {"ok": False, "errors": [f"Profile {profile_id} is not available for surface {surface}"]}
    active_id = profile_id if profile_id in set(clean.values()) else str(payload.get("activeBackendProfileId") or "").strip()
    write_backend_profile_selection_state({
        "activeBackendProfileId": active_id,
        "activeBackendProfileIdsBySurface": clean,
    })
    return get_backend_profile_selection_payload()


def _set_default_profile_in_payload(payload: dict[str, Any], surface: str, profile_id: str) -> bool:
    profiles = payload.get("profiles", []) if isinstance(payload.get("profiles"), list) else []
    requested = next((profile for profile in profiles if isinstance(profile, dict) and str(profile.get("profile_id") or "") == profile_id), None)
    if requested and str(requested.get("profile_role") or "").strip() == "image_background_removal_backend":
        return False
    matched = False
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if profile.get("surface") == surface:
            profile["is_default"] = profile.get("profile_id") == profile_id
            if profile.get("is_default"):
                matched = True
    if matched:
        payload.setdefault("defaults", {})[surface] = profile_id
    return matched

def _apply_api_key_save_load_rules(
    profile_id: str,
    profile: dict[str, Any],
    connection_updates: dict[str, Any],
    existing_connection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply Neo's API key persistence contract to a connection update.

    Rules locked by Pass T:
    - A non-empty manual key is stored in the local secret store, never in profile JSON.
    - Blank password field/update preserves an existing manual key.
    - Clear removes the local secret and marks the profile missing.
    - Switching to env/none stops using local/manual key metadata and removes any local secret.
    """

    updates = {**(connection_updates or {})}
    existing = {**(existing_connection or {})}
    raw_value_present = "api_key_value" in updates
    raw_value = str(updates.pop("api_key_value", "") or "").strip()
    explicit_auth_change = "auth_mode" in updates or "api_key_mode" in updates
    requested_auth_mode = _normalize_auth_mode(
        updates.get("auth_mode")
        or updates.get("api_key_mode")
        or existing.get("auth_mode")
        or existing.get("api_key_mode"),
        "none",
    )
    updates["auth_mode"] = requested_auth_mode
    updates["api_key_mode"] = requested_auth_mode

    should_clear_key = bool(updates.pop("api_key_clear", False)) or raw_value in API_KEY_CLEAR_SENTINELS
    secret_profile = {**(profile or {}), "profile_id": profile_id}

    if should_clear_key:
        _delete_backend_api_key_secret(profile_id)
        updates.update({
            "api_key_saved": False,
            "api_key_preview": "",
            "api_key_ref": _secret_ref_for_profile(profile_id),
            "api_key_storage": "local_secret_store",
            "api_key_status_message": "Manual API key is missing.",
        })
        return updates

    if raw_value:
        updates["auth_mode"] = "manual"
        updates["api_key_mode"] = "manual"
        updates.update(_save_backend_api_key_secret(profile_id, raw_value, profile=secret_profile))
        updates["api_key_storage"] = "local_secret_store"
        updates["api_key_status_message"] = f"Manual API key saved locally · {updates.get('api_key_preview') or 'masked'}"
        return updates

    if explicit_auth_change and requested_auth_mode in {"env", "none"}:
        # Explicitly leaving manual mode means Neo should not keep/advertise a local key.
        _delete_backend_api_key_secret(profile_id)
        for key in ("api_key_saved", "api_key_preview", "api_key_ref", "api_key_storage", "api_key_status_message"):
            updates.pop(key, None)
        return updates

    if requested_auth_mode == "manual":
        # Blank password field: keep existing key and refresh safe metadata only.
        merged_for_status = {**existing, **updates, "auth_mode": "manual", "api_key_mode": "manual"}
        updates.update(_profile_secret_metadata(secret_profile, merged_for_status))

    # Never allow raw key values into backend_profiles.json.
    updates.pop("api_key_value", None)
    return updates


def _merge_missing_seeded_profiles_from_template(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Add newly shipped seeded profiles without overwriting user runtime profiles.

    Backend profile runtime data is intentionally copied out of the repository only
    once. New Neo releases can still ship additional optional surface bindings (for
    example the P3 linked Grok Video profile), so this narrow reconciliation adds
    only template profiles explicitly marked ``seeded_profile`` when their profile
    IDs do not already exist. User edits, defaults, and existing profiles are never
    replaced.
    """
    payload = dict(payload or {})
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), list) else []
    existing_ids = {
        str(profile.get("profile_id") or "").strip()
        for profile in profiles
        if isinstance(profile, dict) and str(profile.get("profile_id") or "").strip()
    }
    try:
        template = json.loads(Path(PROFILE_TEMPLATE_PATH).read_text(encoding="utf-8"))
    except Exception:
        return payload, []
    added: list[str] = []
    for candidate in template.get("profiles", []) if isinstance(template.get("profiles"), list) else []:
        if not isinstance(candidate, dict) or candidate.get("seeded_profile") is not True:
            continue
        profile_id = str(candidate.get("profile_id") or "").strip()
        if not profile_id or profile_id in existing_ids:
            continue
        profiles.append(json.loads(json.dumps(candidate, ensure_ascii=False, default=str)))
        existing_ids.add(profile_id)
        added.append(profile_id)
    payload["profiles"] = profiles
    if added:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {
            **metadata,
            "last_seed_profile_reconciliation": _now_iso(),
            "last_seed_profiles_added": added,
        }
    return payload, added


def _read_payload() -> dict[str, Any]:
    _ensure_profile_payload_exists()
    profile_path = Path(PROFILE_PATH)
    if not profile_path.exists():
        return {"profile_registry_version": PROFILE_REGISTRY_VERSION, "defaults": {}, "profiles": []}
    raw_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    raw_payload, migrated = _migrate_inline_api_keys_to_secret_store(raw_payload)
    raw_payload, seeded_added = _merge_missing_seeded_profiles_from_template(raw_payload)
    if migrated or seeded_added:
        profile_path.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return _normalize_profile_payload(raw_payload)


def _write_payload(payload: dict[str, Any]) -> None:
    _ensure_profile_payload_exists()
    normalized = _normalize_profile_payload(payload)
    sanitized, _ = _migrate_inline_api_keys_to_secret_store(normalized)
    for profile in sanitized.get("profiles", []) if isinstance(sanitized.get("profiles"), list) else []:
        if isinstance(profile, dict) and isinstance(profile.get("connection"), dict):
            profile["connection"].pop("api_key_value", None)
            if _normalize_auth_mode(profile["connection"].get("auth_mode") or profile["connection"].get("api_key_mode"), "none") == "manual":
                profile["connection"].update(_profile_secret_metadata(profile, profile["connection"]))
    profile_path = Path(PROFILE_PATH)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(sanitized, indent=2, ensure_ascii=False), encoding="utf-8")
    get_backend_profile_payload.cache_clear()


def _read_provider_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"providers": []}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"providers": []}


@lru_cache(maxsize=1)
def _provider_manifest_by_id() -> dict[str, dict[str, Any]]:
    providers = _read_provider_manifest().get("providers", [])
    return {
        str(provider.get("provider_id") or ""): provider
        for provider in providers
        if isinstance(provider, dict) and provider.get("provider_id")
    }


def _provider_manifest_for(provider_id: str) -> dict[str, Any]:
    return _provider_manifest_by_id().get(str(provider_id or ""), {})


def _provider_manifest_surfaces(provider_manifest: dict[str, Any] | None) -> list[str]:
    surfaces = (provider_manifest or {}).get("surfaces") or []
    return [str(surface).strip() for surface in surfaces if str(surface or "").strip()]


def _provider_surface_template(provider_manifest: dict[str, Any] | None, surface: str) -> dict[str, Any]:
    templates = (provider_manifest or {}).get("profile_templates") or {}
    if not isinstance(templates, dict):
        return {}
    template = templates.get(surface) or {}
    return template if isinstance(template, dict) else {}


def _provider_default_profile_template(provider_manifest: dict[str, Any] | None, surface: str) -> dict[str, Any]:
    template = _provider_surface_template(provider_manifest, surface)
    default_profile = template.get("default_profile") or {}
    return default_profile if isinstance(default_profile, dict) else {}


def _deep_merge_dicts(*blocks: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _deep_merge_dicts(merged[key], value)
            elif isinstance(value, dict):
                merged[key] = _deep_merge_dicts(value)
            elif isinstance(value, list):
                merged[key] = list(value)
            else:
                merged[key] = value
    return merged


def list_backend_provider_options(surface: str | None = None) -> dict[str, Any]:
    """Return Admin-friendly provider/template options.

    Phase B keeps runtime adapters untouched. This payload is for Admin profile
    creation: Surface -> Provider -> default connection/model/capability template.
    """
    payload = _read_provider_manifest()
    requested_surface = str(surface or "").strip()
    providers: list[dict[str, Any]] = []
    surfaces_seen: set[str] = set()
    for provider in payload.get("providers", []):
        if not isinstance(provider, dict):
            continue
        provider_surfaces = _provider_manifest_surfaces(provider)
        surfaces_seen.update(provider_surfaces)
        if requested_surface and requested_surface not in provider_surfaces:
            continue
        options_for_surfaces = [requested_surface] if requested_surface else provider_surfaces
        for item_surface in options_for_surfaces:
            template = _provider_surface_template(provider, item_surface)
            default_profile = _provider_default_profile_template(provider, item_surface)
            providers.append({
                "provider_id": provider.get("provider_id"),
                "display_name": template.get("display_name") or provider.get("display_name"),
                "provider_label": default_profile.get("provider_label") or template.get("display_name") or provider.get("display_name"),
                "provider_type": provider.get("provider_type"),
                "surface": item_surface,
                "surfaces": provider_surfaces,
                "status": provider.get("status"),
                "connection_type": template.get("connection_type") or provider.get("connection_kind"),
                "connection_kind": provider.get("connection_kind"),
                "supported_modes": template.get("supported_modes") or provider.get("supported_modes") or [],
                "supported_families": template.get("supported_families") or provider.get("supported_families") or [],
                "supported_loaders": template.get("supported_loaders") or provider.get("supported_loaders") or [],
                "model_options": (default_profile.get("model") or {}).get("available_models") or [],
                "default_model": (default_profile.get("model") or {}).get("default_model") or "",
                "template": default_profile,
                "notes": template.get("notes") or provider.get("notes") or "",
            })
    return {
        "provider_registry_version": payload.get("provider_registry_version"),
        "surfaces": sorted(surfaces_seen),
        "providers": providers,
    }


def _infer_surface(profile: dict[str, Any], provider_manifest: dict[str, Any] | None = None) -> str:
    surface = str(profile.get("surface") or "").strip()
    if surface:
        return surface
    role = str(profile.get("profile_role") or "").casefold()
    provider_id = str(profile.get("provider_id") or "").casefold()
    profile_id = str(profile.get("profile_id") or "").casefold()
    if "image" in role or provider_id in {"comfyui", "comfyui_portable", "forge", "a1111"}:
        return "image"
    if "video" in role or profile_id.startswith("video."):
        return "video"
    if "voice" in role or profile_id.startswith("voice."):
        return "voice"
    if "audio" in role or profile_id.startswith("audio."):
        return "audio"
    surfaces = (provider_manifest or {}).get("surfaces") or []
    if isinstance(surfaces, list) and surfaces:
        return str(surfaces[0] or "text")
    return "text"


def _infer_connection_type(profile: dict[str, Any], provider_manifest: dict[str, Any] | None = None) -> str:
    connection = profile.get("connection", {}) or {}
    existing = str(profile.get("connection_type") or connection.get("connection_type") or "").strip()
    if existing:
        return existing
    kind = str(connection.get("kind") or (provider_manifest or {}).get("connection_kind") or "").strip().lower()
    provider_type = str((provider_manifest or {}).get("provider_type") or "").strip().lower()
    if kind in CLOUD_CONNECTION_KINDS or "cloud" in provider_type:
        return "cloud_api"
    if kind in LOCAL_PROCESS_CONNECTION_KINDS or "process" in kind:
        return "local_process_or_http"
    if kind in LOCAL_CONNECTION_KINDS or "local" in provider_type or str(profile.get("provider_id") or ""):
        return "local_http"
    return "unknown"


def _profile_role_for(surface: str, connection_type: str, provider_id: str) -> str:
    if surface == "image":
        return "image_api_backend" if connection_type == "cloud_api" else "image_generation_backend"
    if surface == "video":
        return "video_generation_backend"
    if surface == "voice":
        return "voice_backend"
    if surface == "audio":
        return "audio_backend"
    if surface in {"assistant", "roleplay", "prompt_captioning", "text"}:
        return "text_backend"
    return f"{surface or 'unknown'}_backend"


def _default_capability_flags(surface: str, provider_id: str, connection_type: str) -> dict[str, Any]:
    if surface == "image":
        local_image = connection_type != "cloud_api"
        return {
            "txt2img": True,
            "img2img": True,
            "inpaint": local_image,
            "outpaint": local_image,
            "mask_edit": local_image,
            "negative_prompt": local_image,
            "seed": local_image,
            "steps": local_image,
            "cfg": local_image,
            "sampler": local_image,
            "scheduler": local_image,
            "lora": local_image,
            "controlnet": local_image,
            "ip_adapter": local_image,
            "adetailer_inline": local_image,
            "highres_inline": local_image,
        }
    if surface == "video":
        return {"txt2video": True, "img2video": True, "queue_jobs": True}
    if surface == "voice":
        return {"tts": True, "preview": True, "render": True}
    if surface == "audio":
        return {"song": True, "instrumental": True, "sfx": False, "queue_jobs": True}
    return {
        "supports_text": True,
        "supports_vision": False,
        "supports_captioning": False,
        "streaming_enabled": False,
    }


def _default_generation_defaults(surface: str) -> dict[str, Any]:
    if surface == "image":
        return {"mode": "generate", "width": 1024, "height": 1024, "batch_size": 1}
    if surface == "video":
        return {"workflow_mode": "text_to_video", "output_format": "mp4", "fps": 24, "duration_seconds": 4}
    if surface == "voice":
        return {"job_lane": "generate_speech", "language": "auto", "preview_seconds": 12}
    if surface == "audio":
        return {"lane": "song", "duration_seconds": 30, "output_format": "wav"}
    return {"max_tokens": 512, "temperature": 0.7, "top_p": 0.9, "stop_sequences": []}


def _ui_visibility_for(surface: str, connection_type: str, provider_id: str) -> dict[str, Any]:
    is_cloud = connection_type == "cloud_api"
    is_local_process = connection_type == "local_process_or_http"
    is_local = connection_type in {"local_http", "local_process_or_http"}
    return {
        "show_local_launch_fields": bool(is_local_process),
        "show_local_http_fields": bool(is_local),
        "show_api_auth_fields": bool(is_cloud),
        "show_model_fields": True,
        "show_sd_generation_fields": bool(surface == "image" and not is_cloud),
        "show_cloud_generation_fields": bool(surface == "image" and is_cloud),
        "show_text_generation_fields": bool(surface in {"text", "assistant", "roleplay", "prompt_captioning"}),
    }


def _normalize_connection(connection: dict[str, Any], connection_type: str, provider_manifest: dict[str, Any] | None = None, surface: str = "", profile_id: str = "") -> dict[str, Any]:
    provider_manifest = provider_manifest or {}
    template_connection = (_provider_default_profile_template(provider_manifest, surface).get("connection") or {}) if surface else {}
    config_schema = provider_manifest.get("config_schema", {}) or {}
    auth_schema = provider_manifest.get("auth", {}) or {}
    result = _deep_merge_dicts(template_connection, connection or {})
    result.setdefault("kind", provider_manifest.get("connection_kind") or connection_type)
    result["connection_type"] = connection_type
    if connection_type == "cloud_api":
        result.setdefault("base_url", provider_manifest.get("default_base_url") or config_schema.get("base_url") or result.get("base_url") or "")
        auth_mode = _normalize_auth_mode(result.get("auth_mode") or result.get("api_key_mode") or auth_schema.get("default_auth_mode"), "env")
        result["auth_mode"] = auth_mode
        result["api_key_mode"] = auth_mode
        result.setdefault("api_key_env", auth_schema.get("default_env_key") or result.get("api_key_env") or "")
        if _normalize_auth_mode(result.get("auth_mode") or result.get("api_key_mode"), "none") == "manual":
            result.update(_profile_secret_metadata({"profile_id": profile_id, "provider_id": provider_manifest.get("provider_id") or "", "surface": surface}, result))
        result.update(_connection_api_key_status(result, profile_id))
        result.setdefault("timeout_seconds", config_schema.get("timeout_seconds") or 120)
    elif connection_type in {"local_http", "local_process_or_http"}:
        result.setdefault("base_url", config_schema.get("base_url") or result.get("base_url") or "")
        result.setdefault("auto_connect", False)
        result.setdefault("auto_start", False)
        result.setdefault("portable_path", "")
        result.setdefault("launch_command", "")
        result.setdefault("timeout_seconds", config_schema.get("timeout_seconds") or 30)
        auth_mode = _normalize_auth_mode(result.get("auth_mode") or result.get("api_key_mode"), "none")
        result["auth_mode"] = auth_mode
        result["api_key_mode"] = auth_mode
        result.setdefault("api_key_env", "")
        result.update(_connection_api_key_status(result, profile_id))
    else:
        result.setdefault("timeout_seconds", config_schema.get("timeout_seconds") or 30)
    return result


def _normalize_profile_schema(profile: dict[str, Any]) -> dict[str, Any]:
    """Return a universal backend profile while preserving legacy keys.

    Phase A is intentionally compatibility-first: old consumers can still read
    `capability_flags` and `generation_defaults`, while new Admin/profile UI can
    rely on `connection_type`, `capabilities`, `defaults`, `model`, `ui`, and
    `metadata` existing on every profile.
    """
    if not isinstance(profile, dict):
        profile = {}
    provider_id = str(profile.get("provider_id") or "koboldcpp").strip() or "koboldcpp"
    provider_manifest = _provider_manifest_for(provider_id)
    surface = _infer_surface(profile, provider_manifest)
    profile_id = str(profile.get("profile_id") or f"{surface}.{provider_id}").strip()
    connection_type = _infer_connection_type(profile, provider_manifest)
    provider_default_profile = _provider_default_profile_template(provider_manifest, surface)
    connection = _normalize_connection(profile.get("connection", {}) or {}, connection_type, provider_manifest, surface, profile_id)
    template_capabilities = provider_default_profile.get("capabilities") or provider_default_profile.get("capability_flags") or {}
    capability_flags = {
        **_default_capability_flags(surface, provider_id, connection_type),
        **(template_capabilities if isinstance(template_capabilities, dict) else {}),
        **(profile.get("capability_flags") or {}),
        **(profile.get("capabilities") or {}),
    }
    template_defaults = provider_default_profile.get("defaults") or provider_default_profile.get("generation_defaults") or {}
    generation_defaults = {
        **_default_generation_defaults(surface),
        **(template_defaults if isinstance(template_defaults, dict) else {}),
        **(profile.get("generation_defaults") or {}),
        **(profile.get("defaults") or {}),
    }
    template_model = provider_default_profile.get("model") or {}
    model_block = {
        "default_model": connection.get("model") or (profile.get("model") or {}).get("default_model") or template_model.get("default_model") or generation_defaults.get("model") or "",
        "available_models": (profile.get("model") or {}).get("available_models") or template_model.get("available_models") or [],
    }
    metadata = {
        "schema_version": PROFILE_REGISTRY_VERSION,
        "created_at": "",
        "updated_at": profile.get("metadata", {}).get("updated_at", "") if isinstance(profile.get("metadata"), dict) else "",
        "notes": profile.get("notes") or "",
        **(profile.get("metadata") or {} if isinstance(profile.get("metadata"), dict) else {}),
    }
    template_ui = provider_default_profile.get("ui") or {}
    ui = {
        **_ui_visibility_for(surface, connection_type, provider_id),
        **(template_ui if isinstance(template_ui, dict) else {}),
        **(profile.get("ui") or {} if isinstance(profile.get("ui"), dict) else {}),
    }
    normalized = {
        **profile,
        "profile_id": profile_id,
        "display_name": profile.get("display_name") or str(profile.get("profile_id") or provider_id).replace("_", " ").replace(".", " · ").title(),
        "provider_id": provider_id,
        "provider_label": profile.get("provider_label") or provider_manifest.get("display_name") or provider_id.replace("_", " ").title(),
        "surface": surface,
        "profile_role": profile.get("profile_role") or _profile_role_for(surface, connection_type, provider_id),
        "connection_type": connection_type,
        "enabled": bool(profile.get("enabled", True)),
        "is_default": bool(profile.get("is_default", False)),
        "connection": connection,
        "capability_flags": capability_flags,
        "capabilities": capability_flags,
        "generation_defaults": generation_defaults,
        "defaults": generation_defaults,
        "model": model_block,
        "ui": ui,
        "metadata": metadata,
        "notes": profile.get("notes") or metadata.get("notes") or "",
    }
    return normalized


def _normalize_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
    profiles = [_normalize_profile_schema(profile) for profile in raw_profiles if isinstance(profile, dict)]
    defaults = dict((payload or {}).get("defaults") or {})
    for profile in profiles:
        surface = profile.get("surface") or "text"
        if profile.get("is_default"):
            defaults[surface] = profile.get("profile_id")
    return {
        **(payload or {}),
        "profile_registry_version": PROFILE_REGISTRY_VERSION,
        "defaults": defaults,
        "profiles": profiles,
    }


def _status_value(status_payload: Any) -> str:
    if isinstance(status_payload, dict):
        return str(status_payload.get("status") or "unknown")
    if status_payload is None:
        return "unknown"
    return str(status_payload)


def _empty_models() -> dict[str, list[dict[str, Any]]]:
    return {
        "models": [],
        "diffusion_models": [],
        "text_encoders": [],
        "qwen_text_encoders": [],
        "vaes": [],
        "samplers": [],
        "schedulers": [],
        "gguf_models": [],
        "gguf_text_encoders": [],
        "gguf_text_encoder_primary": [],
        "gguf_text_encoder_secondary": [],
        "gguf_vaes": [],
        "mmproj": [],
        "loras": [],
        "embeddings": [],
        "ip_adapter_models": [],
        "clip_vision_models": [],
        "ip_adapter_faceid_models": [],
        "upscalers": [],
        "text_models": [],
        "vision_models": [],
    }


def _safe_connection_for_ui(connection: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    safe = {**(connection or {})}
    safe.update(_connection_api_key_status(safe, profile_id))
    safe.pop("api_key_value", None)
    return safe


def _split_model_records(records: Any) -> dict[str, list[dict[str, Any]]]:
    buckets = _empty_models()
    if isinstance(records, dict):
        for key in buckets:
            values = records.get(key) or []
            buckets[key] = values if isinstance(values, list) else []
        return buckets
    if not isinstance(records, list):
        return buckets
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind == "vae":
            buckets["vaes"].append(record)
        elif kind in {"diffusion_model", "unet"}:
            buckets["diffusion_models"].append(record)
            buckets["models"].append(record)
        elif kind in {"text_encoder", "clip"}:
            buckets["text_encoders"].append(record)
            if _is_qwen_text_encoder_asset(str(record.get("name") or "")):
                buckets["qwen_text_encoders"].append(record)
        elif kind == "qwen_text_encoder":
            buckets["qwen_text_encoders"].append(record)
            buckets["text_encoders"].append(record)
        elif kind == "sampler":
            buckets["samplers"].append(record)
        elif kind == "scheduler":
            buckets["schedulers"].append(record)
        elif kind in {"gguf_model", "gguf_unet"}:
            buckets["gguf_models"].append(record)
        elif kind in {"gguf_text_encoder", "gguf_clip"}:
            buckets["gguf_text_encoders"].append(record)
        elif kind in {"gguf_text_encoder_primary", "gguf_clip_primary"}:
            buckets["gguf_text_encoder_primary"].append(record)
        elif kind in {"gguf_text_encoder_secondary", "gguf_clip_secondary"}:
            buckets["gguf_text_encoder_secondary"].append(record)
        elif kind in {"gguf_vae", "gguf_vae_or_ae"}:
            buckets["gguf_vaes"].append(record)
        elif kind == "mmproj":
            buckets["mmproj"].append(record)
        elif kind in {"lora", "loras"}:
            buckets["loras"].append(record)
        elif kind in {"embedding", "embeddings", "textual_inversion", "textual-inversion", "ti"}:
            buckets["embeddings"].append(record)
        elif kind in {"ip_adapter", "ipadapter", "ip_adapter_model", "ipadapter_model"}:
            buckets["ip_adapter_models"].append(record)
        elif kind in {"clip_vision", "clipvision", "clip_vision_model"}:
            buckets["clip_vision_models"].append(record)
        elif kind in {"ip_adapter_faceid", "ipadapter_faceid", "faceid_ip_adapter"}:
            buckets["ip_adapter_faceid_models"].append(record)
        elif kind in {"upscaler", "upscalers", "upscale_model", "upscale_models", "esrgan"}:
            buckets["upscalers"].append(record)
        else:
            buckets["models"].append(record)
    return buckets


def _http_get_json(base_url: str, path: str, timeout: float = 3.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_model_names_from_endpoint_payload(payload: Any) -> list[str]:
    """Normalize Comfy `/models/<folder>` style responses into model names.

    Comfy installs and custom launchers differ here: some return a raw list of
    strings, some return dict records, and some wrap the list under `models`,
    `files`, or a folder key. High-Res Lab must not depend only on
    `/object_info/UpscaleModelLoader` because some installs expose the node but
    keep the choices empty until model folders are queried directly.
    """
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in (
            "models", "files", "items", "checkpoints", "checkpoint",
            "diffusion_models", "diffusion_model", "unets", "unet",
            "text_encoders", "text_encoder", "clip", "clips",
            "vaes", "vae", "loras", "lora",
            "upscalers", "upscale_models", "upscale_model",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    elif isinstance(payload, list):
        candidates.extend(payload)

    names: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            raw = item.get("name") or item.get("filename") or item.get("file") or item.get("path")
        else:
            raw = item
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _discover_comfy_model_folder_names(base_url: str, folder_names: list[str], timeout: float = 3.0) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for folder in folder_names:
        try:
            payload = _http_get_json(base_url, f"/models/{folder}", timeout=timeout)
        except Exception:
            continue
        for name in _extract_model_names_from_endpoint_payload(payload):
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _scan_conventional_comfy_upscale_model_folders(backend_details: dict[str, Any] | None = None) -> list[str]:
    """Filesystem fallback for portable Comfy installs.

    V1 effectively read Comfy's upscaler catalog. In V2, if `/object_info` and
    `/models/upscale_models` do not expose the list, scan only conventional
    model folders derived from portable_path/cwd. No user-specific paths are
    hardcoded.
    """
    details = backend_details or {}
    roots: list[Path] = []
    portable_path = str(details.get("portable_path") or "").strip()
    if portable_path:
        root = Path(portable_path).expanduser()
        roots.extend([root, root / "ComfyUI"])
    cwd = Path.cwd()
    roots.extend([cwd, cwd / "ComfyUI"])

    folders: list[Path] = []
    for root in roots:
        folders.extend([
            root / "models" / "upscale_models",
            root / "models" / "upscalers",
            root / "models" / "ESRGAN",
            root / "models" / "esrgan",
        ])

    names: list[str] = []
    seen: set[str] = set()
    suffixes = {".pth", ".pt", ".safetensors", ".ckpt", ".bin"}
    for folder in folders:
        if not folder.exists() or not folder.is_dir():
            continue
        try:
            files = [item for item in folder.rglob("*") if item.is_file() and item.suffix.casefold() in suffixes]
        except Exception:
            continue
        for file_path in sorted(files, key=lambda item: str(item).casefold()):
            try:
                name = file_path.relative_to(folder).as_posix()
            except Exception:
                name = file_path.name
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _extract_comfy_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip() for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _node_required_choices(object_info: dict[str, Any], node_name: str, *input_names: str) -> list[str]:
    required = (((object_info.get(node_name) or {}).get("input") or {}).get("required") or {})
    optional = (((object_info.get(node_name) or {}).get("input") or {}).get("optional") or {})
    merged: list[str] = []
    seen: set[str] = set()
    for input_name in input_names:
        for item in _extract_comfy_choices(required.get(input_name)) + _extract_comfy_choices(optional.get(input_name)):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _merged_node_choices(object_info: dict[str, Any], aliases: list[str], *input_names: str) -> list[str]:
    """Merge model choices from all compatible Comfy loader aliases."""

    merged: list[str] = []
    seen: set[str] = set()
    for node_name in aliases:
        if node_name not in object_info:
            continue
        for item in _node_required_choices(object_info, node_name, *input_names):
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _first_existing_node(object_info: dict[str, Any], aliases: list[str]) -> str:
    return next((alias for alias in aliases if alias in object_info), "")


def _is_mmproj_asset(value: str) -> bool:
    lowered = str(value or "").casefold()
    return bool(
        lowered
        and (
            "mmproj" in lowered
            or "mm_proj" in lowered
            or "mm-proj" in lowered
            or ("vision" in lowered and ("qwen" in lowered or "image" in lowered))
            or ("projector" in lowered and ("qwen" in lowered or "image" in lowered))
        )
    )


def _is_qwen_text_encoder_asset(value: str) -> bool:
    lowered = str(value or "").casefold()
    return bool(("qwen" in lowered or "qw" in lowered) and not _is_mmproj_asset(value))


def _record(kind: str, name: str, *, source: str = "comfy_object_info") -> dict[str, Any]:
    return {"kind": kind, "name": name, "source": source}


def _append_unique(bucket: list[dict[str, Any]], kind: str, names: list[str], *, source: str = "comfy_object_info") -> None:
    seen = {str(item.get("name") or "").casefold() for item in bucket}
    for name in names:
        key = str(name or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        bucket.append(_record(kind, str(name), source=source))


def _discover_comfy_models(base_url: str, timeout: float = 3.0, backend_details: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    try:
        info = _http_get_json(base_url, "/object_info", timeout=timeout)
    except Exception:
        return _empty_models()

    buckets = _empty_models()
    checkpoint_inputs = (((info.get("CheckpointLoaderSimple") or {}).get("input") or {}).get("required") or {})
    checkpoint_names = checkpoint_inputs.get("ckpt_name", [[]])[0] if checkpoint_inputs.get("ckpt_name") else []
    for name in checkpoint_names:
        buckets["models"].append({"kind": "checkpoint", "name": name})

    diffusion_model_names = _merged_node_choices(
        info,
        ["UNETLoader", "DiffusionModelLoader", "LoadDiffusionModel"],
        "unet_name", "model_name", "diffusion_model_name",
    )
    if not diffusion_model_names:
        diffusion_model_names = _discover_comfy_model_folder_names(base_url, ["diffusion_models", "unet", "unets"], timeout=timeout)
    _append_unique(buckets["diffusion_models"], "diffusion_model", diffusion_model_names)
    _append_unique(buckets["models"], "diffusion_model", diffusion_model_names)

    text_encoder_names = _merged_node_choices(
        info,
        ["CLIPLoader", "DualCLIPLoader", "TextEncoderLoader", "LoadCLIP"],
        "clip_name", "clip_name1", "clip_name2", "text_encoder_name", "text_encoder_name1", "text_encoder_name2",
    )
    if not text_encoder_names:
        text_encoder_names = _discover_comfy_model_folder_names(base_url, ["text_encoders", "clip", "clips"], timeout=timeout)
    _append_unique(buckets["text_encoders"], "text_encoder", text_encoder_names)
    _append_unique(buckets["qwen_text_encoders"], "qwen_text_encoder", [item for item in text_encoder_names if _is_qwen_text_encoder_asset(item)])

    vae_names = _merged_node_choices(info, ["VAELoader", "LoadVAE"], "vae_name", "model_name")
    if not vae_names:
        vae_names = _discover_comfy_model_folder_names(base_url, ["vae", "vaes"], timeout=timeout)
    _append_unique(buckets["vaes"], "vae", vae_names)

    lora_node = _first_existing_node(info, ["LoraLoader", "LoraLoaderModelOnly"])
    lora_names = _node_required_choices(info, lora_node, "lora_name") if lora_node else []
    _append_unique(buckets["loras"], "lora", lora_names)

    # Built-in IP Adapter catalog extraction. These buckets are dedicated so the
    # Reference extension never mixes checkpoints, LoRAs, or GGUF assets into
    # IP Adapter-specific dropdowns.
    clip_vision_node = _first_existing_node(info, ["CLIPVisionLoader", "CLIPVisionLoaderModelOnly"])
    clip_vision_names = _node_required_choices(info, clip_vision_node, "clip_name", "clip_vision_name", "model_name") if clip_vision_node else []
    _append_unique(buckets["clip_vision_models"], "clip_vision", clip_vision_names)

    ip_adapter_node = _first_existing_node(info, ["IPAdapterModelLoader", "IPAdapterUnifiedLoader", "IPAdapterLoader"])
    ip_adapter_names = _node_required_choices(info, ip_adapter_node, "ipadapter_file", "ipadapter_name", "model", "model_name", "name") if ip_adapter_node else []
    _append_unique(buckets["ip_adapter_models"], "ip_adapter", ip_adapter_names)

    faceid_node = _first_existing_node(info, ["IPAdapterUnifiedLoaderFaceID", "IPAdapterFaceIDModelLoader"])
    faceid_names = _node_required_choices(info, faceid_node, "model", "model_name", "ipadapter_file", "faceid_model") if faceid_node else []
    _append_unique(buckets["ip_adapter_faceid_models"], "ip_adapter_faceid", faceid_names)

    # FIX4/FIX5: Some IPAdapter Plus FaceID nodes expose presets through /object_info
    # instead of real model file names. Merge configured Comfy extra_model_paths.yaml
    # scans without hardcoding any user-specific filesystem paths.
    fs_inputs = discover_extra_model_path_inputs(backend_details or {})
    _append_unique(buckets["clip_vision_models"], "clip_vision", fs_inputs.get("clip_vision", []), source="extra_model_paths_yaml")
    _append_unique(buckets["ip_adapter_models"], "ip_adapter", fs_inputs.get("ip_adapter", []), source="extra_model_paths_yaml")
    _append_unique(buckets["ip_adapter_faceid_models"], "ip_adapter_faceid", fs_inputs.get("faceid", []), source="extra_model_paths_yaml")

    # V1 Upscale Lab/Image Upscale populated the upscaler dropdown from the
    # provider catalog key `upscale_models`/`upscalers`. In Comfy this comes
    # from UpscaleModelLoader.model_name choices. Keep it in a dedicated bucket
    # so High-Res Lab can show ESRGAN/upscale models without mixing them into
    # checkpoints or general model selectors.
    upscale_node = _first_existing_node(info, ["UpscaleModelLoader", "UpscaleModelLoaderProvider"])
    upscale_names = _node_required_choices(info, upscale_node, "model_name", "upscale_model_name", "upscaler_name", "name") if upscale_node else []
    # Hotfix: some Comfy installs expose UpscaleModelLoader but do not populate
    # object_info choices until the model folder endpoint is queried directly.
    # Use V1-compatible folder keys before falling back to conventional portable
    # model folders. Missing lists should never hide the High-Res Lab selector.
    upscale_names.extend(_discover_comfy_model_folder_names(base_url, ["upscale_models", "upscalers", "ESRGAN", "esrgan"], timeout=timeout))
    upscale_names.extend(_scan_conventional_comfy_upscale_model_folders(backend_details))
    _append_unique(buckets["upscalers"], "upscaler", upscale_names)

    # Phase 12.10D: migrate the V1 GGUF catalog method. GGUF assets are pulled
    # from the GGUF loader node object_info choices, not from the normal
    # checkpoint bucket. This prevents the GGUF model dropdown from showing SDXL
    # checkpoint files.
    gguf_unet_node = _first_existing_node(info, ["UnetLoaderGGUF", "LoaderGGUF"])
    gguf_single_clip_node = _first_existing_node(info, ["CLIPLoaderGGUF", "ClipLoaderGGUF"])
    gguf_dual_clip_node = _first_existing_node(info, ["DualCLIPLoaderGGUF"])
    gguf_vae_node = _first_existing_node(info, ["VaeGGUF", "VAELoaderGGUF"])

    gguf_unet_choices = _node_required_choices(info, gguf_unet_node, "unet_name", "model_name", "gguf_name") if gguf_unet_node else []
    gguf_single_clip_choices = _node_required_choices(info, gguf_single_clip_node, "clip_name", "clip_name1", "text_encoder_name") if gguf_single_clip_node else []
    gguf_dual_a_choices = _node_required_choices(info, gguf_dual_clip_node, "clip_name1", "text_encoder_name", "text_encoder_name1") if gguf_dual_clip_node else []
    gguf_dual_b_choices = _node_required_choices(info, gguf_dual_clip_node, "clip_name2", "text_encoder_name2") if gguf_dual_clip_node else []
    gguf_vae_choices = _node_required_choices(info, gguf_vae_node, "vae_name", "gguf_name") if gguf_vae_node else []

    _append_unique(buckets["gguf_models"], "gguf_model", gguf_unet_choices)
    _append_unique(buckets["gguf_text_encoder_primary"], "gguf_text_encoder_primary", gguf_dual_a_choices or gguf_single_clip_choices)
    _append_unique(buckets["gguf_text_encoder_secondary"], "gguf_text_encoder_secondary", gguf_dual_b_choices)
    _append_unique(buckets["gguf_text_encoders"], "gguf_text_encoder", gguf_single_clip_choices + gguf_dual_a_choices + gguf_dual_b_choices)
    _append_unique(buckets["gguf_vaes"], "gguf_vae", gguf_vae_choices)

    # mmproj can come from CLIPLoaderGGUF/DualCLIPLoaderGGUF choices or from
    # text encoder style folders exposed by Comfy custom nodes.
    mmproj_choices: list[str] = []
    for node_name in [gguf_single_clip_node, gguf_dual_clip_node]:
        if not node_name:
            continue
        required = (((info.get(node_name) or {}).get("input") or {}).get("required") or {})
        optional = (((info.get(node_name) or {}).get("input") or {}).get("optional") or {})
        for input_name, raw in {**required, **optional}.items():
            if "mmproj" in str(input_name).casefold() or "projector" in str(input_name).casefold():
                mmproj_choices.extend(_extract_comfy_choices(raw))
    mmproj_choices.extend([item for item in gguf_single_clip_choices + gguf_dual_a_choices + gguf_dual_b_choices if _is_mmproj_asset(item)])
    _append_unique(buckets["mmproj"], "mmproj", mmproj_choices)

    # Qwen text encoders are also exposed through the single GGUF text encoder
    # choices. Keeping them in the general GGUF text encoder bucket lets the V2 UI
    # filter/label by architecture without needing a Comfy-specific field.
    qwen_text_encoders = [item for item in gguf_single_clip_choices + gguf_dual_a_choices if _is_qwen_text_encoder_asset(item)]
    _append_unique(buckets["gguf_text_encoders"], "gguf_text_encoder", qwen_text_encoders)

    # Phase 11.3: ComfyUI exposes sampler and scheduler options through /object_info/KSampler.
    ksampler_inputs = (((info.get("KSampler") or {}).get("input") or {}).get("required") or {})
    sampler_names = ksampler_inputs.get("sampler_name", [[]])[0] if ksampler_inputs.get("sampler_name") else []
    scheduler_names = ksampler_inputs.get("scheduler", [[]])[0] if ksampler_inputs.get("scheduler") else []
    for name in sampler_names:
        buckets["samplers"].append({"kind": "sampler", "name": name})
    for name in scheduler_names:
        buckets["schedulers"].append({"kind": "scheduler", "name": name})
    return buckets



def _extract_openai_model_names(payload: Any) -> list[str]:
    """Normalize OpenAI-compatible /v1/models payloads used by KoboldCpp."""
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    elif isinstance(payload, list):
        candidates.extend(payload)

    names: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            raw = item.get("id") or item.get("name") or item.get("model")
        else:
            raw = item
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names



KOBOLD_VISION_HINTS = {
    "llava",
    "qwen2vl",
    "qwen-vl",
    "qwen_vl",
    "toriigate",
    "joycaption",
    "minicpm",
    "minicpmv",
    "vision",
    "mmproj",
    "mm_projector",
    "multimodal",
    "image encoder",
    "clip_model_loader",
}


def _kobold_runtime_supports_vision_from_names(names: list[str]) -> bool:
    joined = " ".join(str(name or "") for name in names).casefold()
    return any(hint in joined for hint in KOBOLD_VISION_HINTS)


def _kobold_effective_capabilities(profile: dict[str, Any], names: list[str] | None = None) -> dict[str, Any]:
    flags = profile.get("capability_flags", {}) or {}
    connection = profile.get("connection", {}) or {}
    model_names = list(names or [])
    if connection.get("model"):
        model_names.append(str(connection.get("model") or ""))
    runtime_supports_vision = _kobold_runtime_supports_vision_from_names(model_names)
    profile_supports_text = bool(flags.get("supports_text", True))
    profile_supports_vision = bool(flags.get("supports_vision", False))
    profile_supports_captioning = bool(flags.get("supports_captioning", False))
    effective_supports_vision = profile_supports_vision or runtime_supports_vision
    effective_supports_captioning = profile_supports_captioning or runtime_supports_vision or effective_supports_vision
    if runtime_supports_vision:
        capability_source = "runtime_detected"
    elif profile_supports_captioning:
        capability_source = "manual_caption_flag"
    elif profile_supports_vision:
        capability_source = "inferred_from_vision"
    else:
        capability_source = "profile_flag"
    return {
        "supports_text": profile_supports_text,
        "supports_vision": effective_supports_vision,
        "supports_captioning": effective_supports_captioning,
        "streaming_enabled": bool(flags.get("streaming_enabled", False)),
        "profile_supports_text": profile_supports_text,
        "profile_supports_vision": profile_supports_vision,
        "profile_supports_captioning": profile_supports_captioning,
        "runtime_supports_text": True,
        "runtime_supports_vision": runtime_supports_vision,
        "runtime_supports_captioning": runtime_supports_vision,
        "capability_source": capability_source,
    }

def _probe_koboldcpp_profile(profile: dict[str, Any]) -> dict[str, Any]:
    connection = profile.get("connection", {}) or {}
    base_url = (connection.get("base_url") or "").rstrip("/")
    timeout = float(connection.get("timeout_seconds") or 10)
    checked_at = _now_iso()
    if not base_url:
        return {
            "status": "missing_config",
            "reachable": False,
            "last_checked": checked_at,
            "message": "Base URL is empty.",
            "models": _empty_models(),
        }

    models_path = str(connection.get("healthcheck_path") or "/v1/models")
    try:
        payload = _http_get_json(base_url, models_path, timeout=timeout)
        names = _extract_openai_model_names(payload) or [str(connection.get("model") or "default").strip() or "default"]
        model_records = [{"kind": "text_model", "name": name, "source": "koboldcpp_models"} for name in names]
        buckets = _empty_models()
        buckets["models"] = model_records
        buckets["text_models"] = model_records
        caps = _kobold_effective_capabilities(profile, names)
        if caps.get("supports_vision") or caps.get("supports_captioning"):
            buckets["vision_models"] = [{**record, "kind": "vision_model"} for record in model_records]
        return {
            "status": "connected",
            "reachable": True,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": f"Connected to KoboldCpp at {base_url}.",
            "models": buckets,
            "capabilities": caps,
        }
    except Exception as exc:  # noqa: BLE001 - admin probe should never crash UI.
        return {
            "status": "offline",
            "reachable": False,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": f"Could not reach KoboldCpp at {base_url}: {exc}",
            "models": _empty_models(),
        }


def _cloud_model_records_from_profile(profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    buckets = _empty_models()
    model_block = profile.get("model") or {}
    names = model_block.get("available_models") or []
    default_model = model_block.get("default_model") or (profile.get("connection") or {}).get("model") or ""
    if default_model and default_model not in names:
        names = [default_model, *names]
    records = [{"kind": "cloud_model", "name": str(name), "source": "backend_profile"} for name in names if str(name or "").strip()]
    surface = str(profile.get("surface") or "")
    if surface == "image":
        buckets["models"] = records
    elif surface in {"text", "assistant", "roleplay", "prompt_captioning"}:
        buckets["text_models"] = records
    else:
        buckets["models"] = records
    return buckets


def _http_get_json_with_headers(base_url: str, path: str, headers: dict[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{str(path or '').lstrip('/')}"
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-configured local/cloud backend probe
        body = response.read().decode("utf-8", errors="replace")
    if not body.strip():
        return {}
    return json.loads(body)


def _probe_cloud_api_profile(profile: dict[str, Any]) -> dict[str, Any]:
    connection = profile.get("connection", {}) or {}
    base_url = str(connection.get("base_url") or "").rstrip("/")
    timeout = float(connection.get("timeout_seconds") or 10)
    checked_at = _now_iso()
    key_status = resolve_backend_profile_api_key(profile)
    if not base_url:
        return {
            "status": "missing_config",
            "reachable": False,
            "last_checked": checked_at,
            "message": "Cloud API base URL is empty.",
            "api_key_status": _strip_secret_fields(key_status),
            "models": _cloud_model_records_from_profile(profile),
        }
    if not key_status.get("api_key_is_configured"):
        return {
            "status": "missing_key",
            "reachable": False,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": key_status.get("api_key_status_message") or "API key is missing.",
            "api_key_status": _strip_secret_fields(key_status),
            "models": _cloud_model_records_from_profile(profile),
        }
    health_mode = str(connection.get("healthcheck_mode") or "http_get").strip().lower()
    health_path = str(connection.get("healthcheck_path") or "/models").strip() or "/models"
    headers = {"Accept": "application/json"}
    if key_status.get("api_key_value"):
        header_name = str(connection.get("auth_header_name") or "Authorization").strip() or "Authorization"
        header_prefix = str(connection.get("auth_header_prefix") if connection.get("auth_header_prefix") is not None else "Bearer ")
        headers[header_name] = f"{header_prefix}{key_status.get('api_key_value')}"
    if health_mode == "configured_only":
        return {
            "status": "configured",
            "reachable": True,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": "API credentials are configured. This provider has no non-billable live health endpoint, so Neo did not submit an image.",
            "api_key_status": _strip_secret_fields(key_status),
            "models": _cloud_model_records_from_profile(profile),
            "verification": "configured_only_no_billable_request",
        }
    try:
        payload = _http_get_json_with_headers(base_url, health_path, headers=headers, timeout=timeout)
        models = _cloud_model_records_from_profile(profile)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            discovered = []
            for item in data:
                if isinstance(item, dict):
                    name = str(item.get("id") or item.get("name") or "").strip()
                    if name:
                        discovered.append({"kind": "cloud_model", "name": name, "source": "healthcheck"})
            if discovered:
                surface = str(profile.get("surface") or "")
                if surface in {"text", "assistant", "roleplay", "prompt_captioning"}:
                    models["text_models"] = discovered
                else:
                    models["models"] = discovered
        return {
            "status": "connected",
            "reachable": True,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": f"Connected to cloud API at {base_url}.",
            "api_key_status": _strip_secret_fields(key_status),
            "models": models,
        }
    except error.HTTPError as exc:
        status = "auth_failed" if exc.code in {401, 403} else "offline"
        return {
            "status": status,
            "reachable": False,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": f"Cloud API health check failed with HTTP {exc.code}.",
            "api_key_status": _strip_secret_fields(key_status),
            "models": _cloud_model_records_from_profile(profile),
        }
    except Exception as exc:  # noqa: BLE001 - cloud health checks must not crash Admin
        return {
            "status": "offline",
            "reachable": False,
            "base_url": base_url,
            "last_checked": checked_at,
            "message": f"Could not reach cloud API at {base_url}: {exc}",
            "api_key_status": _strip_secret_fields(key_status),
            "models": _cloud_model_records_from_profile(profile),
        }

def _probe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    provider_id = profile.get("provider_id", "")
    connection = profile.get("connection", {}) or {}
    base_url = (connection.get("base_url") or "").rstrip("/")
    timeout = float(connection.get("timeout_seconds") or 3)
    checked_at = _now_iso()

    if str(profile.get("connection_type") or connection.get("connection_type") or "") == "cloud_api":
        return _probe_cloud_api_profile(profile)

    if provider_id in {"koboldcpp", "openai_compatible_text"}:
        return _probe_koboldcpp_profile(profile)

    if provider_id in {"comfyui", "comfyui_portable"}:
        if not base_url:
            return {
                "status": "missing_config",
                "reachable": False,
                "last_checked": checked_at,
                "message": "Base URL is empty.",
                "models": _empty_models(),
            }
        try:
            stats = _http_get_json(base_url, "/system_stats", timeout=timeout)
            models = _discover_comfy_models(base_url, timeout=timeout, backend_details={"portable_path": connection.get("portable_path") or "", "profile_id": profile.get("profile_id") or ""})
            return {
                "status": "connected",
                "reachable": True,
                "base_url": base_url,
                "last_checked": checked_at,
                "message": "Connected to ComfyUI.",
                "system_stats": stats,
                "models": models,
            }
        except Exception as exc:  # noqa: BLE001 - connection test should never crash UI.
            return {
                "status": "offline",
                "reachable": False,
                "base_url": base_url,
                "last_checked": checked_at,
                "message": f"Could not reach backend at {base_url}: {exc}",
                "models": _empty_models(),
            }

    provider = get_provider(provider_id)
    if provider is None:
        return {
            "status": "missing_config",
            "reachable": False,
            "last_checked": checked_at,
            "message": "Provider adapter is missing.",
            "models": _empty_models(),
        }
    status_payload = provider.status()
    status = _status_value(status_payload)
    return {
        "status": "connected" if status in {"available", "online", "ready"} else status,
        "reachable": status in {"available", "online", "ready", "connected"},
        "last_checked": checked_at,
        "message": status_payload.get("message") if isinstance(status_payload, dict) else "Provider status checked.",
        "models": _split_model_records(provider.discover_models()),
    }


def _enrich_profile(profile: dict[str, Any], *, allow_manual_runtime: bool = False) -> dict[str, Any]:
    provider = get_provider(profile.get("provider_id", ""), profile=profile)
    connection = profile.get("connection", {}) or {}
    runtime = profile.get("runtime") or {}
    auto_connect = bool(connection.get("auto_connect", False))
    runtime_status_raw = str(runtime.get("status") or "").strip().lower()
    activation_raw = str(runtime.get("activation") or "").strip().lower()
    is_cloud_profile = str(profile.get("connection_type") or connection.get("connection_type") or "").strip().lower() == "cloud_api"
    manual_runtime_active = (
        activation_raw in {"manual_connect", "manual_test"}
        and bool(runtime.get("reachable", False))
        and runtime_status_raw in CONNECTION_TEST_READY_STATUSES
    )
    preserve_saved_diagnostic = (
        is_cloud_profile
        and activation_raw in {"manual_connect", "manual_test"}
        and runtime_status_raw in CONNECTION_TEST_DIAGNOSTIC_STATUSES
    )

    # Important: a saved manual_connect runtime is only a remembered *last test*.
    # It must not be trusted blindly on the next page/app load, because the local
    # server may have been closed after the successful test. When a profile was
    # previously connected, do one lightweight live probe before showing Connected.
    # Profiles that were never manually connected remain passive and show
    # Disconnected while auto_connect is off.
    live_runtime = None
    should_validate_saved_runtime = auto_connect or allow_manual_runtime
    if should_validate_saved_runtime:
        try:
            live_runtime = _probe_profile(profile)
        except Exception:  # noqa: BLE001 - profile listing must stay resilient.
            live_runtime = None

    if live_runtime:
        runtime_payload = _decorate_connection_test_result(profile, {
            **live_runtime,
            "activation": runtime.get("activation") or ("auto_connect" if auto_connect else "manual_connect"),
        }, operation="auto_connect" if auto_connect else "manual_connect")
        runtime_status = runtime_payload.get("status") or "missing_config"
        show_live_runtime = bool(runtime_payload.get("reachable", False)) and str(runtime_status).lower() in {"connected", "available", "online", "ready"}
    elif preserve_saved_diagnostic:
        show_live_runtime = False
        runtime_payload = _decorate_connection_test_result(profile, runtime, operation=activation_raw or "manual_test")
        runtime_status = runtime_payload.get("status") or runtime_status_raw or "disconnected"
    elif runtime_status_raw in {"not_checked", "missing_key", "disabled"}:
        show_live_runtime = False
        runtime_status = runtime_status_raw
        runtime_payload = {
            **runtime,
            "status": runtime_status_raw,
            "reachable": False,
            "message": runtime.get("message") or "Click Connect/Test to check this backend profile.",
        }
    else:
        show_live_runtime = False
        runtime_status = "disconnected"
        runtime_payload = {
            **runtime,
            "status": "disconnected",
            "reachable": False,
            "message": "Auto-connect is off. Click Connect/Test to probe this backend.",
        }

    if not show_live_runtime and runtime_payload.get("status") not in CONNECTION_TEST_DIAGNOSTIC_STATUSES | {"not_checked", "disabled"}:
        # A stale saved Connected state should not paint a Connected badge when the
        # live probe is not reachable or no probe was requested.
        runtime_payload = {
            **runtime_payload,
            "status": "disconnected",
            "reachable": False,
            "message": runtime_payload.get("message") or "Backend is not connected.",
        }
        runtime_status = "disconnected"

    models = runtime_payload.get("models") or _empty_models()

    return {
        **profile,
        "connection": _safe_connection_for_ui(profile.get("connection", {}) or {}, profile.get("profile_id")),
        "runtime_status": runtime_status,
        "runtime": runtime_payload,
        "capabilities": profile.get("capabilities", profile.get("capability_flags", {})) or {},
        "provider_capabilities": provider.discover_capabilities() if provider else [],
        "capability_flags": profile.get("capability_flags", {}) or {},
        "generation_defaults": profile.get("generation_defaults", {}) or {},
        "defaults": profile.get("defaults", profile.get("generation_defaults", {})) or {},
        "model": profile.get("model", {}) or {},
        "ui": profile.get("ui", {}) or {},
        "metadata": profile.get("metadata", {}) or {},
        "feature_capabilities": get_provider_feature_capabilities(profile.get("provider_id", ""), profile=profile),
        "models": models if show_live_runtime else _empty_models(),
        "profile_status": "enabled" if profile.get("enabled") else "disabled",
    }


@lru_cache(maxsize=1)
def get_backend_profile_payload() -> dict[str, Any]:
    payload = _read_payload()
    enriched = [_enrich_profile(profile) for profile in payload.get("profiles", [])]
    storage = backend_profile_store_paths()
    return {
        "profile_registry_version": payload.get("profile_registry_version", "0.1.0"),
        "defaults": payload.get("defaults", {}),
        "profiles": enriched,
        "storage": storage,
        "backend_profile_store": storage,
        "backend_selection_persistence": backend_selection_persistence_policy(),
        "selection_persistence": backend_selection_persistence_policy(),
    }


def list_backend_profiles(surface: str | None = None) -> list[dict[str, Any]]:
    profiles = get_backend_profile_payload().get("profiles", [])
    if surface:
        return [profile for profile in profiles if profile.get("surface") == surface]
    return profiles


def get_backend_profile(profile_id: str) -> dict[str, Any] | None:
    for profile in list_backend_profiles():
        if profile.get("profile_id") == profile_id:
            return profile
    return None


def get_backend_profile_for_runtime(profile_id: str) -> dict[str, Any] | None:
    """Return the normalized profile with local secret fields intact for server-side providers.

    UI-facing profile payloads deliberately strip manual API key values. Runtime
    providers need the raw local profile so manual keys can work without leaking
    them back to the browser.
    """
    raw_payload = _read_payload()
    for profile in raw_payload.get("profiles", []):
        if profile.get("profile_id") == profile_id:
            return _normalize_profile_schema(profile)
    return None


def get_backend_profile_for_live_task(profile_id: str) -> dict[str, Any] | None:
    """Return a task-facing profile with local manual/test runtime validated live.

    Normal backend profile listings stay passive when auto_connect is off, so the
    Admin page does not probe every local process on load. Image generation and
    other explicit task routes are different: after the user clicks Connect/Test,
    the task gate must validate the selected local backend live instead of
    reading the passive UI-facing disconnected view.
    """
    raw_profile = get_backend_profile_for_runtime(profile_id)
    if raw_profile is None:
        return None
    return _enrich_profile(raw_profile, allow_manual_runtime=True)



def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_profile_update_values(updates: dict[str, Any]) -> dict[str, Any]:
    fixed = {**(updates or {})}
    if "enabled" in fixed:
        fixed["enabled"] = _coerce_bool(fixed.get("enabled"), True)
    if "is_default" in fixed:
        fixed["is_default"] = _coerce_bool(fixed.get("is_default"), False)
    if isinstance(fixed.get("connection"), dict):
        connection = {**fixed["connection"]}
        for key in ("auto_connect", "auto_start"):
            if key in connection:
                connection[key] = _coerce_bool(connection.get(key), False)
        if "timeout_seconds" in connection:
            try:
                connection["timeout_seconds"] = int(connection.get("timeout_seconds") or 0)
            except Exception:
                connection["timeout_seconds"] = 300
        if "auth_mode" in connection or "api_key_mode" in connection:
            auth_mode = _normalize_auth_mode(connection.get("auth_mode") or connection.get("api_key_mode"), "env")
            connection["auth_mode"] = auth_mode
            connection["api_key_mode"] = auth_mode
        if "api_key_clear" in connection:
            connection["api_key_clear"] = _coerce_bool(connection.get("api_key_clear"), False)
        fixed["connection"] = connection
    for block_name in ("capability_flags", "capabilities"):
        if isinstance(fixed.get(block_name), dict):
            flags = {**fixed[block_name]}
            for key, value in list(flags.items()):
                if isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no", "on", "off", "1", "0"}:
                    flags[key] = _coerce_bool(value, False)
            fixed[block_name] = flags
    for defaults_name in ("generation_defaults", "defaults"):
        if isinstance(fixed.get(defaults_name), dict):
            generation = {**fixed[defaults_name]}
            for key in ("max_tokens", "width", "height", "batch_size", "fps", "duration_seconds", "preview_seconds", "n", "image_count"):
                if key in generation:
                    try:
                        generation[key] = int(generation.get(key) or 0)
                    except Exception:
                        generation[key] = 0
            for key in ("temperature", "top_p", "speaking_rate", "expression_strength"):
                if key in generation:
                    try:
                        generation[key] = float(generation.get(key) or 0)
                    except Exception:
                        generation[key] = 0.0
            fixed[defaults_name] = generation
    return fixed



def _clear_profile_cache() -> None:
    """Invalidate cached backend profile payload after profile/default/runtime writes."""
    try:
        get_backend_profile_payload.cache_clear()
    except Exception:
        pass

def _safe_profile_id(value: str, fallback: str = "backend_profile") -> str:
    raw = str(value or fallback).strip().lower()
    profile_id = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw).strip("_")
    return profile_id or fallback


def create_backend_profile(updates: dict[str, Any]) -> dict[str, Any]:
    updates = _coerce_profile_update_values(updates)
    payload = _read_payload()
    profiles = payload.setdefault("profiles", [])
    surface = str(updates.get("surface") or "text").strip() or "text"
    provider_id = str(updates.get("provider_id") or "koboldcpp").strip() or "koboldcpp"
    display_name = str(updates.get("display_name") or f"{surface.title()} · {provider_id.replace('_', ' ').title()}").strip()
    raw_id = str(updates.get("profile_id") or f"{surface}.{provider_id}.{display_name}")
    profile_id = _safe_profile_id(raw_id, f"{surface}.{provider_id}")
    if any(profile.get("profile_id") == profile_id for profile in profiles):
        return {"ok": False, "errors": [f"Backend profile already exists: {profile_id}"]}
    provider_manifest = _provider_manifest_for(provider_id)
    if not provider_manifest:
        return {"ok": False, "errors": [f"Unknown provider: {provider_id}"]}
    provider_surfaces = _provider_manifest_surfaces(provider_manifest)
    if provider_surfaces and surface not in provider_surfaces:
        return {"ok": False, "errors": [f"Provider {provider_id} does not support surface: {surface}"]}
    provider_template = _provider_default_profile_template(provider_manifest, surface)
    connection_type = str(updates.get("connection_type") or (updates.get("connection") or {}).get("connection_type") or provider_template.get("connection_type") or provider_manifest.get("connection_kind") or "").strip()
    template_connection = provider_template.get("connection") or {}
    template_capabilities = provider_template.get("capabilities") or provider_template.get("capability_flags") or {}
    template_defaults = provider_template.get("defaults") or provider_template.get("generation_defaults") or {}
    seed_profile = {
        "profile_id": profile_id,
        "display_name": display_name,
        "provider_id": provider_id,
        "provider_label": updates.get("provider_label") or provider_template.get("provider_label"),
        "surface": surface,
        "profile_role": updates.get("profile_role") or provider_template.get("profile_role") or _profile_role_for(surface, connection_type or "", provider_id),
        "connection_type": connection_type,
        "enabled": bool(updates.get("enabled", True)),
        "is_default": bool(updates.get("is_default", False)),
        "connection": _deep_merge_dicts(template_connection, updates.get("connection") or {}),
        "capability_flags": _deep_merge_dicts(template_capabilities if isinstance(template_capabilities, dict) else {}, updates.get("capability_flags") or updates.get("capabilities") or {}),
        "generation_defaults": _deep_merge_dicts(template_defaults if isinstance(template_defaults, dict) else {}, updates.get("generation_defaults") or updates.get("defaults") or {}),
        "model": _deep_merge_dicts(provider_template.get("model") or {}, updates.get("model") or {}),
        "ui": _deep_merge_dicts(provider_template.get("ui") or {}, updates.get("ui") or {}),
        "metadata": {"created_at": _now_iso(), "updated_at": _now_iso()},
        "notes": updates.get("notes") or provider_template.get("notes") or "Created from Admin Backend Profiles.",
    }
    seed_profile["connection"] = _apply_api_key_save_load_rules(
        profile_id,
        seed_profile,
        seed_profile.get("connection") if isinstance(seed_profile.get("connection"), dict) else {},
        {},
    )
    profile = _normalize_profile_schema(seed_profile)
    profiles.append(profile)
    if profile.get("is_default"):
        payload.setdefault("defaults", {})[profile.get("surface") or "text"] = profile_id
        for item in profiles:
            if item.get("surface") == profile.get("surface"):
                item["is_default"] = item.get("profile_id") == profile_id
    _write_payload(payload)
    _clear_profile_cache()
    return {"ok": True, "profile": get_backend_profile(profile_id)}


def save_backend_profile(profile_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    updates = _coerce_profile_update_values(updates)
    payload = _read_payload()
    profiles = payload.setdefault("profiles", [])
    nested_keys = {"connection", "runtime", "capability_flags", "capabilities", "generation_defaults", "defaults", "model", "ui", "metadata"}
    for index, profile in enumerate(profiles):
        if profile.get("profile_id") == profile_id:
            merged = {**profile, **{k: v for k, v in updates.items() if k not in nested_keys}}
            if "connection" in updates:
                connection_updates = _apply_api_key_save_load_rules(
                    profile_id,
                    profile,
                    updates.get("connection", {}) if isinstance(updates.get("connection"), dict) else {},
                    profile.get("connection", {}) if isinstance(profile.get("connection"), dict) else {},
                )
                merged["connection"] = {**profile.get("connection", {}), **connection_updates}
                merged["connection"].pop("api_key_value", None)
            if "capability_flags" in updates or "capabilities" in updates:
                merged_flags = {
                    **profile.get("capability_flags", {}),
                    **profile.get("capabilities", {}),
                    **updates.get("capability_flags", {}),
                    **updates.get("capabilities", {}),
                }
                merged["capability_flags"] = merged_flags
                merged["capabilities"] = merged_flags
            if "generation_defaults" in updates or "defaults" in updates:
                merged_defaults = {
                    **profile.get("generation_defaults", {}),
                    **profile.get("defaults", {}),
                    **updates.get("generation_defaults", {}),
                    **updates.get("defaults", {}),
                }
                merged["generation_defaults"] = merged_defaults
                merged["defaults"] = merged_defaults
            if "model" in updates:
                merged["model"] = {**profile.get("model", {}), **updates.get("model", {})}
            if "ui" in updates:
                merged["ui"] = {**profile.get("ui", {}), **updates.get("ui", {})}
            if "metadata" in updates:
                merged["metadata"] = {**profile.get("metadata", {}), **updates.get("metadata", {})}
            merged.setdefault("metadata", {})["updated_at"] = _now_iso()
            # Any profile config change invalidates stale runtime status until the next Connect/Test.
            if any(key in updates for key in {"connection", "capability_flags", "capabilities", "generation_defaults", "defaults", "provider_id", "surface", "connection_type", "model"}):
                merged.pop("runtime", None)
            normalized_profile = _normalize_profile_schema(merged)
            profiles[index] = normalized_profile
            if normalized_profile.get("is_default"):
                _set_default_profile_in_payload(payload, str(normalized_profile.get("surface") or "text"), profile_id)
            _write_payload(payload)
            _clear_profile_cache()
            return {"ok": True, "profile": get_backend_profile(profile_id)}
    return {"ok": False, "errors": [f"Unknown backend profile: {profile_id}"]}


def clear_backend_profile_api_key(profile_id: str) -> dict[str, Any]:
    return save_backend_profile(profile_id, {"connection": {"api_key_clear": True}})


def mark_backend_profile_connected_for_task(profile_id: str, connected: bool = True) -> None:
    pid = str(profile_id or "").strip()
    if not pid:
        return
    if connected:
        _CONNECTED_TASK_PROFILE_IDS.add(pid)
    else:
        _CONNECTED_TASK_PROFILE_IDS.discard(pid)


def is_backend_profile_connected_for_task(profile_id: str) -> bool:
    return str(profile_id or "").strip() in _CONNECTED_TASK_PROFILE_IDS


def set_default_backend_profile(surface: str, profile_id: str) -> dict[str, Any]:
    surface = str(surface or "").strip()
    profile_id = str(profile_id or "").strip()
    payload = _read_payload()
    requested = next((item for item in payload.get("profiles", []) if isinstance(item, dict) and str(item.get("profile_id") or "") == profile_id), None)
    if requested and str(requested.get("profile_role") or "").strip() == "image_background_removal_backend":
        return {
            "ok": False,
            "errors": ["Background-removal utility profiles cannot become the primary Image backend."],
        }
    matched = _set_default_profile_in_payload(payload, surface, profile_id)
    if not matched:
        return {"ok": False, "errors": [f"Profile {profile_id} is not available for surface {surface}"]}
    _write_payload(payload)
    _clear_profile_cache()
    selection = save_backend_profile_selection({"surface": surface, "profile_id": profile_id})
    return {
        "ok": True,
        "surface": surface,
        "default_profile_id": profile_id,
        "selection": selection.get("selection") if isinstance(selection, dict) else {},
        "policy": backend_selection_persistence_policy(),
    }


def connect_backend_profile(profile_id: str) -> dict[str, Any]:
    payload = _read_payload()
    for index, profile in enumerate(payload.get("profiles", [])):
        if profile.get("profile_id") == profile_id:
            runtime = _decorate_connection_test_result(profile, {**_probe_profile(profile), "activation": "manual_connect"}, operation="manual_connect")
            profile["runtime"] = runtime
            payload["profiles"][index] = profile
            _write_payload(payload)
            _clear_profile_cache()
            mark_backend_profile_connected_for_task(profile_id, bool(runtime.get("reachable", False)))
            # Return an explicitly live profile for the button response. The standard
            # /api/backend-profiles listing remains passive when auto_connect=false.
            return {
                "ok": runtime.get("reachable", False),
                "profile_id": profile_id,
                "runtime": runtime,
                "profile": _enrich_profile(profile, allow_manual_runtime=True),
            }
    return {"ok": False, "errors": [f"Unknown backend profile: {profile_id}"]}


def disconnect_backend_profile(profile_id: str) -> dict[str, Any]:
    payload = _read_payload()
    for index, profile in enumerate(payload.get("profiles", [])):
        if profile.get("profile_id") == profile_id:
            runtime = profile.get("runtime", {}) or {}
            profile["runtime"] = {
                **runtime,
                "status": "disconnected",
                "reachable": False,
                "last_checked": _now_iso(),
                "message": "Disconnected by user.",
            }
            payload["profiles"][index] = profile
            _write_payload(payload)
            _clear_profile_cache()
            mark_backend_profile_connected_for_task(profile_id, False)
            return {"ok": True, "profile_id": profile_id, "profile": get_backend_profile(profile_id)}
    return {"ok": False, "errors": [f"Unknown backend profile: {profile_id}"]}


def test_backend_profile(profile_id: str) -> dict[str, Any]:
    payload = _read_payload()
    for index, profile in enumerate(payload.get("profiles", [])):
        if profile.get("profile_id") == profile_id:
            runtime = _decorate_connection_test_result(profile, {**_probe_profile(profile), "activation": "manual_test"}, operation="manual_test")
            profile["runtime"] = runtime
            payload["profiles"][index] = profile
            _write_payload(payload)
            _clear_profile_cache()
            mark_backend_profile_connected_for_task(profile_id, bool(runtime.get("reachable", False)))
            status = str(runtime.get("status") or "offline")
            return {
                "ok": bool(runtime.get("reachable", False)),
                "profile_id": profile_id,
                "status": status,
                "severity": (runtime.get("diagnostic") or {}).get("severity") or _connection_test_severity(status),
                "models": runtime.get("models", _empty_models()),
                "message": runtime.get("message"),
                "diagnostic": runtime.get("diagnostic") or {},
                "profile": _enrich_profile(profile, allow_manual_runtime=True),
            }
    return {"ok": False, "status": "missing_config", "errors": [f"Unknown backend profile: {profile_id}"]}
