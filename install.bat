@echo off
REM -------------------------------------------------------------------------
REM  NKReview - Windows installer
REM
REM  Double-click this file from inside the NKReview folder.
REM  It ensures Miniforge/conda is present (offering to install it for you),
REM  builds a CLEAN "nkreview" conda env with the full scientific stack from
REM  conda-forge, drops a desktop shortcut, and prints what to do next.
REM
REM  Why conda-forge instead of pip:
REM    Mixing conda's MKL-linked numpy with a pip-installed torch loads
REM    libiomp5md.dll TWICE in one process. On Windows that deadlocks Cellpose
REM    the first time it runs (OMP Error #15) - the app appears frozen forever.
REM    Installing torch + numpy + cellpose in ONE conda-forge solve gives a
REM    single OpenMP runtime and avoids the hang entirely.
REM
REM  macOS is installed differently on purpose (see install.command) - it uses
REM  a Homebrew venv to dodge a Tk segfault on macOS 26. Do not "unify" them.
REM
REM  Batch-file house rules (learned the hard way, please keep them):
REM    * CRLF line endings and pure ASCII. LF-only .bat files trip cmd.exe's
REM      label scanner ("batch label not found") on some Windows builds.
REM    * NO "EnableDelayedExpansion". With it on, cmd eats the "!" in every
REM      "[!]" error marker, so they print as an empty "[]". Nothing in here
REM      needs delayed expansion.
REM -------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo  NKReview installer (Windows)
echo ============================================
echo.

REM --- 1. Locate conda - offer to install Miniforge if missing -------------
call :find_conda
if defined CONDA_EXE goto :have_conda

echo [!] Miniforge / conda was not found.
echo.
set "ANS="
set /p "ANS=Download and install Miniforge now? [Y/N] "
if /I not "%ANS%"=="Y" (
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
if not exist "%CONDA_ACTIVATE%" (
    echo [!] Found conda at "%CONDA_EXE%" but no activate.bat next to it:
    echo         %CONDA_ACTIVATE%
    echo     That conda install looks broken. Reinstall Miniforge and retry.
    pause
    exit /b 1
)

REM --- 2. (Re)create the "nkreview" env CLEANLY from conda-forge ---------------
REM  We always rebuild the env: an env left over from the old pip-based
REM  installer still has the duplicate-OpenMP problem, so reusing it would
REM  keep the hang. A clean conda-forge solve is the whole fix.
echo [2/4] Building a clean conda-forge env "nkreview" (Python 3.12) ...

REM  "conda env list" only shows REGISTERED envs, so it is not enough on its
REM  own. On Windows a "conda env remove" that hits a locked DLL (antivirus,
REM  an Explorer preview, a still-running python) wipes conda-meta and then
REM  gives up, leaving the whole tree on disk as an env conda no longer knows
REM  about. "conda create" then quietly installs ON TOP of that tree - stale
REM  pip torch and a second libiomp5md.dll included - and the OMP Error #15
REM  hang walks straight back in. So: unregister it if conda knows it, then
REM  delete the prefix directory outright, then verify it is really gone.
for /f "delims=" %%B in ('"%CONDA_EXE%" info --base 2^>nul') do set "CONDA_BASE=%%B"
if not defined CONDA_BASE set "CONDA_BASE=%CONDA_DIR:~0,-1%"
set "NK_PREFIX=%CONDA_BASE%\envs\nkreview"

"%CONDA_EXE%" env list | findstr /B /C:"nkreview " >nul 2>&1
if not errorlevel 1 (
    echo     Removing the registered "nkreview" env so the OpenMP stack is clean ...
    call "%CONDA_EXE%" env remove -n nkreview -y
)
if exist "%NK_PREFIX%\" (
    echo     Deleting leftover files in "%NK_PREFIX%" ...
    rmdir /s /q "%NK_PREFIX%" >nul 2>&1
)
if exist "%NK_PREFIX%\" (
    echo [!] Could not delete the old env directory:
    echo         %NK_PREFIX%
    echo     Something still has a file in there open. Close NKReview, any
    echo     Python / Anaconda Prompt window, and your antivirus scan ^(a
    echo     reboot settles all three^), then run install.bat again.
    echo     Installing on top of it would bring back the Cellpose freeze.
    pause
    exit /b 1
)

