"""Add separate defense locations while retaining legacy location data."""

import sqlalchemy as sa
from alembic import op

revision = "0005_queue_locations"
down_revision = "0004_api_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("queues", sa.Column("auditorium", sa.String(2000), nullable=True))
    op.add_column("queues", sa.Column("online_link", sa.String(2000), nullable=True))


def downgrade():
    op.drop_column("queues", "online_link")
    op.drop_column("queues", "auditorium")
