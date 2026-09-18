---
guide_id: video.minimax_h3_lora_regression
title: MiniMax H3 LoRA Regression Gate
surface: video
scope: built_in
applies_to:
  - video_generation
  - minimax_h3
  - video_lora_stack
  - comfyui
  - regression
tags:
  - video
  - minimax h3
  - lora
  - turbo
  - regression
  - img2vid
priority: 85
version: 2
updated: 2026-08-31
---

# MiniMax H3 LoRA Regression Gate

Phase 6 is the release gate for the MiniMax H3 implementation of `video.lora_stack`. It intentionally adds no LTX or WAN runtime support. The goal is to prove that the H3 universal LoRA architecture remains safe across every supported H3 UNET generation mode before another family is onboarded.

## Gate status

**CI verified: PASS**

GitHub Actions executed the committed regression module and unittest wrapper on Python 3.11 and returned:

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

The gate is therefore green for Phase 7 family onboarding.

## Run the gate

From the Neo Studio repository root:

```bash
python -m neo_app.video.minimax_h3_lora_regression
```

The module prints a JSON report and returns:

```text
exit 0 -> all regression cases passed
exit 1 -> at least one regression case failed
```

Report schema:

```text
neo.video.minimax_h3.lora_regression.v1
```

The report includes every case, pass/fail state, failure text, total counts, and `next_phase_allowed`.

The unittest wrapper is:

```bash
python -m unittest tests.test_minimax_h3_lora_regression_phase6 -v
```

CI runs both commands through `.github/workflows/phase6_minimax_h3_lora_regression.yml`.

## Test environment

The regression module uses deterministic synthetic ComfyUI `/object_info` data. It does not queue a real video or require installed H3 model weights. The synthetic backend exposes the exact contracts the H3 compiler consumes:

- `UNETLoader`
- `CLIPLoader`
- `VAELoader`
- `LoraLoaderModelOnly`
- H3 FL2VA and Ref2VA model catalog entries
- H3 video/audio VAE catalog entries
- normal Video LoRAs
- MiniMax LightX2V speed LoRA
- Hailuo Lightning speed LoRA

A fixed seed is used so no-LoRA workflow equivalence is structural and deterministic.

The CI runner installs the minimum Neo import dependencies required to exercise the actual repository import path, including Pydantic, Pillow, and CPU Torch. The regression itself remains CPU/deterministic graph validation and does not run H3 weights.

## Generation modes under test

Every core stack test runs against all five H3 UNET modes:

```text
txt2vid
img2vid
first_last_frame
reference_to_video
vid2vid
```

Img2Vid remains a priority regression route because the original product problem was Turbo not surfacing/behaving correctly on Img2Vid.

## 43-case matrix

### Classifier behavior

The gate verifies that:

- `MiniMax-LightX2V-4steps.safetensors` is recognized as an H3 speed candidate;
- `hailuo_lightning_8steps.safetensors` is recognized as an H3 speed candidate;
- a normal manually selected LoRA is not classified as speed and is still accepted when present in the live catalog.

The classifier remains recommendation-only. It is not a permission gate.

### Per-mode graph tests

For each of the five H3 modes the gate verifies:

1. empty stack = original H3 workflow graph;
2. disabled stack containing rows = original H3 workflow graph;
3. one standard LoRA;
4. multiple standard LoRAs;
5. one speed/Turbo LoRA;
6. mixed standard + speed stack with standard rows forced before speed rows.

That is 30 per-mode cases.

### Compiler anchor assertions

For active stacks the gate verifies:

```text
profile schema = neo.video.lora_patch_profile.v1
owner = compiler
loader_type = model_only
loader_node_class = LoraLoaderModelOnly
targets = [all]
validated = true
```

It also verifies that:

