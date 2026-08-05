---
guide_id: image.forge_neo_loader_translation
title: Forge Neo Loader Translation
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
  - loaders
  - model-bundle
  - gguf
priority: 97
version: 3
updated: 2026-07-31
---

# Forge Neo Loader Translation

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

Phase 3 adds schema:

```text
neo.provider.forge_loader_translation.v1
```

Implementation:

```text
neo_app/providers/forge_neo_loader_translation.py
```

## Purpose

Neo Studio keeps backend-neutral family and loader identities in `NeoJob`. Forge does not execute Comfy loader graphs. It loads one primary model and zero or more additional modules.

Phase 3 translates the selected Neo route into the Forge-native bundle:

```text
Neo family + loader + selected role assets
                    ↓
            forge_model_bundle
                    ↓
override_settings.sd_model_checkpoint
override_settings.forge_additional_modules
```

Forge upstream discovers `.ckpt`, `.safetensors`, and `.gguf` primary models in the same checkpoint catalog and passes the selected primary model plus `additional_modules` into its loader. Neo therefore treats GGUF as the primary model format, not as a Comfy GGUF node contract.

## Translation rules

| Neo selection | Forge output |
|---|---|
| `checkpoint`, `diffusion_model`, `gguf_unet`, or `gguf_model` | `override_settings.sd_model_checkpoint` |
| VAE/AE, CLIP, T5, Qwen, Qwen3, Qwen3-VL, MMProj | ordered `override_settings.forge_additional_modules` |
| Clip skip | `override_settings.CLIP_stop_at_last_layers` |
| Flux guidance | translated to workflow metadata and mapped by Phase 4 to Forge `distilled_cfg_scale` |

The output records the request source, portable resolved asset name, classification state, required/optional role, bundle blockers, compiler blockers, and whether the translated bundle is executable.

## Required module selections

Live discovery proving that a module exists does not silently choose it. Modern-family required module roles must be explicitly selected in the submitted Neo job. Missing selections fail the bundle translation even when the profile has compatible assets.

This prevents hidden model/module combinations and keeps replay deterministic.

## Compiler boundary

Loader translation and compiler availability are separate:

- `bundle_ready=true` means the selected model and module roles form a valid Forge bundle.
- `compiler_ready=true` means the route authority has an implemented Forge compiler.
- `executable=true` requires both.

Phase 4 registers route-owned compilers for selected Flux, Flux.2 Klein, Krea 2, Qwen Image/Edit, and Z-Image workflows. Translation now marks those routes compiler-ready only when the route authority names a registered Phase 4 compiler. Planned, provider-gated, unsupported, and unregistered routes remain non-executable.

## Existing SD compiler

The existing SD 1.5/SDXL checkpoint compiler now consumes the translation result rather than collecting model/module fields ad hoc. It supports:

- selected checkpoint;
- optional VAE;
- optional primary/secondary text encoders;
- explicit generic Forge additional modules;
- clip skip.

The active compiler schema is now `neo.provider.forge_compile.v5`.

## Privacy and portability

Translation output contains only portable asset names. Absolute backend model paths, credentials, source image paths, and private directories are not serialized into the model bundle, capability payload, Image overlay, or diagnostics.

## Published surfaces

The static translation contract is published through:

- Forge Admin snapshots;
- `neo.provider.forge_capabilities.v8`;
- the selected-profile Forge Image capability overlay.

Provider validation and compilation remain authoritative. UI publication does not bypass route or compiler gates. See `guides/01_IMAGE/forge_neo_workflow_compilers.md` for the Phase 4 workflow matrix.
