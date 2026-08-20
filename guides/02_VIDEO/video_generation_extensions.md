---
guide_id: video.generation_extensions
title: Video Extensions and Route Compatibility
surface: video
scope: built_in
applies_to:
  - video_generation
  - video_finish
  - extensions
  - comfyui
  - cloud_video
tags:
  - video
  - extensions
  - routing
  - built in
  - external
  - compatibility
priority: 76
version: 5
updated: 2026-08-14
---

# Video Extensions and Route Compatibility

Video extensions use the shared Neo extension runtime, but their compatibility is resolved from a **Video-owned route context**. Video extension routing must never inherit the active Image family, loader, workflow mode, or Image provider gate.

## Canonical Video extension context

For a local Video route, compatibility is evaluated from:

```txt
surface = video
backend/profile
family
loader
generation type
route id
route status
workspace app
extension manifest
```

For cloud Video profiles, the context is provider-capability-driven and uses `backend = cloud_api`; the local WAN/LTX/H3 route matrix is not used to invent cloud support.

## Workspace normalization

Image and Video intentionally have different canonical generation workspace ids:

```txt
Image: generations
Video: generation
```

The shared runtime normalizes workspace ids per surface. Legacy Video `workspace` or `generations` state may migrate to `generation`, but current Video extension records must not be rewritten to Image `generations`.

Video subtabs such as `finish` and `results` are workspace locations. They are **not** generation modes. Video generation modes remain values such as `txt2vid`, `img2vid`, `first_last_frame`, `reference_to_video`, `depth_motion`, `prompt_schedule`, and `audio_video`.

## Route states

The extension UI uses the shared extension states:

- `available`
- `experimental_available`
- `implementation_target`
- `planned_gated`
- `provider_gated`
- `unsupported`

Video route status maps into extension state:

- Video `enabled` → extension `available`
- Video `experimental` → extension `experimental_available`
- Video `planned` → extension `planned_gated`
- unavailable/unsupported → extension `unsupported`

An extension manifest may narrow that state by backend, family, loader, generation type, or workspace.

## Generation-route vs workspace-scoped extensions

`route_context_scope` distinguishes two compatibility models.

### `generation_route`

The extension follows the selected Video generation route. This is appropriate for generation helpers that modify or configure the active family/loader/mode.

Examples:

- Size / Timing Presets
- VRAM Profile Advisor
- Audio-Video
- Depth / Motion Control
- Prompt / Motion Schedule

### `workspace`

The extension belongs to a post/output workspace and does not become incompatible merely because a different generation family is selected.

Examples:

- Frame Interpolation
- SeedVR2 Upscale
- Repair
- Output Recorder where its Results/Generation mount is explicitly declared

Workspace-scoped tools still fail closed when their required backend is unsupported.

## Current compatibility reconciliation

### WAN Rapid AIO GGUF

Rapid AIO is explicitly supported by route-agnostic generation utilities that can operate on the Rapid AIO parameter route:

- `video.size_timing_presets`
- `video.vram_profile_advisor`

Rapid AIO does **not** imply support for LTX-only generation extensions.

### MiniMax H3 external/runtime helpers

MiniMax H3 itself is a built-in local family backed by ComfyUI core H3 nodes. Optional community packs are compatibility helpers, not alternative routing authorities:

- `comfyui_gguf` enables experimental H3 GGUF loader lanes.
- `minimax_h3_spectrum` is an experimental approximate H3 accelerator.
- `minimax_h3_blockcache_t8` is an experimental approximate H3 block cache.
- `kjnodes_h3` provides optional H3/Sage-oriented helpers where the active object-info contract supports them.

Spectrum and T8 BlockCache are mutually exclusive in Neo's canonical H3 compiler. Turbo LoRA is an explicit H3 speed option and may alter fidelity. These helpers never change a route's generation semantics from keyframe mode to semantic reference mode.

### LTX-only extensions

These are restricted to their actual LTX generation types and active UNET/GGUF routes:

- `video.audio_video` → `audio_video`
- `video.depth_motion_control` → `depth_motion`
- `video.prompt_motion_schedule` → `prompt_schedule`

They must not appear as valid WAN/Rapid AIO workflow extensions.

### Finish tools

Interpolation, upscale, and repair are workspace-scoped local Video tools. They remain independent from the currently selected WAN/LTX generation route but are provider-gated on unsupported cloud backends.

### Output Recorder

The recorder is allowed for Neo-owned local and cloud Video results because both paths persist outputs into Neo's Video result ledger. Its compatibility is workspace-scoped rather than model-family-scoped.

## Built-in vs external extensions

Both origins use the same runtime contracts:

```txt
manifest
→ workspace match
→ Video route context
→ compatibility state
→ workflow apply state
```

Origin affects placement, not compatibility semantics:

- built-in → direct workspace tool
- external → External Extensions section

The current rendered placement contract is:

- Neo-native Video capabilities render as first-class built-in tools in their owning workspace;
- installed third-party packages render in a dedicated **External Extensions** section;
- both origins still use the same shared compatibility/runtime truth;
- Finish/Results workspace-scoped tools are not forced through the current generation-family route;
- The persistent right rail carries live Prompt/Preview/Parameters across all five Video workspaces; Route Status and extension/workspace tools remain in the owning left rail.

See `guides/02_VIDEO/video_workspace_layout.md`.

## Workflow apply state

Workflow application keys remain:

```txt
surface + workspace_app + workflow_mode + extension_id
```

For Video, `workflow_mode` is the Video generation type. Example:

```txt
video:generation:img2vid:video.vram_profile_advisor
```

Legacy Video keys created by the old Image-derived mode/workspace normalizer are recognized and migrated when the user changes extension apply state.

## Placement contract

The UI must not recreate the old mixed stack where built-in and external records were concatenated into one generic card list. `videoCompatibleWorkspaceExtensions()` may partition already-compatible records, but compatibility still comes from the shared extension runtime.

Native built-in features may use their specialized Video panels instead of a generic extension card. External records always remain inside the External Extensions section for the matching workspace.

## Maintenance rules

When adding a Video extension:

1. declare `surface: video`;
2. use Video workspace ids (`generation`, `assets`, `reference`, `finish`, `results`);
3. declare real Video generation types in `workflow_modes`; do not use workspace subtabs as workflow modes;
4. declare backend/family/loader limits truthfully;
5. choose `route_context_scope: generation_route` or `workspace` deliberately;
6. use route-state declarations for provider/workspace exceptions;
7. do not read `state.imageDraft` or invoke Image provider gates from Video runtime code;
8. keep built-in/external origin separate from compatibility logic;
9. add route-isolation tests before promoting compatibility.

## Related files

- `neo_app/extensions/schema.py`
- `neo_app/extensions/runtime.py`
- `neo_app/extensions/registry.py`
- `neo_app/static/js/neo.js`
- `neo_extensions/built_in/video.*/extension_manifest.json`
- `guides/02_VIDEO/video_model_families.md`
- `guides/02_VIDEO/video_tab_overview.md`
- `guides/02_VIDEO/README.md`
