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
version: 9
updated: 2026-08-31
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
  -> route-aware Video LoRA stack
       portable rows -> support matrix -> compiler patch profile -> safe model patch
       MiniMax H3 UNET -> standard + speed/Turbo
       LTX 2.3 UNET primary -> standard only
  -> Neo-owned output ledger
       Results -> Video Output Inspector -> lineage/replay
```

**Workspace** is a navigation label only. `generation` is the default Video workspace application and remains separate from the selected generation type.

## Authority map

| Concern | Current authority |
|---|---|
| Video workspace apps and mount slots | `neo_app/surfaces/surface_manifest.json` |
| Local WAN/LTX/MiniMax H3 family/loader/mode routes and route defaults | `neo_app/video/route_matrix.py` |
| Active route + VRAM parameter contract | `GET /api/video/parameter-profile` |
| Cloud Video model/resolution compatibility | active backend profile `model.capabilities_by_model` |
| Surface-aware extension compatibility | `neo_app/extensions/runtime.py` + `registry.py` + extension manifests |
| Universal Video LoRA payload | `neo_extensions/built_in/video.lora_stack/backend/payload_schema.py` |
| Video LoRA exact-route support | `neo_extensions/built_in/video.lora_stack/backend/support_matrix.py` |
| Compiler-owned Video LoRA anchor schema | `neo_app/video/lora_patch_profiles.py` |
| MiniMax H3 Video LoRA runtime | `neo_app/video/video_lora_runtime.py` + `minimax_h3_lora_integration.py` |
| MiniMax H3 LoRA regression gate | `python -m neo_app.video.minimax_h3_lora_regression` |
| LTX 2.3 primary UNET Video LoRA runtime | `neo_app/video/ltx_lora_integration.py` |
| LTX 2.3 Video LoRA regression gate | `python -m neo_app.video.ltx_lora_regression` |
| Video workspace composition | `neo_app/static/js/neo.js` |
| Video surface diagnostics/endpoints | `neo_app/static/js/surfaces/video.js` |
| Persisted Video results | existing Video output-record ledger |
| Inspector view model | `neo_app/video/output_inspector.py` |
| Inspector API | `GET /api/video/results/{result_id}/inspector` |

The browser must not recreate local Video route/default tables, and Video extension routing must not read Image route state. Cloud Video model/resolution choices must likewise come from backend-profile capability metadata rather than provider-specific frontend conditionals.

## Guides

1. [`video_tab_overview.md`](video_tab_overview.md) — product behavior, provider boundary, storage ownership, and navigation.
2. [`video_model_families.md`](video_model_families.md) — canonical local routing and parameter ownership.
3. [`video_generation_extensions.md`](video_generation_extensions.md) — Video-owned extension context, route scopes, Built-in vs External rules.
4. [`video_lora_stack.md`](video_lora_stack.md) — universal Video LoRA payload, exact-route support, compiler-owned anchors, MiniMax H3 standard/Turbo integration, and current fail-closed boundaries.
5. [`minimax_h3_lora_regression.md`](minimax_h3_lora_regression.md) — CI-verified 43-case regression gate for all five MiniMax H3 UNET modes and the Img2Vid Turbo migration path.
6. [`ltx_lora_runtime.md`](ltx_lora_runtime.md) — Phase-7 LTX 2.3 UNET Txt2Vid/Img2Vid standard-LoRA runtime, compiler anchor, fail-closed boundaries, and CI-verified 17-case gate.
7. [`video_workspace_layout.md`](video_workspace_layout.md) — ownership of Generation, Assets, Reference, Finish, and Results bodies.
8. [`video_output_inspector.md`](video_output_inspector.md) — saved-output inspection, lineage, and safe replay.
9. [`video_reference_inputs.md`](video_reference_inputs.md) — shared provider-aware reference images/video/audio and route limits.
10. [`minimax_h3_local_support.md`](minimax_h3_local_support.md) — using MiniMax H3 native audio-video, separate Video/Audio VAE selection, keyframes, Ref2VA references, Ref2VA-backed Video Editing, and speed controls.
11. [`xai_grok_imagine_video.md`](xai_grok_imagine_video.md) — using Grok Text/Image/Reference generation, Video Editing, and Video Extension.
12. [`seedvr2_upscale.md`](seedvr2_upscale.md) — using SeedVR2 Finish Upscale, model selection, custom sizing, and memory controls.
