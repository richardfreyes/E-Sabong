"""Offline domain checks. Database/RLS/transaction guarantees live in tests/test_declarator.py."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.errors import Conflict, Forbidden, Unprocessable
from app.core.permissions import SYSTEM_ROLES, P
from app.modules.declarator.models import DeclarationOutcome, DeclarationStatus, ResultDeclaration
from app.modules.declarator.schemas import DeclarationCreate
from app.modules.declarator.service import review, submit, validate_proposal
from app.modules.events.models import EventStatus
from app.modules.matches.models import MatchStatus
from app.modules.users.models import AccountType


def data(**kwargs):
    return DeclarationCreate(request_id=uuid.uuid4(), outcome="meron", **kwargs)


def test_valid_initial_and_amended_results():
    event = SimpleNamespace(status=EventStatus.LIVE)
    match = SimpleNamespace(status=MatchStatus.IN_PROGRESS)
    for outcome in DeclarationOutcome:
        proposal = DeclarationCreate(request_id=uuid.uuid4(), outcome=outcome, reason="Official decision")
        validate_proposal(event, match, None, proposal)
    match.status = MatchStatus.COMPLETED
    official = SimpleNamespace(revision=1, outcome=DeclarationOutcome.WALA)
    validate_proposal(event, match, official, data(base_revision=1, reason="Video reviewed"))


@pytest.mark.parametrize("state", [MatchStatus.SCHEDULED, MatchStatus.COMPLETED, MatchStatus.CANCELLED])
def test_initial_declaration_rejects_closed_or_unstarted_fights(state):
    with pytest.raises(Unprocessable):
        validate_proposal(SimpleNamespace(status=EventStatus.LIVE), SimpleNamespace(status=state), None, data())


@pytest.mark.parametrize("state", [EventStatus.DRAFT, EventStatus.SCHEDULED, EventStatus.CANCELLED, EventStatus.COMPLETED])
def test_initial_declaration_requires_live_event(state):
    with pytest.raises(Unprocessable):
        validate_proposal(SimpleNamespace(status=state), SimpleNamespace(status=MatchStatus.IN_PROGRESS), None, data())


def test_stale_revision_cannot_overwrite_new_official_result():
    with pytest.raises(Conflict) as error:
        validate_proposal(
            SimpleNamespace(status=EventStatus.LIVE),
            SimpleNamespace(status=MatchStatus.COMPLETED),
            SimpleNamespace(revision=2, outcome=DeclarationOutcome.WALA),
            data(base_revision=1, reason="Review"),
        )
    assert error.value.code == "stale_revision"


@pytest.mark.parametrize(
    "body",
    [
        {"base_revision": 1},
        {"outcome": "cancelled"},
        {"base_revision": 1, "reason": "  "},
    ],
)
def test_amendment_and_cancellation_require_meaningful_reason(body):
    with pytest.raises(ValidationError):
        DeclarationCreate.model_validate({"request_id": str(uuid.uuid4()), "outcome": "meron", **body})


async def test_unauthorized_submission_stops_before_database_access():
    db = AsyncMock()
    actor = SimpleNamespace(user=SimpleNamespace(account_type=AccountType.PLAYER), has=lambda *_: False)
    with pytest.raises(Forbidden):
        await submit(db, actor, uuid.uuid4(), data(), Mock())
    db.execute.assert_not_called()


async def test_unauthorized_finalization_stops_before_database_access():
    db = AsyncMock()
    with pytest.raises(Forbidden):
        await review(db, SimpleNamespace(has=lambda *_: False), uuid.uuid4(), "approve", None, Mock())
    db.execute.assert_not_called()


def test_declarator_role_cannot_finalize_or_use_legacy_result_api():
    permissions = SYSTEM_ROLES["declarator"][1]
    assert permissions == frozenset({P.RESULTS_SUBMIT})
    assert P.RESULTS_APPROVE in SYSTEM_ROLES["tenant_admin"][1]
    assert P.RESULTS_APPROVE not in SYSTEM_ROLES["operator"][1]


def test_database_constraints_serialize_active_proposals_and_idempotency_keys():
    # Import the full metadata before resolving composite FK targets.
    import app.models  # noqa: F401

    table = ResultDeclaration.__table__
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    indexes = " ".join(str(CreateIndex(i).compile(dialect=postgresql.dialect())) for i in table.indexes)
    assert "UNIQUE (tenant_id, request_id)" in sql
    assert "UNIQUE INDEX uq_result_declarations_active" in indexes
    assert "pending_approval" in indexes and "under_review" in indexes
    # Declared and approved actors have distinct, same-tenant constraints.
    assert "FOREIGN KEY(tenant_id, declared_by_id)" in sql
    assert "FOREIGN KEY(tenant_id, approved_by_id)" in sql


def review_context(monkeypatch, *, status="pending_approval", self_approval=False, revision=0):
    from app.modules.declarator import service

    tenant_id, author_id, approver_id, match_id = [uuid.uuid4() for _ in range(4)]
    actor = SimpleNamespace(
        id=author_id if self_approval else approver_id, tenant=SimpleNamespace(id=tenant_id), has=lambda *_: True
    )
    decl = SimpleNamespace(
        id=uuid.uuid4(),
        match_id=match_id,
        declared_by_id=author_id,
        status=DeclarationStatus(status),
        base_revision=revision,
        outcome=DeclarationOutcome.MERON,
        request_id=uuid.uuid4(),
        reason="Review requested",
    )
    match = SimpleNamespace(
        id=match_id, event_id=uuid.uuid4(), status=MatchStatus.COMPLETED if revision else MatchStatus.IN_PROGRESS
    )
    monkeypatch.setattr(service, "get_scoped", AsyncMock(return_value=decl))
    monkeypatch.setattr(
        service, "access_event", AsyncMock(return_value=SimpleNamespace(id=match.event_id, status=EventStatus.LIVE))
    )
    monkeypatch.setattr(service.matches, "get_match", AsyncMock(return_value=match))
    monkeypatch.setattr(service, "assigned_user", AsyncMock(return_value=SimpleNamespace(id=author_id)))
    monkeypatch.setattr("app.core.deps.load_permissions", AsyncMock(return_value={P.RESULTS_SUBMIT}))
    finalize = AsyncMock()
    monkeypatch.setattr(service.results, "declare", finalize)
    correct = AsyncMock()
    monkeypatch.setattr(service.results, "correct", correct)
    return service, actor, decl, finalize, correct


async def test_self_approval_never_calls_official_result_service(monkeypatch):
    service, actor, decl, finalize, correct = review_context(monkeypatch, self_approval=True)
    with pytest.raises(Forbidden) as error:
        await service.review(AsyncMock(), actor, decl.id, "approve", None, Mock())
    assert error.value.code == "self_approval"
    finalize.assert_not_awaited()
    correct.assert_not_awaited()


async def test_approval_retry_returns_finalized_declaration_without_publishing_again(monkeypatch):
    service, actor, decl, finalize, correct = review_context(monkeypatch, status="finalized")
    assert await service.review(AsyncMock(), actor, decl.id, "approve", None, Mock()) is decl
    finalize.assert_not_awaited()
    correct.assert_not_awaited()


async def test_stale_amendment_fails_before_any_official_write(monkeypatch):
    service, actor, decl, finalize, correct = review_context(monkeypatch, revision=1)
    monkeypatch.setattr(service.results, "current_result", AsyncMock(return_value=SimpleNamespace(revision=2)))
    with pytest.raises(Conflict) as error:
        await service.review(AsyncMock(), actor, decl.id, "approve", None, Mock())
    assert error.value.code == "stale_revision"
    finalize.assert_not_awaited()
    correct.assert_not_awaited()


async def test_revoked_assignment_stops_approval(monkeypatch):
    service, actor, decl, finalize, correct = review_context(monkeypatch)
    monkeypatch.setattr(service, "assigned_user", AsyncMock(side_effect=Forbidden("Assignment revoked")))
    with pytest.raises(Forbidden):
        await service.review(AsyncMock(), actor, decl.id, "approve", None, Mock())
    finalize.assert_not_awaited()
    correct.assert_not_awaited()


def test_monitor_holds_finalized_fight_until_next_fight_starts():
    from app.modules.declarator.service import monitor_match

    old = SimpleNamespace(match_number=135, status=MatchStatus.COMPLETED)
    upcoming = SimpleNamespace(match_number=136, status=MatchStatus.SCHEDULED)
    assert monitor_match([old, upcoming]) is old
    upcoming.status = MatchStatus.IN_PROGRESS
    assert monitor_match([old, upcoming]) is upcoming
    assert monitor_match([]) is None
    assert monitor_match([SimpleNamespace(match_number=1, status=MatchStatus.SCHEDULED)]).match_number == 1


def test_event_update_cannot_disable_verified_mode():
    from app.modules.events.schemas import EventUpdate

    request = EventUpdate.model_validate({"result_verification_required": False})
    assert "result_verification_required" not in request.model_dump(exclude_unset=True)
