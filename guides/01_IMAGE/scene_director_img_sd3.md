# IMG-SD3 — Krea2 Regional Engine Integration

**Status:** current Krea 2 Scene Director execution contract  
**Date:** 2026-08-16  
**Applies to:** Krea 2 RAW/Turbo on supported ComfyUI `diffusion_model` and GGUF routes for Generate, Img2Img, and Inpaint  
**External runtime dependency:** `januspluto/ComfyUI-Krea2-Regional`  
**Does not replace:** SDXL/SD1.5 Classic V054 or the existing FLUX.2 Klein / Z-Image lightweight runtime

## Decision

IMG-SD3 retires Neo's `NeoRegionalLoRADelta` as the default Krea 2 regional-LoRA engine. Manual GGUF benchmarking proved that the external Krea2 Regional architecture produced substantially cleaner multi-character composition and identity separation than Neo's SD2 activation-delta route while still accepting Neo's GGUF-loaded Krea model.

Scene Director remains the single user-facing owner. `ComfyUI-Krea2-Regional` is treated as an **external Comfy execution engine**, not as a second Neo extension or a replacement UI.

```text
Neo Scene Director
  ├─ region boxes / optional local prompts
  ├─ LoRA Stack row ownership
  └─ Krea2 regional options
          ↓
Krea2RegionalBuilder
          ↓
Krea2ApplyRegional
          ↓
Neo provider-owned KSampler / latent / decode
```

## Ownership boundary

Neo continues to own:

- native or GGUF Krea model loading;
- Krea/Qwen text-encoder loading;
- VAE selection/decode path;
- sampler, scheduler, steps, CFG, seed, and denoise;
- latent/image source construction;
- Img2Img/Inpaint source and mask ownership;
- High-Res Lab and other finish-stage dispatch;
- LoRA Stack asset selection and row UIDs.

Scene Director owns:

- region geometry;
- optional regional prompt text;
- mapping a LoRA Stack row to a region;
- per-region LoRA strength;
- Krea2 Regional isolation settings.

The external engine owns Krea's joint regional prompt/attention/LoRA execution only.

## Graph contract

A healthy Krea 2 Scene Director graph adds exactly:

```text
Krea2RegionalBuilder × 1
Krea2ApplyRegional   × 1
```

Krea 2 Turbo also adds/uses a `ConditioningZeroOut` for the external conditioning output so the provider's zero-negative policy remains intact.

The graph must keep exactly one provider sampler. It must not add:

- `NeoRegionalLoRADelta` on Krea 2;
- `LoraLoader` / `LoraLoaderModelOnly` for region-assigned LoRAs;
- Classic `NeoSceneDirectorV054`;
- hidden repair samplers.

The external Apply node receives the **model that already reaches the provider sampler**, not an earlier base-model reference. This preserves upstream model modifiers such as `DifferentialDiffusion` or unrelated global model patches instead of bypassing them.

## Builder translation

Neo converts each active Scene Director region into the external Builder `regions_data` JSON:

```json
{
  "shape": "rect",
  "x": 0.08,
  "y": 0.10,
  "w": 0.38,
  "h": 0.80,
  "desc": "optional regional prompt",
  "rtype": "obj",
  "text": "",
  "loras": [
    {"name": "path/to/character.safetensors", "strength": 1.3}
  ]
}
```

The user's provider/global prompt is passed to `Krea2RegionalBuilder.base_prompt` unchanged. IMG-SD3 does not restore the SD1C/SD1D hidden cast/subject bridge.

A LoRA-only region is valid. If its local prompt is blank, Neo supplies a minimal fallback/known activation text so the external Builder does not discard the region. Global prompt text is not polluted with other regions' trigger terms.

## Benchmark defaults

Neo defaults the Krea2 Regional controls to the configuration that behaved best in the validated GGUF comparison:

| Setting | Neo default |
|---|---|
| Adaptive masks | `refine boxes` |
| Exclusive masks | On |
| Restrict image attention | Off |
| Adaptive steps | 2 |
| Adaptive threshold | 0.45 |
| Region lock strength | 0.40 |
| Region lock start | 0.35 |
| Region lock end | 0.85 |
| Restrict end percent | 0.50 |
| Layout in base | `position hints` |
| Base LoRAs exclude regions | Off |
| Unmaskable layers | `skip` |
| Grow px | 0 |
| Feather px | 0 |

`restrict_img_attn` remains available as an optional control but is **Off by default** because the manual GGUF benchmark produced a duplicate/third subject when it was enabled. Adaptive masks were stable in every benchmark generation, and Exclusive Masks improved the clean two-subject result.

## UI contract

Modern Krea Scene Director remains Basic-only. The region card continues to expose its LoRA Stack row assignment and optional local prompt. The Krea-specific engine section exposes:

- Adaptive masks;
- Layout in base;
- Region lock strength;
- Restrict end;
- Exclusive masks;
- Restrict image attention.

The UI labels the engine **Krea2 Regional** and reports missing external nodes instead of claiming the legacy Neo regional runtime is available.

There is no new user-facing `image.krea2_regional` extension.

## Capability discovery and dependency failure

Neo's safe Comfy `/object_info` slice now transports:

```text
Krea2RegionalBuilder
Krea2ApplyRegional
```

When either node is absent on a Krea 2 Scene Director route, compilation fails closed before queue with install guidance for:

```text
januspluto/ComfyUI-Krea2-Regional
```

Neo must **not** silently fall back to `NeoRegionalLoRADelta`, a global LoRA loader, Classic V054, or a finish-pass approximation.

Because this is an external custom-node dependency, IMG-SD3 does not vendor or overwrite its source and does not use Neo's `sync_neo_scene_director_comfy_node.py` helper for it.

## Native Inpaint parity

For Krea native Inpaint, `InpaintModelConditioning` remains the source/mask/latent authority:

```text
Krea2ApplyRegional conditioning
            ↓
InpaintModelConditioning positive input
            ↓
KSampler positive output 0
KSampler negative output 1
KSampler latent   output 2
```

For Krea Turbo, the zero-negative external conditioning is fed into the wrapper's negative input. The sampler remains connected to the wrapper outputs; Scene Director does not replace the source-image or mask-owned latent.

## Execution proof

Provider proof advances to:

```text
neo.image.scene_director.execution_proof.img_sd3.v5
```

Important truth fields include:

- `engine = krea2_regional_external`;
- `krea2_regional_external_graph_verified`;
- Builder/Apply node IDs;
- `external_runtime_repo`;
- regional LoRA route count;
- external model input ref;
- single-sampler preservation;
- sampler parameter preservation;
- latent preservation;
- Inpaint wrapper preservation;
- adaptive/exclusive/restrict settings;
- `global_prompt_mutation = false`.

Compile-time proof establishes graph ownership and routing. It is not a mathematical guarantee that every LoRA will produce perfect visual likeness.

## Family boundaries after IMG-SD3

| Family | Scene Director engine |
|---|---|
| Krea 2 RAW / Turbo | `krea2_regional_external` adapter inside `lightweight_regional` |
| FLUX.2 Klein | Neo lightweight regional + `NeoRegionalLoRADelta` |
| Z-Image Base/Turbo | Neo lightweight regional + `NeoRegionalLoRADelta` |
| SDXL | Classic V054 |
| SD1.5 | Classic V054 experimental |

Qwen-family Scene Director execution is not promoted by IMG-SD3.
