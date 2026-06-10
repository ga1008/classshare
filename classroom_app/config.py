import os
import re

from dotenv import load_dotenv

# Load .env once before path constants read environment variables.
load_dotenv()

from .storage_paths import (
    BASE_DIR,
    DATA_ROOT,
    LEGACY_ATTENDANCE_DIR,
    LEGACY_CHAT_LOG_DIR,
    LEGACY_CHUNKED_UPLOADS_DIR,
    LEGACY_DB_PATH,
    LEGACY_GLOBAL_FILES_DIR,
    LEGACY_HOMEWORK_SUBMISSIONS_DIR,
    LEGACY_ROSTER_DIR,
    LEGACY_SHARE_DIR,
    LEGACY_SIGNATURES_DIR,
    LEGACY_TEXTBOOK_ATTACHMENT_DIR,
    NEW_ATTENDANCE_DIR,
    NEW_CHAT_LOG_DIR,
    NEW_CHUNKED_UPLOADS_DIR,
    NEW_DB_PATH,
    NEW_GLOBAL_FILES_DIR,
    NEW_HOMEWORK_SUBMISSIONS_DIR,
    NEW_ROSTER_DIR,
    NEW_SHARE_DIR,
    NEW_SIGNATURES_DIR,
    NEW_TEXTBOOK_ATTACHMENT_DIR,
    legacy_candidates,
    select_compatible_file,
    select_preferred_dir,
)

def _read_url_env(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    return normalized.rstrip("/")


def _read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_size_limit_env(name: str, default: str = "") -> int | None:
    raw_value = str(os.getenv(name, default) or "").strip()
    if not raw_value:
        return None

    normalized = raw_value.replace(" ", "").lower()
    if normalized in {"0", "off", "false", "none", "unlimited", "no"}:
        return None

    match = re.fullmatch(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgt]?i?b|[kmgt])?", normalized)
    if not match:
        raise ValueError(
            f"Invalid value for {name}: '{raw_value}'. Use values like 512MB, 1GB, 0, or off."
        )

    numeric_value = float(match.group("value"))
    unit = (match.group("unit") or "b").lower()
    multiplier_map = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024 ** 2,
        "mb": 1024 ** 2,
        "mib": 1024 ** 2,
        "g": 1024 ** 3,
        "gb": 1024 ** 3,
        "gib": 1024 ** 3,
        "t": 1024 ** 4,
        "tb": 1024 ** 4,
        "tib": 1024 ** 4,
    }
    multiplier = multiplier_map.get(unit)
    if multiplier is None:
        raise ValueError(
            f"Unsupported size unit for {name}: '{raw_value}'. Use KB, MB, GB, or TB."
        )

    parsed_bytes = int(numeric_value * multiplier)
    return parsed_bytes if parsed_bytes > 0 else None


