from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from neo_app.assistant.contracts import normalize_surface_id, trim_text

ROOT_DIR = Path(__file__).resolve().parents[2]
GUIDES_DIR = ROOT_DIR / "guides"
GUIDE_SCHEMA_ID = "neo.assistant.guides.v1"

SURFACE_PROJECT_MAP = {
    "image_workspace": "image",
    "video_workspace": "video",
    "voice_workspace": "voice",
    "prompt_captioning_workspace": "prompt_captioning",
    "roleplay_workspace": "roleplay",
    "neo_development_workspace": "admin",
    "general": "global",
}

_LIST_FIELD_NAMES = {"applies_to", "tags"}


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def _parse_scalar(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            return text
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse the tiny YAML subset used by Neo guides.

    Keeps runtime dependency-free: supports `key: value` and simple list blocks:

        tags:
          - image
          - qwen
    """

    text = str(raw or "")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        return {}, text
    meta_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1 :]).strip()
    meta: dict[str, Any] = {}
    current_key = ""
    for line in meta_lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  -") and current_key:
            meta.setdefault(current_key, [])
            if isinstance(meta[current_key], list):
                meta[current_key].append(str(line.split("-", 1)[1]).strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        raw_value = value.strip()
        if current_key in _LIST_FIELD_NAMES and not raw_value:
            meta[current_key] = []
        elif current_key in _LIST_FIELD_NAMES:
            meta[current_key] = _coerce_list(raw_value)
        else:
            meta[current_key] = _parse_scalar(raw_value)
    return meta, body


def _guide_path_id(path: Path) -> str:
    try:
        rel = path.relative_to(GUIDES_DIR)
    except Exception:
        rel = path.name
    return re.sub(r"[^a-z0-9._-]+", "_", str(rel).lower().replace("\\", "/"))


def _read_guide(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = parse_frontmatter(raw)
    guide_id = str(meta.get("guide_id") or _guide_path_id(path)).strip()
    title = str(meta.get("title") or path.stem.replace("_", " ").title()).strip()
    surface = normalize_surface_id(meta.get("surface") or "global", default="global")
    tags = _coerce_list(meta.get("tags") or [])
    applies_to = _coerce_list(meta.get("applies_to") or [])
    return {
        "schema_id": GUIDE_SCHEMA_ID,
        "guide_id": guide_id,
        "title": title,
        "surface": surface,
        "scope": str(meta.get("scope") or "built_in"),
        "applies_to": applies_to,
        "tags": tags,
        "priority": int(meta.get("priority") or 50),
        "version": meta.get("version") or 1,
        "updated": str(meta.get("updated") or ""),
        "path": str(path.relative_to(ROOT_DIR)) if path.is_relative_to(ROOT_DIR) else str(path),
        "content": body,
        "frontmatter": meta,
    }


def load_guides() -> list[dict[str, Any]]:
    if not GUIDES_DIR.exists():
        return []
    guides: list[dict[str, Any]] = []
    for path in sorted(GUIDES_DIR.rglob("*.md")):
        guide = _read_guide(path)
        if guide:
            guides.append(guide)
    return guides


def project_surface(project_id: str = "", explicit_surface: str = "") -> str:
    explicit = normalize_surface_id(explicit_surface, default="") if explicit_surface else ""
    if explicit and explicit not in {"assistant", "general"}:
        return explicit
    return SURFACE_PROJECT_MAP.get(str(project_id or "general"), explicit or "global")


def _guide_matches_scope(guide: dict[str, Any], *, surface: str, project_id: str) -> bool:
    surface = normalize_surface_id(surface or "global", default="global")
    guide_surface = normalize_surface_id(guide.get("surface") or "global", default="global")
    applies = {normalize_surface_id(item, default="") for item in _coerce_list(guide.get("applies_to") or [])}
    project = normalize_surface_id(project_id or "general", default="general")
    if project in {"general", "assistant_workspace_general"} or surface in {"global", "all"}:
        return True
    allowed = {"global", "assistant", surface, project}
    return guide_surface in allowed or bool(applies & allowed)


def _term_set(value: str) -> set[str]:
    return {term for term in re.split(r"[^a-z0-9_+-]+", str(value or "").lower()) if len(term) >= 2}


def _score_guide(guide: dict[str, Any], query: str, *, surface: str, project_id: str) -> int:
    score = int(guide.get("priority") or 50)
    guide_surface = normalize_surface_id(guide.get("surface") or "global", default="global")
    if guide_surface == surface:
        score += 40
    if guide_surface == "global":
        score += 18
    if project_id and project_id in _coerce_list(guide.get("applies_to") or []):
        score += 25
    terms = _term_set(query)
    if terms:
        title_terms = _term_set(str(guide.get("title") or ""))
        tag_terms = _term_set(" ".join(_coerce_list(guide.get("tags") or [])))
        apply_terms = _term_set(" ".join(_coerce_list(guide.get("applies_to") or [])))
        content_terms = _term_set(trim_text(guide.get("content") or "", 6000))
        score += 18 * len(terms & title_terms)
        score += 14 * len(terms & tag_terms)
        score += 10 * len(terms & apply_terms)
        score += 4 * len(terms & content_terms)
    return score


def search_guides(query: str = "", *, surface: str = "", project_id: str = "general", limit: int = 8) -> dict[str, Any]:
    resolved_surface = project_surface(project_id, surface)
    guides = [g for g in load_guides() if _guide_matches_scope(g, surface=resolved_surface, project_id=project_id)]
    ranked = sorted(guides, key=lambda g: (_score_guide(g, query, surface=resolved_surface, project_id=project_id), g.get("title") or ""), reverse=True)
    results = []
    for guide in ranked[: max(1, int(limit or 8))]:
        excerpt = trim_text(guide.get("content") or "", 1400)
        results.append({k: guide.get(k) for k in ("guide_id", "title", "surface", "scope", "applies_to", "tags", "priority", "version", "updated", "path")} | {"excerpt": excerpt})
    return {
        "ok": True,
        "schema_id": GUIDE_SCHEMA_ID,
        "query": query,
        "project_id": project_id or "general",
        "surface": resolved_surface,
        "count": len(results),
        "total_available": len(guides),
        "guides": results,
    }


def guides_context_text(payload: dict[str, Any]) -> str:
    guides = payload.get("guides") if isinstance(payload, dict) else []
    if not isinstance(guides, list) or not guides:
        return "No built-in Neo guides matched this scope/message."
    rows = []
    for idx, guide in enumerate(guides, 1):
        tags = ", ".join(_coerce_list(guide.get("tags") or [])[:8])
        applies = ", ".join(_coerce_list(guide.get("applies_to") or [])[:8])
        rows.append("\n".join([
            f"[{idx}] {guide.get('title') or guide.get('guide_id')} ({guide.get('surface') or 'global'} · {guide.get('path') or ''})",
            f"Guide ID: {guide.get('guide_id') or ''}",
            f"Applies to: {applies or 'global'}",
            f"Tags: {tags or 'none'}",
            trim_text(guide.get("excerpt") or "", 1400),
        ]))
    return "\n\n".join(rows).strip()
