# Neo Model Guide — Phase 4.6 Voice Model Lifecycle + Phase 4.5 Repository Snapshot Pipeline + Phase 8 File Download Manager

## Phase 4.6.3 — Voice lifecycle physical closure

The physically exercised post-Phase-4.6 Voice path is now closed on the Windows/NVIDIA host: generation is working with an existing supported local Qwen model after the no-redownload compatibility layer and polling/VRAM hotfix. Admin must therefore continue to distinguish **runtime ready** from **HF copy installed**. A healthy legacy runtime is not a broken model and does not require Repair.

For a legacy-ready Qwen model, **Install HF copy** remains optional and may use network data. Do not require that action for validation, upgrades, or normal generation. Existing valid Chatterbox HF-cache snapshots likewise remain reusable in place; fresh repository-snapshot acquisition is explicit rather than an automatic migration step.

## Phase 4.6.2 — Non-disruptive background model monitoring

Repository-snapshot jobs continue to update Admin model state in the background, but their 1.25-second monitor no longer calls a global Neo render while the user is working in Voice, Video, Image, Prompt/Caption, or another surface. Admin renders job progress only when Admin is the active surface, and poll-driven rendering is deferred while an editable Admin control has focus. Switching back to Admin renders the latest in-memory job state normally.

## Phase 4.6 — Voice Model Lifecycle Unification

Admin → Models now owns repository-snapshot acquisition for all currently executable managed Hugging Face Voice models: Qwen3-TTS 0.6B/1.7B CustomVoice plus Chatterbox Turbo/Multilingual V3. The existing Phase 4.5 cache resolver, disk preflight, background installer, authoritative installed probe and Installed/Sources UX are reused rather than copied into provider-specific downloaders.

Repository-snapshot manifests may optionally declare `install.allow_patterns` / `install.ignore_patterns`. When present, Neo passes those patterns directly to `huggingface_hub.snapshot_download()` while continuing to use the standard Hub cache and no `local_dir`. This supports provider-required materializations such as Chatterbox Multilingual V3 without downloading unrelated historical checkpoints from the same upstream repository. The authoritative content probe, not the pattern list or job history, decides whether the materialization is executable.

Voice acquisition policy is now: setup scripts install dependencies only; Admin installs/repairs weights; local probes decide installed truth; Voice runtime consumes the local path; Generate never downloads weights. Only model variants with an implemented Neo runtime loader receive actionable Admin install records.


## Phase 4.6.1 — Legacy Voice Model Compatibility / No-Redownload Migration

Admin now separates **runtime availability** from **Admin HF-copy state** for managed Voice repository snapshots. This prevents a working legacy Qwen installation from being mislabelled as a broken model merely because the new HF cache copy is absent or has a dangling ref.

For supported Voice records, the local-only status endpoint adds a `runtime` block. Qwen runtime truth follows complete legacy `Neo_Runtime/voice/models/qwen3_tts` first, then a verified HF snapshot. Chatterbox reuses any already-valid historical Hugging Face cache snapshot in place. No compatibility probe performs remote calls, migration, cache creation, or downloads.

The UI rules are:

- legacy Qwen ready + HF copy absent/broken → **Runtime installed · Legacy**; no repair is required;
- the optional transfer action becomes **Install HF copy**, not a mandatory Repair action;
- confirmation warns that the optional HF copy may use network data; cancelling leaves the working legacy runtime untouched;
- HF status remains visible separately (`Installed`, `Not installed`, `Incomplete`, `Different revision cached`, `Cache problem`, `Needs verification`);
- Generate continues to use only an already-verified local runtime source and never downloads weights.

This is a permanent compatibility contract, not a temporary migration grace period. Existing users may keep legacy Qwen snapshots indefinitely with no required re-download.

## Phase 4.5.6 — Admin Models Guide UX / Installed-State Integration

Admin → Models now consumes the Phase 4.5.5 authoritative Hugging Face snapshot probe as a **live user-facing install state**, instead of making users infer readiness from an old scan or a completed download job. Opening/refreshing the Model Guide performs a lightweight local-only repository-snapshot status check. It does **not** walk the configured ComfyUI/Forge/local-model folders and it does not persist a new installed index.

The Sources cards now use these user-facing states:

- **Installed** — the requested revision and required model content are verified in the HF cache.
- **Not installed** — the requested revision is absent.
- **Incomplete** — the requested revision resolves but required files are missing.
- **Different revision cached** — other snapshots exist, but not the manifest-requested revision.
- **Cache problem** — ref/snapshot materialization is corrupt or unreadable.
- **Needs verification** — Neo cannot run the declared content probe and therefore fails closed.

Install controls are state-aware. `Not installed` shows **Install**. `Incomplete`, `Different revision cached`, `Cache problem`, and `Needs verification` show a **Repair**-style action. `Installed` shows **Repair / reinstall** as a secondary action rather than pretending another download is required. A separate **Verify** action refreshes the local authoritative state without starting a transfer.

Repository-snapshot jobs are monitored while the browser session remains open and surface friendly stages: **Queued → Checking disk → Preparing → Downloading → Verifying**. When a job reaches a terminal state, Neo automatically refreshes the local authoritative snapshot status so a successful install becomes **Installed** without requiring a manual full-folder scan. Hugging Face transfer bytes remain indeterminate because `snapshot_download()` owns the transfer.

The Installed tab is now intentionally split into two concepts:

