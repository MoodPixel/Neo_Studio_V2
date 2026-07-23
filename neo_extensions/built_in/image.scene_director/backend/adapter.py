from __future__ import annotations

from typing import Any
import json
import re
from pathlib import Path

from pathlib import Path

from .prompt_authority import (
    PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY,
    build_prompt_authority_contract,
    normalize_prompt_authority,
)

def studio_data_path(name: str, legacy_rel: str | None = None):
    root = Path(__file__).resolve().parents[4]
    path = root / 'neo_data' / name
    path.mkdir(parents=True, exist_ok=True)
    return path

SUPPORTED_FAMILIES = {'sd', 'sdxl', 'sdxl_sd', 'sd15', 'sd1.5', 'sd_1_5', 'sd1_5'}
BLOCKED_FAMILIES = {'flux', 'qwen', 'qwen_image_edit', 'zimage'}

DEFAULT_CONTRACTS = {
    'enabled': True,
    'use_node_auto_prompts': False,
    'count_contract': 'exactly {count} visible subjects, one subject per character region, no extra subjects',
    'subject_contract': 'one complete subject inside this region, not merged, not duplicated',
    'negative_contract': 'extra people, missing subject, wrong number of subjects, merged bodies, fused faces',
    'style_merge': 'use Neo main prompt as the scene style and composition intent',
}



def _safe_identity_slug(name: Any) -> str:
    value = re.sub(r'[^a-zA-Z0-9._ -]+', '', str(name or '').strip())
    value = re.sub(r'\s+', '_', value).strip('._- ')
    return value[:80] or 'identity_profile'


def _identity_profile_dirs() -> list[Path]:
    """Return all identity profile stores used by V2 and legacy V1 bridges.

    V2 UI routes save profiles under neo_data/scene_director/identity_profiles,
    while the early migrated Scene Director backend still looked only in the old
    neo_data/identity_profiles folder. That path drift meant trigger words such
    as JoongChar could be present in region prompts but never resolve to the
    saved profile reference images, leaving scene_director_identity_units empty.
    """
    if studio_data_path is None:
        return []
    candidates: list[Path] = []
    for name, legacy in (
        ('scene_director/identity_profiles', None),
        ('identity_profiles', 'identity_profiles'),
    ):
        try:
            path = studio_data_path(name, legacy_rel=legacy)
        except Exception:
            continue
        if path not in candidates:
            candidates.append(path)
    return candidates


def _identity_profile_dir() -> Path | None:
    dirs = _identity_profile_dirs()
    return dirs[0] if dirs else None


def _find_identity_profile_file(profile_id: Any) -> Path | None:
    name = str(profile_id or '').strip()
    if not name:
        return None
    slug = _safe_identity_slug(name)
    for root in _identity_profile_dirs():
        if root is None or not root.exists():
            continue
        direct = root / f'{slug}.json'
        if direct.exists():
            return direct
        try:
            for path in root.glob('*.json'):
                if path.stem == slug or path.stem == name or path.name == name:
                    return path
        except Exception:
            continue
    return None


def _load_identity_profile(profile_id: Any) -> dict[str, Any]:
    path = _find_identity_profile_file(profile_id)
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get('profile'), dict):
        profile = dict(data.get('profile') or {})
        if not profile.get('profile_name') and data.get('name'):
            profile['profile_name'] = data.get('name')
        if not profile.get('id'):
            profile['id'] = str(profile_id or path.stem)
        return profile
    return data if isinstance(data, dict) else {}


def _iter_identity_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths: list[Path] = []
    for root in _identity_profile_dirs():
        if root is None or not root.exists():
            continue
        try:
            paths.extend(sorted(root.glob('*.json')))
        except Exception:
            continue
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        profile = dict(data.get('profile') or data) if isinstance(data, dict) else {}
        if not profile:
            continue
        if not profile.get('id'):
            profile['id'] = str(data.get('name') or path.stem) if isinstance(data, dict) else path.stem
        if not profile.get('profile_name'):
            profile['profile_name'] = str(data.get('name') or profile.get('name') or path.stem) if isinstance(data, dict) else path.stem
        profile['_profile_slug'] = path.stem
        key = str(profile.get('id') or profile.get('profile_name') or path.stem).lower()
        if key in seen:
            continue
        seen.add(key)
        profiles.append(profile)
    return profiles


def _profile_trigger_tokens(profile: dict[str, Any]) -> list[str]:
    tokens: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                add(item)
            return
        text = str(value or '').strip()
        if not text:
            return
        for item in re.split(r'[\n,]+', text):
            item = item.strip()
            if item and item not in tokens:
                tokens.append(item)

    add(profile.get('trigger_words'))
    add(profile.get('trigger'))
    add(profile.get('trigger_word'))
    add(profile.get('tokens'))
    # V1 allowed practical profile-name style routing; avoid very generic names.
    for key in ('profile_name', 'name', 'id', '_profile_slug'):
        value = str(profile.get(key) or '').strip()
        if value and len(value) >= 3 and value.lower() not in {'person 1', 'person 2', 'region 1', 'region 2'}:
            add(value)
    return tokens


