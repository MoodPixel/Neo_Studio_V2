from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.preview_actions import PREVIEW_ACTIONS
from neo_app.image.provider_action_regression import run_provider_action_regression_matrix
from neo_app.release_hygiene import audit_release_archive, build_clean_release_zip

SCHEMA_ID = "neo.image.provider_action_release_integration_audit.v1"
CASE_SCHEMA_ID = "neo.image.provider_action_release_integration_case.v1"
REQUIRED_BRIDGE_VERSION = "1.2.1"
REQUIRED_NATIVE_OPERATION = "native_txt2img_upscale"
RELEASE_GUIDE = "guides/01_IMAGE/provider_action_release_integration.md"

TEXT_SUFFIXES = {
    ".bat", ".cfg", ".conf", ".css", ".csv", ".html", ".ini", ".js",
    ".json", ".md", ".ps1", ".py", ".txt", ".yaml", ".yml",
}

# Phase 14 currently requires zero release-facing absolute-path allowlists.
# Synthetic redaction fixtures must be assembled at runtime rather than stored
# as machine-path literals in public source files.
SYNTHETIC_PATH_FIXTURE_ALLOWLIST: set[str] = set()

ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]):[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[A-Za-z0-9._-]+/"),
)

SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("xai_key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)

REQUIRED_GUIDE_SECTIONS = (
    "## Upgrade order",
    "## Required Forge Bridge contract",
    "## Complete action-routing reference",
    "## Selected-profile diagnostics",
    "## Migration notes",
    "## Physical Forge smoke-test checklist",
    "## Physical ComfyUI smoke-test checklist",
    "## Deterministic release gates",
    "## Public repository and package hygiene",
    "## Known limitations",
    "## Rollback procedure",
)


@dataclass(frozen=True)
class AuditCase:
    case_id: str
    category: str
    status: str
    detail: str
    evidence: dict[str, Any]
    schema: str = CASE_SCHEMA_ID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case(case_id: str, category: str, ok: bool, detail: str, **evidence: Any) -> AuditCase:
    return AuditCase(
        case_id=case_id,
        category=category,
        status="passed" if ok else "failed",
        detail=detail,
        evidence=evidence,
    )


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _bridge_version_from_python() -> str:
    path = ROOT / "neo_integrations/forge_neo_bridge/forge_extension/scripts/neo_bridge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "BRIDGE_VERSION" for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return ""


def _bridge_version_from_metadata() -> str:
    text = _read("neo_integrations/forge_neo_bridge/forge_extension/metadata.ini")
    match = re.search(r"(?im)^Version\s*=\s*([^\s]+)\s*$", text)
    return match.group(1) if match else ""


def _scan_archive_text(archive_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path_findings: list[dict[str, Any]] = []
    secret_findings: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename.strip("/")
            if not name or Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = zf.read(info).decode("utf-8", errors="ignore")
            except Exception:
                continue
            if name not in SYNTHETIC_PATH_FIXTURE_ALLOWLIST:
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if any(pattern.search(line) for pattern in ABSOLUTE_PATH_PATTERNS):
                        path_findings.append({"path": name, "line": line_no, "sample": line[:180]})
                        if len(path_findings) >= 50:
                            break
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    secret_findings.append({"path": name, "kind": label, "sample": match.group(0)[:12] + "…"})
    return path_findings, secret_findings


def _required_action_ids() -> list[str]:
    return [str(action.get("id") or "") for action in PREVIEW_ACTIONS]


def run_release_integration_audit() -> dict[str, Any]:
    cases: list[AuditCase] = []

    matrix = run_provider_action_regression_matrix()
    matrix_ok = matrix.get("status") == "passed" and matrix.get("failed") == 0
    cases.append(_case(
        "phase13.matrix",
        "regression",
        matrix_ok,
        "Phase 13 provider-action matrix passes with no failed cases.",
        passed=matrix.get("passed"),
        failed=matrix.get("failed"),
        selected_profile_only=matrix.get("selected_profile_only"),
        automatic_provider_fallback=matrix.get("automatic_provider_fallback"),
    ))

    action_ids = _required_action_ids()
    cases.append(_case(
        "registry.inventory",
        "integration",
        len(action_ids) == 13 and len(action_ids) == len(set(action_ids)),
        "Canonical Preview action inventory contains 13 unique actions.",
        action_count=len(action_ids),
        action_ids=action_ids,
    ))

    guide_path = ROOT / RELEASE_GUIDE
    guide_exists = guide_path.is_file()
    guide_text = guide_path.read_text(encoding="utf-8") if guide_exists else ""
    cases.append(_case(
        "documentation.release_guide",
        "documentation",
        guide_exists,
        "Release and integration guide exists.",
        path=RELEASE_GUIDE,
    ))

    missing_sections = [section for section in REQUIRED_GUIDE_SECTIONS if section not in guide_text]
    cases.append(_case(
        "documentation.required_sections",
        "documentation",
        not missing_sections,
        "Release guide includes installation, migration, smoke-test, hygiene, limitation, and rollback sections.",
        missing_sections=missing_sections,
    ))

    missing_action_docs = [action_id for action_id in action_ids if f"`{action_id}`" not in guide_text]
    cases.append(_case(
        "documentation.action_table",
        "documentation",
        not missing_action_docs,
        "Release guide documents every canonical action ID.",
        missing_action_ids=missing_action_docs,
    ))

    bridge_py_version = _bridge_version_from_python()
    bridge_meta_version = _bridge_version_from_metadata()
    bridge_text = _read("neo_integrations/forge_neo_bridge/forge_extension/scripts/neo_bridge.py")
    bridge_readme = _read("neo_integrations/forge_neo_bridge/README.md")
    bridge_versions_ok = bridge_py_version == REQUIRED_BRIDGE_VERSION and bridge_meta_version == REQUIRED_BRIDGE_VERSION
    cases.append(_case(
        "bridge.version",
        "bridge",
        bridge_versions_ok,
        "Bridge implementation and metadata use the required release version.",
        required=REQUIRED_BRIDGE_VERSION,
        implementation=bridge_py_version,
        metadata=bridge_meta_version,
    ))

    bridge_contract_ok = (
        REQUIRED_NATIVE_OPERATION in bridge_text
        and '"native_post_hires": True' in bridge_text
        and '"native_post_hires_size_contract": True' in bridge_text
        and REQUIRED_NATIVE_OPERATION in bridge_readme
        and "native_post_hires" in bridge_readme
        and "native_post_hires_size_contract" in bridge_readme
    )
    cases.append(_case(
        "bridge.native_hires_contract",
        "bridge",
        bridge_contract_ok,
        "Bridge code and operator documentation advertise the complete native Hires capability and size-enforcement contract.",
        operation=REQUIRED_NATIVE_OPERATION,
    ))

    with tempfile.TemporaryDirectory(prefix="neo_phase14_release_") as tmp:
        archive_path = Path(tmp) / "Neo_Studio_V2_clean_release.zip"
        build = build_clean_release_zip(archive_path, ROOT)
        archive_audit = audit_release_archive(archive_path)
        cases.append(_case(
            "package.clean_release",
            "package_hygiene",
            bool(build.get("ok")) and bool(archive_audit.get("ok")),
            "Temporary public runtime archive passes the canonical release-exclusion audit.",
            files_included=build.get("files_included"),
            files_excluded=build.get("files_excluded"),
            blocked_entry_count=archive_audit.get("blocked_entry_count"),
        ))

        path_findings, secret_findings = _scan_archive_text(archive_path)
        cases.append(_case(
            "package.portable_paths",
            "package_hygiene",
            not path_findings,
            "Release-facing archive text contains no non-allowlisted absolute user/backend paths.",
            findings=path_findings,
            synthetic_fixture_allowlist=sorted(SYNTHETIC_PATH_FIXTURE_ALLOWLIST),
        ))
        cases.append(_case(
            "package.credentials",
            "package_hygiene",
            not secret_findings,
            "Release-facing archive text contains no obvious private keys or live credential formats.",
            findings=secret_findings,
        ))

    readme = _read("README.md")
    cases.append(_case(
        "documentation.readme_entrypoint",
        "documentation",
        "Forge / Forge Neo" in readme and "provider_action_release_integration.md" in readme,
        "README exposes the Forge profile and release-integration guide entry point.",
    ))

    locked = _read("neo_system_records/00_READ_FIRST/V2_LOCKED_FIXES_READ_FIRST.md")
    cases.append(_case(
        "records.release_lock",
        "records",
        "Phase 14 release documentation and integration audit" in locked,
        "Read-first record includes the Phase 14 release lock.",
    ))

    failed = sum(case.status == "failed" for case in cases)
    passed = len(cases) - failed
    return {
        "schema_id": SCHEMA_ID,
        "generated_at": _now(),
        "status": "passed" if failed == 0 else "failed",
        "passed": passed,
        "failed": failed,
        "total": len(cases),
        "required_bridge_version": REQUIRED_BRIDGE_VERSION,
        "selected_profile_only": matrix.get("selected_profile_only") is True,
        "automatic_provider_fallback": matrix.get("automatic_provider_fallback") is True,
        "physical_gpu_execution": False,
        "cases": [asdict(case) for case in cases],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit provider-aware Image action release documentation and integration gates.")
    parser.add_argument("--json-out", type=Path, help="Optional path for the machine-readable audit report.")
    args = parser.parse_args()

    report = run_release_integration_audit()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
