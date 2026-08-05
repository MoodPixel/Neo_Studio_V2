# IR-5 — Scene Director Live Route Authority Repair

Status: implemented on the IR-4 cumulative Image build.

## Purpose

IR-5 removes the legacy checkpoint-only Scene Director decision from the live Image workspace. The browser now consumes the extension manifest's exact route-state table and a small `ui_route_authority` engine contract. That manifest contract is regression-tested against `backend/execution_strategy.py` so UI visibility/state cannot silently drift from backend execution support.

## Route authority

The synchronous browser path is:

`Image route -> Scene Director ui_route_authority normalization -> manifest.route_states exact lookup -> engine profile -> panel state`

The backend path remains:

`Image route -> execution_strategy.py -> support_matrix.py -> workflow dispatch / release lock`

IR-5 adds parity tests across every supported canonical family/loader/workflow pair.

### Classic V054

- SDXL + checkpoint: available for Generate / Img2Img / Inpaint.
- SD 1.5 + checkpoint: experimental available for Generate / Img2Img / Inpaint.
- Outpaint remains planned-gated.
- Engine: `classic_v054`.

### Lightweight Regional

- Krea 2 RAW / Turbo
- FLUX.2 Klein
- Z-Image Base / Turbo
- Loaders: `diffusion_model` and `gguf`.
- Generate / Img2Img / Inpaint: available.
- Outpaint: planned-gated.
- Engine: `lightweight_regional`.

### No fallback

FLUX.1, FLUX.1 Krea, Qwen Image, Qwen Edit 2509, Qwen Rapid AIO, HiDream, Wan Image, and Hunyuan Image do not gain Scene Director by falling back to V054.

## Loader normalization

Live UI aliases are family-aware:

- modern Scene Director families + `safetensors` -> `diffusion_model`
- classic SD + `safetensors` -> `checkpoint`
- `components` / `component` -> `diffusion_model`
- GGUF stays `gguf`

The same rule is applied in backend execution strategy and support-matrix normalization.

## Workspace context

Source workflows may legitimately be hosted under Image `reference`:

- Img2Img
- Inpaint
- Outpaint

The backend support matrix now accepts that workspace context instead of contradicting the extension mount contract. Generate remains a Generations route.

## UI truth

The core Scene Director panel now reports the resolved engine and runtime policy instead of always claiming V054 readiness. The extension-owned editor consumes `window.NeoSceneDirectorRouteAuthority` and adds node-readiness checks only after the host has resolved the route.

The editor no longer owns a separate Krea/Klein/Z family support matrix.

## Boundary

IR-5 does **not** repair default extension enablement or guarantee the panel host mounts the extension. Those are IR-6 responsibilities.

A route may therefore be correctly resolved by IR-5 while the panel is still absent if the extension is disabled or its workspace host does not mount it.
