---
guide_id: image.model_families
title: Image Model Families, Loaders, and Routes
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - model_family
  - main_model_type
  - workflow_mode
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
  - xai_grok
  - grok_imagine
  - comfy
  - checkpoint
  - gguf
  - img2img
  - inpaint
  - outpaint
tags:
  - image
  - model families
  - models
  - loaders
  - main model type
  - qwen
  - flux
  - zimage
  - hidream
  - grok
  - xai
  - comfy
  - checkpoint
  - routes
priority: 120
version: 2
updated: 2026-07-09
---

# Image Model Families, Loaders, and Routes

The Image tab is route-aware. It does not treat every model file the same way. Neo resolves the active Image route from:

```text
Backend profile + Model Family + Main Model Type + Workflow Mode
```

Use this guide when the user asks what model families Neo supports, why certain fields appear/disappear, or which route they should choose.

Legacy assistant phrasing may call SDXL/SD 1.5 local checkpoint routes **Checkpoint / Comfy image routes**. In V2, those are represented by the **SDXL** and **SD 1.5** Model Families with the **Safetensors / Checkpoint** Main Model Type.

## Normal Image dropdown families

These are the current normal Image workspace families from the route matrix. Availability still depends on installed local models, custom nodes, backend connection, and profile readiness.

| Model Family label | Internal family | Normal Main Model Types | Normal Workflow Modes | Notes |
|---|---|---|---|---|
| **SDXL** | `sdxl` | **Safetensors / Checkpoint** | Generate, Img2Img, Inpaint, Outpaint | Classic SDXL checkpoint route through Comfy. Good general route when using `.safetensors` checkpoints. |
| **SD 1.5** | `sd15` | **Safetensors / Checkpoint** | Generate, Img2Img, Inpaint, Outpaint | Classic SD 1.5 checkpoint route. Useful for older SD models, some LoRAs, ControlNet-heavy workflows, and lower VRAM tests. |
| **Flux 1** | `flux` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Component route uses diffusion model + text encoders + AE/VAE + Flux Guidance. Inpaint/outpaint resolve through internal Flux Fill behavior; Flux Fill is not a separate normal dropdown family. |
| **Flux 2 Klein** | `flux2_klein` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Flux 2 Klein route. Uses component or GGUF loaders with native image-conditioned route support. |
| **Qwen Rapid AIO** | `qwen_rapid_aio` | **Safetensors / Bundled**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Compact all-in-one Qwen image route. Bundled checkpoint keeps extra model components hidden unless needed. GGUF route needs matching GGUF/runtime support. |
| **Qwen Image Edit** | `qwen_image` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Qwen-native edit family for source-image workflows. Stronger fit when the user wants instruction-like edits while preserving a source image. |
| **Qwen Image Edit 2509** | `qwen_image_edit_2509` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Newer Qwen edit route with multi-source edit support where the selected loader/profile exposes it. |
| **ZImage** | `z_image` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | ZImage route using component or GGUF model setup. Good to explain as a modern component-family route rather than a classic SD checkpoint. |
| **ZImage Turbo** | `z_image_turbo` | **Safetensors / Components**, **GGUF** | Generate, Img2Img, Inpaint, Outpaint | Turbo-style low-step route. Negative prompt and some SD controls may be hidden because the turbo profile uses simplified conditioning. |
| **HiDream** | `hidream` | **Safetensors / Components**, **GGUF** | Generate | Current normal route is txt2img-focused. Image-conditioned variants are not normal queue-enabled routes unless the route matrix/live snapshot says otherwise. |

## Main Model Type labels

| UI label | Internal loader | Meaning |
|---|---|---|
| **Safetensors / Checkpoint** | `checkpoint` | Single classic checkpoint model, mainly SDXL and SD 1.5. |
| **Safetensors / Bundled** | `checkpoint_aio` | All-in-one/bundled model route, currently used by Qwen Rapid AIO. Extra encoder/VAE fields usually stay hidden. |
| **Safetensors / Components** | `diffusion_model` | Split component route: diffusion model plus text encoder(s), VAE/AE, guidance fields, and route-specific parts. |
| **GGUF** | `gguf` | Quantized model route. Requires matching GGUF custom node/runtime support and profile readiness. |
| **API Model** | `api_model` | Cloud/API provider model. Usually controlled by the selected backend profile rather than the normal local model dropdown. |

