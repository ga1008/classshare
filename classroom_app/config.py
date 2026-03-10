import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


# --- 路径配置 ---
BASE_DIR = Path(__file__).resolve().parent.parent # 指向项目根目录 (LAN_File_Sharer/)
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "classroom.db"
HOMEWORK_SUBMISSIONS_DIR = BASE_DIR / "homework_submissions"
SHARE_DIR = BASE_DIR / "shared_files"
ROSTER_DIR = BASE_DIR / "rosters"
ATTENDANCE_DIR = BASE_DIR / "attendance"
CHAT_LOG_DIR = BASE_DIR / "chat_logs"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / "config.json" # 修复：添加回 CONFIG_FILE

# 全局文件存储路径
GLOBAL_FILES_DIR = BASE_DIR / "storage/global_files"
FILE_CHUNK_SIZE = 8192  # 8KB chunks for streaming

# --- 服务器配置 ---
HOST = os.getenv("MAIN_HOST", "0.0.0.0")
PORT = int(os.getenv("MAIN_PORT", 8000))
AI_ASSISTANT_URL = f"http://{os.getenv('AI_HOST', '127.0.0.1')}:{os.getenv('AI_PORT', 8001)}"
MAIN_APP_CALLBACK_URL = os.getenv("MAIN_APP_CALLBACK_URL", f"http://localhost:{PORT}/api/internal/grading-complete")

# --- 教师凭证 ---
TEACHER_USER = os.getenv("TEACHER_NAME", "teacher")
TEACHER_PASS = os.getenv("TEACHER_PASSWD", "admin123")

# --- 功能配置 ---
TOTAL_UPLOAD_MBPS = float(os.getenv("TOTAL_UPLOAD_MBPS", 100.0))
MAX_UPLOAD_SIZE_MB = 2048  # 最大文件大小 2GB
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# --- 分块上传配置 ---
UPLOAD_CHUNK_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per chunk
CHUNKED_UPLOADS_DIR = BASE_DIR / "storage/chunked_uploads"
CHUNK_UPLOAD_TIMEOUT_HOURS = 24  # 自动清理超时的未完成上传

# --- 聊天配置 ---
MAX_HISTORY_IN_MEMORY = int(os.getenv("MAX_HISTORY_IN_MEMORY", 500))
STUDENT_HISTORY_COUNT = int(os.getenv("STUDENT_HISTORY_COUNT", 200))
TEACHER_HISTORY_COUNT = int(os.getenv("TEACHER_HISTORY_COUNT", 500))

# --- 安全配置 ---
SECRET_KEY = os.getenv("SECRET_KEY", "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME")
if SECRET_KEY == "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME":
    print("="*60)
    print("警告：正在使用默认的 SECRET_KEY。请在 .env 文件中设置一个强大的密钥！")
    print("="*60)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 令牌有效期 24 小时

