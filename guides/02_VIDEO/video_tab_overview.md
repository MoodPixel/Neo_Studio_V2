---
guide_id: video.overview
title: Video Tab Overview
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video
tags:
  - video
  - generation
  - timing
  - source assets
  - backend profiles
priority: 70
version: 12
updated: 2026-08-31
---

# Video Tab Overview

The Video tab is one provider-aware workspace for video generation, source frames, progress, previews, output metadata, and result history.

## Phase 4.7.2 runtime diagnostics

For local ComfyUI-backed Video generation, Neo separates **queue ownership** from **model residency ownership**. The core generation compilers submit through `/prompt` and do not perform a normal-generation `/free`/`unload_models` cleanup. The Video Backend Probe reports whether Neo requested explicit CPU offload, VAE offload, or block swap and includes current VRAM information when the backend exposes it. If repeated runs still reload the same model while those controls are off, inspect the active ComfyUI loader/custom-node log rather than applying the Image LayerDiffuse cache-reset policy.

MiniMax H3 additionally supports mixed-format model components: the main H3 diffusion-model loader and the Qwen3-VL text-encoder loader are resolved independently. Live discovery can therefore expose GGUF H3 text encoders while the main model is Safetensors/INT, and vice versa, as long as the connected backend exposes the corresponding loader.

## Workspace navigation

**Workspace is a label, not a selectable Video subtab.** The selectable workspace applications are:

- Generation — default workspace for route setup, backend readiness, prompt/source controls, parameters, extensions, compile/generate actions, and output-path safety.
- Assets — reusable source videos, source images, frames, Video LoRA Stack state, and related inputs.
- Reference — route-aware keyframe, multimodal H3 Ref2VA, motion, depth, prompt, and image-reference controls.
- Finish — interpolation, upscale, repair/cleanup, and export-oriented tools.
- Results — output history, preview, metadata, replay, and reuse.

Old saved UI state that used `workspace` as the Video workspace application is normalized to `generation`. Generation type remains independent, so changing to Finish or Results never changes Txt2Vid/Img2Vid/etc.

## Canonical local family routing

For local ComfyUI-backed Video profiles, route truth is backend-owned. The browser loads `GET /api/video/route-matrix` and derives the visible selectors from that payload instead of maintaining its own family, loader, generation-type, or route table.

The selection chain is:

```txt
Family → Loader → Generation Type → Route → Parameter Profile
```

After a valid route is selected, the browser loads `GET /api/video/parameter-profile` for that exact family + loader + generation type + VRAM profile. Route-specific parameter defaults are owned by `neo_app.video.route_matrix`; `parameter_profiles.py` applies VRAM constraints and exposes the UI/API parameter contract.

Route status has product meaning:

- `enabled` — selectable and runnable.
- `experimental` — selectable and runnable, but clearly identified as experimental.
- `planned` — visible where useful for roadmap context, but disabled and not runnable.
- future family/loader registry entries — known to Neo but not selectable until an actual route exists.

WAN 2.2 currently exposes UNET, GGUF, Rapid AIO GGUF, and planned Native Workflow lanes according to the live route catalog. Rapid AIO Txt2Vid/Img2Vid are experimental routes. LTX 2.3 exposes its enabled UNET/GGUF generation lanes and planned Native Workflow entries from the same catalog.

MiniMax H3 exposes native UNET routes for Txt2Vid, one-keyframe Img2Vid with explicit first/last role, First/Last Frame, Omni Reference / Ref2VA, and Ref2VA-backed Video-to-Video. Equivalent H3 GGUF routes are experimental. H3 generation includes native stereo audio and uses a dedicated H3 compiler so its packed AV latent, dual VAEs, sigma shifts, reference tagging, and accelerator policy do not leak into WAN/LTX compilers.

The frontend must not reintroduce hardcoded copies such as `VIDEO_ROUTE_MATRIX`, model-family arrays, loader arrays, generation-type arrays, or route-specific parameter-default tables.

## Video LoRA Stack

The built-in `video.lora_stack` extension owns the canonical Assets mount `video.assets.lora_stack`. Its payload may be managed from the Assets workspace, but graph compatibility is evaluated against the selected generation route.

Video LoRA application uses three layers of authority:

```txt
portable LoRA rows
  -> exact Video LoRA support matrix
  -> compiler-owned patch profile
  -> validated model patch
```

The extension does not infer ComfyUI node ids from the selected family. A compatible compiler declares the exact upstream model reference and consumer input(s), and the LoRA runtime verifies that relationship before graph mutation.

### MiniMax H3 Phase 5

MiniMax H3 UNET is the first runtime-onboarded family. The same model-only LoRA engine applies to:

- Txt2Vid;
- Img2Vid;
- First + Last Frame;
- Reference-to-Video;
- Video-to-Video.

The H3 compiler publishes one patch anchor immediately before `MiniMaxH3SigmaShift`. Standard rows are inserted first, then `role=speed` rows such as Turbo/LightX2V, after which the model continues through H3's existing sigma shift and optional Sage/Spectrum/T8 processing.

This means H3 Img2Vid does **not** have a special Turbo graph. Its Turbo support comes from the same universal model anchor used by every supported H3 UNET mode.

