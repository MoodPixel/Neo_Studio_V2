---
guide_id: prompt_captioning.comfy_llamacpp_backend
title: ComfyUI LLM / VLM Backend
surface: prompt_captioning
scope: built_in
applies_to:
  - prompt_builder
  - captioning
tags:
  - comfyui
  - llama.cpp
  - vlm
  - captioning
priority: 72
version: 9
updated: 2026-08-18
---

# ComfyUI LLM / VLM Backend

Neo can use the same ComfyUI server used by Image/Video as an optional local backend for **Prompt Studio** and **Caption Studio**. KoboldCpp remains available; choosing this backend does not replace it globally.

## One-time ComfyUI setup

1. Install a compatible `ComfyUI-llama-cpp_vlm` node pack in ComfyUI **and install that node pack's Python requirements** using the same Python environment that runs ComfyUI.
2. Put your LLM/VLM GGUF files and any required `mmproj` projector files in ComfyUI's `models/LLM` folder.
3. Copy Neo Studio's bundled `neo_prompt_captioning` folder into:

```text
<ComfyUI-root>/custom_nodes/neo_prompt_captioning
```

4. Restart ComfyUI fully.
5. In Neo, open **Admin → Backends**, select **ComfyUI LLM / VLM**, and use **Connect/Test**.

The discovery card should show the llama.cpp loader/instruct nodes plus Neo's **Text Output** bridge. Caption Studio additionally needs the Neo **Image Input** bridge, an image-capable VLM route, a compatible `mmproj`, and a usable chat handler.

If the Comfy drive is short on space, `models/LLM` may point to storage on another drive through an operating-system directory junction/symlink. The llama.cpp model list is recursive, so organizing GGUF and matching `mmproj` files in subfolders is supported as long as Comfy still sees them under its `models/LLM` path.

## Readiness checklist

After **Connect/Test**, Neo now separates connection, installation, model, and saved-selection readiness instead of showing one generic backend status.

The Admin readiness card reports **Prompt Ready** and **Caption Ready** independently and checks:

- ComfyUI server reachability and `/object_info` access;
- `llama_cpp_model_loader` and `llama_cpp_instruct_adv`;
- optional `llama_cpp_parameters` support;
- Neo's `NeoPromptCaptionTextOutput` and `NeoPromptCaptionImageInput` bridge nodes;
- at least one visible LLM/VLM model;
- a VLM `mmproj` projector and usable vision chat handler for Caption Studio;
- whether a saved model, projector, or chat-handler selection still exists in the current live Comfy catalog.

If a saved selection disappears after moving or deleting a model, Neo marks that selection as stale and blocks only the routes that depend on it. Choose a currently discovered component (or restore the missing file), then save the backend settings. Neo re-evaluates readiness immediately against the current catalog; use **Connect/Test** again whenever the Comfy installation or model files themselves change.

A missing optional `llama_cpp_parameters` node does not block execution. Neo marks it as a warning because the llama.cpp node's own generation defaults can still be used.

## Final readiness UI

The Comfy backend now uses a compact status vocabulary instead of exposing raw internal readiness labels:

- **Ready** — Prompt and Caption routes are both ready.
- **Prompt only** — Prompt Studio can run, but Caption Studio still needs a VLM-only dependency or selection.
- **Caption only** — Caption is ready while Prompt is not; this is uncommon but remains explicit.
- **Needs setup** — Comfy is reachable, but a required node, model, bridge, projector, or selection is missing.
- **Offline** — the configured Comfy server is not reachable.
- **Validating** — Neo is refreshing the live Comfy catalog after startup or reconnect.
- **Recovering** — Neo is protecting the shared GPU while it reconciles an uncertain/stale Comfy runtime state.

In **Admin → Backends**, the Comfy card is grouped into Connection, Core nodes, Neo bridge, Prompt route, Caption route, and GPU policy. Neo shows one primary corrective action instead of repeating the same `Next:` instruction on every missing check. Full model catalogs and validation timestamps are available under **Catalog & validation details**.

Inside **Prompt Studio** and **Caption Studio**, Neo shows only the readiness of the route you are currently using. Full installation diagnostics stay in Admin so the creative workspace remains compact.

The backend summary also shows the selected model/projector/handler plus the shared-GPU, unload, and batch-retention policy without duplicating normal Prompt/Caption sampling controls.

## Startup and reconnect validation

Neo restores your saved Comfy LLM/VLM model, projector, chat-handler, context, VRAM, and unload settings when Neo starts, but it does **not** treat an old saved Connected state as current truth. The current Comfy server must be validated again before Prompt or Caption execution is considered ready.

