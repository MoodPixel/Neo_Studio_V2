# Krea 2 AnyPaint — Optional Capability Integration

## Summary

Neo treats `alexw5702-afk/krea2-anypaint` as an **optional connected-Comfy capability**.

As of **Phase 5**, Neo supports **Krea 2 AnyPaint** as the **third masked-edit engine through a standalone compiler module** for the supported route:

- family: `krea2_turbo`
- loader: `diffusion_model`
- modes: `inpaint`, `outpaint`

Native Inpaint and LanPaint remain separate engines. Krea 2 Identity Edit remains separate and cannot be stacked with AnyPaint.

---

## Required upstream pieces

Neo discovers and validates these upstream AnyPaint nodes from live Comfy `/object_info`:

- `Krea2AnyPaintPrepare`
- `Krea2AnyPaintEncode`
- `Krea2AnyPaintModelPatch`

Neo also requires:

- `LoraLoaderModelOnly`
- `UNETLoader`
- `CLIPLoader`
- `VAELoader`
- `KSampler`
- `VAEDecode`
- the AnyPaint adapter LoRA `krea2_anypaint_rank32.safetensors`
- a Qwen3-VL-4B text encoder visible to `CLIPLoader(type=krea2)`
- a Qwen Image VAE visible to `VAELoader`
- a diffusion-model catalog entry for the selected Krea 2 Turbo model

---

## Standalone compiler and execution graph

When **Krea 2 AnyPaint** is selected, Neo dispatches directly to `neo_app/providers/comfy_workflows/krea2_anypaint.py`. The Native Krea compiler is not called. The standalone AnyPaint compiler owns validation, asset resolution, readiness enforcement, adapter selection, mask/canvas preparation, sampling, metadata, and raw decode.

### Inpaint / Outpaint graph shape

1. `UNETLoader`
2. `CLIPLoader(type=krea2)`
3. `VAELoader`
4. `LoadImage` (source)
5. `LoadImageMask` (when a mask is present)
6. `Krea2AnyPaintPrepare`
7. `Krea2AnyPaintEncode`
8. `ConditioningZeroOut`
9. `LoraLoaderModelOnly` (AnyPaint adapter)
10. `Krea2AnyPaintModelPatch`
11. `KSampler`
12. `VAEDecode`
13. `PreviewImage`

### Important behavior

- `Krea2AnyPaintPrepare` owns canvas growth and mask preparation.
- `Krea2AnyPaintEncode` produces the grounded positive conditioning plus the latent input used by sampling.
- The AnyPaint adapter is loaded with **model-only** LoRA loading.
- `Krea2AnyPaintModelPatch` wraps the diffusion model before sampling.
- Output uses **raw VAE decode** with **no final `ImageCompositeMasked`** step.
- Neo does **not** inject the native `SetLatentNoiseMask`, `DifferentialDiffusion`, or `InpaintModelConditioning` path for AnyPaint.

---

## Compiler authority

Phase 4 deliberately separates compiler ownership:

- `neo_app/providers/comfy_workflows/krea2.py` — Native Krea 2 RAW/Turbo + Identity Edit authority.
- `neo_app/providers/comfy_workflows/krea2_anypaint.py` — AnyPaint-only authority.
- `neo_app/providers/comfy_workflows/lanpaint_family.py` / LanPaint adapters — LanPaint authority.

`ComfyProvider` dispatches `comfy.krea2_anypaint.phase4` directly to the standalone AnyPaint compiler before checking Native Krea compiler IDs. This prevents later Native Krea changes from accidentally changing the AnyPaint graph and vice versa.

---

## Route rules

AnyPaint is available only on:

- `krea2_turbo + diffusion_model + inpaint`
- `krea2_turbo + diffusion_model + outpaint`

AnyPaint is **not** available on:

- Krea 2 RAW native routes
- Krea 2 GGUF routes
- non-Krea families

### Mutual exclusivity

- **Krea 2 Identity Edit** and **AnyPaint** are mutually exclusive.
- **Crop & Stitch** is forced off while AnyPaint is selected.
- **RES4LYF / ClownsharKSampler** stays disabled for AnyPaint.
- **Multi-KSampler** stays disabled for AnyPaint.

