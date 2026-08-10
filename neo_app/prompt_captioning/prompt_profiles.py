from __future__ import annotations

from typing import Any

from .profile_contract import get_profile_manifest, normalize_profile, profile_option


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


_EDIT_RULES = {
    "general_edit": "Write a precise edit instruction that changes only what the user requests and preserves unrelated content.",
    "identity_preserve": "Treat subject identity as a hard preservation target unless the user explicitly requests an identity change.",
    "outfit_change": "Focus the edit on clothing, accessories, garment materials, fit, and styling while preserving identity, pose, and unrelated scene elements.",
    "face_hair_change": "Focus the edit on the requested face or hair changes while preserving unrelated clothing, pose, camera, and environment details.",
    "background_change": "Change the environment/background while preserving the subject, identity, pose, clothing, and framing unless explicitly requested otherwise.",
    "object_add": "Add only the requested object(s), with placement and interaction stated clearly; preserve unrelated elements.",
    "object_remove": "Remove only the requested object(s) and reconstruct the revealed area coherently without inventing unrelated changes.",
    "object_replace": "Replace only the requested object(s), preserving composition, perspective, lighting, and unrelated content.",
    "pose_restaging": "Change the requested pose/action while preserving identity and other specified appearance/scene constraints.",
    "relighting": "Change lighting direction, quality, color, or contrast only as requested while preserving scene content.",
    "recolor": "Change only the requested colors/material appearance while preserving geometry and unrelated details.",
    "style_transfer": "Apply only the requested visual style/treatment while preserving subject and composition facts unless the user requests structural changes.",
    "composition_change": "Change framing/composition only as requested and preserve unrelated subject/scene facts.",
    "inpaint_instruction": "Phrase the result as a local inpaint instruction: define the intended replacement inside the selected region and preserve surrounding context.",
    "outpaint_instruction": "Phrase the result as an outpaint instruction: extend the visible scene consistently beyond the existing frame without rewriting the existing image.",
}

_PRESERVATION_RULES = {
    "preserve_unrequested": "Treat every element not mentioned for change as preserved by default.",
    "identity_priority": "Give subject identity preservation priority over stylistic or scene reinterpretation.",
    "composition_priority": "Give pose, framing, camera perspective, and scene geometry priority unless the requested edit requires changing them.",
    "flexible": "Allow broader changes only where required to satisfy the request, while retaining all explicit preservation constraints.",
}

_MOTION_RULES = {
    "natural_balanced": "Use realistic continuous motion with natural timing, body mechanics, and plausible secondary motion.",
    "subtle_idle": "Keep motion minimal and restrained: small breathing, blinking, gaze/head shifts, and subtle environmental movement only where relevant.",
    "cinematic": "Use controlled cinematic movement with readable staging, smooth timing, and restrained secondary motion.",
    "dynamic_action": "Use energetic but physically coherent action with clear anticipation, action, follow-through, and continuity.",
    "slow_motion": "Describe deliberate slow-motion movement with physically consistent secondary motion and stable subject continuity.",
    "dreamy_floating": "Use gentle dreamlike or floating motion while keeping subjects and scene geometry coherent.",
    "handheld_documentary": "Use natural documentary-like movement and restrained handheld energy without artificial choreography.",
    "product_showcase": "Keep the product/subject readable with controlled showcase motion, stable geometry, and clean presentation.",
    "loop_ambient": "Use cyclic ambient motion and make the ending visually compatible with the starting state for a seamless loop.",
}

_CAMERA_RULES = {
    "preserve_auto": "Do not force camera movement. Keep the camera stable unless the user requests movement or a subtle move clearly improves the described shot.",
    "static": "Keep the camera locked and stationary for the entire shot.",
    "push_in": "Use a smooth controlled push-in toward the main subject.",
    "pull_back": "Use a smooth controlled pull-back that reveals more of the scene.",
    "pan_left": "Pan smoothly left while maintaining subject continuity.",
    "pan_right": "Pan smoothly right while maintaining subject continuity.",
    "tilt": "Use a smooth tilt appropriate to the action while maintaining scene continuity.",
    "orbit": "Orbit smoothly around the main subject while keeping identity and geometry stable.",
    "tracking": "Track with the moving subject while keeping framing readable and motion coherent.",
    "handheld": "Use subtle natural handheld movement without excessive shake or warping.",
}


def _rule_or_custom(profile: dict[str, Any], dimension: str, rules: dict[str, str]) -> str:
    value = _clean(profile.get(dimension))
    if value == "custom":
        return _clean(profile.get(f"custom_{dimension}"))
    return _clean(rules.get(value))


def _format_rule(profile: dict[str, Any]) -> str:
    fmt = _clean(profile.get("output_format"))
    custom = _clean(profile.get("custom_output_format"))
    rules = {
        "natural_prompt": "Write one cohesive natural-language generation prompt.",
        "sd_tags": "Write concise comma-separated Stable Diffusion / SDXL-style prompt tags; do not add prose explanations.",
        "hybrid_prompt": "Write a concise natural-language prompt followed by a compact set of high-value generation tags in the same final text.",
        "structured_prompt": "Write a compact structured prompt with clear generation-relevant sections, but no commentary about the process.",
        "image_generation_prompt": "Write a polished image-generation prompt.",
        "edit_instruction": "Write one direct image-edit instruction, separating requested changes from preservation constraints.",
        "video_generation_prompt": "Write one temporal video-generation prompt with motion, continuity, camera behavior, and an ending state.",
    }
    return custom if fmt == "custom" and custom else _clean(rules.get(fmt))


