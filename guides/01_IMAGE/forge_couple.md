---
guide_id: image.forge_couple
title: Forge Couple — Basic, Advanced, Mask, and Tile Regional Prompting
surface: image
scope: built_in
applies_to:
  - image
  - forge
  - extensions
  - prompting
tags:
  - forge-couple
  - regional-prompting
  - sd15
  - sdxl
  - basic-mode
  - advanced-mode
  - mask-mode
  - tile-mode
priority: 88
version: 5
updated: 2026-08-01
---

# Forge Couple — Basic, Advanced, Mask, and Tile Regional Prompting

`Image · Forge Couple` is Neo Studio's dedicated frontend for the separately installed **Haoming02 ForgeCouple** Forge extension.

Neo owns the frontend, validation, session mask handling, payload compilation, replay policy, and diagnostics. The installed ForgeCouple extension remains the native runtime that patches regional attention inside Forge. ForgeCouple is not routed through Neo's generic Forge Script Bridge and does not replace Scene Director.

## Requirements

- Active Forge / Forge Neo profile with API access.
- ForgeCouple installed and enabled in Forge.
- Live `Forge Couple` always-on script discovered through `/sdapi/v1/script-info`.
- Verified seventeen-argument ForgeCouple API contract.
- SD 1.5 or SDXL checkpoint route.
- Tile Mode additionally requires Forge's built-in selectable **SD Upscale** script to be visible through `/sdapi/v1/script-info` with its verified four-argument Img2Img contract.

After installing or updating ForgeCouple, restart Forge and refresh Forge Admin in Neo Studio.

## Supported Phase 3 routes

| Route | Region modes | Tile Mode |
|---|---|---|
| SDXL checkpoint Txt2Img | Basic, Advanced, Mask | Unavailable |
| SDXL checkpoint Img2Img | Basic, Advanced, Mask | Experimental for Basic/Advanced with verified SD Upscale |
| SDXL checkpoint Inpaint | Basic, Advanced, Mask, experimental | Unavailable |
| SD 1.5 checkpoint Txt2Img / Img2Img / Inpaint | Experimental | Img2Img Basic/Advanced only, experimental |
| Outpaint | Planned / gated | Gated |
| Flux, Qwen, Krea, Klein, Z-Image | Unsupported by the current ForgeCouple runtime contract | Unsupported |

Tile Mode does not perform tiled generation by itself. ForgeCouple only assigns regional prompt lines to tiles created by another selectable Forge script. FC3 verifies **SD Upscale** and fails closed for arbitrary selectable scripts.

## Prompt authority

The main Neo **Positive Prompt** is the single source of truth. ForgeCouple splits that prompt using the selected separator. Neo never maintains a second hidden regional prompt document.

With the default empty separator, each newline is one region:

```text
2boys, standing side by side
2boys, dark-haired man, black jacket
2boys, blond man, white shirt
```

Empty prompt chunks, repeated separators, and trailing separators block submission.

## Basic mode

- **Horizontal** maps content regions left to right.
- **Vertical** maps content regions top to bottom.
- **Global Effect** can use the first or last prompt line across the full image.
- At least two lines are required without Global Effect; at least three with it.

## Advanced mode

Advanced mode maps each positive-prompt line to one normalized rectangle:

```text
[x1, x2, y1, y2, weight]
```

Coordinates use `0.0` to `1.0` of the canvas. Weight uses `0.0` to `5.0`.

The editor provides:

- draggable regions using the shared Neo Region Canvas;
- eight resize handles covering corners and edges;
- numeric coordinate and weight fields;
- region selection, reorder, duplicate, add, and delete actions;
- built-in layouts and browser-local custom presets;
- prompt-count synchronization;
- optional session-only reference background;
- guaranteed Neo-themed controls loaded from the global runtime;
- exact full-canvas union coverage validation.

Prompt-line count must equal mapping count. Overlap is allowed. Uncovered gaps block submission. Neo submits the normalized mapping in native argument slot `7`.


## Shared Region Canvas

Advanced and Mask modes use Neo Studio's shared **Region Canvas** component, the same interaction engine used by Scene Director. The shared component owns normalized clamping, aspect-ratio-aware canvas sizing, selection, pointer capture, dragging, and eight resize handles.

Adapters keep each extension's native data contract separate:

- Scene Director uses `{x, y, w, h}` boxes.
- ForgeCouple Advanced converts between `{x, y, w, h}` and native `[x1, x2, y1, y2, weight]` mappings.
- ForgeCouple Mask derives boxes from active white-pixel bounds and converts box changes into transformed binary PNG layers.

