@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

:: ==============================================================================
::  LanShare Classroom Platform - One-Click Startup Script
:: ==============================================================================

title LanShare - Startup

:: --- Project root directory ---
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "VENV_DIR=%PROJECT_DIR%\venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_ACTIVATE=%VENV_DIR%\Scripts\activate.bat"
set "INSTALLER=%PROJECT_DIR%\dist\installer\python-3.14.3-amd64.exe"
set "REQUIREMENTS=%PROJECT_DIR%\requirements.lock.txt"

:: --- Ensure logs directory exists ---
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

echo.
echo ============================================================
echo     LanShare Classroom V4.0 - Startup
echo ============================================================
echo.

:: ==============================================================================
:: Step 1: Check and install Python 3.14
:: ==============================================================================
echo [1/4] Checking Python environment...

:: First check if python in PATH has version 3.14
set "SYSTEM_PYTHON_OK=0"
python --version 2>&1 | findstr "3.14" >nul 2>&1
if !errorlevel! equ 0 set "SYSTEM_PYTHON_OK=1"

if "!SYSTEM_PYTHON_OK!"=="1" (
    echo       [OK] System Python 3.14 found.
    goto :step1_done
)

:: Check common Python 3.14 install paths
set "FOUND_PYTHON="
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "FOUND_PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    goto :step1_found
)
if exist "%ProgramFiles%\Python314\python.exe" (
    set "FOUND_PYTHON=%ProgramFiles%\Python314\python.exe"
    goto :step1_found
)
if exist "%ProgramFiles(x86)%\Python314\python.exe" (
    set "FOUND_PYTHON=%ProgramFiles(x86)%\Python314\python.exe"
    goto :step1_found
)
if exist "C:\Python314\python.exe" (
    set "FOUND_PYTHON=C:\Python314\python.exe"
    goto :step1_found
)

:: No Python 3.14 found - install it
echo       [!] Python 3.14 not found. Installing...

if not exist "%INSTALLER%" (
    echo       [ERROR] Python installer not found:
    echo       %INSTALLER%
    echo       Please ensure dist\installer\python-3.14.3-amd64.exe exists.
    goto :error_exit
)

echo       Installing Python 3.14 silently...
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
if !errorlevel! equ 0 (
    echo       [OK] Python installed.
    call :refresh_path
    goto :step1_done
)

echo       [!] Silent install failed. Trying interactive...
echo       Please check "Add Python to PATH" in the installer.
"%INSTALLER%" PrependPath=1 Include_pip=1
if !errorlevel! neq 0 (
    echo       [ERROR] Python install failed. Please install manually.
    goto :error_exit
)
call :refresh_path
goto :step1_done

:step1_found
echo       [OK] Found Python: !FOUND_PYTHON!

:step1_done
echo       Python check passed.
echo.

:: ==============================================================================
:: Step 2: Check and fix virtual environment
:: ==============================================================================
echo [2/4] Checking virtual environment...

if exist "%VENV_PYTHON%" goto :venv_exists

echo       [!] Virtual environment missing or broken. Recreating...

:: Determine which python to use for creating venv
set "CREATE_PYTHON="
if "!SYSTEM_PYTHON_OK!"=="1" (
    set "CREATE_PYTHON=python"
) else if not "!FOUND_PYTHON!"=="" (
    set "CREATE_PYTHON=!FOUND_PYTHON!"
) else (
    python --version >nul 2>&1
    if !errorlevel! equ 0 set "CREATE_PYTHON=python"
)

if "!CREATE_PYTHON!"=="" (
    echo       [ERROR] No Python available to create virtual environment.
    goto :error_exit
)

echo       Using !CREATE_PYTHON! to create virtual environment...

if exist "%VENV_DIR%" (
    echo       Removing old virtual environment...
    rmdir /s /q "%VENV_DIR%" 2>nul
)

!CREATE_PYTHON! -m venv "%VENV_DIR%"
if !errorlevel! equ 0 (
    echo       [OK] Virtual environment created.
    goto :venv_check
)

echo       [ERROR] Failed to create virtual environment with venv.
echo       Trying virtualenv...
!CREATE_PYTHON! -m pip install virtualenv --quiet 2>nul
!CREATE_PYTHON! -m virtualenv "%VENV_DIR%"
if !errorlevel! neq 0 (
    echo       [ERROR] virtualenv also failed. Please troubleshoot manually.
    goto :error_exit
)
echo       [OK] Virtual environment created via virtualenv.
goto :venv_check

