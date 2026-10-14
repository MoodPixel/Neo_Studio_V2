---
guide_id: comfy_spaces.windows_asset_upload_handle_fix
title: Windows Comfy Spaces Asset Upload Handle Fix
surface: comfy_spaces
scope: built_in
applies_to:
  - comfy_spaces
  - admin
tags:
  - comfyui
  - assets
  - windows
  - troubleshooting
priority: 67
version: 1
updated: 2026-10-14
---

# Windows Comfy Spaces asset upload handle fix

## Symptom

`POST /api/comfy-spaces/assets` returns HTTP 500 for a valid MP4 selected in a published Space. The same failure is reproducible with `curl.exe -F` independently of the browser. `GET /api/comfy-spaces/assets` may continue to return HTTP 200; that only proves listing is healthy.

## Source-level root-cause candidate

Before this correction, `neo_app/comfy_spaces/asset_bus.py` called `tempfile.mkstemp(...)` and discarded its returned file descriptor. The code then reopened that path and later called `os.replace()` or wrote a downloaded artifact through the same path. The outstanding descriptor can prevent rename/removal on Windows and leaks descriptors on every platform.

The fix explicitly closes the descriptor immediately after `mkstemp` in both the **input upload** and **generated-output collection** paths. It retains all existing validation, storage, and cleanup rules. This is a strong Windows-specific candidate but the exact live user's server traceback was not provided, so live-root-cause confirmation still requires a retest.

## Verify after patch

1. Stop Neo. Preserve a copy of `neo_app/comfy_spaces/asset_bus.py` before overlaying the patch.
2. Overlay changed files at the Neo root. Restart Neo completely.
3. Test upload with a local, trusted media file. Example in PowerShell:

```powershell
$video = 'F:\LLM\ComfyUI_windows_portable\ComfyUI\output\video\FastH3_LONGVIDEO_00005_.mp4'
curl.exe -i -F "file=@$video;type=video/mp4" -F "kind=video" -F "retention=temporary" 'http://127.0.0.1:7870/api/comfy-spaces/assets'
```

Expected: HTTP 200 with `ok: true` and a typed `asset_ref`. Retry **Validate** using a freshly selected video. If HTTP 500 persists, inspect the **Neo Python/Uvicorn traceback**: do not assume the file-handle issue was the only cause. Do not bypass media validation or send raw client filesystem paths to execution.

This fix does not prove end-to-end Windows browser validation or GPU execution; those still require a live acceptance test.
