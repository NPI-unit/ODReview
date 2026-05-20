@echo off
REM ErysReview — Windows launcher.
REM Double-click to start the app. Use install.bat first to set things up.
setlocal
cd /d "%~dp0"

REM Locate conda
set "CONDA_EXE="
where conda >nul 2>&1
if not errorlevel 1 set "CONDA_EXE=conda"
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

python -m erysreview
if errorlevel 1 (
    echo.
    echo App exited with an error. Press any key to close.
    pause >nul
)
endlocal
