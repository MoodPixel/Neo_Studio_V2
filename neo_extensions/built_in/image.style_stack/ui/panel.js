(() => {
  const EXTENSION_ID = "style_stack";
  const API_BASE = "/api/extensions/style_stack/styles";
  const DEFAULT_STATE = {
    enabled: true,
    target: "both",
    search: "",
    selected_name: "",
    active_styles: [],
    manual_positive: "",
    manual_negative: "",
    editor: { name: "", prompt: "", negative_prompt: "" },
    records: [],
    payload_version: 1,
    last_payload_preview: null,
  };

  function cloneState(state = {}) {
    return {
      ...DEFAULT_STATE,
      ...state,
      editor: { ...DEFAULT_STATE.editor, ...(state.editor || {}) },
      active_styles: Array.isArray(state.active_styles) ? state.active_styles : [],
      records: Array.isArray(state.records) ? state.records : [],
      payload_version: Number(state.payload_version || DEFAULT_STATE.payload_version || 1),
      last_payload_preview: state.last_payload_preview || null,
    };
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }

  function panelState(panel) {
    if (!panel.__neoStyleStackState) panel.__neoStyleStackState = cloneState();
    return panel.__neoStyleStackState;
  }

  function setMessage(panel, message) {
    const node = panel.querySelector("[data-style-stack-message]");
    if (node) node.textContent = message || "Ready.";
  }

  function filteredRecords(state) {
    const query = String(state.search || "").trim().toLowerCase();
    if (!query) return state.records;
    return state.records.filter((style) => [style.name, style.prompt, style.negative_prompt].some((value) => String(value || "").toLowerCase().includes(query)));
  }

  function selectedStyle(state) {
    return state.records.find((style) => style.name === state.selected_name) || filteredRecords(state)[0] || null;
  }

  function cleanActiveStyles(state) {
    const available = new Set((state.records || []).map((style) => String(style.name || "")));
    const seen = new Set();
    return (state.active_styles || []).map((name) => String(name || "").trim()).filter((name) => {
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return available.size ? available.has(name) : true;
    });
  }

  function resolvedStyleRecords(state) {
    const activeStyles = cleanActiveStyles(state);
    const byName = new Map((state.records || []).map((style) => [String(style.name || ""), style]));
    return activeStyles.map((name) => {
      const style = byName.get(name) || { name, prompt: "", negative_prompt: "" };
      return { name: String(style.name || name), prompt: String(style.prompt || ""), negative_prompt: String(style.negative_prompt || "") };
    });
  }

  function replayPayload(state) {
    return {
      enabled: Boolean(state.enabled),
      target: state.target || "both",
      active_styles: cleanActiveStyles(state),
      manual_positive: String(state.manual_positive || ""),
      manual_negative: String(state.manual_negative || ""),
      selected_name: String(state.selected_name || ""),
    };
  }

  function buildPayload(state) {
    const activeStyles = cleanActiveStyles(state);
    const active = Boolean(state.enabled && (activeStyles.length || state.manual_positive || state.manual_negative));
    const payload = {
      extensions: {
        [EXTENSION_ID]: {
          enabled: active,
          version: Number(state.payload_version || 1),
          inputs: active ? {
            active_styles: activeStyles,
            manual_positive: state.manual_positive || "",
            manual_negative: state.manual_negative || "",
          } : {},
          params: active ? { target: state.target || "both" } : {},
          assets: active ? { style_names: activeStyles, style_records: resolvedStyleRecords(state) } : {},
          metadata: {
            source: "image.generations.prompt.style_stack",
            merge_policy: "append_deduped",
            prompt_only: true,
            ui_phase: "F-frontend-state-payload-builder",
            runtime_merge_status: "deferred_to_phase_g",
            payload_builder: "neo_extensions.built_in.image.style_stack.ui.panel:buildPayload",
            library_count: Number(state.records?.length || 0),
            replay_payload: replayPayload(state),
          },
        },
      },
    };
    state.last_payload_preview = payload.extensions[EXTENSION_ID];
    return payload;
  }

  function renderPanel(panel) {
    const state = panelState(panel);
    const records = filteredRecords(state);
    const selected = selectedStyle(state);
    const select = panel.querySelector("[data-style-stack-select]");
    if (select) {
      select.innerHTML = records.length
        ? records.map((style) => `<option value="${escapeHtml(style.name)}" ${style.name === (state.selected_name || selected?.name) ? "selected" : ""}>${escapeHtml(style.name)}</option>`).join("")
        : '<option value="">No styles found</option>';
    }
    const count = panel.querySelector("[data-style-stack-count]");
    if (count) count.textContent = `${state.records.length} styles`;
    const chips = panel.querySelector("[data-style-stack-active-chips]");
    if (chips) {
      chips.innerHTML = state.active_styles.length
        ? state.active_styles.map((name) => `<span class="neo-style-stack-chip"><span>${escapeHtml(name)}</span><button type="button" data-style-stack-remove="${escapeHtml(name)}">×</button></span>`).join("")
        : '<span class="neo-muted">No active style chips yet.</span>';
    }
    const editor = state.editor.name ? state.editor : (selected || state.editor);
    ["name", "prompt", "negative_prompt"].forEach((field) => {
      const node = panel.querySelector(`[data-style-stack-editor="${field}"]`);
      if (node && node.value !== String(editor?.[field] || "")) node.value = editor?.[field] || "";
    });
    const preview = panel.querySelector("[data-style-stack-payload-preview]");
    if (preview) preview.textContent = JSON.stringify(buildPayload(state), null, 2);
  }

  async function loadStyles(panel) {
    setMessage(panel, "Loading Style Stack library…");
    const response = await fetch(API_BASE);
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.detail || result.message || "Style load failed");
    const state = panelState(panel);
    state.records = Array.isArray(result.styles) ? result.styles : [];
    state.selected_name = state.selected_name || state.records[0]?.name || "";
    const selected = selectedStyle(state);
    if (selected) state.editor = { name: selected.name || "", prompt: selected.prompt || "", negative_prompt: selected.negative_prompt || "" };
    setMessage(panel, `Loaded ${state.records.length} style(s).${result.encoding ? ` Encoding: ${result.encoding}` : ""}`);
    renderPanel(panel);
  }

  async function saveStyle(panel) {
    const state = panelState(panel);
    const style = {
      name: panel.querySelector('[data-style-stack-editor="name"]')?.value || "",
      prompt: panel.querySelector('[data-style-stack-editor="prompt"]')?.value || "",
      negative_prompt: panel.querySelector('[data-style-stack-editor="negative_prompt"]')?.value || "",
    };
    if (!style.name.trim()) throw new Error("Style name is required");
    const response = await fetch(`${API_BASE}/save`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ style }) });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.detail || result.message || "Style save failed");
    state.records = Array.isArray(result.styles) ? result.styles : [];
    state.selected_name = style.name;
    state.editor = style;
    setMessage(panel, `Saved ${style.name}.`);
    renderPanel(panel);
  }

  async function deleteStyle(panel) {
    const state = panelState(panel);
    const name = state.selected_name || state.editor.name;
    if (!name) return;
    const response = await fetch(`${API_BASE}/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.detail || result.message || "Style delete failed");
    state.records = Array.isArray(result.styles) ? result.styles : [];
    state.active_styles = state.active_styles.filter((item) => item !== name);
    state.selected_name = state.records[0]?.name || "";
    state.editor = selectedStyle(state) || { name: "", prompt: "", negative_prompt: "" };
    setMessage(panel, `Deleted ${name}.`);
    renderPanel(panel);
  }

  async function duplicateStyle(panel) {
    const state = panelState(panel);
    const name = state.selected_name || state.editor.name;
    if (!name) return;
    const response = await fetch(`${API_BASE}/duplicate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.detail || result.message || "Style duplicate failed");
    state.records = Array.isArray(result.styles) ? result.styles : [];
    const copy = state.records.find((style) => style.name.startsWith(`${name} Copy`)) || state.records[state.records.length - 1];
    if (copy) {
      state.selected_name = copy.name;
      state.editor = { name: copy.name || "", prompt: copy.prompt || "", negative_prompt: copy.negative_prompt || "" };
    }
    setMessage(panel, `Duplicated ${name}.`);
    renderPanel(panel);
  }

  async function importCsv(panel) {
    const file = panel.querySelector("[data-style-stack-import-file]")?.files?.[0];
    if (!file) throw new Error("Choose a CSV file first");
    const mode = panel.querySelector("[data-style-stack-import-mode]")?.value || "merge";
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_BASE}/import?mode=${encodeURIComponent(mode)}`, { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.detail || result.message || "Style import failed");
    const state = panelState(panel);
    state.records = Array.isArray(result.styles) ? result.styles : [];
    state.selected_name = state.records[0]?.name || "";
    state.editor = selectedStyle(state) || { name: "", prompt: "", negative_prompt: "" };
    setMessage(panel, result.message || `Imported ${state.records.length} style(s).`);
    renderPanel(panel);
  }

  function bindPanel(panel) {
    if (!panel || panel.dataset.initialized === "true") return false;
    panel.dataset.initialized = "true";
    panel.addEventListener("input", (event) => {
      const state = panelState(panel);
      const field = event.target.getAttribute?.("data-style-stack-field");
      const editorField = event.target.getAttribute?.("data-style-stack-editor");
      if (field) state[field] = event.target.type === "checkbox" ? Boolean(event.target.checked) : event.target.value;
      if (editorField) state.editor[editorField] = event.target.value;
      renderPanel(panel);
    });
    panel.addEventListener("change", (event) => {
      const state = panelState(panel);
      if (event.target.matches("[data-style-stack-select]")) {
        state.selected_name = event.target.value;
        const selected = selectedStyle(state);
        if (selected) state.editor = { name: selected.name || "", prompt: selected.prompt || "", negative_prompt: selected.negative_prompt || "" };
        renderPanel(panel);
      }
    });
    panel.addEventListener("click", async (event) => {
      const button = event.target.closest?.("[data-style-stack-action],[data-style-stack-remove]");
      if (!button) return;
      event.preventDefault();
      const state = panelState(panel);
      const action = button.getAttribute("data-style-stack-action");
      try {
        if (button.hasAttribute("data-style-stack-remove")) state.active_styles = state.active_styles.filter((name) => name !== button.getAttribute("data-style-stack-remove"));
        if (action === "refresh") await loadStyles(panel);
        if (action === "add-selected") {
          const name = state.selected_name || selectedStyle(state)?.name || "";
          if (name && !state.active_styles.includes(name)) state.active_styles.push(name);
        }
        if (action === "clear-active") state.active_styles = [];
        if (action === "save") await saveStyle(panel);
        if (action === "delete") await deleteStyle(panel);
        if (action === "duplicate") await duplicateStyle(panel);
        if (action === "import") await importCsv(panel);
        renderPanel(panel);
      } catch (error) {
        setMessage(panel, error.message || String(error));
      }
    });
    loadStyles(panel).catch((error) => setMessage(panel, error.message || String(error)));
    return true;
  }

  function initStyleStackPanel(root = document) {
    const panels = Array.from(root.querySelectorAll?.('[data-extension-id="style_stack"]') || []);
    return panels.map(bindPanel).some(Boolean);
  }

  window.NeoStyleStackExtension = Object.freeze({
    id: EXTENSION_ID,
    phase: "F",
    apiBase: API_BASE,
    buildPayload,
    initStyleStackPanel,
    status: "frontend_state_payload_builder",
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initStyleStackPanel(document));
  } else {
    initStyleStackPanel(document);
  }
})();
