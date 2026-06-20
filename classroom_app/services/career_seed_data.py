"""Seed data + question bank for the career-development network feature.

* ``SOFTWARE_ENGINEERING_NETWORK`` — the built-in software-engineering career
  network (大类 CATS + 方向 NODES + 跨方向分叉 LINKS), ported from the design
  reference. Acts both as the live seed for 软件工程 students and as a worked
  example handed to the deep-thinking AI when it generates a network for any
  other major (网络工程、文科 …) so the output stays structurally identical.
* ``CAREER_PERSONALITY_QUESTIONS`` — a ≤10-item career-personality test based on
  Holland's RIASEC vocational-interest model (the most widely used, authoritative
  career framework for university students) plus two work-value items. Each
  answer maps to RIASEC dimension weights used to bias recommendations.

Nothing here is engine specific; the service layer caches the network into
``career_major_networks`` and uses the question bank to score a student.
"""

from __future__ import annotations

import re
from typing import Any

# --- 大类（赛道） ------------------------------------------------------------
SE_CATS: list[dict[str, Any]] = [
    {"id": "A", "name": "技术开发赛道", "desc": "工程师 / IC 路线 · 容量最大、最对口", "icon": "💻", "c1": "#6ee7ff", "c2": "#3b82f6"},
    {"id": "B", "name": "产品与管理赛道", "desc": "懂技术的人做产品与管理更有优势", "icon": "🧭", "c1": "#a78bfa", "c2": "#7c3aed"},
    {"id": "C", "name": "外语 + 技术特色赛道", "desc": "本校王牌 · 技术 + 外语的稀缺组合 ⭐", "icon": "🌏", "c1": "#34d399", "c2": "#059669"},
    {"id": "D", "name": "体制内与稳定赛道", "desc": "稳定抗周期 · 软工是体制内最受欢迎专业之一", "icon": "🏛️", "c1": "#fbbf24", "c2": "#d97706"},
    {"id": "E", "name": "深造与创业赛道", "desc": "读研留学提升门槛 · 创业独立开发", "icon": "🚀", "c1": "#fb7185", "c2": "#e11d48"},
]

