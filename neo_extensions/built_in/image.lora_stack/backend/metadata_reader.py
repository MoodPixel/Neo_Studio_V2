from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

METADATA_KEYS = {
    "modelspec.tags", "tags", "trainedWords", "activation text", "sd tag", "trigger", "triggers",
    "ss_tag_frequency", "ss_base_model_version", "ss_sd_model_name", "modelspec.title",
    "ss_output_name", "modelspec.description", "description", "modelspec.architecture", "modelspec.implementation",
    "negative", "negative prompt", "negative_prompt", "negativePrompt", "uc", "ss_negative_prompt",
    "ss_tag_frequency_negative", "ss_negative_tag_frequency",
}

# Metadata can expose explicit negative prompt fields, but many LoRA files only
# expose one flat training tag frequency. Keep the default rule honest:
# explicit negative fields win. Then apply a conservative unwanted-token bucket
# for common negative-prompt/artifact/avoidance tags so they do not pollute the
# positive chip list. This is intentionally editable in the UI after load.
NEGATIVE_TOKEN_HINTS = {
    "bad anatomy", "bad hands", "bad face", "bad eyes", "bad proportions",
    "deformed", "mutated", "mutation", "extra fingers", "missing fingers",
    "extra limbs", "missing limbs", "fused fingers", "long neck", "cross-eyed",
    "worst quality", "low quality", "lowres", "jpeg artifacts", "blurry",
    "blur", "grainy", "noise", "watermark", "signature", "logo", "text",
    "username", "artist name", "error", "cropped", "out of frame",
    "duplicate", "ugly", "disfigured", "poorly drawn", "monochrome",
    "uncensored", "nude", "completely nude", "topless male", "nipples",
    "penis", "testicles", "erection", "pussy", "vagina", "anus",
    "tongue out", "explicit", "nsfw", "sex", "cum",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                return _as_list(parsed)
            except Exception:  # noqa: BLE001
                pass
        return [item.strip() for item in re.split(r"[,;\n]", stripped) if item.strip()]
    if isinstance(value, dict):
        return [str(key).strip() for key in value.keys() if str(key).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _clean_prompt_token(value: str) -> str:
    text = str(value or "").strip()
    text = text.strip(" ,;\n\t")
    # Remove common prompt-weight wrappers but keep useful words/phrases.
    text = text.strip("()[]{}")
    text = re.sub(r":\s*[-+]?\d+(?:\.\d+)?$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _looks_like_negative_token(value: str) -> bool:
    token = _clean_prompt_token(value).casefold()
    if not token:
        return False
    if token in NEGATIVE_TOKEN_HINTS:
        return True
    return any(hint in token for hint in (
        "bad ", "poorly ", "extra ", "missing ", "deformed", "mutated",
        "artifact", "watermark", "signature", "low quality", "worst quality",
    ))


def _unique(values: list[str], *, limit: int = 80) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_prompt_token(str(value or ""))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _tokens_from_prompt_text(value: Any, *, limit: int = 80) -> list[str]:
    values: list[str] = []
    for item in _as_list(value):
        for part in re.split(r"[,;\n]+", str(item or "")):
            token = _clean_prompt_token(part)
            if token:
                values.append(token)
    return _unique(values, limit=limit)


def _tags_from_frequency(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(value, dict):
        return []
    scored: list[tuple[str, float]] = []
    for group in value.values():
        if isinstance(group, dict):
            for tag, count in group.items():
                try:
                    score = float(count)
                except (TypeError, ValueError):
                    score = 0.0
                scored.append((str(tag), score))
    return [tag for tag, _score in sorted(scored, key=lambda item: item[1], reverse=True)]


def read_safetensors_metadata(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists() or file_path.suffix.lower() != ".safetensors":
        return {"ok": False, "metadata": {}, "known_keys": {}, "error": "Not a readable .safetensors file."}
    try:
        with file_path.open("rb") as handle:
            raw_len = handle.read(8)
            if len(raw_len) != 8:
                return {"ok": False, "metadata": {}, "known_keys": {}, "error": "Invalid safetensors header."}
            header_len = struct.unpack("<Q", raw_len)[0]
            if header_len <= 0 or header_len > 100_000_000:
                return {"ok": False, "metadata": {}, "known_keys": {}, "error": "Unsafe safetensors header length."}
            header = json.loads(handle.read(header_len).decode("utf-8"))
        metadata = header.get("__metadata__") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return {"ok": True, "metadata": metadata, "known_keys": {key: metadata.get(key) for key in METADATA_KEYS if key in metadata}}
    except Exception as exc:  # noqa: BLE001 - corrupt metadata should not break the library.
        return {"ok": False, "metadata": {}, "known_keys": {}, "error": str(exc)}


def infer_defaults_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    metadata = metadata or {}
    triggers: list[str] = []
    for key in ("trainedWords", "activation text", "trigger", "triggers"):
        triggers.extend(_as_list(metadata.get(key)))
    keywords: list[str] = []
    for key in ("modelspec.tags", "tags", "sd tag"):
        keywords.extend(_as_list(metadata.get(key)))
    keywords.extend(_tags_from_frequency(metadata.get("ss_tag_frequency")))
    negative_keywords: list[str] = []
    for key in ("negative", "negative prompt", "negative_prompt", "negativePrompt", "uc", "ss_negative_prompt"):
        negative_keywords.extend(_tokens_from_prompt_text(metadata.get(key)))
    negative_keywords.extend(_tags_from_frequency(metadata.get("ss_tag_frequency_negative")))
    negative_keywords.extend(_tags_from_frequency(metadata.get("ss_negative_tag_frequency")))
    # If a metadata field is explicitly negative, keep it out of positive keywords.
    # Split explicit negative metadata and conservative negative-looking tags out
    # of the positive lists. Training tags are not always true negatives, but
    # these buckets avoid putting obvious avoid-list tokens into the positive
    # prompt chip group. The user can edit/save the final split.
    inferred_negative = [item for item in keywords if _looks_like_negative_token(item)]
    negative_keywords.extend(inferred_negative)
    neg_keys = {item.casefold() for item in negative_keywords}
    keywords = [item for item in keywords if item.casefold() not in neg_keys]
    triggers = [item for item in triggers if item.casefold() not in neg_keys]
    base_model = metadata.get("ss_base_model_version") or metadata.get("ss_sd_model_name") or metadata.get("modelspec.architecture") or ""
    title = metadata.get("modelspec.title") or metadata.get("ss_output_name") or ""
    description = metadata.get("modelspec.description") or metadata.get("description") or ""
    notes = "\n\n".join(str(item).strip() for item in [title, description] if str(item or "").strip())
    result = {
        "triggers": _unique(triggers),
        "keywords": _unique(keywords),
        "negative_keywords": _unique(negative_keywords),
        "base_model": str(base_model or ""),
        "notes": notes,
        "metadata_status": "readable" if metadata else "none",
        "field_sources": {},
    }
    for field in ("triggers", "keywords", "negative_keywords", "base_model", "notes"):
        if result.get(field):
            result["field_sources"][field] = "local:safetensors"
    return result
