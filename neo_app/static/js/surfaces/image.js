// Neo Studio V2 surface module: image
// Runtime behavior still has safe legacy wrappers until each surface is migrated intentionally.
(function () {
  const api = {
    surfaceId: 'image',
    releaseStage: 'ready',
    status: 'ready',
    migratedAreas: [],
    policy: 'Image workspace uses the current Neo Studio V2 generation UI.',
    diagnostics: { status: 'pending', fallback: 'legacy neo.js still owns behavior', risk: 'low' },
    renderers: {},
    actions: {},
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('image', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules['image'] = api;
  }
})();
