// Admin surface module
(function () {
  const runtime = window.NeoSurfaceRuntime;

  function ensureAdminState(state) {
    if (!state) return {};
    state.memoryObservability = state.memoryObservability || null;
    state.surfaceStatus = state.surfaceStatus || {};
    state.surfaceModules = state.surfaceModules || {};
    state.modernUi = state.modernUi || {};
    state.assistantBrain = state.assistantBrain || {};
    state.controlCenterReview = state.controlCenterReview || {};
    state.adminUxContract = state.adminUxContract || null;
    state.comfySpaceBuilder = state.comfySpaceBuilder || {
      capabilities: null, drafts: null, workspace: null, activeDraftId: '', step: 'import',
      busy: false, error: '', notice: '', dirty: false, importRuntimeProfileId: null, importSelectedFile: null, useLiveObjectInfo: false, controlFilter: 'all', previewValues: {},
      publishReadiness: null, lastPublication: null, publishedLibrary: null, nodeRegistry: null, nodeRegistryBusy: false, nodeRegistryError: '', nodeRegistryProfileId: '', nodeRegistryQuery: '',
    };
    return state;
  }

  async function adminLoadJson(ctx, url, fallback) {
    if (typeof ctx.loadJson === 'function') return ctx.loadJson(url, fallback);
    const response = await fetch(url);
    if (!response.ok) return fallback;
    return response.json();
  }

  function adminRender(ctx) {
    if (typeof ctx.render === 'function') ctx.render();
  }

  async function adminPostJson(url, payload = {}) {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }


  async function adminRequestJson(url, { method = 'GET', payload = undefined } = {}) {
    const options = { method, headers: { 'Content-Type': 'application/json' } };
    if (payload !== undefined) options.body = JSON.stringify(payload);
    const response = await fetch(url, options);
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (!response.ok) {
      const detail = body?.detail || body?.message;
      const readable = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => item?.msg || item?.message || JSON.stringify(item)).join(' · ') : detail ? JSON.stringify(detail) : `Request failed (${response.status})`;
      const error = new Error(readable);
      error.status = response.status;
      throw error;
    }
    return body || {};
  }

  const COMFY_BUILDER_STEPS = [
    ['import', '1', 'Import'], ['inspect', '2', 'Inspect'], ['controls', '3', 'Controls'],
    ['outputs', '4', 'Outputs'], ['stages', '5', 'Stages'], ['dynamics', '6', 'Dynamics'], ['presentation', '7', 'Presentation'], ['preview', '8', 'Preview'], ['publish', '9', 'Publish'],
  ];

  const COMFY_BUILDER_CONTROL_OPTIONS = {
    text: ['text_input', 'text_area'], integer: ['integer_input', 'slider', 'seed_input'], number: ['number_input', 'slider'],
    boolean: ['toggle'], choice: ['select', 'search_select', 'model_select'], json: ['json_editor'], image: ['image_upload'],
    mask: ['mask_upload'], video: ['video_upload'], audio: ['audio_upload'], file: ['file_upload'],
  };
  const COMFY_BUILDER_RENDERER_OPTIONS = {
    text: ['text_viewer'], integer: ['numeric_value'], number: ['numeric_value'], boolean: ['boolean_value'], choice: ['choice_value'],
    json: ['json_viewer'], image: ['image_preview'], mask: ['mask_preview'], video: ['video_player'], audio: ['audio_player'], file: ['file_download'],
  };

  function comfyBuilderState(state) { return ensureAdminState(state).comfySpaceBuilder; }
  function comfyBuilderDraft(state) { return comfyBuilderState(state).workspace?.draft || null; }
  function comfyBuilderAnalysis(state) { return comfyBuilderState(state).workspace?.analysis || {}; }
  function comfyBuilderCandidateMap(state, kind) {
    const rows = comfyBuilderAnalysis(state)?.candidates?.[kind] || [];
    return Object.fromEntries(rows.map((item) => [item.candidate_id, item]));
  }
  function comfyBuilderSetDirty(state, dirty = true) {
    const builder = comfyBuilderState(state); builder.dirty = dirty;
    if (dirty) {
      builder.publishReadiness = null;
      builder.lastPublication = null;
      if (builder.workspace) builder.workspace.preview = { available: false, manifest: null, ui: null, status: 'stale_local_edits' };
    }
  }
  function comfyBuilderCastValue(kind, value) {
    if (kind === 'integer') { const parsed = Number.parseInt(value, 10); return Number.isFinite(parsed) ? parsed : value; }
    if (kind === 'number') { const parsed = Number.parseFloat(value); return Number.isFinite(parsed) ? parsed : value; }
    if (kind === 'boolean') return value === true || value === 'true';
    if (kind === 'json') { try { return JSON.parse(value); } catch (_) { return value; } }
    return value;
  }
  function comfyBuilderInputValue(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') { try { return JSON.stringify(value); } catch (_) { return ''; } }
    return String(value);
  }
  function comfyBuilderPatchObject(target, field, value) {
    if (!target || !field) return;
    target[field] = value;
  }
  function comfyBuilderStepNav(ctx) {
    const { state, escapeHtml, escapeAttr } = ctx;
    const builder = comfyBuilderState(state); const hasDraft = Boolean(builder.workspace?.draft);
    return `<nav class="neo-comfy-builder-steps" aria-label="Comfy Space Builder steps">${COMFY_BUILDER_STEPS.map(([id, number, label]) => {
      const disabled = id !== 'import' && !hasDraft;
      return `<button type="button" class="neo-comfy-builder-step ${builder.step === id ? 'active' : ''}" ${disabled ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetStep',{step:'${escapeAttr(id)}'})"><span>${number}</span><strong>${escapeHtml(label)}</strong></button>`;
    }).join('')}</nav>`;
  }
  function comfyBuilderFeedback(ctx) {
    const { state, escapeHtml } = ctx; const builder = comfyBuilderState(state);
    const error = builder.error ? `<div class="neo-inline-status error"><strong>Builder blocked:</strong> ${escapeHtml(builder.error)}</div>` : '';
    const notice = builder.notice ? `<div class="neo-inline-status success">${escapeHtml(builder.notice)}</div>` : '';
    return `${error}${notice}`;
  }
  function comfyBuilderDraftListHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const builder = comfyBuilderState(state);
    const rows = builder.drafts?.drafts || [];
    if (!rows.length) return NeoUI.emptyState('No Comfy Space drafts yet.', 'Import a normal ComfyUI workflow JSON to create the first Builder draft. API-format JSON is also supported.');
    return `<div class="neo-comfy-builder-draft-list">${rows.map((item) => `<button type="button" class="neo-ui-record-card ${builder.activeDraftId === item.draft_id ? 'selected' : ''}" ${item.status !== 'ready' ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderOpenDraft',{draftId:'${escapeAttr(item.draft_id)}'})"><strong>${escapeHtml(item.name || item.draft_id)}</strong><span>${escapeHtml(item.space_id || item.draft_id)} · v${escapeHtml(item.version || '?')} · rev ${item.revision || 0}</span><small>${item.enabled_input_count || 0} inputs · ${item.enabled_output_count || 0} outputs · ${item.preview_available ? 'preview compiled' : 'preview not compiled'}${item.published ? ' · published' : ''}${item.status !== 'ready' ? ` · ${escapeHtml(item.error || 'invalid')}` : ''}</small></button>`).join('')}</div>`;
  }
  function comfyBuilderAvailableRuntimeProfiles(state) {
    return (state.backendProfiles?.profiles || []).filter((profile) => {
      const provider = String(profile.provider_id || profile.backend || '').toLowerCase();
      const connection = String(profile.connection_type || '').toLowerCase();
      return profile.enabled !== false && (provider.includes('comfy') || connection.includes('comfy'));
    });
  }
  function comfyBuilderRuntimeOptions(ctx) {
    const { state, escapeHtml, escapeAttr } = ctx; const builder = comfyBuilderState(state);
    const profiles = comfyBuilderAvailableRuntimeProfiles(state);
    const current = builder.importRuntimeProfileId === null ? (profiles[0]?.profile_id || '') : (builder.importRuntimeProfileId || '');
    if (!profiles.length) return '<option value="">Import without live runtime inspection</option>';
    return `${profiles.map((profile) => `<option value="${escapeAttr(profile.profile_id || '')}" ${current === profile.profile_id ? 'selected' : ''}>${escapeHtml(profile.display_name || profile.profile_id || 'Comfy runtime')}</option>`).join('')}<option value="" ${current ? '' : 'selected'}>Import without live runtime inspection</option>`;
  }
  function comfyBuilderPublishedOptions(ctx) {
    const { state, escapeHtml, escapeAttr } = ctx;
    const rows = comfyBuilderState(state).publishedLibrary?.spaces || [];
    const options = [];
    for (const space of rows) {
      const versions = Array.isArray(space.versions) && space.versions.length ? space.versions : [space.version].filter(Boolean);
      for (const version of versions) options.push({ space_id: space.space_id, version, name: space.name || space.space_id, active: version === (space.active_version || space.version) });
    }
    if (!options.length) return '<option value="">No published Spaces available</option>';
    return options.map((item) => `<option value="${escapeAttr(`${item.space_id}@@${item.version}`)}">${escapeHtml(item.name)} · ${escapeHtml(item.space_id)} · v${escapeHtml(item.version)}${item.active ? ' · active' : ''}</option>`).join('');
  }
  function comfyBuilderVersionManagerHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const builder = comfyBuilderState(state);
    const rows = builder.publishedLibrary?.spaces || [];
    if (!rows.length) return NeoUI.card({ title: 'Published versions', description: 'Immutable published history and rollback controls.', body: NeoUI.emptyState('No published Comfy Spaces yet.', 'Publish a Space before using version rollback.') });
    const body = rows.map((space) => {
      const active = space.active_version || space.version || ''; const latest = space.latest_version || space.version || '';
      const versions = Array.isArray(space.versions) && space.versions.length ? space.versions : [space.version].filter(Boolean);
      return `<div class="neo-ui-record-card"><strong>${escapeHtml(space.name || space.space_id)}</strong><span>${escapeHtml(space.space_id || '')} · active v${escapeHtml(active)}${latest && latest !== active ? ` · latest v${escapeHtml(latest)}` : ''}</span><div class="neo-ui-toolbar">${versions.map((version) => version === active ? `<span class="neo-ui-badge">v${escapeHtml(version)} active</span>` : `<button type="button" class="neo-btn secondary" ${builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRollbackVersion',{spaceId:'${escapeAttr(space.space_id || '')}',version:'${escapeAttr(version)}',expectedActiveVersion:'${escapeAttr(active)}'})">Activate v${escapeHtml(version)}</button>`).join('')}</div></div>`;
    }).join('');
    return NeoUI.card({ title: 'Published versions', description: 'Every published version remains immutable. Activating an older version only moves Neo’s active-version pointer; no package is copied or overwritten.', badge: NeoUI.statusBadge('ready'), body });
  }
  function comfyBuilderReplacementReviewHtml(ctx) {
    const { state, NeoUI, escapeHtml } = ctx; const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); const plan = builder.workspace?.replacement || null;
    if (!draft || !plan) return '';
    const summary = plan.summary || {}; const diff = plan.graph_diff || {}; const mappings = plan.mappings || [];
    const stale = Boolean(plan.review_confirmed && Number(plan.reviewed_draft_revision || 0) !== Number(draft.revision || 0));
    const unresolved = mappings.filter((row) => ['ambiguous','unmapped','review','incompatible'].includes(String(row.status || '')));
    const mapRows = mappings.slice(0, 24).map((row) => `${String(row.status || 'review').toUpperCase()} · ${row.mapping_type || 'mapping'} · ${row.source_id || '?'}${row.selected_candidate_id ? ` → ${row.selected_candidate_id}` : ''} · ${row.reason || ''}`);
    const description = plan.review_confirmed && !stale ? 'Migration review confirmed for this saved draft revision. Any later draft edit requires review confirmation again.' : stale ? 'The draft changed after review confirmation. Reconfirm the migration review before publication.' : 'Neo applied only unique high-confidence lineage mappings. Ambiguous/unmapped items were left for explicit Builder review rather than guessed.';
    const actions = `<button type="button" class="neo-btn primary" ${builder.busy || (plan.review_confirmed && !stale) ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderConfirmReplacementReview',{})">${plan.review_confirmed && !stale ? 'Migration review confirmed' : 'Confirm migration review'}</button>`;
    return NeoUI.card({ title: `Workflow replacement · v${escapeHtml(plan.from_version || '?')} → v${escapeHtml(plan.to_version || '?')}`, description, badge: NeoUI.statusBadge(plan.review_confirmed && !stale ? 'ready' : 'review'), body: `${NeoUI.badgeRow([`auto applied ${summary.auto_applied || 0}`, `review ${summary.review_required || 0}`, `ambiguous ${summary.ambiguous || 0}`, `unmapped ${summary.unmapped || 0}`])}${NeoUI.metaList([`Graph nodes: ${diff.old_node_count || 0} → ${diff.new_node_count || 0}`, `Added canonical nodes: ${(diff.added_canonical_node_ids || []).length}`, `Removed canonical nodes: ${(diff.removed_canonical_node_ids || []).length}`, `Changed class nodes: ${(diff.changed_class_node_ids || []).length}`, `Variants needing review: ${summary.variant_review_count || 0}`], { code: false })}${unresolved.length ? NeoUI.metaList(mapRows, { code: false }) : NeoUI.metaList(['All detected migration items were uniquely compatible.'], { code: false })}`, actions });
  }
  function comfyBuilderWorkflowNodeTypes(workflow) {
    // Class names are compared against ComfyUI live /object_info. Do not infer
    // widget mappings or evaluate node code from imported workflow JSON.
    const seen = new Set();
    const visit = (value, depth = 0) => {
      if (!value || typeof value !== 'object' || depth > 10 || seen.size >= 512) return;
      if (Array.isArray(value)) { for (const row of value) visit(row, depth + 1); return; }
      if (typeof value.class_type === 'string') seen.add(value.class_type);
      if (typeof value.type === 'string' && ('id' in value) && ('pos' in value || 'widgets_values' in value)) seen.add(value.type);
      if (value.nodes && Array.isArray(value.nodes)) visit(value.nodes, depth + 1);
      if (value.definitions?.subgraphs) visit(value.definitions.subgraphs, depth + 1);
      if (value.extra?.groupNodes) visit(value.extra.groupNodes, depth + 1);
      // API graphs store nodes under numeric keys, not a `nodes` array.
      if (depth === 0 && !Array.isArray(value.nodes)) for (const child of Object.values(value)) visit(child, depth + 1);
    };
    visit(workflow);
    return [...seen].filter(x => x.length > 0 && x.length <= 256).slice(0, 512);
  }
  function comfyBuilderNodeRegistryHtml(ctx) {
    const { state, NeoUI, escapeHtml } = ctx; const builder = comfyBuilderState(state);
    const current = builder.importRuntimeProfileId === null ? (comfyBuilderAvailableRuntimeProfiles(state)[0]?.profile_id || '') : (builder.importRuntimeProfileId || '');
    const registry = builder.nodeRegistryProfileId === current ? builder.nodeRegistry : null;
    const checked = registry?.required || {};
    const missing = checked.missing_classes || [];
    let status = registry?.live_checked
      ? `Loaded ${registry.node_count} node classes · synced from selected ComfyUI runtime${registry.synced_at ? ` · ${registry.synced_at}` : ''}`
      : registry?.snapshot_available
      ? `Cached inventory: ${registry.node_count} node classes. Refresh for current live status.`
      : 'Not synchronized in this session. Neo still reads live node schemas on every import.';
    if (registry?.live_checked && !checked.requested_count) status += ' · Select a workflow to compare its required node types.';
    const availability = missing.length
      ? `<div class="neo-inline-status error"><strong>Missing in this runtime:</strong> ${escapeHtml(missing.join(', '))}. Restart ComfyUI after installation, or select the runtime where these nodes are loaded.</div>`
      : registry?.live_checked && checked.requested_count
      ? `<div class="neo-inline-status success">All ${checked.requested_count} checked workflow node types are loaded in this runtime.</div>`
      : '';
    const changes = registry?.live_checked && (registry.new_classes?.length || registry.removed_classes?.length)
      ? `<small>Newly loaded: ${escapeHtml((registry.new_classes || []).slice(0, 12).join(', ') || 'none')} · Removed: ${escapeHtml((registry.removed_classes || []).slice(0, 12).join(', ') || 'none')}</small>` : '';
    return NeoUI.card({ title: 'Installed ComfyUI Nodes', description: 'Read-only discovery from the selected runtime. Synchronize after installing custom nodes and restarting ComfyUI. No manual node schemas or installs.', body:
      `<div class="neo-ui-field-grid two"><div><label>Check a node class (optional)</label><input id="neo-comfy-node-registry-check" placeholder="e.g. SeedVR2" value="${ctx.escapeAttr(builder.nodeRegistryQuery || '')}"></div><div><label>Selected runtime</label><div class="neo-ui-badge">${escapeHtml(current || 'No runtime selected')}</div></div></div><small>${escapeHtml(status)}</small>${changes}${availability}${builder.nodeRegistryError ? `<div class="neo-inline-status error">${escapeHtml(builder.nodeRegistryError)}</div>` : ''}<small>Cached inventory is diagnostic only. Import and Generate always validate against live /object_info.</small>`,
      actions: `<button type="button" class="neo-btn secondary" ${!current || builder.nodeRegistryBusy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRefreshNodeRegistry',{})">${builder.nodeRegistryBusy ? 'Refreshing nodes…' : 'Refresh Installed Nodes'}</button>` });
  }
  function comfyBuilderImportHtml(ctx) {
    const { state, NeoUI } = ctx; const builder = comfyBuilderState(state);
    const profiles = comfyBuilderAvailableRuntimeProfiles(state);
    const runtimeNote = profiles.length
      ? 'Neo automatically reads the selected ComfyUI runtime /object_info schema during import. This resolves legacy positional widgets and custom-node inputs without a separate analysis toggle.'
      : 'No ComfyUI runtime is currently available. Neo can still import workflows that already contain named widget values, but legacy/custom-node mappings may require a connected runtime.';
    const selectedWorkflowName = builder.importSelectedFile?.name || '';
    const freshImport = NeoUI.card({ title: 'Import Comfy workflow', description: 'Use the normal ComfyUI workflow JSON you save from the UI. API-format JSON is also supported. Neo converts normal workflows into its canonical execution graph without running them.', badge: NeoUI.statusBadge('preview'), body: `<div class="neo-ui-field-grid two"><div class="wide"><label>Workflow JSON</label><input id="neo-comfy-builder-file" type="file" accept="application/json,.json" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderSelectImportFile',{file:this.files?.[0]||null})" ${builder.busy ? 'disabled' : ''}><small id="neo-comfy-builder-selected-filename" role="status" aria-live="polite">${selectedWorkflowName ? `Selected: ${ctx.escapeHtml(selectedWorkflowName)} (retained if the form refreshes)` : 'Choose a workflow JSON file to import.'}</small></div><div><label>ComfyUI runtime</label><select id="neo-comfy-builder-runtime-profile" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetImportRuntime',{profileId:this.value})">${comfyBuilderRuntimeOptions(ctx)}</select><small>${runtimeNote}</small></div><div><label>Space ID override <span class="neo-muted">optional</span></label><input id="neo-comfy-builder-import-space-id" placeholder="cinematic.shot"></div><div><label>Name override <span class="neo-muted">optional</span></label><input id="neo-comfy-builder-import-name" placeholder="Cinematic Shot"></div></div>`, actions: `<button type="button" class="neo-btn primary" ${builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderImportSelectedFile',{})">${builder.busy ? 'Importing…' : 'Import & create draft'}</button><button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule('admin','reloadComfySpaceBuilder',{})">Refresh drafts</button>` });
    const replacement = NeoUI.card({ title: 'Replace a published workflow', description: 'Create a new immutable version from a replacement Comfy workflow. Neo compares lineage and migrates only uniquely compatible controls, outputs, anchors, stages, dynamics, and Variant references; ambiguous items remain explicit review work.', badge: NeoUI.statusBadge('review'), body: `<div class="neo-ui-field-grid two"><div class="wide"><label>Replacement workflow JSON</label><input id="neo-comfy-builder-replace-file" type="file" accept="application/json,.json" ${builder.busy ? 'disabled' : ''}></div><div><label>Published base version</label><select id="neo-comfy-builder-replace-base">${comfyBuilderPublishedOptions(ctx)}</select></div><div><label>New immutable version</label><input id="neo-comfy-builder-replace-version" placeholder="1.1.0"></div><div><label>ComfyUI runtime</label><select id="neo-comfy-builder-replace-runtime-profile">${comfyBuilderRuntimeOptions(ctx)}</select><small>Runtime inspection improves custom-node/source-lineage comparison but never executes the workflow.</small></div></div>`, actions: `<button type="button" class="neo-btn primary" ${builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderReplaceSelectedFile',{})">Compare & create replacement draft</button>` });
    return `${freshImport}${comfyBuilderNodeRegistryHtml(ctx)}${replacement}${NeoUI.card({ title: 'Existing drafts', description: 'Drafts are independent from temporary import staging after creation.', body: comfyBuilderDraftListHtml(ctx) })}${comfyBuilderVersionManagerHtml(ctx)}`;
  }

  function comfyBuilderInspectHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const draft = comfyBuilderDraft(state); const analysis = comfyBuilderAnalysis(state);
    if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const summary = analysis.summary || {}; const deps = analysis.dependencies || {}; const graph = analysis.graph || {};
    const subgraphIr = analysis.normalization?.subgraph_ir || {}; const subgraphInstances = subgraphIr.instances || [];
    const graphAnchors = analysis.graph_anchors || {}; const anchorProposals = graphAnchors.proposals || []; const anchorDrafts = Object.fromEntries((draft.anchors || []).map((item) => [item.anchor_id, item]));
    const authoringMap = analysis.authoring_map || {};
    const nodes = deps.node_requirements || []; const models = deps.model_requirements || [];
    const missingNodeHint = (deps.missing_node_classes || []).length ? NeoUI.metaList([`Missing node types: ${(deps.missing_node_classes || []).join(', ')}`, 'Open/check the workflow in the selected ComfyUI runtime and install or enable the custom-node pack that provides each missing node, then re-import or refresh analysis with that runtime selected. Neo does not install custom-node code automatically.'], { code: false }) : '';
    const inventoryRows = [`Nodes: ${summary.node_count || 0}`, `Edges: ${summary.edge_count || 0}`, `Input candidates: ${(analysis.candidates?.inputs || []).length}`, `Output candidates: ${(analysis.candidates?.outputs || []).length}`, `Graph anchor proposals: ${anchorProposals.length}`, `Authoring groups: ${authoringMap.summary?.source_node_group_count || 0} nodes · ${authoringMap.summary?.source_subgraph_group_count || 0} subgraphs`];
    if (subgraphIr.instance_count) inventoryRows.push(`Subgraph instances: ${subgraphIr.instance_count}`, `Nested depth: ${subgraphIr.max_depth || 0}`);
    inventoryRows.push(`Runtime profile: ${draft.provenance?.runtime_profile_id || 'none'}`, `Live object_info: ${draft.provenance?.object_info_used ? 'yes' : 'no'}`, `Workflow hash: ${(draft.provenance?.frozen_workflow_sha256 || '').slice(0, 16)}…`);
    const subgraphCard = subgraphInstances.length ? NeoUI.card({ title: 'Subgraph structure', description: 'Neo flattened these ComfyUI subgraph instances only for execution. Source hierarchy and node lineage remain recorded for Builder review and future graph-aware tooling.', badge: NeoUI.statusBadge('ready'), body: `${NeoUI.badgeRow([`instances ${subgraphIr.instance_count || 0}`, `definitions ${subgraphIr.used_definition_count || 0}`, `depth ${subgraphIr.max_depth || 0}`, `reroutes ${subgraphIr.modern_reroute_count || 0}`])}${NeoUI.metaList(subgraphInstances.slice(0, 12).map((item) => `${'  '.repeat(Math.max(0, Number(item.depth || 1) - 1))}${item.definition_name || item.definition_id || 'Subgraph'} · instance ${item.instance_node_id} · ${item.flattened_node_count || 0} nodes${(item.group_titles || []).length ? ` · groups: ${item.group_titles.join(', ')}` : ''}`))}` }) : '';
    const anchorRows = anchorProposals.map((anchor) => {
      const saved = anchorDrafts[anchor.anchor_id] || {}; const source = anchor.source || {}; const target = anchor.target || {}; const path = source.subgraph_definition_name || (source.subgraph_path || []).join(' / ') || 'Main workflow';
      const edge = target.canonical_node_id ? `${source.class_type || source.node_title || 'Node'}:${source.output_index ?? '?'} → ${target.class_type || target.node_title || 'Node'}.${target.input_field || '?'}` : `${source.class_type || source.node_title || 'Node'}:${source.output_index ?? '?'} · terminal output`;
      return `<label class="neo-ui-check-card"><input type="checkbox" ${saved.confirmed ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchAnchor',{anchorId:'${escapeAttr(anchor.anchor_id)}',confirmed:this.checked})"><span><strong>${escapeHtml(String(anchor.semantic_role || '').replaceAll('_',' '))}</strong><small>${escapeHtml(edge)} · ${escapeHtml(path)} · ${escapeHtml(anchor.evidence?.socket_type || 'typed socket')}${anchor.ambiguous_role ? ` · ${anchor.role_candidate_count} candidates for this role` : ''}</small></span></label>`;
    }).join('');
    const anchorCard = NeoUI.card({ title: 'Graph anchors', description: graphAnchors.runtime_schema_available ? 'Typed, read-only attachment proposals from canonical topology + the selected ComfyUI /object_info socket schema. Confirm only the connection points you want future graph add-ons to use; confirmation does not modify this workflow.' : 'No runtime socket schema was available during import, so Neo did not guess graph anchors. Re-import with a ComfyUI runtime selected to detect typed attachment points.', badge: NeoUI.statusBadge(graphAnchors.runtime_schema_available ? 'review' : 'not checked'), body: `${NeoUI.badgeRow([`proposals ${anchorProposals.length}`, `confirmed ${(draft.anchors || []).filter((item)=>item.confirmed).length}`, `ambiguous roles ${graphAnchors.ambiguous_role_count || 0}`, 'topology read-only'])}${anchorRows || NeoUI.emptyState('No typed graph anchors detected.', graphAnchors.runtime_schema_available ? 'This workflow may not expose supported MODEL / CONDITIONING / LATENT / IMAGE / VAE / VIDEO / AUDIO socket boundaries.' : 'Import with a connected ComfyUI runtime so Neo can inspect /object_info.')}` });
    return `${comfyBuilderReplacementReviewHtml(ctx)}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Workflow inventory', description: `${escapeHtml(draft.provenance?.source_name || 'workflow.json')} · frozen provenance`, badge: NeoUI.statusBadge(deps.preflight_state || 'review'), body: NeoUI.metaList(inventoryRows) })}${subgraphCard}${anchorCard}${NeoUI.card({ title: 'Node classes', description: 'Top classes in the frozen execution graph.', body: NeoUI.metaList(Object.entries(graph.class_type_counts || {}).slice(0, 12).map(([key, value]) => `${key}: ${value}`)) })}</div><div>${NeoUI.card({ title: 'Dependency preflight', description: 'ComfyUI runtime compatibility check. Known missing nodes/models block publication; unchecked dependencies remain warnings.', badge: NeoUI.statusBadge(deps.preflight_state || 'review'), body: `${NeoUI.badgeRow([`nodes ${nodes.length}`, `missing ${deps.missing_node_classes?.length || 0}`, `models ${models.length}`, `missing models ${deps.missing_models?.length || 0}`])}${NeoUI.metaList(nodes.slice(0, 10).map((item) => `${item.available === false ? 'Missing' : item.available === true ? 'Ready' : 'Unchecked'} · ${item.class_type} · ${item.package_hint || item.origin || 'unknown origin'}`))}${missingNodeHint}` })}${NeoUI.card({ title: 'Model requirements', description: 'The selected ComfyUI runtime /object_info combo catalog is authoritative compatibility evidence. Neo can reconcile a saved relative path to one unique runtime catalog entry, but never guesses an ambiguous filename.', body: models.length ? NeoUI.metaList(models.slice(0, 12).map((item) => { const configured = item.configured_name || '(model name unavailable)'; const resolved = item.runtime_resolved_name && item.runtime_resolved_name !== item.configured_name ? ` → ${item.runtime_resolved_name}` : ''; const method = item.resolution_method && !['exact','not_checked','catalog_unavailable'].includes(item.resolution_method) ? ` · ${item.resolution_method.replaceAll('_',' ')}` : ''; return `${item.availability === false ? 'Missing' : item.availability === true ? 'Ready' : 'Unchecked'} · ${item.model_type || 'model'} · ${configured}${resolved}${method}`; })) : NeoUI.emptyState('No model-loader requirements were detected.') })}</div></div>`;
  }
  function comfyBuilderControlSelect(ctx, candidate, item) {
    const { escapeHtml, escapeAttr } = ctx; const kind = candidate?.kind || candidate?.type_system?.kind || 'json'; const recommended = candidate?.type_system?.control || '';
    const options = COMFY_BUILDER_CONTROL_OPTIONS[kind] || [recommended].filter(Boolean); const selected = item.control_override || '';
    return `<select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'control_override',value:this.value||null})"><option value="">Auto · ${escapeHtml(recommended || 'default')}</option>${options.map((value) => `<option value="${escapeAttr(value)}" ${selected === value ? 'selected' : ''}>${escapeHtml(value.replaceAll('_', ' '))}</option>`).join('')}</select>`;
  }
  function comfyBuilderControlsHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state);
    if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const candidates = comfyBuilderCandidateMap(state, 'inputs'); const sections = [...(draft.sections || [])].sort((a,b)=>(a.order||0)-(b.order||0));
    const authoringMap = comfyBuilderAnalysis(state)?.authoring_map || {};
    const filter = builder.controlFilter || 'all';
    const allItems = [...(draft.inputs || [])].sort((a,b)=>(a.order||0)-(b.order||0));
    const visibleItems = allItems.filter((item) => {
      const candidate = candidates[item.candidate_id] || {};
      if (filter === 'enabled') return Boolean(item.enabled);
      if (filter === 'disabled') return !item.enabled;
      if (filter === 'recommended') return Boolean(candidate.recommended);
      return true;
    });
    const cards = visibleItems.map((item) => { const candidate = candidates[item.candidate_id] || {}; const type = candidate.type_system || {}; const constraints = item.constraints_override || type.constraints || {};
      const recommendation = candidate.recommended ? 'Recommended by Neo' : 'Optional · disabled by default';
      const role = candidate.node_role ? ` · ${String(candidate.node_role).replaceAll('_', ' ')}` : '';
      const sourceNode = candidate.subgraph_definition_name ? `${candidate.subgraph_definition_name} / node ${candidate.source_node_id || candidate.node_id || '?'}` : (candidate.node_id || '?');
      return `<article class="neo-comfy-builder-control-card ${item.enabled ? 'enabled' : ''}"><div class="neo-comfy-builder-card-head"><label class="neo-ui-check-card"><input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'enabled',value:this.checked})"> Expose</label><div><strong>${escapeHtml(candidate.class_type || 'Node')} · ${escapeHtml(candidate.field || item.input_id)}</strong><small>${escapeHtml(sourceNode)} · ${escapeHtml(candidate.kind || 'json')} · score ${candidate.score ?? '?'}${escapeHtml(role)} · ${escapeHtml(recommendation)}</small></div></div><div class="neo-ui-field-grid two compact"><div><label>Input ID</label><input value="${escapeAttr(item.input_id)}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'input_id',value:this.value})"></div><div><label>Label</label><input value="${escapeAttr(item.label)}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'label',value:this.value})"></div><div class="wide"><label>Help text</label><input value="${escapeAttr(item.help_text || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'help_text',value:this.value})"></div><div><label>Section</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'section_id',value:this.value})">${sections.map((section) => `<option value="${escapeAttr(section.section_id)}" ${section.section_id === item.section_id ? 'selected' : ''}>${escapeHtml(section.title)}</option>`).join('')}</select></div><div><label>Control</label>${comfyBuilderControlSelect(ctx, candidate, item)}</div>${type.supports_multiple ? `<div><label>Values</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'multiplicity',value:this.value})"><option value="single" ${item.multiplicity !== 'multiple' ? 'selected' : ''}>Single value</option><option value="multiple" ${item.multiplicity === 'multiple' ? 'selected' : ''}>Multiple values</option></select></div>` : ''}<div><label>Order</label><input type="number" value="${item.order ?? 100}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'order',value:Number(this.value)})"></div><div><label>Default policy</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'default_mode',value:this.value})"><option value="template" ${item.default_mode === 'template' ? 'selected' : ''}>Workflow template</option><option value="none" ${item.default_mode === 'none' ? 'selected' : ''}>None</option><option value="custom" ${item.default_mode === 'custom' ? 'selected' : ''}>Custom</option></select></div><div><label>Custom default</label><input value="${escapeAttr(comfyBuilderInputValue(item.default))}" ${item.default_mode !== 'custom' ? 'disabled' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'default',value:this.value,kind:'${escapeAttr(candidate.kind || 'text')}'})"></div><div><label>Behavior</label><label class="neo-ui-check-card"><input type="checkbox" ${item.advanced ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchInput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'advanced',value:this.checked})"> Advanced</label></div>${['integer','number'].includes(candidate.kind) ? `<div><label>Min override</label><input type="number" step="any" value="${escapeAttr(item.constraints_override?.minimum ?? '')}" placeholder="${escapeAttr(type.constraints?.minimum ?? '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchConstraint',{candidateId:'${escapeAttr(item.candidate_id)}',field:'minimum',value:this.value})"></div><div><label>Max override</label><input type="number" step="any" value="${escapeAttr(item.constraints_override?.maximum ?? '')}" placeholder="${escapeAttr(type.constraints?.maximum ?? '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchConstraint',{candidateId:'${escapeAttr(item.candidate_id)}',field:'maximum',value:this.value})"></div><div><label>Step override</label><input type="number" step="any" value="${escapeAttr(item.constraints_override?.step ?? '')}" placeholder="${escapeAttr(type.constraints?.step ?? '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchConstraint',{candidateId:'${escapeAttr(item.candidate_id)}',field:'step',value:this.value})"></div>` : ''}</div><p class="neo-muted">${escapeHtml((candidate.reasons || []).join(' · ') || type.reason || 'Analysis candidate')}</p></article>`;
    });
    const groupingEnabled = Boolean(authoringMap?.candidate_sections);
    const filterBar = `<div class="neo-comfy-builder-control-toolbar"><div class="neo-comfy-builder-filter-row"><button type="button" class="neo-btn compact ${filter === 'all' ? 'active' : ''}" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetControlFilter',{filter:'all'})">All</button><button type="button" class="neo-btn compact ${filter === 'enabled' ? 'active' : ''}" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetControlFilter',{filter:'enabled'})">Exposed</button><button type="button" class="neo-btn compact ${filter === 'disabled' ? 'active' : ''}" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetControlFilter',{filter:'disabled'})">Not exposed</button><button type="button" class="neo-btn compact ${filter === 'recommended' ? 'active' : ''}" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetControlFilter',{filter:'recommended'})">Recommended</button></div><div class="neo-comfy-builder-filter-row"><button type="button" class="neo-btn compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyControlPreset',{preset:'recommended'})">Reset to Neo suggestions</button><button type="button" class="neo-btn compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyControlPreset',{preset:'enable_all'})">Expose all</button><button type="button" class="neo-btn compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyControlPreset',{preset:'disable_all'})">Expose none</button></div><div class="neo-comfy-builder-filter-row"><span class="neo-muted">Group controls:</span><button type="button" class="neo-btn compact" ${groupingEnabled ? '' : 'disabled'} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyGrouping',{mode:'workflow_group'})">Comfy groups</button><button type="button" class="neo-btn compact" ${groupingEnabled ? '' : 'disabled'} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyGrouping',{mode:'semantic_role'})">Functional</button><button type="button" class="neo-btn compact" ${groupingEnabled ? '' : 'disabled'} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyGrouping',{mode:'source_node'})">Source node</button><button type="button" class="neo-btn compact" ${groupingEnabled ? '' : 'disabled'} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyGrouping',{mode:'source_subgraph'})">Source subgraph</button><button type="button" class="neo-btn compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderApplyGrouping',{mode:'manual'})">Reset to Main</button></div></div>`;
    const summary = NeoUI.card({ title: 'Detected controls', description: 'Every detected workflow input stays available here. Neo only recommends the initial exposure state: disabled means the original workflow value is preserved but the control will not appear in the published Space unless you enable it. Grouping is presentation-only: changing a section never bypasses, mutes, inserts, or rewires Comfy nodes.', badge: NeoUI.statusBadge('review'), body: `${NeoUI.badgeRow([`candidates ${allItems.length}`, `exposed ${allItems.filter((x)=>x.enabled).length}`, `recommended ${allItems.filter((x)=>Boolean((candidates[x.candidate_id] || {}).recommended)).length}`, `showing ${visibleItems.length}`])}${filterBar}` });
    // Group the authoring cards exactly as the published Space will group them.
    // This is visual presentation only; order does not affect Comfy execution.
    const cardFragments = cards;
    const sectionCards = sections.map((section) => {
      const relevant = visibleItems.map((item, index) => ({ item, html: cardFragments[index] || '' }))
        .filter(({ item }) => item.section_id === section.section_id);
      if (!relevant.length) return '';
      return `<section class="neo-comfy-builder-node-section"><h4>${escapeHtml(section.title)}</h4>${section.description ? `<p class="neo-muted">${escapeHtml(section.description)}</p>` : ''}${relevant.map(({ html }) => html).join('')}</section>`;
    }).join('');
    return `${summary}<div class="neo-comfy-builder-control-list">${sectionCards || NeoUI.emptyState('No controls match this filter.', 'Switch back to All to see every detected candidate.')}</div>`;
  }
  function comfyBuilderRendererSelect(ctx, candidate, item) {
    const { escapeHtml, escapeAttr } = ctx; const kind = candidate?.kind || candidate?.type_system?.kind || 'json'; const recommended = candidate?.type_system?.renderer || '';
    const options = COMFY_BUILDER_RENDERER_OPTIONS[kind] || [recommended].filter(Boolean); const selected = item.renderer_override || '';
    return `<select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'renderer_override',value:this.value||null})"><option value="">Auto · ${escapeHtml(recommended || 'default')}</option>${options.map((value) => `<option value="${escapeAttr(value)}" ${selected === value ? 'selected' : ''}>${escapeHtml(value.replaceAll('_',' '))}</option>`).join('')}</select>`;
  }
  function comfyBuilderOutputsHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const draft = comfyBuilderDraft(state); if (!draft) return NeoUI.emptyState('Open a Builder draft first.'); const candidates = comfyBuilderCandidateMap(state, 'outputs');
    const cards = [...(draft.outputs || [])].sort((a,b)=>(a.order||0)-(b.order||0)).map((item) => { const candidate = candidates[item.candidate_id] || {}; const sourceNode = candidate.subgraph_definition_name ? `${candidate.subgraph_definition_name} / node ${candidate.source_node_id || candidate.node_id || '?'}` : (candidate.node_id || '?');
      return `<article class="neo-comfy-builder-control-card ${item.enabled ? 'enabled' : ''}"><div class="neo-comfy-builder-card-head"><label class="neo-ui-check-card"><input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'enabled',value:this.checked})"> Expose</label><div><strong>${escapeHtml(candidate.class_type || 'Output')} · ${escapeHtml(candidate.output_name || candidate.field || item.output_id)}</strong><small>${escapeHtml(sourceNode)} · ${escapeHtml(candidate.kind || 'json')} · ${escapeHtml(candidate.binding_source || 'output')}</small></div></div><div class="neo-ui-field-grid two compact"><div><label>Output ID</label><input value="${escapeAttr(item.output_id)}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'output_id',value:this.value})"></div><div><label>Label</label><input value="${escapeAttr(item.label)}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'label',value:this.value})"></div><div><label>Renderer</label>${comfyBuilderRendererSelect(ctx, candidate, item)}</div><div><label>Collection</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'collection',value:this.value})"><option value="first" ${item.collection === 'first' ? 'selected' : ''}>First</option><option value="all" ${item.collection === 'all' ? 'selected' : ''}>All</option></select></div><div><label>Order</label><input type="number" value="${item.order ?? 100}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchOutput',{candidateId:'${escapeAttr(item.candidate_id)}',field:'order',value:Number(this.value)})"></div><div><label>Primary result</label><label class="neo-ui-check-card"><input type="checkbox" ${item.primary ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetPrimaryOutput',{candidateId:'${escapeAttr(item.candidate_id)}',enabled:this.checked})"> Primary</label></div></div><p class="neo-muted">${escapeHtml(candidate.reason || candidate.type_system?.reason || 'Detected workflow output')}</p></article>`;
    }).join('');
    return `${NeoUI.card({ title: 'Visible outputs', description: 'Choose what the future Space returns to Neo. Builder Preview only authors result contracts; it does not collect runtime artifacts.', badge: NeoUI.statusBadge('review'), body: NeoUI.badgeRow([`candidates ${draft.outputs?.length || 0}`, `enabled ${(draft.outputs || []).filter((x)=>x.enabled).length}`, `primary ${(draft.outputs || []).filter((x)=>x.enabled && x.primary).length}`]) })}<div class="neo-comfy-builder-control-list">${cards || NeoUI.emptyState('No output candidates were detected.')}</div>`;
  }
  function comfyBuilderStagesHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const draft = comfyBuilderDraft(state); if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const outputs = (draft.outputs || []).filter((item) => item.enabled);
    const stages = [...(draft.stages || [])].sort((a,b)=>(a.order||0)-(b.order||0));
    const outputChecks = (stage, field) => outputs.map((output) => {
      const checked = (stage[field] || []).includes(output.output_id);
      return `<label class="neo-ui-check-card"><input type="checkbox" ${checked ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderToggleStageOutput',{stageId:'${escapeAttr(stage.stage_id)}',field:'${field}',outputId:'${escapeAttr(output.output_id)}',enabled:this.checked})"><span><strong>${escapeHtml(output.label || output.output_id)}</strong><small>${escapeHtml(output.output_id)}</small></span></label>`;
    }).join('') || '<div class="neo-empty compact">Enable at least one output first.</div>';
    const cards = stages.map((stage) => {
      const dependencyOptions = stages.filter((other) => other.stage_id !== stage.stage_id).map((other) => `<option value="${escapeAttr(other.stage_id)}" ${(stage.required_stage_ids || []).includes(other.stage_id) ? 'selected' : ''}>${escapeHtml(other.label || other.stage_id)}</option>`).join('');
      return `<article class="neo-comfy-builder-section-card"><div class="neo-ui-field-grid two compact"><div><label>Stage ID</label><input value="${escapeAttr(stage.stage_id)}" disabled></div><div><label>Label</label><input value="${escapeAttr(stage.label || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'label',value:this.value})"></div><div class="wide"><label>Description</label><input value="${escapeAttr(stage.description || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'description',value:this.value})"></div><div><label>Order</label><input type="number" value="${stage.order ?? 100}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'order',value:Number(this.value)})"></div><div><label>Dependencies</label><select multiple size="${Math.max(2,Math.min(4,stages.length-1))}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderSetStageDependencies',{stageId:'${escapeAttr(stage.stage_id)}',values:Array.from(this.selectedOptions).map(o=>o.value)})">${dependencyOptions}</select></div><div><label>Resource policy</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'resource_policy',value:this.value})"><option value="automatic" ${stage.resource_policy === 'automatic' ? 'selected' : ''}>Automatic</option><option value="keep_loaded" ${stage.resource_policy === 'keep_loaded' ? 'selected' : ''}>Keep loaded</option><option value="free_memory" ${stage.resource_policy === 'free_memory' ? 'selected' : ''}>Free GPU cache</option><option value="unload_models" ${stage.resource_policy === 'unload_models' ? 'selected' : ''}>Unload models</option></select></div><div><label>Behavior</label><label class="neo-ui-check-card"><input type="checkbox" ${stage.optional ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'optional',value:this.checked})"> Optional</label><label class="neo-ui-check-card"><input type="checkbox" ${stage.enabled_by_default ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'enabled_by_default',value:this.checked})"> Enabled by default</label><label class="neo-ui-check-card"><input type="checkbox" ${stage.can_run_alone ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchStage',{stageId:'${escapeAttr(stage.stage_id)}',field:'can_run_alone',value:this.checked})"> Can run alone</label></div></div><div class="neo-comfy-stage-output-grid"><div><strong>Target outputs</strong>${outputChecks(stage,'target_output_ids')}</div><div><strong>Intermediate results</strong>${outputChecks(stage,'intermediate_output_ids')}</div></div>${stage.stage_id !== 'full' || stages.length > 1 ? `<button type="button" class="neo-btn danger compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRemoveStage',{stageId:'${escapeAttr(stage.stage_id)}'})">Remove stage</button>` : ''}</article>`;
    }).join('');
    return `${NeoUI.card({ title: 'Execution stages', description: 'Stages are explicit execution boundaries. They are not UI sections and they never translate to Comfy bypass/mute. Each stage targets one or more declared Space outputs; Neo computes the safe ancestor slice at run time.', badge: NeoUI.statusBadge('review'), actions: '<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule(\'admin\',\'comfySpaceBuilderAddStage\',{})">Add stage</button>', body: NeoUI.badgeRow([`stages ${stages.length}`, `outputs ${outputs.length}`, 'Run All / Run Stage / Continue']) })}<div class="neo-comfy-builder-section-list">${cards || NeoUI.emptyState('No execution stages defined.', 'Add a stage and select its target output(s).')}</div>`;
  }
  function comfyBuilderDynamicValue(value) {
    const text = String(value ?? '').trim();
    if (!text) return null;
    try { return JSON.parse(text); } catch (_) { return text; }
  }
  function comfyBuilderDynamicsHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const draft = comfyBuilderDraft(state); if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const inputs = (draft.inputs || []).filter((item) => item.enabled); const sections = draft.sections || []; const stages = draft.stages || [];
    const candidates = comfyBuilderCandidateMap(state, 'inputs'); const behaviors = draft.dynamic_behaviors || []; const collections = draft.dynamic_collections || [];
    const targetOptions = (behavior) => `${inputs.map((item)=>`<option value="input:${escapeAttr(item.input_id)}" ${behavior.target_type === 'input' && behavior.target_id === item.input_id ? 'selected' : ''}>Input · ${escapeHtml(item.label || item.input_id)}</option>`).join('')}${sections.map((item)=>`<option value="section:${escapeAttr(item.section_id)}" ${behavior.target_type === 'section' && behavior.target_id === item.section_id ? 'selected' : ''}>Section · ${escapeHtml(item.title || item.section_id)}</option>`).join('')}`;
    const refHint = (source) => source === 'input' ? `Inputs: ${inputs.map((x)=>x.input_id).join(', ') || 'none'}` : source === 'stage_enabled' ? `Stages: ${stages.map((x)=>x.stage_id).join(', ') || 'none'}` : source === 'addon_present' ? 'Use a reviewed Graph Add-on module ID, e.g. neo.controlnet.conditioning.' : 'Variant active uses no reference.';
    const behaviorCards = behaviors.map((behavior) => {
      const predicates = behavior.condition?.predicates || [];
      const predicateRows = predicates.map((predicate, index) => `<div class="neo-comfy-dynamic-predicate"><div class="neo-ui-field-grid two compact"><div><label>Source</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}',index:${index},field:'source',value:this.value})"><option value="input" ${predicate.source === 'input' ? 'selected' : ''}>Input value</option><option value="stage_enabled" ${predicate.source === 'stage_enabled' ? 'selected' : ''}>Stage enabled</option><option value="addon_present" ${predicate.source === 'addon_present' ? 'selected' : ''}>Graph Add-on present</option><option value="variant_active" ${predicate.source === 'variant_active' ? 'selected' : ''}>Variant active</option></select></div><div><label>Reference</label><input value="${escapeAttr(predicate.ref || '')}" ${predicate.source === 'variant_active' ? 'disabled' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}',index:${index},field:'ref',value:this.value})"><small>${escapeHtml(refHint(predicate.source))}</small></div><div><label>Operator</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}',index:${index},field:'operator',value:this.value})">${['equals','not_equals','in','not_in','truthy','falsy','exists','gt','gte','lt','lte'].map((op)=>`<option value="${op}" ${predicate.operator === op ? 'selected' : ''}>${op.replaceAll('_',' ')}</option>`).join('')}</select></div><div><label>Expected value</label><input value="${escapeAttr(comfyBuilderInputValue(predicate.value))}" ${['truthy','falsy','exists'].includes(predicate.operator) ? 'disabled' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}',index:${index},field:'value',value:this.value})"><small>JSON syntax is accepted for arrays, booleans and numbers.</small></div></div>${predicates.length > 1 ? `<button class="neo-btn danger compact" type="button" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRemoveDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}',index:${index}})">Remove condition</button>` : ''}</div>`).join('');
      return `<article class="neo-comfy-builder-section-card neo-comfy-dynamic-card"><div class="neo-ui-field-grid two compact"><div><label>Rule ID</label><input value="${escapeAttr(behavior.behavior_id)}" disabled></div><div><label>Target</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicTarget',{behaviorId:'${escapeAttr(behavior.behavior_id)}',value:this.value})">${targetOptions(behavior)}</select></div><div><label>Effect</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicBehavior',{behaviorId:'${escapeAttr(behavior.behavior_id)}',field:'effect',value:this.value})"><option value="visible" ${behavior.effect === 'visible' ? 'selected' : ''}>Visible</option><option value="enabled" ${behavior.effect === 'enabled' ? 'selected' : ''}>Enabled</option></select></div><div><label>Condition mode</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCondition',{behaviorId:'${escapeAttr(behavior.behavior_id)}',field:'mode',value:this.value})"><option value="all" ${behavior.condition?.mode !== 'any' ? 'selected' : ''}>All conditions</option><option value="any" ${behavior.condition?.mode === 'any' ? 'selected' : ''}>Any condition</option></select><label class="neo-ui-check-card"><input type="checkbox" ${behavior.condition?.negate ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCondition',{behaviorId:'${escapeAttr(behavior.behavior_id)}',field:'negate',value:this.checked})"> Negate result</label></div></div><div class="neo-comfy-dynamic-predicate-list">${predicateRows}</div><div class="neo-workspace-actions"><button class="neo-btn compact" type="button" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderAddDynamicPredicate',{behaviorId:'${escapeAttr(behavior.behavior_id)}'})">Add condition</button><button class="neo-btn danger compact" type="button" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRemoveDynamicBehavior',{behaviorId:'${escapeAttr(behavior.behavior_id)}'})">Remove rule</button></div></article>`;
    }).join('');
    const eligible = inputs.filter((item)=>item.multiplicity === 'multiple');
    const collectionCards = collections.map((collection) => `<article class="neo-comfy-builder-section-card neo-comfy-dynamic-card"><div class="neo-ui-field-grid two compact"><div><label>Collection ID</label><input value="${escapeAttr(collection.collection_id)}" disabled></div><div><label>Input</label><strong>${escapeHtml(collection.input_id)}</strong></div><div><label>Mode</label><select onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'mode',value:this.value})"><option value="repeater" ${collection.mode === 'repeater' ? 'selected' : ''}>Repeater</option><option value="reference_list" ${collection.mode === 'reference_list' ? 'selected' : ''}>Reference list</option></select></div><div><label>Label</label><input value="${escapeAttr(collection.label || 'Items')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'label',value:this.value})"></div><div><label>Item label</label><input value="${escapeAttr(collection.item_label || 'Item')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'item_label',value:this.value})"></div><div><label>Bounds</label><div class="neo-inline-fields"><input type="number" min="0" max="64" value="${collection.min_items ?? 0}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'min_items',value:Number(this.value)})"><input type="number" min="1" max="64" value="${collection.max_items ?? 8}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'max_items',value:Number(this.value)})"></div></div><div><label>Reorder</label><label class="neo-ui-check-card"><input type="checkbox" ${collection.allow_reorder !== false ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}',field:'allow_reorder',value:this.checked})"> Allow reorder</label></div></div><button class="neo-btn danger compact" type="button" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRemoveDynamicCollection',{collectionId:'${escapeAttr(collection.collection_id)}'})">Remove collection</button></article>`).join('');
    const addCollection = eligible.length ? `<div class="neo-comfy-dynamic-add"><select id="neo-comfy-dynamic-collection-input">${eligible.filter((item)=>!collections.some((c)=>c.input_id===item.input_id)).map((item)=>`<option value="${escapeAttr(item.input_id)}">${escapeHtml(item.label || item.input_id)}</option>`).join('')}</select><button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderAddDynamicCollection',{inputId:document.getElementById('neo-comfy-dynamic-collection-input')?.value||''})">Add collection</button></div>` : '<p class="neo-muted">Set an eligible JSON or asset input to Multiple values in Controls before adding a collection UI.</p>';
    return `${NeoUI.card({ title: 'Conditional / Dynamic controls', description: 'Declarative UI behavior only. Rules can react to Space values, enabled stages, selected Graph Add-ons, or Variant state. They cannot bypass nodes, mutate bindings, or execute per-Space JavaScript.', badge: NeoUI.statusBadge('review'), actions: '<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule(\'admin\',\'comfySpaceBuilderAddDynamicBehavior\',{})">Add behavior rule</button>', body: NeoUI.badgeRow([`rules ${behaviors.length}`, `collections ${collections.length}`, 'declarative only', 'no generated JS']) })}<div class="neo-comfy-builder-section-list">${behaviorCards || NeoUI.emptyState('No dynamic behavior rules.', 'Add a rule when a control or section should appear or enable only under a known condition.')}</div>${NeoUI.card({ title: 'Repeaters & reference lists', description: 'Collections are allowed only for inputs whose manifest multiplicity is Multiple. Asset lists reuse Neo’s normal asset transport; JSON repeaters compile to ordinary validated Space values.', badge: NeoUI.statusBadge('review'), body: addCollection })}<div class="neo-comfy-builder-section-list">${collectionCards}</div>`;
  }
  function comfyBuilderPresentationHtml(ctx) {
    const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const draft = comfyBuilderDraft(state); if (!draft) return NeoUI.emptyState('Open a Builder draft first.'); const identity = draft.identity || {}; const sections = [...(draft.sections || [])].sort((a,b)=>(a.order||0)-(b.order||0));
    const sectionCards = sections.map((section) => `<article class="neo-comfy-builder-section-card"><div class="neo-ui-field-grid two compact"><div><label>Section ID</label><input value="${escapeAttr(section.section_id)}" disabled></div><div><label>Title</label><input value="${escapeAttr(section.title)}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'title',value:this.value})"></div><div class="wide"><label>Description</label><input value="${escapeAttr(section.description || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'description',value:this.value})"></div><div><label>Order</label><input type="number" value="${section.order ?? 100}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'order',value:Number(this.value)})"></div><div><label>Layout</label><label class="neo-ui-check-card"><input type="checkbox" ${section.advanced ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'advanced',value:this.checked})"> Advanced</label></div><div><label>Collapse</label><label class="neo-ui-check-card"><input type="checkbox" ${section.collapsible ? 'checked' : ''} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'collapsible',value:this.checked})"> Collapsible</label></div><div><label>Default</label><label class="neo-ui-check-card"><input type="checkbox" ${section.default_collapsed ? 'checked' : ''} ${section.collapsible ? '' : 'disabled'} onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchSection',{sectionId:'${escapeAttr(section.section_id)}',field:'default_collapsed',value:this.checked})"> Collapsed</label></div></div>${section.section_id !== 'main' ? `<button type="button" class="neo-btn danger compact" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderRemoveSection',{sectionId:'${escapeAttr(section.section_id)}'})">Remove section</button>` : ''}</article>`).join('');
    return `${NeoUI.card({ title: 'Space identity', description: 'These values compile into the draft manifest and Space UI identity.', body: `<div class="neo-ui-field-grid two"><div><label>Space ID</label><input value="${escapeAttr(identity.space_id || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchIdentity',{field:'space_id',value:this.value})"></div><div><label>Name</label><input value="${escapeAttr(identity.name || '')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchIdentity',{field:'name',value:this.value})"></div><div><label>Version</label><input value="${escapeAttr(identity.version || '0.1.0')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchIdentity',{field:'version',value:this.value})"></div><div class="wide"><label>Description</label><textarea rows="3" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchIdentity',{field:'description',value:this.value})">${escapeHtml(identity.description || '')}</textarea></div><div><label>Submit button</label><input value="${escapeAttr(draft.submit_label || 'Create')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchRoot',{field:'submit_label',value:this.value})"></div><div><label>Results title</label><input value="${escapeAttr(draft.results_title || 'Results')}" onchange="neoInvokeSurfaceModule('admin','comfySpaceBuilderPatchRoot',{field:'results_title',value:this.value})"></div></div>` })}${NeoUI.card({ title: 'Sections', description: 'Group controls into a native Neo form layout. Removing a section reassigns its inputs to Main.', actions: '<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule(\'admin\',\'comfySpaceBuilderAddSection\',{})">Add section</button>', body: `<div class="neo-comfy-builder-section-list">${sectionCards}</div>` })}`;
  }
  function comfyBuilderPreviewHtml(ctx) {
    const { state, NeoUI } = ctx; const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state);
    if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const preview = builder.workspace?.preview || {};
    if (!preview.available || !preview.ui || !preview.manifest) return `${NeoUI.card({ title: 'Compile preview', description: 'Save the current draft, then compile the manifest + Space UI contracts used by Neo’s shared Space renderer. No Comfy workflow is executed.', badge: NeoUI.statusBadge(builder.dirty ? 'unsaved' : 'not compiled'), actions: `<button type="button" class="neo-btn primary" ${builder.dirty ? 'disabled title="Save Draft first"' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderCompilePreview',{})">Compile preview</button>`, body: builder.dirty ? NeoUI.emptyState('Unsaved draft changes.', 'Save Draft before compiling so preview provenance matches the stored revision.') : NeoUI.emptyState('No current preview.', 'Compile to validate bindings and presentation.') })}`;
    const renderer = window.NeoComfySpaceRenderer;
    if (!renderer || typeof renderer.render !== 'function') return NeoUI.card({ title: 'Shared renderer unavailable', description: 'The compiled contracts are valid, but the shared Space renderer asset did not load.', badge: NeoUI.statusBadge('blocked'), body: NeoUI.emptyState('Reload Neo Studio.', 'If this remains blocked, verify the shared renderer static asset is present.') });
    const rendered = renderer.render({
      rendererId: 'neo-comfy-builder-shared-preview',
      manifest: preview.manifest, ui: preview.ui, dynamicControls: preview.dynamic_controls || null,
      dynamicContext: { enabledStageIds: (draft.stages || []).filter((item)=>item.enabled_by_default).map((item)=>item.stage_id), addonModuleIds: [], variantActive: false },
      values: builder.previewValues || {}, results: {}, mode: 'preview', assetControlsEnabled: false, disabled: false, submit: { enabled: false },
      bridge: { surfaceId: 'admin', inputAction: 'comfySpaceBuilderPreviewInput' },
    });
    return `${NeoUI.card({ title: 'Interactive Space preview', description: 'This is the same declarative renderer used by the public Comfy Spaces runner. Controls are interactive for UI review; Submit and media transport stay disabled.', badge: NeoUI.statusBadge('ready'), body: NeoUI.badgeRow([`inputs ${(preview.manifest.inputs || []).length}`, `outputs ${(preview.manifest.outputs || []).length}`, `status ${preview.manifest.status || 'draft'}`, `revision ${draft.revision}`]), actions: '<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule(\'admin\',\'comfySpaceBuilderCompilePreview\',{})">Recompile</button>' })}${rendered}`;
  }
  function comfyBuilderPublishHtml(ctx) {
    const { state, NeoUI, escapeHtml } = ctx; const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state);
    if (!draft) return NeoUI.emptyState('Open a Builder draft first.');
    const gate = builder.publishReadiness?.publish_gate || null;
    const publication = builder.lastPublication || builder.workspace?.publication || null;
    const publishedCard = publication?.published ? NeoUI.card({ title: 'Published package', description: `${escapeHtml(publication.space_id || draft.identity?.space_id || '')} · v${escapeHtml(publication.version || draft.identity?.version || '')}`, badge: NeoUI.statusBadge('ready'), body: NeoUI.metaList([publication.version_root || publication.manifest_path || 'Published to the Comfy Spaces library.', 'Published packages are immutable by version. Change the version before publishing another revision.', 'Publishing does not execute the workflow.'], { code: false }) }) : '';
    if (builder.dirty) return `${publishedCard}${NeoUI.card({ title: 'Save before publishing', description: 'The publish gate evaluates the authoritative saved draft revision, not unsaved browser state or preview files.', badge: NeoUI.statusBadge('unsaved'), body: NeoUI.emptyState('Unsaved draft changes.', 'Save Draft first, then run the publish readiness check.'), actions: '<button type="button" class="neo-btn" disabled>Check publish readiness</button>' })}`;
    if (!gate) return `${publishedCard}${NeoUI.card({ title: 'Fail-closed publish gate', description: 'Neo recompiles the saved draft, verifies provenance, bindings, UI references, portability, dependency state, trust policy, workflow hash, and target version before writing anything to the Space library.', badge: NeoUI.statusBadge('review'), body: NeoUI.metaList(['Preview files are not trusted for publication.', 'Known missing nodes/models block publication.', 'Unchecked dependencies are warned and must still preflight before execution.', 'Existing published versions are never overwritten.', 'No Comfy graph is executed and no dependencies are installed.'], { code: false }), actions: '<button type="button" class="neo-btn primary" onclick="neoInvokeSurfaceModule(\'admin\',\'comfySpaceBuilderCheckPublishReadiness\',{})">Check publish readiness</button>' })}`;
    const blockers = gate.blockers || []; const warnings = gate.warnings || []; const checks = gate.checks || [];
    const status = gate.ready ? 'ready' : 'blocked';
    const body = `${NeoUI.badgeRow([`checks ${checks.length}`, `blockers ${blockers.length}`, `warnings ${warnings.length}`, `revision ${gate.revision || draft.revision}`])}${blockers.length ? NeoUI.metaList(blockers.map((item) => `BLOCKED · ${item.message || item.code}`), { code: false }) : NeoUI.metaList(checks.map((item) => `${item.status || 'ready'} · ${item.message || item.check}`), { code: false })}${warnings.length ? NeoUI.metaList(warnings.map((item) => `Warning · ${item.message || item.code}`), { code: false }) : ''}`;
    const actions = `<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderCheckPublishReadiness',{})">Recheck</button><button type="button" class="neo-btn primary" ${!gate.ready || builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderPublish',{})">Publish Space</button>`;
    return `${publishedCard}${NeoUI.card({ title: gate.ready ? 'Ready to publish' : 'Publish blocked', description: gate.ready ? 'All fail-closed package gates passed for this saved revision.' : 'Fix every blocker before Neo will write this version to the Comfy Spaces library.', badge: NeoUI.statusBadge(status), body, actions })}`;
  }
  function comfyBuilderCurrentStepHtml(ctx) {
    const builder = comfyBuilderState(ctx.state);
    if (builder.step === 'inspect') return comfyBuilderInspectHtml(ctx);
    if (builder.step === 'controls') return comfyBuilderControlsHtml(ctx);
    if (builder.step === 'outputs') return comfyBuilderOutputsHtml(ctx);
    if (builder.step === 'stages') return comfyBuilderStagesHtml(ctx);
    if (builder.step === 'dynamics') return comfyBuilderDynamicsHtml(ctx);
    if (builder.step === 'presentation') return comfyBuilderPresentationHtml(ctx);
    if (builder.step === 'preview') return comfyBuilderPreviewHtml(ctx);
    if (builder.step === 'publish') return comfyBuilderPublishHtml(ctx);
    return comfyBuilderImportHtml(ctx);
  }

  const api = {
    surfaceId: 'admin',
    area: 'memory',
    status: 'ready',
    migratedAreas: ['render.surface_module_architecture', 'render.modern_ui_system', 'render.memory_observability', 'render.surface_module_status', 'render.assistant_brain_workspace', 'render.control_center_review', 'action.memory_observability.refresh', 'action.surface_module_status.refresh', 'action.surface_module_status.audit', 'action.surface_module_architecture.refresh', 'action.surface_module_architecture.audit', 'action.modern_ui.refresh', 'action.modern_ui.audit', 'action.assistant_brain.refresh', 'action.assistant_brain.select', 'action.assistant_brain.context', 'action.control_center_review.refresh', 'action.control_center_review.select_trace', 'action.control_center_review.review', 'render.comfy_space_builder', 'action.comfy_space_builder.reload', 'action.comfy_space_builder.import', 'action.comfy_space_builder.open', 'action.comfy_space_builder.save', 'action.comfy_space_builder.preview', 'action.comfy_space_builder.preview_input', 'action.comfy_space_builder.control_filter', 'action.comfy_space_builder.control_preset', 'action.comfy_space_builder.anchor_confirmation', 'action.comfy_space_builder.grouping', 'action.comfy_space_builder.dynamic_controls', 'action.comfy_space_builder.publish_readiness', 'action.comfy_space_builder.publish'],
    policy: 'Admin owns its cockpit renderers and action handlers while legacy wrappers remain available as safe fallbacks.',
    diagnostics: {
      module_status: 'admin_memory_cockpit_actions_partial',
      fallback: 'legacy wrappers remain active',
      risk: 'low',
    },

    actions: {
      async reloadMemoryObservability(ctx) {
        const state = ensureAdminState(ctx.state);
        state.memoryObservability = await adminPostJson('/api/memory/observability/snapshot', { limit: 25 });
        adminRender(ctx);
        return { status: 'ok', action: 'reloadMemoryObservability' };
      },
      async reloadSurfaceModuleStatus(ctx) {
        const state = ensureAdminState(ctx.state);
        state.surfaceStatus.status = await adminLoadJson(ctx, '/api/surfaces/status/status', state.surfaceStatus.status || null);
        adminRender(ctx);
        return { status: 'ok', action: 'reloadSurfaceModuleStatus' };
      },
      async runSurfaceModuleStatusAudit(ctx) {
        const state = ensureAdminState(ctx.state);
        state.surfaceStatus.audit = await adminPostJson('/api/surfaces/status/audit', {});
        state.surfaceStatus.status = state.surfaceStatus.audit.status_runtime || state.surfaceStatus.status;
        adminRender(ctx);
        return { status: 'ok', action: 'runSurfaceModuleStatusAudit' };
      },
      async reloadSurfaceModuleArchitecture(ctx) {
        const state = ensureAdminState(ctx.state);
        state.surfaceModules.status = await adminLoadJson(ctx, '/api/surfaces/modules/status', state.surfaceModules.status || null);
        state.surfaceStatus.status = await adminLoadJson(ctx, '/api/surfaces/status/status', state.surfaceStatus.status || null);
        state.modernUi.status = await adminLoadJson(ctx, '/api/ui/modern/status', state.modernUi.status || null);
        state.surfaceModules.manifest = await adminLoadJson(ctx, '/api/surfaces/modules/manifest', state.surfaceModules.manifest || null);
        adminRender(ctx);
        return { status: 'ok', action: 'reloadSurfaceModuleArchitecture' };
      },
      async runSurfaceModuleArchitectureAudit(ctx) {
        const state = ensureAdminState(ctx.state);
        state.surfaceModules.audit = await adminPostJson('/api/surfaces/modules/audit', {});
        state.surfaceModules.status = state.surfaceModules.audit.architecture || state.surfaceModules.status;
        adminRender(ctx);
        return { status: 'ok', action: 'runSurfaceModuleArchitectureAudit' };
      },
      async reloadModernUiSystem(ctx) {
        const state = ensureAdminState(ctx.state);
        state.modernUi.status = await adminLoadJson(ctx, '/api/ui/modern/status', state.modernUi.status || null);
        adminRender(ctx);
        return { status: 'ok', action: 'reloadModernUiSystem' };
      },
      async runModernUiSystemAudit(ctx) {
        const state = ensureAdminState(ctx.state);
        state.modernUi.audit = await adminPostJson('/api/ui/modern/audit', {});
        state.modernUi.status = state.modernUi.audit.modern_ui || state.modernUi.status;
        adminRender(ctx);
        return { status: 'ok', action: 'runModernUiSystemAudit' };
      },
      async reloadAssistantBrainWorkspace(ctx) {
        const state = ensureAdminState(ctx.state);
        state.assistantBrain.status = await adminLoadJson(ctx, '/api/assistant/brain/status', state.assistantBrain.status || null);
        state.adminUxContract = await adminLoadJson(ctx, '/api/admin/ux-contract', state.adminUxContract || null);
        state.surfaceModules.status = await adminLoadJson(ctx, '/api/surfaces/modules/status', state.surfaceModules.status || null);
        state.surfaceStatus.status = await adminLoadJson(ctx, '/api/surfaces/status/status', state.surfaceStatus.status || null);
        state.modernUi.status = await adminLoadJson(ctx, '/api/ui/modern/status', state.modernUi.status || null);
        state.assistantBrain.workspaces = await adminLoadJson(ctx, '/api/assistant/brain/workspaces', state.assistantBrain.workspaces || null);
        const workspaceId = state.assistantBrain.activeWorkspaceId || state.assistantBrain.workspaces?.workspaces?.[0]?.workspace_id || '';
        state.assistantBrain.dashboard = await adminPostJson('/api/assistant/brain/dashboard', { workspace_id: workspaceId, limit: 8 });
        if (state.assistantBrain.dashboard?.active_workspace?.workspace_id) state.assistantBrain.activeWorkspaceId = state.assistantBrain.dashboard.active_workspace.workspace_id;
        adminRender(ctx);
        return { status: 'ok', action: 'reloadAssistantBrainWorkspace' };
      },
      async selectAssistantBrainWorkspace(ctx) {
        const state = ensureAdminState(ctx.state);
        const workspaceId = ctx.workspaceId || '';
        state.assistantBrain.activeWorkspaceId = workspaceId;
        state.assistantBrain.dashboard = await adminPostJson('/api/assistant/brain/dashboard', { workspace_id: workspaceId, limit: 8 });
        adminRender(ctx);
        return { status: 'ok', action: 'selectAssistantBrainWorkspace', workspace_id: workspaceId };
      },
      async activateAssistantBrainWorkspace(ctx) {
        const state = ensureAdminState(ctx.state);
        const workspaceId = state.assistantBrain.activeWorkspaceId || state.assistantBrain.dashboard?.active_workspace?.workspace_id || '';
        state.assistantBrain.lastActivation = { status: 'redirect_required', workspace_id: workspaceId, owner: 'assistant.surface', message: 'Admin Scope Readout is diagnostic only. Activate or edit Scopes in Assistant → Scopes.' };
        adminRender(ctx);
        return { status: 'redirect_required', action: 'activateAssistantBrainWorkspace', workspace_id: workspaceId, owner: 'assistant.surface' };
      },
      async buildAssistantBrainContext(ctx) {
        const state = ensureAdminState(ctx.state);
        const workspaceId = state.assistantBrain.activeWorkspaceId || state.assistantBrain.dashboard?.active_workspace?.workspace_id || '';
        state.assistantBrain.lastContext = await adminPostJson('/api/assistant/brain/context', { workspace_id: workspaceId, query: 'Build Assistant Brain workspace context preview.', limit: 8 });
        await api.actions.reloadAssistantBrainWorkspace(ctx);
        return { status: 'ok', action: 'buildAssistantBrainContext', workspace_id: workspaceId };
      },
      async reloadControlCenterReview(ctx) {
        const state = ensureAdminState(ctx.state);
        const controller = ctx.controller ?? document.getElementById('neo-cc-review-controller')?.value ?? state.controlCenterReview?.filters?.controller ?? '';
        const surface = ctx.surface ?? document.getElementById('neo-cc-review-surface')?.value ?? state.controlCenterReview?.filters?.surface ?? '';
        const query = ctx.query ?? document.getElementById('neo-cc-review-query')?.value ?? state.controlCenterReview?.filters?.query ?? '';
        state.controlCenterReview.filters = { controller, surface, query };
        state.controlCenterReview.status = await adminLoadJson(ctx, '/api/control-center/review/status', state.controlCenterReview.status || null);
        state.assistantBrain.status = await adminLoadJson(ctx, '/api/assistant/brain/status', state.assistantBrain.status || null);
        state.surfaceModules.status = await adminLoadJson(ctx, '/api/surfaces/modules/status', state.surfaceModules.status || null);
        state.modernUi.status = await adminLoadJson(ctx, '/api/ui/modern/status', state.modernUi.status || null);
        state.controlCenterReview.dashboard = await adminPostJson('/api/control-center/review/dashboard', { controller, surface, query, trace_id: state.controlCenterReview.selectedTraceId || '', limit: 20 });
        if (state.controlCenterReview.dashboard?.selected_trace?.trace_id) state.controlCenterReview.selectedTraceId = state.controlCenterReview.dashboard.selected_trace.trace_id;
        adminRender(ctx);
        return { status: 'ok', action: 'reloadControlCenterReview' };
      },
      async selectControlCenterReviewTrace(ctx) {
        const state = ensureAdminState(ctx.state);
        state.controlCenterReview.selectedTraceId = ctx.traceId || '';
        await api.actions.reloadControlCenterReview(ctx);
        return { status: 'ok', action: 'selectControlCenterReviewTrace', trace_id: ctx.traceId || '' };
      },
      async reviewControlCenterTrace(ctx) {
        const state = ensureAdminState(ctx.state);
        const decision = ctx.decision || 'reviewed';
        const traceId = state.controlCenterReview?.selectedTraceId || state.controlCenterReview?.dashboard?.selected_trace?.trace_id || '';
        if (!traceId) throw new Error('No Control Center trace selected.');
        const note = ctx.note ?? (window.prompt ? (window.prompt('Review note for this trace?', decision) || '') : '');
        state.controlCenterReview.lastReview = await adminPostJson('/api/control-center/review/decision', { trace_id: traceId, decision, note });
        await api.actions.reloadControlCenterReview(ctx);
        return { status: 'ok', action: 'reviewControlCenterTrace', trace_id: traceId, decision };
      },

      async reloadComfySpaceBuilder(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state);
        builder.busy = true; builder.error = '';
        try {
          const [capabilities, drafts, publishedLibrary] = await Promise.all([
            adminRequestJson('/api/admin/comfy-spaces/builder/capabilities'),
            adminRequestJson('/api/admin/comfy-spaces/builder/drafts'),
            adminRequestJson('/api/comfy-spaces/library'),
          ]);
          builder.capabilities = capabilities; builder.drafts = drafts; builder.publishedLibrary = publishedLibrary;
          if (builder.activeDraftId && !builder.dirty) {
            try { builder.workspace = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(builder.activeDraftId)}`); builder.publishReadiness = null; builder.lastPublication = null; }
            catch (error) { builder.error = error.message || String(error); }
          } else if (builder.activeDraftId && builder.dirty) {
            builder.notice = 'Draft list refreshed. Unsaved local edits were kept.';
          }
        } finally { builder.busy = false; adminRender(ctx); }
        return { status: 'ok', action: 'reloadComfySpaceBuilder' };
      },
      async comfySpaceBuilderOpenDraft(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draftId = String(ctx.draftId || '').trim();
        if (!draftId) throw new Error('Draft ID is required.');
        if (builder.dirty && window.confirm && !window.confirm('Discard unsaved Comfy Space draft changes and reload/open another draft?')) {
          return { status: 'cancelled', action: 'comfySpaceBuilderOpenDraft', draft_id: draftId };
        }
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          builder.workspace = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draftId)}`);
          builder.previewValues = {}; builder.publishReadiness = null; builder.lastPublication = null; builder.controlFilter = 'all';
          builder.activeDraftId = draftId; builder.step = builder.step === 'import' ? 'inspect' : builder.step; builder.dirty = false;
        } finally { builder.busy = false; adminRender(ctx); }
        return { status: 'ok', action: 'comfySpaceBuilderOpenDraft', draft_id: draftId };
      },
      comfySpaceBuilderSetStep(ctx) {
        const builder = comfyBuilderState(ctx.state); const step = String(ctx.step || 'import');
        if (COMFY_BUILDER_STEPS.some(([id]) => id === step)) builder.step = step;
        adminRender(ctx); return { status: 'ok', action: 'comfySpaceBuilderSetStep', step };
      },
      comfySpaceBuilderSetImportRuntime(ctx) {
        const builder = comfyBuilderState(ctx.state); builder.importRuntimeProfileId = String(ctx.profileId || '');
        builder.useLiveObjectInfo = Boolean(builder.importRuntimeProfileId);
        builder.nodeRegistry = null; builder.nodeRegistryProfileId = ''; builder.nodeRegistryError = '';
        // The file input DOM node is replaced during render. A selected File is
        // retained in the ephemeral Builder state, never serialized to a draft.
        adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderSelectImportFile(ctx) {
        const builder = comfyBuilderState(ctx.state);
        builder.importSelectedFile = ctx.file && typeof ctx.file.text === 'function' ? ctx.file : null;
        builder.error = '';
        const feedback = document.getElementById('neo-comfy-builder-selected-filename');
        if (feedback) feedback.textContent = builder.importSelectedFile ? `Selected: ${builder.importSelectedFile.name}` : 'Choose a workflow JSON file to import.';
        return { status: builder.importSelectedFile ? 'ok' : 'empty' };
      },
      comfySpaceBuilderSetLiveAnalysis(ctx) {
        const builder = comfyBuilderState(ctx.state); builder.useLiveObjectInfo = Boolean(ctx.enabled);
        adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderSetControlFilter(ctx) {
        const builder = comfyBuilderState(ctx.state); const filter = String(ctx.filter || 'all');
        builder.controlFilter = ['all', 'enabled', 'disabled', 'recommended'].includes(filter) ? filter : 'all';
        adminRender(ctx); return { status: 'ok', filter: builder.controlFilter };
      },
      comfySpaceBuilderApplyControlPreset(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const candidates = comfyBuilderCandidateMap(state, 'inputs'); const preset = String(ctx.preset || 'recommended');
        for (const item of (draft.inputs || [])) {
          if (preset === 'enable_all') item.enabled = true;
          else if (preset === 'disable_all') item.enabled = false;
          else item.enabled = Boolean((candidates[item.candidate_id] || {}).recommended);
        }
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok', preset };
      },
      comfySpaceBuilderPatchAnchor(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const anchorId = String(ctx.anchorId || '').trim(); const item = (draft.anchors || []).find((row) => row.anchor_id === anchorId);
        if (!item) return { status: 'missing' };
        item.confirmed = Boolean(ctx.confirmed); comfyBuilderSetDirty(state); adminRender(ctx);
        return { status: 'ok', anchor_id: anchorId, confirmed: item.confirmed };
      },
      comfySpaceBuilderApplyGrouping(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const mode = String(ctx.mode || 'manual');
        if (!['manual', 'workflow_group', 'semantic_role', 'source_node', 'source_subgraph'].includes(mode)) return { status: 'blocked' };
        const modeLabel = mode === 'manual' ? 'Main' : mode.replaceAll('_', ' ');
        if (window.confirm && !window.confirm(`Replace the current section layout with ${modeLabel} grouping? Control bindings and the Comfy graph will not change.`)) return { status: 'cancelled', mode };
        if (mode === 'manual') {
          draft.sections = [{ section_id: 'main', title: 'Main', description: '', order: 10, advanced: false, collapsible: false, default_collapsed: false }];
          (draft.inputs || []).forEach((item, index) => { item.section_id = 'main'; item.order = (index + 1) * 10; });
          comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok', mode };
        }
        const map = comfyBuilderAnalysis(state)?.authoring_map || {}; const groups = map.groups?.[mode] || []; const candidateSections = map.candidate_sections || {};
        if (!groups.length) return { status: 'blocked' };
        draft.sections = [{ section_id: 'main', title: 'Main', description: 'Fallback controls not mapped by the selected authoring view.', order: 5, advanced: false, collapsible: false, default_collapsed: false }, ...groups.map((group, index) => ({
          section_id: group.section_id, title: group.title || `Section ${index + 1}`, description: mode === 'workflow_group' ? 'ComfyUI canvas group, ordered by graph dependencies.' : mode === 'source_node' ? 'Controls sourced from one Comfy node.' : mode === 'source_subgraph' ? 'Controls sourced from one Comfy workflow/subgraph scope.' : 'Controls grouped by Neo semantic role.', order: (index + 1) * 10 + 10, advanced: false, collapsible: true, default_collapsed: false,
        }))];
        (draft.inputs || []).forEach((item, index) => {
          const suggestion = candidateSections[item.candidate_id]?.[mode]; item.section_id = suggestion?.section_id || 'main'; item.order = (index + 1) * 10;
        });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok', mode, section_count: draft.sections.length };
      },
      async comfySpaceBuilderReplaceSelectedFile(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state);
        const file = document.getElementById('neo-comfy-builder-replace-file')?.files?.[0];
        const base = String(document.getElementById('neo-comfy-builder-replace-base')?.value || '');
        const [spaceId, baseVersion] = base.split('@@'); const targetVersion = String(document.getElementById('neo-comfy-builder-replace-version')?.value || '').trim();
        if (!file || !spaceId || !baseVersion || !targetVersion) { builder.error = 'Choose a published base version, replacement workflow JSON, and new semantic version.'; adminRender(ctx); return { status: 'blocked' }; }
        if (builder.dirty && window.confirm && !window.confirm('Discard unsaved Builder changes and create a workflow-replacement draft?')) return { status: 'cancelled' };
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          let workflow = null; try { workflow = JSON.parse(await file.text()); } catch (_) { throw new Error('The selected replacement file is not valid JSON.'); }
          const runtimeProfileId = String(document.getElementById('neo-comfy-builder-replace-runtime-profile')?.value || '').trim();
          const result = await adminRequestJson('/api/admin/comfy-spaces/builder/replace', { method: 'POST', payload: { workflow, source_name: file.name, base_space_id: spaceId, base_version: baseVersion, target_version: targetVersion, runtime_profile_id: runtimeProfileId, use_live_object_info: Boolean(runtimeProfileId) } });
          builder.workspace = result.workspace; builder.activeDraftId = result.draft_id; builder.step = 'inspect'; builder.dirty = false; builder.previewValues = {}; builder.publishReadiness = null; builder.lastPublication = null;
          builder.notice = `Replacement draft created for ${spaceId} v${baseVersion} → v${targetVersion}. Review every ambiguous/unmapped item, save any manual fixes, then explicitly confirm migration review before publishing.`;
          builder.drafts = await adminRequestJson('/api/admin/comfy-spaces/builder/drafts');
        } catch (error) { builder.error = error.message || String(error); }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderReplaceSelectedFile' };
      },
      async comfySpaceBuilderConfirmReplacementReview(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft || !builder.workspace?.replacement) return { status: 'blocked' };
        if (builder.dirty) { builder.error = 'Save Draft before confirming the migration review.'; adminRender(ctx); return { status: 'blocked' }; }
        if (window.confirm && !window.confirm('Confirm that you reviewed the workflow diff and handled or intentionally dropped every non-migrated item?')) return { status: 'cancelled' };
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          const result = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draft.draft_id)}/replacement/confirm`, { method: 'POST', payload: { confirm_review: true } });
          builder.workspace = result.workspace; builder.publishReadiness = null; builder.notice = 'Migration review confirmed for this saved draft revision. Any further draft edit will require confirmation again.';
        } catch (error) { builder.error = error.message || String(error); }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderConfirmReplacementReview' };
      },
      async comfySpaceBuilderRollbackVersion(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const spaceId = String(ctx.spaceId || '').trim(); const version = String(ctx.version || '').trim(); const expected = String(ctx.expectedActiveVersion || '').trim();
        if (!spaceId || !version) return { status: 'blocked' };
        if (window.confirm && !window.confirm(`Activate ${spaceId} v${version}? Published packages stay immutable; this only changes the active-version pointer.`)) return { status: 'cancelled' };
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          const result = await adminRequestJson('/api/admin/comfy-spaces/versioning/rollback', { method: 'POST', payload: { space_id: spaceId, version, expected_active_version: expected, confirm_rollback: true } });
          builder.publishedLibrary = await adminRequestJson('/api/comfy-spaces/library'); builder.notice = `Active version changed to ${spaceId} v${result.active_version || version}. No published package was modified.`;
        } catch (error) { builder.error = error.message || String(error); }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderRollbackVersion' };
      },
      async comfySpaceBuilderRefreshNodeRegistry(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state);
        const runtimeProfileId = document.getElementById('neo-comfy-builder-runtime-profile')?.value?.trim()
          || builder.importRuntimeProfileId || comfyBuilderAvailableRuntimeProfiles(state)[0]?.profile_id || '';
        if (!runtimeProfileId) {
          builder.nodeRegistryError = 'Choose a ComfyUI runtime before refreshing its installed node list.';
          adminRender(ctx); return { status: 'blocked' };
        }
        if (builder.nodeRegistryBusy) return { status: 'busy' };
        const query = document.getElementById('neo-comfy-node-registry-check')?.value?.trim() || '';
        builder.nodeRegistryQuery = query;
        const file = builder.importSelectedFile || document.getElementById('neo-comfy-builder-file')?.files?.[0];
        builder.nodeRegistryBusy = true; builder.nodeRegistryError = '';
        adminRender(ctx);
        try {
          let required = [];
          if (file) {
            let payload;
            try { payload = JSON.parse(await file.text()); }
            catch (_) { throw new Error('Selected workflow JSON is invalid. Fix the file before comparing node types.'); }
            required = comfyBuilderWorkflowNodeTypes(payload);
          }
          if (query && !required.includes(query)) required.push(query);
          const result = await adminRequestJson('/api/admin/comfy-spaces/runtime/node-registry/refresh', {
            method: 'POST', payload: { runtime_profile_id: runtimeProfileId, required_classes: required },
          });
          builder.nodeRegistry = result;
          builder.nodeRegistryProfileId = runtimeProfileId;
          builder.notice = `ComfyUI node inventory refreshed: ${result.node_count || 0} loaded types.`;
        } catch (error) {
          builder.nodeRegistryError = error.message || String(error);
        } finally { builder.nodeRegistryBusy = false; adminRender(ctx); }
        return { status: builder.nodeRegistryError ? 'failed' : 'ok', action: 'comfySpaceBuilderRefreshNodeRegistry' };
      },
      async comfySpaceBuilderImportSelectedFile(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state);
        const input = document.getElementById('neo-comfy-builder-file');
        const file = input?.files?.[0] || builder.importSelectedFile;
        if (!file) { builder.error = 'Choose a ComfyUI workflow JSON first. If you changed the runtime after selecting a file, select the file again.'; adminRender(ctx); return { status: 'blocked' }; }
        if (builder.busy) return { status: 'busy', action: 'comfySpaceBuilderImportSelectedFile' };
        if (builder.dirty && window.confirm && !window.confirm('Discard unsaved Comfy Space draft changes and import a new workflow?')) {
          return { status: 'cancelled', action: 'comfySpaceBuilderImportSelectedFile' };
        }
        // Capture form values before changing busy state and triggering a redraw.
        // Otherwise runtime/identity overrides can be lost along with the input DOM.
        const runtimeProfileId = document.getElementById('neo-comfy-builder-runtime-profile')?.value?.trim() || builder.importRuntimeProfileId || '';
        const spaceIdOverride = document.getElementById('neo-comfy-builder-import-space-id')?.value?.trim() || '';
        const nameOverride = document.getElementById('neo-comfy-builder-import-name')?.value?.trim() || '';
        builder.importSelectedFile = file;
        builder.busy = true; builder.error = ''; builder.notice = `Importing ${file.name}…`;
        adminRender(ctx);
        try {
          let workflow = null;
          try { workflow = JSON.parse(await file.text()); } catch (_) { throw new Error('The selected file is not valid JSON.'); }
          if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) throw new Error('The workflow JSON must contain a ComfyUI workflow object.');
          const payload = {
            workflow, source_name: file.name,
            runtime_profile_id: runtimeProfileId,
            use_live_object_info: Boolean(runtimeProfileId),
            space_id: spaceIdOverride,
            name: nameOverride,
          };
          const result = await adminRequestJson('/api/admin/comfy-spaces/builder/import', { method: 'POST', payload });
          builder.workspace = result.workspace; builder.previewValues = {}; builder.publishReadiness = null; builder.lastPublication = null; builder.controlFilter = 'all'; builder.activeDraftId = result.draft_id; builder.step = 'inspect'; builder.dirty = false;
          builder.notice = result.converted_from_editor ? `Draft created from normal ComfyUI workflow ${file.name}. Neo converted it to the canonical execution graph internally. Review detected controls before saving or compiling a preview.` : `Draft created from ${file.name}. Review detected controls before saving or compiling a preview.`;
          builder.drafts = await adminRequestJson('/api/admin/comfy-spaces/builder/drafts');
          builder.importSelectedFile = null;
        } catch (error) {
          builder.error = `Workflow import failed: ${error.message || String(error)}`;
          builder.notice = '';
        }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderImportSelectedFile' };
      },
      comfySpaceBuilderAddStage(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        draft.stages = draft.stages || []; let index = draft.stages.length + 1; let stageId = `stage_${index}`;
        while (draft.stages.some((item) => item.stage_id === stageId)) { index += 1; stageId = `stage_${index}`; }
        const firstOutput = (draft.outputs || []).find((item) => item.enabled)?.output_id || '';
        draft.stages.push({ stage_id: stageId, label: `Stage ${index}`, description: '', order: index * 10, required_stage_ids: [], target_output_ids: firstOutput ? [firstOutput] : [], intermediate_output_ids: [], enabled_by_default: true, optional: false, can_run_alone: true, resource_policy: 'automatic' });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok', stage_id: stageId };
      },
      comfySpaceBuilderRemoveStage(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const stageId = String(ctx.stageId || ''); draft.stages = (draft.stages || []).filter((item) => item.stage_id !== stageId);
        for (const item of (draft.stages || [])) item.required_stage_ids = (item.required_stage_ids || []).filter((id) => id !== stageId);
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchStage(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.stages || []).find((row) => row.stage_id === String(ctx.stageId || '')); if (!item) return { status: 'missing' };
        item[String(ctx.field || '')] = ctx.value; comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderSetStageDependencies(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.stages || []).find((row) => row.stage_id === String(ctx.stageId || '')); if (!item) return { status: 'missing' };
        item.required_stage_ids = Array.isArray(ctx.values) ? ctx.values.filter((id) => id !== item.stage_id) : [];
        if (item.required_stage_ids.length) item.can_run_alone = false;
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderToggleStageOutput(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.stages || []).find((row) => row.stage_id === String(ctx.stageId || '')); if (!item) return { status: 'missing' };
        const field = String(ctx.field || ''); if (!['target_output_ids','intermediate_output_ids'].includes(field)) return { status: 'blocked' };
        const values = new Set(item[field] || []); if (ctx.enabled) values.add(String(ctx.outputId || '')); else values.delete(String(ctx.outputId || '')); item[field] = [...values].filter(Boolean);
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderAddDynamicBehavior(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        draft.dynamic_behaviors = draft.dynamic_behaviors || []; const used = new Set(draft.dynamic_behaviors.map((x)=>x.behavior_id)); let n=draft.dynamic_behaviors.length+1; let id=`dynamic_rule_${n}`; while(used.has(id)) id=`dynamic_rule_${++n}`;
        const input = (draft.inputs || []).find((x)=>x.enabled); const section = (draft.sections || [])[0];
        const targetType = input ? 'input' : 'section'; const targetId = input?.input_id || section?.section_id || 'main';
        const predicate = input ? { source:'input', ref:input.input_id, operator:'truthy', value:null } : { source:'variant_active', ref:'', operator:'truthy', value:null };
        draft.dynamic_behaviors.push({ behavior_id:id, target_type:targetType, target_id:targetId, effect:'visible', condition:{ mode:'all', predicates:[predicate], negate:false } });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status:'ok', behavior_id:id };
      },
      comfySpaceBuilderRemoveDynamicBehavior(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); if(!draft)return{status:'blocked'}; draft.dynamic_behaviors=(draft.dynamic_behaviors||[]).filter((x)=>x.behavior_id!==ctx.behaviorId); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchDynamicBehavior(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); if(!row)return{status:'missing'}; row[String(ctx.field||'')]=ctx.value; comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchDynamicTarget(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); if(!row)return{status:'missing'}; const [type,...rest]=String(ctx.value||'').split(':'); row.target_type=type==='section'?'section':'input'; row.target_id=rest.join(':'); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchDynamicCondition(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); if(!row)return{status:'missing'}; row.condition=row.condition||{mode:'all',predicates:[],negate:false}; row.condition[String(ctx.field||'')]=ctx.value; comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderAddDynamicPredicate(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); if(!row)return{status:'missing'}; row.condition=row.condition||{mode:'all',predicates:[],negate:false}; row.condition.predicates=row.condition.predicates||[]; if(row.condition.predicates.length>=16)return{status:'blocked'}; const input=(draft.inputs||[]).find((x)=>x.enabled); row.condition.predicates.push(input?{source:'input',ref:input.input_id,operator:'truthy',value:null}:{source:'variant_active',ref:'',operator:'truthy',value:null}); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderRemoveDynamicPredicate(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); if(!row||!Array.isArray(row.condition?.predicates)||row.condition.predicates.length<=1)return{status:'blocked'}; row.condition.predicates.splice(Number(ctx.index||0),1); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchDynamicPredicate(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_behaviors||[]).find((x)=>x.behavior_id===ctx.behaviorId); const predicate=row?.condition?.predicates?.[Number(ctx.index||0)]; if(!predicate)return{status:'missing'}; const field=String(ctx.field||''); let value=ctx.value; if(field==='value') value=comfyBuilderDynamicValue(value); if(field==='source'&&value==='variant_active') predicate.ref=''; predicate[field]=value; if(field==='operator'&&['truthy','falsy','exists'].includes(value)) predicate.value=null; comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderAddDynamicCollection(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); if(!draft)return{status:'blocked'}; const input=(draft.inputs||[]).find((x)=>x.enabled&&x.input_id===String(ctx.inputId||'')&&x.multiplicity==='multiple'); if(!input)return{status:'blocked'}; draft.dynamic_collections=draft.dynamic_collections||[]; if(draft.dynamic_collections.some((x)=>x.input_id===input.input_id))return{status:'blocked'}; const candidate=comfyBuilderCandidateMap(state,'inputs')[input.candidate_id]||{}; const asset=['image','mask','video','audio','file'].includes(candidate.kind); const used=new Set(draft.dynamic_collections.map((x)=>x.collection_id)); let n=draft.dynamic_collections.length+1; let id=`collection_${n}`; while(used.has(id))id=`collection_${++n}`; draft.dynamic_collections.push({collection_id:id,input_id:input.input_id,mode:asset?'reference_list':'repeater',label:input.label||'Items',item_label:asset?'Reference':'Item',min_items:0,max_items:8,allow_reorder:true}); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok',collection_id:id}; },
      comfySpaceBuilderRemoveDynamicCollection(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); if(!draft)return{status:'blocked'}; draft.dynamic_collections=(draft.dynamic_collections||[]).filter((x)=>x.collection_id!==ctx.collectionId); comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchDynamicCollection(ctx) { const state=ensureAdminState(ctx.state); const draft=comfyBuilderDraft(state); const row=(draft?.dynamic_collections||[]).find((x)=>x.collection_id===ctx.collectionId); if(!row)return{status:'missing'}; row[String(ctx.field||'')]=ctx.value; comfyBuilderSetDirty(state); adminRender(ctx); return{status:'ok'}; },
      comfySpaceBuilderPatchIdentity(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        comfyBuilderPatchObject(draft.identity, String(ctx.field || ''), ctx.value); comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchRoot(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        comfyBuilderPatchObject(draft, String(ctx.field || ''), ctx.value); comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchInput(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.inputs || []).find((row) => row.candidate_id === ctx.candidateId); if (!item) return { status: 'missing' };
        const value = ctx.field === 'default' ? comfyBuilderCastValue(ctx.kind || 'text', ctx.value) : ctx.value;
        comfyBuilderPatchObject(item, String(ctx.field || ''), value); comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchConstraint(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.inputs || []).find((row) => row.candidate_id === ctx.candidateId); if (!item) return { status: 'missing' };
        item.constraints_override = item.constraints_override || {};
        const raw = String(ctx.value ?? '').trim(); item.constraints_override[ctx.field] = raw === '' ? null : Number(raw);
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchOutput(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const item = (draft.outputs || []).find((row) => row.candidate_id === ctx.candidateId); if (!item) return { status: 'missing' };
        comfyBuilderPatchObject(item, String(ctx.field || ''), ctx.value); if (!item.enabled) item.primary = false;
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderSetPrimaryOutput(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        (draft.outputs || []).forEach((row) => { row.primary = Boolean(ctx.enabled) && row.candidate_id === ctx.candidateId; if (row.primary) row.enabled = true; });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderPatchSection(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const section = (draft.sections || []).find((row) => row.section_id === ctx.sectionId); if (!section) return { status: 'missing' };
        comfyBuilderPatchObject(section, String(ctx.field || ''), ctx.value);
        if (!section.collapsible) section.default_collapsed = false;
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      comfySpaceBuilderAddSection(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        const existing = new Set((draft.sections || []).map((row) => row.section_id)); let n = (draft.sections || []).length + 1; let sectionId = `section_${n}`;
        while (existing.has(sectionId)) sectionId = `section_${++n}`;
        draft.sections.push({ section_id: sectionId, title: `Section ${n}`, description: '', order: n * 10, advanced: false, collapsible: false, default_collapsed: false });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok', section_id: sectionId };
      },
      comfySpaceBuilderRemoveSection(ctx) {
        const state = ensureAdminState(ctx.state); const draft = comfyBuilderDraft(state); if (!draft || ctx.sectionId === 'main') return { status: 'blocked' };
        draft.sections = (draft.sections || []).filter((row) => row.section_id !== ctx.sectionId);
        (draft.inputs || []).forEach((row) => { if (row.section_id === ctx.sectionId) row.section_id = 'main'; });
        comfyBuilderSetDirty(state); adminRender(ctx); return { status: 'ok' };
      },
      async comfySpaceBuilderSave(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          const result = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draft.draft_id)}`, { method: 'PUT', payload: { expected_revision: draft.revision, draft } });
          builder.workspace = result.workspace; builder.previewValues = {}; builder.publishReadiness = null; builder.lastPublication = null; builder.dirty = false; builder.notice = `Draft saved as revision ${result.revision}. Previous compiled preview and publish readiness were invalidated.`;
          builder.drafts = await adminRequestJson('/api/admin/comfy-spaces/builder/drafts');
        } catch (error) {
          builder.error = error.status === 409 ? `${error.message} Reload the draft before saving so another Builder tab is not overwritten.` : (error.message || String(error));
        } finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderSave' };
      },
      comfySpaceBuilderPreviewInput(ctx) {
        const builder = comfyBuilderState(ctx.state);
        const inputId = String(ctx.inputId || '').trim();
        if (!inputId) return { status: 'blocked' };
        builder.previewValues = builder.previewValues || {};
        builder.previewValues[inputId] = ctx.value;
        return { status: 'ok', action: 'comfySpaceBuilderPreviewInput', input_id: inputId };
      },
      async comfySpaceBuilderCompilePreview(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        if (builder.dirty) { builder.error = 'Save Draft before compiling a preview.'; adminRender(ctx); return { status: 'blocked' }; }
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          const result = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draft.draft_id)}/preview`, { method: 'POST', payload: {} });
          builder.workspace = result.workspace; builder.previewValues = {}; builder.notice = result.ok ? 'Preview contracts compiled successfully. No Comfy workflow was executed.' : 'Preview compile reported validation errors.';
          if (!result.ok) builder.error = (result.compile?.errors || []).map((item) => item.message || item.code).filter(Boolean).join(' · ') || 'Preview compilation failed validation.';
          builder.drafts = await adminRequestJson('/api/admin/comfy-spaces/builder/drafts');
        } catch (error) { builder.error = error.message || String(error); }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderCompilePreview' };
      },
      async comfySpaceBuilderCheckPublishReadiness(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        if (builder.dirty) { builder.error = 'Save Draft before checking publish readiness.'; adminRender(ctx); return { status: 'blocked' }; }
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          builder.publishReadiness = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draft.draft_id)}/publish-readiness`, { method: 'POST', payload: { expected_revision: draft.revision } });
          const gate = builder.publishReadiness?.publish_gate || {};
          builder.notice = gate.ready ? 'Publish readiness passed. Neo has not executed the workflow.' : 'Publish readiness found blockers. Nothing was written to the Space library.';
        } catch (error) { builder.error = error.message || String(error); builder.publishReadiness = null; }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderCheckPublishReadiness' };
      },
      async comfySpaceBuilderPublish(ctx) {
        const state = ensureAdminState(ctx.state); const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state); if (!draft) return { status: 'blocked' };
        if (builder.dirty) { builder.error = 'Save Draft before publishing.'; adminRender(ctx); return { status: 'blocked' }; }
        const gate = builder.publishReadiness?.publish_gate || {};
        if (!gate.ready || Number(gate.revision || 0) !== Number(draft.revision || 0)) { builder.error = 'Run publish readiness for the current saved revision first.'; adminRender(ctx); return { status: 'blocked' }; }
        const confirmed = !window.confirm || window.confirm(`Publish ${draft.identity?.space_id || 'this Space'} v${draft.identity?.version || ''}? Published versions are immutable and this does not run Comfy.`);
        if (!confirmed) return { status: 'cancelled', action: 'comfySpaceBuilderPublish' };
        builder.busy = true; builder.error = ''; builder.notice = '';
        try {
          const result = await adminRequestJson(`/api/admin/comfy-spaces/builder/drafts/${encodeURIComponent(draft.draft_id)}/publish`, { method: 'POST', payload: { expected_revision: draft.revision, confirm_publish: true } });
          builder.publishReadiness = { publish_gate: result.gate, ok: Boolean(result.ok) };
          if (!result.ok) {
            builder.error = (result.gate?.blockers || []).map((item) => item.message || item.code).filter(Boolean).join(' · ') || 'Publication was blocked.';
          } else {
            builder.workspace = result.workspace; builder.lastPublication = { ...(result.publication || {}), published: true }; builder.notice = `Published ${result.publication?.space_id || draft.identity?.space_id} v${result.publication?.version || draft.identity?.version}. No workflow was executed.`;
          }
          builder.drafts = await adminRequestJson('/api/admin/comfy-spaces/builder/drafts');
        } catch (error) { builder.error = error.message || String(error); }
        finally { builder.busy = false; adminRender(ctx); }
        return { status: builder.error ? 'failed' : 'ok', action: 'comfySpaceBuilderPublish' };
      },
    },
    renderers: {
      comfySpaceBuilderOverviewHtml(ctx) {
        const { state, NeoUI } = ctx; const builder = comfyBuilderState(state); const caps = builder.capabilities || {}; const summary = builder.drafts?.summary || {};
        if (!builder.capabilities && !builder.busy) setTimeout(() => api.actions.reloadComfySpaceBuilder(ctx).catch(() => {}), 0);
        return `${NeoUI.card({ title: 'Comfy Space Builder', description: 'Turn normal ComfyUI workflow JSON (or API JSON) into declarative Neo Space drafts without generating extensions or executing Comfy.', badge: NeoUI.statusBadge(caps.status || 'preview'), body: `${NeoUI.badgeRow([`drafts ${summary.draft_count || 0}`, `ready ${summary.ready_count || 0}`, `previews ${summary.preview_count || 0}`, `published ${summary.published_count || 0}`, 'execution off', 'publish gate on'])}${NeoUI.metaList(['Builder authoring workspace + shared dynamic renderer', 'Fail-closed package publication is enabled', 'Manifest remains binding authority; Space UI remains presentation-only', 'Custom-node/model installation and workflow execution stay outside Builder'], { code: false })}`, actions: '<button type="button" class="neo-btn" onclick="neoInvokeSurfaceModule(\'admin\',\'reloadComfySpaceBuilder\',{})">Refresh Builder</button>' })}${NeoUI.card({ title: 'Draft library', description: 'Open an existing Builder draft or import a new workflow.', body: comfyBuilderDraftListHtml(ctx) })}`;
      },
      comfySpaceBuilderWorkspaceHtml(ctx) {
        const { state, NeoUI, escapeHtml, escapeAttr } = ctx; const builder = comfyBuilderState(state); const draft = comfyBuilderDraft(state);
        if (!builder.capabilities && !builder.busy) setTimeout(() => api.actions.reloadComfySpaceBuilder(ctx).catch(() => {}), 0);
        const header = draft ? `<div class="neo-comfy-builder-workspace-head"><div><span class="neo-kicker">${escapeHtml(draft.identity?.space_id || 'draft')}</span><h3>${escapeHtml(draft.identity?.name || 'Comfy Space')}</h3><p>v${escapeHtml(draft.identity?.version || '0.1.0')} · revision ${draft.revision || 1} · ${builder.dirty ? 'unsaved changes' : 'saved'}</p></div><div class="neo-ui-toolbar"><button type="button" class="neo-btn" ${!builder.dirty || builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderSave',{})">${builder.busy ? 'Working…' : 'Save Draft'}</button><button type="button" class="neo-btn secondary" ${builder.busy ? 'disabled' : ''} onclick="neoInvokeSurfaceModule('admin','comfySpaceBuilderOpenDraft',{draftId:'${escapeAttr(draft.draft_id)}'})">Reload</button></div></div>` : `<div class="neo-comfy-builder-workspace-head"><div><span class="neo-kicker">BUILDER PREVIEW</span><h3>Comfy Space Builder</h3><p>Import a workflow or open a draft to start authoring.</p></div></div>`;
        return `<div class="neo-comfy-builder-shell">${header}${comfyBuilderStepNav(ctx)}${comfyBuilderFeedback(ctx)}<div class="neo-comfy-builder-stage">${comfyBuilderCurrentStepHtml(ctx)}</div><div class="neo-comfy-builder-boundary">${NeoUI.badgeRow(['No workflow execution', 'No asset transport', 'Package publish only', 'No generated Neo code'])}</div></div>`;
      },
      surfaceModuleArchitectureHtml(ctx) {
        const { NeoUI, state, center, escapeHtml, escapeAttr } = ctx;
        const payload = state.surfaceModules?.audit || state.surfaceModules?.status || center?.surface_module_architecture || {};
        const summary = payload.summary || {};
        const architecture = payload.architecture || payload;
        const frontend = architecture.frontend || payload.frontend || {};
        const backend = architecture.backend || payload.backend || {};
        const contracts = Array.isArray(backend.surface_contracts) ? backend.surface_contracts : [];
        const findings = Array.isArray(payload.findings) ? payload.findings : [];
        const moduleRows = (frontend.modules || []).slice(0, 12).map((item) => item);
        const contractRows = contracts.slice(0, 9).map((item) => `${item.display_name || item.surface_id}: ${item.backend?.file_count || 0} py file(s) · ${item.frontend?.module_count || 0}/${(item.frontend?.expected_modules || []).length || 1} frontend module(s)`);
        const findingRows = findings.slice(0, 6).map((item) => `${item.severity || 'info'} · ${item.area || 'architecture'} · ${item.title || ''}`);
        return `${NeoUI.card({
          title: 'Modular Surface Architecture',
          description: 'Frontend/backend modularity cockpit. Safe renderer status into surface-owned modules while keeping fallback wrappers.',
          badge: NeoUI.statusBadge(payload.status || architecture.status || 'ready'),
          body: `${NeoUI.badgeRow([`surfaces ${summary.surface_count || 0}`, `modules ${summary.frontend_module_count || 0}`, `main.py ${summary.main_py_lines || 0} lines`, `neo.js ${summary.neo_js_lines || 0} lines`])}<div class="neo-ui-toolbar"><button type="button" class="neo-btn primary" onclick="runSurfaceModuleArchitectureAudit()">Run module audit</button><button type="button" class="neo-btn" onclick="reloadSurfaceModuleArchitecture()">Refresh module status</button></div>`
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Surface module contracts', description: 'Each surface should own backend service files and migrate toward its own frontend module.', body: contractRows.length ? NeoUI.metaList(contractRows) : NeoUI.emptyState('No contracts loaded yet.', 'Refresh module status.') })}${NeoUI.card({ title: 'Frontend modules', description: frontend.module_root || 'neo_app/static/js/surfaces', body: moduleRows.length ? NeoUI.metaList(moduleRows) : NeoUI.emptyState('No frontend modules loaded.') })}</div><div>${NeoUI.card({ title: 'Audit findings', description: 'Status risks and recommended next steps.', badge: NeoUI.statusBadge(findings.some((item) => item.severity === 'high') ? 'needs modularization' : 'ready'), body: findingRows.length ? NeoUI.metaList(findingRows) : NeoUI.emptyState('No findings yet.', 'Run the module audit.') })}${NeoUI.card({ title: 'Module status status', description: 'Admin renderers are the first safe migrated slice.', body: NeoUI.metaList([architecture.policy?.modular || payload.policy?.modular || 'Modular by default.', architecture.policy?.modern || payload.policy?.modern || 'Modern shared UI by default.', 'Migrated: Admin surface architecture renderer + Modern UI renderer.', 'Fallback: legacy neo.js wrappers remain active if module render fails.'], { code: false }) })}</div></div>`;
      },

      memoryObservabilityHtml(ctx) {
        const { NeoUI, state, center } = ctx;
        const obs = state.memoryObservability || center?.memory_observability || {};
        const summary = obs.summary || {};
        const counts = summary.counts || {};
        const memory = obs.memory_inspector || {};
        const retrieval = obs.retrieval_inspector || {};
        const control = obs.control_center_inspector || {};
        const roleplay = obs.roleplay_scene_inspector || {};
        const contracts = obs.prompt_contract_inspector || {};
        const timeline = obs.timeline_inspector || {};
        const traceLines = (control.recent_traces || []).slice(0, 6).map((item) => `${item.controller || 'controller'} · ${item.intent || 'intent'} · ${item.prompt_contract_id || 'contract'} · ${item.status || 'recorded'}`);
        const retrievalLines = (retrieval.recent_access || retrieval.recent_legacy_traces || []).slice(0, 6).map((item) => `${item.consumer || item.profile || 'retrieval'} · ${item.query || 'no query'} · ${item.result_count || (item.result_ids || []).length || 0} result(s)`);
        const roleplayLines = [
          `Scene packets: ${(roleplay.scene_packets || []).length}`,
          `Character states: ${(roleplay.character_states || []).length}`,
          `Relationship states: ${(roleplay.relationship_states || []).length}`,
          `Unresolved threads: ${(roleplay.unresolved_threads || []).length}`,
        ];
        const timelineLines = (timeline.items || []).slice(0, 8).map((item) => `${item.kind || 'event'} · ${item.surface || 'global'} · ${item.title || ''}`);
        return `${NeoUI.card({
          title: 'Memory + Control Center Observability',
          description: 'Admin-owned memory cockpit renderer migrated out of neo.js. Read-only panels show memory, retrieval, traces, roleplay state, prompt contracts, and timeline flow.',
          badge: NeoUI.statusBadge(obs.status || 'ready'),
          body: `${NeoUI.badgeRow([`events ${counts.events || 0}`, `fragments ${counts.fragments || 0}`, `summaries ${counts.summaries || 0}`, `embeddings ${counts.embeddings || 0}`, `traces ${counts.control_traces || 0}`])}`,
          actions: '<button type="button" class="neo-btn primary" onclick="reloadMemoryObservability()">Refresh observability</button>'
        })}${NeoUI.card({ title: 'Memory Inspector Snapshot', description: 'Recent fragments, summaries, facts, and embedding status.', body: `${NeoUI.badgeRow((memory.embedding_status || []).map((item) => `${item.status || 'unknown'} ${item.count || 0}`))}${NeoUI.metaList((memory.recent_fragments || []).slice(0, 6).map((item) => `${item.memory_type || 'fragment'} · ${item.title || item.fragment_id || ''}`))}` })}${NeoUI.card({ title: 'Retrieval Inspector Snapshot', description: 'Recent retrieval access and trace visibility.', body: retrievalLines.length ? NeoUI.metaList(retrievalLines) : NeoUI.emptyState('No retrieval observations yet.', 'Run Assistant or Roleplay retrieval to create access logs/traces.') })}${NeoUI.card({ title: 'Control Center Trace Inspector', description: 'Recent Assistant/Roleplay planning traces and selected prompt contracts.', body: traceLines.length ? NeoUI.metaList(traceLines) : NeoUI.emptyState('No Control Center traces yet.', 'Run Assistant or Roleplay to create traces.') })}${NeoUI.card({ title: 'Roleplay Scene Inspector', description: 'Scene packets, character state, relationship state, unresolved threads, and fragment counts.', body: NeoUI.metaList(roleplayLines) })}${NeoUI.card({ title: 'Prompt Contract Inspector', description: `${(contracts.contracts || []).length} contract(s) available.`, body: NeoUI.metaList((contracts.contracts || []).slice(0, 8).map((item) => `${item.contract_id || 'contract'} · ${item.surface || 'surface'} · ${item.intent || ''}`)) })}${NeoUI.card({ title: 'Memory Timeline', description: 'Recent cross-layer memory/control events.', body: timelineLines.length ? NeoUI.metaList(timelineLines) : NeoUI.emptyState('No timeline items yet.') })}`;
      },
      surfaceModuleStatusHtml(ctx) {
        const { NeoUI, state, center } = ctx;
        const payload = state.surfaceStatus?.audit?.status_runtime || state.surfaceStatus?.status || center?.surface_status || {};
        const summary = payload.summary || {};
        const modules = Array.isArray(payload.modules) ? payload.modules : [];
        const findings = Array.isArray(state.surfaceStatus?.audit?.findings) ? state.surfaceStatus.audit.findings : [];
        const moduleLines = modules.slice(0, 10).map((item) => `${item.surface_id || item.surfaceId || 'surface'} · ${item.status || 'status'} · ${(item.migrated_areas || []).length} migrated area(s)`);
        const findingLines = findings.slice(0, 6).map((item) => `${item.severity || 'info'} · ${item.area || 'status'} · ${item.title || ''}`);
        return `${NeoUI.card({
          title: 'Admin Memory Cockpit',
          description: 'Admin Memory cockpit renderers are available through the Admin surface module.',
          badge: NeoUI.statusBadge(payload.status || 'ready'),
          body: `${NeoUI.badgeRow([`surfaces ${summary.surface_module_count || 0}`, `preview ${summary.partial_migrated_surface_count || 0}`, `planned ${summary.status_shell_surface_count || 0}`, `areas ${summary.migrated_area_count || 0}`, `neo.js ${summary.neo_js_lines || 0} lines`])}<div class="neo-ui-toolbar"><button type="button" class="neo-btn primary" onclick="runSurfaceModuleStatusAudit()">Run surface audit</button><button type="button" class="neo-btn" onclick="reloadSurfaceModuleStatus()">Refresh surface status</button></div>`
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Registered surface modules', description: 'Loaded module scripts and owned workspace areas.', body: moduleLines.length ? NeoUI.metaList(moduleLines) : NeoUI.emptyState('No surface modules loaded yet.') })}</div><div>${NeoUI.card({ title: 'Surface findings', description: 'Surface status and next recommendations.', body: findingLines.length ? NeoUI.metaList(findingLines) : NeoUI.emptyState('No surface findings yet.', 'Run surface audit.') })}${NeoUI.card({ title: 'Module policy', description: 'Admin Memory Cockpit uses safe modular ownership.', body: NeoUI.metaList([payload.policy?.safe_status || 'Keep surface updates incremental and fallback-safe.', payload.policy?.no_big_bang_rewrite || 'Avoid full surface rewrites during public preview stabilization.', 'Keep Admin Memory panels stable and action handlers reviewable.'], { code: false }) })}</div></div>`;
      },
      assistantBrainWorkspaceHtml(ctx) {
        const { NeoUI, state, center, escapeHtml, escapeAttr } = ctx;
        const brain = state.assistantBrain || {};
        const status = brain.status || center?.assistant_brain || {};
        const dashboard = brain.dashboard || {};
        const workspaces = Array.isArray(dashboard.workspaces) ? dashboard.workspaces : (Array.isArray(brain.workspaces?.workspaces) ? brain.workspaces.workspaces : []);
        const active = dashboard.active_workspace || workspaces.find((item) => item.workspace_id === brain.activeWorkspaceId) || workspaces[0] || {};
        const preview = Array.isArray(dashboard.memory_preview) ? dashboard.memory_preview : [];
        const traces = Array.isArray(dashboard.recent_traces) ? dashboard.recent_traces : [];
        const workspaceButtons = workspaces.map((workspace) => {
          const selected = active.workspace_id === workspace.workspace_id;
          const stats = workspace.memory_stats || {};
          const surfaceId = workspace.surface_id || workspace.surface || 'global';
          const scopeId = workspace.scope_id || workspace.project_id || 'general';
          const projectId = workspace.delivery_project_id || workspace.canonical_project_id || 'none';
          return `<button type="button" class="neo-ui-record-card ${selected ? 'selected' : ''}" onclick="selectAssistantBrainWorkspace('${escapeAttr(workspace.workspace_id || '')}')"><strong>${escapeHtml(workspace.name || workspace.workspace_id || 'Scope')}</strong><span>scope ${escapeHtml(scopeId)} · surface ${escapeHtml(surfaceId)} · ${stats.fragments || 0} fragments</span><small>${escapeHtml(projectId !== 'none' ? `delivery project ${projectId}` : 'no linked delivery project')}</small></button>`;
        }).join('');
        const memoryLines = preview.slice(0, 8).map((item) => `${item.memory_type || 'memory'} · ${item.title || item.fragment_id || ''} · ${item.content_preview || ''}`);
        const traceLines = traces.slice(0, 6).map((trace) => `${trace.intent || 'intent'} · ${trace.context_count || 0} ctx · ${trace.status || 'status'} · ${trace.created_at || ''}`);
        const lastContext = brain.lastContext || {};
        const activeSurfaceId = active.surface_id || active.surface || 'global';
        const activeScopeId = active.scope_id || active.project_id || 'general';
        const activeProjectId = active.delivery_project_id || active.canonical_project_id || 'none';
        return `${NeoUI.card({
          title: 'Assistant Scope Readout',
          description: 'Read-only Admin visibility for Assistant scopes, memory previews, and Control Center traces. Edit or activate scopes in the Assistant surface.',
          badge: NeoUI.statusBadge(status.status || 'ready'),
          body: `${NeoUI.badgeRow([`scopes ${status.workspace_count || workspaces.length || 0}`, `preview ${active.name || 'none'}`, `surface ${activeSurfaceId}`])}<div class="neo-ui-toolbar"><button type="button" class="neo-btn primary" onclick="reloadAssistantBrainWorkspace()">Refresh scope readout</button><button type="button" class="neo-btn" onclick="buildAssistantBrainContext()">Build context preview</button><button type="button" class="neo-btn" onclick="setActiveSubtab('assistant','projects')">Open Assistant Scopes</button></div>`
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Scope readout', description: 'Selecting a card changes only this Admin diagnostic preview. Scope activation and editing remain owned by the Assistant surface.', body: workspaceButtons || NeoUI.emptyState('No Assistant scopes loaded yet.', 'Refresh scope readout.') })}</div><div>${NeoUI.card({ title: active.name || 'Selected scope', description: active.description || 'Canonical scope identity and scoped memory preview.', body: `${NeoUI.metaList([`Scope: ${activeScopeId}`, `Surface: ${activeSurfaceId}`, `Delivery project: ${activeProjectId}`, `Legacy workspace: ${active.workspace_id || 'none'}`, `Memory lanes: ${(active.memory_lanes || []).join(', ') || 'none'}`])}${NeoUI.metaList(memoryLines, { empty: 'No scoped memory preview yet.' })}` })}${NeoUI.card({ title: 'Recent Assistant traces', description: 'Recent Assistant Control Center plans for this scope.', body: traceLines.length ? NeoUI.metaList(traceLines) : NeoUI.emptyState('No Assistant traces yet.') })}${NeoUI.card({ title: 'Last context preview', description: 'The compact Assistant brief prepared for backend handoff.', body: lastContext?.brief ? NeoUI.codeBlock(lastContext.brief, 'admin-assistant-brain-context-code') : NeoUI.emptyState('No context preview built yet.', 'Click Build context preview.') })}</div></div>`;
      },
      controlCenterReviewHtml(ctx) {
        const { NeoUI, state, center, escapeHtml, escapeAttr, detailMode } = ctx;
        const review = state.controlCenterReview || {};
        const status = review.status || center?.control_center_review || {};
        const dashboard = review.dashboard || {};
        const traces = Array.isArray(dashboard.traces) ? dashboard.traces : (status.recent_traces || []);
        const selected = dashboard.selected_trace || null;
        const filters = review.filters || {};
        const traceButtons = traces.slice(0, 12).map((trace) => {
          const active = selected?.trace_id === trace.trace_id || review.selectedTraceId === trace.trace_id;
          const safety = trace.safety_guard || {};
          const label = `${trace.controller || 'controller'} · ${trace.intent || 'intent'} · ${trace.context_count || 0} ctx · ${trace.prompt_contract_id || 'contract'}`;
          const sub = `${trace.status || 'recorded'} · ${trace.created_at || ''}${safety.rejected_count ? ` · blocked ${safety.rejected_count}` : ''}`;
          return `<button type="button" class="neo-ui-record-card ${active ? 'selected' : ''}" onclick="selectControlCenterReviewTrace('${escapeAttr(trace.trace_id)}')"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(sub)}</span><small>${escapeHtml(trace.user_preview || trace.trace_id || '')}</small></button>`;
        }).join('');
        const selectedContext = selected?.selected_context || {};
        const selectedItems = Array.isArray(selectedContext.items) ? selectedContext.items : [];
        const contextLines = selectedItems.slice(0, 8).map((item) => `${item.memory_type || 'memory'} · ${item.title || item.fragment_id || ''} · score ${item.score ?? 'n/a'} · ${item.trust_level || 'trust?'}`);
        const related = selected?.related || {};
        const writebacks = Array.isArray(related.writebacks) ? related.writebacks : [];
        const violations = Array.isArray(related.safety_violations) ? related.safety_violations : [];
        const retrievalAccess = Array.isArray(related.retrieval_access) ? related.retrieval_access : [];
        const hints = Array.isArray(selected?.review_hints) ? selected.review_hints : [];
        const hintLines = hints.map((hint) => `${hint.level || 'info'} · ${hint.message || ''}`);
        const selectedBody = selected ? `${NeoUI.badgeRow([selected.controller, selected.surface, selected.intent, selected.prompt_contract_id, selected.status])}
          ${NeoUI.metaList([`Trace: ${selected.trace_id}`, `Project: ${selected.project_id || 'none'}`, `Scope: ${selected.scope_id || 'none'}`, `Backend: ${selected.backend_profile_id || 'not set'}`, `Selection: ${selectedContext.selection_mode || 'unknown'} · ${(selectedContext.item_count ?? selectedItems.length) || 0} item(s)`, `Retrieval trace: ${selectedContext.retrieval_trace_id || 'none'}`])}
          ${NeoUI.card({ title: 'Review hints', description: 'What the cockpit thinks you should inspect first.', body: NeoUI.metaList(hintLines) })}
          ${NeoUI.card({ title: 'Selected context', description: 'The exact compact memory context selected by Control Center after retrieval/rerank/safety.', body: contextLines.length ? NeoUI.metaList(contextLines) : NeoUI.emptyState('No selected context reached the prompt.', 'This is a likely cause of generic or hallucinated answers.') })}
          ${NeoUI.card({ title: 'Retrieval diagnostics', description: 'Related retrieval access records and reranker trace linkage.', body: retrievalAccess.length ? NeoUI.metaList(retrievalAccess.slice(0, 6).map((item) => `${item.consumer || 'consumer'} · ${item.query || 'query'} · ${(item.result_ids || []).length} result(s) · ${item.created_at || ''}`)) : NeoUI.emptyState('No related retrieval access records found.') })}
          ${NeoUI.card({ title: 'Safety guard', description: 'Sandbox/cross-scope blocks related to this trace.', badge: NeoUI.statusBadge(violations.length ? 'needs review' : 'ready'), body: violations.length ? NeoUI.metaList(violations.slice(0, 6).map((item) => `${item.severity || 'warn'} · ${item.check_type || 'check'} · ${item.message || ''}`)) : NeoUI.emptyState('No related safety violations.') })}
          ${NeoUI.card({ title: 'Prompt contract + validation', description: 'The behavior contract selected before backend generation.', body: `${NeoUI.metaList([`Contract: ${selected.prompt_contract_id || 'none'}`, `Validation: ${selected.validation?.status || 'planned'}`, `Checks: ${(selected.validation?.checks || []).join(', ') || 'none'}`])}${detailMode === 'expert' ? NeoUI.codeBlock(selected.prompt_contract || {}, 'admin-control-center-review-contract') : ''}` })}
          ${NeoUI.card({ title: 'Writeback candidates', description: 'Memory evolution candidates linked to this trace.', body: writebacks.length ? NeoUI.metaList(writebacks.slice(0, 8).map((item) => `${item.status || 'queued'} · ${item.risk_level || 'risk'} · ${item.memory_type || 'memory'} · support ${item.support_count || 1}/${item.support_threshold || 1}${item.contradiction_state ? ` · ${item.contradiction_state}` : ''} · ${item.title || item.writeback_id}`)) : NeoUI.emptyState('No writeback candidates linked yet.') })}
          ${NeoUI.card({ title: 'Review decision', description: 'Record an Admin review note without mutating memory content.', actions: `<button type="button" class="neo-btn" onclick="reviewControlCenterTrace('good')">Mark good</button><button type="button" class="neo-btn" onclick="reviewControlCenterTrace('needs_fix')">Needs fix</button><button type="button" class="neo-btn" onclick="reviewControlCenterTrace('scope_issue')">Scope issue</button><button type="button" class="neo-btn" onclick="reviewControlCenterTrace('memory_gap')">Memory gap</button>`, body: selected.latest_review ? NeoUI.metaList([`Latest review: ${selected.latest_review.decision || 'reviewed'}`, `Note: ${selected.latest_review.note || ''}`, `At: ${selected.latest_review.created_at || ''}`]) : NeoUI.emptyState('No review recorded for this trace.') })}` : NeoUI.emptyState('No Control Center trace selected.', 'Run Assistant or Roleplay, then refresh the cockpit.');
        return `${NeoUI.card({
          title: 'Control Center Trace Review',
          description: 'Inspect what Control Center selected, blocked, contracted, and planned before backend generation.',
          badge: NeoUI.statusBadge(status.status || dashboard.status || 'ready'),
          body: `${NeoUI.badgeRow([`traces ${status.trace_count || dashboard.summary?.trace_count || traces.length || 0}`, `reviews ${status.review_count || 0}`, `open safety ${status.open_safety_violations || 0}`, `writebacks ${status.writeback_count || 0}`])}<div class="neo-ui-field-grid"><div><label>Controller</label><select id="neo-cc-review-controller"><option value="">All</option><option value="assistant" ${filters.controller === 'assistant' ? 'selected' : ''}>Assistant</option><option value="roleplay" ${filters.controller === 'roleplay' ? 'selected' : ''}>Roleplay</option></select></div><div><label>Surface</label><input id="neo-cc-review-surface" value="${escapeAttr(filters.surface || '')}" placeholder="assistant / roleplay / image"></div><div><label>Search traces</label><input id="neo-cc-review-query" value="${escapeAttr(filters.query || '')}" placeholder="intent, contract, user text"></div></div>`,
          actions: '<button type="button" class="neo-btn primary" onclick="reloadControlCenterReview()">Refresh cockpit</button>'
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Trace queue', description: 'Latest Control Center traces. Select one to inspect the full route.', body: traceButtons || NeoUI.emptyState('No traces yet.', 'Run Assistant or Roleplay to create traces.') })}</div><div>${selectedBody}</div></div>`;
      },
      modernUiSystemHtml(ctx) {
        const { NeoUI, state, center } = ctx;
        const payload = state.modernUi?.audit?.modern_ui || state.modernUi?.status || center?.modern_ui || {};
        const summary = payload.summary || {};
        const principles = Array.isArray(payload.principles) ? payload.principles : [];
        const markers = Object.entries(payload.css_markers || {}).slice(0, 6).map(([key, ok]) => `${ok ? 'Ready' : 'Missing'} · ${key}`);
        const jsMarkers = Object.entries(payload.js_markers || {}).slice(0, 6).map(([key, ok]) => `${ok ? 'Ready' : 'Missing'} · ${key}`);
        const auditFindings = Array.isArray(state.modernUi?.audit?.findings) ? state.modernUi.audit.findings : [];
        const findingRows = auditFindings.slice(0, 6).map((item) => `${item.severity || 'info'} · ${item.area || 'ui'} · ${item.title || ''}`);
        const tokenLines = Object.entries(payload.design_tokens || {}).map(([key, value]) => `${key}: ${value}`);
        return `${NeoUI.card({
          title: 'UI System Readiness',
          description: 'Shared design-token, focus, status, density, and diagnostic clarity layer.',
          badge: NeoUI.statusBadge(payload.status || 'ready'),
          body: `${NeoUI.badgeRow([`CSS ${summary.css_lines || 0} lines`, `neo.js ${summary.neo_js_lines || 0} lines`, `modules ${summary.surface_module_count || 0}`, `checks ${summary.ready_checks || 0}/${summary.total_checks || 0}`])}<div class="neo-ui-toolbar"><button type="button" class="neo-btn primary" onclick="runModernUiSystemAudit()">Run UI audit</button><button type="button" class="neo-btn" onclick="reloadModernUiSystem()">Refresh UI status</button></div>`
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Modern principles', description: 'What the UI should feel like across Neo surfaces.', body: principles.length ? NeoUI.metaList(principles, { code: false }) : NeoUI.emptyState('No principles loaded.') })}${NeoUI.card({ title: 'Design tokens', description: 'Shared CSS token references for future surface modernization.', body: tokenLines.length ? NeoUI.metaList(tokenLines) : NeoUI.emptyState('No token map loaded.') })}</div><div>${NeoUI.card({ title: 'Readiness markers', description: 'Checks for modern UI helpers in CSS and JS.', body: NeoUI.metaList([...markers, ...jsMarkers]) })}${NeoUI.card({ title: 'Audit findings', description: 'This audit flags monolith risk without blocking current UI.', body: findingRows.length ? NeoUI.metaList(findingRows) : NeoUI.emptyState('No findings yet.', 'Run the UI audit.') })}</div></div>`;
      },
    },
  };

  if (runtime?.register) runtime.register('admin', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules.admin = api;
  }
})();
