---
guide_id: admin.ownership_memory
surface: admin
title: Admin Ownership, Memory, and Delivery Projects
scope: built_in
applies_to:
  - admin
  - memory
  - memory_engine
  - projects
  - assistant_operator
tags:
  - admin
  - memory
  - memory_engine
  - projects
  - ownership
priority: 96
version: 6
updated: 2026-09-29
---

# Admin Ownership, Memory, and Delivery Projects

Neo Admin is a control plane, not a second copy of each creative surface. Phase 2 locks which Admin area owns each kind of setting so Memory, Memory Engine, Backends, Delivery Projects, and Assistant/Operator do not compete for the same responsibility.

## Ownership map

| Admin area | Owns | Does not own |
|---|---|---|
| **Memory** | memory records, review, policies, conflicts/canon, consolidation, retention, citations, lifecycle | embedding/reranker/vector infrastructure, generation sampling |
| **Memory Engine** | sources, text bridge, embeddings, reranker, vector store, retrieval profiles, indexing, engine diagnostics | memory-record governance, generation sampling, delivery-project workflow |
| **Backends** | providers, backend profiles, task generation sampling/defaults | memory governance/indexing, model catalog/download management |
| **Models** | model catalog, installed inventory, source discovery, downloads, packs, workspace requirements | backend profile editing/sampling, Memory Engine retrieval assignments |
| **Delivery Projects** | client/work projects, briefs, linked assets, timelines, milestones, deliverables, reviews/approvals, packages, linked-memory readout | Assistant Scope editing, independent memory indexing |
| **Assistant / Operator** | orchestration diagnostics, read-only Scope visibility, Control Center traces, tool permissions, execution ledger, operator/runtime diagnostics, Voice Input ASR configuration | normal Scope editing, Memory Engine configuration, generation sampling |

## Memory vs Memory Engine

Use **Admin → Memory** when you want to inspect or govern what Neo remembers.

Use **Admin → Memory Engine** when you want to configure or diagnose how memory is indexed/retrieved.

Search + Citations belongs to Memory. Memory Engine diagnostics may show retrieval/index health but should not duplicate the user-facing memory-search experience.


## Assistant Retrieval Gateway after Phase 5

Normal Assistant retrieval now enters through `neo.memory.retrieval_gateway.v1`. The gateway is orchestration over existing authorities: Unified M9 supplies experiential/surface memory, while the Knowledge Index and built-in Guides supply source-backed reference context.

This does not change Admin ownership: **Memory** still governs remembered content and citations, and **Memory Engine** still owns retrieval/index infrastructure. The gateway status is exposed through Memory Engine service diagnostics, while user-facing retrieved-memory review remains under Memory/Assistant Inspector surfaces.

The gateway is not a new memory database and does not move generation sampling into Memory Engine.

## Surface ingestion registry diagnostics after Phase 8

Automatic surface-memory capability is registered through `neo.memory.surface_ingestion_registry.phase8.v1`. Admin → Memory Engine owns the infrastructure/health view for this registry; it does not become another memory-governance editor.

Use `GET /api/memory/surface-ingestion/registry` (also projected into Memory Engine status) to inspect registered surfaces, live/batch capabilities, unsupported manifest surfaces, and the latest in-process ingestion result. Persistent background-job history/progress is now provided by Phase 10 through the unified `neo_memory_jobs` registry.

Project Brain remains the deliberate/manual project-ingestion producer, while the surface registry owns automatic successful-task history. Both write into Unified Memory.

## Generation settings

Temperature, top-p, top-k, token limits, and other generation sampling controls belong to **Admin → Backends** or task/backend profiles. **Admin → Models** remains the catalog/inventory/download guide; it is not the generation-profile editor and it is not the Memory Engine embedding/reranker assignment surface.

The historical `/api/admin/engine/runtime-defaults` route remains readable as a compatibility bridge because Roleplay/older callers may still depend on it. Phase 2 no longer allows that Memory Engine route/UI to become a second authority for generation sampling.

## Delivery Projects vs Assistant Scopes

A Delivery Project is real client/work organization. An Assistant Scope is an AI context priority/sandbox.

Example:

```text
surface_id = image
scope_id   = image_workspace
project_id = heart_soul_2_ball
```

Admin → Delivery Projects manages the `heart_soul_2_ball` work/project lifecycle. The Assistant surface manages `image_workspace` Scope selection/editing. Admin → Assistant / Operator may inspect Scope state but should not activate/edit the user's normal Scope from a second control surface.

## Compatibility policy

Phase 2 is consolidation, not deletion.

- `global.memory_engine_resources` is canonical.
- Historical `global.engine_resources` is expanded at runtime as a deprecated compatibility alias.
- `memory_controls` is a derived compatibility descriptor sourced from `global.memory_resources`.
- Existing Memory Engine runtime-default files may still contain historical generation keys. They remain readable but are not canonical and new writes to those generation keys are ignored.
- The historical Admin index-job JSON/log surfaces remain compatibility projections, but `neo_memory_jobs` is now the Phase 10 execution authority.
- Existing Project Workspace routes/data remain until Delivery Project migration is explicitly completed.


## Voice Input ASR ownership after ACI-4

**Admin → Assistant / Operator → Voice Input** owns speech-input transcription configuration used by both the operator bridge and ACI-4 Assistant/Project media preparation. It stores the selected provider, local/cached model reference, optional language override, device, compute type, and word-timestamp preference.

