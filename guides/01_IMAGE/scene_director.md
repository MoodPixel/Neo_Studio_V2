---
guide_id: image.scene_director
title: Scene Director
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generations
  - scene_director
  - regional prompting
  - regional masks
  - character lock
  - pair pose authority
  - background space authority
  - extension routing
  - sdxl
  - sd15
  - checkpoint
  - generate
  - img2img
  - inpaint
tags:
  - image
  - scene director
  - regional prompt
  - region canvas
  - region cards
  - v054
  - character lock
  - pair pose
  - background space
  - mask refinement
  - layout safety
  - lora routing
  - ipadapter routing
  - controlnet routing
  - route aware
  - loader aware
priority: 126
version: 1
updated: 2026-07-09
---

# Scene Director

**Scene Director** is Neo Studio's built-in regional scene planner for the **Image → Generation** workspace. It lets the user divide an image into regions, assign each region a role and prompt, preserve character/body traits, route global prompt context into regions, and coordinate region-aware extension intent.

Use this guide when the user asks about Scene Director, region boxes, Character Lock, V054 roles, background/character regions, regional LoRA/IPAdapter/ControlNet routing, pair pose authority, or why Scene Director is disabled for a selected Image route.

Scene Director does **not** replace the main Positive/Negative Prompt fields. Neo's main prompts remain the **global scene context**. Scene Director reads those prompts, then adds structured regional guidance on top.


## Quick field map

Scene Director's visible controls are grouped as: **Main Scene Director panel**, **Prompt rules**, **Region context suffix**, **Pair Pose Authority**, **Background Space Authority**, **Fix Pass Controls + Layout Safety**, **First-pass Character Lock Authority**, **Character Lock**, **Global Context Routing**, **Presets**, **Region Canvas**, **Region Cards**, **Advanced Region Control**, **Extension Routing**, and **Character Trait Lock**. Active workflow patching expects the **NeoSceneDirectorV054** Comfy node on supported SDXL/SD1.5 checkpoint routes.

## Route support and gating

Scene Director is family-aware, loader-aware, workflow-aware, and backend-aware.

| Route dimension | Supported / expected behavior |
|---|---|
| **Workspace** | Only mounts in **Image → Generation**. It is not for Assets, Reference, Finish, or Results. |
| **Backend** | Full workflow patching is ComfyUI-based. Non-Comfy/API profiles are provider-gated. |
| **Node readiness** | Current V2 expects the **NeoSceneDirectorV054** Comfy node for active graph mutation. Older V052/V053 active fallback is retired. |
| **Best route** | **SDXL + Safetensors / Checkpoint + Generate / Img2Img / Inpaint**. |
| **Experimental route** | **SD 1.5 + Safetensors / Checkpoint + Generate / Img2Img / Inpaint**. |
| **Planned-gated route** | **Outpaint**. It needs a dedicated canvas/mask policy before Scene Director should mutate the graph. |
| **Unsupported families** | Flux, Flux 2 Klein, Qwen Rapid AIO, Qwen Image Edit, Qwen Image Edit 2509, ZImage, ZImage Turbo, HiDream, Wan, Hunyuan. These do not consume the V054 checkpoint scene-graph conditioning path. |
| **Unsupported loaders** | GGUF, diffusion model/component, native/provider/API loaders. Scene Director V054 is checkpoint-only. |

If Scene Director is visible but disabled/gated, explain the route reason instead of promising it will execute. A region setup can still be preserved as metadata/replay intent, but inactive/gated routes must not fake regional conditioning.

## Main Scene Director panel

