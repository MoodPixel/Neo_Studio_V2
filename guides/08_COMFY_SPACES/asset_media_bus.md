---
guide_id: comfy_spaces.asset_media_bus
title: Comfy Spaces Asset and Media Bus
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - assets
  - media
  - uploads
  - files
priority: 67
version: 5
updated: 2026-10-07
---

# Comfy Spaces Asset and Media Bus

Neo Studio has a media-neutral asset layer for images, masks, video, audio, and files. The NCS-7 execution runtime now uses this bus when a published Space is prepared and when declared media/file outputs are collected.

The top-level Comfy Spaces runner now uses this same bus for published Space media/file inputs and declared media/file outputs.

## Neo asset references

A Space media/file control is represented internally by a Neo asset reference rather than a computer path:

```json
{
  "ref_type": "neo_asset_ref",
  "asset_id": "asset_<id>",
  "kind": "image"
}
```

Neo stores and validates the bytes itself. The browser sees a Neo asset ID and content URL, not the server's local storage path.

## Graph Add-on assets

Per-run Graph Add-ons reuse this same asset bus instead of inventing a second upload path. For example, the reviewed ControlNet module uploads its control image through Neo's normal Comfy Spaces asset endpoint, then the server resolves the resulting `neo_asset_ref` for the selected Comfy runtime while composing the execution clone.

The add-on request never carries an arbitrary server filesystem path. Switching runtime or removing the add-on clears the runner-side add-on selection; Neo-owned asset lifecycle remains governed by the existing Comfy Spaces asset rules.

Space Variants deliberately do **not** persist these asset references. A Variant may remember that a ControlNet module is part of the reusable configuration plus its non-asset settings, but its control image is selected again for the actual run. The same rule applies to normal Space image/mask/video/audio/file controls.

## Supported kinds

The same bus is used for:

```text
image
mask
video
audio
file
```

This is intentionally not an Image-only subsystem. A Space can accept an image and audio file and return a video without each media type needing a separate transport architecture.

## What Neo validates

Before an uploaded asset becomes available, Neo checks its filename, declared media kind, extension/MIME compatibility, size, and content metadata. Image and mask uploads also receive a basic raster-image signature check.

Neo records the byte size and SHA-256 digest. Before handing an input to ComfyUI, it verifies that the stored bytes still match that record. A modified or missing stored asset fails closed.

## Temporary and durable assets

Temporary assets have an expiry and are eligible for cleanup. Durable assets have no automatic expiry and require explicit forced deletion.

Generated outputs are collected back into the same neutral asset system by NCS-7 instead of returning machine-specific Comfy paths to the Space result.

## ComfyUI handoff

When an NCS-7 run targets a selected ComfyUI runtime, Neo resolves the `neo_asset_ref`, verifies the Neo-owned content, stages it into that selected Comfy runtime through Neo's provider transport, and receives a Comfy-ready input filename.

Neo does not search for another runtime if the selected profile cannot accept the asset.

The resulting filename is passed into the existing Comfy Space binding engine on the cloned workflow. The stored published workflow template remains unchanged.

## Current availability

The asset/media bus is wired into the NCS-7 backend execution path and the top-level NCS-9 runner. **Admin Builder Preview still does not upload files into a running graph**. The public runner uploads selected browser files into Neo-owned assets, passes only `neo_asset_ref` values into execution preparation, and renders collected Neo-owned outputs.

NCS-7 resolves input assets before NCS-4 binding and sends trusted output coordinates back to this bus for Neo-owned collection. NCS-8 tracks the resulting long-running jobs/progress without replacing this asset authority.
