"""Database-filtered catalog and keyset pagination for both supported engines."""

import base64
import hashlib
import hmac
import json

from . import minigame_platform as p

CATALOG = (
    "mg_games g JOIN mg_versions v ON v.id=g.current_version_id AND v.game_id=g.id"
)


class CatalogQuery:
    def __init__(self, user, audience):
        self.pg = p.get_configured_db_engine() == "postgres"
        self.args = []
        scope = ["g.scope='public'"]
        if user["role"] == "teacher":
            scope.append("(g.scope='private' AND (g.owner_teacher_id=? OR ?=1))")
            self.args.extend([user["id"], int(audience["admin"])])
        for name, ids in (
            ("class", audience["classes"]),
            ("classroom", audience["offers"]),
        ):
            if not ids:
                continue
            left = self.values("g.targets_json", "t")
            right = self.values("?", "u")
            scope.append(
                f"(g.scope='{name}' AND EXISTS (SELECT 1 FROM {left} JOIN {right} ON t.value=u.value))"
            )
            self.args.append(json.dumps(sorted(ids)))
        for name, fields in (
            ("school", ("school_code",)),
            ("college", ("school_code", "college")),
            ("department", ("school_code", "college", "department")),
        ):
            matches = []
            for org in audience["orgs"]:
                if all(org.get(k) for k in fields):
                    matches.append(
                        "("
                        + " AND ".join(
                            self.field("g.org_json", k) + "=?" for k in fields
                        )
                        + ")"
                    )
                    self.args.extend(org[k] for k in fields)
            if matches:
                scope.append(f"(g.scope='{name}' AND (" + " OR ".join(matches) + "))")
        self.filters = [
            "g.status='listed'",
            "v.status='ready'",
            "(" + " OR ".join(scope) + ")",
        ]
        if audience["tier"] is not None:
            self.filters.append("? BETWEEN g.min_tier AND g.max_tier")
            self.args.append(audience["tier"])

    def values(self, expr, alias):
        if self.pg:
            return f"jsonb_array_elements_text(CAST({expr} AS jsonb)) AS {alias}(value)"
        return f"json_each({expr}) AS {alias}"

    def field(self, column, *keys):
        if self.pg:
            return "CAST(" + column + " AS jsonb) #>> '{" + ",".join(keys) + "}'"
        return "json_extract(" + column + ", '$." + ".".join(keys) + "')"

    @property
    def card_columns(self):
        return "g.*, " + self.field("v.config_json", "modes") + " AS modes_json"

    @property
    def visible(self):
        return " AND ".join(self.filters)

    def search(self, q, mode, tag):
        tags = self.values("g.tags_json", "tag")
        if q:
            term = (
                "%"
                + q.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
                + "%"
            )
            self.filters.append(
                f"(LOWER(g.title) LIKE ? ESCAPE '!' OR EXISTS (SELECT 1 FROM {tags} WHERE LOWER(tag.value) LIKE ? ESCAPE '!'))"
            )
            self.args.extend([term, term])
        if tag:
            self.filters.append(f"EXISTS (SELECT 1 FROM {tags} WHERE tag.value=?)")
            self.args.append(tag)
        if mode == "solo":
            self.filters.append(self.field("v.config_json", "modes", "solo") + "=?")
            self.args.append("true" if self.pg else 1)
        elif mode:
            self.filters.append(
                self.field("v.config_json", "modes", "multiplayer", "kind") + "=?"
            )
            self.args.append(mode)

    def rows(self, conn, limit=None, anchor=None):
        conditions = list(self.filters)
        args = list(self.args)
        if anchor:
            conditions.append("(g.updated_at<? OR (g.updated_at=? AND g.id>?))")
            args.extend((anchor[0], anchor[0], anchor[1]))
        sql = (
            f"SELECT {self.card_columns} FROM {CATALOG} WHERE "
            + " AND ".join(conditions)
            + " ORDER BY g.updated_at DESC,g.id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return conn.execute(sql, tuple(args)).fetchall()


def encode_cursor(anchor, filters):
    raw = json.dumps(anchor, ensure_ascii=True, separators=(",", ":")).encode()
    signature = hmac.new(
        str(p.dependencies.SECRET_KEY).encode(),
        b"game.catalog.v1" + filters + raw,
        hashlib.sha256,
    ).digest()[:16]
    return base64.urlsafe_b64encode(signature + raw).decode().rstrip("=")


def decode_cursor(token, filters):
    if not token or len(token) > 256:
        return None
    try:
        data = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value = json.loads(data[16:])
        if (
            isinstance(value, list)
            and len(value) == 2
            and type(value[0]) is int
            and isinstance(value[1], str)
            and hmac.compare_digest(token, encode_cursor(value, filters))
        ):
            return value
    except (ValueError, TypeError):
        pass
    return None


def page(conn, user, q="", mode="", tag="", cursor="", limit=24, audience=None):
    q = q.strip()[:128]
    tag = tag.strip()[:64]
    mode = mode[:16]
    filters = json.dumps([user["actor"], q, mode, tag], ensure_ascii=True).encode()
    anchor = decode_cursor(cursor, filters)
    query = CatalogQuery(
        user, audience if audience is not None else p._audience(conn, user)
    )
    query.search(q, mode, tag)
    rows = query.rows(conn, limit + 1, anchor)
    next_cursor = (
        encode_cursor([rows[limit - 1]["updated_at"], rows[limit - 1]["id"]], filters)
        if len(rows) > limit
        else None
    )
    return {
        "items": [p.public_game(r) for r in rows[:limit]],
        "cursorReset": bool(cursor and not anchor),
        "nextCursor": next_cursor,
    }


def recommended(conn, query, recent, popular, limit):
    """Fill in priority order; each query returns only the unfilled number of cards."""
    cards = {}

    def pick(condition, args, reason):
        remaining = limit - len(cards)
        if remaining <= 0:
            return
        where = [query.visible, condition]
        params = [*query.args, *args]
        if cards:
            where.append("g.id NOT IN (" + ",".join("?" for _ in cards) + ")")
            params.extend(cards)
        sql = (
            f"SELECT {query.card_columns} FROM {CATALOG} WHERE "
            + " AND ".join(where)
            + " ORDER BY g.updated_at DESC,g.id LIMIT ?"
        )
        for row in conn.execute(sql, (*params, remaining)):
            game = p.public_game(row)
            cards[game["id"]] = {**game, "reason": reason(game)}

    pick("g.featured=1", (), lambda _: "教师精选")
    tags = {tag for game in recent for tag in game["tags"]}
    if tags and len(cards) < limit:
        marks = ",".join("?" for _ in tags)
        pick(
            f"EXISTS (SELECT 1 FROM {query.values('g.tags_json', 't')} WHERE t.value IN ({marks}))",
            sorted(tags),
            lambda game: "同标签 · " + next(tag for tag in game["tags"] if tag in tags),
        )
    for game in popular:
        if len(cards) == limit:
            break
        if game["id"] not in cards:
            cards[game["id"]] = {k: v for k, v in game.items() if k != "playerCount"}
            cards[game["id"]]["reason"] = "近期热门"
    pick("1=1", (), lambda _: "最近更新")
    return list(cards.values())
