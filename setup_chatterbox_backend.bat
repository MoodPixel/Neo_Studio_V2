@echo off
setlocal EnableExtensions

REM ============================================================
REM Neo Studio V2 - Chatterbox Voice Backend Setup (VO-E5B)
REM Model runtime lives outside the Neo Studio source tree.
REM NVIDIA hosts receive an explicit CUDA PyTorch wheel lane.
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%cd%"
set "PYTHON_CMD="
set "PYTORCH_VERSION=2.6.0"
set "TORCHAUDIO_VERSION=2.6.0"
set "DEFAULT_CUDA_VARIANT=cu124"
set "NVIDIA_DETECTED=0"
set "TORCH_VARIANT=cpu"
set "TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu"

call :try_python "py -3.11"
if not defined PYTHON_CMD call :try_python "py -3.10"
if not defined PYTHON_CMD call :try_python "python"
if not defined PYTHON_CMD call :try_python "python3"

if not defined PYTHON_CMD (
    echo.
    echo [ERROR] Python 3.10 or newer was not found.
    echo Chatterbox is officially tested on Python 3.11; Python 3.11 is preferred.
    pause
    exit /b 1
)

call "%PROJECT_ROOT%\neo_voice_engine\runtime_paths.bat" "%PROJECT_ROOT%"
if errorlevel 1 goto :failed

set "VENV_DIR=%NEO_VOICE_ENVS_ROOT%\chatterbox"
set "LEGACY_VENV=%PROJECT_ROOT%\.venv-chatterbox"
set "LEGACY_DATA=%PROJECT_ROOT%\neo_voice_engine_data"
set "BACKUP_DIR=%NEO_VOICE_LEGACY_BACKUPS_ROOT%"

if not exist "%NEO_VOICE_ENVS_ROOT%" mkdir "%NEO_VOICE_ENVS_ROOT%"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

where nvidia-smi >nul 2>nul
if errorlevel 1 goto :device_detection_done
set "NVIDIA_DETECTED=1"
if defined NEO_CHATTERBOX_CUDA_VARIANT (
    set "TORCH_VARIANT=%NEO_CHATTERBOX_CUDA_VARIANT%"
) else (
    set "TORCH_VARIANT=%DEFAULT_CUDA_VARIANT%"
)
call :validate_cuda_variant "%TORCH_VARIANT%"
if errorlevel 1 goto :failed
set "TORCH_INDEX_URL=https://download.pytorch.org/whl/%TORCH_VARIANT%"

:device_detection_done

echo.
echo ============================================================
echo Neo Studio V2 - Chatterbox Backend Setup (VO-E5B)
echo Project source: %PROJECT_ROOT%
echo Voice runtime: %NEO_VOICE_RUNTIME_ROOT%
echo Python:        %PYTHON_CMD%
echo Chatterbox:    %VENV_DIR%
if "%NVIDIA_DETECTED%"=="1" (
    echo GPU mode:      NVIDIA / %TORCH_VARIANT%
) else (
    echo GPU mode:      CPU ^(nvidia-smi not detected^)
)
echo Torch index:   %TORCH_INDEX_URL%
echo ============================================================
echo.

if "%NVIDIA_DETECTED%"=="1" (
    echo [INFO] NVIDIA GPU detected:
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>nul
    echo.
) else (
    echo [INFO] NVIDIA GPU was not detected. Installing the explicit CPU PyTorch lane.
    echo.
)

