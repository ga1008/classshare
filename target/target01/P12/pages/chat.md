# P12 页面迁移工作单：chat

## 页面与业务边界

- 页面入口：`templates/classroom_main_v4.html`
- 主要旧脚本：
  - `static/js/chat.js`
  - `static/js/classroom_private_messages.js`
- 相关后端边界：
  - 聊天、课堂讨论、私信、附件上传、消息中心摘要相关 API。
  - 文件上传链路仍由 `classroom_app/routers/files.py` 提供。

## 旧脚本职责清单

- `static/js/chat.js` 当前承担：
  - 课堂讨论消息加载、刷新、发送、滚动、图片附件预览。
  - 聊天输入框、发送按钮、Markdown/表情/图片等交互。
  - 与课堂页面 DOM、`window.APP_CONFIG` 和全局 toast 工具耦合。
- `static/js/classroom_private_messages.js` 当前承担：
  - 课堂内私信联系人、会话、发送、刷新、附件和状态展示。
  - 与消息中心私信存在业务重叠，必须避免两个页面各自维护一套不兼容状态。

## P12 目标状态

- 新增聊天 page/feature island，建议命名：
  - `frontend/src/islands/classroom-chat.tsx`
  - 或拆为 `classroom-discussion.tsx` 与 `classroom-private-chat.tsx`。
- React 侧拥有聊天输入、消息列表、发送状态、附件预览、滚动状态和错误状态。
- 旧脚本只在迁移期作为兼容控制器存在；迁移完成后必须从 `classroom_main_v4.html` 删除旧 import 和 `new ClassroomChat(...)`。
- 课堂内私信与消息中心私信必须共享 API 语义，不允许出现：
  - 课堂显示已发送但消息中心看不到。
  - 消息中心已读但课堂仍显示未读。
  - 附件权限在两个入口表现不同。

## 迁移拆分顺序

1. 建立 `classroom-chat` island 挂载点，先只读取 `window.APP_CONFIG` 和现有 DOM 容器。
2. 迁移只读消息列表和空状态，保留旧发送逻辑。
3. 迁移发送消息、发送中、失败重试和冷却状态。
4. 迁移图片/文件附件选择和预览，复用现有上传 API，不改文件表结构。
5. 迁移滚动定位、新消息提示和未读状态。
6. 迁移课堂内私信，和消息中心私信建立统一的数据 normalizer。
7. 删除 `new ClassroomChat(...)`、`new ClassroomPrivateMessages(...)` 和对应旧 import。

## 可删除旧代码的硬性条件

- [ ] React island 已覆盖公开讨论发送和刷新。
- [ ] React island 已覆盖课堂内私信发送和刷新。
- [ ] 发送失败有可见错误状态，不吞错。
- [ ] 离开页面会清理 interval、timeout、事件监听和上传回调。
- [ ] 不重复请求聊天列表。
- [ ] 不重复绑定发送按钮。
- [ ] 不改聊天、私信、附件的线上数据结构。

## 测试与验收

- 单元 / 集成：
  - `npm run typecheck` 通过。
  - `npm run build` 通过。
  - 追加 API normalizer 单元测试，覆盖普通文本、图片附件、文件附件、失败消息、AI/系统消息。
- 浏览器回归：
  - 使用 `.codex-temp` 测试库。
  - 教师课堂页发送一条讨论消息，页面立刻显示发送中和完成状态。
  - 学生刷新课堂页能看到该讨论消息。
  - 学生发送带图片或文件的消息，教师能看到附件入口。
  - 教师发送课堂内私信，学生能在课堂私信或消息中心看到。
  - 控制台无重复 key、重复 listener、未处理 promise rejection。
- 数据安全：
  - 所有写入只进入测试数据根。
  - 不使用线上账号做发送消息、附件上传、清空会话或批量已读测试。

