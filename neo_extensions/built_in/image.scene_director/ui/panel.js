// Scene Director Inspector + Debug UI contract renderer.
// Phase 26.10.8 keeps this renderer dependency-free and read-only so the core Neo bundle
// can adopt it later without changing the node contract or mutating generation state.
(function () {
  const ROOT_SELECTOR = '[data-extension-id="image.scene_director"]';

  function asObject(value) {
    if (!value) return {};
    if (typeof value === 'string') {
      try { return JSON.parse(value); } catch (_err) { return {}; }
    }
    return typeof value === 'object' ? value : {};
  }

  function getInspector(metadata) {
    const meta = asObject(metadata);
    return asObject(meta.inspector_debug_ui || (meta.v054 && meta.v054.inspector_debug_ui));
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function summarizeValue(value) {
    if (Array.isArray(value)) return value.map(summarizeValue).join(', ');
    if (value && typeof value === 'object') {
      const keys = Object.keys(value);
      if (!keys.length) return '';
      return keys.slice(0, 4).map((k) => `${k}: ${summarizeValue(value[k])}`).join(' · ');
    }
    return value == null ? '' : String(value);
  }

  function renderCards(counts) {
    return Object.entries(counts || {}).map(([key, value]) => (
      `<div class="neo-scene-inspector-card"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(key.replace(/_/g, ' '))}</span></div>`
    )).join('');
  }

  function renderTable(rows, maxRows) {
    const safeRows = Array.isArray(rows) ? rows.slice(0, maxRows || 20) : [];
    if (!safeRows.length) return '<p class="neo-scene-inspector-empty">No entries.</p>';
    const columns = Array.from(new Set(safeRows.flatMap((row) => Object.keys(row || {})))).slice(0, 10);
    const head = columns.map((c) => `<th>${escapeHtml(c.replace(/_/g, ' '))}</th>`).join('');
    const body = safeRows.map((row) => (
      `<tr>${columns.map((c) => `<td title="${escapeHtml(summarizeValue(row[c]))}">${escapeHtml(summarizeValue(row[c]))}</td>`).join('')}</tr>`
    )).join('');
    return `<div class="neo-scene-inspector-table-wrap"><table class="neo-scene-inspector-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function renderPanel(panel, inspector) {
    const tables = inspector.tables || {};
    const tableId = panel.table_id;
    const content = panel.panel_id === 'overview'
      ? `<div class="neo-scene-inspector-cards">${renderCards(inspector.counts || {})}</div>`
      : renderTable(tables[tableId] || [], 24);
    return `<details class="neo-scene-inspector-panel" ${panel.default_open ? 'open' : ''} data-panel-id="${escapeHtml(panel.panel_id)}">
      <summary><span>${escapeHtml(panel.title || panel.panel_id)}</span><small>${escapeHtml(panel.row_count == null ? '' : `${panel.row_count} rows`)}</small></summary>
      <div class="neo-scene-inspector-panel-body">${content}</div>
    </details>`;
  }

  function render(container, metadata) {
    const root = typeof container === 'string' ? document.querySelector(container) : container;
    if (!root) return false;
    const inspector = getInspector(metadata);
    if (!inspector || !inspector.schema) {
      root.innerHTML = '<section class="neo-scene-inspector"><p class="neo-scene-inspector-empty">No Scene Director inspector metadata available yet.</p></section>';
      return false;
    }
    const severity = inspector.blockers && inspector.blockers.length ? 'blocker' : ((inspector.counts || {}).warnings ? 'warning' : 'ok');
    root.innerHTML = `<section class="neo-scene-inspector" data-phase="${escapeHtml(inspector.phase)}" data-severity="${escapeHtml(severity)}">
      <header class="neo-scene-inspector-header">
        <div><h3>Scene Director Inspector</h3><p>${escapeHtml(inspector.runtime_mode || 'metadata inspector')}</p></div>
        <div class="neo-scene-inspector-status">${escapeHtml(severity)}</div>
      </header>
      <div class="neo-scene-inspector-panels">${(inspector.panels || []).map((panel) => renderPanel(panel, inspector)).join('')}</div>
    </section>`;
    return true;
  }

  window.NeoSceneDirectorInspector = {
    selector: ROOT_SELECTOR,
    getInspector,
    render,
    renderTable,
  };
})();
