# Neo Studio V2

**Neo Studio V2** is a local-first AI creative workspace for **Image, Video, Voice, Prompt & Captioning, Roleplay, and Assistant workflows** in one interface.

It is built for creators who want to use their own local AI backends and models without jumping between separate tools for every part of a workflow.

Neo Studio does **not** bundle third-party AI models, ComfyUI, Forge Neo, KoboldCPP, or cloud API credentials. You connect the tools you want to use, and Neo keeps each workspace tied to the backend profile you selected.

> **New here?** Start with [Installation](#-installation), then [Connect Your Backends](#-connect-your-backends).

---

## Table of Contents

- [✨ What You Can Do](#-what-you-can-do)
- [🖼️ Workspace Screenshots](#️-workspace-screenshots)
- [💻 Requirements](#-requirements)
- [⚙️ Installation](#️-installation)
- [🔌 Connect Your Backends](#-connect-your-backends)
- [🎨 Image](#-image)
- [🎬 Video](#-video)
- [🎙️ Voice](#️-voice)
- [✍️ Prompt & Captioning](#️-prompt--captioning)
- [🎭 Roleplay](#-roleplay)
- [🤖 Assistant](#-assistant)
- [🧠 Models, Custom Nodes, and Extensions](#-models-custom-nodes-and-extensions)
- [📁 User Data and Outputs](#-user-data-and-outputs)
- [🛠️ Troubleshooting](#️-troubleshooting)
- [📚 User Guides](#-user-guides)
- [⚠️ Current Limitations](#️-current-limitations)
- [📜 License](#-license)
- [☕ Support the Project](#-support-the-project)

---

## ✨ What You Can Do

### 🎨 Image

Use Neo Studio as a front end for supported **ComfyUI / ComfyUI Portable**, **Forge Neo**, or optional **xAI Grok Imagine** image workflows.

Depending on the backend, model family, and installed nodes, the Image workspace can provide:

- text-to-image generation;
- Img2Img and image editing;
- inpainting and outpainting;
- multiple reference images;
- ControlNet;
- IP Adapter / FaceID;
- LoRA Stack;
- Style Stack and Wildcards;
- ADetailer;
- LayerDiffuse;
- High-Res and upscale workflows;
- LanPaint;
- Forge Couple;
- Scene Director;
- output inspection, metadata, replay, and reusable result actions.

Neo shows only the controls that make sense for the selected backend/model route whenever that information is available.

### 🎬 Video

The Video workspace uses supported ComfyUI-based workflows for AI video generation and finishing.

Available workflows depend on your installed models and custom nodes and can include:

- text/image-to-video;
- first-frame and first/last-frame workflows;
- source-image video generation;
- multiscene workflows;
- video extension;
- video-to-video;
- depth or motion-guided workflows;
- interpolation;
- upscale and repair/finishing tools.

### 🎙️ Voice

The Voice workspace runs local TTS through the **Neo Voice Engine** and supported isolated voice runtimes.

User-facing Voice tools include:

- single-voice TTS;
- model-specific voice controls;
- reference audio and supported cloning workflows;
- reusable Voice Profile Assets;
- Dialogue / Multi-speaker generation;
- TXT, Markdown, CSV, JSON, and SRT batch workflows;
- shared Voice Results;
- replay to draft;
- local finishing such as normalize, trim, cleanup, conversion, split, and merge where supported.

Current local Voice support includes **Chatterbox** and **Qwen3-TTS CustomVoice** workflows. See [Voice](#️-voice) for setup.

### ✍️ Prompt & Captioning

Use local text or vision-capable backends for:

- prompt generation and rewriting;
- prompt cleanup and negative-prompt helpers;
- image captioning;
- batch captioning;
- prompt/caption libraries;
- reusable presets;
- image-edit and video prompt helpers.

### 🎭 Roleplay

Build structured roleplay and story projects with:

- character and world tools;
- scene workflows;
- stories;
- project memory and continuity;
- local text generation through a connected text backend.

### 🤖 Assistant

Neo Assistant can work with project context, attachments, Neo user guides, saved project knowledge, and workspace-aware information.

It is designed to help you move between creative tasks without losing the context of the project you are working on.

### ⚙️ Admin

Admin is where you manage the tools Neo depends on:

- backend profiles and connection tests;
- model installation/status for supported managed models;
- extensions and custom nodes;
- memory/embedding settings;
- provider settings;
- runtime logs and general configuration.

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

---

## 💻 Requirements

### Neo Studio

- **Windows 10 or Windows 11**
- **Python 3.10 or newer**
- **Git** if you want to clone/update through Git

### For local AI generation

Your hardware requirements depend on the models and backends you choose.

As a general rule:

- image and video generation benefit strongly from an NVIDIA GPU with enough VRAM for the selected model;
- larger Voice models also need available GPU memory when using CUDA;
- video models can require substantially more VRAM/system RAM than image models;
- model quantization, architecture, and runtime matter more than file size alone.

Neo Studio itself can be installed before you configure every AI backend.

---

## ⚙️ Installation

### 1. Clone Neo Studio

Open Command Prompt or PowerShell in the folder where you want Neo Studio and run:

```bat
git clone https://github.com/MoodPixel/Neo_Studio_V2.git
cd Neo_Studio_V2
```

You can also download the repository as a ZIP from GitHub and extract it manually.

### 2. Create the Neo Studio environment

Run:

```bat
setup_neo_studio_venv.bat
```

This creates Neo Studio's local `.venv` and installs the main application requirements.

### 3. Start Neo Studio

Run:

```bat
run_neo_studio.bat
```

Keep the launcher window open while Neo Studio is running.

Neo opens in your browser automatically. The normal launcher uses:

```text
http://127.0.0.1:7870
```

If you changed the host/port, use the URL shown in the launcher window instead.

### 4. Configure the backends you actually use

You do **not** need to install every supported backend.

For example:

- primarily use ComfyUI → connect a ComfyUI profile;
- primarily use Forge Neo → connect the Forge Neo profile;
- use local LLM features → connect KoboldCPP or another supported text route;
- use Voice → set up Neo Voice Engine plus the Voice model family you want.

---

## 🔌 Connect Your Backends

Neo ships with backend profile templates for the main workspaces.

Open:

```text
Admin → Backends
```

Then select an existing profile, add the local path/API key that applies to your machine, save it, and run **Test Connection**.

### Common backends

| Backend | Main use in Neo | Project / download |
|---|---|---|
| **ComfyUI / ComfyUI Portable** | Image, Video, custom-node workflows | https://github.com/Comfy-Org/ComfyUI |
| **Forge Neo** | Local Image generation/editing and supported Forge features | https://github.com/Haoming02/sd-webui-forge-classic/tree/neo |
| **KoboldCPP** | Local Assistant, Roleplay, Prompting, Captioning, chat | https://github.com/LostRuins/koboldcpp/releases |
| **xAI Grok Imagine** | Optional cloud Image generation/editing | https://docs.x.ai/ |

Typical local URLs are:

```text
ComfyUI:    http://127.0.0.1:8188
Forge Neo:  http://127.0.0.1:7860
KoboldCPP:  http://127.0.0.1:5001
Voice:      http://127.0.0.1:8790
```

> Neo Studio and Forge Neo must use different ports. Neo's normal launcher uses `7870`, while Forge commonly uses `7860`.

### Recommended connection flow

1. Start the backend you want to use.
2. Open **Admin → Backends**.
3. Select the matching existing profile.
4. Add/change only the path, launcher, port, or API key that differs on your machine.
5. Click **Save Profile**.
6. Click **Test Connection**.
7. Set it as the default for that workspace only if you want Neo to use it by default.

Neo does not silently switch to another provider just because another backend is online.

---

## 🎨 Image

### Supported backends

The Image workspace currently supports user-facing routes through:

- **ComfyUI / ComfyUI Portable**;
- **Forge Neo**;
- **xAI Grok Imagine** as an optional cloud Image profile.

### Model families

Neo supports a growing set of image-model families. Current user-facing families include:

- SDXL;
- SD 1.5;
- Flux 1;
- Flux 2 Klein;
- Flux 2 Dev for supported workflows;
- Krea 2 RAW;
- Krea 2 Turbo;
- Qwen Rapid AIO;
- Qwen Image Edit;
- Qwen Image Edit 2509;
- Qwen Image Edit 2511;
- ZImage;
- ZImage Turbo;
- Anima Base v1;
- Ideogram 4;
- Stable Diffusion 3.5 for supported workflows;
- HiDream-I1 for supported workflows.

Exact Generate/Edit/Inpaint/Outpaint support depends on the selected backend, model format, installed components, and custom nodes.

For the current family-by-family details, use:

```text
guides/01_IMAGE/image_model_families.md
```

### Safetensors and GGUF

When a model family supports both, Neo lets you choose the model format and then exposes the additional model components required by that workflow.

This means some models may need separate text encoders, VAE/AE files, LoRAs, or other components while classic checkpoint-style models may not.

### References and character consistency

Depending on the selected model/backend, Neo can use approaches such as:

- multi-reference Qwen Image Edit workflows;
- Krea 2 Identity Edit;
- IP Adapter / FaceID;
- LoRA Stack;
- Scene Director.

### Forge Neo users

For the best Forge Neo integration, install/update the bundled **Neo Forge Bridge** using:

```text
neo_integrations/forge_neo_bridge/README.md
```

Forge Neo must be started with API access enabled. A minimal launcher is:

```bat
call webui.bat --api --uv
```

After installing or updating Forge extensions/bridge components, restart Forge Neo and test/refresh its profile in Neo Studio.

---

## 🎬 Video

The Video workspace uses ComfyUI-backed generation and finishing workflows.

### Basic setup

1. Install/start ComfyUI or ComfyUI Portable.
2. Install the video model(s) and custom nodes required by the workflow you want.
3. Open **Admin → Backends → Video**.
4. Select the matching ComfyUI profile.
5. Save and **Test Connection**.
6. Open the Video workspace and choose a model/workflow supported by your installation.

Video support is highly dependent on model files, node packs, GPU VRAM, and system RAM.

See:

```text
guides/02_VIDEO/video_tab_overview.md
```

---

## 🎙️ Voice

Voice uses a lightweight **Neo Voice Engine** gateway plus separate environments for supported Voice model families.

The model-specific environments stay outside Neo Studio's main Python environment so different TTS stacks can use their own dependencies.

### First-time Voice Engine setup

Run:

```bat
setup_neo_voice_engine.bat
```

Then set up the Voice family you want to use.

### Option A — Chatterbox

Run:

```bat
setup_chatterbox_backend.bat
```

Then:

1. Start Neo Studio.
2. Open **Admin → Models**.
3. Install or verify **Chatterbox Turbo** and/or **Chatterbox Multilingual V3**.
4. Start the Voice Engine with:

```bat
run_neo_voice_engine.bat
```

5. Open the Voice workspace and use the **Neo Voice Engine** profile.

### Option B — Qwen3-TTS CustomVoice

Run:

```bat
setup_qwen3_tts_backend.bat
```

Then:

1. Start Neo Studio.
2. Open **Admin → Models**.
3. Install **Qwen3-TTS 0.6B CustomVoice** and/or **Qwen3-TTS 1.7B CustomVoice**.
4. Start the Voice Engine:

```bat
run_neo_voice_engine.bat
```

5. Open the Voice workspace and select the installed Qwen model.

User-visible Qwen differences currently include:

| Model | User controls |
|---|---|
| **Qwen3-TTS 0.6B CustomVoice** | Language + Speaker |
| **Qwen3-TTS 1.7B CustomVoice** | Language + Speaker + Voice Instruction |

The 1.7B model requires substantially more free GPU memory than the 0.6B model. If another image/video workload is already using VRAM, finish or unload that workload before starting a cold Voice generation.

### Voice model downloads

Voice setup scripts install the required runtime/dependencies. They do **not** silently download large model weights during generation.

Supported managed Voice models can be installed explicitly through:

```text
Admin → Models
```

Existing compatible local Voice models can continue to be used when Neo recognizes them; you do not need to duplicate a working model just to use the Voice workspace.

More help:

```text
guides/05_VOICE/voice_overview.md
guides/05_VOICE/qwen3_tts.md
```

---

## ✍️ Prompt & Captioning

Prompt & Captioning uses a connected text or multimodal backend.

A common local setup is **KoboldCPP** for text workflows, while supported ComfyUI LLM/VLM routes can be used for compatible prompt/caption workflows.

Basic flow:

1. Start your text/VLM backend.
2. Open **Admin → Backends**.
3. Select the matching text or Prompt/Caption backend profile.
4. Test the connection.
5. Open **Prompt & Captioning** and choose the task you want.

See:

```text
guides/04_PROMPT_CAPTIONING/prompt_captioning_overview.md
```

---

## 🎭 Roleplay

Roleplay uses a connected local text backend plus Neo project/character/world data.

For a basic local setup:

1. Start KoboldCPP or another supported text backend.
2. Connect/test it from **Admin → Backends**.
3. Open Roleplay.
4. Create or load your project/characters/world data.

See:

```text
guides/03_ROLEPLAY/roleplay_overview.md
```

---

## 🤖 Assistant

Assistant can use Neo project context and optional semantic memory features.

For local generation, connect a supported text backend such as KoboldCPP.

For semantic retrieval/memory, you can also configure embedding and reranker models from Admin. Recommended examples currently documented by Neo include:

- `BAAI/bge-small-en-v1.5`;
- `BAAI/bge-m3`;
- `Qwen/Qwen3-Reranker-4B`.

Configure them from:

```text
Admin → Memory Engine → Embeddings and Reranker
```

See:

```text
guides/06_ASSISTANT/project_brain.md
```

---

## 🧠 Models, Custom Nodes, and Extensions

Neo Studio does **not** include the third-party model files required by ComfyUI, Forge Neo, local LLMs, or Voice models.

Install only the models and node packs needed for the workflows you intend to use.

### ComfyUI custom nodes

Features such as ControlNet preprocessors, IP Adapter, FaceID, LanPaint, RMBG, advanced samplers, video workflows, and some model-family-specific routes require compatible ComfyUI custom nodes.

Neo's detailed user guides describe the required nodes for each feature instead of requiring every user to install one giant node pack.

Useful starting points:

```text
guides/01_IMAGE/controlnet.md
guides/01_IMAGE/ip_adapter_faceid.md
guides/01_IMAGE/lanpaint_route_family.md
guides/01_IMAGE/image_upscale.md
guides/02_VIDEO/video_generation_extensions.md
guides/07_ADMIN/model_guide.md
```

### Extensions not appearing

If a built-in/installed extension is not visible in a workspace:

1. Open **Admin**.
2. Open **Extension**.
3. Select the relevant workspace, such as **Image**.
4. Enable the extension.
5. Return to the workspace and refresh/reload if needed.

---

## 📁 User Data and Outputs

Neo creates local runtime/user data under:

```text
neo_data/
```

This can include:

- settings and backend profile state;
- generated image/video outputs;
- metadata sidecars;
- uploaded source/control/mask/reference files;
- Assistant chats and project context;
- Roleplay/project memory;
- logs and diagnostics.

Voice uses a separate runtime tree next to the Neo Studio source folder:

```text
<Neo parent>/Neo_Runtime/voice/
```

That location can contain Voice environments, model/cache data, temporary files, logs, state, and Voice outputs.

### Back up before replacing your installation

If you are moving Neo Studio to another machine or replacing a working installation, back up any user/project/model data you want to keep before deleting folders.

Do not assume a fresh Git clone contains your local `neo_data` or Voice runtime data.

---

## 🛠️ Troubleshooting

### Neo Studio does not start

- Make sure `setup_neo_studio_venv.bat` completed successfully.
- Confirm `.venv\Scripts\python.exe` exists.
- Run `run_neo_studio.bat` again and read the console error.
- Check logs under `neo_data\logs\` if Neo created them.

### Backend shows disconnected

1. Start the backend itself.
2. Open **Admin → Backends**.
3. Select the correct profile.
4. Check the URL/path/launcher.
5. Click **Test Connection**.
6. Retry the generation after the profile reports ready.

### ComfyUI generation works but Neo live preview is missing

Start ComfyUI with preview output enabled, for example:

```bat
--preview-method auto
```

A portable launcher may look like:

```bat
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --preview-method auto
```

### IP Adapter FaceID / InsightFace on Python 3.13

Some Python 3.13 ComfyUI Portable installations cannot install InsightFace from PyPI normally.

A commonly used prebuilt Windows wheel is:

```bat
python -m pip install --force-reinstall https://github.com/Gourieff/Assets/raw/main/Insightface/insightface-0.7.3-cp313-cp313-win_amd64.whl
python -m pip install --upgrade onnxruntime-gpu
```

Verify with:

```bat
python -c "import insightface; print('insightface ok')"
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

For GPU use, `CUDAExecutionProvider` should appear in the ONNX Runtime provider list.

### Voice model is installed but generation is blocked

- Make sure `run_neo_voice_engine.bat` is running.
- Test the **Neo Voice Engine** backend profile.
- Check **Admin → Models** for the selected Voice model status.
- If using Qwen3-TTS 1.7B, free GPU VRAM by stopping/unloading competing Image or Video workloads.
- Existing compatible local Voice models do not need to be redownloaded solely because an optional managed copy is absent.

For more troubleshooting, use the matching guide under `guides/`.

---

## 📚 User Guides

The `guides/` folder contains Neo Studio's detailed user help.

Start here:

| Area | Guide |
|---|---|
| Neo Studio overview | `guides/00_GLOBAL/neo_overview.md` |
| Backend profiles | `guides/00_GLOBAL/backend_profiles.md` |
| Admin model guide | `guides/07_ADMIN/model_guide.md` |
| Image overview | `guides/01_IMAGE/image_tab_overview.md` |
| Image model families | `guides/01_IMAGE/image_model_families.md` |
| ControlNet | `guides/01_IMAGE/controlnet.md` |
| Forge Neo | `guides/01_IMAGE/forge_neo_complete_support.md` |
| Video overview | `guides/02_VIDEO/video_tab_overview.md` |
| Voice overview | `guides/05_VOICE/voice_overview.md` |
| Qwen3-TTS | `guides/05_VOICE/qwen3_tts.md` |
| Prompt & Captioning | `guides/04_PROMPT_CAPTIONING/prompt_captioning_overview.md` |
| Roleplay | `guides/03_ROLEPLAY/roleplay_overview.md` |
| Assistant / Project Brain | `guides/06_ASSISTANT/project_brain.md` |

The root README is the quick-start/product overview. Use the guides when you need feature-specific setup, settings, compatibility information, or troubleshooting.

---

## ⚠️ Current Limitations

- Neo Studio does not include third-party AI backends or model weights.
- Hardware requirements vary heavily by model and workflow.
- Video generation can be demanding on VRAM and system RAM.
- Custom-node updates can occasionally break workflows until the relevant integration is updated.
- Not every model family supports every Generate/Edit/Inpaint/Outpaint workflow.
- Some Voice families and advanced audio/music profiles are still experimental or not enabled for normal use.
- Music and visual Board workspaces are not currently part of the active public workspace set.
- Neo Studio is under active development, so UI and workflow details can change between releases.

---

## 📜 License

Neo Studio V2 is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.

See the included `LICENSE` file for the full license text.

---

## ☕ Support the Project

If Neo Studio is useful to you and you want to support its development:

👉 https://ko-fi.com/moodpixel

Support is optional and appreciated. 💙


<!-- README screenshot URLs are hosted through GitHub user attachments so normal clones do not download screenshot binaries. -->
[shot-image-01]: https://github.com/user-attachments/assets/2b9b3560-189f-425f-959b-5490ca76fa75
[shot-image-02]: https://github.com/user-attachments/assets/792091a4-8095-4007-aea7-ff7aaff9ceaa
[shot-image-03]: https://github.com/user-attachments/assets/89fa0955-0770-4ab9-a5e0-98cbfb50fd77
[shot-video-01]: https://github.com/user-attachments/assets/2a5075f6-14ed-4c36-bee8-bf97a924b4b4
[shot-video-02]: https://github.com/user-attachments/assets/087fe1d3-a57b-4a8d-aa0e-9c758253b1ef
[shot-video-03]: https://github.com/user-attachments/assets/25048cab-c6b0-4339-a3a5-5a8eba3857ad
[shot-voice-01]: https://github.com/user-attachments/assets/c914007b-1a9f-4fb6-9bae-8a9dbd74baf2
[shot-voice-02]: https://github.com/user-attachments/assets/3ef78f09-1141-4e12-adf6-9031ffd38819
[shot-voice-03]: https://github.com/user-attachments/assets/0ca3b6d7-0c74-4a8b-88c5-e7ada150cd9c
[shot-voice-04]: https://github.com/user-attachments/assets/25048cab-c6b0-4339-a3a5-5a8eba3857ad
[shot-prompt-01]: https://github.com/user-attachments/assets/87eefad8-d206-4e06-8730-ce39630791bf
[shot-prompt-02]: https://github.com/user-attachments/assets/cd1e3e04-4418-440a-a3c1-f2bd4b45b6e4
[shot-prompt-03]: https://github.com/user-attachments/assets/05687313-f062-4a4e-aed7-aa5e8d7634cb
[shot-prompt-04]: https://github.com/user-attachments/assets/a5f8c5bd-90f4-4b2c-b329-d8b59268e1a3
[shot-prompt-05]: https://github.com/user-attachments/assets/8c484e38-2b4e-47bf-b516-b80a6c4fda7b
[shot-prompt-06]: https://github.com/user-attachments/assets/6c8db0ad-4eac-456b-a1e0-33f07e09154a
[shot-roleplay-01]: https://github.com/user-attachments/assets/865591b9-be1b-4ee0-860d-80237afa4354
[shot-roleplay-02]: https://github.com/user-attachments/assets/e7e3a696-0ca4-4ad7-8810-f08f6777f059
[shot-roleplay-03]: https://github.com/user-attachments/assets/b7d9be09-2190-483d-9f40-9fb40df863e5
[shot-roleplay-04]: https://github.com/user-attachments/assets/18ef0557-8d13-45fa-8566-326f5e0213a0
[shot-assistant-01]: https://github.com/user-attachments/assets/9f0bb8e0-1d2f-4a9a-9985-b97a5b2b3a94
[shot-assistant-02]: https://github.com/user-attachments/assets/18f24148-65f6-46f1-b01c-0bf29540ba7f
[shot-assistant-03]: https://github.com/user-attachments/assets/a830ef2d-ab93-462d-88f0-35af2ff5efb9
[shot-admin-01]: https://github.com/user-attachments/assets/24ef7dd2-4acc-4552-a23a-99bab3bb96db
