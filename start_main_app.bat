@echo off
setlocal EnableExtensions
call "%~dp0scripts\windows\run_python_script.bat" launcher.py start --services main --no-browser %*
if errorlevel 1 pause
exit /b %errorlevel%
