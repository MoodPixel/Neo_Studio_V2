---
guide_id: video.ltx23_lora_runtime
title: LTX 2.3 UNET Video LoRA Runtime
surface: video
scope: built_in
applies_to:
  - video_generation
  - ltx23
  - video_lora_stack
  - comfyui
  - txt2vid
  - img2vid
tags:
  - video
  - ltx
  - lora
  - txt2vid
  - img2vid
  - regression
priority: 84
version: 1
updated: 2026-08-31
---

# LTX 2.3 UNET Video LoRA Runtime

Phase 7 activates the universal `video.lora_stack` runtime for the two validated LTX 2.3 UNET primary routes:

```text
ltx23.unet.txt2vid
ltx23.unet.img2vid
```

The phase intentionally enables **standard model-only LoRAs only**. It does not promote LTX speed/Turbo LoRAs, GGUF LoRA injection, WAN-style branch targeting, or the extended LTX generation modes.

## Compiler-owned anchor

Both primary LTX compilers expose the same clean model lineage:

```text
LTX model loader
  -> Video LoRA Stack (0..N standard LoRAs)
  -> LTXVChunkFeedForward
  -> CFGGuider
  -> sampler
```

The Video LoRA extension never knows workflow node IDs. The LTX integration reads the compiler-produced bindings, locates the single `LTXVChunkFeedForward` model consumer, and publishes a `neo.video.lora_patch_profile.v1` profile containing the exact upstream model reference and consumer input.

The resulting profile is:

```text
owner = compiler
loader_type = model_only
loader_node_class = LoraLoaderModelOnly
targets = [all]
validated = true   # only for the two Phase-7 UNET routes
```

## Runtime rules

An active LTX Video LoRA row must satisfy all of the following:

- route is `ltx23.unet.txt2vid` or `ltx23.unet.img2vid`;
- role is `standard`;
- target is `all`;
- ComfyUI exposes `LoraLoaderModelOnly`;
- selected filename exists in the live `LoraLoaderModelOnly.lora_name` catalog.

Multiple standard LoRAs are applied in stack order:

```text
model loader
  -> LoRA 1
  -> LoRA 2
  -> ...
  -> LTXVChunkFeedForward
```

If a row contains `strength_clip`, Phase 7 preserves the model strength but ignores the CLIP strength and emits a runtime warning because the validated LTX topology is model-only.

## Explicit fail-closed boundaries

Phase 7 rejects:

```text
role = speed / turbo          -> blocked
target = high / low           -> blocked
LTX GGUF + LoRA               -> blocked
generic LoraLoader fallback   -> blocked
missing selected LoRA file    -> blocked
empty ModelOnly catalog       -> blocked
```

The following LTX modes remain outside Phase 7 and keep their provisional/unsupported LoRA state until their compiler lineage is separately validated:

- First/Last Frame;
- MultiScene;
- Extend;
- Vid2Vid;
- Depth/Motion;
- Prompt Schedule;
- Audio/Video.

## No-op invariant

For both Phase-7 routes:

```text
empty Video LoRA stack workflow == original LTX workflow
```

and:

```text
disabled populated Video LoRA stack workflow == original LTX workflow
```

No LoRA node is inserted and no model consumer is rewired when the stack is empty or disabled.

## Regression gate

Run from the repository root:

```bash
python -m neo_app.video.ltx_lora_regression
```

Report schema:

```text
neo.video.ltx23.lora_regression.v1
```

The deterministic Phase-7 matrix contains **17 LTX cases**:

- 8 primary-route graph cases across Txt2Vid and Img2Vid;
- 4 route-specific speed/target fail-closed cases;
- 3 live-loader/catalog fail-closed cases;
- 2 GGUF fail-closed cases.

GitHub Actions result on 2026-08-31:

```text
LTX Phase 7: 17 / 17 passed
MiniMax H3 regression guard: 43 / 43 passed
Combined Video LoRA regression: 60 / 60 passed
```

The Phase-6 H3 guard is deliberately rerun in the same workflow so installing the LTX adapter cannot silently regress the MiniMax reference implementation.

## Current supported Video LoRA runtime

```text
MiniMax H3 UNET
  Txt2Vid             standard + speed/Turbo
  Img2Vid             standard + speed/Turbo
  First/Last Frame    standard + speed/Turbo
  Reference-to-Video  standard + speed/Turbo
  Vid2Vid             standard + speed/Turbo

LTX 2.3 UNET
  Txt2Vid             standard only
  Img2Vid             standard only
```

Everything else remains governed by the exact-route support matrix and fails closed unless explicitly validated.

## Related files

- `neo_app/video/ltx_lora_integration.py`
- `neo_app/video/ltx_lora_regression.py`
- `neo_app/video/ltx_txt2vid_compiler.py`
- `neo_app/video/ltx_img2vid_compiler.py`
- `neo_app/video/lora_patch_profiles.py`
- `neo_app/video/video_lora_runtime.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json`
- `guides/02_VIDEO/video_lora_stack.md`
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
