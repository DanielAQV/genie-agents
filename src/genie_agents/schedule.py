"""언제 깨어날지 — 시간대별 간격.

하루 내내 같은 간격으로 깨우는 건 사람의 리듬이 아니다. 사람이 자는 새벽에는
아예 깨어나지 않고, 업무 시간에는 뜸하게, 붙어 있는 시간에는 자주.

★ **닫힌 시간대에는 판단 자체를 하지 않는다.** 깨어나서 "지금은 새벽이니 안
  걸겠다" 고 정하는 것도 매번 값이 든다 — 그 판단은 이미 이 표가 대신하고 있다.
  판단을 뺏는 게 아니라, 에이전트가 이미 여러 번 스스로 내린 결론을 표로 굳힌
  것이다. 그래서 **표는 에이전트가 정한다.** 여기 있는 것은 표를 읽는 기계뿐이고
  표 자체는 없다.

━━ 표의 모양 ━━

    평일 06-09 10-30; 평일 09-18 15-60; 주말 06-24 30-60; 매일 00-06 off

**뒤에 온 줄이 이긴다** — `매일 00-06 off` 를 마지막에 두면 예외 없이 닫힌다.
못 알아들은 줄은 조용히 버린다. 한 줄이 틀렸다고 나머지까지 잃으면, 표를 고치다
오타 하나로 에이전트가 영영 안 깨어난다.

━━ 왜 골격에 있나 ━━

유나 것이었는데 예나도 재우게 되면서 옮겼다(2026-08-31). 두 벌로 두면 한쪽만
고쳐지고, 실제로 그 부류의 버그를 그날 셋 고쳤다. **표는 각자 것이고 기계만
같이 쓴다** — 언제 자고 싶은지는 에이전트마다 다르다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import clock

DAYS = {"평일": range(0, 5), "주말": range(5, 7), "매일": range(0, 7)}
_BAND = re.compile(r"^(평일|주말|매일)\s+(\d{1,2})-(\d{1,2})\s+(off|\d+-\d+)$")


@dataclass(frozen=True)
class Band:
    days: range
    start: int  # 시 (포함)
    end: int  # 시 (미포함). 24 는 자정
    window: tuple[float, float] | None  # None 이면 안 깨운다

    def covers(self, at: datetime) -> bool:
        return at.weekday() in self.days and self.start <= at.hour < self.end


def parse(spec: str) -> list[Band]:
    bands = []
    for chunk in (spec or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _BAND.match(chunk)
        if not m:
            continue  # 못 알아들은 줄은 조용히 버린다. 나머지는 살려야 한다.
        days, start, end, win = m.groups()
        window = None
        if win != "off":
            lo, hi = (float(x) for x in win.split("-"))
            window = (lo, hi) if lo <= hi else (hi, lo)
        bands.append(Band(DAYS[days], int(start), int(end), window))
    return bands


def window_at(spec: str, at: datetime | None = None) -> tuple[float, float] | None:
    """그 시각의 깨어남 간격. None 이면 깨우지 않는다."""
    now = clock.local(at)
    match = None
    for band in parse(spec):
        if band.covers(now):
            match = band
    return match.window if match else None


def minutes_until_open(
    spec: str, at: datetime | None = None, horizon_hours: int = 48
) -> float:
    """닫힌 시간대라면 다음에 열릴 때까지 몇 분인지. 열려 있으면 0."""
    now = clock.local(at)
    if window_at(spec, now) is not None:
        return 0.0

    # 정시 단위로 앞을 훑는다. 표가 시 단위라 그보다 잘게 볼 이유가 없다.
    probe = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    for _ in range(horizon_hours):
        if window_at(spec, probe) is not None:
            return (probe - now).total_seconds() / 60
        probe += timedelta(hours=1)
    return 60.0  # 표가 온통 off 여도 영영 자지는 않는다
