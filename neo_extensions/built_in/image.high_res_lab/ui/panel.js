(function () {
  window.NeoHighResLab = window.NeoHighResLab || {};
  Object.assign(window.NeoHighResLab, {
    extensionId: "image.high_res_lab",
    phase: "K",
    uiMigrated: true,
    payloadRuntimeReady: true,
    workflowPatchReady: true,
    profiles: ["custom", "gentle_polish", "balanced_finish", "detail_push", "bigger_finish"],
    controls: [
      "enabled", "profile", "mode", "resize_method", "scale", "steps", "denoise", "cfg",
      "sampler", "scheduler", "upscaler", "tiled_vae", "tile_size", "tile_overlap"
    ],
    buildPayload(settings, route) {
      const activeStates = ["available", "experimental_available"];
      const enabled = Boolean(settings && settings.enabled && activeStates.includes(route && route.route_state));
      return {
        extensions: {
          "image.high_res_lab": {
            enabled,
            version: 1,
            inputs: {},
            params: enabled ? { ...(settings || {}) } : {},
            assets: enabled && settings && settings.mode === "image_upscale" && settings.upscaler ? { upscaler: settings.upscaler } : {},
            metadata: {
              extension_id: "image.high_res_lab",
              extension_type: "built_in",
              phase: "K",
              route_state: route && route.route_state,
              workflow_patch_ready: true,
              optional_node_discovery_ready: true
            }
          }
        }
      };
    }
  });
})();
