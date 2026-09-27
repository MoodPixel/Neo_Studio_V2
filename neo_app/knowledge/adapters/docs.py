from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import re

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref
from neo_app.knowledge.io import paginate, resolve_inside, safe_relative
from ._helpers import compact_text

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class DocumentationKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.docs"
    adapter_version = "1.0"
    authority_namespaces = ("guide.document", "guide.section", "system_record.document", "system_record.section", "public_doc.document", "public_doc.section")
    primitive_kinds = ("source", "fragment", "revision")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup",)

    def _docs(self) -> list[Path]:
        paths: list[Path] = []
        for base in (self.root_dir / "guides", self.root_dir / "neo_system_records"):
            if base.exists():
                paths.extend(path for path in base.rglob("*.md") if path.is_file())
        for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"):
            path = self.root_dir / name
            if path.exists(): paths.append(path)
        for root_name in ("neo_integrations", "neo_voice_engine", "neo_prompt_captioning", "neo_scene_director"):
            root = self.root_dir / root_name
            if root.exists(): paths.extend(path for path in root.rglob("README.md") if path.is_file())
        return sorted(set(paths), key=lambda p: safe_relative(self.root_dir, p))

    def _frontmatter(self, text: str) -> dict[str, str]:
        match = _FRONTMATTER_RE.match(text)
        if not match: return {}
        data: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("-"):
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip('"\'')
        return data

    def _title(self, text: str, fallback: str) -> str:
        for line in text.splitlines()[:80]:
            match = _HEADING_RE.match(line.strip())
            if match:
                return match.group(2).strip()
        return fallback

    def _classification(self, path: Path, text: str) -> dict[str, str]:
        rel = safe_relative(self.root_dir, path)
        fm = self._frontmatter(text)
        status = str(fm.get("status") or "").lower()
        header = text[:1800]
        lower = header.lower()
        explicit_status = ""
        match = re.search(r"(?im)^\s*(?:\*\*status:\*\*|status:)\s*([a-z_ -]+)\s*$", header)
        if match:
            explicit_status = match.group(1).strip().lower().replace(" ", "_")
        explicitly_superseded = bool(
            status in {"superseded", "deprecated", "historical"}
            or explicit_status in {"superseded", "deprecated", "historical", "historical_record"}
            or re.search(r"(?im)^\s*this\s+(?:record|document|guide)\s+is\s+superseded\s+by\b", header)
        )
        if rel.startswith("guides/"):
            role = "experimental_guide" if status in {"experimental", "draft"} else "current_guide"
            lifecycle = "draft" if role == "experimental_guide" else "active"
            namespace = "guide.document"
        elif rel.startswith("neo_system_records/"):
            namespace = "system_record.document"
            if "/07_CHANGELOG/" in f"/{rel}":
                role, lifecycle = "changelog", "active"
            elif "validation" in path.name.lower() or "/06_VALIDATION/" in f"/{rel}":
                role, lifecycle = "validation_evidence", "active"
            elif "current authority" in lower or ("architecture" in rel.lower() and any(token in path.name for token in ("NKB4", "NKB5", "NKB6"))):
                role, lifecycle = "current_architecture", "active"
            elif explicitly_superseded:
                role, lifecycle = "historical_record", "superseded"
            elif explicit_status in {"historical", "legacy"} or "legacy" in path.name.lower():
                role, lifecycle = "historical_record", "active"
            elif "audit" in path.name.lower() or "architecture" in rel.lower():
                role, lifecycle = "architecture_record", "active"
            else:
                role, lifecycle = "system_record", "active"
        else:
            namespace = "public_doc.document"
            if path.name == "SECURITY.md": role = "security_policy"
            elif path.name == "CONTRIBUTING.md": role = "contributor_policy"
            elif path.name == "CODE_OF_CONDUCT.md": role = "community_policy"
            else: role = "public_reference"
            lifecycle = "active"
        return {"namespace": namespace, "evidence_role": role, "lifecycle": lifecycle, "status": status}

    def _sections(self, text: str) -> list[dict[str, Any]]:
        lines = text.splitlines()
        starts: list[tuple[int, int, str]] = []
        for idx, line in enumerate(lines, start=1):
            match = _HEADING_RE.match(line.strip())
            if match:
                starts.append((idx, len(match.group(1)), match.group(2).strip()))
        sections = []
        for i, (start, level, title) in enumerate(starts):
            end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else len(lines)
            sections.append({"start_line": start, "end_line": end, "level": level, "title": title, "text": "\n".join(lines[start - 1:end]).strip()})
        return sections

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        items = []
        for path in self._docs():
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = safe_relative(self.root_dir, path); classification = self._classification(path, text)
            items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": classification["namespace"], "native_id": rel, "resolver_kind": "file", "resolver_key": {"path": rel}}, "title": self._title(text, path.name), "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "evidence_role": classification["evidence_role"], "lifecycle": classification["lifecycle"]})
        page, next_cursor, total = paginate(items, cursor, limit)
        return {"ok": True, "status": "ready", "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _path_from_ref(self, ref: dict[str, Any]) -> Path | None:
        resolver = ref.get("resolver_key") if isinstance(ref.get("resolver_key"), dict) else {}
        return resolve_inside(self.root_dir, str(resolver.get("path") or ref.get("native_id") or ""))

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        path = self._path_from_ref(ref)
        if not path or not path.exists() or path.suffix.lower() != ".md": return {"ok": False, "status": "missing", "adapter_id": self.adapter_id}
        text = path.read_text(encoding="utf-8", errors="replace"); rel = safe_relative(self.root_dir, path); cls = self._classification(path, text)
        title = self._title(text, path.name)
        native = make_native_ref(adapter_id=self.adapter_id, authority_namespace=cls["namespace"], native_id=rel, resolver_kind="file", resolver_key={"path": rel}, native_schema_id="neo.documentation.source.v1")
        doc_bundle = build_projection_bundle(adapter_id=self.adapter_id, authority_namespace=cls["namespace"], native_id=rel, kind="source", title=title, search_text=text, native_ref=native, revision_token=text, source_locator=SourceLocator(kind="line_range", path=rel, fields={"start_line": 1, "end_line": max(1, len(text.splitlines()))}), context=self.context(context), evidence_role=cls["evidence_role"], derivation="direct", origin="documentation", source_integrity="verified", claim_type="documentation", epistemic_state="established", lifecycle_state=cls["lifecycle"], temporal={"source_modified_at": str(path.stat().st_mtime_ns)}, importance="high" if cls["evidence_role"] in {"current_architecture", "current_guide", "security_policy"} else "normal", compatibility={"frontmatter": self._frontmatter(text)}, payload={"path": rel, "title": title})
        section_projections = []
        section_namespace = cls["namespace"].replace(".document", ".section")
        for section in self._sections(text):
            section_id = f"{rel}#L{section['start_line']}-L{section['end_line']}"
            sref = make_native_ref(adapter_id=self.adapter_id, authority_namespace=section_namespace, native_id=section_id, resolver_kind="repository_range", resolver_key={"path": rel, "start_line": section["start_line"], "end_line": section["end_line"], "heading": section["title"]}, native_schema_id="neo.documentation.section.v1")
            section_projections.append(build_projection_bundle(adapter_id=self.adapter_id, authority_namespace=section_namespace, native_id=section_id, kind="fragment", title=f"{title} · {section['title']}", search_text=section["text"], native_ref=sref, revision_token=section["text"], source_locator=SourceLocator(kind="heading_path", path=rel, fields={"heading": section["title"], "start_line": section["start_line"], "end_line": section["end_line"]}), context=self.context(context), evidence_role=cls["evidence_role"], derivation="direct", origin="documentation", source_integrity="verified", claim_type="documentation_section", epistemic_state="established", lifecycle_state=cls["lifecycle"], payload={"path": rel, **section}))
        return {"ok": True, **doc_bundle, "section_projections": section_projections}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        path = self._path_from_ref(native)
        if not path or not path.exists(): return {"ok": False, "status": "missing", "adapter_id": self.adapter_id}
        text = path.read_text(encoding="utf-8", errors="replace"); resolver = native.get("resolver_key") if isinstance(native.get("resolver_key"), dict) else {}
        start = int(resolver.get("start_line") or 0); end = int(resolver.get("end_line") or 0); lines = text.splitlines()
        selected = "\n".join(lines[start - 1:end]) if start else text
        return {"ok": True, "status": "verified", "adapter_id": self.adapter_id, "path": safe_relative(self.root_dir, path), "start_line": start or 1, "end_line": end or len(lines), "heading": resolver.get("heading") or "", "text": selected, "classification": self._classification(path, text)}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}; q = str(data.get("query") or "").lower().strip(); limit = max(1, min(int(data.get("limit") or 50), 200)); items = []
        for path in self._docs():
            text = path.read_text(encoding="utf-8", errors="replace"); rel = safe_relative(self.root_dir, path)
            if q and q not in rel.lower() and q not in text.lower():
                continue
            cls = self._classification(path, text); title = self._title(text, path.name)
            for section in self._sections(text) or [{"title": title, "start_line": 1, "end_line": len(text.splitlines()), "text": text}]:
                hay = compact_text(rel, title, section["title"], section["text"]).lower()
                if q and q not in hay: continue
                native_id = f"{rel}#L{section['start_line']}-L{section['end_line']}"
                items.append({"authority_namespace": cls["namespace"].replace(".document", ".section"), "native_id": native_id, "title": f"{title} · {section['title']}", "path": rel, "start_line": section["start_line"], "end_line": section["end_line"], "evidence_role": cls["evidence_role"], "lifecycle": cls["lifecycle"], "native_ref": {"adapter_id": self.adapter_id, "authority_namespace": cls["namespace"].replace(".document", ".section"), "native_id": native_id, "resolver_kind": "repository_range", "resolver_key": {"path": rel, "start_line": section["start_line"], "end_line": section["end_line"], "heading": section["title"]}}})
                if len(items) >= limit: return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items)}
