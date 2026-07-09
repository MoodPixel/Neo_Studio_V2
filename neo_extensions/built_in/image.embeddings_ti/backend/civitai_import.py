from __future__ import annotations

from typing import Any

from neo_extensions.built_in.lora_stack.backend.civitai_import import (  # type: ignore
    fetch_civitai_payload,
    import_civitai_into_record,
    normalize_civitai_payload,
    parse_civitai_url,
)

TEXTUAL_INVERSION_TYPES = {"textualinversion", "textual inversion", "embedding", "embeddings"}


def normalize_textual_inversion_civitai_payload(data: dict[str, Any]) -> dict[str, Any]:
    incoming = normalize_civitai_payload(data)
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    model_type = str(model.get("type") or data.get("type") or data.get("modelType") or "").strip()
    trained_words = data.get("trainedWords") or []
    incoming["trigger_words"] = incoming.pop("triggers", []) or trained_words or []
    incoming["default_target"] = "negative_prompt" if any("negative" in str(item).casefold() or "bad" in str(item).casefold() for item in incoming.get("trigger_words") or []) else "positive_prompt"
    incoming["remote_source"] = {**(incoming.get("remote_source") or {}), "model_type": model_type, "provider": "civitai"}
    incoming["field_sources"] = {**(incoming.get("field_sources") or {}), "trigger_words": "remote:civitai", "default_target": "remote:civitai"}
    return incoming


def is_textual_inversion_payload(data: dict[str, Any]) -> bool:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    model_type = str(model.get("type") or data.get("type") or data.get("modelType") or "").strip().casefold()
    if not model_type:
        # Older CivitAI responses do not always include type on model-version payloads.
        return True
    return model_type in TEXTUAL_INVERSION_TYPES
