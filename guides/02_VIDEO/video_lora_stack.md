---
guide_id: video.lora_stack
title: Video LoRA Stack
surface: video
scope: built_in
applies_to:
  - video_generation
  - video_assets
  - comfyui
  - minimax_h3
  - wan22
  - ltx23
tags:
  - video
  - lora
  - minimax h3
  - ltx
  - wan
  - turbo
  - lightx2v
  - workflow patching
  - migration
priority: 84
version: 5
updated: 2026-09-01
---

# Video LoRA Stack

`video.lora_stack` is Neo's built-in route-aware LoRA system for local Video generation. It uses one portable LoRA payload while the selected Video compiler remains responsible for declaring where model patches are safe.

## Core contract

```text
Video route
  + universal LoRA payload
  + exact support-matrix row
  + compiler-owned patch profile
        ↓
validated model anchor(s)
        ↓
Video LoRA chain
        ↓
existing compiler graph
```

The extension must not hardcode workflow node IDs. A compiler profile declares the exact model reference and exact consumer input(s) that may be rewired.

Phase 9 adds a second invariant around saved legacy state:

```text
legacy H3/WAN fields
        ↓ read-compatible intent only
compatibility bridge
        ↓
universal video.lora_stack
        ↓ only writeback + graph authority
compiler-owned patch profile
```

## Universal payload

Standard and speed rows use the same schema:

```json
{
  "uid": "video_lora_1",
  "enabled": true,
  "name": "character.safetensors",
  "strength_model": 0.8,
  "role": "standard",
  "target": "all"
}
```

```json
{
  "uid": "video_lora_2",
  "enabled": true,
  "name": "MiniMax-LightX2V-4steps.safetensors",
  "strength_model": 1.0,
  "role": "speed",
  "target": "all"
}
```

The stack currently allows up to 12 rows.

### Roles

- `standard` — normal character/style/motion/model LoRA behavior.
- `speed` — Turbo, LightX2V, Lightning, distilled, or another acceleration LoRA.

Role affects ordering and compatibility. It does not create a separate LoRA engine.

### Targets

- `all` — single-model routes and the combined target on a verified multi-branch route.
- `high` — WAN high-noise branch only when exposed by the compiler.
- `low` — WAN low-noise branch only when exposed by the compiler.

H3, LTX Phase 7, and WAN single-model UNET use `all` only. WAN dual-noise GGUF supports all three targets.

## Exact-route support

Compatibility is fail-closed.

### MiniMax H3

| Route | Standard | Speed/Turbo | Target |
|---|---:|---:|---|
| `minimax_h3.unet.txt2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.img2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.first_last_frame` | Yes | Yes | `all` |
| `minimax_h3.unet.reference_to_video` | Yes | Yes | `all` |
| `minimax_h3.unet.vid2vid` | Yes | Yes | `all` |

H3 GGUF LoRA application remains fail-closed.

### LTX 2.3

| Route | Standard | Speed/Turbo | Target |
|---|---:|---:|---|
| `ltx23.unet.txt2vid` | Yes | No | `all` |
| `ltx23.unet.img2vid` | Yes | No | `all` |

LTX GGUF and extended LTX modes remain fail-closed/provisional for LoRA runtime until their exact topology is separately validated.

### WAN 2.2

| Route | Standard | Speed | Targets |
|---|---:|---:|---|
| `wan22.unet.txt2vid` | Yes | No | `all` |
| `wan22.unet.img2vid` | Yes | No | `all` |
| `wan22.gguf.img2vid_14b_dual_noise` | Yes | Yes | `all`, `high`, `low` |

WAN Rapid AIO and imported native-workflow routes remain blocked for LoRA runtime.

**Phase 9 does not change this support table.** It hardens compatibility and deprecation behavior only.

## Runtime model flows

### MiniMax H3

```text
H3 model loader
  -> standard LoRA(s)
  -> speed/Turbo LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> scheduler / guider / sampler
```

### LTX 2.3

```text
LTX model loader
  -> standard LoRA(s)
  -> LTXVChunkFeedForward
  -> CFGGuider
  -> sampler
```

LTX currently accepts `role=standard`, `target=all`, UNET, and `LoraLoaderModelOnly` only.

### WAN single-model

