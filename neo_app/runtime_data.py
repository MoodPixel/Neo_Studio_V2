from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[1]
NEO_DATA_DIR: Final[Path] = ROOT_DIR / "neo_data"
RUNTIME_MANIFEST_PATH: Final[Path] = NEO_DATA_DIR / ".neo_runtime_manifest.json"
RUNTIME_SCHEMA_ID: Final[str] = "neo.runtime_data.bootstrap.v1"

ASSISTANT_DEFAULT_PROJECT_ID: Final[str] = "general"
ASSISTANT_SCOPE_SEED_SCHEMA_ID: Final[str] = "neo.runtime_data.assistant_scopes_seed.v1"

ASSISTANT_BUILTIN_SCOPES: Final[tuple[dict[str, Any], ...]] = (
    {
        "workspace_id": "assistant_workspace_general",
        "project_id": "general",
        "surface": "assistant",
        "name": "General Assistant",
        "type": "assistant_workspace",
        "description": "Default Assistant workspace for uncategorized questions, planning, and cross-surface coordination.",
        "memory_lanes": ["project_memory", "assistant_captures", "workspace_context", "recent_assistant_thread"],
    },
    {
        "workspace_id": "assistant_workspace_image",
        "project_id": "image_workspace",
        "surface": "image",
        "name": "Image Workspace",
        "type": "surface_workspace",
        "description": "Assistant brain workspace for Image Tab prompts, generation metadata, models, LoRAs, settings, seeds, and result patterns.",
        "memory_lanes": ["image_generation_metadata", "prompt_patterns", "successful_settings", "model_settings", "failure_patterns"],
    },
    {
        "workspace_id": "assistant_workspace_prompt_captioning",
        "project_id": "prompt_captioning_workspace",
        "surface": "prompt_captioning",
        "name": "Prompt + Captioning Workspace",
        "type": "surface_workspace",
        "description": "Assistant brain workspace for Prompt Studio, Caption Studio, generated prompts, captions, keywords, and saved outputs.",
        "memory_lanes": ["saved_outputs", "caption_outputs", "prompt_patterns", "keyword_patterns", "instruction_patterns"],
    },
    {
        "workspace_id": "assistant_workspace_video",
        "project_id": "video_workspace",
        "surface": "video",
        "name": "Video Workspace",
        "type": "surface_workspace",
        "description": "Assistant brain workspace for Video Tab generation modes, prompts, source frames, performance settings, finish tools, output metadata, and replay context.",
        "memory_lanes": ["video_generation_metadata", "prompt_patterns", "source_assets", "performance_settings", "finish_outputs", "failure_patterns"],
    },
    {
        "workspace_id": "assistant_workspace_voice",
        "project_id": "voice_workspace",
        "surface": "voice",
        "name": "Voice Workspace",
        "type": "surface_workspace",
        "description": "Assistant brain workspace for Voice Tab scripts, voices, profiles, reference audio, render settings, exports, and replay context.",
        "memory_lanes": ["voice_render_metadata", "script_patterns", "voice_profiles", "reference_audio", "export_settings"],
    },
    {
        "workspace_id": "assistant_workspace_roleplay",
        "project_id": "roleplay_workspace",
        "surface": "roleplay",
        "name": "Roleplay Workspace",
        "type": "surface_workspace",
        "description": "Assistant brain workspace for Roleplay universes, worlds, scene packets, canon, character state, and continuity diagnostics.",
        "memory_lanes": ["roleplay_project_memory", "canon_memory", "scene_state", "character_memory", "timeline_memory"],
    },
    {
        "workspace_id": "assistant_workspace_client_work",
        "project_id": "client_work_workspace",
        "surface": "assistant",
        "name": "Client Work Workspace",
        "type": "creative_operations_workspace",
        "description": "Assistant brain workspace for freelance/client work, briefs, quotes, messages, deliverables, and project workflow advice.",
        "memory_lanes": ["client_briefs", "workflow_patterns", "pricing_notes", "delivery_history", "assistant_captures"],
    },
    {
        "workspace_id": "assistant_workspace_neo_development",
        "project_id": "neo_development_workspace",
        "surface": "admin",
        "name": "Neo Development Workspace",
        "type": "system_development_workspace",
        "description": "Assistant brain workspace for Neo Studio development, architecture records, debugging, phases, and system implementation decisions.",
        "memory_lanes": ["system_records", "admin_config", "memory_engine_health", "control_center_traces", "diagnostics"],
    },
)


