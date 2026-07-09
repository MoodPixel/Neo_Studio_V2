from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib import parse, request
import re

SUPPORTED_PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PREVIEWS = 6


def preview_cache_dir(root: str | Path, record_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (record_id or "unknown"))
    return Path(root) / "neo_data" / "extensions" / "embeddings_ti" / "previews" / safe


def normalize_preview_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in paths or []:
        if len(out) >= MAX_PREVIEWS:
            break
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def preview_url_candidates(url: str) -> list[str]:
    text = str(url or "").strip()
    if not text:
        return []
    candidates: list[str] = []
    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)
    add(text)
    parsed = parse.urlparse(text)
    if "image.civitai" in parsed.netloc.casefold():
        for width in ("width=450", "width=512", "width=768", "width=1024"):
            if "/original=true/" in text:
                add(text.replace("/original=true/", f"/{width}/"))
            if re.search(r"/width=\d+/", text):
                add(re.sub(r"/width=\d+/", f"/{width}/", text))
    return candidates


def _extension_from_url(url: str) -> str:
    suffix = Path(parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in SUPPORTED_PREVIEW_EXTENSIONS else ".jpg"


def cache_preview_urls(root: str | Path, record_id: str, urls: list[str], *, fetcher=None) -> dict[str, Any]:
    folder = preview_cache_dir(root, record_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    errors: list[str] = []
    for index, url in enumerate(normalize_preview_paths(urls), start=1):
        last_error = None
        saved_this = False
        for candidate in preview_url_candidates(url):
            target = folder / f"civitai_{index:02d}{_extension_from_url(candidate)}"
            try:
                if fetcher:
                    raw = fetcher(candidate)
                else:
                    req = request.Request(candidate, headers={"Accept": "image/*,*/*;q=0.8", "User-Agent": "Mozilla/5.0 NeoStudio/2.0 EmbeddingsTIPreviewPull", "Referer": "https://civitai.com/"})
                    with request.urlopen(req, timeout=20) as response:
                        raw = response.read()
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                target.write_bytes(raw)
                saved.append(str(target))
                saved_this = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if not saved_this and last_error is not None:
            errors.append(f"{url}: {last_error}")
    return {"ok": not errors or bool(saved), "paths": saved, "errors": errors, "count": len(saved)}


def preview_file_response(path: str, *, root: str | Path | None = None) -> dict[str, Any]:
    target = Path(path)
    suffix = target.suffix.lower()
    if root is not None:
        try:
            data_root = (Path(root) / "neo_data" / "extensions" / "embeddings_ti").resolve()
            resolved = target.resolve()
            if data_root not in resolved.parents and resolved != data_root:
                # Local sidecar previews beside embeddings are allowed too.
                pass
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": str(target), "media_type": "", "error": str(exc)}
    ok_suffix = suffix in SUPPORTED_PREVIEW_EXTENSIONS
    return {"ok": ok_suffix and target.exists() and target.is_file(), "path": str(target), "media_type": f"image/{'jpeg' if suffix in {'.jpg', '.jpeg'} else suffix.lstrip('.')}", "error": "" if ok_suffix else "Unsupported preview type."}
