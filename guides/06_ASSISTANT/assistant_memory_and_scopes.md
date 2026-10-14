---
guide_id: assistant.memory_scopes
title: Assistant Memory, Scopes, and Project Brain
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
  - scope
  - project_brain
  - context
priority: 95
version: 15
updated: 2026-09-29
---

# Assistant Memory, Scopes, and Project Brain

## What a Scope is

An Assistant Scope is an internal context sandbox and retrieval-priority area. It tells Neo what context should be considered first.

Built-in scopes include General, Image Workspace, Video Workspace, Voice Workspace, Prompt & Captioning Workspace, and Roleplay Workspace. Custom scopes can be created for clients, workflows, or other focused contexts.

A Scope is **not** the same thing as a client/delivery Project.

## Canonical identity (Phase 1)

Neo now carries three separate context identities:

```text
surface_id = where the information or task originates
scope_id   = which Assistant context should be prioritized/sandboxed
project_id = the real client/work/delivery project, when one exists
```

Example:

```text
surface_id = image
scope_id   = image_workspace
project_id = heart_soul_2_ball
```

Historical Assistant storage still uses a field named `project_id` for Scope IDs such as `image_workspace`. Phase 1 keeps that legacy field readable but attaches canonical identity alongside it. Existing M9 surface memories such as `surface=image / project_id=image` are also translated through a compatibility filter rather than being mistaken for a real delivery Project.

When inspecting payloads/traces, treat `identity.surface_id`, `identity.scope_id`, and `identity.project_id` as product semantics. Treat `identity.compatibility.*` as temporary storage translation only.

## General Scope

General is the cross-surface Assistant workspace. It is intended for uncategorized questions, planning, and cross-surface coordination.

General should be able to answer questions using relevant memory from any Neo surface when the request calls for it. Memory remains scoped and filtered; Neo should not dump every stored memory into every chat.

## How to add useful memory today

### Save Scope

Use the Scope Description and Notes for durable rules about what the scope represents and what Neo should prioritize.

Good examples:

- what a client/project is;
- permanent style goals;
- workflow boundaries;
- important scope-specific constraints.

Do not use Scope Notes as an unlimited history dump.

### Save to Scope Knowledge

Use Context → Save to Scope Knowledge for deliberate structured information that should remain available inside the active scope. From Phase 7 this writes retrieval-authoritative knowledge into Unified Memory first while preserving the existing Assistant context record as a compatibility/UX projection.

Examples:

- client requirements;
- approved creative direction;
- durable workflow notes;
- important decisions.

### Save selected as memory

Use Chat → Save selected as memory for a specific durable fact/preference/decision from conversation. Phase 7 writes the canonical copy into Unified Memory while keeping the historical local capture/event bridge readable for compatibility.

Examples:

- a confirmed preference;
- a successful recurring setting;
- a confirmed bug/fix pattern;
- a client approval/decision.

Avoid saving greetings or one-off chatter as durable memory.

### Capture Current State

Use Project Brain → Capture Current State after an important surface state is worth remembering, such as a successful Image setup or a finalized workflow configuration. The snapshot is canonically ingested and equivalent repeated captures are deduplicated.

It is a manual pin/snapshot. It is not necessary after every minor change.

### Index Project Data

Use this when Neo has produced enough new saved output metadata/results that you want recent historical settings/results represented in memory. Phase 7 canonically ingests each indexed metadata row/fact while retaining the readable Project Brain index projection. General can coordinate across surfaces; custom Assistant/client scopes do not silently scan every creative surface.

### Upload Project Files

Use this for persistent project/reference documents. Phase 7 reuses the shared Assistant document extractor, so text-like files, PDF, and DOCX can become canonical Project Brain knowledge with source/content provenance. The original uploaded file remains stored.

### Rebuild Project Brain