REM One channel, one solve -> one OpenMP runtime. matplotlib-base skips Qt.
REM --override-channels + --strict-channel-priority matter as much as the
REM "-c conda-forge" does: on a machine that already has Anaconda (we look
REM for anaconda3 in :find_conda, so this is not hypothetical) the "defaults"
REM channel is configured too, and a mixed solve happily pulls Intel's
REM intel-openmp in next to conda-forge's llvm-openmp. Two OpenMP runtimes
REM again. Pinning the solve to conda-forge alone is what keeps it at one.
REM (Default solve is the CPU torch build; NVIDIA users: see README for the GPU variant.)
call "%CONDA_EXE%" create -n nkreview --override-channels -c conda-forge --strict-channel-priority -y python=3.12 "cellpose>=4.1.1,<5" pytorch numpy scipy scikit-image matplotlib-base pandas pillow tifffile openpyxl
if errorlevel 1 (
    echo [!] conda create failed. Check your internet / proxy settings and retry.
    pause
    exit /b 1
)

REM --- 3. Sanity-check the stack actually imports (no OMP deadlock) --------
echo [3/4] Verifying torch + cellpose import cleanly ...
call "%CONDA_ACTIVATE%" nkreview
if errorlevel 1 (
    echo [!] Failed to activate the nkreview env.
    pause
    exit /b 1
)
REM tkinter is in that import list on purpose: the whole UI is Tk, so a
REM python built without it fails at launch, not here where we can say so.
python -c "import numpy, torch, cellpose, tkinter; print('   numpy', numpy.__version__, '| torch', torch.__version__, '| cellpose', getattr(cellpose,'version','?'), '| tk', tkinter.TkVersion)"
if errorlevel 1 (
    echo [!] The scientific stack did not import cleanly. Copy the lines above and report them.
    pause
    exit /b 1
)

REM --- 3b. GPU acceleration (NVIDIA only) ----------------------------------
REM The conda-forge solve above installs the CPU build of PyTorch, so on a
REM machine with an NVIDIA GPU Cellpose crawls (minutes per image) while the
REM GPU sits idle - audit item CP-3. Detection is taken from origin/main
REM (commit 1a4968c), the INSTALL is not: that used
REM   pip install --force-reinstall --index-url .../cu124 torch torchvision
REM which puts a pip torch next to conda's MKL numpy. That is exactly the
REM two-libiomp5md.dll combination CP-1 diagnosed as the OMP Error #15 hang.
REM Staying inside one conda-forge solve keeps a single OpenMP runtime.
REM conda-forge does publish win-64 CUDA builds of pytorch-gpu (cuda128 and
REM cuda130 as of this writing), so this path is real - but it is still NOT
REM verified on a Windows machine with an actual NVIDIA GPU.
echo [3b/4] Checking for an NVIDIA GPU ...
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo     No NVIDIA GPU detected - using CPU PyTorch.
) else (
    python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
    if errorlevel 1 (
        echo     NVIDIA GPU found - installing the CUDA build from conda-forge ^(one-time, large download^) ...
        call "%CONDA_EXE%" install -n nkreview --override-channels -c conda-forge --strict-channel-priority -y "pytorch-gpu"
        if errorlevel 1 (
            echo     [!] CUDA PyTorch install failed - keeping CPU PyTorch ^(app still works, just slower^).
        )
    ) else (
        echo     CUDA PyTorch already active.
    )
)

