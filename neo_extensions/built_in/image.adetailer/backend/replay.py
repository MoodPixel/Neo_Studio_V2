from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, EXTENSION_TYPE, PHASE, WORKSPACE_APP
from .payload_schema import default_payload_block, normalize_block
from .support_matrix import support_for_route
from .validation import validate_and_normalize_payload
from .readiness import build_replay_readiness, summarize_replay_readiness


def _extract_replay_block(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    src = metadata if isinstance(metadata, dict) else {}
    for key in ('replay_payload', 'safe_replay_payload'):
        value = src.get(key)
        if isinstance(value, dict):
            if isinstance(value.get('extensions'), dict) and isinstance(value['extensions'].get(EXTENSION_ID), dict):
                return value['extensions'][EXTENSION_ID]
            if isinstance(value.get(EXTENSION_ID), dict):
                return value[EXTENSION_ID]
            if isinstance(value.get('payload'), dict):
                return value['payload']
    payloads = src.get('replay_payloads') if isinstance(src.get('replay_payloads'), dict) else {}
    if isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads[EXTENSION_ID]
    payload_container = src.get('payloads') if isinstance(src.get('payloads'), dict) else {}
    if isinstance(payload_container.get(EXTENSION_ID), dict):
        return payload_container[EXTENSION_ID]
    return default_payload_block()


def build_replay_payload(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a route-safe ADetailer replay payload from output metadata.

    K2 promotes replay from the old skeleton shape to a normalized extension block
    while keeping it disabled until the target route/node/model checks pass again.
    """
    block = normalize_block({'extensions': {EXTENSION_ID: _extract_replay_block(metadata)}})
    block['enabled'] = False
    block.setdefault('metadata', {})
    block['metadata'].update({
        'extension_id': EXTENSION_ID,
        'extension_type': EXTENSION_TYPE,
        'workspace_app': WORKSPACE_APP,
        'source_phase': 'K',
        'replay_restore_ready': True,
        'revalidation_required': True,
        'restore_policy': 'restore_disabled_until_route_nodes_detector_models_pass_cards_manual_boxes_and_impact_pack_validate',
    })
    return {'extensions': {EXTENSION_ID: block}}


def restore_from_replay(payload: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None, node_inventory: Any = None) -> dict[str, Any]:
    """Normalize replay data and say whether it can be re-enabled for this route.

    The caller may choose to set enabled=true after showing the user the validation
    result. We do not blindly enable because V1-style pass cards may reference local
    detector/SAM models or Impact Pack nodes that are absent on this machine.
    """
    block = normalize_block(payload or {})
    block['enabled'] = False
    block.setdefault('metadata', {})
    block['metadata'].update({
        'extension_id': EXTENSION_ID,
        'extension_type': EXTENSION_TYPE,
        'restored_from_replay': True,
        'restore_policy': 'user_confirm_after_revalidation',
        'source_phase': PHASE,
    })
    support = support_for_route(route or {})
    validation = validate_and_normalize_payload({'extensions': {EXTENSION_ID: {**block, 'enabled': True}}}, route=route or {}, available_nodes=node_inventory)
    readiness = build_replay_readiness(block, route=route or {}, node_inventory=node_inventory, node_status=validation.get('node_status') if isinstance(validation, dict) else {})
    can_enable = support.get('state') in ACTIVE_ROUTE_STATES and bool(validation.get('workflow_patch_allowed')) and bool(readiness.get('can_enable_after_revalidation'))
    reason = '' if can_enable else (summarize_replay_readiness(readiness) or validation.get('reason') or support.get('reason') or 'Replay restored disabled until ADetailer can be revalidated for this route.')
    block.setdefault('metadata', {})
    if isinstance(block['metadata'], dict):
        block['metadata'].update({
            'replay_readiness': readiness,
            'can_enable_after_revalidation': can_enable,
            'restore_enabled': False,
        })
    return {
        'extension_id': EXTENSION_ID,
        'enabled': False,
        'can_enable_after_revalidation': can_enable,
        'payload': block,
        'route': deepcopy(route or {}),
        'support': support,
        'validation': validation,
        'readiness': readiness,
        'reason': reason,
    }
