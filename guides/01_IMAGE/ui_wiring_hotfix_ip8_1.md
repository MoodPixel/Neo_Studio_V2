# IP-8.1 — Image UI Wiring Repair

## Purpose

IP-8.1 repairs two UI integration gaps discovered in live testing after IP-8:

1. the Sampling Preset / Output Intent panel could load its JavaScript but fail to mount because the legacy Image parameter renderer did not expose the explicit `data-image-params-root` markers used by the IP-7 DOM tests;
2. Scene Director runtime support for Krea 2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo could exist while the extension workspace host filtered the panel before the editor mounted because its workspace/mount declarations and UI aliases were incomplete.

## Sampling Preset UI

The preset UI still prefers explicit Image mount markers. A new compatibility fallback recognizes a real Image parameter block only when the same container contains:

- an Image model-family field;
- a route field such as loader/mode/model; and
- at least two sampling controls such as Steps, Sampler, Scheduler, CFG, Guidance, Denoise, or Seed.

This signature requirement prevents generic Video/Admin parameter cards from being mistaken for the Image workspace. The Image surface module also retries the mount after Image-state and extension-mount events.

## Scene Director visibility

Scene Director manifest 1.2.19 with IP-8.1 wiring lock declares the workspace layout used by the current Image runtime:

- Generate → `generations` → `image.generate.scene_director`
- Img2Img → `reference` → `image.img2img.scene_director`
- Inpaint → `reference` → `image.inpaint.scene_director`
- Outpaint → `reference` → `image.outpaint.scene_director`

Outpaint remains visible for diagnostics but execution remains planned-gated by the Scene Director editor/runtime contract.

The UI compatibility layer now accepts the common modern route aliases used by live controls, including `safetensors` / `components` for split modern models and family aliases such as `flux2`, `krea2_raw`, and `zimage_turbo`. The editor normalizes these back to canonical Scene Director families/loaders before readiness checks.

## Supported Scene Director families

This hotfix does not broaden the execution support matrix. The active modern lightweight engine remains limited to:

- Krea 2 RAW
- Krea 2 Turbo
- FLUX.2 Klein
- Z-Image Base
- Z-Image Turbo

Classic SDXL / SD 1.5 keep the V054 route. FLUX.1, Qwen Image/Edit/Rapid, HiDream, Wan Image, and Hunyuan Image are not newly enabled by IP-8.1.

## Safety

- No modern route falls back to classic V054.
- Missing `NeoRegionalLoRADelta` does not hide Scene Director; it gates only regional LoRA.
- No preset values, prompts, styles, or LoRAs are changed by this hotfix.
- No personal/local filesystem paths are introduced.