The legacy H3 Turbo fields remain compatible with saved/request payloads. Neo converts them into one `role=speed` row, and suppresses the synthetic row if the same LoRA is already present in the universal stack.

H3 discovery now requires `LoraLoaderModelOnly`; generic `LoraLoader` is not treated as an equivalent fallback. Speed-candidate discovery recognizes H3/MiniMax/Hailuo family aliases plus Turbo/LightX2V/Lightning/few-step/distilled tokens, so files such as `MiniMax-LightX2V-4steps.safetensors` can be surfaced even without literal `h3` in the filename. The classifier is recommendation-only and never blocks an explicitly selected LoRA.

H3 GGUF Video LoRA/Turbo remains fail-closed until its exact GGUF model/LoRA loader topology is validated. LTX and WAN universal runtime onboarding also remains later work; their support-matrix entries do not automatically activate graph mutation.

See `guides/02_VIDEO/video_lora_stack.md` for the full payload, support-matrix, patch-profile, loader-safety, and diagnostic contract.

## Workspace body composition

The five Video workspace applications use a **stable two-rail body**. The family/loader/generation-type controls remain in the Video command strip. Switching workspaces changes only the left rail; the right rail stays dedicated to the current Prompt + Preview + Parameters generation recipe.

- **Generation left rail** — route-required Source/Reference, backend/VRAM readiness, built-in generation tools, External Extensions, generation-mode-specific panels, and Route Status.
- **Assets left rail** — staged source inventory, the canonical Video LoRA Stack mount/state, compatible Assets tools/extensions, and source-input controls.
- **Reference left rail** — route-aware image/video/depth/motion reference controls plus compatible Reference extensions.
- **Finish left rail** — finish presets, Frame Interpolation, SeedVR2 Upscale, Repair/Cleanup, and compatible Finish extensions. Finish tools use workspace-scoped compatibility and do not become unavailable merely because WAN/LTX/H3 selection changes.
- **Results left rail** — Generation History plus the Video Output Inspector. The Inspector owns selected-output playback, saved Generation Setup, saved prompts/parameters/sources, executed extensions, lineage, replay, and Expert metadata.
- **Persistent right rail** — live Prompt + Preview + Parameters for the selected Video generation route, visible across Generation, Assets, Reference, Finish, and Results.

Route Status and workspace-specific controls never move into the persistent right rail. The Inspector's saved prompt/parameter metadata remains distinct from the live generation controls on the right.

Built-in Video tools are presented as first-class Neo tools. Native Video features such as VRAM Advisor, Video LoRA Stack, Prompt/Motion Schedule, Audio-Video, Frame Interpolation, Upscale, Repair, and Output Recorder are identified as built-in capabilities. Third-party/installed packages are rendered only inside a distinct **External Extensions** section after compatibility filtering.

Start with `guides/02_VIDEO/README.md` for the current authority map. See `video_workspace_layout.md` for layout and `video_output_inspector.md` for Results inspection/replay.

## Surface-aware extension routing

Video extensions use the shared Neo extension runtime, but compatibility is resolved from the active **Video** context: provider/backend profile, canonical family, loader, generation type, route id/status, and workspace app. Video extension routing must not read the active Image draft or pass through an Image provider gate.

The canonical Video generation workspace id is `generation` (singular). Image keeps `generations`; the shared runtime normalizes these per surface. Video workspace locations such as Finish and Results are not generation modes.

Built-in and external Video extensions share the same compatibility/runtime contract. Origin controls placement only: built-ins are direct workspace tools and external packages belong in the External Extensions section. The current UI keeps built-in and external placement separate while sharing one compatibility authority.

Graph-mutating Video extensions have an additional rule: compatibility state alone is not permission to rewrite a workflow. The compiler must publish the appropriate patch profile/anchor for that exact route, and the runtime must fail closed when the profile or loader contract is missing or stale.

See `guides/02_VIDEO/video_generation_extensions.md` for the compatibility matrix, route-context scopes, and manifest rules.

## Workspace rule

The active Video backend profile controls the route and visible parameters. Neo does not create a separate workspace for every provider.

### ComfyUI profiles

The existing WAN/LTX/H3 and local model controls remain available, including model family, loader, components, Compile, backend probe, sampler/scheduler, guidance, frame timing, VRAM, decode, custom-node controls, and route-compatible built-in generation helpers.

### Grok Imagine Video profile

The same Video workspace switches to cloud controls:

- Text to Video (`txt2vid`)
- Image to Video (`img2vid`)
- model
- positive prompt
- existing source-image uploader for image-to-video
- duration
- aspect ratio
- resolution
- provider progress
- existing Video results and output folders

Comfy-only controls are hidden and excluded from the cloud request. Switching profiles preserves each route's draft state. Local Video LoRA support must not be inferred for cloud providers unless the provider itself exposes a compatible LoRA contract.

## Output ownership

Provider output is persisted into Neo-owned Video storage and registered in the existing result ledger. The gallery and preview should not depend on temporary provider URLs.

Use this guide together with the selected provider guide, `video_lora_stack.md` when LoRAs are involved, and the live Video snapshot/profile status.
