@echo off
REM ============================================================================
REM ADHD Engine + Frontend launcher (Windows)
REM
REM Double-click this file, or run from PowerShell / cmd.exe:
REM     .\start.bat
REM
REM Pass through any args to start.py:
REM     .\start.bat --engine-only
REM     .\start.bat --no-browser
REM ============================================================================

setlocal

REM Resolve the repo root from this batch file's location
set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"

REM Prefer the venv python if it exists
set "VENV_PY=%REPO_ROOT%.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" "%REPO_ROOT%start.py" %*
) else (
    echo [start.bat] no .venv found, using system python
    where py >nul 2>nul
    if %errorlevel% == 0 (
        py -3.12 "%REPO_ROOT%start.py" %*
    ) else (
        python "%REPO_ROOT%start.py" %*
    )
)

REM Keep the window open so the user can read any error messages
if errorlevel 1 (
    echo.
    echo [start.bat] start.py exited with code %errorlevel%
    pause
)

endlocal
