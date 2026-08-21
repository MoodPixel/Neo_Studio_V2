@echo off
setlocal EnableExtensions

REM ============================================================
REM Neo Studio V2 - Qwen3-TTS Isolated Worker Setup (Phase 4.5.8)
REM Creates Neo_Runtime\voice\envs\qwen3_tts outside source.
REM Does not download model weights during setup.
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%cd%"
set "PYTHON_CMD="
set "NVIDIA_DETECTED=0"
set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu"

call :try_python "py -3.12"
if not defined PYTHON_CMD call :try_python "py -3.11"
if not defined PYTHON_CMD call :try_python "py -3.10"
if not defined PYTHON_CMD call :try_python "python"
if not defined PYTHON_CMD call :try_python "python3"

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.9-3.13 was not found. Python 3.12 is preferred for Qwen3-TTS.
    pause
    exit /b 1
)

call "%PROJECT_ROOT%\neo_voice_engine\runtime_paths.bat" "%PROJECT_ROOT%"
if errorlevel 1 goto :failed

set "VENV_DIR=%NEO_VOICE_ENVS_ROOT%\qwen3_tts"
set "MODEL_DIR=%NEO_VOICE_MODELS_ROOT%\qwen3_tts"
if not exist "%NEO_VOICE_ENVS_ROOT%" mkdir "%NEO_VOICE_ENVS_ROOT%"

where nvidia-smi >nul 2>nul
if not errorlevel 1 (
    set "NVIDIA_DETECTED=1"
    if defined NEO_QWEN3_TTS_TORCH_INDEX_URL (
        set "TORCH_INDEX_URL=%NEO_QWEN3_TTS_TORCH_INDEX_URL%"
    ) else (
        set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124"
    )
) else if defined NEO_QWEN3_TTS_TORCH_INDEX_URL (
    set "TORCH_INDEX_URL=%NEO_QWEN3_TTS_TORCH_INDEX_URL%"
)

echo.
echo ============================================================
echo Neo Studio - Qwen3-TTS Worker Setup (Phase 4.5.8)
echo Project:      %PROJECT_ROOT%
echo Voice root:   %NEO_VOICE_RUNTIME_ROOT%
echo Worker env:   %VENV_DIR%
echo Model install: Admin ^> Models ^(Hugging Face cache^)
echo Python:       %PYTHON_CMD%
echo Torch index:  %TORCH_INDEX_URL%
echo ============================================================
echo.

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/5] Creating isolated Qwen3-TTS virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :failed
) else (
    echo [1/5] Qwen3-TTS virtual environment already exists.
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :failed

echo [2/5] Updating pip tooling...
python -m pip install --upgrade pip wheel setuptools
if errorlevel 1 goto :failed

echo [3/5] Installing PyTorch / torchaudio from the selected lane...
python -m pip install --upgrade torch torchaudio --index-url "%TORCH_INDEX_URL%"
if errorlevel 1 goto :failed

echo [4/5] Installing Qwen3-TTS worker requirements...
python -m pip install -r "%PROJECT_ROOT%\neo_integrations\qwen3_tts_adapter\requirements.txt"
if errorlevel 1 goto :failed

echo [5/5] Verifying worker imports...
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
set "NEO_QWEN3_TTS_NEO_ROOT=%PROJECT_ROOT%"
set "NEO_VOICE_RUNTIME_ROOT=%NEO_VOICE_RUNTIME_ROOT%"
set "NEO_QWEN3_TTS_MODEL_ROOT=%MODEL_DIR%"
python -c "import torch, qwen_tts, fastapi, uvicorn, soundfile, transformers, accelerate, huggingface_hub; from neo_integrations.qwen3_tts_adapter.app import app; print('Qwen3-TTS worker imports OK'); print('Torch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
if errorlevel 1 goto :failed

if "%NVIDIA_DETECTED%"=="1" (
    python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"
    if errorlevel 1 (
        echo [ERROR] NVIDIA was detected but this Qwen3-TTS environment cannot use CUDA.
        echo [HINT] Override the wheel lane before rerunning, for example:
        echo   set NEO_QWEN3_TTS_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
        goto :failed
    )
)

> "%VENV_DIR%\.neo_qwen3_tts_ready" echo qwen3_tts_runtime_ready

echo.
echo Qwen3-TTS worker environment is ready.
echo No model weights were downloaded by this setup script.
echo.
echo Normal user model installation:
echo   1. Start Neo Studio.
echo   2. Open Admin ^> Models.
echo   3. Install Qwen3-TTS 0.6B or 1.7B CustomVoice.
echo.
echo Neo installs supported Qwen repository snapshots through the Hugging Face cache.
echo Existing complete Neo_Runtime\voice\models\qwen3_tts snapshots remain compatible.
echo Managed Voice generation is local-only and never downloads missing weights.
echo.
echo Normal Voice runtime launcher:
echo   run_neo_voice_engine.bat
echo.
echo Developer-only Qwen diagnostics are kept under scripts\dev\qwen3_tts.
echo They are not part of the normal user setup path.
echo.
echo Base voice-clone and VoiceDesign models remain gated for later dedicated phases.
echo.
pause
exit /b 0

:try_python
set "CANDIDATE=%~1"
%CANDIDATE% -c "import sys; raise SystemExit(0 if (3,9) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=%CANDIDATE%"
exit /b 0

:failed
if defined VENV_DIR if exist "%VENV_DIR%\.neo_qwen3_tts_ready" del /q "%VENV_DIR%\.neo_qwen3_tts_ready" >nul 2>nul
echo.
echo [ERROR] Qwen3-TTS worker setup failed.
echo Neo Studio's main environment was not modified.
echo Runtime root: %NEO_VOICE_RUNTIME_ROOT%
pause
exit /b 1
