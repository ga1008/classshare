"""课次知识树的提示词、完整示例与生成质量检查。

在线模型的推理能力有限，因此提示词给出：硬性结构约束、粒度与比重评分细则、措辞规则、
字段写法、一个完整的高质量示例（输入摘要 + 标准输出），以及输出前自检清单。
``quality_issues`` 在归一化之前检查原始输出；有问题时生成服务会把问题清单反馈给模型
重写一次（最多一次）。示例由 Python 字典生成，单元测试保证示例本身零问题、可归一化。
"""

from __future__ import annotations

import json
import re
from typing import Any


def _unit(label: str, weight: int, detail: str, example: str, question: str, prerequisite: str, extension: str) -> dict[str, Any]:
    return {"label": label, "weight": weight, "detail": detail, "example": example,
            "question": question, "prerequisite": prerequisite, "extension": extension}


EXAMPLE_INPUT = """# 课程信息
课程名称：数据结构（计算机科学与技术专业，2 学分）
# 教材与目录
教材：《数据结构（C语言版）》 第1章 绪论；第2章 线性表；第3章 栈和队列；第4章 串……
# 本次课
标题：第 5 次课 栈与队列
上一课次：线性表的链式存储
上一课次尚未讲完的基础单元：双向链表的插入删除
下一课次：串的模式匹配
# 本次课学习文档（节选）
## 一、栈
栈是限定仅在表尾进行插入和删除的线性表，表尾称为栈顶……后进先出（LIFO）……
基本操作：InitStack、Push、Pop、GetTop、StackEmpty……栈满时 top == MAXSIZE-1……
## 二、存储实现
顺序栈用数组 + top 指针；链栈用单链表，入栈采用头插法……
循环队列：rear = (rear+1) % MAXSIZE，队满条件 (rear+1)%MAXSIZE == front……链队列设 front、rear 两个指针……
## 三、应用
例 3-1 括号匹配检验……例 3-2 中缀表达式求值（运算符栈与操作数栈）……队列用于图的广度优先搜索……
## 课堂练习
用栈判断 "{[()]}" 与 "([)]" 是否匹配。"""