Phase 7 makes this a real idempotent pipeline: scan allowed metadata, extract documents, ingest snapshots/indexes/Scope Knowledge/manual captures, deduplicate, consolidate, update SQLite FTS, validate canonical memory counts, and write a rebuild report. Long-running rebuild/embedding/reindex work now uses the Phase 10 unified Memory Job Service. Rebuild is backgrounded by default and exposes persistent phase/percent/item-count progress, Cancel, Retry, and reconnection after rerender/navigation.

## Native Image intelligence (NKB-15I-I1)

Image Workspace knowledge now has a first native-operation path in addition to semantic retrieval. This matters for questions where a database/operator result is authoritative and a retrieved shortlist is not.

Examples currently handled natively include:

- completed Image generation-run count;
- primary saved Image-output count;
- grouped usage such as most-used checkpoint/model, family, loader, sampler, scheduler, or workflow mode;
- controlled model-speed comparison when recorded runs contain a comparable cohort.

Assistant image attachments also receive a provenance/metadata pass. A proven Neo portable output can resolve to its exact sidecar even after the file has moved; a unique recorded SHA-256 can recover a native sidecar when portable identity is absent; external embedded metadata remains external; and pixels alone never establish hidden generation settings.

These native results still travel through the existing Retrieval Gateway / NKB-8 authority lane and NKB-9 Context Packet. Image does not maintain a separate Assistant brain or duplicate metadata database.

I1 is the provenance/analytics foundation. I2 adds canonical procedures and live capability proof, I3 adds historical Recipe/history/comparison/lineage/replay intelligence, I4 completes the first-pass runtime operation set, I5 executes the deterministic R9/Guide acceptance gates, I6 hardens answer-ready native evidence and semantic population filters, and I7 adds session-scoped Image referent continuity plus Recipe/prompt grounding for attachment follow-ups.

## Native Image procedures and capability proof (NKB-15I-I2)

Image-native questions about **how to do something**, **where a current tool lives**, **what is selected now**, or **whether a feature is actually usable** no longer rely on one generic Guide/retrieval path.

For current procedures, Neo resolves a task to the **current canonical Image Guide** and hydrates the configured procedure or troubleshooting section. This preserves the actual authored UI path and steps. Initial normalized procedures include Background Removal (`Image → Finish → Remove Background`), Outpaint (`Image → Generation → Outpaint`), Results reuse, and Reference. Full-guide requests can hydrate the current Guide body instead of reconstructing a procedure from a short search excerpt.

For current state, the `neo.image` native adapter receives the same read-only Image surface snapshot already used by Neo's surface-context system. When the browser supplied a current-turn snapshot, Neo can establish selected model/family/loader, seed, sampler/scheduler, steps/CFG, workflow mode, current reference/source slots, extension settings and backend/route context. If no current-turn snapshot is available, Neo must say that persisted UI state may be older than the visible browser state.

Capability/readiness answers keep separate proof for:

1. model-family / loader support;
2. selected route compatibility;
3. backend runtime connectivity/readiness;
4. required dependency/node presence;
5. current workspace availability and whether the feature is enabled for this run.

Therefore **supported**, **compatible**, **available**, **enabled**, and **ready** are not interchangeable. For example, a feature may support SDXL but be blocked on the selected Outpaint route, or ControlNet may be family-supported but blocked because a required Comfy node is missing. Neo should report the failing layer rather than collapsing either case into a vague “unsupported.”

These results still flow through the existing NKB-8 native-authority lane and NKB-9 Context Packet. Image does not maintain a parallel Assistant brain.

## Native Image Recipe, history, lineage and replay (NKB-15I-I3)

Image-native historical questions can now use recorded Image sidecars directly instead of asking semantic retrieval to reconstruct a generation from nearby text. Exact Recipe, comparison and replay operations require a proved target: an exact current-turn Neo attachment, an explicit result/output/file identifier, the currently selected Results record, or another deterministic historical/lineage anchor. If Neo cannot establish the target, it must say so rather than selecting the closest history match.