---

## Runtime metadata

AnyPaint submissions include:

- `inpaint_engine = krea2_anypaint`
- `masked_edit_engine = krea2_anypaint`
- `crop_stitch_enabled = false`
- `krea2_anypaint_phase = phase5_dedicated_controls` when submitted from the Phase 5 UI
- `krea2_anypaint_execution_enabled = true` when the backend readiness report is green
- `krea2_anypaint_adapter`
- `krea2_anypaint_reference_max_edge`
- `krea2_anypaint_boundary_redraw_px`
- `krea2_anypaint_vlm_reference`
- `krea2_anypaint_kv_cache`
- `krea2_anypaint_lora_strength`
- `krea2_anypaint_contract` node IDs for prepare / encode / patch / sampler / decode

---


## Dedicated AnyPaint Controls

Phase 5 adds an engine-owned **Krea 2 AnyPaint Controls** panel that appears only while `krea2_anypaint` is the selected masked engine.

The panel exposes the upstream AnyPaint controls directly:

- **AnyPaint Adapter** — populated from the live AnyPaint adapter candidates discovered through Comfy.
- **LoRA Strength** — default `1.0`; this controls the functional AnyPaint adapter only.
- **Boundary Redraw** — `0–256`, default `32`; controls the redraw band around generated-mask boundaries.
- **Reference Max Edge** — `128–768`, step `16`, default `384`; controls semantic-reference resolution.
- **VLM Reference** — default ON; includes the semantic reference in Qwen3-VL conditioning.
- **Reference K/V Cache** — default ON; upstream speed/VRAM optimization.

Native `Mask Grow` / `Mask Blur` are hidden and omitted from the submitted AnyPaint payload because `Krea2AnyPaintPrepare` owns the mask boundary contract. On AnyPaint outpaint routes, the Native outpaint `Feather` field is replaced by a note directing users to **Boundary Redraw**.

The selected values are stored in `state.imageDraft`, included in replay through the normal params restore path, and submitted as `krea2_anypaint_*` runtime fields.

---
## Global LoRA behavior

Global Krea 2 LoRA Stack support remains available.

Neo publishes the standard model-only consumer-rewire profile so user-selected global Krea 2 LoRAs are inserted **upstream of the AnyPaint adapter**.

That keeps the final model path:

`base Krea 2 model -> user model-only LoRAs -> AnyPaint adapter LoRA -> Krea2AnyPaintModelPatch -> KSampler`

---

## UI behavior

On the supported route, Neo shows a **Krea 2 AnyPaint Readiness** card.

It reports:

- node signature status
- adapter availability
- Qwen3-VL-4B candidates
- Qwen Image VAE candidates
- diffusion-model catalog availability
- blockers / warnings / remediation steps

If readiness is green, **Krea 2 AnyPaint** becomes selectable from the masked engine dropdown.

---

## Notable non-goals in this phase

Phase 5 does **not**:

- enable AnyPaint on RAW or GGUF Krea 2 routes
- combine AnyPaint with Identity Edit
- merge AnyPaint into Native Crop & Stitch
- enable alternate sampler backends on top of AnyPaint
- enable Multi-KSampler on top of AnyPaint

## Phase 6 — Outpaint + Canvas Contract

AnyPaint owns its source placement and final canvas geometry. Neo therefore no longer treats AnyPaint outpaint like a normal working-copy outpaint route.

### Source resolution policy

While `krea2_anypaint` is active:

- the source image is preserved at its **original pixel resolution**;
- Neo does **not** insert or imply an `ImageScale` working-copy step;
- the generic Auto / Custom outpaint source-resolution controls are replaced by an **AnyPaint Native Source** status row;
- `outpaint_source_resolution_mode` is normalized to `keep_original` in the compiled metadata.

### 16 px alignment

The upstream AnyPaint helper aligns the physical canvas to a multiple of 16:

`raw_width = source_width + left + right`

`raw_height = source_height + top + bottom`

`final_width = align_up(raw_width, 16)`

`final_height = align_up(raw_height, 16)`

Any alignment remainder is added on the **right** and **bottom** only. The source remains positioned at `(left, top)` without resizing.

