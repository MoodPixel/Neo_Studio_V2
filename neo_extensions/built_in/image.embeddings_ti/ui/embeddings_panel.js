(() => {
  const EXTENSION_ID = "embeddings_ti";
  const SOURCE = "image.assets.embeddings_ti";
  const VERSION = 2;
  const TARGETS = ["positive_prompt", "negative_prompt", "both", "finish_positive", "finish_negative"];

  function normalizeEmbeddingToken(value = "") {
    let text = String(value || "").trim().replace(/\\/g, "/");
    if (!text) return "";
    const weighted = text.match(/^\(\s*(.*?)\s*:\s*[-+]?\d+(?:\.\d+)?\s*\)$/);
    if (weighted) text = weighted[1].trim();
    text = text.replace(/^embedding\s*:\s*/i, "").trim();
    if (/^[A-Za-z]:\//.test(text) || text.startsWith("/")) text = text.split("/").filter(Boolean).pop() || "";
    const file = text.split("/").pop() || text;
    return file.replace(/\.(pt|safetensors|bin)$/i, "").trim();
  }

  function providerSyntax(value, strength = 1, providerId = "comfyui") {
    const token = normalizeEmbeddingToken(value);
    if (!token) return "";
    const amount = Math.max(0, Math.min(2, Number(strength ?? 1) || 1));
    const base = String(providerId || "comfyui").toLowerCase() === "forge" ? token : `embedding:${token}`;
    return Math.abs(amount - 1) < 0.001 ? base : `(${base}:${Number(amount.toFixed(3))})`;
  }

  function cleanEmbeddingItems(items = []) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).map((item) => {
      const token = normalizeEmbeddingToken(item?.asset_name || item?.token || item?.catalog_name || item?.name || "");
      if (!token) return null;
      const strength = Math.max(0, Math.min(2, Number(item?.strength ?? 1) || 1));
      const target = TARGETS.includes(item?.target) ? item.target : "negative_prompt";
      const key = `${token.toLowerCase()}|${target}`;
      if (seen.has(key)) return null;
      seen.add(key);
      return {
        uid: item?.uid || "",
        token,
        asset_name: token,
        catalog_name: String(item?.catalog_name || token).replace(/\\/g, "/"),
        name: item?.name || token,
        strength,
        target,
        source_record_id: item?.source_record_id || "",
      };
    }).filter(Boolean);
  }

  function buildEmbeddingsTiBlock(state = {}, route = {}, applied = false, provider = {}) {
    const items = cleanEmbeddingItems(state.items || []);
    const routeState = route.route_state || "unknown";
    const active = Boolean(applied && items.length && ["available", "experimental_available"].includes(routeState));
    const providerId = String(provider.provider_id || route.backend || "comfyui").toLowerCase();
    return {
      enabled: active,
      version: VERSION,
      inputs: {},
      params: active ? { items } : {},
      assets: active ? {
        selected_embeddings: items.map((item) => ({
          token: item.token,
          asset_name: item.asset_name,
          catalog_name: item.catalog_name,
          name: item.name,
          target: item.target,
          strength: item.strength,
          source_record_id: item.source_record_id,
        })),
      } : {},
      metadata: {
        source: SOURCE,
        route_state: routeState,
        ui_phase: "9-provider-formatting",
        provider_binding: {
          profile_id: provider.profile_id || "",
          provider_id: providerId,
          provider_label: provider.provider_label || "",
          catalog_source: provider.catalog_source || "",
          selected_profile_only: true,
          automatic_provider_fallback: false,
          serialization: providerId === "forge" ? "plain_trigger_compile_time" : "comfy_embedding_prefix_compile_time",
          visible_prompt_mutation: false,
        },
      },
    };
  }

  window.NeoEmbeddingsTI = {
    EXTENSION_ID,
    SOURCE,
    VERSION,
    TARGETS,
    normalizeEmbeddingToken,
    providerSyntax,
    cleanEmbeddingItems,
    buildEmbeddingsTiBlock,
  };
})();
