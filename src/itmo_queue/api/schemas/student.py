import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

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