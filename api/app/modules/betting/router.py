import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import CurrentUser, CurrentUserDep, Meta, TenantDb, require_any_permission, require_module, require_permissions
from app.core.errors import Forbidden, Unprocessable
from app.core.permissions import P
from app.modules.betting import reports, service
from app.modules.betting.models import BetStatus, BookState, WalletEntryKind
from app.modules.betting.schemas import (
    AdminBookOut,
    BetHistoryItem,
    BetIn,
    BetPlaced,
    BettingConfigOut,
    BettingSettingsOut,
    BettingSettingsUpdate,
    FightBookSummary,
    OpenBettingIn,
    FightPools,
    PlayerBookOut,
    Reconciliation,
    StatementSummary,
    WalletEntryRow,
    WalletRow,
)
from app.shared.schemas import Page, PageDep

modules = [Depends(require_module("events")), Depends(require_module("matches"))]
portal = APIRouter(prefix="/portal", tags=["betting"], dependencies=modules)
admin = APIRouter(prefix="/admin", tags=["admin: betting"], dependencies=modules)

SettingsManager = Annotated[CurrentUser, Depends(require_permissions(P.SETTINGS_MANAGE))]
SettingsViewer = Annotated[CurrentUser, Depends(require_any_permission(P.SETTINGS_MANAGE, P.MATCHES_MANAGE, P.EVENTS_VIEW))]
MatchManager = Annotated[CurrentUser, Depends(require_permissions(P.MATCHES_MANAGE))]
EventViewer = Annotated[CurrentUser, Depends(require_any_permission(P.EVENTS_VIEW, P.EVENTS_MANAGE, P.MATCHES_MANAGE))]
# Every player's play balance and ledger: the same audience as every demo-points account.
LedgerViewer = Annotated[CurrentUser, Depends(require_permissions(P.POINTS_VIEW_ALL))]


# ---- player portal ----


@portal.get("/betting", response_model=BettingConfigOut)
async def betting_config(current: CurrentUserDep, db: TenantDb) -> BettingConfigOut:
    """Whether betting is on, its limits and multipliers, and the caller's play balance."""
    return await service.config(db, current)


