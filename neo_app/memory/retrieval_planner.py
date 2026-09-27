from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.admin.semantic_engine import rerank_results
from neo_app.context_identity import resolve_canonical_identity, is_builtin_scope
from neo_app.knowledge.project_documents import PROJECT_DOCUMENT_ADAPTER_ID, classify_fragment_epistemics, resolve_project_document_ref
from neo_app.knowledge.service import NativeKnowledgeService

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"

RETRIEVAL_PLANNER_SCHEMA_ID = "neo.memory.unified_retrieval_planner.v1"
RETRIEVAL_PLANNER_PHASE = "NKB-8"
RRF_K = 60.0

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "what", "when", "where", "which", "who", "why", "how",
    "can", "could", "would", "should", "does", "did", "have", "has", "had", "into", "about", "please", "help", "tell",
    "show", "find", "give", "make", "use", "using", "used", "our", "your", "their", "them", "they", "you", "are", "was",
    "were", "is", "of", "to", "in", "on", "at", "a", "an", "it", "me", "my", "we", "i", "neo", "studio", "assistant",
}

_CREATIVE_TERMS = (
    "create", "write", "draft", "brainstorm", "invent", "imagine", "suggest", "idea", "ideas", "generate", "design", "continue the story",
)
_CANON_TERMS = (
    "canon", "established", "according to", "project context", "story bible", "character bible", "lore", "what happened", "who is",
    "relationship", "works for", "job", "occupation", "chapter", "episode", "timeline",
)
_HISTORY_TERMS = (
    "last", "previous", "history", "past", "used before", "generated before", "saved", "metadata", "sidecar", "replay", "inspector",
    "seed", "settings worked", "output", "outputs", "yesterday", "earlier",
)
_VALIDATION_TERMS = ("test", "tests", "validated", "validation", "regression", "passed", "failed", "coverage")
_HISTORICAL_DESIGN_TERMS = ("why was", "why did", "introduced", "phase", "historical", "previous architecture", "used to", "changelog")
_ADMIN_TERMS = ("admin", "control center", "memory engine", "provider", "backend", "extension registry", "health", "configuration", "config")
_USAGE_TERMS = ("how do i", "how to", "setup", "set up", "configure", "supported", "option", "parameter", "parameters", "tab", "guide")

_SURFACE_ADAPTER = {
    "roleplay": "neo.roleplay",
    "image": "neo.image",
    "video": "neo.video",
    "voice": "neo.voice",
    "prompt_captioning": "neo.prompt_captioning",
    "prompt": "neo.prompt_captioning",
    "caption": "neo.prompt_captioning",
    "captioning": "neo.prompt_captioning",
}

_NATIVE_ROLE_WEIGHTS = {
    "project_source": 1.00,
    "runtime_implementation": 1.00,
    "native_execution_record": 0.98,
    "roleplay_canon": 0.98,
    "current_guide": 0.94,
    "current_architecture": 0.92,
    "validation_test": 0.82,
    "developer_reference": 0.72,
    "historical_record": 0.42,
    "generated_content": 0.30,
}


def _clean(value: Any, limit: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _hash(*parts: Any, length: int = 20) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_@#.+-]{2,}", str(value or ""))]


def _keywords(value: str, *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in _tokens(value):
        low = token.lower()
        if low in _STOPWORDS or len(low) < 3 or low in seen:
            continue
        seen.add(low)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _named_phrases(value: str) -> list[str]:
    phrases: list[str] = []
    for quoted in re.findall(r'["“]([^"”]{2,80})["”]', value or ""):
        phrase = _clean(quoted, 80)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    for match in re.findall(r"\b(?:[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,3})\b", value or ""):
        phrase = _clean(match, 80)
        if phrase and phrase.lower() not in _STOPWORDS and phrase.lower() not in {"neo", "assistant"} and phrase not in phrases:
            phrases.append(phrase)
    for route in re.findall(r"/(?:api|neo|memory|admin|assistant)/[A-Za-z0-9_./{}-]+", value or ""):
        if route not in phrases:
            phrases.append(route)
    for ident in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b|\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\)", value or ""):
        phrase = ident.split("(", 1)[0]
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases[:8]


def _normalize_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _safe_project_term(value: Any) -> str:
    text = _clean(value, 90).strip(" #*`_:-—–")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) < 3 or len(text) > 80:
        return ""
    if "/" in text or "\\" in text or "http" in text.lower():
        return ""
    tokens = re.findall(r"[A-Za-z0-9'’-]+", text)
    if not tokens or len(tokens) > 6 or not any(any(ch.isalpha() for ch in token) for token in tokens):
        return ""
    return text


def _query_phrase_candidates(query: str, named: list[str], keywords: list[str]) -> list[str]:
    out: list[str] = []
    def add(value: Any) -> None:
        phrase = _safe_project_term(value)
        if phrase and _normalize_phrase(phrase) not in {_normalize_phrase(item) for item in out}:
            out.append(phrase)

    for value in named:
        add(value)
    text = str(query or "").strip()
    for pattern in (
        r"\b(?:who|what|where|when)\s+(?:is|are|was|were)\s+(.+?)(?:[?.!]|$)",
        r"\btell\s+me\s+about\s+(.+?)(?:[?.!]|$)",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            add(match.group(1))
    clean_keywords = [str(item) for item in keywords if str(item or "").strip()]
    for n in (4, 3, 2):
        for i in range(0, max(0, len(clean_keywords) - n + 1)):
            add(" ".join(clean_keywords[i:i+n]))
    for item in clean_keywords:
        if len(item) >= 5:
            add(item)
    return out[:14]


def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    low = str(text or "").lower()
    return any(term in low for term in terms)


def _looks_like_code_query(query: str) -> bool:
    text = str(query or "")
    low = text.lower()
    strong = (
        ".py", ".js", ".css", ".html", "function ", "class ", "def ", "import ", "api/", "/api/", "traceback",
        "repository", "repo", "source code", "runtime code", "implementation", "codebase", "symbol", "method ", "endpoint",
    )
    if any(term in low for term in strong):
        return True
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\)", text):
        return True
    if re.search(r"\b(?:neo_app|neo_extensions|tests|scripts)/[\w./-]+", text):
        return True
    # Do not treat generic words such as "route" or "file" as code intent by themselves.
    return False


def _surface_from_query(query: str, active_surface: str) -> str:
    low = str(query or "").lower()
    for surface, markers in (
        ("roleplay", ("roleplay", "character memory", "scene memory", "scene packet")),
        ("image", ("image", "picture", "png", "checkpoint", "lora", "sampler")),
        ("video", ("video", "clip", "frame", "motion", "minimax", "wan")),
        ("voice", ("voice", "tts", "speaker", "audio", "qwen3-tts")),
        ("prompt_captioning", ("prompt", "caption", "captioning", "saved prompt")),
    ):
        if any(marker in low for marker in markers):
            return surface
    return active_surface or "assistant"


