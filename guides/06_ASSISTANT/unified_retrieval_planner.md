---
guide_id: assistant.unified_retrieval_planner.nkb8
surface: assistant
scope: built_in
title: Unified Retrieval Planner
summary: Explains how NKB-8 analyzes Assistant questions, chooses evidence lanes, fuses ranked candidates, applies task-sensitive authority, and verifies strong source-backed evidence before packet assembly.
tags: [assistant, retrieval, planner, memory, project, native_knowledge, authority, reranker, rrf, grounding]
applies_to: [assistant, general]
priority: 100
version: 3
updated: 2026-09-27
status: current
---

# Unified Retrieval Planner

NKB-8 places a query planner inside the Assistant's existing single Retrieval Gateway.

The planner decides **where Neo should look and which evidence should survive**. NKB-9 consumes that verified shortlist into one bounded Context Packet; NKB-10 now consumes the planner/packet state to enforce task-sensitive grounding and strict fail-closed behavior where required.

```text
Assistant question
    ↓
query + claim analysis
    ↓
evidence-lane plan
    ↓
structured / lexical / dense / native candidates
    ↓
weighted reciprocal-rank fusion
    ↓
reranker
    ↓
task-sensitive authority adjudication
    ↓
source hydration for strong claims
    ↓
verified evidence shortlist
    ↓
NKB-9 Context Packet
```

## Query analysis

The planner classifies the turn using deterministic intent/claim signals. Current claim families include:

- project fact/canon recall;
- current Neo runtime implementation;
- Neo usage guidance;
- validation/test evidence;
- historical design questions;
- native generation/history recall;
- creative extension;
- general recall.

Generic words such as `route` or `file` are **not enough by themselves** to activate Neo code retrieval. Development intent requires stronger signals such as a repository path, source extension, API path, function/class/method wording, or implementation/codebase language.

This prevents story questions such as `Which route did Elias take?` from opening the Neo code lane.

## Evidence lanes

The planner can select from:

- `project_structured` — NKB-7 explicit Project entities/aliases/facts linked back to source fragments;
- `unified_memory` — experiential/project/surface memory already represented in M9;
- `native_authority` — NKB-6 Roleplay/Image/Video/Voice/Prompt-Captioning/Code/Docs adapters;
- `knowledge_index` — compatibility static knowledge index;
- `guide_index` — compatibility built-in Guide lane.

The planner chooses lanes by intent rather than sending every source into every turn.

## Project canon retrieval

For Project fact questions, explicit entities/aliases/facts can provide a precise structured match. A structured fact is not treated as standalone truth: it carries the supporting NKB-7 source reference.

For a strong Project claim Neo should resolve that reference back to the original uploaded file and verify the indexed revision before relying on the evidence.

If the source revision is stale or unavailable, the planner can reject that evidence and report `not_established` to later grounding layers.

## Native authority retrieval

When the query asks about a Neo-native surface, the planner can use the corresponding NKB-6 adapter instead of hoping a generic vector index contains the right record.

Examples include:

- Image generation history and metadata;
- Video result/replay lineage;
- Voice jobs and scripts;
- saved prompts/captions;
- Roleplay canon/state;
- Neo code symbols;
- documentation sections.

Native adapters return search projections with references; the owning native store remains authoritative.

## Weighted rank fusion

Scores from BM25/FTS, dense retrieval, native structured lookup, and Guide search are not directly comparable numbers.

NKB-8 therefore combines lane **ranks** with weighted Reciprocal Rank Fusion (RRF) instead of adding raw scores across retrieval systems.

The reranker is applied only after fusion to the bounded shortlist.

Exact duplicate visible evidence is merged while retaining contributing `provenance_lanes`. Reaching the same M9 fragment through multiple allowed scope targets does not inflate the compatibility duplicate counter.

## Relevance is not authority

After reranking, the planner separately evaluates evidence role and lifecycle.

Examples:

- current runtime questions prefer runtime implementation over tests/history;
- usage questions prefer current Guides plus supporting implementation;
- validation questions prefer validation evidence;
- Project fact questions prefer primary Project source evidence;
- native generation questions prefer native execution records.