Current native historical operations include:

- **Recipe:** recorded model/checkpoint/VAE, prompts, sampling, canvas, source assets, extensions, route, lineage, timing and replay fields, with explicit completeness for older records;
- **History search:** bounded descriptive discovery that returns candidates without claiming identity;
- **History filter:** deterministic filtering over implemented fields such as model, mode, extension use, dimensions, steps and date;
- **Recipe comparison:** field-level `same / changed / added / removed / unknown` results between two exact records or supported relative anchors such as selected-result versus recorded parent;
- **Lineage:** recorded parent, root, ancestor and direct-child relationships from Image sidecars;
- **Replay readiness:** current checks for saved checkpoint/backend profile/provider/source assets/extensions and recorded LoRA assets where Neo has authoritative availability data.

Historical fields are never filled from current UI defaults. External image metadata can support a partial external replay preflight, but it is not upgraded into a Neo execution record. A replay packet can say that the required current environment appears ready, but it never guarantees pixel-for-pixel reproduction because model/backend/library drift can change the generated pixels.

These packets still travel through the existing `neo.image` adapter, NKB-8 authority handling and NKB-9 Context Packet. Image sidecars remain the execution authority; the Assistant does not maintain a duplicate history database.

## How to verify what Neo can see

Use **View Context Pack** when debugging Assistant recall. It is the best current inspection path for checking which Scope, Project Brain, Guide, live surface, and memory sections are entering the Assistant context.

## Retrieval after Phase 6

Normal Assistant chat uses one **Retrieval Gateway** rather than independently querying the document/chunk Knowledge Index and Unified M9 memory. Phase 6 adds **Scope Priority, Not Scope Prison** on top of that gateway.

General starts with General durable memory and expands only when the query indicates another surface/project or when an explicit recall request needs bounded non-Roleplay discovery. Surface/custom scopes keep local context first while still allowing relevant General memory and query-driven cross-surface context. Linked Delivery Projects are project-first. Detailed Roleplay memory requires an explicit registered universe/world/scene sandbox.

Phase 7 has now migrated Project Brain and manual Scope Knowledge writes to canonical Unified Memory ingestion. Legacy JSON/snapshot/index/context files remain inspectable compatibility projections and rebuild sources; normal memory retrieval still enters through the Retrieval Gateway.

See `guides/06_ASSISTANT/retrieval_gateway.md`, `guides/06_ASSISTANT/scope_priority_retrieval.md`, and `guides/06_ASSISTANT/project_brain_canonical_ingestion.md`.

## Admin ownership after Phase 2

Assistant Scope selection/editing is owned by the **Assistant surface**. Admin → Assistant / Operator now exposes a **read-only Scope Readout** for diagnostics, memory preview, and Control Center traces; it is not a second place to activate/edit the user's normal Scope.

Admin → Projects is now explicitly **Delivery Projects**. A Delivery Project can later link to an Assistant Scope, but it is a real client/work project rather than an Assistant context sandbox.

For memory administration:

- Admin → **Memory** owns remembered content/governance and Search + Citations.
- Admin → **Memory Engine** owns retrieval/index infrastructure.
- Admin → **Backends** owns provider/model generation sampling.

See `guides/07_ADMIN/admin_ownership_and_memory.md`.
## Universal Assistant behavior after Phase 3

The active Scope controls context priority, not the kinds of tasks Neo Assistant is allowed to perform. Normal Assistant requests are handled through the Universal Assistant Contract.

A request can be creative writing, a recipe, social caption, client response, video script, code, analysis, advice, or another normal assistant task. Neo should perform the task directly rather than returning Control Center lanes or a plan to do the task later.

Broad behavior modes (`COMPLETE`, `RECALL`, `ANALYZE`, `ADVISE`, `ACT`, `CONTINUE`) are internal orchestration hints only. They do not limit subject matter.

