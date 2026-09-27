---
guide_id: assistant.scope_priority_retrieval
title: Scope Classes and Project Sandboxes
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
  - project_sandbox
tags:
  - assistant
  - memory
  - retrieval
  - scope
  - sandbox
  - project
  - cross-surface
priority: 100
version: 3
updated: 2026-09-27
---

# Scope Classes and Project Sandboxes

Neo Assistant now distinguishes three retrieval classes. This supersedes the older blanket **“Scope Priority, Not Scope Prison”** behavior for user-created project scopes.

## General Assistant — federated

General is the place for cross-project and cross-surface recall. It begins with General context and may expand only when the current question gives a reason.

Examples:

- “What seed did I use for that Qwen image?” → General + relevant Image history.
- “What do we remember about MPU?” → General may explicitly target the MPU project scope.
- “What model did I use last time?” → bounded recall discovery across relevant native surfaces.

General does not search every namespace on every message.

## Neo built-in surface scopes — bounded federation

Image, Video, Voice, Prompt + Captioning, Roleplay, Client Work, and Neo Development are Neo-owned workspaces. They remain surface-first and may use bounded cross-surface/context expansion when the query warrants it.

Roleplay keeps its existing explicit universe/scene sandbox rules.

## User-created project scopes — hard sandbox

A user-created Assistant Scope such as `MPU` is a **hard project sandbox by default**.

While that scope is active, normal Assistant retrieval is restricted to:

- the active project scope's Project Brain documents;
- structured objects, aliases, facts, and source-direct fragments for that scope;
- Unified Memory rows stored for that same scope;
- chats/memories explicitly associated with that scope.

The planner must not automatically fall back to:

- General durable memory;
- another user-created project;
- Neo Guides or System Records;
- Neo code/search indexes;
- unrelated Image/Video/Voice/Prompt history.

Cross-project work belongs in **General Assistant** unless a future explicit escape action is designed. The active project scope itself is not an implicit permission to federate.


## Project scope is the implicit domain

When a user-created Project Scope is active, ordinary unqualified questions are interpreted inside that Project. The user does not need to repeat the Project name on every turn.

For example, inside `MPU`:

```text
who is great soul?
```

is treated as a Project/canon recall question, not a general-world-knowledge question. If the Project sources do not establish the answer, CANON grounding fails closed instead of allowing the model to invent outside lore.

Assistant Brain resolves stored custom scopes before General/query heuristics. Inspector should therefore show the custom `scope_id`, `scope_class=project_sandbox`, and `hard_sandbox=true` for the exact turn.

## Chats are Scope-bound

Each Assistant chat belongs to one Scope. Switching the Scope filter must not carry an active chat transcript from General or another Project into the selected Project. Neo switches to an existing chat for that Scope or starts with no active chat; the server also refuses to silently rebind a mismatched session and creates a fresh Scope-bound chat instead.

Prior Assistant replies are conversation history, not canon evidence. CANON answers must still be supported by the current Context Packet/current-turn source.

## Continue response

`Continue response` is a generation instruction, not a new retrieval topic. Neo reuses the previous real user query for retrieval/planning while preserving `CONTINUE` behavior and the active scope/grounding mode.

Example:

```text
User: who is great soul?
Scope: MPU

Continue response
→ planner query remains: who is great soul?
→ scope remains: MPU
→ no Assistant-guide lookup caused by the word "Assistant" in the continuation command
```

## Inspector proof

Knowledge Inspector records:

- `scope_class`;
- `hard_sandbox`;
- selected retrieval lanes;
- blocked cross-scope expansion;
- accepted/rejected evidence.

For a user-created project scope, Inspector should show `project_sandbox` and `hard_sandbox = true`.
