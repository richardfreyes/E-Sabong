import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.modules.betting.models import BookState
from app.modules.betting.schemas import FightBookSummary
from app.modules.declarator.models import DeclarationOutcome, DeclarationStatus
from app.modules.events.schemas import EventOut, MatchOut, ResultHistoryOut
from app.shared.schemas import Schema


class DeclarationCreate(Schema):
    request_id: uuid.UUID
    outcome: DeclarationOutcome
    base_revision: Annotated[int, Field(ge=0)] = 0
    reason: Annotated[
        str | None,
        Field(
            max_length=500, description="Cancellation reasons are limited to 300 characters; other declaration reasons to 500."
        ),
    ] = None

    @model_validator(mode="after")
    def amendment_reason(self) -> "DeclarationCreate":
        if (self.base_revision or self.outcome == DeclarationOutcome.CANCELLED) and len(self.reason or "") < 3:
            raise ValueError("An amendment or cancellation requires a reason of at least 3 characters")
        if self.outcome == DeclarationOutcome.CANCELLED and len(self.reason or "") > 300:
            raise ValueError("Cancellation reasons must not exceed 300 characters")
        return self


class ReviewRequest(Schema):
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class DeclarationOut(Schema):
    id: uuid.UUID
    match_id: uuid.UUID
    outcome: DeclarationOutcome
    status: DeclarationStatus
    base_revision: int
    reason: str | None
    declared_by_id: uuid.UUID
    declared_by: str
    declared_at: datetime
    approved_by_id: uuid.UUID | None
    approved_by: str | None
    finalized_at: datetime | None


class MatchRecord(Schema):
    match: MatchOut
    event_name: str
    workflow_status: DeclarationStatus
    official_winner: DeclarationOutcome | None
    declaration: DeclarationOut | None
    declared_by: str | None
    declared_at: datetime | None
    approved_by: str | None
    finalized_at: datetime | None


class AuditOut(Schema):
    id: uuid.UUID
    declaration_id: uuid.UUID
    actor: str
    action: str
    from_status: str | None
    to_status: str
    reason: str | None
    created_at: datetime


class MatchHistory(Schema):
    record: MatchRecord
    declarations: list[DeclarationOut]
    audit: list[AuditOut]
    revisions: list[ResultHistoryOut]


class BettingControl(Schema):
    """The Declarator's betting switch for the event, computed and enforced by the server.

    ``match_id`` is the fight the switch acts on: the fight in progress, otherwise the next
    scheduled fight. ``status`` is OPEN only while the backend accepts new simulated bets."""

    match_id: uuid.UUID | None
    match_number: int | None
    match_status: str | None
    status: Literal["open", "closed"]
    book_state: BookState | None
    can_open: bool
    can_close: bool
    blocked_reason: str | None
    next_match_number: int | None


class FightDetails(Schema):
    """Entries of the next fight as the Declarator announces them (never invented by the system)."""

    meron_name: str = Field(min_length=1, max_length=120)
    meron_details: str | None = Field(default=None, max_length=500)
    wala_name: str = Field(min_length=1, max_length=120)
    wala_details: str | None = Field(default=None, max_length=500)


class FightDetailsUpdate(Schema):
    meron_name: str | None = Field(default=None, min_length=1, max_length=120)
    meron_details: str | None = Field(default=None, max_length=500)
    wala_name: str | None = Field(default=None, min_length=1, max_length=120)
    wala_details: str | None = Field(default=None, max_length=500)


class MonitorOut(Schema):
    event: EventOut
    matches: list[MatchOut]
    current: MatchRecord | None
    # Simulated betting on this card: aggregate pools, odds, obligations and settlements only.
    books: list[FightBookSummary] = []
    betting: BettingControl | None = None


class AssignmentOut(Schema):
    user_id: uuid.UUID
    username: str
    assigned_at: datetime


class EligibleDeclarator(Schema):
    id: uuid.UUID
    username: str
