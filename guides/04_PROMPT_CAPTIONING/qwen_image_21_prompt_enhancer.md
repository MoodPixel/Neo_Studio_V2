---
guide_id: prompt_captioning.qwen_image_21_prompt_enhancer
title: Qwen Image 2.1 Prompt Enhancer
surface: prompt_captioning
scope: built_in
applies_to:
  - prompt_captioning_workspace
  - prompt_studio
  - caption_studio
  - qwen_image_21_pe
  - comfyui
  - text_to_image
  - image_edit
tags:
  - prompt enhancer
  - qwen image 2.1
  - comfyui
  - prompt studio
  - caption studio
priority: 80
version: 1
updated: 2026-09-26
---

# Qwen Image 2.1 Prompt Enhancer

## Current availability

Neo Studio now has executable Qwen Image 2.1 prompt-enhancer routes for both **Text -> Image** in Prompt Studio and **Image Edit** in Caption Studio, plus durable QPE results, explicit ratio decisions, history, staged replay, and an **Image workspace shortcut** for Qwen Image 2.1. All three entry points reuse the same Prompt & Captioning-owned QPE runtime; the Image workspace does not duplicate the enhancer engine.

Current placement:

```text
Prompt Studio / Text -> Image
  -> Qwen Image 2.1 PE T2I        ✅ implemented; physical runtime test pending

Caption Studio / Image Edit
  -> Qwen Image 2.1 PE Edit       ✅ implemented; physical runtime test pending
```

The Prompt Studio card appears only for **Text -> Image**. It lets you choose the Comfy runtime, the discovered PE T2I checkpoint, and optional enhancer sampling controls. The enhancer uses the current **Prompt output** when one exists; otherwise it uses **Source text / idea**. The rewritten prompt returns to Prompt output.

The Caption Studio PE card appears only for the **Image Edit** task. Caption Studio's normal source image is fixed as **Image 1**. You can then add, reorder, or remove up to nine more references, giving an explicit ordered contract from **Image 2** through **Image 10**. The visible order is the execution order and is preserved as `image_N` / `<imageN>` semantics in the external Edit node.

PE results can carry `wh_ratio` and, for Edit, `ratio_follow`. These begin as **advisory suggestions only**. QPE-4 lets you explicitly accept one suggestion into the durable QPE record for replay and future Image handoff, but acceptance still does **not** resize the Image workspace or apply a source-image ratio. If the external node reports `parse_ok=false`, Neo keeps the fallback rewritten text for manual review, discards both ratio fields, and does not allow a ratio choice to be accepted.

Neo unloads the PE model after a normal enhancement/rewrite run so these large specialist text encoders do not remain resident on the shared Comfy GPU.

## Shared Comfy runtime discovery

This **shared Comfy runtime** contract means QPE does **not** require a separate Image Comfy profile. Neo now accepts any compatible connected Comfy runtime whose live `/object_info` proves the QPE node/model/bridge contract. In normal setups this means either:

- an Image **ComfyUI / ComfyUI Portable** profile; or
- the existing **Prompt & Captioning ComfyUI LLM/VLM** profile.

Connected compatible runtimes are ranked ahead of disconnected profiles. A connected Image Comfy runtime is preferred when it is QPE-ready; otherwise Neo can reuse a QPE-ready Prompt & Captioning Comfy runtime. This avoids making users configure the same Comfy server twice just to run prompt enhancement.

The Prompt & Captioning Comfy profile still has its normal llama.cpp/VLM readiness contract for its general text/caption tasks. QPE readiness is discovered independently from the same Comfy `/object_info`; QPE itself does **not** require llama.cpp or MMProj.

## Required ComfyUI custom node

Install:

```text
benjiyaya/ComfyUI-Qwen-Image-2.1-Prompt-Enhancer
```

Neo looks for these two node classes:

```text
QwenImage21_T2IPromptRewrite
QwenImage21_EditPromptRewrite
```

The Edit node must expose its ordered `image_1` through `image_10` reference inputs for Neo to consider the full Edit dependency compatible.

Use:

```text
Admin -> Extensions -> Node Manager
```

or install the repository manually into ComfyUI's `custom_nodes` folder, then restart ComfyUI.

Neo does not install or update the node automatically.

## Required prompt-enhancer models

Place the PE checkpoints under:

```text
ComfyUI/models/text_encoders/
```

Known Comfy-Org packaged files:

