# Neo Model Guide — Phase 8 Download Manager

Neo Studio includes a model manifest foundation for the future Admin Model Guide.

Phase 8 adds a **safe Download Manager** on top of the local manifest, path resolver, installed scanner, Hugging Face discovery, Civitai discovery, category normalization, advanced filtering, and download planning. Neo can now create local download jobs from a confirmed download plan, track progress, cancel active jobs, and move completed files into the resolved backend model folder.

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

## Shared Comfy model folders

When Comfy models live outside the active installation, configure them in Comfy's local `extra_model_paths.yaml`. Neo reuses the Comfy roots stored under **Admin → Extensions → Node Manager**; it does not need another personal YAML path field.

The YAML must explicitly register every folder family you expect Comfy or Neo to discover. A `base_path` alone does not make arbitrary child folders visible. Use the complete placeholder-only reference in [Comfy extra model paths](comfy_extra_model_paths.md), including core Comfy keys and optional Neo/custom-node keys such as `ipadapter`, `adetailer`, `sams`, `BiRefNet`, `facerestore_models`, and `SEEDVR2`.

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
- **HF Transformers / safetensors LLM repos** are manifest-only for now. They usually require downloading the whole repository snapshot, not one file. Neo's current download manager is single-file oriented, so automated snapshot install should be a later phase.
- **Pose/detection models** such as ONNX ViTPose/YOLO assets are routed as utility/detection models, not regular ControlNet weights.
- **Wan 2.2 HighNoise/LowNoise GGUF sources** are separate entries and should be installed as matched pairs for the relevant T2V/I2V workflow.
