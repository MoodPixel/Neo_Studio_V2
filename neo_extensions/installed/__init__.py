"""Installed Neo extension package helpers.

Installed extension folders are surface-prefixed on disk, for example
``image.final_polish_lab``. Python cannot import a directory containing a dot
as a normal package segment, so this module exposes stable compatibility
packages such as ``neo_extensions.installed.final_polish_lab`` while keeping
extension ids and manifest folders unchanged.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_BASE = Path(__file__).resolve().parent
_ALIASES = {
    "final_polish_lab": "image.final_polish_lab",
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