EXAMPLE_OUTPUT: dict[str, Any] = {
    "title": "栈与队列的原理、实现与应用",
    "summary": "掌握栈和队列的操作受限特性、顺序与链式实现及典型应用。",
    "nodes": [
        {
            "label": "栈的逻辑结构与基本操作",
            "detail": "栈是只允许在一端（栈顶）插入和删除的线性表，核心是后进先出。本部分建立栈的抽象概念并掌握其基本操作及边界条件。",
            "example": "浏览器的“后退”按钮按访问顺序的逆序返回页面。",
            "question": "为什么说栈是“操作受限”的线性表？受限带来了什么好处？",
            "prerequisite": "线性表的定义与顺序存储",
            "extension": "支撑栈的两种存储实现与表达式求值",
            "children": [
                {
                    "label": "栈的定义与特性",
                    "detail": "明确栈顶、栈底与后进先出的含义，区分栈与一般线性表。",
                    "children": [
                        _unit("后进先出（LIFO）特性", 14,
                              "最后入栈的元素最先出栈；给定入栈序列时，并非所有出栈序列都合法。",
                              "入栈顺序 1、2、3 时，出栈序列 3、1、2 不可能出现。",
                              "入栈序列为 a、b、c，有哪些合法的出栈序列？",
                              "线性表的元素次序", "判断出栈序列合法性、括号匹配"),
                        _unit("栈顶与栈底的概念", 4,
                              "栈顶是允许插入删除的一端，由 top 指示；栈底固定不动。",
                              "一摞盘子只能从最上面取放。",
                              "空栈时 top 应取什么值？不同教材为何约定不同？",
                              "数组下标", "顺序栈的 top 指针设计"),
                    ],
                },
                {
                    "label": "栈的基本操作",
                    "detail": "掌握 Push、Pop、GetTop 的语义与前置条件，理解栈空、栈满的判定。",
                    "children": [
                        _unit("入栈与出栈操作", 10,
                              "Push 先判满再将元素放入 top 位置；Pop 先判空再取出栈顶并修改 top。",
                              "依次执行 Push(1)、Push(2)、Pop() 后栈中只剩 1。",
                              "Pop 与 GetTop 有什么区别？各适用于什么场景？",
                              "栈顶指针的含义", "表达式求值中的运算符栈"),
                        _unit("栈空与栈满判定", 6,
                              "顺序栈以 top == -1 为空、top == MAXSIZE-1 为满；链栈一般不存在栈满。",
                              "向已满的顺序栈继续 Push 会产生“上溢”。",
                              "如何让两个栈共享一个数组空间以减少上溢？",
                              "数组容量 MAXSIZE", "循环队列的判满条件"),
                    ],
                },
            ],
        },
        {
            "label": "栈与队列的存储实现",
            "detail": "分别用顺序存储和链式存储实现栈与队列，重点是循环队列解决“假溢出”的方法。",
            "example": "操作系统的打印任务队列通常采用循环队列实现。",
            "question": "顺序实现与链式实现各适合什么场景？",
            "prerequisite": "数组与单链表的基本操作",
            "extension": "为栈和队列的应用提供可运行的数据结构",
            "children": [
                {
                    "label": "顺序存储实现",
                    "detail": "用连续数组配合下标指针实现栈和队列，操作均为常数时间。",
                    "children": [
                        _unit("顺序栈的数组实现", 10,
                              "用数组存放元素、整型 top 指示栈顶，Push/Pop 都是 O(1)。",
                              "MAXSIZE=5 的顺序栈连续入栈 6 个元素时第 6 次失败。",
                              "顺序栈的空间不够时有哪些扩容策略？",
                              "栈空与栈满判定", "共享栈、表达式求值实现"),
                        _unit("循环队列的取模运算", 14,
                              "用 (rear+1)%MAXSIZE 让队尾回绕；牺牲一个单元区分队空与队满。",
                              "MAXSIZE=6、front=4、rear=1 时，队中元素个数为 3。",
                              "不牺牲存储单元时还能怎样区分队空与队满？",
                              "顺序栈的数组实现", "广度优先搜索中的队列"),
                    ],
                },
                {
                    "label": "链式存储实现",
                    "detail": "用单链表实现栈和队列，空间按需分配，不存在上溢。",
                    "children": [
                        _unit("链栈的头插实现", 6,
                              "以链表头作为栈顶，入栈即头插、出栈即删除首结点，无需判满。",
                              "连续 Push 1、2、3 后链表为 3→2→1。",
                              "为什么链栈不把栈顶放在链表尾部？",
                              "单链表的头插法", "递归调用栈"),
                        _unit("链队列的队头队尾指针", 6,
                              "链队列设 front、rear 两个指针，入队改 rear、出队改 front，删到空时要同时修改 rear。",
                              "只剩一个结点时出队，若不重置 rear 会产生悬空指针。",
                              "带头结点的链队列有什么好处？",
                              "单链表的插入与删除", "图的广度优先遍历"),
                    ],
                },
            ],
        },
        {
            "label": "栈与队列的典型应用",
            "detail": "通过括号匹配、表达式求值和广度优先搜索体会两种结构如何解决实际问题。",
            "example": "编译器用栈检查源代码中的括号是否成对。",
            "question": "哪些问题具有“后出现先处理”的特征，适合用栈？",
            "prerequisite": "栈与队列的基本操作",
            "extension": "下一课串的模式匹配、后续图的遍历",
            "children": [
                _unit("括号匹配检验", 10,
                      "遇左括号入栈，遇右括号与栈顶比较并出栈，扫描结束栈空才匹配。",
                      "\"{[()]}\" 匹配成功，\"([)]\" 在读到 ')' 时与 '[' 不匹配。",
                      "只有一种括号时，是否可以不用栈而只用计数器？",
                      "入栈与出栈操作", "表达式求值"),
                _unit("表达式求值中的栈", 12,
                      "用运算符栈和操作数栈按优先级处理中缀表达式，也可先转后缀再求值。",
                      "3+4*2 中 * 优先级更高，先计算 4*2 再加 3。",
                      "后缀表达式求值为什么只需要一个栈？",
                      "后进先出特性、运算符优先级", "编译原理中的语法分析"),
                _unit("队列与广度优先搜索", 8,
                      "BFS 按层访问：出队一个结点后把其未访问的邻居入队，体现先进先出。",
                      "迷宫最短路径问题用 BFS 求解。",
                      "把 BFS 中的队列换成栈会得到什么遍历方式？",
                      "循环队列或链队列", "图的遍历与最短路径"),
            ],
        },
    ],
}

