@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM Neo Studio V2 - Windows Virtual Environment Setup
REM Run this from the Neo Studio V2 project root.
REM ============================================================

title Neo Studio V2 - Setup Venv

set "PROJECT_ROOT=%cd%"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "PYTHON_CMD="

call :try_python "py -3.10"
if not defined PYTHON_CMD call :try_python "python"
if not defined PYTHON_CMD call :try_python "python3"

if not defined PYTHON_CMD (
    echo.
    echo [ERROR] Python 3.10 or newer was not found.
    echo Install Python 3.10, then run this setup again.
    echo.
    echo Recommended: https://www.python.org/downloads/release/python-310/
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Neo Studio V2 - Venv Setup
echo Project: %PROJECT_ROOT%
echo Python:  %PYTHON_CMD%
echo Venv:    %VENV_DIR%
echo ============================================================
echo.

if not exist "%VENV_DIR%" (
    echo [1/5] Creating virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Virtual environment already exists. Skipping create.
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment Python was not found at:
    echo   %VENV_DIR%\Scripts\python.exe
    echo Delete the .venv folder and run setup again.
    pause
    exit /b 1
)

echo [2/5] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

echo [3/5] Upgrading pip/setuptools/wheel...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip tooling.
    pause
    exit /b 1
)

echo [4/5] Installing core project requirements...
if not exist "%PROJECT_ROOT%\requirements.txt" (
    echo [ERROR] requirements.txt was not found in the project root.
    pause
    exit /b 1
)
python -m pip install -r "%PROJECT_ROOT%\requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.txt.
    pause
    exit /b 1
)

echo [5/5] Installing memory / semantic backend requirements...
if not exist "%PROJECT_ROOT%\requirements-memory.txt" (
    echo [ERROR] requirements-memory.txt was not found in the project root.
    pause
    exit /b 1
)
python -m pip install -r "%PROJECT_ROOT%\requirements-memory.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install requirements-memory.txt.
    echo.
    echo PyTorch and semantic-memory packages can be large and platform-specific.
    echo Install the matching PyTorch build for your system if needed, then rerun setup.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Setup complete.
echo.
echo To start Neo Studio V2, run:
echo   run_neo_studio.bat
echo.
echo Then open:
echo   http://127.0.0.1:7860
echo ============================================================
echo.
pause
exit /b 0

:try_python
set "CANDIDATE=%~1"
%CANDIDATE% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=%CANDIDATE%"
)
exit /b 0
