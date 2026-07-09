(() => {
  const EXTENSION_ID = 'cfg_fix_dynamic_thresholding';
  const PRESET_DEFAULTS = Object.freeze({
    off: { enabled: false, mimic_scale: 7.0, threshold_percentile: 1.0 },
    safe: { enabled: true, mimic_scale: 7.0, threshold_percentile: 1.0 },
    detail_push: { enabled: true, mimic_scale: 7.0, threshold_percentile: 0.99 },
    aggressive: { enabled: true, mimic_scale: 6.0, threshold_percentile: 0.98 },
    advanced: { enabled: true, mimic_scale: 7.0, threshold_percentile: 0.99 },
  });
  function resolvePreset(preset, cfg = 7) {
    if (preset === 'smart_auto') {
      if (cfg >= 16) return { enabled: true, mimic_scale: 6.0, threshold_percentile: 0.98 };
      if (cfg >= 12) return { enabled: true, mimic_scale: 7.0, threshold_percentile: 0.99 };
      if (cfg >= 8) return { enabled: true, mimic_scale: 7.5, threshold_percentile: 1.0 };
      return { enabled: true, mimic_scale: 7.0, threshold_percentile: 1.0 };
    }
    return PRESET_DEFAULTS[preset] || PRESET_DEFAULTS.off;
  }
  function shouldShowForRoute(routeState, detailMode = 'guided') {
    if (routeState === 'available' || routeState === 'experimental_available') return true;
    return detailMode === 'expert' && (routeState === 'planned_gated' || routeState === 'provider_gated');
  }
  function controlsEnabledForRoute(routeState) {
    return routeState === 'available' || routeState === 'experimental_available';
  }
  window.NeoBuiltInExtensions = window.NeoBuiltInExtensions || {};
  window.NeoBuiltInExtensions[EXTENSION_ID] = {
    id: EXTENSION_ID,
    phase: 'C',
    mount: 'image.generate.model_loader.after',
    workspace_app: 'generations',
    route_aware: true,
    display_modes: ['compact', 'guided', 'expert'],
    presets: Object.keys(PRESET_DEFAULTS).concat(['smart_auto']),
    resolvePreset,
    shouldShowForRoute,
    controlsEnabledForRoute,
  };
})();
