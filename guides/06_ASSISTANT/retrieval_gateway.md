---
guide_id: assistant.retrieval_gateway
title: Assistant Single Retrieval Gateway
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
  - memory
  - retrieval
  - knowledge
  - provenance
  - control_center
priority: 100
version: 9
updated: 2026-09-27
---

# Assistant Single Retrieval Gateway

Phase 5 established one Assistant retrieval boundary: the **Retrieval Gateway**. NKB-8 keeps that boundary and places the Unified Retrieval Planner inside it.

The gateway does not merge every storage database into one database. It places the existing retrieval authorities behind one query/result contract so Assistant does not independently search them and concatenate unrelated result sets.

## Retrieval lanes

### Unified Memory

Used for experiential/project/surface memory already represented in M9 fragments, including generation history, saved output memory, successful settings, and other surface/runtime facts.

### Knowledge Index

Used for source-backed static Neo knowledge such as:

- `neo_system_records`;
- Neo codebase chunks;
- extension manifests;
- Admin configuration;
- surface blueprints;
- memory-consolidation records.

Historical experiential source-index lanes such as `assistant_memory`, `prompt_libraries`, and legacy `project_workspace` are intentionally **not promoted into the Knowledge adapter** in Phase 5. Their owning migrations happen later.

### Built-in Guides

Neo Guides join the same gateway result as a lightweight source-backed adapter. They retain Guide provenance and do not execute a separate provider-context search.

## One turn, one retrieval result

For normal Assistant chat:

```text
Assistant Control Center
        ↓
Retrieval Gateway (one query)
        ↓
rank + deduplicate + provenance
        ↓
M12 safety check for Unified Memory rows
        ↓
NKB-9 Context Packet consumes the exact same gateway result
        ↓
Prompt Compiler includes one bounded packet
```

Context Pack still exposes compatibility projection sections such as `project_brain`, `project_knowledge`, `built_in_guides`, `memory_engine`, and `source_grounding` for Inspector/older routes. When a NKB-9 packet is present, those compatibility sections are **not provider-visible**; the Prompt Compiler compiles only the finalized packet.

## Adapter balance

The gateway first removes exact normalized duplicate content across adapters. If the requested context limit can hold every active adapter, it reserves one best unique item per active lane before filling remaining slots by global score.

This prevents a high-ranked Guide from crowding a directly matched saved memory out of a small context window, while still allowing globally stronger results to fill the rest of the budget.

This adapter balance remains active in Phase 6. Scope eligibility is handled by **Scope Classes and Project Sandboxes**: General/Neo built-ins may use bounded query-driven federation, while user-created project scopes are hard sandboxes. Adapter balancing does not itself decide which scopes are eligible.

## Identity and storage compatibility

Phase 1 canonical identity remains authoritative:

```text
surface_id
scope_id
project_id
```

The gateway uses the Phase 1 compatibility filter to read existing Unified Memory rows without rewriting SQLite. For example, canonical Image Workspace identity:

```text
surface_id = image
scope_id = image_workspace
project_id = none
```

can still read current M9 rows stored as:

```text
surface = image
project_id = image
```

The storage translation is compatibility-only and must not be presented as delivery-project semantics.

## Provenance

Every gateway item records its origin lane and adapter metadata. Source-backed knowledge/Guide items can also carry citation metadata. Inspector/Control Center diagnostics expose:

- gateway trace ID;
- adapter status and trace IDs;
- candidate/result counts;
- duplicate count;
- source lane;
- source ID/path;
- canonical identity and current compatibility memory filter.

Normal chat should not expose those diagnostics unless the user asks for technical proof.

## Compatibility APIs

Older retrieval helpers/routes remain callable during migration, but normal Assistant chat no longer uses them as independent parallel searches. Source-grounded Assistant compatibility calls now delegate to the Retrieval Gateway's Knowledge adapter and project the established response shape.

## Phase 6 scope-priority extension

The same gateway now carries a `scope_policy` and bounded `retrieval_targets` plan. General can expand into a relevant surface/project when the query calls for it, surface scopes can reuse General durable memory, and generic recall questions can perform bounded non-Roleplay discovery. Detailed Roleplay memory still requires an explicit registered sandbox.

See `guides/06_ASSISTANT/scope_priority_retrieval.md`.

## Current phase boundary

Phase 6 still does **not** migrate Project Brain/Scope Knowledge into Unified Memory, rewrite existing SQLite IDs, consolidate background memory jobs, or migrate Roleplay's own non-Assistant runtime controller onto the Assistant gateway.

## Project-document grounding note

The Retrieval Gateway can retrieve canonical Project Brain fragments through Unified Memory, but a high retrieval score does not by itself prove that every detail inside a broad fragment answers the user's exact question.

For canon-heavy project questions, treat retrieved source text as evidence to be checked for the requested entity/detail. If the current retrieved context does not establish the answer, the safe behavior is to report that the project context is insufficient rather than invent a detail from nearby text or another Scope.

## NKB-2 codebase retrieval note (2026-09-26)

