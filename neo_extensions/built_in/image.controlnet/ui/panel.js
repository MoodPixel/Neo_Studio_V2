(function () {
  const EXTENSION_ID = 'image.controlnet';
  const SOURCE = 'image.reference.controlnet';
  const ACTIVE_STATES = ['available', 'experimental_available'];
  const CONTROLNET_TASKS = ['map_control', 'inpaint_control', 'outpaint_control'];
  function normalizeTask(value) { return CONTROLNET_TASKS.includes(value) ? value : 'map_control'; }
function normalizeQwenAdapter(value) { const text = String(value || 'auto').trim().toLowerCase(); if (['diffsynth','diff_synth','model_patch','model-patch','patch'].includes(text)) return 'diffsynth'; if (['instantx','instant_x','native_controlnet','controlnet'].includes(text)) return 'instantx'; return 'auto'; }
  function normalizeFluxAdapter(value) { const text = String(value || 'auto').trim().toLowerCase().replace(/-/g, '_'); if (['fun_union','flux2_fun_union','flux_2_fun_union','flux2','klein','klein_fun','flux2_klein'].includes(text)) return 'fun_union'; if (['alimama','flux_inpaint','flux_controlnet_inpaint','inpaint','controlnet'].includes(text)) return 'alimama'; return 'auto'; }
  const DEFAULT_UNIT = {
    uid: 'unit_1', enabled: true, unit: 'canny', model: '', preprocessor: 'canny', strength: 0.45,
    start_percent: 0, end_percent: 1, fit_mode: 'contain', detect_resolution: 512,
    safe_mode: true, invert_map: false, save_intermediate: false, canny_low: 100, canny_high: 200,
    openpose_body: true, openpose_hand: false, openpose_face: false, advanced_enabled: false,
    advanced_engine: 'auto', strength_schedule: 'flat', weight_preset: 'balanced', mask_mode: 'none',
    batch_mode: 'auto', sliding_context: false
  };
  const VALID_UNITS = ['auto', 'canny', 'depth', 'openpose', 'lineart', 'lineart_anime', 'softedge', 'tile', 'normalbae', 'scribble'];
  const VALID_PREPROCESSORS = [...VALID_UNITS, 'dwpose', 'none'];
  function clampNumber(value, min, max, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(min, Math.min(max, number));
  }
  function normalizeUnit(raw = {}, index = 0) {
    const data = { ...DEFAULT_UNIT, ...(raw || {}) };
    const unit = VALID_UNITS.includes(data.unit) ? data.unit : 'canny';
    const defaultPreprocessor = unit === 'auto' ? 'none' : unit;
    const preprocessor = VALID_PREPROCESSORS.includes(data.preprocessor) ? data.preprocessor : defaultPreprocessor;
    const clean = {
      uid: String(data.uid || `unit_${index + 1}`),
      enabled: data.enabled !== false,
      unit,
      model: String(data.model || '').trim(),
      preprocessor,
      strength: clampNumber(data.strength, 0, 2, 0.45),
      start_percent: clampNumber(data.start_percent, 0, 1, 0),
      end_percent: clampNumber(data.end_percent, 0, 1, 1),
      fit_mode: ['contain', 'cover', 'stretch', 'native'].includes(data.fit_mode) ? data.fit_mode : 'contain',
      detect_resolution: clampNumber(data.detect_resolution, 64, 4096, 512),
      safe_mode: data.safe_mode !== false,
      invert_map: Boolean(data.invert_map),
      save_intermediate: Boolean(data.save_intermediate),
      advanced_enabled: Boolean(data.advanced_enabled),
      advanced_engine: Boolean(data.advanced_enabled) ? (['auto', 'standard', 'advanced_controlnet'].includes(data.advanced_engine) ? data.advanced_engine : 'auto') : 'auto',
      strength_schedule: ['flat', 'linear', 'ease_in', 'ease_out', 'ease_in_out'].includes(data.strength_schedule) ? data.strength_schedule : 'flat',
      weight_preset: ['balanced', 'prompt_strong', 'control_strong', 'soft', 'strict'].includes(data.weight_preset) ? data.weight_preset : 'balanced',
      mask_mode: ['none', 'control_mask', 'inpaint_mask'].includes(data.mask_mode) ? data.mask_mode : 'none',
      batch_mode: ['auto', 'repeat', 'clamp', 'strict'].includes(data.batch_mode) ? data.batch_mode : 'auto',
      sliding_context: Boolean(data.sliding_context),
    };
    if (clean.end_percent < clean.start_percent) clean.end_percent = 1;
    if (unit === 'canny' || preprocessor === 'canny') {
      clean.canny_low = clampNumber(data.canny_low, 0, 255, 100);
      clean.canny_high = Math.max(clean.canny_low, clampNumber(data.canny_high, 0, 255, 200));
    }
    if (unit === 'openpose' || ['openpose', 'dwpose'].includes(preprocessor)) {
      clean.openpose_body = data.openpose_body !== false;
      clean.openpose_hand = Boolean(data.openpose_hand);
      clean.openpose_face = Boolean(data.openpose_face);
    }
    return clean;
  }
  function cleanUnits(units = []) {
    const seen = new Set();
    return (Array.isArray(units) ? units : []).map(normalizeUnit).filter((unit, index) => {
      if (!unit.enabled) return false;
      if (seen.has(unit.uid)) unit.uid = `${unit.uid}_${index + 1}`;
      seen.add(unit.uid);
      return true;
    });
  }
  function routeControlsEnabled(route = {}) { return ACTIVE_STATES.includes(route.route_state); }
  function buildPayload(settings = {}, route = {}, applied = false) {
    const units = cleanUnits(settings.units || []);
    const active = Boolean(applied && units.length && routeControlsEnabled(route));
    return {
      extensions: {
        [EXTENSION_ID]: {
          enabled: active,
          version: 1,
          inputs: active ? { units } : {},
          params: active ? { advanced_controlnet_requested: units.some((unit) => unit.advanced_enabled), batch_policy: settings.batch_policy || 'auto', controlnet_task: normalizeTask(settings.controlnet_task || 'map_control'), qwen_controlnet_adapter: normalizeQwenAdapter(settings.qwen_controlnet_adapter || settings.params?.qwen_controlnet_adapter || 'auto'), flux_controlnet_adapter: normalizeFluxAdapter(settings.flux_controlnet_adapter || settings.params?.flux_controlnet_adapter || 'auto') } : {},
          assets: active ? (settings.assets || {}) : {},
          metadata: { source: SOURCE, schema: 'neo.image.controlnet.v1', route_state: route.route_state || 'unknown', controlnet_task: normalizeTask(settings.controlnet_task || 'map_control'), ui_phase: 'M-task-selector-ui' }
        }
      }
    };
  }
  function updateChip(root) {
    const enabled = !!root.querySelector('[data-controlnet-field="enabled"]')?.checked;
    const state = root.dataset.routeState || 'unknown';
    const chip = root.querySelector('[data-controlnet-state]');
    if (!chip) return;
    const routeReady = ACTIVE_STATES.includes(state);
    chip.dataset.controlnetState = enabled && routeReady ? 'enabled' : (routeReady ? 'disabled' : 'gated');
    chip.textContent = enabled && routeReady ? 'Enabled' : (routeReady ? 'Disabled' : 'Route gated');
  }
  function initControlNetPanel(root) {
    if (!root || root.dataset.controlnetReady === 'true') return;
    root.dataset.controlnetReady = 'true';
    root.addEventListener('change', () => updateChip(root));
    root.addEventListener('input', () => updateChip(root));
    updateChip(root);
  }
  window.NeoControlNet = { EXTENSION_ID, SOURCE, DEFAULT_UNIT, normalizeTask, normalizeQwenAdapter, normalizeFluxAdapter, normalizeUnit, cleanUnits, buildPayload, initControlNetPanel };
  document.querySelectorAll('[data-extension-id="image.controlnet"]').forEach(initControlNetPanel);
})();
