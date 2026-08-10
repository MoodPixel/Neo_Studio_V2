# LanPaint route-family architecture

Status: active Inpaint + Outpaint engine family  
Original architecture date: 2026-08-03  
Current masked-edit authority: 2026-08-07

> Current behavior: LanPaint is selectable for both **Inpaint** and **Outpaint** when the active family adapter and live Comfy capability snapshot are ready. Crop & Stitch is now an independent toggle: enabled uses the existing LanPaint crop/context/restore pipeline; disabled runs full-frame masked LanPaint sampling. Outpaint is created from Neo's core `ImagePadForOutpaint` canvas/mask and then handed to the same family-aware LanPaint graph. See `guides/01_IMAGE/inpaint_outpaint_engines.md`. Historical phase sections below are retained as implementation history, not current route limits.

> Phase 7 compatibility authority: the exact family/loader/mode adapter and `neo.image.family_compatibility.v1` decide whether LanPaint is selectable. Explicit Steps/CFG/Sampler/Scheduler/Denoise remain user-owned under Parameter Truth; historical sections that describe sampling values as family-locked are superseded.


## Phase 22.1 capability discovery and cache behavior

Neo no longer decides LanPaint readiness from a static node whitelist. The active family-adapter registry contributes every required loader, encoder, conditioning, transform, sampler, crop, latent, restore, and stitch node alias to the selected Comfy profile's safe `/object_info` snapshot.

This matters because installing LanPaint proves only the sampler pack. A family may additionally require nodes such as `CheckpointLoaderSimple`, dual/triple/quadruple CLIP loaders, `FluxGuidance`, `ModelSamplingFlux`, `ModelSamplingSD3`, Qwen Image Edit encoders, or `LanPaint_SamplerCustomAdvanced`. Neo now reports the exact missing class rather than calling every route failure “LanPaint not installed.”

Each Connect/Test snapshot records:

- adapter-registry fingerprint;
- discovery fingerprint;
- active LanPaint route keys;
- required, discovered, and missing node classes;
- the route capability matrix.

After a Neo update, an older browser/profile snapshot may not contain newly onboarded routes. Neo classifies that as **Refresh required** / `blocked_stale_capability_snapshot`. Restart Neo when needed, then use **Admin → Backends → Image → Connect/Test** to rebuild capabilities from live Comfy `/object_info`.

A genuinely missing node remains fail-closed. Ideogram 4, for example, requires `LanPaint_SamplerCustomAdvanced`; the presence of only `LanPaint_KSampler` does not make that route ready.

## Purpose

Neo Studio will treat LanPaint as a reusable Image inpaint engine family rather than as a Krea 2-only workflow.

Canonical future route-family identity:

```text
image.inpaint.lanpaint
```

The executable route key will be selected across these dimensions:

```text
provider + family + loader + mode + engine + variant
```

Example first target:

```text
ComfyUI Portable + Krea 2 Turbo + GGUF + Inpaint + LanPaint
```

Krea 2 is the first physical-validation family. It is not the architecture boundary.

## Phase 0 behavior lock

Phase 0 does not enable or replace any generation route.

Current production behavior remains unchanged:

- Krea 2 native/component inpaint uses the existing Krea 2 compiler with `SetLatentNoiseMask`, `DifferentialDiffusion`, and normal `KSampler`.
- Krea 2 GGUF inpaint uses the same existing masked-latent strategy with `UnetLoaderGGUF` and normal `KSampler`.
- Krea 2 LoRA Stack inpaint routes remain `implementation_target`.
- Qwen GGUF inpaint remains on its locked non-LanPaint route.
- Existing Qwen, Z-Image, Flux, SDXL, and other inpaint routes are not silently redirected through LanPaint.

A family may adopt LanPaint only through an explicit family policy, compiler mapping, capability check, regression set, and physical validation pass.

## Base logical pipeline

The future reusable LanPaint pipeline is framed as logical stages, not fixed model-family nodes:

1. Resolve source image and mask.
2. Crop a context region around the mask when the selected route uses cropped processing.
3. Resize the crop and mask to the family policy's processing size.
4. Refine the sampling mask.
5. Encode the source into the selected family's latent format.
6. Apply the latent noise mask.
7. Apply optional family/model transforms such as LoRA and differential diffusion.
8. Run the LanPaint sampler.
9. Decode the result.
10. Resize the generated crop back to its original crop dimensions.
11. Build the stitch mask.
12. Composite the result into the untouched source image.
13. Save through Neo's normal output, metadata, replay, and lineage path.

The base route owns stage order and state contracts. Family policies own the concrete loader, conditioning, negative-conditioning, sampler defaults, LoRA policy, VAE/latent requirements, and optional transforms.

## Submitted Krea 2 sample inventory

The submitted `Krea2_LanPaint_Inpaint_v1.json` contains 27 nodes, 42 links, and 6 groups.

Reusable route stages found in the sample include:

- `LoadImage`
- `CropByMask`
- `ImageResizeKJv2`
- `GrowMaskWithBlur`
- `VAEEncode`
- `SetLatentNoiseMask`
- `DifferentialDiffusionAdvanced`
- `LanPaint_KSampler`
- `VAEDecode`
- `ImageCompositeMasked`

Family-specific model/conditioning nodes include:

- `UNETLoader`
- `CLIPLoader(type=krea2)`
- `VAELoader`
- `CLIPTextEncode`
- `ConditioningZeroOut`

Authoring or inspection conveniences that do not belong in Neo's base compiler include:

- `Note`
- `PreviewImage`
- `Image Comparer (rgthree)`
- `MaskPreview+`
- `SAM3Segment`
- `Switch mask [Crystools]`
- `CR Upscale Image`

Neo already owns source upload, mask editing, preview, result comparison, output inspection, and independent finishing/upscaling. Those workflow-authoring nodes should not become required route dependencies.

## Sample values recorded for later implementation

The submitted sample uses:

```text
Crop padding: 152
Processing size: 768 × 768
Resize method: lanczos
Sampling mask: expand 45, blur 31
Stitch mask: expand 50, blur 9.1
Sampler steps: 8
CFG: 1
Sampler: euler
Scheduler: simple
Denoise: 1
LanPaint thinking steps: 10
Prompt mode: Image First
```

These values are baseline evidence, not universal defaults. Phase 3 family policies will decide which settings are shared, family-owned, or user-editable.

## Family overlay rule

A family overlay must declare:

- canonical family and variant aliases;
- supported loader branches;
- model loader role;
- text encoder role and type;
- VAE/AE role;
- positive and negative conditioning policy;
- sampler, scheduler, step, CFG, and denoise policy;
- supported LoRA injection strategy;
- required and optional node roles;
- crop/stitch policy;
- output-size preservation policy;
- capability and model-readiness checks;
- explicit conflicts with current route locks.

The first overlay will be Krea 2 Turbo GGUF. Native Krea 2, Krea 2 Base/RAW, Qwen, and Z-Image remain later family passes.

## Qwen protection lock

Current Qwen GGUF and Qwen Rapid inpaint routes explicitly use a non-LanPaint masked-latent path. The generic LanPaint route-family must not automatically capture those routes.

Qwen may be enabled later only when a dedicated Qwen LanPaint family overlay:

- preserves Qwen encoder and MMProj requirements;
- proves compatible sampler settings;
- does not borrow Flux or Krea defaults;
- passes the existing Qwen route-lock tests;
- passes physical image testing.

## LoRA boundary

LanPaint does not own LoRA state. Neo's canonical LoRA Stack remains the source of truth.

The base LanPaint compiler will expose an optional model-transform insertion point. Each family policy decides whether LoRA is:

- supported;
- model-only;
- model-and-CLIP;
- experimental;
- unsupported.

Krea 2 Turbo GGUF is the first planned model-only LoRA validation route. Phase 0 leaves its LoRA inpaint state as `implementation_target`.

## Audit command

Run from the Neo Studio repository root:

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase0.py
```

To inventory a workflow sample:

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase0.py --workflow "<WORKFLOW_JSON>"
```

The command emits:

```text
neo.image.lanpaint_route_family_phase0_audit.v1
```

It does not contact ComfyUI or run a GPU job.

## Phase 1 — Canonical route-family contract

Status: implemented as a contract-only authority; no execution route enabled.

Canonical schema:

```text
neo.image.lanpaint_route_family_contract.v1
```

Authority:

```text
neo_app.image.lanpaint_route_contract
```

Public JSON schema:

```text
neo_app/image/lanpaint_route_contract.schema.json
```

### Canonical identity

Every LanPaint route request normalizes these dimensions:

```text
provider_id
family
loader
mode = inpaint
engine = lanpaint
variant
```

Deterministic route key example:

```text
comfyui_portable:krea2_turbo:gguf:inpaint:lanpaint:default
```

Aliases such as `Comfy UI Portable`, `Krea-2-Turbo`, `GGUF UNET`, `inpainting`, and `Lan Paint` normalize to the same canonical identity.

### Base stage contract

The contract publishes family-neutral logical roles rather than Comfy node classes:

```text
source_image
mask_image
crop_context
processing_resize
sampling_mask_refine
latent_encode
latent_noise_mask
family_model_transform
lanpaint_sample
latent_decode
restore_crop_size
stitch_mask_refine
stitch_composite
output_handoff
```

Concrete nodes such as `UNETLoader`, `UnetLoaderGGUF`, `CLIPLoader`, `VAELoader`, `DifferentialDiffusionAdvanced`, and `LanPaint_KSampler` remain family/compiler decisions for later phases.

### Policy sections

The canonical contract separates:

- `crop_policy`
- `mask_policy`
- `latent_policy`
- `sampler_policy`
- `lora_policy`
- `stitch_policy`
- `capability_requirements`
- `family_policy`
- `execution`
- `validation`

The submitted Krea 2 values can be serialized as explicit request values, but they are not promoted to universal defaults. An empty route template leaves crop size, mask refinement, sampler settings, latent format, Differential Diffusion use, and LoRA strategy unresolved for the family policy.

### LoRA ownership

The contract keeps:

```text
stack_source = neo.image.lora_stack
injection_point = pre_sampler_model_transform
visible_prompt_mutation = false
```

Support and injection strategy remain `family_policy` until the family overlay explicitly declares `model_only`, `model_and_clip`, `experimental`, or `unsupported`.

### Asset-reference boundary

The contract accepts Neo-owned portable identities only:

```text
neo_asset_id
portable_name
output_id
job_id
```

Absolute local paths and external URLs are rejected. Runtime path resolution remains server-side and outside the portable route contract.

### Contract-only execution lock

Phase 1 always publishes:

```text
enabled = false
state = contract_only
selectable = false
compiler_id = null
workflow_type = null
execution_ready = false
```

The contract module and JSON schema may contain the `image.inpaint.lanpaint` identity. Production compilers, route matrices, and extension manifests may not enable it until the compiler and family-policy phases.

