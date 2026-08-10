from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from neo_app.context_identity import CanonicalContextIdentity, builtin_scope_for_surface
from neo_app.memory.unified_schema import ensure_unified_memory_schema

SCOPE_PRIORITY_SCHEMA_ID = "neo.memory.scope_priority.v1"
SCOPE_PRIORITY_PHASE = "6"

_SURFACE_HINTS: dict[str, tuple[tuple[str, float], ...]] = {
    "image": (
        ("image tab", 1.0), ("image workspace", 1.0), ("image generation", 0.95),
        ("image", 0.72), ("photo", 0.65), ("portrait", 0.7), ("lora", 0.85), ("checkpoint", 0.85),
        ("sampler", 0.8), ("seed", 0.72), ("cfg", 0.72), ("sdxl", 0.9),
        ("qwen image", 0.95), ("comfyui", 0.68), ("forge", 0.68),
    ),
    "prompt_captioning": (
        ("prompt studio", 1.0), ("caption studio", 1.0), ("captioning", 0.95),
        ("batch caption", 0.95), ("caption", 0.8), ("negative prompt", 0.78),
        ("visual treatment", 0.9), ("prompt format", 0.9), ("keywords", 0.64),
    ),
    "video": (
        ("video tab", 1.0), ("video workspace", 1.0), ("video generation", 0.95),
        ("video", 0.78), ("render", 0.68), ("first frame", 0.82), ("last frame", 0.82),
        ("wan", 0.82), ("interpolation", 0.82), ("upscale video", 0.9),
    ),
    "voice": (
        ("voice tab", 1.0), ("voice workspace", 1.0), ("voice profile", 0.9),
        ("voice", 0.76), ("tts", 0.9), ("whisper", 0.85), ("reference audio", 0.9),
        ("audio render", 0.82), ("speech", 0.66),
    ),
    "roleplay": (
        ("roleplay", 0.95), ("universe", 0.86), ("canon", 0.84), ("scene", 0.66),
        ("character", 0.62), ("world lore", 0.9),
    ),
    "admin": (
        ("neo development", 0.95), ("admin", 0.78), ("memory engine", 0.88),
        ("control center", 0.86), ("backend profile", 0.78),
    ),
    "assistant": (
        ("client work", 0.9), ("client", 0.64), ("fiverr", 0.82), ("quote", 0.58),
        ("pricing", 0.62), ("delivery", 0.56),
    ),
}

_SURFACE_STORAGE: dict[str, tuple[str, str, str]] = {
    "image": ("image", "image", ""),
    "prompt_captioning": ("prompt_captioning", "prompt_captioning", ""),
    "video": ("video", "video", ""),
    "voice": ("voice", "voice", ""),
    "admin": ("assistant", "assistant:neo_development_workspace", ""),
    "assistant": ("assistant", "assistant:client_work_workspace", ""),
}

_RECALL_DISCOVERY_PATTERNS = (
    "what model did i use", "which model did i use", "what settings did i use",
    "which settings did i use", "what did i use last", "what did we use last",
    "what did i save", "what did we save", "what did i generate", "what did we generate",
    "last time", "previously used", "used before", "do you remember what", "can you remember what",
)

