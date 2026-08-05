# Forge Neo generic extension discovery and bridge E3

Status: **implemented offline / physical validation required**  
Date: **2026-07-31**

E3 adds a provider-owned discovery/bridge layer for Forge extensions installed and managed by Forge itself. It does **not** copy Forge extensions into Neo Studio's extension registry and it does **not** make arbitrary scripts executable by name alone.

## Ownership model

```text
Forge Extension Manager
        owns install / update / enable / disable / remove
                    ↓
Forge /sdapi/v1/extensions + /scripts + /script-info
                    ↓
Neo selected-profile discovery
                    ↓
classification
  ├─ Neo-mapped → existing E1/E2 adapter
  ├─ generic bridge ready → primitive external script only
  └─ adapter required → visible in Admin, not executable
```

The Image workspace receives one Neo-owned built-in surface: `image.forge_script_bridge`. It is a dynamic provider bridge, not a mirror of Forge's Gradio UI.

## Generic bridge eligibility

A script is auto-bridgeable only when all of the following are true:

1. it is attributable to an **enabled external Forge extension** returned by Forge extension discovery;
2. `/sdapi/v1/script-info` exposes a complete API argument schema;
3. every argument is a primitive control that Neo can validate safely:
   - boolean;
   - integer;
   - number;
   - text;
   - string choice/dropdown;
4. no argument label suggests image, mask, file, folder, path, upload, canvas, gallery, JSON/object, or batch-input semantics;
5. the script exposes at most 24 arguments;
6. the selected route is SD 1.5 or SDXL in E3;
7. the submitted schema fingerprint matches the current live Forge schema.

If any requirement fails, the script is classified `adapter_required` and remains visible in Forge Admin diagnostics without becoming executable in the Image workspace.

## Dedicated adapters always win

The generic bridge never duplicates scripts already owned by Neo mappings. Current examples include:

- ControlNet;
- ADetailer;
- ImageStitch Integrated;
- PiD Integrated;
- Spectrum Integrated;
- MultiDiffusion Integrated.

High-Res Lab and Image Upscale also remain on their existing Neo-owned provider mappings rather than entering E3.

## Schema fingerprints

Each bridgeable script receives a fingerprint derived from:

- script name;
- txt2img/img2img mode;
- always-on/selectable invocation type;
- argument index and label;
- inferred primitive type;
- default value;
- numeric limits/step;
- dropdown choices.

A request must submit the fingerprint that was current when its controls were staged. If a Forge extension update changes the schema, Neo rejects the stale payload and asks for a profile refresh rather than sending positional arguments into a changed API.

## Invocation rules

### Always-on scripts

Bridge-safe always-on scripts compile into:

```json
{
  "alwayson_scripts": {
    "Script Name": {"args": []}
  }
}
```

They can stack with each other and with dedicated Neo mappings as long as script names do not collide.

### Selectable scripts

Forge accepts one selectable script per generation request. E3 therefore allows at most one generic selectable script and compiles it to `script_name` + `script_args`. If the active workflow already owns a selectable script, the generic selection is rejected.

## Route policy

Forge does not publish reliable per-model-family compatibility metadata for arbitrary third-party scripts. E3 therefore limits generic execution to:

- SD 1.5 checkpoint routes;
- SDXL checkpoint routes;
- txt2img when Forge publishes a txt2img script schema;
- img2img/inpaint/outpaint when Forge publishes the corresponding img2img script schema.

Modern families remain `adapter_required` until a dedicated adapter or physical-validation-backed compatibility rule is added.

## Forge Admin UX

**Admin → Backends → Image → Forge / Forge Neo** now shows a Forge Extensions & Script Bridge section with:

- external extensions reported by Forge;
- enabled/disabled state;
- version/branch metadata where available;
- scripts classified as `generic_bridge_ready`, `neo_mapped`, or `adapter_required`;
- schema fingerprints;
- fail-closed reasons.

Installation, update, disable/remove, and repository operations remain in Forge's native Extension Manager.

## Image workspace UX

`Image · Forge Script Bridge` shows only bridge-safe scripts for the current Forge mode. Each script gets Neo-generated primitive controls from the live sanitized schema. Complex scripts never receive guessed controls.

## Privacy

The provider catalog keeps portable names, branch/version, shortened commit hash, primitive script metadata, and schema fingerprints. Forge filesystem paths and extension remote URLs are not persisted through the E3 bridge contract.

## Physical validation boundary

E3 automated tests prove discovery/classification/validation/payload compilation only. Before calling a third-party script physically validated, test the exact extension version, Forge version, model family, workflow, generated output, and conflict behavior on a real Forge installation.
