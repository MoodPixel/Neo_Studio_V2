---
guide_id: assistant.native_knowledge_adapters.nkb6
surface: assistant
scope: built_in
title: Native Knowledge Adapters
summary: Explains how Neo exposes authoritative Image, Video, Voice, Prompt/Captioning, Roleplay, code, and documentation records to the Unified Brain without copying them into one master database.
tags: [assistant, memory, knowledge, adapters, image, video, voice, prompt, captioning, roleplay, code, documentation]
applies_to: [assistant, admin, image_workspace, video_workspace, voice_workspace, prompt_captioning_workspace, roleplay_workspace, general]
priority: 100
version: 4
updated: 2026-09-27
status: current
---

# Native Knowledge Adapters

## What NKB-6 changes

Neo now has a common read/reference layer for built-in native knowledge.

```text
Native authority
→ Native Knowledge Adapter
→ Universal Knowledge Contract
→ NKB-8 Unified Retrieval Planner
→ NKB-9 Context Packet
→ NKB-10 grounding policy
→ Assistant
```

The adapters do **not** move all native records into a new database.

## Built-in adapters

```text
neo.roleplay
neo.video
neo.image
neo.voice
neo.prompt_captioning
neo.code
neo.docs
```

Each adapter can expose stable references, project search text, hydrate the current authority, validate the source, resolve citations, and expose structured relationships where supported.

## Authority rule

The owning source remains authoritative:

- Roleplay → Roleplay SQLite;
- Video → canonical Video metadata/replay records;
- Image → canonical output sidecars + portable output identity;
- Voice → native job/profile/replay stores;
- Prompt/Captioning → saved/history/result stores;
- Code → repository source files;
- Documentation → Guide/System Record/README source files.

Unified Memory/search may index a projection, but the projection is not the authority.

## Identity rule

Do not treat these as interchangeable:

```text
native ID
knowledge_id
revision_id
projection_id
```

`knowledge_id` remains stable across reindexing. `revision_id` changes with source content. `projection_id` is disposable.

## Generated content is not canon

A successful generation can authoritatively establish execution facts such as model, seed, status, output ID, or replay lineage.

It does not make the generated text/image story claims project canon.

## Status and diagnostics

Normal status is intentionally lightweight:

```text
GET /api/memory/native-knowledge/status
```

Request a repository/native authority count scan explicitly when debugging:

```text
GET /api/memory/native-knowledge/status?deep=true
```

Other diagnostic/reference operations are available under `/api/memory/native-knowledge/*` for enumeration, projection, hydration, validation, citation lookup, structured lookup, relationships, and conservative legacy mapping.

## Current runtime boundary

Assistant can now search these adapters through the **NKB-8 Unified Retrieval Planner** when the task calls for their authority. Adapter results participate in weighted rank fusion/reranking, and selected strong evidence can be hydrated from the owning native source before packet assembly.

NKB-9 places the verified shortlist into one bounded provider-visible Context Packet, while NKB-10 applies the task-sensitive grounding policy.

NKB-7 implements arbitrary public Project ingestion separately from the native adapter registry. Public Project files emit NKB-5 source/evidence references while the uploaded file remains authority.

## Public project rule

Users do not need to follow a custom Neo schema for their own projects. NKB-7 derives contextual fragments and conservative source-backed helpers from ordinary files. Public Project evidence is intentionally not presented as another native Neo store; NKB-8 orchestrates it alongside these native adapters.


## NKB-8 retrieval handoff

The Unified Retrieval Planner now selects NKB-6 adapters by query intent instead of scanning every native surface for every Assistant turn. Adapter search projections participate in weighted rank fusion, then task-sensitive authority adjudication can hydrate selected native references through the owning adapter.

Native lookup remains an evidence locator: the native store is still authoritative. Code/Docs lookups are intentionally narrowed to one high-information query variant per adapter to avoid repeated broad repository/document scans.
