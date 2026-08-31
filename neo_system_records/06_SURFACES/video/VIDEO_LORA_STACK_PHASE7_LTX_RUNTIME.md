# Video LoRA Stack — Phase 7 LTX UNET Runtime Record

**Recorded:** 2026-08-31  
**Surface:** Video  
**Extension:** `video.lora_stack`  
**Phase:** 7 — LTX 2.3 UNET LoRA Runtime  
**Phase-6 base:** `86fe5c624e43a0b91fd9a0fd8b9603b6b78643a7`  
**Phase-7 branch:** `phase-7-ltx-unet-lora-runtime`  
**Phase-7 PR:** `#3`

## Objective

Phase 7 onboards the first non-MiniMax family into the universal Video LoRA runtime. The scope is intentionally limited to standard model-only LoRAs on the two validated LTX 2.3 UNET primary routes:

```text
ltx23.unet.txt2vid
ltx23.unet.img2vid
```

No LTX speed/Turbo, GGUF, branch targeting, or extended generation-mode support is promoted in this phase.

## Compiler anchor

Both LTX primary compilers produce the same model boundary:

```text
model loader
  -> LTXVChunkFeedForward.model
```

Phase 7 wraps the compiler build entrypoints and publishes an exact compiler-owned patch profile at that boundary. The extension does not contain hardcoded workflow node IDs.

The runtime path becomes:

```text
LTX model loader
  -> standard Video LoRA(s)
  -> LTXVChunkFeedForward
  -> guider / sampler
```

## Runtime contract

Active Phase-7 LTX rows require:

```text
loader class     = LoraLoaderModelOnly
loader type      = model_only
role             = standard
target           = all
route loader     = UNET
selected file    = present in live ModelOnly catalog
```

A selected LoRA that is absent from the live catalog is rejected before workflow queueing.

Generic `LoraLoader` is not accepted as an implicit substitute because the validated graph topology is model-only and does not publish a CLIP patch contract.

## Fail-closed boundaries

Phase 7 explicitly rejects:

- `role=speed` / Turbo on LTX;
- `target=high` or `target=low` on LTX;
- LTX GGUF LoRA injection;
- generic `LoraLoader` without `LoraLoaderModelOnly`;
- empty ModelOnly LoRA catalog;
- selected LoRA filename absent from the live catalog.

Extended LTX modes remain outside this phase:

```text
first_last_frame
multiscene
extend
vid2vid
depth_motion
prompt_schedule
audio_video
```

## Regression harness

Added:

```text
neo_app/video/ltx_lora_regression.py
tests/test_ltx_lora_regression_phase7.py
```

Run:

```bash
python -m neo_app.video.ltx_lora_regression
```

Schema:

```text
neo.video.ltx23.lora_regression.v1
```

The deterministic LTX matrix contains **17 cases**.

### Per-mode cases

For both Txt2Vid and Img2Vid:

1. empty stack graph equivalence;
2. disabled populated stack graph equivalence;
3. one standard LoRA;
4. multiple standard LoRAs;
5. speed role rejected;
6. high target rejected;
7. GGUF LoRA rejected.

### Shared fail-closed cases

- selected file missing from live ModelOnly catalog;
- empty ModelOnly catalog;
- generic `LoraLoader` without ModelOnly loader.

## CI result

GitHub Actions workflow:

```text
Phase 7 LTX UNET LoRA Regression
```

Authoritative result from the first Phase-7 CI execution:

```text
LTX Phase 7 regression      17 / 17 passed
MiniMax H3 regression guard 43 / 43 passed
Combined regression         60 / 60 passed
```

Both unittest wrappers also passed.

This verifies that installing the LTX integration did not regress the Phase-6 MiniMax H3 reference implementation.

## No-op invariant

For both LTX primary routes:

```text
empty stack graph == original graph
```

and:

```text
disabled populated stack graph == original graph
```

No model consumer is rewired in either no-op case.

## Files introduced by the core Phase-7 implementation

```text
.github/workflows/phase7_ltx_lora_regression.yml
neo_app/video/ltx_lora_integration.py
neo_app/video/ltx_lora_regression.py
tests/test_ltx_lora_regression_phase7.py
```

Modified:

```text
neo_app/video/__init__.py
neo_extensions/built_in/video.lora_stack/extension_manifest.json
```

Documentation/records are added as part of the Phase-7 promotion pass.

## Current runtime support after Phase 7

MiniMax H3 UNET remains the speed/Turbo-capable reference implementation across all five validated modes.

LTX 2.3 UNET now supports standard LoRA stacking on Txt2Vid and Img2Vid only.

WAN migration/runtime work remains a later phase and must continue to use compiler-owned anchors rather than extending the legacy hardcoded adapter.

## Promotion decision

Phase 7 is eligible to proceed after the final branch-head GitHub Actions run reports both:

```text
H3: 43 / 43
LTX: 17 / 17
```

with the Phase-7 change-only artifact produced from that same successful run.
