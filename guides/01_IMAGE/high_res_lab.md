---
guide_id: image.high_res_lab
title: Image High-Res Lab
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - high_res_lab
  - highres
  - high_res_fix
  - upscale_refine
  - diffusion_refine
tags:
  - image
  - finish
  - high res
  - upscale
  - diffusion refine
  - selected output
priority: 111
version: 5
updated: 2026-09-23
---

# Image High-Res Lab

**High-Res Lab** is the built-in Image → Finish tool for high-resolution finishing. It is used when the user wants a larger, cleaner, more detailed output while still using the current Image generation route/prompt/model context.

It is different from **Image Upscale**:

- **High-Res Lab** can do a highres-style diffusion refine pass.
- **Image Upscale** is a standalone utility for resizing/upscaling selected or uploaded images without normal prompt context.

## Supported route shape

High-Res Lab supports local Comfy checkpoint routes and selected compiler-owned component/GGUF routes.

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable + SDXL checkpoint + Generate/Img2Img/Inpaint/Outpaint | Available |
| ComfyUI / ComfyUI Portable + SD 1.5 checkpoint + Generate/Img2Img/Inpaint/Outpaint | Available |
| Forge / A1111 style routes | Planned/gated in this V2 UI contract |
| Flux, Qwen, ZImage, and selected component/GGUF routes | Follow the live route profile; active families keep their own sampler/model/VAE rules |
| ComfyUI / ComfyUI Portable + Krea 2 RAW/Turbo + diffusion-model or GGUF + Generate/Img2Img/Inpaint/Outpaint | Experimental |
| HiDream or other unverified component/GGUF/API routes | Do not promise unless the live route snapshot says available |
| xAI Grok / cloud API route | Not a local Comfy high-res graph |

## Krea 2 High-Res Lab support

Krea 2 RAW and Krea 2 Turbo are available experimentally through family-specific High-Res Lab profiles. The finish pass reuses the compiled Krea model, sampler, Qwen3-VL conditioning, and Qwen Image VAE anchors. It does not downgrade the route to SDXL or rebuild the graph as a checkpoint workflow.

Krea-specific safeguards:

- **CFG is route-owned and hidden** in High-Res Lab; RAW preserves its compiled CFG and Turbo preserves its fixed low-CFG behavior.
- **Ultimate SD Upscale is blocked** because it assumes SD-style conditioning semantics.
- Pixel refine, latent refine, and upscale-only remain available with conservative Krea presets.
- GGUF routes quantize only the selected transformer; Qwen3-VL stays native/safetensors.
- ComfyUI and ComfyUI Portable use the same profile rules.

## Qwen Image 2.1 High-Res target — Q21-0

Qwen Image 2.1 has native 2K-oriented generation, but native output size and Neo High-Res Lab are separate features. Q21-0 records the future High-Res target only; no Q21 High-Res route is currently executable.

Q21-5 must provide a family-specific second pass that keeps Qwen3-VL/VAE/reference semantics instead of converting the workflow into an SD-style refine graph.

Required design locks:

- try native Qwen size first when the user only needs a 2K-oriented result;
- preserve Qwen cache and compatible LoRA intent across the finish route;
- preserve the ordered reference set;
- define an explicit Stage-2 role map for edit jobs, because the Stage-1 output, original Image 1 target, and Images 2–10 are not interchangeable;
- keep Ultimate SD Upscale gated unless a dedicated Qwen-safe adapter is proven;
- test Generate, Img2Img/Edit, Inpaint, and Outpaint separately before claiming all-mode support.

## Main controls

| Control | Meaning |
|---|---|
| **Enable High-Res Lab payload** | Adds High-Res Lab settings to the next generation or staged finish pass. |
| **Profile** | Applies a preset group such as Custom, Gentle polish, Balanced finish, Detail push, Bigger finish, Latent rebuild, or Upscale only. |
| **High-res mode** | Chooses the finish strategy. `Latent upscale + rebuild` works like a highres/refine pass; `Pixel upscale + diffusion refine` upscales the image then refines it. |
| **Resize method** | Pixel resize method before/around refinement. Common values include Lanczos, Bicubic, Bilinear, Area, and Nearest-exact. |
| **Scale** | Multiplier for output size. Higher values cost more VRAM/time and can cause artifacts. |
| **Steps** | Number of refinement sampler steps. More steps can increase detail but can overwork the image. |
| **Denoise** | How much the finish pass can change the image. Low values preserve, high values rebuild. |
| **CFG** | Prompt strength for the finish pass on SD-style routes. |
| **Sampler / Scheduler** | Can reuse the main sampler/scheduler or override them for the finish pass. |
| **Upscaler model** | Optional upscale model loaded from Comfy upscale model catalog. |
| **Tiled VAE safety** | Helps reduce memory pressure during high-resolution encode/decode. |
| **Tile size / Tile overlap** | Controls tiled processing. Smaller tiles reduce VRAM use; overlap helps avoid seams. |