def _load_identity_profile_for_region(region: dict[str, Any], raw_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(region, dict):
        return {}
    raw_profile = raw_profile if isinstance(raw_profile, dict) else {}
    profile_id = str(region.get('character_profile_id') or region.get('identity_profile_id') or region.get('profile_id') or raw_profile.get('id') or '').strip()
    if profile_id:
        loaded = _load_identity_profile(profile_id)
        if loaded:
            return {**loaded, **raw_profile}
    # V1 practical fallback: a Character Profile trigger token in the region prompt
    # should resolve the saved profile even when the UI only copied trigger words.
    haystack = ' '.join([
        str(region.get('prompt') or ''),
        str(region.get('positive') or ''),
        str(region.get('label') or ''),
        str(region.get('character_profile_name') or ''),
        str(region.get('identity_profile_name') or ''),
        str(region.get('profile_name') or ''),
        str(region.get('identity_profile_trigger_words') or ''),
        str((region.get('identity') or {}).get('trigger_words') if isinstance(region.get('identity'), dict) else ''),
    ]).lower()
    if not haystack.strip():
        return raw_profile
    for profile in _iter_identity_profiles():
        for token in _profile_trigger_tokens(profile):
            low = token.lower().strip()
            if len(low) < 3:
                continue
            if low in haystack:
                return {**profile, **raw_profile}
    return raw_profile


def _profile_reference_images(profile: dict[str, Any], region: dict[str, Any] | None = None) -> list[str]:
    region = region if isinstance(region, dict) else {}
    refs = profile.get('reference_images') if isinstance(profile.get('reference_images'), list) else profile.get('image_names')
    if not isinstance(refs, list):
        refs = region.get('image_names') if isinstance(region.get('image_names'), list) else region.get('reference_images')
    if not isinstance(refs, list):
        refs = []
    out = [str(item or '').strip() for item in refs if str(item or '').strip()]
    first = str(profile.get('image_name') or region.get('image_name') or region.get('reference_image') or '').strip()
    if first and first not in out and not first.startswith('BOUND_TO_NEO_IPADAPTER_SLOT_'):
        out.insert(0, first)
    return out

def _clamp_float(value: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'enabled'}


def _normalize_mode(value: Any) -> str:
    return str(value or 'txt2img').strip().lower() or 'txt2img'


def _payload_has_controlnet_guide(payload: dict[str, Any]) -> bool:
    units = payload.get('controlnet_units')
    if isinstance(units, list) and any(isinstance(unit, dict) and unit.get('enabled') is not False for unit in units):
        return True
    if _truthy(payload.get('controlnet_enabled')) or _truthy(payload.get('controlnet')):
        return True
    for index in range(1, 5):
        if _truthy(payload.get(f'controlnet_{index}_enabled')) or _truthy(payload.get(f'controlnet_unit_{index}_enabled')):
            return True
    return False


def _normalize_mask_refine_policy(payload: dict[str, Any], scene: dict[str, Any], mode: str) -> dict[str, Any]:
    raw = scene.get('mask_refine') if isinstance(scene.get('mask_refine'), dict) else {}
    if not raw and isinstance(payload.get('scene_director_mask_refine'), dict):
        raw = payload.get('scene_director_mask_refine') or {}
    requested = str(raw.get('mode') or payload.get('scene_director_mask_refine_mode') or 'auto').strip().lower() or 'auto'
    enabled = _truthy(raw.get('enabled')) or _truthy(payload.get('scene_director_mask_refine_enabled'))
    has_controlnet = _payload_has_controlnet_guide(payload)
    source = 'region_box'
    supported = False
    reason = 'disabled'
    message = 'Character Mask Refinement is off; region boxes remain the effective mask source.'
    mode = _normalize_mode(mode)
    if enabled:
        if mode == 'img2img':
            supported = True
            source = 'source_image_detection'
            reason = 'source_image_available'
            message = 'Character Mask Refinement requested for img2img; source-image detection/mask refinement is visible in payload metadata.'
        elif mode == 'inpaint':
            supported = True
            source = 'source_image_and_inpaint_mask'
            reason = 'source_image_and_mask_available'
            message = 'Character Mask Refinement requested for inpaint; source image plus inpaint mask are the intended detection boundary.'
        elif mode == 'txt2img' and has_controlnet:
            supported = True
            source = 'controlnet_guided_mask'
            reason = 'controlnet_guide_available'
            message = 'Character Mask Refinement requested for txt2img with ControlNet guidance; guide-driven masks are the intended refinement source.'
        elif mode == 'txt2img':
            supported = False
            source = 'region_box'
            reason = 'txt2img_no_detectable_source'
            message = 'Plain txt2img has no source image to detect; Character Mask Refinement falls back to region boxes unless ControlNet/pose guidance is active.'
        else:
            supported = False
            source = 'region_box'
            reason = f'unsupported_mode_{mode}'
            message = f'Character Mask Refinement is not supported for {mode}; region boxes remain active.'
    return {
        'enabled': bool(enabled),
        'requested_mode': requested,
        'supported': bool(supported),
        'workflow_mode': mode,
        'controlnet_guide_detected': bool(has_controlnet),
        'source': source,
        'effective_mask_source': source if supported else 'region_box',
        'fallback': bool(enabled and not supported),
        'reason': reason,
        'message': message,
        'implementation_stage': 'metadata_guarded_single_pass',
    }


def _infer_checkpoint_variant(checkpoint_name: Any, family: Any = '') -> str:
    family_value = str(family or '').strip().lower()
    checkpoint = str(checkpoint_name or '').strip().lower()
    if family_value in {'sd15', 'sd1.5', 'sd_1_5', 'sd1_5', 'sd'}:
        return 'sd15'
    if family_value == 'sdxl':
        return 'sdxl'
    sd15_markers = ('sd15', 'sd1.5', 'sd_1_5', '1.5', 'v1-5', 'v1_5', 'anything', 'dreamshaper', 'deliberate', 'revanimated', 'majicmix', 'realisticvision')
    sdxl_markers = ('sdxl', 'xl', 'pony', 'juggernautxl', 'realvisxl', 'albedobase', 'animagine-xl')
    if any(marker in checkpoint for marker in sd15_markers):
        return 'sd15'
    if any(marker in checkpoint for marker in sdxl_markers):
        return 'sdxl'
    return 'checkpoint_sd'


def _render_contract(template: Any, count: int) -> str:
    text = str(template or '').strip()
    return text.replace('{count}', str(count))


def _contracts_from_scene(scene: dict[str, Any]) -> dict[str, Any]:
    raw = scene.get('contracts') if isinstance(scene.get('contracts'), dict) else {}
    contracts = dict(DEFAULT_CONTRACTS)
    contracts.update({k: v for k, v in raw.items() if k in contracts})
    contracts['enabled'] = raw.get('enabled', contracts['enabled']) is not False
    contracts['use_node_auto_prompts'] = bool(raw.get('use_node_auto_prompts', contracts.get('use_node_auto_prompts', False)))
    return contracts


def _unique_csv(parts: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for chunk in str(part or '').split(','):
            item = chunk.strip()
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                out.append(item)
    return ', '.join(out)


def _prompt_text(value: Any) -> str:
    return ' '.join(str(value or '').replace('\n', ' ').split()).strip()


def _normalize_pass_target(value: Any) -> str:
    raw = str(value or 'both').strip().lower()
    return raw if raw in {'base', 'refine', 'both'} else 'both'


def _style_enabled_for_base(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get('style_enabled') is False:
        return False
    target = _normalize_pass_target(payload.get('style_pass_target') or 'both')
    return target in {'base', 'both'}


def _expand_style_template(style_prompt: str, base_prompt: str) -> tuple[str, bool]:
    style_prompt = _prompt_text(style_prompt)
    base_prompt = _prompt_text(base_prompt)
    if not style_prompt:
        return base_prompt, False
    if '{prompt}' in style_prompt:
        return _prompt_text(style_prompt.replace('{prompt}', base_prompt)), True
    return _unique_csv([base_prompt, style_prompt]), False


def _style_context_from_template(style_prompt: str) -> str:
    """Return style-only context without duplicating the full scene prompt into every region."""
    text = _prompt_text(style_prompt)
    if not text:
        return ''
    text = text.replace('{prompt}', ' ')
    text = text.replace(' . ', ', ').replace('..', '.').replace(',,', ',')
    return _unique_csv([text])




def _compact_region_context(text: str, max_chars: int = 420, max_items: int = 18) -> tuple[str, bool]:
    """Keep optional region context short so region identity/position prompts remain first.

    Scene Director compiles per-region prompts separately. If a long global/style
    prompt is prepended to every region, CLIP token limits can truncate the
    region-specific prompt and make masks look inactive. This helper keeps the
    region context dynamic/user-authored, but compact and safe as a suffix.
    """
    text = _prompt_text(text)
    if not text:
        return '', False
    chunks = [chunk.strip() for chunk in text.replace(';', ',').split(',') if chunk.strip()]
    if not chunks:
        words = text.split()
        compact = ' '.join(words[:70])
        return compact, compact != text
    kept: list[str] = []
    used = 0
    seen: set[str] = set()
    truncated = False
    for chunk in chunks:
        key = chunk.lower()
        if key in seen:
            continue
        next_len = len(chunk) + (2 if kept else 0)
        if len(kept) >= max_items or used + next_len > max_chars:
            truncated = True
            break
        kept.append(chunk)
        seen.add(key)
        used += next_len
    compact = ', '.join(kept)
    return compact, truncated or compact != text


def _attention_wrap(text: str, weight: float) -> str:
    text = _prompt_text(text)
    if not text:
        return ''
    weight = _clamp_float(weight, 0.35, 0.0, 2.0)
    if abs(weight - 1.0) < 0.001:
        return text
    return f"({text}:{weight:.2f})"


def _compose_scene_director_prompt_context(payload: dict[str, Any], scene: dict[str, Any], contracts: dict[str, Any], count: int) -> dict[str, Any]:
    """Compose user-controlled global/style context for Scene Director.

    Nothing here hardcodes a style. It only consumes Neo main prompt and the active
    Style Stack payload. The region context mode controls whether a dynamic copy of
    that context is also appended to per-region prompts, because the Scene Director
    model patch compiles region prompts separately from sampler conditioning.
    """
    global_data = scene.get('global') if isinstance(scene.get('global'), dict) else {}
    prompt_authority = normalize_prompt_authority(
        scene.get('prompt_authority')
        or global_data.get('prompt_authority')
        or payload.get('scene_director_prompt_authority')
    )
    main_prompt = _prompt_text(global_data.get('prompt') or payload.get('positive') or '')
    main_negative = _prompt_text(global_data.get('negative_prompt') or payload.get('negative') or '')
    style_positive = _prompt_text(payload.get('style_positive') or '')
    style_negative = _prompt_text(payload.get('style_negative') or '')
    style_allowed = _style_enabled_for_base(payload)
    prompt_extension_merge = payload.get('prompt_extension_merge') if isinstance(payload.get('prompt_extension_merge'), dict) else {}
    scene_director_interop = prompt_extension_merge.get('scene_director_interop') if isinstance(prompt_extension_merge.get('scene_director_interop'), dict) else {}
    style_stack_metadata = (prompt_extension_merge.get('extension_metadata') or {}).get('style_stack') if isinstance(prompt_extension_merge.get('extension_metadata'), dict) else {}
    style_stack_applied_globally = bool(
        scene_director_interop.get('style_stack_applied')
        or (isinstance(style_stack_metadata, dict) and style_stack_metadata.get('enabled'))
    )
    style_stack_global_only = bool(global_data.get('style_stack_global_only') or payload.get('scene_director_style_stack_global_only') or style_stack_applied_globally)
    style_stack_original_prompt = _prompt_text(
        global_data.get('style_stack_original_positive_prompt')
        or payload.get('scene_director_style_stack_original_positive')
        or prompt_extension_merge.get('original_positive')
        or ''
    )
    style_stack_original_negative = _prompt_text(
        global_data.get('style_stack_original_negative_prompt')
        or payload.get('scene_director_style_stack_original_negative')
        or prompt_extension_merge.get('original_negative')
        or ''
    )
    apply_style_to_region_refinement = bool(payload.get('scene_director_style_stack_apply_to_region_refinement') or payload.get('scene_director_apply_global_style_to_region_refinement'))
    region_context_main_prompt = style_stack_original_prompt if style_stack_global_only and style_stack_original_prompt else main_prompt

    if style_allowed and style_positive:
        effective_global_base, template_expanded = _expand_style_template(style_positive, main_prompt)
        style_context = _style_context_from_template(style_positive)
    else:
        effective_global_base, template_expanded, style_context = main_prompt, False, ''

    effective_global = effective_global_base
    if contracts.get('enabled') is not False:
        effective_global = _unique_csv([
            effective_global,
            _render_contract(contracts.get('style_merge'), count),
            _render_contract(_contract_template_for_render(contracts.get('count_contract')), count),
        ])
    effective_negative = _unique_csv([main_negative, style_negative if style_allowed else '', _render_contract(contracts.get('negative_contract'), count) if contracts.get('enabled') is not False else ''])
    if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        # Scene Director-only deliberately keeps only local region prompts and
        # contracts in the regional lanes. The Neo core prompt remains stored in
        # the payload for replay, but is excluded from conditioning here.
        effective_global = ''
        effective_negative = ''

    raw_context = scene.get('prompt_context') if isinstance(scene.get('prompt_context'), dict) else {}
    mode = str(payload.get('scene_director_region_context_mode') or raw_context.get('mode') or 'global_and_style').strip().lower()
    if mode not in {'off', 'global_only', 'style_only', 'global_and_style'}:
        mode = 'global_and_style'
    enabled = payload.get('scene_director_region_context_enabled')
    if enabled is None:
        enabled = raw_context.get('enabled')
    enabled = enabled is not False and mode != 'off' and prompt_authority != PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY
    if prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY:
        mode = 'off'
    weight = _clamp_float(payload.get('scene_director_region_context_weight') if payload.get('scene_director_region_context_weight') is not None else raw_context.get('weight'), 0.35, 0.0, 2.0)
    if mode == 'global_and_style':
        # Use the user-authored global/style prompt before Scene Director contracts.
        # When Style Stack is active, keep its additions on the global Scene
        # Director path only; per-region context falls back to the original main
        # prompt so regional prompts and identity locks do not inherit style text.
        region_context = region_context_main_prompt if style_stack_global_only else effective_global_base
    elif mode == 'global_only':
        region_context = region_context_main_prompt
    elif mode == 'style_only':
        region_context = style_context
    else:
        region_context = ''
    region_context, region_context_truncated = _compact_region_context(region_context)
    if not region_context:
        enabled = False

    region_refinement_global_prompt = effective_global
    region_refinement_negative_prompt = effective_negative
    region_refinement_policy = 'styled_global_allowed'
    if style_stack_global_only and not apply_style_to_region_refinement:
        region_refinement_global_prompt = style_stack_original_prompt or main_prompt
        region_refinement_negative_prompt = style_stack_original_negative or main_negative
        region_refinement_policy = 'global_style_blocked_for_region_refinement'

    prompt_authority_contract = build_prompt_authority_contract(
        {
            'prompt_authority': prompt_authority,
            'region_context': {'enabled': enabled, 'mode': mode, 'weight': weight},
            'global_context_routing': payload.get('global_context_routing') if isinstance(payload.get('global_context_routing'), dict) else {
                'positive': payload.get('scene_director_global_context_route_positive', True),
                'negative': payload.get('scene_director_global_context_route_negative', True),
                'style': payload.get('scene_director_global_context_route_style', True),
            },
        },
        global_positive=main_prompt,
        global_negative=main_negative,
        style_positive=style_positive,
        region_context_weight=weight,
    )
    return {
        'main_prompt': main_prompt,
        'main_negative': main_negative,
        'style_positive': style_positive,
        'style_negative': style_negative,
        'style_allowed': bool(style_allowed),
        'style_template_expanded': bool(template_expanded),
        'effective_global_prompt': effective_global,
        'effective_negative_prompt': effective_negative,
        'region_context_enabled': bool(enabled),
        'region_context_mode': mode,
        'region_context_weight': weight,
        'region_context_text': region_context if enabled else '',
        'region_context_prompt': _attention_wrap(region_context, weight) if enabled else '',
        'region_context_position': 'suffix',
        'region_context_truncated': bool(region_context_truncated) if enabled else False,
        'style_stack_global_only': bool(style_stack_global_only),
        'style_stack_apply_to_region_refinement': bool(apply_style_to_region_refinement),
        'region_refinement_global_prompt': region_refinement_global_prompt,
        'region_refinement_negative_prompt': region_refinement_negative_prompt,
        'region_refinement_policy': region_refinement_policy,
        'prompt_authority': prompt_authority,
        'prompt_authority_contract': prompt_authority_contract,
    }


def _region_type(region: dict[str, Any]) -> str:
    # Phase 26.9.2: the old default treated unknown roles such as held_prop as
    # character subjects. That poisoned subject slots and routed IPAdapter/LoRA
    # masks to subject_3/subject_4 in two-character scenes.
    value = str((region.get('type') or region.get('role') or region.get('region_role') or 'object') if isinstance(region, dict) else 'object').strip().lower()
    if value in {'person', 'subject', 'character', 'main_subject'}:
        return 'character'
    if value in {'object', 'detail', 'detail_lane', 'prop', 'held_prop', 'weapon', 'item', 'hair_detail'}:
        return 'object'
    if value in {'background', 'transition_effect', 'seam', 'style'}:
        return 'background' if value in {'transition_effect', 'seam'} else value
    return 'object'


def _is_character_region(region: dict[str, Any]) -> bool:
    return _region_type(region) == 'character'


def _contract_template_for_render(template: Any) -> str:
    text = str(template or '').strip()
    old_default = 'exactly {count} visible subjects, one subject per enabled region, no extra subjects'
    if text == old_default:
        return DEFAULT_CONTRACTS['count_contract']
    return text


def _scene_region_feather(region: dict[str, Any]) -> int:
    # Tighter default masks reduce cross-region trait bleed when characters overlap.
    # Users can still override by adding feather/mask_feather to region data later.
    explicit = region.get('feather', region.get('mask_feather')) if isinstance(region, dict) else None
    if explicit not in (None, ''):
        return int(round(_clamp_float(explicit, 8.0, 0.0, 64.0)))
    region_type = _region_type(region)
    if region_type == 'character':
        return 8
    if region_type == 'object':
        return 10
    return 18


def _extract_bleed_traits(prompt: Any) -> list[str]:
    text = str(prompt or '')
    if not text.strip():
        return []
    keywords = (
        'hair', 'skin', 'hoodie', 'shirt', 'shorts', 'suit', 'jacket', 'pants', 'jogger',
        'spectacles', 'glasses', 'beard', 'stubble', 'lipstick', 'rose', 'flower',
        'tall', 'short', 'skinny', 'dark', 'fair', 'light', 'pink', 'red', 'black', 'yellow',
        'curly', 'spiky', 'chinese', 'indian', 'sri lankan', 'slman', 'joong'
    )
    traits: list[str] = []
    for chunk in text.replace(';', ',').split(','):
        item = chunk.strip()
        low = item.lower()
        if item and any(keyword in low for keyword in keywords):
            traits.append(item)
    return traits[:12]


def _anti_bleed_negative_for_region(region: dict[str, Any], character_regions: list[dict[str, Any]]) -> str:
    if not _is_character_region(region):
        return ''
    own_id = str(region.get('id') or '')
    traits: list[str] = []
    for other in character_regions:
        if str(other.get('id') or '') == own_id:
            continue
        traits.extend(_extract_bleed_traits(other.get('prompt')))
    return _unique_csv(traits)


def _region_has_identity_reference(region: dict[str, Any]) -> bool:
    if not isinstance(region, dict):
        return False
    if bool(region.get('ipadapter')) or bool(region.get('character_profile_enabled')):
        return True
    if str(region.get('reference') or 'off').strip().lower() not in {'', 'off', 'none', 'false'}:
        return True
    profile = region.get('character_profile') or region.get('identity_profile') or region.get('profile')
    if isinstance(profile, dict) and (profile.get('image_name') or profile.get('image_names') or profile.get('reference_images')):
        return True
    profile_id = str(region.get('character_profile_id') or region.get('identity_profile_id') or region.get('profile_id') or '').strip()
    if profile_id and _load_identity_profile(profile_id):
        return True
    matched_profile = _load_identity_profile_for_region(region, profile if isinstance(profile, dict) else {})
    if matched_profile and _profile_reference_images(matched_profile, region):
        return True
    for key in ('character_profile_id', 'character_profile_name', 'identity_profile_id', 'identity_profile_name', 'profile_id', 'profile_name', 'image_name', 'reference_image'):
        if str(region.get(key) or '').strip():
            return True
    images = region.get('image_names') or region.get('reference_images')
    return isinstance(images, list) and any(str(item or '').strip() for item in images)


def _identity_unit_from_region(region: dict[str, Any], region_index: int) -> dict[str, Any] | None:
    if not isinstance(region, dict) or not _region_has_identity_reference(region):
        return None
    profile = region.get('character_profile') or region.get('identity_profile') or region.get('profile')
    if not isinstance(profile, dict):
        profile = {}
    profile = _load_identity_profile_for_region(region, profile)
    profile_id = str(region.get('character_profile_id') or region.get('identity_profile_id') or region.get('profile_id') or profile.get('id') or '').strip()
    image_names = _profile_reference_images(profile, region)
    if not image_names:
        return {
            'uid': str(profile.get('uid') or profile.get('id') or profile_id or region.get('id') or f'scene_identity_region_{region_index}'),
            'profile_id': profile_id,
            'profile_name': str(profile.get('profile_name') or profile.get('name') or region.get('character_profile_name') or region.get('identity_profile_name') or region.get('label') or f'Region {region_index} Profile'),
            'region_id': str(region.get('id') or ''),
            'region_index': region_index,
            'label': str(region.get('label') or f'Region {region_index}'),
            'missing_reference_image': True,
            'source': 'scene_director_character_profile_region',
        }
    mode = str(profile.get('ipadapter_mode') or profile.get('mode') or region.get('ipadapter_mode') or region.get('mode') or 'faceid').strip().lower() or 'faceid'
    if mode == 'ipadapter':
        mode = 'standard'
    if mode not in {'standard', 'faceid'}:
        mode = 'standard'
    return {
        'uid': str(profile.get('uid') or profile.get('id') or profile_id or region.get('id') or f'scene_identity_region_{region_index}'),
        'profile_id': str(profile.get('id') or profile_id or ''),
        'profile_name': str(profile.get('profile_name') or profile.get('name') or region.get('character_profile_name') or region.get('identity_profile_name') or region.get('label') or f'Region {region_index} Profile'),
        'mode': mode,
        'model': str(profile.get('ipadapter_model') or profile.get('model') or region.get('ipadapter_model') or region.get('ipadapter_name') or '').strip(),
        'clip_vision': str(profile.get('clip_vision_model') or profile.get('clip_vision') or region.get('ipadapter_clip_vision') or region.get('clip_vision') or '').strip(),
        'faceid_preset': str(profile.get('faceid_preset') or region.get('faceid_preset') or '').strip(),
        'faceid_provider': str(profile.get('faceid_provider') or region.get('faceid_provider') or '').strip(),
        'faceid_lora_strength': profile.get('faceid_lora_strength', region.get('faceid_lora_strength')),
        'weight_faceidv2': profile.get('weight_faceidv2', region.get('weight_faceidv2', region.get('ipadapter_weight'))),
        'weight': profile.get('weight', region.get('ipadapter_weight')),
        'start_at': profile.get('start_at', region.get('ipadapter_start_at')),
        'end_at': profile.get('end_at', region.get('ipadapter_end_at')),
        'weight_user_override': bool(profile.get('weight_user_override') or region.get('weight_user_override') or region.get('ipadapter_weight_user_override')),
        'weight_faceidv2_user_override': bool(profile.get('weight_faceidv2_user_override') or region.get('weight_faceidv2_user_override') or profile.get('weight_user_override') or region.get('weight_user_override') or region.get('ipadapter_weight_user_override')),
        'start_at_user_override': bool(profile.get('start_at_user_override') or region.get('start_at_user_override') or region.get('ipadapter_start_at_user_override')),
        'end_at_user_override': bool(profile.get('end_at_user_override') or region.get('end_at_user_override') or region.get('ipadapter_end_at_user_override')),
        'scope_mode': str(profile.get('scope_mode') or profile.get('ipadapter_scope_mode') or region.get('scope_mode') or region.get('ipadapter_scope_mode') or 'identity_only').strip() or 'identity_only',
        'image_name': image_names[0],
        'image_names': image_names,
        'region_id': str(region.get('id') or ''),
        'region_index': region_index,
        'label': str(region.get('label') or f'Region {region_index}'),
        # Final subject_slot/attn output is resolved after all regions are
        # normalized. Region indexes include background/detail lanes and are not
        # valid subject slots.
        'subject_slot': None,
        'attn_mask_output_index': None,
        'source': 'scene_director_character_profile_region',
    }


def normalize_scene_director_state(extension_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = extension_state if isinstance(extension_state, dict) else {}
    regions = state.get('regions') if isinstance(state.get('regions'), list) else []
    active_regions: list[dict[str, Any]] = []
    for index, raw in enumerate(regions):
        if not isinstance(raw, dict):
            continue
        rect = raw.get('rect') if isinstance(raw.get('rect'), dict) else {}
        x = _clamp_float(rect.get('x'), 0.0, 0.0, 1.0)
        y = _clamp_float(rect.get('y'), 0.0, 0.0, 1.0)
        w = _clamp_float(rect.get('w'), 0.33, 0.02, 1.0)
        h = _clamp_float(rect.get('h'), 1.0, 0.02, 1.0)
        if x + w > 1.0:
            x = max(0.0, 1.0 - w)
        if y + h > 1.0:
            y = max(0.0, 1.0 - h)
        raw_profile = raw.get('character_profile') if isinstance(raw.get('character_profile'), dict) else (raw.get('identity_profile') if isinstance(raw.get('identity_profile'), dict) else {})
        raw_profile_id = str(raw.get('character_profile_id') or raw.get('identity_profile_id') or raw.get('profile_id') or raw_profile.get('id') or '').strip()
        loaded_profile = _load_identity_profile(raw_profile_id) if raw_profile_id and not _profile_reference_images(raw_profile, raw) else {}
        merged_profile = {**loaded_profile, **raw_profile} if isinstance(raw_profile, dict) else loaded_profile
        region = {
            'id': str(raw.get('id') or f'region_{index + 1}'),
            'label': str(raw.get('label') or f'Region {index + 1}'),
            'type': _region_type(raw),
        'role': str(raw.get('role') or raw.get('type') or '').strip(),
            'enabled': raw.get('enabled') is not False,
            'visible': raw.get('visible') is not False,
            'prompt': str(raw.get('prompt') or '').strip(),
            'negative_prompt': str(raw.get('negative_prompt') or '').strip(),
            'character_traits': raw.get('character_traits') if isinstance(raw.get('character_traits'), dict) else {},
            'trait_lock': raw.get('trait_lock') if isinstance(raw.get('trait_lock'), dict) else {},
            'strength': _clamp_float(raw.get('strength'), 1.0, 0.0, 2.0),
            'reference': str(raw.get('reference') or 'off'),
            'reference_note': str(raw.get('reference_note') or raw.get('reference_image') or raw.get('image_name') or ''),
            'image_name': str(raw.get('image_name') or raw.get('reference_image') or '').strip(),
            'image_names': raw.get('image_names') if isinstance(raw.get('image_names'), list) else (raw.get('reference_images') if isinstance(raw.get('reference_images'), list) else []),
            'character_profile': merged_profile if isinstance(merged_profile, dict) else {},
            'character_profile_id': raw_profile_id,
            'character_profile_name': str(raw.get('character_profile_name') or raw.get('identity_profile_name') or raw.get('profile_name') or (merged_profile.get('profile_name') if isinstance(merged_profile, dict) else '') or (merged_profile.get('name') if isinstance(merged_profile, dict) else '') or '').strip(),
            'character_profile_enabled': bool(raw.get('character_profile_enabled') or raw.get('identity_profile_enabled') or raw_profile_id),
            'ipadapter_model': str(raw.get('ipadapter_model') or raw.get('ipadapter_name') or ''),
            'ipadapter_clip_vision': str(raw.get('ipadapter_clip_vision') or raw.get('clip_vision') or 'CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors'),
            'ipadapter_weight': _clamp_float(raw.get('ipadapter_weight'), 0.52, 0.0, 2.0),
            'ipadapter_start_at': _clamp_float(raw.get('ipadapter_start_at'), 0.05, 0.0, 1.0),
            'ipadapter_end_at': _clamp_float(raw.get('ipadapter_end_at'), 0.75, 0.0, 1.0),
            'pose': str(raw.get('pose') or 'off'),
            'ipadapter': bool(raw.get('ipadapter')),
            'ipadapter_slot': max(1, min(8, int(_clamp_float(raw.get('ipadapter_slot') or raw.get('ipadapterSlot') or (index + 1), index + 1, 1.0, 8.0)))),
            'ipadapter_use_region_mask': raw.get('ipadapter_use_region_mask') is not False,
            'ipadapter_weight_mode': str(raw.get('ipadapter_weight_mode') or 'slot_default').strip() or 'slot_default',
            'lora': bool(raw.get('lora')),
            'lora_slot': max(1, min(8, int(_clamp_float(raw.get('lora_slot') or raw.get('loraSlot') or (index + 1), index + 1, 1.0, 8.0)))),
            'lora_weight_mode': str(raw.get('lora_weight_mode') or 'slot_default').strip() or 'slot_default',
            'lora_strength': _clamp_float(raw.get('lora_strength'), 0.8, -4.0, 4.0),
            'rect': {'x': x, 'y': y, 'w': w, 'h': h},
            'loras': raw.get('loras') if isinstance(raw.get('loras'), list) else [],
            'character_lock_correction': raw.get('character_lock_correction') if isinstance(raw.get('character_lock_correction'), dict) else {},
            'character_lock_correction_enabled': raw.get('character_lock_correction_enabled'),
            'character_lock_gender_family': raw.get('character_lock_gender_family'),
            'character_lock_positive_text': raw.get('character_lock_positive_text'),
            'character_lock_negative_text': raw.get('character_lock_negative_text'),
            'character_lock_correction_denoise': raw.get('character_lock_correction_denoise'),
            'character_lock_correction_steps': raw.get('character_lock_correction_steps'),
        }
        if region['enabled'] and region['visible'] and (region['prompt'] or _region_has_identity_reference(region)):
            active_regions.append(region)
    return {
        'enabled': bool(state.get('enabled')),
        'family': str(state.get('family') or '').strip().lower(),
        'size': state.get('size') if isinstance(state.get('size'), dict) else {},
        'global': state.get('global') if isinstance(state.get('global'), dict) else {},
        'prompt_authority': normalize_prompt_authority(
            state.get('prompt_authority')
            or (state.get('global') if isinstance(state.get('global'), dict) else {}).get('prompt_authority')
        ),
        'contracts': state.get('contracts') if isinstance(state.get('contracts'), dict) else {},
        'mask_refine': state.get('mask_refine') if isinstance(state.get('mask_refine'), dict) else {},
        'regions': regions,
        'active_regions': active_regions,
        'active_region_count': len(active_regions),
        'active_character_count': len([region for region in active_regions if _is_character_region(region)]),
    }


def build_v052_scene_json(scene: dict[str, Any], width: int, height: int, mask_refine_policy: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    active = scene.get('active_regions') if isinstance(scene.get('active_regions'), list) else []
    character_regions = [region for region in active if _is_character_region(region)]
    count = len(character_regions)
    contracts = _contracts_from_scene(scene)
    contracts_enabled = contracts.get('enabled') is not False
    mask_policy = mask_refine_policy if isinstance(mask_refine_policy, dict) else {'enabled': False, 'effective_mask_source': 'region_box'}
    prompt_context = _compose_scene_director_prompt_context(payload if isinstance(payload, dict) else {}, scene, contracts, count)
    global_style = prompt_context.get('effective_global_prompt', '')

    subjects: list[dict[str, Any]] = []
    ipadapter: dict[str, Any] = {}
    subject_slot = 0
    subject_contract = _render_contract(contracts.get('subject_contract'), count) if contracts_enabled else ''
    for index, region in enumerate(active, start=1):
        rect = region.get('rect') if isinstance(region.get('rect'), dict) else {}
        x = _clamp_float(rect.get('x'), 0.0, 0.0, 1.0)
        y = _clamp_float(rect.get('y'), 0.0, 0.0, 1.0)
        w = _clamp_float(rect.get('w'), 0.33, 0.02, 1.0)
        h = _clamp_float(rect.get('h'), 0.8, 0.02, 1.0)
        region_id = str(region.get('id') or f'region_{index}').strip() or f'region_{index}'
        label = str(region.get('label') or f'Region {index}').strip()
        prompt_parts = []
        # Region identity/location must lead the compiled prompt. If global/style
        # context comes first, long context can hit CLIP limits before the region
        # prompt is read, making Scene Director masks appear inactive.
        if label:
            prompt_parts.append(label)
        region_prompt = str(region.get('prompt') or '').strip()
        if region_prompt:
            prompt_parts.append(region_prompt)
        is_character = _is_character_region(region)
        if is_character:
            subject_slot += 1
        if is_character and subject_contract:
            prompt_parts.append(subject_contract)
        region_context_prompt = str(prompt_context.get('region_context_prompt') or '').strip()
        if region_context_prompt:
            prompt_parts.append(region_context_prompt)
        # Keep per-region negatives fully user-authored.
        # Earlier builds injected traits from other regions here as an automatic
        # anti-bleed negative. That made output-affecting behavior hidden and
        # could fight intentional close-contact poses, so Phase 1 safe upgrade
        # leaves bleed mitigation guidance in the UI instead of mutating prompts.
        region_negative = str(region.get('negative_prompt') or '').strip()
        subjects.append({
            'id': region_id,
            'bbox': [round(x, 4), round(y, 4), round(min(1.0, x + w), 4), round(min(1.0, y + h), 4)],
            'type': _region_type(region),
            'prompt': _unique_csv(prompt_parts),
            'negative': region_negative,
            'negative_prompt': region_negative,
            'character_traits': region.get('character_traits') if isinstance(region.get('character_traits'), dict) else {},
            'trait_lock': region.get('trait_lock') if isinstance(region.get('trait_lock'), dict) else {},
            'pose_type': str(region.get('pose') or '').strip(),
            'facing': '',
            'required': bool(is_character),
            'strength': round(_clamp_float(region.get('strength'), 1.0, 0.0, 2.0), 4),
            'priority': 1.0 if is_character else 0.65,
            'presence_boost': 1.0 if is_character else 0.35,
            'min_body_presence': 0.0,
            'feather': _scene_region_feather(region),
            'mask_refinement': {
                'requested': bool(mask_policy.get('enabled')) and is_character,
                'effective_source': str(mask_policy.get('effective_mask_source') or 'region_box'),
                'fallback': bool(mask_policy.get('fallback')),
            },
            'character_lock_correction': region.get('character_lock_correction') if isinstance(region.get('character_lock_correction'), dict) else {},
        })
        identity_unit = _identity_unit_from_region(region, index) if is_character else None
        if isinstance(identity_unit, dict) and not identity_unit.get('missing_reference_image'):
            refs = identity_unit.get('image_names') if isinstance(identity_unit.get('image_names'), list) else []
            ipadapter[region_id] = {
                'source': 'character_profile',
                'profile_id': identity_unit.get('profile_id') or '',
                'profile_name': identity_unit.get('profile_name') or '',
                'mode': identity_unit.get('mode') or 'faceid',
                'image': refs[0] if refs else identity_unit.get('image_name') or '',
                'images': refs,
                'weight': _clamp_float(identity_unit.get('weight'), 0.52, 0.0, 2.0),
                'weight_faceidv2': _clamp_float(identity_unit.get('weight_faceidv2'), 1.0, 0.0, 2.0),
                'slot': None,
                'subject_slot': max(1, min(4, subject_slot or index)),
                'region_mask': True,
                'attn_mask_output_index': 5 + max(1, min(4, subject_slot or index)),
            }
        elif bool(region.get('ipadapter')) or str(region.get('reference') or 'off') != 'off':
            slot = max(1, min(8, int(_clamp_float(region.get('ipadapter_slot') or index, index, 1.0, 8.0))))
            ipadapter[region_id] = {
                'source': 'neo_ipadapter_slot',
                'image': f'BOUND_TO_NEO_IPADAPTER_SLOT_{slot}',
                'slot': slot,
                'weight': _clamp_float(region.get('ipadapter_weight'), 0.52, 0.0, 2.0),
                'weight_mode': str(region.get('ipadapter_weight_mode') or 'slot_default'),
                'start_at': _clamp_float(region.get('ipadapter_start_at'), 0.05, 0.0, 1.0),
                'end_at': _clamp_float(region.get('ipadapter_end_at'), 0.75, 0.0, 1.0),
                'region_mask': bool(region.get('ipadapter_use_region_mask', True)),
            }

    negative_parts = [prompt_context.get('effective_negative_prompt', '')]
    scene_json = {
        'version': '0.5.2',
        'canvas': {'width': int(width), 'height': int(height)},
        'camera': {
            'framing': 'vertical scene' if height >= width else 'wide scene',
            'angle': '',
            'lens': '',
            'depth': '',
        },
        'global_style': global_style,
        'subjects': subjects,
        'relations': [],
        'negative': _unique_csv(negative_parts),
        'multi_subject_mode': 'count_locked' if count > 1 else 'single_subject',
        'entity_count': count,
        'character_count': count,
        'region_count': len(active),
        'detail_region_count': max(0, len(active) - count),
        'identity': {'ipadapter': ipadapter},
        'mask_refinement': mask_policy,
        'prompt_context': {
            'prompt_authority': prompt_context.get('prompt_authority'),
            'prompt_authority_contract': prompt_context.get('prompt_authority_contract', {}),
            'region_context_enabled': bool(prompt_context.get('region_context_enabled')),
            'region_context_mode': prompt_context.get('region_context_mode'),
            'region_context_weight': prompt_context.get('region_context_weight'),
            'region_context_position': prompt_context.get('region_context_position'),
            'region_context_truncated': bool(prompt_context.get('region_context_truncated')),
            'style_allowed': bool(prompt_context.get('style_allowed')),
            'style_template_expanded': bool(prompt_context.get('style_template_expanded')),
        },
    }
    return json.dumps(scene_json, ensure_ascii=False, indent=2), contracts, prompt_context


def scene_director_to_regional_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    if not isinstance(payload, dict):
        return payload, notes
    state = payload.get('scene_director_state')
    if not isinstance(state, dict):
        state = payload.get('scene_director') if isinstance(payload.get('scene_director'), dict) else {}
    scene = normalize_scene_director_state(state)
    if not scene.get('enabled'):
        return payload, notes

    family = str(payload.get('family') or scene.get('family') or '').strip().lower()
    if family in BLOCKED_FAMILIES or family not in SUPPORTED_FAMILIES:
        notes.append('Scene Director skipped for this generation route: only SD / SDXL checkpoint families can execute the V054 node. Flux/Qwen keep V054 scene graph planning through provider adapters.')
        return payload, notes

    variant = _infer_checkpoint_variant(payload.get('checkpoint') or payload.get('checkpoint_name') or payload.get('ckpt_name'), family)
    mode = _normalize_mode(payload.get('mode') or payload.get('workflow_type') or scene.get('mode') or 'txt2img')
    if mode not in {'txt2img', 'img2img', 'inpaint'}:
        notes.append(f'Scene Director skipped: {mode} is not supported. Supported modes are txt2img, img2img, and inpaint.')
        payload['_neo_scene_director_applied'] = False
        payload['_neo_scene_director_skip_reason'] = f'unsupported_mode_{mode}'
        return payload, notes

    active = scene.get('active_regions') if isinstance(scene.get('active_regions'), list) else []
    if not active:
        notes.append('Scene Director enabled but no active prompt/profile regions were found.')
        return payload, notes

    size = scene.get('size') if isinstance(scene.get('size'), dict) else {}
    width = int(float(payload.get('width') or size.get('width') or 1024))
    height = int(float(payload.get('height') or size.get('height') or 1024))
    mask_refine_policy = _normalize_mask_refine_policy(payload, scene, mode)
    scene_json, contracts, prompt_context = build_v052_scene_json(scene, width, height, mask_refine_policy, payload)

    units: list[dict[str, Any]] = []
    for index, region in enumerate(active, start=1):
        rect = region.get('rect') if isinstance(region.get('rect'), dict) else {}
        strength = _clamp_float(region.get('strength'), 1.0, 0.0, 2.0)
        units.append({
            'source': 'scene_director',
            'id': str(region.get('id') or f'scene_region_{index}'),
            'index': index,
            'enabled': True,
            'label': str(region.get('label') or f'Region {index}'),
            'type': str(region.get('type') or 'character'),
            'prompt': str(region.get('prompt') or '').strip(),
            'negative_prompt': str(region.get('negative_prompt') or '').strip(),
            'character_traits': region.get('character_traits') if isinstance(region.get('character_traits'), dict) else {},
            'trait_lock': region.get('trait_lock') if isinstance(region.get('trait_lock'), dict) else {},
            'mask_source': str(mask_refine_policy.get('effective_mask_source') or 'region_box') if _is_character_region(region) else 'region_box',
            'raw_mask_source': 'region_box',
            'mask_channel': 'alpha',
            'mask_refine_enabled': bool(mask_refine_policy.get('enabled')) and _is_character_region(region),
            'mask_refine_supported': bool(mask_refine_policy.get('supported')) and _is_character_region(region),
            'mask_refine_policy': mask_refine_policy,
            'x': _clamp_float(rect.get('x'), 0.0, 0.0, 1.0),
            'y': _clamp_float(rect.get('y'), 0.0, 0.0, 1.0),
            'w': _clamp_float(rect.get('w'), 0.33, 0.02, 1.0),
            'h': _clamp_float(rect.get('h'), 1.0, 0.02, 1.0),
            'region_role': 'subject' if _is_character_region(region) else 'detail',
            'mask_feather': _scene_region_feather(region),
            'positive_strength': strength,
            'negative_strength': strength,
            'strength': strength,
            'falloff': 0.0,
            'priority': index,
            'composer_mode': 'scene_director',
            'overlap_mode': 'blend',
            'backend_mode': 'v052_node',
            'model_variant': variant,
            'reference': str(region.get('reference') or 'off'),
            'reference_note': str(region.get('reference_note') or ''),
            'image_name': str(region.get('image_name') or '').strip(),
            'image_names': region.get('image_names') if isinstance(region.get('image_names'), list) else [],
            'character_profile': region.get('character_profile') if isinstance(region.get('character_profile'), dict) else {},
            'character_profile_id': str(region.get('character_profile_id') or '').strip(),
            'character_profile_name': str(region.get('character_profile_name') or '').strip(),
            'character_profile_enabled': bool(region.get('character_profile_enabled')),
            'pose': str(region.get('pose') or 'off'),
            'ipadapter': bool(region.get('ipadapter')),
            'ipadapter_model': str(region.get('ipadapter_model') or ''),
            'ipadapter_clip_vision': str(region.get('ipadapter_clip_vision') or 'CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors'),
            'ipadapter_weight': _clamp_float(region.get('ipadapter_weight'), 0.52, 0.0, 2.0),
            'ipadapter_start_at': _clamp_float(region.get('ipadapter_start_at'), 0.05, 0.0, 1.0),
            'ipadapter_end_at': _clamp_float(region.get('ipadapter_end_at'), 0.75, 0.0, 1.0),
            'ipadapter_slot': max(1, min(8, int(_clamp_float(region.get('ipadapter_slot') or index, index, 1.0, 8.0)))),
            'ipadapter_use_region_mask': region.get('ipadapter_use_region_mask') is not False,
            'ipadapter_weight_mode': str(region.get('ipadapter_weight_mode') or 'slot_default').strip() or 'slot_default',
            'lora': bool(region.get('lora')),
            'lora_slot': max(1, min(8, int(_clamp_float(region.get('lora_slot') or index, index, 1.0, 8.0)))),
            'lora_weight_mode': str(region.get('lora_weight_mode') or 'slot_default').strip() or 'slot_default',
            'lora_strength': _clamp_float(region.get('lora_strength'), 0.8, -4.0, 4.0),
            'loras': region.get('loras') if isinstance(region.get('loras'), list) else [],
        })


    scene_ipadapter_bindings: list[dict[str, Any]] = []
    bound_slots: list[int] = []
    ipadapter_auto_fix_actions: list[dict[str, Any]] = []
    for unit in units:
        if not bool(unit.get('ipadapter')):
            continue
        region_index = int(unit.get('index') or (len(scene_ipadapter_bindings) + 1))
        requested_slot = max(1, min(8, int(_clamp_float(unit.get('ipadapter_slot') or region_index, region_index, 1.0, 8.0))))
        slot = requested_slot
        if slot in bound_slots:
            available = next((candidate for candidate in range(1, 9) if candidate not in bound_slots), slot)
            if available != slot:
                ipadapter_auto_fix_actions.append({
                    'action': 'auto_reassign_duplicate_ipadapter_slot',
                    'region_id': str(unit.get('id') or ''),
                    'label': str(unit.get('label') or f'Region {region_index}'),
                    'from_slot': slot,
                    'to_slot': available,
                })
                slot = available
        if slot not in bound_slots:
            bound_slots.append(slot)
        scene_ipadapter_bindings.append({
            'uid': f"scene_bind_{unit.get('id') or region_index}",
            'region_id': str(unit.get('id') or ''),
            'region_index': region_index,
            'label': str(unit.get('label') or f'Region {region_index}'),
            'slot': slot,
            'requested_slot': requested_slot,
            'use_region_mask': bool(unit.get('ipadapter_use_region_mask', True)),
            'weight_mode': str(unit.get('ipadapter_weight_mode') or 'slot_default').strip() or 'slot_default',
            'weight': _clamp_float(unit.get('ipadapter_weight'), 0.52, 0.0, 2.0),
            'start_at': _clamp_float(unit.get('ipadapter_start_at'), 0.05, 0.0, 1.0),
            'end_at': _clamp_float(unit.get('ipadapter_end_at'), 0.75, 0.0, 1.0),
            # Final subject_slot/attn output is resolved after all regions are
        # normalized. Region indexes include background/detail lanes and are not
        # valid subject slots.
        'subject_slot': None,
        'attn_mask_output_index': None,
            'source': 'scene_director_slot_binding',
        })

    # Phase 9.3: LoRA targeting is owned by the main Neo LoRA stack.
    # A LoRA row with apply_to="global" stays global. A row with
    # apply_to="scene_region_N" is removed from the global stack and routed
    # into the masked regional LoRA pass for subject N.
    scene_lora_bindings: list[dict[str, Any]] = []
    bound_lora_slots: list[int] = []
    source_loras = []
    raw_loras = payload.get('loras')
    if isinstance(raw_loras, list):
        source_loras = [dict(item, _neo_lora_slot_index=index + 1) for index, item in enumerate(raw_loras) if isinstance(item, dict)]
    elif str(payload.get('lora_name') or '').strip():
        source_loras = [{
            '_neo_lora_slot_index': 1,
            'name': str(payload.get('lora_name') or '').strip(),
            'strength': payload.get('lora_strength') if payload.get('lora_strength') is not None else 0.8,
            'target': payload.get('lora_target') or 'both',
            'apply_to': payload.get('lora_apply_to') or 'global',
        }]

    region_label_by_index = {int(unit.get('index') or i + 1): str(unit.get('label') or f'Region {i + 1}') for i, unit in enumerate(units)}
    region_id_by_index = {int(unit.get('index') or i + 1): str(unit.get('id') or '') for i, unit in enumerate(units)}
    for slot_index, lora_unit in enumerate(source_loras, 1):
        apply_to = str(lora_unit.get('apply_to') or lora_unit.get('applyTo') or 'global').strip().lower()
        if not apply_to.startswith('scene_region_'):
            continue
        try:
            region_index = int(apply_to.replace('scene_region_', '', 1))
        except Exception:
            region_index = 0
        if region_index <= 0 or region_index > 4:
            continue
        if region_index not in region_label_by_index:
            continue
        slot = int(lora_unit.get('_neo_lora_slot_index') or slot_index)
        if slot not in bound_lora_slots:
            bound_lora_slots.append(slot)
        scene_lora_bindings.append({
            'uid': f"scene_lora_stack_target_{slot}_region_{region_index}",
            'region_id': region_id_by_index.get(region_index, ''),
            'region_index': region_index,
            'label': region_label_by_index.get(region_index, f'Region {region_index}'),
            'slot': slot,
            'weight_mode': 'slot_default',
            'strength': _clamp_float(lora_unit.get('strength') or lora_unit.get('lora_strength'), 0.8, -4.0, 4.0),
            'source': 'neo_lora_stack_apply_to_targeting',
        })

    if bound_lora_slots:
        payload['scene_director_bound_lora_units_source'] = source_loras
        bound_set = set(bound_lora_slots)
        payload['loras'] = [unit for index, unit in enumerate(source_loras) if (index + 1) not in bound_set]
        if 1 in bound_set:
            payload['lora_name'] = ''
            payload['lora_strength'] = ''
            payload['lora_enabled'] = False


    payload['scene_director_state'] = state
    payload['scene_director_enabled'] = True
    payload['scene_director_phase'] = 'phase1_safe_upgrade'
    payload['scene_director_backend_mode'] = 'v052_node'
    payload['scene_director_model_variant'] = variant
    payload['scene_director_model_profile'] = 'sd15_v052_ipadapter_slot_binding' if variant == 'sd15' else 'sdxl_v052_ipadapter_slot_binding'
    payload['scene_director_regional_units'] = units
    payload['scene_director_v052_global_prompt_override'] = prompt_context.get('effective_global_prompt', '')
    payload['scene_director_effective_global_prompt'] = prompt_context.get('effective_global_prompt', '')
    payload['scene_director_effective_negative_prompt'] = prompt_context.get('effective_negative_prompt', '')
    payload['scene_director_prompt_authority'] = normalize_prompt_authority(prompt_context.get('prompt_authority'))
    payload['scene_director_prompt_authority_contract'] = prompt_context.get('prompt_authority_contract', {})
    payload['scene_director_global_prompt_excluded'] = bool(
        payload['scene_director_prompt_authority'] == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY
    )
    payload['scene_director_region_refinement_global_prompt'] = prompt_context.get('region_refinement_global_prompt', prompt_context.get('effective_global_prompt', ''))
    payload['scene_director_region_refinement_negative_prompt'] = prompt_context.get('region_refinement_negative_prompt', prompt_context.get('effective_negative_prompt', ''))
    payload['scene_director_style_stack_apply_to_region_refinement'] = bool(prompt_context.get('style_stack_apply_to_region_refinement'))
    payload['scene_director_style_stack_region_refinement_policy'] = prompt_context.get('region_refinement_policy', 'styled_global_allowed')
    payload['scene_director_style_stack_global_only'] = bool(prompt_context.get('style_stack_global_only'))
    payload['scene_director_style_stack_merged'] = bool(prompt_context.get('style_allowed') and prompt_context.get('style_positive'))
    payload['scene_director_style_merge_mode'] = 'template_expansion_into_scene_director_global' if prompt_context.get('style_template_expanded') else 'append_into_scene_director_global'
    payload['scene_director_style_prompt_hardcoded'] = False
    payload['scene_director_style_prompt_source'] = 'style_positive/style_negative payload only'
    payload['scene_director_region_context_enabled'] = bool(prompt_context.get('region_context_enabled'))
    payload['scene_director_region_context_mode'] = prompt_context.get('region_context_mode')
    payload['scene_director_region_context_weight'] = prompt_context.get('region_context_weight')
    payload['scene_director_region_context_applied'] = bool(prompt_context.get('region_context_enabled'))
    payload['scene_director_region_context_source'] = 'dynamic_user_prompt_context_no_hardcoded_style'
    payload['scene_director_region_context_position'] = prompt_context.get('region_context_position')
    payload['scene_director_region_context_truncated'] = bool(prompt_context.get('region_context_truncated'))
    payload['scene_director_v052_prompt_contracts'] = contracts
    payload['scene_director_v052_scene_json'] = scene_json
    character_unit_count = len([unit for unit in units if str(unit.get('type') or 'character').lower() == 'character'])
    payload['scene_director_v052_base_weight'] = _clamp_float(payload.get('scene_director_v052_base_weight'), 0.35 if character_unit_count >= 2 else 0.45, 0.0, 1.0)
    payload['scene_director_v052_region_gain'] = _clamp_float(payload.get('scene_director_v052_region_gain'), 0.65 if character_unit_count >= 2 else 0.50, 0.0, 1.0)
    payload['scene_director_v052_max_subject_slots'] = 1
    payload['scene_director_v052_normalize_masks'] = True
    payload['scene_director_v052_subject_count'] = len([unit for unit in units if str(unit.get('type') or 'character').lower() == 'character'])
    payload['scene_director_v052_detail_region_count'] = len([unit for unit in units if str(unit.get('type') or 'character').lower() != 'character'])
    payload['scene_director_v052_anti_bleed'] = False
    payload['scene_director_v052_anti_bleed_policy'] = 'user_guided_region_negative_only'
    payload['scene_director_mask_refine'] = mask_refine_policy
    payload['scene_director_mask_refine_enabled'] = bool(mask_refine_policy.get('enabled'))
    payload['scene_director_mask_refine_supported'] = bool(mask_refine_policy.get('supported'))
    payload['scene_director_mask_source'] = str(mask_refine_policy.get('effective_mask_source') or 'region_box')
    payload['scene_director_raw_mask_source'] = 'region_box'
    payload['scene_director_effective_modes'] = ['txt2img', 'img2img', 'inpaint']
    payload['scene_director_v052_enable_auto_prompts'] = bool(contracts.get('use_node_auto_prompts'))
    region_identity_units = []
    subject_slot_by_region: dict[str, int] = {}
    subject_slot = 0
    for unit in units:
        if _is_character_region(unit):
            subject_slot += 1
            rid = str(unit.get('id') or '').strip()
            if rid and subject_slot <= 4:
                subject_slot_by_region[rid] = subject_slot
        identity_unit = _identity_unit_from_region(unit, int(unit.get('index') or (len(region_identity_units) + 1)))
        if identity_unit:
            rid = str(identity_unit.get('region_id') or unit.get('id') or '').strip()
            slot = subject_slot_by_region.get(rid)
            if slot:
                identity_unit['subject_slot'] = slot
                identity_unit['attn_mask_output_index'] = 5 + slot
            region_identity_units.append(identity_unit)
    identity_warnings: list[dict[str, Any]] = []
    valid_region_identity_units = []
    for unit in region_identity_units:
        if unit.get('missing_reference_image'):
            identity_warnings.append({
                'level': 'warning',
                'reason': 'profile_missing_image',
                'profile_id': unit.get('profile_id') or '',
                'profile_name': unit.get('profile_name') or '',
                'region_id': unit.get('region_id') or '',
                'label': unit.get('label') or '',
                'message': f"Character Profile {unit.get('profile_name') or unit.get('profile_id') or 'selected'} has no reference image, so regional FaceID/IPAdapter was not activated.",
            })
            continue
        valid_region_identity_units.append(unit)
    if valid_region_identity_units:
        existing_identity_units = payload.get('scene_director_identity_units') if isinstance(payload.get('scene_director_identity_units'), list) else []
        existing_keys = {str(item.get('uid') or item.get('profile_id') or '') for item in existing_identity_units if isinstance(item, dict)}
        merged_identity_units = list(existing_identity_units)
        for unit in valid_region_identity_units:
            key = str(unit.get('uid') or unit.get('profile_id') or '')
            if key and key in existing_keys:
                continue
            merged_identity_units.append(unit)
        payload['scene_director_identity_units'] = merged_identity_units

    payload['scene_director_ipadapter_guardrails'] = identity_warnings
    payload['scene_director_ipadapter_auto_fix_actions'] = ipadapter_auto_fix_actions
    payload['scene_director_ipadapter_bindings'] = scene_ipadapter_bindings
    payload['scene_director_ipadapter_bound_slots'] = bound_slots
    existing_scene_ipadapter_units = payload.get('scene_director_ipadapter_units') if isinstance(payload.get('scene_director_ipadapter_units'), list) else []
    payload['scene_director_ipadapter_units'] = existing_scene_ipadapter_units
    payload['scene_director_ipadapter_count'] = len(scene_ipadapter_bindings) + len(existing_scene_ipadapter_units) + len(valid_region_identity_units)
    payload['scene_director_suppress_global_ipadapter'] = payload['scene_director_ipadapter_count'] > 0
    payload['scene_director_lora_bindings'] = scene_lora_bindings
    payload['scene_director_lora_bound_slots'] = bound_lora_slots
    payload['scene_director_lora_count'] = len(scene_lora_bindings)
    payload['scene_director_regional_lora_backend'] = True
    payload['regional_prompt_enabled'] = True
    payload['regional_prompt_profile'] = 'scene_director'
    payload['regional_composer_mode'] = 'scene_director'
    payload['regional_backend_mode'] = 'v052_node'
    payload['regional_overlap_mode'] = 'blend'
    payload['regional_count'] = len(units)
    payload['regional_prompt_regions'] = units
    readable_variant = 'SD 1.5' if variant == 'sd15' else ('SDXL' if variant == 'sdxl' else 'SD checkpoint')
    notes.append(f"Scene Director staged {len(units)} {readable_variant} region(s) for {mode}: {payload.get('scene_director_v052_subject_count', 0)} subject region(s), {payload.get('scene_director_v052_detail_region_count', 0)} detail/object region(s), and tighter V052 region masks. Per-region negatives remain user-authored.")
    if mask_refine_policy.get('enabled'):
        notes.append(str(mask_refine_policy.get('message') or 'Scene Director Character Mask Refinement requested.'))
    if identity_warnings:
        notes.extend(str(item.get('message') or item.get('reason')) for item in identity_warnings)
    if ipadapter_auto_fix_actions:
        notes.append('Scene Director auto-reassigned duplicate IPAdapter slot(s): ' + ', '.join(f"{item.get('label')} {item.get('from_slot')}→{item.get('to_slot')}" for item in ipadapter_auto_fix_actions) + '.')
    if scene_ipadapter_bindings:
        notes.append(f'Scene Director bound {len(scene_ipadapter_bindings)} region(s) to existing Neo IPAdapter slot(s): ' + ', '.join(str(slot) for slot in bound_slots) + '. Global IPAdapter is suppressed only because regional IPAdapter bindings are active.')
    if valid_region_identity_units:
        notes.append(f'Scene Director prepared {len(valid_region_identity_units)} Character Profile regional FaceID/IPAdapter unit(s).')
    if region_identity_units:
        notes.append(f'Scene Director routed {len(region_identity_units)} Character Profile region(s) into masked IPAdapter/FaceID units.')
    if scene_lora_bindings:
        notes.append(f'Scene Director Phase 9.2 bound {len(scene_lora_bindings)} region(s) to existing Neo LoRA slot(s): ' + ', '.join(str(slot) for slot in bound_lora_slots) + '. These LoRAs are removed from the global stack and applied later as masked low-denoise regional LoRA passes.')
    return payload, notes


def patch_workflow(workflow: dict[str, Any], neo_settings: dict[str, Any] | None = None, extension_state: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = neo_settings or {}
    family = str(settings.get('family') or settings.get('model_family') or '').strip().lower()
    if family in BLOCKED_FAMILIES or (family and family not in SUPPORTED_FAMILIES):
        return workflow
    normalize_scene_director_state(extension_state)
    return workflow
