from __future__ import annotations

from typing import Any, Iterable

# Values used by Neo/UI profiles to represent "not selected yet". These are
# route controls, never valid Comfy model filenames for required asset roles.
ASSET_SELECTION_SENTINELS = frozenset({
    "",
    "provider_default",
    "automatic",
    "auto",
    "none",
    "null",
    "not_selected",
    "unselected",
})


def normalize_asset_selection(value: Any) -> str:
    """Return a model/asset filename without rewriting its path or case.

    Comfy filenames may include subfolders and case-sensitive segments. Neo only
    trims surrounding whitespace; it must not normalize separators, basename,
    capitalization, or extension.
    """

    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file")
    return str(value or "").strip()


def is_asset_selection_sentinel(value: Any) -> bool:
    normalized = normalize_asset_selection(value)
    lowered = normalized.casefold().replace(" ", "_")
    if lowered in ASSET_SELECTION_SENTINELS:
        return True
    # Manifest/UI placeholders have historically used values such as
    # select_diffusion_model_later. Treat the whole placeholder family as
    # unselected rather than attempting to enumerate every role name.
    return lowered.startswith("select_") and lowered.endswith("_later")


def is_explicit_asset_selection(value: Any) -> bool:
    return not is_asset_selection_sentinel(value)


def first_explicit_asset_selection(values: Iterable[Any]) -> str:
    for value in values:
        if is_explicit_asset_selection(value):
            return normalize_asset_selection(value)
    return ""


def require_explicit_asset_selection(
    validation: Any,
    role_label: str,
    *values: Any,
    refresh_catalog: bool = True,
) -> str:
    """Resolve a required asset and attach an actionable provider error.

    The helper intentionally accepts the provider validation object instead of
    importing its schema, keeping this module usable from readiness and provider
    compilers without circular imports.
    """

    selected = first_explicit_asset_selection(values)
    if selected:
        return selected

    message = f"Select an installed {role_label} before generating."
    if refresh_catalog:
        message += " Refresh the ComfyUI model catalog if the file was added recently."
    errors = getattr(validation, "errors", None)
    if isinstance(errors, list) and message not in errors:
        errors.append(message)
    if hasattr(validation, "ok"):
        validation.ok = False
    return ""
