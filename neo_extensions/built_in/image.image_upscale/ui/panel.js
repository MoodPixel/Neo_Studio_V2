// Phase 10 provider-specific runtime UI marker. The live renderer is neo_app/static/js/neo.js so it can bind
// selected output, workspace detail mode, backend profile catalogs, and queue state.
window.NeoImageUpscaleExtension = window.NeoImageUpscaleExtension || {
  id: 'image.image_upscale',
  phase: '10',
  runtimeActive: true,
  queueEndpoint: '/api/extensions/image-upscale/queue',
  providerPolicy: 'selected_profile_only',
  forgeExecution: 'forge.extras.single_image.v2',
  automaticProviderFallback: false,
};
