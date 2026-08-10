from __future__ import annotations

EXTENSION_ID = "image.adetailer"
EXTENSION_NAME = "Image · ADetailer"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "finish"
MOUNT_SLOT = "image.finish.adetailer"
VERSION = 1
PHASE = "L"
SKELETON_ONLY = False
SUPPORT_MATRIX_READY = True
PAYLOAD_RUNTIME_READY = True
WORKFLOW_PATCH_READY = True
VALIDATION_RUNTIME_READY = True
NODE_DISCOVERY_RUNTIME_READY = True

AVAILABLE = "available"
EXPERIMENTAL_AVAILABLE = "experimental_available"
PLANNED_GATED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"

ACTIVE_ROUTE_STATES = {AVAILABLE, EXPERIMENTAL_AVAILABLE}
GATED_ROUTE_STATES = {PLANNED_GATED, PROVIDER_GATED}
UNSUPPORTED_ROUTE_STATES = {UNSUPPORTED}
VALID_ROUTE_STATES = ACTIVE_ROUTE_STATES | GATED_ROUTE_STATES | UNSUPPORTED_ROUTE_STATES

SUPPORTED_BACKENDS = ("comfyui", "comfyui_portable")
DISCOVERED_FAMILIES = ("sdxl", "sd15", "flux", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "qwen_image_edit_2511", "z_image", "z_image_turbo", "krea2", "krea2_turbo", "hidream", "wan_image", "hunyuan_image")
DISCOVERED_LOADERS = ("checkpoint", "checkpoint_aio", "diffusion_model", "gguf", "native")
DISCOVERED_MODES = ("generate", "img2img", "inpaint", "outpaint")
DISCOVERED_SUBTABS = ("generations", "assets", "reference", "finish", "results")

REQUIRED_NODES = ["FaceDetailer", "UltralyticsDetectorProvider"]
OPTIONAL_NODES = [
    "ONNXDetectorProvider",
    "SAMLoader",
    "SAMModelLoader",
    "BboxDetectorSEGS",
    "SegmDetectorSEGS",
    "ImpactSEGSOrderedFilter",
    "ImpactSEGSRangeFilter",
    "SEGSDetailer",
    "SEGSPaste",
    "MaskToSEGS",
    "ImpactDilateMaskInSEGS",
    "ImpactGaussianBlurMaskInSEGS",
    "ToBasicPipe",
    "CLIPTextEncode",
]
NODE_ALIASES = {
    "FaceDetailerPipe": "FaceDetailer",
    "ImpactFaceDetailer": "FaceDetailer",
    "UltralyticsDetectorProvider //Inspire": "UltralyticsDetectorProvider",
    "UltralyticsDetectorProviderPipe": "UltralyticsDetectorProvider",
    "ONNXDetectorProvider //Inspire": "ONNXDetectorProvider",
    "SAMLoader //Inspire": "SAMLoader",
    "SAMLoaderImpact": "SAMLoader",
    "SAMModelLoaderImpact": "SAMModelLoader",
}

DETECTOR_TYPES = {"bbox", "segm", "onnx_bbox", "onnx_segm"}
TARGET_ORDERS = {"auto", "area_desc", "area_asc", "left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top", "largest_first", "smallest_first", "center_first", "confidence_desc", "score_desc"}
TARGET_SPLIT_MODES = {"sep", "none", "sep_prompt_targets", "single_prompt", "repeat_prompt"}

DEFAULT_PARAMS = {
    "enabled": False,
    "model_source": "generation_model",
    "identity_protection": "none",
    "identity_lora_revision": "route_family",
    "family_preset_mode": "auto_family",
    "detailer_model_family": "sdxl",
    "detailer_checkpoint": "",
    "detailer_vae": "automatic",
    "lora_inheritance": "inherit_all",
    "inherit_lora_uids": [],
    "detailer_lora_enabled": False,
    "detailer_lora": "",
    "detailer_lora_strength_model": 0.8,
    "detailer_lora_strength_clip": 0.8,
    "detailer_lora_trigger": "",
    "detailer_loras": [],
    "custom_detector_root": "",
    "custom_sam_root": "",
    "sam_preset": "",
    "provider": "ultralytics",
    "sam_model": "",
    "custom_classes": "",
    "confidence": 0.35,
    "top_k": 0,
    "bbox_grow": 12,
    "mask_blur": 4,
    "denoise": 0.12,
    "steps": 12,
    "cfg": None,
    "sampler_name": "",
    "scheduler": "",
    "guide_size": 768,
    "max_size": 1024,
    "noise_mask": True,
    "noise_mask_feather": 16,
    "use_main_prompt": True,
    "force_inpaint": True,
    "detector_model": "",
    "detector_type": "bbox",
    "mode": "face",
    "target_order": "auto",
    "target_split_mode": "sep_prompt_targets",
    "start_index": 1,
    "count": 1,
    "min_area": 0,
    "max_area": 0,
    "target_mode": "auto_detect",
    "reference_lock": "none",
    "positive_prompt": "",
    "negative_prompt": "",
    "manual_boxes": "",
    "detailer_passes": [],
}


