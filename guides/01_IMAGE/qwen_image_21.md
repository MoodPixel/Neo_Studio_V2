---
guide_id: image.qwen_image_21
title: Qwen Image 2.1
surface: image
scope: built_in
applies_to:
  - image_workspace
  - qwen_image_21
  - txt2img
  - img2img
  - edit
  - inpaint
  - outpaint
  - safetensors
  - gguf
  - multi_reference
  - rgba
  - high_res_lab
  - lora_stack
tags:
  - image
  - qwen
  - qwen image 2.1
  - multi reference
  - rgba
  - gguf
  - lora
  - high res
priority: 90
version: 1
updated: 2026-09-24
---

# Qwen Image 2.1

## Current Neo status

**Q21-6B is implemented at compiler/contract level. Safetensors/components support Txt2Img, unified Img2Img/Edit, Inpaint, experimental Outpaint, model-only LoRA, High-Res Lab, explicit Auto/RGB/RGBA output-channel policy, and QwenImage21Cache controls with capability-aware enabled/disabled discovery states. GGUF supports Txt2Img and unified Img2Img/Edit with the same channel/cache controls. Q21-6A Txt2Img RGBA has a physical transparency pass; Q21-7 still owns broader edit/cache performance qualification. Outpaint remains implemented but visually unqualified and is intentionally parked for later repair.**

The locked family identity is:

```text
Display name: Qwen Image 2.1
family_id: qwen_image_21
public Main Model Types now: Safetensors, GGUF
GGUF contract: transformer only; native/safetensors Qwen3-VL 8B + Qwen Image 2.1 VAE
implementation state: Q21-6A Experimental family with explicit output-channel control
```

Neo may expose the implemented Q21 routes when live Comfy readiness passes. Keep the family labeled Experimental until Q21-7 physical qualification. Q21-6A Txt2Img RGBA has now passed a real saved-PNG alpha test; edit/reference alpha remains part of Q21-7. Q21-6B cache controls are implemented but performance/memory behavior still requires physical qualification.

## Why it is a separate family

Qwen Image 2.1 is a unified generation/edit model rather than another alias of Neo's older Qwen Image Edit families. Upstream currently documents:

- one model for text-to-image and image editing;
- a 7B visual generation transformer with 32 single-stream DiT layers;
- Qwen3-VL 8B as the vision-language text/reference encoder;
- native RGBA/transparency support;
- up to **10 reference images**;
- prefix KV-cache reuse for efficient editing;
- native high-resolution/2K-oriented generation examples.

Neo therefore keeps `qwen_image_21` independent from `qwen_image`, `qwen_image_edit_2509`, `qwen_image_edit_2511`, and `qwen_rapid_aio`.

## Source authority reviewed for Q21-0

Checked on 2026-09-23:

- Qwen official model/repo: `https://github.com/QwenLM/Qwen-Image-2.1`
- Qwen official model card: `https://huggingface.co/Qwen/Qwen-Image-2.1`
- Qwen official blog: `https://qwen.ai/blog?id=qwen-image-2.1`
- ComfyUI Qwen core node source: `Comfy-Org/ComfyUI/comfy_extras/nodes_qwen.py`
- Comfy-Org Qwen Image 2.1 text-to-image workflow template
- Comfy-Org Qwen Image 2.1 image-edit workflow template
- user-provided CivitAI version `2953213 / 3344958`, which is a repost of the official Qwen Image 2.1 checkpoint according to the available model-page mirror.

The CivitAI repost is not used as architecture authority when it conflicts with Qwen/Comfy upstream.

### Q21-3 GGUF runtime sources reviewed on 2026-09-24

- `city96/ComfyUI-GGUF` current `UnetLoaderGGUF`, whose runtime input is the GGUF diffusion-model name and whose output is `MODEL`;
- current Comfy core `TextEncodeQwenImage21`;
- current Qwen Image 2.1 community Comfy workflows using `UnetLoaderGGUF` together with native `CLIPLoader(type=qwen_image)` and native Qwen Image 2.1 VAE.

These sources support Neo's transformer-only GGUF boundary; they do not justify claiming a GGUF Qwen3-VL/MMProj route.

## Q21-1 implemented scope

Implemented now:

