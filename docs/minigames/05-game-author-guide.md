# 游戏作者手册与 SDK v1 开发规范

本文面向编写单个 HTML5 小游戏的开发者与辅助编码 AI。你的全部交付物是一个**不依赖外网、解压双击即可离线游玩、上传平台即可自动接入账号与多人联机的标准 `.zip` 包**。游戏自身负责画面渲染、布局适配、本地动画与交互音效；LanShare 平台负责身份鉴权、无歧义组队、随机匹配、权威时钟、队内聊天、个人私档与共同章节进度。

---

## 1. 先选定玩法模式与目录骨架

| 典型游戏类型 | `config.json` 关键配置 | 玩家在平台打开后的真实路径 | 双击 `main.html` 离线打开时 |
|---|---|---|---|
| **纯单人解谜 / 闯关**（如《星图拼接》） | `solo: true, multiplayer: null, standalone: "solo"` | 零准备步骤，直接进入游戏并自动加载本人云端进度；支持选章重玩。 | 使用浏览器本地存储（或内存 + JSON 导入导出）独立通关。 |
| **单人 + 多人合作**（如样例《航标接力》） | `solo: true, standalone: "solo"`, `multiplayer: { kind: "coop", privateRooms: true, minPlayers: 2, maxPlayers: 4, randomSizes: [2] }` | 默认直接单人开玩；随时可在宿主顶栏切往 2–4 人私房组队或双人随机匹配，满员 5 秒倒计时后从全队最远共同里程碑起航。 | 默认单人完整可玩（也可在游戏内菜单提供同机合作）。 |
| **纯双人棋盘对战**（如《双人连线棋》） | `solo: false, standalone: "local-seats"`, `multiplayer: { kind: "versus", privateRooms: true, minPlayers: 2, maxPlayers: 2, randomSizes: [2] }` | 展示双人匹配与私房创建/加入；配齐即进入 5 秒同步倒计时开局，服务端 `grid-line-v1` 权威判胜负。 | 自动进入 `localSeats` 模式，同屏轮流执黑白子完整下完一局。 |
| **纯私房团体协作 / 博弈** | `solo: false, standalone: "local-seats"`, `privateRooms: true, randomSizes: []` | 展示创建队伍（可选 `[minPlayers..maxPlayers]` 最高 10 人）、输入四位邀请码加入与历史阵容一键重开。 | 提供同屏多席位轮流操作演示完整规则。 |

### 标准包目录结构

```text
beacon-relay.zip
  main.html                     唯一页面入口（支持离线双击与平台沙盒嵌入）
  config.json                   主清单（元数据、舞台比例、模式、能力与契约路径）
  js/
    lan-game-sdk.v1.js          平台发行的固定字节摘要 classic 脚本（严禁手改）
    offline-contracts.js        由打包命令从三份 JSON 自动生成的离线契约脚本
    game.js                     游戏主控制器（同一份代码无缝兼容平台与本地适配器）
  css/
    game.css                    游戏自有样式（使用相对路径引用字体与背景图）
  static/
    beacon.png                  大厅圆角方标（PNG/WebP，128×128 至 1024×1024）
    levels/map-data.json        可选：包内静态关卡/图集 JSON 数据
    audio/chime.mp3             可选：音效、贴图、视频与本地 WOFF2 字体
  contracts/
    writeback.json              玩家私档白名单字段、存档谱系与线性共同里程碑
    rules.json                  确定性规则描述（多人必填；含共享里程碑的单人也填写）
```

### 双环境零改动运行原理

1. `main.html` 按顺序以相对路径引入 `<script defer src="./js/lan-game-sdk.v1.js"></script>`、`<script defer src="./js/offline-contracts.js"></script>` 与 `<script defer src="./js/game.js"></script>`（v1 统一采用 classic script，无需 ES Module 服务器即可在 `file://` 下直接双击运行；开发期可使用 TypeScript / Vite / Canvas 库，出包时打包为单文件或相对 classic 脚本即可，**严禁引用任何外部 CDN、Web Worker 或动态外网 URL**）。
2. 工具链 `pack` 命令将 `config.json`、`contracts/writeback.json` 与 `contracts/rules.json` 规范化序列化为单行：
   ```js
   window.LanGameOfflineContracts = {"config":{...},"writeback":{...},"rules":{...}};
   ```
   本地双击时，SDK 检测到顶层窗口无平台宿主参数，自动启用本地适配器读取该对象，并按 `(localProfile, gameKey, series, saveVersion)` 读写本机 `localStorage`（若浏览器隐私策略禁用存储则自动回退内存档并开放 JSON 导入/导出）。平台内运行时，服务端校验器仅比对 `offline-contracts.js` 文本与原 JSON 一致性，实际运行一律以宿主经安全桥下发的已验收契约为准。

