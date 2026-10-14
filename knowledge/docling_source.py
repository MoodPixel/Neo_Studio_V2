from __future__ import annotations

from pathlib import Path
from typing import Any

from .source_contract import build_source_document

DOCLING_PARSER_ID = "docling"


class DoclingSourceUnavailable(RuntimeError):
    pass


class DoclingSourceError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _bbox_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json", exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in ("l", "t", "r", "b", "left", "top", "right", "bottom", "coord_origin"):
        if hasattr(value, key):
            item = getattr(value, key)
            out[key] = getattr(item, "value", item)
    return out or None


def _provenance(item: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prov in list(getattr(item, "prov", None) or []):
        row: dict[str, Any] = {}
        page_no = getattr(prov, "page_no", None)
        if page_no is not None:
            try:
                row["page"] = int(page_no)
            except Exception:
                pass
        bbox = _bbox_dict(getattr(prov, "bbox", None))
        if bbox:
            row["bbox"] = bbox
        charspan = getattr(prov, "charspan", None)
        if charspan is not None:
            if hasattr(charspan, "model_dump"):
                try:
                    row["charspan"] = charspan.model_dump(mode="json", exclude_none=True)
                except Exception:
                    row["charspan"] = str(charspan)
            else:
                row["charspan"] = str(charspan)
        if row:
            rows.append(row)
    return rows


def _table_text(item: Any, doc: Any) -> str:
    for name in ("export_to_markdown", "export_to_text"):
        fn = getattr(item, name, None)
        if not callable(fn):
            continue
        try:
            return _clean(fn(doc=doc))
        except TypeError:
            try:
                return _clean(fn(doc))
            except Exception:
                pass
        except Exception:
            pass
    data = getattr(item, "data", None)
    cells = getattr(data, "table_cells", None) or getattr(data, "cells", None) or []
    return " | ".join(_clean(getattr(cell, "text", "")) for cell in cells if _clean(getattr(cell, "text", "")))


def _caption_text(item: Any, doc: Any) -> str:
    fn = getattr(item, "caption_text", None)
    if callable(fn):
        try:
            return _clean(fn(doc))
        except Exception:
            return ""
    return ""


def _confidence(item: Any) -> float | None:
    for name in ("confidence", "score"):
        value = getattr(item, name, None)
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            pass
    return None


def _block_for_item(
    item: Any,
    level: int,
    doc: Any,
    headings: list[tuple[int, str]],
    index: int,
) -> tuple[dict[str, Any] | None, list[tuple[int, str]]]:
    label = _enum(getattr(item, "label", "")) or item.__class__.__name__.lower()
    text = _clean(getattr(item, "text", ""))
    group_name = _clean(getattr(item, "name", ""))
    if not text and label in {"sheet", "slide", "section"}:
        text = group_name
    class_name = item.__class__.__name__.lower()
    heading_level = getattr(item, "level", None)
    try:
        heading_level = int(heading_level) if heading_level is not None else None
    except Exception:
        heading_level = None

    if label in {"sheet", "slide", "section"} and text:
        kind = label
        resolved_level = max(1, int(level or 1))
        headings = [(lvl, value) for lvl, value in headings if lvl < resolved_level]
        headings.append((resolved_level, text))
    elif "table" in class_name or label == "table":
        text = _table_text(item, doc)
        kind = "table"
    elif label in {"picture", "chart"} or "picture" in class_name:
        caption = _caption_text(item, doc)
        text = caption or text or f"[{label}]"
        kind = "chart" if label == "chart" else "picture"
    elif label in {"section_header", "title"} or "sectionheader" in class_name:
        kind = "heading"
        resolved_level = heading_level or max(1, int(level or 1))
        headings = [(lvl, value) for lvl, value in headings if lvl < resolved_level]
        if text:
            headings.append((resolved_level, text))
    elif label == "caption":
        kind = "caption"
    else:
        kind = label or "paragraph"

    if not text:
        return None, headings

    prov = _provenance(item)
    locator: dict[str, Any] = {
        "kind": "source_item",
        "item_index": index,
        "self_ref": str(getattr(item, "self_ref", "") or ""),
        "label": label,
    }
    if group_name:
        locator["group_name"] = group_name
    if prov:
        locator["provenance"] = prov
        if prov[0].get("page") is not None:
            locator["page"] = prov[0]["page"]
        if prov[0].get("bbox"):
            locator["bbox"] = prov[0]["bbox"]

    metadata: dict[str, Any] = {"docling_label": label}
    confidence = _confidence(item)
    if confidence is not None:
        metadata["parser_confidence"] = confidence

    return {
        "kind": kind,
        "text": text,
        "heading_path": [value for _, value in headings],
        "locator": locator,
        "confidence": confidence,
        "metadata": metadata,
    }, headings


def _extract_blocks(doc: Any) -> tuple[list[dict[str, Any]], list[str]]:
    blocks: list[dict[str, Any]] = []
    headings: list[tuple[int, str]] = []
    warnings: list[str] = []
    seen_refs: set[str] = set()

    def append_item(item: Any, level: int, index: int) -> None:
        nonlocal headings
        if item is None:
            return
        ref = str(getattr(item, "self_ref", "") or "")
        if ref and ref in seen_refs:
            return
        try:
            block, headings = _block_for_item(item, level, doc, headings, index)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"item {ref or index} skipped: {exc}")
            return
        if ref:
            seen_refs.add(ref)
        if block:
            blocks.append(block)

    try:
        iterator = doc.iterate_items(with_groups=True, traverse_pictures=True)
        for index, pair in enumerate(iterator, start=1):
            if not pair:
                warnings.append(f"iterator item {index} was empty")
                continue
            try:
                item, level = pair
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"iterator item {index} malformed: {exc}")
                continue
            append_item(item, int(level or 1), index)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"structured iterator fallback: {exc}")
        fallback_index = len(blocks) + 1
        for collection_name in ("texts", "tables", "pictures"):
            for item in list(getattr(doc, collection_name, None) or []):
                append_item(item, 1, fallback_index)
                fallback_index += 1
    return blocks, warnings


