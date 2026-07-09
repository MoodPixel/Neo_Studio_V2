"""Seeded backend wildcard resolver for Neo Image Wildcards.

Phase G activates deterministic prompt resolution for V1-compatible wildcard
syntax while keeping Wildcards prompt-only and provider-neutral.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from neo_extensions.built_in.wildcards.backend.payload_schema import normalize_wildcards_payload
from neo_extensions.built_in.wildcards.backend.wildcard_store import load_wildcard_values

EXTENSION_ID = "wildcards"
TOKEN_PATTERN_DESCRIPTION = "__token__ / __folder/token__"
INLINE_CHOICE_DESCRIPTION = "{option A|option B|option C}"
MAX_EXPANSION_PASSES = 24
INLINE_CHOICE_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
WILDCARD_TOKEN_RE = re.compile(r"__([A-Za-z0-9][A-Za-z0-9_./\- ]*?)__")


@dataclass
class WildcardResolutionStats:
    resolved_tokens: list[str] = field(default_factory=list)
    missing_tokens: list[str] = field(default_factory=list)
    inline_choice_count: int = 0
    file_choice_count: int = 0
    passes: int = 0
    max_passes_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def seed_number(seed_like: Any = "", *, variant_offset: int = 0, channel: str = "") -> int:
    """Return a stable integer seed from any value plus variant/channel data."""

    raw = f"neo-wildcards-v1|{seed_like if seed_like is not None else ''}|{int(variant_offset or 0)}|{channel}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:16], 16)


def wildcard_rng(seed: Any = "", *, variant_offset: int = 0, channel: str = "") -> random.Random:
    """Return a deterministic RNG for wildcard expansion."""

    return random.Random(seed_number(seed, variant_offset=variant_offset, channel=channel))


def _clean_token(raw: Any) -> str:
    token = str(raw or "").strip()
    if token.startswith("__") and token.endswith("__") and len(token) > 4:
        token = token[2:-2]
    token = token.replace("\\", "/").strip().strip("/")
    while "//" in token:
        token = token.replace("//", "/")
    return token


def parse_wildcard_tokens(text: Any) -> list[str]:
    """Return unique file wildcard tokens referenced by prompt text."""

    tokens: list[str] = []
    for match in WILDCARD_TOKEN_RE.finditer(str(text or "")):
        token = _clean_token(match.group(1))
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _dedupe_extend(target: list[str], values: list[str]) -> None:
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in target:
            target.append(clean)


def resolve_inline_choices_once(text: str, rng: random.Random, stats: WildcardResolutionStats | None = None) -> str:
    """Resolve one pass of V1 inline choice syntax: ``{a|b|c}``."""

    def replace(match: re.Match[str]) -> str:
        options = [part.strip() for part in match.group(1).split("|") if part.strip()]
        if not options:
            return match.group(0)
        if stats is not None:
            stats.inline_choice_count += 1
        return rng.choice(options)

    return INLINE_CHOICE_RE.sub(replace, text)


def resolve_file_wildcards_once(
    text: str,
    rng: random.Random,
    *,
    root: str | Path | None = None,
    repo_root: str | Path | None = None,
    stats: WildcardResolutionStats | None = None,
) -> str:
    """Resolve one pass of ``__token__`` file wildcards."""

    def replace(match: re.Match[str]) -> str:
        token = _clean_token(match.group(1))
        if not token:
            return match.group(0)
        values = load_wildcard_values(token, root, repo_root=repo_root)
        choices = list(values.values or [])
        if not choices:
            if stats is not None:
                _dedupe_extend(stats.missing_tokens, [token])
            return match.group(0)
        if stats is not None:
            _dedupe_extend(stats.resolved_tokens, [token])
            stats.file_choice_count += 1
        return rng.choice(choices)

    return WILDCARD_TOKEN_RE.sub(replace, text)


def resolve_wildcard_text(
    text: Any,
    *,
    seed: Any = "",
    variant_offset: int = 0,
    max_passes: int = MAX_EXPANSION_PASSES,
    root: str | Path | None = None,
    repo_root: str | Path | None = None,
    channel: str = "positive",
) -> dict[str, Any]:
    """Resolve inline and file wildcards until stable or the pass limit is hit."""

    original = str(text or "")
    current = original
    passes = max(1, min(int(max_passes or MAX_EXPANSION_PASSES), 48))
    rng = wildcard_rng(seed, variant_offset=variant_offset, channel=channel)
    stats = WildcardResolutionStats()

    for index in range(passes):
        before = current
        current = resolve_inline_choices_once(current, rng, stats)
        current = resolve_file_wildcards_once(current, rng, root=root, repo_root=repo_root, stats=stats)
        stats.passes = index + 1
        if current == before:
            break
    else:
        stats.max_passes_reached = True

    return {
        "source": original,
        "effective": current,
        "changed": current != original,
        **stats.to_dict(),
        "seed": seed,
        "variant_offset": int(variant_offset or 0),
        "channel": channel,
        "max_passes": passes,
        "prompt_only": True,
        "provider_graph_mutation": False,
    }


def _wildcards_payload_from_extensions(extensions: Any) -> dict[str, Any] | None:
    if not isinstance(extensions, Mapping):
        return None
    payloads = extensions.get("payloads") if isinstance(extensions.get("payloads"), Mapping) else extensions
    block = payloads.get(EXTENSION_ID) if isinstance(payloads, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else None


def apply_wildcard_prompt_extension(
    positive_prompt: Any,
    negative_prompt: Any,
    extensions: Any,
    *,
    seed: Any = "",
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Apply Wildcards from ``extensions.payloads.wildcards`` to prompt text."""

    original_positive = str(positive_prompt or "")
    original_negative = str(negative_prompt or "")
    payload = _wildcards_payload_from_extensions(extensions)
    if payload is None:
        return {
            "extension_id": EXTENSION_ID,
            "enabled": False,
            "applied": False,
            "reason": "payload_missing",
            "effective_positive": original_positive,
            "effective_negative": original_negative,
            "metadata": {},
        }

    normalized = normalize_wildcards_payload(payload)
    params = normalized.get("params") if isinstance(normalized.get("params"), Mapping) else {}
    inputs = normalized.get("inputs") if isinstance(normalized.get("inputs"), Mapping) else {}
    if not normalized.get("enabled"):
        return {
            "extension_id": EXTENSION_ID,
            "enabled": False,
            "applied": False,
            "reason": "disabled",
            "effective_positive": original_positive,
            "effective_negative": original_negative,
            "metadata": {"normalized_payload": normalized},
        }
    if not params.get("auto_resolve", True):
        return {
            "extension_id": EXTENSION_ID,
            "enabled": True,
            "applied": False,
            "reason": "auto_resolve_disabled",
            "effective_positive": original_positive,
            "effective_negative": original_negative,
            "metadata": {"normalized_payload": normalized},
        }

    root = inputs.get("root") or ""
    base_seed = seed if params.get("use_seed", True) else "wildcards-unseeded-preview"
    variant_offset = int(params.get("variant_offset") or 0)
    max_passes = int(params.get("max_passes") or MAX_EXPANSION_PASSES)

    positive_result = resolve_wildcard_text(
        original_positive,
        seed=base_seed,
        variant_offset=variant_offset * 2,
        max_passes=max_passes,
        root=root,
        repo_root=repo_root,
        channel="positive",
    )
    negative_result = resolve_wildcard_text(
        original_negative,
        seed=base_seed,
        variant_offset=variant_offset * 2 + 1,
        max_passes=max_passes,
        root=root,
        repo_root=repo_root,
        channel="negative",
    )
    resolved_tokens: list[str] = []
    missing_tokens: list[str] = []
    _dedupe_extend(resolved_tokens, list(positive_result.get("resolved_tokens") or []))
    _dedupe_extend(resolved_tokens, list(negative_result.get("resolved_tokens") or []))
    _dedupe_extend(missing_tokens, list(positive_result.get("missing_tokens") or []))
    _dedupe_extend(missing_tokens, list(negative_result.get("missing_tokens") or []))
    changed = bool(positive_result.get("changed") or negative_result.get("changed"))
    metadata = {
        "enabled": True,
        "prompt_only": True,
        "phase": "G-backend-resolver-hook",
        "resolution_policy": "seeded_replace_tokens",
        "resolution_order": "before_style_stack",
        "runtime_resolution_status": "applied_phase_g",
        "provider_graph_mutation": False,
        "provider_graph_patch_path": "disabled_for_wildcards",
        "seed_used": base_seed,
        "use_seed": bool(params.get("use_seed", True)),
        "variant_offset": variant_offset,
        "max_passes": max_passes,
        "root": str(root or ""),
        "resolved_tokens": resolved_tokens,
        "missing_tokens": missing_tokens,
        "inline_choice_count": int(positive_result.get("inline_choice_count") or 0) + int(negative_result.get("inline_choice_count") or 0),
        "file_choice_count": int(positive_result.get("file_choice_count") or 0) + int(negative_result.get("file_choice_count") or 0),
        "source_positive": original_positive,
        "source_negative": original_negative,
        "effective_positive": positive_result.get("effective") or original_positive,
        "effective_negative": negative_result.get("effective") or original_negative,
        "positive": positive_result,
        "negative": negative_result,
    }
    return {
        "extension_id": EXTENSION_ID,
        "enabled": True,
        "applied": True,
        "changed": changed,
        "reason": "resolved" if changed else "no_tokens_or_no_changes",
        "effective_positive": positive_result.get("effective") or original_positive,
        "effective_negative": negative_result.get("effective") or original_negative,
        "metadata": metadata,
    }


def phase_g_resolver_status() -> dict[str, object]:
    """Return Phase G resolver status for diagnostics and tests."""

    return {
        "extension_id": EXTENSION_ID,
        "phase": "G-backend-resolver-hook",
        "implemented": True,
        "prompt_only": True,
        "provider_graph_mutation": False,
        "token_pattern": TOKEN_PATTERN_DESCRIPTION,
        "inline_choice_pattern": INLINE_CHOICE_DESCRIPTION,
        "max_expansion_passes": MAX_EXPANSION_PASSES,
        "runtime_resolution": True,
        "resolution_order": "before_style_stack",
    }


# Backward-compatible alias retained for earlier phase tests.
def phase_b_resolver_status() -> dict[str, object]:
    status = phase_g_resolver_status()
    status["phase_b_compat"] = True
    return status
