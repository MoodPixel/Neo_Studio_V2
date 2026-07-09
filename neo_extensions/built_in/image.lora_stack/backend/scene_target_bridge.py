from __future__ import annotations

def scene_region_apply_targets(region_count: int = 0) -> list[str]:
    return ["global"] + [f"scene_region_{index + 1}" for index in range(max(0, int(region_count or 0)))]
