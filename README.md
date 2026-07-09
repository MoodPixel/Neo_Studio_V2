# Neo Studio V2

**Neo Studio V2** is a local-first AI creative workspace for controlling image, video, prompt, roleplay, assistant, and project-memory workflows from one structured interface.

Neo Studio does **not** include AI models or third-party backend engines. It connects to tools you install separately, such as **ComfyUI Portable** for image/video workflows and **KoboldCPP** for local text/chat/roleplay workflows.

---

## 💡 Why Neo Studio Exists

Local AI tools are powerful, but they are also scattered.

A creator may need ComfyUI for image generation, KoboldCPP for local chat, separate tools for video, captioning, prompt testing, memory, workflows, models, custom nodes, and backend launching. Each tool has its own setup process, interface, file structure, and workflow logic.

ComfyUI is extremely flexible, but node-based workflows can become complex fast, especially when working with advanced pipelines like ControlNet, IPAdapter, inpainting, video generation, LoRAs, upscaling, metadata, and reusable presets.

Neo Studio was created to make local AI usage more streamlined.

The goal is not to replace tools like ComfyUI or KoboldCPP. Instead, Neo Studio acts as a structured control layer on top of them, helping creators use local AI systems through a cleaner, more organized workspace.

Neo Studio is built to:

- reduce workflow confusion
- simplify repeated creative tasks
- keep outputs, metadata, prompts, and settings organized
- connect image, video, assistant, prompt, and roleplay systems into one workspace
- make local AI workflows easier to launch, manage, inspect, and reuse
- give creators more control without forcing them to manually manage every backend detail

In short: **Neo Studio exists to turn scattered local AI tools into one streamlined creative system.**

---



---

## Table of Contents