```text
family = qwen_image_21
loader = diffusion_model
mode = txt2img
route state = experimental_available
compiler = comfy.qwen_image_21
```

The compiler requires live capability evidence for `UNETLoader`, `CLIPLoader(type=qwen_image)`, `VAELoader`, `TextEncodeQwenImage21`, `EmptyLatentImage`, `KSampler`, and `VAEDecode`. It fails closed when the Qwen 2.1 conditioning role is absent instead of falling back to an older Qwen encoder.

Current graph:

```text
UNETLoader
CLIPLoader(type=qwen_image)
VAELoader
        │
        └── TextEncodeQwenImage21(prompt, negative_prompt, vae, resolution=1024)
                     ├── positive
                     └── negative
EmptyLatentImage(width, height, batch)
        │
KSampler(model, positive, negative, latent)
        │
VAEDecode
        │
SaveImage + PreviewImage
```

The official Comfy Qwen Image 2.1 T2I template uses `EmptyLatentImage` for the requested width/height, so Q21-1 follows that ownership. The latent output returned by `TextEncodeQwenImage21` is not used as the T2I canvas in this phase.

Starting defaults are `1024×1024`, `25` steps, True CFG `1`, Euler, Simple, denoise `1`. Explicit user values remain final truth.

## Q21-2 implemented unified edit scope

Implemented now:

```text
family = qwen_image_21
loader = diffusion_model
mode = img2img | edit
route state = experimental_available
compiler = comfy.qwen_image_21
reference count = 1–10
```

Q21-2 uses the same Qwen Image 2.1 diffusion model, Qwen3-VL 8B encoder, VAE, and `TextEncodeQwenImage21` conditioning node as Q21-1. The difference is reference/canvas ownership:

```text
LoadImage(Image 1 target) ─┐
LoadImage(Image 2 ref) ────┤
...                         ├─→ TextEncodeQwenImage21(images.image_1 ... images.image_10)
LoadImage(Image 10 ref) ───┘         ├─ positive
                                     ├─ negative
                                     └─ latent from Image 1
                                              │
                                           KSampler
                                              │
                                           VAEDecode
```

Rules:

- Image 1 is permanently the **Primary / Edit Target**.
- Images 2–10 are ordered references.
- Active reference lanes must be contiguous from Image 1; Neo rejects gaps instead of silently renumbering them.
- `<image1>` … `<image10>` map to the exact current numeric lane order and that order is stored in actual params/replay metadata.
- Neo's reorder controls swap only occupied reference lanes and rewrite matching prompt tokens in both positive and negative prompts.
- the default edit reference resolution is `0`, matching the current official Comfy edit template: each source stays near its own size, rounded to multiples of 32.
- the default canvas is the latent returned by `TextEncodeQwenImage21`, so Image 1 owns the edit output shape after reference preprocessing.
- **Custom width / height** is an explicit alternate canvas mode and inserts `EmptyLatentImage`; it does not alter reference order.
- edit sampling is full Qwen unified edit (`denoise = 1`); Neo does not pretend this route is classic SD denoise-strength img2img.

The Img2Img/Edit Parameters panel exposes a dedicated **Qwen 2.1 Edit Canvas** card:

- **Edit Canvas → Follow Image 1** uses the latent returned by `TextEncodeQwenImage21`; Neo's Width/Height values remain saved but do not control the current output canvas.
- **Edit Canvas → Custom width / height** switches the sampler latent to `EmptyLatentImage`, and the normal Neo **Width** / **Height** controls become the authoritative output size. Because Qwen reference conditioning still comes from Image 1, large aspect-ratio changes can shift composition; use Source canvas when edit alignment matters more than reframing.
- **Reference Resolution** maps directly to `TextEncodeQwenImage21.resolution`, accepts `0–4096` in 32-pixel steps, and is independent from final output Width/Height. `0` keeps each reference near its own size using Comfy's native Qwen 2.1 preprocessing.

The dedicated Source Images panel owns lane count. There is no duplicate generic “number of references” field.

## Safetensors/component target topology

The Q21-1 native route is designed around the current Comfy core contract:

