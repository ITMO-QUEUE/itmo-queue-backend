import uuid

from pydantic import BaseModel, ConfigDict, Field


class EntryCreate(BaseModel):
    work_id: uuid.UUID = Field(..., description="ID разрешенной работы")
    artifact_url: str = Field(..., min_length=5, max_length=1000, description="Ссылка на работу")


class EntryRead(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    queue_id: uuid.UUID
    status: str
    position: int
    is_reserve: bool
    work_id: uuid.UUID
    artifact_url: str

    model_config = ConfigDict(from_attributes=True)
