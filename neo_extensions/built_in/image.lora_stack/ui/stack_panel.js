(() => {
  window.NeoLoraStack = window.NeoLoraStack || {};
  window.NeoLoraStack.phase = "C";
  window.NeoLoraStack.cleanRows = function cleanRows(rows = []) {
    const seen = new Set();
    return rows.filter((row) => row && row.enabled !== false && row.name).map((row) => {
      const clean = {
        uid: row.uid,
        enabled: row.enabled !== false,
        name: String(row.name || "").trim(),
        strength: Math.max(-4, Math.min(4, Number(row.strength ?? 0.8))),
        target: ["both", "base", "finish"].includes(row.target) ? row.target : "both",
        apply_to: row.apply_to || "global",
      };
      const key = `${clean.name}|${clean.strength}|${clean.target}|${clean.apply_to}`;
      if (seen.has(key)) return null;
      seen.add(key);
      return clean;
    }).filter(Boolean);
  };
  window.NeoLoraStack.buildPayload = function buildPayload(rows = [], route = {}) {
    const clean = window.NeoLoraStack.cleanRows(rows);
    return { extensions: { lora_stack: { enabled: clean.length > 0, version: 1, inputs: {}, params: clean.length ? { loras: clean } : {}, assets: {}, metadata: { source: "image.assets.lora_stack", ui_phase: "C", route_state: route.route_state || "unknown" } } } };
  };
})();