NUMERIC_LIMITS = {
    "confidence": (0.0, 1.0),
    "top_k": (0, 999),
    "bbox_grow": (-128, 512),
    "start_index": (1, 999),
    "count": (0, 999),
    "min_area": (0, 999999999),
    "max_area": (0, 999999999),
    "mask_blur": (0, 128),
    "denoise": (0.0, 1.0),
    "steps": (1, 150),
    "cfg": (0.0, 30.0),
    "guide_size": (64, 4096),
    "max_size": (64, 8192),
    "noise_mask_feather": (0, 128),
    "detailer_lora_strength_model": (-4.0, 4.0),
    "detailer_lora_strength_clip": (-4.0, 4.0),
}
INTEGER_PARAMS = {"top_k", "bbox_grow", "mask_blur", "steps", "guide_size", "max_size", "noise_mask_feather", "start_index", "count", "min_area", "max_area"}
STRING_PARAMS = {"model_source", "identity_protection", "identity_lora_revision", "family_preset_mode", "sampler_name", "scheduler", "detailer_model_family", "detailer_checkpoint", "detailer_vae", "lora_inheritance", "detailer_lora", "detailer_lora_trigger", "detector_model", "positive_prompt", "negative_prompt", "sam_model", "custom_classes", "manual_boxes", "custom_detector_root", "custom_sam_root", "sam_preset", "provider", "mode", "target_mode", "reference_lock"}
ENUM_PARAMS = {"detector_type", "target_order", "target_split_mode"}
RUNTIME_PARAMS = set(DEFAULT_PARAMS)
BOOLEAN_PARAMS = {"use_main_prompt", "force_inpaint", "noise_mask", "detailer_lora_enabled"}
LIST_PARAMS = {"inherit_lora_uids", "detailer_loras"}
DETAILER_PASS_KEYS = {"id", "label", "enabled", "mode", "detector_type", "detector_model", "target_order", "start_index", "count", "min_area", "max_area", "target_mode", "manual_boxes", "reference_lock", "positive_prompt", "negative_prompt"}
DETAILER_PASS_BOOLEAN_KEYS = {"enabled"}
DETAILER_PASS_INTEGER_KEYS = {"start_index", "count", "min_area", "max_area"}
DETAILER_PASS_STRING_KEYS = {"id", "label", "detector_model", "manual_boxes", "positive_prompt", "negative_prompt"}
DETAILER_PASS_MODES = {"face", "hands", "person", "custom"}
DETAILER_PASS_TARGET_MODES = {"auto_detect", "manual_boxes"}
DETAILER_PASS_REFERENCE_LOCKS = {"none", "soft_identity", "strong_identity", "face_only", "style_only", "controlnet", "ipadapter", "both"}
MAX_DETAILER_PASSES = 16
ADVANCED_PARAMS = {"model_source", "identity_protection", "identity_lora_revision", "family_preset_mode", "sampler_name", "scheduler", "guide_size", "max_size", "noise_mask", "noise_mask_feather", "detailer_model_family", "detailer_checkpoint", "detailer_vae", "lora_inheritance", "inherit_lora_uids", "detailer_lora_enabled", "detailer_lora", "detailer_lora_strength_model", "detailer_lora_strength_clip", "detailer_lora_trigger", "detailer_loras", "sam_model", "custom_classes", "target_order", "target_split_mode", "manual_boxes", "detailer_passes"}

# V1 avoided local repair blowups by keeping high generation CFG from leaking into detailer passes.
CFG_SAFETY_CAP = 15.0
