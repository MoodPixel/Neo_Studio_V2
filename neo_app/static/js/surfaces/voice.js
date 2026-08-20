// Neo Studio V2 surface module: voice — VO-R12B visual modernization over the current VO-R12A layout parity shell.
(function () {
  const api = {
    surfaceId: 'voice',
    releaseStage: 'batch',
    status: 'active_batch_runtime',
    legacyStatus: 'surface_reinstated_common_settings_pending',
    migratedAreas: [
      'surface_contract',
      'surface_reinstatement_r1',
      'base_common_voice_contract_r2',
      'provider_capability_routing_r3',
      'generation_runtime_r4',
      'preview_results_r5',
      'reference_clone_r6',
      'voice_profile_assets_r7',
      'provider_specific_controls_r8',
      'finish_runtime_r9',
      'dialogue_multispeaker_r10',
      'batch_runtime_r11',
      'workspace_app_navigation',
      'output_paths',
      'workspace_shell',
      'backend_adapter_contract',
      'chatterbox_adapter_foundation',
      'runtime_workspace',
      'full_render_chunking',
      'reference_upload_qc',
      'clone_lane_v1',
      'saved_voice_profiles',
      'capability_aware_controls',
      'queue_history_recovery_export',
      'kokoro_low_end_adapter',
      'fish_speech_hq_adapter',
      'dialogue_multispeaker_lane',
      'batch_script_import',
      'voice_finish_tools',
      'replay_metadata_memory',
      'surface_layout_parity_r12a',
      'workspace_navigation_flattening_r12a',
      'subtab_visual_modernization_r12b',
      'voice_scoped_control_styling_r12b',
    ],
    policy: 'VO-R11 keeps VO-R2→R10 contracts authoritative and activates bounded Batch orchestration. One selected backend owns each Batch; items reuse current R4 TTS, R6 Clone, or R10 Dialogue jobs, child audio remains in the shared Results registry, historical VO-V12 Dialogue stays compatibility-only behind current R10 Dialogue, historical VO-V13 Batch stays compatibility-only, and historical VO-V14 Finish stays compatibility-only behind the current R9 Finish runtime.',
    diagnostics: { status: 'active', runtime: 'batch_runtime_active', risk: 'low', phase: 'VO-R11', visualPhase: 'VO-R12B' },
    baseContractEndpoint: '/api/voice/base-contract',
    baseContractValidateEndpoint: '/api/voice/base-contract/validate',
    providerRoutingEndpoint: '/api/voice/provider-routing',
    providerControlsEndpoint: '/api/voice/provider-controls',
    generationEndpoint: '/api/voice/generate',
    generationJobsEndpoint: '/api/voice/generation/jobs',
    resultsEndpoint: '/api/voice/results',
    resultDetailEndpoint: '/api/voice/results/{job_id}',
    resultReplayEndpoint: '/api/voice/results/{job_id}/replay',
    resultDownloadEndpoint: '/api/voice/results/{job_id}/download',
    resultOpenFolderEndpoint: '/api/voice/results/{job_id}/open-folder',
    referencesEndpoint: '/api/voice/references',
    referenceUploadEndpoint: '/api/voice/references/upload',
    referenceAnalyzeEndpoint: '/api/voice/references/{reference_id}/analyze',
    referenceAttestEndpoint: '/api/voice/references/{reference_id}/attest',
    cloneGenerationEndpoint: '/api/voice/clone/generate',
    cloneJobEndpoint: '/api/voice/clone/jobs/{job_id}',
    profileAssetsEndpoint: '/api/voice/profile-assets',
    profileAssetDetailEndpoint: '/api/voice/profile-assets/{asset_id}',
    profileAssetApplyEndpoint: '/api/voice/profile-assets/{asset_id}/apply',
    finishRuntimeCapabilitiesEndpoint: '/api/voice/finish-runtime/capabilities',
    finishRuntimeProcessEndpoint: '/api/voice/finish-runtime/process',
    finishRuntimeSplitEndpoint: '/api/voice/finish-runtime/split',
    finishRuntimeMergeEndpoint: '/api/voice/finish-runtime/merge',
    finishRuntimeHistoryEndpoint: '/api/voice/finish-runtime/history',
    finishRuntimeJobEndpoint: '/api/voice/finish-runtime/jobs/{job_id}',
    dialogueRuntimeCapabilitiesEndpoint: '/api/voice/dialogue-runtime/capabilities',
    dialogueRuntimeParseEndpoint: '/api/voice/dialogue-runtime/parse',
    dialogueRuntimeGenerateEndpoint: '/api/voice/dialogue-runtime/generate',
    dialogueRuntimeJobEndpoint: '/api/voice/dialogue-runtime/jobs/{job_id}',
    batchRuntimeCapabilitiesEndpoint: '/api/voice/batch-runtime/capabilities',
    batchRuntimeImportEndpoint: '/api/voice/batch-runtime/import',
    batchRuntimeRunEndpoint: '/api/voice/batch-runtime/{batch_id}/run',
    batchRuntimeGetEndpoint: '/api/voice/batch-runtime/{batch_id}',
    batchRuntimePollEndpoint: '/api/voice/batch-runtime/{batch_id}/poll',
    batchRuntimeRetryEndpoint: '/api/voice/batch-runtime/{batch_id}/retry-item',
    batchRuntimeHistoryEndpoint: '/api/voice/batch-runtime/history',
    commonFieldIds: ['script', 'language', 'model_id', 'voice_id', 'speaking_rate', 'output_format', 'split_long_text', 'max_chunk_chars', 'punctuation_cleanup'],
    capabilityEndpoint: '/api/voice/capabilities',
    capabilityControlsEndpoint: '/api/voice/capability-controls',
    queueEndpoint: '/api/voice/queue',
    historyEndpoint: '/api/voice/history',
    exportsEndpoint: '/api/voice/exports',
    dialogueEndpoint: '/api/voice/dialogue',
    batchImportEndpoint: '/api/voice/batch/import',
    batchHistoryEndpoint: '/api/voice/batch/history',
    finishEndpoint: '/api/voice/finish',
    finishSplitEndpoint: '/api/voice/finish/split',
    finishMergeEndpoint: '/api/voice/finish/merge',
    finishHistoryEndpoint: '/api/voice/finish/history',
    replayHistoryEndpoint: '/api/voice/replays',
    memoryEventsEndpoint: '/api/voice/memory-events',
    lowEndBackend: { provider_id: 'kokoro', profile_id: 'voice.kokoro', family: 'kokoro_preview', badge: 'Low-VRAM / Lightweight' },
    hqBackend: { provider_id: 'fish_speech', profile_id: 'voice.fish_speech', family: 'fish_hq', badge: 'HQ / Advanced', warnings: ['higher_vram_expected', 'slower_startup', 'advanced_setup'] },
    controlZones: ['default', 'advanced', 'backend_native'],
    renderers: {},
    actions: {},
  };


  api.getProfileAssetEndpoints = function getProfileAssetEndpoints(assetId) {
    const id = encodeURIComponent(assetId || '{asset_id}');
    return {
      list: api.profileAssetsEndpoint,
      detail: `/api/voice/profile-assets/${id}`,
      apply: `/api/voice/profile-assets/${id}/apply`,
    };
  };

  api.profileAssetPolicy = Object.freeze({
    auto_switch_backend_profile: false,
    stores_script: false,
    stores_provider_native_controls: false,
    legacy_v7_auto_promotion: false,
  });


  api.commonSettingsStatus = function commonSettingsStatus(draft, contractPayload) {
    const defaults = contractPayload?.defaults || {};
    const source = draft || {};
    const script = String(source.script_body ?? source.script ?? defaults.script ?? '');
    return {
      schema_id: 'neo.voice.common_settings.ui.v1',
      phase: 'VO-R10',
      field_ids: api.commonFieldIds.slice(),
      has_script: Boolean(script.trim()),
      generation_execution: true,
      provider_capability_routing: true,
    };
  };

  api.providerRoutingStatus = function providerRoutingStatus(payload) {
    const routing = payload || {};
    return {
      schema_id: routing.schema_id || 'neo.voice.provider_routing.v1',
      phase: 'VO-R10',
      routing_ready: routing.routing_ready === true,
      profile_id: routing.profile?.profile_id || '',
      provider_id: routing.profile?.provider_id || '',
      model_count: Array.isArray(routing.models?.items) ? routing.models.items.length : 0,
      voice_count: Array.isArray(routing.voices?.items) ? routing.voices.items.length : 0,
      generation_execution: true,
    };
  };

  api.getCommonControlState = function getCommonControlState(routingPayload, fieldId) {
    return routingPayload?.common_controls?.[fieldId] || { visible: true, enabled: true, authority: 'common_contract' };
  };

  api.getResultEndpoints = function getResultEndpoints(jobId) {
    const id = encodeURIComponent(jobId || '{job_id}');
    return {
      list: api.resultsEndpoint,
      detail: `/api/voice/results/${id}`,
      replay: `/api/voice/results/${id}/replay`,
      download: `/api/voice/results/${id}/download`,
      openFolder: `/api/voice/results/${id}/open-folder`,
    };
  };

  api.resultReplayPolicy = Object.freeze({
    auto_switch_backend_profile: false,
    cross_profile_model_voice: 'provider_default',
    common_fields: api.commonFieldIds.slice(),
  });

  api.cloneRouteStatus = function cloneRouteStatus(routingPayload, referenceAsset) {
    const caps = routingPayload?.capabilities || {};
    return {
      phase: 'VO-R10',
      supported: caps.voice_clone === true && caps.reference_audio === true,
      backend_ready: routingPayload?.health?.reachable === true,
      reference_ready: referenceAsset?.clone_ready === true,
      reference_id: referenceAsset?.reference_id || '',
    };
  };

  api.getReferenceEndpoints = function getReferenceEndpoints(referenceId) {
    const id = encodeURIComponent(referenceId || '{reference_id}');
    return {
      list: api.referencesEndpoint,
      upload: api.referenceUploadEndpoint,
      detail: `/api/voice/references/${id}`,
      analyze: `/api/voice/references/${id}/analyze`,
      attest: `/api/voice/references/${id}/attest`,
    };
  };

  api.getCloneEndpoints = function getCloneEndpoints(jobId) {
    const id = encodeURIComponent(jobId || '{job_id}');
    return { generate: api.cloneGenerationEndpoint, poll: `/api/voice/clone/jobs/${id}` };
  };

  api.getCurrentDialogueEndpoints = function getCurrentDialogueEndpoints(jobId) {
    const id = encodeURIComponent(jobId || '{job_id}');
    return {
      capabilities: api.dialogueRuntimeCapabilitiesEndpoint,
      parse: api.dialogueRuntimeParseEndpoint,
      generate: api.dialogueRuntimeGenerateEndpoint,
      poll: `/api/voice/dialogue-runtime/jobs/${id}`,
    };
  };

  api.dialogueRuntimePolicy = Object.freeze({
    phase: 'VO-R10',
    one_selected_backend_profile: true,
    child_runtime_authority: ['VO-R4 TTS', 'VO-R6 Reference Clone'],
    speaker_sources: ['built_in', 'profile_asset', 'reference_clone'],
    combined_output: 'real_ffmpeg_stitch_only',
    placeholder_audio: false,
    auto_switch_backend_profile: false,
    batch_released: true,
  });

  api.getVisibleControls = function getVisibleControls(capabilityPayload, zone) {
    const manifest = capabilityPayload?.control_manifest || capabilityPayload?.ui_manifest || capabilityPayload || {};
    const zones = manifest.zones || {};
    if (zone) return (zones[zone] || []).filter((control) => control.visible !== false && control.enabled !== false);
    return (manifest.controls || []).filter((control) => control.visible !== false && control.enabled !== false);
  };

  api.getVisibleSources = function getVisibleSources(capabilityPayload) {
    const manifest = capabilityPayload?.control_manifest || capabilityPayload?.ui_manifest || capabilityPayload || {};
    return (manifest.source_options || []).filter((source) => source.visible !== false && source.enabled !== false);
  };

  api.isKokoroCapability = function isKokoroCapability(capabilityPayload) {
    const family = capabilityPayload?.family || capabilityPayload?.features?.backend_profile_id || '';
    const runtime = capabilityPayload?.runtime || capabilityPayload?.backend?.provider_id || '';
    return family === 'kokoro_preview' || runtime === 'kokoro';
  };

  api.isFishCapability = function isFishCapability(capabilityPayload) {
    const family = capabilityPayload?.family || capabilityPayload?.features?.backend_profile_id || '';
    const runtime = capabilityPayload?.runtime || capabilityPayload?.backend?.provider_id || '';
    return family === 'fish_hq' || runtime === 'fish_speech';
  };

  api.getBackendWarnings = function getBackendWarnings(capabilityPayload) {
    if (!api.isFishCapability(capabilityPayload)) return [];
    return capabilityPayload?.features?.runtime_warning
      ? [capabilityPayload.features.runtime_warning]
      : api.hqBackend.warnings;
  };

  api.getBackendBadge = function getBackendBadge(capabilityPayload) {
    return capabilityPayload?.backend_badge || capabilityPayload?.control_manifest?.backend_badge || '';
  };

  api.canUseBatch = function canUseBatch(capabilityPayload) {
    if (capabilityPayload?.schema_id === 'neo.voice.batch_capabilities.v1') return capabilityPayload.ready === true;
    const flags = capabilityPayload?.support_flags || capabilityPayload?.control_manifest?.support_flags || {};
    return Boolean(flags.supports_tts || flags.supports_render);
  };

  api.batchRuntimePolicy = Object.freeze({
    phase: 'VO-R11',
    one_selected_backend_profile: true,
    native_provider_batch_required: false,
    child_runtime_authority: ['VO-R4 TTS', 'VO-R6 Reference Clone', 'VO-R10 Dialogue'],
    max_concurrency: 4,
    parent_audio_output: false,
    terminal_parent_immutable: true,
  });

  api.getBatchEndpoints = function getBatchEndpoints(batchId) {
    const id = encodeURIComponent(batchId || '{batch_id}');
    return {
      capabilities: api.batchRuntimeCapabilitiesEndpoint,
      import: api.batchRuntimeImportEndpoint,
      history: api.batchRuntimeHistoryEndpoint,
      get: `/api/voice/batch-runtime/${id}`,
      run: `/api/voice/batch-runtime/${id}/run`,
      poll: `/api/voice/batch-runtime/${id}/poll`,
      retryItem: `/api/voice/batch-runtime/${id}/retry-item`,
    };
  };

  api.describeImportTypes = function describeImportTypes() {
    return ['txt', 'md', 'csv', 'json', 'srt'];
  };

  api.canUseFinish = function canUseFinish(capabilityPayload) {
    const flags = capabilityPayload?.support_flags || capabilityPayload?.control_manifest?.support_flags || {};
    return Boolean(flags.supports_finish_tools || flags.supports_render);
  };

  api.getCurrentFinishEndpoints = function getCurrentFinishEndpoints(jobId) {
    const id = encodeURIComponent(jobId || '{job_id}');
    return {
      capabilities: api.finishRuntimeCapabilitiesEndpoint,
      process: api.finishRuntimeProcessEndpoint,
      split: api.finishRuntimeSplitEndpoint,
      merge: api.finishRuntimeMergeEndpoint,
      history: api.finishRuntimeHistoryEndpoint,
      job: `/api/voice/finish-runtime/jobs/${id}`,
    };
  };

  api.finishRuntimePolicy = Object.freeze({
    provider_independent: true,
    neo_owned_sources_only: true,
    non_destructive_child_outputs: true,
    shared_registry_authority: true,
    placeholder_audio: false,
    legacy_v14_mounted: false,
  });

  api.getFinishEndpoints = function getFinishEndpoints() {
    return {
      finish: api.finishEndpoint,
      split: api.finishSplitEndpoint,
      merge: api.finishMergeEndpoint,
      history: api.finishHistoryEndpoint,
    };
  };

  api.describeFinishTools = function describeFinishTools() {
    return ['normalize', 'silence_trim', 'noise_cleanup', 'loudness_target', 'convert_audio', 'split_chunks', 'merge_chunks'];
  };


  api.getReplayEndpoints = function getReplayEndpoints(jobId) {
    const id = encodeURIComponent(jobId || '{job_id}');
    return {
      jobReplay: `/api/voice/jobs/${id}/replay`,
      replayHistory: api.replayHistoryEndpoint,
      memoryEvents: api.memoryEventsEndpoint,
    };
  };

  api.canUseReplayMemory = function canUseReplayMemory(job) {
    return Boolean(job?.output_file || job?.replay_metadata || job?.memory_export);
  };


  api.parseDialoguePreview = function parseDialoguePreview(script) {
    const turns = [];
    let speaker = 'Narrator';
    String(script || '').split(/\r?\n/).forEach((raw) => {
      const line = raw.trim();
      if (!line) return;
      const block = line.match(/^\[([^\]]+)\]$/);
      const colon = line.match(/^([A-Za-z0-9_ .'-]{1,64})\s*:\s*(.+)$/);
      if (block) { speaker = block[1].trim() || 'Narrator'; return; }
      if (colon) { speaker = colon[1].trim() || speaker; turns.push({ speaker, text: colon[2].trim() }); return; }
      turns.push({ speaker, text: line });
    });
    return { speaker_count: new Set(turns.map((turn) => turn.speaker)).size, turn_count: turns.length, turns };
  };

  api.canUseDialogue = function canUseDialogue(capabilityPayload) {
    const flags = capabilityPayload?.support_flags || capabilityPayload?.control_manifest?.support_flags || {};
    return Boolean(flags.supports_dialogue || flags.supports_speaker_mapping);
  };

  api.getJobActions = function getJobActions(job) {
    return Array.isArray(job?.available_actions) ? job.available_actions : [];
  };

  api.canExport = function canExport(job, format) {
    const formats = job?.export_state?.formats || ['wav', 'mp3'];
    return Boolean(job?.output_file) && formats.includes(format || 'wav');
  };

  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('voice', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules.voice = api;
  }
})();
