import smtplib
from email.message import EmailMessage

import structlog

from app.core.config import get_settings
from app.workers.celery_app import celery_app

log = structlog.get_logger("worker.email")


@celery_app.task(name="email.send", autoretry_for=(smtplib.SMTPException, OSError), retry_backoff=True, max_retries=5)
def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
        if s.smtp_use_tls:
            smtp.starttls()
        if s.smtp_user and s.smtp_password:
            smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
        smtp.send_message(msg)
    log.info("email_sent", to=to, subject=subject)