SURFACE_RUNTIME_LOG_DIRECTORIES: Final[tuple[str, ...]] = (
    # App/global logs are kept separate from surface logs.
    "logs/app",
    # Surface runtime debug logs. Image keeps its existing folder and run layout.
    "logs/image",
    "logs/image/runs",
    "logs/video",
    "logs/video/runs",
    "logs/voice",
    "logs/voice/runs",
    "logs/prompt_captioning",
    "logs/prompt_captioning/runs",
    "logs/roleplay",
    "logs/roleplay/runs",
    "logs/assistant",
    "logs/assistant/runs",
    "logs/admin",
    "logs/admin/runs",
    "logs/admin/index_jobs",
    "logs/board",
    "logs/board/runs",
    # Runtime systems that are not user-facing tabs but still need trace roots.
    "logs/memory",
    "logs/memory/runs",
    "logs/backends",
    "logs/backends/runs",
    "logs/extensions",
    "logs/extensions/runs",
)

RUNTIME_DIRECTORIES: Final[tuple[str, ...]] = (
    # Assistant-level scope / internal project memory.
    "assistant/projects",
    "assistant/sessions",
    "assistant/context_items",
    "assistant/memory_captures",
    "assistant/surface_context",
    "assistant/project_brain",
    "assistant/project_brain/general",
    # Shared memory engine.
    "memory/global",
    "memory/imports",
    "memory/exports",
    # Legacy creator project workspace compatibility layer.
    "projects/workspaces",
    "projects/context",
    "projects/links",
    "projects/handoffs",
    "projects/timeline",
    "projects/briefs",
    "projects/brief_exports",
    "projects/milestones",
    "projects/deliverables",
    "projects/status_exports",
    "projects/review_queue",
    "projects/packages",
    "projects/surface_actions",
    # Surface-owned runtime roots.
    "roleplay/entities",
    "roleplay/source_documents",
    "roleplay/drafts",
    "roleplay/helper_outputs",
    "roleplay/canon_records",
    "roleplay/memory_fragments",
    "roleplay/relationships",
    "roleplay/shared_memories",
    "roleplay/runtime_bundles",
    "roleplay/packages",
    "roleplay/imports",
    "roleplay/exports",
    "roleplay/projects",
    "roleplay/retrieval",
    "roleplay/storylines",
    "roleplay/story_sessions",
    "roleplay/story_checkpoints",
    "roleplay/story_drafts",
    "roleplay/story_snapshots",
    "roleplay/story_branches",
    "roleplay/cloud_sync",
    "roleplay/package_registry",
    "prompt_captioning",
    "voice",
    "video",
    "scene_director/scene_presets",
    "scene_director/identity_profiles",
    "scene_director/region_layout_presets",
    "scene_director/trait_libraries",
    # App settings/state.
    "settings",
    "settings/image",
    "settings/backends",
    "settings/secrets",
    "ui_state",
    "user",
    # Inputs and outputs.
    "inputs/image",
    "inputs/image_masks",
    "inputs/video",
    "inputs/voice",
    "inputs/prompt_captioning",
    "outputs/image",
    "outputs/image_metadata",
    "outputs/image_latents",
    "outputs/video",
    "outputs/voice",
    "outputs/prompt_captioning",
    "outputs/project_packages",
    # Runtime, logs, caches, extensions, and admin engine state.
    "logs",
    *SURFACE_RUNTIME_LOG_DIRECTORIES,
    "cache",
    "tmp",
    "runtime",
    "runtime/jobs",
    "runtime/jobs/image",
    "runtime/jobs/video",
    "runtime/jobs/voice",
    "runtime/jobs/prompt_captioning",
    "runtime/jobs/assistant",
    "runtime/jobs/roleplay",
    "runtime/image_jobs",
    "extensions/image",
    "extensions/registry",
    "admin/engine",
    "admin/engine/index_jobs",
    "admin/engine/chroma_exports",
    "admin/engine/chroma_imports",
    "vector_store",
    "models",
)


DEFAULT_ASSISTANT_PROFILE: Final[dict[str, Any]] = {
    "profile_id": "neo_assistant_v2",
    "display_name": "Neo Assistant",
    "default_project_id": ASSISTANT_DEFAULT_PROJECT_ID,
    "default_mode": "general",
    "memory_source": "memory_engine",
    "legacy_memory_source": "admin_engine",
    "retrieval_profile": "smart",
    "tone": "clear, practical, project-aware",
    "updated_at": "",
}

DEFAULT_ASSISTANT_PROJECT: Final[dict[str, Any]] = {
    "project_id": ASSISTANT_DEFAULT_PROJECT_ID,
    "scope_id": ASSISTANT_DEFAULT_PROJECT_ID,
    "name": "General Assistant",
    "type": "assistant_workspace",
    "description": "Default Assistant workspace for uncategorized questions, planning, and cross-surface coordination.",
    "notes": "",
    "status": "active",
    "created_at": "",
    "updated_at": "",
}

