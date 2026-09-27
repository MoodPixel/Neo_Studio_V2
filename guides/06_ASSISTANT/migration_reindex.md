---
guide_id: assistant_unified_brain_migration_reindex
surface: assistant
scope: built_in
applies_to: [assistant, memory, admin]
tags: [assistant, memory, migration, reindex, project-brain, embeddings]
priority: 91
version: 1
updated: 2026-09-27
status: current
---

# Unified Brain Migration + Reindex

NKB-12 provides a non-destructive upgrade/rebuild workflow for existing Neo memory and search projections.

Use **Admin → Memory Engine → Background Jobs → NKB-12 migration + reindex**.

Recommended flow:

1. Choose **Dry-run plan** first.
2. Review Project count, legacy fragment counts, queued embeddings and native-adapter inventory.
3. Choose **Queue migration + reindex**.
4. Monitor the normal Unified Memory background-job list for progress/cancel/retry.
5. Review the migration report if warnings are shown.

The migration:

- replays existing Project Brain files through the current structured NKB-7 ingestion path;
- validates native Image/Video/Voice/Roleplay/Prompt/Caption adapters without copying their stores;
- rebuilds disposable System Record/code compatibility search projections;
- rebuilds Unified Memory SQLite embeddings using the current embedding configuration;
- preserves legacy authority rows and ambiguous trust/canon metadata instead of silently upgrading them.

Important boundaries:

- Native stores remain authoritative.
- Project source files remain authoritative.
- Search chunks, indexes and embeddings are rebuildable projections.
- Reindex time is not treated as the source's semantic update time.
- Running NKB-12 again is supported; it should refresh projections rather than duplicate project canon.

Diagnostics endpoints:

```text
GET  /api/memory/migration/nkb12/status
POST /api/memory/migration/nkb12/plan
POST /api/memory/migration/nkb12/run
```

Migration reports are written to:

```text
neo_data/memory/migrations/nkb12/
```
