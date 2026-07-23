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
  - character pose authority
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
  - character pose
  - background space
  - mask refinement
  - layout safety
  - lora routing
  - ipadapter routing
  - controlnet routing
  - route aware
  - loader aware
priority: 126
version: 2
updated: 2026-07-22
---

# Scene Director

**Scene Director** is Neo Studio's built-in regional scene planner for the **Image → Generation** workspace. It lets the user divide an image into regions, assign each region a role and prompt, preserve character/body traits, route global prompt context into regions, and coordinate region-aware extension intent.

Use this guide when the user asks about Scene Director, region boxes, Character Lock, V054 roles, background/character regions, regional LoRA/IPAdapter/ControlNet routing, character-local pose authority, or why Scene Director is disabled for a selected Image route.

Scene Director does **not** replace the main Positive/Negative Prompt fields by default. The current V054 default is **Global context + Scene Director structure**: Neo's main prompts own scene mood, environment, style, lighting, and camera context, while Scene Director owns subject count, identities, relationships, poses, and exact object/region placement. The advanced **Scene Director only** mode explicitly excludes the Neo core positive/negative conditioning from the V054 node.

## Current V054 simplification

The normal workflow has one prompt-ownership selector and a canvas with simple region cards. The old authority modes, numeric weights, mask switches, contracts, context tuning, repair passes, identity locks, extension routing, and raw payload details remain available inside collapsed Advanced controls for replay and expert tuning.

| Prompt authority | Runtime behavior | Best use |
|---|---|---|
| **Global context + Scene Director structure** | Keeps Neo core positive/negative conditioning on the canvas lane and shares only a compact, token-safe context suffix with regional branches. | Default for coherent style/environment plus difficult multi-subject or placement scenes. |
| **Scene Director only** | Blanks Neo core prompt conditioning inside the V054 route. Local region prompts, relationships, poses, contracts, and explicit Scene Director-owned lanes remain active. | Advanced isolation/debugging when every conditioning phrase should be authored in Scene Director. |

The global prompt is not copied in full into every region. This keeps regional identity/pose wording readable while preserving the main scene context. Existing saved payloads normalize through the legacy bridge and receive the default global-context mode when no explicit authority field exists.

## Character Lock execution plan

Character Lock strength and extra repair passes are separate controls. **Character Lock Lanes: Strong** activates the V054 in-sampler attention branch; explicit traits influence the character throughout one uninterrupted denoising run. **Mid-sampling Trait Lanes** is only a gate for the separately selected experimental midpoint plan. `Force on` never overrides the fast plan or adds a sampler by itself.

Choose one pass plan when needed:

| Pass plan | Runtime behavior | Cost / use |
|---|---|---|
| **In-sampler latent lock (fast)** | Legacy V1 Hairlock Strong behavior through the V054 `attn2` model patch. No Character Lock repair KSamplers. | Default for gender, ethnicity, hair, build, and clothing stability. |
| **Experimental midpoint trait repair** | Requests a split `KSamplerAdvanced` route. Neo blocks the split for SDE/multistep samplers because a second invocation cannot preserve their solver history. | Diagnostic only; prefer the uninterrupted in-sampler lock. |
| **End refinement only** | Disables the in-sampler appearance branch and runs the selected late repair/refinement controls. | Use only when a deliberate final repair is wanted. |
| **Latent lock + end refinement** | Runs both families. | Slowest and most likely to overcook; use for diagnosis only. |

The old `latent_correction` label normalizes to **In-sampler latent lock (fast)** for compatibility with the legacy behavior. The old `masked_correction` label normalizes to **End refinement only**. Submit a fresh workflow after changing this setting; if a replayed graph still contains the old Scene Director subject-mask chain, the backend now removes that identifiable chain and reconnects decode to the selected plan.

Compatibility behavior: a replayed or older payload may still say
`character_lock_execution_mode = latent_attention` while its visible
**Mid-sampling Trait Lanes** control is `Force on`. The backend now preserves
`latent_attention` as the effective plan, removes any stale midpoint chain, and
keeps one uninterrupted sampler. Only an explicit midpoint pass-plan selection
can request a split.

