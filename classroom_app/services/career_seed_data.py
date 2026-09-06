"""Seed data + question bank for the career-development network feature.

* ``SOFTWARE_ENGINEERING_NETWORK`` — the built-in software-engineering career
  network (大类 CATS + 方向 NODES + 跨方向分叉 LINKS), ported from the design
  reference and revised as non-market career exploration. Only 软件工程 uses
  this seed; other majors start with their own family baseline.
* ``CAREER_PERSONALITY_QUESTIONS`` — a versioned RIASEC vocational-interest
  questionnaire with work-value items. Answers contribute explainable interest
  references; they are neither diagnoses nor recruiting qualifications.

Nothing here is engine specific; the service layer caches the network into
``career_major_networks`` and uses the question bank to score a student.
"""

from __future__ import annotations

import re
from typing import Any

# --- 大类（赛道） ------------------------------------------------------------
SE_CATS: list[dict[str, Any]] = [
    {"id": "A", "name": "技术开发赛道", "desc": "软件开发与工程实践，结合具体岗位核对能力要求", "icon": "💻", "c1": "#6ee7ff", "c2": "#3b82f6"},
    {"id": "B", "name": "产品与管理赛道", "desc": "连接需求分析、技术实现与协作交付", "icon": "🧭", "c1": "#a78bfa", "c2": "#7c3aed"},
    {"id": "C", "name": "外语 + 技术特色赛道", "desc": "跨语言协作、国际化产品与技术传播", "icon": "🌏", "c1": "#34d399", "c2": "#059669"},
    {"id": "D", "name": "公共部门与组织信息化", "desc": "按实际招录条件了解公共部门与组织信息化岗位", "icon": "🏛️", "c1": "#fbbf24", "c2": "#d97706"},
    {"id": "E", "name": "深造与创业赛道", "desc": "研究、继续学习与独立职业实践的探索", "icon": "🚀", "c1": "#fb7185", "c2": "#e11d48"},
]

SE_EXPLORATION_REASON = "用课程、项目、实习或作品验证你对实际工作的兴趣与能力，再结合具体岗位要求选择准备重点。"
SE_MARKET_NOTE = "此为专业探索知识，不代表当前招聘数量、薪资或录用机会；请核对有来源的具体岗位与资格条件。"