### Audit command

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase1.py
```

Machine-readable report:

```text
neo.image.lanpaint_route_family_phase1_audit.v1
```

## Phase 2 — Base workflow abstraction

Status: implemented as a family-neutral and compiler-neutral logical graph; no route enabled.

Canonical schema:

```text
neo.image.lanpaint_workflow_abstraction.v1
```

Authority:

```text
neo_app.image.lanpaint_workflow_abstraction
```

Public JSON schema:

```text
neo_app/image/lanpaint_workflow_abstraction.schema.json
```

### Logical graph

The base abstraction connects the Phase 1 stage roles as one acyclic graph:

```text
source image + mask
→ crop context
→ processing resize
→ sampling-mask refinement
→ latent encode
→ latent noise mask
→ family model-transform insertion point
→ LanPaint sample
→ latent decode
→ restore crop size
→ stitch-mask refinement
→ source-space composite
→ output handoff
```

The graph defines responsibilities, typed input/output ports, required edges, external family ports, invariants, and a deterministic fingerprint. It does not bind a Comfy node class.

### External family interface

The base graph receives these handles from later family policy/compiler phases:

```text
family_model
positive_conditioning
negative_conditioning
vae
sampler_settings
```

This keeps model loading, encoder selection, negative-conditioning behavior, VAE selection, and family sampler defaults outside the shared crop/mask/latent/stitch pipeline.

### Model-transform insertion point

The optional logical stage:

```text
family_model_transform
```

is the only pre-sampler insertion point for:

- Neo LoRA Stack model transforms;
- family-required model patching;
- optional differential-diffusion-style transforms;
- future compatible model-only adapters.

It must provide a type-preserving bypass when no transform is active. The base graph does not choose `model_only`, `model_and_clip`, or another strategy; family policy owns that decision.

### Source-space output lock

The abstraction requires:

- crop geometry to survive processing resize;
- the generated patch to be restored to crop dimensions;
- the stitch mask to return to source-space geometry;
- the untouched source image to remain the composite destination;
- the final handoff to preserve source output dimensions unless a later explicit output policy says otherwise.

### Concrete binding prohibition

Phase 2 bindings remain:

```text
state = unbound
provider_id = null
family_id = null
loader_id = null
compiler_id = null
node_class = null
```

Concrete classes such as model loaders, text encoders, VAE loaders, mask nodes, samplers, decoders, and compositors are added only by later family/provider compiler phases.

### Execution lock

Phase 2 publishes:

```text
enabled = false
selectable = false
state = abstraction_only
compiler_id = null
workflow_type = null
execution_ready = false
```

No extension manifest, route matrix, Image UI, or production compiler is activated.

### Audit command

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase2.py
```

Optional submitted-workflow inventory:

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase2.py --workflow "<WORKFLOW_JSON>"
```

Machine-readable report:

```text
neo.image.lanpaint_route_family_phase2_audit.v1
```

## Phase 3 — Family policy layer

Status: implemented as policy metadata and contract resolution only; no route is selectable or executable.

Canonical policy schema:

```text
neo.image.lanpaint_family_policy.v1
```

Registry schema:

```text
neo.image.lanpaint_family_policy_registry.v1
```

Authority:

```text
neo_app.image.lanpaint_family_policies
```

Public JSON schema:

```text
neo_app/image/lanpaint_family_policy.schema.json
```

### Family-policy responsibility

A LanPaint family policy resolves the model-family decisions intentionally excluded from the Phase 1 route contract and Phase 2 base graph:

```text
model loader
text encoder
VAE
positive conditioning
negative conditioning
family sampler defaults
LoRA compatibility and injection strategy
required node classes
required model roles
ordered model transforms
```

The base graph still owns source image, mask, crop, processing resize, latent/noise-mask flow, LanPaint sampling stage, decode, crop restoration, stitch-mask refinement, source-space composition, and output handoff.

### First complete policy — Krea 2 Turbo

Phase 3 contains one complete policy:

```text
policy_id = lanpaint.krea2_turbo.v1
family = krea2_turbo
providers = comfyui, comfyui_portable
loaders = gguf, diffusion_model
status = complete_policy
state = policy_only
```

The two loader branches are explicit:

```text
GGUF transformer
→ UnetLoaderGGUF or LoaderGGUF

Safetensors/components
→ UNETLoader
```

Both branches retain the same Krea 2 architecture contract:

```text
Qwen3-VL-4B native/safetensors text encoder
→ CLIPLoader(type=krea2)

Qwen Image VAE
→ VAELoader

Positive conditioning
→ CLIPTextEncode

Turbo negative conditioning
→ ConditioningZeroOut from positive conditioning
```

A GGUF Krea 2 transformer must not make the text encoder GGUF. `qwen3vl_4b_gguf`, MMProj sidecars, incompatible Qwen scales, and unrelated Qwen encoder families remain rejected by policy.

### Krea 2 Turbo route defaults

The first Krea 2 test policy adopts the submitted crop/stitch workflow values as Krea-family defaults, not as universal LanPaint defaults:

```text
crop padding = 152 px
processing size = 768 × 768
processing resize = lanczos
sampling mask expand = 45 px
sampling mask blur = 31
stitch mask expand = 50 px
stitch mask blur = 9.1
steps = 8
CFG = 1.0
sampler = euler
scheduler = simple
denoise = 1.0
LanPaint thinking steps = 10
prompt mode = image_first
```

An explicit route-contract value remains authoritative. For example, a requested crop padding of `224` or thinking-step value of `12` survives policy resolution while unresolved values are filled from the Krea 2 policy.

### Krea 2 Turbo LoRA policy

The shared Neo LoRA Stack remains the only canonical stack state. Phase 3 declares:

```text
support_state = experimental
injection_strategy = model_only
loader_node_class = LoraLoaderModelOnly
allow_multiple = true
clip_strength_supported = false
injection_point = pre_sampler_model_transform
visible_prompt_mutation = false
```

This declaration does not enable LoRA inpainting yet. The provider compiler and route matrix remain unchanged until the later LoRA/compiler phases.

### Krea 2 required node roles

The complete policy declares reusable workflow nodes from the submitted sample:

```text
CLIPLoader
CLIPTextEncode
ConditioningZeroOut
VAELoader
CropByMask
ImageResizeKJv2
GrowMaskWithBlur
VAEEncode
SetLatentNoiseMask
DifferentialDiffusionAdvanced
LanPaint_KSampler
VAEDecode
ImageCompositeMasked
```

Loader-specific requirements are evaluated separately:

```text
GGUF → UnetLoaderGGUF or LoaderGGUF
Safetensors/components → UNETLoader
```

`LoraLoaderModelOnly` is conditional and becomes required only when the canonical LoRA Stack has enabled rows.

Authoring conveniences remain excluded from the execution requirement set:

```text
SAM3Segment
Switch mask [Crystools]
MaskPreview+
Image Comparer (rgthree)
CR Upscale Image
PreviewImage
```

### Ordered model transforms

The Krea 2 policy declares one ordered pre-sampler transform pipeline:

```text
optional Neo LoRA Stack model-only chain
→ required DifferentialDiffusionAdvanced model transform
→ LanPaint sampler model input
```

The Phase 2 base graph remains unchanged. Phase 3 supplies transform semantics but does not bind or compile nodes.

### Unresolved family placeholders

The registry also contains non-executable placeholders for:

```text
krea2              (RAW/Base)
qwen_image
qwen_image_edit
z_image
z_image_base
z_image_turbo
```

Every placeholder publishes:

```text
status = unresolved_placeholder
inherits_defaults_from = null
requires_dedicated_family_policy = true
requires_physical_validation = true
```

Placeholder policies have no concrete loader mappings and no crop, mask, latent, sampler, stitch, or LoRA defaults. Qwen and Z-Image therefore cannot inherit Krea 2 Turbo behavior accidentally.

### Policy resolution behavior

`resolve_lanpaint_family_policy(...)` can now resolve a canonical Phase 1 route contract against the selected family policy.

For Krea 2 Turbo it returns:

```text
resolution_state = resolved_policy_only
family policy id/fingerprint attached
loader policy attached
family defaults filled where values were unresolved
required node/model roles attached
execution remains disabled
```

For a placeholder family it returns:

```text
resolution_state = unresolved_placeholder
no Krea defaults applied
execution remains disabled
```

### Execution lock

Phase 3 always retains:

```text
enabled = false
selectable = false
state = policy_only or contract_only
compiler_id = null
workflow_type = null
execution_ready = false
```

No route matrix, Image UI, extension manifest, Comfy workflow compiler, or production generation path is activated.

### Audit command

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase3.py
```

Machine-readable output:

```text
neo.image.lanpaint_route_family_phase3_audit.v1
```

## Phase 4 — Comfy provider/compiler integration

Status: implemented as a provider/compiler binding plan; graph emission, provider dispatch, UI exposure, and execution remain disabled.

Canonical authority:

```text
neo_app.providers.comfy_workflows.lanpaint
```

Compiler-plan schema:

```text
neo.image.lanpaint_comfy_compiler_plan.v1
```

Compiler identity:

```text
comfy.lanpaint.family_aware.v1
```

Workflow type:

```text
image.inpaint.lanpaint
```

### Compiler boundary

Phase 4 combines the earlier authorities without replacing them:

```text
Phase 1 canonical route contract
+ Phase 2 workflow abstraction
+ Phase 3 resolved family policy
+ selected Comfy profile capabilities
→ non-runnable Comfy compiler plan
```

The compiler plan binds logical roles and validates the selected backend, but deliberately does not allocate node ids or emit a `/prompt` payload.

Locked state:

```text
state = binding_only
graph_emitted = false
backend_prompt = null
provider_dispatch_registered = false
ui_route_registered = false
enabled = false
selectable = false
execution_ready = false
```

### Krea 2 Turbo external bindings

For the first complete family policy, Phase 4 resolves:

```text
GGUF diffusion transformer
→ UnetLoaderGGUF or LoaderGGUF

Safetensors/component diffusion model
→ UNETLoader

Qwen3-VL-4B text encoder
→ CLIPLoader(type=krea2)

Qwen Image VAE
→ VAELoader

Positive conditioning
→ CLIPTextEncode

Turbo negative conditioning
→ ConditioningZeroOut
```

The architecture-specific `krea2_clip_loader` capability must be proven by live object-info discovery. Detecting a generic `CLIPLoader` alone is insufficient.

### Base stage bindings

The family-neutral Phase 2 stages now map to provider-local nodes:

```text
source_image            → LoadImage
mask_image              → LoadImage → ImageToMask
crop_context            → CropByMask
processing_resize       → ImageResizeKJv2
sampling_mask_refine    → GrowMaskWithBlur
latent_encode           → VAEEncode
latent_noise_mask       → SetLatentNoiseMask
family_model_transform  → optional model transforms
lanpaint_sample         → LanPaint_KSampler
latent_decode           → VAEDecode
restore_crop_size       → ImageResizeKJv2
stitch_mask_refine      → GrowMaskWithBlur
stitch_composite        → ImageCompositeMasked
output_handoff          → PreviewImage provider sink
```

`PreviewImage` is treated as Neo's provider output handoff sink, not as a family custom-node dependency or a workflow-authoring feature.

### Model-transform binding

The Krea 2 transform plan is ordered as:

```text
optional repeated LoraLoaderModelOnly chain
→ required DifferentialDiffusionAdvanced
→ LanPaint_KSampler.model
```

When the canonical LoRA Stack is empty, the LoRA transform is bypassed. `DifferentialDiffusionAdvanced` remains required by the Krea 2 policy.

### Capability and signature checks

Comfy profile discovery now publishes a safe object-info slice for the LanPaint compiler. Phase 4 validates:

- backend reachability;
- `/object_info` availability;
- loader-role selection;
- `CLIPLoader(type=krea2)` support;
- VAE loader availability;
- required base/custom nodes;
- critical input signatures;
- conditional LoRA loader availability;
- ambiguous loader alternatives.

Missing nodes fail closed and identify relevant custom-node packs where known:

```text
LanPaint_KSampler                → LanPaint
CropByMask                       → comfyui-inpainteasy
ImageResizeKJv2 / GrowMaskWithBlur → ComfyUI-KJNodes
UnetLoaderGGUF / LoaderGGUF      → ComfyUI-GGUF
```

