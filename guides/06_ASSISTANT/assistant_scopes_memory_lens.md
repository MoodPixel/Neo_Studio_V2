---
guide_id: assistant.scopes_memory_lens
title: Assistant Scopes and Memory Lens
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
  - scopes
  - memory_lens
  - context
  - inspector
priority: 96
version: 1
updated: 2026-08-09
---

# Assistant Scopes and Memory Lens

Phase 11 consolidates the Assistant UI around the architecture already locked in Phases 1–10. It changes presentation and inspection boundaries; it does not create another memory engine or change the universal Assistant task contract.

## Scopes, not Projects

The Assistant subtab historically stored the route ID `projects`. The canonical user-facing concept is now **Scopes**.

A Scope is an Assistant context priority/sandbox. A Delivery Project is a real client/work project. The two may be linked, but they are not the same object.

For compatibility, saved UI state and historical routes using `projects` or `project_context` continue to resolve to the Scopes view. New UI copy must use Scope-first language.

## Scopes view

The Scopes view owns normal user interaction with:

- active Scope selection;
- Scope name/type/description/notes;
- canonical Scope, surface, and linked Delivery Project identity;
- Project Brain capture/import/rebuild controls;
- persistent Phase 10 Project Brain job progress.

Admin → Assistant / Operator remains a read-only diagnostic Scope readout rather than a second normal Scope editor.

## Memory Lens

Assistant → Memory is now a user-facing **Memory Lens**, not a list of manual captures.

It may show:

- canonical memory relevant to the active Scope;
- General memory visible to the active non-Roleplay Scope;
- applied durable memories;
- pending durable-memory review candidates as read-only status;
- manual memory pins and Scope Knowledge;
- recent retrieval summaries;
- recent/active memory jobs.

The Memory Lens is an inspection surface. Approve/reject/governance actions remain in **Admin → Memory**.

## Context and Inspector

Normal Chat should stay focused on the user's task. Technical retrieval/compiler/job proof belongs elsewhere:

- **Context**: Scope Knowledge authoring plus context/retrieval proof and Context Pack inspection;
- **Inspector**: runtime diagnostics, Prompt Compiler proof, Retrieval Gateway details, memory diagnostics, and raw Assistant state.

Do not move raw traces, source paths, compiler diagnostics, or memory-engine internals back into normal Chat sidebars merely because they are available.

## Memory Lens source contract

`GET /api/assistant/memory-lens` returns `neo.assistant.memory_lens.phase11.v1`.

The payload is assembled from canonical Phase 5–10 authorities rather than creating another store:

- Unified Memory observability;
- Phase 9 durable writeback state;
- Phase 10 memory jobs;
- recent Assistant Control Center traces;
- manual captures and Scope Knowledge compatibility projections.

Canonical identity from Phase 1 and Scope Priority from Phase 6 remain authoritative.

## Compatibility

The internal/persisted Assistant tab route remains `projects` for compatibility. `scopes` is a canonical alias for UI/action routing. Do not rename persisted data keys destructively in Phase 11.

Project Brain data, Unified Memory rows, writeback rows, job rows, chats, Scope records, and Delivery Project records are unchanged by this UX phase.
