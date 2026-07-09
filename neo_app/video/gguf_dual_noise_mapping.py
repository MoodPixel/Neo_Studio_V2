from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

PHASE: Final[str] = "V-G4"
SCHEMA_VERSION: Final[str] = "neo.video.gguf_dual_noise_mapping.vg4"

HIGH_NOISE_TOKENS: Final[tuple[str, ...]] = (
    "high_noise",
    "high-noise",
    "highnoise",
    "noise_high",
    "high",
    "hn",
)
LOW_NOISE_TOKENS: Final[tuple[str, ...]] = (
    "low_noise",
    "low-noise",
    "lownoise",
    "noise_low",
    "low",
    "ln",
)
WAN_22_TOKENS: Final[tuple[str, ...]] = ("wan2.2", "wan22", "wan_2.2", "wan_22", "wan")
I2V_TOKENS: Final[tuple[str, ...]] = ("i2v", "image_to_video", "img2vid")
MODEL_SIZE_TOKENS: Final[tuple[str, ...]] = ("14b", "14_b")


@dataclass(frozen=True)
class DualNoiseCandidate:
    model_name: str
    role: str
    score: int
    reasons: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "role": self.role,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class DualNoiseModelMapping:
    high_noise_model: str
    low_noise_model: str
    requested_high_noise_model: str
    requested_low_noise_model: str
    high_role_detected: str
    low_role_detected: str
    swap_applied: bool
    same_model_selected: bool
    high_candidates: tuple[DualNoiseCandidate, ...]
    low_candidates: tuple[DualNoiseCandidate, ...]
    ambiguous_candidates: tuple[DualNoiseCandidate, ...]
    diagnostics: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.high_noise_model and self.low_noise_model and not self.same_model_selected)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": PHASE,
            "ready": self.ready,
            "strategy": "filename_role_classification_with_swap_guard",
            "models": {
                "high_noise_model": self.high_noise_model,
                "low_noise_model": self.low_noise_model,
                "requested_high_noise_model": self.requested_high_noise_model,
                "requested_low_noise_model": self.requested_low_noise_model,
            },
            "roles": {
                "high_noise": self.high_role_detected,
                "low_noise": self.low_role_detected,
            },
            "swap_applied": self.swap_applied,
            "same_model_selected": self.same_model_selected,
            "candidate_counts": {
                "high_noise": len(self.high_candidates),
                "low_noise": len(self.low_candidates),
                "ambiguous": len(self.ambiguous_candidates),
            },
            "candidates": {
                "high_noise": [candidate.payload() for candidate in self.high_candidates],
                "low_noise": [candidate.payload() for candidate in self.low_candidates],
                "ambiguous": [candidate.payload() for candidate in self.ambiguous_candidates],
            },
            "diagnostics": list(self.diagnostics),
        }


def _normalize(value: str) -> str:
    return str(value or "").casefold().replace("\\", "/")


def _token_present(value: str, token: str) -> bool:
    value = _normalize(value)
    token = _normalize(token)
    compact_value = value.replace("_", "").replace("-", "").replace(" ", "")
    compact_token = token.replace("_", "").replace("-", "").replace(" ", "")
    if token in value:
        return True
    return bool(compact_token and compact_token in compact_value)


