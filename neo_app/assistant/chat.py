from __future__ import annotations

import json
import re
from typing import Any, Iterator
from uuid import uuid4

from neo_app.assistant.context_pack import build_context_pack, compact_context_messages  # compatibility export; Phase 4 compiler does not use it
from neo_app.assistant.attachments import (
    attachment_context_payload,
    image_attachment_content_part,
    resolve_payload_attachments,
)
from neo_app.control_center.assistant_controller import DEFAULT_DB_PATH as ASSISTANT_MEMORY_DB_PATH, get_assistant_control_center
from neo_app.assistant.store import assistant_profile, create_session_payload, get_session, now_iso, save_session_payload, session_summary
from neo_app.providers.profiles import get_backend_profile, get_backend_profile_for_live_task, get_backend_profile_payload, is_backend_profile_connected_for_task
from neo_app.assistant.brain_workspace import resolve_assistant_brain_chat_payload, get_assistant_brain_workspace
from neo_app.prompt_captioning.providers_koboldcpp import run_chat as run_koboldcpp_chat, run_chat_stream as run_koboldcpp_chat_stream
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot
from neo_app.assistant.prompt_compiler import compile_assistant_prompt
from neo_app.memory.writeback_engine import MemoryWritebackEngine
from neo_app.assistant.universal_contract import (
    action_receipt_succeeded,
    assess_assistant_output,
    requested_word_target,
    resolve_assistant_behavior_mode,
    universal_contract_instruction,
    user_requested_structured_output,
)


TEXT_PROVIDER_IDS = {"koboldcpp", "openai_compatible_text", "ollama", "local_gguf_text", "local_gguf_vision"}
TEXT_SURFACES = {"assistant", "text", "prompt_captioning", "roleplay"}


def _capture_durable_assistant_memory(
    *,
    assistant_control: dict[str, Any],
    trace_id: str,
    user_text: str,
    assistant_text: str,
    behavior_mode: str,
    source_id: str,
) -> dict[str, Any]:
    """Best-effort Phase 9 durable writeback after a successful guarded reply.

    Ordinary chat remains searchable session history only. The classifier emits
    candidates only for durable preference/decision/workflow signals, and M11/M12
    review gates still decide whether those candidates become active memory.
    """
    try:
        plan = assistant_control.get("plan") if isinstance(assistant_control.get("plan"), dict) else {}
        identity = plan.get("identity") if isinstance(plan.get("identity"), dict) else {}
        compatibility = identity.get("compatibility") if isinstance(identity.get("compatibility"), dict) else {}
        result = MemoryWritebackEngine(ASSISTANT_MEMORY_DB_PATH).capture_assistant_turn({
            "trace_id": trace_id,
            "source_id": source_id or trace_id,
            "surface": compatibility.get("memory_surface_id") or identity.get("surface_id") or plan.get("surface") or "assistant",
            "scope_id": identity.get("scope_id") or "general",
            "project_id": compatibility.get("memory_project_id") or identity.get("project_id") or None,
            "canonical_project_id": identity.get("project_id") or None,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "behavior_mode": behavior_mode,
        })
        return result if isinstance(result, dict) else {"ok": False, "status": "invalid_writeback_result"}
    except Exception as exc:
        return {"ok": False, "status": "writeback_error", "error": str(exc)[:500]}


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



def assistant_behavior_mode(user_text: str = "", payload: dict[str, Any] | None = None) -> str:
    """Canonical Phase 3 behavior mode used by Assistant runtime and Control Center."""
    return resolve_assistant_behavior_mode(user_text, payload)


def assistant_answer_mode(user_text: str = "", payload: dict[str, Any] | None = None) -> str:
    """Compatibility alias for older callers.

    Phase 3 deliberately replaces domain-specific answer modes with broad behavior
    modes. The lower-case value is kept for diagnostics/API compatibility.
    """
    return assistant_behavior_mode(user_text, payload).lower()


def _json_from_candidate(candidate: str) -> Any:
    try:
        return json.loads(str(candidate or "").strip())
    except Exception:
        return None