SCHEMA_TEXT = """{
  "title": "本次课主题（不超过20字）",
  "summary": "一句话概括本次课（不超过40字）",
  "nodes": [
    {
      "label": "大知识点",
      "detail": "...", "example": "...", "question": "...", "prerequisite": "...", "extension": "...",
      "children": [
        {"label": "细分知识", "detail": "...", "children": [
          {"label": "基础知识单元", "weight": 8, "detail": "...", "example": "...", "question": "...", "prerequisite": "...", "extension": "..."}
        ]}
      ]
    }
  ]
}"""

SYSTEM_PROMPT = f"""你是高校课程的资深教学设计专家。任务：把“一次课”的学习文档整理成知识树（思维导图），教师上课时逐项勾选讲完的基础知识单元，系统据此计算本课进度。知识树的质量直接决定进度是否准确，请认真完成。

## 一、输出格式（严格遵守）
只输出一个 JSON 对象，不要输出 Markdown 代码块、解释或任何其他文字。结构如下：
{SCHEMA_TEXT}

## 二、结构硬性规则
1. 顶层大知识点 1–4 个，最好 2–3 个；大知识点通常对应学习文档的主要章节（一级标题）。
2. 树只能两层或三层：大知识点 → 细分知识 → 基础知识单元（三层），或大知识点 → 基础知识单元（两层）。同一棵树里不同大知识点可以层数不同。禁止第四层，禁止只有一层。
3. 没有 children 的节点就是末梢（基础知识单元）。只有末梢写 weight；有 children 的节点不要写 weight。
4. 末梢总数 8–24 个；每个有下级的节点至少 2 个下级（只有 1 个下级时应合并到上级）。
5. 同级节点按文档中的讲授顺序排列。

## 三、基础知识单元的粒度
- 一个末梢 = 课堂上 5–20 分钟能讲完、讲完后能明确判断“已讲/未讲”的一个教学单元。
- 太粗（一个末梢覆盖整节内容）会让进度失真；太细（一个定义拆成多个末梢）会让勾选繁琐。
- 文档中的例题、实验、课堂练习不单独成为大知识点，应作为“实践/应用”类末梢挂到它所练习的知识点下；只有整次课就是实验课时才以实验步骤组织。
- 上一课次未讲完的内容：只有本次文档确实包含时才放入，放在最前面，标签里体现它是延续（例如“（续）双向链表的插入”）。

## 四、比重（weight）评分细则
weight 为 1–30 的整数，表示该单元占本课的重要程度与授课时间，系统会归一化为合计 100%：
- 核心概念、重点难点、需要推导或演示的内容：14–30
- 常规知识点、标准操作步骤：6–12
- 铺垫回顾、术语说明、简单了解的内容：2–5
同一棵树里比重要有区分度，不要全部相同。

## 五、文字规则
- label：具体的名词短语或“动作+对象”，4–14 个字，一句话，不加编号与标点结尾；不要重复上级标签里的词；禁止使用“概述”“介绍”“其他”“小结”“总结”“课堂练习”“拓展”这类空泛标签。
- detail：40–120 字，说明这个知识点讲什么、关键结论或要点，依据学习文档原文，不编造文档里没有的数据、公式或结论。
- example：一个具体例子（优先使用文档里的例题/案例），不超过 80 字。
- question：一个能检验理解的思考题，不超过 50 字。
- prerequisite：学习它之前需要掌握的具体知识（写知识点名称，可来自上一课次或本树其他节点），不超过 50 字。
- extension：它会支撑或引出的具体知识（可指向下一课次或本树后续节点），不超过 50 字。
- 末梢的五个字段都要写；大知识点至少写 detail、example、question；细分知识至少写 detail。没有合适内容时省略该字段，不要写“无”或空字符串。
- 全部使用简体中文；专业术语保留常用英文缩写（如 TCP、LIFO）。

## 六、完整示例（只示范格式、粒度、措辞与比重分布，禁止照搬示例内容）
【示例输入】
{EXAMPLE_INPUT}

【示例输出】
{json.dumps(EXAMPLE_OUTPUT, ensure_ascii=False, indent=1)}

示例要点：三个大知识点对应文档三个章节；“课堂练习”融入“括号匹配检验”而非单独成点；“典型应用”只有两层；核心内容比重 12–14、铺垫 4；每个末梢都能明确判断是否讲完。

## 七、输出前自检（逐条确认后再输出）
1. 顶层 1–4 个；没有第四层；没有只有一个下级的节点。
2. 末梢 8–24 个，都写了 weight，非末梢没有 weight。
3. 没有重复或空泛的标签；标签不重复上级用词。
4. 内容全部来自本次课学习文档，覆盖了文档的每个主要章节。
5. 输出是能被 JSON 解析的单个对象。"""

