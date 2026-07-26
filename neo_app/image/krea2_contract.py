from __future__ import annotations

from dataclasses import dataclass
from typing import Any


KREA2_RAW_FAMILIES = {"krea2", "krea_2", "krea2_raw", "krea_2_raw", "krea2_normal", "krea_2_normal", "krea2_base", "krea_2_base", "raw"}
KREA2_TURBO_FAMILIES = {"krea2_turbo", "krea_2_turbo", "krea2turbo", "turbo"}


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_krea2_family(value: Any) -> bool:
    normalized = _normalize(value)
    return normalized in KREA2_RAW_FAMILIES or normalized in KREA2_TURBO_FAMILIES


def is_krea2_model_name(value: Any) -> bool:
    text = _normalize(value)
    return bool(text and ("krea2" in text or "krea_2" in text))


def krea2_variant_from_model(value: Any) -> str | None:
    text = _normalize(value)
    if not is_krea2_model_name(text):
        return None
    if "turbo" in text:
        return "turbo"
    if "raw" in text or "base" in text:
        return "raw"
    return None


def resolve_krea2_variant(family_or_variant: Any, model_name: Any = "") -> str:
    """Resolve canonical Krea 2 variant with concrete model filenames authoritative.

    A concrete model filename wins over stale UI family/variant state.  When a
    model filename only identifies Krea 2 without RAW/Turbo, the visible family
    determines the variant; RAW is the conservative fallback.
    """

    model_variant = krea2_variant_from_model(model_name)
    if model_variant:
        return model_variant
    normalized = _normalize(family_or_variant)
    if normalized in KREA2_TURBO_FAMILIES or "turbo" in normalized:
        return "turbo"
    return "raw"


def canonical_krea2_family(family_or_variant: Any, model_name: Any = "") -> str:
    return "krea2_turbo" if resolve_krea2_variant(family_or_variant, model_name) == "turbo" else "krea2"


def classify_krea2_text_encoder(value: Any) -> str:
    text = _normalize(value)
    if not text:
        return "missing"
    if "mmproj" in text or "projector" in text:
        return "mmproj"
    if text.endswith(".gguf"):
        if ("qwen3vl" in text or "qwen3_vl" in text or "qwen_3_vl" in text) and "4b" in text:
            return "qwen3vl_4b_gguf"
        return "gguf_other"
    if ("qwen3vl" in text or "qwen3_vl" in text or "qwen_3_vl" in text) and "4b" in text:
        return "qwen3vl_4b_native"
    if ("qwen3vl" in text or "qwen3_vl" in text or "qwen_3_vl" in text) and any(scale in text for scale in ("8b", "30b", "32b", "235b")):
        return "qwen3vl_wrong_scale"
    if "qwen2" in text or "qwen_2" in text or "qwen2.5" in text or "qwen25" in text:
        return "qwen2_family"
    if "qwen3" in text or "qwen_3" in text:
        return "qwen3_plain"
    return "unknown"


def classify_krea2_vae(value: Any) -> str:
    text = _normalize(value)
    if not text:
        return "missing"
    if "qwen_image_vae" in text or ("qwen" in text and "image" in text and "vae" in text):
        return "qwen_image_vae"
    if "flux" in text or text in {"ae.safetensors", "ae.gguf"} or text.startswith("ae_"):
        return "foreign_flux_ae"
    if "sdxl" in text or "sd15" in text or "sd_vae" in text:
        return "foreign_sd_vae"
    return "unknown"


@dataclass(frozen=True)
class Krea2Compatibility:
    compatible: bool | None
    variant: str
    text_encoder_kind: str
    vae_kind: str
    loader: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "variant": self.variant,
            "text_encoder_kind": self.text_encoder_kind,
            "vae_kind": self.vae_kind,
            "loader": self.loader,
            "message": self.message,
        }


def check_krea2_compatibility(
    family_or_variant: Any,
    model_name: Any,
    text_encoder: Any,
    vae_name: Any,
    *,
    loader: Any = "diffusion_model",
) -> Krea2Compatibility:
    variant = resolve_krea2_variant(family_or_variant, model_name)
    loader_id = _normalize(loader) or "diffusion_model"
    encoder_kind = classify_krea2_text_encoder(text_encoder)
    vae_kind = classify_krea2_vae(vae_name)

    normalized_family = _normalize(family_or_variant)
    expected_variant = None
    if normalized_family in KREA2_TURBO_FAMILIES or normalized_family == "turbo":
        expected_variant = "turbo"
    elif normalized_family in KREA2_RAW_FAMILIES or normalized_family == "raw":
        expected_variant = "raw"
    model_variant = krea2_variant_from_model(model_name)
    if expected_variant and model_variant and expected_variant != model_variant:
        return Krea2Compatibility(
            False,
            expected_variant,
            encoder_kind,
            vae_kind,
            loader_id,
            f"Krea 2 family/model mismatch: the selected family requires {expected_variant.upper()}, but the selected model filename resolves to {model_variant.upper()}.",
        )

    if encoder_kind == "missing":
        return Krea2Compatibility(False, variant, encoder_kind, vae_kind, loader_id, "Krea 2 requires a Qwen3-VL-4B text encoder.")
    if encoder_kind == "mmproj":
        return Krea2Compatibility(False, variant, encoder_kind, vae_kind, loader_id, "Krea 2 does not use an MMProj sidecar; select the Qwen3-VL-4B text encoder directly.")
    if encoder_kind == "qwen3vl_4b_gguf":
        return Krea2Compatibility(
            False,
            variant,
            encoder_kind,
            vae_kind,
            loader_id,
            "Krea 2 M16 keeps the Qwen3-VL-4B text encoder native/safetensors even when the diffusion transformer is GGUF. Generic GGUF text encoders are gated because Krea 2 requires the 12-layer Qwen3-VL feature stack used by CLIPLoader(type=krea2).",
        )
    if encoder_kind in {"gguf_other", "qwen3vl_wrong_scale", "qwen2_family", "qwen3_plain"}:
        return Krea2Compatibility(False, variant, encoder_kind, vae_kind, loader_id, "Krea 2 requires the Qwen3-VL-4B text encoder loaded with CLIPLoader(type=krea2); the selected encoder is not compatible.")

    if vae_kind == "missing":
        return Krea2Compatibility(False, variant, encoder_kind, vae_kind, loader_id, "Krea 2 requires the Qwen Image VAE (qwen_image_vae.safetensors or a compatible equivalent).")
    if vae_kind in {"foreign_flux_ae", "foreign_sd_vae"}:
        return Krea2Compatibility(False, variant, encoder_kind, vae_kind, loader_id, "Krea 2 uses the Qwen Image VAE; the selected VAE/AE appears to belong to another architecture.")

    if encoder_kind == "qwen3vl_4b_native" and vae_kind == "qwen_image_vae":
        return Krea2Compatibility(True, variant, encoder_kind, vae_kind, loader_id, "Krea 2 model, Qwen3-VL-4B encoder, and Qwen Image VAE are compatible.")

    return Krea2Compatibility(
        None,
        variant,
        encoder_kind,
        vae_kind,
        loader_id,
        "Krea 2 could not fully classify one or more custom asset filenames. Runtime will keep the Krea 2 architecture contract and let ComfyUI validate the custom files.",
    )
