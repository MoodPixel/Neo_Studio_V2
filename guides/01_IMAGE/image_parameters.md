---
guide_id: image.parameters
title: Image Parameters
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - generate
  - txt2img
  - img2img
  - inpaint
  - outpaint
  - edit
  - sdxl
  - sd15
  - flux
  - flux2_klein
  - krea2
  - krea2_turbo
  - qwen_rapid_aio
  - qwen_image_edit
  - qwen_image_edit_2509
  - z_image
  - z_image_turbo
  - hidream
tags:
  - image
  - parameters
  - model family
  - main model type
  - workflow mode
  - prompt
  - negative prompt
  - steps
  - cfg
  - seed
  - resolution
  - latent capture
  - sampler
  - scheduler
priority: 115
version: 9
updated: 2026-08-21
---

# Image Parameters

This guide explains the visible Image Workspace controls. The Assistant should combine this guide with the live Image snapshot before answering settings questions. If the live snapshot is missing, explain the field generally and say that the exact visible fields depend on the selected backend profile, model family, main model type, and workflow mode.

## Main routing controls

These controls decide which Image route Neo will validate and run.

| Field | What it does | How to use it |
|---|---|---|
| **Model Family** | Selects the model family/workflow family, such as SDXL, SD 1.5, Flux 1, Flux 2 Klein, Krea 2 RAW, Krea 2 Turbo, Qwen Rapid AIO, Qwen Image Edit, ZImage, ZImage Turbo, or HiDream. | Choose this first. It controls which loaders, workflow modes, and parameter fields are allowed. |
| **Main Model Type** | Selects the public main-model artifact format. Normal UI uses **Safetensors** or **GGUF**; Neo resolves the internal checkpoint/AIO/split-component strategy behind that choice. | Pick the actual main-model file format. Do not infer component needs from the extension: split Safetensors routes may still require text encoders, VAE/AE, companion models, or other assets. |
| **Workflow Mode** | Selects the generation type. In the normal Image command strip this is usually **Generate**, **Img2Img**, **Inpaint**, or **Outpaint**. Internally, Generate maps to `txt2img`. | Use Generate for text-only creation, Img2Img to preserve a source image, Inpaint to edit a masked region, and Outpaint to expand/canvas extend an image. |
| **Validate** | Runs the readiness/preflight checks without starting a generation. | Use this when a route says a model/source/mask/backend is missing. |
| **Generate** | Starts the selected route. | Disabled when readiness fails or another image job is active. |
| **Pause / Stop** | Runtime controls shown for backends that expose pause/cancel support. | Availability depends on the running backend/provider. |
| **Progress / elapsed time** | Shows generation state, progress label, and elapsed/total run timing. | Use this for runtime feedback. Final timing is also written to output metadata when available. |

## Prompt panel

The Prompt panel is shared by the base Image route and compatible extensions.

| Field / control | What it does | Notes |
|---|---|---|
| **Prompt Library** | Loads saved positive/negative prompt pairs from Prompt Studio records. | Image reads these records; it should not silently overwrite Prompt Studio records unless the user explicitly saves/updates. |
| **Save / Load / Refresh / Edit / Delete prompt controls** | Manage saved prompt pairs. | These controls affect prompt library records, not generated outputs or project records. |
| **Positive Prompt** | Main description of the image or edit goal. | For edit routes, write the desired change clearly while also saying what must stay unchanged. |
| **Negative Prompt** | Things to avoid. | Some routes hide or ignore negative prompt. For example, xAI Grok Imagine and some Turbo/API routes do not expose SD-style negative conditioning. |
| **Prompt Assist** | Helper lane for improving or generating prompt text. | Use for rewriting rough ideas into cleaner image prompts. |
| **Style chips** | Lightweight style helpers/preset language. | Style chips should support the prompt; they do not replace a clear subject/composition prompt. |
| **Saved prompt pairs** | Reusable positive + negative prompt records. | Useful for repeating a consistent baseline across models or outputs. |

## Parameters panel

## Parameter authority

