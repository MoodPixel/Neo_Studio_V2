# Neo Voice Engine manifests

VO-E3 established `neo.voice_engine.manifest.v1`; VO-E4 consumes its hardware/lifecycle metadata; **VO-E5 ships the first active reviewed worker manifest**.

## Active manifest

```text
chatterbox.json
```

It registers:

- engine: `chatterbox`;
- managed loopback worker: `http://127.0.0.1:8791`;
- isolated external runtime: `voice_runtime://envs/chatterbox`;
- models: `chatterbox_turbo`, `chatterbox_multilingual`;
- tasks: TTS + authorized reference cloning;
- CPU/CUDA admission metadata;
- lifecycle/idle-unload policy;
- source/license metadata without claiming unverified commercial rights.

The gateway does not install Chatterbox packages. `setup_chatterbox_backend.bat` remains responsible for the worker environment under the external Voice runtime root. If required install probes are absent, the model remains declared but `not_installed` and execution fails before worker dispatch.

Additional manifest directories can be supplied with `NEO_VOICE_ENGINE_MANIFEST_DIRS` using the platform path separator (`;` on Windows, `:` on Linux/macOS).

Public manifests must not contain user-specific absolute paths, tokens, cookies, API keys, or other secrets.

See `../schema/manifest_v1.schema.json`, `guides/05_VOICE/neo_voice_engine_registry.md`, and `guides/05_VOICE/neo_voice_engine_scheduler.md`.

## VO-E5A environment path scopes

Worker `environment.root` and `environment.python` stay relative in checked-in manifests. `environment.scope` controls the trusted base:

- `project` — resolve under the Neo Studio source root (legacy/default for manifest v1);
- `voice_runtime` — resolve under the configured external Voice runtime root.

Use `voice_runtime` for managed model-family venvs. Absolute paths and `..` traversal remain invalid in public manifests.
