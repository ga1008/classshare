@echo off
REM 1. 切换到 UTF-8 代码页
chcp 65001 > nul

REM 2. 强制切换到 BAT 文件所在的目录
cd /d %~dp0

REM 3. (关键修复) 强制将当前目录添加到 PYTHONPATH
REM 这样 Uvicorn 就能找到 'ai_assistant' 模块
set "PYTHONPATH=%~dp0"

echo ===========================================
echo === 正在启动 AI 助手服务 (AI Assistant)... ===
echo ===     请勿关闭此窗口 (按 CTRL+C 停止)    ===
echo ===========================================

REM 4. 运行
.\python_runtime\python.exe ai_assistant.py

pause