Explicit Parameters values are the final runtime truth. Defaults and presets may populate missing fields, but generation must not silently change a manual Steps, CFG, Sampler, Scheduler, Denoise, Batch Count, Seed, Width, or Height value. Unsupported values should fail validation rather than being normalized into a different request. See `guides/01_IMAGE/parameter_truth.md`.


Neo selects a parameter profile from the active Model Family + Main Model Type + Workflow Mode. That means fields can appear, hide, disable, or change labels depending on the route.

| Field | What it does | Route behavior / advice |
|---|---|---|
| **Main Model** | Selects the primary model/checkpoint/diffusion model/GGUF file. | The label can change by loader: Checkpoint, Main Model, GGUF model, Flux Diffusion Model, Qwen model, ZImage model, etc. |
| **VAE / AE** | Selects the decoder/encoder model when the route exposes it. | Classic SDXL/SD 1.5 can use a VAE override. Flux/ZImage/Qwen component routes may require an AE/VAE. Bundled/API routes may hide this field. |
| **Text Encoder fields** | Select primary/secondary text encoders for split-component routes. | Flux, Qwen component, ZImage, and some GGUF routes may expose encoder fields. Classic checkpoint routes usually hide them. |
| **Sampler** | Controls the sampling algorithm. | An explicit selection is authoritative. Provider/family values are fallbacks only when the field is omitted. Some API/cloud routes do not expose a sampler control. |
| **Scheduler** | Controls the noise schedule. | An explicit selection is authoritative on routes that expose the field. Defaults apply only when it is omitted. |
| **Width / Height** | Output dimensions. | Larger dimensions cost more VRAM/time. Use presets first, then customize. |
| **Swap size** | Swaps width and height. | Useful for changing portrait to landscape without retyping. |
| **Aspect scale slider** | Scales width/height together while preserving the current ratio. | Good for quick size testing without changing composition ratio. |
| **Size Preset** | Applies common sizes such as square, portrait, landscape, reel/shorts, 4:5 feed, or YouTube thumbnail, plus any saved custom size presets. | Choose a preset for the target platform, then adjust manually if needed. Saved custom presets appear by name in the same dropdown. |
| **Save size preset** | Saves the current width/height as a named custom preset. | Neo prompts for a preset name, then stores the preset in `neo_data/ui_state/ui_state.json` so it still appears after reopening Neo. |
| **Steps** | Number of denoising/sampling steps. | The entered value is compiled as-is. Family recommendations are starting points only; Turbo/Base labels do not silently replace a manual value. |
| **CFG** | Sampler CFG / prompt guidance value used by the selected workflow. | Explicit CFG is preserved wherever the route exposes it. Modern families may also expose a separate model-guidance field. |
| **Flux Guidance** | Flux-family model-guidance control. | It is independent from sampler CFG. If both controls are exposed, Neo preserves both explicit values instead of forcing CFG to a family default. |
| **Krea 2 Qwen3-VL-4B Text Encoder** | Selects Krea 2's single Qwen3-VL-4B conditioning model. | Krea 2 requires the specialized `CLIPLoader(type=krea2)` path. For Krea 2 GGUF, keep this encoder as native/safetensors in M16. |
| **Krea 2 VAE** | Selects the VAE/AE used by Krea 2. | `qwen_image_vae.safetensors` remains the recommended/default architecture match. A different VAE/AE is treated as an **experimental override**: Neo shows an inline warning but does not block generation, and ComfyUI owns runtime compatibility validation. |
| **Krea 2 Edit Engine** | Chooses the existing Neo source/mask/canvas adapter or the opt-in Krea 2 Identity Edit v1.2 graph in image modes. | Keep **Neo Native Adapter** for existing behavior. Identity Edit requires the current `comfyui-krea2edit` nodes and a selected Identity Edit LoRA. |
| **Identity Edit LoRA / Strength** | Selects and weights the dedicated model-only Krea 2 editing LoRA. | Required only when Identity Edit is enabled. The recommended v1.2 weight starts around strength `1.0`. |
| **Reference Fit / Reference Boost / Grounding Resolution / Grounding System Prompt** | Controls v1.2 source geometry, reference-fidelity attention, Qwen3-VL image grounding, and the optional grounding-system override. | Start with Fit, identity boost `4.0`, scene boost `1.0`, grounding `768`, and leave system prompt blank unless you intentionally need extra grounding guidance. |
| **Seed** | Controls repeatability. `-1` usually means random/auto-resolved. | Use random for exploration, lock/reuse a seed for revisions, and copy seed when documenting a result. |
| **Seed lock** | Keeps future generations on the same seed. | Use for controlled iterations. |
| **Seed randomize** | Uses a fresh seed. | Use for exploration. |
| **Seed reuse** | Reuses the previous resolved seed. | Good after a strong result. |
| **Seed copy** | Copies the current seed. | Useful for notes, replay, or client/debug handoff. |
| **Batch Count** | Number of outputs to request in one run. | Image allows batching when the provider supports it. Video is separate and usually locks batch count to 1. |
| **Denoise** | Strength for source-image modes. | Lower values preserve the source more. Higher values change more. Usually appears for Img2Img/Inpaint/Outpaint/Edit routes. |
| **Clip Skip** | Skips final CLIP layers for compatible SD checkpoint routes. | Mainly useful for SD 1.5/SDXL-style checkpoint workflows. Hidden/disabled for many modern routes. |
| **Prompt Conditioning** | Controls prompt conditioning handling: **Raw**, **Soft Clamp**, or **Balanced**. | Raw is the default. Soft Clamp/Balanced are safety/conditioning helpers when a prompt needs steadier conditioning. |
| **Latent Capture** | Saves latent restore/debug checkpoints. Options are **Off**, **Final latent only**, **Milestone checkpoints**, and **Full debug checkpoints**. | Off saves replay metadata only. Final is lighter. Milestones/Full are heavier and meant for resume/debug/branch workflows. |
| **Inpaint Target** | Chooses whether to edit the masked area or the inverse/not-masked area. | Only appears for inpaint routes that expose it. |
| **Inpaint Context** | Chooses masked-region focus or full-image context. | Full-image context can preserve broader composition; masked focus concentrates the edit region. |
| **Mask Grow** | Expands the mask before inpainting. | Useful when edges need more room to blend. |
| **Mask Blur** | Softens mask edges. | Useful for smoother transitions. |
| **Reference Attention Mask** | Optional Krea 2 Identity Edit source-side mask that biases the upstream `ref_boost_mask` socket. | Available only while Krea 2 Identity Edit is active. It is separate from the inpaint mask and targets Image 1 in single-reference mode or Image 2 in two-reference mode. |
| **Source Resolution** | Outpaint source handling mode. | Appears for outpaint/canvas routes when the selected profile exposes source-resolution controls. |
| **Max Long Edge / Max Canvas MP** | Outpaint source/canvas safety limits. | Prevents very large source images from creating oversized canvases. |
| **Outpaint Left / Right / Top / Bottom** | Adds canvas area on each side. | Use small increments first. Large padding can increase VRAM and make composition harder. |
| **Outpaint Feather** | Blends old image and new canvas extension. | Higher feather can soften the transition; too high can smear details. |

