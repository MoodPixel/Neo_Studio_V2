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
  - forge
  - connection
priority: 85
version: 8
updated: 2026-07-30
---

# Backend Profiles and Connection State

Neo Studio manages backend setup from **Admin → Backends**. Profiles describe the surface, provider, connection, authentication source, model defaults, capabilities, and runtime state.

## Core rule

Use the shipped profile for the required surface whenever possible. Users normally add only machine-specific paths, local launch commands, API credentials, or deliberate custom URLs.

## Surface profiles

- **Text**: Text, Assistant, Roleplay, and Prompt/Captioning backends.
- **Image**: ComfyUI, Forge Neo Admin, and cloud Image profiles such as Grok Imagine.
- **Video**: ComfyUI Video profiles and Grok Imagine Video.
- **Voice** and **Music / Audio**: provider-specific local/cloud profiles.
- **Provider Diagnostics**: read-only profile/default/capability diagnostics.

Typical seeded profiles include:

| Surface | Examples |
|---|---|
| Image | ComfyUI Local, ComfyUI Portable, Forge / Forge Neo, Grok Imagine |
| Video | Video · ComfyUI Local, Video · ComfyUI Portable, Grok Imagine Video |
| Text | KoboldCpp Local |

The active/default profile is user-controlled and is not replaced by seed reconciliation.

## Utility-only commercial profiles

Some profiles belong to a Finish utility rather than the primary generation backend selector. P6.5 adds:

```text
image.remove_bg_background_removal
image.clipdrop_background_removal
```

Both use `profile_role=image_background_removal_backend`. They remain visible under **Admin → Backends → Image** for credential setup and connection diagnostics, but Neo excludes them from the main Image generation backend selector and rejects attempts to set them as the Image default. Select them from **Image → Finish → Remove Background** instead.

Credential options:

```text
remove.bg  → REMOVE_BG_API_KEY or Manual Local API Key
Clipdrop   → CLIPDROP_API_KEY or Manual Local API Key
```

Manual keys are stored under `neo_data/settings/secrets`; raw keys are not written into repository JSON or returned to the browser. remove.bg can test its account endpoint without processing an image. Clipdrop uses a configured-only profile check so **Test Connection** verifies configuration without spending a removal credit. A real image request occurs only after per-run consent inside the Finish utility.

## Grok linked surface profiles

Grok uses the existing Image and Video workspaces through two surface-scoped bindings:

```text
image.xai_grok_imagine
video.xai_grok_imagine
```

They share the same `xai_grok` provider and can share `XAI_API_KEY`. The Video profile links to the Image profile for manual credential resolution, so users do not need to paste the key twice. The raw key is never copied into repository profile JSON.

The profiles are separate only because Neo selects/defaults backends by surface. They do not create duplicate Grok workspaces or provider implementations.

## Forge Neo Admin profile

The seeded `image.forge` profile supports live Admin connection diagnostics and capability discovery when Forge Neo is launched with `--api`. Neo can inspect models, modules, samplers, schedulers, settings, scripts, extensions, and memory without hardcoding the current Forge catalog.

Admin discovery and Image execution remain separate capability checks. A reachable `connected` or `connected_with_warnings` profile is necessary but not sufficient: Neo enables Forge Image routes only when the required generation, progress, and interrupt API endpoints are available and the selected family/loader/mode route is supported. Optional diagnostic failures such as `/sdapi/v1/cmd-flags` do not by themselves block generation.

The current executable Forge core covers SD 1.5 and SDXL checkpoint `txt2img`, `img2img`, and `inpaint`. Forge outpaint, modern model families/loaders, and unmapped script-backed extensions remain gated.

The profile's Bridge mode is explicit: `auto` prefers a compatible Bridge and falls back to standard SDAPI, `standard` never selects the Bridge, and `required` blocks execution unless the Bridge handshake succeeds. The Bridge changes lifecycle ownership only; it does not unlock routes or extensions.

Forge and Neo Studio must use different listening ports. A common local layout is Forge on `7860` and Neo Studio on `7870`; the backend profile always points to Forge's actual URL.

Runtime discovery is sanitized and cached below `neo_data/provider_cache/`. Absolute backend paths and credentials must not cross the public API or enter repository records. See:

```text
guides/07_ADMIN/forge_neo_admin.md
guides/01_IMAGE/forge_neo_image_job_lifecycle.md
```

## Existing installation migration

When Neo introduces a new shipped profile, startup may merge that missing seeded profile into the runtime profile store. It must not overwrite user profiles, edited connection values, defaults, selections, or saved settings.

## Setup pattern

1. Open **Admin → Backends** and select the correct surface.
2. Select the existing seeded profile.
3. Add only the missing path, launcher setting, or API credential.
4. Save and test the profile.
5. Set it as default only when desired.

For Grok:

1. Configure/test **Grok Imagine** under Image.
2. Select/test **Grok Imagine Video** under Video.
3. The Video profile may reuse the Image profile's manual key or `XAI_API_KEY`.

## Connection state

A connected profile requires a current successful runtime status. Use live surface/profile snapshots rather than passive configuration alone. After restarting Neo or a local backend, users may need to test/connect again.

Runtime/user secrets and connection state belong under `neo_data` or the environment, not repository source folders.

## Task-facing live profile handoff

Prompt Studio, single-image Caption Studio, and Batch Captioning use the same
task-facing backend contract. Their routes perform a current live probe of the
selected profile and pass that live profile into the execution service. The
service must not resolve the passive `/api/backend-profiles` catalog again for
the same task, because local profiles with `auto_connect: false` may appear
disconnected in that passive catalog even after a successful Connect/Test.

The batch route performs this preflight before queueing work and reuses the
validated profile for every image. A lost in-memory session gate can be restored
only by a successful current probe; a stale saved `connected` runtime is never
accepted by itself.

For Caption Studio, a reachable text profile still needs effective vision and
captioning support. KoboldCpp should expose a multimodal model and the required
projector/mmproj when the loaded model requires one. A connection failure and a
vision-capability failure are separate diagnostics.

## Forge Phase 5 extension refresh

Forge script-backed extension availability is profile-scoped runtime data. After changing ControlNet, ADetailer, embeddings, or upscalers in Forge, refresh the Forge backend profile before relying on Image extension controls. Neo stores only sanitized names, schema metadata, and slot counts; backend filesystem paths remain server-side.