See `guides/06_ASSISTANT/universal_assistant_contract.md`.



## Model-facing context after Phase 4

Scope, Project Brain, Guides, live surface context, M9-selected memory, and uploaded document text can still inform Assistant replies, but they now pass through the Assistant Prompt Compiler before provider dispatch.

The compiler deliberately does not forward the raw Brain Workspace/Control Center prompt blocks or the Context Pack monolith. It also avoids duplicating the current message/thread from Context Pack because the conversation already contains them. This isolation changes **prompt assembly**, not memory ownership or retrieval. Phase 5 still owns retrieval consolidation.

See `guides/06_ASSISTANT/prompt_compiler_and_control_center.md`.


## Phase 8 automatic surface memory

Project Brain is no longer the only way useful canonical memory enters Neo. Phase 8 adds the Unified Surface Memory Ingestion Registry for successful runtime work from Image, Video, Voice, Prompt/Captioning and other registered surfaces. This automatic history is selective: failed/running/queued events and ordinary Assistant chatter are not promoted into searchable durable fragments. Roleplay remains sandbox-owned and is replayed through its dedicated adapter rather than generic live writes.

Use Project Brain when you deliberately want to pin project/scope knowledge, documents, snapshots, or indexed historical data. Use normal surface workflows for automatic successful task history. Both are retrieved through the same Retrieval Gateway.
## Durable memory after Phase 9

Searchable history is not automatically a durable preference/fact. Phase 9 runs successful Assistant turns and successful registered surface events through the existing M11 writeback engine as **durable candidates**. Repeated low-risk workflow/settings patterns may auto-promote after independent support; user preference changes, project decisions, contradictions, cross-project claims, and canon-sensitive changes remain review-gated.

When a reviewed replacement is applied, the older durable fragment/fact is superseded so retrieval sees one active truth for the durable key. Ordinary chat remains non-durable. See `guides/06_ASSISTANT/durable_memory_writeback.md`.


## Background memory jobs after Phase 10

Long-running memory operations use the persistent `neo_memory_jobs` authority through `MemoryJobService`. Project Brain rebuild, memory consolidation, embedding/reindex, deliberate/bulk writeback, and the historical Roleplay vector-index job can share the same lifecycle and progress model.

Job rows persist across UI navigation. Worker threads remain process-local; a server restart marks unfinished running jobs as interrupted/failed and retryable rather than falsely claiming background continuation. Cancellation is cooperative at safe checkpoints. Tiny operations remain synchronous.

See `guides/06_ASSISTANT/background_memory_jobs.md`.


## Phase 11 Assistant UX

The user-facing Assistant tab now uses **Scopes** as the canonical label for the historical `projects` route. The route/storage alias remains readable; do not confuse an Assistant Scope with an Admin Delivery Project.

Assistant → **Memory** is now a Memory Lens backed by canonical Unified Memory, durable writeback state, recent retrieval proof, memory jobs, manual pins, and Scope Knowledge. It is for viewing what Neo remembers in the current context; durable approval/rejection and serious governance remain in Admin → Memory.

Assistant → **Context** owns Scope Knowledge authoring plus context/retrieval proof. Assistant → **Inspector** owns detailed runtime/compiler/retrieval/job diagnostics. Normal Chat intentionally avoids raw technical proof so the Universal Assistant remains focused on the user's task.

See `guides/06_ASSISTANT/assistant_scopes_memory_lens.md`.

## Native Image diagnostics, jobs, integrity and remaining operations (NKB-15I-I4)

The Assistant can now answer the remaining first-pass Image-native question families through the same `neo.image` → NKB-8 → NKB-9 path rather than generic semantic retrieval.

Key examples include:

