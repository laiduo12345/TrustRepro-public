"""Record new project creation times without inventing dates for existing projects."""

import sqlalchemy as sa
from alembic import op

revision = "0008_project_created_at"
down_revision = "0007_provider_delete"
branch_labels = None
depends_on = None


def upgrade():
    # Add without a default first: historical rows must remain unknown.
    op.add_column("projects", sa.Column("created_at", sa.BigInteger(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "projects",
            "created_at",
            server_default=sa.text("(extract(epoch from clock_timestamp()) * 1000)::bigint"),
        )


def downgrade():
    raise RuntimeError("Project history must be retained")
