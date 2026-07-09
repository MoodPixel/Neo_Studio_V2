---
guide_id: global.backend_profiles
title: Backend Profiles and Connection State
surface: global
scope: built_in
applies_to:
  - assistant
  - image
  - video
  - roleplay
  - prompt_captioning
  - voice
  - admin
  - backends
tags:
  - backend
  - profiles
  - koboldcpp
  - comfyui
  - grok
  - xai
  - connection
priority: 85
version: 3
updated: 2026-07-09
---

# Backend Profiles and Connection State

Neo Studio V2 manages backend setup from **Admin → Backends**.

Neo uses a profile-based backend system. Backend profiles describe how Neo connects to local or cloud providers, including surface, provider, connection URL, launcher fields, auth mode, model defaults, capability flags, and runtime connection state.

## Core rule

Neo Studio already ships with seeded backend profiles for the main surfaces. In normal setup, users should **select and use the existing profile** instead of creating a new one.

Users usually only need to add machine-specific or private values:

- local backend folder/path;
- launcher command or BAT path;
- API key for cloud profiles;
- custom port/base URL only if their backend is not using the default.

Only recommend creating or heavily editing profiles when the user has a custom backend, custom port, separate test setup, or a failed connection that needs troubleshooting.

## Backend surfaces

Neo's Admin Backends area is split into child tabs:

- **Text**: Text, Assistant, Roleplay, and Prompt/Captioning backend profiles.
- **Image**: Image generation backend profiles, including ComfyUI / ComfyUI Portable and cloud image API profiles such as Grok Imagine.
- **Video**: Video workflow profiles, primarily ComfyUI / ComfyUI Portable routes in the current V2 build.
- **Voice**: early/future Voice/TTS backend profiles.
- **Music / Audio**: early/future music, sound effect, and audio backend profiles.
- **Provider Diagnostics**: read-only backend profile/default/capability diagnostics.

## Seeded profile examples

Current seeded profile examples include:

| Surface | Seeded profiles | Typical default |
|---|---|---|
| **Image** | ComfyUI Local, ComfyUI Portable, Grok Imagine | `comfyui_local` |
| **Video** | Video · ComfyUI Local, Video · ComfyUI Portable | `video.comfyui_portable` |
| **Text** | KoboldCpp Local | `local_koboldcpp_text` |
| **Voice** | Chatterbox, Kokoro Preview, Fish Speech HQ, Zonos, Custom TTS Adapter | `voice.chatterbox` |
| **Music / Audio** | ACE-Step, Stable Audio Open, YuE Song HQ, Custom Audio Adapter | `audio.ace_step` |

The exact default may change if the user clicks **Set Default**.

## Local backend profiles

Common local profiles:

- **ComfyUI / ComfyUI Portable** for image/video workflows. Typical base URL: `http://127.0.0.1:8188`.
- **KoboldCPP** for local text/chat workflows. Typical base URL: `http://127.0.0.1:5001`.

Local launcher profiles may include a portable path and launch command. Use the same launcher or BAT file the user normally uses to start the backend manually.

If the user starts ComfyUI/KoboldCPP manually and the default URL is correct, they may only need to click **Test Connection**.

## Cloud API profiles

Cloud profiles do not need a local backend folder. They need an API key source, base URL, health check path, model defaults, and capability flags.

The current V2 seeded cloud Image profile is:

- **Grok Imagine** (`image.xai_grok_imagine`)
- provider ID: `xai_grok`
- surface: `image`
- base URL: `https://api.x.ai/v1`
- environment key: `XAI_API_KEY`
- health check path: `/models`
- default model: `grok-imagine-image`
- available models: `grok-imagine-image`, `grok-imagine-image-quality`

For Grok Imagine, users usually only need to add their API key, save, and test the existing Image profile. Do not tell users to create a new Grok profile unless they need an experimental duplicate.

Neo currently treats Grok Imagine as an **Image workspace backend** for text-to-image, image edit, and multi-image edit where supported by the selected model/profile. Do not describe it as a Neo Text or Video backend unless a later profile explicitly wires those surfaces.

## Recommended setup answer pattern

When a user asks how to set up backends, answer in this order:

1. Tell them to open **Admin → Backends**.
2. Tell them to select the existing profile for the surface.
3. Tell them to add only missing local paths/API keys.
4. Tell them to click **Save Profile** and **Test Connection**.
5. Tell them to click **Set Default** only if they want that profile to be used by default.
6. Tell them to read this guide or the Grok guide only if they need custom setup or troubleshooting.

Avoid implying the user must build backend profiles from scratch.

## Connection state

A connected profile means Neo has a current runtime status that can be used for tasks. If a profile is disconnected, missing an API key, disabled, or unreachable, ask the user to open **Admin → Backends**, test the correct profile, and retry the task.

For Assistant text, the live task-facing profile should be trusted over a passive settings record. For local manual-connect profiles, the user may need to Connect/Test again after restarting Neo.

Surface snapshots may include backend selection, status, model counts, available models, and runtime capability flags. Use those live values when answering current-state questions.
