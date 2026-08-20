@echo off
setlocal

REM ============================================================
REM Neo Voice Engine - Gateway/Supervisor Setup (VO-E5A)
REM Gateway runtime lives outside Neo Studio source.
REM Lightweight only: no model/Torch stacks are installed here.
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%cd%"
set "PYTHON_CMD="

call :try_python "py -3.11"
if not defined PYTHON_CMD call :try_python "py -3.10"
if not defined PYTHON_CMD call :try_python "python"
if not defined PYTHON_CMD call :try_python "python3"

if not defined PYTHON_CMD (
    echo.
    echo [ERROR] Python 3.10 or newer was not found.
    pause
    exit /b 1
)

call "%PROJECT_ROOT%\neo_voice_engine\runtime_paths.bat" "%PROJECT_ROOT%"
if errorlevel 1 goto :failed

set "VENV_DIR=%NEO_VOICE_ENVS_ROOT%\gateway"
set "LEGACY_VENV=%PROJECT_ROOT%\.venv-voice-engine"
set "LEGACY_DATA=%PROJECT_ROOT%\neo_voice_engine_data"
set "BACKUP_DIR=%NEO_VOICE_LEGACY_BACKUPS_ROOT%"

if not exist "%NEO_VOICE_ENVS_ROOT%" mkdir "%NEO_VOICE_ENVS_ROOT%"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo.
echo ============================================================
echo Neo Voice Engine - Gateway Setup
echo Project source: %PROJECT_ROOT%
echo Voice runtime: %NEO_VOICE_RUNTIME_ROOT%
echo Python:        %PYTHON_CMD%
echo Gateway env:   %VENV_DIR%
echo ============================================================
echo.

if exist "%LEGACY_VENV%\Scripts\python.exe" if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Legacy root-level .venv-voice-engine detected.
    echo [INFO] VO-E5A will rebuild it externally instead of relocating a Windows venv.
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/3] Creating external Voice Engine virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :failed
) else (
    echo [1/3] External Voice Engine virtual environment already exists.
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :failed

echo [2/3] Installing lightweight gateway requirements...
python -m pip install --upgrade pip wheel setuptools
if errorlevel 1 goto :failed
python -m pip install -r "%PROJECT_ROOT%\neo_voice_engine\requirements.txt"
if errorlevel 1 goto :failed

echo [3/3] Verifying gateway imports and runtime-root resolution...
set "NEO_VOICE_ENGINE_PROJECT_ROOT=%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
python -c "import os; from pathlib import Path; from neo_voice_engine.app import app; from neo_voice_engine import PROTOCOL_ID, ENGINE_PHASE; from neo_voice_engine.config import GatewayConfig; c=GatewayConfig.from_env(); assert c.runtime_root.resolve() == Path(os.environ['NEO_VOICE_RUNTIME_ROOT']).resolve(); print('Neo Voice Engine gateway OK:', PROTOCOL_ID, ENGINE_PHASE); print('Voice runtime:', c.runtime_root)"
if errorlevel 1 goto :failed

call :archive_legacy_venv "%LEGACY_VENV%" "gateway-root-venv"
call :archive_legacy_data

echo.
echo ============================================================
echo Neo Voice Engine gateway setup complete.
echo Gateway runtime: %VENV_DIR%
echo Voice root:      %NEO_VOICE_RUNTIME_ROOT%
echo.
echo No model-specific packages were installed in the gateway environment.
echo Chatterbox resolves from %NEO_VOICE_ENVS_ROOT%\chatterbox and is supervised on demand.
echo.
echo Start with:
echo   run_neo_voice_engine.bat
echo ============================================================
echo.
pause
exit /b 0

:archive_legacy_venv
set "OLD_ENV=%~1"
set "BACKUP_NAME=%~2"
if not exist "%OLD_ENV%" exit /b 0
set "BACKUP_TARGET=%BACKUP_DIR%\%BACKUP_NAME%"
if exist "%BACKUP_TARGET%" set "BACKUP_TARGET=%BACKUP_DIR%\%BACKUP_NAME%-%RANDOM%"
echo [MIGRATE] Archiving legacy root-level gateway venv outside Neo Studio...
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
echo [ERROR] Neo Voice Engine gateway setup failed.
echo Neo Studio's main .venv and model worker environments were not modified.
echo Runtime root: %NEO_VOICE_RUNTIME_ROOT%
pause
exit /b 1

:try_python
set "CANDIDATE=%~1"
%CANDIDATE% -c "import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=%CANDIDATE%"
exit /b 0
