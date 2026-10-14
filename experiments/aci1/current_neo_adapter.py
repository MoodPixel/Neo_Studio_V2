from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.assistant.attachments import extract_document_text
from neo_app.knowledge.project_documents import structured_extraction_from_text

from .contracts import ACI1_ARM_CURRENT, normalized_extraction


def prepare_with_current_neo(path: Path) -> dict[str, Any]:
    """Run the ordinary Assistant attachment extractor without improving it.

    ACI-1 intentionally benchmarks the current one-turn Assistant behavior, not
    Project Brain's stronger persistent parser. Extracted legacy text is only
    bridged into structured blocks *after* extraction so both arms can share the
    existing Neo contextual-fragment builder downstream.
    """
    source = Path(path).resolve()
    text, legacy = extract_document_text(source, source.suffix.lower())
    legacy = dict(legacy or {})
    status = str(legacy.get("status") or ("extracted" if text else "stored_only"))
    if text:
        source_kind = source.suffix.lower().lstrip(".") or "legacy_text"
        bridged = structured_extraction_from_text(text, source_kind=source_kind)
        blocks = list(bridged.get("blocks") or [])
        parser = f"assistant_legacy:{legacy.get('method') or legacy.get('encoding') or source_kind}"
    else:
        blocks = []
        parser = f"assistant_legacy:{legacy.get('reason') or 'stored_only'}"
    return normalized_extraction(
        arm=ACI1_ARM_CURRENT,
        source_path=source,
        parser=parser,
        status=status,
        blocks=blocks,
        metadata={"legacy_extraction": legacy, "legacy_text_chars": len(text or "")},
    )
