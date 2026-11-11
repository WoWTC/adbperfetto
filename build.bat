@echo off
rem Build dist\adbperfetto.exe with PyInstaller (onefile).
rem adb is NOT bundled; it is resolved from PATH at runtime.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found. Create it first:
    echo   py -3.13 -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
    exit /b 1
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean adbperfetto.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)
echo Built dist\adbperfetto.exe
