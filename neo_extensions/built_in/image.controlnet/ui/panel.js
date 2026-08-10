(function () {
  const EXTENSION_ID = 'image.controlnet';
  const SOURCE = 'image.reference.controlnet';
  const ACTIVE_STATES = ['available', 'experimental_available'];
  const CONTROLNET_TASKS = ['map_control', 'inpaint_control', 'outpaint_control'];
  const POSE_METHODS = ['controlnet', 'qwen_transfer'];
  const DEFAULT_POSE_INSTRUCTION = 'Make the person in image 1 match the exact pose from image 2. Use image 3 as the extracted pose map. Preserve identity, clothing, background, lighting, and style unless the prompt explicitly requests a change.';
  function normalizeTask(value) { return CONTROLNET_TASKS.includes(value) ? value : 'map_control'; }
  function normalizeQwenAdapter(value) { const text = String(value || 'auto').trim().toLowerCase(); if (['diffsynth','diff_synth','model_patch','model-patch','patch'].includes(text)) return 'diffsynth'; if (['instantx','instant_x','native_controlnet','controlnet'].includes(text)) return 'instantx'; return 'auto'; }
  function normalizeFluxAdapter(value) { const text = String(value || 'auto').trim().toLowerCase().replace(/-/g, '_'); if (['fun_union','flux2_fun_union','flux_2_fun_union','flux2','klein','klein_fun','flux2_klein'].includes(text)) return 'fun_union'; if (['alimama','flux_inpaint','flux_controlnet_inpaint','inpaint','controlnet'].includes(text)) return 'alimama'; return 'auto'; }
  function normalizeBackend(value) { const text = String(value || '').trim().toLowerCase(); return text === 'comfy' ? 'comfyui' : text; }
  function normalizeMode(value) { const text = String(value || '').trim().toLowerCase(); if (text === 'image_to_image') return 'img2img'; return text; }
  function poseTransferRouteSupported(route = {}) {
    return ['comfyui', 'comfyui_portable'].includes(normalizeBackend(route.backend))
      && String(route.family || '').trim().toLowerCase() === 'qwen_image_edit_2511'
      && ['diffusion_model', 'gguf'].includes(String(route.loader || '').trim().toLowerCase())
      && ['img2img', 'edit'].includes(normalizeMode(route.workflow_mode || route.mode));
  }
  const DEFAULT_UNIT = {
    uid: 'unit_1', enabled: true, unit: 'canny', model: '', preprocessor: 'canny', strength: 0.45,
    start_percent: 0, end_percent: 1, fit_mode: 'contain', detect_resolution: 512,
    safe_mode: true, invert_map: false, save_intermediate: false, canny_low: 100, canny_high: 200,
    openpose_body: true, openpose_hand: false, openpose_face: false, advanced_enabled: false,
    advanced_engine: 'auto', strength_schedule: 'flat', weight_preset: 'balanced', mask_mode: 'none',
    batch_mode: 'auto', sliding_context: false, pose_method: 'controlnet', pose_reference_lane: 2,
    pose_map_lane: 3, pose_base_lora: '', pose_helper_lora: '', pose_base_strength: 0.70,
    pose_helper_strength: 0.70, pose_prompt_instruction: DEFAULT_POSE_INSTRUCTION,
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
    const poseMethod = unit === 'openpose' && POSE_METHODS.includes(data.pose_method) ? data.pose_method : 'controlnet';
    const preprocessor = poseMethod === 'qwen_transfer' ? 'dwpose' : (VALID_PREPROCESSORS.includes(data.preprocessor) ? data.preprocessor : defaultPreprocessor);
    const clean = {
      uid: String(data.uid || `unit_${index + 1}`),
      enabled: data.enabled !== false,
      unit,
      pose_method: poseMethod,
      model: poseMethod === 'qwen_transfer' ? '' : String(data.model || '').trim(),
      preprocessor,
      strength: clampNumber(data.strength, 0, 2, 0.45),
      start_percent: clampNumber(data.start_percent, 0, 1, 0),
      end_percent: clampNumber(data.end_percent, 0, 1, 1),
      fit_mode: ['contain', 'cover', 'stretch', 'native'].includes(data.fit_mode) ? data.fit_mode : 'contain',
      detect_resolution: clampNumber(data.detect_resolution, 64, 4096, 512),
      safe_mode: data.safe_mode !== false,
      invert_map: Boolean(data.invert_map),
      save_intermediate: Boolean(data.save_intermediate),
      advanced_enabled: poseMethod === 'qwen_transfer' ? false : Boolean(data.advanced_enabled),
      advanced_engine: poseMethod === 'qwen_transfer' ? 'auto' : (Boolean(data.advanced_enabled) ? (['auto', 'standard', 'advanced_controlnet'].includes(data.advanced_engine) ? data.advanced_engine : 'auto') : 'auto'),
      strength_schedule: ['flat', 'linear', 'ease_in', 'ease_out', 'ease_in_out'].includes(data.strength_schedule) ? data.strength_schedule : 'flat',
      weight_preset: ['balanced', 'prompt_strong', 'control_strong', 'soft', 'strict'].includes(data.weight_preset) ? data.weight_preset : 'balanced',
      mask_mode: poseMethod === 'qwen_transfer' ? 'none' : (['none', 'control_mask', 'inpaint_mask'].includes(data.mask_mode) ? data.mask_mode : 'none'),
      batch_mode: ['auto', 'repeat', 'clamp', 'strict'].includes(data.batch_mode) ? data.batch_mode : 'auto',
      sliding_context: Boolean(data.sliding_context),
    };
    if (poseMethod === 'qwen_transfer') {
      clean.pose_reference_lane = 2;
      clean.pose_map_lane = 3;
      clean.pose_base_lora = String(data.pose_base_lora || '').trim();
      clean.pose_helper_lora = String(data.pose_helper_lora || '').trim();
      clean.pose_base_strength = clampNumber(data.pose_base_strength, 0, 2, 0.70);
      clean.pose_helper_strength = clampNumber(data.pose_helper_strength, 0, 2, 0.70);
      clean.pose_prompt_instruction = String(data.pose_prompt_instruction || DEFAULT_POSE_INSTRUCTION).trim() || DEFAULT_POSE_INSTRUCTION;
    }
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
    const poseTransferActive = units.some((unit) => unit.pose_method === 'qwen_transfer') && poseTransferRouteSupported(route);
    const active = Boolean(applied && units.length && (routeControlsEnabled(route) || poseTransferActive));
    return {
      extensions: {
        [EXTENSION_ID]: {
          enabled: active,
          version: 1,
          inputs: active ? { units } : {},
          params: active ? { advanced_controlnet_requested: units.some((unit) => unit.advanced_enabled), batch_policy: settings.batch_policy || 'auto', controlnet_task: normalizeTask(settings.controlnet_task || 'map_control'), qwen_controlnet_adapter: normalizeQwenAdapter(settings.qwen_controlnet_adapter || settings.params?.qwen_controlnet_adapter || 'auto'), flux_controlnet_adapter: normalizeFluxAdapter(settings.flux_controlnet_adapter || settings.params?.flux_controlnet_adapter || 'auto') } : {},
          assets: active ? (settings.assets || {}) : {},
          metadata: { source: SOURCE, schema: 'neo.image.controlnet.v1', route_state: poseTransferActive ? 'experimental_available' : (route.route_state || 'unknown'), controlnet_task: normalizeTask(settings.controlnet_task || 'map_control') }
        }
      }
    };
  }
  function syncPoseUi(root) {
    const unitSelect = root.querySelector('[data-controlnet-field="unit"]');
    const methodSelect = root.querySelector('[data-controlnet-field="pose_method"]');
    const isPose = unitSelect?.value === 'openpose';
    const isTransfer = isPose && methodSelect?.value === 'qwen_transfer';
    root.querySelectorAll('[data-controlnet-pose-method-row]').forEach((node) => { node.hidden = !isPose; });
    root.querySelectorAll('[data-controlnet-standard-settings]').forEach((node) => { node.hidden = isTransfer; });
    root.querySelectorAll('[data-controlnet-pose-transfer-settings]').forEach((node) => { node.hidden = !isTransfer; });
  }
  function updateChip(root) {
    const enabled = !!root.querySelector('[data-controlnet-field="enabled"]')?.checked;
    const state = root.dataset.routeState || 'unknown';
    const chip = root.querySelector('[data-controlnet-state]');
    if (!chip) return;
    const method = root.querySelector('[data-controlnet-field="pose_method"]')?.value;
    const unit = root.querySelector('[data-controlnet-field="unit"]')?.value;
    const transfer = unit === 'openpose' && method === 'qwen_transfer';
    const routeReady = ACTIVE_STATES.includes(state) || transfer;
    chip.dataset.controlnetState = enabled && routeReady ? 'enabled' : (routeReady ? 'disabled' : 'gated');
    chip.textContent = enabled && transfer ? 'Pose Transfer' : (enabled && routeReady ? 'Enabled' : (routeReady ? 'Disabled' : 'Not available'));
  }
  function initControlNetPanel(root) {
    if (!root || root.dataset.controlnetReady === 'true') return;
    root.dataset.controlnetReady = 'true';
    const refresh = () => { syncPoseUi(root); updateChip(root); };
    root.addEventListener('change', refresh);
    root.addEventListener('input', refresh);
    refresh();
  }
  window.NeoControlNet = { EXTENSION_ID, SOURCE, DEFAULT_UNIT, normalizeTask, normalizeQwenAdapter, normalizeFluxAdapter, poseTransferRouteSupported, normalizeUnit, cleanUnits, buildPayload, initControlNetPanel };
  document.querySelectorAll('[data-extension-id="image.controlnet"]').forEach(initControlNetPanel);
})();
