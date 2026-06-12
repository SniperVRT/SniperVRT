"""Tests for notification system."""

from __future__ import annotations

from copy_trade.notifications.alerts import (
    NotificationEvent, CompositeNotifier, StdoutNotifier,
    event_hash, notify,
)


class FakeNotifier:
    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.sent: list[NotificationEvent] = []
    def send(self, e):
        self.sent.append(e)
        return self.succeed


def test_event_hash_deterministic():
    e1 = NotificationEvent("warn", "x", "y", {"a": 1})
    e2 = NotificationEvent("warn", "x", "y", {"a": 1})
    assert event_hash(e1) == event_hash(e2)


def test_composite_fans_out():
    a = FakeNotifier()
    b = FakeNotifier()
    c = CompositeNotifier([a, b])
    e = NotificationEvent("info", "t", "body", {})
    assert c.send(e)
    assert len(a.sent) == 1 and len(b.sent) == 1


def test_composite_partial_failure_still_succeeds():
    a = FakeNotifier(succeed=False)
    b = FakeNotifier(succeed=True)
    c = CompositeNotifier([a, b])
    e = NotificationEvent("info", "t", "body", {})
    assert c.send(e)


def test_notify_idempotent(conn):
    nfy = FakeNotifier()
    e = NotificationEvent("info", "t", "body", {"x": 1})
    notify(conn, e, notifier=nfy)
    notify(conn, e, notifier=nfy)
    assert len(nfy.sent) == 1  # second send skipped


def test_stdout_notifier_returns_true():
    assert StdoutNotifier().send(NotificationEvent("info", "t", "b", {}))