## Krea 2 RAW / Turbo parameter behavior

Krea 2 is a separate image architecture, not FLUX.1 Krea. Both **Krea 2 RAW** and **Krea 2 Turbo** use one Qwen3-VL-4B text encoder through `CLIPLoader(type=krea2)` plus the Qwen Image VAE. Neo preflights that Krea 2 CLIP type through backend capability discovery; an older ComfyUI build that exposes `CLIPLoader` but not `type=krea2` is blocked before queue submission.

Custom Krea 2 VAE/AE selection is intentionally **warning-only**. Neo does not expose a separate “experimental VAE override” toggle: selecting a concrete non-Qwen-Image VAE is itself the opt-in action. The Parameters surface displays a warning directly below the VAE field and submits the selected asset unchanged so ComfyUI/custom nodes can validate the combination at runtime. Encoder/model architecture mismatches remain hard blockers.

- **Krea 2 RAW:** full/base model. Neo defaults to 52 steps and CFG 3.5. A normal negative prompt remains available.
- **Krea 2 Turbo:** distilled fast model. Neo uses 8 steps / CFG 1 only as defaults when those fields are missing. Manual Steps and CFG remain authoritative. Turbo still uses its family-specific negative-conditioning graph semantics.
- **Safetensors / Components:** the diffusion model, Qwen3-VL-4B encoder, and Qwen Image VAE remain native Comfy components.
- **GGUF (M16 experimental):** only the Krea 2 diffusion transformer may be GGUF. Keep the Qwen3-VL-4B encoder native/safetensors through `CLIPLoader(type=krea2)` because Krea 2 consumes a specialized multi-layer conditioning stack.
- **Img2Img / Inpaint / Outpaint:** the M16 provider-owned latent adapters remain the default. M17 adds an explicit **Krea 2 Identity Edit v1.2** engine for the community `comfyui-krea2edit` + Identity Edit LoRA workflow.
- **Identity Edit graph:** `LoraLoaderModelOnly -> Krea2EditModelPatch`, image-grounded positive + empty grounded negative through `Krea2EditGroundedEncode`, and one shared `EmptySD3LatentImage` target wired to both the patch and KSampler.
- **Two references:** Image 1 is scene/context; optional Image 2 is subject/identity. Image 3 is intentionally unavailable for Identity Edit.
- **Identity Edit inpaint:** the model edits from the trained clean target-noise path; the user mask is applied as the final commit/composite boundary. It is not combined with LanPaint.
- **Identity Edit outpaint:** the clean source stays unpadded and is centered by v1.2 `fit` geometry inside the larger target. Asymmetric padding changes target size but cannot side-anchor the clean reference exactly.
- **Identity Edit negative:** uses the trained empty, image-grounded unconditional path. A normal user negative prompt is not applied in this engine.

