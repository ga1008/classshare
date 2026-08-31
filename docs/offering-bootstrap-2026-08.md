# 教务同步后「一键开设课堂」方案（offering bootstrap）

> 2026-08-31 起草。状态：**方案评审中，未动工**。
> 前置依赖：合班课堂 P0-P4.0（docs/combined-class-offering-plan-2026-08.md）已全部上线。

---

## 1. 现状深度分析

### 1.1 同步完成后教师手里有什么

| 要素 | 状态 | 来源 |
|---|---|---|
| 课程（含课程号、学时、系别） | ✅ 自动 | 课程同步，按课程号去重 |
| 课次模板（课堂设置，64 学时→32 次） | ✅ 自动 | 占位课次自动生成 |
| 班级 + 学生名单 | ✅ 自动 | 名单同步（JXBZC 拆班建班） |
| 逐次真实排课（日期/节次/地点） | ✅ 自动 | `teacher_academic_course_session_occurrences` |
| 教学班↔行政班组成（含合班信号） | ✅ 自动 | 组成拆分 + 课程 metadata |
| 学期归属 | ✅ 自动 | occurrences.semester_id |
| **课堂（offering）** | ❌ 手动 | —— |
| 教材 | ❌ 手动 | 教务接口不提供 |
| AI 助教配置 | ❌ 手动（可 AI 生成，但依赖教材） | —— |

**结论**：开设课堂所需 6 项输入里 5 项已是确定性已知，唯一真正的外部输入是教材（且可选）。
课堂不自动建的现状让教师在同步后仍要"每个教学班跑一遍表单"。

### 1.2 现有三条开课路径与摩擦点

1. **开设课堂表单**（/manage/teaching/offerings）：学期/班级/课程/教材/排课来源 5 项选择
   + 合班勾选 + 预览 + 保存。对 N 个教学班要重复 N 遍；同名课程还需自己对准课程号
   （已有错配拦截兜底，但拦截≠引导）。
2. **开课向导**（逐步开设）：面向首次使用的引导流，本质仍是单课堂逐个建。
3. **教务同步结果面板**：只读报告 +「接下来」文字清单，无行动按钮——教师看完提示
   还要自己导航去开课，动线断裂。

### 1.3 关键约束与可复用构件（代码实测）

- **教材必填是唯一硬堵点**：`_prepare_offering_payload`
  `not textbook_id → CoursePlanningError("请完整选择学期、班级、课程和教材")`；
  开课向导 complete 同样强制。但 `class_offerings.textbook_id` 列本身可空，
  留空的实际影响只有两个：AI 助教配置生成要求教材（api_ai_generate_config 400）、
  课堂页教材栏空——时间轴、名单、作业、互动全不受影响。
- **教学班选择可反向精确化**：现有 `select_academic_teaching_class_for_offering`
  是"由班级猜教学班"（评分匹配）；一键创建从教学班出发，直接传
  `preferred_teaching_class_id` 即可 100% 确定，不存在猜错空间。
- **全部重活都有现成函数**：时间轴 `build_academic_offering_session_plan` +
  `replace_offering_sessions`；合班 `replace_offering_class_links`（含同课程防重）；
  错配拦截 `_validate_academic_class_course_match`；教学班清单
  `summarize_academic_teaching_classes`；组成拆分 `_admin_class_names_from_composition`。
- **幂等天然可得**：候选=「该课程该教学班的组成班级尚未被同课程任何课堂 link 覆盖」，
  已建过的组合自动从候选消失，重复点击零副作用。

## 2. 设计目标（易用性优先）

1. **同步完成 → 一次点击 + 一次确认 → 全部课堂就绪**：把"接下来"从待办清单变成行动按钮。
2. **缺教材不挡路**：教材降为可选（逐行可选可空），留空建堂、后置补选；
   确认层明说留空的唯一代价（AI 助教配置暂不可生成）。
3. **预览确认制，不盲建**：先展示将要创建的课堂清单（课程号/教学班/班级组/人数/排课次数），
   默认全选，教师可取消个别项——与合并向导同一交互语言。
4. **逐项隔离**：单个课堂创建失败只降级为该行提示，不拖垮整批（结课原则）。
5. **建后动线闭环**：结果页给出每个课堂的「进入课堂」「配置 AI 助教」「补选教材」深链。

## 3. 详细设计

### 3.1 候选计算 `build_offering_bootstrap_candidates(conn, teacher_id, semester_id)`

以**教学班**为原子（不是课程、不是班级——教学班正是教务的上课单位）：

```
对教师该学期的每门教务课程（有 occurrences）：
  对每个教学班（summarize_academic_teaching_classes）：
    组成班级名 → classes 按名解析本地班级 id（roster 同步建的班级同名可解析）
    排除：组成中任一班级已被「同课程+同学期」的课堂 link 覆盖 → 该教学班已开课，跳过
    标记不可建原因：组成班级在本地缺失（罕见，提示先跑名单同步）
    产出候选：{course_id, course_name, course_code, teaching_class_id/name,
              class_ids, primary_class_id(组成首班), class_names, student_count,
              session_count, is_combined, suggested_textbook_id}
```

