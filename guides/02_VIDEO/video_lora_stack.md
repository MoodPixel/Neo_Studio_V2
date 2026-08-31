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
priority: 84
version: 4
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

## MiniMax H3 model flow

```text
H3 model loader
  -> standard LoRA(s)
  -> speed/Turbo LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> scheduler / guider / sampler
```

Standard rows are applied before `role=speed` rows.

## LTX 2.3 model flow

```text
LTX model loader
  -> standard LoRA(s)
  -> LTXVChunkFeedForward
  -> CFGGuider
  -> sampler
```

Phase 7 locates the built compiler's actual `LTXVChunkFeedForward.model` reference and publishes it through the compiler-owned patch profile. LTX currently accepts `role=standard`, `target=all`, UNET, and `LoraLoaderModelOnly` only.

## WAN single-model flow

```text
WAN UNET loader
  -> standard LoRA(s)
  -> ModelSamplingSD3
  -> sampler
```

Phase 8 locates the compiler-selected sampling class in the built graph and uses its current model reference as the patch anchor. The integration does not assume workflow node IDs.

WAN UNET currently accepts `role=standard` and `target=all` only. Speed and high/low branch targets fail closed.

## WAN dual-noise flow

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

CI verifies no-op workflow equivalence for:

```text
all five H3 UNET modes
LTX UNET Txt2Vid / Img2Vid
WAN UNET Txt2Vid / Img2Vid
WAN dual-noise GGUF Img2Vid
```

Metadata such as `lora_patch_profile` and `video_lora_stack` may exist outside the Comfy workflow without violating the no-op rule.

## Legacy H3 Turbo compatibility

Saved/request fields remain load-compatible:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

Neo converts these fields into the universal stack instead of inserting an independent Turbo node. Matching universal rows are deduplicated and promoted to `speed` when required.

## Legacy WAN compatibility

Phase 8 retains the historical WAN request fields for saved-workflow compatibility:

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

They are now migration inputs rather than graph mutation authority.

Legacy target conversion:

```text
Both -> all
High -> high
Low  -> low
```

Legacy LightX2V becomes high- and low-target `role=speed` rows. Duplicate branch coverage is suppressed, and a matching existing row can be promoted from `standard` to `speed` instead of loading the same file twice.

The historical fixed WAN LoRA node IDs (`129:101`, `129:102`, `9001`, `9002`) are not used by the Phase-8 universal patcher. Regression tests assert that they do not survive in migrated workflows.

The old `video_lora_adapter.py` remains temporarily for compatibility semantics; it is not deleted in Phase 8.

## Generate payload preservation

WAN Generate functions rebuild dataclass payloads before calling Compile. Since extension blocks are not dataclass fields, this can strip the universal stack from a nested call.

`neo_app/video/wan_lora_payload_context.py` preserves the outer user payload until the compiler build hook consumes it. Phase-8 regression tests cover both WAN UNET Txt2Vid Generate and WAN dual-noise GGUF Img2Vid Generate.

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

Before inserting a LoRA node, Neo verifies that declared consumers still point to the compiler-declared model reference. A stale profile fails closed.

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

The Phase-8 CI workflow reruns all three families together:

```text
H3  43 / 43
LTX 17 / 17
WAN 30 / 30
Total 90 / 90
```

The WAN matrix includes single-model no-op/standard stacks, dual-noise all/high/low targeting, speed ordering, legacy Normal/LightX2V migration and deduplication, loader/catalog failures, historical-node absence, and Generate -> Compile payload preservation.

See `guides/02_VIDEO/wan_lora_runtime.md` for the WAN-specific contract.

## Diagnostics

Compiled route metadata may include:

- `lora_patch_profile` — compiler-owned graph anchor contract;
- `video_lora_stack` — requested/applied counts, roles, targets, loader class, final refs, generated LoRA nodes, warnings, and live-catalog state;
- H3 legacy Turbo bridge metadata;
- WAN legacy bridge metadata and compatibility snapshot.

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
- safe removal of legacy H3/WAN controls from saved-workflow compatibility.

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
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
- `guides/02_VIDEO/ltx_lora_runtime.md`
- `guides/02_VIDEO/wan_lora_runtime.md`

## Promotion rule

Do not widen Video LoRA compatibility unless the new exact-route implementation preserves the existing gates and adds deterministic fail-closed coverage for the promoted topology.

Current baseline:

```text
MiniMax H3: 43 / 43
LTX 2.3:    17 / 17
WAN 2.2:    30 / 30
Combined:   90 / 90
```
