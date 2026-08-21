# Neo Studio Qwen3-TTS Worker Adapter

Phase 4 keeps the isolated physical Qwen3-TTS worker and managed Voice Engine manifest/model registry, then activates installed/runtime-ready **CustomVoice** models in the normal Voice TTS surface. **Phase 4.2 hardens repeated generation by reusing lifecycle-confirmed resident models without re-charging the full cold-load VRAM floor.** The worker remains eligible for gateway launch only when runtime/model probes pass, and catalog/control discovery remains non-starting. Base clone and VoiceDesign are still gated.

```text
Neo Studio Voice
  -> Neo Voice Engine :8790
      -> managed Qwen route (Phase 4 CustomVoice UI active when installed)
          -> Qwen3-TTS worker :8792
              -> Neo_Runtime/voice/envs/qwen3_tts
              -> Neo_Runtime/voice/models/qwen3_tts
```

## Worker endpoints

```text
GET  /api/voice/health
GET  /api/voice/capabilities
GET  /api/voice/models
GET  /api/voice/model-registry
GET  /api/voice/voices
GET  /api/voice/controls
GET  /api/voice/models/{model_id}/lifecycle
POST /api/voice/models/{model_id}/load
POST /api/voice/models/{model_id}/unload
POST /api/voice/render
GET  /api/voice/jobs/{provider_job_id}
GET  /api/voice/jobs/{provider_job_id}/output
POST /api/voice/jobs/{provider_job_id}/cancel
```

The worker is single-GPU/single-resident-model by design. Switching Qwen model IDs unloads the previous model first. After each generation it calls CUDA cache cleanup for **unoccupied transient allocator blocks only**; the selected model weights remain resident so later same-model jobs can use scheduler `resident_reuse` admission.

## Supported upstream roles

- 1.7B CustomVoice -> `tts`, built-in speakers, instruction control.
- 0.6B CustomVoice -> `tts`, built-in speakers, **no instruction control in the current upstream wrapper**.
- 1.7B / 0.6B Base -> `voice_clone` with ICL transcript mode or x-vector-only quick clone.
- 1.7B VoiceDesign -> dedicated `voice_design`; the worker supports it directly but the current gateway task grammar and normal Voice UI remain intentionally gated.

## Models

The Phase 4.5.7 runtime resolver checks, in order:

```text
1. Neo_Runtime/voice/models/qwen3_tts/<neo_model_id>/
2. Neo_Runtime/voice/models/qwen3_tts/<upstream_repo_name>/
3. authoritative Admin-managed Hugging Face cache snapshot
```

The HF cache path is accepted only after the shared requested-revision/materialization/Qwen-content probe reports `installed`. Normal managed/direct launch sets `NEO_QWEN3_TTS_LOCAL_ONLY=1`, so missing, stale, partial, corrupt, or unverified snapshots fail before `Qwen3TTSModel.from_pretrained()` receives a remote repo ID. The engine retains repo-ID fallback only for explicit development/test use when local-only is deliberately disabled.

A Phase 3 snapshot is considered complete only when the root model/processor/tokenizer assets and the bundled `speech_tokenizer` config/weights pass the shared model-registry probe.

## Environment controls

- `NEO_QWEN3_TTS_DEVICE=auto|cuda|cuda:N|cpu`
- `NEO_QWEN3_TTS_DTYPE=auto|bfloat16|float16|float32`
- `NEO_QWEN3_TTS_ATTN=auto|flash_attention_2|sdpa|eager|default`
- `NEO_QWEN3_TTS_MODEL_ROOT=<path>`
- `NEO_QWEN3_TTS_LOCAL_ONLY=1`
- `NEO_QWEN3_TTS_ALLOW_EXTERNAL_REFERENCE=1` — development only; normal clone references must stay under Neo's authorized reference root.

FlashAttention 2 is optional and is not installed by Neo's Windows setup script by default.

## Phase 3+ install boundary

- Run `setup_qwen3_tts_backend.bat` to build/verify `Neo_Runtime/voice/envs/qwen3_tts/`. A `.neo_qwen3_tts_ready` marker is written only after import/CUDA checks succeed.
- Run `download_qwen3_tts_model.bat --list` or `--status`, then pass one explicit Neo model ID to download a full Hugging Face snapshot.
- Managed worker launch sets `NEO_QWEN3_TTS_LOCAL_ONLY=1`; missing/incomplete snapshots fail before model load instead of triggering an implicit Hub download.
- The worker exposes `GET /api/voice/model-registry` for executable runtime state, including `legacy_runtime_snapshot` versus `huggingface_cache_snapshot` source kind.

## Phase 4.2 scheduler/lifecycle contract

- First load uses the manifest's normal cold-load VRAM floor.
- Same-model reuse is allowed only when the gateway scheduler record, managed-worker state, and read-only worker lifecycle all agree that the exact model is resident.
- Resident reuse does not reserve the model's VRAM a second time; it still requires the gateway's generic safety reserve.
- Stale/unavailable lifecycle state fails closed to cold-load semantics.
- Qwen generation cleanup releases transient CUDA allocator cache without unloading the resident model.
- The first physical 0.6B CUDA/WAV generation succeeded; the post-hotfix 6–10 generation resident-reuse stress test remains pending.