- **Generation attempts and jobs:** count durable attempts, inspect the current/recent job, and report recorded failure evidence.
- **Diagnostics:** separate observed native causes from unsupported hypotheses; if no cause is recorded, Neo must say it is not established.
- **Output integrity:** check exact output-file existence/recorded hashes or run a read-only Image-library orphan/identity-collision scan.
- **Delete preview:** show the owning Image output-service delete manifest without deleting anything.
- **Current/saved prompts:** read the current Image prompt from live workspace state or search the native Prompt Library; a saved prompt match does not prove historical generation use.
- **Parameter help:** explain current Image parameters from the canonical Image parameter Guide and label tuning recommendations as advisory.
- **Trends/performance:** compute declared statistics over recorded native populations rather than using retrieval-result counts.
- **Reuse/handoff:** preflight reuse or cross-scope handoff without loading settings, starting generation, deleting files or executing the target action.
- **Attachment provenance:** preserve the distinction between exact Neo identity, recognized external embedded metadata and unidentified/pixel-only images.

I1–I4 now provide baseline runtime routing for all 39 R7 Image operations. This does **not** mean Image Intelligence is release-qualified: the R9 408+ natural-language benchmark and the remaining Image Guide normalization/hardening still have to pass.


## Image Intelligence acceptance and Guide normalization (NKB-15I-I5)

Image Native Intelligence is now benchmarked against the R9 deterministic/native contract rather than being considered complete merely because every operator exists.

The recorded I5 run verifies:

```text
136 canonical Image intelligence cases
3 required wording forms each
408 / 408 route prompts passed
39 / 39 native Image operations covered
114 / 114 Image guides normalized
160 / 160 focused runtime/regression tests passed
```

Image Guide lifecycle/role metadata is normalized through `guides/01_IMAGE/_guide_registry.json`. The registry preserves the authored Markdown bodies while distinguishing current procedures/references from historical phase records and experimental material. Current canonical procedures should therefore be used for user-facing how-to/navigation answers; historical records remain available when the user asks about implementation history or older behavior.

The deterministic/native core is accepted. Provider-specific final prose quality remains a separate release QA gate because it requires an actual connected/selected Assistant backend. Neo must still preserve exact values, proof strength, unknown states, current-versus-historical distinctions, external-versus-native provenance and performance cohort caveats in the final provider response.

Do not weaken native proof merely to make a provider answer sound more complete.


## Native Image answer synthesis and semantic filters (NKB-15I-I6)

Image-native operators may return rich structured scope packets for Inspector/provenance, but those packets are **not** chat-response templates. The Assistant now receives an answer-ready fact projection for native Image evidence and should answer in normal language rather than echoing packet JSON, schema IDs, operation IDs, epistemic labels or authority bookkeeping.

Native analytics also treats user qualifiers as binding population constraints. For example, `average generation time for Krea 2 models` must summarize only records belonging to the Krea 2 family group; it must not compute an all-history average merely because the family could not be resolved as one exact checkpoint name. Broad Krea 2 requests may include recorded RAW and Turbo family variants, while an explicit `Krea 2 Turbo` request stays specific.

If the user names a model/family population that Neo cannot establish from authoritative Image history, the native result fails closed (`not_established`) instead of substituting the unfiltered population. This request↔result constraint validation applies to Image inventory/analytics/performance paths that support semantic population filters.

Full structured native packets remain available to Knowledge Inspector for debugging. Ordinary Assistant answers should lead with the requested fact, use human-readable values and sample sizes/caveats where relevant, and expose raw native structures only when the user explicitly asks for debug/Inspector data.

## Attachment referent continuity and Recipe grounding (NKB-15I-I7)

When a user explicitly attaches an image, Assistant now keeps a bounded **session-native Image referent** after the turn. This is not durable Memory and is cleared with the chat session. It exists so normal follow-ups such as `give me the settings`, `what seed did it use?`, `can I replay it?`, or `give me its prompt for Krea2` remain bound to the same proved asset instead of falling back to visual description or nearby Image history.

