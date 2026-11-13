import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from itmo_queue.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("login", name="uq_users_login"),
        UniqueConstraint("isu", name="uq_users_isu"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # У админов может не быть ИСУ
    isu: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Роль: "student", "admin" и т.д.
    role: Mapped[str] = mapped_column(String(50), default="student", server_default="student")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    can_create_queues: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    stream_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("streams.id")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
