# Liquid Glass 业务动作登记

执行真源：`liquid-glass-execution-plan-2026-09.md` §14。以下为目标动作登记，不表示页面已迁移。S0与S3实际接线、测试及保留旧呈现的边界另列在本文末；目标表中的LQ确认/样式不能被当作现已全部实现。

## 14. 业务按钮登记表（首批）

Schema（`docs/lq-action-registry.md` 每行）：`actionId | 路由/组件 | 可见角色 / 资源权限 / 前置业务状态 | 类型(button/submit/link) | 变体·tone | 尺寸 | 位置(桌面/移动) | 文案·aria | 图标 | 确认 | 状态(默认/hover/focus/active/disabled/loading) | API·版本字段 | 重复点击策略 | 成功/失败反馈 | 完成后焦点/导航 | 测试编号`。不新增前端权限真源：引用后端已有能力字段，缺失时复用现判定 + 403/404/409 兜底。所有按钮继承 §10.1；下表只写领域差异（状态列、重复点击策略统一：busy 锁定 + `aria-busy`）。

### 14.1 试卷编辑器

| actionId | 可见/前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈/完成后 | API |
|---|---|---|---|---|---|---|---|---|---|
| `exam.save` | `can_manage`；≥1 题 | button | prominent（唯一） | md | 顶栏右 / 移动底栏右 | 保存试卷 | 评分不完整 `LQ.choose`；有作答改题 预先禁用+说明 | status synced；新建跳编辑页；409/400 `lq-alert` 内容保留 | `POST/PUT /api/exam-papers` |
| `exam.preview` | 任何 | button | glass | sm | 顶栏右 | 全屏预览 | 无 | `lq-modal--full` | — |
| `exam.ai` | `can_manage` | button | glass | sm | 顶栏右 | AI 出题 | 无 | `lq-modal--lg` + `lq-job` | `POST /api/ai/exam/generate` |
| `exam.import` | `can_manage` | menu 项 | — | — | 更多 | 导入 JSON | 覆盖 confirm destructive | 摘要常显；失败保留原试卷 | `POST /api/exam-papers/import-json` |
| `exam.rubric` | 任何 | button | glass | sm | 顶栏右 | 评分标准 | 无 | `lq-modal--lg`；未完整 warning 徽点 | — |
| `exam.cancel` | 任何 | link | ghost | sm | 顶栏左 | 返回试卷库 | dirty confirm | 跳列表 | — |
| `exam.page.add` / `exam.question.add` | `can_manage` | button | soft | sm / md | rail 底 / 主区页尾 | 新增页面 / 新增题目 | 无 | 追加并聚焦 | 本地 |
| `exam.question.delete` | `can_manage` | button --icon | ghost | sm | 题卡 actions | 删除第N题 | confirm destructive + 撤销 toast 8s | — | 本地 |
| `exam.rubric.distribute` / `exam.rubric.apply` | 评分弹层 | button | soft / prominent | sm / md | 弹层头 / foot | 均分总分 / 完成评分 | 无 | 合计常显 / 关弹层 | 本地 |
| `exam.ai.generate` / `exam.ai.cancel` / `exam.ai.apply` | AI 弹层 | button | prominent / destructive-soft / prominent | md | foot / foot / 预览底 | 开始生成 / 中断生成 / 应用到试卷 | 中断 confirm；应用覆盖 confirm | `lq-job`；status dirty | generate / cancel / 本地 |
| `exam.scope` | `can_manage` | 原生 select | `lq-select` | md | rail 设置 | 开放范围 | 缩小范围说明常显 | — | `PATCH …/attributes` |

### 14.2 布置弹窗

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 反馈 | API |
|---|---|---|---|---|---|---|---|
| `assign.new` / `assign.fromLibrary` | 教师 | button | soft | 任务区头 | 新建作业 / 添加考试 | `lq-modal--lg` | `GET /api/exam-papers` |
| `assign.kind` | 弹层 | `lq-segment` | — | 首行 | 作业/期中/期末 | 未选就地错误 | — |
| `assign.schedule` | 弹层 | `lq-segment` + 日期 | — | 第二组 | 长期/截止/倒计时 | 迟交 `lq-switch` 展开 | — |
| `assign.publish` | 弹层 | submit | prominent | foot 右 | 布置到课堂 | 关弹层 + 卡插入 | `POST …/assign` / `POST /api/assignments` |
| `assign.saveDraft` | 弹层 | button | soft | foot 左 | 存为草稿 | chip draft | 同上 `status:new` |

