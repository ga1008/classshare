"""Shared role examples and planning times for the four career graph columns.

Graduation anchors the common planning axis, not a promised promotion schedule.
The resolver is pure and never changes direction identities or persisted graphs.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

PHASES = ("探索阶段", "入门阶段", "发展阶段", "进阶阶段")

# A platform-maintained planning scale shared by every direction. These bounds
# are illustrative, not labor-market statistics or profession-specific rules.
_TIME_RANGES = ((0, 2), (2, 5), (5, 10), (10, None))


def build_career_time_axis() -> dict[str, Any]:
    """Return a fresh graduation-relative time axis for all four stage columns.

    Keep time separate from ``tl`` so old graphs retain their role/duty contract.
    A graph-wide axis also keeps cross-direction links on the same time scale.
    """
    columns = []
    for stage, (phase, (years_min, years_max)) in enumerate(zip(PHASES, _TIME_RANGES)):
        span = f"{years_min} 年以上" if years_max is None else f"{years_min}–{years_max} 年"
        columns.append({
            "stage": stage,
            "phase": phase,
            "years_min": years_min,
            "years_max": years_max,
            "label": f"毕业后约 {span}",
        })
    return {
        "basis": "graduation",
        "label": "毕业后参考年限",
        "note": "年限用于规划参考，不承诺按时晋升；进修、转行及执业资格路径的实际用时可能不同。",
        "columns": columns,
    }


# Each row is one coherent example path. Adjacent graph links still represent
# alternatives; a technical path does not automatically turn into management.
ROLE_PATHS = {
    "后端开发工程师": ("初级后端开发工程师", "后端开发工程师", "高级后端开发工程师", "资深后端开发工程师"),
    "前端开发工程师": ("初级前端开发工程师", "前端开发工程师", "高级前端开发工程师", "资深前端开发工程师"),
    "全栈工程师": ("初级全栈工程师", "全栈工程师", "高级全栈工程师", "资深全栈工程师"),
    "移动端开发": ("初级移动端开发工程师", "移动端开发工程师", "高级移动端开发工程师", "移动端架构师"),
    "测试 / 测试开发(SDET)": ("测试工程师", "测试开发工程师", "高级测试开发工程师", "测试架构师"),
    "运维 / DevOps / SRE": ("运维工程师", "DevOps工程师", "高级SRE工程师", "基础架构工程师"),
    "数据方向(分析/工程/科学)": ("初级数据分析师", "数据分析师", "高级数据分析师", "资深数据分析师"),
    "人工智能 / 算法 / 大模型": ("初级AI应用工程师", "AI应用工程师", "高级AI应用工程师", "AI应用架构师"),
    "网络安全工程师": ("初级网络安全工程师", "网络安全工程师", "高级网络安全工程师", "安全架构师"),
    "游戏开发": ("初级游戏开发工程师", "游戏开发工程师", "高级游戏开发工程师", "游戏主程序员"),
    "嵌入式 / 物联网": ("初级嵌入式工程师", "嵌入式工程师", "高级嵌入式工程师", "嵌入式系统架构师"),
    "云计算 / 云原生": ("云平台运维工程师", "云原生工程师", "高级云原生工程师", "云架构师"),
    "产品经理(PM)": ("产品助理", "产品经理", "高级产品经理", "产品总监"),
    "项目经理 / 技术管理": ("项目助理", "项目经理", "高级项目经理", "项目总监"),
    "UI / UX 设计师": ("初级UI/UX设计师", "UI/UX设计师", "高级UI/UX设计师", "资深UI/UX设计师"),
    "跨境电商技术 / 运营": ("跨境电商运营助理", "跨境电商运营专员", "跨境电商运营主管", "跨境电商运营经理"),
    "企业出海 / 外企 / 海外远程": ("初级国际化软件工程师", "国际化软件工程师", "高级国际化软件工程师", "资深国际化软件工程师"),
    "技术本地化 / 技术写作": ("技术文档助理", "技术文档工程师", "高级技术文档工程师", "技术文档负责人"),
    "考公务员(信息技术岗)": ("信息技术岗科员", "信息化业务骨干", "信息化项目负责人", "信息化部门负责人"),
    "事业单位 / 国企 / 银行科技岗": ("初级信息技术工程师", "信息技术工程师", "高级信息技术工程师", "信息技术项目负责人"),
    "计算机教学与教育服务": ("计算机助教", "计算机教师", "计算机骨干教师", "计算机教研组长"),
    "考研 / 保研": ("硕士研究生", "博士研究生", "博士后研究人员", "助理研究员"),
    "留学深造": ("海外硕士研究生", "海外博士研究生", "博士后研究人员", "研究员"),
    "创业 / 独立开发 / 自由职业": ("独立开发者", "自由职业开发者", "技术工作室负责人", "技术企业创始人"),
    "翻译与本地化": ("翻译助理", "翻译员", "资深翻译员", "本地化项目经理"),
    "国际商务与跨境服务": ("国际商务助理", "国际商务专员", "国际商务主管", "国际商务经理"),
    "语言教学与培训": ("语言助教", "语言教师", "语言骨干教师", "语言教研组长"),
    "国际内容与传播": ("国际内容编辑助理", "国际内容编辑", "资深国际内容编辑", "国际内容主编"),
    "外事与会展服务": ("会展项目助理", "会展项目专员", "会展项目主管", "会展项目经理"),
    "语言研究与继续深造": ("语言研究助理", "语言学硕士研究生", "语言学博士研究生", "语言学研究员"),
    "业务运营": ("运营助理", "运营专员", "运营主管", "运营经理"),
    "市场与品牌": ("品牌助理", "品牌专员", "品牌主管", "品牌经理"),
    "人力资源服务": ("人力资源助理", "人力资源专员", "人力资源主管", "人力资源经理"),
    "财务与会计支持": ("会计助理", "会计", "总账会计", "财务主管"),
    "供应链与采购": ("采购助理", "采购专员", "采购主管", "采购经理"),
    "客户成功与商务": ("客户成功助理", "客户成功专员", "客户成功经理", "客户成功总监"),
    "学科教学": ("学科助教", "学科教师", "学科骨干教师", "学科教研组长"),
    "课程与学习资源设计": ("课程助理", "课程设计师", "高级课程设计师", "课程研发负责人"),
    "教育项目运营": ("教育项目助理", "教育项目专员", "教育项目主管", "教育项目经理"),
    "学习支持与学生服务": ("学生事务助理", "学生事务专员", "学生事务主管", "学生服务负责人"),
    "教育内容编辑": ("教育编辑助理", "教育编辑", "资深教育编辑", "教育内容主编"),
    "教育研究与深造": ("教育研究助理", "教育学硕士研究生", "教育学博士研究生", "教育研究员"),
    "视觉与品牌设计": ("设计助理", "视觉设计师", "高级视觉设计师", "品牌设计总监"),
    "交互与用户体验": ("交互设计助理", "交互设计师", "高级交互设计师", "用户体验设计负责人"),
    "数字内容制作": ("视频制作助理", "视频剪辑师", "高级视频剪辑师", "视频制作总监"),
    "文化活动与策展": ("策展助理", "策展专员", "策展人", "策展项目负责人"),
    "创意内容与传播": ("内容编辑助理", "内容编辑", "资深内容编辑", "内容主编"),
    "艺术教育与服务": ("艺术助教", "艺术教师", "艺术骨干教师", "艺术教研组长"),
    "专业临床与护理路径": ("住院医师 / 护士", "主治医师 / 护师", "副主任医师 / 主管护师", "主任医师 / 副主任护师"),
    "健康管理服务": ("健康管理助理", "健康管理师", "健康管理主管", "健康管理项目负责人"),
    "康复与社区支持": ("康复治疗士", "康复治疗师", "主管康复治疗师", "副主任康复治疗师"),
    "医药与健康产品支持": ("医药产品助理", "医药产品专员", "医药产品经理", "医药产品总监"),
    "健康内容与科普": ("健康编辑助理", "健康科普编辑", "资深健康科普编辑", "健康内容主编"),
    "专业研究与深造": ("科研助理", "硕士研究生", "博士研究生", "助理研究员"),
    "软件开发": ("初级软件工程师", "软件工程师", "高级软件工程师", "资深软件工程师"),
    "数据分析": ("初级数据分析师", "数据分析师", "高级数据分析师", "资深数据分析师"),
    "网络与信息安全": ("初级网络安全工程师", "网络安全工程师", "高级网络安全工程师", "安全架构师"),
    "系统实施与技术支持": ("技术支持助理", "技术支持工程师", "高级技术支持工程师", "技术支持经理"),
    "产品与技术运营": ("产品运营助理", "产品运营专员", "产品运营主管", "产品运营经理"),
    "技术研究与深造": ("科研助理", "技术研发工程师", "高级研发工程师", "技术研究员"),
    "专业实践与行业服务": ("专业服务助理", "专业服务专员", "专业服务主管", "专业服务项目经理"),
    "内容与信息整理": ("资料编辑助理", "资料编辑", "资深资料编辑", "资料编辑主管"),
    "组织与项目支持": ("项目助理", "项目专员", "项目主管", "项目经理"),
    "研究与数据支持": ("数据助理", "数据分析师", "高级数据分析师", "研究项目经理"),
    "公共与客户服务": ("客户服务助理", "客户服务专员", "客户服务主管", "客户服务经理"),
    "专业进修与继续深造": ("科研助理", "硕士研究生", "博士研究生", "助理研究员"),
    # Deployed computer-science/AI catalogues include these narrower directions.
    "数据工程师": ("初级数据工程师", "数据工程师", "高级数据工程师", "数据架构师"),
    "大数据开发工程师": ("初级大数据开发工程师", "大数据开发工程师", "高级大数据开发工程师", "大数据架构师"),
    "机器学习/算法工程师": ("初级算法工程师", "算法工程师", "高级算法工程师", "资深算法工程师"),
    "AI应用开发工程师": ("初级AI应用开发工程师", "AI应用开发工程师", "高级AI应用开发工程师", "AI应用架构师"),
    "技术项目经理": ("技术项目助理", "技术项目经理", "高级技术项目经理", "技术项目总监"),
    "运维工程师": ("初级运维工程师", "运维工程师", "高级运维工程师", "运维架构师"),
    "售前解决方案工程师": ("售前助理工程师", "售前解决方案工程师", "高级售前解决方案工程师", "售前解决方案架构师"),
    "实施交付工程师": ("初级实施工程师", "实施交付工程师", "高级实施交付工程师", "实施交付项目经理"),
    "解决方案架构师": ("解决方案助理工程师", "解决方案工程师", "高级解决方案工程师", "解决方案架构师"),
    "IT咨询顾问": ("IT咨询助理", "IT咨询顾问", "高级IT咨询顾问", "IT咨询项目经理"),
    "技术销售/客户经理": ("技术销售助理", "技术销售专员", "技术客户经理", "技术销售总监"),
    "IT项目管理办公室PMO": ("PMO助理", "PMO专员", "PMO主管", "PMO经理"),
    "数字化转型/信息化专员": ("信息化助理", "信息化专员", "信息化项目主管", "信息化项目经理"),
    "Python后端开发工程师": ("初级Python后端工程师", "Python后端开发工程师", "高级Python后端工程师", "资深Python后端工程师"),
    "机器学习平台/MLOps工程师": ("初级MLOps工程师", "MLOps工程师", "高级MLOps工程师", "机器学习平台架构师"),
    "商业数据分析师": ("初级商业数据分析师", "商业数据分析师", "高级商业数据分析师", "资深商业数据分析师"),
    "数据标注与治理工程师": ("数据标注专员", "数据治理工程师", "高级数据治理工程师", "数据治理架构师"),
    "AI产品经理": ("AI产品助理", "AI产品经理", "高级AI产品经理", "AI产品总监"),
    "技术销售/客户成功": ("客户成功助理", "客户成功专员", "技术客户成功经理", "客户成功总监"),
    "项目交付经理": ("项目交付助理", "项目交付专员", "项目交付经理", "项目交付总监"),
    "AI测试工程师": ("初级AI测试工程师", "AI测试工程师", "高级AI测试工程师", "AI测试架构师"),
    "技术支持/IT服务台": ("IT服务台专员", "技术支持工程师", "高级技术支持工程师", "IT服务经理"),
    "AI培训讲师/教育工作者": ("AI课程助教", "AI培训讲师", "资深AI培训讲师", "AI课程研发负责人"),
    "科技自媒体/AI内容创作者": ("科技内容编辑助理", "科技内容编辑", "资深科技内容编辑", "科技内容主编"),
    "AI伦理与合规专员": ("AI合规助理", "AI合规专员", "AI合规主管", "AI合规经理"),
    "公务员/事业单位（计算机/数据岗）": ("信息技术岗工作人员", "信息化业务骨干", "信息化项目负责人", "信息化部门负责人"),
    "金融科技开发/数据岗": ("初级金融科技工程师", "金融科技开发工程师", "高级金融科技工程师", "金融科技架构师"),
    "AI产品出海运营（国际化）": ("AI产品海外运营助理", "AI产品海外运营专员", "AI产品海外运营主管", "AI产品海外运营经理"),
}


def _key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


_PATHS_BY_NAME = {_key(name): titles for name, titles in ROLE_PATHS.items()}
_ALIASES = {
    "软件工程师": "软件开发", "软件开发工程师": "软件开发",
    "后端工程师": "后端开发工程师", "后端开发": "后端开发工程师",
    "前端工程师": "前端开发工程师", "前端开发": "前端开发工程师",
    "全栈开发工程师": "全栈工程师", "全栈开发": "全栈工程师",
    "测试开发工程师": "测试 / 测试开发(SDET)", "测试工程师": "测试 / 测试开发(SDET)",
    "测试开发": "测试 / 测试开发(SDET)", "移动端开发工程师": "移动端开发",
    "产品经理": "产品经理(PM)", "项目经理": "项目经理 / 技术管理",
    "UI设计师": "UI / UX 设计师", "UX设计师": "UI / UX 设计师",
    "数据分析师": "数据分析", "翻译": "翻译与本地化", "翻译员": "翻译与本地化",
    "UI/UX设计师": "UI / UX 设计师", "产品运营": "产品与技术运营",
    "SRE/DevOps工程师": "运维 / DevOps / SRE", "DevOps/运维工程师": "运维 / DevOps / SRE",
    "云平台运维/云工程师": "云计算 / 云原生", "云计算运维/SRE": "云计算 / 云原生",
    "技术支持工程师": "系统实施与技术支持", "算法工程师（应用型）": "机器学习/算法工程师",
    "解决方案工程师/售前": "售前解决方案工程师", "技术写作/文档工程师": "技术本地化 / 技术写作",
    "国企信息化/数字化专员": "数字化转型/信息化专员",
}
_PATHS_BY_NAME.update({_key(alias): ROLE_PATHS[target] for alias, target in _ALIASES.items()})

_GENERIC_TITLES = {
    "了解与观察", "实践与证据", "独立承担任务", "专长与协作", "了解与体验",
    "实习与初级工作", "独立承担职责", "入门", "成长阶段", "初级", "中级", "高级",
    "资深", "中/高级", "中级/高级", "经理", "总监", "负责人", "员工", "从业者", "专家",
}
_GENERIC_TITLE_KEYS = {_key(item) for item in _GENERIC_TITLES}
_ROLE_ENDING = re.compile(
    r"(?:工程师|架构师|程序员|开发者|设计师|分析师|咨询师|规划师|会计师|经济师|"
    r"管理师|治疗士|治疗师|剪辑师|医师|医生|护师|护士|药士|药师|技师|教师|助教|教研组长|"
    r"讲师|译员|翻译员|研究员|研究生|研究人员|博士后|编辑|主编|策展人|会计|"
    r"技术员|专员|顾问|助理|经理|主管|总监|负责人|创始人|合伙人|科员|业务骨干|专家|"
    r"创作者|艺术家|作家|摄影师|经纪人|管理员|策划师|运营师|工作人员|服务员|"
    r"警察|辅警|消防员|飞行员|乘务员|律师|法官|检察官|公证员|仲裁员)$"
)
_ENGLISH_ROLE_ENDING = re.compile(
    r"\b(?:engineer|developer|architect|designer|analyst|manager|director|specialist|"
    r"consultant|researcher|scientist|editor|teacher|nurse|physician|lead|officer|professor)$", re.I
)


def is_role_title(value: Any) -> bool:
    """Recognize bounded job/study identities, excluding actions and fragments.

    This is a semantic shape check, not source verification. Publication still
    applies the existing market-claim and text sanitization contract.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        return False
    title = value.strip()
    if any(ord(char) < 32 for char in title) or _key(title) in _GENERIC_TITLE_KEYS:
        return False
    if any(char in title for char in "。！？；;\n\r"):
        return False
    if re.match(r"^(?:了解|掌握|完成|参与|尝试|申请|考取|考上|准备|提升|培养|成为|晋升|从事|转为|负责|积累|独立承担|通过)", title):
        return False
    # UI/UX is a discipline abbreviation. Otherwise every slash-separated
    # alternative must be complete; "资深 / 工程师" is not a useful title.
    parts = re.sub(r"(?<![A-Za-z])UI\s*/\s*UX(?![A-Za-z])", "UIUX", title, flags=re.I).split("/")
    return all(_key(part) not in _GENERIC_TITLE_KEYS and
               bool(_ROLE_ENDING.search(part.strip()) or _ENGLISH_ROLE_ENDING.search(part.strip()))
               for part in parts)


