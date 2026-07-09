(function () {
  'use strict';

  const SAFETY_COPY = Object.freeze({
    deletePrompt: 'Delete saved prompt? This removes the saved Prompt Library record only. Generated outputs, source text, project records, and Image prompts will not be deleted.',
    deleteCaption: 'Delete saved caption? This removes the saved Caption Library record only. Source image assets, generated outputs, and project records will not be deleted.',
    deleteComponent: 'Delete reusable caption component? This removes the component library record only. Saved captions and source images will not be deleted.',
    clearPromptOutput: 'Clear current Prompt Studio output fields? Saved prompts, Prompt Library records, project records, and Image prompts will stay safe.',
    clearCaptionOutput: 'Clear current Caption Studio output? Saved captions, source image assets, and project records will stay safe.',
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function displayModeSetup(detailMode) {
    const mode = detailMode === 'compact' ? 'compact' : (detailMode === 'expert' ? 'expert' : 'guided');
    return {
      mode,
      compact: mode === 'compact',
      guided: mode === 'guided',
      expert: mode === 'expert',
      helperVisible: mode !== 'compact',
      diagnosticsVisible: mode === 'expert',
      keepControlsVisible: true,
    };
  }

  function helperText(text, detailMode) {
    const display = displayModeSetup(detailMode);
    if (!display.helperVisible || !text) return '';
    return `<p class="neo-muted neo-pc-helper" data-display-mode="${escapeAttr(display.mode)}">${escapeHtml(text)}</p>`;
  }

  function expertDiagnostics(payload, detailMode) {
    const display = displayModeSetup(detailMode);
    if (!display.diagnosticsVisible) return '';
    return `<pre class="neo-pc-expert" data-display-mode="expert">${escapeHtml(JSON.stringify(payload || {}, null, 2))}</pre>`;
  }

  function displayModeSummary({ modeId, detailMode } = {}) {
    const display = displayModeSetup(detailMode);
    const modeLabelText = modeId === 'captioning' ? 'Captioning' : 'Prompt Builder';
    const helper = helperText('Compact hides helper/support text only. Guided shows readable labels and help. Expert adds provider IDs, route state, payload previews, and replay diagnostics.', detailMode);
    const expert = expertDiagnostics({
      surface_id: 'prompt_captioning',
      workspace_mode: modeId,
      display_mode: display.mode,
      compact_policy: 'hide helper text only; keep controls mounted',
      guided_policy: 'labels, normal warnings, useful helper text',
      expert_policy: 'provider IDs, route IDs, payload keys, metadata/replay diagnostics',
    }, detailMode);
    return `
      ${helper}
      <div class="neo-badge-row neo-pc-display-contract" data-display-mode="${escapeAttr(display.mode)}" data-workspace-mode="${escapeAttr(modeId)}">
        <span class="neo-badge">${escapeHtml(modeLabelText)}</span>
        <span class="neo-state-pill ${display.compact ? 'warning' : display.expert ? 'success' : ''}">${escapeHtml(display.mode)}</span>
        <span class="neo-badge">Controls stay visible</span>
        <span class="neo-badge">Helper text ${display.compact ? 'hidden' : 'shown'}</span>
        <span class="neo-badge">Diagnostics ${display.expert ? 'shown' : 'hidden'}</span>
      </div>
      ${expert}
    `;
  }

  function modeLabel(modeId = '') {
    return modeId === 'captioning' ? 'Caption' : 'Prompt';
  }

  function studioLabel(modeId = '') {
    return modeId === 'captioning' ? 'Caption Studio' : 'Prompt Studio';
  }

  function assistToolLabel({ toolId = '', modeId = '', childTabs = [] } = {}) {
    const match = Array.isArray(childTabs) ? childTabs.find((item) => item && item.id === toolId) : null;
    return match?.label || (modeId === 'captioning' ? 'Caption Assist Tool' : 'Prompt Assist Tool');
  }

  function modeStudioAssistSummary({ modeId = '', toolId = '', detailMode = '', childTabs = [], currentLeftSubtab = '' } = {}) {
    const mode = modeId === 'captioning' ? 'captioning' : 'prompt_builder';
    const activeTool = toolId || currentLeftSubtab;
    const modeLabelText = modeLabel(mode);
    const studioLabelText = studioLabel(mode);
    const assistLabelText = assistToolLabel({ toolId: activeTool, modeId: mode, childTabs });
    const helper = detailMode !== 'compact'
      ? '<p class="neo-muted neo-pc-navigation-helper">Mode chooses the writing lane. Assist Tool controls the left helper rail. Studio is the main editor on the right.</p>'
      : '';
    return `
      <div class="neo-pc-context-strip" data-testid="prompt-captioning-context-strip" data-mode="${escapeAttr(mode)}" data-assist-tool="${escapeAttr(activeTool)}">
        <span class="neo-context-pill"><strong>Mode</strong>${escapeHtml(modeLabelText)}</span>
        <span class="neo-context-pill"><strong>Assist Tool</strong>${escapeHtml(assistLabelText)}</span>
        <span class="neo-context-pill"><strong>Studio</strong>${escapeHtml(studioLabelText)}</span>
      </div>
      ${helper}
    `;
  }

  function confirmSafetyCopy(kind, confirmFn) {
    const message = SAFETY_COPY[kind] || 'Continue with this Prompt/Captioning action?';
    if (typeof confirmFn !== 'function') return true;
    return confirmFn(message);
  }

  function safetyMicrocopy(text) {
    return `<p class="neo-pc-safety-copy" data-pc-safety-copy="true">${escapeHtml(text)}</p>`;
  }

  function providerErrorPayload(data) {
    const root = data || {};
    const detail = root.detail && typeof root.detail === 'object' ? root.detail : {};
    const providerError = root.provider_error && typeof root.provider_error === 'object' ? root.provider_error : {};
    const nestedProvider = detail.provider_error && typeof detail.provider_error === 'object' ? detail.provider_error : {};
    return Object.keys(providerError).length ? providerError : (Object.keys(nestedProvider).length ? nestedProvider : detail);
  }

  function runErrorMessage(data, fallback) {
    const root = data || {};
    const provider = providerErrorPayload(root);
    const errors = Array.isArray(root.errors) ? root.errors.filter(Boolean).join('\n') : '';
    const detailErrors = Array.isArray((root.detail || {}).errors) ? (root.detail || {}).errors.filter(Boolean).join('\n') : '';
    const title = String(provider.title || '').trim();
    const message = String(provider.message || '').trim();
    const detail = String(provider.detail || provider.raw_detail || '').trim();
    const actions = Array.isArray(provider.recovery_actions) && provider.recovery_actions.length
      ? `\n${provider.recovery_actions.map((item) => `• ${item}`).join('\n')}`
      : '';
    if (title || message || detail) return `${title || fallback}${message ? `: ${message}` : ''}${detail && detail !== message ? `\n${detail}` : ''}${actions}`;
    if (errors || detailErrors) return errors || detailErrors;
    const primitiveProvider = typeof root.provider_error === 'string' ? root.provider_error : '';
    const primitiveDetail = typeof root.detail === 'string' ? root.detail : '';
    return primitiveProvider || root.error || primitiveDetail || root.message || fallback;
  }

  function keywordDiagnosticLabel(status = '') {
    const labels = {
      applied: 'Manual path applied',
      found_no_markdown_keywords: 'Manual path found, no keyword .md files',
      not_found: 'Manual path not found',
      request_failed: 'Keyword request failed',
      not_set: 'Using default scan paths',
    };
    return labels[String(status || 'not_set')] || String(status || 'Unknown');
  }

  function keywordDiagnosticsPanel({ library = {}, records = [] } = {}) {
    const diagnostics = library.keywordDiagnostics || {};
    const fileCount = Number(diagnostics.loaded_file_count ?? library.keywordLibraryFileCount ?? 0);
    const totalCount = Number(diagnostics.loaded_keyword_count ?? library.keywordTotal ?? records.length ?? 0);
    const visibleCount = Number(diagnostics.visible_keyword_count ?? records.length ?? 0);
    const existingRoots = Array.isArray(diagnostics.existing_roots) ? diagnostics.existing_roots : (library.keywordLibraryExistingRoots || []);
    const scannedRoots = Array.isArray(diagnostics.library_roots) ? diagnostics.library_roots : (library.keywordLibraryRoots || []);
    const sampleFiles = Array.isArray(diagnostics.sample_files) ? diagnostics.sample_files : (library.keywordLibraryFiles || []);
    const activeRoot = diagnostics.active_root || library.keywordActiveLibraryRoot || existingRoots[0] || '';
    const manualStatus = diagnostics.manual_path_status || library.keywordManualPathStatus || 'not_set';
    const emptyReasonLabel = diagnostics.empty_reason_label || (library.keywordEmptyReason === 'filters_or_search'
      ? 'Keyword libraries are loaded, but current filters hide every match.'
      : library.keywordEmptyReason === 'no_markdown_keywords'
        ? 'No Markdown keyword libraries were found in the scanned roots.'
        : 'Keywords are loaded and visible.');
    const sampleList = sampleFiles.slice(0, 6).map((file) => `<li>${escapeHtml(String(file || ''))}</li>`).join('');
    return `<div class="neo-pc-keyword-diagnostics" data-testid="prompt-captioning-keyword-diagnostics">
      <div class="neo-pc-keyword-diagnostic-strip">
        <span><strong>${escapeHtml(String(fileCount))}</strong> Markdown file(s)</span>
        <span><strong>${escapeHtml(String(totalCount))}</strong> loaded keyword(s)</span>
        <span><strong>${escapeHtml(String(visibleCount))}</strong> visible after filters</span>
        <span>${escapeHtml(keywordDiagnosticLabel(manualStatus))}</span>
      </div>
      <div class="neo-pc-keyword-diagnostic-grid">
        <p><strong>Active root</strong><br><span>${escapeHtml(activeRoot || 'No active keyword root yet.')}</span></p>
        <p><strong>Existing roots</strong><br><span>${escapeHtml(existingRoots.length ? existingRoots.slice(0, 3).join(' · ') : 'None found.')}</span></p>
        <p><strong>Empty reason</strong><br><span>${escapeHtml(emptyReasonLabel)}</span></p>
        <p><strong>Scanned roots</strong><br><span>${escapeHtml(String(scannedRoots.length || 0))} root candidate(s)</span></p>
      </div>
      ${sampleList ? `<details class="neo-pc-keyword-diagnostic-files"><summary>Loaded keyword files</summary><ul>${sampleList}</ul></details>` : ''}
    </div>`;
  }

  window.NeoPromptCaptioningUIHelpers = Object.freeze({
    displayModeSetup,
    helperText,
    expertDiagnostics,
    displayModeSummary,
    modeLabel,
    studioLabel,
    assistToolLabel,
    modeStudioAssistSummary,
    confirmSafetyCopy,
    safetyMicrocopy,
    providerErrorPayload,
    runErrorMessage,
    keywordDiagnosticLabel,
    keywordDiagnosticsPanel,
    SAFETY_COPY,
  });
}());
