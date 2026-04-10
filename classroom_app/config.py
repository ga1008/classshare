import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env once at process startup.
load_dotenv()


def _read_url_env(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    return normalized.rstrip("/")


# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "classroom.db"
HOMEWORK_SUBMISSIONS_DIR = BASE_DIR / "homework_submissions"
SHARE_DIR = BASE_DIR / "shared_files"
ROSTER_DIR = BASE_DIR / "rosters"
ATTENDANCE_DIR = BASE_DIR / "attendance"
CHAT_LOG_DIR = BASE_DIR / "chat_logs"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / "config.json"

# Global file storage.
GLOBAL_FILES_DIR = BASE_DIR / "storage/global_files"
FILE_CHUNK_SIZE = 8192

# --- Service ---
HOST = os.getenv("MAIN_HOST", "0.0.0.0")
PORT = int(os.getenv("MAIN_PORT", 8000))
AI_ASSISTANT_URL = _read_url_env("AI_ASSISTANT_URL") or f"http://{os.getenv('AI_HOST', '127.0.0.1')}:{os.getenv('AI_PORT', 8001)}"
MAIN_APP_CALLBACK_URL = _read_url_env("MAIN_APP_CALLBACK_URL") or f"http://127.0.0.1:{PORT}/api/internal/grading-complete"

# --- Teacher auth ---
TEACHER_USER = os.getenv("TEACHER_NAME", "teacher")
TEACHER_PASS = os.getenv("TEACHER_PASSWD", "admin123")

# --- Uploads ---
TOTAL_UPLOAD_MBPS = float(os.getenv("TOTAL_UPLOAD_MBPS", 100.0))
MAX_UPLOAD_SIZE_MB = float(os.getenv("MAX_UPLOAD_SIZE_MB", 2048))
MAX_UPLOAD_SIZE_BYTES = int(MAX_UPLOAD_SIZE_MB * 1024 * 1024)
MAX_SUBMISSION_FILE_COUNT = int(os.getenv("MAX_SUBMISSION_FILE_COUNT", 500))

# --- Chunked uploads ---
UPLOAD_CHUNK_SIZE_BYTES = 5 * 1024 * 1024
CHUNKED_UPLOADS_DIR = BASE_DIR / "storage/chunked_uploads"
CHUNK_UPLOAD_TIMEOUT_HOURS = 24

# --- Chat ---
MAX_HISTORY_IN_MEMORY = int(os.getenv("MAX_HISTORY_IN_MEMORY", 500))
STUDENT_HISTORY_COUNT = int(os.getenv("STUDENT_HISTORY_COUNT", 200))
TEACHER_HISTORY_COUNT = int(os.getenv("TEACHER_HISTORY_COUNT", 500))
UI_COPY_GENERATION_ENABLED = os.getenv("UI_COPY_GENERATION_ENABLED", "True").lower() == "true"
UI_COPY_REFRESH_POLL_SECONDS = int(os.getenv("UI_COPY_REFRESH_POLL_SECONDS", 30 * 60))

# --- Security ---
SECRET_KEY = os.getenv("SECRET_KEY", "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME")
if SECRET_KEY == "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME":
    print("=" * 60)
    print("WARNING: using the default SECRET_KEY. Set a strong value in .env.")
    print("=" * 60)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
