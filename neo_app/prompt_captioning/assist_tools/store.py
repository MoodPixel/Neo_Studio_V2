from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "neo_data" / "prompt_captioning"
ASSIST_DIR = DATA_DIR / "assist_tools"
TAG_ASSIST_DIR = DATA_DIR / "tag_assist"
CHARACTER_DIR = DATA_DIR / "characters"
KEYWORD_DIR = DATA_DIR / "keywords"
KEYWORD_LIBRARY_DIR = KEYWORD_DIR / "libraries"
PACKAGE_KEYWORD_LIBRARY_DIR = Path(__file__).resolve().parent / "keyword_libraries"
CAPTION_BROWSER_DIR = DATA_DIR / "caption_browser"

TAG_GROUPS_PATH = TAG_ASSIST_DIR / "tag_groups.json"
CHARACTERS_PATH = CHARACTER_DIR / "characters.json"
KEYWORDS_PATH = KEYWORD_DIR / "keywords.json"
CAPTION_BROWSER_PATH = CAPTION_BROWSER_DIR / "caption_items.json"
CAPTION_RECORDS_PATH = DATA_DIR / "saved_captions.json"
CAPTION_HISTORY_PATH = DATA_DIR / "caption_history.json"
CAPTION_BATCH_RESULTS_PATH = DATA_DIR / "caption_batch_results.json"

MAX_TEXT = 12000
MAX_PROMPT_TEXT = 4000

DEFAULT_KEYWORDS: list[dict[str, Any]] = [
    {"keyword_id": "kw_camera_portrait_lens", "label": "85mm portrait lens", "category": "camera", "favorite": False, "builtin": True},
    {"keyword_id": "kw_camera_shallow_dof", "label": "shallow depth of field", "category": "camera", "favorite": False, "builtin": True},
    {"keyword_id": "kw_lighting_soft_studio", "label": "soft studio lighting", "category": "lighting", "favorite": False, "builtin": True},
    {"keyword_id": "kw_lighting_rim", "label": "subtle rim light", "category": "lighting", "favorite": False, "builtin": True},
    {"keyword_id": "kw_composition_clean", "label": "clean composition", "category": "composition", "favorite": False, "builtin": True},
    {"keyword_id": "kw_fashion_editorial", "label": "fashion editorial", "category": "fashion", "favorite": False, "builtin": True},
    {"keyword_id": "kw_pose_confident", "label": "natural confident pose", "category": "pose", "favorite": False, "builtin": True},
    {"keyword_id": "kw_quality_high_detail", "label": "high detail", "category": "quality", "favorite": False, "builtin": True},
    {"keyword_id": "kw_negative_extra_fingers", "label": "extra fingers", "category": "negative", "favorite": False, "builtin": True},
    {"keyword_id": "kw_negative_blurry", "label": "blurry", "category": "negative", "favorite": False, "builtin": True},
]

TAG_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "of", "on", "or", "the", "to", "with", "wearing",
    "image", "photo", "photograph", "prompt", "create", "generate", "style",
}

