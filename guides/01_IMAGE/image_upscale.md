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
version: 3
updated: 2026-08-14
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

Image Upscale needs either a connected Comfy-compatible backend or a connected Forge profile exposing the native Extras/upscaler contract. The Neo panel stays the same while the provider path changes.

| Route | State |
|---|---|
| ComfyUI / ComfyUI Portable image backend | Available as a standalone finish utility. |
| Forge / Forge Neo | Available when `/sdapi/v1/extra-single-image` and the live upscaler catalog are discovered. Uses Forge Extras; SeedVR2 is hidden. |
| A1111 / cloud API only | Provider gated unless a compatible local Comfy/Forge Image Upscale route is connected. |
| xAI Grok output | Can be staged into a compatible local Comfy or Forge Image Upscale route without rerunning Grok. |

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
| **Upscale engine** | Comfy: `Basic / ESRGAN / interpolation` or `SeedVR2 experimental`. Forge: native `Forge Extras upscale`. |
| **Upscale model** | Comfy model/interpolation option or a live Forge `/sdapi/v1/upscalers` entry, depending on the selected backend. |
| **Resize method** | Comfy-only interpolation/model correction choice. Forge uses its live Extras upscaler catalog instead. |
| **Restore assist** | Comfy: Off or CodeFormer. Forge: only Off, CodeFormer, and/or GFPGAN when the selected profile reports those face restorers. |
| **CodeFormer model** | Comfy uses the discovered face-restorer model path; Forge uses its built-in CodeFormer control and does not expose a separate model picker. |
| **CodeFormer fidelity** | CodeFormer weight. Forge also exposes a separate restore visibility value. |
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

SeedVR2 is experimental and expects `ComfyUI-SeedVR2_VideoUpscaler` plus models in `ComfyUI/models/SEEDVR2/`.

SeedVR2 remains Comfy-only in E1. When Forge is selected, Neo coerces the panel to the Basic/Forge Extras engine instead of trying to translate SeedVR2 nodes into Forge.

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
- check the currently selected Image profile; Forge and Comfy catalogs must never be borrowed from another profile;
- tell the user uploaded/staged files have priority over current preview;
- use High-Res Lab instead when the user wants prompt-guided diffusion refine;
- do not promise SeedVR2 unless the needed custom node/models are installed.


## Phase 10 — Forge Extras provider UI and execution

When the selected Image profile is Forge Neo, the same Image Upscale extension becomes a Forge-native Extras panel. It does not expose Comfy workflow/node controls.

The selected Forge profile supplies:

- primary and secondary upscaler names from `/sdapi/v1/upscalers`;
- optional face-restorer names from `/sdapi/v1/face-restorers`;
- the standalone execution boundary `/sdapi/v1/extra-single-image`.

Forge controls include:

| Control | Forge Extras field |
|---|---|
| Scale factor / Exact dimensions | `resize_mode`, `upscaling_resize`, `upscaling_resize_w`, `upscaling_resize_h` |
| Crop to exact size | `upscaling_crop` |
| Primary upscaler | `upscaler_1` |
| Secondary upscaler | `upscaler_2` |
| Secondary visibility | `extras_upscaler_2_visibility` |
| CodeFormer/GFPGAN visibility | `codeformer_visibility` / `gfpgan_visibility` |
| CodeFormer fidelity | `codeformer_weight` |
| Upscale before restore | `upscale_first` |

Rules:

- the selected profile is the only catalog and execution authority;
- no connected profile search or Forge-to-Comfy fallback is allowed;
- SeedVR2 and Comfy node/model controls stay hidden under Forge;
- face restoration appears only when the selected Forge profile reports it;
- the output is appended as a derived result with source/parent lineage;
- Image Upscale remains a pixel-processing Extras operation and is not Forge native High-Res Fix.


## IMG-R17A preset dispatch parity hotfix — 2026-08-14

The Finish workspace **Generate** action now routes into the standalone **Image Upscale** queue whenever the active Image workspace app is **Finish** and Image Upscale is enabled.

That means:

- preset-driven runs such as **Preserve 2×**, **Preserve 4×**, and **Portrait restore 2×** no longer fall back into a normal image generation queue from the Finish workspace;
- custom and preset configurations now follow the same standalone upscale path;
- uploaded/staged source files still take priority over the currently selected result;
- the dedicated **Upscale selected result** and **Run uploaded batch** buttons continue to work as before.

If the Finish workspace is open and the user is clearly trying to upscale, Neo should treat that as an upscale request, not a fresh prompt-generation request.
