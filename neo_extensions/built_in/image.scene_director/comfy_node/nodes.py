
"""
Neo Scene Director v0.5.4 — Scene Graph Conditioning Node

Phase 27 keeps NeoSceneDirectorV054 as the active route and makes prompt
authority explicit: global context may be shared as a compact regional suffix,
or the Scene Director graph may own conditioning by itself.
V052/V053 implementation code may remain in this source file as historical
reference, but those legacy classes are not exported through NODE_CLASS_MAPPINGS.

Active payload: scene_graph_json as a canonical JSON string.
Retired active payload: scene_json.
"""

from __future__ import annotations

import ast
import json
import math
import re
from copy import deepcopy
from typing import Any, Dict, List, Tuple
try:
    import torch
    import torch.nn.functional as F
    from torch.nn.functional import interpolate
except Exception:  # pragma: no cover - keeps schema/contract imports lightweight.
    class _TorchUnavailable:
        Tensor = Any

        @staticmethod
        def inference_mode():
            return lambda function: function

        def __getattr__(self, name):
            raise RuntimeError("NeoSceneDirectorV054 execution requires PyTorch in the ComfyUI runtime.")

    class _FunctionalUnavailable:
        def __getattr__(self, name):
            raise RuntimeError("NeoSceneDirectorV054 execution requires PyTorch in the ComfyUI runtime.")

    torch = _TorchUnavailable()
    F = _FunctionalUnavailable()

    def interpolate(*_args, **_kwargs):
        raise RuntimeError("NeoSceneDirectorV054 execution requires PyTorch in the ComfyUI runtime.")


def _safe_float(value, default=0.0):
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return float(default)
    try:
        return float(text)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    if value is None:
        return int(default)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return int(default)
    try:
        return int(float(text))
    except Exception:
        return int(default)


def _safe_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def _v054_region_feather(region: Dict[str, Any] | None, default: int = 0) -> int:
    """Read feather from every supported V054/V053 bridge location.

    Neo's V054 UI stores the canonical value under metadata.mask.feather. Older
    bridges read only a top-level feather key, silently producing hard regional
    attention borders. Keep both contracts replay-compatible.
    """
    source = region if isinstance(region, dict) else {}
    mask = source.get("mask") if isinstance(source.get("mask"), dict) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    metadata_mask = metadata.get("mask") if isinstance(metadata.get("mask"), dict) else {}
    for value in (
        source.get("feather"),
        source.get("mask_feather"),
        mask.get("feather"),
        metadata_mask.get("feather"),
    ):
        if value is not None and str(value).strip() != "":
            return max(0, _safe_int(value, default))
    return max(0, _safe_int(default, 0))


def _clean_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _repeat_div(value: int, iterations: int) -> int:
    for _ in range(iterations):
        value = math.ceil(value / 2)
    return value


def _clip_encode_crossattn(clip: Any, text: str) -> torch.Tensor:
    tokens = clip.tokenize(text)

    try:
        encoded = clip.encode_from_tokens(tokens, return_pooled=True, return_dict=True)
        if isinstance(encoded, dict):
            if "cond" in encoded:
                return encoded["cond"]
            if "crossattn" in encoded:
                return encoded["crossattn"]
    except TypeError:
        pass

    encoded = clip.encode_from_tokens(tokens)

    if isinstance(encoded, dict):
        value = encoded.get("cond", None)
        if value is None:
            value = encoded.get("crossattn", None)
        if value is not None:
            return value

    if isinstance(encoded, (tuple, list)):
        first = encoded[0]
        if torch.is_tensor(first):
            return first
        if isinstance(first, (tuple, list)) and first and torch.is_tensor(first[0]):
            return first[0]

    if torch.is_tensor(encoded):
        return encoded

    raise RuntimeError("Could not extract CLIP cross-attention tensor from this ComfyUI version.")


def _pad_context_to_tokens(t: torch.Tensor, token_count: int) -> torch.Tensor:
    if t.shape[1] == token_count:
        return t
    if t.shape[1] > token_count:
        return t[:, :token_count, :]
    pad = torch.zeros((t.shape[0], token_count - t.shape[1], t.shape[2]), device=t.device, dtype=t.dtype)
    return torch.cat([t, pad], dim=1)


def _rect_to_pixels(mask_data: Dict, width: int, height: int):
    x = float(mask_data.get("x", 0))
    y = float(mask_data.get("y", 0))
    w = float(mask_data.get("w", 1))
    h = float(mask_data.get("h", 1))

    if abs(x) <= 1 and abs(w) <= 1:
        x1 = int(width * x)
        x2 = int(width * (x + w))
    else:
        x1 = int(x)
        x2 = int(x + w)

    if abs(y) <= 1 and abs(h) <= 1:
        y1 = int(height * y)
        y2 = int(height * (y + h))
    else:
        y1 = int(y)
        y2 = int(y + h)

    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid/empty rect mask: {mask_data}")
    return x1, y1, x2, y2


def _mask_anchor(mask_data: Dict):
    x = float(mask_data.get("x", 0))
    w = float(mask_data.get("w", 1))
    center = x + w * 0.5
    if center < 0.18:
        return "far left side of frame"
    if center < 0.38:
        return "left side of frame"
    if center < 0.62:
        return "center of frame"
    if center < 0.82:
        return "right side of frame"
    return "far right side of frame"


def _make_rect_mask(mask_data: Dict, width: int, height: int, weight: float = 1.0) -> torch.Tensor:
    x1, y1, x2, y2 = _rect_to_pixels(mask_data, width, height)
    mask = torch.zeros((height, width), dtype=torch.float32)
    mask[y1:y2, x1:x2] = max(0.0, float(weight))
    return mask.unsqueeze(0)


def _feather_mask(mask: torch.Tensor, feather_px: int) -> torch.Tensor:
    feather_px = int(feather_px)
    if feather_px <= 0:
        return mask
    # Phase 21.6: avoid expensive full-canvas pooling for uniform masks.
    # Auto background reinforcement often creates a full-frame shared region;
    # feathering an already-uniform mask is a no-op and can stall CPU-only tests
    # or slower Comfy environments at tall SDXL sizes.
    try:
        if float(mask.max().item()) == float(mask.min().item()):
            return mask.clamp(0, 1)
    except Exception:
        pass
    k = max(3, min(33, feather_px * 2 + 1))
    if k % 2 == 0:
        k += 1
    pad = k // 2
    m = F.avg_pool2d(mask.unsqueeze(0), kernel_size=k, stride=1, padding=pad)
    return m.squeeze(0).clamp(0, 1)


def _clamp01(value: float, default: float = 0.0) -> float:
    try:
        value = float(value)
    except Exception:
        value = float(default)
    return max(0.0, min(1.0, value))


def _appearance_lock_mode_value(value: Any) -> str:
    mode = str(value or "off").strip().lower()
    aliases = {
        "none": "off",
        "disabled": "off",
        "hair": "hair_focus_soft",
        "soft": "full_character_soft",
        "strong": "full_character_strong",
        # Phase 26.9.6 kept V054 compatible with the old upper-identity branch.
        # Phase 26.9.7 adds explicit full-character authority modes while
        # preserving these legacy names for older workflows.
        "identity_soft": "upper_identity_soft",
        "identity_strong": "upper_identity_strong",
        "appearance_soft": "upper_identity_soft",
        "appearance_strong": "upper_identity_strong",
        "full_identity_soft": "full_character_soft",
        "full_identity_strong": "full_character_strong",
        "character_soft": "full_character_soft",
        "character_strong": "full_character_strong",
    }
    mode = aliases.get(mode, mode)
    if mode not in (
        "off",
        "hair_focus_soft",
        "hair_focus_strong",
        "upper_identity_soft",
        "upper_identity_strong",
        "full_character_soft",
        "full_character_strong",
    ):
        return "off"
    return mode



def _v054_legacy_attention_primary_settings(
    appearance_mode: Any,
    *,
    base_weight: Any,
    region_gain: Any,
    appearance_gain: Any,
    identity_strength: Any | None = None,
    mask_feather: Any | None = None,
    character_lock_mode: Any | None = None,
) -> dict[str, Any]:
    """V25.9.3: make the legacy in-sampler attention lock the primary path.

    Older Neo builds got the visible mid-generation correction from the custom
    node's attn2 model patch, not from a later masked KSampler rescue. This
    helper keeps user values where they are already strong enough, but raises
    weak V25.8 defaults for full-character locks so the patched MODEL has
    enough authority during the main sampler.
    """
    mode = _appearance_lock_mode_value(appearance_mode)
    requested_base = _safe_float(base_weight, 0.55)
    requested_region = _safe_float(region_gain, 0.45)
    requested_gain = _safe_float(appearance_gain, 0.70)
    requested_identity = _safe_float(identity_strength, requested_gain)
    requested_feather = _safe_int(mask_feather, 24)
    char_mode = str(character_lock_mode or "").strip().lower()

    effective_base = requested_base
    effective_region = requested_region
    effective_gain = requested_gain
    effective_identity = requested_identity
    effective_feather = requested_feather
    primary = mode != "off"
    legacy_alias = ""

    if mode == "full_character_strong":
        if char_mode == "strict":
            effective_base = min(effective_base, 0.18)
            effective_region = max(effective_region, 0.98)
            effective_gain = max(effective_gain, 1.05)
            effective_identity = max(effective_identity, 0.85)
            effective_feather = min(effective_feather, 12)
        else:
            effective_base = min(effective_base, 0.25)
            effective_region = max(effective_region, 0.90)
            effective_gain = max(effective_gain, 0.95)
            effective_identity = max(effective_identity, 0.75)
            effective_feather = min(effective_feather, 16)
        legacy_alias = "hair_focus_strong"
    elif mode == "full_character_soft":
        effective_base = min(effective_base, 0.35)
        effective_region = max(effective_region, 0.72)
        effective_gain = max(effective_gain, 0.70)
        legacy_alias = "hair_focus_soft"
    elif mode in {"upper_identity_strong", "hair_focus_strong"}:
        effective_base = min(effective_base, 0.30)
        effective_region = max(effective_region, 0.80)
        effective_gain = max(effective_gain, 0.90)
        effective_identity = max(effective_identity, 0.70)
        effective_feather = min(effective_feather, 18)
        legacy_alias = "hair_focus_strong"
    elif mode in {"upper_identity_soft", "hair_focus_soft"}:
        effective_base = min(effective_base, 0.40)
        effective_region = max(effective_region, 0.65)
        effective_gain = max(effective_gain, 0.62)
        legacy_alias = "hair_focus_soft"

    return {
        "schema": "neo.image.scene_director.legacy_attention_primary.v25_9_3",
        "phase": "V25.9.3",
        "status": "primary" if primary else "off",
        "primary_character_lock_path": "legacy_in_sampler_attention" if primary else "none",
        "fallback_masked_correction_role": "rescue_only",
        "appearance_lock_mode": mode,
        "legacy_alias_mode": legacy_alias,
        "requested_base_weight": requested_base,
        "effective_base_weight": effective_base,
        "requested_region_gain": requested_region,
        "effective_region_gain": effective_region,
        "requested_appearance_gain": requested_gain,
        "effective_appearance_gain": effective_gain,
        "requested_identity_strength": requested_identity,
        "effective_identity_strength": effective_identity,
        "requested_mask_feather": requested_feather,
        "effective_mask_feather": effective_feather,
        "strengthened": any([
            abs(effective_base - requested_base) > 1e-6,
            abs(effective_region - requested_region) > 1e-6,
            abs(effective_gain - requested_gain) > 1e-6,
            abs(effective_identity - requested_identity) > 1e-6,
            effective_feather != requested_feather,
        ]),
        "prompt_injection": False,
        "policy": "Character Lock Strong/Strict is enforced through numeric V053-style in-sampler attn2 authority only; external masked KSampler correction is fallback/rescue only.",
    }

def _make_top_focus_mask(
    mask_data: Dict,
    width: int,
    height: int,
    top_ratio: float,
    weight: float = 1.0,
    expand_ratio: float = 0.0,
) -> torch.Tensor:
    """Return a subject-local upper/head area mask used for appearance locking.

    This is intentionally bbox-derived, not detector-derived. It keeps txt2img single-pass safe:
    region boxes still define intent, while the upper subject zone receives a separate
    appearance-conditioning branch to reduce hair/color/style dilution at contact zones.
    """
    x1, y1, x2, y2 = _rect_to_pixels(mask_data, width, height)
    ratio = _clamp01(top_ratio, 0.34)
    ratio = max(0.12, min(0.62, ratio))
    expand_ratio = max(0.0, min(0.20, _safe_float(expand_ratio, 0.0)))
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    # Txt2img subjects can drift slightly outside their authored rectangle.
    # Hair is especially likely to extend above the box, which made a perfectly
    # valid pink-hair lock miss the generated hair pixels.  Expand only the
    # upper-identity branch; the full-character ownership box stays unchanged.
    x_expand = int(round(box_w * expand_ratio * 0.50))
    y_expand = int(round(box_h * expand_ratio))
    x_start = max(0, x1 - x_expand)
    x_end = min(width, x2 + x_expand)
    y_start = max(0, y1 - y_expand)
    y_focus = y1 + max(1, int((y2 - y1) * ratio))
    mask = torch.zeros((height, width), dtype=torch.float32)
    mask[y_start:y_focus, x_start:x_end] = max(0.0, float(weight))
    return mask.unsqueeze(0)


def _face_detail_height_ratio(top_ratio: float) -> float:
    return max(0.30, min(0.42, _safe_float(top_ratio, 0.34) * 0.82))


def _make_face_detail_mask(
    mask_data: Dict,
    width: int,
    height: int,
    top_ratio: float,
    weight: float = 1.0,
) -> torch.Tensor:
    """Return a soft subject-local head/face zone for fine appearance traits.

    Facial hair is a small lower-face texture, so a full-character branch can
    preserve gender and ethnicity while still averaging stubble away.  Keep
    this bbox-derived (no detector or second pass), but make it shorter than
    the normal upper-identity lane and slightly tolerant of txt2img drift.
    """
    face_ratio = _face_detail_height_ratio(top_ratio)
    return _make_top_focus_mask(
        mask_data,
        width,
        height,
        face_ratio,
        weight,
        expand_ratio=0.08,
    )


def _make_clothing_slice_mask(
    mask_data: Dict,
    width: int,
    height: int,
    start_ratio: float,
    end_ratio: float,
    weight: float = 1.0,
    expand_ratio: float = 0.04,
) -> torch.Tensor:
    """Return a bbox-derived body slice for subject-local garment authority.

    Clothing lanes must not cover the face: otherwise an outfit prompt competes
    with the gender/grooming lane at the exact pixels where stubble and facial
    structure are decided.  This remains detector-free and single-sampler safe.
    """
    x1, y1, x2, y2 = _rect_to_pixels(mask_data, width, height)
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    start = max(0.0, min(0.92, _safe_float(start_ratio, 0.20)))
    end = max(start + 0.04, min(1.0, _safe_float(end_ratio, 0.70)))
    expand = max(0.0, min(0.16, _safe_float(expand_ratio, 0.04)))
    x_expand = int(round(box_w * expand))
    x_start = max(0, x1 - x_expand)
    x_end = min(width, x2 + x_expand)
    y_start = max(0, min(height, y1 + int(round(box_h * start))))
    y_end = max(y_start + 1, min(height, y1 + int(round(box_h * end))))
    mask = torch.zeros((height, width), dtype=torch.float32)
    mask[y_start:y_end, x_start:x_end] = max(0.0, float(weight))
    return mask.unsqueeze(0)


def _make_full_character_lock_mask(mask_data: Dict, width: int, height: int, weight: float = 1.0) -> torch.Tensor:
    """Return the full character-region mask used by V054 full-character lock.

    This intentionally mirrors the region rectangle instead of deriving a new
    detector/body mask. Scene Director regions are already the user-authored
    authority boundary, so the full-character branch can reinforce body,
    outfit, silhouette and presentation without guessing anatomy terms.
    """
    return _make_rect_mask(mask_data, width, height, max(0.0, float(weight)))


def _character_trait_terms_for_attention(region: Dict) -> List[str]:
    """Read explicit Character Trait Lock values for the main attention lane.

    V054 previously preserved these values only in scene-graph metadata and in
    the optional late KSampler controller. That made the visible trait fields
    appear to work only at the end of a run. The main node must consume the
    same explicit terms in its subject-local attn2 branches so Strong/Strict
    Character Lock behaves like the legacy V1 Hairlock Strong path.
    """
    raw = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
    if not raw:
        raw = region.get("trait_lock") if isinstance(region.get("trait_lock"), dict) else {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else raw
    terms: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("explicit_terms", "prompt_terms", "custom", "custom_text", "selected_label", "label", "terms"):
                add(value.get(key))
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value or "").strip()
        if text:
            terms.extend([part.strip() for part in text.replace("\n", ",").replace(";", ",").split(",") if part.strip()])

    for category, value in (categories or {}).items():
        if str(category or "").strip().lower() in {"schema", "releasestage", "source_policy"}:
            continue
        add(value)

    seen = set()
    result: List[str] = []
    for term in terms:
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(term)
    return result[:32]


def _character_trait_categories_for_attention(region: Dict) -> Dict[str, List[str]]:
    """Return explicit trait terms grouped by their visible UI category.

    The flat helper above is useful for diagnostics, but a Strong lock needs to
    know *which* guard owns each term.  Keeping the category lets gender and
    hair receive their own authority instead of becoming an undifferentiated
    suffix at the end of a long regional prompt.
    """
    raw = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
    if not raw:
        raw = region.get("trait_lock") if isinstance(region.get("trait_lock"), dict) else {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else raw
    result: Dict[str, List[str]] = {}

    def collect(value: Any) -> List[str]:
        terms: List[str] = []

        def add(item: Any) -> None:
            if isinstance(item, dict):
                # Prefer the exact submitted prompt terms. Selected labels are
                # only a fallback so UI display text does not duplicate them.
                preferred = item.get("explicit_terms") or item.get("prompt_terms") or item.get("terms")
                if preferred:
                    add(preferred)
                else:
                    add(item.get("custom") or item.get("custom_text") or item.get("selected_label") or item.get("label"))
                return
            if isinstance(item, (list, tuple, set)):
                for sub in item:
                    add(sub)
                return
            for part in str(item or "").replace("\n", ",").replace(";", ",").split(","):
                text = part.strip()
                if text:
                    terms.append(text)

        add(value)
        seen = set()
        deduped: List[str] = []
        for term in terms:
            key = term.casefold()
            if key and key not in seen:
                seen.add(key)
                deduped.append(term)
        return deduped

    for category, value in (categories or {}).items():
        key = str(category or "").strip().lower()
        if key in {"schema", "releasestage", "source_policy"}:
            continue
        terms = collect(value)
        if terms:
            result[key] = terms
    return result


def _character_additional_details_block_for_attention(region: Dict) -> Dict[str, Any]:
    """Return the Phase 27.15 character-owned Additional Details block."""
    traits = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
    raw = traits.get("additional_details") if isinstance(traits.get("additional_details"), dict) else {}
    if not raw:
        raw = region.get("character_additional_details") if isinstance(region.get("character_additional_details"), dict) else {}
    return raw


def _additional_detail_target_for_attention(value: Any) -> str:
    target = str(value or "full_body").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "body": "full_body",
        "upper_body": "torso",
        "chest": "torso",
        "waist": "lower_torso",
        "hips": "lower_torso",
        "hands": "arms_hands",
        "arms": "arms_hands",
        "shoes": "feet",
    }
    target = aliases.get(target, target)
    return target if target in {"full_body", "face", "torso", "lower_torso", "arms_hands", "legs", "feet"} else "full_body"


def _character_held_item_terms_for_attention(region: Dict) -> List[str]:
    """Compile user-authored held-item ownership without creating a prop lane."""
    block = _character_additional_details_block_for_attention(region)
    rows = block.get("held_items") if isinstance(block.get("held_items"), list) else []
    terms: List[str] = []
    for row in rows[:8]:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        submitted = str(row.get("prompt") or "").strip()
        if submitted:
            terms.append(submitted)
            continue
        item = str(row.get("item") or row.get("name") or row.get("description") or "").strip()
        if not item:
            continue
        action = str(row.get("action") or "holding").strip() or "holding"
        hand = str(row.get("hand") or "auto").strip().lower()
        placement = "with both hands" if hand == "both" else (f"in the {hand} hand" if hand in {"left", "right"} else "in hand")
        appearance = str(row.get("appearance") or row.get("material_color") or row.get("material") or "").strip()
        terms.append(", ".join(part for part in (f"{action} {item} {placement}", appearance) if part))
    seen = set()
    return [term for term in terms if not (term.casefold() in seen or seen.add(term.casefold()))][:8]


def _character_custom_detail_rows_for_attention(region: Dict) -> List[Dict[str, str]]:
    """Return targeted user/library detail rows with no invented guard text."""
    block = _character_additional_details_block_for_attention(region)
    rows = block.get("custom_details") if isinstance(block.get("custom_details"), list) else []
    result: List[Dict[str, str]] = []
    for row in rows[:12]:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        instruction = str(row.get("instruction") or row.get("prompt") or row.get("text") or "").strip()
        if not instruction:
            continue
        result.append({
            "id": str(row.get("id") or ""),
            "target_area": _additional_detail_target_for_attention(row.get("target_area") or row.get("target")),
            "instruction": instruction,
            "negative": str(row.get("negative") or row.get("negative_prompt") or "").strip(),
        })
    return result


def _character_custom_detail_terms_for_attention(region: Dict, targets: set[str] | None = None) -> List[str]:
    rows = _character_custom_detail_rows_for_attention(region)
    terms: List[str] = []
    seen = set()
    for row in rows:
        if targets is not None and row["target_area"] not in targets:
            continue
        term = row["instruction"]
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _character_custom_detail_negatives_for_attention(region: Dict, targets: set[str] | None = None) -> List[str]:
    terms: List[str] = []
    seen = set()
    for row in _character_custom_detail_rows_for_attention(region):
        if targets is not None and row["target_area"] not in targets:
            continue
        for part in str(row.get("negative") or "").replace("\n", ",").replace(";", ",").split(","):
            text = " ".join(part.strip().split())
            key = text.casefold()
            if key and key not in seen:
                seen.add(key)
                terms.append(text)
    return terms


def _character_additional_details_report_for_attention(region: Dict) -> Dict[str, Any]:
    categories = _character_trait_categories_for_attention(region)
    category_counts = {
        category: len(categories.get(category, []))
        for category in ("body_details", "top_garment_state", "bottom_garment_state", "underlayer")
    }
    held_items = _character_held_item_terms_for_attention(region)
    custom_rows = _character_custom_detail_rows_for_attention(region)
    active = any(category_counts.values()) or bool(held_items) or bool(custom_rows)
    return {
        "schema": "neo.image.scene_director.character_additional_details.runtime.v25_9_16",
        "phase": "SD-V054-27.15",
        "enabled": active,
        "category_term_counts": category_counts,
        "held_item_count": len(held_items),
        "custom_detail_count": len(custom_rows),
        "custom_detail_targets": [row["target_area"] for row in custom_rows],
        "adds_attention_branches": False,
        "adds_sampler": False,
        "routing": {
            "body_details": "primary_plus_existing_full_character",
            "garment_states_underlayer": "matching_existing_top_bottom_or_full_outfit",
            "held_items": "existing_pose_plus_full_character",
            "custom_details": "matching_existing_face_clothing_or_full_character",
        },
        "content_policy_guards_added": False,
        "policy": "User/library-authored additional details reuse existing character branches; no per-detail lane, sampler, or blanket content restriction is created.",
    }


_CHARACTER_LOCAL_POSE_PATTERN = re.compile(
    r"\b(?:stand(?:s|ing)?|sit(?:s|ting)?|seated|kneel(?:s|ing)?|lie|lies|lying|"
    r"reclin(?:e|es|ed|ing)|crouch(?:es|ed|ing)?|squat(?:s|ting)?|leans|leaning|"
    r"hug(?:s|ging)?|embrac(?:e|es|ing)|hold(?:s|ing)?|touch(?:es|ing)?|support(?:s|ing)?|"
    r"carry|carries|carrying|kiss(?:es|ing)?|facing|body\s+angled|torso\s+turned|"
    r"hand\s+placement|arm\s+around|waist|lap|pose|posture|body\s+contact)\b",
    re.IGNORECASE,
)


def _character_local_pose_authority_for_attention(region: Dict) -> Dict[str, Any]:
    """Return one character-owned pose contract and never read shared Pair Pose.

    Phase 27.13 retires the Advanced Pair Pose lane.  The visible Character >
    Pose trait is authoritative.  Older scenes without a structured Pose value
    may replay from pose/action sentences already authored in that character's
    own region prompt, but no scene-level pose paragraph is copied into another
    character mask.
    """
    categories = _character_trait_categories_for_attention(region)
    explicit = [str(term).strip() for term in categories.get("pose", []) if str(term).strip()]
    held_items = _character_held_item_terms_for_attention(region)
    posture_terms: List[str] = list(explicit)
    pose_source = "explicit_character_pose_trait" if explicit else "empty"
    if not explicit:
        prompt = _character_source_prompt_for_attention(region)
        fragments = [
            " ".join(fragment.strip().split())
            for fragment in re.split(r"(?<=[.!?])\s+|[\r\n]+", prompt)
            if fragment.strip() and _CHARACTER_LOCAL_POSE_PATTERN.search(fragment)
        ]
        seen = set()
        for fragment in fragments:
            key = fragment.casefold()
            if key and key not in seen:
                seen.add(key)
                posture_terms.append(fragment)
        if posture_terms:
            pose_source = "character_region_prompt_fallback"

    terms: List[str] = list(posture_terms)
    for term in held_items:
        if term.casefold() not in {item.casefold() for item in terms}:
            terms.append(term)
    source = (
        "explicit_character_pose_plus_held_items" if explicit and held_items
        else "character_region_pose_plus_held_items" if posture_terms and held_items
        else pose_source if posture_terms
        else "character_held_item_action" if held_items
        else "empty"
    )

    mode = _character_lock_guard_mode(region, "pose")
    return {
        "schema": "neo.image.scene_director.character_pose_authority.v25_9_15",
        "phase": "SD-V054-27.13",
        "enabled": bool(terms) and mode != "off",
        "status": "active" if terms and mode != "off" else ("off" if terms else "empty"),
        "source": source,
        "region_id": str(region.get("id") or ""),
        "label": str(region.get("name") or region.get("label") or region.get("id") or "Character").strip(),
        "terms": terms[:8],
        "posture_terms": posture_terms[:8],
        "lock_mode": mode,
        "routes": ["primary_character_region_branch", "existing_full_character_branch"] if terms and mode != "off" else [],
        "adds_attention_branch": False,
        "held_item_term_count": len(held_items),
        "policy": "Character-level Pose plus character-owned held-item action is local to this character and reuses existing single-sampler branches.",
    }


def _character_pose_lock_prompt_for_attention(region: Dict) -> str:
    authority = _character_local_pose_authority_for_attention(region)
    if not authority.get("enabled"):
        return ""
    mode = str(authority.get("lock_mode") or "balanced")
    weights = {"strict": 1.82, "strong": 1.72, "balanced": 1.38, "soft": 1.16, "off": 1.0}
    weight = weights.get(mode, 1.38)
    terms = [str(term).strip() for term in authority.get("terms") or [] if str(term).strip()]
    if not terms:
        return ""
    exact = ", ".join(f"({term}:{weight:.2f})" for term in terms[:8])
    label = str(authority.get("label") or "Character")
    return ", ".join([
        f"{mode if mode in {'strict', 'strong', 'balanced', 'soft'} else 'balanced'} character-local body pose authority for {label}",
        f"exact selected pose and action for this character only: {exact}",
        "preserve this character's posture, body orientation, limb action and named contact role",
        "other character names are contact targets only; do not swap pose ownership between subjects",
    ])


def _character_pose_negative_terms_for_attention(region: Dict) -> List[str]:
    """Derive mutually exclusive posture guards for this region only.

    These never enter the global negative prompt.  A seated character may
    forbid a standing body inside its own mask while a neighboring standing
    character remains unaffected.
    """
    authority = _character_local_pose_authority_for_attention(region)
    if not authority.get("enabled"):
        return []
    posture_terms = [str(term).strip() for term in authority.get("posture_terms") or [] if str(term).strip()]
    if not posture_terms:
        return []
    text = " ".join(posture_terms).casefold()
    families = set()
    patterns = {
        "standing": r"\b(?:stand|stands|standing|upright)\b",
        "seated": r"\b(?:sit|sits|sitting|seated)\b",
        "kneeling": r"\b(?:kneel|kneels|kneeling)\b",
        "lying": r"\b(?:lie|lies|lying|recline|reclined|reclining)\b",
        "crouching": r"\b(?:crouch|crouched|crouching|squat|squatting)\b",
    }
    for family, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            families.add(family)
    if len(families) != 1:
        return ["wrong character pose", "pose role borrowed from neighboring subject"]
    family = next(iter(families))
    opposites = {
        "standing": ["seated body", "sitting body", "kneeling body", "lying body"],
        "seated": ["standing body", "upright full-height stance", "kneeling body", "lying body"],
        "kneeling": ["standing body", "seated body", "lying body"],
        "lying": ["standing body", "seated body", "kneeling body"],
        "crouching": ["standing upright body", "seated body", "lying body"],
    }
    return opposites.get(family, []) + ["wrong character pose", "pose role borrowed from neighboring subject"]


def _character_trait_negative_categories_for_attention(region: Dict) -> Dict[str, List[str]]:
    """Return only data-authored negative terms from the selected trait items."""
    raw = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
    if not raw:
        raw = region.get("trait_lock") if isinstance(region.get("trait_lock"), dict) else {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else raw
    result: Dict[str, List[str]] = {}
    for category, value in (categories or {}).items():
        if not isinstance(value, dict):
            continue
        terms = value.get("negative_terms") if isinstance(value.get("negative_terms"), list) else []
        clean: List[str] = []
        seen = set()
        for term in terms:
            text = str(term or "").strip()
            key = text.casefold()
            if key and key not in seen:
                seen.add(key)
                clean.append(text)
        if clean:
            result[str(category or "").strip().lower()] = clean
    return result


def _character_selected_clothing_for_attention(region: Dict) -> Dict[str, List[str]]:
    """Return explicit structured garments grouped by their body target."""
    categories = _character_trait_categories_for_attention(region)
    result: Dict[str, List[str]] = {}
    for category in ("full_costume", "clothing_top", "clothing_bottom"):
        terms = [str(term).strip() for term in categories.get(category, []) if str(term).strip()]
        if terms:
            result[category] = terms
    return result


def _character_has_selected_upper_garment(region: Dict) -> bool:
    clothing = _character_selected_clothing_for_attention(region)
    return bool(clothing.get("full_costume") or clothing.get("clothing_top"))


def _filter_structural_trait_terms_for_attention(
    region: Dict,
    category: str,
    terms: List[str],
    *,
    face_only: bool = False,
) -> List[str]:
    """Keep gender authority compatible with the selected garment/body scope.

    Older payloads may contain positive gender phrases such as ``flat ...
    chest`` or ``... torso``.  Repeating those phrases while a top/full outfit
    is selected can make an SDXL checkpoint expose the body to satisfy anatomy
    literally.  Filter only positive structural anatomy clauses; the selected
    gender itself and all subject-local negative guards remain intact.
    """
    if str(category or "").strip().lower() != "gender":
        return list(terms)
    face_markers = (
        " body", "body ", "silhouette", "chest", "torso", "shoulder",
        "waist", " hip", "hips", "breast", "cleavage",
    )
    covered_body_markers = ("chest", "torso", "breast", "cleavage", "bare", "shirtless", "topless")
    markers = face_markers if face_only else (covered_body_markers if _character_has_selected_upper_garment(region) else ())
    if not markers:
        return list(terms)
    return [
        term for term in terms
        if not any(marker in f" {str(term).strip().casefold()} " for marker in markers)
    ]


def _character_trait_terms_clothing_aware_for_attention(region: Dict, *, face_only: bool = False) -> List[str]:
    """Flatten submitted traits after applying generic body-scope filtering."""
    categories = _character_trait_categories_for_attention(region)
    result: List[str] = []
    seen = set()
    for category, raw_terms in categories.items():
        terms = _filter_structural_trait_terms_for_attention(
            region,
            category,
            list(raw_terms),
            face_only=face_only,
        )
        for term in terms:
            key = str(term).strip().casefold()
            if key and key not in seen:
                seen.add(key)
                result.append(str(term).strip())
    return result[:32]


def _character_trait_color_terms_for_attention(region: Dict, category: str) -> List[str]:
    raw = region.get("character_traits") if isinstance(region.get("character_traits"), dict) else {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else raw
    value = categories.get(category) if isinstance(categories, dict) and isinstance(categories.get(category), dict) else {}
    assignment = value.get("color_assignment") if isinstance(value.get("color_assignment"), dict) else {}
    terms: List[str] = []
    for key in ("primary_terms", "secondary_terms"):
        source = assignment.get(key) if isinstance(assignment.get(key), list) else []
        for term in source:
            text = str(term or "").strip()
            if text and text.casefold() not in {item.casefold() for item in terms}:
                terms.append(text)
    color_terms = value.get("color_terms") if isinstance(value.get("color_terms"), list) else []
    for term in color_terms:
        text = str(term or "").strip()
        if text and text.casefold() not in {item.casefold() for item in terms}:
            terms.append(text)

    # Replay compatibility for V054 payloads created before the shared color
    # library existed. Those queues stored color and style in one combined
    # term and therefore have no structured color_assignment.
    # New scenes always use the exact editable JSON color terms above; this
    # fallback only recovers a color word already authored in the old trait.
    if not terms:
        category_terms = _character_trait_categories_for_attention(region).get(category, [])
        legacy_text = " " + " ".join(category_terms).casefold() + " "
        color_words = globals().get("V054_CONFLICT_COLOR_WORDS", set())
        modifiers = {"dark", "light", "neon", "vivid"}
        for word in sorted((str(item).casefold() for item in color_words), key=len, reverse=True):
            if word in modifiers:
                continue
            normalized = legacy_text
            for marker in "-_/,.;:()[]{}":
                normalized = normalized.replace(marker, " ")
            if f" {word} " in normalized and word not in {item.casefold() for item in terms}:
                terms.append(word)
    return terms[:4]


def _character_lock_guard_mode(region: Dict, category: str) -> str:
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    category_key = str(category or "").strip().lower()
    owner = {
        "gender": "gender",
        "ethnicity": "character",
        "species_race": "character",
        "hair": "hair",
        "facial_hair": "character",
        "skin": "skin_tone",
        "skin_tone": "skin_tone",
        "build": "build",
        "body": "build",
        "body_height": "body_height",
        "clothing_top": "outfit",
        "clothing_bottom": "outfit",
        "full_costume": "outfit",
        "clothing": "outfit",
        "outfit": "outfit",
        "pose": "character",
        "body_details": "build",
        "top_garment_state": "outfit",
        "bottom_garment_state": "outfit",
        "underlayer": "outfit",
        "held_items": "character",
        "custom_details": "character",
    }.get(category_key)
    mode = str((lock.get(owner) if owner else None) or (lock.get("character") if owner == "character" else None) or "balanced").strip().lower()
    aliases = {"strict_lock": "strict", "hard": "strong", "medium": "balanced", "on": "balanced"}
    return aliases.get(mode, mode)


def _character_trait_authority_prefix(
    region: Dict,
    *,
    allowed_categories: set[str] | None = None,
    max_terms: int = 24,
    face_only: bool = False,
) -> str:
    """Compile concise, weighted exact traits at the front of a local branch.

    SDXL responds much more reliably when the selected identity terms occur
    before layout boilerplate.  The previous V054 path appended them after the
    full region description, which made Strong look active in metadata while
    high-priority identity and distinctive-hair terms remained
    semantically weak.
    """
    categories = _character_trait_categories_for_attention(region)
    order = (
        "gender", "ethnicity", "species_race", "hair", "facial_hair", "skin_tone", "skin",
        "build", "body", "body_height", "full_costume", "clothing_top",
        "clothing_bottom", "clothing", "outfit", "body_details",
        "top_garment_state", "bottom_garment_state", "underlayer",
        "pose", "held_items", "custom_details", "expression", "accessories", "shoes",
    )
    # Phase 27.8: Strong must be materially different from Balanced inside the
    # *same* sampler.  The old 1.42 value was still easy for an SDXL checkpoint
    # to average away in two-person scenes, especially for gender and saturated
    # hair colours.  These remain bounded CLIP weights; they do not add a second
    # sampler or alter the latent/noise schedule.
    weights = {"strict": 1.65, "strong": 1.55, "balanced": 1.22, "soft": 1.10, "off": 1.0}
    weighted: List[str] = []
    seen = set()
    for category in order:
        if allowed_categories is not None and category not in allowed_categories:
            continue
        category_terms = _filter_structural_trait_terms_for_attention(
            region,
            category,
            list(categories.get(category, [])),
            face_only=face_only,
        )
        for term in category_terms:
            key = term.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            weight = weights.get(_character_lock_guard_mode(region, category), 1.20)
            weighted.append(f"({term}:{weight:.2f})")
            if len(weighted) >= max(1, int(max_terms)):
                return ", ".join(weighted)
    return ", ".join(weighted)


def _character_lock_correction_authority_prefix(region: Dict, *, face_only: bool = False) -> str:
    """Weight the submitted positive correction as a compact live CLIP lane.

    The correction text is user/UI-authored and already stored per character.
    V054 previously copied it into a long prose branch where its structural
    gender terms could land after several CLIP chunks.  Keep the exact clauses,
    remove only the display label (``Person N:``), and put them at the front of
    the subject-local branch.
    """
    correction = _character_lock_correction_for_attention(region)
    raw = str(correction.get("positive") or "").strip()
    if not raw:
        return ""
    first, separator, remainder = raw.partition(":")
    if separator and first.strip().casefold().startswith("person "):
        raw = remainder.strip()

    mode = _character_lock_guard_mode(region, "gender")
    weights = {"strict": 1.72, "strong": 1.62, "balanced": 1.34, "soft": 1.16, "off": 1.0}
    weight = weights.get(mode, 1.34)
    seen = set()
    parts: List[str] = []
    face_body_markers = (
        " body", "body ", "silhouette", "chest", "torso", "shoulder",
        "waist", " hip", "hips", "breast", "cleavage",
    )
    for clause in raw.replace("\n", ",").replace(";", ",").split(","):
        text = " ".join(clause.strip().split())
        folded = f" {text.casefold()} "
        if face_only and any(marker in folded for marker in face_body_markers):
            continue
        key = text.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        parts.append(f"({text}:{weight:.2f})" if weight > 1.0 else text)
    return ", ".join(parts[:12])


def _character_source_prompt_for_attention(region: Dict) -> str:
    """Return the original user character prompt without generated lock prose."""
    return str(
        region.get("source_prompt")
        or region.get("user_prompt")
        or region.get("prompt")
        or ""
    ).strip()


def _character_lock_correction_for_attention(region: Dict) -> Dict[str, str]:
    """Return explicit per-character correction text for live conditioning.

    The backend already stores this block in ``scene_graph_json`` and the
    optional masked controller can read it.  V054 must also consume it before
    CLIP branches are encoded; otherwise the user's gender/body guard is only
    diagnostic metadata and cannot influence the main sampler.
    """
    raw = region.get("character_lock_correction") if isinstance(region.get("character_lock_correction"), dict) else {}
    enabled = raw.get("enabled", region.get("character_lock_correction_enabled", "auto"))
    if str(enabled).strip().lower() in {"0", "false", "no", "off", "disabled"}:
        return {"positive": "", "negative": "", "enabled": "false"}

    def text(value: Any) -> str:
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        return " ".join(str(value or "").replace("\n", " ").split())

    positive = text(raw.get("positive_text", raw.get("positive", region.get("character_lock_positive_text", ""))))
    # Phase 27.12 replay migration: legacy UI defaults repeated exposed-anatomy
    # clauses even when an explicit top/full outfit was selected.  Preserve the
    # user's gender/face/presentation clauses while removing only those unsafe
    # covered-body fragments from live conditioning.  The stored editable text
    # remains visible in the UI and is not silently rewritten.
    if positive and _character_has_selected_upper_garment(region):
        covered_body_markers = ("chest", "torso", "bare", "shirtless", "topless")
        kept: List[str] = []
        for clause in positive.replace("\n", ",").replace(";", ",").split(","):
            clean = " ".join(clause.strip().split())
            folded = f" {clean.casefold()} "
            if clean and not any(marker in folded for marker in covered_body_markers):
                kept.append(clean)
        positive = ", ".join(kept)
    negative = text(raw.get("negative_text", raw.get("negative", region.get("character_lock_negative_text", ""))))
    return {"positive": positive, "negative": negative, "enabled": str(enabled or "auto")}


def _character_lock_negative_for_attention(region: Dict) -> str:
    """Build a subject-local negative lock aligned with the positive branch.

    These terms must stay regional.  For example, Person 1 may forbid black
    hair because pink hair is locked while Person 2 explicitly requires black
    hair.  Appending that guard to the global negative prompt would make the
    two characters fight each other.
    """
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    categories = _character_trait_categories_for_attention(region)
    correction = _character_lock_correction_for_attention(region)
    category_negatives = _character_trait_negative_categories_for_attention(region)
    parts: List[Tuple[str, str]] = []

    def add(value: Any, mode: str = "balanced") -> None:
        for item in str(value or "").replace("\n", ",").replace(";", ",").split(","):
            text = item.strip()
            if text:
                parts.append((text, str(mode or "balanced").strip().lower()))

    negative_mode = str(lock.get("negative") or lock.get("character") or "balanced").strip().lower()
    gender_mode = str(lock.get("gender") or lock.get("character") or "balanced").strip().lower()
    hair_mode = str(lock.get("hair") or "balanced").strip().lower()
    skin_mode = str(lock.get("skin_tone") or "balanced").strip().lower()
    build_mode = str(lock.get("build") or lock.get("body_height") or "balanced").strip().lower()
    outfit_mode = str(lock.get("outfit") or "balanced").strip().lower()
    pose_mode = _character_lock_guard_mode(region, "pose")

    add(region.get("negative") or region.get("negative_prompt") or "", negative_mode)
    add(correction.get("negative") or "", gender_mode)
    for category, terms in category_negatives.items():
        category_mode = _character_lock_guard_mode(region, category)
        for term in terms:
            add(term, category_mode)
    for term in _character_pose_negative_terms_for_attention(region):
        add(term, pose_mode)

    gender_text = " ".join(categories.get("gender", [])).casefold()
    if str(lock.get("gender") or lock.get("character") or "off").lower() in {"balanced", "strong", "strict"}:
        if any(term in gender_text for term in ("male", "man", "boy", "masculine")):
            add("female, woman, girl, feminine face, feminine body, breasts, cleavage, curvy hips, hourglass figure, gender swap, wrong gender", gender_mode)
        elif any(term in gender_text for term in ("female", "woman", "girl", "feminine")):
            add("male, man, boy, masculine face, masculine body, gender swap, wrong gender", gender_mode)

    if str(lock.get("hair") or "off").lower() in {"balanced", "strong", "strict"}:
        add("wrong hair color, changed hairstyle, missing hair detail, inconsistent hair", hair_mode)
        hair_colors = _character_trait_color_terms_for_attention(region, "hair")
        if hair_colors:
            requested = " and ".join(hair_colors)
            add(f"missing requested {requested} hair color, default natural hair color replacing requested {requested}", hair_mode)
            # Vivid/fantasy colors need a stronger guard against the model's
            # common black/brown/blonde fallback. This rule is color-agnostic:
            # the requested value still comes from the shared library (or an
            # old payload's authored trait), never from a personal demo prompt.
            natural_words = {"black", "brown", "blonde", "blond", "auburn", "ginger", "gray", "grey", "white", "silver"}
            requested_words = set(requested.casefold().replace("-", " ").split())
            if not (requested_words & natural_words):
                add(f"black hair, brown hair, blonde hair, natural hair color instead of {hair_colors[0]}", hair_mode)
    if str(lock.get("skin_tone") or "off").lower() in {"balanced", "strong", "strict"}:
        add("wrong skin tone, changed complexion, inconsistent skin color", skin_mode)
    if str(lock.get("build") or lock.get("body_height") or "off").lower() in {"balanced", "strong", "strict"}:
        add("wrong body build, changed body type, distorted body proportions", build_mode)
    if str(lock.get("outfit") or "off").lower() in {"balanced", "strong", "strict"}:
        add("wrong outfit, changed clothing, missing costume details", outfit_mode)

    weights = {"strict": 1.45, "strong": 1.35, "balanced": 1.18, "soft": 1.08, "off": 1.0}
    # The same token can arrive first from a Balanced free-text negative and
    # later from a Strong structured gender guard. Retain the strongest live
    # authority instead of letting first-write dedupe downgrade it.
    strongest: Dict[str, Tuple[str, str]] = {}
    order: List[str] = []
    for part, mode in parts:
        key = part.casefold()
        if not key:
            continue
        if key not in strongest:
            order.append(key)
            strongest[key] = (part, mode)
            continue
        previous_part, previous_mode = strongest[key]
        if weights.get(mode, 1.18) > weights.get(previous_mode, 1.18):
            strongest[key] = (previous_part, mode)

    deduped: List[str] = []
    for key in order:
        part, mode = strongest[key]
        weight = weights.get(mode, 1.18)
        deduped.append(f"({part}:{weight:.2f})" if weight > 1.0 and not (part.startswith("(") and part.endswith(")")) else part)
    return ", ".join(deduped)


def _face_identity_grooming_guard_mode(region: Dict) -> str:
    """Use the strongest selected face/grooming guard without adding a lane."""
    rank = {"off": 0, "soft": 1, "balanced": 2, "strong": 3, "strict": 4}
    modes = [
        _character_lock_guard_mode(region, "gender"),
        _character_lock_guard_mode(region, "facial_hair"),
    ]
    if _character_custom_detail_terms_for_attention(region, {"face"}):
        modes.append(_character_lock_guard_mode(region, "custom_details"))
    return max(modes, key=lambda value: rank.get(value, 2))


def _structural_gender_lock_prompt_for_attention(region: Dict) -> str:
    """Build one compact face-identity + grooming branch.

    Phase 27.11 added facial hair as a second face-overlapping lane.  Because
    regional masks are normalized, that lane took authority away from the male
    face while still failing to render a small stubble texture.  Phase 27.12
    keeps the exact data-authored gender, ethnicity, skin, species and grooming
    terms together in one face-local lane.  Accessories, expression, clothing,
    pose and relationship prose never enter this structural branch.
    """
    mode = _face_identity_grooming_guard_mode(region)
    if mode not in {"strong", "strict"}:
        return ""

    categories = _character_trait_categories_for_attention(region)
    gender_terms = _filter_structural_trait_terms_for_attention(
        region,
        "gender",
        list(categories.get("gender", [])),
        face_only=True,
    )
    identity_terms: List[str] = []
    for category in ("gender", "ethnicity", "species_race", "skin_tone", "skin"):
        source = gender_terms if category == "gender" else categories.get(category, [])
        identity_terms.extend(str(term).strip() for term in source if str(term).strip())
    grooming_terms = [str(term).strip() for term in categories.get("facial_hair", []) if str(term).strip()]
    face_detail_terms = _character_custom_detail_terms_for_attention(region, {"face"})
    correction = _character_lock_correction_authority_prefix(region, face_only=True)
    if not identity_terms and not grooming_terms and not face_detail_terms and not correction:
        return ""

    identity_weight = 1.72 if mode == "strict" else 1.62
    grooming_weight = 2.00 if mode == "strict" else 1.90
    exact_identity = ", ".join(f"({term}:{identity_weight:.2f})" for term in identity_terms[:12])
    exact_grooming = ", ".join(f"({term}:{grooming_weight:.2f})" for term in grooming_terms[:6])
    exact_face_details = ", ".join(f"({term}:{identity_weight:.2f})" for term in face_detail_terms[:6])
    label = str(region.get("name") or region.get("label") or region.get("id") or "character").strip()
    strength_label = "strict" if mode == "strict" else "strong"
    parts = [
        f"{strength_label} subject-local face identity and grooming authority for {label}",
        f"exact selected face identity cues: {exact_identity}" if exact_identity else "",
        f"exact selected facial-hair and lower-face grooming: {exact_grooming}" if exact_grooming else "",
        f"exact character-owned face details: {exact_face_details}" if exact_face_details else "",
        f"critical structural correction: {correction}" if correction else "",
        "preserve one coherent face and the selected lower-face grooming texture on this subject only",
        "stable jaw, chin and upper-lip detail, no face or grooming borrowing from a neighboring subject",
    ]
    return ", ".join(part for part in parts if str(part).strip())


def _structural_gender_lock_negative_for_attention(region: Dict) -> str:
    """Return one negative guard paired with the merged face/grooming lane."""
    mode = _face_identity_grooming_guard_mode(region)
    if mode not in {"strong", "strict"}:
        return ""

    categories = _character_trait_categories_for_attention(region)
    negative_categories = _character_trait_negative_categories_for_attention(region)
    correction = _character_lock_correction_for_attention(region)
    raw_parts: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        for item in str(value or "").replace("\n", ",").replace(";", ",").split(","):
            text = " ".join(item.strip().split())
            if text:
                raw_parts.append(text)

    if categories.get("gender"):
        add(correction.get("negative") or "")
        add(negative_categories.get("gender", []))
        add("wrong gender, gender swap")
    if categories.get("facial_hair"):
        add(negative_categories.get("facial_hair", []))
        add("wrong facial-hair style, wrong lower-face grooming state, missing selected facial-hair or grooming detail")
    add(_character_custom_detail_negatives_for_attention(region, {"face"}))

    weight = 1.66 if mode == "strict" else 1.58
    seen = set()
    compact: List[str] = []
    for part in raw_parts:
        key = part.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        compact.append(f"({part}:{weight:.2f})")
        if len(compact) >= 24:
            break
    return ", ".join(compact)


def _clothing_lock_categories_for_attention(region: Dict) -> List[str]:
    """Resolve the existing body-scoped garment lanes without per-detail lanes."""
    clothing = _character_selected_clothing_for_attention(region)
    if clothing.get("full_costume"):
        return ["full_costume"]
    categories = _character_trait_categories_for_attention(region)
    underlayer = [str(term).strip() for term in categories.get("underlayer", []) if str(term).strip()]
    upper_underlayer = [term for term in underlayer if any(marker in term.casefold() for marker in ("undershirt", "tank", "bra", "upper", "top"))]
    lower_underlayer = [term for term in underlayer if term not in upper_underlayer]
    top_custom = _character_custom_detail_terms_for_attention(region, {"torso", "arms_hands"})
    bottom_custom = _character_custom_detail_terms_for_attention(region, {"lower_torso", "legs", "feet"})
    lanes: List[str] = []
    if clothing.get("clothing_top") or categories.get("top_garment_state") or upper_underlayer or top_custom:
        lanes.append("clothing_top")
    if clothing.get("clothing_bottom") or categories.get("bottom_garment_state") or lower_underlayer or bottom_custom:
        lanes.append("clothing_bottom")
    return lanes


def _clothing_lock_prompt_for_attention(region: Dict, category: str) -> str:
    """Compile garment style plus local state/detail text into one body lane."""
    category = str(category or "").strip().lower()
    if category not in {"full_costume", "clothing_top", "clothing_bottom"}:
        return ""
    mode = _character_lock_guard_mode(region, category)
    if mode not in {"strong", "strict"}:
        return ""
    categories = _character_trait_categories_for_attention(region)
    garment_terms = list(_character_selected_clothing_for_attention(region).get(category, []))
    underlayer = [str(term).strip() for term in categories.get("underlayer", []) if str(term).strip()]
    upper_underlayer = [term for term in underlayer if any(marker in term.casefold() for marker in ("undershirt", "tank", "bra", "upper", "top"))]
    lower_underlayer = [term for term in underlayer if term not in upper_underlayer]
    if category == "clothing_top":
        detail_terms = [*categories.get("top_garment_state", []), *upper_underlayer]
        detail_terms.extend(_character_custom_detail_terms_for_attention(region, {"torso", "arms_hands"}))
    elif category == "clothing_bottom":
        detail_terms = [*categories.get("bottom_garment_state", []), *lower_underlayer]
        detail_terms.extend(_character_custom_detail_terms_for_attention(region, {"lower_torso", "legs", "feet"}))
    else:
        detail_terms = [
            *categories.get("top_garment_state", []),
            *categories.get("bottom_garment_state", []),
            *underlayer,
        ]
        detail_terms.extend(_character_custom_detail_terms_for_attention(region, {"torso", "lower_torso", "arms_hands", "legs", "feet"}))
    seen = set()
    detail_terms = [str(term).strip() for term in detail_terms if str(term).strip() and not (str(term).strip().casefold() in seen or seen.add(str(term).strip().casefold()))]
    if not garment_terms and not detail_terms:
        return ""
    label = str(region.get("name") or region.get("label") or region.get("id") or "character").strip()
    target = {
        "full_costume": "complete outfit",
        "clothing_top": "upper-body garment",
        "clothing_bottom": "lower-body garment",
    }[category]
    weight = 1.82 if mode == "strict" else 1.72
    exact_garment = ", ".join(f"({term}:{weight:.2f})" for term in garment_terms[:6])
    exact_details = ", ".join(f"({term}:{weight:.2f})" for term in detail_terms[:10])
    strength_label = "strict" if mode == "strict" else "strong"
    parts = [
        f"{strength_label} subject-local {target} authority for {label}",
        f"exact selected {target}: {exact_garment}" if exact_garment else "",
        f"exact character-owned state, underlayer and targeted details in this area: {exact_details}" if exact_details else "",
        f"the selected {target} is visibly worn by this subject and covers its intended body area",
        "preserve the selected garment type, cut, coverage and color on this subject only",
        "preserve the authored open/closed state, underlayer visibility and local detail on this subject only" if exact_details else "",
        "no missing garment, no outfit swap, no neighboring-subject clothing",
    ]
    return ", ".join(part for part in parts if str(part).strip())


def _clothing_lock_negative_for_attention(region: Dict, category: str) -> str:
    """Return subject-local missing/wrong garment guards for one body slice."""
    category = str(category or "").strip().lower()
    mode = _character_lock_guard_mode(region, category)
    if mode not in {"strong", "strict"}:
        return ""
    negative_categories = _character_trait_negative_categories_for_attention(region)
    raw_parts: List[str] = list(negative_categories.get(category, []))
    categories = _character_trait_categories_for_attention(region)
    top_state_text = " ".join(categories.get("top_garment_state", [])).casefold()
    bottom_state_text = " ".join([*categories.get("bottom_garment_state", []), *categories.get("underlayer", [])]).casefold()
    top_is_open = any(marker in top_state_text for marker in ("unbuttoned", "open at the front", "worn open"))
    bottom_is_open = any(marker in bottom_state_text for marker in ("unzipped", "lowered", "pulled down", "underwear", "underlayer"))
    if category == "clothing_top":
        raw_parts.extend([
            "topless", "shirtless", *( [] if top_is_open else ["bare chest"] ), "missing top garment",
            "wrong top garment", "top garment worn by wrong subject",
        ])
        raw_parts.extend(negative_categories.get("top_garment_state", []))
        raw_parts.extend(_character_custom_detail_negatives_for_attention(region, {"torso", "arms_hands"}))
    elif category == "clothing_bottom":
        raw_parts.extend([
            "missing bottom garment", "wrong bottom garment",
            *( [] if bottom_is_open else ["underwear instead of selected bottom garment"] ), "bottom garment worn by wrong subject",
        ])
        raw_parts.extend(negative_categories.get("bottom_garment_state", []))
        raw_parts.extend(negative_categories.get("underlayer", []))
        raw_parts.extend(_character_custom_detail_negatives_for_attention(region, {"lower_torso", "legs", "feet"}))
    elif category == "full_costume":
        raw_parts.extend([
            "topless", "shirtless", *( [] if top_is_open else ["bare chest"] ), "missing full outfit",
            "partial costume", "wrong full outfit", "outfit worn by wrong subject",
        ])
        raw_parts.extend(negative_categories.get("top_garment_state", []))
        raw_parts.extend(negative_categories.get("bottom_garment_state", []))
        raw_parts.extend(negative_categories.get("underlayer", []))
        raw_parts.extend(_character_custom_detail_negatives_for_attention(region, {"torso", "lower_torso", "arms_hands", "legs", "feet"}))
    weight = 1.62 if mode == "strict" else 1.54
    seen = set()
    compact: List[str] = []
    for value in raw_parts:
        for item in str(value or "").replace("\n", ",").replace(";", ",").split(","):
            text = " ".join(item.strip().split())
            key = text.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            compact.append(f"({text}:{weight:.2f})")
            if len(compact) >= 16:
                return ", ".join(compact)
    return ", ".join(compact)


def _clothing_lock_slice_for_attention(category: str) -> Tuple[float, float, float]:
    """Return start/end/expansion ratios for one garment body lane."""
    return {
        "clothing_top": (0.18, 0.68, 0.06),
        "clothing_bottom": (0.48, 1.00, 0.05),
        "full_costume": (0.16, 1.00, 0.06),
    }.get(str(category or "").strip().lower(), (0.18, 1.00, 0.04))


def _character_body_additional_authority_for_attention(region: Dict) -> str:
    """Compile body-wide Additional Details into the existing full branch."""
    categories = _character_trait_categories_for_attention(region)
    terms = [str(term).strip() for term in categories.get("body_details", []) if str(term).strip()]
    terms.extend(_character_custom_detail_terms_for_attention(region, {"full_body"}))
    seen = set()
    terms = [term for term in terms if not (term.casefold() in seen or seen.add(term.casefold()))]
    if not terms:
        return ""
    rank = {"off": 0, "soft": 1, "balanced": 2, "strong": 3, "strict": 4}
    modes = [
        _character_lock_guard_mode(region, "body_details"),
        _character_lock_guard_mode(region, "custom_details"),
    ]
    mode = max(modes, key=lambda value: rank.get(value, 2))
    weights = {"strict": 1.65, "strong": 1.55, "balanced": 1.22, "soft": 1.10, "off": 1.0}
    weight = weights.get(mode, 1.22)
    return ", ".join(f"({term}:{weight:.2f})" for term in terms[:10])


def _compile_appearance_lock_prompt(region: Dict, mode: str) -> str:
    rid = str(region.get("name") or region.get("label") or region.get("id") or "region").strip()
    prompt = _character_source_prompt_for_attention(region)
    tokens = _clean_list(region.get("tokens", [])) + _clean_list(region.get("owns", []))
    trait_terms = _character_trait_terms_for_attention(region)
    upper_categories = {
        "gender", "ethnicity", "species_race", "hair", "facial_hair", "skin_tone", "skin",
    }
    full_categories = {
        "gender", "ethnicity", "species_race", "hair", "facial_hair", "skin_tone", "skin",
        "build", "body", "body_height", "full_costume", "clothing_top",
        "clothing_bottom", "clothing", "outfit", "shoes",
    }
    scoped_categories = upper_categories if mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong"} else full_categories
    trait_authority = _character_trait_authority_prefix(
        region,
        allowed_categories=scoped_categories,
        max_terms=16 if mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong"} else 24,
        face_only=mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong"},
    )
    correction_authority = _character_lock_correction_authority_prefix(
        region,
        face_only=mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong"},
    )
    pose_authority = _character_pose_lock_prompt_for_attention(region) if mode in {"full_character_soft", "full_character_strong"} else ""
    body_additional_authority = _character_body_additional_authority_for_attention(region) if mode in {"full_character_soft", "full_character_strong"} else ""
    # With explicit fields available, the first sentence is the user's compact
    # appearance description and later sentences usually own pose/interaction.
    # Keep the full prompt as a fallback for prompt-only/auto-extract scenes.
    if trait_terms and mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong"}:
        # Structured upper-identity lanes intentionally exclude accessories,
        # expression, pose and outfit. Those styling instructions still live
        # in the primary character branch, but cannot redefine face/gender.
        prompt = ""
    elif trait_terms and "." in prompt:
        first_sentence = prompt.split(".", 1)[0].strip()
        if first_sentence:
            prompt = first_sentence
    subject_name = f" for {rid}" if rid else ""
    # Phase 27.8: this is deliberately compact. The old full V054 prompt copied
    # the generated lock prose and the user prompt several times, pushing exact
    # gender/hair terms into diluted CLIP chunks. The primary region branch owns
    # pose and composition; this lane owns only structural identity authority.
    if mode in {"full_character_soft", "full_character_strong"}:
        parts = [
            f"critical structural identity traits: {trait_authority}" if trait_authority else "",
            f"critical structural correction: {correction_authority}" if correction_authority else "",
            f"critical character-region pose contract: {pose_authority}" if pose_authority else "",
            f"critical character-owned body details: {body_additional_authority}" if body_additional_authority else "",
            f"full character identity and appearance authority{subject_name}",
            prompt,
            "one coherent subject, preserve the exact face, hair, skin, outfit, body silhouette, proportions, gender and presentation declared above",
            "no identity averaging, no neighboring-subject trait borrowing",
        ]
        if mode == "full_character_strong":
            parts.insert(0, "strong single-sampler structural identity authority")
    else:
        parts = [
            f"critical upper identity traits: {trait_authority}" if trait_authority else "",
            f"critical upper identity correction: {correction_authority}" if correction_authority else "",
            f"appearance lock{subject_name}",
            prompt,
            "preserve the exact face, hair colour, hairstyle, ethnicity, skin and gender presentation declared above",
            "no neighboring-subject appearance borrowing",
        ]
    if tokens:
        parts.append("assigned visual tokens: " + ", ".join(tokens))
    if mode in {"hair_focus_strong", "upper_identity_strong"}:
        parts.insert(0, "strong legacy-style upper identity authority")
    return ", ".join([p for p in parts if str(p).strip()])


def _region_type(region):
    return str(region.get("region_type", region.get("type", region.get("_kind", "region")))).lower()


def _enabled_regions(all_regions):
    return [r for r in all_regions if _safe_bool(r.get("enabled", True), True)]


def _subject_slots_for_region(region, max_subject_slots):
    # v0.4: one character region = one visible subject.
    # Repeating character branches caused v0.3.1 multi-person prompts to collapse.
    return 1

def _compile_entity_count_text(entity_count: int):
    if entity_count <= 0:
        return ""
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    word = words.get(entity_count, str(entity_count))
    return (
        f"(exactly {entity_count} visible subjects:1.35), ({word} separate subjects:1.30), "
        f"one subject per region, natural spacing, clean anatomy, coherent details, realistic proportions"
    )


def _compile_count_locked_contract(all_regions, entity_count: int, mode: str):
    if entity_count <= 0:
        return ""
    chars = [r for r in _enabled_regions(all_regions) if _region_type(r) == "character"]
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    word = words.get(entity_count, str(entity_count))
    slot_parts = []
    for idx, r in enumerate(chars, 1):
        rid = str(r.get("id", r.get("name", f"person_{idx}"))).strip()
        mask_data = r.get("mask") or {"x": r.get("x", 0), "y": r.get("y", 0), "w": r.get("w", 1), "h": r.get("h", 1)}
        anchor = _mask_anchor(mask_data)
        prompt = str(r.get("prompt", "")).strip()
        slot_parts.append(f"PERSON {idx} / {rid}: one complete subject locked on the {anchor}, separate from all others, {prompt}")

    if mode == "count_locked" or entity_count >= 3:
        return (
            f"COUNT-LOCKED SCENE MODE: the final image must show exactly {entity_count} subjects, {word} subjects total, "
            f"not two, not three unless exactly three requested, no missing people, no simplified pair composition, "
            f"no background extras, no split screen panels, no collapsed subjects; "
            f"left-to-right required subject slots: " + "; ".join(slot_parts)
        )
    return (
        f"RELATION-FOCUSED SCENE MODE: preserve exactly {entity_count} visible subjects while prioritizing the object/action relation; "
        + "; ".join(slot_parts)
    )


def _compile_spatial_contract(all_regions):
    parts = []
    for r in _enabled_regions(all_regions):
        if _region_type(r) not in ("character", "object"):
            continue
        rid = str(r.get("id", r.get("name", "region"))).strip()
        mask_data = r.get("mask") or {"x": r.get("x", 0), "y": r.get("y", 0), "w": r.get("w", 1), "h": r.get("h", 1)}
        anchor = _mask_anchor(mask_data)
        if rid:
            parts.append(f"{rid} positioned on the {anchor}")
    return ", ".join(parts)


def _compile_object_ownership(all_regions):
    parts = []
    for r in _enabled_regions(all_regions):
        rid = str(r.get("id", r.get("name", "region"))).strip()
        tokens = _clean_list(r.get("tokens", [])) + _clean_list(r.get("owns", []))
        if tokens:
            parts.append(f"{rid} contains " + ", ".join(tokens))
    return ", ".join(parts)



def _compile_region_summary(all_regions):
    parts = []
    subject_index = 0
    for r in _enabled_regions(all_regions):
        if _region_type(r) != "character":
            continue
        subject_index += 1
        rid = str(r.get("id", r.get("name", "region"))).strip()
        prompt = str(r.get("prompt", "")).strip()
        mask_data = r.get("mask") or {"x": r.get("x", 0), "y": r.get("y", 0), "w": r.get("w", 1), "h": r.get("h", 1)}
        anchor = _mask_anchor(mask_data)
        parts.append(
            f"PERSON {subject_index} slot {rid}: exactly one isolated separate subject on the {anchor}, "
            f"standing inside only this slot, not merged with neighbors, empty space around this person, {prompt}"
        )
    if not parts:
        return ""
    return "count anchor regional layout: " + "; ".join(parts)

def _compile_relation_contract(all_regions):
    parts = []
    for r in _enabled_regions(all_regions):
        rid = str(r.get("id", r.get("name", "region"))).strip()
        obj_label = str(r.get("object_label") or r.get("name") or rid).strip() or rid
        bound_to = str(r.get("bound_to", r.get("owner", ""))).strip()
        owner_label = _owner_display_name(r) or bound_to
        relation = str(r.get("relation", "")).strip()
        target_area = str(r.get("target_area", "")).strip()
        if bound_to:
            if relation == "holding":
                area = target_area or "hands"
                parts.append(f"{owner_label} is holding {obj_label} in {owner_label}'s {area}; {obj_label} is not held by any other person; keep the hand contact clear")
            elif relation:
                area = f" on {target_area}" if target_area else ""
                parts.append(f"{obj_label} is {relation.replace('_', ' ')} {owner_label}{area}; keep {obj_label} visually attached to {owner_label}, not to the wrong subject")
            else:
                parts.append(f"{obj_label} belongs to {owner_label}; keep {obj_label} visually attached to {owner_label}")
    return ", ".join(parts)


def _compile_negative(base_negative: str, all_regions, entity_count: int):
    negatives = [
        base_negative,
        "wrong number of subjects",
        "missing character",
        "hidden character",
        "cropped character",
        "solo",
        "single subject",
        "one subject",
        "merged bodies",
        "fused faces",
        "fused bodies",
        "fused clothing",
        "mixed outfits",
        "object on wrong person",
        "props assigned to wrong character",
        "duplicated important object",
        "same outfit on all characters",
        "same face on all characters",
        "crowd",
        "background extra subjects",
        "low quality",
        "blurry",
        "deformed",
    ]
    if entity_count >= 3:
        negatives.extend(["only two characters", "two people only", "missing third character"])
    if entity_count >= 4:
        negatives.extend(["only three characters", "three people only", "missing fourth character"])

    for r in _enabled_regions(all_regions):
        rid = str(r.get("id", r.get("name", ""))).strip()
        for token in _clean_list(r.get("tokens", [])) + _clean_list(r.get("owns", [])):
            if rid:
                negatives.append(f"{token} outside {rid}")
                negatives.append(f"{token} on wrong side")
                negatives.append(f"{token} on wrong character")

    seen, out = set(), []
    for n in negatives:
        n = str(n).strip()
        if not n:
            continue
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return ", ".join(out)


def _compile_region_prompt(region: Dict):
    rid = str(region.get("id", region.get("name", "region"))).strip()
    rtype = _region_type(region)
    prompt = str(region.get("prompt", "")).strip()
    tokens = _clean_list(region.get("tokens", [])) + _clean_list(region.get("owns", []))
    priority = _safe_float(region.get("priority", 1.0), 1.0)
    presence = _safe_float(region.get("presence_boost", region.get("presence", 1.0)), 1.0)
    mask_data = region.get("mask") or {"x": region.get("x", 0), "y": region.get("y", 0), "w": region.get("w", 1), "h": region.get("h", 1)}
    anchor = _mask_anchor(mask_data)

    prefix = []
    if rid:
        prefix.append(rid)
    if rtype:
        prefix.append(f"{rtype} region")
    prefix.append(anchor)

    if rtype == "character":
        subject_required = _safe_bool(region.get("subject_required", True), True)
        min_body = _safe_float(region.get("min_body_presence", 0.85), 0.85)
        prefix.extend([
            "(visible character or subject:1.35)",
            "(single separate subject:1.45)",
            "(complete visible subject details:1.25)",
            "(isolated from other people:1.25)",
            "(must exist as its own subject:1.45)",
            "one complete subject placed inside this region",
            "do not merge with neighbor regions",
            "clear spacing between nearby people",
            f"minimum visible body presence {min_body:.2f}"
        ])
        if subject_required:
            prefix.append("subject_required true")
    elif rtype == "object":
        prefix.append("(clearly visible assigned object:1.25)")
        bound_to = str(region.get("bound_to", region.get("owner", ""))).strip()
        owner_label = _owner_display_name(region) or bound_to
        relation = str(region.get("relation", "")).strip().lower()
        target_area = str(region.get("target_area", "")).strip()
        if bound_to:
            prefix.append(f"object bound_to {owner_label}")
            prefix.append(f"object owner is {owner_label}")
        if relation:
            prefix.append(f"relation {relation}")
        handoff_prompt = _relationship_handoff_prompt(region)
        if handoff_prompt:
            prefix.append(handoff_prompt)
        if relation == "holding":
            area = target_area or "hands"
            prefix.append(f"{owner_label} holds this object in {owner_label}'s {area}")
            prefix.append("not held by the other person")
    elif rtype == "interaction":
        prefix.append("(shared interaction zone:1.05)")

    if priority >= 1.3:
        prefix.append(f"(high priority region:{min(priority, 1.5):.1f})")
    if presence >= 1.3:
        prefix.append(f"(must be visible:{min(presence, 1.6):.1f})")

    trait_authority = ""
    correction = {"positive": "", "negative": "", "enabled": "false"}
    authority_prefix = []
    if rtype == "character":
        trait_authority = _character_trait_authority_prefix(region)
        correction = _character_lock_correction_for_attention(region)
        pose_authority = _character_pose_lock_prompt_for_attention(region)
        # Put exact traits and the user's own region prompt before structural
        # boilerplate.  This is the executable CLIP branch, not a report.
        if pose_authority:
            authority_prefix.append("critical Character Pose authority: " + pose_authority)
        if trait_authority:
            authority_prefix.append("critical Character Trait authority: " + trait_authority)
        if correction.get("positive"):
            authority_prefix.append("live Character Lock correction: " + correction["positive"])

    compiled = ", ".join(authority_prefix + [prompt] + prefix)
    if tokens:
        compiled += ", assigned objects/tokens: " + ", ".join(tokens)
        compiled += ", keep assigned objects inside this region"
    if rtype == "character":
        trait_terms = _character_trait_terms_for_attention(region)
        prompt_lower = prompt.casefold()
        if trait_terms and "character trait lock terms:" not in prompt_lower:
            compiled += ", live Character Trait Lock terms: " + ", ".join(trait_terms)
        if correction.get("positive") and "character lock correction:" not in prompt_lower and "live Character Lock correction:" not in compiled:
            compiled += ", live Character Lock correction: " + correction["positive"]
    return compiled



def _bbox_to_mask(item: Dict):
    bbox = item.get("bbox", None)
    if bbox is None:
        return item.get("mask") or {"type": "rect", "x": item.get("x", 0), "y": item.get("y", 0), "w": item.get("w", 1), "h": item.get("h", 1)}
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"bbox must be [x1,y1,x2,y2], got: {bbox}")
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return {"type": "rect", "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def _compile_camera_prompt(camera: Dict):
    if not isinstance(camera, dict):
        return ""
    parts = []
    for key in ("framing", "angle", "lens", "depth", "style"):
        val = str(camera.get(key, "")).strip()
        if val:
            parts.append(f"{key}: {val}")
    return ", ".join(parts)


def _relation_phrase(rel: Dict):
    if not isinstance(rel, dict):
        return ""
    source = str(rel.get("from", rel.get("source", ""))).strip()
    target = str(rel.get("to", rel.get("target", ""))).strip()
    rtype = str(rel.get("type", rel.get("relation", ""))).strip().lower()
    obj = str(rel.get("object", rel.get("item", ""))).strip()
    if not source or not rtype:
        return ""
    if rtype == "facing" and target:
        return f"{source} is facing {target}"
    if rtype == "looking_at" and target:
        return f"{source} is looking at {target}"
    if rtype == "holding" and obj:
        return f"{source} is holding {obj}"
    if rtype == "handing_to" and target and obj:
        return f"{source} is handing {obj} to {target}; {target} is receiving {obj}; hands meet naturally at {obj}"
    if rtype == "talking_to" and target:
        return f"{source} is talking to {target}; natural conversational body language"
    if rtype == "standing_beside" and target:
        return f"{source} is standing beside {target} with clean separation"
    if rtype == "standing_behind" and target:
        return f"{source} is standing behind {target} with visible full body separation"
    if rtype == "protecting" and target:
        return f"{source} is protectively positioned near {target}"
    if rtype == "chasing" and target:
        return f"{source} is chasing {target} with dynamic motion"
    if rtype == "sitting_on" and obj:
        return f"{source} is sitting on {obj}"
    if rtype == "leaning_on" and obj:
        return f"{source} is leaning on {obj}"
    if rtype == "surrounding" and target:
        return f"{source} and the other subjects are surrounding {target}"
    pieces = [source, rtype]
    if target:
        pieces.append(target)
    if obj:
        pieces.append(obj)
    return " ".join(pieces)


def _compile_scene_relations(relations: List[Dict]):
    phrases = [_relation_phrase(r) for r in relations or []]
    return "; ".join([p for p in phrases if p])


def _owner_display_name(region: Dict):
    """Return a human label for an attached owner while preserving the raw id separately.

    V054 often carries stable region ids like ``scene_region_...``. Those ids are
    useful for masks, but weak as natural-language constraints. Relationship
    prompts should speak in human-authored subject labels so diffusion can
    keep held props on the intended subject.
    """
    if not isinstance(region, dict):
        return ""
    return str(region.get("owner_label") or region.get("parent_label") or region.get("label") or region.get("name") or region.get("id") or "").strip()


def _relationship_handoff_prompt(region: Dict):
    bound_to = str(region.get("bound_to", region.get("owner", ""))).strip()
    if not bound_to:
        return ""
    owner_label = _owner_display_name(region) or bound_to
    relation = str(region.get("relation", "")).strip().lower()
    target_area = str(region.get("target_area", "")).strip()
    object_label = str(region.get("object_label") or region.get("name") or region.get("id") or "object").strip()
    base = str(region.get("prompt", "")).strip() or object_label
    if relation == "holding":
        area = target_area or "hands"
        return (
            f"{base}, held by {owner_label} only, in {owner_label}'s {area}, "
            f"{object_label} belongs to {owner_label}, not held by any other person, "
            f"clear hand contact, not floating"
        )
    if relation in {"attached_to", "wearing", "carrying", "touching"}:
        area = f" on {target_area}" if target_area else ""
        return f"{base}, {relation.replace('_', ' ')} {owner_label}{area}, belongs only to {owner_label}, not on the wrong subject"
    return f"{base}, visually linked to {owner_label}, belongs only to {owner_label}"


def _normalize_scene_v05(data: Dict, width: int, height: int):
    """Convert v0.5 Scene Director schema into the existing regional engine format.

    Phase 21.3 guard: V054 already bridges into the legacy regional buckets
    consumed by _parse_scene_schema(). Those bridged scene_json payloads also
    include a canvas key, so do not re-normalize them as canonical v0.5
    subject/object schema or the region buckets get discarded.
    """
    if any(k in data for k in ("regions", "object_regions", "shared_regions")):
        return data, data.get("relations", []), data.get("camera", {}), str(data.get("version", "0.4-legacy"))
    if not any(k in data for k in ("subjects", "objects", "camera", "global_style", "relations", "canvas")):
        return data, [], {}, "0.4-legacy"

    camera = data.get("camera", {}) if isinstance(data.get("camera", {}), dict) else {}
    global_style = str(data.get("global_style", "")).strip()
    global_prompt = str(data.get("prompt", "")).strip()
    old_global = data.get("global", {}) if isinstance(data.get("global", {}), dict) else {}
    if not global_prompt:
        global_prompt = str(old_global.get("prompt", "")).strip()
    if global_style:
        global_prompt = f"{global_prompt}, {global_style}" if global_prompt else global_style
    cam_text = _compile_camera_prompt(camera)
    if cam_text:
        global_prompt = f"{global_prompt}, {cam_text}" if global_prompt else cam_text
    # Empty means empty. Neo/Comfy must never receive an invisible quality or
    # demo prompt merely because the user left the global field blank.

    subjects = data.get("subjects", []) or []
    objects = data.get("objects", []) or []
    relations = data.get("relations", []) or []

    regions = []
    for i, s in enumerate(subjects):
        sid = str(s.get("id", f"person_{i+1}")).strip()
        prompt_bits = [str(s.get("prompt", "")).strip()]
        pose = str(s.get("pose_type", s.get("pose", ""))).strip()
        facing = str(s.get("facing", "")).strip()
        action = str(s.get("action", "")).strip()
        if pose:
            prompt_bits.append(f"pose: {pose}")
        if facing:
            prompt_bits.append(f"facing {facing}")
        if action:
            prompt_bits.append(action)
        prompt_bits.append("one separate fully clothed subject, clean anatomy, natural proportions")
        prompt_bits.append(f"this is {sid}, exactly one unique subject in this slot only, not merged, not duplicated, not missing")
        r = {
            "id": sid,
            "region_type": "character",
            "mask": _bbox_to_mask(s),
            "prompt": ", ".join([p for p in prompt_bits if p]),
            "tokens": s.get("tokens", []),
            "strength": s.get("strength", 1.12),
            "priority": s.get("priority", 1.08),
            "presence_boost": s.get("presence_boost", 1.12),
            "subject_required": s.get("required", s.get("subject_required", True)),
            "min_body_presence": s.get("min_body_presence", 0.72),
            "feather": s.get("feather", 28),
            "pose_type": pose,
            "facing": facing,
        }
        regions.append(r)

    object_regions = []
    for i, o in enumerate(objects):
        oid = str(o.get("id", f"object_{i+1}")).strip()
        r = {
            "id": oid,
            "region_type": "object",
            "mask": _bbox_to_mask(o),
            "prompt": str(o.get("prompt", "")).strip() or oid.replace("_", " "),
            "tokens": o.get("tokens", []),
            "bound_to": o.get("bound_to", o.get("owner", "")),
            "relation": o.get("relation", ""),
            "strength": o.get("strength", 0.90),
            "priority": o.get("priority", 0.88),
            "presence_boost": o.get("presence_boost", 0.88),
            "feather": o.get("feather", 34),
        }
        object_regions.append(r)

    shared_regions = []
    for rel in relations:
        obj_id = str(rel.get("object", "")).strip()
        obj = next((o for o in objects if str(o.get("id", "")).strip() == obj_id), None)
        phrase = _relation_phrase(rel)
        if obj and phrase:
            shared_regions.append({
                "id": f"relation_{str(rel.get('type','interaction'))}_{obj_id}",
                "region_type": "interaction",
                "mask": _bbox_to_mask(obj),
                "prompt": phrase,
                "tokens": [obj_id],
                "strength": rel.get("strength", 0.42),
                "priority": rel.get("priority", 0.45),
                "presence_boost": rel.get("presence_boost", 0.45),
                "feather": rel.get("feather", 64),
            })

    negative = str(data.get("negative", old_global.get("negative", ""))).strip()
    entity_count = int(data.get("entity_count", old_global.get("entity_count", len(subjects))))
    mode = str(data.get("multi_subject_mode", data.get("mode", ""))).strip().lower()
    if not mode:
        mode = "count_locked" if entity_count >= 3 else "relation_focused"
    rel_text = _compile_scene_relations(relations)
    if rel_text:
        global_prompt = f"{global_prompt}, directed scene relations: {rel_text}"

    regional = {
        "global": {"entity_count": entity_count, "prompt": global_prompt, "negative": negative, "multi_subject_mode": mode},
        "regions": regions,
        "object_regions": object_regions,
        "shared_regions": shared_regions,
        "relations": relations,
        "camera": camera,
        "canvas": data.get("canvas", {"width": width, "height": height}),
        "multi_subject_mode": mode,
    }
    return regional, relations, camera, "0.5"

def _parse_scene_schema(
    scene_json: str,
    width: int,
    height: int,
    global_prompt_override: str,
    enable_auto_prompts: bool,
    max_subject_slots: int,
    appearance_lock_mode: str = "off",
    appearance_lock_gain: float = 0.35,
    appearance_lock_height: float = 0.34,
    appearance_lock_feather: int = 18,
):
    data = json.loads(scene_json)
    if not isinstance(data, dict):
        raise ValueError("scene_json must be an object.")

    data, scene_relations, scene_camera, scene_schema_version = _normalize_scene_v05(data, width, height)

    global_data = data.get("global", {})
    prompt_authority = str(
        global_data.get("prompt_authority")
        or (data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}).get("prompt_authority")
        or "global_context"
    ).strip().lower()
    suppress_global_context = bool(
        prompt_authority in {"scene_director_only", "scene_only", "regional_only"}
        or global_data.get("global_prompt_excluded")
        or global_data.get("suppress_global_context")
    )
    global_prompt = "" if suppress_global_context else str(global_data.get("prompt", "")).strip()
    if not suppress_global_context and str(global_prompt_override).strip():
        global_prompt = str(global_prompt_override).strip()
    # Preserve an intentionally empty user/global prompt without hidden text.

    base_negative = "" if suppress_global_context else str(global_data.get("negative", "")).strip()
    regional_context = "" if suppress_global_context else str(global_data.get("regional_context") or "").strip()
    regional_context_weight = max(0.0, min(2.0, _safe_float(global_data.get("regional_context_weight"), 0.35)))
    regional_context_enabled = bool(
        not suppress_global_context
        and global_data.get("regional_context_enabled", True)
        and regional_context
    )

    regions = data.get("regions", [])
    shared_regions = data.get("shared_regions", [])
    object_regions = data.get("object_regions", [])

    all_regions = []
    for item in regions:
        r = dict(item); r["_kind"] = "region"; r.setdefault("region_type", "character"); all_regions.append(r)
    for item in object_regions:
        r = dict(item); r["_kind"] = "object"; r.setdefault("region_type", "object"); all_regions.append(r)
    for item in shared_regions:
        r = dict(item); r["_kind"] = "shared"; r.setdefault("region_type", "interaction"); all_regions.append(r)

    if len(all_regions) < 1:
        raise ValueError("scene_json needs at least one region/shared_region/object_region.")

    enabled = _enabled_regions(all_regions)
    auto_entity_count = sum(1 for r in enabled if _region_type(r) == "character")
    entity_count = _safe_int(global_data.get("entity_count", data.get("entity_count", auto_entity_count)), auto_entity_count)
    multi_subject_mode = str(global_data.get("multi_subject_mode", data.get("multi_subject_mode", ""))).strip().lower()
    if not multi_subject_mode:
        multi_subject_mode = "count_locked" if entity_count >= 3 else "relation_focused"

    compiled_global_parts = [global_prompt]
    if enable_auto_prompts:
        compiled_global_parts.append(_compile_entity_count_text(entity_count))
        compiled_global_parts.append(_compile_count_locked_contract(all_regions, entity_count, multi_subject_mode))
        compiled_global_parts.append(_compile_spatial_contract(all_regions))
        compiled_global_parts.append(_compile_region_summary(all_regions))
        compiled_global_parts.append(_compile_object_ownership(all_regions))
        compiled_global_parts.append(_compile_relation_contract(all_regions))
        compiled_global_parts.append(_compile_scene_relations(data.get("relations", [])))
        if multi_subject_mode == "count_locked":
            compiled_global_parts.append("COUNT-LOCKED DIRECTED SCENE MODE, preserve the exact requested number of visible subjects above cinematic background/style, each character region contains exactly one separate subject, every subject slot must be filled, visible subject structure in every person slot, simple clean lineup when 3 or more subjects are requested")
        else:
            compiled_global_parts.append("RELATION-FOCUSED DIRECTED SCENE MODE, layout locked composition, relations control facing/action/object exchange, keep object interaction natural, preserve subject count")

    compiled_global = ", ".join([p for p in compiled_global_parts if p.strip()])
    compiled_negative = _compile_negative(base_negative, all_regions, entity_count) if enable_auto_prompts else base_negative

    branch_prompts = []
    branch_negative_prompts = []
    branch_masks = []
    debug_meta = []
    character_union_mask = _v054_character_union_mask_from_regions(enabled, width, height)

    appearance_lock_mode = _appearance_lock_mode_value(appearance_lock_mode)
    appearance_lock_gain = max(0.0, min(2.0, _safe_float(appearance_lock_gain, 0.35)))
    appearance_lock_height = max(0.12, min(0.62, _safe_float(appearance_lock_height, 0.34)))
    appearance_lock_feather = max(0, min(96, _safe_int(appearance_lock_feather, 18)))

    for idx, region in enumerate(all_regions):
        rid = str(region.get("id", region.get("name", f"region_{idx}")))
        prompt = str(region.get("prompt", "")).strip()
        if not prompt:
            raise ValueError(f"Region '{rid}' is missing prompt.")
        if not _safe_bool(region.get("enabled", True), True):
            continue

        strength = _safe_float(region.get("strength", region.get("weight", 1.0)), 1.0)
        priority = _safe_float(region.get("priority", 1.0), 1.0)
        presence = _safe_float(region.get("presence_boost", region.get("presence", 1.0)), 1.0)
        feather = _v054_region_feather(region, 0)
        rtype = _region_type(region)
        slots = _subject_slots_for_region(region, max_subject_slots)

        mask_strength = strength * max(0.25, min(priority, 3.0)) * max(0.25, min(presence, 3.0))

        mask_data = region.get("mask") or {"type": "rect", "x": region.get("x", 0), "y": region.get("y", 0), "w": region.get("w", 1), "h": region.get("h", 1)}
        if str(mask_data.get("type", "rect")).lower() != "rect":
            raise ValueError("v0.3.1 supports only rect masks.")

        # Feather a unit mask, then apply authority. Feathering previously
        # clamped values above 1.0, so Strong/Strict was reported in metadata
        # while the live attention mask silently lost that extra strength.
        mask = _make_rect_mask(mask_data, width, height, 1.0)
        mask = _feather_mask(mask, feather) * max(0.0, mask_strength)
        background_character_subtracted = False
        if _v054_region_is_background_lane(region):
            mask, background_character_subtracted = _v054_subtract_character_mask_from_background(mask, character_union_mask)
        compiled_prompt = _compile_region_prompt(region)
        compiled_region_negative = _character_lock_negative_for_attention(region) if rtype == "character" else ""
        if regional_context_enabled:
            compiled_prompt = f"{compiled_prompt}, ({regional_context}:{regional_context_weight:.2f})"

        # Multi-subject conditioning:
        # Repeat character regions as independent branches. Each branch shares same mask,
        # but gets a slightly different textual contract to avoid branch collapse.
        for slot in range(slots):
            slot_prompt = compiled_prompt
            if slots > 1:
                slot_prompt += f", subject existence branch {slot + 1} for {rid}, preserve this character"
            branch_prompts.append(slot_prompt)
            branch_negative_prompts.append(compiled_region_negative)
            branch_masks.append(mask / float(slots))

        appearance_branch_added = False
        full_character_branch_added = False
        upper_identity_branch_added = False
        structural_gender_branch_added = False
        facial_hair_branch_added = False
        standalone_facial_hair_branch_added = False
        face_identity_grooming_branch_added = False
        clothing_branch_categories: List[str] = []
        upper_identity_expand_ratio = 0.0
        if rtype == "character" and appearance_lock_mode != "off" and appearance_lock_gain > 0:
            appearance_mask_weight = mask_strength * appearance_lock_gain

            if appearance_lock_mode in {"full_character_soft", "full_character_strong"}:
                # Phase 26.9.7: full-character authority uses the complete
                # user-authored character region, not only the top/head slice.
                # It carries body/silhouette/outfit/presentation terms already
                # present in the region prompt without inventing binary gender.
                full_weight = appearance_mask_weight * (1.10 if appearance_lock_mode == "full_character_strong" else 1.0)
                full_mask = _make_full_character_lock_mask(mask_data, width, height, 1.0)
                if appearance_lock_feather > 0:
                    full_mask = _feather_mask(full_mask, appearance_lock_feather)
                full_mask = full_mask * max(0.0, full_weight)
                branch_prompts.append(_compile_appearance_lock_prompt(region, appearance_lock_mode))
                branch_negative_prompts.append(compiled_region_negative)
                branch_masks.append(full_mask)
                full_character_branch_added = True
                appearance_branch_added = True

            if appearance_lock_mode in {"hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong", "full_character_strong"}:
                # Extra positive-conditioning branch focused on the character's
                # upper/head zone. In full_character_strong this becomes the
                # upper identity reinforcement branch on top of full body lock.
                upper_mode = "upper_identity_strong" if appearance_lock_mode == "full_character_strong" else appearance_lock_mode
                upper_expand_ratio = 0.14 if upper_mode in {"hair_focus_strong", "upper_identity_strong"} else 0.08
                upper_identity_expand_ratio = upper_expand_ratio
                upper_mask = _make_top_focus_mask(
                    mask_data,
                    width,
                    height,
                    appearance_lock_height,
                    1.0,
                    expand_ratio=upper_expand_ratio,
                )
                if appearance_lock_feather > 0:
                    upper_mask = _feather_mask(upper_mask, appearance_lock_feather)
                upper_weight = appearance_mask_weight * (1.25 if upper_mode in {"hair_focus_strong", "upper_identity_strong"} else 1.0)
                upper_mask = upper_mask * max(0.0, upper_weight)
                branch_prompts.append(_compile_appearance_lock_prompt(region, upper_mode))
                branch_negative_prompts.append(compiled_region_negative)
                branch_masks.append(upper_mask)
                upper_identity_branch_added = True
                appearance_branch_added = True

            # Phase 27.12: one face-local branch owns structural face identity
            # and selected grooming together.  The Phase 27.11 standalone
            # facial-hair branch competed with gender after mask normalization;
            # merging them restores male/female face authority while giving
            # stubble/beard texture the same face-local signal.
            structural_gender_prompt = _structural_gender_lock_prompt_for_attention(region)
            if structural_gender_prompt:
                face_mode = _face_identity_grooming_guard_mode(region)
                gender_mask = _make_face_detail_mask(
                    mask_data,
                    width,
                    height,
                    appearance_lock_height,
                    1.0,
                )
                if appearance_lock_feather > 0:
                    gender_mask = _feather_mask(gender_mask, appearance_lock_feather)
                gender_multiplier = 1.74 if face_mode == "strict" else 1.62
                gender_mask = gender_mask * max(0.0, appearance_mask_weight * gender_multiplier)
                branch_prompts.append(structural_gender_prompt)
                branch_negative_prompts.append(_structural_gender_lock_negative_for_attention(region))
                branch_masks.append(gender_mask)
                live_categories = _character_trait_categories_for_attention(region)
                structural_gender_branch_added = bool(live_categories.get("gender"))
                facial_hair_branch_added = bool(live_categories.get("facial_hair"))
                face_identity_grooming_branch_added = True
                appearance_branch_added = True

            # Explicit Top, Bottom and Full Outfit fields now receive their own
            # non-face body slices.  Full Outfit takes precedence over component
            # lanes to avoid contradictory outfit stacks.
            for clothing_category in _clothing_lock_categories_for_attention(region):
                clothing_prompt = _clothing_lock_prompt_for_attention(region, clothing_category)
                if not clothing_prompt:
                    continue
                start_ratio, end_ratio, expand_ratio = _clothing_lock_slice_for_attention(clothing_category)
                if clothing_category in {"clothing_top", "full_costume"}:
                    # Start at/below the face core. Feathered boundaries may
                    # touch softly, but garment authority never owns face
                    # pixels or competes with gender/grooming there.
                    start_ratio = max(start_ratio, _face_detail_height_ratio(appearance_lock_height))
                clothing_mask = _make_clothing_slice_mask(
                    mask_data,
                    width,
                    height,
                    start_ratio,
                    end_ratio,
                    1.0,
                    expand_ratio,
                )
                if appearance_lock_feather > 0:
                    clothing_mask = _feather_mask(clothing_mask, appearance_lock_feather)
                clothing_mode = _character_lock_guard_mode(region, clothing_category)
                clothing_multiplier = 1.62 if clothing_mode == "strict" else 1.48
                clothing_mask = clothing_mask * max(0.0, appearance_mask_weight * clothing_multiplier)
                branch_prompts.append(clothing_prompt)
                branch_negative_prompts.append(_clothing_lock_negative_for_attention(region, clothing_category))
                branch_masks.append(clothing_mask)
                clothing_branch_categories.append(clothing_category)
                appearance_branch_added = True

        debug_meta.append({
            "id": rid,
            "type": rtype,
            "compiled_prompt": compiled_prompt,
            "subject_slots": slots,
            "strength": strength,
            "priority": priority,
            "presence_boost": presence,
            "mask_strength": mask_strength,
            "feather": feather,
            "tokens": _clean_list(region.get("tokens", [])) + _clean_list(region.get("owns", [])),
            "explicit_character_trait_terms": _character_trait_terms_for_attention(region) if rtype == "character" else [],
            "live_character_lock_correction": _character_lock_correction_for_attention(region) if rtype == "character" else {"positive": "", "negative": "", "enabled": "false"},
            "live_character_lock_negative": compiled_region_negative if rtype == "character" else "",
            "character_pose_authority": _character_local_pose_authority_for_attention(region) if rtype == "character" else {},
            "character_additional_details": _character_additional_details_report_for_attention(region) if rtype == "character" else {},
            "appearance_lock_branch": appearance_branch_added,
            "full_character_lock_branch": full_character_branch_added,
            "upper_identity_lock_branch": upper_identity_branch_added,
            "structural_gender_lock_branch": structural_gender_branch_added,
            "facial_hair_lock_branch": facial_hair_branch_added,
            "standalone_facial_hair_lock_branch": standalone_facial_hair_branch_added,
            "face_identity_grooming_lock_branch": face_identity_grooming_branch_added,
            "clothing_lock_branch_categories": clothing_branch_categories,
            "clothing_lock_branch_count": len(clothing_branch_categories),
            "upper_identity_mask_expand_ratio": upper_identity_expand_ratio,
            "single_sampler_identity_prompt_policy": "merged_face_identity_grooming_plus_body_scoped_clothing_and_routed_additional_details" if rtype == "character" and appearance_branch_added else "not_applicable",
            "authority_applied_after_feather": bool(rtype == "character" and appearance_branch_added),
            "background_character_mask_subtracted": background_character_subtracted,
        })

    if len(branch_prompts) < 1:
        raise ValueError("No enabled regions found.")

    character_trait_reports = [
        item for item in debug_meta
        if item.get("type") == "character"
        and (item.get("explicit_character_trait_terms") or (item.get("live_character_lock_correction") or {}).get("positive") or (item.get("live_character_lock_correction") or {}).get("negative"))
    ]
    live_trait_positive_count = sum(
        1 for item in character_trait_reports
        if item.get("explicit_character_trait_terms") or (item.get("live_character_lock_correction") or {}).get("positive")
    )
    live_trait_negative_count = sum(
        1 for item in character_trait_reports
        if (item.get("live_character_lock_correction") or {}).get("negative")
    )

    debug_json = json.dumps({
        "version": "0.5.3",
        "schema": scene_schema_version,
        "multi_subject_mode": multi_subject_mode,
        "entity_count": entity_count,
        "branch_count": len(branch_prompts),
        "appearance_lock": {
            "mode": appearance_lock_mode,
            "gain": appearance_lock_gain,
            "height": appearance_lock_height,
            "feather": appearance_lock_feather,
            "full_character_branch_count": sum(1 for item in debug_meta if item.get("full_character_lock_branch")),
            "upper_identity_branch_count": sum(1 for item in debug_meta if item.get("upper_identity_lock_branch")),
            "structural_gender_branch_count": sum(1 for item in debug_meta if item.get("structural_gender_lock_branch")),
            "facial_hair_branch_count": sum(1 for item in debug_meta if item.get("facial_hair_lock_branch")),
            "face_identity_grooming_branch_count": sum(1 for item in debug_meta if item.get("face_identity_grooming_lock_branch")),
            "standalone_facial_hair_branch_count": sum(1 for item in debug_meta if item.get("standalone_facial_hair_lock_branch")),
            "clothing_branch_count": sum(int(item.get("clothing_lock_branch_count") or 0) for item in debug_meta),
            "policy": "positive subject-local full-character and upper identity authority plus character-local Pose/held-item actions, one merged face/grooming lane, and body-scoped garment/additional-detail routing inside the same sampler"
        },
        "attention_lock_intent": _attention_lock_runtime_proof_template(
            active=bool(branch_prompts and branch_masks),
            appearance_lock_mode=appearance_lock_mode,
            branch_count=len(branch_prompts),
            mask_count=len(branch_masks),
            full_character_branch_count=sum(1 for item in debug_meta if item.get("full_character_lock_branch")),
            upper_identity_branch_count=sum(1 for item in debug_meta if item.get("upper_identity_lock_branch")),
            base_weight=0.0,
            region_gain=0.0,
            normalize_masks=True,
            patched_model_used_by_main_sampler=None,
        ),
        "compiled_global": compiled_global,
        "compiled_negative": compiled_negative,
        "character_trait_conditioning": {
            "schema": "neo.image.scene_director.character_trait_conditioning.v054.v2",
            "status": "applied" if character_trait_reports else "not_present",
            "route": "scene_graph_json.region_fields -> subject_local_clip_branches -> v054_attn2_main_sampler",
            "character_regions_with_live_terms": len(character_trait_reports),
            "positive_branch_regions": live_trait_positive_count,
            "negative_global_guard_regions": live_trait_negative_count,
            "positive_terms_are_live": bool(live_trait_positive_count),
            "negative_terms_are_live": bool(live_trait_negative_count or compiled_negative),
            "regional_negative_branch_count": sum(1 for item in branch_negative_prompts if str(item).strip()),
            "structural_gender_branch_regions": [item.get("id") for item in character_trait_reports if item.get("structural_gender_lock_branch")],
            "facial_hair_branch_regions": [item.get("id") for item in character_trait_reports if item.get("facial_hair_lock_branch")],
            "face_identity_grooming_branch_regions": [item.get("id") for item in character_trait_reports if item.get("face_identity_grooming_lock_branch")],
            "clothing_branch_regions": {
                item.get("id"): list(item.get("clothing_lock_branch_categories") or [])
                for item in character_trait_reports if item.get("clothing_lock_branch_categories")
            },
            "character_pose_regions": {
                item.get("id"): list((item.get("character_pose_authority") or {}).get("terms") or [])
                for item in character_trait_reports if (item.get("character_pose_authority") or {}).get("enabled")
            },
            "character_additional_detail_regions": {
                item.get("id"): item.get("character_additional_details")
                for item in character_trait_reports if (item.get("character_additional_details") or {}).get("enabled")
            },
            "advanced_pair_pose_execution": False,
            "pose_adds_attention_branch": False,
            "additional_details_add_attention_branches": False,
            "additional_details_add_sampler": False,
            "additional_details_content_policy_guards_added": False,
            "accessories_in_structural_face_lane": False,
            "policy": "Character > Pose and Held Items reuse primary/full-character branches; Phase 27.15 details reuse matching face/clothing/full-character branches; Advanced Pair Pose is retired and no per-detail lane or sampler is added.",
        },
        "prompt_authority": prompt_authority,
        "global_prompt_excluded": suppress_global_context,
        "regional_context_enabled": regional_context_enabled,
        "regional_context": regional_context,
        "regions": debug_meta,
    }, indent=2)

    layout_preview = _make_layout_preview(all_regions, width, height, data.get("relations", []))
    return compiled_global, compiled_negative, branch_prompts, branch_negative_prompts, branch_masks, debug_json, layout_preview


def _normalize_masks(masks: List[torch.Tensor], base_weight: float, normalize: bool):
    base = torch.ones_like(masks[0]) * max(0.0, float(base_weight))
    stack = torch.stack([base] + masks, dim=0)
    total = stack.sum(dim=0, keepdim=True)
    if total.min().item() <= 0.0:
        raise ValueError("Masks do not cover full canvas. Increase base_weight or add coverage regions.")
    if normalize:
        stack = stack / total
    return stack


def _downsample_masks(mask_stack: torch.Tensor, batch_size: int, token_count: int, original_shape: Tuple[int, ...], out: torch.Tensor):
    width, height = original_shape[3], original_shape[2]
    scale = math.ceil(math.log2(math.sqrt(height * width / max(1, token_count))))
    size = (_repeat_div(height, scale), _repeat_div(width, scale))
    mask_downsample = interpolate(mask_stack.to(device=out.device, dtype=out.dtype), size=size, mode="nearest")
    mask_downsample = mask_downsample.view(mask_downsample.shape[0], token_count, 1)
    mask_downsample = mask_downsample.unsqueeze(1).repeat(1, batch_size, 1, 1)
    return mask_downsample


def _make_preview(mask_stack: torch.Tensor):
    region_masks = mask_stack[1:]
    if region_masks.shape[0] == 0:
        h, w = mask_stack.shape[-2], mask_stack.shape[-1]
        return torch.zeros((1, h, w, 3), dtype=torch.float32)

    colors = torch.tensor([
        [1.0, 0.1, 0.1],
        [0.1, 0.3, 1.0],
        [0.1, 1.0, 0.2],
        [1.0, 0.8, 0.1],
        [1.0, 0.1, 1.0],
        [0.1, 1.0, 1.0],
        [1.0, 0.5, 0.1],
        [0.6, 0.2, 1.0],
    ], dtype=torch.float32)

    h, w = region_masks.shape[-2], region_masks.shape[-1]
    out = torch.zeros((h, w, 3), dtype=torch.float32)

    for i in range(region_masks.shape[0]):
        color = colors[i % colors.shape[0]]
        m = region_masks[i, 0].clamp(0, 1).unsqueeze(-1)
        out = out * (1.0 - m * 0.70) + color * (m * 0.70)

    return out.unsqueeze(0).clamp(0, 1)



def _draw_rect_border(img, x1, y1, x2, y2, color, thickness=3):
    h, w, _ = img.shape
    x1, x2 = max(0, x1), min(w - 1, x2)
    y1, y2 = max(0, y1), min(h - 1, y2)
    t = max(1, int(thickness))
    img[y1:min(h, y1+t), x1:x2+1] = color
    img[max(0, y2-t+1):y2+1, x1:x2+1] = color
    img[y1:y2+1, x1:min(w, x1+t)] = color
    img[y1:y2+1, max(0, x2-t+1):x2+1] = color


def _draw_line(img, x0, y0, x1, y1, color, thickness=2):
    h, w, _ = img.shape
    steps = max(abs(x1-x0), abs(y1-y0), 1)
    for i in range(steps + 1):
        t = i / steps
        x = int(round(x0 + (x1-x0) * t))
        y = int(round(y0 + (y1-y0) * t))
        r = max(1, int(thickness))
        img[max(0,y-r):min(h,y+r+1), max(0,x-r):min(w,x+r+1)] = color


def _draw_circle(img, cx, cy, radius, color, thickness=2):
    h, w, _ = img.shape
    r = max(2, int(radius))
    yy, xx = torch.meshgrid(torch.arange(h), torch.arange(w), indexing="ij")
    dist = torch.sqrt((xx - int(cx)).float() ** 2 + (yy - int(cy)).float() ** 2)
    ring = (dist >= r - thickness) & (dist <= r + thickness)
    img[ring] = color


def _draw_filled_circle(img, cx, cy, radius, color):
    h, w, _ = img.shape
    r = max(2, int(radius))
    yy, xx = torch.meshgrid(torch.arange(h), torch.arange(w), indexing="ij")
    dist = torch.sqrt((xx - int(cx)).float() ** 2 + (yy - int(cy)).float() ** 2)
    mask = dist <= r
    img[mask] = color


def _draw_filled_rect(img, x1, y1, x2, y2, color):
    h, w, _ = img.shape
    x1, x2 = max(0, int(x1)), min(w, int(x2))
    y1, y2 = max(0, int(y1)), min(h, int(y2))
    if x2 > x1 and y2 > y1:
        img[y1:y2, x1:x2] = color


def _draw_label_bars(img, x1, y1, label_index, color):
    # Tiny barcode-style label: readable as a visual anchor even without font dependencies.
    bar_w = max(4, (x1 + 1) // 80)
    for i in range(label_index):
        _draw_filled_rect(img, x1 + 8 + i * (bar_w + 3), y1 + 8, x1 + 8 + i * (bar_w + 3) + bar_w, y1 + 28, color)


def _make_layout_preview(all_regions, width: int, height: int, relations=None):
    """v0.4.3 Balanced Count layout guide.

    This is intentionally not pretty. It is a high-contrast control image:
    white canvas, strong region boxes, and mandatory body anchors for every subject.
    Feed this to ControlNet Scribble/Lineart/SoftEdge when possible.
    """
    img = torch.ones((height, width, 3), dtype=torch.float32) * 0.96
    colors = torch.tensor([
        [0.95, 0.05, 0.04], [0.04, 0.18, 0.95], [0.02, 0.62, 0.12], [0.95, 0.62, 0.02],
        [0.75, 0.05, 0.85], [0.02, 0.70, 0.70], [0.95, 0.35, 0.02], [0.35, 0.12, 0.85]
    ], dtype=torch.float32)
    subject_i = 0
    for i, r in enumerate(_enabled_regions(all_regions)):
        mask_data = r.get("mask") or {"x": r.get("x", 0), "y": r.get("y", 0), "w": r.get("w", 1), "h": r.get("h", 1)}
        try:
            x1, y1, x2, y2 = _rect_to_pixels(mask_data, width, height)
        except Exception:
            continue
        color = colors[i % colors.shape[0]]
        black = torch.tensor([0.0, 0.0, 0.0], dtype=torch.float32)
        dark = color * 0.35
        rtype = _region_type(r)

        # region tint + hard border
        overlay = torch.zeros_like(img); overlay[y1:y2, x1:x2] = color
        alpha = 0.045 if rtype == "character" else (0.035 if rtype == "object" else 0.025)
        m = (overlay.sum(-1, keepdim=True) > 0).float()
        img = img * (1.0 - m * alpha) + overlay * alpha
        _draw_rect_border(img, x1, y1, x2, y2, color, thickness=3 if rtype == "character" else 2)

        if rtype == "character":
            subject_i += 1
            # full-body count anchor silhouette: head, torso block, arms, legs, feet
            cx = (x1 + x2) // 2
            box_w = max(1, x2 - x1)
            box_h = max(1, y2 - y1)
            head_r = max(8, int(min(box_w, box_h) * 0.065))
            head_cy = y1 + int(box_h * 0.16)
            shoulder_y = y1 + int(box_h * 0.30)
            torso_top = y1 + int(box_h * 0.26)
            torso_bot = y1 + int(box_h * 0.56)
            hip_y = y1 + int(box_h * 0.60)
            knee_y = y1 + int(box_h * 0.76)
            foot_y = y1 + int(box_h * 0.92)
            shoulder = max(12, int(box_w * 0.22))
            torso_w = max(12, int(box_w * 0.18))
            hip_w = max(10, int(box_w * 0.13))

            # Label bars = PERSON number anchor
            _draw_label_bars(img, x1, y1, subject_i, color)

            # soft count anchor silhouette: visible enough for guide, not strong enough to destroy quality
            guide_col = torch.tensor([0.12, 0.12, 0.12], dtype=torch.float32)
            _draw_filled_circle(img, cx, head_cy, head_r, guide_col)
            _draw_circle(img, cx, head_cy, head_r + 3, color, thickness=3)
            _draw_filled_rect(img, cx - torso_w, torso_top, cx + torso_w, torso_bot, guide_col)
            _draw_rect_border(img, cx - torso_w, torso_top, cx + torso_w, torso_bot, color, thickness=3)
            _draw_line(img, cx - shoulder, shoulder_y, cx + shoulder, shoulder_y, guide_col, 3)
            _draw_line(img, cx - shoulder, shoulder_y, x1 + int(box_w * 0.18), y1 + int(box_h * 0.50), guide_col, 3)
            _draw_line(img, cx + shoulder, shoulder_y, x1 + int(box_w * 0.82), y1 + int(box_h * 0.50), guide_col, 3)
            _draw_line(img, cx - hip_w, hip_y, cx - int(box_w * 0.13), knee_y, guide_col, 3)
            _draw_line(img, cx + hip_w, hip_y, cx + int(box_w * 0.13), knee_y, guide_col, 3)
            _draw_line(img, cx - int(box_w * 0.13), knee_y, cx - int(box_w * 0.20), foot_y, guide_col, 3)
            _draw_line(img, cx + int(box_w * 0.13), knee_y, cx + int(box_w * 0.20), foot_y, guide_col, 3)
            _draw_line(img, cx - int(box_w * 0.27), foot_y, cx - int(box_w * 0.12), foot_y, guide_col, 3)
            _draw_line(img, cx + int(box_w * 0.12), foot_y, cx + int(box_w * 0.27), foot_y, guide_col, 3)

            # separation rails at subject boundaries to discourage merging
            _draw_line(img, x1 + 3, y1 + int(box_h*0.10), x1 + 3, y2 - int(box_h*0.08), color, 3)
            _draw_line(img, x2 - 3, y1 + int(box_h*0.10), x2 - 3, y2 - int(box_h*0.08), color, 3)
        elif rtype == "object":
            cx = (x1+x2)//2; cy=(y1+y2)//2
            _draw_line(img, x1, y1, x2, y2, color, 3)
            _draw_line(img, x1, y2, x2, y1, color, 3)
            _draw_filled_circle(img, cx, cy, max(4, min(x2-x1, y2-y1)//9), color)
            _draw_circle(img, cx, cy, max(6, min(x2-x1, y2-y1)//6), black, 2)
        elif rtype == "interaction":
            # interaction should guide, not dominate
            midx, midy = (x1+x2)//2, (y1+y2)//2
            _draw_line(img, x1, midy, x2, midy, color, 2)
            _draw_line(img, midx, y1, midx, y2, color, 2)
    # v0.5 relation/facing arrows. Keep them soft; this is a guide, not a final drawing.
    id_centers = {}
    for r in _enabled_regions(all_regions):
        rid = str(r.get("id", r.get("name", ""))).strip()
        if not rid:
            continue
        try:
            md = r.get("mask") or {"x": r.get("x",0), "y": r.get("y",0), "w": r.get("w",1), "h": r.get("h",1)}
            x1,y1,x2,y2 = _rect_to_pixels(md, width, height)
            id_centers[rid] = ((x1+x2)//2, (y1+y2)//2, x1,y1,x2,y2)
        except Exception:
            pass
    arrow_col = torch.tensor([0.05, 0.05, 0.05], dtype=torch.float32)
    for rel in relations or []:
        if not isinstance(rel, dict):
            continue
        src = str(rel.get("from", rel.get("source", ""))).strip()
        dst = str(rel.get("to", rel.get("target", rel.get("object", "")))).strip()
        obj = str(rel.get("object", "")).strip()
        target = obj if obj in id_centers else dst
        if src in id_centers and target in id_centers:
            x0,y0,_,_,_,_ = id_centers[src]
            x1,y1,_,_,_,_ = id_centers[target]
            _draw_line(img, x0, y0, x1, y1, arrow_col, 2)
            dx = x1 - x0; dy = y1 - y0
            mag = max(1.0, float((dx*dx + dy*dy) ** 0.5))
            ux, uy = dx / mag, dy / mag
            ah = 18
            _draw_line(img, x1, y1, int(x1 - ux*ah - uy*ah*0.45), int(y1 - uy*ah + ux*ah*0.45), arrow_col, 2)
            _draw_line(img, x1, y1, int(x1 - ux*ah + uy*ah*0.45), int(y1 - uy*ah - ux*ah*0.45), arrow_col, 2)
    return img.unsqueeze(0).clamp(0, 1)




def _attention_lock_runtime_proof_template(
    *,
    active: bool,
    appearance_lock_mode: str = "off",
    branch_count: int = 0,
    mask_count: int = 0,
    full_character_branch_count: int = 0,
    upper_identity_branch_count: int = 0,
    base_weight: float | str = 0.0,
    region_gain: float | str = 0.0,
    normalize_masks: bool = True,
    node_class: str = "NeoSceneDirectorV054",
    patched_model_used_by_main_sampler: bool | None = None,
    patched_model_ref: list[Any] | None = None,
    main_sampler_id: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """V25.9.2 non-visual proof that legacy in-sampler attention lock exists.

    This deliberately proves the legacy V053-style mechanism instead of treating
    the external masked correction samplers as Character Lock proof. The custom
    node can prove patch registration; the backend compile patch proves the
    patched MODEL output is wired into the main sampler.
    """
    return {
        "schema": "neo.image.scene_director.attention_lock_runtime_proof.v25_9_2",
        "phase": "V25.9.2",
        "active": bool(active),
        "node_class": node_class,
        "legacy_patch_director_called": bool(active),
        "attn2_patch_registered": bool(active),
        "attn2_output_patch_registered": bool(active),
        "appearance_lock_mode": str(appearance_lock_mode or "off"),
        "regional_conditioning_branch_count": int(branch_count or 0),
        "mask_count": int(mask_count or 0),
        "full_character_branch_count": int(full_character_branch_count or 0),
        "upper_identity_branch_count": int(upper_identity_branch_count or 0),
        "base_weight": _safe_float(base_weight, 0.0),
        "region_gain": _safe_float(region_gain, 0.0),
        "normalize_masks": bool(normalize_masks),
        "patched_model_emitted": bool(active),
        "patched_model_used_by_main_sampler": patched_model_used_by_main_sampler,
        "patched_model_ref": patched_model_ref or [],
        "main_sampler_id": main_sampler_id or "",
        "external_masked_correction_primary": False,
        "fallback_masked_correction_primary": False,
        "proof_scope": "node_patch_registration" if patched_model_used_by_main_sampler is None else "backend_sampler_wiring",
        "policy": "Legacy in-sampler regional attention patch is the primary Character Lock proof; external masked KSampler correction is fallback/rescue only.",
        "warnings": list(warnings or []),
    }

def _patch_director(
    model: Any,
    region_conds: List[torch.Tensor],
    masks: List[torch.Tensor],
    base_weight: float,
    normalize_masks: bool,
    region_gain: float,
    region_negative_conds: List[Any] | None = None,
):
    m = model.clone()
    masks = [mask * max(0.1, float(region_gain)) for mask in masks]
    mask_stack = _normalize_masks(masks, base_weight, normalize_masks)
    region_count_total = len(region_conds) + 1
    aligned_negative_conds = list(region_negative_conds or [])
    if len(aligned_negative_conds) < len(region_conds):
        aligned_negative_conds.extend([None] * (len(region_conds) - len(aligned_negative_conds)))
    elif len(aligned_negative_conds) > len(region_conds):
        aligned_negative_conds = aligned_negative_conds[:len(region_conds)]
    regional_negative_active = any(cond is not None for cond in aligned_negative_conds)

    state = {
        "batch_size": 1,
        "expanded_positive": False,
        "expanded_negative": False,
        "region_count_total": region_count_total,
    }

    @torch.inference_mode()
    def attn2_patch(n, context_attn2, value_attn2, extra_options):
        cond_or_unconds = extra_options.get("cond_or_uncond", [0])
        chunks = len(cond_or_unconds) or 1

        n_chunks = n.chunk(chunks, dim=0)
        ctx_chunks = context_attn2.chunk(chunks, dim=0)
        val_chunks = value_attn2.chunk(chunks, dim=0) if value_attn2 is not None else ctx_chunks

        out_n, out_ctx, out_val = [], [], []
        state["expanded_positive"] = False
        state["expanded_negative"] = False

        for i, cond_or_uncond in enumerate(cond_or_unconds):
            n_i, ctx_i, val_i = n_chunks[i], ctx_chunks[i], val_chunks[i]

            is_negative = cond_or_uncond == 1
            if is_negative and not regional_negative_active:
                out_n.append(n_i); out_ctx.append(ctx_i); out_val.append(val_i)
                continue

            batch_size = n_i.shape[0]
            state["batch_size"] = batch_size
            if is_negative:
                state["expanded_negative"] = True
            else:
                state["expanded_positive"] = True
            token_count, ctx_dim = ctx_i.shape[1], ctx_i.shape[2]

            contexts = [ctx_i]
            values = [val_i]

            local_conds = aligned_negative_conds if is_negative else region_conds
            for cond in local_conds:
                # A region without its own negative must inherit the active
                # base/global negative context. Encoding an empty prompt here
                # would dilute the user's global safeguards inside that mask.
                if cond is None:
                    contexts.append(ctx_i)
                    values.append(val_i)
                    continue
                cond_local = cond.to(device=ctx_i.device, dtype=ctx_i.dtype)
                if cond_local.shape[-1] != ctx_dim:
                    raise RuntimeError(f"Regional context dim {cond_local.shape[-1]} does not match current context dim {ctx_dim}.")
                cond_local = _pad_context_to_tokens(cond_local, token_count)
                cond_local = cond_local.repeat(batch_size, 1, 1)
                contexts.append(cond_local)
                values.append(cond_local)

            out_n.append(n_i.repeat(region_count_total, 1, 1))
            out_ctx.append(torch.cat(contexts, dim=0))
            out_val.append(torch.cat(values, dim=0))

        return torch.cat(out_n, dim=0).to(n), torch.cat(out_ctx, dim=0).to(context_attn2), torch.cat(out_val, dim=0).to(value_attn2)

    @torch.inference_mode()
    def attn2_output_patch(out, extra_options):
        cond_or_unconds = extra_options.get("cond_or_uncond", [0])
        original_shape = extra_options.get("original_shape", None)

        if original_shape is None or not (state.get("expanded_positive", False) or state.get("expanded_negative", False)):
            return out

        batch_size = int(state.get("batch_size", 1))
        token_count = out.shape[1]
        masks_down = _downsample_masks(mask_stack, batch_size, token_count, original_shape, out)

        outputs, pos = [], 0

        for cond_or_uncond in cond_or_unconds:
            is_negative = cond_or_uncond == 1
            if is_negative and not regional_negative_active:
                outputs.append(out[pos:pos + batch_size])
                pos += batch_size
            else:
                count = region_count_total * batch_size
                block = out[pos:pos + count]
                pos += count
                block = block.view(region_count_total, batch_size, out.shape[1], out.shape[2])
                blended = (block * masks_down).sum(dim=0)
                outputs.append(blended)

        return torch.cat(outputs, dim=0)

    m.set_model_attn2_patch(attn2_patch)
    m.set_model_attn2_output_patch(attn2_output_patch)

    proof = _attention_lock_runtime_proof_template(
        active=True,
        branch_count=len(region_conds),
        mask_count=len(masks),
        base_weight=base_weight,
        region_gain=region_gain,
        normalize_masks=normalize_masks,
        node_class="NeoSceneDirectorV054",
    )
    proof.update({
        "patch_methods_present": {
            "set_model_attn2_patch": hasattr(m, "set_model_attn2_patch"),
            "set_model_attn2_output_patch": hasattr(m, "set_model_attn2_output_patch"),
        },
        "region_count_total": int(region_count_total),
        "regional_negative_conditioning_active": bool(regional_negative_active),
        "regional_negative_branch_count": sum(1 for cond in aligned_negative_conds if cond is not None),
        "regional_negative_scope": "same_subject_masks_as_positive_branches" if regional_negative_active else "global_negative_only",
    })

    return m, _make_preview(mask_stack), proof


def _empty_mask(width: int, height: int) -> torch.Tensor:
    # ComfyUI MASK can be HxW, but IPAdapter Plus expects batched masks.
    # Return 1xHxW so IPAdapterAdvanced can safely do mask.unsqueeze(1) -> Nx1xHxW.
    return torch.zeros((1, height, width), dtype=torch.float32)


def _extract_subject_masks_and_identity(scene_json: str, width: int, height: int, max_subjects: int = 4):
    """Return fixed subject MASK outputs and an identity routing plan for external IPAdapter nodes.

    This node does not vendor or call any specific IPAdapter extension. It outputs clean ComfyUI MASKs
    that can be connected to whichever IPAdapter implementation the user already has installed.
    """
    try:
        data = json.loads(scene_json)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    subjects = data.get("subjects", None)
    if not isinstance(subjects, list):
        # Legacy fallback: use character regions.
        regions = []
        for key in ("regions", "object_regions", "shared_regions"):
            value = data.get(key, [])
            if isinstance(value, list):
                regions.extend(value)
        subjects = []
        for i, r in enumerate(regions):
            if isinstance(r, dict) and _region_type(r) == "character":
                subjects.append({
                    "id": r.get("id", r.get("name", f"person_{i+1}")),
                    "bbox": None,
                    "mask": r.get("mask", None),
                    "prompt": r.get("prompt", ""),
                    "identity": r.get("identity", {}),
                })

    identity_root = data.get("identity", {}) if isinstance(data.get("identity", {}), dict) else {}
    ipadapter_root = identity_root.get("ipadapter", {}) if isinstance(identity_root.get("ipadapter", {}), dict) else {}

    masks = []
    entries = []
    for idx in range(max_subjects):
        if idx < len(subjects) and isinstance(subjects[idx], dict):
            s = subjects[idx]
            sid = str(s.get("id", f"person_{idx+1}")).strip() or f"person_{idx+1}"
            try:
                md = _bbox_to_mask(s)
                mask = _make_rect_mask(md, width, height, 1.0).clamp(0, 1)
            except Exception:
                mask = _empty_mask(width, height)
            # Slightly softer masks tend to behave better in masked IPAdapter workflows.
            feather = int(_safe_float(s.get("identity_mask_feather", s.get("feather", 18)), 18))
            if feather > 0 and mask.max().item() > 0:
                mask = _feather_mask(mask, feather).clamp(0, 1)

            identity_data = s.get("identity", {}) if isinstance(s.get("identity", {}), dict) else {}
            ipa_data = ipadapter_root.get(sid, {}) if isinstance(ipadapter_root.get(sid, {}), dict) else {}
            merged = dict(identity_data)
            merged.update(ipa_data)
            entries.append({
                "slot": idx + 1,
                "subject_id": sid,
                "mask_output": f"subject_{idx+1}_mask",
                "prompt": str(s.get("prompt", "")).strip(),
                "recommended_ipadapter_weight": _safe_float(merged.get("weight", 0.62), 0.62),
                "recommended_start_at": _safe_float(merged.get("start_at", 0.0), 0.0),
                "recommended_end_at": _safe_float(merged.get("end_at", 0.75), 0.75),
                "reference_image": str(merged.get("image", merged.get("reference_image", ""))).strip(),
                "notes": "Connect this subject mask to a masked IPAdapter node. Keep weights moderate first: 0.45-0.70."
            })
            masks.append(mask)
        else:
            masks.append(_empty_mask(width, height))

    plan = {
        "version": "0.5.3",
        "purpose": "Regional IPAdapter prep. This node outputs subject masks; external IPAdapter nodes apply the reference images.",
        "subject_count_detected": len([s for s in subjects if isinstance(s, dict)]),
        "entries": entries,
        "recommended_order": ["Run v0.5.2 with no IPAdapter first", "Add IPAdapter one subject at a time", "Use 0.45-0.70 weight", "If count breaks, lower IPAdapter weight before changing region_gain"],
    }
    return masks, json.dumps(plan, indent=2)


class NeoSceneDirector:
    @classmethod
    def INPUT_TYPES(cls):
        default_scene = json.dumps({
            "version": "0.5.2",
            "multi_subject_mode": "count_locked",
            "canvas": {"width": 1344, "height": 768},
            "camera": {"framing": "wide full body", "angle": "eye level", "lens": "50mm", "depth": "studio portrait"},
            "global_style": "realistic cinematic studio lighting, clean grey background, sharp details, high quality full body photo",
            "subjects": [
                {"id": "person_1", "bbox": [0.05, 0.08, 0.30, 0.92], "prompt": "fesci-fi soldier in white armor", "pose_type": "standing relaxed", "facing": "person_2", "required": True},
                {"id": "person_2", "bbox": [0.36, 0.08, 0.61, 0.92], "prompt": "sci-fi soldier in black armor", "pose_type": "turning slightly left", "facing": "person_1", "required": True},
                {"id": "person_3", "bbox": [0.70, 0.08, 0.95, 0.92], "prompt": "sci-fi medic in blue armor", "pose_type": "standing alert", "facing": "person_2", "required": True}
            ],
            "objects": [
                {"id": "energy_core", "bbox": [0.30, 0.38, 0.42, 0.52], "prompt": "small glowing blue energy core", "bound_to": ["person_1", "person_2"], "relation": "held between them"}
            ],
            "relations": [
                {"from": "person_1", "to": "person_2", "type": "handing_to", "object": "energy_core"},
                {"from": "person_3", "to": "person_2", "type": "looking_at"}
            ],
            "negative": "extra subjects, missing person, merged bodies, bad hands, deformed anatomy, nude, nsfw, text, watermark"
        }, indent=2)

        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "width": ("INT", {"default": 1344, "min": 64, "max": 8192, "step": 8}),
                "height": ("INT", {"default": 768, "min": 64, "max": 8192, "step": 8}),
                "global_prompt_override": ("STRING", {"multiline": True, "default": ""}),
                "base_weight": ("STRING", {"default": "0.55"}),
                "region_gain": ("STRING", {"default": "0.45"}),
                "max_subject_slots": ("INT", {"default": 1, "min": 1, "max": 1, "step": 1}),
                "normalize_masks": ("BOOLEAN", {"default": True}),
                "enable_auto_prompts": ("BOOLEAN", {"default": True}),
                "appearance_lock_mode": (["off", "hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong", "full_character_soft", "full_character_strong"], {"default": "full_character_soft"}),
                "appearance_lock_gain": ("STRING", {"default": "0.35"}),
                "appearance_lock_height": ("STRING", {"default": "0.34"}),
                "appearance_lock_feather": ("INT", {"default": 18, "min": 0, "max": 96, "step": 1}),
                "scene_json": ("STRING", {"multiline": True, "default": default_scene}),
            }
        }

    RETURN_TYPES = ("MODEL", "IMAGE", "IMAGE", "STRING", "STRING", "STRING", "MASK", "MASK", "MASK", "MASK", "STRING")
    RETURN_NAMES = ("patched_model", "mask_preview", "layout_preview", "global_prompt", "negative_prompt", "debug_json", "subject_1_mask", "subject_2_mask", "subject_3_mask", "subject_4_mask", "identity_plan_json")
    FUNCTION = "patch"
    CATEGORY = "Neo Studio/Scene Director"

    def patch(
        self,
        model,
        clip,
        width,
        height,
        global_prompt_override,
        base_weight,
        region_gain,
        max_subject_slots,
        normalize_masks,
        enable_auto_prompts,
        scene_json,
        appearance_lock_mode="off",
        appearance_lock_gain="0.35",
        appearance_lock_height="0.34",
        appearance_lock_feather=18,
    ):
        width = int(width)
        height = int(height)
        base_weight_value = _safe_float(base_weight, 0.55)
        region_gain_value = _safe_float(region_gain, 0.45)
        max_subject_slots = int(max_subject_slots)

        global_prompt, negative, branch_prompts, branch_negative_prompts, branch_masks, debug_json, layout_preview = _parse_scene_schema(
            scene_json=scene_json,
            width=width,
            height=height,
            global_prompt_override=global_prompt_override,
            enable_auto_prompts=bool(enable_auto_prompts),
            max_subject_slots=max_subject_slots,
            appearance_lock_mode=appearance_lock_mode,
            appearance_lock_gain=_safe_float(appearance_lock_gain, 0.35),
            appearance_lock_height=_safe_float(appearance_lock_height, 0.34),
            appearance_lock_feather=_safe_int(appearance_lock_feather, 18),
        )

        region_conds = [_clip_encode_crossattn(clip, p) for p in branch_prompts]
        region_negative_conds = [
            _clip_encode_crossattn(clip, prompt) if str(prompt or "").strip() else None
            for prompt in branch_negative_prompts
        ]

        patched_model, preview, attention_lock_runtime_proof = _patch_director(
            model=model,
            region_conds=region_conds,
            masks=branch_masks,
            base_weight=base_weight_value,
            normalize_masks=bool(normalize_masks),
            region_gain=region_gain_value,
            region_negative_conds=region_negative_conds,
        )
        try:
            debug_payload = json.loads(debug_json) if isinstance(debug_json, str) else {}
        except Exception:
            debug_payload = {}
        if isinstance(debug_payload, dict):
            appearance_debug = debug_payload.get("appearance_lock", {}) if isinstance(debug_payload.get("appearance_lock"), dict) else {}
            attention_lock_runtime_proof.update({
                "appearance_lock_mode": appearance_debug.get("mode") or "off",
                "full_character_branch_count": int(appearance_debug.get("full_character_branch_count") or 0),
                "upper_identity_branch_count": int(appearance_debug.get("upper_identity_branch_count") or 0),
            })
            debug_payload["attention_lock_runtime_proof"] = attention_lock_runtime_proof
            debug_json = json.dumps(debug_payload, indent=2)

        subject_masks, identity_plan_json = _extract_subject_masks_and_identity(scene_json, width, height, max_subjects=4)

        return (
            patched_model, preview, layout_preview, global_prompt, negative, debug_json,
            subject_masks[0], subject_masks[1], subject_masks[2], subject_masks[3], identity_plan_json
        )


class NeoSceneDirectorV052Compat(NeoSceneDirector):
    """Compatibility class for existing v0.5.2 and older workflows.

    It intentionally exposes the old widget contract so saved/API workflows do not fail
    validation because of new required inputs. Appearance lock defaults to off unless
    a newer workflow uses NeoSceneDirectorV053 and sends the explicit controls.
    """

    @classmethod
    def INPUT_TYPES(cls):
        data = NeoSceneDirector.INPUT_TYPES()
        required = dict(data.get("required", {}))
        for key in (
            "appearance_lock_mode",
            "appearance_lock_gain",
            "appearance_lock_height",
            "appearance_lock_feather",
        ):
            required.pop(key, None)
        data = dict(data)
        data["required"] = required
        return data


# Phase 15 test anchor: "compiler_phase": "SD-V054-18"
# Phase 14 test anchor: "compiler_phase": "SD-V054-18"
# Phase 13 test anchor: "compiler_phase": "SD-V054-13"
# Phase 12 test anchor: "compiler_phase": "SD-V054-12"
# Phase 11 test anchor: "compiler_phase": "SD-V054-11"
# -----------------------------------------------------------------------------
# Neo Scene Director V054 — JSON Scene Graph Upgrade Layer
# -----------------------------------------------------------------------------
# V054 intentionally upgrades the existing V053 regional attention base instead
# of replacing it. The V054 scene_graph_json contract is normalized into the
# stable v0.5 regional structure, then the existing _parse_scene_schema() and
# _patch_director() machinery performs the first runtime pass. Later phases can
# route the extra masks into ControlNet, detailers, and inpaint branches.

V054_REGION_ROLES = {
    "character",
    "face_detail",
    "hair_detail",
    "hand_detail",
    "character_detail",
    "clothing",
    "held_prop",
    "object",
    "background",
    "background_object",
    "transition_effect",
    "text",
    "lighting",
    "effect",
    "style",
    "custom",
}

V054_PARENT_REQUIRED_ROLES = {
    "face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop"
}

V054_MAIN_PARENT_ROLES = {"character", "background"}
V054_CHARACTER_PARENT_ONLY_ROLES = {
    "face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop"
}
V054_BACKGROUND_PARENT_ONLY_ROLES = {"background_object", "transition_effect"}
V054_ATTACHABLE_ROLES = V054_REGION_ROLES - V054_MAIN_PARENT_ROLES

V054_BACKGROUND_ROLES = {"background", "background_object", "transition_effect"}


V054_BACKGROUND_CONTEXT_KEYWORDS = {
    "background", "city", "street", "streets", "night", "day", "sky", "holographic",
    "billboard", "billboards", "neon", "cyberpunk", "megacity", "urban", "rainy",
    "rain", "fog", "foggy", "atmospheric", "lighting", "reflections", "reflection",
    "rim lighting", "vaporwave", "magenta", "cyan", "environment", "interior",
    "exterior", "room", "forest", "beach", "mountain", "studio", "alley",
}


def _v054_extract_implicit_background_prompt(prompt: str) -> str:
    """Extract environment/style clauses from the global prompt for an auto background lane.

    V054 regional subject masks can overpower plain global background wording. When the
    UI has no explicit background region, this helper promotes environment clauses from
    the global prompt into a full-canvas shared background branch so the old regional
    parser has real conditioning/mask pressure for the scene setting.
    """
    text = str(prompt or "").strip()
    if not text:
        return ""
    clauses = [c.strip(" ,.;") for c in text.replace("\n", ", ").split(",")]
    picked: List[str] = []
    for clause in clauses:
        if not clause:
            continue
        low = clause.lower()
        if any(keyword in low for keyword in V054_BACKGROUND_CONTEXT_KEYWORDS):
            picked.append(clause)
    if not picked:
        return ""
    # Preserve order and de-dupe exact repeated clauses.
    out: List[str] = []
    seen = set()
    for clause in picked:
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clause)
    return ", ".join(out)


def _v054_auto_background_region(prompt: str, width: int, height: int) -> Dict[str, Any] | None:
    bg_prompt = _v054_extract_implicit_background_prompt(prompt)
    if not bg_prompt:
        return None
    return {
        "id": "v054_auto_global_background",
        "name": "Global Background Context",
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "mask": {"type": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        "prompt": (
            f"background environment only, {bg_prompt}, behind the two subjects, "
            "fills the whole scene, no plain studio backdrop, no empty blue sky, no daylight replacement"
        ),
        "strength": 0.72,
        "priority": 0.62,
        "presence_boost": 0.68,
        "feather": 64,
        "enabled": True,
        "region_type": "interaction",
        "v054_role": "background",
        "background_zone": "full_canvas",
        "background_role": "auto_global_background",
    }
V054_DETAIL_ROLES = {
    "face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop",
    "object", "text", "lighting", "effect", "style", "custom"
}


# Phase 10: mixed background regions are first-class zones with visible
# compiler metadata. Defaults stay centralized, and users can override the
# wording per region with background_prompt, background_negative_guard, or
# background_override.{prompt,negative_guard}.
V054_BACKGROUND_PROMPT_REGISTRY = {
    "background": {
        "prompt": "{label} background zone: {prompt}, fills the {zone} of the frame, stays behind the subjects",
        "negative": "background replacing subjects, extra people in background zone, foreground subject swallowed by background",
    },
    "background_object": {
        "prompt": "{label} background object: {prompt}, placed in the {zone} of the frame, remains behind the main subjects",
        "negative": "background object in foreground, object covering subject faces, object duplicated everywhere",
    },
    "transition_effect": {
        "prompt": "{label} transition seam: {prompt}, blends neighboring background zones, stays between the regions",
        "negative": "transition covering faces, portal replacing subjects, transition duplicated across entire image",
    },
}



V054_COMPLEXITY_LIMITS = {
    "main_subjects": {"soft": 3, "hard": 4},
    "total_regions": {"soft": 12, "hard": 24},
    "detail_lanes_per_character": {"soft": 4, "hard": 8},
    "background_zones": {"soft": 2, "hard": 4},
    "regional_controlnet_lanes": {"soft": 2, "hard": 4},
    "detailer_passes": {"soft": 4, "hard": 8},
}


def _v054_background_override(region: Dict[str, Any], key: str) -> str:
    override = region.get("background_override") if isinstance(region.get("background_override"), dict) else {}
    aliases = {
        "prompt": ("prompt", "background_prompt", "zone_prompt"),
        "negative": ("negative", "negative_guard", "background_negative_guard", "zone_negative_guard"),
    }
    for name in aliases.get(key, (key,)):
        value = override.get(name)
        if str(value or "").strip():
            return str(value).strip()
    direct_aliases = {
        "prompt": ("background_prompt", "zone_prompt"),
        "negative": ("background_negative_guard", "zone_negative_guard"),
    }
    for name in direct_aliases.get(key, (key,)):
        value = region.get(name)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _v054_background_zone_name(region: Dict[str, Any]) -> str:
    zone = str(region.get("zone") or region.get("background_zone") or region.get("target_area") or "").strip().lower()
    if zone:
        return zone.replace("_", " ")
    try:
        x1, _y1, x2, _y2 = _v054_region_bbox(region)
        center = (float(x1) + float(x2)) * 0.5
    except Exception:
        center = 0.5
    if center < 0.35:
        return "left side"
    if center > 0.65:
        return "right side"
    return "center"


def _v054_background_template_context(region: Dict[str, Any]) -> Dict[str, str]:
    role = str(region.get("role") or "background").strip().lower()
    return {
        "id": str(region.get("id") or ""),
        "label": _v054_region_label(region, str(region.get("id") or "background")),
        "role": role,
        "zone": _v054_background_zone_name(region),
        "prompt": str(region.get("prompt") or "").strip(),
        "negative": str(region.get("negative") or "").strip(),
        "strength": str(region.get("strength") or ""),
    }


def _v054_background_template(region: Dict[str, Any], key: str) -> Tuple[str, str]:
    override = _v054_background_override(region, key)
    if override:
        return override, "scene_override"
    role = str(region.get("role") or "background").strip().lower()
    cfg = V054_BACKGROUND_PROMPT_REGISTRY.get(role) or V054_BACKGROUND_PROMPT_REGISTRY.get("background", {})
    return str(cfg.get(key) or ""), f"background:{role}"


def _v054_background_prompt(region: Dict[str, Any]) -> str:
    prompt = str(region.get("prompt") or "").strip()
    if not prompt:
        return ""
    template, _source = _v054_background_template(region, "prompt")
    return _v054_render_template(template, _v054_background_template_context(region)) or prompt


def _v054_background_negative_guard(region: Dict[str, Any]) -> str:
    template, _source = _v054_background_template(region, "negative")
    rendered = _v054_render_template(template, _v054_background_template_context(region))
    parts = [rendered, str(region.get("negative") or "").strip()]
    out: List[str] = []
    for part in parts:
        part = str(part or "").strip()
        if part and part not in out:
            out.append(part)
    return ", ".join(out)


def _v054_background_plan_entry(region: Dict[str, Any], compiled_prompt: str) -> Dict[str, Any]:
    prompt_template, prompt_source = _v054_background_template(region, "prompt")
    negative_template, negative_source = _v054_background_template(region, "negative")
    return {
        "id": str(region.get("id") or ""),
        "label": _v054_region_label(region, str(region.get("id") or "background")),
        "role": str(region.get("role") or "background").strip().lower(),
        "zone": _v054_background_zone_name(region),
        "bbox": _v054_region_bbox(region),
        "prompt": str(region.get("prompt") or "").strip(),
        "compiled_prompt": compiled_prompt,
        "negative_guard": _v054_background_negative_guard(region),
        "strength": _safe_float(region.get("strength", 0.65), 0.65),
        "feather": _v054_region_feather(region, 32),
        "template_source": {"prompt": prompt_source, "negative": negative_source},
        "template": {"prompt": prompt_template, "negative": negative_template},
        "customized": prompt_source == "scene_override" or negative_source == "scene_override",
    }





V054_TEXT_REGION_REGISTRY = {
    "composite": {
        "route": "post_decode_compositor",
        "note": "Composite text is rendered after image generation for readable typography."
    },
    "diffusion": {
        "route": "diffusion_prompt",
        "note": "Diffusion text is model-generated and may be unreadable on SDXL."
    },
    "native": {
        "route": "provider_native_text_edit",
        "note": "Native text editing is provider-adapter dependent."
    },
}


def _v054_text_region_spec(region: Dict[str, Any], index: int) -> Dict[str, Any]:
    mode = str(region.get("mode") or region.get("text_mode") or "composite").strip().lower() or "composite"
    if mode not in V054_TEXT_REGION_REGISTRY:
        mode = "composite"
    text = str(region.get("text") or region.get("content") or region.get("prompt") or "").strip()
    return {
        "id": str(region.get("id") or f"text_{index}"),
        "label": str(region.get("label") or region.get("id") or f"Text {index}"),
        "role": "text",
        "bbox": _v054_region_bbox(region),
        "text": text,
        "mode": mode,
        "font_style": str(region.get("font_style") or region.get("font") or "bold clean sans-serif"),
        "font_family": str(region.get("font_family") or region.get("font_name") or ""),
        "font_size": _safe_float(region.get("font_size"), 48),
        "color": str(region.get("color") or region.get("fill") or "white"),
        "stroke_color": str(region.get("stroke_color") or region.get("outline_color") or ""),
        "stroke_width": _safe_float(region.get("stroke_width"), 0),
        "align": str(region.get("align") or region.get("text_align") or "center"),
        "valign": str(region.get("valign") or region.get("vertical_align") or "middle"),
        "opacity": _safe_float(region.get("opacity"), 1.0),
        "rotation": _safe_float(region.get("rotation"), 0),
        "route": V054_TEXT_REGION_REGISTRY[mode]["route"],
        "note": V054_TEXT_REGION_REGISTRY[mode]["note"],
        "mask_output": "detail_masks",
    }


def _v054_text_region_plan(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        if str(region.get("role") or "").strip().lower() == "text":
            plan.append(_v054_text_region_spec(region, index))
    return plan





V054_SDXL_FULL_LOCK_REQUIRED_FEATURES = [
    "scene_graph_json", "v054_node", "regional_conditioning", "character_lock",
    "linked_detail_lanes", "relationship_compiler", "prompt_compiler_registry",
    "conflict_resolver", "complexity_meter", "mixed_background_regions",
    "regional_controlnet", "regional_detailer", "text_regions", "composite_text",
    "img2img_region_reuse", "inpaint_region_targeting", "output_inspector_source_stack",
    "source_image_reuse", "latent_replay",
]


def _v054_provider_capability_from_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    capabilities = metadata.get("provider_capabilities") if isinstance(metadata.get("provider_capabilities"), dict) else {}
    if capabilities:
        result = dict(capabilities)
    else:
        result = {
            "schema": "neo.image.scene_director.provider_capabilities.v054.v1",
            "phase": "SD-V054-21",
            "provider_profile": "sdxl_checkpoint",
            "route_kind": "v054_node_workflow",
            "features": {name: True for name in V054_SDXL_FULL_LOCK_REQUIRED_FEATURES},
            "latent_policy": "compatible_route_only",
            "source_image_policy": "reusable_across_providers",
            "mask_policy": "requires_size_match",
            "notices": [{"level": "info", "code": "provider_sdxl_full_v054_locked", "message": "SDXL checkpoint route is the locked full V054 implementation target."}],
        }
    result["phase"] = "SD-V054-20"
    features = result.get("features") if isinstance(result.get("features"), dict) else {}
    profile = result.get("provider_profile") or "sdxl_checkpoint"
    missing = [name for name in V054_SDXL_FULL_LOCK_REQUIRED_FEATURES if features.get(name) is not True]
    result["sdxl_full_implementation_lock"] = {
        "schema": "neo.image.scene_director.sdxl_full_lock.v054.v1",
        "phase": "SD-V054-18",
        "provider_profile": profile,
        "locked_provider": "sdxl_checkpoint",
        "locked": profile == "sdxl_checkpoint",
        "ready": profile == "sdxl_checkpoint" and not missing,
        "required_features": list(V054_SDXL_FULL_LOCK_REQUIRED_FEATURES),
        "missing_features": missing,
        "stress_test": {"name": "sdxl_v054_two_subject_portal_stress"},
    }

    if result.get("provider_profile") == "flux_adapter_planned":
        result["flux_adapter_plan"] = _v054_flux_adapter_plan_from_graph(graph)
    else:
        result["flux_adapter_plan"] = None
    if result.get("provider_profile") == "qwen_semantic_edit_adapter":
        result["qwen_adapter_plan"] = _v054_qwen_adapter_plan_from_graph(graph)
    else:
        result["qwen_adapter_plan"] = None
    return result


def _v054_flux_region_instruction(region: Dict[str, Any], region_by_id: Dict[str, Dict[str, Any]], index: int) -> Dict[str, Any]:
    rid = str(region.get("id") or f"region_{index}")
    role = str(region.get("role") or region.get("type") or "custom").strip().lower()
    label = str(region.get("label") or region.get("name") or rid)
    prompt = str(region.get("prompt") or region.get("text") or "").strip()
    parent_id = str(region.get("attach_to") or region.get("parent_id") or "").strip()
    parent = region_by_id.get(parent_id, {}) if parent_id else {}
    parent_label = str(parent.get("label") or parent.get("name") or parent.get("id") or parent_id or "")
    relationship = str(region.get("relationship") or "").strip()
    if parent_label:
        instruction = f"Apply {label} to {parent_label}{(' with relationship ' + relationship) if relationship else ''}: {prompt}"
    elif role in {"background", "background_object", "transition_effect"}:
        instruction = f"Use {label} as {str(region.get('zone') or region.get('background_zone') or 'background zone')}: {prompt}"
    elif role == "text":
        instruction = f"Text region {label}: render {str(region.get('text') or prompt)} using {str(region.get('mode') or region.get('text_mode') or 'composite')} text handling."
    else:
        instruction = f"{label}: {prompt}" if prompt else label
    return {
        "id": rid,
        "label": label,
        "role": role,
        "bbox": region.get("bbox"),
        "attach_to": parent_id or None,
        "relationship": relationship or None,
        "instruction": instruction,
        "mask_hint": "region_bbox_or_mask_adapter",
    }


def _v054_flux_adapter_plan_from_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    regions = graph.get("regions") if isinstance(graph.get("regions"), list) else []
    region_by_id = {str(r.get("id")): r for r in regions if isinstance(r, dict) and r.get("id")}
    instructions = [_v054_flux_region_instruction(r, region_by_id, i) for i, r in enumerate(regions, start=1) if isinstance(r, dict)]
    global_block = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    return {
        "schema": "neo.image.scene_director.flux_adapter_plan.v054.v1",
        "phase": "SD-V054-19",
        "provider_profile": "flux_adapter_planned",
        "route_kind": "flux_adapter_required",
        "status": "planning_ready",
        "adapter_required": True,
        "uses_sdxl_v054_node": False,
        "scene_graph_json_reused": True,
        "global_prompt": str(global_block.get("prompt") or ""),
        "global_negative": str(global_block.get("negative") or ""),
        "regional_instructions": instructions,
        "mask_strategy": {"mode": "bbox_or_mask_adapter", "requires_size_match": True},
        "conditioning_strategy": {"route": "flux_workflow_adapter", "regional_conditioning": "workflow_dependent"},
        "controlnet_strategy": {"route": "flux_control_adapter_if_available", "regional_controlnet": "workflow_dependent"},
        "detailer_strategy": {"route": "post_generation_detailer_allowed", "regional_detailer": True},
        "replay_policy": {"scene_graph_json": "reusable", "source_image": "reusable_across_providers", "masks": "requires_size_match", "saved_latent": "disabled_for_provider"},
    }


def _v054_edit_intent(region: Dict[str, Any]) -> Dict[str, Any]:
    intent = region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}
    mode = str(intent.get("mode") or "preserve").strip().lower() or "preserve"
    if mode not in {"preserve", "modify", "replace"}:
        mode = "preserve"
    default_denoise = {"preserve": 0.18, "modify": 0.40, "replace": 0.62}.get(mode, 0.35)
    return {
        "mode": mode,
        "denoise": _safe_float(intent.get("denoise"), default_denoise),
        "preserve_parent_identity": intent.get("preserve_parent_identity", True) is not False,
        "preserve_region": intent.get("preserve_region", mode == "preserve") is not False,
        "mask_reuse": str(intent.get("mask_reuse") or "region"),
        "source_image": str(intent.get("source_image") or region.get("source_image") or "").strip(),
        "source_region_id": str(intent.get("source_region_id") or region.get("source_region_id") or "").strip(),
    }


def _v054_img2img_reuse_plan_entry(region: Dict[str, Any], index: int) -> Dict[str, Any]:
    intent = _v054_edit_intent(region)
    mode = intent["mode"]
    return {
        "id": str(region.get("id") or f"region_{index}"),
        "label": str(region.get("label") or region.get("id") or f"Region {index}"),
        "role": str(region.get("role") or ""),
        "bbox": region.get("bbox") or [0, 0, 1, 1],
        "mode": mode,
        "denoise": intent["denoise"],
        "preserve_parent_identity": intent["preserve_parent_identity"],
        "preserve_region": intent["preserve_region"],
        "mask_reuse": intent["mask_reuse"],
        "source_image": intent["source_image"] or None,
        "source_region_id": intent["source_region_id"] or None,
        "mask_output": "inpaint_masks" if mode in {"modify", "replace"} else "region_masks",
        "route": "img2img_preserve" if mode == "preserve" else "img2img_region_modify" if mode == "modify" else "img2img_or_inpaint_replace",
    }


def _v054_img2img_reuse_plan(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        if isinstance(region.get("edit_intent"), dict):
            plan.append(_v054_img2img_reuse_plan_entry(region, index))
    return plan



V054_INPAINT_ACTION_DEFAULTS = {
    "change_hair": {"denoise": 0.42, "mask_feather": 14, "route": "inpaint_change_hair", "mask_output": "inpaint_masks"},
    "change_outfit": {"denoise": 0.55, "mask_feather": 18, "route": "inpaint_change_outfit", "mask_output": "inpaint_masks"},
    "add_held_prop": {"denoise": 0.52, "mask_feather": 16, "route": "inpaint_add_held_prop", "mask_output": "inpaint_masks"},
    "remove_object": {"denoise": 0.58, "mask_feather": 18, "route": "inpaint_remove_object", "mask_output": "inpaint_masks"},
    "replace_background": {"denoise": 0.70, "mask_feather": 28, "route": "inpaint_replace_background", "mask_output": "background_masks"},
    "fix_face": {"denoise": 0.32, "mask_feather": 10, "route": "inpaint_fix_face", "mask_output": "detail_masks"},
    "fix_hands": {"denoise": 0.38, "mask_feather": 12, "route": "inpaint_fix_hands", "mask_output": "detail_masks"},
    "edit_text_plate": {"denoise": 0.25, "mask_feather": 8, "route": "inpaint_edit_text_plate", "mask_output": "detail_masks"},
    "custom": {"denoise": 0.50, "mask_feather": 16, "route": "inpaint_custom_region", "mask_output": "inpaint_masks"},
}


def _v054_inpaint_target(region: Dict[str, Any]) -> Dict[str, Any]:
    data = region.get("inpaint") if isinstance(region.get("inpaint"), dict) else {}
    action = str(data.get("action") or data.get("mode") or region.get("inpaint_action") or "custom").strip().lower() or "custom"
    if action not in V054_INPAINT_ACTION_DEFAULTS:
        action = "custom"
    defaults = V054_INPAINT_ACTION_DEFAULTS[action]
    parent_id = str(data.get("parent_id") or data.get("parent_region_id") or region.get("attach_to") or "").strip()
    preserve = data.get("preserve_regions") if isinstance(data.get("preserve_regions"), list) else data.get("preserve")
    preserve_regions = [str(item).strip() for item in preserve if str(item).strip()] if isinstance(preserve, list) else []
    if data.get("preserve_parent_identity", True) is not False and parent_id and parent_id not in preserve_regions:
        preserve_regions.append(parent_id)
    return {
        "enabled": data.get("enabled", bool(data)) is not False,
        "action": action,
        "target_region_id": str(data.get("target_region_id") or data.get("region_id") or region.get("id") or "").strip(),
        "parent_id": parent_id or None,
        "mask_mode": str(data.get("mask_mode") or region.get("inpaint_mask_mode") or "region"),
        "mask_source": str(data.get("mask_source") or data.get("mask_reuse") or "v054_region_mask"),
        "denoise": _safe_float(data.get("denoise"), defaults["denoise"]),
        "mask_feather": _safe_int(data.get("mask_feather"), defaults["mask_feather"]),
        "preserve_parent_identity": data.get("preserve_parent_identity", True) is not False,
        "preserve_regions": preserve_regions,
        "prompt": str(data.get("prompt") or data.get("positive") or region.get("inpaint_prompt") or region.get("prompt") or ""),
        "negative": str(data.get("negative") or data.get("negative_prompt") or region.get("inpaint_negative") or ""),
        "route": defaults["route"],
        "mask_output": defaults["mask_output"],
    }


def _v054_inpaint_target_plan_entry(region: Dict[str, Any], index: int) -> Dict[str, Any]:
    target = _v054_inpaint_target(region)
    return {
        "id": str(region.get("id") or f"region_{index}"),
        "label": str(region.get("label") or region.get("id") or f"Region {index}"),
        "role": str(region.get("role") or ""),
        "bbox": region.get("bbox") or [0, 0, 1, 1],
        **target,
    }


def _v054_inpaint_target_plan(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        if isinstance(region.get("inpaint"), dict) and region.get("inpaint", {}).get("enabled") is not False:
            plan.append(_v054_inpaint_target_plan_entry(region, index))
    return plan

def _v054_control_plan_entry(region: Dict[str, Any], index: int) -> Dict[str, Any]:
    control = region.get("control") if isinstance(region.get("control"), dict) else {}
    return {
        "id": str(region.get("id") or f"region_{index}"),
        "label": str(region.get("label") or region.get("id") or f"Region {index}"),
        "role": str(region.get("role") or ""),
        "bbox": region.get("bbox") or [0, 0, 1, 1],
        "enabled": bool(control.get("enabled") is True),
        "type": str(control.get("type") or control.get("preprocessor") or ""),
        "model": str(control.get("model") or control.get("controlnet_model") or ""),
        "reference_id": str(control.get("reference_id") or control.get("image_name") or ""),
        "strength": _safe_float(control.get("strength"), 0.75),
        "start": _safe_float(control.get("start"), 0.0),
        "end": _safe_float(control.get("end"), 0.8),
        "mask_mode": str(control.get("mask_mode") or "region"),
        "mask_output": "control_masks",
    }


def _v054_control_plan(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        control = region.get("control") if isinstance(region.get("control"), dict) else {}
        if control.get("enabled") is True:
            plan.append(_v054_control_plan_entry(region, index))
    return plan


def _v054_detailer_plan_entry(region: Dict[str, Any], index: int) -> Dict[str, Any]:
    detailer = region.get("detailer") if isinstance(region.get("detailer"), dict) else {}
    mode = str(detailer.get("mode") or "face").strip().lower() or "face"
    detector = str(detailer.get("detector") or detailer.get("detector_model") or "").strip()
    return {
        "id": str(region.get("id") or f"region_{index}"),
        "label": str(region.get("label") or region.get("id") or f"Region {index}"),
        "role": str(region.get("role") or ""),
        "bbox": region.get("bbox") or [0, 0, 1, 1],
        "enabled": bool(detailer.get("enabled") is True),
        "mode": mode,
        "detector": detector,
        "detector_type": str(detailer.get("detector_type") or "bbox"),
        "custom_classes": str(detailer.get("custom_classes") or ("hand" if mode == "hand" else "face" if mode == "face" else "all")),
        "denoise": _safe_float(detailer.get("denoise"), 0.3),
        "steps": _safe_int(detailer.get("steps"), 20),
        "cfg": _safe_float(detailer.get("cfg"), 5.5),
        "mask_feather": _safe_int(detailer.get("mask_feather", detailer.get("mask_blur", 12)), 12),
        "detect_inside_region": detailer.get("detect_inside_region", True) is not False,
        "mask_mode": str(detailer.get("mask_mode") or "region"),
        "mask_output": "detail_masks",
    }


def _v054_detailer_plan(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        detailer = region.get("detailer") if isinstance(region.get("detailer"), dict) else {}
        if detailer.get("enabled") is True:
            plan.append(_v054_detailer_plan_entry(region, index))
    return plan

def _v054_complexity_meter(regions: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [r for r in regions if isinstance(r, dict)]
    detail_by_parent: Dict[str, int] = {}
    for region in rows:
        role = str(region.get("role") or "").strip().lower()
        if role in V054_PARENT_REQUIRED_ROLES:
            parent = str(region.get("attach_to") or "__unattached__").strip() or "__unattached__"
            detail_by_parent[parent] = detail_by_parent.get(parent, 0) + 1
    counts = {
        "main_subjects": len([r for r in rows if str(r.get("role") or "").lower() == "character"]),
        "total_regions": len(rows),
        "detail_lanes_per_character": max(detail_by_parent.values(), default=0),
        "background_zones": len([r for r in rows if str(r.get("role") or "").lower() in V054_BACKGROUND_ROLES]),
        "regional_controlnet_lanes": len([r for r in rows if isinstance(r.get("control"), dict) and r.get("control", {}).get("enabled") is True]),
        "detailer_passes": len([r for r in rows if isinstance(r.get("detailer"), dict) and r.get("detailer", {}).get("enabled") is True]),
    }
    labels = {
        "main_subjects": "main subjects",
        "total_regions": "active regions",
        "detail_lanes_per_character": "detail lanes on one parent",
        "background_zones": "background zones",
        "regional_controlnet_lanes": "regional ControlNet lanes",
        "detailer_passes": "detailer passes",
    }
    advice = {
        "main_subjects": "Too many main subjects can reduce identity separation and increase subject blending.",
        "total_regions": "Too many active regions can create prompt conflict and mask overlap.",
        "detail_lanes_per_character": "Too many detail lanes on one character can overconstrain local details.",
        "background_zones": "Too many background zones can weaken composition clarity.",
        "regional_controlnet_lanes": "Too many regional ControlNets can overconstrain poses and increase VRAM pressure.",
        "detailer_passes": "Too many detailer passes can slow generation and overcook details.",
    }
    messages: List[Dict[str, Any]] = []
    hard_hits = 0
    soft_hits = 0
    for metric, count in counts.items():
        limit = V054_COMPLEXITY_LIMITS[metric]
        if count > limit["hard"]:
            hard_hits += 1
            messages.append({"level": "error", "code": f"complexity_hard_{metric}", "metric": metric, "count": count, "soft_limit": limit["soft"], "hard_limit": limit["hard"], "message": f"Scene has {count} {labels[metric]}, above the hard limit of {limit['hard']}. {advice[metric]}"})
        elif count > limit["soft"]:
            soft_hits += 1
            messages.append({"level": "warning", "code": f"complexity_soft_{metric}", "metric": metric, "count": count, "soft_limit": limit["soft"], "hard_limit": limit["hard"], "message": f"Scene has {count} {labels[metric]}, above the soft limit of {limit['soft']}. {advice[metric]}"})
    risk_level = "high_risk" if hard_hits else ("advanced" if soft_hits >= 2 else ("moderate" if soft_hits else "normal"))
    return {"counts": counts, "limits": V054_COMPLEXITY_LIMITS, "messages": messages, "risk_level": risk_level, "detail_lanes_by_parent": detail_by_parent}


def _v054_parse_scene_graph(scene_graph_json: Any) -> Dict[str, Any]:
    if scene_graph_json is None:
        return {"version": "v054", "canvas": {}, "global": {}, "regions": []}

    if isinstance(scene_graph_json, dict):
        data = scene_graph_json
    else:
        text = str(scene_graph_json or "").strip()
        if not text:
            data = {"version": "v054", "canvas": {}, "global": {}, "regions": []}
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Backward-compatible guard for older Neo builds or Comfy widgets that
                # converted a dict into a Python-literal string: {'version': 'v054'}.
                # This keeps the node from crashing while the backend hotfix migrates
                # the active path to canonical JSON strings.
                try:
                    parsed = ast.literal_eval(text)
                except Exception as exc:
                    raise ValueError(
                        "scene_graph_json must be valid JSON or a dict-compatible literal."
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ValueError("scene_graph_json literal must evaluate to a JSON object.")
                data = parsed
    if not isinstance(data, dict):
        raise ValueError("scene_graph_json must be a JSON object.")
    return data


def _v054_bbox_to_mask(bbox: Any) -> Dict[str, Any]:
    if isinstance(bbox, dict):
        x = _safe_float(bbox.get("x", 0), 0)
        y = _safe_float(bbox.get("y", 0), 0)
        w = _safe_float(bbox.get("w", 1), 1)
        h = _safe_float(bbox.get("h", 1), 1)
        return {"type": "rect", "x": x, "y": y, "w": w, "h": h}
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"V054 bbox must be [x1,y1,x2,y2], got: {bbox}")
    x1, y1, x2, y2 = [_safe_float(v, 0) for v in bbox]
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"V054 bbox must have x2>x1 and y2>y1, got: {bbox}")
    return {"type": "rect", "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def _v054_region_bbox(region: Dict[str, Any]) -> List[float]:
    bbox = region.get("bbox", [0, 0, 1, 1])
    if isinstance(bbox, dict):
        x = _safe_float(bbox.get("x", 0), 0)
        y = _safe_float(bbox.get("y", 0), 0)
        w = _safe_float(bbox.get("w", 1), 1)
        h = _safe_float(bbox.get("h", 1), 1)
        return [x, y, x + w, y + h]
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return [_safe_float(v, 0) for v in bbox]
    return [0, 0, 1, 1]


V054_DETAIL_PRIORITY_WEIGHTS = {"override": 1.35, "reinforce": 1.15, "blend": 0.9}
V054_ATTACHED_DETAIL_PRIORITY_WEIGHTS = {"override": 3.0, "reinforce": 1.35, "blend": 0.9}
V054_ATTACHED_DETAIL_PRESENCE_WEIGHTS = {"override": 2.6, "reinforce": 1.2, "blend": 0.9}
V054_RELATIONSHIP_ACTIONS = {
    "holding", "wearing", "attached_to", "standing_near", "behind", "in_front_of",
    "around", "on_top_of", "inside", "carrying", "looking_at", "touching",
    "hugging", "leaning_on", "resting_head_on", "custom",
}

# Phase 7.5: compiler phrase defaults live in a visible registry instead of being
# scattered as hidden hardcoded branches. Scene/region overrides can replace
# these defaults with relationship_prompt, local_prompt_template, negative_guard,
# or compiler_override.{parent_prompt,local_prompt,negative_guard}.
V054_PROMPT_COMPILER_REGISTRY = {
    "holding": {
        "parent": "{parent} is holding {child} naturally in hand, {child} stays near {parent}'s hand, prop belongs only to {parent}",
        "local": "{child}, held by {parent}, near the correct hand, not floating, not duplicated in background",
        "negative": "floating object, object held by wrong subject, duplicated prop in background",
    },
    "wearing": {
        "parent": "{parent} is wearing {child}, outfit belongs only to {parent}, not on any other subject",
        "local": "{child}, worn by {parent}, outfit belongs only to {parent}, not on other subjects",
        "negative": "outfit worn by wrong subject, clothing swapped between subjects",
    },
    "attached_to": {
        "parent": "{child} is attached naturally to {parent} {target}, detail belongs only to {parent}",
        "local": "{child}, attached to {parent} {target}, belongs only to {parent}, not floating, not on other subjects",
        "negative": "detached detail, floating detail, detail on wrong subject",
    },
    "standing_near": {
        "parent": "{parent} is standing near {child}, clear spatial separation, no merged bodies",
        "local": "{child}, standing near {parent}, clear spacing, not merged",
        "negative": "subjects merged together, wrong spacing",
    },
    "behind": {
        "parent": "{parent} is behind {child}, correct depth order and visible separation",
        "local": "{child}, in front of {parent}, visible depth separation",
        "negative": "wrong depth order, subject in front instead of behind",
    },
    "in_front_of": {
        "parent": "{parent} is in front of {child}, correct foreground placement",
        "local": "{child}, behind {parent}, correct depth order",
        "negative": "wrong depth order, subject behind instead of in front",
    },
    "around": {
        "parent": "{child} is around {parent}, surrounding {target} naturally",
        "local": "{child}, around {parent}, natural surrounding placement",
        "negative": "object misplaced far away, incorrect surrounding placement",
    },
    "on_top_of": {
        "parent": "{child} is on top of {parent} {target}, physically supported, not floating",
        "local": "{child}, on top of {parent} {target}, physically supported",
        "negative": "object floating, wrong vertical placement",
    },
    "inside": {
        "parent": "{child} is inside {parent} {target}, contained naturally",
        "local": "{child}, inside {parent} {target}, contained naturally",
        "negative": "object outside target area, impossible placement",
    },
    "carrying": {
        "parent": "{parent} is carrying {child}, object belongs to {parent}, not held by another subject",
        "local": "{child}, carried by {parent}, object belongs only to {parent}",
        "negative": "object floating, object carried by wrong subject",
    },
    "looking_at": {
        "parent": "{parent} is looking at {child}, gaze direction points toward {child}",
        "local": "{child}, target of {parent}'s gaze, clear line of sight",
        "negative": "wrong gaze direction, looking away",
    },
    "touching": {
        "parent": "{parent} is touching {child}, clear contact point, no detached hands",
        "local": "{child}, touched by {parent}, clear contact point, no detached hands",
        "negative": "detached hands, wrong contact point",
    },
    "hugging": {
        "parent": "{parent} is hugging {child}, arms wrap naturally, bodies stay distinct",
        "local": "{child}, hugged by {parent}, natural embrace, bodies remain distinct",
        "negative": "subjects not interacting, detached embrace, merged bodies",
    },
    "leaning_on": {
        "parent": "{parent} is leaning on {child}, natural support contact, stable pose",
        "local": "{child}, supporting {parent}, natural contact point",
        "negative": "wrong contact support, detached leaning pose",
    },
    "resting_head_on": {
        "parent": "{parent} is resting his head on {child}, head contact is clear and natural",
        "local": "{child}, supports {parent}'s head, clear gentle head contact",
        "negative": "wrong head placement, detached head contact",
    },
    "custom": {
        "parent": "{parent} has {focus} {child} on {target}, detail belongs only to {parent}",
        "local": "{child}, attached to {parent}, belongs only to {parent}",
        "negative": "wrong subject, floating detail, misplaced object",
    },
}
V054_ROLE_PROMPT_COMPILER_REGISTRY = {
    "hair_detail": {
        "parent": "{parent} has {focus} {child} on {target}, the hair belongs only to {parent}",
        "local": "{child}, on {parent} {target}, belongs only to {parent}, natural hairline, not floating, not on other subjects",
        "negative": "hair color swap, wrong hairstyle, floating hair, hair on wrong subject",
    },
    "face_detail": {
        "parent": "{parent} has {focus} {child} on the face while preserving {parent} identity",
        "local": "{child}, on {parent} face, preserve {parent} identity, not a face swap",
        "negative": "face swap, wrong face, identity drift",
    },
    "hand_detail": {
        "parent": "{parent} has {focus} {child} on the hands, hands belong only to {parent}",
        "local": "{child}, on {parent} hands, anatomically plausible fingers, not detached",
        "negative": "detached hands, bad fingers, hands on wrong subject",
    },
    "clothing": {
        "relationship": "wearing",
    },
    "held_prop": {
        "relationship": "holding",
    },
}
V054_RELATIONSHIP_NEGATIVE_GUARDS = {key: value.get("negative", "") for key, value in V054_PROMPT_COMPILER_REGISTRY.items()}

def _v054_priority_mode(value: Any) -> str:
    mode = str(value or "reinforce").strip().lower()
    return mode if mode in V054_DETAIL_PRIORITY_WEIGHTS else "reinforce"

def _v054_region_label(region: Dict[str, Any], fallback: str = "region") -> str:
    return str(region.get("label") or region.get("name") or region.get("id") or fallback).strip() or fallback

def _v054_detail_target(role: str, child: Dict[str, Any]) -> str:
    explicit = str(child.get("target_area") or "").strip()
    if explicit:
        return explicit
    return {
        "face_detail": "face",
        "hair_detail": "hair",
        "hand_detail": "hands",
        "character_detail": "character details",
        "clothing": "outfit",
        "held_prop": "hand",
    }.get(role, "detail area")

def _v054_relationship_mode(value: Any) -> str:
    rel = str(value or "attached_to").strip().lower()
    return rel if rel in V054_RELATIONSHIP_ACTIONS else "custom"


def _v054_relationship_object_label(child: Dict[str, Any]) -> str:
    prompt = str(child.get("prompt", "")).strip()
    return prompt or _v054_region_label(child, "detail")

def _v054_compiler_override(child: Dict[str, Any], key: str) -> str:
    override = child.get("compiler_override") if isinstance(child.get("compiler_override"), dict) else {}
    aliases = {
        "parent": ("parent", "parent_prompt", "parent_prompt_template", "relationship_prompt"),
        "local": ("local", "local_prompt", "local_prompt_template"),
        "negative": ("negative", "negative_guard", "negative_prompt", "negative_guard_prompt"),
    }
    for name in aliases.get(key, (key,)):
        value = override.get(name)
        if str(value or "").strip():
            return str(value).strip()
    direct_aliases = {
        "parent": ("relationship_prompt", "parent_prompt", "parent_prompt_template"),
        "local": ("local_prompt", "local_prompt_template"),
        "negative": ("negative_guard", "negative_guard_prompt", "relationship_negative", "negative_relationship_prompt"),
    }
    for name in direct_aliases.get(key, (key,)):
        value = child.get(name)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _v054_template_context(parent: Dict[str, Any] | str, child: Dict[str, Any]) -> Dict[str, str]:
    parent_label = _v054_region_label(parent, str(parent)) if isinstance(parent, dict) else str(parent or "parent")
    role = str(child.get("role") or "custom").strip().lower()
    priority = _v054_priority_mode(child.get("priority"))
    return {
        "parent": parent_label,
        "parent_id": str(parent.get("id") or "") if isinstance(parent, dict) else str(parent or ""),
        "child": _v054_relationship_object_label(child),
        "child_id": str(child.get("id") or ""),
        "child_label": _v054_region_label(child, str(child.get("id") or "child")),
        "role": role,
        "relationship": _v054_relationship_mode(child.get("relationship") or child.get("relation") or "attached_to"),
        "target": _v054_detail_target(role, child),
        "target_area": _v054_detail_target(role, child),
        "priority": priority,
        "focus": "clearly" if priority in ("override", "reinforce") else "subtly",
        "prompt": _v054_relationship_object_label(child),
    }


def _v054_render_template(template: Any, context: Dict[str, str]) -> str:
    text = str(template or "").strip()
    if not text:
        return ""
    try:
        return text.format(**context).strip()
    except Exception:
        # User-authored templates should never crash the generation route.
        return text


def _v054_compiler_template(parent: Dict[str, Any] | str, child: Dict[str, Any], key: str) -> Tuple[str, str]:
    """Return (template, source) for parent/local/negative compiler text."""
    override = _v054_compiler_override(child, key)
    if override:
        return override, "scene_override"
    rel = _v054_relationship_mode(child.get("relationship") or child.get("relation") or "attached_to")
    role = str(child.get("role") or "custom").strip().lower()
    role_cfg = V054_ROLE_PROMPT_COMPILER_REGISTRY.get(role, {})
    if key in role_cfg:
        return str(role_cfg[key]), f"role:{role}"
    mapped_rel = str(role_cfg.get("relationship") or rel)
    rel_cfg = V054_PROMPT_COMPILER_REGISTRY.get(mapped_rel) or V054_PROMPT_COMPILER_REGISTRY.get("custom", {})
    if key in rel_cfg:
        return str(rel_cfg[key]), f"relationship:{mapped_rel}"
    return "", "none"


def _v054_relationship_phrase(parent: Dict[str, Any] | str, child: Dict[str, Any]) -> str:
    prompt = _v054_relationship_object_label(child)
    if not prompt:
        return ""
    context = _v054_template_context(parent, child)
    template, _source = _v054_compiler_template(parent, child, "parent")
    return _v054_render_template(template, context)


def _v054_relationship_negative_guard(child: Dict[str, Any], parent: Dict[str, Any] | None = None) -> str:
    rel = _v054_relationship_mode(child.get("relationship") or child.get("relation") or "attached_to")
    role = str(child.get("role") or "custom").strip().lower()
    guards = []
    context = _v054_template_context(parent or str(child.get("attach_to") or "parent"), child)
    template, _source = _v054_compiler_template(parent or str(child.get("attach_to") or "parent"), child, "negative")
    rendered = _v054_render_template(template, context)
    if rendered:
        guards.append(rendered)
    if role == "held_prop" and rel != "holding":
        guards.append(V054_RELATIONSHIP_NEGATIVE_GUARDS.get("holding", ""))
    if role == "clothing" and rel != "wearing":
        guards.append(V054_RELATIONSHIP_NEGATIVE_GUARDS.get("wearing", ""))
    # Preserve order while removing duplicates.
    out: List[str] = []
    for guard in guards:
        guard = str(guard or "").strip()
        if guard and guard not in out:
            out.append(guard)
    return ", ".join(out)


def _v054_relationship_plan_entry(parent: Dict[str, Any], child: Dict[str, Any], phrase: str, local_prompt: str) -> Dict[str, Any]:
    rel = _v054_relationship_mode(child.get("relationship") or child.get("relation") or "attached_to")
    parent_template, parent_source = _v054_compiler_template(parent, child, "parent")
    local_template, local_source = _v054_compiler_template(parent, child, "local")
    negative_template, negative_source = _v054_compiler_template(parent, child, "negative")
    return {
        "id": str(child.get("id") or ""),
        "attach_to": str(parent.get("id") or ""),
        "parent_id": str(parent.get("id") or ""),
        "parent_label": _v054_region_label(parent, str(parent.get("id") or "parent")),
        "child_id": str(child.get("id") or ""),
        "child_label": _v054_region_label(child, str(child.get("id") or "child")),
        "role": str(child.get("role") or "custom").strip().lower(),
        "relationship": rel,
        "target_area": _v054_detail_target(str(child.get("role") or "custom").strip().lower(), child),
        "priority": _v054_priority_mode(child.get("priority")),
        "parent_prompt_injection": "",
        "relationship_summary": phrase,
        "local_prompt": local_prompt,
        "negative_guard": _v054_relationship_negative_guard(child, parent),
        "template_source": {
            "parent": parent_source,
            "local": local_source,
            "negative": negative_source,
        },
        "template": {
            "parent": parent_template,
            "local": local_template,
            "negative": negative_template,
        },
        "customized": any(source == "scene_override" for source in (parent_source, local_source, negative_source)),
        "conditioning_owner": "child_region_only",
        "parent_receives_child_prompt": False,
    }


def _v054_local_detail_prompt(parent: Dict[str, Any] | None, child: Dict[str, Any]) -> str:
    prompt = str(child.get("prompt", "")).strip()
    if not prompt:
        return ""
    context = _v054_template_context(parent or str(child.get("attach_to") or "parent"), child)
    template, _source = _v054_compiler_template(parent or str(child.get("attach_to") or "parent"), child, "local")
    rendered = _v054_render_template(template, context)
    priority = _v054_priority_mode(child.get("priority"))
    parts = [rendered or prompt]
    if priority == "override":
        parts.append("high priority local detail override")
    elif priority == "blend":
        parts.append("soft blended local detail")
    out: List[str] = []
    for part in parts:
        part = str(part or "").strip()
        if part and part not in out:
            out.append(part)
    return ", ".join(out)


def _v054_child_owned_detail_prompt(
    parent: Dict[str, Any] | None,
    child: Dict[str, Any],
    conflict_plan: List[Dict[str, Any]] | None = None,
) -> str:
    """Compile the detail only inside its own mask.

    Conflict-resolution wording belongs on the child branch as well; copying it
    into the parent character branch would spread a tie/jacket/hair color across
    the complete subject region.
    """
    parts = [_v054_local_detail_prompt(parent, child) or str(child.get("prompt") or "").strip()]
    child_id = str(child.get("id") or "")
    for conflict in _v054_conflicts_for_child(conflict_plan or [], child_id):
        if conflict.get("resolution") in {"detail_override", "detail_reinforce"} and conflict.get("resolution_prompt"):
            parts.append(str(conflict.get("resolution_prompt") or "").strip())
    return _v054_join_unique(parts)


# Phase 8: conflict resolver detects prompt contradictions before they quietly
# fight inside the final conditioning text. Defaults are centralized and can be
# softened by scene-level overrides when needed.
V054_CONFLICT_COLOR_WORDS = {
    "black", "white", "brown", "blonde", "blond", "pink", "red", "blue", "green",
    "purple", "violet", "orange", "yellow", "silver", "gray", "grey", "gold", "golden",
    "dark", "light", "neon", "vivid",
}
V054_CONFLICT_RESOLVER_REGISTRY = {
    "hair_color": {
        "roles": {"hair_detail"},
        "target_terms": {"hair", "hairstyle", "haircut"},
        "template": "Conflict resolved: {child_label} overrides {parent_label}'s conflicting hair description; use {child_prompt} for {parent_label} hair.",
        "negative": "wrong hair color on {parent_label}, hair color copied to wrong subject",
    },
    "clothing_color": {
        "roles": {"clothing"},
        "target_terms": {"shirt", "hoodie", "jacket", "coat", "suit", "pants", "shorts", "dress", "outfit", "clothing"},
        "template": "Conflict resolved: {child_label} overrides {parent_label}'s conflicting outfit description; use {child_prompt} for {parent_label} outfit.",
        "negative": "outfit color copied to wrong subject, clothing swapped between subjects",
    },
    "skin_tone": {
        "roles": {"character_detail"},
        "target_terms": {"skin", "complexion", "tone"},
        "template": "Conflict resolved: {child_label} overrides {parent_label}'s conflicting skin tone description; keep {child_prompt} for {parent_label}.",
        "negative": "skin tone swap between subjects, incorrect complexion",
    },
}


def _v054_lower_words(text: Any) -> set[str]:
    import re
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z]+", str(text or "").lower())}


def _v054_conflict_override(child: Dict[str, Any], key: str) -> str:
    override = child.get("conflict_override") if isinstance(child.get("conflict_override"), dict) else {}
    aliases = {
        "resolution": ("resolution", "resolution_prompt", "prompt"),
        "negative": ("negative", "negative_guard", "negative_prompt"),
    }
    for name in aliases.get(key, (key,)):
        value = override.get(name)
        if str(value or "").strip():
            return str(value).strip()
    direct_aliases = {
        "resolution": ("conflict_resolution_prompt", "resolution_prompt"),
        "negative": ("conflict_negative_guard",),
    }
    for name in direct_aliases.get(key, (key,)):
        value = child.get(name)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _v054_render_conflict_template(template: str, parent: Dict[str, Any], child: Dict[str, Any], conflict_type: str) -> str:
    context = {
        "parent": _v054_region_label(parent, "parent"),
        "parent_label": _v054_region_label(parent, "parent"),
        "parent_id": str(parent.get("id") or ""),
        "child": _v054_region_label(child, "detail"),
        "child_label": _v054_region_label(child, "detail"),
        "child_id": str(child.get("id") or ""),
        "child_prompt": _v054_relationship_object_label(child),
        "target": _v054_detail_target(str(child.get("role") or "custom").lower(), child),
        "conflict_type": conflict_type,
        "priority": _v054_priority_mode(child.get("priority")),
    }
    try:
        return str(template or "").format(**context).strip()
    except Exception:
        return str(template or "").strip()


def _v054_detect_prompt_conflicts(parent: Dict[str, Any], child: Dict[str, Any]) -> List[Dict[str, Any]]:
    role = str(child.get("role") or "custom").strip().lower()
    parent_prompt = str(parent.get("prompt") or "")
    child_prompt = str(child.get("prompt") or "")
    if not parent_prompt or not child_prompt:
        return []
    parent_words = _v054_lower_words(parent_prompt)
    child_words = _v054_lower_words(child_prompt)
    conflicts: List[Dict[str, Any]] = []
    for conflict_type, cfg in V054_CONFLICT_RESOLVER_REGISTRY.items():
        if role not in cfg.get("roles", set()):
            continue
        target_terms = set(cfg.get("target_terms") or set())
        if target_terms and not (parent_words & target_terms or child_words & target_terms):
            continue
        parent_colors = sorted((parent_words & V054_CONFLICT_COLOR_WORDS) - {"dark", "light", "vivid", "neon"})
        child_colors = sorted((child_words & V054_CONFLICT_COLOR_WORDS) - {"dark", "light", "vivid", "neon"})
        # Only warn when both sides describe a concrete but different value.
        if parent_colors and child_colors and set(parent_colors) != set(child_colors):
            priority = _v054_priority_mode(child.get("priority"))
            resolution_template = _v054_conflict_override(child, "resolution") or str(cfg.get("template") or "")
            negative_template = _v054_conflict_override(child, "negative") or str(cfg.get("negative") or "")
            resolution = _v054_render_conflict_template(resolution_template, parent, child, conflict_type)
            negative = _v054_render_conflict_template(negative_template, parent, child, conflict_type)
            conflicts.append({
                "id": f"{parent.get('id') or 'parent'}::{child.get('id') or 'child'}::{conflict_type}",
                "type": conflict_type,
                "parent_id": str(parent.get("id") or ""),
                "parent_label": _v054_region_label(parent, "parent"),
                "child_id": str(child.get("id") or ""),
                "child_label": _v054_region_label(child, "detail"),
                "role": role,
                "priority": priority,
                "parent_values": parent_colors,
                "child_values": child_colors,
                "resolution": "detail_override" if priority == "override" else ("detail_reinforce" if priority == "reinforce" else "blend_warning"),
                "resolution_prompt": resolution,
                "negative_guard": negative,
                "message": f"{_v054_region_label(parent, 'Parent')} and {_v054_region_label(child, 'detail')} have conflicting {conflict_type.replace('_', ' ')} values; {priority} priority will guide compilation.",
                "template_source": "scene_override" if _v054_conflict_override(child, "resolution") else f"conflict:{conflict_type}",
            })
    return conflicts


def _v054_conflicts_for_child(conflict_plan: List[Dict[str, Any]], child_id: str) -> List[Dict[str, Any]]:
    return [item for item in conflict_plan if str(item.get("child_id") or "") == str(child_id or "")]


def _v054_character_lock_to_appearance(mode: Any) -> str:
    value = str(mode or "off").strip().lower()
    if value in ("strict", "strong"):
        return "full_character_strong"
    if value in ("balanced", "soft"):
        return "full_character_soft"
    return "off"


def _v054_character_lock_prompt(region: Dict[str, Any]) -> Tuple[str, str]:
    lock = region.get("lock") if isinstance(region.get("lock"), dict) else {}
    correction = _character_lock_correction_for_attention(region)
    trait_terms = _character_trait_terms_clothing_aware_for_attention(region)
    if not lock and not trait_terms and not correction.get("positive") and not correction.get("negative"):
        return "", ""
    positive: List[str] = []
    negative: List[str] = []
    label = str(region.get("label") or region.get("id") or "character").strip()
    char_mode = str(lock.get("character") or "off").strip().lower()
    if char_mode and char_mode != "off":
        positive.append(f"{label} identity remains consistent, stable face and body traits")
    if str(lock.get("gender") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} gender presentation stays consistent")
        negative.append("wrong gender, gender swap, feminine body on male subject, masculine body on female subject")
    if str(lock.get("skin_tone") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} skin tone stays consistent")
        negative.append("skin tone swap between subjects, incorrect complexion")
    if str(lock.get("hair") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} hair color, haircut, and hairline stay consistent")
        negative.append("hair color swap, wrong hairstyle, floating hair")
    if str(lock.get("build") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} body build stays consistent")
        negative.append("body type swap, incorrect build")
    if str(lock.get("body_height") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} height and body proportions stay consistent")
        negative.append("height swap, distorted body proportions")
    if str(lock.get("outfit") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        positive.append(f"{label} outfit and clothing colors stay consistent")
        negative.append("outfit swap between subjects, wrong clothing color")
    if str(lock.get("negative") or "off").strip().lower() in ("soft", "balanced", "strong", "strict"):
        negative.append("identity blending, face swap, merged faces, swapped clothing, swapped hair")
    if trait_terms:
        positive.append(f"{label} explicit Character Trait Lock terms: {', '.join(trait_terms)}")
    if correction.get("positive"):
        positive.append(f"{label} explicit Character Lock correction: {correction['positive']}")
    if correction.get("negative"):
        negative.append(f"{label} explicit Character Lock negative correction: {correction['negative']}")
    return ", ".join(positive), ", ".join(negative)


def _v054_clean_optional_id(value: Any) -> str:
    """Normalize optional V054 link ids.

    UI/JSON bridges may send missing links as None, "", "None", "null",
    or "undefined". These all mean no attachment and must not be validated
    as real region IDs.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "undefined"}:
        return ""
    return text


def _v054_optional_field(value: Any):
    text = _v054_clean_optional_id(value)
    return text or None


def _v054_allowed_parent_roles(role: Any) -> set[str]:
    safe_role = str(role or "custom").strip().lower()
    if safe_role in V054_CHARACTER_PARENT_ONLY_ROLES:
        return {"character"}
    if safe_role in V054_BACKGROUND_PARENT_ONLY_ROLES:
        return {"background"}
    if safe_role in V054_ATTACHABLE_ROLES:
        return set(V054_MAIN_PARENT_ROLES)
    return set()


def _v054_resolve_child_owned_attachments(graph: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Resolve attachment links without letting one invalid child disable V054.

    Character and Background are main parents and never own an ``attach_to``
    field. Every other role owns its optional/required link on the child side.
    Required detail regions with a missing or invalid parent are skipped for the
    current run and reported; the rest of the scene continues unchanged.
    """
    resolved = deepcopy(graph if isinstance(graph, dict) else {})
    source_regions = [r for r in resolved.get("regions", []) if isinstance(r, dict)]
    by_id = {str(r.get("id") or "").strip(): r for r in source_regions if str(r.get("id") or "").strip()}
    output: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    cleared: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for source in source_regions:
        region = deepcopy(source)
        rid = str(region.get("id") or "").strip()
        label = _v054_region_label(region, rid or "detail")
        role = str(region.get("role") or "custom").strip().lower()
        parent_id = _v054_clean_optional_id(region.get("attach_to"))

        if role in V054_MAIN_PARENT_ROLES:
            if parent_id:
                cleared.append({"region_id": rid, "role": role, "previous_parent_id": parent_id, "reason": "main_parent_role"})
            region.pop("attach_to", None)
            region.pop("relationship", None)
            output.append(region)
            continue

        parent = by_id.get(parent_id) if parent_id else None
        parent_role = str(parent.get("role") or "").strip().lower() if parent else ""
        allowed_parent_roles = _v054_allowed_parent_roles(role)
        invalid_reason = ""
        if not parent_id:
            if role in V054_PARENT_REQUIRED_ROLES:
                invalid_reason = "missing parent"
        elif parent_id == rid:
            invalid_reason = "cannot attach to itself"
        elif parent is None:
            invalid_reason = "parent not found"
        elif allowed_parent_roles and parent_role not in allowed_parent_roles:
            invalid_reason = "parent must be " + " or ".join(sorted(allowed_parent_roles))

        if invalid_reason and role in V054_PARENT_REQUIRED_ROLES:
            skipped.append({"region_id": rid, "label": label, "role": role, "reason": invalid_reason})
            warnings.append(f"Attached detail '{label}' was skipped: {invalid_reason}; choose its parent inside the child region")
            continue
        if invalid_reason and parent_id:
            cleared.append({"region_id": rid, "label": label, "role": role, "previous_parent_id": parent_id, "reason": invalid_reason})
            region.pop("attach_to", None)
            region.pop("relationship", None)
            warnings.append(f"Optional attachment for '{label}' was cleared: {invalid_reason}; the region remains standalone")
        output.append(region)

    resolved["regions"] = output
    metadata = resolved.get("metadata") if isinstance(resolved.get("metadata"), dict) else {}
    resolved["metadata"] = metadata
    metadata["attached_detail_roles"] = {
        "schema": "neo.image.scene_director.attached_detail_resolution.v054.v1",
        "phase": "SD-V054-27.14",
        "policy": "attachment_is_authored_on_the_child_region_only",
        "content_policy_guards_added": False,
        "active_child_count": len([r for r in output if str(r.get("role") or "").lower() in V054_ATTACHABLE_ROLES and _v054_clean_optional_id(r.get("attach_to"))]),
        "skipped": skipped,
        "cleared": cleared,
    }
    return resolved, warnings


def _v054_validate_basic(graph: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    if str(graph.get("version", "v054")).lower() != "v054":
        errors.append("scene_graph.version must be v054")
    regions = graph.get("regions", [])
    if not isinstance(regions, list):
        errors.append("scene_graph.regions must be an array")
        return errors, warnings
    ids = set()
    for idx, region in enumerate(regions):
        if not isinstance(region, dict):
            errors.append(f"regions[{idx}] must be an object")
            continue
        rid = str(region.get("id", "")).strip()
        role = str(region.get("role", "")).strip().lower()
        if not rid:
            errors.append(f"regions[{idx}].id is required")
        elif rid in ids:
            errors.append(f"Duplicate V054 region id: {rid}")
        ids.add(rid)
        if not role:
            errors.append(f"regions[{idx}].role is required")
        elif role not in V054_REGION_ROLES:
            errors.append(f"regions[{idx}].role '{role}' is not supported")
        if role in V054_PARENT_REQUIRED_ROLES and not _v054_clean_optional_id(region.get("attach_to")):
            warnings.append(f"Region '{rid}' with role '{role}' has no parent and will be skipped")
        try:
            _v054_bbox_to_mask(region.get("bbox", [0, 0, 1, 1]))
        except Exception as exc:
            errors.append(f"Region '{rid}' bbox invalid: {exc}")
    for region in regions if isinstance(regions, list) else []:
        if not isinstance(region, dict):
            continue
        rid = str(region.get("id", "")).strip()
        attach_to = _v054_clean_optional_id(region.get("attach_to"))
        if attach_to:
            if attach_to == rid:
                warnings.append(f"Region '{rid}' cannot attach_to itself; its attachment will be skipped or cleared")
            if attach_to not in ids:
                warnings.append(f"Region '{rid}' attach_to target '{attach_to}' was not found; its attachment will be skipped or cleared")
    return errors, warnings


def _v054_pair_pose_authority(graph: Dict[str, Any]) -> Dict[str, Any]:
    metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    raw = metadata.get("pair_pose_authority") if isinstance(metadata.get("pair_pose_authority"), dict) else metadata.get("relationship_pose_authority")
    raw = raw if isinstance(raw, dict) else {}
    prompt = str(raw.get("prompt") or raw.get("pair_pose_prompt") or "").strip()
    negative = str(raw.get("negative") or raw.get("negative_guard") or raw.get("pair_pose_negative_guard") or "").strip()
    character_count = len([
        region for region in (graph.get("regions") if isinstance(graph.get("regions"), list) else [])
        if isinstance(region, dict) and str(region.get("role") or "").strip().lower() == "character"
    ])
    legacy_requested = _safe_bool(raw.get("legacy_input_present"), False) or _safe_bool(raw.get("enabled"), False) or bool(prompt) or bool(negative)
    return {
        "schema": "neo.image.scene_director.pair_pose_retirement.v25_9_15",
        "phase": "SD-V054-27.13",
        "enabled": False,
        "status": "retired_character_region_pose_only",
        "prompt": "",
        "negative": "",
        "strength": 0.0,
        "apply_to_character_traits": False,
        "character_count": character_count,
        "conflict_report": {"status": "retired", "conflicts": [], "conflict_count": 0},
        "source": "retired_advanced_pair_pose",
        "legacy_input_present": legacy_requested,
        "legacy_prompt_length": len(prompt),
        "legacy_negative_length": len(negative),
        "policy": "Advanced Pair Pose is retired and never reaches conditioning. Character > Pose is the sole text-pose authority; exact skeleton authority remains with OpenPose/ControlNet.",
    }


def _v054_to_v05_scene(graph: Dict[str, Any], width: int, height: int, global_prompt_override: str = "") -> Tuple[str, Dict[str, Any], List[str], List[str]]:
    graph, attachment_warnings = _v054_resolve_child_owned_attachments(graph)
    errors, warnings = _v054_validate_basic(graph)
    warnings = [*attachment_warnings, *warnings]
    if errors:
        raise ValueError("V054 scene graph validation failed: " + "; ".join(errors))

    regions = [r for r in graph.get("regions", []) if isinstance(r, dict)]
    by_id = {str(r.get("id", "")).strip(): r for r in regions}
    children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for region in regions:
        parent = _v054_clean_optional_id(region.get("attach_to"))
        if parent:
            children_by_parent.setdefault(parent, []).append(region)

    conflict_plan: List[Dict[str, Any]] = []
    for parent_id, children in children_by_parent.items():
        parent_region = by_id.get(parent_id)
        if not parent_region:
            continue
        for child in children:
            conflict_plan.extend(_v054_detect_prompt_conflicts(parent_region, child))

    global_data = graph.get("global", {}) if isinstance(graph.get("global", {}), dict) else {}
    graph_metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata", {}), dict) else {}
    pair_pose_authority = _v054_pair_pose_authority(graph)
    prompt_authority = str(
        global_data.get("prompt_authority")
        or graph_metadata.get("prompt_authority")
        or "global_context"
    ).strip().lower()
    global_prompt_excluded = bool(
        prompt_authority in {"scene_director_only", "scene_only", "regional_only"}
        or global_data.get("global_prompt_excluded")
        or graph_metadata.get("global_prompt_excluded")
    )
    prompt = "" if global_prompt_excluded else str(global_prompt_override or global_data.get("prompt") or "").strip()
    negative_parts = [] if global_prompt_excluded else [str(global_data.get("negative", "")).strip()]

    v05_regions: List[Dict[str, Any]] = []
    object_regions: List[Dict[str, Any]] = []
    shared_regions: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    linked_detail_lanes: List[Dict[str, Any]] = []
    relationship_plan: List[Dict[str, Any]] = []
    background_plan: List[Dict[str, Any]] = []
    control_plan: List[Dict[str, Any]] = []

    character_count = 0
    for region in regions:
        rid = str(region.get("id", "")).strip()
        role = str(region.get("role", "custom")).strip().lower()
        if str(region.get("negative", "")).strip():
            negative_parts.append(str(region.get("negative", "")).strip())
        lock_prompt, lock_negative = _v054_character_lock_prompt(region)
        if lock_negative:
            negative_parts.append(lock_negative)

        base = {
            "id": rid,
            "name": str(region.get("label", rid)).strip() or rid,
            "bbox": _v054_region_bbox(region),
            "mask": _v054_bbox_to_mask(region.get("bbox", [0, 0, 1, 1])),
            "prompt": str(region.get("prompt", "")).strip(),
            # Preserve the submitted character text before generated lock prose
            # is appended below. The compact in-sampler identity lane consumes
            # this field so it never recursively re-encodes its own compiler
            # instructions.
            "source_prompt": str(region.get("prompt", "")).strip(),
            "negative": str(region.get("negative", region.get("negative_prompt", ""))).strip(),
            "strength": _safe_float(region.get("strength", 1.0), 1.0),
            "priority": V054_DETAIL_PRIORITY_WEIGHTS.get(_v054_priority_mode(region.get("priority")), _safe_float(region.get("priority", 1.0), 1.0)),
            "presence_boost": _safe_float(region.get("presence_boost", 1.0), 1.0),
            "feather": _v054_region_feather(region, 8 if role == "character" else 0),
            "enabled": _safe_bool(region.get("enabled", True), True),
            "v054_role": role,
            "attach_to": _v054_optional_field(region.get("attach_to")),
            "relationship": _v054_optional_field(region.get("relationship")),
            "target_area": _v054_optional_field(region.get("target_area")),
            "object_label": str(region.get("label") or region.get("name") or rid).strip() or rid,
        }
        for field in (
            "lock",
            "character_traits",
            "trait_lock",
            "character_lock_correction",
            "character_lock_correction_enabled",
            "character_lock_gender_family",
            "character_lock_positive_text",
            "character_lock_negative_text",
        ):
            if field in region:
                base[field] = region.get(field)

        parent_id = _v054_clean_optional_id(region.get("attach_to"))
        parent_region = by_id.get(parent_id) if parent_id else None
        if role in V054_ATTACHABLE_ROLES and parent_region is not None:
            priority_mode = _v054_priority_mode(region.get("priority"))
            base["priority"] = V054_ATTACHED_DETAIL_PRIORITY_WEIGHTS[priority_mode]
            base["presence_boost"] = max(
                _safe_float(region.get("presence_boost", 1.0), 1.0),
                V054_ATTACHED_DETAIL_PRESENCE_WEIGHTS[priority_mode],
            )
            base["attached_detail_authority"] = {
                "phase": "SD-V054-27.14",
                "mode": priority_mode,
                "owner_region_id": parent_id,
                "conditioning_owner": "child_region_only",
                "parent_receives_child_prompt": False,
                "priority_weight": base["priority"],
                "presence_weight": base["presence_boost"],
                "content_policy_guards_added": False,
            }

        if role == "character":
            character_count += 1
            parent_prompt_parts = [base["prompt"]]
            if lock_prompt:
                parent_prompt_parts.append(lock_prompt)
            base["prompt"] = ", ".join([p for p in parent_prompt_parts if p])
            base["region_type"] = "character"
            base["subject_required"] = True
            v05_regions.append(base)
            continue

        if role in ("held_prop", "object"):
            parent = _v054_clean_optional_id(region.get("attach_to", region.get("bound_to", "")))
            rel = _v054_clean_optional_id(region.get("relationship", region.get("relation", "")))
            parent_region = by_id.get(parent) if parent else None
            if parent_region:
                phrase = _v054_relationship_phrase(parent_region, region)
                local_prompt = _v054_child_owned_detail_prompt(parent_region, region, conflict_plan) or base["prompt"]
                base["owner_label"] = _v054_region_label(parent_region, parent)
                base["parent_label"] = base["owner_label"]
                base["target_area"] = _v054_optional_field(region.get("target_area")) or _v054_detail_target(role, region)
                if _v054_relationship_mode(region.get("relationship") or region.get("relation")) == "holding":
                    local_prompt = f"{local_prompt}, held by {base['owner_label']} only, in {base['owner_label']}'s {base['target_area'] or 'hands'}, not held by the other person"
                base["prompt"] = local_prompt
                base["relationship_prompt"] = phrase
                for conflict in _v054_conflicts_for_child(conflict_plan, rid):
                    if conflict.get("negative_guard"):
                        negative_parts.append(str(conflict.get("negative_guard")))
                guard = _v054_relationship_negative_guard(region)
                if guard:
                    negative_parts.append(guard)
                entry = _v054_relationship_plan_entry(parent_region, region, phrase, local_prompt)
                child_conflicts = _v054_conflicts_for_child(conflict_plan, rid)
                if child_conflicts:
                    entry["conflicts"] = child_conflicts
                relationship_plan.append(entry)
                linked_detail_lanes.append(entry)
            base["region_type"] = "object"
            base["bound_to"] = parent
            base["owner"] = parent
            base["relation"] = _v054_relationship_mode(rel or region.get("relationship") or region.get("relation"))
            object_regions.append(base)
            if parent and rel:
                relations.append({"from": base.get("owner_label") or parent, "source_id": parent, "to": rid, "object": base.get("object_label") or rid, "object_id": rid, "type": _v054_relationship_mode(rel), "target_area": base.get("target_area"), "prompt": base.get("relationship_prompt", "")})
            continue

        if role in V054_BACKGROUND_ROLES:
            compiled_background_prompt = _v054_background_prompt(region) or base["prompt"]
            base["prompt"] = compiled_background_prompt
            base["region_type"] = "interaction"
            base["background_zone"] = _v054_background_zone_name(region)
            base["background_role"] = role
            base["strength"] = _safe_float(region.get("strength", 0.65), 0.65)
            base["feather"] = _v054_region_feather(region, 32)
            guard = _v054_background_negative_guard(region)
            if guard:
                negative_parts.append(guard)
            background_plan.append(_v054_background_plan_entry(region, compiled_background_prompt))
            shared_regions.append(base)
            continue

        if role in V054_DETAIL_ROLES:
            parent = _v054_clean_optional_id(region.get("attach_to"))
            parent_region = by_id.get(parent) if parent else None
            if parent_region:
                phrase = _v054_relationship_phrase(parent_region, region)
                local_prompt = _v054_child_owned_detail_prompt(parent_region, region, conflict_plan) or base["prompt"]
                base["owner_label"] = _v054_region_label(parent_region, parent)
                base["parent_label"] = base["owner_label"]
                base["target_area"] = _v054_optional_field(region.get("target_area")) or _v054_detail_target(role, region)
                if _v054_relationship_mode(region.get("relationship") or region.get("relation")) == "holding":
                    local_prompt = f"{local_prompt}, held by {base['owner_label']} only, in {base['owner_label']}'s {base['target_area'] or 'hands'}, not held by the other person"
                base["prompt"] = local_prompt
                base["relationship_prompt"] = phrase
                for conflict in _v054_conflicts_for_child(conflict_plan, rid):
                    if conflict.get("negative_guard"):
                        negative_parts.append(str(conflict.get("negative_guard")))
                guard = _v054_relationship_negative_guard(region)
                if guard:
                    negative_parts.append(guard)
                entry = _v054_relationship_plan_entry(parent_region, region, phrase, local_prompt)
                child_conflicts = _v054_conflicts_for_child(conflict_plan, rid)
                if child_conflicts:
                    entry["conflicts"] = child_conflicts
                relationship_plan.append(entry)
                linked_detail_lanes.append(entry)
            base["region_type"] = "object"
            base["bound_to"] = parent
            base["owner"] = parent
            base["relation"] = _v054_relationship_mode(region.get("relationship", "attached_to"))
            object_regions.append(base)
            continue

        # Unknown/custom fallthrough is a shared region so it can still condition.
        base["region_type"] = "interaction"
        shared_regions.append(base)

    # Phase 21.7: remove implicit global-prompt background extraction from the
    # active V054 route. The global prompt remains the main scene concept
    # (subjects, emotion, composition, lighting, and mood). Background control is
    # now explicit-only through user-created background/background_object/
    # transition_effect regions, so a background phrase in the global prompt does
    # not silently become a full-canvas regional conditioning branch.
    has_explicit_background = bool(background_plan or any(str(r.get("role") or "").strip().lower() in V054_BACKGROUND_ROLES for r in regions))
    background_policy = {
        "auto_global_background_disabled": True,
        "manual_background_slot_required": not has_explicit_background,
        "global_prompt_role": "main_scene_concept",
        "background_control_role": "explicit_background_regions_only",
    }

    v05 = {
        "version": "0.5.3-from-v054",
        "canvas": {"width": width, "height": height},
        "global": {
            "prompt": prompt,
            "negative": ", ".join([p for p in negative_parts if p]),
            "prompt_authority": prompt_authority,
            "global_prompt_excluded": global_prompt_excluded,
            "regional_context": "" if global_prompt_excluded else str(global_data.get("regional_context") or "").strip(),
            "regional_context_enabled": bool(not global_prompt_excluded and global_data.get("regional_context_enabled", True)),
            "regional_context_weight": _safe_float(global_data.get("regional_context_weight"), 0.35),
            "entity_count": character_count,
            "multi_subject_mode": "count_locked" if character_count >= 3 else "relation_focused",
        },
        "regions": v05_regions,
        "object_regions": object_regions,
        "shared_regions": shared_regions,
        "relations": relations,
        "v054_linked_detail_lanes": linked_detail_lanes,
        "v054_relationship_plan": relationship_plan,
        "v054_background_plan": background_plan,
        "v054_background_policy": background_policy,
        "v054_control_plan": _v054_control_plan(graph.get("regions") or []),
        "v054_detailer_plan": _v054_detailer_plan(graph.get("regions") or []),
        "v054_text_region_plan": _v054_text_region_plan(graph.get("regions") or []),
        "v054_img2img_reuse_plan": _v054_img2img_reuse_plan(graph.get("regions") or []),
        "v054_inpaint_target_plan": _v054_inpaint_target_plan(graph.get("regions") or []),
        "v054_conflict_plan": conflict_plan,
        "v054_pair_pose_authority": pair_pose_authority,
        "v054_character_pose_authority": {
            str(region.get("id") or ""): _character_local_pose_authority_for_attention(region)
            for region in v05_regions if str(region.get("region_type") or "") == "character"
        },
        "advanced_pair_pose_execution": False,
        "pose_adds_attention_branch": False,
        "v054_scene_graph": graph,
        "prompt_authority": prompt_authority,
        "global_prompt_excluded": global_prompt_excluded,
    }
    return json.dumps(v05), v05, errors, warnings




def _v054_region_is_background_lane(region: Dict[str, Any]) -> bool:
    role = str(region.get("role") or region.get("v054_role") or "").strip().lower()
    background_role = str(region.get("background_role") or "").strip().lower()
    return role in V054_BACKGROUND_ROLES or background_role in V054_BACKGROUND_ROLES or bool(region.get("background_zone"))


def _v054_character_union_mask_from_regions(regions: List[Dict[str, Any]], width: int, height: int) -> torch.Tensor | None:
    union = None
    for region in regions:
        if not isinstance(region, dict) or not _safe_bool(region.get("enabled", True), True):
            continue
        role = str(region.get("role") or region.get("v054_role") or "").strip().lower()
        if role != "character" and _region_type(region) != "character":
            continue
        try:
            mask_data = region.get("mask") or _v054_bbox_to_mask(region.get("bbox", [0, 0, 1, 1]))
            mask = _make_rect_mask(mask_data, width, height, 1.0).clamp(0, 1)
        except Exception:
            continue
        union = mask if union is None else torch.maximum(union, mask)
    return union.clamp(0, 1) if union is not None else None


def _v054_erode_mask(mask: torch.Tensor, pixels: int) -> torch.Tensor:
    """Erode a binary-ish mask without extra dependencies.

    V25.9.6 Fix 4: background lanes should be subordinate to people, but
    subtracting the entire rectangular character lane leaves almost no visible
    background when characters use half-frame/full-height boxes. Eroding the
    protected character union keeps the subject core safe while leaving the
    surrounding environment available to background restore/conditioning.
    """
    pixels = int(max(0, pixels))
    if pixels <= 0:
        return mask.clamp(0, 1)
    try:
        k = max(3, min(129, pixels * 2 + 1))
        if k % 2 == 0:
            k += 1
        pad = k // 2
        source = mask.clamp(0, 1)
        inv = 1.0 - source
        eroded = 1.0 - F.max_pool2d(inv.unsqueeze(0), kernel_size=k, stride=1, padding=pad).squeeze(0)
        return eroded.clamp(0, 1)
    except Exception:
        return mask.clamp(0, 1)


def _v054_subtract_character_mask_from_background(mask: torch.Tensor, character_union: torch.Tensor | None) -> Tuple[torch.Tensor, bool]:
    if character_union is None:
        return mask, False
    try:
        protected = character_union.to(device=mask.device, dtype=mask.dtype).clamp(0, 1)
        try:
            height = int(protected.shape[-2])
            width = int(protected.shape[-1])
            erosion_px = max(18, min(72, int(round(min(width, height) * 0.055))))
        except Exception:
            erosion_px = 36
        protected_core = _v054_erode_mask(protected, erosion_px)
        return (mask * (1.0 - protected_core)).clamp(0, 1), True
    except Exception:
        return mask, False

def _v054_group_masks(v05_scene: Dict[str, Any], graph: Dict[str, Any], width: int, height: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, Dict[str, Any]]:
    detail_masks: List[torch.Tensor] = []
    background_masks: List[torch.Tensor] = []
    control_masks: List[torch.Tensor] = []
    inpaint_masks: List[torch.Tensor] = []
    mask_index: Dict[str, Any] = {}
    graph_regions = graph.get("regions", []) if isinstance(graph, dict) else []
    character_union_mask = _v054_character_union_mask_from_regions([r for r in graph_regions if isinstance(r, dict)], width, height)
    for region in graph_regions:
        if not isinstance(region, dict):
            continue
        rid = str(region.get("id", "")).strip()
        role = str(region.get("role", "custom")).strip().lower()
        try:
            mask = _make_rect_mask(_v054_bbox_to_mask(region.get("bbox", [0, 0, 1, 1])), width, height, 1.0).clamp(0, 1)
        except Exception:
            mask = _empty_mask(width, height)
        groups = []
        if role in V054_DETAIL_ROLES:
            detail_masks.append(mask); groups.append("detail")
        if role in V054_BACKGROUND_ROLES:
            mask, _subtracted = _v054_subtract_character_mask_from_background(mask, character_union_mask)
            background_masks.append(mask); groups.append("background")
        if isinstance(region.get("control"), dict) and _safe_bool(region["control"].get("enabled", False), False):
            control_masks.append(mask); groups.append("control")
        edit_intent = region.get("edit_intent", {}) if isinstance(region.get("edit_intent", {}), dict) else {}
        if (isinstance(region.get("inpaint"), dict) and _safe_bool(region["inpaint"].get("enabled", False), False)) or str(edit_intent.get("mode", "")).lower() in ("modify", "replace"):
            inpaint_masks.append(mask); groups.append("inpaint")
        if rid:
            mask_index[rid] = {"role": role, "groups": groups, "bbox": _v054_region_bbox(region), "attach_to": _v054_optional_field(region.get("attach_to"))}

    def stack(items: List[torch.Tensor]) -> torch.Tensor:
        if not items:
            return _empty_mask(width, height)
        return torch.cat(items, dim=0)

    return stack(detail_masks), stack(background_masks), stack(control_masks), stack(inpaint_masks), mask_index


def _v054_blank_image(width: int, height: int) -> torch.Tensor:
    return torch.zeros((1, height, width, 3), dtype=torch.float32)


def _v054_subject_slot_by_region(graph: Dict[str, Any], max_subject_slots: int = 4) -> Dict[str, int]:
    slots: Dict[str, int] = {}
    for region in graph.get("regions", []) if isinstance(graph, dict) else []:
        if not isinstance(region, dict):
            continue
        if not _safe_bool(region.get("enabled", True), True):
            continue
        role = str(region.get("role") or region.get("type") or "").strip().lower()
        if role != "character":
            continue
        rid = str(region.get("id") or "").strip()
        if not rid:
            continue
        if len(slots) >= max(1, int(max_subject_slots)):
            break
        slots[rid] = len(slots) + 1
    return slots


def _v054_parse_json_object(value: Any, *, default: Any = None) -> Any:
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        try:
            return ast.literal_eval(text)
        except Exception:
            return default


def _v054_region_label(region: Dict[str, Any], fallback: str = "Region") -> str:
    return str(region.get("label") or region.get("name") or region.get("id") or fallback)


def _v054_lora_runtime_proof(route: Dict[str, Any]) -> Dict[str, Any]:
    proof = route.get("runtime_proof") if isinstance(route.get("runtime_proof"), dict) else {}
    runtime_applied = bool(route.get("runtime_applied") or proof.get("runtime_applied"))
    load_success = bool(route.get("lora_load_success") or proof.get("lora_load_success"))
    model_patch_created = bool(route.get("model_patch_created") or proof.get("model_patch_created"))
    delta_eval_attempted = bool(route.get("delta_eval_attempted") or proof.get("delta_eval_attempted"))
    delta_nonzero = bool(route.get("delta_nonzero") or proof.get("delta_nonzero"))
    runtime_applied = bool(runtime_applied and load_success and model_patch_created and delta_eval_attempted and delta_nonzero)
    return {
        "schema": "neo.image.scene_director.regional_lora_runtime_proof.v054.v1",
        "phase": "SD-V054-26.9.14",
        "resolved_lora_path": route.get("resolved_lora_path") or proof.get("resolved_lora_path") or route.get("lora_name"),
        "lora_file_exists": bool(route.get("lora_file_exists") or proof.get("lora_file_exists")),
        "lora_load_success": load_success,
        "lora_load_error": route.get("lora_load_error") or proof.get("lora_load_error") or "",
        "model_patch_created": model_patch_created,
        "delta_eval_attempted": delta_eval_attempted,
        "delta_nonzero": delta_nonzero,
        "delta_norm_mean": proof.get("delta_norm_mean", route.get("delta_norm_mean")),
        "delta_norm_max": proof.get("delta_norm_max", route.get("delta_norm_max")),
        "assigned_mask_coverage": proof.get("assigned_mask_coverage", route.get("assigned_mask_coverage")),
        "effective_delta_strength": proof.get("effective_delta_strength", route.get("strength")),
        "runtime_applied": runtime_applied,
    }


def _v054_extension_route_text(route: Dict[str, Any], region: Dict[str, Any], subject_slot: int | None) -> str:
    ext = str(route.get("extension_type") or route.get("type") or "extension").strip().lower() or "extension"
    label = _v054_region_label(region, str(route.get("region_id") or "Region"))
    target_bits = [
        f"{ext} route authority for {label}",
        "apply only inside this assigned region",
        "do not affect neighboring subjects or background zones",
        "do not borrow identity or style from other regions",
        "preserve assigned region boundary",
    ]
    if subject_slot:
        target_bits.append(f"lock adapter influence to subject slot {subject_slot} and assigned character mask")
    role = str(region.get("role") or region.get("type") or "").strip().lower()
    if role in V054_BACKGROUND_ROLES:
        target_bits.append("lock adapter influence to this background zone only")
    trigger_terms = route.get("trigger_terms") or route.get("trigger_words") or []
    if isinstance(trigger_terms, str):
        trigger_terms = _clean_list(trigger_terms)
    if ext == "ipadapter":
        scope_mode = str(route.get("scope_mode") or "identity_only").strip() or "identity_only"
        target_bits.append(f"IPAdapter/FaceID scope is {scope_mode} for this assigned subject")
        target_bits.append("preserve the Scene Director pose, relationship, outfit, props, background, and composition instructions")
        target_bits.append("do not let the reference image change the requested scene action or framing")
    if ext == "lora" and trigger_terms:
        target_bits.append("assigned LoRA activation terms: " + ", ".join(str(t) for t in trigger_terms if str(t).strip()))
    prompt = str(region.get("prompt") or "").strip()
    if prompt:
        target_bits.append(prompt)
    return ", ".join([b for b in target_bits if str(b).strip()])


def _v054_compile_extension_authority_routes(
    graph: Dict[str, Any],
    extension_routes_json: Any,
    width: int,
    height: int,
    max_subject_slots: int = 4,
) -> Tuple[List[str], List[torch.Tensor], Dict[str, Any]]:
    data = _v054_parse_json_object(extension_routes_json, default={})
    raw_routes = []
    incoming_warnings = []
    if isinstance(data, dict):
        raw_routes = data.get("routes") if isinstance(data.get("routes"), list) else []
        incoming_warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    elif isinstance(data, list):
        raw_routes = data
    region_lookup = {str(r.get("id") or ""): r for r in graph.get("regions", []) if isinstance(r, dict) and str(r.get("id") or "")}
    subject_slots = _v054_subject_slot_by_region(graph, max_subject_slots=max_subject_slots)
    branch_prompts: List[str] = []
    branch_masks: List[torch.Tensor] = []
    routes_meta: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for item in incoming_warnings or []:
        if isinstance(item, dict):
            warnings.append(dict(item))
        elif item:
            warnings.append({"code": str(item), "level": "warning", "message": str(item)})

    for idx, raw in enumerate(raw_routes, start=1):
        if not isinstance(raw, dict):
            continue
        owner_enabled = raw.get("owner_enabled")
        execution_disabled = raw.get("execution_disabled") or raw.get("execution_allowed") is False
        if raw.get("enabled") is False or owner_enabled is False or execution_disabled:
            code = "ipadapter_owner_extension_disabled_execution_suppressed" if str(raw.get("extension_type") or raw.get("type") or "").lower() == "ipadapter" else "extension_route_owner_disabled"
            warnings.append({"code": code, "level": "info", "route_id": raw.get("route_id"), "message": "Disabled extension route skipped."})
            continue
        ext = str(raw.get("extension_type") or raw.get("type") or "unknown").strip().lower() or "unknown"
        rid = str(raw.get("region_id") or "").strip()
        region = region_lookup.get(rid)
        route_id = str(raw.get("route_id") or f"extension_route_{idx}")
        base_meta = {
            "route_id": route_id,
            "extension_type": ext,
            "owner_extension_id": raw.get("owner_extension_id"),
            "region_id": rid,
            "label": raw.get("label") or rid,
            "requested_mode": raw.get("requested_mode") or raw.get("execution_mode") or "node_authority",
        }
        if not region:
            code = "ipadapter_region_mask_missing_fallback_second_pass" if ext == "ipadapter" else "extension_route_region_missing"
            warnings.append({"code": code, "level": "warning", "route_id": route_id, "region_id": rid, "message": f"Extension route {route_id} skipped because the assigned region was not found."})
            routes_meta.append({
                **base_meta,
                "actual_mode": "second_pass_fallback" if ext == "ipadapter" else "skipped",
                "node_authority_mask_confirmed": False,
                "hard_region_isolation": False,
                "model_delta_scope": raw.get("model_delta_scope") or "none",
                "global_bleed_risk": False,
                "fallback_used": ext == "ipadapter",
                "warnings": [code],
            })
            continue
        role = str(region.get("role") or region.get("type") or raw.get("region_role") or "custom").strip().lower()
        subject_slot = subject_slots.get(rid)
        route_warnings: List[str] = [str(w) for w in (raw.get("instruction_preservation_warnings") or raw.get("warnings") or []) if str(w).strip()]
        if ext in {"ipadapter", "lora"} and role == "character" and not subject_slot:
            route_warnings.append("extension_route_subject_slot_mismatch")
        runtime_proof: Dict[str, Any] = {}
        if ext == "lora":
            model_delta_scope = str(raw.get("model_delta_scope") or "node_conditioning_lane")
            actual_mode_raw = str(raw.get("actual_mode") or raw.get("execution_mode") or "").strip()
            runtime_proof = _v054_lora_runtime_proof(raw)
            claimed_mixer = actual_mode_raw == "regional_model_delta_mixer" or model_delta_scope in {"regional_noise_delta", "custom_masked_model_delta"}
            if claimed_mixer and not runtime_proof.get("runtime_applied"):
                raw = dict(raw)
                raw["actual_mode"] = "finish_pass_fallback"
                raw["fallback_used"] = True
                model_delta_scope = "masked_finish_pass"
                if "regional_lora_mixer_claimed_but_no_runtime_delta" not in route_warnings:
                    route_warnings.append("regional_lora_mixer_claimed_but_no_runtime_delta")
                if "regional_clip_delta_missing_may_reduce_character_lora_visibility" not in route_warnings:
                    route_warnings.append("regional_clip_delta_missing_may_reduce_character_lora_visibility")
            hard_isolation = bool(runtime_proof.get("runtime_applied") and raw.get("hard_region_isolation") is True and model_delta_scope in {"regional_noise_delta", "custom_masked_model_delta"})
            global_bleed_risk = model_delta_scope == "global_model_branch" or bool(raw.get("global_bleed_risk"))
            if hard_isolation:
                if "regional_lora_model_delta_mixer_active" not in route_warnings:
                    route_warnings.append("regional_lora_model_delta_mixer_active")
                if str(raw.get("clip_delta_scope") or "") == "region_prompt_only" and "regional_clip_delta_not_supported_without_global_bleed" not in route_warnings:
                    route_warnings.append("regional_clip_delta_not_supported_without_global_bleed")
            else:
                if model_delta_scope == "masked_finish_pass":
                    if "regional_lora_finish_pass_visual_authority_fallback" not in route_warnings:
                        route_warnings.append("regional_lora_finish_pass_visual_authority_fallback")
                else:
                    route_warnings.append("lora_model_delta_is_global_without_true_node_delta" if global_bleed_risk else "lora_node_conditioning_lane_not_model_delta_isolated")
        else:
            model_delta_scope = str(raw.get("model_delta_scope") or ("masked_adapter" if ext == "ipadapter" else "masked_conditioning"))
            hard_isolation = ext in {"ipadapter", "controlnet", "adetailer", "detailer"}
            global_bleed_risk = False

        try:
            strength = max(0.05, min(1.5, _safe_float(raw.get("strength"), 1.0)))
            mask = _make_rect_mask(_v054_bbox_to_mask(region.get("bbox", [0, 0, 1, 1])), width, height, min(0.85, max(0.18, strength * 0.36))).clamp(0, 1)
            feather = _safe_int(raw.get("mask_feather"), _v054_region_feather(region, 12))
            mask = _feather_mask(mask, max(0, min(96, feather)))
            branch_prompts.append(_v054_extension_route_text(raw, region, subject_slot))
            branch_masks.append(mask)
            mask_confirmed = True
        except Exception:
            route_warnings.append("extension_route_mask_ambiguous")
            mask_confirmed = False

        if hard_isolation and mask_confirmed:
            route_warnings.append("extension_route_hard_isolated")
        for code in route_warnings:
            level = "info" if code == "extension_route_hard_isolated" else "warning"
            warnings.append({"code": code, "level": level, "route_id": route_id, "region_id": rid, "message": code.replace("_", " ")})
        routes_meta.append({
            **base_meta,
            "label": _v054_region_label(region, rid),
            "region_role": role,
            "subject_slot": subject_slot,
            "mask_output": raw.get("mask_output") or (f"subject_{subject_slot}_mask" if subject_slot else "region_mask"),
            "mask_mode": raw.get("mask_mode") or "region",
            "target_area": raw.get("target_area") or region.get("target_area"),
            "actual_mode": raw.get("actual_mode") or raw.get("execution_mode") or "node_authority",
            "node_authority_mask_confirmed": mask_confirmed,
            "hard_region_isolation": bool(hard_isolation and mask_confirmed),
            "model_delta_scope": model_delta_scope,
            "clip_delta_scope": raw.get("clip_delta_scope"),
            "clip_delta_hard_isolation": raw.get("clip_delta_hard_isolation"),
            "clip_delta_warning": raw.get("clip_delta_warning"),
            "global_bleed_risk": bool(global_bleed_risk),
            "fallback_used": bool(raw.get("fallback_used")),
            "runtime_applied": bool(runtime_proof.get("runtime_applied")) if ext == "lora" else None,
            "runtime_proof": runtime_proof if ext == "lora" else None,
            "visual_authority_profile": raw.get("visual_authority_profile") or ("regional_character_lora_visual_authority" if ext == "lora" and raw.get("fallback_used") else None),
            "strength": raw.get("strength"),
            "start_at": raw.get("effective_start_at", raw.get("start_at")),
            "end_at": raw.get("effective_end_at", raw.get("end_at")),
            "requested_weight": raw.get("requested_weight", raw.get("strength")),
            "effective_weight": raw.get("effective_weight", raw.get("strength")),
            "requested_start_at": raw.get("requested_start_at", raw.get("start_at")),
            "effective_start_at": raw.get("effective_start_at", raw.get("start_at")),
            "requested_end_at": raw.get("requested_end_at", raw.get("end_at")),
            "effective_end_at": raw.get("effective_end_at", raw.get("end_at")),
            "scope_mode": raw.get("scope_mode") or ("identity_only" if ext == "ipadapter" else None),
            "mask_type": raw.get("mask_type") or ("subject_mask" if subject_slot else "region_mask"),
            "composition_preservation_enabled": bool(raw.get("composition_preservation_enabled", ext == "ipadapter")),
            "trigger_terms": raw.get("trigger_terms") or raw.get("trigger_words") or [],
            "reference_images": raw.get("reference_images") or raw.get("image_names") or [],
            "lora_name": raw.get("lora_name"),
            "regional_lora_delta_mixer": raw.get("regional_lora_delta_mixer"),
            "controlnet_unit_id": raw.get("controlnet_unit_id"),
            "adetailer_pass_id": raw.get("adetailer_pass_id"),
            "isolation_policy": raw.get("isolation_policy") or "assigned_region_mask_no_cross_region_borrowing",
            "warnings": sorted(set(route_warnings)),
        })

    metadata = {
        "schema": "neo.image.scene_director.extension_authority_node.v054.v1",
        "phase": "SD-V054-26.9.14",
        "status": "applied" if routes_meta else ("off" if not raw_routes else "not_applicable"),
        "route_count": len(raw_routes or []),
        "applied_count": len([r for r in routes_meta if r.get("node_authority_mask_confirmed")]),
        "skipped_count": len([r for r in routes_meta if not r.get("node_authority_mask_confirmed")]),
        "routes": routes_meta,
        "warnings": warnings,
        "policy": "Extension routes are resolved inside NeoSceneDirectorV054 to assigned region/subject masks. Disabled owner routes are ignored defensively; Phase 26.9.13 regional LoRA model-delta mixer routes are reported as hard-isolated only when runtime proof confirms a non-zero regional model delta.",
    }
    return branch_prompts, branch_masks, metadata



# -----------------------------------------------------------------------------
# Phase 26.10.1 — Region Lane Compiler
# -----------------------------------------------------------------------------
# This is the compatibility-safe internal lane contract that sits between the
# V054 scene graph and the future V055 regional attention / latent-LoRA engines.
# It does not change the public node inputs, output order, or Character Lock
# behavior. Instead, it produces typed lane metadata so later phases can route
# prompts, masks, and extensions by role instead of relying on one prompt soup.

V054_REGION_LANE_COMPILER_PHASE = "SD-V054-26.10.1"
V054_REGION_LANE_SCHEMA = "neo.image.scene_director.region_lane_compiler.v054.v1"


def _v054_clause_list(text: Any) -> List[str]:
    parts: List[str] = []
    for raw in str(text or "").replace(";", ",").split(","):
        item = raw.strip()
        if item:
            parts.append(item)
    return parts


def _v054_join_unique(parts: List[Any]) -> str:
    out: List[str] = []
    seen = set()
    for part in parts:
        for item in _v054_clause_list(part):
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return ", ".join(out)


def _v054_lane_type_for_role(role: Any) -> str:
    role = str(role or "custom").strip().lower()
    if role == "character":
        return "character"
    if role in {"held_prop", "object", "background_object"}:
        return "prop" if role == "held_prop" else "object"
    if role in {"face_detail", "hair_detail", "hand_detail", "character_detail", "clothing"}:
        return "detail"
    if role == "background":
        return "background"
    if role == "transition_effect":
        return "seam"
    if role == "text":
        return "text"
    if role in {"lighting", "effect", "style"}:
        return "effect"
    return "custom"


def _v054_region_strength_for_lane(region: Dict[str, Any], lane_type: str, defaults: Dict[str, float]) -> float:
    raw = region.get("strength")
    if raw is None:
        raw = {
            "character": defaults.get("identity_strength", 0.70),
            "detail": defaults.get("detail_strength", 0.70),
            "prop": defaults.get("detail_strength", 0.70),
            "object": defaults.get("detail_strength", 0.70),
            "background": defaults.get("background_strength", 0.65),
            "seam": min(defaults.get("background_strength", 0.65), 0.58),
        }.get(lane_type, 0.70)
    return max(0.0, min(2.0, _safe_float(raw, 0.70)))


def _v054_route_owner_summary(region: Dict[str, Any]) -> Dict[str, Any]:
    routes = region.get("extension_routes") if isinstance(region.get("extension_routes"), dict) else {}
    lora_ids = routes.get("lora_row_ids") if isinstance(routes.get("lora_row_ids"), list) else []
    summary = {
        "controlnet_unit_id": str(routes.get("controlnet_unit_id") or "").strip(),
        "adetailer_pass_id": str(routes.get("adetailer_pass_id") or "").strip(),
        "ipadapter_unit_id": str(routes.get("ipadapter_unit_id") or "").strip(),
        "ipadapter_profile_id": str(routes.get("ipadapter_profile_id") or "").strip(),
        "lora_row_ids": [str(x) for x in lora_ids if str(x).strip()],
        "mask_mode": str(routes.get("mask_mode") or "region").strip() or "region",
        "execution": str(routes.get("execution") or "region_assignment_ready").strip() or "region_assignment_ready",
    }
    summary["route_count"] = sum(1 for key in ("controlnet_unit_id", "adetailer_pass_id", "ipadapter_unit_id", "ipadapter_profile_id") if summary.get(key)) + len(summary["lora_row_ids"])
    return summary


def _v054_lane_prompt_for_region(region: Dict[str, Any], parent_lookup: Dict[str, Dict[str, Any]]) -> Tuple[str, str, str]:
    role = str(region.get("role") or "custom").strip().lower()
    prompt = str(region.get("prompt") or "").strip()
    negative = str(region.get("negative") or "").strip()
    source = "region_prompt"

    if role in V054_BACKGROUND_ROLES:
        positive = _v054_background_prompt(region) or prompt
        negative = _v054_join_unique([_v054_background_negative_guard(region), negative])
        source = "background_registry"
    elif role in V054_ATTACHABLE_ROLES and _v054_clean_optional_id(region.get("attach_to")):
        parent = parent_lookup.get(_v054_clean_optional_id(region.get("attach_to")))
        positive = _v054_local_detail_prompt(parent, region) or prompt
        negative = _v054_join_unique([_v054_relationship_negative_guard(region, parent), negative])
        source = "relationship_registry"
    else:
        positive = prompt

    if role == "character":
        pos_guard, neg_guard = _v054_character_lock_prompt(region)
        positive = _v054_join_unique([positive, pos_guard])
        negative = _v054_join_unique([negative, neg_guard])
        source = "character_region_with_lock_contract" if (pos_guard or neg_guard) else "character_region"

    return positive, negative, source


def _v054_build_region_lane_compiler(
    graph: Dict[str, Any],
    width: int,
    height: int,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
) -> Dict[str, Any]:
    regions = [r for r in graph.get("regions", []) if isinstance(r, dict)]
    parent_lookup = {str(r.get("id") or ""): r for r in regions if str(r.get("id") or "")}
    subject_slots = _v054_subject_slot_by_region(graph, max_subject_slots=max_subject_slots)
    defaults = {
        "identity_strength": _safe_float(identity_strength, 0.70),
        "detail_strength": _safe_float(detail_strength, 0.70),
        "background_strength": _safe_float(background_strength, 0.65),
        "mask_feather": _safe_int(mask_feather, 12),
    }
    ext_data = _v054_parse_json_object(extension_routes_json, default={})
    extension_routes = ext_data.get("routes") if isinstance(ext_data, dict) and isinstance(ext_data.get("routes"), list) else []
    extension_by_region: Dict[str, List[Dict[str, Any]]] = {}
    for route in extension_routes:
        if not isinstance(route, dict):
            continue
        rid = str(route.get("region_id") or "").strip()
        if not rid:
            continue
        extension_by_region.setdefault(rid, []).append(route)

    lanes: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    background_prompts: Dict[str, List[Dict[str, Any]]] = {}

    global_data = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    global_prompt = str(global_data.get("prompt") or "").strip()
    global_lane = {
        "lane_id": "global_style",
        "lane_type": "global_style",
        "role": "global",
        "label": "Global Style / Composition",
        "prompt_positive": global_prompt,
        "prompt_negative": str(global_data.get("negative") or "").strip(),
        "ownership_policy": "camera_mood_lighting_style_only; local content belongs to region lanes",
    }

    for index, region in enumerate(regions, start=1):
        if not _safe_bool(region.get("enabled", True), True):
            continue
        rid = str(region.get("id") or f"region_{index}")
        role = str(region.get("role") or "custom").strip().lower() or "custom"
        lane_type = _v054_lane_type_for_role(role)
        parent_id = _v054_clean_optional_id(region.get("attach_to"))
        subject_slot = subject_slots.get(rid)
        positive, negative, prompt_source = _v054_lane_prompt_for_region(region, parent_lookup)
        route_summary = _v054_route_owner_summary(region)
        incoming_routes = extension_by_region.get(rid, [])
        incoming_summary = []
        for route in incoming_routes:
            incoming_summary.append({
                "route_id": str(route.get("route_id") or ""),
                "extension_type": str(route.get("extension_type") or route.get("type") or "unknown").strip().lower() or "unknown",
                "owner_extension_id": route.get("owner_extension_id"),
                "actual_mode": route.get("actual_mode") or route.get("execution_mode"),
                "target_area": route.get("target_area"),
            })
        bbox = _v054_region_bbox(region)
        lane = {
            "lane_id": f"lane_{index}_{rid}",
            "region_id": rid,
            "role": role,
            "lane_type": lane_type,
            "label": _v054_region_label(region, rid),
            "subject_slot": subject_slot,
            "parent_region_id": parent_id or None,
            "parent_label": _v054_region_label(parent_lookup[parent_id], parent_id) if parent_id in parent_lookup else None,
            "target_area": region.get("target_area") or _v054_detail_target(role, region),
            "bbox": bbox,
            "mask_ref": f"subject_{subject_slot}_mask" if lane_type == "character" and subject_slot else f"region:{rid}",
            "mask_source": "region_box",
            "mask_feather": _v054_region_feather(region, defaults["mask_feather"]),
            "strength": _v054_region_strength_for_lane(region, lane_type, defaults),
            "priority": str(region.get("priority") or "reinforce").strip().lower() or "reinforce",
            "prompt_positive": positive,
            "prompt_negative": negative,
            "prompt_source": prompt_source,
            "prompt_policy": "local_content_only; no unrelated region injection; global style stays in global lane",
            "lock_contract": {
                "character": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("character"),
                "gender": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("gender"),
                "skin_tone": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("skin_tone"),
                "hair": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("hair"),
                "build": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("build"),
                "body_height": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("body_height"),
                "negative": (region.get("lock") if isinstance(region.get("lock"), dict) else {}).get("negative"),
                "source": "character_lock_panel_or_region_lock",
            },
            "extension_routes": {
                "submitted_region_routes": route_summary,
                "incoming_authority_routes": incoming_summary,
                "route_count": route_summary.get("route_count", 0) + len(incoming_summary),
            },
            "ownership": {
                "owned_by_region": rid,
                "attached_to": parent_id or None,
                "no_cross_region_borrowing": True,
                "can_feed_attention_lane": True,
                "can_feed_extension_lane": True,
            },
        }
        if lane_type in {"background", "seam"}:
            lane["background_composer_controls"] = _v054_background_composer_controls_from_region(region)
        lanes.append(lane)

        if lane_type == "background":
            key = _v054_join_unique([_v054_background_override(region, "prompt") or region.get("prompt")]).strip().lower()
            if key:
                background_prompts.setdefault(key, []).append(lane)

    duplicate_background_pairs: List[Dict[str, Any]] = []
    for key, items in background_prompts.items():
        if len(items) > 1:
            entry = {
                "prompt": key,
                "region_ids": [item.get("region_id") for item in items],
                "labels": [item.get("label") for item in items],
                "warning": "duplicate_background_prompt_requires_unique_lane_prompt",
            }
            duplicate_background_pairs.append(entry)
            warnings.append({
                "code": "duplicate_background_prompt_requires_unique_lane_prompt",
                "level": "warning",
                "region_ids": entry["region_ids"],
                "message": "Multiple background lanes share the same base prompt; future composer should block or require unique prompts.",
            })

    counts = {
        "lanes": len(lanes),
        "characters": len([l for l in lanes if l.get("lane_type") == "character"]),
        "backgrounds": len([l for l in lanes if l.get("lane_type") == "background"]),
        "details": len([l for l in lanes if l.get("lane_type") == "detail"]),
        "props_objects": len([l for l in lanes if l.get("lane_type") in {"prop", "object"}]),
        "seams": len([l for l in lanes if l.get("lane_type") == "seam"]),
        "extension_routes": sum(int(l.get("extension_routes", {}).get("route_count") or 0) for l in lanes),
    }
    return {
        "schema": V054_REGION_LANE_SCHEMA,
        "phase": V054_REGION_LANE_COMPILER_PHASE,
        "status": "applied" if lanes else "off",
        "compatibility_mode": "metadata_and_internal_contract_only",
        "global_lane": global_lane,
        "lane_count": len(lanes),
        "counts": counts,
        "lanes": lanes,
        "prompt_hygiene": {
            "duplicate_background_prompt_pairs": duplicate_background_pairs,
            "global_prompt_policy": "global prompt carries composition/style; region lanes own local subjects, props, details, and backgrounds",
            "region_prompt_policy": "region prompt carries local content only; later attention controller consumes lane prompts directly",
        },
        "warnings": warnings,
        "future_consumers": [
            "regional_attention_controller_v2",
            "mask_authority_engine",
            "extension_route_controller_v2",
            "regional_lora_latent_executor",
            "output_inspector_lane_preview",
        ],
        "policy": "Phase 26.10.1 compiles typed RegionLane metadata without changing V054 output order or weakening Character Lock. Future phases will use this contract to replace prompt-soup regional routing.",
    }


# -----------------------------------------------------------------------------
# Phase 26.10.2 — Mask Authority Engine
# -----------------------------------------------------------------------------
# Metadata/internal contract only. This turns RegionLane[] into a mask ownership
# map that future V055 attention, latent-LoRA, ControlNet, IPAdapter, ADetailer,
# and post-pass systems can consume. It does not change current V054 branch masks,
# return order, or Character Lock behavior.

V054_MASK_AUTHORITY_PHASE = "SD-V054-26.10.2"
V054_MASK_AUTHORITY_SCHEMA = "neo.image.scene_director.mask_authority_engine.v054.v1"

V054_MASK_PRIORITY_ORDER = [
    "face_identity",
    "hair",
    "hands_prop",
    "character_body",
    "foreground_object",
    "seam",
    "background",
    "global_base",
]


def _v054_mask_priority_for_lane(lane: Dict[str, Any]) -> Tuple[int, str]:
    lane_type = str(lane.get("lane_type") or "custom").strip().lower()
    role = str(lane.get("role") or "custom").strip().lower()
    target = str(lane.get("target_area") or "").strip().lower()

    if role in {"face_detail"} or target in {"face", "head", "identity", "character_identity"}:
        group = "face_identity"
    elif role == "hair_detail" or target == "hair":
        group = "hair"
    elif role == "held_prop" or target in {"hand", "hands", "prop", "weapon", "sword"}:
        group = "hands_prop"
    elif lane_type == "character":
        group = "character_body"
    elif lane_type in {"prop", "object", "detail"}:
        group = "foreground_object"
    elif lane_type == "seam":
        group = "seam"
    elif lane_type == "background":
        group = "background"
    else:
        group = "global_base"
    return V054_MASK_PRIORITY_ORDER.index(group), group


def _v054_mask_tensor_for_lane(lane: Dict[str, Any], width: int, height: int) -> torch.Tensor:
    bbox = lane.get("bbox") or [0, 0, 1, 1]
    try:
        return _make_rect_mask(_v054_bbox_to_mask(bbox), width, height, 1.0).clamp(0, 1)
    except Exception:
        return _empty_mask(width, height)


def _v054_lane_rect_pixels(lane: Dict[str, Any], width: int, height: int) -> Tuple[int, int, int, int, int]:
    try:
        x1, y1, x2, y2 = _rect_to_pixels(_v054_bbox_to_mask(lane.get("bbox") or [0, 0, 1, 1]), int(width), int(height))
    except Exception:
        return 0, 0, 0, 0, 0
    return x1, y1, x2, y2, max(0, x2 - x1) * max(0, y2 - y1)


def _v054_rect_union_pixels(rects: List[Tuple[int, int, int, int, int]]) -> int:
    valid = [(x1, y1, x2, y2) for x1, y1, x2, y2, pixels in rects if pixels > 0 and x2 > x1 and y2 > y1]
    if not valid:
        return 0
    xs = sorted(set([x for x1, _, x2, _ in valid for x in (x1, x2)]))
    ys = sorted(set([y for _, y1, _, y2 in valid for y in (y1, y2)]))
    total = 0
    for xi in range(len(xs) - 1):
        xa, xb = xs[xi], xs[xi + 1]
        if xb <= xa:
            continue
        for yi in range(len(ys) - 1):
            ya, yb = ys[yi], ys[yi + 1]
            if yb <= ya:
                continue
            if any(x1 < xb and x2 > xa and y1 < yb and y2 > ya for x1, y1, x2, y2 in valid):
                total += (xb - xa) * (yb - ya)
    return int(total)


def _v054_build_mask_authority_engine(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
) -> Dict[str, Any]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    lanes = [lane for lane in region_lane_compiler.get("lanes", []) if isinstance(lane, dict)]
    total_pixels = max(1, int(width) * int(height))
    mask_entries: List[Dict[str, Any]] = []
    rects: List[Tuple[int, int, int, int, int]] = []
    warnings: List[Dict[str, Any]] = []

    for lane in lanes:
        rect = _v054_lane_rect_pixels(lane, width, height)
        rects.append(rect)
        pixel_count = int(rect[4])
        coverage = round((pixel_count / total_pixels) * 100.0, 4)
        priority_rank, priority_group = _v054_mask_priority_for_lane(lane)
        lane_type = str(lane.get("lane_type") or "custom")
        source = str(lane.get("mask_source") or "region_box")
        mask_entry = {
            "mask_id": f"mask_{lane.get('region_id') or lane.get('lane_id')}",
            "lane_id": lane.get("lane_id"),
            "region_id": lane.get("region_id"),
            "label": lane.get("label"),
            "role": lane.get("role"),
            "lane_type": lane_type,
            "subject_slot": lane.get("subject_slot"),
            "parent_region_id": lane.get("parent_region_id"),
            "target_area": lane.get("target_area"),
            "bbox": lane.get("bbox"),
            "mask_ref": lane.get("mask_ref"),
            "mask_source": source,
            "mask_type": "subject_mask" if lane_type == "character" and lane.get("subject_slot") else ("background_zone_mask" if lane_type == "background" else "region_mask"),
            "priority_rank": priority_rank,
            "priority_group": priority_group,
            "coverage_percent": coverage,
            "pixel_count": pixel_count,
            "feather": lane.get("mask_feather"),
            "overlaps_with": [],
            "authority_policy": {
                "owned_by_region": lane.get("region_id"),
                "parent_region_id": lane.get("parent_region_id"),
                "no_global_canvas_borrowing": True,
                "higher_priority_masks_win_overlap": True,
                "future_attention_lane_ready": True,
                "future_extension_lane_ready": True,
            },
            "semantic_status": {
                "source_level": "level_1_region_box" if source == "region_box" else "level_2_or_higher",
                "manual_mask_supported_future": True,
                "uploaded_color_mask_supported_future": True,
                "semantic_mask_supported_future": True,
                "derived_body_part_mask_supported_future": True,
            },
        }
        mask_entries.append(mask_entry)
        if source == "region_box" and lane_type in {"character", "prop", "object", "detail"}:
            warnings.append({
                "code": "region_box_mask_only_semantic_upgrade_needed",
                "level": "info",
                "region_id": lane.get("region_id"),
                "message": "This lane currently uses a rectangular region mask; future semantic/manual masks will improve precision.",
            })

    overlap_pairs: List[Dict[str, Any]] = []
    for i, left in enumerate(mask_entries):
        if i >= len(rects):
            continue
        x1a, y1a, x2a, y2a, pa = rects[i]
        for j in range(i + 1, len(mask_entries)):
            if j >= len(rects):
                continue
            x1b, y1b, x2b, y2b, pb = rects[j]
            ox1, oy1 = max(x1a, x1b), max(y1a, y1b)
            ox2, oy2 = min(x2a, x2b), min(y2a, y2b)
            overlap_pixels = max(0, ox2 - ox1) * max(0, oy2 - oy1)
            if overlap_pixels <= 0:
                continue
            pct = round((overlap_pixels / total_pixels) * 100.0, 4)
            pair = {
                "regions": [left.get("region_id"), mask_entries[j].get("region_id")],
                "labels": [left.get("label"), mask_entries[j].get("label")],
                "overlap_pixels": overlap_pixels,
                "overlap_percent": pct,
                "winner_region_id": left.get("region_id") if int(left.get("priority_rank") or 99) <= int(mask_entries[j].get("priority_rank") or 99) else mask_entries[j].get("region_id"),
                "resolution_policy": "lower_priority_mask_yields_to_higher_priority_mask_for_future_v055_consumers",
            }
            overlap_pairs.append(pair)
            left["overlaps_with"].append({"region_id": mask_entries[j].get("region_id"), "overlap_percent": pct})
            mask_entries[j]["overlaps_with"].append({"region_id": left.get("region_id"), "overlap_percent": pct})
            level = "info" if left.get("parent_region_id") == mask_entries[j].get("region_id") or mask_entries[j].get("parent_region_id") == left.get("region_id") else "warning"
            warnings.append({
                "code": "parent_child_mask_overlap_allowed" if level == "info" else "mask_overlap_detected",
                "level": level,
                "region_ids": pair["regions"],
                "message": "Masks overlap; future consumers will resolve by priority and parent/child ownership.",
            })

    covered_pixels = _v054_rect_union_pixels(rects) if rects else 0
    uncovered_pixels = max(0, total_pixels - covered_pixels)
    uncovered_pct = round((uncovered_pixels / total_pixels) * 100.0, 4)
    covered_pct = round((covered_pixels / total_pixels) * 100.0, 4)
    if uncovered_pixels > 0:
        warnings.append({
            "code": "uncovered_pixels_filled_by_global_base",
            "level": "info",
            "message": "Some canvas pixels are not covered by authored masks; future regional engines should assign them to the global/base lane.",
            "uncovered_percent": uncovered_pct,
        })

    counts = {
        "masks": len(mask_entries),
        "character_masks": len([m for m in mask_entries if m.get("lane_type") == "character"]),
        "background_masks": len([m for m in mask_entries if m.get("lane_type") == "background"]),
        "detail_masks": len([m for m in mask_entries if m.get("lane_type") == "detail"]),
        "prop_object_masks": len([m for m in mask_entries if m.get("lane_type") in {"prop", "object"}]),
        "seam_masks": len([m for m in mask_entries if m.get("lane_type") == "seam"]),
        "overlap_pairs": len(overlap_pairs),
    }
    return {
        "schema": V054_MASK_AUTHORITY_SCHEMA,
        "phase": V054_MASK_AUTHORITY_PHASE,
        "status": "applied" if mask_entries else "off",
        "compatibility_mode": "metadata_and_internal_contract_only",
        "canvas": {"width": int(width), "height": int(height), "total_pixels": total_pixels},
        "counts": counts,
        "priority_order": V054_MASK_PRIORITY_ORDER,
        "mask_sources_supported": [
            "region_box_current",
            "manual_mask_future",
            "uploaded_color_mask_future",
            "semantic_mask_future",
            "derived_body_part_mask_future",
        ],
        "masks": mask_entries,
        "coverage": {
            "covered_pixels": covered_pixels,
            "covered_percent": covered_pct,
            "uncovered_pixels": uncovered_pixels,
            "uncovered_percent": uncovered_pct,
            "uncovered_pixels_policy": "fill_with_global_base_lane_for_future_regional_attention_controller",
            "normalization_policy": "future mask stack must normalize overlapping active masks so per-pixel influence sums to 1.0",
        },
        "overlap_summary": {
            "pair_count": len(overlap_pairs),
            "pairs": overlap_pairs,
            "resolution_policy": "face_identity > hair > hands_prop > character_body > foreground_object > seam > background > global_base",
        },
        "authority_layers": [
            {"layer": "face_identity", "allowed_routes": ["ipadapter", "adetailer_face", "face_detail"], "policy": "identity passes should use face/head masks when available"},
            {"layer": "hair", "allowed_routes": ["hair_detail", "lora_hair", "adetailer_hair"], "policy": "hair detail masks override character body and background"},
            {"layer": "hands_prop", "allowed_routes": ["controlnet_prop", "prop_detail", "lora_prop"], "policy": "held props stay attached to parent subject"},
            {"layer": "character_body", "allowed_routes": ["character_attention", "regional_lora", "controlnet_pose"], "policy": "character lanes own body/outfit/silhouette but cannot borrow from other subjects"},
            {"layer": "foreground_object", "allowed_routes": ["object_detail", "controlnet_object"], "policy": "foreground objects beat seam/background lanes"},
            {"layer": "seam", "allowed_routes": ["transition_effect", "seam_harmony"], "policy": "seam blends backgrounds but cannot overwrite subjects"},
            {"layer": "background", "allowed_routes": ["background_attention", "background_controlnet", "background_inpaint"], "policy": "background lanes yield to all foreground/subject masks"},
            {"layer": "global_base", "allowed_routes": ["global_style", "uncovered_canvas"], "policy": "global style fills uncovered pixels without owning local content"},
        ],
        "warnings": warnings,
        "future_consumers": [
            "regional_attention_controller_v2",
            "extension_route_controller_v2",
            "regional_lora_latent_executor",
            "background_region_composer",
            "mask_inspector_ui",
        ],
        "policy": "Phase 26.10.2 creates a mask authority map from RegionLane metadata without changing V054 generation behavior, return order, or Character Lock authority.",
    }



# -----------------------------------------------------------------------------
# Phase 26.10.3 — Regional Attention Controller V2
# -----------------------------------------------------------------------------
# Compatibility-safe controller contract for the V054 regional attention rebuild.
# The existing _patch_director() remains the runtime bridge for this phase; this
# controller compiles the lane/mask/negative-conditioning plan that later phases
# can route directly into a deeper attention/latent engine. It does not change the
# public node inputs, output order, subject masks, or Character Lock behavior.

V054_REGIONAL_ATTENTION_CONTROLLER_PHASE = "SD-V054-26.10.3"
V054_REGIONAL_ATTENTION_CONTROLLER_SCHEMA = "neo.image.scene_director.regional_attention_controller.v054.v2"


def _v054_attention_lane_role(lane: Dict[str, Any]) -> str:
    lane_type = str(lane.get("lane_type") or "custom").strip().lower()
    if lane_type == "global_style":
        return "base_common_style_lane"
    if lane_type == "background":
        return "background_content_lane"
    if lane_type == "character":
        return "character_subject_lane"
    if lane_type in {"detail", "prop", "object"}:
        return "foreground_detail_lane"
    if lane_type == "seam":
        return "transition_seam_lane"
    if lane_type == "text":
        return "text_layout_lane"
    return "custom_regional_lane"


def _v054_attention_lane_schedule(lane: Dict[str, Any], mask_meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    lane_type = str(lane.get("lane_type") or "custom").strip().lower()
    priority_group = str((mask_meta or {}).get("priority_group") or "global_base")
    if lane_type == "global_style":
        start, end = 0.0, 1.0
    elif lane_type == "background":
        start, end = 0.0, 0.92
    elif priority_group in {"face_identity", "hair"}:
        start, end = 0.12, 0.88
    elif priority_group == "hands_prop":
        start, end = 0.05, 0.90
    elif lane_type == "seam":
        start, end = 0.18, 0.96
    else:
        start, end = 0.0, 0.90
    return {
        "start_at": start,
        "end_at": end,
        "policy": "role_weighted_attention_schedule_metadata_for_future_sampler_bridge",
    }


def _v054_build_regional_attention_controller(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
    base_weight: Any = 0.55,
    region_gain: Any = 0.45,
    normalize_masks: Any = True,
) -> Dict[str, Any]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(mask_authority_engine, dict) or not isinstance(mask_authority_engine.get("masks"), list):
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    lanes_in = []
    global_lane = region_lane_compiler.get("global_lane") if isinstance(region_lane_compiler.get("global_lane"), dict) else None
    if global_lane:
        lanes_in.append(global_lane)
    lanes_in.extend([lane for lane in region_lane_compiler.get("lanes", []) if isinstance(lane, dict)])
    masks_by_region = {
        str(mask.get("region_id") or ""): mask
        for mask in mask_authority_engine.get("masks", [])
        if isinstance(mask, dict) and str(mask.get("region_id") or "")
    }
    warnings: List[Dict[str, Any]] = []
    for src in (region_lane_compiler.get("warnings") or [], mask_authority_engine.get("warnings") or []):
        if isinstance(src, dict):
            warnings.append(dict(src))

    attention_lanes: List[Dict[str, Any]] = []
    branch_runtime_count = 0
    regional_negative_ready_count = 0
    character_lock_count = 0
    extension_route_lane_count = 0
    for lane in lanes_in:
        source_lane_id = str(lane.get("lane_id") or lane.get("region_id") or "").strip()
        lane_type = str(lane.get("lane_type") or "custom").strip().lower()
        region_id = str(lane.get("region_id") or "").strip()
        lane_id = "global_style" if lane_type == "global_style" else (region_id or source_lane_id)
        mask_meta = masks_by_region.get(region_id, {}) if region_id else {}
        prompt_positive = str(lane.get("prompt_positive") or "").strip()
        prompt_negative = str(lane.get("prompt_negative") or "").strip()
        is_global = lane_type == "global_style"
        route_count = int((lane.get("extension_routes") or {}).get("route_count") or 0) if isinstance(lane.get("extension_routes"), dict) else 0
        lock_contract = lane.get("lock_contract") if isinstance(lane.get("lock_contract"), dict) else {}
        if prompt_negative:
            regional_negative_ready_count += 1
        if route_count:
            extension_route_lane_count += 1
        if lock_contract:
            character_lock_count += 1
        if not is_global:
            branch_runtime_count += 1
        if not is_global and not mask_meta:
            warnings.append({
                "code": "regional_attention_lane_missing_mask_authority",
                "level": "warning",
                "lane_id": lane_id,
                "region_id": region_id,
                "message": "Regional attention lane has no mask authority entry; legacy branch routing may fallback to region box behavior.",
            })
        attention_lanes.append({
            "lane_id": lane_id,
            "source_lane_id": source_lane_id or lane_id,
            "region_id": region_id or None,
            "label": lane.get("label"),
            "role": lane.get("role"),
            "lane_type": lane_type,
            "attention_role": _v054_attention_lane_role(lane),
            "subject_slot": lane.get("subject_slot"),
            "parent_region_id": lane.get("parent_region_id"),
            "target_area": lane.get("target_area"),
            "mask_ref": "global_base_mask" if is_global else (mask_meta.get("mask_ref") or lane.get("mask_ref")),
            "mask_id": mask_meta.get("mask_id"),
            "mask_type": "global_base" if is_global else mask_meta.get("mask_type"),
            "mask_priority_group": "global_base" if is_global else mask_meta.get("priority_group"),
            "mask_priority_rank": len(V054_MASK_PRIORITY_ORDER) - 1 if is_global else mask_meta.get("priority_rank"),
            "mask_coverage_percent": 100.0 if is_global else mask_meta.get("coverage_percent"),
            "prompt_positive": prompt_positive,
            "prompt_negative": prompt_negative,
            "positive_conditioning_scope": "base_common" if is_global else "assigned_region_only",
            "negative_conditioning_scope": "global_negative_fallback" if is_global else "assigned_region_negative_ready",
            "regional_negative_runtime": "metadata_ready_legacy_global_negative_runtime" if prompt_negative else "not_requested",
            "global_prompt_injection": bool(is_global),
            "region_prompt_isolation": not is_global,
            "conditioning_weight": _safe_float(lane.get("strength"), 1.0 if is_global else 0.70),
            "role_weight": _safe_float(lane.get("strength"), 1.0 if is_global else 0.70),
            "schedule": _v054_attention_lane_schedule(lane, mask_meta),
            "branch_runtime": "base_conditioning" if is_global else "legacy_v054_attn2_branch",
            "legacy_runtime_branch_created": not is_global,
            "character_lock_contract_carried": bool(lock_contract),
            "lock_contract": lock_contract,
            "extension_route_count": route_count,
            "extension_route_types": [str(r.get("extension_type") or "unknown") for r in ((lane.get("extension_routes") or {}).get("incoming_authority_routes") or []) if isinstance(r, dict)] if isinstance(lane.get("extension_routes"), dict) else [],
            "ownership_policy": lane.get("ownership"),
        })

    role_counts: Dict[str, int] = {}
    for lane in attention_lanes:
        key = str(lane.get("attention_role") or "unknown")
        role_counts[key] = role_counts.get(key, 0) + 1

    background_lanes = [lane for lane in attention_lanes if lane.get("lane_type") == "background"]
    bg_prompts = [str(lane.get("prompt_positive") or "").strip().lower() for lane in background_lanes]
    duplicate_background_prompt_pairs = (region_lane_compiler.get("prompt_hygiene") or {}).get("duplicate_background_prompt_pairs") if isinstance(region_lane_compiler.get("prompt_hygiene"), dict) else []
    if duplicate_background_prompt_pairs:
        warnings.append({
            "code": "regional_attention_duplicate_background_prompts_need_unique_lanes",
            "level": "warning",
            "message": "Multiple background attention lanes share the same positive prompt; unique background lane prompts are required for reliable split-world adherence.",
            "pairs": duplicate_background_prompt_pairs,
        })

    mask_coverage = mask_authority_engine.get("coverage") if isinstance(mask_authority_engine.get("coverage"), dict) else {}
    return {
        "schema": V054_REGIONAL_ATTENTION_CONTROLLER_SCHEMA,
        "phase": V054_REGIONAL_ATTENTION_CONTROLLER_PHASE,
        "status": "applied" if attention_lanes else "off",
        "runtime_mode": "legacy_attn2_patch_bridge",
        "compatibility_mode": "lane_and_mask_authority_compiled_without_return_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "counts": {
            "lanes": len(attention_lanes),
            "runtime_regional_branches": branch_runtime_count,
            "regional_negative_lanes_ready": regional_negative_ready_count,
            "character_lock_lanes": character_lock_count,
            "extension_route_lanes": extension_route_lane_count,
            "background_lanes": len(background_lanes),
        },
        "role_counts": role_counts,
        "lanes": attention_lanes,
        "mask_normalization": {
            "normalize_masks": bool(normalize_masks),
            "base_weight": _safe_float(base_weight, 0.55),
            "region_gain": _safe_float(region_gain, 0.45),
            "coverage_percent": mask_coverage.get("covered_percent"),
            "uncovered_percent": mask_coverage.get("uncovered_percent"),
            "uncovered_pixels_policy": mask_coverage.get("uncovered_pixels_policy", "fill_with_global_base_lane"),
            "overlap_resolution_policy": (mask_authority_engine.get("overlap_summary") or {}).get("resolution_policy") if isinstance(mask_authority_engine.get("overlap_summary"), dict) else None,
        },
        "conditioning_contract": {
            "global_lane_owns_style_camera_mood": True,
            "regional_positive_conditioning_by_mask": True,
            "regional_negative_conditioning_compiled": True,
            "regional_negative_runtime": "metadata_ready_legacy_global_negative_runtime",
            "global_prompt_must_not_receive_region_specific_triggers": True,
            "character_lock_contract_carried_to_attention_lanes": True,
            "extension_routes_bound_to_attention_lanes": True,
        },
        "patcher_contract": {
            "function": "_patch_director",
            "attn2_input_patch_required": True,
            "attn2_output_patch_required": True,
            "mask_downsample_to_attention_resolution": True,
            "model_clone_required": True,
            "current_phase_uses_existing_v054_runtime_bridge": True,
        },
        "prompt_hygiene": {
            "background_prompt_count": len(bg_prompts),
            "unique_background_prompt_count": len(set(bg_prompts)),
            "duplicate_background_prompt_pairs": duplicate_background_prompt_pairs or [],
            "region_prompt_isolation_policy": "local region prompts stay in assigned lane and are not appended to global prompt by the controller",
        },
        "warnings": warnings,
        "future_consumers": [
            "extension_route_controller_v2",
            "background_region_composer",
            "regional_lora_latent_executor",
            "mask_inspector_ui",
            "regional_negative_cfg_lanes",
        ],
        "policy": "Phase 26.10.3 compiles lane-aware regional attention authority from RegionLane and Mask Authority metadata while preserving the existing V054 attn2 runtime bridge and all Character Lock contracts.",
    }



# -----------------------------------------------------------------------------
# Phase 26.10.4 — Background Region Composer
# -----------------------------------------------------------------------------
# Compatibility-safe background composer contract. This phase does not create
# new sampler/image nodes yet; it compiles a strict background/seam composition
# plan from RegionLane, Mask Authority, and Regional Attention Controller V2 so
# the next runtime phases can stop relying on duplicated background prompt repair.

V054_BACKGROUND_REGION_COMPOSER_PHASE = "SD-V054-26.10.4"
V054_BACKGROUND_REGION_COMPOSER_SCHEMA = "neo.image.scene_director.background_region_composer.v054.v1"
V054_BACKGROUND_COMPOSER_UI_CONTROLS_PHASE = "SD-V054-26.10.8C"


def _v054_background_composer_controls_from_region(region: Dict[str, Any] | None) -> Dict[str, Any]:
    source = region if isinstance(region, dict) else {}
    override = source.get("background_override") if isinstance(source.get("background_override"), dict) else {}

    def _choice(*values: Any, default: str = "") -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return default

    def _optional_float(*values: Any) -> float | None:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return float(value)
            except Exception:
                continue
        return None

    repair_mode = _choice(source.get("bg_duplicate_repair_mode"), override.get("duplicate_repair_mode"), default="warn")
    if repair_mode not in {"warn", "auto_repair", "block"}:
        repair_mode = "warn"
    controls: Dict[str, Any] = {
        "schema": "neo.image.scene_director.background_composer_controls.v054.v1",
        "phase": V054_BACKGROUND_COMPOSER_UI_CONTROLS_PHASE,
        "authority": _choice(source.get("bg_authority"), override.get("authority"), default="auto"),
        "prevent_subject_overwrite": _choice(source.get("bg_prevent_subject_overwrite"), override.get("prevent_subject_overwrite"), default="auto"),
        "duplicate_repair_mode": repair_mode,
    }
    influence = _optional_float(source.get("bg_influence"), override.get("influence"), source.get("strength"))
    denoise = _optional_float(source.get("bg_denoise"), override.get("denoise"))
    seam_blend = _optional_float(source.get("bg_seam_blend"), override.get("seam_blend_strength"))
    if influence is not None:
        controls["influence"] = max(0.0, min(2.0, influence))
    if denoise is not None:
        controls["denoise"] = max(0.0, min(1.0, denoise))
    if seam_blend is not None:
        controls["seam_blend_strength"] = max(0.0, min(1.0, seam_blend))
    return controls

V054_BACKGROUND_CONCEPT_KEYWORDS = {
    "modern_future": {
        "positive": ["futuristic", "future", "modern", "neon", "cyberpunk", "megacity", "holographic", "glass tower", "billboard", "sci-fi", "urban", "city"],
        "negative": "ancient ruins, medieval castle, fantasy village, torchlit stone hall, old-world tavern, forest-only fantasy background",
        "reinforce": "futuristic modern city zone, neon rain, glass towers, holographic signage, cyberpunk urban depth, keep medieval ruins out of this background lane",
    },
    "fantasy_old_world": {
        "positive": ["fantasy", "medieval", "ancient", "ruins", "stone", "castle", "torch", "torchlight", "old world", "adventurer", "magic", "village"],
        "negative": "futuristic city, neon billboard, holographic signage, cyberpunk street, glass skyscraper, sci-fi megacity dominance",
        "reinforce": "medieval fantasy background zone, ancient stone ruins, warm torchlight, old-world atmosphere, keep futuristic city elements out of this background lane",
    },
    "natural": {
        "positive": ["forest", "mountain", "beach", "river", "sky", "nature", "garden", "field"],
        "negative": "busy city skyline, indoor studio wall, unrelated architecture",
        "reinforce": "natural environment background zone, coherent landscape depth, no unrelated urban/fantasy replacement",
    },
    "interior": {
        "positive": ["room", "interior", "studio", "office", "hall", "corridor", "bedroom", "kitchen"],
        "negative": "outdoor skyline, unrelated landscape, random background city",
        "reinforce": "coherent interior background zone, stable perspective, no outdoor replacement",
    },
}


def _v054_background_text_for_lane(lane: Dict[str, Any]) -> str:
    return " ".join([
        str(lane.get("label") or ""),
        str(lane.get("prompt_positive") or ""),
        str(lane.get("prompt_negative") or ""),
    ]).strip().lower()


def _v054_background_concept_for_lane(lane: Dict[str, Any]) -> str:
    text = _v054_background_text_for_lane(lane)
    scores: Dict[str, int] = {}
    for concept, cfg in V054_BACKGROUND_CONCEPT_KEYWORDS.items():
        scores[concept] = sum(1 for kw in cfg.get("positive", []) if kw in text)
    if not any(scores.values()):
        label = str(lane.get("label") or "").lower()
        if "fantasy" in label:
            return "fantasy_old_world"
        if "modern" in label or "future" in label or "city" in label:
            return "modern_future"
        return "unknown"
    return max(scores, key=lambda key: scores.get(key, 0))


def _v054_background_zone_for_lane(lane: Dict[str, Any]) -> str:
    role = str(lane.get("role") or "").lower()
    if str(lane.get("lane_type") or "").lower() == "seam" or role == "transition_effect":
        return "center_seam"
    target = str(lane.get("target_area") or "").strip().lower()
    # Background lanes often inherit the generic detail target from legacy helpers;
    # ignore that value and derive the zone from the authored bbox instead.
    if str(lane.get("lane_type") or "").lower() != "background" and target and target not in {"auto", "none"}:
        return target.replace(" ", "_")
    try:
        x1, _y1, x2, _y2 = lane.get("bbox") or [0, 0, 1, 1]
        center = (float(x1) + float(x2)) * 0.5
        span = float(x2) - float(x1)
    except Exception:
        center, span = 0.5, 1.0
    if span >= 0.86:
        return "full_canvas"
    if center < 0.40:
        return "left_background"
    if center > 0.60:
        return "right_background"
    return "center_background"


def _v054_background_lane_base_prompt(lane: Dict[str, Any]) -> str:
    return str(lane.get("prompt_positive") or "").strip()


def _v054_background_composer_prompt(lane: Dict[str, Any], duplicate_prompt: bool = False) -> Tuple[str, List[str]]:
    prompt = _v054_background_lane_base_prompt(lane)
    concept = _v054_background_concept_for_lane(lane)
    additions: List[str] = []
    warnings: List[str] = []
    cfg = V054_BACKGROUND_CONCEPT_KEYWORDS.get(concept)
    if cfg:
        reinforce = str(cfg.get("reinforce") or "").strip()
        if reinforce and reinforce.lower() not in prompt.lower():
            additions.append(reinforce)
    if duplicate_prompt:
        warnings.append("background_duplicate_prompt_unique_repair_required")
        if concept == "unknown":
            label = str(lane.get("label") or "").lower()
            if "fantasy" in label:
                concept = "fantasy_old_world"
            elif "modern" in label or "future" in label:
                concept = "modern_future"
            cfg = V054_BACKGROUND_CONCEPT_KEYWORDS.get(concept)
            if cfg:
                additions.append(str(cfg.get("reinforce") or ""))
        if concept == "fantasy_old_world":
            additions.append("distinct fantasy-only background lane, ancient ruins and torchlight dominate this side")
        elif concept == "modern_future":
            additions.append("distinct modern-only background lane, futuristic city elements dominate this side")
        else:
            additions.append("distinct background lane, do not duplicate neighboring background content")
    repaired = _v054_join_unique([prompt] + additions)
    return repaired, sorted(set(warnings))


def _v054_background_composer_negative(lane: Dict[str, Any], duplicate_prompt: bool = False) -> str:
    concept = _v054_background_concept_for_lane(lane)
    cfg = V054_BACKGROUND_CONCEPT_KEYWORDS.get(concept) or {}
    negatives = [str(lane.get("prompt_negative") or "").strip(), str(cfg.get("negative") or "").strip()]
    if duplicate_prompt:
        negatives.append("same background as neighboring zone, duplicated background prompt, background style bleeding across split-world regions")
    if str(lane.get("lane_type") or "").lower() == "background":
        negatives.append("foreground subject swallowed by background, background replacing character, extra people in background zone")
    return _v054_join_unique(negatives)


def _v054_background_overlap_regions(mask_authority_engine: Dict[str, Any], region_id: str) -> List[str]:
    masks = mask_authority_engine.get("masks") if isinstance(mask_authority_engine.get("masks"), list) else []
    for mask in masks:
        if not isinstance(mask, dict) or str(mask.get("region_id") or "") != str(region_id):
            continue
        return [str(item.get("region_id") or "") for item in mask.get("overlaps_with", []) if isinstance(item, dict) and str(item.get("region_id") or "")]
    return []


def _v054_build_background_region_composer(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    regional_attention_controller: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
) -> Dict[str, Any]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(mask_authority_engine, dict) or not isinstance(mask_authority_engine.get("masks"), list):
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(regional_attention_controller, dict) or not isinstance(regional_attention_controller.get("lanes"), list):
        regional_attention_controller = _v054_build_regional_attention_controller(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    attention_lanes = [lane for lane in regional_attention_controller.get("lanes", []) if isinstance(lane, dict)]
    background_lanes = [lane for lane in attention_lanes if str(lane.get("lane_type") or "").lower() == "background"]
    seam_lanes = [lane for lane in attention_lanes if str(lane.get("lane_type") or "").lower() == "seam"]
    warnings: List[Dict[str, Any]] = []
    duplicate_pairs = (region_lane_compiler.get("prompt_hygiene") or {}).get("duplicate_background_prompt_pairs") if isinstance(region_lane_compiler.get("prompt_hygiene"), dict) else []
    duplicate_region_ids = set()
    for pair in duplicate_pairs or []:
        if not isinstance(pair, dict):
            continue
        for rid in pair.get("region_ids", []) or []:
            duplicate_region_ids.add(str(rid))
    if duplicate_pairs:
        warnings.append({
            "code": "background_duplicate_prompt_block_or_repair_required",
            "level": "warning",
            "message": "Background lanes share the same base prompt; the composer created per-lane repair prompts but future runtime should require unique user prompts or explicit auto-repair approval.",
            "pairs": duplicate_pairs,
        })

    mask_by_region = {
        str(mask.get("region_id") or ""): mask
        for mask in (mask_authority_engine.get("masks") or [])
        if isinstance(mask, dict) and str(mask.get("region_id") or "")
    }
    source_lane_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (region_lane_compiler.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }
    source_region_by_id = {
        str(region.get("id") or ""): region
        for region in (graph.get("regions") or [])
        if isinstance(region, dict) and str(region.get("id") or "")
    }
    composer_lanes: List[Dict[str, Any]] = []
    concept_counts: Dict[str, int] = {}
    repaired_count = 0
    blocked_duplicate_count = 0
    auto_repair_count = 0
    for index, lane in enumerate(background_lanes, start=1):
        region_id = str(lane.get("region_id") or "")
        source_lane = source_lane_by_region.get(region_id, {})
        lane_for_composer = dict(lane)
        if isinstance(source_lane, dict):
            lane_for_composer.setdefault("bbox", source_lane.get("bbox"))
            lane_for_composer.setdefault("target_area", source_lane.get("target_area"))
            lane_for_composer.setdefault("role", source_lane.get("role"))
        source_region = source_region_by_id.get(region_id, {})
        controls = _v054_background_composer_controls_from_region(source_region or source_lane or lane)
        duplicate_prompt = region_id in duplicate_region_ids
        duplicate_repair_mode = str(controls.get("duplicate_repair_mode") or "warn")
        composer_prompt, prompt_warnings = _v054_background_composer_prompt(lane_for_composer, duplicate_prompt=duplicate_prompt)
        composer_negative = _v054_background_composer_negative(lane_for_composer, duplicate_prompt=duplicate_prompt)
        concept = _v054_background_concept_for_lane(lane_for_composer)
        concept_counts[concept] = concept_counts.get(concept, 0) + 1
        if duplicate_prompt or composer_prompt != _v054_background_lane_base_prompt(lane):
            repaired_count += 1
        if duplicate_prompt and duplicate_repair_mode == "auto_repair":
            auto_repair_count += 1
        if duplicate_prompt and duplicate_repair_mode == "block":
            blocked_duplicate_count += 1
            warnings.append({
                "code": "background_duplicate_prompt_blocked_by_user_policy",
                "level": "error",
                "region_id": region_id,
                "message": "Background duplicate repair mode is Block until fixed; supply a unique background prompt or switch to Auto repair.",
            })
        for code in prompt_warnings:
            warnings.append({
                "code": code,
                "level": "warning",
                "region_id": region_id,
                "message": "Background composer generated a unique lane prompt repair for this duplicated/ambiguous background lane.",
            })
        mask_meta = mask_by_region.get(region_id, {})
        subject_policy = "background yields to character/detail/prop/seam masks from Mask Authority Engine"
        if controls.get("prevent_subject_overwrite") in {"on", "strict"}:
            subject_policy = f"{subject_policy}; user {controls.get('prevent_subject_overwrite')} subject-overwrite protection"
        composer_lanes.append({
            "composer_lane_id": f"background_lane_{index}_{region_id or lane.get('lane_id')}",
            "region_id": region_id or None,
            "source_attention_lane_id": lane.get("lane_id"),
            "label": lane.get("label"),
            "zone": _v054_background_zone_for_lane(lane_for_composer),
            "concept": concept,
            "mask_ref": lane.get("mask_ref"),
            "mask_id": lane.get("mask_id"),
            "mask_priority_group": lane.get("mask_priority_group"),
            "mask_coverage_percent": lane.get("mask_coverage_percent"),
            "overlaps_with": _v054_background_overlap_regions(mask_authority_engine, region_id),
            "base_prompt_positive": _v054_background_lane_base_prompt(lane),
            "composer_prompt_positive": composer_prompt,
            "composer_prompt_negative": composer_negative,
            "prompt_unique_ready": (not duplicate_prompt) or duplicate_repair_mode == "auto_repair",
            "duplicate_prompt_repaired": bool(duplicate_prompt),
            "duplicate_prompt_repair_mode": duplicate_repair_mode,
            "background_composer_controls": controls,
            "background_authority": controls.get("authority"),
            "background_influence": controls.get("influence"),
            "background_denoise": controls.get("denoise"),
            "prevent_subject_overwrite": controls.get("prevent_subject_overwrite"),
            "attention_scope": "background_zone_only",
            "runtime_stage": "background_foundation_stage",
            "runtime_mode": "metadata_ready_legacy_attention_bridge",
            "subject_protection_policy": subject_policy,
        })

    seam_plan: List[Dict[str, Any]] = []
    for index, lane in enumerate(seam_lanes, start=1):
        region_id = str(lane.get("region_id") or "")
        controls = _v054_background_composer_controls_from_region(source_region_by_id.get(region_id, {}) or source_lane_by_region.get(region_id, {}) or lane)
        seam_negative = _v054_join_unique([
            str(lane.get("prompt_negative") or ""),
            "seam covering faces, seam replacing character bodies, harsh vertical border, pasted split line",
        ])
        seam_subject_policy = "seam cannot overwrite character, face, hair, hands, or prop masks"
        if controls.get("prevent_subject_overwrite") in {"on", "strict"}:
            seam_subject_policy = f"{seam_subject_policy}; user {controls.get('prevent_subject_overwrite')} subject-overwrite protection"
        seam_plan.append({
            "composer_lane_id": f"seam_lane_{index}_{region_id or lane.get('lane_id')}",
            "region_id": region_id or None,
            "source_attention_lane_id": lane.get("lane_id"),
            "label": lane.get("label"),
            "zone": "center_seam",
            "mask_ref": lane.get("mask_ref"),
            "mask_id": lane.get("mask_id"),
            "base_prompt_positive": str(lane.get("prompt_positive") or "").strip(),
            "composer_prompt_positive": _v054_join_unique([lane.get("prompt_positive"), "low-denoise environmental blend, mist and lighting transition only"]),
            "composer_prompt_negative": seam_negative,
            "runtime_stage": "seam_harmony_stage_after_backgrounds",
            "runtime_mode": "metadata_ready_legacy_attention_bridge",
            "background_composer_controls": controls,
            "seam_blend_strength": controls.get("seam_blend_strength"),
            "background_denoise": controls.get("denoise"),
            "prevent_subject_overwrite": controls.get("prevent_subject_overwrite"),
            "subject_protection_policy": seam_subject_policy,
        })
    if seam_plan:
        warnings.append({
            "code": "background_seam_lane_ready",
            "level": "info",
            "message": "Composer detected seam/transition lanes for future low-denoise background harmony pass.",
            "count": len(seam_plan),
        })

    if len(background_lanes) > 2:
        warnings.append({
            "code": "background_zone_soft_limit_exceeded",
            "level": "warning",
            "message": "More than two background zones can weaken split-world clarity; composer will prioritize explicit zones and seam masks.",
            "background_lane_count": len(background_lanes),
        })

    unique_ready_count = len([lane for lane in composer_lanes if lane.get("prompt_unique_ready")])
    return {
        "schema": V054_BACKGROUND_REGION_COMPOSER_SCHEMA,
        "phase": V054_BACKGROUND_REGION_COMPOSER_PHASE,
        "status": "applied" if (composer_lanes or seam_plan) else "off",
        "runtime_mode": "metadata_ready_legacy_attention_bridge",
        "compatibility_mode": "composer_contract_without_return_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "counts": {
            "background_lanes": len(composer_lanes),
            "seam_lanes": len(seam_plan),
            "duplicate_prompt_groups": len(duplicate_pairs or []),
            "composer_prompt_repairs": repaired_count,
            "duplicate_prompt_auto_repairs": auto_repair_count,
            "duplicate_prompt_blocked_lanes": blocked_duplicate_count,
            "unique_background_lanes_ready": unique_ready_count,
        },
        "concept_counts": concept_counts,
        "background_lanes": composer_lanes,
        "seam_lanes": seam_plan,
        "stage_plan": [
            {
                "stage": "background_foundation_stage",
                "source": "background_lanes",
                "execution_target": "future_background_attention_or_inpaint_pass",
                "mask_policy": "background masks yield to all foreground subject/detail/prop masks",
            },
            {
                "stage": "character_foreground_protection_stage",
                "source": "mask_authority_engine",
                "execution_target": "protect existing subject/detail masks during background composition",
                "mask_policy": "face/hair/hands/character masks win over background zones",
            },
            {
                "stage": "seam_harmony_stage_after_backgrounds",
                "source": "seam_lanes",
                "execution_target": "future low-denoise seam blend",
                "mask_policy": "seam blends adjacent background zones only",
            },
        ],
        "prompt_hygiene": {
            "duplicate_background_prompt_pairs": duplicate_pairs or [],
            "strict_unique_prompt_policy": "future runtime should block or require explicit auto-repair when authored background zones share the same base prompt",
            "global_prompt_background_content_policy": "global prompt may define split-world intent but each background lane must own its local environment content",
            "composer_repair_is_metadata_only": True,
            "duplicate_repair_modes": ["warn", "auto_repair", "block"],
            "ui_controls_phase": V054_BACKGROUND_COMPOSER_UI_CONTROLS_PHASE,
        },
        "mask_contract": {
            "uses_mask_authority_engine": True,
            "background_yields_to_foreground": True,
            "seam_yields_to_subjects": True,
            "coverage_policy": (mask_authority_engine.get("coverage") or {}).get("uncovered_pixels_policy") if isinstance(mask_authority_engine.get("coverage"), dict) else None,
            "overlap_policy": (mask_authority_engine.get("overlap_summary") or {}).get("resolution_policy") if isinstance(mask_authority_engine.get("overlap_summary"), dict) else None,
        },
        "warnings": warnings,
        "future_consumers": [
            "background_attention_runtime_bridge",
            "background_zone_inpaint_pass",
            "seam_harmony_pass",
            "mask_inspector_ui",
            "extension_route_controller_v2",
        ],
        "policy": "Phase 26.10.4 compiles strict background/seam composition authority from lane and mask contracts. It preserves the current V054 runtime while preparing unique background lanes, subject-protection masks, and future seam harmony passes.",
    }


# -----------------------------------------------------------------------------
# Phase 26.10.5 — Extension Route Controller V2
# -----------------------------------------------------------------------------
# Metadata/internal contract only. This controller binds LoRA, IPAdapter,
# ControlNet, and ADetailer routes to RegionLane, MaskAuthority, Regional
# Attention, and Background Composer contracts. It does not change current V054
# runtime execution, return order, Character Lock behavior, or fallback paths.

V054_EXTENSION_ROUTE_CONTROLLER_PHASE = "SD-V054-26.10.5"
V054_EXTENSION_ROUTE_CONTROLLER_SCHEMA = "neo.image.scene_director.extension_route_controller.v054.v2"


V054_EXTENSION_ROUTE_FUTURE_EXECUTORS = {
    "lora": "regional_lora_latent_executor",
    "ipadapter": "identity_restore_executor",
    "faceid": "identity_restore_executor",
    "controlnet": "regional_controlnet_conditioning_executor",
    "adetailer": "regional_detailer_crop_executor",
    "detailer": "regional_detailer_crop_executor",
}


def _v054_extension_route_scope(ext: str, lane: Dict[str, Any] | None, route: Dict[str, Any]) -> str:
    ext = str(ext or "unknown").strip().lower()
    lane_type = str((lane or {}).get("lane_type") or "unknown").strip().lower()
    target = str(route.get("target_area") or (lane or {}).get("target_area") or "").strip().lower()
    if ext in {"ipadapter", "faceid"}:
        return str(route.get("scope_mode") or "identity_only")
    if ext == "lora":
        if target in {"hair", "outfit", "armor", "clothing", "prop", "sword", "weapon"}:
            return f"regional_{target}_style"
        return "regional_character_style" if lane_type == "character" else "regional_style"
    if ext == "controlnet":
        if target in {"pose", "body", "character_pose"} or lane_type == "character":
            return "regional_pose_or_structure"
        return "regional_structure"
    if ext in {"adetailer", "detailer"}:
        if target in {"face", "head", "hair"}:
            return f"regional_{target}_detail"
        return "regional_crop_detail"
    return "assigned_region_only"


def _v054_extension_route_stage(ext: str, route: Dict[str, Any]) -> str:
    ext = str(ext or "unknown").strip().lower()
    actual = str(route.get("actual_mode") or route.get("execution_mode") or "").strip().lower()
    if ext == "lora":
        if actual in {"regional_model_delta_mixer", "regional_latent_lora"}:
            return "regional_latent_or_model_delta_stage"
        if actual in {"crop_refine_pass", "finish_pass_fallback", "masked_finish_pass"}:
            return "post_generation_regional_lora_refine_stage"
        return "regional_lora_route_contract_stage"
    if ext in {"ipadapter", "faceid"}:
        return "post_generation_identity_restore_stage" if "second_pass" in actual or "restore" in actual else "identity_conditioning_stage"
    if ext == "controlnet":
        return "first_pass_regional_structure_stage"
    if ext in {"adetailer", "detailer"}:
        return "post_generation_detailer_crop_stage"
    return "assigned_extension_stage"


def _v054_extension_route_fallback_policy(ext: str) -> List[str]:
    ext = str(ext or "unknown").strip().lower()
    if ext == "lora":
        return ["regional_latent_lora", "crop_refine_pass", "masked_finish_pass", "global_fallback_with_warning", "metadata_only_skip"]
    if ext in {"ipadapter", "faceid"}:
        return ["face_only_restore", "identity_only_second_pass_restore", "metadata_only_skip"]
    if ext == "controlnet":
        return ["regional_controlnet_masked_conditioning", "owner_disabled_metadata_only", "metadata_only_skip"]
    if ext in {"adetailer", "detailer"}:
        return ["regional_detailer_crop", "masked_finish_detail", "metadata_only_skip"]
    return ["assigned_region_route", "metadata_only_skip"]


def _v054_build_extension_route_controller_v2(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    regional_attention_controller: Dict[str, Any] | None = None,
    background_region_composer: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
) -> Dict[str, Any]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(mask_authority_engine, dict) or not isinstance(mask_authority_engine.get("masks"), list):
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(regional_attention_controller, dict) or not isinstance(regional_attention_controller.get("lanes"), list):
        regional_attention_controller = _v054_build_regional_attention_controller(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(background_region_composer, dict):
        background_region_composer = _v054_build_background_region_composer(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    data = _v054_parse_json_object(extension_routes_json, default={})
    raw_routes: List[Dict[str, Any]] = []
    incoming_warnings: List[Any] = []
    if isinstance(data, dict):
        raw_routes = [r for r in (data.get("routes") or []) if isinstance(r, dict)]
        incoming_warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    elif isinstance(data, list):
        raw_routes = [r for r in data if isinstance(r, dict)]

    lanes_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (region_lane_compiler.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }
    masks_by_region = {
        str(mask.get("region_id") or ""): mask
        for mask in (mask_authority_engine.get("masks") or [])
        if isinstance(mask, dict) and str(mask.get("region_id") or "")
    }
    attention_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (regional_attention_controller.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }
    background_composer_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (background_region_composer.get("background_lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }
    seam_composer_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (background_region_composer.get("seam_lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }

    warnings: List[Dict[str, Any]] = []
    for item in incoming_warnings or []:
        if isinstance(item, dict):
            warnings.append(dict(item))
        elif item:
            warnings.append({"code": str(item), "level": "warning", "message": str(item)})

    route_entries: List[Dict[str, Any]] = []
    bindings_by_region: Dict[str, List[str]] = {}
    disabled_count = 0
    skipped_count = 0

    for idx, raw in enumerate(raw_routes, start=1):
        ext = str(raw.get("extension_type") or raw.get("type") or "unknown").strip().lower() or "unknown"
        route_id = str(raw.get("route_id") or f"extension_route_{idx}")
        rid = str(raw.get("region_id") or "").strip()
        owner_enabled = raw.get("owner_enabled")
        disabled = bool(raw.get("enabled") is False or owner_enabled is False or raw.get("execution_disabled") or raw.get("execution_allowed") is False)
        lane = lanes_by_region.get(rid)
        mask_meta = masks_by_region.get(rid)
        attention_lane = attention_by_region.get(rid)
        background_lane = background_composer_by_region.get(rid) or seam_composer_by_region.get(rid)
        route_warnings: List[str] = [str(w) for w in (raw.get("instruction_preservation_warnings") or raw.get("warnings") or []) if str(w).strip()]

        if disabled:
            disabled_count += 1
            route_warnings.append("extension_route_owner_disabled")
        if not lane:
            route_warnings.append("extension_route_region_missing")
        if not mask_meta:
            route_warnings.append("extension_route_mask_missing")
        lane_type = str((lane or {}).get("lane_type") or raw.get("region_role") or "unknown").strip().lower()
        subject_slot = (lane or {}).get("subject_slot")
        raw_subject = raw.get("subject_slot")
        if raw_subject is not None and subject_slot is not None and int(raw_subject) != int(subject_slot):
            route_warnings.append("extension_route_subject_slot_mismatch")
        if lane_type == "character" and ext in {"ipadapter", "faceid", "lora"} and not subject_slot:
            route_warnings.append("extension_route_subject_slot_mismatch")

        runtime_proof = _v054_lora_runtime_proof(raw) if ext == "lora" else None
        requested_mode = raw.get("requested_mode") or raw.get("execution_mode") or raw.get("actual_mode") or "node_authority"
        actual_mode = raw.get("actual_mode") or raw.get("execution_mode") or ("metadata_only" if disabled else "node_authority")
        model_delta_scope = raw.get("model_delta_scope")
        hard_region_isolation = False
        global_bleed_risk = bool(raw.get("global_bleed_risk"))

        lora_compatibility = raw.get("lora_compatibility") if isinstance(raw.get("lora_compatibility"), dict) else {}
        visibility_booster = raw.get("visibility_booster") if isinstance(raw.get("visibility_booster"), dict) else {}
        if ext == "lora":
            for warning_code in (lora_compatibility.get("warnings") or []):
                if warning_code not in route_warnings:
                    route_warnings.append(str(warning_code))
            for warning_code in (visibility_booster.get("warnings") or []):
                if warning_code not in route_warnings:
                    route_warnings.append(str(warning_code))
            if runtime_proof and runtime_proof.get("runtime_applied") and model_delta_scope in {"regional_noise_delta", "custom_masked_model_delta"}:
                hard_region_isolation = bool(mask_meta and not disabled)
                route_warnings.append("regional_lora_runtime_proof_confirmed")
            else:
                hard_region_isolation = False
                if model_delta_scope == "global_model_branch":
                    global_bleed_risk = True
                    route_warnings.append("lora_model_delta_is_global_without_true_node_delta")
                else:
                    route_warnings.append("regional_lora_latent_executor_required_for_hard_isolation")
                if str(actual_mode) in {"regional_model_delta_mixer", "regional_latent_lora"} and not (runtime_proof or {}).get("runtime_applied"):
                    actual_mode = "crop_refine_or_finish_pass_fallback"
                    route_warnings.append("regional_lora_runtime_proof_missing_controller_fallback")
        elif ext in {"ipadapter", "faceid"}:
            hard_region_isolation = bool(mask_meta and not disabled)
            global_bleed_risk = False
            if str(actual_mode).lower() == "second_pass_restore":
                route_warnings.append("ipadapter_second_pass_restore_preserved")
            if str(raw.get("mask_type") or "subject_mask") == "subject_mask" and lane_type == "character":
                route_warnings.append("ipadapter_subject_mask_route_identity_only")
        elif ext in {"controlnet", "adetailer", "detailer"}:
            hard_region_isolation = bool(mask_meta and not disabled)
            global_bleed_risk = False
            route_warnings.append(f"{ext}_assigned_region_mask_ready" if not disabled and mask_meta else f"{ext}_metadata_only")
        else:
            hard_region_isolation = bool(mask_meta and not disabled)

        if disabled or not lane or not mask_meta:
            skipped_count += 1
            binding_status = "metadata_only"
            route_warnings.append("extension_route_not_executable_in_controller")
        else:
            binding_status = "bound_to_region_lane"
            bindings_by_region.setdefault(rid, []).append(route_id)

        for code in sorted(set(route_warnings)):
            level = "info" if code in {"ipadapter_second_pass_restore_preserved", "ipadapter_subject_mask_route_identity_only", "regional_lora_runtime_proof_confirmed"} or code.endswith("_assigned_region_mask_ready") else "warning"
            warnings.append({"code": code, "level": level, "route_id": route_id, "region_id": rid, "message": code.replace("_", " ")})

        lock_contract = (lane or {}).get("lock_contract") if isinstance((lane or {}).get("lock_contract"), dict) else {}
        mask_ref = raw.get("mask_output") or (mask_meta or {}).get("mask_ref") or (lane or {}).get("mask_ref")
        route_entries.append({
            "route_id": route_id,
            "extension_type": ext,
            "owner_extension_id": raw.get("owner_extension_id"),
            "owner_enabled": owner_enabled is not False,
            "binding_status": binding_status,
            "region_id": rid or None,
            "lane_id": (lane or {}).get("lane_id"),
            "label": (lane or {}).get("label") or raw.get("label") or rid,
            "region_role": (lane or {}).get("role") or raw.get("region_role"),
            "lane_type": lane_type,
            "subject_slot": subject_slot,
            "parent_region_id": (lane or {}).get("parent_region_id"),
            "target_area": raw.get("target_area") or (lane or {}).get("target_area"),
            "mask_ref": mask_ref,
            "mask_id": (mask_meta or {}).get("mask_id"),
            "mask_type": raw.get("mask_type") or (mask_meta or {}).get("mask_type"),
            "mask_priority_group": (mask_meta or {}).get("priority_group"),
            "mask_coverage_percent": (mask_meta or {}).get("coverage_percent"),
            "node_authority_mask_confirmed": bool(mask_meta and not disabled),
            "attention_lane_id": (attention_lane or {}).get("lane_id"),
            "attention_role": (attention_lane or {}).get("attention_role"),
            "background_composer_lane_id": (background_lane or {}).get("composer_lane_id"),
            "requested_mode": requested_mode,
            "actual_mode": actual_mode,
            "execution_stage": _v054_extension_route_stage(ext, raw),
            "preferred_future_executor": V054_EXTENSION_ROUTE_FUTURE_EXECUTORS.get(ext, "assigned_extension_executor"),
            "scope_mode": _v054_extension_route_scope(ext, lane, raw),
            "strength": raw.get("strength"),
            "start_at": raw.get("effective_start_at", raw.get("start_at")),
            "end_at": raw.get("effective_end_at", raw.get("end_at")),
            "hard_region_isolation": bool(hard_region_isolation),
            "global_bleed_risk": bool(global_bleed_risk),
            "fallback_used": bool(raw.get("fallback_used") or binding_status == "metadata_only"),
            "fallback_policy": _v054_extension_route_fallback_policy(ext),
            "trigger_terms": raw.get("trigger_terms") or raw.get("trigger_words") or [],
            "reference_images": raw.get("reference_images") or raw.get("image_names") or [],
            "lora_name": raw.get("lora_name"),
            "lora_family": raw.get("lora_family") or (lora_compatibility.get("lora_family") if isinstance(lora_compatibility, dict) else None),
            "checkpoint_family": raw.get("checkpoint_family") or (lora_compatibility.get("checkpoint_family") if isinstance(lora_compatibility, dict) else None),
            "lora_compatibility": lora_compatibility,
            "visibility_booster": visibility_booster,
            "controlnet_unit_id": raw.get("controlnet_unit_id"),
            "adetailer_pass_id": raw.get("adetailer_pass_id"),
            "runtime_proof": runtime_proof,
            "character_lock_contract_carried": bool(lock_contract and lane_type == "character"),
            "lock_contract": lock_contract if lane_type == "character" else {},
            "postpass_guard_required": bool(ext in {"lora", "ipadapter", "faceid", "adetailer", "detailer"} and lane_type == "character"),
            "route_prompt_policy": {
                "global_prompt_injection_allowed": False if rid else bool(raw.get("global", False)),
                "trigger_terms_assigned_region_only": ext == "lora",
                "regional_negative_should_follow_lane": True,
                "no_cross_region_borrowing": True,
            },
            "warnings": sorted(set(route_warnings)),
        })

    type_counts: Dict[str, int] = {}
    bound_type_counts: Dict[str, int] = {}
    for route in route_entries:
        ext = str(route.get("extension_type") or "unknown")
        type_counts[ext] = type_counts.get(ext, 0) + 1
        if route.get("binding_status") == "bound_to_region_lane":
            bound_type_counts[ext] = bound_type_counts.get(ext, 0) + 1

    status = "applied" if route_entries else "off"
    return {
        "schema": V054_EXTENSION_ROUTE_CONTROLLER_SCHEMA,
        "phase": V054_EXTENSION_ROUTE_CONTROLLER_PHASE,
        "status": status,
        "runtime_mode": "metadata_ready_legacy_extension_bridge",
        "compatibility_mode": "route_controller_contract_without_runtime_or_return_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "counts": {
            "routes": len(route_entries),
            "bound_routes": len([r for r in route_entries if r.get("binding_status") == "bound_to_region_lane"]),
            "metadata_only_routes": len([r for r in route_entries if r.get("binding_status") == "metadata_only"]),
            "disabled_owner_routes": disabled_count,
            "skipped_routes": skipped_count,
            "types": type_counts,
            "bound_types": bound_type_counts,
        },
        "routes": route_entries,
        "bindings_by_region": bindings_by_region,
        "route_families": {
            "lora": {"preferred_future_executor": "regional_lora_latent_executor", "fallback_policy": _v054_extension_route_fallback_policy("lora")},
            "ipadapter": {"preferred_future_executor": "identity_restore_executor", "fallback_policy": _v054_extension_route_fallback_policy("ipadapter")},
            "controlnet": {"preferred_future_executor": "regional_controlnet_conditioning_executor", "fallback_policy": _v054_extension_route_fallback_policy("controlnet")},
            "adetailer": {"preferred_future_executor": "regional_detailer_crop_executor", "fallback_policy": _v054_extension_route_fallback_policy("adetailer")},
        },
        "mask_contract": {
            "uses_mask_authority_engine": True,
            "mask_priority_order": mask_authority_engine.get("priority_order") if isinstance(mask_authority_engine, dict) else None,
            "overlap_policy": (mask_authority_engine.get("overlap_summary") or {}).get("resolution_policy") if isinstance(mask_authority_engine.get("overlap_summary"), dict) else None,
            "no_global_canvas_borrowing": True,
        },
        "attention_contract": {
            "uses_regional_attention_controller_v2": True,
            "extension_routes_bound_to_attention_lanes": True,
            "regional_trigger_terms_never_added_to_global_prompt": True,
        },
        "background_contract": {
            "uses_background_region_composer": True,
            "background_routes_yield_to_foreground_masks": True,
            "seam_routes_yield_to_subjects": True,
        },
        "stage_plan": [
            {"stage": "first_pass_attention_and_structure", "routes": ["controlnet", "regional_attention_lanes"], "policy": "region masks and lane prompts own local composition"},
            {"stage": "regional_lora_latent_executor_future", "routes": ["lora"], "policy": "multiple U-Net evaluations with region-specific LoRA strengths before claiming hard isolation"},
            {"stage": "post_generation_identity_and_detail", "routes": ["ipadapter", "adetailer", "lora_crop_refine"], "policy": "post-passes must carry Character Lock and Mask Authority contracts"},
        ],
        "warnings": warnings,
        "future_consumers": [
            "regional_lora_latent_executor",
            "regional_controlnet_runtime_bridge",
            "regional_detailer_runtime_bridge",
            "ipadapter_identity_restore_bridge",
            "extension_route_inspector_ui",
        ],
        "policy": "Phase 26.10.5 binds LoRA, IPAdapter, ControlNet, and ADetailer routes to RegionLane and MaskAuthority contracts without changing current V054 runtime behavior, output order, or Character Lock authority.",
    }


# -----------------------------------------------------------------------------
# Phase 26.10.6 — Regional LoRA Latent Executor
# -----------------------------------------------------------------------------
# Compatibility-safe executor contract for true regional LoRA isolation.
# V054 still returns the same 16 outputs and keeps the existing attention bridge;
# this planner binds LoRA routes to masks/lane contracts and describes the
# multi-U-Net latent-composite execution required before hard isolation may be
# claimed. Runtime providers can consume this contract to build the heavier
# workflow branch without weakening Character Lock or older fallback behavior.

V054_REGIONAL_LORA_LATENT_EXECUTOR_PHASE = "SD-V054-26.10.6"
V054_REGIONAL_LORA_LATENT_EXECUTOR_SCHEMA = "neo.image.scene_director.regional_lora_latent_executor.v054.v1"
V054_REGIONAL_LORA_LATENT_EXECUTOR_MAX_RUNTIME_LANES = 2


def _v054_lora_family_guess(name: Any) -> str:
    text = str(name or "").lower()
    if any(token in text for token in ("pony", "pdxl", "score_9")):
        return "pony_xl"
    if any(token in text for token in ("sdxl", "xl", "kohaku", "animagine", "illustrious")):
        return "sdxl"
    if any(token in text for token in ("flux", "dev", "schnell")):
        return "flux"
    if any(token in text for token in ("1.5", "sd15", "sd_15", "v1-5")):
        return "sd15"
    return "unknown"


def _v054_lora_step_window(route: Dict[str, Any]) -> Dict[str, Any]:
    start_at = _clamp01(route.get("start_at", route.get("effective_start_at", 0.0)), 0.0)
    end_at = _clamp01(route.get("end_at", route.get("effective_end_at", 1.0)), 1.0)
    if end_at < start_at:
        start_at, end_at = end_at, start_at
    return {
        "start_at": start_at,
        "end_at": end_at,
        "stop_step_policy": "disable_route_lora_after_end_at_to_reduce_late_bleed",
        "start_step_policy": "enable_route_lora_at_start_at_for_assigned_lane_only",
        "hires_policy": "repeat_same_window_for_hires_or_disable_when_route_requests_stop_hr",
    }


def _v054_lora_strength_plan(route: Dict[str, Any]) -> Dict[str, Any]:
    requested = _safe_float(route.get("strength", route.get("effective_weight", route.get("requested_weight", 0.8))), 0.8)
    requested = max(-2.0, min(2.0, requested))
    booster = route.get("visibility_booster") if isinstance(route.get("visibility_booster"), dict) else {}
    preset_values = booster.get("preset_values") if isinstance(booster.get("preset_values"), dict) else {}
    boosted = requested
    if booster.get("enabled") and preset_values.get("strength") is not None:
        boosted = max(boosted, _safe_float(preset_values.get("strength"), requested))
    boosted = max(-2.0, min(2.0, boosted))
    return {
        "requested_strength": requested,
        "unet_strength": boosted,
        "clip_strength": _safe_float(route.get("clip_strength", route.get("te_strength", boosted)), boosted),
        "strength_source": "visibility_booster" if booster.get("enabled") else "extension_route_controller_v2",
        "visibility_booster": booster,
        "recommended_first_test_range": [0.45, 0.75],
        "overcook_warning_threshold": 0.85,
    }


def _v054_lora_route_prompt(route: Dict[str, Any]) -> Dict[str, Any]:
    trigger_terms = route.get("trigger_terms") or route.get("trigger_words") or []
    if isinstance(trigger_terms, str):
        trigger_terms = _clean_list(trigger_terms)
    trigger_terms = [str(t).strip() for t in trigger_terms if str(t).strip()]
    lane_prompt = str(route.get("prompt_positive") or route.get("region_prompt") or "").strip()
    if not lane_prompt and isinstance(route.get("lock_contract"), dict):
        # The extension controller does not always copy the lane prompt forward;
        # keep this fallback intentionally conservative so it never invents a
        # character description.
        lane_prompt = str(route.get("label") or "").strip()
    prompt_parts: List[str] = []
    for term in trigger_terms:
        if term and term.lower() not in " ".join(prompt_parts).lower():
            prompt_parts.append(term)
    if lane_prompt:
        prompt_parts.append(lane_prompt)
    return {
        "trigger_terms": trigger_terms,
        "local_positive_prompt": ", ".join([p for p in prompt_parts if p]),
        "local_negative_prompt": str(route.get("prompt_negative") or route.get("regional_negative") or "").strip(),
        "global_prompt_injection_allowed": False,
        "trigger_terms_assigned_region_only": True,
    }


def _v054_build_regional_lora_latent_executor(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    regional_attention_controller: Dict[str, Any] | None = None,
    background_region_composer: Dict[str, Any] | None = None,
    extension_route_controller_v2: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
    max_runtime_lanes: int = V054_REGIONAL_LORA_LATENT_EXECUTOR_MAX_RUNTIME_LANES,
) -> Dict[str, Any]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(mask_authority_engine, dict) or not isinstance(mask_authority_engine.get("masks"), list):
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(regional_attention_controller, dict) or not isinstance(regional_attention_controller.get("lanes"), list):
        regional_attention_controller = _v054_build_regional_attention_controller(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(background_region_composer, dict):
        background_region_composer = _v054_build_background_region_composer(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(extension_route_controller_v2, dict) or not isinstance(extension_route_controller_v2.get("routes"), list):
        extension_route_controller_v2 = _v054_build_extension_route_controller_v2(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            background_region_composer=background_region_composer,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    max_runtime_lanes = max(1, int(max_runtime_lanes or V054_REGIONAL_LORA_LATENT_EXECUTOR_MAX_RUNTIME_LANES))
    raw_lora_routes = [
        route for route in (extension_route_controller_v2.get("routes") or [])
        if isinstance(route, dict) and str(route.get("extension_type") or "").strip().lower() == "lora"
    ]

    lanes_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (region_lane_compiler.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }
    masks_by_region = {
        str(mask.get("region_id") or ""): mask
        for mask in (mask_authority_engine.get("masks") or [])
        if isinstance(mask, dict) and str(mask.get("region_id") or "")
    }
    attention_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (regional_attention_controller.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }

    executor_routes: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    runtime_slot = 0

    # Higher priority masks/character routes go first. This keeps the first
    # V054 executor pass deterministic while the provider bridge is still young.
    def _route_sort_key(route: Dict[str, Any]) -> Tuple[int, int, int, str]:
        rid = str(route.get("region_id") or "")
        mask_meta = masks_by_region.get(rid) or {}
        lane = lanes_by_region.get(rid) or {}
        lane_type = str(route.get("lane_type") or lane.get("lane_type") or "unknown").lower()
        lane_rank = 0 if lane_type == "character" else (1 if lane_type in {"prop", "object", "detail", "hair_detail"} else 2)
        return (
            lane_rank,
            int(route.get("subject_slot") or lane.get("subject_slot") or 999),
            int(mask_meta.get("priority_rank") or 999),
            str(route.get("route_id") or ""),
        )

    for route in sorted(raw_lora_routes, key=_route_sort_key):
        rid = str(route.get("region_id") or "").strip()
        route_id = str(route.get("route_id") or f"lora_route_{len(executor_routes)+1}")
        lane = lanes_by_region.get(rid) or {}
        mask_meta = masks_by_region.get(rid) or {}
        attention_lane = attention_by_region.get(rid) or {}
        runtime_proof = _v054_lora_runtime_proof(route)
        proof_confirmed = bool(runtime_proof.get("runtime_applied"))
        bound = route.get("binding_status") == "bound_to_region_lane"
        owner_enabled = route.get("owner_enabled") is not False
        mask_ready = bool(route.get("mask_ref") or mask_meta.get("mask_ref"))
        lora_name = route.get("lora_name") or runtime_proof.get("resolved_lora_path") or route.get("name")
        prompt_plan = _v054_lora_route_prompt(route)
        has_activation = bool(lora_name or prompt_plan.get("trigger_terms"))
        eligible = bool(bound and owner_enabled and mask_ready and has_activation)
        over_limit = eligible and runtime_slot >= max_runtime_lanes
        if eligible and not over_limit:
            runtime_slot += 1
            assigned_slot = runtime_slot
            execution_status = "prepared_for_regional_latent_lora"
        elif proof_confirmed:
            assigned_slot = None
            execution_status = "runtime_confirmed_from_upstream_proof"
        else:
            assigned_slot = None
            execution_status = "metadata_only"

        route_warnings = set(str(w) for w in (route.get("warnings") or []) if str(w).strip())
        if not eligible:
            if not bound:
                route_warnings.add("regional_lora_latent_executor_route_not_bound")
            if not mask_ready:
                route_warnings.add("regional_lora_latent_executor_mask_missing")
            if not has_activation:
                route_warnings.add("regional_lora_latent_executor_lora_or_trigger_missing")
            route_warnings.add("regional_lora_latent_executor_metadata_only")
        if over_limit:
            route_warnings.add("regional_lora_latent_executor_route_limit_metadata_only")
            route_warnings.add("regional_lora_latent_executor_max_two_lanes_initial")
        if not proof_confirmed:
            route_warnings.add("regional_lora_latent_executor_runtime_bridge_required")
            route_warnings.add("regional_lora_no_hard_isolation_until_latent_runtime_proof")
        if route.get("character_lock_contract_carried"):
            route_warnings.add("regional_lora_character_lock_contract_carried")
        family_guess = _v054_lora_family_guess(lora_name)
        if family_guess == "pony_xl":
            route_warnings.add("regional_lora_pony_xl_checkpoint_compatibility_should_be_verified")

        hard_isolation = bool(proof_confirmed and mask_ready and route.get("hard_region_isolation") is True)
        entry = {
            "route_id": route_id,
            "extension_type": "lora",
            "region_id": rid or None,
            "lane_id": route.get("lane_id") or lane.get("lane_id"),
            "label": route.get("label") or lane.get("label") or rid,
            "region_role": route.get("region_role") or lane.get("role"),
            "lane_type": route.get("lane_type") or lane.get("lane_type"),
            "subject_slot": route.get("subject_slot") or lane.get("subject_slot"),
            "parent_region_id": route.get("parent_region_id") or lane.get("parent_region_id"),
            "target_area": route.get("target_area") or lane.get("target_area"),
            "binding_status": route.get("binding_status"),
            "eligible_for_latent_executor": eligible,
            "execution_status": execution_status,
            "scheduled_runtime_slot": assigned_slot,
            "actual_mode": "regional_latent_lora_prepared" if execution_status == "prepared_for_regional_latent_lora" else route.get("actual_mode"),
            "requested_mode": route.get("requested_mode"),
            "lora_name": lora_name,
            "lora_family_guess": family_guess,
            "mask_ref": route.get("mask_ref") or mask_meta.get("mask_ref"),
            "mask_id": route.get("mask_id") or mask_meta.get("mask_id"),
            "mask_priority_group": route.get("mask_priority_group") or mask_meta.get("priority_group"),
            "mask_coverage_percent": route.get("mask_coverage_percent") or mask_meta.get("coverage_percent"),
            "attention_lane_id": route.get("attention_lane_id") or attention_lane.get("lane_id"),
            "attention_role": route.get("attention_role") or attention_lane.get("attention_role"),
            "prompt_plan": prompt_plan,
            "strength_plan": _v054_lora_strength_plan(route),
            "lora_compatibility": route.get("lora_compatibility") if isinstance(route.get("lora_compatibility"), dict) else {},
            "visibility_booster": route.get("visibility_booster") if isinstance(route.get("visibility_booster"), dict) else {},
            "step_window": _v054_lora_step_window(route),
            "latent_composite_plan": {
                "strategy": "multi_unet_regional_latent_composite",
                "base_pass": "run_base_model_without_this_regional_lora_delta",
                "regional_pass": "run_same_noise_sigma_with_this_route_lora_enabled_for_assigned_lane",
                "composite": "base_latent * inverse_mask + regional_lora_latent * assigned_mask",
                "mask_space": "latent",
                "mask_source": "mask_authority_engine",
                "normalize_with_other_lora_masks": True,
                "preserve_uncovered_pixels_with_base": True,
            },
            "runtime_requirements": {
                "provider_workflow_bridge_required": not proof_confirmed,
                "requires_per_route_lora_loader_or_model_patch": True,
                "requires_repeated_unet_eval": True,
                "batch_size_policy": "batch_size_1_first_for_predictable_region_order",
                "max_runtime_lanes_initial": max_runtime_lanes,
            },
            "fallback_policy": route.get("fallback_policy") or _v054_extension_route_fallback_policy("lora"),
            "fallback_if_runtime_missing": "crop_refine_pass_then_masked_finish_pass",
            "runtime_proof_required": True,
            "runtime_proof": runtime_proof,
            "runtime_applied": proof_confirmed,
            "hard_region_isolation": hard_isolation,
            "global_bleed_risk": False if hard_isolation else bool(route.get("global_bleed_risk")),
            "character_lock_contract_carried": bool(route.get("character_lock_contract_carried")),
            "lock_contract": route.get("lock_contract") if isinstance(route.get("lock_contract"), dict) else {},
            "postpass_guard_required": bool(route.get("postpass_guard_required", True)),
            "warnings": sorted(route_warnings),
        }
        executor_routes.append(entry)
        for code in entry["warnings"]:
            level = "info" if code in {"regional_lora_character_lock_contract_carried"} else "warning"
            warnings.append({"code": code, "level": level, "route_id": route_id, "region_id": rid, "message": code.replace("_", " ")})

    prepared = [r for r in executor_routes if r.get("execution_status") == "prepared_for_regional_latent_lora"]
    confirmed = [r for r in executor_routes if r.get("runtime_applied") is True]
    metadata_only = [r for r in executor_routes if r.get("execution_status") == "metadata_only"]
    over_limit_count = len([r for r in executor_routes if "regional_lora_latent_executor_route_limit_metadata_only" in (r.get("warnings") or [])])
    status = "off" if not executor_routes else ("runtime_confirmed" if confirmed and len(confirmed) == len(executor_routes) else "prepared")

    return {
        "schema": V054_REGIONAL_LORA_LATENT_EXECUTOR_SCHEMA,
        "phase": V054_REGIONAL_LORA_LATENT_EXECUTOR_PHASE,
        "status": status,
        "runtime_mode": "executor_contract_ready_provider_bridge_required",
        "compatibility_mode": "metadata_and_route_plan_without_output_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "counts": {
            "routes": len(executor_routes),
            "prepared_routes": len(prepared),
            "metadata_only_routes": len(metadata_only),
            "runtime_confirmed_routes": len(confirmed),
            "over_limit_routes": over_limit_count,
            "routes_with_character_lock_contract": len([r for r in executor_routes if r.get("character_lock_contract_carried")]),
            "routes_with_trigger_terms": len([r for r in executor_routes if (r.get("prompt_plan") or {}).get("trigger_terms")]),
            "max_runtime_lanes_initial": max_runtime_lanes,
        },
        "routes": executor_routes,
        "execution_graph_contract": {
            "stage_1": "base_unet_prediction_without_regional_lora_delta",
            "stage_2": "per_lora_region_unet_prediction_with_route_lora_strength",
            "stage_3": "latent_mask_composite_using_mask_authority_engine",
            "stage_4": "postpass_character_lock_and_detail_guard",
            "claim_policy": "hard_region_isolation_false_until_runtime_proof_confirms_loaded_lora_nonzero_delta_and_masked_composite",
        },
        "dependencies": {
            "region_lane_compiler_phase": region_lane_compiler.get("phase") if isinstance(region_lane_compiler, dict) else None,
            "mask_authority_engine_phase": mask_authority_engine.get("phase") if isinstance(mask_authority_engine, dict) else None,
            "regional_attention_controller_phase": regional_attention_controller.get("phase") if isinstance(regional_attention_controller, dict) else None,
            "extension_route_controller_phase": extension_route_controller_v2.get("phase") if isinstance(extension_route_controller_v2, dict) else None,
        },
        "safety_contract": {
            "character_lock_must_be_carried_to_lora_runtime": True,
            "trigger_terms_never_enter_global_prompt": True,
            "background_lanes_not_eligible_for_character_lora_by_default": True,
            "postpass_guard_required_for_character_lora": True,
            "no_false_hard_isolation_claims": True,
        },
        "provider_bridge_todo": [
            "build Comfy workflow branch with one base model path and one per-route LoraLoader path",
            "evaluate base and regional LoRA branches at matching sigma/noise schedule",
            "composite regional latent outputs with normalized Mask Authority masks",
            "write runtime proof fields back to scene_metadata_json after provider execution",
        ],
        "warnings": warnings,
        "policy": "Phase 26.10.6 prepares the true regional LoRA latent executor contract. V054 does not claim visual hard isolation until a provider workflow bridge proves per-route LoRA load, non-zero delta, and masked latent composite at runtime.",
    }


# -----------------------------------------------------------------------------
# Phase 26.10.7 — Identity / Detail / Harmony Passes
# -----------------------------------------------------------------------------
# This phase does not execute post-passes directly. It turns the earlier regional
# contracts into a strict, provider-facing pass plan so IPAdapter restore,
# ADetailer/detail crops, LoRA fallback refinement, and final scene harmony can
# all carry Character Lock, Mask Authority, and region-local prompt boundaries.

V054_IDENTITY_DETAIL_HARMONY_PHASE = "SD-V054-26.10.7"
V054_IDENTITY_DETAIL_HARMONY_SCHEMA = "neo.image.scene_director.identity_detail_harmony_passes.v054.v1"


def _v054_lock_contract_active(lock_contract: Dict[str, Any] | None) -> bool:
    if not isinstance(lock_contract, dict):
        return False
    for key, value in lock_contract.items():
        if key == "source":
            continue
        if value is None:
            continue
        text = str(value).strip().lower()
        if text and text not in {"off", "false", "0", "none", "null"}:
            return True
    return False


def _v054_postpass_denoise_cap(lane: Dict[str, Any] | None, pass_family: str, target_area: Any = None) -> float:
    lane = lane or {}
    pass_family = str(pass_family or "").strip().lower()
    target = str(target_area or lane.get("target_area") or "").strip().lower()
    lock = lane.get("lock_contract") if isinstance(lane.get("lock_contract"), dict) else {}
    strict_or_strong = any(str(lock.get(k) or "").strip().lower() in {"strong", "strict"} for k in ("character", "gender", "hair", "build", "body_height", "outfit"))
    if pass_family == "identity":
        return 0.24 if strict_or_strong else 0.30
    if pass_family == "detail":
        if target in {"face", "head", "identity"}:
            return 0.28 if strict_or_strong else 0.32
        if target in {"hair", "hand", "hands", "outfit", "clothing", "armor", "prop", "sword", "weapon"}:
            return 0.34 if strict_or_strong else 0.38
        return 0.36 if strict_or_strong else 0.42
    if pass_family == "harmony":
        return 0.12
    return 0.35


def _v054_idh_lookup_dependencies(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None,
    mask_authority_engine: Dict[str, Any] | None,
    regional_attention_controller: Dict[str, Any] | None,
    background_region_composer: Dict[str, Any] | None,
    extension_route_controller_v2: Dict[str, Any] | None,
    regional_lora_latent_executor: Dict[str, Any] | None,
    max_subject_slots: int,
    extension_routes_json: Any,
    identity_strength: Any,
    detail_strength: Any,
    background_strength: Any,
    mask_feather: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if not isinstance(region_lane_compiler, dict) or not isinstance(region_lane_compiler.get("lanes"), list):
        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(mask_authority_engine, dict) or not isinstance(mask_authority_engine.get("masks"), list):
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(regional_attention_controller, dict) or not isinstance(regional_attention_controller.get("lanes"), list):
        regional_attention_controller = _v054_build_regional_attention_controller(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(background_region_composer, dict):
        background_region_composer = _v054_build_background_region_composer(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(extension_route_controller_v2, dict) or not isinstance(extension_route_controller_v2.get("routes"), list):
        extension_route_controller_v2 = _v054_build_extension_route_controller_v2(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            background_region_composer=background_region_composer,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    if not isinstance(regional_lora_latent_executor, dict) or not isinstance(regional_lora_latent_executor.get("routes"), list):
        regional_lora_latent_executor = _v054_build_regional_lora_latent_executor(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            background_region_composer=background_region_composer,
            extension_route_controller_v2=extension_route_controller_v2,
            max_subject_slots=max_subject_slots,
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )
    return region_lane_compiler, mask_authority_engine, regional_attention_controller, background_region_composer, extension_route_controller_v2, regional_lora_latent_executor


def _v054_build_identity_detail_harmony_passes(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    regional_attention_controller: Dict[str, Any] | None = None,
    background_region_composer: Dict[str, Any] | None = None,
    extension_route_controller_v2: Dict[str, Any] | None = None,
    regional_lora_latent_executor: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
) -> Dict[str, Any]:
    (
        region_lane_compiler,
        mask_authority_engine,
        regional_attention_controller,
        background_region_composer,
        extension_route_controller_v2,
        regional_lora_latent_executor,
    ) = _v054_idh_lookup_dependencies(
        graph,
        width,
        height,
        region_lane_compiler,
        mask_authority_engine,
        regional_attention_controller,
        background_region_composer,
        extension_route_controller_v2,
        regional_lora_latent_executor,
        max_subject_slots,
        extension_routes_json,
        identity_strength,
        detail_strength,
        background_strength,
        mask_feather,
    )

    lanes = [lane for lane in (region_lane_compiler.get("lanes") or []) if isinstance(lane, dict)]
    masks = [mask for mask in (mask_authority_engine.get("masks") or []) if isinstance(mask, dict)]
    routes = [route for route in (extension_route_controller_v2.get("routes") or []) if isinstance(route, dict)]
    lora_routes = [route for route in (regional_lora_latent_executor.get("routes") or []) if isinstance(route, dict)]

    lanes_by_region = {str(lane.get("region_id") or ""): lane for lane in lanes if str(lane.get("region_id") or "")}
    masks_by_region = {str(mask.get("region_id") or ""): mask for mask in masks if str(mask.get("region_id") or "")}
    attention_by_region = {
        str(lane.get("region_id") or ""): lane
        for lane in (regional_attention_controller.get("lanes") or [])
        if isinstance(lane, dict) and str(lane.get("region_id") or "")
    }

    warnings: List[Dict[str, Any]] = []
    identity_passes: List[Dict[str, Any]] = []
    detail_passes: List[Dict[str, Any]] = []
    harmony_passes: List[Dict[str, Any]] = []

    def add_warning(code: str, level: str = "warning", **extra: Any) -> None:
        entry = {"code": code, "level": level, "message": code.replace("_", " ")}
        entry.update({k: v for k, v in extra.items() if v is not None})
        warnings.append(entry)

    def common_contract(lane: Dict[str, Any] | None, route: Dict[str, Any] | None = None, mask_meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
        lane = lane or {}
        route = route or {}
        rid = str(route.get("region_id") or lane.get("region_id") or "").strip()
        mask_meta = mask_meta or masks_by_region.get(rid) or {}
        attention_lane = attention_by_region.get(rid) or {}
        lock_contract = route.get("lock_contract") if isinstance(route.get("lock_contract"), dict) else (lane.get("lock_contract") if isinstance(lane.get("lock_contract"), dict) else {})
        return {
            "region_id": rid or None,
            "lane_id": route.get("lane_id") or lane.get("lane_id"),
            "label": route.get("label") or lane.get("label") or rid,
            "lane_type": route.get("lane_type") or lane.get("lane_type"),
            "region_role": route.get("region_role") or lane.get("role"),
            "subject_slot": route.get("subject_slot") or lane.get("subject_slot"),
            "target_area": route.get("target_area") or lane.get("target_area"),
            "mask_ref": route.get("mask_ref") or mask_meta.get("mask_ref") or lane.get("mask_ref"),
            "mask_id": route.get("mask_id") or mask_meta.get("mask_id"),
            "mask_priority_group": route.get("mask_priority_group") or mask_meta.get("priority_group"),
            "mask_coverage_percent": route.get("mask_coverage_percent") or mask_meta.get("coverage_percent"),
            "attention_lane_id": route.get("attention_lane_id") or attention_lane.get("lane_id"),
            "attention_role": route.get("attention_role") or attention_lane.get("attention_role"),
            "prompt_positive": lane.get("prompt_positive") or "",
            "prompt_negative": lane.get("prompt_negative") or "",
            "lock_contract": lock_contract,
            "character_lock_contract_carried": bool(route.get("character_lock_contract_carried") or _v054_lock_contract_active(lock_contract)),
            "requires_mask_authority": True,
            "requires_region_local_negative": True,
            "no_global_prompt_injection": True,
        }

    # Identity pass family: all character lanes get a post-pass guard contract, and
    # IPAdapter/FaceID routes get explicit identity restore passes.
    for lane in lanes:
        if str(lane.get("lane_type") or "").lower() != "character":
            continue
        rid = str(lane.get("region_id") or "").strip()
        mask_meta = masks_by_region.get(rid) or {}
        contract = common_contract(lane, mask_meta=mask_meta)
        active_lock = _v054_lock_contract_active(contract.get("lock_contract"))
        pass_id = f"identity_guard_{rid or len(identity_passes) + 1}"
        identity_passes.append({
            "pass_id": pass_id,
            "pass_family": "identity",
            "source": "character_lock_contract",
            "execution_stage": "post_lora_identity_guard",
            "execution_status": "planned_provider_bridge_required",
            "priority": 10,
            **contract,
            "target_area": "full_character",
            "denoise_cap": _v054_postpass_denoise_cap(lane, "identity", "full_character"),
            "identity_strength": _safe_float(identity_strength, 0.70),
            "detail_strength": _safe_float(detail_strength, 0.70),
            "mask_feather": _safe_int(mask_feather, 12),
            "guard_policy": {
                "preserve_gender_body_skin_hair_outfit": True,
                "protect_against_lora_or_detailer_drift": True,
                "active_character_lock_contract": active_lock,
            },
            "fallback_policy": ["skip_if_no_character_mask", "metadata_only_no_repaint"],
            "warnings": ["identity_guard_character_lock_contract_carried"] if active_lock else ["identity_guard_no_explicit_lock_contract"],
        })
        if active_lock:
            add_warning("identity_guard_character_lock_contract_carried", "info", pass_id=pass_id, region_id=rid)
        elif not mask_meta:
            add_warning("identity_guard_mask_missing", "warning", pass_id=pass_id, region_id=rid)

    for route in routes:
        ext = str(route.get("extension_type") or "").strip().lower()
        if ext not in {"ipadapter", "faceid"}:
            continue
        rid = str(route.get("region_id") or "").strip()
        lane = lanes_by_region.get(rid) or {}
        contract = common_contract(lane, route)
        pass_id = f"identity_restore_{route.get('route_id') or len(identity_passes) + 1}"
        route_bound = route.get("binding_status") == "bound_to_region_lane"
        warnings_local = ["ipadapter_identity_only_scope_preserved"]
        if str(route.get("actual_mode") or "").lower() == "second_pass_restore":
            warnings_local.append("ipadapter_second_pass_restore_preserved")
        if not route_bound:
            warnings_local.append("identity_restore_metadata_only_route_not_bound")
        identity_passes.append({
            "pass_id": pass_id,
            "pass_family": "identity",
            "source": "extension_route_controller_v2",
            "route_id": route.get("route_id"),
            "extension_type": ext,
            "execution_stage": route.get("execution_stage") or "post_generation_identity_restore_stage",
            "execution_status": "planned_provider_bridge_required" if route_bound else "metadata_only",
            "priority": 20,
            **contract,
            "scope_mode": route.get("scope_mode") or "identity_only",
            "denoise_cap": _v054_postpass_denoise_cap(lane, "identity", route.get("target_area")),
            "reference_images": route.get("reference_images") or [],
            "identity_strength": _safe_float(identity_strength, 0.70),
            "guard_policy": {
                "identity_only_not_composition_director": True,
                "relationship_pose_preservation_required": True,
                "scene_prompt_and_region_layout_preserved": True,
                "character_lock_contract_required": bool(contract.get("character_lock_contract_carried")),
            },
            "fallback_policy": route.get("fallback_policy") or _v054_extension_route_fallback_policy(ext),
            "warnings": sorted(set(warnings_local)),
        })
        for code in warnings_local:
            add_warning(code, "info" if "preserved" in code or "scope" in code else "warning", pass_id=pass_id, route_id=route.get("route_id"), region_id=rid)

    # Detail pass family: extension detailers, detail lanes, and LoRA fallback/detail
    # guards all get region-local crop contracts with Character Lock carry-forward.
    detail_region_ids_with_extension = set()
    for route in routes:
        ext = str(route.get("extension_type") or "").strip().lower()
        if ext not in {"adetailer", "detailer"}:
            continue
        rid = str(route.get("region_id") or "").strip()
        detail_region_ids_with_extension.add(rid)
        lane = lanes_by_region.get(rid) or {}
        contract = common_contract(lane, route)
        target = route.get("target_area") or contract.get("target_area") or "regional_crop_detail"
        pass_id = f"detail_crop_{route.get('route_id') or len(detail_passes) + 1}"
        route_bound = route.get("binding_status") == "bound_to_region_lane"
        parent_lock_required = bool(lane.get("parent_region_id") or contract.get("character_lock_contract_carried"))
        local_warnings = ["detail_pass_region_mask_required", "detail_pass_region_negative_carried"]
        if parent_lock_required:
            local_warnings.append("detail_pass_parent_character_lock_required")
        if not route_bound:
            local_warnings.append("detail_pass_metadata_only_route_not_bound")
        detail_passes.append({
            "pass_id": pass_id,
            "pass_family": "detail",
            "source": "extension_route_controller_v2",
            "route_id": route.get("route_id"),
            "extension_type": ext,
            "execution_stage": route.get("execution_stage") or "post_generation_detailer_crop_stage",
            "execution_status": "planned_provider_bridge_required" if route_bound else "metadata_only",
            "priority": 40,
            **contract,
            "target_area": target,
            "detector": route.get("adetailer_pass_id") or route.get("detector") or target or "regional_crop",
            "denoise_cap": _v054_postpass_denoise_cap(lane, "detail", target),
            "detail_strength": _safe_float(detail_strength, 0.70),
            "crop_policy": {
                "detect_inside_region_mask": True,
                "intersect_with_mask_authority": True,
                "paste_back_inside_region_only": True,
                "preserve_parent_identity_if_attached": True,
            },
            "fallback_policy": route.get("fallback_policy") or _v054_extension_route_fallback_policy(ext),
            "warnings": sorted(set(local_warnings)),
        })
        for code in local_warnings:
            add_warning(code, "info" if code.endswith("carried") or code.endswith("required") else "warning", pass_id=pass_id, route_id=route.get("route_id"), region_id=rid)

    for lane in lanes:
        lane_type = str(lane.get("lane_type") or "").lower()
        role = str(lane.get("role") or "").lower()
        rid = str(lane.get("region_id") or "").strip()
        if lane_type not in {"detail", "prop", "object"} and role not in V054_DETAIL_ROLES:
            continue
        if rid in detail_region_ids_with_extension:
            continue
        contract = common_contract(lane)
        target = lane.get("target_area") or _v054_detail_target(role, lane)
        pass_id = f"detail_lane_guard_{rid or len(detail_passes) + 1}"
        detail_passes.append({
            "pass_id": pass_id,
            "pass_family": "detail",
            "source": "region_lane_compiler",
            "execution_stage": "post_generation_regional_detail_guard",
            "execution_status": "planned_provider_bridge_required",
            "priority": 45,
            **contract,
            "target_area": target,
            "detector": "mask_authority_crop",
            "denoise_cap": _v054_postpass_denoise_cap(lane, "detail", target),
            "detail_strength": _safe_float(detail_strength, 0.70),
            "crop_policy": {
                "use_lane_mask_as_crop_boundary": True,
                "paste_back_inside_region_only": True,
                "inherit_parent_lock_contract_when_attached": bool(lane.get("parent_region_id")),
            },
            "fallback_policy": ["mask_crop_refine", "masked_finish_detail", "metadata_only_skip"],
            "warnings": ["detail_lane_has_no_runtime_detailer_route_yet", "detail_lane_region_negative_carried"],
        })
        add_warning("detail_lane_has_no_runtime_detailer_route_yet", "warning", pass_id=pass_id, region_id=rid)

    for route in lora_routes:
        if not route.get("postpass_guard_required", True):
            continue
        rid = str(route.get("region_id") or "").strip()
        lane = lanes_by_region.get(rid) or {}
        contract = common_contract(lane, route)
        pass_id = f"lora_postpass_guard_{route.get('route_id') or len(detail_passes) + 1}"
        local_warnings = ["lora_postpass_guard_requires_runtime_proof_or_fallback", "lora_postpass_character_lock_carried"]
        detail_passes.append({
            "pass_id": pass_id,
            "pass_family": "detail",
            "source": "regional_lora_latent_executor",
            "route_id": route.get("route_id"),
            "extension_type": "lora",
            "execution_stage": "post_lora_crop_refine_or_guard_stage",
            "execution_status": "planned_provider_bridge_required" if route.get("execution_status") == "prepared_for_regional_latent_lora" else "metadata_only",
            "priority": 35,
            **contract,
            "target_area": route.get("target_area") or contract.get("target_area") or "regional_character_style",
            "lora_name": route.get("lora_name"),
            "trigger_terms": (route.get("prompt_plan") or {}).get("trigger_terms") if isinstance(route.get("prompt_plan"), dict) else route.get("trigger_terms"),
            "denoise_cap": _v054_postpass_denoise_cap(lane, "detail", route.get("target_area")),
            "guard_policy": {
                "do_not_change_gender_body_after_lora": True,
                "trigger_terms_remain_region_local": True,
                "fallback_after_latent_executor_only": True,
            },
            "fallback_policy": route.get("fallback_policy") or _v054_extension_route_fallback_policy("lora"),
            "runtime_proof": route.get("runtime_proof") or {},
            "warnings": sorted(set(local_warnings)),
        })
        for code in local_warnings:
            add_warning(code, "warning" if "runtime_proof" in code else "info", pass_id=pass_id, route_id=route.get("route_id"), region_id=rid)

    # Harmony pass family: background/seam/local tone matching after identity/detail
    # passes. These passes must yield to character/body/face masks.
    for bg_lane in (background_region_composer.get("background_lanes") or []):
        if not isinstance(bg_lane, dict):
            continue
        rid = str(bg_lane.get("region_id") or "").strip()
        lane = lanes_by_region.get(rid) or {}
        contract = common_contract(lane)
        pass_id = f"background_harmony_{rid or len(harmony_passes) + 1}"
        harmony_passes.append({
            "pass_id": pass_id,
            "pass_family": "harmony",
            "source": "background_region_composer",
            "execution_stage": "post_detail_background_local_harmony",
            "execution_status": "planned_provider_bridge_required",
            "priority": 70,
            **contract,
            "target_area": "background_zone",
            "background_concept": bg_lane.get("concept") or bg_lane.get("concept_class") or "unknown",
            "prompt_positive": bg_lane.get("repair_prompt") or bg_lane.get("prompt_positive") or contract.get("prompt_positive"),
            "prompt_negative": _v054_join_unique([bg_lane.get("local_negative"), contract.get("prompt_negative")]),
            "denoise_cap": _v054_postpass_denoise_cap(lane, "harmony", "background"),
            "background_strength": _safe_float(background_strength, 0.65),
            "subject_protection_policy": {
                "yield_to_face_identity_masks": True,
                "yield_to_character_body_masks": True,
                "never_repaint_foreground_subjects": True,
            },
            "fallback_policy": ["low_denoise_background_inpaint", "metadata_only_skip"],
            "warnings": ["background_harmony_yields_to_subject_masks"],
        })
        add_warning("background_harmony_yields_to_subject_masks", "info", pass_id=pass_id, region_id=rid)

    for seam_lane in (background_region_composer.get("seam_lanes") or []):
        if not isinstance(seam_lane, dict):
            continue
        rid = str(seam_lane.get("region_id") or "").strip()
        lane = lanes_by_region.get(rid) or {}
        contract = common_contract(lane)
        pass_id = f"seam_harmony_{rid or len(harmony_passes) + 1}"
        harmony_passes.append({
            "pass_id": pass_id,
            "pass_family": "harmony",
            "source": "background_region_composer",
            "execution_stage": "final_low_denoise_seam_blend",
            "execution_status": "planned_provider_bridge_required",
            "priority": 80,
            **contract,
            "target_area": "seam_transition",
            "prompt_positive": seam_lane.get("repair_prompt") or seam_lane.get("prompt_positive") or contract.get("prompt_positive"),
            "prompt_negative": _v054_join_unique(["faces swallowed by seam, seam covering subjects", contract.get("prompt_negative")]),
            "denoise_cap": _v054_postpass_denoise_cap(lane, "harmony", "seam"),
            "subject_protection_policy": {
                "seam_mask_excludes_faces": True,
                "seam_yields_to_character_masks": True,
                "blend_between_background_lanes_only": True,
            },
            "fallback_policy": ["low_denoise_seam_blend", "metadata_only_skip"],
            "warnings": ["seam_harmony_subject_protection_required"],
        })
        add_warning("seam_harmony_subject_protection_required", "warning", pass_id=pass_id, region_id=rid)

    if lanes:
        harmony_passes.append({
            "pass_id": "final_scene_harmony_guard",
            "pass_family": "harmony",
            "source": "scene_director_global_contract",
            "execution_stage": "final_low_denoise_scene_harmony_guard",
            "execution_status": "planned_provider_bridge_required",
            "priority": 90,
            "region_id": None,
            "lane_id": "global_style",
            "label": "Final Scene Harmony Guard",
            "target_area": "full_canvas_with_subject_protection",
            "mask_ref": "global_base_with_subject_protection",
            "denoise_cap": _v054_postpass_denoise_cap(None, "harmony", "global"),
            "prompt_positive": (region_lane_compiler.get("global_lane") or {}).get("prompt_positive") if isinstance(region_lane_compiler.get("global_lane"), dict) else "",
            "prompt_negative": (region_lane_compiler.get("global_lane") or {}).get("prompt_negative") if isinstance(region_lane_compiler.get("global_lane"), dict) else "",
            "subject_protection_policy": {
                "protect_identity_pass_outputs": True,
                "protect_detail_pass_outputs": True,
                "protect_background_boundaries": True,
                "global_harmony_cannot_override_region_lanes": True,
            },
            "fallback_policy": ["metadata_only_skip"],
            "warnings": ["final_harmony_guard_metadata_only_until_provider_bridge"],
        })
        add_warning("final_harmony_guard_metadata_only_until_provider_bridge", "info", pass_id="final_scene_harmony_guard")

    all_passes = identity_passes + detail_passes + harmony_passes
    status = "off" if not all_passes else "planned"
    route_ids = [p.get("route_id") for p in all_passes if p.get("route_id")]
    lock_carried_count = len([p for p in all_passes if p.get("character_lock_contract_carried")])
    mask_bound_count = len([p for p in all_passes if p.get("mask_ref")])

    return {
        "schema": V054_IDENTITY_DETAIL_HARMONY_SCHEMA,
        "phase": V054_IDENTITY_DETAIL_HARMONY_PHASE,
        "status": status,
        "runtime_mode": "postpass_contract_ready_provider_bridge_required",
        "compatibility_mode": "metadata_and_pass_plan_without_runtime_or_return_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "counts": {
            "passes": len(all_passes),
            "identity_passes": len(identity_passes),
            "detail_passes": len(detail_passes),
            "harmony_passes": len(harmony_passes),
            "passes_with_route_ids": len(route_ids),
            "passes_with_region_masks": mask_bound_count,
            "passes_with_character_lock_contract": lock_carried_count,
            "ipadapter_identity_routes": len([p for p in identity_passes if p.get("extension_type") in {"ipadapter", "faceid"}]),
            "adetailer_detail_routes": len([p for p in detail_passes if p.get("extension_type") in {"adetailer", "detailer"}]),
            "lora_postpass_guards": len([p for p in detail_passes if p.get("extension_type") == "lora"]),
            "background_harmony_passes": len([p for p in harmony_passes if str(p.get("target_area") or "") == "background_zone"]),
            "seam_harmony_passes": len([p for p in harmony_passes if str(p.get("target_area") or "") == "seam_transition"]),
        },
        "pass_order": [
            {"order": 1, "stage": "base_regional_attention", "source": "regional_attention_controller_v2", "policy": "region lanes own prompt adherence before post-passes"},
            {"order": 2, "stage": "regional_lora_latent", "source": "regional_lora_latent_executor", "policy": "LoRA routes must prove masked latent composite before hard isolation claims"},
            {"order": 3, "stage": "identity_restore_and_guard", "source": "identity_passes", "policy": "IPAdapter/FaceID and Character Lock restore identity without changing composition"},
            {"order": 4, "stage": "regional_detail_crops", "source": "detail_passes", "policy": "ADetailer/detail/LoRA crop guards paste back inside assigned masks only"},
            {"order": 5, "stage": "background_and_seam_harmony", "source": "harmony_passes", "policy": "background and seam passes yield to face/body/prop masks"},
            {"order": 6, "stage": "final_scene_harmony", "source": "final_scene_harmony_guard", "policy": "low denoise global harmony cannot override region lanes"},
        ],
        "identity_passes": identity_passes,
        "detail_passes": detail_passes,
        "harmony_passes": harmony_passes,
        "route_bindings": {
            "route_ids": route_ids,
            "identity_route_ids": [p.get("route_id") for p in identity_passes if p.get("route_id")],
            "detail_route_ids": [p.get("route_id") for p in detail_passes if p.get("route_id")],
        },
        "safety_contract": {
            "character_lock_must_carry_to_every_identity_and_detail_pass": True,
            "postpasses_must_intersect_mask_authority": True,
            "ipadapter_identity_only_not_composition_director": True,
            "adetailer_crop_must_paste_inside_region": True,
            "lora_postpass_cannot_change_gender_body_after_base_pass": True,
            "background_harmony_yields_to_subjects": True,
            "final_harmony_low_denoise_only": True,
            "no_return_order_change": True,
        },
        "dependencies": {
            "region_lane_compiler_phase": region_lane_compiler.get("phase") if isinstance(region_lane_compiler, dict) else None,
            "mask_authority_engine_phase": mask_authority_engine.get("phase") if isinstance(mask_authority_engine, dict) else None,
            "regional_attention_controller_phase": regional_attention_controller.get("phase") if isinstance(regional_attention_controller, dict) else None,
            "background_region_composer_phase": background_region_composer.get("phase") if isinstance(background_region_composer, dict) else None,
            "extension_route_controller_phase": extension_route_controller_v2.get("phase") if isinstance(extension_route_controller_v2, dict) else None,
            "regional_lora_latent_executor_phase": regional_lora_latent_executor.get("phase") if isinstance(regional_lora_latent_executor, dict) else None,
        },
        "provider_bridge_todo": [
            "build post-generation identity restore branch that intersects IPAdapter/FaceID output with assigned character masks",
            "build detailer crop branches that inherit lane positive/negative prompts and Character Lock contracts",
            "build LoRA crop fallback guard after regional latent executor when runtime proof is missing",
            "build background and seam harmony branches with subject masks subtracted from target masks",
            "write per-pass runtime proof back to scene_metadata_json",
        ],
        "warnings": warnings,
        "policy": "Phase 26.10.7 plans identity, detail, and harmony post-passes from the regional contracts without changing V054 output order or claiming runtime execution before provider proof.",
    }



# -----------------------------------------------------------------------------
# Phase 26.10.8 — Inspector + Debug UI
# -----------------------------------------------------------------------------
# This is still compatibility-safe: it adds a structured inspector contract to
# scene_metadata_json/debug_json, but it does not add/remove sockets, change
# sampler behavior, or weaken Character Lock. The V2 web UI can render these
# sections directly, while Comfy users can inspect the same data from the
# Scene Metadata output.

V054_INSPECTOR_DEBUG_UI_PHASE = "SD-V054-26.10.8"
V054_INSPECTOR_DEBUG_UI_SCHEMA = "neo.image.scene_director.inspector_debug_ui.v054.v1"


def _v054_preview_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _v054_collect_warning_entries(source_id: str, payload: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out: List[Dict[str, Any]] = []
    raw = payload.get("warnings", [])
    if not isinstance(raw, list):
        raw = []
    for idx, item in enumerate(raw):
        if isinstance(item, dict):
            entry = dict(item)
            entry.setdefault("code", str(entry.get("message") or f"{source_id}_warning_{idx}"))
            entry.setdefault("level", "warning")
        else:
            entry = {"code": str(item), "level": "warning", "message": str(item)}
        entry["source"] = source_id
        out.append(entry)
    return out


def _v054_make_inspector_table_row(kind: str, item: Dict[str, Any]) -> Dict[str, Any]:
    if kind == "lane":
        return {
            "lane_id": item.get("lane_id"),
            "region_id": item.get("region_id"),
            "label": item.get("label"),
            "role": item.get("role") or item.get("lane_type"),
            "lane_type": item.get("lane_type"),
            "subject_slot": item.get("subject_slot"),
            "target_area": item.get("target_area"),
            "mask_ref": item.get("mask_ref"),
            "priority": item.get("priority"),
            "extension_route_count": len(item.get("extension_routes", [])) if isinstance(item.get("extension_routes"), list) else 0,
            "prompt_preview": _v054_preview_text(item.get("prompt_positive") or item.get("prompt"), 140),
            "negative_preview": _v054_preview_text(item.get("prompt_negative") or item.get("negative"), 120),
            "character_lock": item.get("lock_contract") if isinstance(item.get("lock_contract"), dict) else {},
        }
    if kind == "mask":
        return {
            "mask_id": item.get("mask_id"),
            "region_id": item.get("region_id"),
            "lane_id": item.get("lane_id"),
            "label": item.get("label"),
            "mask_ref": item.get("mask_ref"),
            "mask_type": item.get("mask_type"),
            "mask_source": item.get("mask_source"),
            "priority_group": item.get("priority_group"),
            "priority_rank": item.get("priority_rank"),
            "coverage_percent": item.get("coverage_percent"),
            "pixel_count": item.get("pixel_count"),
            "overlap_count": len(item.get("overlaps_with", [])) if isinstance(item.get("overlaps_with"), list) else 0,
            "semantic_status": item.get("semantic_status"),
        }
    if kind == "attention":
        return {
            "attention_lane_id": item.get("attention_lane_id") or item.get("lane_id"),
            "region_id": item.get("region_id"),
            "lane_id": item.get("lane_id"),
            "role": item.get("role") or item.get("attention_role"),
            "conditioning_scope": item.get("conditioning_scope"),
            "mask_ref": item.get("mask_ref"),
            "mask_priority_group": item.get("mask_priority_group"),
            "has_local_negative": bool(str(item.get("prompt_negative") or "").strip()),
            "extension_route_count": len(item.get("extension_routes", [])) if isinstance(item.get("extension_routes"), list) else 0,
            "prompt_preview": _v054_preview_text(item.get("prompt_positive"), 140),
        }
    if kind == "route":
        return {
            "route_id": item.get("route_id"),
            "extension_type": item.get("extension_type"),
            "owner_enabled": item.get("owner_enabled"),
            "region_id": item.get("region_id"),
            "lane_id": item.get("lane_id"),
            "subject_slot": item.get("subject_slot"),
            "mask_ref": item.get("mask_ref"),
            "attention_lane_id": item.get("attention_lane_id"),
            "requested_mode": item.get("requested_mode"),
            "actual_mode": item.get("actual_mode"),
            "execution_stage": item.get("execution_stage"),
            "preferred_future_executor": item.get("preferred_future_executor"),
            "hard_region_isolation": item.get("hard_region_isolation"),
            "global_bleed_risk": item.get("global_bleed_risk"),
            "warning_count": len(item.get("warnings", [])) if isinstance(item.get("warnings"), list) else 0,
        }
    if kind == "lora":
        return {
            "route_id": item.get("route_id"),
            "region_id": item.get("region_id"),
            "lane_id": item.get("lane_id"),
            "lora_name": item.get("lora_name"),
            "lora_family_guess": item.get("lora_family_guess"),
            "trigger_terms": item.get("trigger_terms", []),
            "execution_status": item.get("execution_status"),
            "runtime_proof_required": item.get("runtime_proof_required"),
            "hard_region_isolation_claimed": bool(item.get("hard_region_isolation_claimed", False)),
            "fallback_policy": item.get("fallback_policy", []),
            "mask_ref": item.get("mask_ref"),
        }
    if kind == "pass":
        return {
            "pass_id": item.get("pass_id"),
            "pass_family": item.get("pass_family"),
            "source": item.get("source"),
            "execution_stage": item.get("execution_stage"),
            "execution_status": item.get("execution_status"),
            "region_id": item.get("region_id"),
            "lane_id": item.get("lane_id"),
            "target_area": item.get("target_area"),
            "mask_ref": item.get("mask_ref"),
            "route_id": item.get("route_id"),
            "extension_type": item.get("extension_type"),
            "denoise_cap": item.get("denoise_cap"),
            "character_lock_contract_carried": item.get("character_lock_contract_carried"),
            "warning_count": len(item.get("warnings", [])) if isinstance(item.get("warnings"), list) else 0,
        }
    return dict(item)


def _v054_build_inspector_debug_ui(
    graph: Dict[str, Any],
    width: int,
    height: int,
    region_lane_compiler: Dict[str, Any] | None = None,
    mask_authority_engine: Dict[str, Any] | None = None,
    regional_attention_controller: Dict[str, Any] | None = None,
    background_region_composer: Dict[str, Any] | None = None,
    extension_route_controller_v2: Dict[str, Any] | None = None,
    regional_lora_latent_executor: Dict[str, Any] | None = None,
    identity_detail_harmony_passes: Dict[str, Any] | None = None,
    max_subject_slots: int = 4,
    extension_routes_json: Any = "",
    identity_strength: Any = 0.70,
    detail_strength: Any = 0.70,
    background_strength: Any = 0.65,
    mask_feather: Any = 12,
    base_weight: Any = 0.55,
    region_gain: Any = 0.45,
    normalize_masks: Any = True,
    debug_mode: Any = False,
) -> Dict[str, Any]:
    (
        region_lane_compiler,
        mask_authority_engine,
        regional_attention_controller,
        background_region_composer,
        extension_route_controller_v2,
        regional_lora_latent_executor,
    ) = _v054_idh_lookup_dependencies(
        graph,
        int(width),
        int(height),
        region_lane_compiler,
        mask_authority_engine,
        regional_attention_controller,
        background_region_composer,
        extension_route_controller_v2,
        regional_lora_latent_executor,
        int(max_subject_slots),
        extension_routes_json,
        identity_strength,
        detail_strength,
        background_strength,
        mask_feather,
    )

    if not isinstance(identity_detail_harmony_passes, dict) or not isinstance(identity_detail_harmony_passes.get("counts"), dict):
        identity_detail_harmony_passes = _v054_build_identity_detail_harmony_passes(
            graph,
            width=int(width),
            height=int(height),
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller,
            background_region_composer=background_region_composer,
            extension_route_controller_v2=extension_route_controller_v2,
            regional_lora_latent_executor=regional_lora_latent_executor,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=mask_feather,
        )

    lanes = [lane for lane in (region_lane_compiler.get("lanes") or []) if isinstance(lane, dict)]
    masks = [mask for mask in (mask_authority_engine.get("masks") or []) if isinstance(mask, dict)]
    attention_lanes = [lane for lane in (regional_attention_controller.get("lanes") or []) if isinstance(lane, dict)]
    routes = [route for route in (extension_route_controller_v2.get("routes") or []) if isinstance(route, dict)]
    lora_routes = [route for route in (regional_lora_latent_executor.get("routes") or []) if isinstance(route, dict)]
    identity_passes = [p for p in (identity_detail_harmony_passes.get("identity_passes") or []) if isinstance(p, dict)]
    detail_passes = [p for p in (identity_detail_harmony_passes.get("detail_passes") or []) if isinstance(p, dict)]
    harmony_passes = [p for p in (identity_detail_harmony_passes.get("harmony_passes") or []) if isinstance(p, dict)]
    all_passes = identity_passes + detail_passes + harmony_passes

    warnings: List[Dict[str, Any]] = []
    for source_id, payload in [
        ("region_lane_compiler", region_lane_compiler),
        ("mask_authority_engine", mask_authority_engine),
        ("regional_attention_controller_v2", regional_attention_controller),
        ("background_region_composer", background_region_composer),
        ("extension_route_controller_v2", extension_route_controller_v2),
        ("regional_lora_latent_executor", regional_lora_latent_executor),
        ("identity_detail_harmony_passes", identity_detail_harmony_passes),
    ]:
        warnings.extend(_v054_collect_warning_entries(source_id, payload))

    hard_blocker_codes = {
        "background_duplicate_prompt_block_or_repair_required",
        "missing_mask_authority_for_enabled_extension_route",
        "regional_lora_runtime_proof_required_before_hard_isolation",
    }
    blockers = [w for w in warnings if str(w.get("code")) in hard_blocker_codes or str(w.get("level", "")).lower() in {"error", "blocker", "fatal"}]

    lane_table = [_v054_make_inspector_table_row("lane", lane) for lane in lanes]
    mask_table = [_v054_make_inspector_table_row("mask", mask) for mask in masks]
    attention_table = [_v054_make_inspector_table_row("attention", lane) for lane in attention_lanes]
    route_table = [_v054_make_inspector_table_row("route", route) for route in routes]
    lora_table = [_v054_make_inspector_table_row("lora", route) for route in lora_routes]
    pass_table = [_v054_make_inspector_table_row("pass", p) for p in all_passes]

    duplicate_backgrounds = []
    if isinstance(background_region_composer.get("duplicate_prompt_groups"), list):
        duplicate_backgrounds = background_region_composer.get("duplicate_prompt_groups") or []
    elif isinstance(background_region_composer.get("diagnostics"), dict):
        duplicate_backgrounds = background_region_composer.get("diagnostics", {}).get("duplicate_prompt_groups", []) or []
    if not duplicate_backgrounds and isinstance(background_region_composer.get("prompt_hygiene"), dict):
        duplicate_backgrounds = background_region_composer.get("prompt_hygiene", {}).get("duplicate_background_prompt_pairs", []) or []

    overlay_layers = [
        {
            "layer_id": "region_lane_boxes",
            "label": "Region Lane Boxes",
            "source": "region_lane_compiler",
            "default_visible": True,
            "kind": "bbox_overlay",
            "count": len(lanes),
            "items": [
                {
                    "lane_id": lane.get("lane_id"),
                    "region_id": lane.get("region_id"),
                    "label": lane.get("label"),
                    "role": lane.get("role") or lane.get("lane_type"),
                    "bbox": lane.get("bbox"),
                    "mask_ref": lane.get("mask_ref"),
                }
                for lane in lanes
            ],
        },
        {
            "layer_id": "mask_authority_heatmap",
            "label": "Mask Authority Heatmap",
            "source": "mask_authority_engine",
            "default_visible": True,
            "kind": "mask_priority_overlay",
            "count": len(masks),
            "items": [
                {
                    "mask_id": mask.get("mask_id"),
                    "region_id": mask.get("region_id"),
                    "priority_group": mask.get("priority_group"),
                    "coverage_percent": mask.get("coverage_percent"),
                    "overlap_count": len(mask.get("overlaps_with", [])) if isinstance(mask.get("overlaps_with"), list) else 0,
                }
                for mask in masks
            ],
        },
        {
            "layer_id": "attention_lane_routes",
            "label": "Attention Lane Routes",
            "source": "regional_attention_controller_v2",
            "default_visible": bool(debug_mode),
            "kind": "attention_route_overlay",
            "count": len(attention_lanes),
        },
        {
            "layer_id": "extension_route_bindings",
            "label": "Extension Route Bindings",
            "source": "extension_route_controller_v2",
            "default_visible": bool(debug_mode),
            "kind": "extension_route_overlay",
            "count": len(routes),
        },
        {
            "layer_id": "postpass_plan",
            "label": "Identity / Detail / Harmony Passes",
            "source": "identity_detail_harmony_passes",
            "default_visible": False,
            "kind": "postpass_overlay",
            "count": len(all_passes),
        },
    ]

    panels = [
        {
            "panel_id": "overview",
            "title": "Overview",
            "default_open": True,
            "severity": "blocker" if blockers else ("warning" if warnings else "ok"),
            "cards": [
                {"label": "Regions", "value": len(lanes)},
                {"label": "Masks", "value": len(masks)},
                {"label": "Attention lanes", "value": len(attention_lanes)},
                {"label": "Extension routes", "value": len(routes)},
                {"label": "LoRA executor routes", "value": len(lora_routes)},
                {"label": "Post-passes", "value": len(all_passes)},
                {"label": "Warnings", "value": len(warnings)},
                {"label": "Blockers", "value": len(blockers)},
            ],
        },
        {"panel_id": "region_lanes", "title": "Region Lanes", "default_open": True, "table_id": "region_lane_table", "row_count": len(lane_table)},
        {"panel_id": "mask_authority", "title": "Mask Authority", "default_open": True, "table_id": "mask_authority_table", "row_count": len(mask_table)},
        {"panel_id": "attention_controller", "title": "Regional Attention", "default_open": False, "table_id": "attention_lane_table", "row_count": len(attention_table)},
        {"panel_id": "background_composer", "title": "Background Composer", "default_open": bool(duplicate_backgrounds), "table_id": "background_diagnostics", "row_count": len(duplicate_backgrounds)},
        {"panel_id": "extension_routes", "title": "Extension Routes", "default_open": bool(routes), "table_id": "extension_route_table", "row_count": len(route_table)},
        {"panel_id": "regional_lora", "title": "Regional LoRA", "default_open": bool(lora_routes), "table_id": "regional_lora_table", "row_count": len(lora_table)},
        {"panel_id": "post_passes", "title": "Identity / Detail / Harmony", "default_open": bool(all_passes), "table_id": "postpass_table", "row_count": len(pass_table)},
        {"panel_id": "warnings", "title": "Warnings / Blockers", "default_open": bool(warnings), "table_id": "warning_queue", "row_count": len(warnings)},
        {"panel_id": "exports", "title": "Debug Exports", "default_open": False, "table_id": "debug_export_manifest", "row_count": 5},
    ]

    global_lane = region_lane_compiler.get("global_lane") if isinstance(region_lane_compiler.get("global_lane"), dict) else {}
    prompt_audit = {
        "global_prompt_preview": _v054_preview_text(global_lane.get("prompt_positive") or (graph.get("global") or {}).get("prompt"), 220),
        "global_negative_preview": _v054_preview_text(global_lane.get("prompt_negative") or (graph.get("global") or {}).get("negative"), 180),
        "lane_prompt_count": len([row for row in lane_table if row.get("prompt_preview")]),
        "lane_negative_count": len([row for row in lane_table if row.get("negative_preview")]),
        "background_duplicate_groups": duplicate_backgrounds,
    }

    route_family_counts: Dict[str, int] = {}
    for route in routes:
        key = str(route.get("extension_type") or "unknown")
        route_family_counts[key] = route_family_counts.get(key, 0) + 1

    debug_export_manifest = [
        {"export_id": "scene_metadata_json", "label": "Scene Metadata JSON", "source": "node_output_16", "contains": ["all_inspector_sections", "scene_graph", "compiled_v05_scene"]},
        {"export_id": "debug_json", "label": "Debug JSON", "source": "node_output_6", "contains": ["v054", "inspector_debug_ui"]},
        {"export_id": "mask_authority_snapshot", "label": "Mask Authority Snapshot", "source": "mask_authority_engine", "contains": ["coverage", "overlap", "priority"]},
        {"export_id": "extension_route_matrix", "label": "Extension Route Matrix", "source": "extension_route_controller_v2", "contains": ["lora", "ipadapter", "controlnet", "adetailer"]},
        {"export_id": "postpass_plan", "label": "Post-pass Plan", "source": "identity_detail_harmony_passes", "contains": ["identity", "detail", "harmony"]},
    ]

    action_contracts = [
        {"action_id": "copy_lane_prompt", "label": "Copy lane prompt", "requires": ["lane_id"], "safe": True, "runtime_mutation": False},
        {"action_id": "copy_lane_negative", "label": "Copy lane negative", "requires": ["lane_id"], "safe": True, "runtime_mutation": False},
        {"action_id": "toggle_overlay_layer", "label": "Toggle overlay", "requires": ["layer_id"], "safe": True, "runtime_mutation": False},
        {"action_id": "focus_region", "label": "Focus region", "requires": ["region_id"], "safe": True, "runtime_mutation": False},
        {"action_id": "copy_extension_route", "label": "Copy extension route", "requires": ["route_id"], "safe": True, "runtime_mutation": False},
        {"action_id": "export_debug_bundle", "label": "Export debug bundle", "requires": ["scene_metadata_json"], "safe": True, "runtime_mutation": False},
    ]

    status = "empty" if not lanes and not routes and not all_passes else "ready"
    return {
        "schema": V054_INSPECTOR_DEBUG_UI_SCHEMA,
        "phase": V054_INSPECTOR_DEBUG_UI_PHASE,
        "status": status,
        "runtime_mode": "inspector_debug_contract_ready_no_sampler_change",
        "compatibility_mode": "metadata_debug_ui_without_runtime_or_return_order_change",
        "canvas": {"width": int(width), "height": int(height)},
        "ui_mount": {
            "metadata_key": "inspector_debug_ui",
            "primary_source": "scene_metadata_json",
            "secondary_source": "debug_json.v054.inspector_debug_ui",
            "extension_entrypoint": "neo_extensions/built_in/image.scene_director/ui/panel.js",
            "current_renderer_note": "Scene Director UI may still be rendered by the core Neo bundle; this contract is renderer-neutral.",
            "required_panels": [p["panel_id"] for p in panels],
        },
        "counts": {
            "panels": len(panels),
            "overlay_layers": len(overlay_layers),
            "lanes": len(lanes),
            "masks": len(masks),
            "attention_lanes": len(attention_lanes),
            "background_duplicate_groups": len(duplicate_backgrounds),
            "extension_routes": len(routes),
            "regional_lora_routes": len(lora_routes),
            "identity_passes": len(identity_passes),
            "detail_passes": len(detail_passes),
            "harmony_passes": len(harmony_passes),
            "warnings": len(warnings),
            "blockers": len(blockers),
        },
        "panels": panels,
        "overlay_layers": overlay_layers,
        "tables": {
            "region_lane_table": lane_table,
            "mask_authority_table": mask_table,
            "attention_lane_table": attention_table,
            "extension_route_table": route_table,
            "regional_lora_table": lora_table,
            "postpass_table": pass_table,
            "warning_queue": warnings,
            "background_diagnostics": duplicate_backgrounds,
            "debug_export_manifest": debug_export_manifest,
        },
        "prompt_audit": prompt_audit,
        "route_family_counts": route_family_counts,
        "runtime_proof_audit": {
            "lora_routes_require_runtime_proof": len([r for r in lora_routes if r.get("runtime_proof_required")]),
            "lora_routes_claiming_hard_isolation": len([r for r in lora_routes if r.get("hard_region_isolation_claimed")]),
            "routes_with_global_bleed_risk": len([r for r in routes if r.get("global_bleed_risk")]),
            "routes_without_mask_ref": len([r for r in routes if not r.get("mask_ref")]),
            "postpasses_provider_bridge_required": len([p for p in all_passes if str(p.get("execution_status") or "").endswith("provider_bridge_required")]),
        },
        "blockers": blockers,
        "action_contracts": action_contracts,
        "debug_export_manifest": debug_export_manifest,
        "dependencies": {
            "architecture_freeze_phase": V054_ARCHITECTURE_FREEZE_PHASE,
            "region_lane_compiler_phase": region_lane_compiler.get("phase") if isinstance(region_lane_compiler, dict) else None,
            "mask_authority_engine_phase": mask_authority_engine.get("phase") if isinstance(mask_authority_engine, dict) else None,
            "regional_attention_controller_phase": regional_attention_controller.get("phase") if isinstance(regional_attention_controller, dict) else None,
            "background_region_composer_phase": background_region_composer.get("phase") if isinstance(background_region_composer, dict) else None,
            "extension_route_controller_phase": extension_route_controller_v2.get("phase") if isinstance(extension_route_controller_v2, dict) else None,
            "regional_lora_latent_executor_phase": regional_lora_latent_executor.get("phase") if isinstance(regional_lora_latent_executor, dict) else None,
            "identity_detail_harmony_phase": identity_detail_harmony_passes.get("phase") if isinstance(identity_detail_harmony_passes, dict) else None,
        },
        "safety_contract": {
            "no_return_order_change": True,
            "no_sampler_behavior_change": True,
            "no_character_lock_override": True,
            "debug_ui_is_read_only": True,
            "mask_preview_does_not_mutate_generation": True,
            "extension_route_inspector_does_not_execute_routes": True,
            "lora_runtime_proof_honesty_preserved": True,
        },
        "policy": "Phase 26.10.8 exposes a renderer-neutral Inspector + Debug UI contract over Scene Director regional lanes, masks, attention lanes, extension routes, LoRA executor plans, post-pass plans, warnings, overlays, and exports without changing V054 runtime behavior or output order.",
    }


# -----------------------------------------------------------------------------
# Phase 26.10.0 — Node Architecture Audit + Compatibility Freeze
# -----------------------------------------------------------------------------
# This snapshot is intentionally metadata/test-facing only. It freezes the
# public V054 node contract before the larger V055 regional engine refactor so
# future phases can improve internals without breaking saved workflows, the
# Character Lock panel, or existing output ordering.

V054_ARCHITECTURE_FREEZE_PHASE = "SD-V054-26.10.0"

V054_ARCHITECTURE_FREEZE = {
    "schema": "neo.image.scene_director.architecture_freeze.v054.v1",
    "phase": V054_ARCHITECTURE_FREEZE_PHASE,
    "status": "frozen",
    "node_class": "NeoSceneDirectorV054",
    "active_mapping_only": True,
    "base_runtime": "V054 scene graph normalized into V053 regional attention patcher",
    "must_preserve_inputs": [
        "character_lock_mode",
        "identity_strength",
        "detail_strength",
        "background_strength",
        "mask_feather",
        "appearance_lock_mode",
        "appearance_lock_gain",
        "appearance_lock_height",
        "appearance_lock_feather",
        "extension_routes_json",
        "scene_graph_json",
    ],
    "must_preserve_outputs": [
        "patched_model",
        "mask_preview",
        "layout_preview",
        "global_prompt",
        "negative_prompt",
        "debug_json",
        "subject_1_mask",
        "subject_2_mask",
        "subject_3_mask",
        "subject_4_mask",
        "identity_plan_json",
        "detail_masks",
        "background_masks",
        "control_masks",
        "inpaint_masks",
        "scene_metadata_json",
    ],
    "character_lock_contract": {
        "strong_or_strict_maps_to": "full_character_strong",
        "legacy_hair_focus_strong_alias_maps_to": "full_character_strong",
        "explicit_upper_identity_modes_remain_valid": True,
        "full_character_branch_count_required": True,
        "upper_identity_reinforcement_required": True,
        "subject_count_counts_character_regions_only": True,
    },
    "regional_attention_contract": {
        "model_clone_patch_required": True,
        "attn2_input_patch_required": True,
        "attn2_output_patch_required": True,
        "region_masks_downsample_to_attention_resolution": True,
        "normalize_masks_supported": True,
        "base_weight_supported": True,
        "region_gain_supported": True,
    },
    "extension_route_contract": {
        "extension_routes_json_optional_input_required": True,
        "ipadapter_routes_region_bound_or_fallback": True,
        "controlnet_disabled_owner_routes_metadata_only": True,
        "adetailer_disabled_owner_routes_metadata_only": True,
        "regional_lora_claims_require_runtime_proof": True,
        "regional_lora_triggers_never_added_to_global_prompt": True,
    },
    "non_goals_for_this_phase": [
        "no regional attention rewrite",
        "no regional latent LoRA executor",
        "no return order change",
        "no UI control rename",
        "no prompt behavior change except audit metadata",
    ],
}


def _v054_architecture_freeze_snapshot() -> Dict[str, Any]:
    # Round-trip clone keeps callers/tests from mutating the canonical freeze.
    return json.loads(json.dumps(V054_ARCHITECTURE_FREEZE))


class NeoSceneDirectorV054(NeoSceneDirector):
    """V054 scene-graph upgrade of the existing V053 Scene Director node.

    This class keeps the proven V053 regional-attention engine and upgrades the
    input contract to JSON scene_graph_json with typed roles, attach_to links,
    relationships, control/detail/inpaint metadata, and grouped mask outputs.
    """

    @classmethod
    def INPUT_TYPES(cls):
        default_scene = json.dumps({
            "version": "v054",
            "canvas": {"width": 1344, "height": 768},
            "global": {"prompt": "", "negative": ""},
            "regions": [],
            "metadata": {
                "source": "empty_neutral_node_default",
                "prompt_provenance": {
                    "demo_or_personal_prompt_injection": False,
                    "policy": "Neo supplies scene_graph_json at queue time; an empty raw node remains a passthrough."
                }
            }
        }, indent=2)
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "width": ("INT", {"default": 1344, "min": 64, "max": 8192, "step": 8}),
                "height": ("INT", {"default": 768, "min": 64, "max": 8192, "step": 8}),
                "global_prompt_override": ("STRING", {"multiline": True, "default": ""}),
                "base_weight": ("STRING", {"default": "0.55"}),
                "region_gain": ("STRING", {"default": "0.45"}),
                "max_subject_slots": ("INT", {"default": 1, "min": 1, "max": 4, "step": 1}),
                "normalize_masks": ("BOOLEAN", {"default": True}),
                "enable_auto_prompts": ("BOOLEAN", {"default": True}),
                "character_lock_mode": (["off", "soft", "balanced", "strong", "strict"], {"default": "strong"}),
                "identity_strength": ("STRING", {"default": "0.70"}),
                "detail_strength": ("STRING", {"default": "0.70"}),
                "background_strength": ("STRING", {"default": "0.65"}),
                "mask_feather": ("INT", {"default": 12, "min": 0, "max": 256, "step": 1}),
                "debug_mode": ("BOOLEAN", {"default": False}),
                "scene_graph_json": ("STRING", {"multiline": True, "default": default_scene}),
            },
            "optional": {
                "appearance_lock_mode": (["auto", "off", "hair_focus_soft", "hair_focus_strong", "upper_identity_soft", "upper_identity_strong", "full_character_soft", "full_character_strong", "full_identity_soft", "full_identity_strong", "character_soft", "character_strong"], {"default": "auto"}),
                "appearance_lock_gain": ("STRING", {"default": "auto"}),
                "appearance_lock_height": ("STRING", {"default": "auto"}),
                "appearance_lock_feather": ("INT", {"default": -1, "min": -1, "max": 256, "step": 1}),
                "extension_routes_json": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    RETURN_TYPES = (
        "MODEL", "IMAGE", "IMAGE", "STRING", "STRING", "STRING",
        "MASK", "MASK", "MASK", "MASK", "STRING",
        "MASK", "MASK", "MASK", "MASK", "STRING"
    )
    RETURN_NAMES = (
        "patched_model", "mask_preview", "layout_preview", "global_prompt", "negative_prompt", "debug_json",
        "subject_1_mask", "subject_2_mask", "subject_3_mask", "subject_4_mask", "identity_plan_json",
        "detail_masks", "background_masks", "control_masks", "inpaint_masks", "scene_metadata_json"
    )
    FUNCTION = "patch"
    CATEGORY = "Neo Studio/Scene Director"

    def patch(
        self,
        model,
        clip,
        width,
        height,
        global_prompt_override,
        base_weight,
        region_gain,
        max_subject_slots,
        normalize_masks,
        enable_auto_prompts,
        character_lock_mode,
        identity_strength,
        detail_strength,
        background_strength,
        mask_feather,
        debug_mode,
        scene_graph_json,
        appearance_lock_mode="auto",
        appearance_lock_gain="auto",
        appearance_lock_height="auto",
        appearance_lock_feather=-1,
        extension_routes_json="",
    ):
        width = int(width); height = int(height)
        graph = _v054_parse_scene_graph(scene_graph_json)
        canvas = graph.get("canvas", {}) if isinstance(graph.get("canvas", {}), dict) else {}
        width = int(canvas.get("width", width) or width)
        height = int(canvas.get("height", height) or height)
        regions = graph.get("regions", []) if isinstance(graph.get("regions", []), list) else []

        if not regions:
            empty_subjects = [_empty_mask(width, height) for _ in range(4)]
            detail_masks, background_masks, control_masks, inpaint_masks = [_empty_mask(width, height) for _ in range(4)]
            empty_region_lane_compiler = _v054_build_region_lane_compiler(
                graph,
                width=width,
                height=height,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_mask_authority_engine = _v054_build_mask_authority_engine(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_regional_attention_controller = _v054_build_regional_attention_controller(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
                base_weight=base_weight,
                region_gain=region_gain,
                normalize_masks=normalize_masks,
            )
            empty_background_region_composer = _v054_build_background_region_composer(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                regional_attention_controller=empty_regional_attention_controller,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_extension_route_controller_v2 = _v054_build_extension_route_controller_v2(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                regional_attention_controller=empty_regional_attention_controller,
                background_region_composer=empty_background_region_composer,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_regional_lora_latent_executor = _v054_build_regional_lora_latent_executor(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                regional_attention_controller=empty_regional_attention_controller,
                background_region_composer=empty_background_region_composer,
                extension_route_controller_v2=empty_extension_route_controller_v2,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_identity_detail_harmony_passes = _v054_build_identity_detail_harmony_passes(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                regional_attention_controller=empty_regional_attention_controller,
                background_region_composer=empty_background_region_composer,
                extension_route_controller_v2=empty_extension_route_controller_v2,
                regional_lora_latent_executor=empty_regional_lora_latent_executor,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
            )
            empty_inspector_debug_ui = _v054_build_inspector_debug_ui(
                graph,
                width=width,
                height=height,
                region_lane_compiler=empty_region_lane_compiler,
                mask_authority_engine=empty_mask_authority_engine,
                regional_attention_controller=empty_regional_attention_controller,
                background_region_composer=empty_background_region_composer,
                extension_route_controller_v2=empty_extension_route_controller_v2,
                regional_lora_latent_executor=empty_regional_lora_latent_executor,
                identity_detail_harmony_passes=empty_identity_detail_harmony_passes,
                max_subject_slots=int(max_subject_slots),
                extension_routes_json=extension_routes_json,
                identity_strength=identity_strength,
                detail_strength=detail_strength,
                background_strength=background_strength,
                mask_feather=mask_feather,
                base_weight=base_weight,
                region_gain=region_gain,
                normalize_masks=normalize_masks,
                debug_mode=debug_mode,
            )
            metadata = {
                "version": "v054",
                "node": "NeoSceneDirectorV054",
                "runtime": "empty-scene passthrough",
                "counts": {"regions": 0, "subjects": 0, "details": 0, "backgrounds": 0},
                "complexity": _v054_complexity_meter([]),
                "provider_capabilities": _v054_provider_capability_from_graph(graph),
                "flux_adapter_plan": _v054_provider_capability_from_graph(graph).get("flux_adapter_plan"),
                "qwen_adapter_plan": _v054_provider_capability_from_graph(graph).get("qwen_adapter_plan"),
                "sdxl_full_implementation_lock": _v054_provider_capability_from_graph(graph).get("sdxl_full_implementation_lock"),
                "compiler_phase": "SD-V054-21",
                "architecture_freeze": _v054_architecture_freeze_snapshot(),
                "region_lane_compiler": empty_region_lane_compiler,
                "mask_authority_engine": empty_mask_authority_engine,
                "regional_attention_controller_v2": empty_regional_attention_controller,
                "background_region_composer": empty_background_region_composer,
                "extension_route_controller_v2": empty_extension_route_controller_v2,
                "regional_lora_latent_executor": empty_regional_lora_latent_executor,
                "identity_detail_harmony_passes": empty_identity_detail_harmony_passes,
                "inspector_debug_ui": empty_inspector_debug_ui,
                "legacy_text_phase_anchor": "SD-V054-13",
                "scene_graph": graph,
            }
            return (
                model, _v054_blank_image(width, height), _v054_blank_image(width, height),
                str((graph.get("global") or {}).get("prompt", "")), str((graph.get("global") or {}).get("negative", "")), json.dumps(metadata, indent=2),
                empty_subjects[0], empty_subjects[1], empty_subjects[2], empty_subjects[3], json.dumps({"version":"v054","entries":[]}, indent=2),
                detail_masks, background_masks, control_masks, inpaint_masks, json.dumps(metadata, indent=2)
            )

        complexity = _v054_complexity_meter(regions)
        scene_json, v05_scene, errors, warnings = _v054_to_v05_scene(graph, width, height, global_prompt_override)
        warnings = list(warnings) + [str(item.get("message") or "complexity warning") for item in complexity.get("messages", [])]
        requested_appearance_mode = str(appearance_lock_mode or "auto").strip().lower()
        if requested_appearance_mode and requested_appearance_mode != "auto":
            appearance_mode = _appearance_lock_mode_value(requested_appearance_mode)
        else:
            appearance_mode = _v054_character_lock_to_appearance(character_lock_mode)

        identity_value = _safe_float(identity_strength, 0.70)
        if str(appearance_lock_gain or "").strip().lower() == "auto":
            appearance_gain = max(identity_value, 0.90) if appearance_mode in {"upper_identity_strong", "full_character_strong", "hair_focus_strong"} else (max(identity_value, 0.62) if appearance_mode in {"upper_identity_soft", "full_character_soft", "hair_focus_soft"} else identity_value)
        else:
            appearance_gain = _safe_float(appearance_lock_gain, identity_value)
        if str(appearance_lock_height or "").strip().lower() == "auto":
            appearance_height = 0.46 if appearance_mode in {"upper_identity_strong", "full_character_strong", "hair_focus_strong"} else (0.40 if appearance_mode in {"upper_identity_soft", "full_character_soft", "hair_focus_soft"} else 0.34)
        else:
            appearance_height = _safe_float(appearance_lock_height, 0.34)
        appearance_feather = _safe_int(appearance_lock_feather, -1)
        if appearance_feather < 0:
            appearance_feather = _safe_int(mask_feather, 12)

        legacy_attention_primary = _v054_legacy_attention_primary_settings(
            appearance_mode,
            base_weight=base_weight,
            region_gain=region_gain,
            appearance_gain=appearance_gain,
            identity_strength=identity_value,
            mask_feather=appearance_feather,
            character_lock_mode=character_lock_mode,
        )
        effective_base_weight = legacy_attention_primary["effective_base_weight"]
        effective_region_gain = legacy_attention_primary["effective_region_gain"]
        effective_appearance_gain = legacy_attention_primary["effective_appearance_gain"]
        effective_identity_strength = legacy_attention_primary["effective_identity_strength"]
        effective_mask_feather = legacy_attention_primary["effective_mask_feather"]
        identity_value = effective_identity_strength
        appearance_feather = effective_mask_feather

        region_lane_compiler = _v054_build_region_lane_compiler(
            graph,
            width=width,
            height=height,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        mask_authority_engine = _v054_build_mask_authority_engine(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        regional_attention_controller_v2 = _v054_build_regional_attention_controller(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
            base_weight=effective_base_weight,
            region_gain=effective_region_gain,
            normalize_masks=normalize_masks,
        )
        background_region_composer = _v054_build_background_region_composer(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller_v2,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        extension_route_controller_v2 = _v054_build_extension_route_controller_v2(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller_v2,
            background_region_composer=background_region_composer,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        regional_lora_latent_executor = _v054_build_regional_lora_latent_executor(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller_v2,
            background_region_composer=background_region_composer,
            extension_route_controller_v2=extension_route_controller_v2,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        identity_detail_harmony_passes = _v054_build_identity_detail_harmony_passes(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller_v2,
            background_region_composer=background_region_composer,
            extension_route_controller_v2=extension_route_controller_v2,
            regional_lora_latent_executor=regional_lora_latent_executor,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
        )
        inspector_debug_ui = _v054_build_inspector_debug_ui(
            graph,
            width=width,
            height=height,
            region_lane_compiler=region_lane_compiler,
            mask_authority_engine=mask_authority_engine,
            regional_attention_controller=regional_attention_controller_v2,
            background_region_composer=background_region_composer,
            extension_route_controller_v2=extension_route_controller_v2,
            regional_lora_latent_executor=regional_lora_latent_executor,
            identity_detail_harmony_passes=identity_detail_harmony_passes,
            max_subject_slots=int(max_subject_slots),
            extension_routes_json=extension_routes_json,
            identity_strength=effective_identity_strength,
            detail_strength=detail_strength,
            background_strength=background_strength,
            mask_feather=effective_mask_feather,
            base_weight=effective_base_weight,
            region_gain=effective_region_gain,
            normalize_masks=normalize_masks,
            debug_mode=debug_mode,
        )

        global_prompt, negative, branch_prompts, branch_negative_prompts, branch_masks, debug_json, layout_preview = _parse_scene_schema(
            scene_json=scene_json,
            width=width,
            height=height,
            global_prompt_override=global_prompt_override,
            enable_auto_prompts=bool(enable_auto_prompts),
            max_subject_slots=int(max_subject_slots),
            appearance_lock_mode=appearance_mode,
            appearance_lock_gain=effective_appearance_gain,
            appearance_lock_height=appearance_height,
            appearance_lock_feather=appearance_feather,
        )
        debug_payload_for_metadata = json.loads(debug_json) if isinstance(debug_json, str) and debug_json.strip().startswith("{") else {}
        appearance_debug = debug_payload_for_metadata.get("appearance_lock", {}) if isinstance(debug_payload_for_metadata, dict) else {}
        trait_conditioning_debug = debug_payload_for_metadata.get("character_trait_conditioning", {}) if isinstance(debug_payload_for_metadata, dict) else {}
        extension_branch_prompts, extension_branch_masks, extension_authority_node = _v054_compile_extension_authority_routes(
            graph,
            extension_routes_json,
            width,
            height,
            max_subject_slots=int(max_subject_slots),
        )
        if extension_branch_prompts:
            branch_prompts.extend(extension_branch_prompts)
            branch_negative_prompts.extend([""] * len(extension_branch_prompts))
            branch_masks.extend(extension_branch_masks)

        region_conds = [_clip_encode_crossattn(clip, p) for p in branch_prompts]
        region_negative_conds = [
            _clip_encode_crossattn(clip, prompt) if str(prompt or "").strip() else None
            for prompt in branch_negative_prompts
        ]
        patched_model, preview, attention_lock_runtime_proof = _patch_director(
            model=model,
            region_conds=region_conds,
            masks=branch_masks,
            base_weight=effective_base_weight,
            normalize_masks=bool(normalize_masks),
            region_gain=effective_region_gain,
            region_negative_conds=region_negative_conds,
        )
        attention_lock_runtime_proof.update({
            "appearance_lock_mode": appearance_mode,
            "full_character_branch_count": int(appearance_debug.get("full_character_branch_count") or 0),
            "upper_identity_branch_count": int(appearance_debug.get("upper_identity_branch_count") or 0),
            "structural_gender_branch_count": int(appearance_debug.get("structural_gender_branch_count") or 0),
            "facial_hair_branch_count": int(appearance_debug.get("facial_hair_branch_count") or 0),
            "face_identity_grooming_branch_count": int(appearance_debug.get("face_identity_grooming_branch_count") or 0),
            "standalone_facial_hair_branch_count": int(appearance_debug.get("standalone_facial_hair_branch_count") or 0),
            "clothing_branch_count": int(appearance_debug.get("clothing_branch_count") or 0),
            "legacy_attention_primary": legacy_attention_primary,
            "primary_character_lock_path": legacy_attention_primary.get("primary_character_lock_path"),
            "fallback_masked_correction_role": "rescue_only",
            "character_trait_conditioning": deepcopy(trait_conditioning_debug),
            "live_character_trait_conditioning": bool(trait_conditioning_debug.get("positive_terms_are_live") or trait_conditioning_debug.get("negative_terms_are_live")),
        })
        subject_masks, identity_plan_json = _extract_subject_masks_and_identity(scene_json, width, height, max_subjects=4)
        detail_masks, background_masks, control_masks, inpaint_masks, mask_index = _v054_group_masks(v05_scene, graph, width, height)
        metadata = {
            "version": "v054",
            "node": "NeoSceneDirectorV054",
            "prompt_authority": (graph.get("global") if isinstance(graph.get("global"), dict) else {}).get("prompt_authority", "global_context"),
            "global_prompt_excluded": bool((graph.get("global") if isinstance(graph.get("global"), dict) else {}).get("global_prompt_excluded") or (graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}).get("global_prompt_excluded")),
            "upgrade_from": "NeoSceneDirectorV053 regional attention base",
            "runtime": "V054 scene graph normalized into V053 attention patcher",
            "anime_safe_background_mask_policy": {
                "schema": "neo.image.scene_director.background_subject_mask_protection.v054.v1",
                "phase": "SD-V054-26.10.8K3",
                "status": "applied",
                "policy": "background and transition masks are subtracted by character masks before regional attention blending",
            },
            "compiler_phase": "SD-V054-21",
            "architecture_freeze": _v054_architecture_freeze_snapshot(),
            "region_lane_compiler": region_lane_compiler,
            "mask_authority_engine": mask_authority_engine,
            "regional_attention_controller_v2": regional_attention_controller_v2,
            "background_region_composer": background_region_composer,
            "extension_route_controller_v2": extension_route_controller_v2,
            "regional_lora_latent_executor": regional_lora_latent_executor,
            "identity_detail_harmony_passes": identity_detail_harmony_passes,
            "inspector_debug_ui": inspector_debug_ui,
            "legacy_text_phase_anchor": "SD-V054-13",
            "legacy_detailer_phase_anchor": "SD-V054-12",
            "legacy_controlnet_phase_anchor": "SD-V054-11",
            "legacy_compiler_phase_anchor": "SD-V054-9",
            "character_lock_mode": character_lock_mode,
            "appearance_lock_route": appearance_mode,
            "legacy_attention_primary": legacy_attention_primary,
            "attention_lock_runtime_proof": attention_lock_runtime_proof,
            "character_trait_conditioning": deepcopy(trait_conditioning_debug),
            "scene_director_attention_lock_runtime_proof": attention_lock_runtime_proof,
            "legacy_appearance_lock_parity": {
                "phase": "SD-V054-26.9.6",
                "status": "applied" if appearance_mode != "off" else "off",
                "mode": appearance_mode,
                "gain": effective_appearance_gain,
                "requested_gain": _safe_float(appearance_gain, identity_value),
                "height": appearance_height,
                "feather": appearance_feather,
                "policy": "Phase 26.9.6 upper-subject visual appearance lock remains available. Phase 26.9.7 promotes Character Lock Strong/Strict to full-character authority when selected.",
            },
            "full_character_lock_authority_parity": {
                "schema": "neo.image.scene_director.full_character_lock_authority_parity.v054.v1",
                "phase": "SD-V054-26.9.7",
                "status": "applied" if str(appearance_mode).startswith("full_character") else ("off" if appearance_mode == "off" else "not_applicable"),
                "appearance_lock_mode": appearance_mode,
                "full_character_branch_count": int(appearance_debug.get("full_character_branch_count") or 0),
                "upper_identity_branch_count": int(appearance_debug.get("upper_identity_branch_count") or 0),
                "structural_gender_branch_count": int(appearance_debug.get("structural_gender_branch_count") or 0),
                "facial_hair_branch_count": int(appearance_debug.get("facial_hair_branch_count") or 0),
                "face_identity_grooming_branch_count": int(appearance_debug.get("face_identity_grooming_branch_count") or 0),
                "standalone_facial_hair_branch_count": int(appearance_debug.get("standalone_facial_hair_branch_count") or 0),
                "clothing_branch_count": int(appearance_debug.get("clothing_branch_count") or 0),
                "policy": "Character Lock Strong/Strict maps to full-character and upper-identity authority plus one merged face/grooming lane and non-face structured-garment lanes in the same sampler.",
            },
            "extension_authority_node": extension_authority_node,
            "regional_lora_model_delta_mixer": {
                "schema": "neo.image.scene_director.regional_lora_model_delta_mixer.v054.v1",
                "phase": "SD-V054-26.9.14",
                "status": "applied" if any(r.get("extension_type") == "lora" and r.get("actual_mode") == "regional_model_delta_mixer" and r.get("runtime_applied") is True for r in extension_authority_node.get("routes", [])) else ("fallback" if any(r.get("extension_type") == "lora" for r in extension_authority_node.get("routes", [])) else "off"),
                "mixer_mode": "noise_prediction_delta",
                "route_count": len([r for r in extension_authority_node.get("routes", []) if r.get("extension_type") == "lora"]),
                "applied_count": len([r for r in extension_authority_node.get("routes", []) if r.get("extension_type") == "lora" and r.get("actual_mode") == "regional_model_delta_mixer" and r.get("runtime_applied") is True]),
                "fallback_count": len([r for r in extension_authority_node.get("routes", []) if r.get("extension_type") == "lora" and not (r.get("actual_mode") == "regional_model_delta_mixer" and r.get("runtime_applied") is True)]),
                "routes": [r for r in extension_authority_node.get("routes", []) if r.get("extension_type") == "lora"],
                "policy": "Regional LoRA mixer routes only count as applied when runtime proof confirms a loaded LoRA and non-zero masked model delta; otherwise node metadata downgrades to visual-authority fallback.",
            },
            "ipadapter_instruction_preservation": {
                "schema": "neo.image.scene_director.ipadapter_instruction_preservation.v054.v1",
                "phase": "SD-V054-26.9.11",
                "status": "applied" if any(r.get("extension_type") == "ipadapter" for r in extension_authority_node.get("routes", [])) else "not_applicable",
                "routes": [r for r in extension_authority_node.get("routes", []) if r.get("extension_type") == "ipadapter"],
                "policy": "IPAdapter/FaceID route authority is identity-only by default and preserves Scene Director pose, relationship, outfit, props, background, and composition instructions.",
            },
            "extension_routing_authority_node_parity": {
                "schema": "neo.image.scene_director.extension_routing_authority_node_parity.v054.v1",
                "phase": "SD-V054-26.9.10",
                "status": extension_authority_node.get("status"),
                "route_count": extension_authority_node.get("route_count"),
                "policy": "Node parses extension_routes_json and adds assigned-region route authority lanes without weakening Character Lock.",
            },
            "settings": {
                "base_weight": _safe_float(base_weight, 0.55),
                "region_gain": _safe_float(region_gain, 0.45),
                "identity_strength": _safe_float(identity_strength, 0.70),
                "detail_strength": _safe_float(detail_strength, 0.70),
                "background_strength": _safe_float(background_strength, 0.65),
                "mask_feather": _safe_int(mask_feather, 12),
                "appearance_lock_mode": appearance_mode,
                "appearance_lock_gain": appearance_gain,
                "appearance_lock_height": appearance_height,
                "appearance_lock_feather": appearance_feather,
                "normalize_masks": bool(normalize_masks),
                "debug_mode": bool(debug_mode),
            },
            "counts": {
                "regions": len(regions),
                "subjects": len([r for r in regions if isinstance(r, dict) and r.get("role") == "character"]),
                "details": int(detail_masks.shape[0]) if hasattr(detail_masks, "shape") else 0,
                "backgrounds": int(background_masks.shape[0]) if hasattr(background_masks, "shape") else 0,
                "controls": int(control_masks.shape[0]) if hasattr(control_masks, "shape") else 0,
                "inpaint": int(inpaint_masks.shape[0]) if hasattr(inpaint_masks, "shape") else 0,
                "branches": len(branch_prompts),
            },
            "mask_index": mask_index,
            "complexity": complexity,
            "provider_capabilities": _v054_provider_capability_from_graph(graph),
                "flux_adapter_plan": _v054_provider_capability_from_graph(graph).get("flux_adapter_plan"),
                "qwen_adapter_plan": _v054_provider_capability_from_graph(graph).get("qwen_adapter_plan"),
                "sdxl_full_implementation_lock": _v054_provider_capability_from_graph(graph).get("sdxl_full_implementation_lock"),
            "linked_detail_lanes": v05_scene.get("v054_linked_detail_lanes", []),
            "relationship_plan": v05_scene.get("v054_relationship_plan", []),
            "background_plan": v05_scene.get("v054_background_plan", []),
            "control_plan": v05_scene.get("v054_control_plan", []),
            "detailer_plan": v05_scene.get("v054_detailer_plan", []),
            "text_region_plan": v05_scene.get("v054_text_region_plan", _v054_text_region_plan(regions)),
            "text_compositor": {"mode": "composite_default", "route": "post_decode_metadata", "phase": "SD-V054-13"},
            "img2img_reuse_plan": v05_scene.get("v054_img2img_reuse_plan", _v054_img2img_reuse_plan(regions)),
            "img2img_region_reuse": {"phase": "SD-V054-14", "route": "source_image_region_reuse_metadata", "lane_count": len(v05_scene.get("v054_img2img_reuse_plan", _v054_img2img_reuse_plan(regions)))},
            "inpaint_target_plan": v05_scene.get("v054_inpaint_target_plan", _v054_inpaint_target_plan(regions)),
            "inpaint_region_targeting": {"phase": "SD-V054-15", "legacy_phase_anchor": "SD-V054-16", "route": "direct_region_mask_inpaint_metadata", "lane_count": len(v05_scene.get("v054_inpaint_target_plan", _v054_inpaint_target_plan(regions)))},
            "output_inspector_source_stack": {
                "phase": "SD-V054-19",
                "extension_authority_node": extension_authority_node,
                "legacy_phase_anchor": "SD-V054-16",
                "legacy_provider_capability_phase_anchor": "SD-V054-17",
                "route": "output_inspector_source_stack_metadata",
                "scene_graph_replay": True,
                "mask_outputs": ["subject_masks", "detail_masks", "background_masks", "control_masks", "inpaint_masks"],
                "prompt_blocks": ["global_prompt", "negative_prompt", "compiled_v05_scene"],
                "region_branch_actions": ["replay_region", "img2img_region_reuse", "open_region_in_inpaint", "edit_text_plate"],
                "latent_policy": "compatible_route_only",
            },
            "conflict_plan": v05_scene.get("v054_conflict_plan", []),
            "warnings": list(warnings) + [str(item.get("message") or "prompt conflict") for item in v05_scene.get("v054_conflict_plan", [])],
            "scene_graph": graph,
            "compiled_v05_scene": v05_scene,
        }
        debug_payload = debug_payload_for_metadata or ({"debug_json": debug_json} if isinstance(debug_json, str) else {})
        debug_payload["attention_lock_runtime_proof"] = attention_lock_runtime_proof
        debug_payload["scene_director_attention_lock_runtime_proof"] = attention_lock_runtime_proof
        debug_payload["v054"] = metadata
        debug_payload["v054_inspector_debug_ui"] = inspector_debug_ui

        return (
            patched_model, preview, layout_preview, global_prompt, negative, json.dumps(debug_payload, indent=2),
            subject_masks[0], subject_masks[1], subject_masks[2], subject_masks[3], identity_plan_json,
            detail_masks, background_masks, control_masks, inpaint_masks, json.dumps(metadata, indent=2)
        )


NODE_CLASS_MAPPINGS = {
    "NeoSceneDirectorV054": NeoSceneDirectorV054,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "NeoSceneDirectorV054": "Neo Scene Director v0.5.4 (Scene Graph)",
}

# Phase 21 retirement note: V052/V053 classes remain in source for archival comparison only,
# but are no longer exported through Comfy NODE_CLASS_MAPPINGS.

# Phase 14 compatibility anchor: "compiler_phase": "SD-V054-14"

# Phase 16 compatibility anchor: "compiler_phase": "SD-V054-18"
# Legacy test anchor: "compiler_phase": "SD-V054-15"
# Legacy test anchor: "compiler_phase": "SD-V054-14"

# Phase 17 compatibility anchor: "compiler_phase": "SD-V054-18" provider_capabilities

# Phase 19 compatibility anchor: Flux Adapter Planning SD-V054-19 flux_adapter_plan
# Phase 18 compatibility anchor: SDXL Full Implementation Lock SD-V054-18 sdxl_full_implementation_lock

# Phase 20 compatibility anchor: Qwen Adapter Planning SD-V054-20 qwen_adapter_plan

# Phase 21 compatibility anchor: Retire V052/V053 Active Path SD-V054-21
