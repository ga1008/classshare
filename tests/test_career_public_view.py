"""Historical graphs stay auditable while the student view avoids market promises."""
import copy
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.services import career_lifecycle_service as lifecycle
from classroom_app.services import career_path_service as career
from classroom_app.services import career_public_view_service as public
from classroom_app.services.career_recommendation_service import baseline_network
from tests.test_career_lifecycle import fixture, answers_for


def legacy_graph():
    graph = career._validate_network_payload(baseline_network("英语"), "英语")
    graph["graduate_label"] = "保证就业、年薪60万"
    graph["intro"] = "未来三年招聘需求翻倍"
    graph["cats"][0]["desc"] = "市场需求增长50%"
    graph["nodes"][0].update(
        desc="年薪60万，三年晋升经理", reason="招聘需求翻倍", trend="年薪60万且不会被AI替代",
        branch="3年成为经理", rec=5,
        pre=["CET4", "Java17", "3年相关工作经验", "资格证有效期3年", "三年晋升经理"],
        know=["核验资格证有效期3年", "保证录用，年薪60万"],
        tl=[["0-1年", "经理", "年薪60万"], ["1-3年", "经理", "保证晋升"],
            ["3-5年", "总监", "年薪100万"], ["5-10年", "合伙人", "必达"]],
    )
    return graph


class CareerPublicProjectionTests(unittest.TestCase):
    def test_projection_preserves_ids_links_and_qualification_digits_without_mutating_raw(self):
        raw = legacy_graph(); before = copy.deepcopy(raw)
        projected = public.project_network_for_public(raw)
        self.assertEqual(raw, before)
        self.assertEqual(projected["links"], raw["links"])
        self.assertEqual([(n["tag"],n["direction_id"],n["cat"]) for n in projected["nodes"]],
                         [(n["tag"],n["direction_id"],n["cat"]) for n in raw["nodes"]])
        first = projected["nodes"][0]
        self.assertEqual(first["pre"], ["CET4", "Java17", "3年相关工作经验", "资格证有效期3年"])
        self.assertEqual(first["know"], ["核验资格证有效期3年"])
        self.assertEqual(first["rec"], 3)
        self.assertEqual([item[0] for item in first["tl"]], [item[0] for item in public.STAGES])
        self.assertNotRegex(json.dumps(projected,ensure_ascii=False), r"60万|招聘需求翻倍|0-1年|3-5年|保证录用|三年晋升经理")
        self.assertFalse(projected["market_data_verified"])

    def test_maintained_duties_and_explicit_evidence_advice_remain(self):
        raw = career._seed_network_for("软件工程")
        projected = public.project_network_for_public(raw)
        self.assertEqual(projected["nodes"][0]["desc"],raw["nodes"][0]["desc"])
        original = {"summary":"已有2项翻译课程作品，可尝试记录项目复盘。",
                    "timeline_advice":"3年成为经理", "node_tips":{"A1":"CET4证书待核验"},
                    "top_paths":[{"tag":"A1","name":"翻译","why":"年薪60万"}]}
        result = public.project_personalized_advice(original)
        self.assertEqual(result["summary"],original["summary"])
        self.assertEqual(result["node_tips"],original["node_tips"])
        self.assertEqual(result["timeline_advice"],public.EXPLORATION_REASON)
        self.assertEqual(result["top_paths"][0]["tag"],"A1")
        self.assertEqual(original["timeline_advice"],"3年成为经理")
        self.assertEqual(public.project_personalized_advice({}),{})

    def test_generation_prompt_does_not_reintroduce_market_ranking_or_promotion_years(self):
        _, prompt = career.build_network_generation_prompt("护理学（专升本）")
        self.assertIn("rec 固定为3",prompt)
        self.assertIn("不承诺", public.project_network_for_public(legacy_graph())["intro"])
        self.assertNotRegex(prompt,r"市场需求×|薪资上限×|普通本科适配|0-1年|3-5年")
        self.assertIn("不假定普通本科身份",prompt)