Character Trait Lock values are live runtime inputs. V054 preserves the region
`character_traits` and `character_lock_correction` fields, compiles positive
terms into the subject-local attention branches, and routes explicit negative
corrections to the sampler negative conditioning. A runtime report of
`live_in_sampler_attention` means no separate masked trait lane is expected for
the fast plan.


## Quick field map

Scene Director's visible controls are grouped as: **Prompt authority**, **Region Canvas**, **Region Cards**, and collapsed **Advanced Scene Control**. Advanced Scene Control contains prompt rules, region context tuning, background space, fix passes, Character Lock, owner-extension routing, presets, and expert payload details. Each Character region's **Character Trait Lock → Pose** field is the sole text-pose authority. Active workflow patching expects the **NeoSceneDirectorV054** Comfy node on supported SDXL/SD1.5 checkpoint routes.

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

## Legacy execution authority (advanced only)

The seven historical execution modes are retained only for saved-payload compatibility and expert diagnosis. They do not replace the two-option Prompt authority contract above.

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

## Character-local Pose Authority

Phase 27.13 retires the separate Advanced **Pair Pose Authority** control. Each
Character region now owns its own Pose text inside **Character Trait Lock →
Pose**, and that Pose follows the visible Character Lock strength.

Describe only the selected character's body state and action. Another character
may be named only as a contact target:

- Person 1: `standing beside Person 2, one arm around Person 2's waist`;
- Person 2: `seated on the office table, knees bent, torso toward Person 1`.

Do not copy one shared paragraph into both characters. That makes actor/posture
ownership ambiguous and lets one model seed swap who is standing or seated.
Character-local Pose terms ride the existing primary/full-character branches;
they do not add a pose lane, mask, sampler, or repair pass. For exact joint,
hand, and limb coordinates, route OpenPose or another pose ControlNet to the
matching character region.

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

The **Character Lock pass plan** is the parent switch for these lanes. By default, **In-sampler latent lock (fast)** skips Character Lock repair samplers, outfit fallback, background restore, and final background reconciliation. The **Mid-sampling Trait Lanes** control cannot change that selection; it only permits a midpoint lane after the midpoint plan is explicitly selected. The first-pass rescue and end/background lanes run only when **End refinement only** or **Latent lock + end refinement** is selected; the individual controls below can still turn those selected lanes off.

| Field | What it does | Advice |
|---|---|---|
| **Fix pass mode** | Controls the overall repair strategy: Minimal/Fast, Smart/Auto, Manual, or Force All. | Use Minimal/Fast for first tests. Use Smart/Auto for normal correction. Use Force All only when debugging stubborn failures. |
| **First-pass Character Lock** | Controls whether character lock rescue runs before adapters/other passes. | Use Auto unless identity/gender/body preservation is failing. |
| **Background Restore** | Controls background repair/restoration pass. | Use Auto or Off. Force on only when backgrounds are broken and masks are safe. |
| **Mid-sampling Trait Lanes** | Gates an explicitly selected experimental midpoint lane. It never promotes the fast plan. | Keep Auto/Off for normal work. Force on only after explicitly choosing the midpoint plan with a proven split-safe sampler. |
| **Final Background Reconcile** | Controls final cleanup between character/background lanes. | Use Auto if backgrounds and characters conflict. |
| **Environment-aware character lanes** | Lets character repair consider nearby environment. | Usually keep on so character fixes do not ignore the scene. |
| **Layout safety warnings** | Reports whether region boxes leave enough raw background area. | If warnings appear, tighten character boxes or add a background region. |
| **Safe background minimum %** | Minimum free background area target. | Default `12` is a safety threshold. |
| **Full-height box threshold** | Detects too-large/full-height character boxes. | Default `0.92`; warnings mean region boxes may be stealing background authority. |
| **Auto-fit selected/all character boxes** | Shrinks/aligns character boxes to safer proportions. | Use when boxes are oversized or background authority is blocked. |

## Character Lock execution path and repair settings

