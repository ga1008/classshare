"""LessonDoc 2.0 服务层单测:降级校验矩阵 / 渲染↔抽取往返 / 包骨架落库。

夹具约定:内存 sqlite + 手搓最小 course_materials 表;文件存储与引擎探测
均 monkeypatch,不落盘、不依赖真实 DB 配置。手搓 schema 场景必须 reset
`schema_course_doc_packs._SCHEMA_READY`(跨套件污染惯犯,见教学域守则)。
"""

import json
import sqlite3
import unittest
from unittest import mock

from classroom_app.db import schema_course_doc_packs
from classroom_app.services.html_package_service import extract_html_text
from classroom_app.services.lessondoc import (
    LessonDocValidationError,
    extract_embedded_json,
    is_lessondoc_html,
    render_home_html,
    render_lesson_html,
    validate_deck,
    validate_manifest,
)
from classroom_app.services.lessondoc import pack_service


def _deck(**overrides):
    base = {
        "spec": "lessondoc/2.0",
        "kind": "lesson",
        "lesson": 1,
        "course": "《测试课程》",
        "title": "第一课",
        "slides": [
            {"layout": "title"},
            {
                "layout": "content",
                "section": "开场",
                "title": "目标",
                "blocks": [{"type": "text", "md": "hello **world**"}],
            },
        ],
    }
    base.update(overrides)
    return base


def _manifest(**overrides):
    base = {
        "spec": "lessondoc/2.0",
        "kind": "home",
        "course": {"name": "测试课程", "totalHours": 8, "sessionCount": 4},
        "theme": "sky",
        "stages": [{"label": "阶段一", "lessons": [1, 2]}],
        "lessons": [
            {"n": 1, "title": "第一课", "status": "ready", "topics": ["a"]},
            {"n": 2, "title": "第二课", "status": "pending"},
        ],
    }
    base.update(overrides)
    return base


