# 课堂管理平台 V4.0 (Multi-Tenant) - 项目完整描述

> 本文档旨在全面描述项目架构、运行逻辑和关键实现细节，方便后续升级改造时快速理解项目全貌。

---

## 一、项目概述

这是一个面向教育场景的**课堂管理平台**，支持多教师、多班级、多课程的**多租户架构**。教师可以管理班级、创建课程、上传资源、布置作业并利用 AI 自动批改；学生可以登录查看资源、提交作业、参与课堂实时聊天和 AI 助教对话。

### 版本演进脉络
- **V1~V2**: 单课堂单课程，基于 tkinter GUI
- **V3.x**: 引入 Web 界面 (FastAPI)、AI 批改、实时聊天
- **V4.0**: 多租户重构，教师注册/登录，班级-课程-课堂三层数据模型
- **V4.1**: 管理中心 (班级/课程/课堂/AI 配置)
- **V4.2**: AI 聊天系统 (多会话、用户画像)
- **V4.3**: AI 聊天改为流式传输 (SSE)

---

## 二、系统架构

### 2.1 双进程微服务架构

```
┌─────────────────────────────────────────────────────────┐
│                     用户浏览器                            │
│    教师 ──→ /teacher/login   学生 ──→ /student/login     │
└────────────┬──────────────────────────┬─────────────────┘
             │ HTTP / WebSocket         │
             ▼                          │
┌────────────────────────┐              │
│  Nginx (可选, Docker)   │◄────────────┘
│  :80 反向代理           │
│  - /static/ → 静态文件  │
│  - / → 代理到 :8000     │
│  - WebSocket 支持       │
└───────────┬────────────┘
            │
            ▼
┌───────────────────────────────────────────────────────────┐
│           主应用 (main.py → classroom_app/)                │
│                     FastAPI :8000                          │
│                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ UI 路由   │  │ 文件路由  │  │ 作业路由  │  │ AI 路由   │ │
│  │ ui.py    │  │ files.py │  │homework.py│ │  ai.py   │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────┬────┘ │
│  ┌──────────┐  ┌──────────┐                       │      │
│  │ 管理路由  │  │ 会话路由  │                       │      │
│  │manage.py│  │session.py│                       │      │
│  └──────────┘  └──────────┘                       │      │
│                                                  │      │
│  ┌─────────────────────┐  ┌──────────────────┐   │      │
│  │  服务层 (services/)  │  │ SQLite 数据库     │   │      │
│  │ - chat_handler.py   │  │ data/classroom.db │   │      │
│  │ - file_handler.py   │  └──────────────────┘   │      │
│  │ - file_service.py   │                         │      │
│  │ - roster_handler.py │                         │      │
│  └─────────────────────┘                         │      │
│                                                  │      │
│  HTTP 回调 ←────────────────────────────────────────┘      │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP (httpx AsyncClient)
                       ▼
┌───────────────────────────────────────────────────────────┐
│         AI 助教服务 (ai_assistant.py)                       │
│                    FastAPI :8001                           │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  多平台 AI 调度器                                     │ │
│  │  优先级: volcengine → deepseek → siliconflow         │ │
│  │                                                      │ │
│  │  能力模型映射:                                        │ │
│  │  - standard: 常规对话/生成                             │ │
│  │  - thinking: 推理增强 (批改/画像)                      │ │
│  │  - vision: 图片理解 (图片类作业)                       │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  端点:                                                    │
│  POST /api/ai/generate-assignment  → 生成作业              │
│  POST /api/ai/submit-grading-job  → 异步批改              │
│  POST /api/ai/chat                → 非流式聊天             │
│  POST /api/ai/chat-stream         → 流式聊天               │
└───────────────────────────────────────────────────────────┘
```

### 2.2 启动流程

**本地开发:**
```bash
# 终端 1: 启动主应用
python main.py          # → Uvicorn :8000, reload=True

# 终端 2: 启动 AI 助教
python ai_assistant.py  # → Uvicorn :8001
```

**Docker 部署:**
```bash
docker-compose up -d    # Nginx:80 → App:8000 → AI:8001
```

**启动时 `main.py` 做了什么:**
1. `load_dotenv()` 加载 `.env`
2. 创建所有必要目录 (data, homework_submissions, shared_files 等)
3. `init_database()` 初始化 SQLite (WAL 模式)
4. `uvicorn.run()` 启动 ASGI 服务器

---

## 三、数据模型