On startup Neo checks enabled ComfyUI LLM/VLM profiles in the background. The readiness card may briefly show **Validating** while Neo refreshes the current Comfy node/model catalog. Once the live catalog is available, saved selections are checked against it and Prompt Ready / Caption Ready are recalculated.

Neo also revalidates the backend before a real Prompt/Caption task is queued. This catches a Comfy restart, removed GGUF/mmproj file, or changed custom-node installation even if the workspace still had an older readiness card open. While Comfy is offline, Neo periodically retries the local readiness probe; when the server returns, the current catalog replaces the stale session snapshot.

The readiness card shows the validation source, when the live catalog was refreshed, and the last successful validation time. A saved model/projector/handler that no longer exists remains visible as a stale selection and blocks only the routes that depend on it; Neo does not silently replace it.

Using **Disconnect** pauses automatic validation for that Comfy LLM/VLM profile until you use **Connect/Test** again.

## Prompt Studio

Choose **ComfyUI LLM / VLM** from the existing Prompt Studio backend selector. Neo keeps the normal Prompt Studio controls and sends the compiled prompt task through the connected Comfy llama.cpp workflow.

Text results return through Neo's bundled `NeoPromptCaptionTextOutput` node, so Prompt Studio receives the same normal text result shape used by other backends.

## Caption Studio

Choose **ComfyUI LLM / VLM** from the existing Caption Studio backend selector and select/upload an image as usual. Neo sends the current image into Comfy through the bundled `NeoPromptCaptionImageInput` node and routes it into the detected llama.cpp VLM instruct node.

The VLM model is unloaded after the request by default so lower-VRAM systems can move on to Image or Video generation without keeping the caption model resident.

## Comfy-specific backend settings

When **ComfyUI LLM / VLM** is selected, Neo shows an extra settings card without changing the normal Prompt Studio or Caption Studio controls.

Common Comfy settings:

- **LLM / VLM Model** — choose a live-discovered model, or leave it on Auto.
- **Chat Handler** — choose a live-discovered handler, or leave it on Auto.
- **Context Length** — controls the llama.cpp context window.
- **VRAM Budget** — limits the GPU-memory target used by the llama.cpp loader. `Unlimited / llama.cpp default` leaves the node unrestricted.
- **Unload After Run** — enabled by default so the local LLM/VLM does not stay resident when you move back to Image or Video generation.

Caption Studio additionally shows **Vision Projector (mmproj)**. Its **Caption VLM Advanced** section contains **Image Analysis Size**, **Image Token Min**, and **Image Token Max**.

These settings belong to the selected backend profile and save automatically. They are hidden when another backend such as KoboldCpp is selected. The normal **Max Tokens**, **Temperature**, **Top-p**, and other shared Prompt/Caption controls remain provider-neutral and are not duplicated.

## Auto resolution

Auto remains available for model, projector, and chat handler:

- Prompt Studio can use the first available main llama.cpp model when no explicit model is saved.
- Caption Studio prefers a model whose filename looks vision-capable, such as Qwen VL, MiniCPM, LLaVA, GLM-V, Gemma Vision, or similar.
- If only one `mmproj` is available, Neo can use it automatically.
- If multiple projectors or handlers are ambiguous, Neo blocks instead of guessing. Choose the exact component in the Comfy backend settings and run again.

## Generic MTMD vision fallback

Caption Studio now resolves **Auto chat handler** against the selected VLM instead of treating any installed vision handler as compatible. Neo prefers a known dedicated handler when the selected model family has one. If no compatible dedicated handler is available and the bundled Neo bridge exposes **Generic MTMD**, Auto can use the model's GGUF chat template plus its matching `mmproj` through Neo's Generic MTMD route.

The Caption settings/status may therefore show a resolved route such as:

```text
Auto → Generic MTMD
```

This is useful for template-driven multimodal GGUFs that are supported by the installed llama.cpp build but do not have a dedicated handler in the third-party Comfy node pack. ToriiGate / Qwen2-VL-style filenames are routed this way instead of being forced through Qwen2.5-VL or another unrelated handler.

Requirements for the Generic MTMD route:

- the selected main VLM GGUF and its matching `mmproj` must both be visible in Comfy's `models/LLM` catalog;
- Neo's updated `neo_prompt_captioning` bridge must be installed in `ComfyUI/custom_nodes/neo_prompt_captioning`;
- the llama.cpp Python build used by ComfyUI must support Generic MTMD and the model must provide a usable multimodal chat template;
- after updating the Neo bridge, restart ComfyUI and use **Connect/Test** so Neo can detect the new Generic MTMD nodes.

Generic MTMD is a compatibility fallback, not a promise that every arbitrary VLM GGUF will work. Models that require a special prompt/vision adapter may still need a dedicated handler. Neo fails closed when no compatible route can be resolved.

