---
guide_id: video.minimax_h3_local_support
title: MiniMax H3 Local Audio-Video Support
surface: video
scope: built_in
applies_to:
  - video_generation
  - comfyui
  - comfyui_portable
  - minimax_h3
  - multimodal_reference
tags:
  - video
  - minimax h3
  - native audio
  - reference video
  - comfyui
priority: 82
version: 4
updated: 2026-08-22
---

# MiniMax H3 Local Audio-Video Support

MiniMax H3 is available as a local Video family when your selected ComfyUI backend has the required H3 models and nodes. H3 can generate synchronized video and stereo audio in the same workflow and supports several kinds of visual/reference control.

## Choose the right H3 workflow

### Text to Audio-Video

Use **Text-to-Video** when you want H3 to create the scene from the prompt without a keyframe image.

Describe both visual and audio intent in the prompt when needed: dialogue, ambience, sound effects, music, camera movement, and character action can all be part of the generation request.

### Single keyframe

Use **Image-to-Video** with one source image when the image should act as a temporal anchor.

H3 exposes **Keyframe Role**:

- **First** — the image anchors the beginning of the clip.
- **Last** — the image anchors the end of the clip.

A keyframe is different from a semantic reference. If you want an image to guide identity or appearance without forcing it to be the first/last frame, use **Reference-to-Video** instead.

### First + Last Frame

Use **First + Last Frame** when both ends of the shot need hard visual anchors. Add the required first and last images, then describe the motion and transition between them.

### Reference-to-Video / Ref2VA

Use **Reference-to-Video** when you want H3 to reuse identity, wardrobe, objects, environment, motion, or audio cues from several references.

Neo uses the same shared **Reference Inputs** panel used by other reference-capable Video providers, but H3 keeps its own limits:

- up to **9 reference images**;
- up to **3 reference videos**;
- up to **3 standalone reference audio clips**;
- up to **12 files total** across all reference types;
- standalone audio cannot be the only reference type;
- a reference video may also contribute its own soundtrack.

The panel shows the current per-type and total counts and stops additional uploads when the active H3 limit is reached.

### Video Editing / Video-to-Video

Use **Video-to-Video** when an existing clip is the thing you want H3 to edit, restyle, or transform. Neo exposes this as a first-class Video Editing workflow, but the backend semantics stay honest: it is implemented with the H3 **Ref2VA** checkpoint and `MiniMaxH3ReferenceToVideo`, not a separate Vid2Vid checkpoint.

The source panel lets you either upload a source clip or reuse the selected Neo Video result. Neo reserves that source as **`<Video 1>`** and can also pass its synchronized soundtrack as the paired H3 video-audio reference. You can disable the source-soundtrack reference when only the source visuals/motion should guide the edit.

Because the source clip consumes one Ref2VA video/file slot, Video Editing exposes the remaining optional reference budget as:

- up to **9 extra images**;
- up to **2 extra videos**;
- up to **3 standalone audio clips**;
- up to **11 extra files total**, keeping the complete H3 request within the native 12-file limit.

Optional extra references use the same ordered reference panel. Extra videos begin at `<Video 2>` because `<Video 1>` is always the edit source.

## Referencing media in the prompt

H3 uses numbered labels according to reference order:

```text
<Picture 1>
<Video 1>
<Audio 1>
```

Example:

```text
<Picture 1> preserves the lead character's face and wardrobe.
<Video 1> provides the running motion and handheld camera rhythm.
<Audio 1> provides the speaker's voice identity and delivery style.
```

After removing or replacing a reference, recheck the numbering in your prompt.

Reference video/audio material is intended for short clips in the H3 workflow. Neo validates the accepted file type and reference counts before generation; ComfyUI/H3 remains the final authority for media-level compatibility.

## Reference image fidelity

For Ref2VA images, **Reference Image Size** controls how much source-image detail is retained:

- **Match** — scales references toward the generation pixel area and generally costs less memory/compute.
- **Max** — keeps more reference resolution for stronger fine-detail/identity guidance, with higher resource use.

Start with **Match** and use **Max** when the reference loses important face, clothing, or object detail.

## Mixed-format text encoders

MiniMax H3 treats the diffusion-model container and the Qwen3-VL text-encoder container as separate choices. The connected ComfyUI `/object_info` catalogs remain authoritative.

Supported topology includes:

- native/Safetensors (including INT/FP8/scaled Safetensors) H3 diffusion model + native/Safetensors Qwen3-VL encoder;
- native/Safetensors H3 diffusion model + **GGUF Qwen3-VL encoder** when a compatible H3 GGUF CLIP loader is installed;
- GGUF H3 diffusion model + GGUF Qwen3-VL encoder;
- GGUF H3 diffusion model + native/Safetensors Qwen3-VL encoder when the native loader is available.

Neo currently recognizes the normal `CLIPLoader` for native encoders and H3-capable GGUF loader classes such as `H3ClipLoaderAny`, `VideoCLIPLoaderGGUF`, `CLIPLoaderGGUFAdvanced`, and `CLIPLoaderGGUF` when they are advertised by the live backend. The selected encoder filename determines which encoder-loader family is used; the main H3 model loader does not force the encoder format.