| Field / control | What it does | How to use it |
|---|---|---|
| **Enable for workflow** | Turns Scene Director on for the current generation request. | Enable only when the selected route is ready/available and at least one region has useful prompt/reference intent. |
| **Add Region** | Adds a region card and region box to the canvas. | Use one region per main subject, object, background zone, or important style/effect area. |
| **Status chips** | Show Enabled/Disabled, Available/Ready, and node readiness. | If the chip says unsupported/provider-gated, switch family/loader/workflow or install/check the V054 node. |
| **Active regions / Subjects chips** | Count usable region cards and character/person regions. | Use this to confirm Neo sees the intended number of subjects before generating. |
| **Global positive source** | Shows the current main Positive Prompt as Scene Director's global scene context. | Edit the main Positive Prompt in the Prompt panel, not inside this preview box. |
| **Global negative source** | Shows the current main Negative Prompt as Scene Director's global negative context. | Edit the main Negative Prompt in the Prompt panel. |
| **Scene Director authority** | Controls how strongly Scene Director should influence the generation. | Use **Balanced** for normal work, **Soft regional guide** for light influence, **Strong regional correction** for stubborn layouts, and **Neutral / planning only** for metadata/planning without sampler mutation. |
| **Base weight** | Weight of global scene context versus region intent. | Keep near `0.35` for balanced setups. Raise only if global composition should dominate. |
| **Region gain** | Strength of region guidance. | Keep near `0.65` for clear region intent. Raise carefully if regions are being ignored. |
| **Max subject slots** | Maximum character/subject regions Scene Director should plan around. | Keep this equal to or slightly above the number of visible subjects. Too high can invite extra subjects. |
| **Normalize masks** | Normalizes/cleans region masks for safer regional routing. | Usually keep on. Turn off only for advanced debugging. |
| **Character mask refinement metadata** | Preserves intent that character masks should be refined. | Useful for character-heavy scenes, but it is metadata/route-dependent and not always an active extra pass. |

## Authority modes

| Mode | Meaning | Use case |
|---|---|---|
| **Neutral / planning only** | Keeps Scene Director planning/metadata without sampler mutation. | Debug, presets, replay planning, or when the user wants to save layout only. |
| **Layout only** | Treats regions mainly as layout/mask planning. | Good when prompts are already strong and only spatial layout matters. |
| **Soft regional guide** | Adds light regional influence. | Good first test for simple subject/background separation. |
| **Anime safe / prompt append only** | Keeps the base model path intact and applies region guidance mainly as prompt text. | Safer for stylized/anime routes where heavy graph mutation can overcook the image. |
| **Balanced** | Normal default. | Recommended for most Scene Director tests. |
| **Strong regional correction** | Pushes region intent harder. | Use when subjects merge, extra people appear, or region roles are ignored. |
| **Debug / aggressive** | Strongest diagnostic mode. | Use only for troubleshooting; it can overconstrain the output. |

## Prompt rules

The **Prompt rules** subsection adds text contracts used to keep regional generation honest.

| Field | What it does |
|---|---|
| **Enable prompt rules** | Enables Scene Director's count/subject/negative/style contract text. |
| **Use node auto prompts** | Allows the node/compiler to add its own automatic prompts where supported. Leave off if you want full manual wording. |
| **Count rule** | Tells the model how many visible subjects should exist, usually one subject per character region and no extras. |
| **Subject rule** | Tells each character region to contain one complete subject, not a duplicate/merged body. |
| **Negative rule** | Adds guard text against extra people, missing subjects, wrong count, merged bodies, and fused faces. |
| **Style merge note** | Explains how the main Neo prompt should be used as scene style/composition context. |

Use these when multi-person scenes drift, when one subject disappears, or when subjects merge together.

## Region context suffix

Region context suffix controls whether global/style context is appended to each region prompt.

| Field | What it does | Advice |
|---|---|---|
| **Enable region context** | Appends selected global/style context after regional prompts. | Useful when regions need to share the same overall scene/style. |
| **Mode** | Chooses which global/style context is routed. | Use `global_and_style` for normal scene consistency. Use off/limited modes when regional prompts are getting polluted. |
| **Context weight** | Strength/importance of routed context. | Start around `0.35`. Lower it if local region wording should dominate. |
| **Position** | Current UI uses `suffix`. | This means context is appended after each region prompt. |
| **Apply global style to regional refinement passes** | Allows Style Stack/global style to affect regional refinement passes. | Default off keeps Style Stack in the Scene Director global prompt only. Turn on if refinement passes need the same style language. |