```text
UNETLoader
  Qwen Image 2.1 diffusion model
        │
        ├─ optional global Qwen-2.1-compatible model LoRA stack
        │
        └─ optional QwenImage21Cache
                 │
CLIPLoader(type=qwen_image)
  Qwen3-VL 8B
                 │
VAELoader
  Qwen Image 2.1 RGBA VAE
                 │
TextEncodeQwenImage21
  positive + negative (+ edit latent for reference routes)
                 │
KSampler
                 │
VAEDecode
                 │
Neo output persistence
```

Neo must capability-discover these node/socket contracts from the selected Comfy backend rather than hardcoding example filenames.

Official Comfy packaging currently demonstrates filenames such as:

```text
qwen_image_2.1_bf16.safetensors
qwen_image_2.1_int8_convrot.safetensors
qwen3vl_8b_bf16.safetensors
qwen3vl_8b_int8_convrot.safetensors
qwen_image_2.1_vae_bf16.safetensors
```

Those names are examples, not Neo allowlists.

## `TextEncodeQwenImage21` contract

Current Comfy core exposes:

```text
inputs:
  clip
  prompt
  negative_prompt
  vae?             optional at node-schema level
  resolution       default 1024, 0 keeps reference size
  images.image_N   autogrow reference lanes

outputs:
  positive
  negative
  latent
```

Comfy currently exposes autogrow names beyond the upstream model's documented range. **Neo's product contract is capped at 10 references** because Qwen officially documents up to 10.

For Neo edit routes:

- Image 1 is the primary edit target.
- Images 2–10 are ordered references.
- Prompt references use `<image1>` … `<image10>` when helpful.
- reference order is replay truth and must never be silently sorted by filename or upload time.
- the Qwen 2.1 VAE is required by Neo's edit contract so reference latents are available.

For text-to-image, no reference is required.

## Resolution policy

Qwen's official examples include 2048×2048 and larger dimensions for non-square aspect ratios. Comfy's official edit template describes a 2K-oriented pixel budget and starts its local template at 1024 for practical runtime cost.

Neo's future UI therefore distinguishes:

```text
Normal / 1K-oriented generation
Native 2K-oriented generation
Neo High-Res Lab refinement
```

Do not treat “2K” as a hard `width <= 2048 && height <= 2048` rule. Official examples include wide/tall dimensions such as 2752×1536 and 1536×2752.

Explicit Width/Height/Steps/CFG/Sampler/Scheduler remain governed by Parameter Truth once the route is implemented.

## Sampling starting profile

The official Diffusers example uses 40 steps. The current official Comfy local workflow starts at 25 steps with Euler + Simple and CFG 1.

Neo Q21-1 may use the Comfy-oriented local starting profile:

```text
Steps: 25
CFG / True CFG: 1
Sampler: euler
Scheduler: simple
Initial size: 1024-oriented
```

These are defaults only. Neo must not overwrite explicit user values.

With CFG 1, the official Comfy template states that negative prompt conditioning is effectively unused. Negative prompt becomes meaningful when guidance is raised.

## Reference UX contract

Q21-2 uses progressive lanes instead of rendering ten permanent cards:

```text
References · 2 / 10

Image 1 · Primary / Edit Target
Image 2 · Reference
+ Add Reference
```

Rules:

- Generate/txt2img uses no reference lane.
- Img2Img/Edit starts with Image 1.
- Add Reference grows progressively to Image 10.
- Image 1 cannot be reordered.
- occupied Images 2–10 may be reordered; Neo swaps their `<imageN>` prompt tokens at the same time.
- removal is end-first so ordinary removal cannot silently collapse numeric token meaning.
- a manual clear that leaves a numbering gap is blocked at compile time until the gap is filled or later lanes are removed.
- replay restores the exact lane order and role map.
- Inpaint/Outpaint remains a Q21-4 target, not a Q21-2 route.

## Qwen Image 2.1 cache

`QwenImage21Cache` is an inference optimization, not a LoRA-training compatibility flag.

Current Comfy options are:

```text
Cache device:
  auto
  gpu
  cpu
  off

Cache dtype:
  default
  int8
  int4
```

Neo defaults should be:

```text
device = auto
dtype = default
```

This must remain separate in code/UI terminology from Krea Ostris `kv_cache`, whose meaning is tied to how the edit asset was trained/exported.

