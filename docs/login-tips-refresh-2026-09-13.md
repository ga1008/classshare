# 学生与教师开屏内容更新（2026-09-13）

本次更新沿用现有按角色选句、主题配图、昼夜背景和最近阅读去重机制。新增提示围绕 AI 辅助学习与教学、信息核验、真实作品、具体反馈和个人数据保护；清理过时的固定金额、统一资格口径和缺乏依据的效果保证。具体增删以 `life_tip_seed_data.py` 的提交差异为准。

| 提示池 | 保留 | 新增或改写 | 下架 | 更新后 |
| --- | ---: | ---: | ---: | ---: |
| 学生 | 104 | 32 | 62 | 136 |
| 教师 | 4 | 23 | 12 | 27 |
| 合计 | 108 | 55 | 74 | 163 |

种子表按文本哈希计数；改写会产生一条新文案，同时下架对应旧句。`python -m unittest tests.test_life_tips -v` 通过 16 项测试，覆盖角色隔离、旧句退休、反馈保留、手工与公文内容隔离，以及管理员已下架状态不被重置。

浏览器验证 4/4 通过：学生、教师分别在桌面与 390px 手机宽度运行真实 Jinja 登录模板、JavaScript 模块和 CSS，确认新背景加载、欢迎文案完整可读、无横向溢出，键盘或触摸跳过后完成场景交棒。登录响应及目标首页使用本地模拟，不代表真实账号登录或小程序真机验收。配图清单 314 项均有对应文件且无重复。

## 背景图

新增两张原创校园背景，使用内置 imagegen，每张生成一次。通过现有 `tools/tips/compress_images.py` 中的 `compress_one` 压缩为 1600 × 900 WebP，内容哈希命名，并登记主题与关键词标签。

| 文件（相对于 static/img/life_tips） | 场景 | 大小 |
| --- | --- | --- |
| xueye-sunny-learning-commons-202609-21cb288c.webp | 日光学习共享空间 | 121,546 字节 |
| zhichang-bluehour-collaboration-202609-816486ba.webp | 傍晚校园协作教室 | 111,420 字节 |

从随机投放清单下架 12 张偏暗、重复度较高的旧场景：empty-dorm、night-laundry、snowy-busstop、neon-rain、overpass-storm、midnight-negotiation、snow-union-hall、storm-insurance、parking-mirror、shoe-shine、washroom-pause、command-room。原静态文件保留，以兼容已打开页面与缓存中的历史链接；当前投放清单为 314 张。

日光图生成提示：

> Use case: photorealistic-natural. Asset type: landscape background image for a modern Chinese university learning platform login and welcome screen, also center-cropped on mobile. Primary request: a bright contemporary campus learning commons in early autumn, tranquil welcoming and intellectually curious. Wide 16:9 architectural editorial photograph, a spacious warm oak shared worktable with one slim open laptop and a paper notebook in foreground, large floor-to-ceiling windows opening to green trees and a softly sunlit campus courtyard. Restrained warm ivory, leaf green, sky blue; natural daylight, realistic material detail, subtle film grain, generous uncluttered middle of frame for a centered glass login card. A believable modern learning environment, grounded rather than futuristic. No people, no robots, no holograms, no readable text or letters on screen or pages, no logo, no watermark, no UI or login card drawn in the image. The space must still feel pleasant when cropped to the central third.

傍晚图生成提示：

> Use case: photorealistic-natural. Asset type: wide 16:9 background photograph for a contemporary university teacher and student login welcome screen, center cropped on mobile. Primary request: a calm modern campus collaboration studio at blue hour; a light oak round worktable in the lower foreground with a slim laptop at one edge, closed notebooks, a small desk lamp; large glass windows look out over a tree-lined campus courtyard at dusk. Architectural editorial photography, inviting warm indoor lighting and soft blue outside, realistic wood and glass, clean gentle contrast rather than dark or dramatic. Quiet uncluttered center for a centered translucent welcome card, room depth and a few shelves toward edges. Feels thoughtful, human and current; usable center third on a phone. No people, no robots, no holograms, no floating data, no neon cyberpunk, no readable text, no logos, no watermark, no drawn interface or card.

## 数据与发布

不新增数据库结构或登录请求。沿用种子包按文本哈希幂等入库的行为：旧 seed 文案自动转为 retired，反馈记录保留，手工和公文来源内容不受影响。管理员已下架的现有文案保持下架。

发布使用干净提交和项目现有 PostgreSQL 验证门禁，部署前备份代码及数据库，发布后核对两端提示池、新图 URL、清单和服务健康。发布缓存标记只清理 HTTP 缓存，保留浏览器登录状态和作业草稿。

回退代码可恢复原配图清单；旧文案的 retired 状态不会因为代码回退自动恢复。如需回退提示投放，应依据发布前数据库备份逐条恢复本次变更的 seed 状态，保留管理员下架状态及反馈，不回滚整个业务数据库。