The compiler must not silently replace `LanPaint_KSampler` with normal `KSampler`, or guess between multiple GGUF loaders when live role discovery has not selected one.

### Future-family protection

Incomplete family policies receive:

```text
stage_bindings = []
external_bindings = {}
family_policy_not_complete blocker
```

Qwen, Z-Image, Krea 2 RAW/Base, and other future families therefore receive no Krea 2 node, conditioning, sampler, LoRA, crop, mask, or VAE bindings.

### Audit command

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase4.py
```

Machine-readable output:

```text
neo.image.lanpaint_route_family_phase4_audit.v1
```

Phase 5 is responsible for the first real graph emitter and must begin with Krea 2 Turbo GGUF only. It must not activate safetensors or other family routes merely because Phase 4 can resolve their binding templates.

## Phase 5 — Krea 2 Turbo GGUF runnable route

Phase 5 activates one route only:

```text
provider: comfyui or comfyui_portable
family: krea2_turbo
loader: gguf
mode: inpaint
engine: lanpaint
variant: crop_stitch_v1
compiler: comfy.lanpaint.family_aware.v1
```

### Selecting the engine

On the eligible route, Image Params shows:

```text
Inpaint Engine
- Native Inpaint
- LanPaint
```

Native is the default and preserves the existing Krea 2 inpaint compiler. LanPaint is serialized only when the route is eligible. Switching to Forge Neo, another family, another loader, or another mode forces the payload engine back to `native`.

### Runtime graph

The first emitter follows the submitted workflow rather than a generic replacement:

```text
LoadImage(source + mask)
→ ImageToMask
→ optional InvertMask
→ CropByMask
→ ImageResizeKJv2(image + mask)
→ GrowMaskWithBlur(sample mask)
→ VAEEncode
→ SetLatentNoiseMask
→ Krea 2 Turbo GGUF loader
→ CLIPLoader(type=krea2)
→ CLIPTextEncode / ConditioningZeroOut
→ DifferentialDiffusionAdvanced
→ LanPaint_KSampler
→ VAEDecode
→ ImageResizeKJv2(restore crop width/height)
→ GrowMaskWithBlur(stitch mask)
→ ImageCompositeMasked(x/y from CropByMask)
→ PreviewImage
```

Critical ports are locked by the deterministic fixture and Phase 5 tests. In particular, `ImageResizeKJv2` mask output `3`, Differential outputs `0/1`, and CropByMask geometry outputs `2–5` must not be guessed or reordered.

### Capability requirements

The selected Comfy profile must expose compatible current signatures for every serialized node input. Missing or stale nodes block prompt emission. The compiler never substitutes normal `KSampler` or another inpaint family.

The route also requires explicit, compatible selections for:

- Krea 2 Turbo GGUF diffusion transformer;
- native Qwen3-VL-4B text encoder through `CLIPLoader(type=krea2)`;
- Qwen Image VAE.

### Extension status

Phase 5 established the stable no-LoRA base graph and isolated it from standard-KSampler extension patchers. Phase 6 now enables the shared LoRA Stack only through an engine-specific model-only patch profile. Other graph extension families remain excluded.

### Replay metadata

The compiled job records the route key, engine, family, loader, variant, graph state, fingerprints, selected assets, all crop/mask/stitch/sampler controls, and the logical role for each emitted node.

### Deterministic workflow fixture

```text
tests/fixtures/lanpaint/krea2_turbo_gguf_crop_stitch_v1.json
```

This sanitized fixture replaces the missing original workflow JSON as the regression authority for node order, submitted defaults, and critical port wiring. It contains asset roles only and no personal filesystem path.

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase5.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase5_krea2_turbo_gguf.py
```

These checks validate compilation and fail-closed behavior. They do not replace physical execution testing. Before rollout, queue one real masked-region job on the intended ComfyUI/Portable profile and verify model loading, mask behavior, crop restoration, stitch alignment, and source-size output.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE5_KREA2_TURBO_GGUF_20260804.md
```

## Phase 6 — LanPaint LoRA Stack enablement

Phase 6 enables the existing Assets-owned LoRA Stack for one route only:

```text
comfyui or comfyui_portable
krea2_turbo
gguf
inpaint
engine=lanpaint
state=experimental_available
```

Native Krea 2 inpaint remains a separate gated LoRA route. The frontend and backend route identity now include `engine`; non-native engines must match their own exact support key and may not inherit a legacy/native declaration.

### Model-only chain

Krea 2 LanPaint uses `LoraLoaderModelOnly`:

```text
Krea 2 Turbo GGUF loader
→ ordered LoraLoaderModelOnly chain
→ DifferentialDiffusionAdvanced.model
→ LanPaint_KSampler.model from Differential output
```

The LoRA patcher never modifies `CLIPLoader(type=krea2)`, positive conditioning, or zeroed negative conditioning. `strength_clip` is not serialized on this route.

No active global/base rows means no LoRA nodes and the exact Phase 5 graph. Only active global rows targeting `base` or `both` enter the base chain. Disabled, regional, and finish-only rows remain stored for their owning systems, are reported as deferred, and do not make the route require `LoraLoaderModelOnly`.

### Fail-closed validation

Before queue submission Neo requires:

- `LoraLoaderModelOnly` in the selected Comfy profile;
- live inputs `model`, `lora_name`, and `strength_model`;
- every selected active LoRA in the live catalog when the node advertises choices;
- a valid compiler-owned engine-specific patch profile;
- successful model-consumer rewiring.

A missing node, stale signature, missing LoRA asset, or unapplied requested stack changes the compile result to `mock_compiled`. Neo does not fall back to Native Inpaint, standard KSampler, another family, or another provider.

### UI and replay

The shared LoRA panel displays `LoRA mode: Model-only` on LanPaint. Its payload and replay metadata retain:

```text
engine
route key
stack order
LoRA node ids
base model ref
final LoRA model ref
patched Differential node
clip_patched=false
```

The stack remains saved when the user switches routes, but route execution eligibility is recalculated.

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase6.py
PYTHONPATH=. python -m pytest -q tests/test_lanpaint_route_family_phase6_lora_stack.py
```

Physical Comfy validation is still required before promotion from `experimental_available`. Compare no-LoRA, one-LoRA, multi-LoRA, and reversed-order runs using the same source, mask, prompt, seed, and LanPaint controls.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE6_LORA_STACK_20260804.md
```


## Phase 7 — Inpaint UI and state-model integration

Phase 7 replaces the Phase 5 engine-only selector experience with a generic route-aware LanPaint control card while keeping the route-family architecture authoritative.

The panel is active only for:

```text
provider: comfyui or comfyui_portable
family: krea2_turbo
loader: gguf
mode: inpaint
engine: lanpaint
state: experimental_available
```

The UI is not owned by Krea 2. Krea 2 Turbo is the first family policy rendered through the generic contract. Future family onboarding should add a family UI policy and compiler policy rather than create a separate panel.

### Portable UI state

The backend-neutral state authority is:

```text
schema: neo.image.lanpaint_ui_state.v1
authority: neo_app.image.lanpaint_ui_state
phase state: route_aware_ui_state_enabled
```

The state records:

- provider, family, loader, mode, requested engine, effective engine, route key, and route state;
- provider/family/loader/engine/status/LoRA badges;
- requested controls and normalized/resolved controls;
- the family-policy source for each value;
- validation errors and warnings;
- a deterministic state fingerprint.

Family-policy defaults remain authoritative. Explicit user values are normalized through the canonical LanPaint route contract before the provider compiler receives them.

### Visible control groups

The active LanPaint panel exposes only controls owned by the approved crop/stitch workflow:

```text
Crop and processing
- crop padding
- process width and height
- process resize method

Sampling mask
- expansion
- blur radius

LanPaint sampler
- thinking steps
- prompt mode
- denoise
- family-locked steps, CFG, sampler, and scheduler summary

Stitch
- stitch-mask expansion
- stitch-mask blur
- restore resize method
- source-dimension preservation
```

Sampler steps, CFG, sampler name, and scheduler remain family-locked in this phase. They are displayed for transparency rather than exposed as free controls.

### Route switching and persistence

LanPaint control values remain saved when the user changes provider, family, loader, or mode. If the selected route is no longer eligible, Neo changes only the active execution engine to Native. It does not destroy the saved LanPaint tuning.

This lock prevents stale LanPaint execution from leaking into Forge, Native Inpaint, Qwen, Z-Image, safetensors, or another unsupported route while still allowing the user to return to the prior configuration.

UI presets store the same draft state. Execution payloads include both:

```text
lanpaint_ui_state        # portable nested contract
lanpaint_* flat fields   # Phase 5/6 provider compatibility bridge
```

The provider normalizes the nested state again at the backend boundary; DOM values are not trusted as execution authority.

### LoRA summary

The Phase 7 card reads the shared Assets-owned LoRA Stack and displays:

```text
LoRA mode: Model-only
active global/base row count
regional and finish-only rows deferred
```

The panel does not own, duplicate, reorder, or silently activate LoRA rows. Phase 6 remains the LoRA graph authority.

### Replay and lineage

Compiled `actual_params` now include:

```text
lanpaint_ui_state
lanpaint_ui_state_fingerprint
_neo_lanpaint_phase7_ui_state=route_aware_ui_state_enabled
```

This retains requested values, resolved values, route identity, family-policy identity, warnings, active/inactive state, and the deterministic control fingerprint required for replay and audit.

### Static asset revision

Both the HTML cache query and the JavaScript runtime revision are advanced to:

```text
lanpaint_ui_state_20260804
```

This prevents a browser from loading the Phase 7 HTML with stale Phase 6 JavaScript or CSS.

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase7.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase7_ui_state.py
node --check neo_app/static/js/neo.js
```