```text
WAN UNET loader
  -> standard LoRA(s)
  -> ModelSamplingSD3
  -> sampler
```

WAN UNET currently accepts `role=standard` and `target=all` only.

### WAN dual-noise

```text
High model loader
  -> standard LoRA(s)
  -> speed LoRA(s)
  -> optional Sage / TeaCache / low-VRAM patches
  -> high ModelSamplingSD3

Low model loader
  -> standard LoRA(s)
  -> speed LoRA(s)
  -> optional Sage / TeaCache / low-VRAM patches
  -> low ModelSamplingSD3
```

The compiler-owned multi-branch profile exposes distinct `high` and `low` branches. `target=all` maps to both branches. Standard rows are applied before speed rows independently on each branch.

## Empty or disabled stack

No active rows means no LoRA node insertion and no model-consumer rewiring.

CI verifies no-op workflow equivalence for all five H3 UNET modes, LTX UNET Txt2Vid/Img2Vid, WAN UNET Txt2Vid/Img2Vid, and WAN dual-noise GGUF Img2Vid.

Metadata such as `lora_patch_profile`, `video_lora_stack`, and legacy compatibility diagnostics may exist outside the Comfy workflow without violating the no-op rule.

## Phase 9 legacy compatibility boundary

Old saved fields remain readable, but they are no longer valid writeback or graph-authority surfaces.

Compiled hardened H3/WAN metadata declares:

```text
legacy_field_writeback = false
universal_stack_writeback = true
next_save_action = persist_video.lora_stack_only
graph_authority = compiler_owned_universal_video_lora_stack
```

### H3 Turbo compatibility

These fields remain readable:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

Neo converts them into universal rows. If the same file already exists in `video.lora_stack`, the universal row keeps its uid and strength. Legacy Turbo intent may promote it to `speed` and normalize its H3 target to `all`; no duplicate graph path is created.

### WAN compatibility

These fields remain readable:

```text
enable_video_lora
video_lora_mode
video_lora_model
video_lora_strength
video_lora_target
enable_lightx2v
high_noise_lora
low_noise_lora
high_noise_lora_strength
low_noise_lora_strength
```

Legacy target conversion remains:

```text
Both -> all
High -> high
Low  -> low
```

For branch overlap, universal state has precedence over legacy state for:

```text
uid
strength_model
strength_clip
```

Legacy intent may fill an uncovered branch or promote an already-covered matching branch to `speed`.

### Branch-exact WAN speed promotion

Phase 9 fixes an important mixed-state edge case. A universal `target=all` row must not be over-promoted when a legacy speed request applies to only one WAN branch.

Example:

```text
Universal: file A, standard, all, strength 0.72
Legacy:    file A, speed, high, strength 1.00
```

becomes:

```text
file A, speed,    high, strength 0.72
file A, standard, low,  strength 0.72
```

The universal strength wins. The untouched low branch remains standard. Derived branch UIDs are deterministic and collision-safe.

If an exact split would exceed the 12-row stack maximum, migration fails closed rather than dropping or widening state.

## Legacy graph mutation is rejected

Phase 9 verifies every LoRA loader node in hardened H3/WAN workflows after universal patching.

Every `LoraLoaderModelOnly` or generic `LoraLoader` node must be declared by `video_lora_stack.applied`. Any undeclared LoRA node causes compile failure.

Historical WAN graph IDs such as:

```text
129:101
129:102
9001
9002
```

cannot re-enter as a parallel mutation path.

The old `video_lora_adapter.py` remains temporarily for compatibility semantics, but its exposed diagnostic snapshot is sanitized: graph node IDs and source/output model links are removed, the snapshot is marked deprecated/compatibility-only, and its `graph_mutation_authority` is `none`.

See [`video_lora_legacy_compatibility.md`](video_lora_legacy_compatibility.md) for the full deprecation/removal boundary.

## Generate payload preservation

WAN Generate functions rebuild dataclass payloads before calling Compile. `neo_app/video/wan_lora_payload_context.py` preserves the outer user payload until the compiler build hook consumes it, so `extensions.video.lora_stack` survives Generate -> Compile nesting.

## Live catalog validation

Every active H3, LTX, or WAN LoRA runtime requires the selected filename to exist in the live `LoraLoaderModelOnly` catalog exposed by ComfyUI.