This nested section contains the pass-plan selector and the numeric settings for optional repair samplers. The pass plan is the activation control; these fields only tune a selected repair family. The fast in-sampler plan does not add a late sampler.

| Field | What it does |
|---|---|
| **Character Lock pass plan** | Selects the independent execution layer: in-sampler latent lock, mid-sampling trait repair, end refinement, both, prompt guard only, or off. |
| **Allow end-refinement character pass** | Gates the optional end character repair when an end-refinement plan is selected. It does not affect the fast in-sampler lock. |
| **Apply to** | Usually `Strong/Strict locks only`; prevents unnecessary correction when locks are soft. |
| **Correction timing** | Usually after base composition / before adapters, so identity/trait correction happens before adapter-heavy steps. |
| **Correction denoise** | Amount of change in the correction pass. `0.3` is moderate. |
| **Correction steps** | Steps for the correction pass. `10` is a reasonable lightweight default. |
| **Correction CFG** | Can inherit main CFG or use custom CFG. |
| **Mask source** | Chooses full character mask or another mask source. Full character mask is safest for identity/body preservation. |
| **Mask feather** | Softens the correction mask edge. `24` is a common safe value. |
| **Protect outfit / props** | Avoids rewriting clothing/props while correcting character traits. |
| **Protect pose / contact** | Avoids breaking character-local pose/contact while correcting identity/traits. |
| **Per-character correction fields** | Optional per-person gender/positive lock/negative guard text. Use these for exact subject protection. |

## Character Lock

Character Lock controls what to preserve about character regions.

| Field | What it protects |
|---|---|
| **Character Lock** | Overall identity/subject consistency. Modes: Off, Soft, Balanced, Strong, Strict. |
| **Character Lock pass plan** | Chooses in-sampler latent lock versus optional latent repair and end refinement passes. Strong/Strict does not automatically activate late samplers. |
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

## Prompt routing status

Global routing is now summarized from the Prompt authority contract. There are no duplicate global-routing checkboxes in the normal workflow. The compact regional suffix uses Neo core positive/style context only in the global-context mode; Scene Director-only mode disables that suffix and the core canvas conditioning.

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

## Attached Detail Roles (Phase 27.14)

Character and Background are main parent regions. Their cards do not carry
attachment controls. Every other role owns its link on the child/detail card,
so attachment is authored in one place only.

| Field | What it does |
|---|---|
| **Attach to** | Selects the Character or Background parent from inside the child/detail region. |
| **Relationship** | Defines how the child belongs to the parent, such as Wearing, Holding, or Attached to. |
| **Target area** | Names the precise target, such as necktie, jacket, hair, face, or right hand. |
| **Overlap behavior** | Override wins locally; Reinforce shares authority; Blend is softer. |

The child prompt is compiled only into the child mask. It is not copied into
the complete parent Character prompt. This prevents a phrase such as `red silk
necktie` from tinting a black suit while still letting the small tie box win at
the overlap. A required child with no valid parent is skipped with a visible
warning; it does not disable the remaining Scene Director graph.

Example:

```text
Role: Clothing detail
Attach to: Person 1
Relationship: Wearing
Target area: necktie
Overlap behavior: Override
Prompt: clearly visible saturated red silk necktie
```

Neo does not add blanket NSFW/nudity filters or hidden content-policy negatives.
The user's Base Prompt, detail Prompt, structured selections, and optional
Region negative prompt remain authoritative.

## Advanced Region Control

