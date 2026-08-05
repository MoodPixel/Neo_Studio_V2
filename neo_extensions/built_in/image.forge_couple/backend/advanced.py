from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Iterable

from .constants import DEFAULT_ADVANCED_MAPPING


def _number(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if isfinite(parsed) else default


def normalize_mapping(raw: Any) -> list[list[float]]:
    if not isinstance(raw, (list, tuple)):
        return deepcopy(DEFAULT_ADVANCED_MAPPING)
    result: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 5:
            continue
        x1 = max(0.0, min(1.0, _number(item[0], 0.0)))
        x2 = max(0.0, min(1.0, _number(item[1], 1.0)))
        y1 = max(0.0, min(1.0, _number(item[2], 0.0)))
        y2 = max(0.0, min(1.0, _number(item[3], 1.0)))
        weight = max(0.0, min(5.0, _number(item[4], 1.0)))
        result.append([x1, x2, y1, y2, weight])
    return result or deepcopy(DEFAULT_ADVANCED_MAPPING)


def mapping_errors(mapping: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(mapping, (list, tuple)) or not mapping:
        return ["ForgeCouple Advanced mode requires at least one region mapping."]
    for index, item in enumerate(mapping, start=1):
        if not isinstance(item, (list, tuple)) or len(item) != 5:
            errors.append(f"Advanced region {index} must contain x1, x2, y1, y2, and weight.")
            continue
        values: list[float] = []
        valid = True
        for value in item:
            try:
                number = float(value)
            except (TypeError, ValueError):
                valid = False
                break
            if not isfinite(number):
                valid = False
                break
            values.append(number)
        if not valid:
            errors.append(f"Advanced region {index} contains a non-numeric value.")
            continue
        x1, x2, y1, y2, weight = values
        if not all(0.0 <= value <= 1.0 for value in (x1, x2, y1, y2)):
            errors.append(f"Advanced region {index} coordinates must stay between 0.0 and 1.0.")
        if x2 <= x1 or y2 <= y1:
            errors.append(f"Advanced region {index} must have positive width and height.")
        if not 0.0 <= weight <= 5.0:
            errors.append(f"Advanced region {index} weight must stay between 0.0 and 5.0.")
    return errors


def _merged_intervals(intervals: Iterable[tuple[float, float]], *, epsilon: float = 1e-9) -> list[tuple[float, float]]:
    ordered = sorted((max(0.0, start), min(1.0, end)) for start, end in intervals if end - start > epsilon)
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + epsilon:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def mapping_covers_canvas(mapping: Any, *, epsilon: float = 1e-7) -> bool:
    """Exact axis-aligned union coverage test over normalized [0, 1]²."""
    if mapping_errors(mapping):
        return False
    rectangles = [[float(value) for value in item] for item in mapping]
    x_edges = sorted({0.0, 1.0, *[rect[0] for rect in rectangles], *[rect[1] for rect in rectangles]})
    for left, right in zip(x_edges, x_edges[1:]):
        if right - left <= epsilon:
            continue
        x_mid = (left + right) / 2.0
        y_intervals = [(rect[2], rect[3]) for rect in rectangles if rect[0] <= x_mid + epsilon and rect[1] >= x_mid - epsilon]
        merged = _merged_intervals(y_intervals, epsilon=epsilon)
        if not merged or merged[0][0] > epsilon or merged[-1][1] < 1.0 - epsilon:
            return False
        cursor = 0.0
        for start, end in merged:
            if start > cursor + epsilon:
                return False
            cursor = max(cursor, end)
        if cursor < 1.0 - epsilon:
            return False
    return True


def auto_layout(count: int, *, direction: str = "Horizontal") -> list[list[float]]:
    count = max(1, min(32, int(count or 1)))
    result: list[list[float]] = []
    for index in range(count):
        start = index / count
        end = (index + 1) / count
        if direction == "Vertical":
            result.append([0.0, 1.0, start, end, 1.0])
        else:
            result.append([start, end, 0.0, 1.0, 1.0])
    return result
