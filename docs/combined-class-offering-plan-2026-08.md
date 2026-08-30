# 合班课堂（一课堂多行政班）改进方案

> 2026-08-30 起草，同日用户批准动工。
> 进度：**P0 上线**（8c581f84：link 表 + 回填 + membership 服务，生产 10/10 回填）。
> **P1 上线**（d752dca8：学生发现/名单/鉴权/统计/导出等核心链路切 membership 并集；
> 课堂表单多选班级 + 防重 + 合班徽标 + 智慧课表深链多班自动勾选；
> ensure 守卫改按 engine+DB 路径记忆并支持 force/engine 供手搓 schema 测试夹具）。
> **P2 上线**（27dab784：长尾 41 处旧 join 全量收敛；守卫单测
> test_no_legacy_offering_class_joins 禁止裸等值复发；课程 metadata 增
> combined_admin_classes；同步差异预览新增 linked_classes 字段差异（合班缺班检测），
> 教师确认后 apply 自动把缺失行政班挂入课堂（source=academic_sync，失败仅降级提示）；
> 开课表单选择合班课程自动匹配主班级并勾选其余班级）。
> **P3 上线**（ed35e24c：结课/重修/修为排名/消息中心/错题等参数化残余收敛并加读路径
> 自愈回退；课堂学生列表合班分节；说明浮窗接入勾选组与徽标；build_classroom_ai_context
> 注入合班组成（全部 AI 消费方共享）；附件 zip 按 班级/学生 分目录、成绩 xlsx 增班级列。
> 范围调整：官方平时成绩表/考核登分表保持课堂全量口径——与教务登分表（按教学班）天然一致，
> 不做拆分变体，避免触碰 export_payload 回放链）。
> 剩余：P4 历史双开课堂合并向导（高危，单独评审）。
> 背景案例：新同步的「Python程序设计」由多个行政班合班上课；班级本身分开管理，其他课程各自独立，仅这门课合上。

---

## 0. 业务模型梳理（现状 → 目标）

### 0.1 现状实体关系

```
courses ──┐
classes ──┼── class_offerings（课堂）── class_offering_sessions（课次）
teachers ─┘        │
                   └─ class_id 单值 NOT NULL + UNIQUE(class_id, course_id, semester)

students.class_id ──单一归属──> classes（行政班；class_kind: administrative/custom）
```

- **课堂（class_offering）= 课程 × 单个班级 × 学期**，这是全平台的组织核心
  （schema_foundation.py:1319，UNIQUE 约束 1367-1372）。
- 「课堂的学生」在 22+ 个文件里通过 `students.class_id = offering.class_id` 解析
  （作业/考试名单、提交统计、签到互动、成绩材料、侧写、错题、消息、日历、搜索、小程序端……）。
- 学生端发现课堂：`WHERE o.class_id = (SELECT class_id FROM students WHERE id=?)`
  （dashboard_service.py:2963/3225、learning_progress_service.py:1849 等）。

### 0.2 教务系统的真实语义

- 教务的**上课单位是教学班（jxb_id / JXBMC）**，不是行政班。
- 一个教学班可以由多个行政班合成，字段 **JXBZC（教学班组成 class_composition）**
  形如「网工2401,网工2402」。
- 同步侧已有权威数据：
  - `_class_names_from_composition()` 已把组成拆成多个行政班并逐一建班
    （academic_roster_sync_service.py:1199-1240）；
  - **`teacher_academic_roster_memberships`** 表已按（教学班 × 学生 × 行政班）
    存了权威名单（schema_foundation.py:822）——合班课堂的学生集合现成可查。

### 0.3 痛点

| 场景 | 现状后果 |
|---|---|
| 合班课程开课 | 教师被迫为每个行政班各建一个课堂；或只建一个课堂，其余班学生看不到 |
| 作业/考试/投票/互动 | 同一份内容要按班发 N 次，批改与统计割裂 |
| 排课对齐 | N 个课堂都匹配到同一个教务教学班，课次重复维护 |
| 成绩链路 | 教务登分表按教学班（合班全量）出，但平台成绩按单班课堂割裂，两边口径错位 |