## Preview panel

| Field / area | What it does |
|---|---|
| **Live preview** | Shows backend preview frames when supported by the backend and preview websocket settings. |
| **Final preview** | Shows the selected generated output when available. |
| **Batch thumbnails** | Shows each output from the current batch. Click a thumbnail to make it the main preview. |

For modern dual-handoff workflows such as Qwen native edit, the preview panel may show a live `PreviewImage` frame while Neo imports the backend `SaveImage` result into Results. That separation is intentional: preview stays fast, while Results keeps the final saved file that matches Comfy native output more closely.

Output deletion, metadata inspection, replay, source/control asset tracking, and safe cascade delete belong in the Results / Output Inspector area, not the preview panel.

## Workflow-mode requirements

| Workflow Mode | Internal route mode | Requires | Use when |
|---|---|---|---|
| **Generate** | `txt2img` | Positive prompt and valid model route. | Creating a new image from text. |
| **Img2Img** | `img2img` | Source image plus route-compatible model. | Preserving pose/layout/style while changing the image. |
| **Inpaint** | `inpaint` | Source image + mask image. | Editing or repairing a region. |
| **Outpaint** | `outpaint` | Source image/canvas + outpaint padding/canvas settings. | Expanding beyond the original image. |

For local Comfy masked modes, the Parameters panel also exposes **Inpaint/Outpaint Engine** (Native or LanPaint) and an independent **Crop & Stitch** checkbox. Native Crop & Stitch requires `ComfyUI-Inpaint-CropAndStitch`; LanPaint uses its existing family-aware crop/stitch graph. See `guides/01_IMAGE/inpaint_outpaint_engines.md`.
| **Edit** | `edit` | Source image and edit instruction when exposed by the selected workspace/backend. | Instruction-based edits, especially Qwen/Grok-style image edit routes. |

## Route-aware visibility rules

