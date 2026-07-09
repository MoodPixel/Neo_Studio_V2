(() => {
  window.NeoBuiltInIpAdapter = window.NeoBuiltInIpAdapter || {
    extensionId: 'image.ip_adapter',
    workspaceApp: 'reference',
    payloadVersion: 1,
    uiPhase: 'H-display-modes',
    sections: ['compact_layout', 'guided_layout', 'expert_layout', 'identity_presets', 'primary_unit', 'extra_units', 'reference_images', 'faceid_settings'],
    payloadContract: 'extensions.image.ip_adapter',
    displayModes: {
      compact: ['enable', 'mode', 'model_or_preset', 'reference_images', 'weight'],
      guided: ['friendly_help', 'identity_presets', 'node_readiness', 'common_controls'],
      expert: ['route_ids', 'payload_keys', 'node_names', 'compiler_patch_summary'],
    },
  };
})();