class TestValidateDeck(unittest.TestCase):
    def test_fatal_not_object(self):
        with self.assertRaises(LessonDocValidationError):
            validate_deck(["not", "a", "dict"])

    def test_fatal_bad_spec(self):
        with self.assertRaises(LessonDocValidationError):
            validate_deck(_deck(spec="lessondoc/9.9"))
        with self.assertRaises(LessonDocValidationError):
            validate_deck(_deck(spec=None))

    def test_fatal_empty_slides(self):
        with self.assertRaises(LessonDocValidationError):
            validate_deck(_deck(slides=[]))

    def test_fatal_lesson_mismatch(self):
        with self.assertRaises(LessonDocValidationError):
            validate_deck(_deck(lesson=3), expected_lesson=1)

    def test_missing_lesson_backfilled_from_expected(self):
        deck, warnings = validate_deck(_deck(lesson=None), expected_lesson=7)
        self.assertEqual(deck["lesson"], 7)
        self.assertTrue(any("补齐" in w for w in warnings))

    def test_unknown_block_becomes_placeholder(self):
        payload = _deck()
        payload["slides"][1]["blocks"].append({"type": "hologram", "x": 1})
        deck, warnings = validate_deck(payload)
        blocks = deck["slides"][1]["blocks"]
        self.assertEqual(blocks[-1]["type"], "callout")
        self.assertIn("hologram", blocks[-1]["md"])
        self.assertTrue(any("未知内容块类型" in w for w in warnings))

    def test_unknown_layout_falls_back_to_content(self):
        payload = _deck()
        payload["slides"][1]["layout"] = "hexagon"
        deck, warnings = validate_deck(payload)
        self.assertEqual(deck["slides"][1]["layout"], "content")
        self.assertTrue(any("未知版式" in w for w in warnings))

    def test_broken_block_dropped_not_fatal(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {"type": "quiz", "q": "", "options": []},          # 缺题干/选项 → 丢弃
            {"type": "text", "md": "survivor"},
        ]
        deck, warnings = validate_deck(payload)
        blocks = deck["slides"][1]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["md"], "survivor")
        self.assertTrue(any("quiz" in w for w in warnings))

    def test_stepper_stage_missing_type_inferred(self):
        """AI 漏写 stage.type 时按形状推断,舞台图不被占位卡吞掉。"""
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {
                "type": "stepper",
                "stage": {"viewBox": "0 0 100 50", "body": "<rect id='a' width='10' height='10'/>"},
                "steps": [{"text": "第一步", "show": ["a"]}],
            },
            {
                "type": "stepper",
                "stage": {"kind": "flow", "nodes": ["甲", "乙"]},
                "steps": [{"text": "第一步"}],
            },
        ]
        deck, warnings = validate_deck(payload)
        blocks = deck["slides"][1]["blocks"]
        self.assertEqual(blocks[0]["stage"]["type"], "svg")
        self.assertEqual(blocks[1]["stage"]["type"], "diagram")
        self.assertTrue(any("推断为 svg" in w for w in warnings))
        self.assertTrue(any("推断为 diagram" in w for w in warnings))

    def test_table_truncated_and_warned(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {"type": "table", "head": ["a"], "rows": [[str(i)] for i in range(20)]}
        ]
        deck, warnings = validate_deck(payload)
        self.assertEqual(len(deck["slides"][1]["blocks"][0]["rows"]), 12)
        self.assertTrue(any("截断" in w for w in warnings))

    def test_quiz_answer_repaired(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {
                "type": "quiz",
                "q": "1+1?",
                "options": [{"k": "A", "text": "2"}, {"k": "B", "text": "3"}],
                "answer": "Z",
                "explain": "x",
            }
        ]
        deck, warnings = validate_deck(payload)
        self.assertEqual(deck["slides"][1]["blocks"][0]["answer"], "A")
        self.assertTrue(any("答案不在选项" in w for w in warnings))

    def test_svg_sanitized(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {
                "type": "svg",
                "viewBox": "0 0 100 100",
                "body": "<rect fill='#0284c7' onclick=\"alert(1)\"/>"
                        "<script>evil()</script><text fill='#16a34a'>ok</text>",
            }
        ]
        deck, warnings = validate_deck(payload)
        body = deck["slides"][1]["blocks"][0]["body"]
        self.assertNotIn("<script", body)
        self.assertNotIn("onclick", body)
        self.assertNotIn("#0284c7", body)
        self.assertIn("var(--dg-", body)
        self.assertIn("var(--dg-ok)", body)
        self.assertTrue(any("硬编码颜色" in w for w in warnings))

    def test_svg_colors_mapped_by_luminance(self):
        """浅底与深字不能被兜底成同一个主色，否则手写包迁移后图会糊成一片。"""
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {
                "type": "svg",
                "viewBox": "0 0 100 100",
                "body": (
                    "<ellipse fill='#e0f2fe'/>"      # 浅蓝底衬
                    "<text fill='#075985'>深蓝字</text>"
                    "<rect fill='#0284c7'/>"         # 主色
                    "<rect fill='#fff'/>"            # 接近白
                    "<text fill='#64748b'>灰</text>"
                ),
            }
        ]
        deck, _ = validate_deck(payload)
        body = deck["slides"][1]["blocks"][0]["body"]
        self.assertIn("var(--dg-primary-soft)", body)   # #e0f2fe
        self.assertIn("var(--dg-primary-dark)", body)   # #075985
        self.assertIn("var(--dg-fill)", body)           # #fff
        self.assertIn("var(--dg-muted)", body)          # #64748b
        self.assertNotIn("#", body)

    def test_media_remote_src_dropped(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {"type": "media", "kind": "image", "src": "https://evil.example/x.png"},
            {"type": "media", "kind": "image", "src": "media/ok.png"},
        ]
        deck, warnings = validate_deck(payload)
        blocks = deck["slides"][1]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["src"], "media/ok.png")
        self.assertTrue(any("media 路径不合规" in w for w in warnings))

    def test_slides_over_limit_truncated(self):
        payload = _deck()
        filler = {"layout": "content", "title": "x", "blocks": [{"type": "text", "md": "y"}]}
        payload["slides"] = [dict(filler) for _ in range(50)]
        deck, warnings = validate_deck(payload)
        self.assertEqual(len(deck["slides"]), 40)
        self.assertTrue(any("超过" in w for w in warnings))

    def test_theme_normalized(self):
        deck, _ = validate_deck(_deck(theme="TEAL dark"))
        self.assertEqual(deck["theme"], "teal dark")
        deck2, warnings2 = validate_deck(_deck(theme="neon"))
        self.assertNotIn("theme", deck2)
        self.assertTrue(any("未知主题" in w for w in warnings2))


