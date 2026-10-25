"""Six-axis ability radar (六边形能力雷达), one definition for every view.

Axes come from the per-classroom learning snapshot (``metrics_json``), so the
student outline and the peer outline are measured the same way:

* 材料吸收  material.ratio
* 任务执行  assignments.completion_ratio
* 成绩质量  assignments.score_ratio (only once something is graded)
* 互动表达  interactions.interaction_ratio
* 学习稳定  interactions.consistency_ratio
* 修为推进  score / 100

A radar covers a set of classrooms: the teacher detail passes the teacher's
classrooms of that student, the student profile passes *all* of the student's
classrooms.

Comparison rules (so the dashed outline is honest):

* The peer value of a classroom is the mean of the *other* active students
  (the viewer excluded), and only exists when at least ``MIN_PEERS_FOR_AVERAGE``
  other students have that axis — a tiny class can never reveal one
  classmate's numbers.
* Per axis, the student's value is the mean over every classroom that has it.
  The gap to peers is the mean of per-classroom gaps, taken only over
  classrooms that have both values; the peer outline is drawn at
  ``score - gap`` so outline, labels and deltas always agree.

Pure reads; per-classroom peer values are cached for a few minutes.
"""
from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Iterable

from .offering_membership_service import offering_class_ids

AXES: tuple[tuple[str, str, str], ...] = (
    ("material", "材料吸收", "材"),
    ("task", "任务执行", "任"),
    ("quality", "成绩质量", "绩"),
    ("interaction", "互动表达", "互"),
    ("consistency", "学习稳定", "稳"),
    ("cultivation", "修为推进", "修"),
)
AXIS_KEYS = tuple(key for key, _, _ in AXES)
PEER_CACHE_TTL_SECONDS = 300.0
MIN_PEERS_FOR_AVERAGE = 3  # other students (viewer excluded), per axis

# SVG geometry (the template's viewBox leaves room for side labels).
SIZE = 240
CENTER = SIZE / 2
RADIUS = 80
LABEL_RADIUS = 98
RING_RATIOS = (0.25, 0.5, 0.75, 1.0)

_PEER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_PEER_LOCK = threading.Lock()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def _ratio(value: Any) -> float:
    return max(0.0, min(1.0, _num(value)))


def axis_values(metrics: dict[str, Any] | None, score: Any) -> dict[str, float | None]:
    """Axis percentages (0-100) for one classroom snapshot; None = not measurable yet."""
    metrics = metrics if isinstance(metrics, dict) else {}
    material = metrics.get("material") if isinstance(metrics.get("material"), dict) else {}
    assignments = metrics.get("assignments") if isinstance(metrics.get("assignments"), dict) else {}
    interactions = metrics.get("interactions") if isinstance(metrics.get("interactions"), dict) else {}
    values: dict[str, float | None] = {key: None for key in AXIS_KEYS}
    if int(_num(material.get("required_count"))) > 0:
        values["material"] = _ratio(material.get("ratio")) * 100
    if int(_num(assignments.get("assignment_count"))) > 0:
        values["task"] = _ratio(assignments.get("completion_ratio")) * 100
        if int(_num(assignments.get("graded_count"))) > 0:
            values["quality"] = _ratio(assignments.get("score_ratio")) * 100
    if "interaction_ratio" in interactions:
        values["interaction"] = _ratio(interactions.get("interaction_ratio")) * 100
    if "consistency_ratio" in interactions:
        values["consistency"] = _ratio(interactions.get("consistency_ratio")) * 100
    values["cultivation"] = max(0.0, min(100.0, _num(score)))
    return values