The old **UNet** loader remains a legacy/backward-compatibility concept. Normal Image UI should prefer **Safetensors / Components** or **GGUF** for modern split routes.

## Workflow Mode labels

| UI label | Internal mode | Meaning |
|---|---|---|
| **Generate** | `txt2img` | Text-only image generation. Does not require a source image. |
| **Img2Img** | `img2img` | Uses a source image as composition/style/layout guidance. |
| **Inpaint** | `inpaint` | Uses a source image and mask to edit/repair a region. |
| **Outpaint** | `outpaint` | Uses a source image/canvas and padding settings to expand the image. |
| **Edit** | `edit` | Instruction-style image edit where exposed by a specific backend/workspace route. Some route-matrix entries support it even when the normal command strip only shows the four base workflow modes. |

## Family-specific behavior

### SDXL and SD 1.5

- Use the classic checkpoint path.
- Good defaults: moderate steps, CFG available, sampler/scheduler visible, VAE override optional.
- Img2Img/Inpaint/Outpaint add source, denoise, mask, and canvas/padding controls as needed.
- Clip Skip is mostly useful for SD-style checkpoint workflows.

### Flux 1

- Normal family label is **Flux 1**.
- Main Model Type can be **Safetensors / Components** or **GGUF**.
- Flux uses **Flux Guidance** instead of normal SD-style CFG.
- CFG and Clip Skip are hidden/disabled on Flux component routes.
- Inpaint/outpaint use internal Flux Fill behavior when selected through Workflow Mode. Do not tell users to select a separate Flux Fill family in the normal dropdown.

### Flux 2 Klein

- Normal family label is **Flux 2 Klein**.
- Supports component and GGUF route types.
- Explain it as a modern Flux-family route with native image-conditioned support when readiness passes.
- Uses Flux-style guidance behavior rather than classic SD checkpoint assumptions.

### Qwen Rapid AIO

- Normal family label is **Qwen Rapid AIO**.
- Main Model Type can be **Safetensors / Bundled** or **GGUF**.
- Bundled/AIO mode hides extra components because the model route is compact.
- GGUF mode requires matching GGUF model/runtime support and may need additional route readiness.
- Use it when the user wants a simpler Qwen route with fewer visible component fields.

### Qwen Image Edit and Qwen Image Edit 2509

- These are Qwen-native image edit families.
- Use them for source-image edits where the source subject/composition should be preserved.
- Qwen Image Edit 2509 is the newer route and can support multi-source behavior where the route/profile exposes it.
- For prompts, tell the model what to change and explicitly say what must remain unchanged.

### ZImage and ZImage Turbo

- ZImage uses modern component/GGUF routing.
- ZImage Turbo uses low-step/low-CFG defaults.
- Turbo routes may hide the negative prompt and some SD-style conditioning controls.
- Do not recommend high SDXL-like step/CFG values for Turbo unless the user is intentionally experimenting.

### HiDream

- Current normal HiDream route is Generate/txt2img-focused.
- HiDream variant options include I1/E1/O1 when exposed by the parameter profile.
- Do not promise img2img/inpaint/outpaint unless the live route matrix/snapshot shows a selectable route.

## Cloud/API image profile: xAI Grok Imagine

xAI Grok Imagine is not a local Comfy model family dropdown entry. It is a cloud **Image backend profile**.

When the selected image backend profile is `image.xai_grok_imagine` / `xai_grok`:

- Use the API profile model list, such as `grok-imagine-image` or `grok-imagine-image-quality`.
- Normal SD/Comfy fields such as sampler, scheduler, CFG, steps, negative prompt, LoRA, ControlNet, IP Adapter, and mask inpaint are not available unless the backend/profile later exposes them.
- The profile supports text-to-image and image edit / multi-image edit behavior.
- It does not currently expose native mask inpaint or outpaint controls in Neo.

## Diagnostics-only / hidden families

Some families exist in the manifest or route matrix but should not be presented as normal Image dropdown choices unless the live UI shows them.

