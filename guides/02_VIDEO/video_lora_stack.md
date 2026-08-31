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
  - turbo
  - lightx2v
  - workflow patching
priority: 84
version: 2
updated: 2026-08-31
---

# Video LoRA Stack

`video.lora_stack` is Neo's built-in route-aware LoRA system for local Video generation. It uses one portable LoRA payload for normal/style LoRAs and acceleration LoRAs, while the selected Video compiler remains responsible for declaring where model patches are safe.

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

The extension must not hardcode workflow node ids. A compiler profile declares the exact model reference and exact consumer input(s) that may be rewired.

## Universal payload

A normal row:

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

A speed/Turbo row uses the same structure:

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
- `speed` — Turbo, LightX2V, Lightning, distilled, or another few-step acceleration LoRA.

Role affects ordering and compatibility. It does not create a separate LoRA engine.

### Targets

- `all` — single-model routes and the combined target on a verified multi-branch route.
- `high` — WAN high-noise branch only when exposed by the compiler.
- `low` — WAN low-noise branch only when exposed by the compiler.

MiniMax H3 uses `all` only.

## Exact-route support

Compatibility is fail-closed.

### MiniMax H3

Runtime support is active for UNET/Diffusion routes:

| Route | Standard | Speed/Turbo | Target |
|---|---:|---:|---|
| `minimax_h3.unet.txt2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.img2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.first_last_frame` | Yes | Yes | `all` |
| `minimax_h3.unet.reference_to_video` | Yes | Yes | `all` |
| `minimax_h3.unet.vid2vid` | Yes | Yes | `all` |

H3 GGUF LoRA application remains fail-closed even when the base GGUF generation route is selectable.

### WAN 2.2

The support matrix recognizes `wan22.gguf.img2vid_14b_dual_noise` as a verified multi-branch LoRA topology with `all/high/low` semantics. The legacy WAN runtime adapter remains in place until a later compiler-anchor migration phase.

### LTX 2.3

UNET Txt2Vid and Img2Vid have verified clean patch anchors for standard LoRAs. Runtime onboarding is intentionally blocked until the MiniMax Phase-6 regression command passes.

## MiniMax H3 model flow

MiniMax H3 is the reference implementation for the universal stack.

```text
H3 model loader
  -> standard LoRA(s)
  -> speed/Turbo LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> scheduler / guider / sampler
```

Node ids are determined from the compiler graph at compile time. The extension does not own them.

### Empty or disabled stack

No active rows means no LoRA node insertion and no model-consumer rewiring.

The Phase-6 invariant is:

```text
empty/disabled Video LoRA stack workflow == original H3 workflow
```

Metadata such as `lora_patch_profile` and `video_lora_stack` may exist outside the Comfy workflow without violating the no-op rule.

### Standard + speed ordering

Standard rows are applied first. `role=speed` rows are applied last so the acceleration/distillation patch sits immediately upstream of H3 native sigma processing.

## Legacy H3 Turbo compatibility

Saved/request fields remain load-compatible:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

Neo converts these fields into the universal stack instead of inserting an independent Turbo node.

### Duplicate behavior

If the same Turbo file is already present in the universal stack:

```text
keep one row
preserve universal row uid + strength
promote role to speed when needed
normalize H3 target to all when needed
suppress the synthetic legacy duplicate
```

This Phase-6 hardening fixes an edge case where deduplication could prevent double application but leave the remaining row classified as `standard`.

## Turbo / LightX2V discovery

Speed recommendation discovery recognizes H3-family aliases:

```text
h3
minimax
minimax_h3
minimax-h3
hailuo
```

and speed tokens:

```text
turbo
lightx2v
lightning
4step / 4steps
8step / 8steps
distilled
```

This allows names such as:

```text
MiniMax-LightX2V-4steps.safetensors
hailuo_lightning_8steps.safetensors
```

The classifier is **recommendation-only**. A normal manually selected LoRA is not rejected because its filename lacks a speed marker.

## Live catalog validation

Manual selection still has one hard requirement: the selected filename must exist in the live `LoraLoaderModelOnly` catalog exposed by ComfyUI.

Phase 6 changed missing-file behavior from warning-only to fail-closed compile behavior. This prevents an invalid LoRA name from reaching the queue and failing later inside ComfyUI.

The compiler rejects:

- selected LoRA absent from the live ModelOnly catalog;
- an empty ModelOnly LoRA catalog when rows are requested;
- a backend exposing only generic `LoraLoader`;
- H3 `high`/`low` targets;
- H3 GGUF LoRA/Turbo injection.

## Loader safety

MiniMax H3 requires:

```text
LoraLoaderModelOnly
```

Generic `LoraLoader` is not an interchangeable fallback because it has a model+CLIP contract.

## Compiler-owned patch profiles

Schema:

```text
neo.video.lora_patch_profile.v1
```

A single-model profile carries:

```text
route_id
compiler
loader_type
loader_node_class
model_ref
model_consumers[]
targets
target_map
validated
```

Before inserting any LoRA node, Neo verifies that the declared consumers still point to the compiler-declared model reference. A stale graph/profile relationship fails closed.

WAN's verified dual-noise profile uses high/low branches and maps `all` to both branches.

## Phase 6 regression gate

Run:

```bash
python -m neo_app.video.minimax_h3_lora_regression
```

The deterministic gate covers 43 cases across all five H3 UNET modes, including:

- empty stack equivalence;
- disabled populated stack equivalence;
- one standard LoRA;
- multiple standard LoRAs;
- speed/Turbo LoRA;
- mixed standard + speed ordering;
- compiler profile and sigma-consumer validation;
- queue/sidecar JSON serialization;
- legacy Turbo bridge and dedup behavior;
- speed auto-discovery;
- missing file/catalog failures;
- generic-loader rejection;
- invalid H3 target rejection;
- H3 GGUF refusal.

See `guides/02_VIDEO/minimax_h3_lora_regression.md` for the full matrix and promotion rule.

## Diagnostics

H3 compiled metadata includes `video_lora_stack` with:

- active state;
- requested/applied counts;
- standard vs speed counts;
- loader class;
- applied LoRA names, strengths, roles, and generated node ids;
- final model reference;
- live-catalog validation state;
- legacy Turbo bridge state;
- discovered speed candidates;
- model-only warnings such as ignored `strength_clip`.

The compiled result also includes `lora_patch_profile` for exact anchor inspection.

## Current boundaries

The Video LoRA system still does **not** claim:

- H3 GGUF LoRA support;
- LTX runtime LoRA patching;
- universal WAN dual-noise runtime replacement;
- final full Video LoRA Stack UI/library manager;
- silent automatic step/sampler rewrites based on speed-LoRA metadata.

Recommended speed settings may be surfaced later, but user-entered sampling values should not be silently replaced.

## Related files

- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`
- `neo_extensions/built_in/video.lora_stack/backend/payload_schema.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix.py`
- `neo_extensions/built_in/video.lora_stack/backend/patch_profile.py`
- `neo_app/video/lora_patch_profiles.py`
- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `neo_app/video/minimax_h3_lora_regression.py`
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
- `guides/02_VIDEO/minimax_h3_local_support.md`
- `guides/02_VIDEO/video_generation_extensions.md`

## Promotion rule

LTX runtime onboarding may begin only after the Phase-6 command reports:

```json
{
  "gate": "pass",
  "failed": 0,
  "next_phase_allowed": true
}
```
