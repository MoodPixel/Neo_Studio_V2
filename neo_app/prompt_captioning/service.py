from __future__ import annotations

import csv
import json
import shutil
import threading
from pathlib import Path

from typing import Any
from uuid import uuid4
import base64
import mimetypes
from pathlib import Path

from neo_app.providers.profiles import get_backend_profile_for_live_task, get_backend_profile_payload
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot

from .providers_koboldcpp import run_chat
from .storage import (
    append_prompt_history,
    list_prompt_records,
    save_prompt_record,
    append_caption_history,
    list_caption_records,
    save_caption_record,
    append_handoff_history,
    list_handoff_history,
    append_result_metadata,
    list_result_metadata,
    get_result_metadata,
    build_replay_payload_from_metadata,
    list_categories as storage_list_categories,
    save_category as storage_save_category,
)
from .validation import validate_route_payload
from .batch_safety import batch_dataset_safety, normalize_transfer_mode
from .provider_errors import provider_error_response
from .execution import (
    clamp_generation_params,
    execution_metadata,
    profile_flags,
    profile_gate,
    profile_status,
    prompt_captioning_profiles,
    resolve_backend_profile,
    validate_image_asset,
)

from .metadata import (
    build_handoff_metadata,
    build_result_metadata,
    normalize_route,
)


