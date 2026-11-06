"""Agent workspace screens (``/agent``): My Users and wallet transfers for master agents and agents."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, Meta, TenantDb, require_module
from app.modules.agents import inbox, transfers, users
from app.modules.agents.inbox import DecisionIn, InboxCounts, InboxIn, InboxItem
from app.modules.agents.transfers import BalanceTransferOut, TransferIn, TransferPreview, WalletBalance
from app.modules.agents.users import MyUserRow, MyUsersSummary
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/agent", tags=["agent workspace"], dependencies=[Depends(require_module("events"))])


@router.get("/users/summary", response_model=MyUsersSummary)
async def my_users_summary(current: CurrentUserDep, db: TenantDb) -> MyUsersSummary:
    """Counts of the caller's players (and, for a master agent, agents) by status, and online now."""
    return await users.summary(db, current)


@router.get("/users", response_model=Page[MyUserRow])
async def my_users(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    kind: Literal["player", "agent"] | None = None,
    state: Literal["active", "blocked", "banned", "online"] | None = None,
) -> Page[MyUserRow]:
    """The caller's downline players and agents, newest first."""
    return await users.list_users(db, current, params, kind=kind, state=state)


@router.get("/wallet", response_model=WalletBalance)
async def my_wallet(current: CurrentUserDep, db: TenantDb) -> WalletBalance:
    """The caller's transferable wallet. Earnings are not included, and nothing is granted by reading it."""
    return await transfers.sender_wallet(db, current)


@router.get("/balance-transfers", response_model=Page[BalanceTransferOut])
async def balance_transfers(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[BalanceTransferOut]:
    """Transfers this account sent. A master agent also sees transfers sent by their agents."""
    return await transfers.history(db, current, params)


@router.get("/users/{user_id}/transfer", response_model=TransferPreview)
async def transfer_preview(user_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> TransferPreview:
    """Recipient balance and the caller's wallet, for the Add Balance confirmation."""
    return await transfers.preview(db, current, user_id)


@router.post("/users/{user_id}/transfers", response_model=BalanceTransferOut)
async def add_balance(
    user_id: uuid.UUID, body: TransferIn, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> BalanceTransferOut:
    """Move wallet points from the caller to this player or, for a master agent, this agent.

    Earnings are not used, and no points are created.
    """
    return await transfers.transfer(db, current, user_id, body.amount, body.idempotency_key, meta)


@router.get("/inbox", response_model=list[InboxItem])
async def agent_inbox(current: CurrentUserDep, db: TenantDb) -> list[InboxItem]:
    """Open requests from players in this caller's hierarchy."""
    return await inbox.pending(db, current)


@router.get("/inbox/counts", response_model=InboxCounts)
async def agent_inbox_counts(current: CurrentUserDep, db: TenantDb) -> InboxCounts:
    """How many requests are waiting. Commission requests are not a separate ledger."""
    return await inbox.counts(db, current)


@router.post("/inbox/{request_id}/decision", response_model=InboxItem)
async def decide_request(request_id: uuid.UUID, body: DecisionIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> InboxItem:
    """Approve returns the player's play points to the direct upline. Deny leaves both wallets unchanged."""
    return await inbox.decide(db, current, request_id, body.decision, meta)


portal_router = APIRouter(prefix="/portal", tags=["inbox"], dependencies=[Depends(require_module("events"))])


@portal_router.get("/inbox", response_model=list[InboxItem])
async def my_inbox(current: CurrentUserDep, db: TenantDb) -> list[InboxItem]:
    """The signed-in player's own requests, newest first."""
    return await inbox.mine(db, current)


@portal_router.post("/inbox", response_model=InboxItem)
async def send_request(body: InboxIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> InboxItem:
    """Ask the direct upline to take play points back. Earnings cannot be requested, and nothing is paid as cash."""
    return await inbox.submit(db, current, body.amount, body.idempotency_key, meta)
