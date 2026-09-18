---
guide_id: global.lora_release_phase8
title: LoRA Reliability Release — Phase 8
surface: global
scope: built_in
applies_to: [image, video, lora, migration, release]
tags: [lora, release, migration, rollback, validation]
priority: 125
version: 1
updated: 2026-09-18
---

# LoRA Reliability Release — Phase 8

Phase 8 integrates the Image reliability work from Phases 1–6 with the modern Video LoRA UI from Phase 7. It adds release evidence and migration inventory; it does not widen provider routes or silently rewrite user records.

## Before applying

1. Stop Neo Studio and local generation backends.
2. Back up the repository/source snapshot.
3. Back up `neo_data/`, especially `neo_data/extensions/lora_stack/library_index.json`.
4. Apply the changed files as one overlay.
5. Restart Neo, ComfyUI, and Forge where applicable.

## Release audit

Run from the repository root:

```text
python scripts/audit_lora_release_phase8.py --output neo_data/release_candidate/lora_phase8_report.json
```

The report verifies the eight-phase evidence inventory, required files, Python/JavaScript syntax, extension manifests, and migration tooling. It reads the Image LoRA index but does not modify it.

## Image library migration

The report classifies records as canonical, safe candidates, or ambiguous. To migrate:

1. select the correct Image provider/profile;
2. request `/api/extensions/lora_stack/library/identity-audit`;
3. inspect every proposed identity rewrite and every ambiguous record;
4. apply `/identity-repair` with the exact returned `preview_id`;
5. retain the timestamped `library_index.backup-*.json` until the release is accepted.

Neo never merges on basename alone, deletes ambiguous records, or applies a stale preview.

## Video draft migration

Legacy H3/WAN fields remain one-way readable for saved browser drafts. When a draft is loaded, explicit legacy files can be promoted into canonical `video_lora_stack.rows`; retired fields are removed from the in-memory draft and are never written back. The release audit cannot inspect or modify browser-owned storage, so open important saved workflows once and review the resulting stack before generation.

## Acceptance levels

- `code_ready=true`: all packaged evidence, manifests, docs, syntax checks, and migration tooling passed.
- `production_proven=true`: code-ready plus a complete passing physical GPU evidence report.

Deterministic compilation and UI checks do not prove that a backend downloaded the correct model, queued successfully, or produced visible LoRA influence.

## Rollback

Stop Neo, restore the previous source snapshot, and restore the matching `neo_data/` backup. If an Image identity repair was applied, its timestamped index backup may be restored independently. Do not delete `neo_data/` as a shortcut. Video legacy fields are intentionally not regenerated after one-way migration; use the pre-upgrade browser/profile backup when a full rollback is required.
