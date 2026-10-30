"""Delete provider credentials without losing historical connection references."""

import sqlalchemy as sa
from alembic import op

revision = "0007_provider_delete"
down_revision = "0006_provider_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "model_connections",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    raise RuntimeError("Deleted providers must not reappear or regain access")
