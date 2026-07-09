from __future__ import annotations

import json
import re
from typing import Any
from urllib import parse, request

from .merge_policy import merge_record

CIVITAI_HOSTS = ("civitai.com", "civitai.red")


def parse_civitai_url(url: str) -> dict[str, Any]:
    text = (url or "").strip()
    parsed = parse.urlparse(text)
    host = parsed.netloc.casefold()
    host_ok = any(host.endswith(item) for item in CIVITAI_HOSTS)
    model_id = ""
    version_id = ""
    if match := re.search(r"/models/(\d+)", parsed.path):
        model_id = match.group(1)
    if match := re.search(r"/model-versions/(\d+)", parsed.path):
        version_id = match.group(1)
    if match := re.search(r"/api/download/models/(\d+)", parsed.path):
        version_id = match.group(1)
    query = parse.parse_qs(parsed.query)
    if query.get("modelVersionId"):
        version_id = str(query["modelVersionId"][0])
    return {"ok": bool(host_ok and (model_id or version_id)), "url": text, "host": parsed.netloc or "civitai.com", "model_id": model_id, "version_id": version_id}



def _split_prompt_tokens(value: str, *, limit: int = 80) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n]+", str(value or "")):
        text = re.sub(r"[()\[\]{}]", "", part).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        # Drop obvious weight-only fragments while preserving useful phrases.
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(text)
        if len(tokens) >= limit:
            break
    return tokens


NEGATIVE_TOKEN_HINTS = {
    "bad anatomy", "bad hands", "bad face", "bad eyes", "bad proportions",
    "deformed", "mutated", "extra fingers", "missing fingers", "extra limbs",
    "missing limbs", "worst quality", "low quality", "lowres", "jpeg artifacts",
    "blurry", "watermark", "signature", "logo", "text", "username",
    "uncensored", "nude", "completely nude", "topless male", "nipples",
    "penis", "testicles", "erection", "pussy", "vagina", "anus",
    "tongue out", "explicit", "nsfw", "sex", "cum",
}

def _looks_like_negative_token(value: str) -> bool:
    token = re.sub(r"\s+", " ", str(value or "").strip().strip("()[]{}")).casefold()
    if not token:
        return False
    if token in NEGATIVE_TOKEN_HINTS:
        return True
    return any(hint in token for hint in (
        "bad ", "poorly ", "extra ", "missing ", "deformed", "mutated",
        "artifact", "watermark", "signature", "low quality", "worst quality",
    ))



def _stable_civitai_preview_url(url: str) -> str:
    text = str(url or "").strip()
    if "image.civitai" in text.casefold() and "/original=true/" in text:
        return text.replace("/original=true/", "/width=768/")
    return text

def _image_meta(image: dict[str, Any]) -> dict[str, Any]:
    meta = image.get("meta") or {}
    if isinstance(meta, str):
        try:
            loaded = json.loads(meta)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return meta if isinstance(meta, dict) else {}


def _prompt_from_image(image: dict[str, Any]) -> str:
    meta = _image_meta(image)
    return str(meta.get("prompt") or meta.get("Prompt") or "").strip()