Static state, compiler, UI source, schema, responsive CSS, cache revision, replay metadata, and route-leakage checks pass. A browser interaction pass and a live Comfy masked-region generation remain required before promotion from `experimental_available`.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE7_INPAINT_UI_STATE_20260804.md
```

## Phase 8 — Capability detection, gating, and diagnostics

Phase 8 moves LanPaint availability from static frontend route matching to one backend-neutral readiness report:

```text
schema: neo.image.lanpaint_route_capabilities.v1
authority: neo_app.image.lanpaint_capabilities
phase state: capability_gating_and_diagnostics
```

The exact supported overlay remains:

```text
ComfyUI / ComfyUI Portable
+ Krea 2 Turbo
+ GGUF
+ Inpaint
+ LanPaint
```

### Route status vocabulary

```text
available                 # physically validated profile
experimental_available    # complete live profile; physical validation still pending
blocked_missing_nodes     # backend/object_info, node pack, signature, or loader-role blocker
blocked_missing_models    # transformer, encoder, VAE, or selected-asset blocker
unsupported               # route identity is outside the implemented overlay
```

`experimental_available` is selectable and executable. `available` is reserved for a route that has passed the required physical browser and generation checks.

### Required live capability proof

Before LanPaint can be selected, the connected backend profile must advertise:

- a reachable `/object_info` snapshot;
- Comfy core image, mask, conditioning, VAE, latent, composite, and preview nodes;
- `UnetLoaderGGUF` or `LoaderGGUF` from ComfyUI-GGUF;
- `CropByMask` from ComfyUI-InpaintEasy;
- `ImageResizeKJv2`, `GrowMaskWithBlur`, and `DifferentialDiffusionAdvanced` from ComfyUI-KJNodes;
- `LanPaint_KSampler` from LanPaint;
- execution-critical input signatures for every selected node;
- `CLIPLoader` with the discovered Krea 2 loader role, including `type=krea2` support;
- a compatible Krea 2 Turbo GGUF transformer;
- a Qwen3-VL-4B Krea 2 text encoder;
- a Qwen Image VAE.

The diagnostics contract separates missing nodes/signatures from missing model assets. Missing custom nodes are grouped by installable pack and include remediation instructions.

### Conditional LoRA requirement

`LoraLoaderModelOnly` is optional for a no-LoRA LanPaint run. Its absence produces a warning rather than blocking the base route.

When at least one active global/base LoRA row requires model-only injection, the same node and signature become mandatory. Regional and finish-only deferred rows do not trigger this requirement.

### Frontend gating

The frontend may display the LanPaint engine option on the exact contract route, but it disables that option until the selected backend capability report is selectable. A previously selected LanPaint engine is forced back to Native execution when the refreshed profile becomes blocked, while the saved Phase 7 control values remain intact.

The diagnostics card shows:

- route status and readiness;
- concrete blockers and warnings;
- missing node packs;
- model/encoder/VAE checks;
- model-only LoRA readiness;
- remediation steps;
- capability fingerprint.

### Compiler and replay boundary

The LanPaint compiler independently re-evaluates the selected profile and assets before graph emission. A blocked report returns an empty prompt and never falls back to Native Inpaint, normal KSampler, another family, or another provider.

Compiled metadata records:

```text
lanpaint_capability_report
lanpaint_capability_fingerprint
_neo_lanpaint_phase8_capability_state=capability_gating_and_diagnostics
```

This captures the exact readiness evidence used for the run and allows replay/audit to distinguish a historically runnable profile from the current backend state.

### Static asset revision

Phase 8 advances the active browser revision to:

```text
lanpaint_capabilities_20260804
```

The Phase 7 cache identity remains accepted by historical audits while HTML loads the Phase 8 JavaScript and CSS revision.

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase8.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase8_capabilities.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase0_baseline.py tests/test_lanpaint_route_family_phase1_contract.py tests/test_lanpaint_route_family_phase2_workflow_abstraction.py tests/test_lanpaint_route_family_phase3_family_policies.py tests/test_lanpaint_route_family_phase4_comfy_compiler.py tests/test_lanpaint_route_family_phase5_krea2_turbo_gguf.py tests/test_lanpaint_route_family_phase6_lora_stack.py tests/test_lanpaint_route_family_phase7_ui_state.py tests/test_lanpaint_route_family_phase8_capabilities.py
node --check neo_app/static/js/neo.js
```

Static capability, provider discovery, compiler gating, frontend diagnostics, replay lineage, cache revision, and path-hygiene checks pass. A live backend-profile refresh and physical Krea 2 masked-region generation remain required before promotion from `experimental_available` to `available`.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE8_CAPABILITY_GATING_20260804.md
```

## Phase 9 — Family expansion scaffolding

Phase 9 adds a declarative onboarding layer for future LanPaint family/loader overlays:

```text
schema: neo.image.lanpaint_family_expansion_profile.v1
registry: neo.image.lanpaint_family_expansion_registry.v1
authority: neo_app.image.lanpaint_family_expansion
phase state: family_expansion_scaffold_only
execution state: scaffold_only
```

The data authority is:

```text
neo_app/image/lanpaint_family_expansion_profiles.json
```

It defines one profile for each planned family/loader candidate and records:

- canonical provider/family/loader/inpaint/LanPaint route keys;
- matching Phase 3 family policy state;
- model-loader candidates and input keys;
- text/positive/negative conditioning policy;
- LoRA strategy and loader class;
- family-specific model roles;
- candidate node requirements;
- unresolved Phase 10 work;
- automated, compiler, capability, and physical test state.

### Runtime lock

Phase 9 activates nothing. Every scaffold publishes:

```text
enabled=false
selectable=false
executable=false
compiler_id=null
route_status=unsupported
```

The only runnable LanPaint overlay remains Krea 2 Turbo GGUF.

### Expansion matrix

The registry covers:

- Krea 2 Turbo safetensors;
- Krea 2 RAW/Base safetensors and GGUF;
- Qwen Image safetensors and GGUF;
- Qwen Image Edit 2509 safetensors and GGUF;
- Z-Image safetensors and GGUF;
- Z-Image Base identity scaffolds;
- Z-Image Turbo safetensors and GGUF.

Only Krea 2 Turbo safetensors is marked `ready_for_phase10`, because it can reuse the complete Krea 2 Turbo family policy. It still requires a compiler binding, capability profile, parity tests, and physical masked-region validation.

All other profiles remain blocked by an incomplete family policy or unresolved family identity. They do not inherit Krea 2 Turbo defaults.

### Family-specific boundaries

- Krea 2 RAW keeps normal negative-prompt encoding and known 52-step / CFG 3.5 non-LanPaint defaults; LanPaint defaults remain unresolved.
- Qwen Image keeps `clip_type=qwen_image`, AuraFlow sampling, Qwen Image latent/VAE semantics, and model+CLIP LoRA candidate behavior.
- Qwen Image Edit 2509 requires its own edit/source-conditioning policy and live node-variant resolution.
- Z-Image keeps `clip_type=lumina2`, AuraFlow sampling, AE/VAE semantics, and model+CLIP LoRA candidate behavior.
- Z-Image Base must first resolve whether it is a distinct public family id or an alias of the existing base `z_image` route.

### Diagnostics

Known scaffolded routes still return `unsupported`, but Phase 8 capability reports now include:

```text
expansion_scaffold.profile_id
expansion_scaffold.onboarding_state
expansion_scaffold.policy_status
expansion_scaffold.required_work
expansion_scaffold.profile_fingerprint
```

The blocker is `scaffolded_route_not_activated`. Unknown routes retain the generic `unsupported_route` blocker.

Comfy provider discovery publishes a compact `lanpaint_family_expansion` compatibility matrix in online and offline profile snapshots. This matrix is diagnostic only and is not execution authority.

### Phase 10 onboarding rule

A new family route should require only:

1. complete/promote its family policy;
2. bind loader, conditioning, VAE, and model-transform roles;
3. add route-specific capability/model checks;
4. register one explicit compiler route;
5. expose it only after automated and physical validation.

Do not redesign the base route, crop/restore/stitch pipeline, shared LoRA system, or provider contract for each family.

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase9.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase9_expansion_scaffolding.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase0_baseline.py tests/test_lanpaint_route_family_phase1_contract.py tests/test_lanpaint_route_family_phase2_workflow_abstraction.py tests/test_lanpaint_route_family_phase3_family_policies.py tests/test_lanpaint_route_family_phase4_comfy_compiler.py tests/test_lanpaint_route_family_phase5_krea2_turbo_gguf.py tests/test_lanpaint_route_family_phase6_lora_stack.py tests/test_lanpaint_route_family_phase7_ui_state.py tests/test_lanpaint_route_family_phase8_capabilities.py tests/test_lanpaint_route_family_phase9_expansion_scaffolding.py
```

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE9_FAMILY_EXPANSION_SCAFFOLDING_20260804.md
```

## Phase 10 — Qwen and Z-Image onboarding

Phase 10 promotes six exact Phase 9 scaffolds into family-aware, capability-gated LanPaint routes:

```text
qwen_image:diffusion_model:inpaint:lanpaint
qwen_image:gguf:inpaint:lanpaint
z_image:diffusion_model:inpaint:lanpaint
z_image:gguf:inpaint:lanpaint
z_image_turbo:diffusion_model:inpaint:lanpaint
z_image_turbo:gguf:inpaint:lanpaint
```

All six routes remain `experimental_available` until physical masked-region validation succeeds on a real target ComfyUI profile.

### Family-aware compiler

The execution authority is:

```text
neo_app.providers.comfy_workflows.lanpaint_family
compiler id: comfy.lanpaint.family_aware.v1
phase state: qwen_zimage_onboarding
```

The compiler delegates the existing Krea 2 Turbo GGUF route to the Phase 5 compiler unchanged. Qwen and Z-Image use the shared source/mask/crop/resize/noise-mask/LanPaint/decode/restore/stitch pipeline while family policies own:

- native or GGUF model loader;
- CLIP loader and required architecture type;
- text encoder and VAE/AE roles;
- positive and negative conditioning behavior;
- model-sampling transform;
- sampler defaults;
- LoRA patch mode;
- capability requirements and replay metadata.

### Qwen Image

Qwen Image uses:

```text
clip type: qwen_image
model transform: ModelSamplingAuraFlow shift 3.1
steps: 20
cfg: 4.0
negative conditioning: encoded user negative prompt
LoRA: model + CLIP through LoraLoader
```

Both `diffusion_model` and `gguf` routes are supported. GGUF quantizes the diffusion model while the text encoder remains a separately discovered Qwen Image encoder.

### Z-Image

The existing `z_image` family is the public non-Turbo/base identity in Neo. It uses:

```text
clip type: lumina2
model transform: ModelSamplingAuraFlow shift 3.0
steps: 35
cfg: 3.5
negative conditioning: encoded user negative prompt
LoRA: model + CLIP through LoraLoader
```

A separate `z_image_base` route remains blocked to avoid creating a duplicate public family identity.

### Z-Image Turbo

Z-Image Turbo uses:

```text
clip type: lumina2
model transform: ModelSamplingAuraFlow shift 3.0
steps: 9
cfg: 1.0
negative conditioning: ConditioningZeroOut
LoRA: model + CLIP through LoraLoader
```

Turbo does not inherit the non-Turbo negative-prompt or sampling defaults.

### Graph boundary

Qwen and Z-Image graphs use:

```text
family model loader
→ optional shared model+CLIP LoRA chain
→ ModelSamplingAuraFlow
→ LanPaint_KSampler
```

They do **not** contain Krea 2's `DifferentialDiffusionAdvanced` stage. The Krea graph remains:

```text
GGUF loader
→ optional model-only LoRA chain
→ DifferentialDiffusionAdvanced
→ LanPaint_KSampler
```

### Capability gating

Phase 8 diagnostics are now family-aware. A Qwen/Z route requires:

- exact loader branch (`UNETLoader` or `UnetLoaderGGUF`);
- exact CLIP branch (`CLIPLoader` or `CLIPLoaderGGUF`);
- required CLIP type (`qwen_image` or `lumina2`);
- `ModelSamplingAuraFlow(model, shift)`;
- family transformer, text encoder and VAE/AE assets;
- base crop/resize/mask/LanPaint/stitch node signatures;
- `LoraLoader` only when an active global/base model+CLIP LoRA needs it.

Missing or stale capabilities fail closed before queue submission and never fall back to Krea, Native Inpaint, standard KSampler, another family or another provider.

### Routes intentionally still blocked

- `qwen_image_edit_2509`: requires a dedicated source/edit-conditioning contract and may not borrow plain Qwen Image.
- `z_image_base`: unresolved duplicate identity; current `z_image` is the active non-Turbo/base family.
- Krea 2 RAW and Krea 2 Turbo safetensors: outside this Phase 10 onboarding slice.

### Replay and metadata

Compiled jobs record:

```text
_neo_lanpaint_phase10_state
lanpaint_family_policy
lanpaint_capability_report
lanpaint_route_key
lanpaint_model_transform
lanpaint_lora_mode=model_and_clip
lanpaint_node_roles
```

Replay must preserve the exact family, loader, encoder/VAE assets, AuraFlow shift, negative-conditioning mode, LoRA order and Phase 7 crop/mask/stitch state.

### Static asset revision

```text
lanpaint_qwen_zimage_20260804
```

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase10.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase10_qwen_zimage.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py
node --check neo_app/static/js/neo.js
```

