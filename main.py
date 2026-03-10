import os
import sys
import uvicorn
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# --- 从新包中导入 app 实例 ---
try:
    from classroom_app.app import app
    from classroom_app.config import (
        HOST, PORT, AI_ASSISTANT_URL,
        ROSTER_DIR, SHARE_DIR, ATTENDANCE_DIR, CHAT_LOG_DIR, DATA_DIR,
        HOMEWORK_SUBMISSIONS_DIR, CHUNKED_UPLOADS_DIR
    )
    from classroom_app.database import init_database
except ImportError as e:
    print(f"CRITICAL: 导入错误: {e}")
    print("请确保 'classroom_app' 包已正确创建并包含所有模块 (app.py, config.py, etc.)")
    sys.exit(1)


def run_server():
    """
    初始化数据库并启动 Uvicorn 服务器。
    不再需要 tkinter GUI。
    """

    print("=" * 60);
    print("===      欢迎使用课堂管理平台 V4.0 (Multi-Tenant)      ===");
    print("=" * 60)

    # 确保所有目录在启动时都存在
    for d in [DATA_DIR, HOMEWORK_SUBMISSIONS_DIR, SHARE_DIR, ROSTER_DIR, ATTENDANCE_DIR, CHAT_LOG_DIR, CHUNKED_UPLOADS_DIR]:
        d.mkdir(exist_ok=True)

    # 在启动时初始化数据库
    print("[SERVER] 正在初始化数据库...")
    init_database()
    print("[SERVER] 数据库初始化完成。")

    print(f"\n[SERVER] 课堂服务即将运行于: http://{HOST}:{PORT}")
    print(f"[SERVER] AI 助手服务应运行于: {AI_ASSISTANT_URL}")
    print(f"\n[SERVER] 教师请访问: http://127.0.0.1:{PORT}/teacher/login")
    print(f"[SERVER] 学生请访问: http://127.0.0.1:{PORT}/student/login")
    print("\n[SERVER] 按下 CTRL+C 即可停止服务器。")
    print("-" * 60)

    try:
        # 运行 Uvicorn 服务器
        uvicorn.run(
            "classroom_app.app:app",
            host=HOST,
            port=PORT,
            log_level="info",
            reload=True  # 在开发时使用 reload，生产环境请设为 False
        )
    except Exception as e:
        print(f"[ERROR] 服务器启动失败: {e}")


if __name__ == "__main__":
    run_server()
