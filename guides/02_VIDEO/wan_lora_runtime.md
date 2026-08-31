---
guide_id: video.wan_lora_runtime
title: WAN 2.2 Video LoRA Runtime
surface: video
scope: built_in
applies_to:
  - wan22.unet.txt2vid
  - wan22.unet.img2vid
  - wan22.gguf.img2vid_14b_dual_noise
priority: 83
version: 1
updated: 2026-09-01
---

# WAN 2.2 Video LoRA Runtime

Phase 8 migrates WAN Video LoRA graph mutation to the universal `video.lora_stack` architecture. The WAN compiler remains graph authority; the LoRA integration consumes compiler-owned patch profiles and never relies on the historical hardcoded LoRA node IDs.

## Supported routes

| Route | Standard | Speed | Targets | Loader contract |
|---|---:|---:|---|---|
| `wan22.unet.txt2vid` | Yes | No | `all` | `model_only` |
| `wan22.unet.img2vid` | Yes | No | `all` | `model_only` |
| `wan22.gguf.img2vid_14b_dual_noise` | Yes | Yes | `all`, `high`, `low` | `model_only_multi_branch` |

All active WAN LoRA paths require `LoraLoaderModelOnly`. Generic `LoraLoader` is not accepted as a fallback.

WAN Rapid AIO and imported native-workflow routes remain fail-closed for LoRA runtime.

## Single-model WAN UNET

The compiler builds the normal WAN graph first. Phase 8 locates the actual model input consumed by the compiler-selected `ModelSamplingSD3` node and publishes that relationship as a single-model patch profile.

```text
WAN UNET loader
  -> standard LoRA(s)
  -> ModelSamplingSD3
  -> sampler
```

The integration does not assume the loader or sampling node ID. It uses the class selected by the compiler and the model reference found in the built graph.

Phase-8 WAN UNET accepts only:

```text
role   = standard
target = all
loader = UNET
LoRA loader = LoraLoaderModelOnly
```

Speed rows and `high`/`low` targets fail closed on this topology.

## Dual-noise WAN GGUF

The dual-noise compiler exposes distinct high-noise and low-noise model loaders. Phase 8 builds a compiler-owned multi-branch profile from those emitted loader outputs and their actual downstream consumers.

```text
High model loader
  -> [high-target LoRA chain]
  -> Sage / TeaCache / low-VRAM patches when enabled
  -> high ModelSamplingSD3

Low model loader
  -> [low-target LoRA chain]
  -> Sage / TeaCache / low-VRAM patches when enabled
  -> low ModelSamplingSD3
```

Target behavior:

- `all` inserts the row independently on both high and low branches;
- `high` patches only the high-noise branch;
- `low` patches only the low-noise branch.

Within each branch, standard rows are applied before `role=speed` rows.

## Legacy WAN migration bridge

The historical WAN controls remain load-compatible:

```text
enable_video_lora
video_lora_mode
video_lora_model
video_lora_strength
video_lora_target
enable_lightx2v
high_noise_lora
low_noise_lora
high_noise_lora_strength
low_noise_lora_strength
```

They no longer mutate the graph directly.

Phase 8 first compiles a base dual-noise graph with the historical LoRA injection disabled, converts legacy intent into universal rows, merges/deduplicates those rows with `video.lora_stack`, and then patches only through the compiler-owned high/low profile.

Legacy target conversion:

```text
Both -> all
High -> high
Low  -> low
```

Legacy LightX2V becomes two `role=speed` rows: one high-noise row and one low-noise row.

If the same file/branch already exists in the universal stack, the bridge suppresses the duplicate. When a legacy LightX2V request overlaps a universal standard row on the same branch, the existing row is promoted to `speed` instead of loading the same file twice.

The compatibility metadata remains available under `video_lora_adapter`, but Phase 8 marks the universal stack as the graph-mutation authority.

## Historical node IDs

The old adapter used fixed LoRA nodes such as:

```text
129:101
129:102
9001
9002
```

Phase 8 does not use those IDs to insert or rewire LoRAs. The regression gate explicitly asserts that those historical LoRA nodes are absent from migrated workflows.

The legacy adapter file remains in the repository during migration so saved-control semantics can still be interpreted. It is not removed in Phase 8.

## Generate -> Compile payload preservation

WAN Generate functions reconstruct request dataclass payloads before invoking Compile. Extension blocks are not dataclass fields, so an unguarded nested call can drop `extensions.video.lora_stack`.

`neo_app/video/wan_lora_payload_context.py` keeps the original outer user payload authoritative until the compiler build hook consumes it.

The Phase-8 gate tests this explicitly for:

- WAN UNET Txt2Vid Generate;
- WAN dual-noise GGUF Img2Vid Generate.

## Live catalog validation

When any WAN LoRA row is requested, the selected file must exist in the live `LoraLoaderModelOnly.lora_name` catalog returned by ComfyUI `/object_info`.

The compiler fails before queueing when:

- `LoraLoaderModelOnly` is missing;
- only generic `LoraLoader` exists;
- the ModelOnly LoRA catalog is empty;
- a requested filename is absent from the live catalog;
- a WAN UNET row requests `role=speed`;
- a WAN UNET row requests `high` or `low`.

## Sampling behavior for legacy LightX2V

The legacy WAN control historically recommended a 4-step recipe:

```text
steps = 4
guidance = 1.0
split_step = 2
```

Phase 8 preserves the legacy behavior boundary:

- with `preserve_user_overrides=true`, user sampling values remain untouched and the recipe is recommendation-only;
- otherwise the legacy bridge may cap the legacy LightX2V request to the established 4-step values before the base graph is compiled.

Universal speed rows do not silently rewrite sampling values merely because `role=speed` is present.

## Regression gate

Run:

```bash
python -m neo_app.video.wan_lora_regression
```

CI-verified Phase-8 result:

```text
WAN 30 / 30
H3  43 / 43
LTX 17 / 17
Total 90 / 90
```

The 30 WAN cases cover:

- WAN UNET Txt2Vid/Img2Vid empty and disabled no-op equivalence;
- one and multiple standard LoRAs;
- WAN UNET speed/branch-target rejection;
- live-catalog and generic-loader failures;
- dual-noise `all`, `high`, and `low` targeting;
- mixed branch stacks;
- dual-noise speed rows;
- legacy Normal LoRA Both/High migration;
- legacy LightX2V high/low migration;
- duplicate suppression and role promotion;
- absence of historical hardcoded LoRA node IDs;
- Generate -> Compile payload preservation.

## Related files

- `neo_app/video/wan_lora_integration.py`
- `neo_app/video/wan_lora_payload_context.py`
- `neo_app/video/wan_lora_regression.py`
- `neo_app/video/video_lora_adapter.py`
- `neo_app/video/wan_txt2vid_compiler.py`
- `neo_app/video/wan_gguf_i2v14_compiler.py`
- `neo_app/video/lora_patch_profiles.py`
- `neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json`
- `tests/test_wan_lora_regression_phase8.py`

## Current boundary

Phase 8 proves the migration runtime. It does not delete the legacy adapter or remove legacy UI/request fields. That cleanup should happen only after saved-workflow compatibility and the final Video LoRA UI have a stable migration path.
