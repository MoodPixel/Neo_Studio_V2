---
guide_id: video.overview
title: Video Tab Overview
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video
tags:
  - video
  - generation
  - timing
  - source assets
  - backend profiles
priority: 70
version: 2
updated: 2026-07-11
---

# Video Tab Overview

The Video tab is one provider-aware workspace for video generation, source frames, progress, previews, output metadata, and result history.

## Workspace rule

The active Video backend profile controls the route and visible parameters. Neo does not create a separate workspace for every provider.

### ComfyUI profiles

The existing WAN/LTX and local model controls remain available, including model family, loader, components, Compile, backend probe, sampler/scheduler, guidance, frame timing, VRAM, decode, and custom-node controls.

### Grok Imagine Video profile

The same Video workspace switches to cloud controls:

- Text to Video (`txt2vid`)
- Image to Video (`img2vid`)
- model
- positive prompt
- existing source-image uploader for image-to-video
- duration
- aspect ratio
- resolution
- provider progress
- existing Video results and output folders

Comfy-only controls are hidden and excluded from the cloud request. Switching profiles preserves each route's draft state.

## Output ownership

Provider output is persisted into Neo-owned Video storage and registered in the existing result ledger. The gallery and preview should not depend on temporary provider URLs.

Use this guide together with the selected provider guide and live Video snapshot/profile status.