def _treatment_rule(profile: dict[str, Any]) -> str:
    manifest = get_profile_manifest()
    value = _clean(profile.get("visual_treatment"))
    if value == "custom":
        custom = _clean(profile.get("custom_visual_treatment"))
        return f"Requested visual treatment: {custom}" if custom else ""
    option = profile_option(manifest, "visual_treatment", value)
    description = _clean(option.get("description"))
    if value == "source_accurate":
        return "Do not impose an unrelated visual style. Preserve the source idea's own visual direction unless the user requests a treatment."
    return f"Requested visual treatment: {description}" if description else ""


def compile_prompt_task_contract(
    profile: dict[str, Any] | None,
    *,
    tool_id: str = "prompt_generate",
    source_text: str = "",
    user_instruction: str = "",
) -> dict[str, Any]:
    """Compile P23.3 Prompt Studio text-first tasks into provider messages.

    Prompt Studio does not inspect images. The source text and explicit user
    instruction are authoritative. Task profiles control whether the final text
    is an image prompt, image-edit instruction, or temporal video prompt.
    """
    requested = _dict(profile)
    normalized = normalize_profile(requested, surface="prompt_studio").get("profile") or {}
    task = _clean(normalized.get("prompt_task")) or "text_to_image"
    tool = _clean(tool_id) or "prompt_generate"
    source = _clean(source_text)
    custom = _clean(user_instruction)

    system_prompt = (
        "You are Neo Studio Prompt Studio, a source-faithful prompt compiler. "
        "Return only the requested final prompt/instruction text with no markdown fences, no JSON, no title, and no explanation. "
        "Begin immediately with the usable generation prompt/instruction. Never prepend model analysis, a source description, or wrapper phrases such as "
        "'The image shows', 'Here is the prompt', 'The final prompt is', or 'Final prompt:'. "
        "Preserve every concrete subject, identity term, clothing detail, location, object, action, mood, and constraint supplied by the user. "
        "Do not invent unrelated people, creatures, locations, lore, products, relationships, or story events. "
        "The user's explicit custom instruction is the highest task-specific directive."
    )

    lines: list[str] = ["TASK CONTRACT:"]
    operation_rules = {
        "prompt_generate": "Generate a polished prompt from the source idea.",
        "prompt_enhance": "Enhance the existing source prompt with useful generation detail without changing its subject or intent.",
        "prompt_rewrite": "Rewrite the source cleanly while preserving all core meaning and constraints.",
        "text_transform": "Transform the source only according to the user's custom instruction while preserving unrelated source facts.",
    }
    if operation_rules.get(tool):
        lines.append(f"- {operation_rules[tool]}")

    if task == "text_to_video":
        lines.extend([
            "- Create a text-to-video generation prompt, not a static image caption.",
            "- Describe a coherent temporal progression: starting state, requested action/motion, transitions, continuity, and ending state.",
            "- Do not invent extra actions, cuts, scene changes, people, objects, or locations unless the source or user instruction asks for them.",
            "- Keep identity, wardrobe, environment, and geometry consistent across time unless the source explicitly changes them.",
            f"- {_rule_or_custom(normalized, 'motion_profile', _MOTION_RULES)}",
            f"- {_rule_or_custom(normalized, 'camera_behavior', _CAMERA_RULES)}",
        ])
    elif task == "text_image_edit":
        lines.extend([
            "- Create an image-editing instruction from the user's text request; do not create a standalone replacement scene prompt.",
            "- Because no source image is visible to you, never claim source-image details the user did not provide.",
            f"- {_rule_or_custom(normalized, 'edit_intent', _EDIT_RULES)}",
            f"- {_rule_or_custom(normalized, 'preservation_policy', _PRESERVATION_RULES)}",
        ])
    else:
        lines.extend([
            "- Create an image-generation prompt anchored to the user's source idea.",
            "- Expand with generation-useful composition, lighting, camera, material, atmosphere, or quality language only when it supports the supplied idea.",
            "- Do not turn a simple portrait/fashion/product idea into unrelated narrative lore or a different scene.",
        ])

    treatment = _treatment_rule(normalized)
    if treatment:
        lines.append(f"- {treatment}")
    format_rule = _format_rule(normalized)
    if format_rule:
        lines.append(f"- {format_rule}")
    if custom:
        lines.append(f"- USER INSTRUCTION (highest task-specific priority): {custom}")
    lines.append(f"- SOURCE: {source or 'Create a useful prompt matching the selected task without inventing a concrete subject.'}")
    lines.extend([
        "- If a lower-priority profile rule conflicts with the user's source or custom instruction, preserve the user's wording and intent.",
        "- Return only the final prompt/instruction text.",
        "- Start directly with that final text. Do not describe what you are about to output and do not add a 'Final prompt' label or quoted wrapper.",
    ])

    return {
        "profile": normalized,
        "system_prompt": system_prompt,
        "user_prompt": "\n".join(line for line in lines if _clean(line).rstrip("-").strip()),
    }