When Generic MTMD is active, **Image Analysis Size** still applies. **Image Token Min/Max** are dedicated-handler controls and are hidden because that route does not consume them. Shared-GPU locking, normal-run unloading, runtime recovery, and retained Batch Caption sessions use the same policies as the dedicated llama.cpp route.

## Batch caption sessions

When **Batch Captioning** uses the **ComfyUI LLM / VLM** backend, Neo keeps one VLM session for the batch instead of unloading and reloading the model after every image.

- Neo acquires the shared Comfy GPU slot once for the batch.
- The selected VLM remains loaded between sequential caption items.
- The normal per-item **Unload After Run** behavior is temporarily suppressed inside the batch only.
- If **Unload After Run** is enabled on the backend profile, Neo performs one model/memory cleanup after the final batch item.
- Single-image Caption Studio behavior is unchanged.
- A failed individual caption can be recorded while the batch continues. A fatal Comfy/session failure stops the batch and runs the safe cleanup path.
- **Cancel batch** is cooperative: the current image is allowed to finish, then Neo stops before the next image and finalizes the VLM session.

The Batch progress card shows whether the Comfy VLM session is active, whether the model has been retained, and how many inference items have completed.

## Shared GPU lifecycle

When **ComfyUI LLM / VLM** points at the same local GPU used by Neo Image, Video, or Finish tools, Neo coordinates those jobs through one shared Comfy GPU lifecycle.

- A Prompt/Caption llama.cpp run owns the shared local Comfy GPU slot until its Comfy history reaches a terminal state.
- Image generation, local Video generation, and Video Finish/SeedVR2 wait for the same slot instead of queueing a competing heavy workload at the same time.
- Prompt/Caption runs with **Unload After Run** enabled keep the slot through the final Comfy memory-cleanup handoff, then release it.
- Local Comfy servers on different loopback ports are treated as the same physical GPU group by default. Remote Comfy servers are isolated from the local GPU group.
- If a surface request times out while Comfy is still working, Neo keeps the GPU lease in the background until Comfy reports a terminal history state. This prevents a timeout from accidentally opening the door to a second heavy local job.

Normal use does not require a new switch. Keep **Unload After Run** enabled on lower-VRAM systems and let Neo serialize the handoff automatically. If a request reports that the shared Comfy GPU is busy, wait for the active Comfy job to complete and retry.

After a Comfy Prompt or Caption run finishes and releases the shared GPU slot, you can move directly to **Image → Generate**. Neo reuses the Image draft's normal compiled parameters for the GPU handoff; no Comfy or Neo restart is required between the text/VLM run and the image request.

## If a route is gated

Use **Admin → Backends → ComfyUI LLM / VLM → Connect/Test** and read the **Readiness** card. Neo shows the first blocking item plus the next corrective action. Common causes are:

- ComfyUI is not running;
- the llama.cpp/VLM custom nodes are missing;
- no GGUF model is visible in `models/LLM`;
- Caption Studio has no `mmproj` or no usable vision chat handler;
- the bundled `neo_prompt_captioning` bridge folder was not copied into ComfyUI's `custom_nodes` folder;
- multiple VLM components are available and Neo cannot safely choose a pairing automatically yet.

## Runtime recovery and clear errors

Neo now treats Comfy runtime failures as recoverable states where possible instead of exposing raw HTTP/JSON errors or immediately opening the shared GPU to another heavy job.

Common failures are reported with a plain-language cause and next action, including:

- **GPU memory exhausted** — lower the Comfy VRAM Budget, context/image-analysis size, or use a smaller GGUF/VLM before retrying.
- **Comfy disconnected or restarted** — restart ComfyUI, then use **Connect/Test**. Neo reconciles the old Comfy queue before allowing a competing shared-GPU job.
- **Model or mmproj removed after discovery** — refresh with **Connect/Test** and choose a currently detected model/projector.
- **Required custom node missing or changed** — restart ComfyUI after fixing the node installation, then refresh backend readiness.
- **Workflow validation rejected** — refresh the live node/model catalog and verify the selected model, projector, and chat handler.
- **Explicit cleanup failed** — the completed run is not discarded. Neo records a cleanup warning and retries the memory cleanup before later idle Comfy work.

If Neo loses the response to a queued request, it does **not** assume that nothing ran. The local GPU remains guarded while Neo checks Comfy history and the active queue. Once Comfy is reachable and the old prompt is confirmed absent from both places, Neo safely releases the stale guard.

This also protects Neo restarts: if ComfyUI still has work in its queue when Neo starts again, the first new shared-GPU request waits for that existing Comfy queue to clear. Normally no manual reset is required.

