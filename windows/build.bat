@echo off
REM Build the Windows release: PyInstaller bundle + Inno Setup installer.
REM
REM Prereqs (one-time):
REM   python -m pip install -r ..\requirements.txt pyinstaller pystray pillow
REM   Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
REM
REM Run from this folder:  cd windows && build.bat
REM
REM Uses `python` (not `py`) so that CI runners and users with a single
REM Python in PATH both pick up the intended interpreter. `py.exe` resolves
REM to the highest-numbered registered Python and can skip the one
REM PyInstaller was installed into.

setlocal EnableDelayedExpansion
pushd "%~dp0"

REM ── 1. Read version from version.py ──────────────────────────────────────
for /f "tokens=2 delims==" %%v in ('findstr "__version__" version.py') do (
    set "VERSION=%%~v"
)
set VERSION=%VERSION:"=%
set VERSION=%VERSION: =%
echo [build] aMusicServer v%VERSION%

REM ── 2. PyInstaller ───────────────────────────────────────────────────────
REM --distpath ..\dist and --workpath ..\build put outputs at the project
REM root (rather than under windows\), matching what installer.iss expects.
echo [build] running PyInstaller...
python -m PyInstaller --clean --noconfirm ^
       --distpath ..\dist --workpath ..\build ^
       musicserver.spec || goto :err
if not exist "..\dist\aMusicServer\aMusicServer.exe" (
    echo [build] PyInstaller output missing
    goto :err
)

REM ── 3. Inno Setup ────────────────────────────────────────────────────────
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo [build] ISCC.exe not found. Install Inno Setup 6 and re-run.
    goto :err
)

echo [build] running Inno Setup...
"%ISCC%" /DAppVersion=%VERSION% installer.iss || goto :err

echo.
echo [build] DONE -> Output\Setup_aMusicServer_v%VERSION%.exe
popd
exit /b 0

:err
echo.
echo [build] FAILED
popd
exit /b 1
