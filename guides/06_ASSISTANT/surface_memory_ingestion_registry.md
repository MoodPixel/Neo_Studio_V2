---
guide_id: assistant.surface_memory_ingestion_registry.phase8
surface: assistant
title: Unified Surface Memory Ingestion Registry
summary: Explains how successful Neo surface operations become searchable Unified Memory through the Phase 8 registry without turning failed jobs or casual chatter into durable history.
tags: [assistant, memory, surface, ingestion, image, video, voice, prompt_captioning, roleplay, board]
applies_to: [assistant, image, video, voice, prompt_captioning, roleplay, board]
---

# Unified Surface Memory Ingestion Registry

Phase 8 adds one memory-ingestion capability registry for Neo surfaces. The registry does not replace Unified Memory: it standardizes how a surface is allowed to write successful/useful runtime history into it.

## Core rule

A normal successful surface operation may emit a bounded canonical event containing its `surface_id`, `scope_id`, optional real `project_id`, event type/status, source/result identifier, useful prompt/settings/result metadata, and provenance. The registry writes deterministic event/object/fact/fragment rows into Unified Memory. Embedding/reindex work can be queued through the Phase 10 unified Memory Job Service.

Failed, cancelled, queued/running, or unregistered events are not automatically promoted into searchable history. Normal Assistant chatter is also excluded; Assistant live writes require an explicit substantial task/memory event.

Phase 9 now owns **durable promotion** after this history write: successful surface evidence may create an observed durable candidate, but only sufficiently reinforced low-risk patterns auto-apply. Preferences, project decisions, contradictions, cross-project claims, and canon-sensitive changes remain review-gated.

## Registered adapters

- **Image** — live completed generation history + compatibility batch replay.
- **Video** — live completed result history + V22/batch replay compatibility.
- **Voice** — live preview/render/dialogue replay events + Phase 8 batch import from the V15 replay-memory index.
- **Prompt + Captioning** — live successful prompt/caption output history + compatibility metadata/history replay.
- **Roleplay** — sandbox-owned replay adapter only. Generic live registry writes are blocked so Roleplay canon/universes cannot bypass their authoritative DB/sandbox rules.
- **Assistant** — explicit substantial task/memory events only; ordinary chat is not auto-promoted.
- **Board** — live registry contract for pinned/sent workflow assets; no historical batch importer yet.
- **Music/future surfaces** — registry extension contract is available; unsupported/unregistered surfaces fail closed.

## Project Brain relationship

Project Brain Phase 7 is a deliberate/manual project ingestion producer. Phase 8 surface ingestion is automatic runtime history. Both write to the same Unified Memory authority, but neither replaces the other.

## Diagnostics

`GET /api/memory/surface-ingestion/registry` reports registered adapters, batch/live support, producer policy, unsupported manifest surfaces, and the latest in-process ingestion result per surface. Memory Engine status embeds the same registry contract.

The older `POST /api/memory/surface-ingestion/run` endpoint remains available. Its Phase M3 scanners are now compatibility replay implementations dispatched through the Phase 8 registry rather than a hard-coded list of surface ownership.

## Safety

- Surface memory writes must never fail a successful creative task.
- Secrets/binary payloads must not be copied into searchable memory payloads.
- Automatic surface ingestion records task/history evidence. Phase 9 may derive a separate durable candidate from successful evidence, but preferences/canon/high-impact changes remain review-gated.
- Roleplay sandbox ownership remains stronger than the generic surface registry.