Some third-party GGUF Qwen3-VL conversions use loader-owned projector/MMProj companion data. Neo does not fabricate or pair arbitrary sidecars. If a conversion requires one, install/use the compatible H3 loader/node pack that owns that conversion contract.

## Capability boundary

Neo's local H3-Base surface intentionally exposes only capabilities that map to the installed H3-Base model/node contract:

| Neo workflow | H3 backend semantics | Local status |
|---|---|---|
| Text-to-Video | FL2VA with no keyframe; native A/V output | Enabled |
| Image-to-Video | FL2VA with one first **or** last keyframe | Enabled |
| First + Last Frame | FL2VA with both temporal anchors | Enabled |
| Reference-to-Video | Ref2VA multimodal references | Enabled |
| Video-to-Video / Video Editing | Ref2VA with source video reserved as `<Video 1>` | Enabled |
| Audio-only reference generation | Not a valid local Ref2VA input shape | Not exposed |
| H3-Context-IR | Separate upstream stage/system | Not claimed as local H3-Base |
| H3-Regenerate-2K | Separate upstream stage/system | Not claimed as local H3-Base |

This prevents the UI from advertising modes that cannot compile into a real local graph.

## Native audio behavior

H3 can create video and stereo audio together. Neo therefore exposes **two independent VAE selectors** in the H3 **Models** parameter group:

- **H3 Video VAE** — used for visual latent encode/decode;
- **H3 Audio VAE** — used for native 32 kHz stereo audio latent encode/decode.

Both selectors are populated from the connected ComfyUI VAE catalog. The H3 discovery layer separates video- and audio-VAE candidates instead of treating one VAE selection as authority for both. The selected Audio VAE is submitted in `audio_vae_name` and is wired into `MiniMaxH3ReferenceToVideo` where applicable plus `VAEDecodeAudio` for native audio output.

The Audio VAE control is also present in Neo's **frontend fallback parameter profile**. That matters when the canonical `/api/video/parameter-profile` response is still loading or temporarily unavailable: H3 must still render all four Models controls — Model, Text Encoder, Video VAE, and Audio VAE — rather than silently dropping the audio selector.

Use the main prompt to describe the sound you want rather than treating H3 audio as a separate finishing step.

Examples:

```text
He whispers, "Don't turn around," while rain hits the windows.
```

```text
Distant traffic, soft room tone, no music; the character laughs quietly after the line.
```

If you only need visual output, keep the prompt focused on the visual scene and use the available H3 audio controls as appropriate for the active route.

## Timing and canvas

The current Neo H3 local workflow uses **24 FPS** and H3-compatible frame alignment. The normal quality profile starts from a native-quality 16:9 baseline, while the low-VRAM profile deliberately reduces workload for compatibility/preview testing.

Use the normal quality lane for final work when your hardware can handle it. The low-VRAM lane is useful for confirming prompts, references, and route setup before committing to a heavier generation.

## Speed controls

Neo keeps H3 speed options explicit rather than silently stacking them.

### Native baseline

Use the normal H3 model and sampler for the most straightforward quality/reference workflow.

### Sage Attention

Sage is an optional attention optimization and can be used independently when supported by the active backend.

### H3 Turbo

Turbo LoRAs are optional speed profiles. They can reduce generation time but may change fidelity, composition, motion, or audio. Use the provided Turbo preset as a starting point, then tune the exposed values if needed.

### H3 Accelerator

When available, Neo exposes:

```text
Off | Spectrum | T8 BlockCache
```

Choose **one** approximate accelerator. Spectrum and T8 BlockCache are mutually exclusive and Neo will not stack both on the same H3 generation.

For important final shots, compare a speed-optimized result against the native baseline before deciding which version to keep.

## Finish workflow

H3 generation and Finish are separate. Generate the base H3 clip first, inspect it in Results, then use compatible Finish tools for interpolation, upscaling, repair, or export as a non-destructive child result.

## Setup checklist

1. Connect/test the ComfyUI profile you want to use.
2. Select **MiniMax H3** as the Video family.
3. Choose the available H3 model route and workflow mode.
4. In **Models**, confirm the H3 diffusion model, text encoder, **H3 Video VAE**, and **H3 Audio VAE** selections are correct.
5. For Reference-to-Video, add references in the shared **Reference Inputs** panel and stay within the displayed limits.
6. For Video-to-Video, upload/reuse the source video, choose whether its soundtrack should be referenced, then add any optional extra references.
7. Write the prompt, including H3 reference labels where appropriate.
8. Generate and inspect the saved result in Results / Output Inspector.

## Troubleshooting

**Generate stays on Preparing Video generation… and Comfy receives no prompt**  
Current Neo builds fail closed before queue submission: a payload/pre-queue JavaScript failure should end the progress state and show a concrete preparation or queue-handoff error instead of running indefinitely. If an older build stays at the initial Preparing state, apply the current frontend files and hard-refresh the Neo page so the restored shared reference helper is loaded.

