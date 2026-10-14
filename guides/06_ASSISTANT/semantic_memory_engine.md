---
guide_id: assistant.semantic_memory_engine
surface: assistant
scope: built_in
title: Semantic Memory Engine Setup
tags:
  - assistant
  - memory
  - embeddings
  - reranker
  - chroma
  - retrieval
priority: 95
version: 1
updated: 2026-09-29
---

# Semantic Memory Engine Setup

Neo can use local semantic models to improve how Assistant and memory-aware workflows find relevant context. These models are **retrieval helpers**. They do not replace Neo's Assistant LLM and they do not become the source of truth for your memories or Project files.

## The four pieces are different

Think of the stack like this:

```text
Your canonical Neo knowledge
        ↓
Embedding model → semantic vectors → vector index
        ↓
Candidate matches
        ↓
Reranker
        ↓
Verified source context
        ↓
Assistant LLM
```

### Embedding runtime

The software interface that loads/runs an embedding model. Neo's primary local runtime is **Sentence Transformers**.

### Embedding model

The actual model that converts text into vectors. This is user-selectable.

Recommended reference model:

```text
Qwen/Qwen3-Embedding-0.6B
```

### Reranker runtime

The interface that scores a query together with candidate passages after the first retrieval step. Sentence Transformers `CrossEncoder` is the primary local runtime direction.

### Reranker model

The actual ranking model. This is also user-selectable.

Recommended reference model:

```text
Qwen/Qwen3-Reranker-0.6B
```

The recommended Qwen pair is not mandatory. Use a compatible lighter/heavier model when that better fits your machine or language/domain needs.

## Configure models in Admin

Open:

```text
Admin → Memory Engine
```

Set the embedding and reranker resources available in your build. A real model path/ID must be configured; seeing `Sentence Transformers` or another provider label does **not** by itself mean a real model is loaded.

After configuration, use Neo's available test/status controls to verify the engine before relying on semantic retrieval.

## Qualification and honest fallback behavior

ACI-5 separates **configured** from **qualified**. A model path, provider label or installed package is not enough to make semantic retrieval ready. After changing the embedding/reranker configuration, run **Admin → Memory Engine → Qualify semantic engine**. Neo performs real inference probes and persists the result for the exact configuration fingerprint.

The readiness states are intentionally explicit:

```text
ready                   embedding qualified; vector space compatible
degraded                usable, but an optional semantic component is degraded
qualification_required  current model/profile has not been qualified
reindex_required         stored vectors belong to another/legacy embedding space
blocked                 real qualification failed
```

SQLite FTS/keyword retrieval remains available when semantic inference is unavailable. Deterministic hash vectors are retained only as a compatibility/debug primitive; they are **non-semantic**, are never written as qualified semantic cache entries, and can never turn the readiness badge green. `lexical_overlap` is also non-semantic and is reported as degraded when deliberately selected as the reranker.

## Chroma is an index, not your memory database

Neo's canonical knowledge remains source/SQLite-owned. Chroma is used as a semantic index/projection when enabled by the runtime.

That distinction matters because an index can be rebuilt; your canonical facts, source revisions, provenance and Project records should not depend on Chroma being the only copy.

In the current repository baseline, a Chroma store may be selected while write mode is still disabled. Do not assume a selected backend name means vectors are actively being written. Check the Memory Engine status for the actual read/write state available in your build.

## Changing embedding models requires reindexing

Vectors made by different embedding models are not interchangeable.

Example:

```text
old index  → Qwen embedding space
new query  → different model's embedding space
```

Even when two models output vectors with the same number of dimensions, their semantic spaces can differ. After changing the embedding model or important embedding settings, rebuild/reindex the affected semantic index before trusting results.

Changing only the reranker normally does not require regenerating all stored embeddings.


## Embedding profile fingerprints and reindexing

A qualified embedding space is identified by a fingerprint derived from the runtime/provider/model/revision, detected dimension, pooling/normalization behavior, and query/document prompt or instruction settings. This is stronger than comparing only the model name or vector dimension.

Neo binds SQLite semantic caches and non-empty Chroma collections to that fingerprint. If the current fingerprint differs, Neo refuses to mix/query those vectors and reports `reindex_required`. For Neo-owned Chroma mirrors, health also compares canonical SQLite chunk IDs/content hashes and reports bounded missing, stale and orphan-vector drift. Chroma remains disposable; canonical source/SQLite knowledge is not deleted by a semantic reindex.

Changing only a reranker normally leaves the embedding fingerprint unchanged. Changing the embedding model, revision, dimension, normalization, pooling, query/document prompt or instruction can create a different vector space and should be treated as a reindex event.

## Hardware guidance

Embedding/reranker quality can be traded against RAM/VRAM and latency. A smaller model may be better for a machine that also runs Image/Video generation. Neo should not reserve GPU memory for semantic retrieval at the expense of the creative task you are actively running.

Use CPU/offload or smaller models when needed; use larger models only when their measured retrieval improvement is worth the cost.

## Project and chat attachment knowledge

Persistent Project files and ordinary Assistant document/media attachments now share the same `NeoSourceDocument` preparation contract. Their **lifecycle authority** is different:

```text
Project file       durable Project Brain source
Assistant upload   chat-scoped ephemeral source; never promoted automatically
```

ACI-3 provides ephemeral document retrieval and same-chat follow-ups. ACI-4 adds timestamped audio/video-audio transcript evidence. Put long-lived knowledge in a Project when you want durable Project authority; use ordinary attachments when the evidence should remain scoped to the current chat.

## Unified source intelligence

Source preparation is now an official Neo runtime capability rather than an isolated benchmark environment. `requirements-memory.txt` owns the semantic retrieval stack plus Docling/RapidOCR and the lightweight Office fallbacks; `setup_neo_studio_venv.bat` installs that file into Neo's normal root `.venv`. There is no public ACI-specific requirements file or benchmark virtual environment.

The production parser router is capability-based:

```text
PDF / DOCX / PPTX / XLSX
  -> Docling + RapidOCR first
  -> Neo lightweight parser fallback

TXT / Markdown / JSON / CSV / HTML / code-like text
  -> Neo lightweight parser
```

The normalized `NeoSourceDocument` contract records parser identity/version, parser attempts/fallback use, content SHA-256 and revision ID, structured blocks, hierarchy, tables/pictures, capabilities, and source locators. Existing NKB-7 Project manifests and Unified Memory remain downstream authorities; the contract does not replace them.

Sentence Transformers 5.4+ is now the production floor in `requirements-memory.txt` so compatible user-selected Qwen3 rerankers can load through `CrossEncoder`. Neo still does not hard-code a single embedding or reranker model: Admin-selected compatible models remain authoritative.

The default source-router mode is `auto`. Developers can set `NEO_SOURCE_PREPARATION_MODE=lightweight` for diagnosis or environments where advanced parsing is deliberately disabled. Advanced-parser failure falls back explicitly and records the failed attempt; it must not pretend Docling succeeded.

See `guides/06_ASSISTANT/unified_source_preparation.md` for the source contract and routing details.
