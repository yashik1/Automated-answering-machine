@echo off
REM One-command start for the Mr. Singh Pizza answering machine (Windows).
REM
REM   run.bat          Set up (if needed) and start the web/phone server.
REM   run.bat test     Set up (if needed) and run the test suite.
REM   run.bat demo     Run the interactive terminal demo (no server).
REM
REM It creates a virtual environment on first run and installs dependencies,
REM so you can just clone the repo and run this from cmd.

setlocal
cd /d "%~dp0"

set "VENV_DIR=venv"
if "%PYTHON%"=="" set "PYTHON=python"

REM 1. Create the virtual environment on first run.
if not exist "%VENV_DIR%\" (
    echo ==^> Creating virtual environment in .\%VENV_DIR%
    %PYTHON% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :error
)

REM 2. Activate it.
call "%VENV_DIR%\Scripts\activate.bat"

REM 3. Install dependencies (skips quickly if already satisfied).
echo ==^> Installing dependencies
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :error

REM 4. Do what was asked.
if "%~1"=="test" (
    echo ==^> Running tests
    pytest
    goto :eof
)
if "%~1"=="demo" (
    echo ==^> Starting terminal demo
    python src\main.py
    goto :eof
)

if "%PORT%"=="" set "PORT=5000"
echo ==^> Starting server on http://localhost:%PORT%
echo     Website:   http://localhost:%PORT%/
echo     Kitchen:   http://localhost:%PORT%/staff
echo     Press Ctrl+C to stop.
python src\app.py
goto :eof

:error
echo.
echo Setup failed. Make sure Python 3.9+ is installed and on your PATH.
exit /b 1
