"""Synthetic source records stay in isolated SQLite; never import to production."""
import sqlite3
import unittest
import io
import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_career_lifecycle import fixture
from classroom_app.services import career_path_service as career
from classroom_app.services import career_job_posting_service as jobs
from classroom_app.services.career_major_mapping_service import set_career_major_alias


def posting(**overrides):
    now=datetime.now(timezone.utc)
    return {"source":"isolated-test-source","external_id":"test-1","source_url":"https://example.invalid/jobs/1",
            "title":"翻译实践岗位","company":"隔离测试机构","city":"南宁","status":"open",
            "job_description":"岗位要求本科及以上学历，负责翻译与文案整理，熟悉英语，能够独立完成内容校对和资料维护。",
            "checked_at":(now-timedelta(hours=1)).isoformat(),"expires_at":(now+timedelta(days=7)).isoformat(),**overrides}


class PostingTests(unittest.TestCase):
    def setUp(self):
        self.conn=fixture()
    def tearDown(self):
        self.conn.close();jobs._match.cache_clear()

    def test_empty_source_never_fabricates_live_vacancies(self):
        result=jobs.list_job_postings(self.conn,1)
        self.assertEqual(result["items"],[])
        self.assertEqual(result["empty_reason"],"no_verified_source")

    def test_required_provenance_and_dates(self):
        for changes in ({"source":""},{"external_id":""},{"source_url":"javascript:alert(1)"},
                        {"source_url":"https://u:password@example.invalid/j"},{"checked_at":"2026-01-01"}):
            with self.assertRaises(ValueError):jobs.upsert_job_posting(self.conn,posting(**changes))

    def test_upsert_is_idempotent_and_changed_jd_retains_history(self):
        raw=posting();first=jobs.upsert_job_posting(self.conn,raw);same=jobs.upsert_job_posting(self.conn,raw)
        self.assertEqual(first,same)
        changed=jobs.upsert_job_posting(self.conn,{**raw,"job_description":raw["job_description"]+"另需SQL数据整理能力。"})
        self.assertEqual(changed["id"],first["id"])
        self.assertEqual(changed["version"],2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_job_posting_versions").fetchone()[0],2)

    def test_expired_closed_unscoped_and_sourceless_rows_are_excluded(self):
        for index,changes in enumerate(({}, {"school_code":"other"},{"status":"closed"})):
            jobs.upsert_job_posting(self.conn,posting(external_id=f"row-{index}",**changes))
        expired=jobs.upsert_job_posting(self.conn,posting(external_id="expired"))
        self.conn.execute("UPDATE career_job_postings SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(expired["id"],))
        corrupt=jobs.upsert_job_posting(self.conn,posting(external_id="corrupt"))
        self.conn.execute("UPDATE career_job_postings SET source='' WHERE id=?",(corrupt["id"],))
        result=jobs.list_job_postings(self.conn,1)
        self.assertEqual(len(result["items"]),1)
        with self.assertRaises(LookupError):jobs.create_posting_target(self.conn,1,expired["id"])

    def test_city_and_qualification_filter_use_explicit_facts(self):
        jobs.upsert_job_posting(self.conn,posting())
        jobs.upsert_job_posting(self.conn,posting(external_id="shenzhen",city="深圳"))
        self.conn.execute("INSERT INTO resume_educations(student_id,degree,major,end_date) VALUES(1,'专科','英语','2025-06')")
        state=career.initialize_career(self.conn,1)
        career.update_career_preferences(self.conn,1,{"cities":["南宁"]},revision=state["revision"])
        result=jobs.list_job_postings(self.conn,1)
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["items"][0]["match"]["qualification_state"],"known_gap")
        filtered=jobs.list_job_postings(self.conn,1,qualification="no_known_gaps")
        self.assertEqual(filtered["items"],[])
        self.assertEqual(filtered["filtered_on_page"],1)
        other=jobs.list_job_postings(self.conn,2,city="南宁",qualification="no_known_gaps")
        self.assertEqual(other["items"][0]["match"]["qualification_state"],"unknown")
        self.assertEqual(jobs.list_job_postings(self.conn,1,keyword="翻译")["total"],1)
        self.assertEqual(jobs.list_job_postings(self.conn,1,keyword="%")["total"],0)

    def test_vacancy_get_has_no_writes_or_ddl_and_is_bounded(self):
        jobs.upsert_job_posting(self.conn,posting());self.conn.commit()
        deny={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_CREATE_TABLE,
              sqlite3.SQLITE_CREATE_INDEX,sqlite3.SQLITE_ALTER_TABLE}
        self.conn.set_authorizer(lambda action,*args:sqlite3.SQLITE_DENY if action in deny else sqlite3.SQLITE_OK)
        try:
            result=jobs.list_job_postings(self.conn,1,page_size=1)
            self.assertEqual(len(result["items"]),1)
        finally:self.conn.set_authorizer(None)

    def test_future_certificate_date_is_preserved_and_never_confirmed(self):
        jobs.upsert_job_posting(self.conn,posting(job_description="岗位要求本科及以上学历，须持有高中教师资格证，负责课程准备、课堂教学与学习反馈。"))
        self.conn.execute("INSERT INTO resume_educations(student_id,degree,end_date) VALUES(1,'本科','2020-06')")
        self.conn.execute("INSERT INTO resume_certificates(student_id,name,acquired_date) VALUES(1,'高中教师资格证','2099-01')")
        self.assertEqual(jobs._bundle(self.conn,1)['certificate'][0]['acquired_date'],'2099-01')
        self.assertEqual(jobs.list_job_postings(self.conn,1,qualification='confirmed')['items'],[])
        uncertain=jobs.list_job_postings(self.conn,1,qualification='no_known_gaps')['items'][0]['match']
        self.assertEqual(uncertain['qualification_state'],'unknown')

    def test_unparsed_eligibility_blocks_confirmed_but_no_known_gaps_keeps_unknown_visible(self):
        jobs.upsert_job_posting(self.conn,posting(job_description="本科以上学历；2026年1月1日至2027年7月31日毕业；毕业后初次就业；CET-4 425分或IELTS 6.5分。负责资料整理与客户服务。"))
        self.conn.execute("INSERT INTO resume_educations(student_id,degree,end_date) VALUES(1,'本科','2020-06')")
        self.conn.execute("INSERT INTO resume_certificates(student_id,name,description,acquired_date) VALUES(1,'CET-4','成绩300分','2020-01')")
        self.assertEqual(jobs.list_job_postings(self.conn,1,qualification='confirmed')['items'],[])
        uncertain=jobs.list_job_postings(self.conn,1,qualification='no_known_gaps')['items'][0]['match']
        self.assertEqual(uncertain['qualification_state'],'unknown')
        self.assertFalse(uncertain['requirements_extraction_complete'])
        self.assertTrue(any(check['state']=='unknown' for check in uncertain['hard_requirements']))

    def test_target_handoff_is_owned_versioned_and_deduplicated(self):
        raw=posting();record=jobs.upsert_job_posting(self.conn,raw)
        first=jobs.create_posting_target(self.conn,1,record["id"])
        same=jobs.create_posting_target(self.conn,1,record["id"])
        other=jobs.create_posting_target(self.conn,2,record["id"])
        self.assertEqual(first["job_target_id"],same["job_target_id"])
        self.assertNotEqual(first["job_target_id"],other["job_target_id"])
        self.assertEqual(first["item"]["analysis"]["posting_source"]["version"],1)
        jobs.upsert_job_posting(self.conn,{**raw,"job_description":raw["job_description"]+"需教师资格证。"})
        revised=jobs.create_posting_target(self.conn,1,record["id"])
        self.assertNotEqual(revised["job_target_id"],first["job_target_id"])
        self.assertEqual(revised["item"]["analysis"]["posting_source"]["version"],2)

    def test_aliases_share_knowledge_identity_without_merging_pathway(self):
        set_career_major_alias(self.conn,school_code="audit",alias_name="English",canonical_name="英语",reason="校方确认的英文名称")
        self.conn.execute("UPDATE students SET academic_major='English（专升本）' WHERE id=2")
        first=career.resolve_student_context(self.conn,1);second=career.resolve_student_context(self.conn,2)
        self.assertEqual(first["major_id"],second["major_id"])
        self.assertEqual(first["major_key"],second["major_key"])
        self.assertEqual(second["timeline"]["program_pathway"],"top_up")
        self.assertEqual(second["major_identity_source"],"career_mapping")
        self.assertEqual(career.initialize_career(self.conn,1)["tasks"]["network"]["id"],
                         career.initialize_career(self.conn,2)["tasks"]["network"]["id"])
        with self.assertRaises(ValueError):
            set_career_major_alias(self.conn,school_code="audit",alias_name="英语",canonical_name="护理学",reason="错误合并应拒绝")

    def test_operator_cli_dry_run_rolls_back_and_apply_persists(self):
        from tools.career_catalog_import import main
        @contextmanager
        def connection():yield self.conn
        with tempfile.TemporaryDirectory(prefix="career-source-test-") as directory:
            path=Path(directory)/"reviewed.json"
            path.write_text(json.dumps({"postings":[posting()]},ensure_ascii=False),encoding="utf-8")
            with patch("classroom_app.database.get_db_connection",connection),patch("sys.stdout",new=io.StringIO()):
                with patch("sys.argv",["career_catalog_import",str(path)]):main()
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_job_postings").fetchone()[0],0)
                with patch("sys.argv",["career_catalog_import",str(path),"--apply"]):main()
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_job_postings").fetchone()[0],1)


if __name__=="__main__":unittest.main()
