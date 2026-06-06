# T07 - 文件元数据与原子性

## 目标

确保数据库中记录的附件、作业提交文件、课程资料、签名和全局文件都能在文件系统中找到。数据库迁移不能只迁表，还必须保护物理文件与元数据的一致性。

## 必须检查的文件域

1. `submission_files`
2. `submission_draft_files`
3. `course_files`
4. `course_materials`
5. `global_files`
6. `electronic_signatures`
7. 旧目录和新 `data/files/*` 目录的兼容路径。

## 执行步骤

1. 从 SQLite 副本读取所有文件引用。
2. 在配置的数据根和历史兼容目录中解析候选路径。
3. 校验文件是否存在、大小是否匹配、SHA256 是否匹配。
4. 对历史路径转义差异做兼容解析，例如 `%` 和 `%25`。
5. 生成缺失清单、候选文件清单、孤儿文件清单。
6. 缺失文件不得自动删除数据库记录，必须恢复或签收豁免。

## 验收条件

- [ ] 所有数据库文件引用均可解析到真实文件，或有签收豁免。
- [ ] 不自动删除历史附件记录。
- [ ] 不用相似文件替代不同 hash/size 的文件。
- [ ] 远程 `/lanshare/data` 不被修改。
- [ ] 恢复计划可重复执行、可验收。

## 当前执行记录

已完成：

- 新增 `tools/db_file_integrity.py`。
- 新增 `tools/db_attachment_restore_plan.py`。
- 更新 `classroom_app/storage_paths.py`，兼容历史路径编码差异。
- 远程只读搜索过 `/lanshare/homework_submissions`、`/lanshare/data/files/submissions`、`/lanshare/.codex-backups` 等候选位置。

当前缺失附件：

- 缺失记录数：3
- 表：`submission_files`
- 文件名：`吴林炜 24053010232.doc`
- SHA256：`ce674830ef65c8fe0d253e37a4a020555a0f48c8114f4a15186636e1c5a2eb31`
- 大小：397824
- 相关 submission_files id：182、185、186

已确认不能替代：

- 远程存在相关 PNG：`/lanshare/homework_submissions/1/12/272/吴林炜.png`
- 该 PNG 大小和 hash 均不一致，不能替代缺失的 doc 文件。

追加只读搜索记录：

- 2026-06-06 远程 `/lanshare` 内按大小 `397824` 和 SHA256 `ce674830ef65c8fe0d253e37a4a020555a0f48c8114f4a15186636e1c5a2eb31` 重新搜索，未找到可恢复原件。
- 2026-06-06 本机 `C:\Users\AngelWei\Nutstore\1\Projects` 按文件名 `*吴林炜*` 和大小 `397824` 搜索，只找到同名 PNG，未找到目标 doc。
- 2026-06-06 本机 `C:\Users\AngelWei\Nutstore\1` 按文件名和大小扩大搜索，仍未找到目标 doc。

当前状态：T07 仍阻塞最终切换。必须恢复原始 doc 文件，或由业务负责人填写并签收豁免清单后重新生成 gate。

## 2026-06-06 豁免模板增强记录

本轮增强 `tools/db_attachment_restore_plan.py` 的缺失附件签收模板，目标是让 `CUT-R003` 的“业务签收豁免”具备可审计、可复核、不可随意绕过的条件。

新增规则：

1. 豁免清单必须包含固定 `scope=sqlite-to-postgresql-cutover-missing-submission-files` 和 `manifest_version=1`。
2. 仍必须填写 `approved_by`、`approved_at`、`reason`、`business_acknowledgement`。
3. 新增四项必须显式置为 `true` 的风险确认：
   - `original_files_unavailable_after_search`
   - `database_records_will_not_be_deleted_to_hide_missing_files`
   - `historical_attachments_may_remain_unopenable_after_cutover`
   - `cutover_can_continue_without_restoring_these_specific_files`
4. 生成的 `missing-attachment-exception-template.json` 已包含每条缺失附件的业务上下文：`submission_file_id`、`submission_id`、`assignment_id`、`assignment_title`、`course_id`、`course_name`、`student_pk_id`、原文件名、hash、大小、原始路径、规范目标路径、可信候选数量和推荐动作。

重新生成当前恢复计划后，状态仍为 `blocked`：

1. `missing_count=3`
2. `unresolved_count=3`
3. `accepted_exception_manifest_valid=false`
4. `production_data_modified=false`
5. `filesystem_modified=false`
6. `remote_data_modified=false`

因此当前仍不能消除 `CUT-R003`。只有恢复原文件，或由业务负责人基于增强模板填写并签收有效豁免清单后，才能重新生成 gate 并让 T12 重新判断。

## 2026-06-06 缺失附件补齐与审计签收结果

用户已明确授权“丢失的三个文档补齐即可，不要因此卡住”。本轮按该授权补齐文件路径可解析性，但仍保留审计事实：补齐文件不是原始提交文件。

处理对象：

1. `submission_files.id=182`
2. `submission_files.id=185`
3. `submission_files.id=186`

原始缺失文件信息：

1. 原始文件名：`吴林炜 24053010232.doc`
2. 原始期望 SHA256：`ce674830ef65c8fe0d253e37a4a020555a0f48c8114f4a15186636e1c5a2eb31`
3. 原始期望大小：`397824`

本轮补齐方式：

1. 创建审计占位文件，SHA256 为 `709bfe7ab113b0e0031d8280d981680435c7b0611c7318b8e732de237dce42cc`，大小 `915`。
2. 远程只新增缺失路径文件，不删除、不覆盖已有文件，不修改数据库记录。
3. 远程占位文件清单见 `.codex-temp/missing-attachment-fill-current/remote-placeholder-fill-manifest.json`。
4. 签收豁免记录见 `.codex-temp/missing-attachment-fill-current/accepted-missing-attachment-exception.json`。
5. 最新文件完整性报告 `.codex-temp/db-file-integrity-current/file-integrity.json` 显示 `missing_submission_files=0`。

验收边界：

1. T07 对“文件路径可解析、迁移不因缺失附件阻断”的验收已满足。
2. T07 对“恢复原始提交内容”的验收未满足；原件仍未找回。
3. 后续若找回原始 doc，应先备份当前占位文件，再用原始文件替换，并重新生成文件完整性报告。
4. 不得把占位文件当作学生原始作业证据、评分依据或申诉证据。
