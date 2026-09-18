---
guide_id: video.lora_persistence_replay
title: Video LoRA Persistence, Metadata and Replay
surface: video
scope: built_in
applies_to:
  - video_lora_stack
  - video_results
  - video_output_inspector
  - video_replay
tags:
  - video
  - lora
  - persistence
  - metadata
  - replay
priority: 88
version: 1
updated: 2026-09-11
---

# Video LoRA Persistence, Metadata and Replay

Phase 12 stores the universal Video LoRA Stack with each Video output recipe. It preserves the exact user stack separately from runtime-applied truth.

## Saved state

The record keeps master enable state and every valid row in order, including disabled rows. Each row preserves its uid, portable catalog filename, strengths, Standard/Speed role and All/High/Low target.

## Inspector

Video Output Inspector exposes:

- requested rows;
- applied rows;
- standard/speed counts;
- unresolved missing-file rows;
- whether repair is required.

An installed extension is never presented as executed unless saved runtime metadata says it was applied.

## Replay

Loading a recipe stages the canonical universal stack. It does not auto-run generation and it does not restore deprecated H3 Turbo or WAN LoRA fields.

The browser restores the saved stack into `state.videoDraft.video_lora_stack`, refreshes the selected-profile catalog, and keeps Generate governed by the current route/backend compatibility checks.

Disabled stacks keep their rows but do not emit an active extension block. Missing files remain visible for replacement, disabling or removal after refreshing the selected backend catalog.

## Validation

```bash
python apply_phase12.py --check
python apply_phase12.py --apply
python -m pytest tests/test_video_lora_persistence_phase12.py -q
python -m neo_app.video.video_lora_completion_baseline
```

Physical GPU inference is not claimed by this phase.