Static/compiler validation is complete. Physical ComfyUI validation remains mandatory before any route is promoted from `experimental_available` to `available`.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE10_QWEN_ZIMAGE_ONBOARDING_20260804.md
```

## Phase 11 — Replay, lineage, and audit support

Phase 11 adds one portable replay envelope for every compiled LanPaint job:

```text
neo.image.lanpaint_replay.v1
```

Authority:

```text
neo_app.image.lanpaint_replay
```

The envelope is family-neutral and applies to Krea 2, Qwen Image, Z-Image, and Z-Image Turbo LanPaint routes.

### Exact-route replay

Replay retains the exact recorded:

- provider/profile family;
- family and loader;
- `mode=inpaint`;
- `engine=lanpaint`;
- route key, variant, policy, compiler, and graph state;
- resolved crop, processing, sample-mask, sampler, thinking, denoise, and stitch controls;
- UI-state, capability, contract, and compile-plan fingerprints;
- selected transformer, encoder, VAE/AE, and LoRA references.

Replay never silently changes family, loader, engine, or provider route. A route mismatch or replay-fingerprint mismatch fails closed before graph submission.

### Portable source and mask assets

Provider upload aliases such as temporary Comfy input names are not portable replay inputs. Phase 11 binds replay to Neo-owned output-record assets:

```text
source.input_assets[]
├─ role=source
└─ role=mask
```

The replay contract records safe Neo references such as asset id, filename, Neo path, and Neo URL. It deliberately excludes:

- image bytes;
- backend upload aliases;
- absolute provider roots;
- personal machine paths.

If the recorded source or mask no longer has a portable Neo reference, reconstruction becomes:

```text
blocked_missing_portable_assets
```

The job cannot auto-run until the missing input is supplied again.

### LoRA replay safety

LoRA replay preserves:

- family-specific LoRA mode;
- ordered requested rows;
- base-graph and deferred rows;
- model and CLIP strengths;
- base model/CLIP references;
- final patched model/CLIP references;
- emitted LoRA node order.

Saved rows restore **disabled** and require live revalidation of the exact route, loader node, and current LoRA catalog. Replay never silently executes a stale or missing LoRA.

### Workflow and output lineage

The replay envelope records the logical node-role map, backend class types when available, sampler node, LoRA lineage, output lineage, and deterministic replay fingerprint.

The same contract is attached to:

- compiled `actual_params`;
- route metadata and route snapshots;
- persisted output records;
- replay metadata and replay payloads;
- Results reuse payloads;
- Output Inspector audit summaries.

After the shared LoRA patcher changes the graph, `ComfyProvider` refreshes the contract so final LoRA references—not pre-patch references—become authoritative.

### Frontend reconstruction

Loading a LanPaint replay draft:

1. restores the exact supported family, loader, mode, and LanPaint engine;
2. restores the saved LanPaint controls;
3. restores source and mask fields from Neo-owned input assets;
4. restores LoRA rows disabled;
5. records a reconstruction report;
6. requires Phase 8 capability and selected-asset revalidation;
7. never auto-runs.

The Output Inspector includes a **LanPaint Replay Audit** panel showing route identity, fingerprint, portable input state, reconstruction state, and revalidation requirements.

### Static asset revision

```text
lanpaint_replay_lineage_20260804
```

### Validation

```text
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase11.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase11_replay_lineage.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py
node --check neo_app/static/js/neo.js
```

Static, schema, replay, lineage, and regression validation is complete. Physical replay validation still requires a persisted real generation on the target Neo/ComfyUI installation.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE11_REPLAY_LINEAGE_AUDIT_20260804.md
```

## Post-Phase 11 hotfix — selected-profile capability transport

This hotfix repairs a profile/runtime handoff gap discovered during physical UI testing.

### User-visible symptom

A ComfyUI profile could connect successfully while the Inpaint workspace still showed:

```text
Capability Snapshot Unavailable
Backend check: not checked
LanPaint — Missing nodes
```

The downstream engine-aware LoRA route then remained disabled because Neo had already forced the effective inpaint engine back to Native.

The presence of a custom-node directory is not, by itself, authoritative. Neo must see the loaded node classes and model catalogs through the selected Comfy profile's live `/object_info` response.

### Root cause

`ComfyProvider.discover_backend_capabilities()` already produced the Phase 8 LanPaint readiness matrix, but the Backend **Connect/Test** response did not transport that matrix into the selected profile runtime. The Image capability overlay also returned a generic non-Forge contract, so the frontend had no profile-bound fallback source.

### Corrected authority flow

```text
Selected Image backend profile
→ Connect/Test using that profile's base URL and timeout
→ Comfy /object_info capability discovery
→ profile.runtime.backend_capabilities
→ selected-profile Image capability overlay
→ LanPaint route evaluation
→ engine-aware LoRA route evaluation
```

The profile URL is authoritative. Neo must not borrow a manifest default URL, another Comfy profile, or a global provider capability snapshot.

### Stale-state protection

A successful capability snapshot is retained for the current explicit Connect/Test session. Passive profile listings after a restart or disconnect strip the saved snapshot from the live UI payload. This prevents yesterday's installed-node state from being presented as current readiness.

### LoRA behavior

No LoRA bypass was added.

- Plain LanPaint becomes selectable only when the exact route's required nodes and model roles are proven live.
- Krea 2 Turbo GGUF LanPaint then resolves to the Phase 6 model-only LoRA route.
- `LoraLoaderModelOnly` remains optional when no base/global LoRA is active.
- It becomes a real blocker only when an active base/global LoRA requires model injection.
- Native Inpaint, other providers, and unsupported family/loader routes remain unchanged.

### Operator validation

After applying the hotfix:

1. Restart ComfyUI after installing or updating custom nodes.
2. Restart Neo or hard-refresh the browser so the new static revision loads.
3. Select the intended Image backend profile.
4. Click **Connect/Test** for that exact profile.
5. Select the intended family, loader, and Inpaint mode.
6. Review **LanPaint Readiness**.

If the route is still blocked, diagnostics should now name the actual missing node pack, incompatible node signature, transformer, text encoder, VAE/AE, or LoRA loader. It must no longer report `Capability Snapshot Unavailable` after a successful profile-bound probe.

### Static asset revision

```text
lanpaint_capability_transport_hotfix_20260804
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_capability_transport_hotfix.py
PYTHONPATH=. python scripts/audit_lanpaint_capability_transport_hotfix.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py tests/test_lanpaint_capability_transport_hotfix.py
node --check neo_app/static/js/neo.js
```

Static and regression validation is complete. A live target ComfyUI `/object_info` probe and generation remain physical validation work.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_CAPABILITY_TRANSPORT_HOTFIX_20260804.md
```

## Post-Phase 11 hotfix — LanPaint and LoRA independence

This hotfix removes an unintended execution dependency between LanPaint and the shared LoRA Stack.

### Locked behavior

```text
LanPaint selected + LoRA master off
→ compile and run plain LanPaint

LanPaint selected + saved/disabled/deferred LoRA rows
→ preserve LoRA configuration only
→ do not require a LoRA loader
→ do not mutate the LanPaint graph

LanPaint selected + LoRA master explicitly on + active compatible base/global rows
→ apply the route-compatible optional LoRA chain

LanPaint selected + explicit LoRA request + missing/incompatible LoRA loader
→ block the LoRA request before queueing
→ do not redefine LanPaint itself as unavailable
```

LanPaint and LoRA are separate feature entities. Their only integration point is an optional route-compatible graph patch when both are deliberately active.

### Explicit execution intent

LoRA execution now uses a versioned frontend intent:

```text
execution_intent_version = 2
execution_enabled = true | false
```

Legacy `enabled=true` state, saved rows, mounted extension state, replay payloads, or workflow presence are not migrated into execution automatically. A current UI/replay snapshot must explicitly contain `execution_requested=true`; a missing intent field is treated as off. After this hotfix the user must explicitly switch **Apply LoRA Stack (optional)** on again.

The current submit snapshot is authoritative:

```text
_neo_extension_state.extensions.lora_stack.enabled
_neo_extension_state.extensions.lora_stack.workflow_applied
_neo_extension_state.extensions.lora_stack.execution_requested
```

If the current snapshot says LoRA is off, stale extension payloads are ignored before LanPaint capability checks and graph compilation.

### Capability behavior

- Plain LanPaint never requires `LoraLoader` or `LoraLoaderModelOnly`.
- Krea 2 Turbo uses `LoraLoaderModelOnly` only for an explicit active model-only LoRA request.
- Qwen/Z-Image routes use `LoraLoader` only for an explicit active model+CLIP LoRA request.
- Regional and finish-only rows remain deferred and do not create a base-graph dependency.
- Missing LoRA nodes do not block plain LanPaint.

### Static asset revision

```text
lanpaint_lora_independence_hotfix_20260804
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_lora_independence_hotfix.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py tests/test_lanpaint_capability_transport_hotfix.py tests/test_lanpaint_lora_independence_hotfix.py
PYTHONPATH=. python scripts/audit_lanpaint_lora_independence_hotfix.py --pretty
node --check neo_app/static/js/neo.js
```

Physical validation still requires one plain LanPaint generation with LoRA off, followed by one explicit LoRA-enabled generation.

Full implementation record:

```text
neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_LORA_INDEPENDENCE_HOTFIX_20260804.md
```

## Phase 12 — Global LoRA engine decoupling

Phase 12 corrects the LoRA compatibility identity. LoRA support is resolved from the selected backend, family, loader, and workflow mode. Native Inpaint versus LanPaint is not a LoRA compatibility dimension.

```text
LoRA compatibility key
= backend + family + loader + workflow mode

Workflow graph identity
= compatibility key + compiler-owned engine/variant lineage
```

For example, these workflows share one compatibility policy:

```text
krea2_turbo + gguf + inpaint + Native
krea2_turbo + gguf + inpaint + LanPaint
```

Both resolve to:

```text
krea2_turbo:gguf:inpaint
LoRA mode: model-only
Loader: LoraLoaderModelOnly
```

Their graph anchors remain different:

```text
Native Inpaint
GGUF model → optional LoRA chain → native DifferentialDiffusion/model consumer → native sampler

LanPaint
GGUF model → optional LoRA chain → DifferentialDiffusionAdvanced → LanPaint_KSampler
```

The same rule applies to model+CLIP families such as Qwen Image and Z-Image. Their family/loader policy is shared across inpaint engines, while each compiler emits its own model and CLIP references and consumer nodes.

### Optional execution remains explicit

```text
Native Inpaint + LoRA off
→ run without LoRA

LanPaint + LoRA off
→ run without LoRA

Native Inpaint + explicit LoRA on
→ apply the native compiler's family-compatible patch profile

