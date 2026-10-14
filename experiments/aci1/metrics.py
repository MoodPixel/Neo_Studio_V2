from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any


def _lower(value: Any) -> str:
    return str(value or "").casefold()


def _all_text(extraction: dict[str, Any]) -> str:
    return "\n".join(str(item.get("text") or "") for item in (extraction.get("blocks") or []) if isinstance(item, dict))


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _locator_has(locator: dict[str, Any], key: str) -> bool:
    if key in locator and _present(locator.get(key)):
        return True
    nested = locator.get("provenance")
    if isinstance(nested, list):
        return any(isinstance(item, dict) and _present(item.get(key)) for item in nested)
    return False


def extraction_metrics(extraction: dict[str, Any], truth: dict[str, Any] | None = None) -> dict[str, Any]:
    blocks = [item for item in (extraction.get("blocks") or []) if isinstance(item, dict)]
    kinds = Counter(str(item.get("kind") or "unknown") for item in blocks)
    heading_blocks = sum(1 for item in blocks if str(item.get("kind") or "") in {"heading", "section_header", "title"})
    blocks_with_heading_context = sum(1 for item in blocks if item.get("heading_path"))
    table_blocks = sum(1 for item in blocks if "table" in str(item.get("kind") or ""))
    picture_blocks = sum(1 for item in blocks if str(item.get("kind") or "") in {"picture", "chart", "image", "figure"})
    locator_blocks = sum(1 for item in blocks if isinstance(item.get("locator"), dict) and item.get("locator"))
    page_blocks = sum(1 for item in blocks if _locator_has(dict(item.get("locator") or {}), "page"))
    bbox_blocks = sum(1 for item in blocks if _locator_has(dict(item.get("locator") or {}), "bbox"))

    truth = truth if isinstance(truth, dict) else {}
    required_terms = [str(term) for term in (truth.get("required_terms") or []) if str(term).strip()]
    haystack = _lower(_all_text(extraction))
    found_terms = [term for term in required_terms if _lower(term) in haystack]
    missing_terms = [term for term in required_terms if _lower(term) not in haystack]
    term_recall = (len(found_terms) / len(required_terms)) if required_terms else None

    return {
        "status": extraction.get("status"),
        "block_count": len(blocks),
        "chars": int(extraction.get("chars") or sum(len(str(item.get("text") or "")) for item in blocks)),
        "block_kinds": dict(sorted(kinds.items())),
        "heading_blocks": heading_blocks,
        "blocks_with_heading_context": blocks_with_heading_context,
        "table_blocks": table_blocks,
        "picture_blocks": picture_blocks,
        "locator_coverage": round(locator_blocks / len(blocks), 4) if blocks else 0.0,
        "page_provenance_coverage": round(page_blocks / len(blocks), 4) if blocks else 0.0,
        "bbox_provenance_coverage": round(bbox_blocks / len(blocks), 4) if blocks else 0.0,
        "required_terms": required_terms,
        "found_terms": found_terms,
        "missing_terms": missing_terms,
        "required_term_recall": round(term_recall, 4) if term_recall is not None else None,
    }


def retrieval_query_score(query_truth: dict[str, Any], results: list[dict[str, Any]], k: int) -> dict[str, Any]:
    top = [item for item in results[: max(1, int(k))] if isinstance(item, dict)]
    texts = [_lower(item.get("content") or item.get("text") or "") for item in top]
    any_terms = [str(item) for item in (query_truth.get("must_retrieve_any") or []) if str(item).strip()]
    all_terms = [str(item) for item in (query_truth.get("must_retrieve_all") or []) if str(item).strip()]
    any_ok = True if not any_terms else any(_lower(term) in text for term in any_terms for text in texts)
    all_ok = True if not all_terms else all(any(_lower(term) in text for text in texts) for term in all_terms)

    expected_locator = query_truth.get("expected_locator") if isinstance(query_truth.get("expected_locator"), dict) else {}
    locator_ok = True
    if expected_locator:
        locator_ok = False
        for result in top:
            locators = result.get("source_locators") or []
            if isinstance(result.get("source_locator"), dict):
                locators = list(locators) + [result.get("source_locator")]
            for locator in locators:
                if not isinstance(locator, dict):
                    continue
                flat = json.dumps(locator, ensure_ascii=False).casefold()
                if all(_lower(value) in flat for value in expected_locator.values() if _present(value)):
                    locator_ok = True
                    break
            if locator_ok:
                break

    passed = bool(any_ok and all_ok and locator_ok)
    first_hit_rank = None
    for rank, text in enumerate(texts, start=1):
        if (not any_terms or any(_lower(term) in text for term in any_terms)) and (not all_terms or all(_lower(term) in text for term in all_terms)):
            first_hit_rank = rank
            break
    reciprocal_rank = (1.0 / first_hit_rank) if first_hit_rank else 0.0
    return {
        "passed": passed,
        "any_term_match": any_ok,
        "all_term_match": all_ok,
        "locator_match": locator_ok,
        "first_hit_rank": first_hit_rank,
        "reciprocal_rank": round(reciprocal_rank, 6),
    }


def aggregate_query_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"query_count": 0, "recall": None, "mrr": None}
    passed = sum(1 for item in items if item.get("passed"))
    mrr = sum(float(item.get("reciprocal_rank") or 0.0) for item in items) / len(items)
    return {
        "query_count": len(items),
        "recall": round(passed / len(items), 4),
        "mrr": round(mrr, 6),
    }


def safe_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None
