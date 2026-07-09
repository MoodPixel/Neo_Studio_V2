// Neo Studio V2 — Surface Module Runtime
// Public surface registry for ready, preview, and planned workspaces.
(function () {
  const registry = window.NeoSurfaceModules = window.NeoSurfaceModules || {};
  const calls = [];

  function normalizeModule(surfaceId, module) {
    return Object.assign({
      surfaceId,
      releaseStage: module?.releaseStage || module?.status || 'preview',
      status: module?.status || 'registered',
      migratedAreas: module?.migratedAreas || [],
      actions: module?.actions || {},
      renderers: module?.renderers || {},
      diagnostics: module?.diagnostics || {},
    }, module || {});
  }

  function register(surfaceId, module) {
    if (!surfaceId) return null;
    const existing = registry[surfaceId] || {};
    const normalized = normalizeModule(surfaceId, Object.assign({}, existing, module || {}));
    normalized.registeredAt = new Date().toISOString();
    registry[surfaceId] = normalized;
    return normalized;
  }

  function get(surfaceId) {
    return registry[surfaceId] || null;
  }

  function list() {
    return Object.values(registry).map((module) => ({
      surfaceId: module.surfaceId,
      releaseStage: module.releaseStage || module.status || 'preview',
      status: module.status,
      migratedAreas: module.migratedAreas || [],
      rendererCount: Object.keys(module.renderers || {}).length,
      actionCount: Object.keys(module.actions || {}).length,
      diagnostics: module.diagnostics || {},
    }));
  }

  function recordCall(entry) {
    calls.push(Object.assign({ at: new Date().toISOString() }, entry));
    if (calls.length > 80) calls.shift();
  }

  function invoke(surfaceId, area, payload) {
    const module = get(surfaceId);
    const fn = module?.renderers?.[area] || module?.actions?.[area];
    if (typeof fn !== 'function') return undefined;
    const started = Date.now();
    try {
      const result = fn(payload || {});
      if (result && typeof result.then === 'function') {
        recordCall({ surfaceId, area, status: 'pending', elapsedMs: 0 });
        return result.then((value) => {
          recordCall({ surfaceId, area, status: 'ok', elapsedMs: Date.now() - started });
          return value;
        }).catch((error) => {
          recordCall({ surfaceId, area, status: 'failed', error: error?.message || String(error), elapsedMs: Date.now() - started });
          console.warn(`[NeoSurfaceRuntime] ${surfaceId}.${area} failed`, error);
          throw error;
        });
      }
      recordCall({ surfaceId, area, status: 'ok', elapsedMs: Date.now() - started });
      return result;
    } catch (error) {
      recordCall({ surfaceId, area, status: 'failed', error: error?.message || String(error), elapsedMs: Date.now() - started });
      console.warn(`[NeoSurfaceRuntime] ${surfaceId}.${area} failed`, error);
      return undefined;
    }
  }

  function status() {
    return {
      schema_id: 'neo.frontend.surface_runtime.status.v1',
      release_stage: 'public_preview',
      status: 'ready',
      module_count: Object.keys(registry).length,
      modules: list(),
      recent_calls: calls.slice(-12).reverse(),
    };
  }

  window.NeoSurfaceRuntime = { register, get, list, invoke, status };
})();
