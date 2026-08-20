# Scene Director SD-28.9 — Editor UI Restore

SD-28.9 restores the Scene Director editor as an extension-owned UI bundle. The SD-28 backend engines were already available; this phase fixes the missing visible authoring surface.

## Release behavior

- The editor is always mountable on eligible Image → Generations routes.
- Classic SDXL/SD1.5 execution still requires `NeoSceneDirectorV054`.
- Krea 2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo lightweight regional prompting does not require a custom Scene Director node.
- `NeoRegionalLoRADelta` is required only when a compatible modern regional LoRA route is requested.
- A missing optional regional-LoRA node must never hide Scene Director. Regional LoRA fails closed while regional prompting remains available.
- The Inspector is read-only and renders into its own child root; it cannot replace the editor DOM.

## Compatibility serialization

The editor exposes a canonical extension block through `window.NeoSceneDirectorEditor` and mirrors the state into legacy form fields (`scene_director_state`, `scene_graph_json`, regional-unit fields) so existing generation plumbing can continue consuming Scene Director state while per-extension hydration evolves.

## No fallback

Modern routes never fall back to classic V054. Missing requirements are displayed as capability readiness states instead of hiding the editor.

## IMG-SD1 supersession note — 2026-08-15

IMG-SD1 supersedes the modern editor-routing and submit-authority details in this historical phase guide. Modern `lightweight_regional` routes now use Basic-only region cards with Extension Routing in the normal region flow, and regional LoRA selection stores LoRA Stack row UIDs instead of free-text LoRA sources. Provider execution is now fail-closed when an explicitly requested regional contract does not reach the final graph. Classic V054 behavior remains unchanged.
