@echo off
rem Build dist\adbperfetto-<version>.exe with PyInstaller (onefile);
rem builds from a dirty tree are named adbperfetto-<version>-dirty.exe.
rem adb is NOT bundled; it is resolved from PATH at runtime.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found. Create it first:
    echo   py -3.13 -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
    exit /b 1
)
rem Bake the git-derived version into adbperfetto\_buildinfo.py so the
rem exe never calls git at runtime; removed again below on any outcome.
".venv\Scripts\python.exe" tools\write_buildinfo.py
if errorlevel 1 (
    del /q adbperfetto\_buildinfo.py 2>nul
    echo Could not compute the version from git.
    exit /b 1
)
for /f "delims=" %%n in ('.venv\Scripts\python.exe -c "from adbperfetto._buildinfo import EXE_NAME; print(EXE_NAME)"') do set EXE_NAME=%%n
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean adbperfetto.spec
set RC=%errorlevel%
del /q adbperfetto\_buildinfo.py 2>nul
if not "%RC%"=="0" (
    echo Build failed.
    exit /b 1
)
echo Built dist\%EXE_NAME%.exe