## Profiles

| Profile | Use it for |
|---|---|
| **Gentle polish** | Small cleanup while preserving the original output. |
| **Balanced finish** | General high-res/detail pass. |
| **Detail push** | More visible detail; watch for over-sharpening or face drift. |
| **Bigger finish** | Larger size delivery. Needs more VRAM. |
| **Latent rebuild** | Stronger highres rebuild behavior. |
| **Upscale only** | Resize/upscale without diffusion refine; better for clean delivery resizing. |

## Source behavior

High-Res Lab can run from a normal generation draft or from a staged selected output. If the user sends a saved output to High-Res Lab from Results, Neo should use that selected image as the source, not silently restart from the base prompt.

## Recommended starter settings

For SDXL checkpoint:

```text
Profile: Balanced finish
Scale: 1.5–2.0
Steps: 10–20
Denoise: 0.20–0.40
CFG: reuse main CFG or slightly lower
Tiled VAE: On for large images
```

Use lower denoise when the user wants the exact image preserved. Use higher denoise only when they want the result rebuilt or more stylized.

## Assistant rules

When the user asks about High-Res Lab:

- check whether the route is ComfyUI + SDXL/SD1.5 checkpoint;
- check whether High-Res Lab is enabled or only available;
- distinguish High-Res Lab from Image Upscale;
- recommend conservative denoise before high denoise;
- warn that cloud/API outputs need to be staged into a compatible local Comfy finish route.

## Forge Neo native post-generation Hires

When Forge Neo is the selected Image provider, the Preview/Output Inspector ✨ action uses Forge's native selected-image Hires path through **Neo Forge Bridge 1.2.1+**.

Execution contract:

```text
run_forge_native_hires
→ native_txt2img_upscale
→ firstpass_image = selected output
→ enable_hr = true
→ txt2img_upscale = true
```

This operation stays on the selected Forge profile and uses the selected image's real dimensions as the first-pass dimensions. It does not generate a new low-resolution first pass. The original seed is reused when available, while the current High-Res Lab scale, upscaler, denoise, second-pass steps, sampler/scheduler, model/module overrides, prompts, LoRAs, embeddings, and compatible always-on scripts are compiled into the native request.

The action is disabled when the Bridge is missing, unselected, outdated, or does not advertise `native_post_hires`. Neo does not fall back to Comfy. PiD Integrated and High-Res Lab remain mutually exclusive.

## Forge selected-output size enforcement — Hotfix 07

Forge Preview/Output Inspector High-Res Fix now uses **Neo Forge Bridge 1.2.1+** and the size contract `neo.forge_bridge.native_hires_size.v2`.

For scale mode, the selected image remains the first-pass source and Neo computes the final target from the decoded source image:

```text
source: 896×1344
scale: 1.5×
target: 1344×2016
```

The one-shot Preview action temporarily enables the current High-Res Lab settings without permanently changing the user's extension toggle. Scale mode clears stale explicit target fields before compilation. The Bridge then resolves and forces the exact target dimensions into Forge, and rejects a returned primary image whose dimensions do not match the expected target.

Required selected Bridge capabilities:

```text
native_post_hires: true
native_operations includes native_txt2img_upscale
native_post_hires_size_contract: true
```

An older or incomplete Bridge fails closed. Replace the bundled Bridge files, restart Forge Neo, and refresh the selected Forge profile in Admin.

## Provider dispatch and execution truth — IMG-HR2

High-Res Lab support for provider-owned Comfy workflows is decided by the canonical High-Res route matrix. The provider dispatcher must not maintain a second family-only allowlist.

For an active non-checkpoint route such as `qwen_rapid_aio + gguf + img2img`, an enabled High-Res Lab payload is passed into the shared workflow extension patcher. The expected inline refine chain is:

```text
base sampler
→ base decode
→ image/latent upscale
→ VAE encode when required
→ High-Res refine sampler
→ final VAE decode
→ PreviewImage / SaveImage
```

The frontend submit snapshot records user intent as `workflow_requested`; it does not claim provider execution before compilation. After the provider graph is patched, Neo writes `_neo_high_res_execution_proof` into `actual_params` and mirrors the proof at `backend_payload.high_res_execution_proof`.

The proof includes the active route/profile, base sampler, added upscale/encode/refine/decode nodes, source dimensions, target dimensions, and final patched output reference.

If High-Res Lab is explicitly enabled on an active route but no applied High-Res workflow patch exists after compilation, Neo fails closed before `/prompt` instead of silently queueing the base-resolution graph.