DEFAULT_LEGACY_WORKSPACE: Final[dict[str, Any]] = {
    "project_id": "general",
    "name": "General",
    "type": "general",
    "status": "active",
    "description": "Default legacy compatibility workspace for Admin delivery tools and historical Project Workspace routes.",
    "notes": "",
    "surfaces": ["admin"],
    "tags": ["general", "legacy_project_workspace", "compatibility"],
    "memory_namespace": "project:general",
    "created_at": "",
    "updated_at": "",
    "metadata": {
        "compatibility_layer": True,
        "legacy_layer": True,
        "project_workspace_layer": "legacy_creator_project_workspace",
        "assistant_scope_is_primary": True,
        "normal_surface_mount_allowed": False,
    },
}

BACKEND_PROFILE_STORE_SCHEMA_ID: Final[str] = "neo.runtime_data.backend_profile_store.v1"
BACKEND_PROFILE_TEMPLATE_RELATIVE_PATH: Final[str] = "neo_app/providers/backend_profiles.json"
BACKEND_PROFILE_RUNTIME_RELATIVE_PATH: Final[str] = "neo_data/settings/backends/backend_profiles.json"
BACKEND_API_KEY_SECRET_STORE_SCHEMA_ID: Final[str] = "neo.runtime_data.backend_api_key_secret_store.v1"
BACKEND_API_KEY_SECRET_RUNTIME_RELATIVE_PATH: Final[str] = "neo_data/settings/secrets/backend_api_keys.json"


def backend_profile_template_path(root_dir: Path | str | None = None) -> Path:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    return root / BACKEND_PROFILE_TEMPLATE_RELATIVE_PATH


def backend_profile_runtime_path(root_dir: Path | str | None = None) -> Path:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    return root / BACKEND_PROFILE_RUNTIME_RELATIVE_PATH


def backend_api_key_secret_runtime_path(root_dir: Path | str | None = None) -> Path:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    return root / BACKEND_API_KEY_SECRET_RUNTIME_RELATIVE_PATH


def default_backend_api_key_secret_payload() -> dict[str, Any]:
    return {
        "schema_id": BACKEND_API_KEY_SECRET_STORE_SCHEMA_ID,
        "secrets": {},
        "metadata": {
            "runtime_store": True,
            "repo_template": False,
            "plaintext_local_store": True,
            "policy": "local_only_gitignored_neo_data",
        },
    }