1. **Hugging Face repository snapshots** — fast live local-only status for manifest snapshot records.
2. **Local model scanner** — the broader persisted scan of configured ComfyUI, Forge, KoboldCPP, and local-model folders.

The live endpoint is:

```text
POST /api/admin/models/repository-snapshots/status
```

It is read-only and local-only. It does not call Hugging Face, create cache folders, mutate manifests, persist scan state, or use job history/receipts as installation authority.

For Qwen3-TTS CustomVoice, **Phase 4.5.7 binds an authoritative Admin Installed Hugging Face snapshot into the normal Voice runtime** after preserving any complete legacy Neo Runtime snapshot as first priority. **Phase 4.5.8 makes Admin → Models the normal user model-install path and removes direct Qwen download/test/worker BATs from the repository root.** Voice Generate still never downloads weights.

## Phase 4.5.8 — Qwen setup / BAT ownership

Normal Qwen users run `setup_qwen3_tts_backend.bat` once for the isolated worker environment, then install supported CustomVoice snapshots from **Admin → Models**. The setup script does not download weights and no longer creates the legacy model folder as an installation side effect.

Direct Qwen model downloader, direct worker launcher, 0.6B preflight, and 1.7B calibration BATs are developer-only under `scripts/dev/qwen3_tts/`. They must not be presented as normal Model Guide actions or release setup steps. Existing complete legacy snapshots remain runtime-compatible and keep precedence; this cleanup changes guidance/layout, not runtime source precedence.


---

## Phase 4.5.5 — Authoritative Hugging Face Snapshot / Installed-State Probe

Admin → Models now has a local, read-only installed-state authority for manifest-declared Hugging Face `repository_snapshot` models. A successful download job, an existing repository folder, or a Neo receipt is **not** proof that a model is installed. Neo now proves the manifest's **requested revision** against Hugging Face's own local cache structure and then runs the manifest-declared **content probe**.

The authority chain is:

```text
manifest requested revision
  ↓
resolved Hugging Face Hub cache
  ↓
repository refs/<revision> (or an explicit commit revision)
  ↓
snapshots/<resolved commit>
  ↓
intact snapshot materialization
  ↓
manifest install.probe_id content probe
  ↓
installed-state decision
```

For the current Qwen3-TTS CustomVoice records, `install.probe_id = qwen3_tts_model_snapshot`. The Admin probe reuses the shared Qwen snapshot-directory verifier, so an `installed` result requires the root config/tokenizer/preprocessor assets, root model weights, and the bundled `speech_tokenizer` config/preprocessor/weights required by Neo's Qwen runtime contract.

### Installed-state vocabulary

The repository snapshot probe reports one of these states:

- `not_installed` — the resolved HF cache/repository/requested revision is absent and there is no contradictory local state.
- `stale` — the repository has other cached snapshots, but the manifest-requested revision has no local ref/resolution. Neo does not guess that another cached commit is equivalent.
- `partial` — the requested revision resolves to a snapshot, but the snapshot is empty or its manifest-declared content probe reports required model content missing.
- `corrupt` — the cache/ref/snapshot structure is structurally invalid, a ref is unreadable/invalid/dangling, or snapshot materialization contains broken links/unreadable files.
- `unverified` — Neo cannot run a supported manifest content probe. This state fails closed and is never treated as installed.
- `installed` — the requested revision resolves locally, the materialized snapshot is intact, and the manifest-declared content probe passes.

The probe lives in `neo_app/admin/models/huggingface_snapshot_probe.py`. It is deliberately **offline and read-only**: it performs no remote Hugging Face request, creates no directories, writes no receipt, mutates no manifest, and never treats job history or a receipt as authority.

### Scanner and install-job integration

The Admin Installed scanner now routes `repository_snapshot` records through this probe instead of returning the old Phase 4.5.1 `not_scanned / repository_snapshot_probe_required` placeholder. Sources and Installed cards expose the requested revision, resolved commit, snapshot path, materialized-file diagnostics, content-probe state, and missing required paths when available. Summary counts distinguish installed, partial, stale, corrupt, unverified, and not-installed snapshot records.

Phase 4.5.3 snapshot jobs also changed their completion boundary. After `snapshot_download()` returns and its basic return-path sanity check passes, the job enters `verifying` and runs this authoritative local probe against the exact cache captured by the install request. A job reaches `completed` only when the probe returns `installed`; `partial`, `corrupt`, `stale`, `not_installed`, or `unverified` fail the job. Neo additionally fails if the authoritative requested-revision snapshot path disagrees with the path returned by the installer.

### Phase boundaries that remain locked

Phase 4.5.5 establishes **Admin installed-state truth**, and Phase 4.5.7 consumes that same authoritative probe for Qwen CustomVoice runtime selection. Complete Phase 3 legacy/local snapshots remain first priority; otherwise a verified Admin HF snapshot becomes the local worker model path. Non-installed/stale/partial/corrupt/unverified snapshots never become executable. Voice **Generate still never downloads weights**.

Phase 4.5.4 disk preflight also remains conservative for now: it still grants zero partial-cache reuse credit even though Phase 4.5.5 can classify local snapshot state. Safe download-byte credit would require a separate exact missing-blob/transfer-size policy; this phase does not infer it from installed-state labels.

---

## Phase 4.5.4 — Hugging Face snapshot disk-space preflight

