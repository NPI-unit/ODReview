@echo off
REM NKReview - Windows launcher.
REM Double-click to start the app. Use install.bat first to set things up.
REM Keep this file CRLF + pure ASCII, same as install.bat.
setlocal
cd /d "%~dp0"

REM Locate conda. Capture the FULL path from `where` (not the literal "conda"):
REM we later derive the conda root via %%~dpI, which only works on a real path.
set "CONDA_EXE="
for /f "delims=" %%P in ('where conda 2^>nul') do if not defined CONDA_EXE set "CONDA_EXE=%%P"
if "%CONDA_EXE%"=="" (
    REM Keep this list identical to :find_conda in install.bat. A conda the
    REM installer accepts but the launcher does not find means a successful
    REM install that then refuses to start.
    for %%P in (
        "%USERPROFILE%\miniforge3\Scripts\conda.exe"
        "%USERPROFILE%\miniconda3\Scripts\conda.exe"
        "%USERPROFILE%\anaconda3\Scripts\conda.exe"
        "%LOCALAPPDATA%\miniforge3\Scripts\conda.exe"
        "C:\ProgramData\miniforge3\Scripts\conda.exe"
    ) do (
        if exist "%%~P" set "CONDA_EXE=%%~P"
    )
)

if "%CONDA_EXE%"=="" (
    echo [!] conda not found. Run install.bat first.
    pause
    exit /b 1
)

for %%I in ("%CONDA_EXE%") do set "CONDA_DIR=%%~dpI..\"
call "%CONDA_DIR%Scripts\activate.bat" nkreview
if errorlevel 1 (
    echo [!] Could not activate the nkreview env. Run install.bat first.
    pause
    exit /b 1
)

REM conda activate succeeds even when the env is a half-deleted leftover
REM directory, so confirm the interpreter we are about to use is really the
REM one from the nkreview env before blaming the app for the import errors.
python -c "import sys, os; sys.exit(0 if os.path.basename(os.path.dirname(sys.executable)).lower()=='nkreview' else 1)" >nul 2>&1
if errorlevel 1 (
    echo [!] The nkreview env looks incomplete. Run install.bat again.
    pause
    exit /b 1
)

REM -u = unbuffered output so the user sees the loading progress live
REM     instead of a frozen blinking cursor while imports run (10-20 s
REM     the first time on Windows).
python -u -m nkreview
if errorlevel 1 (
    echo.
    echo App exited with an error. Press any key to close.
    pause >nul
)
endlocal
