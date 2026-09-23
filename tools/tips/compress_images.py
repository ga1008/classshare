# -*- coding: utf-8 -*-
"""人生一言背景图压缩 + manifest 生成器.

用法（把 GPT 生成的原图丢进一个目录后运行）::

    python tools/tips/compress_images.py <原图目录>
    python tools/tips/compress_images.py <原图目录> --out static/img/life_tips

    python tools/tips/compress_images.py --frost-only   # 只为既有图库补霜层副本

做四件事：

1. 统一缩放到宽 ≤1600px，转 WebP（quality 自适应降档直到 ≤120KB）；
2. 输出文件名 = ``<原名 slug>-<内容hash前8位>.webp``（内容寻址，nginx 可
   ``immutable`` 强缓存，改图即换名自动失效）；
3. 重写 ``manifest.json``，按文件名前缀映射提示分类（见 PREFIX_CATEGORIES），
   供 ``life_tip_service._pick_image_url`` 按 category 配图；
4. 同批写出 ``frost/<同名>.webp`` 霜层副本（宽 48px 的预模糊图），供全站毛玻璃
   面板以静态贴图取代 ``backdrop-filter`` 实时模糊，见下方 FROST_* 常量。

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
    from PIL import Image, ImageEnhance
except ImportError:  # pragma: no cover
    print("需要 Pillow：pip install Pillow", file=sys.stderr)
    raise SystemExit(1)

MAX_WIDTH = 1600
TARGET_BYTES = 120 * 1024
QUALITY_LADDER = (78, 72, 66, 58, 50, 42)
SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# ── 霜层派生图 ─────────────────────────────────────────────────────────────
# 页面背景图同时还是全站毛玻璃「被透视的底」。面板原本用 backdrop-filter 实时
# 重算模糊，但背景层是 position:fixed 的固定图：同一张图、同一种模糊，每帧重
# 算纯属浪费。首页六个 16px 宿主铺满 88% 视口时，这就是一场重绘风暴。
# 这里为每张图预渲染一份模糊副本，面板用 background-attachment:fixed 取它身后
# 那一块——用贴图代替实时滤镜。
#
# 副本靠「缩小」而非高斯模糊得到模糊感：浏览器放大时的插值就是模糊，不花成本，
# 文件也只有 1KB 出头。背景层的减色一并烘焙进去。
FROST_DIRNAME = "frost"
FROST_WIDTH = 48          # 宽度就是伪装过的模糊半径；再宽就从霜面变回劣质照片
FROST_SATURATION = 0.90   # 背景层亮色 .95、暗色 .85，一份副本兼顾，压在材质底色下看不出差别
FROST_QUALITY = 82

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


def frost_name(file_name: str) -> str:
    """派生图一律是 .webp，无论源图是什么容器。"""
    return f"{file_name.rsplit('.', 1)[0]}.webp"


def write_frost(payload: bytes, out_dir: Path, file_name: str) -> Path:
    """把一张已压缩的背景图写成它的霜层副本。"""
    target = out_dir / FROST_DIRNAME / frost_name(file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(payload)) as image:
        rgb = image.convert("RGB")
        height = max(1, round(rgb.height * FROST_WIDTH / rgb.width))
        small = rgb.resize((FROST_WIDTH, height), Image.LANCZOS)
        small = ImageEnhance.Color(small).enhance(FROST_SATURATION)
        small.save(target, format="WEBP", quality=FROST_QUALITY, method=6)
    return target


def backfill_frost(out_dir: Path) -> int:
    """按 manifest 为既有图库补齐霜层副本，并清掉已不在册的孤儿。

    入库流程只对新一批原图跑；图库里那几百张是历史产物，需要这条补齐路径。
    """
    manifest_path = out_dir / "manifest.json"
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))["images"]
    except (OSError, ValueError, KeyError, TypeError):
        print(f"manifest 不可用: {manifest_path}", file=sys.stderr)
        return 1
    files = sorted({
        entry["file"] for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    })
    if not files:
        print("manifest 里没有可用的图片", file=sys.stderr)
        return 1

    written = missing = 0
    for file_name in files:
        source = out_dir / file_name
        if not source.is_file():
            print(f"源图缺失，跳过: {file_name}", file=sys.stderr)
            missing += 1
            continue
        target = out_dir / FROST_DIRNAME / frost_name(file_name)
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            continue
        write_frost(source.read_bytes(), out_dir, file_name)
        written += 1

    expected = {frost_name(name) for name in files}
    orphans = sorted(
        path for path in (out_dir / FROST_DIRNAME).glob("*.webp")
        if path.name not in expected
    )
    for path in orphans:
        path.unlink()

    print(f"霜层副本：{len(files)} 张在册，新写 {written} 张，清理孤儿 {len(orphans)} 张")
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="人生一言背景图压缩 + manifest 生成器")
    parser.add_argument("source_dir", type=Path, nargs="?", help="GPT 原图所在目录")
    parser.add_argument(
        "--frost-only",
        action="store_true",
        help="不入库，只按 manifest 为既有图库补齐霜层副本",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("static/img/life_tips"),
        help="输出目录（默认 static/img/life_tips）",
    )
    args = parser.parse_args()

    if args.frost_only:
        return backfill_frost(args.out)

    if args.source_dir is None:
        parser.error("需要原图目录，或改用 --frost-only")
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
        # 霜层副本与背景图同批产出，内容寻址的文件名保证两者永远对得上。
        write_frost(payload, args.out, file_name)
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
