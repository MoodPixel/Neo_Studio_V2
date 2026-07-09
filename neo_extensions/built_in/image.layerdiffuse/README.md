# Neo Studio — ComfyUI LayerDiffuse Extension

Transparent-background / RGBA image generation for **Neo Studio** using the external **ComfyUI-LayerDiffuse** node workflow.

This extension is designed for Neo Studio's Image tab extension system. It adds a LayerDiffuse panel, builds LayerDiffuse workflow patches, and routes transparent asset generation through ComfyUI.

> Main Neo Studio repo: [MoodPixel/Neo_Studio](https://github.com/MoodPixel/Neo_Studio.git)  
> Required ComfyUI node repo: [huchenlei/ComfyUI-layerdiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)

---

## What this extension does

- Adds a Neo Studio Image extension panel for LayerDiffuse.
- Supports transparent asset generation.
- Produces RGBA output through LayerDiffuse decode workflows.
- Supports visible output policies such as `new_run`, `preview`, `append`, and `replace`.
- Uses Neo Studio's external extension runtime, validation, and output visibility contracts.
- Keeps the LayerDiffuse workflow separate from Neo Studio core code.

---

## Requirements

Before installing this Neo extension, make sure you have:

1. **Neo Studio**
   - Repository: [MoodPixel/Neo_Studio](https://github.com/MoodPixel/Neo_Studio.git)

2. **ComfyUI**

3. **ComfyUI-LayerDiffuse nodes**
   - Repository: [huchenlei/ComfyUI-layerdiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)
   - Install it into:

```txt
ComfyUI/custom_nodes/ComfyUI-layerdiffuse
```

This Neo extension does **not** replace the original ComfyUI node package. The original LayerDiffuse custom nodes are still required.

---

## Install through Neo Studio

1. Open **Neo Studio**.
2. Go to:

```txt
Admin → Extension Manager
```

3. Choose **Install from Git URL**.
4. Paste this extension repository URL.
5. Install the extension.
6. Restart Neo Studio if the Extension Manager requests it.
7. Go to:

```txt
Image → Extensions
```

8. Enable **ComfyUI LayerDiffuse**.

---

## Install the required ComfyUI node

Inside your ComfyUI `custom_nodes` folder:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/huchenlei/ComfyUI-layerdiffuse
```

Then restart ComfyUI.

If the node has extra dependency instructions, follow the original repo's README.

---

## Important: newer ComfyUI compatibility fix

Some newer ComfyUI builds may change the internal `JoinImageWithAlpha` method used by LayerDiffuse's RGBA decoder.

If you see errors around:

```txt
JoinImageWithAlpha
LayeredDiffusionDecodeRGBA
join_image_with_alpha
join_images
execute
```

or if transparent output fails even though the LayerDiffuse nodes are installed, replace the original file:

```txt
ComfyUI/custom_nodes/ComfyUI-layerdiffuse/layered_diffusion.py
```

with the patched `layered_diffusion.py` included in this extension repository.

The patched version keeps compatibility with multiple ComfyUI method names:

```txt
join_image_with_alpha
join_images
execute
```

After replacing the file, restart ComfyUI and Neo Studio.

---

## Supported workflows

Current supported / guarded workflow modes:

| Mode | Status | Notes |
|---|---:|---|
| Transparent asset | Supported | Prompt-driven RGBA output |
| RGB + Alpha split | Supported / guarded | Exports image and alpha-related output when configured |
| Foreground on background | Guarded | Requires verified source/background routing |
| Background-aware blend | Guarded | Requires verified template validation |
| Extract foreground | Guarded | Requires source image |
| Overlay FX transparent | Guarded | Requires transparent overlay routing |

The primary tested path is:

```txt
CheckpointLoaderSimple
→ LayeredDiffusionApply
→ KSampler
→ VAEDecode
→ LayeredDiffusionDecodeRGBA
→ SaveImage
```

---

## Recommended prompt pattern

LayerDiffuse works best when the prompt clearly asks for an isolated transparent asset.

Example:

```txt
a cinematic glowing blue magic sword, transparent background, isolated object, detailed fantasy weapon, soft edge glow, high quality, clean alpha, no background
```

Recommended negative prompt terms:

```txt
solid background, gray background, white background, black background, scenery, watermark, text, blurry, low quality, cropped
```

---

## Known limitations

- Width and height should be multiples of `64`.
- Batch size is forced to `1` for safe RGBA generation.
- The original ComfyUI-LayerDiffuse nodes must be installed separately.
- Transparent output depends on ComfyUI successfully loading the LayerDiffuse model files.
- Some workflows are intentionally guarded until their templates are verified.

---

## Troubleshooting

### Extension appears in Neo Studio, but output is not transparent

Check:

1. The extension is enabled in:

```txt
Image → Extensions
```

2. The ComfyUI nodes are installed:

```txt
ComfyUI/custom_nodes/ComfyUI-layerdiffuse
```

3. The final workflow contains these nodes:

```txt
LayeredDiffusionApply
LayeredDiffusionDecodeRGBA
```

4. You are using a supported SDXL/SD model family.
5. Width and height are multiples of `64`.

---

### Missing node warning

If Neo Studio reports missing nodes, reinstall or update the original LayerDiffuse node package:

```bash
cd ComfyUI/custom_nodes/ComfyUI-layerdiffuse
git pull
```

Then restart ComfyUI and Neo Studio.

---

### Newer ComfyUI RGBA decode error

Use the compatibility patch included in this repository:

```txt
layered_diffusion.py
```

Copy it over:

```txt
ComfyUI/custom_nodes/ComfyUI-layerdiffuse/layered_diffusion.py
```

Then restart ComfyUI.

---

## Repository layout

```txt
comfyui_layerdiffuse/
├── backend/
│   ├── adapter.py
│   ├── graph_wiring.py
│   ├── validation.py
│   └── ...
├── static/
│   ├── layerdiffuse.js
│   └── layerdiffuse.css
├── workflow_templates/
│   ├── transparent_asset_sdxl.json
│   ├── transparent_asset_split_sdxl.json
│   └── ...
├── extension.config.json
├── neo_extension.json
├── schema.json
├── layered_diffusion.py
├── README.md
└── .gitignore
```

---

## Credits

This extension depends on the original **ComfyUI-LayerDiffuse** custom nodes by huchenlei:

[huchenlei/ComfyUI-layerdiffuse](https://github.com/huchenlei/ComfyUI-layerdiffuse)

Neo Studio:

[MoodPixel/Neo_Studio](https://github.com/MoodPixel/Neo_Studio.git)

---

## Notes for extension developers

This extension should remain an external Neo Studio extension.

Do not patch Neo Studio core files from this repository. If a core runtime behavior is required, it should be implemented globally in Neo Studio's extension runtime, not inside this extension.


---

## Neo V2 adapter bridge status

This package now includes `extension_manifest.json` for Neo V2 discovery. Neo V2 renders a built-in external-section bridge panel, submits payloads under `extensions.payloads.image.layerdiffuse`, and dispatches the adapter through the Comfy workflow hook only when LayerDiffuse is enabled and the route is compatible. The required ComfyUI node package remains `huchenlei/ComfyUI-layerdiffuse` installed under `ComfyUI/custom_nodes`.

## Phase C image-conditioned modes

Foreground on Background, Background-Aware Blend, and Extract Foreground are now available in Neo V2 when their required image IDs are present. These modes use ComfyUI `LoadImage` names / Neo output IDs and remain batch-1 guarded.


## Phase K — Background-Aware Blend Compositor Repair

Background-Aware Blend now wires the requested background image into the final output using ImageCompositeMasked instead of loading it as a debug-only dependency.
