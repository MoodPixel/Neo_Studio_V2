// Neo Studio V2 surface module: prompt_captioning
// Runtime behavior still has safe legacy wrappers until each surface is migrated intentionally.
(function () {
  const api = {
    surfaceId: 'prompt_captioning',
    releaseStage: 'ready',
    status: 'ready',
    migratedAreas: [],
    policy: 'Prompt & Captioning is available in this preview release.',
    diagnostics: { status: 'pending', fallback: 'legacy neo.js still owns behavior', risk: 'low' },
    renderers: {},
    actions: {},
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('prompt_captioning', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules['prompt_captioning'] = api;
  }
})();