Repository-snapshot installs now fail closed before any Hugging Face transfer when Neo cannot prove that the filesystem containing the resolved Hugging Face Hub cache has enough free space.

For every `repository_snapshot` record, `install.expected_size_mb` is now required safety metadata. Neo computes a conservative requirement using:

```text
required free space
= manifest expected_size_mb
+ max(1024 MB, 10% of expected_size_mb)
```

The preflight intentionally grants **zero cache-reuse credit** in Phase 4.5.4. Phase 4.5.5 can now classify authoritative local snapshot state, but it does not calculate exact missing-blob transfer bytes. Neo therefore keeps the conservative full-size requirement rather than converting installed-state labels into unsafe download-size credit.

The check targets the actual filesystem that owns the resolved Hugging Face cache. If the final cache directory does not exist yet, Neo walks upward to the nearest existing parent and measures that filesystem. It does not create the cache merely to inspect free space.

The check runs twice:

1. before a snapshot job is created; and
2. again in the worker immediately before `snapshot_download()` starts.

If either check fails or disk usage cannot be read, the snapshot transfer does not start. A job created after the first successful check may still fail at the second check if free space changed before transfer.

The job record stores the non-secret preflight report: expected bytes, reserve, required free bytes, observed free bytes, and filesystem check path. Tokens remain session-only and are not written to job history.

Phase 4.5.5 makes the Admin cache authoritative for repository-snapshot **installed-state classification**; Phase 4.5.7 now reuses that authority for Qwen CustomVoice runtime resolution. Generate still never downloads weights.

Neo Studio includes a model manifest foundation for the future Admin Model Guide.

Phase 8 adds a **safe single-file Download Manager** on top of the local manifest, path resolver, installed scanner, Hugging Face discovery, Civitai discovery, category normalization, advanced filtering, and download planning. Phase 4.5.3 adds a separate **Hugging Face repository-snapshot installer** for complete Transformers-style repositories. Neo can now create local jobs for both normal file downloads and complete Hugging Face snapshots while keeping the two install classes separate.

Remote source tags are treated as **hints only**. Neo's manifest and category map remain the source of truth for UI filtering, install meaning, backend routing, and download target planning. Downloads require explicit confirmation and tokens are never stored.

## API endpoints

```text
/api/admin/models/catalog
/api/admin/models/filter
/api/admin/models/download/plan
/api/admin/models/download/start
/api/admin/models/download/cancel
/api/admin/models/download/jobs
/api/admin/models/download/jobs/{job_id}
/api/admin/models/folder-rules
/api/admin/models/category-map
/api/admin/models/schema
/api/admin/models/paths
/api/admin/models/resolve-target
/api/admin/models/installed
/api/admin/models/scan-installed
/api/admin/models/repository-snapshots/status
/api/admin/models/remote/huggingface/metadata
/api/admin/models/remote/huggingface/discover-files
/api/admin/models/remote/civitai/metadata
/api/admin/models/remote/civitai/discover-files
```

## Phase 8 provides

- Curated model catalog manifest structure
- Folder routing rules for backend/model-type targets
- Creative category normalization rules
- Local user model path settings under `neo_data`
- Backend-aware target folder resolution
- Local installed model scanner
- Hugging Face metadata lookup and file discovery
- Civitai model/version metadata lookup
- Civitai version/file discovery
- Remote preview URL pass-through without saving images
- Manifest-guided file filtering
- Recommended variant marking from manifest UI rules
- Normalized filter fields per catalog record
- Advanced catalog filtering by domain, base model, model type, provider, backend, creative category, search text, recommended state, and dynamic-source state
- Download planning for selected catalog records and discovered file variants
- Provider-aware source references for Hugging Face and Civitai
- Backend-aware target folder and final path preview
- License/access warnings before any future download starts
- Confirmation metadata for the Download Manager
- Confirmed download job creation
- Background download worker support
- Download progress stored under `neo_data/downloads/download_jobs.json`
- Cancel support for active jobs
- Temp-file downloads before final install
- Final move into the resolved backend model folder after success
- Token redaction from persisted job state

Phase 8 does **not** store Civitai/Hugging Face tokens, persist remote metadata, persist remote previews, hash remote files, install model packs, or auto-download anything without confirmation.

## Repo-owned files

```text
neo_manifests/models/model_catalog.schema.json
neo_manifests/models/model_catalog.json
neo_manifests/models/folder_rules.json
neo_manifests/models/category_map.json
```

These files are safe to commit to GitHub because they contain only public model guide structure and curated metadata.

## Runtime/user-owned data

User-specific model path settings are stored locally under:

```text
neo_data/config/model_paths.json
```

Installed scan indexes are stored locally under:

```text
neo_data/cache/model_installed_index.json
```

Do not commit user model paths, API tokens, download jobs, installed model scans, partial downloads, or downloaded model files.

## Category normalization

Category normalization maps messy source tags into controlled Neo categories.

Example:

```text
Remote/model tags: anime character, OC, cinematic lighting, Q4_K_M
↓
Neo categories: anime, character, cinematic, lighting, gguf
```

The category map lives in:

```text
neo_manifests/models/category_map.json
```

Useful creative categories currently include:

```text
base
character
style
clothing
pose
expression
concept
object
vehicle
creature
environment
architecture
lighting
anime
realistic
cinematic
gguf
chat
assistant
roleplay
utility
mature
```

## Advanced filtering

Use:

```text
POST /api/admin/models/filter
```

