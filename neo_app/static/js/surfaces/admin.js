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

  const api = {
    surfaceId: 'admin',
    area: 'memory',
    status: 'ready',
    migratedAreas: ['render.surface_module_architecture', 'render.modern_ui_system', 'render.memory_observability', 'render.surface_module_status', 'render.assistant_brain_workspace', 'render.control_center_review', 'action.memory_observability.refresh', 'action.surface_module_status.refresh', 'action.surface_module_status.audit', 'action.surface_module_architecture.refresh', 'action.surface_module_architecture.audit', 'action.modern_ui.refresh', 'action.modern_ui.audit', 'action.assistant_brain.refresh', 'action.assistant_brain.select', 'action.assistant_brain.activate', 'action.assistant_brain.context', 'action.control_center_review.refresh', 'action.control_center_review.select_trace', 'action.control_center_review.review'],
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
        if (!workspaceId) throw new Error('No Assistant Brain workspace selected.');
        state.assistantBrain.lastActivation = await adminPostJson('/api/assistant/brain/activate', { workspace_id: workspaceId });
        await api.actions.reloadAssistantBrainWorkspace(ctx);
        return { status: 'ok', action: 'activateAssistantBrainWorkspace', workspace_id: workspaceId };
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
    },
    renderers: {
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
          return `<button type="button" class="neo-ui-record-card ${selected ? 'selected' : ''}" onclick="selectAssistantBrainWorkspace('${escapeAttr(workspace.workspace_id || '')}')"><strong>${escapeHtml(workspace.name || workspace.workspace_id || 'Workspace')}</strong><span>${escapeHtml(workspace.surface || 'assistant')} · ${escapeHtml(workspace.project_id || 'project')} · ${stats.fragments || 0} fragments</span><small>${escapeHtml((workspace.memory_lanes || []).slice(0, 4).join(', '))}</small></button>`;
        }).join('');
        const memoryLines = preview.slice(0, 8).map((item) => `${item.memory_type || 'memory'} · ${item.title || item.fragment_id || ''} · ${item.content_preview || ''}`);
        const traceLines = traces.slice(0, 6).map((trace) => `${trace.intent || 'intent'} · ${trace.context_count || 0} ctx · ${trace.status || 'status'} · ${trace.created_at || ''}`);
        const lastContext = brain.lastContext || {};
        return `${NeoUI.card({
          title: 'Assistant Brain Workspace',
          description: 'Assistant Brain workspace renderer is available in Admin.',
          badge: NeoUI.statusBadge(status.status || 'ready'),
          body: `${NeoUI.badgeRow([`workspaces ${status.workspace_count || workspaces.length || 0}`, `active ${active.name || 'none'}`, `surface ${active.surface || 'assistant'}`])}<div class="neo-ui-toolbar"><button type="button" class="neo-btn primary" onclick="reloadAssistantBrainWorkspace()">Refresh brain workspaces</button><button type="button" class="neo-btn" onclick="activateAssistantBrainWorkspace()">Set active Assistant project</button><button type="button" class="neo-btn" onclick="buildAssistantBrainContext()">Build context preview</button></div>`
        })}<div class="neo-ui-grid two"><div>${NeoUI.card({ title: 'Built-in workspaces', description: 'Each workspace is a sandboxed Assistant project mapped to a Neo surface.', body: workspaceButtons || NeoUI.emptyState('No Assistant brain workspaces loaded yet.', 'Refresh brain workspaces.') })}</div><div>${NeoUI.card({ title: active.name || 'Active workspace', description: active.description || 'Workspace details and scoped memory preview.', body: `${NeoUI.metaList([`Workspace: ${active.workspace_id || 'none'}`, `Project: ${active.project_id || 'none'}`, `Surface: ${active.surface || 'assistant'}`, `Memory lanes: ${(active.memory_lanes || []).join(', ') || 'none'}`])}${NeoUI.metaList(memoryLines, { empty: 'No workspace memory preview yet.' })}` })}${NeoUI.card({ title: 'Recent Assistant traces', description: 'Recent Assistant Control Center plans for this workspace.', body: traceLines.length ? NeoUI.metaList(traceLines) : NeoUI.emptyState('No Assistant brain traces yet.') })}${NeoUI.card({ title: 'Last context preview', description: 'The compact Assistant brief prepared for backend handoff.', body: lastContext?.brief ? NeoUI.codeBlock(lastContext.brief, 'admin-assistant-brain-context-code') : NeoUI.emptyState('No context preview built yet.', 'Click Build context preview.') })}</div></div>`;
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
          ${NeoUI.card({ title: 'Writeback candidates', description: 'Memory evolution candidates linked to this trace.', body: writebacks.length ? NeoUI.metaList(writebacks.slice(0, 8).map((item) => `${item.status || 'queued'} · ${item.risk_level || 'risk'} · ${item.memory_type || 'memory'} · ${item.title || item.writeback_id}`)) : NeoUI.emptyState('No writeback candidates linked yet.') })}
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
