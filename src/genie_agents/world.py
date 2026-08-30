"""외부 세계 입력 — 설계 문서 7.3.

에이전트가 사용자 한 사람에게서만 정보를 받으면 세계가 사용자뿐이다.
관계 밖에서 들어오는 입력이 있어야 "오늘 이런 일이 있었어"가 성립한다.

소스는 어댑터로 갈아끼운다. 이 모듈은 수집·보관·조회만 하고,
무엇을 구독할지는 바깥에서 정한다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from . import clock
from .store import default_root, JsonStore, from_dict


@dataclass
class Signal:
    """외부에서 들어온 정보 한 조각."""

    id: str
    ts: str
    source: str
    title: str
    summary: str = ""
    url: str | None = None
    tags: list[str] = field(default_factory=list)
    surfaced_at: str | None = None  # 브리핑에 이미 올렸는지


class SignalSource(Protocol):
    """외부 소스 어댑터. 네트워크·인증은 각 구현이 알아서 한다."""

    name: str

    def fetch(self) -> list[dict]: ...


@dataclass
class StaticSource:
    """테스트/데모용. 주어진 목록을 그대로 돌려준다."""

    name: str
    items: list[dict]

    def fetch(self) -> list[dict]:
        return list(self.items)


class WorldFeed:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / "world.json")
        raw = self._store.load({"signals": [], "last_poll": {}})
        self._items: list[Signal] = [from_dict(Signal, s) for s in raw["signals"]]
        self._last_poll: dict[str, str] = raw.get("last_poll", {})

    def __len__(self) -> int:
        return len(self._items)

    def ingest(
        self,
        source: str,
        title: str,
        summary: str = "",
        url: str | None = None,
        tags: list[str] | None = None,
    ) -> Signal:
        sig = Signal(
            id=uuid.uuid4().hex[:8],
            ts=clock.now_iso(),
            source=source,
            title=title,
            summary=summary,
            url=url,
            tags=list(tags or []),
        )
        self._items.append(sig)
        self._flush()
        return sig

    def poll(self, source: SignalSource) -> list[Signal]:
        """소스를 한 번 긁어 새 항목만 적재한다. 같은 제목은 중복으로 보고 건너뛴다."""
        seen = {(s.source, s.title) for s in self._items}
        fresh = [
            self.ingest(source=source.name, **item)
            for item in source.fetch()
            if (source.name, item.get("title")) not in seen
        ]
        self._last_poll[source.name] = clock.now_iso()
        self._flush()
        return fresh

    def recent(
        self,
        hours: float = 24,
        limit: int = 8,
        per_source: int = 2,
        unseen_only: bool = False,
    ) -> list[Signal]:
        """최근 신호. 두 가지로 거른다.

        **소스별 상한** — 없으면 항목 많은 뉴스 피드 하나가 브리핑을 통째로 먹고,
        "지금 말을 걸지 말지"라는 판단이 목록 속에 묻힌다.

        **unseen_only** — 이미 브리핑에 올렸던 건 빼고 본다. 사람이 같은 헤드라인을
        2분마다 다시 읽지는 않는다. 안 그러면 에이전트가 같은 날씨 한 줄을 하루 종일
        새 소식처럼 다시 판단한다.
        """
        fresh = [s for s in self._items if clock.elapsed_minutes(s.ts) <= hours * 60]
        if unseen_only:
            fresh = [s for s in fresh if s.surfaced_at is None]
        fresh.sort(key=lambda s: s.ts, reverse=True)

        seen: dict[str, int] = {}
        out = []
        for sig in fresh:
            if seen.get(sig.source, 0) >= per_source:
                continue
            seen[sig.source] = seen.get(sig.source, 0) + 1
            out.append(sig)
            if len(out) >= limit:
                break
        return out

    def poll_all(self, sources) -> list[Signal]:
        """여러 소스를 한 번에. 하나가 죽어도 나머지는 들어온다."""
        fresh = []
        for src in sources:
            try:
                fresh += self.poll(src)
            except Exception:
                # 바깥 세상이 안 열린다고 에이전트가 멈출 이유는 없다.
                continue
        return fresh

    def mark_surfaced(self, signal_ids: list[str]) -> None:
        """브리핑에 올렸음을 기록한다. 다음부터는 새 소식으로 치지 않는다."""
        ts = clock.now_iso()
        ids = set(signal_ids)
        for sig in self._items:
            if sig.id in ids:
                sig.surfaced_at = ts
        if ids:
            self._flush()

    def consume_unseen(self) -> int:
        """에이전트가 실제로 말을 걸었을 때 부른다. 안 본 소식을 전부 소진 처리한다.

        브리핑에 올렸다는 것과 사용자에게 전달됐다는 것은 다르다. 올리기만 하고
        소진시키면, 에이전트가 침묵을 택한 순간 그 뉴스는 영영 사라진다.
        말을 건 뒤에야 "이건 꺼냈다"가 성립한다.
        """
        ids = [s.id for s in self._items if s.surfaced_at is None]
        self.mark_surfaced(ids)
        return len(ids)

    def unseen_count(self, hours: float = 24) -> int:
        return len(
            [
                s
                for s in self._items
                if s.surfaced_at is None and clock.elapsed_minutes(s.ts) <= hours * 60
            ]
        )

    def last_poll(self, source_name: str) -> str | None:
        return self._last_poll.get(source_name)

    def _flush(self) -> None:
        self._store.save(
            {
                "signals": [asdict(s) for s in self._items],
                "last_poll": self._last_poll,
            }
        )
