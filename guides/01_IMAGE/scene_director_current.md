# Scene Director — Current Architecture and Support Contract

**Canonical current-state guide:** IMG-SD3 (Krea2 Regional Engine Integration; IMG-SD2 remains current for FLUX.2 Klein / Z-Image)  
**Runtime release lock:** SD-28.7  
**Surface:** Image → Generations → Scene Director  
**Backend:** ComfyUI / ComfyUI Portable

This document is the primary source of truth for the Scene Director modernization completed through IMG-SD3. Modern Scene Director remains LoRA-isolation-first. Krea 2 RAW/Turbo now delegate regional prompt + regional LoRA execution to the external `januspluto/ComfyUI-Krea2-Regional` runtime while Neo keeps the active native/GGUF loader, sampler, latent, inpaint and decode path. IMG-SD2 remains the current Neo-owned lightweight runtime for FLUX.2 Klein and Z-Image. IMG-SD1C/IMG-SD1D subject-authority behavior remains historical/explicit-opt-in compatibility logic and is disabled by default on modern routes. The numbered phase guides remain design and migration history; when a historical guide conflicts with this document, this document wins.


## Editor UI and readiness

SD-28.9 restores Scene Director as an **extension-owned editor**. The editor no longer depends on `neo_app/static/js/neo.js` for feature-specific hydration.

Readiness is capability-specific:

- classic SDXL/SD1.5 execution requires `NeoSceneDirectorV054`;
- modern Krea 2 RAW/Turbo requires the external `Krea2RegionalBuilder` + `Krea2ApplyRegional` nodes;
- FLUX.2 Klein/Z-Image regional prompting uses the built-in Comfy conditioning/mask nodes declared by the execution strategy;
- `NeoRegionalLoRADelta` is conditionally required only for compatible FLUX.2 Klein/Z-Image region-targeted LoRA routes, not Krea 2;
- missing conditional nodes never hide or unmount the editor. They gate only the dependent execution capability.

The Runtime Inspector renders into a dedicated child root and cannot replace the editor DOM.

### IMG-SD3 Krea2 Regional external engine

Krea 2 RAW/Turbo no longer use `NeoRegionalLoRADelta` as their default Scene Director regional-LoRA runtime. Scene Director translates its region boxes, optional local prompts and LoRA Stack row assignments into one `Krea2RegionalBuilder`, then routes the provider-active model and Builder conditioning through one `Krea2ApplyRegional`. Neo keeps the existing Krea native/GGUF loader, text encoder, VAE, sampler, latent and decode graph.

The external dependency is `januspluto/ComfyUI-Krea2-Regional`. Required live Comfy nodes are `Krea2RegionalBuilder` and `Krea2ApplyRegional`. Missing nodes fail closed before queue; Krea does not fall back to `NeoRegionalLoRADelta` or global LoRA loading.

Neo defaults Krea to Adaptive Masks = `refine boxes`, Exclusive Masks = On, Restrict Image Attention = Off, Layout in Base = `position hints`, and Region Lock Strength = `0.4`. These defaults come from the validated GGUF benchmark used for IMG-SD3. Restrict image attention remains optional because it produced a duplicated subject in that benchmark.

For native Inpaint, external conditioning is inserted upstream of `InpaintModelConditioning`; the sampler remains connected to the wrapper's positive/negative/latent outputs.

See `scene_director_img_sd3.md` for the current Krea-specific compiler and dependency contract.

### IMG-SD2 modern regional LoRA isolation core

For modern `lightweight_regional` routes, **regional LoRA isolation is the primary Scene Director purpose**. The provider/user global prompt stays untouched by default and the modern checkpoint remains responsible for subject count, scene composition, camera, lighting, relationships, and general semantic understanding. A regional prompt is optional local reinforcement; a region may be active with only an assigned LoRA Stack row.

Default modern execution no longer appends hidden subject-count/cast text to the global prompt and no longer appends one-subject contracts to regional prompts. The historical IMG-SD1C/IMG-SD1D structural contracts can execute only through the explicit `strict_cast_control` compatibility flag; the modern UI does not enable that flag. Native inpaint still preserves `InpaintModelConditioning` ownership and the provider sampler/latent path.

Krea 2 RAW/Turbo uses `krea2_activation_delta_v3_strict_isolation`. The runtime continues to apply region-masked model-side LoRA activation deltas, but it now suppresses direct LoRA writes to Krea attention **key/value** projections (`wk` / `wv`). Krea is a single-stream transformer, so region-local K/V changes are a direct cross-region broadcast risk: queries outside the owning region can attend to those changed keys/values. WQ/output/MLP/image-input/final projection targets can remain region-masked. This is a stricter isolation profile, not a claim of mathematically perfect identity isolation; live output A/B testing remains required.

