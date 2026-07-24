# -*- coding: utf-8 -*-
"""人生一言背景图压缩 + manifest 生成器.

用法（把 GPT 生成的原图丢进一个目录后运行）::

    python tools/tips/compress_images.py <原图目录>
    python tools/tips/compress_images.py <原图目录> --out static/img/life_tips

做三件事：

1. 统一缩放到宽 ≤1600px，转 WebP（quality 自适应降档直到 ≤120KB）；
2. 输出文件名 = ``<原名 slug>-<内容hash前8位>.webp``（内容寻址，nginx 可
   ``immutable`` 强缓存，改图即换名自动失效）；
3. 重写 ``manifest.json``，按文件名前缀映射提示分类（见 PREFIX_CATEGORIES），
   供 ``life_tip_service._pick_image_url`` 按 category 配图。

原图命名约定（可选）：``<前缀>-任意.png``，如 ``xueye-library.png`` →
分类【学业规则/论文写作/奖学金】。无匹配前缀的图不带分类标签 = 任意
提示都可选用。重复运行幂等：已存在的同 hash 文件跳过，manifest 全量重建。

Caller: 手工运行；产物被 ``classroom_app/services/life_tip_service.py``
（manifest.json）与浏览器（webp 静态文件）消费。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("需要 Pillow：pip install Pillow", file=sys.stderr)
    raise SystemExit(1)

MAX_WIDTH = 1600
TARGET_BYTES = 120 * 1024
QUALITY_LADDER = (78, 72, 66, 58, 50, 42)
SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 文件名前缀 → 提示分类（与 life_tip_seed_data 的 category 对齐）。
PREFIX_CATEGORIES: dict[str, list[str]] = {
    "xueye": ["学业规则"],
    "lunwen": ["论文写作"],
    "jiangxuejin": ["奖学金"],
    "biye": ["毕业条件"],
    "kaoyan": ["考研"],
    "kaogong": ["考公考编"],
    "shixi": ["实习"],
    "jianli": ["简历面试"],
    "hetong": ["合同五险"],
    "zhichang": ["职业路径", "教学相长", "职称科研"],
    "chengshi": ["行业城市"],
    "rensheng": ["人生大实话", "身心权益"],
}


def slugify(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    return slug or "tip"


def categories_for(stem: str) -> list[str]:
    prefix = stem.split("-", 1)[0].lower()
    return PREFIX_CATEGORIES.get(prefix, [])


def load_tag_registry(source_dir: Path) -> dict[str, list[str]]:
    """合并源目录里所有 tags*.json（{"原文件名.png": ["标签", ...]}）。

    标签由出图批次同步登记（codex brief 要求），用于服务端把提示语
    关键词与图片做模糊匹配。缺失/损坏的登记文件直接跳过。
    """
    registry: dict[str, list[str]] = {}
    for path in sorted(source_dir.glob("tags*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"标签文件损坏，跳过: {path.name}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            continue
        for file_name, tags in payload.items():
            if isinstance(tags, list):
                cleaned = [str(t).strip() for t in tags if str(t).strip()]
                if cleaned:
                    registry[str(file_name)] = cleaned[:8]
    return registry


def compress_one(source: Path) -> tuple[bytes, int]:
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        if rgb.width > MAX_WIDTH:
            height = round(rgb.height * MAX_WIDTH / rgb.width)
            rgb = rgb.resize((MAX_WIDTH, height), Image.LANCZOS)
        payload = b""
        used_quality = QUALITY_LADDER[-1]
        for quality in QUALITY_LADDER:
            buffer = io.BytesIO()
            rgb.save(buffer, format="WEBP", quality=quality, method=6)
            payload = buffer.getvalue()
            used_quality = quality
            if len(payload) <= TARGET_BYTES:
                break
        return payload, used_quality


def main() -> int:
    parser = argparse.ArgumentParser(description="人生一言背景图压缩 + manifest 生成器")
    parser.add_argument("source_dir", type=Path, help="GPT 原图所在目录")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("static/img/life_tips"),
        help="输出目录（默认 static/img/life_tips）",
    )
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        print(f"原图目录不存在: {args.source_dir}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    total_bytes = 0
    sources = sorted(
        path for path in args.source_dir.iterdir()
        if path.suffix.lower() in SOURCE_SUFFIXES
    )
    if not sources:
        print(f"目录里没有可处理的图片: {args.source_dir}", file=sys.stderr)
        return 1

    tag_registry = load_tag_registry(args.source_dir)
    for source in sources:
        payload, quality = compress_one(source)
        digest = hashlib.sha256(payload).hexdigest()[:8]
        file_name = f"{slugify(source.stem)}-{digest}.webp"
        target = args.out / file_name
        if not target.exists():
            target.write_bytes(payload)
        total_bytes += len(payload)
        entry: dict[str, object] = {
            "file": file_name,
            "categories": categories_for(source.stem),
        }
        tags = tag_registry.get(source.name)
        if tags:
            entry["tags"] = tags
        entries.append(entry)
        print(f"{source.name} -> {file_name}  {len(payload) // 1024}KB (q={quality})")

    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(
        json.dumps({"images": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n共 {len(entries)} 张，合计 {total_bytes // 1024}KB，manifest 已写入 {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
