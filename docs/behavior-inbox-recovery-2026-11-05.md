# 行为回执的隔离与管理员恢复

行为请求只有持久化后才返回 202；`accepted_event_count` 仍表示真正入账的事件数。新请求中不可转换的心跳 `idle_seconds`、非有限 JSON 数、会写入 PostgreSQL 文本列的 NUL/非法 Unicode 会在入场前以 422 拒绝；超过 64KiB 的新批次返回 413。内部服务原有 `json.dumps(default=str)` 合同保留，datetime、Path、Decimal 等未知元数据仍按原规则串化。计分、心跳累计公式没有改变。

已接收回执先按账号、课堂、原批次键及内容 hash 对账，再执行新入场规则。因此旧批次即使超过现在的大小限制，也能查询到原来的失败或成功回执。

## 状态及失败边界

| 状态 | 业务效果 | 后续同账号批次 | 生命周期操作 |
| --- | --- | --- | --- |
| pending | 尚未确认，失败事务整体回滚 | 保持同账号跨课堂 FIFO | 阻止删除、合班 |
| applied | 事件、积分/连续学习、脏标记与回执原子提交 | 可继续 | 既有删除/合班政策 |
| quarantined | 未入账；原 events/hash/scope 保留 | 可继续，不越过其他 pending | 阻止删除、合班，须管理员处理 |
| discarded | 管理员明确放弃入账，不冒充 applied | 可继续 | 可按既有政策删除/合班 |

只把明确的行为校验错误，或连续三次相同 PostgreSQL `22xxx` 数据异常隔离。连接异常、锁/事务冲突、未知提交结果、schema/程序错误继续重试；它们不会自动丢弃。隔离记录与 pending 共同计入 20000 条承载上限，不自动过期。

重复请求命中 quarantined/discarded 会得到明确 409 错误回执，绝不再次返回 202 或重复计分。浏览器保留原批次并暂停，不会换 ID 自动重算。健康快照的 `quarantined_count`、`oldest_quarantined_at` 可见；存在隔离记录时 health 为 degraded，live 仍独立。

## 恢复工具

仅由持有服务器管理权限的人主动运行。工具使用该服务的已配置数据库；测试和容量演练不能据此自动连接业务库。默认只读，列出最多 100 条隔离记录，不打印事件内容：

```sh
docker compose exec -T app python tools/behavior_inbox_recovery.py
docker compose exec -T app python tools/behavior_inbox_recovery.py list --class-offering-id 123 --user-role student --user-pk 456
docker compose exec -T app python tools/behavior_inbox_recovery.py inspect --batch-key BATCH_KEY_FROM_LIST
```

`inspect` 供管理员查看原始内容、错误类别和处理审计。先确认根因已经修好，再使用 list 返回的真实键及 hash 替换以下占位值；所有目标字段均须明确提供：

```sh
docker compose exec -T app python tools/behavior_inbox_recovery.py retry --batch-key BATCH_KEY_FROM_LIST --payload-hash ORIGINAL_HASH_FROM_LIST --class-offering-id 123 --user-role student --user-pk 456 --operator teacher:7 --reason "已修复造成数据异常的底层问题" --acknowledge-write
```

retry 会再次验证原始数据，随后恢复为 pending；不修改事件、不换 ID/hash、不清除历史审计。仍不合法的数据不能强行重试。为防止旧心跳或活跃日期倒退，保守恢复仅限接收后 7 天内、同账号没有排序更晚的 applied 回执、行为状态和补签/连续学习日期也没有更晚事实的记录；检查与 worker 的 APPLY_LOCK 互斥。真正回放时还会再次检查并锁定相关状态，覆盖管理员 retry 后才发生补签的情况。超出这些边界会明确拒绝或重新隔离，原始记录仍保留，必须走专门补偿修复或明确 discard；quarantine 不是无限自动重试承诺。worker 会在原有事务中入账，重复执行不会重复计分。

若确认这批记录不能恢复，并决定明确放弃入账，使用相同完整目标和 hash，将 action 改为 `discard`，同时写明实际处理原因。discard 仅适用于 quarantined，保留原始数据及审计，`applied_at` 仍为空。所有修改都在行锁和 hash/scope 比较后进行；重复或状态过期的管理员动作会被拒绝。

成功或明确 discarded 的时间记为 `resolved_at`，终态回执至少保留其后 10 天；旧 `b1_` 批次的新入场期限仍为 7 天，清理后也不能被当成新请求重复计分。quarantined 不自动清理。显式删除账号/课堂会退休终态记录的显示名、会话和原事件内容，并把自由文本原因清除，保留处理动作、操作者、时间、原 hash 等最小审计。合班回执仍保持原 scope/hash/key，不迁移后重新入账。

启动时会迁移旧的两状态 CHECK 并补齐 `resolved_at`。SQLite 复制/替换表在 SAVEPOINT 内执行，失败可回滚重启，不会提交调用方的外层事务；PostgreSQL 迁移由现有启动锁和事务保护。