REM --- 4. Generate a Desktop shortcut --------------------------------------
REM  Written via PowerShell rather than cscript: VBScript is a deprecated
REM  feature-on-demand on Windows 11 and is scheduled to stop shipping
REM  enabled by default. Note also that we ASK Windows where the Desktop is
REM  instead of assuming %USERPROFILE%\Desktop - with OneDrive Known Folder
REM  Move switched on it lives under %OneDrive%\Desktop and the old path
REM  does not exist at all, so the shortcut would silently go nowhere.
echo [4/4] Creating Desktop shortcut ...
set "ICON=%CD%\assets\icon.ico"
set "TARGET=%CD%\run.bat"
set "PSSCRIPT=%TEMP%\make_nkreview_shortcut.ps1"

>  "%PSSCRIPT%" echo $ErrorActionPreference = 'Stop'
>> "%PSSCRIPT%" echo $desktop = [Environment]::GetFolderPath('Desktop')
>> "%PSSCRIPT%" echo $ws = New-Object -ComObject WScript.Shell
>> "%PSSCRIPT%" echo $sc = $ws.CreateShortcut((Join-Path $desktop 'NKReview.lnk'))
>> "%PSSCRIPT%" echo $sc.TargetPath = '%TARGET%'
>> "%PSSCRIPT%" echo $sc.WorkingDirectory = '%CD%'
>> "%PSSCRIPT%" echo $sc.IconLocation = '%ICON%'
>> "%PSSCRIPT%" echo $sc.Description = 'NKReview'
>> "%PSSCRIPT%" echo $sc.Save()
>> "%PSSCRIPT%" echo Write-Host ('    Shortcut: ' + $sc.FullName)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PSSCRIPT%"
if errorlevel 1 (
    echo     [!] Could not create the Desktop shortcut - start the app with run.bat instead.
)
del "%PSSCRIPT%" >nul 2>&1

echo.
echo ============================================
echo  Done. NKReview is installed.
echo ============================================
echo.
echo You now have two ways to start the app:
echo   1. Double-click "NKReview" on your Desktop
echo   2. Double-click run.bat inside this folder
echo.
pause
endlocal
exit /b 0


REM =========================================================================
REM  Subroutines
REM =========================================================================

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
    if exist "%%~P" set "CONDA_EXE=%%~P"
)
goto :eof

:install_miniforge
REM Downloads and silently installs Miniforge to %USERPROFILE%\miniforge3.
REM Returns errorlevel 0 on success, 1 on failure.
REM NSIS wants /D= last AND unquoted, so it cannot express a destination path
REM containing a space. Users called e.g. "Anna Mueller" have exactly that in
REM %USERPROFILE%, and a silent install would land somewhere unintended - so
REM stop with instructions instead of half-installing.
if not "%USERPROFILE%"=="%USERPROFILE: =%" (
    echo.
    echo [!] Your user folder contains a space:
    echo         %USERPROFILE%
    echo     The silent Miniforge installer cannot handle that. Please install
    echo     Miniforge yourself from
    echo       https://github.com/conda-forge/miniforge#download
    echo     and choose a path WITHOUT spaces ^(C:\miniforge3 works well^),
    echo     then run install.bat again.
    echo.
    exit /b 1
)
set "MFURL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"
set "MFEXE=%TEMP%\Miniforge3-Windows-x86_64.exe"
echo     Downloading Miniforge ...
curl -L -f -o "%MFEXE%" "%MFURL%"
if errorlevel 1 (
    echo     curl unavailable or failed; trying PowerShell ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri '%MFURL%' -OutFile '%MFEXE%' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 goto :install_miniforge_fail
)
if not exist "%MFEXE%" goto :install_miniforge_fail
echo     Installing Miniforge silently to %USERPROFILE%\miniforge3 ...
REM NSIS silent install. Note: /D= must be last and unquoted (no spaces in the path).
"%MFEXE%" /InstallationType=JustMe /RegisterPython=0 /S /D=%USERPROFILE%\miniforge3
if errorlevel 1 goto :install_miniforge_fail
del "%MFEXE%" >nul 2>&1
exit /b 0
:install_miniforge_fail
del "%MFEXE%" >nul 2>&1
exit /b 1
