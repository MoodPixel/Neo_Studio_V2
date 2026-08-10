---
guide_id: image.source_visibility_masks
title: Source Visibility Masks
surface: image
status: implemented
updated: 2026-08-07
---

# Source Visibility Masks

Source Visibility Masks let you hide unwanted parts of an Img2Img source before the image reaches ComfyUI. The original uploaded image is not edited.

## Where it appears

On a supported **Img2Img** or **Edit** route, open **Source Image** and select **Hide Parts**. Stitch Image A and Image B expose the same action independently.

The editor uses the same brush interaction as the Inpaint mask editor, but the result has a different purpose:

- **White** hides that part of the source.
- **Black or clear** keeps that part visible.
- Hidden areas are flattened to black before ComfyUI encodes the source or before `ImageStitch` combines the pair.

Use **Edit Hidden Areas** to reopen a saved mask and **Clear Hidden Mask** to remove it.

## Supported routes

This control is shown only for ComfyUI and ComfyUI Portable **Img2Img/Edit** routes. Forge and cloud providers do not show it because they do not yet expose a verified equivalent source-preprocessing contract. Inpaint and Outpaint keep their own mask/canvas systems.

## Direct source behavior

For a normal Img2Img source, Neo creates a derived masked PNG in Neo-owned storage and uploads that file to ComfyUI. The original source reference is retained in metadata and remains available in the UI.

The mask dimensions must match the source dimensions exactly. A stale mask from another source fails before generation instead of being stretched silently.

## Stitch behavior

Each Stitch input owns its own optional visibility mask:

```text
Image A + Image A mask
Image B + Image B mask
        ↓
masked Image A + masked Image B
        ↓
ImageStitch
```

The masks are applied before the stitch node. Clearing or replacing one Stitch image clears only that input's hidden-area mask.

## Difference from Inpaint

A Source Visibility Mask does not select a generation region and does not convert Img2Img into Inpaint. It simply removes information from the conditioning source before the existing family workflow runs.
