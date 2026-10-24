"""Stage-trial (破境试炼) difficulty guidance for the exam-generation prompt.

Two independent axes, both pure data:

* **Tier profile** -- how hard a trial should be at each of the ten levels:
  question count, Bloom-level mix, question-type mix, scenario complexity,
  distractor rules, review share of earlier stages, open-answer rubric.
  Difficulty rises monotonically with the tier.
* **Course family** -- what "harder" means for this kind of course (programming,
  language, maths, lab science, business, humanities, arts, engineering,
  general), detected from the course name / description keywords.

`build_stage_exam_guidance_block` renders both into prompt text the model can
follow literally, for any course on the platform.
"""
from __future__ import annotations

import re
from typing import Any

MAX_TIER = 10
MIN_QUESTION_COUNT = 6
MAX_QUESTION_COUNT = 10

# tier bands: (inclusive_low, inclusive_high, key)
_BANDS = [
    (1, 2, "entry"),
    (3, 4, "basic"),
    (5, 6, "applied"),
    (7, 8, "advanced"),
    (9, 10, "mastery"),
]

BAND_LABELS = {
    "entry": "入门档（前期：简单直接）",
    "basic": "基础档（夯实：开始组合）",
    "applied": "应用档（中期：真实情境）",
    "advanced": "进阶档（后期：综合排错）",
    "mastery": "精通档（巅峰：迁移创造）",
}

# Bloom mix per band (percent of questions).
_BLOOM_MIX = {
    "entry": {"remember": 55, "apply": 35, "analyse": 10, "create": 0},
    "basic": {"remember": 35, "apply": 40, "analyse": 20, "create": 5},
    "applied": {"remember": 20, "apply": 40, "analyse": 30, "create": 10},
    "advanced": {"remember": 10, "apply": 30, "analyse": 40, "create": 20},
    "mastery": {"remember": 5, "apply": 20, "analyse": 40, "create": 35},
}

_BLOOM_LABELS = {
    "remember": "记忆/理解（术语、定义、直接判断）",
    "apply": "应用（套用规则、完成小任务、单步推理）",
    "analyse": "分析（比较、排错、解释原因、多步推理）",
    "create": "评价/创造（综合方案、迁移到新情境、评析优劣）",
}

_SCENARIO = {
    "entry": "题干直接、单一知识点、语境贴近教材原例；选项差异明显，干扰项只针对常见初学误解。",
    "basic": "题干可带一个简短情境（2-3 句），仍以单一知识点为主；干扰项开始使用相近概念的混淆。",
    "applied": "题干给出真实任务情境（代码片段、文本段落、数据表、案例片段），要求把 2 个知识点组合起来；干扰项为“看似合理但有一处关键错误”。",
    "advanced": "题干为多步任务或带缺陷的方案，需要先定位问题再选择/说明修正；至少 1 题跨 2 个以上章节综合；干扰项需要理解机理才能排除。",
    "mastery": "题干为开放情境或新情境迁移，要求设计方案、评价取舍、指出潜在风险并说明理由；客观题也应是“最优解”判断而非唯一正确项的简单识别。",
}

_REVIEW_SHARE = {"entry": 0, "basic": 15, "applied": 20, "advanced": 25, "mastery": 30}

_OPEN_ANSWER_RUBRIC = {
    "entry": "简答题 1-2 句即可得满分；explanation 给出标准答案和一个常见错误提示。",
    "basic": "简答题需要“结论 + 一条理由”；explanation 列出得分点（2 点）。",
    "applied": "简答/综合题需要“结论 + 过程/步骤 + 理由”；explanation 列出 3 个得分点及部分得分规则。",
    "advanced": "综合题需要“分析问题 → 方案 → 验证/权衡”；explanation 列出 3-4 个得分点，并写明只给结论不给过程时的扣分。",
    "mastery": "综合题需要“方案设计 + 取舍评价 + 风险与边界”；explanation 列出 4-5 个得分点，鼓励给出多种可接受答案的评判标准。",
}

# radio / checkbox / text / textarea shares per band
_TYPE_SHARES = {
    "entry": (0.50, 0.17, 0.17, 0.16),
    "basic": (0.40, 0.20, 0.20, 0.20),
    "applied": (0.30, 0.20, 0.20, 0.30),
    "advanced": (0.25, 0.20, 0.15, 0.40),
    "mastery": (0.20, 0.20, 0.10, 0.50),
}
_TYPE_KEYS = ("radio", "checkbox", "text", "textarea")


