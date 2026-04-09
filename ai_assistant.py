# ==============================================================================
# AI 助教服务 (ai_assistant.py - V3.3.3 Dynamic Model Selection, Better Prompts)
# ==============================================================================
import asyncio
import base64
import heapq
import json
import mimetypes
import os
import sys
import traceback
from pathlib import Path
from typing import AsyncGenerator
from typing import Dict, Any, List, Optional, Literal

import httpx
import uvicorn
from PIL import Image
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager # 1. 新增导入
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# --- 加载 .env 配置 ---
load_dotenv()

# --- AI 平台 SDK ---
try:
    from openai import OpenAI, AsyncOpenAI
except ImportError:
    OpenAI, AsyncOpenAI = None, None
try:
    from volcenginesdkarkruntime import Ark, AsyncArk
except ImportError:
    Ark, AsyncArk = None, None


def _read_int_env(*names: str, default: int) -> int:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value in (None, ""):
            continue
        try:
            return int(raw_value)
        except ValueError:
            print(f"[WARNING] Invalid integer for {name}: {raw_value!r}. Using {default}.")
            return default
    return default

# --- AI 配置 (保持不变) ---
AI_HOST = os.getenv("AI_HOST", "127.0.0.1")
AI_PORT = int(os.getenv("AI_PORT", 8001))
GLOBAL_AI_CONCURRENCY = max(
    1,
    min(_read_int_env("GLOBAL_AI_CONCURRENCY", "AI_WORKER_CONCURRENCY", default=3), 3),
)
MAIN_APP_CALLBACK_URL = os.getenv("MAIN_APP_CALLBACK_URL")
PLATFORM_PRIORITY = [p.strip() for p in os.getenv("AI_PLATFORM_PRIORITY", "siliconflow,volcengine,deepseek").split(',')]
VOLCENGINE_OPENAI_BASE_URL = os.getenv("VOLCENGINE_OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
AI_GRADING_MAX_FILE_COUNT = int(os.getenv("AI_GRADING_MAX_FILE_COUNT", 50))
AI_GRADING_MAX_TOTAL_FILE_MB = float(os.getenv("AI_GRADING_MAX_TOTAL_FILE_MB", 20))
AI_GRADING_MAX_TOTAL_FILE_BYTES = int(AI_GRADING_MAX_TOTAL_FILE_MB * 1024 * 1024)
AI_GRADING_MAX_RAW_TEXT_FILE_MB = float(os.getenv("AI_GRADING_MAX_RAW_TEXT_FILE_MB", 2))
AI_GRADING_MAX_RAW_TEXT_FILE_BYTES = int(AI_GRADING_MAX_RAW_TEXT_FILE_MB * 1024 * 1024)
VOLCENGINE_DOCUMENT_MAX_MB = float(os.getenv("VOLCENGINE_DOCUMENT_MAX_MB", 5))
VOLCENGINE_DOCUMENT_MAX_BYTES = int(VOLCENGINE_DOCUMENT_MAX_MB * 1024 * 1024)
VOLCENGINE_IMAGE_MAX_MB = float(os.getenv("VOLCENGINE_IMAGE_MAX_MB", 10))
VOLCENGINE_IMAGE_MAX_BYTES = int(VOLCENGINE_IMAGE_MAX_MB * 1024 * 1024)

# --- 平台详细配置 (保持不变) ---
PLATFORMS_CONFIG = {
    "deepseek": {
        "enabled": os.getenv("DEEPSEEK_ENABLED", "False").lower() == "true",
        "api_key": os.getenv("DEEPSEEK_API_KEY"), "base_url": "https://api.deepseek.com",
        "models": {
            "standard": os.getenv("DEEPSEEK_MODEL_STANDARD", "deepseek-chat"),
            "thinking": os.getenv("DEEPSEEK_MODEL_THINKING", "deepseek-reasoner"),
            "vision": None
        },
        "can_force_json": {
            "standard": True, "thinking": False, "vision": False
        },
        "type": "openai",
    },
    "siliconflow": {
        "enabled": os.getenv("SILICONFLOW_ENABLED", "True").lower() == "true",
        "api_key": os.getenv("SILICONFLOW_API_KEY"), "base_url": "https://api.siliconflow.cn/v1",
        "models": {
            "standard": os.getenv("SILICONFLOW_MODEL_STANDARD", "deepseek-ai/DeepSeek-V2"),
            "thinking": os.getenv("SILICONFLOW_MODEL_THINKING", "deepseek-ai/DeepSeek-V2.5"),
            "vision": os.getenv("SILICONFLOW_MODEL_VISION", "deepseek-ai/deepseek-vl2")
        },
        "can_force_json": {
            "standard": True, "thinking": True, "vision": False
        },
        "type": "openai",
    },
    "volcengine": {
        "enabled": os.getenv("VOLCENGINE_ENABLED", "True").lower() == "true",
        "api_key": os.getenv("ARK_API_KEY"), "base_url": None,
        "responses_base_url": VOLCENGINE_OPENAI_BASE_URL,
        "models": {
            "standard": os.getenv("VOLCENGINE_MODEL_STANDARD", "doubao-seed-2-0-pro-260215"),
            "thinking": os.getenv("VOLCENGINE_MODEL_THINKING", "doubao-seed-2-0-pro-260215"),
            "vision": os.getenv("VOLCENGINE_MODEL_VISION", "doubao-seed-2-0-pro-260215")
        },
        "can_force_json": {
            "standard": False, "thinking": False, "vision": False
        },
        "type": "volcengine",
    }
}
ENABLED_PLATFORMS = [p for p in PLATFORM_PRIORITY if p in PLATFORMS_CONFIG and PLATFORMS_CONFIG[p]["enabled"]]

# --- 全局队列调度和HTTP客户端 ---
TASK_PRIORITY_ORDER = {
    "interactive": 0,
    "default": 1,
    "background": 2,
}


def _sanitize_task_priority(value: Optional[str]) -> str:
    normalized = str(value or "default").strip().lower()
    return normalized if normalized in TASK_PRIORITY_ORDER else "default"


class AIPriorityLimiter:
    def __init__(self, concurrency: int):
        self.concurrency = concurrency
        self._running = 0
        self._waiters: list[tuple[int, int, asyncio.Future[None], str, str]] = []
        self._counter = 0
        self._lock = asyncio.Lock()

    async def acquire(self, *, priority: str, label: Optional[str] = None) -> None:
        normalized_priority = _sanitize_task_priority(priority)
        normalized_label = str(label or "task")

        async with self._lock:
            has_higher_waiter = any(
                waiter_priority < TASK_PRIORITY_ORDER[normalized_priority]
                for waiter_priority, *_rest in self._waiters
            )
            if self._running < self.concurrency and not has_higher_waiter:
                self._running += 1
                return

            loop = asyncio.get_running_loop()
            future: asyncio.Future[None] = loop.create_future()
            heapq.heappush(
                self._waiters,
                (
                    TASK_PRIORITY_ORDER[normalized_priority],
                    self._counter,
                    future,
                    normalized_priority,
                    normalized_label,
                ),
            )
            self._counter += 1

        await future

    async def release(self) -> None:
        async with self._lock:
            self._running = max(0, self._running - 1)
            while self._waiters:
                _priority_value, _counter, future, priority, label = heapq.heappop(self._waiters)
                if future.cancelled():
                    continue
                self._running += 1
                future.set_result(None)
                print(f"[AI QUEUE] 出队: priority={priority}, label={label}, running={self._running}/{self.concurrency}")
                break

    @asynccontextmanager
    async def slot(self, *, priority: str, label: Optional[str] = None):
        normalized_priority = _sanitize_task_priority(priority)
        normalized_label = str(label or "task")
        await self.acquire(priority=normalized_priority, label=normalized_label)
        print(f"[AI QUEUE] 入槽: priority={normalized_priority}, label={normalized_label}")
        try:
            yield
        finally:
            await self.release()


ai_limiter = AIPriorityLimiter(GLOBAL_AI_CONCURRENCY)
callback_client = httpx.AsyncClient()


# --- Pydantic 模型 (去除 model_type 默认值) ---
class GenerationRequest(BaseModel):
    prompt: str
    model_type: Literal["standard", "thinking"] = "standard"


class ExamGenerationRequest(BaseModel):
    prompt: str
    model_type: Literal["standard", "thinking"] = "thinking"
    task_type: str = "exam_generation"
    teacher_id: Optional[int] = None
    class_offering_id: Optional[int] = None


class GradingFile(BaseModel):
    stored_path: str
    original_filename: Optional[str] = None
    relative_path: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    file_ext: Optional[str] = None
    file_hash: Optional[str] = None


class GradingJob(BaseModel):
    submission_id: int
    rubric_md: str
    requirements_md: str = ""
    files: List[GradingFile] = Field(default_factory=list)
    file_paths: List[str] = Field(default_factory=list)
    answers_json: Optional[str] = None
    allowed_file_types_json: Optional[str] = None
    # model_type 将在 run_grading_job 中动态决定，这里不再需要


# --- 提示词模板 (更新) ---
GRADING_SYSTEM_PROMPT = """
你是一个严格、公正的AI作业批改助教。
你的任务是根据提供的【作业要求】、【评分标准】和【学生提交内容】（可能是代码文件、文本答案、图片等），对作业进行批改。
请务必使用 **中文** 进行回复。
你必须严格按照以下JSON格式返回结果，不要包含任何额外的解释或代码块标记：
{
  "score": <评分，整数，0-100>,
  "feedback_md": "<详细的批改反馈，使用Markdown格式，按评分标准的每个维度逐一说明得分点和失分点>"
}
例如:
{
  "score": 85,
  "feedback_md": "- **知识点理解 (30/30)**: 概念理解准确，论述清晰。\n- **代码实现 (25/30)**: 核心逻辑正确，但边界条件处理不完整。\n- **规范性 (15/20)**: 代码格式基本规范，缺少部分注释。\n- **完成度 (15/20)**: 大部分功能已实现，少数功能缺失。\n\n**总结**: 整体完成度较好，建议加强对边界条件的处理和代码注释。"
}
"""

GENERATION_SYSTEM_PROMPT = """
你是一个AI课程助教，擅长根据教师的提示出题。
你的任务是生成作业要求和评分标准。
请务必使用 **中文** 进行回复。
你必须严格按照以下JSON格式返回结果，不要包含任何额外的解释或代码块标记：
{
  "requirements_md": "<Markdown格式的作业要求>",
  "rubric_md": "<Markdown格式的评分标准>"
}
例如:
{
  "requirements_md": "## 作业：使用 Python Turtle 绘制学号最后一位数字\n\n**要求:**\n1. 使用 `turtle` 库。\n2. 绘制你学号的最后一位数字。\n3. ...",
  "rubric_md": "## 评分标准\n\n1. **正确使用turtle模块 (30分)**\n   - ...\n2. **准确绘制数字 (30分)**\n   - ...\n..."
}
"""

EXAM_GENERATION_SYSTEM_PROMPT = """
你是一个AI试卷生成专家，擅长根据教师的要求生成高质量的试卷题目。
你的任务是生成完整的试卷题目，包括题目内容、选项（如果是选择题）、答案和解析。
请务必使用 **中文** 进行回复。

你必须严格按照以下JSON格式返回结果，不要包含任何额外的解释或代码块标记：
{
  "pages": [
    {
      "name": "第一部分",
      "questions": [
        {
          "id": "q1",
          "type": "radio",
          "text": "题目内容",
          "options": ["选项A", "选项B", "选项C", "选项D"],
          "answer": "A",
          "explanation": "解析内容"
        },
        {
          "id": "q2",
          "type": "checkbox",
          "text": "多选题内容",
          "options": ["选项A", "选项B", "选项C", "选项D"],
          "answer": ["A", "B"],
          "explanation": "解析内容"
        },
        {
          "id": "q3",
          "type": "text",
          "text": "填空题内容",
          "placeholder": "提示文本",
          "answer": "正确答案",
          "explanation": "解析内容"
        },
        {
          "id": "q4",
          "type": "textarea",
          "text": "问答题内容",
          "placeholder": "提示文本",
          "answer": "参考答案",
          "explanation": "解析内容"
        }
      ]
    }
  ],
  "description": "试卷描述或说明"
}

注意：
1. id字段格式：q1, q2, q3... 或 p1_q1, p1_q2...
2. type字段必须是：radio（单选题）、checkbox（多选题）、text（填空题）、textarea（问答题）
3. 对于radio和checkbox类型，必须提供options数组（至少2个选项）
4. 对于text和textarea类型，可以提供placeholder作为提示文本
5. answer字段：radio类型为单个选项字母（如"A"），checkbox类型为选项字母数组（如["A", "B"]），text和textarea类型为字符串答案
6. explanation字段：每道题的解析，说明为什么答案正确或其他选项为什么错误
7. 试卷可以包含多个pages（部分），每个部分有name和questions数组
8. 根据教师要求的总题数、题型分布和难度生成题目
"""

# --- Lifespan (替换旧的 Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    if not MAIN_APP_CALLBACK_URL: print("[WARNING] 'MAIN_APP_CALLBACK_URL' 未设置，AI批改结果无法回调主程序。")
    print(f"[AI SERVER] AI 助手服务启动于 http://{AI_HOST}:{AI_PORT}")
    print(f"[AI SERVER] 启用的平台 (按优先级): {', '.join(ENABLED_PLATFORMS)}")
    print(f"[AI SERVER] 全局 AI 并发数: {GLOBAL_AI_CONCURRENCY}")
    await callback_client.__aenter__()

    print("[AI SERVER] Lifespan: Startup complete.")
    yield  # 服务在此运行

    # --- Shutdown ---
    print("[AI SERVER] Lifespan: Shutting down...")
    await callback_client.__aexit__(None, None, None)
    print("[AI SERVER] Lifespan: Shutdown complete.")


app = FastAPI(lifespan=lifespan)  # 2. 在此处注册 lifespan

STREAM_EVENT_MEDIA_TYPE = "application/x-ndjson; charset=utf-8"
THINK_TAG_OPEN = "<think>"
THINK_TAG_CLOSE = "</think>"


@app.get("/api/internal/health")
async def internal_health():
    return {
        "status": "ok",
        "service": "ai",
        "enabled_platforms": ENABLED_PLATFORMS,
        "port": AI_PORT,
    }


class AIChatRequest(BaseModel):
    system_prompt: str
    messages: List[Dict[str, Any]]  # 历史消息, 格式: {"role": "user", "content": "..."}
    new_message: str  # 用户的最新文本输入
    base64_urls: List[str] = Field(default_factory=list)  # 新上传的图片 (base64 data URLs)
    image_inputs: List[Dict[str, Any]] = Field(default_factory=list)
    model_capability: Literal["standard", "thinking", "vision"] = "standard"
    response_format: Literal["text", "json"] = "text"
    task_priority: Literal["interactive", "default", "background"] = "default"
    task_label: Optional[str] = None


# --- 辅助函数 (保持不变) ---
def _encode_stream_event(event: str, **payload: Any) -> str:
    return json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n"


def _coerce_stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_value = item.get("text") or item.get("content")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "".join(parts)
    if isinstance(value, dict):
        text_value = value.get("text") or value.get("content")
        if isinstance(text_value, str):
            return text_value
    return str(value)


def _extract_delta_parts(delta: Any) -> tuple[str, str]:
    content_text = _coerce_stream_text(getattr(delta, "content", None))

    reasoning_text = ""
    for attr_name in ("reasoning_content", "reasoning", "thinking"):
        candidate = _coerce_stream_text(getattr(delta, attr_name, None))
        if candidate:
            reasoning_text = candidate
            break

    if not reasoning_text:
        model_extra = getattr(delta, "model_extra", None)
        if isinstance(model_extra, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                candidate = _coerce_stream_text(model_extra.get(key))
                if candidate:
                    reasoning_text = candidate
                    break

    return reasoning_text, content_text


def _extract_image_url_from_content_item(item: dict[str, Any]) -> str:
    image_url = item.get("image_url")
    if isinstance(image_url, dict):
        return str(image_url.get("url") or "").strip()
    return str(image_url or item.get("url") or "").strip()


def _normalize_request_image_inputs(req: AIChatRequest) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []

    for index, item in enumerate(req.image_inputs or [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            name = str(item.get("name") or "").strip()
            source = str(item.get("source") or "").strip()
            label_parts = [part for part in [source, name] if part]
            if label_parts:
                label = f"[图片 {index}] {' | '.join(label_parts)}"
        normalized.append({
            "url": url,
            "label": label,
        })

    if normalized:
        return normalized

    for url in req.base64_urls:
        normalized_url = str(url or "").strip()
        if normalized_url:
            normalized.append({"url": normalized_url, "label": ""})
    return normalized


def _build_user_message_content(new_message: str, image_inputs: list[dict[str, str]]) -> str | list[dict[str, Any]]:
    text_content = str(new_message or "")
    if not image_inputs:
        return text_content

    content: list[dict[str, Any]] = [{"type": "text", "text": text_content}]
    for item in image_inputs:
        label = str(item.get("label") or "").strip()
        if label:
            content.append({"type": "text", "text": label})
        content.append({
            "type": "image_url",
            "image_url": {"url": item["url"]},
        })
    return content


def _normalize_chat_content_for_platform(
    content: Any,
    *,
    allow_multimodal: bool,
) -> str | list[dict[str, Any]]:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        if item_type in {"text", "input_text"}:
            return str(content.get("text") or content.get("content") or "")
        if item_type in {"image_url", "input_image"}:
            image_url = _extract_image_url_from_content_item(content)
            if allow_multimodal and image_url:
                return [{"type": "image_url", "image_url": {"url": image_url}}]
            label = str(content.get("label") or content.get("name") or "图片").strip()
            return f"[{label}]"
        return _coerce_stream_text(content)

    if not isinstance(content, list):
        return str(content)

    text_fragments: list[str] = []
    multimodal_parts: list[dict[str, Any]] = []
    pending_text: list[str] = []
    has_images = False

    def flush_pending_text() -> None:
        if not pending_text:
            return
        text_value = "\n".join(part for part in pending_text if part).strip()
        pending_text.clear()
        if not text_value:
            return
        if allow_multimodal and has_images:
            multimodal_parts.append({"type": "text", "text": text_value})
        else:
            text_fragments.append(text_value)

    for item in content:
        if isinstance(item, str):
            pending_text.append(item)
            continue
        if not isinstance(item, dict):
            pending_text.append(str(item))
            continue

        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"text", "input_text"}:
            text_value = str(item.get("text") or item.get("content") or "")
            if text_value:
                pending_text.append(text_value)
            continue

        if item_type in {"image_url", "input_image"}:
            image_url = _extract_image_url_from_content_item(item)
            if not image_url:
                continue
            has_images = True
            flush_pending_text()
            if allow_multimodal:
                multimodal_parts.append({"type": "image_url", "image_url": {"url": image_url}})
            else:
                label = str(item.get("label") or item.get("name") or "图片").strip()
                text_fragments.append(f"[{label}]")
            continue

        if item_type in {"input_file", "file"}:
            flush_pending_text()
            label = str(item.get("filename") or item.get("name") or "附件").strip()
            text_fragments.append(f"[附件: {label}]")
            continue

        fallback_text = _coerce_stream_text(item)
        if fallback_text:
            pending_text.append(fallback_text)

    flush_pending_text()

    if allow_multimodal and has_images:
        return multimodal_parts
    return "\n".join(part for part in text_fragments if part).strip()


def _prepare_chat_messages_for_platform(
    messages: List[Dict[str, Any]],
    *,
    capability: Literal["standard", "thinking", "vision"],
) -> list[dict[str, Any]]:
    allow_multimodal = capability == "vision"
    prepared_messages: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        prepared_message = dict(message)
        prepared_message["content"] = _normalize_chat_content_for_platform(
            prepared_message.get("content"),
            allow_multimodal=allow_multimodal,
        )
        prepared_messages.append(prepared_message)

    return prepared_messages


class ThinkTagStreamParser:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_think = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        if not text:
            return []

        self.buffer += text
        segments: list[tuple[str, str]] = []

        while self.buffer:
            tag = THINK_TAG_CLOSE if self.in_think else THINK_TAG_OPEN
            tag_index = self.buffer.find(tag)
            if tag_index == -1:
                safe_length = len(self.buffer) - (len(tag) - 1)
                if safe_length <= 0:
                    break
                chunk = self.buffer[:safe_length]
                self.buffer = self.buffer[safe_length:]
                if chunk:
                    segments.append(("thinking" if self.in_think else "answer", chunk))
                break

            chunk = self.buffer[:tag_index]
            if chunk:
                segments.append(("thinking" if self.in_think else "answer", chunk))
            self.buffer = self.buffer[tag_index + len(tag):]
            self.in_think = not self.in_think

        return segments

    def flush(self) -> list[tuple[str, str]]:
        if not self.buffer:
            return []

        remaining = self.buffer
        self.buffer = ""
        return [("thinking" if self.in_think else "answer", remaining)]


def is_image_file(file_path: Path) -> bool:
    try:
        Image.open(file_path).verify(); return True  # Use verify() for faster check
    except Exception:
        return False


def file_to_base64_url(file_path: Path) -> str:
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        # Use Pillow to get mime type more reliably
        img = Image.open(file_path)
        mime = Image.MIME.get(img.format, "image/jpeg")
        img.close()  # Close the image file handle
        b64 = base64.b64encode(data).decode('utf-8')
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"[ERROR] file_to_base64_url {file_path}: {e}"); return ""


def _extract_answers_text(answers_json: str | None) -> str:
    if not answers_json:
        return ""

    try:
        answers_data = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
        answers = answers_data.get("answers", answers_data) if isinstance(answers_data, dict) else answers_data
        if isinstance(answers, list):
            lines = ["【学生文字答案】"]
            for i, item in enumerate(answers, start=1):
                if isinstance(item, dict):
                    question = item.get("question", f"第{i}题")
                    answer = item.get("answer", item.get("content", item.get("text", "")))
                else:
                    question = f"第{i}题"
                    answer = item
                lines.append(f"\n### {question}\n{answer}")
            return "\n".join(lines)
        if isinstance(answers, dict):
            lines = ["【学生文字答案】"]
            for key, value in answers.items():
                lines.append(f"\n### {key}\n{value}")
            return "\n".join(lines)
        return f"【学生文字答案】\n{answers}"
    except (json.JSONDecodeError, AttributeError, TypeError):
        return f"【学生文字答案】\n{answers_json}"


def _human_size(num_bytes: int | None) -> str:
    size = int(num_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _guess_mime_type(file_path: Path, explicit_mime_type: str | None = None) -> str:
    if explicit_mime_type:
        return explicit_mime_type.lower()
    guessed_mime_type = mimetypes.guess_type(file_path.name)[0]
    return (guessed_mime_type or "application/octet-stream").lower()


def _is_text_like_grading_file(file_path: Path, mime_type: str | None = None) -> bool:
    text_like_extensions = {
        ".c", ".cc", ".cpp", ".cs", ".csv", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js",
        ".json", ".jsx", ".kt", ".log", ".md", ".php", ".py", ".rb", ".rs", ".sh", ".sql", ".svg",
        ".tex", ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
    }
    normalized_mime_type = _guess_mime_type(file_path, mime_type)
    return (
        file_path.suffix.lower() in text_like_extensions
        or normalized_mime_type.startswith("text/")
        or normalized_mime_type in {"application/javascript", "application/json", "application/xml", "image/svg+xml"}
    )


def _normalize_grading_files(job: GradingJob) -> list[dict[str, Any]]:
    normalized_files: list[dict[str, Any]] = []
    if job.files:
        for file in job.files:
            file_path = Path(file.stored_path)
            if not file_path.exists():
                continue
            try:
                file_size = int(file.file_size or file_path.stat().st_size)
            except OSError:
                file_size = int(file.file_size or 0)
            display_name = file.relative_path or file.original_filename or file_path.name
            normalized_files.append(
                {
                    "path": file_path,
                    "display_name": display_name,
                    "original_filename": file.original_filename or file_path.name,
                    "relative_path": file.relative_path or display_name,
                    "mime_type": _guess_mime_type(file_path, file.mime_type),
                    "size": file_size,
                    "ext": (file.file_ext or file_path.suffix).lower(),
                    "hash": file.file_hash,
                }
            )
        return normalized_files

    for raw_path in job.file_paths:
        file_path = Path(raw_path)
        if not file_path.exists():
            continue
        normalized_files.append(
            {
                "path": file_path,
                "display_name": file_path.name,
                "original_filename": file_path.name,
                "relative_path": file_path.name,
                "mime_type": _guess_mime_type(file_path),
                "size": int(file_path.stat().st_size),
                "ext": file_path.suffix.lower(),
                "hash": None,
            }
        )
    return normalized_files


def _categorize_grading_file(file_info: dict[str, Any]) -> str:
    file_path = file_info["path"]
    mime_type = file_info["mime_type"]
    ext = file_info["ext"]
    if mime_type.startswith("image/") or ext in {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"} or is_image_file(file_path):
        return "image"
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx"}:
        return "document"
    if _is_text_like_grading_file(file_path, mime_type):
        return "text"
    return "binary"


def _validate_grading_file_limits(grading_files: list[dict[str, Any]]) -> None:
    if len(grading_files) > AI_GRADING_MAX_FILE_COUNT:
        raise ValueError(f"附件数量超过 AI 批改上限 {AI_GRADING_MAX_FILE_COUNT} 个")

    total_bytes = sum(int(file_info.get("size") or 0) for file_info in grading_files)
    if total_bytes > AI_GRADING_MAX_TOTAL_FILE_BYTES:
        raise ValueError(f"附件总大小超过 AI 批改上限 {_human_size(AI_GRADING_MAX_TOTAL_FILE_BYTES)}")

    for file_info in grading_files:
        category = file_info.get("category")
        file_size = int(file_info.get("size") or 0)
        display_name = file_info.get("display_name") or file_info["path"].name
        if category == "document" and file_size > VOLCENGINE_DOCUMENT_MAX_BYTES:
            raise ValueError(f"文档 '{display_name}' 超过火山方舟文档上限 {_human_size(VOLCENGINE_DOCUMENT_MAX_BYTES)}")
        if category == "image" and file_size > VOLCENGINE_IMAGE_MAX_BYTES:
            raise ValueError(f"图片 '{display_name}' 超过火山方舟图片上限 {_human_size(VOLCENGINE_IMAGE_MAX_BYTES)}")


def _select_grading_execution(grading_files: list[dict[str, Any]]) -> dict[str, Any]:
    has_documents = any(file_info["category"] == "document" for file_info in grading_files)
    has_images = any(file_info["category"] == "image" for file_info in grading_files)
    has_binary = any(file_info["category"] == "binary" for file_info in grading_files)

    if has_documents:
        for platform_name in ENABLED_PLATFORMS:
            if platform_name != "volcengine":
                continue
            config = PLATFORMS_CONFIG[platform_name]
            return {
                "platform_name": platform_name,
                "platform_config": {"name": platform_name, **config},
                "capability": "vision" if has_images else "thinking",
                "mode": "volcengine_responses",
            }
        raise ValueError("当前启用的 AI 平台不支持直接识别 PDF/DOC/XLS 等文档附件，请启用火山方舟。")

    if has_images:
        for platform_name in ENABLED_PLATFORMS:
            config = PLATFORMS_CONFIG[platform_name]
            if platform_name == "volcengine":
                return {
                    "platform_name": platform_name,
                    "platform_config": {"name": platform_name, **config},
                    "capability": "vision",
                    "mode": "volcengine_responses",
                }
            if config["models"].get("vision"):
                return {
                    "platform_name": platform_name,
                    "platform_config": {"name": platform_name, **config},
                    "capability": "vision",
                    "mode": "vision_messages",
                }
        raise ValueError("当前启用的 AI 平台不支持图片附件识别，请启用支持视觉能力的模型。")

    for platform_name in ENABLED_PLATFORMS:
        config = PLATFORMS_CONFIG[platform_name]
        if config["models"].get("thinking"):
            return {
                "platform_name": platform_name,
                "platform_config": {"name": platform_name, **config},
                "capability": "thinking",
                "mode": "text_messages",
            }
        if config["models"].get("standard"):
            return {
                "platform_name": platform_name,
                "platform_config": {"name": platform_name, **config},
                "capability": "standard",
                "mode": "text_messages",
            }

    raise ValueError("没有可用于批改的 AI 平台配置")


def _read_text_file_excerpt(file_path: Path, max_bytes: int = AI_GRADING_MAX_RAW_TEXT_FILE_BYTES) -> tuple[str, bool]:
    with open(file_path, "rb") as file:
        data = file.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="ignore")
    return text, truncated


def _build_text_grading_message(
    rubric_md: str,
    grading_files: list[dict[str, Any]],
    requirements_md: str = "",
    answers_json: str | None = None,
) -> list[dict[str, Any]]:
    answers_text = _extract_answers_text(answers_json)

    text_content = ""
    if requirements_md:
        text_content += f"【作业要求】\n{requirements_md}\n\n"
    text_content += f"【评分标准】\n{rubric_md}\n\n"
    if answers_text:
        text_content += answers_text + "\n\n"

    if grading_files:
        text_content += "【学生提交文件】\n"
        for file_info in grading_files:
            display_name = file_info["display_name"]
            category = file_info["category"]
            file_size = _human_size(file_info["size"])
            if category == "text":
                excerpt, truncated = _read_text_file_excerpt(file_info["path"])
                text_content += f"\n--- 文件: {display_name} ({file_size}) ---\n```\n{excerpt}\n```\n"
                if truncated:
                    text_content += f"[系统说明] 文件 {display_name} 已按 {_human_size(AI_GRADING_MAX_RAW_TEXT_FILE_BYTES)} 截断。\n"
            elif category == "image":
                text_content += f"\n--- 图片文件: {display_name} ({file_size}) ---\n该图片将以图像输入方式提交给模型。\n"
            elif category == "document":
                text_content += f"\n--- 文档文件: {display_name} ({file_size}) ---\n该文档将以原始文件方式提交给模型。\n"
            else:
                text_content += f"\n--- 二进制文件: {display_name} ({file_size}) ---\n当前平台不支持直接解析该类型文件。\n"

    return [{"role": "user", "content": text_content}]


def _build_data_url(file_path: Path, mime_type: str) -> str:
    with open(file_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _build_volcengine_responses_input(
    rubric_md: str,
    grading_files: list[dict[str, Any]],
    requirements_md: str = "",
    answers_json: str | None = None,
) -> list[dict[str, Any]]:
    prompt_lines = []
    if requirements_md:
        prompt_lines.append(f"【作业要求】\n{requirements_md}")
    prompt_lines.append(f"【评分标准】\n{rubric_md}")
    answers_text = _extract_answers_text(answers_json)
    if answers_text:
        prompt_lines.append(answers_text)

    content_items: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": "\n\n".join(prompt_lines + ["【学生提交文件】请结合以下原始文件进行批改。"]),
        }
    ]

    for file_info in grading_files:
        category = file_info["category"]
        if category == "document":
            content_items.append(
                {
                    # Inference: Ark Responses follows the OpenAI-compatible input_file schema.
                    "type": "input_file",
                    "filename": file_info["original_filename"],
                    "file_data": _build_data_url(file_info["path"], file_info["mime_type"]),
                }
            )
            continue
        if category == "image":
            content_items.append(
                {
                    "type": "input_image",
                    "image_url": _build_data_url(file_info["path"], file_info["mime_type"]),
                }
            )
            continue
        if category == "text":
            excerpt, truncated = _read_text_file_excerpt(file_info["path"])
            suffix = f"\n[系统说明] 文件已按 {_human_size(AI_GRADING_MAX_RAW_TEXT_FILE_BYTES)} 截断。" if truncated else ""
            content_items.append(
                {
                    "type": "input_text",
                    "text": f"\n--- 文件: {file_info['display_name']} ---\n```\n{excerpt}\n```\n{suffix}",
                }
            )

    return [{"role": "user", "content": content_items}]


async def _call_volcengine_responses_api(
    *,
    model_name: str,
    api_key: str,
    input_payload: list[dict[str, Any]],
    task_priority: str = "default",
    task_label: Optional[str] = None,
) -> dict[str, Any]:
    request_payload = {
        "model": model_name,
        "instructions": GRADING_SYSTEM_PROMPT,
        "input": input_payload,
        "text": {"format": {"type": "json_object"}},
    }

    async with ai_limiter.slot(priority=task_priority, label=task_label or "responses_api"):
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{VOLCENGINE_OPENAI_BASE_URL}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            response.raise_for_status()
            data = response.json()

    output_text = data.get("output_text")
    if not output_text:
        output = data.get("output") or []
        for item in output:
            if item.get("type") != "message":
                continue
            for content_item in item.get("content", []):
                if content_item.get("type") == "output_text":
                    output_text = content_item.get("text")
                    break
            if output_text:
                break

    if not output_text:
        raise ValueError("火山方舟 Responses API 未返回可解析的文本结果")

    try:
        return json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"火山方舟 Responses API 返回的结果不是有效 JSON: {output_text}") from exc


def build_vision_messages(rubric: str, files: List[Path], platform_type: str,
                          requirements_md: str = "", answers_json: str = None) -> List[Dict[str, Any]]:
    """构建视觉消息 (支持文件 + JSON 答案混合)"""
    answers_text = _extract_answers_text(answers_json)

    if platform_type == "volcengine":
        content = []
        text_content = ""
        if requirements_md:
            text_content += f"【作业要求】\n{requirements_md}\n\n"
        text_content += f"【评分标准】\n{rubric}\n\n"
        if answers_text:
            text_content += answers_text + "\n"
        text_content += "【学生提交文件】\n"
        for file_path in files:
            if is_image_file(file_path):
                text_content += f"- 图片文件: {file_path.name}\n"
                b64_url = file_to_base64_url(file_path)
                if b64_url: content.append({"type": "image_url", "image_url": {"url": b64_url}})
            else:
                try:
                    code = file_path.read_text(encoding='utf-8', errors='ignore')
                    text_content += f"\n--- {file_path.name} ---\n```\n{code}\n```\n"
                except Exception:
                    text_content += f"\n--- {file_path.name} (无法读取) ---\n"
        content.append({"type": "text", "text": text_content})
        return [{"role": "user", "content": content}]
    else:  # OpenAI compatible format
        header_text = ""
        if requirements_md:
            header_text += f"【作业要求】\n{requirements_md}\n\n"
        header_text += f"【评分标准】\n{rubric}\n\n"
        if answers_text:
            header_text += answers_text + "\n"
        header_text += "【学生提交文件】\n请根据以上内容进行评分:"
        content = [{"type": "text", "text": header_text}]
        for file_path in files:
            if is_image_file(file_path):
                b64_url = file_to_base64_url(file_path)
                if b64_url: content.append({"type": "image_url", "image_url": {"url": b64_url}})
            else:
                try:
                    code = file_path.read_text(encoding='utf-8', errors='ignore')
                    content.append(
                        {"type": "text", "text": f"\n--- 文件: {file_path.name} ---\n```\n{code}\n```\n"})
                except Exception:
                    content.append({"type": "text", "text": f"\n--- 文件: {file_path.name} (无法读取) ---\n"})
        return [{"role": "user", "content": content}]


# --- 平台选择与调用 (保持 V3.3.2 的 JSON 逻辑) ---
def _get_selected_platform_config(capability: Literal["standard", "thinking", "vision"]) -> Optional[Dict]:
    for platform_name in ENABLED_PLATFORMS:
        config = PLATFORMS_CONFIG[platform_name]
        if config["models"].get(capability):
            return {"name": platform_name, **config}
    return None


async def _call_ai_platform(
        messages: List[Dict],
        capability: Literal["standard", "thinking", "vision"] = "standard",
        require_json_output: bool = False,
        task_priority: str = "default",
        task_label: Optional[str] = None,
) -> Dict[str, Any]:
    selected_platform_config = _get_selected_platform_config(capability)
    if not selected_platform_config:
        raise HTTPException(500, f"没有找到支持 '{capability}' 能力的已启用AI平台。")

    platform_name = selected_platform_config["name"]
    model_name = selected_platform_config["models"][capability]
    api_key = selected_platform_config["api_key"]
    platform_type = selected_platform_config["type"]
    can_force_json = selected_platform_config.get("can_force_json", {}).get(capability, False)
    prepared_messages = _prepare_chat_messages_for_platform(messages, capability=capability)

    async with ai_limiter.slot(priority=task_priority, label=task_label or f"call:{capability}"):
        print(f"[AI WORKER] 开始处理任务 (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")
        # print(f"[AI WORKER] 发送的 Messages: {json.dumps(messages, ensure_ascii=False, indent=2)}")

        if not api_key: raise HTTPException(500, f"未配置 {platform_name} 的 API_KEY")

        response_content = None
        try:
            if platform_type == "volcengine":
                if not AsyncArk: raise ImportError("volcenginesdkarkruntime 未安装")
                client = AsyncArk(api_key=api_key)
                completion = await client.chat.completions.create(model=model_name, messages=prepared_messages)
                response_content = completion.choices[0].message.content

            elif platform_type == "openai":
                if not AsyncOpenAI: raise ImportError("openai 库未安装")
                base_url = selected_platform_config["base_url"]
                # 设置超时时间，防止长时间等待
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
                kwargs = {"model": model_name, "messages": prepared_messages}

                if require_json_output and can_force_json:
                    print(f"[AI WORKER] 平台 {platform_name} 模型 {model_name} 支持强制JSON，正在启用。")
                    kwargs["response_format"] = {"type": "json_object"}
                elif require_json_output:
                    print(f"[AI WORKER] 平台 {platform_name} 模型 {model_name} 不支持强制JSON，将依赖提示词。")

                completion = await client.chat.completions.create(**kwargs)
                response_content = completion.choices[0].message.content

            else:
                raise HTTPException(500, f"不支持的平台类型: {platform_type}")

            print(f"[AI WORKER] {platform_name} 调用成功。")
            print(f"[AI WORKER] 原始响应内容: >>>\n{response_content}\n<<<")

            # --- 健壮的 JSON 解析 ---
            if not response_content:
                print("[ERROR] AI 返回了空内容。")
                raise HTTPException(500, "AI 返回空内容")

            response_content_cleaned = response_content.strip()
            if response_content_cleaned.startswith("```json"):
                response_content_cleaned = response_content_cleaned[7:]
                if response_content_cleaned.endswith("```"): response_content_cleaned = response_content_cleaned[:-3]
                response_content_cleaned = response_content_cleaned.strip()

            try:
                return json.loads(response_content_cleaned)
            except json.JSONDecodeError as e:
                print(f"[ERROR] 解析AI返回内容为JSON失败: {e}")
                if require_json_output:
                    raise HTTPException(500, f"AI未按要求返回有效的JSON格式: {e}")
                else:
                    return {"text": response_content}
            # --- 解析结束 ---

        except HTTPException as he:
            print(f"[ERROR] {platform_name} 处理失败: {he.detail}")
            raise he
        except Exception as e:
            print(f"[ERROR] {platform_name} 调用失败: {e}")
            print(traceback.format_exc())
            raise HTTPException(500, f"{platform_name} 调用失败: {e}")


async def _call_ai_platform_chat_stream_generator(
        system_prompt: str,
        messages: List[Dict],
        capability: Literal["standard", "thinking", "vision"] = "standard",
        task_priority: str = "interactive",
        task_label: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    (新) 专用于聊天流式输出的 AI 调用函数。
    它是一个异步生成器，逐块 yield 文本。
    它会处理 system_prompt 注入。
    """
    thinking_content = ""
    final_answer = ""
    thinking_start_sent = False
    thinking_end_sent = False

    # 构建最终发送给 AI 的消息列表
    final_messages = [
        {"role": "system", "content": system_prompt},
        *messages  # 添加所有历史消息
    ]

    selected_platform_config = _get_selected_platform_config(capability)
    if not selected_platform_config:
        error_msg = f"没有找到支持 '{capability}' 能力的已启用AI平台。"
        print(f"[ERROR] {error_msg}")
        yield error_msg
        return

    platform_name = selected_platform_config["name"]
    model_name = selected_platform_config["models"][capability]
    api_key = selected_platform_config["api_key"]
    platform_type = selected_platform_config["type"]
    prepared_messages = _prepare_chat_messages_for_platform(final_messages, capability=capability)

    async with ai_limiter.slot(priority=task_priority, label=task_label or f"stream:{capability}"):
        print(
            f"[AI WORKER] 开始处理流式聊天 (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")
        if not api_key:
            error_msg = f"未配置 {platform_name} 的 API_KEY"
            print(f"[ERROR] {error_msg}")
            yield error_msg
            return

        stream = None
        try:
            if platform_type == "volcengine":
                if not AsyncArk: raise ImportError("volcenginesdkarkruntime 未安装")
                # (注意: 火山/豆包的超时设置在客户端初始化时)
                client = AsyncArk(api_key=api_key, timeout=180.0)

                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=prepared_messages,
                    stream=True
                )
                async for chunk in stream:
                    # 检查火山引擎的流式响应结构
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                        final_answer += chunk.choices[0].delta.content
                        # 如果思考过程结束，发送结束标记
                        if thinking_content and not thinking_end_sent:
                            yield "【思考过程结束】"
                            thinking_end_sent = True
                        yield chunk.choices[0].delta.content
                    # (根据您的文档，火山推理模型可能有 reasoning_content)
                    if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[
                        0].delta.reasoning_content:
                        # 我们可以选择是否将思考过程也流式传输，这里暂时只打印
                        # print(f"[{platform_name} Reasoning]: {chunk.choices[0].delta.reasoning_content}")
                        # 发送思考过程开始标记（如果还没发送过）
                        thinking_content += chunk.choices[0].delta.reasoning_content
                        if thinking_content and not thinking_start_sent:
                            yield "【思考过程开始】"
                            thinking_start_sent = True
                        yield chunk.choices[0].delta.reasoning_content  # 如果需要显示思考过程，取消此行注释

            elif platform_type == "openai":  # (DeepSeek 和 SiliconFlow 都使用此类型)
                if not AsyncOpenAI: raise ImportError("openai 库未安装")
                base_url = selected_platform_config["base_url"]
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)

                kwargs = {
                    "model": model_name,
                    "messages": prepared_messages,
                    "stream": True
                }

                # (处理 SiliconFlow 的 DeepSeek-R1 推理模型)
                if "DeepSeek-R1" in model_name:
                    kwargs["extra_body"] = {"thinking_budget": 1024}

                stream = await client.chat.completions.create(**kwargs)

                async for chunk in stream:
                    # 检查 OpenAI 兼容的流式响应结构
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                        final_answer += chunk.choices[0].delta.content
                        # 如果思考过程结束，发送结束标记
                        if thinking_content and not thinking_end_sent:
                            yield "【思考过程结束】"
                            thinking_end_sent = True
                        yield chunk.choices[0].delta.content
                    # (根据您的文档，DeepSeek 推理模型有 reasoning_content)
                    if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[
                        0].delta.reasoning_content:
                        # print(f"[{platform_name} Reasoning]: {chunk.choices[0].delta.reasoning_content}")
                        # 发送思考过程开始标记（如果还没发送过）
                        thinking_content += chunk.choices[0].delta.reasoning_content
                        if thinking_content and not thinking_start_sent:
                            yield "【思考过程开始】"
                            thinking_start_sent = True
                        yield chunk.choices[0].delta.reasoning_content  # 如果需要显示思考过程，取消此行注释

            else:
                error_msg = f"不支持的平台类型: {platform_type}"
                print(f"[ERROR] {error_msg}")
                yield error_msg

        except Exception as e:
            print(f"[ERROR] {platform_name} 流式聊天调用失败: {e}")
            print(traceback.format_exc())
            yield f"\n[AI助手内部错误: {platform_name} 调用失败: {e}]"
        finally:
            print(f"[AI WORKER] {platform_name} 流式聊天结束。")


async def _call_ai_platform_chat_stream_events(
        system_prompt: str,
        messages: List[Dict],
        capability: Literal["standard", "thinking", "vision"] = "standard",
        task_priority: str = "interactive",
        task_label: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    thinking_content = ""
    final_answer = ""

    final_messages = [
        {"role": "system", "content": system_prompt},
        *messages
    ]

    selected_platform_config = _get_selected_platform_config(capability)
    if not selected_platform_config:
        error_msg = f"娌℃湁鎵惧埌鏀寔 '{capability}' 鑳藉姏鐨勫凡鍚敤AI骞冲彴銆?"
        print(f"[ERROR] {error_msg}")
        yield _encode_stream_event("error", message=error_msg)
        yield _encode_stream_event("done", has_thinking=False)
        return

    platform_name = selected_platform_config["name"]
    model_name = selected_platform_config["models"][capability]
    api_key = selected_platform_config["api_key"]
    platform_type = selected_platform_config["type"]
    thinking_supported = capability == "thinking"
    think_tag_parser = ThinkTagStreamParser() if thinking_supported else None
    prepared_messages = _prepare_chat_messages_for_platform(final_messages, capability=capability)

    async with ai_limiter.slot(priority=task_priority, label=task_label or f"stream_events:{capability}"):
        print(
            f"[AI WORKER] 寮€濮嬪鐞嗙粨鏋勫寲娴佸紡鑱婂ぉ (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")

        if not api_key:
            error_msg = f"鏈厤缃?{platform_name} 鐨?API_KEY"
            print(f"[ERROR] {error_msg}")
            yield _encode_stream_event("error", message=error_msg)
            yield _encode_stream_event("done", has_thinking=False)
            return

        thinking_end_sent = False

        def forward_segment(segment_type: str, text: str) -> list[str]:
            nonlocal thinking_content, final_answer, thinking_end_sent
            events: list[str] = []
            if not text:
                return events

            if segment_type == "thinking":
                thinking_content += text
                events.append(_encode_stream_event("thinking_delta", delta=text))
                return events

            if thinking_content and not thinking_end_sent:
                events.append(_encode_stream_event("thinking_end"))
                thinking_end_sent = True

            final_answer += text
            events.append(_encode_stream_event("answer_delta", delta=text))
            return events

        yield _encode_stream_event(
            "meta",
            platform=platform_name,
            model=model_name,
            capability=capability,
            thinking_supported=thinking_supported,
        )

        try:
            if platform_type == "volcengine":
                if not AsyncArk:
                    raise ImportError("volcenginesdkarkruntime 鏈畨瑁?")
                client = AsyncArk(api_key=api_key, timeout=180.0)
                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=prepared_messages,
                    stream=True
                )
            elif platform_type == "openai":
                if not AsyncOpenAI:
                    raise ImportError("openai 搴撴湭瀹夎")
                base_url = selected_platform_config["base_url"]
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
                kwargs = {
                    "model": model_name,
                    "messages": prepared_messages,
                    "stream": True,
                }
                if "DeepSeek-R1" in model_name:
                    kwargs["extra_body"] = {"thinking_budget": 1024}
                stream = await client.chat.completions.create(**kwargs)
            else:
                raise HTTPException(500, f"涓嶆敮鎸佺殑骞冲彴绫诲瀷: {platform_type}")

            async for chunk in stream:
                if not chunk.choices or not chunk.choices[0].delta:
                    continue

                delta = chunk.choices[0].delta
                reasoning_text, content_text = _extract_delta_parts(delta)

                if reasoning_text:
                    for event in forward_segment("thinking", reasoning_text):
                        yield event

                if content_text:
                    content_segments = (
                        think_tag_parser.feed(content_text)
                        if think_tag_parser else [("answer", content_text)]
                    )
                    for segment_type, segment_text in content_segments:
                        for event in forward_segment(segment_type, segment_text):
                            yield event

        except Exception as e:
            print(f"[ERROR] {platform_name} 缁撴瀯鍖栨祦寮忚亰澶╄皟鐢ㄥけ璐? {e}")
            print(traceback.format_exc())
            yield _encode_stream_event(
                "error",
                message=f"AI鍔╂墜鍐呴儴閿欒: {platform_name} 璋冪敤澶辫触: {e}",
            )
        finally:
            if think_tag_parser:
                for segment_type, segment_text in think_tag_parser.flush():
                    for event in forward_segment(segment_type, segment_text):
                        yield event

            if thinking_content and not thinking_end_sent:
                yield _encode_stream_event("thinking_end")

            yield _encode_stream_event(
                "done",
                has_thinking=bool(thinking_content.strip()),
                answer_chars=len(final_answer),
                thinking_chars=len(thinking_content),
            )


async def _call_ai_platform_chat(
        system_prompt: str,
        messages: List[Dict],
        capability: Literal["standard", "thinking", "vision"] = "standard",
        task_priority: str = "interactive",
        task_label: Optional[str] = None,
) -> str:
    """
    (新) 专用于聊天的 AI 调用函数，返回纯文本响应。
    它负责将 system_prompt 注入到 messages 列表中。
    """

    # 构建最终发送给 AI 的消息列表
    # (注意: 不同平台处理 system_prompt 的方式不同)
    final_messages = [
        {"role": "system", "content": system_prompt},
        *messages  # 添加所有历史消息
    ]

    selected_platform_config = _get_selected_platform_config(capability)
    if not selected_platform_config:
        raise HTTPException(500, f"没有找到支持 '{capability}' 能力的已启用AI平台。")

    platform_name = selected_platform_config["name"]
    model_name = selected_platform_config["models"][capability]
    api_key = selected_platform_config["api_key"]
    platform_type = selected_platform_config["type"]
    can_force_json = selected_platform_config.get("can_force_json", {}).get(capability, False)
    prepared_messages = _prepare_chat_messages_for_platform(final_messages, capability=capability)

    async with ai_limiter.slot(priority=task_priority, label=task_label or f"chat:{capability}"):
        print(f"[AI WORKER] 开始处理聊天 (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")
        if not api_key: raise HTTPException(500, f"未配置 {platform_name} 的 API_KEY")

        response_content = None
        try:
            if platform_type == "volcengine":
                if not AsyncArk: raise ImportError("volcenginesdkarkruntime 未安装")
                client = AsyncArk(api_key=api_key)

                # 火山方舟/豆包，system prompt 作为第一条消息
                completion = await client.chat.completions.create(model=model_name, messages=prepared_messages)
                response_content = completion.choices[0].message.content

            elif platform_type == "openai":
                if not AsyncOpenAI: raise ImportError("openai 库未安装")
                base_url = selected_platform_config["base_url"]
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)

                # 修改：对于OpenAI兼容平台，不再使用system参数，而是将system_prompt作为系统消息插入
                # 这样可以兼容更多平台（如SiliconFlow）
                completion = await client.chat.completions.create(
                    model=model_name,
                    messages=prepared_messages  # 直接使用包含system消息的完整消息列表
                )
                response_content = completion.choices[0].message.content

            else:
                raise HTTPException(500, f"不支持的平台类型: {platform_type}")

            print(f"[AI WORKER] {platform_name} 聊天调用成功。")
            return response_content or ""  # 返回纯文本

        except Exception as e:
            print(f"[ERROR] {platform_name} 聊天调用失败: {e}")
            print(traceback.format_exc())
            raise HTTPException(500, f"{platform_name} 聊天调用失败: {e}")


# --- API Endpoints (保持不变) ---
@app.post("/api/ai/generate-assignment")
async def generate_assignment_task(req: GenerationRequest):
    messages = [{"role": "system", "content": GENERATION_SYSTEM_PROMPT}, {"role": "user", "content": req.prompt}]
    return await _call_ai_platform(
        messages,
        capability=req.model_type,
        require_json_output=True,
        task_priority="default",
        task_label="generate_assignment",
    )


@app.post("/api/ai/generate-exam")
async def generate_exam_task(req: ExamGenerationRequest):
    """生成试卷题目（使用高级模型）"""
    # 构建系统提示词和用户提示词
    system_prompt = EXAM_GENERATION_SYSTEM_PROMPT
    user_prompt = req.prompt

    # 如果有课堂ID，可以添加更多上下文信息
    if req.class_offering_id:
        user_prompt = f"课堂ID: {req.class_offering_id}\n{user_prompt}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # 使用thinking模型（高级模型）生成试卷
    result = await _call_ai_platform(
        messages,
        capability=req.model_type,  # 使用thinking模型
        require_json_output=True,
        task_priority="default",
        task_label="generate_exam",
    )

    # 验证返回的数据结构
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="AI返回的数据格式不正确")

    # 确保有pages字段
    if "pages" not in result:
        # 尝试转换不同的格式
        if "questions" in result:
            # 如果是单个问题列表，转换为pages结构
            result = {"pages": [{"name": "试卷题目", "questions": result["questions"]}]}
        else:
            raise HTTPException(status_code=500, detail="AI返回的数据缺少'pages'字段")

    # 验证每个问题的结构
    for page in result["pages"]:
        if "questions" not in page:
            page["questions"] = []
        for question in page["questions"]:
            # 确保每个问题都有必需的字段
            if "id" not in question:
                question["id"] = f"q{hash(str(question)) % 10000}"  # 生成一个简单的ID
            if "type" not in question:
                question["type"] = "radio"  # 默认类型
            if "text" not in question:
                question["text"] = "题目内容未生成"

    return {
        "status": "success",
        "exam_data": result
    }


@app.post("/api/ai/chat-stream")
async def ai_chat_task_stream(req: AIChatRequest):
    """
    (新 V4.3) 处理通用的课堂 AI 聊天请求 (流式)
    """
    # 1. 构建新的用户消息 (可能包含图片)
    image_inputs = _normalize_request_image_inputs(req)
    new_user_message_content = _build_user_message_content(req.new_message, image_inputs)

    # 2. 将新消息添加到历史记录中
    history = list(req.messages or [])
    history.append({
        "role": "user",
        "content": new_user_message_content
    })

    # 3. 创建流式生成器
    stream_generator = _call_ai_platform_chat_stream_events(
        system_prompt=req.system_prompt,
        messages=history,  # 发送包含最新消息的完整历史
        capability=req.model_capability,
        task_priority=req.task_priority,
        task_label=req.task_label or "chat_stream",
    )

    # 4. 返回 StreamingResponse
    # (重要: 确保 UTF-8 编码)
    return StreamingResponse(
        stream_generator,
        media_type=STREAM_EVENT_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ai/chat")
async def ai_chat_task(req: AIChatRequest):
    """
    (新) 处理通用的课堂 AI 聊天请求
    """

    # 1. 构建新的用户消息 (可能包含图片)
    image_inputs = _normalize_request_image_inputs(req)
    new_user_message_content = _build_user_message_content(req.new_message, image_inputs)

    # 4. 将新消息添加到历史记录中
    # (注意: VolcEngine 和 OpenAI 都能处理这种多部分 "content" 列表)
    history = list(req.messages or [])
    history.append({
        "role": "user",
        "content": new_user_message_content
    })

    try:
        if req.response_format == "json":
            json_messages = [
                {"role": "system", "content": req.system_prompt},
                *history,
            ]
            ai_response_json = await _call_ai_platform(
                json_messages,
                capability=req.model_capability,
                require_json_output=True,
                task_priority=req.task_priority,
                task_label=req.task_label or "chat_json",
            )
            return {"status": "success", "response_json": ai_response_json}

        # 5. 调用 AI
        # (注意：_call_ai_platform_chat 会处理 system_prompt)
        ai_response_text = await _call_ai_platform_chat(
            system_prompt=req.system_prompt,
            messages=history,
            capability=req.model_capability,
            task_priority=req.task_priority,
            task_label=req.task_label or "chat_text",
        )

        # 6. 返回纯文本响应
        return {"status": "success", "response_text": ai_response_text}

    except Exception as e:
        # 捕获 _call_ai_platform_chat 中可能抛出的 HTTPException
        detail = getattr(e, 'detail', str(e))
        raise HTTPException(status_code=500, detail=f"AI 聊天处理失败: {detail}")


@app.post("/api/ai/submit-grading-job")
async def submit_grading_task(job: GradingJob):
    print(f"[AI SERVER] 收到批改任务 (Submission ID: {job.submission_id})，已加入后台处理。")
    # 这里不 await，让任务在后台运行
    asyncio.create_task(run_grading_job(job))
    return {"status": "queued", "submission_id": job.submission_id}


# --- 后台任务 (更新: 支持文件 + JSON 答案) ---
async def run_grading_job(job: GradingJob):
    callback_data = {}
    try:
        grading_files = _normalize_grading_files(job)
        for file_info in grading_files:
            file_info["category"] = _categorize_grading_file(file_info)

        has_files = bool(grading_files)
        has_answers = bool(job.answers_json)

        if not has_files and not has_answers:
            raise ValueError("没有找到可批改的内容（无文件也无答案）")

        unsupported_binary_files = [f["display_name"] for f in grading_files if f["category"] == "binary"]
        if unsupported_binary_files:
            raise ValueError(
                "以下附件类型当前无法直接传给 AI 识别: " + ", ".join(unsupported_binary_files[:10])
            )

        _validate_grading_file_limits(grading_files)
        execution = _select_grading_execution(grading_files)
        selected_capability: Literal["standard", "thinking", "vision"] = execution["capability"]
        selected_platform = execution["platform_config"]
        print(
            f"[AI WORKER] 将使用平台 {execution['platform_name']} / 能力 {selected_capability} / 模式 {execution['mode']}"
        )

        if execution["mode"] == "volcengine_responses":
            model_name = selected_platform["models"][selected_capability]
            api_key = selected_platform["api_key"]
            if not api_key:
                raise ValueError("火山方舟 API Key 未配置")
            result = await _call_volcengine_responses_api(
                model_name=model_name,
                api_key=api_key,
                input_payload=_build_volcengine_responses_input(
                    job.rubric_md,
                    grading_files,
                    job.requirements_md,
                    job.answers_json,
                ),
                task_priority="default",
                task_label=f"grading:{job.submission_id}",
            )
        else:
            messages = [{"role": "system", "content": GRADING_SYSTEM_PROMPT}]
            if execution["mode"] == "vision_messages" and has_files:
                file_paths = [file_info["path"] for file_info in grading_files]
                messages.extend(
                    build_vision_messages(
                        job.rubric_md,
                        file_paths,
                        selected_platform["type"],
                        job.requirements_md,
                        job.answers_json,
                    )
                )
            else:
                messages.extend(
                    _build_text_grading_message(
                        job.rubric_md,
                        grading_files,
                        job.requirements_md,
                        job.answers_json,
                    )
                )

            # 批改任务总是要求 JSON 输出
            result = await _call_ai_platform(
                messages,
                capability=selected_capability,
                require_json_output=True,
                task_priority="default",
                task_label=f"grading:{job.submission_id}",
            )

        callback_data = {
            "submission_id": job.submission_id, "status": "graded",
            "score": result.get("score"), "feedback_md": result.get("feedback_md")
        }
    except Exception as e:
        print(f"[ERROR] 批改任务 {job.submission_id} 失败: {e}")
        callback_data = {
            "submission_id": job.submission_id, "status": "grading_failed",
            "score": None, "feedback_md": f"AI 批改失败: {e}"
        }

    # --- 回调 main.py (保持不变) ---
    if not MAIN_APP_CALLBACK_URL:
        print("[ERROR] MAIN_APP_CALLBACK_URL 未设置，无法回调。")
        return
    try:
        print(f"[AI WORKER] 正在回调: {MAIN_APP_CALLBACK_URL}")
        if callback_data:
            await callback_client.post(MAIN_APP_CALLBACK_URL, json=callback_data, timeout=30.0)
            print(f"[AI WORKER] 回调成功 (Submission ID: {job.submission_id})")
        else:
            print(f"[ERROR] Callback data is empty for Submission ID: {job.submission_id}")

    except Exception as e:
        print(f"[ERROR] 回调 main.py 失败 (Submission ID: {job.submission_id}): {e}")



# --- 主程序入口 (保持不变) ---
if __name__ == "__main__":
    if not ENABLED_PLATFORMS: print("[ERROR] 没有在 .env 文件中启用任何 AI 平台，AI 助手无法工作。"); sys.exit(1)
    missing_libs = []
    if any(p['type'] == 'openai' for p in PLATFORMS_CONFIG.values() if
           p['enabled']) and not AsyncOpenAI: missing_libs.append("openai (`pip install openai`)")
    if any(p['type'] == 'volcengine' for p in PLATFORMS_CONFIG.values() if
           p['enabled']) and not AsyncArk: missing_libs.append(
        "volcenginesdkarkruntime (`pip install 'volcengine-python-sdk[ark]'`)")
    if any(p['models'].get('vision') for p in PLATFORMS_CONFIG.values() if p['enabled']):
        try:
            import PIL
        except ImportError:
            missing_libs.append("Pillow (`pip install Pillow`)")
    if missing_libs: print("[ERROR] 缺少必要的库:"); [print(f"- {lib}") for lib in missing_libs]; sys.exit(1)
    uvicorn.run("ai_assistant:app", host=AI_HOST, port=AI_PORT, log_level="info")
