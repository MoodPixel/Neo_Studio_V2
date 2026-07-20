# RMBG-4/8 Mask, Object, and Image Preparation Utilities

Image → Finish → Remove Background now has a **Mask & Object Utilities** mode for the installed ComfyUI-RMBG utility nodes:

- `MaskEnhancer` cleans a mask with blur, offset, smoothing, hole filling, and inversion.
- `MaskCombiner` combines up to four uploaded masks using union, intersection, or difference.
- `MaskExtractor` applies a mask or extracts its masked object with alpha, original, or color background behavior.
- `CropObject` crops an image and mask to the non-zero object bounds with optional padding.
- `ImageMaskConvert` reads an image channel into a mask.
- `ColorToMask` creates a mask from a target color and threshold.
- `MaskOverlay` renders a non-destructive visual mask overlay for inspection.
- `LamaRemover` removes a selected object from an image using a supplied mask. It is an object-removal operation, not a background-segmentation engine.
- `ImageResize` is exposed in Neo as **Image + Mask Resize**. Neo also accepts the older `AILab_ImageMaskResize` alias when that is what the active Comfy profile exposes.
- `ImageCrop` crops the source image to a chosen size and position. If a mask is supplied, Neo applies the same crop to the mask so the pair remains aligned.

## Where these operations belong

The controls live in **Image → Finish → Remove Background → Mask & Object Utilities** so the installed RMBG nodes remain independently selectable. The source panel also contains an **Image Preparation** disclosure that opens this route.

Inpaint, Outpaint, ControlNet, Scene Director, and Stitch consume the resulting Neo-owned image/mask asset; they do not own the crop, resize, overlay, or Lama implementation. Use **Image + Mask Resize** when the mask must stay aligned. Use **Image Crop** for source/Stitch preparation, or with a supplied mask when the cropped pair must remain aligned. The original source is never overwritten.

The route accepts one source image and, when the selected operation needs it, one to four mask image uploads. Mask uploads use Neo’s normal owned input storage and are handed to ComfyUI as uploaded input filenames. No local filesystem path is sent to the browser or stored in the public utility contract.

Neo queries ComfyUI `/object_info` before enabling each operation. If the exact node or required input contract is missing, the operation is blocked. There is no native or silent fallback. Preview-only RMBG nodes remain diagnostics; they are not inserted into a utility graph without an explicit output operation.

## Handoff behavior

Utility runs are appended as non-destructive derived results. Select the derived result in Results/Output Inspector and send it to **Img2Img, Inpaint, Outpaint, or Stitch**. Masked preparation records the source/mask relationship in the result metadata so downstream routes can validate dimensions before queueing.
