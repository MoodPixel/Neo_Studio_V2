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
version: 3
updated: 2026-09-26
status: current
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

NKB-7 separates **persistent Project ingestion** from the bounded extractor used by one-turn Assistant attachments. The original uploaded file remains source authority. Persistent Project text is parsed into structure-aware source blocks and small contextual fragments instead of being reduced to one broad text preview.

Supported structured paths include text/Markdown, HTML, JSON, CSV/TSV, text-extractable PDF, DOCX, PPTX, and XLSX. Text-extractable persistent PDFs are not limited to the first 40 pages, and persistent Project text is not truncated to the former ~24,000-character Project indexing window. Existing upload-byte limits still apply. OCR for image-only/scanned pages is not introduced by NKB-7.

Each fragment keeps document/revision identity, heading/source locators, NKB-5 evidence references, and `consolidation_policy=source_direct`. Explicit entities, aliases, key/value facts, and a bounded set of explicitly declared relationships may be indexed conservatively when the source states them. Neo does not infer unstated project lore during ingestion.

The compatibility Scope Knowledge entry for a Project file is pointer-only; it does not copy a large section of the document into a second model-facing context lane. A `.knowledge.json` manifest records structured descriptors beside the upload metadata.

## Rebuild Project Brain

Rebuild is now a real idempotent pipeline:

1. scan/index allowed Neo-owned metadata;
2. replay snapshots into canonical memory;
3. replay saved metadata indexes;
4. ingest user-authored Scope Knowledge;
5. ingest manual captures;
6. extract/re-ingest Project Brain documents through the NKB-7 persistent structured extractor;
7. preserve primary Project source fragments as `source_direct` evidence while allowing other memory classes to use deterministic consolidation;
8. update SQLite FTS / semantic work queues and validate canonical fragment/fact counts;
9. write/update document knowledge manifests and the rebuild report.

Phase 7 does not synchronously load embedding models. Semantic embedding work can remain queued for the later unified job/progress phase.

## Compatibility

Legacy Project Brain snapshots, indexes, uploads, Scope Knowledge JSON, and manual-capture files are preserved. They remain useful for UI/history/audit and for rebuilding pre-Phase-7 data, but new retrieval-authoritative Project Brain memory is Unified Memory.

No existing SQLite data is deleted or rewritten by Phase 7.

## Large canon-heavy documents

NKB-7 removes the former persistent Project-specific ~24k text ceiling, 40-page PDF scan ceiling, broad ~6k/16-chunk path, and large compatibility Scope copy. Canon-heavy sources are represented as smaller source-located fragments with preserved structure where available.

For named entities, explicit aliases can be propagated into **search text** for fragments in the same entity section. This improves retrieval for queries such as `Eli job` without rewriting the authoritative source fragment.

Primary uploaded document fragments are excluded from generic consolidation because merged summaries can blend nearby people/events/rules. The original uploaded file remains the authority; stored fragments are retrieval projections. Neo can re-open the source and detect whether a stored revision is still verified or has become stale.

NKB-7 improves evidence completeness and separation; NKB-8 retrieval/authority planning, NKB-9 bounded context packets, and NKB-10 grounding modes now complete the strict canon-answering path. Missing evidence is not permission to invent project facts.

See `guides/06_ASSISTANT/public_project_ingestion.md` for the current public Project ingestion behavior.