- **SDXL / SD 1.5 checkpoint routes** usually show Checkpoint, VAE override, Sampler, Scheduler, Steps, CFG, Seed, Batch Count, Clip Skip, and source/mask/outpaint fields as needed.
- **Flux 1 / Flux 2 Klein component routes** use split model components and Flux Guidance. Sampler CFG remains independently editable; Clip Skip stays route-specific.
  - **FLUX.1 Krea (M15):** Krea remains under Flux 1 and resolves to `krea_dev`. Select one T5XXL + one CLIP-L encoder. Krea supports Generate/Img2Img/Inpaint/Outpaint for Safetensors / Components and GGUF; its masked modes retain Krea and use latent-noise-mask + DifferentialDiffusion instead of silently changing to FLUX.1 Fill.
  - **Flux 2 Klein:** pair Klein 4B with Qwen3-4B and Klein 9B with Qwen3-8B; Neo filters the encoder lane and blocks known incompatible pairings before queue.
  - **M14.1:** the visible Klein Qwen3 encoder is stored canonically as `qwen3_text_encoder`. Legacy encoder aliases are reconciled automatically before submission; they are not independent controls.
- **Krea 2 RAW / Krea 2 Turbo (M16):** use the dedicated Krea 2 family contract with Qwen3-VL-4B + Qwen Image VAE. Native txt2img is the primary supported route; Img2Img/Inpaint/Outpaint and GGUF are exposed as experimental provider-owned adapters. Krea 2 does not use Flux Guidance, T5XXL, CLIP-L, Qwen2.5-VL, or MMProj.
- **Qwen Rapid AIO** uses a bundled model route or GGUF route. Extra component fields stay hidden unless the selected loader/profile requires them.
- **Qwen Image / Qwen Image Edit / Qwen Image Edit 2509/2511** support mixed text-encoder formats on safetensors/component routes. The visible `qwen_text_encoder` field merges the native Qwen/text-encoder catalog with the GGUF single-encoder catalog even when native encoders are already present; Neo automatically uses `CLIPLoader(type=qwen_image)` for native encoders and `CLIPLoaderGGUF(type=qwen_image)` for GGUF encoders.
- **Qwen Image Edit / Qwen Image Edit 2509** are stronger for source-image/edit workflows and can expose multi-source behavior depending on route and loader.
- **ZImage Turbo** uses low-step/low-CFG defaults and may hide negative prompt because turbo conditioning is simplified.
- **HiDream** is currently txt2img-focused in normal Image routes.
- **xAI Grok Imagine** is a cloud/API profile. It exposes cloud controls such as model, resolution/aspect ratio/image count through the API profile and hides SD-style fields like sampler, scheduler, CFG, steps, negative prompt, ControlNet, LoRA, and mask inpaint.

When the user asks what a field does, answer from this guide. When the user asks what they are currently using, answer from the live Image snapshot and mention the selected Model Family, Main Model Type, Workflow Mode, backend profile, model, dimensions, steps/guidance, seed, source/mask status, and enabled extensions if present.

## Workspace extension and asset fields

The Image Parameters panel controls the base route. Workspace extension cards add extra fields after the active family/loader/workflow is known. Do not describe every extension as a Generation field; some belong to **Image → Assets**.

### Generation extension fields

| Extension | Key fields | What to explain |
|---|---|---|
| **CFG Fix / Dynamic Thresholding** | Apply CFG Fix, Preset, Mode, Mimic CFG, Threshold percentile. | It patches high-CFG sampler behavior on supported Comfy routes. Use for overbaked high-CFG outputs, not as a universal quality switch. |
| **ComfyUI LayerDiffuse** | Enable, Mode, Decode, Output, SD compatibility, Weight, Sub-batch, Blend strength, foreground/background/source images. | It runs transparent/compositing workflows and may replace the base graph. It is route-gated and mainly SDXL/SD checkpoint-oriented. |
| **Style Stack** | Apply Style Stack, Target pass, Category, Search styles, Active style chips, manual positive/negative style, CSV import/export. | It merges style prompt text and negative style text; no graph patching. |
| **Wildcards** | Enable Wildcards, Insert target, Preview count, Queue variants, Auto-resolve, Use generation seed, token file/value editor, ZIP import/export. | It resolves prompt tokens into text before Style Stack and before provider execution. |
| **Scene Director** | Enable for workflow, Add Region, authority, base weight, region gain, prompt rules, region context suffix, Pair Pose, Background Space, Fix Pass Controls, Character Lock, Global Context Routing, presets, region canvas/cards, V054 role, region prompts, trait locks, and extension routing. | It plans regional subject/background/object lanes and can patch supported SDXL/SD1.5 checkpoint Comfy workflows through the V054 Scene Director node. |