# --- 方向（节点） ------------------------------------------------------------
# riasec: 该方向最相关的霍兰德兴趣代码，用于按测试结果加权高亮。
SE_NODES: list[dict[str, Any]] = [
    {"cat": "A", "tag": "A1", "name": "后端开发工程师", "rec": 5, "lang": False, "riasec": ["I", "R", "C"],
     "desc": "服务端逻辑、数据库、接口与分布式系统的建造者，最硬通货。",
     "reason": "岗位基数最大、技术沉淀深、越老越吃香，转架构/管理/创业都顺，是普通本科最稳妥的主航道。",
     "pre": ["精通一门后端语言(Java/Go/Python/Node)", "数据结构与算法、操作系统、网络", "MySQL + Redis + HTTP/RESTful"],
     "know": ["Spring Boot / Gin / Django 框架", "数据库进阶:索引、事务、SQL优化、分库分表", "消息队列、ElasticSearch", "Git/Linux/Docker/CI-CD", "一个拿得出手的上线项目"],
     "tl": [["0–1 年", "初级后端 / 实习", "写业务接口、改 bug、读懂老代码 · 南宁6-9k / 一线10-18k"],
            ["3–5 年", "中级 / 高级后端", "独立负责模块、设计表结构、性能优化、带实习生 · 一线18-35k"],
            ["5–10 年", "资深 / 技术专家 / Tech Lead", "主导架构、技术选型、攻坚高并发，或转团队管理 · 一线35-60k+"],
            ["10 年+", "架构师 / 研发经理 / CTO", "定义技术战略、跨团队协作、培养梯队 · 50-100k+含股权"]],
     "branch": "→ 全栈 / 数据工程 / 云原生架构 / 技术管理 / 技术创业",
     "trend": "基础 CRUD 被 AI 接管，但分布式架构、高并发、AI 应用后端(RAG/向量库/Agent)门槛升高。要主动学'用 AI 提效'和'为 AI 应用做后端'。"},
    {"cat": "A", "tag": "A2", "name": "前端开发工程师", "rec": 4, "lang": False, "riasec": ["A", "I", "R"],
     "desc": "把设计与数据变成用户能点能用的界面，入门相对友好。",
     "reason": "门槛比后端低、成果可视成就感强；但初级岗最易被低代码/AI 冲击，必须往全栈或工程化走深。",
     "pre": ["HTML/CSS/JavaScript(ES6+) 熟练", "React 或 Vue 至少一个", "算法、网络、浏览器原理"],
     "know": ["TypeScript、状态管理、Vite/Webpack", "工程化:组件库、性能优化、前端监控", "跨端:小程序/RN/Flutter/Electron", "Node.js(向全栈延伸的关键)"],
     "tl": [["0–1 年", "初级前端", "还原设计稿、写组件、对接接口"],
            ["3–5 年", "中 / 高级前端", "架构组件库、性能与体验优化、负责复杂交互"],
            ["5–10 年", "资深 / 前端架构师 / Leader", "制定前端规范、搭建工程体系、跨端方案"],
            ["10 年+", "大前端负责人 / 全栈专家 / 转 PM", "团队与技术体系负责人"]],
     "branch": "→ 全栈 / UI-UX / 产品经理 / 游戏(Web3D)",
     "trend": "低代码与 AI 生成 UI 普及，纯'切图仔'淘汰。出路是工程化深度 + 全栈能力 + 理解用户需求。"},
    {"cat": "A", "tag": "A3", "name": "全栈工程师", "rec": 5, "lang": False, "riasec": ["I", "R", "E"],
     "desc": "前端+后端+部署，一个人把产品从需求做到上线。AI 时代最有机会。",
     "reason": "AI 把前后端、测试、部署都能代劳后，能独立交付完整产品的人反而最稀缺。对中小公司、创业、独立开发、出海都极吃香。",
     "pre": ["先在前端或后端有一门真正扎实", "基本部署运维(Linux/Docker/Nginx/云)"],
     "know": ["一套能打的栈:React/Vue + Node/Java + MySQL + Docker + 云", "数据库设计、API、鉴权、支付、第三方集成", "善用 AI 工具放大单兵产能", "产品思维:聊需求、做原型、定优先级"],
     "tl": [["0–1 年", "从前端或后端单点切入", "先精一端"],
            ["3–5 年", "全栈工程师", "独立负责小型产品/模块全链路"],
            ["5–10 年", "资深全栈 / 技术合伙人 / 独立开发", "端到端交付、带小团队、可创业"],
            ["10 年+", "技术创始人 / CTO / SaaS 老板", "用技术直接创造商业价值"]],
     "branch": "→ 独立开发/创业 / 海外远程 / 技术管理 / 跨境电商自建站",
     "trend": "中大型企业也开始转向全栈。'懂产品的全栈'= 未来十年最保值的工程师画像之一。"},
    {"cat": "A", "tag": "A4", "name": "移动端开发", "rec": 3, "lang": False, "riasec": ["R", "I", "A"],
     "desc": "Android / iOS 手机 App 客户端开发，市场趋于成熟。",
     "reason": "原生岗位收缩、被跨端框架挤压；但出海 App、车机、鸿蒙带来新增量。建议结合跨端或出海做。",
     "pre": ["Kotlin/Java 或 Swift", "移动端 UI、网络、存储、生命周期"],
     "know": ["跨端框架 Flutter/React Native", "性能优化、上架流程", "鸿蒙 ArkTS(国内新增量)"],
     "tl": [["0–1 年", "初级客户端", "负责基础界面与功能"],
            ["3–5 年", "中 / 高级客户端", "负责模块/架构"],
            ["5–10 年", "客户端架构师 / 移动 Leader", "技术方案与团队"],
            ["10 年+", "移动端负责人 / 转全栈或管理", "—"]],
     "branch": "→ 全栈 / 游戏 / 出海 App / 管理",
     "trend": "押注鸿蒙生态(政策红利)或出海 App(结合外语)是差异化突破口。"},
    {"cat": "A", "tag": "A5", "name": "测试 / 测试开发(SDET)", "rec": 4, "lang": False, "riasec": ["C", "I", "R"],
     "desc": "保障软件质量，从手工测试升级为会写代码的测试开发。",
     "reason": "纯手工测试易被替代、天花板低；但测试开发写自动化框架、做性能/安全测试，薪资逼近开发岗、竞争小，是曲线进大厂的好通道。",
     "pre": ["测试理论与用例设计", "测开需一门语言(Python/Java)"],
     "know": ["自动化:Selenium/Appium/Playwright", "接口测试 Postman/JMeter、性能 JMeter", "CI/CD、测试平台开发"],
     "tl": [["0–1 年", "测试工程师", "执行测试、写用例"],
            ["3–5 年", "自动化 / 测试开发工程师", "搭自动化框架、性能测试"],
            ["5–10 年", "资深测开 / 测试架构师 / 质量负责人", "质量体系与平台"],
            ["10 年+", "质量总监 / 转开发或 DevOps", "—"]],
     "branch": "→ DevOps / 后端 / 安全测试 / 质量管理",
     "trend": "AI 生成用例、自愈测试普及，纯手工测试加速淘汰；测开 + AI 测试工具是方向。"},
    {"cat": "A", "tag": "A6", "name": "运维 / DevOps / SRE", "rec": 4, "lang": False, "riasec": ["R", "C", "I"],
     "desc": "保障系统稳定运行、自动化交付，进化为 SRE 后非常值钱。",
     "reason": "传统运维在被替代；但 SRE / 云原生 DevOps 是高薪稀缺岗，越老越吃香，不易被 AI 替代(决策+大局观)。",
     "pre": ["Linux 精通、网络基础", "至少一门脚本(Shell/Python)"],
     "know": ["Docker、Kubernetes(核心竞争力)", "CI/CD、监控 Prometheus/Grafana", "云平台(阿里云/AWS)、Terraform"],
     "tl": [["0–1 年", "运维 / 实施", "系统部署与维护"],
            ["3–5 年", "DevOps 工程师", "自动化交付流水线"],
            ["5–10 年", "SRE / 云原生工程师 / 运维架构师", "稳定性与平台"],
            ["10 年+", "基础架构负责人 / 技术总监", "—"]],
     "branch": "→ 云原生架构 / 安全(DevSecOps) / 管理",
     "trend": "云原生 + 平台工程 + AIOps(用 AI 做智能运维)是升级方向。"},
    {"cat": "A", "tag": "A7", "name": "数据方向(分析/工程/科学)", "rec": 5, "lang": False, "riasec": ["I", "C", "E"],
     "desc": "从数据中挖价值，三个子方向门槛与上限各不同，需求长期增长。",
     "reason": "大数据与 AI 浪潮下需求长期增长。数据分析师对非科班、对外语/商科背景友好，是文科气质院校的好切口。",
     "pre": ["SQL(所有数据岗硬门槛)", "Python + 统计学基础", "强业务理解力"],
     "know": ["BI:Tableau/Power BI", "数据仓库、ETL、Spark/Flink/Hadoop(工程方向)", "机器学习、统计建模(科学方向)"],
     "tl": [["0–1 年", "初级数据分析师", "跑数出报表"],
            ["3–5 年", "业务 / 高级分析师", "建指标体系、驱动决策"],
            ["5–10 年", "数据分析专家 / 数据产品经理", "—"],
            ["10 年+", "数据负责人 / CDO 首席数据官 / 转 AI", "—"]],
     "branch": "→ 算法/AI / 数据产品经理 / 数据工程后端 / 跨境电商数据运营",
     "trend": "会用 AI/SQL Copilot 取数的'业务+数据+表达'复合人才胜出;纯取数被自助 BI 替代。"},
    {"cat": "A", "tag": "A8", "name": "人工智能 / 算法 / 大模型", "rec": 4, "lang": False, "riasec": ["I", "A", "R"],
     "desc": "最热、薪资最高方向之一，但核心岗门槛极高。务实走 AI 应用开发。",
     "reason": "2025 AI 岗位暴增、大模型算法岗月薪 5万+;但核心算法岗基本要 985/211 硕博+论文。务实路径是做 AI 应用开发，门槛低得多、需求爆发。",
     "pre": ["Python、扎实编程", "会调用大模型 API", "理解 RAG/向量库/Agent/提示工程", "(研究方向)线代/概率/ML/PyTorch"],
     "know": ["大模型应用:RAG、Agent、微调、提示工程", "向量数据库、LangChain 类框架", "(研究)深度学习、顶会论文"],
     "tl": [["0–1 年", "AI 应用开发", "做 RAG/Agent 产品"],
            ["3–5 年", "大模型应用工程师", "微调、工程化、效果优化"],
            ["5–10 年", "AI 应用架构师 / 算法工程师", "需补深度"],
            ["10 年+", "AI 技术专家 / AI 产品负责人 / AI 创业", "—"]],
     "branch": "→ AI 应用后端 / 数据科学 / 考研补门槛 / AI 创业",
     "trend": "'会用大模型解决业务问题'的应用层人才需求远大于供给。本校学生走'工程+AI应用'务实路线;冲算法核心岗则先考研。"},
    {"cat": "A", "tag": "A9", "name": "网络安全工程师", "rec": 4, "lang": False, "riasec": ["I", "R", "C"],
     "desc": "攻防、渗透、安全防护与合规，政策驱动需求刚性。",
     "reason": "等保合规、数据安全法驱动，需求刚性、缺口大、越老越吃香、不易被 AI 替代。适合愿钻研、耐得住的人。",
     "pre": ["网络与操作系统基础扎实、Web 原理", "至少一门脚本语言", "法律与职业道德红线(必须授权测试)"],
     "know": ["渗透测试、OWASP Top 10", "安全工具 Burp/Nmap/Metasploit", "应急响应、等保合规、CTF、考证 CISP/OSCP"],
     "tl": [["0–1 年", "安全工程师 / 渗透测试", "漏洞挖掘与防护"],
            ["3–5 年", "高级安全 / 安全开发", "安全方案落地"],
            ["5–10 年", "安全专家 / 安全架构师", "体系建设"],
            ["10 年+", "CSO 首席安全官 / 甲方安全总监", "—"]],
     "branch": "→ DevSecOps / 体制内网安岗(公安、网信) / 安全创业",
     "trend": "AI 攻防、数据安全、车联网/物联网安全是新增长点;体制内安全岗也是稳定出路。"},
    {"cat": "A", "tag": "A10", "name": "游戏开发", "rec": 3, "lang": False, "riasec": ["A", "R", "I"],
     "desc": "游戏客户端/服务端/引擎开发，兴趣驱动强但行业周期性强。",
     "reason": "兴趣浓者成就感极高，出海游戏(结合外语)薪资可观;但版号政策、行业波动、加班是现实，需谨慎。",
     "pre": ["C++/C#", "引擎 Unity/Unreal", "数学(线代、图形学基础)"],
     "know": ["引擎深度、图形渲染、物理、网络同步", "性能优化", "一个完整的游戏 Demo 作品"],
     "tl": [["0–1 年", "游戏开发", "实现玩法"],
            ["3–5 年", "中 / 高级", "负责系统/玩法"],
            ["5–10 年", "主程 / 技术美术 / 引擎专家", "—"],
            ["10 年+", "技术总监 / 制作人 / 独立游戏人", "—"]],
     "branch": "→ 移动端 / 元宇宙XR / 出海游戏 / 独立游戏创业",
     "trend": "出海游戏 + AIGC 内容生成 + XR 是机会区;国内市场竞争激烈。"},
    {"cat": "A", "tag": "A11", "name": "嵌入式 / 物联网", "rec": 3, "lang": False, "riasec": ["R", "I", "C"],
     "desc": "软硬件结合，单片机、驱动、智能硬件，需求稳定竞争小。",
     "reason": "制造业、新能源、机器人需求稳定，竞争比互联网小、相对不易裁;但薪资增长慢、地域受限(珠三角/长三角)。",
     "pre": ["C/C++、单片机 STM32", "电子电路基础、Linux"],
     "know": ["RTOS、驱动开发", "通信协议 MQTT/CAN", "嵌入式 Linux、硬件调试"],
     "tl": [["0–1 年", "嵌入式开发", "基础驱动"],
            ["3–5 年", "中 / 高级", "系统设计"],
            ["5–10 年", "嵌入式架构师 / 系统专家", "—"],
            ["10 年+", "研发总监 / 硬件创业", "—"]],
     "branch": "→ 物联网云平台 / 机器人/自动驾驶 / 智能硬件创业",
     "trend": "新能源、机器人、AIoT、自动驾驶带来增量;适合喜欢软硬结合、沉得下心的人。"},
    {"cat": "A", "tag": "A12", "name": "云计算 / 云原生", "rec": 4, "lang": False, "riasec": ["R", "C", "I"],
     "desc": "云平台、容器、微服务、Serverless，企业上云大势所趋。",
     "reason": "企业全面上云，云原生是后端/运维的高阶延伸，薪资高、稀缺。一般作为工作几年后的进阶方向，而非应届直接切入。",
     "pre": ["先有后端或运维基础", "Linux、网络、容器"],
     "know": ["Kubernetes、Docker、微服务架构", "Service Mesh、Serverless", "主流云认证、IaC"],
     "tl": [["0–1 年", "云开发 / 运维", "上云基础"],
            ["3–5 年", "云原生工程师", "微服务与容器化"],
            ["5–10 年", "云架构师", "架构设计"],
            ["10 年+", "首席架构师 / 云解决方案总监", "—"]],
     "branch": "→ SRE / 后端架构 / 云厂商售前解决方案架构师(结合外语做出海客户)",
     "trend": "多云、混合云、云上 AI 平台是方向;云厂商解决方案架构师可结合外语做出海客户。"},
    {"cat": "B", "tag": "B1", "name": "产品经理(PM)", "rec": 4, "lang": False, "riasec": ["E", "A", "I"],
     "desc": "决定'做什么'，技术背景+沟通能力的人做 PM 很有竞争力。",
     "reason": "互联网产品经理在广西区内期望薪资最高之一(≈9.7k/月)。软工背景懂技术、能跟研发对话;对沟通好、不想一直写代码的人是优选。",
     "pre": ["逻辑与表达、同理心", "需求分析能力", "懂技术实现边界"],
     "know": ["原型 Axure/Figma、PRD 撰写", "数据分析、用户研究", "项目协调、行业理解"],
     "tl": [["0–1 年", "产品助理 / 专员", "需求文档与协调"],
            ["3–5 年", "产品经理", "独立负责产品线"],
            ["5–10 年", "高级 / 资深 PM / 产品总监", "—"],
            ["10 年+", "产品 VP / CPO / 创业", "—"]],
     "branch": "→ 数据/AI 产品经理 / 跨境电商产品 / 管理 / 创业",
     "trend": "AI 产品经理、数据产品经理是高薪热点;'懂技术+懂业务+会表达'的复合 PM 最值钱。"},
    {"cat": "B", "tag": "B2", "name": "项目经理 / 技术管理", "rec": 4, "lang": False, "riasec": ["E", "C", "S"],
     "desc": "工程师的天花板之一:带人、带项目、定战略(TL→架构师→CTO)。",
     "reason": "技术人 5-10 年后的主要进阶方向，非应届起点。管理岗收入与影响力上限高，需技术沉淀+领导力+沟通。",
     "pre": ["先有 3-5 年扎实技术积累", "项目管理、沟通协调、领导力"],
     "know": ["敏捷/Scrum、PMP", "团队管理、技术规划", "跨部门协作、成本与排期"],
     "tl": [["3–5 年", "技术骨干", "攒技术与项目经验"],
            ["5–8 年", "Team Leader / 项目经理", "带小团队"],
            ["8–12 年", "研发经理 / 技术总监", "带部门"],
            ["12 年+", "VP / CTO", "定战略"]],
     "branch": "→ 架构师(偏技术) / 项目总监(偏交付) / 创业合伙人",
     "trend": "技术管理者需懂 AI 如何改造研发流程、如何用更小团队交付更多——'AI 时代的技术领导力'是新课题。"},
    {"cat": "B", "tag": "B3", "name": "UI / UX 设计师", "rec": 3, "lang": False, "riasec": ["A", "E", "S"],
     "desc": "界面与体验设计，软件工程背景做 UX(重交互逻辑)更搭。",
     "reason": "对有审美和交互兴趣的人是好选择，懂技术的 UX 更能与研发协作;但纯 UI 受 AI 生成冲击大，需往体验+研究+产品深耕。",
     "pre": ["设计基础、审美", "Figma/Sketch", "交互逻辑思维"],
     "know": ["交互设计、用户研究", "设计系统、可用性测试、动效", "一点前端(更受欢迎)"],
     "tl": [["0–1 年", "UI/UX 设计师", "界面与交互"],
            ["3–5 年", "资深设计师", "复杂体验设计"],
            ["5–10 年", "设计专家 / 设计 Leader", "设计体系"],
            ["10 年+", "设计总监 / 转产品", "—"]],
     "branch": "→ 产品经理 / 前端 / 自由设计师",
     "trend": "AI 出图普及，'懂用户、懂业务、懂技术'的体验设计师才有壁垒;纯执行型 UI 收缩。"},
    {"cat": "C", "tag": "C1", "name": "跨境电商技术 / 运营", "rec": 5, "lang": True, "riasec": ["E", "C", "I"],
     "desc": "用技术+外语做跨境电商:建站、数据、选品、运营、ERP、独立站。",
     "reason": "跨境电商是重要新兴增长点，广西区位优势明显。软工能力(建站/爬虫/数据/自动化/ERP)+外语沟通=跨境团队稀缺复合人才;也最易过渡到创业/独立站。",
     "pre": ["一门外语(英语/东盟小语种)", "基本编程或数据能力", "电商业务理解"],
     "know": ["独立站 Shopify/WordPress", "平台运营 Amazon/TikTok Shop/Lazada/Shopee", "数据分析、SEO/SEM、广告投放", "爬虫与自动化、ERP/供应链、支付与物流"],
     "tl": [["0–1 年", "跨境技术 / 运营专员", "运营与数据支持"],
            ["3–5 年", "运营主管 / 技术负责人", "操盘店铺或独立站"],
            ["5–10 年", "跨境业务负责人 / 独立站操盘手", "—"],
            ["10 年+", "跨境电商创始人 / 出海技术合伙人", "—"]],
     "branch": "→ 自建独立站创业 / 企业出海 / 电商数据 / 电商产品",
     "trend": "TikTok 电商、东南亚市场、AI 选品与营销是风口;'会技术、懂数据、能用外语谈供应商和客户'是跨境团队最缺的人。"},
    {"cat": "C", "tag": "C2", "name": "企业出海 / 外企 / 海外远程", "rec": 5, "lang": True, "riasec": ["I", "E", "R"],
     "desc": "给出海企业或海外公司做技术，或直接拿海外远程 offer，按外币计薪。",
     "reason": "'技术+英语'打开三扇门:①出海大厂海外业务线 ②外企研发中心 ③海外远程(按美元/欧元计薪)。这是普通本科逆袭收入天花板最现实的路径之一。",
     "pre": ["扎实工程能力(任一开发方向)", "能用英语技术沟通和书面表达(多数程序员的短板,正是本校优势)"],
     "know": ["主流技术栈", "英文文档/邮件/会议能力", "GitHub 开源参与", "远程协作工具、时区与跨文化协作"],
     "tl": [["0–1 年", "出海/外企初级工程师", "对接海外业务"],
            ["3–5 年", "中 / 高级工程师", "对接海外团队"],
            ["5–10 年", "资深 / 远程独立承包", "美元计薪"],
            ["10 年+", "海外技术专家 / 远程自由职业 / 出海技术负责人", "—"]],
     "branch": "→ 全栈(远程最爱) / 独立开发出海 / 跨境 / 海外发展",
     "trend": "远程工作常态化、全球人才市场打通。英语好+某一硬技能的中国工程师在国际市场极具性价比，是被严重低估的赛道。"},
    {"cat": "C", "tag": "C3", "name": "技术本地化 / 技术写作", "rec": 3, "lang": True, "riasec": ["A", "S", "C"],
     "desc": "软件本地化(i18n/l10n)、技术文档、开发者关系 DevRel、技术翻译。",
     "reason": "把外语优势直接变现的'轻技术'岗，竞争小、节奏友好;但天花板与薪资中等，适合外语极强、不想做纯编程又懂技术的人。",
     "pre": ["精通至少一门外语", "理解软件开发流程与术语"],
     "know": ["本地化工具 CAT/TMS", "i18n 工程、技术写作(API 文档/用户手册)", "Markdown/Git、开发者社区运营 DevRel"],
     "tl": [["0–1 年", "本地化 / 技术文档工程师", "翻译与文档"],
            ["3–5 年", "资深技术写作 / 本地化负责人", "—"],
            ["5–10 年", "全球化产品负责人 / DevRel 负责人", "—"],
            ["10 年+", "国际化 / 开发者生态负责人", "—"]],
     "branch": "→ 国际化产品 / 出海 / 跨境",
     "trend": "AI 翻译冲击纯翻译，但'懂技术+懂文化+会沟通'的本地化/DevRel 反而更被需要(AI 译文需要懂行的人把关)。"},
    {"cat": "D", "tag": "D1", "name": "考公务员(信息技术岗)", "rec": 4, "lang": False, "riasec": ["C", "S", "E"],
     "desc": "进政府机关做信息化、网络安全、数据管理岗，稳定体面抗周期。",
     "reason": "软工在公考中可报岗位多(信息中心、网信办、公安网安、税务/海关信息岗)，专业对口岗竞争比文科岗小很多。稳定流首选。",
     "pre": ["应届生身份极宝贵(很多岗限应届)", "行测+申论备考", "部分岗需专业笔试/政审"],
     "know": ["行测、申论、面试", "信息技术岗常考计算机专业知识", "关注国考、省考、广西区考、选调时间线"],
     "tl": [["0–3 年", "科员", "熟悉业务"],
            ["3–8 年", "一级科员 / 副主任科员", "—"],
            ["8–15 年", "科级(科长)", "—"],
            ["15 年+", "处级及以上", "视地区与机遇"]],
     "branch": "→ 事业单位/国企 / 选调生(晋升更快)",
     "trend": "数字政府、政务信息化、数据局建设带来更多技术岗编制;稳定性是最大价值，但要接受收入上限和体制节奏。"},
    {"cat": "D", "tag": "D2", "name": "事业单位 / 国企 / 银行科技岗", "rec": 4, "lang": False, "riasec": ["C", "R", "S"],
     "desc": "进银行科技部、运营商、国企信息中心，稳定+收入尚可。",
     "reason": "介于互联网与公务员之间:比公务员收入高、比互联网稳定。银行科技岗、运营商、电网、烟草、国企信息中心都是软工对口香饽饽。",
     "pre": ["相应招聘考试(笔试+面试)", "银行科技岗常考编程/计算机", "部分要求应届"],
     "know": ["扎实开发基础(银行科技岗看技术)", "计算机专业课", "行测类综合知识、目标单位业务了解"],
     "tl": [["0–3 年", "技术岗员工", "参与开发与运维"],
            ["3–8 年", "高级工程师 / 主管", "—"],
            ["8–15 年", "技术经理 / 部门负责人", "—"],
            ["15 年+", "科技部门负责人 / 中层", "—"]],
     "branch": "→ 公务员(部分人转考) / 互联网(年轻时反向流动) / 内部晋升",
     "trend": "金融科技、数字化转型让银行/国企科技岗需求增大;编制内技术岗是'稳定+不低收入'的优质选择。"},
    {"cat": "D", "tag": "D3", "name": "教师(中职/培训)+ 选调生", "rec": 3, "lang": False, "riasec": ["S", "C", "A"],
     "desc": "当计算机/编程老师，或走选调生快速晋升通道。",
     "reason": "中职/职校计算机教师需求稳定(需教师资格证，部分需考研);编程培训讲师门槛低但行业波动大;选调生晋升快但通常要求党员/学生干部/成绩优异。",
     "pre": ["教师需教师资格证+招教考试", "选调生需符合资格(多要求党员、优秀毕业生)"],
     "know": ["扎实专业知识+表达教学能力", "选调生需综合素质与基层适应力"],
     "tl": [["0–3 年", "教师", "站稳讲台"],
            ["3–10 年", "骨干教师 / 教研组长", "—"],
            ["10 年+", "专业带头人 / 教学管理", "—"],
            ["—", "(选调生)沿行政序列晋升", "—"]],
     "branch": "→ 考研后进高校/更好学校 / 转培训创业 / 教育产品",
     "trend": "职业教育受政策支持、计算机师资紧缺;但需接受教师的收入与节奏特征。"},
    {"cat": "E", "tag": "E1", "name": "考研 / 保研", "rec": 4, "lang": False, "riasec": ["I", "C", "A"],
     "desc": "通过读研提升学历门槛，转入更高天花板的方向(尤其算法/AI)。",
     "reason": "想冲算法/AI 核心岗、大厂、科研、体制内更好岗位的同学，硕士学历能显著打开门(学历溢价明显)。但要权衡时间成本，为目标读研而非逃避就业。",
     "pre": ["大一大二就重视绩点(保研)", "考研需数学、英语、专业课、政治长期准备"],
     "know": ["考研四门+目标院校专业课", "科研/竞赛经历(复试加分)", "提前联系导师"],
     "tl": [["选择", "学硕/专硕/跨考", "—"],
            ["在读", "硕士", "科研与方向"],
            ["毕业", "算法/大厂/体制内高平台", "—"],
            ["长期", "更高天花板", "转博/留学"]],
     "branch": "→ 算法/AI / 大厂研发 / 体制内更高平台 / 留学",
     "trend": "AI、数据、网安方向考研性价比高;但研究生不是万能药，要想清楚'读研是为了进入哪个具体方向'。"},
    {"cat": "E", "tag": "E2", "name": "留学深造", "rec": 3, "lang": True, "riasec": ["I", "A", "E"],
     "desc": "出国/出境读硕，提升背景+拓展国际视野和海外就业可能。",
     "reason": "适合家庭有经济支持、英语好(本校优势)、想走国际化路线的同学。可衔接海外远程、外企。但成本高、回报因人而异，需理性评估。",
     "pre": ["语言成绩(雅思/托福,本校外语基础是优势)", "绩点+文书/项目经历+资金"],
     "know": ["标化考试", "科研/项目背景", "目标国家就业政策(港新、英美澳、东盟)"],
     "tl": [["申请", "语言+背景+文书", "—"],
            ["在读", "海外硕士", "—"],
            ["毕业", "海外就业/远程 / 回国外企大厂", "—"],
            ["长期", "国际化职业路径", "—"]],
     "branch": "→ 海外就业/远程 / 回国外企大厂 / 科研深造",
     "trend": "性价比留学(港新、东南亚、欧洲部分国家)兴起;外语强+技术的人留学回报更高，可结合广西-东盟战略走东南亚路线。"},
    {"cat": "E", "tag": "E3", "name": "创业 / 独立开发 / 自由职业", "rec": 3, "lang": False, "riasec": ["E", "A", "R"],
     "desc": "自己做产品、接项目、做独立开发者，AI 时代单兵作战能力空前增强。",
     "reason": "独立开发者/一人公司前所未有地可行(全栈+AI+出海能让一个人养活一个产品)。但失败率高，建议先积累 3-5 年经验、攒下作品和人脉再上，而非应届裸创。",
     "pre": ["过硬的全栈/某一硬技能", "产品思维+抗风险能力+一定积蓄"],
     "know": ["端到端交付能力", "市场与获客、商业模式、运营", "(出海)外语+海外支付/合规"],
     "tl": [["0–3 年", "副业 / 独立产品", "先小步验证"],
            ["3–5 年", "全职独立开发 / 接单", "—"],
            ["5–10 年", "小团队 / 工作室", "—"],
            ["10 年+", "创始人 / 一人公司大神 / 被收购", "—"]],
     "branch": "→ 跨境创业 / 出海 SaaS / 全栈独立开发 / 失败后回流就业(技术不白学)",
     "trend": "AI+出海+独立开发是这个时代最性感的组合;门槛降低但竞争激烈，关键是找到真实需求、持续交付。"},
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
    "intro": "在双一流硕士扎堆大厂的内卷格局里，普通本科要打差异化牌——技术 + 外语是本校学子区别于纯理工院校的最大资本。",
    "cats": SE_CATS,
    "nodes": SE_NODES,
    "links": SE_LINKS,
}

# 哪些专业直接复用软工种子（按归一化 key）。
SEED_MAJOR_KEYS = {"软件工程", "软件工程技术", "软件技术"}


# --- 职业性格测试题（霍兰德 RIASEC + 工作价值观，≤10 题） ---------------------
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
            {"value": "stable", "label": "体制内/大厂里稳定体面、生活平衡", "weights": {"C": 2, "S": 1}},
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
        "title": "用一两句话说说：你心里最想成为什么样的人，或最担心的是什么？（可留空）",
        "placeholder": "例如：想做能独立做产品的全栈；担心普通本科找不到好工作……（只用于帮 AI 更懂你，不会公开）",
    },
]

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
    # Strip parenthetical 方向/年级 noise and whitespace.
    text = re.sub(r"[（(].*?[）)]", "", text)
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
    by_id = {str(q["id"]): q for q in CAREER_PERSONALITY_QUESTIONS}
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
                if qid in ("q8",):
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
    holland_code = "".join(dim for dim, _ in ranked[:3])
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
