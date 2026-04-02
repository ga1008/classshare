# ==============================================================================
# AI 助教服务 (ai_assistant.py - V3.3.3 Dynamic Model Selection, Better Prompts)
# ==============================================================================
import asyncio
import base64
import json
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
from pydantic import BaseModel

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

# --- AI 配置 (保持不变) ---
AI_HOST = os.getenv("AI_HOST", "127.0.0.1")
AI_PORT = int(os.getenv("AI_PORT", 8001))
GLOBAL_AI_CONCURRENCY = int(os.getenv("GLOBAL_AI_CONCURRENCY", 3))
MAIN_APP_CALLBACK_URL = os.getenv("MAIN_APP_CALLBACK_URL")
PLATFORM_PRIORITY = [p.strip() for p in os.getenv("AI_PLATFORM_PRIORITY", "siliconflow,volcengine,deepseek").split(',')]

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
        "models": {
            "standard": os.getenv("VOLCENGINE_MODEL_STANDARD", "doubao-1-5-pro-32k-250115"),
            "thinking": os.getenv("VOLCENGINE_MODEL_THINKING", "doubao-1-5-pro-32k-250115"),
            "vision": os.getenv("VOLCENGINE_MODEL_VISION", "doubao-1-5-vision-pro-32k-250115")
        },
        "can_force_json": {
            "standard": False, "thinking": False, "vision": False
        },
        "type": "volcengine",
    }
}
ENABLED_PLATFORMS = [p for p in PLATFORM_PRIORITY if p in PLATFORMS_CONFIG and PLATFORMS_CONFIG[p]["enabled"]]

# --- 全局信号量和HTTP客户端 (保持不变) ---
ai_semaphore = asyncio.Semaphore(GLOBAL_AI_CONCURRENCY)
callback_client = httpx.AsyncClient()


# --- Pydantic 模型 (去除 model_type 默认值) ---
class GenerationRequest(BaseModel):
    prompt: str
    model_type: Literal["standard", "thinking"] = "standard"


class GradingJob(BaseModel):
    submission_id: int
    rubric_md: str
    requirements_md: str = ""
    file_paths: List[str] = []
    answers_json: Optional[str] = None
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


class AIChatRequest(BaseModel):
    system_prompt: str
    messages: List[Dict[str, Any]]  # 历史消息, 格式: {"role": "user", "content": "..."}
    new_message: str  # 用户的最新文本输入
    base64_urls: List[str] = []  # 新上传的图片 (base64 data URLs)
    model_capability: Literal["standard", "thinking", "vision"] = "standard"


# --- 辅助函数 (保持不变) ---
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


