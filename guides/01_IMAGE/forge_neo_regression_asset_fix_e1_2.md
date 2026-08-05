# Forge Neo Regression & Asset Fix — E1.2

## Purpose
E1.2 stabilizes the Forge Neo image path after the built-in remap and extra-feature passes.

## What changed
- **High-Res Lab default safety**
  - High-Res Lab no longer defaults to an active Forge payload.
  - Profile selection preserves the current on/off state instead of silently enabling the pass.
  - Legacy draft states from the regression window are treated as **disabled** unless the user explicitly toggled the High-Res switch.
- **Forge Hires payload completeness**
  - Neo now sends:
    - `hr_checkpoint_name = "Use same checkpoint"`
    - `hr_additional_modules = ["Use same choices"]`
  - This avoids the Forge Neo `hr_additional_modules=None` crash in the second pass.
- **ControlNet generated map asset handoff**
  - UI state now preserves the full generated-map object instead of collapsing it to a filename.
  - Forge compilation can consume `preview_data_url` / `data_uri` payloads directly, so a generated map preview can be reused without requiring a matching local filesystem path.

## Result
The verified regression case is fixed:
- Neo Studio → Forge Neo plain txt2img works without High-Res auto-triggering.
- Explicit High-Res requests compile with the safe Forge reuse markers.
- ControlNet generated-map previews can be forwarded to Forge as real image payloads instead of a dangling filename reference.

## Scope notes
This pass does **not** introduce a generic Forge IP-Adapter model-path remap. That can be handled as a later focused pass if needed.
