"""Pure adapter cache contracts. No PostgreSQL or application data is used."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from classroom_app.db import postgres


class RecordingRawConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        result = (sql, params)
        self.calls.append(result)
        return result

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def executemany(self, sql, params):
        self.calls.append((sql, params))


class PostgresSQLConversionCacheTests(unittest.TestCase):
    def setUp(self):
        postgres._cached_sqlite_sql_to_psycopg.cache_clear()
        self.addCleanup(postgres._cached_sqlite_sql_to_psycopg.cache_clear)

    def test_hot_and_cold_results_preserve_the_existing_conversion_contract(self):
        corpus = (
            "SELECT '?' AS literal, \"?\" AS identifier, ? AS value -- ?\n/* ? */",
            "SELECT 'it''s ?' AS text, \"a\"\"?\" FROM t WHERE id=?",
            "SELECT * FROM t WHERE name LIKE ? COLLATE NOCASE ORDER BY name COLLATE NOCASE",
            "SELECT GROUP_CONCAT(DISTINCT COALESCE(code, '')) FROM t WHERE id=?",
            "SELECT GROUP_CONCAT(COALESCE(code, ')')) FROM t",
            "SELECT GROUP_CONCAT(code FROM t",  # Existing unmatched-parenthesis behavior.
            "SELECT '%@AI%', '%%', '%s', '%b', '%t', ? FROM t",
            "SELECT '中文与🛰', ? /* percent % and question ? */",
        )
        old = postgres._sqlite_sql_to_psycopg_uncached
        expected = [old(sql) for sql in corpus]
        with patch.object(postgres, "_sqlite_sql_to_psycopg_uncached", wraps=old) as convert:
            for _ in range(2):
                self.assertEqual(expected, [postgres.sqlite_sql_to_psycopg(sql) for sql in corpus])
            self.assertEqual(len(corpus), convert.call_count)
        self.assertIn("'?' AS literal", expected[0])
        self.assertIn("?\" AS identifier, %s AS value", expected[0])
        self.assertIn("ILIKE %s", expected[2])
        self.assertIn("STRING_AGG(DISTINCT (", expected[3])
        self.assertIn("'%%@AI%%'", expected[6])
        self.assertTrue(expected[0].endswith("-- ?\n/* ? */"))

    def test_cache_hit_does_not_cache_parameters_or_skip_actual_execute(self):
        raw = RecordingRawConnection()
        conn = postgres.LanSharePostgresConnection(raw)
        sql = "SELECT * FROM account_resource WHERE actor_id=? AND resource_id=?"
        first = conn.execute(sql, [7, 42])
        second = conn.execute(sql, [8, 43])
        third = conn.execute(sql)
        self.assertEqual((7, 42), first[1])
        self.assertEqual((8, 43), second[1])
        self.assertIsNone(third[1])
        self.assertEqual(3, len(raw.calls))
        self.assertEqual((1, 2), (postgres._cached_sqlite_sql_to_psycopg.cache_info().misses,
                                  postgres._cached_sqlite_sql_to_psycopg.cache_info().hits))

    def test_executemany_keeps_each_new_parameter_sequence_and_execute_contract(self):
        raw = RecordingRawConnection()
        conn = postgres.LanSharePostgresConnection(raw)
        sql = "INSERT INTO scoped_values(actor_id,value) VALUES(?,?)"
        conn.executemany(sql, ([index, str(index)] for index in (1, 2)))
        conn.executemany(sql, ([index, str(index)] for index in (3, 4)))
        conn.execute(sql, [5, "5"])
        self.assertEqual([(1, "1"), (2, "2")], raw.calls[0][1])
        self.assertEqual([(3, "3"), (4, "4")], raw.calls[1][1])
        self.assertEqual((5, "5"), raw.calls[2][1])
        self.assertEqual(1, postgres._cached_sqlite_sql_to_psycopg.cache_info().currsize)

    def test_lru_evicts_old_sql_and_retains_recent_sql_with_fixed_cardinality(self):
        convert = postgres.sqlite_sql_to_psycopg
        size = postgres._SQL_CONVERSION_CACHE_SIZE
        for number in range(size):
            convert(f"SELECT ? AS value /* scope-{number} */")
        convert("SELECT ? AS value /* scope-0 */")
        convert(f"SELECT ? AS value /* scope-{size} */")
        before = postgres._cached_sqlite_sql_to_psycopg.cache_info()
        convert("SELECT ? AS value /* scope-0 */")
        convert("SELECT ? AS value /* scope-1 */")
        after = postgres._cached_sqlite_sql_to_psycopg.cache_info()
        self.assertEqual(size, after.currsize)
        self.assertEqual(before.hits + 1, after.hits)
        self.assertEqual(before.misses + 1, after.misses)

    def test_only_bounded_exact_strings_are_retained_including_size_boundary(self):
        limit = postgres._SQL_CONVERSION_CACHE_MAX_CHARS
        sql = "SELECT ? " + "x" * (limit - len("SELECT ? "))
        self.assertEqual(limit, len(sql))
        postgres.sqlite_sql_to_psycopg(sql)
        before = postgres._cached_sqlite_sql_to_psycopg.cache_info()
        for index in range(64):
            large = sql + "🛰" * 128 + str(index)
            self.assertEqual(postgres._sqlite_sql_to_psycopg_uncached(large), postgres.sqlite_sql_to_psycopg(large))
        self.assertEqual(before, postgres._cached_sqlite_sql_to_psycopg.cache_info())
        postgres.sqlite_sql_to_psycopg(sql)
        self.assertEqual(before.hits + 1, postgres._cached_sqlite_sql_to_psycopg.cache_info().hits)

    def test_non_string_and_custom_string_keep_uncached_coercion_without_hashing(self):
        class ChangingSQL:
            number = 1
            def __str__(self):
                return f"SELECT ? AS value /* {self.number} */"
        class UnhashableString(str):
            __hash__ = None
        sql = ChangingSQL()
        self.assertIn("/* 1 */", postgres.sqlite_sql_to_psycopg(sql))
        sql.number = 2
        self.assertIn("/* 2 */", postgres.sqlite_sql_to_psycopg(sql))
        self.assertEqual("SELECT %s", postgres.sqlite_sql_to_psycopg(UnhashableString("SELECT ?")))
        self.assertEqual(0, postgres._cached_sqlite_sql_to_psycopg.cache_info().currsize)

    def test_conversion_exception_is_not_retained_or_changed(self):
        expected = RuntimeError("synthetic conversion failure")
        with patch.object(postgres, "_sqlite_sql_to_psycopg_uncached", side_effect=[expected, "SELECT %s"]) as convert:
            with self.assertRaises(RuntimeError) as raised:
                postgres.sqlite_sql_to_psycopg("SELECT ?")
            self.assertIs(expected, raised.exception)
            self.assertEqual(0, postgres._cached_sqlite_sql_to_psycopg.cache_info().currsize)
            self.assertEqual("SELECT %s", postgres.sqlite_sql_to_psycopg("SELECT ?"))
            self.assertEqual("SELECT %s", postgres.sqlite_sql_to_psycopg("SELECT ?"))
            self.assertEqual(2, convert.call_count)

    def test_concurrent_scopes_keep_independent_bindings_and_a_bounded_cache(self):
        barrier = threading.Barrier(8)
        def actor(number):
            raw = RecordingRawConnection()
            conn = postgres.LanSharePostgresConnection(raw)
            barrier.wait(timeout=10)
            for item in range(16):
                sql = f"SELECT ? AS actor_id, ? AS item_id /* kind-{item % 4} */"
                self.assertEqual((number, item), conn.execute(sql, [number, item])[1])
            return len(raw.calls)
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual([16] * 8, list(executor.map(actor, range(8))))
        self.assertEqual(4, postgres._cached_sqlite_sql_to_psycopg.cache_info().currsize)

    def test_exact_sql_keys_preserve_case_whitespace_and_literal_values(self):
        queries = ("SELECT ?", "select ?", "SELECT  ?", "SELECT 'actor-7', ?", "SELECT 'actor-8', ?")
        self.assertEqual([sql.replace("?", "%s") for sql in queries],
                         [postgres.sqlite_sql_to_psycopg(sql) for sql in queries])
        self.assertEqual(len(queries), postgres._cached_sqlite_sql_to_psycopg.cache_info().currsize)

    def test_retained_string_payload_has_a_bounded_expansion(self):
        limit = postgres._SQL_CONVERSION_CACHE_MAX_CHARS
        for fragment in ("?", "%x", "GROUP_CONCAT()", "GROUP_CONCAT(DISTINCT ?)", "🛰?", "/* ? % */"):
            sql = (fragment * (limit // len(fragment)))[:limit]
            with self.subTest(fragment=fragment):
                self.assertLessEqual(len(postgres.sqlite_sql_to_psycopg(sql)), 2 * len(sql))
        self.assertEqual(2 * limit, len(postgres.sqlite_sql_to_psycopg("?" * limit)))


if __name__ == "__main__":
    unittest.main()