### 14.3 学生作答页

| actionId | 可见/前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `submit.send` | 学生；published & accepting & 队列空闲 & 无 409 | submit | prominent（唯一） | lg | foot 右 / 移动底栏 | 提交作业（补交期"补交作业"） | 未答 `LQ.choose` | 锁定；成功 → 互评 → 刷新；409 → conflict；`p03-submit-assignment` | `POST /submit` |
| `submit.withdraw` | 已提交且窗口内 | button | destructive-soft | sm | 提交卡更多 | 撤回提交 | confirm destructive | 刷新 | `DELETE /withdraw` |
| `submit.redoRequest` | graded & homework & 非缺交 | button | soft | sm | 提交卡 | 申请重做 | 审批表单 | chip | 审批流 |
| `submit.peerEval` | 小组未揭晓 | button | soft | sm | 提交卡 | 完成互评 | 无 | `lq-modal` | — |
| `upload.pick/folder/paste` | accepting | `lq-btn-group` | soft/soft/ghost | sm | 附件块头 | 选择文件/选择文件夹/粘贴 | 无 | `lq-upload` | draft-files |
| `upload.remove` | 每项 | button --icon | ghost | sm | chip 尾 | 移除{文件名} | 无 | 撤销 toast | `DELETE /draft-files/{id}` |
| `result.wrongBook` / `result.exportReview` | graded | link / button | link / soft | sm | 提交卡底 | 错题本复盘 / 导出复习 Word | 无 | — | — |

### 14.4 考试作答页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `exam.submit` | 考生；服务器时间未过截止；无 409 | button | prominent（唯一） | 顶栏右 / Dock 右（隐藏时表单尾等价） | 交卷 | 未答 `LQ.choose` → confirm | 锁定；拦截常显 | `POST /submit` |
| `exam.prev` / `exam.next` | 有前/后页 | `lq-btn-group` | glass | 顶栏中 + 主区底 | 上一页/下一页 | 无 | nav-grid current | 本地 |
| `exam.card` | 任何 | button --icon | glass | 顶栏 | 打开答题卡 | 无 | rail / sheet | 本地 |
| `exam.clearPage` / `exam.clearAll` | 任何 | menu 风险组 | destructive-soft | 整理答卷 | 清空当前页/整张试卷 | confirm destructive | status dirty | draft |
| `exam.draw` | 允许作图 | button | soft | 题卡 | 手写作答 | 无 | 白板挂载 | — |
| `exam.withdraw` | 已交且允许 | button | destructive-soft | 结果区 | 撤回 | confirm | — | `DELETE /withdraw` |

### 14.5 批改页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `grade.save` | 教师；非 returned；0–100 有限小数 | submit | prominent | foot 右 / 移动底栏 | 保存评分 | 无 | 携带两个 revision；409 `lq-conflict`；一次通知；`p03-submission-score-input`、`p03-submit-manual-grade` | `POST …/grade` |
| `grade.aiRegrade` | 教师；status≠grading | button | soft | foot 左 | AI 辅助批改 | confirm | `lq-job`；失败保留旧成绩；`p03-ai-regrade-detail` | `POST …/regrade` |
| `grade.template` | 教师 | button | ghost | 评语域头 | 插入逐题模板 | 无 | 光标插入 | 本地 |
| `grade.prev` / `grade.next` | 教师；列表上下文 | `lq-btn-group` | glass | 顶栏 | 上一份/下一份 | dirty confirm | 导航 | — |
| `files.manage.save` / `files.manage.saveAi` | 教师；可管理附件 | button | soft | 面板尾 | 保存附件 / 保存并提交 AI | 后者 confirm | chip | — |
| `files.delete` | 教师 | button --icon | ghost | 文件行 | 删除附件 | confirm destructive | — | — |

