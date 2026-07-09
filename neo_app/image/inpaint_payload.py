from __future__ import annotations

from typing import Any


_MASKED_TARGET_ALIASES = {
    "masked",
    "mask",
    "masked_area",
    "mask_area",
    "inside_mask",
    "inside",
    "painted",
    "selection",
    "selected",
}

_UNMASKED_TARGET_ALIASES = {
    "unmasked",
    "not_masked",
    "not_masked_area",
    "outside_mask",
    "outside",
    "inverse",
    "inverted",
    "invert",
    "background",
}

_TARGET_ALIAS_KEYS = (
    "inpaint_selection_target",
    "inpaint_target",
    "inpaint_mask_target",
    "mask_target",
    "mask_mode",
)


def _clean(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _first_present(params: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        value = params.get(key)
        if value not in (None, ""):
            return key, _clean(value)
    return "", ""


def normalize_inpaint_target_value(value: Any) -> str:
    """Normalize target aliases to the provider-owned inpaint target contract.

    Provider compilers use `masked`/`unmasked` to decide whether the loaded mask
    should be inverted before it reaches the latent mask path. The Image Tab UI
    exposes a model-neutral `inpaint_selection_target` field with values such as
    `masked_area` and `not_masked_area`; this helper keeps both contracts aligned.
    Unknown values intentionally fall back to `masked` so stale editor brush modes
    like `paint`/`erase` or ControlNet mask modes do not accidentally invert masks.
    """

    candidate = _clean(value)
    if candidate in _UNMASKED_TARGET_ALIASES:
        return "unmasked"
    if candidate in _MASKED_TARGET_ALIASES:
        return "masked"
    return "masked"


def inpaint_selection_target_for_provider_target(value: Any) -> str:
    return "not_masked_area" if normalize_inpaint_target_value(value) == "unmasked" else "masked_area"


def normalize_inpaint_target_aliases(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return params with UI/provider inpaint target aliases synchronized.

    This is a narrow payload normalization pass. It does not unlock routes, change
    mask grow/blur defaults, change inpaint context semantics, or mutate compiler
    graph policy. It only makes these aliases agree:

    - UI/surface: `inpaint_selection_target = masked_area|not_masked_area`
    - provider compilers: `inpaint_target = masked|unmasked`
    - legacy/runtime aliases: `inpaint_mask_target`, `mask_target`, `mask_mode`

    When both UI and provider aliases are present, the surface field wins.
    """

    normalized = dict(params or {})
    source_key, raw_value = _first_present(normalized, _TARGET_ALIAS_KEYS)
    provider_target = normalize_inpaint_target_value(raw_value or normalized.get("inpaint_selection_target") or normalized.get("inpaint_target"))
    selection_target = inpaint_selection_target_for_provider_target(provider_target)

    normalized["inpaint_target"] = provider_target
    normalized["inpaint_selection_target"] = selection_target
    normalized["_neo_inpaint_target_alias_normalization"] = {
        "schema": "neo.image.inpaint_target_aliases.v1",
        "source_key": source_key or "default",
        "source_value": raw_value or "",
        "inpaint_target": provider_target,
        "inpaint_selection_target": selection_target,
        "mask_inverted": provider_target == "unmasked",
        "policy": "model-neutral Image Tab target aliases normalized before provider compiler mask handling",
    }
    return normalized