class CareerHistoricalViewTests(unittest.TestCase):
    def setUp(self):
        self.conn=fixture()
        self.ctx=career.resolve_student_context(self.conn,1)

    def tearDown(self):
        self.conn.close()
        lifecycle._cached_recommend.cache_clear()
        lifecycle._validated_graph.cache_clear()

    def submit(self):
        state=career.initialize_career(self.conn,1)
        return career.save_test_and_generate(self.conn,self.ctx,answers_for(self.ctx),
                                            revision=state["revision"],enhance=False)["state"]

    def test_history_restore_and_cache_project_without_rewriting_sources_or_favorites(self):
        state=self.submit(); raw=legacy_graph(); first=raw["nodes"][0]
        career.record_career_feedback(self.conn,1,first["direction_id"],"saved",revision=state["revision"])
        row=self.conn.execute("SELECT * FROM career_major_networks").fetchone()
        stored=json.dumps(raw,ensure_ascii=False)
        sources='{"legacy_note":"unverified digest"}'
        self.conn.execute("UPDATE career_major_networks SET network_json=?,status='ready',revision=1,sources_json=? WHERE id=?",
                          (stored,sources,row["id"]))
        self.conn.execute("INSERT INTO career_network_versions(network_id,revision,network_json,sources_json,schema_version,created_at) VALUES(?,1,?,?,?,?)",
                          (row["id"],stored,sources,"career-network-v2","2026-01-01T00:00:00"))
        self.conn.commit()
        for _ in range(2):
            state=career.build_state(self.conn,1)
            self.assertEqual(state["feedback_by_tag"][first["tag"]],"saved")
            self.assertEqual(state["network"]["nodes"][0]["direction_id"],first["direction_id"])
            self.assertNotIn("60万",json.dumps(state,ensure_ascii=False))
        untouched=self.conn.execute("SELECT network_json,sources_json FROM career_major_networks").fetchone()
        self.assertEqual(tuple(untouched),(stored,sources))
        lifecycle.restore_network_version(self.conn,school_code="audit",major_key="英语",revision=1,reason="历史投影回归")
        restored=career.build_state(self.conn,1)
        self.assertEqual(restored["feedback_by_tag"][first["tag"]],"saved")
        self.assertEqual(restored["network"]["links"],raw["links"])
        self.assertNotIn("60万",json.dumps(restored,ensure_ascii=False))
        self.assertEqual(self.conn.execute("SELECT network_json FROM career_network_versions WHERE revision=1").fetchone()[0],stored)

    def test_public_view_version_invalidates_tokens_and_old_ai_without_get_writes(self):
        with patch.object(lifecycle,"PUBLIC_VIEW_VERSION","legacy-view"):
            self.submit()
            career.career_job_command(self.conn,1,target="personalization",action="retry")
            job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.PERSONALIZE_TASK_KIND,)).fetchone())
            self.assertTrue(lifecycle.apply_personalization_result(self.conn,job,json.loads(job["payload_json"]),{"summary":"年薪60万"}))
            old=career.build_state(self.conn,1)
        self.conn.commit(); before=self.conn.total_changes
        state=career.build_state(self.conn,1,known_result_version=old["result_version"])
        self.assertFalse(state.get("network_unchanged",False))
        self.assertNotEqual(state["result_version"],old["result_version"])
        self.assertEqual(state["recommendation_source"],"baseline")
        self.assertTrue(state["needs_refresh"])
        self.assertNotIn("60万",json.dumps(state,ensure_ascii=False))
        light=career.build_state(self.conn,1,known_result_version=state["result_version"])
        self.assertTrue(light["network_unchanged"])
        self.assertEqual(light["recommendation_source"],"baseline")
        self.assertTrue(light["needs_refresh"])
        self.assertEqual(self.conn.total_changes,before)
        self.assertIn("60万",self.conn.execute("SELECT personalized_json FROM career_student_sessions").fetchone()[0])
        self.assertFalse(lifecycle.apply_personalization_result(self.conn,job,json.loads(job["payload_json"]),{"summary":"outdated"}))


if __name__=="__main__":
    unittest.main()