def _format_size_label(size_bytes: int | None) -> str:
    normalized_size = int(size_bytes or 0)
    if normalized_size <= 0:
        return ""

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(normalized_size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    precision = 0 if value >= 100 or unit_index == 0 else 2
    return f"{value:.{precision}f} {units[unit_index]}"


# --- Paths ---
DATA_DIR = DATA_ROOT
DB_PATH = select_compatible_file(("MAIN_DB_PATH",), NEW_DB_PATH, (LEGACY_DB_PATH,))
HOMEWORK_SUBMISSIONS_DIR = select_preferred_dir(("MAIN_HOMEWORK_SUBMISSIONS_DIR",), NEW_HOMEWORK_SUBMISSIONS_DIR)
SHARE_DIR = select_preferred_dir(("MAIN_SHARE_DIR",), NEW_SHARE_DIR)
ROSTER_DIR = select_preferred_dir(("MAIN_ROSTER_DIR",), NEW_ROSTER_DIR)
ATTENDANCE_DIR = select_preferred_dir(("MAIN_ATTENDANCE_DIR",), NEW_ATTENDANCE_DIR)
CHAT_LOG_DIR = select_preferred_dir(("MAIN_CHAT_LOG_DIR",), NEW_CHAT_LOG_DIR)
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / "config.json"

# Global file storage.
GLOBAL_FILES_DIR = select_preferred_dir(("MAIN_GLOBAL_FILES_DIR",), NEW_GLOBAL_FILES_DIR)
SIGNATURES_DIR = select_preferred_dir(("MAIN_SIGNATURES_DIR",), NEW_SIGNATURES_DIR)
TEXTBOOK_ATTACHMENT_DIR = select_preferred_dir(("MAIN_TEXTBOOK_ATTACHMENT_DIR",), NEW_TEXTBOOK_ATTACHMENT_DIR)
FILE_CHUNK_SIZE = 8192

HOMEWORK_SUBMISSIONS_LEGACY_DIRS = legacy_candidates(
    HOMEWORK_SUBMISSIONS_DIR,
    (LEGACY_HOMEWORK_SUBMISSIONS_DIR, NEW_HOMEWORK_SUBMISSIONS_DIR),
)
GLOBAL_FILES_LEGACY_DIRS = legacy_candidates(
    GLOBAL_FILES_DIR,
    (LEGACY_GLOBAL_FILES_DIR, NEW_GLOBAL_FILES_DIR),
)
SIGNATURES_LEGACY_DIRS = legacy_candidates(
    SIGNATURES_DIR,
    (LEGACY_SIGNATURES_DIR, NEW_SIGNATURES_DIR),
)
TEXTBOOK_ATTACHMENT_LEGACY_DIRS = legacy_candidates(
    TEXTBOOK_ATTACHMENT_DIR,
    (LEGACY_TEXTBOOK_ATTACHMENT_DIR, NEW_TEXTBOOK_ATTACHMENT_DIR),
)
SHARE_LEGACY_DIRS = legacy_candidates(SHARE_DIR, (LEGACY_SHARE_DIR, NEW_SHARE_DIR))

# --- Service ---
HOST = os.getenv("MAIN_HOST", "0.0.0.0")
PORT = int(os.getenv("MAIN_PORT", 8000))
AI_ASSISTANT_URL = _read_url_env("AI_ASSISTANT_URL") or f"http://{os.getenv('AI_HOST', '127.0.0.1')}:{os.getenv('AI_PORT', 8001)}"
MAIN_APP_CALLBACK_URL = _read_url_env("MAIN_APP_CALLBACK_URL") or f"http://127.0.0.1:{PORT}/api/internal/grading-complete"
MAIN_WORKERS = max(1, int(os.getenv("MAIN_WORKERS", 1)))
MAIN_THREADPOOL_TOKENS = max(32, int(os.getenv("MAIN_THREADPOOL_TOKENS", 64)))
MAIN_BACKLOG = max(128, int(os.getenv("MAIN_BACKLOG", 2048)))
MAIN_LIMIT_CONCURRENCY = max(0, int(os.getenv("MAIN_LIMIT_CONCURRENCY", 0)))
MAIN_TIMEOUT_KEEP_ALIVE = max(5, int(os.getenv("MAIN_TIMEOUT_KEEP_ALIVE", 30)))
MAIN_WS_PING_INTERVAL = max(5.0, float(os.getenv("MAIN_WS_PING_INTERVAL", 20.0)))
MAIN_WS_PING_TIMEOUT = max(5.0, float(os.getenv("MAIN_WS_PING_TIMEOUT", 20.0)))
EXAM_GENERATION_STALE_MINUTES = max(15, int(os.getenv("EXAM_GENERATION_STALE_MINUTES", 180)))
AI_GRADING_STALE_MINUTES = max(15, int(os.getenv("AI_GRADING_STALE_MINUTES", 240)))
DEFAULT_PUBLIC_SITE_BASE_URL = "https://www.guardianangel.net.cn"
PUBLIC_SITE_BASE_URL = _read_url_env("PUBLIC_SITE_BASE_URL") or _read_url_env("SITE_BASE_URL") or DEFAULT_PUBLIC_SITE_BASE_URL

# --- Teacher agent task center ---
AGENT_TASKS_ENABLED = _read_bool_env("AGENT_TASKS_ENABLED", True)
AGENT_TASK_RUNTIME_URL = _read_url_env("AGENT_TASK_RUNTIME_URL") or ""
AGENT_TASK_RUNTIME_TOKEN = str(os.getenv("AGENT_TASK_RUNTIME_TOKEN") or "").strip()
AGENT_TASK_RUNTIME_MODEL = str(os.getenv("AGENT_TASK_RUNTIME_MODEL") or "deepseek-v4-pro").strip()
AGENT_TASK_RUNTIME_WORKSPACE_PREFIX = str(
    os.getenv("AGENT_TASK_RUNTIME_WORKSPACE_PREFIX") or "/workspace/tasks"
).strip().rstrip("/")
AGENT_TASK_WORKSPACE_ROOT = DATA_DIR / "agent_tasks"
AGENT_TASK_DEEPSEEK_HOME = AGENT_TASK_WORKSPACE_ROOT / "deepseek_home"
AGENT_TASK_RUNTIME_CONFIG_PATH = AGENT_TASK_DEEPSEEK_HOME / "config.toml"
AGENT_TASK_WORKER_ID = str(os.getenv("AGENT_TASK_WORKER_ID") or "agent-task-worker").strip()
AGENT_TASK_WORKER_POLL_SECONDS = max(2, int(os.getenv("AGENT_TASK_WORKER_POLL_SECONDS", 5)))
AGENT_TASK_RUNTIME_POLL_SECONDS = max(2, int(os.getenv("AGENT_TASK_RUNTIME_POLL_SECONDS", 5)))
AGENT_TASK_MAX_RUNTIME_SECONDS = max(60, int(os.getenv("AGENT_TASK_MAX_RUNTIME_SECONDS", 1800)))
AGENT_TASK_DEEPSEEK_AUTO_APPROVE = _read_bool_env("AGENT_TASK_DEEPSEEK_AUTO_APPROVE", False)
AGENT_TASK_ALLOW_RUNTIME_SHELL = _read_bool_env("AGENT_TASK_ALLOW_RUNTIME_SHELL", False)
# Agent 桥接：运行时容器回连主应用的内网地址（compose 网络里是 http://app:8000）
AGENT_BRIDGE_BASE_URL = (_read_url_env("AGENT_BRIDGE_BASE_URL") or "http://app:8000").rstrip("/")

# --- Email notification worker ---
EMAIL_WORKER_POLL_SECONDS = max(1, int(os.getenv("EMAIL_WORKER_POLL_SECONDS", 5)))
EMAIL_WORKER_BATCH_SIZE = max(1, min(int(os.getenv("EMAIL_WORKER_BATCH_SIZE", 24)), 100))
EMAIL_WORKER_MAX_ATTEMPTS = max(1, min(int(os.getenv("EMAIL_WORKER_MAX_ATTEMPTS", 5)), 12))
EMAIL_SMTP_TIMEOUT_SECONDS = max(3, int(os.getenv("EMAIL_SMTP_TIMEOUT_SECONDS", 12)))
EMAIL_IMAP_TIMEOUT_SECONDS = max(3, int(os.getenv("EMAIL_IMAP_TIMEOUT_SECONDS", 12)))
EMAIL_DEFAULT_PER_MINUTE_LIMIT = max(1, int(os.getenv("EMAIL_DEFAULT_PER_MINUTE_LIMIT", 25)))
EMAIL_DEFAULT_DAILY_LIMIT = max(1, int(os.getenv("EMAIL_DEFAULT_DAILY_LIMIT", 300)))
EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS = max(30, int(os.getenv("EMAIL_WORKER_HEARTBEAT_TIMEOUT_SECONDS", 180)))

# --- Teacher auth ---
TEACHER_USER = os.getenv("TEACHER_NAME", "teacher")
TEACHER_PASS = os.getenv("TEACHER_PASSWD", "admin123")
INITIAL_SUPER_ADMIN_EMAIL = os.getenv("INITIAL_SUPER_ADMIN_EMAIL", "414160375@qq.com").strip().lower()
INITIAL_SUPER_ADMIN_NAME = os.getenv("INITIAL_SUPER_ADMIN_NAME", "张海林老师").strip()
INITIAL_SUPER_ADMIN_PASSWORD = os.getenv("INITIAL_SUPER_ADMIN_PASSWORD", "").strip()

# --- Uploads ---
TOTAL_UPLOAD_MBPS = float(os.getenv("TOTAL_UPLOAD_MBPS", 100.0))
MAX_UPLOAD_SIZE_MB = float(os.getenv("MAX_UPLOAD_SIZE_MB", 2048))
MAX_UPLOAD_SIZE_BYTES = int(MAX_UPLOAD_SIZE_MB * 1024 * 1024)
MAX_SUBMISSION_FILE_COUNT = int(os.getenv("MAX_SUBMISSION_FILE_COUNT", 500))

# Per-submission file limits (separate from global upload limit)
MAX_SUBMISSION_PER_FILE_MB = float(os.getenv("MAX_SUBMISSION_PER_FILE_MB", 5))
MAX_SUBMISSION_PER_FILE_BYTES = int(MAX_SUBMISSION_PER_FILE_MB * 1024 * 1024)
MAX_SUBMISSION_TOTAL_MB = float(os.getenv("MAX_SUBMISSION_TOTAL_MB", 10))
MAX_SUBMISSION_TOTAL_BYTES = int(MAX_SUBMISSION_TOTAL_MB * 1024 * 1024)

# --- Chunked uploads ---
UPLOAD_CHUNK_SIZE_BYTES = 5 * 1024 * 1024
CHUNKED_UPLOADS_DIR = select_preferred_dir(("MAIN_CHUNKED_UPLOADS_DIR",), NEW_CHUNKED_UPLOADS_DIR)
CHUNK_UPLOAD_TIMEOUT_HOURS = 24

RUNTIME_DIRECTORIES = (
    DATA_DIR,
    DB_PATH.parent,
    HOMEWORK_SUBMISSIONS_DIR,
    SHARE_DIR,
    ROSTER_DIR,
    ATTENDANCE_DIR,
    CHAT_LOG_DIR,
    GLOBAL_FILES_DIR,
    SIGNATURES_DIR,
    TEXTBOOK_ATTACHMENT_DIR,
    CHUNKED_UPLOADS_DIR,
    AGENT_TASK_WORKSPACE_ROOT,
    AGENT_TASK_DEEPSEEK_HOME,
)


def ensure_runtime_directories() -> None:
    for directory in RUNTIME_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)

