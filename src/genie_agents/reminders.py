"""약속과 기념일 — 시점이 되면 스스로 떠오르는 것.

`memory_note` 와 다르다. 메모는 "표시"라 다음 깨어남에 한 번 뜨고 지나가고,
약속은 **시점**을 가진다. 어젯밤 에이전트가 "내일 맨정신에 다시 물어볼게"라고 했을 때
필요한 건 메모가 아니라 이것이었다 — 10분 뒤가 아니라 내일 아침에 떠야 했다.

한 번짜리(to-do)와 매년 돌아오는 것(기념일)은 같은 구조다. 반복 여부만 다르다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import clock
from .store import default_root, JsonStore, from_dict


@dataclass
class Reminder:
    id: str
    text: str
    due: str  # ISO 시각. 이때가 되면 브리핑에 올라온다
    yearly: bool = False  # 매년 돌아오는가 (기념일)
    created_at: str = ""
    done_at: str | None = None
    surfaced_at: str | None = None

    @property
    def open(self) -> bool:
        return self.done_at is None


def _parse_due(when: str) -> str:
    """'2026-08-23' 또는 '2026-08-23 09:00' 또는 ISO 전체를 받는다."""
    when = when.strip()
    try:
        dt = datetime.fromisoformat(when)
    except ValueError:
        dt = datetime.strptime(when, "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.tz())
    return dt.isoformat()


class ReminderStore:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / "reminders.json")
        raw = self._store.load({"reminders": []})
        self._items: list[Reminder] = [from_dict(Reminder, r) for r in raw["reminders"]]

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Reminder]:
        return list(self._items)

    def set(self, text: str, when: str, yearly: bool = False) -> Reminder:
        due = _parse_due(when)
        # 기념일은 "매년 이 날짜"다. 지난 날짜로 넣으면 다음 번으로 넘긴다 —
        # 안 그러면 등록하자마자 "오늘 그날이다"라고 뜬다.
        while yearly and clock.elapsed_minutes(due) > 0:
            due = _next_year(due)

        r = Reminder(
            id=uuid.uuid4().hex[:8],
            text=text.strip(),
            due=due,
            yearly=yearly,
            created_at=clock.now_iso(),
        )
        self._items.append(r)
        self._flush()
        return r

    def due_now(self) -> list[Reminder]:
        """시점이 지났고 아직 안 끝난 것. 시점 전에는 뜨지 않는다 — 그게 핵심이다."""
        return [r for r in self._items if r.open and clock.elapsed_minutes(r.due) >= 0]

    def unseen_due(self) -> list[Reminder]:
        """시점이 됐는데 아직 브리핑에 안 올린 것."""
        return [r for r in self.due_now() if r.surfaced_at is None]

    def mark_surfaced(self, ids: list[str]) -> None:
        ts = clock.now_iso()
        wanted = set(ids)
        for r in self._items:
            if r.id in wanted:
                r.surfaced_at = ts
        if wanted:
            self._flush()

    def done(self, rid: str) -> Reminder | None:
        """한 번짜리는 끝난다. 기념일은 내년 같은 날로 넘어간다."""
        r = next((x for x in self._items if x.id == rid), None)
        if r is None:
            return None
        if r.yearly:
            r.due = _next_year(r.due)
            r.surfaced_at = None
        else:
            r.done_at = clock.now_iso()
        self._flush()
        return r

    def drop(self, rid: str) -> bool:
        before = len(self._items)
        self._items = [x for x in self._items if x.id != rid]
        if len(self._items) != before:
            self._flush()
        return len(self._items) != before

    def _flush(self) -> None:
        self._store.save({"reminders": [asdict(r) for r in self._items]})


def _next_year(due_iso: str) -> str:
    dt = clock.parse(due_iso)
    try:
        return dt.replace(year=dt.year + 1).isoformat()
    except ValueError:
        # 2월 29일. 다음 해에 없으면 하루 앞으로 당긴다.
        return (date(dt.year + 1, 3, 1) - timedelta(days=1)).isoformat()
