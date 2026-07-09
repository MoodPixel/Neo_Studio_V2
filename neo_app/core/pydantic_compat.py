from __future__ import annotations

import json
from typing import Any, TypeVar

T = TypeVar('T')


def model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, 'model_dump'):
        return model.model_dump()
    return model.dict()


def model_from_dict(model_cls: type[T], payload: dict[str, Any]) -> T:
    if hasattr(model_cls, 'model_validate'):
        return model_cls.model_validate(payload)  # type: ignore[attr-defined]
    return model_cls.parse_obj(payload)  # type: ignore[attr-defined]


def model_from_json(model_cls: type[T], payload: str) -> T:
    if hasattr(model_cls, 'model_validate_json'):
        return model_cls.model_validate_json(payload)  # type: ignore[attr-defined]
    return model_cls.parse_raw(payload)  # type: ignore[attr-defined]
