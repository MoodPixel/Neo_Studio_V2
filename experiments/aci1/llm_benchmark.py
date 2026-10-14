from __future__ import annotations

import time
from typing import Any


def _lower(value: Any) -> str:
    return str(value or "").casefold()


def _truth_terms(query_truth: dict[str, Any]) -> tuple[list[str], list[str]]:
    all_terms = [str(item) for item in (query_truth.get("answer_must_contain_all") or []) if str(item).strip()]
    any_terms = [str(item) for item in (query_truth.get("answer_must_contain_any") or []) if str(item).strip()]
    if not all_terms and not any_terms:
        all_terms = [str(item) for item in (query_truth.get("must_retrieve_all") or []) if str(item).strip()]
        any_terms = [str(item) for item in (query_truth.get("must_retrieve_any") or []) if str(item).strip()]
    return all_terms, any_terms


def _contains_terms(text: str, all_terms: list[str], any_terms: list[str]) -> bool:
    lowered = _lower(text)
    all_ok = all(_lower(term) in lowered for term in all_terms) if all_terms else True
    any_ok = any(_lower(term) in lowered for term in any_terms) if any_terms else True
    return bool(all_ok and any_ok)


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    defaults = profile.get("generation_defaults") if isinstance(profile.get("generation_defaults"), dict) else {}
    return {
        "profile_id": str(profile.get("profile_id") or ""),
        "display_name": str(profile.get("display_name") or ""),
        "provider_id": str(profile.get("provider_id") or ""),
        "model": str(connection.get("model") or defaults.get("model") or "default"),
        "base_url": str(connection.get("base_url") or ""),
        "runtime_status": str(profile.get("runtime_status") or (profile.get("runtime") or {}).get("status") or ""),
    }


def resolve_llm_profile(profile_id: str = "") -> dict[str, Any]:
    from neo_app.assistant.chat import resolve_assistant_backend_profile

    payload = {"backend_profile_id": str(profile_id or "").strip()} if str(profile_id or "").strip() else {}
    profile = resolve_assistant_backend_profile(payload, live_task=False)
    if not profile:
        raise RuntimeError("No Assistant text backend profile is configured")
    return profile


def run_llm_preflight(profile: dict[str, Any]) -> dict[str, Any]:
    from neo_app.assistant.chat import _provider_run_chat

    messages = [
        {
            "role": "system",
            "content": "ACI-1 benchmark connectivity probe. Return exactly ACI1_LLM_READY and nothing else.",
        },
        {"role": "user", "content": "Probe."},
    ]
    started = time.perf_counter()
    result = _provider_run_chat(
        profile,
        messages,
        {"temperature": 0.0, "top_p": 1.0, "max_tokens": 32},
    )
    wall_ms = round((time.perf_counter() - started) * 1000, 3)
    if not result.get("ok"):
        return {
            "status": "blocked",
            "ready": False,
            "error": str(result.get("error") or result.get("error_type") or "Assistant backend probe failed"),
            "wall_ms": wall_ms,
            "profile": _profile_summary(profile),
        }
    return {
        "status": "ready",
        "ready": True,
        "wall_ms": wall_ms,
        "profile": _profile_summary(profile),
        "probe_text": str(result.get("text") or "")[:200],
        "model": str(result.get("model") or _profile_summary(profile).get("model") or ""),
    }


def _evidence_text(results: list[dict[str, Any]], *, max_chars: int = 14000) -> str:
    chunks: list[str] = []
    used = 0
    for rank, item in enumerate(results, start=1):
        content = str(item.get("content") or item.get("text_preview") or "").strip()
        if not content:
            continue
        locators = item.get("source_locators") or []
        locator_text = ""
        if locators:
            locator_text = f"\nSOURCE_LOCATORS: {locators}"
        chunk = f"[EVIDENCE {rank}]\n{content}{locator_text}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n".join(chunks)