### 3.1 核心表结构 (13 张表)

```
teachers (教师)
├── id, name, email, hashed_password, profile_info, nickname, description, created_at

classes (班级)
├── id, name, created_by_teacher_id, description, created_at

students (学生)
├── id, student_id_number, name, class_id, gender, email, phone, profile_info, nickname, description, created_at

courses (课程模板)
├── id, name, description, credits, created_by_teacher_id, created_at

class_offerings (课堂 = 班级 + 课程 + 教师 + 学期)
├── id, class_id, course_id, teacher_id, semester, schedule_info, created_at
└── UNIQUE(class_id, course_id, semester)

course_files (课程资源文件)
├── id, course_id, file_name, file_hash, file_size, is_public, is_teacher_resource, description, uploaded_by_teacher_id, uploaded_at

chunked_uploads (分块上传跟踪)
├── id, upload_id, course_id, teacher_id, file_name, file_size, chunk_size, total_chunks, received_chunks, status, temp_dir, description, is_public, is_teacher_resource, created_at

assignments (作业)
├── id (UUID), course_id, title, status, requirements_md, rubric_md, grading_mode, created_at

submissions (提交)
├── id, assignment_id, student_pk_id, student_name, status, score, feedback_md, submitted_at
└── UNIQUE(assignment_id, student_pk_id)

submission_files (提交文件)
├── id, submission_id, original_filename, stored_path

chat_logs (课堂聊天记录)
├── id, class_offering_id, user_id, user_name, user_role, message, timestamp

ai_class_configs (课堂 AI 配置)
├── id, class_offering_id (UNIQUE), system_prompt, syllabus, created_at, updated_at

ai_chat_sessions (AI 聊天会话)
├── id, session_uuid (UNIQUE), class_offering_id, user_pk, user_role, title, context_prompt, created_at

ai_chat_messages (AI 聊天消息)
├── id, session_id, role, message, attachments_json, timestamp
```

### 3.2 数据关系

```
Teacher ──1:N──→ Classes (创建)
Teacher ──1:N──→ Courses (创建)
Teacher ──1:N──→ ClassOfferings (授课)

Class ──1:N──→ Students (包含)
Course ──1:N──→ CourseFiles (资源)
Course ──1:N──→ Assignments (作业)

ClassOffering = Class + Course + Teacher (多对多的中间实体)
ClassOffering ──1:N──→ ChatLogs
ClassOffering ──1:1──→ AIClassConfigs

Assignment ──1:N──→ Submissions (通过 student_pk_id 关联学生)
Submission ──1:N──→ SubmissionFiles

AISession ──1:N──→ AIMessages
```

---

## 四、路由系统详解

所有路由在 `classroom_app/routers/` 中定义，通过 `app.py` 汇总注册。

### 4.1 UI 路由 (`ui.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/` | GET | 无 | 根据登录状态重定向 |
| `/student/login` | GET/POST | 无 | 学生登录页面/处理 |
| `/teacher/login` | GET/POST | 无 | 教师登录页面/处理 |
| `/teacher/register` | GET/POST | 无 | 教师注册页面/处理 |
| `/logout` | GET | 登录用户 | 注销 |
| `/dashboard` | GET | 登录用户 | 仪表盘 (显示所有课堂) |
| `/classroom/{class_offering_id}` | GET | 登录用户 | 课堂主界面 |
| `/assignment/{assignment_id}` | GET | 登录用户 | 作业详情页 |
| `/manage/classes` | GET | 教师 | 班级管理页 |
| `/manage/courses` | GET | 教师 | 课程管理页 |
| `/manage/offerings` | GET | 教师 | 课堂开设页 |
| `/manage/ai` | GET | 教师 | AI 配置页 |

### 4.2 文件路由 (`files.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/files/check` | POST | 教师 | 预上传去重检查 |
| `/api/files/upload/init` | POST | 教师 | 初始化分块上传 |
| `/api/files/upload/chunk` | POST | 教师 | 上传单个分块 |
| `/api/files/upload/complete` | POST | 教师 | 完成分块上传 (重组+入库) |
| `/api/files/{file_id}/description` | PUT | 教师 | 更新文件简介 |
| `/api/courses/{course_id}/files/upload` | POST | 教师 | 小文件直传 (兼容旧版) |
| `/api/courses/{course_id}/files/{file_id}` | DELETE | 教师 | 删除课程文件 |
| `/download/course_file/{file_id}` | GET | 登录用户 | 下载课程文件 (流式) |
| `/api/courses/{class_offering_id}/files` | GET | 登录用户 | 获取课堂文件列表 |
| `/submissions/download/{file_id}` | GET | 教师/文件所有者 | 下载提交文件 |
| `/ws/{class_offering_id}` | WS | 登录用户 | WebSocket 聊天 |

