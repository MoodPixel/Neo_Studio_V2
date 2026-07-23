---
guide_id: image.image_upscale
title: Image Upscale
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image_finish
  - image_upscale
  - upscale
  - codeformer
  - seedvr2
  - batch_upscale
  - selected_result
  - transparency
  - rgba
tags:
  - image
  - finish
  - upscale
  - codeformer
  - seedvr2
  - batch
  - results reuse
  - transparent png
  - alpha preservation
priority: 110
version: 1
updated: 2026-07-11
---

# Image Upscale

**Image Upscale** is a built-in Image → Finish utility for increasing image resolution and optionally restoring face detail. It is a standalone queue path, not the normal Image generation compiler.

Use it when the user wants to upscale an existing saved output or uploaded image without re-running the whole prompt/model workflow.

## How it differs from High-Res Lab

| Tool | Best for | Uses prompt/diffusion refine? |
|---|---|---|
| **Image Upscale** | Resize/upscale selected result or uploaded images; optional CodeFormer face restore; experimental SeedVR2 path. | No normal prompt context / no standard KSampler refine. |
| **High-Res Lab** | Highres-style diffusion refine using the current Image recipe. | Yes, route-dependent diffusion refine. |

## Supported route shape

Image Upscale needs a connected Comfy-compatible image backend because it queues a local utility graph.

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable image backend | Available as a standalone finish utility. |
| Forge / A1111 / cloud API only | Provider gated unless a local Comfy backend is also connected. |
| xAI Grok output | Can be used as a source image only after staging into a compatible local Comfy Image Upscale route. |

## Source controls

| Control | Meaning |
|---|---|
| **Source images dropzone** | Upload one or more images for standalone/batch upscale. |
| **Clear** | Clears staged uploaded or preview-staged source images. |
| **Staged source chip** | Shows when a selected output from Preview/Results was sent into Image Upscale. |
| **Upscale selected result** | Uses the currently selected output/result as the source. If uploaded files are staged, Neo prioritizes those files. |
| **Run uploaded batch** | Queues each uploaded source image as an upscale job. |

## Main controls

| Control | Meaning |
|---|---|
| **Enable Image Upscale utility** | Enables the utility panel. |
| **Preset** | Quick setup such as Preserve 2×, Preserve 4×, or Portrait restore 2×. |
| **Target scale** | Scale multiplier. 2× is a common starter. |
| **Upscale engine** | `Basic / ESRGAN / interpolation` or `SeedVR2 experimental`. |
| **Upscale model** | Optional model from Comfy upscale model catalog. If empty, the route can fall back to interpolation-only behavior. |
| **Resize method** | Lanczos, Bicubic, Bilinear, Area, or Nearest-exact. |
| **Restore assist** | Off or CodeFormer restore. |
| **CodeFormer model** | Face restore model discovered from `ComfyUI/models/facerestore_models/`. |
| **CodeFormer fidelity** | Higher preserves original identity more; lower lets restore change more. |
| **Face detection** | Detection backend used by restore assist. |

## SeedVR2 experimental controls

When **Upscale engine** is SeedVR2 experimental, extra controls appear:

| Control | Meaning |
|---|---|
| **Transparency handling** | `Auto Preserve` detects real alpha per source, `Force Preserve RGBA` always preserves alpha, and `Discard transparency` uses RGB only. |
| **SeedVR2 DiT model / VAE model** | Models loaded from SeedVR2 folders. |
| **Output sizing** | Scale factor, short edge, max edge, or manual sizing. |
| **Short-edge resolution / Max edge** | Controls target size from source dimensions. |
| **Batch size** | SeedVR2 batch setting. Higher uses more memory. |
| **Seed** | SeedVR2 stochastic seed. |
| **Device / Offload device** | Runtime device placement, usually `cuda:0` and `cpu`. |
| **Blocks to swap** | Memory-saving swap count. Higher can reduce VRAM pressure but may slow processing. |
| **Attention** | Attention implementation. |
| **Color correction** | Output color correction mode. |
| **Input noise / Latent noise** | Noise injected into source/latent process. Use 0 for preservation. |
| **Tile size / Tile overlap** | Tiled processing controls. |
| **Swap I/O components / Encode tiled / Decode tiled / Cache models / Debug logs** | Runtime safety/performance toggles. |

SeedVR2 is experimental and expects `ComfyUI-SeedVR2_VideoUpscaler` plus models in `ComfyUI/models/SEEDVR2/`. External shared folders can be registered through `extra_model_paths.yaml` using `upscale_models`, `facerestore_models`, and `SEEDVR2` as appropriate. See [Comfy extra model paths](../07_ADMIN/comfy_extra_model_paths.md).

## Transparent PNG and RGBA upscaling

For logos, cutouts, overlays, and other transparent assets, use:

```text
Upscale engine: SeedVR2 experimental
Transparency handling: Auto Preserve — recommended
Restore assist: Off
```

Neo inspects each stored source file. When real transparency is present, it rebuilds the RGBA tensor through Comfy's `JoinImageWithAlpha` node before SeedVR2. Opaque files retain the normal RGB graph. Mixed upload batches are checked one image at a time.

The status card uses a checkerboard sample and reports whether transparency was detected, forced, discarded, or still unverified. Browser detection is only a preview; the backend inspection decides the graph.

**CodeFormer is skipped only for jobs that actually use the RGBA route.** With Auto Preserve, opaque files may still use CodeFormer while transparent files skip it independently. Force Preserve disables it because every source uses RGBA. Choose **Discard transparency** only when an opaque result is intentional.

If Neo reports that `JoinImageWithAlpha` is missing, update ComfyUI or choose **Discard transparency** to use the normal RGB route. Transparent outputs remain PNG.

## Recommended starter settings

For simple delivery upscale:

```text
Preset: Preserve 2×
Upscale engine: Basic / ESRGAN / interpolation
Target scale: 2
Restore assist: Off
```

For portrait cleanup:

```text
Preset: Portrait restore 2×
Restore assist: CodeFormer restore
CodeFormer fidelity: 0.55–0.75
Face detection: RetinaFace if available
```

## Assistant rules

When the user asks about Image Upscale:

- explain it as a standalone utility, not a normal Image generation pass;
- check for a connected Comfy backend;
- tell the user uploaded/staged files have priority over current preview;
- use High-Res Lab instead when the user wants prompt-guided diffusion refine;
- do not promise SeedVR2 unless the needed custom node/models are installed.
