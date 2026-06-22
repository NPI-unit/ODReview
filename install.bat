@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  ErysReview — Windows installer
REM
REM  Double-click this file from inside the ErysReview folder.
REM  It checks Miniforge / conda, creates an "erys" env, installs deps,
REM  drops a desktop shortcut, and prints what to do next.
REM ─────────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================
echo  ErysReview installer (Windows)
echo ============================================
echo.

REM ─── 1. Locate conda ───────────────────────────────────────────────────
REM Capture the FULL path from `where` (not the literal "conda"): the conda
REM root is derived later via %%~dpI, which only works on a real path.
set "CONDA_EXE="
for /f "delims=" %%P in ('where conda 2^>nul') do if not defined CONDA_EXE set "CONDA_EXE=%%P"
if defined CONDA_EXE goto :have_conda

REM Common Miniforge install paths
for %%P in (
    "%USERPROFILE%\miniforge3\Scripts\conda.exe"
    "%USERPROFILE%\miniconda3\Scripts\conda.exe"
    "%USERPROFILE%\anaconda3\Scripts\conda.exe"
    "%LOCALAPPDATA%\miniforge3\Scripts\conda.exe"
    "C:\ProgramData\miniforge3\Scripts\conda.exe"
) do (
    if exist %%~P (
        set "CONDA_EXE=%%~P"
        goto :have_conda
    )
)

echo [!] Miniforge / conda was not found.
echo.
echo Install Miniforge first:
echo   https://github.com/conda-forge/miniforge#download
echo.
echo Pick "Miniforge3-Windows-x86_64.exe", run it, accept defaults.
echo Then double-click install.bat again.
echo.
pause
exit /b 1

:have_conda
echo [1/4] Found conda: %CONDA_EXE%

REM Resolve the conda root so we can call activate later
for %%I in ("%CONDA_EXE%") do set "CONDA_DIR=%%~dpI..\"
set "CONDA_ACTIVATE=%CONDA_DIR%Scripts\activate.bat"

REM ─── 2. Create or update the env ───────────────────────────────────────
echo [2/4] Creating / updating conda env "erys" (Python 3.12) ...
call "%CONDA_EXE%" env list | findstr /B "erys " >nul 2>&1
if errorlevel 1 (
    call "%CONDA_EXE%" create -n erys python=3.12 -y
    if errorlevel 1 (
        echo [!] conda create failed.
        pause
        exit /b 1
    )
) else (
    echo     Env "erys" already exists, reusing.
)

REM ─── 3. Install / upgrade dependencies ─────────────────────────────────
echo [3/4] Installing dependencies from requirements.txt ...
call "%CONDA_ACTIVATE%" erys
if errorlevel 1 (
    echo [!] Failed to activate erys env.
    pause
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] pip install failed. Check your internet / proxy settings.
    pause
    exit /b 1
)

REM ─── 4. Generate a Desktop shortcut ────────────────────────────────────
echo [4/4] Creating Desktop shortcut ...
set "SHORTCUT=%USERPROFILE%\Desktop\ErysReview.lnk"
set "ICON=%CD%\assets\icon.ico"
set "TARGET=%CD%\run.bat"
set "VBSCRIPT=%TEMP%\make_erys_shortcut.vbs"

>  "%VBSCRIPT%" echo Set ws = WScript.CreateObject("WScript.Shell")
>> "%VBSCRIPT%" echo Set sc = ws.CreateShortcut("%SHORTCUT%")
>> "%VBSCRIPT%" echo sc.TargetPath = "%TARGET%"
>> "%VBSCRIPT%" echo sc.WorkingDirectory = "%CD%"
>> "%VBSCRIPT%" echo sc.IconLocation = "%ICON%"
>> "%VBSCRIPT%" echo sc.Description = "ErysReview"
>> "%VBSCRIPT%" echo sc.Save
cscript //nologo "%VBSCRIPT%"
del "%VBSCRIPT%"

echo.
echo ============================================
echo  Done. ErysReview is installed.
echo ============================================
echo.
echo You now have two ways to start the app:
echo   1. Double-click "ErysReview" on your Desktop
echo   2. Double-click run.bat inside this folder
echo.
pause
endlocal
