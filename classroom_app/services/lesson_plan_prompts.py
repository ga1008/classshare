"""Prompt constants for lesson-plan (教案) AI generation & import parsing.

The spec is distilled from the teacher-supplied 《教案编写规范》 (OBE + 两性一度 +
思政融入 + the mandatory cell format). Shared by both the per-session generator
and the multimodal import parser so the two paths produce the same structured
shape.
"""

from __future__ import annotations

import json
from typing import Any

# The pedagogy rulebook — kept faithful to the teacher's 教案编写提示.md so the
# generated 教学内容及过程 reads like the hand-written 第16章 samples.
PEDAGOGY_SPEC = """\
你是资深高校课程教学设计专家，精通 OBE 成果导向、"两性一度"(高阶性/创新性/挑战度) 与课程思政融入。
请严格遵循以下教案编写规范：

【核心理念】
- 成果导向(OBE)：由"教学目的和要求"(学生学完能做什么)逆向驱动；学生为中心，教师是引导者；成果可观测。
- 两性一度：
  · 高阶性——超越"是什么"，聚焦"为什么/怎么办"；善用强类比(如 DNS 是电话簿、SELinux 是安全标签)；以"排错"为核心教学环节。
  · 创新性——优先 PBL(问题驱动)、案例法、引导发现法；融合信息技术(Mermaid 图、AI 辅助)。
  · 挑战度——作业分层，为学有余力者设计综合性/研究性/架构性的 Pro 版作业。
- 思政融入：与专业知识自然结合(数字中国/数字主权/工匠精神/网络安全责任)，导入处立意、小结处升华，拒绝生硬。

【教学内容及过程(process)必须包含且按顺序】
一、教学导入(约8-10分钟)：回顾(承上启下)→引入(PBL/情景案例)→思政融入→目标(OBE)。
二、讲授新课：分 1-2 个小节，每个小节给出"核心理念"，并【必须用 Markdown 表格】，表头固定为：
   | 教学环节 | 教学活动（教师引导） | 学生活动（主体） | 设计意图（OBE & 两性一度） |
   表格内用"手把手/演示/提问/创设情景"描述教师活动，用"动手实践/自主排错/成果输出/结对协作"描述学生活动；
   适当主动设置"陷阱"(如 SELinux/防火墙/语法错误)引导排错；复杂流程/架构可附 Mermaid 代码块。
三、教学小结(约10分钟)：知识梳理(是什么/为什么/怎么做) + 反思与展望(呼应导入思政点、引出下次课)。
四、作业布置：作业(基础版-覆盖核心知识点，全体完成) + 作业 Pro 版(高阶性&挑战度，选做)，均写明"主题/要求/提交物"。

【用语】教案是正式材料，用语专业严谨。
"""

# JSON shape every per-session generation must return.
SESSION_OUTPUT_SCHEMA = {
    "objectives": "教学目的和要求：分『知识目标/能力目标/素养目标』三段，知识目标用『了解/掌握』，能力目标用『能够/熟练掌握』，素养目标用『培养…/树立…』。可用换行分隔。",
    "key_points": "教学重点：2-3 点，本次课必须掌握的核心技能或配置。",
    "difficulties": "教学难点：2-3 点，学生最易混淆/出错/需排错之处。",
    "methods": "教学方法：如 PBL项目驱动法、案例法、强类比法、引导发现法、手把手实践法 等(顿号分隔)。",
    "means": "教学手段：如 思维导图、Mermaid流程图、虚拟机、Xshell、AI辅助工具、PPT 等(顿号分隔)。",
    "process": "教学内容及过程：完整 Markdown，严格包含 一、教学导入 / 二、讲授新课(含规定表头的 Markdown 表格) / 三、教学小结 / 四、作业布置(基础版+Pro版)。",
    "side_notes": "旁批：教师课前准备提示与课堂注意事项(如『提前准备…镜像/确认虚拟化已开启』)，1-4 条，换行分隔；可留空。",
}


