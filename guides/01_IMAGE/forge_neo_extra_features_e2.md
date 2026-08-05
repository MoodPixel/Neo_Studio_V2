# Forge Neo extra features E2

Status: **implemented offline / physical validation required**  
Date: **2026-07-31**

E2 adds three Forge-native capabilities as **Neo Studio built-in Image extensions**. Neo owns the UI and validation; Forge Neo owns execution through its live built-in always-on scripts.

## Design rule

E2 does not mirror Forge's Gradio panels and does not add a generic third-party script bridge. The selected Forge profile must expose the exact expected `/sdapi/v1/script-info` schema before Neo allows the feature.

| Neo extension | Forge script | Contract | Neo exposure |
| --- | --- | --- | --- |
| `image.pid_integrated` | `PiD Integrated` | `forge.pid.integrated.v1` | Finish |
| `image.spectrum` | `Spectrum Integrated` | `forge.spectrum.integrated.v1` | Generations |
| `image.multidiffusion` | `MultiDiffusion Integrated` | `forge.multidiffusion.integrated.v1` | Generations, img2img-family modes |

## PiD Integrated

Neo verifies the current seven-argument script shape and obtains the PiD checkpoint, VAE, and text-encoder choices from the live Forge script schema. Neo sends:

1. enabled
2. optional prompt override
3. PiD checkpoint
4. VAE
5. Gemma2 2B IT ELM text encoder/module
6. degrade sigma
7. color correction

E2 route policy is conservative: SDXL, Flux 1, Flux.2 Klein, Qwen Image, and Qwen Image Edit routes only. PiD is blocked when Neo High-Res Lab / Forge Hires Fix is active because Forge PiD itself rejects that combination.

## Spectrum

Neo verifies the eight-argument Spectrum script and exposes prediction weighting, polynomial degree, regularization, cache window, window growth, warmup steps, and stop-caching step.

Spectrum is fail-closed when the selected Forge profile has either `skip_early_cond > 0` or `s_min_uncond > 0`, matching Forge's own incompatibility with Ignore/Skip Negative Prompt optimizations.

## MultiDiffusion

Neo verifies the six-argument img2img-only script and exposes:

- Method: MultiDiffusion or Mixture of Diffusers
- Tile width
- Tile height
- Tile overlap
- Tile batch size

E2 intentionally enables execution only for SD 1.5 and SDXL on `img2img`, `inpaint`, and `outpaint`. Modern-family support remains gated until physical model tests prove it safe.

## Live discovery and schema drift

Extension presence alone is never permission. Neo requires the selected Forge profile's live script schema. If an update changes argument count, labels, mode exposure, or choices, the corresponding Neo extension becomes provider-gated instead of submitting stale positional arguments.

## What E2 does not do

- It does not install/update/remove Forge extensions.
- It does not support arbitrary third-party Forge scripts.
- It does not claim physical GPU/model validation.
- It does not widen MultiDiffusion to modern families.
- It does not bypass Phase 5 strict route/profile gating.

Generic installed-extension discovery/bridging remains a separate E3 problem.