IMG-SD2 also reports region-box overlap risk. Overlapping region masks are valid but reduce isolation confidence, so the proof records `none / low / medium / high` overlap risk instead of silently claiming perfect separation.

See `scene_director_img_sd2.md` for the exact compiler/runtime contract.

### IMG-SD1A physical-runtime hotfix

Current lightweight regional masking uses `ConditioningSetMask(set_cond_area=default)`. The mask itself remains the spatial authority; modern routes do not request ComfyUI's 2D `mask bounds` derivation because Krea/Qwen-style image latents can be multidimensional.

The core/legacy Image Scene Director panel now follows the same Basic-only modern contract as the extension-owned editor. Krea/Klein/Z routes do not expose Scene Mode or Advanced V054 controls, and primary Extension Routing is rendered directly in the normal region card.

Krea LoRA family sentinel values such as `unknown` are treated as missing metadata and enter runtime preflight. Explicitly declared non-Krea families remain rejected. Provider proof also verifies that every region-assigned LoRA row from submit authority survives into the final regional-LoRA route contract.

### IMG-SD1 modern Basic UI

For `lightweight_regional` routes, Scene Director is intentionally **Basic-only**. The editor-mode selector is hidden, Guided mode is enforced, and per-region Advanced Region Control is not rendered. Each region card includes **Extension Routing** directly in the normal authoring flow. Regional LoRA selection is bound to the actual LoRA Stack row UID rather than a duplicated/free-text model filename.

`classic_v054` routes retain their full classic controls. This keeps SDXL's repair/identity system intact while modern families use Scene Director only for extra spatial authority.

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

## Lightweight modern contract

Modern Scene Director is LoRA-isolation-first. Regional prompting remains optional single-sampler conditioning, not crop-and-regenerate repair:

1. convert the Scene Director region to a mask;
2. optionally feather the mask;
3. encode the regional prompt with the provider-owned CLIP/text encoder;
4. apply `ConditioningSetMask` using multidimensional-safe `default` area handling;
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
  ├─ Krea 2 → Krea2RegionalBuilder / Krea2ApplyRegional
  └─ FLUX.2 Klein / Z-Image → NeoRegionalLoRADelta
