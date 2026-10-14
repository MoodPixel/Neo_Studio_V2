---
guide_id: assistant.context_preparation_bakeoff
surface: assistant
scope: built_in
title: ACI Context Preparation Bake-Off
tags:
  - assistant
  - context
  - documents
  - docling
  - benchmark
  - retrieval
priority: 70
version: 1
updated: 2026-09-28
---

# ACI Context Preparation Bake-Off

Neo's ACI-1 benchmark compares the current ordinary Assistant attachment preparation path against an isolated Docling-assisted path.

This is an **engineering benchmark**, not a live Assistant feature and not a parser switch.

## What stays unchanged

ACI-1 does not replace:

```text
Neo Assistant LLM
SQLite canonical memory/knowledge
Project Brain ingestion
live Assistant attachment routes
user-selected embedding/reranker architecture
```

Both parser arms eventually feed the same downstream Neo fragment/retrieval stack so preparation quality can be compared without changing the reasoning model between arms.

## Build the deterministic ACI-1B corpus

```text
python scripts/build_aci1_reference_corpus.py
```

The generated corpus is local runtime data and should not be committed as product source binaries.

It contains redistributable Neo-authored fixtures for:

```text
large DOCX
120-page PDF
multi-column PDF
scanned PDF
table-heavy PDF
chart/image-heavy PDF
PPTX
XLSX
Markdown
HTML
document screenshot
messy mixed-layout PDF
```

The truth manifest is generated from the authored fixture definitions, not from parser output.

## Run ACI-1B in one command

```text
python scripts/run_aci1b_reference_bakeoff.py \
  --docling-python <path-to-isolated-docling-python>
```

If the isolated runtime is missing Docling, the run still measures Current Neo and reports the Docling arm as `blocked`. It does not substitute another parser.

## Run strict semantic evaluation

After installing the isolated semantic requirements:

```text
python scripts/run_aci1b_reference_bakeoff.py \
  --docling-python <path-to-isolated-docling-python> \
  --semantic \
  --embedding-model Qwen/Qwen3-Embedding-0.6B \
  --reranker-model Qwen/Qwen3-Reranker-0.6B
```

Controlled semantic mode is fail-closed. Missing real models/dependencies produce a blocker; benchmark results must not silently use Neo's production hash or lexical-reranker fallbacks.

## Read the report correctly

Use the **parity-only aggregate** for the main Current-Neo vs Docling comparison.

The document screenshot fixture is supplemental because the live current Neo image-attachment path uses native Image intelligence rather than the legacy document extractor.

A source can have high truth-text recall and still preserve poor structure. Evaluate separately:

```text
text survival
heading hierarchy
table blocks
picture/chart blocks
page provenance
bbox provenance
retrieval quality
resource cost
```

Do not treat raw character count as document understanding.

## Current ACI-1B checkpoint

The deterministic reference corpus and Current-Neo baseline have been executed.

Current legacy attachment preparation on the 11 parity fixtures found 14 of 27 required truth terms. It also produced zero structured table/picture blocks and zero page/bbox provenance in this reference run.

The Docling arm was not executed in the implementation environment because the isolated environment could not install/import Docling. That is an environment blocker, not a parser-quality result.

ACI-1 remains open until both arms run on the same corpus, strict semantic retrieval is measured, and the same Neo Assistant LLM is evaluated over both evidence paths.

## Actual development-machine continuation

ACI-1B now has a Windows development-machine runner so the remaining evidence can be produced on the machine that actually runs Neo rather than inferred from an offline implementation environment.

Environment boundary:

```text
Neo .venv
  Current Neo preparation
  Neo Assistant backend resolution
  same-LLM answer pass

isolated ACI benchmark env
  Docling
  explicit RapidOCR for PDF OCR
  Sentence Transformers
  Chroma
  semantic reference/user-selected models
```

Set up and run:

```text
scripts\setup_aci1b_dev_machine.bat
scripts\run_aci1b_dev_machine.bat
```

The default semantic control remains the fixed Qwen3 0.6B pair so parser comparisons are reproducible. Use:

```text
--semantic-source admin
```

only when the goal is to qualify the embedding/reranker models selected in the current Neo Admin state. Both parser arms still receive the same selected models.

The complete run now measures three layers separately:

```text
1. source preparation
   truth survival / structure / provenance / resource cost

2. retrieval
   Chroma dense + SQLite FTS5 + weighted RRF + CrossEncoder
   recall / MRR

3. same Assistant LLM
   source-answer accuracy
   grounded behavior
   fail-closed behavior
   unsupported-answer rate
```

Queries carry answer truth separately from retrieval truth. This avoids requiring the Assistant to repeat synthetic benchmark labels that are useful for retrieval scoring but are not the natural answer to the question.

A parser arm that produces zero fragments is not removed from the LLM benchmark. It receives an evaluated zero-retrieval result, allowing Neo to measure whether the same Assistant model correctly returns `NOT_ESTABLISHED` instead of inventing an answer.

The report contains an `acceptance` block. Its `technical_candidate_ready` flag is a deterministic engineering gate only. ACI-1 never enables Docling in production and never authorizes ACI-2 automatically.

## ACI-1B implementation status after development-machine runner pass

Implemented:

```text
isolated Docling worker
explicit RapidOCR PDF benchmark policy
isolated semantic worker
real-model qualification
fixture-local Chroma READ/WRITE
SQLite FTS5 + RRF + CrossEncoder
same Neo Assistant backend over both arms
answer-grounding/fail-closed scoring
Windows benchmark environment bootstrap
one-command actual-machine runner
human-review acceptance gate
```

Still pending until executed on the actual Neo development machine:

```text
real Docling scores
real Qwen/Admin semantic scores
real same-LLM answer scores
resource measurements from that machine
final ACI-1 integration decision
```

Do not copy the earlier offline Current-Neo baseline forward as though these pending results were executed.

## ACI-1B repair / resume workflow — 2026-09-28

The first real development-machine continuation exposed several harness/environment issues before semantic retrieval could complete:

- Admin model-path edits could be overwritten by a deferred Admin rerender before Save.
- `--semantic-source admin` correctly read Admin state, but a stale embedding path caused semantic qualification to fail.
- the production `.venv` did not have `pypdf`, so the Current-Neo PDF control reported `pdf_text_extractor_unavailable`;
- one messy mixed-layout Docling result hit a nullable/invalid structured iterator edge;
- Windows subprocess output could raise a CP1252 `UnicodeDecodeError`;
- reruns had no way to reuse already-successful preparation.

The repair pass keeps the production parser route unchanged and adds fail-fast / resume behavior.

Before a final parity run, refresh Neo's production requirements so the legacy Current-Neo PDF control is actually available:

```text
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Admin-selected local model paths are now validated before corpus generation. A missing absolute local path stops immediately instead of spending time on Docling first.

To resume an existing run directory and reuse successful Current-Neo / Docling preparation while recomputing semantic retrieval and the same-LLM pass:

```text
scripts\run_aci1b_dev_machine.bat \
  --semantic-source admin \
  --output-dir neo_data\runtime\aci1\runs\aci1b_<timestamp> \
  --reuse-prepared
```

Reuse is conservative:

- prior preparation errors/blocked arms are rerun;
- prior Current-Neo PDF arms marked `pdf_text_extractor_unavailable` are rerun after the production PDF dependency is restored;
- semantic retrieval is always recomputed with the currently selected embedding/reranker models;
- same-LLM evaluation is always recomputed;
- production parser switching remains disabled.

The runner now prints per-fixture preparation progress and semantic-stage heartbeats instead of appearing idle for long periods.
