"""Email delivery for interview invitations (SMTP, with a labelled demo mode)."""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict

from db import execute, fetch_one, new_id

logger = logging.getLogger(__name__)


def email_config() -> Dict[str, str]:
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": os.environ.get("SMTP_PORT", "587").strip() or "587",
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "sender": os.environ.get("SMTP_FROM", "").strip(),
    }


def email_configured() -> bool:
    config = email_config()
    return bool(config["host"] and config["user"] and config["password"])


def previously_sent(slot_id: str) -> Dict[str, Any] | None:
    return fetch_one(
        "SELECT * FROM email_log WHERE slot_id = ? AND status IN ('sent', 'demo') "
        "ORDER BY created_at DESC LIMIT 1",
        (slot_id,),
    )


def log_email(**fields: Any) -> str:
    log_id = new_id()
    execute(
        """INSERT INTO email_log (id, slot_id, application_id, recruiter_id, to_email,
                subject, body, status, provider, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            log_id,
            fields.get("slot_id", ""),
            fields.get("application_id", ""),
            int(fields.get("recruiter_id") or 0),
            fields.get("to_email", ""),
            fields.get("subject", ""),
            fields.get("body", ""),
            fields.get("status", "demo"),
            fields.get("provider", ""),
            fields.get("error", ""),
        ),
    )
    return log_id


def deliver(to_email: str, subject: str, body: str, **meta: Any) -> Dict[str, Any]:
    """
    Send an email when SMTP is configured; otherwise record a clearly labelled
    demo delivery. Never reports a real send unless the SMTP server accepted it.
    """
    config = email_config()
    if not email_configured():
        log_email(
            to_email=to_email, subject=subject, body=body, status="demo",
            provider="demo", error="SMTP is not configured", **meta,
        )
        logger.info("Recorded DEMO interview email (SMTP not configured)")
        return {
            "status": "demo",
            "sent": False,
            "message": (
                "Demo mode: no SMTP credentials are configured, so the email was NOT sent. "
                "The invitation was saved to the email log so you can preview the flow."
            ),
        }

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"] or config["user"]
    message["To"] = to_email
    message.set_content(body)

    try:
        port = int(config["port"])
        if port == 465:
            with smtplib.SMTP_SSL(config["host"], port, timeout=20,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(config["user"], config["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(config["host"], port, timeout=20) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                except smtplib.SMTPException:
                    logger.warning("SMTP server does not support STARTTLS; sending without it")
                smtp.login(config["user"], config["password"])
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.exception("Interview email delivery failed")
        log_email(
            to_email=to_email, subject=subject, body=body, status="failed",
            provider="smtp", error=str(exc)[:400], **meta,
        )
        return {"status": "failed", "sent": False, "message": f"The email could not be sent: {exc}"}

    log_email(
        to_email=to_email, subject=subject, body=body, status="sent",
        provider="smtp", **meta,
    )
    return {"status": "sent", "sent": True, "message": f"Invitation sent to {to_email}."}