This is distinct from **Admin → Backends**, which owns generation/chat provider profiles, and from the Voice workspace's TTS/model-family runtime management. ACI-4 reuses `neo_app.voice.service` as the single transcription authority; it does not add a second ASR registry under Memory Engine. The faster-whisper attachment path is local-files-only and must report missing/unavailable models rather than downloading implicitly.

## Related guides

- `guides/06_ASSISTANT/assistant_memory_and_scopes.md`
- `guides/06_ASSISTANT/project_brain.md`
- `guides/06_ASSISTANT/retrieval_gateway.md`
## Durable writeback governance after Phase 9

Admin → **Memory** owns the durable writeback review queue. The writeback status exposes observed, pending-review, approved, applied, rejected/archived, and superseded lifecycle states together with support counts, durable keys, candidate class, contradiction state, and review reasons.

Use **Approve + apply** only when a review-gated preference/project/canon-sensitive candidate should become active durable memory. Contradictory replacements do not hide the currently active memory until approval; applying the replacement supersedes the older fragment/fact. Reject leaves the candidate non-active.

Admin → Memory Engine may expose writeback infrastructure/job health, but it must not become the governance editor. Phase 10 provides the persistent unified job registry/progress layer.


## Unified background jobs after Phase 10

Admin -> **Memory Engine** owns infrastructure-level observability for long-running memory jobs. `neo_memory_jobs` is the canonical persistent registry, while the old `neo_data/admin/engine/index_jobs.json` Roleplay queue is now a compatibility projection over that registry rather than an independent worker authority.

The unified registry supports Project Brain rebuild, consolidation, embedding/reindex, deliberate/bulk writeback, and Roleplay vector indexing. It exposes phase, percent, current/total counts, message, cancel/retry state, and result/error history.

Worker threads remain process-local. A restart marks stale running jobs interrupted/failed and retryable; it does not claim cross-process worker continuation. Cancellation is cooperative at safe checkpoints.

See `guides/06_ASSISTANT/background_memory_jobs.md`.


## Admin UX expression after Phase 12

Phase 12 makes the visible Admin layout enforce this ownership contract instead of merely documenting it. Memory now has a dedicated **Durable Review** child tab separate from policy configuration. Memory Engine exposes Phase 10 persistent work as **Background Jobs** while keeping the historical `index_jobs` route readable. Delivery Projects keeps Linked Memory read-only and no longer offers memory-index repair as a normal project control. Admin Assistant / Operator keeps Scope Readout diagnostic-only; old activation calls redirect to Assistant → Scopes.

The canonical UI contract is `neo.admin.ux_consolidation.phase12.v1` at `GET /api/admin/ux-contract`. Top-level and child-tab compatibility aliases remain readable for saved state and older integrations. See `guides/07_ADMIN/admin_ux_consolidation.md`.

## Unified Brain control plane after NKB-12

The Unified Brain keeps the same Admin ownership split:

- **Memory** governs durable remembered content, review, conflicts/canon, lifecycle and citations.
- **Memory Engine** owns embedding/reranker resources, retrieval/index infrastructure, native-knowledge diagnostics, background jobs and NKB-12 migration/reindex operations.
- **Assistant → Inspector** explains the exact evidence path used by a turn; it is read-only and does not replace Memory/Memory Engine configuration.

NKB-8/NKB-9/NKB-10 do not create another Admin authority database. The planner reads the configured semantic-engine resources and task policy, native adapters resolve their owning sources, and the Context Packet/grounding layers consume the verified evidence.

### NKB-12 migration controls

Admin → **Memory Engine → Background Jobs** owns the operator-facing migration/reindex workflow. The backend exposes:

```text
GET  /api/memory/migration/nkb12/status
POST /api/memory/migration/nkb12/plan
POST /api/memory/migration/nkb12/run
```

Use the plan/dry-run before a full migration. Project files are replayed through the current Project ingestion path; native Image/Video/Voice/Prompt/Roleplay stores are inventoried rather than bulk-copied; static and embedding indexes are treated as rebuildable projections.

A successful migration does not promote ambiguous legacy `confirmed`, canon-like, or generated values into authoritative facts.



## ACI-5 — Memory Engine semantic qualification

Admin → Memory Engine owns semantic configuration **and qualification state**. Saving an embedding/reranker path invalidates a prior qualification when the configuration fingerprint changes. Use **Qualify semantic engine** to run real embedding/reranker probes. Provider/path presence alone is not readiness.

Admin exposes the current embedding profile fingerprint and Chroma compatibility. `reindex_required` means one or more non-empty vector projections were produced by a different/legacy embedding space; those indexes must be rebuilt from canonical SQLite/source authority before semantic reads resume. Keyword/FTS retrieval remains available while semantic readiness is degraded.

## ACI-6 — Core User Profile governance

Admin → **Memory → User Profile** owns the read-only proof/history view for Neo's bounded Core User Profile. The profile is derived from approved `neo_memory_facts`; it is not another memory authority.

Durable Review remains the only Admin surface for approving/rejecting review-gated preference candidates. The User Profile tab shows current/historical state, validity windows, fact/writeback IDs, source turns, support/confidence, and supersession links. Memory Engine continues to own retrieval/index infrastructure only.

Hard Project Sandboxes exclude General Core User Profile context by default unless project scope metadata explicitly opts in. Sensitive/private categories are not automatically projected into the Core User Profile.