The `knowledge_index` lane may select `code_audit` for Neo-development queries and can return `neo_codebase` chunks with source citations. Do not interpret that source as a complete repository model: the current code registry has root/extension/file-size limits, Python declarative regions can be under-indexed, current JavaScript uses fallback line chunks, and tests can rank alongside runtime implementation.

For risky implementation questions, retrieved code is a shortlist/evidence locator; the authoritative repository source should be resolved before changing behavior. See `guides/06_ASSISTANT/codebase_knowledge_audit.md`.



## NKB-3 documentation knowledge note (2026-09-26)

Neo currently has two different documentation retrieval paths: built-in Guides are selected as whole documents and expose a short opening excerpt, while `neo_system_records` are indexed as heading-based Knowledge chunks. These paths are source-backed, but they do **not** yet implement a universal current/historical/superseded authority model.

For normal usage help, current built-in Guides remain the preferred explanation surface. For implementation/debug questions, a retrieved Guide or System Record should be treated as supporting evidence and checked against current runtime/source evidence when the claim depends on implementation truth. Historical/superseded records must not be blended into a current answer merely because they retrieve well.

Large Guides can contain useful sections beyond the excerpt currently returned by Guide search, so absence from the opening excerpt is not proof that the Guide lacks the detail. The future Documentation Knowledge Adapter is expected to retrieve section-level evidence with explicit lifecycle/provenance. See `neo_system_records/01_ARCHITECTURE/NEO_UNIFIED_BRAIN_NKB3_DOCUMENTATION_KNOWLEDGE_AUDIT_20260926.md`.

## NKB-4 authority resolution boundary

The Retrieval Gateway's relevance score is not semantic authority.

Future retrieval should conceptually separate:

```text
scope / identity
→ intent + claim type
→ allowed evidence roles
→ lexical / dense / structured retrieval
→ rank fusion / rerank
→ lifecycle / conflict / supersession / authority adjudication
→ bounded evidence packet
```

A highly relevant test, historical System Record, generated output, or inferred memory must not outrank the evidence role appropriate to the question merely because the reranker prefers its text.

Authority is task-sensitive: runtime code for current implementation, current Guides for usage, validation evidence for test-status questions, user-approved/primary project sources for canon, and native output records for generation metadata.

## NKB-5 universal knowledge reference boundary

NKB-5 defines the shared interoperability contract between retrieval and native/source authority.

Future Gateway candidates should resolve through four different identities rather than treating the current chunk ID as the knowledge itself:

```text
native authority ID
stable knowledge_id
immutable revision_id
disposable projection_id
```

FTS/vector/Chroma results are projections. For current implementation, project canon, Roleplay canon/state, native generation metadata, current documentation, and similar strong claims, the selected projection should carry a resolvable knowledge/evidence reference so the owning adapter can hydrate the current authoritative source before packet assembly.

The universal primitive set is:

```text
Project | Source | Object | Fact | Edge | Fragment | Asset | Event | Alias | Revision | Evidence
```

The Gateway should eventually select adapters/sources by intent, retrieve relevant projections/structured records, then pass hydrated Evidence to NKB-4 authority adjudication. It should not require every adapter to use vector search when a deterministic native lookup can answer the query.

See `guides/06_ASSISTANT/universal_knowledge_contract.md` and `neo_system_records/01_ARCHITECTURE/NEO_UNIFIED_BRAIN_NKB5_UNIVERSAL_KNOWLEDGE_CONTRACT_20260926.md`.


## NKB-8 Unified Retrieval Planner

NKB-8 changes the Gateway from a fixed multi-adapter merge into an intent-aware evidence planner. The normal Assistant path now performs:

```text
query / claim analysis
→ lane selection
→ candidate retrieval
→ weighted RRF
→ post-fusion rerank
→ task-sensitive authority adjudication
→ strong-source hydration
→ verified shortlist
```

The Gateway can now include `project_structured` and `native_authority` lanes in addition to compatibility `unified_memory`, `knowledge_index`, and `guide_index`. Generic words such as `route` or `file` no longer independently trigger Neo code retrieval.

Raw scores from different adapters are not added together; weighted reciprocal-rank fusion combines lane ranks before the configured reranker. Authority is evaluated after relevance. Strong Project facts can hydrate the NKB-7 original file/revision before acceptance.

Gateway diagnostics now expose `planner`, `planner_trace_id`, `fusion`, `known_state`, `fail_closed_recommended`, and `authority_rejections`. NKB-9 still owns final bounded packet assembly, so compatibility Context Pack sections have not yet been removed.

See `guides/06_ASSISTANT/unified_retrieval_planner.md` and `neo_system_records/01_ARCHITECTURE/NEO_UNIFIED_BRAIN_NKB8_UNIFIED_RETRIEVAL_PLANNER_20260926.md`.


## NKB-9 Context Packet boundary

NKB-9 is implemented. The Retrieval Gateway remains the single search/planning boundary, while `neo.assistant.context_packet.v1` is now the single normal provider-visible retrieval/context payload.

The packet preserves NKB-8 authority/citation state, adds compact active scope and live-state context, and folds current-turn document text into the same bounded payload. Compatibility Project Brain/Scope/Guide/Memory projections stay available to Inspector but are not compiled beside the packet.

See `guides/06_ASSISTANT/context_packet_architecture.md`.
