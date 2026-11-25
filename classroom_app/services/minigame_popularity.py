"""Game popularity from completed player entries and deduplicated foreground seconds."""

from collections import Counter

from .social_common import serialize_on


DAY = 86400
OFFSET = 8 * 3600
DAYS = 7
POINT_SECONDS = 300
DAILY_POINTS = 4
METRICS = frozenset(("playerCount", "effectiveOpens", "recentPlayerCount", "playSeconds", "recentPlaySeconds", "heatScore"))


def local_day(second):
    return (second + OFFSET) // DAY


def ensure_schema(conn, now_ms):
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_heat_players (
        game_id TEXT NOT NULL REFERENCES mg_games(id) ON DELETE CASCADE, actor TEXT NOT NULL,
        last_day INTEGER NOT NULL, day_seconds INTEGER NOT NULL DEFAULT 0, prior_seconds INTEGER NOT NULL DEFAULT 0,
        coverage_base BIGINT NOT NULL DEFAULT 0, coverage_bits TEXT NOT NULL DEFAULT '0',
        PRIMARY KEY(game_id,actor))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_heat_games (
        game_id TEXT PRIMARY KEY REFERENCES mg_games(id) ON DELETE CASCADE,
        people BIGINT NOT NULL, seconds BIGINT NOT NULL)""")
    # Circular slots bound storage even for games which never receive another visit.
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_heat_days (
        game_id TEXT NOT NULL REFERENCES mg_games(id) ON DELETE CASCADE,
        slot INTEGER NOT NULL, day INTEGER NOT NULL, seconds BIGINT NOT NULL, PRIMARY KEY(game_id,slot))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_heat_windows (
        game_id TEXT NOT NULL REFERENCES mg_games(id) ON DELETE CASCADE,
        slot INTEGER NOT NULL, day INTEGER NOT NULL, people BIGINT NOT NULL, heat BIGINT NOT NULL, bonus INTEGER NOT NULL,
        PRIMARY KEY(game_id,slot))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS mg_heat_rank ON mg_heat_windows(day,heat DESC,people DESC,game_id)""")
    conn.execute("CREATE TABLE IF NOT EXISTS mg_heat_migrations (version INTEGER PRIMARY KEY)")
    if conn.execute("INSERT INTO mg_heat_migrations(version) VALUES(1) ON CONFLICT(version) DO NOTHING").rowcount:
        _backfill(conn, now_ms)


