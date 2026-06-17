@echo off
REM ErysReview — Windows launcher.
REM Double-click to start the app. Use install.bat first to set things up.
setlocal
cd /d "%~dp0"

REM Locate conda (capture the FULL path so we can derive the conda root below)
set "CONDA_EXE="
for /f "delims=" %%i in ('where conda 2^>nul') do (
    if not defined CONDA_EXE set "CONDA_EXE=%%i"
)
if "%CONDA_EXE%"=="" (
    for %%P in (
        "%USERPROFILE%\miniforge3\Scripts\conda.exe"
        "%USERPROFILE%\miniconda3\Scripts\conda.exe"
        "%LOCALAPPDATA%\miniforge3\Scripts\conda.exe"
    ) do (
        if exist %%~P set "CONDA_EXE=%%~P"
    )
)

if "%CONDA_EXE%"=="" (
    echo [!] conda not found. Run install.bat first.
    pause
    exit /b 1
)

for %%I in ("%CONDA_EXE%") do set "CONDA_DIR=%%~dpI..\"
call "%CONDA_DIR%Scripts\activate.bat" erys
if errorlevel 1 (
    echo [!] Could not activate the erys env. Run install.bat first.
    pause
    exit /b 1
)

REM -u = unbuffered output so the user sees the loading progress live
REM     instead of a frozen blinking cursor while imports run (10-20 s
REM     the first time on Windows).
python -u -m erysreview
if errorlevel 1 (
    echo.
    echo App exited with an error. Press any key to close.
    pause >nul
)
endlocal
