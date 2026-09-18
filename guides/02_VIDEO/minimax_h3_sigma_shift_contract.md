---
guide_id: video.minimax_h3.sigma_shift_contract
title: MiniMax H3 Sigma Shift Contract
surface: video
scope: built_in
applies_to: [minimax_h3, unet, gguf, video_lora_stack]
tags: [video, minimax, h3, sigma, audio, regression]
priority: 93
version: 1
updated: 2026-09-11
---

# MiniMax H3 Sigma Shift Contract

The Video tab exposes **Video Sigma Shift** (`h3_shift_video`, default `12.0`) and **Audio Sigma Shift** (`h3_shift_audio`, default `3.0`) for MiniMax H3 routes. The compiler writes them to `MiniMaxH3SigmaShift.shift_video` and `.shift_audio` respectively.

The sigma node remains downstream of the active model/LoRA chain. Adding a standard or speed/Turbo LoRA must change only the sigma node's `model` input reference; it must not reset either shift value.

Phase 20.1 verifies custom values `14.5` and `5.0` across Txt2Vid, Img2Vid, First/Last Frame, Reference-to-Video, and Vid2Vid for both UNET and GGUF, with and without a canonical Turbo row. It also locks the `12.0`/`3.0` defaults and request round-trip.

These are deterministic graph assertions. Physical audio/video behavior still requires the Phase 18 GPU evidence runner.
