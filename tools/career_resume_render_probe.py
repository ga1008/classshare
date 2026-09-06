"""Render synthetic multi-page resumes through the real application exporter.

No database or student files are read. Outputs are QA evidence, not real resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from classroom_app.services.resume import resume_render_service as render


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    portrait = Image.new("RGB", (180, 240), "#dbeafe")
    drawing = ImageDraw.Draw(portrait)
    drawing.ellipse((55, 35, 125, 105), fill="#64748b")
    drawing.rounded_rectangle((30, 125, 150, 225), radius=30, fill="#64748b")
    avatar = output / "synthetic-avatar.png"
    portrait.save(avatar)
    kinds = list(render._EXPERIENCE_KIND_LABEL)
    bundle = {
        "personal": {"name": "渲染测试样本", "phone": "13800000000", "email": "sample@example.invalid",
                     "expected_position": "跨文化项目协调助理", "avatar_file_hash": hashlib.sha256(avatar.read_bytes()).hexdigest(), "avatar_mime_type": "image/png"},
        "education": [{"id": 1, "kind": "university", "school": "合成测试大学", "college": "人文与管理学院", "major": "英语", "degree": "本科",
                       "start_date": "2023-09", "end_date": "2027-06", "content": "课程成果：跨文化交流、教育实践、商务写作与资料分析。本文所有经历均为程序验收合成样本，不代表真实学生。"}],
        "self_intro": [{"id": 1, "content_md": "具有英语沟通、资料整理和活动协作的实践记录。能够核对信息来源、记录需求并跟进反馈。\n\n本样本用于核对长中文、标点、链接与分页：测试 <script>alert(1)</script> 必须显示为普通文本。"}],
        "skill": [{"id": 1, "name": "英语沟通"}, {"id": 2, "name": "资料分析与报告写作"}],
        "certificate": [{"id": 1, "name": "合成语言能力证明", "acquired_date": "2025-06", "description": "仅供格式测试的证明名称。核验链接：https://example.invalid/verification?sample=resume&version=2"}],
        "experience": [{"id": index + 1, "kind": kind, "title": f"{render._EXPERIENCE_KIND_LABEL[kind]}长中文验收样本",
                        "start_date": "2025-03", "end_date": "2025-06", "role": "协作成员",
                        "content": "围绕明确问题整理中英文资料，核对公开来源的适用范围，形成可复核的记录。" * 8,
                        "contribution": "将讨论结论整理为行动清单，记录负责事项与反馈，发现信息不一致时联系相关人员核对。" * 3,
                        "achievement": "交付整理记录与复盘文档。此处没有真实业绩数字，避免渲染测试引入虚构事实。"}
                       for index, kind in enumerate(kinds)],
    }
    layout = {"personal_fields": ["name", "phone", "email", "expected_position"], "blocks": [
        {"type": "self_intro", "ids": [1]}, {"type": "education", "ids": [1]},
        {"type": "experience", "ids": list(range(1, len(kinds) + 1))}, {"type": "skill_cert", "skill_ids": [1, 2], "cert_ids": [1]}]}
    outputs = []
    with patch.object(render.attach, "resolve_global_file_path", return_value=avatar):
        for key in render.RESUME_TEMPLATES:
            resume = {"template_key": key, "layout": layout, "target_position": "跨文化项目协调助理", "content_snapshot": bundle}
            rendered = render.assemble_resume_html(None, 0, resume)
            assert "<script>" not in rendered and "data:image/png;base64," in rendered
            assert all(label in rendered for label in render._EXPERIENCE_KIND_LABEL.values())
            (output / f"resume-{key}.html").write_text(rendered, encoding="utf-8")
            for fmt in ("pdf", "docx"):
                started = time.perf_counter()
                data = render.export_resume_bytes(rendered, fmt)
                path = output / f"resume-{key}.{fmt}"
                path.write_bytes(data)
                outputs.append({"template": key, "format": fmt, "bytes": len(data), "seconds": round(time.perf_counter()-started, 3), "path": str(path)})
    return {"synthetic_only": True, "outputs": outputs, "visual_review": "pending"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    report = run(output)
    (output / "render-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
