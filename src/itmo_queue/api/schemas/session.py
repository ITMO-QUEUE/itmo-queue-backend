import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from itmo_queue.models import QueueStage


class SessionWork(BaseModel):
    work_id: uuid.UUID
    priority: int = Field(ge=1)


class SessionCreateWork(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    priority: int = Field(ge=1)


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discipline_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    planned_start: AwareDatetime
    planned_end: AwareDatetime
    auditorium: str | None = Field(default=None, min_length=1, max_length=2000)
    onlineLink: str | None = Field(default=None, min_length=1, max_length=2000)
    avg_duration_min: int | None = Field(default=10, ge=1)
    quota: int | None = Field(default=None, ge=1)
    registration_open: bool
    teacher_ids: list[uuid.UUID] = Field(min_length=1)
    works: list[SessionCreateWork] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_session(self):
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if self.planned_end <= self.planned_start:
            raise ValueError("planned_end must be after planned_start")
        if not (
            (self.auditorium and self.auditorium.strip())
            or (self.onlineLink and self.onlineLink.strip())
        ):
            raise ValueError("auditorium or onlineLink is required")
        if len(set(self.teacher_ids)) != len(self.teacher_ids):
            raise ValueError("teacher_ids must be unique")
        if len({work.priority for work in self.works}) != len(self.works):
            raise ValueError("work priorities must be unique")
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
    stream_ids: list[uuid.UUID] | None = None
    registration_open: bool | None = None
    stage: QueueStage | None = None
    finish_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    teacher_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    works: list[SessionWork] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_update(self):
        for field in self.model_fields_set - {
            "description",
            "location",
            "avg_duration_min",
            "quota",
            "finish_reason",
        }:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.stream_ids is not None and len(set(self.stream_ids)) != len(self.stream_ids):
            raise ValueError("stream_ids must be unique")
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
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    finished_at: datetime | None
    version: int
    stream_ids: list[uuid.UUID]
    teacher_ids: list[uuid.UUID]
    works: list[SessionWork]


class SessionCreatedWork(SessionWork):
    title: str


class SessionCreated(BaseModel):
    id: uuid.UUID
    discipline_id: uuid.UUID
    title: str
    description: str | None
    planned_start: datetime
    planned_end: datetime
    auditorium: str | None
    onlineLink: str | None
    avg_duration_min: int | None
    quota: int | None
    stage: QueueStage
    registration_open: bool
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    finished_at: datetime | None
    version: int
    stream_id: uuid.UUID
    teacher_ids: list[uuid.UUID]
    works: list[SessionCreatedWork]


class RegistrationUpdate(BaseModel):
    open: bool


class ErrorResponse(BaseModel):
    detail: str | list[dict]


class Option(BaseModel):
    id: uuid.UUID
    name: str


class TeacherOption(Option):
    discipline_ids: list[uuid.UUID]


class SessionOptions(BaseModel):
    disciplines: list[Option]
    teachers: list[TeacherOption]