def _coerce_tier(level: dict[str, Any] | None, max_tier: int) -> int:
    try:
        tier = int((level or {}).get("tier") or max_tier)
    except (TypeError, ValueError):
        tier = max_tier
    return max(1, min(max_tier, tier))


def band_for_tier(tier: int) -> str:
    tier = max(1, min(MAX_TIER, int(tier or MAX_TIER)))
    for low, high, name in _BANDS:
        if low <= tier <= high:
            return name
    return "mastery"


def question_count_for_tier(tier: int, *, max_tier: int = MAX_TIER) -> int:
    tier = max(1, min(max_tier, int(tier or max_tier)))
    span = max(1, max_tier - 1)
    return MIN_QUESTION_COUNT + round((MAX_QUESTION_COUNT - MIN_QUESTION_COUNT) * (tier - 1) / span)


def _type_mix(count: int, band: str) -> dict[str, int]:
    """radio / checkbox / text / textarea counts that sum to `count`, each >= 1."""
    raw = [share * count for share in _TYPE_SHARES[band]]
    mix = {key: max(1, int(value)) for key, value in zip(_TYPE_KEYS, raw)}
    order = sorted(range(4), key=lambda index: raw[index] - int(raw[index]), reverse=True)
    remainder = count - sum(mix.values())
    cursor = 0
    while remainder > 0:
        mix[_TYPE_KEYS[order[cursor % 4]]] += 1
        remainder -= 1
        cursor += 1
    while remainder < 0:
        key = _TYPE_KEYS[order[(3 - cursor) % 4]]
        if mix[key] > 1:
            mix[key] -= 1
            remainder += 1
        cursor += 1
    return mix


def build_tier_profile(level: dict[str, Any] | None, *, max_tier: int = MAX_TIER) -> dict[str, Any]:
    """Difficulty profile for one level; a missing tier falls back to the top tier."""
    tier = _coerce_tier(level, max_tier)
    band = band_for_tier(tier)
    count = question_count_for_tier(tier, max_tier=max_tier)
    return {
        "tier": tier,
        "max_tier": max_tier,
        "band": band,
        "band_label": BAND_LABELS[band],
        "difficulty_index": round((tier - 1) / max(1, max_tier - 1), 2),
        "question_count": count,
        "type_mix": _type_mix(count, band),
        "bloom_mix": dict(_BLOOM_MIX[band]),
        "scenario": _SCENARIO[band],
        "review_share_percent": _REVIEW_SHARE[band],
        "open_answer_rubric": _OPEN_ANSWER_RUBRIC[band],
        "expected_minutes": 20 + tier * 5,
    }


# --- course families --------------------------------------------------------

