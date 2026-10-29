from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "esabong",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.billing",
        "app.workers.tasks.email",
        "app.workers.tasks.usage",
        "app.workers.tasks.domains",
        "app.workers.tasks.licensing",
    ],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=240,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
)

schedule = {
    "usage-flush": {"task": "usage.flush", "schedule": 600.0},
    "domains-recheck": {"task": "domains.recheck_pending", "schedule": crontab(minute=15)},
}
if settings.deployment_mode == "shared":
    schedule["billing-cycle"] = {"task": "billing.run_cycle", "schedule": crontab(minute=5)}
else:
    schedule["license-heartbeat"] = {"task": "licensing.heartbeat", "schedule": crontab(minute=0, hour="*/6")}
celery_app.conf.beat_schedule = schedule