Neo mirrors this contract in `neo_app/image/krea2_anypaint_canvas.py` and records:

- source size + whether it is authoritative;
- requested padding;
- raw canvas;
- 16 px alignment delta;
- effective padding after alignment;
- final aligned canvas;
- source placement;
- `source_resize.enabled = false`.

### Runtime metadata

The standalone compiler publishes both:

- `krea2_anypaint_canvas_contract`
- `_neo_krea2_anypaint_canvas_contract`

When source dimensions are known from the uploaded source metadata, the compiler's effective `width` / `height` become the aligned final canvas dimensions. If source dimensions are not known, Neo keeps the requested dimensions as an unverified fallback and lets `Krea2AnyPaintPrepare` determine the physical canvas at runtime.

### Parameter integrity

Parameter Integrity now recognizes AnyPaint aligned canvas dimensions as an intentional `derived_transform` instead of a size mismatch. Workflow-final extraction uses the authoritative AnyPaint contract because the prepare node computes canvas dimensions dynamically rather than serializing width/height as normal latent-node constants.

Example:

- source: `1001 × 1069`
- right padding: `200`
- raw canvas: `1201 × 1069`
- aligned canvas: `1216 × 1072`
- alignment delta: `+15 px right`, `+3 px bottom`

This is valid AnyPaint geometry and must not trigger a pre-queue parameter-integrity block.

## Phase 7 — Runtime Validation + Diagnostics

Phase 7 validates the **final submitted AnyPaint graph** immediately before Comfy queue and then verifies the completed physical output against the Phase 6 canvas contract.

### Pre-queue submitted-graph validation

Neo validates the post-extension graph, not only the compiler's initial graph. This catches late graph mutations before `/prompt` is sent.

The validator proves:

- `Krea2AnyPaintPrepare`, `Krea2AnyPaintEncode`, `LoraLoaderModelOnly`, `Krea2AnyPaintModelPatch`, `KSampler`, `VAEDecode`, and `PreviewImage` are present at the compiler-owned node IDs;
- the source and mask handoffs still point at the expected Comfy input files;
- Prepare receives the expected padding, `boundary_redraw_px`, and `reference_max_edge`;
- Encode receives the expected semantic-reference / known-image / keep-mask outputs and VLM toggle;
- the AnyPaint adapter file and strength match the selected controls;
- Model Patch receives the expected K/V-cache flag and remains directly downstream of the AnyPaint adapter;
- KSampler remains downstream of the AnyPaint Model Patch and Encode outputs;
- the final output remains raw `VAEDecode -> PreviewImage`;
- none of these Native masked-edit classes appear anywhere in the submitted graph:
  - `ImageCompositeMasked`
  - `DifferentialDiffusion`
  - `InpaintModelConditioning`
  - `SetLatentNoiseMask`

A concrete graph mismatch is **fail-closed before queue**.

### Completed-output verification

After Comfy history reports success, Neo reads enough bytes from the output `/view` stream to identify the physical image dimensions and compares them with the Phase 6 AnyPaint canvas contract.

- If the source size was authoritative at submission time, a physical output-size mismatch is recorded as `output_canvas_mismatch`.
- If the source size was unknown at submission time, the physical output becomes runtime truth; prediction drift is a warning instead of a false failure.
- A completed AnyPaint output with runtime warnings is returned as `completed_with_warnings` and is still persisted into Neo_Data.

### Execution proof semantics

Comfy history does not emit an independent success receipt for every non-output dependency node. Therefore Neo reports AnyPaint Prepare, adapter loading, and Model Patch as **`inferred_executed_or_cached`** when the final dependent output completes successfully. This is explicit inference, not a claim that Comfy supplied separate per-node execution receipts.

### Results / Output Inspector

Saved AnyPaint outputs now expose a **Krea 2 AnyPaint Runtime** diagnostics card showing:

- submitted graph state;
- adapter + strength;
- Prepare boundary/reference/padding values;
- Model Patch K/V state;
- expected vs observed canvas;
- forbidden Native node count;
- runtime warnings/errors;
- full diagnostic JSON in Expert mode.

The diagnostic payload is persisted through `_neo_krea2_anypaint_runtime_diagnostics` in the saved output params.
