from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json


def safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def resolve_inside(root: Path, value: str | Path) -> Path | None:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
        base = root.resolve()
        if resolved != base and base not in resolved.parents:
            return None
        return resolved
    except Exception:
        return None


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def json_records(path: Path, *, keys: Iterable[str] = ("records", "items", "jobs", "profiles")) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def file_revision_token(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {"path": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "content": path.read_bytes()}
    except Exception:
        return {"path": str(path), "missing": True}


def paginate(items: list[Any], cursor: str = "", limit: int = 100) -> tuple[list[Any], str, int]:
    try:
        offset = max(0, int(cursor or 0))
    except (TypeError, ValueError):
        offset = 0
    size = max(1, min(int(limit or 100), 1000))
    page = items[offset: offset + size]
    next_cursor = str(offset + size) if offset + size < len(items) else ""
    return page, next_cursor, len(items)