def build_generation_system_prompt() -> str:
    return (
        PEDAGOGY_SPEC
        + "\n你将收到一次课的课程信息与本次课对应的教学材料正文，请据此生成这次课的教案。\n"
        + "只输出一个 JSON 对象，键与含义如下(值均为字符串)：\n"
        + json.dumps(SESSION_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
        + "\nJSON mode requirement: return exactly one JSON object, no markdown fence, no explanation, no leading or trailing prose."
        + "\n不要输出 JSON 以外的任何内容，不要用代码块包裹。"
    )


def build_generation_user_message(
    *,
    cover: dict[str, Any],
    session_index: int,
    total_sessions: int,
    chapter: str,
    schedule_text: str,
    section_minutes: int,
    homework_hint: str = "",
    neighbor_context: str = "",
) -> str:
    lines = [
        f"课程名称：{cover.get('course_name') or '（未填写）'}",
        f"授课班级：{cover.get('class_name') or '（未填写）'}",
        f"使用教材：{cover.get('textbook') or '（未填写）'}",
        f"本次课为第 {session_index}/{total_sessions} 次课。",
        f"授课时间：{schedule_text or '（未排）'}",
        f"授课章节：{chapter or '（未指定，请根据教学材料自行拟定章节标题）'}",
        f"本次课时长约 {section_minutes} 分钟，请据此分配各环节时间。",
    ]
    if homework_hint:
        lines.append(f"本次课已布置的作业(可作为作业布置参考)：{homework_hint}")
    if neighbor_context:
        lines.append(f"前后课参考(用于承上启下与衔接)：{neighbor_context}")
    lines.append("请生成本次课教案的 JSON。")
    return "\n".join(lines)


def build_missing_doc_system_prompt() -> str:
    return (
        PEDAGOGY_SPEC
        + "\n本次课没有绑定教学材料。请根据课程信息与前后课内容，推断本次课应讲授的主题与知识要点。\n"
        + '只输出 JSON：{"chapter": "拟定的章节标题", "outline": "300-600字的本次课教学内容要点(用于后续生成教案)"}。'
        + "不要输出 JSON 以外内容。"
    )


def build_missing_doc_user_message(
    *, cover: dict[str, Any], session_index: int, total_sessions: int, neighbor_context: str
) -> str:
    return "\n".join(
        [
            f"课程名称：{cover.get('course_name') or '（未填写）'}",
            f"使用教材：{cover.get('textbook') or '（未填写）'}",
            f"本次课为第 {session_index}/{total_sessions} 次课。",
            f"前后课内容参考：\n{neighbor_context or '（无）'}",
            "请推断本次课的章节标题与教学要点 JSON。",
        ]
    )


# --- Import parsing ---------------------------------------------------------
IMPORT_OUTPUT_SCHEMA = {
    "cover": {
        "course_name": "课程名称",
        "course_category": "课程类别/性质(如 专业限选课程)",
        "credits": "学分",
        "total_hours": "学时",
        "teacher_name": "授课教师",
        "teaching_unit": "教学单位(学院/系部)",
        "class_name": "授课班级",
        "textbook": "使用教材名称",
        "publisher": "出版社",
        "semester_label": "学期(如 2025—2026学年第一学期)",
        "school_name": "学校名称",
    },
    "sessions": [
        {
            "schedule": {
                "date": "授课日期 YYYY-MM-DD(若有)",
                "week_index": "第几周(数字,可空)",
                "weekday": "星期几 1-7(数字,可空)",
                "sections": "第几节(如 6-7)",
                "text": "原文授课时间整串(如 2025年09月01日 第一周 星期一 第6-7节)",
            },
            "chapter": "授课章节",
            "objectives": "教学目的和要求(原文)",
            "key_points": "教学重点",
            "difficulties": "教学难点",
            "methods": "教学方法",
            "means": "教学手段",
            "process": "教学内容及过程(尽量保留 Markdown 结构与表格)",
            "side_notes": "旁批(若有)",
            "post_notes": "教学后记(若有)",
        }
    ],
}


def build_import_system_prompt(extra_hint: str = "") -> str:
    base = (
        "你是教案文档解析专家。下面给出一份《教案》文档的正文文本与/或页面图片(可能是 Word/PDF/图片导出)。\n"
        "请把它解析为结构化 JSON。文档通常含一页封面(课程名称/类别/学分/学时/授课教师/教学单位/授课班级/使用教材/出版社/学期)，"
        "以及每次课一张表格(授课时间/授课章节/教学目的和要求/教学重点和难点/教学方法和手段/教学内容及过程/旁批/教学后记)。\n"
        "请尽量完整还原每一次课，按出现顺序排列；教学内容及过程尽量保留 Markdown(表格用 Markdown 表格)。\n"
        "只输出一个 JSON 对象，结构如下：\n"
        + json.dumps(IMPORT_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
        + "\nJSON mode requirement: return exactly one JSON object, no markdown fence, no explanation, no JSON array, no leading or trailing prose."
        + "\n缺失字段填空字符串；不要编造原文没有的内容；不要输出 JSON 以外的任何内容。"
    )
    if extra_hint.strip():
        base += f"\n\n【用户补充提示】{extra_hint.strip()}"
    return base


def build_json_repair_system_prompt(schema_hint: dict[str, Any]) -> str:
    return (
        "You are a JSON repair worker. Extract useful data from the user's malformed AI output "
        "and return exactly one valid JSON object matching the target structure. Do not invent "
        "business content that is not present. Use empty strings or empty arrays when uncertain. "
        "Return JSON only, with no markdown fence or explanation.\n\n"
        "Target structure:\n"
        + json.dumps(schema_hint or {"cover": {}, "sessions": []}, ensure_ascii=False, indent=2)
    )


def build_session_draft_system_prompt() -> str:
    return (
        "You are a course-session planning assistant. Based on the teacher's topic hint, "
        "course context, and neighboring sessions, create one concise session card that can be "
        "inserted into a semester lesson plan. Return exactly one JSON object with keys: "
        "chapter, material_outline, prompt_hint. chapter <= 40 Chinese chars; "
        "material_outline 200-500 Chinese chars; prompt_hint <= 120 Chinese chars. "
        "Return JSON only."
    )


def build_session_draft_user_message(
    *,
    cover: dict[str, Any],
    prompt: str,
    previous_context: str,
    next_context: str,
) -> str:
    return "\n".join(
        [
            f"Course: {cover.get('course_name') or ''}",
            f"Class: {cover.get('class_name') or ''}",
            f"Textbook: {cover.get('textbook') or ''}",
            f"Teacher hint: {prompt or 'Please bridge the neighboring sessions naturally.'}",
            f"Previous session: {previous_context or '(none)'}",
            f"Next session: {next_context or '(none)'}",
            "Return the new session card JSON.",
        ]
    )
