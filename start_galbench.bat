@echo off
setlocal
cd /d "%~dp0"
title galbench - Local Lab
echo.
echo   galbench - Local evaluation lab
echo   Opening http://127.0.0.1:8765
echo   Keep this window open while testing.
echo.
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable 'Add python.exe to PATH', then run this file again.
pause
exit /b 1
:use_python
python -m galbench web --open
goto finished
:use_py
py -3 -m galbench web --open
:finished
echo.
echo galbench has stopped. Existing results are saved in the runs folder.
pause
