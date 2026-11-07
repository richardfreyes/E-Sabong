"""Live stream reconciliation (see app.modules.video.streams.reconcile)."""

from app.modules.video.streams import reconcile
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async


@celery_app.task(name="video.reconcile", ignore_result=True, soft_time_limit=20, time_limit=30)
def reconcile_streams() -> dict[str, int]:
    return run_async(reconcile)


@celery_app.task(name="video.recordings_purge", ignore_result=True, soft_time_limit=120, time_limit=150)
def purge_recordings() -> dict[str, int]:
    """Retention: delete recordings older than RECORDING_RETENTION_DAYS (files, then rows)."""
    from app.modules.video.recordings import purge

    return run_async(purge)
