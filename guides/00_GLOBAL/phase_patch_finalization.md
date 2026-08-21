# Resumable phase validation and patch finalization

Neo development uses `scripts/dev/release/phase_finalizer.py` to make long validation/package runs resumable.

The utility does **not** change Neo runtime behavior. It protects developer handoff integrity when a validation or packaging session is interrupted.

## Why it exists

A phase may require several regression suites, architecture audits, cleanup, diff inspection, minimal ZIP creation, and checksum generation. If that finalization sequence is interrupted, re-running everything manually is slow and can make it easy to package a tree different from the one that passed validation.

The finalizer checkpoints each passed validation against a fingerprint of the source tree. Re-running the same command resumes safely: a validation is skipped only when both its command and source fingerprint are unchanged.

## Source fingerprint exclusions

The fingerprint/diff intentionally excludes generated/runtime material:

- `.git/`
- `.pytest_cache/`
- `__pycache__/`
- `*.pyc` / `*.pyo`
- `neo_data/`
- `dist/`

These paths cannot make a passed source validation appear stale and cannot enter a minimal phase patch.

## Packaging contract

After every supplied validation passes for the current fingerprint, the finalizer:

1. removes safe generated test artifacts;
2. compares the working source with an untouched baseline;
3. creates a deterministic ZIP containing **changed/new source files only**;
4. writes SHA-256, changed/new/deleted lists, validation checkpoints, and the final source fingerprint to `<patch>.zip.summary.json`.

Source deletions are never hidden inside the ZIP. They are reported separately because extracting a ZIP cannot remove pre-existing files.

## Resume rule

Run the exact finalizer command again after an interruption. Previously passed validation steps are reused only while the source fingerprint and command match. Any source edit automatically invalidates the affected checkpoint and forces validation to run again before packaging.

See `scripts/dev/release/README.md` for an invocation example.
