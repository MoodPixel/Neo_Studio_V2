---
guide_id: image.rmbg_batch_video
title: RMBG Batch and Video Segmentation
surface: image
scope: built_in
applies_to:
  - image_workspace
  - finish
  - comfyui
tags:
  - image
  - video
  - rmbg
  - batch
  - segmentation
priority: 100
version: 1
updated: 2026-07-19
---

# RMBG Batch and Video Segmentation — Phase RMBG-7

Phase RMBG-7 adds a collapsible **Batch & Video Segmentation** section to the
end of the existing Image → Finish → Remove Background panel. It is visually
separated from the single-source controls and does not create a separate
surface or expose a local machine path.

The **Segmentation route** selector is route-aware: `Batch images` renders only
the image batch picker, while `Video · frame-wise` renders only the video
picker and its FPS/frame-cap controls. Switching routes clears the staged input
belonging to the other route so an invisible file cannot be queued accidentally.

## Routes

### Batch images

- Accepts one to **32** image uploads.
- Builds one ComfyUI graph with live `ImageBatch` nodes and a live
  batch-capable `SegmentV2`, `Segment_V2`, `Segment_v2`, or `Segment_v1` node.
- Source order is preserved in the Comfy IMAGE batch.
- Foreground and optional alpha-mask PNGs are saved through `SaveImage`.

### Video frame-wise segmentation

- Accepts one MP4, MOV, WEBM, MKV, AVI, or GIF upload.
- Uses the live VideoHelper loader, live batch-capable RMBG segmenter, live
  `MaskToImage`, and live VideoHelper combiner.
- Defaults to 24 FPS and caps processing at 1,200 frames.
- Saves foreground video and, when enabled, a mask video.
- Every loaded frame is segmented independently. Temporal propagation,
  optical-flow tracking, and identity persistence are intentionally not
  claimed by this phase.

## Readiness and failure behavior

Neo reads the active ComfyUI `/object_info` contract before queueing. The route
is blocked when a required node or input is missing; it never switches to a
different segmenter, a different loader, or temporal tracking silently.

The public payload contains portable Comfy input names and bounded settings.
Neo-owned source records may keep server-side storage metadata, but browser
responses and catalogs do not expose personal absolute paths.

The installed ComfyUI-RMBG project documents batch image support for
`Segment_v1` and `Segment_V2`; the current Neo adapter additionally requires
the live node inputs used by the graph. See the upstream project for its node
implementation: <https://github.com/1038lab/ComfyUI-RMBG>.

The extension manifest intentionally leaves `workflow_modes` empty. The
`batch_images` and `video_framewise` names are internal Background Removal
routes, not Image-tab workflow selectors. The Remove Background panel therefore
stays mounted in normal Image workspaces, while the panel and API perform the
live ComfyUI capability gating for each route.

## Limits

| Setting | Limit |
|---|---:|
| Batch images per run | 32 |
| Video frames per run | 1,200 |
| Video FPS | 1–60 |
| Prompt length | 512 characters |
