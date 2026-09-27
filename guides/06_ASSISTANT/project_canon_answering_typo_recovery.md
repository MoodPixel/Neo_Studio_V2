---
guide_id: assistant.project_canon_answering_typo_recovery
surface: assistant
scope: built_in
title: Project Canon Answering and Typo Recovery
summary: Explains how Project Sandbox recall uses verified project evidence directly, handles misspelled project terms conservatively, and keeps query recovery visible in Knowledge Inspector.
tags: [assistant, project, canon, grounding, typo, fuzzy, phrase, retrieval, inspector]
applies_to: [assistant]
priority: 101
version: 1
updated: 2026-09-27
status: current
---

# Project Canon Answering and Typo Recovery

Inside a user-created Project Sandbox, ordinary recall is treated as project canon recall.

When NKB-8 reports `known_state=established` and NKB-9 contains verified Project evidence, Neo is expected to answer directly from that user/project source material. Project evidence is not hidden model data and is not a reason to refuse.

If a local model incorrectly refuses because the answer came from Project context, Neo performs one evidence-focused correction pass. If the model repeats the same meta-refusal, Neo blocks it and reports that verified evidence was found but the active model failed to produce a grounded answer.

## Typo recovery

Project retrieval uses a conservative vocabulary derived only from the active Project. Sources include entity/object labels, explicit aliases, document headings, and glossary/definition terms.

Neo preserves the original user query and can add a separate retrieval variant when a Project-local term is a strong spelling match.

Examples:

```text
who is grat soul?  -> search also for "Great Soul"
who is gret sol?   -> search also for "Great Soul"
who is elias rowen? -> search also for "Elias Rowan"
```

The user message and stored canon are never rewritten.

Similarity alone is not enough: Neo also requires compatible phrase shape and an ambiguity margin so distinct terms such as `Eldraeth` and `Elderwode` are not merged casually.

## Project packet density

For normal Assistant-surface Project Sandbox turns, the provider packet does not include the full Project source catalog/live source-pointer inventory. That inventory remains visible in Inspector, while the model receives compact Scope identity plus the selected evidence relevant to the current question.

## Inspector

Knowledge Inspector shows:

- original query;
- detected entities and query variants;
- effective retrieval query;
- typo/phrase recovery match and confidence;
- Context Packet source-catalog suppression;
- established-evidence refusal repair/blocking state.

This makes typo recovery and final grounding behavior auditable instead of silent.

## Epistemic safety + answerability update — 2026-09-27

Typo recovery now propagates the resolved canonical term into the effective retrieval query rather than leaving misspelled tokens as equal lexical signals. The original message is still preserved unchanged for UI/audit.

Project evidence is also passage-typed. Open-question/speculative passages can explain uncertainty but cannot establish affirmative canon. For identity/definition questions, direct glossary definitions and entity/profile headings receive answerability preference over mere mentions.
