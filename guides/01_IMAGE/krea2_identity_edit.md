# Krea 2 Identity Edit

## Edit weight source

Krea 2 Identity Edit separates the **runtime engine** from the **edit weights** used by that engine.

When **Krea 2 Identity Edit v1.2** is selected, Neo exposes **Edit Weight Source**:

- **Separate LoRA** — original behavior. Neo requires a compatible Identity Edit LoRA, validates it against the connected ComfyUI `LoraLoaderModelOnly` catalog, applies the selected strength, and then runs `Krea2EditModelPatch`.
- **Baked into Model** — use this when the selected Krea 2 diffusion model already has the Identity Edit LoRA merged/baked into its weights. Neo does not require or load a second Identity Edit LoRA, but still runs the Identity Edit patch and grounded Qwen3-VL conditioning.

The default remains **Separate LoRA** so existing saved drafts and older payloads keep their previous behavior.

> Important: baking the LoRA into the model does not replace the Identity Edit runtime. `Krea2EditModelPatch`, `Krea2EditGroundedEncode`, and the target latent path are still required.

### Graph behavior

Separate LoRA:

```text
Krea model
  -> global/style LoRA Stack rows, when enabled
  -> dedicated Identity Edit LoRA
  -> Krea2EditModelPatch
  -> sampler
```

Baked into Model:

```text
Krea model with baked Identity Edit weights
  -> global/style LoRA Stack rows, when enabled
  -> Krea2EditModelPatch
  -> sampler
```

Normal LoRA Stack rows remain independent from the engine-owned Identity Edit weight source. A baked Identity model can still use supported global/style/character LoRAs.

### Safe model switching

Baked/separate is a property of the selected model package, not a global Krea preference. When you manually change the Krea model, family, loader, or edit engine, Neo resets the weight source to **Separate LoRA**. Choose **Baked into Model** again only when the newly selected model actually contains the Identity Edit weights.

Replay is different: a saved result records the effective `krea2_edit_weight_source`, so replaying a baked result keeps baked mode instead of silently restoring a separate edit LoRA.

## Masked-edit engine compatibility

When **Krea 2 Identity Edit** is enabled for Inpaint or Outpaint, Neo uses the Identity Edit workflow together with the **Native Inpaint/Outpaint** masked path.

While Identity Edit is active:

- **Native Inpaint / Outpaint** — available
- **Krea 2 AnyPaint** — unavailable
- **LanPaint** — unavailable

AnyPaint and LanPaint own separate masked-edit workflow architectures, so Neo does not stack them on top of Krea 2 Identity Edit. The UI disables those choices and the backend compile router also fails closed if a manually crafted request tries to combine them.

## Identity Edit controls

Identity Edit controls use a compact responsive layout.

The engine row contains:

- **Krea 2 Edit Engine**
- **Edit Weight Source**

When **Separate LoRA** is selected, Neo also shows:

- **Identity Edit LoRA**
- **Identity Edit LoRA Strength**

When **Baked into Model** is selected, those two controls are hidden because they are not executable in that mode.

The remaining Identity controls stay available in both weight-source modes:

- **Reference Fit**
- **Identity Reference Boost**
- **Scene Reference Boost** when a second reference is active
- **Grounding Resolution**
- optional Identity Edit system prompt
- optional Reference Attention Mask

At narrower window sizes the grid automatically falls back to fewer columns so labels and model names remain readable.

## Ostris / AI Toolkit Krea edit LoRAs

Neo now exposes **Krea 2 Ostris Edit** as a separate Krea edit engine for Img2Img/Edit/Inpaint/Outpaint. It is for Krea 2 edit LoRAs trained with AI Toolkit experimental edit mode (`model_kwargs.edit: true`) and uses `TextEncodeKrea2OstrisEdit` + `Krea2OstrisEditModelPatch`. This is **not** the Identity Edit v1.2 runtime, so do not select the Identity Edit LoRA in the Ostris engine.

With **Separate LoRA**, the **AI Toolkit Edit LoRA** dropdown reads from the normal live Comfy `LoraLoaderModelOnly` LoRA catalog. With **Baked into Model**, that dedicated selector is hidden and Neo skips only the engine-owned edit-LoRA loader.

If an Ostris/AI Toolkit edit LoRA says to enable **KV Cache**, that setting must match how the LoRA was trained. For normally trained edit LoRAs, leave KV Cache off.

Krea-4.1 also hardens socket validation: if Neo has positively discovered the Ostris runtime role but an older capability snapshot omitted the detailed custom-node socket map, that omission is treated as unverified metadata rather than proof that `clip`, `prompt`, `vae`, and `image1` are all missing. Current provider snapshots transport the Ostris node input map explicitly.