- the compiled `prompt_api_payload.prompt` equals the patched workflow;
- LoRA nodes are ordered as expected;
- runtime role ordering matches graph ordering;
- `MiniMaxH3SigmaShift.model` consumes the final LoRA model reference;
- workflow/profile/runtime metadata can be JSON serialized for queue payloads and sidecars;
- the live LoRA catalog was validated before active application.

## Legacy H3 Turbo compatibility

The gate includes Img2Vid-specific migration coverage.

### Legacy Turbo only

Old fields:

```text
h3_turbo_enabled
h3_turbo_lora
h3_turbo_strength
```

must create exactly one universal `role=speed` LoRA node.

### Legacy + universal duplicate

If the same speed LoRA already exists in the universal stack, the synthetic legacy row must be suppressed rather than applied twice.

### Duplicate saved as standard

Phase 6 hardened an edge case found during regression: a saved universal row could contain the same Turbo file but still have `role=standard`. Deduplication previously prevented a duplicate node but also left the row looking non-Turbo.

Current behavior:

```text
same file already in stack
  -> keep one row
  -> preserve universal row strength/uid
  -> promote role to speed
  -> normalize H3 target to all
  -> mark duplicate_suppressed
```

The CI run explicitly passed this case, including `existing_role_promoted=true` and target normalization.

### Auto-discovery

Legacy Turbo enabled with no explicit LoRA name must select a valid H3 speed candidate from the live `LoraLoaderModelOnly` catalog.

## Fail-closed cases

The gate requires explicit failure for:

- selected LoRA missing from the live ModelOnly catalog;
- installed `LoraLoaderModelOnly` exposing an empty LoRA catalog;
- backend exposing only generic `LoraLoader`;
- H3 GGUF + Video LoRA/Turbo;
- H3 row using WAN-only `high`/`low` target;
- legacy Turbo naming a LoRA that is not present in the live ModelOnly catalog.

### Why missing files now fail

Phase 5 only warned when a selected row was not visible in the live LoRA catalog. That could defer the failure until ComfyUI rejected the queue graph.

Phase 6 changes this to a compile-time error. Manual selection is still allowed; the selected filename simply must be a real option exposed by `LoraLoaderModelOnly`.

## H3 graph invariant

For an active stack the intended model flow is:

```text
H3 model loader
  -> standard LoRA(s)
  -> speed/Turbo LoRA(s)
  -> MiniMaxH3SigmaShift
  -> optional Sage / Spectrum / T8 BlockCache
  -> H3 scheduler / guider / sampler
```

For an empty or disabled stack:

```text
Phase-6 workflow graph == original H3 workflow graph
```

LoRA metadata/profile information may be present outside the workflow, but no LoRA node may be inserted and no model consumer may be rewired.

GitHub Actions verified this no-op invariant for all five modes.

## Current boundary

Passing this deterministic gate means the compiler/LoRA graph contract is ready for the next family onboarding phase. It does not claim visual quality equivalence on every third-party LoRA or validate an H3 GGUF LoRA topology on a physical GPU-backed ComfyUI installation.

H3 GGUF remains explicitly blocked until its actual installed LoRA loader contract is verified.

## Related files

- `neo_app/video/minimax_h3_lora_regression.py`
- `neo_app/video/minimax_h3_lora_integration.py`
- `neo_app/video/video_lora_runtime.py`
- `neo_app/video/lora_patch_profiles.py`
- `tests/test_minimax_h3_lora_regression_phase6.py`
- `.github/workflows/phase6_minimax_h3_lora_regression.yml`
- `guides/02_VIDEO/video_lora_stack.md`
- `guides/02_VIDEO/minimax_h3_local_support.md`

## Promotion rule

Phase 6 has satisfied the promotion rule:

```json
{
  "gate": "pass",
  "failed": 0,
  "next_phase_allowed": true
}
```

The next runtime phase may therefore begin with **LTX UNET standard LoRA integration for Txt2Vid and Img2Vid**. This does not authorize LTX speed/Turbo support or unvalidated LTX routes.
