from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any


def _tokenize_fts_query(text: str) -> str:
    tokens = re.findall(r"[\w'-]+", str(text or ""), flags=re.UNICODE)
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:24])


def _rrf(rankings: list[list[str]], *, weights: list[float] | None = None, constant: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    weights = weights or [1.0] * len(rankings)
    for lane_index, ranked_ids in enumerate(rankings):
        weight = float(weights[lane_index] if lane_index < len(weights) else 1.0)
        for rank, item_id in enumerate(ranked_ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + weight / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class StrictSentenceTransformersBackend:
    """Controlled ACI-1 reference semantic backend.

    No fallback is permitted. If either model fails to load or returns invalid
    output, the semantic part of the bake-off must fail rather than comparing
    unequal retrieval stacks.
    """

    def __init__(self, embedding_model: str, reranker_model: str, *, device: str | None = None) -> None:
        if not embedding_model or not reranker_model:
            raise ValueError("ACI-1 semantic evaluation requires explicit embedding and reranker model IDs/paths")
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer  # type: ignore
        except Exception as exc:
            raise RuntimeError("sentence-transformers is required for ACI-1 semantic evaluation") from exc
        kwargs = {"device": device} if device else {}
        self.embedding_model_id = embedding_model
        self.reranker_model_id = reranker_model
        self.embedding_model = SentenceTransformer(embedding_model, **kwargs)
        self.reranker_model = CrossEncoder(reranker_model, **kwargs)

    @staticmethod
    def _rows(value: Any) -> list[list[float]]:
        try:
            if hasattr(value, "detach"):
                value = value.detach().cpu().tolist()
            elif hasattr(value, "tolist"):
                value = value.tolist()
        except Exception:
            pass
        rows = value if isinstance(value, list) else list(value)
        if rows and not isinstance(rows[0], (list, tuple)):
            rows = [rows]
        return [[float(cell) for cell in row] for row in rows]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.embedding_model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return self._rows(vectors)

    def embed_query(self, query: str) -> list[float]:
        prompts = getattr(self.embedding_model, "prompts", {}) or {}
        kwargs: dict[str, Any] = {"normalize_embeddings": True, "convert_to_numpy": True}
        if "query" in prompts:
            kwargs["prompt_name"] = "query"
        vectors = self.embedding_model.encode([query], **kwargs)
        rows = self._rows(vectors)
        return rows[0] if rows else []

    def rerank(self, query: str, candidates: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        if not candidates:
            return []
        pairs = [(query, str(item.get("content") or "")) for item in candidates]
        scores = self.reranker_model.predict(pairs)
        try:
            if hasattr(scores, "tolist"):
                scores = scores.tolist()
        except Exception:
            pass
        scored: list[dict[str, Any]] = []
        for item, score in zip(candidates, scores):
            clone = dict(item)
            try:
                clone["rerank_score"] = float(score)
            except Exception:
                clone["rerank_score"] = 0.0
            scored.append(clone)
        scored.sort(key=lambda item: float(item.get("rerank_score") or 0.0), reverse=True)
        return scored[: max(1, int(top_n))]


class HybridBenchmarkIndex:
    """Fixture-local SQLite FTS + Chroma dense retrieval with weighted RRF."""

    def __init__(self, *, root: Path, collection_name: str, backend: StrictSentenceTransformersBackend) -> None:
        try:
            import chromadb  # type: ignore
        except Exception as exc:
            raise RuntimeError("chromadb is required for ACI-1 semantic evaluation") from exc
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.sqlite = sqlite3.connect(":memory:")
        try:
            self.sqlite.execute("CREATE VIRTUAL TABLE fragments_fts USING fts5(fragment_id UNINDEXED, content, title)")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("SQLite FTS5 is required for the controlled ACI-1 retrieval stack") from exc
        chroma_root = self.root / "chroma"
        chroma_root.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(chroma_root))
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", collection_name)[:63]
        try:
            self.client.delete_collection(name=safe_name)
        except Exception:
            pass
        self.collection = self.client.create_collection(name=safe_name, metadata={"hnsw:space": "cosine"})
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, fragments: list[dict[str, Any]]) -> dict[str, Any]:
        texts = [str(item.get("search_text") or item.get("text") or "") for item in fragments]
        vectors = self.backend.embed_documents(texts) if texts else []
        if len(vectors) != len(fragments):
            raise RuntimeError("Embedding result count does not match ACI-1 fragment count")
        if vectors and not vectors[0]:
            raise RuntimeError("Embedding model returned an empty vector")
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        for index, (fragment, text, vector) in enumerate(zip(fragments, texts, vectors), start=1):
            fragment_id = str(fragment.get("fragment_key") or f"f_{index}_{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}")
            ids.append(fragment_id)
            docs.append(text)
            metas.append({"fragment_id": fragment_id, "index": index})
            self.items[fragment_id] = {**fragment, "fragment_id": fragment_id, "content": text}
            self.sqlite.execute(
                "INSERT INTO fragments_fts(fragment_id, content, title) VALUES (?, ?, ?)",
                (fragment_id, text, str(fragment.get("title") or "")),
            )
        self.sqlite.commit()
        if ids:
            # ACI-1 collections are fixture/arm-local; overwrite safely on reruns.
            try:
                self.collection.delete(ids=ids)
            except Exception:
                pass
            self.collection.upsert(ids=ids, embeddings=vectors, documents=docs, metadatas=metas)
        return {"fragment_count": len(ids), "embedding_dimension": len(vectors[0]) if vectors else 0}

    def _fts(self, query: str, limit: int) -> list[str]:
        expression = _tokenize_fts_query(query)
        if not expression:
            return []
        rows = self.sqlite.execute(
            "SELECT fragment_id, bm25(fragments_fts) AS score FROM fragments_fts WHERE fragments_fts MATCH ? ORDER BY score LIMIT ?",
            (expression, max(1, int(limit))),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _dense(self, query: str, limit: int) -> list[str]:
        query_vector = self.backend.embed_query(query)
        if not query_vector:
            raise RuntimeError("Embedding model returned an empty query vector")
        result = self.collection.query(query_embeddings=[query_vector], n_results=max(1, int(limit)), include=["distances"])
        nested = result.get("ids") or []
        return [str(item) for item in (nested[0] if nested else [])]

    def search(self, query: str, *, candidate_k: int = 24, top_k: int = 6) -> list[dict[str, Any]]:
        if not self.items:
            return []
        dense_ids = self._dense(query, candidate_k)
        fts_ids = self._fts(query, candidate_k)
        fused = _rrf([dense_ids, fts_ids], weights=[1.0, 1.0])
        candidates: list[dict[str, Any]] = []
        for item_id, fusion_score in fused[: max(candidate_k, top_k)]:
            item = dict(self.items.get(item_id) or {})
            if not item:
                continue
            item["rrf_score"] = fusion_score
            candidates.append(item)
        return self.backend.rerank(query, candidates, top_n=top_k)

    def close(self) -> None:
        self.sqlite.close()
