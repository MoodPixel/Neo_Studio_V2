(() => {
  const EXTENSION_ID = "wildcards";
  const DEFAULT_STATE = {
    enabled: true,
    root: "",
    selected_token: "",
    target: "positive_prompt",
    auto_resolve: true,
    use_seed: true,
    preview_count: 3,
    queue_count: 3,
    search: "",
    editor: { token: "", values_text: "", extension: ".txt" },
    library: { entries: [], values: [], status: "", loading: false, loaded: false, count: 0, root: "" },
    variant_offset: 0,
    max_passes: 24,
    payload_version: 1,
    last_preview_results: [],
    last_payload_preview: null,
    phase: "H-prompt-extension-order",
    migration_state_status: "repaired_phase_g5",
  };

  function cloneWildcardsState(state = {}) {
    return {
      ...DEFAULT_STATE,
      ...state,
      target: state.target === "negative" ? "negative_prompt" : (state.target || DEFAULT_STATE.target),
      preview_count: Math.max(1, Math.min(Number(state.preview_count || DEFAULT_STATE.preview_count), 10)),
      queue_count: Math.max(1, Math.min(Number(state.queue_count || DEFAULT_STATE.queue_count), 50)),
      editor: { ...DEFAULT_STATE.editor, ...(state.editor || {}) },
      library: { ...DEFAULT_STATE.library, ...(state.library || {}), entries: Array.isArray(state.library?.entries) ? state.library.entries : [], values: Array.isArray(state.library?.values) ? state.library.values : [] },
      last_preview_results: Array.isArray(state.last_preview_results) ? state.last_preview_results : [],
    };
  }

  function tokenLabel(token = "") {
    const clean = String(token || "").trim().replace(/^__|__$/g, "").replace(/^\/+|\/+$/g, "");
    return clean ? `__${clean}__` : "";
  }

  window.NeoWildcardsExtension = {
    EXTENSION_ID,
    DEFAULT_STATE,
    cloneWildcardsState,
    tokenLabel,
    phase: "H-prompt-extension-order",
    migration_state_status: "repaired_phase_g5",
    apiWired: true,
    runtimeResolution: true,
  };
})();

// Phase G.5 repair: backend runtime resolution is active; main Neo frontend owns payload/replay preview state while Phase H owns prompt order.
