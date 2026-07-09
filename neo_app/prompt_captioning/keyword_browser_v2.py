from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_KEYWORD_DIR = ROOT / "neo_data" / "prompt_captioning" / "keywords"
DEFAULT_LIBRARY_DIR = DATA_KEYWORD_DIR / "libraries"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _clip(value: Any, limit: int = 4000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _resolve_user_path(raw: str = "") -> tuple[str, list[Path]]:
    """Resolve user-entered Keyword Browser paths without legacy shell magic.

    The rebuild intentionally accepts only clear folder/file paths and a small
    suffix recovery for copied relative paths like
    ``Neo_Studio_V2\\neo_data\\prompt_captioning\\keywords\\Libraries``.
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return "", []
    normalized = text.replace("\\", "/")
    p = Path(normalized).expanduser()
    candidates: list[Path] = [p]
    if p.suffix.casefold() != ".md":
        candidates.extend([p / "libraries", p / "Libraries"])
    parts = [part for part in p.parts if part not in {"", "."}]
    lowered = [part.casefold() for part in parts]
    for marker in ("neo_data", "prompt_captioning", "keywords"):
        if marker in lowered:
            suffix = Path(*parts[lowered.index(marker):])
            if suffix.parts and suffix.parts[0].casefold() == "neo_data":
                candidates.append(ROOT / suffix)
            elif suffix.parts and suffix.parts[0].casefold() == "prompt_captioning":
                candidates.append(ROOT / "neo_data" / suffix)
            elif suffix.parts and suffix.parts[0].casefold() == "keywords":
                candidates.append(ROOT / "neo_data" / "prompt_captioning" / suffix)
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c).casefold()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return normalized, out


def _scan_roots(library_path: str = "") -> tuple[list[Path], str, list[str]]:
    manual_text, manual_candidates = _resolve_user_path(library_path)
    roots: list[Path] = []
    notes: list[str] = []
    if manual_candidates:
        roots.extend(manual_candidates)
        notes.append("manual path supplied")
    else:
        roots.extend([
            DEFAULT_LIBRARY_DIR,
            DATA_KEYWORD_DIR / "Libraries",
            DATA_KEYWORD_DIR,
        ])
        notes.append("default keyword paths")
    # Keep this tiny and deterministic. No legacy V1 crawl here.
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out, manual_text, notes


def _clean_label(value: Any, fallback: str = "general") -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def _category_from_path(path: Path, root: Path) -> tuple[str, str]:
    """Return display Category/Subcategory for a keyword .md file.

    Keyword Browser V2 uses the filename contract first:
    ``Category__Subcategory.md``. A file such as
    ``Composition__Camera.md`` is always Category ``Composition`` and
    Subcategory ``Camera``, even when it lives inside a folder. Nested filename
    parts are supported too, e.g. ``Accessories__Amulet__Fantasy_Safe.md`` ->
    Category ``Accessories`` and Subcategory ``Amulet / Fantasy Safe``. Folder
    structure is only a fallback for plain filenames.
    """
    stem = path.stem.strip() or "General"
    if "__" in stem:
        bits = [_clean_label(b, "") for b in stem.split("__") if _clean_label(b, "")]
        if bits:
            return bits[0], " / ".join(bits[1:]) if len(bits) > 1 else "general"
    try:
        rel = path.relative_to(root if root.is_dir() else root.parent)
    except Exception:
        rel = Path(path.name)
    parts = list(rel.parts)
    if len(parts) >= 2:
        category = _clean_label(parts[0], "General")
        subcategory = Path(*parts[1:]).with_suffix("").as_posix().replace("/", " / ")
        return category, _clean_label(subcategory, "general")
    return _clean_label(stem, "General"), "general"


def _parse_line(line: str) -> tuple[str, str, list[str]] | None:
    text = str(line or "").strip()
    if not text or text.startswith(("#", "//", "```")) or text == "---":
        return None
    text = re.sub(r"^[-*+]\s+", "", text).strip()
    text = re.sub(r"^\d+[.)]\s+", "", text).strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split("|") if p.strip()]
    label = parts[0]
    desc = ""
    aliases: list[str] = []
    if len(parts) == 1:
        for sep in (" — ", " – ", " - "):
            if sep in label:
                label, desc = [x.strip() for x in label.split(sep, 1)]
                break
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, value = [x.strip() for x in part.split(":", 1)]
        if key.casefold() in {"desc", "description"}:
            desc = value
        elif key.casefold() in {"alias", "aliases"}:
            aliases = [x.strip() for x in value.split(",") if x.strip()]
    label = label.strip()
    if not label:
        return None
    return label, desc, aliases


def _md_files_for_root(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.casefold() == ".md":
        return [root]
    if not root.exists() or not root.is_dir():
        return []
    return sorted([p for p in root.rglob("*.md") if p.is_file() and not p.name.startswith(".")], key=lambda p: str(p).casefold())


def keyword_browser_v2_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    query = str(payload.get("query") or "").strip()
    category = str(payload.get("category") or "all").strip() or "all"
    subcategory = str(payload.get("subcategory") or "all").strip() or "all"
    library_path = str(payload.get("library_path") or "").strip()
    roots, manual_text, notes = _scan_roots(library_path)
    files: list[Path] = []
    for root in roots:
        files.extend(_md_files_for_root(root))
    # Stable de-dupe.
    deduped_files: list[Path] = []
    seen_files: set[str] = set()
    for path in files:
        key = str(path.resolve() if path.exists() else path).casefold()
        if key not in seen_files:
            seen_files.add(key)
            deduped_files.append(path)
    records: list[dict[str, Any]] = []
    for path in deduped_files:
        root_for_cat = next((root for root in roots if root.exists() and root.is_dir() and str(path).casefold().startswith(str(root).casefold())), path.parent)
        cat, sub = _category_from_path(path, root_for_cat)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            records.append({
                "keyword_id": f"kwv2_error::{len(records)}",
                "label": f"Could not read {path.name}",
                "category": "Diagnostics",
                "subcategory": "errors",
                "desc": str(exc),
                "source_name": path.name,
                "source_path": str(path),
                "enabled": False,
            })
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            parsed = _parse_line(line)
            if not parsed:
                continue
            label, desc, aliases = parsed
            title = aliases[0] if aliases else label
            records.append({
                "keyword_id": f"kwv2::{_norm(str(path))}::{line_no}",
                "label": label,
                "name": label,
                "title": title,
                "keyword_title": title,
                "category": cat,
                "subcategory": sub,
                "desc": desc,
                "description": desc,
                "aliases": aliases,
                "source_name": path.name,
                "source_path": str(path),
                "line": line_no,
                "prompt_fragment": desc or label,
                "enabled": True,
                "source": "keyword_browser_v2_markdown",
                "source_contract": "filename_category_subcategory_v1",
            })
    all_records = [r for r in records if r.get("enabled") is not False]
    q_norm = _norm(query)
    cat_norm = _norm(category)
    sub_norm = _norm(subcategory)
    visible: list[dict[str, Any]] = []
    for item in all_records:
        if cat_norm and cat_norm not in {"all", ""} and _norm(item.get("category")) != cat_norm:
            continue
        if sub_norm and sub_norm not in {"all", ""} and _norm(item.get("subcategory")) != sub_norm:
            continue
        if q_norm:
            # Search filters keyword titles only. Category/subcategory already map to
            # the filename contract, e.g. Composition__Camera.md.
            hay = " ".join(str(item.get(k) or "") for k in ("label", "title", "keyword_title", "aliases"))
            hay_norm = _norm(hay)
            tokens = [t for t in re.split(r"[^a-z0-9]+", query.casefold()) if t]
            if q_norm not in hay_norm and not all(t in hay_norm for t in tokens):
                continue
        visible.append(item)
    categories = ["all"] + sorted({str(r.get("category") or "General") for r in all_records})
    category_subcategories = {
        cat: ["all"] + sorted({str(r.get("subcategory") or "general") for r in all_records if str(r.get("category") or "General") == cat})
        for cat in categories
        if cat != "all"
    }
    category_subcategories["all"] = ["all"] + sorted({str(r.get("subcategory") or "general") for r in all_records})
    scoped = all_records if category in {"", "all"} else [r for r in all_records if _norm(r.get("category")) == cat_norm]
    subcategories = category_subcategories.get(category, ["all"] + sorted({str(r.get("subcategory") or "general") for r in scoped}))
    existing_roots = [str(root) for root in roots if root.exists()]
    empty_reason = ""
    if not deduped_files:
        empty_reason = "no_markdown_files"
    elif not all_records:
        empty_reason = "markdown_files_have_no_keywords"
    elif not visible:
        empty_reason = "filters_hide_keywords"
    return {
        "ok": True,
        "schema": "neo.prompt_captioning.keyword_browser_v2.v1",
        "source": "keyword_browser_v2_rebuild",
        "records": visible,
        "count": len(visible),
        "total": len(all_records),
        "categories": categories,
        "subcategories": subcategories,
        "category_subcategories": category_subcategories,
        "filter_contract": {
            "schema": "neo.prompt_captioning.keyword_browser_v2.filename_filter_contract.v1",
            "filename_pattern": "Category__Subcategory.md",
            "category_source": "filename prefix before double underscore",
            "subcategory_source": "filename suffix after double underscore",
            "search_scope": "keyword title / alias only",
        },
        "selected_category": category if category in categories else "all",
        "selected_subcategory": subcategory if subcategory in subcategories else "all",
        "diagnostics": {
            "schema": "neo.prompt_captioning.keyword_browser_v2.diagnostics.v1",
            "repo_root": str(ROOT),
            "default_library_dir": str(DEFAULT_LIBRARY_DIR),
            "manual_library_path": manual_text,
            "scanned_roots": [str(root) for root in roots],
            "existing_roots": existing_roots,
            "markdown_files": [str(path) for path in deduped_files[:200]],
            "markdown_file_count": len(deduped_files),
            "loaded_keyword_count": len(all_records),
            "visible_keyword_count": len(visible),
            "empty_reason": empty_reason,
            "notes": notes,
            "python_executable": sys.executable,
        },
    }


# --- Keyword Browser V2 Manager -------------------------------------------------
# Local-only .md keyword library editing helpers. These functions intentionally
# operate only inside the selected Keyword Browser V2 library root and only on
# .md files, so the UI can edit/create/upload keyword files without touching
# random project files.

MANAGER_SCHEMA = "neo.prompt_captioning.keyword_browser_v2.manager.v1"
MANAGER_WRITE_SCHEMA = "neo.prompt_captioning.keyword_browser_v2.manager_write.v1"


def _manager_library_root(library_path: str = "") -> Path:
    roots, _manual_text, _notes = _scan_roots(library_path)
    for root in roots:
        if root.exists() and root.is_dir():
            return root.resolve()
    root = (roots[0] if roots else DEFAULT_LIBRARY_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename_part(value: Any, fallback: str = "General") -> str:
    text = str(value or "").strip().replace("/", " ").replace("\\", " ")
    text = re.sub(r"[^A-Za-z0-9 _.-]+", " ", text)
    text = re.sub(r"\s+", "_", text).strip("._-")
    return text or fallback


def _safe_manager_filename(value: Any = "", *, category: str = "", subcategory: str = "") -> str:
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw and (category or subcategory):
        raw = f"{_safe_filename_part(category)}__{_safe_filename_part(subcategory or 'General')}"
    raw = raw.replace("\\", "/").split("/")[-1]
    if raw.casefold().endswith(".md"):
        raw = raw[:-3]
    raw = _safe_filename_part(raw, "General__Keywords")
    if "__" not in raw and category:
        raw = f"{_safe_filename_part(category)}__{raw}"
    return f"{raw}.md"


def _manager_file_path(file_name: str, library_path: str = "") -> tuple[Path, Path]:
    root = _manager_library_root(library_path)
    safe_name = _safe_manager_filename(file_name)
    path = (root / safe_name).resolve()
    try:
        path.relative_to(root)
    except Exception as exc:  # pragma: no cover - defensive path safety guard.
        raise ValueError("Keyword Manager file must stay inside the active library folder.") from exc
    if path.suffix.casefold() != ".md":
        raise ValueError("Keyword Manager only edits .md files.")
    return root, path


def _manager_file_record(path: Path, root: Path) -> dict[str, Any]:
    category, subcategory = _category_from_path(path, root)
    try:
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        line_count = 0
    return {
        "file_name": path.name,
        "path": str(path),
        "category": category,
        "subcategory": subcategory,
        "line_count": line_count,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def keyword_browser_v2_manager_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    library_path = str(payload.get("library_path") or "").strip()
    selected_file = str(payload.get("file_name") or "").strip()
    root = _manager_library_root(library_path)
    files = sorted([p for p in root.rglob("*.md") if p.is_file() and not p.name.startswith(".")], key=lambda p: str(p).casefold())
    records = [_manager_file_record(path, root) for path in files]
    if not selected_file and records:
        selected_file = records[0]["file_name"]
    content = ""
    selected_record = None
    if selected_file:
        try:
            _root, selected_path = _manager_file_path(selected_file, str(root))
            if selected_path.exists():
                content = selected_path.read_text(encoding="utf-8", errors="ignore")
                selected_record = _manager_file_record(selected_path, root)
        except Exception:
            selected_record = None
    return {
        "ok": True,
        "schema": MANAGER_SCHEMA,
        "library_root": str(root),
        "files": records,
        "file_count": len(records),
        "selected_file": selected_file if selected_record else "",
        "selected_record": selected_record,
        "content": content,
        "status": f"{len(records)} Markdown file(s) available for Keyword Manager.",
    }


def keyword_browser_v2_manager_save_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    root, path = _manager_file_path(str(payload.get("file_name") or ""), str(payload.get("library_path") or ""))
    content = _clip(payload.get("content") or "", 200000)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_dir = root / ".keyword_manager_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{path.stem}.{datetime_now_slug()}.bak.md"
        backup.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    return {"ok": True, "schema": MANAGER_WRITE_SCHEMA, "action": "save", "file_name": path.name, "path": str(path), "library_root": str(root)}


def datetime_now_slug() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_keyword_line(keyword: str, alias: str = "", desc: str = "") -> str:
    key = _clip(keyword, 280)
    if not key:
        raise ValueError("Keyword title is required.")
    parts = [key]
    alias = _clip(alias, 280)
    desc = _clip(desc, 2000)
    if alias:
        parts.append(f"alias:{alias}")
    if desc:
        parts.append(f"desc:{desc}")
    return " | ".join(parts)


def keyword_browser_v2_manager_append_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    root, path = _manager_file_path(str(payload.get("file_name") or ""), str(payload.get("library_path") or ""))
    line = _format_keyword_line(str(payload.get("keyword") or ""), str(payload.get("alias") or ""), str(payload.get("desc") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    new = old.rstrip() + ("\n" if old.strip() else "") + line + "\n"
    path.write_text(new, encoding="utf-8")
    return {"ok": True, "schema": MANAGER_WRITE_SCHEMA, "action": "append", "file_name": path.name, "line": line, "path": str(path), "library_root": str(root)}


def keyword_browser_v2_manager_create_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    file_name = _safe_manager_filename(payload.get("file_name") or "", category=str(payload.get("category") or ""), subcategory=str(payload.get("subcategory") or ""))
    root, path = _manager_file_path(file_name, str(payload.get("library_path") or ""))
    if path.exists() and not bool(payload.get("overwrite", False)):
        raise ValueError(f"{path.name} already exists. Use Save File to edit it.")
    content = _clip(payload.get("content") or "", 200000)
    if not content.strip():
        content = "# Keyword library\n# Format: keyword | alias:Display Name | desc:prompt fragment\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    return {"ok": True, "schema": MANAGER_WRITE_SCHEMA, "action": "create", "file_name": path.name, "path": str(path), "library_root": str(root)}


def keyword_browser_v2_manager_upload_text_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    file_name = _safe_manager_filename(payload.get("file_name") or "uploaded_keywords.md")
    content = _clip(payload.get("content") or "", 200000)
    if not content.strip():
        raise ValueError("Uploaded Markdown content is empty.")
    root, path = _manager_file_path(file_name, str(payload.get("library_path") or ""))
    if path.exists() and not bool(payload.get("overwrite", True)):
        raise ValueError(f"{path.name} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    return {"ok": True, "schema": MANAGER_WRITE_SCHEMA, "action": "upload_text", "file_name": path.name, "path": str(path), "library_root": str(root)}
