---
guide_id: assistant.project_brain
title: Assistant Project Brain
surface: assistant
scope: built_in
applies_to:
  - assistant
  - general
  - image_workspace
  - video_workspace
  - roleplay_workspace
  - prompt_captioning_workspace
  - voice_workspace
tags:
  - assistant
  - project
  - memory
  - snapshots
  - guides
priority: 90
version: 7
updated: 2026-08-09
---

# Assistant Project Brain

Project Brain is the user-facing ingestion and inspection area attached to an Assistant Scope. It is not a separate canonical memory database and it is not the same thing as a client/delivery Project.

## Identity contract

Neo separates `surface_id`, Assistant `scope_id`, and real Delivery `project_id`. Project Brain folders still use the historical Scope-as-`project_id` storage key for compatibility, while canonical Unified Memory rows retain explicit Scope/surface/Delivery Project provenance.

For example, Image Workspace is canonically `surface_id=image`, `scope_id=image_workspace`, with `project_id` empty unless a real client/work project is linked.

## Knowledge layers

Project Brain exposes four practical layers:

1. stable Guides from the repo `guides/` folder;
2. captured current-state snapshots;
3. indexed Neo-owned metadata/results;
4. user-provided Scope/Project knowledge, manual captures, and uploaded documents.

From Phase 7, Project Brain write actions ingest retrieval-authoritative knowledge into Unified Memory. Existing files/context cards remain compatibility and audit projections so older data remains readable and rebuildable.

## Controls

### Capture Current State

Use this after an important current surface state is worth deliberately pinning. Neo keeps a readable snapshot projection and writes the snapshot/settings into Unified Memory. Equivalent repeated captures are deduplicated.

### Index Project Data

Scans recent allowed Neo-owned metadata/results and canonically ingests those summaries/facts. General may coordinate across surfaces; a custom Assistant/client Scope no longer silently sweeps every creative surface merely because its surface is `assistant`.

### Upload Project Files

Stores the reference file and uses the shared Assistant document extractor. Text-like documents, PDF, and DOCX can become canonical Project Brain knowledge. Extracted content is chunked with source/content provenance and idempotent hashing.

### Rebuild Project Brain

Rebuild now performs an actual idempotent pipeline: scan metadata, ingest snapshots/indexes/Scope Knowledge/manual captures/documents, deduplicate, consolidate, update SQLite FTS, validate counts, and save a rebuild report.

Phase 10 now owns the long-running execution layer. Rebuild is backgrounded by default through the persistent Memory Job Service and reports stages, percent, item counts, messages, cancellation/retry state, and a durable job record. Embedding/reindex work can use the same job authority rather than blocking the Project Brain card.

### View Context Pack

Use this to inspect the context assembled for Assistant. Normal memory retrieval still enters through the single Retrieval Gateway. Project Brain compatibility projections remain inspectable while canonical Project Brain content is retrievable through Unified Memory.

## Compatibility and safety

- No legacy Project Brain file or existing memory row is deleted by Phase 7.
- Same-content sources are namespaced by Scope/Delivery Project so two clients/scopes cannot overwrite one another.
- Changed versions supersede older active canonical fragments for the same source.
- Roleplay and Phase 6 Scope/Project isolation remain authoritative.
- Project Brain is ingestion UX; Admin -> Delivery Projects does not own a second memory engine.

See:

- `guides/06_ASSISTANT/project_brain_canonical_ingestion.md`
- `guides/06_ASSISTANT/assistant_memory_and_scopes.md`
- `guides/06_ASSISTANT/retrieval_gateway.md`
- `guides/06_ASSISTANT/scope_priority_retrieval.md`


## Phase 8 relationship

Phase 8 does not replace Project Brain. Automatic surface ingestion records successful runtime history; Project Brain remains the deliberate Scope/Delivery Project ingestion workflow for documents, snapshots, manual knowledge, and rebuilds. Both write to Unified Memory.

## Phase 10 background jobs and progress

`Rebuild Project Brain` now creates a `project_brain_rebuild` job in `neo_memory_jobs` by default. The Project Brain card can reconnect to the active/latest job after rerender/navigation and show the current phase, percent, item counts, message, Cancel, and Retry. Repeated clicks with the same active Scope/surface dedupe key reuse the existing queued/running job.

The job record persists in SQLite; the worker thread is process-local. If the Neo server stops mid-job, a stale running job is marked interrupted/failed and retryable at the next startup rather than being reported as still running. Cancellation is cooperative at safe checkpoints.

See `guides/06_ASSISTANT/background_memory_jobs.md`.


## Phase 11 Scopes organization

Project Brain is presented inside Assistant → **Scopes**. The historical internal subtab ID remains `projects` for compatibility. Project Brain is the deliberate capture/import/maintenance workflow for the active Scope; it is not a second Delivery Project system and it is not the Memory Lens.

The Scopes view separates **Capture & import** actions from **Maintain & inspect** actions and keeps the Phase 10 persistent rebuild task card beside the Project Brain controls. Canonical memory inspection belongs in Assistant → Memory, while detailed retrieval/runtime proof belongs in Context/Inspector.
