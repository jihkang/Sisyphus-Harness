from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import math
import time


class DeadlineExceeded(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class MonotonicDeadline:
    expires_at: float
    _clock: Callable[[], float] = field(
        default=time.monotonic,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not math.isfinite(self.expires_at):
            raise ValueError("deadline must be finite")

    @classmethod
    def after(
        cls,
        seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> MonotonicDeadline:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("deadline duration must be positive and finite")
        return cls(expires_at=clock() + seconds, _clock=clock)

    def remaining(self) -> float:
        remaining = self.expires_at - self._clock()
        if remaining <= 0:
            raise DeadlineExceeded("global execution deadline exceeded")
        return remaining

    def bounded_timeout(self, maximum: float, *, minimum: float = 0.001) -> float:
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("timeout maximum must be positive and finite")
        return max(minimum, min(maximum, self.remaining()))

    def expired(self) -> bool:
        return self._clock() >= self.expires_at