### 4.3 作业路由 (`homework.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/courses/{course_id}/assignments` | POST | 教师 | 创建作业 |
| `/api/assignments/{assignment_id}` | PUT/DELETE | 教师 | 更新/删除作业 |
| `/api/assignments/{assignment_id}/submissions` | GET | 教师 | 查看提交列表 |
| `/api/submissions/{submission_id}/grade` | POST | 教师 | 手动评分 |
| `/api/submissions/{submission_id}` | DELETE | 教师 | 退回提交 |
| `/api/assignments/{assignment_id}/export/{class_id}` | GET | 教师 | 导出成绩 (Excel) |
| `/api/assignments/{assignment_id}/submit` | POST | 学生 | 提交作业 |

### 4.4 AI 路由 (`ai.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/ai/generate_assignment` | POST | 教师 | AI 生成作业 |
| `/api/submissions/{id}/regrade` | POST | 教师 | AI 批改 (异步) |
| `/api/internal/grading-complete` | POST | 内部 | AI 批改结果回调 |
| `/api/ai/chat/sessions/{offering_id}` | GET | 登录用户 | 获取 AI 聊天会话列表 |
| `/api/ai/chat/session/new/{offering_id}` | POST | 登录用户 | 创建新 AI 聊天会话 |
| `/api/ai/chat/history/{session_uuid}` | GET | 登录用户 | 获取聊天历史 |
| `/api/ai/chat` | POST | 登录用户 | AI 聊天 (流式响应) |

### 4.5 管理路由 (`manage.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/manage/classes/create` | POST | 教师 | 从 Excel 创建班级+导入学生 |
| `/api/manage/classes/{class_id}` | DELETE | 教师 | 删除班级 (级联) |
| `/api/manage/courses/create` | POST | 教师 | 创建课程 |
| `/api/manage/courses/{course_id}` | DELETE | 教师 | 删除课程 (级联) |
| `/api/manage/class_offerings/create` | POST | 教师 | 创建课堂 (班级+课程关联) |
| `/api/manage/class_offerings/{offering_id}` | DELETE | 教师 | 删除课堂 |
| `/api/manage/ai/configure` | POST | 教师 | 配置课堂 AI |
| `/api/manage/ai/config/{offering_id}` | GET | 教师 | 获取课堂 AI 配置 |

### 4.6 会话路由 (`session.py`)

| 路由 | 方法 | 权限 | 说明 |
|------|------|------|------|
| `/api/session/active` | GET | 教师 | 获取活跃会话列表 |
| `/api/session/invalidate/{user_id}` | POST | 教师 | 强制用户下线 |
| `/api/session/my-info` | GET | 登录用户 | 获取当前会话信息 |

---

## 五、核心业务逻辑详解

### 5.1 认证与安全

**实现位置:** `classroom_app/dependencies.py`

- **密码加密:** `passlib` 的 `pbkdf2_sha256` 方案 (避免 bcrypt 在 Windows/Conda 上的兼容问题)
- **JWT Token:** `python-jose` 库, HS256 算法, 24 小时有效期
- **会话管理:** 内存字典 `active_sessions`, 绑定 IP 地址
- **Token 验证流程:**
  1. 从 Cookie 中取 `access_token`
  2. 解码 JWT → 提取 session_id, IP
  3. 与内存中 `active_sessions` 比对
  4. 验证客户端 IP 一致性
- **权限依赖链:** `get_current_user_optional` → `get_current_user` → `get_current_teacher` / `get_current_student`

### 5.2 文件管理系统

**实现位置:** `classroom_app/routers/files.py`, `classroom_app/services/file_service.py`, `file_handler.py`

**全局文件存储策略:**
- 所有文件存储在 `storage/global_files/` 目录
- 文件名 = SHA256 哈希值 (内容寻址, 自动去重)
- `course_files` 表记录 文件元数据与课程的关联关系
- 同一文件可被多个课程引用, 仅当引用计数归零才删除物理文件

