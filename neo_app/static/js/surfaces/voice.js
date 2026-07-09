// Neo Studio V2 surface module: voice planned workspace placeholder.
(function () {
  const api = {
    surfaceId: 'voice',
    releaseStage: 'planned',
    status: 'contract_active_project_ui_decoupled',
    migratedAreas: [
      'surface_contract',
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
    ],
    policy: 'Voice workflows are planned for a future Neo Studio V2 update and are not active in this preview release.',
    diagnostics: { status: 'active', runtime: 'project_ui_decoupled', risk: 'low' },
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
    const flags = capabilityPayload?.support_flags || capabilityPayload?.control_manifest?.support_flags || {};
    return Boolean(flags.supports_batch || flags.supports_render);
  };

  api.getBatchEndpoints = function getBatchEndpoints(batchId) {
    const id = encodeURIComponent(batchId || '{batch_id}');
    return {
      import: api.batchImportEndpoint,
      history: api.batchHistoryEndpoint,
      get: `/api/voice/batch/${id}`,
      render: `/api/voice/batch/${id}/render`,
      retryItem: `/api/voice/batch/${id}/retry-item`,
    };
  };

  api.describeImportTypes = function describeImportTypes() {
    return ['txt', 'md', 'csv', 'json', 'srt'];
  };

  api.canUseFinish = function canUseFinish(capabilityPayload) {
    const flags = capabilityPayload?.support_flags || capabilityPayload?.control_manifest?.support_flags || {};
    return Boolean(flags.supports_finish_tools || flags.supports_render);
  };

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
