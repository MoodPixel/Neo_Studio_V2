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
version: 2
updated: 2026-07-09
---

# Image Parameters

This guide explains the visible Image Workspace controls. The Assistant should combine this guide with the live Image snapshot before answering settings questions. If the live snapshot is missing, explain the field generally and say that the exact visible fields depend on the selected backend profile, model family, main model type, and workflow mode.

## Main routing controls

These controls decide which Image route Neo will validate and run.

| Field | What it does | How to use it |
|---|---|---|
| **Model Family** | Selects the model family/workflow family, such as SDXL, SD 1.5, Flux 1, Flux 2 Klein, Qwen Rapid AIO, Qwen Image Edit, ZImage, ZImage Turbo, or HiDream. | Choose this first. It controls which loaders, workflow modes, and parameter fields are allowed. |
| **Main Model Type** | Selects the main file/loader contract for the family. Visible labels include **Safetensors / Checkpoint**, **Safetensors / Bundled**, **Safetensors / Components**, and **GGUF**. | Use Checkpoint for classic SDXL/SD 1.5 files, Bundled for all-in-one Qwen Rapid AIO models, Components for split model + text encoder + VAE workflows, and GGUF for quantized routes. |
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

Neo selects a parameter profile from the active Model Family + Main Model Type + Workflow Mode. That means fields can appear, hide, disable, or change labels depending on the route.

| Field | What it does | Route behavior / advice |
|---|---|---|
| **Main Model** | Selects the primary model/checkpoint/diffusion model/GGUF file. | The label can change by loader: Checkpoint, Main Model, GGUF model, Flux Diffusion Model, Qwen model, ZImage model, etc. |
| **VAE / AE** | Selects the decoder/encoder model when the route exposes it. | Classic SDXL/SD 1.5 can use a VAE override. Flux/ZImage/Qwen component routes may require an AE/VAE. Bundled/API routes may hide this field. |
| **Text Encoder fields** | Select primary/secondary text encoders for split-component routes. | Flux, Qwen component, ZImage, and some GGUF routes may expose encoder fields. Classic checkpoint routes usually hide them. |
| **Sampler** | Controls the sampling algorithm. | Use provider/default values unless testing a specific sampler. Some API/cloud routes hide this. |
| **Scheduler** | Controls the noise schedule. | Common for Comfy checkpoint/component routes. Some routes use fixed/default scheduling. |
| **Width / Height** | Output dimensions. | Larger dimensions cost more VRAM/time. Use presets first, then customize. |
| **Swap size** | Swaps width and height. | Useful for changing portrait to landscape without retyping. |
| **Aspect scale slider** | Scales width/height together while preserving the current ratio. | Good for quick size testing without changing composition ratio. |
| **Size Preset** | Applies common sizes such as square, portrait, landscape, reel/shorts, 4:5 feed, or YouTube thumbnail. | Choose a preset for the target platform, then adjust manually if needed. |
| **Save size preset** | Saves the current width/height as a custom preset. | Custom presets are runtime/user data and should stay in `neo_data`. |
| **Steps** | Number of denoising/sampling steps. | Higher can add refinement but increases time and can overcook. Turbo routes often use very low steps. |
| **CFG** | Prompt adherence/guidance for SD-style routes. | Available on SDXL/SD 1.5 and some non-Flux routes. Hidden or disabled when the selected profile uses another guidance system. |
| **Flux Guidance** | Flux-family guidance replacement for SD-style CFG. | Flux 1/Flux 2 component routes use Flux Guidance instead of normal CFG. |
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
| **Source Resolution** | Outpaint source handling mode. | Appears for outpaint/canvas routes when the selected profile exposes source-resolution controls. |
| **Max Long Edge / Max Canvas MP** | Outpaint source/canvas safety limits. | Prevents very large source images from creating oversized canvases. |
| **Outpaint Left / Right / Top / Bottom** | Adds canvas area on each side. | Use small increments first. Large padding can increase VRAM and make composition harder. |
| **Outpaint Feather** | Blends old image and new canvas extension. | Higher feather can soften the transition; too high can smear details. |

## Preview panel

| Field / area | What it does |
|---|---|
| **Live preview** | Shows backend preview frames when supported by the backend and preview websocket settings. |
| **Final preview** | Shows the selected generated output when available. |
| **Batch thumbnails** | Shows each output from the current batch. Click a thumbnail to make it the main preview. |

Output deletion, metadata inspection, replay, source/control asset tracking, and safe cascade delete belong in the Results / Output Inspector area, not the preview panel.

## Workflow-mode requirements

