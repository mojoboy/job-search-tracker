@echo off
rem Double-click to open the Job Search Tracker in your browser. Close this window to stop it.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

python -c "import streamlit, mysql.connector, anthropic, altair" 2>nul
if errorlevel 1 (
    echo Installing what the tracker needs. This only happens the first time...
    python -m pip install -r requirements.txt
)

python -m streamlit run app.py
pause
