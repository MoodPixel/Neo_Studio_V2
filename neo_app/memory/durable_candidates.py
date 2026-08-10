from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DURABLE_CANDIDATE_SCHEMA_ID = "neo.memory.durable_candidate.phase9.v1"

_NOISE_MESSAGES = {
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "cool", "nice", "lol", "yes", "no",
    "continue", "regenerate", "retry", "do it", "go ahead",
}

_PREFERENCE_DIMENSIONS = (
    ("skin_tone", ("skin tone", "skin tones", "natural skin", "skin colour", "skin color")),
    ("response_style", ("response style", "answer style", "reply style", "writing style")),
    ("aspect_ratio", ("aspect ratio", "portrait ratio", "landscape ratio")),
    ("resolution", ("resolution", "image size", "video size")),
    ("caption_style", ("caption style", "caption format")),
    ("prompt_style", ("prompt style", "prompt format")),
    ("color_palette", ("color palette", "colour palette", "color grading", "colour grading")),
    ("lighting", ("lighting", "light style")),
    ("language", ("language", "english", "chinese", "sinhala")),
    ("model", ("model", "checkpoint")),
    ("backend", ("backend", "provider")),
    ("sampler", ("sampler",)),
    ("scheduler", ("scheduler",)),
    ("cfg", ("cfg", "guidance scale")),
    ("steps", ("steps", "step count")),
    ("workflow", ("workflow", "process")),
    ("tone", ("tone",)),
    ("length", ("length", "word count", "duration")),
)

_PROJECT_DECISION_DIMENSIONS = (
    ("budget", ("budget", "price", "pricing", "quote", "cost")),
    ("delivery", ("delivery", "deadline", "timeline", "due date")),
    ("scope", ("scope", "deliverable", "deliverables", "revision", "revisions")),
    ("style", ("style", "branding", "brand", "visual direction", "design direction")),
    ("format", ("format", "resolution", "aspect ratio", "1080p", "4k")),
    ("workflow", ("workflow", "process", "pipeline")),
    ("model", ("model", "backend", "provider")),
)

_VOLATILE_SETTING_KEYS = {
    "seed", "random_seed", "noise_seed", "batch_index", "index", "created_at", "updated_at", "timestamp",
    "output", "output_path", "path", "file", "filename", "result_id", "job_id", "metadata_id",
}
_SETTING_KEYS = {
    "steps", "cfg", "cfg_scale", "sampler", "scheduler", "width", "height", "resolution", "denoise",
    "strength", "fps", "duration", "frames", "frame_count", "voice_profile", "profile_id", "speed", "pitch",
    "guidance", "guidance_scale", "clip_skip", "control_mode", "controlnet", "mode", "quality", "format",
}


