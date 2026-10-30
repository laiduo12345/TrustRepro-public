"""Convert the existing monetary representation to CNY without changing entitlement."""

import copy
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "0009_cny_accounting"
down_revision = "0008_project_created_at"
branch_labels = None
depends_on = None

# Frozen migration rate. Future configuration must never revalue this history.
RATE = Decimal("6.7")


def amount(value):
    if value is None:
        return None
    result = Decimal(str(value)) * RATE
    return format(result, "f") if isinstance(value, str) else float(result)


def prices(value):
    result = copy.deepcopy(value or {})
    if not result or result.get("currency") == "CNY":
        return result
    for key in ("inputRate", "outputRate"):
        if key in result:
            result[key] = amount(result[key])
    if "models" in result:
        result["models"] = [prices(model) for model in result["models"]]
    result["currency"] = "CNY"
    return result


def allocation(value):
    result = copy.deepcopy(value or {})
    if "maxCost" in result and result.get("currency") != "CNY":
        result["maxCost"] = amount(result["maxCost"])
        result["currency"] = "CNY"
    return result


def plan(value):
    result = copy.deepcopy(value)
    if result and "allocation" in result:
        result["allocation"] = allocation(result["allocation"])
    return result


def usage(values):
    result = copy.deepcopy(values or [])
    for item in result:
        if not isinstance(item, dict) or item.get("currency") == "CNY":
            continue
        for key in ("estimatedCost", "actualCost", "reservedCost"):
            if key in item:
                item[key] = amount(item[key])
        if "pricing" in item:
            item["pricing"] = prices(item["pricing"])
        item["currency"] = "CNY"
    return result


def budget(value):
    result = copy.deepcopy(value or {})
    if result.get("currency") == "CNY":
        return result
    for key in ("maxCost", "spentCost", "reservedCost", "remainingCost"):
        if key in result:
            result[key] = amount(result[key])
    result["currency"] = "CNY"
    return result


def publication(value):
    if value is None or value.get("currency") == "CNY":
        return value
    result = copy.deepcopy(value)
    if "approvedPlan" in result:
        result["approvedPlan"] = plan(result["approvedPlan"])
    if "budget" in result:
        result["budget"] = budget(result["budget"])
    if "usage" in result:
        result["usage"] = usage(result["usage"])
    result["currency"] = "CNY"
    result["currencyConversion"] = {"fromCurrency": "USD", "exchangeRate": str(RATE)}
    return result


def convert_rows(db):
    metadata = sa.MetaData()

    def update(name, names, convert):
        table = sa.Table(name, metadata, autoload_with=db)
        for row in db.execute(sa.select(table.c.id, *(table.c[n] for n in names))).mappings():
            values = convert(row)
            if values:
                db.execute(table.update().where(table.c.id == row["id"]).values(**values))

    update("accounts", ["policy"], lambda r: {"policy": allocation(r["policy"])})
    update(
        "grants",
        ["max_cost"],
        lambda r: {
            "max_cost": Decimal(str(r["max_cost"])) * RATE if r["max_cost"] is not None else None,
        },
    )

    def settings(row):
        value = copy.deepcopy(row["content"])
        if value.get("currency") == "CNY":
            return {}
        value.update(
            allocation=allocation(value["allocation"]), currency="CNY", usdToCnyRate=str(RATE)
        )
        return {"content": value, "revision": row["revision"] + 1}

    update("user_settings", ["content", "revision"], settings)
    update(
        "model_connections",
        ["config", "version"],
        lambda r: (
            {}
            if r["config"].get("currency") == "CNY"
            else {
                "config": prices(r["config"]),
                "version": r["version"] + 1,
            }
        ),
    )
    update(
        "conversations",
        ["model_bindings"],
        lambda r: {
            "model_bindings": {role: prices(value) for role, value in r["model_bindings"].items()},
        },
    )
    update(
        "agent_sessions", ["model_binding"], lambda r: {"model_binding": prices(r["model_binding"])}
    )

    def card(row):
        if row["content"].get("allocation", {}).get("currency") == "CNY":
            return {}
        receipt = copy.deepcopy(row["edit_receipt"])
        if receipt.get("allocation"):
            receipt["allocation"] = allocation(receipt["allocation"])
        return {
            "content": plan(row["content"]),
            "edit_receipt": receipt,
            "revision": row["revision"] + 1,
        }

    update("cards", ["content", "edit_receipt", "revision"], card)
    update("plans", ["content"], lambda r: {"content": plan(r["content"])})

    def request(row):
        if row["pricing"].get("currency") == "CNY":
            return {}
        pricing = prices(row["pricing"])
        # Keep the exact pre-conversion ledger values, including uncertain reservations.
        pricing.update(
            currency="CNY",
            currencyConversion={
                "fromCurrency": "USD",
                "exchangeRate": str(RATE),
                "originalPricing": copy.deepcopy(row["pricing"]),
                "reservedCost": str(row["reserved_cost"]),
                "actualCost": str(row["actual_cost"]) if row["actual_cost"] is not None else None,
            },
        )
        return {
            "pricing": pricing,
            "connection": prices(row["connection"]),
            "reserved_cost": row["reserved_cost"] * RATE,
            "actual_cost": row["actual_cost"] * RATE if row["actual_cost"] is not None else None,
        }

    update("model_requests", ["pricing", "connection", "reserved_cost", "actual_cost"], request)
    update(
        "reports",
        ["usage", "publication"],
        lambda r: {
            "usage": usage(r["usage"]),
            "publication": publication(r["publication"]),
        },
    )

    def connection_test(row):
        result = copy.deepcopy(row["result"])
        if result.get("currency") == "CNY":
            return {}
        if "connection" in result:
            result["connection"] = prices(result["connection"])
        if "calls" in result:
            result["calls"] = usage(result["calls"])
        result["currency"] = "CNY"
        return {"result": result}

    update("connection_tests", ["result"], connection_test)
    update(
        "jobs",
        ["kind", "result"],
        lambda r: (
            {
                "result": {**r["result"], "usage": usage(r["result"]["usage"])},
            }
            if r["kind"] == "export" and isinstance(r["result"], dict) and "usage" in r["result"]
            else {}
        ),
    )


def upgrade():
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        for table, columns in (
            ("grants", ("max_cost",)),
            ("model_requests", ("actual_cost", "reserved_cost")),
        ):
            for column in columns:
                op.alter_column(
                    table, column, existing_type=sa.Numeric(20, 8), type_=sa.Numeric(24, 10)
                )
    convert_rows(db)


def downgrade():
    raise RuntimeError("Currency conversion and original financial snapshots must be retained")
