@echo off
rem Starts the A&A 1942 Online odds overlay using the virtual environment kept outside OneDrive.
rem Extra arguments are passed through, e.g.:  run_overlay.bat --debug
set "VENV=%USERPROFILE%\aaoverlay-venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found at %VENV%
    echo Create it with:
    echo   python -m venv "%VENV%"
    echo   "%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    exit /b 1
)
cd /d "%~dp0"
"%VENV%\Scripts\python.exe" -m aaoverlay %*
