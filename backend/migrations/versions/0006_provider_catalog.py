"""A provider API owns a catalog of models, each with its own price."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_provider_catalog"
down_revision = "0005_role_models"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    connections = sa.table("model_connections", sa.column("id"), sa.column("config", JSONB))
    references = []
    for value in db.execute(sa.text("SELECT model_bindings FROM conversations")).scalars():
        references.extend(value.values())
    references.extend(db.execute(sa.text("SELECT model_binding FROM agent_sessions")).scalars())
    for value in db.execute(sa.text("SELECT content FROM user_settings")).scalars():
        references.extend(value.get("models", {}).values())
    for row in db.execute(sa.select(connections)).mappings():
        config = dict(row["config"])
        default = {k: config.pop(k) for k in ("model", "inputRate", "outputRate")}
        models = {default["model"]: default}
        for ref in references:
            if ref.get("connectionId") == row["id"] and ref.get("model"):
                models.setdefault(ref["model"], {**default, "model": ref["model"]})
        config["models"] = list(models.values())
        db.execute(connections.update().where(connections.c.id == row["id"]).values(config=config))


def downgrade():
    raise RuntimeError("Provider catalogs and historical model references must be preserved")
