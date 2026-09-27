---
guide_id: assistant.public_project_ingestion.nkb7
surface: assistant
scope: built_in
title: Public Project Ingestion
summary: Explains how persistent Assistant Project files are converted into structured, source-backed searchable knowledge without requiring users to follow a Neo-specific schema.
tags: [assistant, project_brain, project, memory, ingestion, pdf, docx, markdown, canon, retrieval]
applies_to: [assistant, general]
priority: 100
version: 2
updated: 2026-09-26
status: current
---

# Public Project Ingestion

## You do not need a special Project schema

Persistent Assistant Project files can be ordinary documents such as PDF, DOCX, Markdown, text, HTML, JSON, CSV/TSV, PPTX, or XLSX.

Neo derives internal retrieval structure while keeping the original uploaded file as the source authority.

```text
Project file
→ structured extraction
→ contextual source fragments
→ explicit source-backed entities/facts where available
→ searchable Project evidence
```

## Persistent Project files are different from one-turn attachments

One-turn Assistant attachments still use bounded preview/extraction rules.

Files deliberately added to Project Brain use the persistent NKB-7 extraction path instead. Persistent Project text is not cut to the former ~24k-character Project indexing window, and text-extractable PDF ingestion is not limited to the first 40 pages.

Project upload byte limits still apply.

## Structure is preserved when possible

Neo keeps source location information such as:

- Markdown/document heading paths;
- text line ranges;
- PDF page/paragraph locations;
- DOCX headings/tables;
- spreadsheet sheets/row ranges;
- presentation slides;
- HTML nodes;
- JSON top-level sections.

This lets retrieval find a small relevant section instead of passing a large mixed document block to the model.

## Source-backed helpers are conservative

When a document explicitly defines information such as:

```text
## Elias Rowan
Aliases: Eli
Occupation: Secretary to Julian Mori
```

Neo may index the entity name, alias, and explicit key/value fact as retrieval helpers.

Neo does not treat unstated relationships, motives, personality traits, or generated guesses as source facts.

## Aliases help retrieval without rewriting the source

If a section explicitly establishes `Eli` as an alias for `Elias Rowan`, Neo can add that identity context to search projections for later fragments in the same section.

The cited/source fragment remains the original document text.

## Project documents are source-direct

Primary uploaded Project fragments are not replaced by generic consolidated summaries. This avoids blending facts from nearby characters/events in canon-heavy documents.

Derived summaries may exist elsewhere, but primary source evidence remains primary.

## Scope Knowledge no longer duplicates the document body

The compatibility Scope Knowledge entry created for an uploaded Project document is now a lightweight pointer/summary rather than another large copy of the document text.

That reduces duplicate prompt context while preserving Project Brain UI/history compatibility.

## Revisions and source verification

Neo tracks a stable document identity and an immutable content revision.

When a file changes, old active source projections are superseded as the new revision is ingested.

The Project evidence resolver can reopen the original file and report whether a stored reference is still `verified` or has become `stale`.

## What NKB-7 does not do yet

NKB-7 improves ingestion and evidence quality. It does not yet implement the final cross-source Assistant planner or strict fail-closed generation policy.

Those are later Unified Brain phases:

```text
NKB-8 → choose/fuse/authorize evidence
NKB-9 → build bounded Context Packets
NKB-10 → CANON / FACTUAL / ANALYSIS / CREATIVE grounding rules
```

If evidence is absent, future canon grounding should return `not_established` rather than inventing a project fact.


## NKB-8 retrieval handoff

NKB-8 now consumes NKB-7 explicit entities/aliases/facts through the `project_structured` lane. Structured matches keep the supporting Project fragment/native reference and, for strong Project-fact claims, can be hydrated back to the original uploaded file before authority acceptance.

If the current source hash/revision no longer matches, the evidence is marked stale and cannot establish current Project canon. This makes structured facts retrieval helpers rather than detached truth records.

See `guides/06_ASSISTANT/unified_retrieval_planner.md`.
