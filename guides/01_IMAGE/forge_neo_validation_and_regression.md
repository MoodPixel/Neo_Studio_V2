---
guide_id: image.forge_neo_validation_and_regression
title: Forge Neo Validation and Regression Protection
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
  - validation
  - regression
  - route-authority
priority: 100
version: 2
updated: 2026-07-31
---

# Forge Neo Validation and Regression Protection

For the canonical current Forge setup and support matrix, see `guides/01_IMAGE/forge_neo_complete_support.md`.

Phase 6 locks the Forge family-routing work from Phases 1–5 behind one deterministic validation contract:

```text
neo.provider.forge_validation.v1
```

Implementation:

```text
neo_app/providers/forge_neo_validation.py
scripts/validate_forge_neo_phase6.py
```

## What the offline matrix proves

The validator runs representative sanitized Forge profiles through the complete Neo contract chain:

```text
route authority
→ live model/module classification
→ selected-profile route intersection
→ Forge model-bundle translation
→ family workflow compiler
→ strict UX gating
→ diagnostic redaction
```

The locked matrix covers:

- SD 1.5 checkpoint txt2img/img2img/inpaint/outpaint;
- SDXL checkpoint txt2img/img2img/inpaint/outpaint;
- Flux 1 GGUF txt2img and experimental img2img;
- Flux.2 Klein with the regular-img2img setting enabled and disabled;
- Krea 2 Turbo GGUF txt2img;
- Qwen Image GGUF txt2img;
- Qwen Image Edit 2509 single-source img2img/edit baseline; E1 separately covers optional verified ImageStitch references;
- Z-Image Turbo GGUF txt2img;
- unsupported/provider-gated family profiles;
- missing-module profiles;
- fail-closed Flux inpaint, disabled Flux.2 img2img, multi-source Qwen Edit, and Rapid AIO requests.

Run it with:

```bash
python scripts/validate_forge_neo_phase6.py
```

Full JSON:

```bash
python scripts/validate_forge_neo_phase6.py --json
```

Write a report without hardcoding a machine path:

```bash
python scripts/validate_forge_neo_phase6.py --output <report-path>.json
```

Validate a delivery patch ZIP for unsafe paths, runtime state, caches, bytecode, databases, logs, model files, and traversal entries:

```bash
python scripts/validate_forge_neo_phase6.py --patch <phase-patch>.zip
```

## Regression-lock integration

`neo_app/models/route_regression_lock.py` maps Forge-critical paths to the core Phase 1–6 groups plus the post-closeout extension groups:

- `forge_route_authority`
- `forge_live_classification`
- `forge_loader_translation`
- `forge_workflow_compilers`
- `forge_strict_ux_gating`
- `forge_validation_matrix`
- `forge_builtin_feature_remap_e1`
- `forge_extra_features_e2`
- `forge_generic_extension_bridge_e3`

Changing a Forge compiler, classifier, translator, overlay, or selector also triggers the shared route-matrix, mode-filtering, and parameter-visibility baseline groups.

## Validation boundary

**Offline validation is not physical GPU validation.**

The deterministic matrix proves contract consistency and fail-closed behavior. It does not prove:

- that a real Forge installation can load every representative model;
- output quality or prompt adherence;
- VRAM usage, OOM recovery, or quantization performance;
- backend-specific module compatibility;
- actual generated image dimensions and content;
- extension behavior under a real Forge process;
- third-party E3 script compatibility, extension-manager updates, and live schema-fingerprint drift.

Physical signoff still requires real local profiles for SDXL, SD 1.5, Flux, Flux.2 Klein, Krea 2, Qwen Image, Qwen Image Edit, and Z-Image. Record the exact Forge revision, model filenames, module filenames, GPU, VRAM, workflow, result, and failure logs. Do not mark a family physically validated from unit tests alone.

## Public-repository rules

- Validation fixtures use synthetic portable model/module names.
- Absolute backend paths are deliberately inserted into raw fixture records and must disappear from classified, UX, and redacted compile payloads.
- Reports must not contain credentials, authorization headers, source files, masks, raw base64 images, runtime databases, or personal paths.
- Patch archives must contain only changed/new source, tests, guides, and records.
- GitHub is reference-only for this phase and is not modified.