### 0.4 目标模型

```
class_offerings（课堂 ↔ 教学班）
    │ class_id（保留：主班级，兼容 + 展示兜底 + 唯一约束锚点）
    └─ class_offering_class_links（新）: offering ↔ 班级 一对多
students.class_id 不变（学生仍单一行政班归属）
```

- 课堂对应**教学班**；单班课=1条link（现状不变），合班课=N条link。
- 「课堂的学生」= 所有 link 班级的学生并集。
- 班级依旧独立管理；同一班级在不同课程可以属于不同课堂组合。

---

## 1. 数据库改动

### 1.1 新表 `class_offering_class_links`

按既有惯例做 **engine-aware runtime schema**（仿 polls / group_schemes：
`ensure_*_schema(conn)` 双引擎建表，不进 `REQUIRED_POSTGRES_TABLES`）：

```sql
CREATE TABLE IF NOT EXISTS class_offering_class_links (
    id           INTEGER/SERIAL PRIMARY KEY,
    offering_id  INTEGER NOT NULL REFERENCES class_offerings(id) ON DELETE CASCADE,
    class_id     INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    teacher_id   INTEGER NOT NULL,
    is_primary   INTEGER NOT NULL DEFAULT 0,          -- 恰好 1 条 =1，与 offerings.class_id 一致
    source       TEXT NOT NULL DEFAULT 'manual',      -- manual / academic_sync / backfill
    academic_admin_class_name TEXT NOT NULL DEFAULT '', -- 教务侧行政班原名（追溯）
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (offering_id, class_id)
);
CREATE INDEX idx_cocl_class    ON class_offering_class_links(class_id);
CREATE INDEX idx_cocl_offering ON class_offering_class_links(offering_id);
```

### 1.2 `class_offerings` 增列（不破坏既有列）

- `is_combined INTEGER NOT NULL DEFAULT 0` —— 冗余标记，列表/徽标零 join。
- `combined_class_names TEXT NOT NULL DEFAULT ''` —— 展示缓存（「网工2401·网工2402」），
  link 变更时由服务层重算。
- **`class_id` 保留原语义 = 主班级**。`UNIQUE(class_id, course_id, semester)` 不动
  （主班级维度仍唯一）。

### 1.3 防重约束（应用层）

数据库层无法表达「某班级不能同时出现在同课程两个课堂」，在创建/编辑/同步三个入口做校验：
保存 link 前检查 `该 class_id 是否已被同 course_id + semester 的其他课堂 link 覆盖`，
冲突时报错并给出跳转（不静默合并）。

---

## 2. 现有数据兼容

1. **幂等回填**：`ensure_offering_class_links_schema()` 内做
   `INSERT ... SELECT id, class_id, teacher_id, 1, 'backfill' FROM class_offerings
   WHERE NOT EXISTS (同 offering 主 link)`。跑多少次结果一致。
2. **回滚安全**：旧代码只读 `class_id`，新表纯增量；任一阶段回滚到旧版本，单班课堂行为不变
   （合班课堂退化为「只有主班级可见」，不丢数据）。
3. **不变式维护**：服务层保证「每个 offering 恰有 1 条 is_primary=1 的 link，且其
   class_id == offerings.class_id」；变更主班级时两处同步更新（同事务）。
4. 迁移导出器（tools/db，见 local-dev-postgres 备忘）补充新表；上线前在真 PG 验证。
5. 单测 `_SCHEMA_READY` 重置坑（classroom_retake 备忘）照旧处理。

---

## 3. 后端逻辑改进

### 3.1 单一真源服务 `offering_membership_service.py`（新）

所有「课堂↔学生/班级」解析收敛到一个模块：

