"""Role titles remain useful across seed, fallback, AI and historical graphs."""
import copy
import unittest

from classroom_app.services.career_stage_service import PHASES, ROLE_PATHS, build_career_stages, is_role_title
from classroom_app.services.career_seed_data import SE_NODES
from classroom_app.services.career_recommendation_service import FAMILY_DIRECTIONS


class CareerStageTests(unittest.TestCase):
    def test_all_maintained_directions_have_complete_occupation_paths(self):
        names = [node["name"] for node in SE_NODES]
        names += [entry[0] for family in FAMILY_DIRECTIONS.values() for entry in family]
        names += list(ROLE_PATHS)
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, ROLE_PATHS)
                stages = build_career_stages({"name": name})
                self.assertEqual([row[0] for row in stages], list(PHASES))
                self.assertTrue(all(is_role_title(row[1]) for row in stages))
                self.assertEqual(len({row[1] for row in stages}), 4)
                self.assertTrue(all(row[1] in row[2] for row in stages))

    def test_software_titles_are_complete_and_retain_the_technical_direction(self):
        for name in ("后端开发工程师", "前端开发工程师", "全栈工程师", "软件开发"):
            with self.subTest(name=name):
                titles = [stage[1] for stage in build_career_stages({"name": name})]
                self.assertTrue(titles[2].startswith("高级"))
                self.assertTrue(titles[3].startswith("资深"))
                self.assertTrue(all("工程师" in title for title in titles))
                self.assertFalse(any("经理" in title or "CTO" in title for title in titles))
        self.assertEqual(build_career_stages({"name": "测试开发工程师"})[2][1], "高级测试开发工程师")

    def test_known_directions_override_historical_actions_and_untrusted_titles(self):
        raw = {"name": "后端开发工程师", "role_titles": ["销售经理"] * 4,
               "tl": [["0-1年", "了解与观察", "旧说明"]] * 4}
        before = copy.deepcopy(raw)
        stages = build_career_stages(raw)
        self.assertEqual(raw, before)
        self.assertEqual(stages[3][1], "资深后端开发工程师")
        self.assertNotIn("0-1年", str(stages))

    def test_regulated_paths_use_the_actual_major(self):
        node = {"name": "专业临床与护理路径"}
        for major, titles in (
            ("护理学（专升本）", ["护士", "护师", "主管护师", "副主任护师"]),
            ("临床医学", ["住院医师", "主治医师", "副主任医师", "主任医师"]),
            ("药学", ["药士", "药师", "主管药师", "副主任药师"]),
        ):
            with self.subTest(major=major):
                self.assertEqual([row[1] for row in build_career_stages(node, major_name=major)], titles)
        teacher = build_career_stages({"name": "学科教学"})
        self.assertFalse(any("工程师" in row[1] for row in teacher))

    def test_unknown_directions_preserve_complete_ai_and_historical_titles(self):
        titles = ["航测助理", "航测工程师", "高级航测工程师", "航测项目经理"]
        node = {"name": "无人机航测", "role_titles": titles}
        first = build_career_stages(node)
        self.assertEqual([row[1] for row in first], titles)
        self.assertEqual(build_career_stages({"name": node["name"], "tl": first}), first)
        history = {"name": "无人机航测", "tl": [["旧阶段", title, "旧说明"] for title in titles]}
        self.assertEqual(build_career_stages(history), first)

    def test_invalid_stage_fragments_fall_back_without_corrupting_other_titles(self):
        node = {"name": "导航工程师", "tl": [
            ["旧阶段", "了解与观察", "x"], ["旧阶段", "中 / 高级", "x"],
            ["旧阶段", "高级导航工程师", "x"], ["旧阶段", "专长与协作", "x"],
        ]}
        stages = build_career_stages(node)
        self.assertEqual([row[1] for row in stages], ["初级导航工程师", "导航工程师", "高级导航工程师", "资深导航工程师"])
        self.assertEqual([row[1] for row in build_career_stages({"name": "航测项目经理"})], ["航测项目经理"] * 4)
        for value in (None, [], "", "了解与观察", "实践与证据", "独立承担任务", "专长与协作", "中 / 高级", "资深 / 工程师", "成为高级工程师", "了解项目经理", "经理", "总监"):
            with self.subTest(value=value):
                self.assertFalse(is_role_title(value))
        self.assertTrue(is_role_title("Senior Software Engineer"))
        self.assertTrue(is_role_title("UI/UX设计师"))

    def test_returned_lists_do_not_share_mutable_state(self):
        first = build_career_stages({"name": "软件开发"})
        second = build_career_stages({"name": "软件开发"})
        first[0][1] = "local mutation"
        self.assertEqual(second[0][1], "初级软件工程师")
        self.assertEqual(build_career_stages({"name": "软件开发"}), second)


if __name__ == "__main__":
    unittest.main()
