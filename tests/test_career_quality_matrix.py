"""77 synthetic majors with structural and counterfactual checks.

This is a review aid, not an employment expert's endorsement. Evidence is
deliberately synthetic to test input sensitivity and does not validate scoring
weights, labor-market facts, real credentials or hiring outcomes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from classroom_app.services import career_path_service as career
from classroom_app.services.career_recommendation_service import baseline_network, major_family, recommend, SCORER_VERSION
from classroom_app.services.career_public_view_service import project_network_for_public, PUBLIC_VIEW_VERSION

MAJORS = {
    "language": ["英语","商务英语","日语","泰语","越南语","法语","德语","西班牙语","翻译","外语教育","英语（商务方向）"],
    "business": ["工商管理","市场营销","人力资源管理","会计学","财务管理","金融学","国际经济与贸易","电子商务","物流管理","旅游管理","酒店管理"],
    "education": ["学前教育","小学教育","特殊教育","教育技术学","科学教育","汉语国际教育","体育教育","思想政治教育","艺术教育","音乐教育","教育学"],
    "design": ["视觉传达设计","数字媒体艺术","环境设计","产品设计","服装与服饰设计","艺术设计学","美术学","音乐学","舞蹈学","广播电视编导","播音与主持艺术"],
    "health": ["护理学","临床医学","口腔医学","预防医学","康复治疗学","药学","中药学","医学检验技术","公共卫生管理","护理学（专升本）","医学影像技术"],
    "technology": ["软件工程","软件工程技术","计算机科学与技术","网络工程","人工智能","数据科学与大数据技术","信息安全","电子信息工程","自动化","计算机应用技术","网络工程（专升本）"],
    "general": ["汉语言文学","历史学","哲学","法学","社会工作","心理学","统计学","数学与应用数学","地理科学","生态学","物理学"],
}


def _score(result, tag):
    return next(item["score"] for item in result["rankings"] if item["tag"]==tag)


def _top(result):
    return [{key:row[key] for key in ("name","score","evidence_coverage","horizon","why","gaps")} for row in result["rankings"][:5]]


def build_quality_report():
    rows=[]
    for expected_family,names in MAJORS.items():
        for major in names:
            source="existing_software_seed" if major=="软件工程" else "family_baseline"
            graph=career._seed_network_for(major) if major=="软件工程" else baseline_network(major)
            graph=career._validate_network_payload(graph,major)
            # Match the current route's public projection before ranking.
            graph=project_network_for_public(graph)
            first,last=graph["nodes"][0],graph["nodes"][-1]
            common={"test_result":{"scores":{"R":50,"I":50,"A":50,"S":50,"E":50,"C":50}},
                    "evidence":{"evidence":[]},"preferences":{},"feedback":{},"timeline":{"months_to_graduation":3}}
            baseline=recommend(graph,**common)
            evidence_node=graph["nodes"][len(graph["nodes"])//2]
            evidence={"evidence":[{"section":"experience","id":1,"text":"合成课程成果："+"；".join(evidence_node["pre"]),"source":"isolated_synthetic_fixture"}]}
            supported=recommend(graph,**{**common,"evidence":evidence})
            intent=recommend(graph,**{**common,"evidence":{"evidence":[],"intent":{"expected_position":"；".join(first["pre"])}}})
            preferred=recommend(graph,**{**common,"preferences":{"target_positions":[last["name"]]},"feedback":{last["direction_id"]:"saved"}})
            hidden=recommend(graph,**{**common,"feedback":{first["direction_id"]:"dismissed"}})
            far=recommend(graph,**{**common,"timeline":{"months_to_graduation":30}})
            sensitive=recommend(graph,**{**common,"evidence":{"evidence":[],"name":"合成名字","gender":"女",
                                                                   "mental_state_summary":"合成心理字段","ethnicity":"合成群体"}})
            regional=recommend(graph,**{**common,"preferences":{"city":"南宁"}})
            checks={
                "expected_family":major_family(major)==expected_family,
                "valid_bounded_structure":6<=len(graph["nodes"])<=60,
                "deterministic":baseline==recommend(copy.deepcopy(graph),**copy.deepcopy(common)),
                "evidence_changes_selected_score":_score(supported,evidence_node["tag"])>_score(baseline,evidence_node["tag"]),
                "evidence_changes_top_paths":[row["tag"] for row in supported["rankings"][:3]]!=[row["tag"] for row in baseline["rankings"][:3]],
                "intent_not_ability":intent["rankings"]==baseline["rankings"],
                "explicit_preference_changes_score":_score(preferred,last["tag"])>_score(baseline,last["tag"]),
                "dismissal_reduces_score":_score(hidden,first["tag"])<_score(baseline,first["tag"]),
                "preparation_stage_changes_priorities":far["rankings"]!=baseline["rankings"],
                "identity_and_psychology_excluded":sensitive["rankings"]==baseline["rankings"],
                "city_without_market_source_has_no_fake_rank_boost":regional["rankings"]==baseline["rankings"],
                "market_facts_not_claimed_verified":graph["market_data_verified"] is False,
            }
            flags=["specialist_review_pending","weights_not_calibrated_with_human_labels"]
            if expected_family=="general":flags.append("generic_framework_requires_specialty_catalog")
            if expected_family in ("health","education"):flags.append("regulated_credentials_require_real_JD_and_individual_verification")
            if source=="existing_software_seed":flags.append("software_specialist_catalog_review_pending")
            rows.append({"major":major,"family":major_family(major),"source":source,"directions":len(graph["nodes"]),
                         "direction_names":[node["name"] for node in graph["nodes"]],"checks":checks,
                         "all_automatic_checks_pass":all(checks.values()),"review_flags":flags,
                         "review_inputs":{"common":common,"course_evidence":evidence,
                                          "preferred_direction":last["name"]},
                         "evidence_target":evidence_node["name"],
                         "profile_a_no_material":_top(baseline),"profile_b_course_evidence_for_selected_direction":_top(supported),
                         "profile_c_explicit_last_direction_preference":_top(preferred)})
    source_root=Path(__file__).resolve().parents[1]
    sources=["classroom_app/services/career_recommendation_service.py","classroom_app/services/career_public_view_service.py",
             "classroom_app/services/career_seed_data.py","tests/test_career_quality_matrix.py"]
    return {"synthetic_only":True,"human_expert_review":"not_performed","scorer_version":SCORER_VERSION,
            "public_view_version":PUBLIC_VIEW_VERSION,
            "source_sha256":{name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in sources},
            "sample_count":len(rows),"automatic_pass_count":sum(row["all_automatic_checks_pass"] for row in rows),
            "generic_family_count":sum(row["family"]=="general" for row in rows),
            "scope":"Structure and input sensitivity only; no real labor-market, credential, probability, or human quality validation",
            "rows":rows}


def write_quality_report(json_path, markdown_path):
    report=build_quality_report()
    Path(json_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["77专业合成推荐对照", "=================", "",
           "本报告只验证结构、确定性和输入变化是否产生预期影响。样本材料为合成课程证据，没有真实学生数据。未进行专业教师或就业指导人员评审，不能据此宣称专业内容、权重或市场质量已通过人工验收。", "",
           f"自动检查：{report['automatic_pass_count']}/{report['sample_count']}；通用框架专业：{report['generic_family_count']}。每项真实资格仍须以岗位JD和本人材料核验。", "",
           "A：无材料；B：增加指定方向的合成课程证据；C：明确收藏并偏好最后一个方向。表中列出前三方向；JSON 保留 Top-5、完整合成输入、公开投影版本和源码哈希，供人工逐项判读。", "",
           "| 专业 | 专业族/方向数 | A 无材料 | B 课程证据 | C 明确偏好 | 自动检查 | 人工待核对 |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["rows"]:
        def names(key):return "、".join(item["name"].replace("|","/") for item in row[key][:3])
        review="专业与权重"
        if row["family"]=="general":review+="；通用框架待补专业目录"
        if row["family"] in ("health","education"):review+="；执业资格"
        if row["source"]=="existing_software_seed":review+="；软件专业路径"
        lines.append(f"| {row['major']} | {row['family']} / {row['directions']} | {names('profile_a_no_material')} | {names('profile_b_course_evidence_for_selected_direction')} | {names('profile_c_explicit_last_direction_preference')} | {'通过' if row['all_automatic_checks_pass'] else '需检查'} | {review} |")
    lines.extend(["", "JSON 对照保留每项检查、分数、准备阶段、材料覆盖、推荐依据与缺口，供后续专家逐条评审。", ""])
    Path(markdown_path).write_text("\n".join(lines),encoding="utf-8")
    return report


class CareerQualityMatrixTests(unittest.TestCase):
    def test_77_majors_have_valid_deterministic_counterfactual_outputs(self):
        report=build_quality_report()
        self.assertGreaterEqual(report["sample_count"],70)
        failures=[(row["major"],[key for key,value in row["checks"].items() if not value]) for row in report["rows"] if not row["all_automatic_checks_pass"]]
        self.assertFalse(failures,failures)
        self.assertEqual(report["human_expert_review"],"not_performed")
        self.assertEqual(report["generic_family_count"],11)


if __name__=="__main__":unittest.main()
