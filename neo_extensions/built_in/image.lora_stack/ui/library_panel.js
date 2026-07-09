(() => {
  window.NeoLoraLibrary = window.NeoLoraLibrary || {};
  window.NeoLoraLibrary.phase = "C.2";
  window.NeoLoraLibrary.mergeModes = ["fill_missing", "smart_merge", "overwrite_selected", "previews_only"];
  window.NeoLoraLibrary.source = "comfy_lora_loader";
  window.NeoLoraLibrary.recordToStackRow = function recordToStackRow(record = {}) {
    return {
      uid: `lora_${Date.now()}`,
      enabled: true,
      name: record.name || record.file || "",
      strength: Number(record.default_strength ?? 0.8),
      target: "both",
      apply_to: "global",
      source_record_id: record.id || "",
    };
  };
})();