class TestValidateManifest(unittest.TestCase):
    def test_fatal_missing_course_name(self):
        with self.assertRaises(LessonDocValidationError):
            validate_manifest(_manifest(course={"credits": 2}))

    def test_lessons_deduped_and_sorted(self):
        manifest, warnings = validate_manifest(
            _manifest(
                lessons=[
                    {"n": 2, "title": "b"},
                    {"n": 1, "title": "a"},
                    {"n": 2, "title": "dup"},
                    {"n": 0, "title": "bad"},
                ]
            )
        )
        self.assertEqual([l["n"] for l in manifest["lessons"]], [1, 2])
        self.assertTrue(any("重复" in w or "无效" in w for w in warnings))

    def test_missing_stage_coverage_backfilled(self):
        manifest, warnings = validate_manifest(
            _manifest(stages=[{"label": "s1", "lessons": [1]}])
        )
        labels = [s["label"] for s in manifest["stages"]]
        self.assertIn("其他课次", labels)
        self.assertTrue(any("未被任何阶段覆盖" in w for w in warnings))

    def test_overlapping_stage_lessons_deduped_first_wins(self):
        manifest, warnings = validate_manifest(
            _manifest(stages=[{"label": "A", "lessons": [1, 2]}, {"label": "B", "lessons": [2, 1]}])
        )
        self.assertEqual([s["lessons"] for s in manifest["stages"]], [[1, 2]])
        self.assertTrue(any("已属于前面的阶段" in w for w in warnings))
        self.assertTrue(any("未覆盖任何有效课次,已丢弃" in w for w in warnings))


class TestRenderRoundtrip(unittest.TestCase):
    def test_lesson_roundtrip_lossless(self):
        deck, _ = validate_deck(_deck())
        html = render_lesson_html(deck)
        self.assertTrue(is_lessondoc_html(html))
        self.assertIn('data-doc-kind="lesson"', html)
        extracted = extract_embedded_json(html)
        self.assertEqual(extracted, deck)

    def test_home_roundtrip_lossless(self):
        manifest, _ = validate_manifest(_manifest())
        html = render_home_html(manifest)
        self.assertIn('data-doc-kind="home"', html)
        self.assertEqual(extract_embedded_json(html), manifest)

    def test_embedded_script_close_escaped(self):
        payload = _deck()
        payload["slides"][1]["blocks"] = [
            {"type": "code", "code": "print('</script>')"}
        ]
        deck, _ = validate_deck(payload)
        html = render_lesson_html(deck)
        # 内容中的 </script> 不能提前闭合数据节点
        self.assertEqual(extract_embedded_json(html)["slides"][1]["blocks"][0]["code"],
                         "print('</script>')")

    def test_extract_html_text_uses_lessondoc_branch(self):
        deck, _ = validate_deck(_deck())
        html = render_lesson_html(deck)
        text = extract_html_text(html)
        self.assertIn("hello **world**", text)
        self.assertIn("第一课", text)

    def test_extract_from_plain_html_unaffected(self):
        text = extract_html_text("<html><body><p>普通页面</p></body></html>")
        self.assertIn("普通页面", text)