_GENERIC_PROJECT_IDS = {
    "assistant", "image", "prompt_captioning", "video", "voice", "roleplay",
    "assistant:general", "assistant:image_workspace", "assistant:prompt_captioning_workspace",
    "assistant:video_workspace", "assistant:voice_workspace", "assistant:roleplay_workspace",
    "assistant:client_work_workspace", "assistant:neo_development_workspace",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _terms(value: Any) -> set[str]:
    stop = {"the", "and", "for", "with", "that", "this", "from", "what", "when", "where", "which", "have", "about", "used", "use", "remember", "memory", "neo", "studio"}
    return {t for t in re.findall(r"[a-z0-9_+-]{3,}", _norm(value)) if t not in stop}


def _surface_scores(query: str) -> dict[str, float]:
    text = _norm(query)
    scores: dict[str, float] = {}
    for surface, hints in _SURFACE_HINTS.items():
        score = 0.0
        hits = 0
        for phrase, weight in hints:
            if phrase in text:
                score = max(score, weight)
                hits += 1
        if hits > 1:
            score = min(1.0, score + min(0.15, (hits - 1) * 0.04))
        if score >= 0.62:
            scores[surface] = round(score, 4)
    return scores


def _needs_general_recall_discovery(query: str) -> bool:
    text = _norm(query)
    return any(pattern in text for pattern in _RECALL_DISCOVERY_PATTERNS)


def _target(*, target_id: str, surface: str, project_id: str = "", scope_id: str = "", priority: float, reason: str, source: str, canonical_surface: str = "", canonical_scope: str = "", hard_boundary: bool = False) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "surface": str(surface or ""),
        "project_id": str(project_id or ""),
        "scope_id": str(scope_id or ""),
        "priority": round(max(0.0, min(1.0, float(priority))), 4),
        "reason": reason,
        "source": source,
        "canonical_surface": canonical_surface or surface or "global",
        "canonical_scope": canonical_scope,
        "hard_boundary": bool(hard_boundary),
    }


def _match_score(query: str, *, key: str, label: str = "", description: str = "") -> float:
    q = _norm(query)
    key_n = _norm(key).replace("_", " ").replace(":", " ")
    label_n = _norm(label)
    if label_n and len(label_n) >= 4 and label_n in q:
        return 1.0
    if key_n and len(key_n) >= 4 and key_n in q:
        return 0.98
    qterms = _terms(q)
    identity_terms = _terms(f"{key_n} {label_n}")
    if not identity_terms:
        return 0.0
    overlap = qterms & identity_terms
    if len(identity_terms) >= 2 and len(overlap) >= min(2, len(identity_terms)):
        return min(0.94, 0.72 + (len(overlap) * 0.07))
    if len(identity_terms) == 1 and identity_terms <= qterms and len(next(iter(identity_terms))) >= 6:
        return 0.82
    return 0.0


def _db_project_targets(db_path: Path, query: str, *, limit: int = 3) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            ensure_unified_memory_schema(conn)
            rows = conn.execute(
                "SELECT project_id, label, surface, project_type, description, status FROM neo_memory_projects WHERE status='active' ORDER BY updated_at DESC LIMIT 250"
            ).fetchall()
    except Exception:
        return []
    matched: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        pid = str(item.get("project_id") or "")
        if not pid or pid in _GENERIC_PROJECT_IDS:
            continue
        score = _match_score(query, key=pid.removeprefix("project:").removeprefix("assistant:"), label=str(item.get("label") or ""), description=str(item.get("description") or ""))
        if score < 0.8:
            continue
        matched.append((score, _target(
            target_id=f"project:{pid}",
            surface=str(item.get("surface") or ""),
            project_id=pid,
            priority=min(0.98, 0.9 + (score * 0.08)),
            reason="query_mentions_project",
            source="memory_project_registry",
            canonical_surface=str(item.get("surface") or "global"),
        )))
    matched.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in matched[:limit]]


def _roleplay_scope_target(db_path: Path, query: str) -> dict[str, Any] | None:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            ensure_unified_memory_schema(conn)
            rows = conn.execute(
                """
                SELECT scope_id, scope_key, label, scope_type, project_id
                FROM neo_memory_scopes
                WHERE surface='roleplay' AND project_id='roleplay'
                  AND scope_type IN ('roleplay_scope', 'scene_packet')
                ORDER BY updated_at DESC LIMIT 300
                """
            ).fetchall()
    except Exception:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        item = dict(row)
        score = _match_score(query, key=str(item.get("scope_key") or ""), label=str(item.get("label") or ""))
        if score < 0.82:
            continue
        target = _target(
            target_id=f"roleplay:{item.get('scope_id')}",
            surface="roleplay",
            project_id="roleplay",
            scope_id=str(item.get("scope_id") or ""),
            priority=0.99,
            reason="explicit_roleplay_sandbox",
            source="roleplay_scope_registry",
            canonical_surface="roleplay",
            canonical_scope=str(item.get("scope_key") or ""),
            hard_boundary=True,
        )
        if best is None or score > best[0]:
            best = (score, target)
    return best[1] if best else None


