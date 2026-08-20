# VAE Decode Utilities

Neo now exposes a **VAE Decode** selector next to the standard **VAE** dropdown on supported Image routes.

## What it does
Two decode paths are available:

1. **Native decode**
   - Uses Comfy's built-in `VAELoader` and `VAEDecode`.
   - This is the default path.

2. **VAE Utils auto (Wan/Qwen 2× ready)**
   - Uses `VAEUtils_CustomVAELoader` and `VAEUtils_VAEDecodeTiled` from **ComfyUI-VAE-Utils**.
   - Neo sends `upscale = -1`, which lets the node auto-detect the upscale factor from the chosen VAE.
   - To actually get the 2× decode behavior, the selected VAE in the normal **VAE** dropdown must be the special Wan/Qwen upscale VAE.

## Where it appears
Visible only on these route families:
- `qwen_image` (native components and GGUF transformer routes)
- `qwen_image_edit_2509` / `qwen_image_edit_2511` (native components and GGUF transformer routes where available)
- `krea2`
- `krea2_turbo`

And only for:
- `txt2img`
- `img2img` / `edit`

## Backend requirement
The dropdown only enables the VAE Utils option when live backend capability discovery sees both nodes:
- `VAEUtils_CustomVAELoader`
- `VAEUtils_VAEDecodeTiled`

If they are missing, Neo keeps the option disabled and shows the requirement inline.

## Important boundary
This feature only changes the **decode** stage.
It does **not** make unsupported families compatible with the Wan/Qwen 2× VAE.

## Capability discovery
Neo's Comfy capability slice explicitly transports the two VAE Utils node classes from `/object_info`. The UI checks both the active profile and its capability-overlay fallback, so an installed node is not incorrectly shown as unavailable because of profile-cache shape differences.

The same VAE Decode selector is mounted inside Neo's dedicated **GGUF Runtime** card for compatible Qwen/Krea 2 GGUF routes.

## High-Res Lab compatibility
IMG-VAE1B makes High-Res Lab decode-aware. If the base generation uses **VAE Utils auto**, High-Res Lab keeps the same VAE-Utils decoder for both image-upscale/refine and latent-refine paths.

This is required for the Wan/Qwen 2x VAE because its raw decoder output is 12-channel data that must be pixel-shuffled by `VAEUtils_VAEDecodeTiled` before any RGB-only upscaler such as ESRGAN/Spandrel receives it.

Do not replace the VAE-Utils decoder with a core `VAEDecode` anywhere in a finish chain using the special 2x VAE.

## IMG-VAE1C payload authority hotfix
The `VAE Decode` selection is part of the queued Image job payload. This is required because the special Wan/Qwen 2× VAE must never fall back to core `VAEDecode`; doing so exposes the raw 12-channel decoder output to image finish/save nodes.