**分块上传协议 (支持大文件):**
1. `POST /api/files/check` — 按文件名+大小去重检查, 如已存在则自动关联
2. `POST /api/files/upload/init` — 创建上传会话, 返回 upload_id, chunk_size, total_chunks
3. `POST /api/files/upload/chunk` — 逐块上传 (每块 5MB), 写入线程池避免阻塞
4. `POST /api/files/upload/complete` — 重组所有分块 → 计算 SHA256 → 存入全局存储 → 写数据库 → 广播通知

**下载优化:**
- 流式传输 (`StreamingResponse`), 8KB 分块
- Windows 并发保护: `asyncio.Semaphore(80)` 限制同时下载流数
- RFC 5987 URL 编码处理中文文件名

### 5.3 作业与批改系统

**作业生命周期:**
```
new (草稿) → published (已发布) → [学生提交] → submitted → graded/grading_failed
```

**AI 批改流程:**
```
教师点击"AI批改" → main.py 发送 POST /api/ai/submit-grading-job
                                    ↓
                        ai_assistant.py 创建异步任务
                                    ↓
                        检测文件类型: 图片? → vision 能力
                                     代码? → thinking 能力
                                    ↓
                        构建评分 prompt → 调用 AI 平台
                                    ↓
                        解析 JSON 响应 (score + feedback_md)
                                    ↓
                        POST 回调 main.py /api/internal/grading-complete
                                    ↓
                        main.py 更新 submissions 表
```

### 5.4 实时聊天系统

**实现位置:** `classroom_app/services/chat_handler.py`, `classroom_app/routers/files.py` (WebSocket)

**多房间架构:** 每个 `class_offering_id` 对应一个聊天室

**MultiRoomConnectionManager:**
- `rooms: Dict[room_id, Dict[client_id, WebSocket]]` — 房间到连接映射
- `user_info: Dict[client_id, dict]` — 用户信息
- `client_to_room: Dict[client_id, room_id]` — 连接到房间映射

**消息持久化:**
- 内存: `chat_histories[room_id]` — deque, 上限 500 条
- 文件: `chat_logs/classroom_{room_id}.log` — JSONL 格式
- 数据库: `chat_logs` 表

**刷新去抖机制:** 断开连接后延迟 5 秒广播"离开", 如果用户快速重连则取消

### 5.5 AI 聊天系统 (V4.2/V4.3)

**实现位置:** `classroom_app/routers/ai.py`, `ai_assistant.py`

**会话模型:**
- 每个用户在每个课堂可有多个 AI 聊天会话 (`ai_chat_sessions`)
- 每个会话缓存用户背景 prompt (`context_prompt`)
- 消息存储在 `ai_chat_messages`

**AI 聊天请求流程:**
1. 用户发送消息 → POST `/api/ai/chat` (multipart: message + session_uuid + files)
2. 验证会话所有权, 加载缓存的用户背景
3. 处理附件 → Base64 编码 (仅图片)
4. 保存用户消息到数据库
5. 构建完整 prompt = 教师系统指令 + 课程大纲(RAG) + 用户背景 + 聊天历史
6. 流式调用 AI 助教 `/api/ai/chat-stream`
7. 流式返回给前端, 同时:
   - 实时解析思考过程 (标记: 【思考过程开始】/【思考过程结束】)
   - 流结束后保存完整 AI 响应
   - 每 5 条用户消息触发一次用户画像更新

**用户画像自动生成:**
- `format_system_prompt()` — 根据用户信息生成初始画像
- `update_user_profile()` — 异步任务, 调用 AI 总结聊天记录更新画像
- 画像存储在 `teachers.description` 或 `students.description`

### 5.6 AI 平台调度

**实现位置:** `ai_assistant.py`

**平台配置:**
| 平台 | SDK | Standard | Thinking | Vision |
|------|-----|----------|----------|--------|
| 火山引擎 (VolcEngine) | volcenginesdkarkruntime | deepseek-v3 | deepseek-r1 | doubao-vision |
| DeepSeek | openai | deepseek-chat | deepseek-reasoner | 不支持 |
| 硅基流动 (SiliconFlow) | openai | DeepSeek-V3 | DeepSeek-V3.2 | Qwen3-VL |

**调度逻辑:**
- 按 `AI_PLATFORM_PRIORITY` 配置的优先级顺序选择
- 根据任务能力需求 (standard/thinking/vision) 选择支持该能力的第一个平台
- 全局并发控制: `asyncio.Semaphore(GLOBAL_AI_CONCURRENCY)` 默认 3
- JSON 输出: 部分平台支持 `response_format: json_object`, 其他依赖提示词

