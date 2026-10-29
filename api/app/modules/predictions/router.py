import uuid

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, TenantDb, require_module
from app.modules.predictions import service
from app.modules.predictions.models import PredictionSide, PredictionStatus
from app.modules.predictions.schemas import PredictionIn, PredictionOut
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/portal", tags=["predictions"], dependencies=[Depends(require_module("events"))])


@router.get("/predictions", response_model=Page[PredictionOut])
async def my_predictions(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    status: PredictionStatus | None = None,
    side: PredictionSide | None = None,
    event_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
) -> Page[PredictionOut]:
    return await service.history(db, current, params, status=status, side=side, event_id=event_id, match_id=match_id)


@router.put("/matches/{match_id}/prediction", response_model=PredictionOut)
async def predict(match_id: uuid.UUID, body: PredictionIn, current: CurrentUserDep, db: TenantDb) -> PredictionOut:
    """Create or change the caller's prediction. Repeating the same side is a no-op."""
    return await service.predict(db, current, match_id, body.side)
