(() => {
  window.NeoBuiltInForgeCouple = window.NeoBuiltInForgeCouple || {
    extensionId: 'image.forge_couple',
    phase: 'FC3+RC2G',
    nativeRuntime: 'Haoming02/sd-forge-couple',
    modes: ['Basic', 'Advanced', 'Mask'],
    tileMode: 'img2img_verified_sd_upscale_runtime',
    tileRegionModes: ['Basic', 'Advanced'],
    maskTileMode: 'gated_pending_api_verification',
    promptAuthority: 'neo_core_positive_prompt',
    regionCanvas: 'shared_neo_region_canvas_rc2g',
    maskRegionBoxes: 'cropped_binary_preview_inside_shared_advanced_style_boxes',
    maskTransformRules: 'translate_exact_pixels_resize_source_nearest_paint_resets_source',
    advancedRegionCards: 'container_responsive_no_horizontal_clip',
    selectedRegionRemoval: 'toolbar_and_row_safe_delete',
    tileRuntimeRelationship: 'forge_couple_prompt_assignment_plus_separate_forge_sd_upscale_script',
    maskGeometryAuthority: 'generation_target_width_height_same_as_advanced',
    providerGatedUi: 'hidden_until_compatible_image_provider_selected',
    imageWorkspaceChrome: 'top_command_row_only_no_repeated_start_here_or_summary_cards',
  };
})();