Superseded/deprecated evidence cannot establish current claims. Open conflicts lower authority. Strong claims can require current source verification.

## Source hydration

For strong Project/native claims, selected projections can be hydrated from the owning source.

For NKB-7 Project documents this means:

```text
indexed projection
→ project-document native_ref
→ original uploaded file
→ revision-hash verification
→ reconstructed source fragment
```

A changed source returns `stale`; stale evidence cannot establish a strong current Project fact.

## Planner result state

The Retrieval Gateway now exposes:

- planner intent and claim type;
- selected lanes and lane weights;
- query variants/entities;
- RRF diagnostics;
- reranker status;
- authority rejections;
- source hydration status;
- `known_state`;
- `fail_closed_recommended`.

`known_state` can currently be `established`, `not_established`, or `unresolved_conflict`.

`fail_closed_recommended` is a planner signal; NKB-10 now converts it into task-sensitive model-facing grounding behavior and deterministic fail-closed replacement for strict missing-evidence claims.

## Inspector endpoints

Admin/diagnostic callers can inspect the planner without running a full Assistant turn:

```text
GET  /api/memory/retrieval-planner/status
POST /api/memory/retrieval-planner/plan
```

The plan endpoint analyzes the query and reports intended lanes/authority policy. It does not itself build the final prompt packet.

## Phase boundary

NKB-8 deliberately does **not** remove all compatibility Context Pack projections. The current Context Pack can still expose Project Brain/Scope/Guide compatibility sections around the canonical Gateway result.

NKB-9 owns consolidation into a bounded evidence packet so the model receives the verified shortlist once rather than multiple compatibility projections.

## Planner query versus retrieval query

Assistant retrieval may enrich a search query with active workspace/project descriptors to improve recall. NKB-8 keeps that search text separate from the planner's intent input. `planner_query` should be the actual user utterance; workspace metadata must not accidentally turn a usage question into history/code/canon intent.

## Broad Code/Docs discovery

Broad natural-language Code/Docs questions use indexed retrieval for candidate discovery, then selected source paths can hydrate through `neo.code` / `neo.docs`. Direct native authority scanning is reserved for higher-information identifiers such as a repository path, API route, symbol, or explicit document path. This avoids repeatedly scanning thousands of repository/doc files on normal Assistant turns while retaining source authority verification.


## NKB-9 integration

NKB-9 is implemented. The planner does not render several source lanes independently for the model. Its adjudicated `items`, citations, `known_state`, and `fail_closed_recommended` state flow into `neo.assistant.context_packet.v1`.

Packet assembly performs only deterministic clipping/deduplication and current-turn input budgeting; it does not search or rerank again. Compatibility source projections remain Inspector-only when the packet is present.

## Project phrase + typo recovery — 2026-09-27

For active Project Scopes, NKB-8 now preserves likely multi-word recall subjects and can recover conservative spelling variants from a vocabulary built only from that Project's source-backed/indexed terms.

The original user query remains unchanged. The planner exposes `query_normalization` plus a separate `retrieval_query`; high/medium-confidence corrections are added as retrieval variants and feed Unified Memory search/reranking.

Generic autocorrect is intentionally not used because fictional/project terms may be valid words unknown to a general dictionary. Ambiguity margins and phrase/token compatibility guard against merging distinct canon terms.

## Project evidence semantics + answerability — 2026-09-27

Project relevance is now separated from answerability. A verified passage may be highly relevant while still being unable to establish an affirmative answer (for example, an `OPEN QUESTIONS` section).

NKB-8 classifies Project passages conservatively and applies an answerability factor after reranking and authority checks. Direct definitions and entity/profile headings are preferred for definition/identity questions; unresolved-question and speculative passages remain available for uncertainty reporting but cannot establish positive claims.

Project `known_state` can therefore resolve to `explicit_unknown` when the active source explicitly preserves the requested point as unresolved.

Typo recovery now preserves the original query for audit while replacing the matched typo phrase in the effective retrieval query and adding the canonical Project term as a quoted lexical anchor.