CATEGORY_HINTS: dict[str, list[str]] = {
    "editorial": ["fashion editorial", "professional editorial photography", "clean composition", "soft studio lighting", "tasteful styling"],
    "portrait": ["portrait photography", "85mm portrait lens", "shallow depth of field", "sharp focus", "natural expression"],
    "cinematic": ["cinematic lighting", "dramatic composition", "film still", "high contrast", "color graded"],
    "product": ["commercial product photography", "clean background", "sharp product detail", "studio lighting", "advertising layout"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_text(value: Any, limit: int = MAX_TEXT) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _slug(value: Any, fallback: str = "general") -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return text or fallback


def _norm_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _keyword_id(category: str, subcategory: str, canonical: str) -> str:
    return f"kw::{category or 'misc'}::{subcategory or 'general'}::{_norm_token(canonical)}"


def _parse_keyword_lib_filename(filename: str) -> tuple[str, str] | None:
    """Parse V1 Neo Library keyword markdown filenames.

    Supported V1 patterns:
    - <category>__keywords__<subcategory>.md
    - <category>__keywords.md
    - <category>__<subcategory>.md
    - <category>__<section>__<subcategory>__<rating>.md

    V2 stores these in neo_data/prompt_captioning/keywords/libraries/*.md.
    """
    stem = Path(filename).stem
    parts = [part.strip() for part in stem.split("__") if part.strip()]
    if len(parts) >= 3 and parts[1].casefold() == "keywords":
        return parts[0], "__".join(parts[2:]).strip() or "general"
    if len(parts) >= 2 and parts[-1].casefold() == "keywords":
        cat = "__".join(parts[:-1]).strip()
        return (cat, "general") if cat else None
    if len(parts) >= 2:
        if parts[1].casefold() in {"packs", "bases"}:
            return None
        return parts[0], "__".join(parts[1:]).strip() or "general"
    if len(parts) == 1:
        # Real user libraries are not always named with the V1 double-underscore
        # convention. Treat a single Markdown filename as a category with a
        # general subcategory so files like camera.md, poses.md, lighting.md,
        # or user-supplied theme packs still load instead of silently vanishing.
        return parts[0], "general"
    return None


def _parse_keyword_line(line: str) -> dict[str, Any] | None:
    raw = str(line or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("//") or raw.startswith("```") or raw == "---":
        return None
    # Accept normal Markdown list lines, not only raw pipe-delimited rows.
    # V1 libraries often came from editable .md files where users typed
    # "- keyword | desc: ..." or "1. keyword - description".
    raw = re.sub(r"^[-*+]\s+", "", raw).strip()
    raw = re.sub(r"^\d+[.)]\s+", "", raw).strip()
    if not raw:
        return None
    if "|" not in raw:
        for sep in (" — ", " – ", " - "):
            if sep in raw:
                name, desc = raw.split(sep, 1)
                raw = f"{name.strip()} | desc: {desc.strip()}"
                break
    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if not parts:
        return None
    meta: dict[str, Any] = {"aliases": [], "desc": "", "enabled": True}
    for segment in parts[1:]:
        if ":" not in segment:
            continue
        key, value = segment.split(":", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key in {"alias", "aliases"}:
            meta["aliases"] = [item.strip() for item in value.split(",") if item.strip()]
        elif key in {"desc", "description"}:
            meta["desc"] = value
        elif key in {"enabled", "enable"}:
            meta["enabled"] = value.casefold() not in {"0", "false", "no", "off"}
    return {"name": parts[0], **meta}



def _normalize_keyword_manual_path(raw: str = "") -> str:
    """Normalize user-pasted Keyword Browser paths across Windows/portable launches.

    Users commonly paste paths like ``Neo_Studio_V2/neo_data/...`` or
    ``F:/LLM/Neo_Studio_V2/neo_data/...``.  On POSIX test runners a
    backslash is not a separator, and on Windows a relative project-folder
    prefix may not match the actual extracted folder name.  Normalize
    separators before building candidate roots, then add suffix-based
    candidates below.
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return text.replace("\\", "/")


def _keyword_manual_root_candidates(manual: str = "") -> list[Path]:
    """Return robust root candidates for a user-supplied keyword library path."""
    normalized = _normalize_keyword_manual_path(manual)
    if not normalized:
        return []
    manual_path = Path(normalized).expanduser()
    candidates: list[Path] = [
        manual_path,
        manual_path / "libraries",
        manual_path / "Libraries",
        manual_path / "keywords" / "libraries",
        manual_path / "keywords" / "Libraries",
        manual_path / "prompt_captioning" / "keywords" / "libraries",
        manual_path / "prompt_captioning" / "keywords" / "Libraries",
        manual_path / "neo_data" / "prompt_captioning" / "keywords" / "libraries",
        manual_path / "neo_data" / "prompt_captioning" / "keywords" / "Libraries",
    ]

    # If the pasted path contains a project-folder prefix that does not match
    # the current extracted repo name, recover by remapping the known Neo_Data
    # suffix onto every detected project root. Example:
    #   Neo_Studio_V2/neo_data/prompt_captioning/keywords/Libraries
    # while the actual running folder is
    #   Neo_Studio_V2_Prompt_Captioning_.../neo_data/...
    parts = [part for part in manual_path.parts if part not in {"", "."}]
    lowered = [part.casefold() for part in parts]
    suffixes: list[Path] = []
    for marker in ("neo_data", "prompt_captioning", "keywords"):
        if marker in lowered:
            idx = lowered.index(marker)
            suffix = Path(*parts[idx:]) if parts[idx:] else Path()
            if str(suffix) not in {"", "."}:
                suffixes.append(suffix)
    for project_root in _project_root_candidates():
        for suffix in suffixes:
            if suffix.parts and suffix.parts[0].casefold() == "neo_data":
                candidates.append(project_root / suffix)
            elif suffix.parts and suffix.parts[0].casefold() == "prompt_captioning":
                candidates.append(project_root / "neo_data" / suffix)
            elif suffix.parts and suffix.parts[0].casefold() == "keywords":
                candidates.append(project_root / "neo_data" / "prompt_captioning" / suffix)
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except Exception:
            key = str(candidate).casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(candidate)
    return out

def _project_root_candidates() -> list[Path]:
    """Return likely Neo Studio project roots for user data discovery.

    V1 found libraries from the running project/library root, not only from a
    module-relative path. In V2, users may run Neo from a launcher, from
    ``neo_app``, or from a copied zip folder. This scanner intentionally checks
    the module root, current working directory, argv root, and optional env roots
    so files dropped into ``<Neo_Studio_V2>/neo_data/...`` are found reliably.
    """
    candidates: list[Path] = [ROOT]
    for raw in (os.environ.get("NEO_STUDIO_ROOT"), os.environ.get("NEO_DATA_ROOT")):
        if raw:
            path = Path(raw).expanduser()
            candidates.append(path if path.name != "neo_data" else path.parent)
    try:
        candidates.append(Path.cwd())
        candidates.append(Path.cwd().parent)
    except Exception:
        pass
    try:
        argv_root = Path(sys.argv[0]).resolve().parent
        candidates.append(argv_root)
        candidates.append(argv_root.parent)
    except Exception:
        pass
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except Exception:
            key = str(candidate).casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def _keyword_library_roots(extra_path: str = "") -> list[Path]:
    """Return V2 keyword library roots, plus V1-compatible fallback roots.

    Accepted user drop locations include:
    - <root>/neo_data/prompt_captioning/keywords/libraries/*.md
    - <root>/neo_data/prompt_captioning/keywords/Libraries/*.md
    - <root>/neo_data/prompt_captioning/keywords/*.md
    - <root>/neo_library_v1/libraries/*.md
    - <root>/neo_library_data/libraries/*.md

    The canonical write target remains the lowercase V2 libraries folder.
    """
    roots: list[Path] = []
    manual = _normalize_keyword_manual_path(extra_path or os.environ.get("NEO_KEYWORD_LIBRARY_PATH") or "")
    if manual:
        # Accept exact paths, project roots, Windows-style backslash paths, and
        # relative ``Neo_Studio_V2/neo_data/...`` paths even when the actual
        # extracted folder has a different name.
        roots.extend(_keyword_manual_root_candidates(manual))
    for project_root in _project_root_candidates():
        neo_data = project_root / "neo_data"
        roots.extend([
            neo_data / "prompt_captioning" / "keywords" / "libraries",
            neo_data / "prompt_captioning" / "keywords" / "Libraries",
            neo_data / "prompt_captioning" / "keywords",
            project_root / "neo_library_v1" / "libraries",
            project_root / "neo_library_data" / "libraries",
            project_root / "libraries",
        ])
    # Prefer explicit/manual and project-root libraries first. The packaged
    # fallback is useful, but it must not hide user-supplied .md files or make
    # diagnostics look like only bundled libraries were loaded.
    roots = roots + [KEYWORD_LIBRARY_DIR, KEYWORD_DIR / "Libraries", KEYWORD_DIR, PACKAGE_KEYWORD_LIBRARY_DIR]
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve()).casefold()
        except Exception:
            key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _keyword_library_files(extra_path: str = "") -> list[Path]:
    files: dict[str, Path] = {}
    for root in _keyword_library_roots(extra_path):
        if not root.exists():
            continue
        root_name = root.name.casefold()
        candidates = root.rglob("*.md") if root == KEYWORD_DIR or root_name in {"keywords", "libraries", "library", "keyword_libraries"} else root.glob("*.md")
        for path in candidates:
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.name.casefold() in {"readme.md", "index.md"}:
                continue
            if not _parse_keyword_lib_filename(path.name):
                continue
            try:
                key = str(path.resolve()).casefold()
            except Exception:
                key = str(path).casefold()
            files[key] = path
    return list(files.values())


def _load_markdown_keywords(extra_path: str = "") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in _keyword_library_files(extra_path):
        info = _parse_keyword_lib_filename(path.name)
        if not info:
            continue
        category, subcategory = info
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line_number, line in enumerate(lines, start=1):
            parsed = _parse_keyword_line(line)
            if not parsed:
                continue
            keyword_id = _keyword_id(category, subcategory, parsed["name"])
            if keyword_id in seen:
                continue
            seen.add(keyword_id)
            aliases = parsed.get("aliases") or []
            desc = str(parsed.get("desc") or "")
            label = str(parsed.get("name") or "").strip()
            records.append({
                "keyword_id": keyword_id,
                "id": keyword_id,
                "label": label,
                "name": label,
                "category": category,
                "subcategory": subcategory,
                "aliases": aliases,
                "desc": desc,
                "description": desc,
                "enabled": bool(parsed.get("enabled", True)),
                "prompt_fragment": label,
                "source": "markdown_library",
                "builtin": True,
                "source_file": str(path),
                "source_name": path.name,
                "line_number": line_number,
            })
    return records


def _keyword_insert_text(record: dict[str, Any], include_desc: bool = True) -> str:
    label = str(record.get("prompt_fragment") or record.get("label") or record.get("name") or "").strip()
    desc = str(record.get("desc") or record.get("description") or "").strip()
    if include_desc and desc:
        return f"{label}, {desc}"
    return label


def _keyword_primary_file(category: str, subcategory: str = "general", preferred: str = "") -> Path:
    KEYWORD_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    category = str(category or "Custom").strip() or "Custom"
    subcategory = str(subcategory or "general").strip() or "general"
    if preferred:
        preferred_path = Path(preferred)
        info = _parse_keyword_lib_filename(preferred_path.name)
        if info and _norm_token(info[0]) == _norm_token(category) and _norm_token(info[1]) == _norm_token(subcategory):
            return preferred_path
    safe_cat = re.sub(r"[^A-Za-z0-9_-]+", "_", category).strip("_") or "Custom"
    safe_sub = re.sub(r"[^A-Za-z0-9_-]+", "_", subcategory).strip("_") or "general"
    return KEYWORD_LIBRARY_DIR / f"{safe_cat}__{safe_sub}.md"


def _write_markdown_keywords(category: str, subcategory: str, records: list[dict[str, Any]], preferred: str = "") -> Path:
    path = _keyword_primary_file(category, subcategory, preferred)
    lines: list[str] = []
    for item in sorted(records, key=lambda row: str(row.get("label") or row.get("name") or "").casefold()):
        label = str(item.get("label") or item.get("name") or "").strip()
        if not label:
            continue
        segments = [label]
        aliases = [str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()]
        if aliases:
            segments.append("alias:" + ",".join(aliases))
        desc = str(item.get("desc") or item.get("description") or "").strip()
        if desc:
            segments.append("desc:" + desc)
        if item.get("enabled") is False:
            segments.append("enabled:false")
        lines.append(" | ".join(segments))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        records = data.get("records") or data.get("items") or []
    else:
        records = data
    return records if isinstance(records, list) else []


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")


def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,\n;]+", str(value or ""))
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = re.sub(r"\s+", " ", str(item or "").strip())
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag[:120])
    return tags


def _filter_records(records: list[dict[str, Any]], query: str = "", category: str = "") -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    c = str(category or "").strip().lower()
    out: list[dict[str, Any]] = []
    for item in records:
        haystack = " ".join(str(item.get(key) or "") for key in (
            "name", "title", "label", "category", "summary", "prompt", "prompt_fragment", "negative_fragment", "caption", "notes", "source_text", "style"
        ))
        if item.get("tags"):
            haystack += " " + " ".join(str(tag) for tag in item.get("tags") or [])
        if q and q not in haystack.lower():
            continue
        if c and str(item.get("category") or "").lower() != c:
            continue
        out.append(item)
    return out


def _upsert(path: Path, id_key: str, record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = _read_records(path)
    now = _now()
    record["updated_at"] = now
    if not record.get("created_at"):
        record["created_at"] = now
    target_id = record.get(id_key)
    replaced = False
    for index, item in enumerate(records):
        if item.get(id_key) == target_id:
            record["created_at"] = item.get("created_at") or now
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.insert(0, record)
    _write_records(path, records)
    return record, records


def _source_keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or ""))
    tags: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.lower()
        if key in TAG_STOPWORDS or key in seen:
            continue
        seen.add(key)
        tags.append(word.lower())
    return tags[:12]


def assist_bootstrap_payload() -> dict[str, Any]:
    tag_count = len(_read_records(TAG_GROUPS_PATH))
    character_count = len(_read_records(CHARACTERS_PATH))
    custom_keyword_count = len(_read_records(KEYWORDS_PATH))
    markdown_keyword_count = len(_load_markdown_keywords())
    caption_count = len(caption_browser_list_payload().get("records") or [])
    return {
        "ok": True,
        "surface": "prompt_captioning",
        "wave": "prompt_assist_tools_wave2_backend_routes",
        "tools": [
            {"tool_id": "tag_assist", "label": "Tag Assist", "routes": ["generate", "save", "list"]},
            {"tool_id": "character_builder", "label": "Character Builder", "routes": ["save", "list", "build_prompt"]},
            {"tool_id": "keyword_browser", "label": "Keyword Browser", "routes": ["list", "save"]},
            {"tool_id": "caption_browser", "label": "Caption Browser", "routes": ["list", "save", "send_to_prompt"]},
        ],
        "counts": {
            "tag_groups": tag_count,
            "characters": character_count,
            "custom_keywords": custom_keyword_count,
            "markdown_keywords": markdown_keyword_count,
            "captions": caption_count,
        },
        "storage": {
            "root": str(DATA_DIR),
            "tag_assist": str(TAG_ASSIST_DIR),
            "characters": str(CHARACTER_DIR),
            "keywords": str(KEYWORD_DIR),
            "keyword_libraries": str(KEYWORD_LIBRARY_DIR),
            "keyword_libraries_bundled_fallback": str(PACKAGE_KEYWORD_LIBRARY_DIR),
            "caption_browser": str(CAPTION_BROWSER_DIR),
        },
        "insert_contract": {
            "default_mode": "append",
            "supported_modes": ["append", "prepend", "replace_selected", "replace_full", "copy_only"],
            "silent_overwrite_allowed": False,
        },
    }


def tag_assist_generate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = _clip_text(payload.get("source_text") or payload.get("idea") or payload.get("prompt") or "", 2000)
    style = _clip_text(payload.get("style") or payload.get("category") or "", 120).lower()
    requested = _split_tags(payload.get("tags") or payload.get("seed_tags") or "")
    tags = requested + _source_keywords(source)
    for hint_key, hint_tags in CATEGORY_HINTS.items():
        if hint_key in style or hint_key in source.lower():
            tags.extend(hint_tags)
    if not tags:
        tags.extend(["clean composition", "soft studio lighting", "high detail", "sharp focus"])
    final_tags = _split_tags(tags)
    prompt_fragment = ", ".join(final_tags)
    return {
        "ok": True,
        "record": {
            "name": _clip_text(payload.get("name") or "Generated tag group", 160),
            "category": _clip_text(payload.get("category") or "General", 80) or "General",
            "source_text": source,
            "tags": final_tags,
            "prompt_fragment": prompt_fragment,
            "insert_mode": payload.get("insert_mode") or "append",
            "created_at": _now(),
        },
        "tags": final_tags,
        "prompt_fragment": prompt_fragment,
    }


def tag_assist_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tags = _split_tags(payload.get("tags") or payload.get("prompt_fragment") or "")
    if not tags:
        return {"ok": False, "errors": ["Tag Assist requires at least one tag to save."]}
    group_id = str(payload.get("tag_group_id") or f"tag_group_{uuid4().hex[:12]}")
    name = _clip_text(payload.get("name") or "Untitled Tag Group", 160) or "Untitled Tag Group"
    record = {
        "tag_group_id": group_id,
        "name": name,
        "category": _clip_text(payload.get("category") or "General", 80) or "General",
        "tags": tags,
        "prompt_fragment": _clip_text(payload.get("prompt_fragment") or ", ".join(tags), MAX_PROMPT_TEXT),
        "negative_fragment": _clip_text(payload.get("negative_fragment") or "", MAX_PROMPT_TEXT),
        "source_text": _clip_text(payload.get("source_text") or "", 2000),
        "notes": _clip_text(payload.get("notes") or "", 2000),
        "favorite": bool(payload.get("favorite", False)),
    }
    saved, records = _upsert(TAG_GROUPS_PATH, "tag_group_id", record)
    return {"ok": True, "record": saved, "records": records, "count": len(records)}


def tag_assist_list_payload(query: str = "", category: str = "") -> dict[str, Any]:
    records = _filter_records(_read_records(TAG_GROUPS_PATH), query, category)
    return {"ok": True, "records": records, "count": len(records)}


def character_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    character_id = str(payload.get("character_id") or f"character_{uuid4().hex[:12]}")
    name = _clip_text(payload.get("name") or "Untitled Character", 160) or "Untitled Character"
    fields = {
        "presentation": _clip_text(payload.get("presentation") or payload.get("gender") or "", 400),
        "age_range": _clip_text(payload.get("age_range") or payload.get("age") or "", 120),
        "appearance": _clip_text(payload.get("appearance") or "", 1200),
        "hair": _clip_text(payload.get("hair") or "", 500),
        "face": _clip_text(payload.get("face") or "", 500),
        "body": _clip_text(payload.get("body") or "", 600),
        "outfit": _clip_text(payload.get("outfit") or "", 1000),
        "pose": _clip_text(payload.get("pose") or "", 800),
        "mood": _clip_text(payload.get("mood") or payload.get("expression") or "", 500),
        "style": _clip_text(payload.get("style") or "", 500),
        "scene_notes": _clip_text(payload.get("scene_notes") or payload.get("notes") or "", 1200),
    }
    prompt_fragment = _clip_text(payload.get("prompt_fragment") or payload.get("identity_fragments") or _build_character_fragment(name, fields), MAX_PROMPT_TEXT)
    negative_fragment = _clip_text(payload.get("negative_fragment") or payload.get("negative_safeguards") or "", MAX_PROMPT_TEXT)
    record = {
        "character_id": character_id,
        "name": name,
        "category": _clip_text(payload.get("category") or "Character", 80) or "Character",
        "tags": _split_tags(payload.get("tags") or ""),
        "fields": fields,
        "prompt_fragment": prompt_fragment,
        "negative_fragment": negative_fragment,
        "notes": _clip_text(payload.get("notes") or "", 2000),
        "favorite": bool(payload.get("favorite", False)),
    }
    saved, records = _upsert(CHARACTERS_PATH, "character_id", record)
    return {"ok": True, "record": saved, "records": records, "count": len(records)}


def character_list_payload(query: str = "", category: str = "") -> dict[str, Any]:
    records = _filter_records(_read_records(CHARACTERS_PATH), query, category)
    return {"ok": True, "records": records, "count": len(records)}


def _build_character_fragment(name: str, fields: dict[str, str]) -> str:
    parts = [name]
    for key in ("presentation", "age_range", "appearance", "hair", "face", "body", "outfit", "pose", "mood", "style", "scene_notes"):
        value = fields.get(key)
        if value:
            parts.append(value)
    return ", ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def build_character_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    character_id = str(payload.get("character_id") or "")
    record = None
    if character_id:
        record = next((item for item in _read_records(CHARACTERS_PATH) if item.get("character_id") == character_id), None)
    if not record:
        fields = {
            "presentation": _clip_text(payload.get("presentation") or payload.get("gender") or "", 400),
            "age_range": _clip_text(payload.get("age_range") or payload.get("age") or "", 120),
            "appearance": _clip_text(payload.get("appearance") or "", 1200),
            "hair": _clip_text(payload.get("hair") or "", 500),
            "face": _clip_text(payload.get("face") or "", 500),
            "body": _clip_text(payload.get("body") or "", 600),
            "outfit": _clip_text(payload.get("outfit") or "", 1000),
            "pose": _clip_text(payload.get("pose") or "", 800),
            "mood": _clip_text(payload.get("mood") or payload.get("expression") or "", 500),
            "style": _clip_text(payload.get("style") or "", 500),
            "scene_notes": _clip_text(payload.get("scene_notes") or payload.get("notes") or "", 1200),
        }
        name = _clip_text(payload.get("name") or "Character", 160)
        prompt_fragment = _build_character_fragment(name, fields)
        negative_fragment = _clip_text(payload.get("negative_fragment") or payload.get("negative_safeguards") or "", MAX_PROMPT_TEXT)
    else:
        prompt_fragment = str(record.get("prompt_fragment") or "")
        negative_fragment = str(record.get("negative_fragment") or "")
    mode = str(payload.get("mode") or "detailed").strip().lower()
    if mode == "short":
        prompt_fragment = ", ".join(_split_tags(prompt_fragment)[:8]) or prompt_fragment[:500]
    elif mode == "editorial" and "editorial" not in prompt_fragment.lower():
        prompt_fragment = f"{prompt_fragment}, fashion editorial, professional studio photography, clean composition"
    return {
        "ok": True,
        "prompt_fragment": _clip_text(prompt_fragment, MAX_PROMPT_TEXT),
        "negative_fragment": _clip_text(negative_fragment, MAX_PROMPT_TEXT),
        "insert_mode": payload.get("insert_mode") or "append",
        "record": record,
    }


def keyword_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    label = _clip_text(payload.get("label") or payload.get("keyword") or payload.get("name") or "", 160)
    if not label:
        return {"ok": False, "errors": ["Keyword label is required."]}
    category = _clip_text(payload.get("category") or "Custom", 120) or "Custom"
    subcategory = _clip_text(payload.get("subcategory") or "general", 160) or "general"
    aliases = _split_tags(payload.get("aliases") or "")
    desc = _clip_text(payload.get("desc") or payload.get("description") or payload.get("notes") or "", 1000)
    existing = [item for item in _load_markdown_keywords() if _norm_token(item.get("category")) == _norm_token(category) and _norm_token(item.get("subcategory")) == _norm_token(subcategory)]
    preferred = str((existing[0] or {}).get("source_file") or "") if existing else ""
    keyword_id = str(payload.get("keyword_id") or _keyword_id(category, subcategory, label))
    updated = False
    normalized_label = _norm_token(label)
    markdown_rows: list[dict[str, Any]] = []
    for item in existing:
        row = dict(item)
        if row.get("keyword_id") == keyword_id or _norm_token(row.get("label")) == normalized_label:
            row.update({"keyword_id": keyword_id, "id": keyword_id, "label": label, "name": label, "aliases": aliases, "desc": desc, "description": desc, "enabled": bool(payload.get("enabled", True)), "prompt_fragment": label})
            updated = True
        markdown_rows.append(row)
    if not updated:
        markdown_rows.append({"keyword_id": keyword_id, "id": keyword_id, "label": label, "name": label, "aliases": aliases, "desc": desc, "description": desc, "enabled": bool(payload.get("enabled", True)), "prompt_fragment": label, "category": category, "subcategory": subcategory})
    path = _write_markdown_keywords(category, subcategory, markdown_rows, preferred=preferred)
    record = {
        "keyword_id": keyword_id,
        "id": keyword_id,
        "label": label,
        "name": label,
        "category": category,
        "subcategory": subcategory,
        "aliases": aliases,
        "desc": desc,
        "description": desc,
        "prompt_fragment": label,
        "negative": bool(payload.get("negative", False)),
        "favorite": bool(payload.get("favorite", False)),
        "notes": _clip_text(payload.get("notes") or "", 1000),
        "builtin": False,
        "source": "markdown_library",
        "source_file": str(path),
        "source_name": path.name,
    }
    # Keep the JSON sidecar as a fast recent-custom index, while Markdown remains source-of-truth.
    _upsert(KEYWORDS_PATH, "keyword_id", record)
    listed = keyword_list_payload(category=category, subcategory=subcategory, include_builtin=True)
    return {"ok": True, "record": record, "records": listed.get("records") or [], "count": listed.get("count") or 0, "categories": listed.get("categories") or [], "subcategories": listed.get("subcategories") or [], "library_path": str(path)}



def keyword_library_diagnostics(library_path: str = "", *, filtered_count: int = 0, total_count: int = 0) -> dict[str, Any]:
    """Return UI-facing diagnostics for Keyword Browser library discovery.

    This is intentionally read-only: it explains what roots/files Neo scanned
    without changing keyword loading behavior.
    """
    manual = _normalize_keyword_manual_path(library_path or "")
    roots = _keyword_library_roots(manual)
    existing_roots = [root for root in roots if root.exists()]
    files = _keyword_library_files(manual)
    active_root = ""
    if files:
        first_file = files[0]
        for root in existing_roots:
            try:
                first_file.resolve().relative_to(root.resolve())
                active_root = str(root)
                break
            except Exception:
                continue
        if not active_root:
            active_root = str(first_file.parent)
    manual_path_status = "not_set"
    manual_candidates: list[str] = []
    if manual:
        manual_roots = _keyword_manual_root_candidates(manual)
        manual_candidates = [str(root) for root in manual_roots[:10]]
        manual_existing = [root for root in manual_roots if root.exists()]
        manual_files: list[Path] = []
        for root in manual_existing:
            root_name = root.name.casefold()
            candidates = root.rglob("*.md") if root_name in {"keywords", "libraries", "library", "keyword_libraries"} else root.glob("*.md")
            for path in candidates:
                if path.is_file() and not path.name.startswith(".") and path.suffix.casefold() == ".md" and path.name.casefold() not in {"readme.md", "index.md"}:
                    manual_files.append(path)
        if manual_files:
            manual_path_status = "applied"
        elif manual_existing:
            manual_path_status = "found_no_markdown_keywords"
        else:
            manual_path_status = "not_found"
    empty_reason = ""
    if total_count <= 0:
        empty_reason = "no_markdown_keywords"
    elif filtered_count <= 0:
        empty_reason = "filters_or_search"
    return {
        "schema": "neo.prompt_captioning.keyword_browser_diagnostics.v1",
        "loaded_file_count": len(files),
        "loaded_keyword_count": int(total_count or 0),
        "visible_keyword_count": int(filtered_count or 0),
        "active_root": active_root,
        "existing_root_count": len(existing_roots),
        "scanned_root_count": len(roots),
        "library_roots": [str(root) for root in roots],
        "existing_roots": [str(root) for root in existing_roots],
        "sample_files": [str(path) for path in files[:24]],
        "manual_library_path": manual,
        "manual_path_status": manual_path_status,
        "manual_path_candidates": manual_candidates,
        "empty_reason": empty_reason,
        "empty_reason_label": {
            "no_markdown_keywords": "No Markdown keyword libraries were found in the scanned roots.",
            "filters_or_search": "Keyword libraries are loaded, but the current search/category filters hide every match.",
            "": "Keywords are loaded and visible.",
        }.get(empty_reason, empty_reason),
    }

def keyword_list_payload(query: str = "", category: str = "", subcategory: str = "", include_builtin: bool = True, library_path: str = "") -> dict[str, Any]:
    markdown_records = _load_markdown_keywords(library_path)
    custom_sidecar = _read_records(KEYWORDS_PATH)
    legacy_builtin = DEFAULT_KEYWORDS if include_builtin and not markdown_records else []
    records = markdown_records + custom_sidecar + legacy_builtin
    # De-dupe records by keyword id/label while preserving Markdown first.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        key = str(item.get("keyword_id") or item.get("id") or item.get("label") or item.get("name") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        if item.get("enabled") is False:
            continue
        deduped.append(item)
    q = str(query or "").strip().casefold()
    cn = _norm_token(category)
    sn = _norm_token(subcategory)
    categories = ["all"] + sorted({str(item.get("category") or "General") for item in deduped})
    selected_category = category if category in categories else "all"
    scoped_for_subs = [item for item in deduped if selected_category in {"", "all", "*"} or _norm_token(item.get("category")) == _norm_token(selected_category)]
    subcategories = ["all"] + sorted({str(item.get("subcategory") or "general") for item in scoped_for_subs})
    selected_subcategory = subcategory if subcategory in subcategories else "all"
    cn = _norm_token(selected_category)
    sn = _norm_token(selected_subcategory)
    filtered: list[dict[str, Any]] = []
    for item in deduped:
        cat = str(item.get("category") or "General")
        sub = str(item.get("subcategory") or "general")
        if cn and cn not in {"all", "*"} and _norm_token(cat) != cn:
            continue
        if sn and sn not in {"all", "*"} and _norm_token(sub) != sn:
            continue
        if q:
            hay = " ".join(str(item.get(key) or "") for key in ("label", "name", "category", "subcategory", "desc", "description", "prompt_fragment", "source_name"))
            if item.get("aliases"):
                hay += " " + " ".join(str(alias) for alias in item.get("aliases") or [])
            hay_norm = _norm_token(hay)
            q_norm = _norm_token(q)
            q_tokens = [token for token in re.split(r"[^a-z0-9]+", q.casefold()) if token]
            # Match V1 behavior more closely: normalized text matching, with a
            # practical all-token fallback for searches like "piggyback / hoodie".
            if q_norm and q_norm not in hay_norm and not all(token in hay_norm for token in q_tokens):
                continue
        filtered.append(item)
    diagnostics = keyword_library_diagnostics(library_path, filtered_count=len(filtered), total_count=len(deduped))
    return {
        "ok": True,
        "records": filtered,
        "entries": [{"label": (item.get("label") or item.get("name") or ""), "id": item.get("keyword_id") or item.get("id")} for item in filtered],
        "count": len(filtered),
        "total": len(deduped),
        "categories": categories,
        "subcategories": subcategories,
        "selected_category": selected_category,
        "selected_subcategory": selected_subcategory,
        "library_path": str(KEYWORD_LIBRARY_DIR),
        "library_file_count": diagnostics.get("loaded_file_count", 0),
        "library_roots": diagnostics.get("library_roots", []),
        "library_existing_roots": diagnostics.get("existing_roots", []),
        "library_files": diagnostics.get("sample_files", []),
        "keyword_diagnostics": diagnostics,
        "manual_library_path": str(library_path or ""),
        "manual_library_path_status": diagnostics.get("manual_path_status", "not_set"),
        "active_library_root": diagnostics.get("active_root", ""),
        "project_root_candidates": [str(root) for root in _project_root_candidates()],
        "active_query": q,
        "active_category": category or "all",
        "active_subcategory": subcategory or "all",
        "empty_reason": ("filters_or_search" if not filtered and deduped else "no_markdown_keywords" if not deduped else ""),
        "source": "markdown_libraries",
    }


def keyword_record_payload(keyword_id: str = "") -> dict[str, Any]:
    if not keyword_id:
        return {"ok": False, "errors": ["Keyword id is required."]}
    for item in _load_markdown_keywords() + _read_records(KEYWORDS_PATH) + DEFAULT_KEYWORDS:
        if str(item.get("keyword_id") or item.get("id") or "") == str(keyword_id):
            return {"ok": True, "record": item}
    return {"ok": False, "errors": ["Keyword not found."]}


def keyword_insert_text_payload(keyword_id: str = "", include_desc: bool = True) -> dict[str, Any]:
    record_payload = keyword_record_payload(keyword_id)
    if not record_payload.get("ok"):
        return record_payload
    record = record_payload.get("record") or {}
    return {"ok": True, "text": _keyword_insert_text(record, include_desc=include_desc), "record": record}


def _caption_image_url(item: dict[str, Any]) -> str:
    url = str(item.get("library_image_url") or item.get("stored_image_url") or item.get("source_image_url") or item.get("preview_url") or item.get("url") or "").strip()
    if url:
        return url
    for key in ("library_image", "stored_image", "source_image", "asset_name", "image"):
        value = str(item.get(key) or "").strip()
        if value and re.search(r"\.(png|jpe?g|webp|bmp|gif)$", value, re.I):
            return f"/api/prompt-captioning/caption/asset/{Path(value).name}"
    return ""


def _caption_record_to_browser_item(item: dict[str, Any], source: str) -> dict[str, Any] | None:
    caption = str(item.get("caption") or item.get("output_caption") or item.get("output_text") or item.get("text") or "").strip()
    if not caption:
        return None
    return {
        "caption_item_id": item.get("caption_id") or item.get("history_id") or item.get("id") or f"caption_{uuid4().hex[:12]}",
        "source": source,
        "title": item.get("name") or item.get("title") or Path(str(item.get("source_image") or item.get("image") or "Caption")).stem or "Caption",
        "caption": _clip_text(caption, MAX_PROMPT_TEXT),
        "source_image": item.get("source_image") or item.get("asset_name") or item.get("image") or "",
        "image_url": _caption_image_url(item),
        "library_image_url": item.get("library_image_url") or item.get("stored_image_url") or "",
        "metadata_card": item.get("metadata_card") or "",
        "category": item.get("category") or "Caption",
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or item.get("created_at") or "",
        "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    }


def _caption_records_from_core() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, source in ((CAPTION_RECORDS_PATH, "saved_caption"), (CAPTION_HISTORY_PATH, "caption_history")):
        for item in _read_records(path):
            browser_item = _caption_record_to_browser_item(item, source)
            if browser_item:
                records.append(browser_item)

    # Also surface the latest Batch Captioning run results, including Dataset
    # Preparation runs that were not saved into the caption library.
    for batch in _read_records(CAPTION_BATCH_RESULTS_PATH):
        batch_id = str(batch.get("batch_id") or "")
        workflow = str(batch.get("workflow_mode") or "batch")
        created = str(batch.get("created_at") or "")
        for index, result in enumerate(batch.get("results") if isinstance(batch.get("results"), list) else []):
            browser_item = _caption_record_to_browser_item(result if isinstance(result, dict) else {}, "batch_result")
            if not browser_item:
                continue
            browser_item["caption_item_id"] = f"{batch_id}_{index}" if batch_id else browser_item["caption_item_id"]
            browser_item["title"] = browser_item.get("title") or Path(str(result.get("image") or result.get("file") or "Batch caption")).stem
            browser_item["category"] = browser_item.get("category") or f"Batch / {workflow}"
            browser_item["created_at"] = browser_item.get("created_at") or created
            browser_item["metadata"] = {**(browser_item.get("metadata") or {}), "batch_id": batch_id, "workflow_mode": workflow}
            records.append(browser_item)
    return records


def _sort_caption_browser_records(records: list[dict[str, Any]], sort_by: str = "newest") -> list[dict[str, Any]]:
    sort = str(sort_by or "newest").strip().lower()
    if sort == "oldest":
        return sorted(records, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    if sort == "name":
        return sorted(records, key=lambda item: str(item.get("title") or item.get("name") or "").casefold())
    if sort == "category":
        return sorted(records, key=lambda item: (str(item.get("category") or "").casefold(), str(item.get("title") or "").casefold()))
    return sorted(records, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)


def caption_browser_save_payload(payload: dict[str, Any]) -> dict[str, Any]:
    caption = _clip_text(payload.get("caption") or payload.get("text") or payload.get("output_caption") or "", MAX_PROMPT_TEXT)
    if not caption:
        return {"ok": False, "errors": ["Caption text is required."]}
    item_id = str(payload.get("caption_item_id") or f"caption_item_{uuid4().hex[:12]}")
    record = {
        "caption_item_id": item_id,
        "source": "caption_browser",
        "title": _clip_text(payload.get("title") or payload.get("name") or "Saved caption", 160),
        "caption": caption,
        "source_image": _clip_text(payload.get("source_image") or payload.get("asset_name") or "", 500),
        "source_image_url": _clip_text(payload.get("source_image_url") or payload.get("image_url") or payload.get("preview_url") or "", 500),
        "image_url": _clip_text(payload.get("source_image_url") or payload.get("image_url") or payload.get("preview_url") or "", 500),
        "category": _clip_text(payload.get("category") or "Caption", 80) or "Caption",
        "tags": _split_tags(payload.get("tags") or ""),
        "notes": _clip_text(payload.get("notes") or "", 1000),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    saved, records = _upsert(CAPTION_BROWSER_PATH, "caption_item_id", record)
    return {"ok": True, "record": saved, "records": records, "count": len(records)}


def caption_browser_list_payload(query: str = "", category: str = "", include_core: bool = True, sort_by: str = "newest") -> dict[str, Any]:
    custom = _read_records(CAPTION_BROWSER_PATH)
    normalized_custom = []
    for item in custom:
        if isinstance(item, dict):
            cloned = dict(item)
            cloned.setdefault("image_url", _caption_image_url(cloned))
            cloned.setdefault("created_at", cloned.get("updated_at") or "")
            normalized_custom.append(cloned)
    records = normalized_custom + (_caption_records_from_core() if include_core else [])
    filtered = _filter_records(records, query, category)
    sorted_records = _sort_caption_browser_records(filtered, sort_by)
    categories = sorted({str(item.get("category") or "Caption") for item in records if str(item.get("category") or "").strip()})
    return {"ok": True, "records": sorted_records, "count": len(sorted_records), "categories": categories, "sort_by": sort_by}


def caption_browser_send_to_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    item_id = str(payload.get("caption_item_id") or "")
    caption = _clip_text(payload.get("caption") or "", MAX_PROMPT_TEXT)
    record = None
    if item_id:
        record = next((item for item in caption_browser_list_payload(include_core=True).get("records", []) if item.get("caption_item_id") == item_id), None)
        if record and not caption:
            caption = str(record.get("caption") or "")
    if not caption:
        return {"ok": False, "errors": ["Caption Browser needs a caption to send to Prompt Studio."]}
    instruction = _clip_text(payload.get("instruction") or "Convert this caption into a clean image prompt while preserving the described subject.", 1000)
    return {
        "ok": True,
        "prompt_payload": {
            "inputs": {
                "source_text": caption,
                "custom_instructions": instruction,
                "style": payload.get("style") or "caption-to-prompt",
            },
            "params": {
                "insert_mode": payload.get("insert_mode") or "append",
            },
            "metadata": {
                "source": "caption_browser",
                "caption_item_id": item_id,
            },
        },
        "caption": caption,
        "record": record,
    }
