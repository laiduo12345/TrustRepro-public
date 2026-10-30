"""Shared ownership, account activity and authorization for every executor."""

import secrets
from decimal import Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.money import CURRENCY
from trustrepro.domain.runtime import merged_ms
from trustrepro.infrastructure.db.models import (
    Account,
    Activity,
    AgentSession,
    Grant,
    Job,
    ModelRequest,
    Run,
    now_ms,
)

LEASE_MS = 60000
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
WORKSPACE_WRITES = ("write_file", "apply_patch", "publish", "import")


def fence(row, owner, version):
    if row.owner != owner or row.owner_version != version or row.lease_until <= now_ms():
        raise BusinessError("OWNER_EXPIRED", "作业所有权已失效")


def authenticate(db, token, *, lock=False):
    query = select(AgentSession).where(AgentSession.lease_token == token)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if not token or row is None or row.lease_until <= now_ms() or row.status != "RUNNING":
        raise BusinessError("OWNER_EXPIRED", "会话所有权已失效")
    return row


def activity(db, row, prefix, *, stop=False):
    stamp = now_ms()
    key = f"{prefix}:{row.id}:{row.owner_version}"
    if prefix == "agent" and row.activity_epoch:
        key += f":{row.activity_epoch}"
    current = db.get(Activity, key)
    if current is None and not stop:
        current = Activity(
            id=key,
            account_id=row.account_id,
            usage_scope="research"
            if getattr(row, "kind", None) == "research" or getattr(row, "scope", None) == "research"
            else "reproduction",
            owner_version=row.owner_version,
            started_at=stamp,
            lease_until=stamp + LEASE_MS,
        )
        db.add(current)
    if current and current.ended_at is None:
        if stop:
            current.ended_at = min(stamp, current.lease_until)
        else:
            current.lease_until = stamp + LEASE_MS


def budget(db, account_id, scope="execution"):
    return budgets(db, [account_id], scope)[account_id]