def docling_available() -> tuple[bool, str]:
    try:
        import docling  # type: ignore
        return True, str(getattr(docling, "__version__", "unknown"))
    except Exception as exc:
        return False, str(exc)


def prepare_docling_source(path: Path, *, content_hash: str = "") -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        import docling  # type: ignore
        from docling.datamodel.base_models import InputFormat  # type: ignore
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions  # type: ignore
        from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore
    except Exception as exc:
        raise DoclingSourceUnavailable(f"Docling is not installed in Neo's runtime: {exc}") from exc

    try:
        pdf_options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
        pdf_options.ocr_options = RapidOcrOptions(lang=["iso:en"])
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)})
        try:
            result = converter.convert(str(source), raises_on_error=False)
        except TypeError:
            result = converter.convert(str(source))
    except Exception as exc:
        raise DoclingSourceError(f"Docling conversion failed: {exc}") from exc

    conversion_status = _enum(getattr(result, "status", "success")) or "success"
    if conversion_status not in {"success", "partial_success", "partial"}:
        errors = [str(getattr(error, "error_message", error)) for error in list(getattr(result, "errors", None) or [])]
        raise DoclingSourceError("; ".join(errors) or f"Docling conversion status: {conversion_status}")

    doc = result.document
    blocks, warnings = _extract_blocks(doc)
    version = str(getattr(docling, "__version__", "unknown"))
    page_locator_count = sum(1 for block in blocks if (block.get("locator") or {}).get("page") is not None)
    bbox_locator_count = sum(1 for block in blocks if (block.get("locator") or {}).get("bbox"))
    table_count = sum(1 for block in blocks if block.get("kind") == "table")
    picture_count = sum(1 for block in blocks if block.get("kind") in {"picture", "chart"})
    return build_source_document(
        path=source,
        blocks=blocks,
        parser_id=DOCLING_PARSER_ID,
        parser_version=version,
        parser_mode="advanced",
        status="partial" if conversion_status in {"partial_success", "partial"} else ("extracted" if blocks else "stored_only"),
        content_hash=content_hash,
        attempts=[{"parser_id": DOCLING_PARSER_ID, "version": version, "status": "extracted" if blocks else "stored_only"}],
        warnings=warnings,
        capabilities={
            "hierarchy": True,
            "tables": table_count > 0,
            "pictures": picture_count > 0,
            "page_provenance": page_locator_count > 0,
            "bbox_provenance": bbox_locator_count > 0,
            "ocr": source.suffix.lower() == ".pdf",
            "table_structure": source.suffix.lower() == ".pdf",
        },
        metadata={
            "conversion_status": conversion_status,
            "ocr_engine": "rapidocr" if source.suffix.lower() == ".pdf" else "",
            "ocr_language": "iso:en" if source.suffix.lower() == ".pdf" else "",
            "table_count": table_count,
            "picture_count": picture_count,
        },
    )