# --- SQLite ---
DB_ENGINE = str(os.getenv("DB_ENGINE", "sqlite") or "sqlite").strip().lower()
DATABASE_URL = str(os.getenv("DATABASE_URL", "") or "").strip()
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", 30000))
SQLITE_CACHE_SIZE_KB = int(os.getenv("SQLITE_CACHE_SIZE_KB", 8192))
SQLITE_WAL_AUTOCHECKPOINT_PAGES = int(os.getenv("SQLITE_WAL_AUTOCHECKPOINT_PAGES", 2000))

# --- PostgreSQL ---
POSTGRES_POOL_MIN = max(1, int(os.getenv("POSTGRES_POOL_MIN", 1)))
POSTGRES_POOL_MAX = max(
    POSTGRES_POOL_MIN,
    int(os.getenv("POSTGRES_POOL_MAX", max(4, MAIN_WORKERS * 4))),
)
POSTGRES_STATEMENT_TIMEOUT_MS = max(0, int(os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", 30000)))
POSTGRES_LOCK_TIMEOUT_MS = max(0, int(os.getenv("POSTGRES_LOCK_TIMEOUT_MS", 5000)))
POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS = max(
    0,
    int(os.getenv("POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS", 30000)),
)
POSTGRES_BACKEND_READY = _read_bool_env("POSTGRES_BACKEND_READY", False)

# --- Behavior tracking ---
BEHAVIOR_WRITE_QUEUE_SIZE = int(os.getenv("BEHAVIOR_WRITE_QUEUE_SIZE", 20000))
BEHAVIOR_WRITE_BATCH_SIZE = int(os.getenv("BEHAVIOR_WRITE_BATCH_SIZE", 128))
BEHAVIOR_WRITE_FLUSH_INTERVAL_MS = int(os.getenv("BEHAVIOR_WRITE_FLUSH_INTERVAL_MS", 250))
BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS = int(os.getenv("BEHAVIOR_WRITE_ENQUEUE_TIMEOUT_MS", 1500))
BEHAVIOR_WRITE_SYNC_TIMEOUT_MS = int(os.getenv("BEHAVIOR_WRITE_SYNC_TIMEOUT_MS", 5000))

# --- Chat ---
MAX_HISTORY_IN_MEMORY = int(os.getenv("MAX_HISTORY_IN_MEMORY", 500))
STUDENT_HISTORY_COUNT = int(os.getenv("STUDENT_HISTORY_COUNT", 200))
TEACHER_HISTORY_COUNT = int(os.getenv("TEACHER_HISTORY_COUNT", 500))
UI_COPY_GENERATION_ENABLED = os.getenv("UI_COPY_GENERATION_ENABLED", "True").lower() == "true"
UI_COPY_REFRESH_POLL_SECONDS = int(os.getenv("UI_COPY_REFRESH_POLL_SECONDS", 30 * 60))

# --- Classroom download policy ---
CLASSROOM_DOWNLOAD_LIMIT_ENABLED = _read_bool_env("CLASSROOM_DOWNLOAD_LIMIT_ENABLED", False)
CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES = _parse_size_limit_env("CLASSROOM_DOWNLOAD_MAX_SIZE", "0")
CLASSROOM_DOWNLOAD_MAX_SIZE_LABEL = _format_size_label(CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES)
CLASSROOM_DOWNLOAD_LIMIT_ACTIVE = bool(
    CLASSROOM_DOWNLOAD_LIMIT_ENABLED and CLASSROOM_DOWNLOAD_MAX_SIZE_BYTES
)

# --- Security ---
SECRET_KEY = os.getenv("SECRET_KEY", "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME")
if SECRET_KEY == "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME":
    print("=" * 60)
    print("WARNING: using the default SECRET_KEY. Set a strong value in .env.")
    print("=" * 60)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# --- Site record / ownership ---
SITE_DISPLAY_NAME = os.getenv("SITE_DISPLAY_NAME", "课堂互动平台")
SITE_OWNER_NAME = os.getenv("SITE_OWNER_NAME", "张老师")
SITE_OWNER_STATEMENT = os.getenv(
    "SITE_OWNER_STATEMENT",
    f"本网站（{SITE_DISPLAY_NAME}）由{SITE_OWNER_NAME}负责运营，站内内容与服务均归权利人依法所有。",
)
SITE_ICP_RECORD_NUMBER = os.getenv("SITE_ICP_RECORD_NUMBER", "桂ICP备2026007021号").strip()
SITE_ICP_RECORD_APPROVED_AT = os.getenv("SITE_ICP_RECORD_APPROVED_AT", "2026-04-20").strip()
SITE_ICP_RECORD_LOOKUP_URL = _read_url_env("SITE_ICP_RECORD_LOOKUP_URL") or "https://beian.miit.gov.cn"
SITE_ICP_NOTICE_SOURCE = os.getenv("SITE_ICP_NOTICE_SOURCE", "工信部ICP备案").strip()

SITE_RECORD = {
    "site_name": SITE_DISPLAY_NAME,
    "owner_name": SITE_OWNER_NAME,
    "owner_statement": SITE_OWNER_STATEMENT,
    "icp_number": SITE_ICP_RECORD_NUMBER,
    "approved_at": SITE_ICP_RECORD_APPROVED_AT,
    "lookup_url": SITE_ICP_RECORD_LOOKUP_URL,
    "notice_source": SITE_ICP_NOTICE_SOURCE,
}