LanPaint + explicit LoRA on
→ apply the LanPaint compiler's family-compatible patch profile
```

Saved rows, disabled rows, regional rows, finish-only rows, replay metadata, and extension mounting remain configuration only. They do not imply execution.

### Patch-profile v2

The compiler-owned profile is now:

```text
neo.image.lora_stack.patch_profile.v2
```

It records both:

```text
compatibility_route_key
workflow_route_key
workflow_engine
```

Legacy v1 profiles with engine-specific route keys are normalized to v2. Their compatibility key drops the engine, while their workflow route and engine remain audit/replay metadata. Migration never enables LoRA execution.

### Manifest contract

The generated LoRA manifest declares:

```json
{
  "compatibility_dimensions": {
    "backend": true,
    "family": true,
    "loader": true,
    "workflow_mode": true,
    "engine": false
  }
}
```

`route_states` and `route_policies` contain no `:native` or `:lanpaint` compatibility keys. The frontend reads family/loader policy from `route_policies`; engine switching changes graph lineage only.

### Static validation

```text
PYTHONPATH=. pytest -q tests/test_lora_stack_phase12_global_engine_decoupling.py
PYTHONPATH=. pytest -q tests/test_lanpaint*.py tests/test_lora_stack_phase12_global_engine_decoupling.py
PYTHONPATH=. python scripts/audit_lora_stack_phase12_global_engine_decoupling.py
node --check neo_app/static/js/neo.js
```

Physical validation remains required for Native Inpaint and LanPaint with LoRA off/on on every activated family and loader.

## Phase 13 — Universal LanPaint family adapter v2

Phase 13 introduces one immutable family/loader adapter authority for every planned LanPaint route:

```text
neo.image.lanpaint_family_adapter.v2
neo_app.image.lanpaint_family_adapter
```

The adapter resolves a canonical route into:

- model, text-encoder, and VAE loader contracts;
- portable model-role and parameter aliases;
- positive and negative conditioning policy;
- crop, sampling-mask, and stitch policy;
- latent encode/decode and family sampling transforms;
- basic or advanced LanPaint sampler contract;
- engine-independent LoRA compatibility policy;
- required and conditional Comfy node groups;
- replay, capability, and audit identity;
- exact compiler-binding state.

### Binding authority

The compile router, backend discovery, capability gate, UI-state normalizer, graph compilers, and replay validator consume the same adapter identity and fingerprint. Static frontend family data remains presentation/default fallback only and cannot activate a route.

### Active route lock

Phase 13 activates no new route. The exact runnable set remains:

```text
krea2_turbo:gguf:inpaint:lanpaint
qwen_image:diffusion_model:inpaint:lanpaint
qwen_image:gguf:inpaint:lanpaint
z_image:diffusion_model:inpaint:lanpaint
z_image:gguf:inpaint:lanpaint
z_image_turbo:diffusion_model:inpaint:lanpaint
z_image_turbo:gguf:inpaint:lanpaint
```

The registry describes but does not activate:

```text
krea2_turbo:diffusion_model     policy_complete_unbound
krea2:*                         scaffold_only
qwen_image_edit:*               scaffold_only
z_image_base:*                  scaffold_only
```

A complete policy is not sufficient for execution. `binding.state=compiler_bound`, `selectable=true`, and a concrete compiler ID are all required.

### Graph-profile separation

Krea2 Turbo GGUF retains:

```text
krea2_differential_crop_stitch_v1
GGUF loader → optional model-only LoRA → DifferentialDiffusionAdvanced → LanPaint_KSampler
```

Qwen and Z-Image retain:

```text
aura_crop_stitch_v1
family loader → optional model+CLIP LoRA → ModelSamplingAuraFlow → LanPaint_KSampler
```

The adapter chooses family policy. Compiler modules continue to own node IDs, output ports, consumer rewiring, and final graph emission.

### Adapter fingerprint

`adapter_fingerprint` identifies immutable family/loader architecture. User crop, mask, stitch, and sampler overrides have their own UI/replay fingerprints and do not create a new adapter identity for each generation. The family policy fingerprint remains inside the adapter fingerprint, so real policy or architecture changes still invalidate stale replay lineage.

### Capability and replay rules

- `/object_info` checks derive required nodes and model roles from the adapter.
- Conditional LoRA nodes are required only for explicit LoRA execution.
- UI route selection requires the selected backend profile to publish a matching selectable adapter.
- Replay records adapter ID and fingerprint and fails closed on exact-route adapter drift.
- No personal paths, backend installation roots, or provider upload aliases belong in the adapter.

### Static asset revision

```text
lanpaint_family_adapter_v2_20260805
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase13_family_adapter_v2.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase13.py
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py tests/test_lanpaint_capability_transport_hotfix.py tests/test_lanpaint_lora_independence_hotfix.py tests/test_lora_stack_phase12_global_engine_decoupling.py
node --check neo_app/static/js/neo.js
```

Physical validation remains required on the existing Krea2, Qwen, and Z-Image routes before Phase 14 adds or promotes another family.

## Phase 14 — Existing route parity and stabilization

Phase 14 promotes the policy-complete Krea 2 Turbo safetensors/component route and records one stabilization envelope for every currently bound LanPaint family/loader route.

### Active route set

```text
krea2_turbo:diffusion_model:inpaint:lanpaint
krea2_turbo:gguf:inpaint:lanpaint
qwen_image:diffusion_model:inpaint:lanpaint
qwen_image:gguf:inpaint:lanpaint
z_image:diffusion_model:inpaint:lanpaint
z_image:gguf:inpaint:lanpaint
z_image_turbo:diffusion_model:inpaint:lanpaint
z_image_turbo:gguf:inpaint:lanpaint
```

Only `krea2_turbo:diffusion_model:inpaint:lanpaint` is newly bound in Phase 14. Krea RAW, Qwen Image Edit, the duplicate `z_image_base` identity, and later-family scaffolds remain non-selectable.

### Krea loader parity

Both Krea 2 Turbo loaders use the same family and graph contracts:

```text
Krea family policy
→ Krea2 CLIPLoader(type=krea2)
→ Qwen Image VAE
→ optional model-only LoRA chain
→ DifferentialDiffusionAdvanced
→ LanPaint_KSampler
→ restore crop geometry
→ source-space stitch
```

The loader boundary is exact:

```text
Safetensors/component: UNETLoader(unet_name, weight_dtype)
GGUF:                  UnetLoaderGGUF or LoaderGGUF
```

No loader fallback is allowed. Missing `UNETLoader` blocks only the safetensors route; missing a GGUF loader blocks only the GGUF route.

### Stabilization metadata

Every compiler-bound adapter publishes:

```text
stabilization.state = phase14_stabilized
stabilization.loader_parity_group = <family>:inpaint:lanpaint
stabilization.physical_validation = pending
stabilization.promotion_state = experimental_available
```

Phase rollout metadata is excluded from existing adapter architecture fingerprints. The new stabilization fields are optional in the existing v2 JSON schema so older persisted adapter snapshots remain readable. This preserves replay compatibility for the seven pre-Phase-14 routes. The newly bound Krea safetensors route receives its own loader-specific adapter fingerprint.

### LoRA boundary

Phase 19 preserves the Phase 12 engine-independent compatibility key and adds exact live-provider catalog binding plus fail-closed execution proof. LoRA compatibility depends on family, loader, and workflow mode—not the inpaint engine.

```text
Krea safetensors + Native Inpaint  → krea2_turbo:diffusion_model:inpaint
Krea safetensors + LanPaint        → krea2_turbo:diffusion_model:inpaint
Krea GGUF + Native Inpaint         → krea2_turbo:gguf:inpaint
Krea GGUF + LanPaint               → krea2_turbo:gguf:inpaint
```

The active compiler still owns graph anchors. Plain Native or LanPaint execution has no LoRA dependency; `LoraLoaderModelOnly` is required only after explicit LoRA execution intent.

### Physical boundary

All eight routes remain `experimental_available`. Before promotion, run Krea safetensors and GGUF with the same source, mask, prompt, seed, controls, and optional LoRA. Confirm model loading, mask behavior, output dimensions, crop restoration, stitch alignment, and replay lineage.

### Static validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase14_route_parity.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase14.py
PYTHONPATH=. pytest -q tests/test_lanpaint*.py tests/test_lora_stack_phase12_global_engine_decoupling.py
node --check neo_app/static/js/neo.js
```

## Phase 15 — SD family onboarding

Phase 15 adds exact LanPaint adapters and compiler bindings for SDXL, SD 1.5, and SD 3.5. The universal Phase 13 adapter remains the policy authority and Phase 12 remains the LoRA compatibility authority.

### Active SD route set

```text
sdxl:checkpoint:inpaint:lanpaint
sd15:checkpoint:inpaint:lanpaint
sd35:diffusion_model:inpaint:lanpaint
sd35:gguf:inpaint:lanpaint
```

All four routes remain `experimental_available` until physical generation is completed.

### SDXL and SD 1.5 checkpoint topology

SDXL and SD 1.5 use a bundled checkpoint graph:

```text
CheckpointLoaderSimple
├─ MODEL ──────────────┐
├─ CLIP → positive/negative conditioning
└─ VAE  → VAEEncode / VAEDecode

MODEL → optional model+CLIP LoRA chain
      → LanPaint_KSampler
      → decode
      → restore crop geometry
      → source-space stitch
```

The graph shape is shared, but defaults and adapter fingerprints remain family-specific. SDXL has no GGUF LanPaint route.

### SD 3.5 split-loader topology

```text
Safetensors/component:
UNETLoader
TripleCLIPLoader(CLIP-L, CLIP-G, T5XXL)
VAELoader

GGUF:
UnetLoaderGGUF
TripleCLIPLoaderGGUF(CLIP-L, CLIP-G, T5XXL)
VAELoader

MODEL → optional model+CLIP LoRA chain
      → ModelSamplingSD3
      → LanPaint_KSampler
      → decode / restore / stitch
```

SD 3.5 must not inherit Krea Differential Diffusion or Qwen/Z AuraFlow transforms.

### SD 1.5 GGUF boundary

```text
sd15:gguf:inpaint:lanpaint
state: blocked_loader_ecosystem
```

LanPaint can sample many model architectures, but that does not establish a usable GGUF loader ecosystem for a classic convolutional SD 1.5 UNet. Neo keeps the route represented for diagnostics while leaving it non-selectable and non-executable. No adjacent loader fallback is allowed.

### Capability requirements

Checkpoint routes require:

```text
CheckpointLoaderSimple
CLIPTextEncode
VAEEncode / VAEDecode
LanPaint_KSampler
CropByMask
ImageResizeKJv2
GrowMaskWithBlur
ImageCompositeMasked
```

SD 3.5 additionally requires the exact selected model loader, exact triple-CLIP loader, all three encoder assets, VAE, and `ModelSamplingSD3(model, shift)`.

### LoRA boundary

LoRA remains optional and engine-independent.

```text
SDXL checkpoint compatibility: sdxl:checkpoint:inpaint
SD 1.5 checkpoint compatibility: sd15:checkpoint:inpaint
SD 3.5 safetensors compatibility: sd35:diffusion_model:inpaint
SD 3.5 GGUF compatibility: sd35:gguf:inpaint
```

Plain LanPaint emits no LoRA node and does not require `LoraLoader`. An explicit compatible LoRA request uses a compiler-owned model+CLIP patch profile.

### Replay and stabilization

- The eight Phase 14 adapter fingerprints remain unchanged.
- Each new SD route records its exact adapter ID, adapter fingerprint, loader, family, controls, source/mask lineage, model assets, and LoRA graph anchors.
- Replay must fail closed on exact-route or adapter drift.
- SD 1.5 GGUF cannot be restored as SD 1.5 checkpoint or another family.

### Static asset revision

```text
lanpaint_sd_family_phase15_20260805
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase15_sd_onboarding.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase15.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase*.py
node --check neo_app/static/js/neo.js
```

Physical validation must run each active SD route with the same source, mask, prompt, seed, and controls; verify model loading, mask isolation, source dimensions, crop restoration, stitch boundaries, optional LoRA influence, and replay reconstruction.

## Phase 16 — Flux.1 family onboarding

Phase 16 adds Flux.1 Dev and Schnell LanPaint execution through the universal family adapter. Dev and Schnell share the Flux.1 architecture and LoRA compatibility surface, but they retain separate variant defaults, model identity, warnings, and replay lineage.

### Active Flux.1 route set

```text
flux:diffusion_model:inpaint:lanpaint
flux:gguf:inpaint:lanpaint
```

