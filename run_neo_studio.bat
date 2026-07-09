@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM Neo Studio V2 - Windows Launcher
REM Double-click this file from the Neo Studio V2 project root.
REM ============================================================

cd /d "%~dp0"

set "APP_NAME=Neo Studio V2"
set "VENV_PY=.venv\Scripts\python.exe"
set "ORIGINAL_ARGS=%*"

if not defined NEO_HOST set "NEO_HOST=127.0.0.1"
if not defined NEO_PORT set "NEO_PORT=7860"
if not defined NEO_BACKEND_BASE_URL set "NEO_BACKEND_BASE_URL=http://localhost:5001"

call :parse_args %*
set "NEO_URL=http://%NEO_HOST%:%NEO_PORT%"

if not exist "%VENV_PY%" (
    echo.
    echo [ERROR] Neo Studio V2 virtual environment was not found.
    echo Expected:
    echo   %cd%\.venv\Scripts\python.exe
    echo.
    echo Run setup first:
    echo   setup_neo_studio_venv.bat
    echo.
    pause
    exit /b 1
)

echo ========================================
echo  %APP_NAME%
echo ========================================
echo.
echo Checking environment...
echo Starting %APP_NAME% at %NEO_URL%
echo Backend base URL: %NEO_BACKEND_BASE_URL%
echo Runtime data: neo_data\
echo Console log: neo_data\logs\neo_console.log
echo Server log: neo_data\logs\neo_server.log
echo Error log: neo_data\logs\neo_error.log
echo Generation log: neo_data\logs\neo_generation.log
echo.
set "NEO_LAUNCHER_PRINTED=1"

REM Open browser shortly after the backend starts.
start "%APP_NAME% Browser Opener" cmd /c "timeout /t 3 /nobreak >nul && start "" "%NEO_URL%""

REM Start the app. Keep this window open while Neo Studio V2 is running.
"%VENV_PY%" -m neo_app.main %ORIGINAL_ARGS%

if errorlevel 1 (
    echo.
    echo Neo Studio V2 failed to start.
    echo Check that the virtual environment is installed and requirements are installed.
    echo.
)

pause
endlocal
exit /b 0

:parse_args
if "%~1"=="" exit /b 0
if /I "%~1"=="--host" (
    if not "%~2"=="" set "NEO_HOST=%~2"
    shift
    shift
    goto :parse_args
)
if /I "%~1"=="--port" (
    if not "%~2"=="" set "NEO_PORT=%~2"
    shift
    shift
    goto :parse_args
)
shift
goto :parse_args
