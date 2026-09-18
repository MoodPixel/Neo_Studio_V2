---
guide_id: video.lora_stack.release_acceptance
title: Video LoRA Release Acceptance
surface: video
scope: built_in
applies_to: [video_lora_stack, release, gpu_validation]
tags: [video, lora, release, acceptance, evidence]
priority: 100
version: 1
updated: 2026-09-11
---

# Video LoRA Release Acceptance

Phase 21 consolidates Phases 11–20.1 and produces one acceptance report. `code_ready=true` requires every deterministic compiler, route, migration, recovery, persistence, UI, and sigma-shift gate plus a support matrix with no unvalidated supported route.

`production_proven=true` additionally requires a passing Phase 18 physical GPU evidence report in which every selected case queued, completed, produced an output, and returned readable bytes. Never treat deterministic graph compilation as physical inference proof.

Run the preflight on a disposable tree, apply the release bundle, inspect `phase21_acceptance_report.json`, then execute the configured physical matrix. Native-workflow routes remain blocked until a real importer supplies the Phase 17 graph-bound contract.
