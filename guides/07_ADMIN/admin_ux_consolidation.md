---
guide_id: admin.ux_consolidation
surface: admin
title: Admin UX Consolidation
scope: built_in
applies_to:
  - admin
  - memory
  - memory_engine
  - projects
  - assistant_operator
tags:
  - admin
  - ux
  - ownership
  - memory
  - projects
priority: 97
version: 1
updated: 2026-08-09
---

# Admin UX Consolidation

Phase 12 makes the Admin interface follow the ownership contract established in Phase 2 and the runtime systems implemented through Phase 11.

## The four important Admin boundaries

### Admin → Memory

Use for memory **content governance**:

- Inspector;
- Search + Citations;
- policy defaults;
- Durable Review;
- conflicts/canon;
- consolidation;
- retention;
- governance diagnostics.

`Durable Review` is intentionally separate from `Policies`: policy configuration describes how memory should behave, while Durable Review makes individual approve/reject decisions.

### Admin → Memory Engine

Use for memory **infrastructure**:

- text bridge;
- embeddings and reranker;
- vector store;
- retrieval profiles;
- sources;
- indexing;
- unified memory background jobs;
- infrastructure diagnostics.

The historical child route `index_jobs` remains readable, but its visible label is **Background Jobs** because Phase 10 made `neo_memory_jobs` the persistent authority for Project Brain rebuild, embedding/reindex, consolidation, bulk writeback, and compatibility indexing.

Memory Engine does not own Search + Citations, durable review, or generation sampling.

### Admin → Delivery Projects

Use for actual client/work delivery objects:

- delivery workspace;
- asset tray and handoffs;
- briefs;
- timeline;
- milestones/deliverables;
- review/approval;
- packages;
- read-only linked-memory context.

Linked Memory is a relationship/readout view. It does not rebuild Project Brain or repair memory indexes. Project Brain ingestion belongs to the Assistant/shared memory pipeline; memory infrastructure belongs to Memory Engine.

### Admin → Assistant / Operator

Use for orchestration and execution visibility:

- read-only Scope Readout;
- Operator execution/confirmation surface;
- Voice input bridge;
- Internet/API permission flow;
- Control Center traces;
- tools and permission profiles;
- execution ledger;
- diagnostics.

Admin does **not** activate or edit the user's Assistant Scope. The historical Admin activation callable remains a compatibility shim that redirects to **Assistant → Scopes** instead of mutating Assistant state.

## Compatibility aliases

Phase 12 keeps old route/state values readable:

```text
memory_engine     -> engine
delivery_projects -> projects
project_workspace -> projects
assistant         -> assistant_operator
operator          -> assistant_operator
```

Child-tab compatibility includes:

```text
Memory:          writeback/review -> durable_review
Memory Engine:   background_jobs/jobs -> index_jobs
Delivery Projects: linked_memory -> project_memory
Assistant/Operator: scope_readout -> assistant_workspaces
```

The visible language follows current ownership; route IDs remain compatibility-safe.

## Dashboard ownership map

The Admin Dashboard exposes the Phase 12 ownership contract so users can jump to the owning area rather than hunting through duplicate controls.

The canonical backend payload is:

```text
GET /api/admin/ux-contract
schema: neo.admin.ux_consolidation.phase12.v1
```

The same payload is included in `/api/admin/control-center` for diagnostics.

## Post-Phase-12 ownership update — Operator Consolidation (Phase 13)

Phase 13 is now implemented. Assistant/Control Center owns action understanding and emits structured action requests. Operator no longer owns general human-intent classification or source/profile selection. Admin → Assistant / Operator therefore remains the place to inspect:

- tool permission profiles;
- confirmation requirements;
- structured execution requests;
- execution receipts/proof;
- Tool Execution Ledger records;
- Voice/Internet bridges and execution diagnostics.

Normal action understanding stays in Assistant/Control Center. Admin must not become a second action-planning UI.

## What Phase 12/13 still do not change

- Specialist model routing/migration is unchanged; Phase 14 owns that work.
- Existing Delivery Project routes/data are preserved.
- Existing memory rows, job rows, Scope records, and Project Brain data are not rewritten.
