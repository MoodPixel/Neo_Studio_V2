---
guide_id: video.model_families
title: Video Model Families and Canonical Routing
surface: video
scope: built_in
applies_to:
  - video_generation
  - comfyui
  - comfyui_portable
tags:
  - video
  - model families
  - loaders
  - routing
  - parameter profiles
priority: 75
version: 4
updated: 2026-08-14
---

# Video Model Families and Canonical Routing

Neo Studio keeps one backend-owned route catalog for local Video generation. The current authority is `neo_app/video/route_matrix.py`, published through `GET /api/video/route-matrix`.

## Routing chain

```txt
Backend profile
  ↓
Model family
  ↓
Loader
  ↓
Generation type
  ↓
Canonical route
  ↓
Parameter profile
  ↓
Compiler / generate request
```

The browser derives Family, Loader, and Generation Type options from the route-matrix payload. It must not own a duplicate route catalog.

## Route states

| State | Visible | Selectable | Runnable | Meaning |
|---|---:|---:|---:|---|
| `enabled` | yes | yes | yes | supported route |
| `experimental` | yes | yes | yes | usable route with explicit experimental status |
| `planned` | yes where relevant | no | no | known route contract without runnable product support |
| future registry entry | catalog only | no | no | family/loader known but no selectable route |

Experimental does not mean hidden. In particular, WAN Rapid AIO GGUF Txt2Vid and Img2Vid are intentionally selectable while retaining their experimental status. Planned Native Workflow routes remain disabled.

## WAN 2.2

WAN routing is resolved from the live catalog rather than a UI whitelist. Current catalog lanes include:

- UNET / Diffusion — enabled Txt2Vid and Img2Vid.
- GGUF — enabled WAN 2.2 14B dual-noise Img2Vid route.
- WAN Rapid AIO GGUF — experimental Txt2Vid and Img2Vid routes using dynamic Comfy model catalogs.
- Native Workflow — planned routes, visible but not selectable.

## MiniMax H3

MiniMax H3 is a first-class local audio-video family. Native UNET / Diffusion routes are enabled for Txt2Vid, one-keyframe Img2Vid (first **or** last), First/Last Frame, and `reference_to_video` / Ref2VA. GGUF variants expose the same four generation contracts as `experimental` routes because they depend on external GGUF loaders and community model variants.

H3 route defaults carry H3-specific fields such as video/audio sigma shifts, keyframe role, reference-image sizing, optional Turbo LoRA, and a single approximate-accelerator selection. The compiler treats native video + stereo audio as one output contract rather than bolting audio on after video generation.

Ref2VA is its own generation type because semantic picture/video/audio references are not interchangeable with hard first/last temporal keyframes. See `guides/02_VIDEO/minimax_h3_local_support.md`.

## LTX 2.3

LTX 2.3 exposes its enabled UNET/GGUF routes from the same catalog, including advanced modes already represented in the backend route matrix such as First/Last Frame, MultiScene, Extend, Video-to-Video, Depth/Motion, Prompt/Motion Schedule, and Audio-Video where the exact route is enabled. Native Workflow entries remain planned until promoted.

## Parameter ownership

Route-specific defaults live directly on the canonical `VideoRoute` records. `neo_app/video/parameter_profiles.py` no longer owns a second route-default table. It asks `route_parameter_profile(route_id)` for those route-owned defaults, then applies the selected VRAM profile and produces `neo.video.parameter_profile.v3`.

The browser loads:

- `/api/video/route-matrix` for routing/catalog truth;
- `/api/video/parameter-profile` for the active route + VRAM parameter contract.

A frontend fallback may render values already present in the route-matrix payload if the parameter-profile endpoint is temporarily unavailable, but that fallback is degraded display behavior, not a second routing authority.

## Cloud provider boundary

This family matrix governs local ComfyUI-style Video routes. Cloud Video profiles remain provider-capability-driven. They may use the same Video workspace and result storage, but the local WAN/LTX/H3 route matrix must not be used to invent cloud capabilities.

## Extension compatibility boundary

The canonical Video route is also the input to extension compatibility, but route availability does not automatically grant every extension. The shared extension runtime receives the selected Video provider/backend, family, loader, generation type, route id/status, and workspace, then narrows support through the extension manifest.

For example, experimental WAN Rapid AIO routes may use route-agnostic helpers such as Size / Timing Presets and VRAM Profile Advisor, while LTX-only Audio-Video, Depth/Motion, and Prompt/Motion extensions remain unavailable. Finish tools can declare `route_context_scope: workspace` so their compatibility follows the Finish backend/source lane instead of the currently selected generation family.

See `guides/02_VIDEO/video_generation_extensions.md`.


See `guides/02_VIDEO/README.md` for the consolidated current Video authority map.
