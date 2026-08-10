from __future__ import annotations

from typing import Any

from .profile_contract import build_profile_instruction_blocks, get_profile_manifest, normalize_profile, profile_option
from .visual_analysis import build_visual_analysis_request


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_PURPOSE_RULES = {
    "general": "Describe the most useful visible facts for the requested output without inventing narrative context.",
    "character_identity": "Prioritize stable visible identity traits such as face shape, hair, body build, distinctive marks, and repeatable appearance. Keep variable pose, outfit, lighting, and background distinct so they are not mistaken for identity.",
    "pose": "Prioritize body orientation, torso direction, limb placement, hand position, weight distribution, gesture, contact points, and camera-relative pose.",
    "expression": "Prioritize observable expression signals: eyes, gaze, brows, mouth, head angle, facial tension, and visible affect. Do not invent a psychological backstory.",
    "outfit": "Prioritize visible garments, layers, cut, fit, material, color, footwear, accessories, and construction details.",
    "art_style": "Prioritize visible medium, linework, brushwork, rendering method, shading, palette, texture, edge treatment, and detail density.",
    "concept": "Prioritize the defining visible concept or theme and omit incidental details that do not help reproduce or classify it.",
    "object_product": "Prioritize visible object shape, construction, materials, surface, color, condition, viewpoint, and distinctive design elements.",
    "environment": "Prioritize visible architecture, terrain, room layout, materials, weather, spatial structure, lighting, and surrounding context.",
}

_SCOPE_RULES = {
    "full_image": "Use the full visible frame as context.",
    "person": "Focus on the visible person or people; include background only when needed to understand them.",
    "face": "Focus on visible facial appearance, hair around the face, expression, gaze, and head orientation.",
    "outfit": "Focus on clothing, accessories, materials, fit, and styling.",
    "pose": "Focus on body position, gesture, orientation, action posture, and physical contact.",
    "environment": "Focus on the visible environment and spatial context; people are secondary unless needed for scale or interaction.",
    "composition_camera": "Focus on framing, shot size, angle, perspective, camera position, depth, and lens feel.",
    "lighting_color": "Focus on light direction, softness/hardness, contrast, exposure feel, reflections, and color palette.",
    "action_interaction": "Focus on visible actions and physical interactions between subjects and objects.",
    "custom_crop": "Use only the supplied crop as visual evidence.",
}

_OUTPUT_RULES = {
    "descriptive_caption": "Return one grounded natural-language visual caption.",
    "image_generation_prompt": "Return one image-generation prompt that preserves grounded source facts and expresses only the requested visual treatment.",
    "tags_keywords": "Return compact comma-separated grounded tags or keywords only.",
    "alt_text": "Return concise accessibility-oriented alt text. Prioritize essential visible content and avoid decorative prompt language.",
    "shot_breakdown": "Return a concise plain-text shot breakdown covering subject, composition/camera, lighting, and scene. No markdown table.",
    "attribute_list": "Return a compact plain-text visible attribute list suitable for a character/reference sheet. Do not infer hidden attributes.",
    "dataset_caption": "Return one strict training caption. Favor repeatable visual facts over prose. Do not add atmospheric storytelling or unsupported identity claims.",
    "video_generation_prompt": "Return one temporal image-to-video prompt. Start from the source image state, describe motion in sequence, preserve continuity, and state camera behavior clearly.",
    "edit_instruction": "Return one image-edit instruction that clearly separates requested changes from elements that must remain unchanged.",
    "natural_prompt": "Return one natural-language generation prompt.",
    "sd_tags": "Return compact Stable Diffusion / SDXL-style comma-separated prompt tags.",
    "hybrid_prompt": "Return a natural-language prompt followed by a compact set of high-value generation tags, without headings.",
    "structured_prompt": "Return a concise sectioned plain-text prompt structure without JSON or markdown fences.",
}