def _score_for(value: str, role_tokens: tuple[str, ...]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for token in role_tokens:
        if _token_present(value, token):
            score += 12 if "noise" in token else 8
            reasons.append(f"role token: {token}")
            break
    for token in WAN_22_TOKENS:
        if _token_present(value, token):
            score += 3
            reasons.append(f"wan token: {token}")
            break
    for token in I2V_TOKENS:
        if _token_present(value, token):
            score += 2
            reasons.append(f"i2v token: {token}")
            break
    for token in MODEL_SIZE_TOKENS:
        if _token_present(value, token):
            score += 1
            reasons.append(f"size token: {token}")
            break
    if _normalize(value).endswith(".gguf"):
        score += 1
        reasons.append("gguf extension")
    return score, reasons


def classify_noise_role(model_name: str) -> str:
    high_score, _ = _score_for(model_name, HIGH_NOISE_TOKENS)
    low_score, _ = _score_for(model_name, LOW_NOISE_TOKENS)
    # Subtract shared non-role points before comparing when neither role token is present.
    high_role_hit = any(_token_present(model_name, token) for token in HIGH_NOISE_TOKENS)
    low_role_hit = any(_token_present(model_name, token) for token in LOW_NOISE_TOKENS)
    if high_role_hit and not low_role_hit:
        return "high_noise"
    if low_role_hit and not high_role_hit:
        return "low_noise"
    if high_role_hit and low_role_hit:
        if high_score > low_score:
            return "high_noise"
        if low_score > high_score:
            return "low_noise"
        return "ambiguous"
    return "ambiguous"


def _candidate_for(model_name: str) -> DualNoiseCandidate:
    role = classify_noise_role(model_name)
    role_tokens = HIGH_NOISE_TOKENS if role == "high_noise" else LOW_NOISE_TOKENS if role == "low_noise" else ()
    score, reasons = _score_for(model_name, role_tokens)
    return DualNoiseCandidate(model_name=model_name, role=role, score=score, reasons=tuple(reasons))


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = _normalize(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _best(candidates: tuple[DualNoiseCandidate, ...], fallback: str) -> str:
    if not candidates:
        return fallback
    ordered = sorted(candidates, key=lambda item: (-item.score, item.model_name.casefold()))
    return ordered[0].model_name


def resolve_wan22_dual_noise_mapping(
    available_models: list[str] | tuple[str, ...],
    *,
    fallback_high_noise_model: str,
    fallback_low_noise_model: str,
    requested_high_noise_model: str | None = None,
    requested_low_noise_model: str | None = None,
) -> DualNoiseModelMapping:
    values = _unique(available_models)
    candidates = tuple(_candidate_for(value) for value in values)
    high_candidates = tuple(candidate for candidate in candidates if candidate.role == "high_noise")
    low_candidates = tuple(candidate for candidate in candidates if candidate.role == "low_noise")
    ambiguous_candidates = tuple(candidate for candidate in candidates if candidate.role == "ambiguous")
    diagnostics: list[str] = []

    selected_high = str(requested_high_noise_model or "").strip() or _best(high_candidates, fallback_high_noise_model)
    selected_low = str(requested_low_noise_model or "").strip() or _best(low_candidates, fallback_low_noise_model)
    requested_high = selected_high
    requested_low = selected_low

    if values and not high_candidates:
        diagnostics.append("No GGUF model with a clear high-noise filename token was found in ComfyUI /object_info.")
    if values and not low_candidates:
        diagnostics.append("No GGUF model with a clear low-noise filename token was found in ComfyUI /object_info.")

    high_role = classify_noise_role(selected_high)
    low_role = classify_noise_role(selected_low)
    swap_applied = False
    if high_role == "low_noise" and low_role == "high_noise":
        selected_high, selected_low = selected_low, selected_high
        high_role, low_role = "high_noise", "low_noise"
        swap_applied = True
        diagnostics.append("Requested WAN GGUF dual-noise models looked inverted; Neo swapped high/low assignments before compile.")

    same = bool(selected_high and selected_low and _normalize(selected_high) == _normalize(selected_low))
    if same:
        diagnostics.append("High-noise and low-noise GGUF selections resolve to the same model; dual-noise route requires two role-specific model files.")
    if high_role == "low_noise":
        diagnostics.append(f"High-noise slot received a low-noise-looking model: {selected_high}")
    if low_role == "high_noise":
        diagnostics.append(f"Low-noise slot received a high-noise-looking model: {selected_low}")
    if high_role == "ambiguous":
        diagnostics.append(f"High-noise slot model role is ambiguous from filename: {selected_high}")
    if low_role == "ambiguous":
        diagnostics.append(f"Low-noise slot model role is ambiguous from filename: {selected_low}")

    return DualNoiseModelMapping(
        high_noise_model=selected_high,
        low_noise_model=selected_low,
        requested_high_noise_model=requested_high,
        requested_low_noise_model=requested_low,
        high_role_detected=high_role,
        low_role_detected=low_role,
        swap_applied=swap_applied,
        same_model_selected=same,
        high_candidates=high_candidates,
        low_candidates=low_candidates,
        ambiguous_candidates=ambiguous_candidates,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )
