from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from neo_app.knowledge.project_documents import build_contextual_fragments

from .contracts import (
    ACI1_ARM_CURRENT,
    ACI1_ARM_DOCLING,
    ACI1_RESULT_SCHEMA_ID,
    load_manifest,
    stable_hash,
)
from .current_neo_adapter import prepare_with_current_neo
from .docling_adapter import prepare_with_docling
from .metrics import aggregate_query_metrics, extraction_metrics, retrieval_query_score
from .llm_benchmark import evaluate_arm_queries, resolve_llm_profile, run_llm_preflight
from .semantic_worker_client import SemanticWorkerError, run_semantic_worker_batch




def preflight_docling_runtime(python_executable: str) -> dict[str, Any]:
    requested = str(python_executable or '').strip()
    resolved = shutil.which(requested) if requested and not Path(requested).exists() else requested
    if not resolved or not Path(resolved).exists():
        return {
            "status": "blocked",
            "ready": False,
            "python": requested,
            "error": "Docling Python executable does not exist",
        }
    command = [
        str(resolved),
        "-c",
        (
            "import json, importlib.metadata, docling, rapidocr; "
            "from docling.datamodel.pipeline_options import RapidOcrOptions; "
            "RapidOcrOptions(lang=['iso:en']); "
            "print(json.dumps({'version': getattr(docling, '__version__', 'unknown'), "
            "'rapidocr_version': importlib.metadata.version('rapidocr')}))"
        ),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=False)
    except Exception as exc:
        return {"status": "blocked", "ready": False, "python": str(resolved), "error": str(exc)}
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "Docling import failed").strip()[-3000:]
        return {"status": "blocked", "ready": False, "python": str(resolved), "error": error}
    version = "unknown"
    try:
        payload = json.loads((completed.stdout or '').strip().splitlines()[-1])
        version = str(payload.get('version') or 'unknown')
        rapidocr_version = str(payload.get('rapidocr_version') or 'unknown')
    except Exception:
        rapidocr_version = 'unknown'
    return {
        "status": "ready",
        "ready": True,
        "python": str(resolved),
        "docling_version": version,
        "rapidocr_version": rapidocr_version,
        "pdf_ocr_engine": "rapidocr",
    }


def current_neo_pdf_preflight() -> dict[str, Any]:
    try:
        import pypdf  # type: ignore
        return {"status": "ready", "ready": True, "provider": "pypdf", "version": getattr(pypdf, "__version__", "unknown")}
    except Exception as pypdf_exc:
        try:
            import PyPDF2  # type: ignore
            return {"status": "ready", "ready": True, "provider": "PyPDF2", "version": getattr(PyPDF2, "__version__", "unknown")}
        except Exception as pypdf2_exc:
            return {
                "status": "blocked",
                "ready": False,
                "error": f"Neither pypdf nor PyPDF2 is importable: pypdf={pypdf_exc}; PyPDF2={pypdf2_exc}",
            }


def _load_reuse_report(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for fixture in payload.get("fixtures") or []:
        if isinstance(fixture, dict) and fixture.get("id"):
            rows[str(fixture.get("id"))] = fixture
    return rows


def _reusable_arm(previous_fixture: dict[str, Any] | None, arm_key: str) -> dict[str, Any] | None:
    if not isinstance(previous_fixture, dict):
        return None
    arm = ((previous_fixture.get("arms") or {}).get(arm_key) or {})
    if not isinstance(arm, dict) or arm.get("error") or arm.get("status") in {"error", "blocked"}:
        return None
    if arm_key == ACI1_ARM_CURRENT and "pdf_text_extractor_unavailable" in str(arm.get("parser") or ""):
        # Re-run a previously dependency-blocked PDF control arm after pypdf is installed.
        return None
    if not isinstance(arm.get("preparation"), dict) or not isinstance(arm.get("fragments"), list):
        return None
    reused = json.loads(json.dumps(arm, ensure_ascii=False, default=str))
    reused["reused_prepared_result"] = True
    # Retrieval/LLM must always be recomputed with the currently selected semantic stack.
    reused.pop("retrieval", None)
    reused.pop("llm", None)
    return reused


def _mean(values: list[float]) -> float | None:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    return round(sum(clean) / len(clean), 6) if clean else None


def _aggregate_arm(fixtures: list[dict[str, Any]], arm_key: str, *, parity_only: bool) -> dict[str, Any]:
    rows = [row for row in fixtures if not parity_only or bool(row.get('parity', True))]
    attempted = len(rows)
    available = []
    blocked = 0
    errors = 0
    for row in rows:
        arm = (row.get('arms') or {}).get(arm_key) or {}
        if arm.get('preparation'):
            available.append(arm)
        elif arm.get('status') == 'blocked':
            blocked += 1
        elif arm.get('error'):
            errors += 1
    preps = [arm.get('preparation') or {} for arm in available]
    perfs = [arm.get('performance') or {} for arm in available]
    required_total = sum(len(prep.get('required_terms') or []) for prep in preps)
    found_total = sum(len(prep.get('found_terms') or []) for prep in preps)
    statuses: dict[str, int] = {}
    for prep in preps:
        key = str(prep.get('status') or 'unknown')
        statuses[key] = statuses.get(key, 0) + 1
    query_count = 0
    query_passed = 0
    reciprocal_sum = 0.0
    for arm in available:
        for query in ((arm.get('retrieval') or {}).get('queries') or []):
            query_count += 1
            query_passed += 1 if query.get('passed') else 0
            reciprocal_sum += float(query.get('reciprocal_rank') or 0.0)
    llm_rows: list[dict[str, Any]] = []
    for arm in available:
        llm = arm.get('llm') if isinstance(arm.get('llm'), dict) else {}
        llm_rows.extend([item for item in (llm.get('queries') or []) if isinstance(item, dict)])
    llm_evaluated = [item for item in llm_rows if item.get('status') == 'evaluated']
    llm_count = len(llm_evaluated)

    return {
        'arm': arm_key,
        'parity_only': parity_only,
        'fixture_count': attempted,
        'evaluated_fixture_count': len(available),
        'blocked_fixture_count': blocked,
        'error_fixture_count': errors,
        'status_counts': statuses,
        'required_term_recall_weighted': round(found_total / required_total, 6) if required_total else None,
        'required_terms_found': found_total,
        'required_terms_total': required_total,
        'mean_locator_coverage': _mean([prep.get('locator_coverage') for prep in preps]),
        'mean_page_provenance_coverage': _mean([prep.get('page_provenance_coverage') for prep in preps]),
        'mean_bbox_provenance_coverage': _mean([prep.get('bbox_provenance_coverage') for prep in preps]),
        'total_table_blocks': sum(int(prep.get('table_blocks') or 0) for prep in preps),
        'total_picture_blocks': sum(int(prep.get('picture_blocks') or 0) for prep in preps),
        'mean_wall_ms': _mean([perf.get('wall_ms') for perf in perfs]),
        'retrieval_query_count': query_count,
        'retrieval_recall': round(query_passed / query_count, 6) if query_count else None,
        'retrieval_mrr': round(reciprocal_sum / query_count, 6) if query_count else None,
        'llm_query_count': len(llm_rows),
        'llm_evaluated_query_count': llm_count,
        'llm_source_answer_accuracy': (
            round(sum(1 for item in llm_evaluated if item.get('source_truth_pass')) / llm_count, 6) if llm_count else None
        ),
        'llm_grounded_behavior_accuracy': (
            round(sum(1 for item in llm_evaluated if item.get('grounded_behavior_pass')) / llm_count, 6) if llm_count else None
        ),
        'llm_fail_closed_accuracy': (
            round(sum(1 for item in llm_evaluated if item.get('fail_closed_pass')) / llm_count, 6) if llm_count else None
        ),
        'llm_unsupported_answer_rate': (
            round(sum(1 for item in llm_evaluated if item.get('unsupported_answer')) / llm_count, 6) if llm_count else None
        ),
        'llm_false_not_established_rate': (
            round(sum(1 for item in llm_evaluated if item.get('false_not_established')) / llm_count, 6) if llm_count else None
        ),
    }


def aggregate_report(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        'parity_only': {
            ACI1_ARM_CURRENT: _aggregate_arm(fixtures, ACI1_ARM_CURRENT, parity_only=True),
            ACI1_ARM_DOCLING: _aggregate_arm(fixtures, ACI1_ARM_DOCLING, parity_only=True),
        },
        'all_fixtures': {
            ACI1_ARM_CURRENT: _aggregate_arm(fixtures, ACI1_ARM_CURRENT, parity_only=False),
            ACI1_ARM_DOCLING: _aggregate_arm(fixtures, ACI1_ARM_DOCLING, parity_only=False),
        },
    }


class _PeakRssSampler:
    def __init__(self, interval: float = 0.02) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak = 0
        self.start = 0

    @staticmethod
    def _rss() -> int:
        try:
            import psutil  # type: ignore
            return int(psutil.Process(os.getpid()).memory_info().rss)
        except Exception:
            return 0

    def __enter__(self) -> "_PeakRssSampler":
        self.start = self._rss()
        self.peak = self.start

        def sample() -> None:
            while not self._stop.wait(self.interval):
                self.peak = max(self.peak, self._rss())

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.peak = max(self.peak, self._rss())
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)

    def metrics(self) -> dict[str, Any]:
        return {
            "rss_start_mb": round(self.start / 1024 / 1024, 3) if self.start else None,
            "rss_peak_mb": round(self.peak / 1024 / 1024, 3) if self.peak else None,
            "rss_peak_delta_mb": round(max(0, self.peak - self.start) / 1024 / 1024, 3) if self.peak and self.start else None,
        }


def _prepare_arm(fn: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    with _PeakRssSampler() as sampler:
        extraction = fn()
    elapsed = (time.perf_counter() - started) * 1000
    perf = {"wall_ms": round(elapsed, 3), **sampler.metrics()}
    return extraction, perf


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def _arm_result(
    *,
    fixture: dict[str, Any],
    extraction: dict[str, Any],
    perf: dict[str, Any],
) -> dict[str, Any]:
    path = Path(str(fixture.get("path")))
    fragments = build_contextual_fragments(extraction, filename=path.name)
    worker_perf = ((extraction.get("metadata") or {}).get("docling") or {}) if extraction.get("arm") == ACI1_ARM_DOCLING else {}
    return {
        "arm": extraction.get("arm"),
        "parser": extraction.get("parser"),
        "preparation": extraction_metrics(extraction, fixture.get("truth") or {}),
        "performance": {
            **perf,
            "worker_wall_ms": worker_perf.get("wall_ms"),
            "worker_rss_start_mb": worker_perf.get("rss_start_mb"),
            "worker_rss_peak_mb": worker_perf.get("rss_peak_mb"),
            "worker_rss_peak_delta_mb": worker_perf.get("rss_peak_delta_mb"),
            "worker_peak_vram_mb": worker_perf.get("peak_vram_mb"),
            "normalized_json_bytes": _json_size(extraction),
            "fragment_json_bytes": _json_size(fragments),
        },
        "fragment_count": len(fragments),
        "fragments": fragments,
        "metadata": extraction.get("metadata") or {},
        "raw_artifact": extraction.get("raw_artifact") or "",
    }


def _run_retrieval_for_arm(
    *,
    fixture: dict[str, Any],
    arm_result: dict[str, Any],
    output_dir: Path,
    backend: Any,
    candidate_k: int,
    top_k: int,
) -> dict[str, Any]:
    from .semantic_benchmark import HybridBenchmarkIndex

    arm = str(arm_result.get("arm") or "arm")
    fixture_id = str(fixture.get("id") or "fixture")
    collection = f"aci1_{stable_hash({'fixture': fixture_id, 'arm': arm}, 20)}"
    index = HybridBenchmarkIndex(root=output_dir / "semantic_index", collection_name=collection, backend=backend)
    try:
        index_meta = index.add(list(arm_result.get("fragments") or []))
        query_rows: list[dict[str, Any]] = []
        queries = ((fixture.get("truth") or {}).get("queries") or [])
        for idx, truth in enumerate(queries, start=1):
            query = str(truth.get("query") or "").strip()
            if not query:
                continue
            results = index.search(query, candidate_k=candidate_k, top_k=top_k)
            score = retrieval_query_score(truth, results, top_k)
            query_rows.append(
                {
                    "id": str(truth.get("id") or f"q{idx}"),
                    "query": query,
                    **score,
                    "results": [
                        {
                            "fragment_id": item.get("fragment_id"),
                            "title": item.get("title"),
                            "rerank_score": item.get("rerank_score"),
                            "rrf_score": item.get("rrf_score"),
                            "text_preview": str(item.get("text") or item.get("content") or "")[:700],
                            "source_locators": item.get("source_locators") or [],
                        }
                        for item in results
                    ],
                }
            )
        return {"status": "evaluated", "index": index_meta, "summary": aggregate_query_metrics(query_rows), "queries": query_rows}
    finally:
        index.close()


def _comparison(current: dict[str, Any], docling: dict[str, Any]) -> dict[str, Any]:
    c = current.get("preparation") or {}
    d = docling.get("preparation") or {}
    fields = [
        "chars",
        "block_count",
        "heading_blocks",
        "table_blocks",
        "picture_blocks",
        "locator_coverage",
        "page_provenance_coverage",
        "bbox_provenance_coverage",
        "required_term_recall",
    ]
    deltas: dict[str, Any] = {}
    for field in fields:
        cv, dv = c.get(field), d.get(field)
        if isinstance(cv, (int, float)) and isinstance(dv, (int, float)):
            deltas[field] = round(float(dv) - float(cv), 6)
    cr = ((current.get("retrieval") or {}).get("summary") or {})
    dr = ((docling.get("retrieval") or {}).get("summary") or {})
    retrieval_delta = {}
    for field in ("recall", "mrr"):
        if isinstance(cr.get(field), (int, float)) and isinstance(dr.get(field), (int, float)):
            retrieval_delta[field] = round(float(dr[field]) - float(cr[field]), 6)
    return {"docling_minus_current": deltas, "retrieval_docling_minus_current": retrieval_delta}


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Neo ACI-1 Context Preparation Bake-Off",
        "",
        f"Status: **{report.get('status')}**",
        "",
        "This report is experimental. It does not change Neo's production attachment parser.",
        "",
        "| Fixture | Arm | Status | Chars | Blocks | Headings | Tables | Locator cov. | Term recall | Wall ms | Retrieval recall | MRR |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fixture in report.get("fixtures") or []:
        for key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
            arm = (fixture.get("arms") or {}).get(key) or {}
            prep = arm.get("preparation") or {}
            perf = arm.get("performance") or {}
            ret = ((arm.get("retrieval") or {}).get("summary") or {})
            lines.append(
                "| {fixture} | {arm_name} | {status} | {chars} | {blocks} | {headings} | {tables} | {loc} | {term} | {wall} | {rr} | {mrr} |".format(
                    fixture=fixture.get("id"),
                    arm_name=key,
                    status=prep.get("status"),
                    chars=prep.get("chars"),
                    blocks=prep.get("block_count"),
                    headings=prep.get("heading_blocks"),
                    tables=prep.get("table_blocks"),
                    loc=prep.get("locator_coverage"),
                    term=prep.get("required_term_recall"),
                    wall=perf.get("wall_ms"),
                    rr=ret.get("recall"),
                    mrr=ret.get("mrr"),
                )
            )
    lines += ["", "## Runtime preflight", ""]
    docling = ((report.get("environment") or {}).get("docling_preflight") or {})
    lines.append(f"- Docling runtime: **{docling.get('status', 'unknown')}**")
    if docling.get('docling_version'):
        lines.append(f"- Docling version: `{docling.get('docling_version')}`")
    if docling.get('error'):
        lines.append(f"- Docling blocker: `{str(docling.get('error'))[:500]}`")
    semantic = report.get('semantic') or {}
    lines.append(f"- Semantic evaluation: **{semantic.get('status', 'unknown')}**")
    if semantic.get('embedding_model'):
        lines.append(f"- Embedding model: `{semantic.get('embedding_model')}`")
    if semantic.get('reranker_model'):
        lines.append(f"- Reranker model: `{semantic.get('reranker_model')}`")
    llm = report.get('llm') or {}
    lines.append(f"- Same-LLM evaluation: **{llm.get('status', 'unknown')}**")
    profile = llm.get('profile') if isinstance(llm.get('profile'), dict) else {}
    if profile.get('display_name') or profile.get('model'):
        lines.append(f"- Assistant profile/model: `{profile.get('display_name') or profile.get('profile_id') or 'default'}` / `{profile.get('model') or 'default'}`")

    lines += ["", "## Aggregate — parity fixtures", ""]
    parity = ((report.get('aggregate') or {}).get('parity_only') or {})
    lines += [
        "| Arm | Evaluated | Blocked | Errors | Weighted term recall | Mean locator coverage | Mean wall ms | Retrieval recall | MRR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
        item = parity.get(key) or {}
        lines.append(
            f"| {key} | {item.get('evaluated_fixture_count')} | {item.get('blocked_fixture_count')} | {item.get('error_fixture_count')} | "
            f"{item.get('required_term_recall_weighted')} | {item.get('mean_locator_coverage')} | {item.get('mean_wall_ms')} | "
            f"{item.get('retrieval_recall')} | {item.get('retrieval_mrr')} |"
        )

    lines += ["", "## Same-LLM aggregate — parity fixtures", ""]
    lines += [
        "| Arm | Evaluated queries | Source-answer accuracy | Grounded behavior | Fail-closed accuracy | Unsupported answer rate | False not-established rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
        item = parity.get(key) or {}
        lines.append(
            f"| {key} | {item.get('llm_evaluated_query_count')} | {item.get('llm_source_answer_accuracy')} | "
            f"{item.get('llm_grounded_behavior_accuracy')} | {item.get('llm_fail_closed_accuracy')} | "
            f"{item.get('llm_unsupported_answer_rate')} | {item.get('llm_false_not_established_rate')} |"
        )

    acceptance = report.get('acceptance') or {}
    lines += ["", "## Acceptance evidence", ""]
    lines.append(f"- Evidence complete: **{acceptance.get('evidence_complete')}**")
    lines.append(f"- Technical candidate ready for human review: **{acceptance.get('technical_candidate_ready')}**")
    lines.append(f"- Decision: **{acceptance.get('decision', 'unknown')}**")
    lines.append("- Production change authorized: **False**")
    for gate, value in (acceptance.get('gates') or {}).items():
        lines.append(f"- `{gate}`: `{value}`")

    blockers = report.get('blockers') or []
    if blockers:
        lines += ["", "## Blockers", ""]
        for blocker in blockers:
            lines.append(f"- {blocker}")

    lines += ["", "## Notes", ""]
    for note in report.get("notes") or []:
        lines.append(f"- {note}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _semantic_jobs(fixtures: list[dict[str, Any]], *, top_k: int) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    jobs: list[dict[str, Any]] = []
    blocked_arms: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for fixture in fixtures:
        queries = [item for item in ((fixture.get('truth') or {}).get('queries') or []) if isinstance(item, dict)]
        for arm_key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
            arm = ((fixture.get('arms') or {}).get(arm_key) or {})
            fragments = [item for item in (arm.get('fragments') or []) if isinstance(item, dict)]
            if not queries:
                arm['retrieval'] = {'status': 'not_requested_for_fixture', 'summary': aggregate_query_metrics([]), 'queries': []}
                continue
            if arm.get('status') in {'blocked', 'error'} or arm.get('error'):
                arm['retrieval'] = {'status': 'blocked', 'error': str(arm.get('error') or 'Preparation unavailable'), 'queries': []}
                blocked_arms.append((fixture, arm))
                continue
            if not fragments:
                zero_queries: list[dict[str, Any]] = []
                for index, truth in enumerate(queries, start=1):
                    score = retrieval_query_score(truth, [], top_k)
                    zero_queries.append({
                        'id': str(truth.get('id') or f'q{index}'),
                        'query': str(truth.get('query') or ''),
                        **score,
                        'results': [],
                    })
                arm['retrieval'] = {
                    'status': 'evaluated',
                    'index': {'fragment_count': 0, 'embedding_dimension': 0, 'reason': 'no_fragments'},
                    'summary': aggregate_query_metrics(zero_queries),
                    'queries': zero_queries,
                }
                continue
            jobs.append(
                {
                    'fixture_id': str(fixture.get('id') or 'fixture'),
                    'arm': arm_key,
                    'fragments': fragments,
                    'queries': queries,
                }
            )
    return jobs, blocked_arms


def _merge_semantic_results(fixtures: list[dict[str, Any]], worker_result: dict[str, Any]) -> None:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in worker_result.get('jobs') or []:
        if isinstance(item, dict):
            indexed[(str(item.get('fixture_id') or ''), str(item.get('arm') or ''))] = item
    for fixture in fixtures:
        fixture_id = str(fixture.get('id') or '')
        for arm_key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
            arm = ((fixture.get('arms') or {}).get(arm_key) or {})
            if arm.get('retrieval'):
                continue
            result = indexed.get((fixture_id, arm_key))
            if result:
                arm['retrieval'] = {
                    key: value for key, value in result.items() if key not in {'fixture_id', 'arm'}
                }
            else:
                arm['retrieval'] = {'status': 'blocked', 'error': 'Semantic worker returned no result for this arm', 'queries': []}


def _run_llm_evaluation(
    *,
    fixtures: list[dict[str, Any]],
    profile: dict[str, Any],
    max_evidence_chars: int,
) -> None:
    for fixture in fixtures:
        for arm_key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
            arm = ((fixture.get('arms') or {}).get(arm_key) or {})
            retrieval = arm.get('retrieval') if isinstance(arm.get('retrieval'), dict) else {}
            if retrieval.get('status') != 'evaluated':
                arm['llm'] = {
                    'status': 'blocked',
                    'error': 'LLM evaluation requires an evaluated strict semantic retrieval result',
                    'queries': [],
                }
                continue
            arm['llm'] = evaluate_arm_queries(
                profile=profile,
                fixture=fixture,
                arm_result=arm,
                max_evidence_chars=max_evidence_chars,
            )


def _numeric_not_worse(candidate: Any, baseline: Any, *, higher_is_better: bool = True) -> bool | None:
    if not isinstance(candidate, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return float(candidate) >= float(baseline) if higher_is_better else float(candidate) <= float(baseline)


def _acceptance(report: dict[str, Any], *, semantic_requested: bool, llm_requested: bool) -> dict[str, Any]:
    parity = ((report.get('aggregate') or {}).get('parity_only') or {})
    current = parity.get(ACI1_ARM_CURRENT) or {}
    docling = parity.get(ACI1_ARM_DOCLING) or {}
    fixture_total = int(docling.get('fixture_count') or 0)
    docling_complete = bool(
        fixture_total
        and int(docling.get('evaluated_fixture_count') or 0) == fixture_total
        and int(docling.get('blocked_fixture_count') or 0) == 0
        and int(docling.get('error_fixture_count') or 0) == 0
    )
    semantic_status = (report.get('semantic') or {}).get('status')
    llm_status = (report.get('llm') or {}).get('status')
    current_pdf_ready = bool((((report.get('environment') or {}).get('current_neo_pdf_preflight') or {}).get('ready')) is not False)
    evidence_complete = bool(
        docling_complete
        and current_pdf_ready
        and (not semantic_requested or semantic_status == 'complete')
        and (not llm_requested or llm_status == 'complete')
    )
    gates = {
        'current_neo_pdf_control_ready': current_pdf_ready,
        'docling_executed_all_parity_fixtures': docling_complete,
        'semantic_worker_complete': (semantic_status == 'complete') if semantic_requested else None,
        'same_llm_evaluation_complete': (llm_status == 'complete') if llm_requested else None,
        'extraction_truth_recall_not_worse': _numeric_not_worse(
            docling.get('required_term_recall_weighted'), current.get('required_term_recall_weighted')
        ),
        'retrieval_recall_not_worse': _numeric_not_worse(
            docling.get('retrieval_recall'), current.get('retrieval_recall')
        ) if semantic_requested else None,
        'llm_source_answer_accuracy_not_worse': _numeric_not_worse(
            docling.get('llm_source_answer_accuracy'), current.get('llm_source_answer_accuracy')
        ) if llm_requested else None,
        'llm_unsupported_answer_rate_not_worse': _numeric_not_worse(
            docling.get('llm_unsupported_answer_rate'), current.get('llm_unsupported_answer_rate'), higher_is_better=False
        ) if llm_requested else None,
        'llm_false_not_established_rate_not_worse': _numeric_not_worse(
            docling.get('llm_false_not_established_rate'), current.get('llm_false_not_established_rate'), higher_is_better=False
        ) if llm_requested else None,
        'production_route_unchanged': report.get('production_route_changed') is False,
    }
    comparable_gates = [value for value in gates.values() if isinstance(value, bool)]
    technical_candidate_ready = bool(evidence_complete and comparable_gates and all(comparable_gates))
    return {
        'evidence_complete': evidence_complete,
        'technical_candidate_ready': technical_candidate_ready,
        'decision': 'pending_human_review' if evidence_complete else 'evidence_incomplete',
        'production_change_authorized': False,
        'gates': gates,
        'note': 'ACI-1 evidence can qualify a candidate for review, but this harness never authorizes or performs the ACI-2 production integration.',
    }


def run_bakeoff(
    *,
    manifest_path: Path,
    output_dir: Path,
    docling_python: str,
    semantic: bool = False,
    semantic_python: str = '',
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B",
    semantic_device: str | None = None,
    candidate_k: int = 24,
    top_k: int = 6,
    llm: bool = False,
    backend_profile_id: str = '',
    llm_max_evidence_chars: int = 14000,
    save_raw_docling: bool = True,
    reuse_prepared_report: Path | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    manifest = load_manifest(Path(manifest_path))
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "docling_raw" if save_raw_docling else None
    reuse_rows = _load_reuse_report(reuse_prepared_report)

    blockers: list[str] = []
    current_pdf_preflight = current_neo_pdf_preflight()
    if not current_pdf_preflight.get("ready"):
        blockers.append("Current Neo PDF control is not fully ready: install pypdf in the production .venv before treating PDF parity as final evidence")
    docling_preflight = preflight_docling_runtime(docling_python)
    if not docling_preflight.get('ready'):
        blockers.append(f"Docling runtime blocked: {docling_preflight.get('error') or 'not ready'}")

    fixture_reports: list[dict[str, Any]] = []
    failures = 0
    manifest_fixtures = list(manifest.get("fixtures") or [])
    for fixture_index, fixture in enumerate(manifest_fixtures, start=1):
        path = Path(str(fixture.get("path")))
        fixture_id = str(fixture.get("id") or f"fixture_{fixture_index}")
        if progress:
            print(f"[ACI-1][{fixture_index}/{len(manifest_fixtures)}] {fixture_id}", flush=True)
        previous_fixture = reuse_rows.get(fixture_id)
        row: dict[str, Any] = {
            "id": fixture.get("id"),
            "path": str(path),
            "category": fixture.get("category"),
            "parity": bool(fixture.get("parity", True)),
            "note": fixture.get("note") or "",
            "truth": fixture.get("truth") or {},
            "arms": {},
        }
        if not path.exists():
            row["status"] = "missing_fixture"
            row["error"] = f"Fixture does not exist: {path}"
            failures += 1
            fixture_reports.append(row)
            continue

        current = _reusable_arm(previous_fixture, ACI1_ARM_CURRENT)
        if current is not None:
            if progress:
                print("  Current Neo: reused prepared result", flush=True)
        else:
            try:
                current_extraction, current_perf = _prepare_arm(lambda: prepare_with_current_neo(path))
                current = _arm_result(fixture=fixture, extraction=current_extraction, perf=current_perf)
                if progress:
                    print(f"  Current Neo: {current.get('preparation', {}).get('status', 'done')}", flush=True)
            except Exception as exc:  # noqa: BLE001
                current = {"arm": ACI1_ARM_CURRENT, "status": "error", "error": str(exc)}
                failures += 1
                if progress:
                    print(f"  Current Neo: ERROR {exc}", flush=True)

        docling = _reusable_arm(previous_fixture, ACI1_ARM_DOCLING)
        if docling is not None:
            if progress:
                print("  Docling: reused prepared result", flush=True)
        elif not docling_preflight.get('ready'):
            docling = {
                "arm": ACI1_ARM_DOCLING,
                "status": "blocked",
                "error": str(docling_preflight.get('error') or 'Docling runtime unavailable'),
            }
        else:
            try:
                if progress:
                    print("  Docling: running...", flush=True)
                docling_extraction, docling_perf = _prepare_arm(
                    lambda: prepare_with_docling(path, python_executable=docling_python, save_raw_dir=raw_dir)
                )
                docling = _arm_result(fixture=fixture, extraction=docling_extraction, perf=docling_perf)
                if progress:
                    print(f"  Docling: {docling.get('preparation', {}).get('status', 'done')} ({docling.get('performance', {}).get('wall_ms', 0)} ms)", flush=True)
            except Exception as exc:  # noqa: BLE001
                docling = {"arm": ACI1_ARM_DOCLING, "status": "error", "error": str(exc)}
                failures += 1
                if progress:
                    print(f"  Docling: ERROR {exc}", flush=True)

        row["arms"] = {ACI1_ARM_CURRENT: current, ACI1_ARM_DOCLING: docling}
        row["status"] = "complete" if not current.get("error") and not docling.get("error") and docling.get('status') != 'blocked' else "partial"
        fixture_reports.append(row)

    semantic_status: dict[str, Any] = {
        'enabled': bool(semantic),
        'status': 'disabled',
        'fallback_allowed': False,
    }
    if semantic:
        if progress:
            print(f"[ACI-1][SEMANTIC] embedding={embedding_model} reranker={reranker_model}", flush=True)
        semantic_python = str(semantic_python or docling_python or '').strip()
        jobs, _blocked = _semantic_jobs(fixture_reports, top_k=top_k)
        if not jobs:
            semantic_status = {
                'enabled': True,
                'status': 'blocked',
                'fallback_allowed': False,
                'error': 'No prepared arms produced semantic benchmark jobs',
            }
            blockers.append('Strict semantic runtime blocked: no benchmark jobs were available')
        else:
            worker_path = Path(__file__).resolve().parents[3] / 'scripts' / 'aci1_semantic_worker.py'
            try:
                worker_result = run_semantic_worker_batch(
                    python_executable=semantic_python,
                    worker_path=worker_path,
                    jobs=jobs,
                    output_dir=output,
                    embedding_model=embedding_model,
                    reranker_model=reranker_model,
                    device=semantic_device,
                    candidate_k=candidate_k,
                    top_k=top_k,
                )
                _merge_semantic_results(fixture_reports, worker_result)
                worker_state = str(worker_result.get('status') or 'blocked')
                semantic_status = {
                    'enabled': True,
                    'status': worker_state,
                    'embedding_runtime': 'sentence_transformers',
                    'embedding_model': embedding_model,
                    'reranker_runtime': 'sentence_transformers.CrossEncoder',
                    'reranker_model': reranker_model,
                    'vector_index': 'chroma_read_write_fixture_local',
                    'lexical_index': 'sqlite_fts5_fixture_local',
                    'fusion': 'weighted_rrf',
                    'fallback_allowed': False,
                    'worker': worker_result.get('worker') or {},
                    'runtime': worker_result.get('runtime') or {},
                    'qualification': worker_result.get('qualification') or {},
                    'wall_ms': worker_result.get('wall_ms'),
                    'error': worker_result.get('error') or '',
                }
                if worker_state != 'complete':
                    blockers.append(f"Strict semantic runtime blocked/partial: {worker_result.get('error') or worker_state}")
            except SemanticWorkerError as exc:
                for fixture in fixture_reports:
                    for arm_key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
                        arm = ((fixture.get('arms') or {}).get(arm_key) or {})
                        if not arm.get('retrieval'):
                            arm['retrieval'] = {'status': 'blocked', 'error': str(exc), 'queries': []}
                semantic_status = {
                    'enabled': True,
                    'status': 'blocked',
                    'embedding_model': embedding_model,
                    'reranker_model': reranker_model,
                    'fallback_allowed': False,
                    'error': str(exc),
                }
                blockers.append(f"Strict semantic runtime blocked: {exc}")

    llm_status: dict[str, Any] = {'enabled': bool(llm), 'status': 'disabled'}
    if llm:
        if progress:
            print("[ACI-1][LLM] same Assistant backend evaluation", flush=True)
        if not semantic:
            llm_status = {'enabled': True, 'status': 'blocked', 'error': 'Same-LLM evaluation requires --semantic'}
            blockers.append('Same-LLM evaluation blocked: semantic evaluation was not enabled')
        elif semantic_status.get('status') != 'complete':
            llm_status = {'enabled': True, 'status': 'blocked', 'error': 'Strict semantic evaluation did not complete'}
            blockers.append('Same-LLM evaluation blocked: strict semantic retrieval did not complete')
        else:
            try:
                profile = resolve_llm_profile(backend_profile_id)
                preflight = run_llm_preflight(profile)
                if not preflight.get('ready'):
                    llm_status = {'enabled': True, 'status': 'blocked', 'preflight': preflight, 'profile': preflight.get('profile') or {}}
                    blockers.append(f"Same-LLM evaluation blocked: {preflight.get('error') or 'Assistant backend unavailable'}")
                else:
                    _run_llm_evaluation(
                        fixtures=fixture_reports,
                        profile=profile,
                        max_evidence_chars=max(1000, int(llm_max_evidence_chars)),
                    )
                    llm_errors = 0
                    llm_blocked = 0
                    for fixture in fixture_reports:
                        for arm_key in (ACI1_ARM_CURRENT, ACI1_ARM_DOCLING):
                            arm_llm = ((((fixture.get('arms') or {}).get(arm_key) or {}).get('llm') or {}))
                            if arm_llm.get('status') == 'partial':
                                llm_errors += 1
                            elif arm_llm.get('status') == 'blocked':
                                llm_blocked += 1
                    llm_state = 'complete' if llm_errors == 0 and llm_blocked == 0 else 'partial'
                    llm_status = {
                        'enabled': True,
                        'status': llm_state,
                        'preflight': preflight,
                        'profile': preflight.get('profile') or {},
                        'max_evidence_chars': max(1000, int(llm_max_evidence_chars)),
                        'chat_storage_used': False,
                        'memory_writeback_used': False,
                    }
                    if llm_state != 'complete':
                        blockers.append('Same-LLM evaluation was partial because one or more arm/query evaluations were unavailable')
            except Exception as exc:  # noqa: BLE001
                llm_status = {'enabled': True, 'status': 'blocked', 'error': str(exc)}
                blockers.append(f"Same-LLM evaluation blocked: {exc}")

    for row in fixture_reports:
        arms = row.get('arms') or {}
        row['comparison'] = _comparison(arms.get(ACI1_ARM_CURRENT) or {}, arms.get(ACI1_ARM_DOCLING) or {})

    aggregate = aggregate_report(fixture_reports)
    if failures:
        status = 'partial'
    elif blockers:
        status = 'blocked'
    else:
        status = 'complete'
    report = {
        "schema_id": ACI1_RESULT_SCHEMA_ID,
        "status": status,
        "production_route_changed": False,
        "manifest": manifest.get("manifest_path"),
        "corpus_kind": manifest.get("corpus_kind") or "external_or_local",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "docling_python": str(docling_python),
            "semantic_python": str(semantic_python or ''),
            "docling_preflight": docling_preflight,
            "current_neo_pdf_preflight": current_pdf_preflight,
            "reused_prepared_report": str(reuse_prepared_report or ""),
        },
        "semantic": semantic_status,
        "llm": llm_status,
        "blockers": blockers,
        "fixtures": fixture_reports,
        "aggregate": aggregate,
        "notes": [
            "Arm A uses the ordinary Assistant legacy attachment extractor exactly as the current user path does.",
            "Arm B runs Docling in an external Python process and returns a normalized structured extraction.",
            "Both arms use Neo's existing build_contextual_fragments function after preparation.",
            "When enabled, semantic evaluation runs out-of-process in an isolated Python runtime and requires real Sentence Transformers embeddings, Chroma, SQLite FTS5, weighted RRF, and a real CrossEncoder reranker; no hash/lexical fallback is accepted.",
            "When enabled, same-LLM evaluation resolves one Neo Assistant backend profile once and uses that identical profile over both arms without chat persistence or memory writeback.",
            "ACI-1B uses a deterministic redistributable reference corpus; private/client documents remain optional local fixtures, not repository requirements.",
            "Document screenshot rows are parity=false because current Neo image attachments use native image intelligence rather than the legacy document extractor; they are supplemental capability evidence only.",
            "Prepared arm results may be reused from an earlier partial report, but retrieval and same-LLM evaluation are always recomputed for the current semantic configuration.",
            "This harness records evidence only. It never switches Neo's production parser or authorizes ACI-2 integration.",
        ],
    }
    report['acceptance'] = _acceptance(report, semantic_requested=semantic, llm_requested=llm)
    json_path = output / "aci1_bakeoff_report.json"
    md_path = output / "aci1_bakeoff_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _write_markdown(report, md_path)
    report["report_json"] = str(json_path)
    report["report_markdown"] = str(md_path)
    return report
