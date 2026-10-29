import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.modules.events.models import EventStatus
from app.modules.matches.models import MatchStatus
from app.modules.results.models import MatchOutcome
from app.shared.schemas import Schema


class EventOut(Schema):
    id: uuid.UUID
    name: str
    venue: str | None
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    status: EventStatus
    created_at: datetime
    match_count: int = 0
    completed_match_count: int = 0


class EventCreate(Schema):
    name: str = Field(min_length=2, max_length=200)
    venue: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    starts_at: datetime
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> "EventCreate":
        if self.starts_at.tzinfo is None or (self.ends_at and self.ends_at.tzinfo is None):
            raise ValueError("Timestamps must include a timezone offset")
        if self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EventUpdate(Schema):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    venue: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ResultOut(Schema):
    outcome: MatchOutcome
    notes: str | None
    declared_at: datetime
    revision: int


class MatchOut(Schema):
    id: uuid.UUID
    event_id: uuid.UUID
    event_name: str | None = None
    match_number: int
    meron_name: str
    meron_details: str | None
    wala_name: str
    wala_details: str | None
    scheduled_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    status: MatchStatus
    cancel_reason: str | None
    result: ResultOut | None = None


class MatchCreate(Schema):
    match_number: int | None = Field(default=None, ge=1, le=100_000)
    meron_name: str = Field(min_length=1, max_length=120)
    meron_details: str | None = Field(default=None, max_length=500)
    wala_name: str = Field(min_length=1, max_length=120)
    wala_details: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None


class MatchUpdate(Schema):
    meron_name: str | None = Field(default=None, min_length=1, max_length=120)
    meron_details: str | None = Field(default=None, max_length=500)
    wala_name: str | None = Field(default=None, min_length=1, max_length=120)
    wala_details: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None


class MatchCancel(Schema):
    reason: str = Field(min_length=1, max_length=300)


class ResultDeclare(Schema):
    outcome: MatchOutcome
    notes: str | None = Field(default=None, max_length=500)


class ResultCorrect(Schema):
    outcome: MatchOutcome
    reason: str = Field(min_length=3, max_length=500)
    notes: str | None = Field(default=None, max_length=500)


class ResultHistoryOut(Schema):
    id: uuid.UUID
    revision: int
    outcome: MatchOutcome
    previous_outcome: MatchOutcome | None
    reason: str | None
    changed_by_id: uuid.UUID
    changed_by_username: str | None = None
    created_at: datetime