**Reference-to-Video will not accept another file**  
Check both the per-type counter and the total counter. H3 allows 9 images, 3 videos, 3 standalone audios, and 12 references total.

**H3 Audio VAE is missing from Models**  
Current builds render the Audio VAE selector from both the canonical parameter profile and the frontend fallback profile. Hard-refresh Neo after applying the current frontend file. If the selector appears but has no usable Audio VAE option, run **Test/Refresh backend** and verify ComfyUI exposes the Audio VAE through `VAELoader`.

**Video-to-Video says the source is missing**  
Use the H3 Video Edit source panel to upload a video or choose **Use Selected Neo Result**. The generic LTX selected-result source state is not used as hidden H3 authority.

**Only audio references are loaded**  
Add at least one picture or video reference. Standalone audio cannot be the only H3 reference modality.

**A reference seems ignored**  
Use the correct `<Picture n>`, `<Video n>`, or `<Audio n>` label and state exactly what that reference should control.

**First/last image behavior is wrong**  
Check **Keyframe Role**. A first/last keyframe is a temporal anchor; use Reference-to-Video when you only want semantic guidance.

**GGUF H3 text encoder is missing from the Text Encoder dropdown**  
Connect/Test the active ComfyUI Video backend again so Neo refreshes live `/object_info`. MiniMax H3 merges native and supported GGUF encoder catalogs regardless of whether the main H3 model is Safetensors/UNET or GGUF. If the GGUF file is still absent, confirm that your installed H3 GGUF CLIP loader actually advertises that file in its `clip_name`/text-encoder dropdown.

**The same H3/Video model reloads every generation**  
Open the Video Backend Probe and inspect the model-residency section. Normal Neo Video generation does not call Comfy `/free`. If **Neo forced unload** and **Neo /free during normal Video generation** are both false and explicit CPU-offload/block-swap is off, capture the Comfy terminal lines at the end of run 1 and start of run 2. The loader/custom node or VRAM pressure is then the likely residency authority.

**Generation runs out of memory**  
Try the low-VRAM profile, reduce workload where the route permits it, use Reference Image Size **Match**, or disable optional accelerators/extra processing while diagnosing the base route.

**Spectrum and T8 cannot both be enabled**  
That is intentional. Choose only one H3 approximate accelerator.

For the shared reference-input behavior, see `guides/02_VIDEO/video_reference_inputs.md`.

## Source readiness in Img2Vid

MiniMax H3 **Img2Vid** uses the Video workspace's source-image state. After a source image is uploaded successfully, the workspace readiness gate should recognize that image immediately and enable Compile/Generate when the backend and route are otherwise ready.

Reference media is separate from an Img2Vid source image. H3 **Reference-to-Video** checks the shared Reference Inputs collection instead of the Img2Vid source field. A reference image guides the generated video; it is not treated as frame zero unless the selected workflow explicitly uses it as a source/keyframe.

If the header still reports a missing source after the preview/path is visible, refresh the Video backend once and verify that the selected workflow is **Img2Vid**, not **Reference-to-Video**.

## Live preview and final playback

MiniMax H3 local jobs are queued with a Neo-owned ComfyUI `client_id`. When the connected ComfyUI backend emits websocket preview frames, Video → Preview shows the latest live sampling frame while the job runs. After sampling, the status changes through decode / assemble / save stages and the finished `SaveVideo` output is imported into the normal Video result player.

If Image live preview works but H3 does not, refresh/restart Neo after applying the current files so the H3 queue payload includes the client id. ComfyUI itself must also have preview generation enabled; Neo cannot invent latent preview frames when the backend emits none.


## 2026-08-22 — Final playback + model-row hotfix

### Final Comfy video import

Neo's live sampler frame is temporary. The final Preview player switches to the completed Neo-owned video only after Neo imports the Comfy `SaveVideo` result from `/history/{prompt_id}` and `/view`.

Core Comfy `SaveVideo` can report the final MP4 through the historical `images` UI bucket, and different Comfy/proxy builds may represent its location as either `filename` + `subfolder`, a single `path` such as `video/MyClip.mp4`, or a nested `ui.images` payload. Neo now normalizes all of those shapes and preserves the `video/` subfolder when requesting the file from `/view`.

The generation poller is bound to the exact queued Neo result id. An `execution_success` websocket event triggers an immediate import attempt, while the normal result poller remains as fallback. Import failures are surfaced as retry/status text instead of silently leaving the last live preview frame visible.

### Local Models row

The canonical Python parameter profile now owns generic Model / Text Encoder / VAE fields for WAN and LTX as well as the H3-specific four-field model contract. MiniMax H3 must render:

```text
H3 Diffusion Model | H3 Text Encoder | H3 Video VAE | H3 Audio VAE
```

WAN/LTX canonical profiles must not lose their base model selectors simply because `/api/video/parameter-profile` is available. The frontend fallback remains a degraded-mode safety net, not the primary source of these controls.