同名不同号课程天然安全：候选按课程**自身**的排课组成生成，不存在跨课程号错配；
`_validate_academic_class_course_match` 在执行层再兜一道底。

### 3.2 执行 `bulk_create_offerings_from_academic(conn, teacher_id, semester_id, selections)`

`selections = [{course_id, teaching_class_id, class_ids, primary_class_id, textbook_id?}]`

每项（try/except 隔离，失败记入该行结果）：
1. `_prepare_offering_payload` 复用（新增 `allow_missing_textbook=True` 参数，
   默认 False——既有路径行为零变化）：传
   `class_id/class_ids/course_id/semester_id/textbook_id(可空)/
   academic_teaching_class_id(精确)/schedule_source=academic_sync`，
   拿到时间轴 plan + 全套校验（错配拦截、班级归属、课次模板）。
2. 幂等复查：组合已被覆盖 → 标记 skipped。
3. INSERT `class_offerings`（onboarding 同款列）→
   `replace_offering_class_links`（合班 links，source='academic_sync'）→
   `replace_offering_sessions(plan.sessions)`。
4. 行结果：{offering_id, status: created/skipped/failed, message, 深链}。

返回汇总：created/skipped/failed 计数 + 每行详情。整体一次 commit——行级失败
不 raise，记录后继续（与合并向导的 all-or-nothing 不同：建课堂是纯增量操作，
部分成功优于全部回滚）。

### 3.3 API（挂 classes_courses_offerings.py）

- `GET  /api/manage/class_offerings/bootstrap/candidates?semester_id=`
- `POST /api/manage/class_offerings/bootstrap/execute` `{semester_id, selections: [...]}`

### 3.4 入口与交互（两处，一套组件语言）

**入口 A｜同步结果面板（主动线）**：`academic_sync_dialog.js` renderResult 在
「接下来」清单上方插入行动区——同步成功后立即 fetch candidates，非空时渲染：

> ✦ 检测到 **N 个教学班**可直接开设课堂（课程、班级、课次、排课均已就绪）
> 【一键开设课堂】

点击展开确认层（对话框内内联展开，同合并向导模式）：
- 每行：勾选框（默认全选）· 课程名 + 课程号 pill · 教学班组成（合班徽标）·
  人数 · 排课次数 · **教材下拉（默认建议值或"暂不选择"）**
- 底部说明：「教材可稍后在课堂里补选；未选教材的课堂暂不能生成 AI 助教配置。」
- 【创建所选 N 个课堂】→ 行内逐项结果（✓ 已创建/深链、⚠ 失败原因）

**入口 B｜开设课堂页检测卡（补救动线）**：offerings 页顶部（合并卡旁）同款检测卡，
复用同一 candidates API + 同一确认层组件——错过同步时机也能随时一键补开。

**建后引导**：结果行提供「进入课堂」；有教材的行加「AI 生成助教配置」深链；
课堂卡对 `textbook_id` 为空的教务课堂显示「待绑教材」小徽标（顺手补的呈现项）。

### 3.5 教材策略（易用性权衡记录）

- **逐行下拉而非全局单选**：批量创建往往跨多门课程，教材按课程各不相同，
  全局单选反而制造错误。
- **默认建议 > 默认留空 > 绝不猜测**：同课程已有课堂绑过的教材自动预填为建议值
  （可改可清空）；无历史则留空——教务无教材数据，任何猜测都可能错绑。
- `allow_missing_textbook` 仅对 bootstrap 路径开放，普通表单仍必填——避免常规
  路径产生大量无教材课堂。

### 3.6 测试计划

- 候选：单班/合班组成解析、已开课排除（含部分覆盖）、班级缺失标记、
  同名两课程各出各的候选、幂等（建后候选消失）；
- 执行：课堂 + links + 时间轴落库断言（session_count=排课数）、无教材可建、
  有教材正常绑、单项注入失败不影响其余、防重复查 skipped；
- 路由快照基线更新；真实数据浏览器演练（同步结果面板 → 一键创建 → 课堂页时间轴/名单）。

### 3.7 规模与节奏

一次交付（S+ 规模：服务 ~250 行 + 2 API + 2 处前端入口 + 单测），不分阶段；
上线后生产下一次教务同步自然获得主动线验证（顺带完成此前待办的
linked_classes 合班建议项人工验证）。

## 4. 评审待决

1. **AI 助教配置是否纳入一键流**：建议不纳入——AI 生成每课堂一次思考型调用，
   批量触发成本高且教材缺失时必失败；保留建后深链手动触发。
2. **教材建议预填**：同课程已有课堂的教材自动预填（可改可清空）——建议做，成本极低。
3. **开课向导是否同加"从教务导入"分支**：建议不加——两个入口已覆盖主/补动线，
   向导保持面向手工建课的纯引导定位。