Referent precedence is conservative: a new current-turn attachment wins; multiple current-turn images create an ambiguous referent; an explicit result/output/file ID can override an older session referent; and visual similarity never creates identity. Exact native referents resolve back to their authoritative Image sidecar. External embedded-metadata referents stay external and produce only a partial external Recipe.

For generation facts, recorded metadata outranks vision. Vision may describe what the image visibly contains, but it cannot invent or fill prompt, seed, steps, sampler, scheduler, checkpoint, LoRA, VAE or workflow fields. If the user requests the original prompt, Neo uses the recorded prompt when available. If the user asks for a new prompt adapted to another model, Neo should begin from that recorded prompt; when no source prompt is recoverable, any visual reconstruction must be labeled as a new approximation.



## Native exact response delivery and continuation (NKB-15I-I8)

When Neo already owns an authoritative native field, the Assistant should not ask a language model to reproduce that field from memory. Image is the first implementation of this rule.

Normal generation-settings questions may use a deterministic Recipe summary containing recorded scalar values such as model, VAE, seed, steps, CFG, sampler, scheduler and resolution. Long recorded prompts are represented by length plus a short preview rather than being dumped into the settings answer. Full structured Recipe packets remain Inspector data.

Explicit exact-field requests such as `give me the full original prompt` use Neo's native exact-field renderer. The renderer reads the authoritative Image sidecar field after exact target resolution and bypasses provider synthesis. Prompt-adaptation requests such as `rewrite this prompt for Krea2` are different: they keep provider synthesis, but the recorded source prompt remains the grounding source.

If an exact native field is too long for one response, Assistant stores a bounded session continuation pointer under `native_response_state`:

```text
scope / operation / field
source reference
cursor / total length
SHA-256
status
```

The full field is **not** copied into session state. On Continue, Neo re-resolves the same authority field, verifies the hash, and emits the next exact character range. If the sidecar disappears or changes, continuation fails closed rather than reconstructing text from model memory.

This state is ephemeral like `native_referents`: it is session continuity, not durable Memory or Project canon, and session clear removes it. The contract is generic so future Video/Voice/native scopes can reuse exact-field continuation without copying Image's schema.

## Semantic retrieval setup and current attachment boundary

Neo's durable memory truth remains in its canonical SQLite/source records. Semantic models help **find** relevant records; they do not become memory authority.

In **Admin → Memory Engine**, treat these as separate concepts:

```text
Embedding runtime   e.g. Sentence Transformers
Embedding model     the model you choose
Reranker runtime    e.g. CrossEncoder
Reranker model      the model you choose
Vector index        Chroma / configured semantic index
```

`Sentence Transformers` is a runtime/framework, not the name of the active embedding model. If the selected model/path is empty or fails real qualification, Neo keeps the semantic lane unavailable/degraded and continues through canonical SQLite/FTS lexical retrieval where permitted. It does not present deterministic hash vectors as the configured semantic model. A configured provider name alone does not prove that a real semantic model is active.

A practical reference pair for users who want a known-compatible local semantic stack is:

```text
Qwen/Qwen3-Embedding-0.6B
Qwen/Qwen3-Reranker-0.6B
```

These are recommendations, not requirements. Different compatible local models may be selected based on hardware, language and retrieval needs.

Changing the embedding model is different from changing the reranker. Existing vectors belong to the embedding model/configuration that created them, so switching embedding spaces requires reindexing before semantic search can be trusted. A reranker can normally be replaced without rebuilding the stored embedding index.

### Ordinary Assistant ephemeral document knowledge

Ordinary Assistant document uploads now use the same `NeoSourceDocument` preparation contract as persistent Project files. PDF/DOCX/PPTX/XLSX therefore receive the same capability-based Docling-first preparation with Neo lightweight fallback, while text-like sources stay on the lightweight parser path.

The lifecycle is deliberately different from Project knowledge: ordinary chat documents are stored under the Assistant attachment/session tree, fragmented into an ephemeral source bundle, and searched only for that chat. They are not inserted into canonical Unified Memory or Project Brain. Follow-up turns can retrieve previously sent chat documents without re-uploading them.

