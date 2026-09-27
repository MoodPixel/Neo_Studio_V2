---
guide_id: assistant.universal_knowledge_contract.nkb5
surface: assistant
scope: built_in
title: Universal Knowledge Contract
summary: Defines how Neo references native authorities through stable knowledge IDs, revisions, evidence envelopes and typed relationships without copying every subsystem into one database.
tags: [assistant, memory, knowledge, evidence, provenance, adapters, retrieval, project, canon]
applies_to: [assistant, image_workspace, video_workspace, voice_workspace, prompt_captioning_workspace, roleplay_workspace, general]
priority: 100
version: 1
updated: 2026-09-26
---

# Universal Knowledge Contract

## Why Neo needs it

Neo has multiple real sources of authority:

- Image sidecars/portable IDs;
- Video output/replay records;
- Voice jobs/profiles/replay;
- Prompt + Captioning saved/history records;
- Roleplay SQLite canon/state;
- repository code;
- Guides/System Records;
- user project files.

The Assistant should reason across all of them without copying every field into one giant memory database.

The NKB-5 rule is:

```text
native authority
→ stable universal reference
→ searchable projection
→ resolve authoritative evidence on demand
→ Assistant packet
```

Unified Memory/search remains an index/evidence locator. The owning native system/source remains authoritative.

## Core primitives

The shared semantic contract defines:

```text
Project
Source
Object
Fact
Edge
Fragment
Asset
Event
Alias
Revision
Evidence
```

These are conceptual interfaces. They are not required to be separate SQLite tables.

## Identity layers

Do not collapse these:

```text
native ID
stable knowledge_id
immutable revision_id
disposable projection_id
```

- Native ID comes from the authority that owns the record.
- `knowledge_id` identifies the continuing logical thing across reindexing.
- `revision_id` identifies one immutable source/content version.
- `projection_id` belongs to FTS/vector/search infrastructure and may change on rebuild.

Search-index IDs must never become the only durable identity.

## Context identity

Use Neo's canonical context distinction:

```text
surface_id
scope_id
project_id
workspace_id (compatibility/optional)
```

A built-in Assistant Scope is not automatically a user Project.

Project/scope isolation happens before semantic search. Similar content from another project must not leak into the active project.

## Native references

Every adapter should be able to retain/resolve:

```text
adapter_id
authority namespace
native schema/version
native record ID
native revision ID when available
internal resolver key
```

Examples:

```text
neo.image -> neo_output_id
neo.video -> output/replay ID
neo.roleplay -> character/canon/memory row ID
neo.code -> qualified symbol + repository revision
neo.docs -> document path + document revision
```

## Fragments

A Fragment is a source-backed evidence slice, not a generic memory blob.

It should preserve:

```text
source
revision
exact locator
source text/payload
content hash
retrieval-only contextual prefix separately
```

For project documents this enables section/page/paragraph-aware retrieval rather than dumping large documents into model context.

## Evidence

Evidence wraps a source-backed/derived item with the NKB-4 semantics:

```text
origin
derivation
source integrity
evidence role
claim type
epistemic state
confidence kind
execution state
user confirmation
approval
canon + domain
lifecycle
conflict/supersession
effective time
retrieval salience
retention
```

`confirmed`, `approved`, `canon`, `active`, and `important` are not synonyms.

## Relationships

Core relationships are namespaced so different meanings do not collapse into one graph.

Examples:

```text
provenance.derived_from
evidence.supports
evidence.contradicts
version.supersedes
validation.validates
software.implements
software.imports
documentation.documents
identity.alias_of
lineage.produced_by
```

Domain adapters may add relationships such as `story.works_for` or `video.extends_clip`.

## Known unknowns

Neo distinguishes:

```text
established
explicit_unknown
not_established
unresolved_conflict
```

A missed search does not create a permanent "unknown" Fact.

For project/canon recall, `not_established` means Neo should say the current project context does not establish the detail rather than inventing it.

## Public projects

Users do **not** need a special Neo document schema.

Neo may accept ordinary PDF/DOCX/Markdown/TXT/etc. and internally derive Sources, Fragments, Objects, Aliases, Facts and Edges.

Extracted/inferred structure remains evidence-backed and does not automatically become canon.

## Native projects/surfaces

Neo-owned systems can use strict adapters because Neo already knows their schemas.

Examples:

- Roleplay characters/canon/memory;
- Image output IDs/sidecars/lineage;
- Video output/replay lineage;
- Voice jobs/profiles/script metadata;
- saved prompts/captions;
- repository symbols/tests/routes;
- documentation roles/revisions.

Do not rediscover deterministic native metadata from flattened prose when a native record already exists.

## Compatibility

Current runtime fields remain unchanged for now:

```text
trust_level
memory_state
retention_scope
approval_state
importance
confidence
status
```

Future adapters map these conservatively into the universal contract and preserve ambiguous values under compatibility metadata.

In particular, legacy `trust_level="confirmed"` must not automatically become "authoritative truth".

## Retrieval rule

Future retrieval should conceptually become:

```text
scope gate
→ intent/claim type
→ adapter/source selection
→ structured + lexical + dense retrieval
→ rank fusion/rerank
→ resolve native/source evidence
→ authority/conflict/supersession adjudication
→ bounded packet
```

Embeddings/rerankers rank relevance. They do not decide authority.

## NKB-5 implementation boundary

NKB-5 is records-only. No schema, adapters, migration, index changes or runtime behavior are implemented yet.

NKB-6 implements Native Knowledge Adapters against this contract. Arbitrary public Project ingestion remains NKB-7.

## NKB-6 implementation status — 2026-09-26

The native adapter layer defined by this contract is now implemented under `neo_app/knowledge/` for Roleplay, Video, Image, Voice, Prompt/Captioning, Neo code, and Neo documentation.

NKB-6 preserves the contract boundary:

```text
native authority
→ stable reference/evidence
→ disposable projection
```

It does not migrate native stores and does not yet connect adapter results to automatic Assistant retrieval. Public arbitrary project-file ingestion remains NKB-7; Assistant query planning/fusion remains NKB-8.

See `guides/06_ASSISTANT/native_knowledge_adapters.md` and System Record `01_ARCHITECTURE/NEO_UNIFIED_BRAIN_NKB6_NATIVE_KNOWLEDGE_ADAPTERS_20260926.md`.
