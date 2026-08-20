# IMG-SD1B — Scene Director Bundled Comfy Runtime Deployment

**Status:** Current hotfix — 2026-08-15  
**Surface:** Image → Generations / Reference → Scene Director  
**Backend:** ComfyUI / ComfyUI Portable

IMG-SD1B closes the runtime-package gap exposed after IMG-SD1A correctly preserved two Krea 2 regional LoRA routes but failed closed with:

```text
missing_runtime_node
NeoRegionalLoRADelta
```

## Root cause

The Neo repository contained the current Scene Director Comfy runtime under:

```text
neo_extensions/built_in/image.scene_director/comfy_node/
```

including:

```text
NeoSceneDirectorV054
NeoRegionalLoRADelta
```

However the standalone installable package at:

```text
neo_scene_director/
```

was stale and exported only `NeoSceneDirectorV054`. IMG-SD1 and IMG-SD1A changed the Neo-side compiler but did not update/deploy that external Comfy custom-node package. Therefore a correctly configured modern regional-LoRA request reached provider preflight, while Comfy `/object_info` could not advertise `NeoRegionalLoRADelta`.

## Bundled runtime package

The root `neo_scene_director` package is now synchronized with the extension-owned Comfy package and contains:

```text
neo_scene_director/
├─ __init__.py
├─ nodes.py
└─ regional_lora.py
```

Its `NODE_CLASS_MAPPINGS` exports both:

```text
NeoSceneDirectorV054
NeoRegionalLoRADelta
```

The root bundle must remain byte-parity with the extension-owned `comfy_node/nodes.py` and `comfy_node/regional_lora.py` runtime sources.

## Install the bundled Comfy runtime

Neo ships the installable Scene Director Comfy package as `neo_scene_director` in the Neo Studio root. Copy that folder to:

```text
<ComfyUI-root>/custom_nodes/neo_scene_director
```

If an older copy already exists, replace it with the bundled version from the Neo release you are using. Then fully restart ComfyUI and refresh/Test the selected ComfyUI profile in Neo.

## Capability transport

`ComfyProvider.discover_backend_capabilities()` now transports these Scene Director node classes in Neo's safe `/object_info` slice:

```text
NeoSceneDirectorV054
NeoRegionalLoRADelta
```

It also publishes:

```text
scene_director_runtime_diagnostics
```

so the UI can distinguish:

- classic V054 runtime available;
- modern regional-LoRA runtime available;
- runtime node missing;
- `/object_info` unavailable.

## Fail-closed policy

The existing IMG-SD1A guard remains intact. If regional LoRA routing is requested and `NeoRegionalLoRADelta` is still absent, Neo does **not** globally load the assigned LoRAs and does **not** queue a prompt-only substitute. It blocks before queue and now reports the bundled runtime sync remediation explicitly.

## Runtime target

For Krea 2 Turbo GGUF with two assigned regional LoRAs:

```text
2 regional prompts
2 regional LoRA rows
ConditioningSetMask(set_cond_area=default)
1 NeoRegionalLoRADelta
1 provider KSampler
0 global LoraLoader / LoraLoaderModelOnly for assigned rows
```

The custom node must appear in live Comfy `/object_info` before Neo can arm that graph.
