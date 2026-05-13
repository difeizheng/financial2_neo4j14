"""Stage timer for tracking parsing stage durations."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _StageTiming:
    stage: str
    start: float
    end: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start if self.end else 0.0


class StageTimer:
    """Tracks time spent in each parsing stage."""

    def __init__(self) -> None:
        self.stages: list[_StageTiming] = []
        self._current: _StageTiming | None = None

    def start(self, stage: str) -> None:
        self._current = _StageTiming(stage=stage, start=time.time())
        self.stages.append(self._current)

    def stop(self) -> None:
        if self._current:
            self._current.end = time.time()

    def summary(self) -> list[dict]:
        """Return [{stage, duration_s, pct}, ...]."""
        total = sum(s.duration for s in self.stages)
        return [
            {
                "stage": s.stage,
                "duration_s": round(s.duration, 2),
                "pct": round(s.duration / total * 100, 1) if total else 0,
            }
            for s in self.stages
        ]