---

## 六、目录结构详解

```
lanshare/
├── main.py                          # 主应用入口 (启动器)
├── ai_assistant.py                  # AI 助教独立服务
├── config.json                      # (旧版) 课程配置文件, V4.0 不再使用
├── .env                             # 环境配置 (端口、API密钥、安全密钥)
├── docker.env                       # Docker 专用环境变量
├── docker-compose.yml               # Docker Compose 编排 (Nginx + App + AI)
├── nginx.conf                       # Nginx 反向代理配置
├── Dockerfile                       # 应用 Docker 镜像
├── DockerfileBase                   # 基础镜像
├── requirements.txt                 # Python 依赖
├── requirements-docker.txt          # Docker 环境 Python 依赖
├── req.txt                          # 额外依赖列表
│
├── classroom_app/                   # 主应用 Python 包
│   ├── __init__.py                  # (隐含)
│   ├── app.py                       # FastAPI 实例, 生命周期, 异常处理, 路由注册
│   ├── config.py                    # 路径/服务器/安全/上传配置 (从 .env 读取)
│   ├── core.py                      # 全局状态: app, templates, ai_client, chat_histories
│   ├── database.py                  # SQLite 连接管理, 13张表初始化
│   ├── dependencies.py              # 认证: JWT, 密码, 会话管理, 权限依赖
│   │
│   ├── routers/                     # API 路由层
│   │   ├── ui.py                    # 页面路由 (登录、仪表盘、课堂、作业、管理)
│   │   ├── files.py                 # 文件上传/下载 + WebSocket 聊天
│   │   ├── homework.py              # 作业 CRUD + 学生提交
│   │   ├── ai.py                    # AI 生成/批改/聊天 (流式)
│   │   ├── manage.py                # 管理中心 API (班级/课程/课堂/AI配置)
│   │   └── session.py               # 会话管理 API
│   │
│   └── services/                    # 业务逻辑服务层
│       ├── chat_handler.py          # 多房间 WebSocket 管理器 + 聊天持久化
│       ├── file_handler.py          # 文件上传/下载/删除
│       ├── file_service.py          # 全局文件存储 (SHA256去重, 流式读取, 清理)
│       └── roster_handler.py        # Excel/CSV 学生名单解析
│
├── templates/                       # Jinja2 HTML 模板
│   ├── student_login_v4.html        # 学生登录页
│   ├── teacher_login_v4.html        # 教师登录页
│   ├── teacher_register_v4.html     # 教师注册页
│   ├── dashboard.html               # 仪表盘 (课堂列表)
│   ├── classroom_main_v4.html       # 课堂主界面 (资源+作业+聊天+AI)
│   ├── assignment_detail_student.html # 学生作业详情
│   ├── assignment_detail_teacher.html # 教师作业详情
│   ├── error.html                   # 404/500 错误页
│   ├── status.html                  # 操作结果页
│   ├── manage/                      # 管理中心模板
│   │   ├── layout.html              # 管理中心布局 (侧边栏)
│   │   ├── classes.html             # 班级管理
│   │   ├── courses.html             # 课程管理
│   │   ├── offerings.html           # 课堂开设
│   │   └── ai.html                  # AI 配置
│   └── ... (旧版模板保留)
│
├── static/                          # 静态资源 (CSS, JS, 字体等)
│
├── data/                            # 数据目录
│   └── classroom.db                 # SQLite 数据库文件
│
├── storage/                         # 全局文件存储
│   ├── global_files/                # 按SHA256哈希存储的课程资源
│   └── chunked_uploads/             # 分块上传临时目录
│
├── homework_submissions/            # 作业提交文件 (按 课程ID/作业ID/学生PK/ 组织)
├── shared_files/                    # (旧版) 共享文件
├── rosters/                         # 学生名单 Excel 文件
├── attendance/                      # 考勤记录
├── chat_logs/                       # 聊天日志文件 (JSONL格式)
├── python_runtime/                  # Python 运行时 (便携版)
│
├── start_main_app.bat               # Windows 启动脚本 (主应用)
└── start_ai_assistant.bat           # Windows 启动脚本 (AI助教)
```

---

## 七、配置系统

### 7.1 环境变量 (`.env`)