3. 关卡 JSON、音效与画布要分别验证平台模式和双击模式。平台模式可通过 SDK 已验收的资源相对路径读取；双击时不依赖 `fetch(file://…)`。需要离线读取的 JSON 在构建时序列化为本地 classic JS 的数据常量，打包器核对与对应 JSON 内容一致；音频可用包内相对路径的媒体元素，像素采样必须时可预生成所需像素数据。在关闭网络、没有本地 HTTP 服务的浏览器完整走通包含这些资源的关卡。平台嵌入时遇到握手超时或权限丢失，宿主显示恢复或退出入口，禁止擅自创建本机进度替代云档。

---

### 作者打包与本地验收

在项目虚拟环境中运行下面的命令。示例中的 `my-game` 是包含 `main.html`、`config.json` 和各素材目录的游戏源码根目录。

```shell
python tools/minigame_pack.py pack my-game dist/my-game.zip
python tools/minigame_pack.py validate dist/my-game.zip
```

打包器从源码中的 JSON 生成离线契约，写入平台发行的 SDK，逐项校验路径、完整性、素材、人数范围与规则后输出 ZIP。源码目录会保留原样。重新生成同一输出文件时显式使用 `--replace`。成功输出包含游戏标识、版本、文件数和摘要；错误输出定位到文件和字段。教师上传后，平台还会用隔离浏览器验证在线握手、离线启动及静态素材，随后由教师打开预览并确认试玩结果。

## 2. 主清单 `config.json` 与展示契约规范

完整机器校验定义见 [config.schema.json](contracts/config.schema.json)，联调真实例子见 [example/config.json](example/config.json)。所有协议 JSON 均采用 UTF-8、无重复键且 `additionalProperties: false`（拒绝未知拼写错误字段）：

| 字段 | 类型与跨字段验收约束 |
|---|---|
| `formatVersion, sdk` | 固定为 `1` 与 `"js/lan-game-sdk.v1.js"`。验收器逐字节核对 SDK SHA-256，防止篡改通信桥。 |
| `gameKey, version` | `gameKey` 为 `^[a-z][a-z0-9-]{1,47}$` 稳定标识（首个通过验收的版本永久绑定至该游戏）；`version` 为 `X.Y.Z` 三段非负整数。同一游戏已通过验收的版本号不可改内容覆盖。 |
| `title, summary, icon, entry` | 纯文本默认标题（1–48 字）、简介（1–240 字）、包内 位于 `static/` 的 PNG 或 WebP 图标路径、固定入口 `"main.html"`。教师可在后台覆盖展示文案，但包本身无权自行上架或设定组织权限。 |
| `display`（可选，强烈推荐） | `{ "aspectRatio": "free" \| "16:9" \| "4:3" \| "1:1" \| "3:4" \| "9:16", "stageTone": "auto" \| "dark" \| "light" }`。省略时默认 `"free"` 铺满舞台与 `"auto"` 主题底色；固定比例棋盘或横版画布声明后，平台宿主会自动在桌面宽屏与手机横竖屏上等比居中留边，确保画面绝不拉伸变形。 |
| `standalone` | `"solo"`（离线单人通关）或 `"local-seats"`（离线同屏轮流/多席位）。`modes.solo == false` 时必须为 `"local-seats"`。 |
| `modes` | `{ "solo": boolean, "multiplayer": null \| { "kind": "coop"\|"versus", "privateRooms": boolean, "minPlayers": 2..10, "maxPlayers": 2..10, "randomSizes": [...] } }`。须满足 `minPlayers <= maxPlayers`，且 `randomSizes` 每一项均落在 `[minPlayers, maxPlayers]` 闭区间内。 |
| `capabilities` | 按需声明 `"save.private"`（读写 `writeback.privateFields`）与 `"room.chat"`（允许唤起宿主队聊抽屉；无多人模式时禁止声明）。 |
| `assets`（可选） | 当 JS 按关卡号动态拼接路径（如 `` `static/levels/lv-${n}.json` ``）时，在此数组显式列出确切 `static/...` 相对路径，供校验器核实文件存在并纳入内容网关白名单。 |
| `contracts` | `writeback` 固定为 `"contracts/writeback.json"`；`rules` 在支持多人时必须为 `"contracts/rules.json"`，纯单人无状态机时可为 `null`。 |