def _description_text(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    return re.sub(r"<[^>]+>", "", text).strip()


def normalize_civitai_payload(data: dict[str, Any]) -> dict[str, Any]:
    data = data or {}
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    images = data.get("images") if isinstance(data.get("images"), list) else []
    prompts = []
    preview_urls = []
    negative_prompts = []
    for image in images:
        if not isinstance(image, dict):
            continue
        if image.get("url"):
            preview_urls.append(_stable_civitai_preview_url(str(image["url"])))
        prompt = _prompt_from_image(image)
        if prompt:
            prompts.append(prompt)
        meta = _image_meta(image)
        negative_prompt = meta.get("negativePrompt") or meta.get("negative prompt") or meta.get("negative_prompt") or meta.get("Negative prompt")
        if negative_prompt:
            negative_prompts.append(str(negative_prompt))
    notes_parts = [_description_text(model.get("description")), _description_text(data.get("description"))]
    negative_keywords = _split_prompt_tokens(negative_prompts[0]) if negative_prompts else []
    if negative_prompts:
        notes_parts.append("Negative prompt: " + negative_prompts[0])
    model_id = data.get("modelId") or model.get("id") or ""
    version_id = data.get("id") or ""
    model_name = model.get("name") or data.get("modelName") or ""
    version_name = data.get("name") or ""
    def _clean_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            return _split_prompt_tokens(value)
        if isinstance(value, list):
            out: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    out.append(str(item.get("name") or item.get("tag") or item.get("label") or "").strip())
                else:
                    out.append(str(item or "").strip())
            return [item for item in out if item]
        return []

    triggers = _clean_tags(data.get("trainedWords") or [])
    raw_keywords = _clean_tags(model.get("tags") or data.get("tags") or [])
    inferred_negative = [item for item in raw_keywords if _looks_like_negative_token(item)]
    negative_keywords = list(dict.fromkeys([*negative_keywords, *inferred_negative]))
    neg_keys = {item.casefold() for item in negative_keywords}
    keywords = [item for item in raw_keywords if item.casefold() not in neg_keys]
    triggers = [item for item in triggers if item.casefold() not in neg_keys]

    return {
        "triggers": triggers,
        "keywords": keywords,
        "negative_keywords": negative_keywords,
        "base_model": data.get("baseModel") or data.get("base_model") or "",
        "example_prompt": prompts[0] if prompts else "",
        "prompt_options": [{"name": f"CivitAI Prompt {index + 1}", "prompt": prompt} for index, prompt in enumerate(prompts)],
        "preview_urls": preview_urls,
        "preview_images": preview_urls,
        "notes": "\n\n".join(part for part in notes_parts if part),
        "remote_source": {
            "provider": "civitai",
            "model_id": str(model_id),
            "version_id": str(version_id),
            "model_name": str(model_name),
            "version_name": str(version_name),
        },
        "field_sources": {
            "triggers": "remote:civitai",
            "keywords": "remote:civitai",
            "negative_keywords": "remote:civitai",
            "base_model": "remote:civitai",
            "example_prompt": "remote:civitai",
            "prompt_options": "remote:civitai",
            "preview_images": "remote:civitai",
            "notes": "remote:civitai",
        },
    }


def fetch_civitai_payload(url: str, *, timeout: float = 12.0, fetcher=None) -> dict[str, Any]:
    parsed = parse_civitai_url(url)
    if not parsed.get("ok"):
        return {"ok": False, "error": "A valid CivitAI model or model-version URL is required.", "parsed": parsed}
    candidates: list[str] = []
    hosts = [parsed.get("host") or "civitai.com"] + [host for host in CIVITAI_HOSTS if host != parsed.get("host")]
    for host in hosts:
        if parsed.get("version_id"):
            candidates.append(f"https://{host}/api/v1/model-versions/{parsed['version_id']}")
        if parsed.get("model_id"):
            candidates.append(f"https://{host}/api/v1/models/{parsed['model_id']}")
    errors: list[str] = []
    for candidate in candidates:
        try:
            if fetcher:
                data = fetcher(candidate)
            else:
                req = request.Request(
                    candidate,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "NeoStudio/2.0 LoRAStackMetadataPull (+https://local.neo-studio)",
                    },
                )
                with request.urlopen(req, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
            if parsed.get("model_id") and not parsed.get("version_id") and isinstance(data, dict) and data.get("modelVersions"):
                versions = data.get("modelVersions") or []
                data = {**(versions[0] if versions and isinstance(versions[0], dict) else {}), "model": data}
            return {"ok": True, "url": candidate, "data": data, "parsed": parsed}
        except Exception as exc:  # noqa: BLE001 - try fallback host/endpoint.
            errors.append(f"{candidate}: {exc}")
    return {"ok": False, "error": "Could not fetch CivitAI metadata.", "errors": errors, "parsed": parsed}


def import_civitai_into_record(existing: dict[str, Any], incoming: dict[str, Any], *, mode: str = "fill_missing", selected_fields: list[str] | None = None) -> dict[str, Any]:
    return merge_record(existing, incoming, mode=mode, selected_fields=selected_fields)