| Workflow Mode | Internal route mode | Requires | Use when |
|---|---|---|---|
| **Generate** | `txt2img` | Positive prompt and valid model route. | Creating a new image from text. |
| **Img2Img** | `img2img` | Source image plus route-compatible model. | Preserving pose/layout/style while changing the image. |
| **Inpaint** | `inpaint` | Source image + mask image. | Editing or repairing a region. |
| **Outpaint** | `outpaint` | Source image/canvas + outpaint padding/canvas settings. | Expanding beyond the original image. |
| **Edit** | `edit` | Source image and edit instruction when exposed by the selected workspace/backend. | Instruction-based edits, especially Qwen/Grok-style image edit routes. |

## Route-aware visibility rules

- **SDXL / SD 1.5 checkpoint routes** usually show Checkpoint, VAE override, Sampler, Scheduler, Steps, CFG, Seed, Batch Count, Clip Skip, and source/mask/outpaint fields as needed.
- **Flux 1 / Flux 2 Klein component routes** use split model components and Flux Guidance. Normal CFG and Clip Skip are hidden or disabled.
- **Qwen Rapid AIO** uses a bundled model route or GGUF route. Extra component fields stay hidden unless the selected loader/profile requires them.
- **Qwen Image Edit / Qwen Image Edit 2509** are stronger for source-image/edit workflows and can expose multi-source behavior depending on route and loader.
- **ZImage Turbo** uses low-step/low-CFG defaults and may hide negative prompt because turbo conditioning is simplified.
- **HiDream** is currently txt2img-focused in normal Image routes.
- **xAI Grok Imagine** is a cloud/API profile. It exposes cloud controls such as model, resolution/aspect ratio/image count through the API profile and hides SD-style fields like sampler, scheduler, CFG, steps, negative prompt, ControlNet, LoRA, and mask inpaint.

When the user asks what a field does, answer from this guide. When the user asks what they are currently using, answer from the live Image snapshot and mention the selected Model Family, Main Model Type, Workflow Mode, backend profile, model, dimensions, steps/guidance, seed, source/mask status, and enabled extensions if present.

## Generation extension fields

The Image Parameters panel controls the base route. Built-in Generation extension cards add extra fields that are resolved after the active family/loader/workflow is known.

| Extension | Key fields | What to explain |
|---|---|---|
| **CFG Fix / Dynamic Thresholding** | Apply CFG Fix, Preset, Mode, Mimic CFG, Threshold percentile. | It patches high-CFG sampler behavior on supported Comfy routes. Use for overbaked high-CFG outputs, not as a universal quality switch. |
| **ComfyUI LayerDiffuse** | Enable, Mode, Decode, Output, SD compatibility, Weight, Sub-batch, Blend strength, foreground/background/source images. | It runs transparent/compositing workflows and may replace the base graph. It is route-gated and mainly SDXL/SD checkpoint-oriented. |
| **LoRA Stack** | Apply LoRA Stack, rows, LoRA name, Strength, Pass, Target, row order. | It applies LoRA rows when the route exposes safe patch points. Regional targets are preserved for Scene Director. |
| **LoRA Library** | Search Comfy LoRAs, Comfy LoRA selector, triggers, keywords, sample prompt, CivitAI link/merge/pull. | It manages LoRA metadata and can add a selected LoRA to the stack. It does not execute a LoRA by itself. |
| **Style Stack** | Apply Style Stack, Target pass, Category, Search styles, Active style chips, manual positive/negative style, CSV import/export. | It merges style prompt text and negative style text; no graph patching. |
| **Wildcards** | Enable Wildcards, Insert target, Preview count, Queue variants, Auto-resolve, Use generation seed, token file/value editor, ZIP import/export. | It resolves prompt tokens into text before Style Stack and before provider execution. |
| **Scene Director** | Enable for workflow, Add Region, authority, base weight, region gain, prompt rules, region context suffix, Pair Pose, Background Space, Fix Pass Controls, Character Lock, Global Context Routing, presets, region canvas/cards, V054 role, region prompts, trait locks, and extension routing. | It plans regional subject/background/object lanes and can patch supported SDXL/SD1.5 checkpoint Comfy workflows through the V054 Scene Director node. |

Use the dedicated extension guides for detailed behavior:

- `guides/01_IMAGE/image_generation_extensions.md`
- `guides/01_IMAGE/cfg_fix_dynamic_thresholding.md`
- `guides/01_IMAGE/layerdiffuse.md`
- `guides/01_IMAGE/lora_stack.md`
- `guides/01_IMAGE/style_stack.md`
- `guides/01_IMAGE/wildcards.md`
- `guides/01_IMAGE/scene_director.md`
