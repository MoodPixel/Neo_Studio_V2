# Neo Studio V2

**Neo Studio V2** is a local-first AI creative workspace that brings Image, Video, Voice, Prompt & Captioning, Roleplay, Assistant, project context, and backend management into one structured interface.

Neo Studio does **not** bundle third-party AI models or engines. It connects to backends you install separately and keeps the selected backend profile authoritative for each workflow.

> **Current highlight:** the Voice workspace is now functional through the unified **Neo Voice Engine**, and the Image workspace supports multiple local model families through **ComfyUI / ComfyUI Portable**, **Forge Neo**, plus an optional **xAI Grok Imagine** cloud profile.

---

## Table of Contents

- [✨ What Neo Studio Does](#-what-neo-studio-does)
- [🖼️ Workspace Screenshots](#️-workspace-screenshots)
- [🎨 Image Backends](#-image-backends)
- [🧠 Supported Image Model Families](#-supported-image-model-families)
- [🎙️ Voice Workspace](#️-voice-workspace)
- [🧭 Main Tabs](#-main-tabs)
- [⚙️ Installation](#️-installation)
- [🔌 Backend Setup](#-backend-setup)
- [🧩 ComfyUI Custom Nodes](#-comfyui-custom-nodes)
- [🧠 Memory / Embedding / Reranker Setup](#-memory--embedding--reranker-setup)
- [📁 Runtime Data and Project Files](#-runtime-data-and-project-files)
- [🧩 Backend Notes & Troubleshooting](#-backend-notes--troubleshooting)
- [📚 Documentation and Guides](#-documentation-and-guides)
- [⚠️ Known Limitations](#️-known-limitations)
- [📜 License](#-license)
- [🚀 Future Direction](#-future-direction)
- [☕ Support the Project](#-support-the-project)

---

## ✨ What Neo Studio Does

### 🎨 Image

- Generate, edit, inpaint, outpaint, refine, upscale, inspect, and replay Image jobs from one workspace.
- Route requests through the selected **ComfyUI / ComfyUI Portable**, **Forge Neo**, or **Grok Imagine** backend profile without silently switching providers.
- Supports checkpoint, component/safetensors, GGUF, bundled/AIO, and provider-owned workflows depending on the selected model family.
- Built-in workflow tools include **ControlNet**, **IP Adapter / FaceID**, **LoRA Stack**, **Style Stack**, **Wildcards**, **ADetailer**, **LayerDiffuse**, **High-Res Lab**, **Image Upscale**, **LanPaint**, **Forge Couple**, and **Scene Director** where the selected route supports them.
- Output Inspector records source lineage, effective settings, provider information, elapsed timing, replay metadata, and reusable result actions.

### 🎬 Video

- ComfyUI-backed video generation and finishing with route-aware source workflows.
- Current work includes LTX and WAN-oriented generation routes, Img2Vid, first/last-frame, multiscene, extend, Vid2Vid, depth/motion control, interpolation, upscale, and repair when the required local models/nodes are installed.

### 🎙️ Voice

- Functional local TTS workspace through **Neo Voice Engine**.
- Single-voice TTS, Reference / Clone, reusable Voice Profile Assets, Dialogue / Multi-speaker, Batch, Results, and non-destructive Finish tools.
- The gateway keeps model runtimes isolated so different Voice model families can use separate environments without polluting the Neo Studio Python environment.

### ✍️ Prompt & Captioning

- Prompt building, captioning, prompt libraries, reusable presets, batch captioning, and edit/video prompt helpers.

### 🎭 Roleplay

- Character/world Forge tools, scene runtime, stories, memory/retrieval, continuity support, and structured writing workflows.

### 🤖 Assistant

- Scope-aware local Assistant with project context, attachments, guide-aware knowledge, memory capture, and handoff between Neo workspaces.

### ⚙️ Admin

- Backend profiles, provider connections, launch/configuration tools, model guidance, extensions, custom nodes, runtime logs, and system settings.

---

## 🖼️ Workspace Screenshots

### Image

![Neo Studio Image workspace][shot-image-01]

<details>
<summary>More Image screenshots</summary>

![Image workspace details][shot-image-02]
![Image workflow controls][shot-image-03]

</details>

### Video

![Neo Studio Video workspace][shot-video-01]

<details>
<summary>More Video screenshots</summary>

![Video workflow panels][shot-video-02]
![Video generation controls][shot-video-03]

</details>

### Voice

![Neo Studio Voice workspace][shot-voice-01]

<details>
<summary>More Voice screenshots</summary>

![Voice generation and controls][shot-voice-02]
![Voice results and workflow][shot-voice-03]
![Voice assets and finishing][shot-voice-04]

</details>

### Prompt & Captioning

![Neo Studio Prompt and Captioning][shot-prompt-01]

<details>
<summary>More Prompt & Captioning screenshots</summary>

![Prompt Studio][shot-prompt-02]
![Prompt Library][shot-prompt-03]
![Caption Studio][shot-prompt-04]
![Batch Captioning][shot-prompt-05]
![Caption Library][shot-prompt-06]

</details>

### Roleplay

![Neo Studio Roleplay][shot-roleplay-01]

<details>
<summary>More Roleplay screenshots</summary>

![Roleplay workspace][shot-roleplay-02]
![Roleplay Forge controls][shot-roleplay-03]
![Roleplay editor][shot-roleplay-04]

</details>

### Assistant

![Neo Studio Assistant][shot-assistant-01]

<details>
<summary>More Assistant screenshots</summary>

![Assistant project context][shot-assistant-02]
![Assistant workflow][shot-assistant-03]

</details>

### Admin

![Neo Studio Admin][shot-admin-01]

> README screenshots are hosted through GitHub user attachments rather than committed to the repository, so normal clones do not download the screenshot image files.

---

## 🎨 Image Backends

Neo uses the **selected Image backend profile as the execution authority**. A workflow never silently jumps to another backend just because another provider happens to be connected.

| Backend | Role in Neo | Typical workflow ownership |
|---|---|---|
| **ComfyUI / ComfyUI Portable** | Primary advanced local graph backend | Component/safetensors models, GGUF transformers, checkpoint workflows, Qwen/Krea/ZImage/Flux-family compilers, ControlNet, IP Adapter, LanPaint, custom-node workflows, live preview, local finishing. |
| **Forge Neo** | Local API-driven Image backend | Forge checkpoint/model routes, native Img2Img/Inpaint controls, Forge High-Res Fix, LoRA/Embeddings, provider-discovered ControlNet/IP Adapter features, Forge Extras upscale, ADetailer and Forge Couple mappings where available. |
| **xAI Grok Imagine** | Optional cloud Image backend | API image generation/editing through an explicit Grok profile and API key. It is never used as an automatic fallback for local jobs. |

### How routing works

```text
Image UI
  ↓
Selected backend profile
  ↓
Model family + loader + workflow mode
  ↓
Neo route/compiler
  ↓
Comfy graph OR Forge API OR cloud API
  ↓
Neo-owned output + metadata + replay lineage
```

The same model family can have different support depending on the selected loader/backend. Neo therefore checks the active route instead of assuming that a model name alone guarantees a workflow.

---

## 🧠 Supported Image Model Families

The current Image family registry includes the following user-facing families. Exact workflow availability is still route-, loader-, model-, and node-dependent.

| Family | Current Neo coverage |
|---|---|
| **SDXL** | Checkpoint Generate, Img2Img, Inpaint, Outpaint and finishing routes. |
| **SD 1.5** | Checkpoint Generate, Img2Img, Inpaint, Outpaint and finishing routes. |
| **Flux 1** | Component/safetensors and GGUF routes; includes FLUX.1 Dev-compatible variants such as FLUX.1 Krea. Generate, Img2Img, Inpaint and Outpaint where the selected route is available. |
| **Flux 2 Klein** | Separate family with component/safetensors and GGUF generation/edit workflows, plus source-driven masked workflows. |
| **Krea 2 RAW** | Native Krea 2 architecture, component/safetensors and experimental GGUF routes. Generate plus source-driven edit workflows. |
| **Krea 2 Turbo** | Distilled Krea 2 few-step family with its own sampling defaults and the same family-specific edit architecture. |
| **Qwen Rapid AIO** | Bundled/AIO and GGUF routes for Generate, Edit/Img2Img, Inpaint and Outpaint. |
| **Qwen Image Edit** | Standard Qwen Image Edit routes for source-driven editing and mask/canvas workflows. |
| **Qwen Image Edit 2509** | Multi-reference Qwen edit route with up to three references for Img2Img/Edit. |
| **Qwen Image Edit 2511** | Multi-reference edit route with improved consistency/geometry; up to three references for Img2Img/Edit. |
| **ZImage** | Component/safetensors generation and image-conditioned workflows; GGUF coverage depends on route. |
| **ZImage Turbo** | Separate low-step family with component/safetensors and GGUF workflows. |
| **Anima Base v1** | Qwen3-conditioned generation, Img2Img and LanPaint workflows with standard/GGUF model loading. |
| **Ideogram 4** | Open-weight txt2img plus dedicated LanPaint inpaint/outpaint path when the required custom workflow stack is installed. |
| **Stable Diffusion 3.5** | Dedicated LanPaint inpaint/outpaint route; not presented as a generic SDXL-style family. |
| **Flux 2 Dev** | Dedicated LanPaint path with its own encoder/VAE architecture. |
| **HiDream-I1** | HiDream I1 family support with dedicated multi-encoder/LanPaint routes; unrelated HiDream edit architectures remain separate. |
| **Wan / Hunyuan Image** | Registered for provider/capability awareness but held or provider-gated where Neo does not yet have a validated Image compiler. |

### Character and reference continuity

Neo currently offers several continuity approaches depending on the model:

- **Krea 2 Identity Edit v1.2** — dedicated identity-edit LoRA + Krea2 appearance/grounding path.
- **Qwen Image Edit 2509 / 2511** — 1–3 visual reference images for multi-reference edits.
- **IP Adapter / FaceID** — available on compatible routes/backends.
- **LoRA Stack** — route-aware global LoRAs with open numeric strength entry for models that require unusually strong values.
- **Scene Director** — structured regional identity/composition control where the selected workflow supports it.

---

## 🎙️ Voice Workspace

Voice is no longer a placeholder surface.

### Current architecture

```text
Neo Studio Voice UI
  ↓
voice.neo_engine profile
  ↓
Neo Voice Engine gateway
  ↓
manifest / scheduler / worker supervisor
  ↓
isolated Voice worker environment
  ↓
model runtime
```

The default runtime root is outside the source tree:

```text
<Neo parent>/Neo_Runtime/voice/
  envs/
  models/
  cache/
  temp/
  logs/
  state/
  outputs/
```

This prevents the Neo Studio repository from accumulating one Python environment per Voice model family.

### Currently validated Voice backend

| Backend | State | Notes |
|---|---|---|
| **Neo Voice Engine + Chatterbox** | ✅ Functional / validated | Unified gateway route, local TTS, reference cloning, isolated CUDA environment, job/result handling. |
| **Chatterbox Legacy Direct** | Diagnostic fallback | Kept for direct backend troubleshooting; not the normal daily route. |
| **Kokoro Preview** | Preview adapter | Lightweight profile exists but is not the primary validated gateway worker family. |
| **Fish Speech HQ** | Advanced/preview adapter | Profile and capability layer exist; separate runtime validation/integration is still evolving. |
| **Zonos / Custom TTS** | Experimental / disabled by default | Reserved for later worker-family onboarding. |

### Voice features available in the UI

- Single Voice TTS
- Provider-specific controls
- Reference audio upload + authorization check
- Voice cloning when the selected backend supports it
- Saved Voice Profile Assets
- Dialogue / Multi-speaker generation
- TXT / Markdown / CSV / JSON / SRT Batch workflows
- Shared Voice Results registry
- Replay to draft
- Download/open-output actions
- Provider-independent Finish tools for Neo-owned audio: normalize, trim, cleanup, loudness, conversion, split, and merge where FFmpeg/runtime capabilities are available

### First-time Chatterbox setup

```bat
setup_chatterbox_backend.bat
setup_neo_voice_engine.bat
```

Normal daily Voice-engine launch:

```bat
run_neo_voice_engine.bat
```

Then start Neo Studio normally with `run_neo_studio.bat`.

---

## 🧭 Main Tabs

| Tab | Purpose | Current state |
|---|---|---|
| **Image** | Generate, edit, refine, inspect, replay, reference, and finish images. | Active |
| **Video** | Generate and finish local video workflows. | Active / expanding |
| **Voice** | TTS, clone, Dialogue, Batch, reusable Voice assets, Results and Finish. | Active |
| **Prompt & Captioning** | Prompt generation, libraries, captioning and batch tools. | Active |
| **Roleplay** | Character/world creation, scenes, stories and memory-aware writing. | Active |
| **Assistant** | Scope-aware local Assistant and project context. | Active |
| **Admin** | Backends, models, extensions, custom nodes, runtime and settings. | Active |
| **Music** | Music/audio generation workspace. | Planned |
| **Board** | Visual planning/organization workspace. | Planned |

---

## ⚙️ Installation

### Requirements

- Windows 10/11.
- Python 3.10+.
- Git.
- Recommended local backends:
  - ComfyUI Portable.
  - Forge Neo.
  - KoboldCPP.

### Setup

1. Clone or download the Neo Studio repository.
2. Open the Neo Studio folder.
3. Run:

```bat
setup_neo_studio_venv.bat
```

4. Start Neo Studio:

```bat
run_neo_studio.bat
```

5. Open the local URL shown in the console.

> Neo Studio does not download AI models automatically. Install and place models in your backend folders manually.

---

## 🔌 Backend Setup

Neo Studio does **not** include AI models, API keys, or third-party backend engines. Install your local backends and models separately, then connect them through Neo's pre-created backend profiles.

In **Neo Studio V2**, backend setup is handled from:

```text
Admin → Backends
```

Neo already ships with seeded backend profiles for the main surfaces. In most cases, you do **not** need to create new profiles. Use the existing profiles, add only the missing local paths/API keys, then test the connection.

### Pre-created backend profiles

| Surface | Pre-created profiles | Used For | What you usually need to add |
|---|---|---|---|
| **Image** | ComfyUI Local, ComfyUI Portable, Forge Neo, Grok Imagine | Image generation, image edit, Comfy workflows, Forge Neo workflows, cloud image generation/edit | Comfy/Forge path or launcher if local, or xAI API key for Grok |
| **Video** | Video · ComfyUI Local, Video · ComfyUI Portable | Video generation, video finishing, source-frame workflows | Comfy path/launcher if using local video routes |
| **Text** | KoboldCpp Local | Assistant, Roleplay, Prompting, Captioning, local chat workflows | KoboldCPP launcher/path and model setup |
| **Voice** | Neo Voice Engine, Chatterbox Legacy Direct, Kokoro Preview, Fish Speech HQ, Zonos, Custom TTS Adapter | Unified Voice gateway; Chatterbox is the currently validated local worker family | Voice runtimes default to the external sibling `Neo_Runtime\voice` tree |
| **Music / Audio** | ACE-Step, Stable Audio Open, YuE Song HQ, Custom Audio Adapter | Planned or early audio/music workflow profiles | Only needed if you are testing audio/music workflows |

Supported backend tools:

| Backend | Used For | Link |
|---|---|---|
| **Forge / Forge Neo** | Local image generation through Forge Neo, built-in Forge features, ADetailer, Forge Couple, provider-aware Preview/Finish actions | https://github.com/Haoming02/sd-webui-forge-classic |
| **ComfyUI / ComfyUI Portable** | Local image generation, video generation, Comfy workflows, custom nodes, live preview, metadata/replay workflows | https://github.com/Comfy-Org/ComfyUI |
| **Forge Neo** | Local image generation and image finishing through Forge Neo APIs, built-in Forge features, provider-aware Preview/Output Inspector actions, and supported Forge extensions | https://github.com/Haoming02/sd-webui-forge-classic/tree/neo |
| **KoboldCPP** | Local text backend for Assistant, Roleplay, Prompting, Captioning, and chat workflows | https://github.com/LostRuins/koboldcpp/releases |
| **xAI Grok Imagine API** | Cloud image generation and image edit workflows through the seeded Image backend profile | https://docs.x.ai/ |

Suggested local backend folder style:

```text
<backend-root>/ComfyUI_windows_portable/
<backend-root>/Forge_Neo/
<backend-root>/KoboldCPP/
```

Cloud API profiles do not need a local backend folder, but they do need a valid API key.

---

### Recommended setup flow

1. Open **Neo Studio**.
2. Go to **Admin → Backends**.
3. Choose the surface tab you need:
   - **Image** for ComfyUI, Forge Neo, or Grok Imagine profiles.
   - **Video** for ComfyUI video profiles.
   - **Text** for KoboldCPP / local LLM profiles.
4. Select the existing profile that matches your backend.
5. Only edit what is missing or different on your machine.
6. Click **Save Profile**.
7. Click **Test Connection**.
8. If the profile works, click **Set Default** for that surface if needed.

> Do not create a new backend profile unless you need a custom port, custom backend folder, different provider, or a separate experimental setup.

---

### Local backend profile setup

For ComfyUI, Forge Neo, or KoboldCPP, the seeded profiles already include the usual default URLs. Check these first before changing anything:

```text
ComfyUI:    http://127.0.0.1:8188
Forge Neo:  http://127.0.0.1:7860
KoboldCPP:  http://127.0.0.1:5001
```

> Forge and Neo Studio must not bind the same local port. Keep Forge Neo on its own backend port, such as `7860`, and run Neo Studio on the separate URL shown by Neo's launcher.

For launcher-based profiles, update only the machine-specific launcher fields:

- **Portable Path**
- **Launch Command**

Use the same launcher file or command you normally use to start ComfyUI, Forge Neo, or KoboldCPP manually.

If your backend runs on the default URL and you start it manually outside Neo, you may only need to click **Test Connection**.

---

### Forge Neo setup for Image generation

Neo Studio V2 includes a seeded **Forge Neo** backend profile under the **Image** backend tab. Use this profile for local Forge Neo image generation, image editing, provider-aware reference workflows, and supported finishing actions.

### Current Forge Image support

Neo currently supports Forge Neo image routes through the selected Forge profile, including checkpoint-based SD 1.5/SDXL workflows and provider-mapped routes such as Flux.2 Klein component or GGUF where the selected Forge installation publishes the required models, modules, and API capabilities. See `guides/01_IMAGE/forge_neo_complete_support.md` for the current route matrix and limitations.

Offline Forge validation does not replace physical GPU/model testing. Model loading, VRAM behavior, visual output quality, and third-party extension compatibility must still be verified on the target Forge installation.

Current Forge Neo extension coverage:

| Extension type | Coverage |
|---|---|
| **Forge Neo built-in extensions/features** | Covered through Neo's Forge provider integration when the selected Forge installation reports the required API capability, model, module, script, or upscaler. |
| **External extension: ADetailer** | Covered for provider-owned Forge Img2Img detail-repair passes. |
| **External extension: Forge Couple** | Covered through the Forge Neo external-extension mapping. |

> Third-party Forge extensions that are not listed above are not automatically assumed to be compatible. They may require a dedicated Neo capability and payload mapping.

For the full provider-aware integration, install or update the bundled **Neo Forge Bridge** as documented in:

```text
neo_integrations/forge_neo_bridge/README.md
```

Restart Forge Neo after installing or updating the Bridge, then refresh/test the selected Forge Neo profile in Neo Studio.

#### Recommended Forge Neo startup BAT

Forge Neo should run with API access enabled. The minimal recommended launcher is:

```bat
@echo off
call webui.bat --api --uv
```

Optional flags can be added when your installation needs them:

```bat
@echo off
call webui.bat --api --uv --cuda-malloc --model-ref "<SHARED_MODEL_ROOT>" --forge-ref-comfy-yaml "<COMFYUI_ROOT>\extra_model_paths.yaml"
```

| Flag | Requirement | Purpose |
|---|---|---|
| `--api` | Required for Neo | Enables the Forge API used by the Neo Studio backend profile. |
| `--uv` | Recommended launch option | Starts Forge Neo using its UV-managed environment. |
| `--cuda-malloc` | Optional | Use only when you want or need Forge's CUDA memory-allocation mode. |
| `--model-ref "<SHARED_MODEL_ROOT>"` | Optional | Reuses models from a separate shared model root. Omit it when Forge uses only its own model folders. |
| `--forge-ref-comfy-yaml "<COMFYUI_ROOT>\extra_model_paths.yaml"` | Optional | Lets Forge reuse model locations declared in a ComfyUI `extra_model_paths.yaml` file. Omit it when you do not share ComfyUI model paths. |

Do not copy another user's drive paths. Replace placeholders only with folders that exist on your machine.

#### Connect Forge Neo in Neo Studio

1. Install Forge Neo separately from Neo Studio.
2. Start Forge Neo using the API-enabled BAT command above.
3. Open **Neo Studio**.
4. Go to:

```text
Admin → Backends → Image
```

5. Select the existing **Forge Neo** profile.
6. Set the Forge Neo folder or launcher only if it differs from the seeded profile.
7. Confirm the API URL, normally:

```text
http://127.0.0.1:7860
```

8. Click **Save Profile**.
9. Click **Test Connection**.
10. Refresh provider capabilities after installing/updating Forge extensions or the Neo Forge Bridge.
11. Click **Set Default** only if you want the Image workspace to use Forge Neo by default.

---

### xAI Grok Imagine setup for Image generation

Neo Studio V2 includes a seeded **Grok Imagine** backend profile under the **Image** backend tab. You usually only need to add your xAI API key and test the connection.

This profile is currently wired into Neo as an **Image workspace backend** for:

- text-to-image;
- image edit;
- multi-image edit where supported by the selected model/profile.

It is **not** documented here as a Neo Text or Video backend.

1. Get an xAI API key from your xAI account.
2. Open **Neo Studio**.
3. Go to:

```text
Admin → Backends → Image
```

4. Select the existing **Grok Imagine** profile.
5. Confirm the API base URL is already set to:

```text
https://api.x.ai/v1
```

6. Add your API key using one of these options.

#### Option A — Environment variable

Set this environment variable before launching Neo:

```text
XAI_API_KEY
```

Then keep the profile auth/key mode set to environment variable mode.

#### Option B — Manual local key

Paste the API key into the profile's manual local API key field.

Manual local secrets should stay under Neo runtime data, not inside the source repo:

```text
neo_data/settings/secrets/
```

7. Confirm the health check path is:

```text
/models
```

8. Confirm or select the image model, such as:

```text
grok-imagine-image
grok-imagine-image-quality
```

9. Click **Save Profile**.
10. Click **Test Connection**.
11. Click **Set Default** only if you want the Image workspace to use Grok Imagine by default.

---

### When to edit or create backend profiles

Only change the seeded profiles if:

- your backend uses a different port or URL;
- your backend folder is in a different location;
- you use a custom launcher command;
- you want separate experimental profiles;
- the connection test fails and the guide tells you what to check.

For detailed troubleshooting, refer to:

```text
guides/00_GLOBAL/backend_profiles.md
guides/01_IMAGE/forge_neo_complete_support.md
guides/07_ADMIN/forge_neo_admin.md
guides/01_IMAGE/xai_grok_imagine.md
```

### Backend profile actions

| Action | Meaning |
|---|---|
| **Save Profile** | Saves profile settings and connection details. |
| **Test Connection** | Checks whether Neo can reach the local backend or cloud API. |
| **Set Default** | Makes the profile the default for that surface. |
| **Clear saved key** | Removes a manually saved API key from local Neo runtime data. |

### Important backend notes

- Neo Studio does not ship with AI models.
- Neo Studio does not ship with ComfyUI, Forge Neo, KoboldCPP, or xAI credentials.
- Neo already includes seeded backend profile templates, so users usually only need to add local paths or API keys.
- Local backend folders should stay outside the Neo repo.
- User/runtime data should stay under `neo_data/`.
- Cloud API keys should never be committed to the repo.
- If a task says the backend is disconnected, go to **Admin → Backends**, test the correct profile, then retry the task.
- For local manual-connect profiles, a profile may need to be tested/connected again after restarting Neo.

---

## 🧩 ComfyUI Custom Nodes

Some Image and Video workflows require ComfyUI custom nodes.

You can install nodes through:

```text
Admin → Extensions → Node Manager
```

Or install them manually into:

```text
ComfyUI/custom_nodes/
```

### Recommended ComfyUI custom nodes

| Node | Purpose | Link |
|---|---|---|
| `comfyui-art-venture` | Image, JSON, model, text, URL-loading, and inpaint helper utilities used by compatible Comfy workflows | https://github.com/sipherxyz/comfyui-art-venture.git |
| `comfyui-essentials` | Common utility nodes used by many workflows | https://github.com/comfyorg/comfyui-essentials.git |
| `ComfyUI-GGUF` (city96) | Standard GGUF model support for image/video model routes | https://github.com/city96/ComfyUI-GGUF.git |
| `ComfyUI-GGUF` (Krea2-compatible fork) | Alternate GGUF loader used when a Krea 2 GGUF route requires the compatible fork. Install only one ComfyUI-GGUF implementation at a time | https://github.com/molbal/ComfyUI-GGUF.git |
| `gguf` | Additional GGUF utility support | https://github.com/calcuis/gguf.git |
| `ComfyUI-Impact-Pack` | Detection, detailing, masks, segmentation, and utility workflows | https://github.com/ltdrdata/ComfyUI-Impact-Pack.git |
| `ComfyUI-Impact-Subpack` | Support package for Impact Pack | https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git |
| `ComfyUI-Inspire-Pack` | Workflow helpers and utility nodes | https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git |
| `ComfyUI-KJNodes` | Advanced utility, video, and image helper nodes | https://github.com/kijai/ComfyUI-KJNodes.git |
| `comfyui_controlnet_aux` | Control-map preprocessors for depth, pose, canny, lineart, normal maps, edges, and related ControlNet workflows | https://github.com/Fannovel16/comfyui_controlnet_aux.git |
| `ComfyUI_IPAdapter_plus` | IPAdapter reference/identity workflows | https://github.com/cubiq/ComfyUI_IPAdapter_plus.git |
| `ComfyUI_UltimateSDUpscale` | Tiled upscale workflow support | https://github.com/ssitu/ComfyUI_UltimateSDUpscale.git |
| `sd-dynamic-thresholding` | CFG Fix / Dynamic Thresholding support | https://github.com/mcmonkeyprojects/sd-dynamic-thresholding |
| `LanPaint` | Universal inpaint/outpaint sampler used by Neo LanPaint routes across supported image families | https://github.com/scraed/LanPaint.git |
| `ComfyUI-InpaintEasy` | Smart inpaint crop, image/mask resize, and merge helpers used by compatible masked-edit/LanPaint routes | https://github.com/CY-CHENYUE/ComfyUI-InpaintEasy.git |
| `ComfyUI-Inpaint-CropAndStitch` | Native masked-area crop/stitch engine used by Neo Native Inpaint Crop & Stitch workflows | https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch.git |
| `comfyui-krea2edit` | Krea 2 Identity Edit model patch + Qwen3-VL grounded instruction nodes | https://github.com/lbouaraba/comfyui-krea2edit.git |
| `comfyui-krea2-controlnet` | Krea 2 native Control LoRA loader, control-image VAE encode, and model-apply nodes used by the Krea Depth Control route | https://github.com/facok/comfyui-krea2-controlnet.git |
| `comfyui-krea2-controlnetPlus` | Krea 2 Control Plus nodes used by the Krea Composition / Silhouette route | https://github.com/tori29umai0123/comfyui-krea2-controlnetPlus.git |
| `ComfyUI-Krea2-Ostris-Edit` | Krea 2 reference-image edit conditioning and model patch used by the Krea OpenPose / Ostris route | https://github.com/ostris/ComfyUI-Krea2-Ostris-Edit.git |
| `ComfyUI-NK2E` | NK2E in-context model/reference nodes used by the Krea Canny route | https://github.com/Nynxz/ComfyUI-NK2E.git |
| `ComfyUI-VAE-Utils` | Extended VAE loading/decoding and latent-upscale utilities, including Wan/Qwen-compatible upscale VAE workflows | https://github.com/spacepxl/ComfyUI-VAE-Utils.git |
| `ComfyUI-SUPIR` | Optional legacy SUPIR wrapper for restoration/upscale workflows; current ComfyUI also provides SUPIR support in core | https://github.com/kijai/ComfyUI-SUPIR.git |
| `facerestore_cf` | CodeFormer / FaceRestore nodes used by Image Upscale face restore assist | https://github.com/mav-rik/facerestore_cf.git |
| `ComfyUI-RMBG` | Background removal, matting, segmentation, masks, object/fashion regions, and image-preparation utilities | https://github.com/1038lab/ComfyUI-RMBG.git |
| `ComfyUI_BiRefNet_ll` | BiRefNet image background-removal workflows | https://github.com/lldacing/ComfyUI_BiRefNet_ll.git |
| `ComfyUI-WanVideoWrapper` | WAN video workflow support and video-specific node paths | https://github.com/kijai/ComfyUI-WanVideoWrapper.git |
| `ComfyUI-TeaCache` | Optional video performance / caching support for compatible WAN/LTX routes | https://github.com/welltop-cn/ComfyUI-TeaCache.git |
| `ComfyUI-LTXVideo` | LTX video generation nodes and LTX-specific workflow support | https://github.com/Lightricks/ComfyUI-LTXVideo.git |
| `ComfyUI-Frame-Interpolation` | Finish-lane interpolation / FPS smoothing | https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git |
| `ComfyUI-VideoHelperSuite` | Video load/combine/save helpers used by many Comfy video workflows | https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git |
| `ComfyUI-SeedVR2_VideoUpscaler` | Video/Image Upscale workflows | https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git |
| `RES4LYF` | RES4LYF sampler support | https://github.com/ClownsharkBatwing/RES4LYF |
| `rgthree-comfy` | Workflow utility nodes | https://github.com/rgthree/rgthree-comfy.git |
| `ComfyUI-llama-cpp_vlm` | Local GGUF LLM/VLM execution used by the optional ComfyUI Prompt & Captioning backend | https://github.com/lihaoyun6/ComfyUI-llama-cpp_vlm.git |
| `neo_prompt_captioning` | Neo-owned Comfy bridge for stable Prompt/Caption image input, text output, and Generic MTMD VLM fallback | Included in this repo; copy `neo_prompt_captioning` into ComfyUI `custom_nodes` |
| `neo_scene_director` | Neo Studio Scene Director node support | Included in this repo; copy `neo_scene_director` into ComfyUI `custom_nodes` if needed |

### Custom-node setup notes

- **ComfyUI-llama-cpp_vlm:** installing the folder alone is not enough. Install its Python requirements with the same Python environment that runs ComfyUI, restart ComfyUI, then reconnect/test the backend in Neo. GGUF LLM/VLM files belong under `ComfyUI/models/LLM`; VLMs also need their matching `mmproj` file.
- **Krea 2 Control (Depth):** `comfyui-krea2-controlnet` loads its Control LoRA from `ComfyUI/models/loras`. It does not generate depth/canny/pose maps itself; use normal Comfy nodes or `comfyui_controlnet_aux` for the control map.
- **Krea 2 Control (Composition / Silhouette):** `comfyui-krea2-controlnetPlus` adds the Krea2 Control Plus loader/image-encode/apply nodes and is required before Neo can expose the Composition / Silhouette intent.
- **Krea 2 Control (OpenPose / Ostris):** `ComfyUI-Krea2-Ostris-Edit` provides `TextEncodeKrea2OstrisEdit` and `Krea2OstrisEditModelPatch`. Neo still relies on `comfyui_controlnet_aux` to build the DWPose/OpenPose map and on a separate OpenPose control LoRA in `ComfyUI/models/loras`.
- **Krea 2 Control (Canny / NK2E):** `ComfyUI-NK2E` provides the in-context wrapper/reference nodes used by the Krea Canny route. Neo uses the current `NK2EInContextModelNode` + `NK2ESetReferenceNode` path and expects a separate NK2E Canny LoRA in `ComfyUI/models/loras`.
- **Krea 2 Identity Edit:** `comfyui-krea2edit` requires the Krea 2 Identity Edit LoRA separately; the node repository does not bundle the LoRA weights.

### Krea 2 ControlNet install matrix

> All Krea 2 Control LoRAs below belong under `ComfyUI/models/loras/`. Neo currently keeps **one active Krea control unit** at a time.

| Intent | Neo route | Required custom nodes | Control LoRA / model file | Notes |
|---|---|---|---|---|
| **Depth** | Krea 2 RAW + Turbo | `comfyui-krea2-controlnet`, `comfyui_controlnet_aux` | `depth-control-lora.safetensors` | Native Krea control route. Physically validated on the user's setup. |
| **Composition / Silhouette** | Krea 2 RAW + Turbo | `comfyui-krea2-controlnetPlus` | `krea2-anythng_step_007000.safetensors` | Control Plus route with Strength + Start % + End %. Start at Strength `0.7`, Start `0.0`, End `1.0`. |
| **OpenPose / Ostris** | Krea 2 Turbo | `ComfyUI-Krea2-Ostris-Edit`, `comfyui_controlnet_aux` | `krea2_turbo_openpose_controlnet.safetensors` | Uses DWPose/OpenPose map generation plus the Ostris edit conditioning/model patch path. Start around Strength `0.85`. |
| **Canny / NK2E** | Krea 2 RAW | `ComfyUI-NK2E`, `comfyui_controlnet_aux` | `NK2E-canny-v0.1.safetensors` | Uses the NK2E in-context model/reference path. Start around Strength `0.70`, Detect res `512`, Canny `100/200`, Denoise `1.0`. |

### Krea 2 ControlNet quick links

**Node repositories**

- `comfyui-krea2-controlnet` — https://github.com/facok/comfyui-krea2-controlnet
- `comfyui-krea2-controlnetPlus` — https://github.com/tori29umai0123/comfyui-krea2-controlnetPlus
- `ComfyUI-Krea2-Ostris-Edit` — https://github.com/ostris/ComfyUI-Krea2-Ostris-Edit
- `ComfyUI-NK2E` — https://github.com/Nynxz/ComfyUI-NK2E
- `comfyui_controlnet_aux` — https://github.com/Fannovel16/comfyui_controlnet_aux

**Control LoRA / model files**

- `depth-control-lora.safetensors` — https://huggingface.co/Patil/Krea-2-depth-controlnet/blob/main/depth-control-lora.safetensors
- `krea2-anythng_step_007000.safetensors` — https://huggingface.co/tori29umai/krea2-controlnet/blob/main/krea2-anythng_step_007000.safetensors
- `krea2_turbo_openpose_controlnet.safetensors` — https://huggingface.co/thedeoxen/Krea-2-pose-controlnet/blob/main/krea2_turbo_openpose_controlnet.safetensors
- `NK2E-canny-v0.1.safetensors` — https://huggingface.co/nynxz/NK2E/tree/main/comfy/canny_v0.1

- **SUPIR:** the Kijai wrapper remains useful for older workflows, but upstream notes that SUPIR support is now available in ComfyUI core.
- **Neo-owned nodes:** after updating Neo, recopy the bundled `neo_prompt_captioning` or `neo_scene_director` folder into ComfyUI `custom_nodes` whenever that bundled node itself was updated.

> **Note:** Some custom nodes require additional model files that are not included during installation. Check each node’s official GitHub page for its required models, download instructions, and correct ComfyUI model folders before using the related workflow.

### Installing nodes with Neo Node Manager

1. Open **Neo Studio**.
2. Go to **Admin > Extensions > Node Manager**.
3. Set **ComfyUI custom_nodes path**.

Example:

```text
<ComfyUI-portable-root>/ComfyUI/custom_nodes
```

4. Set **Python executable for pip installs**.

Example:

```text
<ComfyUI-portable-root>/python_embeded/python.exe
```

5. Save settings.
6. Use the GitHub links to install nodes one by one.
7. Wait until each install finishes before starting the next.
8. Restart ComfyUI after installing or updating nodes.
9. Reconnect/test the backend in Neo.

> Recommended: disconnect the Comfy backend in Neo before installing/updating custom nodes, then restart ComfyUI and reconnect after installation.

### Important note for `neo_scene_director`

`neo_scene_director` is included with Neo Studio. Copy it into your ComfyUI `custom_nodes` folder if it is not installed automatically.

Example:

```text
ComfyUI/custom_nodes/neo_scene_director
```

---

## 🧠 Memory / Embedding / Reranker Setup

Assistant and Roleplay memory/retrieval features may use local embedding and reranker models.

Recommended models:

| Model | Purpose |
|---|---|
| `BAAI/bge-small-en-v1.5` | Lightweight embedding model |
| `BAAI/bge-m3` | Stronger multilingual/general embedding model |
| `Qwen/Qwen3-Reranker-4B` | Reranking retrieved memory/context |

### Download example

Install the Hugging Face CLI first, then download models to a local folder:

```bat
hf download BAAI/bge-small-en-v1.5 --local-dir "ADD YOUR PATH\bge-small-en-v1.5"
hf download BAAI/bge-m3 --local-dir "ADD YOUR PATH\bge-m3"
hf download Qwen/Qwen3-Reranker-4B --local-dir "ADD YOUR PATH\Qwen3-Reranker-4B"
```

You can choose any local folder path. Do **not** use hardcoded paths from another machine.

### Link models inside Neo Studio

1. Open **Admin**.
2. Go to **Memory Engine**.
3. Open **Embeddings and Reranker**.
4. Set the embedding model path.
5. Set the reranker model path.
6. Save the engine settings.
7. Restart or reload memory-aware surfaces if needed.

---

## 📁 Runtime Data and Project Files

Neo Studio keeps user/runtime data outside the source repo under:

```text
neo_data/
```

Typical runtime data includes:

- backend status and local profile state;
- generated image/video outputs;
- metadata sidecars;
- source/control/mask/reference uploads;
- Assistant chats, attachments, snapshots, and project-brain indexes;
- Roleplay memory/compile/runtime records;
- logs and diagnostic traces.

Release/source packages should not include `neo_data/`, cache folders, generated outputs, or local user project data.

---
## 🧩 Backend Notes & Troubleshooting
---

### 🧩 Extensions Not Showing in a Workspace

If an extension is installed/built in but does not appear inside a workspace, first check whether it is enabled for that surface.

Neo Studio extensions can be enabled or disabled per surface. For example, an Image extension may be installed but hidden if it is disabled under the Image surface settings.

To check this:

1. Open **Neo Studio**.
2. Go to **Admin**.
3. Open **Extention**.
4. Select the surface you want to check, for example **Image**.
5. Review the available extensions.
6. Enable or disable the extension as needed.
7. Return to the workspace and refresh/reload if required.

Example path:

```text
Admin → Extention → Image
```

---

### ⚠️ InsightFace / IPAdapter FaceID Setup Note (Python 3.13)

If you are using newer ComfyUI portable builds with **Python 3.13**, normal:

```bat
pip install insightface
```

may fail with errors like:

```txt
No module named 'insightface'
fatal error C1083: Cannot open include file: 'Python.h'
```

This happens because PyPI may try to build InsightFace from source instead of using a compatible wheel.

#### Recommended fix for Python 3.13

Install the prebuilt `cp313` wheel directly:

```bat
python -m pip install --force-reinstall https://github.com/Gourieff/Assets/raw/main/Insightface/insightface-0.7.3-cp313-cp313-win_amd64.whl
```

Then install/update ONNX Runtime GPU:

```bat
python -m pip install --upgrade onnxruntime-gpu
```

#### Verify installation

```bat
python -c "import insightface; print('insightface ok')"
```

Expected result:

```txt
insightface ok
```

#### Verify CUDA provider

```bat
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

Expected providers should include:

```txt
CUDAExecutionProvider
```

Do **not** rely on `pip install insightface` for Python 3.13 portable builds unless you intentionally want to compile from source with full Visual Studio C++ Build Tools installed.

This mainly affects:

- IPAdapter FaceID;
- Scene Director identity routing;
- ReActor / face swap systems;
- InsightFace-based workflows.

---

### ⚠️ Live Preview Not Working Inside Neo Studio

If **Neo Studio shows no live preview**, even though generation still completes correctly, the issue may be ComfyUI preview websocket output not being enabled for external websocket/API clients.

Typical Neo debug state may show:

```js
window.getNeoGenerationPreviewDebugState()
```

Result:

```txt
socket_open: true
binary_frames: 0
preview_frames: 0
```

This means:

- Neo connected successfully;
- no preview image frames were received.

#### Recommended fix

Add:

```bat
--preview-method auto
```

to your ComfyUI startup BAT.

Example:

```bat
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --preview-method auto
```

Even if previews appear inside the normal ComfyUI browser interface, external websocket/API preview clients such as Neo Studio may not receive preview frames unless preview output is explicitly enabled.

This mainly affects:

- Neo Studio live preview;
- external websocket preview clients;
- API-driven generation dashboards;
- custom frontend integrations using Comfy websocket previews.

---

## 🎥 Setup Guide Video

Will be added when the current setup walkthrough is ready.

---

## 📚 Documentation and Guides

User-facing and Assistant-readable guides are available in:

```text
guides/
```

Recommended starting points:

| Area | Guide |
|---|---|
| Global overview | `guides/00_GLOBAL/neo_overview.md` |
| Backend profiles | `guides/00_GLOBAL/backend_profiles.md` |
| Admin Model Guide | `guides/07_ADMIN/model_guide.md` |
| Forge Neo complete support | `guides/01_IMAGE/forge_neo_complete_support.md` |
| Forge Neo Admin setup | `guides/07_ADMIN/forge_neo_admin.md` |
| xAI Grok Imagine backend | `guides/01_IMAGE/xai_grok_imagine.md` |
| Image overview | `guides/01_IMAGE/image_tab_overview.md` |
| Provider-action release integration | `guides/01_IMAGE/provider_action_release_integration.md` |
| Image parameters | `guides/01_IMAGE/image_parameters.md` |
| Image model families | `guides/01_IMAGE/image_model_families.md` |
| Qwen Rapid AIO | `guides/01_IMAGE/qwen_rapid_aio.md` |
| Output Inspector | `guides/01_IMAGE/output_inspector.md` |
| Video overview | `guides/02_VIDEO/video_tab_overview.md` |
| Roleplay overview | `guides/03_ROLEPLAY/roleplay_overview.md` |
| Prompting & Captioning | `guides/04_PROMPT_CAPTIONING/prompt_captioning_overview.md` |
| Voice overview | `guides/05_VOICE/voice_overview.md` |
| Assistant Project Brain | `guides/06_ASSISTANT/project_brain.md` |

Neo Assistant can use these guides as built-in stable knowledge when answering scope-aware questions.

---

## 🧠 Philosophy

Neo Studio is built as a **system**, not just a single tool.

- Local-first workflows.
- Modular backend/provider control.
- Traceable generation metadata.
- Surface-aware project context.
- Assistant-guided creative work.
- Designed for creators who want control instead of black-box automation.

---

## ⚠️ Known Limitations

- External backends must be installed manually.
- AI models are not included.
- Custom nodes can break or change behavior after upstream updates.
- Video workflows are hardware-heavy and depend strongly on local VRAM, model choices, and installed node packs.
- Some surfaces are still under active development.
- UI/UX improvements and documentation are ongoing.
- Not optimized for low-end systems.

---

## 📜 License

Neo Studio is licensed under the GNU General Public License (GPL).

---

## 🚀 Future Direction

Neo Studio will continue evolving into a unified local creative system, expanding deeper into:

- video generation and finishing;
- audio/music workflows;
- additional Voice model families and speech workflows;
- project delivery systems;
- visual board workflows;
- stronger Assistant project memory and automation.

---

## ☕ Support the Project

If you find Neo Studio useful and want to support development:

👉 https://ko-fi.com/moodpixel

Support is optional, but always appreciated 💙


<!-- README screenshot URLs: upload each PNG through GitHub user-attachments, then replace only the URL on the matching line below. The PNG files themselves should stay untracked. -->
[shot-image-01]: https://github.com/user-attachments/assets/REPLACE-Image01
[shot-image-02]: https://github.com/user-attachments/assets/REPLACE-Image02
[shot-image-03]: https://github.com/user-attachments/assets/REPLACE-Image03
[shot-video-01]: https://github.com/user-attachments/assets/REPLACE-Video01
[shot-video-02]: https://github.com/user-attachments/assets/REPLACE-Video02
[shot-video-03]: https://github.com/user-attachments/assets/REPLACE-Video03
[shot-voice-01]: https://github.com/user-attachments/assets/REPLACE-Voice01
[shot-voice-02]: https://github.com/user-attachments/assets/REPLACE-Voice02
[shot-voice-03]: https://github.com/user-attachments/assets/REPLACE-Voice03
[shot-voice-04]: https://github.com/user-attachments/assets/REPLACE-Voice04
[shot-prompt-01]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning01
[shot-prompt-02]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning02
[shot-prompt-03]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning03
[shot-prompt-04]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning04
[shot-prompt-05]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning05
[shot-prompt-06]: https://github.com/user-attachments/assets/REPLACE-PromptCaptioning06
[shot-roleplay-01]: https://github.com/user-attachments/assets/REPLACE-Roleplay01
[shot-roleplay-02]: https://github.com/user-attachments/assets/REPLACE-Roleplay02
[shot-roleplay-03]: https://github.com/user-attachments/assets/REPLACE-Roleplay03
[shot-roleplay-04]: https://github.com/user-attachments/assets/REPLACE-Roleplay04
[shot-assistant-01]: https://github.com/user-attachments/assets/REPLACE-Assistant01
[shot-assistant-02]: https://github.com/user-attachments/assets/REPLACE-Assistant02
[shot-assistant-03]: https://github.com/user-attachments/assets/REPLACE-Assistant03
[shot-admin-01]: https://github.com/user-attachments/assets/REPLACE-Admin