Advanced Region Control contains optional prompt templates, region-level
routing, source/edit settings, and compositor controls. Attachment itself now
lives only in the visible child-role panel.

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
| **Ethnicity** | Explicit ethnicity/heritage terms from the editable data library. Built-in entries include broader appearance wording where useful. |
| **Species / Fantasy Race** | Human, mer, demon, elf, etc. |
| **Build / Body** | Slim, muscular, curvy, athletic, etc. |
| **Skin Tone** | Skin tone preservation terms. |
| **Hair** | Color-neutral hairstyle/length. Open **🎨 Add color** for a primary color and, optionally, a second color plus a blend pattern. |
| **Facial Hair** | Clean-shaven, stubble, beard, moustache, sideburn, and related facial-hair authority. |
| **Clothing Top** | Color-neutral top/shirt/jacket/upper outfit; optional shared color. |
| **Clothing Bottom** | Color-neutral pants/skirt/lower outfit; optional shared color. |
| **Full Costume** | Color-neutral full outfit override when top/bottom is not enough; optional shared color. |
| **Pose** | Sole text-pose authority for this character. Describe its own posture/action; mention another Person only as a contact target. |
| **Expression** | Facial expression / mood. |
| **Accessories** | Glasses, jewelry, props, bags, etc. |
| **Shoes** | Footwear terms. |
| **Custom + button** | Adds custom terms to the matching trait JSON library for future dropdown reuse. |

Use trait fields when the model keeps changing gender, body, clothing, ethnicity, pose, or expression. Keep fields short and descriptive. The region prompt still carries the main creative wording. For stubborn gender/body drift, use the per-character correction positive/negative fields; they are compiled into the live V054 route rather than stored only as metadata.

### Shared colors and two-color hair

Hair, Clothing Top, Clothing Bottom, and Full Costume use one editable shared
color library. Style entries are intentionally color-neutral, so a hairstyle or
garment does not need to be duplicated once per color. The built-in palette is
a curated set of 72 common, fashion, natural-hair, pastel, metallic, and neon
colors; **＋ Add custom color** can extend it without changing Python code.

The palette opener is shown as **🎨 Add color**. The normal `＋` icon remains
reserved for creating a new reusable trait/color, which avoids confusing
"assign this existing color" with "add a new library record."

The selected value now includes a real swatch, and **View visual palette**
opens clickable color chips. Hex values remain library metadata/tooltips rather
than being the only visible clue. If the free character prompt names one color
near the selected style while the picker assigns another, Neo shows a warning:
both values would otherwise reach the sampler and compete.

Clothing uses one optional color. Hair supports a primary color plus one
optional secondary color with an explicit pattern:

- highlights;
- roots to lengths;
- colored tips;
- streaks;
- split dye;
- ombré.

A color can also be selected while the style dropdown remains **Auto / none**.
In that case Neo compiles a neutral target such as `pink hair` or `red top
garment` and leaves the exact style to the prompt/model.

Two colors are compiled as a deterministic phrase (for example, a primary
hairstyle with secondary highlights); Neo does not numerically average RGB
values. Literal color mixing is ambiguous to diffusion models and is less
controllable than naming where each color appears.

Older combined library selections such as a colored hairstyle are migrated to
their neutral style plus the matching shared color. Saved V054 queues that
predate structured colors can still recover a color already present in their
authored trait text.

### Facial hair, ethnicity, character Pose, and mask edges

Facial Hair is a first-class live trait category, so requested stubble or a
clean-shaven face receives the same character-local Strong/Strict authority as
other identity traits. Its opposite terms also remain subject-local; one
character's clean-shaven guard cannot remove another character's beard.

For Strong/Strict Character Lock, facial hair shares one short face-local lane
with structural gender/identity. Phase 27.12 replaces the older standalone
facial-hair lane because two overlapping normalized face masks could make the
grooming branch steal authority from the male/female face without actually
rendering a small stubble texture. The merged lane uses only selected editable
identity/grooming terms and their data-authored negatives, applies them to one
softly feathered head/face zone, and stays inside the same uninterrupted
sampler. Accessories, expression, clothing, pose, and relationship prose remain
in the normal styling/character branches and cannot redefine structural face
identity.

Ethnicity wording is stored in editable JSON trait entries. The runtime does
not contain person-specific ethnicity substitutions or a hidden example scene.
Prompt provenance reports that core prompts, region text, traits, colors, and
character-local Pose come from explicit user fields or editable libraries;
demo/personal prompt injection is false.

Character Pose is compiled first in that character's primary prompt and carried
through its existing full-character branch at Character Lock strength. Regional
opposite-posture guards remain inside the same character mask. Old Pair Pose
metadata is retained only as a content-free retirement marker and cannot reach
conditioning. This remains text conditioning, not a replacement for pose
ControlNet when exact limbs/contact geometry is required.