```text
Text -> Image
qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors

Image Edit
qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors
```

Neo distinguishes the two roles instead of treating every Qwen text encoder as a prompt-enhancer model. A normal Qwen/Qwen3.5 text encoder is therefore not shown as a PE candidate just because its filename contains `qwen`.

The PE files remain normal ComfyUI text encoders and can coexist with the standard Qwen Image 2.1 text encoder used for image generation.

## Native ComfyUI requirement

The connected ComfyUI build must expose native:

```text
CLIPLoader
```

with:

```text
type = qwen_image
```

If the loader exists but does not advertise `qwen_image`, update ComfyUI and restart it before testing again.

## Neo image bridge for Edit

The image-aware Edit enhancer reuses Neo's bundled:

```text
NeoPromptCaptionImageInput
```

If Neo reports that this bridge is missing, recopy the bundled `neo_prompt_captioning` folder into:

```text
ComfyUI/custom_nodes/neo_prompt_captioning
```

then restart ComfyUI and Connect/Test again.

## Caption Studio Edit runtime

The Edit compiler uses the ordered graph below:

```text
CLIPLoader(type=qwen_image, PE-I2I checkpoint)
        │
        ├── NeoPromptCaptionImageInput(Image 1) ─┐
        ├── NeoPromptCaptionImageInput(Image 2) ─┤
        ├── ...                                  ├─> QwenImage21_EditPromptRewrite
        └── NeoPromptCaptionImageInput(Image 10) ┘
                                                   │
                                                   └─> NeoQwenImage21PromptEnhancerOutput
```

The runtime preserves the external wrapper defaults unless you change the advanced controls:

```text
temperature      = 1.0
top_p            = 0.95
presence_penalty = 0.0
max_length       = 24000
seed             = 42
```

Reference rules:

- at least one image is required;
- Caption Studio source is always slot 1;
- extra uploads fill slots 2-10 in the exact visible order;
- moving/removing an extra reference compacts the numbering without changing the primary source;
- local file references are accepted only from Neo-managed Prompt & Captioning asset folders; remote URLs are not read by the QPE runtime;
- `ratio_follow` such as `<image1>` is displayed to the user but is not automatically applied.

## Durable QPE results

Every successful T2I or Edit enhancer run now writes a dedicated QPE result record under Neo's Prompt & Captioning runtime data:

```text
neo_data/prompt_captioning/qwen_image_21_pe_history.json
```

The record keeps the source instruction, rewritten positive/negative prompt fields, parse state, `wh_ratio`, `ratio_follow`, accepted ratio decision, Comfy runtime profile, PE model, sampling parameters, prompt id, and execution lineage. Edit records also keep the ordered replayable reference assets for Image 1 through Image 10.

Neo also writes a normal Prompt & Captioning `result_metadata` record for each successful QPE run. This keeps the specialist enhancer aligned with the existing metadata/replay infrastructure instead of creating an isolated result system. Persistence is best-effort: if a PE run completes successfully but local result storage fails, the rewritten prompt is still returned and Neo reports the persistence warning rather than converting the successful Comfy run into a provider failure.

The model's internal `thinking` text is still excluded from normal saved QPE results and replay records.

## Explicit ratio decisions

A structured QPE result may offer either:

```text
wh_ratio     -> a recommended output aspect ratio
ratio_follow -> a recommendation to inherit the canvas ratio from a particular reference
```

The result card exposes explicit actions such as **Accept ratio 16:9** or **Accept canvas <image1>**. Neo stores the choice as:

```text
ratio_selection = { kind: "wh_ratio" | "ratio_follow" | "none", value: "..." }
```

This decision is durable and replayable, but **QPE-4 never writes Image workspace width/height or changes the current canvas**. The accepted value is reserved for the later Image handoff integration. You can clear the accepted choice at any time.

A ratio/canvas suggestion can be accepted only when `parse_ok=true` and the corresponding structured field exists. Fallback text from a failed JSON parse remains usable as prompt text but cannot silently become a canvas decision.

## QPE history and replay

Prompt Studio and Caption Studio now expose recent QPE history with explicit **Replay** actions. Replay is a staging operation only; it does not execute the PE model again.

T2I replay restores:

- Prompt Studio / Text -> Image mode;
- original source prompt and rewritten prompt;
- PE runtime profile, checkpoint, and sampling parameters;
- structured ratio suggestions and accepted ratio choice;
- result/metadata lineage.