## Pair Pose Authority

Pair Pose Authority is for relationship/contact intent across character regions.

| Field | What it does | Advice |
|---|---|---|
| **Enable pair pose authority** | Activates shared pose/contact intent for character regions. | Use for couples, hugs, hand-holding, close selfie poses, fighting/contact poses, or any pose involving more than one person. |
| **Feed into Character Trait pose group** | Copies pair-pose intent into character trait pose context. | Keep enabled when character pose dropdowns should inherit the shared contact idea. |
| **Pair pose / contact intent** | Describes the relationship/contact action for the scene. | Be specific: who is behind/in front, who touches whom, relaxed vs dramatic, etc. |
| **Pair pose negative guard** | Guards against failed contact. | Include issues like separated bodies, wrong contact, extra arms, fused hands, broken anatomy. |
| **Pose strength** | Controls influence of pair pose intent. | Around `0.75` is strong but still flexible. Lower if composition becomes stiff. |

Pair Pose Authority does not replace exact spatial control. Use it together with clear region boxes and, when needed, OpenPose/ControlNet/reference images.

## Background Space Authority

Background Space Authority creates a background lane when the scene has character regions but no explicit background region.

| Field | What it does | Advice |
|---|---|---|
| **Enable background space authority** | Adds a hidden/full-canvas background intent lane. | Use when characters occupy the scene but the background keeps becoming blank, generic, or wrong. |
| **Background space prompt** | Describes the intended environment/background. | Keep it environment-focused, not subject-focused. |
| **Background negative guard** | Prevents bad backgrounds or subject takeover. | Use phrases like blank wall, flat studio backdrop, background covering subject, extra people. |
| **Source mode** | Controls where background authority comes from. | `Explicit field only` means only this field creates the lane. |
| **Strength** | Influence of the background lane. | Around `0.7` is strong enough for visible environments. |
| **Denoise** | Denoise amount for background repair/restore lanes where used. | Around `0.42` is moderate. Lower preserves more; higher changes more. |

If the scene already has a proper background region card, use the region card instead of relying only on Background Space Authority.

## Fix Pass Controls + Layout Safety

Fix Pass Controls are optional repair/cleanup lanes. They are not always needed. Smaller, tighter character boxes usually solve more than extra repair passes.

| Field | What it does | Advice |
|---|---|---|
| **Fix pass mode** | Controls the overall repair strategy: Minimal/Fast, Smart/Auto, Manual, or Force All. | Use Minimal/Fast for first tests. Use Smart/Auto for normal correction. Use Force All only when debugging stubborn failures. |
| **First-pass Character Lock** | Controls whether character lock rescue runs before adapters/other passes. | Use Auto unless identity/gender/body preservation is failing. |
| **Background Restore** | Controls background repair/restoration pass. | Use Auto or Off. Force on only when backgrounds are broken and masks are safe. |
| **Character Trait Lanes** | Controls repair lanes for explicit character traits. | Use Auto for character consistency problems. |
| **Final Background Reconcile** | Controls final cleanup between character/background lanes. | Use Auto if backgrounds and characters conflict. |
| **Environment-aware character lanes** | Lets character repair consider nearby environment. | Usually keep on so character fixes do not ignore the scene. |
| **Layout safety warnings** | Reports whether region boxes leave enough raw background area. | If warnings appear, tighten character boxes or add a background region. |
| **Safe background minimum %** | Minimum free background area target. | Default `12` is a safety threshold. |
| **Full-height box threshold** | Detects too-large/full-height character boxes. | Default `0.92`; warnings mean region boxes may be stealing background authority. |
| **Auto-fit selected/all character boxes** | Shrinks/aligns character boxes to safer proportions. | Use when boxes are oversized or background authority is blocked. |

## First-pass Character Lock Authority

This nested section owns the first character rescue pass details.

