---
guide_id: video.lora_stack.ux_compatibility_recovery
title: Video LoRA UX and Compatibility Recovery
surface: video
scope: built_in
applies_to: [video_lora_stack, persistence, replay, backend_profiles]
tags: [video, lora, ux, accessibility, compatibility, recovery]
priority: 91
version: 1
updated: 2026-09-11
---

# Video LoRA UX and Compatibility Recovery

Phase 19 makes a saved LoRA stack safe when a workflow, backend profile, catalog, or file changes. Neo preserves the saved row instead of silently deleting or substituting it. An enabled unresolved row blocks generation and presents three explicit recovery paths: choose a replacement file, disable the row, or remove it.

## Operator flow

1. Open the Video tab and select the intended backend profile and route.
2. Refresh the live LoRA catalog when Neo reports that the catalog is unavailable.
3. Resolve every highlighted enabled row:
   - **Replace:** choose an available file in that row's selector.
   - **Disable row:** retain the saved configuration without applying it.
   - **Remove:** explicitly delete the row from the stack.
4. Re-check role and target. Speed/Turbo and high/low targeting remain route-dependent.
5. Generate only after the recovery warning clears.

The check is case-insensitive for catalog filenames, but the live catalog spelling becomes the canonical runtime name. Disabled stacks and disabled rows retain compatibility notices without blocking generation.

## Keyboard and assistive technology

Rows expose an accessible group name, issue status, labelled move/remove controls, and a polite live status region. Use **Alt+Up** and **Alt+Down** while focus is inside a row to reorder it. Focus is restored after a stack re-render.

## Recovery contract

Recovery never guesses. Similar filenames may be suggested by the backend helper, but replacement requires an explicit user choice. Replay remains deterministic: missing intent is preserved; an unresolved enabled row cannot enter a generation payload.

Phase 19 tests prove the recovery and UI contracts. Physical inference proof still comes from the Phase 18 GPU evidence runner.