### Assets extension fields

| Asset extension | Key fields | What to explain |
|---|---|---|
| **LoRA Stack** | Apply LoRA Stack, rows, LoRA name, Strength, Pass, Target, row order. | It is an Image → Assets tool. It applies LoRA rows only when the route exposes safe patch points. Regional targets are preserved for Scene Director. |
| **LoRA Library** | Search Comfy LoRAs, Comfy LoRA selector, triggers, keywords, sample prompt, CivitAI link/merge/pull. | It is an Image → Assets metadata/catalog manager and can add a selected LoRA to the stack. It does not execute a LoRA by itself. |
| **Embeddings / Textual Inversion** | Apply Embeddings/TI, Scan Folder, Refresh, Embeddings folder, Search, Embedding, Prompt token, Target, Strength, Add Embedding, CivitAI link, merge mode, Applied Embeddings. | It is an Image → Assets prompt-token manager. It inserts/preserves tokens such as `embedding:name`; no custom loader node is required. |

Use the dedicated guides for detailed behavior:

- `guides/01_IMAGE/image_generation_extensions.md`
- `guides/01_IMAGE/image_assets.md`
- `guides/01_IMAGE/cfg_fix_dynamic_thresholding.md`
- `guides/01_IMAGE/layerdiffuse.md`
- `guides/01_IMAGE/lora_stack.md`
- `guides/01_IMAGE/embeddings_textual_inversion.md`
- `guides/01_IMAGE/style_stack.md`
- `guides/01_IMAGE/wildcards.md`
- `guides/01_IMAGE/scene_director.md`

## Bundled checkpoint model fields — P2.1

The Qwen Rapid AIO field `qwen_rapid_aio_checkpoint` is a checkpoint-role field and receives options from the shared Comfy checkpoint catalog. For `checkpoint_aio`, normal UI shows only the bundled main model selector; external VAE and component selectors remain hidden.

## Required Model Components — Phase 4.7

Neo resolves this card from the active **Model Family + internal load strategy + Workflow Mode** contract. The card is no longer tied to GGUF and is not hidden merely because the main model ends in `.safetensors`.

| Control | Example routes | What to select |
|---|---|---|
| Text Encoder 1 / 2 | Flux 1, SD3.5, HiDream, other split routes | Exact installed encoder required by the family contract. |
| Text Encoder 3 / 4 | SD3.5 / HiDream multi-encoder routes | Exact T5/LLM encoder required by that route. |
| Qwen Text Encoder | Qwen Image/Edit native split Safetensors routes | Exact installed Qwen encoder. |
| Qwen3 Text Encoder | Flux 2 Klein, ZImage / ZImage Turbo | Exact installed Qwen3 encoder. |
| Qwen3-VL-4B Text Encoder | Krea 2 RAW / Turbo | Exact installed Qwen3-VL-4B encoder. Krea 2 keeps this encoder native/safetensors even when the transformer itself is GGUF. |
| VAE / AE | Flux, Qwen, ZImage, Krea2, HiDream and other split routes | Exact decoder/autoencoder required by the route. The selector stays in the existing primary-model row rather than being duplicated in the component card. |
| Unconditional Model | Ideogram 4 | Exact companion/unconditional model required by the dual-model topology. |
| MMProj | Qwen image-conditioned routes that explicitly declare it | Select the matching projector only when the active route marks MMProj required/optional. Format alone does not control this field. |

Classic SDXL/SD 1.5 Safetensors checkpoints and Qwen Rapid AIO bundled Safetensors remain simple bundled routes, so Neo does not create an unnecessary split-component card for them.

