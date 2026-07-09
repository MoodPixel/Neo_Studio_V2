from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_TYPE, PHASE, WORKSPACE_APP
from .payload_schema import normalize_block
from .replay import build_replay_payload
from .readiness import build_replay_readiness, summarize_replay_readiness


def build_memory_event_payload_from_readiness(readiness: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    src = readiness if isinstance(readiness, dict) else {}
    block = src.get('block') if isinstance(src.get('block'), dict) else src.get('payload')
    normalized = normalize_block({'extensions': {EXTENSION_ID: block}} if isinstance(block, dict) else {})
    metadata = normalized.get('metadata') if isinstance(normalized.get('metadata'), dict) else {}
    normalization = metadata.get('normalization') if isinstance(metadata.get('normalization'), dict) else {}
    multi_pass = src.get('multi_pass') if isinstance(src.get('multi_pass'), dict) else metadata.get('multi_pass', {})
    assistant_summary = src.get('assistant_summary') or metadata.get('assistant_summary') or 'ADetailer replay-ready settings were captured.'
    readiness_report = build_replay_readiness(normalized, route=route or src.get('route') or {}, node_inventory=src.get('available_nodes'), node_status=src.get('node_availability') or src.get('node_status'))
    memory_readiness_summary = summarize_replay_readiness(readiness_report)
    return {
        'extension_id': EXTENSION_ID,
        'extension_type': EXTENSION_TYPE,
        'workspace_app': WORKSPACE_APP,
        'phase': 'K',
        'route': deepcopy(route or src.get('route') or {}),
        'assets': deepcopy(normalized.get('assets') or {}),
        'params': deepcopy(normalized.get('params') or {}),
        'outputs': deepcopy(src.get('outputs') or {}),
        'workflow_summary': assistant_summary,
        'assistant_summary': assistant_summary,
        'replay_payload': build_replay_payload({'payloads': {EXTENSION_ID: normalized}}),
        'node_availability': deepcopy(src.get('node_availability') or src.get('node_status') or {}),
        'gated_reason': src.get('gated_reason') or src.get('reason') or '',
        'multi_pass': deepcopy(multi_pass or {
            'detailer_pass_count': normalization.get('detailer_pass_count', 0),
            'enabled_detailer_pass_count': normalization.get('enabled_detailer_pass_count', 0),
            'manual_unit_count': normalization.get('all_manual_box_count', 0),
            'sep_unit_count': normalization.get('sep_target_count', 0),
        }),
        'restore_policy': readiness_report.get('restore_policy'),
        'replay_readiness': readiness_report,
        'memory_readiness': {
            'ready_for_memory': True,
            'ready_for_replay_restore': True,
            'ready_to_auto_enable': False,
            'summary': memory_readiness_summary,
            'checklist': deepcopy(readiness_report.get('checklist') or {}),
            'blockers': deepcopy(readiness_report.get('blockers') or []),
        },
    }
