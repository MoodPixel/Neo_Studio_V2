---
guide_id: assistant.project_epistemic_answerability
surface: assistant
scope: built_in
title: Project Evidence Semantics and Answerability
summary: Explains how Neo distinguishes declarative Project evidence from unresolved questions/speculation and ranks passages by their ability to answer the current question.
tags: [assistant, project, canon, epistemic, answerability, retrieval, grounding, inspector]
applies_to: [assistant]
priority: 102
version: 1
updated: 2026-09-27
status: current
---

# Project Evidence Semantics and Answerability

Project retrieval separates three different questions:

1. **Is this passage relevant?**
2. **Is this source authoritative/current?**
3. **Can this passage actually establish the answer being claimed?**

A verified source passage is not automatically verified affirmative evidence.

## Passage roles

Project fragments may be classified as:

- `declarative_passage` — can support affirmative claims when relevant;
- `unresolved_question` — establishes that a question remains open, not a positive answer;
- `speculative_passage` — can support attributed possibility/interpretation, not settled canon.

For example, a source line:

```text
Who first discovered the final seal?
```

may support:

```text
The current Project leaves the discoverer's identity unresolved.
```

It may **not** support:

```text
Great Soul discovered the final seal.
```

## Answerability ranking

For `Who/What is X?` recall, Neo boosts direct definitions and entity/profile headings, then declarative passages. Mere mentions are lower-value evidence. Open questions and speculation are heavily penalized for affirmative answering while remaining available to explain uncertainty.

## Known state

Project recall can resolve to:

- `established` — answerable affirmative evidence exists;
- `explicit_unknown` — the Project explicitly preserves the requested point as unresolved;
- `not_established` — no sufficient answerable evidence exists;
- `unresolved_conflict` — current authoritative evidence materially conflicts.

Strict CANON mode fails closed for `explicit_unknown` and `not_established` instead of inventing lore.

## Canonical typo resolution

When a high-confidence Project-local correction is available, the user text is preserved while the effective retrieval query uses the canonical term. The Context Packet also carries the canonical term so the model answers with the Project spelling rather than repeating the typo.

## Inspector

Knowledge Inspector displays:

- relevance score;
- authority multiplier;
- answerability score/multiplier;
- epistemic role/state;
- whether the passage supports affirmative claims;
- direct-definition/entity-heading matches;
- canonical typo/phrase recovery;
- per-source pseudo-answer dump detection.