Example request:

```json
{
  "filters": {
    "domain": "image",
    "base_model": "sdxl",
    "model_type": "lora",
    "creative_category": "character",
    "backend": "comfyui"
  }
}
```

Search and boolean filters are also supported:

```json
{
  "search": "roleplay gguf",
  "recommended": true,
  "dynamic_source": true
}
```

Supported filter fields:

```text
domain / category
base_model
model_type
technical_type
provider
source_mode
creative_category / creative_categories
backend / backend_target
recommended
dynamic_source
search
```

The catalog endpoint also returns `filter_options`, so the UI can build dropdowns from the active manifest instead of hardcoding filter values.

## Remote discovery normalization

Remote discovery variants now include a `normalized` block.

Example variant normalization:

```json
{
  "normalized": {
    "schema_id": "neo.admin.models.variant_normalization.v1",
    "domain": "image",
    "base_model": "sdxl",
    "model_type": "lora",
    "technical_type": "lora",
    "provider": "civitai",
    "creative_categories": ["character"],
    "recommended": false
  }
}
```

This lets the future Admin UI filter remote file variants without trusting raw Civitai or Hugging Face tags directly.

## Civitai metadata lookup

Use:

```text
POST /api/admin/models/remote/civitai/metadata
```

Example request by model:

```json
{
  "model_id": "12345"
}
```

Example request by version:

```json
{
  "version_id": "67890"
}
```

Or use a catalog record:

```json
{
  "catalog_id": "sdxl-lora-character-guide",
  "model_id": "12345"
}
```

Returned metadata is session-only. Neo does not save remote descriptions, tags, stats, creator data, or preview URLs by default.

## Civitai file discovery

Use:

```text
POST /api/admin/models/remote/civitai/discover-files
```

Example request:

```json
{
  "catalog_id": "sdxl-lora-character-guide",
  "model_id": "12345",
  "version_id": "67890"
}
```

Neo will:

1. Load the manifest record.
2. Read the record's `file_rules`.
3. Fetch the Civitai model or model version payload.
4. Read model versions and files.
5. Filter allowed file extensions and excluded patterns.
6. Normalize category and base-model metadata.
7. Return UI-ready variants/files with source metadata.

For SDXL LoRA records, the guide currently allows:

```text
.safetensors
.pt
```

and excludes common non-model assets such as:

```text
training
dataset
sample
```

## Remote previews

Civitai payloads may include preview image URLs. Neo passes these URLs through to the UI as remote references only.

```text
Remote preview files saved: no
Remote preview metadata persisted: no
```

If the UI later displays these previews, the browser loads them directly from the source site during that session.

## Hugging Face metadata/file discovery

Hugging Face discovery remains available:

```text
POST /api/admin/models/remote/huggingface/metadata
POST /api/admin/models/remote/huggingface/discover-files
```

This is mainly intended for model repos/folders such as GGUF collections where many variants live in the same repository.

## Privacy boundary

Phase 8 remote source and download behavior follows this policy:

```text
Remote metadata saved: no
Remote previews saved: no
Tokens saved: no
Downloads: only after explicit confirmation
```

If a token is passed for a gated/private source, it is used only for that request and is not stored by the model guide foundation.

## Folder resolver concept

Neo resolves:

```text
backend + model_type → local target folder
```

Examples:

```text
comfyui + lora       → ComfyUI/models/loras
comfyui + checkpoint → ComfyUI/models/checkpoints
comfyui + unet_gguf  → ComfyUI/models/unet
koboldcpp + llm_gguf → user-selected LLM model folder
```

Resolver endpoint:

```text
POST /api/admin/models/resolve-target
```

The resolver is read-only. It does not create folders, scan installed models, or download files.

## Installed scanner

Neo Studio can scan configured local model folders and compare detected files with the Model Guide manifest.

Available endpoints:

```text
GET  /api/admin/models/installed
POST /api/admin/models/scan-installed
```

The scanner can report:

- detected model files
- extension counts
- target folder counts
- manifest records with exact filename matches
- manifest records with local candidates
- missing path / missing folder warnings

Records without an exact filename, such as manual guide placeholders or dynamic Hugging Face/Civitai sources, are not treated as fully installed yet. They may show local candidates until source-discovery provides exact file identities.

## Current boundaries

Remote preview rendering in the actual Admin UI, download hashing, retry UI, and model-pack installation are planned for later phases.


## Download planning

Use:

```text
POST /api/admin/models/download/plan
```

Phase 7 creates a safe preview of a future download. It does not transfer files.

Example Hugging Face GGUF plan:

```json
{
  "catalog_id": "flux-gguf-unet-source-guide",
  "backend": "comfyui",
  "source": {
    "provider": "huggingface",
    "repo": "example/flux-gguf",
    "revision": "main"
  },
  "variant": {
    "provider": "huggingface",
    "path": "flux-dev-Q4_K_M.gguf",
    "filename": "flux-dev-Q4_K_M.gguf",
    "size_bytes": 123456789
  }
}
```

Example Civitai LoRA plan:

```json
{
  "catalog_id": "sdxl-lora-character-guide",
  "backend": "comfyui",
  "source": {
    "provider": "civitai",
    "model_id": "12345",
    "version_id": "67890"
  },
  "variant": {
    "provider": "civitai",
    "path": "character-lora.safetensors",
    "filename": "character-lora.safetensors",
    "metadata": {
      "download_url": "https://civitai.com/api/download/models/67890"
    }
  }
}
```