if exist "%LEGACY_VENV%\Scripts\python.exe" if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Legacy root-level .venv-chatterbox detected.
    echo [INFO] VO-E5A+ creates a fresh external venv instead of relocating it.
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/6] Creating external Chatterbox virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :failed
) else (
    echo [1/6] External Chatterbox virtual environment already exists.
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :failed

echo [2/6] Updating pip tooling...
python -m pip install --upgrade pip wheel "setuptools<82"
if errorlevel 1 goto :failed

echo [3/6] Ensuring PyTorch %PYTORCH_VERSION% / torchaudio %TORCHAUDIO_VERSION% from %TORCH_VARIANT%...
call :ensure_torch_runtime
if errorlevel 1 goto :failed

echo [4/6] Installing Chatterbox adapter requirements...
python -m pip install -r "%PROJECT_ROOT%\neo_integrations\chatterbox_adapter\requirements.txt"
if errorlevel 1 goto :failed

echo [5/6] Re-checking PyTorch after Chatterbox dependency resolution...
call :verify_torch_runtime
if errorlevel 1 (
    echo [WARN] Chatterbox dependency resolution changed or invalidated the selected PyTorch runtime.
    echo [INFO] Re-applying the explicit %TORCH_VARIANT% PyTorch lane...
    call :repair_torch_runtime
    if errorlevel 1 goto :failed
    call :verify_torch_runtime
    if errorlevel 1 goto :failed
)

echo [6/6] Verifying adapter imports and device readiness...
python -c "import chatterbox, torch, torchaudio, fastapi, uvicorn, perth; assert callable(getattr(perth, 'PerthImplicitWatermarker', None)), 'PerTh watermarker unavailable; verify setuptools<82'; print('Chatterbox backend dependencies OK'); print('Torch:', torch.__version__); print('Torch CUDA:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); print('PerTh watermarker: OK')"
if errorlevel 1 goto :failed

if "%NVIDIA_DETECTED%"=="1" (
    python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() and torch.version.cuda else 1)"
    if errorlevel 1 (
        echo.
        echo [ERROR] NVIDIA was detected, but the Chatterbox environment cannot use CUDA.
        echo [ERROR] Neo Voice Engine may request CUDA, so a CPU-only Torch build is unsafe on this host.
        echo [HINT] Default lane is cu124. For an older/newer compatible driver, set one of:
        echo        set NEO_CHATTERBOX_CUDA_VARIANT=cu118
        echo        set NEO_CHATTERBOX_CUDA_VARIANT=cu124
        echo        set NEO_CHATTERBOX_CUDA_VARIANT=cu126
        echo        then rerun setup_chatterbox_backend.bat.
        goto :failed
    )
)

> "%VENV_DIR%\.neo_chatterbox_ready" echo Neo Studio Chatterbox runtime verified - Phase 4.6
if errorlevel 1 goto :failed

call :archive_legacy_venv "%LEGACY_VENV%" "chatterbox-root-venv"
call :archive_legacy_data

echo.
echo ============================================================
echo Chatterbox worker environment is ready for Neo Voice Engine.
echo Runtime: %VENV_DIR%
if "%NVIDIA_DETECTED%"=="1" (
    echo PyTorch lane: %TORCH_VARIANT% ^(CUDA verified^)
) else (
    echo PyTorch lane: CPU
)
echo.
echo Normal path:
echo   1. Start Neo Studio and open Admin ^> Models.
echo   2. Install or verify Chatterbox Turbo / Chatterbox Multilingual V3 there.
echo   3. Run setup_neo_voice_engine.bat if needed, then run_neo_voice_engine.bat.
echo   4. Use Voice - Neo Voice Engine at http://127.0.0.1:8790.
echo.
echo Model weights are NOT downloaded by this setup script or by Voice Generate.
echo The gateway auto-starts Chatterbox on 127.0.0.1:8791 only after model admission.
echo Developer direct-worker diagnostics live under scripts\dev\chatterbox.
echo ============================================================
echo.
pause
exit /b 0

:ensure_torch_runtime
call :verify_torch_runtime >nul 2>nul
if not errorlevel 1 (
    echo [INFO] Existing PyTorch runtime already matches %TORCH_VARIANT% requirements.
    exit /b 0
)
call :repair_torch_runtime
exit /b %errorlevel%

:repair_torch_runtime
python -m pip uninstall -y torch torchaudio >nul 2>nul
python -m pip install "torch==%PYTORCH_VERSION%" "torchaudio==%TORCHAUDIO_VERSION%" --index-url "%TORCH_INDEX_URL%"
if errorlevel 1 exit /b 1
call :verify_torch_runtime
if errorlevel 1 (
    echo [ERROR] PyTorch installed, but the selected runtime lane failed verification.
    exit /b 1
)
exit /b 0

