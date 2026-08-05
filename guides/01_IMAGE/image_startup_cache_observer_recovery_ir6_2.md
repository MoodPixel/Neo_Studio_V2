# Image Startup Recovery — IR-6.2

Use this recovery when Neo opens at `127.0.0.1:7860` but remains on **Loading…** or Chrome displays **Page Unresponsive** after an Image-tab patch.

## Install

1. Close Neo and all Neo browser tabs.
2. Extract the IR-6.2 files into the Neo Studio root and overwrite existing files.
3. Start Neo with `run_neo_studio.bat`.
4. The launcher opens the versioned URL `/?neo_startup=ir6_2` automatically.

Do not copy the patch into an extra nested `Neo_Studio_V2` folder. The patched `neo_app`, `tests`, `guides`, and `neo_system_records` folders must merge with the folders already at the project root.

## Quick verification

Open this address in the same browser:

`http://127.0.0.1:7860/static/js/negative_prompt_eligibility.js?v=ir6_2_20260728`

The file should contain `STARTUP_RECOVERY_PHASE = 'IR-6.2'`. The response header should include `X-Neo-Startup-Recovery: IR-6.2`.

## What this recovery changes

- forces a fresh shell and fresh startup scripts;
- prevents JavaScript caching during local patch testing;
- scopes Image observers to the actual panel host;
- disconnects the eligibility observer during its own DOM writes;
- coalesces sampling-preset remount work.

It does not alter sampling values, saved presets, Scene Director regions, model selection, provider profiles, or generation workflows.
