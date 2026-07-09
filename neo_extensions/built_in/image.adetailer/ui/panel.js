(() => {
  const EXTENSION_ID = 'image.adetailer';
  const MOUNT_SLOT = 'image.finish.adetailer';
  const PHASE = 'G';
  const ACTIVE_STATES = new Set(['available', 'experimental_available']);
  const DIAGNOSTIC_STATES = new Set(['planned_gated', 'provider_gated']);
  const DEFAULT_PARAMS = {
    enabled: false,
    detector_model: '',
    detector_type: 'bbox',
    confidence: 0.30,
    top_k: 1,
    bbox_grow: 16,
    mask_blur: 4,
    denoise: 0.35,
    steps: 20,
    cfg: null,
    positive_prompt: '',
    negative_prompt: '',
    sam_model: '',
    custom_classes: '',
    target_order: 'area_desc',
    target_split_mode: 'sep_prompt_targets',
    manual_boxes: '',
  };

  const ROUTE_REASONS = {
    available: 'ADetailer is available for this Image Finish route when required Impact Pack nodes are present.',
    experimental_available: 'ADetailer is available experimentally for this checkpoint route; validate runtime visual parity.',
    planned_gated: 'This workspace or mode needs more canvas/mask validation before ADetailer can safely mutate the graph.',
    provider_gated: 'Required Comfy Impact Pack nodes or a compatible Comfy image provider are not available.',
    unsupported: 'No safe Impact Pack patch path is proven for this family/loader/mode. No fallback is allowed.',
    unchecked: 'Route has not been resolved yet.',
  };

  function asNumber(value, fallback, { integer = false, min = null, max = null } = {}) {
    if (value === '' || value === null || value === undefined) return fallback;
    let n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    if (integer) n = Math.round(n);
    if (min !== null) n = Math.max(min, n);
    if (max !== null) n = Math.min(max, n);
    return n;
  }

  function normalizeRoute(route = {}) {
    const workspace = String(route.workspace_app || route.workspace || route.subtab || 'finish').toLowerCase();
    const backend = String(route.backend || 'comfyui').toLowerCase().replace(/^comfy$/, 'comfyui');
    const family = String(route.family || route.model_family || 'sdxl').toLowerCase().replace('stable-diffusion-xl', 'sdxl').replace('sd1.5', 'sd15').replace('qwen_image_edit', 'qwen_image');
    const loader = String(route.loader || route.loader_type || 'checkpoint').toLowerCase().replace('ckpt', 'checkpoint').replace('safetensors', 'checkpoint');
    const mode = String(route.workflow_mode || route.mode || 'generate').toLowerCase().replace('txt2img', 'generate');
    return { backend, family, loader, mode, workspace_app: workspace };
  }

  function stateForRoute(route = {}, nodeStatus = {}) {
    const r = normalizeRoute(route);
    if (r.workspace_app !== 'finish') {
      if (r.workspace_app === 'generations' || r.workspace_app === 'reference') return 'planned_gated';
      return 'unsupported';
    }
    if (!(r.backend === 'comfyui' || r.backend === 'comfyui_portable')) return 'provider_gated';
    if (r.mode === 'outpaint') return 'planned_gated';
    if (r.loader !== 'checkpoint') return 'unsupported';
    if (r.family === 'sdxl') return nodeStatus.ready === false ? 'provider_gated' : 'available';
    if (r.family === 'sd15') return nodeStatus.ready === false ? 'provider_gated' : 'experimental_available';
    if (r.family === 'wan_image' || r.family === 'hunyuan_image') return 'provider_gated';
    return 'unsupported';
  }

  function parseNodeStatus(availableNodes) {
    if (availableNodes === undefined || availableNodes === null) return { checked: false, ready: null, missing_required: [] };
    const names = new Set(Array.isArray(availableNodes) ? availableNodes : Object.keys(availableNodes || {}));
    const required = ['FaceDetailer', 'UltralyticsDetectorProvider'];
    const missing = required.filter((name) => !names.has(name));
    return { checked: true, ready: missing.length === 0, missing_required: missing };
  }

  function readParams(root) {
    const params = { ...DEFAULT_PARAMS };
    root.querySelectorAll('[data-adetailer-field]').forEach((field) => {
      const key = field.getAttribute('data-adetailer-field');
      if (!key) return;
      if (field.type === 'checkbox') params[key] = Boolean(field.checked);
      else if (['confidence', 'denoise'].includes(key)) params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { min: 0, max: 1 });
      else if (key === 'top_k') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 1, max: 50 });
      else if (key === 'bbox_grow') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: -128, max: 512 });
      else if (key === 'mask_blur') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 0, max: 128 });
      else if (key === 'steps') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 1, max: 150 });
      else if (key === 'cfg') params[key] = field.value === '' ? null : asNumber(field.value, DEFAULT_PARAMS[key], { min: 0, max: 15 });
      else params[key] = String(field.value || '').trim();
    });
    params.enabled = Boolean(params.enabled);
    return params;
  }

  function applyDisplayMode(root, mode) {
    const resolved = ['compact', 'guided', 'expert'].includes(mode) ? mode : 'guided';
    root.dataset.displayMode = resolved;
    const select = root.querySelector('[data-adetailer-display-mode]');
    if (select && select.value !== resolved) select.value = resolved;
  }

  function updateRouteUI(root, route = {}, options = {}) {
    const nodeStatus = options.nodeStatus || parseNodeStatus(options.availableNodes);
    const state = options.state || stateForRoute(route, nodeStatus);
    const diagnostic = Boolean(options.diagnostic || options.advanced || root.dataset.displayMode === 'expert');
    const reason = options.reason || (nodeStatus.ready === false && state === 'provider_gated'
      ? `Missing required nodes: ${nodeStatus.missing_required.join(', ')}`
      : ROUTE_REASONS[state] || ROUTE_REASONS.unchecked);

    root.dataset.routeState = state;
    root.dataset.routeReason = reason;
    const shouldHide = state === 'unsupported' && !diagnostic;
    root.hidden = shouldHide;

    const gate = root.querySelector('[data-adetailer-gate]');
    if (gate) gate.hidden = ACTIVE_STATES.has(state);
    const gateTitle = root.querySelector('[data-adetailer-gate-title]');
    if (gateTitle) gateTitle.textContent = state === 'unsupported' ? 'ADetailer unsupported for this route.' : 'ADetailer gated for this route.';
    const gateReason = root.querySelector('[data-adetailer-gate-reason]');
    if (gateReason) gateReason.textContent = reason;

    const statusChip = root.querySelector('[data-adetailer-status-chip]');
    if (statusChip) {
      statusChip.textContent = state.replaceAll('_', ' ');
      statusChip.classList.toggle('neo-adetailer-chip--active', ACTIVE_STATES.has(state));
      statusChip.classList.toggle('neo-adetailer-chip--gated', DIAGNOSTIC_STATES.has(state));
      statusChip.classList.toggle('neo-adetailer-chip--unsupported', state === 'unsupported');
    }
    const routeLabel = root.querySelector('[data-adetailer-route-label]');
    if (routeLabel) routeLabel.textContent = JSON.stringify(normalizeRoute(route));

    const disabled = !ACTIVE_STATES.has(state);
    root.querySelectorAll('[data-adetailer-field]').forEach((field) => { field.disabled = disabled && field.getAttribute('data-adetailer-field') !== 'enabled'; });
    return { state, reason, node_status: nodeStatus, route: normalizeRoute(route), visible: !shouldHide };
  }

  function mount(root, options = {}) {
    if (!root) return null;
    applyDisplayMode(root, options.displayMode || root.dataset.displayMode || 'guided');
    updateRouteUI(root, options.route || {}, options);
    const modeSelect = root.querySelector('[data-adetailer-display-mode]');
    if (modeSelect) modeSelect.addEventListener('change', () => {
      applyDisplayMode(root, modeSelect.value);
      updateRouteUI(root, options.route || {}, { ...options, diagnostic: modeSelect.value === 'expert' || options.diagnostic });
    });
    const enabled = root.querySelector('[data-adetailer-field="enabled"]');
    const enabledChip = root.querySelector('[data-adetailer-enabled-chip]');
    const syncEnabledChip = () => {
      if (enabledChip) {
        enabledChip.textContent = enabled && enabled.checked ? 'Enabled' : 'Disabled';
        enabledChip.classList.toggle('neo-adetailer-chip--active', Boolean(enabled && enabled.checked));
      }
    };
    if (enabled) enabled.addEventListener('change', syncEnabledChip);
    syncEnabledChip();
    return root;
  }

  function buildPayload(rootOrParams) {
    const params = rootOrParams && rootOrParams.querySelectorAll ? readParams(rootOrParams) : { ...DEFAULT_PARAMS, ...(rootOrParams || {}) };
    const enabled = Boolean(params.enabled);
    return {
      enabled,
      version: 1,
      inputs: {},
      params,
      assets: {},
      metadata: {
        phase: PHASE,
        ui_runtime_ready: true,
        mount_slot: MOUNT_SLOT,
        display_mode: rootOrParams && rootOrParams.dataset ? rootOrParams.dataset.displayMode : undefined,
        route_state: rootOrParams && rootOrParams.dataset ? rootOrParams.dataset.routeState : undefined,
        workflow_patch_ready: false,
      },
    };
  }

  window.NeoBuiltInExtensions = window.NeoBuiltInExtensions || {};
  window.NeoBuiltInExtensions[EXTENSION_ID] = {
    phase: PHASE,
    skeletonOnly: false,
    uiRuntimeReady: true,
    mountSlot: MOUNT_SLOT,
    defaultParams: DEFAULT_PARAMS,
    mount,
    buildPayload,
    normalizeRoute,
    stateForRoute,
    updateRouteUI,
  };

  if (typeof document !== 'undefined') {
    document.querySelectorAll(`[data-extension-id="${EXTENSION_ID}"]`).forEach((root) => mount(root));
  }
})();
