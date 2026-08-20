# IMG-SD1 — Modern Scene Director Basic Routing + Submit Authority

**Status:** Current implementation — 2026-08-15  
**Surface:** Image → Generations / Reference → Scene Director  
**Backend:** ComfyUI / ComfyUI Portable

IMG-SD1 closes the gap between the modern Scene Director editor and the lightweight regional workflow compiler. The modern engine already supported masked regional prompting and regional LoRA model-delta execution, but the browser/provider submit path could drop Scene Director before compilation and the editor still exposed LoRA routing as a filename-style advanced control.

## UI contract

Scene Director keeps two internal UI modes based on the canonical execution engine:

```text
classic_v054
→ SDXL / SD1.5 checkpoint routes
→ full classic controls remain available

lightweight_regional
→ Krea 2 RAW / Turbo
→ FLUX.2 Klein
→ Z-Image / Z-Image Turbo
→ Basic regional editor only
```

For `lightweight_regional` routes the editor-mode selector is hidden and the editor is forced to Guided/Basic behavior. Advanced Region Control is not rendered. Modern models already own the main generation semantics; Scene Director adds spatial authority rather than rebuilding the V054 repair pipeline.

Each Basic region card owns:

- regional prompt;
- regional negative where the family supports it;
- region strength;
- mask feather;
- **Extension Routing**;
- LoRA Stack row selector;
- regional LoRA strength;
- ControlNet unit selector;
- ADetailer pass selector;
- IP Adapter unit/profile selector;
- mask routing mode.

Owner-extension selectors remain route/capability gated. IMG-SD1 specifically proves the regional LoRA execution path; selecting another owner extension does not bypass that extension's own support matrix.

## Regional LoRA ownership

The Scene Director editor no longer asks for a free-text LoRA filename. The selector is populated from the current LoRA Stack rows and stores the real row UID.

```text
LoRA Stack row
  uid = lora_lakmal
  name = Krea2/Lakmal/...

Scene Director Region 1
  extension_routes.lora_row_id = lora_lakmal
```

At submit time Neo preserves the LoRA Stack owner payload and rewrites the assigned row's `apply_to` target to the Scene Director region id. That removes the row from normal global/base LoRA execution while keeping its asset identity available to Scene Director.

Modern execution is therefore:

```text
provider model
    ↓
NeoRegionalLoRADelta
    ├─ LoRA row A → Region A mask
    └─ LoRA row B → Region B mask
    ↓
existing provider KSampler
```

There is no `LoraLoader` or `LoraLoaderModelOnly` fallback for region-targeted LoRAs.

## Submit-authority contract

Frontend authority snapshot:

```text
_neo_scene_director_submit_authority
schema = neo.image.scene_director.submit_authority.img_sd1.v1
```

It records:

- Scene Director enabled state;
- regional submit intent;
- route eligibility;
- whether the canonical Scene Director payload was emitted;
- active region ids;
- assigned LoRA Stack row ids;
- row-id → region-id mapping;
- whether the LoRA owner payload was emitted.

Before `NeoJob` creation the API fails closed when an enabled, route-eligible Scene Director request loses its canonical payload. If regional LoRA rows were assigned, the API also requires the same LoRA Stack owner rows to remain present.

This prevents:

```text
Scene Director UI enabled
→ payload silently dropped
→ plain Krea/Flux/Z graph queued
```

and prevents:

```text
regional LoRA assignment
→ owner row lost
→ accidental global/missing LoRA execution
```

## Non-checkpoint provider dispatch

`ComfyProvider._non_checkpoint_allowed_extension_ids()` now asks the Scene Director canonical support matrix whether the current generation/reference route is executable. It no longer silently strips `image.scene_director` from Krea/Klein/Z non-checkpoint jobs.

Scene Director route evaluation uses the generation/reference workspace context, not the Finish workspace used by High-Res/ADetailer dispatch.

## Provider execution proof

After graph mutation the provider writes:

```text
_neo_scene_director_execution = applied | blocked_before_queue | inactive
_neo_scene_director_execution_proof.schema_version
  = neo.image.scene_director.execution_proof.img_sd1.v1
```

The proof records:

- route and execution engine;
- Scene Director patch type;
- provider sampler node;
- region count;
- nodes added;
- `ConditioningSetMask` nodes;
- `ConditioningCombine` nodes;
- `NeoRegionalLoRADelta` node;
- regional prompt lane count;
- regional LoRA route count;
- regional LoRA compile status;
- row-id → region-id bindings;
- single-sampler preservation;
- contract result.

If regional LoRA routes are requested, graph execution is considered verified only when exactly one regional LoRA wrapper is armed with `armed_not_gpu_proven`. A missing `NeoRegionalLoRADelta` node or other failed regional-LoRA graph arm blocks the job before queueing even if regional prompt nodes were successfully built.

Runtime GPU proof remains a separate per-run concern and is not fabricated by compile-time metadata.

## Krea 2 Turbo reference contract

For the IMG-SD1 regression case:

```text
Krea 2 Turbo GGUF
896×1344
8 steps
CFG 1
2 character regions
2 Krea LoRA Stack rows
```

The final compiled graph must prove:

- one provider `KSampler` only;
- at least two `ConditioningSetMask` nodes;
- regional conditioning combined into the provider conditioning path;
- exactly one `NeoRegionalLoRADelta`;
- two regional LoRA routes;
- no global `LoraLoader` / `LoraLoaderModelOnly` for those assigned rows;
- row A bound only to Region A;
- row B bound only to Region B;
- Krea Turbo sampling values preserved.

## Classic SDXL contract

IMG-SD1 does not simplify or replace classic V054. SDXL checkpoint routes retain the full classic Scene Director controls and repair/identity pipeline. SD1.5 remains on the existing experimental classic route.

## Migration

Older extension-editor payloads that stored `region.lora.source` are accepted only as a migration hint. When possible the editor matches that stored name to a currently available LoRA Stack row and converts it to `extension_routes.lora_row_id`. The serialized current payload removes the old free-text source field.


## IMG-SD1A runtime correction — 2026-08-15

Physical Krea 2 Turbo GGUF validation found three follow-up gaps. See `scene_director_img_sd1a.md` for the current hotfix contract.

- lightweight `ConditioningSetMask` now uses `set_cond_area=default` rather than `mask bounds` for multidimensional Krea/Qwen-style image latents;
- Krea LoRA metadata sentinel `unknown` is treated as missing metadata and routed to runtime preflight rather than rejected as a declared incompatible family;
- the legacy/core Image Scene Director panel now follows the same Modern Basic-only UI contract as the extension-owned editor, with primary Extension Routing in the normal region card.
