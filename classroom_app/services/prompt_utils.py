"""
提示词工具函数 —— 为 AI 提示生成提供通用能力：
- 礼貌称呼
- 时间 / 语境信息
- 模型能力探测
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 礼貌称呼
# ---------------------------------------------------------------------------

# 中文常见单姓集合（直接按单字拆成集合）
_COMMON_SURNAMES: frozenset[str] = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程"
    "嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗"
    "山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司"
    "韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟"
    "谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴"
    "瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄"
    "阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾母沙乜养鞠须"
    "丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

_COMPOUND_SURNAMES: frozenset[str] = frozenset({
    "欧阳", "司马", "上官", "诸葛", "令狐", "东方", "西门", "南宫", "皇甫", "尉迟",
    "公孙", "慕容", "长孙", "宇文", "独孤", "端木", "百里", "轩辕", "太叔", "申屠",
})


def _is_cjk_char(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def _extract_surname(name: str) -> str:
    """尝试提取中文姓名中的姓氏（支持单姓/复姓）；非中文姓名返回空串以免误截断。"""
    name = (name or "").strip()
    if not name:
        return ""
    if len(name) >= 3 and name[:2] in _COMPOUND_SURNAMES:
        return name[:2]
    if name[0] in _COMMON_SURNAMES:
        return name[0]
    if _is_cjk_char(name[0]):
        return name[0]
    return ""


def polite_address(name: str, role: str = "student") -> str:
    """
    将直呼其名转换为礼貌称呼。

    - 教师 → "张老师"（非中文名如 "Alice" → "Alice 老师"）
    - 学生 → "王同学"（非中文名如 "Alice" → "Alice 同学"）
    - 已带“老师/同学”后缀时避免重复拼接
    """
    name = (name or "").strip()
    suffix = "老师" if role == "teacher" else "同学"
    if not name:
        return suffix
    if name.endswith(suffix):
        return name
    surname = _extract_surname(name)
    if surname:
        return f"{surname}{suffix}"
    return f"{name} {suffix}"


# ---------------------------------------------------------------------------
# 时间 / 语境上下文
# ---------------------------------------------------------------------------

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_PERIOD_LABELS = [
    (0, 5, "深夜"),
    (5, 8, "清晨"),
    (8, 12, "上午"),
    (12, 14, "中午"),
    (14, 18, "下午"),
    (18, 22, "傍晚至晚间"),
    (22, 24, "深夜"),
]


def _current_time_period(hour: int) -> str:
    for start, end, label in _PERIOD_LABELS:
        if start <= hour < end:
            return label
    return "深夜"


def build_time_context_text(now: Optional[datetime] = None) -> str:
    """
    生成面向 AI 的时间 / 语境描述，供 system prompt 注入。

    示例输出:
        当前时间：2026-04-10 14:35（周四 · 下午）
        时节提示：临近五一假期，学生可能开始期待放假。
    """
    if now is None:
        now = datetime.now()

    weekday = _WEEKDAY_CN[now.weekday()]
    period = _current_time_period(now.hour)
    time_text = f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}（{weekday} · {period}）"

    # 节令/学期提示
    season_hints: list[str] = []
    month = now.month
    day = now.day

    if month == 1 and day < 10:
        season_hints.append("元旦刚过，新年伊始。")
    elif month == 1 and day >= 20:
        season_hints.append("临近春节，学生可能心绪波动较大。")
    elif month == 2 and day < 15:
        season_hints.append("春节 / 寒假期间。")
    elif month == 3:
        season_hints.append("新学期开学初期，学生可能在适应节奏。")
    elif month == 4 and day >= 25:
        season_hints.append("临近五一假期，学生可能开始期待放假。")
    elif month == 5 and day <= 5:
        season_hints.append("五一假期中或假期刚结束。")
    elif month == 6:
        season_hints.append("进入期末阶段，学生压力可能上升。")
    elif month == 7:
        season_hints.append("暑假或期末考试结束阶段。")
    elif month == 9:
        season_hints.append("秋季学期开学初期。")
    elif month == 10 and day <= 7:
        season_hints.append("国庆假期中。")
    elif month == 12 and day >= 20:
        season_hints.append("临近寒假，学生可能开始期待放假。")

    if now.weekday() >= 5:
        season_hints.append("今天是周末。")

    season_text = ""
    if season_hints:
        season_text = "\n时节提示：" + " ".join(season_hints)

    return time_text + season_text


def build_system_info_text() -> str:
    """生成面向 AI 的平台简介，供 system prompt 注入。"""
    return (
        "平台信息：高校课堂互动教学平台，涵盖课程与资料管理、学习文档、作业考试、"
        "课堂研讨、消息通知与 AI 教学辅助。"
    )


def build_natural_voice_rules(scene: str = "general") -> str:
    """
    全平台统一的 AI 自然表达原则：温和、清醒、克制、有深度，去 AI 腔与空套话。
    注意：严禁包含任何隐私/内部标识词，可安全注入公开对话与教学场景。
    """
    base = (
        "表达要求：\n"
        "- 像一位靠谱、真诚、分寸得体的大学同事或助教那样说话：温和但不讨好，专业但不端着，简练但不冷漠。\n"
        "- 开门见山回应重点；少用“好问题”“很高兴为您解答”“值得注意的是”“综上所述”“不难发现”等套话，不用“赋能/抓手/闭环/维度”等空洞热词。\n"
        "- 结尾自然收住，不要无故加一段总结、鸡汤或“希望以上内容对你有所帮助，如有问题欢迎继续提问”。\n"
        "- 信息有把握就直接给结论和依据，把握不足就诚实说明边界，不编造事实。"
    )
    if scene == "discussion":
        return (
            base
            + "\n- 研讨室公开回复默认控制在 1–3 句，像顺手接话一样自然；只有在问题本身较复杂时才分段或列点。"
        )
    if scene == "grading":
        return (
            base
            + "\n- 评语紧扣这份作业的具体作答：先肯定真正做对的地方，再指出具体卡点和可立即动手修改的一步，不写放之四海而皆准的空鼓励。"
        )
    if scene == "teaching_doc":
        return (
            base
            + "\n- 教学文档、教案与分析材料用准确平实的学科语言撰写，讲清逻辑和实操细节，不堆砌宏大口号或模板套话。"
        )
    return base


# ---------------------------------------------------------------------------
# 模型能力工具
# ---------------------------------------------------------------------------

# 支持联网搜索的模型 provider 关键字
_WEB_SEARCH_PROVIDERS: frozenset[str] = frozenset({
    "volcano", "doubao", "doubao-pro", "doubao-lite",
    "ark", "volcengine",
})


def should_enable_web_search(model_capability: Optional[str] = None) -> bool:
    """
    根据模型能力标签判断是否应启用联网搜索。
    默认关闭，仅当明确传入支持联网的 provider 时才开启。
    """
    if not model_capability:
        return False
    normalized = model_capability.strip().lower()
    return any(provider in normalized for provider in _WEB_SEARCH_PROVIDERS)
