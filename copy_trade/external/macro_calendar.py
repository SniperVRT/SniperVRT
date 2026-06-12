"""Embedded US macro calendar — caution mode within ±2h of event."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Approximate scheduled dates for next 90 days. Update periodically.
EVENTS = [
    # (iso datetime UTC, name)
    ("2026-06-12T12:30:00+00:00", "CPI"),
    ("2026-06-18T18:00:00+00:00", "FOMC"),
    ("2026-07-03T12:30:00+00:00", "NFP"),
    ("2026-07-10T12:30:00+00:00", "CPI"),
    ("2026-07-30T18:00:00+00:00", "FOMC"),
    ("2026-08-01T12:30:00+00:00", "NFP"),
    ("2026-08-12T12:30:00+00:00", "CPI"),
    ("2026-08-29T12:30:00+00:00", "PCE"),
    ("2026-09-05T12:30:00+00:00", "NFP"),
    ("2026-09-11T12:30:00+00:00", "CPI"),
    ("2026-09-17T18:00:00+00:00", "FOMC"),
]

CAUTION_WINDOW = timedelta(hours=2)


def in_caution_window(now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    for iso, name in EVENTS:
        t = datetime.fromisoformat(iso)
        if abs((t - now).total_seconds()) <= CAUTION_WINDOW.total_seconds():
            return {"event": name, "event_at": iso,
                    "minutes_to_event": int((t - now).total_seconds() / 60)}
    return None