The response includes:

```text
source provider/repo/version/download reference
selected filename and size
backend target type
resolved model folder
final path preview
allowed extension validation
license/access warnings
confirmation metadata
```

The planner rejects or warns about unsafe states such as manual-only records, missing discovered variants, unresolved backend paths, and file extensions that do not match the target model type.


## Download manager

Use these endpoints:

```text
POST /api/admin/models/download/start
POST /api/admin/models/download/cancel
GET  /api/admin/models/download/jobs
GET  /api/admin/models/download/jobs/{job_id}
```

The Download Manager starts from a successful Phase 7 download plan. A request must include `confirmed: true`; otherwise Neo refuses to start the transfer.

Example dry-run start request:

```json
{
  "confirmed": true,
  "dry_run": true,
  "plan": {
    "ok": true,
    "source": {
      "provider": "huggingface",
      "download_url": "https://huggingface.co/example/repo/resolve/main/model.gguf"
    },
    "file": {
      "filename": "model.gguf",
      "extension": ".gguf",
      "size_bytes": 123456789
    },
    "target": {
      "backend": "comfyui",
      "target_type": "unet_gguf",
      "folder_path": "<ComfyUI-models-root>/unet",
      "final_path": "<ComfyUI-models-root>/unet/model.gguf"
    }
  }
}
```

For real downloads, omit `dry_run` or set it to `false`. Neo will:

1. Validate the plan.
2. Require confirmation.
3. Create a local job under `neo_data/downloads/download_jobs.json`.
4. Download into `neo_data/downloads/tmp` using a `.part` file.
5. Move the completed file into the resolved target folder.
6. Mark the job as `completed`, `failed`, `cancelled`, or `blocked`.

Tokens can be passed for a single request with `token`, but they are used in memory only and are not persisted in the job store.

Download job state is local runtime data only:

```text
neo_data/downloads/download_jobs.json
```

The job store may include source URLs, file names, status, progress, and target paths. It must not include API tokens or Authorization headers.


## Phase 9 — Model Packs

Neo Model Guide now supports public recommended model packs.

Model packs group related catalog records into workflow-ready sets, such as:

- Flux ComfyUI GGUF starter workflows
- SDXL ComfyUI foundation workflows
- Roleplay LLM GGUF starter workflows

Pack data lives in the public repo manifest:

```text
neo_manifests/models/recommended_packs.json
```

Available pack APIs:

```text
GET  /api/admin/models/packs
POST /api/admin/models/packs/status
POST /api/admin/models/packs/download/plan
```

Pack status can read the local installed scan index from:

```text
neo_data/cache/model_installed_index.json
```

Pack download planning composes the existing Phase 7 download plans for each pack item. It does **not** start downloads by itself. Actual file transfers still require explicit Phase 8 download confirmation per plan.


Privacy rules:

- Pack manifests are public repo metadata.
- User paths remain in `neo_data/config/model_paths.json`.
- Installed scans remain in `neo_data/cache/model_installed_index.json`.
- Download jobs remain in `neo_data/downloads/download_jobs.json`.
- Pack planning does not save tokens, remote metadata, or preview images.

## Phase 10 — Workspace Integration

Neo Model Guide now exposes workspace requirement mappings so Image, Roleplay, Assistant, and future workspaces can ask which model packs or catalog records are needed for a workflow.

Workspace requirement data lives in the public repo manifest:

```text
neo_manifests/models/workspace_requirements.json
```

Available workspace APIs:

```text
GET  /api/admin/models/workspaces
POST /api/admin/models/workspaces/status
POST /api/admin/models/workspaces/download/plan
```

The workspace manifest connects a workspace/workflow to:

- surface id, such as `image`, `roleplay`, or `assistant`
- backend id, such as `comfyui`, `koboldcpp`, or `local_llm`
- base model family, such as `sdxl`, `flux`, or `general`
- recommended model packs
- required catalog records
- optional catalog records
- guide filters for opening Admin → Models in the right area

Example workspace status request:

```json
{
  "workspace_id": "image.sdxl.comfyui.foundation",
  "scan": {
    "catalog_status": [
      {
        "catalog_id": "sdxl-base-checkpoint-foundation",
        "overall_status": "installed"
      }
    ]
  }
}
```

The status response can report:

```text
ready
missing_required
needs_variant_selection
not_scanned
workspace_not_found
```

Workspace integration does not download files by itself. It only explains what a workspace needs and points the UI toward Admin → Models, pack status, installed scanning, or download planning.

Privacy rules:

- Workspace requirement manifests are public repo metadata.
- Status checks can read an explicit scan payload or `neo_data/cache/model_installed_index.json`.
- Workspace checks do not call Hugging Face or Civitai.
- Workspace checks do not save remote metadata, preview images, tokens, or user paths.
- Workspace download planning composes existing pack download plans only and does not start download jobs.

## Seed Manifest Test Entries

The public model catalog now includes two curated seed entries for testing live source discovery:

- `sdxl-checkpoint-heirloom-male-xl-civitai` — Civitai SDXL checkpoint/merge source using model id `2284365`.
- `qwen-image-edit-rapid-aio-hf` — Hugging Face Qwen Image Edit Rapid AIO source using repo `Phr00t/Qwen-Image-Edit-Rapid-AIO`.

These entries live in:

```text
neo_manifests/models/model_catalog.json
```

