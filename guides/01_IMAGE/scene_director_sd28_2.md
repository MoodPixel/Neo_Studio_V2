---

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.

guide_id: image.scene_director.sd28_2
title: Scene Director — SD-28.2 Lightweight Regional Prompt Engine
surface: image
scope: built_in
applies_to:
  - image_workspace
  - scene_director
  - regional_prompting
  - krea2
  - krea2_turbo
  - flux2_klein
  - z_image
  - z_image_turbo
  - diffusion_model
  - gguf
  - generate
  - img2img
  - inpaint
priority: 128
version: 1
updated: 2026-07-27
---

# Scene Director — SD-28.2 Lightweight Regional Prompt Engine

SD-28.2 activates Scene Director regional **prompt** execution for Neo's modern Krea 2, FLUX.2 Klein, and Z-Image families without sending those models through the SDXL V054 repair stack.

## Execution boundary

Scene Director still presents one user-facing system, but workflow execution now has two engines:

| Family | Engine | State | Regional prompt | Regional LoRA | Heavy SD repairs |
|---|---|---|---|---|---|
| SDXL | `classic_v054` | available | existing V054 | existing V054 contract | existing V054 policy |
| SD1.5 | `classic_v054` | experimental | existing V054 | existing V054 contract | existing V054 policy |
| Krea 2 RAW | `lightweight_regional` | experimental | masked conditioning | gated for SD-28.3 | off |
| Krea 2 Turbo | `lightweight_regional` | experimental | masked conditioning | gated for SD-28.3 | off |
| FLUX.2 Klein | `lightweight_regional` | experimental | masked conditioning + FluxGuidance | gated for SD-28.3 | off |
| Z-Image | `lightweight_regional` | experimental | masked conditioning | gated for SD-28.3 | off |
| Z-Image Turbo | `lightweight_regional` | experimental | masked conditioning | gated for SD-28.3 | off |

Modern routes are enabled for `diffusion_model` and `gguf` loaders in Generate, Img2Img, and Inpaint. Outpaint remains planned-gated.

## Lightweight graph contract

For each active region with prompt text, Neo adds only built-in Comfy conditioning/mask nodes:

```text
Region bbox
   ↓
SolidMask (local)
   ↓ optional
FeatherMask
   ↓
MaskComposite → full-canvas region mask
   ↓
CLIPTextEncode(region prompt)
   ↓
[FluxGuidance only for FLUX.2 Klein]
   ↓
ConditioningSetMask(set_cond_area = mask bounds)
   ↓
ConditioningCombine with current positive conditioning
   ↓
EXISTING KSampler
```

The engine does **not** add a second sampler and does not replace the provider-owned latent/model stack.

### Provider-owned graph stays authoritative

SD-28.2 does not recreate Krea/Klein/Z-Image model loading. It consumes the model, CLIP and sampler references already compiled by the active provider route. Therefore native and GGUF routes keep their own model loader, text-encoder loader, VAE, image/inpaint latent path, sampler settings, and decode path.

## Negative prompt policy

The regional negative path follows the native model family instead of forcing one universal SD-style CFG policy.

| Family | Regional negative behavior |
|---|---|
| Krea 2 RAW | regional negative is encoded, masked and combined |
| Z-Image base | regional negative is encoded, masked and combined |
| Krea 2 Turbo | authored regional negatives are suppressed; final negative is `ConditioningZeroOut` from the combined positive |
| FLUX.2 Klein | authored regional negatives are suppressed; final negative is `ConditioningZeroOut` from the combined positive |
| Z-Image Turbo | authored regional negatives are suppressed; final negative is `ConditioningZeroOut` from the combined positive |

This protects Turbo/Klein low-CFG/distilled conditioning behavior rather than silently turning those routes into SD-style positive/negative guidance.

## Prompt Authority

### Global context + Scene Director structure

The provider's existing global positive/negative conditioning remains the canvas base. Scene Director combines masked regional conditions on top of it. The global prompt is **not copied into every regional CLIP encode**.

### Scene Director only

Neo zeroes the existing base conditioning and combines only Scene Director regional prompt lanes. This preserves the existing user-facing authority meaning without adding another sampler.

## FLUX.2 Klein

Klein's provider graph applies `FluxGuidance` after `CLIPTextEncode`. SD-28.2 mirrors that model-native path for every regional positive lane using the same guidance value already present in the base graph. It does not bypass or replace Klein guidance.

## Compatibility bridge

The existing classic `backend/workflow_patch.py` is intentionally left unchanged. SD-28.2 adds `backend/workflow_dispatch.py` and routes the historical Python import through it:

- classic route → lazy delegate to the frozen V054 patcher;
- lightweight route → `lightweight_regional.py`;
- unsupported route → no mutation, no V054 fallback.

`backend/payload_schema_dispatch.py` similarly delegates the existing payload schema and only adjusts modern-route metadata/warning text so Krea/Klein/Z-Image do not report a false missing-V054 or SD1.5 experimental message.

The proxy modules implement `__getattr__` delegation so legacy imports of helper symbols keep resolving to the original modules.

## Runtime proof

Every applied lightweight prompt patch emits `scene_director_lightweight_runtime_proof` with:

```text
sampler ids before / after
sampler count before / after
single_sampler_preserved
sampler_parameters_preserved
model_input_unchanged
positive_input_rewired
negative_input_rewired
regional_prompt_lane_count
regional_negative_lane_count
regional_negative_suppressed_count
heavy_sd_repairs_added = false
repair_sampler_nodes_added = 0
regional_lora_nodes_added = 0
global_model_mutation = false
```

`runtime_status = graph_contract_applied_not_gpu_proven` is deliberate. The implementation has static graph/unit proof but must not claim visual/GPU runtime validation until it has been run against live Comfy models.

## Regional LoRA boundary

SD-28.2 does not implement regional LoRA model deltas.

The Scene Director region → LoRA assignment may be preserved in payload/replay metadata, but the lightweight prompt compiler adds no LoRA loader, no masked-LoRA finish pass, and no global model mutation. True native masked-delta LoRA execution belongs to SD-28.3.

## Required Comfy nodes

The lightweight core requires:

```text
CLIPTextEncode
ConditioningSetMask
ConditioningCombine
ConditioningZeroOut
SolidMask
MaskComposite
FeatherMask
```

FLUX.2 Klein additionally requires `FluxGuidance`.

The modern path does **not** require `NeoSceneDirectorV054`; that node remains required for classic SDXL/SD1.5.
