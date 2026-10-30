"""Separate usage domains and persist editable product configuration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_product_settings"
down_revision = "0003_terminal_intent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("grants", sa.Column("max_cost", sa.Numeric(20, 8)))
    for table in ("activities", "model_requests"):
        op.add_column(
            table,
            sa.Column("usage_scope", sa.String(24), nullable=False, server_default="reproduction"),
        )
    for name in ("pricing", "connection"):
        op.add_column(
            "model_requests", sa.Column(name, JSONB(), nullable=False, server_default="{}")
        )
    op.add_column(
        "conversations",
        sa.Column("input_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations", sa.Column("model_binding", JSONB(), nullable=False, server_default="{}")
    )
    for name, default in (("revision", "1"), ("input_version", "0")):
        op.add_column(
            "cards", sa.Column(name, sa.Integer(), nullable=False, server_default=default)
        )
    op.add_column("cards", sa.Column("edit_receipt", JSONB(), nullable=False, server_default="{}"))
    op.create_table(
        "model_connections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "user_settings",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
    )
    op.create_table(
        "connection_tests",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column(
            "connection_id", sa.String(64), sa.ForeignKey("model_connections.id"), nullable=False
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.execute(
        "UPDATE model_requests SET usage_scope='research' WHERE session_id IN "
        "(SELECT id FROM agent_sessions WHERE kind='research')"
    )
    op.execute(
        "UPDATE activities SET usage_scope='research' WHERE EXISTS "
        "(SELECT 1 FROM agent_sessions s WHERE s.kind='research' "
        "AND activities.id LIKE 'agent:' || s.id || ':%')"
    )
    op.execute("UPDATE accounts SET policy='{}' WHERE confirmed=false")


def downgrade():
    raise RuntimeError("Usage and model configuration history must be preserved")