| Field | What it does |
|---|---|
| **Execution type** | Usually `Masked correction pass`; runs correction inside the selected character mask. |
| **Enable first-pass character correction** | Turns the first-pass correction lane on/off. |
| **Apply to** | Usually `Strong/Strict locks only`; prevents unnecessary correction when locks are soft. |
| **Correction timing** | Usually after base composition / before adapters, so identity/trait correction happens before adapter-heavy steps. |
| **Correction denoise** | Amount of change in the correction pass. `0.3` is moderate. |
| **Correction steps** | Steps for the correction pass. `10` is a reasonable lightweight default. |
| **Correction CFG** | Can inherit main CFG or use custom CFG. |
| **Mask source** | Chooses full character mask or another mask source. Full character mask is safest for identity/body preservation. |
| **Mask feather** | Softens the correction mask edge. `24` is a common safe value. |
| **Protect outfit / props** | Avoids rewriting clothing/props while correcting character traits. |
| **Protect pose / contact** | Avoids breaking pair pose/contact while correcting identity/traits. |
| **Per-character correction fields** | Optional per-person gender/positive lock/negative guard text. Use these for exact subject protection. |

## Character Lock

Character Lock controls what to preserve about character regions.

| Field | What it protects |
|---|---|
| **Character Lock** | Overall identity/subject consistency. Modes: Off, Soft, Balanced, Strong, Strict. |
| **Gender Guard** | Prevents gender swap/wrong gender. Strong/Strict are useful for binary-gender preservation tests. |
| **Skin Tone Guard** | Helps preserve described skin tone. |
| **Hair Guard** | Helps preserve hair color/style. |
| **Build Guard** | Helps preserve body/build type. |
| **Body / Height Guard** | Helps preserve body height/proportion. |
| **Outfit Preservation** | Helps preserve clothing/outfit. |
| **Negative Guard** | Adds negative identity/body failure guards. |
| **Identity Strength** | Overall identity preservation strength. |
| **Detail Strength** | How hard the system protects/fixes character details. |
| **Background Strength** | How much background authority remains relative to character lock. |
| **Mask Feather** | Softens mask edges used by lock/fix routes. |

Use **Strong** or **Strict** only when identity/traits keep changing. Higher locks can protect subjects but may reduce creative flexibility.

## Global Context Routing

Global Context Routing connects the main Neo prompts to Scene Director.

| Field | What it does |
|---|---|
| **Use Neo positive as global style/context** | Routes the main Positive Prompt into the Scene Director global scene context. |
| **Use Neo negative as global negative** | Routes the main Negative Prompt into Scene Director global negative context. |
| **Allow style context suffix** | Allows style/global context to appear after region prompts when region context suffix is enabled. |

Default behavior should keep Neo core prompts as the global context and use region prompts for local intent.

## Presets

| Preset type | What it saves | What it should not do |
|---|---|---|
| **Scene Presets** | Full Scene Director setup: regions, prompt rules, routing, Character Lock, mask/fix settings. | Do not treat it as just geometry; it can restore behavior settings. |
| **Region Layout Presets** | Region rectangles, labels, roles/types, visible/locked state. | Should not overwrite character identity/profile data unless explicitly designed to. |

Use Scene Presets for repeatable scene behavior. Use Region Layout Presets when only the box layout should be reused.

## Region Canvas

The Region Canvas is a normalized 0–1 coordinate planner for Scene Director boxes.

| Canvas element | Meaning |
|---|---|
| **Region boxes** | Drag/resizable areas for characters, objects, backgrounds, style/effects, or text. |
| **Canvas size chip** | Mirrors current Image width × height. |
| **Orientation chip** | Shows portrait/landscape/square based on current Image dimensions. |
| **Normalized 0–1 coordinates** | Region x/y/w/h values are stored as proportions, not pixels. This allows replay/resizing across output sizes. |

Keep character boxes close to the visible subject. Huge/full-height character boxes can consume background space and cause layout safety warnings.

## Region Cards

Each region card defines a local lane.

