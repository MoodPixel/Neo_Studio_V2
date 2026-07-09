from __future__ import annotations

import json
import re
import uuid
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.forge import CANONICAL_TEMPLATE_KINDS, HIERARCHY, KIND_ALIASES, load_builder_template, save_forge_record_payload

SUPPORTED_EXTENSIONS = ('.json', '.md')
SCOPE_MODES = ('preserve_file_scope', 'apply_active_scope_where_empty', 'force_active_scope')
CONFLICT_MODES = ('skip_existing', 'replace_existing', 'copy_with_new_id')


def _slug(value: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(value or '').strip().lower()).strip('_')
    return cleaned[:72] or 'record'


def _normalise_kind(kind: Any) -> str:
    candidate = str(kind or '').strip().lower().replace(' ', '_').replace('/', '_')
    candidate = KIND_ALIASES.get(candidate, candidate)
    if candidate == 'region_kingdom':
        candidate = 'region'
    if candidate == 'city_settlement':
        candidate = 'city'
    if candidate in CANONICAL_TEMPLATE_KINDS:
        return candidate
    return 'legend'


def _nowish_id(kind: str, label: str) -> str:
    return f'{kind}_{_slug(label)}_{uuid.uuid4().hex[:6]}'


def _ensure_record_shape(payload: dict[str, Any], source_name: str = '') -> dict[str, Any]:
    record = deepcopy(payload) if isinstance(payload, dict) else {}
    kind = _normalise_kind(record.get('kind'))
    base = deepcopy(load_builder_template(kind).get('json_template_payload') or {})
    # Merge shallow top-level so missing template shells exist, while preserving uploaded nested data.
    for key, value in base.items():
        if key not in record or record.get(key) in (None, ''):
            record[key] = deepcopy(value)
    record['kind'] = kind
    label = str(record.get('label') or record.get('display_label') or Path(source_name).stem or kind).strip()
    if not record.get('label'):
        record['label'] = label
    if not record.get('display_label'):
        record['display_label'] = label
    if not record.get('summary'):
        record['summary'] = ''
    if not record.get('id'):
        record['id'] = _nowish_id(kind, label)
    record.setdefault('links', {})
    record['links'].setdefault('scope', {})
    record['links'].setdefault('related', {})
    record['links'].setdefault('reverse_links', {'strategy': 'derived', 'materialized': {}})
    record.setdefault('fields', {})
    record.setdefault('memory_hints', {})
    record.setdefault('meta', {})
    record['meta'].setdefault('status', 'draft')
    return record


