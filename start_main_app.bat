@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
call "%~dp0scripts\windows\run_python_script.bat" launcher.py start --services main --no-browser %*
if errorlevel 1 pause
exit /b %errorlevel%