```bash
# 主应用
MAIN_HOST=0.0.0.0           # 监听地址
MAIN_PORT=8000               # 监听端口
MAIN_APP_CALLBACK_URL        # AI 批改结果回调地址

# AI 助教
AI_HOST=127.0.0.1            # AI 服务地址
AI_PORT=8001                 # AI 服务端口
AI_WORKER_CONCURRENCY=3      # AI 并发数

# AI 平台配置 (三套, 每套含 API_KEY 和 三种能力模型名)
AI_PLATFORM_PRIORITY=volcengine,deepseek,siliconflow
DEEPSEEK_ENABLED/API_KEY/MODEL_*
SILICONFLOW_ENABLED/API_KEY/MODEL_*
VOLCENGINE_ENABLED/ARK_API_KEY/MODEL_*

# 安全
SECRET_KEY                   # JWT 签名密钥

# 旧配置 (保留兼容)
TEACHER_NAME/TEACHER_PASSWD  # (V4.0 已弃用, 改用数据库教师账户)
```

### 7.2 代码内配置 (`classroom_app/config.py`)

所有路径通过 `pathlib.Path` 相对于项目根目录构建, 关键常量:
- `MAX_UPLOAD_SIZE_MB = 2048` (2GB)
- `UPLOAD_CHUNK_SIZE_BYTES = 5MB`
- `ACCESS_TOKEN_EXPIRE_MINUTES = 1440` (24小时)
- `MAX_HISTORY_IN_MEMORY = 500`
- `FILE_CHUNK_SIZE = 8192` (流式下载分块)

---

## 八、数据库细节

- **引擎:** SQLite, WAL 模式 (支持高并发读写)
- **位置:** `data/classroom.db`
- **连接:** `get_db_connection()` 每次创建新连接, `conn.row_factory = sqlite3.Row`
- **外键:** `PRAGMA foreign_keys = ON`, 所有外键设置 `ON DELETE CASCADE`
- **触发器:** `ai_class_configs` 表有自动更新 `updated_at` 的触发器
- **兼容性:** `init_database()` 使用 `ALTER TABLE ADD COLUMN` + `try/except` 做列级兼容

---

## 九、前端技术栈

- **模板引擎:** Jinja2, 服务器端渲染
- **自定义过滤器:** `datetime_format` — 日期格式化
- **JavaScript:** 原生 Vanilla JS (无框架)
- **实时通信:** WebSocket API (原生)
- **AI 聊天流式:** `fetch` + `ReadableStream` 处理 SSE
- **文件上传:** 分块上传使用 `File.slice()` + `FormData`

---

## 十、依赖清单

| 库 | 用途 |
|----|------|
| fastapi | Web 框架 |
| uvicorn[standard] | ASGI 服务器 |
| jinja2 | HTML 模板引擎 |
| python-multipart | 文件上传解析 |
| python-jose[cryptography] | JWT Token |
| passlib | 密码哈希 (pbkdf2_sha256) |
| python-dotenv | .env 配置加载 |
| aiofiles | 异步文件 I/O |
| httpx | 异步 HTTP 客户端 (调用 AI 服务) |
| pandas + openpyxl + xlrd | Excel/CSV 解析 |
| qrcode | 二维码生成 |
| openai | DeepSeek/SiliconFlow API |
| volcengine-python-sdk[ark] | 火山引擎 API |
| Pillow | 图片处理 (AI 视觉) |

---

## 十一、已知 TODO 和改进空间

### 代码中的 TODO 标记:
1. **文件上传 (`files.py`):** 完成分块上传中有重复的文件重组逻辑 (先异步组装后又调用了 `sync_assemble_file`)
2. **作业删除 (`homework.py`):** 删除作业时未清理磁盘文件夹
3. **提交删除 (`homework.py`):** 退回提交时未清理磁盘文件
4. **WebSocket (`files.py`):** 需验证用户是否有权进入特定 `class_offering_id` 房间
5. **课程删除 (`manage.py`):** 删除课程时未清理磁盘上的相关文件
6. **AI 批改回调 (`ai.py`):** 需通过 WebSocket 向教师推送批改完成通知
7. **课程文件上传 (`manage.py`):** 使用旧的存储路径逻辑, 与新的全局文件存储不一致

### 架构层面的改进建议:
- `active_sessions` 存储在内存中, 重启后所有会话失效
- 数据库连接未使用连接池
- 缺少日志系统 (大量 print 语句)
- 缺少单元测试和集成测试
- 前端缺乏模块化和构建工具
- config.json 为旧版遗留, 可清理
