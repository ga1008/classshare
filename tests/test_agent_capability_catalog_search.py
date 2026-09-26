"""Capability index search: the model searches in Chinese, the index is English.

Production task 19 issued ~45 find_capabilities calls ("保存AI配置", "settings",
"Api Save Ai", "manage/ai" …) and never found the route it needed, because the
old search required every raw word to be a substring of key/label/description.
"""
import unittest

from classroom_app.services.agent_capability_catalog_service import MAX_QUERY_HITS, rank_catalog_items


def _route(label, method, path, usage=""):
    return {"key": f"route.{abs(hash((method, path))) % 10**8:08d}", "label": label, "method": method, "path": path,
            "domain": "management", "usage": usage or f"{method} {path}"}


CATALOG = [
    _route("Api Configure Ai Offering", "POST", "/api/manage/ai/configure", "POST /api/manage/ai/configure；表单字段: class_offering_id*, system_prompt, syllabus"),
    _route("Api Get Ai Config", "GET", "/api/manage/ai/config/{class_offering_id}"),
    _route("Api Ai Generate Config", "POST", "/api/manage/ai/ai-generate", "POST /api/manage/ai/ai-generate；JSON body 字段（从接口源码推断）: class_offering_id, textbook_id"),
    _route("Api Set Exam Email Reminder", "POST", "/api/manage/exams/{exam_id}/reminder"),
    _route("Api Schedule Editor Availability", "GET", "/api/manage/schedule/availability"),
    _route("List Assignments", "GET", "/api/assignments"),
]


class CatalogSearchTests(unittest.TestCase):
    def labels(self, query):
        return [item["label"] for item in rank_catalog_items(CATALOG, query)]

    def test_chinese_intent_words_reach_english_labels_and_paths(self):
        self.assertEqual("Api Configure Ai Offering", self.labels("保存AI配置")[0])
        self.assertEqual("Api Configure Ai Offering", self.labels("课堂 AI 助教 配置 保存")[0])
        self.assertIn("Api Ai Generate Config", self.labels("生成 提示词 大纲")[:2])

    def test_path_fragments_and_methods_are_searchable(self):
        found = self.labels("manage/ai")
        self.assertEqual({"Api Configure Ai Offering", "Api Get Ai Config", "Api Ai Generate Config"}, set(found))
        self.assertNotIn("Api Schedule Editor Availability", found)
        self.assertEqual("Api Configure Ai Offering", self.labels("post configure")[0])

    def test_partial_matches_are_returned_ranked_instead_of_dropped(self):
        # The old all-words rule returned nothing for this; now the best partial hit leads.
        found = self.labels("Api Save Ai")
        self.assertTrue(found)
        self.assertEqual("Api Configure Ai Offering", found[0])
        self.assertEqual([], self.labels("zzz"))
        self.assertEqual([], self.labels("   "))

    def test_result_count_is_bounded(self):
        many = [_route(f"Api Thing {index}", "GET", f"/api/things/{index}") for index in range(MAX_QUERY_HITS + 20)]
        self.assertEqual(MAX_QUERY_HITS, len(rank_catalog_items(many, "thing")))


if __name__ == "__main__":
    unittest.main()
