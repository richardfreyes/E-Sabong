"""Live stream reconciliation (see app.modules.video.streams.reconcile)."""

from app.modules.video.streams import reconcile
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async


@celery_app.task(name="video.reconcile", ignore_result=True, soft_time_limit=20, time_limit=30)
def reconcile_streams() -> dict[str, int]:
    return run_async(reconcile)
