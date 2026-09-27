---
guide_id: assistant.context_packet.nkb9
surface: assistant
scope: built_in
title: Context Packet Architecture
summary: Explains how NKB-9 turns the NKB-8 verified shortlist, live surface state, compact scope metadata, and current-turn document text into one bounded provider-visible Assistant context packet.
tags: [assistant, context, packet, retrieval, grounding, project, citations, inspector, prompt]
applies_to: [assistant, general]
priority: 100
version: 2
updated: 2026-09-27
status: current
---

# Context Packet Architecture

NKB-9 defines the single provider-visible retrieval context used by normal Neo Assistant chat.

The Retrieval Gateway and Unified Retrieval Planner still decide **which evidence survives**. NKB-9 does not search again and does not create another knowledge database. It packages the already-selected evidence into one bounded packet before the model call. NKB-10 now consumes the packet's evidence state to resolve CANON/FACTUAL/ANALYSIS/CREATIVE/DEVELOPMENT/ROLEPLAY grounding behavior.

```text
User turn
  ↓
NKB-8 Retrieval Planner
  ↓
verified/adjudicated shortlist
  ↓
NKB-9 Context Packet
  + compact active scope
  + relevant live surface state
  + current-turn document text
  + explicit legacy workspace context only when requested
  ↓
Prompt Compiler
  ↓
Provider/model
```

## One provider-visible retrieval payload

When a NKB-9 packet is present, normal Assistant generation compiles only the packet as retrieval/context evidence.

Compatibility sections remain available to Inspector and older UI/routes:

- Retrieval Gateway rendering;
- Project Brain snapshot/index projection;
- Scope Knowledge/context-item projection;
- built-in Guide projection;
- Unified Memory projection;
- source-grounding projection;
- legacy Admin-memory projection.

They are **not compiled beside the packet**.

This removes the earlier failure mode where one turn could contain the same Project fact through several differently formatted context lanes.

## Packet types

A packet can contain:

### Active scope

Compact workspace/project identity, description and notes. This is orientation metadata, not independent factual proof.

### Live state

Current surface state supplied by the active Neo tab. Live state is kept separate from historical/indexed generation memory.

### Current-turn direct inputs

Document text uploaded directly in the current Assistant turn is inserted into the packet before lower-priority retrieved evidence is budgeted. Vision/image attachments continue through the provider's multimodal message path.

### Verified/retrieved evidence

The selected NKB-8 items preserve:

- source lane;
- evidence role;
- source integrity;
- lifecycle/conflict state;
- bracket citation index when available;
- source locator;
- provider-visible evidence text.

NKB-9 does not invent missing facts or convert `not_established` into synthetic evidence.

## Bounded profiles

The packet uses the Assistant retrieval profile as a hard character budget:

- `fast`: 7,000 chars;
- `smart`: 12,000 chars;
- `deep`: 18,000 chars.

Current-turn direct document text and compact current state are budgeted before retrieved evidence. Evidence is kept in the authority/ranking order returned by NKB-8; lower-priority items are clipped or dropped when necessary.

The packet records candidate/selected/dropped counts for Inspector.

## Citations

Bracket citation numbers produced by the Retrieval Gateway are preserved inside packet evidence. This lets the model use the same source-backed citation identity selected before packet assembly.

A citation is evidence provenance, not permission to treat a stale/superseded source as current. NKB-8 authority adjudication happens before packet construction.

## Known state

The packet carries the Retrieval Planner's state:

- `established`;
- `not_established`;
- `unresolved_conflict`;
- `fail_closed_recommended`.

NKB-9 exposes these signals to the model boundary and NKB-10 now enforces the final CANON/FACTUAL/ANALYSIS/CREATIVE/DEVELOPMENT/ROLEPLAY grounding-mode contract.

## Prompt compiler behavior

When `neo.assistant.context_packet.v1` is available:

1. current-turn text attachments are folded into the packet;
2. packet budget is recalculated;
3. internal orchestration-marker sanitization is applied;
4. exactly one `context_packet` context section is compiled;
5. compatibility Project/Guide/Memory sections stay Inspector-only.

Older saved Context Packs without NKB-9 data still use the previous compatibility compiler path.

## Diagnostics

`GET /api/assistant/context-packet/status` reports the packet contract and profile budgets.

`GET /api/assistant/context-pack-preview` includes the current packet preview alongside compatibility Inspector sections.

Prompt Compiler diagnostics expose:

- packet schema/ID;
- rendered character count;
- selected evidence count;
- direct-input count;
- compatibility-context suppression state.

## Phase boundary

NKB-9 solves evidence **packaging**, not final factual/canon behavior.

NKB-10 must use packet `known_state`, claim type and creative intent to implement explicit grounding modes such as CANON, FACTUAL, ANALYSIS and CREATIVE.


## NKB-10 grounding handoff

The packet carries `known_state` and `fail_closed_recommended`; NKB-10 converts those fields plus planner intent/claim type into a grounding mode. Packet assembly itself still does not decide whether the final answer should fail closed or allow creative/inference behavior.

For strict source-dependent `not_established` turns with no current-turn document source, NKB-10 can deterministically replace the provider answer with a fail-closed response. See `guides/06_ASSISTANT/assistant_grounding_modes.md`.

## Project source-catalog suppression — 2026-09-27

For normal user-created `project_sandbox` turns on the Assistant surface, the full live Project source catalog/pointer inventory remains Inspector-visible but is suppressed from the provider-visible NKB-9 packet. This protects the bounded packet budget for actual selected canon evidence and avoids confusing local models with backend/source inventory metadata.
