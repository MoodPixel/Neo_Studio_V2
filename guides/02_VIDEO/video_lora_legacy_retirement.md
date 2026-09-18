---
guide_id: video.lora_stack.legacy_retirement
title: Video LoRA Legacy Retirement
surface: video
scope: built_in
applies_to: [video_lora_stack, persistence, replay, migration]
tags: [video, lora, legacy, migration, retirement]
priority: 92
version: 1
updated: 2026-09-11
---

# Video LoRA Legacy Retirement

Phase 20 makes the built-in Video LoRA Stack the only normal-user configuration and graph-intent authority. The old H3 Turbo and WAN Video LoRA controls no longer appear in the Video tab and are never written to new state, output records, recipes, or replay payloads.

## Old projects

Old fields remain readable at the compatibility boundary. On first load they are converted into canonical rows, recorded under `video_lora_stack.migration`, and removed from the writable state. Existing canonical rows always win; Neo never combines competing legacy intent with a populated canonical stack.

If an old enabled control did not save a filename, its intent becomes an unresolved migration diagnostic rather than a guessed LoRA. Phase 19 recovery rules then keep generation fail-closed until the row is repaired or disabled.

## Retired fields

The reader recognizes the three H3 Turbo fields and ten WAN/LightX2V fields listed in `video_lora_legacy_retirement.LEGACY_FIELDS`. These names are input-only compatibility data. Any new writer emitting them fails the retirement gate.

## Compatibility module boundary

`video_lora_adapter.py` and the Phase 9 bridge are retained temporarily because current WAN compiler integration imports their schema/conversion helpers. They are compatibility readers, not a second graph authority. Removing those modules requires a separate compiler refactor after release telemetry proves old-state migration is no longer needed.

## Verification

Run the Phase 20 deterministic gate and load representative pre-stack H3, WAN standard, and WAN LightX2V projects. Confirm that each migrates once, survives reload as canonical state, does not duplicate LoRA nodes, and contains none of the retired keys after save.
