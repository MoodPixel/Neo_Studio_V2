---
guide_id: image.parameter_integrity
title: Image Parameter Integrity
surface: image
scope: built_in
applies_to:
  - image_workspace
  - parameters
  - comfyui
  - comfyui_portable
  - forge
  - cloud_image_providers
tags:
  - parameters
  - diagnostics
  - sampler
  - steps
  - cfg
  - denoise
  - integrity
priority: 121
version: 1
updated: 2026-08-07
---

# Image Parameter Integrity

Neo records a parameter-integrity trace for Image generation so a user can verify that the values shown in **Parameters** are the values that reached the provider-bound workflow.

## What is tracked

The shared trace covers:

- Width and Height
- Steps
- CFG and True CFG when present
- Sampler
- Scheduler
- Denoise
- Batch Count
- Seed
- Flux Guidance / route Guidance when present
- Clip Skip when present

No prompt text, source-image path, mask path, LoRA filename, or other personal file reference is required for this trace.

## Boundaries

For local Image generation Neo records the following boundaries when available:

1. **UI before build** — the visible draft before route synchronizers run.
2. **Client payload** — the values serialized by the browser.
3. **API received** — the values received by `/api/image/generate`.
4. **API normalized** — values after type/seed/source normalization.
5. **NeoJob** — the authoritative prepared job sent to the provider.
6. **Provider actual** — the provider/compiler's applied parameter record.
7. **Workflow final** — the values physically serialized into the final Comfy graph or Forge request payload.

A negative seed is intentionally treated as delegated because Neo resolves it to a concrete random seed before provider execution. Values such as `provider_default`, `automatic`, and `auto` are also delegated rather than treated as concrete values.

## Mismatch behavior

Concrete user values are authoritative. If ComfyUI or Forge compilation changes a concrete tracked value between the prepared job and the final provider-bound workflow, Neo fails the request **before queueing** and reports the boundary that changed it.

Neo does not silently repair the mismatch. The trace includes:

- requested value;
- observed value;
- source boundary;
- destination boundary;
- mismatch count;
- last verified boundary.

Fields that a provider cannot expose at a later boundary are marked **unverified**, not silently assumed to match. Unverified is diagnostic; a proven mismatch is blocking.

## Output Inspector

Saved outputs can show a **Parameter Integrity** card in Output Inspector. Guided mode shows the status and counts. Expert mode can expand the full parameter trace JSON.

The latest submission trace is also retained in the browser as `window.__neo_last_parameter_integrity` and in local storage under `neo_image_parameter_integrity_latest` for troubleshooting.

## Provider coverage

- **ComfyUI / ComfyUI Portable:** verifies through the final graph. Standard KSampler, LanPaint KSampler, custom-sampler scheduler/guider graphs, latent batch nodes, and Flux Guidance are inspected.
- **Forge:** verifies through the final Forge request payload using canonical aliases such as `cfg_scale`, `sampler_name`, `denoising_strength`, and `n_iter`.
- **Cloud providers:** traces UI → API → NeoJob and compares provider `actual_params` when the provider exposes them. Unsupported/nonexistent provider fields remain unverified rather than fabricated.

## When debugging

If a result appears to ignore a parameter, open Output Inspector and inspect **Parameter Integrity** first. A clean local workflow should show the final boundary as `workflow_final` with zero mismatches.

## Multi-KSampler stage proof

The shared Parameter Integrity card continues to treat the original Parameters sampler as Stage 1 authority. Multi-KSampler Stage 2/3 are nested advanced-stage controls, so their requested/resolved values and physical node IDs are recorded under `actual_params.multi_ksampler.resolved_stages` rather than being flattened into the Stage 1 comparison fields. The Multi-KSampler compiler writes those values directly into the added KSampler nodes and rejects invalid values instead of substituting them.

## RES4LYF sampler-backend proof

Phase 5 adds `sampler_backend` to the integrity trace. A final core `KSampler` reports `standard`; a final primary `ClownsharKSampler_Beta` / `ClownsharKSampler` reports `res4lyf_clownshark`. The RES4LYF graph patch runs after Multi-KSampler expansion and before the final Comfy Parameter Integrity gate, so a requested primary backend that did not reach the physical graph is blocked before `/prompt`.

Stage-specific mixed Multi-KSampler backends are additionally recorded in `actual_params.res4lyf_sampler.stage_backends` and `actual_params.multi_ksampler.stage_nodes`.

## Phase 6 Multi-KSampler final-graph proof

Multi-KSampler now runs an additional stage-specific integrity check after optional RES4LYF conversion and before the shared final Parameter Integrity gate. It proves every physical Stage 1/2/3 sampler, each optional `LatentUpscaleBy` transition, previous-stage latent wiring, and the terminal sampler node. The result is stored at `actual_params.multi_ksampler.integrity` and shown in Output Inspector. A concrete mismatch blocks Comfy queue submission.

## Phase 7 — compatibility before integrity

Parameter Integrity proves values on a graph that is allowed to exist. Phase 7 therefore adds a compatibility preflight before compilation for local Image jobs:

```text
exact family/loader/mode/engine compatibility
→ compile
→ Multi-KSampler
→ RES4LYF replacement
→ Multi-KSampler physical proof
→ Parameter Integrity
→ /prompt
```

Unsupported base routes and explicitly requested incompatible advanced features fail before queue submission. The selected compatibility entry is preserved in runtime parameters as `_neo_family_compatibility` for diagnostics.