_EDIT_RULES = {
    "general_edit": "Make only the requested edit and preserve unrelated visible content.",
    "identity_preserve": "Strongly preserve the subject's facial identity, hairstyle, age appearance, body proportions, and distinctive visible traits while applying the requested edit.",
    "outfit_change": "Change only the requested clothing/accessories. Preserve identity, face, hair, body proportions, pose, camera, lighting, and environment unless the user explicitly asks otherwise.",
    "face_hair_change": "Change only the requested face or hair attributes. Preserve body, clothing, pose, composition, and environment unless explicitly requested.",
    "background_change": "Change the environment/background while preserving the subject's identity, pose, clothing, scale, and foreground placement unless explicitly requested.",
    "object_add": "Add only the requested object with plausible scale, perspective, lighting, and contact. Preserve existing unrelated elements.",
    "object_remove": "Remove only the requested object/person and reconstruct the revealed background coherently without changing unrelated content.",
    "object_replace": "Replace only the requested object/person while preserving composition, perspective, lighting, and unrelated content.",
    "pose_restaging": "Apply the requested pose/restaging while preserving identity, clothing, scene style, and recognizable subject appearance unless explicitly requested otherwise.",
    "relighting": "Change lighting only; preserve subject identity, geometry, pose, clothing, environment structure, and camera framing.",
    "recolor": "Change only the requested colors/material color appearance while preserving shape, texture, identity, pose, and composition.",
    "style_transfer": "Apply the requested rendering treatment while preserving source subject identity, composition, pose, and scene content unless the user explicitly requests broader change.",
    "composition_change": "Apply the requested reframing/composition change while preserving source identities and scene content unless explicitly changed.",
    "inpaint_instruction": "Treat the user's requested region/change as local. Preserve content outside the intended edit region.",
    "outpaint_instruction": "Extend the scene beyond the source boundaries while preserving the original image content, perspective, lighting, and continuity.",
}

_PRESERVATION_RULES = {
    "preserve_unrequested": "Treat every visible element not explicitly requested for change as a preservation target.",
    "identity_priority": "Identity preservation has priority over stylistic or scene reinterpretation.",
    "composition_priority": "Pose, framing, camera perspective, and scene geometry have priority unless the requested edit requires changing them.",
    "flexible": "Allow broader changes only where needed to satisfy the request, while retaining explicit source constraints.",
}

_MOTION_RULES = {
    "natural_balanced": "Use realistic continuous motion with natural timing, body mechanics, cloth/hair response, and no abrupt transformations.",
    "subtle_idle": "Keep motion minimal: breathing, blinking, tiny gaze/head shifts, and subtle hair/clothing/environment movement only where plausible.",
    "cinematic": "Use controlled cinematic motion with readable staging, smooth timing, and restrained secondary motion.",
    "dynamic_action": "Use energetic but physically coherent action with clear anticipation, action, follow-through, and continuity.",
    "slow_motion": "Describe graceful slow-motion movement with physically consistent secondary motion and stable identity.",
    "dreamy_floating": "Use gentle floating or dreamlike motion while keeping source geometry and identity coherent.",
    "handheld_documentary": "Use natural documentary-like motion and restrained handheld camera behavior without artificial choreography.",
    "product_showcase": "Keep the subject/product readable with controlled motion, clean presentation, and stable geometry.",
    "loop_ambient": "Use cyclic ambient motion and make the ending visually compatible with the starting state for a seamless loop.",
}

_CAMERA_RULES = {
    "preserve_auto": "Preserve the source framing by default; use no camera motion unless the requested action clearly benefits from a subtle move.",
    "static": "Keep the camera locked and stationary for the entire shot.",
    "push_in": "Use a smooth controlled push-in toward the main subject.",
    "pull_back": "Use a smooth controlled pull-back revealing more of the scene.",
    "pan_left": "Pan smoothly to the left while maintaining subject continuity.",
    "pan_right": "Pan smoothly to the right while maintaining subject continuity.",
    "tilt": "Use a smooth tilt appropriate to the requested action while preserving scene geometry.",
    "orbit": "Orbit smoothly around the main subject with stable identity and background geometry.",
    "tracking": "Track with the moving subject while keeping framing readable and motion physically coherent.",
    "handheld": "Use subtle natural handheld movement without excessive shake or warping.",
}


