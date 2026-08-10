---
guide_id: assistant.scope_priority_retrieval
title: Scope Priority, Not Scope Prison
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
  - memory
  - retrieval
  - scope
  - cross-surface
  - project
  - roleplay
priority: 100
version: 1
updated: 2026-08-09
---

# Scope Priority, Not Scope Prison

Phase 6 changes how the Assistant Retrieval Gateway interprets an active Scope.

A Scope is a **priority signal and sandbox boundary**, not an instruction to become blind to relevant Neo memory elsewhere.

## General Scope

General starts with General durable memory, then expands only when the current question provides a reason.

Examples:

- “What seed did I use for that Qwen image?” → search General + Image memory.
- “What did Caption Studio save for Quiet Connection?” → search General + Prompt/Captioning memory.
- “What do we remember about Heart & Soul 2 Ball?” → search General + the explicitly matched project memory.
- “What model did I use last time?” → bounded recall discovery across Image, Prompt/Captioning, Video, and Voice because the request is clearly asking for remembered prior work but does not name the originating surface.

General does **not** search every memory namespace on every message.

## Surface Scopes

A surface Scope keeps its own memory first, then may use General durable memory and another clearly relevant surface.

For example, Image Workspace can use:

1. active Image memory;
2. linked Delivery Project memory, when present;
3. General durable preferences;
4. another surface only when the query points there.

The active Scope receives a ranking preference, but a highly relevant expanded result may still outrank weak local context.

## Delivery Projects

A linked Delivery Project is first-priority context. Neo also keeps a compatibility target for the current `project:<id>` Unified Memory ingestion shape until project memory is migrated later.

From General, a project is expanded only when the query strongly matches a registered project name or ID. Unrelated projects are not searched by default.

## Roleplay hard boundary

Roleplay remains the exception to broad cross-surface recall.

General never performs open-ended retrieval across all Roleplay universes. A query must match a concrete registered universe/world/scene/sandbox before detailed Roleplay memory is included.

Example:

- “What do we remember from roleplay?” → no detailed Roleplay memory expansion.
- “In Universe Alpha, what does canon say about Ren?” → retrieve only the matched Universe Alpha sandbox.

This prevents Universe A / Universe B contamination.

## M12 safety proof

Every approved Phase 6 expansion is recorded in the Retrieval Gateway result:

- retrieval target;
- reason;
- target surface/project/scope;
- priority;
- cross-surface/project permission;
- blocked expansions.

Control Center passes those permissions into M12 validation. Later M12 trace audits reuse the persisted permission proof instead of incorrectly treating approved cross-surface context as a sandbox violation.

## Recall discovery

A generic recall request such as “What model did I use last time?” may not identify a surface. General can therefore perform a bounded discovery across the main non-Roleplay creative memory surfaces.

This is not enabled for ordinary tasks such as “Help me plan tomorrow.” It requires explicit recall language.

## Inspector

The Retrieval Gateway exposes `scope_policy` and `retrieval_targets` for Inspector/Admin diagnostics. Normal chat should use the retrieved knowledge naturally and should not dump routing diagnostics unless the user asks for technical proof.
