from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


GENERIC_KLEIN_VARIANTS = frozenset({"klein", "flux2_klein", "flux_2_klein"})
SPECIFIC_KLEIN_VARIANTS = frozenset({
    "klein_4b",
    "klein_9b",
    "klein_4b_distilled",
    "klein_9b_distilled",
})
ALL_KLEIN_VARIANTS = GENERIC_KLEIN_VARIANTS | SPECIFIC_KLEIN_VARIANTS


FLUX2_KLEIN_ENCODER_ALIAS_KEYS = (
    "qwen3_text_encoder",
    "text_encoder_1",
    "gguf_text_encoder_1",
    "gguf_text_encoder_primary",
    "text_encoder_primary",
    "clip_name",
)

FLUX2_KLEIN_FOREIGN_QWEN_KEYS = (
    "qwen_text_encoder",
    "qwen_mmproj",
    "mmproj",
    "mmproj_name",
)


@dataclass(frozen=True)
class Flux2KleinCompatibility:
    variant: str
    model_scale: str
    expected_encoder_scale: str
    encoder_family: str
    encoder_scale: str
    compatible: bool | None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "model_scale": self.model_scale,
            "expected_encoder_scale": self.expected_encoder_scale,
            "encoder_family": self.encoder_family,
            "encoder_scale": self.encoder_scale,
            "compatible": self.compatible,
            "message": self.message,
        }


