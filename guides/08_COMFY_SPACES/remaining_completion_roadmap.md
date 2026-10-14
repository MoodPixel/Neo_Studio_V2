---
guide_id: comfy_spaces.remaining_completion_roadmap
title: Comfy Spaces Remaining Completion Roadmap
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - spaces
  - roadmap
  - presets
priority: 45
version: 1
updated: 2026-10-08
---

# Comfy Spaces — Remaining Completion Roadmap

The shipped code and implementation records currently cover NCS-1 through **NCS-15**, including the Graph Anchors, per-run Graph Add-ons, Stage-aware execution, and Variant subphases of NCS-10. This guide separates the remaining planned work from available features; a planned feature should not be described as implemented.

## Completed since the previous audit

- **NCS-14 — Presets:** value-only, version-pinned, non-media input overlays with independent local storage, save/list/apply/delete UI, user/Space scope metadata and stale-base handling. Presets cannot insert nodes or update stages/Graph Add-ons; use **Variants** for graph or stage configuration. No GPU execution was performed.

## NCS-15 now available

- Export a published version with **Export .neospace** on its library card.
- Import a `.neospace` file from the library; Neo validates before requesting confirmation and will not overwrite existing versions.
- Packages carry declarative files and safe presentation images, not model weights, scripts or custom-node code. Hashes protect archive integrity but do not authenticate who published it.
- Destination runtime requirements remain **not checked** until that runtime is probed; Generate still performs live preflight.

## Remaining work
- **NCS-16 — Trust/security hardening** is the next phase. NCS-15 supplies bounded archives, file digests, non-overwriting imports, and re-use of existing publication validators; publisher authentication and expanded untrusted-content review are not complete.

- **NCS-17 — Cross-surface publication:** make Image, Video, Assistant and other surfaces reference one published Space by stable ID/version rather than copying the workflow.
- **NCS-18 — Registry/community (optional):** sharing/discovery metadata after the local package and trust layers are validated.

## Definition of completion

For a locally complete Comfy Spaces release, finish 16 and validate on a connected **real ComfyUI** instance with text/image input, multi-media outputs, graph add-ons, stage continue/resume, Variants, version migration/rollback, missing-dependency blocking, and recovery/cancellation. Automated contract tests by themselves do not demonstrate physical GPU execution.

Per-run configuration always receives live graph/runtime validation; a previously green Dependency Health snapshot is never permission to bypass preflight.

The authoritative internal implementation/acceptance record is `neo_system_records/01_ARCHITECTURE/NEO_COMFY_SPACES_POST_NCS13_COMPLETION_AUDIT_20261008.md`.