def _score01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    if isinstance(value, dict):
        return value
    raw = item.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    if raw:
        try:
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}

def _item_epistemics(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
    semantic = evidence.get("semantic") if isinstance(evidence.get("semantic"), dict) else {}
    role = str(metadata.get("epistemic_role") or item.get("epistemic_role") or "").strip()
    state = str(metadata.get("epistemic_state") or semantic.get("epistemic_state") or item.get("epistemic_state") or "").strip()
    supports = metadata.get("supports_positive_claims")
    if role and state and supports is not None:
        return {
            "epistemic_role": role,
            "epistemic_state": state,
            "supports_positive_claims": bool(supports),
            "classification_reason": "stored_projection",
        }

    title = str(item.get("title") or "")
    content = str(item.get("content") or item.get("snippet") or "")
    heading_path = metadata.get("heading_path") if isinstance(metadata.get("heading_path"), list) else []
    classified = classify_fragment_epistemics(title=title, heading_path=[str(v) for v in heading_path], text=content)
    if state and state != "established" and classified.get("epistemic_state") == "established":
        classified["epistemic_state"] = state
    return classified


def _answer_target_terms(plan: dict[str, Any]) -> list[str]:
    normalization = plan.get("query_normalization") if isinstance(plan.get("query_normalization"), dict) else {}
    resolved = list(normalization.get("resolved_terms") or normalization.get("canonical_terms") or [])
    values = list(resolved)
    values.extend(plan.get("entities") or [])
    if not resolved:
        phrases = [str(v) for v in (normalization.get("phrase_candidates") or []) if len(_normalize_phrase(v).split()) >= 2]
        # Longest query concepts first; single keyword matches (for example
        # ``first``) are too weak for answerability and caused unrelated passages
        # to look like direct answers.
        phrases.sort(key=lambda v: len(_normalize_phrase(v).split()), reverse=True)
        values.extend(phrases[:8])
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_project_term(value)
        norm = _normalize_phrase(text)
        if not text or not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(text)
    return out[:8]


def _definition_query(plan: dict[str, Any]) -> bool:
    query = str(plan.get("query") or "").strip()
    return bool(re.search(r"^\s*(?:who|what)\s+(?:is|are|was|were)\b", query, flags=re.I))


def _answerability_profile(plan: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Estimate whether a relevant passage can directly answer this query.

    Relevance is not answerability. A passage can mention Great Soul repeatedly
    while only listing open questions. This score is deliberately interpretable
    and sits beside authority rather than replacing it.
    """
    epistemics = _item_epistemics(item)
    role = str(epistemics.get("epistemic_role") or "declarative_passage")
    supports_positive = bool(epistemics.get("supports_positive_claims", True))
    title = str(item.get("title") or "")
    content = str(item.get("content") or item.get("snippet") or "")
    title_norm = _normalize_phrase(title)
    content_norm = _normalize_phrase(content)
    targets = _answer_target_terms(plan)
    definition_query = _definition_query(plan)

    exact_target = ""
    title_tokens = set(title_norm.split())
    content_tokens = set(content_norm.split())
    for target in targets:
        norm = _normalize_phrase(target)
        if not norm:
            continue
        target_tokens = [token for token in norm.split() if token not in _STOPWORDS]
        direct_text_match = norm in title_norm or norm in content_norm
        token_match = bool(target_tokens) and all(token in (title_tokens | content_tokens) for token in target_tokens)
        if direct_text_match or token_match:
            exact_target = target
            break

    score = 0.72
    multiplier = 0.78
    reasons: list[str] = []
    direct_definition = False
    heading_match = False

    if role == "unresolved_question":
        score, multiplier = 0.10, 0.12
        reasons.append("epistemic:unresolved_question")
    elif role == "speculative_passage":
        score, multiplier = 0.28, 0.38
        reasons.append("epistemic:speculative")

    if targets and not exact_target:
        score = min(score, 0.24)
        multiplier = min(multiplier, 0.30)
        reasons.append("target_not_present")
    elif exact_target:
        target_norm = _normalize_phrase(exact_target)
        heading_parts = []
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        heading_parts = [str(v) for v in (metadata.get("heading_path") or []) if str(v or "").strip()]
        heading_match = any(_normalize_phrase(part) == target_norm for part in heading_parts)
        if not heading_match:
            heading_match = bool(re.search(rf"(?:^|[>·])\s*{re.escape(exact_target)}\s*$", title, flags=re.I))

        # Direct glossary/profile patterns: **Great Soul** — ..., Great Soul: ...,
        # Great Soul is ..., or a section headed exactly by the entity name.
        direct_patterns = (
            rf"(?:\*\*)?{re.escape(exact_target)}(?:\*\*)?\s*(?:—|–|-|:)\s+",
            rf"\b{re.escape(exact_target)}\s+(?:is|was|are|were)\b",
        )
        direct_definition = any(re.search(pattern, content, flags=re.I) for pattern in direct_patterns)
        if supports_positive and direct_definition:
            score, multiplier = 1.0, 1.35
            reasons.append("direct_definition")
        elif supports_positive and heading_match:
            score, multiplier = max(score, 0.96), max(multiplier, 1.25)
            reasons.append("entity_heading")
        elif supports_positive and definition_query:
            score, multiplier = max(score, 0.68), max(multiplier, 0.82)
            reasons.append("entity_mention")
        if "glossary" in title.lower() and supports_positive:
            score, multiplier = max(score, 0.94), max(multiplier, 1.18)
            reasons.append("glossary")

    # An unresolved/speculative passage never becomes affirmative evidence merely
    # because its heading happens to match the query target.
    if not supports_positive:
        multiplier = min(multiplier, 0.40 if role == "speculative_passage" else 0.14)
        score = min(score, 0.30 if role == "speculative_passage" else 0.12)
        direct_definition = False

    return {
        **epistemics,
        "answerability_score": round(max(0.0, min(1.0, score)), 6),
        "answerability_multiplier": round(max(0.05, min(1.5, multiplier)), 6),
        "answer_target": exact_target,
        "direct_definition": direct_definition,
        "entity_heading_match": heading_match,
        "definition_query": definition_query,
        "answerability_reasons": reasons,
    }



class UnifiedRetrievalPlanner:
    """NKB-8 query planner/fusion/authority resolver.

    This layer decides *where to look* and *which evidence should survive*.
    It deliberately does not build the final prompt packet; NKB-9 owns packet shape.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, root_dir: Path = ROOT_DIR) -> None:
        self.db_path = Path(db_path)
        self.root_dir = Path(root_dir).resolve()
        self.native = NativeKnowledgeService(self.root_dir)

    def _project_vocabulary(self, identity: Any, *, limit: int = 5000) -> list[str]:
        if not self.db_path.exists():
            return []
        try:
            memory_filter = identity.memory_filter()
        except Exception:
            memory_filter = {}
        surface = str(memory_filter.get("surface") or "assistant")
        project_id = str(memory_filter.get("project_id") or "")
        scope_id = str(memory_filter.get("scope_id") or "")
        terms: dict[str, str] = {}

        def add(value: Any) -> None:
            term = _safe_project_term(value)
            norm = _normalize_phrase(term)
            if not term or not norm or norm in terms:
                return
            terms[norm] = term

        try:
            with self._connect() as conn:
                clauses = ["status = 'active'"]
                params: list[Any] = []
                if surface:
                    clauses.append("surface = ?")
                    params.append(surface)
                if project_id:
                    clauses.append("project_id = ?")
                    params.append(project_id)
                elif scope_id:
                    clauses.append("scope_id = ?")
                    params.append(scope_id)
                where = " AND ".join(clauses)
                for row in conn.execute(
                    f"SELECT label, object_key FROM neo_memory_objects WHERE {where} ORDER BY updated_at DESC LIMIT 1800",
                    tuple(params),
                ).fetchall():
                    add(row[0])
                    add(str(row[1] or "").replace("_", " ").replace(":", " "))
                for row in conn.execute(
                    f"SELECT predicate, object_value FROM neo_memory_facts WHERE {where} ORDER BY updated_at DESC LIMIT 2200",
                    tuple(params),
                ).fetchall():
                    predicate = str(row[0] or "").lower()
                    if predicate in {"identity.alias", "identity.name", "name", "alias", "title"}:
                        add(row[1])
                for row in conn.execute(
                    f"SELECT title, content FROM neo_memory_fragments WHERE {where} AND source_type = 'assistant_project_brain_upload' ORDER BY updated_at DESC LIMIT ?",
                    tuple([*params, max(1, min(limit, 8000))]),
                ).fetchall():
                    title = str(row[0] or "")
                    for segment in re.split(r"\s+[·>]\s+", title):
                        add(segment)
                    content = str(row[1] or "")
                    for match in re.findall(r"\*\*([^*\n]{2,80})\*\*\s*(?:—|–|-|:)", content):
                        add(match)
                    for match in re.findall(r"(?m)^#{1,6}\s+(.{2,80})$", content):
                        add(match)
                    # Character/location names are not always headings or explicit
                    # key/value entities. Capture conservative multi-word proper
                    # phrases so Project-local typo recovery can resolve names such
                    # as ``Elias Rowen`` -> ``Elias Rowan`` without a global
                    # dictionary. Generic heading starters are filtered out.
                    for match in re.findall(r"\b[A-Z][A-Za-z'’-]{2,}(?:\s+[A-Z][A-Za-z'’-]{2,}){1,2}\b", content):
                        first = match.split()[0].lower()
                        if first in {"the", "this", "that", "part", "chapter", "volume", "appendix", "section", "table", "note", "final", "complete"}:
                            continue
                        add(match)
        except (sqlite3.Error, OSError):
            return []
        return list(terms.values())[:12000]

    def _recover_project_query(self, query: str, identity: Any, *, named: list[str], keywords: list[str]) -> dict[str, Any]:
        vocabulary = self._project_vocabulary(identity)
        phrase_candidates = _query_phrase_candidates(query, named, keywords)
        by_norm = {_normalize_phrase(term): term for term in vocabulary if _normalize_phrase(term)}
        recoveries: list[dict[str, Any]] = []
        canonical_terms: list[str] = []
        corrected_terms: list[str] = []

        for phrase in phrase_candidates:
            qnorm = _normalize_phrase(phrase)
            if not qnorm or qnorm in _STOPWORDS:
                continue
            if qnorm in by_norm:
                canonical = by_norm[qnorm]
                if canonical not in canonical_terms:
                    canonical_terms.append(canonical)
                recoveries.append({"input": phrase, "canonical": canonical, "score": 1.0, "confidence": "exact", "applied": False})
                continue
            qtokens = qnorm.split()
            if not qtokens:
                continue
            ranked: list[tuple[float, str]] = []
            for canonical in vocabulary:
                cnorm = _normalize_phrase(canonical)
                ctokens = cnorm.split()
                if not cnorm or len(ctokens) != len(qtokens):
                    continue
                if len(qtokens) == 1 and min(len(qnorm), len(cnorm)) < 5:
                    continue
                initial_compatible = all((a[:1] == b[:1]) for a, b in zip(qtokens, ctokens))
                ratio = SequenceMatcher(None, qnorm, cnorm).ratio()
                if not initial_compatible and ratio < 0.93:
                    continue
                if ratio >= 0.78:
                    ranked.append((ratio, canonical))
            ranked.sort(key=lambda item: item[0], reverse=True)
            if not ranked:
                continue
            top_score, canonical = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            margin = top_score - second_score
            confidence = ""
            applied = False
            if top_score >= 0.86 and margin >= 0.05:
                confidence, applied = "high", True
            elif top_score >= 0.82 and margin >= 0.08:
                confidence, applied = "medium", True
            if not confidence:
                continue
            recoveries.append({
                "input": phrase,
                "canonical": canonical,
                "score": round(top_score, 6),
                "margin": round(margin, 6),
                "confidence": confidence,
                "applied": applied,
            })
            if canonical not in canonical_terms:
                canonical_terms.append(canonical)
            if applied and _normalize_phrase(canonical) != qnorm and canonical not in corrected_terms:
                corrected_terms.append(canonical)

        original_query = str(query or "").strip()
        retrieval_query = original_query
        # High-confidence Project-local corrections become the actual retrieval
        # wording, while ``original_query`` remains untouched for audit/UI.
        # Appending misspelled and corrected forms together made lexical fallback
        # rerankers overweight the typo; replacing the matched phrase gives the
        # canonical term a clean search signal.
        for match in recoveries:
            if not match.get("applied"):
                continue
            source_phrase = str(match.get("input") or "").strip()
            canonical = str(match.get("canonical") or "").strip()
            if not source_phrase or not canonical or _normalize_phrase(source_phrase) == _normalize_phrase(canonical):
                continue
            replaced = re.sub(re.escape(source_phrase), canonical, retrieval_query, count=1, flags=re.I)
            if replaced != retrieval_query:
                retrieval_query = replaced
        # If phrase replacement could not be performed (for example because the
        # candidate was assembled from keywords), use a canonical quoted query
        # rather than mixing misspellings into the search.
        if corrected_terms and retrieval_query == original_query:
            retrieval_query = " ".join(f'"{term}"' for term in corrected_terms)
        elif corrected_terms:
            quoted = " ".join(f'"{term}"' for term in corrected_terms if f'"{term}"' not in retrieval_query)
            if quoted:
                retrieval_query = (retrieval_query + " " + quoted).strip()
        return {
            "original_query": original_query,
            "phrase_candidates": phrase_candidates,
            "project_vocabulary_count": len(vocabulary),
            "matches": recoveries[:12],
            "canonical_terms": canonical_terms[:8],
            "corrected_terms": corrected_terms[:6],
            "resolved_terms": canonical_terms[:8],
            "retrieval_query": retrieval_query,
            "policy": "Project-local fuzzy recovery preserves the original query, adds only high/medium-confidence scope vocabulary variants, and never rewrites stored canon.",
        }

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema_id": RETRIEVAL_PLANNER_SCHEMA_ID,
            "phase": RETRIEVAL_PLANNER_PHASE,
            "status": "ready",
            "stages": ["query_analysis", "lane_planning", "candidate_fusion", "rerank", "authority_adjudication", "source_hydration"],
            "fusion": {"method": "weighted_rrf", "rrf_k": RRF_K, "raw_cross_lane_score_addition": False},
            "native_adapter_count": (self.native.adapters().get("count") or 0),
            "project_document_resolver": PROJECT_DOCUMENT_ADAPTER_ID,
            "policy": "Planner selects/fuses/verifies evidence. NKB-9 owns bounded Context Packets and NKB-10 owns final fail-closed/creative grounding modes.",
        }

    def analyze(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        query = str(data.get("query") or data.get("message") or data.get("user_input") or "").strip()
        requested_profile = str(data.get("retrieval_profile") or data.get("profile") or "smart").strip().lower() or "smart"
        identity = resolve_canonical_identity(
            data,
            legacy_project_is_scope=bool(data.get("legacy_project_id")),
            source="nkb8_retrieval_planner",
        )
        active_surface = identity.surface_id or str(data.get("surface") or "assistant") or "assistant"
        target_surface = _surface_from_query(query, active_surface)
        low = query.lower()
        named = _named_phrases(query)
        keywords = _keywords(query)
        project_scope = bool(identity.scope_id and identity.scope_id not in {"", "general", "global"}) or bool(identity.project_id and identity.project_id not in {"general", "global"})
        project_sandbox = bool(identity.scope_id and identity.scope_id not in {"", "general", "global"} and not is_builtin_scope(identity.scope_id))
        query_normalization = self._recover_project_query(query, identity, named=named, keywords=keywords) if project_scope else {
            "original_query": query, "phrase_candidates": [], "project_vocabulary_count": 0, "matches": [],
            "canonical_terms": [], "corrected_terms": [], "retrieval_query": query,
        }
        for term in query_normalization.get("canonical_terms") or []:
            if term and term.lower() not in {item.lower() for item in named}:
                named.append(term)
        code_intent = _looks_like_code_query(query)
        strong_admin_terms = ("admin", "control center", "memory engine", "provider", "backend", "extension registry", "health")
        admin_intent = _contains_any(query, strong_admin_terms) and ("neo" in low or active_surface in {"assistant", "global"})
        creative = _contains_any(query, _CREATIVE_TERMS)
        history = _contains_any(query, _HISTORY_TERMS)
        historical_design = code_intent and _contains_any(query, _HISTORICAL_DESIGN_TERMS)
        validation = code_intent and _contains_any(query, _VALIDATION_TERMS)
        usage = _contains_any(query, _USAGE_TERMS) and not historical_design
        canonish = _contains_any(query, _CANON_TERMS) or (project_scope and active_surface in {"assistant", "global"} and not creative and not code_intent and not admin_intent and not usage)

        if code_intent:
            intent = "neo_development"
            claim_type = "historical_design" if historical_design else "validation" if validation else "usage_guidance" if usage else "current_runtime"
        elif admin_intent:
            intent = "neo_admin_diagnostic"
            claim_type = "current_runtime"
        elif target_surface == "roleplay" and (active_surface == "roleplay" or "roleplay" in low or "scene" in low):
            intent = "roleplay_recall" if not creative else "roleplay_creative"
            claim_type = "project_fact" if not creative else "creative_extension"
        elif history and target_surface in _SURFACE_ADAPTER:
            intent = f"{target_surface}_history"
            claim_type = "native_execution"
        elif project_scope and canonish:
            intent = "project_canon_recall" if _contains_any(query, _CANON_TERMS) else "project_recall"
            claim_type = "project_fact"
        elif usage and ("neo" in low or target_surface in _SURFACE_ADAPTER):
            intent = "neo_usage"
            claim_type = "usage_guidance"
        elif creative:
            intent = "creative_request"
            claim_type = "creative_extension"
        else:
            intent = "general_recall"
            claim_type = "general_fact"

        adapter_ids: list[str] = []
        if intent in {"neo_development", "neo_admin_diagnostic"}:
            adapter_ids.extend(["neo.code", "neo.docs"])
        elif intent == "neo_usage":
            adapter_ids.append("neo.docs")
        if target_surface in _SURFACE_ADAPTER and (history or active_surface == target_surface or intent.startswith(target_surface)):
            adapter = _SURFACE_ADAPTER[target_surface]
            if adapter not in adapter_ids:
                adapter_ids.append(adapter)
        if target_surface == "roleplay" and "neo.roleplay" not in adapter_ids:
            adapter_ids.append("neo.roleplay")

        include_project = bool(project_scope and claim_type in {"project_fact", "creative_extension", "general_fact"})
        include_unified = intent not in {"neo_usage"} or bool(project_scope)
        include_knowledge = intent in {"neo_development", "neo_admin_diagnostic", "neo_usage", "general_recall"}
        include_guides = intent in {"neo_usage", "neo_development", "neo_admin_diagnostic", "general_recall"}
        if claim_type == "project_fact":
            include_knowledge = False
            include_guides = False
        if project_sandbox:
            # User-created project scopes are strict context sandboxes. Do not let
            # Neo Guides/System Records/code/native histories or unrelated global
            # memory become a fallback merely because the query is ambiguous.
            include_project = True
            include_unified = True
            include_knowledge = False
            include_guides = False
            adapter_ids = []
            target_surface = identity.surface_id or "assistant"
            if creative:
                intent, claim_type = "creative_request", "creative_extension"
            elif claim_type not in {"project_fact", "creative_extension"}:
                intent, claim_type = "project_canon_recall", "project_fact"

        if claim_type == "current_runtime":
            preferred_roles = ["runtime_implementation", "current_architecture", "validation_test", "current_guide"]
        elif claim_type == "usage_guidance":
            preferred_roles = ["current_guide", "runtime_implementation", "current_architecture"]
        elif claim_type == "historical_design":
            preferred_roles = ["historical_record", "current_architecture", "runtime_implementation"]
        elif claim_type == "validation":
            preferred_roles = ["validation_test", "runtime_implementation", "current_architecture"]
        elif claim_type == "project_fact":
            preferred_roles = ["project_source", "roleplay_canon", "user_confirmed"]
        elif claim_type == "native_execution":
            preferred_roles = ["native_execution_record", "runtime_implementation"]
        else:
            preferred_roles = ["project_source", "native_execution_record", "current_guide", "runtime_implementation"]

        variants: list[str] = []
        for value in [*(query_normalization.get("phrase_candidates") or [])[:4], *(query_normalization.get("canonical_terms") or [])[:4], *named, *keywords[:6]]:
            value = _clean(value, 100)
            if value and value.lower() not in {item.lower() for item in variants}:
                variants.append(value)
        if not variants and query:
            variants = [query[:120]]

        lane_weights = {
            "project_structured": 1.35 if include_project else 0.0,
            "unified_memory": 1.15 if include_project else 1.0,
            "native_authority": 1.30 if adapter_ids else 0.0,
            "knowledge_index": 1.15 if include_knowledge else 0.0,
            "guide_index": 1.20 if claim_type == "usage_guidance" else (0.85 if include_guides else 0.0),
        }
        strong_claim = claim_type in {"current_runtime", "project_fact", "native_execution", "validation"}
        fail_closed = claim_type in {"project_fact", "current_runtime", "validation", "native_execution"}
        return {
            "schema_id": RETRIEVAL_PLANNER_SCHEMA_ID,
            "phase": RETRIEVAL_PLANNER_PHASE,
            "planner_trace_id": f"planner_{uuid4().hex[:12]}",
            "query": query,
            "retrieval_query": str(query_normalization.get("retrieval_query") or query),
            "query_normalization": query_normalization,
            "requested_profile": requested_profile,
            "identity": identity.as_dict(),
            "intent": intent,
            "claim_type": claim_type,
            "target_surface": target_surface,
            "project_scope": project_scope,
            "project_sandbox": project_sandbox,
            "scope_class": "project_sandbox" if project_sandbox else ("general_federated" if identity.scope_id == "general" else "built_in_surface"),
            "hard_sandbox": project_sandbox,
            "creative_request": creative,
            "entities": named,
            "keywords": keywords,
            "query_variants": variants[:8],
            "native_adapters": adapter_ids,
            "lanes": {
                "project_structured": include_project,
                "unified_memory": include_unified,
                "knowledge_index": include_knowledge,
                "guide_index": include_guides,
                "native_authority": bool(adapter_ids),
            },
            "lane_weights": lane_weights,
            "authority": {
                "preferred_evidence_roles": preferred_roles,
                "require_current": claim_type not in {"historical_design"},
                "require_verified_source": strong_claim,
                "reject_superseded_for_current": claim_type != "historical_design",
                "reject_stale_for_strong_claim": strong_claim,
                "fail_closed_recommended": fail_closed,
            },
            "policy": "Intent and claim type choose evidence lanes. User-created project scopes are hard sandboxes; General and Neo built-ins may use bounded federated retrieval. Relevance and authority are resolved separately.",
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def project_structured_candidates(self, plan: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
        if not (plan.get("lanes") or {}).get("project_structured") or not self.db_path.exists():
            return []
        identity = plan.get("identity") if isinstance(plan.get("identity"), dict) else {}
        project_id = str(identity.get("project_id") or "")
        scope_id = str(identity.get("scope_id") or "")
        surface = str(identity.get("surface_id") or "assistant")
        keywords = [str(item).lower() for item in (plan.get("entities") or plan.get("keywords") or []) if str(item or "").strip()]
        if not keywords:
            keywords = [str(item).lower() for item in (plan.get("keywords") or [])[:6]]
        with self._connect() as conn:
            clauses = ["status = 'active'"]
            params: list[Any] = []
            if surface:
                clauses.append("surface = ?")
                params.append(surface)
            if project_id:
                clauses.append("(project_id = ? OR project_id IS NULL OR project_id = '')")
                params.append(project_id)
            if scope_id:
                clauses.append("(scope_id = ? OR scope_id IS NULL OR scope_id = '')")
                params.append(scope_id)
            objects = [dict(row) for row in conn.execute(
                f"SELECT * FROM neo_memory_objects WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 800",
                tuple(params),
            ).fetchall()]
            facts = [dict(row) for row in conn.execute(
                f"SELECT * FROM neo_memory_facts WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 1600",
                tuple(params),
            ).fetchall()]
            fragments = [dict(row) for row in conn.execute(
                f"SELECT * FROM neo_memory_fragments WHERE {' AND '.join(clauses)} AND source_type = 'assistant_project_brain_upload' ORDER BY updated_at DESC LIMIT 2400",
                tuple(params),
            ).fetchall()]

        object_map = {str(row.get("object_id") or ""): row for row in objects}
        matched_subjects: set[str] = set()
        object_scores: dict[str, float] = {}
        for row in objects:
            attrs = _metadata({"metadata_json": row.get("attributes_json")})
            hay = " ".join([str(row.get("label") or ""), str(row.get("object_key") or ""), str(row.get("summary") or ""), json.dumps(attrs, ensure_ascii=False)]).lower()
            matches = sum(1 for term in keywords if term in hay)
            if matches:
                oid = str(row.get("object_id") or "")
                matched_subjects.add(oid)
                object_scores[oid] = min(1.0, 0.55 + (0.12 * matches))
        # Alias facts explicitly map user wording to an entity even when the canonical label is absent from the question.
        for row in facts:
            if str(row.get("predicate") or "") != "identity.alias":
                continue
            value = str(row.get("object_value") or "").lower()
            if any(term == value or term in value or value in term for term in keywords):
                sid = str(row.get("subject_id") or "")
                if sid:
                    matched_subjects.add(sid)
                    object_scores[sid] = max(object_scores.get(sid, 0.0), 0.95)

        candidates: list[dict[str, Any]] = []
        for row in facts:
            sid = str(row.get("subject_id") or "")
            statement = str(row.get("statement") or "")
            predicate = str(row.get("predicate") or "")
            object_value = str(row.get("object_value") or "")
            hay = f"{statement} {predicate} {object_value}".lower()
            term_matches = sum(1 for term in keywords if term in hay)
            if sid not in matched_subjects and not term_matches:
                continue
            subject = object_map.get(sid) or {}
            meta = _metadata(row)
            block_id = str(meta.get("source_block_id") or "")
            supporting = None
            for fragment in fragments:
                fmeta = _metadata(fragment)
                if meta.get("document_id") and str(fmeta.get("document_id") or "") != str(meta.get("document_id") or ""):
                    continue
                if meta.get("revision_id") and str(fmeta.get("revision_id") or "") != str(meta.get("revision_id") or ""):
                    continue
                if block_id and block_id not in [str(item) for item in (fmeta.get("source_block_ids") or [])]:
                    continue
                supporting = fragment
                break
            fmeta = _metadata(supporting or {}) if supporting else {}
            evidence = fmeta.get("evidence") if isinstance(fmeta.get("evidence"), dict) else {}
            native_ref = evidence.get("native_ref") if isinstance(evidence.get("native_ref"), dict) else {}
            locator = evidence.get("source_locator") if isinstance(evidence.get("source_locator"), dict) else {}
            base = min(1.0, max(object_scores.get(sid, 0.45), 0.55 + term_matches * 0.10))
            candidates.append({
                "item_id": str(row.get("fact_id") or f"fact:{_hash(statement)}"),
                "source_lane": "project_structured",
                "kind": "fact",
                "surface": row.get("surface") or surface,
                "project_id": row.get("project_id") or project_id,
                "scope_id": row.get("scope_id") or scope_id,
                "source_type": "project_explicit_fact",
                "source_id": str(row.get("source_event_id") or row.get("fact_id") or ""),
                "memory_type": "project_fact",
                "title": str(subject.get("label") or predicate or "Project fact"),
                "content": statement or f"{predicate}: {object_value}",
                "snippet": _clean(statement or object_value, 700),
                "score": base,
                "retrieval_type": "structured_fact",
                "trust_level": str(row.get("trust_level") or "confirmed"),
                "memory_state": str(row.get("status") or "active"),
                "approval_state": "",
                "citation": {
                    "source_id": str(row.get("source_event_id") or ""),
                    "source_path": str(locator.get("path") or fmeta.get("stored_path") or ""),
                    "label": str(locator.get("label") or fmeta.get("filename") or subject.get("label") or "Project source"),
                    "start_line": None,
                    "end_line": None,
                    "viewer_endpoint": "",
                },
                "metadata": {
                    **meta,
                    "subject_id": sid,
                    "subject_label": subject.get("label") or "",
                    "predicate": predicate,
                    "object_value": object_value,
                    "evidence_role": "project_source",
                    "native_ref": native_ref,
                    "source_locator": locator,
                },
                "provenance": {"adapter": PROJECT_DOCUMENT_ADAPTER_ID, "native_ref": native_ref, "evidence_role": "project_source"},
                "authority": {"evidence_role": "project_source", "lifecycle": str(row.get("status") or "active"), "source_integrity": "present"},
            })
        candidates.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
        return candidates[: max(1, min(limit, 80))]

    def native_candidates(self, plan: dict[str, Any], *, limit: int = 16) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not (plan.get("lanes") or {}).get("native_authority"):
            return [], {}
        adapters = [str(item) for item in (plan.get("native_adapters") or []) if str(item or "").strip()]
        if not adapters:
            return [], {}
        context = plan.get("identity") if isinstance(plan.get("identity"), dict) else {}
        variants = list(plan.get("query_variants") or [])[:4]
        query = str(plan.get("query") or "")
        reports: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        per_adapter = max(2, min(limit, 12))
        for adapter_id in adapters:
            # Code/Docs authorities can contain thousands of source files. Broad
            # natural-language discovery should use their indexed shortlist and
            # then hydrate through the native adapter; directly scanning the
            # authority store is reserved for a high-information path/symbol/title.
            lookup_query = ""
            if variants:
                if adapter_id == "neo.code":
                    lookup_query = next((item for item in variants if "/" in item or "." in item or "_" in item or "(" in item), "")
                elif adapter_id == "neo.docs":
                    lookup_query = next((item for item in variants if "/" in item or item.lower().endswith(".md")), "")
                else:
                    lookup_query = variants[0]
            if adapter_id in {"neo.code", "neo.docs"} and not lookup_query:
                reports[adapter_id] = {
                    "ok": True,
                    "status": "deferred_to_index",
                    "query": "",
                    "candidate_count": 0,
                    "policy": "Broad Code/Docs discovery uses indexed retrieval; selected source paths hydrate through the native adapter.",
                }
                continue
            lookup_query = lookup_query or query[:100]
            try:
                lookup = self.native.lookup({"adapter_id": adapter_id, "query": lookup_query, "context": context, "limit": per_adapter})
                rows = list(lookup.get("items") or [])
                reports[adapter_id] = {"ok": bool(lookup.get("ok", True)), "query": lookup_query, "candidate_count": len(rows)}
                for rank, row in enumerate(rows[:per_adapter], start=1):
                    if not isinstance(row, dict):
                        continue
                    native_ref = row.get("native_ref") if isinstance(row.get("native_ref"), dict) else {
                        "adapter_id": adapter_id,
                        "authority_namespace": row.get("authority_namespace") or "",
                        "native_id": row.get("native_id") or "",
                        "resolver_kind": "",
                        "resolver_key": {"path": row.get("path") or ""} if row.get("path") else {},
                    }
                    projected = self.native.project({"adapter_id": adapter_id, "native_ref": native_ref, "context": context})
                    if not projected.get("ok"):
                        continue
                    projection = projected.get("projection") if isinstance(projected.get("projection"), dict) else {}
                    evidence = projected.get("evidence") if isinstance(projected.get("evidence"), dict) else {}
                    semantic = evidence.get("semantic") if isinstance(evidence.get("semantic"), dict) else {}
                    lifecycle = evidence.get("lifecycle") if isinstance(evidence.get("lifecycle"), dict) else {}
                    provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
                    locator = evidence.get("source_locator") if isinstance(evidence.get("source_locator"), dict) else {}
                    role = str(semantic.get("evidence_role") or projection.get("source_role") or row.get("evidence_role") or "native_execution_record")
                    search_text = str(projection.get("search_text") or projected.get("payload", {}).get("text") or row.get("title") or "")
                    candidates.append({
                        "item_id": str(projection.get("projection_id") or f"native:{adapter_id}:{row.get('native_id') or rank}"),
                        "source_lane": "native_authority",
                        "kind": str(projected.get("kind") or "knowledge"),
                        "surface": str((projection.get("context") or {}).get("surface_id") or context.get("surface_id") or "global"),
                        "project_id": str((projection.get("context") or {}).get("project_id") or context.get("project_id") or ""),
                        "scope_id": str((projection.get("context") or {}).get("scope_id") or context.get("scope_id") or ""),
                        "source_type": role,
                        "source_id": str((projected.get("knowledge_ref") or {}).get("knowledge_id") or row.get("native_id") or ""),
                        "memory_type": "native_evidence",
                        "title": str(projection.get("title") or row.get("title") or row.get("native_id") or adapter_id),
                        "content": _clean(search_text, 2600),
                        "snippet": _clean(search_text, 800),
                        "score": max(0.2, 1.0 - ((rank - 1) * 0.06)),
                        "retrieval_type": "native_structured_lookup",
                        "trust_level": "",
                        "memory_state": str(lifecycle.get("state") or projection.get("lifecycle_state") or "active"),
                        "approval_state": str((evidence.get("workflow") or {}).get("approval_state") or ""),
                        "citation": {
                            "source_id": str((projected.get("knowledge_ref") or {}).get("knowledge_id") or ""),
                            "source_path": str(locator.get("path") or ""),
                            "start_line": locator.get("start_line"),
                            "end_line": locator.get("end_line"),
                            "label": str(locator.get("label") or projection.get("title") or row.get("title") or adapter_id),
                            "viewer_endpoint": "",
                        },
                        "metadata": {"adapter_id": adapter_id, "native_ref": projected.get("native_ref") or native_ref, "knowledge_ref": projected.get("knowledge_ref") or {}, "revision": projected.get("revision") or {}, "evidence": evidence},
                        "provenance": {"adapter": adapter_id, "native_ref": projected.get("native_ref") or native_ref, "evidence_id": evidence.get("evidence_id") or "", "evidence_role": role},
                        "authority": {"evidence_role": role, "lifecycle": str(lifecycle.get("state") or "active"), "source_integrity": str(provenance.get("source_integrity") or "present"), "conflict": str((evidence.get("conflict") or {}).get("state") or "none")},
                    })
            except Exception as exc:
                reports[adapter_id] = {"ok": False, "query": lookup_query, "candidate_count": 0, "error": str(exc)[:500]}
        return candidates, reports

    def _candidate_key(self, item: dict[str, Any]) -> str:
        # Cross-lane retrieval can represent the same evidence with different
        # lane-local IDs (memory fragment, static chunk, guide hit). Keep the
        # legacy content-deduplication contract by using normalized visible text
        # as the fusion identity when available. Stable knowledge/evidence/source
        # IDs remain attached to the merged candidate as provenance, but do not
        # prevent equivalent text from collapsing into one result.
        text = _clean(item.get("content") or item.get("snippet"), 1800).lower()
        if text:
            return f"text:{_hash(text)}"
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
        knowledge_ref = metadata.get("knowledge_ref") if isinstance(metadata.get("knowledge_ref"), dict) else {}
        if knowledge_ref.get("knowledge_id"):
            return "knowledge:" + str(knowledge_ref.get("knowledge_id"))
        if evidence.get("evidence_id"):
            return "evidence:" + str(evidence.get("evidence_id"))
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        path = str(citation.get("source_path") or "")
        if path:
            start = citation.get("start_line") or ""
            end = citation.get("end_line") or ""
            if start or end:
                return f"source:{path}:{start}:{end}"
        if metadata.get("document_id") and metadata.get("revision_id"):
            return f"pdoc:{metadata.get('document_id')}:{metadata.get('revision_id')}:{metadata.get('fragment_key') or metadata.get('source_block_id') or item.get('item_id')}"
        return f"item:{_hash(str(item.get('item_id') or item))}"

    def fuse(self, plan: dict[str, Any], lane_items: dict[str, list[dict[str, Any]]], *, limit: int = 24) -> dict[str, Any]:
        weights = plan.get("lane_weights") if isinstance(plan.get("lane_weights"), dict) else {}
        merged: dict[str, dict[str, Any]] = {}
        duplicate_count = 0
        lane_counts: dict[str, int] = {}
        for lane, items in lane_items.items():
            clean_items = [item for item in items if isinstance(item, dict) and _clean(item.get("content") or item.get("snippet"))]
            clean_items.sort(key=lambda item: _score01(item.get("score")), reverse=True)
            lane_counts[lane] = len(clean_items)
            lane_weight = float(weights.get(lane, 1.0) or 0.0)
            if lane_weight <= 0:
                continue
            for rank, item in enumerate(clean_items, start=1):
                key = self._candidate_key(item)
                contribution = lane_weight / (RRF_K + rank)
                if key not in merged:
                    updated = dict(item)
                    updated["fusion_key"] = key
                    updated["rrf_score_raw"] = contribution
                    updated["fusion_lanes"] = [lane]
                    updated["provenance_lanes"] = [lane]
                    updated["lane_ranks"] = {lane: rank}
                    updated["raw_scores"] = {lane: _score01(item.get("score"))}
                    merged[key] = updated
                else:
                    existing = merged[key]
                    # Reaching the exact same memory item through multiple approved
                    # scope targets is a routing duplicate, not a second content
                    # duplicate. Preserve the legacy Retrieval Gateway accounting
                    # contract while still accumulating its RRF contribution.
                    existing_item_id = _clean(existing.get("item_id"))
                    incoming_item_id = _clean(item.get("item_id"))
                    if not existing_item_id or not incoming_item_id or existing_item_id != incoming_item_id:
                        duplicate_count += 1
                    existing["rrf_score_raw"] = float(existing.get("rrf_score_raw") or 0.0) + contribution
                    if lane not in existing["fusion_lanes"]:
                        existing["fusion_lanes"].append(lane)
                    provenance_lanes = existing.setdefault("provenance_lanes", list(existing.get("fusion_lanes") or []))
                    if lane not in provenance_lanes:
                        provenance_lanes.append(lane)
                    existing["lane_ranks"][lane] = rank
                    existing["raw_scores"][lane] = _score01(item.get("score"))
                    # Prefer the candidate carrying a native/project authority reference and a source citation.
                    incoming_meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    existing_meta = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                    if incoming_meta.get("native_ref") and not existing_meta.get("native_ref"):
                        for field in ("metadata", "provenance", "authority", "citation", "source_type", "source_id", "title", "content", "snippet"):
                            existing[field] = item.get(field)
                    elif not (existing.get("citation") or {}).get("source_path") and (item.get("citation") or {}).get("source_path"):
                        existing["citation"] = item.get("citation")
        ranked = sorted(merged.values(), key=lambda item: float(item.get("rrf_score_raw") or 0.0), reverse=True)
        peak = max([float(item.get("rrf_score_raw") or 0.0) for item in ranked] or [1.0])
        for item in ranked:
            item["score"] = round(float(item.get("rrf_score_raw") or 0.0) / peak, 6)
            item["retrieval_type"] = "weighted_rrf+" + str(item.get("retrieval_type") or "candidate")
        return {"items": ranked[: max(1, min(limit, 80))], "lane_counts": lane_counts, "duplicates_removed": duplicate_count, "method": "weighted_rrf", "rrf_k": RRF_K}

    def rerank(self, plan: dict[str, Any], items: list[dict[str, Any]], *, top_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not items:
            return [], {"status": "no_candidates"}
        if str(plan.get("requested_profile") or "smart") == "fast":
            return items[:top_n], {"status": "skipped", "reason": "fast_profile"}
        try:
            result = rerank_results(str(plan.get("retrieval_query") or plan.get("query") or ""), items, top_n=min(top_n, len(items)), allow_fallback=True)
            return list(result.get("results") or items[:top_n]), {k: v for k, v in result.items() if k != "results"}
        except Exception as exc:
            return items[:top_n], {"status": "fallback", "reason": str(exc)[:500]}

    def _authority_multiplier(self, plan: dict[str, Any], item: dict[str, Any]) -> tuple[float, list[str], bool]:
        authority = item.get("authority") if isinstance(item.get("authority"), dict) else {}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
        semantic = evidence.get("semantic") if isinstance(evidence.get("semantic"), dict) else {}
        lifecycle = evidence.get("lifecycle") if isinstance(evidence.get("lifecycle"), dict) else {}
        provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
        role = str(authority.get("evidence_role") or semantic.get("evidence_role") or metadata.get("evidence_role") or item.get("source_type") or "")
        state = str(authority.get("lifecycle") or lifecycle.get("state") or item.get("memory_state") or "active").lower()
        integrity = str(authority.get("source_integrity") or provenance.get("source_integrity") or "present").lower()
        conflict = str(authority.get("conflict") or (evidence.get("conflict") or {}).get("state") or "none").lower()
        preferred = list((plan.get("authority") or {}).get("preferred_evidence_roles") or [])
        if role in preferred:
            role_weight = max(0.72, 1.0 - (preferred.index(role) * 0.08))
        else:
            role_weight = _NATIVE_ROLE_WEIGHTS.get(role, 0.72 if item.get("source_lane") in {"knowledge_index", "guide_index"} else 0.78)
        reasons: list[str] = []
        blocked = False
        if state in {"superseded", "deprecated", "archived", "deleted"}:
            if (plan.get("authority") or {}).get("reject_superseded_for_current"):
                blocked = True
                reasons.append(f"lifecycle:{state}")
            else:
                role_weight *= 0.65
        elif state == "draft":
            role_weight *= 0.75
            reasons.append("lifecycle:draft")
        if integrity in {"stale", "missing", "unavailable"}:
            if (plan.get("authority") or {}).get("reject_stale_for_strong_claim"):
                blocked = True
                reasons.append(f"source_integrity:{integrity}")
            else:
                role_weight *= 0.60
        if conflict not in {"", "none", "resolved"}:
            role_weight *= 0.65
            reasons.append(f"conflict:{conflict}")
        return max(0.0, min(1.0, role_weight)), reasons, blocked

    def hydrate(self, plan: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        updated = dict(item)
        metadata = dict(updated.get("metadata") or {}) if isinstance(updated.get("metadata"), dict) else {}
        native_ref = metadata.get("native_ref") if isinstance(metadata.get("native_ref"), dict) else {}
        if not native_ref:
            native_ref = ((updated.get("provenance") or {}).get("native_ref") if isinstance(updated.get("provenance"), dict) else {}) or {}
        adapter_id = str(native_ref.get("adapter_id") or metadata.get("adapter_id") or (updated.get("provenance") or {}).get("adapter") or "")
        if adapter_id == PROJECT_DOCUMENT_ADAPTER_ID and native_ref:
            resolved = resolve_project_document_ref(self.root_dir, native_ref)
            updated["hydration"] = {k: v for k, v in resolved.items() if k not in {"fragment", "text", "search_text"}}
            authority = dict(updated.get("authority") or {})
            authority["source_integrity"] = resolved.get("source_integrity") or resolved.get("status") or ("verified" if resolved.get("ok") else "unavailable")
            authority["hydrated"] = bool(resolved.get("ok"))
            updated["authority"] = authority
            if resolved.get("ok"):
                updated["content"] = str(resolved.get("text") or updated.get("content") or "")
                updated["snippet"] = _clean(updated["content"], 800)
            return updated
        if adapter_id.startswith("neo.") and native_ref and adapter_id != PROJECT_DOCUMENT_ADAPTER_ID:
            try:
                resolved = self.native.resolve({"adapter_id": adapter_id, "native_ref": native_ref})
                updated["hydration"] = {k: v for k, v in resolved.items() if k not in {"record", "file", "text"}}
                authority = dict(updated.get("authority") or {})
                authority["source_integrity"] = resolved.get("status") or ("verified" if resolved.get("ok") else "missing")
                authority["hydrated"] = bool(resolved.get("ok"))
                classification = resolved.get("classification") if isinstance(resolved.get("classification"), dict) else {}
                if classification:
                    if classification.get("evidence_role"):
                        authority["evidence_role"] = str(classification.get("evidence_role"))
                    if classification.get("lifecycle"):
                        authority["lifecycle"] = str(classification.get("lifecycle"))
                updated["authority"] = authority
                if resolved.get("ok") and resolved.get("text"):
                    updated["content"] = str(resolved.get("text") or "")
                    updated["snippet"] = _clean(updated["content"], 800)
            except Exception as exc:
                updated["hydration"] = {"ok": False, "status": "unavailable", "error": str(exc)[:400]}
        return updated

    def adjudicate(self, plan: dict[str, Any], items: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
        require_verify = bool((plan.get("authority") or {}).get("require_verified_source"))
        hydrated: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for rank, original in enumerate(items, start=1):
            item = dict(original)
            if require_verify and rank <= max(limit * 2, 8):
                item = self.hydrate(plan, item)
            multiplier, reasons, blocked = self._authority_multiplier(plan, item)
            answerability = _answerability_profile(plan, item)
            retrieval_score = _score01(item.get("score"))
            answerability_multiplier = float(answerability.get("answerability_multiplier") or 1.0)
            item["relevance_score"] = retrieval_score
            item["authority_multiplier"] = round(multiplier, 6)
            item["authority_reasons"] = reasons
            item["epistemic_role"] = str(answerability.get("epistemic_role") or "declarative_passage")
            item["epistemic_state"] = str(answerability.get("epistemic_state") or "established")
            item["supports_positive_claims"] = bool(answerability.get("supports_positive_claims", True))
            item["answerability_score"] = answerability.get("answerability_score")
            item["answerability_multiplier"] = round(answerability_multiplier, 6)
            item["answerability_reasons"] = list(answerability.get("answerability_reasons") or [])
            item["answer_target"] = str(answerability.get("answer_target") or "")
            item["direct_definition"] = bool(answerability.get("direct_definition"))
            item["entity_heading_match"] = bool(answerability.get("entity_heading_match"))
            item["score"] = round(min(1.0, retrieval_score * multiplier * answerability_multiplier), 6)
            item["authority_status"] = "rejected" if blocked else "accepted"
            if blocked:
                rejected.append(item)
            else:
                hydrated.append(item)
        hydrated.sort(
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("answerability_score") or 0.0),
                1 if item.get("supports_positive_claims") else 0,
            ),
            reverse=True,
        )
        selected = hydrated[: max(1, min(limit, 40))]

        affirmative = [
            item for item in selected
            if item.get("supports_positive_claims")
            and float(item.get("answerability_score") or 0.0) >= 0.45
            and float(item.get("score") or 0.0) >= 0.18
        ]
        explicit_unknown = [
            item for item in selected
            if str(item.get("epistemic_role") or "") == "unresolved_question"
            and float(item.get("relevance_score") or 0.0) >= 0.18
        ]
        established = bool(affirmative)
        known_state = "established" if established else ("explicit_unknown" if explicit_unknown else "not_established")
        if any("conflict:" in reason for item in selected for reason in (item.get("authority_reasons") or [])):
            known_state = "unresolved_conflict"
        return {
            "items": selected,
            "rejected": rejected,
            "known_state": known_state,
            "established": established,
            "affirmative_evidence_count": len(affirmative),
            "explicit_unknown_evidence_count": len(explicit_unknown),
            "fail_closed_recommended": bool((plan.get("authority") or {}).get("fail_closed_recommended")),
        }