The runtime fails before queueing when:

- `LoraLoaderModelOnly` is unavailable;
- only generic `LoraLoader` exists;
- the ModelOnly catalog is empty when rows are requested;
- a selected filename is absent from the live catalog;
- a route receives an unsupported role or target.

Generic `LoraLoader` is not an interchangeable fallback because the validated routes use model-only patch profiles.

## Compiler-owned patch profiles

Schema:

```text
neo.video.lora_patch_profile.v1
```

Single-model profiles declare one model ref and its exact consumers. WAN dual-noise uses `model_only_multi_branch`, distinct high/low refs, and an `all` mapping to both branches.

Before inserting a LoRA node, Neo verifies that declared consumers still point to the compiler-declared model reference. Phase 9 additionally verifies that every resulting LoRA node is declared by the universal runtime.

## Regression gates

### MiniMax H3 — Phase 6

```bash
python -m neo_app.video.minimax_h3_lora_regression
```

```text
43 / 43 passed
```

### LTX 2.3 — Phase 7

```bash
python -m neo_app.video.ltx_lora_regression
```

```text
17 / 17 passed
```

### WAN 2.2 — Phase 8

```bash
python -m neo_app.video.wan_lora_regression
```

```text
30 / 30 passed
```

### Legacy compatibility — Phase 9

```bash
python -m neo_app.video.video_lora_legacy_compat_regression
```

```text
21 / 21 passed
```

The Phase-9 promotion gate reruns all earlier families:

```text
H3       43 / 43
LTX      17 / 17
WAN      30 / 30
Phase 9  21 / 21
-----------------
Total   111 / 111
```

The 21 Phase-9 cases cover branch-exact WAN migration, universal uid/strength precedence, partial branch filling, deterministic ordering, UID collision handling, max-stack failure, H3 duplicate Turbo precedence, deprecation/writeback metadata, sanitized WAN diagnostics, and graph-authority acceptance/rejection.

## Deprecation/removal boundary

Phase 9 deliberately does **not** remove legacy H3/WAN fields or `video_lora_adapter.py`.

Removal is allowed only after:

1. saved-state writeback persists universal `video.lora_stack` state;
2. migration diagnostics show no unresolved legacy-only states;
3. a compatibility release boundary has retained legacy read support before final removal.

So the current contract is:

```text
legacy read support   = yes
legacy writeback      = no
legacy graph authority= no
universal writeback   = yes
universal graph authority = yes
```

## Current boundaries

The Video LoRA system still does **not** claim:

- H3 GGUF LoRA support;
- LTX speed/Turbo LoRA support;
- LTX GGUF LoRA support;
- LTX extended-mode LoRA support;
- WAN UNET speed LoRA support;
- WAN UNET high/low targeting;
- WAN Rapid AIO LoRA support;
- WAN imported native-workflow LoRA support;
- final full Video LoRA Stack UI/library manager;
- permission to delete legacy H3/WAN read compatibility yet.

## Related files

- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`
- `neo_extensions/built_in/video.lora_stack/backend/payload_schema.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json`
- `neo_app/video/lora_patch_profiles.py`
- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `neo_app/video/minimax_h3_lora_regression.py`
- `neo_app/video/ltx_lora_integration.py`
- `neo_app/video/ltx_lora_regression.py`
- `neo_app/video/wan_lora_integration.py`
- `neo_app/video/wan_lora_payload_context.py`
- `neo_app/video/wan_lora_regression.py`
- `neo_app/video/video_lora_legacy_compat.py`
- `neo_app/video/video_lora_legacy_compat_regression.py`
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
- `guides/02_VIDEO/ltx_lora_runtime.md`
- `guides/02_VIDEO/wan_lora_runtime.md`
- `guides/02_VIDEO/video_lora_legacy_compatibility.md`

## Promotion rule

Do not widen Video LoRA compatibility unless the new exact-route implementation preserves every existing gate and adds deterministic fail-closed coverage for any newly promoted topology.

Current baseline:

```text
MiniMax H3: 43 / 43
LTX 2.3:    17 / 17
WAN 2.2:    30 / 30
Phase 9:    21 / 21
Combined:  111 / 111
```