COURSE_FAMILIES: dict[str, dict[str, Any]] = {
    "programming": {
        "label": "编程 / 软件开发 / 计算机",
        "keywords": ["程序", "编程", "python", "java", "c++", "c语言", "javascript", "web", "前端", "后端", "数据库", "sql", "算法", "数据结构", "软件", "开发", "计算机", "网络", "操作系统", "人工智能", "机器学习", "大数据", "数据分析"],
        "guidance": [
            "题干尽量给真实代码片段、报错信息、表结构或输入输出样例；代码使用课程语言，长度控制在 12 行以内。",
            "“应用”题要求预测输出、补全一行或选出正确实现；“分析”题要求找 bug、解释复杂度或说明为什么某方案错误；“创造”题要求写出关键步骤/伪代码或设计方案。",
            "干扰项用常见的边界错误（越界、空值、类型、作用域、语法细节），不要凭空编造不存在的 API。",
            "填空题答案要是唯一确定的标识符、关键字或输出结果，避免同义写法歧义。",
        ],
    },
    "language": {
        "label": "语言 / 外语 / 文学",
        "keywords": ["英语", "日语", "韩语", "法语", "德语", "西班牙", "俄语", "泰语", "越南", "阿拉伯", "语言", "口语", "听力", "阅读", "写作", "翻译", "语法", "词汇", "文学", "语文", "汉语", "对外", "english"],
        "guidance": [
            "题干使用目标语言的真实语段（对话、短文、商务邮件片段），长度随境界递增：入门 1-2 句，进阶 1 个段落，精通 2 段对照。",
            "“应用”题考语法/词汇在语境中的正确使用；“分析”题考语篇理解、语气、文化差异、翻译取舍；“创造”题要求改写、续写或给出翻译并说明理由。",
            "干扰项用形近/义近词、时态体态误用、搭配错误，而非明显拼写错误。",
            "简答/写作题的 explanation 给出评分要点：准确性、得体性、连贯性，并允许多种可接受表达。",
        ],
    },
    "math_stats": {
        "label": "数学 / 统计 / 定量方法",
        "keywords": ["数学", "高数", "微积分", "线性代数", "概率", "统计", "离散", "数理", "运筹", "计量", "定量"],
        "guidance": [
            "数值题给出完整条件和单位，答案可唯一验算；填空只填最终结果（含单位或化简形式）。",
            "“应用”题是套公式的一步计算；“分析”题要求判断方法适用条件或识别错误推导步骤；“创造”题要求建模、选择方法并说明理由。",
            "干扰项来自典型计算失误（符号、量纲、漏项、条件误用），不要给出无意义的随机数。",
            "简答题的 explanation 写出关键推导步骤，允许等价表达形式。",
        ],
    },
    "science_lab": {
        "label": "自然科学 / 实验 / 医护",
        "keywords": ["物理", "化学", "生物", "实验", "地理", "环境", "医学", "护理", "药", "解剖", "生理", "食品", "材料"],
        "guidance": [
            "题干给实验情境、观测数据或现象描述；后期题提供有缺陷的实验设计让学生排错。",
            "“应用”题考原理到现象的直接解释；“分析”题考变量控制、误差来源、数据解读；“创造”题要求设计实验方案或评估安全/伦理风险。",
            "干扰项是“方向相反”“条件混淆”“忽略控制变量”的典型错误。",
            "简答题 explanation 列出原理、证据、结论三类得分点。",
        ],
    },
    "business_management": {
        "label": "经管 / 商科 / 法律 / 公共管理",
        "keywords": ["管理", "经济", "会计", "金融", "市场", "营销", "财务", "人力", "商务", "贸易", "电子商务", "物流", "旅游", "酒店", "法学", "法律", "行政", "公共", "审计", "税务"],
        "guidance": [
            "题干使用企业/机构小案例（可虚构但要合理），后期题给出多方利益冲突的情境。",
            "“应用”题考概念在案例中的识别和简单计算（如比率、成本）；“分析”题考原因诊断、方案比较、合规判断；“创造”题要求给出决策建议并论证取舍。",
            "干扰项是“概念混用”“只看短期”“忽略法规/伦理约束”等典型误判。",
            "简答题 explanation 给出分析框架要点（问题—依据—建议），允许不同但自洽的结论。",
        ],
    },
    "humanities_social": {
        "label": "人文 / 社科 / 教育 / 思政",
        "keywords": ["历史", "史纲", "近现代史", "通史", "哲学", "政治", "思想", "马克思", "社会", "心理", "教育", "新闻", "传播", "文化", "伦理", "形势", "党史", "军事"],
        "guidance": [
            "题干引用课程材料中的观点、史料或情境片段，后期题给出两种相互竞争的解释。",
            "“应用”题考概念识别与史实/理论对应；“分析”题考因果解释、立场辨析、材料解读；“创造”题要求形成论点并用证据支撑。",
            "干扰项是“时序颠倒”“概念近似”“以偏概全”等典型误区，避免把价值判断当成对错陷阱。",
            "简答题 explanation 列出论点、论据、逻辑三类得分点，并明确开放性题目允许多种合理立场。",
        ],
    },
    "arts_design": {
        "label": "艺术 / 设计 / 音乐 / 体育",
        "keywords": ["设计", "美术", "绘画", "音乐", "舞蹈", "视觉", "动画", "影视", "摄影", "表演", "体育", "运动", "健身", "服装", "数字媒体"],
        "guidance": [
            "题干描述作品、场景或动作要求（文字描述即可，不要依赖图片），后期题给出带问题的方案让学生评析。",
            "“应用”题考技法/规则的正确运用；“分析”题考风格、结构、节奏或动作要领的辨析；“创造”题要求提出创作/训练方案并说明依据。",
            "干扰项是“风格混淆”“技法误用”“安全/规范忽略”。",
            "简答题 explanation 给出评分维度（规范性、表现力、合理性）。",
        ],
    },
    "engineering_tech": {
        "label": "工程 / 技术 / 职业技能",
        "keywords": ["工程", "机械", "电子", "电气", "自动化", "建筑", "土木", "测绘", "汽车", "维修", "工艺", "制造", "施工", "安全"],
        "guidance": [
            "题干给参数、图纸文字说明、工艺流程或故障现象；后期题给出不完整/有缺陷的方案。",
            "“应用”题考规范参数和流程步骤；“分析”题考故障诊断、参数取舍、安全隐患识别；“创造”题要求提出改进方案并评估风险。",
            "干扰项来自“步骤颠倒”“参数越限”“忽视安全规范”。",
            "简答题 explanation 列出规范依据、步骤、验证方法。",
        ],
    },
    "general": {
        "label": "通用课程",
        "keywords": [],
        "guidance": [
            "题干围绕本课程材料中的真实概念与案例，后期题给出需要综合多个知识点的情境。",
            "“应用”题考概念在具体例子中的识别与运用；“分析”题考比较、解释原因、排错；“创造”题要求提出方案或评价取舍。",
            "干扰项是课程里学生最常混淆的相近概念，不要用明显荒谬的选项凑数。",
            "简答题 explanation 列出 2-4 个得分点并写明部分得分规则。",
        ],
    },
}


