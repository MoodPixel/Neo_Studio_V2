from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

IDENTITY_SCHEMA_ID: Final[str] = "neo.context_identity.canonical.v1"
IDENTITY_PHASE: Final[str] = "1"

CANONICAL_SURFACE_IDS: Final[frozenset[str]] = frozenset({
    "global",
    "assistant",
    "admin",
    "image",
    "video",
    "voice",
    "prompt_captioning",
    "roleplay",
    "board",
    "project",
})

# Assistant Scope -> canonical origin/runtime surface. These are context-priority
# identities, not delivery-project identities.
SCOPE_SURFACE_ALIASES: Final[dict[str, str]] = {
    "general": "global",
    "image_workspace": "image",
    "prompt_captioning_workspace": "prompt_captioning",
    "video_workspace": "video",
    "voice_workspace": "voice",
    "roleplay_workspace": "roleplay",
    "client_work_workspace": "assistant",
    "neo_development_workspace": "admin",
}

# Only true surface workspaces are reversible automatically. General/client/admin
# scopes are policy workspaces and should not be inferred merely from a surface.
SURFACE_SCOPE_ALIASES: Final[dict[str, str]] = {
    "image": "image_workspace",
    "prompt_captioning": "prompt_captioning_workspace",
    "video": "video_workspace",
    "voice": "voice_workspace",
    "roleplay": "roleplay_workspace",
}

# Compatibility IDs used by the existing unified-memory store. Phase 1 does not
# rewrite existing SQLite rows, so retrieval must translate canonical context to
# the storage namespaces that already exist.
SURFACE_MEMORY_PROJECT_ALIASES: Final[dict[str, str]] = {
    "image": "image",
    "prompt_captioning": "prompt_captioning",
    "video": "video",
    "voice": "voice",
    "roleplay": "roleplay",
}


def normalize_identity_id(value: Any, default: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = re.sub(r"[^a-z0-9_:.]+", "", text)[:160]
    return text or default


def canonical_surface_for_scope(scope_id: Any, default: str = "assistant") -> str:
    scope = normalize_identity_id(scope_id)
    return SCOPE_SURFACE_ALIASES.get(scope, normalize_identity_id(default, "assistant"))


def builtin_scope_for_surface(surface_id: Any, default: str = "") -> str:
    surface = normalize_identity_id(surface_id)
    return SURFACE_SCOPE_ALIASES.get(surface, default)


def is_builtin_scope(scope_id: Any) -> bool:
    return normalize_identity_id(scope_id) in SCOPE_SURFACE_ALIASES


@dataclass(slots=True)
class CanonicalContextIdentity:
    """Canonical context identity used at Assistant/Memory boundaries.

    Canonical fields:
    - surface_id: origin/runtime surface
    - scope_id: Assistant context-priority/sandbox identity
    - project_id: real client/work/delivery project identity

    Existing Neo stores still overload ``project_id`` in several places. Phase 1
    preserves those rows and exposes their translation under ``compatibility``.
    """

    surface_id: str = "assistant"
    scope_id: str = "general"
    project_id: str | None = None
    workspace_id: str = ""
    source: str = "resolved"
    compatibility: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_id": IDENTITY_SCHEMA_ID,
            "phase": IDENTITY_PHASE,
            "surface_id": self.surface_id,
            "scope_id": self.scope_id,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "source": self.source,
            "compatibility": dict(self.compatibility or {}),
        }

    def memory_filter(self) -> dict[str, str]:
        compat = self.compatibility or {}
        return {
            "surface": normalize_identity_id(compat.get("memory_surface_id") or self.surface_id),
            "project_id": normalize_identity_id(compat.get("memory_project_id") or self.project_id or ""),
            "scope_id": normalize_identity_id(compat.get("memory_scope_id") or ""),
        }


def _compatibility_memory_target(surface_id: str, scope_id: str, project_id: str | None) -> tuple[str, str, str]:
    """Return the current-storage target without pretending it is canonical.

    Existing surface ingestion writes pseudo-project IDs such as ``image`` and
    ``video``. Existing Assistant Scope ingestion writes ``assistant:<scope>``.
    These aliases stay compatibility-only until later migration phases.
    """

    if project_id:
        return surface_id or "global", project_id, ""
    if surface_id in SURFACE_MEMORY_PROJECT_ALIASES:
        return surface_id, SURFACE_MEMORY_PROJECT_ALIASES[surface_id], ""
    if scope_id:
        # Assistant Scope records are currently ingested by surface_ingestion as
        # assistant:<scope_id>, even for policy workspaces such as Neo Development.
        return "assistant", f"assistant:{scope_id}", ""
    return surface_id or "global", "", ""


