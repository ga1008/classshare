#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_app.database import init_database  # noqa: E402
from classroom_app.services.blog_news_crawler_service import (  # noqa: E402
    reclassify_existing_assistant_posts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用博客快速编辑分类器重分 AI 历史文章；默认仅预览，不写数据库。"
    )
    parser.add_argument("--apply", action="store_true", help="确认写入板块和编辑记忆元数据")
    parser.add_argument(
        "--all",
        action="store_true",
        help="包括已有编辑元数据的文章；默认只处理尚未分类的旧文章",
    )
    parser.add_argument("--batch-size", type=int, default=12, help="每次快速模型分类数量")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    init_database()
    result = await reclassify_existing_assistant_posts(
        apply=args.apply,
        include_already_classified=args.all,
        batch_size=max(1, min(args.batch_size, 50)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\n当前为预览模式；核对后使用 --apply 写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
