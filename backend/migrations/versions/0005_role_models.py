"""Pin independent research and execution model connections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_role_models"
down_revision = "0004_product_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_sessions", sa.Column("model_binding", JSONB(), nullable=False, server_default="{}")
    )
    op.execute(
        "UPDATE agent_sessions s SET model_binding=c.model_binding "
        "FROM conversations c WHERE s.conversation_id=c.id"
    )
    op.alter_column("conversations", "model_binding", new_column_name="model_bindings")
    op.execute(
        "UPDATE conversations SET model_bindings=jsonb_build_object("
        "'research',model_bindings,'execution',model_bindings)"
    )
    op.execute(
        "UPDATE user_settings SET content=(content-'connectionId'-'model') || "
        "jsonb_build_object('models',jsonb_build_object("
        "'research',jsonb_build_object('connectionId',content->'connectionId','model',content->'model'),"
        "'execution',jsonb_build_object('connectionId',content->'connectionId','model',content->'model')))"
    )
    op.execute(
        "UPDATE model_connections SET config=config || jsonb_build_object('provider',"
        "CASE WHEN id='connection-default' THEN 'cliproxy' ELSE 'custom' END)"
    )


def downgrade():
    raise RuntimeError("Independent model bindings and historical references must be preserved")