- **Flux 1 Fill** is an internal alias/route behavior for Flux inpaint/outpaint. It is not a normal Model Family dropdown entry.
- **Wan Image / Video** and **Hunyuan Image / Video** are provider-gated/diagnostic in the Image model-family manifest. Treat active video routes separately in the Video workspace.
- **Other / Manual** is a fallback/manual/extension concept, not a recommended normal beginner route.

## How the Assistant should answer support questions

When the user asks “what model families does Neo Image support?” answer with the normal Image dropdown families first:

```text
SDXL, SD 1.5, Flux 1, Flux 2 Klein, Qwen Rapid AIO, Qwen Image Edit, Qwen Image Edit 2509, ZImage, ZImage Turbo, and HiDream.
```

Then add that exact availability depends on the selected backend profile, installed models/custom nodes, and route readiness. Mention xAI Grok Imagine separately as an Image backend profile, not as a local Model Family dropdown option.

When the user asks what they are currently using, use the live Image snapshot and say the selected **Model Family**, **Main Model Type**, **Workflow Mode**, **Main Model**, **VAE/components**, and backend profile if available.

## Built-in extension compatibility by family/loader

Image extensions are also family-aware and loader-aware. The same extension can be ready on SDXL checkpoint routes, experimental on component/GGUF routes, and unsupported on API routes.

| Extension | SDXL checkpoint | SD 1.5 checkpoint | Flux / Flux 2 component or GGUF | Qwen / ZImage / HiDream component, bundled, or GGUF | API profiles such as Grok Imagine |
|---|---|---|---|---|---|
| **CFG Fix / Dynamic Thresholding** | Available for Generate. | Experimental for Generate. | Planned/gated unless route matrix explicitly promotes it. | Planned/gated unless route matrix explicitly promotes it. | Not exposed as SD sampler patching. |
| **LayerDiffuse** | Available for Generate; image-conditioned modes are experimental/guarded. | Experimental for Generate. | Not supported in normal routes. | Not supported in normal routes. | Not supported. |
| **LoRA Stack** | Available for Generate, Img2Img, Inpaint, Outpaint. | Experimental for Generate, Img2Img, Inpaint, Outpaint. | Experimental where compiler-owned LoRA patch profiles exist. | Experimental or planned/gated depending on family/mode; HiDream is mainly Generate. | Not a normal graph route unless the API/backend adds explicit LoRA support. |
| **LoRA Library** | Available as asset metadata/catalog manager. | Available as asset metadata/catalog manager. | Available as metadata/catalog manager. | Available as metadata/catalog manager. | Metadata only; does not load API LoRAs. |
| **Style Stack** | Available as prompt-only merge. | Available as prompt-only merge. | Available as prompt-only merge. | Available as prompt-only merge. | Applies only if the API profile accepts prompt text. |
| **Wildcards** | Available as prompt-only resolution. | Available as prompt-only resolution. | Available as prompt-only resolution. | Available as prompt-only resolution. | Applies only if the API profile accepts prompt text. |
| **Scene Director** | Available for Generate/Img2Img/Inpaint with ComfyUI + V054 node. | Experimental for Generate/Img2Img/Inpaint with ComfyUI + V054 node. | Unsupported for active V054 graph mutation; adapter plans may be metadata only. | Unsupported for active V054 graph mutation; Qwen/modern component routes must not consume checkpoint-only Scene Director fields. | Not supported as a local Comfy graph patch. |

Rules for Assistant answers:

- Say **prompt-only** for Style Stack and Wildcards. They do not need Comfy custom nodes and do not patch graphs.
- Say **graph/workflow patch** for CFG Fix, LayerDiffuse, and direct LoRA Stack execution.
- Say **metadata/catalog manager** for LoRA Library. It helps choose/enrich LoRAs but does not apply a LoRA until the LoRA is added to LoRA Stack.
- Do not promise LoRA/CFG/LayerDiffuse execution on Grok/API routes.
- If the live snapshot says an extension is disabled, answer what it does and how to enable it, but do not describe it as active.
- For Scene Director, check Image → Generation, ComfyUI backend, checkpoint loader, SDXL/SD1.5 family, Generate/Img2Img/Inpaint mode, and `NeoSceneDirectorV054` node readiness before promising active regional execution.