:verify_torch_runtime
if "%NVIDIA_DETECTED%"=="1" (
    python -c "import sys, torch, torchaudio; tv=torch.__version__.split('+',1)[0]; av=torchaudio.__version__.split('+',1)[0]; ok=(tv=='%PYTORCH_VERSION%' and av=='%TORCHAUDIO_VERSION%' and torch.cuda.is_available() and bool(torch.version.cuda)); sys.exit(0 if ok else 1)"
) else (
    python -c "import sys, torch, torchaudio; tv=torch.__version__.split('+',1)[0]; av=torchaudio.__version__.split('+',1)[0]; ok=(tv=='%PYTORCH_VERSION%' and av=='%TORCHAUDIO_VERSION%'); sys.exit(0 if ok else 1)"
)
exit /b %errorlevel%

:validate_cuda_variant
set "CUDA_VARIANT=%~1"
if /I "%CUDA_VARIANT%"=="cu118" exit /b 0
if /I "%CUDA_VARIANT%"=="cu124" exit /b 0
if /I "%CUDA_VARIANT%"=="cu126" exit /b 0
echo [ERROR] Unsupported NEO_CHATTERBOX_CUDA_VARIANT: %CUDA_VARIANT%
echo [ERROR] Supported values for Chatterbox/PyTorch 2.6 are cu118, cu124, or cu126.
exit /b 1

:archive_legacy_venv
set "OLD_ENV=%~1"
set "BACKUP_NAME=%~2"
if not exist "%OLD_ENV%" exit /b 0
set "BACKUP_TARGET=%BACKUP_DIR%\%BACKUP_NAME%"
if exist "%BACKUP_TARGET%" set "BACKUP_TARGET=%BACKUP_DIR%\%BACKUP_NAME%-%RANDOM%"
echo [MIGRATE] Archiving legacy root-level venv outside Neo Studio...
move "%OLD_ENV%" "%BACKUP_TARGET%" >nul
if errorlevel 1 (
    robocopy "%OLD_ENV%" "%BACKUP_TARGET%" /E /MOVE /R:1 /W:1 >nul
    if errorlevel 8 (
        echo [WARN] Could not archive %OLD_ENV% automatically. The new external runtime is still valid.
    ) else (
        echo [MIGRATE] Legacy environment archived across volumes at %BACKUP_TARGET%
    )
) else (
    echo [MIGRATE] Legacy environment archived at %BACKUP_TARGET%
)
exit /b 0

:archive_legacy_data
if not exist "%LEGACY_DATA%" exit /b 0
set "DATA_BACKUP=%BACKUP_DIR%\neo_voice_engine_data-root-vo-e5a"
if exist "%DATA_BACKUP%" set "DATA_BACKUP=%BACKUP_DIR%\neo_voice_engine_data-root-vo-e5a-%RANDOM%"
echo [MIGRATE] Archiving legacy neo_voice_engine_data outside Neo Studio...
move "%LEGACY_DATA%" "%DATA_BACKUP%" >nul
if errorlevel 1 (
    robocopy "%LEGACY_DATA%" "%DATA_BACKUP%" /E /MOVE /R:1 /W:1 >nul
    if errorlevel 8 (
        echo [WARN] Could not archive legacy neo_voice_engine_data automatically.
    ) else (
        echo [MIGRATE] Legacy gateway data archived across volumes at %DATA_BACKUP%
    )
) else (
    echo [MIGRATE] Legacy gateway data archived at %DATA_BACKUP%
)
exit /b 0

:failed
echo.
echo [ERROR] Chatterbox backend setup failed.
echo Neo Studio's main .venv was not modified.
echo Runtime root: %NEO_VOICE_RUNTIME_ROOT%
pause
exit /b 1

:try_python
set "CANDIDATE=%~1"
%CANDIDATE% -c "import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=%CANDIDATE%"
exit /b 0
