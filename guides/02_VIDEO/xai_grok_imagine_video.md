---
guide_id: video.xai_grok_imagine
title: xAI Grok Imagine Video Backend
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video
  - grok
  - xai_grok
  - grok_imagine_video
  - cloud_api
tags:
  - video
  - backend
  - grok
  - xai
  - cloud
  - text-to-video
  - image-to-video
priority: 97
version: 1
updated: 2026-07-11
---

# xAI Grok Imagine Video Backend

Neo Studio includes a seeded **Grok Imagine Video** profile for the existing Video workspace.

## Core rule

Do not create a separate Grok Video tab or workspace. Select the existing Video profile and use the normal Video workspace:

- **Text to Video** uses the existing `txt2vid` mode.
- **Image to Video** uses the existing `img2vid` mode and source-image uploader.
- Progress, previews, output folders, and history use Neo's existing Video systems.

## Profile contract

- profile ID: `video.xai_grok_imagine`
- provider ID: `xai_grok`
- surface: `video`
- connection type: `cloud_api`
- base URL: `https://api.x.ai/v1`
- environment variable: `XAI_API_KEY`
- linked credential profile: `image.xai_grok_imagine`
- models: `grok-imagine-video`, `grok-imagine-video-1.5`

The Video profile can reuse a manually saved key from the Image Grok profile or the shared environment variable. Neo does not copy the raw key into profile configuration.

## Workspace behavior

When Grok Video is selected, Neo shows only provider-relevant controls:

- workflow mode;
- model;
- positive prompt;
- source image for image-to-video;
- duration;
- aspect ratio;
- resolution;
- Generate and Refresh Results;
- provider progress and saved outputs.

Neo hides Comfy-only controls such as model family, loader, Compile, Comfy Probe, checkpoint/UNet, encoders, VAE, sampler, scheduler, guidance, steps, frame count, FPS, VRAM, decode, custom-node, and websocket controls.

Switching back to a Comfy profile restores the user's existing local Video draft rather than resetting it.

## Job lifecycle

Video generation is asynchronous. Neo submits the request, stores the returned external request ID in its durable generation job registry, polls xAI server-side, downloads the completed MP4 into Neo's Video output directory, and writes the normal Video result record.

The browser never receives the xAI API key and does not poll xAI directly. Image-to-video sends the source using the Video API JSON shape `image: {"url": ...}`; it does not reuse Image Edit's `type: "image_url"` field.

## Initial supported modes

- Text to Video
- Image to Video

Video editing, extension, and advanced reference modes are not enabled by P3.

## Setup

1. Open **Admin → Backends → Image** and configure/test the existing **Grok Imagine** profile with `XAI_API_KEY` or a locally saved manual key.
2. Open **Admin → Backends → Video**.
3. Select **Grok Imagine Video**.
4. Save and test the profile.
5. Set it as the Video default only when desired.
6. Open the Video workspace and choose Text to Video or Image to Video.

## Troubleshooting

- Confirm the xAI key is valid and the Image Grok profile is configured when using linked manual credentials.
- Confirm the active profile is `video.xai_grok_imagine`.
- Confirm the selected mode/model/resolution combination is supported.
- Completed provider URLs are temporary; Neo must be able to download and persist the MP4.
- Runtime job state belongs under `neo_data/runtime/jobs/video/`, not in repository source folders.
