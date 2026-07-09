from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_CHARACTER_DIR = ROOT / "neo_data" / "prompt_captioning" / "characters"
DEFAULT_LIBRARY_DIR = DATA_CHARACTER_DIR / "libraries"
FALLBACK_KEYWORD_LIBRARY_DIR = ROOT / "neo_data" / "prompt_captioning" / "keywords" / "libraries"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _clean_label(value: Any, fallback: str = "general") -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def _resolve_user_path(raw: str = "") -> tuple[str, list[Path]]:
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
    for marker in ("neo_data", "prompt_captioning", "characters", "character_builder", "keywords"):
        if marker in lowered:
            suffix = Path(*parts[lowered.index(marker):])
            if suffix.parts and suffix.parts[0].casefold() == "neo_data":
                candidates.append(ROOT / suffix)
            elif suffix.parts and suffix.parts[0].casefold() == "prompt_captioning":
                candidates.append(ROOT / "neo_data" / suffix)
            elif suffix.parts and suffix.parts[0].casefold() in {"characters", "character_builder"}:
                candidates.append(ROOT / "neo_data" / "prompt_captioning" / "characters" / Path(*suffix.parts[1:]))
            elif suffix.parts and suffix.parts[0].casefold() == "keywords":
                candidates.append(ROOT / "neo_data" / "prompt_captioning" / "keywords" / Path(*suffix.parts[1:]))
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
            DATA_CHARACTER_DIR / "Libraries",
            DATA_CHARACTER_DIR,
            ROOT / "neo_data" / "prompt_captioning" / "character_builder" / "libraries",
            ROOT / "neo_data" / "prompt_captioning" / "character_builder" / "Libraries",
            # Fallback lets existing Gender__Category__Type__Era.md files work if
            # they were placed in the same keyword library folder during testing.
            FALLBACK_KEYWORD_LIBRARY_DIR,
        ])
        notes.append("default character library paths")
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out, manual_text, notes


