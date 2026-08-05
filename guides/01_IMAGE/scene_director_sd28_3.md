---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_3
title: Scene Director — SD-28.3 Regional LoRA Runtime Foundation
surface: image
scope: built_in
applies_to:
  - image_workspace
  - generations
  - scene_director
  - regional_lora
  - krea2
  - krea2_turbo
  - comfyui
priority: 129
version: 1
updated: 2026-07-27
---

# Phase SD-28.3 — Regional LoRA Runtime Foundation

SD-28.3 adds the first executable model-side regional LoRA path to Scene Director's `lightweight_regional` engine without changing the classic SDXL/SD1.5 V054 runtime.

The first executable adapter is deliberately narrow:

| Family | Regional prompts | Regional LoRA in SD-28.3 | Status |
|---|---|---|---|
| Krea 2 RAW | Lightweight masked conditioning | `krea2_activation_delta_v1` | Experimental runtime foundation |
| Krea 2 Turbo | Lightweight masked conditioning | `krea2_activation_delta_v1` | Experimental runtime foundation |
| FLUX.2 Klein | Lightweight masked conditioning | No adapter | Adapter-gated |
| Z-Image | Lightweight masked conditioning | No adapter | Adapter-gated |
| Z-Image Turbo | Lightweight masked conditioning | No adapter | Adapter-gated |

Do not describe Klein or Z-Image regional LoRA as supported yet. Their prompt engine support is separate from their LoRA model-side runtime support.

## User-facing workflow stays the same

There is no second Scene Director panel and no separate "Krea Scene Director" mode.

The existing Region → Advanced LoRA assignment remains the user-facing intent source. LoRA Stack owns which LoRA file/row is selected. Scene Director owns which Scene Director region that row targets.

Conceptually:

```text
LoRA Stack row
    apply_to = scene_region_N
             │
             ▼
Scene Director region binding
             │
             ├── regional prompt → masked conditioning
             │
             └── Krea2 regional LoRA → NeoRegionalLoRADelta
                                         │
                                         ▼
                                  cloned MODEL wrapper
                                         │
                                         ▼
                                  existing provider graph
                                         │
                                         ▼
                                     ONE sampler
```

Global LoRA rows continue through LoRA Stack's normal base-graph patching path. Scene-region-targeted rows are not reinterpreted as global rows.

## Internal runtime node

SD-28.3 registers one small internal Comfy node:

```text
NeoRegionalLoRADelta
```

Its contract is intentionally limited:

```text
INPUTS
  MODEL
  regional route JSON
  model family
  canvas size
  seam feather
  sampler count proof

OUTPUT
  MODEL
```

It does not output CLIP, conditioning, latent, image, mask, or sampler data. It does not own Character Lock, ControlNet, ADetailer, image decode, inpaint/outpaint semantics, or repair passes.

The node clones the active Comfy `ModelPatcher`, then attaches a keyed `DIFFUSION_MODEL` wrapper. The original MODEL is not weight-patched by the regional execution path.

## Krea 2 activation-delta adapter

For each region-targeted Krea 2 LoRA, Neo:

1. resolves the LoRA file through ComfyUI's configured `loras` search paths;
2. parses supported standard LoRA A/B or down/up matrix pairs;
3. uses ComfyUI's current Krea2-aware `model_lora_keys_unet()` mapping first;
4. resolves each pair to a live Krea 2 diffusion-model module;
5. derives an image-token mask from the Scene Director bounding box at runtime;
6. registers temporary forward hooks only for matched layers;
7. computes the LoRA activation delta;
8. multiplies that direct delta by the owning region's token mask;
9. adds the masked delta to that layer output;
10. removes every temporary forward hook in `finally` after the wrapped model call.

The execution shape is:

```text
base layer output + region_mask × LoRA_delta
```

Multiple regional LoRAs can participate in the same forward pass. SD-28.3 has no inherited two-route limit.

### Important isolation wording

`NeoRegionalLoRADelta` provides **direct activation-delta masking**: the LoRA delta added at a hooked layer is zero for tokens outside the selected region and text tokens are never deliberately assigned the region mask.

Do not claim that the final generated pixels outside a region are mathematically guaranteed to be identical to a no-LoRA generation. Later transformer layers can mix information between tokens. Runtime leakage must therefore still be measured with fixed-seed comparison tests before Neo promotes the adapter beyond experimental status.

## CLIP policy

Regional CLIP LoRA mutation is intentionally suppressed:

```text
clip_delta_execution = suppressed_model_side_only
```

A LoRA row may still contain `target=both` because LoRA Stack owns that source record, but SD-28.3 executes the regional component as `model_only`. Trigger/activation words from the selected LoRA record are appended only to the owning Scene Director region prompt; they are not injected into the global prompt or neighboring regions.

