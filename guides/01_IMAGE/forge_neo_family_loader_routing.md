---
guide_id: image.forge_neo_family_loader_routing
title: Forge Neo Family and Loader Route Authority
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - models
  - admin
tags:
  - forge
  - forge-neo
  - routes
  - model-families
  - loaders
  - gguf
priority: 96
version: 5
updated: 2026-07-31
---

# Forge Neo Family and Loader Route Authority

For the canonical current setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`. This guide explains the underlying authority and its phase history.

Phase 1 introduced declarative schema `neo.provider.forge_route_authority.v1` at:

```text
neo_app/models/forge_neo_route_catalog.py
```

It is the provider-specific truth layer between Neo's backend-neutral model families and Forge's primary-model-plus-modules loading model.

## Core rule

Forge route availability is not inherited from ComfyUI and is not inferred merely because Forge upstream mentions an architecture.

A route may become selectable only when all of the following are true:

1. The Forge route authority declares the family, loader, and workflow.
2. The route state is `available` or `experimental_available`.
3. A Forge-owned compiler exists for the route.
4. The selected Forge profile exposes the required live capability.
5. Required settings, scripts, and model/module assets are present.

Phase 1 implemented the route authority. Phase 2 added conservative live model/module classification and selected-profile intersection. Phase 3 added Forge-native loader translation. Phase 4 added family workflow compilers and SD outpaint. Phase 5 made the executable live intersection the only normal-UI authority. Phase 6 locked the chain with deterministic regression validation. The live profile intersection remains mandatory.

## Forge model-bundle abstraction

All Forge families resolve through the provider loader identity:

```text
forge_model_bundle
```

This is not a new normal Image loader option yet. It is the provider translation target.

Neo loader identities remain useful as user-facing and replay metadata, but Forge compilation must translate them as follows:

| Neo concept | Forge-native meaning |
|---|---|
| Checkpoint / diffusion model / GGUF model | Primary Forge model |
| VAE, AE, CLIP, T5, Qwen text encoder | Forge additional module |
| Guidance values | Generation parameters |
| Comfy loader node requirement | Not a Forge capability |

## GGUF rule

For Forge, GGUF is a primary model file format inside the model bundle. It must not inherit Comfy node requirements such as GGUF UNet or CLIP loader nodes.

A family-level GGUF route is selectable only when Forge model discovery, required module classification, live prerequisites, and a registered family workflow compiler all agree.

## Historical Phase 1 route states

### Available

| Family | Neo loader | Workflows |
|---|---|---|
| SD 1.5 | `checkpoint` | txt2img, img2img, inpaint |
| SDXL | `checkpoint` | txt2img, img2img, inpaint |

At the Phase 1 lock these routes used `forge.sdapi_checkpoint`; Phase 4 later added SD outpaint and modern-family compilers. The current matrix appears below.

### Implementation targets

The catalog records provider-native targets for:

- Flux 1 safetensors and GGUF;
- Flux.2 Klein safetensors and GGUF;
- Krea 2 RAW and Turbo;
- Qwen Image;
- Qwen Image Edit 2509;
- Z-Image and Z-Image Turbo.

They are not selectable in Phase 1 because no matching modern-family Forge compiler is enabled.

### Provider-gated or unsupported

- Qwen Rapid AIO bundled checkpoints remain unsupported because that loader is a Comfy-specific bundled contract.
- Qwen Rapid AIO GGUF remains provider-gated until a stable Forge architecture identity exists.
- Wan remains provider-gated on the Image surface because current Forge support is video-oriented.
- Hunyuan Image and HiDream remain provider-gated because Neo has no verified current Forge route.
- `other` and the legacy `flux1_fill` alias remain unsupported.

## Family-specific prerequisites recorded now

- Flux.2 Klein regular img2img records a required Forge setting: `flux2_klein_regular_img2img`.
- Qwen Image Edit records `image_stitch_integrated` for multi-image edit handling.
- Qwen Edit retains `qwen` + `edit` detection hints for the future live classification phase.

Recording a prerequisite does not make the route available.

## Published contracts

The route authority is consumed by:

- `neo_app/models/route_matrix.py`;
- `neo_app/image/capability_overlays.py`;
- `neo_app/providers/forge_neo_capabilities.py`.

The selected-profile Image overlay publishes only implemented/selectable routes. Modern targets remain diagnostics-only.

## Public-repository rules

The catalog contains no absolute local paths, credentials, model directories, or machine-specific launch settings. GitHub was used read-only for upstream reference and was not updated.



## Current route authority summary

The current catalog version is `1.3.0`. Executable authority rows are:

- SD 1.5/SDXL checkpoint: txt2img, img2img, inpaint, outpaint;
- Flux 1 component/GGUF: txt2img and experimental img2img;
- Flux.2 Klein component/GGUF: txt2img and setting-gated experimental img2img;
- Krea 2 RAW/Turbo component/GGUF: txt2img;
- Qwen Image component/GGUF: txt2img;
- Qwen Image Edit 2509 component/GGUF: img2img/edit with one main source; optional verified Forge ImageStitch references;
- Z-Image/Turbo component/GGUF: txt2img.

Every other declared route remains planned, provider-gated, or unsupported. The selected profile may expose a strict subset when models, modules, settings, scripts, or endpoints are missing.

## Phase 2 — live model classification

Phase 2 adds two runtime schemas:

```text
neo.provider.forge_live_model_classification.v1
neo.provider.forge_live_route_intersection.v1
```

The classifier consumes only sanitized data from the selected Forge profile:

- `/sdapi/v1/sd-models`;
- `/sdapi/v1/sd-modules`;
- the safe settings catalog;
- scripts and script metadata;
- extensions and optional Bridge capabilities;
- path-safe OpenAPI feature keys;
- standard generation endpoint availability.

It does not open model files, inspect private directories, or persist absolute backend paths.

### Primary model classification

The classifier records the portable model name, file format, packaging type, precision hints, family candidates, confidence, variant, and loader candidates. Filename classification is advisory and deliberately conservative.

- Explicit family signals such as `sdxl`, `flux2` + `klein`, `qwen` + `image`, `qwen` + `edit` + `2509`, `krea2`, and `z-image` can produce exact candidates.
- A generic `.safetensors` or `.ckpt` classic checkpoint remains ambiguous between SD 1.5 and SDXL. Neo does not fabricate certainty that Forge's standard model endpoint did not supply.
- A Qwen Edit model without the verified `2509` signal is recorded but not route-eligible for the `qwen_image_edit_2509` contract.
- Nunchaku/SVDQ is classified as `nunchaku_svdq`, never as GGUF.
- GGUF is classified as a primary model format and is mapped to the family's Forge model bundle rather than Comfy nodes.

### Module role classification

Forge modules are translated into authority roles including:

- `text_encoder_primary`;
- `text_encoder_secondary`;
- `qwen_text_encoder`;
- `qwen3_text_encoder`;
- `qwen3vl_4b_text_encoder`;
- `vae`, `vae_or_ae`, and `ae_or_vae`;
- `qwen_image_vae`;
- `mmproj`.

These roles populate selected-profile model buckets and route diagnostics. They do not activate a compiler by themselves.

### Live prerequisite discovery

Phase 2 publishes canonical capability IDs rather than exposing unstable Forge option keys as product contracts:

- `flux2_klein_regular_img2img` is discovered from a matching live setting label/key/description and records whether it is enabled.
- `image_stitch_integrated` is discovered from scripts, script metadata, extensions, Bridge capability keys, or path-safe OpenAPI feature keys.

### Intersection rule

The executable route summary is now:

```text
route authority
∩ selected profile modes
∩ classified primary models
∩ required modules
∩ required settings
∩ required scripts
∩ live API endpoints
```

An `implementation_target` can report `compiler_gated_assets_ready` when every live asset is present. It still remains non-selectable until a Forge-owned compiler is implemented. The Image overlay hides such models from the normal generation catalog while keeping them visible in diagnostics.

### Compatibility behavior

Older cached Forge snapshots are upgraded in memory through the same classifier. A live refresh persists the Phase 2 schemas. Exact SDXL discovery hides SD 1.5 routes; exact SD 1.5 discovery hides SDXL routes. Ambiguous classic checkpoints may preserve both safe candidate routes until stronger architecture evidence is available.


## Phase 3 — loader translation

Phase 3 adds `neo.provider.forge_loader_translation.v1` and makes `forge_model_bundle` executable as a translation contract without promoting modern-family compilers.

- Primary checkpoint/diffusion/GGUF selections translate to `override_settings.sd_model_checkpoint`.
- Explicit VAE/AE/text-encoder/MMProj role selections translate to an ordered `override_settings.forge_additional_modules` list.
- Required modern module roles must be explicitly selected; inventory presence does not silently auto-select them.
- `bundle_ready`, `compiler_ready`, and `executable` are separate states.
- The compiler pipeline consumes translation output through `neo.provider.forge_compile.v5`.
- Phase 4 promotes only the workflows listed in the workflow compiler guide; every other modern route remains gated.

Detailed guide: `guides/01_IMAGE/forge_neo_loader_translation.md`.


## Phase 4 — workflow compilers

Phase 4 adds `neo.provider.forge_workflow_compilers.v1` and updates the route catalog to version `1.3.0`.

Available routes now include:

- SD 1.5/SDXL checkpoint txt2img, img2img, inpaint, and Neo-preprocessed outpaint;
- Flux 1 safetensors/GGUF txt2img and experimental img2img;
- Flux.2 Klein safetensors/GGUF txt2img and setting-gated experimental img2img;
- Krea 2 RAW/Turbo, Qwen Image, Z-Image, and Z-Image Turbo txt2img;
- Qwen Image Edit 2509 img2img/edit with one main source and optional verified Forge ImageStitch references.

Generic multi-source Qwen editing remains fail-closed. E1 permits extra model-reference images only when the selected Forge profile verifies the exact three-argument `ImageStitch Integrated` contract; otherwise the reference path is hidden/blocked. Modern inpaint/outpaint routes remain gated unless explicitly listed above.

Detailed guide: `guides/01_IMAGE/forge_neo_workflow_compilers.md`.

## Phase 5 strict UX gating

The selected Forge profile's `neo.provider.forge_ux_gating.v1` payload is now the sole normal-UI selector authority. The browser may not use the static authority as a fallback when live executable routes are absent. Family, loader, mode, and primary-model values are scoped to executable route tuples, stale state is coerced after refresh, and an empty intersection hides route controls and blocks generation. See `forge_neo_strict_ux_gating.md`.
