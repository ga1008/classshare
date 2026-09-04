# 课堂班群二维码

课堂首屏右侧显示班群二维码卡片，现有统计、成员与课程详情按钮位于其左侧；窄屏自动换行。未配置时，教师看到“点击设置”，学生看到“暂未设置”。两端使用原生 `dialog` 放大查看：宽屏以二维码预览和简介双栏展示，窄屏纵向排列；支持键盘焦点约束、Escape / 遮罩关闭和焦点返回。

本课堂任课教师可上传、更换或移除二维码、编辑简介，保存前可恢复原图或撤销选图。移除图片后简介仍可查看。学生只读，查看权限沿用课堂成员规则，包含合班内的在读学生；师生均可保存原图到设备。其他教师、其他班级和非在读学生不能读取配置或图片；学生提交写请求返回 403。

关闭含未保存修改的浮窗时提供“继续编辑 / 放弃修改”，离开页面使用浏览器原生提示。加载失败可原位重试；保存失败保留草稿。多页面版本冲突时读取最新设置，教师可采用最新设置或保留自己的修改后再次确认保存，未编辑字段自动使用最新值。选图会先预览解码，保存后首屏同步更新。

## 存储与接口

- 配置绑定 `class_offerings`，字段为 `group_qr_file_hash`、`group_qr_mime_type`、`group_qr_description` 和 `group_qr_revision`。SQLite 和 PostgreSQL 均在现有启动迁移中补齐默认空字段，请重启应用以应用迁移。
- `GET /api/classrooms/{class_offering_id}/group-qr`：返回 `image_url`、`description`、`revision`。
- 同路径 `POST`：multipart 表单，包含 `description`、当前 `revision`、可选 `file` 和 `remove_image`（默认 `false`）。省略图片时保留原图；`remove_image=true` 清空图片引用，与上传互斥；简介可清空。成功后返回新配置，版本冲突返回 409。
- `GET /api/classrooms/{class_offering_id}/group-qr/image`：每次验证课堂访问权，响应为 `private, no-store`，并使用校验得到的图片 MIME。加 `?download=true` 时以附件方式下载，文件扩展名由真实格式决定。
- 图片限静态 PNG/JPEG/WebP、5 MiB、1200 万像素；使用 Pillow 结构与完整解码验证，保留原图像素与边缘，沿用共享哈希存储的原子写入。简介限 1000 字，按纯文本显示；保存及读取统一 CRLF / CR 为 LF，长度按规范化后的内容计算。替换或移除图片不会直接删除共享哈希文件。
- 首屏由服务端提供初始信息，只在浮窗打开时重新读取，不增加轮询。同步路由在工作线程处理图片、文件及数据库操作，保存使用带版本条件的单条 UPDATE。

## 验证

```powershell
venv/Scripts/python.exe -m unittest discover -s tests -p test_classroom_group_qr.py -v
venv/Scripts/python.exe -m unittest discover -s tests -p test_db_postgres_schema.py -q
node tools/validate_classroom_group_qr.cjs
npm run test:e2e -- tests/e2e/specs/classroom-group-qr.spec.ts --project=chromium
```

后端测试覆盖授权与撤权、上传边界与损坏图片、下载格式、移除共享图片、并发版本冲突、失败回滚、迁移幂等，以及浏览器 multipart 换行往返与长度边界。

独立浏览器脚本使用真实首屏模板、JS、CSS 和二维码接口，数据库与文件隔离在 `.codex-temp`，退出时清理；覆盖上传、替换、移除与撤销、刷新、原位重试、未保存保护、多页面版本冲突、学生只读、键盘关闭和 320–1440 px 布局。可通过 `QR_SCREENSHOT_DIR` 指定截图目录。

完整页面 Playwright spec 使用已有 P03 隔离服务，真实登录后进入课堂，验证二维码操作及原有统计、成员、课程详情按钮，保留正常动画并检查桌面和窄屏布局、权限、下载及控制台错误。验收截图写入 `artifacts/classroom-group-qr`；P03 测试运行文件位于 `.codex-temp/p03-artifacts`。

部署后可在应用容器内验证真实 PostgreSQL 服务 SQL：

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres.yml exec -T app python tools/validate_classroom_group_qr_postgres.py
```

该脚本要求 PostgreSQL，只使用一条连接及一个最终回滚的事务；三张同名临时表以 `ON COMMIT DROP` 创建，`search_path` 排除 public，并在写入假数据前验证临时命名空间。图片存储定向到临时目录，退出时回滚、关闭连接并清理文件。覆盖上传、换行、版本冲突、师生及合班权限、移除和重新上传，不写入实际课堂或师生数据。