@portal.get("/matches/{match_id}/book", response_model=PlayerBookOut)
async def fight_book(match_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> PlayerBookOut:
    """All-player pool totals and odds for one fight, plus the caller's own stakes and balance."""
    return await service.player_book(db, current, match_id)


@portal.get("/events/{event_id}/pools", response_model=list[FightPools])
async def event_pools(event_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> list[FightPools]:
    """MERON and WALA totals for every fight of an event, so history can mark the higher stake L and the lower D."""
    return await service.event_pools(db, current, event_id)


@portal.post("/matches/{match_id}/bets", response_model=BetPlaced, status_code=status.HTTP_201_CREATED)
async def place_bet(match_id: uuid.UUID, body: BetIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> BetPlaced:
    """Stake play balance on one side. Resending the same ``request_id`` returns the original bet."""
    return await service.place_bet(db, current, match_id, body, meta)


@portal.get("/betting/entries", response_model=Page[WalletEntryRow])
async def my_arena_points_history(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    kind: WalletEntryKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[WalletEntryRow]:
    """Your own Arena Points ledger: the starting grant, bets, wins credited, refunds and corrections.

    Each bet row carries the bet's side and outcome (won / lost / refunded / open). Always scoped to the
    signed-in player (no user filter is accepted), newest first, read-only. Dates are UTC calendar days.
    """
    return await reports.entries(
        db, current.tenant.id, params, kind=kind, user_id=current.id, date_from=date_from, date_to=date_to
    )


@portal.get("/betting/statement", response_model=StatementSummary)
async def my_statement(
    current: CurrentUserDep, db: TenantDb, date_from: date | None = None, date_to: date | None = None
) -> StatementSummary:
    """Your own statement totals: staked, winnings credited, losses, refunds, corrections. Read-only."""
    return await reports.statement_summary(db, current.tenant.id, current.id, date_from=date_from, date_to=date_to)


@portal.get("/bets", response_model=Page[BetHistoryItem])
async def my_bets(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    match_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    status: BetStatus | None = None,
) -> Page[BetHistoryItem]:
    return await service.my_bets(db, current, params, match_id=match_id, event_id=event_id, status=status)


# ---- administration ----


@admin.get("/betting/settings", response_model=BettingSettingsOut)
async def get_settings(current: SettingsViewer, db: TenantDb) -> BettingSettingsOut:
    return BettingSettingsOut.model_validate(await service.settings_for(db, current.tenant.id))


@admin.put("/betting/settings", response_model=BettingSettingsOut)
async def put_settings(body: BettingSettingsUpdate, current: SettingsManager, db: TenantDb, meta: Meta) -> BettingSettingsOut:
    """Changes apply to bets accepted afterwards; accepted bets and opened books keep their terms."""
    return BettingSettingsOut.model_validate(await service.update_settings(db, current, body, meta))


@admin.get("/events/{event_id}/books", response_model=list[AdminBookOut])
async def event_books(event_id: uuid.UUID, current: EventViewer, db: TenantDb) -> list[AdminBookOut]:
    return await service.event_books(db, current.tenant.id, event_id)


@admin.get("/matches/{match_id}/book", response_model=AdminBookOut)
async def admin_book(match_id: uuid.UUID, current: EventViewer, db: TenantDb) -> AdminBookOut:
    return await service.admin_book(db, current.tenant.id, match_id)


@admin.post("/matches/{match_id}/betting/open", response_model=AdminBookOut)
async def open_betting(
    match_id: uuid.UUID, current: MatchManager, db: TenantDb, meta: Meta, body: OpenBettingIn | None = None
) -> AdminBookOut:
    """Open (or reopen) betting on a fight. Only one fight per event takes bets at a time: if another
    fight of the event is open this is refused (409 ``betting_open_elsewhere``) unless an event
    manager sends ``{"supersede": true, "reason": ...}``, which closes the other fight first (audited)."""
    reason = None
    if body is not None and body.supersede:
        if not current.has(P.EVENTS_MANAGE):
            raise Forbidden("Superseding an open fight requires event management permission", code="supersede_forbidden")
        reason = (body.reason or "").strip()
        if len(reason) < 3:
            raise Unprocessable("A reason is required to supersede an open fight", code="reason_required")
    return await service.open_betting(db, current, match_id, meta, supersede_reason=reason)


@admin.post("/matches/{match_id}/betting/close", response_model=AdminBookOut)
async def close_betting(match_id: uuid.UUID, current: MatchManager, db: TenantDb, meta: Meta) -> AdminBookOut:
    """Last call: freezes the pools and locks the final MERON/WALA multipliers."""
    return await service.close_betting(db, current, match_id, meta)


@admin.get("/betting/books", response_model=Page[FightBookSummary])
async def books_overview(
    current: EventViewer,
    db: TenantDb,
    params: PageDep,
    state: Annotated[list[BookState] | None, Query()] = None,
    event_id: uuid.UUID | None = None,
    live: bool = False,
) -> Page[FightBookSummary]:
    """Fights with betting across events: live pools and odds, payout obligations, settlements."""
    items, total = await reports.fight_summaries(
        db, current.tenant.id, states=state, event_id=event_id, live_events_only=live, params=params
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@admin.get("/betting/wallets", response_model=Page[WalletRow])
async def play_wallets(current: LedgerViewer, db: TenantDb, params: PageDep) -> Page[WalletRow]:
    return await reports.wallets(db, current.tenant.id, params)


@admin.get("/betting/entries", response_model=Page[WalletEntryRow])
async def wallet_entries(
    current: LedgerViewer,
    db: TenantDb,
    params: PageDep,
    kind: WalletEntryKind | None = None,
    user_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[WalletEntryRow]:
    """The append-only play balance ledger: grants, stakes, payouts, refunds and resettlements."""
    return await reports.entries(
        db,
        current.tenant.id,
        params,
        kind=kind,
        user_id=user_id,
        match_id=match_id,
        date_from=date_from,
        date_to=date_to,
    )


@admin.get("/betting/statements/{user_id}", response_model=StatementSummary)
async def player_statement(
    user_id: uuid.UUID, current: LedgerViewer, db: TenantDb, date_from: date | None = None, date_to: date | None = None
) -> StatementSummary:
    """One account's statement totals (ledger viewers only, this tenant only). Read-only."""
    return await reports.statement_summary(db, current.tenant.id, user_id, date_from=date_from, date_to=date_to)


@admin.get("/betting/reconciliation", response_model=Reconciliation)
async def reconciliation(current: LedgerViewer, db: TenantDb) -> Reconciliation:
    return await reports.reconciliation(db, current.tenant.id)
