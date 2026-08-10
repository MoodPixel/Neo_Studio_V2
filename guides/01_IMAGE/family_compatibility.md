---
guide_id: image.family_compatibility
title: Image Family Compatibility and Feature Gating
surface: image
scope: built_in
applies_to:
  - image
  - model_family
  - main_model_type
  - workflow_mode
  - inpaint
  - outpaint
  - lanpaint
  - multi_ksampler
  - res4lyf
  - clownshark
  - parameter_integrity
tags:
  - image
  - compatibility
  - model families
  - loaders
  - routes
  - lanpaint
  - multi ksampler
  - res4lyf
priority: 124
version: 2
updated: 2026-08-07
---

# Image Family Compatibility and Feature Gating

Neo resolves Image capabilities from the exact runtime route, not from a broad family label alone:

```text
Backend + Family + Loader + Mode + Masked Edit Engine
```

Phase 7 adds `neo.image.family_compatibility.v1` as the central compatibility view used by local ComfyUI / ComfyUI Portable discovery and pre-queue validation.

## Why this exists

A feature being technically present in Neo does not mean every model family can use it safely. The final graph architecture matters. Examples:

- SDXL checkpoint routes expose a normal compiler-owned `KSampler`.
- Ideogram 4 uses `SamplerCustomAdvanced` and a dual-model guider graph.
- LanPaint owns its own route-native sampler topology.
- HiDream, Anima and Ideogram 4 masked editing currently use their exact LanPaint adapters rather than a generic checkpoint-style Native Inpaint fallback.

Neo must gate by that exact graph contract instead of borrowing support from another family.

## Feature states

The compatibility matrix may report:

- **available** — route/compiler shape is supported; any required live node is present.
- **requires_discovery** — static route is compatible, but Connect/Test is needed to inspect Comfy `/object_info`.
- **missing_dependency** / **missing_core_node** — the route is structurally compatible but the connected backend lacks a required node.
- **incompatible_dependency** — a custom node is installed but its live signature no longer matches the contract Neo knows how to compile.
- **gated** — this family/loader/mode/engine does not have a verified graph contract for that feature.

Gated or missing capabilities must fail clearly when explicitly requested. Neo must never silently drop the feature or switch to a different family compiler.

## Sampler architecture classes

### Core KSampler

Routes with a compiler-owned standard `KSampler` may use:

- Multi-KSampler Stage 2/3 refinement;
- ClownsharKSampler / RES4LYF replacement when the live node signature is compatible;
- `LatentUpscaleBy` transitions when the core node exists.

This includes many SD, Flux, Krea 2, Qwen, Z-Image, HiDream Generate, and Anima Generate/Img2Img graphs.

### LanPaint route-native

LanPaint routes keep their family-specific sampler/guider topology. They are **not** silently converted into core Multi-KSampler chains.

Current Phase 7 behavior:

- Parameter Truth remains active.
- Parameter Integrity still records the final local graph.
- Multi-KSampler / ClownsharKSampler remain gated until a dedicated LanPaint stage adapter exists.

### Custom advanced

Ideogram 4 native generation uses a custom advanced sampler/guider graph. Core Multi-KSampler and RES4LYF replacement are therefore gated for this architecture until an explicit adapter is implemented.

## Masked-edit compatibility

Native and LanPaint are separate engine contracts. A family may support one, both, or neither for a masked mode.

Important Phase 7 locks:

- **SDXL / SD 1.5:** Native Inpaint/Outpaint and LanPaint masked routes are available where their exact adapters are bound.
- **SD 3.5:** masked routes are LanPaint-only in the current local contract.
- **Flux 1:** Native masked routes remain available; exact LanPaint adapters may also be selected where bound.
- **Flux 2 Dev:** masked routes are LanPaint-only in the current local contract.
- **Flux 2 Klein:** Native masked routes are available and LanPaint routes are separately gated by exact adapter state.
- **Krea 2 RAW:** Native masked adapters remain available; image modes may also opt into the separate Krea 2 Identity Edit v1.2 graph. Identity Edit owns its clean target-noise path and final inpaint commit mask, so it does not stack with LanPaint.
- **Krea 2 Turbo:** Native masked adapters and the proven Krea 2 Turbo LanPaint adapter remain separate selectable routes; Krea 2 Identity Edit v1.2 is a third opt-in image-engine path and does not change the base route-matrix state.
- **Qwen Rapid AIO:** Native masked routes remain available; LanPaint is not inferred without an exact adapter.
- **Qwen Image / Edit 2509 / Edit 2511:** Native masked routes are available; exact LanPaint adapters may be used where bound.
- **Z-Image / Z-Image Turbo:** Native masked routes are available; exact LanPaint adapters may be used where bound.
- **Anima:** Native Inpaint/Outpaint are gated; masked editing uses the exact LanPaint adapter.
- **Ideogram 4:** Native Inpaint/Outpaint are gated; masked editing uses its custom-advanced LanPaint adapter.
- **HiDream:** Native masked modes are gated; HiDream-I1 masked editing uses its exact LanPaint adapter.
- **Wan Image / Hunyuan Image:** remain provider/implementation gated; Neo does not invent local Comfy image compilers for them.

## Outpaint synchronization

Phase 3 added real LanPaint Outpaint compilation. Phase 7 synchronizes the family manifest with the executable adapters for:

- SD 3.5;
- Flux 2 Dev;
- Anima;
- Ideogram 4;
- HiDream.

These states mean **LanPaint Outpaint is executable**. They do not promote an unverified Native Outpaint compiler for those families.

Z-Image Turbo GGUF Img2Img/Inpaint/Outpaint manifest states are also synchronized with the already-enabled provider routes instead of remaining stale `implementation_target` entries.

## Parameter Truth remains global

Compatibility decides whether a requested graph is valid. It does **not** decide what sampling values are aesthetically recommended.

When a field is exposed and accepted by the selected graph, explicit user input remains final truth:

```text
user value → validation → exact compiler input → final graph proof
```

Family defaults such as Krea 2 RAW 52-step, Krea 2 Turbo 8-step, or Z-Image Turbo low-step profiles are defaults/recommendations only. Neo must not rewrite an explicit user value to match them.

## Live dependency checks

Phase 7 uses Comfy `/object_info` when the requested feature needs live proof:

- `ClownsharKSampler_Beta` for RES4LYF;
- `LatentUpscaleBy` for Multi-KSampler inter-stage latent upscale;
- `InpaintCropImproved` + `InpaintStitchImproved` for Native Crop & Stitch.

If a requested dependency is missing or has an incompatible signature, queue submission is blocked rather than silently degraded.

## Runtime metadata

The selected compatibility entry is preserved under:

```text
actual_params._neo_family_compatibility
```

Backend discovery also exposes an `image_family_compatibility` matrix so the frontend can disable unsupported engine/sampler choices before submission.

## Source authority

The uploaded Neo Studio ZIP remains implementation authority. GitHub is read-only reference material and must not be modified by this work.