def _extract_lane_from_object(parsed: Any) -> tuple[str, str]:
    if isinstance(parsed, dict):
        for key in ("answer", "content", "reply", "response", "text", "message", "prompt", "finished_response"):
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
        "assistant response",
        "assistant",
        "response",
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
        lines.append(line)
    return "\n".join(lines).strip()


def _strip_internal_sections(text: str) -> tuple[str, list[str]]:
    """Remove user-facing leakage of internal planning/metadata sections."""
    value = str(text or "")
    if not value:
        return "", []
    drop_headings = {
        "evidence_summary",
        "evidence summary",
        "missing_context",
        "missing context",
        "next_step",
        "next step",
        "metadata",
        "final json",
        "final_json",
        "final thoughts",
        "control center",
        "prompt contract",
        "validation checks",
        "writeback plan",
        "output lanes",
        "input lanes",
    }
    removed: list[str] = []
    kept: list[str] = []
    dropping = False
    for line in value.splitlines():
        heading_match = re.match(r"^\s*#{1,6}\s*(.*?)\s*$", line)
        if heading_match:
            heading = heading_match.group(1).strip().lower().rstrip(":")
            if heading in drop_headings:
                dropping = True
                removed.append(heading)
                continue
            dropping = False
        if dropping:
            continue
        if re.match(r"^\s*<\|[^|>]{1,100}\|>\s*$", line):
            removed.append("role_token")
            continue
        if re.match(r"^\s*\\</?[A-Za-z0-9_.@-]{2,80}>\s*$", line) or re.match(r"^\s*</?(?:assistant|response|answer|user)>\s*$", line, flags=re.I):
            removed.append("role_tag")
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def _remove_probable_source_echoes(text: str, user_text: str) -> tuple[str, int]:
    """Remove paragraphs that are near-verbatim copies of long pasted source blocks."""
    from difflib import SequenceMatcher

    output = str(text or "")
    source = str(user_text or "")
    if not output or not source or re.search(r"\b(?:quote|repeat|verbatim|copy exactly)\b", source, flags=re.I):
        return output.strip(), 0

    def canon(value: str) -> str:
        value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        return re.sub(r"\s+", " ", value).strip().lower()

    source_blocks = [block.strip() for block in re.split(r"\n\s*\n", source) if len(canon(block)) >= 120]
    if not source_blocks:
        return output.strip(), 0
    removed = 0
    kept_blocks: list[str] = []
    for block in re.split(r"\n\s*\n", output):
        block_clean = block.strip()
        canonical = canon(block_clean)
        is_echo = False
        if len(canonical) >= 120:
            for source_block in source_blocks:
                source_canonical = canon(source_block)
                if canonical == source_canonical or canonical in source_canonical or source_canonical in canonical:
                    is_echo = True
                    break
                if SequenceMatcher(None, canonical, source_canonical).ratio() >= 0.90:
                    is_echo = True
                    break
        if is_echo:
            removed += 1
        elif block_clean:
            kept_blocks.append(block_clean)
    return "\n\n".join(kept_blocks).strip(), removed