def _custom_or_rule(profile: dict[str, Any], dimension: str, rules: dict[str, str]) -> str:
    value = _clean(profile.get(dimension))
    if value == "custom":
        return _clean(profile.get(f"custom_{dimension}"))
    return _clean(rules.get(value))


def compile_caption_task_contract(
    profile: dict[str, Any] | None,
    *,
    user_instruction: str = "",
    caption_length: str = "medium",
    detail_level: str = "detailed",
) -> dict[str, Any]:
    """Compile the canonical P23 profile into one grounded Caption Studio task contract."""
    requested = _dict(profile)
    surface = _clean(requested.get("surface")) or "caption_studio"
    normalized = normalize_profile(requested, surface=surface).get("profile") or {}
    task = _clean(normalized.get("prompt_task")) or "caption_image"
    visual_request = build_visual_analysis_request(
        profile=normalized,
        task=task,
        user_instruction=user_instruction,
    )
    instruction_blocks = build_profile_instruction_blocks(normalized, user_instruction, surface=surface)

    manifest = get_profile_manifest()
    visual = profile_option(manifest, "visual_treatment", _clean(normalized.get("visual_treatment")))
    visual_desc = _clean(visual.get("description"))
    purpose_rule = _custom_or_rule(normalized, "purpose", _PURPOSE_RULES)
    scope_rule = _clean(_SCOPE_RULES.get(_clean(normalized.get("analysis_scope"))))
    output_rule = _custom_or_rule(normalized, "output_format", _OUTPUT_RULES)

    task_rules: list[str] = []
    if task == "caption_image":
        task_rules.append("Describe/analyze the supplied image according to the selected profile.")
    elif task == "image_recreation":
        task_rules.extend([
            "Create a prompt that can recreate the supplied source image.",
            "Keep subject count, identity-relevant visible traits, pose, composition, environment, lighting, and camera facts faithful unless the user's instruction explicitly requests a transformation.",
        ])
    elif task == "image_edit":
        task_rules.extend([
            "Create an image-editing instruction for the supplied source image; do not merely caption it.",
            _custom_or_rule(normalized, "edit_intent", _EDIT_RULES),
            _custom_or_rule(normalized, "preservation_policy", _PRESERVATION_RULES),
        ])
    elif task == "image_to_video":
        task_rules.extend([
            "Create an image-to-video animation prompt for the supplied source image; do not rewrite the image as a static image prompt.",
            "The first frame/state must remain consistent with the supplied image. Preserve subject identity, clothing, environment, lighting, and composition unless the user's motion instruction explicitly changes them.",
            _custom_or_rule(normalized, "motion_profile", _MOTION_RULES),
            _custom_or_rule(normalized, "camera_behavior", _CAMERA_RULES),
            "Do not invent extra people, objects, scene changes, cuts, or actions that the user did not request.",
        ])
    elif task == "dataset_prepare":
        task_rules.extend([
            "Create a strict training-dataset caption for the supplied image.",
            "Prefer concrete visible attributes useful to the selected dataset purpose. Omit story, mood speculation, unsupported demographics, and decorative prose.",
        ])
    elif task == "library_caption":
        task_rules.append("Create a grounded reusable library caption/prompt according to the selected profile.")

    if surface in {"batch_dataset", "batch_library"}:
        task_rules.extend([
            "This request contains exactly one current image. Write exactly one caption/prompt for that image only.",
            "Never enumerate images or emit provider/internal attachment labels such as [Image 1], image indices, filenames, file paths, or attachment names.",
        ])

    trigger = _clean(normalized.get("trigger_token"))
    if task == "dataset_prepare" and trigger:
        task_rules.append(f"Begin the final dataset caption with the exact trigger token `{trigger}` exactly once, followed by a comma and the grounded caption.")

    if visual_desc:
        if _clean(normalized.get("visual_treatment")) == "source_accurate":
            task_rules.append("Visual treatment is Source Accurate: describe/preserve the source treatment rather than restyling it.")
        else:
            task_rules.append("Requested visual treatment: " + visual_desc)

    length_rule = {
        "short": "Keep the result short and selective.",
        "medium": "Use moderate detail without padding or repetition.",
        "long": "Use thorough detail, but every detail must remain relevant and grounded.",
        "any": "Use the length needed for a useful result without padding.",
    }.get(_clean(caption_length), "Use moderate detail without padding or repetition.")
    detail_rule = {
        "basic": "Prioritize only the highest-value visible facts.",
        "detailed": "Include useful visible detail while avoiding redundant micro-description.",
        "attribute_rich": "Capture many distinct visible attributes, but do not infer hidden or uncertain traits.",
    }.get(_clean(detail_level), "Include useful visible detail while avoiding redundant micro-description.")

    system_prompt = (
        "You are Neo Studio's grounded visual-analysis and prompt-writing engine. Inspect the supplied image carefully. "
        "Return only the final requested caption or prompt text, with no markdown fences, no explanation of your reasoning, and no invented concrete facts. "
        "The user's explicit instruction is the highest task-specific directive, but it never permits unsupported factual claims."
    )

    lines = ["TASK CONTRACT:"]
    for block in instruction_blocks.get("blocks") or []:
        text = _clean(block.get("text"))
        if text:
            lines.append(f"- {text}")
    for rule in (purpose_rule, scope_rule, output_rule, *task_rules, detail_rule, length_rule):
        if _clean(rule):
            lines.append(f"- {_clean(rule)}")
    lines.extend([
        "- If the user's instruction narrows the requested content, ignore any lower-priority Purpose, Scope, Treatment, or format guidance that would expand beyond that instruction.",
        "- Silently inspect relevant source facts before writing; do not expose chain-of-thought or an internal analysis dump.",
        "- If a requested output would require an unsupported detail, omit that detail rather than guessing.",
        "- Return only the final requested text.",
    ])

    return {
        "profile": normalized,
        "system_prompt": system_prompt,
        "user_prompt": "\n".join(lines),
        "visual_analysis_request": visual_request,
    }


