(function () {
  'use strict';

  function escapeHtml(value) {
    return `${value ?? ''}`.replace(/[&<>\"]/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '\"': '&quot;',
    }[char]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, '&#39;');
  }

  function providerErrorSource(payload) {
    const detail = payload && payload.detail;
    return (detail && typeof detail === 'object') ? detail : (payload || {});
  }

  function providerErrorMessage(payload, fallback = 'Image provider request failed') {
    const source = providerErrorSource(payload);
    const message = source.message || source.detail || source.error || payload?.message || payload?.error || fallback;
    const actions = Array.isArray(source.recovery_actions) ? source.recovery_actions.filter(Boolean) : [];
    if (!actions.length) return String(message || fallback);
    return `${message}\n\nTry this:\n- ${actions.join('\n- ')}`;
  }

  function providerErrorTitle(payload, fallback = 'Image provider error') {
    const source = providerErrorSource(payload);
    return String(source.title || fallback);
  }

  function renderGenerationReadinessPreflight(readiness) {
    if (!readiness) return '';
    const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
    const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
    const checksList = Array.isArray(readiness.checks) ? readiness.checks : [];
    const detailMode = String(document.body?.dataset?.detailMode || '').toLowerCase();
    const expandedByDefault = detailMode === 'expert';
    const tone = blockers.length ? 'danger' : (warnings.length ? 'warning' : 'success');
    const icon = blockers.length ? '⛔' : (warnings.length ? '⚠️' : '✅');
    const primaryIssue = blockers[0] || warnings[0] || null;
    const summaryText = primaryIssue
      ? `${readiness.state_label} · ${primaryIssue.label}: ${primaryIssue.message}`
      : `${readiness.state_label} · ${readiness.workflow_label} is ready.`;
    const countParts = [];
    if (blockers.length) countParts.push(`${blockers.length} blocker${blockers.length === 1 ? '' : 's'}`);
    if (warnings.length) countParts.push(`${warnings.length} warning${warnings.length === 1 ? '' : 's'}`);
    if (!countParts.length) countParts.push('all checks clear');
    const checks = checksList.map((check) => {
      const status = String(check.status || 'muted');
      const checkTone = status === 'ready' ? 'success' : (status === 'blocked' ? 'danger' : (status === 'warning' ? 'warning' : 'muted'));
      const checkIcon = status === 'ready' ? '✅' : (status === 'blocked' ? '⛔' : (status === 'warning' ? '⚠️' : '•'));
      return `<li class="neo-image-preflight-check ${checkTone}" data-preflight-check="${escapeAttr(check.id)}"><span>${checkIcon}</span><strong>${escapeHtml(check.label)}</strong><small>${escapeHtml(check.message)}</small></li>`;
    }).join('');
    return `<details class="neo-image-preflight-card neo-image-preflight-compact ${tone}" data-testid="image-generation-preflight" data-preflight-ready="${readiness.ready ? 'true' : 'false'}" data-preflight-expanded-default="${expandedByDefault ? 'true' : 'false'}" ${expandedByDefault ? 'open' : ''}>
    <summary class="neo-image-preflight-summary"><span class="neo-image-preflight-icon">${icon}</span><div><strong>${escapeHtml(readiness.state_label)}</strong><small>${escapeHtml(summaryText)}</small></div><span class="neo-image-preflight-count">${escapeHtml(countParts.join(' · '))}</span><span class="neo-image-preflight-toggle">Details</span></summary>
    <div class="neo-image-preflight-details"><div class="neo-image-preflight-head"><span class="neo-image-preflight-icon">${icon}</span><div><strong>${escapeHtml(readiness.state_label)}</strong><small>${escapeHtml(readiness.workflow_label)} readiness check before Generate.</small></div></div>
    <ul>${checks}</ul></div>
  </details>`;
  }

  window.NeoImageUIHelpers = Object.freeze({
    escapeHtml,
    escapeAttr,
    providerErrorMessage,
    providerErrorTitle,
    renderGenerationReadinessPreflight,
  });
}());
