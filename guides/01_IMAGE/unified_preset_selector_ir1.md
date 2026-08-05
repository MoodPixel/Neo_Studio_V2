# IR-1 — Unified Preset Selector Foundation

## Status
Implemented against the uploaded Neo Studio V2 build used for live UI testing.

## Purpose
IR-1 removes the duplicate-preset UX model without merging storage authorities. The live Image workspace keeps one existing preset selector and presents two sources inside it:

- **Defaults** — immutable sampling choices from Neo's built-in sampling registry, plus `Manual / No Preset`.
- **User Presets** — the existing full Image workspace/UI snapshots stored through `/api/ui-presets/image`.

## Visible selector
The Image label is now `Preset`, with native `<optgroup>` sections:

- Defaults
  - Manual / No Preset
  - Provider Defaults
  - Default · Balanced
  - Empty · Clean Slate
- User Presets
  - existing saved Image UI/workspace presets

The existing user preset JSON files are not migrated, renamed, or copied into the sampling preset registry.

## Manual / No Preset
`Manual / No Preset` is the Image boot default. The Image base contract now uses an empty `sampling_preset_id`.

IR-2 now supersedes the presentation-only boundary: the same selector performs explicit authoring authority handoff. Manual keeps current values, Provider Defaults clears managed overrides, Balanced applies the route recipe, and Clean Slate clears managed fields for manual authoring.

Starred user workspace presets remain starred in the User Presets group, but Image no longer auto-loads one at startup. Other Neo surfaces retain their previous default-UI-preset behavior.

## Storage ownership
- Built-in sampling definitions: `neo_app/models/sampling_presets_builtin.json`
- Existing user Image workspace presets: `neo_data/ui_presets/image/`
- User sampling presets from IP-7 remain a separate backend authoring namespace and are not surfaced as a second visible control in IR-1.

## Single visible UI owner
`neo_app/static/js/neo.js#workspaceUiPreset` is the only visible Image preset selector.

`image_sampling_presets.js` remains available as a resolver/authoring API but no longer auto-mounts its standalone Sampling Preset / Output Intent panel. This prevents two preset selectors from competing for the same Image workspace.

## User preset actions
When a built-in/default option is selected, mutation actions that require an existing user preset (Update, Rename, Remove, Delete, Make Default) are disabled. Save remains available so the current workspace can still be captured as a new user preset.

## IR-2 superseding authority note
IR-2 activates Provider Defaults, Balanced, Empty Clean Slate, Manual/User handoff, route re-resolution, and stale-authority protection while keeping this single-selector presentation. Scene Director, CFG/negative-prompt UX, and final Image job payload submission remain later IR phases.