def _append_unique(targets: list[dict[str, Any]], target: dict[str, Any]) -> None:
    key = (target.get("surface") or "", target.get("project_id") or "", target.get("scope_id") or "")
    for existing in targets:
        existing_key = (existing.get("surface") or "", existing.get("project_id") or "", existing.get("scope_id") or "")
        if existing_key == key:
            if float(target.get("priority") or 0) > float(existing.get("priority") or 0):
                existing.update(target)
            return
    targets.append(target)


def build_scope_priority_plan(query: str, identity: CanonicalContextIdentity, *, db_path: Path) -> dict[str, Any]:
    """Build query-driven memory targets without turning Scope into a hard prison.

    The plan is intentionally bounded. It always preserves the active context as
    the primary lane, adds General durable memory where safe, and expands only to
    surfaces/projects strongly indicated by the current query. Roleplay detailed
    memory requires an explicit matched sandbox.
    """
    targets: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    scores = _surface_scores(query)
    active_surface = identity.surface_id or "global"
    active_scope = identity.scope_id or "general"
    active_project = identity.project_id or ""
    compat = identity.compatibility or {}

    if active_project:
        # Canonical future rows may use the real project ID on any surface.
        _append_unique(targets, _target(
            target_id=f"active_project:{active_project}", surface="", project_id=active_project,
            priority=1.0, reason="active_delivery_project", source="canonical_identity",
            canonical_surface=active_surface, canonical_scope=active_scope,
        ))
        # Current Project Workspace ingestion stores project:<id> under surface=project.
        _append_unique(targets, _target(
            target_id=f"active_project_compat:project:{active_project}", surface="project", project_id=f"project:{active_project}",
            priority=0.97, reason="active_delivery_project_compat", source="phase1_compatibility",
            canonical_surface=active_surface, canonical_scope=active_scope,
        ))

    if active_surface == "roleplay":
        explicit_roleplay = _roleplay_scope_target(db_path, query)
        if explicit_roleplay:
            _append_unique(targets, explicit_roleplay)
        else:
            blocked.append({"target": "roleplay", "reason": "roleplay_requires_explicit_sandbox"})
    else:
        memory_surface = str(compat.get("memory_surface_id") or "")
        memory_project = str(compat.get("memory_project_id") or "")
        memory_scope = str(compat.get("memory_scope_id") or "")
        if memory_surface or memory_project or memory_scope:
            _append_unique(targets, _target(
                target_id="active_scope", surface=memory_surface, project_id=memory_project, scope_id=memory_scope,
                priority=0.96 if active_project else 1.0,
                reason="active_scope_priority", source="canonical_identity",
                canonical_surface=active_surface, canonical_scope=active_scope,
            ))

    # General durable memory is a background preference/context lane for every
    # non-roleplay Assistant scope. General itself already has this as primary.
    if active_surface != "roleplay":
        _append_unique(targets, _target(
            target_id="general_memory", surface="assistant", project_id="assistant:general",
            priority=0.82 if active_scope != "general" else 1.0,
            reason="general_durable_memory", source="scope_priority_policy",
            canonical_surface="global", canonical_scope="general",
        ))

    # Query-driven surface expansion. Roleplay is handled separately below.
    for surface, score in sorted(scores.items(), key=lambda row: row[1], reverse=True):
        if surface == "roleplay":
            continue
        storage = _SURFACE_STORAGE.get(surface)
        if not storage:
            continue
        storage_surface, storage_project, storage_scope = storage
        _append_unique(targets, _target(
            target_id=f"surface:{surface}", surface=storage_surface, project_id=storage_project, scope_id=storage_scope,
            priority=min(0.94, 0.80 + (score * 0.14)),
            reason="query_relevant_surface", source="query_surface_classifier",
            canonical_surface=surface,
            canonical_scope=builtin_scope_for_surface(surface, default=""),
        ))

    # General recall questions sometimes omit the originating surface (for example
    # "what model did I use last time?"). In that case perform a bounded discovery
    # across non-Roleplay creative surfaces instead of forcing the user to switch
    # Scope or guessing one surface. This runs only for explicit recall language.
    if active_scope == "general" and _needs_general_recall_discovery(query) and not any(surface in scores for surface in ("image", "prompt_captioning", "video", "voice")):
        for surface in ("image", "prompt_captioning", "video", "voice"):
            storage_surface, storage_project, storage_scope = _SURFACE_STORAGE[surface]
            _append_unique(targets, _target(
                target_id=f"recall_discovery:{surface}", surface=storage_surface, project_id=storage_project, scope_id=storage_scope,
                priority=0.76, reason="recall_discovery", source="general_recall_policy",
                canonical_surface=surface, canonical_scope=builtin_scope_for_surface(surface, default=""),
            ))

    # Roleplay cross-surface recall never searches all roleplay memory. A concrete
    # universe/world/scene/sandbox must match a registered roleplay scope.
    if "roleplay" in scores and active_surface != "roleplay":
        explicit_roleplay = _roleplay_scope_target(db_path, query)
        if explicit_roleplay:
            _append_unique(targets, explicit_roleplay)
        else:
            blocked.append({"target": "roleplay", "reason": "roleplay_requires_explicit_sandbox"})

    # Strongly mentioned delivery/custom memory projects may be searched even from
    # General or another scope. This is query-driven expansion, not global search.
    for project_target in _db_project_targets(db_path, query):
        _append_unique(targets, project_target)

    targets.sort(key=lambda item: float(item.get("priority") or 0), reverse=True)
    targets = targets[:8]
    expanded_surfaces = sorted({str(t.get("canonical_surface") or t.get("surface") or "") for t in targets if t.get("reason") in {"query_relevant_surface", "recall_discovery"} and str(t.get("canonical_surface") or "") not in {active_surface, "global", "assistant"}})
    project_expansion = any(t.get("reason") == "query_mentions_project" for t in targets)
    roleplay_expansion = any(t.get("reason") == "explicit_roleplay_sandbox" for t in targets)
    general_memory_included = any(t.get("reason") == "general_durable_memory" for t in targets)
    return {
        "schema_id": SCOPE_PRIORITY_SCHEMA_ID,
        "phase": SCOPE_PRIORITY_PHASE,
        "policy": "Scope sets retrieval priority, not a hard prison. Expansion is bounded, query-driven, traceable, and Roleplay requires an explicit sandbox.",
        "active_identity": identity.as_dict(),
        "surface_scores": scores,
        "targets": targets,
        "blocked_expansions": blocked,
        "allow_cross_surface": bool(expanded_surfaces or roleplay_expansion or (general_memory_included and active_surface not in {"global", "assistant"})),
        # Current unified surface memories use compatibility pseudo-project IDs
        # (image, video, prompt_captioning, ...). Approved cross-surface expansion
        # therefore also needs M12 cross-project permission even though this is not
        # a canonical delivery-project boundary crossing.
        "allow_cross_project": bool(active_project or project_expansion or expanded_surfaces or roleplay_expansion or general_memory_included),
        "allow_scope_expansion": bool(roleplay_expansion),
        "expanded_surfaces": expanded_surfaces,
        "query_project_expansion": project_expansion,
        "recall_discovery": any(t.get("reason") == "recall_discovery" for t in targets),
        "general_memory_included": general_memory_included,
        "roleplay_sandbox_expansion": roleplay_expansion,
        "target_count": len(targets),
    }
