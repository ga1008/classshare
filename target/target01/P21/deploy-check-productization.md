# P21-B - 部署前后检查产品化工作单

状态：待实施  
范围：`deployment/deploy_remote.ps1`、Docker Compose 健康检查、本地/远程 smoke、部署报告  
目标：把部署前后检查固定为可重复、可失败、可留痕的产品化流程。

## 背景与现状

当前已有基础：

- `deployment/deploy_remote.ps1` 已支持 `-DryRun`，会构建 deploy manifest、拒绝受保护路径、创建 archive。
- `deployment/deploy_remote.ps1` 真实部署时会备份远程代码和数据库，执行 `docker compose config --quiet`、`docker compose up -d --build`，并检查 main/AI health。
- `docker-compose.yml` 已为 `ai`、`app`、`mailer`、`nginx` 配置健康检查。
- `package.json` 已有 `typecheck`、`test`、`build`、`inventory:frontend`、`test:e2e:p03`、`test:e2e:p12`。

风险是这些检查还没有产品化为一个固定门禁：

- 哪些命令必须在部署前跑，仍依赖人工记忆。
- build/typecheck/test、后端测试、manifest 检查、迁移 dry run、deploy dry run 没有一个统一报告。
- 部署后核心页面、worker 台账、最近错误日志、公网入口检查没有固定 smoke 清单。
- 历史 failed job 可能让 health 中的 background task 摘要显示 `ok:false`，需要区分“历史债务警告”和“本次部署新增失败”。

## 总体原则

- 部署入口继续使用 `deployment/deploy_remote.ps1`，不要另起一套绕过保护路径和备份逻辑的脚本。
- 所有真实部署前必须先跑 dry run。
- 部署前检查可以读本地真实代码，但可写业务测试必须使用 `.codex-temp` 复制数据根。
- 生产 postflight 默认只做只读检查；不提交作业、不上传材料、不发消息、不触发 AI 批改。
- 每次 preflight/postflight 必须生成报告，方便回溯“为什么准许上线”。
- 检查失败必须非零退出，不能只打印 warning 后继续部署。

## 建议交付物

建议新增：

```text
tools/deploy/preflight.ps1
tools/deploy/postflight.ps1
tools/deploy/check_manifest.py
tools/deploy/check_health_snapshot.py
```

建议新增 npm scripts 或文档命令：

```json
{
  "deploy:preflight": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/deploy/preflight.ps1",
  "deploy:postflight": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/deploy/postflight.ps1"
}
```

脚本命名可以调整，但必须做到：

- 一条命令跑完部署前门禁。
- 一条命令跑完部署后只读 smoke。
- 报告输出到 `.codex-temp/deploy-checks/<timestamp>/`。

## Preflight 检查目标

### 1. 工作区与清单安全

检查项：

- 当前分支、commit、dirty 状态。
- 已 stage 的删除是否和 deploy manifest 一致。
- `git ls-files -co --exclude-standard` 结果不包含受保护路径。
- deploy archive 不包含：
  - `data/`
  - `attendance/`
  - `chat_logs/`
  - `homework_submissions/`
  - `logs/`
  - `rosters/`
  - `shared_files/`
  - `storage/`
  - `node_modules/`
  - `venv/`
  - `.env`
  - `docker.env`
  - `tools/guardianangel.net.cn_nginx/`

期望结果：

- 不安全路径直接失败。
- 缺失文件直接失败。
- 工作区 dirty 时默认失败；如果允许 dirty，必须通过显式参数并记录原因。

### 2. 前端静态质量门

命令：

```powershell
npm run typecheck
npm test
npm run build
npm run inventory:frontend
```

期望结果：

- TypeScript 无错误。
- Vitest 全部通过。
- Vite build 成功并生成 `static/dist/manifest.json`。
- `docs/frontend-migration-inventory.md` 可刷新。
- manifest 中必须包含关键 entry，例如：
  - `frontend/src/islands/app-shell.tsx`
  - `frontend/src/islands/message-center-sync.tsx`
  - `frontend/src/islands/classroom-page.tsx`
  - `frontend/src/islands/materials-manage-page.tsx`
  - `frontend/src/islands/message-center-page.tsx`
- manifest 中每个 `file`、`css`、`imports` 指向的构建产物都存在。

### 3. 后端测试质量门

命令：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

如果后续正式接入 pytest，可追加：

