---
guide_id: assistant.grounding_modes.nkb10
surface: assistant
scope: built_in
title: Assistant Grounding Modes
summary: Explains how NKB-10 separates task behavior from evidence-grounding behavior so canon recall fails closed, analysis can reason without rewriting facts, and creative work can extend projects without silently changing established canon.
tags: [assistant, grounding, canon, factual, analysis, creative, development, roleplay, evidence, hallucination]
applies_to: [assistant, general, roleplay]
priority: 100
version: 1
updated: 2026-09-27
status: current
---

# Assistant Grounding Modes

NKB-10 adds a grounding policy after NKB-8 retrieval and NKB-9 packet assembly.

Grounding mode is **separate from Assistant behavior mode**.

Behavior modes such as `COMPLETE`, `RECALL`, `ANALYZE`, `ADVISE`, `ACT`, and `CONTINUE` describe what Neo should do. Grounding modes describe what Neo is allowed to claim from the available evidence.

```text
User turn
  ↓
Behavior mode
  +
NKB-8 intent/claim type
  +
NKB-9 evidence state
  ↓
NKB-10 grounding mode
  ↓
provider grounding contract
  ↓
post-generation grounding enforcement
```

## Modes

### CANON

Used for source-dependent Project/canon recall.

Rules:

- answer established project facts only from the Context Packet or explicit current-turn source material;
- do not invent missing lore;
- do not transfer one character/entity's attributes to another;
- do not merge nearby fragments into a new fact;
- when evidence is missing, report that the detail is not established;
- when authoritative sources conflict, report the conflict rather than selecting a convenient answer.

For strict `not_established` canon recall with no current-turn direct source, Neo deterministically replaces the provider output with a fail-closed answer.

### FACTUAL

Default for ordinary factual/recall work that is not project canon, Neo development, roleplay, or a creative/analysis task.

Source-dependent facts must stay grounded. Ordinary general knowledge is not forced to fail closed unless the Retrieval Planner marks the claim source-dependent.

### ANALYSIS

Allows interpretation and inference while keeping the factual base separate.

For Project analysis, current canon remains binding. Neo may explain implications or likely motivations, but it must not convert that analysis into an established project fact.

### CREATIVE

Allows invention when the user asks for creative work or creative extension.

Existing canon remains binding unless the user explicitly asks to rewrite it. New material must not be represented as though it already existed in the source/history.

### DEVELOPMENT

Used for Neo implementation, Admin/runtime diagnostics, validation, and source-dependent development questions.

For current behavior:

- verified runtime source is implementation authority;
- tests are validation evidence;
- Guides/System Records can provide usage/design/history evidence;
- Neo must not invent file paths, symbols, routes, settings, APIs, schemas, test results, or runtime behavior.

Source-dependent `not_established` current-runtime/validation claims fail closed.

### ROLEPLAY

Preserves roleplay canon, character identity, relationships, knowledge visibility, and scene state.

Roleplay recall fails closed when canon/history is absent. Active scene generation may create new scene-local actions and dialogue when consistent with canon, but it may not retroactively manufacture memories or character knowledge.

## Deterministic fail-closed guard

Prompt instructions alone are not treated as sufficient protection for strict source-dependent recall.

After provider generation, NKB-10 checks the resolved grounding policy. When strict mode requires evidence and NKB-8/NKB-9 report `not_established` with no current-turn direct document source, Neo discards the provider text and returns the mode-specific fail-closed response.

This works for both normal and streaming Assistant turns because streaming provider tokens are already buffered until Neo's output guard completes.

## Current-turn documents

A document attached directly to the current Assistant turn is direct user evidence, not historical memory. Its presence allows the model to answer a strict source question even when persistent retrieval has no established result.

The model is still instructed to stay within that supplied material rather than fill gaps from generic model knowledge.

## Conflicts

`unresolved_conflict` is not treated as `not_established`.

When conflicting authoritative evidence exists, the model receives that state and must describe the conflict. NKB-10 does not replace it with the missing-evidence fallback.

## Durable memory

NKB-10 does not promote assistant-generated creative content into canon.

Existing durable Assistant writeback candidates are derived from explicit user statements such as preferences, explicit memory requests, or confirmed project decisions. NKB-10 also records the grounding mode with those candidates for later review/diagnostics.

## Diagnostics

Runtime endpoints:

```text
GET  /api/assistant/grounding-modes/status
POST /api/assistant/grounding-modes/resolve
```

Prompt compiler diagnostics include:

- `grounding_mode`;
- full resolved grounding policy;
- whether deterministic fail-closed replacement is armed;
- output-guard grounding enforcement results.

## Project Sandbox defense-in-depth — 2026-09-27

A user-created `project_sandbox` is an implicit project domain. Normal recall inside it defaults to **CANON** grounding even if upstream intent metadata unexpectedly falls back to `general_recall/general_fact`. This is a safety backstop, not a replacement for correct NKB-8 classification.

Prior Assistant prose is never sufficient canon evidence by itself. When `known_state=not_established` and there is no current-turn direct source, strict Project recall returns the deterministic project-context fallback rather than generic model knowledge.


## Established Project evidence must be answered — 2026-09-27

For CANON turns where `known_state=established` and verified packet evidence exists, that evidence is explicitly treated as user/project-provided source material that the provider is allowed and expected to use. The model must not refuse merely because the evidence arrived through Neo's Context Packet or describe it as `internal model data`.

A narrow output guard detects that specific established-evidence meta-refusal, performs one evidence-focused correction pass, and blocks a repeated refusal rather than inventing an answer in runtime code.

## Unresolved-question grounding — 2026-09-27

CANON mode distinguishes a verified source passage from a verified affirmative claim. Evidence marked as unresolved-question/explicit-unknown or otherwise non-affirmative may establish that the Project leaves something open, but it must never be converted into a positive fact. `explicit_unknown` strict recall fails closed with an unresolved-state answer instead of invented lore.
