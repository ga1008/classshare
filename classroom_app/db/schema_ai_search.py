"""Durable, account-scoped web-search budgets; initialized only at startup."""
from __future__ import annotations


AI_SEARCH_TABLES = {
    'ai_web_search_daily': """CREATE TABLE IF NOT EXISTS ai_web_search_daily (
        budget_date TEXT NOT NULL,
        actor_kind TEXT NOT NULL CHECK(actor_kind IN ('teacher','student','wanderer')),
        actor_id BIGINT NOT NULL CHECK(actor_id > 0),
        channel TEXT NOT NULL CHECK(channel IN ('chat','agent','wanderer')),
        used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
        reserved_count INTEGER NOT NULL DEFAULT 0 CHECK(reserved_count >= 0),
        unknown_count INTEGER NOT NULL DEFAULT 0 CHECK(unknown_count >= 0),
        empty_count INTEGER NOT NULL DEFAULT 0 CHECK(empty_count >= 0),
        failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
        released_count INTEGER NOT NULL DEFAULT 0 CHECK(released_count >= 0),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(budget_date,actor_kind,actor_id,channel),
        CHECK((actor_kind='wanderer' AND channel='wanderer') OR
              (actor_kind IN ('teacher','student') AND channel IN ('chat','agent'))),
        CHECK(used_count + reserved_count + unknown_count <= 10)
    )""",
    'ai_web_search_calls': """CREATE TABLE IF NOT EXISTS ai_web_search_calls (
        call_id TEXT PRIMARY KEY,
        actor_kind TEXT NOT NULL CHECK(actor_kind IN ('teacher','student','wanderer')),
        actor_id BIGINT NOT NULL CHECK(actor_id > 0),
        channel TEXT NOT NULL CHECK(channel IN ('chat','agent','wanderer')),
        logical_call_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        budget_date TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved','sent','succeeded','empty','failed','unknown','released')),
        dispatch_token TEXT NOT NULL,
        lease_until TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT 'null',
        error_code TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        sent_at TEXT,
        finished_at TEXT,
        UNIQUE(actor_kind,actor_id,channel,logical_call_id),
        CHECK((actor_kind='wanderer' AND channel='wanderer') OR
              (actor_kind IN ('teacher','student') AND channel IN ('chat','agent')))
    )""",
}

AI_SEARCH_REQUIRED_COLUMNS = {
    'ai_web_search_daily': ('budget_date','actor_kind','actor_id','channel','used_count','reserved_count',
        'unknown_count','empty_count','failed_count','released_count','updated_at'),
    'ai_web_search_calls': ('call_id','actor_kind','actor_id','channel','logical_call_id','fingerprint',
        'budget_date','state','dispatch_token','lease_until','result_json','error_code','created_at',
        'updated_at','sent_at','finished_at'),
}


def ensure_ai_search_schema(conn, *, engine=None):
    for sql in AI_SEARCH_TABLES.values():
        conn.execute(sql)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ai_search_expiring '
                 'ON ai_web_search_calls(budget_date,actor_kind,actor_id,channel,state,lease_until)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ai_search_actor_history '
                 'ON ai_web_search_calls(actor_kind,actor_id,channel,budget_date,created_at)')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_search_cached_expiry "
                 "ON ai_web_search_calls(finished_at,call_id) WHERE result_json<>'null' "
                 "AND state IN ('succeeded','empty','failed','unknown','released')")