```

Modern regional LoRA rules:

- the region stores the **LoRA Stack row UID** as the owner identity;
- assigned rows are removed from global/base LoRA execution and preserved in the LoRA Stack owner payload for Scene Director;
- LoRA source-record trigger words are merged only into the owning region prompt when compatibility accepts the binding; trigger words remain local to that region;
- model side only;
- no regional CLIP mutation claim;
- cloned `ModelPatcher` / no global model weight mutation;
- Krea 2 uses zero `NeoRegionalLoRADelta` wrappers and exactly one external Builder/Apply pair; FLUX.2 Klein/Z-Image use at most one `NeoRegionalLoRADelta` wrapper;
- no route-count cap: 3+ regional LoRAs are allowed;
- no standard `LoraLoader`/`LoraLoaderModelOnly` fallback;
- no masked finish sampler fallback;
- unknown family/module/token layouts fail closed.

## Family adapters

### Krea 2 RAW / Turbo

Adapter: `krea2_regional_external` through `januspluto/ComfyUI-Krea2-Regional`.

Key rules:

- exactly one `Krea2RegionalBuilder` + one `Krea2ApplyRegional` on a mutated Krea graph;
- zero `NeoRegionalLoRADelta` nodes on Krea;
- Scene Director region boxes/LoRA row assignments are serialized into Builder `regions_data`;
- the provider/user global prompt is passed to `base_prompt` unchanged;
- the external Apply node receives the provider-active model already connected to the sampler, preserving upstream model wrappers;
- GGUF and native Krea loaders remain Neo-owned;
- Turbo provider profile is preserved: 8 steps, Comfy CFG 1, zero-negative;
- Adaptive Masks defaults to `refine boxes`; Exclusive Masks defaults On; Restrict Image Attention defaults Off;
- missing external nodes fail closed with no Krea fallback to Neo's legacy regional runtime.

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

## Safety behavior

Modern Scene Director fails closed when a regional workflow would require an unsafe fallback. It does not silently switch families, add an unrelated sampler, convert a region-targeted LoRA into a global LoRA, move a modern route through the classic engine, or replace the provider-owned sampler/latent path.

If the requested regional capability cannot be prepared safely, the dependent Scene Director action is reported as gated or blocked and the provider workflow remains authoritative.

## Inspector truth model

Inspector schema: `neo.image.scene_director.inspector.v2`

The Inspector intentionally separates:

- **Route** — whether the family/loader/mode is released;
- **Engine** — classic V054 or lightweight regional;
- **Regional Prompt** — active, gated or blocked;
- **Regional LoRA** — active, preflight, failed closed or absent;
- **GPU Proof** — pending or proven for the actual run;
- **Safety status** — ready, gated, or blocked.

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

Missing regional-LoRA runtime node while a regional LoRA is explicitly assigned
  → provider execution proof fails
  → generation is blocked before queue
  → no prompt-only partial execution is silently accepted

Missing regional-LoRA runtime node with no regional LoRA assignment
  → regional prompting may still run if its route remains valid

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

## Runtime readiness

Scene Director availability is checked against the selected ComfyUI profile. If a supported route is visible but its execution capability is blocked, use `scene_director_live_validation.md` (Scene Director Runtime Readiness and Troubleshooting) for installation, refresh, and dependency checks.

## IP-8.1 live-UI wiring correction

Manual UI validation after the IP-8 sampling release lock found that Scene Director could be runtime-supported yet hidden by workspace/alias routing. Scene Director manifest 1.2.19 with IP-8.1 wiring lock now uses per-workflow physical mount slots, maps Img2Img/Inpaint/Outpaint to the Image `reference` workspace, and normalizes modern Safetensors/Components aliases to the canonical `diffusion_model` route. This is a visibility/routing correction only; the supported execution families are unchanged.

## IR-5 live route authority lock

IR-5 removes the old checkpoint-only browser route gate. Live Image UI route state now comes from `extension_manifest.json.route_states` plus `ui_route_authority`, parity-locked to `backend/execution_strategy.py`. Krea 2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo use `lightweight_regional` on diffusion_model/GGUF; SDXL/SD1.5 retain `classic_v054`. Unsupported families never fall back across engines. Default enablement and physical workspace mounting remain IR-6 work.

## IMG-SD1 submit authority and provider proof

IMG-SD1 fixes the real provider path for non-checkpoint modern routes. `image.scene_director` is admitted only when the canonical Scene Director support matrix marks the generation/reference route executable; it is no longer silently stripped by the non-checkpoint provider filter.

Frontend state records Scene Director as `workflow_requested` / `pending_provider_compile` until the provider proves graph mutation. `_neo_scene_director_submit_authority` fails closed if the canonical Scene Director block or any assigned LoRA Stack owner rows disappear before `NeoJob` creation.

After compilation, `_neo_scene_director_execution_proof` records the final masked-conditioning and regional-LoRA graph evidence. When regional LoRA rows are requested, exactly one armed `NeoRegionalLoRADelta` wrapper is required; a missing/unarmed wrapper blocks queueing rather than degrading to a global LoRA or a prompt-only partial result.

See `scene_director_img_sd1.md` for the route details.

## IMG-SD1B bundled Comfy runtime deployment

Modern regional LoRA isolation requires the live Comfy custom node `NeoRegionalLoRADelta`. Neo ships the implementation under the built-in Scene Director extension and mirrors it in the root `neo_scene_director` installable package. If `/object_info` does not expose the runtime node, generation fails closed rather than globally applying region-assigned LoRAs.

Copy the bundled `neo_scene_director` folder from the Neo Studio root into `<ComfyUI-root>/custom_nodes/neo_scene_director`, then fully restart ComfyUI and refresh/Test the selected ComfyUI profile. The package supplies the Neo Scene Director runtime nodes used by supported classic and lightweight-regional routes.

## IMG-SD1C / IMG-SD1D historical subject-authority compatibility

IMG-SD1C introduced a compact cast/cardinality bridge and IMG-SD1D merged that bridge into the provider global CLIP text. IMG-SD2 supersedes that behavior as the modern default because modern checkpoints already understand scene composition well and hidden cast text can compete with native prompting.

The helper/compatibility code remains available only behind explicit `strict_cast_control=true` payload intent for regression and future experimentation. Current modern UI payloads submit `strict_cast_control=false`; normal modern generation therefore receives **no automatic global cast/count mutation and no automatic local one-subject contract**. Prompt-vs-mask direction checks remain diagnostic-only.


## IMG-SD2A submit-scope hotfix

IMG-SD2A fixes a frontend submit regression introduced by IMG-SD2 where `sceneDirectorPayloadPreview()` referenced `modernBasicOnly` after using the modern route predicate only as an inline expression. The payload builder now resolves `modernBasicOnly = sceneDirectorModernBasicOnly(route)` once at function scope and reuses that same authority for modern settings normalization and emitted payload fields. This is a frontend-only scope correction; the IMG-SD2 regional LoRA isolation runtime contract is unchanged.