| Field / control | What it does | Advice |
|---|---|---|
| **Region enabled** | Enables/disables the region. | Disabled regions stay in the UI but do not become active lanes. |
| **Move up/down** | Reorders the region priority/list position. | Useful when parent/child or overlap relationships matter. |
| **Duplicate** | Copies the region. | Good for creating multiple similar character regions. |
| **Delete** | Removes the region. | Does not delete external source/reference files. |
| **Label** | Human-readable region name. | Use clear labels like Person 1, Person 2, Background - Modern, Kitchen Counter. |
| **V054 Role** | Semantic role used by the V054 scene graph. | Common roles: Character, Object/Prop, Background, Style. Advanced roles include detail/text/effect variants where available. |
| **Visible** | Whether the region participates visibly. | Hidden regions can preserve layout/metadata but should not be used as active prompt lanes. |
| **Locked** | Prevents editing the region. | Use after a layout is approved. |
| **Prompt** | Local positive prompt for that region. | Keep it focused on what belongs inside the region, not the whole scene. |
| **Region negative prompt** | Local negative guard for that region. | Use to prevent wrong subject/background bleed, extra people, or bad local details. |
| **x / y / w / h** | Normalized region coordinates. | Values are 0–1 proportions. Use canvas drag/resize or exact fields. |
| **Strength** | Local region influence. | Around `0.65` works for many background/object lanes; character lanes can vary. |
| **Mask feather** | Softens the region mask edge. | Lower for sharp local edits, higher for smoother blending. |

A region is active when it is enabled, visible, and has prompt/reference/routing intent.

## Advanced Region Control

Advanced Region Control contains optional region-level routing and overrides.

### Relationship & Attachment

| Field | What it does |
|---|---|
| **Attach to** | Links this region to another region/parent. Useful for hair/detail/object regions attached to a character. |
| **Relationship** | Defines relationship type to the attached region. |
| **Target area** | Names the target area such as hair, face, hands, outfit, prop. |
| **Priority** | Controls whether the region reinforces, overrides, or resolves conflicts with parent wording. |

### Prompt Overrides

| Field | What it does |
|---|---|
| **Parent prompt override** | Optional template that rewrites how child/parent region prompts combine. Supports placeholders like `{parent}`, `{child}`, `{target}`, `{relationship}`. |
| **Local mask prompt override** | Optional prompt used only for this region mask lane. |
| **Negative guard override** | Region-specific negative guard for relationship/attachment issues. |
| **Conflict resolution override** | Tells Neo which region wording wins when regions overlap/conflict. |
| **Conflict negative guard** | Negative prompt added only when this conflict lane is relevant. |

### Background Slot

| Field | What it does |
|---|---|
| **Background zone** | Names the background area, such as left side, right side, center seam, kitchen counter. |
| **Background prompt override** | Local background wording for this region only. |
| **Background negative guard** | Prevents background failures such as covering subjects, era mismatch, extra people, or blank wall. |
| **Background composer controls** | Advanced background override/influence/denoise/seam controls where available. |

Manual background regions should describe the environment only. Do not put main character wording inside a background lane unless intentionally painting figures in the background.

### Img2Img Settings

Visible on Img2Img routes.

| Field | What it does |
|---|---|
| **Img2Img intent** | Preserve, modify, or replace the selected region relative to the source image. |
| **Denoise** | How much the region can change. Lower preserves; higher changes more. |
| **Mask reuse** | Uses region mask, source mask, or saved metadata mask when available. |
| **Source image** | Optional source/output image name for region reuse. |

### Inpaint Settings

Visible on Inpaint routes.

| Field | What it does |
|---|---|
| **Inpaint Target** | Marks the region as the target for inpainting. |
| **Action** | The kind of edit/repair requested. |
| **Denoise** | How strongly the inpaint changes the target. |
| **Mask feather** | Softens the inpaint mask edge. |
| **Mask mode** | Chooses region/source/custom mask behavior. |
| **Inpaint prompt / negative** | Local prompt and local negative guard for the inpaint target. |

### Text Region

