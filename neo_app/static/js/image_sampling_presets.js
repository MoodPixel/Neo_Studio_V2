(function (root) {
  'use strict';

  const SCHEMA = 'neo.image.sampling_preset_ui.v1';
  const PHASE = 'IP-8';
  const HOTFIX_PHASE = 'IP-8.1';
  const IR1_PHASE = 'IR-1';
  const IR2_PHASE = 'IR-2';
  const VISIBLE_UI_OWNER = 'neo.js#workspaceUiPreset';
  const BASE_ENDPOINT = '/api/image/base';
  const USER_ENDPOINT = '/api/ui-presets/image_sampling';
  const PROVIDER_DEFAULTS_ID = 'provider_defaults';
  const EMPTY_CLEAN_SLATE_ID = 'empty_clean_slate';

  const FAMILY_ALIASES = Object.freeze({
    'sd1.5': 'sd15', sd_1_5: 'sd15', stable_diffusion_1_5: 'sd15', sd_xl: 'sdxl',
    flux1: 'flux', flux_1: 'flux', 'flux.1': 'flux', klein: 'flux2_klein',
    flux2: 'flux2_klein', flux_2_klein: 'flux2_klein', krea2_raw: 'krea2',
    krea2_base: 'krea2', zimage: 'z_image', zimage_turbo: 'z_image_turbo',
    qwen: 'qwen_image', qwen_2509: 'qwen_image_edit_2509',
    qwen_2511: 'qwen_image_edit_2511',
  });

  const FIELD_SELECTORS = Object.freeze({
    family: ['#imageWorkspaceFamily', '#imageFamily', '#imageModelFamily', '#modelFamily', '#model_family', '[name="family"]', '[name="model_family"]', '[data-image-field="family"]'],
    loader: ['#imageWorkspaceLoader', '#imageLoader', '#imageLoaderType', '#modelLoader', '#loaderType', '[name="loader"]', '[name="loader_type"]', '[data-image-field="loader"]'],
    mode: ['#imageWorkflowMode', '#imageMode', '#workflowMode', '[name="mode"]', '[name="workflow_mode"]', '[data-image-field="mode"]'],
    variant: ['#imageModelVariant', '#fluxVariant', '#modelVariant', '[name="variant"]', '[name="flux_variant"]', '[name="krea2_variant"]', '[data-image-field="variant"]'],
    model: ['#imageModel', '#modelName', '#checkpoint', '[name="model"]', '[name="model_name"]', '[name="diffusion_model"]', '[name="gguf_model"]', '[name="gguf_unet"]', '[data-image-field="model"]'],
    sampler: ['#imageSampler', '#sampler', '[name="sampler"]', '[data-param="sampler"]'],
    scheduler: ['#imageScheduler', '#scheduler', '[name="scheduler"]', '[data-param="scheduler"]'],
    width: ['#imageWidth', '#width', '[name="width"]', '[data-param="width"]'],
    height: ['#imageHeight', '#height', '[name="height"]', '[data-param="height"]'],
    steps: ['#imageSteps', '#steps', '[name="steps"]', '[data-param="steps"]'],
    cfg: ['#imageCfg', '#cfg', '#cfgScale', '[name="cfg"]', '[name="cfg_scale"]', '[data-param="cfg"]'],
    true_cfg: ['#imageCfg[data-neo-guidance-semantic="true_cfg"]', '#imageParam_true_cfg', '#trueCfg', '#true_cfg', '[name="true_cfg"]', '[data-param="true_cfg"]'],
    flux_guidance: ['#fluxGuidance', '#flux_guidance', '[name="flux_guidance"]', '[data-param="flux_guidance"]'],
    guidance: ['#guidance', '#guidanceScale', '[name="guidance"]', '[name="guidance_scale"]', '[data-param="guidance"]'],
    guidance_scale: ['#guidanceScale', '[name="guidance_scale"]', '[data-param="guidance_scale"]'],
    model_guidance: ['#modelGuidance', '#model_guidance', '[name="model_guidance"]', '[data-param="model_guidance"]'],
    denoise: ['#imageDenoise', '#denoise', '#strength', '[name="denoise"]', '[name="strength"]', '[data-param="denoise"]'],
    seed: ['#imageSeed', '#seed', '[name="seed"]', '[data-param="seed"]'],
    requested_seed: ['[name="requested_seed"]', '[data-param="requested_seed"]'],
    actual_seed: ['[name="actual_seed"]', '[data-param="actual_seed"]'],
  });

  const ROUTE_FIELDS = new Set(['family', 'loader', 'mode', 'variant', 'model']);

  const state = {
    mounted: false,
    panel: null,
    scope: null,
    base: null,
    userPresets: [],
    userCache: new Map(),
    selectedPresetId: '',
    outputIntent: 'none',
    applying: false,
    dirty: false,
    lastContextKey: '',
    refreshSerial: 0,
    observer: null,
  };

  function token(value) {
    return String(value ?? '').trim().toLowerCase().replace(/[- ]/g, '_');
  }

  function normalizeFamily(value) {
    const raw = token(value);
    return FAMILY_ALIASES[raw] || raw;
  }

  function normalizeLoader(value) {
    const raw = token(value);
    const aliases = { ckpt: 'checkpoint', checkpointaio: 'checkpoint_aio', components: 'diffusion_model', component: 'diffusion_model', safetensors: 'diffusion_model' };
    return aliases[raw] || raw;
  }

  function normalizeMode(value) {
    const raw = token(value || 'txt2img');
    if (['generate', 'text2img', 'text_to_image'].includes(raw)) return 'txt2img';
    if (raw === 'image_to_image') return 'img2img';
    return raw || 'txt2img';
  }

  function normalizeIntent(value) {
    const raw = token(value || 'none');
    if (['photo', 'photorealistic', 'photoreal', 'realism'].includes(raw)) return 'realistic';
    if (['anime', 'illustration', 'anime_illustration'].includes(raw)) return 'anime_illustration';
    if (['neutral', 'off', 'disabled'].includes(raw)) return 'none';
    return raw || 'none';
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>\"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;' }[char]));
  }

  function findFirst(scope, selectors) {
    if (!scope || typeof scope.querySelector !== 'function') return null;
    for (const selector of selectors || []) {
      const found = scope.querySelector(selector);
      if (found) return found;
    }
    return null;
  }

  function valueOf(element) {
    if (!element) return '';
    return element.value != null ? element.value : (element.getAttribute?.('value') || '');
  }

  function imageScopeFrom(node) {
    return node?.closest?.('[data-image-workspace], [data-surface="image"], [data-surface-id="image"], [data-slot^="image."], #imagePanel, .neo-image-panel, .neo-image-workspace') || state.scope || null;
  }

  function fieldFor(name, scope) {
    const searchRoot = scope || state.scope || root.document;
    const external = findFirst(searchRoot, FIELD_SELECTORS[name]);
    if (external && !external.closest?.('[data-neo-image-sampling-presets]')) return external;
    const global = findFirst(root.document, FIELD_SELECTORS[name]);
    if (global && !global.closest?.('[data-neo-image-sampling-presets]')) return global;
    return state.panel?.querySelector?.(`[data-neo-shadow-field="${name}"]`) || null;
  }

  function activeSubtabMode(scope) {
    const explicit = valueOf(fieldFor('mode', scope));
    if (explicit) return normalizeMode(explicit);
    const active = scope?.querySelector?.('[data-subtab].active, [data-subtab][aria-selected="true"], [data-image-subtab].active, [data-image-subtab][aria-selected="true"]');
    return normalizeMode(active?.dataset?.subtab || active?.dataset?.imageSubtab || active?.getAttribute?.('data-mode') || 'txt2img');
  }

  function contextFromDom(scope) {
    const searchRoot = scope || state.scope || root.document;
    return {
      family: normalizeFamily(valueOf(fieldFor('family', searchRoot))),
      variant: token(valueOf(fieldFor('variant', searchRoot))) || '*',
      loader: normalizeLoader(valueOf(fieldFor('loader', searchRoot))),
      mode: activeSubtabMode(searchRoot),
      model_name: String(valueOf(fieldFor('model', searchRoot)) || '').trim(),
      intent: normalizeIntent(state.outputIntent || state.panel?.querySelector?.('[data-neo-output-intent]')?.value || 'none'),
    };
  }

  function contextKey(context) {
    const ctx = context || contextFromDom();
    return [ctx.family, ctx.variant, ctx.loader, ctx.mode, ctx.model_name].join('|');
  }

  function selectorValues(value, dimension) {
    if (value == null || value === '' || value === '*') return new Set(['*']);
    const values = Array.isArray(value) ? value : [value];
    const out = new Set();
    values.forEach((raw) => {
      if (String(raw ?? '').trim() === '*') out.add('*');
      else if (dimension === 'family') out.add(normalizeFamily(raw));
      else if (dimension === 'loader') out.add(normalizeLoader(raw));
      else if (dimension === 'mode') out.add(normalizeMode(raw));
      else if (dimension === 'intent') out.add(normalizeIntent(raw));
      else out.add(token(raw));
    });
    if (!out.size) out.add('*');
    return out;
  }

  function entryScore(entry, context) {
    const match = entry?.match && typeof entry.match === 'object' ? entry.match : {};
    const weights = { family: 32, variant: 16, loader: 8, mode: 4, intent: 2 };
    let score = Number(entry?.priority || 0) * 1000;
    for (const [dimension, weight] of Object.entries(weights)) {
      const allowed = selectorValues(match[dimension] ?? '*', dimension);
      const value = context[dimension] || '';
      if (allowed.has('*')) continue;
      if (!allowed.has(value)) return null;
      score += weight;
    }
    return score;
  }

  function setBaseContract(base) {
    // IR-2: IR-1 made this module headless, so the live Image renderer supplies
    // the already-loaded /api/image/base contract instead of mounting/fetching a
    // second UI just to initialize the resolver.
    state.base = base && typeof base === 'object' ? base : null;
    return state.base;
  }

  function rawBuiltIns() {
    return Array.isArray(state.base?.sampling_presets?.built_in_presets) ? state.base.sampling_presets.built_in_presets : [];
  }

  function selectRawBuiltIn(presetId, context) {
    const id = token(presetId);
    const candidates = rawBuiltIns().map((entry) => [entryScore(entry, context), entry]).filter(([score, entry]) => score != null && token(entry.preset_id) === id);
    if (!candidates.length) return null;
    candidates.sort((a, b) => b[0] - a[0] || String(b[1]?.entry_id || '').localeCompare(String(a[1]?.entry_id || '')));
    const top = candidates[0][0];
    if (candidates.filter(([score]) => score === top).length > 1) throw new Error(`Ambiguous built-in preset: ${id}`);
    return candidates[0][1];
  }

  function inheritedContext(context, inherit) {
    const parent = Object.assign({}, context);
    ['family', 'variant', 'loader', 'mode', 'intent'].forEach((dimension) => {
      if (!Object.prototype.hasOwnProperty.call(inherit || {}, dimension)) return;
      const raw = inherit[dimension];
      if (dimension === 'family') parent[dimension] = normalizeFamily(raw);
      else if (dimension === 'loader') parent[dimension] = normalizeLoader(raw);
      else if (dimension === 'mode') parent[dimension] = normalizeMode(raw);
      else if (dimension === 'intent') parent[dimension] = normalizeIntent(raw);
      else parent[dimension] = token(raw);
    });
    return parent;
  }

  function meaningfulInheritance(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const inherit = Object.fromEntries(Object.entries(raw).filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== ''));
    return Object.keys(inherit).length ? inherit : null;
  }

  function materializeBuiltIn(entry, context, trail) {
    const chain = Array.isArray(trail) ? trail : [];
    const entryId = String(entry?.entry_id || '');
    if (chain.includes(entryId)) throw new Error(`Sampling preset inheritance cycle: ${[...chain, entryId].join(' → ')}`);
    const local = Object.assign({}, entry?.values || {});
    // IR-6 recovery: FastAPI/Pydantic may serialize an omitted inheritance block
    // as an empty object (or an object containing only null fields). That is a
    // direct preset, not a request to inherit from itself.
    const inherit = meaningfulInheritance(entry?.inherit);
    const drops = Array.isArray(entry?.drop_fields) ? entry.drop_fields.map(String) : [];
    if (!inherit) return Object.assign({}, entry, { local_values: local, values: local, inheritance: { inherited: false, chain: entryId ? [entryId] : [], drop_fields: drops } });
    const parentContext = inheritedContext(context, inherit);
    const parentId = token(inherit.preset_id || entry.preset_id);
    const parentRaw = selectRawBuiltIn(parentId, parentContext);
    if (!parentRaw) throw new Error(`Preset ${entryId} has an unavailable parent ${parentId}.`);
    const parent = materializeBuiltIn(parentRaw, parentContext, [...chain, entryId]);
    const values = Object.assign({}, parent.values || {});
    drops.forEach((field) => delete values[field]);
    Object.assign(values, local);
    return Object.assign({}, entry, {
      local_values: local,
      values,
      inheritance: { inherited: true, parent_preset_id: parentId, parent_entry_id: parent.entry_id, parent_context: parentContext, chain: [...(parent.inheritance?.chain || []), entryId], drop_fields: drops },
    });
  }

  function resolveBuiltInPreset(presetId, context) {
    const ctx = context || contextFromDom();
    const raw = selectRawBuiltIn(presetId, ctx);
    if (!raw) return null;
    return materializeBuiltIn(raw, ctx, []);
  }

  function availableBuiltIns(context) {
    const ids = [...new Set(rawBuiltIns().map((entry) => token(entry.preset_id)).filter(Boolean))];
    return ids.map((id) => resolveBuiltInPreset(id, context)).filter(Boolean).sort((a, b) => {
      const order = { provider_defaults: 0, empty_clean_slate: 1, default_balanced: 2 };
      return (order[token(a.preset_id)] ?? 50) - (order[token(b.preset_id)] ?? 50) || String(a.label || '').localeCompare(String(b.label || ''));
    });
  }

  function userMatchesContext(summary, context) {
    const saved = summary?.context || {};
    if (normalizeFamily(saved.family) !== context.family) return false;
    if (normalizeLoader(saved.loader) !== context.loader) return false;
    if (normalizeMode(saved.mode) !== context.mode) return false;
    const variant = token(saved.variant) || '*';
    return variant === '*' || variant === context.variant;
  }

  function intentCatalog() {
    return Array.isArray(state.base?.output_intents?.intents) ? state.base.output_intents.intents : [];
  }

  function managedFields() {
    return Array.isArray(state.base?.sampling_presets?.managed_fields) ? state.base.sampling_presets.managed_fields : [];
  }

  function ensureShadowField(name) {
    if (!state.panel || !root.document) return null;
    let field = state.panel.querySelector(`[data-neo-shadow-field="${name}"]`);
    if (!field) {
      field = root.document.createElement('input');
      field.type = 'hidden';
      field.name = name;
      field.setAttribute('data-neo-shadow-field', name);
      state.panel.appendChild(field);
    }
    return field;
  }

  function emitFieldEvents(field) {
    if (!field || typeof field.dispatchEvent !== 'function') return;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function setFieldValue(name, value) {
    let field = fieldFor(name, state.scope);
    if (!field) field = ensureShadowField(name);
    if (!field) return;
    const targetValue = value == null ? '' : String(value);
    if (field.tagName === 'SELECT' && targetValue && ![...field.options].some((option) => option.value === targetValue)) {
      const option = root.document.createElement('option');
      option.value = targetValue;
      option.textContent = `${targetValue} · preset`;
      option.setAttribute('data-neo-preset-option', 'true');
      field.appendChild(option);
    }
    field.value = targetValue;
    field.removeAttribute?.('data-neo-preset-unset');
    emitFieldEvents(field);
  }

  function clearField(name) {
    let field = fieldFor(name, state.scope);
    if (!field) field = ensureShadowField(name);
    if (!field) return;
    if (field.tagName === 'SELECT') {
      const providerOption = [...field.options].find((option) => option.value === 'provider_default');
      if (providerOption && state.selectedPresetId === PROVIDER_DEFAULTS_ID) field.value = 'provider_default';
      else {
        field.value = '';
        if (field.value !== '') field.selectedIndex = -1;
      }
    } else field.value = '';
    field.setAttribute?.('data-neo-preset-unset', 'true');
    emitFieldEvents(field);
  }

  function clearManagedFields() {
    // Qwen uses one visible input for both compatibility cfg and semantic
    // true_cfg. Clear each physical DOM field once so aliasing cannot trigger
    // duplicate destructive input/change cycles.
    const seen = new Set();
    managedFields().forEach((name) => {
      const field = fieldFor(name, state.scope) || ensureShadowField(name);
      if (!field || seen.has(field)) return;
      seen.add(field);
      clearField(name);
    });
  }

  function readManagedValues() {
    const out = {};
    managedFields().forEach((name) => {
      const field = fieldFor(name, state.scope);
      if (!field) return;
      const raw = valueOf(field);
      if (raw === '' || raw == null || field.getAttribute?.('data-neo-preset-unset') === 'true') return;
      if (['sampler', 'scheduler'].includes(name)) out[name] = String(raw);
      else {
        const numeric = Number(raw);
        out[name] = Number.isFinite(numeric) ? numeric : raw;
      }
    });
    return out;
  }

  function selectedOptionMeta() {
    const select = state.panel?.querySelector?.('[data-neo-sampling-preset-select]');
    const option = select?.selectedOptions?.[0];
    return { source: option?.dataset?.source || '', label: option?.textContent || select?.value || '', id: select?.value || '' };
  }

  function syncHiddenState() {
    if (!state.panel) return;
    const presetHidden = state.panel.querySelector('[data-neo-sampling-preset-hidden]');
    const intentHidden = state.panel.querySelector('[data-neo-output-intent-hidden]');
    if (presetHidden) presetHidden.value = state.selectedPresetId || PROVIDER_DEFAULTS_ID;
    if (intentHidden) intentHidden.value = state.outputIntent || 'none';
  }

  function setStatus(message, tone) {
    const node = state.panel?.querySelector?.('[data-neo-preset-status]');
    if (!node) return;
    node.textContent = String(message || '');
    node.dataset.tone = tone || 'muted';
  }

  function renderInspector() {
    const node = state.panel?.querySelector?.('[data-neo-preset-inspector]');
    if (!node) return;
    const context = contextFromDom(state.scope);
    const meta = selectedOptionMeta();
    const values = readManagedValues();
    const builtIn = meta.source === 'built_in' ? resolveBuiltInPreset(meta.id, context) : null;
    const inheritance = builtIn?.inheritance || {};
    const rows = [
      ['Route', `${context.family || '—'} · ${context.variant || 'default'} · ${context.loader || '—'} · ${context.mode || '—'}`],
      ['Preset', `${meta.label || meta.id || '—'} · ${meta.source || 'unknown'}`],
      ['Intent', `${state.outputIntent || 'none'} · metadata only`],
      ['Inheritance', inheritance.inherited ? (inheritance.chain || []).join(' → ') : 'direct / user-authored'],
      ['Resolution', builtIn?.resolution_policy || (context.mode === 'txt2img' ? 'family canvas' : 'source / expanded canvas')],
      ['Sampling', Object.keys(values).length ? Object.entries(values).map(([k,v]) => `${k}=${v}`).join(' · ') : 'provider/manual unresolved'],
    ];
    node.innerHTML = `<div class="neo-image-sampling-inspector__title"><strong>Preset Inspector</strong><span>Authoring preflight</span></div>${rows.map(([label,value]) => `<div class="neo-image-sampling-inspector__row"><span>${escapeHtml(label)}</span><code>${escapeHtml(value)}</code></div>`).join('')}<small>Runtime Inspector + IP-8 release-lock state are attached to Image job metadata after submission. No GPU quality proof is implied.</small>`;
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-sampling-preset-inspected', { detail: { schema: 'neo.image.sampling_preset_inspector.ui.v1', phase: PHASE, context, preset_id: meta.id, output_intent: state.outputIntent } }));
  }

  function updateButtons() {
    if (!state.panel) return;
    const meta = selectedOptionMeta();
    const isUser = meta.source === 'user';
    ['rename', 'delete'].forEach((action) => {
      const button = state.panel.querySelector(`[data-neo-preset-action="${action}"]`);
      if (button) button.disabled = !isUser;
    });
    const duplicate = state.panel.querySelector('[data-neo-preset-action="duplicate"]');
    if (duplicate) duplicate.disabled = !meta.id;
    const reset = state.panel.querySelector('[data-neo-preset-action="reset"]');
    if (reset) reset.disabled = !meta.id;
    const badge = state.panel.querySelector('[data-neo-preset-badge]');
    if (badge) badge.textContent = isUser ? 'My Preset' : 'Built-in · Read-only';
  }

  function renderOptions() {
    if (!state.panel || !state.base) return;
    const select = state.panel.querySelector('[data-neo-sampling-preset-select]');
    if (!select) return;
    const context = contextFromDom(state.scope);
    const builtIns = availableBuiltIns(context);
    const users = state.userPresets.filter((item) => userMatchesContext(item, context));
    const previous = state.selectedPresetId || PROVIDER_DEFAULTS_ID;
    select.innerHTML = '';

    function appendBuiltInGroup(label, entries) {
      if (!entries.length) return;
      const group = root.document.createElement('optgroup');
      group.label = label;
      entries.forEach((entry) => {
        const option = root.document.createElement('option');
        option.value = token(entry.preset_id);
        option.textContent = entry.label || entry.preset_id;
        option.dataset.source = 'built_in';
        option.dataset.category = entry.category || 'defaults';
        option.dataset.entryId = entry.entry_id || '';
        option.title = entry.description || '';
        group.appendChild(option);
      });
      select.appendChild(group);
    }

    appendBuiltInGroup('Defaults', builtIns.filter((entry) => token(entry.category) !== 'templates'));
    appendBuiltInGroup('Templates', builtIns.filter((entry) => token(entry.category) === 'templates'));

    const mine = root.document.createElement('optgroup');
    mine.label = 'My Presets';
    users.forEach((item) => {
      const option = root.document.createElement('option');
      option.value = item.preset_id;
      option.textContent = item.name || item.preset_id;
      option.dataset.source = 'user';
      option.title = item.description || '';
      mine.appendChild(option);
    });
    if (users.length) select.appendChild(mine);

    const optionIds = [...select.options].map((option) => option.value);
    if (optionIds.includes(previous)) state.selectedPresetId = previous;
    else state.selectedPresetId = optionIds.includes(PROVIDER_DEFAULTS_ID) ? PROVIDER_DEFAULTS_ID : (optionIds[0] || '');
    select.value = state.selectedPresetId;
    syncHiddenState();
    updateButtons();
    renderInspector();
  }

  function renderIntentOptions() {
    const select = state.panel?.querySelector?.('[data-neo-output-intent]');
    if (!select) return;
    select.innerHTML = '';
    intentCatalog().forEach((intent) => {
      const option = root.document.createElement('option');
      option.value = normalizeIntent(intent.intent_id);
      option.textContent = intent.label || intent.intent_id;
      option.title = intent.description || '';
      select.appendChild(option);
    });
    const ids = [...select.options].map((option) => option.value);
    if (!ids.includes(state.outputIntent)) state.outputIntent = ids.includes('none') ? 'none' : (ids[0] || 'none');
    select.value = state.outputIntent;
    syncHiddenState();
  }

  async function apiJson(url, options) {
    const response = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
    let payload = null;
    try { payload = await response.json(); } catch (_error) { payload = null; }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || `${response.status} ${response.statusText}`;
      throw new Error(String(message));
    }
    return payload || {};
  }

  async function loadContracts() {
    const [base, users] = await Promise.all([
      apiJson(BASE_ENDPOINT),
      apiJson(USER_ENDPOINT).catch((error) => ({ presets: [], warning: error.message })),
    ]);
    state.base = base;
    state.userPresets = Array.isArray(users?.presets) ? users.presets : [];
    if (users?.warning) setStatus(`My Presets unavailable: ${users.warning}`, 'warning');
    renderIntentOptions();
    renderOptions();
    return { base, users };
  }

  async function getUserPreset(presetId) {
    if (state.userCache.has(presetId)) return state.userCache.get(presetId);
    const record = await apiJson(`${USER_ENDPOINT}/${encodeURIComponent(presetId)}`);
    state.userCache.set(presetId, record);
    return record;
  }

  async function selectedRecord() {
    const meta = selectedOptionMeta();
    if (meta.source === 'user') return getUserPreset(meta.id);
    const entry = resolveBuiltInPreset(meta.id, contextFromDom(state.scope));
    return entry ? { preset_id: meta.id, name: entry.label, description: entry.description || '', source: 'built_in', immutable: true, context: contextFromDom(state.scope), values: Object.assign({}, entry.values || {}), application_mode: entry.application_mode, authoring_template: Boolean(entry.authoring_template) } : null;
  }

  async function applySelected(reason) {
    if (!state.panel) return null;
    const meta = selectedOptionMeta();
    if (!meta.id) return null;
    state.selectedPresetId = meta.id;
    state.applying = true;
    try {
      const record = await selectedRecord();
      if (!record) throw new Error('The selected sampling preset is unavailable for this route.');
      clearManagedFields();
      const mode = String(record.application_mode || 'replace_sampling_fields');
      if (mode === 'replace_sampling_fields') Object.entries(record.values || {}).forEach(([name, value]) => setFieldValue(name, value));
      // delegate_provider and clean_slate intentionally leave fields unset.
      state.dirty = false;
      syncHiddenState();
      updateButtons();
      const detail = {
        schema: SCHEMA, phase: PHASE, reason: reason || 'apply', preset_id: meta.id,
        source: meta.source, context: contextFromDom(state.scope), values: Object.assign({}, record.values || {}),
        application_mode: mode, output_intent: state.outputIntent,
      };
      root.document?.dispatchEvent?.(new CustomEvent('neo:image-sampling-preset-applied', { detail }));
      root.document?.dispatchEvent?.(new CustomEvent('neo:image-state-changed', { detail }));
      setStatus(`${record.name || meta.label} applied${mode === 'delegate_provider' ? ' · provider owns sampling' : mode === 'clean_slate' ? ' · clean slate' : ''}.`, 'success');
      renderInspector();
      return detail;
    } finally {
      state.applying = false;
    }
  }

  function captureSnapshot() {
    const context = contextFromDom(state.scope);
    return {
      context: { family: context.family, variant: context.variant, loader: context.loader, mode: context.mode, intent: '*' },
      values: readManagedValues(),
      base_preset_id: state.selectedPresetId || '',
    };
  }

  async function saveAs(defaultName) {
    const name = String(root.prompt?.('Preset name', defaultName || 'My Sampling Preset') || '').trim();
    if (!name) return null;
    const snapshot = captureSnapshot();
    const record = await apiJson(USER_ENDPOINT, { method: 'POST', body: JSON.stringify({ name, snapshot }) });
    state.userCache.set(record.preset_id, record);
    await refreshUsers();
    state.selectedPresetId = record.preset_id;
    renderOptions();
    state.panel.querySelector('[data-neo-sampling-preset-select]').value = record.preset_id;
    syncHiddenState();
    updateButtons();
    setStatus(`${record.name} saved to My Presets.`, 'success');
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-sampling-preset-saved', { detail: { preset_id: record.preset_id, context: snapshot.context } }));
    return record;
  }

  async function duplicateSelected() {
    const record = await selectedRecord();
    if (!record) return null;
    const name = String(root.prompt?.('Duplicate preset as', `${record.name || record.label || 'Sampling Preset'} Copy`) || '').trim();
    if (!name) return null;
    const snapshot = { context: Object.assign({}, record.context || contextFromDom(state.scope), { intent: '*' }), values: Object.assign({}, record.values || {}), base_preset_id: record.preset_id || state.selectedPresetId };
    const created = await apiJson(USER_ENDPOINT, { method: 'POST', body: JSON.stringify({ name, description: record.description || '', snapshot }) });
    state.userCache.set(created.preset_id, created);
    await refreshUsers();
    state.selectedPresetId = created.preset_id;
    renderOptions();
    state.panel.querySelector('[data-neo-sampling-preset-select]').value = created.preset_id;
    updateButtons();
    syncHiddenState();
    setStatus(`${created.name} created in My Presets.`, 'success');
    return created;
  }

  async function renameSelected() {
    const meta = selectedOptionMeta();
    if (meta.source !== 'user') return null;
    const record = await getUserPreset(meta.id);
    const name = String(root.prompt?.('Rename preset', record.name || '') || '').trim();
    if (!name || name === record.name) return record;
    const updated = await apiJson(`${USER_ENDPOINT}/${encodeURIComponent(meta.id)}`, { method: 'PUT', body: JSON.stringify({ name }) });
    state.userCache.set(meta.id, updated);
    await refreshUsers();
    state.selectedPresetId = meta.id;
    renderOptions();
    setStatus(`Renamed to ${updated.name}.`, 'success');
    return updated;
  }

  async function deleteSelected() {
    const meta = selectedOptionMeta();
    if (meta.source !== 'user') return null;
    if (root.confirm && !root.confirm(`Delete "${meta.label}" from My Presets?`)) return null;
    const result = await apiJson(`${USER_ENDPOINT}/${encodeURIComponent(meta.id)}`, { method: 'DELETE' });
    state.userCache.delete(meta.id);
    await refreshUsers();
    state.selectedPresetId = PROVIDER_DEFAULTS_ID;
    renderOptions();
    await applySelected('delete_fallback');
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-sampling-preset-deleted', { detail: { preset_id: meta.id } }));
    return result;
  }

  async function refreshUsers() {
    const payload = await apiJson(USER_ENDPOINT);
    state.userPresets = Array.isArray(payload?.presets) ? payload.presets : [];
    return state.userPresets;
  }

  async function refreshCatalog(options) {
    const serial = ++state.refreshSerial;
    if (!state.base) await loadContracts();
    else await refreshUsers().catch(() => state.userPresets);
    if (serial !== state.refreshSerial) return;
    const before = state.selectedPresetId;
    renderIntentOptions();
    renderOptions();
    const changedSelection = before !== state.selectedPresetId;
    if (options?.autoApply || changedSelection) await applySelected(changedSelection ? 'route_fallback' : 'route_change');
  }

  async function handleRouteChange() {
    const key = contextKey(contextFromDom(state.scope));
    if (!key || key === state.lastContextKey) return;
    state.lastContextKey = key;
    try {
      await refreshCatalog({ autoApply: true });
    } catch (error) {
      setStatus(`Preset route refresh failed: ${error.message}`, 'warning');
    }
  }

  function setOutputIntent(value) {
    state.outputIntent = normalizeIntent(value);
    const select = state.panel?.querySelector?.('[data-neo-output-intent]');
    if (select) select.value = state.outputIntent;
    syncHiddenState();
    const detail = { schema: 'neo.image.output_intent_ui.v1', phase: PHASE, output_intent: state.outputIntent, sampling_mutation: false };
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-output-intent-changed', { detail }));
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-state-changed', { detail }));
    setStatus(`Output Intent: ${select?.selectedOptions?.[0]?.textContent || state.outputIntent}. Sampling values unchanged.`, 'muted');
    renderInspector();
    return detail;
  }

  function ensureStyles() {
    if (!root.document || root.document.getElementById('neo-image-sampling-preset-styles')) return;
    const style = root.document.createElement('style');
    style.id = 'neo-image-sampling-preset-styles';
    style.textContent = `
      .neo-image-sampling-presets{display:grid;gap:.7rem;padding:.8rem;margin:0 0 .85rem;border:1px solid var(--neo-border,#334155);border-radius:.65rem;background:var(--neo-panel,#111827)}
      .neo-image-sampling-presets__head{display:flex;align-items:flex-start;justify-content:space-between;gap:.7rem}.neo-image-sampling-presets__head strong{display:block}.neo-image-sampling-presets__head small{display:block;margin-top:.18rem;color:var(--neo-muted,#94a3b8);line-height:1.35}
      .neo-image-sampling-presets__badge{white-space:nowrap;font-size:.72rem;padding:.18rem .45rem;border-radius:999px;border:1px solid var(--neo-border,#334155);color:var(--neo-muted,#94a3b8)}
      .neo-image-sampling-presets__grid{display:grid;grid-template-columns:minmax(180px,1fr) minmax(160px,.75fr);gap:.6rem}.neo-image-sampling-presets label{display:grid;gap:.3rem;font-size:.78rem;color:var(--neo-muted,#94a3b8)}
      .neo-image-sampling-presets select{min-width:0;width:100%}.neo-image-sampling-presets__actions{display:flex;flex-wrap:wrap;gap:.4rem}.neo-image-sampling-presets__actions button{font:inherit}
      .neo-image-sampling-presets__status{font-size:.76rem;line-height:1.35;color:var(--neo-muted,#94a3b8)}.neo-image-sampling-presets__status[data-tone="warning"]{color:var(--neo-warning,#f6c177)}.neo-image-sampling-presets__status[data-tone="success"]{color:var(--neo-success,#86efac)}
      .neo-image-sampling-inspector{display:grid;gap:.32rem;padding:.6rem;border:1px dashed var(--neo-border,#334155);border-radius:.5rem;background:rgba(15,23,42,.35)}.neo-image-sampling-inspector__title{display:flex;justify-content:space-between;gap:.5rem;align-items:center}.neo-image-sampling-inspector__title span,.neo-image-sampling-inspector small{font-size:.7rem;color:var(--neo-muted,#94a3b8)}.neo-image-sampling-inspector__row{display:grid;grid-template-columns:90px 1fr;gap:.5rem;font-size:.72rem}.neo-image-sampling-inspector__row span{color:var(--neo-muted,#94a3b8)}.neo-image-sampling-inspector__row code{white-space:normal;overflow-wrap:anywhere}
      @media(max-width:720px){.neo-image-sampling-presets__grid{grid-template-columns:1fr}}
    `;
    root.document.head?.appendChild(style);
  }

  function panelHtml() {
    return `
      <div class="neo-image-sampling-presets__head"><div><strong>Sampling Preset</strong><small>Workflow-aware sampling only. Output Intent stays separate and never injects prompts, styles, or LoRAs.</small></div><span class="neo-image-sampling-presets__badge" data-neo-preset-badge>Built-in · Read-only</span></div>
      <div class="neo-image-sampling-presets__grid">
        <label>Sampling Preset<select data-neo-sampling-preset-select aria-label="Sampling Preset"></select></label>
        <label>Output Intent<select data-neo-output-intent aria-label="Output Intent"></select></label>
      </div>
      <div class="neo-image-sampling-presets__actions">
        <button class="neo-btn compact" type="button" data-neo-preset-action="apply">Apply</button>
        <button class="neo-btn secondary compact" type="button" data-neo-preset-action="save_as">Save As</button>
        <button class="neo-btn secondary compact" type="button" data-neo-preset-action="duplicate">Duplicate</button>
        <button class="neo-btn secondary compact" type="button" data-neo-preset-action="rename">Rename</button>
        <button class="neo-btn secondary compact" type="button" data-neo-preset-action="delete">Delete</button>
        <button class="neo-btn secondary compact" type="button" data-neo-preset-action="reset">Reset</button>
      </div>
      <div class="neo-image-sampling-presets__status" data-neo-preset-status>Loading preset contracts…</div>
      <div class="neo-image-sampling-inspector" data-neo-preset-inspector></div>
      <input type="hidden" name="sampling_preset_id" value="" data-neo-sampling-preset-hidden>
      <input type="hidden" name="output_intent" value="none" data-neo-output-intent-hidden>
    `;
  }

  function nearestPanelLike(element) {
    return element?.closest?.(
      '[data-image-params-root], [data-section-id="params"], [data-section="params"], .neo-image-parameters, .neo-parameters, .neo-card, .neo-panel, .panel, section, form'
    ) || null;
  }

  function containsImageSamplingSignature(container) {
    if (!container || typeof container.querySelector !== 'function') return false;
    const family = findFirst(container, FIELD_SELECTORS.family);
    if (!family) return false;
    const routeControl = findFirst(container, [...FIELD_SELECTORS.loader, ...FIELD_SELECTORS.mode, ...FIELD_SELECTORS.model]);
    let samplingCount = 0;
    ['steps', 'sampler', 'scheduler', 'cfg', 'true_cfg', 'flux_guidance', 'denoise', 'seed'].forEach((name) => {
      if (findFirst(container, FIELD_SELECTORS[name])) samplingCount += 1;
    });
    return Boolean(routeControl) && samplingCount >= 2;
  }

  function legacyImageParamsTarget(searchRoot) {
    // IP-8.1 integration fallback for Neo's legacy Image renderer. Earlier IP-7
    // tests only exercised explicit data-image-params-root fixtures, while the
    // live renderer can expose the same controls without that marker. Require an
    // Image family field + route control + at least two sampling controls before
    // accepting a generic panel, so Video/Admin parameter cards cannot match.
    const family = findFirst(searchRoot, FIELD_SELECTORS.family) || findFirst(root.document, FIELD_SELECTORS.family);
    if (!family) return null;
    const candidates = [];
    let node = nearestPanelLike(family);
    while (node && node !== root.document?.body && node !== root.document?.documentElement) {
      candidates.push(node);
      node = nearestPanelLike(node.parentElement);
    }
    const explicitPanel = candidates.find(containsImageSamplingSignature);
    if (explicitPanel) return explicitPanel;

    // Last-resort sibling/common workspace search. This is intentionally strict.
    const imageLike = family.closest?.('[data-image-workspace], [data-surface="image"], [data-surface-id="image"], #imagePanel, .neo-image-panel, .neo-image-workspace, #surfacePanels');
    if (imageLike && containsImageSamplingSignature(imageLike)) {
      const steps = findFirst(imageLike, FIELD_SELECTORS.steps);
      return nearestPanelLike(steps) || imageLike;
    }
    return null;
  }

  function findMountTarget(scope) {
    const searchRoot = scope && typeof scope.querySelector === 'function' ? scope : root.document;
    const explicitImageSelectors = [
      '[data-image-sampling-preset-root]', '[data-image-params-root]', '[data-slot="image.generate.params"]',
      '[data-slot="image.img2img.params"]', '[data-slot="image.inpaint.params"]', '[data-slot="image.outpaint.params"]',
      '[data-slot="image.edit.params"]', '#imageParameters', '#imageParams', '.neo-image-parameters',
    ];
    const explicit = findFirst(searchRoot, explicitImageSelectors);
    if (explicit) return explicit;

    // Never mount into another surface's generic Parameters section. IP-8.1 may
    // use the legacy fallback only after the candidate proves an Image-family +
    // route-control + sampling-control signature.
    // Prefer a proven Image workspace when modern surface markers exist.
    const imageRoot = searchRoot.matches?.('[data-image-workspace], [data-surface="image"], [data-surface-id="image"], #imagePanel, .neo-image-panel, .neo-image-workspace')
      ? searchRoot
      : findFirst(searchRoot, ['[data-image-workspace]', '[data-surface="image"]', '[data-surface-id="image"]', '#imagePanel', '.neo-image-panel', '.neo-image-workspace']);
    if (!imageRoot) {
      const legacy = legacyImageParamsTarget(searchRoot);
      if (legacy) return legacy;
      return null;
    }
    const params = findFirst(imageRoot, ['[data-section-id="params"]', '[data-section="params"]', '.neo-parameters']);
    if (params) return params;

    // A proven Image workspace can still use the legacy field signature when its
    // Parameters section itself has no canonical marker.
    return legacyImageParamsTarget(imageRoot);
  }

  function bindPanel() {
    if (!state.panel) return;
    state.panel.addEventListener('change', (event) => {
      const select = event.target?.closest?.('[data-neo-sampling-preset-select]');
      if (select) {
        state.selectedPresetId = select.value;
        state.dirty = false;
        syncHiddenState();
        updateButtons();
        setStatus('Preset selected. Apply to update sampling controls.', 'muted');
        renderInspector();
        return;
      }
      const intent = event.target?.closest?.('[data-neo-output-intent]');
      if (intent) setOutputIntent(intent.value);
    });
    state.panel.addEventListener('click', async (event) => {
      const button = event.target?.closest?.('[data-neo-preset-action]');
      if (!button) return;
      const action = button.dataset.neoPresetAction;
      button.disabled = true;
      try {
        if (action === 'apply' || action === 'reset') await applySelected(action);
        else if (action === 'save_as') await saveAs();
        else if (action === 'duplicate') await duplicateSelected();
        else if (action === 'rename') await renameSelected();
        else if (action === 'delete') await deleteSelected();
      } catch (error) {
        setStatus(error.message || String(error), 'warning');
      } finally {
        updateButtons();
      }
    });
  }

  async function mount(scope) {
    if (!root.document) return null;
    ensureStyles();
    const target = findMountTarget(scope);
    if (!target) return null;
    const existing = target.querySelector?.('[data-neo-image-sampling-presets]');
    if (existing) {
      state.panel = existing;
      state.scope = imageScopeFrom(existing);
      state.mounted = true;
      return existing;
    }
    const panel = root.document.createElement('section');
    panel.className = 'neo-image-sampling-presets';
    panel.setAttribute('data-neo-image-sampling-presets', 'true');
    panel.setAttribute('data-phase', PHASE);
    panel.innerHTML = panelHtml();
    target.prepend(panel);
    state.panel = panel;
    state.scope = imageScopeFrom(panel) || target;
    state.mounted = true;
    bindPanel();
    root.document?.dispatchEvent?.(new CustomEvent('neo:image-sampling-presets-mounted', { detail: { phase: PHASE, hotfix_phase: HOTFIX_PHASE, target } }));
    try {
      await loadContracts();
      state.lastContextKey = contextKey(contextFromDom(state.scope));
      if (state.dirty) {
        // The workspace DOM can be rebuilt after a manual sampling edit. Do
        // not re-apply the previously selected built-in preset on remount or
        // it will erase the authored value that neo.js just restored from
        // state.imageDraft (most visibly Qwen True CFG).
        syncHiddenState();
        updateButtons();
        setStatus('Manual sampling edits preserved after workspace refresh.', 'warning');
        renderInspector();
      } else {
        await applySelected('initial_mount');
      }
    } catch (error) {
      setStatus(`Sampling presets unavailable: ${error.message}`, 'warning');
    }
    return panel;
  }

  function bindDocument() {
    if (!root.document || root.__neoImageSamplingPresetsBound) return;
    root.__neoImageSamplingPresetsBound = true;
    let mountQueued = false;
    let mountRunning = false;
    let remountRequested = false;
    const enqueue = typeof root.queueMicrotask === 'function'
      ? root.queueMicrotask.bind(root)
      : (callback) => Promise.resolve().then(callback);
    const tryMount = () => {
      if (mountRunning) { remountRequested = true; return; }
      if (mountQueued) return;
      mountQueued = true;
      enqueue(async () => {
        mountQueued = false;
        mountRunning = true;
        try {
          await mount(root.document);
        } catch (_error) {
          // The panel is optional while a non-Image surface is active.
        } finally {
          mountRunning = false;
          if (remountRequested) {
            remountRequested = false;
            tryMount();
          }
        }
      });
    };
    root.document.addEventListener('change', (event) => {
      if (state.applying) return;
      const target = event.target;
      if (!target || target.closest?.('[data-neo-image-sampling-presets]')) return;
      for (const name of ROUTE_FIELDS) {
        const field = fieldFor(name, state.scope || root.document);
        if (field === target) { handleRouteChange(); return; }
      }
      for (const name of managedFields()) {
        const field = fieldFor(name, state.scope || root.document);
        if (field === target && state.mounted) {
          state.dirty = true;
          setStatus('Sampling controls modified after preset application. Reset reapplies the selected preset.', 'warning');
          renderInspector();
          return;
        }
      }
    }, true);
    root.document.addEventListener('input', (event) => {
      if (state.applying || !state.mounted) return;
      const target = event.target;
      if (!target || target.closest?.('[data-neo-image-sampling-presets]')) return;
      for (const name of managedFields()) {
        if (fieldFor(name, state.scope || root.document) === target) {
          state.dirty = true;
          setStatus('Sampling controls modified after preset application. Reset reapplies the selected preset.', 'warning');
          renderInspector();
          break;
        }
      }
    }, true);
    root.document.addEventListener('neo:image-route-changed', handleRouteChange);
    root.document.addEventListener('neo:image-state-changed', () => { if (!state.applying) tryMount(); });
    const ObserverCtor = root.MutationObserver || (typeof MutationObserver !== 'undefined' ? MutationObserver : null);
    state.observer = ObserverCtor ? new ObserverCtor(() => {
      if (!state.mounted || !root.document.contains(state.panel)) {
        state.mounted = false;
        tryMount();
      }
    }) : null;
    const start = () => {
      const observerTarget = root.document.getElementById?.('surfacePanels') || root.document.body || root.document.documentElement;
      if (state.observer && observerTarget) state.observer.observe(observerTarget, { childList: true, subtree: true });
      tryMount();
    };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }

  function status() {
    return {
      schema: SCHEMA, phase: PHASE, hotfix_phase: HOTFIX_PHASE, ir1_phase: IR1_PHASE, ir2_phase: IR2_PHASE, mounted: state.mounted, selected_preset_id: state.selectedPresetId,
      output_intent: state.outputIntent, dirty: state.dirty, user_preset_count: state.userPresets.length,
      context: contextFromDom(state.scope), user_endpoint: USER_ENDPOINT, visible_ui_owner: VISIBLE_UI_OWNER, auto_mount: false,
    };
  }

  root.NeoImageSamplingPresets = Object.freeze({
    schema: SCHEMA,
    phase: PHASE,
    mount,
    refresh: refreshCatalog,
    setBaseContract,
    resolveBuiltInPreset,
    availableBuiltIns,
    contextFromDom,
    applySelected,
    saveAs,
    duplicateSelected,
    renameSelected,
    deleteSelected,
    reset: () => applySelected('reset'),
    setOutputIntent,
    inspect: renderInspector,
    status,
  });

  // IR-1: the live Image workspace owns one unified Preset dropdown in neo.js.
  // Keep this module available as the sampling resolver/authoring API, but do not
  // auto-mount a second Sampling Preset / Output Intent panel.
}(typeof window !== 'undefined' ? window : globalThis));
