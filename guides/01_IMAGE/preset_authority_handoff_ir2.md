# IR-2 — Preset Authority + State Handoff

## Status
Implemented against the uploaded Neo Studio V2 build used for live UI testing, layered after IR-1.

## Purpose
IR-2 turns the single IR-1 Image `Preset` selector from a presentation-only control into an authoring authority switch. It does not add another dropdown.

The selector still presents two independent sources:

- **Defaults** — Manual / No Preset, Provider Defaults, Default · Balanced, Empty · Clean Slate.
- **User Presets** — existing full Image workspace/UI snapshots.

Persistence remains separate. IR-2 changes only who owns the current sampling fields and how authority is handed off safely.

## Authority semantics

### Manual / No Preset
- `sampling_preset_id = ""`
- no preset values are injected;
- no managed fields are cleared;
- current editable field values remain as they are;
- later route changes do not reapply any built-in sampling recipe.

### Provider Defaults
- managed sampling overrides are cleared from the Image draft;
- `sampling_preset_id = "provider_defaults"`;
- the provider/compiler is the intended sampling authority;
- sampler/scheduler display provider-owned state while numeric managed fields are not faked from Image base defaults.

### Default · Balanced
- resolves through the existing IP built-in registry for the active family + variant + loader + workflow;
- clears prior managed sampling fields, then applies the resolved route values;
- source/canvas workflows keep inherited width/height dropped when the registry says source/canvas owns resolution;
- active Balanced authority re-resolves after family, loader, workflow, or route-significant variant/model changes;
- if no Balanced recipe exists for the new route, Neo falls back to Manual / No Preset instead of guessing.

### Empty · Clean Slate
- clears managed sampling fields once;
- `sampling_preset_id = "empty_clean_slate"`;
- blank required fields are visibly rendered as unset rather than silently borrowing Image base defaults;
- sampler/scheduler expose an `Unset · manual value required` option;
- manual values entered after Clean Slate remain under Clean Slate authority for later backend validation.

## User workspace preset handoff
User Presets remain full workspace snapshots under `/api/ui-presets/image`.

When a User Preset is loaded:
- its captured field values become authoritative;
- hidden built-in sampling authority is removed;
- `sampling_preset_id` is cleared;
- later family/workflow changes do not silently reapply a previously selected built-in.

When an Image workspace preset is saved or updated, its serialized Image draft strips built-in sampling metadata and stores `sampling_preset_id` as empty. This lets the snapshot capture current values without embedding a second invisible authority.

## Manual edits after a built-in
IR-2 locks an explicit handoff rule:

- editing a managed sampling field while **Default · Balanced** or **Provider Defaults** owns sampling immediately switches the unified selector to **Manual / No Preset** and preserves the user's edit;
- **Empty · Clean Slate** is the exception because it is intentionally an authoring template; editing its blank fields keeps Clean Slate selected and marks those fields as manually authored.

This prevents a browser field from looking custom while a hidden built-in would later overwrite it.

## Headless resolver bridge
IR-1 stopped `image_sampling_presets.js` from auto-mounting. IR-2 therefore supplies the already-loaded `/api/image/base` contract from `neo.js` into the headless sampling resolver through `setBaseContract()`.

There is still one visible Image preset selector and one loaded base contract; no duplicate fetch or duplicate preset panel is introduced.

## Route refresh lock
Built-in authority is re-resolved after:
- Model Family changes;
- Main Model Type / loader changes;
- Workflow Mode changes;
- route-significant model/variant changes such as Klein variant or component model changes.

Manual and User Preset authorities are not auto-reapplied on route changes.

## IR-3 integration status
IR-3 is implemented. The Image job payload now carries the active built-in `sampling_preset_id`, carries Output Intent separately, omits hidden built-in authority for Manual/User selections, and reuses one prepared `NeoJob` for provider execution plus registry/context proof. CFG/negative-prompt live UX repair remains IR-4. Scene Director route/mount recovery remains IR-5/IR-6.