def clean_assistant_reply_text(text: str, answer_mode: str = "", user_text: str = "") -> tuple[str, dict[str, Any]]:
    """Return user-facing Assistant text without model/control scaffolding.

    The cleaner is a fallback guard, not the primary behavior engine. It keeps
    explicitly requested structured output intact and otherwise hides common
    local-model schema leakage before chat/history persistence.
    """
    raw = str(text or "").strip()
    mode = str(answer_mode or "complete").lower()
    diagnostics: dict[str, Any] = {
        "schema_id": "neo.assistant.reply_cleanup.v3",
        "changed": False,
        "mode": "plain_text",
        "answer_mode": mode,
        "structured_output_requested": user_requested_structured_output(user_text),
    }
    if not raw:
        return "", diagnostics

    if diagnostics["structured_output_requested"]:
        return raw, diagnostics

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
        cleaned, removed_sections = _strip_internal_sections(cleaned)
        cleaned, source_echo_count = _remove_probable_source_echoes(cleaned, user_text)
        diagnostics.update({
            "changed": cleaned != raw,
            "mode": "json_lane_extracted",
            "extracted_key": extracted_key,
            "available_keys": sorted(str(k) for k in parsed.keys()) if isinstance(parsed, dict) else [],
            "removed_internal_sections": removed_sections,
            "removed_source_echoes": source_echo_count,
        })
        return _strip_noise_headings(cleaned, answer_mode=mode), diagnostics

    # Mixed Markdown + fenced JSON metadata.
    fence_matches = list(re.finditer(r"```(?:json)?\s*\n(.*?)\n```", raw, flags=re.I | re.S))
    rewritten = raw
    if fence_matches:
        extracted_values: list[str] = []
        for match in fence_matches:
            parsed_block = _json_from_candidate(match.group(1).strip())
            value, _key = _extract_lane_from_object(parsed_block)
            if value:
                extracted_values.append(value)
                rewritten = rewritten.replace(match.group(0), value)
        if extracted_values and mode in {"complete", "continue"}:
            rewritten = extracted_values[-1].strip()
            diagnostics["mode"] = "fenced_json_lane_extracted"
        elif extracted_values:
            diagnostics["mode"] = "fenced_json_rewritten"

    cleaned, removed_sections = _strip_internal_sections(rewritten)
    cleaned = _strip_noise_headings(cleaned, answer_mode=mode)
    cleaned, source_echo_count = _remove_probable_source_echoes(cleaned, user_text)
    if cleaned != raw:
        diagnostics.update({
            "changed": True,
            "mode": diagnostics.get("mode") if diagnostics.get("mode") != "plain_text" else "internal_scaffolding_removed",
            "removed_internal_sections": removed_sections,
            "removed_source_echoes": source_echo_count,
        })
    return cleaned, diagnostics


def _natural_reply_instruction_message(behavior_mode: str = "COMPLETE") -> dict[str, str]:
    return {"role": "system", "content": universal_contract_instruction(behavior_mode)}


def _answer_mode_instruction_message(answer_mode: str) -> dict[str, str]:
    """Compatibility message name; content now follows broad Phase 3 behavior."""
    mode = str(answer_mode or "complete").upper()
    if mode not in {"COMPLETE", "RECALL", "ANALYZE", "ADVISE", "ACT", "CONTINUE"}:
        mode = "COMPLETE"
    rules = {
        "COMPLETE": "Produce the requested result now. A request to write/create/draft is satisfied by the finished artifact, not a summary of what it should contain.",
        "RECALL": "Use available memory/context to answer the recall request; distinguish remembered evidence from uncertainty.",
        "ANALYZE": "Inspect and explain the material directly; give concrete conclusions rather than a generic plan.",
        "ADVISE": "Recommend a practical choice and explain the most relevant tradeoffs.",
        "ACT": "Only report successful execution when the runtime supplied a successful action receipt.",
        "CONTINUE": "Continue from the previous response without restarting or recapping completed content.",
    }
    return {"role": "system", "content": f"Neo Assistant behavior: {mode}. {rules[mode]}"}


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


