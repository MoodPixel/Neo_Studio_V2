(() => {
  const api = window.NeoLoraStack = window.NeoLoraStack || {};
  api.phase = "12";
  api.portableName = function portableName(value = "") {
    let text = String(value || "").replace(/\\/g, "/").trim().replace(/^<lora:([^:>]+)(?::[^>]*)?>$/i, "$1");
    if (/^[A-Za-z]:\//.test(text) || text.startsWith("/")) text = text.split("/").filter(Boolean).pop() || "";
    while (text.startsWith("./")) text = text.slice(2);
    return text;
  };
  api.identityKey = function identityKey(value = "") {
    const portable = api.portableName(value);
    const file = portable.split("/").pop() || portable;
    return file.replace(/\.(safetensors|ckpt|pt|pth|bin)$/i, "").toLowerCase();
  };
  api.forgeTag = function forgeTag(name, strength = 0.8) {
    const clean = api.identityKey(name);
    const weight = Math.max(-4, Math.min(4, Number(strength ?? 0.8)));
    return clean ? `<lora:${clean}:${Number(weight.toFixed(4))}>` : "";
  };
  api.cleanRows = function cleanRows(rows = []) {
    const seen = new Set();
    return rows.filter((row) => row && row.enabled !== false && row.name).map((row, index) => {
      const clean = {
        uid: String(row.uid || `lora_${index + 1}`),
        enabled: row.enabled !== false,
        name: api.portableName(row.name),
        strength: Math.max(-4, Math.min(4, Number(row.strength ?? 0.8))),
        target: ["both", "base", "finish"].includes(row.target) ? row.target : "both",
        apply_to: row.apply_to || "global",
      };
      if (row.source_record_id) clean.source_record_id = String(row.source_record_id);
      const key = `${api.identityKey(clean.name)}|${clean.strength}|${clean.target}|${clean.apply_to}`;
      if (!api.identityKey(clean.name) || seen.has(key)) return null;
      seen.add(key);
      return clean;
    }).filter(Boolean);
  };
  api.compatibilityMode = function compatibilityMode(route = {}) {
    if (route.lora_mode) return route.lora_mode;
    return ["krea2", "krea2_turbo"].includes(String(route.family || "")) ? "model_only" : "model_and_clip";
  };
  api.buildPayload = function buildPayload(rows = [], route = {}, provider = {}) {
    const clean = api.cleanRows(rows);
    return {
      extensions: {
        lora_stack: {
          enabled: clean.length > 0,
          version: 1,
          inputs: clean.length ? { loras: clean } : {},
          params: clean.length ? { loras: clean } : {},
          assets: {},
          metadata: {
            source: "image.assets.lora_stack",
            ui_phase: "12",
            route_state: route.route_state || "unknown",
            backend: route.backend || "",
            family: route.family || "",
            loader: route.loader || "",
            workflow_mode: route.workflow_mode || route.mode || "generate",
            engine: route.engine || "native",
            workflow_engine: route.workflow_engine || route.engine || "native",
            compatibility_engine_independent: true,
            route_key: route.compatibility_route_key || route.route_key || "",
            compatibility_route_key: route.compatibility_route_key || route.route_key || "",
            workflow_route_key: route.workflow_route_key || route.route_key || "",
            lora_mode: api.compatibilityMode(route),
            provider_binding: {
              profile_id: provider.profile_id || "",
              provider_id: provider.provider_id || "",
              selected_profile_only: true,
              automatic_provider_fallback: false,
              serialization: provider.provider_id === "forge" ? "positive_prompt_compile_time" : "workflow_loader",
              visible_prompt_mutation: false,
            },
          },
        },
      },
    };
  };
})();