Text regions are for text/compositor planning.

| Field | What it does |
|---|---|
| **Text mode** | Chooses model-route text or post-decode/composite behavior where available. |
| **Text content** | The actual text to place/render. |
| **Font style / color / size / align / vertical / opacity** | Text layout and styling metadata. |

### Extension Routing

Extension Routing links owner-extension units/rows to Scene Director regions.

| Route | Owner | What Scene Director does |
|---|---|---|
| **ControlNet unit** | ControlNet extension | Stores selected unit id and routes it through the region mask when valid. |
| **ADetailer pass** | ADetailer/detailer extension | Stores selected pass id and routes it through the region mask when valid. |
| **IPAdapter / identity profile** | IP Adapter extension / identity profile records | Preserves identity intent and can route masked IPAdapter/FaceID when required references/nodes exist. |
| **LoRA rows** | LoRA Stack | Uses LoRA Stack row selection but owns the region target assignment and masked regional finish-pass intent. |

Do not duplicate owner-extension controls inside Scene Director. Scene Director should reference existing owner rows/units/profiles and preserve routing metadata if the dependency is disabled.

## Character Trait Lock

Character Trait Lock is available on V054 Character regions. It gives explicit traits that override auto-extraction fallback.

| Trait field | Purpose |
|---|---|
| **Gender** | Explicit gender terms for the character. |
| **Ethnicity** | Explicit ethnicity/heritage terms. |
| **Species / Fantasy Race** | Human, mer, demon, elf, etc. |
| **Build / Body** | Slim, muscular, curvy, athletic, etc. |
| **Skin Tone** | Skin tone preservation terms. |
| **Hair** | Hair color/style/length terms. |
| **Clothing Top** | Top/shirt/jacket/upper outfit. |
| **Clothing Bottom** | Pants/skirt/lower outfit. |
| **Full Costume** | Full outfit override when top/bottom is not enough. |
| **Pose** | Character pose or relation to another region. |
| **Expression** | Facial expression / mood. |
| **Accessories** | Glasses, jewelry, props, bags, etc. |
| **Shoes** | Footwear terms. |
| **Custom + button** | Adds custom terms to the matching trait JSON library for future dropdown reuse. |

Use trait fields when the model keeps changing gender, body, clothing, ethnicity, pose, or expression. Keep fields short and descriptive. The region prompt still carries the main creative wording.

## Best practices

- Use **one character region per visible person**.
- Keep character boxes tight around the subject; avoid full-canvas boxes unless the character fills the image.
- Use the main Positive Prompt for the global composition/style.
- Use region prompts for local subject/background/object instructions.
- Add a background region when the background matters.
- Use Prompt Rules for subject count and anti-merge protection.
- Use Pair Pose Authority for contact/relationship scenes.
- Use Character Lock and Trait Lock when identity/body/gender/clothing keep drifting.
- Test once with Style Stack/CFG Fix off if you are debugging pure Scene Director behavior.
- Do not expect Scene Director to work on Qwen/Flux/ZImage/HiDream/Grok routes unless the UI explicitly shows a future compatible adapter route.

## Assistant behavior

When answering Scene Director questions:

1. Check the live Image snapshot for Model Family, Main Model Type, Workflow Mode, backend, Scene Director enabled state, and route state.
2. If the route is not SDXL/SD1.5 checkpoint Generate/Img2Img/Inpaint on ComfyUI, explain the gating first.
3. Explain fields in user terms. Do not dump raw `scene_graph_json` or metadata unless requested.
4. For prompt help, produce a practical region setup: global prompt, region labels/roles, region prompts, negatives, and any useful Pair Pose / Background Space / Character Lock settings.
5. For multi-character issues, recommend tighter boxes, one character per region, Prompt Rules, Pair Pose Authority, Character Lock, and explicit Character Trait Lock.
6. For regional LoRA/IPAdapter/ControlNet, explain owner-extension dependency: LoRA Stack/IP Adapter/ControlNet own assets; Scene Director owns regional assignment.