def budgets(db, account_ids, scope="execution"):
    """The same account calculation for one account or a page of projects."""
    if not account_ids:
        return {}
    research = scope == "research"
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(account_ids)))}
    grants = {}
    for grant in db.scalars(
        select(Grant).where(Grant.account_id.in_(account_ids), Grant.scope.in_([scope, "all"]))
    ):
        grants.setdefault(grant.account_id, []).append(grant)
    costs = {
        row[0]: row[1:]
        for row in db.execute(
            select(
                ModelRequest.account_id,
                func.coalesce(func.sum(ModelRequest.actual_cost), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                ModelRequest.status.in_(["REQUESTED", "UNCERTAIN"]),
                                ModelRequest.reserved_cost,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(
                ModelRequest.account_id.in_(account_ids),
                ModelRequest.usage_scope == ("research" if research else "reproduction"),
            )
            .group_by(ModelRequest.account_id)
        )
    }
    stamp = now_ms()
    intervals = {}
    for identifier, start, end, lease in db.execute(
        select(
            Activity.account_id, Activity.started_at, Activity.ended_at, Activity.lease_until
        ).where(
            Activity.account_id.in_(account_ids),
            Activity.usage_scope == ("research" if research else "reproduction"),
        )
    ):
        intervals.setdefault(identifier, []).append(
            (start, end if end is not None else min(stamp, lease))
        )
    values = {}
    for identifier, account in accounts.items():
        policy = account.policy if account.confirmed and not research else {}
        hours = Decimal(str(policy.get("maxHours", 0)))
        cost = Decimal(str(policy.get("maxCost", 0)))
        for grant in grants.get(identifier, []):
            hours = max(hours, grant.max_hours)
            if grant.max_cost is not None:
                cost = max(cost, grant.max_cost)
        spent, reserved = costs.get(identifier, (0, 0))
        seconds = merged_ms(intervals.get(identifier, [])) / 1000
        values[identifier] = {
            **policy,
            "currency": CURRENCY,
            "confirmed": account.confirmed,
            "maxHours": float(hours),
            "maxCost": float(cost),
            "limited": bool(policy),
            "spentCost": float(spent),
            "reservedCost": float(reserved),
            "elapsedSeconds": seconds,
            "remainingSeconds": max(0, float(hours) * 3600 - seconds),
        }
    return values


def require_budget(db, account_id, scope="execution", amount=0):
    value = budget(db, account_id, scope)
    if scope == "research":
        return value
    if not value["confirmed"]:
        raise BusinessError("APPROVAL_REQUIRED", "请先确认复现方案")
    if value["remainingSeconds"] <= 0:
        raise BusinessError("TIME_BUDGET_EXCEEDED", "已达到批准的处理时长")
    if sum(Decimal(str(value[k])) for k in ("spentCost", "reservedCost")) + Decimal(
        str(amount)
    ) > Decimal(str(value["maxCost"])):
        raise BusinessError("COST_BUDGET_EXCEEDED", "剩余费用额度不足")
    return value


def require_run(db, run_id, scope="execution", version=None):
    row = db.get(Run, run_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "运行不存在")
    if version is not None and row.authorization_version != version:
        raise BusinessError("AUTHORIZATION_CHANGED", "运行授权已变更")
    if scope == "execution" and row.status != "RUNNING":
        raise BusinessError("RUN_STOPPED", "运行已停止")
    require_budget(db, row.account_id, scope)
    return row


def claim(db, model, owner, kinds=None):
    query = select(model).where(
        or_(model.status == "QUEUED", (model.status == "RUNNING") & (model.lease_until < now_ms()))
    )
    if kinds:
        query = query.where(model.kind.in_(kinds))
    query = query.where(model.retry_at <= now_ms())
    if model is AgentSession:
        query = query.where(model.status == "QUEUED", model.desired_state == "ACTIVE")
    if model is Job:
        writer = aliased(Job)
        busy_workspace = (
            select(writer.id)
            .where(
                writer.run_id == Job.run_id,
                writer.id != Job.id,
                writer.kind.in_(WORKSPACE_WRITES),
                writer.status == "RUNNING",
                writer.lease_until >= now_ms(),
            )
            .exists()
        )
        query = query.where(or_(Job.kind.not_in(WORKSPACE_WRITES), ~busy_workspace))
        active_agent = (
            select(AgentSession.id)
            .where(
                AgentSession.run_id == Job.run_id, AgentSession.status.in_(["QUEUED", "RUNNING"])
            )
            .exists()
        )
        pending_model = (
            select(ModelRequest.id)
            .where(
                ModelRequest.account_id == Job.account_id,
                ModelRequest.usage_scope == "reproduction",
                ModelRequest.status == "REQUESTED",
            )
            .exists()
        )
        query = query.where(or_(Job.kind != "export", and_(~active_agent, ~pending_model)))
    row = db.scalar(query.order_by(model.created_at).with_for_update(skip_locked=True).limit(1))
    if row is None:
        return None
    if model is Job and row.run_id and row.kind in WORKSPACE_WRITES:
        run = db.scalar(select(Run).where(Run.id == row.run_id).with_for_update(skip_locked=True))
        if run is None:
            return None
        writing = db.scalar(
            select(Job.id).where(
                Job.run_id == row.run_id,
                Job.id != row.id,
                Job.kind.in_(WORKSPACE_WRITES),
                Job.status == "RUNNING",
                Job.lease_until >= now_ms(),
            )
        )
        if writing:
            return None
    activity(db, row, "agent" if model is AgentSession else "job", stop=True)
    row.owner, row.owner_version = owner, row.owner_version + 1
    row.lease_until, row.status = now_ms() + LEASE_MS, "RUNNING"
    if model is AgentSession:
        row.lease_token = secrets.token_urlsafe(40)
    elif row.started_at is None:
        row.started_at = now_ms()
    activity(db, row, "agent" if model is AgentSession else "job")
    return row


def heartbeat(db, model, identifier, owner, version):
    row = db.get(model, identifier, with_for_update=True)
    fence(row, owner, version)
    row.lease_until = now_ms() + LEASE_MS
    if model is not AgentSession or row.state.get("phase") != "waiting_user":
        activity(db, row, "agent" if model is AgentSession else "job")
    return row
