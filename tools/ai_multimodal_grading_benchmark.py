#!/usr/bin/env python3
"""Reproducible cross-provider benchmark for LanShare multimodal grading.

The dataset is intentionally external to git.  It is expected to be an
anonymized export with a ``manifest.json`` and per-sample files.  API keys are
read from the repository ``.env`` file and are never written to the results.

Example:

    python tools/ai_multimodal_grading_benchmark.py run \
        --dataset .codex-temp/model-benchmark/dataset \
        --output .codex-temp/model-benchmark/run-2026-07-12

The runner appends one JSON object per call, so an interrupted run can be
resumed safely with the same command.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_assistant_doc_extract import extract_document_text  # noqa: E402


GRADING_SYSTEM_PROMPT = """
你是一个严格、公正的AI作业批改助教。
你的任务是根据提供的【作业要求】、【评分标准】和【学生提交内容】（可能是代码文件、文本答案、图片等），对作业进行批改。
请务必使用中文进行回复。
你必须严格按照以下 JSON 格式返回结果，不要包含任何额外解释、Markdown 代码块或无关字段：
{
  "score": <总分，整数，0-100>,
  "summary": "<总评，120字以内>",
  "questions": [
    {
      "question_no": <题号，整数，从1开始>,
      "question_id": "<题目ID；没有就写 q1、q2...>",
      "score": <本题得分，数字>,
      "max_score": <本题满分，数字；无法判断时写 null>,
      "deduction_points": "<扣分点描述；没有扣分点写‘无’>",
      "evaluation": "<本题简短评价>"
    }
  ]
}
评分要求：
1. score 必须与逐题得分整体一致，不得臆测未提供的附件内容。
2. 只根据评分标准和提交证据评分；看不清的证据必须明确说明，不得自行补全。
3. 每题只写本题得分、扣分点和简短评价，不要暴露完整参考答案。
4. 如果附件只能提供文件属性，只能判断提交形式，不得推断文件内部已经完成。
""".strip()


WEB_ASSIGNMENT_RUBRIC = """
## 固定评分量表（总分 100 分）

1. 身份信息（25分）：网页醒目位置同时出现姓名和学号；只出现其一得 10 分，均未出现得 0 分。
2. 搜索筛选控件（25分）：关键词、分类、最高预算、搜索按钮四项齐全得满分；每缺一项扣 6 分。
3. 页面内容完整度（25分）：有完整商品/业务内容列表得满分；只有搜索区而主体空白不得分。
4. 搜索筛选证据（15分）：截图能看出筛选输入、筛选后结果或其他足以证明筛选内容已实现的证据；仅有空控件最多 5 分。
5. 页面质量与数据正确性（10分）：布局清晰、可读、无明显空白或 null 等数据错误。明显数据错误扣 5-10 分。

只评价截图中可见的证据，不因猜测后台代码已经完成而加分。
""".strip()


RECOGNITION_PROMPTS = {
    "web": """
请只识别图片中客观可见的内容，不进行作业评分。返回严格 JSON：
{
  "identity_visible": <是否同时能看到姓名和学号，true/false>,
  "search_controls_visible": <是否能看到关键词、分类、预算和搜索按钮，true/false>,
  "product_card_count": <可见商品卡片数量，整数>,
  "seller_null_visible": <是否能看到卖家字段为 null，true/false>,
  "screenshot_usable": <截图是否足够清晰，可用于判断上述内容，true/false>
}
不要输出姓名、学号或任何个人信息。
""".strip(),
    "tcp": """
