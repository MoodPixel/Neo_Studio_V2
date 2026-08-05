# Scene Director — SD-28.8 Documentation + System Records

> **Superseded surface note — SD-28.9:** SD-28.8 remains the documentation close-out record for the SD-28.1→28.8 architecture, but SD-28.9 subsequently restores the extension-owned editor UI and conditional node-readiness contract. Use `scene_director_current.md` for current behavior.


## Purpose

SD-28.8 closes the SD-28 modernization series without changing runtime execution. Its job is to make the released architecture auditable and difficult to misread after seven implementation phases.

## Documentation decisions

1. `scene_director_current.md` is the canonical current-state guide.
2. SD-28.1 through SD-28.7 guides remain phase history and receive an explicit historical-document banner.
3. `scene_director_live_validation.md` defines the live GPU qualification procedure and preserves the distinction between static support, runtime proof and visible leakage measurement.
4. System records mirror the same hierarchy: one current-state architecture record plus immutable phase records.
5. Historical gates are not deleted. They remain useful evidence of why later adapters and release locks exist.

## Runtime boundary

No generation/runtime implementation changes are introduced by SD-28.8:

- execution strategy remains SD-28.7;
- release-lock schema remains `neo.image.scene_director.release_lock.v1`;
- Inspector schema remains `neo.image.scene_director.inspector.v2`;
- modern released families remain Krea 2 RAW/Turbo, FLUX.2 Klein and Z-Image Base/Turbo;
- modern loaders remain `diffusion_model` and GGUF;
- modern modes remain Generate, Img2Img and Inpaint;
- modern Outpaint remains planned-gated;
- classic V054 remains frozen for SDXL/SD1.5.

## Documentation precedence

When reading the repository:

1. current architecture guide/record;
2. SD-28.7 release lock contract;
3. latest family-specific phase guide;
4. earlier historical phase guide.

A historical statement such as “Klein LoRA is gated” from SD-28.3 is correct history but is not the current support state after SD-28.5.

## Regression requirement

Because this is documentation-only, the acceptance condition is stricter than a normal feature phase: the complete SD-28.7 runtime test suite must pass unchanged. A documentation change must not become an excuse to alter runtime behavior or weaken a regression.