---

## 3. 沙盒安全边界、Opaque 握手与多媒体/画布最佳实践

### 3.1 为什么你的代码运行在 `Origin: null` 下，以及如何丝滑加载音效、字体与画布像素

平台将 `main.html` 挂载于无 Cookie 隔离内容源的 `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer">` 中。此时子帧拥有**唯一不透明源（Opaque Origin `"null"`）**：

1. **精确 `MessageChannel` 握手协议（SDK 内置，作者无需手写）**：
   - 宿主生成 128 位随机一次性 `nonce`，通过入口 URL Fragment `#ls_host=<encodeURIComponent(hostOrigin)>&ls_nonce=<nonce>` 传入子帧。`lan-game-sdk.v1.js` 在同步初始化阶段读取启动参数，将挑战留在本次握手的内存状态中；可尝试使用 History API 仅移除保留的启动 fragment，失败时继续握手。签发入口为无查询串地址，协议不依赖改写历史成功。宿主只在当前子帧预期的首次 load 后发探测，后续导航先注销旧绑定。
   - **标准合规细节**：因沙盒子帧为不透明源 `"null"`，根据 HTML 规范，父窗口探测时调用 `iframe.contentWindow.postMessage({ type: "lanshare:discover", hostOrigin }, "*")`（不含任何敏感数据）；子帧 SDK 严格校验 `event.source === window.parent && event.origin === expectedHostOrigin`，随即创建 `MessageChannel`，调用 `window.parent.postMessage({ type: "lanshare:hello", protocol: 1, nonce }, expectedHostOrigin, [channel.port2])` 移交端口。父页核验 `event.source === iframe.contentWindow`、`event.origin === "null"` 与未消耗 `nonce` 后，校验恰好收到一个端口。**子帧保留 port1，父页通过收到的 event.ports[0]（转移来的 port2）回送 welcome**。挑战只接受一次，并绑定启动上下文、固定版本、宿主实例及加载代次。刷新、导航或观察到撤权须关闭端口并拒绝未完成请求；服务端独立复核每次写入。父页恢复浏览器往返缓存（pageshow.persisted）时，先重验当前会话并重新挂载子帧，不能继续使用离页时已关闭的端口。[WHATWG Channel messaging](https://html.spec.whatwg.org/multipage/web-messaging.html#channel-messaging)。
2. **Web Audio、WOFF2 字体、JSON 关卡与 `<canvas>` 像素读取须知**：
   - 平台部署时，内容网关须给已授权的包内资源返回 `Access-Control-Allow-Origin: null`。CSP 的 `connect-src` 显式列出内容站 HTTPS origin。用相对路径、`credentials: "omit"` 读取静态关卡 JSON 或音频字节，再交给 `decodeAudioData`。CORS 只决定浏览器能否读取响应，授权由网关执行。续租期间相对资源基址保持稳定。
   - **严禁在相对路径后拼接动态查询参数**（如 `./static/beacon.png?t=123`）：为阻断侧信道外带，内容网关对任何含 `?query` 的请求一律返回 `404`。
   - **画布像素读取防污染（Canvas Tainting）**：若你需要把 `static/` 下的图片画入 `<canvas>` 并调用 `ctx.getImageData(...)`，创建图片时**必须声明 `img.crossOrigin = "anonymous"`**（或 `<img crossorigin="anonymous" src="./static/...">`），因为在 `"null"` 源下未声明 `crossOrigin` 的图片会被浏览器视为跨源不透明图像而锁定画布读取。
   - **浏览器自动播放与宿主静音联动**：新标签页打开游戏时，子帧内的 `AudioContext` 需在玩家首次点击/按键（`pointerdown` / `keydown`）时调用 `audioCtx.resume()` 解锁；同时务必监听 SDK 的 `audio.changed` 事件（按事件载荷 `{muted}` 更新；初值取 `ready().audio.muted`），确保玩家点击宿主顶栏「静音」按钮时游戏立即静音。

---

## 4. `window.LanGame` SDK v1 接口手册

| 方法 / 事件 | 入参与返回值 | 语义约束与错误处理 |
|---|---|---|
| `await sdk.ready()` | 返回 `{ protocol: 1, environment, me, mode, capabilities, roomId, runId, audio: { muted } }`。`environment` 为 `"platform" \| "preview" \| "local"`；`me` 为 `{ playerId, displayName, seat }`；`mode` 为 `"solo" \| "private" \| "random" \| "localSeats"`。 | 整个页面生命周期只握手一次。候场预热阶段 `runId` 可为 `null`，待倒计时结束收到 `session.changed` 后进入可操作对局。 |
| `await sdk.definition()` | 返回冻结的 `{ config, writeback, rules }`。 | 只读结构；平台与离线双击返回完全一致的对象。 |
| `await sdk.session.snapshot()` | 返回 `{ phase, mode, startRank, seat, turnSeat, state, outcome, members, revision, seq, serverNow, startsAt }`。 | `phase !== "running"` 时仅渲染等待态或结局结算，不得提交玩法意图。 |
| `await sdk.game.intent(action, opts)` | `action = { type, payload }`，`opts = { expectedRevision, requestId }`；返回 `{ runId, revision, throughSeq }`。 | 用户每次有效点击生成一个 `requestId`；网络超时重试复用同一 `requestId`；收到 `STALE_REVISION` 时重拉 `snapshot()` 刷新画面。 |
| `await sdk.progress.read()` | 返回 `{ series, saveVersion, rank, milestoneId, privateSave, privateRevision }`。 | `rank` 为本人历史已解锁的最高共同里程碑索引（`0` 起）；当前对局若为选章重玩，本局起点看 `snapshot.startRank`。 |
| `await sdk.progress.writePrivate(values, opts)` | 全量提交已声明的 `privateFields` 对象，`opts = { expectedRevision, requestId }`；返回 `{ privateRevision }`。 | 须在 `config.capabilities` 声明 `"save.private"`。建议在设置变更防抖（如 500ms）及过关时调用，勿每帧调用。 |
| `sdk.progress.onFlush(handler)` | 注册当前游戏唯一的切换前存档回调，返回取消注册函数。handler 结束时返回完整的最新私档对象，已确认且无变更时返回 null。 | 本地有防抖或内存草稿的授权游戏必须注册；回调只收集值，不自行发切换命令。无持久能力的包直接回应无待存数据。 |
| `sdk.newRequestId()` | 为一次用户操作生成随机 ID，SDK 将该操作、请求摘要和键保留至结果明确。 | 同一次超时重发沿用原键；不同用户动作分配新键。 |
| `await sdk.history.list({ cursor, limit })` | 返回本人在**当前游戏**的近期对局记录（开始/结束时间、模式、结果、抵达里程碑与 `nextCursor`，单页上限 20）。 | 不含对手真实账号或聊天内容。 |
| `await sdk.room.members()`、`await sdk.chat.open()` | 读取本局队友名片与连接状态（`active / reconnecting`）；请求宿主展开队内聊天抽屉并返回 `{ opened: boolean }`。 | 聊天输入与发送完全在可信宿主面板内完成，游戏代码无法读取聊天文本或代发消息。 |
| `await sdk.game.finish({ requestId })` | 仅限 `rules: null` 的纯单人自由玩法，向平台登记一次“自主完成”。 | 有 `rules.json` 的游戏由服务端规则引擎进入终态自动结算，调用此方法会返回 `NOT_APPLICABLE`。 |
| `sdk.on(event, handler)`、`sdk.dispose()` | 事件集：`session.changed`、`room.changed`、`game.changed`、`progress.changed`、`audio.changed`、`connection.changed`、`session.ended`。 | `on` 返回 `unsubscribe()`。`pagehide` 时调用 `dispose()` 释放监听与音频句柄。 |

### 从单人局切到组队时怎样收好最后一笔存档

只打开或取消组队面板时，单人局继续有效。玩家实际提交创建/加入/排队后，宿主发带 `transitionId` 和当前 `controlEpoch` 的 `prepareLeave`，SDK 暂停本局输入、先核准之前在途的写回执，再调用 onFlush 并返回 `{transitionId, privateSave, expectedPrivateRevision}`。宿主随同一个幂等切换命令提交。服务端同事务复核目标可加入、控制代数和私档修订，保存最后草稿、挂起原局并分配目标多人占位；取得成功回执才更换画面。回调超时、错码、房满或私档冲突时保留原局和草稿，解除冻结。若提交结果未知，先按原键取回执/读当前活动，确认未切换后才能解冻。对已确认的退出或终局，重新登录后取权威快照；浏览器关闭时以最后确认的云档为准。

端口消息限定 `{v,id,method,params}`、`{v,id,ok,result|error}` 与 `{v,event,seq,data}`。宿主检查当前端口代次、方法、参数、字节预算与能力。只读页签可看自己有权读取的快照，不能冒用当前控制页发动作、写私档、续控制租约或代发聊天。第一次握手与换房后的身份可不同，`session.changed` 携带新上下文，刷新后以最新快照内的本人席位恢复。

### 接入示例（《航标接力》核心控制流）

```js
const sdk = window.LanGame;

async function boot() {
  let ctx = await sdk.ready();
  setAudioMuted(ctx.audio.muted);

  const syncView = async () => {
    const snap = await sdk.session.snapshot();
    renderBoard(snap, ctx.me);
  };

  const unsubs = [
    sdk.on("session.changed", (updated) => {
      ctx = updated;
      syncView().catch(renderError);
    }),
    sdk.progress.onFlush(() => collectPendingPrivateSave()), // 游戏提供完整草稿或 null
    sdk.on("game.changed", () => syncView().catch(renderError)),
    sdk.on("audio.changed", ({ muted }) => setAudioMuted(muted)),
    sdk.on("session.ended", ({ reason }) => renderEndedBanner(reason)),
  ];

  window.addEventListener("pagehide", () => {
    unsubs.forEach((off) => off());
    sdk.dispose();
  }, { once: true });

  await syncView();
}

// 玩家点击罗盘方向或返航靠岸：每次用户意图生成唯一 requestId，重试保持同键幂等
async function submitMove(type, payload, currentRevision, requestId) {
  // 按键处理器在第一次提交前用 sdk.newRequestId() 创建 requestId，并保存到确认。
  return await sdk.game.intent({ type, payload }, {
    expectedRevision: currentRevision,
    requestId,
  });
}

boot().catch(renderError);
```

---

## 5. 存档契约 `writeback.json` 与规则契约 `rules.json`

### 5.1 个人私档与共同里程碑（[writeback.schema.json](contracts/writeback.schema.json)）

- **`privateFields`**：每个字段必须声明明确类型与合法默认值，支持 `boolean`、带 `[min, max]` 的 `integer`、有限 `enum`，以及封闭嵌套结构的 `document`（内部支持固定键 `object`、有界 `array`、范围 `integer`、限长 `string`、有限 `enum` 与 `boolean`，总 JSON ≤ `32 KiB`、深度 ≤ `8` 层）。若 `privateFields` 为空 `{}`，则 `capabilities` 中不得申领 `"save.private"`，反之亦然。
  - document 中的 `{"type":"enum","values":[…]}` 是本协议的简记。解析器先递归转为标准 `{"type":"string","enum":[…]}`，再校验默认值及每次实际写入。枚举值唯一且默认值属于集合，有界数值/文本的上下限不能颠倒，required 必须引用已声明的属性。校验深度和原始字节预算先于递归解析。[JSON Schema 枚举](https://json-schema.org/understanding-json-schema/reference/enum)
- **`milestones`** 按索引从 0 连续编号。每项有唯一 id、恢复节点 resumeState，可选纯文本 label。例如 `{"id":"pier","label":"码头","resumeState":"atPier"}`；没有 label 时宿主显示“第 1 章”。label 用于展示，可随版本修订，存档比较使用稳定的 id 和索引。
  - 无章节积累的对战棋牌仅写一项 `[{ "id": "start", "resumeState": "board" }]`；无规则的自由单机写 `[{ "id": "start", "resumeState": null }]`；
  - 有章节的合作/闯关游戏中，当规则状态机首次推进至 `milestones[k].resumeState` 时，服务端自动将全队成员的最高 `shared_rank` 提升至 $\max(\text{old\_rank}, k)$；
  - **重玩友好承诺**：玩家通关全部航标后，仍可在宿主顶栏选择任意已解锁里程碑（如回到 `rank 0` `"pier"`）重新体验完整流程，此时 `snapshot.startRank` 为所选起点，而 `progress.read().rank` 始终保留其历史最高解锁章，绝不倒退。

### 5.2 统一规则入口（[rules.schema.json](contracts/rules.schema.json)）

`contracts/rules.json` 按顶层 `"engine"` 字段选择服务端确定性解析器：

1. **`"finite-turn-v1"`（轮次、合作与状态图）**：
   - 定义初始状态 `initial`、状态集合 `states`（`outcome` 为 `null / "completed" / "draw" / "actorWins"`）与有向边 `transitions`（`from, actor("turn"|"any"), type, payload, to, turn("keep"|"next")`）。
   - **席位参与度**。从 rank 0 完整体验一场“全员轮流操作”的私房局，验收要按实际座号执行并记录谁接到了合法回合；只数边数，遇到 `turn:keep` 就可能误判。从 [航标接力规则](example/contracts/rules.json) 的 `atPier` 出发，4 席依次提交东、南、西及返航动作。若从海角（rank 2）续玩，剩余两步属于正常进度；验收分别记录“从头参与”和“尾章可完成”，无需人为添加等待回合。全员可同时操作或允许提前取胜的玩法按其声明验证，不套用轮流操作步数。
   - 结构校验通过后还需验证初始/引用节点存在、非终态有出边、终态无出边、同一节点的意图与精确 payload 只命中一条边。以每个规范恢复点为起点，对 `(node,highestRank)` 作有界遍历，确认可达终态、首次解锁不跳级，公共恢复点唯一且可继续。运行时在事务内按同一规则复核回合与 expectedRevision，终态只结算一次。
2. **`"grid-line-v1"`（双人连线棋盘，独立子 Schema 见 [grid-rules.schema.json](contracts/grid-rules.schema.json)）**：
   - 仅需一行，例如五子棋 `{"engine": "grid-line-v1", "size": 15, "line": 5}` 或井字棋 `{"engine": "grid-line-v1", "size": 3, "line": 3}`（须满足 `3 <= line <= size <= 19`）。
   - 意图固定为 `type: "place", payload: { x, y }`（`0 <= x, y < size`），在线为固定 2 席的 versus 对战，独立入口为 local-seats。席位 `1` 先手，服务端自动完成落子合法性、四向连线判胜、满盘判和与断线重播。

---

## 6. 出包自检与联调验收清单

在将 ZIP 提交给教师上传前，请逐项核对以下 7 条自检标准：

| 检查项 | 预期结果 |
|---|---|
| **1. 纯离线双击通关** | 断开网络，解压 ZIP 直接双击 `main.html`：零控制台报错、零外网请求，单人或同机多席可完整走完一局，刷新后进度可恢复或可导出 JSON。 |
| **2. Schema 正反例校验** | 原包通过 `config / writeback / rules` 全量校验；故意改为 `maxPlayers: 11`、`randomSizes: [5]`（超出 `maxPlayers: 4`）或 `line: 6, size: 5` 时均被精准拦截并指出字段路径。 |
| **3. 沙盒媒体与画布零 CORS** | 在平台预览壳（`sandbox="allow-scripts"`）内，图标、WOFF2 字体、Web Audio 音效、`static/*.json` 关卡与 `<canvas>` 像素操作均无 `SecurityError` 或 `CORS` 拒绝；无 `?query` 资源请求。 |
| **4. 舞台比例与宿主静音** | 在 `1440×900` 桌面、`390×844` 竖屏手机与 `844×390` 横屏手机下画面按 `display.aspectRatio` 等比居中、按钮不裁切；点击宿主顶栏静音开关时游戏声音即时响应。 |
| **5. 多人同步与防抢回合** | 双账号加入同一房间：5 秒倒计时同步开局；非当前回合席位提交动作或提交非法 `payload` 均被服务端拒绝；网络抖动重发同一 `requestId` 幂等返回原结果。 |
| **6. 共同里程碑与选章重玩** | `rank=0` 与 `rank=2` 玩家组队：倒计时取消不改档，正式开局后 `0` 号同步至 `2` 且双方私档独立；通关后选 `startRank=0` 重玩可从头游玩且最高 `rank=2` 保持不变。 |
| **7. 断线重连与撤权收敛** | 对局中刷新浏览器可无缝回到原席位与当前回合；教师后台点击下架或玩家退出登录后，子帧端口立即关闭且无法继续写入。 |
