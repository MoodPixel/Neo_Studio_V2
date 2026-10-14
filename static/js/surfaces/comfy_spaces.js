// Neo Studio V2 surface module: Comfy Spaces (NCS-12 Full Dependency Preflight + Health)
// User-facing library + runner over published declarative Comfy Space packages.
(function () {
  'use strict';

  const SURFACE_ID = 'comfy_spaces';
  const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'canceled']);
  const ASSET_CONTROLS = new Set(['image_upload', 'mask_upload', 'video_upload', 'audio_upload', 'file_upload']);
  const state = {
    library: null,
    libraryLoading: false,
    libraryError: '',
    portableMessage: '', portableBusy: false,
    active: null,
    activeLoading: false,
    activeError: '',
    search: '',
    category: 'all',
    values: {},
    assetFiles: {},
    assetRefs: {},
    runtimeProfileId: '',
    optionCatalogs: {},
    catalogStates: {},
    optionCatalogError: '',
    optionCatalogLoading: false,
    addonCatalog: null,
    addonCatalogError: '',
    addonCatalogLoading: false,
    graphAddons: [],
    addonFiles: {},
    addonAssetRefs: {},
    addonDraft: { moduleId: '', rawNodeClass: '', rawAnchorId: '' },
    dependencyHealth: null,
    dependencyHealthLoading: false,
    dependencyHealthError: '',
    stageRunMode: 'all',
    stageId: '',
    enabledStageIds: [],
    continueFromRunId: '',
    presets: [], presetName: '', presetError: '', selectedPresetId: '',
    configPanelOpen: false,
    variants: [],
    variantLoading: false,
    variantError: '',
    selectedVariantId: '',
    variantName: '',
    saveVariantValues: true,
    activeRun: null,
    selectedRun: null,
    progress: null,
    preflight: null,
    actionError: '',
    actionMessage: '',
    busyAction: '',
    socket: null,
    pollTimer: null,
    lastContext: null,
    loadScheduled: false,
  };

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  }
  function attr(value) { return esc(value); }
  function normalizeStatus(value) { return String(value || '').trim().toLowerCase(); }
  function isTerminal(run) { return TERMINAL.has(normalizeStatus(run?.status)); }
  function rendererId() {
    const s = state.active?.space || {};
    return `comfy-space-runner-${String(s.space_id || 'space').replace(/[^A-Za-z0-9_.:-]/g, '-')}`;
  }
  function requestRender() {
    const render = state.lastContext?.render;
    if (typeof render === 'function') render();
  }
  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, options);
    const text = await response.text();
    let payload = {};
    if (text) {
      try { payload = JSON.parse(text); }
      catch (_) { payload = { detail: text }; }
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === 'string' ? detail : (detail?.message || payload?.message || `${response.status} ${response.statusText}`);
      throw new Error(message);
    }
    return payload;
  }
  function scheduleLibraryLoad() {
    if (state.library || state.libraryLoading || state.loadScheduled) return;
    state.loadScheduled = true;
    setTimeout(() => {
      state.loadScheduled = false;
      loadLibrary().catch(() => {});
    }, 0);
  }
  async function loadLibrary({ quiet = false } = {}) {
    if (state.libraryLoading) return state.library;
    state.libraryLoading = true;
    if (!quiet) state.libraryError = '';
    try {
      state.library = await jsonRequest('/api/comfy-spaces/library');
      state.libraryError = '';
      return state.library;
    } catch (error) {
      state.libraryError = error?.message || String(error);
      throw error;
    } finally {
      state.libraryLoading = false;
      requestRender();
    }
  }
  function favorites() {
    try { return new Set(JSON.parse(localStorage.getItem('neo.comfy_spaces.favorites.v1') || '[]')); }
    catch (_) { return new Set(); }
  }
  function setFavorites(values) {
    try { localStorage.setItem('neo.comfy_spaces.favorites.v1', JSON.stringify([...values])); }
    catch (_) {}
  }
  function toggleFavorite(payload = {}) {
    const id = String(payload.spaceId || '');
    if (!id) return;
    const next = favorites();
    if (next.has(id)) next.delete(id); else next.add(id);
    setFavorites(next);
    requestRender();
  }
  function categories() {
    const rows = state.library?.spaces || [];
    return ['all', ...new Set(rows.map((row) => String(row.category || 'Workflow').trim()).filter(Boolean))];
  }
  function filteredSpaces() {
    const q = state.search.trim().toLowerCase();
    const fav = favorites();
    return (state.library?.spaces || []).filter((space) => {
      if (state.category === 'favorites' && !fav.has(space.space_id)) return false;
      if (state.category !== 'all' && state.category !== 'favorites' && String(space.category || 'Workflow') !== state.category) return false;
      if (!q) return true;
      const hay = [space.name, space.description, space.category, ...(space.tags || [])].join(' ').toLowerCase();
      return hay.includes(q);
    });
  }
  function runProgressPercent(run) {
    const p = run?.progress || {};
    const direct = Number(p.percent ?? p.progress_percent ?? NaN);
    if (Number.isFinite(direct)) return Math.max(0, Math.min(100, direct));
    const value = Number(p.value ?? p.step ?? NaN);
    const max = Number(p.max ?? p.total_steps ?? NaN);
    if (Number.isFinite(value) && Number.isFinite(max) && max > 0) return Math.max(0, Math.min(100, (value / max) * 100));
    return 0;
  }
  function statusLabel(run) {
    const status = normalizeStatus(run?.status || 'idle');
    if (status === 'cancel_requested') return 'Stopping';
    return status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Idle';
  }
  function runStage(progress, run) {
    const p = progress?.progress || progress || run?.progress || {};
    return String(p.stage_label || p.stage || p.current_node_title || p.current_node_class || run?.message || statusLabel(run));
  }

  async function importPortable() {
    const input = document.getElementById('neo-comfy-neospace-upload');
    const file = input?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.neospace') || file.size > 33554432) {
      state.portableMessage = 'Choose a .neospace file up to 32 MiB.'; requestRender(); return;
    }
    state.portableBusy = true;
    state.portableMessage = 'Checking imported Space…'; requestRender();
    try {
      const form = new FormData(); form.append('file', file);
      const previewResponse = await fetch('/api/comfy-spaces/portable/import?dry_run=true', { method: 'POST', body: form });
      const preview = await previewResponse.json();
      if (!previewResponse.ok) throw new Error(typeof preview.detail === 'string' ? preview.detail : 'Import validation failed');
      if (!confirm(`Import ${preview.space_id} v${preview.version}? Models/custom nodes must be installed separately. Existing versions will not be overwritten.`)) {
        state.portableMessage = 'Import cancelled.'; return;
      }
      const resultResponse = await fetch('/api/comfy-spaces/portable/import', { method: 'POST', body: form });
      const result = await resultResponse.json();
      if (!resultResponse.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Import failed');
      state.portableMessage = `Imported ${result.space_id} v${result.version}. Check runtime dependencies before running.`;
      state.library = null;
      await loadLibrary({quiet: true});
    } catch (error) { state.portableMessage = error?.message || String(error); }
    finally { state.portableBusy = false; requestRender(); }
  }

  function libraryCard(space) {
    const fav = favorites().has(space.space_id);
    const active = space.active_run;
    const pct = runProgressPercent(active);
    const visual = space.thumbnail_url
      ? `<img class="neo-comfy-library-thumb" src="${attr(space.thumbnail_url)}" alt="" loading="lazy">`
      : `<div class="neo-comfy-library-thumb placeholder">${space.icon_url ? `<img src="${attr(space.icon_url)}" alt="">` : '<span>◆</span>'}</div>`;
    const tags = [space.category, ...(space.tags || []).slice(0, 2)].filter(Boolean).map((tag) => `<span class="neo-badge">${esc(tag)}</span>`).join('');
    const health = space.dependency_health || {}; const healthStatus = String(health.status || 'not_checked');
    const healthBadge = `<span class="neo-badge ${healthStatus === 'ready' && !health.stale ? 'success' : healthStatus === 'blocked' || healthStatus === 'unreachable' ? 'warning' : ''}">Health · ${esc(health.stale && healthStatus === 'ready' ? 'stale' : healthStatus.replaceAll('_',' '))}</span>`;
    const activeHtml = active ? `<div class="neo-comfy-card-run"><span>${esc(statusLabel(active))}</span><div class="neo-progress-track"><span style="width:${pct.toFixed(1)}%"></span></div></div>` : '';
    return `<article class="neo-comfy-library-card" data-space-card="${attr(space.space_id)}">
      ${visual}
      <div class="neo-comfy-library-card-body">
        <div class="neo-comfy-card-title-row"><div><h3>${esc(space.name)}</h3><small>active v${esc(space.active_version || space.version)}${space.latest_version && space.latest_version !== (space.active_version || space.version) ? ` · latest v${esc(space.latest_version)}` : ''}</small></div><button type="button" class="neo-icon-btn" title="${fav ? 'Remove from favorites' : 'Add to favorites'}" data-space-id="${attr(space.space_id)}" onclick="neoInvokeSurfaceModule('comfy_spaces','toggleFavorite',{spaceId:this.dataset.spaceId})">${fav ? '★' : '☆'}</button></div>
        <p>${esc(space.description || 'Published Comfy Space')}</p>
        <div class="neo-badge-row">${tags}${healthBadge}${space.latest_version && space.latest_version !== (space.active_version || space.version) ? '<span class="neo-badge warning">Rolled back</span>' : ''}</div>
        ${activeHtml}
        <div class="neo-comfy-card-actions"><a class="neo-btn compact" download href="/api/comfy-spaces/portable/export/${encodeURIComponent(space.space_id)}/${encodeURIComponent(space.version)}" title="Export this published version as a .neospace package">Export .neospace</a><button type="button" class="neo-btn primary" data-space-id="${attr(space.space_id)}" data-version="${attr(space.version)}" onclick="neoInvokeSurfaceModule('comfy_spaces','openSpace',{spaceId:this.dataset.spaceId,version:this.dataset.version})">${active ? 'Open run' : 'Open'}</button></div>
      </div>
    </article>`;
  }
  function libraryHtml() {
    scheduleLibraryLoad();
    if (state.libraryLoading && !state.library) return '<div class="neo-comfy-loading">Loading published Comfy Spaces…</div>';
    if (state.libraryError && !state.library) return `<div class="neo-inline-status error"><strong>Could not load Comfy Spaces.</strong><p>${esc(state.libraryError)}</p><button class="neo-btn" onclick="neoInvokeSurfaceModule('comfy_spaces','refreshLibrary')">Retry</button></div>`;
    const rows = filteredSpaces();
    const cats = categories();
    const filters = [...cats, 'favorites'].filter((item, idx, arr) => arr.indexOf(item) === idx).map((cat) => {
      const label = cat === 'all' ? 'All' : cat === 'favorites' ? 'Favorites' : cat;
      return `<button type="button" class="neo-btn compact ${state.category === cat ? 'active' : ''}" data-category="${attr(cat)}" onclick="neoInvokeSurfaceModule('comfy_spaces','setCategory',{category:this.dataset.category})">${esc(label)}</button>`;
    }).join('');
    if (!(state.library?.spaces || []).length) {
      return `<section class="neo-comfy-spaces-library"><header class="neo-comfy-library-head"><div><span class="neo-kicker">WORKSPACE</span><h2>Comfy Spaces</h2><p>Run published ComfyUI workflows through native Neo interfaces.</p></div></header><div class="neo-empty neo-comfy-empty"><h3>No Comfy Spaces published yet</h3><p>Publish a workflow from Admin → Comfy Spaces to make it available here.</p><button class="neo-btn" onclick="neoInvokeSurfaceModule('comfy_spaces','openBuilder')">Open Comfy Space Builder</button><label class="neo-btn compact" for="neo-comfy-neospace-upload">Import .neospace</label><input hidden id="neo-comfy-neospace-upload" type="file" accept=".neospace" onchange="neoInvokeSurfaceModule('comfy_spaces','importPortable')"></div>${state.portableMessage ? `<p class="neo-inline-status">${esc(state.portableMessage)}</p>` : ''}</section>`;
    }
    return `<section class="neo-comfy-spaces-library">
      <header class="neo-comfy-library-head"><div><span class="neo-kicker">WORKSPACE</span><h2>Comfy Spaces</h2><p>Published ComfyUI workflows, presented as native Neo tools.</p></div><div class="neo-comfy-library-count">${esc(state.library.count || 0)} published</div></header>
      <div class="neo-comfy-library-toolbar"><input type="search" value="${attr(state.search)}" placeholder="Search Spaces" aria-label="Search Comfy Spaces" oninput="neoInvokeSurfaceModule('comfy_spaces','setSearch',{value:this.value})"><div class="neo-comfy-library-filters">${filters}</div><button class="neo-btn compact" onclick="neoInvokeSurfaceModule('comfy_spaces','refreshLibrary')">Refresh</button><label class="neo-btn compact" for="neo-comfy-neospace-upload">Import .neospace</label><input hidden id="neo-comfy-neospace-upload" type="file" accept=".neospace" onchange="neoInvokeSurfaceModule('comfy_spaces','importPortable')"></div>
      ${state.portableMessage ? `<div class="neo-inline-status">${esc(state.portableMessage)}</div>` : ''}
      ${state.libraryError ? `<div class="neo-inline-status warning"><span>${esc(state.libraryError)}</span></div>` : ''}
      <div class="neo-comfy-library-grid">${rows.length ? rows.map(libraryCard).join('') : '<div class="neo-empty">No Spaces match these filters.</div>'}</div>
    </section>`;
  }

  async function loadRuntimeOptions() {
    const s = state.active?.space;
    const runtime = state.runtimeProfileId;
    if (!s || !runtime) {
      state.optionCatalogs = {};
      state.catalogStates = {};
      state.optionCatalogError = runtime ? '' : 'No ComfyUI runtime selected.';
      state.optionCatalogLoading = false;
      return;
    }
    state.optionCatalogLoading = true;
    state.optionCatalogError = '';
    requestRender();
    try {
      const params = new URLSearchParams({
        space_id: String(s.space_id || ''),
        version: String(s.version || ''),
        runtime_profile_id: String(runtime || ''),
      });
      const payload = await jsonRequest(`/api/comfy-spaces/runtime/options?${params.toString()}`);
      state.optionCatalogs = payload.option_catalogs || {};
      state.catalogStates = payload.catalog_states || {};
      state.optionCatalogError = '';
    } catch (error) {
      state.optionCatalogs = {};
      state.catalogStates = {};
      state.optionCatalogError = error.message || String(error);
      state.actionMessage = `Runtime catalogs could not be refreshed: ${state.optionCatalogError}`;
    } finally {
      state.optionCatalogLoading = false;
      requestRender();
    }
  }
  async function loadAddonCatalog() {
    const s = state.active?.space;
    const runtime = state.runtimeProfileId;
    if (!s || !runtime) {
      state.addonCatalog = null;
      state.addonCatalogError = runtime ? '' : 'No ComfyUI runtime selected.';
      state.addonCatalogLoading = false;
      return;
    }
    state.addonCatalogLoading = true;
    state.addonCatalogError = '';
    requestRender();
    try {
      const params = new URLSearchParams({
        space_id: String(s.space_id || ''),
        version: String(s.version || ''),
        runtime_profile_id: String(runtime || ''),
      });
      state.addonCatalog = await jsonRequest(`/api/comfy-spaces/runtime/addons/catalog?${params.toString()}`);
      state.addonCatalogError = '';
      const modules = state.addonCatalog?.modules || [];
      if (!state.addonDraft.moduleId) state.addonDraft.moduleId = String(modules.find((item) => item.compatible)?.module_id || modules[0]?.module_id || '');
      const rawNodes = state.addonCatalog?.raw_nodes || [];
      if (!state.addonDraft.rawNodeClass) state.addonDraft.rawNodeClass = String(rawNodes[0]?.class_type || '');
      const raw = rawNodes.find((item) => item.class_type === state.addonDraft.rawNodeClass);
      if (!state.addonDraft.rawAnchorId) state.addonDraft.rawAnchorId = String(raw?.placements?.[0]?.anchor_id || '');
    } catch (error) {
      state.addonCatalog = null;
      state.addonCatalogError = error.message || String(error);
    } finally {
      state.addonCatalogLoading = false;
      requestRender();
    }
  }

  async function loadDependencyHealth({ refresh = false } = {}) {
    const s = state.active?.space; const runtime = state.runtimeProfileId;
    if (!s || !runtime) { state.dependencyHealth = null; state.dependencyHealthError = ''; return null; }
    state.dependencyHealthLoading = true; state.dependencyHealthError = ''; requestRender();
    try {
      if (refresh) {
        state.dependencyHealth = await jsonRequest('/api/comfy-spaces/health/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ space_id: s.space_id, version: s.version, runtime_profile_id: runtime }),
        });
      } else {
        const params = new URLSearchParams({ space_id: String(s.space_id || ''), version: String(s.version || ''), runtime_profile_id: String(runtime || '') });
        state.dependencyHealth = await jsonRequest(`/api/comfy-spaces/health?${params.toString()}`);
      }
      state.dependencyHealthError = '';
      updateActiveHealthSummary(state.dependencyHealth);
      return state.dependencyHealth;
    } catch (error) {
      state.dependencyHealth = null; state.dependencyHealthError = error.message || String(error); throw error;
    } finally { state.dependencyHealthLoading = false; requestRender(); }
  }
  async function refreshDependencyHealth() {
    if (state.busyAction || !state.runtimeProfileId) return;
    state.busyAction = 'health'; state.actionError = ''; requestRender();
    try {
      const health = await loadDependencyHealth({ refresh: true });
      const label = String(health?.status || 'not_checked').replaceAll('_', ' ');
      state.actionMessage = `Dependency health refreshed: ${label}. Queue-time validation will still recheck live runtime state.`;
      await loadLibrary({ quiet: true });
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }

  async function loadPresets() {
    const active = state.active?.space;
    if (!active) { state.presets = []; return; }
    try {
      const params = new URLSearchParams({ space_id: active.space_id, version: active.version });
      const data = await jsonRequest(`/api/comfy-spaces/presets?${params.toString()}`);
      state.presets = data.presets || []; state.presetError = '';
    } catch (error) { state.presets = []; state.presetError = error.message || String(error); }
    requestRender();
  }
  function setPresetName(payload = {}) { state.presetName = String(payload.value || ''); }
  function setConfigPanelOpen(payload = {}) { state.configPanelOpen = payload.open === true; }
  function applyPreset(payload = {}) {
    const preset = state.presets.find((item) => item.preset_id === String(payload.presetId || ''));
    if (!preset || !preset.ready) { state.actionError = preset?.reason || 'Preset unavailable.'; requestRender(); return; }
    // Overlays ONLY input values; Variant, add-ons, stages, and selected media remain intact.
    state.values = { ...state.values, ...(preset.saved_values || {}) };
    state.selectedPresetId = preset.preset_id; state.preflight = null;
    state.actionMessage = `Preset applied: ${preset.name}. Revalidate before generating.`;
    requestRender();
  }
  async function savePreset() {
    const active = state.active?.space; const name = state.presetName.trim();
    if (!active || state.busyAction) return;
    if (!name) { state.actionError = 'Enter a Preset name first.'; requestRender(); return; }
    const media = new Set(['image', 'mask', 'video', 'audio', 'file']); const saved_values = {};
    for (const input of state.active?.manifest?.inputs || []) {
      if (!media.has(String(input.kind || '')) && Object.prototype.hasOwnProperty.call(state.values, input.input_id)) saved_values[input.input_id] = state.values[input.input_id];
    }
    state.busyAction = 'preset-save'; state.actionError = ''; requestRender();
    try {
      const saved = await jsonRequest('/api/comfy-spaces/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, scope: 'user', base_space_id: active.space_id, base_version: active.version, saved_values }) });
      state.presetName = ''; await loadPresets(); state.selectedPresetId = saved.preset_id;
      state.actionMessage = `Saved Preset: ${saved.name}.`;
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }
  async function deletePreset(payload = {}) {
    const id = String(payload.presetId || ''); if (!id || state.busyAction) return;
    state.busyAction = 'preset-delete'; state.actionError = ''; requestRender();
    try {
      await jsonRequest(`/api/comfy-spaces/presets/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (state.selectedPresetId === id) state.selectedPresetId = '';
      await loadPresets(); state.actionMessage = 'Preset deleted; published Space unchanged.';
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }

  async function loadVariants() {
    const s = state.active?.space;
    if (!s) { state.variants = []; state.variantError = ''; return; }
    state.variantLoading = true; state.variantError = ''; requestRender();
    try {
      const params = new URLSearchParams({ space_id: String(s.space_id || ''), version: String(s.version || '') });
      const payload = await jsonRequest(`/api/comfy-spaces/variants?${params.toString()}`);
      state.variants = payload.variants || [];
      if (state.selectedVariantId && !state.variants.some((item) => item.variant_id === state.selectedVariantId && item.ready)) state.selectedVariantId = '';
    } catch (error) { state.variants = []; state.variantError = error.message || String(error); }
    finally { state.variantLoading = false; requestRender(); }
  }
  function baseInputValues() {
    const values = {};
    for (const input of state.active?.manifest?.inputs || []) values[input.input_id] = input.default;
    return values;
  }
  function applyVariant(payload = {}) {
    const id = String(payload.variantId || '');
    if (!id) {
      state.selectedVariantId = ''; state.values = baseInputValues(); state.graphAddons = [];
      const stages = state.active?.stages?.stages || [];
      state.stageRunMode = 'all'; state.stageId = String(stages[0]?.stage_id || '');
      state.enabledStageIds = stages.filter((stage) => stage.enabled_by_default || !stage.optional).map((stage) => String(stage.stage_id || '')).filter(Boolean);
      state.addonFiles = {}; state.addonAssetRefs = {}; state.preflight = null; state.actionMessage = 'Base Space configuration restored.'; requestRender(); return;
    }
    const variant = state.variants.find((item) => item.variant_id === id);
    if (!variant) return;
    if (!variant.ready) { state.actionError = variant.reason || 'This Variant is stale and cannot be applied.'; requestRender(); return; }
    state.selectedVariantId = id;
    state.values = { ...baseInputValues(), ...(variant.saved_values || {}) };
    state.graphAddons = JSON.parse(JSON.stringify(variant.graph_addons || []));
    state.addonFiles = {}; state.addonAssetRefs = {};
    const defaults = variant.stage_defaults || {};
    state.stageRunMode = String(defaults.preferred_run_mode || 'all');
    state.stageId = String(defaults.preferred_stage_id || state.active?.stages?.stages?.[0]?.stage_id || '');
    state.enabledStageIds = [...(defaults.enabled_stage_ids || [])];
    state.continueFromRunId = ''; state.preflight = null; state.actionError = '';
    state.actionMessage = `Variant applied: ${variant.name}. Per-run assets still need to be selected.`;
    requestRender();
  }
  function setVariantName(payload = {}) { state.variantName = String(payload.value || ''); requestRender(); }
  function setSaveVariantValues(payload = {}) { state.saveVariantValues = !!payload.enabled; requestRender(); }
  function variantGraphAddons() {
    return state.graphAddons.map((item) => {
      const clone = JSON.parse(JSON.stringify(item)); clone.values = clone.values || {};
      for (const [key, value] of Object.entries(clone.values)) if (value && typeof value === 'object') delete clone.values[key];
      return clone;
    });
  }
  function variantSavedValues() {
    if (!state.saveVariantValues) return {};
    const media = new Set(['image','mask','video','audio','file']);
    const result = {};
    for (const input of state.active?.manifest?.inputs || []) {
      if (media.has(String(input.kind || ''))) continue;
      if (Object.prototype.hasOwnProperty.call(state.values, input.input_id)) result[input.input_id] = state.values[input.input_id];
    }
    return result;
  }
  async function saveVariant() {
    const s = state.active?.space; const name = state.variantName.trim();
    if (!s || !name || state.busyAction) { if (!name) state.actionError = 'Enter a Variant name first.'; requestRender(); return; }
    state.busyAction = 'variant-save'; state.actionError = ''; requestRender();
    try {
      const body = {
        name, base_space_id: s.space_id, base_version: s.version, graph_addons: variantGraphAddons(),
        enabled_stage_ids: [...state.enabledStageIds], preferred_run_mode: state.stageRunMode === 'continue' ? 'all' : state.stageRunMode,
        preferred_stage_id: state.stageRunMode === 'stage' ? String(state.stageId || '') : '', saved_values: variantSavedValues(),
      };
      const saved = await jsonRequest('/api/comfy-spaces/variants', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      state.variantName = ''; await loadVariants(); state.selectedVariantId = saved.variant_id;
      state.actionMessage = `Saved Variant: ${saved.name}.`;
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }
  async function deleteVariant(payload = {}) {
    const id = String(payload.variantId || state.selectedVariantId || ''); if (!id || state.busyAction) return;
    state.busyAction = 'variant-delete'; state.actionError = ''; requestRender();
    try {
      await jsonRequest(`/api/comfy-spaces/variants/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (state.selectedVariantId === id) state.selectedVariantId = '';
      await loadVariants(); state.actionMessage = 'Variant deleted. The published Space was not changed.';
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }

  async function openSpace(payload = {}) {
    const spaceId = String(payload.spaceId || '').trim();
    const version = String(payload.version || '').trim();
    if (!spaceId) return;
    stopRunObservers();
    state.activeLoading = true;
    state.activeError = '';
    state.actionError = '';
    state.actionMessage = '';
    requestRender();
    try {
      const path = version
        ? `/api/comfy-spaces/library/${encodeURIComponent(spaceId)}/${encodeURIComponent(version)}`
        : `/api/comfy-spaces/library/${encodeURIComponent(spaceId)}`;
      const detail = await jsonRequest(path);
      state.active = detail;
      state.values = {};
      for (const input of detail.manifest?.inputs || []) {
        state.values[input.input_id] = ['image','mask','video','audio','file'].includes(input.kind) ? null : input.default;
      }
      state.assetFiles = {};
      state.assetRefs = {};
      state.runtimeProfileId = detail.default_runtime_profile_id || detail.space?.default_runtime_profile_id || detail.runtime_profiles?.[0]?.profile_id || '';
      state.optionCatalogs = {};
      state.catalogStates = {};
      state.optionCatalogError = '';
      state.optionCatalogLoading = false;
      state.addonCatalog = null;
      state.addonCatalogError = '';
      state.addonCatalogLoading = false;
      state.graphAddons = [];
      state.addonFiles = {};
      state.addonAssetRefs = {};
      state.addonDraft = { moduleId: '', rawNodeClass: '', rawAnchorId: '' };
      state.dependencyHealth = null; state.dependencyHealthError = ''; state.dependencyHealthLoading = false;
      const stages = detail.stages?.stages || [];
      state.stageRunMode = 'all';
      state.stageId = String(stages[0]?.stage_id || '');
      state.enabledStageIds = stages.filter((stage) => stage.enabled_by_default || !stage.optional).map((stage) => String(stage.stage_id || '')).filter(Boolean);
      state.continueFromRunId = '';
      state.variants = []; state.variantError = ''; state.variantLoading = false; state.selectedVariantId = ''; state.variantName = '';
      state.presets = []; state.presetName = ''; state.presetError = ''; state.selectedPresetId = '';
      state.configPanelOpen = false;
      state.preflight = null;
      state.selectedRun = null;
      await loadRuntimeOptions();
      await loadAddonCatalog();
      await loadDependencyHealth();
      await loadVariants();
      await loadPresets();
      const recent = detail.recent_runs || [];
      const activeSummary = recent.find((run) => !isTerminal(run));
      if (activeSummary?.run_id) await attachRun(activeSummary.run_id);
      else if (recent[0]?.run_id && isTerminal(recent[0])) {
        try { state.selectedRun = await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(recent[0].run_id)}`); } catch (_) {}
      }
      if (typeof globalThis.recordMemoryEvent === 'function') globalThis.recordMemoryEvent('space.opened', 'comfy_spaces', { space_id: spaceId, version: detail.space?.version || version });
    } catch (error) {
      state.activeError = error?.message || String(error);
    } finally {
      state.activeLoading = false;
      requestRender();
    }
  }
  function closeSpace() {
    stopRunObservers();
    state.active = null;
    state.activeRun = null;
    state.selectedRun = null;
    state.progress = null;
    state.preflight = null;
    state.optionCatalogs = {};
    state.catalogStates = {};
    state.optionCatalogError = '';
    state.optionCatalogLoading = false;
    state.addonCatalog = null;
    state.addonCatalogError = '';
    state.addonCatalogLoading = false;
    state.graphAddons = [];
    state.addonFiles = {};
    state.addonAssetRefs = {};
    state.addonDraft = { moduleId: '', rawNodeClass: '', rawAnchorId: '' };
    state.dependencyHealth = null; state.dependencyHealthError = ''; state.dependencyHealthLoading = false;
    state.stageRunMode = 'all'; state.stageId = ''; state.enabledStageIds = []; state.continueFromRunId = '';
    state.variants = []; state.variantError = ''; state.variantLoading = false; state.selectedVariantId = ''; state.variantName = '';
    state.presets = []; state.presetName = ''; state.presetError = ''; state.selectedPresetId = '';
    state.actionError = '';
    state.actionMessage = '';
    loadLibrary({ quiet: true }).catch(() => {});
    requestRender();
  }
  function setSearch(payload = {}) { state.search = String(payload.value || ''); requestRender(); }
  function setCategory(payload = {}) { state.category = String(payload.category || 'all'); requestRender(); }
  async function refreshLibrary() { state.library = null; await loadLibrary(); }

  function runtimeHealthSummary(profileId) {
    const rows = state.active?.dependency_health?.items || [];
    return rows.find((item) => String(item.runtime_profile_id || '') === String(profileId || '')) || null;
  }
  function runtimeHealthLabel(profile) {
    const health = runtimeHealthSummary(profile.profile_id);
    if (!health) return profile.connected ? 'runtime online · health unchecked' : 'health unchecked';
    if (health.stale) return `${String(health.status || 'not checked').replaceAll('_',' ')} · stale`;
    if (health.ready) return 'dependencies ready';
    if (health.status === 'blocked') return 'dependencies blocked';
    if (health.status === 'unreachable') return 'runtime unreachable';
    return String(health.status || 'not checked').replaceAll('_',' ');
  }
  function updateActiveHealthSummary(health) {
    if (!state.active || !health?.runtime_profile_id) return;
    const base = health.base || {}; const inventory = health.runtime_inventory || {};
    const summary = {
      runtime_profile_id: health.runtime_profile_id, status: health.status || 'not_checked', ready: !!health.ready, stale: !!health.stale,
      stale_reason: health.stale_reason || '', checked_at: health.checked_at || '', runtime_changed_since_previous: !!health.runtime_changed_since_previous,
      missing_node_count: (base.missing_node_classes || []).length, missing_model_count: (base.missing_models || []).length,
      node_count: Number(inventory.node_count || 0), model_catalog_count: Number(inventory.model_catalog_count || 0),
    };
    const matrix = state.active.dependency_health || { schema_version: 'neo.comfy_space.dependency_health_summary.v1', space_id: state.active?.space?.space_id || '', version: state.active?.space?.version || '', items: [], count: 0 };
    const rows = [...(matrix.items || [])]; const idx = rows.findIndex((item) => String(item.runtime_profile_id || '') === String(summary.runtime_profile_id));
    if (idx >= 0) rows[idx] = summary; else rows.push(summary);
    state.active.dependency_health = { ...matrix, items: rows, count: rows.length };
  }
  function runtimeSelectorHtml() {
    const profiles = state.active?.runtime_profiles || [];
    if (!profiles.length) return '<span class="neo-badge warning">No Comfy runtime configured</span>';
    const options = profiles.map((profile) => `<option value="${attr(profile.profile_id)}" ${profile.profile_id === state.runtimeProfileId ? 'selected' : ''}>${esc(profile.display_name || profile.profile_id)} · ${esc(runtimeHealthLabel(profile))}</option>`).join('');
    if (profiles.length === 1) return `<span class="neo-comfy-runtime-readout">Runtime: <strong>${esc(profiles[0].display_name || profiles[0].profile_id)}</strong> <small>· ${esc(runtimeHealthLabel(profiles[0]))}</small></span>`;
    return `<label class="neo-comfy-runtime-select"><span>Runtime</span><select onchange="neoInvokeSurfaceModule('comfy_spaces','setRuntime',{profileId:this.value})">${options}</select></label>`;
  }
  async function setRuntime(payload = {}) {
    state.runtimeProfileId = String(payload.profileId || '');
    state.preflight = null;
    state.actionError = '';
    state.graphAddons = [];
    state.addonFiles = {};
    state.addonAssetRefs = {};
    state.addonDraft = { moduleId: '', rawNodeClass: '', rawAnchorId: '' };
    await loadRuntimeOptions();
    await loadAddonCatalog();
    await loadDependencyHealth();
    requestRender();
  }
  function rendererInput(payload = {}) {
    if (!payload.inputId) return;
    state.values[payload.inputId] = payload.value;
    if (ASSET_CONTROLS.has(String(payload.control || ''))) {
      const files = globalThis.NeoComfySpaceRenderer?.getAssetFiles?.(payload.rendererId || rendererId(), payload.inputId) || [];
      if (files.length) state.assetFiles[payload.inputId] = files;
      else delete state.assetFiles[payload.inputId];
      delete state.assetRefs[payload.inputId];
    }
  }
  function inputKind(inputId) {
    const item = (state.active?.manifest?.inputs || []).find((row) => row.input_id === inputId) || {};
    const kind = String(item.kind || item.value_type || '').toLowerCase();
    if (['image', 'mask', 'video', 'audio', 'file'].includes(kind)) return kind;
    const control = (state.active?.ui?.inputs || []).find((row) => row.input_id === inputId)?.control || '';
    return ({ image_upload: 'image', mask_upload: 'mask', video_upload: 'video', audio_upload: 'audio', file_upload: 'file' })[control] || 'file';
  }
  async function uploadAsset(file, inputId) {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', inputKind(inputId));
    form.append('retention', 'temporary');
    form.append('label', `${state.active?.space?.name || 'Comfy Space'} · ${inputId}`);
    const response = await jsonRequest('/api/comfy-spaces/assets', { method: 'POST', body: form });
    return response.asset_ref;
  }
  async function runtimeValues() {
    const values = { ...(globalThis.NeoComfySpaceRenderer?.getValues?.(rendererId()) || state.values || {}) };
    for (const input of (state.active?.manifest?.inputs || [])) {
      if (!['image', 'mask', 'video', 'audio', 'file'].includes(input.kind)) continue;
      const files = (state.assetFiles[input.input_id]?.length ? state.assetFiles[input.input_id] : globalThis.NeoComfySpaceRenderer?.getAssetFiles?.(rendererId(), input.input_id)) || [];
      if (!files.length) {
        // Never submit a saved workflow filename or browser-side file metadata.
        // Runtime preflight must report a missing asset, not a false asset reference.
        values[input.input_id] = null;
        continue;
      }
      const inputId = input.input_id;
      let refs = state.assetRefs[inputId];
      if (!Array.isArray(refs) || refs.length !== files.length) {
        refs = [];
        for (const file of files) {
          try { refs.push(await uploadAsset(file, inputId)); }
          catch (error) { throw new Error(`Could not register ${inputId} (${file.name}): ${error.message || error}`); }
        }
        state.assetRefs[inputId] = refs;
      }
      const manifestInput = (state.active?.manifest?.inputs || []).find((item) => item.input_id === inputId) || {};
      values[inputId] = manifestInput.multiplicity === 'multiple' ? refs : (refs.length > 1 ? refs : refs[0]);
    }
    return values;
  }
  function addonModule(moduleId) {
    return (state.addonCatalog?.modules || []).find((item) => item.module_id === moduleId) || null;
  }
  function addonAnchor(anchorId) {
    return (state.addonCatalog?.anchors || []).find((item) => item.anchor_id === anchorId) || null;
  }
  function anchorsForRole(role) {
    return (state.addonCatalog?.anchors || []).filter((item) => item.semantic_role === role && item.attachment_mode === 'insert_between');
  }
  function addonInstanceId(prefix = 'addon') {
    const safe = String(prefix || 'addon').toLowerCase().replace(/[^a-z0-9._-]+/g, '_').replace(/^[_\-.]+|[_\-.]+$/g, '') || 'addon';
    return `${safe}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
  }
  function setAddonDraft(payload = {}) {
    const field = String(payload.field || '');
    if (!field) return;
    state.addonDraft[field] = String(payload.value || '');
    if (field === 'rawNodeClass') {
      const raw = (state.addonCatalog?.raw_nodes || []).find((item) => item.class_type === state.addonDraft.rawNodeClass);
      state.addonDraft.rawAnchorId = String(raw?.placements?.[0]?.anchor_id || '');
    }
    requestRender();
  }
  function addModule(payload = {}) {
    const moduleId = String(payload.moduleId || state.addonDraft.moduleId || '');
    const definition = addonModule(moduleId);
    if (!definition?.compatible) {
      state.actionError = definition?.compatibility_message || 'This Graph Add-on module is not compatible with the selected Space/runtime.';
      requestRender(); return;
    }
    const anchorIds = {};
    for (const role of definition.required_anchor_roles || []) {
      const candidates = anchorsForRole(role);
      anchorIds[role] = candidates.length === 1 ? candidates[0].anchor_id : '';
    }
    const values = {};
    for (const control of definition.controls || []) {
      if (control.kind !== 'asset' && control.default != null) values[control.control_id] = control.default;
    }
    state.graphAddons.push({
      schema_version: 'neo.comfy_space.graph_addon.v1', instance_id: addonInstanceId(moduleId.split('.').pop() || 'module'), lane: 'module',
      module_id: definition.module_id, module_version: definition.version, anchor_ids: anchorIds, values,
    });
    state.actionError = ''; state.preflight = null; requestRender();
  }
  function addRawNode() {
    const classType = String(state.addonDraft.rawNodeClass || '');
    const anchorId = String(state.addonDraft.rawAnchorId || '');
    const raw = (state.addonCatalog?.raw_nodes || []).find((item) => item.class_type === classType);
    const placement = raw?.placements?.find((item) => item.anchor_id === anchorId);
    if (!raw || !placement) { state.actionError = 'Choose a compatible raw Comfy node and confirmed anchor first.'; requestRender(); return; }
    const values = {};
    for (const control of placement.controls || []) if (control.default != null) values[control.control_id] = control.default;
    state.graphAddons.push({
      schema_version: 'neo.comfy_space.graph_addon.v1', instance_id: addonInstanceId(classType), lane: 'raw_node',
      module_id: '', module_version: '', anchor_ids: {}, raw_node_class: classType, raw_anchor_id: anchorId, values,
    });
    state.actionError = ''; state.preflight = null; requestRender();
  }
  function removeAddon(payload = {}) {
    const instanceId = String(payload.instanceId || '');
    state.graphAddons = state.graphAddons.filter((item) => item.instance_id !== instanceId);
    for (const key of Object.keys(state.addonFiles)) if (key.startsWith(`${instanceId}:`)) delete state.addonFiles[key];
    for (const key of Object.keys(state.addonAssetRefs)) if (key.startsWith(`${instanceId}:`)) delete state.addonAssetRefs[key];
    state.preflight = null; requestRender();
  }
  function setAddonAnchor(payload = {}) {
    const item = state.graphAddons.find((row) => row.instance_id === String(payload.instanceId || ''));
    if (!item || item.lane !== 'module') return;
    item.anchor_ids = item.anchor_ids || {};
    item.anchor_ids[String(payload.role || '')] = String(payload.anchorId || '');
    state.preflight = null; requestRender();
  }
  function setAddonValue(payload = {}) {
    const item = state.graphAddons.find((row) => row.instance_id === String(payload.instanceId || ''));
    if (!item) return;
    item.values = item.values || {};
    item.values[String(payload.controlId || '')] = payload.value;
    state.preflight = null;
  }
  function addonFileInput(payload = {}) {
    const instanceId = String(payload.instanceId || '');
    const controlId = String(payload.controlId || '');
    const element = payload.element;
    const file = element?.files?.[0] || null;
    const key = `${instanceId}:${controlId}`;
    if (file) state.addonFiles[key] = { file, kind: String(payload.kind || 'file') };
    else delete state.addonFiles[key];
    delete state.addonAssetRefs[key];
    const item = state.graphAddons.find((row) => row.instance_id === instanceId);
    if (item?.values) delete item.values[controlId];
    state.preflight = null; requestRender();
  }
  async function uploadAddonAsset(entry, instanceId, controlId) {
    const form = new FormData();
    form.append('file', entry.file);
    form.append('kind', entry.kind || 'file');
    form.append('retention', 'temporary');
    form.append('label', `${state.active?.space?.name || 'Comfy Space'} · add-on ${instanceId} · ${controlId}`);
    const response = await jsonRequest('/api/comfy-spaces/assets', { method: 'POST', body: form });
    return response.asset_ref;
  }
  async function runtimeGraphAddons() {
    const rows = state.graphAddons.map((item) => JSON.parse(JSON.stringify(item)));
    for (const item of rows) {
      item.values = item.values || {};
      for (const [key, entry] of Object.entries(state.addonFiles)) {
        const [instanceId, controlId] = key.split(':', 2);
        if (instanceId !== item.instance_id || !entry?.file) continue;
        let ref = state.addonAssetRefs[key];
        if (!ref) {
          ref = await uploadAddonAsset(entry, instanceId, controlId);
          state.addonAssetRefs[key] = ref;
        }
        item.values[controlId] = ref;
      }
    }
    return rows;
  }

  function stageRequestPayload() {
    return {
      stage_run_mode: state.stageRunMode || 'all',
      stage_id: state.stageRunMode === 'all' ? '' : String(state.stageId || ''),
      enabled_stage_ids: state.stageRunMode === 'all' ? [...state.enabledStageIds] : [],
      continue_from_run_id: state.stageRunMode === 'continue' ? String(state.continueFromRunId || '') : '',
    };
  }
  function stageById(stageId) { return (state.active?.stages?.stages || []).find((stage) => stage.stage_id === stageId) || null; }
  function latestCompletedDependencyRun(stage) {
    const required = new Set(stage?.required_stage_ids || []);
    if (!required.size) return '';
    return String((state.active?.recent_runs || []).find((run) => normalizeStatus(run.status) === 'completed' && [...required].every((id) => (run.stage_plan?.selected_stage_ids || []).includes(id)))?.run_id || '');
  }
  function setStageMode(payload = {}) {
    const mode = String(payload.mode || 'all'); const stageId = String(payload.stageId || state.stageId || '');
    state.stageRunMode = ['all','stage','continue'].includes(mode) ? mode : 'all'; state.stageId = stageId;
    state.continueFromRunId = state.stageRunMode === 'continue' ? String(payload.runId || latestCompletedDependencyRun(stageById(stageId)) || '') : '';
    state.preflight = null; state.actionError = ''; requestRender();
  }
  function toggleStageEnabled(payload = {}) {
    const id = String(payload.stageId || ''); const values = new Set(state.enabledStageIds || []);
    if (payload.enabled) values.add(id); else values.delete(id); state.enabledStageIds = [...values]; state.preflight = null; requestRender();
  }
  async function runStageNow(payload = {}) { setStageMode({ mode: 'stage', stageId: payload.stageId }); await generate(); }
  async function continueStageNow(payload = {}) { const stageId = String(payload.stageId || ''); const runId = latestCompletedDependencyRun(stageById(stageId)); setStageMode({ mode: 'continue', stageId, runId }); await generate(); }
  function ensureRunnable() {
    if (!state.active?.space) throw new Error('Open a published Comfy Space first.');
    if (!state.runtimeProfileId) throw new Error('Choose a ComfyUI runtime before generating.');
  }
  function blockInvalidInputRun() {
    const errors = globalThis.NeoComfySpaceRenderer?.getValidationErrors?.(rendererId()) || {};
    if (!Object.keys(errors).length) return false;
    state.actionError = 'Some structured settings contain invalid JSON. Fix them before validating or generating.';
    // Do not rerender here: preserve the user's in-progress JSON for correction.
    const panel = typeof document !== 'undefined' ? document.querySelector?.('.neo-comfy-execution-strip') : null;
    if (panel && !panel.querySelector('.neo-comfy-json-run-error')) {
      panel.insertAdjacentHTML('afterbegin', '<div class="neo-inline-status error neo-comfy-json-run-error" role="alert">Invalid structured setting. Correct the JSON field before running.</div>');
    }
    return true;
  }
  async function validateRun() {
    if (state.busyAction || blockInvalidInputRun()) return;
    state.busyAction = 'validate'; state.actionError = ''; state.actionMessage = ''; requestRender();
    try {
      ensureRunnable();
      const values = await runtimeValues();
      const graphAddons = await runtimeGraphAddons();
      const s = state.active.space;
      state.preflight = await jsonRequest('/api/comfy-spaces/runtime/preflight', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ space_id: s.space_id, version: s.version, runtime_profile_id: state.runtimeProfileId, variant_id: state.selectedVariantId || '', values, graph_addons: graphAddons, ...stageRequestPayload() }) });
      await loadDependencyHealth({ refresh: true });
      state.actionMessage = state.preflight.ready_for_queue ? 'Ready to generate. Dependency health snapshot refreshed.' : 'Validation found blockers. Dependency health was refreshed; open Details for more information.';
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }
  async function generate() {
    if (state.busyAction || (state.activeRun && !isTerminal(state.activeRun)) || blockInvalidInputRun()) return;
    state.busyAction = 'generate'; state.actionError = ''; state.actionMessage = ''; requestRender();
    try {
      ensureRunnable();
      const values = await runtimeValues();
      const graphAddons = await runtimeGraphAddons();
      const s = state.active.space;
      const run = await jsonRequest('/api/comfy-spaces/runtime/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ space_id: s.space_id, version: s.version, runtime_profile_id: state.runtimeProfileId, variant_id: state.selectedVariantId || '', values, graph_addons: graphAddons, ...stageRequestPayload() }) });
      state.activeRun = run;
      state.selectedRun = run;
      state.progress = run.progress || null;
      state.preflight = null;
      state.actionMessage = run.message || 'Generation queued.';
      observeRun(run.run_id);
      if (typeof globalThis.recordMemoryEvent === 'function') globalThis.recordMemoryEvent('space.run.started', 'comfy_spaces', { space_id: s.space_id, version: s.version, run_id: run.run_id });
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }
  async function stopRun() {
    const runId = state.activeRun?.run_id;
    if (!runId || isTerminal(state.activeRun) || state.busyAction) return;
    state.busyAction = 'stop'; state.actionError = ''; requestRender();
    try {
      state.activeRun = await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
      state.actionMessage = state.activeRun.message || 'Stop requested.';
      if (typeof globalThis.recordMemoryEvent === 'function') globalThis.recordMemoryEvent('space.run.cancelled', 'comfy_spaces', { run_id: runId, space_id: state.active?.space?.space_id });
    } catch (error) { state.actionError = error.message || String(error); }
    finally { state.busyAction = ''; requestRender(); }
  }
  function pauseRun() {
    state.actionMessage = 'Pause is not supported by the current Comfy Spaces execution contract. Stop is available.';
    requestRender();
  }

  function stopRunObservers() {
    if (state.socket) { try { state.socket.close(); } catch (_) {} state.socket = null; }
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }
  async function refreshRun(runId, { forcePoll = false } = {}) {
    if (!runId) return;
    try {
      const run = forcePoll
        ? await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}/poll`, { method: 'POST' })
        : await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}`);
      state.activeRun = run;
      state.selectedRun = run;
      if (run.progress) state.progress = run.progress;
      if (isTerminal(run)) {
        stopRunObservers();
        state.actionMessage = run.message || statusLabel(run);
        if (normalizeStatus(run.status) === 'completed' && typeof globalThis.recordMemoryEvent === 'function') globalThis.recordMemoryEvent('space.run.completed', 'comfy_spaces', { run_id: run.run_id, space_id: run.space_id });
        loadLibrary({ quiet: true }).catch(() => {});
      }
      requestRender();
    } catch (error) {
      state.actionError = `Run status could not be refreshed: ${error.message}`;
      requestRender();
    }
  }
  async function attachRun(runId) {
    if (!runId) return;
    try {
      const run = await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}`);
      state.activeRun = run;
      state.selectedRun = run;
      state.progress = run.progress || null;
      if (!isTerminal(run)) observeRun(runId);
    } catch (error) { state.actionError = `Could not restore run: ${error.message}`; }
  }
  function observeRun(runId) {
    stopRunObservers();
    if (!runId) return;
    try {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${protocol}//${location.host}/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}/progress/ws`);
      state.socket = ws;
      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data || '{}');
          if (message.type === 'space_run_progress') {
            state.progress = message.data;
            if (message.data?.status && state.activeRun) state.activeRun.status = message.data.status;
            requestRender();
            if (message.data?.terminal) refreshRun(runId).catch(() => {});
          }
        } catch (_) {}
      };
      ws.onclose = () => { if (state.socket === ws) state.socket = null; };
    } catch (_) {}
    state.pollTimer = setInterval(() => refreshRun(runId, { forcePoll: true }).catch(() => {}), 2500);
  }
  async function viewRun(payload = {}) {
    const runId = String(payload.runId || '');
    if (!runId) return;
    state.actionError = '';
    try {
      const run = await jsonRequest(`/api/comfy-spaces/runtime/runs/${encodeURIComponent(runId)}`);
      state.selectedRun = run;
      if (!isTerminal(run)) { state.activeRun = run; state.progress = run.progress || null; observeRun(runId); }
      requestRender();
    } catch (error) { state.actionError = error.message || String(error); requestRender(); }
  }

  function progressDetailsHtml(run) {
    const wrapper = state.progress || run?.progress || {};
    const p = wrapper.progress || wrapper || {};
    const nodeTitle = p.current_node_title || p.current_node_class || '';
    const nodeId = p.current_node_id || '';
    const step = p.node_step ?? p.step ?? p.value;
    const max = p.node_step_max ?? p.total_steps ?? p.max;
    const rows = [];
    if (nodeTitle) rows.push(`<div><span>Current stage</span><strong>${esc(nodeTitle)}</strong></div>`);
    if (step != null && max != null) rows.push(`<div><span>Sampler</span><strong>${esc(step)} / ${esc(max)} steps</strong></div>`);
    const completedCount = Array.isArray(p.completed_node_ids) ? p.completed_node_ids.length : p.completed_node_count;
    const nodeCount = p.node_count ?? p.total_node_count;
    if (completedCount != null && nodeCount != null) rows.push(`<div><span>Graph</span><strong>${esc(completedCount)} / ${esc(nodeCount)} nodes</strong></div>`);
    if (!rows.length) rows.push(`<div><span>Status</span><strong>${esc(runStage(wrapper, run))}</strong></div>`);
    return `<details class="neo-comfy-run-details"><summary>Details</summary><div class="neo-comfy-run-detail-grid">${rows.join('')}</div>${nodeId ? `<small class="neo-muted">Graph node: ${esc(nodeId)}</small>` : ''}${state.preflight ? `<pre>${esc(JSON.stringify(state.preflight, null, 2))}</pre>` : ''}</details>`;
  }
  function addonControlsFor(item) {
    if (item.lane === 'module') return addonModule(item.module_id)?.controls || [];
    const raw = (state.addonCatalog?.raw_nodes || []).find((row) => row.class_type === item.raw_node_class);
    return raw?.placements?.find((row) => row.anchor_id === item.raw_anchor_id)?.controls || [];
  }
  function addonControlHtml(item, control) {
    const id = String(control.control_id || '');
    const value = item.values?.[id] ?? control.default ?? '';
    const disabled = state.busyAction || (state.activeRun && !isTerminal(state.activeRun));
    const common = `data-addon-instance="${attr(item.instance_id)}" data-addon-control="${attr(id)}"`;
    if (control.kind === 'asset') {
      const key = `${item.instance_id}:${id}`;
      const selected = state.addonFiles[key]?.file?.name || state.addonAssetRefs[key]?.asset_id || item.values?.[id]?.asset_id || '';
      return `<label class="neo-comfy-addon-field"><span>${esc(control.label || id)}${control.required ? ' *' : ''}</span><input type="file" accept="${control.asset_kind === 'image' ? 'image/*' : control.asset_kind === 'video' ? 'video/*' : control.asset_kind === 'audio' ? 'audio/*' : '*/*'}" ${common} onchange="neoInvokeSurfaceModule('comfy_spaces','addonFileInput',{instanceId:this.dataset.addonInstance,controlId:this.dataset.addonControl,kind:'${attr(control.asset_kind || 'file')}',element:this})" ${disabled ? 'disabled' : ''}>${selected ? `<small>${esc(selected)}</small>` : '<small>Upload for this run only.</small>'}</label>`;
    }
    if (control.kind === 'select') {
      const options = (control.options || []).map((option) => `<option value="${attr(option)}" ${String(option) === String(value) ? 'selected' : ''}>${esc(option)}</option>`).join('');
      return `<label class="neo-comfy-addon-field"><span>${esc(control.label || id)}${control.required ? ' *' : ''}</span><select ${common} onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonValue',{instanceId:this.dataset.addonInstance,controlId:this.dataset.addonControl,value:this.value})" ${disabled ? 'disabled' : ''}>${options}</select></label>`;
    }
    if (control.kind === 'toggle') {
      return `<label class="neo-comfy-addon-field toggle"><span>${esc(control.label || id)}</span><input type="checkbox" ${common} ${value ? 'checked' : ''} onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonValue',{instanceId:this.dataset.addonInstance,controlId:this.dataset.addonControl,value:this.checked})" ${disabled ? 'disabled' : ''}></label>`;
    }
    const type = control.kind === 'integer' || control.kind === 'number' ? 'number' : 'text';
    const step = control.step != null ? ` step="${attr(control.step)}"` : (control.kind === 'number' ? ' step="any"' : '');
    const min = control.minimum != null ? ` min="${attr(control.minimum)}"` : '';
    const max = control.maximum != null ? ` max="${attr(control.maximum)}"` : '';
    return `<label class="neo-comfy-addon-field"><span>${esc(control.label || id)}${control.required ? ' *' : ''}</span><input type="${type}" value="${attr(value)}"${step}${min}${max} ${common} onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonValue',{instanceId:this.dataset.addonInstance,controlId:this.dataset.addonControl,value:this.value})" ${disabled ? 'disabled' : ''}></label>`;
  }
  function addonAnchorLabel(anchor) {
    if (!anchor) return 'Unknown anchor';
    const edge = anchor.target_label ? `${anchor.source_label} → ${anchor.target_label}.${anchor.target_input_field || ''}` : anchor.source_label;
    const path = (anchor.source_subgraph_path || []).length ? ` · ${anchor.source_subgraph_path.join(' / ')}` : '';
    return `${edge}${path}`;
  }
  function addonCardHtml(item) {
    const definition = item.lane === 'module' ? addonModule(item.module_id) : null;
    const title = item.lane === 'module' ? (definition?.label || item.module_id) : (state.addonCatalog?.raw_nodes || []).find((row) => row.class_type === item.raw_node_class)?.display_name || item.raw_node_class;
    const anchorRows = item.lane === 'module' ? (definition?.required_anchor_roles || []).map((role) => {
      const candidates = anchorsForRole(role);
      const options = [`<option value="">Choose ${esc(role.replaceAll('_', ' ').toLowerCase())}…</option>`, ...candidates.map((anchor) => `<option value="${attr(anchor.anchor_id)}" ${item.anchor_ids?.[role] === anchor.anchor_id ? 'selected' : ''}>${esc(addonAnchorLabel(anchor))}</option>`)].join('');
      return `<label class="neo-comfy-addon-field"><span>${esc(role.replaceAll('_', ' '))} anchor *</span><select data-addon-instance="${attr(item.instance_id)}" data-addon-role="${attr(role)}" onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonAnchor',{instanceId:this.dataset.addonInstance,role:this.dataset.addonRole,anchorId:this.value})">${options}</select></label>`;
    }).join('') : `<div class="neo-comfy-addon-anchor-readout"><span>Anchor</span><strong>${esc(addonAnchorLabel(addonAnchor(item.raw_anchor_id)))}</strong></div>`;
    const controls = addonControlsFor(item).map((control) => addonControlHtml(item, control)).join('');
    return `<article class="neo-comfy-addon-card"><header><div><span class="neo-kicker">${item.lane === 'module' ? 'MODULE' : 'RAW COMFY NODE'}</span><h4>${esc(title)}</h4><p>${esc(item.lane === 'module' ? (definition?.description || '') : 'Safe single typed transform discovered from this runtime’s /object_info.')}</p></div><button type="button" class="neo-icon-btn" title="Remove add-on" data-addon-instance="${attr(item.instance_id)}" onclick="neoInvokeSurfaceModule('comfy_spaces','removeAddon',{instanceId:this.dataset.addonInstance})">×</button></header><div class="neo-comfy-addon-fields">${anchorRows}${controls}</div><footer>Per-run only · published workflow remains unchanged</footer></article>`;
  }
  function presetsHtml() {
    const options = ['<option value="">Choose a Preset</option>', ...state.presets.map((preset) => `<option value="${attr(preset.preset_id)}" ${preset.ready ? '' : 'disabled'}>${esc(preset.name)}${preset.ready ? '' : ' · stale'}</option>`)].join('');
    const selected = state.presets.find((preset) => preset.preset_id === state.selectedPresetId);
    return `<section class="neo-comfy-variants-panel neo-comfy-config-card"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">PRESETS</span><h3>Input presets</h3><p>Restore saved input values without changing nodes, stages or uploaded media.</p></div><span class="neo-badge">${state.presets.length} saved</span></div>
      ${state.presetError ? `<div class="neo-inline-status warning">${esc(state.presetError)}</div>` : ''}
      <div class="neo-comfy-variant-toolbar"><label><span>Apply Preset</span><select onchange="neoInvokeSurfaceModule('comfy_spaces','applyPreset',{presetId:this.value})">${options}</select></label>${selected ? `<button class="neo-btn compact danger" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','deletePreset',{presetId:'${attr(selected.preset_id)}'})">Delete</button>` : ''}</div>
      <div class="neo-comfy-variant-save"><input type="text" maxlength="120" value="${attr(state.presetName)}" placeholder="Preset name" oninput="neoInvokeSurfaceModule('comfy_spaces','setPresetName',{value:this.value})"><button class="neo-btn" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','savePreset')" ${state.busyAction ? 'disabled' : ''}>Save input Preset</button></div>
      <small class="neo-muted">Only non-media input values are saved. Presets pin the exact published Space version and fail closed if it changes.</small></section>`;
  }

  function variantsHtml() {
    const options = [`<option value="">Base Space · no Variant</option>`, ...(state.variants || []).map((variant) => `<option value="${attr(variant.variant_id)}" ${variant.variant_id === state.selectedVariantId ? 'selected' : ''} ${variant.ready ? '' : 'disabled'}>${esc(variant.name)}${variant.ready ? '' : ' · stale'}</option>`)].join('');
    const selected = (state.variants || []).find((item) => item.variant_id === state.selectedVariantId);
    const status = selected ? `<span class="neo-badge ${selected.ready ? 'success' : 'warning'}">${selected.ready ? (selected.graph_addon_count ? `${esc(selected.graph_addon_count)} add-on${selected.graph_addon_count === 1 ? '' : 's'}` : 'Values/stages') : 'Stale'}</span>` : '<span class="neo-badge">Base Space</span>';
    return `<section class="neo-comfy-variants-panel neo-comfy-config-card"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">VARIANTS</span><h3>Space variants</h3><p>Reuse approved Graph Add-ons and stage settings, optionally with input values.</p></div>${status}</div>
      ${state.variantError ? `<div class="neo-inline-status warning">${esc(state.variantError)}</div>` : ''}
      <div class="neo-comfy-variant-toolbar"><label><span>Use Variant</span><select onchange="neoInvokeSurfaceModule('comfy_spaces','applyVariant',{variantId:this.value})" ${state.variantLoading || state.busyAction ? 'disabled' : ''}>${options}</select></label>${selected ? `<button class="neo-btn compact danger" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','deleteVariant',{variantId:'${attr(selected.variant_id)}'})" ${state.busyAction ? 'disabled' : ''}>Delete</button>` : ''}</div>
      ${selected && !selected.ready ? `<div class="neo-inline-status warning"><strong>Variant blocked</strong><span>${esc(selected.reason || 'The exact base Space version is no longer available.')}</span></div>` : ''}
      <div class="neo-comfy-variant-save"><input type="text" value="${attr(state.variantName)}" maxlength="120" placeholder="Variant name" oninput="neoInvokeSurfaceModule('comfy_spaces','setVariantName',{value:this.value})"><label class="neo-ui-check-card"><input type="checkbox" ${state.saveVariantValues ? 'checked' : ''} onchange="neoInvokeSurfaceModule('comfy_spaces','setSaveVariantValues',{enabled:this.checked})"> Save current non-media values</label><button class="neo-btn" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','saveVariant')" ${state.busyAction ? 'disabled' : ''}>Save as Variant</button></div>
      <small class="neo-muted">Images, masks, video, audio, files, and per-run add-on asset uploads are never persisted in a Variant.</small></section>`;
  }

  function savedConfigurationsHtml() {
    const count = (state.variants || []).length + (state.presets || []).length;
    const selected = (state.variants || []).find((item) => item.variant_id === state.selectedVariantId);
    const activeText = selected ? `Variant: ${selected.name}` : 'Base Space';
    return `<details class="neo-comfy-config-hub" ${state.configPanelOpen ? 'open' : ''} ontoggle="neoInvokeSurfaceModule('comfy_spaces','setConfigPanelOpen',{open:this.open})">
      <summary class="neo-comfy-config-summary"><span class="neo-comfy-config-glyph" aria-hidden="true">◈</span><span class="neo-comfy-config-title"><span class="neo-kicker">REUSABLE SETTINGS</span><strong>Configurations</strong><small>${esc(activeText)} · ${count} saved configuration${count === 1 ? '' : 's'}</small></span><span class="neo-comfy-config-chevron" aria-hidden="true">⌄</span></summary>
      <div class="neo-comfy-config-grid">${variantsHtml()}${presetsHtml()}</div>
    </details>`;
  }

  function graphAddonsHtml() {
    const catalog = state.addonCatalog;
    const running = !!state.activeRun && !isTerminal(state.activeRun);
    if (state.addonCatalogLoading && !catalog) return `<section class="neo-comfy-addons-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">GRAPH ADD-ONS</span><h3>Loading runtime node catalog…</h3></div></div></section>`;
    if (state.addonCatalogError) return `<section class="neo-comfy-addons-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">GRAPH ADD-ONS</span><h3>Per-run graph additions</h3><p>Unavailable until Neo can verify confirmed anchors against this runtime.</p></div></div><div class="neo-inline-status warning">${esc(state.addonCatalogError)}</div></section>`;
    if (!catalog) return '';
    const modules = catalog.modules || [];
    const moduleOptions = modules.map((item) => `<option value="${attr(item.module_id)}" ${state.addonDraft.moduleId === item.module_id ? 'selected' : ''}>${esc(item.label)}${item.compatible ? '' : ' · unavailable'}</option>`).join('');
    const selectedModule = modules.find((item) => item.module_id === state.addonDraft.moduleId) || modules[0];
    const rawNodes = catalog.raw_nodes || [];
    const rawOptions = rawNodes.map((item) => `<option value="${attr(item.class_type)}" ${state.addonDraft.rawNodeClass === item.class_type ? 'selected' : ''}>${esc(item.display_name || item.class_type)}</option>`).join('');
    const selectedRaw = rawNodes.find((item) => item.class_type === state.addonDraft.rawNodeClass) || rawNodes[0];
    const rawAnchorOptions = (selectedRaw?.placements || []).map((placement) => {
      const anchor = addonAnchor(placement.anchor_id);
      return `<option value="${attr(placement.anchor_id)}" ${state.addonDraft.rawAnchorId === placement.anchor_id ? 'selected' : ''}>${esc(placement.semantic_role)} · ${esc(addonAnchorLabel(anchor))}</option>`;
    }).join('');
    const cards = state.graphAddons.length ? state.graphAddons.map(addonCardHtml).join('') : '<div class="neo-empty compact">No graph changes selected. This run will use the published Space exactly as-is.</div>';
    return `<section class="neo-comfy-addons-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">GRAPH ADD-ONS</span><h3>Per-run graph additions</h3><p>Plug real Comfy nodes into author-confirmed anchors for this run only. Add-ons never rewrite the published Space.</p></div><span class="neo-badge ${state.graphAddons.length ? 'success' : ''}">${esc(state.graphAddons.length)} selected</span></div>
      <div class="neo-comfy-addon-pickers"><div class="neo-comfy-addon-picker"><strong>Add module</strong><div class="neo-comfy-addon-picker-row"><select onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonDraft',{field:'moduleId',value:this.value})" ${running ? 'disabled' : ''}>${moduleOptions}</select><button class="neo-btn" type="button" data-module-id="${attr(selectedModule?.module_id || '')}" onclick="neoInvokeSurfaceModule('comfy_spaces','addModule',{moduleId:this.dataset.moduleId})" ${!selectedModule?.compatible || running ? 'disabled' : ''}>Add Module</button></div><small>${esc(selectedModule?.compatibility_message || 'No reviewed module is available on this runtime.')}</small></div>
      <details class="neo-comfy-addon-picker advanced"><summary>Advanced · Add Raw Comfy Node</summary><p>Neo only lists nodes that can be proven as a single typed transform at a confirmed anchor. Complex multi-input nodes are intentionally excluded.</p>${rawNodes.length ? `<div class="neo-comfy-addon-picker-row"><select onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonDraft',{field:'rawNodeClass',value:this.value})" ${running ? 'disabled' : ''}>${rawOptions}</select><select onchange="neoInvokeSurfaceModule('comfy_spaces','setAddonDraft',{field:'rawAnchorId',value:this.value})" ${running ? 'disabled' : ''}>${rawAnchorOptions}</select><button class="neo-btn" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','addRawNode')" ${!state.addonDraft.rawAnchorId || running ? 'disabled' : ''}>Add Node</button></div>` : '<div class="neo-empty compact">No safe raw single-transform nodes were found for the confirmed anchors on this runtime.</div>'}</details></div>
      <div class="neo-comfy-addon-list">${cards}</div><div class="neo-comfy-addon-policy">One add-on may claim a confirmed anchor per run in NCS-10B. Conflicts fail validation instead of Neo guessing an execution order.</div></section>`;
  }

  function stageExecutionHtml() {
    const stages = [...(state.active?.stages?.stages || [])].sort((a,b)=>(a.order||0)-(b.order||0));
    if (!stages.length) return '';
    const running = !!state.activeRun && !isTerminal(state.activeRun);
    const cards = stages.map((stage, index) => {
      const enabled = state.enabledStageIds.includes(stage.stage_id); const predecessor = latestCompletedDependencyRun(stage);
      const deps = (stage.required_stage_ids || []).map((id) => stageById(id)?.label || id).join(', ');
      const targetLabels = (stage.target_output_ids || []).join(', ');
      return `<article class="neo-comfy-stage-card ${state.stageId === stage.stage_id && state.stageRunMode !== 'all' ? 'active' : ''}"><header><span class="neo-comfy-stage-index">${index + 1}</span><div><strong>${esc(stage.label || stage.stage_id)}</strong><small>${deps ? `Requires ${esc(deps)} · ` : ''}${esc(targetLabels || 'No targets')}</small></div><label class="neo-ui-check-card"><input type="checkbox" ${enabled ? 'checked' : ''} ${!stage.optional || running ? 'disabled' : ''} onchange="neoInvokeSurfaceModule('comfy_spaces','toggleStageEnabled',{stageId:'${attr(stage.stage_id)}',enabled:this.checked})"> ${stage.optional ? 'Enabled' : 'Required'}</label></header><p>${esc(stage.description || 'Explicit execution stage')}</p><footer><button class="neo-btn compact" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','runStageNow',{stageId:'${attr(stage.stage_id)}'})" ${running ? 'disabled' : ''}>Run Stage</button>${(stage.required_stage_ids || []).length ? `<button class="neo-btn compact primary" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','continueStageNow',{stageId:'${attr(stage.stage_id)}'})" ${!predecessor || running ? 'disabled' : ''}>Continue</button>` : ''}<span class="neo-badge">${esc((stage.resource_policy || 'automatic').replaceAll('_',' '))}</span></footer></article>`;
    }).join('');
    return `<section class="neo-comfy-stages-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">PIPELINE</span><h3>Execution stages</h3><p>Run the whole configured pipeline or execute one stage boundary. Continue uses a completed predecessor run on this runtime and lets Comfy reuse valid cache.</p></div><button class="neo-btn ${state.stageRunMode === 'all' ? 'primary' : ''}" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','setStageMode',{mode:'all'})" ${running ? 'disabled' : ''}>Run All</button></div><div class="neo-comfy-stage-list">${cards}</div></section>`;
  }
  function dependencyHealthHtml() {
    const h = state.dependencyHealth;
    const running = !!state.activeRun && !isTerminal(state.activeRun);
    if (state.dependencyHealthLoading && !h) return `<section class="neo-comfy-health-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">DEPENDENCIES</span><h3>Checking runtime health…</h3></div></div></section>`;
    if (state.dependencyHealthError) return `<section class="neo-comfy-health-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">DEPENDENCIES</span><h3>Runtime health unavailable</h3><p>${esc(state.dependencyHealthError)}</p></div><button class="neo-btn compact" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','refreshDependencyHealth')" ${running || state.busyAction ? 'disabled' : ''}>Recheck</button></div></section>`;
    if (!h) return '';
    const status = String(h.status || 'not_checked'); const stale = !!h.stale;
    const tone = status === 'ready' && !stale ? 'success' : ['blocked','unreachable'].includes(status) ? 'warning' : '';
    const base = h.base || {}; const missingNodes = base.missing_node_classes || []; const missingModels = base.missing_models || [];
    const stageRows = (h.stages || []).map((item) => `<div class="neo-comfy-health-row"><span>${esc(item.label || item.stage_id)}</span><strong class="${item.ready ? 'ok' : 'bad'}">${esc(item.status || 'unknown')}</strong><small>${esc((item.missing_node_classes || []).length ? `${item.missing_node_classes.length} missing nodes` : (item.missing_models || []).length ? `${item.missing_models.length} missing models` : `${item.node_count || 0} nodes`)}</small></div>`).join('');
    const addonRows = (h.addons?.modules || []).map((item) => `<div class="neo-comfy-health-row"><span>${esc(item.label || item.module_id)}</span><strong class="${item.ready ? 'ok' : 'bad'}">${item.ready ? 'ready' : 'unavailable'}</strong><small>${esc(item.message || '')}</small></div>`).join('');
    const packageHints = (h.package_hints || []).filter((item) => item.package).slice(0, 6).map((item) => `<span class="neo-badge">${esc(item.class_type)} · ${esc(item.package)}</span>`).join('');
    return `<section class="neo-comfy-health-panel"><div class="neo-comfy-addons-head"><div><span class="neo-kicker">DEPENDENCY HEALTH</span><h3>Selected runtime · ${esc(status.replaceAll('_',' '))}${stale ? ' · stale' : ''}</h3><p>Persistent compatibility evidence for this exact Space version and runtime. Generate still performs a fresh queue-time preflight.</p></div><div class="neo-comfy-health-actions"><span class="neo-badge ${tone}">${esc(status)}</span><button class="neo-btn compact" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','refreshDependencyHealth')" ${running || state.busyAction ? 'disabled' : ''}>Refresh Health</button></div></div>
      ${h.runtime_changed_since_previous ? '<div class="neo-inline-status warning">This runtime inventory changed since the previous health check. Review node/model readiness before running.</div>' : ''}
      <div class="neo-comfy-health-summary"><div><span>Base workflow</span><strong>${base.ready ? 'Ready' : 'Needs attention'}</strong></div><div><span>Missing nodes</span><strong>${esc(missingNodes.length)}</strong></div><div><span>Missing models</span><strong>${esc(missingModels.length)}</strong></div><div><span>Checked</span><strong>${esc(h.checked_at || 'Never')}</strong></div></div>
      ${missingNodes.length ? `<details><summary>Missing node classes</summary><div class="neo-badge-row">${missingNodes.map((item) => `<span class="neo-badge warning">${esc(item)}</span>`).join('')}</div></details>` : ''}
      ${missingModels.length ? `<details><summary>Missing models</summary><div class="neo-comfy-health-list">${missingModels.map((item) => `<div class="neo-comfy-health-row"><span>${esc(item.model_type || 'model')}</span><strong>${esc(item.configured_name || '')}</strong><small>${esc(item.class_type || '')}</small></div>`).join('')}</div></details>` : ''}
      ${packageHints ? `<details><summary>Node package hints</summary><div class="neo-badge-row">${packageHints}</div><small>Hints only. Neo never installs Comfy custom nodes automatically.</small></details>` : ''}
      ${stageRows ? `<details><summary>Stage readiness</summary><div class="neo-comfy-health-list">${stageRows}</div></details>` : ''}
      ${addonRows ? `<details><summary>Graph Add-on readiness</summary><div class="neo-comfy-health-list">${addonRows}</div></details>` : ''}
    </section>`;
  }

  function executionStripHtml() {
    const run = state.activeRun;
    const running = !!run && !isTerminal(run);
    const pct = runProgressPercent(state.progress?.progress ? { progress: state.progress.progress } : run);
    const status = running ? statusLabel(run) : (run ? statusLabel(run) : 'Idle');
    const generateDisabled = !state.runtimeProfileId || !!state.busyAction || running;
    const stopDisabled = !running || !!state.busyAction;
    const validateDisabled = !state.runtimeProfileId || !!state.busyAction || running;
    const stage = running ? runStage(state.progress, run) : (run?.message || '');
    return `<section class="neo-comfy-execution-strip">
      <div class="neo-comfy-execution-top"><div class="neo-workspace-actions neo-run-actions">
        <button class="neo-btn" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','validateRun')" ${validateDisabled ? 'disabled' : ''}>Validate</button>
        <button class="neo-btn primary" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','generate')" ${generateDisabled ? 'disabled' : ''}>Generate</button>
        <button class="neo-btn" type="button" title="Pause is not supported by this runtime yet" onclick="neoInvokeSurfaceModule('comfy_spaces','pauseRun')" disabled aria-disabled="true">Pause</button>
        <button class="neo-btn danger" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','stopRun')" ${stopDisabled ? 'disabled' : ''}>Stop</button>
      </div>${runtimeSelectorHtml()}</div>
      ${state.actionError ? `<div class="neo-inline-status error"><strong>Generation issue</strong><span>${esc(state.actionError)}</span></div>` : ''}
      ${state.actionMessage ? `<div class="neo-inline-status ${state.preflight?.ready_for_queue === false ? 'warning' : 'muted'}"><span>${esc(state.actionMessage)}</span></div>` : ''}
      <div class="neo-progress-wrap"><div class="neo-progress-meta"><span>Progress${stage ? ` · ${esc(stage)}` : ''}</span><strong>${esc(status)}</strong></div><div class="neo-progress-track"><span style="width:${running ? Math.max(2, pct).toFixed(1) : (normalizeStatus(run?.status) === 'completed' ? '100' : '0')}%"></span></div></div>
      ${(run || state.preflight) ? progressDetailsHtml(run) : ''}
    </section>`;
  }

  function historyHtml() {
    const rows = state.active?.recent_runs || [];
    if (!rows.length) return '<div class="neo-empty compact">No runs yet.</div>';
    return `<div class="neo-comfy-run-history">${rows.slice(0, 12).map((run) => `<button type="button" class="neo-comfy-run-row" data-run-id="${attr(run.run_id)}" onclick="neoInvokeSurfaceModule('comfy_spaces','viewRun',{runId:this.dataset.runId})"><span><strong>${esc(statusLabel(run))}</strong><small>${esc(run.message || run.run_id)}</small></span><span>${esc(run.updated_at || '')}</span></button>`).join('')}</div>`;
  }
  function runnerHtml() {
    if (state.activeLoading) return '<div class="neo-comfy-loading">Opening Comfy Space…</div>';
    if (state.activeError) return `<div class="neo-inline-status error"><strong>Could not open Space.</strong><p>${esc(state.activeError)}</p><button class="neo-btn" onclick="neoInvokeSurfaceModule('comfy_spaces','closeSpace')">Back to library</button></div>`;
    const detail = state.active;
    if (!detail?.space) return libraryHtml();
    const space = detail.space;
    const tags = [space.category, ...(space.tags || [])].filter(Boolean).map((tag) => `<span class="neo-badge">${esc(tag)}</span>`).join('');
    const results = state.selectedRun?.results || state.activeRun?.results || {};
    const renderer = globalThis.NeoComfySpaceRenderer?.render ? globalThis.NeoComfySpaceRenderer.render({
      rendererId: rendererId(), manifest: detail.manifest, ui: detail.ui, dynamicControls: detail.dynamic_controls || null,
      dynamicContext: { enabledStageIds: state.enabledStageIds || [], addonModuleIds: (state.graphAddons || []).map((item) => item.module_id).filter(Boolean), variantActive: Boolean(state.selectedVariantId) },
      mode: 'runtime', values: state.values, results, optionCatalogs: state.optionCatalogs, catalogStates: state.catalogStates, optionCatalogError: state.optionCatalogError, optionCatalogLoading: state.optionCatalogLoading, assetControlsEnabled: true, disabled: !!state.busyAction,
      showHeader: false, layoutMode: 'split', submit: { visible: false, enabled: false },
      bridge: { surfaceId: SURFACE_ID, inputAction: 'rendererInput' },
    }) : '<div class="neo-inline-status error">Shared Comfy Space renderer is unavailable.</div>';
    return `<section class="neo-comfy-space-runner">
      <header class="neo-comfy-runner-head"><div class="neo-comfy-runner-title"><button class="neo-btn compact" type="button" onclick="neoInvokeSurfaceModule('comfy_spaces','closeSpace')">← Spaces</button><div><span class="neo-kicker">COMFY SPACE</span><h2>${esc(space.name)}</h2><p>${esc(space.description || '')}</p><div class="neo-badge-row">${tags}<span class="neo-badge success">Published · v${esc(space.version)}</span></div></div></div><div class="neo-comfy-ready-pill">${!state.runtimeProfileId ? 'Runtime needed' : state.dependencyHealth?.ready && !state.dependencyHealth?.stale ? 'Runtime healthy' : state.dependencyHealth?.status === 'blocked' ? 'Dependencies blocked' : 'Ready to validate'}</div></header>
      ${savedConfigurationsHtml()}
      <div class="neo-comfy-runner-renderer">${renderer}</div>
      ${dependencyHealthHtml()}
      ${stageExecutionHtml()}
      ${graphAddonsHtml()}
      ${executionStripHtml()}
      <section class="neo-comfy-history-panel"><div class="neo-section-title-row"><div><span class="neo-kicker">RUNS</span><h3>Recent runs</h3></div><button class="neo-btn compact" onclick="neoInvokeSurfaceModule('comfy_spaces','refreshActive')">Refresh</button></div>${historyHtml()}</section>
    </section>`;
  }

  async function refreshActive() {
    const s = state.active?.space;
    if (!s) return;
    await openSpace({ spaceId: s.space_id, version: s.version });
  }
  function openBuilder() {
    const ctx = state.lastContext;
    if (ctx?.state) {
      ctx.state.activeSurfaceId = 'admin';
      ctx.state.activeSubtabId = 'comfy_spaces';
      try { localStorage.setItem('neo.active.surface', 'admin'); localStorage.setItem('neo.active.subtab.admin', 'comfy_spaces'); } catch (_) {}
      ctx.render?.();
    }
  }
  function workspaceHtml(context = {}) {
    state.lastContext = context;
    return state.active ? runnerHtml() : libraryHtml();
  }

  const api = {
    surfaceId: SURFACE_ID,
    releaseStage: 'active',
    status: 'ready',
    architectureContract: 'neo.comfy_space.surface.v1',
    policy: 'Published Comfy Spaces are native Neo mini-apps. NCS-12 persists selected-runtime dependency health as advisory evidence while live queue-time preflight remains authoritative; Neo never auto-installs Comfy dependencies.',
    migratedAreas: ['published_library', 'space_runner', 'shared_renderer', 'asset_handoff', 'execution_controls', 'live_progress', 'run_history', 'live_runtime_catalog_controls', 'per_run_graph_addons', 'stage_aware_execution', 'space_variants', 'space_presets', 'portable_spaces', 'conditional_dynamic_controls', 'dependency_health'],
    diagnostics: { public_runner_mounted: true, generic_ui_presets: false, space_presets: true, pause_supported: false, shared_renderer: true, live_runtime_catalog_controls: true, per_run_graph_addons: true, stage_aware_execution: true, space_variants: true, conditional_dynamic_controls: true, dependency_health: true, published_workflow_mutated: false },
    renderers: { workspaceHtml },
    actions: {
      importPortable, openSpace, closeSpace, refreshLibrary, refreshActive, setSearch, setCategory, toggleFavorite, setRuntime,
      rendererInput, validateRun, generate, pauseRun, stopRun, viewRun, openBuilder,
      setAddonDraft, addModule, addRawNode, removeAddon, setAddonAnchor, setAddonValue, addonFileInput,
      setStageMode, toggleStageEnabled, runStageNow, continueStageNow,
      setConfigPanelOpen, applyPreset, setPresetName, savePreset, deletePreset, applyVariant, setVariantName, setSaveVariantValues, saveVariant, deleteVariant, refreshDependencyHealth,
    },
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register(SURFACE_ID, api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules[SURFACE_ID] = api;
  }
})();
