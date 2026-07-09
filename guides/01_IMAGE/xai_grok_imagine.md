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
version: 2
updated: 2026-07-09
---

# xAI Grok Imagine Image Backend

Neo Studio V2 includes a seeded **Grok Imagine** profile for the Image workspace.

Use this guide when the user asks how to configure Grok/xAI for image generation or image edit inside Neo.

## Core rule

The Grok Imagine backend profile is already created in Neo. Users normally should **not** create a new profile. They should select the existing **Grok Imagine** profile under **Admin → Backends → Image**, add their API key, save, and test.

Only recommend creating a duplicate profile if the user wants a separate experimental setup.

## Neo profile contract

Current seeded profile details:

- profile ID: `image.xai_grok_imagine`
- display name: `Grok Imagine`
- provider ID: `xai_grok`
- surface: `image`
- connection type: `cloud_api`
- base URL: `https://api.x.ai/v1`
- API key environment variable: `XAI_API_KEY`
- health check path: `/models`
- default model: `grok-imagine-image`
- available models: `grok-imagine-image`, `grok-imagine-image-quality`

## Supported inside Neo's Image integration

The current Neo profile supports:

- text-to-image;
- image edit;
- multi-image edit where supported by the selected model/profile.

The current Neo profile does not expose classic SD/Comfy controls such as:

- negative prompt;
- seed;
- steps;
- CFG;
- sampler;
- scheduler;
- LoRA;
- ControlNet;
- IP Adapter;
- mask inpaint / outpaint controls.

If the user asks for these controls with Grok Imagine, explain that they are Comfy/checkpoint-style controls and may not be available through the Grok Image API profile.

## Setup steps

1. Open **Admin → Backends → Image**.
2. Select the existing **Grok Imagine** profile.
3. Confirm the base URL is already set to `https://api.x.ai/v1`.
4. Set the API key source:
   - environment variable: `XAI_API_KEY`, or
   - manual local key stored under Neo runtime data.
5. Confirm health check path is `/models`.
6. Choose or confirm the default image model.
7. Click **Save Profile**.
8. Click **Test Connection**.
9. Click **Set Default** only if the Image workspace should use Grok Imagine by default.

## Troubleshooting

If Grok Imagine does not connect:

- confirm the API key is valid;
- confirm the profile is under **Image**, not Text or Video;
- confirm the base URL is `https://api.x.ai/v1`;
- confirm the health check path is `/models`;
- confirm the selected model is available on the profile;
- clear and re-add the manual local key if the saved key is stale.

## Assistant behavior

When answering user questions about this backend:

- Say the profile is already seeded/pre-created in Neo.
- Tell the user they usually only need to add the API key and test the connection.
- Say it is a cloud API Image profile, not a local Comfy profile.
- Mention that API keys should never be committed to the repo.
- Mention that runtime/user secrets belong under `neo_data/` or the user environment.
- Use live Image snapshot/profile status when available before claiming the profile is connected.
- If `XAI_API_KEY` is missing or the health check fails, direct the user to **Admin → Backends → Image**.
