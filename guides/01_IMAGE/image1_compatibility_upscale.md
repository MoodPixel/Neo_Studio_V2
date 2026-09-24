# Image-1 — Compatibility foundation and upscale quick wins

## Apply this overlay

Close Neo, back up the affected files, then copy the ZIP contents into the Neo
repository root. Merge the folders and replace matching files. Restart Neo and
hard-refresh the browser. This is a direct file overlay: no patch command, database
migration, model download, or user-settings reset is required.

The overlay is based on the Neo snapshot used in this conversation, including
the preceding Image results and LoRA phases. It contains only the files changed
or added for Image-1. Model weights, test dependencies, caches, and neo_data are
excluded. Keep a backup if your local source has newer edits to these files.

## User-requested output size remains authoritative

The model name and native scale never select the user's final output size.
A native 4× model with a requested 2× scale still produces a 2× result. "4K" in a
filename is not interpreted as an instruction to output 4K. Scale factors and
pixel dimensions remain distinct controls.

For the conventional Comfy model route, Neo measures each uploaded source,
runs the chosen upscale model, and resizes to `round(source_dimension * scale)`
with no crop. This also handles renamed/custom models without depending on a
filename hint. Legacy graph-building calls without source dimensions retain
the existing relative-resize fallback, with curated native scale taking
precedence over the legacy filename heuristic. Unknown models in such legacy
calls still depend on that heuristic; the normal queue supplies measured sizes.

Interpolation-only behavior, Forge's scale/exact-dimension controls, and
SeedVR2's existing resolution/alpha contract remain intact. Image-1 does not
extend conventional upscaling's transparency behavior.

## What changed

- Optional `artifact` contracts extend `neo.models.catalog.v1` without changing
  existing IDs or requiring metadata on legacy entries. They distinguish model
  family, architecture, exact variant, file format, precision, task, runtime,
  provider/platform declarations, input/output behavior, native scale, sizing
  constraints, tiling ownership, and provenance. Unconfirmed precision is
  explicitly `unspecified`; platform validation is `not_tested`.
- Four optional sources are available in **Admin → Models → Sources**:
  ClearReality V1 Normal/Soft safetensors, Real-ESRGAN x4plus PTH, and
  Real-ESRGAN General x4v3 PTH. Plan a download, inspect its configured target,
  and use the existing confirmation flow. Files target Comfy's
  `models/upscale_models` directory. No weights are bundled or auto-installed.
- ClearReality sources use a full upstream commit and published SHA256 hashes.
  Real-ESRGAN uses upstream versioned release URLs; no expected upstream hash
  has been recorded for these two entries. A computed download digest is not
  represented as upstream verification.
- The single-file downloader checks supplied SHA256 and transfer length before
  publishing the file. A mismatched or truncated download fails before replacing
  an existing model. Successful jobs retain checksum evidence. Legacy sources
  without expected hashes remain supported and explicitly unverified.
- Conventional Image Upscale discovery uses the selected Comfy server's
  `/models/upscale_models`, with its live UpscaleModelLoader choices as fallback.
  Curated-but-absent models are not injected as installed dropdown choices.
  Existing manually selected names remain usable. Late responses from a previous
  profile are discarded and never change the user's requested scale.
- The model API separately reports backend file presence, required node/input
  availability, and inference verification. A filename match is metadata only,
  not proof that the local bytes equal the curated artifact. No GPU/device or
  successful inference is inferred from discovery.
- Requested output dimensions and native-scale provenance are included in the
  compiled job metadata; the queue response reports the conventional target.

The General x4v3 entry uses one weight through the existing loader. It does not
add upstream DNI/denoise-strength blending. Loader architecture support remains
dependent on the installed Comfy/Spandrel versions. No ONNX runner is added in
Image-1; that belongs to the subsequent utility-runtime phase.

## Verification

New coverage: **50 Python tests and 6 JavaScript tests**. It covers native 4× →
requested 2×, scales 0.25–8×, odd source dimensions, renamed models, per-image
dimension probing, Forge exact 2048×1152, HTTP queue graph/context handoff,
optional-schema migration, source pinning, format identity, missing node inputs,
profile switching, active Sources-tab download actions, and download integrity.

The broader catalog/upscale selection finishes with **211 passed, 3 failed**.
All three failures reproduce on the untouched baseline:

1. Two tests in `test_admin_model_guide_seed_manifest_entries.py` expect the old
   `sdxl-checkpoint-heirloom-male-xl-civitai` ID, which the baseline no longer has.
2. `test_pass_ab_task_profile_uses_live_probe_while_ui_listing_stays_passive`
   imports the provider registry and fails because Torch is unavailable in this
   test environment. The older Forge suite also cannot collect through that
   registry; its exact-size compilation path is covered directly by the new test.

JavaScript syntax checking passes. These are deterministic/API-contract tests
with simulated backend responses. They do not establish successful Comfy GPU
inference, visual quality, desktop browser behavior, or Windows/macOS support.
No real model download/inference was performed. The catalog's execution state
therefore remains `not_tested`; this phase does not persist per-device inference
certification after generation.

Run from the repository root in an environment with pytest, FastAPI,
python-multipart, httpx, Pillow, jsonschema and huggingface-hub installed:

```sh
python -m pytest -q tests/test_image1_compatibility_upscale.py
node --check neo_app/static/js/neo.js
node --test tests/image1_upscale_ui.test.cjs
```

Before advertising a model as tested, load the exact artifact on the chosen
backend, run representative images at native and non-native requested sizes,
check output dimensions and quality, and record the model digest plus runtime
and device versions. The existing source/result lineage should be checked with
"Use as Source" on the saved output.

## Upstream references checked

- [ClearReality model card](https://huggingface.co/Kim2091/ClearRealityV1)
- [Normal artifact and SHA256](https://huggingface.co/Kim2091/ClearRealityV1/blob/main/4x-ClearRealityV1.safetensors)
- [Soft artifact and SHA256](https://huggingface.co/Kim2091/ClearRealityV1/blob/main/4x-ClearRealityV1_Soft.safetensors)
- [Real-ESRGAN inference definitions and release URLs](https://github.com/xinntao/Real-ESRGAN/blob/master/inference_realesrgan.py)
- [Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN/releases)
- [Real-ESRGAN license](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE)

## Changed/new files

```text
guides/01_IMAGE/image1_compatibility_upscale.md
neo_app/admin/models/artifact_compatibility.py
neo_app/admin/models/download_manager.py
neo_app/admin/models/download_planner.py
neo_app/admin/models/manifest_schema.py
neo_app/admin/models/model_catalog_service.py
neo_app/static/js/neo.js
neo_extensions/built_in/image.image_upscale/backend/api_routes.py
neo_extensions/built_in/image.image_upscale/backend/workflow.py
neo_manifests/models/model_catalog.json
neo_manifests/models/model_catalog.schema.json
tests/image1_upscale_ui.test.cjs
tests/test_image1_compatibility_upscale.py
```