class _PackFixture(unittest.TestCase):
    """内存 sqlite + monkeypatch 文件存储/引擎探测的包操作夹具."""

    def setUp(self):
        schema_course_doc_packs.reset_schema_ready_for_tests()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE course_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                parent_id INTEGER,
                root_id INTEGER,
                material_path TEXT NOT NULL,
                name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                mime_type TEXT DEFAULT '',
                preview_type TEXT DEFAULT '',
                ai_capability TEXT DEFAULT 'none',
                file_ext TEXT DEFAULT '',
                file_hash TEXT,
                file_size INTEGER DEFAULT 0,
                ai_parse_status TEXT DEFAULT 'idle',
                ai_optimize_status TEXT DEFAULT 'idle',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE session_material_generation_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_offering_id INTEGER NOT NULL DEFAULT 0,
                session_id INTEGER NOT NULL DEFAULT 0,
                teacher_id INTEGER NOT NULL,
                trigger_mode TEXT DEFAULT 'guided',
                status TEXT DEFAULT 'queued',
                document_type TEXT DEFAULT '',
                requirement_text TEXT DEFAULT '',
                request_payload_json TEXT DEFAULT '{}',
                result_payload_json TEXT,
                generated_material_id INTEGER,
                generated_material_path TEXT,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE class_offerings (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                home_learning_material_id INTEGER
            );
            CREATE TABLE class_offering_sessions (
                id INTEGER PRIMARY KEY,
                class_offering_id INTEGER NOT NULL,
                order_index INTEGER NOT NULL,
                learning_material_id INTEGER
            );
            """
        )
        self.blob_store = {}

        def fake_store(content: str):
            key = f"hash-{len(self.blob_store)}-{len(content)}"
            self.blob_store[key] = content
            return key, len(content.encode("utf-8"))

        def fake_load(conn, material_row):
            return self.blob_store.get(material_row["file_hash"] or "", "")

        patches = [
            mock.patch(
                "classroom_app.services.session_material_generation_service._store_markdown_bytes",
                side_effect=fake_store,
            ),
            mock.patch.object(pack_service, "_load_file_text", side_effect=fake_load),
            mock.patch.object(pack_service, "get_configured_db_engine", return_value="sqlite"),
            mock.patch.object(
                schema_course_doc_packs, "get_configured_db_engine", return_value="sqlite"
            ),
            mock.patch(
                "classroom_app.services.session_material_generation_service.get_configured_db_engine",
                return_value="sqlite",
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.conn.close)
        self.addCleanup(schema_course_doc_packs.reset_schema_ready_for_tests)

    def _create_pack(self):
        return pack_service.create_pack_skeleton(
            conn=self.conn,
            teacher_id=9,
            course_id=42,
            manifest=_manifest(),
            theme="teal",
        )


class TestPackSkeleton(_PackFixture):
    def test_skeleton_shape(self):
        result = self._create_pack()
        pack = result["pack"]
        self.assertEqual(pack["course_id"], 42)
        self.assertEqual(pack["theme"], "teal")
        root_id = result["root_material_id"]

        rows = self.conn.execute(
            "SELECT name, node_type, parent_id FROM course_materials ORDER BY id"
        ).fetchall()
        names = [r["name"] for r in rows]
        # 根目录 + assets + 6 引擎文件 + README + course.json + main.html
        self.assertIn("assets", names)
        self.assertIn("deck-engine.js", names)
        self.assertIn("themes.css", names)
        self.assertIn("README.md", names)
        self.assertIn("course.json", names)
        self.assertIn("main.html", names)
        root_row = self.conn.execute(
            "SELECT * FROM course_materials WHERE id = ?", (root_id,)
        ).fetchone()
        self.assertEqual(root_row["node_type"], "folder")
        self.assertEqual(root_row["root_id"], root_id)

        lessons = pack_service.list_pack_lessons(self.conn, pack["id"])
        self.assertEqual([l["lesson_no"] for l in lessons], [1, 2])
        self.assertTrue(all(l["gen_status"] == "pending" for l in lessons))

        # main.html 是合法 LessonDoc 壳且清单可反读
        home_row = self.conn.execute(
            "SELECT * FROM course_materials WHERE name = 'main.html'"
        ).fetchone()
        html = self.blob_store[home_row["file_hash"]]
        self.assertTrue(is_lessondoc_html(html))
        manifest = pack_service.read_manifest(self.conn, pack)
        self.assertEqual(manifest["course"]["name"], "测试课程")

    def test_duplicate_pack_name_rejected(self):
        self._create_pack()
        with self.assertRaises(pack_service.LessonDocPackError):
            self._create_pack()

    def test_write_lesson_files_create_and_overwrite(self):
        result = self._create_pack()
        pack = result["pack"]
        warnings = pack_service.write_lesson_files(self.conn, pack, 2, _deck(lesson=2))
        self.assertEqual(warnings, [])
        entry = self.conn.execute(
            "SELECT * FROM course_materials WHERE name = 'lesson_2.html'"
        ).fetchone()
        self.assertIsNotNone(entry)
        folder = self.conn.execute(
            "SELECT * FROM course_materials WHERE id = ?", (entry["parent_id"],)
        ).fetchone()
        self.assertEqual(folder["name"], "lesson_2")
        first_hash = entry["file_hash"]

        # 覆盖写不新增行
        pack_service.write_lesson_files(self.conn, pack, 2, _deck(lesson=2, title="改版"))
        rows = self.conn.execute(
            "SELECT * FROM course_materials WHERE name = 'lesson_2.html'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["file_hash"], first_hash)
        self.assertIn("改版", self.blob_store[rows[0]["file_hash"]])

    def test_lesson_state_upsert_and_status_guard(self):
        pack = self._create_pack()["pack"]
        pack_service.update_lesson_state(
            self.conn, pack_id=pack["id"], lesson_no=1, gen_status="ready",
            warnings=["w1"], last_task_id=77,
        )
        lesson = pack_service.list_pack_lessons(self.conn, pack["id"])[0]
        self.assertEqual(lesson["gen_status"], "ready")
        self.assertEqual(lesson["warnings"], ["w1"])
        self.assertEqual(lesson["last_task_id"], 77)
        with self.assertRaises(pack_service.LessonDocPackError):
            pack_service.update_lesson_state(
                self.conn, pack_id=pack["id"], lesson_no=1, gen_status="exploded"
            )

    def test_archive_on_material_delete(self):
        result = self._create_pack()
        pack = result["pack"]
        changed = pack_service.archive_pack_for_material(self.conn, result["root_material_id"])
        self.assertTrue(changed)
        self.assertEqual(pack_service.get_pack(self.conn, pack["id"])["status"], "archived")
        # 非包根材料不受影响
        self.assertFalse(pack_service.archive_pack_for_material(self.conn, 999999))

    def test_refresh_assets_counts(self):
        pack = self._create_pack()["pack"]
        updated = pack_service.refresh_pack_assets(self.conn, pack)
        self.assertEqual(updated, 6)

    def test_assets_outdated_flag_lifecycle(self):
        """R5 引擎版本治理：新包不过期 → 引擎升级(指纹漂移)后过期 → 刷新恢复。"""
        result = self._create_pack()
        root_id = result["root_material_id"]

        def outdated():
            items = [{"id": root_id, "node_type": "folder"}]
            pack_service.attach_pack_metadata(self.conn, items)
            return items[0]["lessondoc_pack"]["assets_outdated"]

        self.assertFalse(outdated())            # 建包时写入当前指纹
        self.conn.execute(
            "UPDATE course_doc_packs SET assets_fingerprint = 'stale' WHERE id = ?",
            (result["pack"]["id"],),
        )
        self.assertTrue(outdated())             # 指纹漂移 → 可更新
        pack_service.refresh_pack_assets(self.conn, result["pack"])
        self.assertFalse(outdated())            # 刷新引擎后恢复一致


class TestStaleLessonReclaim(_PackFixture):
    """服务重启后卡在 queued/running 的课次要能被回收，否则教师无路可走。"""

    def _age(self, pack_id, lesson_no, seconds):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(seconds=seconds)).isoformat()
        self.conn.execute(
            "UPDATE course_doc_pack_lessons SET updated_at = ? WHERE pack_id = ? AND lesson_no = ?",
            (old, pack_id, lesson_no))
        self.conn.commit()

    def test_stale_running_becomes_failed_and_regenerable(self):
        from classroom_app.services.lessondoc import generate as generate_module
        pack = self._create_pack()["pack"]
        pid = pack["id"]
        pack_service.update_lesson_state(self.conn, pack_id=pid, lesson_no=1, gen_status="running")
        pack_service.update_lesson_state(self.conn, pack_id=pid, lesson_no=2, gen_status="queued")
        pack_service.update_lesson_state(self.conn, pack_id=pid, lesson_no=3, gen_status="running")
        self._age(pid, 1, 3600)                 # 1 小时前 → 过期
        self._age(pid, 2, 3600)
        self._age(pid, 3, 60)                   # 1 分钟前 → 仍在跑，不能误杀

        reclaimed = pack_service.reclaim_stale_lessons(self.conn, pid)
        self.assertEqual(reclaimed, [1, 2])
        lessons = {l["lesson_no"]: l for l in pack_service.list_pack_lessons(self.conn, pid)}
        self.assertEqual(lessons[1]["gen_status"], "failed")
        self.assertEqual(lessons[2]["gen_status"], "failed")
        self.assertEqual(lessons[3]["gen_status"], "running")
        self.assertIn(pack_service.STALE_LESSON_WARNING, lessons[1]["warnings"])

        # 回收后单课生成不再被"already_running"去重拒绝
        task = generate_module.create_lessondoc_task(self.conn, pack=pack, lesson_no=1)
        self.assertFalse(task["already_running"])
        # 未过期的 running 仍被去重保护
        task3 = generate_module.create_lessondoc_task(self.conn, pack=pack, lesson_no=3)
        self.assertTrue(task3["already_running"])

    def test_create_task_reclaims_stale_itself(self):
        from classroom_app.services.lessondoc import generate as generate_module
        pack = self._create_pack()["pack"]
        pack_service.update_lesson_state(self.conn, pack_id=pack["id"], lesson_no=1, gen_status="running")
        self._age(pack["id"], 1, 3600)
        task = generate_module.create_lessondoc_task(self.conn, pack=pack, lesson_no=1)
        self.assertFalse(task["already_running"])
        self.assertEqual(task["status"], "queued")


class TestSlideRewrite(_PackFixture):
    """R2 单页重写：替换成功 / AI 包壳剥离 / 坏结果拒绝且原文件不动。"""

    def setUp(self):
        super().setUp()
        import contextlib
        from classroom_app.services.lessondoc import generate as generate_module

        self.generate = generate_module

        @contextlib.contextmanager
        def fake_conn():
            yield self.conn

        p = mock.patch("classroom_app.database.get_db_connection", fake_conn)
        p.start(); self.addCleanup(p.stop)

        result = self._create_pack()
        self.pack = result["pack"]
        pack_service.write_lesson_files(self.conn, self.pack, 1, _deck(lesson=1))

    def _run(self, ai_return, slide_no=2, hint="改得更生动"):
        import asyncio
        with mock.patch.object(self.generate, "_call_lessondoc_ai",
                               mock.AsyncMock(return_value=ai_return)):
            return asyncio.run(self.generate.rewrite_slide_with_ai(
                pack_id=self.pack["id"], lesson_no=1, slide_no=slide_no, user_hint=hint))

    def _current_deck(self):
        return self.generate._load_lesson_deck(self.conn, self.pack, 1)

    def test_unwrap_slide_payload_shapes(self):
        """AI 返回的常见包装形态都要能剥出目标页。"""
        unwrap = self.generate._unwrap_slide_payload
        page = {"layout": "content", "blocks": [{"type": "text", "md": "x"}]}
        other = {"layout": "title"}
        self.assertEqual(unwrap(page, slide_index=1), page)                       # 裸对象
        self.assertEqual(unwrap({"slide": page}, slide_index=1), page)            # 单键包装
        self.assertEqual(unwrap({"result": page}, slide_index=1), page)           # 任意键包装
        self.assertEqual(unwrap({"slides": [page]}, slide_index=1), page)         # 单元素数组
        self.assertEqual(unwrap({"slides": [other, page, other]}, slide_index=1), page)  # 整 deck 取对应页
        self.assertIsNone(unwrap({"nonsense": True}, slide_index=1))              # 垃圾
        self.assertIsNone(unwrap("text", slide_index=1))                          # 非 dict

    def test_rewrite_replaces_only_target_slide(self):
        before = self._current_deck()
        new_page = {"layout": "content", "section": "改", "title": "重写后的标题",
                    "blocks": [{"type": "text", "md": "重写后的内容"}]}
        result = self._run(new_page)
        after = self._current_deck()
        self.assertEqual(result["slide_no"], 2)
        self.assertEqual(len(after["slides"]), len(before["slides"]))   # 页数不变
        self.assertEqual(after["slides"][1]["title"], "重写后的标题")     # 目标页已换
        self.assertEqual(after["slides"][0], before["slides"][0])        # 其余页不动

    def test_wrapped_payload_unwrapped(self):
        page = {"layout": "content", "title": "剥壳页",
                "blocks": [{"type": "text", "md": "内容"}]}
        self._run({"slides": [page]})
        self.assertEqual(self._current_deck()["slides"][1]["title"], "剥壳页")

    def test_garbage_rejected_and_file_untouched(self):
        from fastapi import HTTPException
        before = self._current_deck()
        with self.assertRaises(HTTPException):
            self._run({"nonsense": True})
        self.assertEqual(self._current_deck(), before)

    def test_dropped_slide_rejected_and_file_untouched(self):
        from fastapi import HTTPException
        before = self._current_deck()
        # content 版式且无任何有效块 → validate 整页丢弃 → 页数变少 → 必须拒绝
        with self.assertRaises(HTTPException) as ctx:
            self._run({"layout": "content", "blocks": [{"type": "text", "md": ""}]})
        self.assertIn("原页面未改动", str(ctx.exception.detail))
        self.assertEqual(self._current_deck(), before)


class TestBatchRetry(_PackFixture):
    """R4 韧性：批量生成中单课失败自动重试一次；重试成功不再重试。"""

    def setUp(self):
        super().setUp()
        import contextlib
        from classroom_app.services.lessondoc import generate as generate_module

        self.generate = generate_module

        @contextlib.contextmanager
        def fake_conn():
            yield self.conn

        p = mock.patch("classroom_app.database.get_db_connection", fake_conn)
        p.start(); self.addCleanup(p.stop)
        self.pack = self._create_pack()["pack"]

    def _run_batch(self, statuses_per_call):
        """mock run_lessondoc_task：按调用序号把课次置为给定状态。"""
        import asyncio
        calls = []

        async def fake_run(pack_id, lesson_no, **kwargs):
            status = statuses_per_call[min(len(calls), len(statuses_per_call) - 1)]
            calls.append((lesson_no, status))
            pack_service.update_lesson_state(
                self.conn, pack_id=pack_id, lesson_no=lesson_no, gen_status=status)
            # 领取产生的 queued 任务清掉，模拟真实执行完毕
            self.conn.execute("UPDATE session_material_generation_tasks SET status='completed'")
            self.conn.commit()

        with mock.patch.object(self.generate, "run_lessondoc_task", side_effect=fake_run):
            asyncio.run(self.generate.run_lessondoc_batch(
                pack_id=self.pack["id"], lesson_nos=[1],
                teacher_id=self.pack["teacher_id"]))
        return calls

    def test_failed_lesson_retried_once_then_succeeds(self):
        calls = self._run_batch(["failed", "ready"])
        self.assertEqual([c[1] for c in calls], ["failed", "ready"])   # 恰好重试一次
        lesson = pack_service.list_pack_lessons(self.conn, self.pack["id"])[0]
        self.assertEqual(lesson["gen_status"], "ready")

    def test_persistent_failure_stops_after_one_retry(self):
        calls = self._run_batch(["failed", "failed"])
        self.assertEqual(len(calls), 2)                                # 不无限重试
        lesson = pack_service.list_pack_lessons(self.conn, self.pack["id"])[0]
        self.assertEqual(lesson["gen_status"], "failed")               # 留给断点续跑

    def test_ready_lesson_skipped_entirely(self):
        pack_service.update_lesson_state(
            self.conn, pack_id=self.pack["id"], lesson_no=1, gen_status="ready")
        calls = self._run_batch(["ready"])
        self.assertEqual(calls, [])                                    # 已就绪不重跑


class TestFindPackForOffering(_PackFixture):
    """课堂 → pack 反查(课堂页/课堂管理页共用)。"""

    def _bind_offering(self, *, home_material_id=None, session_material_id=None):
        self.conn.execute(
            "INSERT INTO class_offerings (id, teacher_id, home_learning_material_id) VALUES (77, 9, ?)",
            (home_material_id,),
        )
        self.conn.execute(
            "INSERT INTO class_offering_sessions (id, class_offering_id, order_index, learning_material_id)"
            " VALUES (701, 77, 1, ?)",
            (session_material_id,),
        )

    def _entry_id(self, name):
        row = self.conn.execute(
            "SELECT id FROM course_materials WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return int(row["id"]) if row else None

    def test_found_via_home_material(self):
        result = self._create_pack()
        pack = result["pack"]
        pack_service.write_lesson_files(self.conn, pack, 1, _deck(lesson=1))
        self._bind_offering(home_material_id=self._entry_id("main.html"))
        found = pack_service.find_pack_for_offering(self.conn, class_offering_id=77)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], pack["id"])

    def test_found_via_session_material(self):
        result = self._create_pack()
        pack = result["pack"]
        pack_service.write_lesson_files(self.conn, pack, 1, _deck(lesson=1))
        self._bind_offering(session_material_id=self._entry_id("lesson_1.html"))
        found = pack_service.find_pack_for_offering(self.conn, class_offering_id=77)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], pack["id"])

    def test_unbound_offering_returns_none(self):
        self._create_pack()
        self._bind_offering()
        self.assertIsNone(pack_service.find_pack_for_offering(self.conn, class_offering_id=77))

    def test_archived_pack_not_returned(self):
        result = self._create_pack()
        pack = result["pack"]
        pack_service.write_lesson_files(self.conn, pack, 1, _deck(lesson=1))
        self._bind_offering(home_material_id=self._entry_id("main.html"))
        pack_service.archive_pack_for_material(self.conn, result["root_material_id"])
        self.assertIsNone(pack_service.find_pack_for_offering(self.conn, class_offering_id=77))


if __name__ == "__main__":
    unittest.main()
