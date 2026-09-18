---
guide_id: video.lora_stack_ui
title: Video LoRA Stack UI
surface: video
scope: built_in
applies_to:
  - video_assets
  - video_generation
  - minimax_h3
  - ltx23
  - wan22
  - comfyui
tags:
  - video
  - lora
  - assets
  - turbo
  - lightx2v
  - ui
priority: 86
version: 2
updated: 2026-09-18
---

# Video LoRA Stack UI

## Phase 7 modern interface

The modern Video Assets panel keeps the established Phase-10 payload and later Phase-20 compatibility boundary intact. “Phase 7” here is a UI modernization revision, not a rollback of the Video LoRA runtime phases.

The panel now provides:

- a compact route/capability header and explicit master switch;
- active, standard, speed, and stack-capacity counters;
- a collapsible exact-route diagnostic summary;
- live catalog search with quoted/multiple filename terms;
- provider-relative folder and nested-folder filtering;
- a selected-LoRA preview with advisory Standard or Speed role;
- modern ordered row cards with strength presets, duplicate, reorder, disable, and remove actions;
- a confirmed Clear Stack action and responsive narrow-screen layout.

Exact live catalog filenames remain authoritative. Folder names and search terms only help discovery. Speed detection remains advisory, runtime compatibility still fails closed, and the browser never chooses workflow node IDs.

## Phase 8 release and migration

The release audit verifies that the modern panel still writes the canonical `video.lora_stack` payload and retains the one-way retired-field reader. Old browser drafts are migrated only when loaded; the audit command never edits browser storage. Legacy fields are not written back, compiler route support is not widened, and a passing deterministic report is not presented as physical GPU proof.

Run `python scripts/audit_lora_release_phase8.py` from the repository root. See `guides/00_GLOBAL/lora_release_phase8.md` for backup, preview, rollback, and acceptance steps.

Phase 10 makes `video.lora_stack` a first-class tool in **Video → Assets**. It does not change compiler patch locations or widen runtime route support. The UI writes the same universal Video LoRA payload already consumed by the H3, LTX and WAN runtime integrations.

## Product flow

```text
Video route + selected local Video profile
        ↓
Assets → Video LoRA Stack
        ↓
GET /api/video/lora-catalog
        ↓
live ComfyUI /object_info
        ↓
LoraLoaderModelOnly signature + exact file catalog
        ↓
ordered user stack
        ↓
extensions["video.lora_stack"]
        ↓
existing compiler-owned runtime
```

The browser does not decide graph node IDs. It owns only user intent: ordered rows, strength, role, target and enabled state.

## Live catalog

Endpoint:

```text
GET /api/video/lora-catalog
```

The endpoint is selected-profile and exact-route aware. It reports:

- resolved route id;
- support-matrix row;
- `LoraLoaderModelOnly` availability and required-input signature;
- exact live `lora_name` catalog;
- advisory speed/Turbo candidates;
- standard/speed capability;
- allowed targets;
- fail-closed reasons.

A valid ModelOnly signature requires:

```text
model
lora_name
strength_model
```

Generic `LoraLoader` is not substituted.

## Stack rows

Each row exposes:

```text
enabled
LoRA file
strength_model
role = standard | speed
target = all | high | low (only when route supports it)
ordering
```

The stack remains capped at 12 rows.

### Manual selection

Speed classification is advisory only. Any exact file returned by the live `LoraLoaderModelOnly` catalog may be selected manually. A filename does not need a Turbo/LightX2V token to be usable as a standard LoRA.

## MiniMax H3

All validated H3 UNET routes show standard and Speed/Turbo roles with `target=all`:

- Txt2Vid
- Img2Vid
- First/Last
- Reference-to-Video
- Vid2Vid

H3 Turbo is no longer a separate normal-user parameter control. It is a `role=speed` row in the common Video LoRA Stack. This fixes the original Img2Vid product problem without an Img2Vid-specific UI path.

Spectrum and BlockCache remain separate H3 acceleration controls. Activating a speed LoRA does not silently rewrite user steps, sampler or scheduler.

H3 GGUF LoRA remains gated.

## LTX 2.3

LTX UNET Txt2Vid and Img2Vid expose standard LoRAs only, `target=all`.

The UI does not expose speed/Turbo as an enabled role for LTX and does not promote GGUF or extended LTX routes.

## WAN 2.2

WAN UNET Txt2Vid and Img2Vid expose standard LoRA + `target=all` only.

WAN dual-noise GGUF Img2Vid exposes:

```text
role: standard | speed
target: all | high | low
```

`high` and `low` are semantic compiler branches. The UI never knows or stores numeric workflow node IDs.

WAN Rapid AIO and native-workflow LoRA paths remain gated.

## Legacy migration banner

When explicit old H3/WAN LoRA settings are detected and a user opens the stack, Phase 10 offers **Move into Video LoRA Stack**.

Explicit mappings include:

```text
H3 Turbo -> speed/all
WAN Normal Both -> standard/all
WAN Normal High -> standard/high
WAN Normal Low -> standard/low
WAN LightX2V high file -> speed/high
WAN LightX2V low file -> speed/low
```

After explicit migration, legacy activation flags are disabled so the universal stack becomes the normal UI write path. Phase-9 backend read compatibility remains installed for old saved workflows.

If old state requests auto-discovery but contains no explicit file, the UI does not invent a filename. The user is asked to choose a live catalog item.

## Canonical request block

When enabled with active rows, Video generation includes:

```json
{
  "extensions": {
    "video.lora_stack": {
      "enabled": true,
      "version": 1,
      "inputs": {},
      "params": {
        "loras": []
      },
      "assets": {},
      "metadata": {
        "source": "video.assets.lora_stack",
        "ui_phase": "10"
      }
    }
  }
}
```

Disabled or empty stacks do not emit an active block, preserving the strict graph no-op rule.

## Legacy controls in Parameters

The old normal-user fields are hidden from the normal Parameters surface:

- WAN `enable_video_lora`, Normal LoRA and LightX2V branch fields;
- H3 `h3_turbo_enabled`, `h3_turbo_lora`, `h3_turbo_strength`.

The fields remain readable by Phase-9 compatibility code. They are not deleted from backend schemas in Phase 10.

## Validation

Run:

```bash
python -m neo_app.video.video_lora_ui_regression
python -m unittest tests.test_video_lora_stack_ui_phase10 -v
```

Phase-10 local gate: **33 / 33**.

The unchanged runtime guards remain:

```text
H3       43 / 43
LTX      17 / 17
WAN      30 / 30
Phase 9  21 / 21
Phase 10 33 / 33
-----------------
Total   144 / 144
```

The Phase-10 pack is locally verified against the reconstructed developer snapshot. Its new GitHub Actions workflow is included for branch/PR validation after the user chooses to apply the pack.
