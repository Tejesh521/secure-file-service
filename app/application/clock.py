from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class FixedClock:
    """Deterministic clock for tests."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self._at = self._at + timedelta(seconds=seconds)
