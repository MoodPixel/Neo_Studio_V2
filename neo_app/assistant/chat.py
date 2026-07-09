from __future__ import annotations

import json
import re
from typing import Any, Iterator
from uuid import uuid4

from neo_app.assistant.context_pack import build_context_pack, compact_context_messages
from neo_app.assistant.attachments import (
    attachment_context_payload,
    image_attachment_content_part,
    resolve_payload_attachments,
)
from neo_app.control_center.assistant_controller import get_assistant_control_center
from neo_app.assistant.store import assistant_profile, create_session_payload, get_session, now_iso, save_session_payload, session_summary
from neo_app.providers.profiles import get_backend_profile, get_backend_profile_for_live_task, get_backend_profile_payload, is_backend_profile_connected_for_task
from neo_app.assistant.brain_workspace import resolve_assistant_brain_chat_payload, get_assistant_brain_workspace
from neo_app.prompt_captioning.providers_koboldcpp import run_chat as run_koboldcpp_chat, run_chat_stream as run_koboldcpp_chat_stream
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot


TEXT_PROVIDER_IDS = {"koboldcpp", "openai_compatible_text", "ollama", "local_gguf_text", "local_gguf_vision"}
TEXT_SURFACES = {"assistant", "text", "prompt_captioning", "roleplay"}


def _profile_supports_text(profile: dict[str, Any]) -> bool:
    flags = profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    caps = runtime.get("capabilities") if isinstance(runtime.get("capabilities"), dict) else {}
    return bool(flags.get("supports_text", caps.get("supports_text", True)))


