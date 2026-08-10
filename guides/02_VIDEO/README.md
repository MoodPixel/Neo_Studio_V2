---
guide_id: video.index
title: Video Guides — Current Architecture
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video_generation
  - video_extensions
  - video_finish
  - video_results
priority: 100
version: 2
updated: 2026-08-09
---

# Video Guides — Current Architecture

This folder describes the **current Video product contract**. Historical implementation phases live under `neo_system_records/06_SURFACES/video/`; they are evidence of how the system evolved, not alternative runtime specifications.

## Current architecture at a glance

```txt
Video provider/profile
  -> workspace: Generation | Assets | Reference | Finish | Results
       left rail = active workspace content
       right rail = persistent Prompt + Preview + Parameters
  -> local generation: canonical Video route matrix
       family -> loader -> generation type -> route -> parameter profile
  -> shared surface-aware extension compatibility
       built-in -> first-class workspace tool
       external -> External Extensions
  -> Neo-owned output ledger
       Results -> Video Output Inspector -> lineage/replay
```

**Workspace** is a navigation label only. `generation` is the default Video workspace application and remains separate from the selected generation type.

## Authority map

| Concern | Current authority |
|---|---|
| Video workspace apps and mount slots | `neo_app/surfaces/surface_manifest.json` |
| Local WAN/LTX family/loader/mode routes and route defaults | `neo_app/video/route_matrix.py` |
| Active route + VRAM parameter contract | `GET /api/video/parameter-profile` |
| Surface-aware extension compatibility | `neo_app/extensions/runtime.py` + `registry.py` + extension manifests |
| Video workspace composition | `neo_app/static/js/neo.js` |
| Video surface diagnostics/endpoints | `neo_app/static/js/surfaces/video.js` |
| Persisted Video results | existing Video output-record ledger |
| Inspector view model | `neo_app/video/output_inspector.py` |
| Inspector API | `GET /api/video/results/{result_id}/inspector` |

The browser must not recreate local Video route/default tables, and Video extension routing must not read Image route state.

## Guides

1. [`video_tab_overview.md`](video_tab_overview.md) — product behavior, provider boundary, storage ownership, and navigation.
2. [`video_model_families.md`](video_model_families.md) — canonical local routing and parameter ownership.
3. [`video_generation_extensions.md`](video_generation_extensions.md) — Video-owned extension context, route scopes, Built-in vs External rules.
4. [`video_workspace_layout.md`](video_workspace_layout.md) — ownership of Generation, Assets, Reference, Finish, and Results bodies.
5. [`video_output_inspector.md`](video_output_inspector.md) — saved-output inspection, lineage, and safe replay.
6. [`xai_grok_imagine_video.md`](xai_grok_imagine_video.md) — Grok Imagine Video provider-specific behavior.

## Non-negotiable regression locks

- Never restore `Workspace` as a selectable Video subtab or `video.workspace.*` mount namespace.
- Never maintain a second frontend family/loader/generation route matrix.
- `enabled` and `experimental` local routes are runnable; `planned` routes are non-runnable.
- Image `generations` and Video `generation` remain different canonical workspace ids.
- Finish/Results workspace-scoped extensions are not gated by the active WAN/LTX family unless their own manifest says so.
- Every built-in Video extension mount slot must be declared by the Video surface manifest.
- Workspace switching replaces only the Video left rail; the right rail remains Prompt + Preview + Parameters across all five workspaces.
- Route Status and route-specific tools remain workspace-owned on the left; the persistent right rail does not absorb workspace tools.
- Results keeps history + Output Inspector in the left rail while the right rail remains the live generation recipe.
- Replay stages a validated recipe only. It never auto-runs and never silently substitutes an unknown/retired route.
- Inspector extension reporting comes from persisted result metadata, not current installation state.

For architecture history and supersession notes, start with `neo_system_records/06_SURFACES/video/README.md`.