## Q21-3 implemented GGUF boundary

Q21-3 implements **GGUF diffusion-transformer support** for the same Qwen Image 2.1 txt2img and unified 1–10-reference edit compiler used by the Safetensors/components route. This is a mixed stack, not an all-GGUF route.

Implemented contract:

```text
Qwen Image 2.1 GGUF transformer    -> UnetLoaderGGUF or LoaderGGUF (live-discovered)
Qwen3-VL 8B encoder               -> native/safetensors CLIPLoader(type=qwen_image)
Qwen Image 2.1 VAE                -> native/safetensors VAELoader
TextEncodeQwenImage21              -> Comfy core
```

The GGUF route supports `txt2img`, `img2img`, and `edit`. Image 1–10 ordering, reference resolution, source-owned canvas, and custom Width/Height canvas behave the same way as Q21-2. The only model-loading change is the diffusion-transformer loader.

Neo requires live evidence for a compatible GGUF model loader and for the native Qwen 2.1 conditioning stack. If neither `UnetLoaderGGUF` nor a compatible `LoaderGGUF` is available, the route fails closed before queueing.

Q21-3 deliberately does **not** use `CLIPLoaderGGUF`, a GGUF Qwen3-VL encoder, or MMProj. A future GGUF vision/text-encoder route is a separate qualification problem and must not be inferred from transformer GGUF support.

Q21-3 is repository-qualified but remains Experimental until physical runs prove the selected GGUF asset/runtime combination.

## LoRA target contract

Qwen Image 2.1 LoRA inference is a future experimental Neo route.

Planned ordering:

```text
base Qwen 2.1 model
→ global compatible LoRA Stack rows
→ QwenImage21Cache (if enabled)
→ sampler
```

Q21 implementation must:

- use the live provider LoRA catalog and exact catalog rebinding;
- prefer model-only LoRA patching unless physical/runtime evidence proves a text-encoder patch is required;
- preserve LoRA pass targeting without applying the same LoRA twice to an already patched model;
- treat Qwen Image 2.1 LoRAs as a separate compatibility class from older Qwen Image/Edit LoRAs;
- fail closed when the asset's target architecture cannot be established.

## Inpaint / Outpaint design target

Qwen upstream documents local editing and separate-mask workflows, but current Comfy `TextEncodeQwenImage21` does not expose a direct mask socket. Therefore Q21-0 **does not approve reuse of Neo's generic SD/Flux Native mask compiler**.

Q21-4 must implement and physically validate a Qwen-specific masked-edit adapter. Product target:

### Inpaint

```text
Image 1 source
+ user mask
+ Qwen edit instruction / local-edit preparation
→ Qwen 2.1 edit generation
→ optional strict final masked commit
```

User-facing preservation policy target:

```text
Strict Preserve Outside Mask   (Neo default candidate)
Native / Allow Spill           (advanced)
```

### Outpaint

```text
Image 1 source
→ Neo canvas expansion
→ Qwen 2.1 edit generation on expanded target
→ optional re-composite of untouched original area
```

User-facing preservation policy target:

```text
Preserve Original Area         (Neo default candidate)
Allow Source Redraw            (advanced)
```

Exact mask-guidance/source-preparation topology remains a Q21-4 research item and must not be guessed in Q21-1/2/3.

## High-Res Lab target

High-Res support is Neo-owned, not an upstream Qwen feature toggle.

Q21-5 target is family-specific support for Generate, Img2Img/Edit, Inpaint, and Outpaint. It must:

- keep Qwen model/encoder/VAE semantics;
- keep ordered references and their role metadata;
- use Qwen-compatible second-pass conditioning;
- block Ultimate SD Upscale unless a dedicated Qwen-safe adapter is proven;
- define edit-mode Stage-2 reference mapping explicitly instead of blindly treating the original Image 1 and the Stage-1 output as the same role.

Because Qwen Image 2.1 already generates at 2K-oriented sizes, users should generally try native size first and use High-Res Lab when they specifically want a second refinement/upscale pass.

## Q21-6A — RGBA / Alpha Control

Q21-6A implements transparency as first-class output intent rather than as background-removal postprocessing. The visible control is:

