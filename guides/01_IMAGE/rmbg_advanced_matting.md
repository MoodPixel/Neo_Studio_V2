# RMBG Advanced Matting and High-Resolution Edges — Phase RMBG-5

Phase RMBG-5 adds model-aware advanced matting inside the existing **Image → Finish → Remove Background** panel.

## Supported live profiles

Neo exposes a profile only when the active ComfyUI `/object_info` catalog contains the exact node, its required inputs, and a live model-choice list:

- **BiRefNet HR** — high-resolution general edge preservation;
- **BiRefNet Matting** — soft alpha and fine contours;
- **BiRefNet HR Matting** — high-resolution soft alpha;
- **BiRefNet Lite 2K** — lower-memory high-resolution processing;
- **SDMatte / SDMatte Plus** — trimap-guided matting through `AILab_SDMatte`.

The verified node contracts are `BiRefNetRMBG` and `AILab_SDMatte`, based on the installed [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG) node family. Neo does not infer a model from a filename when the live node does not expose that choice.

## Input policy

Every run uses exactly one source image. BiRefNet profiles can run directly from the source image. SDMatte requires either an uploaded grayscale trimap/mask through the matting mask input or explicit **Use source alpha as trimap/mask** when the source contains a meaningful alpha channel.

Matting masks are stored as Neo-owned input assets and are never represented as personal filesystem paths in browser-visible payloads.

## Edge controls

- **Process resolution** is bounded to 256–2560px and controls the SDMatte workload; BiRefNet HR profiles retain their model-defined high-resolution behavior.
- **High-resolution edges** is the default edge mode.
- **Soft alpha**, **trimap-guided alpha**, and **foreground estimation** are explicit modes recorded in metadata.
- Mask blur, offset, inversion, sensitivity, transparent-object handling, and optional foreground colour estimation are preserved for replay.

There is no silent fallback from matting to ordinary BiRefNet segmentation. If the selected profile, node, model choice, or required trimap is unavailable, Neo blocks the run and reports the missing contract.

## Outputs and records

The verified matting node returns the RGBA image and grayscale mask. Neo saves the transparent foreground and optional mask PNG as a non-destructive derived result, records the profile/model/edge mode/process resolution, and keeps the source and matting mask asset snapshot for replay.
