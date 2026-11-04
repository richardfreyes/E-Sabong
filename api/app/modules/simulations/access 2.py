"""Tournament role vocabulary over existing identities; no stored roles or user IDs are renamed."""

import uuid
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import NotFound
from app.core.permissions import P
from app.modules.simulations.models import SimulationArena, SimulationController
from app.modules.users.models import AccountType


class TournamentRole(StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    MASTER_HOST = "MASTER_HOST"
    REGIONAL_HOST = "REGIONAL_HOST"
    DECLARATOR = "DECLARATOR"
    PLAYER = "PLAYER"


TOURNAMENT_ROLE = {
    AccountType.ADMIN: TournamentRole.SYSTEM_ADMIN,
    AccountType.MASTER_AGENT: TournamentRole.MASTER_HOST,
    AccountType.AGENT: TournamentRole.REGIONAL_HOST,
    AccountType.DECLARATOR: TournamentRole.DECLARATOR,
    AccountType.PLAYER: TournamentRole.PLAYER,
}


async def visible_arena(db: AsyncSession, current: CurrentUser, arena_id: uuid.UUID) -> SimulationArena:
    arena = await db.get(SimulationArena, arena_id)
    if arena is None or arena.tenant_id != current.tenant.id:
        raise NotFound("Simulation arena not found")
    # Roles are descriptive; permission AND assignment govern operational access.
    if current.has(P.SIMULATIONS_MANAGE):
        return arena
    if current.has(P.SIMULATIONS_CONTROL) and await db.get(SimulationController, (arena.id, current.id)):
        return arena
    raise NotFound("Simulation arena not found")