Ephemeral retrieval uses session-local SQLite FTS5 for lexical recall, then the Admin-selected embedding model and reranker when they are available. Semantic/reranker failure is reported in retrieval diagnostics and falls back to the FTS/lexical shortlist rather than pretending the external model ran. Deleting the attachment removes its source bundle; session cleanup removes attachment directories and the session FTS index.

Persistent Project files remain the correct path when a source should survive the chat lifecycle, participate in Project canon, or be maintained as durable knowledge.

### Ordinary Assistant ephemeral audio/media knowledge

ACI-4 extends ACI-3's chat-scoped source lifecycle to audio and video-audio attachments. Media is transcribed through Neo's existing Voice transcription service, normalized into `NeoSourceDocument`, fragmented with time-range provenance, and retrieved with the same ephemeral FTS/semantic/reranker pipeline as document attachments. Earlier media attachments remain eligible for follow-up questions in the same chat.

ASR settings are configured under **Admin → Assistant / Operator → Voice Input**. The default production path uses `faster-whisper` when installed/configured and requests word timestamps for evidence provenance. Missing/unconfigured ASR remains `stored_only`/diagnostic evidence rather than a fabricated transcript.

Video attachments contribute **audio-track transcript evidence only** in ACI-4. The media source records `visual_track_indexed=false`; claims about what appears visually in frames require a separate future visual-video evidence path. Media attachments remain ephemeral unless they are uploaded through the persistent Project path, which reuses the same media adapter but writes through Project Brain authority.

### Unified source preparation

Persistent Project files now normalize through the production `NeoSourceDocument` contract before contextual fragments are built. Structured binary documents (`PDF`, `DOCX`, `PPTX`, `XLSX`) prefer Docling + RapidOCR in the default `auto` router mode; Neo's lightweight parsers remain the fallback path. Text/Markdown/JSON/CSV/HTML stay on the lightweight path because they do not benefit from the heavier parser by default.

The source contract preserves parser identity/version, content and revision hashes, hierarchy, tables/pictures, and source locators such as page/bounding-box, slide, sheet/row, line, and future time-range/speaker locators. This changes source preparation only; SQLite remains canonical memory authority and Chroma remains a rebuildable semantic projection.

Ordinary Assistant documents now consume this same contract through the ACI-3 ephemeral lifecycle. Important long-lived sources should still be Project files because ephemeral attachments remain chat-scoped and are never promoted automatically. See `guides/06_ASSISTANT/unified_source_preparation.md`.


## ACI-5 semantic qualification and vector-space safety

Semantic readiness is now qualification-backed. Admin-selected model paths are configuration, not proof that inference works. Neo records a real-inference qualification for the current semantic configuration and derives an embedding profile fingerprint from the resolved vector-space settings. SQLite semantic caches and Chroma collections are accepted only when they match that fingerprint/dimension.

If qualification is missing/failed or a collection is stale, retrieval remains available through canonical SQLite/FTS lanes and diagnostics report `qualification_required`, `blocked`, `degraded`, or `reindex_required` rather than silently substituting hash vectors and calling the result semantic. Explicit `lexical_overlap` reranking remains a non-semantic degraded mode.

## ACI-6 Core User Profile and temporal preferences

Assistant now has a bounded **Core User Profile** derived from approved durable SQLite facts. It is not a second profile database and it is not inferred from every chat. Pending Durable Review items remain outside normal profile context.

Current approved profile facts carry temporal/provenance proof (`fact_id`, `writeback_id`, source turn/event, `valid_from`, `valid_to`, supersession links). When a reviewed preference changes, the older fact becomes historical and the replacement becomes the one current fact for that profile key. Historical facts remain inspectable but are not injected as current preference context.