### 14.6 教师作业页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `tasks.status` | 教师 | `lq-segment` | — | 页头右 | 草稿/进行中/已截止 | confirm（含邮件通知） | chip | `PATCH /api/assignments/{id}` |
| `tasks.close` | published | menu 项 | destructive-soft | 更多 | 截止作业 | `lq-modal` + `LQ.choose` 默认分 | — | `/close` |
| `tasks.aiGradeAll` / `tasks.zeroUnsubmitted` / `tasks.offline` | 条件各异 | menu 项 / button | soft / destructive-soft / soft | 批改处理 / 风险组 / 未提交行 | AI 批量批改 / 未提交记 0 / 线下代交 | confirm / confirm destructive(写人数) / 无 | 行 `lq-job` / chip absence-zero / `lq-modal--lg` | 各端点 |
| `tasks.withdrawSelected` / `tasks.withdrawAll` | 有选中 / 有已提交 | button | destructive-soft | 批量条 | 撤回选中 (N) / 全部撤回 | `lq-modal`（新截止） | chip returned | `/submissions/withdraw` |
| `tasks.filter` | 任何 | `lq-chip-row --filter` | — | 列表头 | 全部/已提交/已批改/待重交/未提交(计数) | 无 | 空结果保留清除 | 本地 |
| `tasks.kind` / `tasks.edit` / `tasks.exportGrades` / `tasks.exportFiles` / `tasks.delete` | 教师 | menu 项 | delete 风险组 | 管理作业 | 作业分类 / 编辑作业 / 导出成绩 / 导出附件 / 删除作业 | 原生 dialog 保留 / — / — / — / confirm destructive | export `lq-job` | 各端点 |
| `tasks.wrongSummary` | 有批改 | link | soft | 页头 | 错题归集 | 无 | 跳转 | — |

### 14.7 后续批次范围

课堂主页、3D 课表宿主、最终材料（三签名点、导出）、消息中心、白板工具条、AI composer、简历、管理列表通用（新建/筛选/批量/导出/删除）、系统管理。每批进 `lq-action-registry.md` 并补 `测试编号`。


## S0 动作验收补充

| 动作 | 状态与副作用约束 | 当前测试编号 | 后续门禁 |
|---|---|---|---|
| 保存评分 | 有限小数、0分；review/assignment两个版本；busy锁定；失败保留输入 | tests/test_mp_grade_safety.py（SQLite+PG）；components/submission-grading.spec.ts；specs/manual-grade-revisions.spec.ts | S3补齐完整六链，S5标准状态组件 |

## S3 已落地动作与测试映射（验收进行中）

| 动作 | 当前实现与版本约束 | 实际测试文件 / 场景 | 尚未替代的后续工作 |
|---|---|---|---|
| `exam.save` | 原保存入口增加disabled/aria-busy；PUT携带SSR `expected_revision`，成功接收新revision，409常显且保留本地输入；新建跳转前保持busy | `exam-authoring.spec.ts`：503保值→实际保存读回、双窗口409、已有作答/越权；`test_exam_paper_revision.py`；`tests/lq/exam-editor-revision.test.mjs` | S5完整编辑器、评分选择、AI/导入和标准LQ布局 |
| `submit.send` / `upload.pick` | 保留原上传队列和服务端提交版本；实际成功后清草稿且刷新不再反写 | `assignment-student-draft.spec.ts`：跨页面服务器文字与附件恢复→真实提交；原 `assignment-submission.spec.ts` 继续保留 | S5表单/上传呈现与全部题型 |
| `exam.submit` | 生产submit模块的load/save/submit单owner；固定打开版本、冲突停止写、失败保留File；原生业务确认保持原合同 | `exam-take.spec.ts`：空答/确认、503→重试、旧页跨退回轮次、截止竞态；`exam_draft_version.test.cjs`直接import23项 | S5全页拆模块、LQ确认、答题卡/Dock布局 |
| `grade.save` / `tasks.withdrawSelected` | 沿用S0双版本CAS；并发只允许一个有效新revision和一次通知；退回新轮次隔离旧作答/旧批改页 | `grading-concurrency.spec.ts`；`grading-return-resubmit.spec.ts`；原 `manual-grade-revisions.spec.ts` | S5评分/作业页呈现及其他动作 |
| `tasks.wrongSummary` / 重整归集 | 实际owner与课程scope；503不清缓存统计；实际人工修订后投影更新 | `wrong-summary.spec.ts`：1错1难→503保留→真实修订100分→0错0难、学生/他师拒绝、unsupported/404 | S5错题归集样式、真实AI任务poll生命周期 |
| 八个管理列表新建/编辑/删除/筛选 | 只替换壳和page-head；保留原按钮、name/form、文件/iframe及controller。移动更多释放自身面板后由原业务入口处理 | `manage-pilot.spec.ts`；`teacher-app-shell.spec.ts`；`test_manage_lq_pilot_templates.py`；`layout-stability.spec.ts` | 页内旧弹层、原生确认与控件逐批迁移；未执行项不能因已登记而算通过 |
| 学生成绩页筛选/更多/外观/账号 | 原权限投影；0、null、退回、小组隐藏、冻结公布均分开；主题刷新只更新图表外观；原位更多只有一个owner | `report-card-pilot.spec.ts`：本人/角色、六配色亮暗、偏好/security/feedback入口、20次生命周期、无JS | 其他成长页面及全站topbar/Dock迁移 |
| 试点回退 | 服务端关闭 `LANSHARE_LQ_PILOT` 恢复原HTML/CSS/JS分支，query不启用 | `pilot-rollback.spec.ts`，独立 `lq-s3-rollback.playwright.config.ts` | 真实发布/回滚和设备验收仍独立 |