# --- 方向（节点） ------------------------------------------------------------
# riasec: 该方向最相关的霍兰德兴趣代码，用于按测试结果加权高亮。
SE_NODES: list[dict[str, Any]] = [
    {"cat": "A", "tag": "A1", "name": "后端开发工程师", "rec": 3, "lang": False, "riasec": ["I", "R", "C"],
     "desc": "负责服务端业务逻辑、数据库、接口与系统集成。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["精通一门后端语言(Java/Go/Python/Node)", "数据结构与算法、操作系统、网络", "MySQL + Redis + HTTP/RESTful"],
     "know": ["Spring Boot / Gin / Django 框架", "数据库进阶:索引、事务、SQL优化、分库分表", "消息队列、ElasticSearch", "Git/Linux/Docker/CI-CD", "一个拿得出手的上线项目"],
     "tl": [["探索阶段", "初级后端 / 实习", "写业务接口、改 bug、读懂老代码"], ["入门阶段", "中级 / 高级后端", "独立负责模块、设计表结构、性能优化、带实习生"], ["发展阶段", "资深 / 技术专家 / Tech Lead", "主导架构、技术选型、攻坚高并发，或转团队管理"], ["进阶阶段", "架构师 / 研发经理 / CTO", "定义技术战略、跨团队协作、培养梯队"]],
     "branch": "→ 全栈 / 数据工程 / 云原生架构 / 技术管理 / 技术创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A2", "name": "前端开发工程师", "rec": 3, "lang": False, "riasec": ["A", "I", "R"],
     "desc": "实现浏览器界面、交互组件和前端工程流程。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["HTML/CSS/JavaScript(ES6+) 熟练", "React 或 Vue 至少一个", "算法、网络、浏览器原理"],
     "know": ["TypeScript、状态管理、Vite/Webpack", "工程化:组件库、性能优化、前端监控", "跨端:小程序/RN/Flutter/Electron", "Node.js(向全栈延伸的关键)"],
     "tl": [["探索阶段", "初级前端", "还原设计稿、写组件、对接接口"], ["入门阶段", "中 / 高级前端", "架构组件库、性能与体验优化、负责复杂交互"], ["发展阶段", "资深 / 前端架构师 / Leader", "制定前端规范、搭建工程体系、跨端方案"], ["进阶阶段", "大前端负责人 / 全栈专家 / 转 PM", "团队与技术体系负责人"]],
     "branch": "→ 全栈 / UI-UX / 产品经理 / 游戏(Web3D)",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A3", "name": "全栈工程师", "rec": 3, "lang": False, "riasec": ["I", "R", "E"],
     "desc": "连接前后端实现、数据存储和部署交付。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["先在前端或后端有一门真正扎实", "基本部署运维(Linux/Docker/Nginx/云)"],
     "know": ["一套能打的栈:React/Vue + Node/Java + MySQL + Docker + 云", "数据库设计、API、鉴权、支付、第三方集成", "善用 AI 工具放大单兵产能", "产品思维:聊需求、做原型、定优先级"],
     "tl": [["探索阶段", "从前端或后端单点切入", "先精一端"], ["入门阶段", "全栈工程师", "独立负责小型产品/模块全链路"], ["发展阶段", "资深全栈 / 技术合伙人 / 独立开发", "端到端交付、带小团队、可创业"], ["进阶阶段", "技术创始人 / CTO / SaaS 老板", "用技术直接创造商业价值"]],
     "branch": "→ 独立开发/创业 / 海外远程 / 技术管理 / 跨境电商自建站",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A4", "name": "移动端开发", "rec": 3, "lang": False, "riasec": ["R", "I", "A"],
     "desc": "开发和维护移动应用的界面、功能与运行体验。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["Kotlin/Java 或 Swift", "移动端 UI、网络、存储、生命周期"],
     "know": ["跨端框架 Flutter/React Native", "性能优化、上架流程", "鸿蒙 ArkTS(国内新增量)"],
     "tl": [["探索阶段", "初级客户端", "负责基础界面与功能"], ["入门阶段", "中 / 高级客户端", "负责模块/架构"], ["发展阶段", "客户端架构师 / 移动 Leader", "技术方案与团队"], ["进阶阶段", "移动端负责人 / 转全栈或管理", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 全栈 / 游戏 / 出海 App / 管理",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A5", "name": "测试 / 测试开发(SDET)", "rec": 3, "lang": False, "riasec": ["C", "I", "R"],
     "desc": "设计测试用例、自动化验证和软件质量流程。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["测试理论与用例设计", "测开需一门语言(Python/Java)"],
     "know": ["自动化:Selenium/Appium/Playwright", "接口测试 Postman/JMeter、性能 JMeter", "CI/CD、测试平台开发"],
     "tl": [["探索阶段", "测试工程师", "执行测试、写用例"], ["入门阶段", "自动化 / 测试开发工程师", "搭自动化框架、性能测试"], ["发展阶段", "资深测开 / 测试架构师 / 质量负责人", "质量体系与平台"], ["进阶阶段", "质量总监 / 转开发或 DevOps", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ DevOps / 后端 / 安全测试 / 质量管理",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A6", "name": "运维 / DevOps / SRE", "rec": 3, "lang": False, "riasec": ["R", "C", "I"],
     "desc": "维护系统运行、部署流程、可观测性和故障处理。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["Linux 精通、网络基础", "至少一门脚本(Shell/Python)"],
     "know": ["Docker、Kubernetes(核心竞争力)", "CI/CD、监控 Prometheus/Grafana", "云平台(阿里云/AWS)、Terraform"],
     "tl": [["探索阶段", "运维 / 实施", "系统部署与维护"], ["入门阶段", "DevOps 工程师", "自动化交付流水线"], ["发展阶段", "SRE / 云原生工程师 / 运维架构师", "稳定性与平台"], ["进阶阶段", "基础架构负责人 / 技术总监", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 云原生架构 / 安全(DevSecOps) / 管理",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A7", "name": "数据方向(分析/工程/科学)", "rec": 3, "lang": False, "riasec": ["I", "C", "E"],
     "desc": "围绕数据整理、分析、工程或研究解决具体问题。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["SQL(所有数据岗硬门槛)", "Python + 统计学基础", "强业务理解力"],
     "know": ["BI:Tableau/Power BI", "数据仓库、ETL、Spark/Flink/Hadoop(工程方向)", "机器学习、统计建模(科学方向)"],
     "tl": [["探索阶段", "初级数据分析师", "跑数出报表"], ["入门阶段", "业务 / 高级分析师", "建指标体系、驱动决策"], ["发展阶段", "数据分析专家 / 数据产品经理", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "数据负责人 / CDO 首席数据官 / 转 AI", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 算法/AI / 数据产品经理 / 数据工程后端 / 跨境电商数据运营",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A8", "name": "人工智能 / 算法 / 大模型", "rec": 3, "lang": False, "riasec": ["I", "A", "R"],
     "desc": "探索机器学习、模型应用和系统评估等不同分支。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["Python、扎实编程", "会调用大模型 API", "理解 RAG/向量库/Agent/提示工程", "(研究方向)线代/概率/ML/PyTorch"],
     "know": ["大模型应用:RAG、Agent、微调、提示工程", "向量数据库、LangChain 类框架", "(研究)深度学习、顶会论文"],
     "tl": [["探索阶段", "AI 应用开发", "做 RAG/Agent 产品"], ["入门阶段", "大模型应用工程师", "微调、工程化、效果优化"], ["发展阶段", "AI 应用架构师 / 算法工程师", "需补深度"], ["进阶阶段", "AI 技术专家 / AI 产品负责人 / AI 创业", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ AI 应用后端 / 数据科学 / 考研补门槛 / AI 创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A9", "name": "网络安全工程师", "rec": 3, "lang": False, "riasec": ["I", "R", "C"],
     "desc": "在授权范围内开展系统防护、安全评估与合规支持。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["网络与操作系统基础扎实、Web 原理", "至少一门脚本语言", "法律与职业道德红线(必须授权测试)"],
     "know": ["渗透测试、OWASP Top 10", "安全工具 Burp/Nmap/Metasploit", "应急响应、等保合规、CTF、考证 CISP/OSCP"],
     "tl": [["探索阶段", "安全工程师 / 渗透测试", "漏洞挖掘与防护"], ["入门阶段", "高级安全 / 安全开发", "安全方案落地"], ["发展阶段", "安全专家 / 安全架构师", "体系建设"], ["进阶阶段", "CSO 首席安全官 / 甲方安全总监", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ DevSecOps / 体制内网安岗(公安、网信) / 安全创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A10", "name": "游戏开发", "rec": 3, "lang": False, "riasec": ["A", "R", "I"],
     "desc": "实现游戏系统、交互玩法或相关开发工具。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["C++/C#", "引擎 Unity/Unreal", "数学(线代、图形学基础)"],
     "know": ["引擎深度、图形渲染、物理、网络同步", "性能优化", "一个完整的游戏 Demo 作品"],
     "tl": [["探索阶段", "游戏开发", "实现玩法"], ["入门阶段", "中 / 高级", "负责系统/玩法"], ["发展阶段", "主程 / 技术美术 / 引擎专家", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "技术总监 / 制作人 / 独立游戏人", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 移动端 / 元宇宙XR / 出海游戏 / 独立游戏创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A11", "name": "嵌入式 / 物联网", "rec": 3, "lang": False, "riasec": ["R", "I", "C"],
     "desc": "连接软件、设备和嵌入式运行环境。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["C/C++、单片机 STM32", "电子电路基础、Linux"],
     "know": ["RTOS、驱动开发", "通信协议 MQTT/CAN", "嵌入式 Linux、硬件调试"],
     "tl": [["探索阶段", "嵌入式开发", "基础驱动"], ["入门阶段", "中 / 高级", "系统设计"], ["发展阶段", "嵌入式架构师 / 系统专家", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "研发总监 / 硬件创业", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 物联网云平台 / 机器人/自动驾驶 / 智能硬件创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "A", "tag": "A12", "name": "云计算 / 云原生", "rec": 3, "lang": False, "riasec": ["R", "C", "I"],
     "desc": "设计和维护云上应用、容器平台与基础设施。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["先有后端或运维基础", "Linux、网络、容器"],
     "know": ["Kubernetes、Docker、微服务架构", "Service Mesh、Serverless", "主流云认证、IaC"],
     "tl": [["探索阶段", "云开发 / 运维", "上云基础"], ["入门阶段", "云原生工程师", "微服务与容器化"], ["发展阶段", "云架构师", "架构设计"], ["进阶阶段", "首席架构师 / 云解决方案总监", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ SRE / 后端架构 / 云厂商售前解决方案架构师(结合外语做出海客户)",
     "trend": SE_MARKET_NOTE},
    {"cat": "B", "tag": "B1", "name": "产品经理(PM)", "rec": 3, "lang": False, "riasec": ["E", "A", "I"],
     "desc": "分析用户需求、设计产品方案并协调实现与验证。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["逻辑与表达、同理心", "需求分析能力", "懂技术实现边界"],
     "know": ["原型 Axure/Figma、PRD 撰写", "数据分析、用户研究", "项目协调、行业理解"],
     "tl": [["探索阶段", "产品助理 / 专员", "需求文档与协调"], ["入门阶段", "产品经理", "独立负责产品线"], ["发展阶段", "高级 / 资深 PM / 产品总监", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "产品 VP / CPO / 创业", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 数据/AI 产品经理 / 跨境电商产品 / 管理 / 创业",
     "trend": SE_MARKET_NOTE},
    {"cat": "B", "tag": "B2", "name": "项目经理 / 技术管理", "rec": 3, "lang": False, "riasec": ["E", "C", "S"],
     "desc": "组织技术项目的计划、交付、风险和团队协作。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["通过实际技术项目积累工程与交付经验", "项目管理、沟通协调、领导力"],
     "know": ["敏捷/Scrum、PMP", "团队管理、技术规划", "跨部门协作、成本与排期"],
     "tl": [["探索阶段", "技术骨干", "攒技术与项目经验"], ["入门阶段", "Team Leader / 项目经理", "带小团队"], ["发展阶段", "研发经理 / 技术总监", "带部门"], ["进阶阶段", "VP / CTO", "定战略"]],
     "branch": "→ 架构师(偏技术) / 项目总监(偏交付) / 创业合伙人",
     "trend": SE_MARKET_NOTE},
    {"cat": "B", "tag": "B3", "name": "UI / UX 设计师", "rec": 3, "lang": False, "riasec": ["A", "E", "S"],
     "desc": "通过用户研究、界面和交互设计改善产品体验。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["设计基础、审美", "Figma/Sketch", "交互逻辑思维"],
     "know": ["交互设计、用户研究", "设计系统、可用性测试、动效", "一点前端(更受欢迎)"],
     "tl": [["探索阶段", "UI/UX 设计师", "界面与交互"], ["入门阶段", "资深设计师", "复杂体验设计"], ["发展阶段", "设计专家 / 设计 Leader", "设计体系"], ["进阶阶段", "设计总监 / 转产品", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 产品经理 / 前端 / 自由设计师",
     "trend": SE_MARKET_NOTE},
    {"cat": "C", "tag": "C1", "name": "跨境电商技术 / 运营", "rec": 3, "lang": True, "riasec": ["E", "C", "I"],
     "desc": "支持跨境业务的网站、系统、数据与运营流程。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["一门外语(英语/东盟小语种)", "基本编程或数据能力", "电商业务理解"],
     "know": ["独立站 Shopify/WordPress", "平台运营 Amazon/TikTok Shop/Lazada/Shopee", "数据分析、SEO/SEM、广告投放", "爬虫与自动化、ERP/供应链、支付与物流"],
     "tl": [["探索阶段", "跨境技术 / 运营专员", "运营与数据支持"], ["入门阶段", "运营主管 / 技术负责人", "操盘店铺或独立站"], ["发展阶段", "跨境业务负责人 / 独立站操盘手", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "跨境电商创始人 / 出海技术合伙人", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 自建独立站创业 / 企业出海 / 电商数据 / 电商产品",
     "trend": SE_MARKET_NOTE},
    {"cat": "C", "tag": "C2", "name": "企业出海 / 外企 / 海外远程", "rec": 3, "lang": True, "riasec": ["I", "E", "R"],
     "desc": "在跨语言团队中开展技术交付与国际化协作。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["扎实工程能力(任一开发方向)", "能用英语技术沟通和书面表达(多数程序员的短板,正是本校优势)"],
     "know": ["主流技术栈", "英文文档/邮件/会议能力", "GitHub 开源参与", "远程协作工具、时区与跨文化协作"],
     "tl": [["探索阶段", "出海/外企初级工程师", "对接海外业务"], ["入门阶段", "中 / 高级工程师", "对接海外团队"], ["发展阶段", "资深 / 远程独立承包", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "海外技术专家 / 远程自由职业 / 出海技术负责人", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 全栈开发 / 跨境业务 / 国际化技术协作",
     "trend": SE_MARKET_NOTE},
    {"cat": "C", "tag": "C3", "name": "技术本地化 / 技术写作", "rec": 3, "lang": True, "riasec": ["A", "S", "C"],
     "desc": "面向技术产品提供文档、内容本地化和开发者沟通。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["精通至少一门外语", "理解软件开发流程与术语"],
     "know": ["本地化工具 CAT/TMS", "i18n 工程、技术写作(API 文档/用户手册)", "Markdown/Git、开发者社区运营 DevRel"],
     "tl": [["探索阶段", "本地化 / 技术文档工程师", "翻译与文档"], ["入门阶段", "资深技术写作 / 本地化负责人", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "全球化产品负责人 / DevRel 负责人", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "国际化 / 开发者生态负责人", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 国际化产品 / 出海 / 跨境",
     "trend": SE_MARKET_NOTE},
    {"cat": "D", "tag": "D1", "name": "考公务员(信息技术岗)", "rec": 3, "lang": False, "riasec": ["C", "S", "E"],
     "desc": "了解公共部门信息技术岗位的职责与公开招录条件。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["应届生身份极宝贵(很多岗限应届)", "行测+申论备考", "部分岗需专业笔试/政审"],
     "know": ["行测、申论、面试", "信息技术岗常考计算机专业知识", "关注国考、省考、广西区考、选调时间线"],
     "tl": [["探索阶段", "科员", "熟悉业务"], ["入门阶段", "一级科员 / 副主任科员", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "科级(科长)", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "处级及以上", "视地区与机遇"]],
     "branch": "→ 组织信息化 / 事业单位或国企技术岗位，分别核对招录条件",
     "trend": SE_MARKET_NOTE},
    {"cat": "D", "tag": "D2", "name": "事业单位 / 国企 / 银行科技岗", "rec": 3, "lang": False, "riasec": ["C", "R", "S"],
     "desc": "探索组织内部的软件、信息系统与技术支持岗位。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["相应招聘考试(笔试+面试)", "银行科技岗常考编程/计算机", "部分要求应届"],
     "know": ["扎实开发基础(银行科技岗看技术)", "计算机专业课", "行测类综合知识、目标单位业务了解"],
     "tl": [["探索阶段", "技术岗员工", "参与开发与运维"], ["入门阶段", "高级工程师 / 主管", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "技术经理 / 部门负责人", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "科技部门负责人 / 中层", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 组织内部专业岗位 / 技术管理 / 其他技术方向，核对转换要求",
     "trend": SE_MARKET_NOTE},
    {"cat": "D", "tag": "D3", "name": "计算机教学与教育服务", "rec": 3, "lang": False, "riasec": ["S", "C", "A"],
     "desc": "探索计算机课程、教学资源与教育服务相关工作。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["教师需教师资格证+招教考试", "选调生需符合资格(多要求党员、优秀毕业生)"],
     "know": ["扎实专业知识+表达教学能力", "选调生需综合素质与基层适应力"],
     "tl": [["探索阶段", "教师", "站稳讲台"], ["入门阶段", "骨干教师 / 教研组长", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "专业带头人 / 教学管理", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "教育研究 / 教学管理 / 专业发展", "核对岗位资质并结合个人选择规划"]],
     "branch": "→ 教育产品 / 课程研发 / 继续深造，核对资质与招聘条件",
     "trend": SE_MARKET_NOTE},
    {"cat": "E", "tag": "E1", "name": "考研 / 保研", "rec": 3, "lang": False, "riasec": ["I", "C", "A"],
     "desc": "结合具体研究方向了解研究生培养与申请要求。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["大一大二就重视绩点(保研)", "考研需数学、英语、专业课、政治长期准备"],
     "know": ["考研四门+目标院校专业课", "科研/竞赛经历(复试加分)", "提前联系导师"],
     "tl": [["探索阶段", "学硕/专硕/跨考", "结合实际项目、岗位条件与个人选择明确下一步"], ["入门阶段", "硕士", "科研与方向"], ["发展阶段", "算法/大厂/体制内高平台", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "继续研究或职业实践", "转博/留学"]],
     "branch": "→ 研究岗位 / 工程研发 / 继续深造，按项目与岗位要求准备",
     "trend": SE_MARKET_NOTE},
    {"cat": "E", "tag": "E2", "name": "留学深造", "rec": 3, "lang": True, "riasec": ["I", "A", "E"],
     "desc": "比较不同学位项目、研究方向与后续实践选择。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["语言成绩(雅思/托福,本校外语基础是优势)", "绩点+文书/项目经历+资金"],
     "know": ["标化考试", "科研/项目背景", "目标国家就业政策(港新、英美澳、东盟)"],
     "tl": [["探索阶段", "语言+背景+文书", "结合实际项目、岗位条件与个人选择明确下一步"], ["入门阶段", "海外硕士", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "海外就业/远程 / 回国外企大厂", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "国际化职业路径", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 海外就业/远程 / 回国外企大厂 / 科研深造",
     "trend": SE_MARKET_NOTE},
    {"cat": "E", "tag": "E3", "name": "创业 / 独立开发 / 自由职业", "rec": 3, "lang": False, "riasec": ["E", "A", "R"],
     "desc": "通过小规模项目验证产品、服务和独立交付方式。",
     "reason": SE_EXPLORATION_REASON,
     "pre": ["过硬的全栈/某一硬技能", "产品思维+抗风险能力+一定积蓄"],
     "know": ["端到端交付能力", "市场与获客、商业模式、运营", "(出海)外语+海外支付/合规"],
     "tl": [["探索阶段", "副业 / 独立产品", "先小步验证"], ["入门阶段", "全职独立开发 / 接单", "结合实际项目、岗位条件与个人选择明确下一步"], ["发展阶段", "小团队 / 工作室", "结合实际项目、岗位条件与个人选择明确下一步"], ["进阶阶段", "创始人 / 一人公司大神 / 被收购", "结合实际项目、岗位条件与个人选择明确下一步"]],
     "branch": "→ 跨境创业 / 出海 SaaS / 全栈独立开发 / 失败后回流就业(技术不白学)",
     "trend": SE_MARKET_NOTE},
]

# 跨方向分叉：[fromTag, fromStage, toTag, toStage]
SE_LINKS: list[list[Any]] = [
    ["A2", 0, "A3", 0], ["A5", 1, "A1", 1], ["A7", 1, "A8", 0], ["A1", 2, "B2", 1],
    ["A3", 2, "E3", 1], ["C1", 2, "E3", 2], ["A6", 2, "A12", 2], ["B1", 1, "E3", 2],
    ["E1", 2, "A8", 2], ["A2", 2, "B1", 1], ["C2", 2, "E3", 2], ["A7", 2, "B1", 1],
]

SOFTWARE_ENGINEERING_NETWORK: dict[str, Any] = {
    "major_name": "软件工程",
    "graduate_label": "软件工程毕业生",
    "intro": "从24个专业方向了解典型职责、准备要求和可能的实践路径。阶段表示探索顺序，不承诺晋升年限；具体招聘条件和薪酬以有来源的当前职位为准。",
    "cats": SE_CATS,
    "nodes": SE_NODES,
    "links": SE_LINKS,
}

# 技术类问卷的历史专业名称；网络种子仅用于“软件工程”。
SEED_MAJOR_KEYS = {"软件工程", "软件工程技术", "软件技术"}


# --- 职业兴趣探索题（RIASEC + 工作价值观，快速7题 / 完整11题） -------------
# kind: single(单选) / multi(多选) / scale(程度) / text(填空/简答)
# 每个选项携带 RIASEC 维度权重；scale 题按所选档位线性加权两端维度。
CAREER_PERSONALITY_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q1", "kind": "single",
        "title": "如果只能选一件事做一整天且不觉得累，你会选：",
        "options": [
            {"value": "build", "label": "动手把一个东西从零搭出来、调试到能跑", "weights": {"R": 2, "I": 1}},
            {"value": "analyze", "label": "钻研一个难题、查资料、把原理想透", "weights": {"I": 2, "C": 1}},
            {"value": "create", "label": "设计界面/内容、做出好看又好用的作品", "weights": {"A": 2, "E": 1}},
            {"value": "connect", "label": "组织大家、协调推进、把事情谈成", "weights": {"E": 2, "S": 1}},
        ],
    },
    {
        "id": "q2", "kind": "single",
        "title": "做小组项目时，你最常自然承担的角色是：",
        "options": [
            {"value": "coder", "label": "核心开发，把功能真正实现出来", "weights": {"R": 2, "I": 1}},
            {"value": "planner", "label": "定方向、排优先级、对外沟通的那个人", "weights": {"E": 2, "S": 1}},
            {"value": "designer", "label": "负责设计、文案、演示，让成果出彩", "weights": {"A": 2}},
            {"value": "organizer", "label": "管进度、整理文档、确保不出错", "weights": {"C": 2, "S": 1}},
        ],
    },
    {
        "id": "q3", "kind": "multi", "max_select": 3,
        "title": "下面哪些事让你有成就感？（可多选，最多 3 项）",
        "options": [
            {"value": "ship", "label": "亲手做的产品被人真正用起来", "weights": {"R": 1, "E": 1}},
            {"value": "solve", "label": "攻克一个别人搞不定的技术难题", "weights": {"I": 2}},
            {"value": "beauty", "label": "做出审美在线、体验顺滑的东西", "weights": {"A": 2}},
            {"value": "help", "label": "帮到具体的人、被需要、被感谢", "weights": {"S": 2}},
            {"value": "lead", "label": "带着团队拿下一个目标", "weights": {"E": 2}},
            {"value": "order", "label": "把混乱的东西整理得井井有条", "weights": {"C": 2}},
        ],
    },
    {
        "id": "q4", "kind": "scale",
        "title": "“我更喜欢确定、稳定、有规则的环境，而不是高变化、高不确定的环境。”",
        "scale": {"min": 1, "max": 5, "min_label": "完全不同意（爱闯爱变）", "max_label": "非常同意（求稳）"},
        "low_weights": {"E": 2, "A": 1},
        "high_weights": {"C": 2, "S": 1},
    },
    {
        "id": "q5", "kind": "scale",
        "title": "“比起和人打交道，我更愿意和代码、数据、机器打交道。”",
        "scale": {"min": 1, "max": 5, "min_label": "完全不同意（爱与人协作）", "max_label": "非常同意（偏好独立钻研）"},
        "low_weights": {"S": 2, "E": 1},
        "high_weights": {"I": 2, "R": 1},
    },
    {
        "id": "q6", "kind": "single",
        "title": "理想中毕业 5 年后的状态，更接近哪一个？",
        "options": [
            {"value": "expert", "label": "某个技术领域里靠谱、被信任的专家", "weights": {"I": 2, "R": 1}},
            {"value": "manager", "label": "带团队、对结果负责的负责人", "weights": {"E": 2}},
            {"value": "stable", "label": "有明确流程、制度和职责边界，重视生活平衡", "weights": {"C": 2, "S": 1}},
            {"value": "free", "label": "能远程/独立/自由地靠本事吃饭", "weights": {"A": 1, "E": 1, "I": 1}},
        ],
    },
    {
        "id": "q7", "kind": "single",
        "title": "对“外语 + 出海 / 跨境 / 海外远程”这条路，你的态度是：",
        "options": [
            {"value": "love", "label": "很向往，愿意把外语练成自己的武器", "weights": {"E": 1, "A": 1, "I": 1}},
            {"value": "ok", "label": "可以接受，看机会", "weights": {"E": 1}},
            {"value": "domestic", "label": "更想在国内/家乡发展", "weights": {"C": 1, "S": 1}},
        ],
    },
    {
        "id": "q_loc", "kind": "single",
        "title": "毕业后，你更想在哪里发展？（这会直接影响为你推荐的就业方向与节奏）",
        "options": [
            {"value": "nanning", "label": "留在南宁 / 广西本地，离家近、生活成本低"},
            {"value": "newtier1", "label": "去新一线城市（成都、杭州、武汉、长沙、重庆等）"},
            {"value": "tier1", "label": "闯一线城市（北京、上海、广州、深圳）"},
            {"value": "coastal", "label": "长三角 / 珠三角等沿海发达地区"},
            {"value": "abroad", "label": "出国 / 海外发展或海外远程"},
            {"value": "flexible", "label": "哪里机会好就去哪 / 还没想好"},
        ],
    },
    {
        "id": "q8", "kind": "multi", "max_select": 2,
        "title": "选 1–2 个你愿意为之多花时间死磕的方向：",
        "options": [
            {"value": "backend", "label": "后端 / 架构 / 系统", "weights": {"I": 1, "R": 1, "C": 1}},
            {"value": "frontend", "label": "前端 / 交互 / 视觉", "weights": {"A": 2}},
            {"value": "data_ai", "label": "数据 / 人工智能", "weights": {"I": 2}},
            {"value": "product", "label": "产品 / 运营 / 管理", "weights": {"E": 2}},
            {"value": "security", "label": "网络安全 / 运维", "weights": {"R": 1, "I": 1, "C": 1}},
            {"value": "civil", "label": "考公 / 国企 / 教师", "weights": {"C": 1, "S": 1}},
        ],
    },
    {
        "id": "q9", "kind": "scale",
        "title": "“我愿意持续投入考研/考证/长期备考这类需要延迟满足的事。”",
        "scale": {"min": 1, "max": 5, "min_label": "更想尽快就业实战", "max_label": "愿意长期深造备考"},
        "low_weights": {"R": 1, "E": 1},
        "high_weights": {"I": 1, "C": 1},
    },
    {
        "id": "q10", "kind": "text", "optional": True, "max_length": 200,
        "title": "希望尝试哪些工作内容，或想补充什么职业目标？（可留空）",
        "placeholder": "例如：想参与跨语言产品项目，或先通过实习了解一个方向。补充内容用于职业建议，不作为已具备能力的证据。",
    },
]

# Non-technology majors should not be forced to choose among software-only
# directions.  This question keeps the same RIASEC scoring contract while
# making the default quick assessment useful to language, business, education,
# communication and other majors as well.
CAREER_GENERAL_FOCUS_QUESTION: dict[str, Any] = {
    "id": "q_focus", "kind": "multi", "max_select": 2,
    "title": "选 1–2 个你愿意持续投入、做出作品或成果的方向：",
    "options": [
        {"value": "language_global", "label": "语言 / 跨文化 / 国际业务", "weights": {"A": 1, "E": 1, "S": 1}},
        {"value": "research_data", "label": "数据分析 / 研究 / 规划", "weights": {"I": 2, "C": 1}},
        {"value": "education_service", "label": "教育 / 咨询 / 公共服务", "weights": {"S": 2, "C": 1}},
        {"value": "content_brand", "label": "内容 / 设计 / 品牌传播", "weights": {"A": 2, "E": 1}},
        {"value": "operations_management", "label": "运营 / 组织 / 项目管理", "weights": {"E": 2, "C": 1}},
        {"value": "digital_systems", "label": "数字技术 / 信息系统", "weights": {"I": 1, "R": 1, "C": 1}},
    ],
}

# 就业地域偏好 → 给 AI 用的自然语言描述（用于按城市定制推荐与节奏）
LOCATION_PREF_LABELS = {
    "nanning": "留在南宁 / 广西本地（看重离家近、生活成本低、本地体制内与区域产业机会）",
    "newtier1": "去新一线城市（成都、杭州、武汉、长沙、重庆等，性价比与发展机会兼顾）",
    "tier1": "闯一线城市（北京、上海、广州、深圳，机会多、竞争与成本也高）",
    "coastal": "长三角 / 珠三角等沿海发达地区（产业密集、外贸与制造业机会多）",
    "abroad": "出国 / 海外发展或海外远程（看重国际化、语言与跨境机会）",
    "flexible": "地点灵活，哪里机会好去哪 / 尚未确定",
}

RIASEC_LABELS = {
    "R": "实干型 Realistic（动手、工程、硬技能）",
    "I": "研究型 Investigative（钻研、分析、求真）",
    "A": "艺术型 Artistic（创意、审美、表达）",
    "S": "社会型 Social（助人、沟通、协作）",
    "E": "企业型 Enterprising（领导、说服、经营）",
    "C": "常规型 Conventional（条理、规范、稳定）",
}


def normalize_major_key(major_name: str) -> str:
    """Collapse a major name to a stable slug used as the cache key."""
    text = str(major_name or "").strip()
    if not text:
        return "unknown"
    # Only education-path/year annotations are aliases. A named specialty must
    # not silently collapse into a different major's knowledge base.
    text = re.sub(r"[（(](?:专升本|普通本科|本科|专科|高职|[一二三四五六123456]年制|20\d{2}级)[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("专业", "")
    return text or "unknown"


def score_personality_answers(answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate RIASEC dimension scores from raw answers and pick a code.

    ``answers`` is a list of ``{question_id, value}`` where value is a string
    (single/text), a list (multi) or an int (scale). Returns the normalized
    dimension scores (0–100), the top-3 Holland code, and a free-text note.
    """
    scores: dict[str, float] = {k: 0.0 for k in RIASEC_LABELS}
    by_id = {str(q["id"]): q for q in [*CAREER_PERSONALITY_QUESTIONS, CAREER_GENERAL_FOCUS_QUESTION]}
    free_text = ""
    selected_focus: list[str] = []
    location_pref = ""

    for ans in answers or []:
        if not isinstance(ans, dict):
            continue
        qid = str(ans.get("question_id") or ans.get("id") or "")
        question = by_id.get(qid)
        if not question:
            continue
        kind = question.get("kind")
        value = ans.get("value")

        if qid == "q_loc":
            location_pref = str(value if not isinstance(value, list) else (value[0] if value else "")).strip()
            continue

        if kind in ("single", "multi"):
            chosen = value if isinstance(value, list) else [value]
            opt_by_value = {str(o["value"]): o for o in question.get("options", [])}
            for picked in chosen:
                opt = opt_by_value.get(str(picked))
                if not opt:
                    continue
                for dim, w in (opt.get("weights") or {}).items():
                    scores[dim] = scores.get(dim, 0.0) + float(w)
                if qid in ("q8", "q_focus"):
                    selected_focus.append(str(opt.get("label") or picked))
        elif kind == "scale":
            try:
                rating = int(value)
            except (TypeError, ValueError):
                continue
            cfg = question.get("scale", {})
            lo, hi = int(cfg.get("min", 1)), int(cfg.get("max", 5))
            span = max(hi - lo, 1)
            # 0 at min .. 1 at max
            frac = max(0.0, min(1.0, (rating - lo) / span))
            for dim, w in (question.get("high_weights") or {}).items():
                scores[dim] = scores.get(dim, 0.0) + float(w) * frac
            for dim, w in (question.get("low_weights") or {}).items():
                scores[dim] = scores.get(dim, 0.0) + float(w) * (1.0 - frac)
        elif kind == "text":
            free_text = str(value or "").strip()

    max_score = max(scores.values()) if scores else 0.0
    normalized = {
        dim: round((val / max_score) * 100) if max_score > 0 else 0
        for dim, val in scores.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    holland_code = "".join(dim for dim, value in ranked[:3] if value > 0)
    top_dims = [
        {"dim": dim, "label": RIASEC_LABELS[dim], "score": normalized[dim]}
        for dim, _ in ranked[:3]
        if normalized[dim] > 0
    ]
    return {
        "scores": normalized,
        "holland_code": holland_code,
        "top_dims": top_dims,
        "focus_choices": selected_focus,
        "free_text": free_text,
        "location_pref": location_pref,
        "location_label": LOCATION_PREF_LABELS.get(location_pref, ""),
    }
