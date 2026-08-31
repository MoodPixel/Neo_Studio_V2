# Video LoRA Stack — Phase 8 WAN Runtime Migration

Date: 2026-09-01

## Status

Phase 8 migrates WAN 2.2 Video LoRA graph mutation to the universal `video.lora_stack` runtime while retaining the existing WAN request controls as a compatibility bridge.

Initial core CI verification on the implementation branch passed:

```text
MiniMax H3 guard: 43 / 43
LTX 2.3 guard:    17 / 17
WAN 2.2 gate:     30 / 30
Combined:         90 / 90
```

All three unittest wrappers also passed.

## Phase-8 branch

```text
base:   044705fea944cb42493245bbc6bcc8bcb9a42fb3
branch: phase-8-wan-video-lora-runtime
PR:     #4 (draft, unmerged)
```

The Phase-7 CI-green head is the Phase-8 base so the diff remains phase-isolated.

## Promoted WAN routes

### Single-model UNET

```text
wan22.unet.txt2vid
wan22.unet.img2vid
```

Supported Phase-8 LoRA contract:

```text
standard LoRA: yes
speed LoRA:    no
targets:       all
loader type:   model_only
node class:    LoraLoaderModelOnly
```

The integration identifies the actual model reference consumed by the compiler-selected `ModelSamplingSD3` node and publishes that relationship through `neo.video.lora_patch_profile.v1`.

### Dual-noise GGUF

```text
wan22.gguf.img2vid_14b_dual_noise
```

Supported Phase-8 LoRA contract:

```text
standard LoRA: yes
speed LoRA:    yes
targets:       all / high / low
loader type:   model_only_multi_branch
node class:    LoraLoaderModelOnly
```

The integration derives high/low anchors from the compiler-emitted model loaders and their actual downstream consumers. The universal runtime applies each target independently.

## Legacy migration

The previous WAN LoRA fields are still accepted, including Normal LoRA and paired LightX2V controls. They are converted into universal rows before graph mutation.

Historical fixed LoRA node IDs are no longer patch authority:

```text
129:101
129:102
9001
9002
```

The Phase-8 regression gate explicitly rejects a migrated graph if those historical LoRA nodes survive.

The existing `neo_app/video/video_lora_adapter.py` remains tracked during migration because it still defines compatibility semantics and legacy selection planning. It is not deleted in Phase 8.

## Legacy target bridge

```text
Both -> all
High -> high
Low  -> low
```

Legacy LightX2V becomes two speed rows:

```text
high-noise file -> role=speed, target=high
low-noise file  -> role=speed, target=low
```

Duplicate branch coverage is suppressed. An existing universal row may be promoted from `standard` to `speed` when a matching legacy LightX2V branch requires speed semantics.

## Generate payload preservation defect avoided

During implementation inspection, WAN Generate was found to rebuild request payloads from dataclasses before calling Compile. Since extension blocks are not dataclass fields, this could silently remove `extensions.video.lora_stack` in a nested Generate -> Compile call.

Phase 8 adds:

```text
neo_app/video/wan_lora_payload_context.py
```

This keeps the original user payload authoritative through the nested call. Regression cases verify both WAN UNET Txt2Vid Generate and dual-noise GGUF Img2Vid Generate retain and apply the outer universal stack.

## Loader safety

All Phase-8 WAN LoRA paths require live `LoraLoaderModelOnly` discovery. Generic `LoraLoader` is not accepted.

Requested rows fail closed when:

- the ModelOnly loader is missing;
- the ModelOnly catalog is empty;
- the selected file is absent from the live catalog;
- WAN UNET requests a speed role;
- WAN UNET requests high/low targeting.

## No-op invariant

Empty or disabled universal stacks preserve the compiler's original workflow for:

```text
WAN UNET Txt2Vid
WAN UNET Img2Vid
WAN dual-noise GGUF Img2Vid
```

Metadata can be attached outside the Comfy graph without violating this invariant.

## Regression gate

Authoritative command:

```bash
python -m neo_app.video.wan_lora_regression
```

Phase-8 WAN matrix contains 30 deterministic cases.

Cross-family promotion requires all of these to remain green in the same Actions job:

```bash
python -m neo_app.video.minimax_h3_lora_regression
python -m neo_app.video.ltx_lora_regression
python -m neo_app.video.wan_lora_regression
```

Expected combined gate:

```text
43 H3 + 17 LTX + 30 WAN = 90
```

## Files introduced by Phase 8 runtime

```text
neo_app/video/wan_lora_integration.py
neo_app/video/wan_lora_payload_context.py
neo_app/video/wan_lora_regression.py
tests/test_wan_lora_regression_phase8.py
.github/workflows/phase8_wan_lora_regression.yml
guides/02_VIDEO/wan_lora_runtime.md
```

Phase 8 also updates package initialization, the Video LoRA support matrix, manifest, guide index, and canonical Video LoRA guide.

## Remaining boundaries

Phase 8 does not claim LoRA support for:

```text
WAN Rapid AIO GGUF
WAN imported native workflows
WAN UNET speed LoRAs
WAN UNET high/low targeting
```

It also does not remove the legacy adapter or legacy request/UI fields. A later migration-hardening/deprecation phase should define when those old surfaces can safely stop being user-facing.
