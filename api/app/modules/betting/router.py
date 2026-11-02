import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import CurrentUser, CurrentUserDep, Meta, TenantDb, require_any_permission, require_module, require_permissions
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
    PlayerBookOut,
    Reconciliation,
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


@portal.post("/matches/{match_id}/bets", response_model=BetPlaced, status_code=status.HTTP_201_CREATED)
async def place_bet(match_id: uuid.UUID, body: BetIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> BetPlaced:
    """Stake play balance on one side. Resending the same ``request_id`` returns the original bet."""
    return await service.place_bet(db, current, match_id, body, meta)


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
async def open_betting(match_id: uuid.UUID, current: MatchManager, db: TenantDb, meta: Meta) -> AdminBookOut:
    return await service.open_betting(db, current, match_id, meta)


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
) -> Page[WalletEntryRow]:
    """The append-only play balance ledger: grants, stakes, payouts, refunds and resettlements."""
    return await reports.entries(db, current.tenant.id, params, kind=kind, user_id=user_id, match_id=match_id)


@admin.get("/betting/reconciliation", response_model=Reconciliation)
async def reconciliation(current: LedgerViewer, db: TenantDb) -> Reconciliation:
    return await reports.reconciliation(db, current.tenant.id)
