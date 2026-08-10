"""One-shot cleanup for AI blog posts that share an identical cover image.

A real news photo belongs to exactly one article; the same image bytes used as
the cover of two or more AI-curated posts means the crawler grabbed site
furniture (trending-video thumbnails, "related reads" covers). This tool finds
those shared covers, strips the image markdown + credit line from each post,
clears the stored cover hash, and regenerates the list summary.

Usage (from repo root):
    python tools/cleanup_duplicate_blog_covers.py          # dry run, prints plan
    python tools/cleanup_duplicate_blog_covers.py --apply  # actually rewrite
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_app.database import get_db_connection  # noqa: E402
from classroom_app.services.blog_service import _generate_summary  # noqa: E402


def _strip_cover_markdown(content_md: str, file_hash: str) -> str:
    pattern = re.compile(
        rf"!\[[^\]\r\n]*\]\(/api/blog/image/{re.escape(file_hash)}\)[ \t]*"
        rf"(?:\r?\n){{0,3}}(?:>[^\r\n]*(?:\r?\n|$))?",
        re.IGNORECASE,
    )
    cleaned = pattern.sub("\n", str(content_md or ""))
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite posts (default: dry run)")
    args = parser.parse_args()

    with get_db_connection() as conn:
        shared_hashes = [
            str(row["cover_image_hash"])
            for row in conn.execute(
                """
                SELECT cover_image_hash
                FROM blog_posts
                WHERE author_role = 'assistant'
                  AND COALESCE(cover_image_hash, '') != ''
                GROUP BY cover_image_hash
                HAVING COUNT(*) >= 2
                """
            ).fetchall()
        ]
        if not shared_hashes:
            print("No shared assistant cover images found; nothing to clean.")
            return 0

        touched = 0
        for file_hash in shared_hashes:
            rows = conn.execute(
                """
                SELECT id, title, content_md
                FROM blog_posts
                WHERE author_role = 'assistant' AND cover_image_hash = ?
                ORDER BY id ASC
                """,
                (file_hash,),
            ).fetchall()
            print(f"\ncover {file_hash[:12]}... shared by {len(rows)} posts:")
            for row in rows:
                post_id = int(row["id"])
                print(f"  - post {post_id}: {str(row['title'])[:60]}")
                if not args.apply:
                    continue
                cleaned = _strip_cover_markdown(str(row["content_md"] or ""), file_hash)
                conn.execute(
                    """
                    UPDATE blog_posts
                    SET content_md = ?, summary = ?, cover_image_hash = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (cleaned, _generate_summary(cleaned), post_id),
                )
                touched += 1
        if args.apply:
            conn.commit()
            print(f"\nRewrote {touched} posts (covers cleared, summaries regenerated).")
        else:
            print("\nDry run only. Re-run with --apply to rewrite these posts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