## Comfy polling recovery for repeated High-Res runs — 2026-08-22

Krea 2 High-Res jobs are long-running Img2Img jobs. A temporary HTTP history-poll disconnect must not be interpreted as a Comfy execution failure when the prompt is already queued.

Current recovery contract:

```text
queued High-Res prompt
→ websocket preview may continue independently
→ /history poll temporarily times out/resets
→ Neo keeps the job in running state
→ retry /history
→ explicit Comfy history success/failure decides the terminal state
→ import the completed output into Neo_Data
```

The selected-profile **Connect/Test gate applies to starting a new task**. Once a job is queued, Preview, Poll, Recover, and Cancel bind to the durable job record and the exact backend URL that accepted that prompt. A stale UI/runtime connection badge must not orphan an already-running High-Res job.

This is especially important for repeated Krea 2 High-Res passes where Comfy can continue sampling after a preview/progress transport closes. Neo should keep polling instead of converting a transient transport interruption into `failed`.

## Qwen Image Edit 2511 High-Res Lab parity + repeated-run cache diagnostics — 2026-08-24

Qwen Image Edit 2511 is now explicitly onboarded into High-Res Lab for both **Safetensors / Components** (`diffusion_model`) and **GGUF** routes. The extension no longer falls through to a generic `Not ready` implementation target when the base 2511 route is available.

2511 follows the existing Qwen-native High-Res safety rules:

- family-specific model / sampler / VAE anchors are reused instead of rebuilding the route as SDXL;
- Qwen-style low-CFG refinement policy is applied;
- pixel refine, standard diffusion refine, upscale-only, and Qwen re-edit remain eligible where the live route profile allows them;
- **Ultimate SD Upscale remains blocked** for Qwen edit routes because it assumes SD-style conditioning semantics;
- Native and GGUF have separate 2511 route profiles rather than borrowing a 2509 profile at runtime.

Repeated Qwen edit generations also publish a cache-diagnostics contract. Neo keeps normal model residency policy unchanged: ordinary Image generation does **not** issue Comfy `/free` or `unload_models`. Instead, Neo now preserves content-addressed Comfy source handoff names across normal repeats and surfaces Comfy's `execution_cached` evidence when available.

For an unchanged source / prompt / model / conditioning route with only seed changed, the expected diagnostic is:

```text
source handoff: neo_img2img_cache_<content hash>
cache contract: same as previous
execution_cached: Qwen conditioning node present
UI: Qwen conditioning cache hit — reusing encoder output
```

The upload handoff uses `overwrite=true` for the deterministic content-addressed filename. This prevents a duplicate upload collision from silently changing the Comfy `LoadImage` name and invalidating downstream Qwen conditioning cache identity.

A 2511 diffusion model that is larger than available VRAM may still be dynamically staged by Comfy. That is expected and is distinct from Neo explicitly unloading the model. If the cache contract is unchanged but Comfy does not emit an `execution_cached` hit for the Qwen conditioning node, investigate Comfy's cache mode / eviction or VRAM pressure rather than reintroducing a Neo `/free` workaround.

## Qwen Image 2.1 High-Res Lab — Q21-5

Q21-5 adds experimental High-Res profiles for `qwen_image_21`.

**Q21-6B cache interaction:** when `QwenImage21Cache` is enabled, High-Res Stage 2 reuses the Stage-1 sampler model path (including cache and any DifferentialDiffusion wrapper) and does not create a second cache node.

### Components/Safetensors

Txt2Img, Img2Img/Edit, Inpaint, and Outpaint can use pixel refine, standard/latent refine, or upscale-only. Target size snaps to 32-pixel multiples. Q21 blocks Ultimate SD Upscale and legacy Qwen re-edit because those paths do not own the 1–10-reference Q21 conditioning contract.

High-Res preserves the base Q21 KSampler CFG (True-CFG) and reuses the existing positive/negative conditioning refs instead of rebuilding `TextEncodeQwenImage21`.

### Masked routes

For Q21 Inpaint/Outpaint, Stage 2:

1. obtains the Stage-1 `SetLatentNoiseMask` mask;
2. resizes it with core `MaskToImage → ImageScale → ImageToMask`;
3. reapplies it with `SetLatentNoiseMask` to the refinement latent;
4. preserves the Stage-1 model chain, including model-only LoRA and `DifferentialDiffusion`;
5. when Stage 1 used strict pixel preservation, composites the Stage-2 masked result over the scaled Stage-1 preserved image.

### GGUF

The existing Q21-3 GGUF txt2img/img2img/edit routes receive an experimental High-Res profile, but remain physically unqualified. GGUF Inpaint/Outpaint stays unavailable because the base Q21 route itself remains unsupported.