```text
Output Channels:
  Auto · Preserve native channels
  RGB · Strip alpha
  RGBA / Transparent · Preserve alpha
```

Runtime behavior:

- **Auto** leaves the native Qwen VAE decode channels untouched. No transparency prompt suffix is added.
- **RGB** inserts `SplitImageWithAlpha` before the final provider output and sends only output 0/RGB to `SaveImage`.
- **RGBA / Transparent** preserves the native Qwen decode. For edit/reference routes, Neo reconstructs each uploaded RGBA source from `LoadImage` output 0 (RGB) plus output 1 (Comfy transparency mask) through `JoinImageWithAlpha` before `TextEncodeQwenImage21`, allowing Qwen's VAE reference encoding to receive four-channel input. An append-only transparency prompt assist is recorded separately and never replaces the user's prompt.
- `SaveImage` remains PNG, and Neo portable metadata is inserted into the PNG byte stream without re-encoding pixels. Regression coverage verifies that metadata insertion preserves alpha bytes/pixels.
- JPEG is not an RGBA-safe result format. WebP is not promoted as an RGBA guarantee until the complete provider → Neo path is physically proven.

High-Res Lab also owns an explicit channel bridge. Qwen decode can be four-channel, while DAT/ESRGAN/Spandrel model upscalers are RGB-only. Q21-6A therefore splits RGB before `ImageUpscaleWithModel`; Auto/RGBA resizes and rejoins the alpha mask before the Qwen Stage-2 VAE encode, while RGB intentionally discards alpha and strips any Stage-2 alpha before the final output. Preview-action `LoadImage` can recover its transparency from MASK output 1.

Compiler/byte-level validation is complete. **Txt2Img RGBA has a physical saved-PNG alpha pass** with genuine transparent and semi-transparent pixels. Edit/reference alpha preservation still belongs to Q21-7, so Neo should not generalize the Txt2Img result into a universal transparency-quality claim.

## Q21-6B — QwenImage21Cache Controls

Q21-6B exposes the current Comfy `QwenImage21Cache` inference optimization. Q21-6B.1 keeps the cache card visible for Qwen Image 2.1 even before the node is confirmed: when live backend discovery proves the `model`, `device`, and `dtype` sockets, the selectors are enabled; otherwise they remain visible but disabled with an **Available / Unavailable / Not checked** discovery status and guidance to run provider Connect/Test. The controls are:

```text
Cache Device:
  Auto · VRAM then RAM
  GPU · VRAM
  CPU · RAM
  Off · Recompute prefix

Cache Precision:
  Default · Lossless
  INT8 · Lower memory
  INT4 · Lowest memory
```

Runtime ordering is deliberately model-path safe:

```text
base Qwen Image 2.1 model
→ compatible global model-only LoRA stack
→ QwenImage21Cache
→ DifferentialDiffusion when the masked route needs it
→ KSampler
```

The compiler emits the cache node against the base model. Neo's LoRA extension then rewires that cache node's model input to the patched model, which keeps the final graph in the order above. High-Res Lab reuses the sampler's already cached model path and must not add a duplicate cache node.

Device/precision semantics are inference-only:

- **Auto** lets the Qwen runtime use spare VRAM and then RAM.
- **GPU** keeps cache storage in VRAM.
- **CPU** keeps cache storage in system RAM.
- **Off** explicitly disables the prefix cache/recomputes it, useful for diagnostics but normally slower.
- **Default / INT8 / INT4** change cache storage precision only; they do not quantize or replace the selected Qwen model weights.

Safetensors/components and GGUF transformer routes share this cache contract because the node consumes/returns a Comfy `MODEL`. GGUF masked routes remain gated for the existing Q21 reasons. Cache settings are recorded in `_neo_qwen21_cache_execution` for replay/inspection. The UI no longer silently hides the feature when discovery is stale or missing; it shows disabled controls and the discovery state. Explicit requests still fail closed if the connected backend no longer exposes the compatible cache node.

This cache is **not** Krea/Ostris `kv_cache`: Krea's setting describes edit-training/runtime compatibility, while `QwenImage21Cache` is a Qwen Image 2.1 inference optimization.

Q21-6B is repository-qualified only. Q21-7 must physically measure GPU/CPU/Auto/Off behavior, memory use, speed, and INT8/INT4 output drift before Neo makes performance recommendations.