```python
offering_class_ids(conn, offering_id) -> list[int]
offering_student_where(alias_s="s", alias_o="o") -> str
    # "s.class_id IN (SELECT l.class_id FROM class_offering_class_links l WHERE l.offering_id = o.id)"
student_offering_where(alias_o="o") -> str          # 学生端反向发现
load_offering_students(conn, offering_id, ...)      # 含 enrollment_status 过滤，按班分组可选
offering_display_class_name(conn, offering)         # 单班=班名；合班="A·B 合班"
replace_offering_class_links(conn, offering_id, class_ids, primary_class_id, source)
    # 事务内：写 links + 更新 offerings.class_id/is_combined/combined_class_names + 防重校验
```

### 3.2 存量查询收敛（分两批）

以 grep 清单驱动逐处替换 `s.class_id = o.class_id` / `o.class_id = (SELECT class_id FROM students…)`：

- **P1 核心链路（学生可感知/教学闭环）**：
  - 学生端课堂发现：dashboard_service、learning_progress_service、personalized_learning_path_service、ui_parts/common、learning.py；
  - 名单与鉴权：homework_parts（assignments/submissions/exam_papers/exports）、files.py、resource_access_service、ui_parts/classroom；
  - 课堂统计：`_attach_teacher_assignment_card_metrics` 的 total_students、materials_parts/common。
- **P2 长尾**：collaboration、poll、message_center、calendar_feed、global_search、
  chat_platform_query、wrong_question_summary、classroom_retake、dashboard 教师侧、
  agent_bridge_service（只读 SQL 模板）、mp/（小程序 teacher/tasks）、
  academic_course_exam_sync / exam_roster（考试名单按课堂取全量）。
- **防回归守卫**：加一个源码扫描单测，断言 classroom_app 内不再新增
  `class_id = o.class_id` 裸 join 模式（白名单豁免迁移/回填代码）。

### 3.3 排课与课堂匹配

- `select_academic_teaching_class_for_offering`：匹配文本从单班名改为
  「全部 link 班级名 + 组成」拼接评分——合班课堂与教学班的匹配从"部分包含"变成"精确对齐"。
- `_sync_existing_offering_academic_sessions` 的 class_row 取值同步调整。
- 结课/重修/豁免/修为值等 offering 维度逻辑天然兼容，仅名单来源变化。

---

## 4. 教务同步集成（自动识别合班）

### 4.1 同步预览（reconciliation）

- 新的检测规则：教学班组成含 **N>1 个行政班**时，检查该课程本地课堂状态：
  1. **已有 1 个课堂**（任一班）→ 生成 `offering_add_classes` 建议项：
     「Python程序设计 检测到合班（网工2401、网工2402），建议将缺失班级挂入课堂 #X」，
     教师确认后写 links；
  2. **尚无课堂** → 不建课堂（保持现状：同步不自动开课），但把合班信息写进
     课程 metadata（`academic_metadata_json.combined_admin_classes`），供开课向导自动填写；
  3. **已有 ≥2 个同课程单班课堂**（历史双开）→ 只提示不自动合并（见 P4 合并工具），
     避免静默迁移作业/提交。
- 学生名单本身不变：`teacher_academic_roster_memberships` 已是全量真源。

### 4.2 开课向导 / 课堂表单自动填写

- 选择课程后，读课程 metadata 的教学班组成：
  - 自动勾选全部对应行政班（首个为主班级），显示提示条
    「教务教学班组成：网工2401、网工2402（已自动选中，可调整）」；
  - `academic_teaching_class_id/name` 沿用现有字段绑定教学班。

---

## 5. 前端改进

### 5.1 表单（manage_offerings.js + 模板、teacher_onboarding.js）

- `offeringClassSelect` 单选 → **多选班级组件**（复用材料中心分类多选的 tw- 风格；
  勾选列表 + 已选 chips，首个/可指定「主班级」标记）。
- payload：`class_id`（主班级，兼容旧接口）+ `class_ids[]`（全量）。
- 校验文案更新：「至少选择一个班级；合班时其余班级不可再为该课程单独开课堂」。
- 冲突提示：后端防重校验返回的冲突课堂给出深链。

### 5.2 展示与交互设计

- 课堂卡片/列表/详情眉题：合班徽标 `合班 · 2班`，班级名用 `combined_class_names`；
  单班课堂完全不变。