def build_vision_messages(rubric: str, files: List[Path], platform_type: str,
                          requirements_md: str = "", answers_json: str = None) -> List[Dict[str, Any]]:
    """构建视觉消息 (支持文件 + JSON 答案混合)"""
    # 构建 JSON 答案的文本描述
    answers_text = ""
    if answers_json:
        try:
            answers_data = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
            answers = answers_data.get("answers", answers_data)
            if isinstance(answers, list):
                answers_text = "【学生文字答案】\n"
                for i, item in enumerate(answers):
                    q = item.get("question", f"第 {i+1} 题")
                    a = item.get("answer", item.get("content", item.get("text", "")))
                    answers_text += f"\n### {q}\n{a}\n"
            elif isinstance(answers, dict):
                answers_text = "【学生文字答案】\n"
                for key, value in answers.items():
                    answers_text += f"\n### {key}\n{value}\n"
        except (json.JSONDecodeError, AttributeError):
            answers_text = f"\n【学生文字答案】\n{answers_json}\n"

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
        require_json_output: bool = False
) -> Dict[str, Any]:
    selected_platform_config = _get_selected_platform_config(capability)
    if not selected_platform_config:
        raise HTTPException(500, f"没有找到支持 '{capability}' 能力的已启用AI平台。")

    platform_name = selected_platform_config["name"]
    model_name = selected_platform_config["models"][capability]
    api_key = selected_platform_config["api_key"]
    platform_type = selected_platform_config["type"]
    can_force_json = selected_platform_config.get("can_force_json", {}).get(capability, False)

    async with ai_semaphore:
        print(f"[AI WORKER] 开始处理任务 (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")
        # print(f"[AI WORKER] 发送的 Messages: {json.dumps(messages, ensure_ascii=False, indent=2)}")

        if not api_key: raise HTTPException(500, f"未配置 {platform_name} 的 API_KEY")

        response_content = None
        try:
            if platform_type == "volcengine":
                if not AsyncArk: raise ImportError("volcenginesdkarkruntime 未安装")
                client = AsyncArk(api_key=api_key)
                completion = await client.chat.completions.create(model=model_name, messages=messages)
                response_content = completion.choices[0].message.content

            elif platform_type == "openai":
                if not AsyncOpenAI: raise ImportError("openai 库未安装")
                base_url = selected_platform_config["base_url"]
                # 设置超时时间，防止长时间等待
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
                kwargs = {"model": model_name, "messages": messages}

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
        capability: Literal["standard", "thinking", "vision"] = "standard"
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

    async with ai_semaphore:
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
                    messages=final_messages,
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
                    "messages": final_messages,
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