That file is repo-owned and should be committed to GitHub. Runtime/user-specific data still belongs in `neo_data`, including:

- local model paths
- installed scan results
- download jobs
- tokens/API keys
- optional runtime cache files

The seed entries are intended for testing:

- Civitai metadata discovery
- Hugging Face metadata discovery
- file/variant discovery
- target folder planning
- category filtering
- download planning

They do not store remote preview images or remote descriptions permanently. Remote metadata should remain session-only unless a future optional cache setting is added.

## Phase 10.1 — Admin Model Guide UI

The Model Guide is now exposed as a visible Admin subtab:

```text
Admin → Models
```

This UI is the first frontend layer for the model manifest system. It uses the existing Phase 1–10 backend contracts and does not introduce a new model storage system.

Available child tabs:

- **Guide** — browse and filter the public model catalog.
- **Sources** — run session-only Hugging Face/Civitai file discovery for manifest records.
- **Installed** — run local installed-model scans using configured model paths.
- **Paths** — set ComfyUI, Forge, KoboldCPP, local LLM, embedding, reranker, and download temp folders.
- **Packs** — review recommended model packs.
- **Workspace Needs** — review workspace model requirements.
- **Downloads** — review planned downloads and local download job state.
- **Raw** — inspect raw payloads in Expert detail mode.

Important boundaries:

- Public catalog data is loaded from `neo_manifests/models`.
- User paths are saved under `neo_data/config/model_paths.json`.
- Installed scan results are saved under `neo_data/cache/model_installed_index.json`.
- Download jobs are saved under `neo_data/downloads/download_jobs.json`.
- Remote descriptions, previews, tags, and variants remain session-only unless a future optional cache setting is added.
- The UI does not silently download models. Download planning and dry-run jobs require explicit user action.

This phase makes the Model Guide accessible from the Admin navigation, but it still respects the manifest-first design: GitHub stores the model catalog and Neo stores user runtime state locally.


---

## Phase 10.2 — Source Dropdown, Remote Details, and Download Controls

The Admin Models UI now avoids listing every discovered file as a full card.

Recommended flow:

1. Open **Admin → Models**.
2. Filter by **Domain**, **Base**, **Type**, or **Provider**.
3. Use **Source / file** to pick the focused manifest entry.
4. Open **Sources**.
5. Click **Load details / previews** to fetch remote description, tags, stats, and preview URLs for the current session.
6. Click **Discover files**.
7. Use **Available file / variant** to select one file from the discovered variants.
8. Click **Plan selected file**.
9. Review the target path.
10. Click **Download planned file** only after the plan looks correct.

Remote preview images and remote metadata are loaded from the source website for the current session only. They are not saved into the repo and are not persisted as model metadata by default.

The UI intentionally shows one selected discovered file at a time so large Hugging Face/Civitai source folders do not cram the screen with many repeated model cards.

---

## Phase 10.3 — Cascading Filters, Actionable Sources, and Download Progress

The Admin Models UI now treats the model catalog as a structured browser instead of a flat list.

Filter behavior:

1. **Domain** controls the available base-model options.
2. **Base** controls the available model-type options.
3. **Type** controls whether the LoRA category filter is available.
4. **LoRA category** is only enabled when **Type = LoRA**.
5. **Source / file** only shows concrete, actionable Hugging Face or Civitai source entries.

Manifest guide/template entries are intentionally hidden from the actionable source dropdown. They can still exist in the manifest for planning, packs, or workspace contracts, but they should not appear as downloadable models unless they define a real source such as a Hugging Face repo, Civitai model ID, version ID, file, or source URL.

The Domain filter includes future-facing domains such as:

```text
Image
Video
Voice
LLM
Utility
```

A domain may appear even before curated model records exist for that domain. In that case, the Base and Type filters will show an empty-state option instead of mixing unrelated models from another domain.

Download jobs now expose progress details when available:

- percent complete
- downloaded bytes / total bytes
- download speed
- estimated time remaining
- elapsed time
- cancel button for queued/downloading jobs

Download progress is stored only in local runtime state under:

```text
neo_data/downloads/download_jobs.json
```

No remote preview images, remote descriptions, remote tags, or source metadata are permanently saved by this UI flow.

---

## Phase 10.4 — Installed Tab Local Files First

The **Installed** tab now prioritizes actual local model files found in configured model folders.

Expected behavior:

1. Open **Admin → Models → Paths**.
2. Configure the relevant model roots, such as ComfyUI `models`, Forge `models`, or a local LLM models folder.
3. Open **Admin → Models → Installed**.
4. Click **Scan installed models**.
5. The tab shows local files first, grouped with backend, model type, extension, size, relative path, and target folder.

Manifest comparison is now secondary. It appears under a collapsed **Manifest comparison** section and hides guide/template records so planning entries do not look like real installed models.

This means the Installed tab is for answering:

```text
What model files do I actually have in my configured folders?
```

The manifest comparison is for answering:

```text
Which manifest records appear installed, missing, or have local candidates?
```

If no local files appear after scanning, check:

- **Admin → Models → Paths** has the correct model root folder.
- The backend path points to the actual folder containing model files.
- The files use allowed model extensions for their target type.
- The configured folder is accessible by Neo Studio.

Installed scan results remain local-only under:

```text
neo_data/cache/model_installed_index.json
```

---

## Phase 10.5 — Sources-Only Model Browser

