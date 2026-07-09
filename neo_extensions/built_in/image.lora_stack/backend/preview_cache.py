from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib import parse, request
import re

SUPPORTED_PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PREVIEWS = 6


def preview_cache_dir(root: str | Path, record_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (record_id or "unknown"))
    return Path(root) / "neo_data" / "extensions" / "lora_stack" / "lora_previews" / safe


def normalize_preview_paths(paths: list[str]) -> list[str]:
    seen: list[str] = []
    keys: set[str] = set()
    for value in paths:
        if len(seen) >= MAX_PREVIEWS:
            break
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in keys:
            continue
        keys.add(key)
        seen.append(text)
    return seen



def preview_url_candidates(url: str) -> list[str]:
    """Return CivitAI preview URL variants.

    CivitAI image URLs can expire or reject the `original=true` transform while
    the same asset remains available through width transforms. Try stable web
    preview transforms before giving up.
    """
    text = str(url or "").strip()
    if not text:
        return []
    candidates: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    add(text)
    parsed = parse.urlparse(text)
    if "image.civitai" in parsed.netloc.casefold():
        # Common CivitAI forms: /<token>/<uuid>/original=true/file.jpeg
        # and /<token>/<uuid>/width=450/file.jpeg. Some old original=true
        # links 404 while width transforms still serve correctly.
        for width in ("width=450", "width=512", "width=768", "width=1024"):
            if "/original=true/" in text:
                add(text.replace("/original=true/", f"/{width}/"))
            if re.search(r"/width=\d+/", text):
                add(re.sub(r"/width=\d+/", f"/{width}/", text))
        if "/original=true/" not in text:
            add(re.sub(r"/width=\d+/", "/original=true/", text) if re.search(r"/width=\d+/", text) else text)
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
            suffix = _extension_from_url(candidate)
            target = folder / f"civitai_{index:02d}{suffix}"
            try:
                if fetcher:
                    raw = fetcher(candidate)
                else:
                    req = request.Request(
                        candidate,
                        headers={
                            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                            "User-Agent": "Mozilla/5.0 NeoStudio/2.0 LoRAStackPreviewPull",
                            "Referer": "https://civitai.com/",
                        },
                    )
                    with request.urlopen(req, timeout=20) as response:
                        raw = response.read()
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                target.write_bytes(raw)
                saved.append(str(target))
                saved_this = True
                break
            except Exception as exc:  # noqa: BLE001 - try next CivitAI URL variant.
                last_error = exc
        if not saved_this and last_error is not None:
            errors.append(f"{url}: {last_error}")
    return {"ok": not errors or bool(saved), "paths": saved, "errors": errors, "count": len(saved)}


def preview_file_response(path: str, *, root: str | Path | None = None) -> dict[str, Any]:
    target = Path(path)
    suffix = target.suffix.lower()
    ok_suffix = suffix in SUPPORTED_PREVIEW_EXTENSIONS
    if root is not None:
        try:
            data_root = (Path(root) / "neo_data" / "extensions" / "lora_stack").resolve()
            resolved = target.resolve()
            if data_root not in resolved.parents and resolved != data_root:
                return {"ok": False, "path": str(target), "media_type": "", "error": "Preview path is outside LoRA Stack data directory."}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": str(target), "media_type": "", "error": str(exc)}
    return {"ok": ok_suffix and target.exists() and target.is_file(), "path": str(target), "media_type": f"image/{'jpeg' if suffix in {'.jpg', '.jpeg'} else suffix.lstrip('.')}", "error": "" if ok_suffix else "Unsupported preview type."}