- 课堂学生列表：**按行政班分节展示**（合班时每班一个分组标题 + 人数）。
- 编辑课堂移除某班级：确认弹窗说明「该班学生的历史提交与成绩记录保留，仅不再看到该课堂」。
- 学生端：课堂卡片可选加「合班」小标；发现逻辑全在后端，无其他改动。
- 说明浮窗（ui-explanation-popover 惯例）：给多选班级控件与合班徽标接入说明。

### 5.3 前端数据关联

- offering 序列化统一带 `class_ids`、`is_combined`、`combined_class_names`、
  `class_links[{class_id,name,is_primary,student_count}]`；
- 依赖 offering.class_id 的前端过滤（如按班筛课堂）改为按 class_ids 包含判断。

---

## 6. 数据流转与下游口径

| 域 | 变化 |
|---|---|
| 作业/考试/投票/互动 | 面向 offering 不变；名单自动扩为并集；提交表按 student_pk 记录，无历史迁移 |
| 随机分组 | offering 作用域天然支持跨行政班分组（合班的核心收益之一） |
| 成绩材料链 | 平时成绩表/考核登分表默认按课堂（=教学班全量）出，与教务登分口径**对齐**；新增「按行政班拆分导出」选项（export_payload 回放真源同步扩展，见 grade-materials-chain 三处同步坑） |
| 教师评学表/考核计划/教案 | offering/course 维度，仅班级展示名变化 |
| 统计口径 | 明确区分「班级学生数」与「课堂学生数=∑link班级 active 学生」；监控/首页计数复核 |
| 小程序端 | mp_sessions 独立会话不变；教师批改任务名单走同一 membership 服务 |

---

## 7. AI 功能

1. **AI 开课配置**（api_ai_generate_config）：提示词补充合班上下文
   （班级列表、各班人数、合班总人数），生成的分组/互动建议按总人数标定。
2. **AI 归集类功能**（评学表、错题归集、侧写、个性化路径）：名单扩大后样本变多，
   沿用现有分批与 token 预算机制，无提示词改动；验证 60-95 分带等约束不受人数影响。
3. **合班识别不用 AI**：教学班组成是确定性字段，识别一律走确定性解析；
   AI 仅用于 reconciliation 建议项的人话解释（可选）。
4. **agent-bridge / 提示词注入**：更新平台知识中「课堂」的定义（可多班级）与
   只读 SQL 模板（agent_bridge_service.py:438 那类模板改走 membership 子查询）。

---

## 8. 分阶段实施与测试

| 阶段 | 内容 | 可独立部署/回滚 |
|---|---|---|
| **P0 地基** | 新表 + 回填 + membership 服务 + 不变式；行为零变化 | ✅ |
| **P1 核心** | 学生发现/名单/鉴权/统计切 membership；表单多选 + 保存链路 + 防重；卡片徽标 | ✅ |
| **P2 收敛** | 长尾查询全量替换 + 守卫单测；同步 `offering_add_classes` 建议 + 开课自动填写 | ✅ |
| **P3 增强** | 按行政班拆分导出；学生列表分节；说明浮窗；AI 上下文 | ✅ |
| **P4 治理** | 历史双开课堂「合并向导」（迁移作业/提交/成绩到保留课堂，高危、显式确认、备份先行） | 单独评审 |

**测试要点**：membership 服务双引擎单测；学生端发现（合班/单班/移除班级）；
作业名单并集与鉴权；同步预览合班建议 + apply 幂等；回填幂等；
路由快照（p02_route_snapshot）；`app.py` 403→401 重写坑的鉴权断言；真 PG 全链路验证后按 deploy-workflow 上线。

**主要风险**：① 22+ 文件替换面广——靠 P0 服务收敛 + 守卫测试兜底；
② IN 子查询性能——两个索引 + 200 并发规模下可忽略，必要时 EXISTS 化；
③ 历史双开课堂数据合并复杂——放 P4 单独做，前期只提示；
④ 前端多选组件涉及开课向导与课堂表单两处，注意 cache-busting（frontend-change-playbook）。
