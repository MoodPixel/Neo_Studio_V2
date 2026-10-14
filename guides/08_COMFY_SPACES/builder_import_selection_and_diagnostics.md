# Comfy Spaces Builder — Import selection and diagnostics

Updated: 2026-10-14

## Import workflow

1. In **Admin → Comfy Space Builder → Import**, choose the ComfyUI runtime profile you want Neo to inspect, or choose **Import without live runtime inspection**.
2. Choose a **normal ComfyUI Save workflow JSON** (preferred) or API-format workflow JSON. The selection is kept in the current browser session even if the Builder redraws the form. The filename appears below the picker.
3. Optionally set the Space ID and display-name overrides, then click **Import & create draft**.
4. The Builder shows **Importing…** while submitting. On success it opens **Inspect**. On failure it displays **Builder blocked: Workflow import failed: ...** and retains the selected file for a retry.

Selecting a runtime can redraw the form. If the file input appears empty but the status shows a retained filename, the current browser session still has the file; select a different file to replace it. The file object is not serialized to the draft, persisted on the server, or automatically queued for execution as part of selecting it.

## Troubleshooting

- **No file selected**: choose a JSON file. Browser reloads and new sessions do not retain the selection.
- **Invalid JSON**: export a ComfyUI **Save** workflow (not an image, log, or truncated paste).
- **Runtime schema unavailable**: try **Import without live runtime inspection** to isolate the ComfyUI `/object_info` probe. This can reduce custom-node widget detection accuracy.
- **Backend HTTP 400/500**: capture the exact **Builder blocked** message and the failing `/api/admin/comfy-spaces/builder/import` response, plus the Neo backend traceback if applicable. This UI fix does not waive workflow/schema checks or fix an unidentified backend failure.
- **Still no visual response**: reload Neo and hard-refresh the browser after applying the patch. If the button remains inert, gather the first console JavaScript error, which points to module loading/event-handler failure.

Keep the original JSON and relevant non-sensitive error output when reporting failures. Native canvas group extraction changes only Builder presentation, not the workflow graph.