Sharing the canvas does not merge Scene Director and ForgeCouple runtimes. They remain mutually exclusive regional-conditioning engines. Reference backgrounds and mask pixels remain session-only.

### Fitted canvas geometry

### Advanced clipping recovery

RC2-B measures the Advanced canvas after the extension card, accordion, and side panels have completed layout. The shared canvas subtracts the wrapper padding from the real content width, applies an explicit fitted pixel width/height, and refits through `ResizeObserver` whenever the available panel width changes. A second animation-frame pass catches late CSS, font, and accordion settlement.

The Advanced mapping values remain normalized and unchanged. This is a display-containment fix only; Forge still receives `[x1, x2, y1, y2, weight]` in native slot `7`.

### Mask fitted-frame alignment

RC2-C removes the Mask painter's independent frame markup. The painter is now rendered by `NeoRegionCanvas.renderSurface()` and joins the same fit-group as the saved-mask region canvas. Both surfaces therefore use the same source dimensions, wrapper-content measurement, maximum display limits, orientation, reference-background frame, resize observer, and second-frame settlement.

The visible paint coordinates map directly from the fitted frame into the working binary mask bitmap. Mask-specific CSS controls only painting-layer stacking and clipping; it no longer owns width, height, aspect ratio, or maximum frame size.

Advanced, saved-mask regions, and the active Mask painter now use the same fitted frame. Neo constrains the visible frame by both panel width and a maximum display height, then preserves the selected source aspect ratio. This prevents wide landscape canvases from being clipped horizontally and prevents portrait Mask painters from being stretched into a square or full-width column.

For Mask mode, a loaded source image supplies the geometry. Without a source image, the current Image Width and Height controls supply it. The browser may downscale the internal working mask for performance, but the visible frame and normalized mask coordinates preserve the original orientation and aspect ratio.

## Mask mode

Mask mode binds one binary mask layer to each regional prompt line. Pure white pixels are active; black or transparent pixels are inactive.

The Neo editor provides:

- paint and erase tools;
- adjustable brush size;
- uploaded mask normalization;
- optional visual reference background;
- save, override, load, reorder, reweight, delete, and reset actions;
- combined mask-union coverage diagnostics;
- a shared composition canvas showing all saved masks together as visible boxes and translucent overlays;
- selectable mask boxes with drag/resize transforms using nearest-neighbour binary resampling.

### Mask contract

- At least one saved mask is required.
- Mask order equals regional prompt-line order.
- Each mask weight must remain between `0.0` and `5.0`.
- With Global Effect `None`, the union of all masks must cover the full canvas.
- With First/Last Line Global Effect, one additional prompt line is required and uncovered pixels inherit the global conditioning.
- Neo performs browser-side and server-side union coverage checks for submitted session masks.

Masks are kept as binary PNG data URIs inside Neo only. At the provider boundary, Neo strips the data-URI prefix and sends ForgeCouple the raw base64 string expected by its native slot-`7` API. Mask image bytes are never written into output summaries, public records, presets, or replay payloads.

### Session and replay behavior

Mask layers and reference backgrounds are session-only. Output metadata records only layer count, order, weight, and presence. Replay restores ForgeCouple disabled, removes mask bytes, and requires mask re-upload/recreation before re-enabling.

## Tile Mode

Tile Mode is an experimental **Img2Img-only** companion to Basic or Advanced region assignment.

ForgeCouple uses the region geometry to decide which prompt lines belong to each tile. It does not create or iterate tiles itself. FC3 requires:

1. Image workflow set to Img2Img.
2. ForgeCouple enabled with valid Basic, Advanced, or Mask regions.
3. Region Assignment set to Basic or Advanced. Mask + Tile is gated in FC3 because the upstream API tile path does not expose a verified conversion of submitted base64 mask mappings into the tile-overlap mapper.
4. Tile Mode enabled in the ForgeCouple panel.
5. Forge's built-in selectable **SD Upscale** script detected by Neo with its exact four-argument API contract. No generic Script Bridge setup is required.

The Tile controls expose:

- SD Upscale upscaler;
- scale factor;
- tile overlap;
- calculated final width and height;
- calculated column and row counts;
- inclusion threshold;
- subject replacement rules;
- Forge console debug output.

Subject replacement uses one rule per line:

```text
1boy: 2boys, multiple boys
1girl: 2girls, multiple girls
```

The right side contains source tags to replace; the left side is the singular prompt inserted for tile processing.

Neo submits Tile Mode through native argument slots `11–16`:

