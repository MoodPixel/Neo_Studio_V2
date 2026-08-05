# Phase IP-7 — Preset UI + User Preset Authoring

Status: **Implemented**

IP-7 makes the IP-3→IP-6 sampling architecture visible and authorable in the Image Parameters workspace without merging sampling, creative intent, prompts, styles, or extensions into one preset object.

## UI model

The Image Parameters workspace now exposes two independent controls:

1. **Sampling Preset**
   - Defaults: `Provider Defaults` and `Default · Balanced` when the active route supports it.
   - Templates: `Empty · Clean Slate`.
   - My Presets: user-authored sampling-only JSON records that match the active family / variant / loader / workflow.
2. **Output Intent**
   - `None`
   - `Realistic`
   - `Anime / Illustration`

Output Intent remains the IP-6 metadata-only layer. Selecting or changing it does not change sampler, scheduler, steps, CFG/guidance, denoise, dimensions, prompts, negative prompts, styles, LoRAs, embeddings, or extension payloads.

## Built-ins are immutable

Repository built-ins remain read-only. IP-7 does not edit `sampling_presets_builtin.json` when the user changes controls.

Built-in actions:

- **Apply** — materialize the selected built-in for the current route.
- **Duplicate** — create an independent user preset containing the currently resolved sampling values.
- **Reset** — reapply the selected preset.

Rename and Delete are disabled for built-ins.

## My Presets

User presets are portable JSON files stored under:

```text
neo_data/image/sampling_presets
```

They use schema:

```text
neo.image.sampling_preset.user.v1
```

Each record stores only:

- name / description,
- family,
- variant,
- loader,
- workflow,
- managed sampling values,
- optional `base_preset_id` provenance,
- creation/update timestamps.

The stored intent selector is always wildcard `*`. Output Intent is not captured by a sampling preset.

### Authoring actions

- **Save As** — snapshot current managed sampling values into a new My Preset.
- **Duplicate** — clone a resolved built-in or existing user preset into a new My Preset.
- **Rename** — rename a user preset in place.
- **Delete** — delete a user preset only.
- **Reset** — reapply the selected preset to discard manual sampling edits.

IP-7 does not define an automatic user-default preset. Selection stays explicit.

## Storage/API bridge

Neo already exposes generic UI-preset CRUD at `/api/ui-presets/{surface}`. IP-7 reserves the surface id:

```text
image_sampling
```

Requests to `/api/ui-presets/image_sampling` are delegated to the dedicated sampling-only store above. Existing whole-UI presets continue to use `neo_data/ui_presets/<surface>` with their previous schema and behavior.

No additional FastAPI route family is required.

## Route-aware availability

The visible preset catalog is recalculated when family, model/variant, loader, or workflow changes.

A user preset is eligible only when its saved route context matches the active:

```text
family + variant + loader + workflow
```

A saved wildcard variant may match the active variant. A mismatched workflow or loader does not appear as an applicable My Preset.

If the currently selected preset becomes invalid after a route change, the UI falls back to `Provider Defaults` and reapplies it rather than carrying incompatible values forward.

## Sampling-only safety boundary

User records are validated against the same managed-field authority used by the built-in sampling registry. A user preset cannot store prompt or extension fields.

Examples of rejected fields include:

```text
positive_prompt
negative_prompt
styles
lora
embedding
controlnet_units
extensions
```

Applying a user preset clears/replaces only managed sampling fields. Existing prompt text, negative-prompt draft, ControlNet/extension state, Scene Director state, and other creative inputs remain untouched.

## UI mounting boundary

`image_sampling_presets.js` is additive. It mounts only when an Image Parameters target or proven Image workspace is present. A generic `data-section-id="params"` belonging to another Neo surface is not a valid mount target by itself.

The module registers through the Image surface runtime and does not depend on legacy `neo.js` behavior.

## Runtime authority

Frontend application is convenience and visibility; the backend remains authoritative. Submitted `sampling_preset_id` is resolved again by `neo_app/image/sampling_presets.py` at the Image job boundary before IP-2 negative-prompt eligibility and provider compilation.

The existing ordering remains:

```text
IP-6 Output Intent normalization
        ↓
IP-3/IP-4/IP-5/IP-7 Sampling Preset resolution
        ↓
IP-2 Negative Prompt Eligibility
        ↓
Provider validation / compile
```

## Boundary

IP-7 does not add Quality/Fast presets, intent-specific sampling, prompt/style presets, or GPU-derived automatic tuning. Those remain outside this phase. IP-8 owns the final regression/inspector/documentation release lock.

## IP-8.1 live-UI mount correction

The original IP-7 DOM fixture used an explicit `data-image-params-root`. The legacy live Image renderer can expose the same controls without that marker, causing the module to load without inserting the dropdown. IP-8.1 adds a strict field-signature fallback (Image family + route control + at least two sampling controls) and retries the mount when Image/extension state changes. The preset data/authoring contract itself is unchanged.

## IR-1 superseding UI note
IR-1 replaces the separate visible sampling-preset panel with one unified Image `Preset` selector owned by `neo.js`. The IP-7 sampling resolver/authoring API remains available, but its standalone panel no longer auto-mounts. Built-in sampling definitions and existing workspace preset storage remain separate internally.


## IR-2 authority handoff note
IR-2 makes the unified IR-1 selector authoritative for authoring state. Built-in sampling defaults are applied through the headless IP resolver, User Presets own their captured workspace values without retaining hidden built-in `sampling_preset_id` state, and manual edits under Balanced/Provider Defaults release to Manual. Clean Slate remains an authoring template and preserves values entered after its reset.
