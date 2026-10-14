---
guide_id: assistant.conversation_continuity_index
title: Conversation Continuity Index
surface: assistant
scope: built_in
applies_to:
  - assistant
  - general
  - project_sandbox
  - built_in_scope
tags:
  - assistant
  - conversation
  - continuity
  - checkpoints
  - retrieval
  - memory
priority: 94
version: 1
updated: 2026-09-29
status: current
---

# Conversation Continuity Index

ACI-7.2 gives long Assistant chats a **same-chat continuity index**. The goal is to remember an older topic or decision without sending the entire raw conversation to the text model on every turn.

## Canonical vs derived data

Raw Assistant session messages remain the conversation source of truth.

Neo derives a disposable continuity projection:

```text
canonical chat messages
        ↓
per-chat SQLite FTS5
        ├─ message rows
        ├─ checkpoint rows
        ├─ topic/entity anchors
        └─ decision state
        ↓
optional private Chroma projection
```

The continuity index is **not durable user memory**. A normal chat message does not become a Core User Profile fact or `neo_memory_facts` record merely because it is indexed for same-chat recall.

## How routing works

Neo separates the current request from conversation-history dependency.

```text
current_only      standalone or self-contained current request
recent            immediate pronoun/deictic follow-up
chat retrieval    specific older topic/decision in this same chat
checkpointed      broad continuation or “where did we leave off?” recall
```

Examples:

- `Who is Kest?` can search Project/Scope knowledge but does not automatically pull old chat history.
- `What did we decide about Kest earlier?` searches the current chat for the relevant old decision.
- `What were we discussing before?` can use hierarchical checkpoints because the request is broad rather than topic-specific.
- `What about his brother?` can use a short recent raw-message window.

## Incremental indexing

Neo does not rebuild the whole chat after every message.

For normal append-only chat activity:

1. the new canonical message is saved;
2. only newly appended message rows are added to the local FTS index;
3. the small derived checkpoint set is reconciled;
4. if semantic indexing is available, a deduplicated background job is scheduled;
5. the next user question searches the prepared index rather than reprocessing the conversation.

A full continuity-projection rebuild is reserved for source-history edits/deletions, checkpoint-prefix changes, embedding-profile changes, or other integrity breaks.

## Lexical first, semantic only when needed

Same-chat retrieval starts with SQLite FTS5 and deterministic topic/entity anchors. Recall-control filler such as `what did we decide earlier` is filtered so the actual subject terms carry more weight.

When lexical/anchor recall is already strong, Neo skips the foreground embedding query. If lexical recall is thin and the ACI-5 semantic engine is qualified, Neo may embed the current query once and search only that chat's private Chroma collection.

There is no hash-vector or fake semantic fallback.

## Checkpoints

ACI-7 hierarchical checkpoints remain source-linked summaries over older canonical messages. ACI-7.2 makes them searchable as derived continuity records, but targeted older-topic recall does not automatically inject every broad checkpoint into the provider prompt.

This keeps a specific recall focused while still allowing broad continuation requests to reconstruct the long-running thread.

## Same-chat decision state

Explicit decision-style messages can carry derived `current` or `historical/superseded` state inside the chat continuity projection. Newer decisions are preferred for ordinary current-state recall. A query that explicitly asks for the original/previous decision can recover the superseded version.

This is chat-local temporal context only. ACI-6 remains the authority for reviewed durable user preferences and temporal profile facts.

## Semantic background job

When semantic writes are enabled, changed chat continuity rows are projected in the background through:

```text
assistant_chat_semantic_index
```

The job uses the currently qualified embedding profile fingerprint and the normal Neo Chroma root. It is delayed/deduplicated so several nearby messages can coalesce and the embedding model does not compete with the foreground Assistant generation path.

If checkpoint identities are removed/rebuilt, Neo resets the disposable chat collection before rebuilding it so removed checkpoint vectors cannot remain as orphan semantic hits.

## Privacy and lifecycle

Each Assistant session owns its own SQLite continuity index and its own private Chroma source identity.

Cross-chat retrieval is not permitted by this path.

Clearing or deleting a chat removes:

- the local SQLite continuity index;
- WAL/SHM files if present;
- the private Chroma collection.

Deleting the continuity projection never deletes canonical durable memory or Project Brain data.

## Inspector proof

Assistant → **Inspector** shows the same-chat continuity decision for the exact latest turn, including:

- whether chat retrieval was required;
- selected continuity message/checkpoint count;
- FTS candidate count;
- whether semantic search was skipped or used;
- source message IDs/indexes;
- decision current/superseded state;
- retrieval lanes;
- conversation-retrieval timing.

Inspector remains read-only and does not run another retrieval.

## Performance rule

Conversation continuity follows the same prepare-once/retrieve-later rule as Neo's prepared document sources:

> Store/index a canonical message once, then search the prepared projection later. Do not repeatedly summarize/embed the entire chat in the foreground.