def _clean(value: Any, limit: int = 1800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _hash(value: Any, length: int = 24) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _dimension(text: str, specs: tuple[tuple[str, tuple[str, ...]], ...], fallback_prefix: str) -> str:
    lower = text.lower()
    for key, markers in specs:
        if any(marker in lower for marker in markers):
            return key
    tokens = [tok for tok in re.findall(r"[a-z0-9_]+", lower) if tok not in {
        "i", "we", "my", "our", "the", "a", "an", "to", "for", "that", "this", "it", "is", "are", "be", "use",
        "always", "prefer", "preference", "client", "approved", "confirmed", "decided", "agreed", "final", "from", "now", "on",
    }]
    return f"{fallback_prefix}_{'_'.join(tokens[:4]) or _hash(lower, 8)}"


def _candidate(
    *,
    source_trace_id: str = "",
    source_type: str,
    source_id: str,
    surface: str,
    project_id: str | None,
    scope_id: str | None,
    memory_type: str,
    candidate_class: str,
    title: str,
    content: str,
    durable_key: str,
    confidence: float,
    importance: str = "normal",
    support_threshold: int = 1,
    decision_reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
    requires_review_for: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_id": DURABLE_CANDIDATE_SCHEMA_ID,
        "source_trace_id": source_trace_id or None,
        "source_type": source_type,
        "source_id": source_id,
        "surface": surface or "global",
        "project_id": project_id or None,
        "scope_id": scope_id or None,
        "memory_type": memory_type,
        "candidate_class": candidate_class,
        "title": _clean(title, 220),
        "content": _clean(content, 2200),
        "durable_key": durable_key,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "importance": importance,
        "support_threshold": max(1, int(support_threshold or 1)),
        "decision_reason": decision_reason,
        "evidence": list(evidence or []),
        "payload": dict(payload or {}),
        "requires_review_for": list(requires_review_for or []),
    }


def assistant_turn_candidates(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = payload or {}
    user_text = _clean(data.get("user_text") or data.get("message"), 2600)
    if not user_text or user_text.lower().strip(" .!?") in _NOISE_MESSAGES or len(user_text) < 10:
        return []

    surface = _clean(data.get("surface") or data.get("surface_id") or "assistant", 80) or "assistant"
    scope_id = _clean(data.get("scope_id"), 180) or None
    project_id = _clean(data.get("project_id"), 180) or None
    canonical_project_id = _clean(data.get("canonical_project_id") or data.get("delivery_project_id"), 180) or None
    trace_id = _clean(data.get("trace_id"), 180)
    source_id = _clean(data.get("source_id") or trace_id or _hash(user_text, 16), 220)
    lower = user_text.lower()
    evidence = [{"source_type": "assistant_user_message", "source_id": source_id, "text": user_text[:700]}]
    common_payload = {
        "assistant_text_preview": _clean(data.get("assistant_text"), 900),
        "behavior_mode": _clean(data.get("behavior_mode"), 40) or "COMPLETE",
        "explicit_user_statement": True,
    }
    out: list[dict[str, Any]] = []

    preference_match = re.search(r"\b(?:i|we)\s+(?:really\s+)?prefer\s+(.+?)(?:[.!?]|$)", user_text, flags=re.I)
    if not preference_match:
        preference_match = re.search(r"\b(?:my|our)\s+preference\s+(?:is|for)\s+(.+?)(?:[.!?]|$)", user_text, flags=re.I)
    if preference_match:
        statement = _clean(preference_match.group(0), 1200)
        dim = _dimension(statement, _PREFERENCE_DIMENSIONS, "preference")
        out.append(_candidate(
            source_trace_id=trace_id,
            source_type="assistant_turn",
            source_id=source_id,
            surface=surface,
            project_id=project_id,
            scope_id=scope_id,
            memory_type="user_preference_change",
            candidate_class="explicit_user_preference",
            title=f"User preference · {dim.replace('_', ' ')}",
            content=statement,
            durable_key=f"user_preference:{scope_id or 'general'}:{dim}",
            confidence=0.96,
            importance="high",
            support_threshold=1,
            decision_reason="Explicit user preference statement; durable but review-gated by M11/M12 policy.",
            evidence=evidence,
            payload=common_payload,
            requires_review_for=["user_preference_change"],
        ))

    workflow_match = re.search(
        r"\b(?:from now on|going forward|we should always|i want to always|our standard(?: workflow)? is|default workflow is)\b.{0,180}",
        user_text,
        flags=re.I,
    )
    if workflow_match and not preference_match:
        statement = _clean(workflow_match.group(0), 1200)
        dim = _dimension(statement, _PREFERENCE_DIMENSIONS, "workflow")
        out.append(_candidate(
            source_trace_id=trace_id,
            source_type="assistant_turn",
            source_id=source_id,
            surface=surface,
            project_id=project_id,
            scope_id=scope_id,
            memory_type="workflow_preference_candidate",
            candidate_class="workflow_preference",
            title=f"Workflow preference · {dim.replace('_', ' ')}",
            content=statement,
            durable_key=f"workflow_preference:{scope_id or project_id or surface}:{dim}",
            confidence=0.88,
            importance="normal",
            support_threshold=2,
            decision_reason="Workflow preference needs repeated support before low-risk promotion.",
            evidence=evidence,
            payload=common_payload,
        ))

    remember_match = re.search(r"\bremember\s+(?:that\s+|this\s*:?[ ]*)(.+?)(?:[.!?]|$)", user_text, flags=re.I)
    if remember_match and not out:
        statement = _clean(remember_match.group(1), 1200)
        dim = _dimension(statement, _PREFERENCE_DIMENSIONS, "note")
        out.append(_candidate(
            source_trace_id=trace_id,
            source_type="assistant_turn",
            source_id=source_id,
            surface=surface,
            project_id=project_id,
            scope_id=scope_id,
            memory_type="user_memory_directive",
            candidate_class="explicit_memory_directive",
            title=f"Explicit memory · {dim.replace('_', ' ')}",
            content=statement,
            durable_key=f"user_memory:{scope_id or 'general'}:{dim}",
            confidence=0.98,
            importance="high",
            support_threshold=1,
            decision_reason="User explicitly asked Neo to remember this; review-gated to avoid silently replacing an existing durable fact.",
            evidence=evidence,
            payload=common_payload,
            requires_review_for=["user_memory_directive"],
        ))

    decision_markers = (
        "client approved", "client has approved", "we decided", "we have decided", "we agreed", "we have agreed",
        "final decision", "this is final", "confirmed that", "client confirmed", "client has confirmed", "finalized",
    )
    if canonical_project_id and any(marker in lower for marker in decision_markers):
        dim = _dimension(user_text, _PROJECT_DECISION_DIMENSIONS, "decision")
        out.append(_candidate(
            source_trace_id=trace_id,
            source_type="assistant_turn",
            source_id=source_id,
            surface=surface,
            project_id=project_id,
            scope_id=scope_id,
            memory_type="project_decision_candidate",
            candidate_class="confirmed_project_decision",
            title=f"Project decision · {dim.replace('_', ' ')}",
            content=user_text,
            durable_key=f"project_decision:{canonical_project_id}:{dim}",
            confidence=0.94,
            importance="high",
            support_threshold=1,
            decision_reason="Explicitly confirmed project decision; review required before replacing durable project truth.",
            evidence=evidence,
            payload={**common_payload, "canonical_project_id": canonical_project_id},
            requires_review_for=["project_decision_candidate"],
        ))

    # Deduplicate when one sentence matches two closely related heuristics.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in out:
        deduped[(item["memory_type"], item["durable_key"])] = item
    return list(deduped.values())


def _flatten_settings(event: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("model", "model_id", "provider_id", "backend_profile_id", "route_id", "family", "category"):
        value = event.get(key)
        if value not in (None, "", [], {}):
            result[key] = _clean(value, 300)
    settings = event.get("settings") or event.get("parameters") or event.get("params")
    if isinstance(settings, dict):
        for key, value in settings.items():
            key_l = str(key).lower().strip()
            if key_l in _VOLATILE_SETTING_KEYS:
                continue
            if key_l in _SETTING_KEYS and value not in (None, "", [], {}):
                result[key_l] = _clean(value, 300)
    return result


def surface_event_candidates(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = payload or {}
    surface = _clean(data.get("surface_id") or data.get("surface"), 80)
    if not surface or surface == "roleplay":
        return []
    signature = _flatten_settings(data)
    if not signature:
        return []
    scope_id = _clean(data.get("scope_id"), 180) or None
    project_id = _clean(data.get("project_id"), 180) or None
    source_id = _clean(data.get("source_id") or data.get("result_id") or data.get("job_id") or _hash(data, 16), 220)
    signature_key = _hash(signature, 20)
    content = "Successful configuration: " + ", ".join(f"{key}={value}" for key, value in sorted(signature.items()))
    return [_candidate(
        source_type=f"surface_success:{surface}",
        source_id=source_id,
        surface=surface,
        project_id=project_id,
        scope_id=scope_id,
        memory_type="successful_setting_candidate",
        candidate_class="repeated_successful_setting",
        title=f"Repeated successful {surface} setting",
        content=content,
        durable_key=f"successful_setting:{project_id or scope_id or surface}:{surface}:{signature_key}",
        confidence=0.9,
        importance="normal",
        support_threshold=2,
        decision_reason="A successful setting must recur in at least two distinct successful tasks before durable auto-promotion.",
        evidence=[{"source_type": f"surface_registry:{surface}", "source_id": source_id, "event_type": data.get("event_type") or ""}],
        payload={"settings_signature": signature, "event_type": data.get("event_type") or "", "history_fragment_id": data.get("history_fragment_id") or ""},
    )]