def resolve_canonical_identity(
    payload: dict[str, Any] | None = None,
    *,
    surface_id: Any = None,
    scope_id: Any = None,
    project_id: Any = None,
    workspace_id: Any = None,
    legacy_project_is_scope: bool = False,
    source: str = "resolved",
) -> CanonicalContextIdentity:
    """Resolve canonical identity while preserving legacy Assistant ``project_id``.

    ``legacy_project_is_scope=True`` is used only at Assistant boundaries where
    historical APIs called Assistant Scopes "projects". Generic Control Center
    callers keep normal ``project_id`` semantics unless they provide an explicit
    ``scope_id`` or canonical ``identity`` object.
    """

    data = payload if isinstance(payload, dict) else {}
    nested = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    nested_compat = nested.get("compatibility") if isinstance(nested.get("compatibility"), dict) else {}

    explicit_surface = normalize_identity_id(
        surface_id
        or nested.get("surface_id")
        or data.get("surface_id")
        or data.get("surface")
        or data.get("active_surface")
        or ""
    )
    explicit_scope = normalize_identity_id(
        scope_id
        or nested.get("scope_id")
        or data.get("scope_id")
        or metadata.get("scope_id")
        or ""
    )
    raw_project_id = normalize_identity_id(data.get("project_id") or "")
    nested_project_is_authoritative = "project_id" in nested
    explicit_project = normalize_identity_id(
        (nested.get("project_id") if nested_project_is_authoritative else None)
        or ("" if nested_project_is_authoritative else data.get("delivery_project_id"))
        or ("" if nested_project_is_authoritative else data.get("actual_project_id"))
        or ("" if nested_project_is_authoritative else data.get("linked_project_id"))
        or ("" if nested_project_is_authoritative else metadata.get("delivery_project_id"))
        or ("" if nested_project_is_authoritative else project_id)
        or ("" if nested_project_is_authoritative else (raw_project_id if raw_project_id and not legacy_project_is_scope and not is_builtin_scope(raw_project_id) else ""))
        or ""
    )
    legacy_project = normalize_identity_id(
        data.get("legacy_project_id")
        or nested_compat.get("legacy_project_id")
        or raw_project_id
        or ""
    )
    resolved_workspace = normalize_identity_id(
        workspace_id
        or nested.get("workspace_id")
        or data.get("workspace_id")
        or data.get("workspace")
        or metadata.get("workspace_id")
        or ""
    )

    # A nested canonical identity is authoritative. Otherwise historical
    # Assistant routes may still put the scope identity in project_id.
    resolved_scope = explicit_scope
    resolved_project = explicit_project or None
    consumed_legacy_project_as_scope = False
    if not resolved_scope and legacy_project:
        legacy_marks_scope = (
            legacy_project_is_scope
            or legacy_project in SCOPE_SURFACE_ALIASES
            or bool(metadata.get("assistant_scope"))
            or str(metadata.get("scope_model") or "") == "assistant_internal_scope"
        )
        if legacy_marks_scope:
            resolved_scope = legacy_project
            consumed_legacy_project_as_scope = True
            if not nested.get("project_id") and not data.get("delivery_project_id") and not data.get("actual_project_id") and not data.get("linked_project_id"):
                resolved_project = None

    if not resolved_scope and resolved_workspace.startswith("assistant_workspace_"):
        suffix = resolved_workspace.removeprefix("assistant_workspace_")
        candidate = f"{suffix}_workspace" if suffix not in {"general"} else "general"
        if candidate in SCOPE_SURFACE_ALIASES:
            resolved_scope = candidate

    if not resolved_scope:
        inferred_scope = builtin_scope_for_surface(explicit_surface)
        resolved_scope = inferred_scope or "general"

    alias_surface = canonical_surface_for_scope(resolved_scope, default="assistant")
    resolved_surface = explicit_surface
    if not resolved_surface or resolved_surface == "assistant" and alias_surface not in {"assistant", "global"}:
        resolved_surface = alias_surface
    elif resolved_scope == "general" and resolved_surface in {"", "assistant"}:
        resolved_surface = "global"
    if not resolved_surface:
        resolved_surface = alias_surface or "assistant"

    memory_surface, memory_project, memory_scope = _compatibility_memory_target(resolved_surface, resolved_scope, resolved_project)
    compatibility = {
        **nested_compat,
        "legacy_project_id": legacy_project or (resolved_scope if consumed_legacy_project_as_scope else ""),
        "legacy_project_id_role": "assistant_scope" if consumed_legacy_project_as_scope or legacy_project in SCOPE_SURFACE_ALIASES else ("delivery_project" if legacy_project else ""),
        "legacy_surface_id": normalize_identity_id(data.get("surface") or data.get("active_surface") or ""),
        "assistant_store_project_id": resolved_scope or legacy_project or "general",
        "memory_surface_id": memory_surface,
        "memory_project_id": memory_project,
        "memory_scope_id": normalize_identity_id(nested_compat.get("memory_scope_id") or data.get("memory_scope_id") or "") or memory_scope,
        "requires_storage_migration": bool((not resolved_project and memory_project) or consumed_legacy_project_as_scope),
    }

    return CanonicalContextIdentity(
        surface_id=resolved_surface,
        scope_id=resolved_scope or "general",
        project_id=resolved_project,
        workspace_id=resolved_workspace,
        source=source,
        compatibility=compatibility,
    )


def canonical_identity_payload(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return resolve_canonical_identity(payload, **kwargs).as_dict()


def memory_filter_from_payload(payload: dict[str, Any] | None = None, *, legacy_project_is_scope: bool = False) -> dict[str, Any]:
    identity = resolve_canonical_identity(payload, legacy_project_is_scope=legacy_project_is_scope, source="memory_filter")
    return {"identity": identity.as_dict(), **identity.memory_filter()}