def _md_files_for_root(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.casefold() == ".md":
        return [root]
    if not root.exists() or not root.is_dir():
        return []
    return sorted([p for p in root.rglob("*.md") if p.is_file() and not p.name.startswith(".")], key=lambda p: str(p).casefold())


def _filename_parts(path: Path) -> tuple[str, str, str, str]:
    """Parse Gender__Category__Type__Era.md.

    Extra filename sections are folded into Era with slash display so files such
    as Male__Accessories__Amulet__Fantasy_Safe.md display cleanly as
    Male / Accessories / Amulet / Fantasy Safe.
    """
    bits = [_clean_label(b, "") for b in path.stem.split("__") if _clean_label(b, "")]
    if len(bits) >= 4:
        return bits[0], bits[1], bits[2], " / ".join(bits[3:])
    if len(bits) == 3:
        return bits[0], bits[1], bits[2], "general"
    if len(bits) == 2:
        return bits[0], bits[1], "general", "general"
    return "General", _clean_label(path.parent.name if path.parent else "General", "General"), _clean_label(path.stem, "general"), "general"


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


def _option_list(values: set[str]) -> list[str]:
    return ["all"] + sorted(v for v in values if v)


def character_keyword_browser_v2_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    library_path = str(payload.get("library_path") or "").strip()
    gender = str(payload.get("gender") or "all").strip() or "all"
    category = str(payload.get("category") or "all").strip() or "all"
    item_type = str(payload.get("type") or payload.get("item_type") or "all").strip() or "all"
    era = str(payload.get("era") or "all").strip() or "all"
    query = str(payload.get("query") or "").strip()

    roots, manual_text, notes = _scan_roots(library_path)
    files: list[Path] = []
    for root in roots:
        files.extend(_md_files_for_root(root))
    deduped_files: list[Path] = []
    seen_files: set[str] = set()
    for path in files:
        # Character Browser only consumes files with the explicit 4-part contract.
        if len([b for b in path.stem.split("__") if b.strip()]) < 4:
            continue
        key = str(path.resolve() if path.exists() else path).casefold()
        if key not in seen_files:
            seen_files.add(key)
            deduped_files.append(path)

    records: list[dict[str, Any]] = []
    for path in deduped_files:
        g, cat, typ, er = _filename_parts(path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            records.append({
                "keyword_id": f"charv2_error::{len(records)}",
                "label": f"Could not read {path.name}",
                "gender": "Diagnostics",
                "category": "errors",
                "type": "errors",
                "era": "errors",
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
                "keyword_id": f"charv2::{_norm(str(path))}::{line_no}",
                "label": label,
                "name": label,
                "title": title,
                "keyword_title": title,
                "gender": g,
                "category": cat,
                "type": typ,
                "era": er,
                "desc": desc,
                "description": desc,
                "aliases": aliases,
                "source_name": path.name,
                "source_path": str(path),
                "line": line_no,
                "prompt_fragment": desc or label,
                "enabled": True,
                "source": "character_keyword_browser_v2_markdown",
                "source_contract": "gender_category_type_era_filename_v1",
            })

    all_records = [r for r in records if r.get("enabled") is not False]
    g_norm, c_norm, t_norm, e_norm, q_norm = map(_norm, (gender, category, item_type, era, query))
    visible: list[dict[str, Any]] = []
    for item in all_records:
        if g_norm not in {"", "all"} and _norm(item.get("gender")) != g_norm:
            continue
        if c_norm not in {"", "all"} and _norm(item.get("category")) != c_norm:
            continue
        if t_norm not in {"", "all"} and _norm(item.get("type")) != t_norm:
            continue
        if e_norm not in {"", "all"} and _norm(item.get("era")) != e_norm:
            continue
        if q_norm:
            hay = " ".join(str(item.get(k) or "") for k in ("label", "title", "keyword_title", "aliases"))
            hay_norm = _norm(hay)
            tokens = [tok for tok in re.split(r"[^a-z0-9]+", query.casefold()) if tok]
            if q_norm not in hay_norm and not all(tok in hay_norm for tok in tokens):
                continue
        visible.append(item)

    genders = _option_list({str(r.get("gender") or "General") for r in all_records})
    gender_scoped = all_records if gender in {"", "all"} else [r for r in all_records if _norm(r.get("gender")) == g_norm]
    categories = _option_list({str(r.get("category") or "General") for r in gender_scoped})
    category_scoped = gender_scoped if category in {"", "all"} else [r for r in gender_scoped if _norm(r.get("category")) == c_norm]
    types = _option_list({str(r.get("type") or "general") for r in category_scoped})
    type_scoped = category_scoped if item_type in {"", "all"} else [r for r in category_scoped if _norm(r.get("type")) == t_norm]
    eras = _option_list({str(r.get("era") or "general") for r in type_scoped})

    empty_reason = ""
    if not deduped_files:
        empty_reason = "no_character_markdown_files"
    elif not all_records:
        empty_reason = "character_markdown_files_have_no_keywords"
    elif not visible:
        empty_reason = "filters_hide_character_keywords"

    existing_roots = [str(root) for root in roots if root.exists()]
    return {
        "ok": True,
        "schema": "neo.prompt_captioning.character_keyword_browser_v2.v1",
        "source": "character_keyword_browser_v2_rebuild",
        "records": visible,
        "count": len(visible),
        "total": len(all_records),
        "genders": genders,
        "categories": categories,
        "types": types,
        "eras": eras,
        "selected_gender": gender if gender in genders else "all",
        "selected_category": category if category in categories else "all",
        "selected_type": item_type if item_type in types else "all",
        "selected_era": era if era in eras else "all",
        "filter_contract": {
            "schema": "neo.prompt_captioning.character_keyword_browser_v2.filename_filter_contract.v1",
            "filename_pattern": "Gender__Category__Type__Era.md",
            "gender_source": "filename part 1",
            "category_source": "filename part 2",
            "type_source": "filename part 3",
            "era_source": "filename part 4+",
            "search_scope": "keyword title / alias only",
        },
        "diagnostics": {
            "schema": "neo.prompt_captioning.character_keyword_browser_v2.diagnostics.v1",
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