Both routes remain `experimental_available` pending physical generation. Each route accepts an explicit `flux_variant` of `dev` or `schnell`; filename inference is permitted only when it agrees with the explicit variant.

### Safetensors/component topology

```text
UNETLoader
DualCLIPLoader(type=flux, CLIP-L, T5XXL)
VAELoader(AE/VAE)

MODEL → optional model+CLIP LoRA chain
      → Flux conditioning
      → LanPaint_KSampler
      → VAEDecode
      → restore crop dimensions
      → source-space stitch
```

The positive prompt is encoded through the Flux dual-encoder path and `FluxGuidance`. Negative conditioning is zeroed. Prompt First is disabled because Flux does not use the normal CFG-positive/negative pairing expected by that mode.

### GGUF topology

```text
UnetLoaderGGUF or LoaderGGUF
DualCLIPLoaderGGUF(type=flux, CLIP-L, T5XXL)
VAELoader(AE/VAE)

MODEL → optional model+CLIP LoRA chain
      → ModelSamplingFlux(max_shift, base_shift)
      → Flux conditioning
      → LanPaint_KSampler
      → decode / restore / stitch
```

GGUF availability is proven from the selected profile's live node signatures and model/encoder catalogs. LanPaint installation alone does not make the GGUF route selectable.

### Dev and Schnell policies

```text
Flux.1 Dev
steps: 30
CFG: 1.0
Flux guidance: 1.5 default
LanPaint thinking steps: 5
recommended guidance range: 1.0–2.0

Flux.1 Schnell
steps: 4
CFG: 1.0
Flux guidance: 1.0 default
LanPaint thinking steps: 2
recommended guidance range: 0.0–1.5
```

Schnell is a distilled four-step variant and cannot silently substitute for Dev. Krea2 model names detected under the generic Flux family fail closed and direct the caller to the dedicated Krea2 route.

### Scope exclusions

Phase 16 does not activate:

```text
Flux.2 Dev
Flux.2 Klein
Flux Fill
Flux Kontext
ControlNet
Redux/reference conditioning
outpaint
```

These retain their existing Neo routes or later onboarding phases.

### LoRA boundary

LoRA remains optional and engine-independent:

```text
flux:diffusion_model:inpaint
flux:gguf:inpaint
```

Plain Native Inpaint or LanPaint execution does not require `LoraLoader`. An explicit compatible LoRA request uses model+CLIP patch anchors emitted by the active compiler. Saved or disabled rows do not mutate the graph.

### Capability and replay boundary

The selected route requires the exact model loader, dual-CLIP loader, CLIP-L, T5XXL, AE/VAE, Flux guidance contract, LanPaint sampler, and shared crop/restore/stitch nodes. The GGUF route additionally requires `ModelSamplingFlux` and a compatible GGUF model loader.

Replay retains the exact `dev` or `schnell` variant, loader, adapter fingerprint, model/encoder/VAE assets, guidance, controls, source/mask lineage, optional LoRA refs, and output lineage. Variant or loader drift fails closed.

### Static asset revision

```text
lanpaint_flux1_family_phase16_20260805
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase16_flux1_onboarding.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase16.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint*.py tests/test_lora_stack_phase12_global_engine_decoupling.py
node --check neo_app/static/js/neo.js
```

Physical validation must run Dev and Schnell through both loaders, with LoRA off and on, and verify loader/model selection, low-guidance behavior, mask isolation, crop restoration, stitch boundaries, source dimensions, and replay reconstruction.

## Phase 17 — Flux.2 Dev and Klein onboarding

Phase 17 adds separate Flux.2 Dev and Flux.2 Klein LanPaint adapters and compiler bindings. Flux.2 is not treated as a Flux.1 preset: its encoder architecture, VAE, variant identity, capability requirements, defaults, and replay lineage remain independent.

### Active Flux.2 route set

```text
flux2_dev:diffusion_model:inpaint:lanpaint
flux2_dev:gguf:inpaint:lanpaint
flux2_klein:diffusion_model:inpaint:lanpaint
flux2_klein:gguf:inpaint:lanpaint
```

All four routes remain `experimental_available` pending physical generation.

### Flux.2 Dev topology

```text
Safetensors/component transformer:
UNETLoader
CLIPLoader(type=flux2, Mistral 3 Small Flux2)
VAELoader(Flux2 VAE)

GGUF transformer:
UnetLoaderGGUF or LoaderGGUF
CLIPLoader(type=flux2, native Mistral 3 Small Flux2)
VAELoader(Flux2 VAE)

MODEL → optional model+CLIP LoRA chain
      → CLIPTextEncode
      → FluxGuidance
      → ConditioningZeroOut
      → VAEEncode / SetLatentNoiseMask
      → LanPaint_KSampler
      → decode / restore / stitch
```

The GGUF Dev route quantizes the transformer branch only. It does not claim an unproven GGUF Mistral 3 text-encoder contract. The selected live backend must expose the native Flux2 Mistral encoder through `CLIPLoader(type=flux2)` and the matching catalog asset.

### Flux.2 Klein topology and variants

Klein uses the same crop/mask/stitch base graph but a Qwen3 Flux2 text-encoder contract. The following variant identities remain explicit:

```text
klein_4b
klein_4b_distilled
klein_9b
klein_9b_distilled
```

The 4B variants require the approved Qwen3-4B encoder path. The 9B variants require the approved Qwen3-8B encoder path. Base and distilled defaults are not interchangeable:

```text
Klein base:       50 steps, guidance 4.0, thinking 3
Klein distilled:   4 steps, guidance 1.0, thinking 2
```

Safetensors/component routes use `UNETLoader + CLIPLoader(type=flux2)`. GGUF routes use an approved GGUF transformer loader and may use `CLIPLoaderGGUF(type=flux2)` only when the selected profile proves the compatible Qwen3 encoder signature and asset.

### Family isolation

Phase 17 does not borrow:

```text
Flux.1 GGUF ModelSamplingFlux assumptions
Krea DifferentialDiffusionAdvanced
Qwen/Z ModelSamplingAuraFlow
SD ModelSamplingSD3
```

Flux.2 Dev model evidence cannot run through Klein, Klein model evidence cannot run through Dev, Qwen3 cannot substitute for Dev's Mistral 3 encoder, and Klein 4B/9B encoder-size mismatches fail closed.

### LoRA boundary

LoRA remains optional and engine-independent under the Phase 12 compatibility contract, with Phase 19 exact-catalog enforcement:

```text
flux2_dev:diffusion_model:inpaint
flux2_dev:gguf:inpaint
flux2_klein:diffusion_model:inpaint
flux2_klein:gguf:inpaint
```

Plain Native Inpaint or LanPaint contains no LoRA node. Only explicit compatible base/global LoRA execution intent activates a model+CLIP chain using compiler-owned graph anchors.

### Capability and replay boundary

The selected route must prove its exact model loader, Flux2 text-encoder loader/signature, expected encoder architecture, Flux2 VAE, `FluxGuidance`, LanPaint sampler, crop/mask/latent/restore/stitch nodes, and selected catalog assets. No family, variant, encoder, loader, provider, or engine fallback is permitted.

Replay records the exact Dev/Klein family, loader, Klein size/distillation variant, model, encoder architecture and asset, VAE, guidance, source/mask lineage, optional LoRA chain, adapter fingerprint, sampler, restore, stitch, and output lineage.

### Scope exclusions

Phase 17 does not add Flux.2 image editing, multi-reference conditioning, Redux, Kontext, Fill, ControlNet, outpaint, or video routes.

### Static asset revision

```text
lanpaint_flux2_family_phase17_20260805
```

### Validation

```text
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase17_flux2_onboarding.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase17.py --pretty
PYTHONPATH=. pytest -q tests/test_lanpaint*.py tests/test_lora_stack_phase12_global_engine_decoupling.py
node --check neo_app/static/js/neo.js
```

Physical validation must run Flux.2 Dev and every supported Klein size/distillation variant through both transformer loaders, with LoRA off and on. Verify encoder/model matching, mask isolation, crop restoration, stitch boundaries, source dimensions, optional LoRA influence, capability diagnostics, and exact replay reconstruction.

## Phase 18 — Qwen Image Edit 2509 and 2511 onboarding

Phase 18 first creates `qwen_image_edit_2511` as a real Neo model family, then adds its normal Comfy routes and version-aware LanPaint support. Qwen Image Edit 2509 and 2511 may share compiler helpers, but they remain distinct model, capability, adapter, and replay identities.

### Qwen Image Edit 2511 normal route set

```text
qwen_image_edit_2511:diffusion_model:{txt2img,img2img,edit,inpaint,outpaint}
qwen_image_edit_2511:gguf:{txt2img,img2img,edit,inpaint,outpaint}
```

The no-source `txt2img` route is experimental compatibility behavior; plain `qwen_image` remains the recommended primary text-to-image family.

Img2Img and Edit expose Image 1 plus optional Image 2 and Image 3 lanes. Qwen Stitch is available only for Img2Img/Edit and creates one explicit composed source; it does not replace native multi-source conditioning. Inpaint and Outpaint are single-canvas workflows and consume Image 1 only.

### Loader boundaries

Safetensors/components use `UNETLoader`, `CLIPLoader(type=qwen_image)`, Qwen2.5-VL 7B, Qwen Image VAE, `TextEncodeQwenImageEditPlus`, and AuraFlow shift 3.1. GGUF uses the approved GGUF transformer/text-encoder loaders and requires an explicit matching MMProj sidecar. Missing or mismatched MMProj fails closed.

### Active Qwen Edit LanPaint routes

```text
qwen_image_edit_2509:diffusion_model:inpaint:lanpaint
qwen_image_edit_2509:gguf:inpaint:lanpaint
qwen_image_edit_2511:diffusion_model:inpaint:lanpaint
qwen_image_edit_2511:gguf:inpaint:lanpaint
```

The LanPaint graph crops Image 1 and its mask, uses `TextEncodeQwenImageEditPlus` with the cropped Image 1 for both conditioning branches, applies `ModelSamplingAuraFlow`, runs `LanPaint_KSampler`, restores the crop, and composites into source space. Image 2 and Image 3 are not injected into the masked latent graph.

LoRA remains optional and engine-independent. Plain Native Inpaint and LanPaint need no LoRA loader. Version/model/loader mismatch, missing edit-conditioning nodes, or missing GGUF MMProj evidence blocks only the exact selected route.

All Phase 18 routes remain `experimental_available` until physical ComfyUI validation covers normal one/two/three-source editing, Stitch, Native Inpaint, Outpaint, LanPaint, LoRA off/on, replay, and negative dependency tests.


## Phase 19 — Global LoRA exact-catalog binding and execution proof

Phase 19 is a cross-workflow LoRA correction inserted before further LanPaint family onboarding. It does not change the LanPaint sampler or make LoRA compatibility depend on Native versus LanPaint.

### Identity boundary

Neo persists portable names with `/`, while Comfy graph compilation must submit the exact value advertised by the selected live loader catalog. On Windows these may differ only by separator:

```text
portable: Krea2/Style.safetensors
provider: Krea2\Style.safetensors
```

Before graph mutation, each explicit row is rebound against the exact `LoraLoader` or `LoraLoaderModelOnly` catalog for the selected Image profile. Ambiguous normalized matches, missing entries, unavailable catalogs, missing loader nodes, invalid compiler patch profiles, and missing graph anchors fail closed.

### Workflow boundary

The same enforcement applies to Generate, Img2Img, Native Inpaint, LanPaint Inpaint, and Outpaint. LoRA off remains graph-neutral. LoRA on must either produce a verified patched graph or stop before `/prompt`; it may not silently execute the unpatched base workflow.

