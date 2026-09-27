from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import ast
import re

from neo_app.knowledge.base import NativeKnowledgeAdapter
from neo_app.knowledge.contracts import ContextRef, SourceLocator, build_projection_bundle, make_native_ref
from neo_app.knowledge.io import paginate, resolve_inside, safe_relative
from ._helpers import compact_text

_CODE_ROOTS = (
    "neo_app", "neo_extensions", "neo_integrations", "neo_scene_director",
    "neo_voice_engine", "neo_prompt_captioning", "neo_manifests", "scripts", "tests",
)
_CODE_EXTENSIONS = {".py", ".js", ".css", ".json", ".yaml", ".yml", ".toml", ".bat", ".sh"}
_JS_SYMBOL_RE = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)|^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z0-9_$]+\s*=>)", re.MULTILINE)


class CodeKnowledgeAdapter(NativeKnowledgeAdapter):
    adapter_id = "neo.code"
    adapter_version = "1.0"
    authority_namespaces = ("repository.file", "repository.symbol", "repository.registry", "repository.test")
    primitive_kinds = ("source", "object", "fact", "edge")
    query_capabilities = NativeKnowledgeAdapter.query_capabilities + ("lookup", "relationships")

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for root_name in _CODE_ROOTS:
            root = self.root_dir / root_name
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in _CODE_EXTENSIONS:
                    continue
                if any(part in {"__pycache__", ".git", "node_modules", "neo_data"} for part in path.parts):
                    continue
                files.append(path)
        for name in ("config.yml", "requirements.txt", "requirements-memory.txt"):
            path = self.root_dir / name
            if path.exists():
                files.append(path)
        return sorted(set(files), key=lambda p: safe_relative(self.root_dir, p))

    def _file_native_id(self, path: Path) -> str:
        return safe_relative(self.root_dir, path)

    def _role_for_path(self, path: Path) -> str:
        rel = safe_relative(self.root_dir, path)
        if rel.startswith("tests/"):
            return "validation_test"
        if rel.startswith("scripts/"):
            return "developer_tooling"
        if rel.startswith("neo_manifests/") or path.suffix.lower() in {".json", ".yaml", ".yml", ".toml"}:
            return "declarative_configuration"
        return "runtime_implementation"

    def _symbols(self, path: Path, text: str) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return []
            symbols: list[dict[str, Any]] = []
            parents: list[tuple[ast.AST, str]] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    symbols.append({"name": node.name, "qualified": node.name, "symbol_type": "class", "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent_name = ""
                    for cls in ast.walk(tree):
                        if isinstance(cls, ast.ClassDef) and node in list(cls.body):
                            parent_name = cls.name
                            break
                    qualified = f"{parent_name}.{node.name}" if parent_name else node.name
                    symbols.append({"name": node.name, "qualified": qualified, "symbol_type": "method" if parent_name else "function", "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            name = target.id
                            if name.isupper() or any(token in name.lower() for token in ("registry", "routes", "matrix", "schema", "config", "profiles", "policy")):
                                symbols.append({"name": name, "qualified": name, "symbol_type": "registry" if any(token in name.lower() for token in ("registry", "routes", "matrix", "profiles")) else "constant", "start_line": getattr(node, "lineno", 1), "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1))})
            return sorted(symbols, key=lambda s: (s["start_line"], s["end_line"]))
        if path.suffix.lower() == ".js":
            symbols = []
            for match in _JS_SYMBOL_RE.finditer(text):
                name = next((g for g in match.groups() if g), "section")
                start = text.count("\n", 0, match.start()) + 1
                symbols.append({"name": name, "qualified": name, "symbol_type": "javascript", "start_line": start, "end_line": start})
            return symbols
        return []

    def enumerate_authorities(self, context: dict[str, Any] | ContextRef | None = None, *, cursor: str = "", limit: int = 100) -> dict[str, Any]:
        items = []
        for path in self._files():
            rel = self._file_native_id(path)
            ns = "repository.test" if rel.startswith("tests/") else "repository.file"
            items.append({"native_ref": {"adapter_id": self.adapter_id, "authority_namespace": ns, "native_id": rel, "resolver_kind": "file", "resolver_key": {"path": rel}}, "title": rel, "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        page, next_cursor, total = paginate(items, cursor, limit)
        return {"ok": True, "status": "ready", "adapter_id": self.adapter_id, "items": page, "count": len(page), "total": total, "next_cursor": next_cursor}

    def _resolve_file(self, native: dict[str, Any]) -> tuple[Path | None, int, int, str]:
        resolver = native.get("resolver_key") if isinstance(native.get("resolver_key"), dict) else {}
        path = resolve_inside(self.root_dir, str(resolver.get("path") or native.get("native_id") or ""))
        if not path or not path.exists() or not path.is_file():
            return None, 0, 0, ""
        return path, int(resolver.get("start_line") or 0), int(resolver.get("end_line") or 0), str(resolver.get("symbol") or "")

    def project(self, native_ref: dict[str, Any], context: dict[str, Any] | ContextRef | None = None) -> dict[str, Any]:
        ref = native_ref.get("native_ref") if isinstance(native_ref.get("native_ref"), dict) else native_ref
        path, start, end, symbol = self._resolve_file(ref)
        if not path:
            return {"ok": False, "status": "missing", "adapter_id": self.adapter_id}
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if start:
            end = end or start
            snippet = "\n".join(lines[start - 1:end])
            native_id = str(ref.get("native_id") or f"{safe_relative(self.root_dir, path)}::{symbol or start}")
            namespace = str(ref.get("authority_namespace") or "repository.symbol")
            kind = "object"
            title = f"{safe_relative(self.root_dir, path)} · {symbol or f'lines {start}-{end}'}"
            locator = SourceLocator(kind="symbol_range", path=safe_relative(self.root_dir, path), fields={"start_line": start, "end_line": end, "symbol": symbol})
            revision_token = snippet
        else:
            native_id = self._file_native_id(path)
            namespace = "repository.test" if native_id.startswith("tests/") else "repository.file"
            kind = "source"
            title = native_id
            snippet = text
            locator = SourceLocator(kind="line_range", path=native_id, fields={"start_line": 1, "end_line": max(1, len(lines))})
            revision_token = text
        role = self._role_for_path(path)
        native = make_native_ref(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, resolver_kind="repository_range" if start else "file", resolver_key={"path": safe_relative(self.root_dir, path), "start_line": start, "end_line": end, "symbol": symbol}, native_schema_id="neo.repository.source.v1")
        bundle = build_projection_bundle(adapter_id=self.adapter_id, authority_namespace=namespace, native_id=native_id, kind=kind, title=title, search_text=snippet, native_ref=native, revision_token=revision_token, source_locator=locator, context=self.context(context), evidence_role=role, derivation="direct", origin="repository", source_integrity="verified", claim_type="runtime_implementation" if role == "runtime_implementation" else "validation_evidence" if role == "validation_test" else "developer_reference", epistemic_state="established", lifecycle_state="active", importance="high" if role == "runtime_implementation" else "normal", payload={"path": safe_relative(self.root_dir, path), "start_line": start or 1, "end_line": end or len(lines), "symbol": symbol, "text": snippet})
        return {"ok": True, **bundle}

    def resolve(self, ref: dict[str, Any]) -> dict[str, Any]:
        native = ref.get("native_ref") if isinstance(ref.get("native_ref"), dict) else ref
        path, start, end, symbol = self._resolve_file(native)
        if not path:
            return {"ok": False, "status": "missing", "adapter_id": self.adapter_id}
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        snippet = "\n".join(lines[start - 1:(end or start)]) if start else text
        return {"ok": True, "status": "verified", "adapter_id": self.adapter_id, "path": safe_relative(self.root_dir, path), "start_line": start or 1, "end_line": end or len(lines), "symbol": symbol, "text": snippet}

    def structured_lookup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        query = str(data.get("query") or "").strip()
        q = query.lower()
        limit = max(1, min(int(data.get("limit") or 50), 200))
        items: list[dict[str, Any]] = []
        for path in self._files():
            rel = safe_relative(self.root_dir, path)
            if q and q in rel.lower():
                items.append({"authority_namespace": "repository.test" if rel.startswith("tests/") else "repository.file", "native_id": rel, "title": rel, "path": rel, "evidence_role": self._role_for_path(path)})
            if len(items) >= limit:
                break
            if path.suffix.lower() not in {".py", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if q:
                q_tokens = [token for token in re.split(r"[^a-z0-9_$]+", q) if token]
                searchable = f"{rel.lower()}\n{text.lower()}"
                if q not in searchable and not (q_tokens and all(token in searchable for token in q_tokens)):
                    continue
            for symbol in self._symbols(path, text):
                symbol_hay = f"{symbol['name']} {symbol['qualified']} {rel}".lower()
                if q and q not in symbol_hay:
                    continue
                native_id = f"{rel}::{symbol['qualified']}"
                items.append({"authority_namespace": "repository.registry" if symbol["symbol_type"] == "registry" else "repository.symbol", "native_id": native_id, "title": native_id, "path": rel, "symbol": symbol["qualified"], "symbol_type": symbol["symbol_type"], "start_line": symbol["start_line"], "end_line": symbol["end_line"], "native_ref": {"adapter_id": self.adapter_id, "authority_namespace": "repository.registry" if symbol["symbol_type"] == "registry" else "repository.symbol", "native_id": native_id, "resolver_kind": "repository_symbol", "resolver_key": {"path": rel, "start_line": symbol["start_line"], "end_line": symbol["end_line"], "symbol": symbol["qualified"]}}})
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        return {"ok": True, "adapter_id": self.adapter_id, "query": query, "items": items, "count": len(items)}

    def expand_relationships(self, ref: dict[str, Any], *, relation_filter: list[str] | None = None, max_hops: int = 1) -> dict[str, Any]:
        resolved = self.resolve(ref)
        if not resolved.get("ok"):
            return {"ok": False, "status": "missing", "items": [], "count": 0}
        path = resolve_inside(self.root_dir, resolved.get("path") or "")
        if not path or path.suffix.lower() != ".py":
            return {"ok": True, "items": [], "count": 0, "max_hops": 1}
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            return {"ok": True, "items": [], "count": 0, "max_hops": 1}
        items = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    items.append({"relation_type": "software.imports", "source_id": safe_relative(self.root_dir, path), "target_id": alias.name})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    items.append({"relation_type": "software.imports", "source_id": safe_relative(self.root_dir, path), "target_id": f"{module}.{alias.name}".strip(".")})
        filters = set(relation_filter or [])
        if filters:
            items = [item for item in items if item["relation_type"] in filters]
        return {"ok": True, "adapter_id": self.adapter_id, "items": items, "count": len(items), "max_hops": 1}
