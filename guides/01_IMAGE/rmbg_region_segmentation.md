# RMBG-3 Region Segmentation

Neo exposes the installed ComfyUI-RMBG region nodes inside Image → Finish → Remove Background:

- `FaceSegment` for face-parsing classes such as skin, eyes, lips, hair, and ears.
- `ClothesSegment` for clothing/body classes such as upper clothes, pants, shoes, bags, and arms.
- `FashionSegmentClothing` for garment and fashion-detail classes such as dresses, jackets, shoes, collars, and pockets.
- `FashionSegmentAccessories` plus `FashionSegmentClothing` for accessory and detail classes such as hats, glasses, bags, belts, scarves, bows, zippers, and trims. Neo connects the selector node's `ACCESSORIES_OPTIONS` output to the clothing node's `accessories_options` input.

The route is one source image per run. Users can add up to eight target rows. Each row names `face`, `clothes`, or `fashion`, followed optionally by comma-separated live class labels:

```text
face: Skin, Hair
clothes: Upper-clothes, Pants
fashion: dress, shoe
accessories: hat, glasses, bag, belt
```

Neo queries the active ComfyUI `/object_info` catalog before execution. It only sends class inputs that the live node exposes, and it blocks when a required node or class contract is missing. There is no native or silent fallback for this route.

The selected mask operation is explicit:

- `union` combines all target masks.
- `intersection` keeps only overlapping regions.
- `subtract` subtracts later target masks from the first target.

The mask is then joined to the original source image and persisted as a non-destructive derived PNG. The optional grayscale mask PNG follows the normal Remove Background output policy. The accessories route is blocked unless both live FashionSegmentAccessories and FashionSegmentClothing contracts are available.

The RMBG nodes download or load their own model assets according to their node implementation. Neo records portable node and class identifiers only; it does not store machine-specific model paths.
