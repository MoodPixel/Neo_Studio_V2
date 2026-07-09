// Neo Studio V2 surface module: roleplay
// Safe Roleplay module status. Legacy neo.js wrappers remain as fallbacks while this module owns selected Compile/Runtime/Scene/Stories/Archive/Provenance actions.
(function () {
  function render(ctx) { if (typeof ctx.render === 'function') ctx.render(); }
  function stateOf(ctx) { return ctx.state || {}; }
  function escapeHtml(ctx, value) { return typeof ctx.escapeHtml === 'function' ? ctx.escapeHtml(value) : String(value ?? ''); }
  function escapeAttr(ctx, value) { return typeof ctx.escapeAttr === 'function' ? ctx.escapeAttr(value) : escapeHtml(ctx, value).replace(/"/g, '&quot;'); }
  function badgeRow(ctx, items = []) { return ctx.NeoUI?.badgeRow ? ctx.NeoUI.badgeRow(items.filter(Boolean)) : `<div>${items.filter(Boolean).map((item)=>`<span>${escapeHtml(ctx,item)}</span>`).join('')}</div>`; }
  function metaList(ctx, items = []) { return ctx.NeoUI?.metaList ? ctx.NeoUI.metaList(items, { code: false, empty: 'No rows yet.' }) : `<ul>${items.map((i)=>`<li>${escapeHtml(ctx,i)}</li>`).join('')}</ul>`; }
  async function loadJson(ctx, url, fallback = null) {
    if (typeof ctx.loadJson === 'function') return ctx.loadJson(url, fallback);
    try { const r = await fetch(url); if (!r.ok) return fallback; return await r.json(); } catch { return fallback; }
  }
  async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 60000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), Math.max(5000, Number(timeoutMs || 60000)));
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const result = await response.json().catch(() => ({}));
      return { response, result };
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error(`Request timed out after ${Math.round(Math.max(5000, Number(timeoutMs || 60000)) / 1000)}s.`);
      throw error;
    } finally { window.clearTimeout(timer); }
  }
  function getValue(id, fallback = '') { return document.getElementById(id)?.value ?? fallback; }
  function isChecked(id, fallback = false) { const el = document.getElementById(id); return el ? Boolean(el.checked) : Boolean(fallback); }

  function scopeBuildLevel(ctx) {
    const s = stateOf(ctx);
    const explicit = s.roleplayScopeBuildLevel || getValue('roleplay-scope-build-level', '');
    if (explicit) return explicit;
    const counts = s.roleplayScopeBuild?.scope_counts || {};
    return ['universe','world','scenario','region','city','location','project'].find((kind)=>Number(counts[kind] || 0)>0) || 'universe';
  }
  function scopesForLevel(ctx, level = '') {
    const s = stateOf(ctx); const clean = level || scopeBuildLevel(ctx); const byType = s.roleplayScopeBuild?.scopes_by_type || {};
    if (Array.isArray(byType[clean])) return byType[clean];
    const scopes = s.roleplayScopeBuild?.scopes || [];
    return scopes.filter((item)=>item.scope_type === clean || item.record_kind === clean || item.kind === clean);
  }
  function selectedScopeValue(ctx) {
    const s = stateOf(ctx); const level = scopeBuildLevel(ctx); const current = s.roleplayScopeBuildSelected || '';
    if (current && current.startsWith(`${level}::`)) return current;
    const first = scopesForLevel(ctx, level)[0] || {};
    return first.scope_type && first.scope_id ? `${first.scope_type}::${first.scope_id}` : '';
  }
  function setScopeSelection(ctx) {
    const s = stateOf(ctx);
    s.roleplayScopeBuildLevel = getValue('roleplay-scope-build-level', scopeBuildLevel(ctx));
    s.roleplayScopeBuildSelected = getValue('roleplay-scope-build-select', '');
    s.roleplayScopeBuildPreview = null;
  }
  function scopePayload(ctx) {
    const s = stateOf(ctx);
    const level = getValue('roleplay-scope-build-level', scopeBuildLevel(ctx));
    const raw = getValue('roleplay-scope-build-select', selectedScopeValue(ctx));
    const [rawType, ...rest] = raw.split('::');
    const payload = {
      scope_type: rawType || level || 'world',
      scope_id: rest.join('::'),
      graph_depth: Number(getValue('roleplay-scope-build-depth', 2)),
      include_reverse_links: isChecked('roleplay-scope-build-reverse', true),
      include_scope_family: isChecked('roleplay-scope-build-family', true),
      mode: getValue('roleplay-scope-build-mode', 'changed_only'),
      include_compiled: isChecked('roleplay-scope-build-include-compiled', false),
      kind: getValue('roleplay-scope-build-kind', ''),
      compile_memory: isChecked('roleplay-scope-build-compile', true),
      rebuild_search: isChecked('roleplay-scope-build-rebuild', true),
      index_after: isChecked('roleplay-scope-build-index', true),
      mirror_after: isChecked('roleplay-scope-build-mirror', false),
      build_runtime: isChecked('roleplay-scope-build-runtime', true),
      bundle_title: getValue('roleplay-scope-build-title', ''),
    };
    s.roleplayScopeBuildOptions = { ...payload };
    return payload;
  }

  function runtimePresetSelectedValue(ctx) {
    const s = stateOf(ctx);
    const fromUi = getValue('roleplay-runtime-preset-select', '');
    if (fromUi) return fromUi;
    if (s.roleplayRuntimePresetSelected) return s.roleplayRuntimePresetSelected;
    return (s.roleplayRuntimePresets?.latest_preset || s.roleplayRuntimePresets?.presets?.[0] || {}).preset_id || '';
  }
  function runtimePacketPayload(ctx) {
    return {
      preset_id: runtimePresetSelectedValue(ctx),
      scenario_id: getValue('roleplay-runtime-preset-scenario', ''),
      player_character_ids: getValue('roleplay-runtime-preset-player', ''),
      query: getValue('roleplay-runtime-preset-query', ''),
      title: getValue('roleplay-runtime-preset-packet-title', ''),
      mode: getValue('roleplay-runtime-preset-mode', 'hybrid'),
      limit: Number(getValue('roleplay-runtime-preset-limit', 8)),
      candidate_limit: Number(getValue('roleplay-runtime-preset-candidate-limit', 24)),
      rerank_candidate_limit: Number(getValue('roleplay-runtime-preset-rerank-candidate-limit', 8)),
      rerank: document.getElementById('roleplay-runtime-preset-rerank')?.checked !== false,
      run_retrieval: isChecked('roleplay-runtime-preset-run-retrieval', false),
    };
  }

  function syncSceneControls(ctx) {
    const s = stateOf(ctx); const isStreaming = !!s.roleplaySceneStreaming;
    const pairs = [
      ['roleplay-scene-stream-btn', isStreaming, isStreaming ? 'Streaming…' : 'Stream scene turn'],
      ['roleplay-scene-continue-btn', isStreaming, null],
      ['roleplay-scene-send-btn', isStreaming, null],
      ['roleplay-scene-stop-btn', !isStreaming, null],
    ];
    pairs.forEach(([id, disabled, label])=>{ const el=document.getElementById(id); if(el){ el.disabled=!!disabled; if(label) el.textContent=label; }});
  }

  async function saveSceneSetup(ctx, options = {}) {
    const s = stateOf(ctx);
    const payload = {
      scene_id: s.roleplayScene?.scene_id || 'default',
      title: getValue('roleplay-scene-title', s.roleplayScene?.setup?.title || 'Untitled Scene'),
      premise: getValue('roleplay-scene-premise', ''),
      tone: getValue('roleplay-scene-tone', 'Scene-defined'),
      reply_style: getValue('roleplay-scene-style', 'Scene-defined prose'),
      scene_notes: getValue('roleplay-scene-notes', ''),
      narrator_posture: getValue('roleplay-scene-posture', 'partner_focus'),
      continuity_mode: getValue('roleplay-scene-continuity-mode', 'runtime_anchored'),
      runtime_bundle_id: getValue('roleplay-scene-runtime', ''),
      scene_packet_id: s.roleplayScene?.setup?.scene_packet_id || s.roleplayRuntime?.last_scene_packet_id || '',
      turn_input_style: getValue('roleplay-scene-turn-input-style', 'free_typing'),
      autosave_checkpoint: isChecked('roleplay-scene-autosave', false),
      participants: getValue('roleplay-scene-participants', s.roleplayScene?.setup?.participants || ''),
      memory_scope: getValue('roleplay-scene-memory-scope', s.roleplayScene?.setup?.memory_scope || 'roleplay.scene'),
      scene_rules: getValue('roleplay-scene-rules', s.roleplayScene?.setup?.scene_rules || ''),
    };
    const response = await fetch('/api/roleplay/scene/setup/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || result.error || 'Scene setup save failed');
    s.roleplayScene = result.scene || await loadJson(ctx, '/api/roleplay/scene/state', null);
    if (options.renderAfter !== false) render(ctx);
    return s.roleplayScene;
  }

  async function pollPacketJob(ctx, jobId) {
    const s = stateOf(ctx); const started = Date.now();
    while (true) {
      const { response, result } = await fetchJsonWithTimeout(`/api/roleplay/runtime-presets/build-scene-packet-job/${encodeURIComponent(jobId)}`, { method: 'GET' }, 30000);
      if (!response.ok) throw new Error(result.error || result.detail || 'Scene packet job status failed');
      const job = result.job || result; const elapsed = Math.round((Date.now() - started) / 1000);
      s.roleplayRuntimePacketProgress = { status: job.status || 'running', label: job.status === 'built' ? 'Scene packet built' : job.status === 'failed' ? 'Scene packet build failed' : 'Building scene packet…', detail: `${job.message || 'Retrieval/rerank is still running.'} · ${elapsed}s elapsed`, step: job.stage || 'background_job', job_id: jobId, started_at: job.started_at, updated_at: job.updated_at, elapsed_ms: job.elapsed_ms };
      render(ctx);
      if (job.status === 'built') return job.result || {};
      if (job.status === 'failed' || job.status === 'missing') throw new Error(job.error || job.message || 'Scene packet background job failed');
      await new Promise((resolve)=>setTimeout(resolve, 2000));
    }
  }




  function appendSceneTurn(ctx, role, text, status = 'streaming', displayRole = '') {
    const wrap = document.querySelector('.roleplay-v1-scene-transcript');
    if (!wrap) return null;
    let list = wrap.querySelector('.roleplay-v1-scene-transcript-list');
    if (!list) {
      wrap.innerHTML = '<div class="roleplay-v1-scene-transcript-list"></div>';
      list = wrap.querySelector('.roleplay-v1-scene-transcript-list');
    }
    const node = document.createElement('div');
    node.className = `roleplay-v1-scene-turn ${role}`;
    node.id = `module-stream-turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    node.innerHTML = `<strong>${escapeHtml(ctx, displayRole || role)}</strong><p></p><span>${escapeHtml(ctx, status)} · live</span>`;
    const p = node.querySelector('p');
    if (p) p.textContent = text || '';
    list.appendChild(node);
    node.scrollIntoView({ block: 'nearest' });
    return node;
  }

  function setTurnNode(node, text, status = '', roleClass = '', displayRole = '') {
    if (!node) return;
    if (roleClass) node.className = `roleplay-v1-scene-turn ${roleClass}`;
    if (displayRole) { const strong = node.querySelector('strong'); if (strong) strong.textContent = displayRole; }
    const p = node.querySelector('p');
    if (p) p.textContent = text || '';
    const span = node.querySelector('span');
    if (span && status) span.textContent = status;
  }

  function sceneTurnPayload(ctx, continueScene = false, capturedMessage = null) {
    const s = stateOf(ctx);
    const input = document.getElementById('roleplay-scene-user-turn');
    const message = capturedMessage !== null ? String(capturedMessage || '') : (input?.value || '');
    return {
      scene_id: s.roleplayScene?.scene_id || 'default',
      message: continueScene && !message.trim() ? '[Continue the scene from the current transcript state.]' : message,
      continue_scene: !!continueScene,
      runtime_bundle_id: getValue('roleplay-scene-runtime', s.roleplayScene?.setup?.runtime_bundle_id || ''),
      scene_packet_id: s.roleplayScene?.setup?.scene_packet_id || s.roleplayRuntime?.last_scene_packet_id || s.roleplayScenePacket?.packet_id || '',
      profile_id: s.roleplayScene?.setup?.active_profile_id || s.roleplayScene?.text_backend?.active_profile_id || s.roleplayTextBackend?.active_profile_id || '',
      enable_live_retrieval: isChecked('roleplay-scene-live-retrieval', false),
      enable_memory_engine: false,
      active_user_character_name: getValue('roleplay-scene-active-user-character', s.roleplayScene?.session_setup?.active_user_character_name || ''),
      control_mode: getValue('roleplay-scene-control-mode', s.roleplayScene?.session_setup?.control_mode || 'strict'),
      turn_input_style: getValue('roleplay-scene-turn-input-style', s.roleplayScene?.setup?.turn_input_style || 'free_typing'),
      prompt_context_max_chars: 11000,
      dispatch_owner: 'roleplay_surface_module_m18_7',
    };
  }

  async function refreshSceneAfterTurn(ctx) {
    const s = stateOf(ctx);
    s.roleplayScene = await loadJson(ctx, '/api/roleplay/scene/state', s.roleplayScene || null);
    s.roleplayMemory = await loadJson(ctx, '/api/roleplay/memory/state', s.roleplayMemory || null);
    s.roleplayRetrieval = await loadJson(ctx, '/api/roleplay/retrieval/state', s.roleplayRetrieval || null);
    s.roleplaySceneDirectorStatus = await loadJson(ctx, '/api/roleplay/scene-director/status', s.roleplaySceneDirectorStatus || null);
  }

  async function executeSceneTurnNonStream(ctx, continueScene = false) {
    const s = stateOf(ctx);
    const input = document.getElementById('roleplay-scene-user-turn');
    const capturedMessage = input?.value || '';
    if (!continueScene && !capturedMessage.trim()) return;
    if (s.roleplaySceneStreaming) return;
    if (s.roleplayScene?.session_setup?.needs_setup || s.roleplayScene?.session_setup?.status !== 'ready') { alert('Start the Roleplay session from the Scene setup card first.'); return; }
    const payload = sceneTurnPayload(ctx, continueScene, capturedMessage);
    // Do not autosave the Scene setup immediately before dispatch. Session setup
    // is stored separately in the transcript; a setup autosave can compare packet
    // identities before the first real turn and accidentally reset the ready
    // session, causing the backend dispatch to be skipped.
    appendSceneTurn(ctx, 'user', payload.message, 'submitted_non_stream', payload.active_user_character_name || 'User');
    const assistantNode = appendSceneTurn(ctx, 'assistant', '[Sending to backend…]', 'non_stream_pending');
    syncSceneControls(ctx);
    try {
      const response = await fetch('/api/roleplay/scene/turn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || result.message || result.error || 'Scene turn execution failed');
      if (!result.ok) {
        const detail = result.message || result.error || result.status || result.result?.error || result.result?.status || 'Scene turn execution failed';
        const resultStatus = result.result?.status || result.status || 'scene turn';
        setTurnNode(assistantNode, `[Scene send failed: ${detail}]`, `${resultStatus} · backend`, 'system');
        console.warn('Roleplay scene turn failed', result);
        s.roleplayScene = result.scene || s.roleplayScene || await loadJson(ctx, '/api/roleplay/scene/state', null);
        return;
      }
      await refreshSceneAfterTurn(ctx);
      if (input && result.ok) input.value = '';
      render(ctx);
    } catch (error) {
      setTurnNode(assistantNode, `[Scene send failed before/backend response: ${error.message || 'Scene turn execution failed'}]`, 'send_error · module', 'system');
      console.warn('Roleplay non-stream dispatch failed', error);
    }
  }

  async function executeSceneTurnStream(ctx, continueScene = false) {
    const s = stateOf(ctx);
    const input = document.getElementById('roleplay-scene-user-turn');
    const capturedMessage = input?.value || '';
    if (!continueScene && !capturedMessage.trim()) return;
    if (s.roleplaySceneStreaming) return;
    if (s.roleplayScene?.session_setup?.needs_setup || s.roleplayScene?.session_setup?.status !== 'ready') { alert('Start the Roleplay session from the Scene setup card first.'); return; }
    const payload = sceneTurnPayload(ctx, continueScene, capturedMessage);
    // Do not autosave the Scene setup immediately before dispatch. Session setup
    // is stored separately in the transcript; a setup autosave can compare packet
    // identities before the first real turn and accidentally reset the ready
    // session, causing the backend dispatch to be skipped.
    const controller = new AbortController();
    s.roleplaySceneAbortController = controller;
    s.roleplaySceneStreaming = true;
    syncSceneControls(ctx);
    appendSceneTurn(ctx, 'user', payload.message, 'submitted_stream', payload.active_user_character_name || 'User');
    const assistantNode = appendSceneTurn(ctx, 'assistant', '[Preparing Scene Director dispatch…]', 'streaming');
    let assistantText = '';
    let streamHadError = false;
    let streamDoneOk = false;
    try {
      const response = await fetch('/api/roleplay/scene/turn-stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal });
      if (!response.ok || !response.body) throw new Error('Scene stream failed to start');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const handleEvent = (raw) => {
        const dataLine = raw.split('\n').find((line) => line.startsWith('data:'));
        if (!dataLine) return;
        let data = {};
        try { data = JSON.parse(dataLine.slice(5).trim() || '{}'); } catch (error) { console.warn('Invalid Roleplay stream event', raw, error); return; }
        if (data.type === 'token') {
          assistantText += data.text || '';
          setTurnNode(assistantNode, assistantText, 'streaming · live');
          assistantNode?.scrollIntoView({ block: 'nearest' });
        } else if (data.type === 'status' || data.type === 'start' || data.type === 'backend_start') {
          const status = `${data.status || data.type || 'running'} · live`;
          if (!assistantText && data.message) setTurnNode(assistantNode, `[${data.message}]`, status);
          else setTurnNode(assistantNode, assistantText, status);
        } else if (data.type === 'error') {
          streamHadError = true;
          setTurnNode(assistantNode, `[Stream error: ${data.error || data.status || 'unknown'}]`, `${data.status || 'stream_error'} · live`, 'system');
          console.warn('Roleplay scene stream error', data);
        } else if (data.type === 'done') {
          const doneStatus = String(data.status || '');
          const backendSkipped = !!(data.execution && data.execution.backend_skipped);
          streamDoneOk = !!data.ok && !backendSkipped && !doneStatus.startsWith('scene_session_setup');
          const finalText = data.assistant_turn?.text || assistantText || (data.error ? `[Stream failed: ${data.error}]` : '');
          const roleClass = streamDoneOk ? 'assistant' : 'system';
          setTurnNode(assistantNode, finalText, `${doneStatus || (streamDoneOk ? 'streamed' : 'stream_error')} · ${streamDoneOk ? 'saved' : 'not_saved'}`, roleClass, data.assistant_turn?.display_role || (streamDoneOk ? 'Neo' : 'System'));
        }
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) if (part.trim()) handleEvent(part);
      }
      if (buffer.trim()) handleEvent(buffer);
      if (input && !continueScene && streamDoneOk && !streamHadError) input.value = '';
      await refreshSceneAfterTurn(ctx);
      if (streamDoneOk && !streamHadError) render(ctx);
    } catch (error) {
      if (error?.name !== 'AbortError') {
        const message = error?.message || 'Scene stream failed';
        setTurnNode(assistantNode, `[Stream connection failed: ${message}. Try Send non-stream once, then check the backend terminal if this repeats.]`, 'network_error · module', 'system');
        console.warn('Roleplay stream dispatch failed', error);
      }
    } finally {
      s.roleplaySceneStreaming = false;
      s.roleplaySceneAbortController = null;
      syncSceneControls(ctx);
    }
  }


  async function appendTranscriptPlaceholder(ctx) {
    const s = stateOf(ctx);
    const input = document.getElementById('roleplay-scene-user-turn');
    const message = input?.value || '';
    await saveSceneSetup(ctx, { renderAfter: false });
    const payload = { scene_id: s.roleplayScene?.scene_id || 'default', message };
    const userNode = appendSceneTurn(ctx, 'user', message || '[placeholder]', 'placeholder_pending');
    try {
      const response = await fetch('/api/roleplay/scene/transcript/append-placeholder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || result.error || 'Scene transcript append failed');
      s.roleplayScene = result.scene || await loadJson(ctx, '/api/roleplay/scene/state', null);
      render(ctx);
    } catch (error) {
      setTurnNode(userNode, `[Transcript placeholder failed: ${error.message || 'append failed'}]`, 'placeholder_error · module', 'system');
      console.warn('Roleplay transcript placeholder append failed', error);
    }
  }

  async function continueTranscriptPlaceholder(ctx) {
    const input = document.getElementById('roleplay-scene-user-turn');
    if (input && !input.value.trim()) input.value = '[Continue scene from the current transcript state.]';
    await appendTranscriptPlaceholder(ctx);
  }



  function sceneSessionSetupHtml(ctx, scene) {
    const setup = scene?.session_setup || {};
    const roster = Array.isArray(setup.roster) ? setup.roster : [];
    if (!roster.length || setup.played) return '';
    if (setup.status === 'ready') {
      const readyText = [
        'Scene ready.',
        '',
        `You are playing: ${(setup.user_controls || []).join(', ') || 'selected character/s'}`,
        `Neo controls: ${(setup.neo_controls || []).join(', ') || 'environment only'}`,
        `Control mode: ${String(setup.control_mode || 'strict').replace(/^./, (c)=>c.toUpperCase())}`,
        '',
        'Start with your first scene beat.'
      ].join('\n');
      return `<section class="roleplay-v1-scene-status roleplay-session-setup-card ready"><div class="roleplay-v1-scene-row-between"><div><strong>Scene ready</strong><pre class="roleplay-session-ready-text">${escapeHtml(ctx, readyText)}</pre></div><span class="neo-chip success">ready</span></div></section>`;
    }
    const rows = roster.map((row) => `<label class="roleplay-session-character"><input type="checkbox" value="${escapeHtml(ctx, String(row.number || ''))}" ${(row.default_control === 'user') ? 'checked' : ''}> <strong>${escapeHtml(ctx, `${row.number || ''}. ${row.name || 'Character'}`)}</strong><span>${escapeHtml(ctx, [row.pronouns || row.gender || '', row.default_control === 'user' ? 'scenario default' : 'Neo by default'].filter(Boolean).join(' · '))}</span></label>`).join('');
    return `<section class="roleplay-v1-scene-status roleplay-session-setup-card"><div class="roleplay-v1-scene-row-between"><div><strong>Scene setup</strong><p>Select the character/s you will play for this Roleplay session. This setup is Roleplay-only and is not shown in Novel mode.</p></div><span class="neo-chip">setup needed</span></div><div class="roleplay-session-character-list">${rows}</div><div class="roleplay-v1-studio-grid two compact"><label>Control mode<select id="roleplay-scene-control-mode"><option value="strict" ${(setup.control_mode || 'strict') === 'strict' ? 'selected' : ''}>Strict Mode — no user-character actions/dialogue</option><option value="moderate" ${setup.control_mode === 'moderate' ? 'selected' : ''}>Moderate Mode — light continuity references only</option></select></label></div><div class="neo-actions roleplay-v1-scene-actions"><button type="button" class="neo-btn primary admin-engine-btn" onclick="roleplaySaveSceneSessionSetup()">Start Roleplay Session</button></div><p class="neo-muted">Strict is best for true multi-character RP. Moderate is best when you want cinematic continuity without Neo speaking or deciding for your character.</p></section>`;
  }

  function sceneContextBudgetHtml(ctx, scene) {
    const budget = scene?.context_budget || {};
    if (!budget.context_window_tokens) return '';
    const pct = Number(budget.percent || 0);
    const status = budget.status || 'ok';
    return `<section class="roleplay-v1-scene-status roleplay-context-budget ${status}"><div class="roleplay-v1-scene-row-between"><div><strong>Context budget</strong><p>Estimated ${escapeHtml(ctx, String(budget.estimated_tokens || 0))}/${escapeHtml(ctx, String(budget.context_window_tokens || 0))} tokens · ${escapeHtml(ctx, String(budget.percent || 0))}% used.</p></div><span class="neo-chip">${escapeHtml(ctx, status)}</span></div>${budget.needs_continuation_session ? '<div class="neo-actions roleplay-v1-scene-actions"><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayStartSceneContinuationSession()">Start continuation session</button></div>' : ''}</section>`;
  }

  async function saveSceneSessionSetup(ctx) {
    const s = stateOf(ctx);
    const checked = Array.from(document.querySelectorAll('.roleplay-session-character input[type="checkbox"]:checked')).map((el)=>el.value);
    const numbers = checked.join(',');
    if (!numbers) { alert('Choose at least one character to play before starting the session.'); return; }
    const mode = getValue('roleplay-scene-control-mode', 'strict');
    const payload = { scene_id: s.roleplayScene?.scene_id || 'default', runtime_bundle_id: getValue('roleplay-scene-runtime', s.roleplayScene?.setup?.runtime_bundle_id || ''), user_control_numbers: numbers, control_mode: mode };
    const response = await fetch('/api/roleplay/scene/session/setup', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const result = await response.json().catch(()=>({}));
    if (!response.ok || !result.ok) throw new Error(result.error || result.detail || 'Scene session setup failed');
    s.roleplayScene = result.scene || await loadJson(ctx, '/api/roleplay/scene/state', s.roleplayScene || null);
    render(ctx);
  }

  async function startSceneContinuationSession(ctx) {
    const s = stateOf(ctx);
    if (!window.confirm('Start a new continuation session from the current transcript summary? The current transcript will be archived first.')) return;
    const response = await fetch('/api/roleplay/scene/session/continue-new', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ scene_id: s.roleplayScene?.scene_id || 'default' }) });
    const result = await response.json().catch(()=>({}));
    if (!response.ok || !result.ok) throw new Error(result.error || result.detail || 'Continuation session failed');
    s.roleplayScene = result.scene || await loadJson(ctx, '/api/roleplay/scene/state', s.roleplayScene || null);
    render(ctx);
  }

  function checkpointPayload(ctx) {
    const s = stateOf(ctx);
    const scene = s.roleplayScene || {};
    const setup = scene.setup || {};
    const chat = scene.chat || {};
    const transcript = chat.transcript || scene.transcript || {};
    const turns = transcript.turns || [];
    const summary = turns.slice(-8).map((turn) => `${turn.role || 'turn'}: ${turn.text || turn.content || ''}`).join('\n').slice(0, 2000);
    return {
      scene_id: scene.scene_id || 'default',
      storyline_id: s.roleplayStories?.active_storyline_id || s.roleplayStories?.workspace?.active_storyline_id || 'unassigned',
      session_id: s.roleplayStories?.active_session_id || s.roleplayStories?.workspace?.active_session_id || 'unassigned',
      title: setup.title ? `${setup.title} checkpoint` : 'Scene checkpoint',
      summary: summary || setup.premise || 'Scene checkpoint saved from Scene transcript.',
      turn_count: turns.length,
      source: 'scene_live_module_m18_8',
      runtime_bundle_id: setup.runtime_bundle_id || '',
      scene_packet_id: setup.scene_packet_id || scene.scene_memory_injection?.scene_packet_id || '',
      scene_packet: scene.scene_packet || scene.scene_memory_injection?.scene_packet || {},
      scene_setup: setup,
      transcript,
      module_owner: 'roleplay_surface_module_m18_8',
    };
  }

  async function createSceneCheckpoint(ctx) {
    const s = stateOf(ctx);
    const payload = checkpointPayload(ctx);
    s.roleplaySceneCheckpointProgress = { status: 'running', label: 'Saving Scene checkpoint…', detail: `Capturing ${payload.turn_count || 0} transcript turns with runtime/packet context.`, started_at: new Date().toISOString() };
    render(ctx);
    try {
      const response = await fetch('/api/roleplay/story-checkpoint/capture-active', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.status === 'error') throw new Error(result.detail || result.error || 'Roleplay checkpoint save failed');
      s.roleplayStories = result.stories || await loadJson(ctx, '/api/roleplay/stories/state', s.roleplayStories || null);
      s.roleplayMemory = await loadJson(ctx, '/api/roleplay/memory/state', s.roleplayMemory || null);
      s.roleplayRetrieval = await loadJson(ctx, '/api/roleplay/retrieval/state', s.roleplayRetrieval || null);
      s.roleplayScene = await loadJson(ctx, '/api/roleplay/scene/state', s.roleplayScene || null);
      s.roleplaySceneCheckpointProgress = { status: 'saved', label: 'Scene checkpoint saved', detail: `${payload.title} · ${payload.turn_count || 0} turns`, finished_at: new Date().toISOString() };
      render(ctx);
    } catch (error) {
      s.roleplaySceneCheckpointProgress = { status: 'failed', label: 'Scene checkpoint failed', detail: error.message || 'Roleplay checkpoint save failed', finished_at: new Date().toISOString() };
      render(ctx);
      throw error;
    }
  }


  function checkpointDiffHtml(ctx) {
    const s = stateOf(ctx);
    const diff = s.roleplayCheckpointDiff || null;
    if (!diff) return '<div class="roleplay-stories-note">No checkpoint diff loaded yet. Select two checkpoints and compare.</div>';
    const summary = diff.summary || {};
    const setupRows = (diff.setup_diff || []).slice(0, 12).map((row) => `<li><strong>${escapeHtml(ctx, row.path || '')}</strong>: ${escapeHtml(ctx, String(row.before ?? ''))} → ${escapeHtml(ctx, String(row.after ?? ''))}</li>`).join('') || '<li>No setup changes.</li>';
    const turnRows = (diff.turn_diff || []).slice(0, 8).map((row) => `<li><strong>#${escapeHtml(ctx, row.index ?? '')}</strong> ${escapeHtml(ctx, row.change_type || 'changed')} · ${escapeHtml(ctx, String(row.before?.role || ''))} → ${escapeHtml(ctx, String(row.after?.role || ''))}</li>`).join('') || '<li>No transcript changes.</li>';
    return `<div class="roleplay-memory-card"><strong>Checkpoint diff</strong>${metaList(ctx,[`Meta changes: ${summary.meta_changes ?? 0}`, `Setup changes: ${summary.setup_changes ?? 0}`, `Turn changes: ${summary.turn_changes ?? 0}`, `A turns: ${summary.left_turns ?? 0}`, `B turns: ${summary.right_turns ?? 0}`])}<div class="roleplay-v1-grid two"><div><h4>Setup</h4><ul>${setupRows}</ul></div><div><h4>Transcript</h4><ul>${turnRows}</ul></div></div></div>`;
  }

  function sceneStateInspectorHtml(ctx) {
    const s = stateOf(ctx);
    const scene = s.roleplayScene || {};
    const setup = scene.setup || {};
    const chat = scene.chat || {};
    const transcript = chat.transcript || scene.transcript || {};
    const turns = Array.isArray(transcript.turns) ? transcript.turns : (Array.isArray(scene.turns) ? scene.turns : []);
    const injection = scene.scene_memory_injection || {};
    const director = s.roleplaySceneDirectorStatus || {};
    const checkpointProgress = s.roleplaySceneCheckpointProgress || {};
    const latestTurns = turns.slice(-6).map((turn, idx) => `${turn.role || 'turn'}: ${String(turn.text || turn.content || '').slice(0, 160)}`);
    return `<section class="neo-ui-card roleplay-scene-state-inspector">
      <div class="neo-ui-section-head"><div><strong>Roleplay Scene State Inspector</strong><p class="neo-muted">Live scene state, packet binding, transcript health, and checkpoint readiness.</p></div>${badgeRow(ctx,[scene.scene_id || setup.scene_id || 'default', injection.status || 'packet?', turns.length ? `${turns.length} turns` : 'no turns', checkpointProgress.status || 'checkpoint idle'])}</div>
      <div class="neo-inline-actions"><button class="neo-btn secondary" type="button" onclick="roleplaySceneStateInspectorRefresh()">Refresh inspector</button><button class="neo-btn secondary" type="button" onclick="createRoleplaySceneCheckpointFromUi()">Save checkpoint</button><button class="neo-btn secondary" type="button" onclick="refreshRoleplayStoriesFromUi()">Refresh Stories</button></div>
      <div class="neo-ui-grid two">
        <div>${metaList(ctx,[`Scene: ${scene.scene_id || setup.scene_id || 'default'}`, `Title: ${setup.title || scene.title || 'Untitled'}`, `Runtime: ${setup.runtime_bundle_id || 'none'}`, `Packet: ${setup.scene_packet_id || injection.scene_packet_id || 'none'}`, `Memory scope: ${setup.memory_scope || 'roleplay.scene'}`])}</div>
        <div>${metaList(ctx,[`Backend: ${scene.text_backend?.active_profile_id || 'default'}`, `Director: ${director.status || 'unknown'}`, `Streaming: ${s.roleplaySceneStreaming ? 'active' : 'idle'}`, `Autosave: ${setup.autosave_checkpoint ? 'on' : 'off'}`, `Last checkpoint: ${checkpointProgress.label || 'none'}`])}</div>
      </div>
      <details class="neo-ui-details" open><summary>Recent transcript turns</summary>${latestTurns.length ? metaList(ctx, latestTurns) : '<p class="neo-muted">No transcript turns loaded yet.</p>'}</details>
      <details class="neo-ui-details"><summary>Scene setup payload</summary><pre class="neo-code-block small">${escapeHtml(ctx, JSON.stringify(setup, null, 2)).slice(0, 5000)}</pre></details>
      <details class="neo-ui-details"><summary>Scene memory injection</summary><pre class="neo-code-block small">${escapeHtml(ctx, JSON.stringify(injection, null, 2)).slice(0, 5000)}</pre></details>
    </section>`;
  }

  function checkpointInspectorHtml(ctx) {
    const s = stateOf(ctx);
    const stories = s.roleplayStories || {};
    const workspace = stories.workspace || {};
    const checkpoints = workspace.checkpoints || stories.checkpoints || [];
    const branches = workspace.branches || stories.branches || [];
    const restore = s.roleplayStoryRestore || {};
    const restoreCounts = restore.counts || {};
    const selectedLeft = s.roleplayDiffLeftCheckpointId || getValue('roleplay-diff-left', '');
    const checkpointCards = checkpoints.slice(0, 8).map((item) => {
      const id = item.checkpoint_id || item.record_id || item.id || '';
      return `<article class="roleplay-story-card"><strong>${escapeHtml(ctx, item.title || item.summary || id || 'Checkpoint')}</strong><p>${escapeHtml(ctx, item.summary || item.snapshot_summary || item.storage_path || '')}</p><small>${escapeHtml(ctx, id)} · ${escapeHtml(ctx, item.created_at || item.updated_at || '')}</small><div class="roleplay-story-card-actions"><button type="button" class="neo-btn admin-engine-btn small" onclick="restoreRoleplayStoryToSceneFromUi('checkpoint','${escapeHtml(ctx, id)}')">Restore</button><button type="button" class="neo-btn admin-engine-btn small" onclick="roleplaySelectCheckpointForDiff('${escapeHtml(ctx, id)}')">Diff</button><button type="button" class="neo-btn admin-engine-btn small" onclick="roleplayBranchCheckpointFromUi('${escapeHtml(ctx, id)}')">Branch</button></div></article>`;
    }).join('') || '<div class="roleplay-stories-empty">No checkpoints loaded yet. Save a checkpoint from Scene first.</div>';
    return `<section class="neo-ui-card roleplay-checkpoint-inspector">
      <div class="neo-ui-section-head"><div><strong>Roleplay Checkpoint Inspector</strong><p class="neo-muted">Checkpoint restore, branch, diff, and checkpoint state review.</p></div>${badgeRow(ctx,[`${checkpoints.length} checkpoints`, `${branches.length} branches`, restore.status || 'restore state'])}</div>
      <div class="neo-inline-actions"><button class="neo-btn secondary" type="button" onclick="roleplaySceneStateInspectorRefresh()">Refresh state</button><button class="neo-btn secondary" type="button" onclick="roleplayStoriesResumePlaceholder()">Resume selected</button><button class="neo-btn secondary" type="button" onclick="roleplayCompareCheckpointsFromUi()">Compare checkpoints</button></div>
      <div class="neo-ui-grid two">
        <div>${metaList(ctx,[`Restore snapshots: ${restoreCounts.checkpoint_snapshots || 0}`, `Restore events: ${restoreCounts.restore_events || 0}`, `Branches: ${restoreCounts.branches || branches.length || 0}`, `Selected diff left: ${selectedLeft || 'none'}`])}</div>
        <div><label>Diff left</label><input id="roleplay-diff-left" value="${escapeHtml(ctx, selectedLeft)}" placeholder="checkpoint id"><label>Diff right</label><input id="roleplay-diff-right" value="${escapeHtml(ctx, getValue('roleplay-diff-right', ''))}" placeholder="checkpoint id"><label>Branch title</label><input id="roleplay-branch-title" placeholder="Branch title"><label>Branch type</label><input id="roleplay-branch-type" value="alternate"></div>
      </div>
      <details class="neo-ui-details" open><summary>Recent checkpoints</summary><div class="roleplay-story-list compact">${checkpointCards}</div></details>
      <details class="neo-ui-details" open><summary>Checkpoint diff</summary>${checkpointDiffHtml(ctx)}</details>
      <details class="neo-ui-details"><summary>Restore state payload</summary><pre class="neo-code-block small">${escapeHtml(ctx, JSON.stringify(restore, null, 2)).slice(0, 5000)}</pre></details>
    </section>`;
  }

  function normalizeSceneDirectorPayload(ctx) {
    const s = stateOf(ctx);
    const scene = s.roleplayScene || {};
    const setup = scene.setup || {};
    return {
      scene_id: scene.scene_id || setup.scene_id || 'default',
      title: getValue('roleplay-scene-title', setup.title || scene.title || 'Untitled Scene'),
      user_turn: getValue('roleplay-scene-input', '') || getValue('roleplay-scene-user-input', '') || '',
      runtime_bundle_id: getValue('roleplay-scene-runtime', setup.runtime_bundle_id || ''),
      scene_packet_id: setup.scene_packet_id || s.roleplayRuntime?.last_scene_packet_id || s.roleplayScenePacket?.packet_id || '',
      player_character_id: getValue('roleplay-scene-player-character', setup.player_character_id || ''),
      player_character_name: getValue('roleplay-scene-player-character-name', setup.player_character_name || ''),
      intent: getValue('roleplay-scene-director-intent', 'roleplay.scene_turn'),
      mode: getValue('roleplay-scene-director-mode', 'scene_turn'),
      include_trace: true,
    };
  }

  function sceneDirectorTraceRows(ctx, traces = []) {
    if (!traces.length) return '<p class="neo-muted">No Scene Director traces yet. Run preflight or send a Scene Chat turn to create one.</p>';
    return traces.slice(0, 8).map((trace) => {
      const label = trace.trace_id || trace.scene_id || 'trace';
      const sub = [trace.scene_id || 'default scene', trace.intent || 'intent?', trace.status || 'status?'].filter(Boolean).join(' · ');
      return `<button type="button" class="neo-ui-record-card" data-roleplay-scene-director-trace="${escapeHtml(ctx, label)}"><strong>${escapeHtml(ctx, label)}</strong><span>${escapeHtml(ctx, sub)}</span><small>${escapeHtml(ctx, trace.created_at || trace.storage_path || '')}</small></button>`;
    }).join('');
  }

  function sceneDirectorCockpitHtml(ctx) {
    const s = stateOf(ctx);
    const status = s.roleplaySceneDirectorStatus || {};
    const preflight = s.roleplaySceneDirectorPreflight || {};
    const validation = s.roleplaySceneDirectorValidation || {};
    const tracePayload = s.roleplaySceneDirectorTraces || {};
    const traces = Array.isArray(tracePayload.traces) ? tracePayload.traces : (Array.isArray(status.recent_traces) ? status.recent_traces : []);
    const policy = status.policy || {};
    const tableCounts = status.roleplay_table_counts || {};
    const preflightPrompt = preflight.director_prompt || preflight.prompt || preflight.brief || '';
    const preflightMeta = preflight.metadata || preflight.director_metadata || preflight.scene_director || {};
    const validationWarnings = Array.isArray(validation.warnings) ? validation.warnings : [];
    return `<section class="neo-ui-card roleplay-scene-director-cockpit">
      <div class="neo-ui-section-head"><div><strong>Roleplay Scene Director Cockpit</strong><p class="neo-muted">Control Center and Scene Director preflight, validation, traces, and runtime readiness.</p></div>${badgeRow(ctx,[status.status || 'unknown', status.control_center_status || 'control?', policy.send_full_universe_prompt === false ? 'compact brief' : 'prompt risk'])}</div>
      <div class="neo-ui-field-grid">
        <label>Intent<select id="roleplay-scene-director-intent"><option value="roleplay.scene_turn">Scene turn</option><option value="roleplay.canon_summary">Canon summary</option><option value="roleplay.scene_continue">Continue</option><option value="roleplay.character_dialogue">Character dialogue</option></select></label>
        <label>Mode<select id="roleplay-scene-director-mode"><option value="scene_turn">Scene turn</option><option value="summary">Summary</option><option value="diagnostic">Diagnostic</option></select></label>
      </div>
      <div class="neo-inline-actions"><button class="neo-btn secondary" type="button" onclick="roleplaySceneDirectorStatus()">Refresh status</button><button class="neo-btn secondary" type="button" onclick="roleplaySceneDirectorPreflight()">Run preflight</button><button class="neo-btn secondary" type="button" onclick="roleplaySceneDirectorValidate()">Validate last response</button><button class="neo-btn secondary" type="button" onclick="roleplaySceneDirectorTraces()">Refresh traces</button></div>
      <div class="neo-ui-grid two">
        <div>${metaList(ctx,[`Roleplay DB: ${status.roleplay_db_ready ? 'ready' : 'missing'}`, `Packets: ${tableCounts.rp_scene_memory_packets ?? 0}`, `Fragments: ${tableCounts.rp_memory_fragments ?? 0}`, `Character states: ${tableCounts.rp_character_states ?? 0}`, `Relationship states: ${tableCounts.rp_relationship_state ?? 0}`, `Unresolved threads: ${tableCounts.rp_unresolved_threads ?? 0}`])}</div>
        <div>${metaList(ctx,[`LLM role: ${policy.llm_role || 'performer/pilot'}`, `Director role: ${policy.scene_director_role || 'runtime copilot'}`, `Memory role: ${policy.memory_role || 'continuity library'}`, `Player boundary: ${policy.player_control_boundary_required ? 'required' : 'unknown'}`, `Dialogue lanes: ${policy.dialogue_lanes_required_for_scene_turns ? 'required' : 'unknown'}`])}</div>
      </div>
      <details class="neo-ui-details" open><summary>Latest preflight director brief</summary>${preflightPrompt ? `<pre class="neo-code-block">${escapeHtml(ctx, preflightPrompt).slice(0, 6000)}</pre>` : '<p class="neo-muted">No preflight brief yet. Click Run preflight after loading a scene packet.</p>'}${Object.keys(preflightMeta).length ? `<pre class="neo-code-block small">${escapeHtml(ctx, JSON.stringify(preflightMeta, null, 2)).slice(0, 3000)}</pre>` : ''}</details>
      <details class="neo-ui-details"><summary>Validation warnings</summary>${validationWarnings.length ? metaList(ctx, validationWarnings.map((w)=>`${w.level || 'warn'} · ${w.rule || w.kind || 'check'} · ${w.message || w}`)) : '<p class="neo-muted">No validation warnings recorded.</p>'}</details>
      <details class="neo-ui-details"><summary>Recent Scene Director traces</summary>${sceneDirectorTraceRows(ctx, traces)}</details>
    </section>`;
  }


  function storiesActiveView(ctx) {
    const s = stateOf(ctx); const allowed = ['workspace', 'storylines', 'archive', 'inspector'];
    const value = s.roleplayStoriesActiveView || s.roleplayStories?.active_view || 'workspace';
    return allowed.includes(value) ? value : 'workspace';
  }
  function storiesArchiveView(ctx) {
    const s = stateOf(ctx); const allowed = ['stories', 'roleplay', 'canon'];
    const value = s.roleplayStoriesArchiveView || s.roleplayStories?.archive?.active_child_view || 'stories';
    return allowed.includes(value) ? value : 'stories';
  }
  function storiesInspectorView(ctx) {
    const s = stateOf(ctx); const allowed = ['summary', 'continuity', 'provenance'];
    const value = s.roleplayStoriesInspectorView || 'summary';
    return allowed.includes(value) ? value : 'summary';
  }
  function storiesTabButton(ctx, view, label) {
    const active = storiesActiveView(ctx) === view ? ' active' : '';
    return `<button type="button" class="roleplay-v1-tab${active}" onclick="roleplayStoriesSetView('${escapeAttr(ctx, view)}')">${escapeHtml(ctx, label)}</button>`;
  }
  function storiesArchiveButton(ctx, view, label) {
    const active = storiesArchiveView(ctx) === view ? ' active' : '';
    return `<button type="button" class="roleplay-v1-tab${active}" onclick="roleplayStoriesSetArchiveView('${escapeAttr(ctx, view)}')">${escapeHtml(ctx, label)}</button>`;
  }
  function storiesInspectorButton(ctx, view, label) {
    const active = storiesInspectorView(ctx) === view ? ' active' : '';
    return `<button type="button" class="roleplay-v1-tab${active}" onclick="roleplayStoriesSetInspectorView('${escapeAttr(ctx, view)}')">${escapeHtml(ctx, label)}</button>`;
  }
  function storiesStatusBadge(ctx, status) {
    const clean = String(status || 'draft').toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
    return `<span class="roleplay-story-status status-${escapeHtml(ctx, clean)}">${escapeHtml(ctx, status || 'Draft')}</span>`;
  }
  function storiesCard(ctx, record, type = 'storyline') {
    const id = record.storyline_id || record.session_id || record.checkpoint_id || record.record_id || 'unsaved';
    const title = record.title || record.summary || record.premise || id;
    const meta = type === 'session'
      ? `${record.mode_lock || 'mode n/a'} · ${record.interaction_mode || 'roleplay'} · checkpoints ${record.checkpoint_count || 0}`
      : `${record.status || 'draft'} · ${id}`;
    const body = record.summary || record.premise || record.arc || record.storage_path || 'No summary yet.';
    const safeId = escapeAttr(ctx, id);
    const actions = type === 'checkpoint'
      ? `<div class="roleplay-story-card-actions"><button type="button" class="neo-btn admin-engine-btn small" onclick="restoreRoleplayStoryToSceneFromUi('checkpoint','${safeId}')">Restore to Scene</button><button type="button" class="neo-btn admin-engine-btn small" onclick="roleplayBranchCheckpointFromUi('${safeId}')">Branch</button><button type="button" class="neo-btn admin-engine-btn small" onclick="roleplaySelectCheckpointForDiff('${safeId}')">Diff</button></div>`
      : type === 'session'
        ? `<div class="roleplay-story-card-actions"><button type="button" class="neo-btn admin-engine-btn small" onclick="restoreRoleplayStoryToSceneFromUi('session','${safeId}')">Resume session</button></div>`
        : type === 'branch'
          ? `<div class="roleplay-story-card-actions"><button type="button" class="neo-btn admin-engine-btn small" onclick="restoreRoleplayStoryToSceneFromUi('session','${escapeAttr(ctx, record.session_id || '')}')">Resume branch</button><button type="button" class="neo-btn admin-engine-btn small" onclick="roleplaySelectCheckpointForDiff('${escapeAttr(ctx, record.source_checkpoint_id || '')}')">Diff source</button></div>`
          : '';
    return `<div class="roleplay-story-card status-${escapeHtml(ctx, String(record.status || 'draft').toLowerCase().replace(/[^a-z0-9_-]+/g, '_'))}">
      <strong>${escapeHtml(ctx, title)}</strong>
      <div class="roleplay-story-card-meta">${storiesStatusBadge(ctx, record.status || 'draft')} <span>${escapeHtml(ctx, type)}</span></div>
      <p>${escapeHtml(ctx, meta)}</p>
      <small>${escapeHtml(ctx, body)}</small>
      ${actions}
    </div>`;
  }
  function storiesTopbarHtml(ctx, stories) {
    const active = stories?.active_storyline_id || stories?.workspace?.active_storyline_id || '';
    return `<div class="roleplay-v1-stories-hero">
      <div><div class="roleplay-v1-eyebrow">Stories</div><p>Storyline workspace, session/checkpoint history, archive reading, and resume paths back into Scene.</p></div>
      <span class="badge">Save + resume</span>
    </div>
    <div class="roleplay-v1-stories-actions">
      <button type="button" class="neo-btn primary admin-engine-btn" onclick="roleplayStoriesSetView('workspace')">Create storyline</button>
      <button type="button" class="neo-btn admin-engine-btn" onclick="refreshRoleplayStoriesFromUi()">Refresh</button>
      <button type="button" class="neo-btn admin-engine-btn" onclick="roleplayStoriesFillFromScene()">Create from current Scene</button>
      <span class="badge">${active ? `Active · ${escapeHtml(ctx, active)}` : 'No storyline selected'}</span>
    </div>
    <div class="roleplay-v1-tabbar">${storiesTabButton(ctx,'workspace','Workspace')}${storiesTabButton(ctx,'storylines','Storylines')}${storiesTabButton(ctx,'archive','Archive')}${storiesTabButton(ctx,'inspector','Inspector')}</div>`;
  }
  function storiesWorkspaceHtml(ctx, stories) {
    const s = stateOf(ctx); const workspace = stories?.workspace || {};
    const storylines = stories?.storylines || []; const sessions = workspace.sessions || stories?.sessions || [];
    const checkpoints = workspace.checkpoints || stories?.checkpoints || []; const branches = workspace.branches || stories?.branches || [];
    const restore = workspace.restore_target || {}; const activeStory = storylines[0] || {}; const restoreState = s.roleplayStoryRestore || {}; const restoreCounts = restoreState.counts || {};
    const sessionList = sessions.length ? sessions.map((item)=>storiesCard(ctx,item,'session')).join('') : '<div class="roleplay-stories-empty">No sessions yet. Create one after saving a storyline.</div>';
    const checkpointList = checkpoints.length ? checkpoints.map((item)=>storiesCard(ctx,item,'checkpoint')).join('') : '<div class="roleplay-stories-empty">No checkpoints yet. Save one from Scene or create a Story checkpoint record.</div>';
    const branchList = branches.length ? branches.map((item)=>storiesCard(ctx,item,'branch')).join('') : '<div class="roleplay-stories-empty">No checkpoint branches yet. Use Branch on a checkpoint to create an alternate run.</div>';
    return `<div class="roleplay-stories-workspace-grid">
      <div class="roleplay-stories-left-lane">
        <section class="roleplay-stories-card"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">Save model</div><p>Storyline → Session → Checkpoint hierarchy. This module keeps the save/resume lane readable while backend persistence remains server-owned.</p></div><span class="badge">Stories</span></div>
          <div class="roleplay-stories-note">Storyline = umbrella. Session = one active run. Checkpoint = a restorable save. Restore target feeds Scene setup, packet binding, and checkpoint preview.</div>
          <div class="roleplay-stories-note"><strong>Checkpoint restore layer</strong><br>Snapshots · ${escapeHtml(ctx, String(restoreCounts.checkpoint_snapshots || 0))} · Restore events · ${escapeHtml(ctx, String(restoreCounts.restore_events || 0))} · Branches · ${escapeHtml(ctx, String(restoreCounts.branches || branches.length || 0))}</div>
        </section>
        <section class="roleplay-stories-card"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">New storyline</div><p>Create a storyline shell from scratch or fill from the current Scene.</p></div><span class="badge">Create</span></div>
          <label>Title</label><input id="roleplay-storyline-title" placeholder="Storyline title" maxlength="200">
          <label>Summary</label><textarea id="roleplay-storyline-premise" rows="4" placeholder="What this storyline covers, what is being continued, or what this branch is about."></textarea>
          <div class="roleplay-v1-grid two"><div><label>Project id</label><input id="roleplay-storyline-project-id" placeholder="Optional project id" value="${escapeAttr(ctx, activeStory.project_id || '')}"></div><div><label>Continuity policy</label><select id="roleplay-storyline-continuity-policy"><option value="runtime_anchored">Runtime anchored</option><option value="canon_strict">Canon strict</option><option value="branch_experimental">Branch experimental</option></select></div></div>
          <label>Arc notes</label><textarea id="roleplay-storyline-arc" rows="3" placeholder="Optional arc notes"></textarea>
          <label>Planned beats</label><textarea id="roleplay-storyline-beats" rows="3" placeholder="Optional planned beats"></textarea>
          <div class="roleplay-v1-actions"><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayStoriesFillFromScene()">Fill from current Scene</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayStoriesClearStorylineForm()">Clear</button><button type="button" class="neo-btn primary admin-engine-btn" onclick="createRoleplayStorylineFromUi()">Save storyline</button></div>
        </section>
      </div>
      <div class="roleplay-stories-right-lane">
        <section class="roleplay-stories-card"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">New session</div><p>Start or branch a run under the active storyline.</p></div><span class="badge">Session</span></div>
          <label>Session summary</label><textarea id="roleplay-story-session-summary" rows="3" placeholder="What this session/run should continue or test."></textarea>
          <label><input id="roleplay-story-session-seed" type="checkbox" checked> Seed from selected checkpoint when available</label>
          <div class="roleplay-v1-actions"><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayStoriesClearSessionForm()">Clear</button><button type="button" class="neo-btn primary admin-engine-btn" onclick="createRoleplayStorySessionFromUi()">Save session</button></div>
        </section>
        <section class="roleplay-stories-card tall"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">Workspace overview</div><p>Current story records available for resume, checkpoint restore, and branch creation.</p></div><span class="badge">${storylines.length} stories</span></div>
          <div class="roleplay-v1-grid two"><div><h4>Sessions</h4>${sessionList}</div><div><h4>Checkpoints</h4>${checkpointList}</div></div><h4>Branches</h4>${branchList}
          <div class="roleplay-stories-note"><strong>Restore target</strong><br>Storyline · ${escapeHtml(ctx, restore.storyline_id || 'none')}<br>Session · ${escapeHtml(ctx, restore.session_id || 'none')}<br>Checkpoint · ${escapeHtml(ctx, restore.checkpoint_id || 'none')}</div>
        </section>
      </div>
    </div>`;
  }
  function storiesStorylinesHtml(ctx, stories) {
    const records = stories?.storylines || [];
    return `<div class="roleplay-stories-card tall"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">Storylines</div><p>Storyline browser rail. Search, filter, and pick the branch you want to inspect or resume.</p></div><span class="badge">${records.length}</span></div>${records.length ? `<div class="roleplay-story-list">${records.map((r)=>storiesCard(ctx,r,'storyline')).join('')}</div>` : '<div class="roleplay-stories-empty">No storylines yet.</div>'}</div>`;
  }
  function storiesArchiveHtml(ctx, stories) {
    const view = storiesArchiveView(ctx); const archive = stories?.archive || {};
    const rows = view === 'canon' ? (archive.canon || []) : view === 'roleplay' ? (archive.roleplay || []) : (archive.stories || stories?.storylines || []);
    return `<div class="roleplay-stories-card tall"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">Archive</div><p>Archive lanes keep Stories, Roleplay runs, and Canon records separate to avoid cross-scope memory soup.</p></div><span class="badge">${rows.length}</span></div><div class="roleplay-v1-tabbar inner">${storiesArchiveButton(ctx,'stories','Stories')}${storiesArchiveButton(ctx,'roleplay','Roleplay')}${storiesArchiveButton(ctx,'canon','Canon')}</div>${rows.length ? `<div class="roleplay-story-list">${rows.map((r)=>storiesCard(ctx,r,view)).join('')}</div>` : '<div class="roleplay-stories-empty">No archive records in this lane yet.</div>'}</div>`;
  }

  function provenanceViewBox(ctx) {
    const view = stateOf(ctx).roleplayProvenanceView || {};
    return { x: Number.isFinite(view.x) ? view.x : -760, y: Number.isFinite(view.y) ? view.y : -520, w: Number.isFinite(view.w) ? view.w : 1520, h: Number.isFinite(view.h) ? view.h : 1040 };
  }
  function provenanceNodeColor(type) {
    const colors = { entity: '#38bdf8', memory_fragment: '#a855f7', shared_memory: '#22c55e', continuity_row: '#f59e0b', turn_summary: '#0ea5e9', story_checkpoint: '#ec4899', retrieval_trace: '#6366f1', runtime_bundle: '#14b8a6', contradiction_report: '#f87171', scene_packet: '#06b6d4', scene_turn: '#60a5fa' };
    return colors[type] || '#94a3b8';
  }
  function provenanceGraphLayout(nodes, edges) {
    const safeNodes = nodes.slice(0, 140);
    const degree = new Map(safeNodes.map((node) => [node.id, 0]));
    edges.forEach((edge) => { if (degree.has(edge.source)) degree.set(edge.source, degree.get(edge.source) + 1); if (degree.has(edge.target)) degree.set(edge.target, degree.get(edge.target) + 1); });
    const centerNodeId = safeNodes.slice().sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))[0]?.id || null;
    const types = [...new Set(safeNodes.map((node) => node.type || 'unknown'))];
    const typeIndex = new Map(types.map((type, idx) => [type, idx]));
    const laidOut = safeNodes.map((node, idx) => {
      if (node.id === centerNodeId) return { ...node, x: 0, y: 0, r: 24 + Math.min(16, (degree.get(node.id) || 0) * 3) };
      const ring = 1 + (typeIndex.get(node.type || 'unknown') % 4);
      const ringCount = Math.max(1, safeNodes.filter((candidate) => candidate.id !== centerNodeId && ((typeIndex.get(candidate.type || 'unknown') % 4) + 1) === ring).length);
      const ringPosition = safeNodes.slice(0, idx + 1).filter((candidate) => candidate.id !== centerNodeId && ((typeIndex.get(candidate.type || 'unknown') % 4) + 1) === ring).length - 1;
      const angle = (Math.PI * 2 * ringPosition / ringCount) + (ring * 0.37);
      const radius = 170 + ring * 135 + Math.min(110, (degree.get(node.id) || 0) * 12);
      return { ...node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, r: 15 + Math.min(12, (degree.get(node.id) || 0) * 2) };
    });
    return { nodes: laidOut, nodeById: new Map(laidOut.map((node) => [node.id, node])) };
  }
  function provenanceVisualGraphHtml(ctx, nodes, edges, trace) {
    const safeNodes = nodes.slice(0, 140);
    if (!safeNodes.length) return '<div class="roleplay-provenance-canvas-empty">No graph nodes available yet. Create Forge records, Scene turns, checkpoints, retrieval traces, or runtime bundles first.</div>';
    const { nodes: laidOut, nodeById } = provenanceGraphLayout(safeNodes, edges);
    const activeId = trace?.node_id || trace?.direct_node?.id || '';
    const relatedIds = new Set([activeId, ...(trace?.related_nodes || []).map((node) => node.id).filter(Boolean)]);
    const renderedEdges = edges.slice(0, 260).map((edge) => {
      const a = nodeById.get(edge.source); const b = nodeById.get(edge.target); if (!a || !b) return '';
      const active = edge.source === activeId || edge.target === activeId || (relatedIds.has(edge.source) && relatedIds.has(edge.target));
      const stroke = active ? '#fbbf24' : 'rgba(148, 163, 184, 0.34)'; const width = active ? 2.6 : 1.25; const midX = (a.x + b.x) / 2; const midY = (a.y + b.y) / 2;
      return `<g class="roleplay-svg-edge ${active ? 'active' : ''}"><line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" stroke="${stroke}" stroke-width="${width}" marker-end="url(#roleplayGraphArrow)"/><text x="${midX.toFixed(1)}" y="${midY.toFixed(1)}">${escapeHtml(ctx, edge.type || '')}</text></g>`;
    }).join('');
    const renderedNodes = laidOut.map((node) => {
      const active = node.id === activeId; const related = relatedIds.has(node.id) && !active; const fill = provenanceNodeColor(node.type || 'unknown'); const label = String(node.label || node.source_id || node.id || '').slice(0, 26);
      return `<g class="roleplay-svg-node ${active ? 'active' : ''} ${related ? 'related' : ''}" transform="translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})" onclick="roleplayTraceProvenanceNode(${escapeAttr(ctx, JSON.stringify(node.id || ''))})" role="button" tabindex="0"><circle r="${node.r.toFixed(1)}" fill="${fill}"/><text y="${(node.r + 16).toFixed(1)}">${escapeHtml(ctx, label)}</text><title>${escapeHtml(ctx, `${node.type || 'node'} · ${node.source_id || node.id || ''}`)}</title></g>`;
    }).join('');
    const vb = provenanceViewBox(ctx);
    return `<div class="roleplay-provenance-canvas-shell"><div class="roleplay-provenance-canvas-head"><strong>Visual graph canvas</strong><span>${safeNodes.length} nodes shown · ${edges.length} edges available</span></div><svg class="roleplay-provenance-svg" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}" xmlns="http://www.w3.org/2000/svg" aria-label="Roleplay provenance visual graph"><defs><marker id="roleplayGraphArrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L7,3 z" fill="rgba(148, 163, 184, 0.72)"/></marker></defs><rect x="${vb.x}" y="${vb.y}" width="${vb.w}" height="${vb.h}" rx="18" fill="rgba(2,6,23,.18)"/>${renderedEdges}${renderedNodes}</svg></div>`;
  }
  function roleplayProvenanceGraphHtml(ctx) {
    const st = stateOf(ctx); const graph = st.roleplayProvenanceGraph || {}; const trace = st.roleplayProvenanceTrace || {}; const counts = graph.counts || {}; const nodes = Array.isArray(graph.nodes) ? graph.nodes : []; const edges = Array.isArray(graph.edges) ? graph.edges : []; const byType = counts.by_type || {};
    const typeOptions = ['', ...(graph.filters?.node_types || [])].map((type) => `<option value="${escapeAttr(ctx, type)}">${escapeHtml(ctx, type || 'all types')}</option>`).join('');
    const typeBadges = Object.keys(byType).map((key) => `<span class="roleplay-chip">${escapeHtml(ctx, key)} · ${byType[key]}</span>`).join('');
    const nodeCards = nodes.slice(0, 80).map((node) => `<div class="roleplay-provenance-node ${trace?.node_id === node.id ? 'active' : ''} roleplay-node-${escapeHtml(ctx, node.type || 'unknown')}"><div><strong>${escapeHtml(ctx, node.label || node.source_id || node.id)}</strong><p>${escapeHtml(ctx, node.type || 'node')} · ${escapeHtml(ctx, node.source_id || '')}</p></div><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayTraceProvenanceNode(${escapeAttr(ctx, JSON.stringify(node.id || ''))})">Trace</button></div>`).join('') || '<div class="roleplay-v1-studio-empty">No provenance nodes yet. Create Forge records, Scene turns, checkpoints, retrieval traces, or runtime bundles first.</div>';
    const edgeItems = edges.slice(0, 80).map((edge) => `<li><strong>${escapeHtml(ctx, edge.type || 'edge')}</strong> · ${escapeHtml(ctx, edge.source || '')} → ${escapeHtml(ctx, edge.target || '')}</li>`).join('') || '<li>No connected edges yet.</li>';
    const traceHtml = trace?.direct_node ? `<div class="roleplay-provenance-trace"><div class="roleplay-v1-row-between"><div><strong>Selected trace</strong><p>${escapeHtml(ctx, trace.node_id || '')}</p></div><span class="badge">${escapeHtml(ctx, trace.status || 'trace')}</span></div>${metaList(ctx, [`Incoming: ${trace.counts?.incoming ?? 0}`, `Outgoing: ${trace.counts?.outgoing ?? 0}`, `Related nodes: ${trace.counts?.related_nodes ?? 0}`])}<pre class="roleplay-stories-pre small">${escapeHtml(ctx, JSON.stringify({ direct_node: trace.direct_node, incoming: trace.incoming, outgoing: trace.outgoing, related_nodes: trace.related_nodes?.slice?.(0, 12) || [] }, null, 2))}</pre></div>` : '<div class="roleplay-v1-studio-empty">Click a graph node or select Trace on any node to inspect incoming/outgoing provenance.</div>';
    return `<div class="roleplay-provenance-shell"><div class="roleplay-v1-row-between"><div><strong>Provenance graph</strong><p>Knowledge trace showing where Roleplay context came from: Forge records, memory fragments, retrieval traces, runtime bundles, scene packets, checkpoints, and contradiction reports.</p></div><span class="badge">visual graph active</span></div><div class="roleplay-v1-studio-grid three compact"><div><label>Scope filter</label><input id="roleplay-provenance-scope" placeholder="scope / scene / storyline / bundle id"></div><div><label>Node type</label><select id="roleplay-provenance-node-type">${typeOptions}</select></div><div><label>Limit</label><input id="roleplay-provenance-limit" type="number" value="250"></div></div><div class="roleplay-v1-studio-row"><button type="button" class="neo-btn primary admin-engine-btn" onclick="roleplayRefreshProvenanceGraph()">Refresh graph</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayReloadProvenanceState()">Reload state</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenanceZoom(0.82)">Zoom in</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenanceZoom(1.22)">Zoom out</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenancePan(-120,0)">←</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenancePan(120,0)">→</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenancePan(0,-90)">↑</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenancePan(0,90)">↓</button><button type="button" class="neo-btn admin-engine-btn" onclick="roleplayProvenanceResetView()">Reset view</button></div><div class="roleplay-memory-grid"><div class="roleplay-memory-card"><strong>Graph summary</strong>${metaList(ctx,[`Nodes: ${counts.nodes ?? 0}`, `Edges: ${counts.edges ?? 0}`, `Status: ${graph.status || 'active'}`])}</div><div class="roleplay-memory-card"><strong>Node types</strong><div class="roleplay-chip-row">${typeBadges || '<span class="roleplay-chip">empty</span>'}</div></div><div class="roleplay-memory-card"><strong>Interaction</strong>${metaList(ctx,['SVG canvas-style renderer','Click node to trace provenance','Zoom and pan controls','Filters reuse graph endpoint'])}</div></div>${provenanceVisualGraphHtml(ctx, nodes, edges, trace)}<div class="roleplay-provenance-grid"><div class="roleplay-provenance-panel"><strong>Graph nodes</strong>${nodeCards}</div><div class="roleplay-provenance-panel"><strong>Connected edges</strong><ul>${edgeItems}</ul>${traceHtml}</div></div></div>`;
  }

  function storiesInspectorHtml(ctx, stories) {
    const inspector = stories?.inspector || {}; const view = storiesInspectorView(ctx);
    const summary = inspector.summary || {}; const continuity = inspector.continuity || {}; const provenance = inspector.provenance || {};
    const rows = view === 'provenance'
      ? [`Traces: ${provenance.trace_count ?? 0}`, `Checkpoint memory rows: ${provenance.checkpoint_memory_count ?? 0}`, `Trace enabled: ${provenance.trace_enabled ? 'yes' : 'no'}`, provenance.message || 'Provenance graph is active below.']
      : view === 'continuity'
        ? [`Rows: ${continuity.row_count ?? 0}`, 'Checks enabled: yes', continuity.message || 'Continuity rows are connected to the contradiction resolver.']
        : [`Storylines: ${summary.storyline_count ?? 0}`, `Sessions: ${summary.session_count ?? 0}`, `Checkpoints: ${summary.checkpoint_count ?? 0}`, `Memory fragments: ${summary.memory_rows?.fragments ?? 0}`, `Active storyline: ${summary.active_storyline_id || 'none'}`, summary.message || 'Summary placeholder only.'];
    return `<div class="roleplay-stories-card tall"><div class="roleplay-v1-row-between"><div><div class="roleplay-v1-eyebrow">Inspector</div><p>Summary, continuity, and provenance review for the active storyline/session/checkpoint.</p></div><span class="badge">Inspector</span></div><div class="roleplay-v1-tabbar inner">${storiesInspectorButton(ctx,'summary','Summary')}${storiesInspectorButton(ctx,'continuity','Continuity')}${storiesInspectorButton(ctx,'provenance','Provenance')}</div>${metaList(ctx, rows)}${view === 'provenance' ? roleplayProvenanceGraphHtml(ctx) : ''}</div>`;
  }
  function storiesWorkspaceShellHtml(ctx) {
    const stories = stateOf(ctx).roleplayStories || {};
    const active = storiesActiveView(ctx);
    const body = active === 'storylines' ? storiesStorylinesHtml(ctx, stories) : active === 'archive' ? storiesArchiveHtml(ctx, stories) : active === 'inspector' ? storiesInspectorHtml(ctx, stories) : storiesWorkspaceHtml(ctx, stories);
    return `${storiesTopbarHtml(ctx, stories)}<div class="roleplay-v1-stories-body">${body}</div>`;
  }
  // These duplicate no backend logic; they move deep Studio UI lanes and handlers into roleplay.js while keeping neo.js fallback wrappers.
  function progressHtml(ctx, progress, options = {}) {
    if (!progress) return '';
    const status = progress.status || 'idle';
    const running = status === 'running';
    const failed = status === 'failed' || status === 'error';
    const done = ['built','complete','completed','done','ready'].includes(status);
    const badgeClass = failed ? 'danger' : done ? 'success' : 'warn';
    const title = options.title || 'Progress';
    const label = progress.label || (running ? 'Working…' : failed ? 'Failed' : done ? 'Done' : 'Ready');
    const detail = progress.detail || '';
    const step = progress.step ? `Step: ${progress.step}` : '';
    return `<div class="roleplay-action-progress ${running ? 'is-running' : ''} ${failed ? 'is-failed' : ''} ${done ? 'is-done' : ''}" role="status" aria-live="polite"><div class="roleplay-action-progress-head"><div><strong>${escapeHtml(ctx,title)}</strong><span>${escapeHtml(ctx,label)}</span></div><span class="neo-badge ${badgeClass}">${escapeHtml(ctx,status)}</span></div>${running ? '<div class="roleplay-action-progress-bar"><span></span></div>' : ''}${detail ? `<p>${escapeHtml(ctx,detail)}</p>` : ''}${step ? `<small>${escapeHtml(ctx,step)}</small>` : ''}</div>`;
  }
  function busyLabel(ctx, label, busyLabelText, isBusy) { return isBusy ? `<span class="roleplay-btn-spinner" aria-hidden="true"></span>${escapeHtml(ctx,busyLabelText)}` : escapeHtml(ctx,label); }
  function listItems(ctx, items = []) { return `<ul>${(items || []).map((item)=>`<li>${escapeHtml(ctx,item)}</li>`).join('')}</ul>`; }

  function scopedCompilePayload(ctx) {
    const ids = getValue('roleplay-scoped-compile-record-ids', '');
    return {
      mode: getValue('roleplay-scoped-compile-mode', 'changed_only'),
      scope_type: getValue('roleplay-scoped-compile-scope-type', 'global'),
      scope_id: getValue('roleplay-scoped-compile-scope-id', ''),
      kind: getValue('roleplay-scoped-compile-kind', ''),
      record_ids: ids.split(',').map((item)=>item.trim()).filter(Boolean),
      include_compiled: isChecked('roleplay-scoped-compile-include-compiled', false),
    };
  }
  function scopedCompileHtml(ctx) {
    const s = stateOf(ctx); const scoped=s.roleplayScopedCompile || {}; const plan=s.roleplayScopedCompilePlan || {}; const last=s.roleplayScopedCompileLastRun || {}; const counts=scoped.status_counts || {};
    const records = Array.isArray(plan.records) ? plan.records : [];
    const modeOptions = [['changed_only','Changed / stale only'],['current_scope','Current scope'],['selected_records','Selected records'],['all','All matching records']].map(([v,l])=>`<option value="${escapeAttr(ctx,v)}">${escapeHtml(ctx,l)}</option>`).join('');
    const scopeOptions = ['global','project','sandbox','universe','world','region','city','location','scenario'].map((value)=>`<option value="${escapeAttr(ctx,value)}">${escapeHtml(ctx,value)}</option>`).join('');
    const rows = records.length ? `<div class="neo-stack compact">${records.slice(0,24).map((row)=>`<div class="roleplay-v1-studio-card inner"><div class="roleplay-v1-scene-row-between"><div><strong>${escapeHtml(ctx,row.title || row.record_id || 'Untitled')}</strong><span>${escapeHtml(ctx,row.record_kind || row.kind || '')} · ${escapeHtml(ctx,row.record_id || '')}</span></div><span class="neo-badge">${escapeHtml(ctx,row.action || 'skip')}</span></div>${listItems(ctx,[`Status: ${row.compile_status || 'new'}`,`Reason: ${row.reason || ''}`,`Scope: ${row.scope_id || row.sandbox_id || 'global'}`,`World: ${row.world_id || '—'}`])}</div>`).join('')}</div>` : '<div class="roleplay-v1-studio-empty">Preview a compile plan to see exactly which records will be compiled or skipped.</div>';
    return `<div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Scoped Builder memory compile</strong><p>Scoped compile planner. Preview before compiling to avoid cross-universe memory soup.</p></div><span class="neo-badge success">Compile</span></div><div class="roleplay-v1-studio-grid three compact"><div><label>Compile mode</label><select id="roleplay-scoped-compile-mode">${modeOptions}</select></div><div><label>Scope type</label><select id="roleplay-scoped-compile-scope-type">${scopeOptions}</select></div><div><label>Scope id</label><input id="roleplay-scoped-compile-scope-id" placeholder="world_id / sandbox_id / scenario_id"></div></div><div class="roleplay-v1-studio-grid three compact"><div><label>Kind filter</label><input id="roleplay-scoped-compile-kind" placeholder="optional: character / world / artifact"></div><div><label>Selected record IDs</label><input id="roleplay-scoped-compile-record-ids" placeholder="record_a, record_b"></div><div><label>Include already compiled</label><input id="roleplay-scoped-compile-include-compiled" type="checkbox"></div></div><div class="roleplay-v1-studio-grid three compact"><div><label>Rebuild search after compile</label><input id="roleplay-scoped-compile-rebuild-search" type="checkbox" checked></div><div><label>Index vectors after compile</label><input id="roleplay-scoped-compile-index" type="checkbox" checked></div><div><label>Mirror Chroma after compile</label><input id="roleplay-scoped-compile-mirror" type="checkbox"></div></div><div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn primary admin-engine-btn" onclick="previewRoleplayScopedCompilePlanFromUi()">Preview compile plan</button><button type="button" class="neo-btn primary admin-engine-btn" onclick="executeRoleplayScopedCompilePlanFromUi()">Compile selected plan</button><button type="button" class="neo-btn admin-engine-btn" onclick="reloadRoleplayScopedCompileState()">Refresh compile state</button></div>${badgeRow(ctx,[`new ${counts.new || 0}`,`changed ${counts.changed_since_compile || 0}`,`compiled ${counts.compiled || 0}`,`failed ${counts.compile_failed || 0}`,`active fragments ${scoped.active_fragment_count || 0}`,`superseded ${scoped.superseded_fragment_count || 0}`])}${plan.schema_id ? `<p class="roleplay-panel-note">Plan: ${escapeHtml(ctx,plan.status || '')} · compile ${plan.compile_count ?? 0} · skip ${plan.skipped_count ?? 0} · out of scope ${plan.out_of_scope_count ?? 0}${plan.warning ? ` · ${escapeHtml(ctx,plan.warning)}` : ''}</p>` : ''}${last.compile_run_id ? `<p class="roleplay-panel-note">Last run: ${escapeHtml(ctx,last.status || '')} · ${escapeHtml(ctx,last.compile_run_id || '')} · compiled ${last.compiled_count ?? 0} · fragments ${last.fragment_count ?? 0} · errors ${last.error_count ?? 0}</p>` : ''}${rows}</div>`;
  }

  function scopeBuildDeepHtml(ctx, studio = {}) {
    const s = stateOf(ctx); const scopeBuild=s.roleplayScopeBuild || {}; const scopeLevel=scopeBuildLevel(ctx);
    const levels=[['project','Project'],['universe','Universe'],['world','World'],['region','Region / Kingdom'],['city','City / Settlement'],['location','Location'],['scenario','Scenario']];
    const levelOptions=levels.map(([v,l])=>{const count=Number((scopeBuild.scope_counts||{})[v]||0); return `<option value="${escapeAttr(ctx,v)}" ${v===scopeLevel?'selected':''}>${escapeHtml(ctx,l + (count ? ` (${count})` : ''))}</option>`;}).join('');
    const scopes=scopesForLevel(ctx, scopeLevel); const selected=selectedScopeValue(ctx);
    const scopeOptions=scopes.length ? scopes.map((item)=>{const value=`${item.scope_type}::${item.scope_id}`; const status=item.compile_status ? ` · ${item.compile_status}` : ''; return `<option value="${escapeAttr(ctx,value)}" ${value===selected?'selected':''}>${escapeHtml(ctx,item.title || item.label || item.scope_id)}${escapeHtml(ctx,status)}</option>`;}).join('') : `<option value="">No ${escapeHtml(ctx,scopeLevel)} records found</option>`;
    const opts=s.roleplayScopeBuildOptions || {}; const progress=s.roleplayScopeBuildProgress || {}; const busy=progress.status==='running'; const preview=s.roleplayScopeBuildPreview || {}; const linked=preview.linked_records || preview.records || [];
    const linkedRows=linked.length ? `<div class="neo-stack compact">${linked.slice(0,32).map((row)=>`<div class="roleplay-v1-mini-row"><strong>${escapeHtml(ctx,row.title || row.record_id || row.id || 'record')}</strong><span>${escapeHtml(ctx,row.record_kind || row.kind || '')} · ${escapeHtml(ctx,row.record_id || row.id || '')} · ${escapeHtml(ctx,row.compile_status || row.status || 'pending')}</span></div>`).join('')}</div>` : '<div class="roleplay-v1-studio-empty">Preview Build Plan to inspect linked records before writing memory.</div>';
    const checked=(key, fallback)=> opts[key] ?? fallback ? 'checked' : '';
    return `<div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Build Scope Memory + Runtime</strong><p>Pick a readable scope, preview linked records, then build memory/runtime from one traceable plan.</p></div><span class="neo-badge success">Compile</span></div><div class="roleplay-v1-studio-grid three compact"><div><label>Scope level</label><select id="roleplay-scope-build-level" onchange="setRoleplayScopeBuildLevelFromUi()">${levelOptions}</select></div><div><label>Scope record</label><select id="roleplay-scope-build-select" onchange="setRoleplayScopeBuildSelectionFromUi()">${scopeOptions}</select></div><div><label>Graph depth</label><input id="roleplay-scope-build-depth" type="number" value="${escapeAttr(ctx,opts.graph_depth ?? 2)}" min="0" max="6"></div></div><div class="roleplay-v1-studio-grid three compact"><div><label>Compile mode</label><select id="roleplay-scope-build-mode"><option value="changed_only">Changed/stale only</option><option value="all">Force rebuild all linked records</option><option value="runtime_only">Runtime only</option></select></div><div><label>Kind filter</label><input id="roleplay-scope-build-kind" placeholder="optional" value="${escapeAttr(ctx,opts.kind || '')}"></div><div><label>Bundle title</label><input id="roleplay-scope-build-title" placeholder="Auto: Runtime — selected scope" value="${escapeAttr(ctx,opts.bundle_title || '')}"></div></div><div class="roleplay-v1-studio-row wrap"><label class="roleplay-checkbox"><input id="roleplay-scope-build-family" type="checkbox" ${checked('include_scope_family', true)}> Scope family</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-reverse" type="checkbox" ${checked('include_reverse_links', true)}> Reverse links</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-include-compiled" type="checkbox" ${checked('include_compiled', false)}> Include compiled</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-compile" type="checkbox" ${checked('compile_memory', true)}> Compile memory</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-runtime" type="checkbox" ${checked('build_runtime', true)}> Build runtime</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-rebuild" type="checkbox" ${checked('rebuild_search', true)}> Rebuild search</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-index" type="checkbox" ${checked('index_after', true)}> Index vectors</label><label class="roleplay-checkbox"><input id="roleplay-scope-build-mirror" type="checkbox" ${checked('mirror_after', false)}> Mirror Chroma</label></div><div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn primary admin-engine-btn" onclick="previewRoleplayScopeBuildFromUi()" ${busy?'disabled':''}>Preview Build Plan</button><button type="button" class="neo-btn primary admin-engine-btn ${busy?'is-busy':''}" onclick="buildRoleplayScopeMemoryRuntimeFromUi(false)" ${busy?'disabled':''}>${busyLabel(ctx,'Build Memory + Runtime','Building…',busy)}</button><button type="button" class="neo-btn admin-engine-btn" onclick="buildRoleplayScopeMemoryRuntimeFromUi(true)" ${busy?'disabled':''}>Rebuild Runtime Only</button><button type="button" class="neo-btn admin-engine-btn" onclick="reloadRoleplayScopeBuildState()" ${busy?'disabled':''}>Refresh scopes</button></div>${progressHtml(ctx, progress, {title:'Compile progress'})}${badgeRow(ctx,levels.map(([v,l])=>`${l} ${(scopeBuild.scope_counts||{})[v]||0}`))}<div class="roleplay-v1-studio-card inner"><strong>Linked record preview</strong>${linkedRows}</div></div>`;
  }

  function retrievalResultsHtml(ctx, search) {
    const results = search?.results || []; const candidates = search?.candidates_before_rerank || search?.merged_candidates || []; const diagnostics = search?.diagnostics || {};
    if (!search) return '<div class="roleplay-v1-studio-empty">Run runtime retrieval to preview candidate rows before Scene packet use.</div>';
    const diag = `<div class="roleplay-v1-studio-card inner"><strong>Runtime diagnostics</strong>${listItems(ctx,[`Trace: ${search.trace_id || 'none'}`,`Mode: ${search.mode || diagnostics.mode || 'retrieval'}`,`Keyword candidates: ${diagnostics.keyword_candidate_count ?? 'n/a'}`,`Semantic candidates: ${diagnostics.semantic_candidate_count ?? 'n/a'}`,`Merged candidates: ${diagnostics.merged_candidate_count ?? candidates.length}`,`Final results: ${diagnostics.final_result_count ?? results.length}`,`Reranker: ${(diagnostics.reranker_engine || {}).mode || (diagnostics.rerank ? 'enabled' : 'disabled')}`])}</div>`;
    if (!results.length) return `<div class="roleplay-v1-studio-empty">No rows found for “${escapeHtml(ctx, search.query || '')}”. Trace: ${escapeHtml(ctx, search.trace_id || 'none')}</div>${diag}`;
    const candidateHtml = candidates.length ? `<details class="roleplay-v1-studio-card inner"><summary>Pre-rerank candidates · ${candidates.length}</summary><div class="neo-stack">${candidates.slice(0,20).map((item)=>`<div class="roleplay-v1-mini-row"><strong>${escapeHtml(ctx,item.retrieval_label || item.title || item.result_id || 'candidate')}</strong><span>${escapeHtml(ctx,item.scene_category || item.table || '')} · ${escapeHtml(ctx,String(item.score ?? item.combined_score ?? 0))}</span></div>`).join('')}</div></details>` : '';
    return `${diag}<div class="neo-stack">${results.map((item)=>`<div class="roleplay-v1-studio-card inner"><div class="roleplay-v1-scene-row-between"><div><strong>${escapeHtml(ctx,item.retrieval_label || item.title || item.result_id || 'memory')}</strong><span>${escapeHtml(ctx,item.result_id || item.source_id || '')}</span></div><span class="neo-chip">${escapeHtml(ctx,item.scene_category || item.memory_type || 'retrieved_memory')}</span></div><p>${escapeHtml(ctx,(item.content || '').slice(0,520))}</p>${listItems(ctx,[`Why: ${item.why_selected || 'retrieval match'}`,`Scope: ${item.scope_id || 'global'}`,`Score: ${item.score ?? item.combined_score ?? 0}`,`Vector: ${item.vector_score ?? 'n/a'}`,`Rerank: ${item.rerank_score ?? 'n/a'}`])}</div>`).join('')}</div>${candidateHtml}`;
  }

  function scenePacketPreviewHtml(ctx, packet) {
    if (!packet) return '<div class="roleplay-v1-studio-empty">No Scene packet built yet. Build one after retrieval to prove exactly what Scene Chat should use.</div>';
    const counts = packet.counts || {}; const labels = packet.section_labels || {}; const keys = packet.scene_packet_category_order || ['universe_context','world_context','location_context','character_context','relationship_context','artifact_context','scenario_context','canon_guards','reveal_gates','continuity_rows','retrieved_memory'];
    const categories = keys.filter((key)=>Number(counts[key] || 0)>0).map((key)=>`${labels[key] || key.replace(/_/g,' ')}: ${counts[key]}`);
    return `<div class="roleplay-v1-studio-card inner"><div class="roleplay-v1-scene-row-between"><div><strong>${escapeHtml(ctx,packet.title || packet.scene_packet_id || 'Scene packet')}</strong><span>${escapeHtml(ctx,packet.scene_packet_id || '')}</span></div><span class="neo-badge">${escapeHtml(ctx,packet.status || 'built')}</span></div>${listItems(ctx,[`Scene: ${packet.scene_id || 'default'}`,`Scope: ${packet.scope_id || packet.sandbox_id || 'global'}`,`Trace: ${(packet.retrieval_trace || {}).trace_id || 'none'}`,`Selected: ${counts.selected_entities || 0}`,`Retrieved: ${counts.retrieved_results || 0}`])}<div class="roleplay-v1-studio-card inner"><strong>Packet categories</strong>${listItems(ctx,categories.length ? categories : ['No categorized context rows yet'])}</div><details><summary>Packet JSON</summary><pre class="roleplay-v1-code">${escapeHtml(ctx,JSON.stringify(packet, null, 2))}</pre></details></div>`;
  }

  function runtimePresetOptionsHtml(ctx, selectedId = '') {
    const presets = stateOf(ctx).roleplayRuntimePresets?.presets || [];
    if (!presets.length) return '<option value="">No compiled scopes yet</option>';
    return presets.map((preset)=>`<option value="${escapeAttr(ctx,preset.preset_id || '')}" ${(selectedId || runtimePresetSelectedValue(ctx)) === preset.preset_id ? 'selected' : ''}>${escapeHtml(ctx,preset.label || preset.preset_id)} · ${escapeHtml(ctx,preset.status || 'ready')}</option>`).join('');
  }
  function selectedRuntimePreset(ctx) { const id = runtimePresetSelectedValue(ctx); const presets = stateOf(ctx).roleplayRuntimePresets?.presets || []; return presets.find((preset)=>preset.preset_id === id) || stateOf(ctx).roleplayRuntimePresets?.latest_preset || null; }
  function presetSelectOptions(ctx, items = [], selected = '') { if (!items.length) return '<option value="">None found</option>'; return items.map((item)=>`<option value="${escapeAttr(ctx,item.record_id || '')}" ${selected === item.record_id ? 'selected' : ''}>${escapeHtml(ctx,item.title || item.record_id)}</option>`).join(''); }
  function presetRecordSummaryHtml(ctx, preset) {
    if (!preset) return '<div class="roleplay-v1-studio-empty">Build a scope in Compile first, then Runtime can use it as a preset.</div>';
    const rows = Object.entries(preset.kind_counts || {}).filter(([,c])=>Number(c || 0)>0).map(([kind,c])=>`${kind.replace(/_/g,' ')}: ${c}`);
    const groups = Object.entries(preset.records_by_kind || {}).filter(([,items])=>(items || []).length).map(([kind,items])=>`<details class="roleplay-v1-studio-card inner"><summary>${escapeHtml(ctx,kind.replace(/_/g,' '))} · ${(items || []).length}</summary><div class="neo-stack">${(items || []).slice(0,10).map((item)=>`<div class="roleplay-v1-mini-row"><strong>${escapeHtml(ctx,item.title || item.record_id)}</strong><span>${escapeHtml(ctx,item.record_id || '')} · ${escapeHtml(ctx,item.compile_status || 'new')}</span></div>`).join('')}</div></details>`).join('');
    return `<div class="roleplay-v1-studio-card inner"><strong>Linked records in this compiled scope</strong>${listItems(ctx, rows.length ? rows : ['No linked record counts yet'])}</div>${groups}`;
  }
  function runtimeDeepHtml(ctx, studio = {}) {
    const s=stateOf(ctx); const presetsState=s.roleplayRuntimePresets || {}; const runtime=s.roleplayRuntime || {}; const preset=selectedRuntimePreset(ctx); const selectedId=runtimePresetSelectedValue(ctx); const prepared=s.roleplayRuntimePreparedPacket || {}; const packetProgress=s.roleplayRuntimePacketProgress || {}; const packetBusy=packetProgress.status==='running';
    const scenarios=preset?.records_by_kind?.scenario || preset?.records_by_kind?.scenarios || []; const characters=preset?.records_by_kind?.character || preset?.records_by_kind?.characters || [];
    const scenarioOptions=presetSelectOptions(ctx, scenarios, prepared.packet_payload?.scenario_id || ''); const playerOptions=presetSelectOptions(ctx, characters, prepared.packet_payload?.player_character_ids || '');
    const readiness=[`Status: ${preset ? (preset.status || 'ready') : 'no preset'}`,`Linked records: ${preset?.linked_record_count || 0}`,`Compiled records: ${preset?.compiled_count || 0}`,`Fragments: ${preset?.fragment_count || 0}`,`Runtime bundle: ${preset?.runtime_bundle_id || runtime.latest_bundle_id || 'none'}`];
    return `<div class="roleplay-v1-studio-grid two"><div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Runtime Preset</strong><p>Select a compiled scope and build scene packets without manual ID drift.</p></div><span class="neo-badge success">Compile</span></div><div class="roleplay-v1-studio-grid two compact"><div><label>Compiled scope / runtime preset</label><select id="roleplay-runtime-preset-select" onchange="setRoleplayRuntimePresetFromUi()">${runtimePresetOptionsHtml(ctx, selectedId)}</select></div><div><label>Status</label><input readonly value="${escapeAttr(ctx,preset ? (preset.status || 'ready') : 'No preset')}"></div></div><div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn admin-engine-btn" onclick="reloadRoleplayRuntimePresets()">Refresh presets</button><button type="button" class="neo-btn admin-engine-btn" onclick="setActiveSubtab('roleplay','studio')">Open Compile Build</button><button type="button" class="neo-btn admin-engine-btn" onclick="refreshRoleplayRuntimeFoundation()">Refresh runtime history</button></div><div class="roleplay-memory-grid"><div class="roleplay-memory-card"><strong>Readiness</strong>${listItems(ctx,readiness)}</div><div class="roleplay-memory-card"><strong>Runtime store</strong>${listItems(ctx,[`Bundles: ${(runtime.bundles || []).length}`,`Latest bundle: ${runtime.latest_bundle_id || 'none'}`,`Preset count: ${presetsState.preset_count || 0}`])}</div></div></div><div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Scene Packet from Preset</strong><p>Pick scenario/player and optionally run retrieval/rerank. Packet building stays backend-owned; the UI lane is module-owned.</p></div><span class="neo-badge">packet builder</span></div><div class="roleplay-v1-studio-grid three compact"><div><label>Scenario</label><select id="roleplay-runtime-preset-scenario">${scenarioOptions}</select></div><div><label>Player character</label><select id="roleplay-runtime-preset-player">${playerOptions}</select></div><div><label>Mode</label><select id="roleplay-runtime-preset-mode"><option value="hybrid">hybrid</option><option value="semantic">semantic</option><option value="keyword">keyword</option><option value="auto">auto</option></select></div></div><label>Packet title</label><input id="roleplay-runtime-preset-packet-title" placeholder="Auto: Scenario — Runtime Packet"><label>Retrieval query override</label><input id="roleplay-runtime-preset-query" placeholder="Optional. Leave blank to auto-build from scenario, cast, relationship, artifacts."><div class="roleplay-v1-studio-grid four compact"><div><label>Final results</label><input id="roleplay-runtime-preset-limit" type="number" value="8" min="1" max="24"></div><div><label>Candidate limit</label><input id="roleplay-runtime-preset-candidate-limit" type="number" value="24" min="1" max="48"></div><div><label>Rerank top</label><input id="roleplay-runtime-preset-rerank-candidate-limit" type="number" value="8" min="1" max="16"></div><div><label>Rerank</label><input id="roleplay-runtime-preset-rerank" type="checkbox"></div></div><label class="roleplay-checkbox"><input id="roleplay-runtime-preset-run-retrieval" type="checkbox"> Run retrieval during packet build <span class="roleplay-panel-note">Optional. Uses background job and diagnostics when enabled.</span></label><div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn admin-engine-btn" onclick="prepareRoleplayPresetScenePacketFromUi()" ${packetBusy?'disabled':''}>${busyLabel(ctx,'Preview Auto Packet','Previewing…',packetBusy && packetProgress.step==='prepare_packet')}</button><button type="button" class="neo-btn primary admin-engine-btn ${packetBusy?'is-busy':''}" onclick="buildRoleplayPresetScenePacketFromUi()" ${packetBusy?'disabled':''}>${busyLabel(ctx,'Build Scene Packet','Building…',packetBusy && packetProgress.step==='build_packet')}</button><button type="button" class="neo-btn admin-engine-btn" onclick="openRoleplaySceneChatFromRuntime()" ${packetBusy?'disabled':''}>Open Scene Chat</button></div>${progressHtml(ctx,packetProgress,{title:'Scene packet progress'})}${scenePacketPreviewHtml(ctx, (prepared || {}).packet_payload || null)}${scenePacketPreviewHtml(ctx, s.roleplayScenePacket || (s.roleplayScenePacketBuilder || {}).latest_packet)}</div><div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Linked Records</strong><p>These records came from the selected compiled scope. This is what the Runtime preset can use.</p></div><span class="neo-badge">${preset?.linked_record_count || 0} records</span></div>${presetRecordSummaryHtml(ctx,preset)}</div><details class="roleplay-v1-studio-card wide"><summary><strong>Advanced retrieval + manual packet controls</strong></summary><div class="roleplay-v1-studio-card inner"><div class="roleplay-v1-scene-row-between"><div><strong>Retrieval debug</strong><p>Use this when testing memory ranking, reranking, and packet candidate selection.</p></div><span class="neo-badge">advanced</span></div><div class="roleplay-v1-studio-grid three compact"><div><label>Query</label><input id="roleplay-runtime-memory-query" placeholder="Search entities, memories, continuity..."></div><div><label>Scope / sandbox</label><input id="roleplay-runtime-memory-scope" placeholder="optional sandbox/session/bundle id"></div><div><label>Mode</label><select id="roleplay-runtime-memory-mode"><option value="hybrid">hybrid</option><option value="semantic">semantic</option><option value="keyword">keyword</option><option value="auto">auto</option></select></div></div><div class="roleplay-v1-studio-grid three compact"><div><label>Final limit</label><input id="roleplay-runtime-memory-limit" type="number" value="12"></div><div><label>Candidate limit</label><input id="roleplay-runtime-memory-candidate-limit" type="number" value="36"></div><div><label>Index limit</label><input id="roleplay-runtime-memory-index-limit" type="number" value="500"></div></div><label>Memory types</label><input id="roleplay-runtime-memory-types" value="entities,memory_fragments,shared_memories,continuity,turn_summaries,story_checkpoints"><div class="roleplay-v1-studio-grid three compact"><div><label>Rerank</label><input id="roleplay-runtime-memory-rerank" type="checkbox" checked></div><div><label>Force reindex</label><input id="roleplay-runtime-memory-index-force" type="checkbox"></div><div><label>Rebuild search docs</label><input id="roleplay-runtime-memory-rebuild-search" type="checkbox"></div></div><div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn admin-engine-btn" onclick="indexRoleplaySemanticMemoryFromUi('studio')">Index memory vectors</button><button type="button" class="neo-btn primary admin-engine-btn" onclick="runRoleplayRuntimeRetrievalLaneFromUi('studio')">Run runtime retrieval</button><button type="button" class="neo-btn admin-engine-btn" onclick="searchRoleplaySemanticRetrievalFromUi('studio')">Semantic only</button><button type="button" class="neo-btn admin-engine-btn" onclick="searchRoleplayRetrievalFoundationFromUi('studio')">Keyword only</button></div>${retrievalResultsHtml(ctx, s.roleplayRuntimeRetrievalSearch || s.roleplayRetrievalSearch)}</div><div class="roleplay-v1-studio-card inner"><strong>Raw preset JSON</strong><pre class="roleplay-v1-code">${escapeHtml(ctx,JSON.stringify(preset || {}, null, 2))}</pre></div></details></div>`;
  }

  function studioCompileDeepHtml(ctx, studio = {}) {
    const compile = studio?.compile || {}; const sourceDocs=compile.novel_source_count ?? studio?.novel?.source_count ?? 0; const chunks=compile.novel_chunk_count ?? studio?.novel?.chunk_count ?? 0; const canon=compile.novel_canon_record_count ?? studio?.novel?.canon_record_count ?? 0;
    return `<div class="roleplay-v1-studio-grid two">${scopeBuildDeepHtml(ctx, studio)}<div class="roleplay-v1-studio-card"><div class="roleplay-v1-scene-row-between"><div><strong>Source / Novel lane</strong><p>Source documents stay separate from Builder records. Generate breakdowns and compile source memory only for novel/chapter canon testing.</p></div><span class="neo-badge">source</span></div>${listItems(ctx,[`Source docs: ${sourceDocs}`,`Source chunks: ${chunks}`,`Candidate canon: ${canon}`,`Existing bundles: ${compile.bundle_count ?? stateOf(ctx).roleplayRuntime?.bundle_count ?? 0}`,`Latest bundle: ${compile.latest_bundle_id || stateOf(ctx).roleplayRuntime?.latest_bundle_id || 'none'}`])}<div class="roleplay-v1-studio-row wrap"><button type="button" class="neo-btn admin-engine-btn" onclick="generateRoleplayAllSourceBreakdownsFromUi()">Generate Source breakdowns</button><button type="button" class="neo-btn primary admin-engine-btn" onclick="compileRoleplayAllNovelSourcesFromUi()">Compile Source memory</button><button type="button" class="neo-btn admin-engine-btn" onclick="refreshRoleplayRuntimeFoundation()">Refresh runtime history</button></div><label class="roleplay-checkbox"><input id="roleplay-novel-promote-canon" type="checkbox"> Also write source chunks as candidate canon records</label></div><div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Scoped Compile Inspector</strong><p>Deep compile planner is now module-owned, but backend compile remains authoritative.</p></div><span class="neo-badge">compile planner</span></div>${scopedCompileHtml(ctx)}</div><div class="roleplay-v1-studio-card wide"><div class="roleplay-v1-scene-row-between"><div><strong>Pipeline guide</strong><p>Scope → linked record preview → build memory/runtime → Runtime retrieval → Scene packet → Scene Chat.</p></div><span class="neo-badge">compiler</span></div><pre class="roleplay-v1-code">Unified scope build\n- Select a Universe / World / Region / City / Location / Scenario.\n- Neo finds direct records, reverse links, and neighbor-linked records.\n- Preview shows compile status before touching memory.\n- Build supersedes old fragments, rebuilds search, indexes vectors, then builds a scoped runtime bundle.\n- Runtime uses the same compiled scope instead of memory dumping.</pre></div></div>`;
  }


  function legacyForgeRenderer(ctx, name, fallback = '') {
    try {
      const fn = window[name];
      if (typeof fn === 'function') return fn(ctx.forge || stateOf(ctx).roleplayForge || {});
    } catch (error) { console.warn(`Roleplay Forge renderer ${name} failed`, error); }
    return fallback || '<div class="roleplay-v1-studio-empty">Forge renderer is not available yet. Legacy fallback remains active.</div>';
  }
  function roleplayForgePanelShellHtml(ctx, title, body, badge = 'Forge') {
    return `<div class="roleplay-v1-studio-card wide roleplay-forge-module-card"><div class="roleplay-v1-scene-row-between"><div><strong>${escapeHtml(ctx,title)}</strong><p>Forge Builder lane. Backend save/delete/import and file-backed records remain server-owned.</p></div><span class="neo-badge success">${escapeHtml(ctx,badge)}</span></div>${body}</div>`;
  }
  function forgeBuilderRailModuleHtml(ctx) { return roleplayForgePanelShellHtml(ctx, 'Forge Builder Rail', legacyForgeRenderer(ctx, 'roleplayForgeBuilderRailHtml'), 'rail'); }
  function forgeBuilderModuleHtml(ctx) { return roleplayForgePanelShellHtml(ctx, 'Forge Builder', legacyForgeRenderer(ctx, 'roleplayForgeBuilderHtml'), 'builder'); }
  function forgeRecordsModuleHtml(ctx) { return roleplayForgePanelShellHtml(ctx, 'Forge Builder Records', legacyForgeRenderer(ctx, 'roleplayForgeRecordsHtml'), 'records'); }
  function forgeTemplateInspectorHtml(ctx) {
    const st = stateOf(ctx);
    const forge = ctx.forge || st.roleplayForge || {};
    const kind = forgeCurrentKind(ctx);
    const template = forgeTemplate(ctx, kind) || {};
    const fieldPaths = Array.isArray(template.field_paths) ? template.field_paths : [];
    const hierarchy = template.hierarchy || forge.hierarchy?.[kind] || {};
    const payloadPreview = template.json_template_payload || forgeCapturePayload(ctx) || {};
    const markdown = template.md_template_text || `# ${kind}\n\n## Summary\n\n`;
    const fieldList = fieldPaths.length
      ? fieldPaths.slice(0, 80).map((path) => `<li><code>${escapeHtml(ctx, path)}</code></li>`).join('')
      : '<li>No template field paths declared yet.</li>';
    return `<div class="roleplay-v1-studio-card roleplay-forge-template-inspector"><div class="roleplay-v1-scene-row-between"><div><strong>Template Inspector</strong><p>Template inspector for the active Forge kind. This is read-only; builder save/apply behavior stays unchanged.</p></div><span class="neo-badge">template</span></div>${metaList(ctx,[`Kind: ${kind}`, `Template kind: ${template.template_kind || kind}`, `Fields: ${fieldPaths.length}`, `Markdown chars: ${markdown.length}`])}<div class="roleplay-v1-studio-grid two"><div><h4>Field paths</h4><ul class="roleplay-v1-mini-list">${fieldList}</ul></div><div><h4>Hierarchy</h4><pre class="roleplay-v1-code">${escapeHtml(ctx, JSON.stringify(hierarchy, null, 2))}</pre></div></div><details class="roleplay-v1-details"><summary>JSON template payload preview</summary><pre class="roleplay-v1-code">${escapeHtml(ctx, JSON.stringify(payloadPreview, null, 2))}</pre></details><details class="roleplay-v1-details"><summary>Markdown template preview</summary><pre class="roleplay-v1-code">${escapeHtml(ctx, markdown)}</pre></details><div class="roleplay-v1-studio-row"><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayRefreshForgeTemplateInspector()">Refresh template inspector</button><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayForgeResetCurrentPayload()">New from template</button></div></div>`;
  }
  function forgeSqliteInspectorHtml(ctx) {
    const st = stateOf(ctx);
    const forge = ctx.forge || st.roleplayForge || {};
    const inspector = forge.inspector || {};
    const sqlite = forge.sqlite || {};
    const records = Array.isArray(forge.records) ? forge.records : [];
    const activeKind = forgeCurrentKind(ctx);
    const kindCount = records.filter((record) => record.kind === activeKind).length;
    const tables = Array.isArray(sqlite.tables) ? sqlite.tables : [];
    const tableList = tables.length ? tables.slice(0, 80).map((table) => `<li><code>${escapeHtml(ctx, table)}</code></li>`).join('') : '<li>No SQLite tables reported by Forge state yet.</li>';
    return `<div class="roleplay-v1-studio-card roleplay-forge-sqlite-inspector"><div class="roleplay-v1-scene-row-between"><div><strong>SQLite Inspector</strong><p>Storage inspector for Forge SQLite visibility. Persistence and sync remain backend-owned.</p></div><span class="neo-badge">sqlite</span></div>${metaList(ctx,[`Status: ${sqlite.status || 'not connected'}`, `SQLite path: ${sqlite.path || 'neo_data/roleplay/roleplay.sqlite'}`, `Records: ${inspector.record_count ?? records.length}`, `Active kind records: ${kindCount}`, `Storage root: ${inspector.storage_root || 'neo_data/roleplay/entities'}`])}<div class="roleplay-v1-studio-grid two"><div><h4>Reported tables</h4><ul class="roleplay-v1-mini-list">${tableList}</ul></div><div><h4>Storage contract</h4><pre class="roleplay-v1-code">${escapeHtml(ctx, JSON.stringify({ source_of_truth: 'SQLite + file-backed Forge records', json: 'import/export/snapshots', compile: 'backend-owned memory/runtime pipeline', chroma: 'optional semantic mirror' }, null, 2))}</pre></div></div><div class="roleplay-v1-studio-row"><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayRefreshForgeSqliteInspector()">Refresh SQLite inspector</button><button type="button" class="neo-btn secondary roleplay-btn" onclick="reloadRoleplayForgeState()">Reload Forge state</button></div></div>`;
  }
  function forgeInspectorModuleHtml(ctx) { return roleplayForgePanelShellHtml(ctx, 'Forge Inspector / SQLite', `${forgeSqliteInspectorHtml(ctx)}${forgeTemplateInspectorHtml(ctx)}`, 'inspector'); }
  function forgeImportState(ctx) {
    const s = stateOf(ctx);
    s.roleplayForgeImport = s.roleplayForgeImport || { file: null, filename: '', scopeMode: 'apply_active_scope_where_empty', conflictMode: 'replace_existing', preview: null, result: null, status: '', busy: false };
    return s.roleplayForgeImport;
  }
  function forgeCurrentKind(ctx) {
    if (typeof window.roleplayForgeCurrentKind === 'function') return window.roleplayForgeCurrentKind();
    return stateOf(ctx).roleplayForgeWorkingPayload?.kind || stateOf(ctx).roleplayForge?.active_kind || 'universe';
  }
  function forgeCapturePayload(ctx) {
    if (typeof window.roleplayForgeCaptureFormPayload === 'function') return window.roleplayForgeCaptureFormPayload();
    return stateOf(ctx).roleplayForgeWorkingPayload || { kind: forgeCurrentKind(ctx), label: 'Untitled Roleplay Record', summary: '' };
  }
  function forgeApplyScope(ctx, payload) {
    if (typeof window.roleplayForgeApplyActiveScopeToPayload === 'function') return window.roleplayForgeApplyActiveScopeToPayload(payload);
    return payload;
  }
  function forgeTemplate(ctx, kind) {
    if (typeof window.roleplayForgeTemplate === 'function') return window.roleplayForgeTemplate(kind);
    return null;
  }
  function forgeRecordId(ctx, record) {
    if (typeof window.roleplayForgeRecordId === 'function') return window.roleplayForgeRecordId(record || {});
    return record?.record_id || record?.id || record?.payload?.id || '';
  }
  function forgeActiveScopeRecord(ctx) {
    if (typeof window.roleplayForgeActiveScopeRecord === 'function') return window.roleplayForgeActiveScopeRecord();
    const id = stateOf(ctx).roleplayForgeActiveScopeRecordId || '';
    return (stateOf(ctx).roleplayForge?.records || []).find((item)=>forgeRecordId(ctx,item) === id) || null;
  }

  function forgeScopeKindsForMode(ctx, mode = '') {
    const clean = String(mode || stateOf(ctx).roleplayForgeScopeMode || 'universe').toLowerCase();
    const map = { universe:['universe'], world:['world'], region:['region'], city:['city'], location:['location'], character:['character'], canon:['legend','scenario','artifact','ritual','cycle','creature','organization'] };
    return map[clean] || map.universe;
  }
  function forgeScopeModeFromRecord(ctx, record = {}) {
    const kind = String(record.kind || '').toLowerCase();
    return ['universe','world','region','city','location','character'].includes(kind) ? kind : 'canon';
  }
  function forgeScopeCandidateRecords(ctx) {
    const s = stateOf(ctx); const forge = s.roleplayForge || {};
    const allowed = new Set(forgeScopeKindsForMode(ctx));
    const query = String(s.roleplayForgeScopeSearch || '').trim().toLowerCase();
    return (forge.records || []).filter((record) => {
      if (!allowed.has(String(record.kind || '').toLowerCase())) return false;
      if (!query) return true;
      const tags = Array.isArray(record.tags) ? record.tags : [];
      return [record.title, record.record_id, record.kind, record.body, record.payload?.summary, ...tags].join(' ').toLowerCase().includes(query);
    });
  }
  function forgeKindDisplay(ctx, kind = '') {
    const forge = stateOf(ctx).roleplayForge || {};
    const found = (forge.kinds || []).find((item) => item.kind_id === kind || item.kind === kind);
    return found?.display_name || String(kind || 'record').replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
  }
  function forgeScopeSummaryHtml(ctx) {
    const active = forgeActiveScopeRecord(ctx);
    if (!active) return `<div class="roleplay-v1-scope-empty"><strong>No active scope set.</strong><span>Pick a ${escapeHtml(ctx,(stateOf(ctx).roleplayForgeScopeMode || 'universe').replace(/_/g,' '))} below. New records and imports can inherit that scope automatically.</span></div>`;
    return `<div class="roleplay-v1-active-scope"><strong>Current scope: ${escapeHtml(ctx,active.title || forgeRecordId(ctx, active))}</strong><span>${escapeHtml(ctx,forgeKindDisplay(ctx, active.kind || 'record'))} · ${escapeHtml(ctx,forgeRecordId(ctx, active))}</span></div>`;
  }
  function forgeScopePickerHtml(ctx) {
    const s = stateOf(ctx); const mode = s.roleplayForgeScopeMode || 'universe';
    const activeId = String(s.roleplayForgeActiveScopeRecordId || '');
    const rows = forgeScopeCandidateRecords(ctx).map((record)=>{ const id=forgeRecordId(ctx, record); const active=id===activeId; return `<div class="roleplay-v1-scope-row${active?' active':''}"><div><strong>${escapeHtml(ctx,record.title || id)}</strong><span>${escapeHtml(ctx,forgeKindDisplay(ctx, record.kind || 'record'))} · ${escapeHtml(ctx,id)}</span></div><button type="button" class="neo-btn secondary roleplay-btn${active?' active-scope-btn':''}" onclick="roleplayForgeUseRecordAsScope('${escapeAttr(ctx,id)}')">${active?'Current scope':'Set scope'}</button></div>`; }).join('');
    const empty = `<div class="roleplay-v1-scope-empty"><strong>No ${escapeHtml(ctx,mode.replace(/_/g,' '))} records found.</strong><span>Create/save a ${escapeHtml(ctx,mode.replace(/_/g,' '))} record first, or switch scope type.</span></div>`;
    return `<div class="roleplay-v1-scope-card roleplay-forge-advanced-scope-module"><div class="roleplay-v1-scene-row-between"><div><strong>Advanced scope picker</strong><p>Scope selection drives child creation, import inheritance, Compile discovery, and Runtime packet grounding.</p></div><span class="neo-badge success">Scope</span></div><select id="roleplay-forge-scope-kind" class="roleplay-v1-select" onchange="roleplayForgeSetScopeMode(this.value)"><option value="universe" ${mode==='universe'?'selected':''}>Universe scope</option><option value="world" ${mode==='world'?'selected':''}>World scope</option><option value="region" ${mode==='region'?'selected':''}>Region / Kingdom scope</option><option value="city" ${mode==='city'?'selected':''}>City / Settlement scope</option><option value="location" ${mode==='location'?'selected':''}>Location scope</option><option value="character" ${mode==='character'?'selected':''}>Character scope</option><option value="canon" ${mode==='canon'?'selected':''}>Canon / story scope</option></select>${forgeScopeSummaryHtml(ctx)}<div class="roleplay-v1-scope-tools"><input id="roleplay-forge-scope-search" value="${escapeAttr(ctx,s.roleplayForgeScopeSearch || '')}" oninput="roleplayForgeSetScopeSearch(this.value)" placeholder="Search ${escapeAttr(ctx,mode.replace(/_/g,' '))} scope records"><button type="button" class="neo-btn secondary roleplay-btn" onclick="reloadRoleplayForgeState()">Refresh scope list</button></div><strong>Select or lock a scope to keep Forge imports and child records sandboxed.</strong>${rows ? `<div class="roleplay-v1-scope-list">${rows}</div>` : empty}</div>`;
  }
  function forgeImportRecordsPreviewHtml(ctx, records = []) {
    if (!Array.isArray(records) || !records.length) return '';
    return `<details class="roleplay-v1-details" open><summary>Import preview records · ${records.length}</summary><div class="roleplay-forge-import-record-list">${records.map((record)=>`<div class="roleplay-forge-import-record"><strong>${escapeHtml(ctx,record.title || record.label || record.record_id || 'Untitled')}</strong><span>${escapeHtml(ctx,record.kind || record.record_kind || 'record')} · ${escapeHtml(ctx,record.record_id || record.id || '')}</span>${record.scope_id || record.scope_kind ? `<small>${escapeHtml(ctx,record.scope_kind || 'scope')} · ${escapeHtml(ctx,record.scope_id || '')}</small>` : ''}</div>`).join('')}</div></details>`;
  }
  function forgeImportStatusHtml(ctx) {
    const imp = forgeImportState(ctx); const preview = imp.preview || null; const result = imp.result || null;
    const counts = preview?.kind_counts || result?.kind_counts || {};
    const countChips = Object.entries(counts).map(([kind,count])=>`<span class="neo-chip">${escapeHtml(ctx,forgeKindDisplay(ctx, kind))}: ${escapeHtml(ctx,String(count))}</span>`).join('');
    const records = preview?.records || []; const warnings = preview?.warnings || result?.warnings || []; const errors = result?.errors || [];
    return `<div class="roleplay-forge-import-status ${imp.busy?'running':''}"><div class="roleplay-forge-import-line"><strong>${escapeHtml(ctx,imp.status || 'No bundle selected yet.')}</strong>${imp.busy?'<span class="neo-mini-loader"></span>':''}</div>${preview ? `<div class="neo-muted roleplay-import-preview-summary">Preview found ${escapeHtml(ctx,String(preview.record_count || 0))} records. Showing all ${escapeHtml(ctx,String(records.length || 0))}. Applied scope fields: ${escapeHtml(ctx,String(preview.applied_scope_field_count || 0))}.</div>` : ''}${result ? `<div class="neo-muted">Imported ${escapeHtml(ctx,String(result.imported_count || 0))} records · Errors ${escapeHtml(ctx,String(result.error_count || 0))}.</div>` : ''}${countChips ? `<div class="neo-chip-row roleplay-forge-import-counts">${countChips}</div>` : ''}${forgeImportRecordsPreviewHtml(ctx, records)}${warnings.length ? `<div class="neo-warn">${warnings.slice(0,8).map((item)=>escapeHtml(ctx,String(item))).join('<br>')}${warnings.length > 8 ? `<br>… ${escapeHtml(ctx,String(warnings.length - 8))} more warning(s)` : ''}</div>` : ''}${errors.length ? `<div class="neo-error">${errors.slice(0,8).map((item)=>escapeHtml(ctx,String(item.error || item))).join('<br>')}${errors.length > 8 ? `<br>… ${escapeHtml(ctx,String(errors.length - 8))} more error(s)` : ''}</div>` : ''}</div>`;
  }
  function forgeAdvancedImportHtml(ctx) {
    const imp = forgeImportState(ctx); const activeScope = forgeActiveScopeRecord(ctx);
    const scopeLabel = activeScope ? `${forgeKindDisplay(ctx, activeScope.kind || '')} · ${activeScope.title || forgeRecordId(ctx, activeScope)}` : 'No active scope selected';
    return `<div class="roleplay-forge-import-card roleplay-forge-advanced-import-module"><div class="roleplay-forge-import-head"><div><strong>Advanced import / scope-safe bundle intake</strong><p>Upload JSON, Markdown, or ZIP bundles and bind them to the active scope without bypassing backend Forge contracts.</p></div><span class="neo-badge">${escapeHtml(ctx,scopeLabel)}</span></div><div class="roleplay-forge-import-grid"><label>Upload file<input id="roleplay-forge-import-file" type="file" accept=".json,.md,.zip,application/json,application/zip,text/markdown" onchange="roleplayForgeImportChooseFile(this.files && this.files[0])"></label><label>Scope behavior<select id="roleplay-forge-import-scope-mode" onchange="roleplayForgeImportUpdateOption('scopeMode', this.value)"><option value="preserve_file_scope" ${imp.scopeMode==='preserve_file_scope'?'selected':''}>Preserve file scope</option><option value="apply_active_scope_where_empty" ${imp.scopeMode==='apply_active_scope_where_empty'?'selected':''}>Apply active scope where empty</option><option value="force_active_scope" ${imp.scopeMode==='force_active_scope'?'selected':''}>Force active scope</option></select></label><label>Existing records<select id="roleplay-forge-import-conflict-mode" onchange="roleplayForgeImportUpdateOption('conflictMode', this.value)"><option value="replace_existing" ${imp.conflictMode==='replace_existing'?'selected':''}>Replace existing</option><option value="skip_existing" ${imp.conflictMode==='skip_existing'?'selected':''}>Skip existing</option><option value="copy_with_new_id" ${imp.conflictMode==='copy_with_new_id'?'selected':''}>Create copy with new ID</option></select></label></div><div class="roleplay-v1-studio-card inner"><strong>Import scope contract</strong>${metaList(ctx,[`File: ${imp.filename || 'none selected'}`, `Scope mode: ${imp.scopeMode || 'apply_active_scope_where_empty'}`, `Conflict mode: ${imp.conflictMode || 'replace_existing'}`, `Active scope: ${scopeLabel}`, 'Backend remains authoritative for parsing, conflict handling, save path, SQLite sync, and compile/rerank rebuild.'])}</div><div class="neo-actions roleplay-v1-actions"><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayForgeImportPreview()" ${imp.busy || !imp.file ? 'disabled' : ''}>Preview import</button><button type="button" class="neo-btn primary roleplay-btn" onclick="roleplayForgeImportRun()" ${imp.busy || !imp.file ? 'disabled' : ''}>Import records</button><button type="button" class="neo-btn secondary roleplay-btn" onclick="roleplayForgeImportClear()">Clear</button></div>${forgeImportStatusHtml(ctx)}</div>`;
  }
  function forgeImportFormData(ctx) {
    if (typeof window.roleplayForgeImportFormData === 'function') return window.roleplayForgeImportFormData();
    const imp = forgeImportState(ctx);
    if (!imp.file) throw new Error('Choose a .json, .md, or .zip file first.');
    const activeScope = forgeActiveScopeRecord(ctx);
    const form = new FormData();
    form.append('file', imp.file);
    form.append('scope_mode', imp.scopeMode || 'apply_active_scope_where_empty');
    form.append('conflict_mode', imp.conflictMode || 'replace_existing');
    form.append('scope_kind', activeScope?.kind || '');
    form.append('scope_id', forgeRecordId(ctx, activeScope || {}) || '');
    return form;
  }

  const actions = {
    async reloadRoleplayForgeState(ctx) { const st=stateOf(ctx); st.roleplayForge = await loadJson(ctx, '/api/roleplay/forge/state', st.roleplayForge || null); render(ctx); },
    async roleplayRefreshForgeSqliteInspector(ctx) { const st=stateOf(ctx); st.roleplayForge = await loadJson(ctx, '/api/roleplay/forge/state', st.roleplayForge || null); st.roleplayMemory = await loadJson(ctx, '/api/roleplay/memory/state', st.roleplayMemory || null); st.roleplayRetrieval = await loadJson(ctx, '/api/roleplay/retrieval/state', st.roleplayRetrieval || null); render(ctx); },
    async roleplayRefreshForgeTemplateInspector(ctx) { const st=stateOf(ctx); st.roleplayForge = await loadJson(ctx, '/api/roleplay/forge/state', st.roleplayForge || null); render(ctx); },
    roleplayForgeSetScopeMode(ctx) { const s=stateOf(ctx); s.roleplayForgeScopeMode = String(ctx.mode || ctx.value || 'universe'); s.roleplayForgeScopeSearch = ''; const active = forgeActiveScopeRecord(ctx); const allowed = new Set(forgeScopeKindsForMode(ctx, s.roleplayForgeScopeMode)); if (active && !allowed.has(String(active.kind || '').toLowerCase())) s.roleplayForgeActiveScopeRecordId = ''; render(ctx); },
    roleplayForgeSetScopeSearch(ctx) { stateOf(ctx).roleplayForgeScopeSearch = String(ctx.value || ctx.search || ''); render(ctx); },
    roleplayForgeUseRecordAsScope(ctx) { const s=stateOf(ctx); const recordId = ctx.recordId || ctx.record_id || ctx.id || ''; s.roleplayForgeActiveScopeRecordId = recordId; const record = forgeActiveScopeRecord(ctx); if (record) s.roleplayForgeScopeMode = forgeScopeModeFromRecord(ctx, record); s.roleplayForgeRecordFilters = { ...(s.roleplayForgeRecordFilters || {}), view: 'all', group: 'kind' }; const payload = JSON.parse(JSON.stringify(s.roleplayForgeWorkingPayload || forgeCapturePayload(ctx) || {})); s.roleplayForgeWorkingPayload = forgeApplyScope(ctx, payload); render(ctx); },
    roleplayForgeImportUpdateOption(ctx) { const imp=forgeImportState(ctx); const key = ctx.key || ''; if (key) imp[key] = ctx.value; imp.preview = null; imp.result = null; render(ctx); },
    roleplayForgeImportChooseFile(ctx) { const imp=forgeImportState(ctx); const file = ctx.file || null; imp.file = file; imp.filename = file?.name || ''; imp.preview = null; imp.result = null; imp.status = file ? `Selected ${file.name}` : 'No bundle selected yet.'; render(ctx); },
    roleplayForgeImportClear(ctx) { stateOf(ctx).roleplayForgeImport = { file: null, filename: '', scopeMode: 'apply_active_scope_where_empty', conflictMode: 'replace_existing', preview: null, result: null, status: '', busy: false }; render(ctx); },
    roleplayForgeSelectKind(ctx) { if (typeof window.roleplayForgeSelectKind === 'function') window.roleplayForgeSelectKind(ctx.kindId || ctx.kind || 'universe', ctx.railId || ''); else { const st=stateOf(ctx); if (st.roleplayForge) st.roleplayForge.active_kind = ctx.kindId || ctx.kind || 'universe'; render(ctx); } },
    roleplayForgeLoadRecord(ctx) { if (typeof window.roleplayForgeLoadRecord === 'function') window.roleplayForgeLoadRecord(ctx.recordId || ctx.record_id || ''); },
    roleplayForgeResetCurrentPayload(ctx) { if (typeof window.roleplayForgeResetCurrentPayload === 'function') window.roleplayForgeResetCurrentPayload(); },
    async saveRoleplayForgeRecordFromUi(ctx) { const st=stateOf(ctx); const payloadObject = forgeApplyScope(ctx, forgeCapturePayload(ctx)); st.roleplayForgeWorkingPayload = payloadObject; const payload = { kind: payloadObject.kind || forgeCurrentKind(ctx), title: payloadObject.label || payloadObject.display_label || 'Untitled Roleplay Record', body: payloadObject.summary || '', tags: Array.isArray(payloadObject.tags) ? payloadObject.tags : String(payloadObject.tags || ''), payload: payloadObject, markdown: st.roleplayForgeMarkdownDraft || forgeTemplate(ctx, payloadObject.kind)?.md_template_text || '' }; const response = await fetch('/api/roleplay/forge/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); const result = await response.json().catch(()=>({})); if (!response.ok) throw new Error(result.detail || result.error || 'Forge save failed'); st.roleplayForge = result.forge || await loadJson(ctx, '/api/roleplay/forge/state', null); st.roleplayMemory = await loadJson(ctx, '/api/roleplay/memory/state', st.roleplayMemory || null); st.roleplayRetrieval = await loadJson(ctx, '/api/roleplay/retrieval/state', st.roleplayRetrieval || null); st.roleplayForgeWorkingPayload = result.record?.payload || payloadObject; st.roleplayForgeLoadedRecordId = result.record?.record_id || st.roleplayForgeLoadedRecordId || ''; st.roleplayForgeEditorTab = 'form'; render(ctx); },
    async deleteRoleplayForgeRecordFromUi(ctx) { const st=stateOf(ctx); const recordId=ctx.recordId || ctx.record_id || ''; if(!recordId) return; if(!window.confirm('Delete this Forge record?')) return; const response = await fetch('/api/roleplay/forge/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ record_id: recordId, kind: ctx.kind || '' }) }); const result = await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Forge delete failed'); st.roleplayForge = result.forge || await loadJson(ctx, '/api/roleplay/forge/state', null); st.roleplayMemory = await loadJson(ctx, '/api/roleplay/memory/state', st.roleplayMemory || null); st.roleplayRetrieval = await loadJson(ctx, '/api/roleplay/retrieval/state', st.roleplayRetrieval || null); if(st.roleplayForgeLoadedRecordId === recordId){ st.roleplayForgeLoadedRecordId=''; if(typeof window.roleplayForgeResetCurrentPayload === 'function') window.roleplayForgeResetCurrentPayload(); else render(ctx); return; } render(ctx); },
    async roleplayForgeImportPreview(ctx) { const imp=forgeImportState(ctx); try { imp.busy=true; imp.status='Previewing import bundle…'; render(ctx); const response=await fetch('/api/roleplay/forge-import/preview',{method:'POST',body:forgeImportFormData(ctx)}); const payload=await response.json().catch(()=>({})); if(!response.ok) throw new Error(payload.detail || payload.message || 'Preview failed'); imp.preview=payload; imp.result=null; imp.status=payload.record_count ? `Preview ready: ${payload.record_count} records found.` : 'Preview found no supported records.'; } catch(error){ imp.status=`Preview failed: ${error.message || error}`; } finally { imp.busy=false; render(ctx); } },
    async roleplayForgeImportRun(ctx) { const st=stateOf(ctx); const imp=forgeImportState(ctx); try { imp.busy=true; imp.status='Importing records through Forge save path…'; render(ctx); const response=await fetch('/api/roleplay/forge-import/import',{method:'POST',body:forgeImportFormData(ctx)}); const payload=await response.json().catch(()=>({})); if(!response.ok) throw new Error(payload.detail || payload.message || 'Import failed'); imp.result=payload; imp.status=`Import complete: ${payload.imported_count || 0} records imported.`; st.roleplayForge=await loadJson(ctx,'/api/roleplay/forge/state',st.roleplayForge||null); st.roleplayScopedCompile=await loadJson(ctx,'/api/roleplay/scoped-compile/state',st.roleplayScopedCompile||null); st.roleplayScopeBuild=await loadJson(ctx,'/api/roleplay/scope-build/state',st.roleplayScopeBuild||null); st.roleplayScopeBuildPreview=null; st.roleplayScopeBuildSelected=''; } catch(error){ imp.status=`Import failed: ${error.message || error}`; } finally { imp.busy=false; render(ctx); } },
    setRoleplayStoriesView(ctx) { stateOf(ctx).roleplayStoriesActiveView = ctx.view || 'workspace'; render(ctx); },
    setRoleplayStoriesArchiveView(ctx) { stateOf(ctx).roleplayStoriesArchiveView = ctx.view || 'stories'; render(ctx); },
    setRoleplayStoriesInspectorView(ctx) { stateOf(ctx).roleplayStoriesInspectorView = ctx.view || 'summary'; render(ctx); },
    async refreshRoleplayStoriesFromUi(ctx) { const st=stateOf(ctx); st.roleplayStories = await loadJson(ctx, '/api/roleplay/stories/state', st.roleplayStories || null); st.roleplayProjectLinks = await loadJson(ctx, '/api/roleplay/project-links?limit=80', st.roleplayProjectLinks || null); st.roleplayEngineBridge = await loadJson(ctx, '/api/roleplay/engine-bridge/state', st.roleplayEngineBridge || null); st.roleplayStoryRestore = await loadJson(ctx, '/api/roleplay/story-checkpoint-restore/state', st.roleplayStoryRestore || null); render(ctx); },
    fillRoleplayStoriesFromScene(ctx) { const scene=stateOf(ctx).roleplayScene || {}; const setup=scene.setup || {}; const title=document.getElementById('roleplay-storyline-title'); const summary=document.getElementById('roleplay-storyline-premise'); if(title && !title.value) title.value = setup.title || setup.scene_id || 'Storyline from current Scene'; if(summary && !summary.value) summary.value = setup.scene_premise || setup.premise || setup.scene_notes || ''; },
    clearRoleplayStorylineForm() { ['roleplay-storyline-title','roleplay-storyline-premise','roleplay-storyline-project-id','roleplay-storyline-arc','roleplay-storyline-beats'].forEach((id)=>{ const node=document.getElementById(id); if(node) node.value=''; }); },
    clearRoleplayStorySessionForm() { const node=document.getElementById('roleplay-story-session-summary'); if(node) node.value=''; },
    async createRoleplayStorylineFromUi(ctx) { const st=stateOf(ctx); const payload={ title:getValue('roleplay-storyline-title','Untitled Storyline') || 'Untitled Storyline', premise:getValue('roleplay-storyline-premise',''), project_id:getValue('roleplay-storyline-project-id',''), continuity_policy:getValue('roleplay-storyline-continuity-policy','runtime_anchored'), arc:getValue('roleplay-storyline-arc',''), beats:getValue('roleplay-storyline-beats',''), status:'draft' }; const response=await fetch('/api/roleplay/storyline/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Storyline create failed'); st.roleplayStories=result.stories || await loadJson(ctx,'/api/roleplay/stories/state',null); st.roleplayStoriesActiveView='workspace'; render(ctx); },
    async createRoleplayStorySessionFromUi(ctx) { const st=stateOf(ctx); const stories=st.roleplayStories || {}; const payload={ storyline_id:stories.active_storyline_id || stories.workspace?.active_storyline_id || '', summary:getValue('roleplay-story-session-summary','Untitled session branch') || 'Untitled session branch', seed_from_checkpoint:isChecked('roleplay-story-session-seed', true), mode_lock:stories.workspace?.restore_target?.mode_lock || 'cinematic_authoring', interaction_mode:stories.workspace?.restore_target?.interaction_mode || 'roleplay' }; const response=await fetch('/api/roleplay/story-session/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Story session create failed'); st.roleplayStories=result.stories || await loadJson(ctx,'/api/roleplay/stories/state',null); st.roleplayStoriesActiveView='workspace'; render(ctx); },
    async reloadRoleplayScopedCompileState(ctx) { const s=stateOf(ctx); s.roleplayScopedCompile = await loadJson(ctx,'/api/roleplay/scoped-compile/state',s.roleplayScopedCompile || null); s.roleplayScopeBuild = await loadJson(ctx,'/api/roleplay/scope-build/state',s.roleplayScopeBuild || null); render(ctx); },
    async previewRoleplayScopedCompilePlanFromUi(ctx) { const s=stateOf(ctx); const response=await fetch('/api/roleplay/scoped-compile/preview-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scopedCompilePayload(ctx))}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Scoped compile preview failed'); s.roleplayScopedCompilePlan=result; s.roleplayScopedCompile=await loadJson(ctx,'/api/roleplay/scoped-compile/state',s.roleplayScopedCompile || null); s.roleplayScopeBuild=await loadJson(ctx,'/api/roleplay/scope-build/state',s.roleplayScopeBuild || null); render(ctx); },
    async executeRoleplayScopedCompilePlanFromUi(ctx) { const s=stateOf(ctx); const payload=scopedCompilePayload(ctx); payload.plan=s.roleplayScopedCompilePlan || null; payload.rebuild_search=isChecked('roleplay-scoped-compile-rebuild-search', true); payload.index_after=isChecked('roleplay-scoped-compile-index', true); payload.mirror_after=isChecked('roleplay-scoped-compile-mirror', false); const response=await fetch('/api/roleplay/scoped-compile/execute-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Scoped compile failed'); s.roleplayScopedCompileLastRun=result; s.roleplayScopedCompile=result.state || await loadJson(ctx,'/api/roleplay/scoped-compile/state',s.roleplayScopedCompile || null); s.roleplayMemory=await loadJson(ctx,'/api/roleplay/memory/state',s.roleplayMemory || null); s.roleplayRetrieval=await loadJson(ctx,'/api/roleplay/retrieval/state',s.roleplayRetrieval || null); s.roleplayRuntimeRetrieval=await loadJson(ctx,'/api/roleplay/runtime-retrieval/state',s.roleplayRuntimeRetrieval || null); render(ctx); },
    async indexRoleplaySemanticMemoryFromUi(ctx) { const source=ctx.source || 'studio'; const prefix=source === 'scene' ? 'roleplay-scene-memory' : 'roleplay-runtime-memory'; const payload={scope_id:getValue(`${prefix}-scope`,''), limit:Number(getValue(`${prefix}-index-limit`,500)), force:isChecked(`${prefix}-index-force`,false)}; const response=await fetch('/api/roleplay/retrieval/index-roleplay-memory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Vector indexing failed'); stateOf(ctx).roleplayRetrieval=result.retrieval || await loadJson(ctx,'/api/roleplay/retrieval/state',stateOf(ctx).roleplayRetrieval || null); render(ctx); },
    async runRoleplayRuntimeRetrievalLaneFromUi(ctx) { const source=ctx.source || 'studio'; const prefix=source === 'scene' ? 'roleplay-scene-memory' : 'roleplay-runtime-memory'; const payload={query:getValue(`${prefix}-query`,''), scope_id:getValue(`${prefix}-scope`,''), memory_types:getValue(`${prefix}-types`,'entities,memory_fragments,shared_memories,continuity,turn_summaries,story_checkpoints'), limit:Number(getValue(`${prefix}-limit`,12)), candidate_limit:Number(getValue(`${prefix}-candidate-limit`,36)), mode:getValue(`${prefix}-mode`,'hybrid'), rerank:isChecked(`${prefix}-rerank`,true), rebuild_search:isChecked(`${prefix}-rebuild-search`,false), source}; const response=await fetch('/api/roleplay/runtime-retrieval/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Runtime retrieval failed'); const search=result.search || result; if(source === 'scene') stateOf(ctx).roleplaySceneRetrievalSearch=search; else stateOf(ctx).roleplayRuntimeRetrievalSearch=search; stateOf(ctx).roleplayRetrievalSearch=search; stateOf(ctx).roleplayRuntimeRetrieval=result.runtime_retrieval || await loadJson(ctx,'/api/roleplay/runtime-retrieval/state',stateOf(ctx).roleplayRuntimeRetrieval || null); stateOf(ctx).roleplayRetrieval=stateOf(ctx).roleplayRuntimeRetrieval?.retrieval || await loadJson(ctx,'/api/roleplay/retrieval/state',stateOf(ctx).roleplayRetrieval || null); render(ctx); },
    async searchRoleplaySemanticRetrievalFromUi(ctx) { const source=ctx.source || 'studio'; const prefix=source === 'scene' ? 'roleplay-scene-memory' : 'roleplay-runtime-memory'; const payload={query:getValue(`${prefix}-query`,''), scope_id:getValue(`${prefix}-scope`,''), limit:Number(getValue(`${prefix}-limit`,12)), rerank:isChecked(`${prefix}-rerank`,false), fallback_keyword:true, source}; const response=await fetch('/api/roleplay/retrieval/search-semantic',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Semantic retrieval search failed'); if(source === 'scene') stateOf(ctx).roleplaySceneRetrievalSearch=result.search || result; else stateOf(ctx).roleplayRetrievalSearch=result.search || result; stateOf(ctx).roleplaySemanticRetrievalSearch=result.search || result; stateOf(ctx).roleplayRetrieval=result.retrieval || await loadJson(ctx,'/api/roleplay/retrieval/state',stateOf(ctx).roleplayRetrieval || null); render(ctx); },
    async searchRoleplayRetrievalFoundationFromUi(ctx) { const source=ctx.source || 'studio'; const prefix=source === 'scene' ? 'roleplay-scene-memory' : 'roleplay-runtime-memory'; const payload={query:getValue(`${prefix}-query`,''), scope_id:getValue(`${prefix}-scope`,''), memory_types:getValue(`${prefix}-types`,'entities,memory_fragments,shared_memories,continuity,turn_summaries,story_checkpoints'), limit:Number(getValue(`${prefix}-limit`,12)), source}; const response=await fetch('/api/roleplay/retrieval/search-foundation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Retrieval setup search failed'); if(source === 'scene') stateOf(ctx).roleplaySceneRetrievalSearch=result.search || result; else stateOf(ctx).roleplayRetrievalSearch=result.search || result; stateOf(ctx).roleplayRetrieval=result.retrieval || await loadJson(ctx,'/api/roleplay/retrieval/state',stateOf(ctx).roleplayRetrieval || null); render(ctx); },
    async reloadRoleplayScopeBuildState(ctx) { const s=stateOf(ctx); s.roleplayScopeBuild = await loadJson(ctx, '/api/roleplay/scope-build/state', s.roleplayScopeBuild || null); render(ctx); },
    async previewRoleplayScopeBuildFromUi(ctx) { const s=stateOf(ctx); setScopeSelection(ctx); const response = await fetch('/api/roleplay/scope-build/preview', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(scopePayload(ctx)) }); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Scope build preview failed'); s.roleplayScopeBuildPreview=result; s.roleplayScopeBuild=await loadJson(ctx, '/api/roleplay/scope-build/state', s.roleplayScopeBuild || null); render(ctx); },
    async buildRoleplayScopeMemoryRuntimeFromUi(ctx) { const s=stateOf(ctx); const runtimeOnly=!!ctx.runtimeOnly; setScopeSelection(ctx); const payload=scopePayload(ctx); if(!payload.scope_id) throw new Error('Choose a scope record first.'); if(runtimeOnly){ payload.compile_memory=false; payload.rebuild_search=false; payload.index_after=false; payload.mirror_after=false; payload.build_runtime=true; } payload.preview=s.roleplayScopeBuildPreview || null; s.roleplayScopeBuildProgress={status:'running', label:runtimeOnly?'Rebuilding runtime bundle…':'Building memory + runtime…', detail:runtimeOnly?'Using the selected scope and existing compiled memory.':'Compiling linked records, rebuilding search docs, indexing vectors, then writing runtime bundle.', step:runtimeOnly?'runtime_bundle':'compile_memory', started_at:new Date().toISOString()}; render(ctx); const response=await fetch('/api/roleplay/scope-build/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok || result.status==='error') throw new Error(result.error || result.detail || 'Scope build failed'); s.roleplayScopeBuildLastRun=result; s.roleplayScopeBuildPreview=result.preview || s.roleplayScopeBuildPreview; s.roleplayScopeBuild=result.state || await loadJson(ctx,'/api/roleplay/scope-build/state',s.roleplayScopeBuild||null); s.roleplayScopedCompile=await loadJson(ctx,'/api/roleplay/scoped-compile/state',s.roleplayScopedCompile||null); s.roleplayRuntime=await loadJson(ctx,'/api/roleplay/runtime/bundles',s.roleplayRuntime||null); s.roleplayRuntimePresets=await loadJson(ctx,'/api/roleplay/runtime-presets/state',s.roleplayRuntimePresets||null); s.roleplayMemory=await loadJson(ctx,'/api/roleplay/memory/state',s.roleplayMemory||null); s.roleplayRuntimeRetrieval=await loadJson(ctx,'/api/roleplay/runtime-retrieval/state',s.roleplayRuntimeRetrieval||null); const summary=result.summary||{}; s.roleplayScopeBuildProgress={status:'built', label:'Memory + Runtime build complete', detail:`Linked ${summary.linked_record_count ?? 0}, compiled ${summary.compiled_count ?? 0}, fragments ${summary.fragment_count ?? 0}, runtime ${summary.runtime_bundle_id || 'skipped'}.`, step:'done', finished_at:new Date().toISOString()}; render(ctx); },
    async reloadRoleplayRuntimePresets(ctx) { const s=stateOf(ctx); s.roleplayRuntimePresets=await loadJson(ctx,'/api/roleplay/runtime-presets/state',s.roleplayRuntimePresets||null); s.roleplayRuntime=await loadJson(ctx,'/api/roleplay/runtime/bundles',s.roleplayRuntime||null); s.roleplayScenePacketBuilder=await loadJson(ctx,'/api/roleplay/scene-packet/state',s.roleplayScenePacketBuilder||null); render(ctx); },
    setRoleplayRuntimePresetFromUi(ctx) { const s=stateOf(ctx); s.roleplayRuntimePresetSelected=getValue('roleplay-runtime-preset-select',''); s.roleplayRuntimePreparedPacket=null; render(ctx); },
    async prepareRoleplayPresetScenePacketFromUi(ctx) { const s=stateOf(ctx); const payload=runtimePacketPayload(ctx); s.roleplayRuntimePacketProgress={status:'running',label:'Previewing scene packet…',detail:'Resolving preset, scenario, cast, linked records, and retrieval query.',step:'prepare_packet',started_at:new Date().toISOString()}; render(ctx); const {response,result}=await fetchJsonWithTimeout('/api/roleplay/runtime-presets/prepare-scene-packet',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},30000); if(!response.ok || result.status==='error') throw new Error(result.error || result.detail || 'Prepare packet failed'); s.roleplayRuntimePreparedPacket=result; const packet=result.packet_payload || {}; s.roleplayRuntimePacketProgress={status:'ready',label:'Scene packet preview ready',detail:`Prepared ${packet.title || 'auto packet'} with scope ${packet.scope_id || 'global'}.`,step:'preview_ready',finished_at:new Date().toISOString()}; render(ctx); },
    async buildRoleplayPresetScenePacketFromUi(ctx) { const s=stateOf(ctx); const payload=runtimePacketPayload(ctx); s.roleplayRuntimePacketProgress={status:'running',label:'Building scene packet…',detail:'Saving packet payload and binding the runtime preset for Scene Chat.',step:'build_packet',started_at:new Date().toISOString()}; render(ctx); let result; if(payload.run_retrieval){ const started=await fetchJsonWithTimeout('/api/roleplay/runtime-presets/build-scene-packet-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},30000); if(!started.response.ok || started.result.status==='error') throw new Error(started.result.error || started.result.detail || 'Build packet job failed'); const jobId=started.result.job?.job_id || started.result.job_id; if(!jobId) throw new Error('Build packet job did not return a job id'); s.roleplayRuntimePacketProgress={status:'running',label:'Building scene packet…',detail:`Background retrieval/rerank job started: ${jobId}`,step:'background_job',job_id:jobId,started_at:new Date().toISOString()}; render(ctx); result=await pollPacketJob(ctx,jobId); } else { const direct=await fetchJsonWithTimeout('/api/roleplay/runtime-presets/build-scene-packet',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},30000); if(!direct.response.ok || direct.result.status==='error') throw new Error(direct.result.error || direct.result.detail || 'Build packet failed'); result=direct.result; } if(!result || result.status==='error') throw new Error(result?.error || result?.detail || 'Build packet failed'); s.roleplayScenePacket=result.scene_packet || null; s.roleplayScenePacketBuilder=result.scene_packet_builder || await loadJson(ctx,'/api/roleplay/scene-packet/state',s.roleplayScenePacketBuilder||null); s.roleplayRuntimePresets=result.runtime_presets || await loadJson(ctx,'/api/roleplay/runtime-presets/state',s.roleplayRuntimePresets||null); s.roleplayRuntimePreparedPacket=result.prepared || null; const packet=s.roleplayScenePacket || {}; s.roleplayRuntimePacketProgress={status:'built',label:'Scene packet built',detail:`Packet ${packet.packet_id || packet.id || 'saved'} is ready. Next: Open Scene Chat.`,step:'done',finished_at:new Date().toISOString()}; render(ctx); },
    openRoleplaySceneChatFromRuntime(ctx) { if (typeof window.setActiveSubtab === 'function') window.setActiveSubtab('roleplay', 'scene'); else { stateOf(ctx).activeSubtabId = 'scene'; render(ctx); } },
    async refreshRoleplaySceneStateFromUi(ctx) { const s=stateOf(ctx); s.roleplayScene=await loadJson(ctx,'/api/roleplay/scene/state',s.roleplayScene||null); render(ctx); },
    async saveRoleplaySceneSetupFromUi(ctx) { await saveSceneSetup(ctx, ctx.options || {}); },
    stopRoleplaySceneStreamFromUi(ctx) { const s=stateOf(ctx); if(s.roleplaySceneAbortController) s.roleplaySceneAbortController.abort(); s.roleplaySceneAbortController=null; s.roleplaySceneStreaming=false; syncSceneControls(ctx); },
    async resetRoleplaySceneTranscriptFromUi(ctx) { const s=stateOf(ctx); if(s.roleplaySceneAbortController) s.roleplaySceneAbortController.abort(); s.roleplaySceneAbortController=null; s.roleplaySceneStreaming=false; syncSceneControls(ctx); if(!window.confirm('Reset the current Scene transcript?')) return; const response=await fetch('/api/roleplay/scene/transcript/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene_id:s.roleplayScene?.scene_id || 'default'})}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Scene transcript reset failed'); s.roleplayScene=result.scene || await loadJson(ctx,'/api/roleplay/scene/state',null); render(ctx); },
    async roleplaySaveSceneSessionSetup(ctx) { await saveSceneSessionSetup(ctx); },
    async roleplayStartSceneContinuationSession(ctx) { await startSceneContinuationSession(ctx); },
    async appendRoleplaySceneTranscriptPlaceholderFromUi(ctx) { await appendTranscriptPlaceholder(ctx); },
    async continueRoleplayScenePlaceholderFromUi(ctx) { await continueTranscriptPlaceholder(ctx); },
    async createRoleplaySceneCheckpointFromUi(ctx) { await createSceneCheckpoint(ctx); },
    async refreshRoleplaySceneStateInspector(ctx) { const st=stateOf(ctx); st.roleplayScene=await loadJson(ctx,'/api/roleplay/scene/state',st.roleplayScene||null); st.roleplayStories=await loadJson(ctx,'/api/roleplay/stories/state',st.roleplayStories||null); st.roleplayStoryRestore=await loadJson(ctx,'/api/roleplay/story-checkpoint-restore/state',st.roleplayStoryRestore||null); st.roleplaySceneDirectorStatus=await loadJson(ctx,'/api/roleplay/scene-director/status',st.roleplaySceneDirectorStatus||null); st.roleplaySceneDirectorTraces=await loadJson(ctx,'/api/roleplay/scene-director/traces?limit=20',st.roleplaySceneDirectorTraces||null); render(ctx); },
    selectRoleplayCheckpointForDiff(ctx) { const st=stateOf(ctx); const clean=String(ctx.checkpointId || '').trim(); if(!clean) return; st.roleplayDiffLeftCheckpointId=clean; const left=document.getElementById('roleplay-diff-left'); const right=document.getElementById('roleplay-diff-right'); if(left && !left.value) left.value=clean; else if(right && !right.value) right.value=clean; else if(left) left.value=clean; render(ctx); },
    async compareRoleplayCheckpointsFromUi(ctx) { const st=stateOf(ctx); const left=getValue('roleplay-diff-left', st.roleplayDiffLeftCheckpointId || ''); const right=getValue('roleplay-diff-right', ''); if(!left || !right) throw new Error('Choose two checkpoint IDs to compare.'); const response=await fetch('/api/roleplay/story-checkpoint/diff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({left_checkpoint_id:left,right_checkpoint_id:right})}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Checkpoint diff failed'); st.roleplayCheckpointDiff=result; render(ctx); },
    async branchRoleplayCheckpointFromUi(ctx) { const st=stateOf(ctx); const clean=String(ctx.checkpointId || '').trim(); if(!clean) throw new Error('Choose a checkpoint to branch from.'); const title=getValue('roleplay-branch-title', `Branch from ${clean}`); const branchType=getValue('roleplay-branch-type','alternate'); const response=await fetch('/api/roleplay/story-checkpoint/branch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({checkpoint_id:clean,title,branch_type:branchType})}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Checkpoint branch failed'); st.roleplayStories=result.stories || await loadJson(ctx,'/api/roleplay/stories/state',st.roleplayStories||null); st.roleplayStoryRestore=await loadJson(ctx,'/api/roleplay/story-checkpoint-restore/state',st.roleplayStoryRestore||null); st.roleplayStoriesActiveView='workspace'; render(ctx); },
    async restoreRoleplayStoryToSceneFromUi(ctx) { const st=stateOf(ctx); const type=ctx.type || 'checkpoint'; const cleanId=String(ctx.id || '').trim(); if(!cleanId) throw new Error('No checkpoint/session selected to restore.'); const endpoint=type==='checkpoint'?'/api/roleplay/story-checkpoint/restore-snapshot':'/api/roleplay/story-resume'; const payload={scene_id:st.roleplayScene?.scene_id || 'default', restore_mode:'replace_scene'}; if(type==='checkpoint') payload.checkpoint_id=cleanId; else payload.session_id=cleanId; const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.message || result.error || 'Story restore failed'); st.roleplayScene=result.scene || await loadJson(ctx,'/api/roleplay/scene/state',st.roleplayScene||null); st.roleplayStories=result.stories || await loadJson(ctx,'/api/roleplay/stories/state',st.roleplayStories||null); st.roleplayStoryRestore=await loadJson(ctx,'/api/roleplay/story-checkpoint-restore/state',st.roleplayStoryRestore||null); st.roleplayStoriesActiveView='workspace'; st.activeSubtab='scene'; render(ctx); },
    async resumeRoleplayStoryFromSelection(ctx) { const st=stateOf(ctx); const stories=st.roleplayStories || {}; const restore=stories.workspace?.restore_target || {}; const checkpointId=restore.checkpoint_id || stories.active_checkpoint_id || ''; const sessionId=restore.session_id || stories.active_session_id || ''; await actions.restoreRoleplayStoryToSceneFromUi({ ...ctx, type: checkpointId ? 'checkpoint' : 'session', id: checkpointId || sessionId }); },
    async executeRoleplaySceneTurnStreamFromUi(ctx) { await executeSceneTurnStream(ctx, !!ctx.continueScene); },
    async executeRoleplaySceneTurnFromUi(ctx) { await executeSceneTurnNonStream(ctx, !!ctx.continueScene); },
    async refreshRoleplaySceneDirectorStatus(ctx) { const s=stateOf(ctx); s.roleplaySceneDirectorStatus = await loadJson(ctx, '/api/roleplay/scene-director/status', s.roleplaySceneDirectorStatus || null); render(ctx); },
    async runRoleplaySceneDirectorPreflight(ctx) { const s=stateOf(ctx); const payload = normalizeSceneDirectorPayload(ctx); s.roleplaySceneDirectorProgress = { status: 'running', label: 'Running Scene Director preflight…', detail: 'Control Center is preparing a compact director brief from packet, role boundaries, and scoped memory.', started_at: new Date().toISOString() }; render(ctx); const { response, result } = await fetchJsonWithTimeout('/api/roleplay/scene-director/preflight', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 45000); if (!response.ok || result.status === 'error') throw new Error(result.error || result.detail || 'Scene Director preflight failed'); s.roleplaySceneDirectorPreflight = result; s.roleplaySceneDirectorProgress = { status: 'ready', label: 'Scene Director preflight ready', detail: `Contract ${result.contract_id || result.prompt_contract_id || 'selected'} · context ${result.context_item_count ?? result.context_items?.length ?? 'n/a'}`, finished_at: new Date().toISOString() }; s.roleplaySceneDirectorStatus = await loadJson(ctx, '/api/roleplay/scene-director/status', s.roleplaySceneDirectorStatus || null); render(ctx); },
    async validateRoleplaySceneDirectorLastResponse(ctx) { const s=stateOf(ctx); const transcript = s.roleplayScene?.transcript || s.roleplayScene?.turns || []; const lastAssistant = Array.isArray(transcript) ? [...transcript].reverse().find((item)=>String(item.role || '').toLowerCase() === 'assistant') : null; const payload = { ...normalizeSceneDirectorPayload(ctx), response_text: getValue('roleplay-scene-director-validation-text', lastAssistant?.content || lastAssistant?.text || '') }; const { response, result } = await fetchJsonWithTimeout('/api/roleplay/scene-director/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 30000); if (!response.ok || result.status === 'error') throw new Error(result.error || result.detail || 'Scene Director validation failed'); s.roleplaySceneDirectorValidation = result; render(ctx); },
    async refreshRoleplaySceneDirectorTraces(ctx) { const s=stateOf(ctx); s.roleplaySceneDirectorTraces = await loadJson(ctx, '/api/roleplay/scene-director/traces?limit=20', s.roleplaySceneDirectorTraces || null); render(ctx); },
    async reloadRoleplayProvenanceState(ctx) { const s=stateOf(ctx); s.roleplayProvenance = await loadJson(ctx, '/api/roleplay/provenance/state', s.roleplayProvenance || null); s.roleplayProvenanceGraph = await loadJson(ctx, '/api/roleplay/provenance/graph?limit=160', s.roleplayProvenanceGraph || null); render(ctx); },
    async refreshRoleplayProvenanceGraph(ctx) { const s=stateOf(ctx); const scope=getValue('roleplay-provenance-scope',''); const nodeType=getValue('roleplay-provenance-node-type',''); const limit=getValue('roleplay-provenance-limit','250'); const params=new URLSearchParams({ scope_id: scope, node_type: nodeType, limit }); s.roleplayProvenanceGraph = await loadJson(ctx, `/api/roleplay/provenance/graph?${params.toString()}`, s.roleplayProvenanceGraph || null); s.roleplayProvenanceTrace = null; render(ctx); },
    async traceRoleplayProvenanceNode(ctx) { const s=stateOf(ctx); const nodeId=ctx.nodeId || ctx.id || ''; if(!nodeId) throw new Error('No provenance node selected.'); const response=await fetch('/api/roleplay/provenance/trace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ node_id: nodeId })}); const result=await response.json().catch(()=>({})); if(!response.ok) throw new Error(result.detail || result.error || 'Provenance trace failed'); s.roleplayProvenanceTrace=result; render(ctx); },
    roleplayProvenanceZoom(ctx) { const s=stateOf(ctx); const vb=provenanceViewBox(ctx); const factor=Number(ctx.factor || 1); const cx=vb.x + vb.w / 2; const cy=vb.y + vb.h / 2; const nextW=Math.max(420, Math.min(3600, vb.w * factor)); const nextH=Math.max(300, Math.min(2600, vb.h * factor)); s.roleplayProvenanceView={x:cx-nextW/2,y:cy-nextH/2,w:nextW,h:nextH}; render(ctx); },
    roleplayProvenancePan(ctx) { const s=stateOf(ctx); const vb=provenanceViewBox(ctx); s.roleplayProvenanceView={...vb,x:vb.x + Number(ctx.dx || 0), y:vb.y + Number(ctx.dy || 0)}; render(ctx); },
    roleplayProvenanceResetView(ctx) { stateOf(ctx).roleplayProvenanceView = null; render(ctx); },
  };


  Object.assign(actions, {
    'action.roleplay.forge.import_choose_file': actions.roleplayForgeImportChooseFile,
    'action.roleplay.forge.import_update_option': actions.roleplayForgeImportUpdateOption,
    'action.roleplay.forge.import_clear': actions.roleplayForgeImportClear,
    'action.roleplay.forge.scope_set_mode': actions.roleplayForgeSetScopeMode,
    'action.roleplay.forge.scope_search': actions.roleplayForgeSetScopeSearch,
    'action.roleplay.forge.scope_use_record': actions.roleplayForgeUseRecordAsScope,
  });

  const renderers = {
    roleplaySceneStatusHtml(ctx) { const s=stateOf(ctx); const scene=s.roleplayScene || {}; return `<section class="neo-ui-card roleplay-module-status-card"><strong>Roleplay Scene module</strong>${badgeRow(ctx,[scene.scene_id || 'default', scene.scene_memory_injection?.status || 'packet pending', scene.chat?.execution_enabled ? 'chat live' : 'chat ready'])}${metaList(ctx,[`Runtime: ${scene.setup?.runtime_bundle_id || 'none'}`, `Packet: ${scene.setup?.scene_packet_id || scene.scene_memory_injection?.scene_packet_id || 'none'}`, `Backend: ${scene.text_backend?.active_profile_id || 'default'}`])}</section>`; },
    roleplayRuntimeStatusHtml(ctx) { const s=stateOf(ctx); const presets=s.roleplayRuntimePresets || {}; const progress=s.roleplayRuntimePacketProgress || {}; return `<section class="neo-ui-card roleplay-module-status-card"><strong>Roleplay Runtime module</strong>${badgeRow(ctx,[`presets ${(presets.presets || []).length}`, progress.status || 'idle', s.roleplayRuntimePresetSelected || presets.latest_preset?.preset_id || 'no preset selected'])}</section>`; },
    roleplayCompileStatusHtml(ctx) { const s=stateOf(ctx); const scope=s.roleplayScopeBuild || {}; const progress=s.roleplayScopeBuildProgress || {}; return `<section class="neo-ui-card roleplay-module-status-card"><strong>Roleplay Compile module</strong>${badgeRow(ctx,[scope.status || 'ready', progress.status || 'idle', `${(scope.scopes || []).length || 0} scopes`])}</section>`; },
    roleplaySceneDirectorCockpitHtml: sceneDirectorCockpitHtml,
    roleplaySceneStateInspectorHtml: sceneStateInspectorHtml,
    roleplayCheckpointInspectorHtml: checkpointInspectorHtml,
    roleplayStoriesWorkspaceShellHtml: storiesWorkspaceShellHtml,
    roleplayStoriesArchiveHtml: storiesArchiveHtml,
    roleplayStoriesInspectorHtml: storiesInspectorHtml,
    roleplayProvenanceGraphHtml,
    roleplayStudioCompileDeepHtml: studioCompileDeepHtml,
    roleplayStudioRuntimeDeepHtml: runtimeDeepHtml,
    roleplayScopedCompileDeepHtml: scopedCompileHtml,
    roleplayScopeBuildDeepHtml: scopeBuildDeepHtml,
    roleplayForgeBuilderRailHtml: forgeBuilderRailModuleHtml,
    roleplayForgeBuilderHtml: forgeBuilderModuleHtml,
    roleplayForgeRecordsHtml: forgeRecordsModuleHtml,
    roleplayForgeInspectorHtml: forgeInspectorModuleHtml,
    roleplayForgeSqliteInspectorHtml: forgeSqliteInspectorHtml,
    roleplayForgeTemplateInspectorHtml: forgeTemplateInspectorHtml,
    roleplayForgeAdvancedImportHtml: forgeAdvancedImportHtml,
    roleplayForgeAdvancedScopeHtml: forgeScopePickerHtml,
    'render.roleplay_forge_advanced_import': forgeAdvancedImportHtml,
    'render.roleplay_forge_advanced_scope': forgeScopePickerHtml,
  };

  const api = {
    surfaceId: 'roleplay',
    releaseStage: 'ready',
    status: 'ready',
    migratedAreas: [
      'render.roleplay_scene_status',
      'render.roleplay_runtime_status',
      'render.roleplay_compile_status',
      'render.roleplay_scene_director_cockpit',
      'action.roleplay.scope_build.refresh',
      'action.roleplay.scope_build.preview',
      'action.roleplay.scope_build.build',
      'action.roleplay.runtime.refresh_presets',
      'action.roleplay.runtime.select_preset',
      'action.roleplay.runtime.preview_packet',
      'action.roleplay.runtime.build_packet',
      'action.roleplay.runtime.open_scene_chat',
      'action.roleplay.scene.refresh_state',
      'action.roleplay.scene.save_setup',
      'action.roleplay.scene.stop_stream',
      'action.roleplay.scene.reset_transcript',
      'action.roleplay.scene_director.status',
      'action.roleplay.scene_director.preflight',
      'action.roleplay.scene_director.validate',
      'action.roleplay.scene_director.traces',
      'action.roleplay.scene_chat.stream_turn',
      'action.roleplay.scene_chat.send_non_stream',
      'action.roleplay.scene.session_setup',
      'action.roleplay.scene.continuation_session',
      'action.roleplay.scene.transcript_placeholder',
      'action.roleplay.scene.continue_placeholder',
      'action.roleplay.scene.save_checkpoint',
      'render.roleplay_scene_state_inspector',
      'render.roleplay_checkpoint_inspector',
      'action.roleplay.scene_state_inspector.refresh',
      'action.roleplay.checkpoint.diff_select',
      'action.roleplay.checkpoint.diff_compare',
      'action.roleplay.checkpoint.branch',
      'action.roleplay.checkpoint.restore',
      'action.roleplay.checkpoint.resume_selected',
      'render.roleplay_stories_workspace',
      'action.roleplay.stories.refresh',
      'action.roleplay.stories.set_view',
      'action.roleplay.stories.set_archive_view',
      'action.roleplay.stories.set_inspector_view',
      'action.roleplay.stories.fill_from_scene',
      'action.roleplay.stories.clear_storyline_form',
      'action.roleplay.stories.clear_session_form',
      'action.roleplay.stories.create_storyline',
      'action.roleplay.stories.create_session',
      'render.roleplay_stories_archive',
      'render.roleplay_stories_inspector_provenance',
      'render.roleplay_provenance_graph',
      'action.roleplay.provenance.reload_state',
      'action.roleplay.provenance.refresh_graph',
      'action.roleplay.provenance.trace_node',
      'action.roleplay.provenance.zoom',
      'action.roleplay.provenance.pan',
      'action.roleplay.provenance.reset_view',
      'render.roleplay_studio_compile_deep',
      'render.roleplay_studio_runtime_deep',
      'render.roleplay_scoped_compile_deep',
      'render.roleplay_scope_build_deep',
      'action.roleplay.scoped_compile.refresh',
      'action.roleplay.scoped_compile.preview',
      'action.roleplay.scoped_compile.execute',
      'action.roleplay.runtime_retrieval.index_vectors',
      'action.roleplay.runtime_retrieval.run',
      'action.roleplay.runtime_retrieval.semantic_only',
      'action.roleplay.runtime_retrieval.keyword_only'
,
      'render.roleplay_forge_builder_rail',
      'render.roleplay_forge_builder',
      'render.roleplay_forge_records',
      'render.roleplay_forge_inspector',
      'action.roleplay.forge.refresh_state',
      'action.roleplay.forge.select_kind',
      'action.roleplay.forge.load_record',
      'action.roleplay.forge.reset_payload',
      'action.roleplay.forge.save_record',
      'action.roleplay.forge.delete_record',
      'action.roleplay.forge.import_preview',
      'action.roleplay.forge.import_run',
      'render.roleplay_forge_sqlite_inspector',
      'render.roleplay_forge_template_inspector',
      'action.roleplay.forge.refresh_sqlite_inspector',
      'action.roleplay.forge.refresh_template_inspector',
      'render.roleplay_forge_advanced_import',
      'render.roleplay_forge_advanced_scope',
      'action.roleplay.forge.import_choose_file',
      'action.roleplay.forge.import_update_option',
      'action.roleplay.forge.import_clear',
      'action.roleplay.forge.scope_set_mode',
      'action.roleplay.forge.scope_search',
      'action.roleplay.forge.scope_use_record',
    ],
    diagnostics: { status: 'partial', fallback: 'legacy neo.js wrappers remain as fallback for Scene Chat dispatch/transcript helpers', scene_director_cockpit: 'module-owned', scene_chat_dispatch: 'stream + non-stream', transcript_checkpoint_helpers: 'placeholder/continue/checkpoint', scene_state_checkpoint_inspector: 'state + checkpoint cockpit', stories_workspace: 'Stories workspace shell and create/session actions', archive_provenance: 'Archive + Provenance graph render/actions', compile_runtime_deep: 'Studio Compile/Runtime deep renderers and advanced retrieval handlers', forge_builder: 'Forge Builder rail/builder/records/inspector renderers and save/delete/import action handlers', forge_sqlite_template_inspector: 'Forge SQLite and Template Inspector lanes', forge_advanced_import_scope: 'advanced import and active scope picker lanes', risk: 'medium' },
    renderers,
    actions,
  };
  if (window.NeoSurfaceRuntime?.register) window.NeoSurfaceRuntime.register('roleplay', api);
  else { window.NeoSurfaceModules = window.NeoSurfaceModules || {}; window.NeoSurfaceModules.roleplay = api; }
})();
