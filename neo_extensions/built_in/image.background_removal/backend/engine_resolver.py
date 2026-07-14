from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .constants import NATIVE_PRESET_MODELS, PRESET_MODEL_CANDIDATES


@dataclass(frozen=True)
class EngineResolution:
    requested_engine: str
    resolved_engine: str
    preset: str
    model: str
    fallback_used: bool
    fallback_reason: str
    ready: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["errors"] = list(self.errors)
        return data


def _first_matching(candidates: tuple[str, ...], available: list[str]) -> str:
    """Return the exact catalog value while allowing an unambiguous basename match.

    Comfy preserves subfolder-qualified model names. Preset candidates intentionally
    use canonical basenames, so the resolver may match ``folder/model.safetensors``
    only when that basename is unique. It never guesses between duplicate names.
    """
    clean = [str(item).strip().replace("\\", "/") for item in available if str(item or "").strip()]
    by_full = {item.casefold(): item for item in clean}
    by_base: dict[str, list[str]] = {}
    for item in clean:
        by_base.setdefault(item.rsplit("/", 1)[-1].casefold(), []).append(item)
    for candidate in candidates:
        key = str(candidate or "").strip().replace("\\", "/").casefold()
        actual = by_full.get(key)
        if actual:
            return actual
        matches = by_base.get(key.rsplit("/", 1)[-1], [])
        if len(matches) == 1:
            return matches[0]
    return ""


def resolve_engine(
    settings: dict[str, Any],
    *,
    comfy_catalog: dict[str, Any] | None = None,
    native_status: dict[str, Any] | None = None,
) -> EngineResolution:
    """Resolve the execution engine without guessing unavailable assets.

    Smart routing is deterministic and capability-led. It never treats a missing
    model as installed and never reports fallback when the user selected a strict
    engine. Anime prefers the native ISNet specialization; all other presets prefer
    the existing Comfy BiRefNet route when it is ready.
    """

    clean = dict(settings or {})
    requested = str(clean.get("engine") or "smart").strip().lower()
    if requested not in {"smart", "comfy_birefnet", "native_rembg"}:
        requested = "smart"
    preset = str(clean.get("preset") or "smart_auto").strip().lower()
    fallback_policy = str(clean.get("fallback_policy") or "on_unavailable").strip().lower()
    allow_fallback = fallback_policy in {"on_unavailable", "on_unavailable_or_queue_failure"}

    catalog = comfy_catalog if isinstance(comfy_catalog, dict) else {}
    native = native_status if isinstance(native_status, dict) else {}
    comfy_models = [str(item) for item in catalog.get("models") or [] if str(item or "").strip()]
    comfy_nodes_ready = bool(catalog.get("nodes_ready"))
    selected_comfy = str(clean.get("model") or "").strip()
    if selected_comfy:
        selected_comfy = _first_matching((selected_comfy,), comfy_models)
    if not selected_comfy:
        selected_comfy = _first_matching(PRESET_MODEL_CANDIDATES.get(preset, PRESET_MODEL_CANDIDATES["smart_auto"]), comfy_models)
    comfy_ready = bool(comfy_nodes_ready and selected_comfy)

    native_available = bool(native.get("available"))
    native_models = [str(item) for item in native.get("models") or [] if str(item or "").strip()]
    preferred_native = str(clean.get("native_model") or NATIVE_PRESET_MODELS.get(preset) or NATIVE_PRESET_MODELS["smart_auto"]).strip()
    native_model = preferred_native if not native_models or preferred_native in native_models else ""
    native_ready = bool(native_available and native_model)

    if requested == "comfy_birefnet":
        if comfy_ready:
            return EngineResolution(requested, "comfy_birefnet", preset, selected_comfy, False, "", True, ())
        return EngineResolution(requested, "", preset, selected_comfy, False, "", False, ("Comfy BiRefNet is not ready for the selected preset.",))

    if requested == "native_rembg":
        if native_ready:
            return EngineResolution(requested, "native_rembg", preset, native_model, False, "", True, ())
        return EngineResolution(requested, "", preset, native_model, False, "", False, (str(native.get("reason") or "Native rembg is not installed or could not be loaded."),))

    # Smart routing: anime has a purpose-built ISNet model, while the established
    # Comfy BiRefNet route remains the quality-first default for all other presets.
    prefer_native = preset == "anime"
    primary = "native_rembg" if prefer_native else "comfy_birefnet"
    secondary = "comfy_birefnet" if prefer_native else "native_rembg"
    primary_ready = native_ready if prefer_native else comfy_ready
    secondary_ready = comfy_ready if prefer_native else native_ready

    if primary_ready:
        return EngineResolution(requested, primary, preset, native_model if prefer_native else selected_comfy, False, "", True, ())
    if allow_fallback and secondary_ready:
        reason = (
            "Anime specialization was unavailable; using the installed Comfy BiRefNet route."
            if prefer_native
            else "Comfy BiRefNet was unavailable for this preset; using Neo's native rembg fallback."
        )
        return EngineResolution(requested, secondary, preset, selected_comfy if prefer_native else native_model, True, reason, True, ())

    errors: list[str] = []
    if not comfy_ready:
        errors.append("Comfy BiRefNet route is unavailable or has no compatible installed model.")
    if not native_ready:
        errors.append(str(native.get("reason") or "Native rembg fallback is unavailable."))
    if not allow_fallback:
        errors.append("Fallback policy is set to Never fallback.")
    return EngineResolution(requested, "", preset, "", False, "", False, tuple(errors))