## License

The official Qwen Image 2.1 weights are under the **Qwen Research License Agreement** dated 2026-09-20. The current license grants use of the materials for **non-commercial purposes only** and states that commercial use requires a separate commercial license from Qwen.

Neo should surface this as model-license information. Neo Studio's own license does not replace the model-weight license.

## Planned implementation sequence

```text
Q21-0  Architecture & records
Q21-1  Family + Safetensors Txt2Img
Q21-2  Unified Edit + dynamic 1–10 references
Q21-3  GGUF diffusion-transformer route
Q21-4  Qwen-specific Inpaint / Outpaint
Q21-5  LoRA Stack + High-Res Lab
Q21-6A RGBA / Alpha Control
Q21-6B QwenImage21Cache controls                  ← current
Q21-7  Physical qualification and support promotion
```

Q21-0 through Q21-6B are implemented at compiler/contract level. Q21-6A Txt2Img RGBA has a physical alpha pass; Q21-6B cache behavior and broader route qualification remain under Q21-7. Outpaint remains a parked visual-qualification exception despite having a compiled runtime path.

## Q21-4 Safetensors Inpaint + Outpaint

Q21-4 promotes masked workflows for **Safetensors / Components** only. GGUF inpaint/outpaint remains gated pending a real GGUF model and physical qualification.

### Inpaint topology

```text
Image 1 + optional Images 2–10
        │
        ├─→ TextEncodeQwenImage21
        │
Mask ─→ LoadImageMask → optional GrowMask
        │
Image 1 → VAEEncode → SetLatentNoiseMask
MODEL → DifferentialDiffusion
        │
      KSampler → VAEDecode
        │
        ├─ Native: return Qwen output
        └─ Strict: ImageCompositeMasked restores original pixels outside mask
```

Policies:

- **Strict · Preserve outside mask** is the Neo default. Sampling is mask-controlled and the final image is additionally composited so pixels outside the final mask come from Image 1.
- **Native · Allow edit spill** keeps the same latent mask but skips the final pixel guard, allowing Qwen's decoded result to change outside the requested region.
- Image 1 remains the primary target. Images 2–10 remain ordered semantic references and keep the `<imageN>` token contract from Q21-2.
- `mask_grow` uses Comfy core `GrowMask`; Q21-4 intentionally does not require KJNodes. `mask_blur` is persisted for replay but is not injected by the Q21-4 compiler.

### Outpaint topology

Current runtime topology after Q21-4C:

```text
Image 1 → ImagePadForOutpaint ───────────────→ TextEncodeQwenImage21 as image_1
                   └─ outpaint mask ───────────────┐          └─ output[2] native first-reference latent
                                                   │                         │
                                                   │                         ↓
                                                   └──────────────────────→ KSampler → VAEDecode
                                                                               │
                                                                               ├─ Allow Source Redraw: return native Qwen full-canvas output
                                                                               └─ Preserve Original: invert outpaint mask and restore the padded source center
```

The original Q21-4 latent-mask outpaint prototype was retired after physical testing showed weak/blank-looking expansion behavior. Only Inpaint retains `VAEEncode → SetLatentNoiseMask → DifferentialDiffusion`.

Policies:

- **Preserve original area** is the Neo default and restores the original center after generation.
- **Allow source redraw** returns the native Qwen full-canvas result.
- Neo's normal Outpaint Padding contract owns left/right/top/bottom padding and feathering.
- The padded Image 1 is what Qwen sees as `<image1>`; Images 2–10 remain ordered references.

Q21-4 does not claim visual qualification. Unit/compiler tests prove graph and state contracts only; Q21-7 remains the physical qualification phase.

### Q21-4A Outpaint runtime repair

Runtime follow-up `Q21-4A` retired the initial outpaint latent-mask prototype (`VAEEncode -> SetLatentNoiseMask -> DifferentialDiffusion`). Q21-4A temporarily used an external `EmptyLatentImage`; Q21-4C supersedes that temporary latent source with `TextEncodeQwenImage21.output[2]`, so the sampling latent is owned by the same first-reference geometry that Qwen uses for edit conditioning. `Preserve original area` remains a final `ImageCompositeMasked` restore; `Allow source redraw` returns the native full-canvas Qwen result. Inpaint is unchanged and still uses the latent-mask + DifferentialDiffusion path.


