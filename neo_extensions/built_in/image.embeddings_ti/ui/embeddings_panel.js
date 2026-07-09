(() => {
  const EXTENSION_ID = "embeddings_ti";
  const SOURCE = "image.assets.embeddings_ti";
  const TARGETS = ["positive_prompt", "negative_prompt", "finish_positive", "finish_negative"];
  function normalizeEmbeddingToken(value = "") {
    const text = String(value || "").trim();
    if (!text) return "";
    if (text.startsWith("embedding:")) return text;
    const file = text.replace(/\\/g, "/").split("/").pop() || text;
    return `embedding:${file.replace(/\.(pt|safetensors|bin)$/i, "")}`;
  }
  function cleanEmbeddingItems(items = []) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).map((item) => {
      const token = normalizeEmbeddingToken(item?.token || item?.name || "");
      if (!token) return null;
      const strength = Math.max(0, Math.min(2, Number(item?.strength ?? 1) || 1));
      const target = TARGETS.includes(item?.target) ? item.target : "negative_prompt";
      const key = `${token.toLowerCase()}|${strength}|${target}`;
      if (seen.has(key)) return null;
      seen.add(key);
      return { token, name: item?.name || token.replace(/^embedding:/, ""), strength, target, source_record_id: item?.source_record_id || "" };
    }).filter(Boolean);
  }
  function buildEmbeddingsTiBlock(state = {}, route = {}, applied = false) {
    const items = cleanEmbeddingItems(state.items || []);
    const routeState = route.route_state || "unknown";
    const active = Boolean(applied && items.length && ["available", "experimental_available"].includes(routeState));
    return {
      enabled: active,
      version: 1,
      inputs: {},
      params: active ? { items } : {},
      assets: active ? { selected_embeddings: items.map((item) => ({ token: item.token, name: item.name, target: item.target, strength: item.strength })) } : {},
      metadata: { source: SOURCE, route_state: routeState, ui_phase: "H-backend-library" }
    };
  }
  window.NeoEmbeddingsTI = { EXTENSION_ID, SOURCE, normalizeEmbeddingToken, cleanEmbeddingItems, buildEmbeddingsTiBlock };
})();
