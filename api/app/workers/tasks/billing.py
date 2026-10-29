import structlog

from app.core.database import platform_session
from app.modules.subscriptions.service import run_billing_cycle
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async

log = structlog.get_logger("worker.billing")


@celery_app.task(name="billing.run_cycle")
def run_cycle() -> dict[str, int]:
    async def _run() -> dict[str, int]:
        async with platform_session() as db:
            result = await run_billing_cycle(db)
        log.info("billing_cycle_completed", **result.__dict__)
        return result.__dict__

    return run_async(_run)