```powershell
pytest
```

期望结果：

- unittest 全部通过。
- 权限、材料、作业、消息中心、后台任务、Vite manifest、API contract 测试均通过。
- 不允许用 skip/fixme 掩盖新增失败；既有 skip 必须在报告中列出。

### 4. 迁移 dry run 与复制库验证

目标不是对生产库做迁移，而是在复制库上证明：

- `init_database()` 或 migration runner 幂等。
- schema 补丁不会破坏现有数据。
- `PRAGMA quick_check` 为 `ok`。

建议命令形态：

```powershell
$env:LANSHARE_DATA_ROOT = ".codex-temp\p21-migration-dry-run"
$env:MAIN_DATA_DIR = ".codex-temp\p21-migration-dry-run"
$env:MAIN_DB_PATH = ".codex-temp\p21-migration-dry-run\db\classroom.db"
python tools\deploy\migration_dry_run.py
```

期望结果：

- 复制本地或指定 fixture 数据库到 `.codex-temp`。
- 初始化/迁移只作用于复制库。
- 输出迁移前后表数量、索引数量、quick_check。
- 任何写入真实 `data/classroom.db` 或远程 `/lanshare/data` 的行为直接失败。

### 5. Playwright 本地业务门

命令：

```powershell
npm run test:e2e:p12
npm run test:e2e:p03
```

期望结果：

- 使用 `.codex-temp` runtime root。
- P12 覆盖迁移页面：课堂页、chat、材料管理、消息中心。
- P03 覆盖核心业务：登录、课堂、作业提交、教师查看提交、AI 批改、材料管理、消息中心、后台任务、超管权限、数据安全。
- 可写测试不触碰真实本地 `data/classroom.db`。

### 6. deploy dry run

命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1 -DryRun
```

期望结果：

- 输出 deployable file count。
- archive 创建成功。
- 不上传文件。
- 不执行 docker compose。
- 不触碰远程 `/lanshare/data`。
- dry-run 报告保存到 `.codex-temp/deploy-checks/<timestamp>/deploy-dry-run.txt`。

### 7. 部署前健康快照

只读检查：

- `https://guardianangel.net.cn/api/internal/health`
- `http://106.53.153.171/api/internal/health` 或内网等价入口
- 远程 `docker compose ps`，如有 SSH 可用
- 当前 worker 台账或 health 中的 `background_tasks`

期望结果：

- 记录部署前 app/ai/nginx/mailer/worker 状态。
- 记录当前历史 failed count，用于部署后比较 delta。
- 如果部署前就有历史 failed count，不直接阻断，但必须在报告中标记为 pre-existing warning。

## Postflight 检查目标

### 1. 容器健康

检查：

- `docker compose ps`
- app health
- ai health
- nginx health
- mailer health

期望结果：

- app、ai、nginx、mailer 为 healthy 或明确可接受状态。
- agent-worker、blog-crawler 为 running。
- 最近启动时间与部署时间一致或合理。

### 2. 公网入口只读 smoke

检查：

- `GET https://guardianangel.net.cn/api/internal/health`
- `GET https://guardianangel.net.cn/teacher/login`
- `GET https://guardianangel.net.cn/student/login`
- `GET https://guardianangel.net.cn/static/dist/manifest.json` 或等价 manifest 可达性检查

期望结果：

- health 返回 200 且 `status=ok`。
- 登录页返回 200。
- manifest 可解析，包含关键 Vite entry。
- 不需要真实用户登录，不写生产业务数据。

### 3. 核心页面 smoke

生产默认只读：

- 未登录访问受保护页应重定向或拒绝，不应 500。
- 教师/学生登录页可渲染。
- 静态资源、CSS、Vite JS 可加载。

如果存在专用 staging 或用户明确授权的 QA 账号，才允许执行 authenticated smoke：

- 教师 dashboard 只读打开。
- 学生 dashboard 只读打开。
- 消息中心只读打开。
- 材料列表只读打开。
- 课堂页只读打开。

禁止项：

- 不提交作业。
- 不上传材料。
- 不发消息。
- 不启动 AI 批改。
- 不批量改分。
- 不清理真实队列。

### 4. Worker 状态 smoke

检查：

- AI 批改队列深度。
- material AI import 队列深度。
- session material generation 队列深度。
- private message AI reply 队列深度。
- email outbox worker heartbeat。
- blog crawler heartbeat。
- agent worker heartbeat。
- behavior write pipeline alive。

