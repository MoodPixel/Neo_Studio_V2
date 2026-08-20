---
guide_id: video.reference_inputs
title: Video Reference Inputs
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video_reference
  - reference_to_video
  - multimodal_reference
tags:
  - video
  - reference images
  - reference video
  - reference audio
  - provider aware
priority: 84
version: 1
updated: 2026-08-17
---

# Video Reference Inputs

Neo Studio uses a shared **Reference Inputs** panel for Video routes that support semantic reference media.

The panel is provider-aware. It does **not** assume that every model accepts the same number or type of references. When you change backend, model family, loader, or workflow, Neo changes the available reference lanes and counters to match that route.

## Reference media vs a source frame

A **source image** in Image-to-Video usually acts as a temporal or visual starting anchor.

A **reference input** supplies information the model can reuse—such as identity, clothing, objects, style, movement, voice, or environment—without automatically making that media the first frame.

Choose the workflow based on what you want the media to do, not simply on how many files you have.

## Adding references

1. Choose a Video workflow that supports references.
2. Open the **Reference Inputs** area.
3. Add the media types offered by that route.
4. Watch the per-type and total counters.
5. Describe the purpose of each reference clearly in the prompt.
6. Generate normally.

When a route reaches its limit, Neo disables additional uploads for that reference type or for the overall reference set.

## Current examples

### Grok Reference-to-Video

- images: up to 7;
- video references: not used;
- audio references: not used;
- total: up to 7.

Prompt labels use `<IMAGE_1>`, `<IMAGE_2>`, and so on.

### MiniMax H3 Ref2VA

- images: up to 9;
- videos: up to 3;
- standalone audio clips: up to 3;
- total references: up to 12;
- standalone audio cannot be the only reference type.

H3 prompt labels use `<Picture n>`, `<Video n>`, and `<Audio n>`.

These limits belong to the active provider/route. Neo may show different limits when another Video backend gains reference support later.

## Prompting references

Give each reference one clear job. For example:

```text
<IMAGE_1> defines the main character's face.
<IMAGE_2> defines his wardrobe.
<IMAGE_3> defines the apartment lighting and color palette.
```

For MiniMax H3, use its route-specific labels instead:

```text
<Picture 1> keeps the character identity and clothing.
<Video 1> supplies the running motion and camera rhythm.
<Audio 1> supplies the voice identity.
```

Avoid asking several references to define the same feature in conflicting ways unless you intentionally want the model to blend them.

## Removing or replacing a reference

Remove the existing reference, then upload its replacement. After changing the order, update any numbered reference labels in the prompt so they still point to the intended media.

## Troubleshooting

**The Add button is disabled**  
You reached either the per-type limit or the overall route limit.

**A reference type is not shown**  
The active route does not support that media type. Neo hides unsupported lanes instead of submitting media the provider will ignore.

**The result ignores one reference**  
Make the reference's role explicit in the prompt and reduce conflicts between sources. For identity-critical work, use clear, consistent references with similar appearance details.

**The numbered prompt labels changed meaning**  
Reference numbering follows the current order. Recheck the prompt after removing or reordering inputs.
