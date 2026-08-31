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
  - turbo
  - lightx2v
  - workflow patching
priority: 84
version: 3
updated: 2026-08-31
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

## Universal payload

A standard row:

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
- `speed` — Turbo, LightX2V, Lightning, distilled, or another acceleration LoRA.

Role affects ordering and compatibility. It does not create a separate LoRA engine.

### Targets

- `all` — single-model routes and the combined target on a verified multi-branch route.
- `high` — WAN high-noise branch only when exposed by the compiler.
- `low` — WAN low-noise branch only when exposed by the compiler.

MiniMax H3 and current LTX Phase-7 routes use `all` only.

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

### LTX 2.3

Phase 7 activates standard model-only LoRA runtime on the two primary UNET routes:

| Route | Standard | Speed/Turbo | Target |
|---|---:|---:|---|
| `ltx23.unet.txt2vid` | Yes | No | `all` |
| `ltx23.unet.img2vid` | Yes | No | `all` |

LTX GGUF and extended LTX modes remain fail-closed/provisional for LoRA runtime until their exact topology is separately validated.

### WAN 2.2

The support matrix recognizes `wan22.gguf.img2vid_14b_dual_noise` as a verified multi-branch LoRA topology with `all/high/low` semantics. The legacy WAN runtime adapter remains in place until a later compiler-anchor migration phase.

## MiniMax H3 model flow

MiniMax H3 remains the speed/Turbo-capable reference implementation:

```text
H3 model loader
  -> standard LoRA(s)
  -> speed/Turbo LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> scheduler / guider / sampler
```

Standard rows are applied before `role=speed` rows so the acceleration patch remains immediately upstream of native H3 sigma processing.

## LTX 2.3 model flow

Phase 7 uses a separate LTX integration adapter while preserving the same universal payload and compiler-owned profile contract:

```text
LTX model loader
  -> standard LoRA(s)
  -> LTXVChunkFeedForward
  -> CFGGuider
  -> sampler
```

The adapter locates the compiler-declared `LTXVChunkFeedForward` class in the built workflow, extracts its current model reference, and publishes that exact reference and consumer input in the patch profile. It does not own or assume workflow node IDs.

LTX Phase 7 accepts only:

```text
role = standard
target = all
loader = UNET
loader node = LoraLoaderModelOnly
```

`strength_clip` is ignored with a runtime warning because this validated topology is model-only.

## Empty or disabled stack

For every currently validated runtime route, no active rows means no LoRA node insertion and no model-consumer rewiring.

Verified invariants include:

```text
empty/disabled H3 stack workflow == original H3 workflow
empty/disabled LTX stack workflow == original LTX workflow
```

Metadata such as `lora_patch_profile` and `video_lora_stack` may exist outside the Comfy workflow without violating the no-op rule.

## Legacy H3 Turbo compatibility

Saved/request fields remain load-compatible:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

Neo converts these fields into the universal stack instead of inserting an independent Turbo node.

If the same Turbo file already exists in the universal stack, Neo keeps one row, preserves its uid/strength, promotes it to `speed` when necessary, normalizes the H3 target to `all`, and suppresses the synthetic legacy duplicate.

## Turbo / LightX2V discovery

H3 speed recommendation discovery recognizes family aliases such as `h3`, `minimax`, `minimax_h3`, `minimax-h3`, and `hailuo`, plus acceleration tokens such as `turbo`, `lightx2v`, `lightning`, `4step(s)`, `8step(s)`, and `distilled`.

The classifier is **recommendation-only**. A normal manually selected LoRA is not rejected because its filename lacks a speed marker.

LTX Phase 7 does not activate a speed classifier or speed-LoRA runtime path.

## Live catalog validation

Manual selection has one hard requirement on active H3 and LTX routes: the selected filename must exist in the live `LoraLoaderModelOnly` catalog exposed by ComfyUI.

The runtime rejects selected files that are missing, an empty ModelOnly catalog when rows are requested, and a backend exposing only generic `LoraLoader`.

Generic `LoraLoader` is not an interchangeable fallback because it has a model+CLIP contract while the currently validated H3/LTX routes are model-only.

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

Before inserting a LoRA node, Neo verifies that the declared consumers still point to the compiler-declared model reference. A stale graph/profile relationship fails closed.

WAN's verified dual-noise profile uses high/low branches and maps `all` to both branches.

## Regression gates

### MiniMax H3 — Phase 6

```bash
python -m neo_app.video.minimax_h3_lora_regression
```

CI-verified result:

```text
43 / 43 passed
```

The matrix covers all five H3 UNET modes, standard + speed stacks, legacy Turbo migration, no-op equivalence, catalog validation, generic-loader rejection, target validation, and GGUF refusal.

### LTX 2.3 — Phase 7

```bash
python -m neo_app.video.ltx_lora_regression
```

CI-verified result:

```text
17 / 17 passed
```

The LTX gate covers Txt2Vid and Img2Vid no-op equivalence, one/multiple standard LoRAs, speed-role rejection, branch-target rejection, catalog validation, generic-loader rejection, and GGUF refusal.

The Phase-7 workflow reruns the H3 gate in the same job. Current combined result:

```text
H3  43 / 43
LTX 17 / 17
Total 60 / 60
```

## Diagnostics

Compiled H3/LTX metadata includes `video_lora_stack` with route-specific runtime information such as:

- active state;
- requested/applied counts;
- standard vs speed counts;
- loader class;
- applied names, strengths, roles, and generated node IDs;
- final model reference;
- live-catalog validation state;
- model-only warnings such as ignored `strength_clip`.

H3 additionally reports legacy Turbo bridge state and discovered speed candidates.

The compiled result also includes `lora_patch_profile` for exact anchor inspection.

## Current boundaries

The Video LoRA system still does **not** claim:

- H3 GGUF LoRA support;
- LTX speed/Turbo LoRA support;
- LTX GGUF LoRA support;
- LTX extended-mode LoRA support;
- universal WAN dual-noise runtime replacement;
- final full Video LoRA Stack UI/library manager;
- silent automatic step/sampler rewrites based on speed-LoRA metadata.

Recommended speed settings may be surfaced later, but user-entered sampling values should not be silently replaced.

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
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
- `guides/02_VIDEO/ltx_lora_runtime.md`
- `guides/02_VIDEO/minimax_h3_local_support.md`
- `guides/02_VIDEO/video_generation_extensions.md`

## Promotion rule

Do not widen LTX or WAN LoRA compatibility unless the new exact-route implementation preserves both existing regression gates:

```text
MiniMax H3: 43 / 43
LTX 2.3:    17 / 17
```

and adds its own deterministic fail-closed coverage for the newly promoted route topology.