期望结果：

- health/ledger 接口可读。
- 队列深度不是 `null` 或解析失败。
- 活跃 worker 心跳在合理时间窗内。
- 部署后新增 failed count 为 0；历史 failed count 可作为 warning 记录。
- 若某 worker 本身不要求常驻，必须在报告中声明可接受条件。

### 5. 最近错误日志

检查：

```bash
docker compose logs --tail=120 app ai mailer agent-worker blog-crawler
```

期望结果：

- 无新增 traceback、critical、unhandled exception。
- 如果出现历史 error-like 文本，报告要区分时间戳和部署后新增。
- 不能只靠 grep “error” 简单失败；要保留原始日志片段供人工判断。

## 报告格式

建议每次生成：

```text
.codex-temp/deploy-checks/<timestamp>/
  preflight-report.json
  postflight-report.json
  deploy-dry-run.txt
  manifest-check.json
  health-before.json
  health-after.json
  docker-compose-ps-after.json
  worker-ledger-after.json
  public-smoke-after.json
```

报告 JSON 至少包含：

- `commit`
- `branch`
- `started_at`
- `finished_at`
- `status`
- `commands`
- `checks`
- `warnings`
- `failures`
- `data_safety`
- `remote`

其中 `data_safety` 必须明确：

- 是否使用 `.codex-temp` runtime。
- 是否触碰生产 `/lanshare/data`。
- 是否执行生产写入 smoke。
- 是否完成远程 DB backup。

## 失败规则

以下情况必须阻断部署：

- `npm run typecheck` 失败。
- `npm test` 失败。
- `npm run build` 失败。
- Vite manifest 缺关键 entry 或构建文件缺失。
- 后端 unittest 失败。
- migration dry run 未使用复制库。
- migration dry run quick_check 非 `ok`。
- Playwright P03/P12 失败且与本次改动相关。
- deploy dry run 失败。
- deploy manifest 包含受保护路径。
- 无远程 DB backup 且未获得用户明确授权跳过。

以下情况部署后必须视为失败或需要回滚评估：

- app/ai/nginx health 非 200。
- app 或 ai 容器不 healthy。
- 公开登录页 500。
- manifest 不可访问或不可解析。
- 部署后新增 traceback/unhandled exception。
- worker 台账接口 500。
- 队列状态解析失败。
- 数据目录大小异常突变且无法解释。

以下情况可作为 warning，但必须记录：

- 部署前已存在的历史 failed task count。
- npm audit 中非本次引入且未被当前目标处理的中低风险。
- Browserslist caniuse-lite outdated。
- 非核心 worker 无健康检查但容器 running。

## 验收清单

| 条件 | 验收方式 | 状态 |
| --- | --- | --- |
| preflight 一条命令可运行 | `tools/deploy/preflight.ps1` | 待实施 |
| postflight 一条命令可运行 | `tools/deploy/postflight.ps1` | 待实施 |
| preflight 覆盖 typecheck/test/build/unittest | 报告命令记录 | 待实施 |
| preflight 覆盖 manifest 检查 | `manifest-check.json` | 待实施 |
| preflight 覆盖迁移复制库 dry run | `migration-dry-run.json` | 待实施 |
| preflight 覆盖 deploy dry run | `deploy-dry-run.txt` | 待实施 |
| postflight 覆盖公网 health 与登录页 | `public-smoke-after.json` | 待实施 |
| postflight 覆盖 docker compose 状态 | `docker-compose-ps-after.json` | 待实施 |
| postflight 覆盖 worker 台账 | `worker-ledger-after.json` | 待实施 |
| postflight 区分历史 failed count 与新增失败 | health before/after delta | 待实施 |
| 报告保存在 `.codex-temp` | 目录存在且不入 git | 待实施 |
| 生产数据保护可证明 | 报告 `data_safety` 字段 | 待实施 |

## 数据安全提醒

部署检查产品化不是让生产环境承担测试负载。默认规则：

- 本地完整业务回归使用 `.codex-temp`。
- 远程生产只做只读 health、manifest、登录页、受保护页拒绝、worker 台账检查。
- 真实部署必须保留 `/lanshare/data`，并在部署前完成 DB backup。
- 任何生产写入 smoke 必须有用户明确授权、专用 QA 数据、可回滚策略和执行记录。
