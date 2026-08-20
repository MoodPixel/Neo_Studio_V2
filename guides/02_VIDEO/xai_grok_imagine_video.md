---
guide_id: video.xai_grok_imagine
title: xAI Grok Imagine Video
surface: video
scope: built_in
applies_to:
  - video_workspace
  - video
  - grok
  - xai_grok
  - grok_imagine_video
  - cloud_api
tags:
  - video
  - grok
  - xai
  - cloud
  - text-to-video
  - image-to-video
  - reference-to-video
  - video-editing
  - video-extension
priority: 97
version: 4
updated: 2026-08-17
---

# xAI Grok Imagine Video

Neo Studio uses Grok Imagine Video inside the normal **Video** workspace. Select the **Grok Imagine Video** backend profile, then choose the workflow you need from **Generation**.

## Available workflows

### Text-to-Video

Use this when the prompt should create the whole shot without a starting image.

You can set the model, prompt, duration, aspect ratio, and resolution when the selected Grok model supports them.

### Image-to-Video

Use this when one image should act as the starting visual anchor for the video.

1. Choose **Image-to-Video**.
2. Add the source image in the Source area.
3. Describe the movement, camera behavior, expressions, environment changes, and other motion in the prompt.
4. Choose the available duration, aspect ratio, and resolution.
5. Generate normally.

Image-to-Video uses one source image. If you want several images to guide identity, wardrobe, objects, or environment instead of using one image as the starting frame, use **Reference-to-Video**.

### Reference-to-Video

Use this when several still images should guide the video without treating one of them as the first frame.

Neo shows the shared **Reference Inputs** panel for this mode. Grok currently accepts **1–7 reference images**. The panel automatically stops accepting more images when the active provider limit is reached.

Reference order matters. You can refer to the images in the prompt as:

```text
<IMAGE_1>
<IMAGE_2>
...
<IMAGE_7>
```

Example:

```text
<IMAGE_1> defines the main character's face and hairstyle.
<IMAGE_2> defines his black jacket and accessories.
<IMAGE_3> defines the rainy neon street environment.
Create a cinematic tracking shot as he walks toward camera, keeping the same identity and wardrobe.
```

Reference-to-Video currently uses **grok-imagine-video**, with a maximum duration of **10 seconds** and a maximum resolution of **720p**.

### Video Editing

Use this when you already have a short video and want Grok to change something inside it.

1. Choose **Video Editing**.
2. Upload an MP4 source video, or use the selected Neo result when that saved result is an MP4.
3. Describe the requested change in the prompt.
4. Generate.

For editing, Neo hides duration, aspect ratio, and resolution controls because those values are inherited from the source video rather than chosen for the edit request. The current Grok edit input limit is **8.7 seconds** and the resulting resolution is capped by the provider at **720p**.

### Video Extension

Use this when a generated or imported clip should continue beyond its current ending.

1. Choose **Video Extension**.
2. Upload an MP4 source video, or use the selected Neo result when that saved result is an MP4.
3. Describe what should happen next.
4. Choose the extension duration.
5. Generate.

The current Grok extension contract accepts source videos from **2–15 seconds** and an extension duration from **2–10 seconds**. Aspect ratio and resolution are inherited from the source, with provider output capped at **720p**.

This works especially well with Neo Results: generate a clip, select that result, send it into Video Extension, then continue building the shot without manually locating the file again.

## Model availability

Neo filters workflow and resolution choices according to the selected Grok model rather than showing unsupported combinations.

- **grok-imagine-video** is the general Grok Video model used for all five Neo workflows: Text-to-Video, Image-to-Video, Reference-to-Video, Video Editing, and Video Extension. It exposes 480p/720p generation.
- **grok-imagine-video-1.5** is currently exposed for **Image-to-Video** only. It can expose 480p, 720p, and 1080p for that workflow. Neo does not show 1.5 for Text-to-Video, Reference-to-Video, Video Editing, or Video Extension while xAI's current official model/capability documentation keeps those modes on the base model.

If a model disappears after changing workflow mode, that means the selected model does not advertise that workflow in Neo's current provider contract. Choose one of the models still shown for that mode.

## Shared Reference Inputs

Neo uses one provider-aware Reference Inputs experience across supported Video backends. The panel changes its allowed media types and counts based on the active route.

For Grok Reference-to-Video:

- media type: images only;
- maximum images: 7;
- maximum total references: 7.

MiniMax H3 uses the same Neo panel but has a different local reference contract. See **MiniMax H3 Local Audio-Video Support** for its image/video/audio limits.

## Using Neo results as source video

Video Editing and Video Extension can use the currently selected Neo Video result as their source. This preserves the relationship between the new result and the previous clip in Neo's result metadata, making chained edits/extensions easier to trace later.

You can still upload an external MP4 instead when the source did not originate in Neo.

## What the controls mean

| Control | When it appears | Purpose |
|---|---|---|
| **Model** | All Grok modes | Chooses a model compatible with the current workflow. |
| **Prompt** | All Grok modes | Describes the requested shot, edit, or continuation. |
| **Source Image** | Image-to-Video | Supplies the single starting image. |
| **Reference Inputs** | Reference-to-Video | Adds up to seven semantic reference images. |
| **Source Video** | Video Editing / Video Extension | Supplies the MP4 that should be edited or continued. |
| **Duration** | Generation modes | Chooses generated clip duration within the active mode limit. |
| **Extension Duration** | Video Extension | Chooses how much new video should be appended. |
| **Aspect Ratio** | Text/Image/Reference generation when supported | Chooses the generated frame shape. |
| **Resolution** | Text/Image/Reference generation when supported | Chooses an allowed provider resolution. |

Neo deliberately hides controls that the active Grok workflow does not accept instead of showing settings that would be ignored.

## Results and progress

Grok Video jobs are asynchronous, but you do not need to manage provider request IDs manually. Neo handles submission and progress, then imports the completed video into the normal Video Results workspace.

Use **Refresh Results** if a completed provider job has not appeared yet. Completed clips should be managed from Results / Output Inspector rather than the Generation preview panel.

## Setup

1. Configure and test your xAI/Grok credential in **Admin → Backends**.
2. Select **Grok Imagine Video** as the Video backend profile.
3. Open **Video → Generation**.
4. Choose the workflow mode.
5. Add the source/reference media required by that mode.
6. Choose a compatible model and available settings.
7. Generate.

## Troubleshooting

**A workflow is missing**  
The selected backend/model does not currently advertise that workflow. Confirm that Grok Imagine Video is the active Video backend and choose a compatible model.

**Reference-to-Video will not accept another image**  
Grok's current limit is seven reference images. Remove an existing reference before adding another.

**Image-to-Video says a source is missing**  
Add one source image. Reference images do not replace the required Image-to-Video source image.

**Video Editing or Extension cannot generate**  
Add an MP4 source video or use the selected Neo result. Check the source-duration limits shown for the mode.

**1080p is unavailable**  
1080p is model/mode specific. Choose a model that exposes 1080p for the active workflow rather than expecting it on every Grok route.

For general Video workspace behavior, see `guides/02_VIDEO/README.md`.