GENERIC_LABELS = ("概述", "介绍", "其他", "小结", "总结", "课堂练习", "拓展", "补充", "简介")
_GENERIC_RE = re.compile("^(?:" + "|".join(GENERIC_LABELS) + ")$")


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    kids = node.get("children")
    return [kid for kid in kids if isinstance(kid, dict)] if isinstance(kids, list) else []


def quality_issues(raw: Any) -> list[str]:
    """Problems in a raw AI tree worth one corrective re-ask (empty list = acceptable)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not raw["nodes"]:
        return ["没有输出 nodes 数组"]
    issues: list[str] = []
    top = [node for node in raw["nodes"] if isinstance(node, dict)]
    if len(top) > 4:
        issues.append(f"顶层大知识点有 {len(top)} 个，必须为 1–4 个")
    leaves: list[dict[str, Any]] = []
    labels: list[str] = []
    flags = {"deep": False, "single": False}

    def walk(node: dict[str, Any], level: int) -> None:
        labels.append(str(node.get("label") or node.get("title") or "").strip())
        kids = _children(node)
        if level > 3:
            flags["deep"] = True
        if len(kids) == 1:
            flags["single"] = True
        if kids:
            for kid in kids:
                walk(kid, level + 1)
        else:
            leaves.append(node)

    for node in top:
        walk(node, 1)
    if all(not _children(node) for node in top):
        issues.append("知识树只有一层，大知识点下必须有细分知识或基础单元")
    if flags["deep"]:
        issues.append("出现了第四层，树最多三层")
    if flags["single"]:
        issues.append("存在只有一个下级的节点，应合并到上级或补充同级")
    if len(leaves) < 6:
        issues.append(f"基础知识单元只有 {len(leaves)} 个，粒度太粗，应为 8–24 个")
    if len(leaves) > 30:
        issues.append(f"基础知识单元有 {len(leaves)} 个，粒度太细，应为 8–24 个")
    seen: set[str] = set()
    duplicates: set[str] = set()
    for label in labels:
        if label and label in seen:
            duplicates.add(label)
        seen.add(label)
    if duplicates:
        issues.append("存在重复标签：" + "、".join(sorted(duplicates)[:5]))
    generic = [label for label in labels if _GENERIC_RE.match(label)]
    if generic:
        issues.append("存在空泛标签：" + "、".join(generic[:5]))
    weighted = [leaf.get("weight") for leaf in leaves if isinstance(leaf.get("weight"), (int, float)) and leaf.get("weight") > 0]
    if leaves and len(weighted) * 2 < len(leaves):
        issues.append("大多数基础单元没有给出 weight")
    if len(leaves) >= 6 and len(weighted) == len(leaves) and len(set(weighted)) == 1:
        issues.append("所有基础单元比重相同，请按重要程度区分")
    missing_detail = sum(1 for leaf in leaves if not str(leaf.get("detail") or "").strip())
    if leaves and missing_detail * 3 > len(leaves):
        issues.append("超过三分之一的基础单元缺少 detail")
    return issues


def build_repair_message(original: str, previous: dict[str, Any], issues: list[str]) -> str:
    """Second-round user message: same input plus the defects of the first answer."""
    previous_text = json.dumps(previous, ensure_ascii=False)[:12000]
    return (
        f"{original}\n\n# 上一次输出存在的问题（必须全部修正）\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + f"\n\n# 上一次输出（请在此基础上修正，并输出完整 JSON）\n{previous_text}"
    )
