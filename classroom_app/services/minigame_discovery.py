"""Server-side catalog preferences. The package schema supplies the genre vocabulary."""

from functools import lru_cache
import json

from fastapi import HTTPException

from . import minigame_package_validation as validation
from . import minigame_platform as platform


@lru_cache(maxsize=1)
def genre_labels():
    schema = json.loads((validation.SCHEMAS / "config.schema.json").read_text(encoding="utf-8"))
    return {item["const"]: item["title"] for item in schema["properties"]["genre"]["oneOf"]}


def genre_options():
    return [{"id": key, "label": label} for key, label in genre_labels().items()]


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_discovery_preferences(
        actor TEXT PRIMARY KEY, genres_json TEXT NOT NULL, tags_json TEXT NOT NULL, revision INTEGER NOT NULL)""")


def read(conn, actor):
    row = conn.execute("SELECT genres_json,tags_json,revision FROM mg_discovery_preferences WHERE actor=?",
                       (actor,)).fetchone()
    if row:
        return {"genres": json.loads(row[0]), "tags": json.loads(row[1]), "revision": row[2]}
    return {"genres": [], "tags": [], "revision": 0}


def normalize(values, *, genres=False):
    limit = len(genre_labels()) if genres else 12
    if (not isinstance(values, list) or len(values) > limit or
            any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 32 for x in values)):
        raise HTTPException(422, "类型或标签的数量、长度不符合要求。")
    result = sorted({x.strip() for x in values})
    if genres and any(key not in genre_labels() for key in result):
        raise HTTPException(422, "请选择列表中的游戏类型。")
    return result


def active_filters(saved, genres, tags):
    def selected(key, value):
        if value is None:
            return saved[key]
        return normalize(value.split(",") if value else [], genres=key == "genres")
    return selected("genres", genres), selected("tags", tags)


def save(user, body):
    if (not isinstance(body, dict) or set(body) != {"genres", "tags", "expectedRevision"}
            or type(body["expectedRevision"]) is not int or body["expectedRevision"] < 0):
        raise HTTPException(422, "请提供类型、标签和当前偏好版本。")
    genres = normalize(body["genres"], genres=True)
    tags = normalize(body["tags"])
    actor = user["actor"]
    with platform.game_db() as conn:
        platform.db_start(conn)
        platform.assert_session(conn, user)
        previous = read(conn, actor)
        if previous["genres"] == genres and previous["tags"] == tags:
            return previous
        expected = body["expectedRevision"]
        if previous["revision"] == expected:
            encoded = json.dumps(genres), json.dumps(tags, ensure_ascii=False)
            if expected == 0:
                changed = conn.execute("""INSERT INTO mg_discovery_preferences VALUES(?,?,?,1)
                    ON CONFLICT(actor) DO NOTHING""", (actor, *encoded)).rowcount
            else:
                changed = conn.execute("""UPDATE mg_discovery_preferences SET genres_json=?,tags_json=?,
                    revision=revision+1 WHERE actor=? AND revision=?""", (*encoded, actor, expected)).rowcount
            if changed:
                conn.commit()
                return {"genres": genres, "tags": tags, "revision": expected + 1}
        raise HTTPException(409, {"code": "STALE_REVISION", "message": "偏好已在另一页面更新，请重新确认。",
                                  "current": read(conn, actor)})
