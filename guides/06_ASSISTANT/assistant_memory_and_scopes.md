---
guide_id: assistant.memory_scopes
title: Assistant Memory, Scopes, and Project Brain
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
  - scope
  - project_brain
  - context
priority: 95
version: 10
updated: 2026-08-09
---

# Assistant Memory, Scopes, and Project Brain

## What a Scope is

An Assistant Scope is an internal context sandbox and retrieval-priority area. It tells Neo what context should be considered first.

Built-in scopes include General, Image Workspace, Video Workspace, Voice Workspace, Prompt & Captioning Workspace, and Roleplay Workspace. Custom scopes can be created for clients, workflows, or other focused contexts.

A Scope is **not** the same thing as a client/delivery Project.

## Canonical identity (Phase 1)

Neo now carries three separate context identities:

```text
surface_id = where the information or task originates
scope_id   = which Assistant context should be prioritized/sandboxed
project_id = the real client/work/delivery project, when one exists
```

Example:

```text
surface_id = image
scope_id   = image_workspace
project_id = heart_soul_2_ball
```

Historical Assistant storage still uses a field named `project_id` for Scope IDs such as `image_workspace`. Phase 1 keeps that legacy field readable but attaches canonical identity alongside it. Existing M9 surface memories such as `surface=image / project_id=image` are also translated through a compatibility filter rather than being mistaken for a real delivery Project.

When inspecting payloads/traces, treat `identity.surface_id`, `identity.scope_id`, and `identity.project_id` as product semantics. Treat `identity.compatibility.*` as temporary storage translation only.

## General Scope

General is the cross-surface Assistant workspace. It is intended for uncategorized questions, planning, and cross-surface coordination.

General should be able to answer questions using relevant memory from any Neo surface when the request calls for it. Memory remains scoped and filtered; Neo should not dump every stored memory into every chat.

## How to add useful memory today

### Save Scope

Use the Scope Description and Notes for durable rules about what the scope represents and what Neo should prioritize.

Good examples:

- what a client/project is;
- permanent style goals;
- workflow boundaries;
- important scope-specific constraints.

Do not use Scope Notes as an unlimited history dump.

### Save to Scope Knowledge

Use Context → Save to Scope Knowledge for deliberate structured information that should remain available inside the active scope. From Phase 7 this writes retrieval-authoritative knowledge into Unified Memory first while preserving the existing Assistant context record as a compatibility/UX projection.

Examples:

- client requirements;
- approved creative direction;
- durable workflow notes;
- important decisions.

### Save selected as memory

Use Chat → Save selected as memory for a specific durable fact/preference/decision from conversation. Phase 7 writes the canonical copy into Unified Memory while keeping the historical local capture/event bridge readable for compatibility.

Examples:

- a confirmed preference;
- a successful recurring setting;
- a confirmed bug/fix pattern;
- a client approval/decision.

Avoid saving greetings or one-off chatter as durable memory.

### Capture Current State

Use Project Brain → Capture Current State after an important surface state is worth remembering, such as a successful Image setup or a finalized workflow configuration. The snapshot is canonically ingested and equivalent repeated captures are deduplicated.

It is a manual pin/snapshot. It is not necessary after every minor change.

### Index Project Data

Use this when Neo has produced enough new saved output metadata/results that you want recent historical settings/results represented in memory. Phase 7 canonically ingests each indexed metadata row/fact while retaining the readable Project Brain index projection. General can coordinate across surfaces; custom Assistant/client scopes do not silently scan every creative surface.

### Upload Project Files

Use this for persistent project/reference documents. Phase 7 reuses the shared Assistant document extractor, so text-like files, PDF, and DOCX can become canonical Project Brain knowledge with source/content provenance. The original uploaded file remains stored.

### Rebuild Project Brain

Phase 7 makes this a real idempotent pipeline: scan allowed metadata, extract documents, ingest snapshots/indexes/Scope Knowledge/manual captures, deduplicate, consolidate, update SQLite FTS, validate canonical memory counts, and write a rebuild report. Long-running rebuild/embedding/reindex work now uses the Phase 10 unified Memory Job Service. Rebuild is backgrounded by default and exposes persistent phase/percent/item-count progress, Cancel, Retry, and reconnection after rerender/navigation.

## How to verify what Neo can see

Use **View Context Pack** when debugging Assistant recall. It is the best current inspection path for checking which Scope, Project Brain, Guide, live surface, and memory sections are entering the Assistant context.

## Retrieval after Phase 6

Normal Assistant chat uses one **Retrieval Gateway** rather than independently querying the document/chunk Knowledge Index and Unified M9 memory. Phase 6 adds **Scope Priority, Not Scope Prison** on top of that gateway.

General starts with General durable memory and expands only when the query indicates another surface/project or when an explicit recall request needs bounded non-Roleplay discovery. Surface/custom scopes keep local context first while still allowing relevant General memory and query-driven cross-surface context. Linked Delivery Projects are project-first. Detailed Roleplay memory requires an explicit registered universe/world/scene sandbox.

