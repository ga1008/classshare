"""全国统一节假日 / 调休 自动获取与记录 (national holiday & make-up workday feed).

Source: the public *holiday-cn* dataset (mirrors 国务院办公厅 notices as JSON,
one file per year: ``{"year", "papers", "days": [{"name", "date", "isOffDay"}]}``).
The dataset says *which* weekend dates are workdays but not *which weekday
timetable* is followed; we infer that from the holiday block the workday
belongs to (the workdays make up for the last weekday(s) of the block) and mark
the mapping ``inferred`` so calendars can label it as a推断. Curated entries in
``academic_service.ACADEMIC_MAKEUP_DATA`` always take precedence.

Everything here is best-effort: fetch failures leave the last stored rows in
place and ``cached_national_lookup`` never raises.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.schema_national_holidays import ensure_national_holiday_schema

logger = logging.getLogger(__name__)

HOLIDAY_CN_MIRRORS = (
    "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
)
HOLIDAY_CN_SOURCE = "holiday-cn"
NATIONAL_HOLIDAY_REFRESH_TASK_KIND = "national_holiday_refresh"
NATIONAL_HOLIDAY_REFRESH_DEDUPE_KEY = "national-holiday-refresh"
REFRESH_INTERVAL_SECONDS = 7 * 24 * 3600
FETCH_TIMEOUT_SECONDS = 6.0   # 三个镜像 × 两个年份的最坏情况也控制在 40s 内（手动刷新按钮会等待）
LOOKUP_CACHE_TTL_SECONDS = 300
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# Distinct colours for concurrent swaps (calendar arrows / editor links).
SWAP_COLOR_COUNT = 6

_lookup_cache: dict[tuple[int, ...], tuple[float, dict[str, dict[str, Any]]]] = {}
_lookup_lock = threading.Lock()


class NationalHolidayFetchError(RuntimeError):
    """Raised when every mirror fails for a year."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _weekday_label(value: date) -> str:
    return WEEKDAY_LABELS[value.weekday()]


