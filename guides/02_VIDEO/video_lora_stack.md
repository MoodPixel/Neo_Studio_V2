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
version: 1
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

The extension must not hardcode workflow node ids. A compiler profile declares the exact model reference and the exact consumer input that may be rewired.

## Universal payload

A normal row looks like:

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
- `speed` — Turbo, LightX2V, Lightning, distilled, or other few-step acceleration LoRAs.

Several legacy labels normalize into those two roles. The role affects ordering/compatibility; it does not create a separate LoRA engine.

### Targets

- `all` — the normal single-model target and the combined target on a verified multi-branch route.
- `high` — WAN high-noise branch only where the compiler exposes it.
- `low` — WAN low-noise branch only where the compiler exposes it.

High/low targets must not appear on MiniMax H3 or LTX single-model routes.

## Exact-route support

Compatibility is fail-closed.

### MiniMax H3

Phase 5 runtime support is active for UNET/Diffusion routes:

| Route | Standard | Speed/Turbo | Target |
|---|---:|---:|---|
| `minimax_h3.unet.txt2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.img2vid` | Yes | Yes | `all` |
| `minimax_h3.unet.first_last_frame` | Yes | Yes | `all` |
| `minimax_h3.unet.reference_to_video` | Yes | Yes | `all` |
| `minimax_h3.unet.vid2vid` | Yes | Yes | `all` |

H3 GGUF LoRA application remains provisional/fail-closed even when the underlying GGUF generation route itself is selectable.

### WAN 2.2

The support matrix recognizes `wan22.gguf.img2vid_14b_dual_noise` as a verified multi-branch LoRA topology with `all/high/low` targeting. Universal runtime migration of the legacy WAN adapter is a later phase; Phase 5 does not replace that adapter.

### LTX 2.3

The matrix recognizes the clean model anchors for UNET Txt2Vid and Img2Vid standard LoRAs. Runtime onboarding is intentionally later than the MiniMax validation gate.

## MiniMax H3 Phase-5 behavior

MiniMax H3 is the reference implementation for the universal stack.

### Compiler anchor

The H3 compiler integration publishes one model-only patch branch immediately before `MiniMaxH3SigmaShift`.

```text
H3 model loader
  -> standard LoRA 1
  -> standard LoRA 2
  -> speed/Turbo LoRA
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> scheduler / guider / sampler
```

Node ids are determined from the compiler graph at compile time; the extension does not own them.

### Empty stack

With no active Video LoRA rows and legacy Turbo disabled, no LoRA nodes are added. The compiler still publishes profile metadata, but the graph remains on its normal H3 model path.

### Standard + speed ordering

Standard rows are applied first. `role: speed` rows are applied last so the acceleration/distillation patch sits downstream of character/style rows and immediately upstream of H3's native model processing.

## Legacy H3 Turbo compatibility

The existing fields remain load-compatible:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

When enabled, Neo converts them into one universal `role: speed` row. The legacy direct LoRA insertion path is bypassed so Turbo and normal Video LoRAs cannot accidentally use two independent patch systems.

If the selected Turbo LoRA already exists in the universal stack, Neo suppresses the synthetic legacy row instead of applying the same file twice.

## Turbo / LightX2V discovery

The old H3 discovery logic required the filename to literally contain `h3`. That excluded valid names such as:

```text
MiniMax-LightX2V-4steps.safetensors
```

Phase 5 classifies recommendations with an H3-family alias plus a speed token.

Family aliases include:

```text
h3
minimax
minimax_h3
minimax-h3
hailuo
```

Speed tokens include:

```text
turbo
lightx2v
lightning
4step / 4steps
8step / 8steps
distilled
```

This classifier is **recommendation-only**. An explicitly selected LoRA is not rejected merely because its filename does not match the heuristic.

## Loader safety

MiniMax H3 uses a model-only LoRA contract:

```text
LoraLoaderModelOnly
```

Neo does not treat generic `LoraLoader` as an interchangeable fallback. A generic loader commonly requires both model and CLIP inputs and therefore has a different graph contract.

If H3 LoRAs are requested and `LoraLoaderModelOnly` is unavailable, compilation fails with a specific error instead of generating a malformed graph.

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

Before inserting any LoRA node, Neo verifies that each declared consumer still points to the compiler-declared model reference. If the graph changes and that relation is stale, LoRA application fails closed.

WAN's verified dual-noise profile uses two branches instead:

```text
high model -> high consumers
low model  -> low consumers
```

The `all` target maps to both branches.

## Diagnostics

H3 compiled metadata includes a `video_lora_stack` block with:

- active state;
- requested/applied counts;
- standard vs speed counts;
- loader class;
- applied LoRA names, strengths, roles, and generated LoRA node ids;
- final model reference;
- legacy Turbo bridge state;
- discovered speed candidates;
- warnings for catalog or model-only CLIP-strength mismatches.

The compiled result also includes `lora_patch_profile` so Phase-6 regression tests can inspect the exact anchor contract.

## Current boundaries

Phase 5 does **not** yet claim:

- H3 GGUF LoRA support;
- LTX runtime LoRA patching;
- universal WAN dual-noise runtime replacement;
- the final full Video LoRA Stack UI/library manager;
- automatic sampler/step overwrites based on speed-LoRA metadata.

Recommended settings may be surfaced later, but Neo should not silently replace user-entered sampling values.

## Related files

- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`
- `neo_extensions/built_in/video.lora_stack/backend/payload_schema.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix.py`
- `neo_extensions/built_in/video.lora_stack/backend/patch_profile.py`
- `neo_app/video/lora_patch_profiles.py`
- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `guides/02_VIDEO/minimax_h3_local_support.md`
- `guides/02_VIDEO/video_generation_extensions.md`

## Next gate

Phase 6 is a MiniMax-only regression gate. Validate all five H3 modes with no LoRA, standard LoRA(s), speed/Turbo, mixed standard+speed, legacy bridge behavior, missing loader/file errors, and GGUF refusal before enabling another family.
