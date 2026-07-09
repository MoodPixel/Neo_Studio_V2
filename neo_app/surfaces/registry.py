from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .schema import SurfaceDefinition, SurfaceManifest
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict

MANIFEST_PATH = Path(__file__).with_name("surface_manifest.json")


@lru_cache(maxsize=1)
def load_manifest() -> SurfaceManifest:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return model_from_dict(SurfaceManifest, data)


def list_surfaces(include_disabled: bool = False) -> list[SurfaceDefinition]:
    manifest = load_manifest()
    if include_disabled:
        return manifest.surfaces
    return [surface for surface in manifest.surfaces if surface.status != "disabled"]


def get_surface(surface_id: str) -> SurfaceDefinition | None:
    for surface in list_surfaces(include_disabled=True):
        if surface.surface_id == surface_id:
            return surface
    return None


def get_surface_payload(include_disabled: bool = False) -> dict:
    manifest = load_manifest()
    surfaces = list_surfaces(include_disabled=include_disabled)
    return {
        "schema_version": manifest.schema_version,
        "surfaces": [model_to_dict(surface) for surface in surfaces],
    }
