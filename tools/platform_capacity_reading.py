"""Published material/blog reading flows for the explicitly synthetic fixture."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path


def seed_reading(runtime: Path, fixture: dict) -> dict:
    from classroom_app.database import get_db_connection
    from classroom_app.services.blog_service import create_post

    if fixture.get("uiV3Synthetic") is not True:
        raise RuntimeError("Explicit synthetic fixture required")
    title = "合成容量验证：网络分层与课堂协作"
    with get_db_connection() as conn:
        teacher = conn.execute("SELECT * FROM teachers WHERE id=?", (fixture["teacher"]["id"],)).fetchone()
        if not teacher or teacher["email"] != fixture["teacher"]["email"] or not teacher["email"].endswith("@example.invalid"):
            raise RuntimeError("Synthetic teacher ownership mismatch")
        teacher = {**dict(teacher), "role": "teacher"}
        old = conn.execute("SELECT id FROM blog_posts WHERE title=? AND author_identity=?",
                           (title, f"teacher:{teacher['id']}")).fetchone()
        if old:
            post_id = int(old["id"])
        else:
            result = create_post(conn, teacher, title=title,
                content_md="# 网络分层与课堂协作\n\n" + ("观察地址、网关和协议，记录实验过程，并在课堂中交流验证结果。\n\n" * 80),
                visibility="class", visible_class_id=fixture["classId"],
                visible_class_offering_id=fixture["classOfferingId"], tags=["合成容量验证"])
            post_id = int(result["id"])
        material = int(fixture["visualMaterialIds"][0])
        row = conn.execute("SELECT teacher_id,name,file_hash FROM course_materials WHERE id=?", (material,)).fetchone()
        if not row or int(row["teacher_id"]) != int(teacher["id"]):
            raise RuntimeError("Synthetic published material ownership mismatch")
        conn.commit()
    metadata = {"synthetic": True, "blog_post_id": post_id, "material_id": material,
                "class_offering_id": int(fixture["classOfferingId"]), "title": title,
                "material_name": str(row["name"]), "material_sha256": str(row["file_hash"])}
    (runtime / "capacity-reading.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def reading_paths(runtime: Path, fixture: dict) -> list[tuple[str, str]]:
    data = json.loads((runtime / "capacity-reading.json").read_text(encoding="utf-8"))
    if (data.get("synthetic") is not True or int(data["class_offering_id"]) != int(fixture["classOfferingId"])
            or int(data["material_id"]) not in fixture["visualMaterialIds"]):
        raise RuntimeError("Reading fixture identity mismatch")
    return [("blog-page", "/blog"), ("blog-feed", "/api/blog/posts"),
            ("blog-post", f"/api/blog/posts/{int(data['blog_post_id'])}"),
            ("material-view", f"/materials/view/{int(data['material_id'])}"),
            ("material-content", f"/materials/download/{int(data['material_id'])}")]


def verify_reading_response(label: str, response, metadata: dict) -> None:
    """A successful login/error document cannot count as a content read."""
    if response.status_code != 200:
        return  # The main probe records the exact failing HTTP status.
    if label == "blog-page" and "data-blog-center" not in response.text:
        raise AssertionError("Blog page content missing")
    if label == "material-view" and metadata["material_name"] not in response.text:
        raise AssertionError("Published material viewer missing")
    if label == "material-content" and hashlib.sha256(response.content).hexdigest() != metadata["material_sha256"]:
        raise AssertionError("Published material bytes differ from the fixture")
    if label == "blog-post":
        post = response.json().get("post", {})
        if int(post.get("id") or 0) != metadata["blog_post_id"] or post.get("title") != metadata["title"] or not post.get("content_md"):
            raise AssertionError("Published blog post body missing")
