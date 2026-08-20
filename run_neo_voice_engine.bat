@echo off
setlocal

REM ============================================================
REM Neo Voice Engine - Gateway/Supervisor Launcher (VO-E5A)
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%cd%"
call "%PROJECT_ROOT%\neo_voice_engine\runtime_paths.bat" "%PROJECT_ROOT%"
if errorlevel 1 goto :failed

set "VENV_PY=%NEO_VOICE_ENVS_ROOT%\gateway\Scripts\python.exe"
set "LEGACY_VENV_PY=%PROJECT_ROOT%\.venv-voice-engine\Scripts\python.exe"
if not defined NEO_VOICE_ENGINE_HOST set "NEO_VOICE_ENGINE_HOST=127.0.0.1"
if not defined NEO_VOICE_ENGINE_PORT set "NEO_VOICE_ENGINE_PORT=8790"
set "NEO_VOICE_ENGINE_PROJECT_ROOT=%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"

if not exist "%VENV_PY%" (
    echo.
    echo [ERROR] External Neo Voice Engine environment was not found:
    echo   %VENV_PY%
    if exist "%LEGACY_VENV_PY%" (
        echo.
        echo A legacy root-level .venv-voice-engine was detected.
        echo Run setup_neo_voice_engine.bat once to rebuild it under Neo_Runtime\voice\envs\gateway.
    ) else (
        echo Run setup first:
        echo   setup_neo_voice_engine.bat
    )
    echo.
    pause
    exit /b 1
)

echo ========================================
echo  Neo Voice Engine - Gateway / Supervisor
echo ========================================
echo URL:     http://%NEO_VOICE_ENGINE_HOST%:%NEO_VOICE_ENGINE_PORT%
echo Health:  http://%NEO_VOICE_ENGINE_HOST%:%NEO_VOICE_ENGINE_PORT%/api/voice/health
echo Runtime: %NEO_VOICE_RUNTIME_ROOT%
echo Phase:   VO-E5A - external Voice runtime root; Chatterbox auto-starts on first executable job
echo.

set "UVICORN_FLAGS=--no-access-log --log-level warning"
if /I "%~1"=="--dev" set "UVICORN_FLAGS=--log-level info"

"%VENV_PY%" -m uvicorn neo_voice_engine.app:app --host "%NEO_VOICE_ENGINE_HOST%" --port "%NEO_VOICE_ENGINE_PORT%" %UVICORN_FLAGS%

if errorlevel 1 (
    echo.
    echo Neo Voice Engine stopped with an error.
)
pause
endlocal
exit /b 0

:failed
echo [ERROR] Voice runtime path resolution failed.
pause
endlocal
exit /b 1
