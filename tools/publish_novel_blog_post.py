"""在博客中心发布《三次握手》课程小说并置顶。

用法（在服务器 app 容器内运行）：
    python tools/publish_novel_blog_post.py /app/data/tmp/novel_blog.md

- 作者取超管教师账号
- 通过 blog_service.create_post 走正常发帖流程（摘要/作者快照/标签）
- 发布后调用 pin_post 置顶
- 幂等：若已存在同标题的已发布帖子则跳过创建，仅确保置顶
"""
import sys

from classroom_app.database import get_db_connection, init_database
from classroom_app.services import blog_service

POST_TITLE = "三次握手——写给《计算机网络》的入门小说"
POST_TAGS = ["计算机网络", "课程导读", "小说"]


def load_content(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        raise SystemExit(f"内容文件为空: {path}")
    return content


def find_super_admin(conn) -> dict:
    row = conn.execute(
        "SELECT * FROM teachers WHERE is_super_admin = 1 AND is_active = 1 "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM teachers WHERE is_active = 1 ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        raise SystemExit("找不到可用的教师账号")
    user = dict(row)
    user["role"] = "teacher"
    return user


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python tools/publish_novel_blog_post.py <markdown文件路径>")
    content_md = load_content(sys.argv[1])

    init_database()
    with get_db_connection() as conn:
        user = find_super_admin(conn)
        print(f"作者: {user.get('name')} (teacher:{user.get('id')})")

        existing = conn.execute(
            "SELECT id, is_pinned FROM blog_posts "
            "WHERE title = ? AND status = 'published' ORDER BY id LIMIT 1",
            (POST_TITLE,),
        ).fetchone()

        if existing is not None:
            post_id = int(existing["id"])
            already_pinned = bool(existing["is_pinned"])
            print(f"帖子已存在 (id={post_id}, pinned={already_pinned})，跳过创建")
        else:
            result = blog_service.create_post(
                conn,
                user,
                title=POST_TITLE,
                content_md=content_md,
                visibility="public",
                allow_comments=True,
                tags=POST_TAGS,
            )
            post_id = int(result["id"])
            already_pinned = False
            print(f"发帖成功: id={post_id}, status={result['status']}")

        if not already_pinned:
            pin_result = blog_service.pin_post(conn, user, post_id)
            if not pin_result.get("is_pinned"):
                # pin_post 是开关式切换，若意外取消置顶则再切一次
                pin_result = blog_service.pin_post(conn, user, post_id)
            print(f"置顶状态: {pin_result}")

        conn.commit()
        print(f"完成。博客深链: /blog?post={post_id}")


if __name__ == "__main__":
    main()
