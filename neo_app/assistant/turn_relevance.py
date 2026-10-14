from __future__ import annotations

import re
from typing import Any

TURN_RELEVANCE_SCHEMA_ID = "neo.assistant.turn_relevance.v1"
TURN_RELEVANCE_VERSION = "1.2.0-aci7.2"

_RECALL_MARKERS = (
    "remember", "recall", "last time", "previously", "earlier", "before", "what did i", "what did we",
    "which one did", "what model did", "what settings", "in the project", "according to", "from the file",
    "from the document", "from my memory", "we discussed", "we decided", "we agreed", "what happened",
    "again", "bring back", "we settled", "settled on", "what was that", "remind me what",
)

_PREFERENCE_PATTERNS = (
    r"^\s*i\s+prefer\b",
    r"^\s*i\s+like\b.+\b(?:responses?|answers?|format|style|tone|workflow|prompts?|captions?|images?)\b",
    r"^\s*i\s+want\s+(?:you|neo)\s+to\b",
    r"^\s*(?:please\s+)?(?:keep|make|use|write|answer|respond)\b.+\b(?:concise|brief|detailed|short|long|english|format|style|tone)\b",
    r"^\s*(?:do\s+not|don't|never)\s+(?:mention|use|recommend|include|show)\b",
    r"^\s*from\s+now\s+on\b",
    r"^\s*i\s+(?:switched|changed|moved)\s+from\b.+\bto\b",
)

_ACK_PATTERNS = (
    r"^\s*(?:thanks|thank you|got it|okay|ok|cool|nice|great|perfect|understood)[.!\s]*$",
    r"^\s*(?:hi|hello|hey|yo)[.!\s]*$",
)


_BROAD_RECALL_PATTERNS = (
    r"\bwhat (?:were|was) we (?:discussing|working on|talking about)\b",
    r"\bwhere did we leave off\b",
    r"\bcontinue where we left off\b",
    r"\bwhat have we (?:decided|done|covered)\b",
    r"\bsummarize (?:what|everything) we\b",
    r"\bcatch me up\b",
    r"\bremind me where we were\b",
)


def _broad_recall_needs_checkpoints(text: str) -> bool:
    value = _clean(text)
    return any(re.search(pattern, value, flags=re.I) for pattern in _BROAD_RECALL_PATTERNS)

_RECENT_HISTORY_PATTERNS = (
    r"^\s*(?:and|also|then|so|but)\b",
    r"^\s*(?:what|how)\s+about\b",
    r"^\s*(?:why|how|when|where)\s*(?:\?|$)",
    r"^\s*(?:who|what)\s+(?:is|was|are|were)\s+(?:he|she|they)\b",
    r"\b(?:this|that|it|those|these|above|the previous|the last answer|your last answer)\b",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def classify_turn_relevance(
    text: str,
    *,
    has_current_attachments: bool = False,
    is_continue: bool = False,
    behavior_mode: str = "COMPLETE",
) -> dict[str, Any]:
    """Conservative ACI-7 foreground retrieval gate.

    The gate only bypasses Retrieval Gateway work when the current turn is clearly
    self-contained. It never suppresses current-turn attachment evidence, Continue,
    explicit recall, or action execution. This is intentionally deterministic so a
    local model is not required merely to decide whether retrieval is necessary.
    """
    value = _clean(text)
    low = value.lower()
    mode = str(behavior_mode or "COMPLETE").upper()

    if is_continue or low in {"continue", "continue.", "go on", "go on.", "keep going", "keep going."}:
        return _result(True, "continuation", "conversation_continuation", history_mode="checkpointed")
    if mode in {"RECALL", "CONTINUE"}:
        return _result(True, mode.lower(), f"behavior_mode_{mode.lower()}", history_mode="checkpointed", conversation_retrieval_required=(mode == "RECALL"))
    if any(marker in low for marker in _RECALL_MARKERS):
        history_mode = "checkpointed" if _broad_recall_needs_checkpoints(value) else "current_only"
        return _result(True, "explicit_recall", "recall_marker", history_mode=history_mode, conversation_retrieval_required=True)
    if has_current_attachments:
        history_mode = "recent" if _matches_any(value, _RECENT_HISTORY_PATTERNS) else "current_only"
        return _result(True, "direct_attachment", "current_turn_attachment", history_mode=history_mode)
    if mode == "ACT":
        history_mode = "recent" if _matches_any(value, _RECENT_HISTORY_PATTERNS) else "current_only"
        return _result(True, "act", "behavior_mode_act", history_mode=history_mode)

    if _matches_any(value, _PREFERENCE_PATTERNS):
        return _result(
            False,
            "preference_update",
            "self_contained_user_preference",
            claim_type="user_directive",
            writeback_relevant=True,
            history_mode="current_only",
        )
    if _matches_any(value, _ACK_PATTERNS):
        return _result(False, "conversation_ack", "self_contained_acknowledgement", claim_type="conversation_control", history_mode="current_only")

    # Very short user-authored directives that contain no recall/deictic dependency
    # should not search every Neo guide/memory lane merely because the planner has no
    # better domain classification. Avoid bypassing questions here; general questions
    # may still need project/current-source evidence.
    directive = bool(re.match(r"^\s*(?:please\s+)?(?:be|keep|use|write|answer|respond|format|make)\b", value, flags=re.I))
    deictic = bool(re.search(r"\b(?:this|that|it|those|these|above|previous)\b", low))
    if directive and not deictic and len(value) <= 320:
        return _result(False, "self_contained_instruction", "direct_instruction_without_referent", claim_type="user_directive", history_mode="current_only")

    # Retrieval need and conversation-history need are separate decisions. A
    # standalone factual lookup such as ``who is Kest?`` needs scoped evidence
    # but should not inherit an unrelated old request/checkpoint. Only explicit
    # conversational/deictic follow-ups receive recent raw history.
    history_mode = "recent" if _matches_any(value, _RECENT_HISTORY_PATTERNS) else "current_only"
    return _result(True, "contextual_request", "retrieval_may_be_needed", history_mode=history_mode)


def _result(
    retrieval_required: bool,
    intent: str,
    reason: str,
    *,
    claim_type: str = "general_fact",
    writeback_relevant: bool = False,
    history_mode: str = "current_only",
    conversation_retrieval_required: bool = False,
) -> dict[str, Any]:
    return {
        "schema_id": TURN_RELEVANCE_SCHEMA_ID,
        "version": TURN_RELEVANCE_VERSION,
        "retrieval_required": bool(retrieval_required),
        "intent": intent,
        "claim_type": claim_type,
        "reason": reason,
        "writeback_relevant": bool(writeback_relevant),
        "history_mode": history_mode if history_mode in {"current_only", "recent", "checkpointed"} else "current_only",
        "conversation_retrieval_required": bool(conversation_retrieval_required),
    }
