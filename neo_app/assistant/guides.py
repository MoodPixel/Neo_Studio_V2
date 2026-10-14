from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from neo_app.assistant.contracts import normalize_surface_id, trim_text
from neo_app.context_identity import SCOPE_SURFACE_ALIASES

ROOT_DIR = Path(__file__).resolve().parents[2]
GUIDES_DIR = ROOT_DIR / "guides"
GUIDE_SCHEMA_ID = "neo.assistant.guides.v1"
IMAGE_GUIDE_REGISTRY_PATH = GUIDES_DIR / "01_IMAGE" / "_guide_registry.json"

SURFACE_PROJECT_MAP = dict(SCOPE_SURFACE_ALIASES)


_LIST_FIELD_NAMES = {
    "applies_to",
    "tags",
    "tasks",
    "ui_path",
    "supersedes",
    "prerequisites",
    "warnings",
}
_CURRENT_LIFECYCLES = {"current", "active", "canonical"}
_HISTORICAL_LIFECYCLES = {"historical", "superseded", "deprecated", "archived", "legacy"}


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

    Keeps runtime dependency-free: supports ``key: value`` and simple list
    blocks. R8 canonical-guide metadata intentionally stays inside this subset.
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



def _load_image_guide_registry() -> dict[str, dict[str, Any]]:
    """Load the Image guide-role registry used to normalize the mixed guide corpus.

    The registry is an overlay, not a second documentation store: Markdown remains
    the authored source.  It only supplies lifecycle/role/surface metadata for files
    that predate Neo guide frontmatter or whose historical phase role needs to be
    machine-readable.
    """
    try:
        payload = json.loads(IMAGE_GUIDE_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        return {}
    return {str(name): dict(value) for name, value in entries.items() if isinstance(value, dict)}


def _guide_registry_overlay(path: Path) -> dict[str, Any]:
    try:
        rel = path.relative_to(GUIDES_DIR).as_posix()
    except Exception:
        return {}
    if not rel.startswith("01_IMAGE/"):
        return {}
    return _load_image_guide_registry().get(path.name, {})


def _merge_guide_metadata(meta: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    if not overlay:
        return dict(meta)
    merged = dict(meta)
    # Registry authority is intentionally narrow: corpus role/lifecycle/surface and
    # ranking may be normalized centrally without rewriting 100+ authored files.
    for key in ("surface", "scope", "guide_kind", "lifecycle_state", "priority", "canonical"):
        if key in overlay:
            merged[key] = overlay[key]
    # Preserve authored stable IDs and task contracts when present; use registry
    # values only to fill older documents that have no frontmatter.
    for key in ("guide_id", "tasks", "ui_path", "applies_to", "tags", "supersedes"):
        if not merged.get(key) and overlay.get(key):
            merged[key] = overlay[key]
    return merged

def _guide_path_id(path: Path) -> str:
    try:
        rel = path.relative_to(GUIDES_DIR)
    except Exception:
        rel = path.name
    return re.sub(r"[^a-z0-9._-]+", "_", str(rel).lower().replace("\\", "/"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def markdown_sections(content: str) -> list[dict[str, Any]]:
    """Split a Markdown document into addressable heading sections.

    Each section includes the heading and all body text until the next heading at
    the same or a higher level. This is intentionally deterministic and does not
    summarize or rewrite authored Guide text.
    """

    lines = str(content or "").splitlines()
    headings: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((idx, len(match.group(1)), match.group(2).strip()))
    sections: list[dict[str, Any]] = []
    for pos, (line_index, level, title) in enumerate(headings):
        end = len(lines)
        for next_line, next_level, _ in headings[pos + 1 :]:
            if next_level <= level:
                end = next_line
                break
        body_lines = lines[line_index + 1 : end]
        text = "\n".join([lines[line_index], *body_lines]).strip()
        sections.append(
            {
                "title": title,
                "slug": _slug(title),
                "level": level,
                "start_line": line_index + 1,
                "end_line": end,
                "body": "\n".join(body_lines).strip(),
                "text": text,
            }
        )
    return sections


def _guide_lifecycle(meta: dict[str, Any], path: Path) -> str:
    explicit = str(meta.get("lifecycle_state") or meta.get("status") or "").strip().lower()
    if explicit:
        return explicit
    stem = path.stem.lower()
    if any(marker in stem for marker in ("historical", "legacy", "hotfix", "phase_", "branch_restore", "regression")):
        return "historical"
    return "current"


def _read_guide(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = parse_frontmatter(raw)
    meta = _merge_guide_metadata(meta, _guide_registry_overlay(path))
    guide_id = str(meta.get("guide_id") or _guide_path_id(path)).strip()
    title = str(meta.get("title") or path.stem.replace("_", " ").title()).strip()
    surface = normalize_surface_id(meta.get("surface") or "global", default="global")
    tags = _coerce_list(meta.get("tags") or [])
    applies_to = _coerce_list(meta.get("applies_to") or [])
    lifecycle_state = _guide_lifecycle(meta, path)
    return {
        "schema_id": GUIDE_SCHEMA_ID,
        "guide_id": guide_id,
        "title": title,
        "surface": surface,
        "scope": str(meta.get("scope") or "built_in"),
        "applies_to": applies_to,
        "tags": tags,
        "tasks": _coerce_list(meta.get("tasks") or []),
        "ui_path": _coerce_list(meta.get("ui_path") or []),
        "guide_kind": str(meta.get("guide_kind") or "reference").strip() or "reference",
        "lifecycle_state": lifecycle_state,
        "procedure_section": str(meta.get("procedure_section") or "").strip(),
        "prerequisites_section": str(meta.get("prerequisites_section") or "").strip(),
        "warnings_section": str(meta.get("warnings_section") or "").strip(),
        "troubleshooting_section": str(meta.get("troubleshooting_section") or "").strip(),
        "supersedes": _coerce_list(meta.get("supersedes") or []),
        "priority": int(meta.get("priority") or 50),
        "canonical": bool(meta.get("canonical") or False),
        "version": meta.get("version") or 1,
        "updated": str(meta.get("updated") or ""),
        "path": str(path.relative_to(ROOT_DIR)) if path.is_relative_to(ROOT_DIR) else str(path),
        "content": body,
        "sections": markdown_sections(body),
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


def get_guide(guide_id: str) -> dict[str, Any] | None:
    needle = str(guide_id or "").strip().lower()
    if not needle:
        return None
    for guide in load_guides():
        if str(guide.get("guide_id") or "").strip().lower() == needle:
            return guide
    return None


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


def _score_guide(guide: dict[str, Any], query: str, *, surface: str, project_id: str, task_id: str = "") -> int:
    score = int(guide.get("priority") or 50)
    guide_surface = normalize_surface_id(guide.get("surface") or "global", default="global")
    lifecycle = str(guide.get("lifecycle_state") or "current").lower()
    if lifecycle in _CURRENT_LIFECYCLES:
        score += 70
    elif lifecycle in _HISTORICAL_LIFECYCLES:
        score -= 80
    if guide.get("canonical"):
        score += 35
    if task_id and str(guide.get("guide_kind") or "") == "user_procedure":
        score += 35
    if guide_surface == surface:
        score += 40
    if guide_surface == "global":
        score += 18
    if project_id and project_id in _coerce_list(guide.get("applies_to") or []):
        score += 25
    if task_id:
        tasks = {str(item).lower() for item in _coerce_list(guide.get("tasks") or [])}
        if task_id.lower() in tasks:
            score += 180
    terms = _term_set(query)
    if terms:
        title_terms = _term_set(str(guide.get("title") or ""))
        tag_terms = _term_set(" ".join(_coerce_list(guide.get("tags") or [])))
        apply_terms = _term_set(" ".join(_coerce_list(guide.get("applies_to") or [])))
        task_terms = _term_set(" ".join(_coerce_list(guide.get("tasks") or [])))
        path_terms = _term_set(" ".join(_coerce_list(guide.get("ui_path") or [])))
        content_terms = _term_set(trim_text(guide.get("content") or "", 9000))
        score += 18 * len(terms & title_terms)
        score += 14 * len(terms & tag_terms)
        score += 10 * len(terms & apply_terms)
        score += 18 * len(terms & task_terms)
        score += 12 * len(terms & path_terms)
        score += 4 * len(terms & content_terms)
    return score


def resolve_canonical_guide(
    query: str = "",
    *,
    surface: str = "",
    project_id: str = "general",
    task_id: str = "",
    guide_id: str = "",
    include_historical: bool = False,
) -> dict[str, Any]:
    """Resolve one authoritative current Guide without excerpt reconstruction."""

    resolved_surface = project_surface(project_id, surface)
    guides = [g for g in load_guides() if _guide_matches_scope(g, surface=resolved_surface, project_id=project_id)]
    if guide_id:
        exact = [g for g in guides if str(g.get("guide_id") or "").lower() == str(guide_id).lower()]
        if exact:
            guides = exact
    if not include_historical:
        current = [g for g in guides if str(g.get("lifecycle_state") or "current").lower() not in _HISTORICAL_LIFECYCLES]
        if current:
            guides = current
    ranked = sorted(
        guides,
        key=lambda g: (
            _score_guide(g, query, surface=resolved_surface, project_id=project_id, task_id=task_id),
            str(g.get("updated") or ""),
            str(g.get("title") or ""),
        ),
        reverse=True,
    )
    if not ranked:
        return {
            "ok": False,
            "status": "not_found",
            "schema_id": GUIDE_SCHEMA_ID,
            "query": query,
            "task_id": task_id,
            "guide_id": guide_id,
            "surface": resolved_surface,
        }
    selected = ranked[0]
    return {
        "ok": True,
        "status": "resolved",
        "schema_id": GUIDE_SCHEMA_ID,
        "query": query,
        "task_id": task_id,
        "surface": resolved_surface,
        "guide": selected,
        "candidates": [
            {
                "guide_id": g.get("guide_id"),
                "title": g.get("title"),
                "lifecycle_state": g.get("lifecycle_state"),
                "guide_kind": g.get("guide_kind"),
                "path": g.get("path"),
            }
            for g in ranked[:5]
        ],
    }


def resolve_guide_section(guide: dict[str, Any], section_query: str = "") -> dict[str, Any] | None:
    sections = guide.get("sections") if isinstance(guide.get("sections"), list) else markdown_sections(str(guide.get("content") or ""))
    if not sections:
        return None
    needle = _slug(section_query)
    if not needle:
        return sections[0]
    exact = [s for s in sections if str(s.get("slug") or "") == needle]
    if exact:
        return exact[0]
    terms = _term_set(section_query)
    ranked = sorted(
        sections,
        key=lambda s: (
            30 * len(terms & _term_set(str(s.get("title") or "")))
            + 5 * len(terms & _term_set(trim_text(s.get("body") or "", 6000))),
            -int(s.get("level") or 6),
        ),
        reverse=True,
    )
    best = ranked[0]
    score = 30 * len(terms & _term_set(str(best.get("title") or ""))) + 5 * len(terms & _term_set(trim_text(best.get("body") or "", 6000)))
    return best if score > 0 else None


def search_guides(query: str = "", *, surface: str = "", project_id: str = "general", limit: int = 8) -> dict[str, Any]:
    resolved_surface = project_surface(project_id, surface)
    guides = [g for g in load_guides() if _guide_matches_scope(g, surface=resolved_surface, project_id=project_id)]
    ranked = sorted(guides, key=lambda g: (_score_guide(g, query, surface=resolved_surface, project_id=project_id), g.get("title") or ""), reverse=True)
    results = []
    for guide in ranked[: max(1, int(limit or 8))]:
        excerpt = trim_text(guide.get("content") or "", 3200)
        results.append(
            {k: guide.get(k) for k in (
                "guide_id", "title", "surface", "scope", "applies_to", "tags", "tasks", "ui_path",
                "guide_kind", "lifecycle_state", "canonical", "procedure_section", "prerequisites_section", "warnings_section",
                "troubleshooting_section", "supersedes", "priority", "version", "updated", "path"
            )}
            | {"excerpt": excerpt}
        )
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
            f"Lifecycle: {guide.get('lifecycle_state') or 'current'} · Kind: {guide.get('guide_kind') or 'reference'}",
            f"Applies to: {applies or 'global'}",
            f"Tags: {tags or 'none'}",
            trim_text(guide.get("excerpt") or "", 3200),
        ]))
    return "\n\n".join(rows).strip()
