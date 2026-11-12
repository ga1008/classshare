"""Deterministic, positive-only attendance rules; no IO and no duration gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import re
from statistics import median

VERSION = "cohort-v2-20261109"
ACTUAL_WINDOW_VERSION = "actual-window-v3-20261112"
SHANGHAI = timezone(timedelta(hours=8))
TOLERANCE_MS = 20 * 60 * 1000
SCHOOL_PERIODS = {
    2: ("08:30", "09:10"), 3: ("09:20", "10:00"),
    4: ("10:20", "11:00"), 5: ("11:10", "11:50"),
    6: ("14:30", "15:10"), 7: ("15:20", "16:00"),
    8: ("16:20", "17:00"), 9: ("17:10", "17:50"),
    10: ("19:30", "20:10"), 11: ("20:20", "21:00"),
}
STATUS_LABELS = {"PENDING": "待确认", "PRESENT": "出勤", "ABSENT": "缺课",
                 "SICK_LEAVE": "病假", "PERSONAL_LEAVE": "事假", "LATE": "迟到", "EARLY": "早退"}


def to_ms(value):
    if isinstance(value, (int, float)):
        return int(value)
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=SHANGHAI)
    return int(stamp.timestamp() * 1000)


def display_time(value):
    return datetime.fromtimestamp(value / 1000, SHANGHAI).isoformat(timespec="seconds") if value is not None else None


def normalize_networks(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise ValueError("请设置 1—16 个已确认的教室 IP 或网段。")
    result = []
    for value in values:
        net = ipaddress.ip_network(str(value).strip(), strict=False)
        # A public /24 must be supplied explicitly, never inferred from teacher IP.
        if net.prefixlen == 0:
            raise ValueError("不能将整个互联网作为教室网络。")
        result.append(str(net))
    return sorted(set(result))


def matches_network(ip, networks):
    try:
        address = ipaddress.ip_address(ip)
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return any(address in ipaddress.ip_network(network) for network in networks)
    except (ValueError, TypeError):
        return False


def suggested_network(ip):
    """Editable network suggestion, never an automatically enabled trust rule."""
    try:
        address = ipaddress.ip_address(ip)
        address = getattr(address, 'ipv4_mapped', None) or address
        return str(ipaddress.ip_network(f'{address}/{24 if address.version == 4 else 64}', strict=False))
    except (ValueError, TypeError):
        return None


def actual_session_window(session, *, timestamp, duration_minutes=None, explicit_start=None, explicit_end=None):
    """A teacher starts a real recording now; timetable identity stays separate."""
    if explicit_start is not None:
        raise ValueError('记录从点击开始时的服务器时间起算，请设置预计时长，不要填写课表开始时间。')
    if duration_minutes is not None and explicit_end is not None:
        raise ValueError('预计时长和预计结束时间只能选择一种。')
    if explicit_end is not None:
        end = to_ms(explicit_end)
    else:
        if duration_minutes is None:
            duration_minutes = default_duration_minutes(session)
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or not 1 <= duration_minutes <= 240:
            raise ValueError('预计时长须为 1—240 分钟的整数。')
        end = timestamp + duration_minutes * 60_000
    if not 0 < end - timestamp <= 4 * 3600_000:
        raise ValueError('预计结束必须晚于当前服务器时间，且不超过 4 小时。')
    return {'starts_at_ms': timestamp, 'ends_at_ms': end, 'time_source': 'server_actual'}


def default_duration_minutes(session):
    try:
        period = resolve_session_window(session)
        return max(1, (period['ends_at_ms'] - period['starts_at_ms']) // 60_000)
    except (TypeError, ValueError):
        return 90


def evaluate_actual_roster(roster, observations, *, start_ms, end_ms, networks, enforce_network, applied_receipts=None):
    """Positive server-time facts; cohort statistics never veto an individual."""
    ids = {int(row['student_id']) for row in roster}
    first, last, seen, counts = {}, {}, set(), {}
    for item in observations:
        sid, stamp = int(item['student_id']), int(item['received_at_ms'])
        if sid not in ids or not start_ms <= stamp < end_ms:
            continue
        seen.add(sid)
        if enforce_network and not matches_network(item.get('ip_address'), networks):
            continue
        first[sid] = min(first.get(sid, stamp), stamp)
        last[sid] = max(last.get(sid, stamp), stamp)
        counts[sid] = counts.get(sid, 0) + 1
    results = {}
    for row in roster:
        sid = int(row['student_id'])
        access = sid in first
        receipt = (applied_receipts or {}).get(sid) if not enforce_network else None
        good = access or bool(receipt)
        reason = ('server_access_observed' if access else 'server_learning_received') if good else (
            'network_unconfirmed' if sid in seen else 'no_access_evidence')
        results[sid] = {'suggestion':'PRESENT' if good else 'PENDING', 'reason_code':reason,
            'first_seen_ms':first.get(sid), 'last_seen_ms':last.get(sid), 'deviation_ms':None,
            'matching_seen_ms':first.get(sid) if access else receipt,
            'quality':'server_observed' if good else 'unknown', 'access_observation_count':counts.get(sid,0)}
    return {'version':ACTUAL_WINDOW_VERSION, 'baseline':{'state':'not_required', 'method':'individual_server_evidence'}, 'results':results}


def resolve_session_window(session, *, explicit_start=None, explicit_end=None):
    date = str(session.get("session_date") or "")[:10]
    datetime.strptime(date, "%Y-%m-%d")
    source = "academic_time"
    if bool(explicit_start) != bool(explicit_end):
        raise ValueError("请同时填写本课开始与结束时间。")
    if explicit_start and explicit_end:
        start, end = to_ms(explicit_start), to_ms(explicit_end)
        source = "teacher_confirmed"
    else:
        times = re.findall(r"(?:[01]?\d|2[0-3]):[0-5]\d", str(session.get("academic_time_text") or ""))
        if len(times) >= 2:
            start, end = to_ms(date + "T" + times[0]), to_ms(date + "T" + times[-1])
        else:
            sections = [int(x) for x in re.findall(r"\d+", str(session.get("academic_section_text") or ""))]
            if len(sections) != 2 or tuple(sections) not in {(2,3),(4,5),(6,7),(8,9),(10,11)}:
                raise ValueError("课次缺少明确起止时间，请核对课表或确认本次起止时间。")
            start = to_ms(date + "T" + SCHOOL_PERIODS[sections[0]][0])
            end = to_ms(date + "T" + SCHOOL_PERIODS[sections[-1]][1])
            source = "gxufl_timetable_initial"
    if end <= start or end - start > 4 * 3600_000:
        raise ValueError("课次时长须大于 0 且不超过 4 小时。")
    if datetime.fromtimestamp(start / 1000, SHANGHAI).date().isoformat() != date:
        raise ValueError("确认时间必须属于当前课次日期。")
    return {"starts_at_ms": start, "ends_at_ms": end, "time_source": source}


def cohort_baseline(points, expected_count):
    """One first-visible timestamp per learner, strict majority, unique dense peak.

    Equally dense close windows select the earliest median deterministically;
    far-apart tied peaks remain unknown. Membership qualification is global;
    leave-one-out sensitivity below never rechecks minimum group size.
    """
    ordered = sorted((int(t), int(sid)) for sid, t in points.items() if t is not None)
    required = max(5, expected_count // 2 + 1)
    left, candidates, best = 0, [], 0
    for right in range(len(ordered)):
        while ordered[right][0] - ordered[left][0] >= TOLERANCE_MS:
            left += 1
        group = ordered[left:right + 1]
        size = len(group)
        if size > best:
            best, candidates = size, [group]
        elif size == best:
            candidates.append(group)
    base = {"valid_count": len(ordered), "expected_count": expected_count, "required_count": required,
            "main_count": best, "reference_ms": None, "member_ids": [], "state": "insufficient"}
    if best < required:
        return base
    references = [int(median(t for t, _ in group)) for group in candidates]
    if max(references) - min(references) >= 10 * 60_000:
        return {**base, "state": "multiple_peaks"}
    chosen = min(candidates, key=lambda g: (median(t for t, _ in g), tuple(sid for _, sid in g)))
    return {**base, "state": "ready", "reference_ms": int(median(t for t, _ in chosen)),
            "member_ids": [sid for _, sid in chosen]}


def evaluate_roster(roster, observations, *, start_ms, end_ms, networks, teacher_reference_ms=None):
    ids = {int(row["student_id"]) for row in roster}
    expected = {int(row["student_id"]) for row in roster if row.get("expected", 1)}
    first, last, seen = {}, {}, set()
    for item in observations:
        sid, timestamp = int(item["student_id"]), int(item["observed_at_ms"])
        if sid not in ids or not start_ms <= timestamp < end_ms:
            continue
        seen.add(sid)
        if not matches_network(item["ip_address"], networks):
            continue
        first[sid] = min(first.get(sid, timestamp), timestamp)
        last[sid] = max(last.get(sid, timestamp), timestamp)
    baseline = cohort_baseline({sid:t for sid,t in first.items() if sid in expected}, len(expected))
    later_baseline = cohort_baseline({sid:t for sid,t in last.items() if sid in expected}, len(expected))
    results = {}
    for row in roster:
        sid = int(row["student_id"])
        t = first.get(sid)
        reference = baseline["reference_ms"]
        route = "cohort"
        if reference is None and teacher_reference_ms is not None and start_ms <= teacher_reference_ms < end_ms:
            reference, route = teacher_reference_ms, "teacher_reference"
        reason = "no_access_evidence" if sid not in seen else "network_unconfirmed"
        good, deviation = False, None
        if t is not None:
            reason = "baseline_insufficient"
            if reference is not None:
                deviation = abs(t - reference)
                good = deviation < TOLERANCE_MS
                reason = route + "_matched" if good else "outside_cohort_tolerance"
                others = [first[i] for i in baseline["member_ids"] if i != sid]
                if good and route == "cohort" and others and abs(t - median(others)) >= TOLERANCE_MS:
                    good, reason = False, "boundary_sensitive"
        matching_seen_ms = t
        if not good and t is not None and later_baseline["state"] == "ready" and last[sid] > t:
            later_ref = later_baseline["reference_ms"]
            others = [last[i] for i in later_baseline["member_ids"] if i != sid]
            if abs(last[sid] - later_ref) < TOLERANCE_MS and (not others or abs(last[sid] - median(others)) < TOLERANCE_MS):
                good, reason = True, "later_access_cohort_matched"
                matching_seen_ms = last[sid]
        results[sid] = {"suggestion": "PRESENT" if good else "PENDING", "reason_code": reason,
                        "first_seen_ms": t, "last_seen_ms": last.get(sid), "deviation_ms": deviation,
                        "matching_seen_ms":matching_seen_ms,
                        "quality": "network_observed" if t is not None else "unknown"}
    return {"version": VERSION, "baseline": {**baseline,"later_access":later_baseline}, "results": results}
