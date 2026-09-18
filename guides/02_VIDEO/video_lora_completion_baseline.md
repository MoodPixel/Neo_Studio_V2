---
guide_id: video.lora_completion_baseline
title: Video LoRA Completion Baseline
surface: video
scope: built_in
applies_to:
  - video_lora_stack
  - minimax_h3
  - ltx23
  - wan22
  - regression
  - release_validation
tags:
  - video
  - lora
  - baseline
  - audit
  - regression
priority: 87
version: 1
updated: 2026-09-11
---

# Video LoRA Completion Baseline

Phase 11 provides one deterministic command for validating the complete Phase 6–10 Video LoRA contract on the current working tree:

```bash
python -m neo_app.video.video_lora_completion_baseline
```

The command runs all 144 established regression cases and audits support-matrix/extension-manifest parity.

## Passing result

```text
gate = pass
audit_failed = 0
regression_case_count = 144
supported_route_count = 10
next_phase_allowed = true
```

## What it protects

- MiniMax H3 UNET remains standard + Speed/Turbo across its five promoted modes.
- LTX UNET Txt2Vid/Img2Vid remains standard-only until its expansion phase.
- WAN UNET Txt2Vid/Img2Vid remains standard-only until speed validation.
- WAN dual-noise GGUF retains semantic All/High/Low targeting.
- H3 GGUF, LTX extended/GGUF/native and WAN Rapid AIO/native routes do not become enabled accidentally.
- Promoted routes require validated compiler-owned patch profiles.
- Manifest availability agrees with exact-route runtime support.

## What it does not prove

This is not a GPU inference test. It does not download models, queue ComfyUI jobs or judge visual LoRA influence. Physical execution remains a separate release gate.

## CI

The Phase-11 workflow runs the aggregate module and its unittest wrapper. It replaces the missing historical per-phase workflow files as the forward current-tree gate.

## Next phase

After this gate passes on the developer checkout, Phase 12 implements canonical Video LoRA save/reload, output metadata, Output Inspector visibility and replay round-trip behavior.

