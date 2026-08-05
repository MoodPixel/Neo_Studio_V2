---
guide_id: image.forge_neo_capability_overlays
title: Forge Neo Image Capability Overlays
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - admin
tags:
  - forge
  - forge-neo
  - capabilities
  - catalogs
  - overlays
priority: 92
version: 5
updated: 2026-08-02
---

# Forge Neo Image Capability Overlays

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

**Status:** Implemented in Forge Neo Phase 4; strict route composition added in loader-routing Phase 5  
**Scope:** Neo Image interface only

## Purpose

Neo Studio remains the primary Image frontend while Forge Neo remains the execution backend. The Image interface must therefore render only controls and catalogs that the selected Forge profile has actually discovered.

The overlay endpoint is:

```text
GET /api/image/capability-overlay?profile_id=<profile_id>
```

The response is derived from the selected backend profile and the sanitized Forge Admin capability cache. It never scans or returns another profile's model catalog.

## What the overlay controls

- checkpoint, VAE, text-encoder/module, sampler, scheduler, and upscaler catalogs;
- executable selected-profile families, loaders, modes, and route-scoped primary models;
- width/height alignment policy;
- field visibility for Forge routes;
- Forge-native route controls such as face restoration, tiling, and inpaint settings;
- extension availability based on both the extension manifest and verified Forge capabilities;
- readiness and discovery warnings shown in the Image parameters panel.

## Catalog and route isolation

When `catalog_scope` is `selected_profile`, Neo must not fall back to models from another Image backend. Phase 5 also forbids fallback to the static Forge route matrix or family manifest for normal selectors. The overlay publishes `neo.provider.forge_ux_gating.v1`; only its executable route tuples may populate family, loader, mode, and primary-model controls.

## Resolution policy

Forge Neo uses 64-pixel dimension increments by default. Neo applies this rule in three places:

1. width and height input attributes;
2. browser-side payload normalization;
3. provider-side payload compilation.

The provider compiler remains authoritative even if a client bypasses the interface.

## Extension policy

Wildcards and Style Stack are provider-neutral because they resolve before the provider boundary. Other extensions remain gated unless:

1. their manifest declares Forge support; and
2. the overlay confirms a verified Forge mapping.

A local backend is not automatically considered extension-compatible.

## Route-owned Forge controls

Control visibility is compiler-specific rather than global. Classic SD routes may expose Restore Faces, Tiling, and verified Forge inpaint controls. Flux-family routes may expose Flux guidance. Source, mask, denoise, and outpaint controls appear only when the active executable route consumes them. Comfy-only latent/context controls remain hidden.

## Deliberate limits

The overlay never promotes a route. Available routes come from registered Forge compilers plus live selected-profile requirements. Unsupported modern modes, generic/unverified multi-source Qwen edit, Rapid AIO, Wan Image-surface generation, Hunyuan, HiDream, generic A1111, and unverified extension mappings remain gated. Verified E1 ImageStitch references are exposed only by route-owned control policy.

## Phase 5 extension handoff

Phase 5 supersedes the Phase 4 blanket extension gate for a verified subset. The overlay now carries live extension contracts, mode availability, script slot counts, ControlNet model/module catalogs, and Forge embedding names and selected-profile LoRA catalog metadata. LoRA Stack, Embeddings/TI, High-Res Lab, ControlNet, and ADetailer render only when both their manifest and selected-profile capability record allow them. See `guides/01_IMAGE/forge_neo_extension_compatibility.md`.

## Phase 6.1 readiness-state reconciliation

The overlay treats `connected_with_warnings` as reachable when the profile's core Forge execution contract is healthy. Warning badges remain visible, but the Image readiness gate must not report the backend as disconnected merely because a soft-optional Admin endpoint failed.

## Strict selector handoff

When `ux_gating.ready=false`, Neo hides the Forge route selectors and generation controls and adds the blocking `forge_executable_route` readiness item. When a refresh changes executable routes, stale family/loader/mode/model state is coerced only among the new executable tuples. See `guides/01_IMAGE/forge_neo_strict_ux_gating.md`.

Phase 8 upgrades LoRA capability discovery to `forge.extra_network.lora.v2`: catalog names are profile-local, browser-safe, and never borrowed from another provider.


## Phase 9 Embeddings/TI catalog binding

Forge Embeddings/TI capability uses `forge.embedding.token.v2` and `neo.embeddings_ti.provider_catalog.v1`. The selected profile's embedding names are authoritative. Browser records contain portable catalog names, while absolute model paths remain server-side. Canonical chips store plain trigger identities; Forge renders plain triggers and ComfyUI renders the `embedding:` prefix only during compilation. Automatic provider fallback and visible prompt mutation are both disabled.


## Phase 10 Image Upscale overlay

The `image.image_upscale` policy now carries:

- `upscalers` and `face_restorers`;
- `supports_codeformer` and `supports_gfpgan`;
- exact-dimension, secondary-upscaler, crop-to-fit, and upscale-first flags;
- `selected_profile_only=true`;
- `automatic_provider_fallback=false`.

The browser must not infer face restoration from product defaults. Controls appear only from this selected-profile capability record.