Quantized, INT, FP8, scaled, or reduced-precision `.safetensors` files are still Safetensors. If the family uses split components, keep selecting the required encoder(s)/VAE/companion files exactly as the route contract specifies.

The main model and component selectors use live Comfy/Admin catalogs. After adding model files:

1. Put each file in the correct Comfy model folder.
2. Refresh/reconnect the selected backend.
3. Reopen the family/model controls.
4. Select the exact installed filenames.

`provider_default`, `automatic`, and `select_*` placeholders are not real selections when a component is required. Readiness should fail rather than silently guessing a file.

## Qwen Rapid AIO CFG — P2.3

For `Qwen Rapid AIO + Safetensors / Bundled`, **CFG Scale** is visible and maps directly to `KSampler.inputs.cfg`.

| Setting | Recommended interpretation |
|---|---|
| `1.0` | Route default and safest starting point for Rapid/distilled AIO checkpoints. |
| Higher values | Stronger guidance only when the selected checkpoint recommends it; increase gradually. |
| CFG Fix extension | Separate dynamic-thresholding graph patch. It is not automatically enabled just because normal CFG is visible. |

If generation fails, Neo distinguishes a genuine Comfy node exception from a successful history record that contains no output references.

## Multi-KSampler advanced sampling

Local ComfyUI Parameters can expose **Advanced Sampling → Multi-KSampler**. Stage 1 is the normal Parameters sampler. Enabling the feature adds Stage 2 and optionally Stage 3 core KSampler refinement passes that consume the previous stage's latent. Each later stage has its own Steps, CFG, Sampler, Scheduler, Denoise, and seed policy. Phase 4 uses direct latent refinement only; no inter-stage upscale is inserted automatically. Route-native custom sampler graphs such as LanPaint and Ideogram 4 remain fail-closed until dedicated adapters are implemented. See `guides/01_IMAGE/multi_ksampler.md`.

## Sampler Engine · RES4LYF ClownsharKSampler

Local ComfyUI Parameters now expose **Sampler Engine** next to Sampler and Scheduler. **Standard KSampler** keeps the normal core graph. **ClownsharKSampler · RES4LYF** is available only when Neo detects a compatible live `ClownsharKSampler_Beta` / `ClownsharKSampler` node signature.

When selected, the ordinary user-owned Steps, CFG, Sampler, Scheduler, Denoise, and Seed fields remain authoritative. Neo adds only RES4LYF Eta and BongMath for the Phase 5 standard-mode integration. Multi-KSampler Stage 2/3 can select the sampler engine independently. See `guides/01_IMAGE/res4lyf_clownshark_sampler.md`.

## Multi-KSampler Phase 6

Advanced Sampling now supports the production `neo.image.multi_ksampler.v2` contract. Stage 2/3 can directly refine the previous latent or optionally run Comfy core `LatentUpscaleBy` before the next sampling pass. Scale/method are user-owned Parameters. Mixed Standard KSampler / RES4LYF ClownsharKSampler stages remain supported on graph-compatible local Comfy routes. Final stage and transition integrity is checked before `/prompt`.

## Phase 7 — family compatibility authority

Parameters stay user-owned, but a parameter feature is only rendered/executed when the exact family + loader + mode + engine graph can represent it. Neo now consumes `neo.image.family_compatibility.v1` from the connected local Comfy backend.

This affects advanced sampling choices such as Multi-KSampler, ClownsharKSampler, inter-stage latent upscale, and Native Crop & Stitch. A gated feature is disabled with its route reason; Neo must not silently switch engines or substitute a different family compiler.

See `guides/01_IMAGE/family_compatibility.md`.


## Phase 8 generation setup summary

The Parameters panel now includes a compact **Generation Setup** summary. It is a user-facing preview of the workflow Neo intends to run, not a second source of settings.

It summarizes the selected family, main model type, workflow mode, Native/LanPaint masked engine, Crop & Stitch state, sampler engine, Multi-KSampler stage count, and inter-stage latent-upscale count. The editable controls above remain authoritative.

