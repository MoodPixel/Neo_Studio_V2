// Neo Studio V2 surface module: board
// Runtime behavior still has safe legacy wrappers until each surface is migrated intentionally.
(function () {
  const api = {
    surfaceId: 'board',
    releaseStage: 'planned',
    status: 'planned',
    migratedAreas: [],
    policy: 'Board is a future planned workspace placeholder in this preview release.',
    diagnostics: { status: 'pending', fallback: 'legacy neo.js still owns behavior', risk: 'low' },
    renderers: {},
    actions: {},
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('board', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules['board'] = api;
  }
})();
