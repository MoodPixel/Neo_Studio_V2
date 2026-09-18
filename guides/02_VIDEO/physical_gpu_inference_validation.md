---
guide_id: video.physical_gpu_inference_validation
title: Physical GPU Inference Validation
surface: video
scope: built_in
applies_to: [video_lora_stack, comfyui, release_validation]
tags: [video, lora, gpu, inference, validation, evidence]
priority: 90
version: 1
updated: 2026-09-11
---

# Physical GPU Inference Validation

Phase 18 adds the release evidence runner that separates graph-contract success from real inference success.

A case passes only when Neo queues the real route Generate function, receives a Comfy prompt ID, observes a completed `/history/{prompt_id}` entry, finds an output artifact, and reads bytes from `/view`. Compile success alone never sets `physical_inference_proven=true`.

## Safe workflow

1. Copy `phase18_gpu_validation_plan.example.json` outside the repository or rename it.
2. Replace every `CHANGE_ME` model, LoRA, and source-media value.
3. Validate without spending GPU time:

```bash
python -m neo_app.video.physical_gpu_validation --plan my_gpu_plan.json
python -m neo_app.video.physical_gpu_validation --plan my_gpu_plan.json --list-cases
```

4. Run one case first:

```bash
python -m neo_app.video.physical_gpu_validation --plan my_gpu_plan.json --evidence-dir gpu_evidence --case h3_img2vid_turbo --run
```

5. Run or resume the complete configured matrix:

```bash
python -m neo_app.video.physical_gpu_validation --plan my_gpu_plan.json --evidence-dir gpu_evidence --resume --run
```

The runner never queues unless `--run` is explicit and every case explicitly sets `dry_run=false` plus `expected.output_required=true`.

## Evidence

Each case writes an atomic JSON file under `evidence-dir/cases`. The aggregate `report.json` passes only when every selected case has physical output proof. Evidence records route, request, prompt ID, applied LoRA metadata, patch profile, history state, output filename/type, readable byte count, sampled SHA-256, timestamps, and errors.

Large generated videos are not copied into the evidence folder. The runner samples at most the first MiB through Comfy `/view` to prove the artifact is readable without duplicating heavy media.

## Release matrix

For every advertised route, test no-LoRA and one standard LoRA. Add speed-only and mixed cases where supported. WAN dual-noise additionally requires `all`, `high`, and `low`. Source-based routes require real accessible media. Missing-node, empty-catalog, missing-file, and incompatible-loader cases should remain expected compile failures in a separate negative plan; they are not inference-pass cases.

The bundled example is intentionally small and contains placeholders. It must not be run unchanged.