def _pc_runtime_summary(kind: str, payload: dict[str, Any] | None = None, *, result: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    assets = payload.get("assets") if isinstance(payload.get("assets"), dict) else {}
    source_text = str(inputs.get("source_text") or inputs.get("idea") or inputs.get("prompt") or inputs.get("text") or "")
    summary: dict[str, Any] = {
        "schema_id": "neo.prompt_captioning.runtime_log.summary.pass_m.v1",
        "kind": kind,
        "tool_id": payload.get("tool") or payload.get("tool_id") or result.get("tool_id") or "",
        "mode": payload.get("mode") or "",
        "surface_id": payload.get("surface_id") or payload.get("workspace") or "prompt_captioning",
        "source_text_char_count": len(source_text),
        "input_keys": sorted(str(key) for key in inputs.keys())[:80],
        "param_keys": sorted(str(key) for key in params.keys())[:80],
        "asset_keys": sorted(str(key) for key in assets.keys())[:80],
        "backend_profile_id": result.get("backend_profile_id") or ((payload.get("metadata") or {}).get("backend_profile_id") if isinstance(payload.get("metadata"), dict) else "") or "",
        "provider_id": result.get("provider_id") or "",
        "model": result.get("model") or "",
        "ok": result.get("ok") if "ok" in result else None,
        "metadata_id": metadata.get("metadata_id") or result.get("metadata_id") or "",
        "output_char_count": len(str(result.get("prompt") or result.get("output_prompt") or result.get("caption") or result.get("text") or "")),
        "warning": str(result.get("warning") or "")[:500],
        "error": str(result.get("error") or "")[:500],
    }
    if extra:
        summary.update(extra)
    return summary


def _pc_log_event(event: str, *, run_id: str, payload: dict[str, Any] | None = None, level: str = "INFO", snapshot_name: str | None = None) -> None:
    try:
        log_surface_event("prompt_captioning", event, run_id=run_id, level=level, payload=payload or {})
        if snapshot_name:
            record_surface_snapshot("prompt_captioning", snapshot_name, payload or {}, run_id=run_id)
    except Exception:
        pass


def _pc_log_error(message: str, *, run_id: str, payload: dict[str, Any] | None = None, exc: BaseException | None = None) -> None:
    try:
        record_surface_error("prompt_captioning", message, exc=exc, payload=payload or {}, run_id=run_id)
    except Exception:
        pass


def _select_profile(payload: dict[str, Any], backend_payload: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = str((payload.get("metadata") or {}).get("backend_profile_id") or "").strip()
    profiles = backend_payload.get("profiles") or []
    if requested_id:
        match = next((profile for profile in profiles if profile.get("profile_id") == requested_id), None)
        if match:
            return match
    defaults = backend_payload.get("defaults") or {}
    wanted = defaults.get("prompt_captioning") or defaults.get("text")
    if wanted:
        match = next((profile for profile in profiles if profile.get("profile_id") == wanted), None)
        if match:
            return match
    return next((profile for profile in profiles if profile.get("surface") in {"prompt_captioning", "text"}), None)


def _payload_backend_profile_id(payload: dict[str, Any] | None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(metadata.get("backend_profile_id") or payload.get("profile_id") or payload.get("backend_profile_id") or "").strip()


def _task_backend_payload(payload: dict[str, Any] | None, task_profile: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a backend catalog with the selected task profile live-injected.

    Backend profile listings intentionally remain passive when local auto-connect
    is disabled. Prompt/Captioning execution must use the profile that was
    explicitly task-gated by the route, otherwise the service can re-read the
    passive ``disconnected`` listing and reject an already connected backend.
    """
    payload = payload if isinstance(payload, dict) else {}
    backend_payload = get_backend_profile_payload()
    live_profile = task_profile if isinstance(task_profile, dict) else None
    profile_id = _payload_backend_profile_id(payload)
    if live_profile is None:
        defaults = backend_payload.get("defaults") if isinstance(backend_payload.get("defaults"), dict) else {}
        profile_id = profile_id or str(defaults.get("prompt_captioning") or defaults.get("text") or "").strip()
        if profile_id:
            live_profile = get_backend_profile_for_live_task(profile_id)
    if not isinstance(live_profile, dict) or not live_profile.get("profile_id"):
        return backend_payload, None

    live_id = str(live_profile.get("profile_id") or "").strip()
    profiles = list(backend_payload.get("profiles") or [])
    replaced = False
    for index, profile in enumerate(profiles):
        if isinstance(profile, dict) and str(profile.get("profile_id") or "").strip() == live_id:
            profiles[index] = live_profile
            replaced = True
            break
    if not replaced:
        profiles.append(live_profile)
    return {**backend_payload, "profiles": profiles}, live_profile


def _source_text(payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") or {}
    for key in ("source_text", "idea", "prompt", "text"):
        value = str(inputs.get(key) or "").strip()
        if value:
            return value
    return ""


def _system_prompt(tool_id: str) -> str:
    base = (
        "You are Neo Studio Prompt Studio. Return only the requested prompt text. "
        "No markdown fences, no commentary. Preserve the user's exact subject, identity terms, clothing, mood, and scene. "
        "Do not introduce unrelated locations, creatures, characters, genres, narrative titles, or story elements that are not implied by the source. If the source is a short fashion/editorial idea, expand it as a fashion/editorial prompt, not as a story scene."
    )
    if tool_id == "negative_prompt":
        return base + " Create concise Stable Diffusion negative prompt tags only."
    if tool_id == "prompt_cleanup":
        return base + " Clean duplicate/conflicting prompt tokens while preserving the user's intent."
    if tool_id == "prompt_enhance":
        return base + " Enhance the source with useful visual details for image generation without changing the subject."
    if tool_id == "prompt_rewrite":
        return base + " Rewrite the prompt cleanly while preserving meaning and all core subject terms."
    if tool_id == "text_transform":
        return base + " Transform the text according to the custom instruction without replacing the subject."
    return base + " Generate a polished image-generation prompt anchored to the source."


def _user_prompt(tool_id: str, payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") or {}
    params = payload.get("params") or {}
    source = _source_text(payload)
    style = str(inputs.get("style") or inputs.get("prompt_style") or "").strip()
    custom = str(inputs.get("custom_instructions") or "").strip()
    mode_note = str(params.get("enhance_mode") or params.get("rewrite_mode") or params.get("cleanup_mode") or "").strip()
    lines = [
        "TASK: Create one image-generation prompt from the SOURCE only.",
        "STRICT RULES:",
        "- Keep the exact subject and core descriptors from SOURCE.",
        "- Do not replace the subject with an unrelated scene.",
        "- Do not add aliens, monsters, fantasy lore, robes, rocky hillsides, grief scenes, landscapes, vehicles, or extra characters unless SOURCE asks for them.",
        "- For fashion/editorial sources, keep it as fashion/editorial photography with styling, lighting, pose, lens, composition, and quality tags.",
        "- Do not output JSON, dictionaries, arrays, keys, labels, or a title before the prompt.",
        "- Output only the final prompt text as one plain text prompt.",
    ]
    if source:
        lines.append(f"SOURCE: {source}")
    else:
        lines.append("SOURCE: Create a detailed cinematic image prompt.")
    if style:
        lines.append(f"STYLE: {style}")
    if mode_note:
        lines.append(f"MODE: {mode_note}")
    if custom:
        lines.append(f"CUSTOM INSTRUCTIONS: {custom}")
    return "\n".join(lines)


_PROMPT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "for", "from", "in", "into", "is", "it", "of", "on", "or", "the", "to", "under", "with", "wearing",
    "prompt", "image", "photo", "photograph", "generate", "create", "style", "editorial",
}


def _source_keywords(text: str) -> set[str]:
    import re
    words = {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or ""))}
    return {word for word in words if word not in _PROMPT_STOPWORDS}


_DRIFT_KEYWORDS = {
    "alien", "aliens", "humanoid", "creature", "creatures", "monster", "monsters", "ethereal",
    "robe", "robes", "landscape", "hillside", "windswept", "rocky", "desolate", "twilight",
    "watcher", "tear", "tears", "fantasy", "dragon", "spaceship", "battlefield", "castle",
    "forest", "forestland", "sci-fi", "scifi", "sorrow", "grief", "melancholic", "mystery",
}


def _output_core_text(source: str, output: str) -> str:
    """Return the generated body after stripping an echoed source line/prefix.

    Some local text models echo the SOURCE first and then continue into an unrelated
    scene. Checking overlap against the full output lets that bad response pass, so
    the guard evaluates the body after the echoed source too.
    """
    text = str(output or "").strip()
    src = str(source or "").strip()
    if not text or not src:
        return text
    lowered = text.lower()
    src_lower = src.lower().strip(" \\\"'")
    if lowered.startswith(src_lower):
        return text[len(src):].lstrip(" .,:;—–-\n\t\r\"'")
    lines = text.splitlines()
    if lines and lines[0].strip().lower().strip(" \\\"'") == src_lower:
        return "\n".join(lines[1:]).strip()
    return text


def _contains_unrequested_drift(source: str, output: str) -> bool:
    source_words = _source_keywords(source)
    output_words = _source_keywords(output)
    if not output_words:
        return False
    drift_hits = {word for word in output_words if word in _DRIFT_KEYWORDS and word not in source_words}
    return bool(drift_hits)


def _output_preserves_source(source: str, output: str) -> bool:
    keywords = _source_keywords(source)
    if not keywords:
        return True
    lowered = str(output or "").lower()
    matches = sum(1 for word in keywords if word in lowered)
    required = 1 if len(keywords) <= 2 else 2
    if matches < required:
        return False
    core = _output_core_text(source, output)
    core_lowered = core.lower()
    core_matches = sum(1 for word in keywords if word in core_lowered)
    # If the model only echoed the source and then continued into an unrelated body, reject it.
    if len(core.strip()) > 80 and core_matches == 0:
        return False
    if _contains_unrequested_drift(source, core):
        return False
    return True


def _anchored_prompt_fallback(source: str, style: str = "", custom: str = "") -> str:
    parts = [str(source or "").strip()]
    if style:
        parts.append(f"{style} style")
    parts.extend([
        "professional editorial photography",
        "clean composition",
        "natural confident pose",
        "soft studio lighting",
        "high detail",
        "sharp focus",
        "tasteful styling",
    ])
    if custom:
        parts.append(str(custom).strip())
    return ", ".join(dict.fromkeys(part for part in parts if part))


def _guard_prompt_output(tool_id: str, clean_payload: dict[str, Any], output_text: str) -> tuple[str, str]:
    if tool_id not in {"prompt_generate", "prompt_enhance", "prompt_rewrite", "text_transform"}:
        return output_text, ""
    source = _source_text(clean_payload)
    if not source or _output_preserves_source(source, output_text):
        return output_text, ""
    inputs = clean_payload.get("inputs") or {}
    fallback = _anchored_prompt_fallback(
        source,
        str(inputs.get("style") or inputs.get("prompt_style") or "").strip(),
        str(inputs.get("custom_instructions") or "").strip(),
    )
    return fallback, "Provider output did not preserve the source idea, so Neo replaced it with an anchored prompt fallback."


def _text_from_candidate(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("prompt", "output_prompt", "output_text", "caption", "output_caption", "text", "content", "message", "partial_text"):
            text = _text_from_candidate(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        for item in value:
            text = _text_from_candidate(item)
            if text:
                return text
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        nested = _text_from_candidate(parsed)
        return nested or text
    return text


def _provider_text(result: dict[str, Any]) -> str:
    for key in ("prompt", "output_prompt", "output_text", "caption", "output_caption", "text", "result", "partial_text"):
        if key in result:
            value = _text_from_candidate(result.get(key))
            if value:
                return value
    return ""


def _provider_error_payload(result: dict[str, Any], fallback: str, *, operation: str = "prompt", profile: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _provider_text(result)
    if text:
        normalized = provider_error_response(result, operation=operation, profile=profile, fallback=fallback)
        return {
            "ok": True,
            "recoverable": bool(result.get("recoverable") or normalized.get("recoverable")),
            "partial_text": text,
            "text": text,
            "warning": result.get("warning") or normalized.get("warning") or "",
            "provider_error": normalized.get("provider_error") or {},
            "error_type": normalized.get("error_type") or result.get("error_type") or "",
            "finish_reason": result.get("finish_reason") or "",
            "errors": [],
        }
    return provider_error_response(result, operation=operation, profile=profile, fallback=fallback)


def _prompt_output_aliases(output_text: str, result: dict[str, Any], resolved_key: str = "text") -> dict[str, Any]:
    """Return every safe alias the Prompt Studio frontend may consume."""
    text = str(output_text or "").strip()
    partial = str(result.get("partial_text") or text or "").strip()
    keys = [key for key in ("prompt", "output_prompt", "output_text", "text", "result", "partial_text") if key in result or (key != "result" and text)]
    return {
        "prompt": text,
        "text": text,
        "output_text": text,
        "output_prompt": text,
        "result": text,
        "partial_text": partial,
        "response_debug": {
            "response_keys": sorted([str(key) for key in result.keys()]),
            "returned_aliases": keys,
            "resolved_output_key": resolved_key,
            "resolved_output_length": len(text),
        },
    }


def _resolved_provider_key(result: dict[str, Any]) -> str:
    for key in ("prompt", "output_prompt", "output_text", "text", "result", "partial_text"):
        if str(result.get(key) or "").strip():
            return key
    return ""


def run_prompt_tool(payload: dict[str, Any], *, task_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    run_id = f"prompt_{uuid4().hex[:12]}"
    _pc_log_event("prompt.generate.started", run_id=run_id, payload=_pc_runtime_summary("prompt", payload))
    backend_payload, _live_profile = _task_backend_payload(payload, task_profile)
    validation = validate_route_payload(payload, backend_payload)
    if not validation.get("ok"):
        summary = _pc_runtime_summary("prompt", payload, result={"ok": False, "error": validation.get("state") or "validation_failed"})
        _pc_log_error("Prompt generation validation failed.", run_id=run_id, payload=summary)
        return {"ok": False, "route_state": validation.get("state"), **validation}
    clean_payload = validation["payload"]
    tool_id = clean_payload.get("tool") or "prompt_generate"
    profile = resolve_backend_profile(clean_payload, backend_payload, require="text")
    allowed, gate_reason = profile_gate(profile, require="text")
    if not allowed:
        summary = _pc_runtime_summary("prompt", clean_payload, result={"ok": False, "error": gate_reason})
        _pc_log_error("Prompt generation provider gated.", run_id=run_id, payload=summary)
        return {"ok": False, "errors": [gate_reason], "payload": clean_payload, "route_state": "provider_gated"}
    messages = [
        {"role": "system", "content": _system_prompt(tool_id)},
        {"role": "user", "content": _user_prompt(tool_id, clean_payload)},
    ]
    clean_params = clamp_generation_params(clean_payload.get("params") or {}, profile.get("generation_defaults") or {})
    result = run_chat(profile, messages, clean_params)
    resolved_key = _resolved_provider_key(result)
    output_text = _provider_text(result)
    output_text, guard_warning = _guard_prompt_output(tool_id, clean_payload, output_text)
    if guard_warning:
        result["warning"] = " ".join([str(result.get("warning") or "").strip(), guard_warning]).strip()
    if not result.get("ok") and not output_text:
        error_payload = _provider_error_payload(result, "Prompt generation failed.", operation="prompt", profile=profile)
        summary = _pc_runtime_summary("prompt", clean_payload, result={**result, **error_payload, "ok": False})
        _pc_log_error("Prompt generation failed.", run_id=run_id, payload=summary)
        return {"ok": False, **error_payload, "payload": clean_payload, "provider": profile.get("provider_id")}
    route = normalize_route(
        provider_id=str(profile.get("provider_id") or ""),
        backend_profile_id=str(profile.get("profile_id") or ""),
        model=str(result.get("model") or profile.get("model") or ""),
        route_state="available",
    )
    aliases = _prompt_output_aliases(output_text, result, resolved_key or "text")
    outputs = {
        **aliases,
        "finish_reason": result.get("finish_reason") or "",
        "warning": result.get("warning") or "",
        "recoverable": bool(result.get("recoverable")),
    }
    result_metadata = build_result_metadata(
        tool_id=tool_id,
        mode="prompt_builder",
        payload=clean_payload,
        outputs=outputs,
        route=route,
        workflow_summary=f"Ran Prompt Studio tool {tool_id} with a text backend profile.",
        event_type=f"prompt_captioning.prompt.{tool_id}",
    )
    stored_metadata = append_result_metadata(result_metadata)
    history = append_prompt_history({
        "tool_id": tool_id,
        "source_text": _source_text(clean_payload),
        "output_text": output_text,
        "output_prompt": output_text,
        "prompt": output_text,
        "text": output_text,
        "finish_reason": result.get("finish_reason") or "",
        "partial_text": result.get("partial_text") or output_text,
        "recoverable": bool(result.get("recoverable")),
        "warning": result.get("warning") or "",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "payload": clean_payload,
        "metadata_id": stored_metadata.get("metadata_id"),
        "result_metadata": stored_metadata,
        "response_debug": aliases.get("response_debug") or {},
    })
    response = {
        "ok": True,
        "tool_id": tool_id,
        **aliases,
        "recoverable": bool(result.get("recoverable")),
        "warning": result.get("warning") or "",
        "provider_error": result.get("error") or "",
        "error_type": result.get("error_type") or "",
        "finish_reason": result.get("finish_reason") or "",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": result.get("model") or "",
        "payload": clean_payload,
        "stripped_fields": validation.get("stripped_fields", []),
        "reasoning_stripped": bool(result.get("reasoning_stripped", False)),
        "execution_metadata": execution_metadata(profile, clean_payload),
        "history": history,
        "metadata": stored_metadata,
        "replay_payload": stored_metadata.get("replay_payload") or {},
    }
    _pc_log_event("prompt.generate.completed", run_id=run_id, payload=_pc_runtime_summary("prompt", clean_payload, result=response, metadata=stored_metadata), snapshot_name="neo_last_prompt_payload")
    return response


def backend_execution_status() -> dict[str, Any]:
    backend_payload = get_backend_profile_payload()
    profiles = prompt_captioning_profiles(backend_payload)
    records = []
    for profile in profiles:
        flags = profile_flags(profile)
        records.append({
            "profile_id": profile.get("profile_id") or "",
            "provider_id": profile.get("provider_id") or "",
            "surface": profile.get("surface") or "",
            "status": profile_status(profile),
            "enabled": profile.get("enabled") is not False,
            "supports_text": flags["supports_text"],
            "supports_vision": flags["supports_vision"],
            "supports_captioning": flags["supports_captioning"],
            "text_gate": profile_gate(profile, require="text")[1] if not profile_gate(profile, require="text")[0] else "",
            "caption_gate": profile_gate(profile, require="caption")[1] if not profile_gate(profile, require="caption")[0] else "",
        })
    return {"ok": True, "profiles": records, "count": len(records), "defaults": backend_payload.get("defaults") or {}}


def save_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    return save_prompt_record(payload)


def prompt_records() -> dict[str, Any]:
    return list_prompt_records()

from .storage import (
    append_caption_batch_result,
    delete_caption_component,
    delete_caption_preset,
    delete_caption_record,
    delete_prompt_preset,
    delete_prompt_record,
    duplicate_caption_component,
    duplicate_caption_preset,
    duplicate_caption_record,
    duplicate_prompt_preset,
    duplicate_prompt_record,
    list_caption_batch_results,
    list_caption_components,
    list_caption_history,
    list_caption_presets,
    list_character_records,
    list_prompt_history,
    list_prompt_presets,
    save_caption_component,
    save_caption_preset,
    save_character_record,
    save_prompt_preset,
    toggle_caption_preset_favorite,
    toggle_prompt_preset_favorite,
)


def prompt_history(limit: int = 25) -> dict[str, Any]:
    return list_prompt_history(limit)


def prompt_presets(query: str = "", category: str = "") -> dict[str, Any]:
    return list_prompt_presets(query, category)


def save_preset(payload: dict[str, Any]) -> dict[str, Any]:
    return save_prompt_preset(payload)


def delete_preset(preset_id: str) -> dict[str, Any]:
    return delete_prompt_preset(preset_id)


def duplicate_preset(preset_id: str) -> dict[str, Any]:
    return duplicate_prompt_preset(preset_id)


def toggle_preset_favorite(preset_id: str) -> dict[str, Any]:
    return toggle_prompt_preset_favorite(preset_id)


def character_records(query: str = "") -> dict[str, Any]:
    return list_character_records(query)


def save_character(payload: dict[str, Any]) -> dict[str, Any]:
    return save_character_record(payload)


def delete_saved_prompt(prompt_id: str) -> dict[str, Any]:
    return delete_prompt_record(prompt_id)


def duplicate_saved_prompt(prompt_id: str) -> dict[str, Any]:
    return duplicate_prompt_record(prompt_id)



def _select_caption_profile(payload: dict[str, Any], backend_payload: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = str((payload.get("metadata") or {}).get("backend_profile_id") or "").strip()
    profiles = backend_payload.get("profiles") or []
    if requested_id:
        match = next((profile for profile in profiles if profile.get("profile_id") == requested_id), None)
        if match:
            return match
    for profile in profiles:
        flags = profile.get("capability_flags") or {}
        runtime_flags = (profile.get("runtime") or {}).get("capabilities") or {}
        runtime_supports_vision = bool(runtime_flags.get("runtime_supports_vision", runtime_flags.get("supports_vision", False)))
        runtime_supports_captioning = bool(runtime_flags.get("runtime_supports_captioning", runtime_flags.get("supports_captioning", False)))
        supports_vision = bool(flags.get("supports_vision", False)) or runtime_supports_vision
        supports_captioning = bool(flags.get("supports_captioning", False)) or runtime_supports_captioning or runtime_supports_vision
        if profile.get("enabled") is not False and supports_vision and supports_captioning:
            return profile
    return None


def _asset_path(clean_payload: dict[str, Any]) -> str:
    assets = clean_payload.get("assets") or {}
    for key in ("image", "source_image", "result_image"):
        value = str(assets.get(key) or "").strip()
        if value:
            return value
    images = assets.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return str(first.get("asset_ref") or first.get("path") or first.get("image") or first.get("source_image") or "").strip()
        return str(first or "").strip()
    return ""


def _image_content(asset_path: str) -> tuple[str, str] | tuple[None, None]:
    if not asset_path:
        return None, None
    path = Path(asset_path)
    if not path.exists() or not path.is_file():
        return None, None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}", mime



def _caption_output_aliases(output_text: str, result: dict[str, Any], resolved_key: str = "text") -> dict[str, Any]:
    """Return every safe alias the Caption Studio frontend may consume."""
    text = str(output_text or "").strip()
    partial = str(result.get("partial_text") or text or "").strip()
    keys = [key for key in ("caption", "output_caption", "output_text", "text", "result", "partial_text") if key in result or (key not in {"result"} and text)]
    return {
        "caption": text,
        "text": text,
        "output_caption": text,
        "output_text": text,
        "result": text,
        "partial_text": partial,
        "response_debug": {
            "response_keys": sorted([str(key) for key in result.keys()]),
            "returned_aliases": keys,
            "resolved_output_key": resolved_key,
            "resolved_output_length": len(text),
        },
    }


def _resolved_caption_provider_key(result: dict[str, Any]) -> str:
    for key in ("caption", "output_caption", "output_text", "text", "result", "partial_text"):
        if str(result.get(key) or "").strip():
            return key
    return ""

def _caption_instruction(payload: dict[str, Any]) -> str:
    inputs = payload.get("inputs") or {}
    params = payload.get("params") or {}
    lines = ["Caption this image for creative prompt reuse."]
    instruction = str(inputs.get("caption_instruction") or "").strip()
    if instruction:
        lines.append(f"User instruction: {instruction}")
    for label, key in (("Caption style", "caption_style"), ("Caption length", "caption_length"), ("Output style", "output_style")):
        value = str(inputs.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    for label, key in (("Caption mode", "caption_mode"), ("Component type", "component_type"), ("Detail level", "detail_level")):
        value = str(params.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    lines.append("Return only the caption text. No markdown fences, no commentary.")
    return "\n".join(lines)


def run_caption_tool(payload: dict[str, Any], *, task_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    run_id = f"caption_{uuid4().hex[:12]}"
    _pc_log_event("caption.generate.started", run_id=run_id, payload=_pc_runtime_summary("caption", payload))
    backend_payload, _live_profile = _task_backend_payload(payload, task_profile)
    validation = validate_route_payload(payload, backend_payload)
    if not validation.get("ok"):
        summary = _pc_runtime_summary("caption", payload, result={"ok": False, "error": validation.get("state") or "validation_failed"})
        _pc_log_error("Caption generation validation failed.", run_id=run_id, payload=summary)
        return {"ok": False, "route_state": validation.get("state"), **validation}
    clean_payload = validation["payload"]
    tool_id = clean_payload.get("tool") or "image_captioning"
    profile = resolve_backend_profile(clean_payload, backend_payload, require="caption")
    allowed, gate_reason = profile_gate(profile, require="caption")
    if not allowed:
        if not profile:
            gate_reason = "No vision-capable Caption Backend Profile configured."
        summary = _pc_runtime_summary("caption", clean_payload, result={"ok": False, "error": gate_reason})
        _pc_log_error("Caption generation provider gated.", run_id=run_id, payload=summary)
        return {"ok": False, "errors": [gate_reason], "payload": clean_payload, "route_state": "provider_gated"}
    image_path = _asset_path(clean_payload)
    asset_check = validate_image_asset(image_path)
    if not asset_check.get("ok"):
        summary = _pc_runtime_summary("caption", clean_payload, result={"ok": False, "error": asset_check.get("error") or "asset_unreadable"})
        _pc_log_error("Caption generation asset validation failed.", run_id=run_id, payload=summary)
        return {"ok": False, "errors": [asset_check.get("error") or "Captioning requires a readable image asset."], "payload": clean_payload, "asset_validation": asset_check}
    image_url, mime = _image_content(str(asset_check["path"]))
    if not image_url:
        summary = _pc_runtime_summary("caption", clean_payload, result={"ok": False, "error": "asset_content_unreadable"})
        _pc_log_error("Caption generation image content failed.", run_id=run_id, payload=summary)
        return {"ok": False, "errors": ["Captioning requires a readable image asset."], "payload": clean_payload}
    messages = [
        {"role": "system", "content": "You are Neo Studio Caption Studio. Produce concise, useful visual captions for prompt reuse."},
        {"role": "user", "content": [
            {"type": "text", "text": _caption_instruction(clean_payload)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]
    clean_params = clamp_generation_params(clean_payload.get("params") or {}, profile.get("generation_defaults") or {})
    result = run_chat(profile, messages, clean_params)
    resolved_key = _resolved_caption_provider_key(result)
    caption = _provider_text(result)
    if not result.get("ok") and not caption:
        error_payload = _provider_error_payload(result, "Caption generation failed.", operation="caption", profile=profile)
        summary = _pc_runtime_summary("caption", clean_payload, result={**result, **error_payload, "ok": False})
        _pc_log_error("Caption generation failed.", run_id=run_id, payload=summary)
        return {"ok": False, **error_payload, "payload": clean_payload, "provider": profile.get("provider_id")}
    source_image = str(asset_check.get("path") or image_path)
    route = normalize_route(
        provider_id=str(profile.get("provider_id") or ""),
        backend_profile_id=str(profile.get("profile_id") or ""),
        model=str(result.get("model") or profile.get("model") or ""),
        route_state="available",
    )
    aliases = _caption_output_aliases(caption, result, resolved_key or "text")
    outputs = {
        **aliases,
        "finish_reason": result.get("finish_reason") or "",
        "warning": result.get("warning") or "",
        "recoverable": bool(result.get("recoverable")),
    }
    result_metadata = build_result_metadata(
        tool_id=tool_id,
        mode="captioning",
        payload=clean_payload,
        outputs=outputs,
        route=route,
        assets={"image": source_image, "mime_type": mime or ""},
        workflow_summary=f"Ran Caption Studio tool {tool_id} on one image asset.",
        event_type=f"prompt_captioning.caption.{tool_id}",
    )
    stored_metadata = append_result_metadata(result_metadata)
    history = append_caption_history({
        "tool_id": tool_id,
        "caption": caption,
        "source_image": source_image,
        "finish_reason": result.get("finish_reason") or "",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "payload": clean_payload,
        "metadata_id": stored_metadata.get("metadata_id"),
        "result_metadata": stored_metadata,
    })
    response = {
        "ok": True,
        "tool_id": tool_id,
        "caption": caption,
        "output_caption": caption,
        "text": caption,
        "partial_text": result.get("partial_text") or caption,
        "recoverable": bool(result.get("recoverable")),
        "warning": result.get("warning") or "",
        "provider_error": result.get("error") or "",
        "error_type": result.get("error_type") or "",
        "source_image": str(asset_check.get("path") or image_path),
        "mime_type": mime or "",
        "finish_reason": result.get("finish_reason") or "",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": result.get("model") or "",
        "payload": clean_payload,
        "stripped_fields": validation.get("stripped_fields", []),
        "reasoning_stripped": bool(result.get("reasoning_stripped", False)),
        "execution_metadata": execution_metadata(profile, clean_payload),
        "history": history,
        "metadata": stored_metadata,
        "replay_payload": stored_metadata.get("replay_payload") or {},
    }
    _pc_log_event("caption.generate.completed", run_id=run_id, payload=_pc_runtime_summary("caption", clean_payload, result=response, metadata=stored_metadata, extra={"source_image": source_image}), snapshot_name="neo_last_caption_payload")
    return response


def save_caption(payload: dict[str, Any]) -> dict[str, Any]:
    return save_caption_record(payload)


def caption_records(query: str = "", category: str = "") -> dict[str, Any]:
    return list_caption_records(query, category)


def caption_history(limit: int = 25) -> dict[str, Any]:
    return list_caption_history(limit)


def caption_presets(query: str = "", category: str = "") -> dict[str, Any]:
    return list_caption_presets(query, category)


def save_caption_preset_record(payload: dict[str, Any]) -> dict[str, Any]:
    return save_caption_preset(payload)


def delete_caption_preset_record(preset_id: str) -> dict[str, Any]:
    return delete_caption_preset(preset_id)


def duplicate_caption_preset_record(preset_id: str) -> dict[str, Any]:
    return duplicate_caption_preset(preset_id)


def toggle_caption_preset_record_favorite(preset_id: str) -> dict[str, Any]:
    return toggle_caption_preset_favorite(preset_id)


def caption_components(query: str = "", component_type: str = "") -> dict[str, Any]:
    return list_caption_components(query, component_type)


def save_caption_component_record(payload: dict[str, Any]) -> dict[str, Any]:
    return save_caption_component(payload)


def delete_caption_component_record(component_id: str) -> dict[str, Any]:
    return delete_caption_component(component_id)


def duplicate_caption_component_record(component_id: str) -> dict[str, Any]:
    return duplicate_caption_component(component_id)


def delete_saved_caption(caption_id: str) -> dict[str, Any]:
    return delete_caption_record(caption_id)


def duplicate_saved_caption(caption_id: str) -> dict[str, Any]:
    return duplicate_caption_record(caption_id)


def caption_batch_results(limit: int = 25) -> dict[str, Any]:
    return list_caption_batch_results(limit)


SUPPORTED_BATCH_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}

CAPTION_BATCH_JOBS: dict[str, dict[str, Any]] = {}
CAPTION_BATCH_JOB_LOCK = threading.RLock()

def _batch_job_update(job_key: str, **updates: Any) -> dict[str, Any]:
    with CAPTION_BATCH_JOB_LOCK:
        job = CAPTION_BATCH_JOBS.get(job_key) or {"job_id": job_key}
        job.update(updates)
        CAPTION_BATCH_JOBS[job_key] = job
        return dict(job)

def _batch_job_get(job_key: str) -> dict[str, Any]:
    with CAPTION_BATCH_JOB_LOCK:
        return dict(CAPTION_BATCH_JOBS.get(job_key) or {})

def _normalize_batch_workflow_mode(value: object) -> str:
    text = str(value or "dataset").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"library", "save_to_library", "save_library"}:
        return "library"
    return "dataset"



def _batch_inputs(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    inputs = payload.get('inputs') if isinstance(payload.get('inputs'), dict) else {}
    params = payload.get('params') if isinstance(payload.get('params'), dict) else {}
    dataset = params.get('dataset') if isinstance(params.get('dataset'), dict) else {}
    library = params.get('library') if isinstance(params.get('library'), dict) else {}
    return inputs, params, dataset, library


def _batch_exts(params: dict[str, Any]) -> set[str]:
    raw = str(params.get('include_exts') or 'png,jpg,jpeg,webp,bmp,gif')
    exts = {('.' + part.strip().lower().lstrip('.')) for part in raw.split(',') if part.strip()}
    return exts & SUPPORTED_BATCH_EXTS or SUPPORTED_BATCH_EXTS


def _scan_batch_folder(folder: str, params: dict[str, Any]) -> list[Path]:
    root = Path(str(folder or '')).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    exts = _batch_exts(params)
    iterator = root.rglob('*') if params.get('recursive') else root.glob('*')
    return sorted([p for p in iterator if p.is_file() and p.suffix.lower() in exts], key=lambda x: str(x).lower())


def _batch_images_from_payload(payload: dict[str, Any], params: dict[str, Any]) -> list[Path]:
    assets = payload.get('assets') if isinstance(payload.get('assets'), dict) else {}
    images = assets.get('images') if isinstance(assets.get('images'), list) else []
    paths: list[Path] = []
    for item in images:
        if isinstance(item, dict):
            value = item.get('asset_ref') or item.get('path') or item.get('image') or item.get('source_image')
        else:
            value = item
        if value:
            p = Path(str(value))
            if p.exists() and p.is_file() and p.suffix.lower() in SUPPORTED_BATCH_EXTS:
                paths.append(p)
    if paths:
        return paths
    inputs = payload.get('inputs') if isinstance(payload.get('inputs'), dict) else {}
    return _scan_batch_folder(str(inputs.get('folder_path') or ''), params)


def caption_batch_preview(payload: dict[str, Any]) -> dict[str, Any]:
    inputs, params, dataset, library = _batch_inputs(payload)
    if (params.get('caption_settings') or {}).get('caption_mode') == 'custom_crop' or params.get('caption_mode') == 'custom_crop':
        return {'ok': False, 'errors': ['Batch captioning does not support Custom crop mode.']}
    files = _batch_images_from_payload(payload, params)
    return {'ok': True, 'count': len(files), 'files': [{'name': p.name, 'path': str(p), 'suffix': p.suffix.lower()} for p in files[:500]]}


def _dataset_name(pattern: str, prefix: str, number: int, padding: int, original: Path) -> str:
    num = str(number).zfill(max(1, int(padding or 4)))
    stem = (pattern or '{prefix}_{num}').replace('{prefix}', prefix or 'character').replace('{num}', num).replace('{index}', num).replace('{name}', original.stem)
    safe = ''.join(ch if ch.isalnum() or ch in '._- ' else '_' for ch in stem).strip() or original.stem
    return safe


def _caption_for_batch_image(image_path: Path, payload: dict[str, Any], caption_images: bool, *, task_profile: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    if not caption_images:
        return '', {'ok': True, 'skipped_caption': True}
    shared = dict(payload)
    inputs = dict(shared.get('inputs') or {})
    params = dict(shared.get('params') or {})
    caption_settings = params.get('caption_settings') if isinstance(params.get('caption_settings'), dict) else {}
    inputs.update({
        'caption_instruction': inputs.get('caption_instruction') or caption_settings.get('instruction') or '',
        'caption_style': caption_settings.get('caption_style') or inputs.get('caption_style') or 'descriptive',
        'caption_length': caption_settings.get('caption_length') or inputs.get('caption_length') or 'medium',
        'output_style': caption_settings.get('output_style') or inputs.get('output_style') or 'auto',
    })
    params.update({k: v for k, v in caption_settings.items() if k in {'temperature','top_p','top_k','max_tokens','caption_mode','component_type','detail_level'}})
    single = {
        'workspace': 'prompt_captioning', 'surface_id': 'prompt_captioning', 'mode': 'captioning',
        'tool': 'image_captioning', 'tool_id': 'image_captioning', 'inputs': inputs, 'params': params,
        'assets': {'image': str(image_path), 'source_image': str(image_path), 'images': [{'asset_ref': str(image_path), 'filename': image_path.name}]},
        'metadata': shared.get('metadata') or {},
    }
    result = run_caption_tool(single, task_profile=task_profile)
    return str(result.get('caption') or result.get('text') or result.get('output_caption') or ''), result


def _write_batch_log(out_dir: Path, rows: list[dict[str, Any]], fmt: str) -> str:
    if fmt == 'none':
        return ''
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == 'json':
        path = out_dir / 'caption_batch_log.json'
        path.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        return str(path)
    path = out_dir / 'caption_batch_log.csv'
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=['source','image','caption','status','error'])
        writer.writeheader(); writer.writerows(rows)
    return str(path)


def _caption_batch_worker(job_id: str, payload: dict[str, Any], *, task_profile: dict[str, Any] | None = None) -> None:
    inputs, params, dataset, library = _batch_inputs(payload)
    workflow = _normalize_batch_workflow_mode(inputs.get('workflow_mode') or 'dataset')
    caption_mode = (params.get('caption_settings') or {}).get('caption_mode') or params.get('caption_mode') or ''
    _batch_job_update(job_id, status='running', message='Batch running.', log=['Batch started.', 'Batch running.'])
    if caption_mode == 'custom_crop':
        _batch_job_update(job_id, status='failed', message='Batch captioning does not support Custom crop mode.', errors=['Batch captioning does not support Custom crop mode.'], error_count=1, log=['Batch failed: custom crop is not supported.'])
        return
    images = _batch_images_from_payload(payload, params)
    total = len(images)
    if not images:
        _batch_job_update(job_id, status='failed', message='No supported images found for batch captioning.', total_items=0, processed=0, saved=0, skipped=0, errors=['No supported images found for batch captioning.'], error_count=1, log=['Batch failed: no supported images found.'])
        return
    _batch_job_update(job_id, total_items=total, current_index=0, processed=0, saved=0, skipped=0, error_count=0, errors=[], results=[], progress_percent=0)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    log_rows: list[dict[str, Any]] = []
    log_lines: list[str] = ['Batch started.', f'Found {total} image(s).']
    saved = 0
    skipped = 0
    log_path = ''
    try:
        if workflow == 'library':
            base = str(library.get('base_name') or 'caption')
            category = str(library.get('new_category') or library.get('category') or 'Uncategorized').strip() or 'Uncategorized'
            storage_save_category({'label': category, 'used_by': ['caption', 'batch']})
            start_no = int(library.get('number_start') or 1)
            for idx, image_path in enumerate(images, start=1):
                _batch_job_update(job_id, current_index=idx, current_file=str(image_path), message=f'Processing {idx} / {total}', log=log_lines[-100:])
                caption, result = _caption_for_batch_image(image_path, payload, True, task_profile=task_profile)
                if not caption:
                    err = '; '.join(result.get('errors') or [result.get('error') or 'Caption failed'])
                    errors.append(f'{image_path.name}: {err}')
                    log_lines.append(f'Error {image_path.name}: {err}')
                else:
                    record = save_caption_record({'name': f'{base}_{start_no + idx - 1}', 'category': category, 'caption': caption, 'source_image': str(image_path), 'source_image_url': '', 'settings': params, 'origin': 'batch_captioning', 'metadata': {'batch_id': job_id, 'workflow_mode': workflow, 'source_file': str(image_path)}})
                    row = {'file': str(image_path), 'image': str(image_path), 'caption': caption, 'status': 'saved_to_library', 'record': record.get('record')}
                    results.append(row)
                    log_rows.append({'source': str(image_path), 'image': str(image_path), 'caption': caption, 'status': 'saved_to_library', 'error': ''})
                    saved += 1
                    log_lines.append(f'Saved {image_path.name} to library.')
                processed = idx
                _batch_job_update(job_id, processed=processed, saved=saved, skipped=skipped, errors=list(errors), error_count=len(errors), results=list(results), progress_percent=round((processed / total) * 100, 2), log=log_lines[-100:])
        else:
            output_folder = str(dataset.get('output_folder') or '').strip()
            if not output_folder:
                _batch_job_update(job_id, status='failed', message='Dataset Preparation requires an output folder.', errors=['Dataset Preparation requires an output folder.'], error_count=1, log=['Batch failed: missing output folder.'])
                return
            safety = batch_dataset_safety(dataset)
            if not safety.get('ok'):
                _batch_job_update(job_id, status='failed', message='; '.join(safety.get('errors') or ['Batch safety confirmation is required.']), errors=list(safety.get('errors') or []), error_count=len(safety.get('errors') or []), log=['Batch failed: safety confirmation required.'])
                return
            out_dir = Path(output_folder).expanduser(); out_dir.mkdir(parents=True, exist_ok=True)
            prefix = str(dataset.get('prefix') or 'character')
            pattern = str(dataset.get('pattern') or '{prefix}_{num}')
            number = int(dataset.get('number_start') or 1)
            padding = int(dataset.get('number_padding') or 4)
            caption_images = bool(dataset.get('caption_images', True))
            save_txt = bool(dataset.get('save_txt', True))
            rename = bool(dataset.get('rename_images', True))
            transfer = normalize_transfer_mode(dataset.get('transfer_mode') or 'copy')
            skip = bool(dataset.get('skip_processed', True))
            overwrite = bool(dataset.get('overwrite_existing', False))
            for idx, image_path in enumerate(images, start=1):
                _batch_job_update(job_id, current_index=idx, current_file=str(image_path), message=f'Processing {idx} / {total}', log=log_lines[-100:])
                stem = _dataset_name(pattern, prefix, number + idx - 1, padding, image_path) if rename else image_path.stem
                dest_img = out_dir / f'{stem}{image_path.suffix.lower()}'
                dest_txt = out_dir / f'{stem}.txt'
                if skip and dest_img.exists() and (not save_txt or dest_txt.exists()):
                    skipped += 1
                    log_rows.append({'source': str(image_path), 'image': str(dest_img), 'caption': '', 'status': 'skipped_existing', 'error': ''})
                    log_lines.append(f'Skipped existing {image_path.name}.')
                elif dest_img.exists() and not overwrite:
                    err = f'{dest_img.name}: exists and overwrite is off'
                    errors.append(err)
                    log_lines.append(f'Error {err}')
                else:
                    caption, result = _caption_for_batch_image(image_path, payload, caption_images, task_profile=task_profile)
                    if caption_images and not caption:
                        err = '; '.join(result.get('errors') or [result.get('error') or 'Caption failed'])
                        errors.append(f'{image_path.name}: {err}')
                        log_lines.append(f'Error {image_path.name}: {err}')
                    else:
                        if transfer == 'move':
                            shutil.move(str(image_path), str(dest_img))
                        else:
                            shutil.copyfile(image_path, dest_img)
                        if save_txt and caption_images:
                            dest_txt.write_text(caption, encoding='utf-8')
                        row = {'source': str(image_path), 'image': str(dest_img), 'caption': caption, 'txt': str(dest_txt) if save_txt else '', 'status': 'done'}
                        results.append(row)
                        log_rows.append({'source': str(image_path), 'image': str(dest_img), 'caption': caption, 'status': 'done', 'error': ''})
                        saved += 1
                        log_lines.append(f'Wrote {dest_img.name}.')
                processed = idx
                _batch_job_update(job_id, processed=processed, saved=saved, skipped=skipped, errors=list(errors), error_count=len(errors), results=list(results), progress_percent=round((processed / total) * 100, 2), log=log_lines[-100:])
            log_path = _write_batch_log(out_dir, log_rows, str(dataset.get('log_format') or 'csv'))
        final_status = 'completed' if not errors else 'completed_with_errors'
        batch = append_caption_batch_result({'batch_id': job_id, 'workflow_mode': workflow, 'count': total, 'completed': len(results), 'saved': saved, 'skipped': skipped, 'errors': errors, 'results': results, 'payload': payload, 'log_path': log_path, 'status': final_status})
        log_lines.append('Batch completed.' if not errors else 'Batch completed with issues.')
        final_job = _batch_job_update(job_id, ok=not errors, status=final_status, message='Batch completed.' if not errors else 'Batch completed with issues.', batch=batch, records=list_caption_batch_results().get('records', []), categories=storage_list_categories().get('categories', []), log=log_lines[-100:], current_index=total, processed=total, saved=saved, skipped=skipped, errors=list(errors), error_count=len(errors), results=list(results), progress_percent=100)
        _pc_log_event("batch_caption.completed", run_id=job_id, level="INFO" if not errors else "WARNING", payload=_pc_runtime_summary("batch_caption", payload, result=final_job, extra={"saved": saved, "skipped": skipped, "error_count": len(errors), "log_path": log_path}), snapshot_name="neo_last_caption_payload")
    except Exception as exc:  # noqa: BLE001 - batch worker must never die silently
        errors.append(str(exc))
        log_lines.append(f'Batch failed: {exc}')
        failed_job = _batch_job_update(job_id, ok=False, status='failed', message=str(exc), errors=list(errors), error_count=len(errors), log=log_lines[-100:])
        _pc_log_error("Batch captioning failed.", run_id=job_id, payload=_pc_runtime_summary("batch_caption", payload, result=failed_job, extra={"error_count": len(errors)}), exc=exc)


def run_caption_batch(payload: dict[str, Any], *, task_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    batch_log_run_id = f"caption_batch_{uuid4().hex[:12]}"
    _pc_log_event("batch_caption.started", run_id=batch_log_run_id, payload=_pc_runtime_summary("batch_caption", payload))
    inputs, params, dataset, library = _batch_inputs(payload)
    workflow = _normalize_batch_workflow_mode(inputs.get('workflow_mode') or 'dataset')
    caption_mode = (params.get('caption_settings') or {}).get('caption_mode') or params.get('caption_mode') or ''
    if caption_mode == 'custom_crop':
        return {'ok': False, 'errors': ['Batch captioning does not support Custom crop mode.'], 'status': 'failed', 'message': 'Batch captioning does not support Custom crop mode.'}
    images = _batch_images_from_payload(payload, params)
    total = len(images)
    if not images:
        message = 'Batch captioning requires a non-empty image batch.'
        _pc_log_error(message, run_id=batch_log_run_id, payload=_pc_runtime_summary("batch_caption", payload, result={"ok": False, "error": message}))
        return {'ok': False, 'errors': [message], 'status': 'failed', 'message': message, 'results': []}
    if workflow == 'dataset' and not str(dataset.get('output_folder') or '').strip():
        return {'ok': False, 'errors': ['Dataset Preparation requires an output folder.'], 'status': 'failed', 'message': 'Dataset Preparation requires an output folder.'}
    if workflow == 'dataset':
        safety = batch_dataset_safety(dataset)
        if not safety.get('ok'):
            return {'ok': False, 'errors': list(safety.get('errors') or []), 'warnings': list(safety.get('warnings') or []), 'status': 'failed', 'message': '; '.join(safety.get('errors') or ['Batch safety confirmation is required.']), 'batch_safety': safety, 'results': []}
    # Resolve the selected profile once before queueing. The worker receives this
    # live profile explicitly so a batch cannot fall back to the passive profile
    # catalog for each image.
    backend_payload, live_profile = _task_backend_payload(payload, task_profile)
    validation = validate_route_payload({**payload, "metadata": {**(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}), **({"backend_profile_id": live_profile.get("profile_id")} if live_profile else {})}}, backend_payload)
    if not validation.get("ok"):
        return {"ok": False, "errors": list(validation.get("errors") or [validation.get("state") or "Captioning backend validation failed."]), "status": "failed", "message": "; ".join(validation.get("errors") or [validation.get("state") or "Captioning backend validation failed."]), "results": []}
    clean_batch_payload = validation.get("payload") if isinstance(validation.get("payload"), dict) else payload
    profile = resolve_backend_profile(clean_batch_payload, backend_payload, require="caption")
    allowed, gate_reason = profile_gate(profile, require="caption")
    if not allowed:
        return {"ok": False, "errors": [gate_reason], "status": "failed", "message": gate_reason, "results": []}
    payload = clean_batch_payload
    task_profile = profile
    if workflow == 'library':
        category = str(library.get('new_category') or library.get('category') or 'Uncategorized').strip() or 'Uncategorized'
        storage_save_category({'label': category, 'used_by': ['caption', 'batch']})
    job_id = batch_log_run_id
    job = _batch_job_update(
        job_id,
        ok=True,
        job_id=job_id,
        status='queued',
        message='Batch started.',
        total_items=total,
        current_index=0,
        current_file='',
        processed=0,
        saved=0,
        skipped=0,
        errors=[],
        error_count=0,
        progress_percent=0,
        results=[],
        log=['Batch started.', f'Found {total} image(s).'],
        categories=storage_list_categories().get('categories', []),
        payload=payload,
    )
    if workflow == 'dataset' and not bool(dataset.get('caption_images', True)):
        _caption_batch_worker(job_id, payload, task_profile=task_profile)
        result = _batch_job_get(job_id)
        _pc_log_event("batch_caption.completed", run_id=job_id, payload=_pc_runtime_summary("batch_caption", payload, result=result or {}, extra={"total_items": total}), snapshot_name="neo_last_caption_payload")
        return result
    worker = threading.Thread(target=_caption_batch_worker, args=(job_id, payload), kwargs={"task_profile": task_profile}, daemon=True)
    worker.start()
    _pc_log_event("batch_caption.queued", run_id=job_id, payload=_pc_runtime_summary("batch_caption", payload, result=job, extra={"total_items": total}), snapshot_name="neo_last_caption_payload")
    return job

def caption_batch_status(job_id: str = '') -> dict[str, Any]:
    current = _batch_job_get(job_id)
    if current:
        return {'ok': True, **current}
    records = list_caption_batch_results(25).get('records', [])
    current_record = next((r for r in records if r.get('batch_id') == job_id), records[0] if records else {})
    if not current_record:
        return {'ok': True, 'job_id': job_id, 'job': {}, 'status': 'idle', 'message': 'No batch job found.', 'total_items': 0, 'processed': 0, 'saved': 0, 'skipped': 0, 'errors': [], 'error_count': 0, 'progress_percent': 0, 'log': []}
    errors = current_record.get('errors') if isinstance(current_record.get('errors'), list) else []
    results = current_record.get('results') if isinstance(current_record.get('results'), list) else []
    total = int(current_record.get('count') or len(results) + len(errors))
    return {
        'ok': True,
        'job_id': current_record.get('batch_id') or job_id,
        'job': current_record,
        'status': current_record.get('status') or ('completed_with_errors' if errors else 'completed'),
        'message': 'Batch completed.' if not errors else 'Batch completed with issues.',
        'total_items': total,
        'current_index': total,
        'current_file': '',
        'processed': len(results) + len(errors),
        'saved': len(results),
        'errors': errors,
        'error_count': len(errors),
        'skipped': max(0, total - len(results) - len(errors)),
        'progress_percent': 100 if total else 0,
        'results': results,
        'records': records,
        'log': current_record.get('log') or [],
    }

def caption_batch_cancel(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'ok': False,
        'status': 'unsupported',
        'message': 'Batch cancel is not available yet. Run/preview/status/export are the supported batch actions.',
        'errors': ['Batch cancel is not implemented for the current worker.'],
    }


def caption_batch_resume(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'ok': False,
        'status': 'unsupported',
        'message': 'Batch resume is not available yet. Start a new batch after the current job finishes.',
        'errors': ['Batch resume is not implemented for the current worker.'],
    }


def caption_batch_retry_failed(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'ok': False,
        'status': 'unsupported',
        'message': 'Retry failed is not available yet. Rerun the batch with skip existing enabled.',
        'errors': ['Batch retry-failed is not implemented for the current worker.'],
    }


def caption_batch_export_log() -> str:
    records = list_caption_batch_results(100).get('records', [])
    return json.dumps(records, indent=2)

# Phase L — Library / Presets / History / Reuse hardening service helpers
from .storage import (
    clear_library_history,
    duplicate_library_record,
    get_library_record,
    import_library_snapshot,
    library_snapshot,
    list_library_kind,
    update_library_record,
)


def library_record(kind: str, record_id: str) -> dict[str, Any]:
    return get_library_record(kind, record_id)


def library_list(kind: str, query: str = "", category: str = "", limit: int = 200) -> dict[str, Any]:
    return list_library_kind(kind, query, category, limit)


def library_update(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return update_library_record(kind, payload)


def library_duplicate(kind: str, record_id: str) -> dict[str, Any]:
    return duplicate_library_record(kind, record_id)


def library_export_snapshot() -> dict[str, Any]:
    return library_snapshot()


def library_import_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    merge = payload.get("merge", True) is not False
    return import_library_snapshot(payload, merge=merge)


def history_clear(kind: str) -> dict[str, Any]:
    return clear_library_history(kind)


def build_reuse_payload(kind: str, record_id: str, target: str = "prompt_studio", mode: str = "replace") -> dict[str, Any]:
    found = get_library_record(kind, record_id)
    if not found.get("ok"):
        return found
    record = found.get("record") or {}
    target = str(target or "prompt_studio").strip()
    mode = str(mode or "replace").strip()
    text = ""
    assets: dict[str, Any] = {}
    if kind in {"saved_prompts", "prompt_history", "prompt_presets", "characters"}:
        text = str(record.get("prompt") or record.get("output_text") or record.get("default_positive") or record.get("identity_fragments") or "")
    elif kind in {"saved_captions", "caption_history", "caption_components", "caption_presets"}:
        text = str(record.get("caption") or record.get("component_text") or record.get("instruction") or "")
        if record.get("source_image"):
            assets["image"] = record.get("source_image")
        if record.get("source_image_url"):
            assets["image_url"] = record.get("source_image_url")
    reuse = {
        "workspace": "prompt_captioning",
        "source_kind": kind,
        "source_record_id": record_id,
        "target": target,
        "mode": mode,
        "text": text,
        "assets": assets,
        "record": record,
        "metadata": {
            "source_surface": "prompt_captioning",
            "source_kind": kind,
            "source_record_id": record_id,
            "target": target,
            "handoff_mode": mode,
        },
    }
    return {"ok": True, "reuse_payload": reuse}


# Phase M — Cross-tab handoff
ALLOWED_HANDOFF_TARGETS: dict[str, set[str]] = {
    "image": {"positive_prompt", "negative_prompt", "source_image", "source_image_2", "source_image_3"},
    "prompt_builder": {"source_text", "output_text", "negative_output_text", "custom_instructions"},
    "captioning": {"caption_instruction", "output_caption", "selected_image"},
    "clipboard": {"text"},
}
ALLOWED_HANDOFF_MODES = {"append", "replace", "copy"}


def _handoff_text_from_payload(payload: dict[str, Any]) -> str:
    for key in ("text", "prompt", "caption", "output_text", "negative_prompt"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    for key in ("text", "prompt", "caption", "source_text", "caption_instruction"):
        value = str(inputs.get(key) or "").strip()
        if value:
            return value
    return ""


def _handoff_assets_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    assets = payload.get("assets") if isinstance(payload.get("assets"), dict) else {}
    out = dict(assets)
    for key in ("source_image", "source_image_url", "source_image_name", "image", "image_url", "image_name"):
        if payload.get(key):
            out[key] = payload.get(key)
    return out


def build_cross_tab_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    target_workspace = str(payload.get("target_workspace") or payload.get("target") or "image").strip()
    target_field = str(payload.get("target_field") or "positive_prompt").strip()
    mode = str(payload.get("mode") or payload.get("handoff_mode") or "append").strip()
    source_kind = str(payload.get("source_kind") or "").strip()
    source_record_id = str(payload.get("source_record_id") or payload.get("record_id") or payload.get("id") or "").strip()

    if mode not in ALLOWED_HANDOFF_MODES:
        return {"ok": False, "errors": [f"Unsupported handoff mode: {mode}"]}
    if target_workspace not in ALLOWED_HANDOFF_TARGETS:
        return {"ok": False, "errors": [f"Unsupported target workspace: {target_workspace}"]}
    if target_field not in ALLOWED_HANDOFF_TARGETS[target_workspace]:
        return {"ok": False, "errors": [f"Unsupported target field for {target_workspace}: {target_field}"]}

    text = _handoff_text_from_payload(payload)
    assets = _handoff_assets_from_payload(payload)
    record: dict[str, Any] = {}
    if source_kind and source_record_id:
        reuse = build_reuse_payload(source_kind, source_record_id, target_field, mode)
        if not reuse.get("ok"):
            return reuse
        reuse_payload = reuse.get("reuse_payload") or {}
        record = reuse_payload.get("record") or {}
        text = text or str(reuse_payload.get("text") or "")
        assets = {**(reuse_payload.get("assets") or {}), **assets}

    if target_field in {"positive_prompt", "negative_prompt", "source_text", "output_text", "negative_output_text", "custom_instructions", "caption_instruction", "output_caption", "text"} and not text:
        return {"ok": False, "errors": ["Handoff text is empty."]}
    if target_field.startswith("source_image") or target_field == "selected_image":
        image_ref = assets.get("source_image") or assets.get("image") or assets.get("path")
        image_url = assets.get("source_image_url") or assets.get("image_url") or assets.get("url")
        if not image_ref and not image_url:
            return {"ok": False, "errors": ["Handoff image asset is empty."]}

    client_mutation = {
        "target_workspace": target_workspace,
        "target_field": target_field,
        "mode": mode,
        "text": text,
        "assets": assets,
    }
    event = append_handoff_history({
        "source_surface": "prompt_captioning",
        "source_tool": str(payload.get("source_tool") or payload.get("tool") or "library_reuse" if source_kind else payload.get("source_tool") or "direct_handoff"),
        "source_kind": source_kind,
        "source_record_id": source_record_id,
        "target_workspace": target_workspace,
        "target_field": target_field,
        "handoff_mode": mode,
        "text_preview": text[:500],
        "assets": assets,
        "record_name": record.get("name") or "",
    })
    handoff_payload = {
        "workspace": "prompt_captioning",
        "surface_id": "prompt_captioning",
        "source_surface": "prompt_captioning",
        "source_kind": source_kind,
        "source_record_id": source_record_id,
        "target_workspace": target_workspace,
        "target_field": target_field,
        "mode": mode,
        "text": text,
        "assets": assets,
        "metadata": {
            "source_surface": "prompt_captioning",
            "source_tool": str(payload.get("source_tool") or payload.get("tool") or "direct_handoff"),
            "target_workspace": target_workspace,
            "target_field": target_field,
            "handoff_mode": mode,
            "handoff_id": event.get("handoff_id"),
            "timestamp": event.get("created_at"),
        },
        "client_mutation": client_mutation,
    }
    result_metadata = build_handoff_metadata(event, client_mutation)
    stored_metadata = append_result_metadata(result_metadata)
    handoff_payload["metadata_id"] = stored_metadata.get("metadata_id")
    handoff_payload["result_metadata"] = stored_metadata
    handoff_payload["replay_payload"] = stored_metadata.get("replay_payload") or {}
    return {"ok": True, "handoff": handoff_payload, "event": {**event, "metadata_id": stored_metadata.get("metadata_id")}, "metadata": stored_metadata, "replay_payload": stored_metadata.get("replay_payload") or {}}


def handoff_history(limit: int = 50) -> dict[str, Any]:
    return list_handoff_history(limit)


def result_metadata_records(limit: int = 100, tool_id: str = "") -> dict[str, Any]:
    return list_result_metadata(limit, tool_id)


def result_metadata_record(metadata_id: str) -> dict[str, Any]:
    return get_result_metadata(metadata_id)


def replay_payload_for_metadata(metadata_id: str) -> dict[str, Any]:
    return build_replay_payload_from_metadata(metadata_id)



def categories() -> dict[str, Any]:
    return storage_list_categories()


def save_shared_category(payload: dict[str, Any]) -> dict[str, Any]:
    return storage_save_category(payload if isinstance(payload, dict) else {})
