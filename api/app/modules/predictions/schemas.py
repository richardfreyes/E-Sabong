import uuid
from datetime import datetime

from app.modules.matches.models import MatchStatus
from app.modules.predictions.models import PredictionSide, PredictionStatus
from app.modules.results.models import MatchOutcome
from app.shared.schemas import Schema


class PredictionIn(Schema):
    side: PredictionSide


class PredictionOut(Schema):
    id: uuid.UUID
    match_id: uuid.UUID
    event_id: uuid.UUID
    event_name: str
    match_number: int
    meron_name: str
    wala_name: str
    match_status: MatchStatus
    official_outcome: MatchOutcome | None
    side: PredictionSide
    status: PredictionStatus
    # True while the side can still be changed.
    open: bool
    changes: int
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
