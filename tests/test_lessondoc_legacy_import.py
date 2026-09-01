"""旧手写 HTML 包 → LessonDoc deck 的迁移抽取单测。

夹具是按《学习文档HTML包设计规范》手写的最小页面（与 cnet-course 同构），
不依赖外部文件；真实 cnet-course 的验收在 P4 手工跑过（25 页全量还原）。
"""

import unittest

from classroom_app.services.lessondoc.legacy_import import (
    extract_deck_from_legacy_html,
    extract_manifest_from_legacy_home,
)
from classroom_app.services.lessondoc.validate import validate_deck, validate_manifest

LEGACY_LESSON = """
<!DOCTYPE html><html><head><title>第3课 · 传输层</title></head>
<body class="slides-page">
<div class="deck" data-course="《计算机网络原理》">
  <section class="slide slide--title">
    <span class="lesson-badge">第 3 课 · 理论课</span>
    <h1>传输层概览</h1>
    <p class="title-sub">端到端 · 可靠传输</p>
    <p class="course-name">《计算机网络原理》</p>
  </section>
  <section class="slide" data-section="开场">
    <h2 class="slide-title">学习目标</h2>
    <p class="slide-sub">四个要点</p>
    <div class="slide-body">
      <div class="grid-2">
        <div class="s-card fragment"><h4>① 端口</h4><p>区分进程</p></div>
        <div class="s-card fragment"><h4>② 可靠</h4><p>确认重传</p></div>
      </div>
      <div class="callout think fragment">💡 传输层是端到端的</div>
    </div>
  </section>
  <section class="slide slide--section">
    <p class="sec-no">01</p><p class="sec-title">UDP</p><p class="sec-hint">无连接</p>
  </section>
  <section class="slide" data-section="对比">
    <h2 class="slide-title">TCP / UDP</h2>
    <div class="slide-body">
      <table class="nice">
        <thead><tr><th>维度</th><th>TCP</th><th>UDP</th></tr></thead>
        <tbody>
          <tr class="fragment"><td>连接</td><td>要</td><td>不要</td></tr>
          <tr class="fragment"><td>可靠</td><td>是</td><td>否</td></tr>
        </tbody>
      </table>
      <div class="s-timeline">
        <div class="tl-item fragment"><b>一</b><span>建连</span></div>
        <div class="tl-item fragment"><b>二</b><span>传输</span></div>
      </div>
    </div>
  </section>
  <section class="slide" data-section="实操">
    <h2 class="slide-title">看看端口</h2>
    <div class="slide-body">
      <div class="code-block"><pre>netstat -an</pre></div>
      <div class="code-out">TCP 0.0.0.0:80 LISTENING</div>
      <ul class="tasklist"><li>记录监听端口</li><li>在 lanshare 平台提交</li></ul>
      <figure>
        <svg viewBox="0 0 400 200"><rect x="1" y="2" fill="#0284c7"/><text>A</text></svg>
        <figcaption>端口示意</figcaption>
      </figure>
    </div>
  </section>
  <section class="slide" data-section="测验">
    <h2 class="slide-title">随堂测验</h2>
    <div class="slide-body">
      <div class="quiz" data-answer="B">
        <div class="quiz-q">哪个协议无连接?</div>
        <div class="quiz-opts">
          <button data-k="A">A. TCP</button>
          <button data-k="B">B. UDP</button>
        </div>
        <div class="quiz-exp">✔ UDP 无需建连。</div>
      </div>
    </div>
  </section>
  <section class="slide slide--end">
    <h2>本课小结</h2>
    <p>传输层负责端到端交付。</p>
    <div class="next-up">下节课 · 第4课 拥塞控制</div>
    <p><a href="../main.html">返回课程首页</a></p>
  </section>
</div>
</body></html>
"""

LEGACY_HOME = """
<!DOCTYPE html><html><head><title>计算机网络原理</title></head><body>
<header class="hero">
  <h1>计算机网络原理</h1>
  <p>一门解释你每天都在用的系统的课。</p>
  <div class="stats">
    <div class="stat"><b>64</b><span>学时(理论32+实验32)</span></div>
    <div class="stat"><b>32</b><span>次课(16周)</span></div>
    <div class="stat"><b>4.0</b><span>学分</span></div>
    <div class="stat"><b>考试</b><span>考核方式</span></div>
  </div>
</header>
</body></html>
"""