def evaluate_query_with_llm(
    *,
    profile: dict[str, Any],
    query_truth: dict[str, Any],
    retrieval_query: dict[str, Any],
    max_evidence_chars: int = 14000,
) -> dict[str, Any]:
    from neo_app.assistant.chat import _provider_run_chat

    query = str(query_truth.get("query") or retrieval_query.get("query") or "").strip()
    results = [item for item in (retrieval_query.get("results") or []) if isinstance(item, dict)]
    evidence = _evidence_text(results, max_chars=max(1000, int(max_evidence_chars)))
    all_terms, any_terms = _truth_terms(query_truth)
    evidence_supports = _contains_terms(evidence, all_terms, any_terms)

    messages = [
        {
            "role": "system",
            "content": (
                "You are the Neo ACI-1 document-answer benchmark. Answer ONLY from the supplied evidence. "
                "Do not use outside knowledge, do not infer hidden values, and do not mention benchmark internals. "
                "If the evidence does not establish the answer, return exactly NOT_ESTABLISHED. "
                "If it does establish the answer, answer the user's question directly and concisely."
            ),
        },
        {
            "role": "user",
            "content": f"QUESTION:\n{query}\n\nEVIDENCE:\n{evidence or '[NO EVIDENCE RETRIEVED]'}",
        },
    ]
    started = time.perf_counter()
    result = _provider_run_chat(
        profile,
        messages,
        {"temperature": 0.0, "top_p": 1.0, "max_tokens": 180},
    )
    wall_ms = round((time.perf_counter() - started) * 1000, 3)
    if not result.get("ok"):
        return {
            "id": str(query_truth.get("id") or retrieval_query.get("id") or ""),
            "query": query,
            "status": "error",
            "error": str(result.get("error") or result.get("error_type") or "Assistant backend failed"),
            "wall_ms": wall_ms,
            "evidence_supports_answer": evidence_supports,
        }

    answer = str(result.get("text") or "").strip()
    refusal = "not_established" in answer.casefold()
    truth_pass = _contains_terms(answer, all_terms, any_terms)
    fail_closed_pass = (not refusal) if evidence_supports else refusal
    unsupported_answer = bool(not evidence_supports and not refusal)
    false_not_established = bool(evidence_supports and refusal)
    grounded_pass = bool(truth_pass) if evidence_supports else bool(refusal)
    return {
        "id": str(query_truth.get("id") or retrieval_query.get("id") or ""),
        "query": query,
        "status": "evaluated",
        "answer": answer,
        "wall_ms": wall_ms,
        "model": str(result.get("model") or ""),
        "evidence_supports_answer": evidence_supports,
        "source_truth_pass": bool(truth_pass),
        "fail_closed_pass": bool(fail_closed_pass),
        "grounded_behavior_pass": bool(grounded_pass),
        "unsupported_answer": unsupported_answer,
        "false_not_established": false_not_established,
        "not_established": refusal,
        "answer_must_contain_all": all_terms,
        "answer_must_contain_any": any_terms,
        "evidence_chars": len(evidence),
    }


def aggregate_llm_queries(items: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [item for item in items if item.get("status") == "evaluated"]
    errors = [item for item in items if item.get("status") == "error"]
    if not evaluated:
        return {
            "query_count": len(items),
            "evaluated_query_count": 0,
            "error_query_count": len(errors),
            "source_answer_accuracy": None,
            "grounded_behavior_accuracy": None,
            "fail_closed_accuracy": None,
            "unsupported_answer_rate": None,
            "false_not_established_rate": None,
        }
    total = len(evaluated)
    return {
        "query_count": len(items),
        "evaluated_query_count": total,
        "error_query_count": len(errors),
        "source_answer_accuracy": round(sum(1 for item in evaluated if item.get("source_truth_pass")) / total, 6),
        "grounded_behavior_accuracy": round(sum(1 for item in evaluated if item.get("grounded_behavior_pass")) / total, 6),
        "fail_closed_accuracy": round(sum(1 for item in evaluated if item.get("fail_closed_pass")) / total, 6),
        "unsupported_answer_rate": round(sum(1 for item in evaluated if item.get("unsupported_answer")) / total, 6),
        "false_not_established_rate": round(sum(1 for item in evaluated if item.get("false_not_established")) / total, 6),
        "mean_wall_ms": round(sum(float(item.get("wall_ms") or 0.0) for item in evaluated) / total, 3),
    }


def evaluate_arm_queries(
    *,
    profile: dict[str, Any],
    fixture: dict[str, Any],
    arm_result: dict[str, Any],
    max_evidence_chars: int = 14000,
) -> dict[str, Any]:
    retrieval = arm_result.get("retrieval") if isinstance(arm_result.get("retrieval"), dict) else {}
    retrieval_queries = {
        str(item.get("id") or ""): item
        for item in (retrieval.get("queries") or [])
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for index, query_truth in enumerate(((fixture.get("truth") or {}).get("queries") or []), start=1):
        if not isinstance(query_truth, dict):
            continue
        query_id = str(query_truth.get("id") or f"q{index}")
        retrieval_query = retrieval_queries.get(query_id)
        if not retrieval_query:
            rows.append(
                {
                    "id": query_id,
                    "query": str(query_truth.get("query") or ""),
                    "status": "blocked",
                    "error": "No semantic retrieval result is available for this query",
                }
            )
            continue
        rows.append(
            evaluate_query_with_llm(
                profile=profile,
                query_truth=query_truth,
                retrieval_query=retrieval_query,
                max_evidence_chars=max_evidence_chars,
            )
        )
    return {
        "status": "evaluated" if rows and all(item.get("status") == "evaluated" for item in rows) else "partial",
        "summary": aggregate_llm_queries(rows),
        "queries": rows,
    }