The Admin Models UI no longer has a separate **Guide** child tab.

The previous Guide and Sources tabs overlapped, so the UI now keeps one primary browsing surface:

```text
Admin → Models → Sources
```

Use **Sources** for the full model browser flow:

1. Filter by **Domain**.
2. Pick the available **Base** for that domain.
3. Pick the available **Type** for that base.
4. If the type is **LoRA**, optionally use **LoRA category**.
5. Pick a concrete **Source / file** entry.
6. Click **Load details / previews** for session-only remote metadata.
7. Click **Discover files** to populate available variants.
8. Select one discovered file from the variant dropdown.
9. Click **Plan selected file** before downloading.

The **Source / file** dropdown only shows concrete Hugging Face/Civitai records that can be acted on. Manifest guide/template records remain hidden from this dropdown because they are planning records, not real downloadable sources.

Each source entry now owns its external link through an **Open source** button. This keeps the source URL close to the actual source card instead of showing it in a separate duplicated Guide list.

Remote details and previews still follow the same privacy rule:

- Loaded from the source website for the current session only.
- Not saved into the repository.
- Not persisted into `neo_data` by default.
- Preview images are displayed from remote URLs and are not downloaded by Neo.

## Curated base manifest notes

The curated manifest includes user-preferred sources for SDXL, Qwen Image Edit, Flux, Z-Image, Wan 2.2, LTX 2.3, ControlNet, IPAdapter, CLIP Vision, pose/detection assets, and local LLM GGUF sources.

Important support notes:

- **GGUF image/video models** are handled as selectable variants from Hugging Face folder discovery. They are routed to ComfyUI model folders such as `models/unet` or `models/diffusion_models` depending on target type.
- **Vision-capable LLM GGUF repos** are split into two manifest entries where needed: one entry filters real `.gguf` model files, and one entry filters `mmproj` / projector files for vision support.
- **HF Transformers / safetensors repositories** require whole-repository installation rather than a selected shard. Phase 4.5.1 adds the generic `repository_snapshot` manifest contract and starts with Qwen3-TTS CustomVoice. Existing LLM Transformers records remain `manual_only` until they are explicitly migrated and the snapshot installer is enabled for them.
- **Pose/detection models** such as ONNX ViTPose/YOLO assets are routed as utility/detection models, not regular ControlNet weights.
- **Wan 2.2 HighNoise/LowNoise GGUF sources** are separate entries and should be installed as matched pairs for the relevant T2V/I2V workflow.


---

## Phase 4.5.1 — Repository Snapshot Manifest Contract

Admin Model Guide now understands a second install class alongside normal single-file model downloads:

```text
source_mode = repository_snapshot
source.provider = huggingface
install.strategy = huggingface_snapshot
install.target_type = hf_cache
```

This contract is for models that are only valid when the complete Hugging Face repository is present, including configuration, tokenizer assets, nested components, and all required weight shards. A repository snapshot must never be reduced to one `.safetensors` file.

The first registered snapshot records are:

- `qwen3_tts_06b_custom_voice` → `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`
- `qwen3_tts_17b_custom_voice` → `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`

Their Admin catalog ids intentionally match the Qwen Voice Engine model ids. This keeps the later Admin-install/runtime binding direct and avoids a second alias table. Base clone and VoiceDesign variants are not exposed in Admin Models yet because their normal Voice surface activation is still gated.

### Contract boundary

Phase 4.5.1 is declarative only. It does **not** make the existing single-file download manager install repository snapshots. The current planner fails closed with `snapshot_installer_required`, and the Sources UI does not show **Discover files** or **Plan default** for snapshot records. Remote model details and **Open source** remain available.

The ordinary Installed file scanner continues to exclude repository snapshots from backend-folder scanning. **Historical Phase 4.5.1 behavior** returned `not_scanned / repository_snapshot_probe_required`; Phase 4.5.5 supersedes that placeholder with the authoritative local Hugging Face requested-revision + content probe.

Machine-specific Hugging Face cache state must never be written into `model_catalog.json`. The manifest declares what Neo knows how to install; later runtime probes declare whether the current machine actually has it.

Phase 4.5.2 now supplies the machine-local Hugging Face cache resolver described below.


---


## Phase 4.5.2 — Hugging Face Cache Resolver

Admin Model Guide now resolves the effective Hugging Face Hub cache as **read-only runtime state**. The resolver does not create directories, does not modify `model_catalog.json`, and does not persist the resolved cache path into `neo_data/config/model_paths.json`.

Resolution follows the Hugging Face Hub environment contract in this order:

```text
HF_HUB_CACHE
  ↓
HUGGINGFACE_HUB_CACHE   (legacy compatibility)
  ↓
HF_HOME / hub
  ↓
XDG_CACHE_HOME / huggingface / hub
  ↓
~/.cache/huggingface/hub
```

`HUGGINGFACE_HUB_CACHE` remains recognized because current `huggingface_hub` keeps it as a legacy compatibility variable. Neo reports when that legacy variable is the active authority instead of silently treating it as `HF_HOME`.

The resolver lives in `neo_app/admin/models/huggingface_cache.py`. `Admin → Models → Paths` exposes the resulting Hub cache, HF home, authority source, current existence state, and optional `huggingface_hub` library diagnostics in a read-only card. Saving Admin model paths cannot override or persist this Hugging Face cache result.