def _assistant_generation_params(
    user_text: str,
    behavior_mode: str,
    params: dict[str, Any] | None,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply task-aware runtime floors without changing backend profile defaults."""
    clean = dict(params or {})
    defaults = (profile or {}).get("generation_defaults") if isinstance((profile or {}).get("generation_defaults"), dict) else {}
    if "max_tokens" not in clean:
        try:
            default_max = int(float(defaults.get("max_tokens") or 512))
        except (TypeError, ValueError):
            default_max = 512
        wanted = max(1, min(default_max, 8192))
        if str(behavior_mode or "").upper() == "COMPLETE":
            wanted = max(wanted, 768)
        target_words = requested_word_target(user_text)
        if target_words:
            # English prose commonly needs ~1.3-1.7 tokens/word. This leaves room
            # for punctuation/formatting while staying under the provider clamp.
            wanted = max(wanted, min(8192, int(target_words * 1.9) + 160))
        clean["max_tokens"] = wanted

    if not user_requested_structured_output(user_text):
        existing = clean.get("stop_sequences")
        stop_sequences = list(existing) if isinstance(existing, list) else ([str(existing)] if existing else [])
        for marker_text in (
            "\n### Final JSON",
            "\n## Final JSON",
            "\n## evidence_summary",
            "\n## missing_context",
            "\n## next_step",
        ):
            if marker_text not in stop_sequences:
                stop_sequences.append(marker_text)
        clean["stop_sequences"] = stop_sequences
    return clean


def _repair_assistant_output(
    profile: dict[str, Any],
    base_messages: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    user_text: str,
    behavior_mode: str,
    issues: list[str],
) -> dict[str, Any]:
    """Run one corrective pass when the first model answer clearly did not do the task."""
    issue_text = ", ".join(issues[:8]) or "incomplete answer"
    repair_messages = list(base_messages)
    repair_messages.append({
        "role": "system",
        "content": (
            "Correction pass. The previous attempt failed Neo's user-facing completion guard "
            f"({issue_text}). Complete the user's ORIGINAL request now. Do not discuss the failure, "
            "do not summarize what you intend to do, do not repeat long pasted source material, and do not output internal headings, role tokens, JSON metadata, or planning lanes. "
            "Return only the finished answer in the format/length the user requested."
        ),
    })
    repair_messages.append({"role": "user", "content": "Correct the previous attempt and complete my original request now."})
    return _provider_run_chat(profile, repair_messages, params)


def _finalize_assistant_output(
    *,
    profile: dict[str, Any],
    request_messages: list[dict[str, Any]],
    params: dict[str, Any],
    user_text: str,
    behavior_mode: str,
    payload: dict[str, Any],
    raw_text: str,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Clean, assess, and if needed repair one Assistant generation."""
    cleaned, cleanup = clean_assistant_reply_text(raw_text, answer_mode=behavior_mode.lower(), user_text=user_text)
    assessment = assess_assistant_output(
        user_text=user_text,
        raw_text=raw_text,
        cleaned_text=cleaned,
        behavior_mode=behavior_mode,
        action_succeeded=action_receipt_succeeded(payload),
    )
    severe = {
        "empty_output",
        "task_deferred_instead_of_completed",
        "requested_length_missed",
        "long_source_echo",
        "unverified_action_claim",
        "meta_summary_instead_of_deliverable",
    }
    first_issues = list(assessment.get("issues") or [])
    should_repair = bool(severe.intersection(first_issues))
    diagnostics: dict[str, Any] = {
        "schema_id": "neo.assistant.output_guard.v1",
        "cleanup": cleanup,
        "assessment": assessment,
        "repair_attempted": False,
        "repair_used": False,
    }
    if not should_repair:
        return cleaned, diagnostics, None

    repair_result = _repair_assistant_output(
        profile,
        request_messages,
        params,
        user_text=user_text,
        behavior_mode=behavior_mode,
        issues=first_issues,
    )
    diagnostics["repair_attempted"] = True
    diagnostics["repair_provider_result"] = {k: repair_result.get(k) for k in ("ok", "error_type", "finish_reason", "warning", "model") if k in repair_result}
    if not repair_result.get("ok"):
        return cleaned, diagnostics, repair_result

    repaired_raw = str(repair_result.get("text") or repair_result.get("partial_text") or "").strip()
    repaired, repaired_cleanup = clean_assistant_reply_text(repaired_raw, answer_mode=behavior_mode.lower(), user_text=user_text)
    repaired_assessment = assess_assistant_output(
        user_text=user_text,
        raw_text=repaired_raw,
        cleaned_text=repaired,
        behavior_mode=behavior_mode,
        action_succeeded=action_receipt_succeeded(payload),
    )
    diagnostics["repair_cleanup"] = repaired_cleanup
    diagnostics["repair_assessment"] = repaired_assessment

    first_score = int(assessment.get("issue_count") or 0)
    repair_score = int(repaired_assessment.get("issue_count") or 0)
    if repaired and (repair_score < first_score or not cleaned):
        diagnostics["repair_used"] = True
        return repaired, diagnostics, repair_result
    return cleaned, diagnostics, repair_result


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


def _retrieval_gateway_result_from_control(assistant_control: dict[str, Any] | None) -> dict[str, Any] | None:
    control = assistant_control if isinstance(assistant_control, dict) else {}
    control_center = control.get("control_center") if isinstance(control.get("control_center"), dict) else {}
    plan = control_center.get("plan") if isinstance(control_center.get("plan"), dict) else {}
    selected = plan.get("selected_context") if isinstance(plan.get("selected_context"), dict) else {}
    gateway = selected.get("retrieval_gateway") if isinstance(selected.get("retrieval_gateway"), dict) else None
    return gateway if isinstance(gateway, dict) and gateway.get("schema_id") else None



def run_assistant_chat_turn(payload: dict[str, Any]) -> dict[str, Any]:
    payload = resolve_assistant_brain_chat_payload(payload or {})
    text = str(payload.get("message") or payload.get("text") or "").strip()
    raw_attachment_payload = payload.get("attachments") or payload.get("attachment_ids") or []
    has_attachment_payload = bool(raw_attachment_payload) if isinstance(raw_attachment_payload, (list, tuple, str)) else False
    if not text and has_attachment_payload:
        text = "Please review the attached file(s)."
    if not text and (payload.get("continue_response") or str(payload.get("mode") or "").lower() in {"continue_response", "continue"}):
        text = "Continue the previous Assistant response from where it stopped."
    behavior_mode = assistant_behavior_mode(text, payload)
    answer_mode = behavior_mode.lower()
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
        "behavior_mode": behavior_mode,
    }, persist=True)
    context_pack = build_context_pack(
        session_id=session_id,
        project_id=project_id,
        message=text,
        retrieval_profile=retrieval_profile,
        active_surface=str(payload.get("active_surface") or payload.get("surface") or ""),
        surface_context_snapshot=(payload.get("surface_context_snapshot") if isinstance(payload.get("surface_context_snapshot"), dict) else (payload.get("active_surface_context") if isinstance(payload.get("active_surface_context"), dict) else None)),
        retrieval_gateway_result=_retrieval_gateway_result_from_control(assistant_control),
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
        "behavior_mode": behavior_mode,
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
    compiled_prompt = compile_assistant_prompt(
        user_text=text,
        behavior_mode=behavior_mode,
        assistant_control=assistant_control,
        context_pack=context_pack,
        attachment_context=attachment_context,
        history_messages=history_messages,
    )
    request_messages = compiled_prompt.get("messages") if isinstance(compiled_prompt.get("messages"), list) else history_messages
    diagnostics["prompt_compiler"] = compiled_prompt.get("diagnostics") or {}
    try:
        get_assistant_control_center().record_prompt_compilation(
            diagnostics.get("assistant_control_trace_id") or "",
            diagnostics.get("prompt_compiler") or {},
        )
    except Exception:
        pass
    params = _assistant_generation_params(text, behavior_mode, payload.get("params") if isinstance(payload.get("params"), dict) else {}, profile)
    result = _provider_run_chat(profile or {}, request_messages, params)
    diagnostics["provider_result"] = {k: result.get(k) for k in ("ok", "recoverable", "error_type", "finish_reason", "warning", "model") if k in result}
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
    assistant_text, output_guard, repair_result = _finalize_assistant_output(
        profile=profile or {},
        request_messages=request_messages,
        params=params,
        user_text=text,
        behavior_mode=behavior_mode,
        payload=payload,
        raw_text=raw_assistant_text,
    )
    diagnostics["reply_cleanup"] = output_guard.get("cleanup") or {}
    diagnostics["output_guard"] = output_guard
    if repair_result is not None:
        diagnostics["repair_provider_result"] = {k: repair_result.get(k) for k in ("ok", "error_type", "finish_reason", "warning", "model") if k in repair_result}
    if not assistant_text:
        saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
        return {"ok": False, "status": "empty_response", "message": "Assistant backend did not produce a usable user-facing reply.", "session": saved["session"], "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics, "provider_result": result}
    assistant_message = {
        "message_id": uuid4().hex,
        "role": "assistant",
        "text": assistant_text,
        "raw_text": raw_assistant_text if (output_guard.get("cleanup") or {}).get("changed") or output_guard.get("repair_used") else "",
        "created_at": now_iso(),
        "source": "assistant_chat_runtime",
        "backend_profile_id": profile.get("profile_id") or "",
        "provider_id": profile.get("provider_id") or "",
        "model": result.get("model") or (profile.get("connection") or {}).get("model") or "",
        "diagnostics": diagnostics,
        "source_grounding": context_pack.get("source_grounding") or {},
    }
    try:
        get_assistant_control_center().record_generation_result(
            diagnostics.get("assistant_control_trace_id") or "",
            {
                "ok": True,
                "status": "completed_repaired" if output_guard.get("repair_used") else "completed",
                "backend_profile_id": (profile or {}).get("profile_id") or "",
                "provider_id": (profile or {}).get("provider_id") or "",
                "model": assistant_message.get("model") or "",
                "text": assistant_text,
            },
        )
    except Exception:
        pass
    diagnostics["durable_writeback"] = _capture_durable_assistant_memory(
        assistant_control=assistant_control,
        trace_id=diagnostics.get("assistant_control_trace_id") or "",
        user_text=text,
        assistant_text=assistant_text,
        behavior_mode=behavior_mode,
        source_id=str(user_message.get("message_id") or ""),
    )
    assistant_message["diagnostics"] = diagnostics
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
    behavior_mode = assistant_behavior_mode(text, payload)
    answer_mode = behavior_mode.lower()
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
        "behavior_mode": behavior_mode,
    }, persist=True)
    context_pack = build_context_pack(
        session_id=session_id,
        project_id=project_id,
        message=text,
        retrieval_profile=retrieval_profile,
        active_surface=str(payload.get("active_surface") or payload.get("surface") or ""),
        surface_context_snapshot=(payload.get("surface_context_snapshot") if isinstance(payload.get("surface_context_snapshot"), dict) else (payload.get("active_surface_context") if isinstance(payload.get("active_surface_context"), dict) else None)),
        retrieval_gateway_result=_retrieval_gateway_result_from_control(assistant_control),
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
        "behavior_mode": behavior_mode,
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
    compiled_prompt = compile_assistant_prompt(
        user_text=text,
        behavior_mode=behavior_mode,
        assistant_control=assistant_control,
        context_pack=context_pack,
        attachment_context=attachment_context,
        history_messages=history_messages,
    )
    request_messages = compiled_prompt.get("messages") if isinstance(compiled_prompt.get("messages"), list) else history_messages
    diagnostics["prompt_compiler"] = compiled_prompt.get("diagnostics") or {}
    try:
        get_assistant_control_center().record_prompt_compilation(
            diagnostics.get("assistant_control_trace_id") or "",
            diagnostics.get("prompt_compiler") or {},
        )
    except Exception:
        pass
    params = _assistant_generation_params(text, behavior_mode, payload.get("params") if isinstance(payload.get("params"), dict) else {}, profile)
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
                # Phase 3 protected streaming: buffer provider tokens until the
                # universal output guard has removed schema/source leakage.
                raw_parts.append(token)
        elif event_type == "error":
            partial = "".join(raw_parts) or str(event.get("partial_text") or "")
            safe_partial, partial_cleanup = clean_assistant_reply_text(partial, answer_mode=answer_mode, user_text=text)
            diagnostics["reply_cleanup"] = partial_cleanup
            diagnostics["provider_result"] = {"ok": False, "error_type": event.get("status") or "provider_stream_error", "error": event.get("error") or "Assistant stream failed.", "partial_chars": len(partial)}
            saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
            error_event = {"type": "error", "ok": False, "status": str(event.get("status") or "provider_stream_error"), "message": event.get("error") or "Assistant stream failed.", "session_id": session_id, "run_id": run_id, "partial_text": safe_partial, "session": saved.get("session"), "sessions": saved.get("sessions", []), "diagnostics": diagnostics}
            yield error_event
            yield {**error_event, "type": "done"}
            return
        elif event_type == "backend_done":
            provider_meta.update({k: event.get(k) for k in ("provider", "backend_profile_id", "model", "status") if k in event})
            if event.get("text") and not raw_parts:
                raw_parts.append(str(event.get("text") or ""))

    raw_assistant_text = "".join(raw_parts).strip()
    assistant_text, output_guard, repair_result = _finalize_assistant_output(
        profile=profile or {},
        request_messages=request_messages,
        params=params,
        user_text=text,
        behavior_mode=behavior_mode,
        payload=payload,
        raw_text=raw_assistant_text,
    )
    diagnostics["reply_cleanup"] = output_guard.get("cleanup") or {}
    diagnostics["output_guard"] = output_guard
    if repair_result is not None:
        diagnostics["repair_provider_result"] = {k: repair_result.get(k) for k in ("ok", "error_type", "finish_reason", "warning", "model") if k in repair_result}
    diagnostics["provider_result"] = {"ok": bool(assistant_text), "finish_reason": "stream_complete", **provider_meta}
    if not assistant_text:
        saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics})
        error_event = {"type": "error", "ok": False, "status": "empty_response", "message": "Assistant backend returned no reply text.", "session_id": session_id, "run_id": run_id, "session": saved.get("session"), "sessions": saved.get("sessions", []), "diagnostics": diagnostics}
        yield error_event
        yield {**error_event, "type": "done"}
        return

    if output_guard.get("repair_attempted"):
        yield {"type": "status", "status": "response_guard_repaired", "session_id": session_id, "run_id": run_id, "message": "Neo corrected an incomplete model reply before displaying it."}
    # Never stream raw provider scaffolding. Emit only the guarded user-facing text.
    yield {"type": "delta", "status": "streaming", "session_id": session_id, "run_id": run_id, "text": assistant_text, "token": assistant_text}

    assistant_message = {
        "message_id": uuid4().hex,
        "role": "assistant",
        "text": assistant_text,
        "raw_text": raw_assistant_text if (output_guard.get("cleanup") or {}).get("changed") or output_guard.get("repair_used") else "",
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
                "status": "completed_stream_repaired" if output_guard.get("repair_used") else "completed_stream",
                "backend_profile_id": (profile or {}).get("profile_id") or "",
                "provider_id": (profile or {}).get("provider_id") or "",
                "model": assistant_message.get("model") or "",
                "text": assistant_text,
            },
        )
    except Exception:
        pass
    diagnostics["durable_writeback"] = _capture_durable_assistant_memory(
        assistant_control=assistant_control,
        trace_id=diagnostics.get("assistant_control_trace_id") or "",
        user_text=text,
        assistant_text=assistant_text,
        behavior_mode=behavior_mode,
        source_id=str(user_message.get("message_id") or ""),
    )
    assistant_message["diagnostics"] = diagnostics
    saved = save_session_payload({**session, "messages": messages, "draft": "", "last_diagnostics": diagnostics, "memory_summary": session.get("memory_summary") or session_summary({**session, "messages": messages}).get("preview") or ""})
    yield {"type": "done", "ok": True, "schema_id": "neo.assistant.chat_stream.v1", "status": "completed", "session_id": session_id, "run_id": run_id, "reply": assistant_text, "assistant_message": assistant_message, "session": saved.get("session"), "sessions": saved.get("sessions", []), "context_pack": context_pack, "diagnostics": diagnostics}
