# Scene Director — Current Architecture and Support Contract

**Canonical current-state guide:** SD-28.9  
**Runtime release lock:** SD-28.7  
**Surface:** Image → Generations → Scene Director  
**Backend:** ComfyUI / ComfyUI Portable

This document is the primary source of truth for the Scene Director modernization completed in SD-28.1 through SD-28.9. The numbered phase guides remain available as design and migration history; when a historical guide conflicts with this document, this document wins for current support state.


## Editor UI and readiness

SD-28.9 restores Scene Director as an **extension-owned editor**. The editor no longer depends on `neo_app/static/js/neo.js` for feature-specific hydration.

Readiness is capability-specific:

- classic SDXL/SD1.5 execution requires `NeoSceneDirectorV054`;
- modern Krea/Klein/Z regional prompting requires only the built-in Comfy conditioning/mask nodes declared by the execution strategy;
- `NeoRegionalLoRADelta` is conditionally required only when a compatible modern region-targeted LoRA route is requested;
- missing conditional nodes never hide or unmount the editor. They gate only the dependent execution capability.

The Runtime Inspector renders into a dedicated child root and cannot replace the editor DOM.

## Current engine split

Scene Director exposes one user-facing feature but uses two internal execution engines:

```text
Scene Director
├─ classic_v054
│  ├─ SDXL checkpoint
│  └─ SD1.5 checkpoint (experimental)
└─ lightweight_regional
   ├─ Krea 2 RAW
   ├─ Krea 2 Turbo
   ├─ FLUX.2 Klein
   ├─ Z-Image
   └─ Z-Image Turbo
```

`NeoSceneDirectorV054` remains the classic custom node. Modern families do not route through V054 and do not inherit its SDXL repair/rescue chain.

## Released route matrix

| Family | Engine | Loader | Generate | Img2Img | Inpaint | Outpaint |
|---|---|---|---|---|---|---|
| SDXL | `classic_v054` | checkpoint | Available | Available | Available | Planned-gated |
| SD1.5 | `classic_v054` | checkpoint | Experimental | Experimental | Experimental | Planned-gated |
| Krea 2 RAW | `lightweight_regional` | diffusion_model | Available | Available | Available | Planned-gated |
| Krea 2 RAW | `lightweight_regional` | GGUF | Available | Available | Available | Planned-gated |
| Krea 2 Turbo | `lightweight_regional` | diffusion_model | Available | Available | Available | Planned-gated |
| Krea 2 Turbo | `lightweight_regional` | GGUF | Available | Available | Available | Planned-gated |
| FLUX.2 Klein | `lightweight_regional` | diffusion_model | Available | Available | Available | Planned-gated |
| FLUX.2 Klein | `lightweight_regional` | GGUF | Available | Available | Available | Planned-gated |
| Z-Image | `lightweight_regional` | diffusion_model | Available | Available | Available | Planned-gated |
| Z-Image | `lightweight_regional` | GGUF | Available | Available | Available | Planned-gated |
| Z-Image Turbo | `lightweight_regional` | diffusion_model | Available | Available | Available | Planned-gated |
| Z-Image Turbo | `lightweight_regional` | GGUF | Available | Available | Available | Planned-gated |

Scene Director Outpaint is currently **planned-gated for both classic and modern engines**. For modern routes, planned-gated also means no graph mutation and no fallback to V054 or a generic repair pass.

## Lightweight regional prompt contract

Modern regional prompting is single-sampler conditioning, not crop-and-regenerate repair:

1. convert the Scene Director region to a mask;
2. optionally feather the mask;
3. encode the regional prompt with the provider-owned CLIP/text encoder;
4. apply `ConditioningSetMask` using `mask bounds`;
5. combine with the provider/base conditioning;
6. reuse the provider's existing sampler and latent path.

Family negative policy:

- Krea 2 RAW and Z-Image Base: regional negative conditioning is supported;
- Krea 2 Turbo, FLUX.2 Klein and Z-Image Turbo: the provider zero-negative policy is preserved.

FLUX.2 Klein regional positive lanes retain the provider's `FluxGuidance` semantics.

## Regional LoRA ownership

LoRA Stack remains the selection/asset owner. Scene Director owns spatial execution only for LoRA rows assigned to Scene Director regions.

```text
Global LoRA row
  → LoRA Stack normal provider/base-model patch

Region-targeted LoRA row
  → Scene Director
  → NeoRegionalLoRADelta
  → family-specific masked model-side activation delta
```

Modern regional LoRA rules:

- model side only;
- no regional CLIP mutation claim;
- cloned `ModelPatcher` / no global model weight mutation;
- at most one `NeoRegionalLoRADelta` wrapper per compiled modern graph;
- no route-count cap: 3+ regional LoRAs are allowed;
- no standard `LoraLoader`/`LoraLoaderModelOnly` fallback;
- no masked finish sampler fallback;
- unknown family/module/token layouts fail closed.

## Family adapters

### Krea 2 RAW / Turbo

Adapter: `krea2_activation_delta_v2`

Key rules:

- Krea-specific current Comfy LoRA mapping is preferred;
- RAW and Turbo use the same Krea family adapter;
- declared non-Krea LoRAs are rejected before trigger injection;
- text-fusion/text-MLP/timestep paths are excluded from regional spatial execution;
- GGUF uses activation hooks rather than quantized weight mutation;
- Turbo provider profile is preserved: 8 steps, Comfy CFG 1, zero-negative;
- RAW provider/user sampling values are preserved.

### FLUX.2 Klein

Adapter: `flux2_klein_activation_delta_v1`

Key rules:

- 4B and 9B are distinct LoRA architectures and cross-scale pairings are blocked;
- same-scale Base ↔ Distilled metadata crossing requires runtime preflight rather than automatic compatibility;
- double-stream image branches are image-only regional targets;
- single-stream `linear1`/`linear2` are combined text+image targets with the text slice forced to zero;
- Comfy partial QKV LoRA target slices are preserved;
- provider `FluxGuidance`, latent wrappers, and sampler stay authoritative.

### Z-Image / Z-Image Turbo

Adapter: `z_image_activation_delta_v1`

Runtime arming requires the Z-Image NextDiT signature used by the current implementation:

- dim 3840;
- in_channels 16;
- 30 heads;
- 30 main transformer layers;
- 2 noise refiner layers;
- 2 context refiner layers;
- patch size 2.

Key rules:

- context refiner, caption projection, timestep/AdaLN and norm-only paths are excluded;
- caption padding and image padding always receive a zero regional mask;
- unknown/omni sequence layouts fail closed;
- Base ↔ Turbo cross-variant LoRA metadata requires runtime preflight;
- `ModelSamplingAuraFlow` remains provider-owned;
- Turbo preserves Neo's current 9-KSampler-step / CFG-1 / zero-negative contract.

## Release lock

Schema: `neo.image.scene_director.release_lock.v1`

The SD-28.7 release lock is authoritative for modern compiled graphs. It blocks and restores the original provider graph if a mutation introduces any of the following:

- cross-family fallback;
- a new sampler node;
- standard LoRA-loader fallback for regional LoRA;
- `NeoSceneDirectorV054` on a modern route;
- more than one regional-LoRA wrapper;
- provider sampler parameter mutation;
- provider latent-input mutation;
- global model mutation;
- heavy SD repair/rescue behavior on a modern route.

A blocked lock returns the incoming provider graph. It does not attempt a second strategy.

## Inspector truth model

Inspector schema: `neo.image.scene_director.inspector.v2`

The Inspector intentionally separates:

- **Route** — whether the family/loader/mode is released;
- **Engine** — classic V054 or lightweight regional;
- **Regional Prompt** — active, gated or blocked;
- **Regional LoRA** — active, preflight, failed closed or absent;
- **GPU Proof** — pending or proven for the actual run;
- **Release Lock** — preflight, locked, gated safe or blocked.

A route can be supported and release-locked while GPU proof remains pending. Compile-time support metadata must never fabricate live spatial-isolation proof.

## Runtime proof fields

Modern regional-LoRA execution records the evidence required to distinguish a compiled route from an actually executed delta. Core fields include:

- `lora_loaded`
- `model_family_match`
- `region_mask_bound`
- `masked_delta_hook_active`
- `delta_eval_attempted`
- `delta_nonzero`
- `global_model_mutation`
- `sampler_count`
- `forward_hooks_removed`
- family/loader spatial-scope diagnostics such as `spatial_scope_filter_active`, `loader_supported`, and `token_mask_scope_proven` where available.

`runtime_gpu_proven=true` is a per-run statement. It is not equivalent to “final pixels outside the region can never differ.” Transformer layers can mix information after a masked delta, so visible leakage must still be measured with fixed-seed A/B tests when hard isolation matters.

## Fallback policy

Modern Scene Director is fail-closed:

```text
Unsupported/gated family or mode
  → no Scene Director mutation

Missing regional-LoRA runtime node
  → regional LoRA blocked
  → regional prompt may still run if its route remains valid

Incompatible LoRA
  → reject before owning-region trigger injection

Unknown token/module geometry
  → zero regional LoRA mask

Release-lock violation
  → restore original provider graph
```

There is no modern → classic V054 fallback.

## Apply order for patch-only development builds

The SD-28 patch packages were deliberately delivered as sequential deltas. Apply in order:

1. SD-28.1 — execution architecture;
2. SD-28.2 — lightweight regional prompting;
3. SD-28.3 — regional LoRA foundation;
4. SD-28.4 — Krea 2 RAW/Turbo;
5. SD-28.5 — FLUX.2 Klein;
6. SD-28.6 — Z-Image/Turbo;
7. SD-28.7 — Inspector + release lock;
8. SD-28.8 — documentation/system records;
9. SD-28.9 — extension-owned editor restore + route-aware readiness.

Do not apply a later patch onto the public GitHub baseline while skipping its predecessors unless a later consolidated release explicitly states that it is standalone.

## Validation status

The SD-28.7 runtime release suite completed with **114 passing tests** before the documentation phase. SD-28.9 changes the editor/manifest readiness surface only; it does not change the SD-28.7 workflow compiler, family adapters, sampler policy, or release-lock invariants.

For live backend qualification, use `scene_director_live_validation.md`.

## IP-8.1 live-UI wiring correction

Manual UI validation after the IP-8 sampling release lock found that Scene Director could be runtime-supported yet hidden by workspace/alias routing. Scene Director manifest 1.2.19 with IP-8.1 wiring lock now uses per-workflow physical mount slots, maps Img2Img/Inpaint/Outpaint to the Image `reference` workspace, and normalizes modern Safetensors/Components aliases to the canonical `diffusion_model` route. This is a visibility/routing correction only; the supported execution families are unchanged.

## IR-5 live route authority lock

IR-5 removes the old checkpoint-only browser route gate. Live Image UI route state now comes from `extension_manifest.json.route_states` plus `ui_route_authority`, parity-locked to `backend/execution_strategy.py`. Krea 2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo use `lightweight_regional` on diffusion_model/GGUF; SDXL/SD1.5 retain `classic_v054`. Unsupported families never fall back across engines. Default enablement and physical workspace mounting remain IR-6 work.