Native Inpaint and LanPaint continue sharing one family/loader/workflow compatibility state while supplying different compiler-owned model/CLIP consumer anchors.

### Execution proof

Compiled output metadata records the portable request, exact submitted provider enum, selected loader class, inserted node IDs, strengths, original/patched model and CLIP references, rewired consumers, provider/profile, route, engine, and one explicit execution state. Replay stores the portable identity and re-resolves it against the current live catalog.

### Physical validation boundary

Static/compiler tests prove exact-name rebinding and graph lineage. Release promotion still requires fixed-seed A/B runs on the target ComfyUI installation for representative model-only, model+CLIP, checkpoint, split-model, GGUF, source-conditioned, Native Inpaint, and LanPaint routes.

### Next phase status

Phase 20 now completes Z-Image Base and Turbo as separate LanPaint inpainting routes. The duplicate `z_image_base` scaffold remains blocked because the canonical `z_image` family owns Base.

## Phase 20 — Z-Image LanPaint inpainting onboarding

Phase 20 is a focused LanPaint inpainting completion pass over the earlier Z-Image scaffold. It does not redesign Z-Image txt2img, Img2Img, Native Inpaint, Outpaint, ControlNet, or post-processing routes.

### Canonical family identities

```text
z_image       = Z-Image Base
z_image_turbo = Z-Image Turbo
```

The older `z_image_base` scaffold is intentionally not activated. A second public Base id would create ambiguous capability, UI, adapter, and replay identities.

### Active routes

```text
z_image:diffusion_model:inpaint:lanpaint
z_image:gguf:inpaint:lanpaint
z_image_turbo:diffusion_model:inpaint:lanpaint
z_image_turbo:gguf:inpaint:lanpaint
```

All remain `experimental_available` until physical ComfyUI validation.

### Base policy

Z-Image Base uses `z_image_lanpaint_base_crop_stitch_v2`, AuraFlow shift 3.0, separate positive and negative `CLIPTextEncode` branches, 35 steps, CFG 3.5, and a cautious LanPaint thinking default of 3. Its stability profile is `z_image_lanpaint_base_cautious_v1`.

The UI and compiler may warn when an explicit Base thinking value exceeds the cautious default, but replay must preserve a deliberate user value rather than silently clamping it.

### Turbo policy

Z-Image Turbo uses `z_image_turbo_lanpaint_crop_stitch_v2`, AuraFlow shift 3.0, `ConditioningZeroOut` for the negative branch, 9 steps, CFG 1.0, and a LanPaint thinking default of 5. Its stability profile is `z_image_turbo_distilled_v1`.

Base and Turbo must retain separate adapter fingerprints, graph profiles, conditioning, sampling defaults, capability evidence, and replay variants.

### Shared graph stages

```text
source + mask
→ crop and processing resize
→ sampling-mask refinement
→ Z-Image model / lumina2 CLIP / AE loaders
→ family-specific conditioning
→ ModelSamplingAuraFlow
→ VAEEncode + SetLatentNoiseMask
→ LanPaint_KSampler
→ VAEDecode
→ restore crop dimensions
→ mask blur and source-space stitch
→ output
```

The graph must not insert Krea Differential Diffusion or borrow another family’s encoder, VAE, negative-conditioning, or sampler defaults.

### Loader and capability boundary

Safetensors/components use `UNETLoader`; GGUF uses the approved `UnetLoaderGGUF` lane. Both require a matching lumina2/Qwen3 encoder, AE/VAE, AuraFlow node, LanPaint sampler, and crop/resize/mask/stitch nodes. GGUF availability is not inferred from LanPaint availability. Missing exact evidence blocks only the selected route.

Base and Turbo model tokens are checked independently. Selecting a Base model under Turbo or Turbo under Base must fail capability validation.

### LoRA boundary

The Phase 19 exact-catalog contract applies unchanged. Both families use engine-independent model+CLIP compatibility keys. LanPaint supplies its own model and CLIP graph anchors; an explicit compatible LoRA is rebound to the exact live `LoraLoader` enum and must reach AuraFlow plus both conditioning branches. Missing or unprovable LoRA requests fail closed before `/prompt`.

### Replay

Replay stores canonical family, loader, `base`/`turbo` variant, stability profile, source/mask lineage, crop/processing/stitch controls, sampling controls, selected assets, portable LoRA rows, adapter id, and adapter fingerprint. Replay revalidates the current provider and cannot convert Base to Turbo, GGUF to safetensors, or LanPaint to Native.

### Validation commands

```bash
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase20_z_image_onboarding.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase20_z_image.py
```

### Physical boundary

Run all four routes with LoRA off/on, small and edge-touching masks, Base default and explicit higher thinking values, missing-node/model tests, replay, source-dimension restoration, and fixed-seed LoRA A/B comparisons. Do not promote the routes until those target-backend runs pass.

### Next phase

Phase 21 — HiDream and Hunyuan LanPaint inpainting onboarding. These remain separate families and must own their encoder topology, model sampling, guidance, loader, LoRA, capability, and replay contracts.

## Phase 21 — HiDream-I1 onboarding and Hunyuan workspace hold

Phase 21 activates HiDream-I1 as an Image model family and LanPaint inpainting option while refusing to blur Hunyuan image and video architectures together.

### Active HiDream-I1 routes

```text
hidream:diffusion_model:inpaint:lanpaint
hidream:gguf:inpaint:lanpaint
```

Normal HiDream-I1 text-to-image continues through the existing HiDream compiler. LanPaint adds source image, mask, crop/restore, and stitch ownership without changing the normal generation contract.

### Profile defaults

```text
Full → 50 steps, CFG 5.0, SD3 shift 3.0
Dev  → 28 steps, CFG 1.0, SD3 shift 6.0
Fast → 16 steps, CFG 1.0, SD3 shift 3.0
```

All profiles use the HiDream four-encoder topology:

```text
CLIP-L + CLIP-G + T5XXL + Llama 3.1 8B
```

Every slot must resolve against the selected Comfy profile. Missing one encoder blocks the exact route; Neo may not silently substitute an adjacent encoder catalog.

### Graph boundary

```text
source + mask
→ crop and processing resize
→ sampling-mask refinement
→ HiDream-I1 model and four-encoder CLIP loaders
→ positive/negative conditioning
→ ModelSamplingSD3
→ VAEEncode + SetLatentNoiseMask
→ LanPaint_KSampler
→ VAEDecode
→ restore and source-space stitch
```

HiDream-I1 does not inherit AuraFlow, Differential Diffusion, Qwen edit encoding, or another family’s guidance defaults. HiDream-E1/E1.1 and HiDream-O1 remain blocked because their workflows are not the I1 generation architecture.

### LoRA

LoRA compatibility remains keyed by provider, family, loader, and workflow mode—not the inpaint engine. Phase 19 exact-catalog rebinding applies before the graph runs, and HiDream LanPaint must prove that the patched model reaches `ModelSamplingSD3` and both conditioning branches reach the patched CLIP.

### Hunyuan boundary

```text
HunyuanVideo/T2V → held_for_video_workspace
HunyuanImage     → held_for_separate_verified_image_workflow
```

The LanPaint Hunyuan example uses the HunyuanVideo/T2V architecture. Neo therefore keeps it out of Image until the Video workspace owns temporal model and output contracts. The separate HunyuanImage family remains visible only in diagnostics/provider planning and cannot become runnable until an image-native LanPaint graph is physically proven.

### Validation

```bash
PYTHONPATH=. pytest -q tests/test_lanpaint_route_family_phase21_hidream_hunyuan_hold.py
PYTHONPATH=. python scripts/audit_lanpaint_route_family_phase21_hidream_hunyuan_hold.py
```

Physical validation must cover Full/Dev/Fast, both available loader lanes, all four encoder slots, LoRA off/on, mask boundaries, replay, missing dependencies, and confirmation that both Hunyuan holds remain non-runnable in Image.

### Next phase

Phase 22 — Anima and Ideogram4 LanPaint Inpainting Onboarding. HunyuanVideo remains deferred to the Video workspace program.



## Phase 22 — Anima and Ideogram 4 Image-family and LanPaint onboarding

Phase 22 registers both families before enabling workflow routes. It does not treat “LanPaint supports almost any model” as proof that every loader or workflow exists. Each route must prove its native Comfy graph, model assets, loader nodes, capability contract, replay identity, and LoRA policy.

### Anima Base v1

Active routes:

```text
anima:diffusion_model:txt2img
anima:gguf:txt2img
anima:diffusion_model:img2img
anima:gguf:img2img
anima:diffusion_model:inpaint:lanpaint
anima:gguf:inpaint:lanpaint
```

Anima uses a normal image-generation topology:

```text
UNETLoader or UnetLoaderGGUF
+ CLIPLoader(type=stable_diffusion) with Qwen3 0.6B
+ VAELoader with Qwen Image VAE
+ CLIPTextEncode positive/negative
+ EmptyLatentImage for txt2img or VAEEncode source latent for img2img
+ KSampler
```

LanPaint replaces only the normal sampler path and adds source/mask crop, latent noise mask, restore, and source-space stitch. Anima uses the basic `LanPaint_KSampler`. Its official Turbo accelerator is a model-only LoRA, so Neo uses `LoraLoaderModelOnly` with Phase 19 exact-catalog rebinding. GGUF support is experimental and quantizes the diffusion model only unless the connected provider proves additional compatible assets.

### Ideogram 4

Active routes:

```text
ideogram4:diffusion_model:txt2img
ideogram4:gguf:txt2img
ideogram4:diffusion_model:inpaint:lanpaint
ideogram4:gguf:inpaint:lanpaint
```

Held route:

```text
ideogram4:*:img2img → held_unverified
```

Ideogram 4 requires a paired-model advanced graph:

```text
main diffusion model
+ unconditional diffusion model
+ CLIPLoader(type=ideogram4) with Qwen3-VL 8B
+ Flux 2 VAE
+ positive conditioning + zeroed negative conditioning
+ Ideogram4Scheduler
+ DualModelGuider
+ SamplerCustomAdvanced for txt2img
```

Its LanPaint route must use `LanPaint_SamplerCustomAdvanced`, not `LanPaint_KSampler`. The advanced sampler receives `RandomNoise`, `KSamplerSelect`, `Ideogram4Scheduler`, `DualModelGuider`, the masked source latent, and all LanPaint custom controls.

Neo does not synthesize an Ideogram 4 img2img route from generic VAE encoding because no verified local graph contract was found. Likewise, LoRA remains blocked for Ideogram 4: a single loader inserted on only the main model would leave the unconditional model branch inconsistent. Explicit LoRA requests therefore fail closed.

### Capability and replay rules

- Safetensors and GGUF routes are separate capability contracts.
- Ideogram 4 must prove both the main and unconditional model assets.
- Anima GGUF does not imply a GGUF text encoder or VAE.
- Replay stores portable asset identities and revalidates the live provider catalog.
- Ideogram replay preserves the custom-advanced sampler contract and dual-model requirement.
- Missing nodes or assets block the exact route before `/prompt`; no family, loader, sampler, or workflow fallback is allowed.

### Physical validation boundary

Before promotion, test Anima txt2img/img2img/LanPaint and Ideogram 4 txt2img/LanPaint on every installed loader lane, including fixed-seed LoRA A/B tests for Anima, paired-model mismatch tests for Ideogram 4, mask-edge/stitch tests, replay, missing dependencies, and Comfy history lineage inspection.

### Next phase

Phase 23 — Wan 2.2 Image and Reference LanPaint Inpainting Onboarding. Wan video remains a separate Video-workspace program.