此表映射测试位置，不代替 `lq-acceptance.md` 中的执行结果。管理故障流仅对指定method/endpoint注入有界失败；权限、版本、提交、评分与读回由真实合成应用执行。各spec保留原 `p03-*`，不请求真实账号/教务/模型服务。
| 遇到409 | 不覆盖新版本、不静默重试；保留本地输入并提示核对 | submission-grading.spec.ts conflict/reload/withdrawn/storage cases | 所有会刷新/覆盖的操作必须受同一冲突状态约束 |
| 重新核对 | 当前教师/提交隔离的sessionStorage；可靠存储后才刷新；新答卷与旧草稿同时可核对 | submission-grading.spec.ts draft recovery cases | S5统一lq反馈与无障碍 |
| 旧开课向导入口 | 教师教学域鉴权；301→课堂中心；保留查询串；无业务写入 | test_manage_nav_service.py；teacher-app-shell.spec.ts | S7检查embedded_mode消费者后退役 |
| 组件预览 | 默认不可达；启用后仅教师；无写入行为 | test_lq_foundation.py LqPreviewTests | S1/S2填入真实组件与状态 |

其余状态、权限、请求字段、幂等/版本、失败恢复及按钮三入口语义按执行计划逐项继承；迁移时必须新增测试编号，不能把“已登记”作为“已验收”。

## S4 第一包动作映射

| 动作 | 合同 | 门禁与范围 |
|---|---|---|
| 共享顶栏更多/导航/反馈 | 借用原节点、原form和controller；dialog关闭后原处理器接管；不重复click/业务请求 | `lq-s4-shell.spec.ts`、`lq-s4-shell-visual.spec.ts`、`test_lq_navbar_shell.py`；正文业务仍属各包 |
| 学生Dock跳转 | SSR真实href、当前section激活、单一contentRoot补白；无JS仍可用 | `lq-s4-shell.spec.ts`；模拟软键盘不等于实体设备 |
| 已有密码登录与重试 | 原生POST/JS共用认证编排；失败保留账号和safe next、不回填SSR密码；成功真实会话；注册403 | `test_auth_form_fallback.py`、`lq-s4-auth.spec.ts`；首次设密及找回完整无JS仍待D后包 |
| 登录材质降级 | 图片失败/超时或能力/偏好要求厚/off时同步切材质；提交入口与位置保持可达 | `lq-centered.spec.ts`、`probe_login_page_backgrounds.cjs`；实际认证与像素测试分别记录 |
| Profile原分区 | 师生薄壳、共享原正文；原控件/脚本唯一、整页GET保留 | `test_profile_template_contract.py`及32份语义渲染；外观分区/消息异步修复未由C0代验 |

关闭页族flag的回退、资源与性能票使用独立spec及报告，只有执行后才补结论；此表不是完成勾选。
