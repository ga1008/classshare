# LanShare 小游戏开发规范

适用对象为制作 HTML 游戏包的开发者和 AI。交付物是一份教师可以直接上传的 ZIP。玩家解压后双击 `main.html` 可以离线游玩，同一份文件上传后由平台提供身份、组队、对局与存档。本文使用游戏包协议 v1，教师可将完整开发资料包原样交给 AI。资料包含本文 `AI-GAME-SPEC.md`、原版 SDK、四份 JSON Schema、三个游戏示例及与平台同源的打包校验工具。若只收到本文，请先向教师索取同版开发资料包或原版 SDK，再交付成品。平台核对 SDK 文件摘要，自行仿写或格式化都会使游戏包验收失败。

## 1. 先选一个能实现的玩法

| 目标 | 起点 | 平台怎样判定进度或胜负 |
| --- | --- | --- |
| 自由单人，如找不同、节奏练习 | 使用第 3 节的最小包，`rules: null` | 游戏自己处理本局玩法。完成后调用 `game.finish`。需要恢复个人练习状态时声明私档。共同里程碑只有一个。 |
| 单人分关或多人轮流协作、对战 | 单人参考 `star-map.zip`，合作参考 `beacon-relay.zip`。规则引擎为 `finite-turn-v1` | 把每个状态及允许的动作写入规则文件。平台逐步核对动作，推进章节，到达终态自动结算。 |
| 双人轮流落子、横竖斜连续 N 子获胜 | 从 `three-in-line.zip` 起步。规则引擎为 `grid-line-v1` | 平台维护棋盘、占位、轮次和胜负。游戏控制器只绘制棋局并提交坐标。 |

v1 的跨设备联机支持上表两个规则引擎。自由移动同步、任意广播、自定义权威服务器、实时物理对抗等需求超出 v1 能力时，应先与教师确认改为回合动作或自由单人。复制示例后要同时检查它的 `js/game.js`。三个示例共用的控制器会按 `gameKey` 选择界面，改游戏标识时必须一并替换相应分支和写死的题目、动作及棋盘尺寸。

**交给 AI 的工作顺序**

1. 写下单人或多人、在线人数、离线操作方式、每步动作、胜负条件、需要保存的私有字段。选定上表中的一行。
2. 先完成最小可玩的“一次输入 → 状态变化 → 结算”流程。多人先设计规则和席位，再画 UI。
3. 按第 2～6 节写齐文件，复制原版 SDK，生成离线描述。每次改动清单或契约后重新生成。
4. 按第 7 节运行检查，将游戏 ZIP 和验收结果交给教师。开发资料包、测试说明和游戏 ZIP 分开交付。

## 2. 核对包内目录和加载顺序

```text
main.html
config.json
js/lan-game-sdk.v1.js
js/offline-contracts.js
js/game.js
static/icon.png
contracts/writeback.json
```

以上是第 3 节示例的完整目录。需要规则时增加 `contracts/rules.json`，有样式时可增加 `css/game.css`。素材可在 `static/` 下分目录。把这些内容放在 ZIP 根目录最稳妥，平台也可解开恰好一层统一外壳目录。不要将开发资料整包、其他游戏 ZIP、Markdown、构建脚本、源码映射、依赖目录或系统隐藏文件一起压入上传包。