V054 now reads feather from the normalized region mask, including
`metadata.mask.feather`, before converting to the live attention graph. This
keeps soft character boundaries around hair, hands, and overlapping bodies
instead of silently falling back to a hard zero-feather edge.

### Seed-stable structural gender authority

Strong and Strict gender locks add one compact subject-local face/grooming lane
inside the existing sampler. It contains only selected data-authored gender,
ethnicity, skin/species, and facial-hair cues, the face-safe portion of the
visible per-character correction field, and their matching regional negative.
This prevents a long pose/outfit/relationship/accessory branch from diluting or
re-gendering the face differently across random seeds.

The lane does not start a second sampler, restart an SDE solver, repaint the
latent, or inject a person-specific scene prompt. Gender and grooming
vocabulary lives in editable JSON libraries; generic compiler wording only
describes how the selected values are applied.

### Clothing-aware gender and body-scoped garment lanes

When Clothing Top or Full Costume is explicitly selected, legacy positive
correction fragments such as bare-chest/torso anatomy are removed from live
conditioning. The selected gender, face, presentation, silhouette, and all
regional gender negatives remain active. Fresh built-in gender entries are
clothing-safe by default; the runtime filter exists for older saved queues and
custom correction text.

Strong/Strict structured clothing now receives exact subject-local garment
conditioning from the editable trait and shared color libraries:

- Clothing Top uses a softly feathered upper-body slice and regional
  missing/wrong-top consistency guards. When Top Garment State explicitly says
  open or unbuttoned, Neo removes the conflicting `bare chest` exclusion while
  still preserving the worn garment.
- Clothing Bottom uses a lower-body slice and missing/wrong-bottom guards.
  Lowered/unzipped states and a visible Underlayer disable the old
  `underwear instead of selected bottom garment` conflict.
- Full Costume uses one body clothing slice and takes precedence over separate
  Top/Bottom lanes to avoid contradictory outfit stacks.

These masks start below the face, so a hoodie, suit, shorts, or costume cannot
compete with stubble/gender at face pixels. Earrings and other Accessories still
reach the primary character prompt as requested styling, but are excluded from
structural face identity. Every lane remains inside one uninterrupted sampler.

### Character Additional Details

Phase 27.15 adds a nested **Character → Additional Details** panel for visible
character-owned requirements that do not belong in identity, ethnicity, Pose,
or the main garment-type picker:

- **Body Details** uses an editable JSON library plus custom text for body hair,
  freckles, tattoos, scars, and similar body-wide traits.
- **Top Garment State** controls buttoned, unbuttoned, open, rolled-sleeve,
  tucked, and related states without changing the selected top garment.
- **Bottom Garment State** controls fastened, unzipped, belt-open, lowered, or
  partially pulled-down states without replacing the selected bottom garment.
- **Underlayer / Underwear** controls the visible underlayer type and uses the
  shared visual color library.
- **Held Items** is repeatable and records the item, hand, action, and
  material/color description.
- **Targeted Custom Details** is repeatable and stores an instruction, target
  area, optional subject-local negative, and an optional reusable JSON preset.

Targeted custom areas are Full body, Face, Torso / upper body, Lower torso /
waist, Arms / hands, Legs, and Feet. They reuse the matching existing branch:
Face joins the merged face/grooming branch; torso and lower-body targets join
the existing clothing branch; full-body and limb details stay in the existing
character branch. Neo does not create one attention lane per detail.

Held Items establishes character ownership and hand action in that character's
existing Pose/full-character text. When the object's exact silhouette or local
appearance matters, use a Phase 27.14 child **Held Prop** rectangle as well.
The character field and child object serve different jobs and neither prompt is
copied into the parent from the child.

Essential visible states should still be stated once in the Base Prompt. The
Base Prompt owns the complete scene and shared visibility; Character Additional
Details assigns exact body, garment-state, underlayer, and held-item ownership
to the correct person.