General and built-in Assistant scopes may use this profile. A hard Project Sandbox excludes it unless that Project explicitly opts in. Current user instructions in the active chat always override older profile lines.

Use **Assistant → Memory** to see the user-facing profile view and **Admin → Memory → User Profile** for read-only proof/history. Approval/rejection remains under **Admin → Memory → Durable Review**.

## ACI-7 long conversation checkpoints and foreground latency

Long Assistant sessions keep raw messages as canonical source truth, but history is now selected by **turn dependency**, not merely by chat length. Self-contained preferences and standalone factual lookups compile the current turn without unrelated old checkpoints; deictic follow-ups use a bounded recent raw window; explicit recall/continuation uses source-linked hierarchical checkpoints plus recent turns. Checkpoints carry source message IDs/ranges and regenerate when older canonical source history changes; they are never durable memory authority.

ACI-7 keeps expensive maintenance out of the foreground answer path. Unified Memory reads do not bulk-embed missing fragments while the user waits; they use current FTS/current-profile vectors and queue deduplicated embedding catch-up in the background. **Already-ingested Project evidence is never reparsed with Docling during retrieval hydration.** Neo hydrates from the stored projection, verifies the original source revision/content hash, and marks changed sources stale for the normal re-ingestion path.

Foreground retrieval obeys Admin `query_top_k`, `rerank_top_n`, and `max_context_chars`. Assistant lanes supply candidates without local reranking; the Retrieval Gateway owns one bounded final rerank. Qwen3 reranking is candidate-capped and processed in small batches. If the qualified reranker is unavailable, the fused top-K/FTS shortlist remains available to authority adjudication instead of being truncated to the reranker output size.

Clearly self-contained preference/instruction turns can bypass retrieval entirely. Current-turn attachments, recall, continuation, actions and context-dependent questions still retrieve normally. Retrieval relevance and conversation-history relevance are separate decisions.

Use **Assistant → Inspector** to diagnose slow turns. ACI-7 exposes foreground stages (`brain_control_context`, retrieval lane timing, `context_pack`, `prompt_compile`, `total_before_backend`) separately from `provider_generation`, so backend latency and Neo orchestration latency are no longer conflated.

## ACI-7.2 Conversation Continuity Index

Long-chat continuity uses the canonical Assistant session messages as source truth and maintains a disposable, **same-chat-only** retrieval projection. New/changed messages are appended incrementally to a per-session SQLite FTS5 index. Source-linked checkpoints are indexed as derived continuity records, and lightweight topic/entity/decision anchors make exact older decisions easy to recover.

The continuity router deliberately separates four cases:

```text
current_only      standalone/self-contained current request
recent            immediate deictic/pronoun follow-up
chat retrieval    specific older topic/decision in this same chat
checkpointed      broad continuation / “where did we leave off?” recall
```

A specific request such as `What did we decide about Kest earlier?` performs targeted same-chat retrieval and does **not** automatically prepend broad checkpoints or unrelated old raw turns. A broad request such as `Where did we leave off?` may use hierarchical checkpoints. A standalone lookup such as `Who is Kest?` does not search old chat unless the wording actually asks for prior-chat context.

SQLite FTS is updated synchronously because it is cheap. The private Chroma projection is incremental and queues after the Assistant has completed the turn; it never blocks provider generation merely to catch up embeddings. If exact FTS/topic-anchor recall is already strong, foreground chat retrieval skips the embedding query entirely. If semantic recall is needed, only the current chat collection is queried and no hash-vector fallback is presented as semantic evidence.

Conversation decisions may carry current/superseded state inside this **chat-local projection** so newer same-chat decisions can outrank historical versions. This is continuity metadata, not ACI-6 durable temporal user memory. No chat message is automatically promoted into `neo_memory_facts` or the Core User Profile.

Deleting or clearing a chat removes its SQLite continuity index and corresponding private Chroma projection. Rebuilding this projection never changes canonical chat history or durable memory authority.
