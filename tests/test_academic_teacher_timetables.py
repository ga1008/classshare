"""Teacher lookup uses an in-memory database and synthetic upstream responses."""
import asyncio
import sqlite3
import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from classroom_app.db.schema_academic_teacher_timetables import ensure_academic_teacher_timetable_schema
from classroom_app.services import academic_teacher_timetable_service as svc


def fixture_teacher(no="000123", name="同名教师"):
    return {"teacher_no": no, "name": name, "college_name": "合成学院", "title": "讲师",
            "schedule_payload": {"kbList": [{"fixture": True}]},
            "unscheduled_courses": [{"course_name": "未排课程", "description": "未安排时间"}],
            "lessons": [{"course_name": "测试课程", "course_code": "C001", "weekday": 2,
                         "weeks": [1, 3, 5], "sections": [2, 3], "class_label": "合成班",
                         "classroom": "合成楼101", "teaching_class_name": "测试-001", "student_count": 30}]}


class TeacherTimetableTests(unittest.IsolatedAsyncioTestCase):
    def open_fixture(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def setUp(self):
        self.conn = self.open_fixture()
        self.addCleanup(self.conn.close)
        schema = """
            CREATE TABLE teachers(id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER DEFAULT 1,
                school_code TEXT, school_name TEXT, college TEXT, department TEXT,
                employee_number TEXT NOT NULL DEFAULT '');
            INSERT INTO teachers(id,name,is_active,school_code,school_name,college,department)
                VALUES(1,'查询人',1,'gxufl','合成学校','',''),
                (2,'关联人',1,'gxufl','合成学校','',''),(3,'其他学校',1,'other','其他学校','','');
            CREATE TABLE teacher_organization_memberships(id INTEGER PRIMARY KEY,teacher_id INTEGER,
                school_code TEXT,school_name TEXT,college TEXT,department TEXT,is_primary INTEGER,
                is_active INTEGER,updated_at TEXT,employee_number TEXT NOT NULL DEFAULT '');
            CREATE TABLE teacher_academic_system_credentials(id INTEGER PRIMARY KEY,teacher_id INTEGER,
                school_code TEXT,username TEXT,enabled INTEGER,last_status TEXT,last_verified_at TEXT);
            INSERT INTO teacher_academic_system_credentials VALUES(10,1,'gxufl','admin',1,'verified','2026-01-01'),
                (20,2,'gxufl','000123',1,'verified','2026-01-01');
            CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,name TEXT,school_code TEXT,
                start_date TEXT,end_date TEXT,week_count INTEGER);
            INSERT INTO academic_semesters VALUES(1,'2026-2027第一学期','gxufl','2026-09-07','2027-01-24',20),
                (2,'2026-2027第二学期','gxufl','2027-03-01','2027-07-11',20);
        """
        for statement in schema.split(";"):
            if statement.strip():
                self.conn.execute(statement)
        ensure_academic_teacher_timetable_schema(self.conn)
        self.conn.commit()
        @contextmanager
        def db():
            yield self.conn
        @asynccontextmanager
        async def opened(_credential):
            yield object(), SimpleNamespace(school_code="gxufl"), {}
        self.credential = {"credential_id": 10, "school_code": "gxufl", "username": "admin"}
        self.fetch = AsyncMock(return_value={"teachers": [fixture_teacher(), fixture_teacher("000456")], "options": {}})
        for p in (
            patch.object(svc, "get_db_connection", db),
            patch.object(svc, "load_teacher_academic_access_method", return_value=self.credential),
            patch.object(svc, "open_authenticated_academic_client", opened),
            patch.object(svc, "_fetch_teacher_timetables", self.fetch),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.query = {"year": "2026-2027", "term": "1", "q": "同名教师"}

    async def test_complete_same_name_query_cached_and_forced_sync(self):
        first = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("academic", first["source"])
        self.assertEqual(["000123", "000456"], [t["teacher_no"] for t in first["items"]])
        self.assertEqual(("linked", 2), (first["items"][0]["account_status"], first["items"][0]["linked_teacher_id"]))
        second = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("local", second["source"])
        self.assertEqual(1, self.fetch.await_count)
        await svc.query_teacher_timetables(1, self.query, force_sync=True)
        self.assertEqual(2, self.fetch.await_count)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM academic_teacher_timetables").fetchone()[0])

    async def test_query_coverage_is_not_inferred_from_one_cached_same_name(self):
        self.fetch.return_value = {"teachers": [fixture_teacher()]}
        await svc.query_teacher_timetables(1, {**self.query, "q": "000123"})
        self.fetch.return_value = {"teachers": [fixture_teacher(), fixture_teacher("000456")]}
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(2, result["pagination"]["total_count"])
        self.assertEqual(2, self.fetch.await_count)

    async def test_valid_empty_query_is_cached(self):
        self.fetch.return_value = {"teachers": []}
        self.assertEqual([], (await svc.query_teacher_timetables(1, self.query))["items"])
        self.assertEqual("local", (await svc.query_teacher_timetables(1, self.query))["source"])
        self.assertEqual(1, self.fetch.await_count)

    async def test_unscheduled_only_teacher_counts_known_classes_without_inventing_meetings(self):
        person = fixture_teacher()
        person["lessons"] = []
        person["unscheduled_courses"][0]["class_label"] = "合成班"
        self.fetch.return_value = {"teachers": [person]}
        result = await svc.query_teacher_timetables(1, self.query)
        stats = result["items"][0]["summary"]
        self.assertEqual((1, 1, 0, 0), (stats["course_count"], stats["class_count"],
                                        stats["lesson_count"], stats["total_hours"]))
        detail = svc.load_teacher_timetable(self.conn, 1, "000123", year="2026-2027", term="1")
        self.assertEqual("合成班", detail["unscheduled_courses"][0]["class_label"])

    async def test_failure_and_partial_snapshot_preserve_previous_data(self):
        await svc.query_teacher_timetables(1, self.query)
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")]
        bad = fixture_teacher("000456")
        bad["lessons"][0]["weeks"] = []
        self.fetch.return_value = {"teachers": [fixture_teacher(), bad]}
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.query_teacher_timetables(1, self.query, force_sync=True)
        self.assertEqual(502, error.exception.http_status)
        self.assertEqual(before, [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")])
        self.assertEqual("", self.conn.execute("SELECT lease_token FROM academic_teacher_query_leases").fetchone()[0])
        self.assertEqual("local", (await svc.query_teacher_timetables(1, self.query))["source"])

    async def test_separate_terms_and_detail_retains_unscheduled_and_full_lesson(self):
        await svc.query_teacher_timetables(1, self.query)
        await svc.query_teacher_timetables(1, {**self.query, "term": "2"})
        self.assertEqual(4, self.conn.execute("SELECT COUNT(*) FROM academic_teacher_timetables").fetchone()[0])
        detail = svc.load_teacher_timetable(self.conn, 1, "000123", year="2026-2027", term="1")
        self.assertEqual("未排课程", detail["unscheduled_courses"][0]["course_name"])
        self.assertEqual(2, detail["teacher"]["summary"]["course_count"])
        self.assertEqual(3, detail["teacher"]["summary"]["lesson_count"])
        week = detail["overview"]["weeks"][0]
        lesson = week["lessons"][0]
        self.assertEqual("合成班", detail["lessons"][lesson["id"]]["class_label"])
        self.assertEqual("", lesson["classroom_url"])
        self.assertEqual((2, [2, 3]), (lesson["weekday"], lesson["sections"]))

    async def test_other_school_and_revoked_membership_cannot_read_cache(self):
        await svc.query_teacher_timetables(1, self.query)
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.query_teacher_timetables(3, self.query)
        self.assertEqual(403, error.exception.http_status)
        self.conn.execute("INSERT INTO teacher_organization_memberships VALUES(1,1,'gxufl','合成','','',1,0,'2026','')")
        with self.assertRaises(svc.TimetableQueryError):
            svc.load_teacher_timetable(self.conn, 1, "000123", year="2026-2027", term="1")

    async def test_secondary_school_membership_and_ambiguous_accounts(self):
        self.conn.execute("INSERT INTO teacher_organization_memberships VALUES(1,3,'gxufl','合成','','',0,1,'2026','')")
        self.conn.execute("INSERT INTO teacher_academic_system_credentials VALUES(30,3,'gxufl','000123',1,'verified','2026')")
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("ambiguous", result["items"][0]["account_status"])
        self.assertIsNone(result["items"][0]["linked_teacher_id"])
        self.assertEqual("local", (await svc.query_teacher_timetables(3, self.query))["source"])
        self.conn.execute("UPDATE teacher_academic_system_credentials SET enabled=0 WHERE id=30")
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(2, result["items"][0]["linked_teacher_id"])

    async def test_employee_number_links_during_sync_without_academic_credentials(self):
        self.conn.execute("DELETE FROM teacher_academic_system_credentials WHERE teacher_id=2")
        self.conn.execute(
            "INSERT INTO teacher_organization_memberships VALUES(1,2,'gxufl','合成','','',1,1,'2026','000123')"
        )
        result = await svc.query_teacher_timetables(1, self.query)
        linked = result["items"][0]
        self.assertEqual(("linked", 2, "employee_number"),
                         (linked["account_status"], linked["linked_teacher_id"], linked["account_link_source"]))
        self.assertEqual("unlinked", result["items"][1]["account_status"])

    async def test_profile_saved_after_sync_links_cached_queries_and_detail_immediately(self):
        self.conn.execute("DELETE FROM teacher_academic_system_credentials WHERE teacher_id=2")
        self.conn.commit()
        before = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("unlinked", before["items"][0]["account_status"])
        self.conn.execute("UPDATE teachers SET employee_number='000123' WHERE id=2")
        self.conn.commit()
        after = await svc.query_teacher_timetables(1, {**self.query, "local_only": True})
        self.assertEqual(("local", 2), (after["source"], after["items"][0]["linked_teacher_id"]))
        detail = svc.load_teacher_timetable(self.conn, 1, "000123", year="2026-2027", term="1")
        self.assertEqual(2, detail["teacher"]["linked_teacher_id"])
        self.assertEqual(1, self.fetch.await_count)

    async def test_changed_employee_number_supersedes_old_verified_credential_and_cached_link(self):
        self.conn.execute(
            "INSERT INTO teacher_organization_memberships VALUES(1,2,'gxufl','合成','','',1,1,'2026','000123')"
        )
        await svc.query_teacher_timetables(1, self.query)
        self.conn.execute(
            "UPDATE teacher_organization_memberships SET employee_number='000456' WHERE teacher_id=2"
        )
        self.conn.commit()
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("unlinked", result["items"][0]["account_status"])
        self.assertEqual(2, result["items"][1]["linked_teacher_id"])
        self.assertEqual(1, self.fetch.await_count)

    async def test_revoked_membership_and_disabled_teacher_remove_employee_link(self):
        self.conn.execute("UPDATE teachers SET employee_number='000123' WHERE id=2")
        self.conn.execute(
            "INSERT INTO teacher_organization_memberships VALUES(1,2,'gxufl','合成','','',1,1,'2026','000123')"
        )
        await svc.query_teacher_timetables(1, self.query)
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=0 WHERE teacher_id=2")
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("unlinked", result["items"][0]["account_status"])
        self.conn.execute("UPDATE teacher_organization_memberships SET is_active=1 WHERE teacher_id=2")
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=2")
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("unlinked", result["items"][0]["account_status"])

    async def test_school_and_leading_zero_identity_never_inferred_from_name_or_primary_projection(self):
        self.conn.execute("DELETE FROM teacher_academic_system_credentials WHERE teacher_id=2")
        self.conn.execute("UPDATE teachers SET employee_number='000123' WHERE id=3")
        self.conn.execute(
            "INSERT INTO teacher_organization_memberships VALUES(1,3,'gxufl','合成','','',0,1,'2026','123')"
        )
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertTrue(all(t["account_status"] == "unlinked" for t in result["items"]))
        self.conn.execute("UPDATE teacher_organization_memberships SET employee_number='000123' WHERE teacher_id=3")
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(3, result["items"][0]["linked_teacher_id"])

    async def test_explicit_employee_conflict_with_legacy_credential_never_chooses_an_account(self):
        self.conn.execute(
            "INSERT INTO teacher_organization_memberships VALUES(1,3,'gxufl','合成','','',0,1,'2026','000123')"
        )
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual("ambiguous", result["items"][0]["account_status"])
        self.assertIsNone(result["items"][0]["linked_teacher_id"])
        self.assertEqual(0, result["summary"]["linked_count"])

    async def test_employee_identity_normalization_is_shared_and_preserves_source_number(self):
        self.conn.execute("DELETE FROM teacher_academic_system_credentials WHERE teacher_id=2")
        self.conn.execute("UPDATE teachers SET employee_number='AB001' WHERE id=2")
        self.fetch.return_value = {"teachers": [fixture_teacher("ａｂ００１")]}
        result = await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(2, result["items"][0]["linked_teacher_id"])
        self.assertEqual("ａｂ００１", result["items"][0]["teacher_no"])

    async def test_lease_contention_is_busy_and_does_not_fetch(self):
        key = svc._lease_key(self.credential)
        self.assertTrue(svc._claim(self.conn, key, "owner", svc._stamp()))
        self.conn.commit()
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(409, error.exception.http_status)
        self.fetch.assert_not_awaited()
        svc._release(self.conn, key, "not-owner")
        self.assertEqual("owner", self.conn.execute("SELECT lease_token FROM academic_teacher_query_leases").fetchone()[0])

    async def test_expired_lease_cannot_publish(self):
        async def expired(*args, **kwargs):
            self.conn.execute("UPDATE academic_teacher_query_leases SET expires_at='2000-01-01'")
            self.conn.commit()
            return {"teachers": [fixture_teacher()]}
        self.fetch.side_effect = expired
        with self.assertRaises(svc.TimetableQueryError):
            await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM academic_teacher_timetables").fetchone()[0])

    async def test_credential_changed_during_fetch_cannot_publish(self):
        async def changed(*args, **kwargs):
            self.conn.execute("UPDATE teacher_academic_system_credentials SET username='other' WHERE id=10")
            self.conn.commit()
            return {"teachers": [fixture_teacher()]}
        self.fetch.side_effect = changed
        with self.assertRaises(svc.TimetableQueryError):
            await svc.query_teacher_timetables(1, self.query)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM academic_teacher_timetables").fetchone()[0])

    async def test_transaction_failure_after_writes_rolls_back_every_cache_table(self):
        await svc.query_teacher_timetables(1, self.query)
        tables = ("academic_teacher_directory", "academic_teacher_timetables", "academic_teacher_query_cache")
        before = {t: [tuple(r) for r in self.conn.execute("SELECT * FROM " + t)] for t in tables}
        self.fetch.return_value = {"teachers": [fixture_teacher("000999", "新教师")]}
        original = svc._publish
        def fail_after_writes(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic failure before commit")
        with patch.object(svc, "_publish", fail_after_writes), self.assertRaises(svc.TimetableQueryError):
            await svc.query_teacher_timetables(1, self.query, force_sync=True)
        after = {t: [tuple(r) for r in self.conn.execute("SELECT * FROM " + t)] for t in tables}
        self.assertEqual(before, after)

    async def test_safe_upstream_business_error_remains_actionable(self):
        from classroom_app.services.academic_teacher_timetable_adapter import TeacherTimetableSourceError
        self.fetch.side_effect = TeacherTimetableSourceError("匹配教师超过20位，请缩小查询条件。")
        with self.assertRaises(svc.TimetableQueryError) as result:
            await svc.query_teacher_timetables(1, self.query)
        self.assertIn("缩小查询条件", str(result.exception))
        self.assertEqual(502, result.exception.http_status)

    async def test_cancelled_remote_request_releases_lease_and_preserves_cache(self):
        await svc.query_teacher_timetables(1, self.query)
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")]
        self.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await svc.query_teacher_timetables(1, self.query, force_sync=True)
        self.assertEqual("", self.conn.execute("SELECT lease_token FROM academic_teacher_query_leases").fetchone()[0])
        self.assertEqual(before, [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")])

    def test_default_uses_current_term_and_previous_term_during_holidays(self):
        for when, expected in [(date(2026, 10, 20), "1"), (date(2027, 2, 10), "1"),
                               (date(2027, 3, 1), "2"), (date(2027, 8, 10), "2")]:
            result = svc.build_teacher_timetable_options(self.conn, 1, today=when)
            self.assertEqual(("2026-2027", expected), (result["default_term"]["year"], result["default_term"]["term"]))
        self.fetch.assert_not_awaited()

    async def test_invalid_filters_do_not_query_upstream(self):
        for bad in [{"primary_only": "false"}, {"external": "yes"}, {"year": "nonsense"}, {"local_only": "true"},
                    {"q": "x" * 81}]:
            with self.subTest(bad=bad), self.assertRaises(svc.TimetableQueryError):
                await svc.query_teacher_timetables(1, {**self.query, **bad})
        self.fetch.assert_not_awaited()

    async def test_empty_search_is_local_without_credentials_and_sync_requires_keyword(self):
        with patch.object(svc, "load_teacher_academic_access_method") as credential:
            result = await svc.query_teacher_timetables(1, {**self.query, "q": "  "})
            self.assertEqual(("local", "local_browse", []), (result["source"], result["mode"], result["items"]))
            self.assertEqual(0, result["pagination"]["total_count"])
            with self.assertRaises(svc.TimetableQueryError) as error:
                await svc.query_teacher_timetables(1, {**self.query, "q": ""}, force_sync=True)
            self.assertEqual(400, error.exception.http_status)
            credential.assert_not_called()
        self.fetch.assert_not_awaited()
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM academic_teacher_query_leases").fetchone()[0])

    async def test_local_browse_has_stable_term_scope_pagination_and_page_statistics(self):
        self.fetch.return_value = {"teachers": [fixture_teacher(), fixture_teacher("000456"), fixture_teacher("000789")]}
        await svc.query_teacher_timetables(1, self.query)
        await svc.query_teacher_timetables(1, {**self.query, "term": "2"})
        self.conn.execute("UPDATE academic_teacher_timetables SET synced_at='2026-10-20T10:00:00' WHERE teacher_no<>'000123'")
        self.conn.execute("UPDATE academic_teacher_timetables SET synced_at='2026-10-19T10:00:00' WHERE teacher_no='000123'")
        # A list must not load/decode raw snapshots, even when they are large.
        self.conn.execute("UPDATE academic_teacher_timetables SET snapshot_json='not-list-data',lessons_json='not-list-data'")
        self.conn.commit()
        with patch.object(svc, "load_teacher_academic_access_method") as credential, patch.object(
            svc, "_account_links", wraps=svc._account_links,
        ) as links:
            query = {**self.query, "q": "", "page_size": 2, "college_id": "unrelated-opening-college", "campus_id": "old"}
            first = await svc.query_teacher_timetables(1, query)
            second = await svc.query_teacher_timetables(1, {**query, "page": 99})
            credential.assert_not_called()
            self.assertEqual(["000456", "000789"], links.call_args_list[0].args[1])
            self.assertEqual(["000123"], links.call_args_list[1].args[1])
        self.assertEqual(["000456", "000789"], [p["teacher_no"] for p in first["items"]])
        self.assertEqual(["000123"], [p["teacher_no"] for p in second["items"]])
        self.assertEqual({"page": 2, "page_size": 2, "total_count": 3, "total_page": 2}, second["pagination"])
        self.assertEqual({"teacher_count": 3, "linked_count": 0, "course_count": 4, "lesson_count": 6}, first["summary"])
        self.assertEqual(1, second["summary"]["linked_count"])
        self.assertEqual("page", first["summary_scope"])
        self.assertEqual(["campus_id", "college_id"], first["ignored_filters"])
        self.assertIn("未应用", first["message"])
        self.assertEqual("1", first["selected_term"]["term"])
        self.assertEqual(2, self.fetch.await_count)
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.query_teacher_timetables(3, query)
        self.assertEqual(403, error.exception.http_status)

    async def test_local_only_refresh_never_falls_back_to_remote(self):
        with patch.object(svc, "load_teacher_academic_access_method") as credential:
            missing = await svc.query_teacher_timetables(1, {**self.query, "local_only": True})
            self.assertEqual(("local_only", []), (missing["mode"], missing["items"]))
            credential.assert_not_called()
        self.fetch.assert_not_awaited()
        await svc.query_teacher_timetables(1, self.query)
        result = await svc.query_teacher_timetables(1, {**self.query, "local_only": True})
        self.assertEqual(("local", 2), (result["source"], len(result["items"])))
        self.assertEqual(1, self.fetch.await_count)
        with self.assertRaises(svc.TimetableQueryError):
            await svc.query_teacher_timetables(1, {**self.query, "local_only": True}, force_sync=True)

    async def test_exact_sync_updates_only_target_and_never_replaces_fuzzy_coverage(self):
        await svc.query_teacher_timetables(1, {**self.query, "q": "000123"})
        coverage = [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_query_cache")]
        other = tuple(self.conn.execute("SELECT * FROM academic_teacher_timetables WHERE teacher_no='000456'").fetchone())
        person = fixture_teacher()
        person["lessons"][0]["course_name"] = "更新课程"
        self.fetch.return_value = {"teachers": [person]}
        detail = await svc.sync_teacher_timetable(1, "000123", {**self.query, "q": "000456", "college_id": "outdated"})
        self.assertEqual("academic", detail["source"])
        self.assertEqual("000123", detail["teacher"]["teacher_no"])
        self.assertEqual("更新课程", detail["lessons"]["1"]["course_name"])
        self.assertEqual("未排课程", detail["unscheduled_courses"][0]["course_name"])
        self.assertEqual("1", detail["overview"]["selected_term"]["term"])
        self.assertEqual("000123", self.fetch.call_args.kwargs["exact_teacher_no"])
        self.assertEqual("000123", self.fetch.call_args.kwargs["filters"]["q"])
        self.assertEqual("", self.fetch.call_args.kwargs["filters"]["college_id"])
        self.assertEqual(coverage, [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_query_cache")])
        self.assertEqual(other, tuple(self.conn.execute("SELECT * FROM academic_teacher_timetables WHERE teacher_no='000456'").fetchone()))
        cached = await svc.query_teacher_timetables(1, {**self.query, "q": "000123", "local_only": True})
        self.assertEqual(2, len(cached["items"]))

    async def test_exact_sync_missing_wrong_or_partial_response_preserves_every_cache(self):
        await svc.query_teacher_timetables(1, self.query)
        tables = ("academic_teacher_directory", "academic_teacher_timetables", "academic_teacher_query_cache")
        before = {t: [tuple(r) for r in self.conn.execute("SELECT * FROM " + t)] for t in tables}
        invalid = fixture_teacher()
        invalid["lessons"][0]["weeks"] = []
        for teachers in ([], [fixture_teacher("000456")], [fixture_teacher(), fixture_teacher("000456")], [invalid]):
            with self.subTest(teachers=teachers):
                self.fetch.return_value = {"teachers": teachers}
                with self.assertRaises(svc.TimetableQueryError) as error:
                    await svc.sync_teacher_timetable(1, "000123", self.query)
                self.assertEqual(502, error.exception.http_status)
                self.assertEqual(before, {t: [tuple(r) for r in self.conn.execute("SELECT * FROM " + t)] for t in tables})
                self.assertEqual("", self.conn.execute("SELECT lease_token FROM academic_teacher_query_leases").fetchone()[0])

    async def test_exact_sync_uses_same_lease_scope_and_cancellation_cleanup(self):
        await svc.query_teacher_timetables(1, self.query)
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")]
        self.fetch.reset_mock()
        key = svc._lease_key(self.credential)
        self.assertTrue(svc._claim(self.conn, key, "query-owner", svc._stamp()))
        self.conn.commit()
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.sync_teacher_timetable(1, "000123", self.query)
        self.assertEqual(409, error.exception.http_status)
        self.fetch.assert_not_awaited()
        svc._release(self.conn, key, "query-owner")
        self.conn.commit()
        self.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await svc.sync_teacher_timetable(1, "000123", self.query)
        self.assertEqual("", self.conn.execute("SELECT lease_token FROM academic_teacher_query_leases").fetchone()[0])
        self.assertEqual(before, [tuple(r) for r in self.conn.execute("SELECT * FROM academic_teacher_timetables")])
        with self.assertRaises(svc.TimetableQueryError) as error:
            await svc.sync_teacher_timetable(3, "000123", self.query)
        self.assertEqual(403, error.exception.http_status)
        for payload in (None, {}, {"year": "2026-2027"}, {"year": "bad", "term": "1"}):
            with self.assertRaises(svc.TimetableQueryError):
                await svc.sync_teacher_timetable(1, "000123", payload)


if __name__ == "__main__":
    unittest.main()
