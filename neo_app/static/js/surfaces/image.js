// Neo Studio V2 surface module: image
// IR-1 unified preset selector foundation.
(function () {
  const presetApi = () => window.NeoImageSamplingPresets || null;
  const api = {
    surfaceId: 'image',
    releaseStage: 'ready',
    status: 'ready',
    migratedAreas: ['sampling_presets', 'output_intent', 'provider_capability_overlays'],
    policy: 'The live Image workspace exposes one unified Preset selector and selected-profile capability overlays. Provider catalogs, resolution rules, route controls, and extension gating must come from the active backend profile rather than cross-profile fallbacks.',
    diagnostics: {
      status: presetApi() ? 'ready' : 'pending',
      fallback: 'neo.js#workspaceUiPreset owns visible preset UI; no second sampling preset panel auto-mounts',
      risk: 'low',
      phase: 'Forge Neo Phase 4 / IR-1',
    },
    renderers: {
      samplingPresets: () => presetApi()?.status?.(),
    },
    actions: {
      refreshSamplingPresets: (payload) => presetApi()?.refresh?.(payload || {}),
      applySamplingPreset: () => presetApi()?.applySelected?.('surface_action'),
      saveSamplingPresetAs: (payload) => presetApi()?.saveAs?.(payload?.name || ''),
      duplicateSamplingPreset: () => presetApi()?.duplicateSelected?.(),
      renameSamplingPreset: () => presetApi()?.renameSelected?.(),
      deleteSamplingPreset: () => presetApi()?.deleteSelected?.(),
      resetSamplingPreset: () => presetApi()?.reset?.(),
      setOutputIntent: (payload) => presetApi()?.setOutputIntent?.(payload?.intent || payload?.output_intent || 'none'),
      samplingPresetStatus: () => presetApi()?.status?.(),
    },
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('image', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules.image = api;
  }
})();
