import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from itmo_queue.models import QueueStage


class SessionWork(BaseModel):
    work_id: uuid.UUID
    priority: int = Field(ge=1)


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discipline_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    planned_start: AwareDatetime
    planned_end: AwareDatetime
    location: str | None = Field(default=None, max_length=2000)
    avg_duration_min: int | None = Field(default=None, ge=1)
    quota: int | None = Field(default=None, ge=1)
    teacher_ids: list[uuid.UUID] = Field(min_length=1)
    works: list[SessionWork] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_session(self):
        if self.planned_end <= self.planned_start:
            raise ValueError("planned_end must be after planned_start")
        if len(set(self.teacher_ids)) != len(self.teacher_ids):
            raise ValueError("teacher_ids must be unique")
        if len({work.work_id for work in self.works}) != len(self.works):
            raise ValueError("work_ids must be unique")
        return self


class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    planned_start: AwareDatetime | None = None
    planned_end: AwareDatetime | None = None
    location: str | None = Field(default=None, max_length=2000)
    avg_duration_min: int | None = Field(default=None, ge=1)
    quota: int | None = Field(default=None, ge=1)
    teacher_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    works: list[SessionWork] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_update(self):
        for field in self.model_fields_set - {
            "description",
            "location",
            "avg_duration_min",
            "quota",
        }:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.teacher_ids and len(set(self.teacher_ids)) != len(self.teacher_ids):
            raise ValueError("teacher_ids must be unique")
        if self.works and len({w.work_id for w in self.works}) != len(self.works):
            raise ValueError("work_ids must be unique")
        return self


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    discipline_id: uuid.UUID
    title: str
    description: str | None
    planned_start: datetime
    planned_end: datetime
    location: str | None
    avg_duration_min: int | None
    quota: int | None
    stage: QueueStage
    registration_open: bool
    teacher_ids: list[uuid.UUID]
    works: list[SessionWork]


class RegistrationUpdate(BaseModel):
    open: bool


class ErrorResponse(BaseModel):
    detail: str | list[dict]


class Option(BaseModel):
    id: uuid.UUID
    name: str


class WorkOption(BaseModel):
    id: uuid.UUID
    discipline_id: uuid.UUID
    title: str
    position_no: int


class SessionOptions(BaseModel):
    disciplines: list[Option]
    teachers: list[Option]
    works: list[WorkOption]
