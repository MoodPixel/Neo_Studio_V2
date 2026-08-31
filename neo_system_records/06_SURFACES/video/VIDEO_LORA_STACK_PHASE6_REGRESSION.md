# Video LoRA Stack — Phase 6 MiniMax H3 Regression Record

**Recorded:** 2026-08-31  
**Surface:** Video  
**Extension:** `video.lora_stack`  
**Phase:** 6 — MiniMax H3 LoRA Regression Gate  
**Phase-5 base:** `1520f2a7219726e6fb7e91a6c701e4ac00d442f1`  
**Phase-6 branch:** `phase-6-minimax-h3-lora-regression`  
**CI status:** **GREEN — 43/43 passed, 0 failed**

## Objective

Phase 6 does not onboard a new model family. Its purpose is to stress the MiniMax H3 reference implementation of the universal Video LoRA architecture before LTX runtime work begins.

The gate verifies:

```text
compiler owns patch location
extension owns requested rows
standard + speed share one LoRA engine
empty/disabled stack is a graph no-op
H3 uses LoraLoaderModelOnly only
manual selection is classifier-independent but catalog-valid
legacy Turbo cannot double-apply
H3 GGUF remains fail closed
```

## Regression harness

Added:

```text
neo_app/video/minimax_h3_lora_regression.py
```

Run from the repository root:

```bash
python -m neo_app.video.minimax_h3_lora_regression
```

The command returns JSON schema:

```text
neo.video.minimax_h3.lora_regression.v1
```

and exits non-zero if any case fails.

A normal unittest wrapper is also tracked at:

```text
tests/test_minimax_h3_lora_regression_phase6.py
```

## CI execution

Phase 6 is now executed by GitHub Actions through:

```text
.github/workflows/phase6_minimax_h3_lora_regression.yml
```

Draft validation PR:

```text
#2 — Phase 6: MiniMax H3 Video LoRA regression gate
```

The final authoritative CI run used Python 3.11 on Ubuntu with Neo import dependencies (`pydantic`, Pillow, CPU Torch), compiled the Phase-6 Python files, executed the regression module, then executed the unittest wrapper.

Authoritative result:

```json
{
  "ok": true,
  "gate": "pass",
  "case_count": 43,
  "passed": 43,
  "failed": 0,
  "next_phase_allowed": true
}
```

Both CI steps passed:

```text
python -m neo_app.video.minimax_h3_lora_regression       PASS
python -m unittest tests.test_minimax_h3_lora_regression_phase6 -v   PASS
```

This removes the earlier session limitation where the assistant container could inspect the repo but could not execute the full import graph locally.

## Deterministic backend contract

The regression harness uses synthetic ComfyUI `/object_info` rather than real model generation. This keeps the graph gate deterministic and independent from GPU/model availability while exercising the actual H3 compiler and Video LoRA integration code in the repository.

Synthetic catalogs include:

- FL2VA and Ref2VA H3 models;
- Qwen3-VL text encoder;
- H3 video and audio VAEs;
- `LoraLoaderModelOnly`;
- two ordinary LoRAs;
- `MiniMax-LightX2V-4steps.safetensors`;
- `hailuo_lightning_8steps.safetensors`.

The test request uses a fixed seed so graph-equivalence comparisons are not polluted by random noise-seed generation.

## Five-mode matrix

All core stack cases run against:

```text
txt2vid
img2vid
first_last_frame
reference_to_video
vid2vid
```

Per mode:

1. empty stack graph equivalence;
2. disabled populated stack graph equivalence;
3. one standard LoRA;
4. multiple standard LoRAs;
5. speed/Turbo LoRA;
6. mixed standard + speed ordering.

This contributes 30 cases.

## Additional gate cases

Classifier:

- MiniMax LightX2V recognized as speed;
- Hailuo Lightning recognized as speed;
- normal manually selected LoRA remains non-speed and allowed.

Legacy Img2Vid Turbo:

