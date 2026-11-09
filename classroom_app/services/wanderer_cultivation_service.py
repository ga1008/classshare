"""One idempotent award path: actual learner only, bond cost before daily cap."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from ..db.schema_wanderer_world import ensure_wanderer_world_schema
from ..time_utils import local_iso, local_now
from .wanderer_bond_service import bond_count, direct_partners, lock_agents

SCALE = 1_000_000


def award_cultivation(conn, wanderer_id, base, kind, event_id, *, daily_cap=None):
    ensure_wanderer_world_schema(conn)
    amount = Decimal(str(base))
    if not amount.is_finite() or amount < 0:
        raise ValueError("修为增益必须是非负有限数")
    units = int((amount * SCALE).to_integral_value(rounding=ROUND_HALF_UP))
    lock_agents(conn, [wanderer_id, *direct_partners(conn, wanderer_id)])
    prior = conn.execute("SELECT * FROM wanderer_cultivation_awards WHERE event_id=? AND wanderer_id=?", (str(event_id), int(wanderer_id))).fetchone()
    if prior:
        if int(prior["base_units"]) != units or prior["kind"] != str(kind):
            raise ValueError("修为事件编号已用于另一笔增益")
        return (int(prior["awarded_units"])-int(prior['debt_paid_units'])) / SCALE
    multiplier = Decimal("0.9") if bond_count(conn, wanderer_id) else Decimal(1)
    granted = int((Decimal(units) * multiplier).to_integral_value(rounding=ROUND_HALF_UP))
    day = local_now().date().isoformat()
    if daily_cap is not None:
        from .wanderer_resource_service import snapshot, consume
        resource = "cultivation_todo" if kind == "todo" else "cultivation_interaction"
        available = snapshot(conn, wanderer_id, resource)
        granted = min(granted, int(Decimal(str(available["remaining"])) * SCALE))
        receipt = consume(conn, wanderer_id, resource, Decimal(granted) / SCALE, f"xp:{wanderer_id}:{event_id}")
        if not receipt["ok"]:
            granted = 0
    from .wanderer_penalty_service import repay_gain_debt
    repayment=repay_gain_debt(conn,wanderer_id,'cultivation',granted,gain_key=f'xp:{event_id}')
    paid=int(repayment['paid_units'])
    conn.execute("INSERT INTO wanderer_cultivation_awards(event_id,wanderer_id,kind,base_units,awarded_units,debt_paid_units,bond_multiplier,budget_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (str(event_id), int(wanderer_id), str(kind), units, granted, paid, str(multiplier), day, local_iso()))
    conn.execute("UPDATE wanderer_agents SET cultivation_score=cultivation_score+?,updated_at=? WHERE id=?", ((granted-paid) / SCALE, local_iso(), int(wanderer_id)))
    from .wanderer_identity_service import invalidate_wanderer_cache
    row = conn.execute("SELECT student_id FROM wanderer_agents WHERE id=?", (int(wanderer_id),)).fetchone()
    invalidate_wanderer_cache([row["student_id"]])
    return (granted-paid) / SCALE
