// Neo Studio V2 surface module: assistant
// Safe Assistant deep panel status. Legacy neo.js wrappers remain as fallbacks while this module owns Assistant chat + deep panels/action lanes.
(function () {
  function assistantThreadScrollSnapshot() {
    const thread = document.querySelector('.assistant-thread');
    if (!thread) return null;
    const distanceFromBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    return {
      scrollTop: thread.scrollTop,
      scrollHeight: thread.scrollHeight,
      clientHeight: thread.clientHeight,
      nearBottom: distanceFromBottom <= 96,
    };
  }

  function assistantRestoreThreadScroll(snapshot, options = {}) {
    const restore = () => {
      const thread = document.querySelector('.assistant-thread');
      if (!thread) return;
      const maxTop = Math.max(0, thread.scrollHeight - thread.clientHeight);
      if (options.stickToBottom || snapshot?.nearBottom || (!snapshot && options.preferBottom)) {
        thread.scrollTop = maxTop;
        return;
      }
      if (snapshot) {
        thread.scrollTop = Math.min(snapshot.scrollTop, maxTop);
      }
    };
    window.requestAnimationFrame?.(restore) || window.setTimeout(restore, 0);
    window.setTimeout(restore, 0);
  }

  function assistantRender(ctx, options = {}) {
    const snapshot = assistantThreadScrollSnapshot();
    if (typeof ctx.render === 'function') ctx.render();
    assistantRestoreThreadScroll(snapshot, options);
  }

  async function assistantFetchJson(ctx, url, options = {}) {
    if (typeof ctx.assistantFetchJson === 'function') return ctx.assistantFetchJson(url, options);
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.detail || payload.message || 'Assistant request failed');
    return payload;
  }

  function assistantState(ctx) {
    const a = typeof ctx.assistantState === 'function' ? ctx.assistantState() : (ctx.state.assistant = ctx.state.assistant || {});
    if (!Array.isArray(a.pendingAttachments)) a.pendingAttachments = [];
    return a;
  }

  function assistantBackendProfile(ctx) {
    const state = ctx.state || {};
    const profiles = state.backendProfiles?.profiles || [];
    const defaults = state.backendProfiles?.defaults || {};
    const selected = state.activeBackendProfileIdsBySurface?.assistant || state.activeBackendProfileIdsBySurface?.text || defaults.assistant || defaults.text || '';
    return profiles.find((profile) => profile.profile_id === selected) || profiles.find((profile) => profile.profile_id === defaults.assistant) || profiles.find((profile) => profile.profile_id === defaults.text) || null;
  }

  function assistantBackendConnected(ctx) {
    const profile = assistantBackendProfile(ctx);
    if (!profile || profile.enabled === false) return false;
    const status = String(profile.runtime_status || profile.runtime?.status || profile.profile_status || '').toLowerCase();
    return ['connected', 'online', 'ready', 'available'].includes(status) && profile.runtime?.reachable !== false;
  }

  function assistantBackendBlockedMessage(ctx) {
    const profile = assistantBackendProfile(ctx);
    if (!profile) return 'No Assistant text backend profile is configured.';
    const status = profile.runtime_status || profile.runtime?.status || 'not_checked';
    return `Backend not connected. Click Connect/Test for ${profile.display_name || profile.profile_id} before sending Assistant messages. Current status: ${status}.`;
  }

  function activeProject(ctx) {
    if (typeof ctx.assistantActiveProject === 'function') return ctx.assistantActiveProject();
    const a = assistantState(ctx);
    return (a.projects || []).find((project) => project.project_id === a.selectedProjectId) || (a.projects || [])[0] || { project_id: 'general', name: 'General' };
  }


  function assistantProjectSurface(ctx, project = null) {
    const active = project || activeProject(ctx) || {};
    const explicit = String(active.surface || '').trim();
    if (explicit) return explicit;
    const projectId = String(active.project_id || assistantState(ctx).selectedProjectId || '').trim();
    const map = {
      image_workspace: 'image',
      video_workspace: 'video',
      voice_workspace: 'voice',
      prompt_captioning_workspace: 'prompt_captioning',
      roleplay_workspace: 'roleplay',
      neo_development_workspace: 'admin',
    };
    if (map[projectId]) return map[projectId];
    return ctx.state?.activeSurfaceId || ctx.state?.activeSurface || 'assistant';
  }

  function compactExtensionRecordsForSurface(ctx, surface) {
    const records = Array.isArray(ctx.state?.extensions?.extensions) ? ctx.state.extensions.extensions : [];
    return records
      .filter((record) => (record?.manifest?.surface || record?.surface) === surface)
      .slice(0, 40)
      .map((record) => ({
        extension_id: record.extension_id || record.id || record.manifest?.id || '',
        name: record.manifest?.name || record.name || record.extension_id || record.id || '',
        enabled: record.enabled !== false && record.registry_enabled !== false,
        workflow_enabled: record.workflow_enabled,
        version: record.manifest?.version || record.version || '',
        workspace_apps: record.manifest?.workspace_apps || [],
        workflow_modes: record.manifest?.workflow_modes || record.manifest?.subtabs || [],
        mount_slots: record.manifest?.mount_slots || [],
        settings: record.settings || record.config || {},
      }));
  }

  function assistantSurfaceProjectSnapshot(ctx, surface = '') {
    const s = ctx.state || {};
    const resolvedSurface = surface || assistantProjectSurface(ctx);
    const runtime = s.surfaceRuntime?.[resolvedSurface] || {};
    const snapshot = {
      schema_id: 'neo.assistant.client_surface_project_snapshot.v1',
      surface: resolvedSurface,
      active_surface_id: s.activeSurfaceId || s.activeSurface || '',
      active_subtab_id: s.activeSubtabId || s.activeSubtab || '',
      detailMode: s.detailMode || 'guided',
      surfaceRuntime: s.surfaceRuntime || {},
      activeBackendProfileId: s.activeBackendProfileId || '',
      activeBackendProfileIdsBySurface: s.activeBackendProfileIdsBySurface || {},
      extensionWorkflowApplications: s.extensionWorkflowApplications || {},
      extensions: compactExtensionRecordsForSurface(ctx, resolvedSurface),
    };
    if (resolvedSurface === 'image') {
      snapshot.imageDraft = s.imageDraft || {};
      snapshot.activeSavedOutputFileId = s.activeSavedOutputFileId || '';
      snapshot.activeSavedResultMetadata = s.activeSavedResultMetadata || null;
      snapshot.activeSavedInspectorMediaId = s.activeSavedInspectorMediaId || '';
    } else if (resolvedSurface === 'video') {
      snapshot.videoDraft = s.videoDraft || {};
      snapshot.videoLastGenerate = s.videoLastGenerate || null;
      snapshot.videoRunProgress = s.videoRunProgress || null;
    } else if (resolvedSurface === 'prompt_captioning') {
      snapshot.promptCaptioning = s.promptCaptioning || {};
    } else if (resolvedSurface === 'roleplay') {
      snapshot.roleplayRuntime = s.roleplayRuntime || null;
      snapshot.roleplayRuntimePreparedPacket = s.roleplayRuntimePreparedPacket || null;
      snapshot.roleplayStudio = s.roleplayStudio || null;
      snapshot.roleplayForge = s.roleplayForge || null;
      snapshot.roleplayStories = s.roleplayStories || null;
      snapshot.roleplayEngineBridge = s.roleplayEngineBridge || null;
      snapshot.roleplayScopedCompile = s.roleplayScopedCompile || null;
      snapshot.roleplayScopedCompilePlan = s.roleplayScopedCompilePlan || null;
      snapshot.roleplayScopedCompileLastRun = s.roleplayScopedCompileLastRun || null;
    } else if (resolvedSurface === 'voice') {
      snapshot.voiceDraft = s.voiceDraft || {};
      snapshot.voiceLastJob = s.voiceLastJob || null;
      snapshot.voiceHistory = s.voiceHistory || null;
    }
    snapshot.surface_runtime = runtime;
    return snapshot;
  }

  function escapeHtml(ctx, value) { return typeof ctx.escapeHtml === 'function' ? ctx.escapeHtml(value) : String(value ?? ''); }
  function escapeAttr(ctx, value) { return typeof ctx.escapeAttr === 'function' ? ctx.escapeAttr(value) : String(value ?? '').replaceAll('"', '&quot;'); }

  function assistantAttachmentUrl(attachment = {}) {
    return attachment.url || (attachment.attachment_id ? `/api/assistant/attachments/${encodeURIComponent(attachment.attachment_id)}` : '');
  }

  function assistantImageAttachmentThumbHtml(ctx, attachment = {}, removable = false) {
    const href = assistantAttachmentUrl(attachment);
    if (!href || attachment.kind !== 'image') return '';
    const name = attachment.filename || attachment.stored_filename || attachment.attachment_id || 'image attachment';
    const remove = removable ? `<button type="button" aria-label="Remove attachment" onclick="assistantRemovePendingAttachment('${escapeAttr(ctx, attachment.attachment_id || '')}')">×</button>` : '';
    return `<span class="assistant-attachment-thumb ${removable ? 'pending' : 'sent'}"><a href="${escapeAttr(ctx, href)}" target="_blank" rel="noopener"><img src="${escapeAttr(ctx, href)}" alt="${escapeAttr(ctx, name)}"><small>${escapeHtml(ctx, name)}</small></a>${remove}</span>`;
  }

  async function assistantReadEventStream(response, onEvent) {
    if (!response.ok) {
      let detail = '';
      try { detail = (await response.json()).detail || ''; } catch (_) { detail = await response.text().catch(() => ''); }
      throw new Error(detail || `Assistant stream failed (${response.status})`);
    }
    if (!response.body || typeof response.body.getReader !== 'function') {
      const text = await response.text();
      text.split(/\n\n+/).forEach((block) => {
        const dataLine = block.split('\n').find((line) => line.startsWith('data:'));
        if (!dataLine) return;
        try { onEvent(JSON.parse(dataLine.slice(5).trim())); } catch (_) {}
      });
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const flushBlock = (block) => {
      const lines = block.split(/\r?\n/);
      const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
      if (!data) return;
      try { onEvent(JSON.parse(data)); } catch (_) {}
    };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() || '';
      parts.forEach(flushBlock);
    }
    buffer += decoder.decode();
    if (buffer.trim()) flushBlock(buffer);
  }

  function assistantApplyStreamEvent(ctx, event) {
    const a = assistantState(ctx);
    const session = a.activeSession || (a.activeSession = { messages: [] });
    if (event.session) {
      a.activeSession = event.session;
      if (!Array.isArray(a.activeSession.messages)) a.activeSession.messages = [];
    }
    if (event.sessions) a.sessions = event.sessions;
    if (event.context_pack) a.contextPreview = event.context_pack;
    const active = a.activeSession || session;
    active.messages = Array.isArray(active.messages) ? active.messages : [];
    if (event.type === 'delta') {
      let streaming = active.messages.find((message) => message.message_id === a.streamingMessageId);
      if (!streaming) {
        streaming = { message_id: `stream_${event.run_id || Date.now()}`, role: 'assistant', text: '', created_at: new Date().toISOString(), status: 'streaming', streaming: true };
        a.streamingMessageId = streaming.message_id;
        active.messages.push(streaming);
      }
      streaming.text = `${streaming.text || ''}${event.text || event.token || ''}`;
      streaming.status = 'streaming';
      a.status = 'Assistant is streaming…';
      assistantRender(ctx, { preferBottom: true });
      return;
    }
    if (event.type === 'start' || event.type === 'user_message') {
      a.status = 'Assistant stream started.';
      assistantRender(ctx, { stickToBottom: true });
      return;
    }
    if (event.type === 'status') {
      a.status = event.message || event.status || 'Assistant stream running…';
      assistantRender(ctx);
      return;
    }
    if (event.type === 'error') {
      a.status = event.message || event.error || 'Assistant stream failed.';
      assistantRender(ctx);
      return;
    }
    if (event.type === 'done') {
      a.streaming = false;
      a.streamingMessageId = '';
      if (event.ok && event.session) {
        a.activeSession = event.session;
        a.sessions = event.sessions || a.sessions || [];
        a.contextPreview = event.context_pack || a.contextPreview || null;
        a.draft = '';
        a.pendingAttachments = [];
        a.status = 'Assistant streamed reply with context proof.';
      } else {
        a.status = event.message || event.error || 'Assistant stream ended with an error.';
      }
      assistantRender(ctx, { preferBottom: true });
    }
  }

  function listItems(ctx, items = []) {
    const rows = Array.isArray(items) ? items.filter((item) => item !== undefined && item !== null) : [];
    if (ctx.NeoUI?.metaList) return ctx.NeoUI.metaList(rows.map((item) => typeof item === 'string' ? item : JSON.stringify(item)), { code: false, empty: 'No items yet.' });
    return `<ul>${rows.map((item) => `<li>${escapeHtml(ctx, typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>`;
  }

  function emptyState(ctx, title, description = '') {
    if (ctx.NeoUI?.emptyState) return ctx.NeoUI.emptyState(title, description);
    return `<div class="neo-empty"><strong>${escapeHtml(ctx, title)}</strong><p>${escapeHtml(ctx, description)}</p></div>`;
  }

  function badgeRow(ctx, items = []) {
    if (ctx.NeoUI?.badgeRow) return ctx.NeoUI.badgeRow(items.filter(Boolean));
    return `<div class="neo-badge-row">${items.filter(Boolean).map((item) => `<span class="neo-badge">${escapeHtml(ctx, item)}</span>`).join('')}</div>`;
  }

  function metaList(ctx, items = []) {
    if (ctx.NeoUI?.metaList) return ctx.NeoUI.metaList(items, { code: false, empty: 'No records yet.' });
    return listItems(ctx, items);
  }

  function assistantBackendSupportsVision(ctx) {
    const profile = assistantBackendProfile(ctx) || {};
    const flags = profile.capability_flags || {};
    const caps = profile.runtime?.capabilities || {};
    const provider = String(profile.provider_id || '').toLowerCase();
    const model = String(profile.connection?.model || '').toLowerCase();
    if (flags.supports_vision || caps.supports_vision || caps.runtime_supports_vision) return true;
    return provider.includes('vision') || model.includes('vision') || model.includes('llava') || model.includes('qwen-vl') || model.includes('qwen2vl') || model.includes('minicpm') || model.includes('mmproj');
  }

  function attachmentChipsHtml(ctx) {
    const attachments = assistantState(ctx).pendingAttachments || [];
    if (!attachments.length) return `<div class="assistant-attachment-empty">No files attached.</div>`;
    return `<div class="assistant-attachment-chip-row">${attachments.map((attachment) => {
      if (attachment.kind === 'image') {
        const warning = !assistantBackendSupportsVision(ctx) ? '<em>backend cannot inspect images</em>' : '';
        return `${assistantImageAttachmentThumbHtml(ctx, attachment, true)}${warning}`;
      }
      const kind = 'Doc';
      return `<span class="assistant-attachment-chip ${escapeAttr(ctx, attachment.kind || 'file')}"><strong>${escapeHtml(ctx, kind)}</strong>${escapeHtml(ctx, attachment.filename || attachment.stored_filename || attachment.attachment_id || 'attachment')}<button type="button" onclick="assistantRemovePendingAttachment('${escapeAttr(ctx, attachment.attachment_id || '')}')">×</button></span>`;
    }).join('')}</div>`;
  }

  function actionCardsHtml(ctx, actions = []) {
    if (!Array.isArray(actions) || !actions.length) return emptyState(ctx, 'No actions planned.', 'Safe tools stay gated until Neo Operator has a plan.');
    return `<div class="assistant-action-review-cards">${actions.slice(0, 8).map((action) => {
      const status = action.blocked ? 'blocked' : action.confirmation_required ? 'confirm' : 'read-only';
      return `<article class="assistant-evidence-card"><div class="assistant-evidence-top"><strong>${escapeHtml(ctx, action.title || action.action_id || action.tool_id || 'Action')}</strong><span>${escapeHtml(ctx, status)}</span></div><p>${escapeHtml(ctx, action.summary || action.reason || action.description || '')}</p><div class="assistant-evidence-meta"><span>${escapeHtml(ctx, action.kind || action.mode || 'operator')}</span><span>${escapeHtml(ctx, action.risk || action.policy || '')}</span></div></article>`;
    }).join('')}</div>`;
  }

  function projectManagerRefsHtml(ctx, refs = []) {
    if (!Array.isArray(refs) || !refs.length) return emptyState(ctx, 'No scope refs yet.', 'Run Scope Brief after adding scope records.');
    return `<div class="assistant-project-manager-refs">${refs.slice(0, 10).map((ref) => `<article class="assistant-evidence-card"><div class="assistant-evidence-top"><strong>${escapeHtml(ctx, ref.ref || ref.title || 'Scope ref')}</strong><span>${escapeHtml(ctx, ref.kind || 'record')}</span></div><p>${escapeHtml(ctx, ref.summary || ref.title || '')}</p><div class="assistant-evidence-meta"><span>${escapeHtml(ctx, ref.status || 'record')}</span><span>${escapeHtml(ctx, ref.timestamp || '')}</span></div></article>`).join('')}</div>`;
  }

  function projectManagerPanelHtml(ctx) {
    const a = assistantState(ctx);
    const pm = a.projectManager || {};
    const counts = pm.counts || {};
    const blockers = Array.isArray(pm.blockers) ? pm.blockers : [];
    const nextActions = Array.isArray(pm.next_actions) ? pm.next_actions : [];
    const refs = Array.isArray(pm.source_citations) ? pm.source_citations : [];
    const answer = pm.answer || 'Run Scope Brief to summarize the active Assistant scope with grounded references.';
    return `<section class="assistant-side-card assistant-project-manager-panel" data-neo-assistant-module="scope_brief"><span class="assistant-kicker">Scope Brief</span><h3>Assistant scope snapshot</h3><p>${escapeHtml(ctx, answer)}</p>${badgeRow(ctx, [pm.status || 'not loaded', `${counts.milestones || 0} milestones`, `${counts.deliverables || 0} deliverables`, `${counts.reviews || 0} reviews`, `${counts.packages || 0} packages`])}<div class="assistant-project-manager-actions"><button class="neo-btn primary" type="button" onclick="assistantLoadScopeBrief()">Refresh scope brief</button><button class="neo-btn" type="button" onclick="assistantAskScopeBrief()">Ask brief</button></div><label class="assistant-project-manager-question">Question<input id="assistant-project-manager-question" placeholder="What is blocked? What should I do next?"></label><h4>Blockers</h4>${blockers.length ? metaList(ctx, blockers.slice(0, 5).map((item) => `${item.level || 'note'} · ${item.title || 'Blocker'} · ${item.source_ref || ''}`)) : emptyState(ctx, 'No blockers detected.', 'Current scope records look clear.')}<h4>Next actions</h4>${nextActions.length ? metaList(ctx, nextActions.slice(0, 5).map((item) => `${item.priority || 'normal'} · ${item.action || ''} · ${item.source_ref || ''}`)) : emptyState(ctx, 'No suggested actions yet.', 'Run Scope Brief.')}<details><summary>Scope citations</summary>${projectManagerRefsHtml(ctx, refs)}</details></section>`;
  }

  function sourceGroundingPanelHtml(ctx) {
    const a = assistantState(ctx);
    const grounding = a.groundedPreview || a.contextPreview?.source_grounding || null;
    const diagnostics = a.contextPreview?.diagnostics || a.activeSession?.last_diagnostics?.context_pack || a.activeSession?.last_diagnostics || {};
    const count = grounding?.evidence_count ?? (Array.isArray(grounding?.evidence) ? grounding.evidence.length : diagnostics.source_grounding_evidence_count || 0);
    const trace = grounding?.trace_id || diagnostics.source_grounding_trace_id || '';
    const confidence = grounding?.confidence || (count ? 'grounded' : 'thin');
    const evidence = grounding && typeof ctx.assistantEvidenceCardsHtml === 'function' ? ctx.assistantEvidenceCardsHtml(grounding, true) : emptyState(ctx, 'No grounded evidence loaded.', 'Preview sources from the composer or send a grounded message.');
    return `<section class="assistant-side-card assistant-source-grounding-card" data-neo-assistant-module="source_grounding"><span class="assistant-kicker">Source grounding</span><h3>Memory proof layer</h3><div class="assistant-grounding-metrics"><div><span>Mode</span><strong>${a.groundedMode ? 'On' : 'Off'}</strong></div><div><span>Evidence</span><strong>${escapeHtml(ctx, String(count || 0))}</strong></div><div><span>Confidence</span><strong>${escapeHtml(ctx, confidence)}</strong></div></div>${trace ? `<p class="assistant-grounding-trace">Trace: ${escapeHtml(ctx, trace)}</p>` : '<p class="assistant-grounding-trace">No trace yet. Preview sources or send a message.</p>'}${evidence}</section>`;
  }

  function citationViewerHtml(ctx) {
    const viewer = assistantState(ctx).citationViewer;
    if (!viewer) return '';
    const source = viewer.source_path || viewer.chunk?.source_path || 'memory';
    const title = viewer.title || viewer.chunk?.title || 'Source viewer';
    const lineRows = Array.isArray(viewer.lines) ? viewer.lines.map((row) => `${row.highlight ? '▶' : ' '} ${String(row.line || '').padStart(4, ' ')}  ${row.text || ''}`).join('\n') : '';
    const content = lineRows || viewer.fallback_content || viewer.source_excerpt || viewer.content || viewer.chunk?.content || '';
    return `<section class="assistant-side-card assistant-citation-viewer-card" data-neo-assistant-module="citation_viewer"><span class="assistant-kicker">Citation viewer</span><h3>${escapeHtml(ctx, title)}</h3><p>${escapeHtml(ctx, source)}</p><pre class="assistant-source-pre">${escapeHtml(ctx, content || JSON.stringify(viewer, null, 2))}</pre></section>`;
  }

  function actionReviewPanelHtml(ctx) {
    const review = assistantState(ctx).actionReview || {};
    const status = review.status || {};
    const plan = review.plan || null;
    const run = review.run || null;
    const actions = plan?.actions || run?.actions || [];
    const summary = plan?.action_summary || run?.action_summary || {};
    const resultText = run?.response_text || '';
    const trace = run?.retrieval_trace_id || plan?.operator_plan?.trace_id || '';
    return `<section class="assistant-side-card assistant-action-review-panel" data-neo-assistant-module="action_review"><span class="assistant-kicker">Action review</span><h3>Safe tool execution</h3><div class="assistant-action-review-metrics"><div><span>Read-only</span><strong>${escapeHtml(ctx, String(summary.read_only_count || 0))}</strong></div><div><span>Confirm</span><strong>${escapeHtml(ctx, String(summary.confirmation_required_count || 0))}</strong></div><div><span>Blocked</span><strong>${escapeHtml(ctx, String(summary.blocked_count || 0))}</strong></div></div><p>${escapeHtml(ctx, status.policy || 'Actions are planned through Neo Operator and gated before execution.')}</p>${trace ? `<p class="assistant-grounding-trace">Trace: ${escapeHtml(ctx, trace)}</p>` : ''}${actionCardsHtml(ctx, actions)}<div class="assistant-action-row compact"><button class="neo-btn" type="button" onclick="assistantPlanActionReview()">Plan</button><button class="neo-btn" type="button" onclick="assistantRunActionReview(false)">Run safe read</button><button class="neo-btn primary" type="button" onclick="assistantRunActionReview(true)">Run confirmed</button></div>${resultText ? `<pre class="assistant-action-review-result">${escapeHtml(ctx, resultText)}</pre>` : ''}</section>`;
  }

  function projectWorkspaceMainHtml(ctx, activeProject) {
    const a = assistantState(ctx);
    const brain = a.projectBrain || {};
    const counts = brain.counts || {};
    const latest = Array.isArray(brain.latest_snapshots) ? brain.latest_snapshots.slice(0, 4).map((row) => `${row.surface || 'surface'} · ${row.title || row.snapshot_id || 'snapshot'} · ${row.created_at || ''}`) : [];
    return `<section class="assistant-modern-card" data-neo-assistant-module="scope_workspace"><span class="assistant-kicker">Assistant scope</span><h2>Shape the internal context Neo should remember</h2><div class="assistant-scope-editor assistant-project-editor assistant-form-grid"><label>Name<input id="assistant-scope-name" value="${escapeAttr(ctx, activeProject.name || '')}"></label><label>Type<input id="assistant-scope-type" value="${escapeAttr(ctx, activeProject.type || 'general')}"></label><label class="wide">Description<textarea id="assistant-scope-description">${escapeHtml(ctx, activeProject.description || '')}</textarea></label><label class="wide">Notes<textarea id="assistant-scope-notes">${escapeHtml(ctx, activeProject.notes || '')}</textarea></label><button class="neo-btn primary" type="button" onclick="assistantSaveScopeEditor()">Save scope</button></div></section><section class="assistant-modern-card assistant-project-brain-card" data-neo-assistant-module="project_brain"><span class="assistant-kicker">Project Brain</span><h2>Guides + snapshots + metadata + uploads</h2><p>Capture the active scope state, index Neo-owned metadata, and upload brand/client docs for this Assistant scope.</p><div class="assistant-action-row compact"><button class="neo-btn primary" type="button" onclick="assistantCaptureCurrentProjectState()">Capture Current State</button><button class="neo-btn" type="button" onclick="assistantIndexProjectData()">Index Project Data</button><button class="neo-btn" type="button" onclick="assistantRebuildProjectBrain()">Rebuild Project Brain</button><button class="neo-btn secondary" type="button" onclick="assistantPreviewContextPack()">View Context Pack</button></div><div class="assistant-action-row compact"><input id="assistant-project-file-input" type="file" multiple onchange="assistantUploadProjectFiles(this)" hidden><button class="neo-btn" type="button" onclick="document.getElementById('assistant-project-file-input')?.click()">Upload Project Files</button><button class="neo-btn" type="button" onclick="assistantRefreshProjectBrainStatus()">Refresh Brain Status</button></div>${badgeRow(ctx, [`Guides: ${counts.built_in_guides_visible || 0}`, `Snapshots: ${counts.snapshots || 0}`, `Indexes: ${counts.indexes || 0}`, `Uploads: ${counts.uploads || 0}`])}<h4>Latest captures</h4>${latest.length ? metaList(ctx, latest) : emptyState(ctx, 'No captures yet.', 'Click Capture Current State to save the selected scope snapshot.')}</section>`;
  }

  function memoryMainHtml(ctx) {
    const captures = (assistantState(ctx).memoryCaptures || []).map((capture) => `${capture.title || capture.capture_id} · ${capture.project_id || 'general'}`);
    return `<section class="assistant-modern-card" data-neo-assistant-module="memory_lens"><span class="assistant-kicker">Memory lens</span><h2>Assistant memory without hiding the engine</h2><p>Manual captures stay visible here and write through the centralized Memory Engine service when available.</p>${listItems(ctx, captures.length ? captures : ['No manual captures yet.'])}</section>`;
  }

  function contextMainHtml(ctx) {
    return `<section class="assistant-modern-card" data-neo-assistant-module="scope_knowledge"><span class="assistant-kicker">Scope knowledge</span><h2>Feed Neo reusable scope context</h2><div class="assistant-scope-editor assistant-project-editor assistant-form-grid"><label>Title<input id="assistant-context-title" placeholder="Client notes, tab summary, scope brief..."></label><label>Tags<input id="assistant-context-tags" placeholder="client, prompt, roleplay"></label><label class="wide">Context text<textarea id="assistant-context-text" placeholder="Paste reusable scope knowledge, client requirements, debug notes, or surface context here."></textarea></label><button class="neo-btn primary" type="button" onclick="assistantSaveScopeKnowledge()">Save to scope knowledge</button><button class="neo-btn secondary" type="button" onclick="assistantPreviewContextPack()">Preview context pack</button></div></section>`;
  }

  function toolsMainHtml(ctx) {
    return `<section class="assistant-modern-card" data-neo-assistant-module="safe_tools"><span class="assistant-kicker">Safe tools</span><h2>Useful hands. No chainsaws.</h2><div class="assistant-tool-grid"><button class="assistant-tool-card" type="button" onclick="assistantRunSafeTool('preview_context_pack')"><strong>Preview context pack</strong><span>Inspect what Neo will inject.</span></button><button class="assistant-tool-card" type="button" onclick="assistantRunSafeTool('guide_current_tab')"><strong>Guide current tab</strong><span>Get surface-aware help.</span></button><button class="assistant-tool-card" type="button" onclick="assistantAttachCurrentSurfaceContext()"><strong>Attach current tab</strong><span>Send this surface to Assistant.</span></button><button class="assistant-tool-card" type="button" onclick="assistantLoadScopeBrief()"><strong>Scope Brief</strong><span>Summarize scope blockers and next actions with citations.</span></button></div><p>Shell commands, patch apply, destructive file operations, and external connector actions remain locked out.</p></section>`;
  }

  function guideMainHtml(ctx) {
    const items = ['Image workflow and route state', 'Prompt & Captioning outputs and handoffs', 'Roleplay memory, canon, scenes, and stories', 'Memory Engine health and memory settings'];
    return `<section class="assistant-modern-card" data-neo-assistant-module="guide"><span class="assistant-kicker">Neo Guide</span><h2>Ask the heart of Neo what to do next</h2><p>Assistant can explain implemented surfaces, attach tab snapshots, preview context, then help turn confusion into a clean plan.</p><div class="assistant-guide-grid">${items.map((item) => `<div>${escapeHtml(ctx, item)}</div>`).join('')}</div><button class="neo-btn primary" type="button" onclick="assistantAttachCurrentSurfaceContext()">Ask Assistant about current tab</button></section>`;
  }

  function validationMainHtml(ctx, surface) {
    const a = assistantState(ctx);
    const rows = [`Surface status: ${surface?.status || 'unknown'}`, `Scopes: ${(a.projects || []).length}`, `Sessions: ${(a.sessions || []).length}`, `Captures: ${(a.memoryCaptures || []).length}`, `Model runtime: ${a.capabilities?.model_runtime ? 'ready' : 'not ready'}`, `Centralized memory writeback: ${a.capabilities?.centralized_memory_writeback ? 'ready' : 'not ready'}`, `Storage root: ${a.storage?.data_root || 'pending'}`, `Cross-surface context: ${a.capabilities?.cross_surface_context ? 'ready' : 'not ready'}`, `Scope knowledge: ${(a.capabilities?.scope_knowledge || a.capabilities?.project_knowledge) ? 'ready' : 'not ready'}`, `Scope Brief: ${a.projectManager?.schema_id ? 'ready' : 'available'}`, `Safe tool catalog: ${a.capabilities?.safe_tool_catalog ? 'ready' : 'not ready'}`, `Setup lock: ${a.lockLayer?.contract_version || 'pending'}`];
    return `<section class="assistant-modern-card" data-neo-assistant-module="validation"><span class="assistant-kicker">Validation</span><h2>Lock state and health checks</h2>${listItems(ctx, rows)}</section>`;
  }

  function inspectorMainHtml(ctx) {
    const a = assistantState(ctx);
    const payload = { profile: a.profile, activeSession: a.activeSession, projects: a.projects, sessions: a.sessions, storage: a.storage, capabilities: a.capabilities, thinkingLayer: a.thinkingLayer, diagnostics: a.activeSession?.last_diagnostics, lockLayer: a.lockLayer };
    return `<section class="assistant-modern-card" data-neo-assistant-module="inspector"><span class="assistant-kicker">Inspector</span><h2>Assistant internals</h2><pre class="roleplay-stories-pre small">${escapeHtml(ctx, JSON.stringify(payload, null, 2))}</pre></section>`;
  }

  function currentSurfaceSnapshot(ctx) {
    const a = assistantState(ctx);
    const project = activeProject(ctx);
    const surface = assistantProjectSurface(ctx, project);
    const runtime = ctx.state?.surfaceRuntime?.[surface] || {};
    const subtab = runtime.subtab || ctx.state?.activeSubtabId || ctx.state?.activeSubtab || '';
    const snapshot = assistantSurfaceProjectSnapshot(ctx, surface);
    return {
      surface,
      subtab,
      project_id: a.selectedProjectId || project.project_id || 'general',
      session_id: a.activeSession?.session_id || '',
      title: `Live ${surface} project context${subtab ? ` / ${subtab}` : ''}`,
      summary: `Assistant can inspect current ${surface}${subtab ? `/${subtab}` : ''} settings, extension state, runtime mode, and Neo-owned metadata context.`,
      suggested_action: 'guide',
      payload: snapshot,
      surface_context_snapshot: snapshot,
    };
  }


  function assistantKnownSurfaceIds() {
    return ['image', 'video', 'prompt_captioning', 'roleplay', 'voice'];
  }

  function assistantProjectBrainCapturePayload(ctx) {
    const a = assistantState(ctx);
    const project = activeProject(ctx);
    const surface = assistantProjectSurface(ctx, project);
    const projectId = a.selectedProjectId || project.project_id || 'general';
    if (projectId === 'general') {
      const snapshots = {};
      assistantKnownSurfaceIds().forEach((id) => { snapshots[id] = assistantSurfaceProjectSnapshot(ctx, id); });
      return {
        project_id: projectId,
        session_id: a.activeSession?.session_id || '',
        surface: 'all',
        title: 'General Assistant full Neo state capture',
        summary: 'Captured all known Neo surface snapshots from Assistant > Project.',
        surface_context_snapshot: { schema_id: 'neo.assistant.all_surface_snapshot.v1', snapshots },
      };
    }
    const snapshot = assistantSurfaceProjectSnapshot(ctx, surface);
    return {
      project_id: projectId,
      session_id: a.activeSession?.session_id || '',
      surface,
      title: `Live ${surface} state capture`,
      summary: `Captured current ${surface} state from Assistant > Project.`,
      surface_context_snapshot: snapshot,
    };
  }

  function deepPanelLayout(ctx) {
    const a = assistantState(ctx);
    const activeScope = ctx.activeProject || activeProject(ctx);
    const tab = ctx.subtab?.subtab_id || ctx.subtabId || 'chat';
    const linked = (a.sessions || []).filter((s) => s.project_id === activeScope.project_id).map((s) => `${s.title || s.session_id} · ${s.message_count || 0} messages`);
    const contextRows = (a.contextItems || []).slice(0, 12).map((item) => `${item.title || item.context_id} · ${item.kind || 'context'} · ${item.surface || 'assistant'}`);
    const preview = a.contextPreview || {};
    if (tab === 'chat') {
      return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: api.renderers.chatPanelHtml(ctx), sideHtml: `${projectManagerPanelHtml(ctx)}${sourceGroundingPanelHtml(ctx)}${actionReviewPanelHtml(ctx)}${citationViewerHtml(ctx)}${api.renderers.sideProofHtml({ ...ctx, activeProject: activeScope })}<section class="assistant-side-card"><span class="assistant-kicker">Scope</span><h3>${escapeHtml(ctx, activeScope.name || 'General')}</h3><p>${escapeHtml(ctx, activeScope.description || activeScope.notes || 'No scope notes yet.')}</p><button class="neo-btn secondary" type="button" onclick="assistantPreviewContextPack()">Preview context pack</button></section>` };
    }
    if (tab === 'projects' || tab === 'project_context') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: projectWorkspaceMainHtml(ctx, activeScope), sideHtml: `${projectManagerPanelHtml(ctx)}<section class="assistant-side-card"><span class="assistant-kicker">Linked chats</span>${listItems(ctx, linked.length ? linked : ['No linked chats yet.'])}</section>` };
    if (tab === 'memory') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: memoryMainHtml(ctx), sideHtml: api.renderers.sideProofHtml({ ...ctx, activeProject: activeScope }) };
    if (tab === 'context') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: contextMainHtml(ctx), sideHtml: `${sourceGroundingPanelHtml(ctx)}<section class="assistant-side-card"><span class="assistant-kicker">Saved cards</span>${listItems(ctx, contextRows.length ? contextRows : ['No scope knowledge cards saved yet.'])}</section><section class="assistant-side-card"><span class="assistant-kicker">Preview</span><pre class="roleplay-stories-pre small">${escapeHtml(ctx, JSON.stringify(preview, null, 2))}</pre></section>` };
    if (tab === 'tools') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: toolsMainHtml(ctx), sideHtml: `<section class="assistant-side-card"><span class="assistant-kicker">Last tool result</span><pre class="roleplay-stories-pre small">${escapeHtml(ctx, JSON.stringify(a.lastToolResult || {}, null, 2))}</pre></section>` };
    if (tab === 'guide') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: guideMainHtml(ctx), sideHtml: api.renderers.sideProofHtml({ ...ctx, activeProject: activeScope }) };
    if (tab === 'validation') return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: validationMainHtml(ctx, ctx.surface), sideHtml: api.renderers.sideProofHtml({ ...ctx, activeProject: activeScope }) };
    return { className: `neo-assistant-workspace assistant-tab-${escapeAttr(ctx, tab)}`, railHtml: api.renderers.railHtml(ctx), mainHtml: inspectorMainHtml(ctx), sideHtml: '', wide: true };
  }

  const api = {
    surfaceId: 'assistant',
    releaseStage: 'ready',
    status: 'ready',
    migratedAreas: [
      'render.assistant_chat_panel',
      'render.assistant_rail',
      'render.assistant_side_proof',
      'action.assistant.refresh',
      'action.assistant.create_session',
      'action.assistant.load_session',
      'action.assistant.save_session',
      'action.assistant.send_local_message',
      'action.assistant.upload_attachments',
      'action.assistant.remove_pending_attachment',
      'action.assistant.set_grounded_mode',
      'action.assistant.use_starter',
      'action.assistant.clear_draft',
      'action.assistant.clear_chat',
      'action.assistant.continue_response',
      'action.assistant.set_search',
      'action.assistant.set_project_filter',
      'action.assistant.set_scope_filter',
      'action.assistant.create_project',
      'action.assistant.create_scope',
      'action.assistant.rename_project',
      'action.assistant.rename_scope',
      'action.assistant.save_project_editor',
      'action.assistant.save_scope_editor',
      'action.assistant.rename_session',
      'action.assistant.delete_session',
      'action.assistant.capture_memory',
      'action.assistant.preview_context_pack',
      'render.assistant_project_manager',
      'render.assistant_scope_brief',
      'render.assistant_source_grounding',
      'render.assistant_action_review',
      'render.assistant_citation_viewer',
      'render.assistant_project_workspace',
      'render.assistant_scope_workspace',
      'render.assistant_memory_lens',
      'render.assistant_context_knowledge',
      'render.assistant_safe_tools',
      'render.assistant_guide',
      'render.assistant_validation',
      'render.assistant_inspector',
      'render.assistant_deep_panel_layout',
      'action.assistant.project_manager.load',
      'action.assistant.scope_brief.load',
      'action.assistant.project_manager.ask',
      'action.assistant.scope_brief.ask',
      'action.assistant.action_review.plan',
      'action.assistant.action_review.run',
      'action.assistant.citation.open',
      'action.assistant.project_knowledge.save',
      'action.assistant.scope_knowledge.save',
      'action.assistant.surface_context.attach',
      'action.assistant.safe_tool.run',
      'action.assistant.project_brain.capture',
      'action.assistant.project_brain.index',
      'action.assistant.project_brain.rebuild',
      'action.assistant.project_brain.upload',
      'action.assistant.project_brain.status'
    ],
    policy: 'Assistant owns chat plus deep panels/action lanes while legacy neo.js keeps bridge wrappers and fallback behavior.',
    diagnostics: {
      status: 'assistant_deep_panel_slice_partial',
      fallback: 'legacy neo.js wrappers remain active',
      risk: 'low',
    },
    renderers: {
      chatPanelHtml(ctx) {
        const a = assistantState(ctx);
        const session = a.activeSession || {};
        const projectId = session.project_id || a.selectedProjectId || 'general';
        const messageCount = Array.isArray(session.messages) ? session.messages.length : 0;
        const canContinue = Array.isArray(session.messages) && !![...session.messages].reverse().find((message) => message && message.role === 'assistant' && String(message.text || '').trim());
        const statusText = a.status || 'Runtime-ready. Context pack + Memory Engine memory bridge active.';
        const pills = typeof ctx.assistantCapabilityPills === 'function' ? ctx.assistantCapabilityPills() : '';
        const starters = typeof ctx.assistantStarterCardsHtml === 'function' ? ctx.assistantStarterCardsHtml() : '';
        const messages = typeof ctx.assistantMessageCards === 'function' ? ctx.assistantMessageCards() : '';
        const grounded = typeof ctx.assistantGroundedModeControlsHtml === 'function' ? ctx.assistantGroundedModeControlsHtml() : '';
        const firstRun = typeof window.neoPreviewFirstRunCardHtml === 'function' ? window.neoPreviewFirstRunCardHtml('assistant', 'Assistant quick start') : '';
        return `<section class="assistant-chat-shell" data-neo-assistant-module="chat_panel">
          <header class="assistant-hero-card neo-assistant-feature-title-card" data-neo-feature-title-card="true" data-testid="assistant-feature-title-card">
            <div class="assistant-hero-glow" aria-hidden="true"></div>
            <div>
              <span class="assistant-kicker">Neo Assistant</span>
              <h2>${escapeHtml(ctx, session.title || 'New assistant chat')}</h2>
              <p>${escapeHtml(ctx, session.mode || 'general')} · ${escapeHtml(ctx, projectId)} · ${messageCount} messages</p>
              ${pills}
            </div>
            <div class="assistant-hero-actions">
              <button class="neo-btn" type="button" onclick="assistantRenameActiveSession()">Rename</button>
              <button class="neo-btn danger" type="button" onclick="assistantDeleteActiveSession()">Delete</button>
            </div>
          </header>

          ${firstRun}

          <section class="assistant-ask-card">
            <div class="assistant-card-headline"><div><span class="assistant-kicker">Quick starts</span><h3>What are we building?</h3></div><span class="assistant-soft-pill">Scope-aware</span></div>
            <p>Pick a starting move or type freely. Neo will pull scope context, thread memory, and Memory Engine proof before answering.</p>
            ${starters}
          </section>

          <section class="assistant-thread" aria-live="polite">${messages}</section>

          <section class="assistant-composer">
            <label for="assistant-composer-text">Composer</label>
            <textarea id="assistant-composer-text" placeholder="Ask a question, draft a reply, plan a feature, or drop in diagnostic notes...">${escapeHtml(ctx, a.draft || session.draft || '')}</textarea>
            <div class="assistant-attachment-tray">
              <input id="assistant-attachment-input" type="file" multiple accept="image/png,image/jpeg,image/webp,image/bmp,.txt,.md,.markdown,.json,.jsonl,.csv,.tsv,.log,.yaml,.yml,.xml,.html,.htm,.srt,.vtt,.pdf,.docx,.py,.js,.jsx,.ts,.tsx,.css" onchange="assistantUploadAttachments(this)" hidden>
              <div class="assistant-attachment-tray-top"><button class="neo-btn secondary" type="button" onclick="document.getElementById('assistant-attachment-input')?.click()">Attach image/document</button><small>Images require a vision-capable backend. Documents are extracted into context when possible.</small></div>
              ${attachmentChipsHtml(ctx)}
            </div>
            ${grounded}
            <div class="assistant-composer-bar">
              <div class="assistant-status-line"><span class="assistant-status-dot"></span><small>${escapeHtml(ctx, statusText)}</small></div>
              <div class="assistant-action-row">
                <button class="neo-btn primary" type="button" onclick="assistantSendLocalMessage()" ${a.streaming ? 'disabled' : ''}>Send</button>
                <button class="neo-btn" type="button" onclick="assistantContinueResponse()" ${a.streaming || !canContinue ? 'disabled' : ''}>Continue response</button>
                <button class="neo-btn" type="button" onclick="assistantSaveActiveSession()">Save chat</button>
                <button class="neo-btn" type="button" onclick="assistantClearDraft()">Clear draft</button>
                <button class="neo-btn secondary" type="button" onclick="assistantCaptureSelectionAsMemory()">Save selected as memory</button>
              </div>
            </div>
          </section>
        </section>`;
      },

      railHtml(ctx) {
        const a = assistantState(ctx);
        const project = ctx.activeProject || activeProject(ctx);
        const options = typeof ctx.assistantProjectOptions === 'function' ? ctx.assistantProjectOptions(a.selectedProjectId) : '';
        const sessions = typeof ctx.assistantSessionListHtml === 'function' ? ctx.assistantSessionListHtml() : '';
        return `<aside class="assistant-rail" data-neo-assistant-module="rail">
          <div class="assistant-rail-brand">
            <div class="assistant-orb">N</div>
            <div><span class="assistant-kicker">Assistant</span><h3>Command Center</h3><p>Chats, scopes, context.</p></div>
          </div>
          <button class="neo-btn primary full" type="button" onclick="assistantCreateSession()">+ New chat</button>
          <label>Scope filter</label>
          <select id="assistant-scope-filter" onchange="assistantSetScopeFilter(this.value)">${options}</select>
          <div class="assistant-action-row compact"><button class="neo-btn" type="button" onclick="assistantCreateScope()">+ Scope</button><button class="neo-btn" type="button" onclick="assistantRenameScope()">Rename scope</button></div>
          <label>Search chats</label>
          <input id="assistant-search" value="${escapeAttr(ctx, a.searchQuery || '')}" placeholder="Find chats, plans, notes..." oninput="assistantSetSearch(this.value)">
          <div class="assistant-active-scope-mini assistant-active-project-mini"><span>Active scope</span><strong>${escapeHtml(ctx, project.name || 'General')}</strong><small>${escapeHtml(ctx, project.description || project.notes || 'No scope notes yet.')}</small></div>
          <div class="assistant-chat-list">${sessions}</div>
        </aside>`;
      },

      sideProofHtml(ctx) {
        const a = assistantState(ctx);
        const project = ctx.activeProject || activeProject(ctx);
        const diagnostics = a.activeSession?.last_diagnostics || a.contextPreview?.diagnostics || {};
        const rows = [
          ['Scope', project.name || 'General'],
          ['Sessions', String((a.sessions || []).length)],
          ['Captures', String((a.memoryCaptures || []).length)],
          ['Context cards', String((a.contextItems || []).length)],
          ['Setup', a.lockLayer?.contract_version || a.assistantBootstrap?.lock_layer?.contract_version || 'locked'],
        ];
        return `<section class="assistant-side-card assistant-proof-card" data-neo-assistant-module="side_proof">
          <span class="assistant-kicker">Context proof</span>
          <h3>Active brain state</h3>
          <div class="assistant-proof-list">${rows.map(([k, v]) => `<div><span>${escapeHtml(ctx, k)}</span><strong>${escapeHtml(ctx, v)}</strong></div>`).join('')}</div>
          <details><summary>Diagnostics JSON</summary><pre class="roleplay-stories-pre small">${escapeHtml(ctx, JSON.stringify(diagnostics, null, 2))}</pre></details>
        </section>`;
      },
      projectManagerPanelHtml(ctx) { return projectManagerPanelHtml(ctx); },
      sourceGroundingPanelHtml(ctx) { return sourceGroundingPanelHtml(ctx); },
      actionReviewPanelHtml(ctx) { return actionReviewPanelHtml(ctx); },
      citationViewerHtml(ctx) { return citationViewerHtml(ctx); },
      projectWorkspaceMainHtml(ctx) { return projectWorkspaceMainHtml(ctx, ctx.activeProject || activeProject(ctx)); },
      memoryMainHtml(ctx) { return memoryMainHtml(ctx); },
      contextMainHtml(ctx) { return contextMainHtml(ctx); },
      toolsMainHtml(ctx) { return toolsMainHtml(ctx); },
      guideMainHtml(ctx) { return guideMainHtml(ctx); },
      validationMainHtml(ctx) { return validationMainHtml(ctx, ctx.surface); },
      inspectorMainHtml(ctx) { return inspectorMainHtml(ctx); },
      deepPanelLayout(ctx) { return deepPanelLayout(ctx); },
    },
    actions: {
      async assistantLoadProjectManager(ctx) {
        const a = assistantState(ctx);
        const project = ctx.activeProject || activeProject(ctx);
        const resolved = ctx.projectId || project.project_id || a.selectedProjectId || 'general';
        a.projectManager = await assistantFetchJson(ctx, `/api/assistant/project-manager?project_id=${encodeURIComponent(resolved)}`);
        a.status = 'Scope Brief snapshot loaded';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantLoadProjectManager' };
      },
      async assistantAskProjectManager(ctx) {
        const a = assistantState(ctx);
        const project = ctx.activeProject || activeProject(ctx);
        const question = ctx.question || document.getElementById('assistant-project-manager-question')?.value || a.draft || 'What should I do next?';
        a.projectManager = await assistantFetchJson(ctx, '/api/assistant/project-manager/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: project.project_id || a.selectedProjectId || 'general', question }) });
        a.status = 'Scope Brief answered with grounded refs';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantAskProjectManager' };
      },
      async assistantLoadScopeBrief(ctx) {
        return api.actions.assistantLoadProjectManager(ctx);
      },
      async assistantAskScopeBrief(ctx) {
        return api.actions.assistantAskProjectManager(ctx);
      },
      async assistantPlanActionReview(ctx) {
        const a = assistantState(ctx);
        const text = (document.getElementById('assistant-composer-text')?.value || a.draft || '').trim();
        if (!text) { a.status = 'Add a command before reviewing actions.'; assistantRender(ctx); return { status: 'empty', action: 'assistantPlanActionReview' }; }
        a.status = 'Planning safe actions through Neo Operator...';
        assistantRender(ctx);
        const payload = await assistantFetchJson(ctx, '/api/assistant/action-review/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: text, retrieval_profile: a.profile?.retrieval_profile || 'smart', limit: 8 }) });
        a.actionReview = { ...(a.actionReview || {}), plan: payload, run: null, command: text };
        const summary = payload.action_summary || {};
        a.status = `Action plan ready: ${summary.read_only_count || 0} read-only, ${summary.confirmation_required_count || 0} confirmation, ${summary.blocked_count || 0} blocked.`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantPlanActionReview' };
      },
      async assistantRunActionReview(ctx) {
        const a = assistantState(ctx);
        const executeConfirmed = !!ctx.executeConfirmed;
        const command = (a.actionReview?.command || document.getElementById('assistant-composer-text')?.value || a.draft || '').trim();
        if (!command) { a.status = 'Plan an action first.'; assistantRender(ctx); return { status: 'empty', action: 'assistantRunActionReview' }; }
        if (executeConfirmed && !ctx.confirmed && window.confirm && !window.confirm('Run confirmation-required Operator actions? Read the action plan first.')) return { status: 'cancelled', action: 'assistantRunActionReview' };
        a.status = executeConfirmed ? 'Running confirmed Operator actions...' : 'Running safe read-only Operator actions...';
        assistantRender(ctx);
        const payload = await assistantFetchJson(ctx, '/api/assistant/action-review/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command, retrieval_profile: a.profile?.retrieval_profile || 'smart', execute_confirmed: executeConfirmed, limit: 8 }) });
        a.actionReview = { ...(a.actionReview || {}), run: payload, command };
        const blocked = (payload.blocked_actions || []).length;
        const executed = (payload.executed_actions || []).length;
        a.status = `Operator run complete: ${executed} executed, ${blocked} blocked/gated.`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRunActionReview' };
      },
      async assistantOpenCitationViewer(ctx) {
        const a = assistantState(ctx);
        const chunkId = ctx.chunkId || ctx.chunk_id || '';
        if (!chunkId) return { status: 'empty', action: 'assistantOpenCitationViewer' };
        a.status = 'Opening source citation...';
        assistantRender(ctx);
        a.citationViewer = await assistantFetchJson(ctx, `/api/memory/citations/${encodeURIComponent(chunkId)}`);
        a.status = 'Citation source loaded.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantOpenCitationViewer' };
      },
      async assistantSaveProjectKnowledge(ctx) {
        const a = assistantState(ctx);
        const text = ctx.text || document.getElementById('assistant-context-text')?.value || '';
        const title = ctx.title || document.getElementById('assistant-context-title')?.value || 'Scope knowledge';
        const tags = ctx.tags || document.getElementById('assistant-context-tags')?.value || '';
        if (!String(text || '').trim()) { if (window.alert) window.alert('Paste some context text first.'); return { status: 'empty', action: 'assistantSaveProjectKnowledge' }; }
        const payload = await assistantFetchJson(ctx, '/api/assistant/context-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, text, tags, project_id: a.selectedProjectId || 'general', session_id: a.activeSession?.session_id || '', surface: 'assistant', kind: 'project_knowledge' }) });
        a.contextItems = payload.context_items || a.contextItems || [];
        a.status = 'Scope knowledge saved.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSaveProjectKnowledge' };
      },
      async assistantSaveScopeKnowledge(ctx) {
        const result = await api.actions.assistantSaveProjectKnowledge(ctx);
        return { ...result, action: 'assistantSaveScopeKnowledge' };
      },
      async assistantAttachCurrentSurfaceContext(ctx) {
        const a = assistantState(ctx);
        const payload = await assistantFetchJson(ctx, '/api/assistant/surface-context', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(currentSurfaceSnapshot(ctx)) });
        if (payload.context_item) a.contextItems = [payload.context_item, ...(a.contextItems || [])];
        if (payload.handoff) a.surfaceContext = [payload.handoff, ...(a.surfaceContext || [])];
        a.draft = `Use the attached ${payload.handoff?.surface || 'surface'} context to explain what matters and suggest the next best steps.`;
        a.status = payload.message || 'Surface context attached.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantAttachCurrentSurfaceContext' };
      },
      async assistantRunSafeTool(ctx) {
        const a = assistantState(ctx);
        const toolId = ctx.toolId || ctx.tool_id || 'preview_context_pack';
        const args = { project_id: a.selectedProjectId || 'general', session_id: a.activeSession?.session_id || '', message: document.getElementById('assistant-composer-text')?.value || a.draft || '', retrieval_profile: a.profile?.retrieval_profile || 'smart', surface: assistantProjectSurface(ctx), subtab: ctx.state?.activeSubtabId || ctx.state?.activeSubtab || '' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/tool-execute', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tool_id: toolId, args }) });
        a.lastToolResult = payload;
        if (toolId === 'preview_context_pack') a.contextPreview = payload;
        a.status = `Safe tool executed: ${toolId}`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRunSafeTool' };
      },
      async assistantRefresh(ctx) {
        if (typeof ctx.assistantBootstrap === 'function') await ctx.assistantBootstrap();
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRefresh' };
      },
      async assistantCreateSession(ctx) {
        const a = assistantState(ctx);
        const payload = await assistantFetchJson(ctx, '/api/assistant/session-create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: a.selectedProjectId || 'general', title: 'New assistant chat' }) });
        a.activeSession = payload.session;
        a.sessions = payload.sessions || [];
        a.selectedProjectId = payload.session?.project_id || a.selectedProjectId;
        a.status = 'New chat created';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantCreateSession' };
      },
      async assistantLoadSession(ctx) {
        const payload = await assistantFetchJson(ctx, `/api/assistant/session-load?session_id=${encodeURIComponent(ctx.sessionId || '')}`);
        const a = assistantState(ctx);
        a.activeSession = payload.session;
        a.selectedProjectId = payload.session?.project_id || a.selectedProjectId;
        a.draft = payload.session?.draft || '';
        a.status = 'Chat loaded';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantLoadSession' };
      },
      async assistantSaveActiveSession(ctx) {
        const a = assistantState(ctx);
        let session = a.activeSession;
        if (!session && api.actions.assistantCreateSession) {
          await api.actions.assistantCreateSession(ctx);
          session = assistantState(ctx).activeSession;
        }
        const draftEl = document.getElementById('assistant-composer-text');
        if (draftEl) a.draft = draftEl.value;
        const payload = await assistantFetchJson(ctx, '/api/assistant/session-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...(session || {}), draft: a.draft, project_id: a.selectedProjectId || session?.project_id || 'general' }) });
        a.activeSession = payload.session;
        a.sessions = payload.sessions || [];
        a.status = 'Chat saved';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSaveActiveSession' };
      },
      async assistantSendLocalMessage(ctx) {
        const a = assistantState(ctx);
        if (a.streaming) return { status: 'blocked', action: 'assistantSendLocalMessage', reason: 'already_streaming' };
        const draftEl = document.getElementById('assistant-composer-text');
        const text = (draftEl?.value || a.draft || '').trim();
        const pendingAttachmentIds = (a.pendingAttachments || []).map((attachment) => attachment.attachment_id).filter(Boolean);
        if (!text && !pendingAttachmentIds.length) return { status: 'empty', action: 'assistantSendLocalMessage' };
        const messageText = text || 'Please review the attached file(s).';
        if (!assistantBackendConnected(ctx)) {
          a.status = assistantBackendBlockedMessage(ctx);
          assistantRender(ctx);
          return { status: 'blocked', action: 'assistantSendLocalMessage', reason: 'backend_not_connected' };
        }
        if (!a.activeSession) await api.actions.assistantCreateSession(ctx);
        const session = assistantState(ctx).activeSession || {};
        a.status = 'Starting Assistant stream…';
        a.streaming = true;
        a.streamingMessageId = '';
        assistantRender(ctx);
        try {
          const response = await fetch('/api/assistant/chat-run-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              session_id: session.session_id || '',
              project_id: a.selectedProjectId || session.project_id || 'general',
              message: messageText,
              retrieval_profile: a.profile?.retrieval_profile || 'smart',
              source_grounded: !!a.groundedMode,
              attachments: pendingAttachmentIds,
              profile_id: assistantBackendProfile(ctx)?.profile_id || '',
              active_surface: assistantProjectSurface(ctx),
              surface_context_snapshot: assistantSurfaceProjectSnapshot(ctx, assistantProjectSurface(ctx)),
            }),
          });
          await assistantReadEventStream(response, (event) => assistantApplyStreamEvent(ctx, event));
        } catch (error) {
          a.streaming = false;
          a.streamingMessageId = '';
          a.status = `Assistant send failed: ${error.message}`;
          assistantRender(ctx);
        }
        return { status: 'ok', action: 'assistantSendLocalMessage' };
      },
      async assistantContinueResponse(ctx) {
        const a = assistantState(ctx);
        if (a.streaming) return { status: 'blocked', action: 'assistantContinueResponse', reason: 'already_streaming' };
        const session = a.activeSession || {};
        const messages = Array.isArray(session.messages) ? session.messages : [];
        const lastAssistant = [...messages].reverse().find((message) => message && message.role === 'assistant' && String(message.text || '').trim());
        if (!lastAssistant || !session.session_id) { a.status = 'No assistant response to continue yet.'; assistantRender(ctx); return { status: 'empty', action: 'assistantContinueResponse' }; }
        if (!assistantBackendConnected(ctx)) {
          a.status = assistantBackendBlockedMessage(ctx);
          assistantRender(ctx);
          return { status: 'blocked', action: 'assistantContinueResponse', reason: 'backend_not_connected' };
        }
        a.status = 'Continuing Assistant response…';
        a.streaming = true;
        a.streamingMessageId = '';
        assistantRender(ctx, { stickToBottom: true });
        try {
          const response = await fetch('/api/assistant/chat-run-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              session_id: session.session_id || '',
              project_id: a.selectedProjectId || session.project_id || 'general',
              message: 'Continue the previous Assistant response from where it stopped.',
              mode: 'continue_response',
              continue_response: true,
              retrieval_profile: a.profile?.retrieval_profile || 'smart',
              source_grounded: !!a.groundedMode,
              profile_id: assistantBackendProfile(ctx)?.profile_id || '',
              active_surface: assistantProjectSurface(ctx),
              surface_context_snapshot: assistantSurfaceProjectSnapshot(ctx, assistantProjectSurface(ctx)),
            }),
          });
          await assistantReadEventStream(response, (event) => assistantApplyStreamEvent(ctx, event));
        } catch (error) {
          a.streaming = false;
          a.streamingMessageId = '';
          a.status = `Assistant continue failed: ${error.message}`;
          assistantRender(ctx);
        }
        return { status: 'ok', action: 'assistantContinueResponse' };
      },
      async assistantUploadAttachments(ctx) {
        const input = ctx.input || document.getElementById('assistant-attachment-input');
        const files = Array.from(input?.files || []);
        if (!files.length) return { status: 'empty', action: 'assistantUploadAttachments' };
        const a = assistantState(ctx);
        if (!a.activeSession) await api.actions.assistantCreateSession(ctx);
        const session = assistantState(ctx).activeSession || {};
        a.pendingAttachments = Array.isArray(a.pendingAttachments) ? a.pendingAttachments : [];
        a.status = `Uploading ${files.length} Assistant attachment${files.length === 1 ? '' : 's'}...`;
        assistantRender(ctx);
        try {
          for (const file of files) {
            const data = new FormData();
            data.append('file', file);
            data.append('session_id', session.session_id || '');
            data.append('project_id', a.selectedProjectId || session.project_id || 'general');
            data.append('kind', file.type && file.type.startsWith('image/') ? 'image' : 'auto');
            const payload = await assistantFetchJson(ctx, '/api/assistant/attachments/upload', { method: 'POST', body: data });
            if (payload.attachment) {
              const exists = (a.pendingAttachments || []).some((item) => item.attachment_id === payload.attachment.attachment_id);
              if (!exists) a.pendingAttachments.push(payload.attachment);
            }
          }
          a.status = `${a.pendingAttachments.length} attachment${a.pendingAttachments.length === 1 ? '' : 's'} ready for next message.`;
        } catch (error) {
          a.status = `Assistant attachment upload failed: ${error.message}`;
        } finally {
          if (input) input.value = '';
          assistantRender(ctx);
        }
        return { status: 'ok', action: 'assistantUploadAttachments' };
      },
      async assistantRemovePendingAttachment(ctx) {
        const a = assistantState(ctx);
        const attachmentId = ctx.attachmentId || ctx.attachment_id || '';
        a.pendingAttachments = (a.pendingAttachments || []).filter((attachment) => attachment.attachment_id !== attachmentId);
        a.status = 'Attachment removed from pending message.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRemovePendingAttachment' };
      },
      assistantSetGroundedMode(ctx) {
        assistantState(ctx).groundedMode = !!ctx.value;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSetGroundedMode' };
      },
      assistantUseStarter(ctx) {
        assistantState(ctx).draft = ctx.text || '';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantUseStarter' };
      },
      assistantClearDraft(ctx) {
        const a = assistantState(ctx);
        a.draft = '';
        a.pendingAttachments = [];
        const el = document.getElementById('assistant-composer-text');
        if (el) el.value = '';
        a.status = 'Draft cleared.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantClearDraft' };
      },
      async assistantClearChat(ctx) {
        const a = assistantState(ctx);
        const session = a.activeSession || {};
        if (!session.session_id) return { status: 'empty', action: 'assistantClearChat' };
        if (!window.confirm('Clear all messages in this Assistant chat? This keeps the session but removes the thread.')) return { status: 'cancelled', action: 'assistantClearChat' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/session-clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: session.session_id }) });
        a.activeSession = payload.session;
        a.sessions = payload.sessions || a.sessions || [];
        a.draft = '';
        a.pendingAttachments = [];
        a.status = 'Chat cleared.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantClearChat' };
      },
      assistantSetSearch(ctx) {
        assistantState(ctx).searchQuery = ctx.value || '';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSetSearch' };
      },
      assistantSetProjectFilter(ctx) {
        assistantState(ctx).selectedProjectId = ctx.value || 'general';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSetProjectFilter' };
      },
      assistantSetScopeFilter(ctx) {
        const result = api.actions.assistantSetProjectFilter(ctx);
        return { ...result, action: 'assistantSetScopeFilter' };
      },
      async assistantCreateProject(ctx) {
        const name = ctx.name || (window.prompt ? window.prompt('Scope name?', 'New scope') : 'New scope');
        if (!name) return { status: 'cancelled', action: 'assistantCreateProject' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/project-create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
        const a = assistantState(ctx);
        a.projects = payload.projects || [];
        a.selectedProjectId = payload.project?.project_id || a.selectedProjectId;
        a.status = 'Scope created';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantCreateProject' };
      },
      async assistantCreateScope(ctx) {
        const result = await api.actions.assistantCreateProject(ctx);
        return { ...result, action: result?.status === 'cancelled' ? 'assistantCreateScope' : 'assistantCreateScope' };
      },
      async assistantRenameProject(ctx) {
        const a = assistantState(ctx);
        const project = activeProject(ctx);
        const name = ctx.name || (window.prompt ? window.prompt('Rename scope:', project.name || 'Scope') : '');
        if (!name) return { status: 'cancelled', action: 'assistantRenameProject' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/project-rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: project.project_id, name }) });
        a.projects = payload.projects || [];
        a.status = 'Scope renamed';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRenameProject' };
      },
      async assistantRenameScope(ctx) {
        const result = await api.actions.assistantRenameProject(ctx);
        return { ...result, action: 'assistantRenameScope' };
      },
      async assistantSaveProjectEditor(ctx) {
        const a = assistantState(ctx);
        const project = activeProject(ctx);
        const payload = {
          project_id: project.project_id,
          name: document.getElementById('assistant-scope-name')?.value || document.getElementById('assistant-project-name')?.value || project.name || 'Scope',
          type: document.getElementById('assistant-scope-type')?.value || document.getElementById('assistant-project-type')?.value || project.type || 'general',
          description: document.getElementById('assistant-scope-description')?.value || document.getElementById('assistant-project-description')?.value || '',
          notes: document.getElementById('assistant-scope-notes')?.value || document.getElementById('assistant-project-notes')?.value || '',
        };
        const result = await assistantFetchJson(ctx, '/api/assistant/project-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        a.projects = result.projects || [];
        a.status = 'Scope saved';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantSaveProjectEditor' };
      },
      async assistantSaveScopeEditor(ctx) {
        const result = await api.actions.assistantSaveProjectEditor(ctx);
        return { ...result, action: 'assistantSaveScopeEditor' };
      },
      async assistantRenameActiveSession(ctx) {
        const a = assistantState(ctx);
        const session = a.activeSession;
        if (!session) return { status: 'skipped', action: 'assistantRenameActiveSession' };
        const title = ctx.title || (window.prompt ? window.prompt('Rename chat:', session.title || 'Assistant chat') : '');
        if (!title) return { status: 'cancelled', action: 'assistantRenameActiveSession' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/session-rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: session.session_id, title }) });
        a.activeSession = payload.session;
        a.sessions = payload.sessions || [];
        a.status = 'Chat renamed';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRenameActiveSession' };
      },
      async assistantDeleteActiveSession(ctx) {
        const a = assistantState(ctx);
        const session = a.activeSession;
        if (!session) return { status: 'skipped', action: 'assistantDeleteActiveSession' };
        if (!ctx.confirmed && window.confirm && !window.confirm('Delete this Assistant chat?')) return { status: 'cancelled', action: 'assistantDeleteActiveSession' };
        const payload = await assistantFetchJson(ctx, '/api/assistant/session-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: session.session_id }) });
        a.sessions = payload.sessions || [];
        a.activeSession = null;
        a.status = 'Chat deleted';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantDeleteActiveSession' };
      },
      async assistantCaptureSelectionAsMemory(ctx) {
        const a = assistantState(ctx);
        const selected = String(window.getSelection?.() || '').trim();
        const draft = document.getElementById('assistant-composer-text')?.value?.trim() || '';
        const text = selected || draft;
        if (!text) { if (window.alert) window.alert('Select text or write something in the composer first.'); return { status: 'empty', action: 'assistantCaptureSelectionAsMemory' }; }
        const payload = await assistantFetchJson(ctx, '/api/assistant/manual-memory-capture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, project_id: a.selectedProjectId || 'general', session_id: a.activeSession?.session_id || '' }) });
        a.memoryCaptures = [payload.capture, ...(a.memoryCaptures || [])];
        a.status = payload.message || 'Memory captured';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantCaptureSelectionAsMemory' };
      },
      async assistantRefreshProjectBrainStatus(ctx) {
        const a = assistantState(ctx);
        const project = activeProject(ctx);
        const surface = assistantProjectSurface(ctx, project);
        const url = `/api/assistant/project-brain/status?project_id=${encodeURIComponent(a.selectedProjectId || project.project_id || 'general')}&surface=${encodeURIComponent(surface)}`;
        a.projectBrain = await assistantFetchJson(ctx, url);
        a.status = 'Project Brain status refreshed.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRefreshProjectBrainStatus' };
      },
      async assistantCaptureCurrentProjectState(ctx) {
        const a = assistantState(ctx);
        const payload = assistantProjectBrainCapturePayload(ctx);
        a.status = 'Capturing current Project Brain state...';
        assistantRender(ctx);
        const result = await assistantFetchJson(ctx, '/api/assistant/project-capture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (result.context_item) a.contextItems = [result.context_item, ...(a.contextItems || [])];
        a.projectBrain = result.project_brain || a.projectBrain || null;
        a.status = 'Current state captured into Project Brain.';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantCaptureCurrentProjectState' };
      },
      async assistantIndexProjectData(ctx) {
        const a = assistantState(ctx);
        const project = activeProject(ctx);
        const surface = assistantProjectSurface(ctx, project);
        a.status = 'Indexing Neo-owned project data...';
        assistantRender(ctx);
        const result = await assistantFetchJson(ctx, '/api/assistant/project-index', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: a.selectedProjectId || project.project_id || 'general', surface }) });
        if (result.context_item) a.contextItems = [result.context_item, ...(a.contextItems || [])];
        a.projectBrain = result.project_brain || a.projectBrain || null;
        a.status = `Indexed ${result.index?.record_count || 0} project data record(s).`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantIndexProjectData' };
      },
      async assistantRebuildProjectBrain(ctx) {
        const a = assistantState(ctx);
        const project = activeProject(ctx);
        const surface = assistantProjectSurface(ctx, project);
        a.status = 'Rebuilding Project Brain...';
        assistantRender(ctx);
        const result = await assistantFetchJson(ctx, '/api/assistant/project-brain-rebuild', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: a.selectedProjectId || project.project_id || 'general', surface }) });
        a.projectBrain = result.project_brain || a.projectBrain || null;
        a.status = `Project Brain rebuilt: ${result.report?.metadata_record_count || 0} indexed metadata record(s).`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantRebuildProjectBrain' };
      },
      async assistantUploadProjectFiles(ctx) {
        const a = assistantState(ctx);
        const input = ctx.input || null;
        const files = Array.from(input?.files || []);
        if (!files.length) return { status: 'empty', action: 'assistantUploadProjectFiles' };
        const project = activeProject(ctx);
        const surface = assistantProjectSurface(ctx, project);
        a.status = `Uploading ${files.length} Project Brain file${files.length === 1 ? '' : 's'}...`;
        assistantRender(ctx);
        let last = null;
        for (const file of files) {
          const data = new FormData();
          data.append('file', file);
          data.append('project_id', a.selectedProjectId || project.project_id || 'general');
          data.append('surface', surface);
          data.append('session_id', a.activeSession?.session_id || '');
          last = await assistantFetchJson(ctx, '/api/assistant/project-files/upload', { method: 'POST', body: data });
          if (last.context_item) a.contextItems = [last.context_item, ...(a.contextItems || [])];
        }
        if (input) input.value = '';
        a.projectBrain = last?.project_brain || a.projectBrain || null;
        a.status = `${files.length} Project Brain file${files.length === 1 ? '' : 's'} uploaded.`;
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantUploadProjectFiles' };
      },
      async assistantPreviewContextPack(ctx) {
        const a = assistantState(ctx);
        const message = document.getElementById('assistant-composer-text')?.value || a.draft || '';
        const url = `/api/assistant/context-pack-preview?session_id=${encodeURIComponent(a.activeSession?.session_id || '')}&project_id=${encodeURIComponent(a.selectedProjectId || 'general')}&message=${encodeURIComponent(message)}&retrieval_profile=${encodeURIComponent(a.profile?.retrieval_profile || 'smart')}&surface=${encodeURIComponent(assistantProjectSurface(ctx))}`;
        a.contextPreview = await assistantFetchJson(ctx, url);
        a.status = 'Context pack preview refreshed';
        assistantRender(ctx);
        return { status: 'ok', action: 'assistantPreviewContextPack' };
      },
    },
  };

  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('assistant', api);
  else {
    window.NeoSurfaceModules = window.NeoSurfaceModules || {};
    window.NeoSurfaceModules.assistant = api;
  }
})();