| 检查项 | 游戏 ZIP 要满足的条件 |
| --- | --- |
| 文件类型 | 唯一 HTML 为根目录的 `main.html`。JavaScript 放 `js/`，CSS 放 `css/`。素材允许 `png jpg jpeg webp gif mp3 ogg wav mp4 webm woff2 json`。图标限 PNG 或 WebP，宽、高各 128～1024 像素，普通图片不超过 2000 万像素。扩展名与真实文件格式一致。 |
| 路径 | 使用相对路径和正斜杠，逐字匹配大小写。建议使用小写 ASCII 文件名。清单的图标路径只接受 ASCII 字母、数字、下划线、短横线及目录分隔符，扩展名小写。包内路径不超过 240 个 UTF-8 字节、16 级，无隐藏段、盘符、系统保留名或大小写重名。 |
| 大小 | ZIP ≤ 64 MiB，解压后总量 ≤ 192 MiB，普通单文件 ≤ 32 MiB，HTML、JS、CSS 和 JSON 文本单文件 ≤ 8 MiB。最多 1000 个文件、2000 个 ZIP 条目（含目录），单条目展开与压缩比 ≤ 1500。使用普通文件和目录、Store 或 Deflate 压缩。 |
| 引用方式 | HTML 的 `src`、`href`、`srcset`、`poster` 和 CSS 的 `url()`、`@import` 都按所在文件解析。CSS 中引用同包图片可用 `../static/icon.png`。运行资源全部随包提供，引用不使用查询串、外部 URL、站点根路径或 CDN。 |
| 运行脚本 | 按顺序加载外置 **classic script**，第一个为原版 SDK，第二个为离线描述，第三个起才是自编控制器。把监听器写进 JS。模块脚本、内联脚本、HTML 的 `onclick` 等属性、`iframe`、`base`、`form`、`object`、`embed`、自动跳转均不能用于入口。 |
| 浏览器环境 | 平台运行于隔离的 iframe。不要自行访问平台 Cookie、父页面 DOM、平台 HTTP 接口或登录令牌。WebSocket、外网请求、Service Worker、Web Worker、弹窗登录、摄像头和麦克风不能作为运行依赖。多人状态与身份全部通过 SDK 取得。 |
| 文本与素材 | 文本使用 UTF-8。JSON 仅用双引号，各键唯一，嵌套深度 ≤ 16，不含注释、尾逗号、`__proto__`、`constructor`、`prototype` 或非有限数。契约数字用整数。确保图片可解码、字体可装载、音频可解码、视频能取到首帧。 |

需要动态关卡数据时，可把同一源数据在构建时生成到外置 classic JS 常量中，直接双击也能读取。若同时交付 `static/*.json`，生成时自行比对两者。不要以 `fetch("config.json")` 或 `fetch("static/levels.json")` 成功作为离线启动条件。入口建议带移动端 viewport，提供触屏和键盘都能操作的按钮，音频从玩家手势启动并遵守宿主静音状态。

## 3. 逐文件制作一个可上传的最小包

下面以“选择偶数”为例。先新建独立游戏目录，按小标题保存文件。将开发资料包的 `static/minigames/lan-game-sdk.v1.js` 复制到游戏的 `js/lan-game-sdk.v1.js`，以二进制复制保留原字节和换行。自行准备一张 256×256 PNG 存为 `static/icon.png`，也可先复制 `docs/minigames/downloads/star-map.zip` 中的同名图标完成联调。

### config.json

```json
{
  "formatVersion": 1,
  "gameKey": "even-number-practice",
  "version": "1.0.0",
  "title": "选择偶数",
  "summary": "在 2 和 3 中选出偶数，答对即完成本次练习。",
  "entry": "main.html",
  "icon": "static/icon.png",
  "sdk": "js/lan-game-sdk.v1.js",
  "standalone": "solo",
  "modes": {"solo": true, "multiplayer": null},
  "capabilities": [],
  "contracts": {"writeback": "contracts/writeback.json", "rules": null}
}
```

### contracts/writeback.json

```json
{
  "formatVersion": 1,
  "series": "practice",
  "saveVersion": 1,
  "privateFields": {},
  "milestones": [{"id": "start", "label": "练习", "resumeState": null}]
}
```

### main.html

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>选择偶数</title>
</head>
<body>
  <main>
    <h1>哪个是偶数？</h1>
    <button type="button" data-answer="2" disabled>2</button>
    <button type="button" data-answer="3" disabled>3</button>
    <p id="message" role="status">正在打开练习…</p>
  </main>
  <script src="js/lan-game-sdk.v1.js"></script>
  <script src="js/offline-contracts.js"></script>
  <script src="js/game.js"></script>
