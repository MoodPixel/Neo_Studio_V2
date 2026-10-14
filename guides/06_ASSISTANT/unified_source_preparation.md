# Unified Source Preparation

Neo Studio normalizes persistent Project files through one production source contract before fragmenting, indexing, retrieval, or grounding.

## Contract

The canonical preparation object is:

```text
NeoSourceDocument
  source identity
  source path/name/media type
  content SHA-256
  revision ID
  parser identity/version/mode
  parser attempts + fallback state
  structured blocks
  capabilities
  extraction metadata
```

Each source block carries:

```text
block ID
kind
text
heading path
source locator
optional parser confidence
parser metadata
```

The contract schema IDs are:

```text
neo.knowledge.source_document.v1
neo.knowledge.source_block.v1
neo.knowledge.source_parser.v1
neo.knowledge.source_revision.v1
```

`NeoSourceDocument` is a preparation contract. It does **not** replace Project manifests, Unified Memory, source evidence envelopes, SQLite authority, or Chroma projections.

## Parser routing

The default router mode is `auto`.

### Structured binary documents

```text
PDF
DOCX
PPTX
XLSX
  -> Docling first
  -> RapidOCR/table structure for PDF
  -> Neo lightweight parser if Docling is unavailable or conversion fails
```

### Lightweight text sources

```text
TXT / Markdown
JSON / JSONL
CSV / TSV
HTML
code/config/log text
  -> Neo lightweight structured parser
```

This is deliberately capability-based. A heavy parser is not used merely because it exists.

For diagnostics, `NEO_SOURCE_PREPARATION_MODE=lightweight` disables the advanced attempt. An advanced failure is recorded in `parser.attempts`; fallback is explicit rather than silent.

## Source locators

The contract can carry locators for:

- page and page region / bounding box;
- heading/section path;
- paragraph or line range;
- slide;
- sheet and row range;
- table/picture/chart items;
- generic structured source items;
- media time ranges and optional speaker labels for transcribed audio/video-audio evidence.

Docling source items preserve page and bounding-box provenance when available.

## Revision identity

Every prepared file records its SHA-256 and derives a stable revision ID:

```text
content_sha256 = <64-char SHA-256>
revision_id    = rev_<first 24 chars>
```

Project ingestion may already know the upload hash; that hash remains compatible with the source contract and NKB-7 revision lineage.

## Dependencies

Neo keeps official source-intelligence dependencies in only the normal root requirement files:

```text
requirements.txt
requirements-memory.txt
```

`requirements-memory.txt` owns:

- Chroma;
- Sentence Transformers / Transformers / Torch;
- Docling + RapidOCR;
- faster-whisper for local Assistant/Project media transcription;
- python-docx / python-pptx / openpyxl lightweight fallbacks.

`requirements.txt` retains `pypdf` for the lightweight PDF path and current ordinary Assistant attachment compatibility.

`setup_neo_studio_venv.bat` installs both files into Neo's root `.venv`. No ACI/benchmark virtual environment is part of the product architecture.

## Audio/media preparation

ACI-4 extends the same contract to audio files and the audio track of video files. Neo reuses `neo_app.voice.service` as the single ASR authority rather than introducing a second transcriber. The media adapter emits transcript blocks with `media_time_range` locators, language/runtime metadata, optional word timestamps/confidence, and parser/model provenance.

The default production dependency is `faster-whisper`. Its ACI-4 loader runs local-files-only so attachment transcription does not implicitly fetch missing model weights. The older Whisper provider remains a compatibility option. ASR configuration is owned by **Admin → Assistant / Operator → Voice Input**.

Video capability is intentionally bounded in ACI-4: only the audio track is normalized here. `visual_track_indexed=false` is recorded and Neo must not treat the transcript as evidence that visual frames were inspected.

## Lifecycle boundary

Persistent Project files and ordinary Assistant document/media attachments share this preparation contract. They do **not** share persistence semantics.

- Project files continue into Project manifests, canonical Unified Memory and durable Project retrieval.
- Ordinary Assistant documents and transcribed media become chat-scoped ephemeral source bundles beside the temporary attachment. A session-local SQLite FTS5 index plus optional Admin-selected embeddings/reranking retrieves bounded fragments for the current question. No canonical-memory or Project write occurs automatically.

Deleting an ordinary attachment removes its prepared bundle. Session cleanup removes the session attachment tree and its ephemeral FTS index. This keeps one parser contract without creating a second durable knowledge system.

## Known follow-up work

The accepted source-preparation benchmark identified two quality areas that remain follow-up targets rather than blockers for the contract itself:

- OCR confidence/evidence sufficiency for difficult scanned text;
- retrieval ranking for some dense multi-column layouts.

Those should be improved above this shared contract instead of creating another format-specific ingestion path.
