---
title: Multi-KSampler Generation
surface: image
section: parameters
status: experimental
updated: 2026-08-07
---

# Multi-KSampler Generation

**Multi-KSampler** is an advanced Image Parameters workflow that runs two or three separate sampling passes on the evolving latent.

It is not the same as choosing multiple sampler algorithms inside one KSampler. Every enabled stage is a distinct sampler node. Stage 2 receives Stage 1's latent; Stage 3 receives Stage 2's latent.

## Where it lives

Image → Parameters → **Advanced Sampling → Multi-KSampler**.

The ordinary Parameters controls are Stage 1:

- Steps
- CFG
- Sampler
- Scheduler
- Denoise
- Seed
- Sampler Engine

Stage 2 and Stage 3 expose their own Steps, CFG, Sampler, Scheduler, Denoise, seed policy, and Sampler Engine.

## Phase 6 execution model

The production Phase 6 contract is `neo.image.multi_ksampler.v2`.

Direct refinement:

```text
Stage 1 sampler
        ↓ LATENT
Stage 2 sampler
        ↓ LATENT
Stage 3 sampler (optional)
        ↓
existing downstream workflow
```

Optional latent-upscale refinement:

```text
Stage 1 sampler
        ↓ LATENT
LatentUpscaleBy
        ↓ LATENT
Stage 2 sampler
        ↓ LATENT
LatentUpscaleBy (optional)
        ↓ LATENT
Stage 3 sampler (optional)
```

Each later stage is a complete refinement pass, not a fragment of Stage 1's denoising schedule.

## Inter-stage transition

Before Stage 2 and Stage 3, the user can select:

- **None · direct latent**
- **Latent Upscale · Comfy core**

Latent Upscale uses ComfyUI core `LatentUpscaleBy`; no extra custom node is needed.

User controls:

| Control | Meaning |
|---|---|
| Scale | Exact `scale_by` value submitted to `LatentUpscaleBy`. |
| Method | `bislerp`, `bicubic`, `bilinear`, `area`, or `nearest-exact`. |

Neo validates only Comfy's hard node limits. It does not replace a user scale or method with a recommended value.

## Stage controls

Stage 2 and Stage 3 support:

| Control | Behavior |
|---|---|
| Sampler Engine | Use Stage 1, Standard KSampler, or ClownsharKSampler · RES4LYF. |
| Steps | Exact number of steps for this pass. |
| CFG | Exact stage CFG. Empty means use Stage 1 CFG. |
| Sampler | Exact sampler, or **Use Stage 1**. |
| Scheduler | Exact scheduler, or **Use Stage 1**. |
| Denoise | 0–1 strength for this pass. |
| Seed | Same as Stage 1 or increment by stage. |

Defaults shown in the UI are suggestions only. Parameter Truth remains authoritative.

## Sampler engines

Phase 6 supports mixed stage chains when the route starts from a compiler-owned core KSampler:

```text
KSampler → KSampler
KSampler → ClownsharKSampler
ClownsharKSampler → KSampler
ClownsharKSampler → ClownsharKSampler
```

RES4LYF integration is applied after the stage graph is built, so the latent-chain compiler stays single-source. See `guides/01_IMAGE/res4lyf_clownshark_sampler.md`.

## Route compatibility

Multi-KSampler is enabled only on local ComfyUI / ComfyUI Portable routes that expose Neo's compiler-owned base `KSampler` anchor.

This includes many SD, Flux, Krea 2, Qwen, Z-Image, HiDream, and compatible Anima workflows that compile through core KSampler.

It remains intentionally gated for route-native custom sampler graphs such as:

- LanPaint custom samplers;
- Ideogram 4 `SamplerCustomAdvanced`;
- other graphs without a stable core KSampler anchor.

A gated route fails clearly instead of silently dropping the requested stages.

## Final graph integrity

Phase 6 adds a dedicated final-graph proof after Multi-KSampler construction and after optional RES4LYF replacement, but before the shared Parameter Integrity gate and Comfy `/prompt` queue.

Neo verifies:

- stage order;
- physical sampler node for every stage;
- Standard vs RES4LYF backend;
- Steps;
- CFG;
- Sampler;
- Scheduler;
- Denoise;
- Seed;
- previous-stage latent wiring;
- `LatentUpscaleBy` node class;
- upscale method;
- upscale scale;
- terminal sampler identity.

Any concrete mismatch blocks queueing.

## Output Inspector

Generated outputs record the resolved stage graph under `runtime.actual_params.multi_ksampler`.

The Output Inspector shows:

- stage count;
- sampler engine per stage;
- node ids;
- stage Steps / Sampler / Denoise;
- inter-stage latent upscale operations;
- final graph integrity state;
- full runtime JSON in Expert mode.

## Interaction with High-Res Lab

Multi-KSampler's inter-stage latent upscale is part of the sampling pipeline. High-Res Lab remains a separate finishing/refinement feature.

Both may appear in the same final workflow, but Neo does not merge their contracts or silently substitute one for the other.

## Quality claim

Neo makes no automatic claim that two or three sampler passes improve every image. Later passes can add detail, but high Denoise values or aggressive upscaling can also create drift or artifacts. The user owns the experiment and all explicit stage values.

## Phase 7 — family-by-family gating

Multi-KSampler is no longer inferred merely from a family name. Neo checks the active `neo.image.family_compatibility.v1` entry and requires a compiler-owned core `KSampler` architecture.

- core-KSampler routes may build Stage 2/3;
- LanPaint route-native sampler graphs remain gated pending a dedicated adapter;
- Ideogram 4 custom-advanced sampler graphs remain gated;
- inter-stage `LatentUpscaleBy` additionally requires live core-node discovery.

A request that was saved while compatible but is no longer compatible with the selected route/backend is blocked before `/prompt`; Neo does not collapse it to one stage.
