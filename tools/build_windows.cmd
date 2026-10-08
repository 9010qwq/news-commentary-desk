@echo off
setlocal
cd /d "%~dp0\.."
echo This downloads dependencies and Chromium and builds an unsigned local portable app.
echo No upload or publication is performed. Windows 10/11 x64 and Python 3.11+ required.
set /p OK=Continue? [y/N]:
if /i not "%OK%"=="y" exit /b 0
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed
set PLAYWRIGHT_BROWSERS_PATH=0
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --name NewsDesk --add-data "newsdesk/static;newsdesk/static" --collect-all playwright --collect-all trafilatura --collect-all keyring --collect-all tzdata --hidden-import keyring.backends.Windows launch.py
if errorlevel 1 goto failed
copy README.md dist\NewsDesk\
copy LICENSE dist\NewsDesk\
copy THIRD_PARTY_NOTICES.md dist\NewsDesk\
xcopy docs dist\NewsDesk\docs\ /E /I /Y
echo Build produced dist\NewsDesk\NewsDesk.exe. Test it on a clean Windows machine before sharing.
pause
exit /b 0
:failed
echo Build failed. Nothing was published. Review the error above.
pause
exit /b 1
