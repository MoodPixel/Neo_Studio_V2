---
guide_id: global.backend_connect_dropzone_recovery_bcr1
title: Backend Connect and Image Dropzone Recovery — BCR-1
surface: global
scope: built_in
applies_to:
  - admin
  - backends
  - image
  - img2img
  - inpaint
  - outpaint
  - comfyui
tags:
  - backend
  - comfyui
  - connection
  - performance
  - drag and drop
  - scene director
  - recovery
priority: 118
version: 1
updated: 2026-08-05
---

# Backend Connect and Image Dropzone Recovery — BCR-1

Use this guide when connecting a ComfyUI Image profile takes unusually long and source-image drag/drop stops responding after the connection completes.

## Root cause

Three independent lifecycle costs amplified one another:

1. A Comfy profile probe fetched the complete `/object_info` payload once for model discovery and again for capability discovery.
2. The 28-route LanPaint capability matrix rebuilt the complete family-adapter registry and family-expansion registry inside every route evaluation. The Connect response then enriched the profile through a second live probe.
3. Backend connection launches several Image capability/catalog refreshes. Each full Image render could start another four-request Scene Director library hydration because no loading or in-flight state existed. Every completion rendered again. Source dropzones used listeners attached directly to DOM nodes, so the repeated renders continuously replaced the nodes and their listeners.

The upload endpoint and image file validation were not the failure owners. The visible dropzones became lifecycle-orphaned while the Image DOM was being replaced.

## Recovery behavior

BCR-1 establishes these boundaries:

- Scene Director library hydration is single-flight. One unresolved hydration owns exactly four library requests, and one completed attempt—successful or failed—prevents automatic retry on every render. Manual Refresh remains available.
- Image source/reference drop handling is delegated once at the document boundary before panel rendering. Img2Img, Inpaint, Outpaint, reference lanes, and stitch lanes survive DOM replacement without per-node rebinding.
- A Comfy Connect/Test probe fetches one `/object_info` snapshot and reuses it for model catalogs and backend capabilities.
- The freshly probed runtime is reused while shaping the button response; it is not probed again.
- LanPaint adapter and expansion registries are built once per capability discovery and shared across the complete route matrix.
- Upscaler folder/filesystem fallbacks run only when `/object_info` has no upscaler choices.

## Install

Merge the BCR-1 patch folders into the Neo Studio project root and overwrite the listed files. Do not place the patch inside an additional nested project folder. Restart Neo and perform a hard refresh so the `hotfix_bcr1=backend_connect_dropzone_recovery_20260805` JavaScript revision is loaded.

## Verification

1. Start ComfyUI and Neo Studio.
2. Open **Admin → Backends → Image**, select the intended Comfy profile, and click **Connect**.
3. Open Image and switch between Img2Img, Inpaint, and Outpaint.
4. Drop a PNG/JPG/WEBP/BMP source onto each source area, then change mode or reconnect and repeat.
5. Open Scene Director and confirm its library content loads once. Manual **Refresh Libraries** should still perform a deliberate refresh.
6. Confirm LanPaint capability diagnostics and stale-snapshot remediation still appear for the selected route.
