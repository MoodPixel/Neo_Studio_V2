---
guide_id: image.forge_neo_provider_foundation
title: Forge Neo Provider Foundation
surface: image
scope: built_in
applies_to:
  - image
  - providers
  - admin
tags:
  - forge
  - forge-neo
  - provider
  - compiler
  - checkpoint
priority: 93
version: 5
updated: 2026-07-31
---

# Forge Neo Provider Foundation

Neo Studio uses a real `ForgeNeoProvider` for the Image surface. Forge is installed and launched separately; Neo connects through Forge's A1111-compatible REST API and owns route validation, model-bundle translation, workflow compilation, durable local job state, output import, and UI gating.

For the complete current support matrix and setup flow, start with:

```text
guides/01_IMAGE/forge_neo_complete_support.md
```

## Provider boundary

Forge generation uses:

```text
/sdapi/v1/txt2img
/sdapi/v1/img2img
/sdapi/v1/progress
/sdapi/v1/interrupt
```

Admin discovery additionally reads model, module, sampler, scheduler, script, extension, settings, and diagnostics endpoints. Optional endpoint failure does not automatically invalidate core generation.

## Executable route families

The provider currently compiles:

- SD 1.5 and SDXL checkpoint txt2img, img2img, inpaint, and Neo-owned outpaint;
- Flux 1 component/GGUF txt2img and experimental img2img;
- Flux.2 Klein component/GGUF txt2img and setting-gated experimental img2img;
- Krea 2 RAW/Turbo component/GGUF txt2img;
- Qwen Image component/GGUF txt2img;
- Qwen Image Edit 2509 single-source component/GGUF img2img/edit;
- Z-Image/Turbo component/GGUF txt2img.

The active profile may expose fewer routes. Qwen Rapid AIO, unverified/generic multi-source Qwen edit, modern inpaint/outpaint, Wan Image-surface routes, Hunyuan Image, HiDream, unknown families, and unsupported packaging combinations remain gated. E1 permits route-owned ImageStitch reference inputs only when the selected profile verifies the exact built-in script contract.

## Contract chain

A request reaches Forge only when these provider contracts agree:

```text
route authority
→ live model/module classification
→ selected-profile intersection
→ loader translation
→ workflow compiler
→ strict UX policy
→ provider validation
```

Admin `Connected`, a translated bundle, and an upstream-supported architecture are not generation permission by themselves.

## Model-bundle compilation

Forge loads one primary model plus additional modules:

| Neo concept | Forge request ownership |
|---|---|
| Checkpoint, diffusion model, GGUF model | `override_settings.sd_model_checkpoint` |
| VAE/AE and text encoders | `override_settings.forge_additional_modules` |
| CLIP skip | `override_settings.CLIP_stop_at_last_layers` |
| Flux guidance | Route-owned generation parameter such as `distilled_cfg_scale` |

GGUF is a primary Forge model format. It does not inherit Comfy GGUF loader nodes or manual encoder-layout controls.

## Common NeoJob mapping

| NeoJob value | Forge request field |
|---|---|
| Prompt | `prompt` |
| Effective negative prompt | `negative_prompt` |
| Width / height | `width` / `height` |
| Steps | `steps` |
| CFG | `cfg_scale` |
| Sampler | `sampler_name` |
| Scheduler | `scheduler` |
| Seed | `seed` |
| Batch size / count | `batch_size` / `n_iter` |
| Source image | `init_images[]` |
| Denoise | `denoising_strength` |
| Inpaint/outpaint mask | `mask` |

Neo-owned images are encoded at the provider boundary. Absolute local paths are never sent as Forge request fields or returned in public diagnostics.

## Validation policy

The provider fails closed when any of these apply:

- the selected route is absent from the live executable intersection;
- the primary model is missing, ambiguous, mismatched, or not installed in the selected profile;
- a required encoder/VAE/module is missing;
- a route-specific Forge setting or script prerequisite is unavailable;
- a workflow lacks its required source, mask, or padding;
- dimensions violate the active Forge resolution contract;
- an unsupported multi-image or extension payload is requested;
- the profile is disabled, unreachable, credential-bearing in its URL, or missing core endpoints.

## Extensions

Core family/workflow support and extension support are separate contracts. Verified Forge mappings are applied only after the base workflow compiler succeeds. Unsupported graph/script integrations remain hidden and fail closed.

See:

```text
guides/01_IMAGE/forge_neo_extension_compatibility.md
```

## Authentication

Credential-bearing URLs such as `http://user:password@host` are rejected.

Optional Basic authentication can be supplied through environment-backed profile fields:

```text
api_auth_env
```

or:

```text
api_auth_username
api_auth_password_env
```

Credentials are not stored in public manifests or returned in diagnostics.

## Job and preview boundary

`ForgeNeoProvider.run_job()` compiles and queues the request through Neo's Forge lifecycle manager. It performs the synchronous REST call outside the request handler, exposes progress/preview/cancel controls, spools images, and hands results to Neo's normal output persistence layer.

A compatible optional Bridge can own durable backend jobs. Standard SDAPI mode cannot reconstruct a lost synchronous HTTP response after Neo restarts and requires explicit requeue.

See:

```text
guides/01_IMAGE/forge_neo_image_job_lifecycle.md
guides/01_IMAGE/forge_neo_optional_bridge.md
```

## Privacy and repository policy

Runtime capability caches live below `neo_data/` and are excluded from public releases. Public code, records, tests, and patches must not contain credentials, absolute user paths, model files, generated outputs, runtime databases, logs, caches, or bytecode.
