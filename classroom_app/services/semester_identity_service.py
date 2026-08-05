"""Canonical academic year/semester (学年学期) identity — single source of truth.

The platform historically produced the same semester under many string shapes
(``2025-2026第二学期`` / ``2025-2026学年第2学期`` / ``2025-2026学年 第2学期`` /
``2025-2026-2`` / ``2025-2026-1``), plus the smart-classroom ``(year, term)``
pair and the ZF academic ``xnm``/``xqm`` codes. That drift created duplicate
semester rows and duplicate filter entries for one real term.

This module defines ONE canonical identity — ``SemesterIdentity(start_year,
term)`` — and the parsing/formatting helpers every producer and consumer must
go through. Rules of the road:

* **Canonical display name** = ``{start}-{end}第{一|二|三}学期`` (汉字学期号，无空格)。
* Identity is ``(start_year: int, term: 1|2|3)``; ``code`` (``2025-2026-2``) is the
  stable machine key for grouping/dedup.
* Never invent a second parser. If a new legacy shape appears, extend
  :func:`parse_semester_identity` here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

# 年份区间：2025-2026 / 2025—2026 / 2025~2026 / 2025 至 2026
_YEAR_RANGE_RE = re.compile(r"(20\d{2})\s*[-–—~～至]\s*(20\d{2})")
# "第二学期" / "第2学期" / "第 二 学期"
_TERM_WORD_RE = re.compile(r"第\s*([一二三两123１２３])\s*学期")
# 年份区间之后紧跟的 "-1" / "-2" / "-3"（如 "2025-2026-2"）
_TRAILING_TERM_RE = re.compile(r"^[\s\-–—_/]*([123１２３])\b")
_TERM_DIGITS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "1": 1,
    "2": 2,
    "3": 3,
    "１": 1,
    "２": 2,
    "３": 3,
}
_TERM_CN = {1: "一", 2: "二", 3: "三"}

# ZF 教务系统学期码：xqm 3 = 第一学期，12 = 第二学期，16 = 第三学期。
_XQM_TO_TERM = {"3": 1, "12": 2, "16": 3}
_TERM_TO_XQM = {1: "3", 2: "12", 3: "16"}


@dataclass(frozen=True)
class SemesterIdentity:
    """A single real academic semester, independent of how it was written."""

    start_year: int
    term: int  # 1、2 或 3

    @property
    def end_year(self) -> int:
        return self.start_year + 1

    @property
    def canonical_name(self) -> str:
        return canonical_semester_name(self.start_year, self.term)

    @property
    def code(self) -> str:
        """Stable machine key for grouping/dedup, e.g. ``2025-2026-2``."""
        return f"{self.start_year}-{self.end_year}-{self.term}"

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.start_year, self.term)

    def as_year_term(self) -> tuple[str, str]:
        """Smart-classroom style: ``("2025-2026", "2")``."""
        return (f"{self.start_year}-{self.end_year}", str(self.term))

    def as_xnm_xqm(self) -> tuple[str, str]:
        """ZF academic style: ``("2025", "12")``."""
        return (str(self.start_year), _TERM_TO_XQM[self.term])


def canonical_semester_name(start_year: Any, term: Any) -> str:
    """``(2025, 2)`` → ``"2025-2026第二学期"``."""
    year = int(start_year)
    term_number = _TERM_DIGITS.get(str(term).strip(), 1)
    return f"{year}-{year + 1}第{_TERM_CN[term_number]}学期"


def make_identity(start_year: Any, term: Any) -> SemesterIdentity | None:
    try:
        year = int(str(start_year).strip())
    except (TypeError, ValueError):
        return None
    term_number = _TERM_DIGITS.get(str(term).strip())
    if term_number is None:
        return None
    if year < 1900 or year > 2200:
        return None
    return SemesterIdentity(start_year=year, term=term_number)


def _term_from_text(text: str, *, after: int = 0) -> int | None:
    word = _TERM_WORD_RE.search(text)
    if word:
        return _TERM_DIGITS.get(word.group(1))
    trailing = _TRAILING_TERM_RE.match(text[after:]) if after else None
    if trailing:
        return _TERM_DIGITS.get(trailing.group(1))
    return None


def _identity_from_single_text(text: str) -> SemesterIdentity | None:
    normalized = str(text or "").replace("　", " ").strip()
    if not normalized:
        return None
    year_match = _YEAR_RANGE_RE.search(normalized)
    if not year_match:
        return None
    start_year = int(year_match.group(1))
    # 先找"第X学期"，再找年份区间后紧跟的 "-1/-2"。
    term = _term_from_text(normalized)
    if term is None:
        term = _term_from_text(normalized, after=year_match.end())
    if term is None:
        return None
    return make_identity(start_year, term)


def parse_semester_identity(*sources: Any) -> SemesterIdentity | None:
    """Best-effort parse of any known semester representation into an identity.

    Accepts free-text names, ``(year_range, term)`` pairs/tuples, and plain
    values. Returns the first source that yields a full ``(year, term)``
    identity, or ``None`` when nothing is resolvable.
    """
    for source in sources:
        if source is None or source == "":
            continue
        if isinstance(source, SemesterIdentity):
            return source
        if isinstance(source, (tuple, list)) and len(source) == 2:
            identity = identity_from_year_term(source[0], source[1])
            if identity is not None:
                return identity
            continue
        identity = _identity_from_single_text(str(source))
        if identity is not None:
            return identity
    return None


def identity_from_year_term(year_range: Any, term: Any) -> SemesterIdentity | None:
    """Smart-classroom pair: ``("2025-2026", "2")`` → identity."""
    year_match = _YEAR_RANGE_RE.search(str(year_range or ""))
    if year_match:
        start_year: Any = int(year_match.group(1))
    else:
        single = re.match(r"\s*(20\d{2})\s*$", str(year_range or ""))
        if not single:
            return None
        start_year = int(single.group(1))
    term_text = str(term or "").strip()
    term_number = _TERM_DIGITS.get(term_text)
    if term_number is None and term_text in _XQM_TO_TERM:
        term_number = _XQM_TO_TERM.get(term_text)
    if term_number is None:
        term_number = _term_from_text(term_text)
    if term_number is None:
        return None
    return make_identity(start_year, term_number)


def identity_from_xnm_xqm(xnm: Any, xqm: Any) -> SemesterIdentity | None:
    """ZF academic codes: ``xnm="2024", xqm="12"`` → identity (2024, 2)."""
    match = re.match(r"\s*(20\d{2})", str(xnm or ""))
    if not match:
        return None
    term = _XQM_TO_TERM.get(str(xqm or "").strip())
    if term is None:
        term = _TERM_DIGITS.get(str(xqm or "").strip())
    if term is None:
        return None
    return make_identity(int(match.group(1)), term)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def infer_identity_from_dates(
    start_date: Any,
    end_date: Any = None,
    *,
    name: Any = None,
) -> SemesterIdentity | None:
    """Derive identity from a semester's calendar dates (term by start month).

    First honours an explicit ``name`` if it parses; otherwise uses the start
    date: 学期开始月份 ∈ [8, 1] → 第一学期，否则第二学期。学年起始年 = 秋季
    学期所在自然年。
    """
    if name:
        identity = _identity_from_single_text(str(name))
        if identity is not None:
            return identity
    start = _as_date(start_date)
    if start is None:
        return None
    if start.month >= 8:
        return make_identity(start.year, 1)
    if start.month <= 1:
        return make_identity(start.year - 1, 1)
    return make_identity(start.year - 1, 2)


def identity_from_semester_record(
    semester: Any,
    *,
    reference_date: Any = None,
) -> SemesterIdentity | None:
    """Resolve a database semester row/dict through the canonical parser.

    Explicit names win because a third/summer term cannot be inferred from its
    dates alone.  Dates remain the compatibility fallback for older rows whose
    names did not include a term number.
    """
    if semester is None:
        return None
    try:
        name = semester.get("name") or semester.get("semester_name")
        start_date = semester.get("start_date")
        end_date = semester.get("end_date")
    except AttributeError:
        try:
            name = semester["name"]
            start_date = semester["start_date"]
            end_date = semester["end_date"]
        except (KeyError, TypeError, IndexError):
            return None
    return (
        parse_semester_identity(name)
        or infer_identity_from_dates(start_date, end_date, name=name)
        or (current_identity(reference_date) if reference_date is not None else None)
    )


def zf_term_params_from_semester(semester: Any) -> dict[str, str] | None:
    """Return the verified GXUFL/ZF ``xnm`` + ``xqm`` pair for a semester."""
    identity = identity_from_semester_record(semester)
    if identity is None:
        return None
    xnm, xqm = identity.as_xnm_xqm()
    return {"xnm": xnm, "xqm": xqm}


def normalize_semester_text(*sources: Any, fallback: str = "") -> str:
    """Return the canonical name when any source parses, else a stripped原文。

    Used to规范化存量自由文本而不丢信息：能解析→规范名，不能→保留原文（或
    显式 fallback）。
    """
    identity = parse_semester_identity(*sources)
    if identity is not None:
        return identity.canonical_name
    for source in sources:
        text = str(source or "").replace("　", " ").strip()
        if text:
            return text
    return fallback


def semester_group(*sources: Any, unset_label: str = "未设学期") -> tuple[str, str]:
    """→ ``(group_key, display_label)`` for filter dropdowns.

    - 可解析 → ``(identity.code, identity.canonical_name)``（同一真实学期不同写法
      归并到同一 key）。
    - 不可解析但有文本 → ``("raw:<text>", <text>)``。
    - 全空 → ``("none", unset_label)``（``none`` 区别于筛选"全部学期"的空串）。
    """
    identity = parse_semester_identity(*sources)
    if identity is not None:
        return (identity.code, identity.canonical_name)
    for source in sources:
        text = str(source or "").replace("　", " ").strip()
        if text:
            return (f"raw:{text}", text)
    return ("none", unset_label)


def current_identity(reference_date: Any = None) -> SemesterIdentity:
    """今天所在的学年学期（按月份推断，不依赖数据库）。"""
    today = _as_date(reference_date) or date.today()
    if today.month >= 8:
        return SemesterIdentity(today.year, 1)
    if today.month <= 1:
        return SemesterIdentity(today.year - 1, 1)
    return SemesterIdentity(today.year - 1, 2)


def sort_identities(identities: Iterable[SemesterIdentity], *, reverse: bool = True):
    return sorted(identities, key=lambda item: item.sort_key, reverse=reverse)