```text
11 enable tile mode
12 column count
13 row count
14 inclusion threshold
15 subject replacement
16 debug tiles
```

Neo directly owns the selectable-script payload for Tile Mode and submits Forge's exact SD Upscale argument order: overlap, upscaler, scale factor, and save-to-Extras. Arbitrary selectable scripts are not accepted as tilers. If another generic selectable script is active, Neo fails closed because Forge permits only one selectable script per request.

## Separator and Common Prompts

Leave the separator empty to use a newline. Literal `\n` and `\t` escapes follow native ForgeCouple behavior.

Common Prompt syntax supports `off`, `{ }`, and `< >`. ForgeCouple remains the final parser authority.

## Hires Fix behavior

**Base pass only during Hires Fix** is enabled by default. ForgeCouple establishes composition during the base pass while Hires Fix refines the result without reapplying the regional patch.

## Conflicts and coexistence

Forge Couple is mutually exclusive with:

- Scene Director;
- MultiDiffusion regional conditioning.

It may coexist with independent Forge always-on scripts such as ADetailer, ControlNet, and standard IP-Adapter. Neo merges each script without allowing one adapter to overwrite another.

Tile Mode additionally uses one selectable script slot, currently SD Upscale. Neo preserves the normal one-selectable-script rule.

## Replay safety

Basic, Advanced, Mask descriptors, and Tile settings may be summarized, but replay always restores ForgeCouple disabled until Neo revalidates:

- active Forge profile;
- live ForgeCouple schema;
- supported route;
- prompt count and region geometry;
- mask re-upload and mask coverage;
- Tile Mode route and selectable SD Upscale script.

Tile Mode is restored disabled. Mask image bytes are never replayed.

## Attribution

Native runtime: `Haoming02/sd-forge-couple`, GPL-3.0.

Neo Studio uses an original frontend built against the documented ForgeCouple API contract; it does not copy the upstream Gradio interface.

## Shared Mask overlay boxes — RC2-D

Saved Mask layers now use the exact same visible box and interaction method as Advanced regions. Each binary layer is cropped to a transparent preview and rendered inside its shared box, so the shape itself follows drag and resize operations instead of remaining behind as a separate full-canvas overlay.

When the edit is committed, Neo rebuilds the full binary PNG with nearest-neighbour sampling, recalculates its active bounds, refreshes the preview, and runs coverage validation again. Mask previews and bounds are session-only; Forge still receives only the native mask plus weight.


## Clean Mask transformation rules — RC2-E

Mask box edits now use explicit operations:

- dragging performs an integer-pixel translation of the current binary mask;
- resizing samples from the layer's stable paint/override source with explicit nearest-neighbour lookup;
- repeated resizing does not resample the previous resized result;
- paint or erase changes only the active painter, and Save/Override establishes a new transform source;
- **Reset selected transform** restores that source exactly;
- pointer cancellation reverts the visual box instead of committing.

After every commit Neo recalculates binary bounds, regenerates the cropped overlay preview, and reruns union coverage validation. Transform-source data remains session-only and the Forge payload remains `{mask, weight}`.

## Responsive Advanced region cards — RC2-F

Advanced coordinate cards now fit the ForgeCouple panel using container-responsive grid areas rather than browser-width media queries. Prompt text, `x1`, `x2`, `y1`, `y2`, weight, and action controls reflow inside the card, so a narrow workspace column does not cut off the right-hand controls.

The toolbar also provides **Remove selected region**. It uses the same safe deletion path as each row's Delete button, keeps at least one region, selects the nearest remaining region, and reruns prompt-count and full-canvas coverage validation.

Tile Mode uses two separate Forge runtime pieces. ForgeCouple assigns regional prompts; Forge's selectable **SD Upscale** script performs upscaling and the tile loop. Neo stages both in one Img2Img request, but SD Upscale is not part of the ForgeCouple extension.

## Mask target geometry and Image workspace cleanup — RC2-G

ForgeCouple Mask now uses the active Image **Width** and **Height** as its only visible geometry authority, matching Advanced mode. An uploaded Img2Img source may have a different native size or orientation, but Forge resizes that source into the requested generation target before regional conditioning. The Mask painter and saved-mask canvas therefore follow the generation target rather than `source_image_width` / `source_image_height`.

Provider-incompatible Image extensions remain installed and retain their saved settings, but Neo omits them from the active workspace until a compatible provider profile is selected. This removes disabled Provider Gated cards without changing capability validation or provider compilation.

Image subtabs no longer repeat the Start Here and Workspace Summary cards. Workspace, workflow mode, route, and backend context remain available in the Image top command row.
