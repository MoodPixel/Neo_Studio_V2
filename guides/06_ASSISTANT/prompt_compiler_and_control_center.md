---
guide_id: assistant.prompt_compiler
title: Assistant Prompt Compiler and Control Center Isolation
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
  - control_center
  - prompt_compiler
  - context
  - diagnostics
priority: 99
version: 2
updated: 2026-08-09
---

# Assistant Prompt Compiler and Control Center Isolation

Neo Assistant has two different information layers:

1. **internal orchestration** used by Control Center, Brain Workspace, validation, retrieval diagnostics, and future writeback;
2. **provider-visible prompt context** used by the text model to answer the user.

Phase 4 makes that boundary explicit.

## Provider-visible flow

```text
Control Center structured plan
        +
Context Pack / selected memory / attachments
        ↓
Assistant Prompt Compiler
        ↓
clean system contract
turn behavior directive
relevant bounded context
context-use constraints
conversation messages
        ↓
text provider
```

The provider does **not** receive raw Brain Workspace messages, raw Control Center prompt blocks, or the Context Pack's monolithic prompt block.

## What remains internal

Control Center may continue to store detailed information such as:

- canonical identity;
- behavior mode and intent;
- selected memory candidates;
- validation plans;
- writeback plans;
- retrieval diagnostics;
- full prompt-contract objects;
- trace metadata.

These are Inspector/Admin concepts. They must not become a user-facing response format.

## Context compilation

The compiler keeps useful context while avoiding common duplication:

- the current user message comes from conversation history only;
- thread history is not copied a second time from Context Pack;
- persona/control text is not copied from Context Pack;
- live surface context, Scope context, the canonical Phase 5 Retrieval Gateway result, Project Brain context, Scope Knowledge, and uploaded document text can still be included;
- Context Pack compatibility projections (`source_grounding`, `built_in_guides`, `memory_engine`, `admin_memory`) are suppressed from provider-visible context when the canonical Retrieval Gateway section is present;
- repeated context is de-duplicated before dispatch;
- context is bounded by retrieval-profile-aware character budgets.

The compiler also removes lines containing known internal orchestration schema markers from provider-visible retrieved context. The original unsanitized Context Pack remains available for diagnostics.

## Structured output

If the user explicitly requests JSON, YAML, XML, CSV, code, or another structured artifact, Neo preserves that requested output format. Prompt isolation does not force everything into prose.

## Inspector proof

Each compiled turn records prompt-compilation diagnostics on the Assistant Control Center trace, including:

- compiled message/system character counts;
- included context sections;
- duplicate/context-internal lines removed;
- whether raw Control Center or raw Context Pack prompt blocks were forwarded;
- a bounded internal-plan preview;
- a bounded compiled-model-prompt preview.

Normal chat does not display these diagnostics.

## Retrieval boundary after Phase 5

Phase 5 adds the Single Retrieval Gateway. Assistant Control Center performs the retrieval once, Context Pack reuses that exact result, and this compiler includes the gateway context once while suppressing compatibility projections. Raw retrieval diagnostics remain internal.

Phase 6 still owns query-driven scope expansion and prioritization; prompt compilation must not implement that policy itself.
