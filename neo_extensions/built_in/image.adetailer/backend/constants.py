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
DISCOVERED_FAMILIES = ("sdxl", "sd15", "flux", "qwen_image", "qwen_rapid_aio", "qwen_image_edit_2509", "z_image", "hidream", "wan_image", "hunyuan_image")
DISCOVERED_LOADERS = ("checkpoint", "diffusion_model", "gguf", "native")
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
}
INTEGER_PARAMS = {"top_k", "bbox_grow", "mask_blur", "steps", "start_index", "count", "min_area", "max_area"}
STRING_PARAMS = {"detector_model", "positive_prompt", "negative_prompt", "sam_model", "custom_classes", "manual_boxes", "custom_detector_root", "custom_sam_root", "sam_preset", "provider", "mode", "target_mode", "reference_lock"}
ENUM_PARAMS = {"detector_type", "target_order", "target_split_mode"}
RUNTIME_PARAMS = set(DEFAULT_PARAMS)
BOOLEAN_PARAMS = {"use_main_prompt", "force_inpaint"}
DETAILER_PASS_KEYS = {"id", "label", "enabled", "mode", "detector_type", "detector_model", "target_order", "start_index", "count", "min_area", "max_area", "target_mode", "manual_boxes", "reference_lock", "positive_prompt", "negative_prompt"}
DETAILER_PASS_BOOLEAN_KEYS = {"enabled"}
DETAILER_PASS_INTEGER_KEYS = {"start_index", "count", "min_area", "max_area"}
DETAILER_PASS_STRING_KEYS = {"id", "label", "detector_model", "manual_boxes", "positive_prompt", "negative_prompt"}
DETAILER_PASS_MODES = {"face", "hands", "person", "custom"}
DETAILER_PASS_TARGET_MODES = {"auto_detect", "manual_boxes"}
DETAILER_PASS_REFERENCE_LOCKS = {"none", "soft_identity", "strong_identity", "face_only", "style_only", "controlnet", "ipadapter", "both"}
MAX_DETAILER_PASSES = 16
ADVANCED_PARAMS = {"sam_model", "custom_classes", "target_order", "target_split_mode", "manual_boxes", "detailer_passes"}

# V1 avoided local repair blowups by keeping high generation CFG from leaking into detailer passes.
CFG_SAFETY_CAP = 15.0
