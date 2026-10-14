---
guide_id: assistant_knowledge_inspector
surface: assistant
scope: built_in
applies_to: [assistant, memory, inspector]
tags: [assistant, knowledge, inspector, retrieval, grounding, context]
priority: 92
version: 2
updated: 2026-09-29
status: current
---

# Knowledge Inspector

Assistant → **Inspector** now begins with the NKB-11 Knowledge Inspector. It explains the exact evidence path used for the latest Assistant turn without running retrieval again.

The Inspector shows, in order:

1. **Query decision** — behavior mode, grounding mode, intent, claim type, target surface, known-state and fail-closed state.
2. **Retrieval lanes** — which project/native/memory/code/Guide lanes were selected, their weights, status and candidate counts.
3. **Fusion + rerank** — weighted RRF status, lane counts, duplicate removal, reranker status, detected entities and query variants.
4. **Context packet** — evidence candidates, evidence selected, current-turn direct inputs, live surface state, packet budget and dropped evidence.
5. **Evidence accepted/rejected** — source lane, evidence role, source integrity, scores, citations and authority rejection reasons.
6. **Conversation continuity** — whether same-chat retrieval ran, which older messages/checkpoints were selected, lexical/semantic lane status, and current/superseded decision state.
7. **Final grounding** — whether deterministic grounding replaced the provider answer, whether provider text was discarded, and whether the normal output-repair guard ran.
8. **Performance** — per-stage foreground timings including same-chat retrieval and provider generation.

The trace schema is:

```text
neo.assistant.knowledge_inspector.v1
```

Diagnostics endpoints:

```text
GET /api/assistant/knowledge-inspector/status
GET /api/assistant/knowledge-inspector/trace?session_id=<id>
```

## Important boundary

Knowledge Inspector is **read-only observability**. Opening or refreshing it must never perform another retrieval, rerank, source hydration, memory write, authority decision, or prompt compilation. It renders the trace stored with the exact Assistant turn that produced the answer.

Raw Assistant diagnostics remain available underneath the normalized Inspector for engineering compatibility and deeper audits.

## Query recovery and established-evidence diagnostics — 2026-09-27

Inspector now shows the effective retrieval query and Project-local typo/phrase recovery diagnostics, including the original phrase, canonical term, confidence/score and whether the variant was applied. It also shows whether the Project source catalog was suppressed from provider context and whether a bogus established-evidence meta-refusal was repaired/blocked.

## Epistemic / answerability fields — 2026-09-27

Accepted evidence rows now expose passage epistemic role/state, whether the passage supports affirmative claims, answerability score/multiplier, direct-definition/entity-heading matches and the effective canonical query target. Final grounding also reports detection of source-by-source pseudo-answer dumps.

## ACI-7.2 same-chat continuity proof — 2026-09-29

When the latest turn explicitly recalls an older topic from the current chat, Inspector renders the continuity trace stored with that exact turn. It shows same-chat isolation, selected item/message IDs, FTS candidate count, semantic used/skipped reason, decision state and continuity timing.

Inspector does not run chat retrieval again. If a turn did not need old-chat context, the continuity section should report `not_needed` rather than inventing continuity evidence.
