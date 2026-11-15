"""Client contracts shared by HTTP and realtime handlers."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from itmo_queue.api.schemas.session import SessionRead
from itmo_queue.models import AttemptStatus, EntryStatus, ResultKind, SwapStatus, UserRole


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class EntryArtifact(StrictModel):
    work_id: uuid.UUID
    artifact_url: HttpUrl = Field(max_length=2000)


class Enrollment(StrictModel):
    works: list[EntryArtifact] = Field(min_length=1, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def legacy_single_work(cls, data):
        if isinstance(data, dict) and "works" not in data and "work_id" in data:
            return {
                "works": [{"work_id": data["work_id"], "artifact_url": data.get("artifact_url")}]
            }
        return data

    @model_validator(mode="after")
    def unique_works(self):
        if len({w.work_id for w in self.works}) != len(self.works):
            raise ValueError("Work IDs must be unique")
        return self


class ParticipantWork(StrictModel):
    work_id: uuid.UUID
    title: str
    priority: int
    artifact_url: str | None = None


class Participant(StrictModel):
    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    queue_id: uuid.UUID
    status: EntryStatus
    position: int | None
    is_reserve: bool
    is_top3: bool
    self_moves_count: int
    teacher_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None
    works: list[ParticipantWork]
    # Compatibility with the first single-work client; use works in new clients.
    work_id: uuid.UUID | None = Field(default=None, deprecated=True)
    artifact_url: str | None = Field(default=None, deprecated=True)


class SwapRead(StrictModel):
    swap_id: uuid.UUID
    queue_id: uuid.UUID
    from_entry_id: uuid.UUID
    to_entry_id: uuid.UUID
    from_student_id: uuid.UUID
    to_student_id: uuid.UUID
    from_position: int | None
    to_position: int | None
    status: SwapStatus
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


class Reason(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)


class ParticipantAction(StrictModel):
    status: Literal["called", "in_defense", "waiting"]
    attempt_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    confirm_out_of_order: bool = False


class WorkResult(StrictModel):
    work_id: uuid.UUID
    kind: ResultKind
    score: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_score(self):
        if (self.kind == ResultKind.score) != (self.score is not None):
            raise ValueError("Only score results require a numeric score")
        return self


class Evaluation(StrictModel):
    attempt_id: uuid.UUID
    status: Literal["accepted", "absent"]
    results: list[WorkResult] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_results(self):
        if self.status == "absent" and self.results:
            raise ValueError("No-show attempts have no work grades")
        if self.status == "accepted" and not self.results:
            raise ValueError("At least one evaluated work is required")
        if len({r.work_id for r in self.results}) != len(self.results):
            raise ValueError("Results must have unique work IDs")
        return self


class AttemptRead(StrictModel):
    id: uuid.UUID
    entry_id: uuid.UUID
    teacher_id: uuid.UUID
    status: AttemptStatus
    called_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    called_out_of_order: bool


class HistoryItem(AttemptRead):
    queue_id: uuid.UUID
    queue_title: str
    discipline_id: uuid.UUID
    discipline_name: str
    student_id: uuid.UUID
    student_name: str
    teacher_name: str
    works: list[ParticipantWork]
    results: list[WorkResult]


class UserProfile(StrictModel):
    id: uuid.UUID
    name: str
    login: str
    isu: str | None
    role: UserRole
    is_active: bool
    can_create_queues: bool
    stream_id: uuid.UUID | None


class UserCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    login: str = Field(min_length=3, max_length=100)
    isu: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    password: str = Field(min_length=8, max_length=1024, json_schema_extra={"writeOnly": True})
    role: UserRole = UserRole.student
    can_create_queues: bool = False
    stream_id: uuid.UUID | None = None


class NotificationRead(StrictModel):
    id: uuid.UUID
    queue_id: uuid.UUID | None
    type: str
    payload: dict
    created_at: datetime
    read_at: datetime | None


class TeacherRead(StrictModel):
    id: uuid.UUID
    name: str
    attempt_id: uuid.UUID | None
    entry_id: uuid.UUID | None
    is_busy: bool
    is_active: bool


class QueueDetail(SessionRead):
    discipline_name: str
    teachers: list[TeacherRead]
    students: list[Participant]
    waiting_count: int
    my_entry: Participant | None
    estimated_wait_minutes: int | None
    wait_status: Literal[
        "preliminary",
        "finished",
        "unavailable",
        "called",
        "overdue",
        "next",
        "estimate",
        "waiting_for_start",
    ]


class BanRead(StrictModel):
    student_id: uuid.UUID
    student_name: str
    reason: str | None
    created_at: datetime
