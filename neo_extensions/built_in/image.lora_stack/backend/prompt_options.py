from __future__ import annotations

from typing import Any


def upsert_prompt_option(record: dict[str, Any], name: str, prompt: str) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return record
    options = record.setdefault("prompt_options", [])
    if not isinstance(options, list):
        options = []
        record["prompt_options"] = options
    name = (name or f"Prompt Option {len(options) + 1}").strip()
    for option in options:
        if isinstance(option, dict) and option.get("name") == name:
            option["prompt"] = prompt
            return record
    options.append({"name": name, "prompt": prompt})
    return record


def add_civitai_prompt_option(record: dict[str, Any], prompt: str, *, name: str = "CivitAI Prompt") -> dict[str, Any]:
    existing = (record.get("example_prompt") or "").strip()
    prompt = (prompt or "").strip()
    if not prompt:
        return record
    if not existing:
        record["example_prompt"] = prompt
        record.setdefault("field_sources", {})["example_prompt"] = "remote:civitai"
        return record
    if existing.casefold() != prompt.casefold():
        return upsert_prompt_option(record, name, prompt)
    return record
