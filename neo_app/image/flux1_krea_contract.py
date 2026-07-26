from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FLUX1_KREA_VARIANTS = {"krea", "krea_dev", "flux1_krea", "flux_1_krea", "flux1_krea_dev", "flux_1_krea_dev"}


def normalize_flux1_variant(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_flux1_krea_variant(value: Any) -> bool:
    return normalize_flux1_variant(value) in FLUX1_KREA_VARIANTS


def is_flux1_krea_model_name(value: Any) -> bool:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text or "krea" not in text:
        return False
    # Phase M16: Krea 2 is a separate native architecture, not FLUX.1 Krea.
    if "krea2" in text or "krea-2" in text:
        return False
    return "flux" in text or text.startswith("krea")


def resolve_flux1_variant(value: Any, model_name: Any = "") -> str:
    """Resolve the effective Flux 1 variant without creating a new top-level family.

    The selected model is authoritative for Krea because it is the actual tensor
    architecture Comfy will load. Generic/custom Flux models remain on their
    submitted variant so M15 does not steal base Dev/Schnell/Fill routes.
    """

    if is_flux1_krea_model_name(model_name):
        return "krea_dev"
    normalized = normalize_flux1_variant(value)
    if normalized in FLUX1_KREA_VARIANTS:
        return "krea_dev"
    return normalized or "dev"


def is_flux1_krea_route(value: Any, model_name: Any = "") -> bool:
    if is_flux1_krea_model_name(model_name):
        return True
    model_text = str(model_name or "").strip().lower()
    # A concrete non-Krea model should beat stale Krea UI state. If no concrete
    # model is selected yet, the explicit Krea variant remains useful for UI and
    # preflight routing.
    if model_text and model_text not in {"provider_default", "automatic", "auto", "none"}:
        return False
    return is_flux1_krea_variant(value)


def classify_flux1_text_encoder(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(".", "_")
    if not text:
        return "missing"
    if "qwen" in text or "mmproj" in text:
        return "foreign_qwen"
    if "t5" in text and ("xxl" in text or "v1_1" in text or "v1.1" in str(value or "").lower()):
        return "t5xxl"
    if "clip_l" in text or "clip_l" in text.replace("__", "_") or "vit_l_14" in text:
        return "clip_l"
    return "unknown"


@dataclass(frozen=True)
class Flux1KreaCompatibility:
    compatible: bool | None
    message: str
    variant: str
    model_name: str
    encoder_a: str
    encoder_b: str
    encoder_a_kind: str
    encoder_b_kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "message": self.message,
            "variant": self.variant,
            "model_name": self.model_name,
            "encoder_a": self.encoder_a,
            "encoder_b": self.encoder_b,
            "encoder_a_kind": self.encoder_a_kind,
            "encoder_b_kind": self.encoder_b_kind,
            "required_encoder_set": ["t5xxl", "clip_l"],
            "encoder_order_policy": "either_order",
            "vae_policy": "Flux 1 AE/VAE such as ae.safetensors; custom compatible AE names are not filename-blocked",
        }


def check_flux1_krea_compatibility(
    variant: Any,
    model_name: Any,
    encoder_a: Any,
    encoder_b: Any,
) -> Flux1KreaCompatibility:
    resolved = resolve_flux1_variant(variant, model_name)
    model = str(model_name or "").strip()
    a = str(encoder_a or "").strip()
    b = str(encoder_b or "").strip()
    kind_a = classify_flux1_text_encoder(a)
    kind_b = classify_flux1_text_encoder(b)

    if resolved != "krea_dev":
        return Flux1KreaCompatibility(None, "Not a FLUX.1 Krea route.", resolved, model, a, b, kind_a, kind_b)

    if kind_a == "missing" or kind_b == "missing":
        return Flux1KreaCompatibility(False, "FLUX.1 Krea requires two text encoders: T5XXL and CLIP-L.", resolved, model, a, b, kind_a, kind_b)

    if "foreign_qwen" in {kind_a, kind_b}:
        return Flux1KreaCompatibility(False, "FLUX.1 Krea uses the FLUX.1 dual encoder stack (T5XXL + CLIP-L); Qwen/MMProj assets are not compatible.", resolved, model, a, b, kind_a, kind_b)

    kinds = {kind_a, kind_b}
    if kinds == {"t5xxl", "clip_l"}:
        return Flux1KreaCompatibility(True, "FLUX.1 Krea encoder pair is compatible.", resolved, model, a, b, kind_a, kind_b)

    if kind_a in {"t5xxl", "clip_l"} and kind_b in {"t5xxl", "clip_l"}:
        missing = "CLIP-L" if kind_a == kind_b == "t5xxl" else "T5XXL"
        return Flux1KreaCompatibility(False, f"FLUX.1 Krea requires one T5XXL encoder and one CLIP-L encoder; {missing} is missing.", resolved, model, a, b, kind_a, kind_b)

    return Flux1KreaCompatibility(
        None,
        "FLUX.1 Krea encoder filenames could not be fully classified. Expected one T5XXL and one CLIP-L encoder; Neo will allow the route but keep it unverified.",
        resolved,
        model,
        a,
        b,
        kind_a,
        kind_b,
    )
