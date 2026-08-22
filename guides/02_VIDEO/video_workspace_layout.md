---
guide_id: video.workspace_layout
title: Video Workspace Layout
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video_generation
  - video_assets
  - video_reference
  - video_finish
  - video_results
  - extensions
tags:
  - video
  - ux
  - workspace
  - built in
  - external extensions
  - results
priority: 78
version: 3
updated: 2026-08-09
---

# Video Workspace Layout

Video uses one surface with five selectable workspace applications. **Workspace** itself is a presentation label, not an application.

```txt
Workspace
  Generation | Assets | Reference | Finish | Results
```

The command strip owns provider/family/loader/generation-type selection and the primary Compile/Generate/Probe/Refresh actions.

## Persistent two-rail shell

The current Video UI uses a stable two-rail layout on desktop:

```txt
Left: active workspace rail            Right: persistent generation rail

Generation / Assets / Reference /      Prompt + Preview
Finish / Results content only          Parameters
```

Switching Video workspace applications replaces **only the left rail**. The right rail remains dedicated to the live Prompt, Preview, and Parameters for the selected Video generation route.

This is intentional. Users can inspect Assets, configure References, run Finish tools, or inspect Results without losing sight of the current generation recipe.

On narrow screens the two rails may stack responsively, but ownership remains the same: active-workspace content first, Prompt/Preview/Parameters as the shared generation rail.

## Generation — left rail

Generation owns the route-specific controls that are not part of the persistent generation rail:

```txt
Left
  backend/readiness
  VRAM Advisor
  built-in generation tools
  External Extensions
  route-required Source / Reference
  Prompt / Motion Schedule when selected
  Audio-Video controls when selected
  expert diagnostics

Right — persistent
  Prompt + Preview
  Parameters
```

Source/Reference appears only when the active generation type consumes it. Prompt/Motion Schedule and Audio-Video panels appear only for their matching generation modes.

## Assets — left rail

Assets is input-focused:

```txt
Left
  active asset inventory
  compatible Assets tools/extensions
  active source inputs

Right — persistent
  Prompt + Preview
  Parameters
```

## Reference — left rail

Reference is conditioning-focused:

```txt
Left
  reference route context
  compatible Reference tools/extensions
  image/video/depth/motion reference input for the active route

Right — persistent
  Prompt + Preview
  Parameters
```

When the selected generation type has no reference input, show an explicit empty state instead of a fake/planned control surface.

## Finish — left rail

Finish is independent from the active WAN/LTX generation-family choice. Each finish tool owns its own source/readiness contract.

```txt
Left
  finish presets
  External Extensions
  expert external-node readiness (Expert mode)
  Frame Interpolation
  SeedVR2 Upscale
  Repair / Cleanup
  finish notes

Right — persistent
  Prompt + Preview
  Parameters
```

The three native finish tools are first-class built-ins. Their direct source pickers and child-output safety rules remain intact. The persistent right rail is visible for generation context, but Finish tools themselves remain isolated to the left rail.

## Results — left rail

Results owns both history and inspection:

```txt
Left
  Generation History / Output Recorder
  result-scoped External Extensions
  Video Output Inspector
    playback
    Generation Setup
    Prompt Recipe
    Parameters
    Sources
    Executed Extensions
    Lineage
    Replay
    Expert Metadata (Expert mode)

Right — persistent
  Prompt + Preview
  Parameters
```

The Inspector reads `GET /api/video/results/{result_id}/inspector` and keeps the selected saved output separate from the live generation recipe shown in the right rail. Finish children remain the selected/playable result while replay resolves to the nearest valid generation ancestor when one exists. Loading a recipe stages Generation only; it never auto-runs.

See `guides/02_VIDEO/video_output_inspector.md` for the normalized result, lineage, and replay contract.

## Built-in vs External

Both extension origins use the shared surface-aware compatibility contract.

```txt
Video route/workspace context
  -> compatibility
  -> origin partition
       built_in -> first-class/native tool
       external -> External Extensions section
```

Do not mix both origins back into one generic extension stack. Do not add per-panel family/loader compatibility tables; the shared extension runtime remains authoritative.

## 2026-08-09 — Workspace card flattening

- Video workspaces no longer wrap left-rail content inside extra shell cards like **Generation Tools**, **Assets**, **Reference**, or **Finish Setup**.
- The active workspace now renders its real cards directly in the left rail.
- Prompt, Preview, and Parameters remain persistent on the right rail.


## 2026-08-09 — Results + Finish visual polish

The Video left rail uses the modern Neo card language consistently. **Results & Save Details**, **Replay Storage Manager**, and **Motion Timing + Finish Presets** use Video-specific dark/glass form controls, compact context chips, stronger internal grouping, and modern status/action rows. This is presentation-only; routing, save behavior, replay behavior, and finish execution are unchanged.

## 2026-08-22 — Generation declutter (step 1)

- The **Generation** left rail no longer mounts the **Start Here / Video quick start** helper card.
- **Route Status** is removed from the Generation left rail. Route readiness remains represented in the command strip chips and runtime progress/status handling.
- **Output Storage** is removed from the Generation left rail. Output-folder checks remain an implementation helper, not a primary Generation card.
- **Video · Output Recorder** no longer mounts in Generation. It remains owned by **Results**.