def _backfill(conn, now_ms):
    # Historical wellbeing totals have no game identity. Only a real started run is attributable.
    today = local_day(now_ms // 1000)
    conn.execute("""INSERT INTO mg_heat_players(game_id,actor,last_day)
        SELECT a.game,s.actor,(MAX(a.started)/1000+?)/? FROM mg_rt_activity a
        JOIN mg_rt_seats s ON s.activity=a.id JOIN mg_games g ON g.id=a.game
        WHERE a.run IS NOT NULL AND a.started>0 AND a.started<=? AND g.status NOT IN ('deleting','deleted')
          AND (substr(s.actor,1,8) IN ('student:','teacher:')) GROUP BY a.game,s.actor""", (OFFSET, DAY, now_ms))
    conn.execute("""INSERT INTO mg_heat_games(game_id,people,seconds)
        SELECT game_id,COUNT(*),0 FROM mg_heat_players GROUP BY game_id""")
    bonus = "CASE " + " ".join(f"WHEN g.people>={1 << n} THEN {n+1}" for n in reversed(range(10))) + " ELSE 0 END"
    for end in range(today, today + DAYS):
        conn.execute(f"""INSERT INTO mg_heat_windows(game_id,slot,day,people,heat,bonus)
            SELECT p.game_id,?,?,COUNT(*),COUNT(*)*10+{bonus},{bonus} FROM mg_heat_players p
            JOIN mg_heat_games g ON g.game_id=p.game_id WHERE p.last_day BETWEEN ? AND ?
            GROUP BY p.game_id,g.people""", (end % DAYS, end, end-DAYS+1, end))


def _point(seconds):
    return min(DAILY_POINTS, seconds // POINT_SECONDS)


def record(conn, game_id, actor, now_ms, *, entry=False, seconds=()):
    """Caller checked the live play lease. All accepted samples are at most 90 seconds old."""
    from .minigame_wellbeing import LEASE_MS

    serialize_on(conn, "mg-heat-player", [game_id + "/" + actor])
    row = conn.execute("SELECT * FROM mg_heat_players WHERE game_id=? AND actor=?", (game_id, actor)).fetchone()
    if not row and not entry:
        return
    today = local_day(now_ms // 1000)
    old_day = row["last_day"] if row else today - DAYS
    base = max((now_ms - LEASE_MS)//1000, row["coverage_base"] if row else 0)
    bits = int(row["coverage_bits"], 16) >> (base - row["coverage_base"]) if row else 0
    additions = Counter()
    for second in seconds:
        if second < base or second >= now_ms // 1000:
            continue
        flag = 1 << (second - base)
        if not bits & flag:
            bits |= flag
            additions[local_day(second)] += 1
    if not additions and (not entry or old_day >= today):
        return

    latest = max(old_day, today if entry else max(additions))
    counts = {old_day: row["day_seconds"], old_day-1: row["prior_seconds"]} if row else {}
    # Each local day contributes to seven window end dates. Only an actor's latest
    # observed day matters for distinct membership in today's and future windows.
    people = Counter()
    points = Counter()
    for end in range(max(today, old_day + DAYS), latest + DAYS):
        people[end] = 1
    for day, delta in sorted(additions.items()):
        before = counts.get(day, 0)
        counts[day] = before + delta
        gain = _point(counts[day]) - _point(before)
        if gain:
            for end in range(max(today, day), day + DAYS):
                points[end] += gain
    conn.execute("""INSERT INTO mg_heat_players(game_id,actor,last_day,day_seconds,prior_seconds,coverage_base,coverage_bits)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(game_id,actor) DO UPDATE SET last_day=excluded.last_day,
        day_seconds=excluded.day_seconds,prior_seconds=excluded.prior_seconds,
        coverage_base=excluded.coverage_base,coverage_bits=excluded.coverage_bits""",
        (game_id, actor, latest, counts.get(latest, 0), counts.get(latest-1, 0), base, format(bits, "x")))
    # Serialize aggregate changes for this game at its single totals row. Take the same
    # lock before changing any window so concurrent threshold crossings share a bonus.
    total = conn.execute("""INSERT INTO mg_heat_games(game_id,people,seconds) VALUES(?,?,?)
        ON CONFLICT(game_id) DO UPDATE SET people=mg_heat_games.people+excluded.people,
        seconds=mg_heat_games.seconds+excluded.seconds RETURNING people""",
        (game_id, int(row is None), sum(additions.values()))).fetchone()[0]
    for day, delta in sorted(additions.items()):
        conn.execute("""INSERT INTO mg_heat_days(game_id,slot,day,seconds) VALUES(?,?,?,?)
            ON CONFLICT(game_id,slot) DO UPDATE SET day=excluded.day,
            seconds=CASE WHEN mg_heat_days.day=excluded.day THEN mg_heat_days.seconds ELSE 0 END+excluded.seconds
            WHERE mg_heat_days.day<=excluded.day""", (game_id, day % DAYS, day, delta))
    bonus = min(10, int(total).bit_length())
    # Lifetime bonus can change only on a new entry today, which touches all seven windows.
    changes = [(game_id, end % DAYS, end, people[end], 10*people[end]+points[end]+bonus, bonus)
               for end in sorted(people.keys() | points.keys())]
    if changes:
        values = ",".join("(?,?,?,?,?,?)" for _ in changes)
        conn.execute(f"""INSERT INTO mg_heat_windows(game_id,slot,day,people,heat,bonus) VALUES {values}
            ON CONFLICT(game_id,slot) DO UPDATE SET day=excluded.day,
            people=CASE WHEN mg_heat_windows.day=excluded.day THEN mg_heat_windows.people ELSE 0 END+excluded.people,
            heat=CASE WHEN mg_heat_windows.day=excluded.day THEN mg_heat_windows.heat ELSE 0 END+excluded.heat
                - CASE WHEN mg_heat_windows.day=excluded.day THEN mg_heat_windows.bonus ELSE 0 END, bonus=excluded.bonus
            WHERE mg_heat_windows.day<=excluded.day""",
            tuple(value for change in changes for value in change))
    if row is None and bonus != min(10, int(total-1).bit_length()):
        # A delayed entry from before midnight may overlap a newer writer's future window.
        conn.execute("""UPDATE mg_heat_windows SET heat=heat+(?-bonus),bonus=?
            WHERE game_id=? AND day>=? AND bonus<>?""", (bonus, bonus, game_id, today, bonus))


def popular_statement(query):
    # A scalar audience check keeps the ordered rank index as the candidate source.
    # Filtering the day inside a many-way join can lead PG to sort every visible game.
    return f"""WITH picks AS MATERIALIZED (
        SELECT h.game_id,h.day,h.people,h.heat FROM mg_heat_windows h WHERE h.day=? AND
        (SELECT 1 FROM mg_games g JOIN mg_versions v ON v.id=g.current_version_id AND v.game_id=g.id
         WHERE g.id=h.game_id AND {query.visible})=1
        ORDER BY h.heat DESC,h.people DESC,h.game_id LIMIT ?)
        SELECT {query.card_columns},h.people AS recent_people,h.heat,
        t.people AS lifetime_people,t.seconds AS lifetime_seconds,
        (SELECT COALESCE(SUM(d.seconds),0) FROM mg_heat_days d
         WHERE d.game_id=h.game_id AND d.day BETWEEN h.day-6 AND h.day) AS recent_seconds
        FROM picks h JOIN mg_heat_games t ON t.game_id=h.game_id
        JOIN mg_games g ON g.id=h.game_id JOIN mg_versions v ON v.id=g.current_version_id
        LEFT JOIN mg_version_authorship va ON va.version_id=v.id
        ORDER BY h.heat DESC,h.people DESC,h.game_id"""


def popular(conn, query, now_ms, limit):
    from .minigame_platform import public_game

    result = []
    for row in conn.execute(popular_statement(query), (local_day(now_ms // 1000), *query.args, limit)):
        result.append({**public_game(row), "playerCount": row["lifetime_people"], "effectiveOpens": row["lifetime_people"],
                       "recentPlayerCount": row["recent_people"], "playSeconds": row["lifetime_seconds"],
                       "recentPlaySeconds": int(row["recent_seconds"]), "heatScore": row["heat"]})
    return result