def detect_course_family(course_name: str | None, *extra_texts: Any) -> str:
    """Keyword vote; a hit inside the course name itself counts double."""
    name = str(course_name or "").lower()
    haystack = re.sub(r"\s+", " ", " ".join(str(part or "") for part in (course_name, *extra_texts)).lower())
    best_key, best_hits = "general", 0
    for key, family in COURSE_FAMILIES.items():
        hits = 0
        for keyword in family["keywords"]:
            token = keyword.lower()
            if token in haystack:
                hits += 2 if token in name else 1
        if hits > best_hits:
            best_key, best_hits = key, hits
    return best_key


def _bloom_lines(count: int, mix: dict[str, int]) -> list[str]:
    lines = []
    allocated = 0
    keys = list(mix.keys())
    for index, key in enumerate(keys):
        if index == len(keys) - 1:
            questions = max(0, count - allocated)
        else:
            questions = round(count * mix[key] / 100)
            allocated += questions
        if mix[key] == 0 and questions == 0:
            continue
        lines.append(f"  - {_BLOOM_LABELS[key]}：约 {questions} 题（{mix[key]}%）")
    return lines


def build_stage_exam_guidance_block(
    level: dict[str, Any] | None,
    *,
    course_name: str | None,
    course_description: str | None = None,
    extra_hints: str | None = None,
    pass_score: int = 80,
) -> dict[str, Any]:
    """Prompt text + machine profile for one trial: {"text", "profile", "course_family", "course_family_label"}."""
    profile = build_tier_profile(level)
    family_key = detect_course_family(course_name, course_description, extra_hints)
    family = COURSE_FAMILIES[family_key]
    mix = profile["type_mix"]
    level_name = str((level or {}).get("name") or "当前境界")
    review_line = (
        f"回顾比例：约 {profile['review_share_percent']}% 的题目回顾更早境界的核心知识点，但要和当前范围结合出题，不能原样重复早期试炼。"
        if profile["review_share_percent"]
        else "回顾比例：无需回顾更早境界，全部聚焦当前范围内最基础、最核心的知识点。"
    )
    lines = [
        "【试炼难度指引】",
        f"境界：{level_name}（第 {profile['tier']} / {profile['max_tier']} 境，{profile['band_label']}，难度系数 {profile['difficulty_index']}）。",
        f"题量：恰好 {profile['question_count']} 题；题型分布：单选 {mix['radio']} 题、多选 {mix['checkbox']} 题、填空 {mix['text']} 题、简答/综合 {mix['textarea']} 题。",
        "认知层级分布（按题数）：",
        *_bloom_lines(profile["question_count"], profile["bloom_mix"]),
        f"情境与干扰项：{profile['scenario']}",
        review_line,
        f"主观题评分：{profile['open_answer_rubric']}",
        f"难度校准：目标是认真学完当前范围的学生能拿到 {pass_score}-90 分，而只靠背诵或猜测的学生明显低于 {pass_score} 分；"
        "整体难度必须随境界单调上升——同一门课中，高境界试炼绝不能比低境界更容易。",
        "分值：题目 points 之和为 100；主观题分值高于客观题，入门档可平均分配。",
        "",
        f"【课程类型指引】课程类型：{family['label']}",
    ]
    lines.extend(f"- {item}" for item in family["guidance"])
    lines.append("- 所有题目必须能从【当前破境范围】内的课堂章节和学习文档中找到依据；不得考范围外内容，也不得出与课程无关的常识题。")
    return {
        "text": "\n".join(lines),
        "profile": profile,
        "course_family": family_key,
        "course_family_label": family["label"],
    }