Edit replay restores:

- Caption Studio / Image Edit mode;
- original edit instruction and rewritten edit prompt;
- PE runtime profile, checkpoint, and sampling parameters;
- Image 1 plus stored Images 2-10 when the saved assets remain replayable;
- structured ratio suggestions and accepted ratio choice;
- result/metadata lineage.

If an Edit reference was inline-only or is otherwise not replayable, Neo stages the rest of the replay and clearly lists the missing reference slot(s) so you can re-add them before running the enhancer again. Replay does not fetch arbitrary remote URLs or bypass the existing Prompt & Captioning asset boundary.

## Image workspace shortcut

When **Qwen Image 2.1** is the active Image family, the main Image prompt panel now exposes **Enhance with Qwen 2.1 PE** as a convenience handoff to the same Prompt & Captioning-owned QPE engine. The shortcut deliberately does not duplicate the full Prompt Studio / Caption Studio enhancer interface.

Task selection follows the active Image workflow:

```text
Txt2Img / Generate -> PE-T2I
Img2Img / Edit      -> PE-I2I Edit
Inpaint / Outpaint  -> PE-I2I Edit
```

For Edit-capable modes, Neo forwards the Image workspace's current ordered Qwen references as `Image 1` through `Image 10`. These files remain Neo-managed Image source assets under `neo_data/inputs/image`; QPE-5 expands the safe Edit-reference boundary to accept that folder and `/api/image/source-file/<name>` references directly, without copying the images into Caption Studio first. Arbitrary local filesystem paths and remote URLs remain rejected.

The Image shortcut is **review-first**:

1. click **Enhance with Qwen 2.1 PE**;
2. inspect the returned enhanced prompt and any structured canvas suggestion;
3. choose **Use Enhanced Prompt** to replace only the Image positive prompt; or
4. choose **Use Prompt + ...** to explicitly accept the saved QPE ratio/canvas suggestion and apply it to the Image workspace.

A canvas suggestion is never applied merely because enhancement completed. `wh_ratio` keeps roughly the current Image pixel area while changing aspect ratio and respecting Neo's resolution alignment. For Img2Img/Edit, an accepted `<image1>` `ratio_follow` restores Qwen's **Source canvas** policy; an accepted `<imageN>` for references 2-10 uses that reference's aspect ratio with the current output pixel area and switches to **Custom canvas**.

Inpaint and Outpaint deliberately keep mask/padding ownership. Their PE prompt can be used, but the Image shortcut does not apply PE canvas suggestions to those modes.

Image-originated QPE results still enter the shared durable QPE history/replay system and are marked with `source_surface = image`. This preserves one engine and one history contract regardless of whether enhancement started in Prompt Studio, Caption Studio, or Image.

## Refreshing readiness

After installing/updating the node pack or PE models:

1. restart ComfyUI;
2. open **Admin -> Backends**;
3. select the compatible Comfy profile you want QPE to use — either Image ComfyUI/Portable or Prompt & Captioning ComfyUI LLM/VLM;
4. run **Connect/Test** again.

Neo refreshes the live Comfy node list and text-encoder catalog from that backend. If Prompt & Captioning already points to the same Comfy server and is connected, QPE can reuse it directly; a duplicate Image profile is not required.

QPE readiness remains independent from llama.cpp/MMProj even when the selected runtime is the **ComfyUI LLM / VLM** profile. Those dependencies belong to its normal Prompt/Caption tasks, not to the Qwen Image 2.1 PE rewrite graph.

## Licenses

The community custom-node wrapper is MIT licensed. The Qwen prompt-enhancer model weights use the **Qwen Research License**. The model-weight license still applies even though the wrapper code is MIT.

## Runtime notes

The bundled `neo_prompt_captioning` Comfy bridge now includes:

```text
NeoQwenImage21PromptEnhancerOutput
```

This structured terminal returns `positive_prompt`, `negative_prompt`, `wh_ratio`, `ratio_follow`, and `parse_ok` through Comfy history without saving the model's internal `thinking` trace into normal Neo prompt content. After updating Neo, recopy the bundled `neo_prompt_captioning` folder into ComfyUI `custom_nodes`, restart ComfyUI, and run Connect/Test so Neo can detect the new terminal.

## Still to come

- physical VRAM/speed/output qualification across Prompt Studio, Caption Studio, and Image-shortcut T2I/Edit PE runtime choices in QPE-6.
