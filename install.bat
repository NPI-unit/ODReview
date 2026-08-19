@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  ErysReview — Windows installer
REM
REM  Double-click this file from inside the ErysReview folder.
REM  It ensures Miniforge/conda is present (offering to install it for you),
REM  builds a CLEAN "erys" conda env with the full scientific stack from
REM  conda-forge, drops a desktop shortcut, and prints what to do next.
REM
REM  Why conda-forge instead of pip:
REM    Mixing conda's MKL-linked numpy with a pip-installed torch loads
REM    libiomp5md.dll TWICE in one process. On Windows that deadlocks Cellpose
REM    the first time it runs (OMP Error #15) — the app appears frozen forever.
REM    Installing torch + numpy + cellpose in ONE conda-forge solve gives a
REM    single OpenMP runtime and avoids the hang entirely.
REM
REM  macOS is installed differently on purpose (see install.command) — it uses
REM  a Homebrew venv to dodge a Tk segfault on macOS 26. Do not "unify" them.
REM ─────────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================
echo  ErysReview installer (Windows)
echo ============================================
echo.

REM ─── 1. Locate conda — offer to install Miniforge if missing ────────────
call :find_conda
if defined CONDA_EXE goto :have_conda

echo [!] Miniforge / conda was not found.
echo.
set /p "ANS=Download and install Miniforge now? [Y/N] "
if /I not "!ANS!"=="Y" (
    echo.
    echo No problem. Install Miniforge yourself from:
    echo   https://github.com/conda-forge/miniforge#download
    echo Pick "Miniforge3-Windows-x86_64.exe", run it, accept defaults,
    echo then double-click install.bat again.
    echo.
    pause
    exit /b 1
)

call :install_miniforge
if errorlevel 1 (
    echo [!] Miniforge installation failed. Check your internet / proxy and retry.
    pause
    exit /b 1
)
call :find_conda
if not defined CONDA_EXE (
    echo [!] Miniforge installed, but conda was still not found.
    echo     Close this window, open a NEW terminal, and re-run install.bat.
    pause
    exit /b 1
)

:have_conda
echo [1/4] Found conda: %CONDA_EXE%

REM Resolve the conda root so we can activate later
for %%I in ("%CONDA_EXE%") do set "CONDA_DIR=%%~dpI..\"
set "CONDA_ACTIVATE=%CONDA_DIR%Scripts\activate.bat"

REM ─── 2. (Re)create the "erys" env CLEANLY from conda-forge ──────────────
REM  We always rebuild the env: an env left over from the old pip-based
REM  installer still has the duplicate-OpenMP problem, so reusing it would
REM  keep the hang. A clean conda-forge solve is the whole fix.
echo [2/4] Building a clean conda-forge env "erys" (Python 3.12) ...
call "%CONDA_EXE%" env list | findstr /B "erys " >nul 2>&1
if not errorlevel 1 (
    echo     Removing the existing "erys" env so the OpenMP stack is clean ...
    call "%CONDA_EXE%" env remove -n erys -y
)

REM One channel, one solve -> one OpenMP runtime. matplotlib-base skips Qt.
REM (Default solve is the CPU torch build; NVIDIA users: see README for the GPU variant.)
call "%CONDA_EXE%" create -n erys -c conda-forge -y python=3.12 "cellpose>=4.1.1,<5" pytorch numpy scipy scikit-image matplotlib-base pandas pillow tifffile openpyxl
if errorlevel 1 (
    echo [!] conda create failed. Check your internet / proxy settings and retry.
    pause
    exit /b 1
)

REM ─── 3. Sanity-check the stack actually imports (no OMP deadlock) ───────
echo [3/4] Verifying torch + cellpose import cleanly ...
call "%CONDA_ACTIVATE%" erys
if errorlevel 1 (
    echo [!] Failed to activate the erys env.
    pause
    exit /b 1
)
python -c "import numpy, torch, cellpose; print('   numpy', numpy.__version__, '| torch', torch.__version__, '| cellpose', getattr(cellpose,'version','?'))"
if errorlevel 1 (
    echo [!] The scientific stack did not import cleanly. Copy the lines above and report them.
    pause
    exit /b 1
)

REM ─── 3b. GPU acceleration (NVIDIA only) ────────────────────────────────
REM The conda-forge solve above installs the CPU build of PyTorch, so on a
REM machine with an NVIDIA GPU Cellpose crawls (minutes per image) while the
REM GPU sits idle — audit item CP-3. Detection is taken from origin/main
REM (commit 1a4968c), the INSTALL is not: that used
REM   pip install --force-reinstall --index-url .../cu124 torch torchvision
REM which puts a pip torch next to conda's MKL numpy. That is exactly the
REM two-libiomp5md.dll combination CP-1 diagnosed as the OMP Error #15 hang.
REM Staying inside one conda-forge solve keeps a single OpenMP runtime.
REM NOT yet verified on a Windows machine with an NVIDIA GPU.
echo [3b/4] Checking for an NVIDIA GPU ...
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo     No NVIDIA GPU detected - using CPU PyTorch.
) else (
    python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
    if errorlevel 1 (
        echo     NVIDIA GPU found - installing the CUDA build from conda-forge ^(one-time, large download^) ...
        call "%CONDA_EXE%" install -n erys -c conda-forge -y "pytorch-gpu"
        if errorlevel 1 (
            echo     [!] CUDA PyTorch install failed - keeping CPU PyTorch ^(app still works, just slower^).
        )
    ) else (
        echo     CUDA PyTorch already active.
    )
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
exit /b 0


REM ═════════════════════════════════════════════════════════════════════════
REM  Subroutines
REM ═════════════════════════════════════════════════════════════════════════

:find_conda
REM Sets CONDA_EXE (FULL path) if conda is on PATH or in a known location.
set "CONDA_EXE="
for /f "delims=" %%i in ('where conda 2^>nul') do (
    if not defined CONDA_EXE set "CONDA_EXE=%%i"
)
if defined CONDA_EXE goto :eof
for %%P in (
    "%USERPROFILE%\miniforge3\Scripts\conda.exe"
    "%USERPROFILE%\miniconda3\Scripts\conda.exe"
    "%USERPROFILE%\anaconda3\Scripts\conda.exe"
    "%LOCALAPPDATA%\miniforge3\Scripts\conda.exe"
    "C:\ProgramData\miniforge3\Scripts\conda.exe"
) do (
    if exist %%~P set "CONDA_EXE=%%~P"
)
goto :eof

:install_miniforge
REM Downloads and silently installs Miniforge to %USERPROFILE%\miniforge3.
REM Returns errorlevel 0 on success, 1 on failure.
set "MFURL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"
set "MFEXE=%TEMP%\Miniforge3-Windows-x86_64.exe"
echo     Downloading Miniforge ...
curl -L -f -o "%MFEXE%" "%MFURL%"
if errorlevel 1 (
    echo     curl unavailable or failed; trying PowerShell ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '%MFURL%' -OutFile '%MFEXE%' } catch { exit 1 }"
    if errorlevel 1 goto :install_miniforge_fail
)
echo     Installing Miniforge silently to %USERPROFILE%\miniforge3 ...
REM NSIS silent install. Note: /D= must be last and unquoted (no spaces in the path).
"%MFEXE%" /InstallationType=JustMe /RegisterPython=0 /S /D=%USERPROFILE%\miniforge3
if errorlevel 1 goto :install_miniforge_fail
del "%MFEXE%" >nul 2>&1
exit /b 0
:install_miniforge_fail
del "%MFEXE%" >nul 2>&1
exit /b 1
