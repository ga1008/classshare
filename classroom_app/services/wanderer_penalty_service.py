"""Quantized original sanctions and non-recursive 80/20 direct-bond liability.

Amounts are settled once. Fractional tier/warning liabilities remain owned debts.
Old life deficits stay with their original life epoch and never charge a revival.
"""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from ..db.schema_wanderer_world import ensure_wanderer_world_schema
from ..time_utils import local_iso
from .wanderer_bond_service import direct_partners, lock_agents, lock_lifecycle

SCALE = 1_000_000


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _units(component, value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("处罚必须是非负有限数")
    if component not in {"life_seconds", "cultivation", "tier", "warning", "interaction_seconds"} and not component.startswith("talent:"):
        raise ValueError("处罚尚无明确的可分量化单位")
    if component.startswith("talent:"):
        from .wanderer_life_core import TALENTS
        if component.split(":",1)[1] not in TALENTS:
            raise ValueError("未知天赋处罚")
    return int((amount * (1 if component in {"life_seconds", "interaction_seconds"} else SCALE)).to_integral_value(rounding=ROUND_HALF_UP))


def _debt(conn, owner, component, delta):
    conn.execute("INSERT INTO wanderer_penalty_debts(wanderer_id,component,balance_units,updated_at) VALUES(?,?,?,?) ON CONFLICT(wanderer_id,component) DO UPDATE SET balance_units=wanderer_penalty_debts.balance_units+excluded.balance_units,updated_at=excluded.updated_at", (int(owner), component, int(delta), local_iso()))


def _mark_paid(conn, owner, component, units):
    paid_lines=[]
    rows = conn.execute("SELECT a.* FROM wanderer_penalty_allocations a LEFT JOIN wanderer_penalty_reversals r ON r.root_id=a.root_id WHERE a.beneficiary_id=? AND a.component=? AND a.applied_units<a.amount_units AND r.root_id IS NULL ORDER BY a.created_at,a.root_id", (int(owner), component)).fetchall()
    for row in rows:
        part = min(units, int(row["amount_units"])-int(row["applied_units"]))
        conn.execute("UPDATE wanderer_penalty_allocations SET applied_units=applied_units+? WHERE root_id=? AND beneficiary_id=? AND component=?", (part, row["root_id"], int(owner), component))
        if part:
            paid_lines.append({'root_id':row['root_id'],'owner_id':int(owner),'component':component,
                               'life_epoch':json.loads(row['detail_json']).get('life_epoch'),'units':part})
        units -= part
        if not units:
            break
    return paid_lines


def repay_gain_debt(conn, owner_id, component, gain_units, *, gain_key):
    """Pay the original liability from a new gain without creating another sanction."""
    ensure_wanderer_world_schema(conn)
    if component != 'cultivation' and not component.startswith('talent:'):
        raise ValueError('新收益只能偿还对应修为或天赋负担')
    gain_units=int(gain_units)
    if gain_units<0 or not str(gain_key):
        raise ValueError('收益偿债需要非负数量与幂等编号')
    lock_agents(conn,[owner_id])
    prior=conn.execute('SELECT * FROM wanderer_gain_debt_payments WHERE gain_key=? AND owner_id=? AND component=?',(str(gain_key),int(owner_id),component)).fetchone()
    if prior:
        if int(prior['gain_units']) != gain_units:
            raise ValueError('收益编号已用于不同数额')
        return {'paid_units':int(prior['paid_units']),'allocations':json.loads(prior['allocations_json']),'replayed':True}
    row=conn.execute('SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component=?',(int(owner_id),component)).fetchone()
    paid=min(gain_units,int(row['balance_units']) if row else 0)
    lines=_mark_paid(conn,owner_id,component,paid) if paid else []
    if sum(line['units'] for line in lines) != paid:
        raise ValueError('处罚负担与原始裁决不一致，须先核对账目')
    if paid:
        conn.execute('UPDATE wanderer_penalty_debts SET balance_units=balance_units-?,updated_at=? WHERE wanderer_id=? AND component=?',(paid,local_iso(),int(owner_id),component))
    epoch=conn.execute('SELECT life_epoch FROM wanderer_agents WHERE id=?',(int(owner_id),)).fetchone()['life_epoch']
    conn.execute('INSERT INTO wanderer_gain_debt_payments(gain_key,owner_id,component,gain_units,paid_units,life_epoch,allocations_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (str(gain_key),int(owner_id),component,gain_units,paid,int(epoch),_json(lines),local_iso()))
    return {'paid_units':paid,'allocations':lines,'replayed':False}


def _pay(conn, owner, component, units):
    from .wanderer_identity_service import current_life, load_wanderer
    from .wanderer_life_core import normalize_talents, talent_bounds, lifespan_weeks_for
    fresh = load_wanderer(conn, int(owner))
    if fresh is None or fresh.get("status") == "dead":
        return 0
    if component == "life_seconds":
        remaining = int(current_life(fresh)["remaining_seconds"])
        paid = min(units, remaining)
        conn.execute("UPDATE wanderer_agents SET life_penalty_weeks=life_penalty_weeks+?,updated_at=? WHERE id=?", (paid / 604800, local_iso(), owner))
    elif component == "cultivation":
        paid = min(units, max(0, int(Decimal(str(fresh["cultivation_score"])) * SCALE)))
        conn.execute("UPDATE wanderer_agents SET cultivation_score=cultivation_score-?,updated_at=? WHERE id=?", (paid / SCALE, local_iso(), owner))
    elif component == "tier":
        steps = min(units // SCALE, int(fresh["tier"]))
        paid = steps * SCALE
        talents = normalize_talents(fresh["talents"], slug=fresh["slug"])
        conn.execute("UPDATE wanderer_agents SET tier=tier-?,lifespan_weeks=?,updated_at=? WHERE id=?", (steps, lifespan_weeks_for(int(fresh["tier"])-steps, talents["vitality"]), local_iso(), owner))
    elif component == "warning":
        # Liability is separate from the historical fact of an actual warning.
        steps = units // (3 * SCALE)
        paid = steps * 3 * SCALE
        if steps:
            _debt(conn, owner, "tier", steps * SCALE)
    elif component == "interaction_seconds":
        # One unit = one fully restricted second, keeping split duration exact.
        from datetime import timedelta
        from ..time_utils import local_now, parse_datetime
        old = parse_datetime(fresh.get("interaction_penalty_until"))
        until = max(local_now(), old) if old else local_now()
        conn.execute("UPDATE wanderer_agents SET interaction_limit_penalty=1000000,interaction_penalty_until=?,updated_at=? WHERE id=?", ((until + timedelta(seconds=units)).isoformat(timespec="seconds"), local_iso(), owner))
        paid = units
    else:
        key = component.split(":", 1)[1]
        from .wanderer_life_core import TALENTS
        if key not in TALENTS:
            raise ValueError("未知天赋处罚")
        talents = normalize_talents(fresh["talents"], slug=fresh["slug"])
        low, _ = talent_bounds(key)
        paid = min(units, max(0, int(round((talents[key] - low) * SCALE))))
        talents[key] = float(Decimal(str(talents[key])) - Decimal(paid) / SCALE)
        conn.execute("UPDATE wanderer_agents SET talents_json=?,updated_at=? WHERE id=?", (_json(talents), local_iso(), owner))
        if key == "vitality":
            conn.execute("UPDATE wanderer_agents SET lifespan_weeks=? WHERE id=?", (lifespan_weeks_for(fresh["tier"], talents[key]), owner))
    return paid


def replay_debts(conn, wanderer_id, *, check_death=True):
    ensure_wanderer_world_schema(conn)
    lock_lifecycle(conn,[wanderer_id]) if check_death else lock_agents(conn,[wanderer_id])
    rows = conn.execute("SELECT component,balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND balance_units>0 ORDER BY component", (int(wanderer_id),)).fetchall()
    results = {}
    for row in rows:
        paid = _pay(conn, int(wanderer_id), row["component"], int(row["balance_units"]))
        if paid:
            conn.execute("UPDATE wanderer_penalty_debts SET balance_units=balance_units-?,updated_at=? WHERE wanderer_id=? AND component=?", (paid, local_iso(), int(wanderer_id), row["component"]))
            _mark_paid(conn, wanderer_id, row["component"], paid)
        results[row["component"]] = paid
    # A warning threshold may have just created tier debt; no second split.
    if results.get("warning"):
        tier = conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component='tier'", (int(wanderer_id),)).fetchone()
        paid = _pay(conn, int(wanderer_id), "tier", int(tier["balance_units"]))
        conn.execute("UPDATE wanderer_penalty_debts SET balance_units=balance_units-?,updated_at=? WHERE wanderer_id=? AND component='tier'", (paid, local_iso(), int(wanderer_id)))
        results["tier"] = results.get("tier", 0) + paid
    from .wanderer_identity_service import invalidate_wanderer_cache, load_wanderer, death_reason, mark_dead
    owner = load_wanderer(conn, wanderer_id)
    invalidate_wanderer_cache([owner["student_id"]])
    if check_death and owner["status"] != "dead":
        reason = death_reason(conn, owner)
        if reason:
            mark_dead(conn, wanderer_id, reason)
    return results


def apply_penalty(conn, offender_id, components, *, reason, operation_id, notify_offender=True):
    ensure_wanderer_world_schema(conn)
    amounts = {str(k): _units(str(k), v) for k, v in components.items()}
    if not amounts or not str(reason).strip() or not str(operation_id).strip():
        raise ValueError("处罚需要总量、原因与幂等编号")
    fingerprint = hashlib.sha256(_json([int(offender_id), amounts, str(reason)]).encode()).hexdigest()
    partners = direct_partners(conn, offender_id)
    lock_lifecycle(conn, [offender_id, *partners])
    prior = conn.execute("SELECT * FROM wanderer_penalty_roots WHERE id=?", (str(operation_id),)).fetchone()
    if prior:
        if prior["request_hash"] != fingerprint:
            raise ValueError("处罚编号不能重复绑定其他处罚")
        return json.loads(prior["result_json"])
    # Snapshot only effective locked edges. Later death must not erase this liability.
    partners = [i for i in direct_partners(conn, offender_id) if i in partners]
    now = local_iso()
    conn.execute("INSERT INTO wanderer_penalty_roots(id,offender_id,request_hash,reason,components_json,partners_json,result_json,created_at) VALUES(?,?,?,?,?,?,'{}',?)", (str(operation_id), int(offender_id), fingerprint, str(reason)[:1000], _json(amounts), _json(partners), now))
    allocations = []
    for component, total in amounts.items():
        partner_total = total // 5 if partners else 0
        distribution = [(int(offender_id), total-partner_total)]
        if partners:
            each, remainder = divmod(partner_total, len(partners))
            distribution += [(peer, each + int(index < remainder)) for index, peer in enumerate(partners)]
        for owner, units in distribution:
            conn.execute("INSERT INTO wanderer_penalty_allocations(root_id,beneficiary_id,component,amount_units,detail_json,created_at) VALUES(?,?,?,?,?,?)", (str(operation_id), owner, component, units, _json({"derived": owner != int(offender_id)}), now))
            owner_epoch = conn.execute("SELECT life_epoch FROM wanderer_agents WHERE id=?", (owner,)).fetchone()["life_epoch"]
            conn.execute("UPDATE wanderer_penalty_allocations SET detail_json=? WHERE root_id=? AND beneficiary_id=? AND component=?", (_json({"derived": owner != int(offender_id), "life_epoch": int(owner_epoch)}), str(operation_id), owner, component))
            _debt(conn, owner, component, units)
            allocations.append({"owner_id": owner, "component": component, "units": units, "derived": owner != int(offender_id)})
    # First settle everyone, then evaluate deaths, retaining the original graph.
    for owner in [int(offender_id), *partners]:
        replay_debts(conn, owner, check_death=False)
    from .wanderer_identity_service import load_wanderer, death_reason, mark_dead
    from .heavenly_dao_service import _record
    for owner in [int(offender_id), *partners]:
        fresh = load_wanderer(conn, owner)
        own = [a for a in allocations if a["owner_id"] == owner]
        shares = "、".join(f"{a['component']}={a['units'] if a['component'].endswith('seconds') else a['units']/SCALE:g}" for a in own)
        if notify_offender or owner != int(offender_id):
            _record(conn, fresh, kind="penalty", verdict="deducted", from_tier=fresh["tier"], to_tier=fresh["tier"],
                    reason=f"{str(reason).splitlines()[0][:120]}；本次{'承责' if owner != int(offender_id) else '本责'}：{shares}。", evidence={"root_penalty_id": operation_id, "derived": owner != int(offender_id), "allocations": own})
        dead = death_reason(conn, fresh)
        if dead and fresh["status"] != "dead":
            mark_dead(conn, owner, f"天罚：{str(reason)[:160]}；{dead}")
    result = {"penalty_id": str(operation_id), "allocations": allocations, "partners": partners, "reason": str(reason).splitlines()[0][:200]}
    conn.execute("UPDATE wanderer_penalty_roots SET result_json=? WHERE id=?", (_json(result), str(operation_id)))
    from .wanderer_bond_service import _invalidate
    _invalidate(conn,[int(offender_id),*partners])
    return result


def wipe(conn, offender_id, *, reason, operation_id):
    from .wanderer_identity_service import load_wanderer, current_life
    ensure_wanderer_world_schema(conn)
    partners = direct_partners(conn, offender_id)
    lock_lifecycle(conn, [offender_id, *partners])
    remaining = int(current_life(load_wanderer(conn, offender_id))["remaining_seconds"])
    # ceil(5R/4) makes the offender's 80% exactly R integer seconds.
    total = (5 * remaining + 3) // 4 if partners else remaining
    return apply_penalty(conn, offender_id, {"life_seconds": total}, reason=reason, operation_id=operation_id)


def pardon_warnings(conn, wanderer_id, *, reason):
    """Revoke original warning judgements, including their original shared burden."""
    ensure_wanderer_world_schema(conn)
    def pending():
        return [dict(r) for r in conn.execute('SELECT p.* FROM wanderer_penalty_roots p LEFT JOIN wanderer_penalty_reversals r ON r.root_id=p.id WHERE p.offender_id=? AND r.root_id IS NULL ORDER BY p.created_at,p.id',(int(wanderer_id),)).fetchall() if 'warning' in json.loads(r['components_json'])]
    roots=pending()
    if any(set(json.loads(row['components_json'])) != {'warning'} for row in roots):
        raise ValueError('警告包含复合裁决，请按原裁决完整复核后撤回')
    owners={int(wanderer_id)}
    for root in roots:
        owners.update(int(r['beneficiary_id']) for r in conn.execute('SELECT beneficiary_id FROM wanderer_penalty_allocations WHERE root_id=?',(root['id'],)).fetchall())
    lock_agents(conn,owners)
    if {r['id'] for r in pending()} != {r['id'] for r in roots}:
        raise ValueError('警告裁决刚发生变化，请重新载入后撤回')
    for root in roots:
        reverse_penalty(conn,root['id'],reason=reason,operation_id=f"pardon-warning:{root['id']}")
    return {'revoked_roots':[r['id'] for r in roots]}


def reverse_penalty(conn, root_id, *, reason, operation_id):
    """Reverse original allocations, independent of today's relationship graph."""
    ensure_wanderer_world_schema(conn)
    rows = conn.execute("SELECT * FROM wanderer_penalty_allocations WHERE root_id=? ORDER BY beneficiary_id,component", (str(root_id),)).fetchall()
    if not rows:
        raise ValueError("原处罚不存在")
    lock_agents(conn, [r["beneficiary_id"] for r in rows])
    old = conn.execute("SELECT result_json FROM wanderer_penalty_reversals WHERE root_id=?", (str(root_id),)).fetchone()
    if old:
        return json.loads(old["result_json"])
    from .wanderer_identity_service import load_wanderer
    from .wanderer_life_core import lifespan_weeks_for, normalize_talents, talent_bounds

    def undo_tier(owner, amount):
        debt = conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component='tier'", (owner,)).fetchone()
        balance = int(debt["balance_units"]) if debt else 0
        restore = max(0, (amount-balance+SCALE-1)//SCALE)
        new_balance = balance + restore*SCALE-amount
        conn.execute("INSERT INTO wanderer_penalty_debts(wanderer_id,component,balance_units,updated_at) VALUES(?,'tier',?,?) ON CONFLICT(wanderer_id,component) DO UPDATE SET balance_units=excluded.balance_units,updated_at=excluded.updated_at", (owner,new_balance,local_iso()))
        fresh = load_wanderer(conn, owner)
        tier = min(10, int(fresh["tier"])+restore)
        conn.execute("UPDATE wanderer_agents SET tier=?,lifespan_weeks=? WHERE id=?", (tier, lifespan_weeks_for(tier,fresh["talents"]["vitality"]),owner))
        return restore

    results = []
    for row in rows:
        owner, component = int(row["beneficiary_id"]), row["component"]
        amount, paid = int(row["amount_units"]), int(row["applied_units"])
        fresh = load_wanderer(conn, owner)
        debt = conn.execute("SELECT balance_units FROM wanderer_penalty_debts WHERE wanderer_id=? AND component=?", (owner,component)).fetchone()
        balance = int(debt["balance_units"]) if debt else 0
        if component == "tier":
            undo_tier(owner,amount)
        elif component == "warning":
            thresholds = max(0,(amount-balance+3*SCALE-1)//(3*SCALE))
            conn.execute("UPDATE wanderer_penalty_debts SET balance_units=?,updated_at=? WHERE wanderer_id=? AND component='warning'", (balance+thresholds*3*SCALE-amount,local_iso(),owner))
            if thresholds:
                undo_tier(owner,thresholds*SCALE)
        else:
            same_life = component != "life_seconds" or int(json.loads(row["detail_json"])["life_epoch"]) == int(fresh["life_epoch"])
            unpaid = min(balance,max(0,amount-paid)) if same_life else 0
            conn.execute("UPDATE wanderer_penalty_debts SET balance_units=balance_units-?,updated_at=? WHERE wanderer_id=? AND component=?", (unpaid,local_iso(),owner,component))
            if component == "life_seconds":
                if int(json.loads(row["detail_json"])["life_epoch"]) == int(fresh["life_epoch"]):
                    conn.execute("UPDATE wanderer_agents SET life_penalty_weeks=? WHERE id=?", (max(0,float(fresh['life_penalty_weeks'])-paid/604800),owner))
            elif component == "cultivation":
                conn.execute("UPDATE wanderer_agents SET cultivation_score=cultivation_score+? WHERE id=?", (paid/SCALE,owner))
            elif component.startswith("talent:"):
                key = component.split(":",1)[1]
                talents = normalize_talents(fresh["talents"],slug=fresh["slug"])
                talents[key] = min(talent_bounds(key)[1],talents[key]+paid/SCALE)
                conn.execute("UPDATE wanderer_agents SET talents_json=? WHERE id=?",(_json(talents),owner))
                if key == "vitality":
                    conn.execute("UPDATE wanderer_agents SET lifespan_weeks=? WHERE id=?",(lifespan_weeks_for(fresh["tier"],talents[key]),owner))
            elif component == "interaction_seconds":
                from datetime import timedelta
                from ..time_utils import parse_datetime, local_now
                until = parse_datetime(fresh.get("interaction_penalty_until"))
                if until:
                    conn.execute("UPDATE wanderer_agents SET interaction_penalty_until=? WHERE id=?",(max(local_now(),until-timedelta(seconds=paid)).isoformat(timespec="seconds"),owner))
        results.append({"owner_id":owner,"component":component,"amount_units":amount,"applied_units":paid})
    result={"root_penalty_id":str(root_id),"reversed":results,"reason":str(reason)[:500]}
    conn.execute("INSERT INTO wanderer_penalty_reversals(root_id,operation_id,reason,result_json,created_at) VALUES(?,?,?,?,?)",(str(root_id),str(operation_id),str(reason),_json(result),local_iso()))
    from .wanderer_bond_service import _invalidate
    _invalidate(conn,[r["beneficiary_id"] for r in rows])
    from .heavenly_dao_service import _record
    for owner in sorted({int(r['beneficiary_id']) for r in rows}):
        fresh=load_wanderer(conn,owner)
        _record(conn,fresh,kind='appeal',verdict='upheld',from_tier=fresh['tier'],to_tier=fresh['tier'],
                reason=f"原处罚已撤回：{str(reason).splitlines()[0][:120]}",
                evidence={'root_penalty_id':str(root_id),'reversal_operation_id':str(operation_id),'original_allocation_restored':True})
    return result
