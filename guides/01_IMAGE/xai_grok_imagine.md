---
guide_id: image.xai_grok_imagine
title: xAI Grok Imagine Image Backend
surface: image
scope: built_in
applies_to:
  - image_workspace
  - image
  - grok
  - xai_grok
  - grok_imagine
  - cloud_api
  - image_edit
tags:
  - image
  - backend
  - grok
  - xai
  - imagine
  - api
  - cloud
priority: 97
version: 3
updated: 2026-07-11
---

# xAI Grok Imagine Image Backend

Neo Studio includes a seeded **Grok Imagine** profile for the existing Image workspace.

## Core rule

Do not create a separate Grok Image workspace. Select the existing **Grok Imagine** profile under **Admin → Backends → Image**:

- Generate uses the existing text-to-image workspace.
- Img2Img uses the existing image-edit workspace.
- The same prompt, upload, preview, progress, history, and replay systems are reused.

Users normally only need to add their xAI API key, save the seeded profile, and test the connection.

## Profile contract

- profile ID: `image.xai_grok_imagine`
- provider ID: `xai_grok`
- surface: `image`
- connection type: `cloud_api`
- base URL: `https://api.x.ai/v1`
- environment variable: `XAI_API_KEY`
- health check path: `/models`
- default model: `grok-imagine-image`
- available models: `grok-imagine-image`, `grok-imagine-image-quality`

## Supported workspace modes

- Text-to-image through Generate
- Single-image edit through Img2Img
- Multi-image edit through the existing Image 1–3 lanes when the active profile advertises support

Image edit reuses the current source-image state. Neo does not create a separate `grokEditDraft` or duplicate uploader.

## Provider-aware visibility

When Grok is active, Neo shows provider-supported image fields such as model, prompt, aspect ratio, resolution, output count, and source images for edit.

Neo hides and excludes unsupported Comfy/checkpoint controls including negative prompt, seed, steps, CFG, sampler, scheduler, denoise, checkpoint/components, VAE, encoders, LoRA, ControlNet, IP Adapter, CFG Fix, mask inpaint, and outpaint controls.

Hidden local values remain in the user's Comfy draft and return when the user switches profiles. They are not sent to xAI because the Grok request uses a strict allowlist.

## Setup

1. Open **Admin → Backends → Image**.
2. Select **Grok Imagine**.
3. Configure `XAI_API_KEY` through the environment or Neo's local manual-secret storage.
4. Save the profile.
5. Test the connection.
6. Set it as the Image default only when desired.
7. Open Generate or Img2Img in the existing Image workspace.

## Troubleshooting

- Confirm the profile is under Image and uses `https://api.x.ai/v1`.
- Confirm the API key is valid and the selected model is available.
- For image edit, confirm Image 1 is supplied.
- API keys must not be committed to the repository; runtime/user secrets belong under `neo_data` or the environment.
- Use live profile status before claiming the backend is connected.