- [✨ Features](#-features)
- [🚧 Project Status](#-project-status)
- [🧭 Main Tabs Overview](#-main-tabs-overview)
- [⚙️ Installation](#️-installation)
- [🔌 Backend Setup](#-backend-setup)
- [🧩 ComfyUI Custom Nodes](#-comfyui-custom-nodes)
- [🧠 Memory / Embedding / Reranker Setup](#-memory--embedding--reranker-setup)
- [📁 Runtime Data and Project Files](#-runtime-data-and-project-files)
- [🧩 Backend Notes & Troubleshooting](#-backend-notes--troubleshooting)
- [🎥 Setup Guide Video](#-setup-guide-video)
- [📚 Documentation and Guides](#-documentation-and-guides)
- [🧠 Philosophy](#-philosophy)
- [⚠️ Known Limitations](#️-known-limitations)
- [📜 License](#-license)
- [🚀 Future Direction](#-future-direction)
- [☕ Support the Project](#-support-the-project)

---

## ✨ Features

### 🎨 Image Workspace

- Structured image generation and refinement workflows.
- Route-aware model families such as Qwen Rapid AIO, Qwen image edit, checkpoint/Comfy routes, txt2img, img2img, inpaint, and outpaint.
- Extension-aware workflows for ControlNet, IP Adapter, LoRA Stack, Style Stack, Wildcards, LayerDiffuse, CFG Fix / Dynamic Thresholding, High-Res Lab, Image Upscale, and Scene Director.
- Neo-owned output metadata, elapsed generation timing, source asset tracking, cleanup reports, and safe cascade delete from the Output Inspector.

### 🎬 Video Workspace

- Video generation and normal work-task video workflows through ComfyUI-backed routes.
- Active LTX 2.3 and WAN-oriented route support where matching local models/custom nodes are installed.
- Supports txt2vid, img2vid, first/last-frame, multiscene, extend, vid2vid, depth/motion control, prompt scheduling, audio-video metadata routes, and finish lanes such as interpolation/upscale/repair where available.
- Tracks progress, elapsed time, source files, output records, and Neo-owned result playback/import metadata.

### ✍️ Prompting & Captioning

- Generate, refine, and manage prompts/captions.
- Use local text backends for creative drafting, prompt cleanup, and caption workflows.
- Bridge prompt/caption outputs into Assistant and project context.

### 🎭 Roleplay System

- **Forge** — create characters, worlds, universes, legends, and structured entities.
- **Scene** — live roleplay / novel-writing environment with runtime guardrails.
- **Stories** — workspace, storyline, archive, and inspector tooling.
- Memory-aware scene packets, compile/runtime controls, and character/world records.

### 🤖 Assistant

- Scope-aware chat workspace for General, Image, Video, Prompt/Captioning, Roleplay, and Voice contexts.
- Streaming chat, image/document attachments, project file uploads, guide-aware context, and project-brain memory capture.
- Reads built-in `guides/`, live surface snapshots, indexed metadata, uploaded project docs, and selected project memory.

### ⚙️ Admin / Control Tower

- Configure and launch local backends such as ComfyUI Portable and KoboldCPP.
- Manage backend profiles, provider capability checks, connection tests, extension panels, and custom node setup.
- Runtime/user settings are stored under `neo_data/`, not inside the main source folders.

---

## 🚧 Project Status

Neo Studio is currently in **V2 active development**.

Functional areas include:

- Image workspace and output inspector.
- Video workspace and route-aware task workflows.
- Assistant with project-brain context and attachments.
- Roleplay Forge/Scene/Memory systems.
- Prompting/Captioning tools.
- Admin backend/profile/node management.

Still evolving:

- UI polish and documentation coverage.
- More complete guide pages for every surface.
- Additional video route hardening.
- Voice/Music/Board expansion.
- More project automation and delivery tooling.

---

## 🧭 Main Tabs Overview

Each tab in Neo Studio is designed as a focused system:

| Tab | Purpose | Current State |
|---|---|---|
| **Image** | Build, refine, inspect, replay, and manage image generation workflows. | Active / expanding |
| **Video** | Generate, finish, inspect, and manage video workflows and source assets. | Active / expanding |
| **Prompting & Captioning** | Generate and manage prompts, captions, and text assets. | Active / expanding |
| **Roleplay** | Build worlds/characters and run memory-aware scenes. | Active / expanding |
| **Assistant** | Scope-aware chat, project brain, uploaded docs, and workflow help. | Active / expanding |
| **Project** | Link outputs, notes, handoffs, milestones, and project context. | Active / expanding |
| **Voice** | Voice/transcription-related surface foundation. | Early / planned expansion |
| **Music** | Music/audio generation workflow surface. | Planned |
| **Board** | Visual planning and creative organization workspace. | Planned |
| **Admin** | Backend profiles, launchers, extensions, custom nodes, and system tools. | Active / expanding |

---

## ⚙️ Installation

### Requirements

- Windows 10/11.
- Python 3.10+.
- Git.
- Recommended local backends:
  - ComfyUI Portable.
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
| **Image** | ComfyUI Local, ComfyUI Portable, Grok Imagine | Image generation, image edit, Comfy workflows, cloud image generation/edit | Comfy path/launcher if local, or xAI API key for Grok |
| **Video** | Video · ComfyUI Local, Video · ComfyUI Portable | Video generation, video finishing, source-frame workflows | Comfy path/launcher if using local video routes |
| **Text** | KoboldCpp Local | Assistant, Roleplay, Prompting, Captioning, local chat workflows | KoboldCPP launcher/path and model setup |
| **Voice** | Chatterbox, Kokoro Preview, Fish Speech HQ, Zonos, Custom TTS Adapter | Voice/TTS-related future or early workflows | Only needed if you are testing voice workflows |
| **Music / Audio** | ACE-Step, Stable Audio Open, YuE Song HQ, Custom Audio Adapter | Planned or early audio/music workflow profiles | Only needed if you are testing audio/music workflows |

Supported backend tools:

| Backend | Used For | Link |
|---|---|---|
| **ComfyUI / ComfyUI Portable** | Local image generation, video generation, Comfy workflows, custom nodes, live preview, metadata/replay workflows | https://github.com/Comfy-Org/ComfyUI |
| **KoboldCPP** | Local text backend for Assistant, Roleplay, Prompting, Captioning, and chat workflows | https://github.com/LostRuins/koboldcpp/releases |
| **xAI Grok Imagine API** | Cloud image generation and image edit workflows through the seeded Image backend profile | https://docs.x.ai/ |

Suggested local backend folder style:

```text
F:\Backends\ComfyUI_windows_portable\
F:\Backends\KoboldCPP\
```

Cloud API profiles do not need a local backend folder, but they do need a valid API key.

---

### Recommended setup flow

1. Open **Neo Studio**.
2. Go to **Admin → Backends**.
3. Choose the surface tab you need:
   - **Image** for ComfyUI image profiles or Grok Imagine.
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

For ComfyUI or KoboldCPP, the seeded profiles already include the usual default URLs. Check these first before changing anything:

```text
ComfyUI:    http://127.0.0.1:8188
KoboldCPP:  http://127.0.0.1:5001
```

For launcher-based profiles, update only the machine-specific launcher fields:

- **Portable Path**
- **Launch Command**

Use the same launcher file or command you normally use to start ComfyUI or KoboldCPP manually.

If your backend runs on the default URL and you start it manually outside Neo, you may only need to click **Test Connection**.

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
- Neo Studio does not ship with ComfyUI, KoboldCPP, or xAI credentials.
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
| `comfyui-essentials` | Common utility nodes used by many workflows | https://github.com/comfyorg/comfyui-essentials.git |
| `ComfyUI-GGUF` | GGUF model support for image/video model routes | https://github.com/city96/ComfyUI-GGUF.git |
| `gguf` | Additional GGUF utility support | https://github.com/calcuis/gguf.git |
| `ComfyUI-Impact-Pack` | Detection, detailing, masks, segmentation, and utility workflows | https://github.com/ltdrdata/ComfyUI-Impact-Pack.git |
| `ComfyUI-Impact-Subpack` | Support package for Impact Pack | https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git |
| `ComfyUI-Inspire-Pack` | Workflow helpers and utility nodes | https://github.com/ltdrdata/ComfyUI-Inspire-Pack.git |
| `ComfyUI-KJNodes` | Advanced utility, video, and image helper nodes | https://github.com/kijai/ComfyUI-KJNodes.git |
| `comfyui_controlnet_aux` | ControlNet preprocessors for depth, pose, edges, and maps | https://github.com/Fannovel16/comfyui_controlnet_aux.git |
| `ComfyUI_IPAdapter_plus` | IPAdapter reference/identity workflows | https://github.com/cubiq/ComfyUI_IPAdapter_plus.git |
| `ComfyUI_UltimateSDUpscale` | Tiled upscale workflow support | https://github.com/ssitu/ComfyUI_UltimateSDUpscale.git |
| `sd-dynamic-thresholding` | CFG Fix / Dynamic Thresholding support | https://github.com/mcmonkeyprojects/sd-dynamic-thresholding |
| `RES4LYF` | RES4LYF sampler support | https://github.com/ClownsharkBatwing/RES4LYF |
| `rgthree-comfy` | Workflow utility nodes | https://github.com/rgthree/rgthree-comfy.git |
| `facerestore_cf` | CodeFormer / FaceRestore nodes used by Image Upscale face restore assist | https://github.com/mav-rik/facerestore_cf.git |
| `ComfyUI-WanVideoWrapper` | WAN video workflow support and video-specific node paths | https://github.com/kijai/ComfyUI-WanVideoWrapper.git |
| `ComfyUI-TeaCache` | Optional video performance / caching support for compatible WAN/LTX routes | https://github.com/welltop-cn/ComfyUI-TeaCache.git |
| `ComfyUI-LTXVideo` | LTX video generation nodes and LTX-specific workflow support | https://github.com/Lightricks/ComfyUI-LTXVideo.git |
| `ComfyUI-Frame-Interpolation` | Finish-lane interpolation / FPS smoothing | https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git |
| `ComfyUI-VideoHelperSuite` | Video load/combine/save helpers used by many Comfy video workflows | https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git |
| `ComfyUI-SeedVR2_VideoUpscaler` | Video/Image Upscale workflows | https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git |
| `neo_scene_director` | Neo Studio Scene Director node support | Included in this repo; copy `neo_scene_director` into ComfyUI `custom_nodes` if needed |

### Installing nodes with Neo Node Manager

1. Open **Neo Studio**.
2. Go to **Admin > Extensions > Node Manager**.
3. Set **ComfyUI custom_nodes path**.

Example:

```text
F:\ComfyUI_windows_portable\ComfyUI\custom_nodes
```

4. Set **Python executable for pip installs**.

Example:

```text
F:\ComfyUI_windows_portable\python_embeded\python.exe
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

Will be updated when Ready

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
| xAI Grok Imagine backend | `guides/01_IMAGE/xai_grok_imagine.md` |
| Image overview | `guides/01_IMAGE/image_tab_overview.md` |
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
- voice and transcription pipelines;
- project delivery systems;
- visual board workflows;
- stronger Assistant project memory and automation.

---

## ☕ Support the Project

If you find Neo Studio useful and want to support development:

👉 https://ko-fi.com/moodpixel

Support is optional, but always appreciated 💙