async def _call_ai_platform_chat(
        system_prompt: str,
        messages: List[Dict],
        capability: Literal["standard", "thinking", "vision"] = "standard"
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

    async with ai_semaphore:
        print(f"[AI WORKER] 开始处理聊天 (Platform: {platform_name}, Model: {model_name}, Capability: {capability})")
        if not api_key: raise HTTPException(500, f"未配置 {platform_name} 的 API_KEY")

        response_content = None
        try:
            if platform_type == "volcengine":
                if not AsyncArk: raise ImportError("volcenginesdkarkruntime 未安装")
                client = AsyncArk(api_key=api_key)

                # 火山方舟/豆包，system prompt 作为第一条消息
                completion = await client.chat.completions.create(model=model_name, messages=final_messages)
                response_content = completion.choices[0].message.content

            elif platform_type == "openai":
                if not AsyncOpenAI: raise ImportError("openai 库未安装")
                base_url = selected_platform_config["base_url"]
                client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=180.0)

                # 修改：对于OpenAI兼容平台，不再使用system参数，而是将system_prompt作为系统消息插入
                # 这样可以兼容更多平台（如SiliconFlow）
                completion = await client.chat.completions.create(
                    model=model_name,
                    messages=final_messages  # 直接使用包含system消息的完整消息列表
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
    return await _call_ai_platform(messages, capability=req.model_type, require_json_output=True)


@app.post("/api/ai/chat-stream")
async def ai_chat_task_stream(req: AIChatRequest):
    """
    (新 V4.3) 处理通用的课堂 AI 聊天请求 (流式)
    """
    # 1. 构建新的用户消息 (可能包含图片)
    # (注意: VolcEngine 和 OpenAI 都能处理这种多部分 "content" 列表)
    new_user_message_content = []
    new_user_message_content.append({"type": "text", "text": req.new_message})

    for b64_url in req.base64_urls:
        if "base64," in b64_url:
            new_user_message_content.append({
                "type": "image_url",
                "image_url": {"url": b64_url}
            })

    # 2. 将新消息添加到历史记录中
    history = req.messages
    history.append({
        "role": "user",
        "content": new_user_message_content
    })

    # 3. 创建流式生成器
    stream_generator = _call_ai_platform_chat_stream_generator(
        system_prompt=req.system_prompt,
        messages=history,  # 发送包含最新消息的完整历史
        capability=req.model_capability
    )

    # 4. 返回 StreamingResponse
    # (重要: 确保 UTF-8 编码)
    return StreamingResponse(stream_generator, media_type="text/plain; charset=utf-8")


@app.post("/api/ai/chat")
async def ai_chat_task(req: AIChatRequest):
    """
    (新) 处理通用的课堂 AI 聊天请求
    """

    # 1. 构建新的用户消息 (可能包含图片)
    new_user_message_content = []

    # 2. 添加文本
    new_user_message_content.append({
        "type": "text",
        "text": req.new_message
    })

    # 3. 添加图片
    for b64_url in req.base64_urls:
        if "base64," in b64_url:
            new_user_message_content.append({
                "type": "image_url",
                "image_url": {"url": b64_url}
            })

    # 4. 将新消息添加到历史记录中
    # (注意: VolcEngine 和 OpenAI 都能处理这种多部分 "content" 列表)
    history = req.messages
    history.append({
        "role": "user",
        "content": new_user_message_content
    })

    # 5. 调用 AI
    # (注意：_call_ai_platform_chat 会处理 system_prompt)
    try:
        ai_response_text = await _call_ai_platform_chat(
            system_prompt=req.system_prompt,
            messages=history,
            capability=req.model_capability
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
    selected_capability: Literal["standard", "thinking", "vision"] = "thinking"  # 默认使用 thinking
    try:
        file_paths = [Path(p) for p in job.file_paths if Path(p).exists()]
        has_files = bool(file_paths)
        has_answers = bool(job.answers_json)

        if not has_files and not has_answers:
            raise ValueError("没有找到可批改的内容（无文件也无答案）")

        # 检查是否有图片文件
        has_image = has_files and any(is_image_file(fp) for fp in file_paths)
        if has_image:
            selected_capability = "vision"
            print(f"[AI WORKER] 检测到图片文件，将使用 '{selected_capability}' 能力。")
        else:
            print(f"[AI WORKER] 将使用 '{selected_capability}' 能力。")

        selected_platform = _get_selected_platform_config(selected_capability)
        if not selected_platform: raise ValueError(f"没有找到支持 {selected_capability} 能力的平台")
        platform_type = selected_platform["type"]

        messages = [{"role": "system", "content": GRADING_SYSTEM_PROMPT}]

        if selected_capability == "vision" and has_files:
            # 图片类作业：构建视觉消息
            messages.extend(build_vision_messages(job.rubric_md, file_paths, platform_type,
                                                  job.requirements_md, job.answers_json))
        else:
            # 文本类作业：构建文本消息
            text_content = ""
            if job.requirements_md:
                text_content += f"【作业要求】\n{job.requirements_md}\n\n"
            text_content += f"【评分标准】\n{job.rubric_md}\n\n"

            # 添加 JSON 答案内容
            if has_answers:
                text_content += "【学生提交答案】\n"
                try:
                    answers_data = json.loads(job.answers_json) if isinstance(job.answers_json, str) else job.answers_json
                    answers = answers_data.get("answers", answers_data)
                    if isinstance(answers, list):
                        for i, item in enumerate(answers):
                            q = item.get("question", f"第 {i+1} 题")
                            a = item.get("answer", item.get("content", item.get("text", "")))
                            text_content += f"\n### {q}\n{a}\n"
                    elif isinstance(answers, dict):
                        for key, value in answers.items():
                            text_content += f"\n### {key}\n{value}\n"
                except (json.JSONDecodeError, AttributeError):
                    text_content += job.answers_json
                text_content += "\n"

            # 添加文件内容
            if has_files:
                text_content += "【学生提交文件】\n"
                for file_path in file_paths:
                    try:
                        code = file_path.read_text(encoding='utf-8', errors='ignore')
                        text_content += f"\n--- 文件: {file_path.name} ---\n```\n{code}\n```\n"
                    except Exception:
                        text_content += f"\n--- 文件: {file_path.name} (无法读取) ---\n"

            messages.append({"role": "user", "content": text_content})

        # 批改任务总是要求 JSON 输出
        result = await _call_ai_platform(messages, capability=selected_capability, require_json_output=True)

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
