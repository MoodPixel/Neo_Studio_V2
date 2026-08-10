"""Prompt & Captioning first-class surface helpers."""

from .support_matrix import get_support_matrix
from .validation import validate_route_payload
from .payload_contract import create_caption_payload, create_prompt_payload, normalize_prompt_captioning_payload
from .profile_contract import get_profile_manifest, get_profile_manifest_payload, normalize_profile
from .visual_analysis import build_visual_analysis_request, empty_visual_analysis, normalize_visual_analysis
from .caption_profiles import compile_caption_task_contract, caption_sampling_params
from .prompt_profiles import compile_prompt_task_contract
from .persistence_migration import PERSISTENCE_SCHEMA_VERSION, canonical_profile_for_record, normalize_persisted_record
from .service import run_prompt_tool, save_prompt, prompt_records

__all__ = [
    "get_support_matrix",
    "validate_route_payload",
    "normalize_prompt_captioning_payload",
    "create_prompt_payload",
    "create_caption_payload",
    "get_profile_manifest",
    "get_profile_manifest_payload",
    "normalize_profile",
    "build_visual_analysis_request",
    "empty_visual_analysis",
    "normalize_visual_analysis",
    "compile_caption_task_contract",
    "compile_prompt_task_contract",
    "caption_sampling_params",
    "PERSISTENCE_SCHEMA_VERSION",
    "canonical_profile_for_record",
    "normalize_persisted_record",
    "run_prompt_tool",
    "save_prompt",
    "prompt_records",
]
