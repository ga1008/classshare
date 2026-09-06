# 职业与简历 AI 增强灰度

这组配置只控制新 AI 增强任务的准入，不新增功能开关表或管理平台。默认 `all` 保持原有行为；名单外学生仍能做基础职业探索、完成测评、编辑资料、保存及发布手工简历、查看历史和导出已生成版本。手工发布使用 `lane=render`，不受 AI 名单限制。

```dotenv
CAREER_AI_ROLLOUT_MODE=allowlist
CAREER_AI_ROLLOUT_STUDENT_IDS=101,102
CAREER_AI_ROLLOUT_MAJORS='[{"school_code":"example-school","major_key":"英语"}]'
```

- `CAREER_AI_ROLLOUT_MODE`：`all` 或 `allowlist`，默认 `all`。
- `CAREER_AI_ROLLOUT_STUDENT_IDS`：数据库学生主键的逗号分隔列表，最多 500 项，正整数；不是学号。
- `CAREER_AI_ROLLOUT_MAJORS`：最多 100 项 JSON 数组，每项必须且只能有 `school_code` 和 `major_key`。专业键使用该学校现有别名映射后的 canonical major，不按文本包含关系猜测。相同专业名在另一学校不会自动开放。
- 每项原始配置最多 32768 UTF-8 字节。`allowlist` 下重复 JSON 键、未知字段、非法 ID、超限或非法 JSON 均关闭 AI 准入；错误模式同样关闭准入，不回退为 `all`。空的有效名单也不开放任何新 AI 任务。

学生名单与学校专业名单取并集。专业名单会开放对应专业的学生，也允许系统维护补建该专业网络。只配置学生名单时，首次专业网络必须由当前合资格学生触发；后台维护不会因为专业曾有一名灰度学生而自行扩张准入。

应用和 worker 必须部署相同配置并重启相应进程；配置不是数据库热开关。可以通过已认证学生的 `/api/career-path/state` 返回的 `ai_availability` 检查生效结果及 `policy_revision`，不需要把整个名单暴露给浏览器。先用明确的测试学生验证名单内外，再扩大名单。

共享专业网络虽然由 `system` 持有，入队仍检查持久网络行的学校和专业，以及服务端从当前学生解析并单独传递的 requester。请求 JSON 中的 `requested_by_student_id` 仅供审计，不能作为准入凭据。系统维护没有可信 requester 时，仅 `all` 或显式学校专业名单允许入队；筛选在 SQL 的 LIMIT 之前执行。被拒学生的请求不会把共享网络改成失败或暂停。

被拒绝的新 AI 请求返回 HTTP 403、`detail.code=rollout_limited`、`retryable=false`，不设置 `Retry-After`。页面持续显示分批开放说明，保留当前输入。基础测评提交不会因附带增强意图而失败；已接受的工作、已有候选与已生成版本继续可查看、取消或采用。缩小名单不是撤销过去的任务授权；如需暂停整个领域，继续使用原有 `CAREER_JOBS_ENABLED` 紧急开关，其既有语义包括暂停新的后台渲染任务。

验证入口：

```powershell
python -B -m unittest tests.test_career_ai_rollout tests.test_career_lifecycle tests.test_resume_versioned_workflows
python -B tools/career_rollout_postgres_probe.py --output .codex-temp/rollout-pg-new.json
```

PostgreSQL 工具只使用本机一次性随机 schema，模型 execute 是合成 stub；真实路由、入队、lease CAS、发布和纯 HTML 渲染不替换。结束检查 schema 已删除及源码指纹未变化，不执行 Office。

完整页面的专项验收使用新数据库目录和 loopback 服务：

```powershell
$env:CAREER_AI_ROLLOUT_MODE='allowlist'
$env:CAREER_AI_ROLLOUT_STUDENT_IDS='1'
$env:CAREER_AI_ROLLOUT_MAJORS='[]'
python -B tools/career_frontend_http_probe.py --port 8773 --output-dir .codex-temp/rollout-http-new
# 在另一个终端执行，结束后停止上面的测试服务：
node tests/frontend/career_rollout_http_probe.cjs http://127.0.0.1:8773 .codex-temp/rollout-browser-new
```

这两个工具不能挂到生产应用，也不证明生产容量 SLA。它们验证的是名单内外的真实请求组合、共享任务不会绕过准入，以及普通资料和手工简历流程完整。
