from __future__ import annotations

EXTENSION_ID = "image.forge_couple"
EXTENSION_NAME = "Image · Forge Couple"
VERSION = "0.3.0"
PHASE = "FC3"
CONTRACT = "haoming02.forge_couple.basic_advanced_mask_tile.api.v1"
SUPPORTED_FAMILIES = {"sd15", "sdxl"}
SUPPORTED_MODES = {"txt2img", "img2img", "inpaint"}
REGION_MODES = {"Basic", "Advanced", "Mask"}
TILE_REGION_MODES = {"Basic", "Advanced"}
DIRECTIONS = {"Horizontal", "Vertical"}
BACKGROUNDS = {"None", "First Line", "Last Line"}
COMMON_PARSERS = {"off", "{ }", "< >"}
DEFAULT_ADVANCED_MAPPING = [
    [0.0, 0.5, 0.0, 1.0, 1.0],
    [0.5, 1.0, 0.0, 1.0, 1.0],
]
DEFAULT_PARAMS = {
    "mode": "Basic",
    "disable_hr": True,
    "separator": "",
    "direction": "Horizontal",
    "background": "None",
    "background_weight": 0.5,
    "advanced_mapping": DEFAULT_ADVANCED_MAPPING,
    "mask_mapping": [],
    "common_parser": "{ }",
    "common_debug": False,
    "def_in_prompt": True,
    "tile_enabled": False,
    "tile_columns": 2,
    "tile_rows": 2,
    "tile_threshold": 0.75,
    "tile_subject_replacement": "",
    "tile_debug": False,
    "tile_upscaler": "None",
    "tile_save_to_extras": False,
    "tile_scale_factor": 2.0,
    "tile_overlap": 64,
    "tile_final_width": -1,
    "tile_final_height": -1,
}
