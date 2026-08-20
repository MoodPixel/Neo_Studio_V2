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
version: 2
updated: 2026-08-17
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

## Native audio behavior

H3 can create video and stereo audio together. Use the main prompt to describe the sound you want rather than treating audio as a separate finishing step.

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
4. Confirm the required H3 model/encoder/video-VAE/audio-VAE assets are visible in the route controls.
5. For Reference-to-Video, add references in the shared **Reference Inputs** panel and stay within the displayed limits.
6. Write the prompt, including H3 reference labels where appropriate.
7. Generate and inspect the saved result in Results / Output Inspector.

## Troubleshooting

**Reference-to-Video will not accept another file**  
Check both the per-type counter and the total counter. H3 allows 9 images, 3 videos, 3 standalone audios, and 12 references total.

**Only audio references are loaded**  
Add at least one picture or video reference. Standalone audio cannot be the only H3 reference modality.

**A reference seems ignored**  
Use the correct `<Picture n>`, `<Video n>`, or `<Audio n>` label and state exactly what that reference should control.

**First/last image behavior is wrong**  
Check **Keyframe Role**. A first/last keyframe is a temporal anchor; use Reference-to-Video when you only want semantic guidance.

**Generation runs out of memory**  
Try the low-VRAM profile, reduce workload where the route permits it, use Reference Image Size **Match**, or disable optional accelerators/extra processing while diagnosing the base route.

**Spectrum and T8 cannot both be enabled**  
That is intentional. Choose only one H3 approximate accelerator.

For the shared reference-input behavior, see `guides/02_VIDEO/video_reference_inputs.md`.
