"""Pluggable notification system: Slack/Discord/email + stdout fallback.

All notifiers fail-soft: one failure never blocks others.
Idempotency via sent_notifications table keyed on event_hash.
"""

from __future__ import annotations

import hashlib
import json
import smtplib
import sqlite3
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx
import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.notifications")


@dataclass
class NotificationEvent:
    severity: str          # info | warn | critical
    title: str
    body: str
    context: dict


def event_hash(e: NotificationEvent) -> str:
    payload = json.dumps({"sev": e.severity, "title": e.title, "ctx": e.context},
                          sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class Notifier(Protocol):
    def send(self, e: NotificationEvent) -> bool: ...


class StdoutNotifier:
    def send(self, e: NotificationEvent) -> bool:
        log.info("notify_stdout", sev=e.severity, title=e.title, body=e.body)
        return True


class SlackNotifier:
    def __init__(self, webhook_url: str):
        self.url = webhook_url
    def send(self, e: NotificationEvent) -> bool:
        if not self.url:
            return False
        text = f"*[{e.severity.upper()}]* {e.title}\n{e.body}"
        try:
            r = httpx.post(self.url, json={"text": text}, timeout=10)
            return 200 <= r.status_code < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("slack_send_failed", err=str(exc))
            return False


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.url = webhook_url
    def send(self, e: NotificationEvent) -> bool:
        if not self.url:
            return False
        content = f"**[{e.severity.upper()}]** {e.title}\n{e.body}"[:2000]
        try:
            r = httpx.post(self.url, json={"content": content}, timeout=10)
            return 200 <= r.status_code < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("discord_send_failed", err=str(exc))
            return False


class EmailNotifier:
    def __init__(self, *, smtp_host: str, smtp_port: int,
                 smtp_user: str, smtp_password: str,
                 from_addr: str, to_addr: str):
        self.host, self.port = smtp_host, smtp_port
        self.user, self.password = smtp_user, smtp_password
        self.from_addr, self.to_addr = from_addr, to_addr
    def send(self, e: NotificationEvent) -> bool:
        if not (self.host and self.from_addr and self.to_addr):
            return False
        msg = EmailMessage()
        msg["Subject"] = f"[{e.severity.upper()}] {e.title}"
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg.set_content(f"{e.body}\n\n{json.dumps(e.context, indent=2, default=str)}")
        try:
            with smtplib.SMTP(self.host, self.port) as s:
                s.starttls()
                if self.user:
                    s.login(self.user, self.password)
                s.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("email_send_failed", err=str(exc))
            return False


class CompositeNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers
    def send(self, e: NotificationEvent) -> bool:
        any_ok = False
        for n in self.notifiers:
            try:
                if n.send(e):
                    any_ok = True
            except Exception as exc:  # noqa: BLE001
                log.warning("notifier_failed", n=type(n).__name__, err=str(exc))
        return any_ok


def build_default(settings: CopyTradeSettings | None = None) -> Notifier:
    settings = settings or get_settings()
    parts: list[Notifier] = [StdoutNotifier()]
    if settings.slack_webhook_url:
        parts.append(SlackNotifier(settings.slack_webhook_url))
    if settings.discord_webhook_url:
        parts.append(DiscordNotifier(settings.discord_webhook_url))
    if settings.smtp_host and settings.notification_email_to:
        parts.append(EmailNotifier(
            smtp_host=settings.smtp_host, smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user, smtp_password=settings.smtp_password,
            from_addr=settings.notification_email_from,
            to_addr=settings.notification_email_to,
        ))
    return CompositeNotifier(parts)


def notify(conn: sqlite3.Connection,
           e: NotificationEvent,
           notifier: Notifier | None = None,
           settings: CopyTradeSettings | None = None) -> bool:
    notifier = notifier or build_default(settings)
    eh = event_hash(e)
    row = conn.execute(
        "SELECT id FROM sent_notifications WHERE event_hash=?", (eh,),
    ).fetchone()
    if row:
        return True  # idempotent
    ok = notifier.send(e)
    if ok:
        conn.execute(
            "INSERT OR IGNORE INTO sent_notifications "
            "(event_hash, sent_at, severity, title) VALUES (?,?,?,?)",
            (eh, utc_now_iso(), e.severity, e.title),
        )
    return ok