</body>
</html>
```

### js/game.js

```javascript
(async () => {
  const sdk = window.LanGame;
  const message = document.getElementById("message");
  const buttons = Array.from(document.querySelectorAll("[data-answer]"));
  const setEnabled = enabled => buttons.forEach(button => { button.disabled = !enabled; });
  const context = await sdk.ready();
  const snapshot = await sdk.session.snapshot();
  if (snapshot.phase !== "running") {
    message.textContent = "本局当前不可操作。";
    return;
  }
  message.textContent = context.me.displayName + "，请选择一个数字。";
  let finishId;
  setEnabled(true);
  for (const button of buttons) {
    button.addEventListener("click", async () => {
      if (button.dataset.answer !== "2") {
        message.textContent = "3 除以 2 有余数，再试一次。";
        return;
      }
      setEnabled(false);
      finishId ??= sdk.newRequestId();
      try {
        await sdk.game.finish({requestId: finishId});
        message.textContent = "答对了，本局已完成。";
      } catch (error) {
        const current = await sdk.session.snapshot().catch(() => null);
        if (current?.outcome) {
          message.textContent = "本局已结算。";
        } else {
          message.textContent = "结算尚未确认，可点击 2 重试。";
          button.disabled = false;
        }
      }
    });
  }
})().catch(error => {
  document.getElementById("message").textContent = "打开失败：" + (error.code || error.message);
});
```

### js/offline-contracts.js

用实际 JSON 自动生成，避免手工维护两套配置。在游戏目录中运行以下 Python 3 代码即可。生成脚本放在 ZIP 外。输出是唯一一条 `window.LanGameOfflineContracts = …;` 赋值，末尾分号必需。三个键为 `config`、`writeback`、`rules`，无规则时最后一个值为 JSON 的 `null`。

```python
import json
from pathlib import Path

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

