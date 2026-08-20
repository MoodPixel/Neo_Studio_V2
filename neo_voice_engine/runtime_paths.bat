@echo off
REM ============================================================
REM Neo Voice Engine - external runtime root resolver (VO-E5A)
REM
REM Call from a launcher after cd /d to the Neo Studio project root:
REM   call "%PROJECT_ROOT%\neo_voice_engine\runtime_paths.bat" "%PROJECT_ROOT%"
REM
REM Precedence:
REM   1. NEO_VOICE_RUNTIME_ROOT
REM   2. legacy NEO_VOICE_ENGINE_DATA
REM   3. NEO_RUNTIME_ROOT\voice
REM   4. sibling ..\Neo_Runtime\voice
REM ============================================================

if "%~1"=="" (
    echo [ERROR] runtime_paths.bat requires the Neo Studio project root.
    exit /b 2
)

set "_NEO_VOICE_PROJECT_ROOT=%~f1"

if defined NEO_RUNTIME_ROOT (
    for %%I in ("%NEO_RUNTIME_ROOT%") do set "NEO_RUNTIME_ROOT=%%~fI"
)

if defined NEO_VOICE_RUNTIME_ROOT goto :voice_root_ready
if defined NEO_VOICE_ENGINE_DATA (
    set "NEO_VOICE_RUNTIME_ROOT=%NEO_VOICE_ENGINE_DATA%"
    goto :voice_root_ready
)
if defined NEO_RUNTIME_ROOT (
    set "NEO_VOICE_RUNTIME_ROOT=%NEO_RUNTIME_ROOT%\voice"
    goto :voice_root_ready
)

for %%I in ("%_NEO_VOICE_PROJECT_ROOT%\..\Neo_Runtime") do set "NEO_RUNTIME_ROOT=%%~fI"
set "NEO_VOICE_RUNTIME_ROOT=%NEO_RUNTIME_ROOT%\voice"

:voice_root_ready
for %%I in ("%NEO_VOICE_RUNTIME_ROOT%") do set "NEO_VOICE_RUNTIME_ROOT=%%~fI"
if not defined NEO_RUNTIME_ROOT for %%I in ("%NEO_VOICE_RUNTIME_ROOT%\..") do set "NEO_RUNTIME_ROOT=%%~fI"
if not defined NEO_VOICE_ENGINE_DATA set "NEO_VOICE_ENGINE_DATA=%NEO_VOICE_RUNTIME_ROOT%"

set "NEO_VOICE_ENVS_ROOT=%NEO_VOICE_RUNTIME_ROOT%\envs"
set "NEO_VOICE_MODELS_ROOT=%NEO_VOICE_RUNTIME_ROOT%\models"
set "NEO_VOICE_CACHE_ROOT=%NEO_VOICE_RUNTIME_ROOT%\cache"
set "NEO_VOICE_TEMP_ROOT=%NEO_VOICE_RUNTIME_ROOT%\temp"
set "NEO_VOICE_LOGS_ROOT=%NEO_VOICE_RUNTIME_ROOT%\logs"
set "NEO_VOICE_STATE_ROOT=%NEO_VOICE_RUNTIME_ROOT%\state"
set "NEO_VOICE_OUTPUTS_ROOT=%NEO_VOICE_RUNTIME_ROOT%\outputs"
set "NEO_VOICE_LEGACY_BACKUPS_ROOT=%NEO_VOICE_RUNTIME_ROOT%\legacy_backups"

set "_NEO_VOICE_PROJECT_ROOT="
exit /b 0
