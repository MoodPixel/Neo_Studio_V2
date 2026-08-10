---
guide_id: assistant.project_brain_canonical_ingestion
title: Project Brain Canonical Ingestion
surface: assistant
scope: built_in
applies_to:
  - assistant
  - general
  - image_workspace
  - video_workspace
  - voice_workspace
  - prompt_captioning_workspace
  - roleplay_workspace
tags:
  - assistant
  - project_brain
  - memory
  - ingestion
  - pdf
  - docx
priority: 94
version: 1
updated: 2026-08-09
---

# Project Brain Canonical Ingestion

Project Brain is the Assistant-facing workflow for deliberately adding Scope/Project knowledge to Neo. From Phase 7 onward its retrieval-authoritative writes go through Unified Memory; the files under `neo_data/assistant/project_brain/` remain compatibility/audit projections and are not a second canonical memory database.

## Canonical write path

```text
Project Brain action
  -> canonical identity (surface_id / scope_id / project_id)
  -> Project Brain Ingestion Service
  -> Unified Memory event / fact / fragment
  -> SQLite FTS immediately
  -> semantic embedding/reindex can be queued through the Phase 10 unified Memory Job Service
```

Every canonical row keeps provenance for the originating Scope, surface, optional Delivery Project, source record/file, and source-content hash.

## Save to Scope Knowledge

Context -> Save to Scope Knowledge writes the user-authored note into Unified Memory first and keeps the existing Assistant JSON context record as a compatibility/UX projection. Editing the same knowledge item supersedes its older active canonical fragment instead of creating two competing active versions.

## Save selected as memory

A manual Assistant capture now writes its canonical memory representation first. The historical local capture/event/index bridges remain available for backward compatibility until the later migration phase.

## Capture Current State

Capture Current State keeps a readable Project Brain snapshot file and writes the snapshot summary/settings into Unified Memory. Repeating a capture with the same meaningful content reuses the existing snapshot/canonical fragment rather than multiplying equivalent snapshots.

## Index Project Data

Index Project Data still creates a readable metadata-index projection, but every summarized metadata record is also ingested into Unified Memory with facts such as model/settings when available.

Scope safety matters:

- General may scan all registered creative surface metadata for cross-surface coordination.
- A concrete surface Scope indexes that surface.
- A custom Assistant/client Scope does not automatically sweep Image, Video, Voice, Prompt/Captioning, and Roleplay history just because its surface is `assistant`.
- An explicit surface request can still target the requested surface.

## Upload Project Files

Project Brain now reuses the same shared Assistant document extractor used by normal attachments. Text-like documents, PDF, and DOCX can therefore become canonical Project Brain knowledge through one extraction implementation.

The original file is kept. Extracted text is chunked deterministically and written with file/content provenance. Rebuilding or uploading the same unchanged source does not create duplicate active fragments. Image uploads remain stored as project assets unless a later multimodal ingestion phase provides textual knowledge for them.

## Rebuild Project Brain

Rebuild is now a real idempotent pipeline:

1. scan/index allowed Neo-owned metadata;
2. replay snapshots into canonical memory;
3. replay saved metadata indexes;
4. ingest user-authored Scope Knowledge;
5. ingest manual captures;
6. extract/re-ingest Project Brain documents through the shared extractor;
7. run deterministic scoped consolidation and update SQLite FTS;
8. validate canonical fragment/fact/embedding-queue counts and write a report.

Phase 7 does not synchronously load embedding models. Semantic embedding work can remain queued for the later unified job/progress phase.

## Compatibility

Legacy Project Brain snapshots, indexes, uploads, Scope Knowledge JSON, and manual-capture files are preserved. They remain useful for UI/history/audit and for rebuilding pre-Phase-7 data, but new retrieval-authoritative Project Brain memory is Unified Memory.

No existing SQLite data is deleted or rewritten by Phase 7.
