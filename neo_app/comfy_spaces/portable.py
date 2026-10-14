"""NCS-15: bounded, declarative .neospace transport for immutable published versions.

Transport digest validates bytes; it is not publisher authentication. Imported packages
are revalidated by the standard published Space loader and never auto-executed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from neo_app.comfy_spaces.execution_runtime import _load_published_space_package
from neo_app.comfy_spaces.paths import comfy_space_version_root
from neo_app.core.pydantic_compat import model_to_dict

FORMAT = 'neo.comfy_space.portable.v1'
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024
MAX_FILES = 32
SAFE_IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
CORE = {'space_manifest.json', 'workflow.json', 'space_ui.json', 'publication.json'}
OPTIONAL = {'graph_anchors.json', 'stages.json', 'dynamic_controls.json', 'migration.json'}
_TOKEN = re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')


class SpacePortableError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (name == path.as_posix() and not path.is_absolute() and
            len(path.parts) <= 4 and all(p not in {'.', '..', ''} for p in path.parts) and
            (name in CORE | OPTIONAL or
             (path.parts[0] == 'assets' and path.suffix.lower() in SAFE_IMAGE)))


def _inspect(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(data) > MAX_ARCHIVE:
        raise SpacePortableError('Archive exceeds 32 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 1 or len({e.filename for e in entries}) != len(entries):
                raise SpacePortableError('Too many archive entries or duplicate filenames')
            if sum(e.file_size for e in entries) > MAX_UNPACKED:
                raise SpacePortableError('Uncompressed archive exceeds 64 MiB')
            files = {}
            for entry in entries:
                name = entry.filename
                if entry.is_dir() or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                    raise SpacePortableError('Directory or symlink entries are forbidden')
                if name != 'neospace.json' and not _safe_path(name):
                    raise SpacePortableError('Forbidden archive path or file type')
                files[name] = archive.read(entry)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
        raise SpacePortableError('Invalid .neospace archive') from exc
    try:
        header = json.loads(files.pop('neospace.json'))
        if not isinstance(header, dict) or header.get('format') != FORMAT:
            raise ValueError('unsupported header')
        declared = header['files']
        if not isinstance(declared, dict) or set(declared) != set(files) or not CORE <= set(files):
            raise ValueError('file inventory mismatch')
        if not _TOKEN.fullmatch(header['space_id']):
            raise ValueError('invalid space id')
        if not isinstance(header['version'], str):
            raise ValueError('invalid version')
        for name, blob in files.items():
            record = declared[name]
            if not isinstance(record, dict) or record.get('sha256') != _digest(blob) or record.get('bytes') != len(blob):
                raise ValueError('file hash mismatch')
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SpacePortableError('Portable manifest is invalid or tampered with') from exc
    return header, files


def _package_files(space_id: str, version: str, *, root_dir=None) -> dict[str, bytes]:
    _load_published_space_package(space_id, version, root_dir=root_dir)
    root = comfy_space_version_root(space_id, version, root_dir)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise SpacePortableError('Symlink in published package')
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if not _safe_path(name):
            raise SpacePortableError(f'Unsupported published file: {name}')
        files[name] = path.read_bytes()
    if not CORE <= set(files):
        raise SpacePortableError('Published package has missing required files')
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_UNPACKED:
        raise SpacePortableError('Published package exceeds portable limits')
    return files


def export_space(space_id: str, version: str, *, root_dir=None) -> bytes:
    files = _package_files(space_id, version, root_dir=root_dir)
    header = {'format': FORMAT, 'space_id': space_id, 'version': version,
              'files': {name: {'sha256': _digest(blob), 'bytes': len(blob)} for name, blob in sorted(files.items())},
              'models_included': False, 'code_included': False}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('neospace.json', json.dumps(header, sort_keys=True))
        for name, blob in sorted(files.items()):
            archive.writestr(name, blob)
    result = stream.getvalue()
    if len(result) > MAX_ARCHIVE:
        raise SpacePortableError('Archive exceeds 32 MiB')
    return result


def import_space(data: bytes, *, root_dir=None, dry_run: bool = False) -> dict[str, Any]:
    header, files = _inspect(data)
    space_id, version = header['space_id'], header['version']
    try:
        target = comfy_space_version_root(space_id, version, root_dir)
    except ValueError as exc:
        raise SpacePortableError('Invalid target Space version') from exc
    if target.exists():
        raise SpacePortableError('This exact Space version already exists; existing publications cannot be overwritten')
    # Stage on the same filesystem as the library so publishing is an atomic directory rename.
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.neospace-', dir=parent) as temporary:
        staged = Path(temporary) / 'package'
        staged.mkdir()
        for name, blob in files.items():
            dest = staged.joinpath(*PurePosixPath(name).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        # Existing loader checks the publication receipt, manifest, UI, optional signed
        # sidecars, trust and workflow integrity. No runtime execution is invoked.
        # Loader uses standard path resolver, so validate with a temporary isolated root.
        with tempfile.TemporaryDirectory(prefix='neo-portable-check-') as isolated:
            check_path = comfy_space_version_root(space_id, version, isolated)
            check_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged, check_path)
            try:
                _load_published_space_package(space_id, version, root_dir=isolated)
            except Exception as exc:
                raise SpacePortableError(f'Imported publication failed Neo validation: {exc}') from exc
        result = {'ok': True, 'space_id': space_id, 'version': version,
                  'file_count': len(files), 'dry_run': dry_run,
                  'dependency_status': 'not_checked', 'requires_runtime_preflight': True,
                  'provenance': 'integrity_checked_not_author_authenticated'}
        if not dry_run:
            try:
                staged.rename(target)
            except OSError as exc:
                raise SpacePortableError('Unable to install imported Space') from exc
        return result
