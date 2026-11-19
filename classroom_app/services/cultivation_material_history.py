"""Recover earned material credit on the first upgrade of a legacy projection."""
from __future__ import annotations

from bisect import bisect_right
import json
import math


def _second(value):
    return str(value or "").replace(" ", "T")[:19]


def _object(value):
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return math.nan


def _publication_bounds(conn, offering_id):
    # Older class schedules were resequenced. A newer copy of a binding cannot
    # prove when the old binding first existed. File creation and the earliest
    # surviving binding or class reading bound that uncertainty on either side.
    rows = conn.execute("""
        WITH primary_files AS (
            SELECT learning_material_id AS material_id, id AS session_id
              FROM class_offering_sessions WHERE class_offering_id=?
            UNION ALL
            SELECT home_learning_material_id, 0 FROM class_offerings WHERE id=?
        ), bindings AS (
            SELECT p.material_id, b.created_at AS bound_at FROM primary_files p
            LEFT JOIN class_offering_learning_materials b
              ON b.class_offering_id=? AND b.session_id=p.session_id AND b.material_id=p.material_id
            UNION ALL
            SELECT material_id, created_at FROM course_material_assignments WHERE class_offering_id=?
        ), bound AS (
            SELECT material_id, MIN(bound_at) AS bound_at FROM bindings GROUP BY material_id
        ), seen AS (
            SELECT material_id, MIN(first_viewed_at) AS seen_at FROM learning_material_progress
            WHERE class_offering_id=? GROUP BY material_id
        )
        SELECT m.created_at, b.bound_at, s.seen_at FROM bound b
        JOIN course_materials m ON m.id=b.material_id AND m.node_type='file'
        LEFT JOIN seen s ON s.material_id=m.id
    """, (offering_id,) * 5).fetchall()
    possible, established = [], []
    for row in rows:
        observations = [_second(row[key]) for key in ("bound_at", "seen_at") if row[key]]
        first_seen = min(observations, default="9999")
        possible.append(min(_second(row["created_at"]), first_seen))
        established.append(first_seen)
    return sorted(possible), sorted(established)


def _known_count(bounds, at):
    possible, established = (bisect_right(dates, at) for dates in bounds)
    return established if possible == established else None


def _replay_material_history(rows, bounds, weight_version, model_cutover=""):
    """Keep verified dilution losses and credit subsequent learning against remaining headroom."""
    earned = 0.0
    last_at = None
    last_points = None
    last_weight = 0.0
    last_reset = None
    for row in rows:
        data = _object(row["metadata_json"])
        at = _second(row["created_at"])
        source = str(row["source_ref"] or "")
        if last_at and last_at < model_cutover <= at:
            # Some idle actors first received the new reading/mastery formula
            # through a normal worker refresh months after their previous score.
            # A matching rounded fraction across two formulas is not proof of
            # dilution. Start again from their first published post-upgrade value.
            earned, last_at, last_points = 0.0, None, None
        if source == "weekly:published-balance":
            current = _number(data["current"])
            weight = _number(data["weights"].get("material")) if isinstance(data.get("weights"), dict) else 0
            if data.get("weight_version") == weight_version and weight > 0 and 0 <= current <= weight:
                if last_points != current:
                    earned = current / weight
                last_at, last_points, last_weight = at, current, weight
            continue
        if (data.get("weight_version") != weight_version or source.startswith("weights:")
                or source in ("snapshot:model-upgrade", "snapshot:initial-or-upgrade")):
            # Explicit policy changes open a new entitlement epoch.
            reset = at, source, data.get("weight_version")
            if reset != last_reset:
                earned = 0.0
                last_at = last_points = None
                last_reset = reset
            if row["component"] != "material" or data.get("weight_version") != weight_version:
                continue
        if row["component"] != "material":
            continue
        weight = _number(data["weights"].get("material")) if isinstance(data.get("weights"), dict) else 0
        if not 0 < weight <= 100 or "previous" not in data or "current" not in data:
            earned, last_at, last_points = 0.0, None, None
            continue
        previous, current = _number(data["previous"]), _number(data["current"])
        if not (0 <= previous <= weight and 0 <= current <= weight):
            earned, last_at, last_points = 0.0, None, None
            continue
        if previous != last_points:
            earned, last_at = previous / weight, None
        if current < previous:
            before = _known_count(bounds, last_at) if last_at else None
            after = _known_count(bounds, at)
            # The ledger stores each endpoint to 0.1. Use the overlapping
            # rounding intervals around the unrounded old/new numerators.
            dilution = (before and after and after > before and
                        abs(previous * before - current * after) <= 0.05 * (before + after) + 1e-8)
            if not dilution:
                earned = max(current / weight, earned - (previous - current) / weight)
        elif previous < weight:
            earned += (1.0 - earned) * (current - previous) / (weight - previous)
        last_at, last_points, last_weight = at, current, weight
    if last_points is None or not math.isfinite(earned) or earned <= last_points / last_weight + 1e-9:
        return 0.0
    return min(1.0, earned)


def legacy_material_credit(conn, class_offering_id, student_id, previous_metrics):
    """Return a weight-independent lower bound backed by the actor's own published events."""
    weight_version = (previous_metrics or {}).get("weight_version")
    if not weight_version:
        return 0.0
    rows = conn.execute("""
        SELECT component, delta, source_ref, metadata_json, created_at FROM cultivation_score_events
        WHERE class_offering_id=? AND student_id=? AND
              (component='material' OR source_ref LIKE ? OR
               source_ref IN ('snapshot:model-upgrade','snapshot:initial-or-upgrade'))
        ORDER BY created_at, id
    """, (class_offering_id, student_id, "weights:%")).fetchall()
    if not any(row["component"] == "material" and row["delta"] < 0 for row in rows):
        return 0.0
    # A policy reset may change another component and leave the material balance
    # untouched. Weeklies retain that *published* balance and its calculation
    # time even in the absence of a material event. Never use the capture time
    # of a copied, possibly stale, weekly projection as the observation date.
    for row in conn.execute("""
        SELECT components_json, metadata_json FROM cultivation_weekly_snapshots
        WHERE class_offering_id=? AND student_id=?
    """, (class_offering_id, student_id)).fetchall():
        data = _object(row["metadata_json"])
        balance = _object(row["components_json"]).get("material")
        if data.get("snapshot_calculated_at") and balance is not None:
            rows.append({"component": "material", "source_ref": "weekly:published-balance",
                         "created_at": data["snapshot_calculated_at"],
                         "metadata_json": json.dumps({**data, "current": balance})})
    rows.sort(key=lambda row: (_second(row["created_at"]), row["source_ref"] == "weekly:published-balance"))
    cutover = conn.execute("""
        SELECT MIN(created_at) AS started FROM cultivation_score_events WHERE source_ref='snapshot:model-upgrade'
    """).fetchone()
    return _replay_material_history(rows, _publication_bounds(conn, class_offering_id), weight_version,
                                    _second(cutover["started"]))
