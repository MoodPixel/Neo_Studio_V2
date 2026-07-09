"""Constants for the Image Upscale built-in extension."""
from __future__ import annotations

EXTENSION_ID = "image.image_upscale"
EXTENSION_NAME = "Image · Image Upscale"
EXTENSION_TYPE = "built_in"
EXTENSION_VERSION = "0.8.1-phase-j-global-route-lock"
WORKSPACE_APP = "finish"
MOUNT_SLOT = "image.finish.image_upscale"
QUEUE_ENDPOINT = "/api/extensions/image-upscale/queue"
PHASE = "J"

AVAILABLE = "available"
EXPERIMENTAL_AVAILABLE = "experimental_available"
PLANNED_GATED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"
ACTIVE_ROUTE_STATES = {AVAILABLE, EXPERIMENTAL_AVAILABLE}
GATED_ROUTE_STATES = {PLANNED_GATED, PROVIDER_GATED}
UNSUPPORTED_ROUTE_STATES = {UNSUPPORTED}
VALID_ROUTE_STATES = ACTIVE_ROUTE_STATES | GATED_ROUTE_STATES | UNSUPPORTED_ROUTE_STATES

SUPPORTED_COMFY_BACKENDS = ("comfyui", "comfyui_portable")
PROVIDER_GATED_BACKENDS = ("forge", "a1111", "cloud_api", "mock")
IMAGE_WORKSPACE = "image"

# V1 Image Upscale is a standalone Comfy utility graph. These are base nodes
# required before any queue path may run.
REQUIRED_COMFY_NODES = ("LoadImage", "ImageScaleBy", "SaveImage")
OPTIONAL_PREVIEW_NODES = ("PreviewImage",)
OPTIONAL_UPSCALE_MODEL_NODES = ("UpscaleModelLoader", "ImageUpscaleWithModel")
OPTIONAL_CODEFORMER_NODES = ("FaceRestoreModelLoader", "FaceRestoreCFWithModel")
OPTIONAL_SEEDVR2_NODES = ("SeedVR2LoadDiTModel", "SeedVR2LoadVAEModel", "SeedVR2VideoUpscaler")

OPTIONAL_NODE_GROUPS = {
    "preview": OPTIONAL_PREVIEW_NODES,
    "model_upscale": OPTIONAL_UPSCALE_MODEL_NODES,
    "codeformer_restore": OPTIONAL_CODEFORMER_NODES,
    "seedvr2_experimental": OPTIONAL_SEEDVR2_NODES,
}

CODEFORMER_MODEL_FOLDER_HINT = "ComfyUI/models/facerestore_models/"
CODEFORMER_FACE_DETECTION_OPTIONS = ("retinaface_resnet50", "retinaface_mobile0.25", "YOLOv5l", "YOLOv5n")
SEEDVR2_MODEL_FOLDER_HINT = "ComfyUI/models/SEEDVR2/"
SEEDVR2_DIT_DEFAULT = "seedvr2_ema_3b-Q4_K_M.gguf"
SEEDVR2_VAE_DEFAULT = "ema_vae_fp16.safetensors"
SEEDVR2_ATTENTION_MODES = ("sdpa", "flash_attn_2", "flash_attn_3", "sageattn_2", "sageattn_3")
SEEDVR2_COLOR_CORRECTION_OPTIONS = ("lab", "wavelet", "wavelet_adaptive", "hsv", "adain", "none")
SEEDVR2_ENGINE_ID = "seedvr2"

# Discovered V2 route axes. Image Upscale itself does not depend on these axes,
# but Phase D documents every discovered route so UI/diagnostics can explain
# why the extension stays provider/node based instead of family/loader based.
DISCOVERED_FAMILIES = ("sdxl", "sd15", "flux", "flux2_klein", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "z_image_turbo", "hidream", "wan_image", "hunyuan_image", "unknown")
DISCOVERED_LOADERS = ("checkpoint", "checkpoint_aio", "diffusion_model", "gguf", "native", "provider", "unknown")
DISCOVERED_MODES = ("txt2img", "generate", "img2img", "inpaint", "outpaint", "reference", "edit", "multi_reference", "image_upscale")
DISCOVERED_SUBTABS = ("generations", "assets", "reference", "finish", "results")
