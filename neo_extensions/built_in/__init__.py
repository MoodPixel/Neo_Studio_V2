
"""Built-in Neo extension package helpers.

Built-in extension folders are surface-prefixed on disk, for example
``image.lora_stack``.  Python cannot import a directory containing a dot as a
normal package segment, so we expose stable compatibility packages such as
``neo_extensions.built_in.lora_stack`` and route their ``__path__`` to the
surface-prefixed folder.  Extension ids and payload keys stay unchanged.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_BASE = Path(__file__).resolve().parent
_ALIASES = {
    "cfg_fix_dynamic_thresholding": "image.cfg_fix_dynamic_thresholding",
    "lora_stack": "image.lora_stack",
    "embeddings_ti": "image.embeddings_ti",
    "controlnet": "image.controlnet",
    "ip_adapter": "image.ip_adapter",
    "scene_director": "image.scene_director",
    "high_res_lab": "image.high_res_lab",
    "image_upscale": "image.image_upscale",
    "background_removal": "image.background_removal",
    "adetailer": "image.adetailer",
    "style_stack": "image.style_stack",
    "wildcards": "image.wildcards",
    "layerdiffuse": "image.layerdiffuse",
}

for _alias, _folder in _ALIASES.items():
    _path = _BASE / _folder
    if not _path.exists():
        continue
    _module_name = f"{__name__}.{_alias}"
    _module = sys.modules.get(_module_name)
    if _module is None:
        _module = types.ModuleType(_module_name)
        _module.__file__ = str(_path / "__init__.py")
        _module.__package__ = _module_name
        _module.__path__ = [str(_path)]
        sys.modules[_module_name] = _module
