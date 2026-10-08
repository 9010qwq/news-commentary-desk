@echo off
setlocal
cd /d "%~dp0"
if exist "NewsDesk.exe" (
  start "" "NewsDesk.exe"
  exit /b 0
)
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)"
  if errorlevel 1 goto python_missing
  set PY=py -3
) else (
  python -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)"
  if errorlevel 1 goto python_missing
  set PY=python
)
echo First setup downloads Python packages and Chromium for real webpage screenshots.
echo This uses disk space and internet. No API charge, email, startup task or security change.
set /p OK=Install these dependencies in this folder? [y/N]:
if /i not "%OK%"=="y" exit /b 0
%PY% -m venv .venv
if errorlevel 1 goto failed
:dependencies
if exist ".venv\newsdesk-ready" goto run
echo Installing required packages. Please wait...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
 echo ready> ".venv\newsdesk-ready"
:run
".venv\Scripts\python.exe" launch.py %*
if errorlevel 1 goto failed
exit /b 0
:python_missing
echo Python 3.11 or newer is required for this source package.
echo Install from https://www.python.org/downloads/windows/ and enable PATH.
echo Then double-click this script again. No Python was silently installed.
pause
exit /b 1
:failed
echo Setup or launch failed. Keep the error above. Never share API keys in screenshots.
echo Check your internet, Python version, disk space and installation permissions.
pause
exit /b 1