This avoids pretending that a globally patched CLIP object can be hard spatially isolated by a region mask.

## Provider wrapper preservation

The regional MODEL node is inserted at the active model reference and Neo rewires matching MODEL consumers rather than blindly replacing the sampler's MODEL input.

This matters for provider-owned wrappers such as:

```text
Regional MODEL
      ↓
DifferentialDiffusion
      ↓
KSampler
```

or other model-sampling wrappers. Scene Director must not bypass those provider semantics merely to insert regional LoRA execution.

Global LoRA + regional LoRA also composes in this order:

```text
base MODEL
   ↓
LoRA Stack global rows
   ↓
NeoRegionalLoRADelta regional rows
   ↓
provider wrappers
   ↓
existing sampler
```

## Single-sampler and fallback contract

The lightweight engine still has a strict one-sampler policy.

SD-28.3 does not add:

- a regional-LoRA KSampler;
- a crop/finish KSampler;
- Character Lock repair passes;
- midpoint repair;
- end refinement;
- background repaint/reconciliation;
- a standard global `LoraLoader` fallback for a regional row.

If the regional runtime cannot be armed, the regional LoRA route is blocked/gated rather than converted into one of those alternatives.

## Fail-closed conditions

Regional LoRA execution is blocked when any of the following is true:

- the selected family has no validated model-side adapter;
- `NeoRegionalLoRADelta` is missing from Comfy object-info;
- a selected LoRA file is missing from Comfy's LoRA search paths;
- the LoRA contains no supported standard A/B or down/up pairs;
- no LoRA layers match the active Krea 2 model;
- the runtime latent cannot provide a usable Krea 2 image-token grid.

For token-sequence masking, unknown layouts use an all-zero mask. Neo does **not** substitute an average/global mask when it cannot prove the image-token span.

## Runtime proof

The node keeps a mutable runtime attachment with these proof fields:

```json
{
  "lora_loaded": true,
  "model_family_match": true,
  "region_mask_bound": true,
  "masked_delta_hook_active": true,
  "delta_eval_attempted": true,
  "delta_nonzero": true,
  "global_model_mutation": false,
  "sampler_count": 1,
  "forward_hooks_removed": true,
  "runtime_gpu_proven": true
}
```

`runtime_gpu_proven=true` is possible only after the wrapped diffusion-model call actually executes and produces a non-zero masked delta with one sampler. Static graph compilation must continue to report `armed_not_gpu_proven` / `runtime_gpu_proven=false`.

A future inspector phase can expose the attachment directly in Neo's Output Inspector.

## Current validation level

SD-28.3 includes static graph tests and tensor-level wrapper/hook tests. These prove graph placement, failure behavior, hook cleanup, masking mechanics, route count behavior, and runtime-proof transitions on a synthetic torch module.

They are **not** a substitute for a real Krea 2 ComfyUI generation. Before promotion from experimental status, run fixed-seed GPU tests with real Krea 2 RAW and Turbo LoRAs, including outside-region difference measurements and 3+ regional LoRA scenes.

## Source / compatibility notes

The adapter is aligned with current upstream behavior as of 2026-07-27:

- Krea 2 officially recommends training LoRAs on RAW and applying them to Turbo.
- Current ComfyUI exposes Krea2-specific LoRA key mapping through `model_lora_keys_unet()`.
- Current ComfyUI `ModelPatcher` exposes clone, keyed wrappers, and attachments; `WrappersMP.DIFFUSION_MODEL` is the model-forward wrapper type used by this foundation.
- The activation-delta/bounding-box approach was validated as an implementation direction against the public Krea2 Multi-Character Regional LoRA custom-node project, but Neo keeps its own narrower fail-closed contract and does not copy its broader feature claims.

References:
- https://github.com/krea-ai/krea-2
- https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/lora.py
- https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/model_patcher.py
- https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/patcher_extension.py
- https://github.com/CliffNodes/Krea2-Multi-Character-Lora-Node-w-bounding-box

## Superseded Krea status — SD-28.4

SD-28.4 promotes Krea 2 RAW and Krea 2 Turbo beyond this phase's experimental foundation. Krea now uses the `krea2_activation_delta_v2` contract with explicit RAW/Turbo compatibility checks, Turbo sampler-profile protection, spatial-module filtering, GGUF loader propagation, and per-run runtime proof. FLUX.2 Klein and Z-Image regional LoRA remain adapter-gated.

## Superseded Z-Image foundation status — SD-28.6

SD-28.6 promotes Z-Image Base and Z-Image Turbo beyond the adapter-gated foundation recorded here. They now use `z_image_activation_delta_v1` with family-specific NextDiT spatial filtering and fail-closed token-padding handling. The original SD-28.3 runtime-proof requirements remain mandatory.