This phase still does **not** install repository snapshots. `repository_snapshot_install` remains false, and the existing `snapshot_installer_required` fail-closed behavior is unchanged.

Phase 4.5.3 now consumes this resolver through the dedicated repository-snapshot installer described below.

---

## Phase 4.5.3 — Hugging Face Snapshot Installer

Admin → Models can now execute complete Hugging Face repository-snapshot installs for manifest records that declare:

```text
source_mode = repository_snapshot
source.provider = huggingface
install.strategy = huggingface_snapshot
install.target_type = hf_cache
```

The installer lives in `neo_app/admin/models/huggingface_snapshot_installer.py` and calls `huggingface_hub.snapshot_download()` with the repository id, revision, and the Phase 4.5.2 resolved Hub cache. It deliberately does **not** pass `local_dir`. Hugging Face therefore owns its normal `blobs / refs / snapshots` cache layout instead of Neo constructing or mirroring that layout itself.

The main Neo Studio environment now includes `huggingface-hub>=0.36,<2` in `requirements.txt` because Admin → Models owns this generic installer. Existing installations that predate Phase 4.5.3 must refresh the main Neo environment (for example by rerunning `setup_neo_studio_venv.bat`) before the Install action can execute. The isolated Qwen worker environment is not used as the generic Admin installer runtime.

### Admin install flow

Repository snapshots do not use the single-file planner. The Sources card exposes an **Install** action that posts directly to the existing local download-job endpoint with `snapshot_install=true` and the catalog id. Neo validates the manifest record, resolves the Hugging Face cache, verifies that `huggingface_hub` is available, requires explicit confirmation, and then starts a background snapshot job.

```text
Admin → Models → Sources
  ↓
Qwen3-TTS repository snapshot
  ↓
Install
  ↓
validate manifest contract
  ↓
resolve HF cache
  ↓
huggingface_hub.snapshot_download()
  ↓
HF-managed cache snapshot
  ↓
installer return-path sanity check
  ↓
completed job
```

Snapshot jobs share Neo's existing local download-job history under `neo_data/downloads/download_jobs.json`, but they are marked with:

```text
job_kind = repository_snapshot
installer = huggingface_snapshot
```

Typical states are:

```text
queued → preparing → downloading → verifying → completed
                                      ↘ failed
queued/downloading → cancelling → cancelled
```

Phase 4.5.3 progress is intentionally **indeterminate** while `snapshot_download()` owns the transfer. Neo reports the current stage, repository, revision, cache path, elapsed time, and final returned snapshot path. It does not claim precise byte progress yet.

Cancellation is **cooperative**. A cancel request is recorded immediately, but Hugging Face may finish the transfer that is already in progress before the worker can observe cancellation. Neo does not delete Hugging Face cache content simply because a job was cancelled after transfer began.

### Security and state boundaries

- Installation requires explicit user confirmation.
- Session tokens may be passed to `snapshot_download()` but are not persisted in Neo's job store.
- The public `model_catalog.json` remains declarative and is never mutated by installation.
- The machine-specific cache path remains runtime state from Phase 4.5.2.
- The installer does not use `local_dir` and does not manually create Hugging Face `blobs`, `refs`, or `snapshots` structures.
- Normal single-file Hugging Face/Civitai downloads continue through the existing file planner/worker.
- The single-file planner continues to return `snapshot_installer_required` for repository-snapshot records.

### Deliberate next-phase boundaries

Phase 4.5.3 established the execution path. The responsibilities that were intentionally deferred there are now implemented separately:

- **Phase 4.5.4 — Disk-space preflight** estimates the conservative storage requirement and blocks insufficient disks before transfer.
- **Phase 4.5.5 — Installed snapshot probe** validates the requested local revision, snapshot materialization, and required model assets. The Phase 4.5.3 `snapshot_download()` return-path check remains only an installer sanity check and is **not** the authoritative installed-state decision.

Qwen CustomVoice runtime resolution is active in Phase 4.5.7; legacy snapshots remain first priority and verified HF cache snapshots are the fallback. "Generate never downloads" remains locked.

---

## 2026-08-01 — One shared Comfy model-path authority for Forge Neo

When ComfyUI and Forge Neo use the same centralized model library, do not create a second model tree just for Forge. Use **Admin → Models → Paths** as follows:

1. Point **ComfyUI models root** at the centralized Comfy-style `models` directory.
2. Set **Shared extra_model_paths.yaml** to the YAML that describes any external model folders.
3. Keep the Forge-specific root fields available for Forge's own normal installation/inventory needs; they are not a duplicate shared-YAML authority.
4. Launch Forge Neo with `--forge-ref-comfy-yaml <same YAML path>`. Neo detects whether the running Forge process references the same YAML but does not modify launch files.
5. For Forge ADetailer only, register the shared detector directories—or a parent directory that recursively contains them—in ADetailer's native `ad_extra_models_dir` and restart Forge after changes.

The shared YAML can feed any Forge Neo model category supported by Forge's native Comfy-reference loader. Neo extension adapters still use live backend capability catalogs before allowing execution. In particular, IP-Adapter remains governed by Forge Integrated ControlNet's live model/preprocessor catalog, while ADetailer shared suggestions are `.pt` basenames and are checked against its native extra-model-directory registration.

Absolute local paths remain in ignored `neo_data/config/model_paths.json` and server-side provider resolution only. Public guides, capability snapshots, extension state, and generation payloads must not contain contributor-specific paths.