def _parse_json_payload(value: Any, source_name: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [_ensure_record_shape(item, source_name) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if isinstance(value.get('records'), list):
        return [_ensure_record_shape(item, source_name) for item in value.get('records', []) if isinstance(item, dict)]
    if isinstance(value.get('all_records'), list):
        return [_ensure_record_shape(item, source_name) for item in value.get('all_records', []) if isinstance(item, dict)]
    if isinstance(value.get('payload'), dict) and value.get('kind') is None:
        return [_ensure_record_shape(value.get('payload'), source_name)]
    if value.get('kind') or value.get('fields') or value.get('links'):
        return [_ensure_record_shape(value, source_name)]
    return []


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1]
    body = parts[2].lstrip('\r\n')
    meta: dict[str, Any] = {}
    current_parent: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        if not line.startswith((' ', '\t')) and ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip().strip('"\'')
            if value == '':
                meta[key] = {}
                current_parent = key
            else:
                meta[key] = value
                current_parent = None
        elif current_parent and ':' in line:
            key, value = line.split(':', 1)
            if not isinstance(meta.get(current_parent), dict):
                meta[current_parent] = {}
            meta[current_parent][key.strip()] = value.strip().strip('"\'')
    return meta, body


def _markdown_to_record(text: str, source_name: str) -> dict[str, Any]:
    meta, body = _parse_frontmatter(text)
    kind = _normalise_kind(meta.get('kind') or 'legend')
    base = deepcopy(load_builder_template(kind).get('json_template_payload') or {})
    label = str(meta.get('display_label') or meta.get('label') or Path(source_name).stem).strip()
    base.update({
        'id': str(meta.get('id') or '') or _nowish_id(kind, label),
        'kind': kind,
        'label': str(meta.get('label') or label),
        'display_label': label,
        'summary': str(meta.get('summary') or body[:500]).strip(),
    })
    if isinstance(meta.get('scope'), dict):
        base.setdefault('links', {}).setdefault('scope', {}).update(meta['scope'])
    base.setdefault('fields', {}).setdefault('rich_authoring', {})['longform_' + ('practice' if kind == 'ritual' else kind) + '_overview'] = body.strip()
    base.setdefault('meta', {})['import_source_file'] = source_name
    base['meta'].setdefault('status', 'draft')
    return _ensure_record_shape(base, source_name)


def parse_import_bytes(filename: str, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    suffix = Path(filename).suffix.lower()
    if suffix == '.zip':
        with zipfile.ZipFile(BytesIO(data)) as zf:
            for name in sorted(zf.namelist()):
                if name.endswith('/'):
                    continue
                ext = Path(name).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    raw = zf.read(name)
                    text = raw.decode('utf-8-sig')
                    if ext == '.json':
                        records.extend(_parse_json_payload(json.loads(text), name))
                    elif ext == '.md':
                        records.append(_markdown_to_record(text, name))
                except Exception as exc:
                    warnings.append(f'{name}: {exc}')
    elif suffix == '.json':
        records.extend(_parse_json_payload(json.loads(data.decode('utf-8-sig')), filename))
    elif suffix == '.md':
        records.append(_markdown_to_record(data.decode('utf-8-sig'), filename))
    else:
        warnings.append(f'Unsupported file type: {filename}')
    # Deduplicate exact IDs in same upload by keeping last and warning.
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (_normalise_kind(record.get('kind')), str(record.get('id') or ''))
        if key in seen:
            warnings.append(f'Duplicate record in upload: {key[0]} / {key[1]} — kept latest occurrence.')
        seen[key] = record
    return list(seen.values()), warnings


def _set_if(scope: dict[str, Any], key: str, value: str, force: bool) -> bool:
    if not value:
        return False
    if force or not scope.get(key):
        scope[key] = value
        return True
    return False


def apply_active_scope(record: dict[str, Any], scope_kind: str = '', scope_id: str = '', mode: str = 'apply_active_scope_where_empty') -> tuple[dict[str, Any], list[str]]:
    clean_mode = mode if mode in SCOPE_MODES else 'apply_active_scope_where_empty'
    if clean_mode == 'preserve_file_scope' or not scope_kind or not scope_id:
        return record, []
    force = clean_mode == 'force_active_scope'
    applied: list[str] = []
    kind = _normalise_kind(record.get('kind'))
    scope = record.setdefault('links', {}).setdefault('scope', {})
    scope_kind = _normalise_kind(scope_kind)
    # Do not set a record as its own parent except for explicit force? keep safe.
    if kind == scope_kind and str(record.get('id')) == scope_id:
        return record, []
    if scope_kind == 'universe':
        if _set_if(scope, 'universe_id', scope_id, force): applied.append('universe_id')
    elif scope_kind == 'world':
        for key in ('world_id', 'origin_world_id', 'current_world_id'):
            if key in scope or kind in ('region','city','location','character','organization','artifact','ritual','cycle','creature','legend','scenario','relationship'):
                if _set_if(scope, key, scope_id, force): applied.append(key)
    elif scope_kind == 'region':
        for key in ('region_id', 'origin_region_id', 'current_region_id'):
            if key in scope or kind in ('city','location','character','organization','artifact','ritual','cycle','creature','legend','scenario','relationship'):
                if _set_if(scope, key, scope_id, force): applied.append(key)
    elif scope_kind == 'city':
        for key in ('city_id', 'origin_city_id', 'current_city_id'):
            if key in scope or kind in ('location','character','organization','artifact','scenario','relationship'):
                if _set_if(scope, key, scope_id, force): applied.append(key)
    elif scope_kind == 'location':
        for key in ('location_id', 'origin_location_id', 'current_location_id', 'base_location_id'):
            if key in scope or kind in ('character','organization','artifact','ritual','cycle','creature','legend','scenario','relationship'):
                if _set_if(scope, key, scope_id, force): applied.append(key)
    elif scope_kind == 'scenario':
        related = record.setdefault('links', {}).setdefault('related', {})
        scenario_ids = related.setdefault('scenario_ids', [])
        if isinstance(scenario_ids, list) and scope_id not in scenario_ids and kind != 'scenario':
            scenario_ids.append(scope_id)
            applied.append('related.scenario_ids')
    if applied:
        record.setdefault('meta', {})['import_applied_scope'] = {'scope_kind': scope_kind, 'scope_id': scope_id, 'mode': clean_mode, 'fields': applied}
    return record, applied


def preview_import_records(filename: str, data: bytes, *, scope_kind: str = '', scope_id: str = '', scope_mode: str = 'apply_active_scope_where_empty', conflict_mode: str = 'skip_existing') -> dict[str, Any]:
    records, warnings = parse_import_bytes(filename, data)
    details = []
    counts: dict[str, int] = {}
    applied_total = 0
    for record in records:
        preview_record = deepcopy(record)
        preview_record, applied = apply_active_scope(preview_record, scope_kind, scope_id, scope_mode)
        applied_total += len(applied)
        kind = _normalise_kind(preview_record.get('kind'))
        counts[kind] = counts.get(kind, 0) + 1
        details.append({
            'id': preview_record.get('id'),
            'kind': kind,
            'display_label': preview_record.get('display_label') or preview_record.get('label'),
            'summary': preview_record.get('summary'),
            'scope': preview_record.get('links', {}).get('scope', {}),
            'applied_scope_fields': applied,
            'action': 'import',
        })
    return {
        'schema_id': 'neo.roleplay.forge_import.preview.v1',
        'status': 'ready' if records else 'empty',
        'filename': filename,
        'record_count': len(records),
        'counts_by_kind': counts,
        'warnings': warnings,
        'scope_mode': scope_mode,
        'scope_kind': scope_kind,
        'scope_id': scope_id,
        'applied_scope_field_count': applied_total,
        'conflict_mode': conflict_mode if conflict_mode in CONFLICT_MODES else 'skip_existing',
        'records': details,
    }


def import_records(filename: str, data: bytes, *, scope_kind: str = '', scope_id: str = '', scope_mode: str = 'apply_active_scope_where_empty', conflict_mode: str = 'skip_existing') -> dict[str, Any]:
    records, warnings = parse_import_bytes(filename, data)
    imported = []
    skipped = []
    errors = []
    counts: dict[str, int] = {}
    for raw in records:
        record = deepcopy(raw)
        record, applied = apply_active_scope(record, scope_kind, scope_id, scope_mode)
        kind = _normalise_kind(record.get('kind'))
        record_id = str(record.get('id') or '')
        try:
            payload = {'kind': kind, 'record_id': record_id, 'payload': record, 'markdown': ''}
            # Conflict mode is handled gently here: save path itself safely replaces. Full skip check needs disk lookup and can be added later.
            if conflict_mode == 'copy_with_new_id':
                payload['record_id'] = _nowish_id(kind, record.get('display_label') or record.get('label') or kind)
                record['id'] = payload['record_id']
                payload['payload'] = record
            result = save_forge_record_payload(payload)
            saved = result.get('record', {})
            imported.append({
                'id': saved.get('record_id') or record.get('id'),
                'kind': saved.get('kind') or kind,
                'display_label': saved.get('title') or record.get('display_label') or record.get('label'),
                'applied_scope_fields': applied,
                'memory_sync': result.get('memory_sync', {}),
            })
            counts[kind] = counts.get(kind, 0) + 1
        except Exception as exc:
            errors.append({'id': record_id, 'kind': kind, 'error': str(exc)})
    return {
        'schema_id': 'neo.roleplay.forge_import.import.v1',
        'status': 'imported_with_errors' if errors else 'imported',
        'filename': filename,
        'imported_count': len(imported),
        'skipped_count': len(skipped),
        'error_count': len(errors),
        'counts_by_kind': counts,
        'warnings': warnings,
        'imported': imported,
        'skipped': skipped,
        'errors': errors,
        'next_step': 'Review Forge records, then use Compile > Build Scope Memory + Runtime.',
    }


def forge_import_contract() -> dict[str, Any]:
    return {
        'schema_id': 'neo.roleplay.forge_import.contract.v1',
        'status': 'ready',
        'supported_files': ['.json', '.md', '.zip containing .json/.md'],
        'scope_modes': list(SCOPE_MODES),
        'conflict_modes': list(CONFLICT_MODES),
        'save_path_rule': 'Imports call the same Forge save/upsert path as manual Builder Save.',
        'supported_kinds': list(CANONICAL_TEMPLATE_KINDS),
    }