def caption_sampling_params(params: dict[str, Any] | None, profile: dict[str, Any] | None) -> dict[str, Any]:
    """Apply conservative sampling for batch-grounded workflows without changing single-caption freedom."""
    out = dict(params or {})
    clean_profile = _dict(profile)
    surface = _clean(clean_profile.get("surface"))
    task = _clean(clean_profile.get("prompt_task"))
    if surface == "batch_dataset" or task == "dataset_prepare":
        try:
            out["temperature"] = min(float(out.get("temperature", 0.25)), 0.30)
        except (TypeError, ValueError):
            out["temperature"] = 0.25
        try:
            out["top_p"] = min(float(out.get("top_p", 0.85)), 0.85)
        except (TypeError, ValueError):
            out["top_p"] = 0.85
        stops = list(out.get("stop_sequences") or []) if isinstance(out.get("stop_sequences"), list) else ([str(out.get("stop_sequences"))] if out.get("stop_sequences") else [])
        for marker in ("\n[Image", "\n[image"):
            if marker not in stops:
                stops.append(marker)
        out["stop_sequences"] = stops
    elif surface == "batch_library" or task == "library_caption":
        try:
            out["temperature"] = min(float(out.get("temperature", 0.45)), 0.55)
        except (TypeError, ValueError):
            out["temperature"] = 0.45
        stops = list(out.get("stop_sequences") or []) if isinstance(out.get("stop_sequences"), list) else ([str(out.get("stop_sequences"))] if out.get("stop_sequences") else [])
        for marker in ("\n[Image", "\n[image"):
            if marker not in stops:
                stops.append(marker)
        out["stop_sequences"] = stops
    return out
