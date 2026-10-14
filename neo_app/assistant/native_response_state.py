from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from neo_app.image.native_intelligence import exact_recipe_field_value, recipe_packet

NATIVE_RESPONSE_STATE_SCHEMA_ID = "neo.assistant.native_response_state.v1"
DEFAULT_EXACT_CHUNK_CHARS = 6000


def _clean(value: Any, limit: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if limit and len(text) > limit:
        return text[:limit]
    return text


def session_native_response_state(session: dict[str, Any] | None) -> dict[str, Any]:
    data = session if isinstance(session, dict) else {}
    state = data.get("native_response_state") if isinstance(data.get("native_response_state"), dict) else {}
    return deepcopy(state)


def clear_native_response_state(session: dict[str, Any]) -> None:
    session["native_response_state"] = {}


def _set_state(session: dict[str, Any], state: dict[str, Any]) -> None:
    session["native_response_state"] = deepcopy(state)


def has_continuable_native_response(session: dict[str, Any] | None) -> bool:
    state = session_native_response_state(session)
    return bool(
        state.get("schema_id") == NATIVE_RESPONSE_STATE_SCHEMA_ID
        and state.get("status") == "continuable"
        and int(state.get("cursor") or 0) < int(state.get("total_chars") or 0)
        and bool(state.get("field_path"))
    )


def _is_prompt_adaptation(text: str) -> bool:
    low = str(text or "").lower()
    adaptation_terms = (
        "adapt", "rewrite", "convert", "optimize", "optimise", "improve", "rework",
        "make a prompt", "write a prompt", "prompt to generate", "prompt for krea", "prompt in krea",
        "for flux", "in flux", "for qwen", "in qwen", "for sdxl", "in sdxl",
    )
    return any(term in low for term in adaptation_terms)


def requested_exact_recipe_field(text: str) -> str:
    """Return an exact historical Recipe field requested for deterministic delivery.

    This deliberately excludes model-adaptation requests. "Give me the prompt for Krea2"
    is a synthesis task; "give me the full original prompt" is an exact-field task.
    """
    low = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not low or _is_prompt_adaptation(low):
        return ""
    if "negative prompt" in low and any(term in low for term in ("full", "exact", "original", "recorded", "used", "give me", "show me")):
        return "prompt.negative"
    prompt_terms = ("prompt", "original prompt", "recorded prompt", "prompt used", "generation prompt")
    if any(term in low for term in prompt_terms):
        exact_markers = ("full", "exact", "original", "recorded", "used to generate", "generated this", "from this image", "for this image", "give me the prompt", "show me the prompt", "just the prompt")
        if any(marker in low for marker in exact_markers):
            return "prompt.positive"
    return ""


def requested_recipe_summary(text: str) -> bool:
    low = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not low or _is_prompt_adaptation(low) or requested_exact_recipe_field(low):
        return False
    setting_terms = (
        "generation settings", "settings used", "settings to generate", "settings generated",
        "parameters used", "generation parameters", "generation info", "generation information",
        "recipe used", "generation recipe", "what settings", "which settings",
    )
    return any(term in low for term in setting_terms)


def _native_context_with_session_referent(native_context: dict[str, Any] | None, session: dict[str, Any] | None) -> dict[str, Any]:
    context = dict(native_context or {})
    refs = (session or {}).get("native_referents") if isinstance((session or {}).get("native_referents"), dict) else {}
    image = refs.get("image") if isinstance(refs.get("image"), dict) else {}
    if image and not context.get("active_image_referent"):
        context["active_image_referent"] = deepcopy(image)
    return context


def _resolution_query(text: str, context: dict[str, Any]) -> str:
    low = str(text or "").lower()
    has_attachment = bool(context.get("attachment_image_intelligence"))
    has_referent = isinstance(context.get("active_image_referent"), dict) and bool(context.get("active_image_referent"))
    explicit_target = bool(re.search(r"\b(?:result|output|file|neoimg)[-_ :#]?[a-z0-9_.-]+\b", low))
    if has_referent and not has_attachment and not explicit_target and not any(term in low for term in ("this image", "that image", "same image", "attached image", " it ", " its ")):
        return f"{text} this image".strip()
    return text


def _recipe(root_dir: Path, text: str, *, native_context: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    context = _native_context_with_session_referent(native_context, session)
    return recipe_packet(Path(root_dir), _resolution_query(text, context), native_context=context)


def _get_path(payload: dict[str, Any], field_path: str) -> Any:
    value: Any = payload
    for part in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _source_ref(packet: dict[str, Any]) -> dict[str, Any]:
    refs = packet.get("target_refs") if isinstance(packet.get("target_refs"), list) else []
    ref = refs[0] if refs and isinstance(refs[0], dict) else {}
    return {key: ref.get(key) for key in ("result_id", "neo_output_id", "file_id", "filename", "kind") if ref.get(key) not in (None, "")}


def _chunk_exact_text(
    session: dict[str, Any],
    *,
    exact_text: str,
    field_path: str,
    source_ref: dict[str, Any],
    offset: int = 0,
    chunk_chars: int = DEFAULT_EXACT_CHUNK_CHARS,
) -> dict[str, Any]:
    text = str(exact_text or "")
    start = max(0, min(int(offset or 0), len(text)))
    end = min(len(text), start + max(1000, int(chunk_chars or DEFAULT_EXACT_CHUNK_CHARS)))
    chunk = text[start:end]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if end < len(text):
        state = {
            "schema_id": NATIVE_RESPONSE_STATE_SCHEMA_ID,
            "scope": "image",
            "operation_id": "image.recipe.get",
            "kind": "exact_field",
            "field_path": field_path,
            "source_ref": deepcopy(source_ref),
            "cursor": end,
            "total_chars": len(text),
            "sha256": digest,
            "status": "continuable",
        }
        _set_state(session, state)
        status = "partial"
    else:
        clear_native_response_state(session)
        state = {
            "schema_id": NATIVE_RESPONSE_STATE_SCHEMA_ID,
            "scope": "image",
            "operation_id": "image.recipe.get",
            "kind": "exact_field",
            "field_path": field_path,
            "source_ref": deepcopy(source_ref),
            "cursor": end,
            "total_chars": len(text),
            "sha256": digest,
            "status": "complete",
        }
        status = "complete"
    return {
        "ok": True,
        "kind": "exact_field",
        "status": status,
        "text": chunk,
        "state": state,
        "field_path": field_path,
        "source_ref": source_ref,
        "cursor_start": start,
        "cursor_end": end,
        "total_chars": len(text),
    }


def continue_native_exact_response(
    session: dict[str, Any],
    *,
    root_dir: Path,
    native_context: dict[str, Any] | None = None,
    chunk_chars: int = DEFAULT_EXACT_CHUNK_CHARS,
) -> dict[str, Any] | None:
    state = session_native_response_state(session)
    if not has_continuable_native_response(session):
        return None
    source_ref = state.get("source_ref") if isinstance(state.get("source_ref"), dict) else {}
    alias = next((str(source_ref.get(key) or "").strip() for key in ("result_id", "neo_output_id", "file_id", "filename") if str(source_ref.get(key) or "").strip()), "")
    context = _native_context_with_session_referent(native_context or {}, session)
    query = alias or "this image"
    resolved = exact_recipe_field_value(
        Path(root_dir),
        query,
        str(state.get("field_path") or ""),
        native_context=context,
    )
    text = str(resolved.get("value") or "")
    if resolved.get("status") != "established" or not text:
        clear_native_response_state(session)
        return {
            "ok": False,
            "kind": "exact_field",
            "status": "source_unavailable",
            "text": "Neo could not safely continue the exact native field because the authoritative source is no longer available.",
        }
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(state.get("sha256") or ""):
        clear_native_response_state(session)
        return {
            "ok": False,
            "kind": "exact_field",
            "status": "integrity_mismatch",
            "text": "Neo could not safely continue the exact native field because the authoritative source changed after the previous chunk.",
        }
    return _chunk_exact_text(
        session,
        exact_text=text,
        field_path=str(state.get("field_path") or ""),
        source_ref=source_ref,
        offset=int(state.get("cursor") or 0),
        chunk_chars=chunk_chars,
    )


def render_recipe_summary(packet: dict[str, Any]) -> str:
    payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
    if str(packet.get("known_state") or "") not in {"established", "partial"}:
        limitation = next((str(item) for item in (packet.get("limitations") or []) if str(item).strip()), "")
        return limitation or "Neo could not establish the generation settings for that image from authoritative metadata."

    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    sampling = payload.get("sampling") if isinstance(payload.get("sampling"), dict) else {}
    canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else {}
    provider = payload.get("provider") if isinstance(payload.get("provider"), dict) else {}
    generation = payload.get("generation") if isinstance(payload.get("generation"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else {}
    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}

    lines = ["Generation settings"]
    origin = str(identity.get("origin") or "")
    if origin == "external" or "EXTMETA" in [str(item).upper() for item in ((packet.get("authority") or {}).get("classes") or [])]:
        lines.append("Source: embedded external image metadata (not a Neo generation record)")
    checkpoint = model.get("checkpoint") or model.get("model")
    fields = [
        ("Family", model.get("family")),
        ("Loader", model.get("loader")),
        ("Model", checkpoint),
        ("VAE", model.get("vae")),
        ("Workflow", generation.get("mode") or payload.get("mode")),
        ("Seed", sampling.get("actual_seed") if sampling.get("actual_seed") is not None else sampling.get("seed")),
        ("Steps", sampling.get("steps")),
        ("CFG", sampling.get("cfg")),
        ("Sampler", sampling.get("sampler")),
        ("Scheduler", sampling.get("scheduler")),
    ]
    for label, value in fields:
        if value not in (None, "", []):
            lines.append(f"{label}: {value}")
    width, height = canvas.get("width"), canvas.get("height")
    if width not in (None, "") and height not in (None, ""):
        lines.append(f"Resolution: {width} × {height}")
    elapsed = timing.get("elapsed_seconds") or payload.get("elapsed_seconds")
    if elapsed not in (None, ""):
        try:
            lines.append(f"Generation time: {float(elapsed):.2f} seconds")
        except Exception:
            lines.append(f"Generation time: {elapsed}")
    if provider.get("generator"):
        lines.append(f"Generator: {provider.get('generator')}")
    if extensions:
        enabled = [str(key) for key, value in extensions.items() if value not in (None, False, "", [], {})]
        if enabled:
            lines.append("Recorded extensions: " + ", ".join(enabled[:12]))

    positive = str(prompt.get("positive") or prompt.get("effective_positive") or "")
    negative = str(prompt.get("negative") or prompt.get("effective_negative") or "")
    stats = payload.get("recorded_text_stats") if isinstance(payload.get("recorded_text_stats"), dict) else {}
    positive_stats = stats.get("prompt.positive") if isinstance(stats.get("prompt.positive"), dict) else {}
    negative_stats = stats.get("prompt.negative") if isinstance(stats.get("prompt.negative"), dict) else {}
    positive_chars = int(positive_stats.get("characters") or len(positive))
    negative_chars = int(negative_stats.get("characters") or len(negative))
    if positive_chars:
        preview = re.sub(r"\s+", " ", positive).strip()[:260]
        suffix = "…" if positive_chars > len(preview) else ""
        lines.append(f"Prompt: recorded ({positive_chars} characters). Preview: {preview}{suffix}")
    else:
        lines.append("Prompt: not recorded in the available metadata")
    if negative_chars:
        lines.append(f"Negative prompt: recorded ({negative_chars} characters)")

    missing = ((payload.get("record_completeness") or {}).get("missing_fields") if isinstance(payload.get("record_completeness"), dict) else []) or []
    if missing:
        lines.append("Unavailable recorded fields: " + ", ".join(str(item) for item in missing[:12]))
    return "\n".join(lines)


def maybe_render_native_response(
    *,
    root_dir: Path,
    session: dict[str, Any],
    user_text: str,
    native_context: dict[str, Any] | None,
    is_continue: bool = False,
    chunk_chars: int = DEFAULT_EXACT_CHUNK_CHARS,
) -> dict[str, Any] | None:
    """Return a deterministic native response when provider synthesis is unsafe/unnecessary."""
    if is_continue and has_continuable_native_response(session):
        return continue_native_exact_response(
            session,
            root_dir=Path(root_dir),
            native_context=native_context or {},
            chunk_chars=chunk_chars,
        )

    field_path = requested_exact_recipe_field(user_text)
    wants_summary = requested_recipe_summary(user_text)
    if not field_path and not wants_summary:
        return None

    packet = _recipe(Path(root_dir), user_text, native_context=native_context or {}, session=session)
    if str(packet.get("known_state") or "") not in {"established", "partial"}:
        clear_native_response_state(session)
        limitation = next((str(item) for item in (packet.get("limitations") or []) if str(item).strip()), "")
        return {
            "ok": True,
            "kind": "recipe_summary" if wants_summary else "exact_field",
            "status": "not_established",
            "text": limitation or "Neo could not establish that generation field from authoritative image metadata.",
            "packet": packet,
        }

    if wants_summary:
        clear_native_response_state(session)
        return {
            "ok": True,
            "kind": "recipe_summary",
            "status": "complete",
            "text": render_recipe_summary(packet),
            "packet": packet,
        }

    context = _native_context_with_session_referent(native_context or {}, session)
    exact_result = exact_recipe_field_value(
        Path(root_dir),
        _resolution_query(user_text, context),
        field_path,
        native_context=context,
    )
    exact = exact_result.get("value")
    if exact in (None, "") or exact_result.get("status") != "established":
        clear_native_response_state(session)
        label = "original prompt" if field_path == "prompt.positive" else "negative prompt"
        return {
            "ok": True,
            "kind": "exact_field",
            "status": "not_established",
            "text": f"The {label} is not recorded in the available generation metadata.",
            "packet": packet,
        }
    return {
        **_chunk_exact_text(
            session,
            exact_text=str(exact),
            field_path=field_path,
            source_ref=exact_result.get("source_ref") if isinstance(exact_result.get("source_ref"), dict) else _source_ref(packet),
            chunk_chars=chunk_chars,
        ),
        "packet": packet,
    }
