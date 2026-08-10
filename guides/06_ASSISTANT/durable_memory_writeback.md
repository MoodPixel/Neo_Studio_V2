---
guide_id: assistant.durable_memory_writeback.phase9
surface: assistant
title: Durable Memory Writeback
summary: Explains how Neo promotes selected repeated/confirmed history into durable memory while review-gating preferences, project decisions, contradictions, cross-project claims, and canon-sensitive changes.
tags: [assistant, memory, durable_memory, writeback, preferences, projects, review, m11]
applies_to: [assistant, image, video, voice, prompt_captioning, roleplay, board, admin]
priority: 98
version: 1
updated: 2026-08-09
---

# Durable Memory Writeback

Phase 9 evolves the historical M11 writeback engine into Neo's durable-memory promotion layer. Searchable task history and durable memory are deliberately different.

## Three memory levels

1. **Searchable history** — successful task/output evidence from the Phase 8 Surface Ingestion Registry or deliberate Project Brain material from Phase 7.
2. **Observed durable candidate** — something that may deserve long-term promotion, but does not yet have enough support or approval.
3. **Applied durable memory** — a reviewed or sufficiently reinforced low-risk memory written as an active Unified Memory fragment/fact.

A successful task does not automatically become a durable preference.

## Candidate policy

Neo may auto-promote only bounded low-risk patterns after repeated independent support, such as a successful stable model/settings combination or a repeated workflow pattern. Volatile fields such as seed/output path/timestamps do not define the durable setting signature.

The following require review before durable application:

- user preference changes or direct remember-this directives;
- confirmed Delivery Project decisions;
- cross-project claims;
- contradictions/replacements of an existing durable memory;
- Roleplay canon, relationship, character-secret/knowledge, or player-character-sensitive changes;
- other high-impact project facts.

Ordinary chatter and incidental task text should produce no durable candidate.

## Support and provenance

Each candidate stores a durable key, semantic/content hash, provenance/evidence, support count, support threshold, confidence, importance, risk class, Scope/Surface/Project identity, and review reason. Replaying identical evidence is idempotent and does not artificially increase support.

For the default successful-settings policy, a first distinct success is observed. A second independent success with the same stable configuration may reach the support threshold and auto-apply.

## Contradiction and supersession

A conflicting candidate never silently replaces an active durable memory. The old memory remains active while the replacement is pending review. When the replacement is approved/applied, the older writeback, fragment, fact, and source event are marked superseded and the old fragment is removed from active FTS retrieval.

This keeps one active durable truth for the same durable key instead of allowing conflicting preferences to compete in retrieval.

## Assistant relationship

After a successful guarded Assistant response, Neo classifies the turn for durable-memory candidates. This classification is separate from Phase 8 task-history ingestion. Normal requests such as stories, recipes, rewrites, or casual chat do not become durable memory unless the turn contains a meaningful durable signal.

The Control Center no longer plans a generic `assistant_interaction_candidate` for every turn. Durable classification happens after the final successful response so the candidate can use the actual completed task context.

## Surface relationship

A successful registered surface event first becomes searchable history through Phase 8. The same successful event may then produce a durable candidate through Phase 9. Durable writeback is best-effort and must never fail the underlying Image/Video/Voice/Prompt/Board task.

## Admin review

Admin → Memory owns durable-memory governance. Its Policies area exposes writeback status and the pending review queue. Approve/apply and reject actions use the existing `/api/memory/writeback/review` endpoint. Memory Engine remains infrastructure-only.

Phase 10 now supplies persistent background-job progress. Normal per-turn Phase 9 candidate classification remains synchronous because it is small; deliberate/bulk `memory_writeback` work can be queued through the unified Memory Job Service.

## Python 3.10 startup compatibility hotfix — 2026-08-09

Neo Studio V2 supports Python 3.10+. Durable writeback semantic hashing must therefore avoid Python 3.12+ f-string grammar features. In particular, do not place a backslash-containing regular-expression literal such as `r"\s+"` directly inside an f-string expression. Normalize the content first, then interpolate the normalized value. This is a parser compatibility rule only; it does not change durable-memory hashing semantics.
