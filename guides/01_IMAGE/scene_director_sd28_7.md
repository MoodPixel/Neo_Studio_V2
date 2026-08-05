# Scene Director — SD-28.7 UX, Inspector, Regression + Release Lock

> **SD-28.8 documentation note:** This is a phase-history document. For the current released Scene Director architecture, support matrix, proof semantics, and apply order, use `guides/01_IMAGE/scene_director_current.md`. Historical gates/statuses below describe this phase at the time it shipped and must not override the current guide.


## Purpose

SD-28.7 is the release-hardening boundary for the Scene Director modernization series. It does **not** add another model-family execution engine. It locks the architecture introduced by SD-28.1 through SD-28.6, makes runtime state understandable in the read-only Inspector, and fails closed if a future graph mutation violates the validated contract.

## Released engines

| Engine | Families | Loader | Modes | Release state |
|---|---|---|---|---|
| `classic_v054` | SDXL | checkpoint | Generate / Img2Img / Inpaint | Available, frozen |
| `classic_v054` | SD1.5 | checkpoint | Generate / Img2Img / Inpaint | Experimental, frozen |
| `lightweight_regional` | Krea 2 RAW / Turbo | diffusion_model / GGUF | Generate / Img2Img / Inpaint | Available, release locked |
| `lightweight_regional` | FLUX.2 Klein | diffusion_model / GGUF | Generate / Img2Img / Inpaint | Available, release locked |
| `lightweight_regional` | Z-Image / Z-Image Turbo | diffusion_model / GGUF | Generate / Img2Img / Inpaint | Available, release locked |

Outpaint remains `planned_gated`. A gated route is considered safe only when Scene Director leaves the provider graph unchanged.

## Release lock

`backend/release_lock.py` evaluates the compiled Scene Director result after the family-specific compiler finishes and before the graph is returned to Neo.

Schema:

`neo.image.scene_director.release_lock.v1`

Modern blocking invariants:

- exact backend/family/loader/mode whitelist;
- no new `KSampler`, `KSamplerAdvanced`, `SamplerCustom`, or `SamplerCustomAdvanced` nodes;
- no standard `LoraLoader` / `LoraLoaderModelOnly` fallback for a regional LoRA;
- no `NeoSceneDirectorV054` insertion on a lightweight route;
- exactly one `NeoRegionalLoRADelta` wrapper when regional LoRA execution is active, otherwise none;
- provider sampler count and parameters remain preserved;
- provider latent input remains preserved;
- `global_model_mutation` is never true;
- no heavy SD repair chain or hidden repair sampler;
- fallback policy remains fail-closed rather than cross-family/global/finish-pass fallback;
- the lightweight graph contract must remain healthy after mutation.

If a blocking invariant fails, Scene Director discards its mutated graph and returns the exact provider workflow it received. The Inspector records the blocker. No alternate execution path is attempted.

Classic V054 is release-locked separately: it remains checkpoint-only, SDXL/SD1.5-only, and must not gain the modern regional-LoRA runtime wrapper.

## GPU proof is separate from support

Release lock is a **compile/graph contract**, not a visual leakage benchmark.

A route can be:

- **Available + Release Locked + GPU Proof Pending** — expected before a live regional-LoRA execution proves the delta hook;
- **Available + Release Locked + GPU Proven** — live runtime proof has been supplied for the run;
- **Release Blocked** — the graph violated an invariant and was reverted;
- **Gated Safe** — the route is unsupported/planned-gated and no graph mutation occurred.

Compile-time code must never promote `runtime_gpu_proven` on its own.

## Inspector v2

Schema:

`neo.image.scene_director.inspector.v2`

The Inspector remains read-only and exposes six panels:

1. **Overview** — region, subject, regional prompt, regional LoRA, sampler, blocker, and warning counts.
2. **Route & Engine** — backend, family, loader, mode, route state, engine, and fallback policy.
3. **Regional LoRA Compatibility** — family adapter compatibility/preflight rows for targeted LoRAs.
4. **Runtime Proof** — one-sampler preservation, parameter/latent preservation, global mutation, regional-LoRA route count, runtime state, and contract health.
5. **Release Lock** — every release invariant with OK/blocker state.
6. **Diagnostics** — validation and release-lock warnings/blockers.

Status chips summarize:

- Route
- Engine
- Regional Prompt
- Regional LoRA
- GPU Proof
- Release Lock

The companion `ui/release_inspector.js` only renders metadata. It does not register generation actions or mutate Scene Director state.

## Preflight UX

`payload_schema_dispatch.py` attaches a preflight Inspector to modern normalized payload metadata. Preflight can state that a feature is available, but it intentionally reports the release lock as `preflight` and GPU proof as not proven. The final release lock is evaluated only after graph compilation.

## Regression lock

The release suite covers:

- every modern family × both supported loaders × Generate/Img2Img/Inpaint;
- Outpaint remains planned-gated;
- SDXL and SD1.5 remain on classic V054;
- no modern→V054 fallback;
- no new sampler insertion;
- no standard LoRA-loader fallback;
- no multiple regional-LoRA wrappers;
- release-lock failure reverts to the original provider graph;
- GPU proof pending is not a compile blocker and is never fabricated;
- Inspector panels/chips remain present;
- previous Krea, Klein, Z-Image, lightweight prompt, and regional-LoRA regressions remain green.

## Release discipline

SD-28.7 is the lock point for the current Scene Director modernization. Changes after this phase that alter the supported route matrix, add sampler passes, change family adapters, relax fallback rules, or change proof semantics require a new explicit phase and regression update instead of silently modifying the release contract.
