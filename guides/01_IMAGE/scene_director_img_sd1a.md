# IMG-SD1A — Modern Scene Director Multidim Mask + Basic UI Runtime Hotfix

**Status:** Current hotfix — 2026-08-15  
**Surface:** Image → Generations / Reference → Scene Director  
**Backend:** ComfyUI / ComfyUI Portable

IMG-SD1A hardens the IMG-SD1 modern/basic contract after physical Krea 2 Turbo GGUF validation exposed three runtime/UI gaps.

## 1. Multidimensional mask compatibility

The lightweight modern engine previously emitted:

```text
ConditioningSetMask.set_cond_area = mask bounds
```

That is unsafe for current Krea/Qwen-style image latents that carry an additional singleton latent dimension. Current ComfyUI can resize/broadcast the mask through its multidimensional sampler path, but its `set_area_to_bounds` branch still reduces to a bounding-box helper that assumes a two-dimensional mask. The live failure is:

```text
ValueError: too many values to unpack (expected 2)
```

IMG-SD1A keeps the same full-canvas regional masks but emits:

```text
ConditioningSetMask.set_cond_area = default
```

The mask remains the spatial authority; only the optional 2D bounds derivation is disabled. This change applies to the `lightweight_regional` engine only. Classic SDXL V054 behavior is not changed.

Execution metadata now reports:

```text
set_cond_area = default
set_cond_area_reason = multidim_safe_mask_conditioning
```

## 2. Metadata-less Krea LoRAs

LoRA rows can arrive from the catalog with family sentinel values such as:

```text
unknown
auto
unspecified
unset
none
verify
```

IMG-SD1 treated the literal token `unknown` as an explicit non-Krea declaration and rejected the binding before `NeoRegionalLoRADelta` could perform runtime preflight.

IMG-SD1A treats these sentinel values as **missing metadata**. They enter the existing Krea runtime-preflight path:

```text
compatible = unknown
state = unknown_runtime_preflight_required
```

An explicitly declared incompatible family such as `sdxl` remains rejected.

## 3. Legacy Image panel parity

Physical use showed the active Scene Director surface was still the legacy/core Image panel (`source = legacy_image_panel`), while the IMG-SD1 Basic routing UI work had mainly corrected the extension-owned editor.

IMG-SD1A brings the core panel to the same contract:

### Modern lightweight families

- Krea 2 RAW / Turbo
- FLUX.2 Klein
- Z-Image / Z-Image Turbo

These routes are now **Basic-only** in the core Image panel:

- no Scene Mode selector;
- no Advanced Scene Controls block;
- no visible Advanced Region Control block;
- advanced V054 values may remain saved for SDXL replay but are not submitted to the modern route.

### Basic region card

Primary Extension Routing now lives directly in the normal region card:

- LoRA Stack row;
- ControlNet unit;
- ADetailer pass;
- IP Adapter unit/profile;
- mask mode.

For classic SDXL, primary Extension Routing remains in the basic region card as well. Route-specific expert tuning is retained under **Route Advanced Settings** inside Classic V054 Advanced Region Control.

## 4. Regional LoRA submit/execution truth

Provider proof now compares the browser submit-authority row list against the final Scene Director regional-LoRA contract.

If the browser submitted two region-assigned LoRA row IDs but only one or zero survives compatibility/routing validation, Neo blocks before queue instead of silently running prompt-only regional Scene Director.

Proof fields include:

```text
regional_lora_expected_row_ids
regional_lora_expected_count
regional_lora_route_count
regional_lora_request_preserved
```

This is in addition to the existing one-wrapper and compile-status checks.

## Runtime target

For the validated Krea 2 Turbo GGUF case:

```text
2 regional prompts
2 assigned Krea LoRA rows
1 provider KSampler
ConditioningSetMask(set_cond_area=default)
1 NeoRegionalLoRADelta
no global LoraLoader fallback
```

The provider must fail closed if the two requested regional LoRA rows cannot both reach the regional runtime contract.
