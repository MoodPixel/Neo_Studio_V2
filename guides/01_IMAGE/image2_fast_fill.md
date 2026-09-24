# Image-2 — Fast Fill / Object Removal

Fast Fill is available directly in **Image → Finish**, independently of the selected generation backend. It uses optional local ONNX Runtime and a pinned LaMa model. Existing **Remove Background → Mask & Object Utilities → Object Remover · Lama** continues to use ComfyUI unchanged.

## Install

Overlay this ZIP on the Neo tree **after Image-1**, preserving its relative paths, then restart Neo. It contains changed/new source, tests, and this guide only. It includes no weights, Python packages, or user configuration. No database migration is needed.

In the Python environment that runs Neo:

```bash
python -m pip install onnxruntime
python scripts/install_lama_fast_fill.py
```

Use the appropriate ONNX Runtime package for your platform. Do not install competing CPU/GPU/DirectML distributions into one environment. CUDA and DirectML are optional choices only when their execution providers are installed; this release was exercised on CPU. An advertised provider is not proof that it can execute this model. Loading and execution are checked on each run; an unsupported provider fails explicitly. Select CPU yourself if desired.

The installer explicitly downloads about 208 MB, checks the size and SHA-256, then moves the verified model into `neo_data/models/lama/lama_fp32.onnx`. It does not install packages. App startup and runtime refresh never download anything. If an existing file has the wrong identity, rename it before reinstalling; the installer will not replace it.

For a manually managed location, set `NEO_LAMA_MODEL_PATH` to the absolute path of the verified file before starting Neo. The installer respects this variable; `--destination` can also set its output path. For offline installation, transfer the exact artifact to this path. Click **Refresh runtime** after setup.

## Use

1. Select the exact saved image file in Results, or upload a source in Fast Fill. Uploads take precedence; **Use selected saved result** clears the staged upload and mask.
2. Click **Paint removal mask** to use Neo's existing brush editor, or upload a saved mask. White removes and black preserves. Paint the shadow/reflection too when those should disappear. Transparent white in an uploaded mask is treated as unselected.
3. Set feather, mask growth, tile overlap, and device. Start with CPU, zero growth/feather, and 128-pixel overlap.
4. Click **Remove object / Fill**. The filled image and effective mask become normal saved Image results. Select the filled image for subsequent work.

Masks are tied to the selected source. Changing the selected file requires repainting or uploading its matching mask. Uploaded masks must match the source dimensions exactly; Neo never stretches them. The effective mask saved with the output shows the editable region after growth and feathering. Feather expands outward without weakening the original white selection.

The original RGB values outside the effective mask and the complete original alpha channel are preserved exactly in the decoded output. Transparent sources remain transparent; this feature repairs RGB content, not missing alpha. Source orientation is normalized from EXIF before matching masks and processing.

## Runtime and limits

| Property | Contract |
| --- | --- |
| Artifact | `Carve/LaMa-ONNX`, `lama_fp32.onnx` |
| Revision | `c3c0c9e468934d62e79c329e35d82dd09ff8c444` |
| SHA-256 | `1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6` |
| Size / license | 208,044,816 bytes / Apache-2.0 |
| Inputs | `image`: float32 RGB NCHW in 0–1; `mask`: float32 N1HW in 0/1 |
| Shape | Model batch is symbolic `batch`; Neo always runs batch 1, spatial size 512×512 |
| Output | `output`: float32 RGB NCHW in pixel units 0–255 |
| Large sources | Overlapping 512×512 tiles; no whole-image resize; edge padding for small sources |
| Limits | 16,777,216 pixels; overlap 32–256; feather 0–32; growth 0–64 pixels |
| Concurrency | One native Fast Fill run at a time per Neo process; another run receives HTTP 409 |

The adapter blends overlapping predictions with edge weights, then explicitly composites only inside the effective mask. Empty masks and masks covering the entire image are rejected. Growth/feather must leave some background context. Objects covering most of a tile may produce weak results because LaMa cannot see beyond that tile; blending reduces seams but does not guarantee semantic consistency. There is no text prompt, diffusion sampling, or automatic backend fallback.

Inference runs in a worker thread so the API event loop stays responsive. The current run finishes even if the browser leaves the panel; there is no native cancellation/progress API in this phase. Runtime sessions are released after each run, so model loading contributes to latency.

## API and metadata

- `GET /api/extensions/background-removal/fast-fill/status`: package, provider, and model presence. `execution_verified` stays false on this passive check.
- `POST /api/extensions/background-removal/fast-fill/run`: multipart `mask_file`, `settings_json`, and exactly one of `image_file` or `source_json`.
- `settings_json`: `provider`, `overlap`, `feather`, `grow`.
- `source_json`: canonical Neo saved-output reference with `result_id` and `file_id`; resolved server-side through the existing source resolver.

Successful runs use Neo's existing native result persister. Output Inspector metadata retains source/mask assets, model revision and checksum, requested/actual provider, ONNX version, dimensions, tile count, and processing settings. Saved-source results retain immediate parent file identity, original root, and ancestor depth through the standard derived-action contract. Selecting a secondary output file does not silently use the record's primary file. Uploads have no saved parent result.

No new generation recipe or automatic replay executor is introduced. To repeat a fill, select the source again and reuse its saved effective mask or paint a new one. Removing the optional ONNX package or model only makes Fast Fill unavailable; existing Comfy LaMa and Image-1 upscale behavior remain intact.

## Verification

Validated with Python 3.12, ONNX Runtime 1.30.0 CPU:

- Real pinned model loaded and executed on a 641×385 RGBA fixture spanning two tiles, overlap 128 and feather 3.
- Output retained 641×385 dimensions; all alpha values and every pixel outside the effective mask were byte-exact. 2,881 pixels inside the mask changed. Run time was approximately 13 seconds in the validation environment, not a performance guarantee.
- 33 focused Python tests cover masks, tiled blending, exact preservation, bad contracts/output, runtime absence, source identity/lineage, API persistence and errors, and concurrency. API tests execute the real route and shared upload helper definitions without importing unrelated Comfy/torch engines.
- 9 JavaScript tests execute the actual Fast Fill functions and check source binding, stale mask rejection, request payloads, duplicate submission, errors, shared editor integration, and panel mounting.
- The previous Image-1 suite also passes: 50 Python and 6 JavaScript tests. JavaScript syntax validation passes.

```bash
python -m pytest -q tests/test_image2_fast_fill.py tests/test_image1_compatibility_upscale.py
node --test tests/image2_fast_fill_ui.test.cjs tests/image1_upscale_ui.test.cjs
node --check neo_app/static/js/neo.js
```

GPU inference and a complete running-Neo browser session were not exercised. Tests do not establish quality for every photo or mask. No model weights or validation images are included in this patch.

Model/export sources: [Carve model card](https://huggingface.co/Carve/LaMa-ONNX), [export inference example](https://huggingface.co/spaces/Carve/LaMa-Demo-ONNX/blob/main/app.py), [original LaMa](https://github.com/advimman/lama). The exact local model's tensor descriptors, checksum, and real CPU execution were checked in addition to these sources.
