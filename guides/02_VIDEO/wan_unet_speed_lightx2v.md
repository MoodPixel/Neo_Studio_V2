---
guide_id: video.wan_unet_speed_lightx2v
title: WAN UNET Speed and LightX2V LoRAs
surface: video
scope: built_in
tags: [video, wan, unet, lora, lightx2v, speed]
version: 1
updated: 2026-09-11
---

# WAN UNET Speed and LightX2V LoRAs

Phase 15 enables `role=speed` rows on `wan22.unet.txt2vid` and `wan22.unet.img2vid`. LightX2V, Lightning and other WAN accelerators use the same compiler-owned `LoraLoaderModelOnly` anchor as standard LoRAs.

Application order is deterministic:

```text
WAN model -> standard LoRA(s) -> speed/LightX2V LoRA(s) -> ModelSamplingSD3
```

The selected file must exist in the live model-only LoRA catalog. Filename classification only recommends likely WAN accelerators; it never overrides a manually chosen role. Every single-model WAN row must target `all` because this graph has no separate high/low model branches.

Neo preserves user sampling values. When a speed row is active above eight steps, it records a warning instead of silently rewriting the request. Follow the selected LoRA author's steps, sampler, scheduler and shift recipe.

WAN GGUF dual-noise behavior is unchanged and retains `all/high/low` targeting. Rapid AIO and native-workflow routes remain blocked pending their own exact compiler contracts.

## Validation

```bash
python -m unittest tests.test_wan_unet_speed_lora_phase15 -v
python -m neo_app.video.wan_unet_speed_lora_regression
python -m neo_app.video.wan_lora_regression
python -m neo_app.video.video_lora_completion_baseline
```

These are deterministic graph-contract tests. A real GPU generation is required to prove that a specific LightX2V file and sampling recipe produces usable frames.