config = read_json("config.json")
contract = config["contracts"]
offline = {
    "config": config,
    "writeback": read_json(contract["writeback"]),
    "rules": read_json(contract["rules"]) if contract["rules"] else None,
}
Path("js/offline-contracts.js").write_text(
    "window.LanGameOfflineContracts = " + json.dumps(offline, ensure_ascii=False, allow_nan=False) + ";\n",
    encoding="utf-8",
)
```

在新目录解压并双击入口，应当显示两个按钮。点击 3 能重试，点击 2 显示完成并停止继续答题。新作品请分配自己的 `gameKey`。同一游戏升级时保持已有 `gameKey`。

## 4. 填写清单、存档和规则

**主清单使用 `config.schema.json`。** 第 3 节清单中的字段都是必填。允许添加的顶层字段只有 `display`、`assets`。键名、大小写和 JSON 基本类型必须完全匹配，中文用于标题、简介、标签等展示文本。

| 字段 | 可填写的值 |
| --- | --- |
| `formatVersion`、`version`、`gameKey` | 协议号固定为数字 1。发行版本是三个用点隔开的十进制整数，每段 0～99999，无前导零，如 `"1.2.0"`。标识符匹配 `^[a-z][a-z0-9-]{1,47}$`，新作品使用独有标识。 |
| `title`、`summary`、`entry`、`sdk`、`icon` | 标题 1～48 字符，简介 1～240 字符，均有实际文字。入口、SDK 路径固定为第 3 节所列。图标文件位于 `static/` 且真实存在。 |
| `standalone`、`modes.solo` | `standalone` 区分离线 `"solo"` 与 `"local-seats"`。在线 `solo` 为布尔值。纯单人配置使用 `true`、`multiplayer:null` 和 `standalone:"solo"`。纯多人使用 `false`、有效的多人对象和 `standalone:"local-seats"`。 |
| `modes.multiplayer` | 对象示例为 `{"kind":"coop","privateRooms":true,"minPlayers":2,"maxPlayers":4,"randomSizes":[2]}`。五项全部必填。`kind` 为 `coop` 或 `versus`。人数为 2～10 的整数且最小值 ≤ 最大值。随机人数列出区间内可成局的明确人数，不能重复。仅私房用 `true` 和空数组，仅随机用 `false` 和非空数组。 |
| `capabilities` | 只可列 `save.private`、`room.chat`，各至多一次。`privateFields` 非空时必须包含 `save.private`，为空时必须省略该能力。仅多人游戏可声明 `room.chat`。无可选能力时填 `[]`。 |
| `contracts` | `writeback` 固定为 `"contracts/writeback.json"`。`rules` 取 `null` 或 `"contracts/rules.json"`。多人必须提供规则文件，单人若需平台核准分关也提供规则文件。 |
| `display`、`assets` | `display.aspectRatio` 可取 `free`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。`stageTone` 可取 `auto`、`light`、`dark`。可选的 `assets` 数组最多 1000 条唯一的真实包内 `static/` 路径，命名与扩展名须符合 schema。所有实际使用的文件均须随包交付。 |

**存档使用 `writeback.schema.json`。** `contracts/writeback.json` 是本地声明文件。平台以游戏、玩家、`series`、`saveVersion` 隔离存档。游戏通过 SDK 读写，不能用 HTTP 地址替换该文件路径。规则名称、`series`、里程碑 ID、私档字段名均用小写字母开头，后续为英文字母、数字或下划线，总长 1～40。

- `saveVersion` 为 1～65535 的整数。`privateFields` 可为空对象，最多 64 项。每项有 `type` 和 `default`，默认值必须满足该项全部约束。`boolean` 用布尔值。`integer` 同时写 `min`、`max`，两者在有符号 32 位整数范围内。`enum` 同时写 `values`，包含 1～64 个唯一合法名称。`document` 同时写 `schema`。
- `document.schema` 是有界结构。对象列出 `properties`、`required` 和 `additionalProperties:false`。数组列出 `items` 和 `maxItems`（上限 4096）。嵌套整数使用 `minimum/maximum`，字符串使用 `maxLength`（上限 256），枚举使用 `values`。可含布尔值。结构层级最多深入 8 层。每次提交**完整私档对象**，紧凑 JSON 的 UTF-8 总长 ≤ 32768 字节。
- `milestones` 有 1～256 个元素，顺序由浅到深。每项必填 `id`、`resumeState`，可加 1～48 字符的 `label`。`id` 和恢复状态各自唯一。自由单人只有一项，恢复状态为 `null`。多人开局默认取该存档谱系内队员已确认的最高里程碑。平台向前对齐共同进度，各人的私档保持独立。选择已解锁章节重玩时，个人历史最高记录继续保留。
- 升级同一款游戏时递增发行 `version`。在同一 `series/saveVersion` 中发布新版本，保留旧里程碑的 ID、恢复状态和顺序，保留旧私档字段的完整定义。新增里程碑接在末尾，新增字段给出有效默认值。需要重排章节或改动旧字段类型时，启用新 `saveVersion` 或 `series`，向教师说明旧存档无法自动沿用。

**规则统一用 `rules.schema.json` 验证。** 棋盘分支也可以用 `grid-rules.schema.json` 单独检查。四份 schema 是开发工具输入，上传游戏中只放实际的清单和契约 JSON。

- **有限状态。** 顶层为 `engine:"finite-turn-v1"`、`initial`、`states`、`transitions`。1～1024 个命名状态，每个状态形如 `{"outcome":null}`（可操作），或以 `completed`、`draw`、`actorWins` 作为结局。合作玩法禁止 `actorWins`。每个恢复节点都可操作，第一个恢复节点等于初始状态。
- **动作。** 1～4096 条边，每条有 `from`、`actor`、`type`、`payload`、`to`、`turn`。如 `{"from":"start","actor":"turn","type":"choose","payload":{"answer":"even"},"to":"done","turn":"keep"}`。`actor` 取 `turn`（轮到的席位）或 `any`（任一席位）。`turn` 取 `next`（顺次轮换）或 `keep`。载荷最多 8 个命名字段，每值是合法名称字符串、−32768～32767 的整数或布尔值。UI 提交的动作类型及载荷字段和值要与一条边精确匹配。
- **规则图自检。** 每个状态都能从初始状态到达，且能沿有向边抵达某个终点。终点无出边，同一 `(from,type,payload)` 唯一，首次前进一次至多越过一个里程碑。静态遍历预算为 100000 次边检查。轮流多人玩法应在所承诺的最大人数下验证每个席位都有合法操作。改了关卡名，须同步更新规则、里程碑、控制器以及生成的离线契约。
- **连线棋。** 全部规则可写为 `{"engine":"grid-line-v1","size":3,"line":3}`。满足 `3 ≤ line ≤ size ≤ 19`，恰好两人、`kind:"versus"`、`minPlayers:2`、`maxPlayers:2`，唯一恢复状态为 `"board"`。提交 `{type:"place",payload:{x,y}}`，两个坐标均为 0 起始的整数。按 `state.board[y * size + x]` 读取格子，0 为空，1、2 为席位。只能向空格落子。平台判定横、竖或两条斜线上的连续棋子，满盘无胜者判和。

## 5. 接入真实 SDK

取 `const sdk = window.LanGame`。先 `await sdk.ready()`，通过 `context.me.displayName` 显示本局昵称，`me.seat` 表示本人席位。游戏只接收本局身份名片。使用 `context.environment` 判断运行环境。

| 环境值 | 怎样触发 | 应实现的行为 |
| --- | --- | --- |
| `platform` | 从已发布且有权限的游戏进入 | 使用宿主会话。自由组队、历史队伍、四位码、随机匹配、满员五秒倒计时、重连和聊天窗口由宿主提供。游戏关注对局状态和输入。 |
| `preview` | 教师在验收就绪后进入预览 | SDK 使用临时模拟对局。预览不产生玩家正式云存档。多人游戏需可在预览中演示席位轮换。 |
| `local` | 解压后顶层双击 `main.html`，地址为 `file:`，无宿主参数 | 使用本机存储或内存。离线 `local-seats` 对应 `context.mode === "localSeats"`，按 `snapshot.turnSeat` 把操作权交给同机下一位玩家。直接用 HTTP 静态站打开入口不会触发本机模式。 |

| SDK 方法 | 参数、返回值与适用范围 |
| --- | --- |
| `await sdk.ready()`、`await sdk.definition()` | 前者返回 `me, environment, mode, capabilities, runId, roomId, audio` 等上下文。后者返回 `{config,writeback,rules}`。使用定义读取棋盘尺寸、玩法与字段，避免写死样例数据。 |
| `await sdk.session.snapshot()` | 读取 `phase, state, revision, turnSeat, members, outcome` 等。运行时只在 `phase === "running"` 下提交游戏动作，轮次玩法同时检查本人席位。接收变化通知后再读快照并重绘。 |
| `sdk.newRequestId()` | 为一次用户写操作生成 ID。由游戏保存这个 ID 及原参数，直到结果确定。 |
| `await sdk.game.intent(action, opts)` | 有规则时使用。`action` 只含 `type,payload`。`opts` 为 `{requestId, expectedRevision: snapshot.revision}`。模拟与平台成功时均返回含 `runId,revision,throughSeq` 的回执。胜负和共同进度由规则引擎提交。 |
| `await sdk.game.finish({requestId})` | 仅用于运行中的无规则自由单人局。有规则时通过动作进入终态自动结算。 |
| `await sdk.progress.read()` | 返回 `series,saveVersion,rank,milestoneId,privateSave,privateRevision` 等。用 `privateSave` 载入本人设置，`rank` 是平台已确认的最高里程碑。 |
| `await sdk.progress.writePrivate(values, opts)` | 已声明 `save.private` 时可写。`values` 是全部私档字段。`opts` 为 `{requestId, expectedRevision: progress.privateRevision}`。本机和预览的成功结果含 `privateRevision`，平台返回更新后的进度信息。两边都可再次调用 `progress.read()` 取得完整私档。切勿把对局的 `snapshot.revision` 用于私档写入。 |
| `sdk.progress.onFlush(handler)` | 有内存草稿时注册一个函数。宿主切换或离开前，函数返回完整待保存的私档对象，或在没有修改时返回 `null`。回调期间 SDK 已暂停新写入，直接返回值。注册返回解绑函数。 |
| `await sdk.room.members()`、`await sdk.chat.open()` | 前者返回当前局成员数组，成员有 `playerId,displayName,seat,status`。平台有房间且声明 `room.chat` 时，后者由宿主打开聊天，返回 `{opened:true}`。本机、预览和无可用房间时返回 `{opened:false}`。聊天内容和发送权限归宿主。 |
| `await sdk.history.list({cursor,limit})` | 当前玩家当前游戏的历史，分页结果含 `items,nextCursor`。每页最多 20 条。首次可传 `{limit:10}`，下一页沿用平台返回的游标。 |
| `sdk.on(event,handler)`、`sdk.dispose()` | 关注 `session.changed`、`game.changed`、`room.changed`、`progress.changed`、`audio.changed` 和 `session.ended`。事件参数在模拟和平台环境中可以不同。将游戏变化和结算事件作为刷新信号，重新读快照中的 `state` 和 `outcome`。静音消息读取 `muted`。订阅返回解绑函数。离开时释放业务定时器、监听器及音频句柄，SDK 会在 `pagehide` 断开桥。 |
| `sdk.local.export()`、`sdk.local.import(data)` | 仅在顶层离线运行、且 `ready()` 成功后同步调用，用于用户主动导出、导入本机存档。离线存档与平台云存档相互独立。 |

**动作与存档的冲突处理**

1. 玩家点击时先检查回合和 `phase`，读取当前对局修订号，固定这次动作、修订号和请求 ID。在请求完成前暂停同一按钮。按下棋盘第 1 行第 1 列时，例如提交 `sdk.game.intent({type:"place",payload:{x:0,y:0}},{expectedRevision:snapshot.revision,requestId})`。本地同机多人要按当前席位轮换，不能始终限制为离线名片的第一席。
2. 收到成功回执后重读快照。遇到 `RESULT_UNKNOWN` 时保留原动作、原修订号、原请求 ID，核对最新状态，只用相同参数重试这次操作。`STALE_REVISION` 表示已有新状态，重读快照或私档，重新评估输入，新操作使用新 ID。`WRONG_TURN`、`CELL_OCCUPIED`、`ILLEGAL_MOVE` 应提示玩家并重绘。
3. 私档更新应先读当前值，例如 `const p = await sdk.progress.read()`，再以 `{...p.privateSave, sound:false}` 合并设置，以 `p.privateRevision` 提交。`sound` 必须是自己契约中已声明的字段。多个标签页先后写入时，应读回并合并仍有意义的本地修改。共同关卡、他人私档与整段历史均不属于 `writePrivate` 的参数。
4. 遇到断网、重新连接或 `TRANSITION_PENDING`，暂停写入并保留未确认的草稿。恢复后读取最新快照和私档。不要用前端倒计时裁决开局或胜负，也不要逐帧向平台写位置、进度或心跳。用 Canvas 动画或定时器做的画面效果留在游戏内。

## 6. 打包并核对上传结果

**安装与打包**

解压完整开发资料包，从含 `AI-GAME-SPEC.md` 和 `requirements-author.txt` 的根目录执行下列命令。使用 Python 3.12 或更高版本，建议 3.13。第一条安装依赖，准备好依赖后，打包和校验都可断网进行。游戏源码单独放在 `my-game/`，其它文件保留原目录。打包器会复制发行 SDK、重建离线契约、生成 ZIP 并实际校验。最后一条复核交付字节。输出 `staticValidation: passed` 表示静态检查合格。

```shell
python -m pip install -r requirements-author.txt
python tools/minigame_pack.py pack ./my-game ./even-number-practice-1.0.0.zip
python tools/minigame_pack.py validate ./even-number-practice-1.0.0.zip
```

**检查最终 ZIP**

资料中的四份 schema 在 `docs/minigames/contracts/`，三个示例在 `docs/minigames/downloads/`。也可解压一个示例到 `my-game/` 后改写，再执行上面的命令。工具默认保护已有输出文件，请给新包使用新文件名。用解压工具重新打开交付 ZIP，检查实际目录、脚本顺序，在新目录断网双击。有 Python 但缺少工具依赖时，可复制原版 SDK，用第 3 节的标准库代码生成离线描述，再手工打 ZIP。没有 Python 时，可交给能使用打包工具的人，或用本地工具从实际 JSON 序列化同等内容并检查固定赋值格式。交付说明应标出尚待运行的校验项目。

**教师上传**

教师从“小游戏管理 → 上传新游戏”提交 ZIP，查看版本任务。若被拒绝，使用错误中的 `file` 和 `field` 定位文件和字段，整改后提交新包。启用了分步上线的站点会将静态通过的包保留在“待浏览器验收”。重复提交同一包不能替代浏览器验收。

**试玩与发布**

浏览器验收在平台沙盒和离线环境中检查 SDK 握手、脚本错误、越界资源请求和素材解码。通过后，教师应在隔离预览中实际完成试玩、登记该版本结果、设置可见范围和修为范围，再发布。只有符合可见条件的玩家才能在大厅看到已发布游戏。开发者的本机试玩、自动静态通过、教师最终发布是三项不同的验收结论。

| 常见现象或错误码 | 先检查哪一步 |
| --- | --- |
| `FILE_TYPE`、`DUPLICATE_PATH`、`EXPANDED_QUOTA` | 解压最终 ZIP 看层级、重复文件名、残留依赖、单文件与展开容量。 |
| `SDK_DIGEST`、`SCRIPT_ORDER`、`OFFLINE_CONTRACTS` | 重新二进制复制发行 SDK。核对三个外置脚本顺序，从当前清单和契约重新生成离线 JS。 |
| `INVALID_CONTRACT`、`MISSING_RESOURCE`、`SAVE_CAPABILITY` | 对照报错字段检查大小写、必填项、值类型、路径及实际文件。私档声明要与能力项对应。 |
| `RULE_UNREACHABLE`、`RULE_DEAD_END`、`MILESTONE_SKIP`、`E_RULES_IDLE_SEAT` | 从首状态实际走一遍每条分支，画出到终点的路径。按最大人数轮换，检查是否有人全局没有出手机会。 |
| `HOST_UNAVAILABLE`、`MISSING_OFFLINE_CONTRACT` | 本机测试用解压后的顶层 `file:` 打开。平台模式从大厅或教师预览启动。核对离线描述、脚本加载和控制台报错。 |
| `INVALID_PRIVATE_SAVE`、`SAVE_INCOMPATIBLE` | 用最新字段定义核对完整私档、默认值、边界和序列化大小。发布版本升级时核对存档谱系。 |
| `VERSION_NOT_READY`、`PLAYTEST_REQUIRED` | 检查该版本的浏览器验收和教师试玩登记。静态检查通过后保留同一待验收作业即可。 |

## 7. 教师与 AI 的交付单

| 实测任务 | 可验收的结果 |
| --- | --- |
| 在新目录、断网、直接打开解压后的 `main.html` | 姓名和题面显示，能开始、操作和结算。需要保存时能恢复个人设置。同机多席位能轮流操作至终局。 |
| 在窄屏和桌面各完成一次完整操作 | 按钮可点击、键盘可达，关键画面和提示不被遮住。声音受静音开关控制，离开后不残留播放。 |
| 有平台验收环境时，分别以两个账号验证已声明的多人能力 | 选定人数可建队、四位码能加入，随机匹配仅匹配所声明人数，满员五秒后进入同局。每个席位能按规则操作，双方看到同一结果。未声明的模式在验收表中标明“不适用”。 |
| 断线、重复点击、切换模式、关闭再开 | 重试同一动作不重复落子或结算。恢复后读取权威状态。私档各自保留，共同关卡只向前推进。 |
| 交付最终 ZIP 与结果说明 | 说明游戏标识、版本、支持人数、使用的规则引擎、存档字段和每项实测结果。需平台、其他设备或教师确认的项目明确写“待验收”，附复现步骤。 |

**教师可复制以下文字，连同开发资料包发给 AI。**

请按随附《LanShare 小游戏开发规范》制作【在此填写教学目标和玩法】。先写明选用的参考游戏、规则引擎、单人或多人人数、离线方式、胜负条件、共同里程碑和私档字段，再开始编码。请打开随附示例和 schema 核对字段，使用资料中的原版 SDK。把全部运行依赖放进游戏 ZIP，从最终契约重新生成离线描述。游戏界面要能完成一整局，平台的登录、组队与聊天交给宿主。完成后交付可上传的 ZIP 和第 7 节逐项实测结果。凡是当前环境没有实际验证的内容，标“待验收”。如需求超出现有两类联机规则，请先说明哪项动作无法表达，给出可执行的单人或回合制替代方案供教师确认。
