@echo off
rem Double-click to open the Job Search Tracker in your browser. Close this window to stop it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run

where python >nul 2>nul
if errorlevel 1 goto nopython
echo Setting up the tracker. This only happens the first time and takes a few minutes...
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

:run
rem localhost only: nobody else on your network can open it
".venv\Scripts\python.exe" -m streamlit run app.py --server.address localhost
pause
exit /b

:nopython
echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:failed
echo Setup didn't finish. Check your internet connection, then double-click start.bat again.
pause
exit /b 1
