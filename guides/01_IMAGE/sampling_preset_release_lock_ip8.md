# IP-8 — Sampling Preset Regression, Inspector + Documentation Lock

## Status

**Locked.** IP-8 is the final release contract for the Image sampling-preset program introduced in IP-1 through IP-7.

## Runtime order

1. **IP-6 Output Intent normalization** — metadata only.
2. **IP-3/IP-4/IP-5/IP-7 Sampling Preset resolution** — built-in or user preset.
3. **IP-2 Negative Prompt Eligibility** — derives the executable negative branch from the effective guidance state.
4. **IP-8 Sampling Preset Release Lock** — checks invariants and records `locked` or `blocked` before provider compile/run.
5. **IP-8 Inspector** — observational snapshot of the final contract state.
6. Provider validation/compiler/runtime.

The release lock is intentionally inspectable at `NeoJob` construction. A blocked preset does not make the job object impossible to inspect; the provider validation boundary refuses execution before compile/run.

## Regression matrix

`build_sampling_preset_regression_matrix()` expands every concrete immutable `Default · Balanced` registry selector into family × variant × loader × workflow contexts. It verifies:

- every concrete Balanced row resolves uniquely;
- Img2Img/Edit/Inpaint/Outpaint effective presets do not carry Txt2Img `width`/`height`;
- FLUX.2 Klein Base remains 50 steps / Guidance 4 and Distilled remains 4 steps / Guidance 1 for explicit 4B/9B variants;
- FLUX.1 Components Inpaint/Outpaint remains the internal Fill recipe while GGUF masked routes retain normal FLUX guidance;
- FLUX/Klein negative prompting stays `DISABLED_ROUTE` under the current compiler contract;
- Krea 2 Turbo and Z-Image Turbo negative prompting stays `DISABLED_FAMILY`.

The matrix is generated from the registry rather than maintained as a second numeric preset table.

## Release-lock states

- `locked` — selected preset state satisfies all IP-8 invariants.
- `blocked` — provider validation must refuse execution.
- `not_applicable` — no sampling preset was selected, or the job is not Image. Legacy/manual Image jobs remain compatible.

Fail-closed checks include:

- unavailable/incomplete selected preset;
- Provider Defaults retaining stale managed sampling values;
- Clean Slate receiving hidden preset values;
- Output Intent mutating sampling/creative fields;
- effective negative text contradicting IP-2 eligibility;
- image-workflow Balanced presets carrying Txt2Img dimensions.

## Inspector

The Inspector exposes seven panels:

1. Route
2. Preset
3. Inheritance
4. Sampling semantics
5. Output Intent
6. Negative prompt eligibility
7. Release lock

The backend Inspector is authoritative for prepared job metadata. The browser panel is explicitly an **authoring preflight** and does not claim runtime/GPU proof.

For privacy and provenance, the Inspector never copies negative-prompt text. It reports only whether user/effective negative text is present.

## User presets

User sampling presets remain portable JSON under:

`neo_data/image/sampling_presets`

They remain route-scoped to family + variant + loader + workflow. Output Intent stays separate and is never captured into a user sampling preset.

## Runtime proof boundary

IP-8 validates contracts, payload transformations, route isolation, and release invariants. It does **not** claim visual quality, LoRA leakage, image fidelity, or GPU runtime correctness without a real backend generation.

## Ownership lock

| Concern | Owner |
|---|---|
| Sampling/guidance capability semantics | IP-1 `sampling_guidance_registry.py` |
| Negative prompt execution eligibility | IP-2 `negative_prompt_eligibility.py` |
| Preset resolver / Clean Slate / Provider Defaults | IP-3 `sampling_presets.py` |
| Family Balanced defaults | IP-4 built-in preset registry |
| Workflow inheritance / Fill-vs-GGUF behavior | IP-5 built-in preset registry + resolver |
| Output Intent | IP-6 `output_intents.py` |
| User authoring + browser UI | IP-7 `user_sampling_presets.py` + `image_sampling_presets.js` |
| Regression matrix + release gate | IP-8 `sampling_preset_release_lock.py` |
| Final prepared-job Inspector | IP-8 `sampling_preset_inspector.py` |
