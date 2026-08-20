---
guide_id: image.forge_neo_workflow_compilers
title: Forge Neo Workflow Compilers
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
  - workflow-compilers
  - img2img
  - outpaint
  - qwen-edit
priority: 98
version: 3
updated: 2026-07-31
---

# Forge Neo Workflow Compilers

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

Phase 4 adds the provider-owned compiler registry and final Forge payload schema:

```text
neo.provider.forge_workflow_compilers.v1
neo.provider.forge_compile.v5
```

Implementation:

```text
neo_app/providers/forge_neo_workflow_compilers.py
neo_app/providers/forge_neo_outpaint.py
neo_app/providers/forge_neo_compile.py
```

## Execution chain

Forge generation is authorized only through the complete chain:

```text
route authority
∩ selected-profile live classification
∩ loader translation
∩ family/workflow compiler
∩ provider validation
```

A translated model bundle is not sufficient by itself. The route must be `available` or `experimental_available`, its compiler ID must be registered, required settings must be discovered and enabled, and all source/mask requirements must be satisfied.

## Compiler registry

| Compiler | Supported workflows |
|---|---|
| `forge.sdapi_checkpoint` | SD 1.5/SDXL txt2img, img2img, inpaint |
| `forge.sdapi_outpaint` | SD 1.5/SDXL outpaint through Neo canvas/mask preprocessing |
| `forge.sdapi_modern_txt2img` | Flux 1, Flux.2 Klein, Krea 2 RAW/Turbo, Qwen Image, Z-Image/Turbo txt2img |
| `forge.sdapi_modern_img2img` | Flux 1 and Flux.2 Klein img2img |
| `forge.sdapi_qwen_edit` | Qwen Image Edit 2509 img2img/edit; optional extra references through verified ImageStitch Integrated |

Routes outside this table remain gated even when Forge discovers a matching model.

## Modern payload rules

Modern families still use Forge's standard generation endpoints. Their translated primary model and modules are applied through:

```text
override_settings.sd_model_checkpoint
override_settings.forge_additional_modules
```

Flux-family guidance is mapped to Forge's `distilled_cfg_scale`. Existing Neo sampling presets remain authoritative; provider defaults are used only when the submitted job leaves a value at Provider Default.

Flux.2 Klein regular img2img additionally requires the live setting capability:

```text
flux2_klein_regular_img2img
```

A missing or disabled setting blocks compilation.

## Qwen Image Edit and Forge ImageStitch

Qwen Image Edit 2509 always keeps one main source image in Forge `init_images`. E1 can additionally translate Neo Stitch pairs / extra source slots into `ImageStitch Integrated` reference images when the selected profile exposes the verified three-argument script contract.

The compiler never enables this from the script name alone. Missing or changed argument metadata adds `image_stitch_contract_not_verified` and blocks only the extra-reference submission. The same optional reference path is available to Flux.2 Klein img2img when its normal regular-img2img prerequisite is satisfied.

## SD outpaint

Forge does not expose a separate standard outpaint endpoint. Neo owns the preprocessing step:

1. Read the Neo-owned source image.
2. Expand the requested canvas.
3. Seed expanded regions with source edge pixels.
4. Build a white generation mask with a protected black source interior.
5. Align the final dimensions to the Forge resolution step.
6. Submit the expanded image and mask through `/sdapi/v1/img2img`.

Source paths and generated base64 images are not persisted in diagnostics. Only padding, size, overlap, and policy metadata are retained.

## Still gated

- Flux Fill inpaint/outpaint until Fill-model identity is enforced.
- Krea 2 img2img/inpaint/outpaint.
- Qwen Image base-model img2img/inpaint/outpaint.
- Qwen Image Edit mask-based inpaint/outpaint; extra-reference edit on profiles without the verified ImageStitch contract.
- Z-Image img2img/inpaint/outpaint.
- Qwen Rapid AIO.
- Wan Image-surface generation.
- Hunyuan Image, HiDream, unclassified models, and generic A1111 routes.

## Public-repository rules

Compiler contracts contain portable model/module names only. They do not serialize local model roots, source paths, credentials, authorization headers, runtime databases, or full base64 images. GitHub was used read-only for upstream reference and was not modified.

## Phase 5 UI boundary

Phase 4 compiler registration does not by itself place a route in the UI. Phase 5 intersects compiler-ready routes with the selected Forge profile and publishes only executable tuples through `neo.provider.forge_ux_gating.v1`. Route-owned field/control policy prevents unsupported controls from leaking across compiler families. The provider compiler remains the final authority at submission time.

## If a compiled route is unavailable

Neo exposes only workflow combinations that the selected Forge profile currently reports as executable. If a family/loader/workflow combination disappears after a Forge change, refresh the profile in **Admin → Backends → Image** and check `forge_neo_validation_and_regression.md` for user troubleshooting steps.