### Q21-4B Outpaint visual qualification assist

Q21-4B keeps the Q21-4A native expanded-canvas graph and adds two qualification aids:

- **Prompt-safe outpaint assist:** Neo preserves the user's positive prompt verbatim and appends a short, inspectable outpaint-only instruction telling Qwen that the padded outer border is generation space rather than image content to preserve. Preserve mode explicitly asks to keep the original Image 1 region unchanged; redraw mode permits source redraw only when needed for a seamless continuation. The exact suffix and final effective prompt are recorded in `qwen21_outpaint_prompt_assist` and `qwen21_outpaint_effective_prompt`.
- **Three-stage diagnostic proof:** the graph exposes temporary `PreviewImage` proof nodes for the padded conditioning canvas, native generated pre-composite image, and final user-visible result. These diagnostic previews are excluded from Neo's final output import contract; `SaveImage` remains authoritative.

High-Res Lab now treats `SaveImage` as the authoritative Stage-1 final output when diagnostic previews are present, so Q21-4B proof nodes cannot be mistaken for the final image. Preserve-mode High-Res keeps center authority with the final Stage-2 composite and does not reintroduce the retired Q21-4 outpaint latent-mask path.

### Q21-4C Native latent alignment

Q21-4C aligns Outpaint with the current `TextEncodeQwenImage21` edit contract. The padded canvas remains Image 1, but `KSampler.latent_image` now consumes **`TextEncodeQwenImage21.output[2]`** instead of a generic `EmptyLatentImage`. The native node derives this latent from the first reference image geometry, which prevents Neo from sampling on an independently sized latent that can shift or weaken the edit.

For Outpaint only, the effective Qwen reference resolution is forced to **`0`** so the padded Image 1 stays near its own dimensions (rounded by Qwen to its required multiple) rather than being rescaled to a square-pixel budget before the native latent is created. Neo records both `qwen21_reference_resolution_requested` and the effective `qwen21_reference_resolution`, plus `_neo_qwen21_outpaint_execution.latent_alignment_policy=first_reference_native_latent`.

Q21-4B prompt assistance and padded/native/final diagnostic previews remain active. No new custom node is required for this repair.

## Q21-5 — LoRA Stack + High-Res Lab

Q21-5 enables these features experimentally while preserving the existing Q21 base compiler contracts.

### LoRA Stack

Qwen Image 2.1 uses a **model-only** LoRA path:

```text
Qwen Image 2.1 model
→ LoraLoaderModelOnly (LoRA 1)
→ LoraLoaderModelOnly (LoRA 2...)
→ existing Q21 model consumer
```

Qwen3-VL is not patched. Use LoRAs trained specifically for Qwen Image 2.1; Neo does not infer compatibility from Qwen 1.x, Qwen Image Edit 2509/2511, SDXL, Flux, or another architecture.

The normal LoRA Library remains authoritative for exact provider catalog names, folder filters, CivitAI source, notes, strengths, and replay identity.

### High-Res Lab

Q21 High-Res keeps the original Q21 conditioning rather than rebuilding references:

```text
Stage 1 Q21 conditioning (Image 1–10)
→ Stage 1 sample/decode
→ upscale
→ Stage 2 sample using the same positive/negative Q21 conditioning refs
→ decode
```

Rules:

- target sizes snap to 32-pixel multiples;
- Stage 2 preserves the base Q21 True-CFG / KSampler CFG;
- `Ultimate SD Upscale` and legacy `qwen_reedit` are blocked;
- native 2K should be tried before High-Res when a second refinement pass is not required.

For **Inpaint**, High-Res preserves Q21-4 latent-mask authority by resizing the Stage-1 mask and reapplying it to Stage 2, then using a final composite for Strict mode. For **Q21-4C Outpaint**, High-Res does **not** reintroduce the retired outpaint latent-mask path; Preserve mode resizes the outpaint mask only for the final Stage-2 preservation composite, while Redraw mode remains full-canvas.

Q21-5 is compiler-tested but not physically qualified. Q21-7 still owns promotion from experimental status.
