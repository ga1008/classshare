# 统一说明浮窗

## 目标

说明浮窗用于承接“理解功能时才需要”的补充内容，让页面默认只保留任务、状态和操作。它不替代错误、风险、权限、提交结果或不可逆操作确认。

组件覆盖通用页面、教师管理中心、简历工作台及独立编辑页，因为这些页面都加载 `templates/partials/ui_system_assets.html`。

## 开发者入口

这是项目唯一的通用功能说明组件。新增或改造功能时，应优先调用这里的 Jinja、数据属性或 JavaScript 接口，不要复制浮窗 DOM、悬停计时、长按识别、定位逻辑或私有 tooltip 样式。

| 职责 | 文件 |
| --- | --- |
| Jinja 模板接口 | `templates/macros/ui_explanation.html` |
| 浏览器运行时与公开 API | `static/js/ui_explanation.js` |
| 共享视觉样式 | `static/css/ui-system.src.css` 中的 `.ui-explain-*` |
| 全局加载入口 | `templates/partials/ui_system_assets.html` |
| 导航说明字段 | `classroom_app/services/manage_nav_service.py` 中的 `ManageNavItem.help_text` |
| 契约与回归测试 | `tests/test_ui_explanation_system.py` |

最短接入路径：服务端模板使用 `explain_attrs` 或 `explain_button`；运行时生成的模块使用 `window.LanShareExplanation.attach(...)`；可复用说明先 `register(...)`，再用 `data-explain-id` 引用。

代码评审时应检查：是否保留必须持续可见的错误、风险和权限信息；普通点击与移动端滚动是否未受影响；说明媒体是否懒加载；是否误建第二套浮窗实现。

## 交互契约

- 桌面端：指针停留 `2000ms` 后打开；移入浮窗可继续阅读和点击链接。
- 键盘：触发元素获得焦点 `300ms` 后打开，`Escape` 关闭。
- 移动端：长按默认 `650ms` 打开；移动超过 `10px` 视为滚动并取消。
- 显式说明按钮可点击开关；普通链接和功能按钮的短按/单击行为不变。
- 浮窗按可用空间自动选择上、下、左、右，并始终与视口保持至少 `12px` 间距。
- 页面滚动和尺寸变化时，只在浮窗已打开期间按动画帧重新定位。

## Jinja 接口

### 给现有模块增加说明

```jinja2
{% from "macros/ui_explanation.html" import explain_attrs %}

<section {{ explain_attrs(
    '课堂材料',
    '管理课程文档，并把需要的内容分配到课堂。',
    links=[
        {'label': '材料库', 'href': '/manage/teaching/materials'},
        {'label': '开设课堂', 'href': '/manage/teaching/offerings'}
    ],
    placement='right'
) }}>
    ...
</section>
```

### 使用显式说明按钮

```jinja2
{% from "macros/ui_explanation.html" import explain_button %}

{{ explain_button(
    '考核计划表',
    '每学期每门课一份，支持生成、导入、预览和导出。',
    placement='bottom',
    label='考核计划表说明'
) }}
```

### 可用参数

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `title` | 简短标题，建议不超过 18 个汉字 | 空 |
| `text` | 必要说明，建议 40–160 字 | 空 |
| `links` | 最多 4 个 `{label, href}` 快速入口 | 空 |
| `media` | PNG/JPG/WebP/GIF 等演示资源 URL，首次打开时才加载 | 空 |
| `media_alt` | 演示图替代文字 | 自动生成 |
| `placement` | `auto/top/bottom/left/right` | `auto` |
| `delay` | 桌面悬停延迟，范围 250–5000ms | `2000` |
| `long_press` | 移动端长按延迟，范围 450–1500ms | `650` |
| `explanation_id` | 使用 JavaScript 注册表中的说明 ID | 空 |

## JavaScript 接口

模块导出并同时暴露 `window.LanShareExplanation`：

```js
import {
    attachExplanation,
    closeExplanation,
    openExplanation,
    registerExplanations,
} from '/static/js/ui_explanation.js';

registerExplanations({
    'course-materials': {
        title: '课堂材料',
        text: '管理课程文档并分配到课堂。',
        links: [{ label: '打开材料库', href: '/manage/teaching/materials' }],
    },
});

attachExplanation('[data-dynamic-module]', {
    title: '动态模块',
    text: '运行时创建的模块也无需单独绑定事件。',
});
```

公开方法：

- `register(entries)`：注册可复用的 ID → 配置映射。
- `attach(elementOrSelector, config)`：给动态元素附加配置。
- `open(elementOrSelector, override?)`：主动打开。
- `close()`：关闭当前说明。

## 数据属性接口

无需 Jinja 时可直接使用：

- `data-explain`
- `data-explain-id`
- `data-explain-title`
- `data-explain-text`
- `data-explain-links`（JSON 数组）
- `data-explain-media`
- `data-explain-media-alt`
- `data-explain-placement`
- `data-explain-delay`
- `data-explain-long-press`
- `data-explain-toggle`（把当前元素设为点击开关）

旧的 `data-lp-tip` 已由新组件兼容，迁移期间不会同时出现两套浮窗。

## 维护边界

- 导航说明使用 `ManageNavItem.help_text`；未设置时兼容回退到现有 `ai_hint`。
- 浮窗只接受纯文本、受限链接和安全的 HTTP(S) 媒体 URL，不接收任意 HTML。
- 外部链接自动使用 `noopener noreferrer`。
- GIF/图片在首次打开对应说明前不请求，避免隐藏内容占用带宽。
- 全页面只懒创建一个浮窗 DOM；使用全局事件委托，不为每个模块创建监听器，不使用轮询或 `MutationObserver`。
- 在 `prefers-reduced-motion` 下关闭过渡动画；深色主题使用同一内容结构。

## 不应放入浮窗的内容

- 表单校验错误、同步失败、保存结果和实时进度。
- 删除、覆盖、公开范围、成绩调整等不可逆或高风险操作确认。
- 文件格式、大小、截止时间等提交前必须持续可见的硬约束。
- 权限不足、账号未配置、数据缺失等阻断当前任务的信息。
