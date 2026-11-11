import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StudentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Иванов Иван"])
    login: str = Field(..., min_length=3, max_length=100, examples=["ivanov"])
    isu: str = Field(..., min_length=1, max_length=50, examples=["123456"])


class StudentCreate(StudentBase):
    password: str = Field(..., min_length=6, description="Пароль (будет захеширован)")


class StudentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    login: str | None = Field(None, min_length=3, max_length=100)
    isu: str | None = Field(None, min_length=1, max_length=50)
    password: str | None = Field(None, min_length=6)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data: dict):
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None:
                    raise ValueError(f"Field '{key}' cannot be null")
        return data


class StudentRead(StudentBase):
    id: uuid.UUID
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StudentList(BaseModel):
    items: list[StudentRead]
    total: int
    limit: int
    offset: int