The summary must never rewrite Steps, CFG, Sampler, Scheduler, Denoise, Seed, dimensions, batch count, or stage settings. Compatibility status is shown as Ready, Experimental, or Gated. Technical route keys, schema IDs, compiler IDs, and node IDs stay out of normal/guided UI and remain available only in Expert diagnostics.

## GGUF text-encoder discovery

When **Main Model Type = GGUF**, Neo builds text-encoder selectors from the connected ComfyUI profile rather than assuming the encoder must use the same file format as the main diffusion model.

The dropdown can therefore include both supported GGUF encoders and compatible native/Safetensors text encoders exposed by ComfyUI. This is intentional: a GGUF diffusion model can still use a native text encoder when the selected workflow/compiler supports that topology.

For Qwen image-conditioned routes, an explicit MMProj/projector remains part of Neo's compatibility contract where that route declares it. Neo does not remove saved MMProj state merely because compatible ComfyUI-GGUF builds may auto-pair a matching projector. Existing project/replay data with an explicit projector remains valid.

## Qwen native edit controls

When the active route is **Qwen Image Edit**, **Qwen Image Edit 2509**, or **Qwen Image Edit 2511** on the **Safetensors / Components** path, Neo shows an extra **Qwen Native Edit Controls** card.

- **Qwen text encoder loader**: lets you keep the mixed Qwen encoder catalog while forcing **Auto**, **Native CLIPLoader**, or **GGUF CLIPLoader** when needed.
- **Aura shift**: feeds `ModelSamplingAuraFlow`.
- **CFGNorm**: lets you enable/disable the patch, set **strength**, and choose **pre-CFG** behavior.
- Neo also aligns the workflow more closely with native Comfy behavior by preprocessing Qwen source images with `FluxKontextImageScale`, using `VAEEncode(Image 1)` as the latent anchor for Qwen img2img/outpaint, and automatically applying `FluxKontextMultiReferenceLatentMethod(index_timestep_zero)` on both positive and negative conditioning for Qwen 2509/2511 img2img — including single-source edits.

### Qwen parity-node readiness

On Qwen safetensors/component edit routes, Neo now verifies the live Comfy node classes needed by the selected Parameters. `CFGNorm` must be present when CFGNorm is enabled; Qwen img2img uses `FluxKontextImageScale`; and Qwen 2509/2511 img2img uses `FluxKontextMultiReferenceLatentMethod(index_timestep_zero)` for both single- and multi-source edits. If live `/object_info` proves an explicitly required node is missing, Neo blocks the route with a concrete readiness error rather than silently omitting the requested stage.

## Img2Img Source Resolution (V25.9.24)

Native/safetensors source-latent img2img routes now expose an **Img2Img Source Resolution** control for the first source image before VAE encode. This is available on Flux native, Qwen native edit variants, Krea 2 native/Turbo, and ZImage native/Turbo.

- **Keep source resolution** — preserves the original source size. This matches the previous behavior and explains why some native img2img runs ignored the requested width/height.
- **Fit source to target size** — resizes the source directly to the requested width/height. This can distort aspect ratio.
- **Crop to target** — scales to cover the requested width/height and center-crops.
- **Pad to target** — scales to fit inside the requested width/height and center-pads the remaining canvas.

Neo records the resolved `img2img_source_resolution` policy inside job params so replay/inspection can see exactly how Image 1 was prepared. Krea 2 Identity Edit intentionally hides this control because that workflow owns its own reference handling path.

## Outpaint Parameter Integrity (V25.9.24)

Neo's shared parameter-integrity guard now understands that **outpaint intentionally transforms the final canvas size**. The user-requested width/height can remain the route/default sizing intent while the compiled workflow legitimately expands to a larger final latent based on:

- outpaint working copy / source-resolution policy
- left/right/top/bottom padding
- final latent canvas derived from `working_size + padding`

This means outpaint jobs no longer fail merely because `workflow_final.width/height` differ from the original requested width/height. Neo now verifies the **derived outpaint final size** instead of demanding a raw equality match. If the compiled workflow's final canvas does not match the derived outpaint contract, Neo still blocks the queue as a real integrity error.