def normalize_flux2_klein_variant(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_flux2_klein_variant(value: Any) -> bool:
    return normalize_flux2_klein_variant(value) in ALL_KLEIN_VARIANTS


def is_generic_flux2_klein_variant(value: Any) -> bool:
    return normalize_flux2_klein_variant(value) in GENERIC_KLEIN_VARIANTS


def is_flux2_klein_model_name(value: Any) -> bool:
    text = str(value or "").strip().lower().replace("_", "-")
    return "klein" in text and ("flux-2" in text or "flux2" in text or text.startswith("klein"))


def flux2_klein_model_scale(model_name: Any) -> str:
    if not is_flux2_klein_model_name(model_name):
        return ""
    text = str(model_name or "").strip().lower().replace("_", "-")
    if re.search(r"(?:^|[^0-9a-z])9b(?:[^0-9a-z]|$)", text):
        return "9b"
    if re.search(r"(?:^|[^0-9a-z])4b(?:[^0-9a-z]|$)", text):
        return "4b"
    return ""


def variant_scale(variant: Any) -> str:
    normalized = normalize_flux2_klein_variant(variant)
    if "9b" in normalized:
        return "9b"
    if "4b" in normalized:
        return "4b"
    return ""


def resolve_flux2_klein_variant(raw_variant: Any, model_name: Any) -> str | None:
    """Resolve the canonical Klein size variant with model identity as authority.

    The selected diffusion model is the tensor architecture that Comfy will load,
    so a filename that explicitly identifies 4B/9B wins over stale or generic UI
    state. Generic Klein aliases never terminate size resolution early.
    """

    normalized = normalize_flux2_klein_variant(raw_variant)
    model_scale = flux2_klein_model_scale(model_name)
    if model_scale:
        distilled = "distill" in normalized
        return f"klein_{model_scale}{'_distilled' if distilled else ''}"
    if normalized in SPECIFIC_KLEIN_VARIANTS:
        return normalized
    if normalized in GENERIC_KLEIN_VARIANTS:
        return normalized
    if is_flux2_klein_model_name(model_name):
        # Preserve the historical 4B fallback only for Klein filenames that do
        # not expose a size marker. Known 4B/9B filenames are resolved above.
        return "klein_4b"
    return None


def default_flux2_klein_encoder(variant: Any) -> str:
    return "qwen_3_8b_fp8mixed.safetensors" if variant_scale(variant) == "9b" else "qwen_3_4b.safetensors"


def default_flux2_klein_model(variant: Any, *, file_format: str = "gguf") -> str:
    scale = variant_scale(variant) or "4b"
    if str(file_format or "gguf").lower() == "gguf":
        return f"flux-2-klein-{scale}-fp8.gguf"
    return f"flux-2-klein-{scale}.safetensors"


def flux2_klein_expected_encoder_scale(variant: Any) -> str:
    scale = variant_scale(variant)
    if scale == "9b":
        return "8b"
    if scale == "4b":
        return "4b"
    return ""


def classify_qwen_encoder(value: Any) -> tuple[str, str]:
    """Return (family, scale) for recognizable Qwen encoder filenames.

    This is deliberately filename-based and conservative. Unknown custom aliases
    remain unclassified rather than being rejected. Qwen2/2.5 and MMProj assets
    are recognized so they can be blocked from the Klein Qwen3 route.
    """

    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        return "", ""
    if "mmproj" in text or "mm_proj" in text or "projector" in text:
        return "mmproj", ""
    if "qwen2.5" in text or "qwen2_5" in text or "qwen_2_5" in text:
        return "qwen2.5", _encoder_scale_from_text(text)
    if "qwen2" in text or "qwen_2" in text:
        return "qwen2", _encoder_scale_from_text(text)
    if "qwen3" in text or "qwen_3" in text:
        return "qwen3", _encoder_scale_from_text(text)
    if "qwen" in text or text.startswith("qw"):
        return "qwen_unknown", _encoder_scale_from_text(text)
    return "", ""


def _encoder_scale_from_text(text: str) -> str:
    for size in ("32b", "30b", "14b", "8b", "7b", "4b", "3b", "1.7b", "0.6b"):
        escaped = re.escape(size).replace(r"\.", r"[._]")
        if re.search(rf"(?:^|[^0-9a-z]){escaped}(?:[^0-9a-z]|$)", text):
            return size
    return ""


def check_flux2_klein_compatibility(variant: Any, model_name: Any, text_encoder: Any) -> Flux2KleinCompatibility:
    canonical = resolve_flux2_klein_variant(variant, model_name) or normalize_flux2_klein_variant(variant)
    model_scale = flux2_klein_model_scale(model_name) or variant_scale(canonical)
    expected_encoder_scale = flux2_klein_expected_encoder_scale(canonical)
    encoder_family, encoder_scale = classify_qwen_encoder(text_encoder)

    if not str(text_encoder or "").strip():
        return Flux2KleinCompatibility(canonical, model_scale, expected_encoder_scale, "", "", None, "")

    if encoder_family in {"qwen2", "qwen2.5", "mmproj"}:
        return Flux2KleinCompatibility(
            canonical,
            model_scale,
            expected_encoder_scale,
            encoder_family,
            encoder_scale,
            False,
            "FLUX.2 Klein requires a Qwen3 text encoder; Qwen2/Qwen2.5-VL/MMProj assets are not compatible with the Klein Flux2 text-conditioning path.",
        )

    if encoder_family == "qwen3" and expected_encoder_scale and encoder_scale and encoder_scale != expected_encoder_scale:
        model_label = f"Klein {model_scale.upper()}" if model_scale else "the selected Klein model"
        return Flux2KleinCompatibility(
            canonical,
            model_scale,
            expected_encoder_scale,
            encoder_family,
            encoder_scale,
            False,
            f"FLUX.2 {model_label} requires a Qwen3-{expected_encoder_scale.upper()} text encoder, but the selected encoder appears to be Qwen3-{encoder_scale.upper()}.",
        )

    if encoder_family == "qwen3":
        return Flux2KleinCompatibility(canonical, model_scale, expected_encoder_scale, encoder_family, encoder_scale, True, "")

    # Custom aliases cannot be proven compatible from their filename. Allow the
    # route, but expose an indeterminate compatibility state for diagnostics.
    return Flux2KleinCompatibility(
        canonical,
        model_scale,
        expected_encoder_scale,
        encoder_family,
        encoder_scale,
        None,
        "Neo could not infer the Qwen3 encoder family/size from the selected filename; compatibility will be validated by ComfyUI.",
    )


def reconcile_flux2_klein_encoder_params(
    params: dict[str, Any] | None,
    model_name: Any,
    *,
    default_if_missing: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize all Klein encoder aliases to one architecture-compatible value.

    M14.1 treats ``qwen3_text_encoder`` as the canonical runtime field, but old
    presets and earlier UI builds can still carry independent values in
    ``text_encoder_1`` / GGUF aliases. Reconciliation is compatibility-first:
    an alias that matches the selected Klein model size wins over a stale alias
    that does not. When multiple compatible aliases exist, the canonical Qwen3
    field has precedence.

    The function is intentionally pure: callers decide whether to persist the
    normalized mapping. Foreign Qwen Image/Edit MMProj fields are removed from
    the returned params so they cannot leak into the Klein route.
    """

    clean = dict(params or {})
    raw_variant = clean.get("flux_variant") or clean.get("variant") or "flux2_klein"
    canonical_variant = resolve_flux2_klein_variant(raw_variant, model_name) or normalize_flux2_klein_variant(raw_variant) or "flux2_klein"
    expected_scale = flux2_klein_expected_encoder_scale(canonical_variant)

    candidates: list[tuple[str, str, Flux2KleinCompatibility]] = []
    seen_values: set[str] = set()
    for key in FLUX2_KLEIN_ENCODER_ALIAS_KEYS:
        value = str(clean.get(key) or "").strip()
        if not value:
            continue
        normalized_value = value.casefold()
        if normalized_value in seen_values:
            continue
        seen_values.add(normalized_value)
        candidates.append((key, value, check_flux2_klein_compatibility(canonical_variant, model_name, value)))

    compatible = [item for item in candidates if item[2].compatible is True]
    selected_key = ""
    selected_value = ""
    selection_reason = "missing"
    if compatible:
        canonical_match = next((item for item in compatible if item[0] == "qwen3_text_encoder"), None)
        selected_key, selected_value, _ = canonical_match or compatible[0]
        selection_reason = "compatible_alias"
    elif candidates:
        selected_key, selected_value, _ = candidates[0]
        selection_reason = "first_declared_alias"
    elif default_if_missing:
        selected_key = "default"
        selected_value = default_flux2_klein_encoder(canonical_variant)
        selection_reason = "default_for_variant"

    before = {key: str(clean.get(key) or "") for key in FLUX2_KLEIN_ENCODER_ALIAS_KEYS if str(clean.get(key) or "").strip()}
    foreign_before = {key: str(clean.get(key) or "") for key in FLUX2_KLEIN_FOREIGN_QWEN_KEYS if str(clean.get(key) or "").strip()}

    clean["flux_variant"] = canonical_variant
    if selected_value:
        clean["qwen3_text_encoder"] = selected_value
        clean["text_encoder_1"] = selected_value
        clean["gguf_text_encoder_1"] = selected_value
        clean["gguf_text_encoder_primary"] = selected_value
        clean["text_encoder_primary"] = selected_value
    else:
        for key in ("qwen3_text_encoder", "text_encoder_1", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_primary"):
            clean.pop(key, None)

    for key in FLUX2_KLEIN_FOREIGN_QWEN_KEYS:
        clean.pop(key, None)

    after = {key: str(clean.get(key) or "") for key in ("qwen3_text_encoder", "text_encoder_1", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_primary") if str(clean.get(key) or "").strip()}
    conflicting_values = sorted({value for value in before.values() if value and value != selected_value}, key=str.casefold)
    repaired = bool(foreign_before or conflicting_values or (selected_value and any(value != selected_value for value in before.values())))

    metadata = {
        "schema": "neo.image.flux2_klein.encoder_reconciliation.v1",
        "canonical_field": "qwen3_text_encoder",
        "variant": canonical_variant,
        "model_scale": flux2_klein_model_scale(model_name) or variant_scale(canonical_variant),
        "expected_encoder_scale": expected_scale,
        "selected_source": selected_key,
        "selected_encoder": selected_value,
        "selection_reason": selection_reason,
        "aliases_before": before,
        "aliases_after": after,
        "conflicting_alias_values": conflicting_values,
        "foreign_qwen_fields_cleared": sorted(foreign_before),
        "repaired": repaired,
    }
    return clean, metadata
