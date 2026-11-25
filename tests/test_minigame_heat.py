"""Game heat uses audience-filtered, indexed totals and counts a lifetime player only once."""

from contextlib import closing
import sqlite3
from unittest import TestCase

from classroom_app.services import minigame_catalog as catalog
from classroom_app.services import minigame_platform as platform
from classroom_app.services import minigame_popularity as heat
from classroom_app.services.minigame_packages import ensure_package_schema
from classroom_app.services.minigame_runtime_store import ensure_runtime_schema


class GameHeatTests(TestCase):
    def setUp(self):
        self.conn = self.enterContext(closing(sqlite3.connect(":memory:")))
        self.conn.row_factory = sqlite3.Row
        platform.ensure_catalog_schema(self.conn)
        ensure_package_schema(self.conn)
        ensure_runtime_schema(self.conn)
        self.noon = 20000 * heat.DAY + 4 * 3600
        self.query = catalog.CatalogQuery({}, {"admin": True})
        self.add_game("a")

    def add_game(self, game):
        self.conn.execute("""INSERT INTO mg_games(id,owner_teacher_id,title,status,scope,current_version_id,created_at,updated_at)
            VALUES(?,1,?,'listed','public',?,1,1)""", (game, game, game))
        self.conn.execute("INSERT INTO mg_versions(id,game_id,status,created_at) VALUES(?,?,'ready',1)", (game, game))

    def install(self):
        heat.ensure_schema(self.conn, self.noon*1000)

    def read(self, at=None):
        return heat.popular(self.conn, self.query, (at or self.noon)*1000, 5)

    def entry(self, actor, at=None, game="a"):
        heat.record(self.conn, game, actor, (at or self.noon)*1000, entry=True)

    def play(self, actor, start, duration, *, game="a"):
        for offset in range(0, duration, 60):
            end = start + min(duration, offset+60)
            heat.record(self.conn, game, actor, end*1000, seconds=range(start+offset, end))

    def test_historical_distinct_lifetime_and_seven_day_players(self):
        for i, (actor, age, run) in enumerate((
                ("student:1", 125, True), ("student:1", 0, True),
                ("teacher:1", 7, True), ("student:2", 6, True),
                ("teacher:3", 0, False), ("system:1", 0, True))):
            at = (self.noon - age * heat.DAY)*1000
            self.conn.execute("""INSERT INTO mg_rt_activity(id,game,version,mode,phase,owner,capacity,run,created,started)
                VALUES(?,'a','a','solo','ended',?,1,?,?,?)""", (str(i), actor, str(i) if run else None, at, at))
            self.conn.execute("INSERT INTO mg_rt_seats VALUES(?,?,0,'alias','player',?)", (str(i), actor, at))
        self.install()
        self.install()
        old = self.read()[0]
        self.assertEqual((old["playerCount"], old["effectiveOpens"], old["recentPlayerCount"],
                          old["playSeconds"], old["recentPlaySeconds"], old["heatScore"]), (3, 3, 2, 0, 0, 22))
        self.assertEqual(self.read(self.noon+heat.DAY)[0]["recentPlayerCount"], 1)
        self.assertEqual(self.read(self.noon+7*heat.DAY), [])
        self.entry("teacher:1", self.noon+8*heat.DAY)
        returned = self.read(self.noon+8*heat.DAY)[0]
        self.assertEqual((returned["playerCount"], returned["recentPlayerCount"], returned["heatScore"]), (3, 1, 12))

    def test_window_expiry_and_daily_time_cap_with_duplicate_and_disordered_tabs(self):
        self.install()
        self.entry("student:1")
        self.entry("teacher:1")
        self.entry("student:1")
        self.play("student:1", self.noon, 2000)
        self.play("student:1", self.noon+1930, 70)
        first = self.read()[0]
        self.assertEqual((first["playSeconds"], first["recentPlaySeconds"], first["heatScore"]), (2000, 2000, 26))
        day2 = self.noon+heat.DAY
        self.entry("student:1", day2)
        self.play("student:1", day2, 400)
        self.assertEqual(self.read(day2)[0]["heatScore"], 27)
        day8 = self.noon + 7*heat.DAY
        moved = self.read(day8)[0]
        self.assertEqual((moved["playerCount"], moved["recentPlayerCount"], moved["recentPlaySeconds"],
                          moved["playSeconds"], moved["heatScore"]), (2, 1, 400, 2400, 13))
        self.add_game("b")
        self.entry("student:1", day2, "b")
        self.play("student:1", day2, 100, game="b")
        self.assertEqual([(r["id"], r["playSeconds"]) for r in self.read(day2)], [("a", 2400), ("b", 100)])

    def test_midnight_overlaps_time_quantum_index_and_withdrawal(self):
        self.install()
        midnight = self.noon + 12*3600
        self.entry("student:7", midnight-300)
        self.play("student:7", midnight-300, 261)
        # A newer tab arrives first. The older tab crosses midnight and repeats 10 seconds.
        self.play("student:7", midnight, 40)
        self.play("student:7", midnight-40, 50)
        self.play("student:7", midnight+40, 260)
        result = self.read(midnight+300)[0]
        self.assertEqual((result["recentPlaySeconds"], result["heatScore"]), (600, 13))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mg_heat_windows").fetchone()[0], 7)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM mg_heat_days").fetchone()[0], 2)
        plan = self.conn.execute("EXPLAIN QUERY PLAN "+heat.popular_statement(self.query),
                                 (heat.local_day(midnight), 5)).fetchall()
        self.assertIn("mg_heat_rank", " ".join(str(tuple(r)) for r in plan))
        self.conn.execute("UPDATE mg_games SET status='unlisted' WHERE id='a'")
        self.assertEqual(self.read(midnight), [])
