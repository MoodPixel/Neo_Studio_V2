---
guide_id: video.ltx_complete_route_lora
title: LTX Complete Compiler-Route LoRA Onboarding
surface: video
scope: built_in
tags: [video, ltx, lora, unet, gguf]
version: 1
updated: 2026-09-11
---

# LTX Complete Compiler-Route LoRA Onboarding

Phase 14 enables standard Video LoRAs on all 18 Neo-compiled LTX 2.3 routes:

- Txt2Vid and Img2Vid;
- First/Last Frame, MultiScene, Extend and Vid2Vid;
- Depth/Motion, Prompt Schedule and Audio-Video;
- both UNET and GGUF loaders.

Every route owns an exact model-only anchor immediately upstream of `LTXVChunkFeedForward`. Multiple LoRAs are chained in saved stack order and the final model reference rewires that existing consumer.

GGUF routes additionally require live `/object_info` proof that an actual GGUF loader emits `MODEL` and `LoraLoaderModelOnly` accepts `MODEL`. Fallback names and generic `LoraLoader` are rejected.

LTX speed/Turbo semantics are not promoted: LTX rows remain `role=standard`, `target=all`. Empty or disabled stacks preserve graph equivalence.

## Native-workflow boundary

`ltx23.native_workflow.txt2vid` and `.img2vid` remain blocked. The current tree has support-matrix placeholders but no LTX native-workflow compiler, importer, or compiler-owned model anchor. They cannot be safely patched until that architecture exists.

## Validation

```bash
python -m unittest tests.test_ltx_complete_route_lora_phase14 -v
python -m neo_app.video.ltx_complete_route_lora_regression
python -m neo_app.video.ltx_lora_regression
python -m neo_app.video.video_lora_completion_baseline
```

Contract tests prove routing, graph policy and live socket validation. They are not physical GPU inference proof.
