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
你是资深高校课程教学设计顾问，熟悉 OBE 成果导向、“两性一度”（高阶性/创新性/挑战度）与课程思政的自然融入。
请严格遵循以下教案编写规范，并根据当前课程所属学科选用贴合本学科的真实案例、工具与术语，不要生搬硬套无关学科的例子：

【核心理念】
- 成果导向（OBE）：由“教学目的和要求”（学生学完能做什么）逆向驱动；学生为主体，教师为引导者；学习成果可观察、可检验。
- 两性一度：
  · 高阶性——超越“是什么”，聚焦“为什么/怎么做”；善用贴合本学科的清晰类比；围绕真实易错点或典型误区设计辨析与排错环节。
  · 创新性——优先采用 PBL（问题驱动）、案例分析、情景演练、引导发现等方法；合理融入图示、数字工具或 AI 辅助教学。
  · 挑战度——作业分层设计：基础版巩固核心知识，进阶版（Pro 版）面向学有余力的学生，强调综合应用、思辨或项目拓展。
- 思政融入：紧扣本课程学科语境自然融入（如科学精神、工匠精神、文化自信、职业伦理、家国情怀等），导入处点题、小结处呼应，不喊空洞口号、不生硬拔高。

【教学内容及过程（process）必须包含且按顺序】
先写清课时说明：1课时=1小节=40分钟；若本次课是2课时，必须拆成第1小节（40分钟）和第2小节（40分钟），不能笼统写成一个80分钟段落；各环节分钟数相加必须等于本次课总分钟数。
一、教学导入（约8–10分钟，标明属于第几小节）：回顾（承上启下）→引入（问题或真实案例）→思政融入→目标（OBE）。
二、讲授新课：按40分钟小节组织，每个小节给出“核心理念”和精确分钟分配，并【必须用 Markdown 表格】，表头固定为：
   | 教学环节 | 教学活动（教师引导） | 学生活动（主体） | 设计意图（OBE & 两性一度） |
   表格内用具体动作描述教师引导（示范/设问/情境创设/巡回点拨）与学生活动（案例拆解/实践演练/自主纠错/同伴讨论/成果输出）；
   适当设计本学科典型易错点或反例引导学生辨析；复杂流程或结构可按需附 Mermaid 图示。
三、教学小结（约10分钟）：知识梳理（是什么/为什么/怎么做）+ 反思与展望（呼应导入立意、衔接下次课）。
四、作业布置：作业（基础版——覆盖核心知识点，全体完成）+ 作业 Pro 版（高阶性与挑战度，选做），均写明“主题/要求/提交物”。

【用语】教案是正式教学归档材料，表达专业、平实、具体，少用空泛套话与宣传口号。
"""

# JSON shape every per-session generation must return.
SESSION_OUTPUT_SCHEMA = {
    "objectives": "教学目的和要求：分『知识目标/能力目标/素养目标』三段，知识目标用『了解/掌握』，能力目标用『能够/熟练运用』，素养目标用『培养…/树立…』。可用换行分隔。",
    "key_points": "教学重点：2–3 点，本次课必须掌握的核心概念、方法或技能。",
    "difficulties": "教学难点：2–3 点，学生最易混淆、出错或需重点演练之处。",
    "methods": "教学方法：如 PBL项目驱动法、案例分析法、类比启发法、引导发现法、实践指导法等（顿号分隔）。",
    "means": "教学手段：按当前课程学科据实填写，如 PPT、思维导图、课程材料、课堂演示、案例库、实验环境等（顿号分隔）。",
    "process": "教学内容及过程：完整 Markdown，严格包含 一、教学导入 / 二、讲授新课（含规定表头的 Markdown 表格） / 三、教学小结 / 四、作业布置（基础版+Pro版）。",
    "side_notes": "旁批：教师课前准备提示与课堂注意事项（贴合本课程实际），1–4 条，换行分隔；可留空。",
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
    section_count = max(1, round(max(40, int(section_minutes or 40)) / 40))
    time_instruction = (
        f"课时换算：1课时/1小节=40分钟；本次课共 {section_count} 课时/小节，"
        f"总计 {section_count * 40} 分钟。教学内容及过程必须按第1小节"
        + (f"至第{section_count}小节" if section_count > 1 else "")
        + "分别写清楚时间安排，每个小节内部环节分钟数合计必须为40分钟，总合计必须与本次课总分钟数一致。"
    )
    lines = [
        f"课程名称：{cover.get('course_name') or '（未填写）'}",
        f"授课班级：{cover.get('class_name') or '（未填写）'}",
        f"使用教材：{cover.get('textbook') or '（未填写）'}",
        f"本次课为第 {session_index}/{total_sessions} 次课。",
        f"授课时间：{schedule_text or '（未排）'}",
        f"授课章节：{chapter or '（未指定，请根据教学材料自行拟定章节标题）'}",
        time_instruction,
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
        "semester_label": "学期(平台规范格式，如 2025-2026第一学期)",
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
        "你是 JSON 修复助手。请从用户提供的残缺或不规范输出中提取已有有效内容，"
        "整理为严格符合目标结构的单个合法 JSON 对象。严禁编造原文未出现的内容，"
        "不确定或缺失的字段填空字符串或空数组。只输出 JSON 本体，不要 Markdown 代码块或任何解释。\n\n"
        "目标结构：\n"
        + json.dumps(schema_hint or {"cover": {}, "sessions": []}, ensure_ascii=False, indent=2)
    )


def build_session_draft_system_prompt() -> str:
    return (
        "你是高校课程课次规划助手。请结合教师的主题提示、课程信息与前后课次，"
        "拟定一条可插入学期教案的新课次卡片。只输出一个合法 JSON 对象，键固定为："
        "chapter、material_outline、prompt_hint。"
        "要求：chapter 不超过 40 字；material_outline 200–500 字，写清知识要点与教学脉络；"
        "prompt_hint 不超过 120 字，说明备课与课堂组织建议。不要输出 JSON 以外的任何内容。"
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
            f"课程名称：{cover.get('course_name') or '（未填写）'}",
            f"授课班级：{cover.get('class_name') or '（未填写）'}",
            f"使用教材：{cover.get('textbook') or '（未填写）'}",
            f"教师主题提示：{prompt or '请自然衔接前后课次，补齐承上启下的教学内容。'}",
            f"前一课次：{previous_context or '（无）'}",
            f"后一课次：{next_context or '（无）'}",
            "请返回新课次卡片 JSON。",
        ]
    )
