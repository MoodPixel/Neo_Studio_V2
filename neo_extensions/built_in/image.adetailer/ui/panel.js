(() => {
  const EXTENSION_ID = 'image.adetailer';
  const MOUNT_SLOT = 'image.finish.adetailer';
  const PHASE = 'P7';
  const ACTIVE_STATES = new Set(['available', 'experimental_available']);
  const DIAGNOSTIC_STATES = new Set(['planned_gated', 'provider_gated']);
  const QWEN_EDIT_FAMILIES = new Set(['qwen_image_edit_2509', 'qwen_image_edit_2511']);
  const MODERN_ROUTE_LOADERS = {
    qwen_image: new Set(['diffusion_model', 'gguf']),
    qwen_rapid_aio: new Set(['checkpoint_aio', 'gguf']),
    flux: new Set(['diffusion_model', 'gguf']),
    z_image: new Set(['diffusion_model', 'gguf']),
    z_image_turbo: new Set(['diffusion_model', 'gguf']),
    krea2: new Set(['diffusion_model', 'gguf']),
    krea2_turbo: new Set(['diffusion_model', 'gguf']),
    qwen_image_edit_2509: new Set(['diffusion_model', 'gguf']),
    qwen_image_edit_2511: new Set(['diffusion_model', 'gguf']),
  };
  const FAMILY_ALIASES = {
    'sd1.5': 'sd15', sd_1_5: 'sd15', sd_xl: 'sdxl',
    flux1: 'flux', flux_1: 'flux', 'flux.1': 'flux',
    qwen: 'qwen_image', qwen_rapid: 'qwen_rapid_aio', qwen_2509: 'qwen_image_edit_2509',
    qwen_image_edit: 'qwen_image_edit_2509', qwen_2511: 'qwen_image_edit_2511',
    zimage: 'z_image', zimage_turbo: 'z_image_turbo',
    krea_2: 'krea2', krea2_raw: 'krea2', krea_2_turbo: 'krea2_turbo',
  };
  const FAMILY_PRESETS = {
    sdxl: { id: 'sdxl_balanced_v1', name: 'SDXL Balanced', values: { steps: 16, cfg: 5.5, denoise: 0.25, sampler_name: 'dpmpp_2m_sde', scheduler: 'karras', guide_size: 768, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: [] },
    sd15: { id: 'sd15_balanced_v1', name: 'SD 1.5 Balanced', values: { steps: 16, cfg: 6.0, denoise: 0.28, sampler_name: 'dpmpp_2m', scheduler: 'karras', guide_size: 768, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: [] },
    qwen_image: { id: 'qwen_image_route_owned_v1', name: 'Qwen Image Route-Owned', values: { steps: 20, cfg: 1.0, denoise: 0.24, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    qwen_rapid_aio: { id: 'qwen_rapid_low_step_v1', name: 'Qwen Rapid Low-Step', values: { steps: 4, cfg: 1.0, denoise: 0.24, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: [] },
    flux: { id: 'flux_route_owned_v1', name: 'FLUX.1 Route-Owned', values: { steps: 20, cfg: 1.0, denoise: 0.24, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'sampler_name', 'scheduler'] },
    z_image: { id: 'z_image_route_owned_v1', name: 'Z-Image Base Route-Owned', values: { steps: 20, cfg: 1.0, denoise: 0.24, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    z_image_turbo: { id: 'z_image_turbo_low_step_v1', name: 'Z-Image Turbo Low-Step', values: { steps: 9, cfg: 1.0, denoise: 0.22, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: [] },
    qwen_image_edit_2509: { id: 'qwen_edit_2509_identity_safe_v1', name: 'Qwen Edit 2509 Identity-Safe', values: { steps: 20, cfg: 1.0, denoise: 0.22, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 20 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    krea2: { id: 'krea2_raw_route_owned_v1', name: 'Krea 2 RAW Route-Owned', values: { steps: 52, cfg: 3.5, denoise: 0.22, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    krea2_turbo: { id: 'krea2_turbo_route_owned_v1', name: 'Krea 2 Turbo Route-Owned', values: { steps: 8, cfg: 1.0, denoise: 0.20, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    qwen_image_edit_2511: { id: 'qwen_edit_2511_identity_safe_v1', name: 'Qwen Edit 2511 Identity-Safe', values: { steps: 20, cfg: 1.0, denoise: 0.20, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 20 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
    hidream: { id: 'hidream_route_owned_v1', name: 'HiDream Route-Owned', values: { steps: 20, cfg: 1.0, denoise: 0.24, sampler_name: 'euler', scheduler: 'simple', guide_size: 1024, max_size: 1024, noise_mask: true, force_inpaint: true, noise_mask_feather: 16 }, routeOwned: ['steps', 'cfg', 'sampler_name', 'scheduler'] },
  };
  const DEFAULT_PARAMS = {
    enabled: false,
    model_source: 'generation_model',
    family_preset_mode: 'auto_family',
    detailer_model_family: 'sdxl',
    detailer_checkpoint: '',
    detailer_vae: 'automatic',
    identity_protection: 'none',
    identity_lora_revision: 'route_family',
    lora_inheritance: 'inherit_all',
    inherit_lora_uids: '',
    detailer_lora_enabled: false,
    detailer_lora: '',
    detailer_lora_strength_model: 0.8,
    detailer_lora_strength_clip: 0.8,
    detailer_lora_trigger: '',
    detector_model: '',
    detector_type: 'bbox',
    confidence: 0.30,
    top_k: 1,
    bbox_grow: 16,
    mask_blur: 4,
    denoise: 0.35,
    steps: 20,
    cfg: null,
    sampler_name: '',
    scheduler: '',
    guide_size: 768,
    max_size: 1024,
    noise_mask: true,
    force_inpaint: true,
    noise_mask_feather: 16,
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
    experimental_available: 'ADetailer is available experimentally for this route. Review identity, precision, model, and detector warnings before queueing.',
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
    const familyToken = String(route.family || route.model_family || 'sdxl').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_');
    const family = FAMILY_ALIASES[familyToken] || familyToken;
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
    if (!['generate', 'img2img', 'inpaint'].includes(r.mode)) return 'unsupported';
    if (r.loader === 'checkpoint' && r.family === 'sdxl') return nodeStatus.ready === false ? 'provider_gated' : 'available';
    if (r.loader === 'checkpoint' && r.family === 'sd15') return nodeStatus.ready === false ? 'provider_gated' : 'experimental_available';
    if (MODERN_ROUTE_LOADERS[r.family]?.has(r.loader)) return nodeStatus.ready === false ? 'provider_gated' : 'experimental_available';
    if (r.family === 'wan_image' || r.family === 'hunyuan_image') return 'provider_gated';
    return 'unsupported';
  }

  function parseNodeStatus(availableNodes) {
    if (availableNodes === undefined || availableNodes === null) return { checked: false, ready: null, missing_required: [] };
    const source = availableNodes && typeof availableNodes === 'object' && availableNodes.object_info && typeof availableNodes.object_info === 'object' ? availableNodes.object_info : availableNodes;
    const names = new Set(Array.isArray(source) ? source : Object.keys(source || {}));
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
      else if (['detailer_lora_strength_model', 'detailer_lora_strength_clip'].includes(key)) params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { min: -4, max: 4 });
      else if (key === 'top_k') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 1, max: 50 });
      else if (key === 'bbox_grow') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: -128, max: 512 });
      else if (['mask_blur', 'noise_mask_feather'].includes(key)) params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 0, max: 128 });
      else if (key === 'guide_size') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 64, max: 4096 });
      else if (key === 'max_size') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 64, max: 8192 });
      else if (key === 'steps') params[key] = asNumber(field.value, DEFAULT_PARAMS[key], { integer: true, min: 1, max: 150 });
      else if (key === 'cfg') params[key] = field.value === '' ? null : asNumber(field.value, DEFAULT_PARAMS[key], { min: 0, max: 15 });
      else params[key] = String(field.value || '').trim();
    });
    params.enabled = Boolean(params.enabled);
    params.identity_protection = ['none', 'detailer_identity_lora', 'dedicated_detailer_model'].includes(params.identity_protection) ? params.identity_protection : 'none';
    params.identity_lora_revision = ['route_family', 'qwen_image_edit_2509', 'qwen_image_edit_2511', 'both'].includes(params.identity_lora_revision) ? params.identity_lora_revision : 'route_family';
    if (params.identity_protection === 'detailer_identity_lora') {
      params.model_source = 'generation_model';
      params.detailer_lora_enabled = true;
    } else if (params.identity_protection === 'dedicated_detailer_model') {
      params.model_source = 'dedicated_checkpoint';
      params.lora_inheritance = 'inherit_none';
      params.inherit_lora_uids = '';
    }
    return params;
  }

  function applyDisplayMode(root, mode) {
    const resolved = ['compact', 'guided', 'expert'].includes(mode) ? mode : 'guided';
    root.dataset.displayMode = resolved;
    const select = root.querySelector('[data-adetailer-display-mode]');
    if (select && select.value !== resolved) select.value = resolved;
  }

  function routeStatusDescriptor(state) {
    const map = {
      available: { id: 'available', label: 'Available', tone: 'success' },
      experimental_available: { id: 'experimental', label: 'Experimental', tone: 'warning' },
      planned_gated: { id: 'planned_gated', label: 'Planned gated', tone: 'warning' },
      provider_gated: { id: 'missing_nodes', label: 'Missing nodes', tone: 'danger' },
      unsupported: { id: 'unsupported', label: 'Unsupported', tone: 'danger' },
      unchecked: { id: 'unchecked', label: 'Route unchecked', tone: 'muted' },
    };
    return map[state] || map.unchecked;
  }

  function riskStatuses(root, route = {}, nodeStatus = {}) {
    const r = normalizeRoute(route);
    const state = root.dataset.routeState || stateForRoute(r, nodeStatus);
    const items = [routeStatusDescriptor(state)];
    const add = (id, label, tone, message = '') => { if (!items.some((item) => item.id === id)) items.push({ id, label, tone, message }); };
    const source = root.querySelector('[data-adetailer-field="model_source"]')?.value || 'generation_model';
    const identity = root.querySelector('[data-adetailer-field="identity_protection"]')?.value || 'none';
    const directEnabled = Boolean(root.querySelector('[data-adetailer-field="detailer_lora_enabled"]')?.checked);
    const directName = String(root.querySelector('[data-adetailer-field="detailer_lora"]')?.value || '').trim();
    if (source === 'dedicated_checkpoint') add('dedicated_model_only', 'Dedicated-model only', 'info', 'The isolated SDXL/SD1.5 detailer branch is selected.');
    if (r.family === 'z_image_turbo') add('precision_warning', 'Precision warning', 'warning', 'Z-Image Turbo detailing remains precision-sensitive and experimental.');
    if (QWEN_EDIT_FAMILIES.has(r.family)) {
      if (identity === 'none') add('identity_warning', 'Identity warning', 'warning', 'Native Qwen Edit FaceDetailer may change facial identity.');
      if (identity === 'detailer_identity_lora' && (!directEnabled || !directName)) add('missing_lora', 'Missing LoRA', 'danger', 'Identity-LoRA mode requires a selected detailer-only LoRA.');
      else if (identity === 'detailer_identity_lora') add('lora_assisted', 'LoRA-assisted', 'warning', 'Identity assistance is active, not guaranteed.');
    } else if (directEnabled && !directName) add('missing_lora', 'Missing LoRA', 'danger', 'Select a detailer-only LoRA before queueing.');
    if (nodeStatus.ready === false || state === 'provider_gated') add('missing_nodes', 'Missing nodes', 'danger', `Missing required nodes: ${(nodeStatus.missing_required || []).join(', ') || 'Impact Pack detector nodes'}.`);
    return items;
  }

  function renderStatusCenter(root, route = {}, nodeStatus = {}, reason = '') {
    const r = normalizeRoute(route);
    const items = riskStatuses(root, r, nodeStatus);
    const chips = root.querySelector('[data-adetailer-risk-chips]');
    if (chips) chips.innerHTML = items.map((item) => `<span class="neo-adetailer-status-chip ${item.tone}" data-adetailer-ux-status="${item.id}">${item.label}</span>`).join('');
    const routeSummary = root.querySelector('[data-adetailer-route-summary]');
    if (routeSummary) routeSummary.textContent = [r.backend, r.family, r.loader, r.mode].filter(Boolean).join(' · ');
    const message = root.querySelector('[data-adetailer-status-message]');
    const blocking = items.find((item) => item.tone === 'danger' && item.message);
    const warning = items.find((item) => item.tone === 'warning' && item.message);
    if (message) {
      message.textContent = blocking?.message || warning?.message || reason || 'Route is ready. Queue-time validation remains authoritative.';
      message.classList.toggle('neo-warn', Boolean(blocking));
      message.classList.toggle('neo-muted', !blocking);
    }
    return items;
  }

  function updateRouteUI(root, route = {}, options = {}) {
    const nodeStatus = options.nodeStatus || parseNodeStatus(options.availableNodes);
    const state = options.state || stateForRoute(route, nodeStatus);
    const reason = options.reason || (nodeStatus.ready === false && state === 'provider_gated'
      ? `Missing required nodes: ${nodeStatus.missing_required.join(', ')}`
      : ROUTE_REASONS[state] || ROUTE_REASONS.unchecked);

    root.dataset.routeState = state;
    root.dataset.routeFamily = normalizeRoute(route).family;
    root.dataset.routeReason = reason;
    root.dataset.routeJson = JSON.stringify(normalizeRoute(route));
    root.dataset.routeControlsEnabled = ACTIVE_STATES.has(state) ? 'true' : 'false';
    root.hidden = false;

    const gate = root.querySelector('[data-adetailer-gate]');
    if (gate) gate.hidden = ACTIVE_STATES.has(state);
    const gateTitle = root.querySelector('[data-adetailer-gate-title]');
    if (gateTitle) gateTitle.textContent = state === 'unsupported' ? 'ADetailer unsupported for this route.' : 'ADetailer gated for this route.';
    const gateReason = root.querySelector('[data-adetailer-gate-reason]');
    if (gateReason) gateReason.textContent = reason;

    const statusChip = root.querySelector('[data-adetailer-status-chip]');
    if (statusChip) {
      statusChip.textContent = routeStatusDescriptor(state).label;
      statusChip.classList.toggle('neo-adetailer-chip--active', ACTIVE_STATES.has(state));
      statusChip.classList.toggle('neo-adetailer-chip--gated', DIAGNOSTIC_STATES.has(state));
      statusChip.classList.toggle('neo-adetailer-chip--unsupported', state === 'unsupported');
    }
    const routeLabel = root.querySelector('[data-adetailer-route-label]');
    if (routeLabel) routeLabel.textContent = JSON.stringify(normalizeRoute(route));

    const disabled = !ACTIVE_STATES.has(state);
    root.querySelectorAll('[data-adetailer-field]').forEach((field) => { field.disabled = disabled; });
    if (!disabled) { syncFamilyPresetFields(root); syncIdentityFields(root); syncModelSourceFields(root); }
    renderStatusCenter(root, route, nodeStatus, reason);
    return { state, reason, node_status: nodeStatus, route: normalizeRoute(route), visible: true, statuses: riskStatuses(root, route, nodeStatus) };
  }

  function activePresetFamily(root) {
    const source = root.querySelector('[data-adetailer-field="model_source"]')?.value || 'generation_model';
    if (source === 'dedicated_checkpoint') return root.querySelector('[data-adetailer-field="detailer_model_family"]')?.value || 'sdxl';
    return root.dataset.routeFamily || 'sdxl';
  }

  function syncFamilyPresetFields(root) {
    const modeField = root.querySelector('[data-adetailer-field="family_preset_mode"]');
    const mode = modeField?.value || 'auto_family';
    const family = activePresetFamily(root);
    const profile = FAMILY_PRESETS[family];
    const auto = mode === 'auto_family';
    const summary = root.querySelector('[data-adetailer-family-preset-summary]');
    if (summary) {
      if (!auto) summary.textContent = mode === 'legacy_manual' ? 'Legacy manual compatibility values are preserved.' : 'Manual sampling controls are active.';
      else if (!profile) summary.textContent = `No preset is registered for ${family}; backend validation will fail closed without an SDXL fallback.`;
      else {
        const routeBits = profile.routeOwned.length ? ` Route-owned: ${profile.routeOwned.join(', ')}.` : '';
        summary.textContent = `${profile.name} · ${profile.id}.${routeBits}`;
      }
    }
    const routeDisabled = root.dataset.routeControlsEnabled !== 'true';
    root.querySelectorAll('[data-adetailer-family-sampling-field]').forEach((field) => { field.disabled = routeDisabled || auto; });
    const presetChip = root.querySelector('[data-adetailer-preset-chip]');
    if (presetChip) presetChip.textContent = auto ? (profile?.name || 'Missing preset') : mode.replaceAll('_', ' ');
    if (auto && profile) {
      Object.entries(profile.values).forEach(([key, value]) => {
        const field = root.querySelector(`[data-adetailer-field="${key}"]`);
        if (!field || profile.routeOwned.includes(key)) return;
        if (field.type === 'checkbox') field.checked = Boolean(value);
        else field.value = String(value);
      });
    }
  }


  function syncIdentityFields(root, { applyPolicy = false } = {}) {
    const family = root.dataset.routeFamily || '';
    const applicable = QWEN_EDIT_FAMILIES.has(family);
    const modeField = root.querySelector('[data-adetailer-field="identity_protection"]');
    const revisionField = root.querySelector('[data-adetailer-field="identity_lora_revision"]');
    root.querySelectorAll('[data-adetailer-identity-field]').forEach((node) => { node.hidden = !applicable; });
    const notApplicable = root.querySelector('[data-adetailer-identity-not-applicable]');
    const identityChip = root.querySelector('[data-adetailer-identity-chip]');
    if (!applicable) {
      root.querySelectorAll('[data-adetailer-identity-revision-field]').forEach((node) => { node.hidden = true; });
      if (notApplicable) notApplicable.hidden = false;
      if (identityChip) identityChip.textContent = 'Not applicable';
      return;
    }
    if (notApplicable) notApplicable.hidden = true;
    const mode = modeField?.value || 'none';
    if (identityChip) identityChip.textContent = mode.replaceAll('_', ' ');
    root.querySelectorAll('[data-adetailer-identity-revision-field]').forEach((node) => { node.hidden = mode !== 'detailer_identity_lora'; });
    const summary = root.querySelector('[data-adetailer-identity-summary]');
    if (summary) {
      summary.textContent = mode === 'detailer_identity_lora'
        ? 'LoRA-assisted experimental: model-only LoRA branch plus compiler-owned Qwen sampling reapplication. No identity guarantee.'
        : mode === 'dedicated_detailer_model'
          ? 'Dedicated SDXL/SD1.5 repair fallback. Visual identity still requires validation.'
          : family === 'qwen_image_edit_2509'
            ? 'Qwen Edit 2509 native FaceDetailer can change facial identity.'
            : 'Qwen Edit 2511 improves consistency, but FaceDetailer identity preservation is not guaranteed.';
    }
    if (!applyPolicy) return;
    const source = root.querySelector('[data-adetailer-field="model_source"]');
    const inheritance = root.querySelector('[data-adetailer-field="lora_inheritance"]');
    const direct = root.querySelector('[data-adetailer-field="detailer_lora_enabled"]');
    if (mode === 'detailer_identity_lora') {
      if (source) source.value = 'generation_model';
      if (direct) direct.checked = true;
    } else if (mode === 'dedicated_detailer_model') {
      if (source) source.value = 'dedicated_checkpoint';
      if (inheritance) inheritance.value = 'inherit_none';
    }
    if (revisionField && !revisionField.value) revisionField.value = 'route_family';
  }

  function syncModelSourceFields(root) {
    const source = root.querySelector('[data-adetailer-field="model_source"]')?.value || 'generation_model';
    const routeDisabled = root.dataset.routeControlsEnabled !== 'true';
    const inheritance = root.querySelector('[data-adetailer-field="lora_inheritance"]');
    root.querySelectorAll('[data-adetailer-dedicated-field]').forEach((node) => { node.hidden = source !== 'dedicated_checkpoint'; });
    if (inheritance) {
      if (source === 'dedicated_checkpoint') inheritance.value = 'inherit_none';
      inheritance.disabled = routeDisabled || source === 'dedicated_checkpoint';
    }
    const selected = inheritance?.value === 'inherit_selected' && source !== 'dedicated_checkpoint';
    root.querySelectorAll('[data-adetailer-selected-lora-field]').forEach((node) => { node.hidden = !selected; });
    const direct = Boolean(root.querySelector('[data-adetailer-field="detailer_lora_enabled"]')?.checked);
    root.querySelectorAll('[data-adetailer-direct-lora-field]').forEach((node) => { node.hidden = !direct; });
    const modelChip = root.querySelector('[data-adetailer-model-source-chip]');
    if (modelChip) modelChip.textContent = source === 'dedicated_checkpoint' ? 'Dedicated-model only' : 'Generation model';
    const loraChip = root.querySelector('[data-adetailer-lora-chip]');
    if (loraChip) loraChip.textContent = direct ? 'Detailer LoRA' : (inheritance?.value || 'inherit_all').replaceAll('_', ' ');
    syncFamilyPresetFields(root);
    syncIdentityFields(root);
    let route = { family: root.dataset.routeFamily || '' };
    try { route = JSON.parse(root.dataset.routeJson || '{}'); } catch (_) {}
    renderStatusCenter(root, route, { ready: root.dataset.routeState !== 'provider_gated', missing_required: [] }, root.dataset.routeReason || '');
  }

  function mount(root, options = {}) {
    if (!root) return null;
    root.querySelectorAll('[data-adetailer-section]').forEach((section) => {
      const key = `neo.image.adetailer.section.${section.getAttribute('data-adetailer-section') || 'unknown'}`;
      try { const saved = sessionStorage.getItem(key); if (saved !== null) section.open = saved === 'true'; } catch (_) {}
      section.addEventListener('toggle', () => { try { sessionStorage.setItem(key, section.open ? 'true' : 'false'); } catch (_) {} });
    });
    applyDisplayMode(root, options.displayMode || root.dataset.displayMode || 'guided');
    updateRouteUI(root, options.route || {}, options);
    syncModelSourceFields(root);
    const sourceSelect = root.querySelector('[data-adetailer-field="model_source"]');
    if (sourceSelect) sourceSelect.addEventListener('change', () => {
      const mode = root.querySelector('[data-adetailer-field="identity_protection"]');
      if (mode?.value === 'detailer_identity_lora' && sourceSelect.value === 'dedicated_checkpoint') mode.value = 'dedicated_detailer_model';
      else if (mode?.value === 'dedicated_detailer_model' && sourceSelect.value === 'generation_model') mode.value = 'none';
      syncModelSourceFields(root);
    });
    const dedicatedFamily = root.querySelector('[data-adetailer-field="detailer_model_family"]');
    if (dedicatedFamily) dedicatedFamily.addEventListener('change', () => syncFamilyPresetFields(root));
    const presetMode = root.querySelector('[data-adetailer-field="family_preset_mode"]');
    if (presetMode) presetMode.addEventListener('change', () => syncFamilyPresetFields(root));
    const identitySelect = root.querySelector('[data-adetailer-field="identity_protection"]');
    if (identitySelect) identitySelect.addEventListener('change', () => { syncIdentityFields(root, { applyPolicy: true }); syncModelSourceFields(root); });
    const inheritanceSelect = root.querySelector('[data-adetailer-field="lora_inheritance"]');
    if (inheritanceSelect) inheritanceSelect.addEventListener('change', () => syncModelSourceFields(root));
    const directLoraToggle = root.querySelector('[data-adetailer-field="detailer_lora_enabled"]');
    if (directLoraToggle) directLoraToggle.addEventListener('change', () => syncModelSourceFields(root));
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
        workflow_patch_ready: true,
        ui_contract: 'neo.image.adetailer.ui_ux.v1',
        selection_preservation: 'family_loader_backend_safe',
        risk_states: rootOrParams && rootOrParams.querySelectorAll ? riskStatuses(rootOrParams, { family: rootOrParams.dataset.routeFamily || '', backend: 'comfyui' }, { ready: rootOrParams.dataset.routeState !== 'provider_gated', missing_required: [] }).map((item) => item.id) : [],
      },
    };
  }

  window.NeoBuiltInExtensions = window.NeoBuiltInExtensions || {};
  window.NeoBuiltInExtensions[EXTENSION_ID] = {
    phase: PHASE,
    skeletonOnly: false,
    uiRuntimeReady: true,
    mountSlot: MOUNT_SLOT,
    uiContract: 'neo.image.adetailer.ui_ux.v1',
    selectionPreservation: 'family_loader_backend_safe',
    defaultParams: DEFAULT_PARAMS,
    familyPresets: FAMILY_PRESETS,
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