- legacy Turbo only -> one universal speed row;
- universal duplicate -> synthetic legacy row suppressed;
- duplicate saved as standard -> row promoted to speed;
- duplicate H3 target normalized to `all`;
- universal row strength preserved during migration;
- legacy Turbo auto-discovery selects a live speed candidate.

Fail-closed:

- selected LoRA missing from live catalog;
- empty ModelOnly catalog;
- generic `LoraLoader` without `LoraLoaderModelOnly`;
- H3 GGUF LoRA/Turbo;
- H3 `high`/`low` target;
- legacy Turbo naming a missing file.

Total deterministic matrix: **43 cases**.

## Defect found #1 — duplicate Turbo role drift

### Phase-5 behavior

If the universal stack already contained the same file selected by legacy Turbo, Phase 5 correctly suppressed a duplicate node. However, if that existing row was stored as `role=standard`, runtime Turbo/speed metadata could become false even though the model was not patched twice.

### Phase-6 fix

`merge_h3_legacy_turbo()` now treats the existing universal row as uid/strength truth while applying legacy semantic migration:

```text
same filename
  -> no second node
  -> preserve universal strength
  -> role = speed
  -> target = all
  -> duplicate_suppressed = true
```

Diagnostics include:

```text
existing_role_promoted
existing_target_normalized
```

The CI gate verifies this exact case on Img2Vid.

## Defect found #2 — missing LoRA deferred too late

### Phase-5 behavior

A manually selected LoRA absent from the live `LoraLoaderModelOnly` catalog produced a warning but could continue into the compiled graph. ComfyUI would reject the invalid combo value later.

### Phase-6 fix

Active H3 rows require the filename to exist in the live ModelOnly catalog before graph mutation.

Fail-closed errors cover:

```text
missing selected file
empty ModelOnly catalog
ModelOnly loader absent
```

This does **not** reintroduce filename-classifier gating. A normal custom LoRA is valid regardless of naming convention when it appears in the live catalog.

## Compiler/profile assertions

For every active UNET stack the CI gate checks:

```text
schema_version = neo.video.lora_patch_profile.v1
owner = compiler
loader_type = model_only
loader_node_class = LoraLoaderModelOnly
targets = [all]
validated = true
```

The gate also verifies:

- `prompt_api_payload.prompt` equals the patched graph;
- expected LoRA node order;
- expected runtime role order;
- `MiniMaxH3SigmaShift.model` consumes the final LoRA reference;
- workflow/profile/runtime metadata are JSON serializable;
- live catalog validation occurred.

## No-op invariant

For every H3 mode the CI gate proved:

```text
empty stack workflow == original H3 workflow
```

and:

```text
disabled populated stack workflow == original H3 workflow
```

Only metadata outside the workflow may differ.

## Validation boundary

The green Phase-6 gate validates compiler graph construction, LoRA ordering, payload migration, loader safety, route behavior, and fail-closed contracts. It does not claim visual-quality equivalence for every third-party LoRA or physically validate H3 GGUF LoRA loading on a GPU-backed ComfyUI instance.

H3 GGUF remains deliberately blocked.

## Phase-6 files

Core/runtime:

- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `neo_app/video/minimax_h3_lora_regression.py`
- `neo_extensions/built_in/video.lora_stack/extension_manifest.json`

Regression automation:

- `tests/test_minimax_h3_lora_regression_phase6.py`
- `.github/workflows/phase6_minimax_h3_lora_regression.yml`

Guides/records:

- `guides/02_VIDEO/video_lora_stack.md`
- `guides/02_VIDEO/minimax_h3_lora_regression.md`
- `guides/02_VIDEO/README.md`
- `neo_system_records/06_SURFACES/video/VIDEO_LORA_STACK_PHASE6_REGRESSION.md`

## Next phase

The Phase-6 gate is green, so the next implementation phase is allowed to onboard **LTX UNET standard LoRA runtime integration**, starting with Txt2Vid and Img2Vid only. Speed/Turbo support for LTX is not implied.