class TestLegacyLessonExtraction(unittest.TestCase):
    def setUp(self):
        self.deck, self.warnings = extract_deck_from_legacy_html(
            LEGACY_LESSON, lesson_no=3, course_name="计算机网络原理"
        )

    def test_deck_header(self):
        self.assertEqual(self.deck["spec"], "lessondoc/2.0")
        self.assertEqual(self.deck["lesson"], 3)
        self.assertEqual(self.deck["title"], "传输层概览")
        self.assertEqual(self.deck["subtitle"], "端到端 · 可靠传输")
        self.assertIn("第 3 课", self.deck["badge"])

    def test_layouts_preserved(self):
        layouts = [s["layout"] for s in self.deck["slides"]]
        self.assertEqual(layouts[0], "title")
        self.assertIn("section", layouts)
        self.assertEqual(layouts[-1], "end")
        self.assertEqual(len(self.deck["slides"]), 7)

    def test_section_slide_fields(self):
        section = next(s for s in self.deck["slides"] if s["layout"] == "section")
        self.assertEqual(section["no"], "01")
        self.assertEqual(section["title"], "UDP")
        self.assertEqual(section["hint"], "无连接")

    def test_cards_and_callout(self):
        slide = self.deck["slides"][1]
        self.assertEqual(slide["section"], "开场")
        self.assertEqual(slide["title"], "学习目标")
        self.assertEqual(slide["sub"], "四个要点")
        cards = next(b for b in slide["blocks"] if b["type"] == "cards")
        self.assertEqual(cards["cols"], 2)
        self.assertEqual([i["title"] for i in cards["items"]], ["① 端口", "② 可靠"])
        callout = next(b for b in slide["blocks"] if b["type"] == "callout")
        self.assertEqual(callout["tone"], "think")

    def test_fragment_becomes_incremental_steps(self):
        slide = self.deck["slides"][1]
        cards = next(b for b in slide["blocks"] if b["type"] == "cards")
        callout = next(b for b in slide["blocks"] if b["type"] == "callout")
        self.assertEqual([i["step"] for i in cards["items"]], [1, 2])
        self.assertEqual(callout["step"], 3)

    def test_table_and_timeline(self):
        slide = next(s for s in self.deck["slides"] if s.get("section") == "对比")
        table = next(b for b in slide["blocks"] if b["type"] == "table")
        self.assertEqual(table["head"], ["维度", "TCP", "UDP"])
        self.assertEqual(table["rows"][0], ["连接", "要", "不要"])
        self.assertTrue(table["rowStep"])
        timeline = next(b for b in slide["blocks"] if b["type"] == "timeline")
        self.assertEqual([i["title"] for i in timeline["items"]], ["一", "二"])

    def test_code_absorbs_output_and_svg_kept(self):
        slide = next(s for s in self.deck["slides"] if s.get("section") == "实操")
        code = next(b for b in slide["blocks"] if b["type"] == "code")
        self.assertEqual(code["code"], "netstat -an")
        self.assertIn("LISTENING", code["output"])
        self.assertEqual(sum(1 for b in slide["blocks"] if b["type"] == "code"), 1)
        tasklist = next(b for b in slide["blocks"] if b["type"] == "tasklist")
        self.assertEqual(len(tasklist["items"]), 2)
        svg = next(b for b in slide["blocks"] if b["type"] == "svg")
        self.assertEqual(svg["viewBox"], "0 0 400 200")
        self.assertIn("<rect", svg["body"])
        self.assertEqual(svg["caption"], "端口示意")

    def test_quiz_option_prefix_stripped(self):
        slide = next(s for s in self.deck["slides"] if s.get("section") == "测验")
        quiz = next(b for b in slide["blocks"] if b["type"] == "quiz")
        self.assertEqual(quiz["answer"], "B")
        self.assertEqual([o["text"] for o in quiz["options"]], ["TCP", "UDP"])
        self.assertEqual(quiz["explain"], "UDP 无需建连。")

    def test_end_slide(self):
        end = self.deck["slides"][-1]
        self.assertEqual(end["title"], "本课小结")
        self.assertIn("端到端", end["summary"])
        self.assertIn("第4课", end["nextUp"])

    def test_extracted_deck_passes_validation(self):
        clean, warnings = validate_deck(self.deck, expected_lesson=3)
        self.assertEqual(len(clean["slides"]), 7)
        # 手写 SVG 的硬编码色应被 validate 收敛为语义色
        svg = next(
            b
            for s in clean["slides"]
            for b in s.get("blocks", [])
            if b["type"] == "svg"
        )
        self.assertNotIn("#0284c7", svg["body"])
        self.assertIn("var(--dg-", svg["body"])
        self.assertTrue(any("硬编码颜色" in w for w in warnings))

    def test_non_slide_html_rejected(self):
        with self.assertRaises(ValueError):
            extract_deck_from_legacy_html("<html><body><p>普通页面</p></body></html>", lesson_no=1)


class TestLegacyHomeExtraction(unittest.TestCase):
    def test_course_stats_parsed(self):
        manifest, warnings = extract_manifest_from_legacy_home(
            LEGACY_HOME,
            lessons=[{"n": 1, "title": "第一课", "status": "ready", "topics": []}],
        )
        course = manifest["course"]
        self.assertEqual(course["name"], "计算机网络原理")
        self.assertEqual(course["totalHours"], 64)
        self.assertEqual(course["sessionCount"], 32)   # 手写包写作「次课」
        self.assertEqual(course["credits"], 4)         # "4.0" 不能被压成 40
        self.assertEqual(course["assessment"], "考试")
        self.assertIn("系统", course["intro"])
        self.assertTrue(any("阶段分组" in w for w in warnings))

    def test_manifest_passes_validation(self):
        manifest, _ = extract_manifest_from_legacy_home(
            LEGACY_HOME,
            lessons=[{"n": 1, "title": "第一课", "status": "ready", "topics": []}],
        )
        clean, _ = validate_manifest(manifest)
        self.assertEqual(clean["stages"][0]["lessons"], [1])

    def test_no_lessons_rejected(self):
        with self.assertRaises(ValueError):
            extract_manifest_from_legacy_home(LEGACY_HOME, lessons=[])


if __name__ == "__main__":
    unittest.main()
