// Phase H runtime UI marker. The live renderer is neo_app/static/js/neo.js so it can bind
// selected output, workspace detail mode, backend profile catalogs, and queue state.
window.NeoImageUpscaleExtension = window.NeoImageUpscaleExtension || {
  id: 'image.image_upscale',
  phase: 'H',
  runtimeActive: true,
  queueEndpoint: '/api/extensions/image-upscale/queue',
};