def _profile_supports_vision(profile: dict[str, Any]) -> bool:
    flags = profile.get("capability_flags") if isinstance(profile.get("capability_flags"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    caps = runtime.get("capabilities") if isinstance(runtime.get("capabilities"), dict) else {}
    provider_id = str(profile.get("provider_id") or "").lower()
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    model_hint = " ".join([provider_id, str(connection.get("model") or "")]).lower()
    if bool(flags.get("supports_vision") or caps.get("supports_vision") or caps.get("runtime_supports_vision")):
        return True
    if provider_id in {"local_gguf_vision", "openai_compatible_vision"}:
        return True
    return any(marker in model_hint for marker in ("vision", "vl", "llava", "minicpm", "mmproj", "multimodal"))


def _payload_live_backend_profile(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the task-gated live profile injected by an explicit route gate.

    Backend profile listings are intentionally passive for local/manual backends
    when auto-connect is off. Explicit task routes, however, first run the shared
    Connect/Test gate and then inject the live profile here so Assistant chat does
    not re-read the passive disconnected profile and falsely block the send.
    """

    for key in ("_neo_live_backend_profile", "_neo_task_backend_profile", "live_backend_profile"):
        profile = payload.get(key)
        if isinstance(profile, dict) and profile.get("profile_id") and profile.get("enabled", True) and _profile_supports_text(profile):
            return profile
    return None


def _resolve_profile_candidate(profile_id: str, *, live_task: bool) -> dict[str, Any] | None:
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    profile = None
    if live_task and is_backend_profile_connected_for_task(pid):
        profile = get_backend_profile_for_live_task(pid)
    if profile is None:
        profile = get_backend_profile(pid)
    if profile and profile.get("enabled", True) and _profile_supports_text(profile):
        return profile
    return None


def resolve_assistant_backend_profile(payload: dict[str, Any] | None = None, *, live_task: bool = True) -> dict[str, Any] | None:
    payload = payload or {}
    live_profile = _payload_live_backend_profile(payload)
    requested = str(payload.get("backend_profile_id") or payload.get("profile_id") or "").strip()
    if live_profile and (not requested or str(live_profile.get("profile_id") or "") == requested):
        return live_profile

    backend_payload = get_backend_profile_payload()
    defaults = backend_payload.get("defaults") if isinstance(backend_payload.get("defaults"), dict) else {}
    candidates = [requested, str(defaults.get("assistant") or ""), str(defaults.get("text") or ""), str(defaults.get("prompt_captioning") or "")]
    seen: set[str] = set()
    for profile_id in candidates:
        profile_id = str(profile_id or "").strip()
        if not profile_id or profile_id in seen:
            continue
        seen.add(profile_id)
        profile = _resolve_profile_candidate(profile_id, live_task=live_task)
        if profile:
            return profile
    for profile in backend_payload.get("profiles", []) if isinstance(backend_payload.get("profiles"), list) else []:
        if not profile.get("enabled", True):
            continue
        if not _profile_supports_text(profile):
            continue
        if profile.get("surface") in TEXT_SURFACES or profile.get("provider_id") in TEXT_PROVIDER_IDS:
            resolved = _resolve_profile_candidate(str(profile.get("profile_id") or ""), live_task=live_task)
            return resolved or profile
    return None


def _backend_available(profile: dict[str, Any]) -> tuple[bool, str]:
    if not profile:
        return False, "No Assistant text backend profile is configured."
    if not profile.get("enabled", True):
        return False, f"Backend profile '{profile.get('profile_id')}' is disabled."
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    status = str(profile.get("runtime_status") or runtime.get("status") or "unknown").lower()
    if status in {"disconnected", "offline", "missing_config", "disabled", "error", "unknown"}:
        return False, f"Backend profile '{profile.get('profile_id')}' is {status}. Click Connect/Test before running Assistant tasks."
    return True, "available"


def _provider_run_chat(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(profile.get("provider_id") or "")
    if provider_id in {"koboldcpp", "openai_compatible_text", "openai_compatible_vision", "ollama", "local_gguf_text", "local_gguf_vision"}:
        return run_koboldcpp_chat(profile, messages, params)
    return {"ok": False, "error_type": "unsupported_provider", "error": f"Assistant chat does not support provider '{provider_id}' yet."}



def _provider_run_chat_stream(profile: dict[str, Any], messages: list[dict[str, Any]], params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    provider_id = str(profile.get("provider_id") or "")
    if provider_id in {"koboldcpp", "openai_compatible_text", "openai_compatible_vision", "ollama", "local_gguf_text", "local_gguf_vision"}:
        yield from run_koboldcpp_chat_stream(profile, messages, params)
        return
    yield {"type": "error", "status": "unsupported_provider", "error": f"Assistant chat streaming does not support provider '{provider_id}' yet."}



def assistant_answer_mode(user_text: str = "", payload: dict[str, Any] | None = None) -> str:
    data = payload if isinstance(payload, dict) else {}
    if data.get("continue_response") or str(data.get("mode") or "").lower() in {"continue_response", "continue"}:
        return "continue_response"
    text = str(user_text or data.get("message") or data.get("text") or "").lower()
    if any(term in text for term in ("client reply", "client response", "reply to this", "respond to this", "write a response")):
        return "client_message"
    if "prompt" in text and any(term in text for term in ("give", "write", "create", "make", "need", "image edit", "qwen")):
        return "direct_prompt"
    if any(term in text for term in ("settings", "suggest", "best", "parameter", "cfg", "steps", "seed", "sampler")):
        return "settings_recommendation"
    if any(term in text for term in ("error", "bug", "broken", "not working", "failed", "fix", "diagnose")):
        return "debug_plan"
    if any(term in text for term in ("how do i", "how to", "what model", "model families", "supports", "supported", "guide", "use the", "this tab", "image tab", "video tab", "roleplay")):
        return "surface_help"
    return "general_chat"


def _json_from_candidate(candidate: str) -> Any:
    try:
        return json.loads(str(candidate or "").strip())
    except Exception:
        return None


def _extract_lane_from_object(parsed: Any) -> tuple[str, str]:
    if isinstance(parsed, dict):
        for key in ("answer", "content", "reply", "response", "text", "message", "prompt"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), key
        string_values = [str(v).strip() for v in parsed.values() if isinstance(v, str) and str(v).strip()]
        if string_values:
            return "\n\n".join(string_values[:3]).strip(), "string_values"
    return "", ""


def _strip_noise_headings(text: str, *, answer_mode: str = "") -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    noisy_headings = {
        "detailed response",
        "answer",
        "final answer",
        "finalize the response",
        "consider next steps",
        "review and adjust",
    }
    lines = []
    for line in value.splitlines():
        clean = re.sub(r"^#{1,6}\s*", "", line).strip().lower().rstrip(":")
        if clean in noisy_headings:
            continue
        if answer_mode in {"direct_prompt", "client_message", "continue_response"} and re.match(r"^#{1,6}\s*step\s+\d+\b", line.strip(), flags=re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

def clean_assistant_reply_text(text: str, answer_mode: str = "") -> tuple[str, dict[str, Any]]:
    """Return natural Assistant text when a backend emits control/schema wrappers.

    Handles pure JSON, fenced JSON, and mixed markdown+JSON blocks. Local text
    models often treat Control Center lanes as an output schema; chat UX should
    preserve the useful answer while hiding metadata wrappers.
    """
    raw = str(text or "").strip()
    mode = str(answer_mode or "general_chat")
    diagnostics: dict[str, Any] = {"schema_id": "neo.assistant.reply_cleanup.v2", "changed": False, "mode": "plain_text", "answer_mode": mode}
    if not raw:
        return "", diagnostics

    candidate = raw
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
            if candidate.lower().startswith("json\n"):
                candidate = candidate[5:].strip()
    parsed = _json_from_candidate(candidate) if candidate[:1] in {"{", "["} else None
    cleaned, extracted_key = _extract_lane_from_object(parsed)
    if cleaned:
        diagnostics.update({"changed": cleaned != raw, "mode": "json_lane_extracted", "extracted_key": extracted_key, "available_keys": sorted(str(k) for k in parsed.keys()) if isinstance(parsed, dict) else []})
        return _strip_noise_headings(cleaned, answer_mode=mode), diagnostics

    # Mixed Markdown + fenced JSON metadata, e.g. "### Detailed Response ... ```json {content: ...}```".
    fence_matches = list(re.finditer(r"```(?:json)?\s*\n(.*?)\n```", raw, flags=re.I | re.S))
    if fence_matches:
        extracted_values: list[str] = []
        rewritten = raw
        for match in fence_matches:
            block = match.group(1).strip()
            parsed_block = _json_from_candidate(block)
            value, key = _extract_lane_from_object(parsed_block)
            if value:
                extracted_values.append(value)
                rewritten = rewritten.replace(match.group(0), value)
        if extracted_values and mode in {"direct_prompt", "client_message", "continue_response"}:
            cleaned = extracted_values[-1].strip()
            diagnostics.update({"changed": cleaned != raw, "mode": "fenced_json_lane_extracted", "extracted_key": "content_or_answer"})
            return _strip_noise_headings(cleaned, answer_mode=mode), diagnostics
        if extracted_values:
            cleaned = _strip_noise_headings(rewritten, answer_mode=mode)
            diagnostics.update({"changed": cleaned != raw, "mode": "fenced_json_rewritten", "extracted_key": "content_or_answer"})
            return cleaned, diagnostics

    cleaned = _strip_noise_headings(raw, answer_mode=mode)
    if cleaned != raw:
        diagnostics.update({"changed": True, "mode": "noise_headings_removed"})
    return cleaned, diagnostics


def _natural_reply_instruction_message() -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Assistant final reply rule: respond in natural chat text. Do not output JSON, dictionaries, "
            "schema objects, title/description/content objects, control lanes, or metadata unless the user explicitly asks for JSON. "
            "Use the Control Center lanes internally, then write the answer as normal prose. "
            "Do not dump raw metadata paths or index JSON; synthesize the answer."
        ),
    }


def _answer_mode_instruction_message(answer_mode: str) -> dict[str, str]:
    mode = str(answer_mode or "general_chat")
    rules = {
        "direct_prompt": "The user is asking for a usable prompt/draft. Output the final prompt directly first. Do not explain steps, do not wrap it in JSON, and do not repeat the user request as analysis.",
        "client_message": "The user is asking for a message draft. Output the draft directly first. Keep notes minimal and outside the draft only if needed.",
        "settings_recommendation": "The user wants settings advice. Use built-in guides first, then live snapshot values. Give practical recommended values and explain tradeoffs briefly.",
        "surface_help": "The user wants help with a Neo surface. Use built-in Neo guides first. Explain what is supported/available without dumping metadata indexes.",
        "debug_plan": "The user wants a fix. Give diagnosis, likely cause, and implementation/validation steps. Keep raw traces summarized.",
        "continue_response": "Continue exactly from where the last assistant response stopped. Do not restart, recap, or repeat earlier sections unless one short bridge phrase is necessary.",
        "general_chat": "Answer directly and naturally. Use context only when it helps the current request.",
    }
    return {"role": "system", "content": f"Assistant answer mode: {mode}. {rules.get(mode, rules['general_chat'])}"}


def _attachment_context_messages(attachment_context: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    document_context = str(attachment_context.get("document_context") or "").strip()
    if document_context:
        messages.append({
            "role": "system",
            "content": "Assistant attachments extracted from uploaded documents. Use them as user-provided context, and mention when extraction was unavailable.\n\n" + document_context,
        })
    warnings = attachment_context.get("warnings") if isinstance(attachment_context.get("warnings"), list) else []
    if warnings:
        messages.append({"role": "system", "content": "Assistant attachment notices:\n" + "\n".join(f"- {item}" for item in warnings)})
    return messages


def _build_history_messages_with_attachments(messages: list[dict[str, Any]], current_user_message_id: str, attachment_context: dict[str, Any]) -> list[dict[str, Any]]:
    history_messages: list[dict[str, Any]] = []
    vision_supported = bool(attachment_context.get("vision_supported"))
    for msg in messages[-12:]:
        role = "assistant" if msg.get("role") == "assistant" else "user"
        content_text = str(msg.get("text") or "").strip()
        if not content_text:
            continue
        if role == "user" and str(msg.get("message_id") or "") == current_user_message_id:
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": content_text}]
            if vision_supported:
                for record in attachment_context.get("images") or []:
                    part = image_attachment_content_part(record)
                    if part:
                        content_parts.append(part)
            if len(content_parts) > 1:
                history_messages.append({"role": role, "content": content_parts})
                continue
        history_messages.append({"role": role, "content": content_text})
    return history_messages


def _assistant_chat_log_summary(
    *,
    session_id: str = "",
    project_id: str = "",
    text: str = "",
    status: str = "",
    profile: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    result = result if isinstance(result, dict) else {}
    return {
        "session_id": str(session_id or ""),
        "scope_id": str(project_id or diagnostics.get("project_id") or "general"),
        "project_id": str(project_id or diagnostics.get("project_id") or "general"),
        "message_chars": len(str(text or "")),
        "status": str(status or ""),
        "backend_profile_id": str(profile.get("profile_id") or diagnostics.get("backend_profile_id") or ""),
        "provider_id": str(profile.get("provider_id") or diagnostics.get("provider_id") or ""),
        "backend_status": str(diagnostics.get("backend_status") or ""),
        "context_chars": int(((diagnostics.get("context_pack") or {}) if isinstance(diagnostics.get("context_pack"), dict) else {}).get("chars") or 0),
        "context_section_count": int(((diagnostics.get("context_pack") or {}) if isinstance(diagnostics.get("context_pack"), dict) else {}).get("section_count") or 0),
        "reply_chars": len(str(result.get("text") or result.get("partial_text") or "")),
        "result_ok": bool(result.get("ok")) if result else None,
        "error_type": str(result.get("error_type") or ""),
    }


def _safe_log_assistant_event(event: str, *, run_id: str = "", payload: dict[str, Any] | None = None, level: str = "INFO") -> None:
    try:
        log_surface_event("assistant", event, run_id=run_id or None, level=level, payload=payload or {})
    except Exception:
        pass



def run_assistant_chat_turn(payload: dict[str, Any]) -> dict[str, Any]:
    payload = resolve_assistant_brain_chat_payload(payload or {})
    text = str(payload.get("message") or payload.get("text") or "").strip()
    raw_attachment_payload = payload.get("attachments") or payload.get("attachment_ids") or []
    has_attachment_payload = bool(raw_attachment_payload) if isinstance(raw_attachment_payload, (list, tuple, str)) else False
    if not text and has_attachment_payload:
        text = "Please review the attached file(s)."
    if not text and (payload.get("continue_response") or str(payload.get("mode") or "").lower() in {"continue_response", "continue"}):
        text = "Continue the previous Assistant response from where it stopped."
    answer_mode = assistant_answer_mode(text, payload)
    if not text:
        try:
            record_surface_error("assistant", "Assistant message is required.", payload={"payload_keys": sorted((payload or {}).keys())}, run_id=str(payload.get("session_id") or "assistant_chat"))
        except Exception:
            pass
        raise ValueError("Assistant message is required")

    session_id = str(payload.get("session_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip() or str(assistant_profile().get("default_project_id") or "general")
    session = get_session(session_id) if session_id else None
    if not session:
        created = create_session_payload({"title": str(payload.get("title") or "New assistant chat"), "project_id": project_id, "mode": str(payload.get("mode") or "general")})
        session = created["session"]
        session_id = session["session_id"]

    _safe_log_assistant_event("assistant.chat.started", run_id=session_id, payload=_assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="started"))

    messages = list(session.get("messages") if isinstance(session.get("messages"), list) else [])
    user_message = {"message_id": uuid4().hex, "role": "user", "text": text, "created_at": now_iso(), "source": "assistant_chat_runtime"}
    messages.append(user_message)
    session["messages"] = messages
    session["project_id"] = project_id

    retrieval_profile = str(payload.get("retrieval_profile") or assistant_profile().get("retrieval_profile") or "smart")
    assistant_control = get_assistant_brain_workspace().context({
        **payload,
        "message": text,
        "session_id": session_id,
        "project_id": project_id,
        "retrieval_profile": retrieval_profile,
    }, persist=True)
    context_pack = build_context_pack(
        session_id=session_id,
        project_id=project_id,
        message=text,
        retrieval_profile=retrieval_profile,
        active_surface=str(payload.get("active_surface") or payload.get("surface") or ""),
        surface_context_snapshot=(payload.get("surface_context_snapshot") if isinstance(payload.get("surface_context_snapshot"), dict) else (payload.get("active_surface_context") if isinstance(payload.get("active_surface_context"), dict) else None)),
    )
    profile = resolve_assistant_backend_profile(payload)
    available, reason = _backend_available(profile or {})
    attachment_records = resolve_payload_attachments(payload)
    attachment_context = attachment_context_payload(attachment_records, vision_supported=_profile_supports_vision(profile or {}))
    if attachment_records:
        user_message["attachments"] = attachment_context.get("records") or []
        user_message["attachment_ids"] = [item.get("attachment_id") for item in (attachment_context.get("records") or []) if item.get("attachment_id")]
        session["messages"] = messages
    diagnostics = {
        "assistant_brain_workspace": assistant_control.get("diagnostics") or {},
        "assistant_control_center": (assistant_control.get("control_center") or {}).get("diagnostics") or {},
        "assistant_control_trace_id": assistant_control.get("trace_id") or "",
        "context_pack": context_pack.get("diagnostics") or {},
        "attachments": {
            "schema_id": "neo.assistant.attachments.runtime.v1",
            "counts": attachment_context.get("counts") or {},
            "records": attachment_context.get("records") or [],
            "warnings": attachment_context.get("warnings") or [],
            "vision_supported": bool(attachment_context.get("vision_supported")),
        },
        "backend_profile_id": (profile or {}).get("profile_id") or "",
        "provider_id": (profile or {}).get("provider_id") or "",
        "backend_status": "available" if available else "provider_gated",
        "backend_reason": reason,
        "answer_mode": answer_mode,
    }
    if not available:
        saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
        summary = _assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="provider_gated", profile=profile, diagnostics=diagnostics)
        _safe_log_assistant_event("assistant.chat.provider_gated", run_id=session_id, level="WARNING", payload=summary)
        try:
            record_surface_error("assistant", reason, payload=summary, run_id=session_id)
        except Exception:
            pass
        return {"ok": False, "status": "provider_gated", "message": reason, "session": saved["session"], "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics}

    history_messages = _build_history_messages_with_attachments(messages, str(user_message.get("message_id") or ""), attachment_context)
    control_messages = assistant_control.get("messages") if isinstance(assistant_control.get("messages"), list) else []
    request_messages = [_natural_reply_instruction_message(), _answer_mode_instruction_message(answer_mode)] + control_messages + compact_context_messages(context_pack) + _attachment_context_messages(attachment_context) + history_messages
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    result = _provider_run_chat(profile or {}, request_messages, params)
    diagnostics["provider_result"] = {k: result.get(k) for k in ("ok", "recoverable", "error_type", "finish_reason", "warning", "model") if k in result}
    try:
        get_assistant_control_center().record_generation_result(
            diagnostics.get("assistant_control_trace_id") or "",
            {
                "ok": bool(result.get("ok")),
                "status": "completed" if result.get("ok") else "provider_error",
                "backend_profile_id": (profile or {}).get("profile_id") or "",
                "provider_id": (profile or {}).get("provider_id") or "",
                "model": result.get("model") or (profile.get("connection") or {}).get("model") if profile else "",
                "text": result.get("text") or result.get("partial_text") or "",
            },
        )
    except Exception:
        pass

    if not result.get("ok"):
        saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
        summary = _assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="provider_error", profile=profile, diagnostics=diagnostics, result=result)
        _safe_log_assistant_event("assistant.chat.provider_error", run_id=session_id, level="ERROR", payload=summary)
        try:
            record_surface_error("assistant", str(result.get("error") or "Assistant backend failed."), payload=summary, run_id=session_id)
        except Exception:
            pass
        return {"ok": False, "status": "provider_error", "message": result.get("error") or "Assistant backend failed.", "session": saved["session"], "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics, "provider_result": result}

    raw_assistant_text = str(result.get("text") or result.get("partial_text") or "").strip()
    assistant_text, cleanup_diagnostics = clean_assistant_reply_text(raw_assistant_text, answer_mode=answer_mode)
    diagnostics["reply_cleanup"] = cleanup_diagnostics
    assistant_message = {
        "message_id": uuid4().hex,
        "role": "assistant",
        "text": assistant_text,
        "raw_text": raw_assistant_text if cleanup_diagnostics.get("changed") else "",
        "created_at": now_iso(),
        "source": "assistant_chat_runtime",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": result.get("model") or (profile.get("connection") or {}).get("model") or "",
        "diagnostics": diagnostics,
        "source_grounding": context_pack.get("source_grounding") or {},
    }
    messages.append(assistant_message)
    saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics, "memory_summary": session.get("memory_summary") or session_summary({**session, "messages": messages}).get("preview") or ""})
    summary = _assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="completed", profile=profile, diagnostics=diagnostics, result=result)
    _safe_log_assistant_event("assistant.chat.completed", run_id=session_id, payload=summary)
    try:
        record_surface_snapshot("assistant", "neo_last_chat_turn.json", {"summary": summary, "diagnostics": diagnostics}, run_id=session_id)
    except Exception:
        pass
    return {"ok": True, "status": "completed", "reply": assistant_text, "assistant_message": assistant_message, "session": saved["session"], "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics, "provider_result": result}


def stream_assistant_chat_turn_event_dicts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Execute an Assistant turn and emit SSE-friendly event dictionaries."""
    payload = resolve_assistant_brain_chat_payload(payload or {})
    text = str(payload.get("message") or payload.get("text") or "").strip()
    raw_attachment_payload = payload.get("attachments") or payload.get("attachment_ids") or []
    has_attachment_payload = bool(raw_attachment_payload) if isinstance(raw_attachment_payload, (list, tuple, str)) else False
    if not text and has_attachment_payload:
        text = "Please review the attached file(s)."
    if not text and (payload.get("continue_response") or str(payload.get("mode") or "").lower() in {"continue_response", "continue"}):
        text = "Continue the previous Assistant response from where it stopped."
    answer_mode = assistant_answer_mode(text, payload)
    if not text:
        yield {"type": "error", "ok": False, "status": "missing_message", "message": "Assistant message is required."}
        yield {"type": "done", "ok": False, "status": "missing_message", "message": "Assistant message is required."}
        return

    session_id = str(payload.get("session_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip() or str(assistant_profile().get("default_project_id") or "general")
    session = get_session(session_id) if session_id else None
    if not session:
        created = create_session_payload({"title": str(payload.get("title") or "New assistant chat"), "project_id": project_id, "mode": str(payload.get("mode") or "general")})
        session = created["session"]
        session_id = session["session_id"]

    run_id = uuid4().hex
    yield {"type": "status", "schema_id": "neo.assistant.chat_stream.v1", "status": "preparing_context", "session_id": session_id, "run_id": run_id, "message": "Preparing Assistant context…"}

    messages = list(session.get("messages") if isinstance(session.get("messages"), list) else [])
    user_message = {"message_id": uuid4().hex, "role": "user", "text": text, "created_at": now_iso(), "source": "assistant_chat_stream_runtime"}
    messages.append(user_message)
    session["messages"] = messages
    session["project_id"] = project_id

    retrieval_profile = str(payload.get("retrieval_profile") or assistant_profile().get("retrieval_profile") or "smart")
    assistant_control = get_assistant_brain_workspace().context({
        **payload,
        "message": text,
        "session_id": session_id,
        "project_id": project_id,
        "retrieval_profile": retrieval_profile,
    }, persist=True)
    context_pack = build_context_pack(
        session_id=session_id,
        project_id=project_id,
        message=text,
        retrieval_profile=retrieval_profile,
        active_surface=str(payload.get("active_surface") or payload.get("surface") or ""),
        surface_context_snapshot=(payload.get("surface_context_snapshot") if isinstance(payload.get("surface_context_snapshot"), dict) else (payload.get("active_surface_context") if isinstance(payload.get("active_surface_context"), dict) else None)),
    )
    profile = resolve_assistant_backend_profile(payload)
    available, reason = _backend_available(profile or {})
    attachment_records = resolve_payload_attachments(payload)
    attachment_context = attachment_context_payload(attachment_records, vision_supported=_profile_supports_vision(profile or {}))
    if attachment_records:
        user_message["attachments"] = attachment_context.get("records") or []
        user_message["attachment_ids"] = [item.get("attachment_id") for item in (attachment_context.get("records") or []) if item.get("attachment_id")]
        session["messages"] = messages

    diagnostics = {
        "assistant_brain_workspace": assistant_control.get("diagnostics") or {},
        "assistant_control_center": (assistant_control.get("control_center") or {}).get("diagnostics") or {},
        "assistant_control_trace_id": assistant_control.get("trace_id") or "",
        "context_pack": context_pack.get("diagnostics") or {},
        "attachments": {
            "schema_id": "neo.assistant.attachments.runtime.v1",
            "counts": attachment_context.get("counts") or {},
            "records": attachment_context.get("records") or [],
            "warnings": attachment_context.get("warnings") or [],
            "vision_supported": bool(attachment_context.get("vision_supported")),
        },
        "backend_profile_id": (profile or {}).get("profile_id") or "",
        "provider_id": (profile or {}).get("provider_id") or "",
        "backend_status": "available" if available else "provider_gated",
        "backend_reason": reason,
        "answer_mode": answer_mode,
        "streaming": True,
        "run_id": run_id,
    }

    saved_user = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
    yield {"type": "start", "ok": True, "schema_id": "neo.assistant.chat_stream.v1", "status": "started", "session_id": session_id, "run_id": run_id, "user_message": user_message, "session": saved_user.get("session"), "context_pack": context_pack, "diagnostics": diagnostics}
    yield {"type": "user_message", "session_id": session_id, "run_id": run_id, "message": user_message}

    if not available:
        summary = _assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="provider_gated", profile=profile, diagnostics=diagnostics)
        _safe_log_assistant_event("assistant.chat_stream.provider_gated", run_id=session_id, level="WARNING", payload=summary)
        event = {"type": "error", "ok": False, "status": "provider_gated", "message": reason, "session_id": session_id, "run_id": run_id, "session": saved_user.get("session"), "sessions": saved_user.get("sessions", []), "diagnostics": diagnostics}
        yield event
        yield {**event, "type": "done"}
        return

    history_messages = _build_history_messages_with_attachments(messages, str(user_message.get("message_id") or ""), attachment_context)
    control_messages = assistant_control.get("messages") if isinstance(assistant_control.get("messages"), list) else []
    request_messages = [_natural_reply_instruction_message(), _answer_mode_instruction_message(answer_mode)] + control_messages + compact_context_messages(context_pack) + _attachment_context_messages(attachment_context) + history_messages
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    raw_parts: list[str] = []
    provider_meta: dict[str, Any] = {}
    yield {"type": "status", "status": "backend_streaming", "session_id": session_id, "run_id": run_id, "message": "Streaming Assistant reply…"}
    for event in _provider_run_chat_stream(profile or {}, request_messages, params):
        event_type = str(event.get("type") or "")
        if event_type == "backend_start":
            provider_meta.update({k: event.get(k) for k in ("provider", "backend_profile_id", "model", "status") if k in event})
            yield {"type": "status", "status": "backend_started", "session_id": session_id, "run_id": run_id, "provider": event.get("provider") or event.get("provider_id") or "", "model": event.get("model") or ""}
        elif event_type == "token":
            token = str(event.get("text") or "")
            if token:
                raw_parts.append(token)
                yield {"type": "delta", "status": "streaming", "session_id": session_id, "run_id": run_id, "text": token, "token": token}
        elif event_type == "error":
            partial = "".join(raw_parts) or str(event.get("partial_text") or "")
            diagnostics["provider_result"] = {"ok": False, "error_type": event.get("status") or "provider_stream_error", "error": event.get("error") or "Assistant stream failed.", "partial_chars": len(partial)}
            saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
            error_event = {"type": "error", "ok": False, "status": str(event.get("status") or "provider_stream_error"), "message": event.get("error") or "Assistant stream failed.", "session_id": session_id, "run_id": run_id, "partial_text": partial, "session": saved.get("session"), "sessions": saved.get("sessions", []), "diagnostics": diagnostics}
            yield error_event
            yield {**error_event, "type": "done"}
            return
        elif event_type == "backend_done":
            provider_meta.update({k: event.get(k) for k in ("provider", "backend_profile_id", "model", "status") if k in event})
            if event.get("text") and not raw_parts:
                raw_parts.append(str(event.get("text") or ""))

    raw_assistant_text = "".join(raw_parts).strip()
    assistant_text, cleanup_diagnostics = clean_assistant_reply_text(raw_assistant_text, answer_mode=answer_mode)
    diagnostics["reply_cleanup"] = cleanup_diagnostics
    diagnostics["provider_result"] = {"ok": bool(assistant_text), "finish_reason": "stream_complete", **provider_meta}
    if not assistant_text:
        saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
        error_event = {"type": "error", "ok": False, "status": "empty_response", "message": "Assistant backend returned no reply text.", "session_id": session_id, "run_id": run_id, "session": saved.get("session"), "sessions": saved.get("sessions", []), "diagnostics": diagnostics}
        yield error_event
        yield {**error_event, "type": "done"}
        return

    assistant_message = {
        "message_id": uuid4().hex,
        "role": "assistant",
        "text": assistant_text,
        "raw_text": raw_assistant_text if cleanup_diagnostics.get("changed") else "",
        "created_at": now_iso(),
        "source": "assistant_chat_stream_runtime",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": provider_meta.get("model") or (profile.get("connection") or {}).get("model") or "",
        "diagnostics": diagnostics,
        "source_grounding": context_pack.get("source_grounding") or {},
        "streaming": True,
    }
    messages.append(assistant_message)
    saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics, "memory_summary": session.get("memory_summary") or session_summary({**session, "messages": messages}).get("preview") or ""})
    summary = _assistant_chat_log_summary(session_id=session_id, project_id=project_id, text=text, status="completed_stream", profile=profile, diagnostics=diagnostics, result={"ok": True, "text": assistant_text, "model": assistant_message.get("model")})
    _safe_log_assistant_event("assistant.chat_stream.completed", run_id=session_id, payload=summary)
    try:
        get_assistant_control_center().record_generation_result(
            diagnostics.get("assistant_control_trace_id") or "",
            {
                "ok": True,
                "status": "completed_stream",
                "backend_profile_id": (profile or {}).get("profile_id") or "",
                "provider_id": (profile or {}).get("provider_id") or "",
                "model": assistant_message.get("model") or "",
                "text": assistant_text,
            },
        )
    except Exception:
        pass
    yield {"type": "done", "ok": True, "schema_id": "neo.assistant.chat_stream.v1", "status": "completed", "session_id": session_id, "run_id": run_id, "reply": assistant_text, "assistant_message": assistant_message, "session": saved.get("session"), "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics}
