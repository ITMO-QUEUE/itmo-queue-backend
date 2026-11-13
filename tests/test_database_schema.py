from alembic.config import Config
from alembic.script import ScriptDirectory

import itmo_queue.models as models
from itmo_queue.db.base import Base
from itmo_queue.db.models.group import Group
from itmo_queue.db.models.user import User


def test_models_share_one_user_table():
    assert models.User is User
    assert Base.metadata.tables["users"] is User.__table__
    assert Base.metadata.tables["groups"] is Group.__table__
    assert {"name", "isu", "is_active", "stream_id", "can_create_queues"} <= set(
        User.__table__.columns.keys()
    )
    assert Base.metadata.tables["queue_entries"].c.student_id.references(User.__table__.c.id)
    assert Base.metadata.sorted_tables


def test_migrations_have_one_linear_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["5524e0c29d8a"]
    assert scripts.get_revision("5524e0c29d8a").down_revision == "0001_initial"
