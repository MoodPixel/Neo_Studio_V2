"""Admin Model Guide foundation package.

Phase 1 is intentionally read-only: manifest loading, validation, grouping,
and catalog payload generation. User paths, remote metadata, installed scans,
and downloads are later phases and must stay out of repository manifests.
"""

from .model_catalog_service import (
    admin_model_catalog_payload,
    admin_model_catalog_summary_payload,
    admin_model_category_map_payload,
    admin_model_folder_rules_payload,
)

__all__ = [
    "admin_model_catalog_payload",
    "admin_model_catalog_summary_payload",
    "admin_model_category_map_payload",
    "admin_model_folder_rules_payload",
]
