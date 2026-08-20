---
guide_id: video.seedvr2_upscale
title: SeedVR2 Video Upscale
surface: video
scope: built_in
applies_to:
  - video_finish
  - seedvr2_upscale
priority: 88
version: 1
updated: 2026-08-17
---

# SeedVR2 Video Upscale

Use **Video → Finish → Upscale** to create a non-destructive SeedVR2-upscaled child video. The selected source video remains unchanged.

## Basic workflow

1. Choose or upload the source video in the Upscale finish tool.
2. Choose a **VRAM Profile**. Use **Custom** when you need a target above the profile's normal short-edge limit or want to tune memory controls manually.
3. Choose the **DiT Model** and **VAE Model** shown by Neo.
4. Choose the output **Target** and format.
5. Open **Advanced** only when you need custom sizing, batch, overlap, tiles, color correction, or block swap.
6. Run **Compile** to inspect readiness without queueing, or **Run Upscale** to process the video.

## Model dropdowns and Auto

Neo reads the connected ComfyUI SeedVR2 loader catalogs before compiling a run. **Auto** is a Neo convenience choice; it is not sent literally to `SeedVR2LoadDiTModel` or `SeedVR2LoadVAEModel`.

When Auto is selected, Neo prefers the profile's recommended model when that exact filename exists on the connected backend. For the VAE, the normal recommendation is:

```text
ema_vae_fp16.safetensors
```

If the preferred model is not installed but another valid model is exposed by the live SeedVR2 loader, Neo can use the first live model for Auto. If you explicitly select a model that is no longer present, Neo stops before queueing and asks you to refresh/reconnect rather than silently switching models.

If a newly installed model does not appear immediately, reconnect/refresh the Video backend and retry. Compile/Run also refresh the SeedVR2 model catalog when Neo does not already have both DiT and VAE catalogs.

## Custom target sizing

SeedVR2 uses a **target short edge** while preserving the source aspect ratio.

For a 16:9 landscape video targeting approximately **2560×1440**, use **Target = Custom** and enter `1440` as the short-edge custom value. For a 9:16 vertical video targeting approximately **1440×2560**, the short edge is also `1440`.

The current Custom Width/Height controls feed Neo's SeedVR2 short-edge request rather than forcing both output dimensions. Avoid entering both long-edge and short-edge values when you only want a 1440p/QHD upscale.

## Memory controls

- **Batch Size** affects temporal grouping and VRAM use.
- **Temporal Overlap** helps continuity between batches.
- **Encode Tile / Decode Tile** reduce VAE memory pressure when lowered.
- **Blocks To Swap** trades speed for lower VRAM usage; higher swap values are safer on smaller GPUs.
- **Output FPS Override** should normally remain blank to preserve the source timing.

If a run is blocked before queueing, read Neo's inline message first. Missing SeedVR2 loader/model catalogs are backend-readiness problems; CUDA out-of-memory errors require lower-memory settings instead.