def _fallback_titles(name: str) -> tuple[str, ...]:
    """Use an identifiable occupation without inventing managerial grades."""
    if is_role_title(name):
        if re.search(r"(?:工程师|设计师|分析师|架构师|开发者|程序员)$", name):
            base = re.sub(r"^(?:初级|中级|高级|资深)", "", name)
            return ("初级" + base, base, "高级" + base, "资深" + base)
        # Existing manager, teacher, clinical and regulated titles remain exact;
        # unknown organizational grades must not be manufactured from a suffix.
        return (name,) * 4
    return ("项目助理", "项目专员", "项目主管", "项目负责人")


def build_career_stages(node: dict[str, Any], *, major_name: str = "") -> list[list[str]]:
    """Return four independent [phase, role title, duty] rows without mutation.

    Maintained paths win for known directions. Other directions use complete
    candidate titles or valid historical titles, with conservative fallback.
    Titles describe examples; employers and professional bodies set conditions.
    """
    name = str(node.get("name") or "").strip()
    titles = _PATHS_BY_NAME.get(_key(name))
    if _key(name) == _key("专业临床与护理路径"):
        if "护理" in major_name:
            titles = ("护士", "护师", "主管护师", "副主任护师")
        elif "药" in major_name:
            titles = ("药士", "药师", "主管药师", "副主任药师")
        elif "康复" in major_name:
            titles = ROLE_PATHS["康复与社区支持"]
        elif any(word in major_name for word in ("医学", "临床", "口腔", "中医")):
            titles = ("住院医师", "主治医师", "副主任医师", "主任医师")
    if titles is None:
        fallback = _fallback_titles(name)
        proposed = node.get("role_titles")
        proposed = proposed if isinstance(proposed, (list, tuple)) and len(proposed) == 4 else ()
        historical = node.get("tl")
        historical = historical if isinstance(historical, (list, tuple)) and len(historical) == 4 else ()
        resolved = []
        for index in range(4):
            title = proposed[index] if proposed else None
            old = historical[index] if historical else None
            if not is_role_title(title) and isinstance(old, (list, tuple)) and len(old) == 3:
                title = old[1]
            resolved.append(title.strip() if is_role_title(title) else fallback[index])
        titles = tuple(resolved)
    duties = (
        "了解“{title}”的具体职责，核对学历、经验或执业资格，从基础任务积累相关成果。",
        "围绕“{title}”的职责，完成常见任务并记录成果，结合实际反馈补足专业能力。",
        "围绕“{title}”的职责，处理较复杂的任务，完善专业方法并参与团队协作。",
        "了解“{title}”的专业深度与责任范围，按实际岗位条件和个人选择规划下一步。",
    )
    return [[phase, title, duty.format(title=title)] for phase, title, duty in zip(PHASES, titles, duties)]
