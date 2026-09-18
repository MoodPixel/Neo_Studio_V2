---
guide_id: video.wan22_rapid_aio_lora
title: WAN Rapid AIO Video LoRA
surface: video
scope: built_in
applies_to: [wan22, rapid_aio_gguf, video_lora_stack, txt2vid, img2vid]
tags: [video, wan, rapid aio, gguf, lora, lightx2v]
priority: 88
version: 1
updated: 2026-09-11
---

# WAN Rapid AIO Video LoRA

Phase 16 enables the universal Video LoRA Stack for:

- `wan22.rapid_aio_gguf.txt2vid`
- `wan22.rapid_aio_gguf.img2vid`

Both standard and speed/LightX2V rows are supported. Rapid AIO is a single-model graph, so every row must use `target=all`; WAN `high` and `low` remain exclusive to the dual-noise workflow.

## Graph contract

```text
Rapid AIO GGUF loader
  -> optional Sage/TeaCache/low-VRAM model adapters
  -> standard LoRA(s)
  -> speed/LightX2V LoRA(s)
  -> ModelSamplingSD3
  -> sampler
```

The compiler exposes its final model link to Phase 16. No new code depends on the template's numeric node IDs. The active route publishes a compiler-owned `neo.video.lora_patch_profile.v1` profile and runtime metadata.

## Live safety proof

An active stack is accepted only when `/object_info` proves:

- the selected Rapid AIO GGUF loader returns `MODEL`;
- `LoraLoaderModelOnly` exists;
- its inputs include `model`, `lora_name`, and `strength_model`;
- it returns `MODEL`;
- every selected LoRA file is present in its live catalog.

Generic `LoraLoader` fallback is forbidden. Filename classification remains advisory; manual speed-role selection is valid when the selected file exists.

## Still blocked

- `target=high` and `target=low` on Rapid AIO;
- generic model+CLIP loader fallback;
- a packed/provider loader that does not expose a `MODEL` output;
- imported WAN native-workflow LoRA mutation.

## Validation

Run `python -m neo_app.video.wan_rapid_aio_lora_regression` and the Phase 16 unittest. These are deterministic graph/socket contract tests. Real Txt2Vid and Img2Vid GPU generations with standard, speed, and mixed stacks are still required for production sign-off.