:venv_exists
echo       [OK] Virtual environment exists.

:venv_check
:: Verify venv python works
"%VENV_PYTHON%" --version >nul 2>&1
if !errorlevel! neq 0 (
    echo       [ERROR] Virtual environment Python is broken.
    echo       Try deleting the venv folder and run this script again.
    goto :error_exit
)
echo.

:: ==============================================================================
:: Step 3: Check and install dependencies
:: ==============================================================================
echo [3/4] Checking dependencies...

set "DEPS_OK=1"
"%VENV_PYTHON%" -c "import fastapi; import uvicorn; import httpx; import openai; import dotenv; import PIL; import pandas" 2>nul
if !errorlevel! neq 0 set "DEPS_OK=0"

if "!DEPS_OK!"=="1" goto :deps_ok

echo       [!] Missing dependencies. Installing...

if exist "%REQUIREMENTS%" goto :install_lock
if exist "%PROJECT_DIR%\requirements.txt" goto :install_req

echo       [WARNING] No requirements file found. Skipping.
goto :deps_done

:install_lock
echo       Installing from requirements.lock.txt...
"%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS%" --quiet 2>&1
if !errorlevel! equ 0 goto :deps_done
echo       [WARNING] Locked requirements failed. Trying requirements.txt...
if not exist "%PROJECT_DIR%\requirements.txt" goto :install_fail
:install_req
echo       Installing from requirements.txt...
"%VENV_PYTHON%" -m pip install -r "%PROJECT_DIR%\requirements.txt" --quiet 2>&1
if !errorlevel! neq 0 goto :install_fail
goto :deps_done

:install_fail
echo       [ERROR] Dependency install failed. Check network connection.
goto :error_exit

:deps_done
echo       [OK] Dependencies installed.
goto :deps_end

:deps_ok
echo       [OK] All dependencies ready.

:deps_end
echo.

:: ==============================================================================
:: Step 4: Start services
:: ==============================================================================
echo [4/4] Starting services...
echo.

:: Activate virtual environment
call "%VENV_ACTIVATE%"

:: --- Start AI Assistant (background) ---
echo       Starting AI Assistant service (port 8001)...
start "LanShare-AI-Assistant" /min cmd /c ""%VENV_PYTHON%" "%PROJECT_DIR%\ai_assistant.py""

:: Wait for AI Assistant to be ready
echo       Waiting for AI Assistant to be ready...
set "AI_READY=0"
for /l %%i in (1,1,15) do (
    if "!AI_READY!"=="0" (
        timeout /t 1 /nobreak >nul 2>&1
        "%VENV_PYTHON%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "AI_READY=1"
            echo       [OK] AI Assistant is ready.
        )
    )
)
if "!AI_READY!"=="0" (
    echo       [WARNING] AI Assistant not ready yet. It may still be starting.
)

echo.

:: --- Start Main App ---
echo       Starting Main App (port 8000)...
echo.
echo ============================================================
echo   Teacher: http://127.0.0.1:8000/teacher/login
echo   Student: http://127.0.0.1:8000/student/login
echo ============================================================
echo   Close this window to stop all services.
echo ============================================================
echo.

:: Run main app (foreground, blocks here)
"%VENV_PYTHON%" "%PROJECT_DIR%\main.py"

:: --- Cleanup after main app exits ---
echo.
echo [INFO] Main app stopped. Closing AI Assistant...
taskkill /fi "WINDOWTITLE eq LanShare-AI-Assistant" >nul 2>&1
echo       All services stopped.
goto :end

:: ==============================================================================
:: Subroutines
:: ==============================================================================

:refresh_path
set "USER_PATH="
set "SYS_PATH="
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%b"
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%b"
if defined USER_PATH if defined SYS_PATH (
    set "PATH=!SYS_PATH!;!USER_PATH!"
) else if defined SYS_PATH (
    set "PATH=!SYS_PATH!"
) else if defined USER_PATH (
    set "PATH=!USER_PATH!"
)
goto :eof

:error_exit
echo.
echo ============================================================
echo   Startup failed! Check error messages above.
echo ============================================================
echo.
pause
exit /b 1

:end
pause
exit /b 0
