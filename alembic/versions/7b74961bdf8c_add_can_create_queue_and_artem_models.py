"""add can_create_queue and artem models

Revision ID: 7b74961bdf8c
Revises: 5524e0c29d8a
Create Date: ...
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "7b74961bdf8c"
down_revision: str | None = "5524e0c29d8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Все таблицы и колонки уже были созданы в предыдущих миграциях.
    pass


def downgrade() -> None:
    pass