def _mean(values: Iterable[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    return sum(items) / len(items) if items else None


def _load_snapshots(conn: Any, offering_id: int, student_ids: list[int]) -> list[dict[str, Any]]:
    if not student_ids:
        return []
    rows = conn.execute(
        f"""
        SELECT student_id, score, metrics_json
        FROM learning_progress_snapshots
        WHERE class_offering_id = ? AND student_id IN ({','.join('?' for _ in student_ids)})
        """,
        (int(offering_id), *student_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_metrics(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _active_student_ids(conn: Any, offering_id: int) -> list[int]:
    class_ids = offering_class_ids(conn, int(offering_id))
    if not class_ids:
        return []
    rows = conn.execute(
        f"""
        SELECT id FROM students
        WHERE class_id IN ({','.join('?' for _ in class_ids)})
          AND COALESCE(enrollment_status, 'active') = 'active'
        """,
        tuple(class_ids),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _classroom_values(conn: Any, offering_id: int) -> dict[str, Any]:
    """Per-student axis values of one classroom's active students (cached)."""
    now = time.monotonic()
    with _PEER_LOCK:
        cached = _PEER_CACHE.get(int(offering_id))
        if cached and cached[0] > now:
            return cached[1]
    student_ids = _active_student_ids(conn, int(offering_id))
    rows = _load_snapshots(conn, int(offering_id), student_ids)
    result = {
        "student_count": len(student_ids),
        "values": {
            int(row["student_id"]): axis_values(_parse_metrics(row.get("metrics_json")), row.get("score")) for row in rows
        },
    }
    with _PEER_LOCK:
        _PEER_CACHE[int(offering_id)] = (now + PEER_CACHE_TTL_SECONDS, result)
    return result


def peer_average(conn: Any, offering_id: int, *, exclude_student_id: int | None = None) -> dict[str, Any]:
    """Mean of the other students per axis; None where fewer than MIN_PEERS_FOR_AVERAGE have it."""
    classroom = _classroom_values(conn, int(offering_id))
    others = [values for student_id, values in classroom["values"].items() if student_id != exclude_student_id]
    axes: dict[str, float | None] = {}
    for key in AXIS_KEYS:
        samples = [values[key] for values in others if values.get(key) is not None]
        axes[key] = (sum(samples) / len(samples)) if len(samples) >= MIN_PEERS_FOR_AVERAGE else None
    return {
        "available": any(value is not None for value in axes.values()),
        "student_count": classroom["student_count"],
        "sample_count": len(others),
        "axes": axes,
    }


def clear_peer_cache() -> None:
    with _PEER_LOCK:
        _PEER_CACHE.clear()


def _point(index: int, ratio: float, radius: float = RADIUS) -> tuple[float, float]:
    angle = -math.pi / 2 + index * 2 * math.pi / len(AXES)
    return CENTER + math.cos(angle) * radius * ratio, CENTER + math.sin(angle) * radius * ratio


def _polygon(values: list[float]) -> str:
    return " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (_point(i, max(0.0, min(1.0, v / 100))) for i, v in enumerate(values))
    )


def _label_geometry(index: int) -> dict[str, Any]:
    angle = -math.pi / 2 + index * 2 * math.pi / len(AXES)
    cos, sin = math.cos(angle), math.sin(angle)
    x = CENTER + cos * LABEL_RADIUS
    y = CENTER + sin * LABEL_RADIUS
    anchor = "middle" if abs(cos) < 0.3 else ("start" if cos > 0 else "end")
    if sin < -0.5:
        y -= 12  # two-line label sits above the top vertex
    elif sin > 0.5:
        y += 2
    else:
        y -= 6
    return {"label_x": round(x, 1), "label_y": round(y, 1), "anchor": anchor}


def build_ability_radar(
    conn: Any,
    *,
    student_id: int,
    offering_ids: Iterable[int],
    self_states: dict[int, dict[str, Any]] | None = None,
    peer_label: str = "同门均值",
) -> dict[str, Any]:
    """Cross-classroom radar for one student.

    ``self_states`` may supply fresh ``{offering_id: {"metrics": ..., "score": ...}}``
    (the teacher view already computed them); otherwise snapshots are used.
    """
    ids = sorted({int(item) for item in offering_ids})
    self_states = self_states or {}
    own_rows: dict[int, dict[str, Any]] = {}
    for offering_id in ids:
        if offering_id in self_states:
            state = self_states[offering_id]
            own_rows[offering_id] = {"metrics": state.get("metrics") or {}, "score": state.get("score")}
            continue
        rows = _load_snapshots(conn, offering_id, [int(student_id)])
        if rows:
            own_rows[offering_id] = {"metrics": _parse_metrics(rows[0].get("metrics_json")), "score": rows[0].get("score")}
    measured = [offering_id for offering_id in ids if offering_id in own_rows]
    own_per_course = {
        offering_id: axis_values(own_rows[offering_id]["metrics"], own_rows[offering_id]["score"]) for offering_id in measured
    }
    peers = {offering_id: peer_average(conn, offering_id, exclude_student_id=int(student_id)) for offering_id in measured}

    axes: list[dict[str, Any]] = []
    for index, (key, label, short) in enumerate(AXES):
        own = _mean(own_per_course[offering_id][key] for offering_id in measured)
        # Gap = mean of per-classroom gaps over classrooms that have both values.
        gaps = [
            own_per_course[offering_id][key] - peers[offering_id]["axes"][key]
            for offering_id in measured
            if own_per_course[offering_id][key] is not None and peers[offering_id]["axes"].get(key) is not None
        ]
        score = int(round(own)) if own is not None else None
        delta = int(round(sum(gaps) / len(gaps))) if gaps and score is not None else None
        average = max(0, min(100, score - delta)) if delta is not None else None
        vertex_x, vertex_y = _point(index, (score or 0) / 100)
        axes.append({
            "key": key,
            "label": label,
            "short_label": short,
            "score": score,
            "average": average,
            "delta": (score - average) if average is not None else None,
            "vertex_x": round(vertex_x, 1),
            "vertex_y": round(vertex_y, 1),
            **_label_geometry(index),
        })
    has_average = any(axis["average"] is not None for axis in axes)
    measured_axes = [axis for axis in axes if axis["score"] is not None]
    compared = [axis for axis in measured_axes if axis["delta"] is not None]
    # Strength / focus: compare against peers when possible, otherwise by own value.
    pool = compared if len(compared) >= 2 else measured_axes
    key_of = (lambda axis: axis["delta"]) if pool is compared else (lambda axis: axis["score"])
    strongest = max(pool, key=key_of) if pool else None
    weakest = min(pool, key=key_of) if len(pool) >= 2 else None
    # Gaps in an outline are drawn where the other outline is, so a missing value
    # never looks like a zero.
    self_values = [axis["score"] if axis["score"] is not None else (axis["average"] or 0) for axis in axes]
    peer_values = [axis["average"] if axis["average"] is not None else (axis["score"] or 0) for axis in axes]
    peer_count = max((peers[offering_id]["student_count"] for offering_id in measured), default=0)
    return {
        "available": bool(measured),
        "course_count": len(measured),
        "axes": axes,
        "points": _polygon(self_values),
        "average_points": _polygon(peer_values) if has_average else "",
        "has_average": has_average,
        "peer_label": peer_label,
        "peer_count": peer_count,
        "rings": [{"ratio": int(ratio * 100), "points": _polygon([ratio * 100] * len(AXES))} for ratio in RING_RATIOS],
        "spokes": [{"x": round(_point(i, 1)[0], 1), "y": round(_point(i, 1)[1], 1)} for i in range(len(AXES))],
        "center": CENTER,
        "strength": strongest,
        "focus": weakest if weakest is not strongest else None,
        "overall": int(round(_mean(axis["score"] for axis in measured_axes))) if measured_axes else None,
    }