请只识别图片中客观可见的 TCP 流程，不进行作业评分。多张图应合并判断。返回严格 JSON：
{
  "handshake_visible": <是否包含三次握手，true/false>,
  "teardown_visible": <是否包含四次挥手，true/false>,
  "handshake_steps": <可见握手报文步骤数，整数，没有写0>,
  "teardown_steps": <可见挥手报文步骤数，整数，没有写0>,
  "has_sequence_ack_details": <是否明确标出 seq/ack 数值或变量关系，true/false>,
  "has_tcp_states": <是否明确标出 SYN-SENT、SYN-RCVD、FIN-WAIT、TIME-WAIT 等状态，true/false>
}
不要输出姓名、学号或任何个人信息。
""".strip(),
}


RECOGNITION_GOLD: dict[str, dict[str, Any]] = {
    "S001": {"identity_visible": False, "search_controls_visible": True, "product_card_count": 0, "seller_null_visible": False, "screenshot_usable": True},
    "S002": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 0, "seller_null_visible": False, "screenshot_usable": True},
    "S003": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 4, "seller_null_visible": True, "screenshot_usable": True},
    "S004": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 4, "seller_null_visible": False, "screenshot_usable": True},
    "S005": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 0, "seller_null_visible": False, "screenshot_usable": True},
    "S006": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 4, "seller_null_visible": True, "screenshot_usable": True},
    "S007": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 4, "seller_null_visible": True, "screenshot_usable": True},
    "S008": {"identity_visible": True, "search_controls_visible": True, "product_card_count": 4, "seller_null_visible": True, "screenshot_usable": True},
    "S009": {"handshake_visible": True, "teardown_visible": False, "handshake_steps": 3, "teardown_steps": 0, "has_sequence_ack_details": False, "has_tcp_states": False},
    "S010": {"handshake_visible": True, "teardown_visible": True, "handshake_steps": 3, "teardown_steps": 4, "has_sequence_ack_details": True, "has_tcp_states": False},
    "S011": {"handshake_visible": True, "teardown_visible": True, "handshake_steps": 3, "teardown_steps": 4, "has_sequence_ack_details": True, "has_tcp_states": True},
    "S012": {"handshake_visible": True, "teardown_visible": True, "handshake_steps": 3, "teardown_steps": 4, "has_sequence_ack_details": True, "has_tcp_states": True},
}


# Independently adjudicated after inspecting the submitted source files,
# structured answers and visible screenshot evidence.  Ranges are used instead
# of a single point because the original rubrics leave legitimate teacher
# discretion.  They deliberately do not use any provider's output as truth.
ADJUDICATED_SCORE_RANGES: dict[str, tuple[float, float]] = {
    # Two valid Python files, but the requested send/receive loop is absent.
    "S013": (50.0, 70.0),
    # 29/30 objective points; fragmentation calculation and comprehensive
    # answers contain material errors, so a near-perfect score is unjustified.
    "S017": (78.0, 88.0),
    # S should be 09+48=57 rather than 18; visible ping/NAT evidence also fails.
    "S019": (45.0, 65.0),
    # Objective answers are strong, but q9-q11 text is empty and cross-file
    # S/role evidence conflicts, so both a failing and a perfect score are harsh.
    "S020": (60.0, 80.0),
}


CATEGORY_SAMPLE_IDS: dict[str, frozenset[str]] = {
    "human_web": frozenset(f"S{index:03d}" for index in range(1, 9)),
    "tcp_visual": frozenset(f"S{index:03d}" for index in range(9, 13)),
    "code": frozenset(f"S{index:03d}" for index in range(13, 16)),
    "long_exam": frozenset(f"S{index:03d}" for index in range(16, 19)),
    "multi_lab": frozenset(f"S{index:03d}" for index in range(19, 21)),
}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    thinking: bool
    input_price_cny_per_million: float
    output_price_cny_per_million: float
    price_note: str


def model_specs() -> dict[str, ModelSpec]:
    return {
        "qwen37": ModelSpec(
            key="qwen37",
            provider="qwen",
            model="qwen3.7-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="QIANWEN_API_KEY",
            thinking=True,
            input_price_cny_per_million=1.6,
            output_price_cny_per_million=6.4,
            price_note="2026-07 中国内地 <=256K 限时8折；原价2/8元",
        ),
        "qwen36": ModelSpec(
            key="qwen36",
            provider="qwen",
            model="qwen3.6-flash",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="QIANWEN_API_KEY",
            thinking=False,
            input_price_cny_per_million=1.2,
            output_price_cny_per_million=7.2,
            price_note="2026-07 中国内地 <=256K 在线推理价",
        ),
        "glm46v": ModelSpec(
            key="glm46v",
            provider="glm",
            model="glm-4.6v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key_env="ZHIPU_API_KEY",
            thinking=True,
            input_price_cny_per_million=1.0,
            output_price_cny_per_million=3.0,
            price_note="2026-07 输入长度 <32K；超过32K为2/6元",
        ),
        "glm46v_flash": ModelSpec(
            key="glm46v_flash",
            provider="glm",
            model="glm-4.6v-flash",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key_env="ZHIPU_API_KEY",
            thinking=True,
            input_price_cny_per_million=0.0,
            output_price_cny_per_million=0.0,
            price_note="2026-07 官方免费模型",
        ),
        "doubao_pro": ModelSpec(
            key="doubao_pro",
            provider="doubao",
            model=os.getenv("VOLCENGINE_MODEL_MULTIMODAL_DEEP", "doubao-seed-2-1-pro-260628"),
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key_env="ARK_API_KEY",
            thinking=True,
            input_price_cny_per_million=6.0,
            output_price_cny_per_million=30.0,
            price_note="2026-07 火山方舟在线推理价",
        ),
        "doubao_turbo": ModelSpec(
            key="doubao_turbo",
            provider="doubao",
            model=os.getenv("VOLCENGINE_MODEL_MULTIMODAL_LIGHT", "doubao-seed-2-1-turbo-260628"),
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key_env="ARK_API_KEY",
            thinking=False,
            input_price_cny_per_million=3.0,
            output_price_cny_per_million=15.0,
            price_note="2026-07 火山方舟在线推理价",
        ),
    }


DEFAULT_MODELS = ("qwen37", "qwen36", "glm46v", "glm46v_flash", "doubao_pro", "doubao_turbo")
REPEAT_SAMPLES = frozenset({"S001", "S004", "S009", "S019"})


def load_dotenv_file(path: Path) -> None:
    """Load .env without returning or logging any secret values."""
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    except ImportError:
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_manifest(dataset: Path) -> dict[str, Any]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset manifest has no samples")
    return manifest


def anonymized_answers_text(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return str(raw)[:100_000]
    answers = payload.get("answers") if isinstance(payload, dict) else payload
    if not isinstance(answers, list):
        return json.dumps(answers, ensure_ascii=False)
    lines = ["【学生结构化答案】"]
    for index, item in enumerate(answers, start=1):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id") or f"q{index}")
        question = str(item.get("question") or "").strip()
        answer = item.get("answer")
        if isinstance(answer, (dict, list)):
            answer_text = json.dumps(answer, ensure_ascii=False)
        else:
            answer_text = str(answer or "")
        lines.append(f"- {question_id} 题目：{question}\n  学生答案：{answer_text}")
    return "\n".join(lines)


def rubric_for_sample(sample: dict[str, Any]) -> str:
    if int(sample.get("assignment_id") or 0) in {11, 12}:
        return WEB_ASSIGNMENT_RUBRIC
    rubric = str(sample.get("rubric_md") or "").strip()
    return rubric or "请严格根据作业要求和提交证据，按 100 分制评分。"


def _question_attachment_label(relative_path: str | None, index: int) -> str:
    normalized = str(relative_path or "").replace("\\", "/")
    match = re.search(r"exam_question_files/([^/]+)/", normalized)
    if match:
        return f"题目 {match.group(1)} 的附件 {index}"
    return f"附件 {index}"


def _data_url(path: Path, mime_type: str | None = None) -> str:
    mime = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _append_document_content(content: list[dict[str, Any]], path: Path, label: str) -> tuple[int, int]:
    result = extract_document_text(path, path.suffix.lower(), 2 * 1024 * 1024)
    text_chars = 0
    image_count = 0
    if result.text.strip():
        text = result.text.strip()
        content.append({"type": "text", "text": f"\n--- {label}（文档提取文本）---\n{text}"})
        text_chars += len(text)
    for image_index, image in enumerate(result.images, start=1):
        data_url = image.get("data_url") or ""
        if not data_url:
            continue
        content.append({"type": "text", "text": f"\n--- {label} 内嵌图片 {image_index} ---"})
        content.append({"type": "image_url", "image_url": {"url": data_url}})
        image_count += 1
    if not result.text.strip() and not result.images:
        content.append({"type": "text", "text": f"\n--- {label} ---\n[文档无法提取可读内容]"})
    return text_chars, image_count


def build_sample_content(
    sample: dict[str, Any],
    dataset: Path,
    *,
    task: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if task == "grading":
        header = "\n\n".join(
            part
            for part in (
                f"【作业要求】\n{str(sample.get('requirements_md') or '').strip()}",
                f"【评分标准】\n{rubric_for_sample(sample)}",
                anonymized_answers_text(sample.get("answers_json")),
                "【学生提交文件】",
            )
            if part.strip()
        )
    elif task == "recognition":
        assignment_id = int(sample.get("assignment_id") or 0)
        prompt_key = "web" if assignment_id in {11, 12} else "tcp"
        header = RECOGNITION_PROMPTS[prompt_key]
    else:
        raise ValueError(f"unsupported task: {task}")

    content: list[dict[str, Any]] = [{"type": "text", "text": header}]
    stats = {"text_chars": len(header), "image_count": 0, "file_bytes": 0}
    for index, file_info in enumerate(sample.get("files") or [], start=1):
        archive_path = str(file_info.get("archive_path") or "")
        path = dataset / archive_path
        if not path.is_file():
            continue
        label = _question_attachment_label(file_info.get("relative_path"), index)
        suffix = path.suffix.lower()
        stats["file_bytes"] += path.stat().st_size
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            content.append({"type": "text", "text": f"\n--- {label} ---"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(path, file_info.get("mime_type"))},
                }
            )
            stats["image_count"] += 1
        elif suffix in {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf"}:
            text_chars, image_count = _append_document_content(content, path, label)
            stats["text_chars"] += text_chars
            stats["image_count"] += image_count
        elif suffix in {
            ".c", ".cpp", ".css", ".csv", ".html", ".ini", ".java", ".js", ".json", ".log", ".md",
            ".php", ".py", ".sh", ".sql", ".svg", ".toml", ".ts", ".txt", ".xml", ".yaml", ".yml",
        }:
            text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
            content.append({"type": "text", "text": f"\n--- {label}（{suffix}）---\n{text}"})
            stats["text_chars"] += len(text)
        else:
            content.append(
                {
                    "type": "text",
                    "text": f"\n--- {label} ---\n[仅提供属性：类型 {suffix or '未知'}，大小 {path.stat().st_size} 字节]",
                }
            )
    return content, stats


def parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE):
        candidates.insert(0, match.group(1))
    first = raw.find("{")
    last = raw.rfind("}")
    if 0 <= first < last:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def normalized_score(parsed: dict[str, Any] | None) -> float | None:
    if not parsed:
        return None
    try:
        value = float(parsed.get("score"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return min(100.0, max(0.0, value))


def score_recognition(parsed: dict[str, Any] | None, sample_id: str) -> tuple[int, int]:
    gold = RECOGNITION_GOLD.get(sample_id)
    if not gold:
        return 0, 0
    if not parsed:
        return 0, len(gold)
    correct = 0
    for key, expected in gold.items():
        actual = parsed.get(key)
        if isinstance(expected, bool):
            if actual is expected:
                correct += 1
        elif isinstance(expected, int):
            try:
                if int(actual) == expected:
                    correct += 1
            except (TypeError, ValueError):
                pass
        elif actual == expected:
            correct += 1
    return correct, len(gold)


def _thinking_extra(spec: ModelSpec) -> dict[str, Any]:
    if spec.provider == "qwen":
        return {"enable_thinking": spec.thinking}
    return {"thinking": {"type": "enabled" if spec.thinking else "disabled"}}


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if hasattr(usage, key)
    }


def call_model(
    spec: ModelSpec,
    *,
    system_prompt: str,
    content: list[dict[str, Any]],
    timeout_seconds: float,
) -> tuple[str, dict[str, Any], float, int]:
    from openai import OpenAI

    api_key = os.getenv(spec.api_key_env)
    if not api_key:
        raise RuntimeError(f"missing API key environment variable: {spec.api_key_env}")
    client = OpenAI(api_key=api_key, base_url=spec.base_url, timeout=timeout_seconds, max_retries=0)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=spec.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                temperature=0,
                max_tokens=8192,
                extra_body=_thinking_extra(spec),
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            message = response.choices[0].message
            return str(message.content or ""), _usage_dict(response.usage), latency_ms, attempt
        except Exception as exc:  # provider SDK exceptions differ
            last_error = exc
            if attempt >= 3:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    raise last_error


def estimated_cost(spec: ModelSpec, usage: dict[str, Any]) -> float:
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    input_price = spec.input_price_cny_per_million
    output_price = spec.output_price_cny_per_million
    if spec.key == "glm46v" and prompt_tokens > 32_000:
        input_price, output_price = 2.0, 6.0
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


def _result_key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(row.get("model_key")),
        str(row.get("sample_id")),
        str(row.get("task")),
        int(row.get("repeat") or 0),
    )


def load_existing_results(path: Path) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    rows: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows[_result_key(row)] = row
    return rows


def load_result_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


def append_result(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
        handle.flush()


def selected_samples(manifest: dict[str, Any], requested: set[str] | None = None) -> list[dict[str, Any]]:
    samples = list(manifest["samples"])
    if requested:
        samples = [sample for sample in samples if str(sample.get("sample_id")) in requested]
    return samples


def run_benchmark(args: argparse.Namespace) -> int:
    load_dotenv_file(REPO_ROOT / ".env")
    specs = model_specs()
    unknown = [key for key in args.models if key not in specs]
    if unknown:
        raise ValueError(f"unknown model keys: {', '.join(unknown)}")
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(dataset)
    sample_filter = set(args.samples) if args.samples else None
    samples = selected_samples(manifest, sample_filter)
    results_path = output / "results.jsonl"
    existing = load_existing_results(results_path)
    prompt_hash = hashlib.sha256(GRADING_SYSTEM_PROMPT.encode("utf-8")).hexdigest()

    work: list[tuple[ModelSpec, dict[str, Any], str, int]] = []
    for model_key in args.models:
        spec = specs[model_key]
        for sample in samples:
            sample_id = str(sample["sample_id"])
            if "grading" in args.tasks:
                repeats = args.anchor_repeats if sample_id in REPEAT_SAMPLES else 1
                for repeat in range(repeats):
                    work.append((spec, sample, "grading", repeat))
            if "recognition" in args.tasks and sample_id in RECOGNITION_GOLD:
                work.append((spec, sample, "recognition", 0))

    completed = 0
    for index, (spec, sample, task, repeat) in enumerate(work, start=1):
        key = (spec.key, str(sample["sample_id"]), task, repeat)
        if key in existing and existing[key].get("status") == "success":
            print(f"[{index}/{len(work)}] skip {key}", flush=True)
            completed += 1
            continue
        content, request_stats = build_sample_content(sample, dataset, task=task)
        system_prompt = GRADING_SYSTEM_PROMPT if task == "grading" else "你是严谨的视觉证据识别器，只输出要求的 JSON。"
        row: dict[str, Any] = {
            "model_key": spec.key,
            "provider": spec.provider,
            "model": spec.model,
            "sample_id": sample["sample_id"],
            "assignment_id": sample.get("assignment_id"),
            "reference_source": sample.get("reference_source"),
            "reference_score": sample.get("reference_score"),
            "task": task,
            "repeat": repeat,
            "thinking": spec.thinking,
            "request_stats": request_stats,
            "prompt_sha256": prompt_hash if task == "grading" else None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        print(f"[{index}/{len(work)}] {spec.key} {sample['sample_id']} {task} r{repeat}", flush=True)
        try:
            raw_output, usage, latency_ms, attempts = call_model(
                spec,
                system_prompt=system_prompt,
                content=content,
                timeout_seconds=args.timeout,
            )
            parsed = parse_json_object(raw_output)
            row.update(
                {
                    "status": "success",
                    "latency_ms": latency_ms,
                    "attempts": attempts,
                    "usage": usage,
                    "estimated_cost_cny": round(estimated_cost(spec, usage), 8),
                    "raw_output": raw_output,
                    "parsed": parsed,
                    "json_valid": parsed is not None,
                }
            )
            if task == "grading":
                row["score"] = normalized_score(parsed)
                row["absolute_error"] = (
                    abs(float(row["score"]) - float(sample["reference_score"]))
                    if row["score"] is not None and sample.get("reference_score") is not None
                    else None
                )
            else:
                correct, total = score_recognition(parsed, str(sample["sample_id"]))
                row["recognition_correct"] = correct
                row["recognition_total"] = total
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "latency_ms": None,
                    "attempts": 3,
                    "usage": {},
                    "estimated_cost_cny": 0.0,
                    "json_valid": False,
                }
            )
        append_result(results_path, row)
        existing[key] = row
        completed += 1
    print(f"completed={completed} results={results_path}", flush=True)
    summarize_results(results_path, output, specs)
    return 0


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rank(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for offset in range(index, end):
            ranks[indexed[offset][0]] = average_rank
        index = end
    return ranks


def pearson(values_x: Sequence[float], values_y: Sequence[float]) -> float | None:
    if len(values_x) != len(values_y) or len(values_x) < 2:
        return None
    mean_x = statistics.fmean(values_x)
    mean_y = statistics.fmean(values_y)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(values_x, values_y))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in values_x) * sum((y - mean_y) ** 2 for y in values_y)
    )
    return numerator / denominator if denominator else None


def spearman(values_x: Sequence[float], values_y: Sequence[float]) -> float | None:
    return pearson(_rank(values_x), _rank(values_y))


def _aggregate_model(rows: list[dict[str, Any]], spec: ModelSpec) -> dict[str, Any]:
    success = [row for row in rows if row.get("status") == "success"]
    primary_grading = [row for row in success if row.get("task") == "grading" and int(row.get("repeat") or 0) == 0]
    human = [row for row in primary_grading if row.get("reference_source") == "human_teacher" and row.get("score") is not None]
    historical = [row for row in primary_grading if row.get("reference_source") != "human_teacher" and row.get("score") is not None]
    recognition = [row for row in success if row.get("task") == "recognition"]
    latencies = [float(row["latency_ms"]) for row in success if row.get("latency_ms") is not None]
    json_rows = [row for row in success if row.get("task") in {"grading", "recognition"}]
    prompt_tokens = sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in success)
    completion_tokens = sum(int((row.get("usage") or {}).get("completion_tokens") or 0) for row in success)
    result: dict[str, Any] = {
        "model_key": spec.key,
        "provider": spec.provider,
        "model": spec.model,
        "thinking": spec.thinking,
        "calls": len(rows),
        "successful_calls": len(success),
        "error_calls": len(rows) - len(success),
        "json_valid_rate": (
            sum(1 for row in json_rows if row.get("json_valid")) / len(json_rows) if json_rows else None
        ),
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_cny": round(sum(float(row.get("estimated_cost_cny") or 0) for row in success), 6),
        "input_price_cny_per_million": spec.input_price_cny_per_million,
        "output_price_cny_per_million": spec.output_price_cny_per_million,
        "price_note": spec.price_note,
    }
    for label, subset in (("human", human), ("historical", historical)):
        errors = [abs(float(row["score"]) - float(row["reference_score"])) for row in subset]
        predicted = [float(row["score"]) for row in subset]
        reference = [float(row["reference_score"]) for row in subset]
        result[f"{label}_samples"] = len(subset)
        result[f"{label}_mae"] = statistics.fmean(errors) if errors else None
        result[f"{label}_rmse"] = math.sqrt(statistics.fmean([error**2 for error in errors])) if errors else None
        result[f"{label}_within_5"] = sum(error <= 5 for error in errors) / len(errors) if errors else None
        result[f"{label}_within_10"] = sum(error <= 10 for error in errors) / len(errors) if errors else None
        result[f"{label}_spearman"] = spearman(predicted, reference) if errors else None
        result[f"{label}_bias"] = statistics.fmean(
            [float(row["score"]) - float(row["reference_score"]) for row in subset]
        ) if errors else None
    recognition_correct = sum(int(row.get("recognition_correct") or 0) for row in recognition)
    recognition_total = sum(int(row.get("recognition_total") or 0) for row in recognition)
    result["recognition_accuracy"] = recognition_correct / recognition_total if recognition_total else None
    result["recognition_correct"] = recognition_correct
    result["recognition_total"] = recognition_total

    repeats_by_sample: dict[str, list[float]] = {}
    for row in success:
        if row.get("task") != "grading" or row.get("score") is None:
            continue
        repeats_by_sample.setdefault(str(row.get("sample_id")), []).append(float(row["score"]))
    repeat_ranges = [max(values) - min(values) for values in repeats_by_sample.values() if len(values) >= 2]
    repeat_stdevs = [statistics.pstdev(values) for values in repeats_by_sample.values() if len(values) >= 2]
    result["stability_anchor_samples"] = len(repeat_ranges)
    result["stability_mean_range"] = statistics.fmean(repeat_ranges) if repeat_ranges else None
    result["stability_mean_stdev"] = statistics.fmean(repeat_stdevs) if repeat_stdevs else None

    adjudicated_rows = [
        row
        for row in primary_grading
        if str(row.get("sample_id")) in ADJUDICATED_SCORE_RANGES and row.get("score") is not None
    ]
    adjudicated_distances = []
    for row in adjudicated_rows:
        lower, upper = ADJUDICATED_SCORE_RANGES[str(row["sample_id"])]
        score = float(row["score"])
        distance = lower - score if score < lower else score - upper if score > upper else 0.0
        adjudicated_distances.append(distance)
    result["adjudicated_samples"] = len(adjudicated_rows)
    result["adjudicated_hit_rate"] = (
        sum(distance == 0 for distance in adjudicated_distances) / len(adjudicated_distances)
        if adjudicated_distances
        else None
    )
    result["adjudicated_mean_distance"] = (
        statistics.fmean(adjudicated_distances) if adjudicated_distances else None
    )

    categories: dict[str, Any] = {}
    for category, sample_ids in CATEGORY_SAMPLE_IDS.items():
        subset = [row for row in primary_grading if str(row.get("sample_id")) in sample_ids]
        subset_latencies = [float(row["latency_ms"]) for row in subset if row.get("latency_ms") is not None]
        subset_errors = [
            abs(float(row["score"]) - float(row["reference_score"]))
            for row in subset
            if row.get("score") is not None and row.get("reference_score") is not None
        ]
        categories[category] = {
            "samples": len(subset),
            "latency_p50_ms": percentile(subset_latencies, 0.50),
            "latency_max_ms": max(subset_latencies) if subset_latencies else None,
            "prompt_tokens": sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in subset),
            "completion_tokens": sum(int((row.get("usage") or {}).get("completion_tokens") or 0) for row in subset),
            "estimated_cost_cny": round(sum(float(row.get("estimated_cost_cny") or 0) for row in subset), 6),
            "reference_mae": statistics.fmean(subset_errors) if subset_errors else None,
        }
    result["categories"] = categories
    return result


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def summarize_results(results_path: Path, output: Path, specs: dict[str, ModelSpec] | None = None) -> dict[str, Any]:
    specs = specs or model_specs()
    events = load_result_events(results_path)
    latest = load_existing_results(results_path)
    rows = list(latest.values())
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_model.setdefault(str(row.get("model_key")), []).append(row)
    models = [
        _aggregate_model(model_rows, specs[key])
        for key, model_rows in by_model.items()
        if key in specs
    ]
    models.sort(
        key=lambda item: (
            -(item.get("recognition_accuracy") or 0),
            item.get("human_mae") if item.get("human_mae") is not None else 999,
            item.get("estimated_cost_cny") or 0,
        )
    )
    for model in models:
        model_key = str(model["model_key"])
        model_events = [event for event in events if str(event.get("model_key")) == model_key]
        error_events = [event for event in model_events if event.get("status") == "error"]
        error_keys = {_result_key(event) for event in error_events}
        recovered = sum(1 for key in error_keys if latest.get(key, {}).get("status") == "success")
        model["observed_error_events"] = len(error_events)
        model["recovered_error_keys"] = recovered
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "result_rows": len(rows),
        "result_events": len(events),
        "models": models,
        "adjudicated_score_ranges": {
            sample_id: {"min": bounds[0], "max": bounds[1]}
            for sample_id, bounds in ADJUDICATED_SCORE_RANGES.items()
        },
        "method_notes": [
            "human_teacher 表示人工批改模式中最终落库的教师分数；历史反馈文字可能经过AI辅助，不能视为完全独立盲评。",
            "historical 指标只衡量与历史线上分数的一致性，不用于证明真实评分质量。",
            "识别准确率来自12份样本的人工字段级金标准，不包含姓名或学号文本。",
            "成本按厂商返回Token和2026-07公开单价估算，不等于最终账单。",
            "S013/S017/S019/S020 另有基于源文件和可见证据的独立合理分数区间，用于发现历史分本身的偏差。",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    headers = [
        "模型", "成功/总调用", "观测错误/恢复", "JSON有效率", "人工分MAE", "人工±10", "独立复核命中", "识别准确率",
        "稳定性范围", "P50延迟(ms)", "P95延迟(ms)", "Token(入/出)", "估算成本(元)",
    ]
    lines = ["# 多模态批改基准摘要", "", "| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for item in models:
        cells = [
            f"{item['model_key']} ({item['model']})",
            f"{item['successful_calls']}/{item['calls']}",
            f"{item['observed_error_events']}/{item['recovered_error_keys']}",
            _fmt((item.get("json_valid_rate") or 0) * 100, 1) + "%",
            _fmt(item.get("human_mae")),
            _fmt((item.get("human_within_10") or 0) * 100, 1) + "%",
            _fmt((item.get("adjudicated_hit_rate") or 0) * 100, 1) + "%",
            _fmt((item.get("recognition_accuracy") or 0) * 100, 1) + "%",
            _fmt(item.get("stability_mean_range")),
            _fmt(item.get("latency_p50_ms"), 0),
            _fmt(item.get("latency_p95_ms"), 0),
            f"{item['prompt_tokens']}/{item['completion_tokens']}",
            _fmt(item.get("estimated_cost_cny"), 4),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## 解释边界", ""] + [f"- {note}" for note in summary["method_notes"]])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def summarize_command(args: argparse.Namespace) -> int:
    load_dotenv_file(REPO_ROOT / ".env")
    summary = summarize_results(args.results.resolve(), args.output.resolve())
    print(json.dumps({"models": len(summary["models"]), "rows": summary["result_rows"]}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run or resume provider calls")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    run.add_argument("--tasks", nargs="+", choices=("grading", "recognition"), default=["grading", "recognition"])
    run.add_argument("--samples", nargs="+")
    run.add_argument("--anchor-repeats", type=int, default=3)
    run.add_argument("--timeout", type=float, default=300.0)
    run.set_defaults(func=run_benchmark)

    summarize_parser = subparsers.add_parser("summarize", help="rebuild summary from an existing JSONL")
    summarize_parser.add_argument("--results", type=Path, required=True)
    summarize_parser.add_argument("--output", type=Path, required=True)
    summarize_parser.set_defaults(func=summarize_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
