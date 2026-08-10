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
version: 2
updated: 2026-08-09
---

# Assistant Single Retrieval Gateway

Phase 5 gives normal Neo Assistant turns one retrieval boundary: the **Retrieval Gateway**.

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
Context Pack reuses the exact same gateway result
        ↓
Prompt Compiler includes the gateway context once
```

Context Pack still exposes compatibility projection sections such as `built_in_guides`, `memory_engine`, and `source_grounding` for Inspector/older routes, but those are **derived from the same gateway result**. They do not perform additional retrievals and the Prompt Compiler suppresses the duplicate projections when the canonical gateway section is present.

## Adapter balance

The gateway first removes exact normalized duplicate content across adapters. If the requested context limit can hold every active adapter, it reserves one best unique item per active lane before filling remaining slots by global score.

This prevents a high-ranked Guide from crowding a directly matched saved memory out of a small context window, while still allowing globally stronger results to fill the rest of the budget.

This adapter balance remains active in Phase 6. Scope expansion is now handled by the separate **Scope Priority, Not Scope Prison** policy; adapter balancing does not itself decide which scopes are eligible.

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