All built-in Additional Detail presets are editable JSON. User-written prompts
and optional negatives remain authoritative. Phase 27.15 adds no blanket
content-policy negative, nudity restriction, hidden safety prompt, extra
sampler, masked repaint pass, or per-detail attention branch.

### Mid-sampling Character Trait Repair

The V054 midpoint route runs only when the **Character Lock pass plan** is
explicitly set to the experimental midpoint option and the lane gate allows it.
It continues the full latent canvas and scopes extra conditioning to character
masks, but it is not the default Character Lock mechanism.

This is important for fresh txt2img generation: the midpoint route must not
freeze the background with a new `SetLatentNoiseMask`, because the background
would still be unfinished diffusion noise at that point. End refinement is a
separate optional pass and remains off unless selected independently.

Neo blocks midpoint splitting for SDE and multistep samplers such as
`dpmpp_3m_sde_gpu`. Matching the seed cannot restore the solver/Brownian state
lost when a new sampler invocation begins. The safe fallback is the uninterrupted
V054 in-sampler attention route.

### Strong trait authority and regional negatives

Strong/Strict character traits are compiled at the front of each character's
live CLIP branch. Phase 27.8 keeps this inside the same uninterrupted sampler
and gives each locked character two compact identity lanes: a full structural
identity lane and an upper/head identity lane. These lanes use the original
submitted character description plus the explicit trait/correction fields;
they do not recursively copy V054's generated lock prose.

Strong exact traits use bounded CLIP authority (`1.55`), while the submitted
structural correction clauses use `1.62`. This makes gender morphology and
distinctive hair instructions materially stronger than Balanced without
starting a second sampler or changing the denoising schedule.

Negative guards are character-local. This matters in mixed-trait scenes: a
pink-hair character can reject black/brown/blonde hair inside Person 1's mask
while Person 2 still keeps explicitly requested black hair. Gender/body guards
follow the same subject-mask boundary instead of becoming diagnostic-only text.
When the same negative arrives from both a Balanced free-text field and a Strong
structured guard, the strongest weight wins instead of the earlier entry
silently downgrading it.

Mask feathering is applied before numeric authority. This preserves Strong and
Strict mask gain above `1.0`; the previous order clamped the gain during
feathering even though diagnostics still reported the stronger value.

Replayed graphs remove old midpoint conditioning chains when the fast plan is
selected, so decode reconnects to the uninterrupted main sampler. The upper
identity mask also expands slightly above the authored character rectangle to
cover hair that drifts beyond the box during txt2img generation.

## Best practices

- Use **one character region per visible person**.
- Keep character boxes tight around the subject; avoid full-canvas boxes unless the character fills the image.
- Use the main Positive Prompt for the global composition/style.
- Use region prompts for local subject/background/object instructions.
- Add a background region when the background matters.
- Use Prompt Rules for subject count and anti-merge protection.
- For contact scenes, give each Character its own Pose and name the other Person only as the contact target.
- Use Character Lock and Trait Lock when identity/body/gender/clothing keep drifting.
- Test once with Style Stack/CFG Fix off if you are debugging pure Scene Director behavior.
- Do not expect Scene Director to work on Qwen/Flux/ZImage/HiDream/Grok routes unless the UI explicitly shows a future compatible adapter route.

## Assistant behavior

When answering Scene Director questions:

1. Check the live Image snapshot for Model Family, Main Model Type, Workflow Mode, backend, Scene Director enabled state, and route state.
2. If the route is not SDXL/SD1.5 checkpoint Generate/Img2Img/Inpaint on ComfyUI, explain the gating first.
3. Explain fields in user terms. Do not dump raw `scene_graph_json` or metadata unless requested.
4. For prompt help, produce a practical region setup: global prompt, region labels/roles, region prompts, character-local Pose fields, negatives, and any useful Background Space / Character Lock settings.
5. For multi-character issues, recommend tighter boxes, one character per region, Prompt Rules, one Pose per character, Character Lock, and explicit Character Trait Lock.
6. For regional LoRA/IPAdapter/ControlNet, explain owner-extension dependency: LoRA Stack/IP Adapter/ControlNet own assets; Scene Director owns regional assignment.
