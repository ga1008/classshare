"""Verify the known synthetic render fixture; no Office, database, or student data.

Run with the bundled document Python (lxml and pypdf). This is a content check,
not a replacement for inspecting the rendered pages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

from lxml import etree, html
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("实习", "项目", "课程成果", "比赛", "社团 / 学生工作", "志愿服务", "兼职", "调研 / 科研", "全职工作")


def normalize(text):
    # Preserve wording, punctuation and numbers. Only layout whitespace goes.
    return re.sub(r"\s+", "", text)


def expected_facts():
    facts = [
        ("name", "渲染测试样本", 1), ("phone", "13800000000", 1),
        ("email", "sample@example.invalid", 1), ("target", "跨文化项目协调助理", 1),
        ("school", "合成测试大学", 1), ("college", "人文与管理学院", 1),
        ("major", "英语", 1), ("degree", "本科", 1),
        ("education_start", "2023-09", 1), ("education_end", "2027-06", 1),
        ("education_content", "课程成果：跨文化交流、教育实践、商务写作与资料分析。本文所有经历均为程序验收合成样本，不代表真实学生。", 1),
        ("intro_one", "具有英语沟通、资料整理和活动协作的实践记录。能够核对信息来源、记录需求并跟进反馈。", 1),
        ("intro_two", "本样本用于核对长中文、标点、链接与分页：测试 <script>alert(1)</script> 必须显示为普通文本。", 1),
        ("skill_one", "英语沟通", 1), ("skill_two", "资料分析与报告写作", 1),
        ("certificate", "合成语言能力证明", 1), ("certificate_date", "2025-06", 1),
        ("certificate_description", "仅供格式测试的证明名称。核验链接：https://example.invalid/verification?sample=resume&version=2", 1),
    ]
    facts.extend((f"experience_title_{index}", f"{kind}长中文验收样本", 1) for index, kind in enumerate(KINDS))
    facts.extend([
        ("experience_start", "2025-03", 9), ("experience_end", "2025-06", 10),
        ("role", "协作成员", 9),
        ("content_full", "围绕明确问题整理中英文资料，核对公开来源的适用范围，形成可复核的记录。" * 8, 9),
        ("contribution_full", "将讨论结论整理为行动清单，记录负责事项与反馈，发现信息不一致时联系相关人员核对。" * 3, 9),
        ("achievement_full", "交付整理记录与复盘文档。此处没有真实业绩数字，避免渲染测试引入虚构事实。", 9),
    ])
    return facts


def pdf_surface(path):
    reader = PdfReader(path)
    images = set()
    for page in reader.pages:
        for item in page.images:
            images.add(hashlib.sha256(item.data).hexdigest())
    return "\n".join(page.extract_text() or "" for page in reader.pages), {
        "pages": len(reader.pages), "unique_embedded_images": len(images)}


def run(directory):
    rows = []
    for template in ("classic", "modern", "sidebar"):
        paths = {"html": directory / f"resume-{template}.html",
                 "docx": directory / f"resume-{template}.docx",
                 "pdf": directory / f"resume-{template}.pdf",
                 "docx_pdf": directory / f"qa-docx-{template}" / f"resume-{template}.pdf"}
        dom = html.fromstring(paths["html"].read_text(encoding="utf-8"))
        texts = {"html": dom.text_content()}
        media = {"html_avatar_count": len(dom.xpath('//img[starts-with(@src,"data:image/")]')),
                 "html_executable_script_count": len(dom.xpath('//script'))}
        with ZipFile(paths["docx"]) as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
            texts["docx"] = "".join(document.xpath('//w:t/text()', namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))
            media["docx_images"] = len([name for name in archive.namelist() if name.startswith("word/media/") and not name.endswith("/")])
        for key in ("pdf", "docx_pdf"):
            texts[key], media[key] = pdf_surface(paths[key])
        checks = {}
        for surface, text in texts.items():
            normalized = normalize(text)
            facts = [{"id": key, "expected_minimum_occurrences": count,
                      "actual_occurrences": normalized.count(normalize(value)),
                      "passed": normalized.count(normalize(value)) >= count}
                     for key, value, count in expected_facts()]
            checks[surface] = {"facts_checked": len(facts), "passed": all(item["passed"] for item in facts), "facts": facts}
        rows.append({"template": template, "files": {key: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for key, path in paths.items()},
                     "media": media, "surfaces": checks})
    return {"synthetic_only": True, "normalization": "Whitespace only; wording, punctuation and numbers unchanged. Full repeated experience content is checked nine times, not only one matching snippet.",
            "visual_review": "Separate required evidence; not performed by this tool",
            "fixture_sha256": hashlib.sha256((ROOT / 'tools/career_resume_render_probe.py').read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "expected_facts": [{"id": key, "text": value, "minimum_occurrences": count} for key, value, count in expected_facts()],
            "rows": rows, "ok": all(all(check["passed"] for check in row["surfaces"].values()) and row["media"]["html_executable_script_count"] == 0 and row["media"]["html_avatar_count"] == 1 and row["media"]["docx_images"] == 1 and all(row["media"][key]["unique_embedded_images"] == 1 for key in ('pdf', 'docx_pdf')) for row in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.directory.resolve())
    output = args.output or args.directory / "fact-qa.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "facts_per_surface": len(expected_facts()), "surfaces": 12,
                      "mismatches": [{"template": row["template"], "surface": surface, "fact": fact} for row in result["rows"] for surface, check in row["surfaces"].items() for fact in check["facts"] if not fact["passed"]]}, ensure_ascii=False))
    raise SystemExit(0 if result["ok"] else 1)
