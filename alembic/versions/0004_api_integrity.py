"""Protect current participation and index durable realtime delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0004_api_integrity"
down_revision = "7b74961bdf8c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_current_student_entry",
        "queue_entries",
        ["queue_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('waiting', 'called', 'in_defense')"),
    )
    op.create_index(
        "uq_current_teacher_attempt",
        "attempts",
        ["teacher_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('called', 'in_progress')"),
    )
    op.create_index(
        "uq_current_entry_attempt",
        "attempts",
        ["entry_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('called', 'in_progress')"),
    )
    op.create_index("ix_queue_events_queue_id_id", "queue_events", ["queue_id", "id"])
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index("ix_swap_offers_queue_status", "swap_offers", ["queue_id", "status"])


def downgrade():
    for name, table in [
        ("ix_swap_offers_queue_status", "swap_offers"),
        ("ix_notifications_user_created", "notifications"),
        ("ix_queue_events_queue_id_id", "queue_events"),
        ("uq_current_entry_attempt", "attempts"),
        ("uq_current_teacher_attempt", "attempts"),
        ("uq_current_student_entry", "queue_entries"),
    ]:
        op.drop_index(name, table_name=table)
