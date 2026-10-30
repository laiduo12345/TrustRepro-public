"""Keep cancelled execution intent terminal after progression is introduced."""

from alembic import op

revision = "0003_terminal_intent"
down_revision = "0002_progression"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE agent_sessions SET desired_state='STOPPED', status='CANCELLED' "
        "WHERE kind='execution' AND run_id IN "
        "(SELECT id FROM runs WHERE status IN ('CANCELLED','CANCELLING'))"
    )


def downgrade():
    raise RuntimeError("Cancelled intent cannot be restored to an active state")