Phase 7 has now migrated Project Brain and manual Scope Knowledge writes to canonical Unified Memory ingestion. Legacy JSON/snapshot/index/context files remain inspectable compatibility projections and rebuild sources; normal memory retrieval still enters through the Retrieval Gateway.

See `guides/06_ASSISTANT/retrieval_gateway.md`, `guides/06_ASSISTANT/scope_priority_retrieval.md`, and `guides/06_ASSISTANT/project_brain_canonical_ingestion.md`.

## Admin ownership after Phase 2

Assistant Scope selection/editing is owned by the **Assistant surface**. Admin → Assistant / Operator now exposes a **read-only Scope Readout** for diagnostics, memory preview, and Control Center traces; it is not a second place to activate/edit the user's normal Scope.

Admin → Projects is now explicitly **Delivery Projects**. A Delivery Project can later link to an Assistant Scope, but it is a real client/work project rather than an Assistant context sandbox.

For memory administration:

- Admin → **Memory** owns remembered content/governance and Search + Citations.
- Admin → **Memory Engine** owns retrieval/index infrastructure.
- Admin → **Backends** owns provider/model generation sampling.

See `guides/07_ADMIN/admin_ownership_and_memory.md`.
## Universal Assistant behavior after Phase 3

The active Scope controls context priority, not the kinds of tasks Neo Assistant is allowed to perform. Normal Assistant requests are handled through the Universal Assistant Contract.

A request can be creative writing, a recipe, social caption, client response, video script, code, analysis, advice, or another normal assistant task. Neo should perform the task directly rather than returning Control Center lanes or a plan to do the task later.

Broad behavior modes (`COMPLETE`, `RECALL`, `ANALYZE`, `ADVISE`, `ACT`, `CONTINUE`) are internal orchestration hints only. They do not limit subject matter.

See `guides/06_ASSISTANT/universal_assistant_contract.md`.



## Model-facing context after Phase 4

Scope, Project Brain, Guides, live surface context, M9-selected memory, and uploaded document text can still inform Assistant replies, but they now pass through the Assistant Prompt Compiler before provider dispatch.

The compiler deliberately does not forward the raw Brain Workspace/Control Center prompt blocks or the Context Pack monolith. It also avoids duplicating the current message/thread from Context Pack because the conversation already contains them. This isolation changes **prompt assembly**, not memory ownership or retrieval. Phase 5 still owns retrieval consolidation.

See `guides/06_ASSISTANT/prompt_compiler_and_control_center.md`.


## Phase 8 automatic surface memory

Project Brain is no longer the only way useful canonical memory enters Neo. Phase 8 adds the Unified Surface Memory Ingestion Registry for successful runtime work from Image, Video, Voice, Prompt/Captioning and other registered surfaces. This automatic history is selective: failed/running/queued events and ordinary Assistant chatter are not promoted into searchable durable fragments. Roleplay remains sandbox-owned and is replayed through its dedicated adapter rather than generic live writes.

Use Project Brain when you deliberately want to pin project/scope knowledge, documents, snapshots, or indexed historical data. Use normal surface workflows for automatic successful task history. Both are retrieved through the same Retrieval Gateway.
## Durable memory after Phase 9

Searchable history is not automatically a durable preference/fact. Phase 9 runs successful Assistant turns and successful registered surface events through the existing M11 writeback engine as **durable candidates**. Repeated low-risk workflow/settings patterns may auto-promote after independent support; user preference changes, project decisions, contradictions, cross-project claims, and canon-sensitive changes remain review-gated.

When a reviewed replacement is applied, the older durable fragment/fact is superseded so retrieval sees one active truth for the durable key. Ordinary chat remains non-durable. See `guides/06_ASSISTANT/durable_memory_writeback.md`.


## Background memory jobs after Phase 10

Long-running memory operations use the persistent `neo_memory_jobs` authority through `MemoryJobService`. Project Brain rebuild, memory consolidation, embedding/reindex, deliberate/bulk writeback, and the historical Roleplay vector-index job can share the same lifecycle and progress model.

Job rows persist across UI navigation. Worker threads remain process-local; a server restart marks unfinished running jobs as interrupted/failed and retryable rather than falsely claiming background continuation. Cancellation is cooperative at safe checkpoints. Tiny operations remain synchronous.

See `guides/06_ASSISTANT/background_memory_jobs.md`.


## Phase 11 Assistant UX

The user-facing Assistant tab now uses **Scopes** as the canonical label for the historical `projects` route. The route/storage alias remains readable; do not confuse an Assistant Scope with an Admin Delivery Project.

Assistant → **Memory** is now a Memory Lens backed by canonical Unified Memory, durable writeback state, recent retrieval proof, memory jobs, manual pins, and Scope Knowledge. It is for viewing what Neo remembers in the current context; durable approval/rejection and serious governance remain in Admin → Memory.

Assistant → **Context** owns Scope Knowledge authoring plus context/retrieval proof. Assistant → **Inspector** owns detailed runtime/compiler/retrieval/job diagnostics. Normal Chat intentionally avoids raw technical proof so the Universal Assistant remains focused on the user's task.

See `guides/06_ASSISTANT/assistant_scopes_memory_lens.md`.