def default_backend_profile_payload() -> dict[str, Any]:
    return {
        "profile_registry_version": "0.2.0-unified-backend-profile-schema",
        "defaults": {},
        "profiles": [],
        "metadata": {
            "seed_source": "empty_runtime_profile_store",
            "runtime_store": True,
        },
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_neo_data_root(root_dir: Path | str | None = None) -> Path:
    """Return the Neo runtime data root for a repo root.

    Neo currently treats `neo_data/` as repo-adjacent runtime state. Keep this
    helper centralized so future portable/profile roots can be added without
    touching every surface module.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    return root / "neo_data"


def _display_path(path: Path, root_dir: Path) -> str:
    try:
        return path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _json_stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _safe_backend_profile_ref(value: Any, fallback: str = "backend_profile") -> str:
    raw = str(value or fallback).strip().lower()
    clean = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw).strip("_")
    return clean or fallback


def _mask_backend_secret(value: Any) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    return f"••••{clean[-4:]}" if len(clean) >= 4 else "••••"


def _normalize_backend_auth_mode(value: Any, default: str = "env") -> str:
    mode = str(value or default or "env").strip().lower()
    return mode if mode in {"env", "manual", "none"} else default


def _backend_profile_secret_metadata(profile_id: str, secret_value: str) -> dict[str, Any]:
    preview = _mask_backend_secret(secret_value)
    return {
        "api_key_saved": bool(secret_value),
        "api_key_ref": _safe_backend_profile_ref(profile_id),
        "api_key_preview": preview,
        "api_key_storage": "local_secret_store",
    }


def migrate_backend_profile_api_keys_to_secret_store(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Migrate legacy inline Backend Profile API keys into neo_data secrets.

    Older builds could save ``connection.api_key_value`` inside the Backend
    Profile JSON. Pass S/T made the local secret store the real key home; Pass W
    makes the migration happen at startup/bootstrap too, not only when the Admin
    Backends module happens to read the profile payload.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    ensure_neo_data_root(root)
    runtime_path = backend_profile_runtime_path(root)
    secret_path = backend_api_key_secret_runtime_path(root)
    ensure_backend_api_key_secret_store(root)

    result: dict[str, Any] = {
        "ok": True,
        "schema_id": "neo.runtime_data.backend_profile_secret_migration.v1",
        "migration_pass": "Pass W",
        "runtime_profile_path": _display_path(runtime_path, root),
        "secret_store_path": _display_path(secret_path, root),
        "raw_keys_migrated": 0,
        "raw_keys_removed": 0,
        "manual_metadata_backfilled": 0,
        "stale_manual_metadata_removed": 0,
        "profiles_checked": 0,
        "profile_store_updated": False,
        "secret_store_updated": False,
        "raw_key_values_returned": False,
    }
    if not runtime_path.exists():
        result["status"] = "profile_store_missing"
        return result

    payload = _read_json(runtime_path, default_backend_profile_payload())
    if not isinstance(payload, dict):
        payload = default_backend_profile_payload()
    profiles = payload.get("profiles") if isinstance(payload.get("profiles"), list) else []
    secret_payload = _read_json(secret_path, default_backend_api_key_secret_payload())
    if not isinstance(secret_payload, dict):
        secret_payload = default_backend_api_key_secret_payload()
    secret_payload.setdefault("schema_id", BACKEND_API_KEY_SECRET_STORE_SCHEMA_ID)
    secrets = secret_payload.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}
        secret_payload["secrets"] = secrets

    profile_changed = False
    secret_changed = False
    stamp = now_iso()

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        result["profiles_checked"] += 1
        connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
        if not isinstance(connection, dict):
            connection = {}
        profile_id = str(profile.get("profile_id") or connection.get("api_key_ref") or "backend_profile").strip() or "backend_profile"
        secret_ref = _safe_backend_profile_ref(profile_id)
        raw_key_present = "api_key_value" in connection
        raw_key = str(connection.get("api_key_value") or "").strip()
        auth_mode = _normalize_backend_auth_mode(connection.get("auth_mode") or connection.get("api_key_mode"), "none")

        if raw_key:
            # Legacy inline keys are treated as local manual keys. The runtime
            # profile keeps only password-style metadata; the raw value moves to
            # the local neo_data secret store.
            secrets[secret_ref] = {
                "value": raw_key,
                "provider_id": str(profile.get("provider_id") or ""),
                "surface": str(profile.get("surface") or ""),
                "profile_id": profile_id,
                "preview": _mask_backend_secret(raw_key),
                "migrated_from": "connection.api_key_value",
                "migration_pass": "Pass W",
                "updated_at": stamp,
            }
            connection["auth_mode"] = "manual"
            connection["api_key_mode"] = "manual"
            connection.update(_backend_profile_secret_metadata(profile_id, raw_key))
            connection["api_key_source"] = "manual"
            result["raw_keys_migrated"] += 1
            secret_changed = True
            profile_changed = True
        elif auth_mode == "manual":
            secret_record = secrets.get(secret_ref) if isinstance(secrets.get(secret_ref), dict) else {}
            secret_value = str((secret_record or {}).get("value") or "").strip()
            before = {key: connection.get(key) for key in ("api_key_saved", "api_key_ref", "api_key_preview", "api_key_storage", "api_key_source")}
            connection.update(_backend_profile_secret_metadata(profile_id, secret_value))
            connection["api_key_source"] = "manual"
            after = {key: connection.get(key) for key in ("api_key_saved", "api_key_ref", "api_key_preview", "api_key_storage", "api_key_source")}
            if before != after:
                result["manual_metadata_backfilled"] += 1
                profile_changed = True
        elif auth_mode in {"env", "none"}:
            stale_keys = ("api_key_saved", "api_key_ref", "api_key_preview", "api_key_storage", "api_key_source")
            if any(key in connection for key in stale_keys):
                for key in stale_keys:
                    connection.pop(key, None)
                result["stale_manual_metadata_removed"] += 1
                profile_changed = True

        if raw_key_present:
            connection.pop("api_key_value", None)
            result["raw_keys_removed"] += 1
            profile_changed = True

        profile["connection"] = connection

    if profile_changed:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {
            **metadata,
            "runtime_store": True,
            "backend_api_key_migration_pass": "Pass W",
            "raw_key_values_allowed": False,
            "raw_key_values_returned_to_frontend": False,
            "api_key_secret_store": _display_path(secret_path, root),
            "migrated_at": stamp,
        }
        _write_json(runtime_path, payload)
    if secret_changed:
        metadata = secret_payload.get("metadata") if isinstance(secret_payload.get("metadata"), dict) else {}
        secret_payload["metadata"] = {
            **metadata,
            "runtime_store": True,
            "secret_store": True,
            "plaintext_local_store": True,
            "policy": "local_only_gitignored_neo_data",
            "migration_pass": "Pass W",
            "updated_at": stamp,
        }
        _write_json(secret_path, secret_payload)

    result["profile_store_updated"] = profile_changed
    result["secret_store_updated"] = secret_changed
    result["status"] = "migrated" if profile_changed or secret_changed else "no_migration_needed"
    return result


def _assistant_scope_notes(scope: dict[str, Any]) -> str:
    lanes = ", ".join(str(item) for item in scope.get("memory_lanes") or [])
    return (
        "Built-in Assistant Scope.\n"
        f"Surface: {scope.get('surface') or 'assistant'}\n"
        f"Workspace ID: {scope.get('workspace_id') or ''}\n"
        f"Memory lanes: {lanes}"
    ).strip()


def _assistant_scope_record(scope: dict[str, Any], stamp: str) -> dict[str, Any]:
    scope = deepcopy(scope)
    project_id = str(scope.get("project_id") or ASSISTANT_DEFAULT_PROJECT_ID).strip() or ASSISTANT_DEFAULT_PROJECT_ID
    memory_lanes = list(scope.get("memory_lanes") or [])
    metadata = {
        "assistant_scope": True,
        "builtin_scope": True,
        "scope_model": "assistant_internal_scope",
        "workspace_id": str(scope.get("workspace_id") or project_id),
        "surface": str(scope.get("surface") or "assistant"),
        "memory_lanes": memory_lanes,
        "seed_schema_id": ASSISTANT_SCOPE_SEED_SCHEMA_ID,
        "runtime_only": True,
    }
    return {
        "project_id": project_id,
        "scope_id": project_id,
        "name": str(scope.get("name") or project_id),
        "type": str(scope.get("type") or "assistant_scope"),
        "description": str(scope.get("description") or ""),
        "notes": str(scope.get("notes") or _assistant_scope_notes(scope)),
        "status": str(scope.get("status") or "active"),
        "created_at": stamp,
        "updated_at": stamp,
        "metadata": metadata,
    }


def _assistant_scope_index_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("project_id", "scope_id", "name", "type", "description", "notes", "status", "created_at", "updated_at", "metadata")
    return {key: record.get(key) for key in keys if key in record}


def _merge_builtin_assistant_scope(existing: dict[str, Any], default: dict[str, Any], stamp: str) -> dict[str, Any]:
    merged = dict(existing or {})
    project_id = str(default.get("project_id") or merged.get("project_id") or ASSISTANT_DEFAULT_PROJECT_ID).strip() or ASSISTANT_DEFAULT_PROJECT_ID
    merged["project_id"] = project_id
    merged.setdefault("scope_id", project_id)
    # Keep user-edited display fields, but backfill missing built-in fields.
    for key in ("name", "type", "description", "notes", "status"):
        if not str(merged.get(key) or "").strip():
            merged[key] = default.get(key)
    merged.setdefault("created_at", default.get("created_at") or stamp)
    merged["updated_at"] = merged.get("updated_at") or default.get("updated_at") or stamp
    metadata = merged.get("metadata") if isinstance(merged.get("metadata"), dict) else {}
    default_metadata = default.get("metadata") if isinstance(default.get("metadata"), dict) else {}
    # Built-in routing metadata must stay authoritative. Custom metadata survives beside it.
    merged["metadata"] = {**metadata, **default_metadata}
    return merged


def ensure_neo_data_root(root_dir: Path | str | None = None) -> Path:
    neo_data = get_neo_data_root(root_dir)
    neo_data.mkdir(parents=True, exist_ok=True)
    return neo_data


def ensure_runtime_data_tree(root_dir: Path | str | None = None) -> dict[str, Any]:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    neo_data = ensure_neo_data_root(root)
    created: list[str] = []
    existing: list[str] = []
    for directory in RUNTIME_DIRECTORIES:
        path = neo_data / directory
        already_exists = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        rel = _display_path(path, root)
        (existing if already_exists else created).append(rel)
    return {
        "ok": True,
        "schema_id": RUNTIME_SCHEMA_ID,
        "neo_data_root": _display_path(neo_data, root),
        "created_directories": created,
        "existing_directories": existing,
        "directory_count": len(RUNTIME_DIRECTORIES),
    }


def ensure_assistant_base_seed(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Create the base Assistant storage expected on a clean clone.

    This writes the profile, sessions index, and a durable `general` fallback.
    Full built-in Assistant scopes are seeded by `ensure_assistant_scope_seed`.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    neo_data = ensure_neo_data_root(root)
    assistant_root = neo_data / "assistant"
    projects_dir = assistant_root / "projects"
    sessions_dir = assistant_root / "sessions"
    context_dir = assistant_root / "context_items"
    captures_dir = assistant_root / "memory_captures"
    surface_context_dir = assistant_root / "surface_context"
    for path in (assistant_root, projects_dir, sessions_dir, context_dir, captures_dir, surface_context_dir):
        path.mkdir(parents=True, exist_ok=True)

    stamp = now_iso()
    created_files: list[str] = []
    profile_path = assistant_root / "assistant_profile.json"
    if not profile_path.exists():
        profile = dict(DEFAULT_ASSISTANT_PROFILE)
        profile["updated_at"] = stamp
        _write_json(profile_path, profile)
        created_files.append(_display_path(profile_path, root))

    projects_index_path = assistant_root / "assistant_projects_index.json"
    general_project_path = projects_dir / "general.json"
    if not projects_index_path.exists():
        project = _assistant_scope_record(ASSISTANT_BUILTIN_SCOPES[0], stamp)
        _write_json(projects_index_path, {"projects": [project], "updated_at": stamp})
        created_files.append(_display_path(projects_index_path, root))
        if not general_project_path.exists():
            _write_json(general_project_path, project)
            created_files.append(_display_path(general_project_path, root))
    else:
        index = _read_json(projects_index_path, {"projects": []})
        projects = index.get("projects") if isinstance(index, dict) else []
        projects = projects if isinstance(projects, list) else []
        if not any((item or {}).get("project_id") == ASSISTANT_DEFAULT_PROJECT_ID for item in projects):
            project = _assistant_scope_record(ASSISTANT_BUILTIN_SCOPES[0], stamp)
            projects.insert(0, project)
            _write_json(projects_index_path, {"projects": projects, "updated_at": stamp})
            created_files.append(_display_path(projects_index_path, root))
            if not general_project_path.exists():
                _write_json(general_project_path, project)
                created_files.append(_display_path(general_project_path, root))

    sessions_index_path = assistant_root / "assistant_sessions_index.json"
    if not sessions_index_path.exists():
        _write_json(sessions_index_path, {"sessions": [], "updated_at": stamp})
        created_files.append(_display_path(sessions_index_path, root))

    return {
        "ok": True,
        "schema_id": "neo.runtime_data.assistant_base_seed.v1",
        "created_files": created_files,
        "assistant_root": _display_path(assistant_root, root),
        "default_project_id": ASSISTANT_DEFAULT_PROJECT_ID,
    }


def ensure_assistant_scope_seed(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Seed built-in Assistant scopes on first run without overwriting custom scopes.

    The UI can still call them projects for compatibility, but this bootstrap
    marks them as Assistant internal scopes and writes one durable JSON file per
    scope under `neo_data/assistant/projects/`.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    neo_data = ensure_neo_data_root(root)
    assistant_root = neo_data / "assistant"
    projects_dir = assistant_root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    base = ensure_assistant_base_seed(root)
    stamp = now_iso()
    index_path = assistant_root / "assistant_projects_index.json"
    index = _read_json(index_path, {"projects": []})
    existing_projects = index.get("projects") if isinstance(index, dict) else []
    existing_projects = existing_projects if isinstance(existing_projects, list) else []

    existing_by_id: dict[str, dict[str, Any]] = {}
    custom_projects: list[dict[str, Any]] = []
    builtin_ids = {str(scope.get("project_id") or "") for scope in ASSISTANT_BUILTIN_SCOPES}
    for item in existing_projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or "").strip()
        if not project_id:
            continue
        if project_id in builtin_ids:
            existing_by_id.setdefault(project_id, item)
        else:
            custom_projects.append(item)

    created_files: list[str] = list(base.get("created_files") or [])
    updated_files: list[str] = []
    seeded_scope_ids: list[str] = []
    final_projects: list[dict[str, Any]] = []

    for scope in ASSISTANT_BUILTIN_SCOPES:
        default_record = _assistant_scope_record(scope, stamp)
        project_id = default_record["project_id"]
        record_path = projects_dir / f"{project_id}.json"
        disk_record = _read_json(record_path, {})
        existing = {**existing_by_id.get(project_id, {})}
        if isinstance(disk_record, dict) and disk_record:
            existing = {**existing, **disk_record}
        was_missing = not existing
        merged = _merge_builtin_assistant_scope(existing, default_record, stamp)
        seeded_scope_ids.append(project_id)
        final_projects.append(_assistant_scope_index_record(merged))

        if not record_path.exists():
            _write_json(record_path, merged)
            created_files.append(_display_path(record_path, root))
        elif _json_stable(disk_record) != _json_stable(merged):
            _write_json(record_path, merged)
            updated_files.append(_display_path(record_path, root))
        elif was_missing:
            created_files.append(_display_path(record_path, root))

    seen = set(seeded_scope_ids)
    for project in custom_projects:
        project_id = str(project.get("project_id") or "").strip()
        if not project_id or project_id in seen:
            continue
        seen.add(project_id)
        final_projects.append(project)

    existing_updated_at = index.get("updated_at") if isinstance(index, dict) else ""
    new_index = {
        "schema_id": ASSISTANT_SCOPE_SEED_SCHEMA_ID,
        "scope_model": "assistant_internal_scope",
        "projects": final_projects,
        "builtin_scope_ids": seeded_scope_ids,
        "updated_at": existing_updated_at or stamp,
    }
    index_compare = dict(index) if isinstance(index, dict) else {}
    new_compare = dict(new_index)
    index_compare.pop("updated_at", None)
    new_compare.pop("updated_at", None)
    if _json_stable(index_compare) != _json_stable(new_compare):
        new_index["updated_at"] = stamp
        _write_json(index_path, new_index)
        if _display_path(index_path, root) not in created_files:
            updated_files.append(_display_path(index_path, root))

    return {
        "ok": True,
        "schema_id": ASSISTANT_SCOPE_SEED_SCHEMA_ID,
        "scope_model": "assistant_internal_scope",
        "assistant_root": _display_path(assistant_root, root),
        "created_files": created_files,
        "updated_files": updated_files,
        "builtin_scope_ids": seeded_scope_ids,
        "builtin_scope_count": len(seeded_scope_ids),
        "custom_scope_count": len(final_projects) - len(seeded_scope_ids),
        "default_project_id": ASSISTANT_DEFAULT_PROJECT_ID,
    }


def ensure_compat_project_workspace_seed(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Seed legacy Project Workspace files without making it the global UI model."""

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    neo_data = ensure_neo_data_root(root)
    workspace_root = neo_data / "projects"
    workspaces_dir = workspace_root / "workspaces"
    for path in (
        workspace_root,
        workspaces_dir,
        workspace_root / "context",
        workspace_root / "links",
        workspace_root / "handoffs",
        workspace_root / "timeline",
        workspace_root / "briefs",
        workspace_root / "brief_exports",
        workspace_root / "milestones",
        workspace_root / "deliverables",
        workspace_root / "status_exports",
        workspace_root / "review_queue",
        workspace_root / "packages",
        workspace_root / "surface_actions",
    ):
        path.mkdir(parents=True, exist_ok=True)

    stamp = now_iso()
    created_files: list[str] = []
    index_path = workspace_root / "project_workspace_index.json"
    active_path = workspace_root / "active_project.json"
    general_workspace_path = workspaces_dir / "general.json"
    if not index_path.exists():
        workspace = dict(DEFAULT_LEGACY_WORKSPACE)
        workspace["created_at"] = stamp
        workspace["updated_at"] = stamp
        _write_json(index_path, {"projects": [workspace], "updated_at": stamp})
        _write_json(general_workspace_path, workspace)
        created_files.extend([_display_path(index_path, root), _display_path(general_workspace_path, root)])
    else:
        index = _read_json(index_path, {"projects": []})
        projects = index.get("projects") if isinstance(index, dict) else []
        projects = projects if isinstance(projects, list) else []
        if not any((item or {}).get("project_id") == "general" for item in projects):
            workspace = dict(DEFAULT_LEGACY_WORKSPACE)
            workspace["created_at"] = stamp
            workspace["updated_at"] = stamp
            projects.insert(0, workspace)
            _write_json(index_path, {"projects": projects, "updated_at": stamp})
            _write_json(general_workspace_path, workspace)
            created_files.extend([_display_path(index_path, root), _display_path(general_workspace_path, root)])

    if not active_path.exists():
        _write_json(active_path, {"project_id": "general", "updated_at": stamp, "compatibility_layer": True})
        created_files.append(_display_path(active_path, root))

    return {
        "ok": True,
        "schema_id": "neo.runtime_data.project_workspace_compat_seed.v1",
        "created_files": created_files,
        "workspace_root": _display_path(workspace_root, root),
        "default_project_id": "general",
        "compatibility_layer": True,
        "legacy_layer": True,
        "assistant_scope_is_primary": True,
        "normal_surface_mount_allowed": False,
    }


def ensure_backend_profile_store(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Seed the runtime Backend Profile store from the repo template.

    `neo_app/providers/backend_profiles.json` is the shipped template only.
    User edits made from Admin > Backends are runtime data and belong under
    `neo_data/settings/backends/backend_profiles.json` so restarts preserve them
    and repo uploads never depend on committed `neo_data/`.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    ensure_neo_data_root(root)
    runtime_path = backend_profile_runtime_path(root)
    template_path = backend_profile_template_path(root)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []
    seeded_from_template = False
    seed_source = "existing_runtime_store"
    if not runtime_path.exists():
        if template_path.exists():
            payload = _read_json(template_path, default_backend_profile_payload())
            seed_source = _display_path(template_path, root)
            seeded_from_template = True
        else:
            payload = default_backend_profile_payload()
            seed_source = "empty_default_payload"
        if isinstance(payload, dict):
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            payload["metadata"] = {
                **metadata,
                "runtime_store": True,
                "template_path": _display_path(template_path, root),
                "runtime_path": _display_path(runtime_path, root),
                "seeded_from_template": seeded_from_template,
                "seeded_at": now_iso(),
            }
        _write_json(runtime_path, payload)
        created_files.append(_display_path(runtime_path, root))

    migration = migrate_backend_profile_api_keys_to_secret_store(root)

    return {
        "ok": True,
        "schema_id": BACKEND_PROFILE_STORE_SCHEMA_ID,
        "runtime_store": True,
        "runtime_path": _display_path(runtime_path, root),
        "template_path": _display_path(template_path, root),
        "created_files": created_files,
        "seeded_from_template": seeded_from_template,
        "seed_source": seed_source,
        "profile_store_location": "neo_data",
        "repo_template_is_read_only_seed": True,
        "migration": migration,
        "raw_key_values_allowed": False,
    }




def ensure_backend_api_key_secret_store(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Ensure the local API-key secret store exists under neo_data.

    This is a local plaintext store for development/self-hosted use. It is
    protected by the repo policy that neo_data/ is runtime-only and gitignored.
    UI-facing Backend Profile payloads must never return raw values from it.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    ensure_neo_data_root(root)
    secret_path = backend_api_key_secret_runtime_path(root)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    created_files: list[str] = []
    if not secret_path.exists():
        _write_json(secret_path, default_backend_api_key_secret_payload())
        created_files.append(_display_path(secret_path, root))
    return {
        "ok": True,
        "schema_id": BACKEND_API_KEY_SECRET_STORE_SCHEMA_ID,
        "runtime_store": True,
        "secret_store": True,
        "runtime_path": _display_path(secret_path, root),
        "created_files": created_files,
        "store_location": "neo_data/settings/secrets",
        "plaintext_local_store": True,
        "raw_values_returned_to_frontend": False,
        "repo_template": False,
    }


def ensure_runtime_manifest(root_dir: Path | str | None = None, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    neo_data = ensure_neo_data_root(root)
    manifest_path = neo_data / ".neo_runtime_manifest.json"
    existing = _read_json(manifest_path, {})
    created_at = existing.get("created_at") if isinstance(existing, dict) else None
    stamp = now_iso()
    manifest = {
        "schema_id": RUNTIME_SCHEMA_ID,
        "created_by": "Neo Studio V2",
        "runtime_only": True,
        "version": 1,
        "created_at": created_at or stamp,
        "updated_at": stamp,
        "neo_data_root": _display_path(neo_data, root),
        "policy": {
            "repo_should_not_depend_on_committed_neo_data": True,
            "safe_to_recreate_on_first_run": True,
            "assistant_scope_is_primary_project_model": True,
            "legacy_project_workspace_is_compatibility_layer": True,
            "unified_surface_log_folders": True,
            "backend_api_keys_saved_in_local_secret_store": True,
            "backend_profile_json_must_not_store_raw_api_keys": True,
        },
    }
    if extra:
        manifest["bootstrap"] = extra
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": _display_path(manifest_path, root)}


def bootstrap_neo_runtime_data(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Create Neo's runtime-only data tree and minimal seed files.

    This is intentionally idempotent. It may run at import/startup time, during
    tests, or from setup scripts without overwriting user-created projects.
    """

    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR
    tree = ensure_runtime_data_tree(root)
    assistant = ensure_assistant_base_seed(root)
    assistant_scopes = ensure_assistant_scope_seed(root)
    project_workspace = ensure_compat_project_workspace_seed(root)
    backend_profiles = ensure_backend_profile_store(root)
    backend_api_key_secrets = ensure_backend_api_key_secret_store(root)
    manifest = ensure_runtime_manifest(
        root,
        extra={
            "directory_count": tree.get("directory_count"),
            "created_directory_count": len(tree.get("created_directories") or []),
            "assistant_created_file_count": len(assistant.get("created_files") or []),
            "assistant_scope_count": assistant_scopes.get("builtin_scope_count"),
            "assistant_scope_seed_schema_id": assistant_scopes.get("schema_id"),
            "project_workspace_created_file_count": len(project_workspace.get("created_files") or []),
            "backend_profile_store_created_file_count": len(backend_profiles.get("created_files") or []),
            "backend_profile_store_runtime_path": backend_profiles.get("runtime_path"),
            "backend_api_key_secret_store_created_file_count": len(backend_api_key_secrets.get("created_files") or []),
            "backend_api_key_secret_store_runtime_path": backend_api_key_secrets.get("runtime_path"),
            "surface_log_directory_count": len(SURFACE_RUNTIME_LOG_DIRECTORIES),
        },
    )
    return {
        "ok": True,
        "schema_id": RUNTIME_SCHEMA_ID,
        "status": "ready",
        "neo_data_root": tree["neo_data_root"],
        "manifest": manifest,
        "tree": tree,
        "assistant": assistant,
        "assistant_scopes": assistant_scopes,
        "project_workspace": project_workspace,
        "backend_profiles": backend_profiles,
        "backend_api_key_secrets": backend_api_key_secrets,
        "surface_logs": {
            "ok": True,
            "schema_id": "neo.runtime_data.surface_logs.pass_k.v1",
            "root": _display_path(root / "neo_data" / "logs", root),
            "directories": list(SURFACE_RUNTIME_LOG_DIRECTORIES),
            "directory_count": len(SURFACE_RUNTIME_LOG_DIRECTORIES),
        },
    }
