---
guide_id: video.minimax_h3_gguf_lora_turbo
title: MiniMax H3 GGUF LoRA and Turbo
surface: video
scope: built_in
tags: [video, minimax h3, gguf, lora, turbo]
version: 1
updated: 2026-09-11
---

# MiniMax H3 GGUF LoRA and Turbo

Phase 13 enables the universal Video LoRA Stack on all five MiniMax H3 GGUF routes: Txt2Vid, Img2Vid, First/Last Frame, Reference-to-Video and Vid2Vid.

Standard and `role=speed` rows use the same compiler-owned model-only chain:

```text
H3 GGUF loader -> standard LoRA(s) -> speed/Turbo LoRA(s) -> MiniMaxH3SigmaShift
```

Img2Vid Turbo is supported both through the canonical stack and the temporary legacy H3 Turbo bridge. The bridge deduplicates the same file and never creates a second graph path.

## Runtime gate

GGUF permission is exact-route and live-schema gated. Active LoRA rows require `/object_info` to prove:

- a supported H3 GGUF loader is genuinely installed, not merely selected through a fallback class name;
- that loader advertises a `MODEL` output;
- `LoraLoaderModelOnly` is installed and advertises a `MODEL` input;
- the chosen LoRA exists in its live catalog;
- the compiler-owned patch profile is validated;
- generic `LoraLoader` fallback remains forbidden.

If any check fails, compilation stops before queueing. An empty/disabled stack leaves the original GGUF graph unchanged.

## Test commands

```bash
python -m neo_app.video.minimax_h3_gguf_lora_regression
python -m unittest tests.test_minimax_h3_gguf_lora_phase13 -v
python -m neo_app.video.minimax_h3_lora_regression
python -m neo_app.video.video_lora_completion_baseline
```

The Phase 13 gate contains 13 deterministic cases: standard and Turbo for five GGUF modes, the Img2Vid legacy Turbo bridge, and two live-schema rejection cases.

## Proof boundary

Passing these tests proves route policy, graph mutation, node ordering, catalog validation, and serialization. It does not prove that a specific GGUF quant plus third-party LoRA produces correct frames on a physical GPU. Run the GPU acceptance matrix before calling the feature production-proven.