def _parse_day(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        day = date.fromisoformat(str(raw.get("date") or "").strip())
    except ValueError:
        return None
    name = str(raw.get("name") or "").strip()
    return {"date": day.isoformat(), "name": name, "is_off_day": bool(raw.get("isOffDay"))}


# ---------------------------------------------------------------------------
# Inference: which weekday does a make-up workday follow?
# ---------------------------------------------------------------------------

def infer_makeup_mappings(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized rows (holiday + workday) with make-up targets.

    Rule of thumb from State Council notices: a holiday block "borrows" weekend
    days to extend itself, and the borrowed weekend workdays make up for the
    *weekday* holidays that were added. We map the k workdays of a block to the
    last k weekday (Mon–Fri) holidays of the same block, in date order. When the
    block has fewer weekday holidays than workdays the extra workdays keep an
    empty target (the calendar still shows 调休上课).
    """
    parsed = [item for item in (_parse_day(raw) for raw in days) if item]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in parsed:
        grouped.setdefault(item["name"], []).append(item)
    rows: list[dict[str, Any]] = []
    for name, items in grouped.items():
        holidays = sorted((i for i in items if i["is_off_day"]), key=lambda i: i["date"])
        workdays = sorted((i for i in items if not i["is_off_day"]), key=lambda i: i["date"])
        weekday_holidays = [i for i in holidays if date.fromisoformat(i["date"]).weekday() < 5]
        targets = weekday_holidays[-len(workdays):] if workdays and weekday_holidays else []
        # Right-align: the last workday makes up for the last weekday holiday.
        offset = len(workdays) - len(targets)
        for holiday in holidays:
            rows.append({
                "date": holiday["date"], "kind": "holiday", "name": name, "label": name,
                "makeup_for_date": "", "makeup_for_weekday": "", "inferred": 0,
            })
        for index, workday in enumerate(workdays):
            target = targets[index - offset] if index >= offset and targets else None
            if target:
                target_day = date.fromisoformat(target["date"])
                label = f"{name}调休上课：补 {target_day.month} 月 {target_day.day} 日（{_weekday_label(target_day)}）课程"
                rows.append({
                    "date": workday["date"], "kind": "workday", "name": name, "label": label,
                    "makeup_for_date": target["date"], "makeup_for_weekday": _weekday_label(target_day), "inferred": 1,
                })
            else:
                rows.append({
                    "date": workday["date"], "kind": "workday", "name": name, "label": f"{name}调休上班",
                    "makeup_for_date": "", "makeup_for_weekday": "", "inferred": 1,
                })
    rows.sort(key=lambda row: row["date"])
    return rows


# ---------------------------------------------------------------------------
# Fetch + store
# ---------------------------------------------------------------------------

def fetch_holiday_cn_year(year: int, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Download one year from the first mirror that answers with a ``days`` list."""
    errors: list[str] = []
    owned = client is None
    http = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        for template in HOLIDAY_CN_MIRRORS:
            url = template.format(year=int(year))
            try:
                response = http.get(url)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{url}: {exc}")
                continue
            if isinstance(payload, dict) and isinstance(payload.get("days"), list):
                return {"year": int(year), "days": payload["days"], "papers": payload.get("papers") or [], "source_url": url}
            errors.append(f"{url}: unexpected payload")
    finally:
        if owned:
            http.close()
    raise NationalHolidayFetchError("; ".join(errors) or f"no mirror answered for {year}")


def store_national_holidays(conn, year: int, payload: dict[str, Any]) -> int:
    """Replace the stored rows for ``year`` with the fetched ones. Returns row count."""
    ensure_national_holiday_schema(conn)
    rows = infer_makeup_mappings(list(payload.get("days") or []))
    fetched_at = _now_iso()
    source_url = str(payload.get("source_url") or "")
    conn.execute("DELETE FROM national_holiday_days WHERE year = ? AND source = ?", (int(year), HOLIDAY_CN_SOURCE))
    for row in rows:
        conn.execute(
            """
            INSERT INTO national_holiday_days (
                year, date, kind, name, label, makeup_for_date, makeup_for_weekday, inferred,
                source, source_url, fetched_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(year), row["date"], row["kind"], row["name"], row["label"], row["makeup_for_date"],
             row["makeup_for_weekday"], int(row["inferred"]), HOLIDAY_CN_SOURCE, source_url, fetched_at, fetched_at, fetched_at),
        )
    return len(rows)


def refresh_national_holidays(years: list[int] | None = None, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch and store the given years (default: this year and next). Never raises."""
    today = date.today()
    target_years = sorted({int(y) for y in (years or [today.year, today.year + 1])})
    summary: dict[str, Any] = {"stored": {}, "failed": {}, "refreshed_at": _now_iso()}
    with get_db_connection() as conn:
        for year in target_years:
            try:
                payload = fetch_holiday_cn_year(year, client=client)
                if not payload["days"]:
                    summary["failed"][str(year)] = "数据集尚未发布该年度"
                    continue
                summary["stored"][str(year)] = store_national_holidays(conn, year, payload)
            except NationalHolidayFetchError as exc:
                summary["failed"][str(year)] = str(exc)[:300]
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("national holiday refresh failed for %s: %s", year, exc)
                summary["failed"][str(year)] = str(exc)[:300]
        conn.commit()
    invalidate_lookup_cache()
    return summary


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------

def load_national_holiday_rows(conn, years: list[int] | tuple[int, ...] | None = None) -> list[dict[str, Any]]:
    ensure_national_holiday_schema(conn)
    if years:
        marks = ",".join("?" for _ in years)
        cursor = conn.execute(
            f"SELECT * FROM national_holiday_days WHERE year IN ({marks}) ORDER BY date ASC", tuple(int(y) for y in years)
        )
    else:
        cursor = conn.execute("SELECT * FROM national_holiday_days ORDER BY date ASC")
    return [dict(row) for row in cursor.fetchall()]


def national_lookup_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Shape rows like ``build_holiday_lookup`` entries so they can be overlaid."""
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        info: dict[str, Any] = {
            "label": str(row.get("label") or row.get("name") or ""),
            "name": str(row.get("name") or ""),
            "scope": "national",
            "kind": str(row.get("kind") or "holiday"),
            "source": str(row.get("source") or HOLIDAY_CN_SOURCE),
            "source_url": str(row.get("source_url") or ""),
            "confidence": 0.9 if int(row.get("inferred") or 0) else 0.95,
            "fetched_at": str(row.get("fetched_at") or ""),
        }
        if row.get("makeup_for_date"):
            info["makeup_for_date"] = str(row["makeup_for_date"])
            info["makeup_for_weekday"] = str(row.get("makeup_for_weekday") or "")
            info["inferred"] = bool(int(row.get("inferred") or 0))
            if info["inferred"]:
                info["verification_note"] = "补课星期由放假通知推断（调休日补当周被占用的工作日课程）；如学校另有通知，以校内通知为准。"
        lookup[str(row["date"])] = info
    return lookup


def invalidate_lookup_cache() -> None:
    with _lookup_lock:
        _lookup_cache.clear()


def cached_national_lookup(years: Any) -> dict[str, dict[str, Any]]:
    """Lookup for the given years from the stored feed, cached 5 minutes. Never raises."""
    normalized: list[int] = []
    for raw in years or []:
        try:
            normalized.append(int(raw))
        except (TypeError, ValueError):
            continue
    key = tuple(sorted(set(normalized)))
    if not key:
        return {}
    now = time.monotonic()
    with _lookup_lock:
        cached = _lookup_cache.get(key)
        if cached and now - cached[0] < LOOKUP_CACHE_TTL_SECONDS:
            return dict(cached[1])
    try:
        with get_db_connection() as conn:
            lookup = national_lookup_from_rows(load_national_holiday_rows(conn, key))
    except Exception as exc:  # pragma: no cover - defensive: lookup must never break pages
        logger.debug("national holiday lookup unavailable: %s", exc)
        return {}
    with _lookup_lock:
        _lookup_cache[key] = (now, lookup)
    return dict(lookup)


def load_national_holiday_status(conn) -> dict[str, Any]:
    ensure_national_holiday_schema(conn)
    rows = conn.execute(
        "SELECT year, COUNT(*) AS total, SUM(CASE WHEN kind = 'workday' THEN 1 ELSE 0 END) AS workdays, MAX(fetched_at) AS fetched_at "
        "FROM national_holiday_days GROUP BY year ORDER BY year"
    ).fetchall()
    return {
        "source": HOLIDAY_CN_SOURCE,
        "years": [{"year": int(r["year"]), "total": int(r["total"] or 0), "workdays": int(r["workdays"] or 0),
                   "fetched_at": str(r["fetched_at"] or "")} for r in rows],
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
    }


def calendar_swaps(lookup: dict[str, dict[str, Any]], start: date | str | None = None,
                   end: date | str | None = None) -> list[dict[str, Any]]:
    """Make-up pairs (workday → replaced weekday) inside [start, end], with colour indexes."""
    def _as_date(value: Any) -> date | None:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)) if value else None
        except ValueError:
            return None

    lower, upper = _as_date(start), _as_date(end)
    swaps: list[dict[str, Any]] = []
    for iso_date, info in sorted(lookup.items()):
        if str(info.get("kind") or "") != "workday" or not info.get("makeup_for_date"):
            continue
        day = _as_date(iso_date)
        if day is None or (lower and day < lower) or (upper and day > upper):
            continue
        swaps.append({
            "workday_date": iso_date,
            "makeup_for_date": str(info["makeup_for_date"]),
            "makeup_for_weekday": str(info.get("makeup_for_weekday") or ""),
            "label": str(info.get("label") or ""),
            "inferred": bool(info.get("inferred")),
            "source": str(info.get("source") or "built_in"),
        })
    for index, swap in enumerate(swaps):
        swap["color_index"] = index % SWAP_COLOR_COUNT
    return swaps


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------

def ensure_national_holiday_refresh_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn, task_kind=NATIONAL_HOLIDAY_REFRESH_TASK_KIND, run_at=datetime.now() + timedelta(minutes=3),
        payload={}, dedupe_key=NATIONAL_HOLIDAY_REFRESH_DEDUPE_KEY, recurrence_seconds=REFRESH_INTERVAL_SECONDS,
        title="全国节假日/调休自动获取", priority=90, max_attempts=3, replace=False,
    )


def handle_national_holiday_refresh(task: dict[str, Any] | None = None) -> str:
    payload = (task or {}).get("payload") if isinstance(task, dict) else None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    years = payload.get("years") if isinstance(payload, dict) else None
    summary = refresh_national_holidays([int(y) for y in years] if isinstance(years, list) and years else None)
    stored = ", ".join(f"{year}:{count}" for year, count in summary["stored"].items()) or "none"
    failed = ", ".join(f"{year}:{msg}" for year, msg in summary["failed"].items())
    return f"stored [{stored}]" + (f" failed [{failed}]" if failed else "")
