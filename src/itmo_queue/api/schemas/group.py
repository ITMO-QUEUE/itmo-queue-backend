import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# Схема для создания (POST) - клиент присылает только имя
class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["K3161"])


# Схема для обновления (PATCH) - клиент может изменить имя
class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)


# Схема для ответа (GET) - сервер отдает все данные, включая даты
class GroupRead(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime

    # Эта настройка позволяет Pydantic читать данные из SQLAlchemy моделей
    model_config = ConfigDict(from_attributes=True)


# Схема для списка с пагинацией
class GroupList(BaseModel):
    items: list[GroupRead]
    total: int
    limit: int
    offset: int
