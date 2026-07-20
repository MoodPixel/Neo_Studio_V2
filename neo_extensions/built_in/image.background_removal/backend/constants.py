from __future__ import annotations

EXTENSION_ID = "image.background_removal"
EXTENSION_VERSION = 13
QUEUE_ENDPOINT = "/api/extensions/background-removal/queue"
MODELS_ENDPOINT = "/api/extensions/background-removal/models"
SOURCE_FILE_ENDPOINT = "/api/extensions/background-removal/source-file"
DETECT_SUBJECTS_ENDPOINT = "/api/extensions/background-removal/detect-subjects"
BATCH_VIDEO_CATALOG_ENDPOINT = "/api/extensions/background-removal/batch-video/catalog"
BATCH_VIDEO_QUEUE_ENDPOINT = "/api/extensions/background-removal/batch-video/queue"
SEGMENTATION_LAB_SCHEMA_ID = "neo.image.background_removal.segmentation_lab.v1"
REGION_SEGMENTATION_SCHEMA_ID = "neo.image.background_removal.region_segmentation.v1"
MASK_UTILITIES_SCHEMA_ID = "neo.image.background_removal.mask_utilities.v2"
IMAGE_MASK_PREPARATION_SCHEMA_ID = "neo.image.image_mask_preparation.v1"
MATTING_SCHEMA_ID = "neo.image.background_removal.matting.v1"
SUPPORTED_COMFY_BACKENDS = {"comfyui", "comfyui_portable"}
COMMERCIAL_PROVIDER_IDS = {"remove_bg", "clipdrop_remove_bg"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MODEL_FOLDER_NAMES = ("birefnet", "BiRefNet", "BIREFNET")

NATIVE_MODEL_IDS = (
    "isnet-general-use",
    "isnet-anime",
    "u2net_human_seg",
    "u2netp",
    "birefnet-general",
    "birefnet-general-lite",
    "birefnet-portrait",
)

SAM_SESSION_MODEL_ID = "sam"
SAM_MODEL_VARIANTS = (
    "sam_vit_b_01ec64",
    "sam_vit_l_0b3195",
    "sam_vit_h_4b8939",
)
SAM_EXECUTION_MODES = ("auto", "comfy_impact", "native_onnx")
SAM_COMFY_FILENAMES = {
    "sam_vit_b_01ec64": "sam_vit_b_01ec64.pth",
    "sam_vit_l_0b3195": "sam_vit_l_0b3195.pth",
    "sam_vit_h_4b8939": "sam_vit_h_4b8939.pth",
}
MAX_SAM_SUBJECTS = 24
SAM_REFINEMENT_MODEL_IDS = (
    "birefnet-general",
    "birefnet-general-lite",
    "birefnet-portrait",
)

NATIVE_PRESET_MODELS = {
    "smart_auto": "isnet-general-use",
    "fine_edges": "birefnet-general",
    "portrait": "birefnet-portrait",
    "product": "isnet-general-use",
    "anime": "isnet-anime",
    "low_vram": "u2netp",
    "interactive_select": "birefnet-general",
}

PRESET_MODEL_CANDIDATES = {
    "smart_auto": (
        "General-dynamic.safetensors",
        "General.safetensors",
        "General-HR.safetensors",
    ),
    "fine_edges": (
        "Matting-HR.safetensors",
        "Matting.safetensors",
        "General-HR.safetensors",
        "General-dynamic.safetensors",
    ),
    "portrait": (
        "Portrait.safetensors",
        "General-dynamic.safetensors",
        "General.safetensors",
    ),
    "product": (
        "General-dynamic.safetensors",
        "General-HR.safetensors",
        "General.safetensors",
    ),
    "anime": (
        "General-dynamic.safetensors",
        "General.safetensors",
    ),
    "low_vram": (
        "General-Lite.safetensors",
        "Matting-Lite.safetensors",
        "General-Lite-2K.safetensors",
    ),
    "interactive_select": (
        "General-dynamic.safetensors",
        "Matting-HR.safetensors",
        "Portrait.safetensors",
    ),
}

DEFAULTS = {
    "enabled": True,
    "workflow_mode": "segment",
    "engine": "smart",
    "fallback_policy": "on_unavailable",
    "native_model": "isnet-general-use",
    "native_provider": "AUTO",
    "native_alpha_matting": False,
    "native_post_process_mask": False,
    "native_foreground_threshold": 240,
    "native_background_threshold": 10,
    "native_erode_size": 10,
    "preset": "smart_auto",
    "model": "",
    # The generic ComfyUI-RMBG node has its own model contract. Keep it
    # separate from the BiRefNet-specific ``model`` field.
    "rmbg_model": "",
    "rmbg_node_class": "",
    "rmbg_input_map": {},
    "rmbg_sensitivity": 1.0,
    "rmbg_mask_blur": 0,
    "rmbg_mask_offset": 0,
    "rmbg_invert_output": False,
    "rmbg_refine_foreground": False,
    "rmbg_background": "Alpha",
    "rmbg_background_color": "#222222",
    "device": "AUTO",
    "dtype": "float32",
    "use_weight": False,
    "width": 1024,
    "height": 1024,
    "upscale_method": "bilinear",
    "mask_threshold": 0.0,
    "mask_expand": 0,
    "mask_feather": 0,
    "foreground_estimation": True,
    "blur_size": 91,
    "blur_size_two": 7,
    "save_mask": True,
    "preview_image": False,
    "preview_background": "checkerboard",
    "manual_mask": False,
    "mask_source": "birefnet",
    "source_mode": "selected_result_or_upload",
    # Phase RMBG-5 model-aware advanced matting and high-resolution edges.
    "matting_enabled": False,
    "matting_profile": "birefnet_hr",
    "matting_model": "",
    "matting_node_class": "",
    "matting_process_res": 2048,
    "matting_device": "Auto",
    "matting_mask_refine": True,
    "matting_sensitivity": 0.9,
    "matting_transparent_object": True,
    "matting_use_source_alpha": False,
    "matting_edge_mode": "high_resolution_edges",
    "matting_mask_blur": 0,
    "matting_mask_offset": 0,
    "matting_invert": False,
    "matting_background": "Alpha",
    "matting_background_color": "#222222",
    "matting_refine_foreground": False,
    "matting_mask_names": [],
    # P6.5 commercial providers are always explicit and per-run opt-in.
    "commercial_profile_id": "",
    "commercial_upload_consent": False,
    "commercial_output_size": "auto",
    "commercial_subject_type": "auto",
    "commercial_preserve_semitransparency": True,
    "commercial_transparency_handling": "return_input_if_non_opaque",
    "parent_result_id": "",
    "parent_file_id": "",
    # P6.4 interactive SAM selection. Prompts are normalized [0, 1] so replay
    # remains valid when a source is reloaded at its original dimensions.
    "sam_prompts": [],
    "sam_subjects": [],
    "sam_execution": "auto",
    "sam_comfy_model": "",
    "sam_detector_model": "",
    "sam_detector_type": "bbox",
    "sam_detection_confidence": 0.35,
    "sam_model_variant": "sam_vit_b_01ec64",
    "sam_quantized": True,
    "sam_refine_mode": "birefnet_gate",
    "sam_refine_model": "birefnet-general",
    "sam_refine_fallback": True,
    "sam_gate_expand": 12,
    "sam_gate_feather": 6,
    "sam_mask_operation": "union",
    # Phase RMBG-2 prompt segmentation lab. Prompt rows are model-neutral;
    # the server resolves the adapter from live Comfy /object_info.
    "segmentation_lab_enabled": False,
    "segmentation_adapter": "auto",
    "segmentation_mask_operation": "union",
    "segmentation_lab_prompts": [],
    "segmentation_threshold": 0.35,
    "segmentation_confidence_threshold": 0.5,
    "segmentation_max_segments": 0,
    "segmentation_sam_model": "",
    "segmentation_sam2_model": "",
    "segmentation_dino_model": "",
    "segmentation_device": "Auto",
    "segmentation_segment_pick": 0,
}
