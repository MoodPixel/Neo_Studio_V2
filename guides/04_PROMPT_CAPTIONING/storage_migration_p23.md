---
guide_id: prompt_captioning.storage_migration_p23
title: Prompt + Captioning P23 Storage Migration
surface: prompt_captioning
scope: built_in
applies_to:
  - prompt_studio
  - caption_studio
  - prompt_captioning_library
  - result_metadata
  - replay
priority: 82
version: 1
updated: 2026-08-08
---

# Prompt + Captioning P23 Storage Migration

## What P23.4 changes

P23.4 closes the Prompt/Captioning profile migration by making the canonical P23 profile durable across local storage.

The canonical profile is preserved for:

- saved prompts and prompt presets
- prompt history
- saved captions and caption presets
- caption history
- batch result records
- library export/import
- result metadata
- replay payloads
- library reuse payloads

The persistence marker is `prompt_captioning.persistence.v2`.

## Non-destructive compatibility

Neo keeps historical fields such as `style`, `target_use`, `caption_style`, `output_style`, `caption_mode`, and `component_type`. These remain compatibility data; the canonical `profile` is authoritative for new P23 behavior.

Unknown historical style values are preserved as `Custom` profile values. Historical scope aliases including `person_only`, `face_only`, `outfit_only`, `pose_only`, and `location_only` are migrated to the matching Analysis Scope. Historical `sd_prompt` maps to Image Generation Prompt.

## Lazy migration

Opening/listing an old library does not require a destructive rewrite. Neo derives the canonical profile when a profile-bearing record is read.

New saves, updates, duplicates, and imported records persist the canonical profile automatically.

## Migration status

Read-only status:

```text
GET /api/prompt-captioning/storage/migration-status
```

The response includes total profile-bearing records and `needs_migration`.

## Explicit migration

Dry run (default):

```json
{}
```

or:

```json
{
  "dry_run": true,
  "backup": true
}
```

Apply migration:

```json
{
  "dry_run": false,
  "backup": true
}
```

Send that body to:

```text
POST /api/prompt-captioning/storage/migrate
```

When applied with backup enabled, Neo copies each changed JSON file into:

```text
neo_data/prompt_captioning/migration_backups/p23_4_<UTC timestamp>/
```

before writing the canonicalized records.

The migration is idempotent. After a successful apply, a new dry run should report `needs_migration = 0`.

## Snapshot compatibility

Exports now use:

```text
prompt_captioning.library.v2
```

The snapshot carries canonical profiles, persistence/profile schema markers, and migration counts. Import still accepts older library payloads and migrates profile-bearing incoming records before write.

## Replay compatibility

Replay payloads now use:

```text
prompt_captioning.replay_payload.v2
```

Old metadata with v1 replay payloads is normalized when read. The original input/parameter/output content remains intact while the canonical profile and profile schema marker are added.

## Safety boundary

P23.4 does not automatically rewrite all user files during application startup. Lazy migration keeps older data immediately usable. Physical rewriting only occurs through normal save/update/import operations or the explicit storage migration endpoint.
