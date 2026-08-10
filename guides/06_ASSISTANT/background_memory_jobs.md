---
guide_id: assistant.background_memory_jobs
title: Background Memory Jobs and Progress
surface: assistant
scope: built_in
applies_to:
  - assistant
  - project_brain
  - memory
  - memory_engine
  - admin
  - roleplay
tags:
  - assistant
  - memory
  - jobs
  - progress
  - project_brain
  - indexing
priority: 94
version: 1
updated: 2026-08-09
---

# Background Memory Jobs and Progress

Phase 10 makes `neo_memory_jobs` the canonical persistent job registry for long-running memory work. UI buttons and Admin panels are clients of the job service; they do not own the worker lifetime or the job state.

## Canonical lifecycle

Long-running jobs use:

```text
queued -> running -> completed
                  -> failed
                  -> cancelled
```

A job stores its type, title, payload, Scope/surface/Delivery Project identity, attempt/retry lineage, timestamps, result/error state, and structured progress.

Progress may include:

- current phase;
- current/total item counts;
- percentage;
- elapsed execution time derived from persisted timestamps;
- human-readable message;
- warnings/errors;
- whether cancellation/retry is currently available.

## What uses background jobs

Phase 10 supports the following canonical job types:

- `project_brain_rebuild`;
- `memory_consolidation`;
- `embedding_reindex`;
- `memory_writeback` for deliberate/bulk runs;
- `roleplay_memory_vectors` through the historical Admin compatibility route.

Small/instant operations should remain synchronous. Normal Phase 9 per-turn durable-candidate evaluation is intentionally not forced into a background job.

## Project Brain progress

Rebuild Project Brain is backgrounded by default. Its job can report phases such as:

```text
scan_metadata
ingest_snapshots
ingest_metadata
ingest_scope_knowledge
ingest_captures
extract_documents
consolidate
validate
report
completed
```

The Assistant Project Brain card can reattach to the active job after rerender/navigation and display phase, percent, item counts, message, Cancel, and Retry.

## Persistence and restart truthfulness

`neo_memory_jobs` rows persist in SQLite, so navigating to another tab or rerendering the Assistant does not lose the job record/progress.

The actual worker threads are still process-local. If the Neo server process stops while a job is running, Phase 10 does **not** claim that work continued in the background. On the next process startup, a stale `running` row is marked `failed/interrupted` and becomes retryable from its stored payload.

True cross-process worker resumption is not implemented in Phase 10.

## Cancellation

Cancellation is cooperative. A queued job can cancel immediately. A running job stops at the next safe checkpoint exposed by the handler.

Some monolithic third-party/model operations cannot be interrupted safely mid-call; the cancellation request remains recorded and is honored at the next checkpoint. Neo must not report a job as cancelled before the worker reaches a safe cancellation boundary.

## Retry and deduplication

Failed/cancelled jobs can be retried from their stored payload. A retry creates a new job row with `retry_of_job_id` and an incremented attempt number so history is preserved.

Active jobs may use a `dedupe_key`. Creating another queued/running job with the same job type + dedupe key reuses the existing active job rather than double-executing the same work.

## Admin ownership

Admin -> **Memory Engine** owns infrastructure/job observability. It can show the unified job registry and progress for indexing/consolidation/rebuild work.

Admin -> **Memory** still owns memory-content governance and durable-memory review decisions.

The historical Admin Roleplay `index_jobs.json` queue is now a compatibility projection over `neo_memory_jobs`; it is no longer an independent execution authority.

## APIs

Canonical Phase 10 job APIs include:

- `GET /api/memory/jobs`;
- `GET /api/memory/jobs/{job_id}`;
- `POST /api/memory/jobs/create`;
- `POST /api/memory/jobs/{job_id}/cancel`;
- `POST /api/memory/jobs/{job_id}/retry`.

Historical Admin indexing routes remain available and bridge to the same authority.

## Phase boundary

Phase 10 does not redesign the full Assistant Memory/Scope UI, does not consolidate Operator execution, and does not introduce a durable external worker daemon. Those belong to later phases.
